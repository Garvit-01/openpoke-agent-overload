"""Side-by-side demo: the original OpenPoke and this branch, with the same 300 agents.

    python tools/demo.py seed                      # fill both apps with the same synthetic history
    python tools/run_mine.py                       # this branch, port 8001 (own terminal)
    python tools/run_original.py                   # the original, port 8002 (own terminal)
    python tools/demo.py ask "Did Sarah Chen reply about the Q3 invoice?"
    python tools/demo.py status

Every version at once (main, each approach on its own, all three), each its own app:

    python tools/demo.py --variants every seed     # the same history into the original and all five
    python tools/run_mine.py --variant main        # one terminal each: main, plurals_typos,
    ...                                            #   past_requests, embeddings, all (ports 8011 to 8015)
    python tools/run_original.py
    python tools/demo.py --variants every ask "Could you send the updated grocery to Sarah"
    python tools/demo.py --variants every cases    # one labelled case per category, then a score table
    python tools/demo.py --variants every status   # the score so far, per version

--variants also takes a list, such as `--variants main,all`.

`seed` loads the eval's synthetic history (300 agents, seed 7) into both apps before any chat:
the original gets the names in its roster.json, this branch gets the same agents in its registry,
with their summaries and cached search phrases. The agents are pretend: there is no real email
behind them. But which agent each app picks for a message is real, and that's what `ask` shows.
`seed` never deletes anything: existing data is moved to a dated backup folder first.

`ask` sends one message to both apps, waits for their replies, and prints which agent each used.
If the message is one of the eval's labelled cases, it also prints the right answer, and the
result counts towards the score `status` shows.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE / "tools"))

DEFAULT_ORIGINAL = HERE.parent / "openpoke-original"
OURS_URL, ORIGINAL_URL = "http://localhost:8001", "http://localhost:8002"
OURS_DATA = HERE / "server" / "data"
VARIANTS_DATA = HERE / "server" / "data_variants"
# Ports for tools/run_mine.py --variant; the keys are the versions in evals/configs.py
VARIANT_PORTS = {"main": 8011, "plurals_typos": 8012, "past_requests": 8013, "embeddings": 8014, "all": 8015}
SCORES_FILE = "demo_scores.jsonl"
PHRASES_CACHE = HERE / "evals" / "results" / "agent_phrases.json"
# Checked twice a second, so each app's time to its final reply is accurate to about half a second
POLL_SECONDS = 0.5
QUIET_AFTER_RESULT = 6.0
QUIET_AFTER_ONE_REPLY = 25.0
ORIGINAL_LABEL = "original"


def variant_data(key: str) -> Path:
    return VARIANTS_DATA / key


class App(NamedTuple):
    """One copy of this branch: where it listens, where its data is, and the settings it runs with."""

    label: str
    url: str
    data: Path
    settings: Optional[Dict[str, str]] = None  # None: the settings of whoever started it (plain run_mine.py)


def apps_for(args) -> List[App]:
    if not args.variants:
        return [App("this branch", OURS_URL, OURS_DATA)]
    from evals.configs import CONFIGS

    wanted = list(VARIANT_PORTS) if args.variants == "every" else args.variants.split(",")
    unknown = set(wanted) - set(VARIANT_PORTS)
    if unknown:
        sys.exit(f"Unknown version(s): {', '.join(sorted(unknown))}. Choose from: every, {', '.join(VARIANT_PORTS)}")
    return [App(label, f"http://localhost:{VARIANT_PORTS[key]}", variant_data(key), values)
            for key, label, values in CONFIGS if key in wanted]


def scores_path(args) -> Path:
    return (VARIANTS_DATA if args.variants else OURS_DATA) / SCORES_FILE


def slugify(name: str) -> str:
    """The original's log file naming (server/services/execution/log_store.py at 5b5f635)."""
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in name.strip()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "agent"


def scenario_for(args):
    from evals.scenarios import build_scenario

    # Timestamps relative to today, so the apps' lifecycle pass sees the same ages as the eval
    return build_scenario(args.agents, seed=args.seed, now=datetime.now(timezone.utc))


def move_aside(folder: Path) -> Optional[Path]:
    if not folder.exists() or not any(folder.rglob("*")):
        return None
    backup = folder.with_name(f"{folder.name}_backup_{datetime.now():%Y%m%d_%H%M%S}")
    # A plain rename: shutil.move would fall back to copy-then-delete and leave half the files
    # behind when a running app holds one open (Windows)
    try:
        folder.rename(backup)
    except OSError as exc:
        sys.exit(f"Couldn't move {folder} aside ({exc.strerror}). Stop the app using it (Ctrl+C in its terminal) and run seed again.")
    return backup


