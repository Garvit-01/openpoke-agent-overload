"""Deterministic entity extraction and tokenisation used to index execution agents."""

from __future__ import annotations

import re
from typing import Dict, Iterable, List

# Email addresses and Gmail thread ids are "hard" entities: an exact match means
# "this is the same conversation". Names and subjects are "soft": they help rank.
HARD_KINDS = ("email", "thread")
SOFT_KINDS = ("person", "subject")

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Require a "thread id" label next to the hex id so random hex strings are ignored
THREAD_PATTERN = re.compile(r"thread[_\s-]?id[\"'\s:=]+([0-9a-fA-F]{12,24})", re.IGNORECASE)
SUBJECT_PATTERN = re.compile(
    r"[\"']?subject[\"']?\s*[:=]\s*[\"']([^\"'\n]{3,120})[\"']",
    re.IGNORECASE,
)
# "Email to Sarah Chen", "lunch with Priya", "invoice from Acme": a capitalised name after a preposition.
# Only spaces/tabs between words, so a name never runs across a line break.
PERSON_PATTERN = re.compile(
    r"\b(?:to|with|from|for|cc|and|re|ask|tell|email|ping)[ \t]+"
    r"([A-Z][a-z'\-]+(?:[ \t]+[A-Z][a-z'\-]+){0,2})"
)

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """
    a an the and or but if then so to of in on at by for with from about into over
    is are was were be been being am do does did have has had i me my we our you your
    he she they them their it its this that these those there here what which who whom
    when where why how can could would should will shall may might must not no yes
    please pls thanks thank hi hey ok okay just also any some all get got let lets
    com org net www http https re fw fwd
    """.split()
)

# Words that describe the *kind* of agent rather than *who/what* it is about.
# "Email to Sarah Chen" and "Sarah Chen thread" both reduce to "chen sarah".
NAME_FILLER = frozenset(
    """
    email emails mail mails message messages thread threads reply replies replying
    follow up followup followups conversation chat agent task note draft drafts
    send sending sent respond response reminder reminders remind
    """.split()
)


# Words that end in "s" but are not plurals
_NOT_PLURAL = frozenset("news series species".split())


def fold_plural(token: str) -> str:
    """Map a plural to its singular, so "groceries" matches "grocery" and "lists" matches "list".

    A few suffix rules, no stemmer: "ies" -> "y" (groceries), "es" goes after ss/x/ch/sh/zz
    (addresses, boxes, matches, wishes), otherwise a final "s" goes (lists, cases, houses).
    Words of 3 letters or fewer, words ending in ss/us/is (address, status, analysis, bus) and
    anything with a digit stay as they are. The result is a real word in most cases, which
    matters because matched keywords are shown to the model.
    """

    if len(token) <= 3 or not token.isalpha() or token in _NOT_PLURAL:
        return token
    if not token.endswith("s") or token.endswith(("ss", "us", "is")):
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith(("sses", "xes", "ches", "shes", "zzes")):
        return token[:-2]
    return token[:-1]


def tokenize(text: str, *, fold: bool = True) -> List[str]:
    """Lowercase word tokens without stopwords or single characters, plurals folded.

    fold=False keeps words as written; person names use it so "Lucas" doesn't become "Luca".
    """

    tokens = [
        token
        for token in _TOKEN_PATTERN.findall((text or "").lower())
        if len(token) > 1 and token not in STOPWORDS
    ]
    return [fold_plural(token) for token in tokens] if fold else tokens


def normalize_agent_name(name: str) -> str:
    """Order-independent key for an agent name, used to catch near-duplicate names.

    Plurals are folded too, so "Grocery lists" and "grocery list" get the same key. The resolver
    reuses on a same key only when exactly one agent has it and no new email address is named.
    """

    tokens = {token for token in tokenize(name) if token not in NAME_FILLER}
    return " ".join(sorted(tokens))


def _dedupe(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def extract_entities(text: str) -> Dict[str, List[str]]:
    """Pull emails, thread ids, subjects and person names out of free text."""

    text = text or ""
    entities: Dict[str, List[str]] = {
        "email": _dedupe(match.lower() for match in EMAIL_PATTERN.findall(text)),
        "thread": _dedupe(match.lower() for match in THREAD_PATTERN.findall(text)),
        "subject": _dedupe(match.strip() for match in SUBJECT_PATTERN.findall(text)),
        "person": _dedupe(match.strip() for match in PERSON_PATTERN.findall(text)),
    }
    return {kind: values for kind, values in entities.items() if values}


def merge_entities(*sources: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Union several entity maps, keeping first-seen order."""

    merged: Dict[str, List[str]] = {}
    for source in sources:
        for kind, values in (source or {}).items():
            merged[kind] = _dedupe([*merged.get(kind, []), *values])
    return merged


__all__ = [
    "HARD_KINDS",
    "SOFT_KINDS",
    "extract_entities",
    "fold_plural",
    "merge_entities",
    "normalize_agent_name",
    "tokenize",
]
