"""Side-by-side demo: the original OpenPoke and this branch, with the same 300 agents.

    python tools/demo.py seed                      # fill both apps with the same synthetic history
    python -m server.server                        # this branch, port 8001 (own terminal)
    python tools/run_original.py                   # the original, port 8002 (own terminal)
    python tools/demo.py ask "Did Sarah Chen reply about the Q3 invoice?"
    python tools/demo.py status

`seed` loads the eval's synthetic history (300 agents, seed 7) into both apps before any chat:
the original gets the names in its roster.json, this branch gets the same agents in its registry,
with their summaries and cached search phrases. The agents are pretend: there is no real email
behind them. But which agent each app picks for a message is real, and that's what `ask` shows.
`seed` never deletes anything: existing data is moved to a dated backup folder first.

`ask` sends one message to both apps, waits for their replies, and prints which agent each used.
If the message is one of the eval's labelled cases, it also prints the right answer.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

DEFAULT_ORIGINAL = HERE.parent / "openpoke-original"
OURS_URL, ORIGINAL_URL = "http://localhost:8001", "http://localhost:8002"
OURS_DATA = HERE / "server" / "data"
PHRASES_CACHE = HERE / "evals" / "results" / "agent_phrases.json"


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
    shutil.move(str(folder), str(backup))
    return backup


# ------------------------------------------------------------------------- seed


def seed(args) -> None:
    from evals.harness import build_registry
    from server.services.execution.phrases import PHRASE_KIND

    original_data = args.original / "server" / "data"
    if not (args.original / "server" / "app.py").is_file():
        sys.exit(f'No original checkout at {args.original}. Run: git worktree add "{args.original}" main')

    for folder in (OURS_DATA, original_data):
        backup = move_aside(folder)
        if backup:
            print(f"Moved existing data aside: {backup}")

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
    registry = build_registry(scenario, OURS_DATA / "execution_agents" / "registry.db")

    print(f"Seeded {len(names)} agents into both apps (seed {args.seed}).")
    print(f"  original:    {roster}")
    print(f"  this branch: {registry.db_path} ({with_phrases} with search phrases)")
    print("\nStart both servers (two terminals), then try messages like these:\n")
    shown = set()
    for case in scenario.cases:
        if case.category in shown:
            continue
        shown.add(case.category)
        right = "a NEW agent" if case.gold.startswith("__") else case.gold
        print(f'  python tools/demo.py ask "{case.message}"')
        print(f"      right answer: {right}   ({case.category})")


# ------------------------------------------------------------------------- ask


def history(base: str) -> List[Dict[str, str]]:
    import httpx

    return httpx.get(f"{base}/api/v1/chat/history", timeout=10).json().get("messages", [])


def send_and_wait(base: str, message: str, timeout: float) -> Tuple[List[str], Optional[str]]:
    """Send one message, then wait until the app's replies stop changing."""
    import httpx

    try:
        before = len(history(base))
        httpx.post(f"{base}/api/v1/chat/send", json={"messages": [{"role": "user", "content": message}]}, timeout=60)
    except httpx.HTTPError as exc:
        return [], f"not reachable ({exc.__class__.__name__}). Is it running?"
    started, last_change, seen = time.time(), time.time(), before
    while time.time() - started < timeout:
        time.sleep(2)
        count = len(history(base))
        if count != seen:
            seen, last_change = count, time.time()
        replies = count - before - 1  # minus the user's own message
        if replies >= 2 and time.time() - last_change > 8:  # the "on it" reply plus the agent's result
            break
    new = history(base)[before:]
    return [m["content"] for m in new if m.get("role") != "user"], None


def original_state(original: Path) -> Tuple[List[str], Dict[str, float]]:
    folder = original / "server" / "data" / "execution_agents"
    roster = json.loads((folder / "roster.json").read_text(encoding="utf-8")) if (folder / "roster.json").exists() else []
    logs = {path.stem: path.stat().st_mtime for path in folder.glob("*.log")}
    return roster, logs