# ------------------------------------------------------------------------- seed


def seed(args) -> None:
    from evals.harness import build_registry
    from server.services.execution.phrases import PHRASE_KIND

    original_data = args.original / "server" / "data"
    if not (args.original / "server" / "app.py").is_file():
        sys.exit(f'No original checkout at {args.original}. Run: git worktree add "{args.original}" 5b5f635')

    apps = apps_for(args)
    for folder in [app.data for app in apps] + [original_data]:
        backup = move_aside(folder)
        if backup:
            print(f"Moved existing data aside: {backup}")
    scores = scores_path(args)
    if scores.exists():
        scores.rename(scores.with_name(f"{scores.stem}_backup_{datetime.now():%Y%m%d_%H%M%S}{scores.suffix}"))

    scenario = scenario_for(args)
    names = [agent.name for agent in scenario.agents]  # creation order, like the original's roster.json

    # The original: a list of names, exactly what its roster.json holds
    roster = original_data / "execution_agents" / "roster.json"
    roster.parent.mkdir(parents=True, exist_ok=True)
    roster.write_text(json.dumps(names, indent=2), encoding="utf-8")

    # This branch: the same agents in the registry, with summaries, entities, status and the
    # cached search phrases (added before the one bulk import; one write per agent is slow on Windows)
    cache = json.loads(PHRASES_CACHE.read_text(encoding="utf-8")) if PHRASES_CACHE.exists() else {}
    with_phrases = 0
    for agent in scenario.agents:
        cached = cache.get(agent.name)
        if cached and cached.get("summary") == agent.summary and cached.get("phrases"):
            agent.entities = {**agent.entities, PHRASE_KIND: cached["phrases"]}
            with_phrases += 1

    print(f"Seeded {len(names)} agents into the original and {len(apps)} version(s) of this branch (seed {args.seed}).")
    print(f"  {ORIGINAL_LABEL}: {roster}")
    for app in apps:
        registry = build_registry(scenario, app.data / "execution_agents" / "registry.db")
        print(f"  {app.label}: {registry.db_path} ({with_phrases} with search phrases)")
    if args.variants:
        print("\nStart each in its own terminal:\n")
        for key in (key for key in VARIANT_PORTS if any(app.data == variant_data(key) for app in apps)):
            print(f"  python tools/run_mine.py --variant {key}")
        print("  python tools/run_original.py")
        print("\nThen try messages like these (or run them all: python tools/demo.py --variants ... cases):\n")
    else:
        print("\nStart both servers (two terminals), then try messages like these:\n")
    variants = f"--variants {args.variants} " if args.variants else ""
    for case in sample_cases(scenario, 1):
        right = "a NEW agent" if case.gold.startswith("__") else case.gold
        print(f'  python tools/demo.py {variants}ask "{case.message}"')
        print(f"      right answer: {right}   ({case.category})")


def sample_cases(scenario, per_category: int):
    """The first cases of each category, in the eval's order."""
    taken: Dict[str, int] = {}
    picked = []
    for case in scenario.cases:
        if taken.get(case.category, 0) < per_category:
            taken[case.category] = taken.get(case.category, 0) + 1
            picked.append(case)
    return picked


# ------------------------------------------------------------------------- ask


def history(base: str) -> List[Dict[str, str]]:
    import httpx

    return httpx.get(f"{base}/api/v1/chat/history", timeout=10).json().get("messages", [])


def send_and_wait(base: str, message: str, timeout: float) -> Tuple[List[str], Optional[str], float]:
    """Send one message, then wait until the app's replies stop changing.

    Returns the replies, an error if the app wasn't reachable, and seconds until the last reply arrived.
    """
    import httpx

    started = time.time()
    try:
        before = len(history(base))
        httpx.post(f"{base}/api/v1/chat/send", json={"messages": [{"role": "user", "content": message}]}, timeout=60)
    except httpx.HTTPError as exc:
        return [], f"not reachable ({exc.__class__.__name__}). Is it running?", 0.0
    last_change, seen = time.time(), before
    while time.time() - started < timeout:
        time.sleep(POLL_SECONDS)
        count = len(history(base))
        if count != seen:
            seen, last_change = count, time.time()
        replies = count - before - 1  # minus the user's own message
        quiet = time.time() - last_change
        # Usually two replies: "on it", then the agent's result. A direct answer is a single reply,
        # so after one reply wait longer, in case an agent is still working
        if (replies >= 2 and quiet > QUIET_AFTER_RESULT) or (replies == 1 and quiet > QUIET_AFTER_ONE_REPLY):
            break
    new = history(base)[before:]
    return [m["content"] for m in new if m.get("role") != "user"], None, last_change - started


