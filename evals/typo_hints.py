"""A labelled check of the "did you mean ...?" hints, and write evals/results/typo_hints.md and .json.

    python -m evals.typo_hints

The routing evals have no "ask the user" outcome, so they can't score the hints. This checks the
hint itself, on the 300-agent history:
- recall: for a first name of a known contact, misspelt once (letters swapped, dropped, doubled or
  replaced by a keyboard neighbour), does the hint name that first name and list every contact with it?
  With the surname after it, does it narrow to that one person?
- false alarms: does a hint fire on any of the eval's real messages, or on the same templates
  with the name spelt right? Every one of those is a needless "did you mean" in front of the model.
Typos in the first letter are counted separately: the hint only compares names with the same first letter.
"""

from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from server.services.execution.retrieval import NAME_SUGGEST_MIN_LENGTH, AgentRetriever

from .harness import build_registry
from .paraphrase_set import build_paraphrase_scenario
from .scenarios import build_scenario

RESULTS_DIR = Path(__file__).parent / "results"
SEED = 11

# Name positions the hint looks at: after "to"/"ask"/"email" and the like, or capitalised mid-sentence
TEMPLATES = [
    "Send the updated list to {name}.",
    "Can you ask {name} whether the invoice went out?",
    "Did {name} reply about the offsite agenda?",
    "Email {name} about the contract renewal.",
]
SURNAME_TEMPLATE = "Send the revised quote to {name} {surname}."

NEIGHBOURS = {
    "a": "sq", "b": "vn", "c": "xv", "d": "sf", "e": "wr", "f": "dg", "g": "fh", "h": "gj", "i": "uo",
    "j": "hk", "k": "jl", "l": "k", "m": "n", "n": "bm", "o": "ip", "p": "o", "q": "w", "r": "et",
    "s": "ad", "t": "ry", "u": "yi", "v": "cb", "w": "qe", "x": "zc", "y": "tu", "z": "x",
}
EDITS = ["swap", "drop", "double", "neighbour"]
# Common first names that no contact in the history has: a hint on one of these is a needless
# "did you mean" for someone new (the cost of catching short typos like "Noha")
UNKNOWN_NAMES = (
    "Mike Dave John Paul Kate Anna Mark Tony Lisa Jake Sara Nora Ella Luke Ivan Hugo Rory Amir Ezra Kyra "
    "Dale Gina Owen Iris Joel Rhea Seth Tara Vito Yuri Zoey Rose June Hope Bill Will"
).split()


def misspell(name: str, edit: str, rng: random.Random, *, first_letter: bool = False) -> Optional[str]:
    """One edit to a name, never at the first letter unless first_letter."""

    word = name.lower()
    if first_letter:
        options = NEIGHBOURS.get(word[0], "")
        return (rng.choice(options) + word[1:]).capitalize() if options else None
    if edit == "swap":
        spots = [i for i in range(1, len(word) - 1) if word[i] != word[i + 1]]
        if not spots:
            return None
        i = rng.choice(spots)
        word = word[:i] + word[i + 1] + word[i] + word[i + 2:]
    elif edit == "drop":
        i = rng.randrange(1, len(word))
        word = word[:i] + word[i + 1:]
    elif edit == "double":
        i = rng.randrange(1, len(word))
        word = word[:i] + word[i] + word[i:]
    else:
        spots = [i for i in range(1, len(word)) if word[i] in NEIGHBOURS]
        i = rng.choice(spots)
        word = word[:i] + rng.choice(NEIGHBOURS[word[i]]) + word[i + 1:]
    return word.capitalize()


def contacts(retriever: AgentRetriever) -> Dict[str, List[str]]:
    """First name (as written) -> full names with it."""
    people: Dict[str, List[str]] = {}
    for person in retriever._registry.entity_values("person"):
        words = person.split()
        if len(words) > 1:
            people.setdefault(words[0].capitalize(), []).append(person)
    return people


def check(retriever: AgentRetriever, message: str, intended: str, expected_people: List[str]) -> Tuple[bool, bool, int]:
    """(named the intended first name, listed exactly the expected people, other names suggested)."""
    suggestions = retriever.suggest_names(message)
    hits = [s for s in suggestions if s.name.lower() == intended.lower()]
    listed = bool(hits) and {p.lower() for p in hits[0].people} == {p.lower() for p in expected_people}
    return bool(hits), listed, len(suggestions) - len(hits)


