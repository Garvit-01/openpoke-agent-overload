"""The three approaches side by side on a real model, and write evals/results/comparison_llm.md and .json.

    python -m evals.compare_llm                 # all rows, both sets: about $2.30 on Haiku 5.5, about an hour
    python -m evals.compare_llm --reuse         # build the table from runs already saved, no API calls

Each row is one `python -m evals.run_eval --router llm --phrases` run in its own process, with the row's
settings in the environment, saved under a `_cmp_<row>` tag. All rows run the same cases (the usual 40
plus 6 past-request cases, and the 24 hand-written paraphrases), the same seed and the same model, on
the same day. The original runs in every row too, which shows how much the model varies between runs.
Needs OPENROUTER_API_KEY, and the embedding model for the hybrid rows (OPENPOKE_EMBEDDING_CACHE).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .compare import CONFIGS, OFF
from .stats import majority_right, mcnemar_exact, paired_flips

RESULTS_DIR = Path(__file__).parent / "results"
SETS = {"generated": "Generated follow-ups (40 cases + 6 past-request cases)", "paraphrase": "Hand-written paraphrases (24)"}


def result_path(model: str, set_name: str, key: str, hybrid: bool) -> Path:
    """The file run_eval writes for one row (same naming as evals/llm.py)."""
    stem = "llm_results_" + re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    stem += "_paraphrase" if set_name == "paraphrase" else ""
    stem += "_phrases" + ("_hybrid" if hybrid else "") + f"_cmp_{key}"
    return RESULTS_DIR / f"{stem}.json"


def run_row(model: str, set_name: str, key: str, values: Dict[str, str]) -> int:
    env = {**os.environ, **OFF, **values}
    command = [sys.executable, "-m", "evals.run_eval", "--router", "llm", "--phrases", "--model", model,
               "--retriever", env["OPENPOKE_AGENT_RETRIEVER"], "--tag", f"cmp_{key}"]
    if set_name == "paraphrase":
        command += ["--set", "paraphrase"]
    print(f"  {set_name}: {key} ...", flush=True)
    return subprocess.run(command, env=env).returncode


def arm_stats(saved: dict, arm: str, prices) -> dict:
    outcomes = [o for o in saved["outcomes"] if o["arm"] == arm]
    right = majority_right((o["case_id"], o["chosen"] == o["gold"]) for o in outcomes)
    summary = saved["summary"][arm]
    input_price, output_price = prices
    cost = sum(o["input_tokens"] * input_price + o["output_tokens"] * output_price for o in outcomes) / 1e6
    return {
        "pass1": summary["accuracy"],
        "pass3": summary["all_repeats_correct"],
        "duplicates": summary["duplicate_rate"],
        "no_route": summary["no_route_rate"],
        "input_tokens": summary["input_tokens_per_decision"],
        "majority": right,
        "cost": cost,
    }


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def table(set_name: str, rows: Dict[str, dict]) -> List[str]:
    lines = [
        "| Version | Runs right (pass^1) | Right on all 3 (pass^3) | Cases right by majority | Duplicates "
        "| Did not route | Fixed / broke vs main | Input tokens per decision | Cost of the run |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    originals = [row["original"] for row in rows.values()]
    lines.append(
        f"| Original OpenPoke (same cases in every row: lowest to highest) "
        f"| {_pct(min(o['pass1'] for o in originals))} to {_pct(max(o['pass1'] for o in originals))} "
        f"| {_pct(min(o['pass3'] for o in originals))} to {_pct(max(o['pass3'] for o in originals))} "
        f"| {min(sum(o['majority'].values()) for o in originals)} to {max(sum(o['majority'].values()) for o in originals)}"
        f" /{len(originals[0]['majority'])} | {_pct(statistics.fmean(o['duplicates'] for o in originals))} (mean) "
        f"| | | {statistics.fmean(o['input_tokens'] for o in originals):,.0f} | |"
    )
    main = rows.get("main", {}).get("solution")
    for key, row in rows.items():
        s = row["solution"]
        flips = ""
        if main and key != "main":
            fixed, broke = paired_flips(main["majority"], s["majority"])
            flips = f"{fixed} / {broke}" + (f" (p = {mcnemar_exact(fixed, broke):.2g})" if fixed + broke else "")
        lines.append(
            f"| {row['label']} | {_pct(s['pass1'])} | {_pct(s['pass3'])} | {sum(s['majority'].values())}/{len(s['majority'])} "
            f"| {_pct(s['duplicates'])} | {_pct(s['no_route'])} | {flips} | {s['input_tokens']:,.0f} "
            f"| ${row['solution']['cost'] + row['original']['cost']:.2f} |"
        )
    return lines


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="anthropic/claude-haiku-5.5")
    parser.add_argument("--set", choices=["both", "generated", "paraphrase"], default="both")
    parser.add_argument("--rows", default=",".join(key for key, _, _ in CONFIGS), help="comma-separated row keys")
    parser.add_argument("--reuse", action="store_true", help="use saved runs only, no API calls")
    parser.add_argument("--input-price", type=float, default=0.10, help="USD per million input tokens (Haiku 5.5)")
    parser.add_argument("--output-price", type=float, default=0.50, help="USD per million output tokens (Haiku 5.5)")
    args = parser.parse_args(argv)

    wanted = args.rows.split(",")
    configs = [c for c in CONFIGS if c[0] in wanted]
    sets = list(SETS) if args.set == "both" else [args.set]
    prices = (args.input_price, args.output_price)
    results: Dict[str, Dict[str, dict]] = {}
    for set_name in sets:
        print(f"{SETS[set_name]}:", flush=True)
        rows: Dict[str, dict] = {}
        for key, label, values in configs:
            hybrid = values.get("OPENPOKE_AGENT_RETRIEVER") == "hybrid"
            path = result_path(args.model, set_name, key, hybrid)
            if not path.exists() and not args.reuse:
                if run_row(args.model, set_name, key, values) != 0:
                    print(f"  {set_name}: {key} failed; rerun with the same command to continue")
                    continue
            if not path.exists():
                print(f"  {set_name}: {key}: no saved run at {path.name}")
                continue
            saved = json.loads(path.read_text(encoding="utf-8"))
            rows[key] = {"label": label, "settings": values, "file": path.name,
                         "original": arm_stats(saved, "baseline", prices), "solution": arm_stats(saved, "solution", prices)}
        results[set_name] = rows

    md = [
        "# The three approaches on a real model",
        "",
        f"Generated by `python -m evals.compare_llm`. {args.model}, 3 runs per case, 300 agents, seed 7, search "
        "phrases on, every row run the same day. \"Cases right by majority\" counts a case right when 2 of its 3 runs "
        "were; fixed / broke compares those against main with McNemar's exact p. Cost is both arms of the row's run, "
        f"at ${args.input_price:.2f} / ${args.output_price:.2f} per million input / output tokens.",
    ]
    for set_name in sets:
        if results[set_name]:
            md += ["", f"## {SETS[set_name]}", ""] + table(set_name, results[set_name])
    text = "\n".join(md) + "\n"
    print()
    print(text)
    (RESULTS_DIR / "comparison_llm.md").write_text(text, encoding="utf-8")
    serialisable = {
        set_name: {key: {**row, "original": {**row["original"], "majority": row["original"]["majority"]},
                         "solution": row["solution"]} for key, row in rows.items()}
        for set_name, rows in results.items()
    }
    (RESULTS_DIR / "comparison_llm.json").write_text(json.dumps(serialisable, indent=2), encoding="utf-8")
    print("Wrote evals/results/comparison_llm.md and .json")


if __name__ == "__main__":
    main()