def original_state(original: Path) -> Tuple[List[str], Dict[str, float]]:
    folder = original / "server" / "data" / "execution_agents"
    roster = json.loads((folder / "roster.json").read_text(encoding="utf-8")) if (folder / "roster.json").exists() else []
    logs = {path.stem: path.stat().st_mtime for path in folder.glob("*.log")}
    return roster, logs


def ours_decisions(data: Path = OURS_DATA) -> List[dict]:
    path = data / "execution_agents" / "routing_decisions.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def ours_prompt_size(message: str, app: App) -> Tuple[int, int]:
    """(agents this copy shows for the message, agents that exist), using the server's own code and the copy's settings."""
    from server.config import get_settings
    from server.services.execution.registry import AgentRegistry
    from server.services.execution.retrieval import AgentRetriever

    path = app.data / "execution_agents" / "registry.db"
    if not path.exists():
        return 0, 0
    if app.settings is not None:
        from evals.compare import apply_settings
        from evals.configs import OFF

        apply_settings({**OFF, **app.settings})
    settings, registry = get_settings(), AgentRegistry(path)
    retriever = AgentRetriever(registry)
    shortlist = retriever.shortlist(
        message, retrieved=settings.agent_shortlist_retrieved, recent=settings.agent_shortlist_recent
    )
    related = retriever.named_people_agents(
        message, exclude=[item.name for item in shortlist], limit=settings.agent_named_people_limit
    )
    return len(shortlist) + len(related), registry.count()


def usage_cost(rows: List[dict], prices: Dict[str, Tuple[float, float]]) -> float:
    total = 0.0
    for row in rows:
        per_in, per_out = prices.get(row["model"], (0.0, 0.0))
        total += row["prompt_tokens"] * per_in + row["completion_tokens"] * per_out
    return total


def usage_lines(rows: List[dict], prices: Dict[str, Tuple[float, float]], seconds: float) -> List[str]:
    """Cost, tokens and time for one app's share of one message."""
    if not rows:
        return [f"  time to final reply: {seconds:.1f}s   (no model calls recorded; was it started with tools/run_*.py?)"]

    total_in = sum(row["prompt_tokens"] for row in rows)
    total_out = sum(row["completion_tokens"] for row in rows)
    thinking = sum(row["reasoning_tokens"] for row in rows)
    priced = "" if all(row["model"] in prices for row in rows) else " (prices unavailable)"
    lines = [
        f"  cost: ${usage_cost(rows, prices):.4f}{priced}   model calls: {len(rows)}   "
        f"tokens: {total_in:,} in, {total_out:,} out ({thinking:,} thinking)   time to final reply: {seconds:.1f}s"
    ]
    by_part: Dict[str, List[dict]] = {}
    for row in rows:
        by_part.setdefault(row["part"], []).append(row)
    for part, part_rows in by_part.items():
        lines.append(
            f"      {part}: {len(part_rows)} call(s), {sum(r['prompt_tokens'] for r in part_rows):,} in, "
            f"{sum(r['completion_tokens'] for r in part_rows):,} out, ${usage_cost(part_rows, prices):.4f}"
        )
    return lines


def is_right(right: Optional[str], used: List[Tuple[str, bool]]) -> Optional[bool]:
    """None for a message without a label. A NEW label is right when every agent used was new."""
    from evals.scenarios import NEW

    if right is None:
        return None
    if not used:
        return False
    if right == NEW:
        return all(created for _, created in used)
    return any(name.lower() == right.lower() for name, _ in used)


def routing_per_call(usage: List[dict]) -> Optional[float]:
    calls = [r["prompt_tokens"] for r in usage if r["part"] == "interaction agent (routing)"]
    return sum(calls) / len(calls) if calls else None