def run() -> Dict[str, object]:
    rng = random.Random(SEED)
    scenario = build_scenario(300, seed=7)
    with tempfile.TemporaryDirectory() as workdir:
        registry = build_registry(scenario, Path(workdir) / "registry.db")
        retriever = AgentRetriever(registry)
        people = contacts(retriever)
        known = {word.lower() for full in people.values() for p in full for word in p.split()}
        names = sorted(name for name in people if len(name) >= NAME_SUGGEST_MIN_LENGTH)

        by_edit: Dict[str, Dict[str, int]] = {}
        misses: List[str] = []
        narrowed = {"cases": 0, "right": 0}
        for name in names:
            for edit in EDITS + ["first_letter"]:
                typo = misspell(name, edit, rng, first_letter=edit == "first_letter")
                if not typo or typo.lower() in known or len(typo) < NAME_SUGGEST_MIN_LENGTH:
                    continue
                row = by_edit.setdefault(edit, {"cases": 0, "named": 0, "listed": 0, "other_names": 0})
                template = rng.choice(TEMPLATES)
                named, listed, others = check(retriever, template.format(name=typo), name, people[name])
                row["cases"] += 1
                row["named"] += named
                row["listed"] += listed
                row["other_names"] += others
                if not named and edit != "first_letter":
                    misses.append(f"{typo} (for {name})")
                if edit == "swap" and len(people[name]) > 1:
                    target = people[name][0]
                    message = SURNAME_TEMPLATE.format(name=typo, surname=target.split()[-1])
                    narrowed["cases"] += 1
                    narrowed["right"] += check(retriever, message, name, [target])[1]

        # False alarms: real eval messages, and the templates with names spelt right
        real = [case.message for case in scenario.cases]
        real += [turn.message for conversation in scenario.conversations for turn in conversation.turns]
        real += [case.message for case in build_paraphrase_scenario(300, seed=7).cases]
        spelt_right = [template.format(name=name) for name in names for template in TEMPLATES]
        unknown = [name for name in UNKNOWN_NAMES if name.lower() not in known]
        unknown_hints = {name: retriever.suggest_names(TEMPLATES[0].format(name=name)) for name in unknown}
        alarms = {
            "real_messages": len(real),
            "real_with_hint": [m for m in real if retriever.suggest_names(m)],
            "spelt_right": len(spelt_right),
            "spelt_right_with_hint": [m for m in spelt_right if retriever.suggest_names(m)],
            "unknown_names": len(unknown),
            "unknown_with_hint": {name: [s.name for s in hints] for name, hints in unknown_hints.items() if hints},
        }
    return {"first_names": len(names), "by_edit": by_edit, "narrowed": narrowed, "misses": misses, "false_alarms": alarms}


def _pct(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole * 100:.0f}%)" if whole else "n/a"


def report(result: Dict[str, object]) -> str:
    edits = result["by_edit"]
    inner = {k: sum(edits[e][k] for e in EDITS if e in edits) for k in ("cases", "named", "listed", "other_names")}
    alarms = result["false_alarms"]
    lines = [
        "# Typo hints: labelled check",
        "",
        f"Generated by `python -m evals.typo_hints`. {result['first_names']} first names of contacts in the "
        f"300-agent history (seed 7), each misspelt once per edit type (typo seed {SEED}).",
        "",
        "| Typo | Cases | Hint names the right first name | Lists exactly the people with that name | Other names also suggested |",
        "|---|---|---|---|---|",
    ]
    for edit in EDITS + ["first_letter"]:
        if edit in edits:
            row = edits[edit]
            label = "first letter (not handled)" if edit == "first_letter" else edit
            lines.append(f"| {label} | {row['cases']} | {_pct(row['named'], row['cases'])} | "
                         f"{_pct(row['listed'], row['cases'])} | {row['other_names']} |")
    lines.append(f"| **all, first letter kept** | {inner['cases']} | {_pct(inner['named'], inner['cases'])} | "
                 f"{_pct(inner['listed'], inner['cases'])} | {inner['other_names']} |")
    lines += [
        "",
        f"With the surname after the typo (\"{SURNAME_TEMPLATE.format(name='Sarha', surname='Okafor')}\"), the hint "
        f"narrows to that one person in {_pct(result['narrowed']['right'], result['narrowed']['cases'])} of cases.",
        "",
        "## False alarms",
        "",
        "| Messages | Count | With a hint |",
        "|---|---|---|",
        f"| Every eval message (generated cases, conversation turns, hand-written paraphrases) | {alarms['real_messages']} "
        f"| {len(alarms['real_with_hint'])} |",
        f"| The templates above with each name spelt right | {alarms['spelt_right']} | {len(alarms['spelt_right_with_hint'])} |",
        f"| A common first name no contact has (\"Send the updated list to Nora.\") | {alarms['unknown_names']} "
        f"| {len(alarms['unknown_with_hint'])} |",
    ]
    for message in (alarms["real_with_hint"] + alarms["spelt_right_with_hint"])[:10]:
        lines.append(f"- {message}")
    if alarms["unknown_with_hint"]:
        pairs = ", ".join(f"{name} -> {' / '.join(hints)}" for name, hints in alarms["unknown_with_hint"].items())
        lines += ["", f"Names no contact has that got a hint: {pairs}. The model then asks \"did you mean ...?\" "
                  "once; giving the person's email address with the name turns the hint off."]
    if result["misses"]:
        lines += ["", "## Missed typos (first letter kept)", "", ", ".join(result["misses"])]
    return "\n".join(lines) + "\n"


def main() -> None:
    result = run()
    text = report(result)
    print(text)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "typo_hints.md").write_text(text, encoding="utf-8")
    (RESULTS_DIR / "typo_hints.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("Wrote evals/results/typo_hints.md and .json")


if __name__ == "__main__":
    main()
