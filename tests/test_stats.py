"""The small-sample statistics used in the eval reports."""

import pytest

from evals.stats import majority_right, mcnemar_exact, paired_flips, paired_lines, wilson_interval


def test_wilson_interval_matches_known_values():
    low, high = wilson_interval(39, 40)
    assert (round(low, 3), round(high, 3)) == (0.871, 0.996)
    low, high = wilson_interval(24, 40)
    assert (round(low, 3), round(high, 3)) == (0.446, 0.737)
    # Unlike p +/- 1.96 se, it never claims a range of exactly 100% from a perfect score
    assert wilson_interval(40, 40)[0] < 1.0
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_mcnemar_exact():
    assert mcnemar_exact(15, 0) == pytest.approx(2 / 2**15)
    assert mcnemar_exact(6, 2) == pytest.approx(0.289, abs=0.001)
    assert mcnemar_exact(5, 5) == 1.0
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(2, 6) == mcnemar_exact(6, 2), "two-sided"


def test_case_is_the_unit_not_the_run():
    runs = [("a", True), ("a", True), ("a", False), ("b", True), ("b", False), ("b", False)]
    assert majority_right(runs) == {"a": True, "b": False}


def test_paired_flips_and_report():
    old = {"a": False, "b": True, "c": True, "d": False}
    new = {"a": True, "b": True, "c": False, "d": True}
    assert paired_flips(old, new) == (2, 1)
    text = "\n".join(paired_lines(old, new))
    assert "| Original OpenPoke | 2/4 (50.0%) |" in text
    assert "fixed 2 and broke 1" in text
    assert "could be chance" in text