def ask_one(args, apps: List[App], message: str, right: Optional[str]) -> Dict[str, dict]:
    """Send one message to the original and every copy, print what each did, and return a row per app."""
    import usage_meter
    from evals.llm import fetch_prices
    from evals.scenarios import NEW

    roster_before, logs_before = original_state(args.original)
    decisions_before = {app.label: len(ours_decisions(app.data)) for app in apps}
    sizes = {app.label: ours_prompt_size(message, app) for app in apps}

    names = "both apps" if len(apps) == 1 else f"the original and {len(apps)} versions"
    print(f'Asking {names}: "{message}"\n')
    started = time.time()
    with ThreadPoolExecutor(len(apps) + 1) as pool:
        original_job = pool.submit(send_and_wait, ORIGINAL_URL, message, args.timeout)
        jobs = {app.label: pool.submit(send_and_wait, app.url, message, args.timeout) for app in apps}
        original_replies, original_error, original_seconds = original_job.result()
        answers = {label: job.result() for label, job in jobs.items()}
    prices = fetch_prices()
    original_usage = usage_meter.read(args.original / "server" / "data", since=started)

    # Original: which agents ran (their log files changed) and whether its roster grew
    roster_after, logs_after = original_state(args.original)
    created = [name for name in roster_after if name not in roster_before]
    by_slug = {slugify(name): name for name in roster_after}
    ran = [by_slug.get(slug, slug) for slug, mtime in logs_after.items() if mtime > logs_before.get(slug, 0)]

    print("=" * 70)
    print(f"ORIGINAL ({ORIGINAL_URL}): all {len(roster_before)} agent names were in its prompt for this message")
    print("=" * 70)
    if original_error:
        print(f"  {original_error}")
    for reply in original_replies:
        print(f"  reply: {reply}")
    for name in ran:
        print(f"  agent used: {name}" + ("   <- NEW agent created" if name in created else "   (existing)"))
    if not ran:
        print("  agent used: none (no execution agent ran)")
    for line in usage_lines(original_usage, prices, original_seconds):
        print(line)
    original_used = [(name, name in created) for name in ran]
    rows = {ORIGINAL_LABEL: {
        # An app that wasn't reachable isn't scored
        "used": original_used, "right": None if original_error else is_right(right, original_used), "shown": len(roster_before),
        "error": original_error, "seconds": original_seconds, "cost": usage_cost(original_usage, prices),
        "routing_per_call": routing_per_call(original_usage),
    }}

    ours_usage: List[dict] = []
    for app in apps:
        replies, error, seconds = answers[app.label]
        usage = usage_meter.read(app.data, since=started)
        shown, total = sizes[app.label]
        print()
        print("=" * 70)
        print(f"{app.label.upper()} ({app.url}): {shown} of its {total} agents were in its prompt for this message")
        print("=" * 70)
        if error:
            print(f"  {error}")
        for reply in replies:
            print(f"  reply: {reply}")
        new_decisions = ours_decisions(app.data)[decisions_before[app.label]:]
        for decision in new_decisions:
            tag = "   <- NEW agent created" if decision.get("created") else f"   (existing, matched by: {decision.get('reason')})"
            print(f"  agent used: {decision.get('agent_name')}{tag}")
            if decision.get("possible_duplicates"):
                print(f"      look-alikes it reported: {', '.join(decision['possible_duplicates'][:3])}")
        if not new_decisions:
            print("  agent used: none (no execution agent ran)")
        for line in usage_lines(usage, prices, seconds):
            print(line)
        used = [(d.get("agent_name", ""), bool(d.get("created"))) for d in new_decisions]
        rows[app.label] = {
            "used": used, "right": None if error else is_right(right, used), "shown": shown, "error": error,
            "seconds": seconds, "cost": usage_cost(usage, prices), "routing_per_call": routing_per_call(usage),
        }
        ours_usage = usage

    if right is not None:
        print()
        print(f"RIGHT ANSWER (from the eval's labels): {'a NEW agent' if right == NEW else right}")

    if len(apps) == 1:
        for line in routing_lines(original_usage, ours_usage):
            print(line)
    else:
        print()
        for line in summary_lines(rows):
            print(line)

    record = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "message": message, "right_answer": right,
              "results": {label: {**row, "used": [name for name, _ in row["used"]]} for label, row in rows.items()}}
    path = scores_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return rows


def summary_lines(rows: Dict[str, dict]) -> List[str]:
    """One line per app for one message: what it used, whether that was right, and what it cost."""
    width = max(len(label) for label in rows)
    lines = ["SUMMARY", f"  {'':<{width}}  right?  agents in prompt  routing tokens/call  cost     time   agent used"]
    for label, row in rows.items():
        verdict = {True: "yes", False: "NO", None: "-"}[row["right"]]
        per_call = f"{row['routing_per_call']:,.0f}" if row["routing_per_call"] else "-"
        used = ", ".join(f"{name}{' (new)' if new else ''}" for name, new in row["used"]) or ("not reachable" if row["error"] else "none")
        lines.append(f"  {label:<{width}}  {verdict:<6}  {row['shown']:<16}  {per_call:<19}  ${row['cost']:.4f}  "
                     f"{row['seconds']:4.0f}s  {used}")
    return lines


