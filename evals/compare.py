"""The three approaches side by side, on the same cases, and write evals/results/comparison.md and .json.

    python -m evals.compare                    # simulated router, free: generated set + hand-written paraphrases
    python -m evals.compare --set generated    # one set only
    python -m evals.compare --skip-hybrid      # without the embedding model installed

Each row switches settings in this process and reruns the same seed, so the only difference between
rows is the approach. "main" is agent-overload as submitted (all three off). The simulated router
matches words, so on the paraphrase set read the recall columns, not accuracy (see docs/EVALUATION.md).
Real-model numbers per approach are in each approach's own write-up.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from server.config import get_settings
from server.services.execution import entities

from .configs import CONFIGS, OFF
from .harness import ArmMetrics, run_simulated
from .scenarios import PAST_REQUEST, build_scenario
from .stats import mcnemar_exact, paired_flips

RESULTS_DIR = Path(__file__).parent / "results"


def apply_settings(values: Dict[str, str]) -> None:
    """Set the switches for one row on the cached Settings and on entities.FOLD_PLURALS.

    Settings field defaults read the environment once, at import, so changing os.environ alone does
    nothing; the cached instance is the one every module's get_settings() returns, so it is set directly.
    """
    os.environ.update(values)
    entities.FOLD_PLURALS = values["OPENPOKE_FOLD_PLURALS"] != "0"
    settings = get_settings()
    settings.agent_name_hints = values["OPENPOKE_AGENT_NAME_HINTS"] != "0"
    settings.agent_index_requests = values["OPENPOKE_AGENT_INDEX_REQUESTS"] != "0"
    settings.agent_retriever = values["OPENPOKE_AGENT_RETRIEVER"]


def current_settings() -> Dict[str, str]:
    settings = get_settings()
    return {
        "OPENPOKE_FOLD_PLURALS": "1" if entities.FOLD_PLURALS else "0",
        "OPENPOKE_AGENT_NAME_HINTS": "1" if settings.agent_name_hints else "0",
        "OPENPOKE_AGENT_INDEX_REQUESTS": "1" if settings.agent_index_requests else "0",
        "OPENPOKE_AGENT_RETRIEVER": settings.agent_retriever,
    }


def embedder_available() -> bool:
    from server.services.execution.embeddings import get_embedder

    return get_embedder() is not None


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _right(decisions: List[dict]) -> Dict[str, bool]:
    return {d["case_id"]: d["chosen"] == d["gold"] for d in decisions}


def load_paraphrase_scenario(agents: int, seed: int):
    from .llm import load_phrases, phrases_file
    from .paraphrase_set import build_paraphrase_scenario

    scenario = build_paraphrase_scenario(agents, seed=seed)
    return scenario, load_phrases(scenario.agents, RESULTS_DIR / phrases_file(agents))


def run_set(name: str, args, configs) -> Dict[str, object]:
    if name == "generated":
        scenario, phrases = build_scenario(args.agents, seed=args.seed), None
    else:
        scenario, phrases = load_paraphrase_scenario(args.agents, args.seed)
    rows: Dict[str, dict] = {}
    original: Optional[ArmMetrics] = None
    original_right: Dict[str, bool] = {}
    main_right: Dict[str, bool] = {}
    for key, label, values in configs:
        apply_settings({**OFF, **values})
        started = time.perf_counter()
        result = run_simulated(scenario, threshold=args.threshold, phrases=phrases,
                               retriever=get_settings().agent_retriever)
        seconds = time.perf_counter() - started
        new: ArmMetrics = result["solution"]
        right = _right(result["decisions"]["solution"])
        if original is None:
            original, original_right = result["baseline"], _right(result["decisions"]["baseline"])
        if key == "main":
            main_right = right
        fixed, broke = paired_flips(main_right, right) if main_right else (0, 0)
        rows[key] = {
            "label": label,
            "settings": values,
            "metrics": new.__dict__,
            "right": sum(right.values()),
            "cases": len(right),
            "fixed_vs_main": fixed,
            "broke_vs_main": broke,
            "p_vs_main": mcnemar_exact(fixed, broke),
            "conversation_success": result["conversations"]["solution"].conversation_success
            if scenario.conversations else None,
            "seconds": seconds,
        }
        print(f"  {name}: {label}: {sum(right.values())}/{len(right)} right ({seconds:.0f}s)", flush=True)
    return {
        "cases": len(scenario.cases),
        "agents": len(scenario.agents),
        "original": {"metrics": original.__dict__, "right": sum(original_right.values()), "cases": len(original_right)},
        "rows": rows,
    }


def set_table(name: str, data: Dict[str, object]) -> List[str]:
    paraphrase = name == "paraphrase"
    extra = "Topic-only in prompt" if paraphrase else "Past-request cases right"
    lines = [
        f"| Version | Right | Duplicates | Wrong agent | Right agent in prompt | In search top 8 | {extra} "
        f"| Fixed / broke vs main | Roster tokens | Routing p50 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    def extra_value(metrics: dict) -> str:
        if paraphrase:
            return _pct(metrics["recall_by_category"].get("handwritten_topic_only"))
        return _pct(metrics["by_category"].get(PAST_REQUEST))

    original = data["original"]
    m = original["metrics"]
    lines.append(
        f"| Original OpenPoke | {original['right']}/{original['cases']} | {_pct(m['duplicate_rate'])} "
        f"| {_pct(m['wrong_agent_rate'])} | all shown | n/a | {extra_value(m)} | | "
        f"{m['roster_tokens_mean']:,.0f} | {m['latency_ms_p50']:.0f} ms |"
    )
    for key, row in data["rows"].items():
        m = row["metrics"]
        flips = "" if key == "main" else (
            f"{row['fixed_vs_main']} / {row['broke_vs_main']}"
            + (f" (p = {row['p_vs_main']:.2g})" if row["fixed_vs_main"] + row["broke_vs_main"] else "")
        )
        lines.append(
            f"| {row['label']} | {row['right']}/{row['cases']} | {_pct(m['duplicate_rate'])} "
            f"| {_pct(m['wrong_agent_rate'])} | {_pct(m['recall_shortlist'])} | {_pct(m['recall_search'])} "
            f"| {extra_value(m)} | {flips} | {m['roster_tokens_mean']:,.0f} | {m['latency_ms_p50']:.0f} ms |"
        )
    return lines


SET_TITLES = {
    "generated": "Generated follow-ups (simulated router)",
    "paraphrase": "Hand-written paraphrases, with search phrases (simulated router: read the recall columns)",
}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", choices=["both", "generated", "paraphrase"], default="both")
    parser.add_argument("--agents", type=int, default=300)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--threshold", type=float, default=0.6)
    parser.add_argument("--skip-hybrid", action="store_true", help="leave out the rows that need the embedding model")
    args = parser.parse_args(argv)

    saved = {key: os.environ.get(key) for key in OFF}
    saved_settings = current_settings()
    configs = list(CONFIGS)
    note = ""
    apply_settings({**OFF, "OPENPOKE_AGENT_RETRIEVER": "hybrid"})
    if args.skip_hybrid or not embedder_available():
        configs = [c for c in configs if c[2].get("OPENPOKE_AGENT_RETRIEVER") != "hybrid"]
        note = ("Rows with meaning search are left out: " + ("--skip-hybrid." if args.skip_hybrid else
                "the embedding model isn't available (pip install -r server/requirements-embeddings.txt, "
                "and set OPENPOKE_EMBEDDING_CACHE if the model is in a custom folder)."))
        print(note)

    sets = ["generated", "paraphrase"] if args.set == "both" else [args.set]
    results: Dict[str, object] = {}
    try:
        for name in sets:
            print(f"{SET_TITLES[name]}:", flush=True)
            results[name] = run_set(name, args, configs)
    finally:
        apply_settings(saved_settings)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    md = [
        "# The three approaches, side by side",
        "",
        f"Generated by `python -m evals.compare`. {args.agents} agents, seed {args.seed}, simulated router. "
        "Each row turns one approach on over agent-overload (main); the last row turns all three on. "
        "Fixed / broke counts cases against main on the same cases, with McNemar's exact p.",
    ]
    if note:
        md += ["", note]
    for name in sets:
        data = results[name]
        md += ["", f"## {SET_TITLES[name]}", "", f"{data['cases']} cases, {data['agents']} agents in the history.", ""]
        md += set_table(name, data)
    text = "\n".join(md) + "\n"
    print()
    print(text)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = "comparison" if args.agents == 300 else f"comparison_{args.agents}agents"
    (RESULTS_DIR / f"{stem}.md").write_text(text, encoding="utf-8")
    (RESULTS_DIR / f"{stem}.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"Wrote evals/results/{stem}.md and .json")


if __name__ == "__main__":
    main()
