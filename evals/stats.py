"""Small-sample statistics for comparing the two arms on the same cases.

- Wilson interval: a 95% range for an accuracy measured on n cases. Better than
  p +/- 1.96 * se when n is small or the accuracy is near 0% or 100%.
- McNemar's exact test: the standard paired test for two classifiers on one test set.
  It only looks at the cases that flipped: fixed (old wrong, new right) and broke
  (old right, new wrong). If the change did nothing, flips would split 50/50.

With repeated runs, the case is the unit, not the run: repeats of one message are
not independent, so counting 120 runs as 120 samples would give a falsely tight range.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, Tuple


def wilson_interval(right: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    p = right / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def mcnemar_exact(fixed: int, broke: int) -> float:
    """Two-sided exact p-value: how likely a split this lopsided is if both arms were equally good."""
    flips = fixed + broke
    if flips == 0:
        return 1.0
    tail = sum(math.comb(flips, i) for i in range(min(fixed, broke) + 1)) / 2**flips
    return min(1.0, 2 * tail)


def paired_flips(old: Dict[str, bool], new: Dict[str, bool]) -> Tuple[int, int]:
    """(fixed, broke) over the cases both arms ran."""
    shared = old.keys() & new.keys()
    fixed = sum(1 for case in shared if new[case] and not old[case])
    broke = sum(1 for case in shared if old[case] and not new[case])
    return fixed, broke


def majority_right(results: Iterable[Tuple[str, bool]]) -> Dict[str, bool]:
    """Per case: right on more than half of its repeats."""
    runs: Dict[str, list] = {}
    for case_id, correct in results:
        runs.setdefault(case_id, []).append(correct)
    return {case_id: sum(values) * 2 > len(values) for case_id, values in runs.items()}


def describe_p(p: float) -> str:
    if p < 0.001:
        return f"p = {p:.1g}, very unlikely to be chance"
    if p < 0.05:
        return f"p = {p:.2g}, unlikely to be chance"
    return f"p = {p:.2g}, could be chance"


def paired_lines(old: Dict[str, bool], new: Dict[str, bool], *, unit: str = "cases") -> list:
    """Markdown: each arm's accuracy with a 95% interval, then the paired fixed/broke test."""
    lines = ["| | Right | 95% interval |", "|---|---|---|"]
    for label, results in (("Original OpenPoke", old), ("This change", new)):
        right, total = sum(results.values()), len(results)
        low, high = wilson_interval(right, total)
        lines.append(f"| {label} | {right}/{total} ({right / total * 100:.1f}%) | {low * 100:.1f}% to {high * 100:.1f}% |")
    fixed, broke = paired_flips(old, new)
    lines += [
        "",
        f"Same {unit}, both versions: this change fixed {fixed} and broke {broke} "
        f"(McNemar's exact test, {describe_p(mcnemar_exact(fixed, broke))}).",
    ]
    return lines


__all__ = ["describe_p", "majority_right", "mcnemar_exact", "paired_flips", "paired_lines", "wilson_interval"]