def ask(args) -> None:
    right = next((case.gold for case in scenario_for(args).cases if case.message == args.message), None)
    ask_one(args, apps_for(args), args.message, right)


def cases(args) -> None:
    apps = apps_for(args)
    picked = sample_cases(scenario_for(args), args.per_category)
    print(f"Sending {len(picked)} labelled messages to the original and {len(apps)} version(s), one at a time. "
          f"Each costs about a cent per app, so about ${len(picked) * (len(apps) + 1) * 0.01:.2f} in all.\n")
    results = []
    for number, case in enumerate(picked, 1):
        print(f"\n######## {number}/{len(picked)}  ({case.category})\n")
        results.append(ask_one(args, apps, case.message, case.gold))
    print()
    for line in score_lines([{label: row["right"] for label, row in rows.items()} for rows in results], "this run"):
        print(line)


def score_lines(verdicts: List[Dict[str, Optional[bool]]], which: str) -> List[str]:
    """Right / labelled per app over several messages."""
    labels: List[str] = []
    for verdict in verdicts:
        labels += [label for label in verdict if label not in labels]
    width = max([len(label) for label in labels] + [7])
    lines = [f"SCORE ({which}): labelled messages each app got right"]
    for label in labels:
        marks = [verdict[label] for verdict in verdicts if verdict.get(label) is not None]
        lines.append(f"  {label:<{width}}  {sum(marks)}/{len(marks)}")
    return lines


# ------------------------------------------------------------------------- status


def routing_lines(original_usage: List[dict], ours_usage: List[dict]) -> List[str]:
    """Routing input tokens, per call as well as in total: the apps can make a different number of calls."""
    routing = "interaction agent (routing)"
    original = [r["prompt_tokens"] for r in original_usage if r["part"] == routing]
    ours = [r["prompt_tokens"] for r in ours_usage if r["part"] == routing]
    if not original or not ours:
        return []
    original_each, ours_each = sum(original) / len(original), sum(ours) / len(ours)
    return [
        "",
        f"ROUTING PROMPT (the part the agent list is in), input tokens per call: "
        f"original {original_each:,.0f} vs this branch {ours_each:,.0f} ({original_each / ours_each:.1f}x)",
        f"  in total for this message: original {sum(original):,} over {len(original)} call(s) vs "
        f"this branch {sum(ours):,} over {len(ours)} call(s)",
    ]


def status(args) -> None:
    from server.services.execution.registry import AgentRegistry

    apps = apps_for(args)
    roster, logs = original_state(args.original)
    print(f"Original: {len(roster)} agents in roster.json, {len(logs)} have run")
    for app in apps:
        registry_path = app.data / "execution_agents" / "registry.db"
        count = AgentRegistry(registry_path).count() if registry_path.exists() else 0
        decisions = ours_decisions(app.data)
        print(f"{app.label}: {count} agents in the registry, {len(decisions)} routing decisions logged")
        for decision in decisions[-5:]:
            print(f"  {decision.get('requested_name')!r} -> {decision.get('agent_name')!r} ({decision.get('reason')})")
    path = scores_path(args)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
    labelled = [{label: row["right"] for label, row in r["results"].items()} for r in records if r["right_answer"] is not None]
    if labelled:
        print()
        for line in score_lines(labelled, f"since the last seed, {len(labelled)} labelled of {len(records)} messages"):
            print(line)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--original", type=Path, default=DEFAULT_ORIGINAL, help="folder with the original checked out")
    parser.add_argument("--agents", type=int, default=300)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--variants", help=f"every, or a comma-separated list of: {', '.join(VARIANT_PORTS)}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="fill the apps with the same synthetic agents")
    ask_parser = commands.add_parser("ask", help="send one message to every app and compare")
    ask_parser.add_argument("message")
    ask_parser.add_argument("--timeout", type=float, default=150)
    cases_parser = commands.add_parser("cases", help="send labelled eval cases to every app and score them")
    cases_parser.add_argument("--per-category", type=int, default=1, help="cases per category (10 categories)")
    cases_parser.add_argument("--timeout", type=float, default=150)
    commands.add_parser("status", help="what each app has so far, and the score")
    args = parser.parse_args(argv)
    args.original = args.original.resolve()
    {"seed": seed, "ask": ask, "cases": cases, "status": status}[args.command](args)


if __name__ == "__main__":
    main()