def ours_decisions() -> List[dict]:
    path = OURS_DATA / "execution_agents" / "routing_decisions.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def ask(args) -> None:
    from evals.scenarios import NEW

    right = next((case.gold for case in scenario_for(args).cases if case.message == args.message), None)
    roster_before, logs_before = original_state(args.original)
    decisions_before = len(ours_decisions())

    print(f'Asking both apps: "{args.message}"\n')
    with ThreadPoolExecutor(2) as pool:
        ours_job = pool.submit(send_and_wait, OURS_URL, args.message, args.timeout)
        original_job = pool.submit(send_and_wait, ORIGINAL_URL, args.message, args.timeout)
        ours_replies, ours_error = ours_job.result()
        original_replies, original_error = original_job.result()

    # Original: which agents ran (their log files changed) and whether its roster grew
    roster_after, logs_after = original_state(args.original)
    created = [name for name in roster_after if name not in roster_before]
    by_slug = {slugify(name): name for name in roster_after}
    ran = [by_slug.get(slug, slug) for slug, mtime in logs_after.items() if mtime > logs_before.get(slug, 0)]

    print("=" * 70)
    print(f"ORIGINAL ({ORIGINAL_URL}): {len(roster_after)} agents, all {len(roster_before)} names in every prompt")
    print("=" * 70)
    if original_error:
        print(f"  {original_error}")
    for reply in original_replies:
        print(f"  reply: {reply}")
    for name in ran:
        print(f"  agent used: {name}" + ("   <- NEW agent created" if name in created else "   (existing)"))
    if not ran:
        print("  agent used: none (no execution agent ran)")

    print()
    print("=" * 70)
    print(f"THIS BRANCH ({OURS_URL}): shortlist of about 12 agents per turn")
    print("=" * 70)
    if ours_error:
        print(f"  {ours_error}")
    for reply in ours_replies:
        print(f"  reply: {reply}")
    new_decisions = ours_decisions()[decisions_before:]
    for decision in new_decisions:
        tag = "   <- NEW agent created" if decision.get("created") else f"   (existing, matched by: {decision.get('reason')})"
        print(f"  agent used: {decision.get('agent_name')}{tag}")
        if decision.get("possible_duplicates"):
            print(f"      look-alikes it reported: {', '.join(decision['possible_duplicates'][:3])}")
    if not new_decisions:
        print("  agent used: none (no execution agent ran)")

    if right is not None:
        print()
        print(f"RIGHT ANSWER (from the eval's labels): {'a NEW agent' if right == NEW else right}")


# ------------------------------------------------------------------------- status


def status(args) -> None:
    from server.services.execution.registry import AgentRegistry

    roster, logs = original_state(args.original)
    print(f"Original:    {len(roster)} agents in roster.json, {len(logs)} have run")
    registry_path = OURS_DATA / "execution_agents" / "registry.db"
    count = AgentRegistry(registry_path).count() if registry_path.exists() else 0
    decisions = ours_decisions()
    print(f"This branch: {count} agents in the registry, {len(decisions)} routing decisions logged")
    for decision in decisions[-5:]:
        print(f"  {decision.get('requested_name')!r} -> {decision.get('agent_name')!r} ({decision.get('reason')})")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--original", type=Path, default=DEFAULT_ORIGINAL, help="folder with the original checked out")
    parser.add_argument("--agents", type=int, default=300)
    parser.add_argument("--seed", type=int, default=7)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="fill both apps with the same synthetic agents")
    ask_parser = commands.add_parser("ask", help="send one message to both apps and compare")
    ask_parser.add_argument("message")
    ask_parser.add_argument("--timeout", type=float, default=150)
    commands.add_parser("status", help="what each app has so far")
    args = parser.parse_args(argv)
    args.original = args.original.resolve()
    {"seed": seed, "ask": ask, "status": status}[args.command](args)


if __name__ == "__main__":
    main()
