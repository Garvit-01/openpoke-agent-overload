"""Run the routing evaluation and write evals/results/results.md and results.json.

    python -m evals.run_eval               # full run: 3 seeds, roster sizes 50..800, threshold sweep
    python -m evals.run_eval --quick       # one seed, 300 agents (writes results_quick.*)
    python -m evals.run_eval --router llm  # same cases through a real model (needs OPENROUTER_API_KEY)

Offline runs are deterministic: the same seed gives the same numbers.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Sequence

from .harness import ArmMetrics, run_simulated
from .scenarios import NEW, build_scenario
from .stats import paired_lines

RESULTS_DIR = Path(__file__).parent / "results"
# Assumption for cost estimates only: Claude Sonnet 4 list price for input tokens, USD per million.
# Check current OpenRouter pricing before quoting these numbers.
INPUT_PRICE_PER_MTOK = 3.00


def _pct(value) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _cost_per_1k_turns(tokens: float) -> str:
    return f"${tokens * 1000 / 1_000_000 * INPUT_PRICE_PER_MTOK:.2f}"


def _mean_range(values: Sequence[float]) -> str:
    if len(values) == 1:
        return _pct(values[0])
    return f"{_pct(statistics.fmean(values))} ({_pct(min(values))}-{_pct(max(values))})"


def main_table(base: ArmMetrics, new: ArmMetrics) -> List[str]:
    rows = [
        ("Routing accuracy (all cases)", _pct(base.accuracy), _pct(new.accuracy)),
        ("Duplicate created (right agent existed)", _pct(base.duplicate_rate), _pct(new.duplicate_rate)),
        ("Wrong existing agent used", _pct(base.wrong_agent_rate), _pct(new.wrong_agent_rate)),
        ("False reuse (should have been new)", _pct(base.false_reuse_rate), _pct(new.false_reuse_rate)),
        ("Right agent in prompt shortlist (recall@12)", "100% (shows all)", _pct(new.recall_shortlist)),
        ("Right agent in search_agents top 8", "n/a (no tool)", _pct(new.recall_search)),
        ("Turns that needed search_agents", "n/a", _pct(new.search_rate)),
        ("Roster tokens per turn (approx.)", f"{base.roster_tokens_mean:,.0f}", f"{new.roster_tokens_mean:,.0f}"),
        ("Roster input cost per 1,000 turns*", _cost_per_1k_turns(base.roster_tokens_mean), _cost_per_1k_turns(new.roster_tokens_mean)),
        ("Routing overhead per turn, p50 / p95", f"{base.latency_ms_p50:.1f} / {base.latency_ms_p95:.1f} ms",
         f"{new.latency_ms_p50:.1f} / {new.latency_ms_p95:.1f} ms"),
    ]
    lines = ["| Metric | Original OpenPoke | This change |", "|---|---|---|"]
    lines += [f"| {name} | {left} | {right} |" for name, left, right in rows]
    return lines


def category_table(base: ArmMetrics, new: ArmMetrics, counts: Dict[str, int]) -> List[str]:
    lines = ["| Category | Cases | Original accuracy | New accuracy | New recall@12 |", "|---|---|---|---|---|"]
    for name in base.by_category:
        lines.append(
            f"| {name} | {counts[name]} | {_pct(base.by_category[name])} | {_pct(new.by_category[name])} | "
            f"{_pct(new.recall_by_category.get(name))} |"
        )
    return lines


def conversation_tables(conversations: Dict[str, object]) -> List[str]:
    base, new, no_flag = (
        conversations["baseline"], conversations["solution"], conversations["solution_without_new_task"]
    )
    lines = [
        "| Metric | Original OpenPoke | This change | This change, model never sets new_task |",
        "|---|---|---|---|",
    ]
    for label, attr in [("Turn accuracy (all turns)", "turn_accuracy"),
                        ("Turn accuracy (turns 2+)", "later_turn_accuracy"),
                        ("Conversations with every turn right", "conversation_success")]:
        lines.append(f"| {label} | {_pct(getattr(base, attr))} | {_pct(getattr(new, attr))} | {_pct(getattr(no_flag, attr))} |")
    lines += [
        "",
        "| Kind | Conversations | Original: all turns right | New: all turns right | New, no new_task |",
        "|---|---|---|---|---|",
    ]
    for kind, values in new.by_kind.items():
        lines.append(
            f"| {kind} | {values['conversations']} | {_pct(base.by_kind[kind]['conversation_success'])} | "
            f"{_pct(values['conversation_success'])} | {_pct(no_flag.by_kind[kind]['conversation_success'])} |"
        )
    return lines


def run_offline(args) -> None:
    started = time.perf_counter()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    seeds = [args.seed] if args.quick else args.seeds
    sizes = [] if args.quick else args.sizes
    thresholds = [] if args.quick else args.thresholds

    # 1. Main comparison at the headline roster size, over several seeds
    runs = []
    for seed in seeds:
        scenario = build_scenario(args.agents, seed=seed)
        runs.append((scenario, run_simulated(scenario, threshold=args.threshold)))
    main_scenario, main = runs[0]

    # 2. Scaling: does accuracy hold, and do tokens stay flat, as the roster grows?
    scaling = []
    for size in sizes:
        result = run_simulated(build_scenario(size, seed=args.seed), threshold=args.threshold)
        scaling.append((size, result["baseline"], result["solution"]))

    # 3. Router threshold sweep: is the result an artefact of one setting?
    sweep = []
    for threshold in thresholds:
        result = run_simulated(main_scenario, threshold=threshold)
        sweep.append((threshold, result["baseline"], result["solution"]))

    counts: Dict[str, int] = {}
    for case in main_scenario.cases:
        counts[case.category] = counts.get(case.category, 0) + 1
    messages = {case.id: case.message for case in main_scenario.cases}

    md: List[str] = [
        "# Routing evaluation results",
        "",
        f"Generated by `python -m evals.run_eval{' --quick' if args.quick else ''}`. "
        "Synthetic data, simulated router (see the README for what that means and its limits).",
        "",
        f"## Main comparison: {args.agents} agents, {len(main_scenario.cases)} labelled follow-ups, seed {seeds[0]}",
        "",
        *main_table(main["baseline"], main["solution"]),
        "",
        f"\\* Input tokens for the roster section only, at an assumed ${INPUT_PRICE_PER_MTOK:.2f} per million "
        "input tokens. Tokens are estimated as characters / 4.",
        "",
        "### Is the difference real?",
        "",
        *paired_lines(
            {d["case_id"]: d["chosen"] == d["gold"] for d in main["decisions"]["baseline"]},
            {d["case_id"]: d["chosen"] == d["gold"] for d in main["decisions"]["solution"]},
        ),
        "",
        "### By category",
        "",
        *category_table(main["baseline"], main["solution"], counts),
    ]

    conversations = main["conversations"]
    turn_count = conversations["solution"].turns
    md += [
        "",
        f"## Multi-turn conversations: {conversations['solution'].conversations} conversations, {turn_count} turns",
        "",
        "State carries over between turns: an agent created in turn 1 exists in turn 2, and a wrong pick early "
        "on makes \"ok, send it\" go to the wrong agent too. Turns like \"send it\" are resolved from the "
        "conversation the same way in both arms.",
        "",
        *conversation_tables(conversations),
    ]
    conversation_failures = [d for d in conversations["turns"]["solution"] if not d["correct"]]
    if conversation_failures:
        md += ["", "| Conversation | Turn | Message | Right answer | Chosen |", "|---|---|---|---|---|"]
        md += [
            f"| {d['conversation_id']} | {d['turn']} | {d['message']} | {d['expected']} | {d['chosen']} |"
            for d in conversation_failures
        ]

    if len(runs) > 1:
        md += [
            "",
            f"### Across seeds {', '.join(str(seed) for seed in seeds)} (mean, min-max)",
            "",
            "| Metric | Original OpenPoke | This change |",
            "|---|---|---|",
        ]
        for label, attr in [("Accuracy", "accuracy"), ("Duplicate rate", "duplicate_rate"),
                            ("Wrong agent rate", "wrong_agent_rate"), ("False reuse rate", "false_reuse_rate")]:
            base_values = [getattr(result["baseline"], attr) for _, result in runs]
            new_values = [getattr(result["solution"], attr) for _, result in runs]
            md.append(f"| {label} | {_mean_range(base_values)} | {_mean_range(new_values)} |")
        base_values = [result["conversations"]["baseline"].conversation_success for _, result in runs]
        new_values = [result["conversations"]["solution"].conversation_success for _, result in runs]
        md.append(f"| Conversations with every turn right | {_mean_range(base_values)} | {_mean_range(new_values)} |")

    if scaling:
        md += [
            "",
            "## As the roster grows",
            "",
            "| Agents | Original accuracy | New accuracy | Original roster tokens | New roster tokens |",
            "|---|---|---|---|---|",
        ]
        for size, base, new in scaling:
            md.append(
                f"| {size} | {_pct(base.accuracy)} | {_pct(new.accuracy)} | "
                f"{base.roster_tokens_mean:,.0f} | {new.roster_tokens_mean:,.0f} |"
            )

    if sweep:
        md += [
            "",
            "## Router threshold sweep (keyword-only messages)",
            "",
            "| Threshold | Original accuracy | New accuracy |",
            "|---|---|---|",
        ]
        md += [f"| {threshold} | {_pct(base.accuracy)} | {_pct(new.accuracy)} |" for threshold, base, new in sweep]

    failures = [d for d in main["decisions"]["solution"] if d["chosen"] != d["gold"]]
    md += [
        "",
        f"## Every failure of the new version ({len(failures)})",
        "",
        "| Category | Message | Right answer | Chosen |",
        "|---|---|---|---|",
    ]
    for decision in failures:
        right = "new agent" if decision["gold"] == NEW else decision["gold"]
        chosen = "new agent" if decision["chosen"] == NEW else decision["chosen"]
        md.append(f"| {decision['category']} | {messages[decision['case_id']]} | {right} | {chosen} |")

    md += ["", f"Run time: {time.perf_counter() - started:.0f}s."]
    # Quick runs get their own files so they never overwrite the committed full results
    stem = "results_quick" if args.quick else "results"
    (RESULTS_DIR / f"{stem}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (RESULTS_DIR / f"{stem}.json").write_text(
        json.dumps(
            {
                "agents": args.agents,
                "seeds": seeds,
                "threshold": args.threshold,
                "main": {arm: asdict(main[arm]) for arm in ("baseline", "solution")},
                "seeds_runs": [
                    {"seed": scenario.seed, **{arm: asdict(result[arm]) for arm in ("baseline", "solution")}}
                    for scenario, result in runs
                ],
                "scaling": [
                    {"agents": size, "baseline": asdict(base), "solution": asdict(new)} for size, base, new in scaling
                ],
                "threshold_sweep": [
                    {"threshold": threshold, "baseline": asdict(base), "solution": asdict(new)}
                    for threshold, base, new in sweep
                ],
                "decisions": main["decisions"],
                "conversations": {
                    arm: asdict(main["conversations"][arm])
                    for arm in ("baseline", "solution", "solution_without_new_task")
                },
                "conversation_turns": main["conversations"]["turns"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("\n".join(md))
    print(f"\nWrote {RESULTS_DIR / (stem + '.md')}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--router", choices=["simulated", "llm"], default="simulated")
    parser.add_argument("--agents", type=int, default=300)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 11, 23])
    parser.add_argument("--sizes", type=int, nargs="+", default=[50, 100, 200, 400, 800])
    parser.add_argument("--threshold", type=float, default=0.6)
    parser.add_argument("--thresholds", type=float, nargs="+", default=[0.5, 0.6, 0.75])
    parser.add_argument("--quick", action="store_true")
    # LLM router options
    # A cheap current model; OpenRouter no longer serves claude-sonnet-4, the app's old default
    parser.add_argument("--model", default="anthropic/claude-haiku-5.5")
    parser.add_argument("--limit", type=int, default=40, help="LLM mode: number of cases (cost control)")
    parser.add_argument("--repeats", type=int, default=3, help="LLM mode: runs per case, to report a pass rate")
    parser.add_argument("--rescore", action="store_true", help="LLM mode: re-report the saved run for --model, no API calls")
    parser.add_argument("--concurrency", type=int, default=4)
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    if args.router == "llm":
        from .llm import run_llm

        run_llm(args)
    else:
        run_offline(args)


if __name__ == "__main__":
    main()
