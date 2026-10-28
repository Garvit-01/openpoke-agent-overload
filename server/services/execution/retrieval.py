"""Rank execution agents for a message.

Rule: exact entity matches (same email address, same Gmail thread id) are hard
signals and always rank first. Keyword similarity (BM25 over name, summary and
entities) only orders agents within those groups. Similarity can say "what is
this about", but only an exact entity can say "which thread is this".

Past requests (the last 20 sent to each agent) are a second, weaker field: each
word counts once, words the name/summary/entities already have are left out, and
the field has its own BM25 score added at REQUEST_WEIGHT. The resolver never asks
for them, so they help find an agent but never decide a merge.
"""

from __future__ import annotations

import difflib
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence

from .entities import HARD_KINDS, STOPWORDS, extract_entities, fold_plural, tokenize
from .registry import (
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_DORMANT,
    AgentRecord,
    AgentRegistry,
)

HARD_MATCH_BONUS = 100.0
# An email shared by many agents (usually the user's own address) says nothing about which thread
MAX_AGENTS_PER_HARD_ENTITY = 5
# Status and recency only nudge the ranking: small multipliers, so they mean the same at any
# corpus size and an archived agent that clearly matches better still beats a weaker active one.
# (An earlier 0.6 multiplier for archived agents was strong enough to hide the right agent.)
STATUS_WEIGHT = {STATUS_ACTIVE: 1.0, STATUS_DORMANT: 0.95, STATUS_ARCHIVED: 0.9}
RECENCY_BOOST = 0.05  # at most +5% for an agent used right now, halving every two weeks
RECENCY_HALF_LIFE_DAYS = 14.0
# Words this common (e.g. "latest" in every summary) are not shown as reasons for a match
MIN_REASON_IDF = 0.5
# Past requests count for half: one old instruction is weaker evidence than the agent's name or summary
REQUEST_WEIGHT = 0.5
# Words of the best-matching past request shown as the reason, so the model can see why the agent is listed
REQUEST_SNIPPET_WORDS = 14

# Did-you-mean for contact names ("Sarha" -> "Sarah"). difflib ratio: one swapped or wrong
# letter in a 5-letter name is exactly 0.8. Only words in a name position are checked.
NAME_SUGGEST_CUTOFF = 0.8
NAME_SUGGEST_MIN_LENGTH = 4
NAME_SUGGEST_MAX_PEOPLE = 8
# A lowercase word right after one of these is in a name position ("send it to sarha")
NAME_CUES = frozenset("to with from for cc ask tell email ping text call message".split())
# Capitalised mid-sentence but not names
_CAPITALISED_WORDS = frozenset(
    """
    monday tuesday wednesday thursday friday saturday sunday
    january february march april june july august september october november december
    """.split()
)
_WORD = re.compile(r"[A-Za-z]+")


@dataclass
class NameSuggestion:
    """A word in the message that looks like a misspelt contact name."""

    typed: str  # as written, e.g. "Sarha"
    name: str  # the closest known first name, e.g. "Sarah"
    people: List[str] = field(default_factory=list)  # full names with that first name, most recent first

    def hint(self) -> str:
        shown = self.people[:NAME_SUGGEST_MAX_PEOPLE]
        listed = ", ".join(shown)
        if len(self.people) > len(shown):
            listed += f" and {len(self.people) - len(shown)} more"
        closest = f"{self.name} ({listed})" if listed else self.name
        ask = "which one they mean" if len(self.people) > 1 else "if that is who they mean"
        return f'"{self.typed}" isn\'t a known contact. Closest: {closest}. Ask the user {ask} before acting.'


def with_suggested_names(query: str, suggestions: Sequence[NameSuggestion]) -> str:
    """The query with suggested names added, so retrieval and the named-people list find them."""

    names = list(dict.fromkeys(suggestion.name for suggestion in suggestions))
    return f"{query} {' '.join(names)}" if names else query


@dataclass
class PromptCandidates:
    """Everything the interaction agent's per-turn prompt shows about existing agents."""

    shortlist: List[ScoredAgent]
    related: List[AgentRecord]
    suggestions: List[NameSuggestion]


@dataclass
class ScoredAgent:
    """An agent plus why it was picked, so routing decisions can be explained and logged."""

    record: AgentRecord
    score: float
    reasons: List[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.record.name


def agent_document_tokens(record: AgentRecord) -> List[str]:
    """Tokens that describe an agent. The name counts twice since it was chosen on purpose."""

    parts = [record.name, record.name, record.summary]
    for kind, values in record.entities.items():
        if kind == "thread":
            continue  # hex ids only matter as exact matches
        parts.extend(values)
    return tokenize(" ".join(parts))


def request_tokens(record: AgentRecord, identity: Optional[Iterable[str]] = None) -> List[str]:
    """Distinct words of the agent's past requests that its name, summary and entities don't already have.

    Each word once, so an agent asked about "invoice" 20 times is not 20 times more about invoices.
    """

    known = set(agent_document_tokens(record) if identity is None else identity)
    words = (token for text in record.requests for token in tokenize(text) if token not in known)
    return list(dict.fromkeys(words))


def request_snippet(requests: Sequence[str], terms: Iterable[str], words: int = REQUEST_SNIPPET_WORDS) -> str:
    """The past request that shares most of `terms` (newest wins ties), cut to a few words around the match."""

    # Request words are compared plural-folded, so the terms are folded the same way
    wanted = set(tokenize(" ".join(terms)))
    best, best_hits = "", 0
    for text in reversed(requests):
        hits = len(wanted & set(tokenize(text)))
        if hits > best_hits:
            best, best_hits = text, hits
    parts = best.split()
    first = next((index for index, part in enumerate(parts) if wanted & set(tokenize(part))), 0)
    start = max(0, first - 4)
    piece = parts[start : start + words]
    return ("..." if start else "") + " ".join(piece) + ("..." if start + len(piece) < len(parts) else "")


def _index_requests_by_default() -> bool:
    from ...config import get_settings

    return bool(get_settings().agent_index_requests)


class BM25Index:
    """Plain BM25 over a small in-memory corpus (hundreds to low thousands of agents)."""

    def __init__(self, documents: Dict[str, List[str]], *, k1: float = 1.2, b: float = 0.75):
        self._k1 = k1
        self._b = b
        self._term_counts = {key: Counter(tokens) for key, tokens in documents.items()}
        self._lengths = {key: len(tokens) for key, tokens in documents.items()}
        self._avg_length = (sum(self._lengths.values()) / len(documents)) if documents else 0.0
        document_frequency: Counter[str] = Counter()
        for counts in self._term_counts.values():
            document_frequency.update(counts.keys())
        total = len(documents)
        self._idf = {
            term: math.log(1 + (total - freq + 0.5) / (freq + 0.5))
            for term, freq in document_frequency.items()
        }

    def idf(self, term: str) -> float:
        return self._idf.get(term, 0.0)

    def score(self, query_tokens: Iterable[str]) -> Dict[str, float]:
        unique_terms = [term for term in dict.fromkeys(query_tokens) if term in self._idf]
        scores: Dict[str, float] = {}
        if not unique_terms or not self._avg_length:
            return scores
        for key, counts in self._term_counts.items():
            total = 0.0
            length_norm = self._k1 * (1 - self._b + self._b * self._lengths[key] / self._avg_length)
            for term in unique_terms:
                frequency = counts.get(term, 0)
                if frequency:
                    total += self._idf[term] * frequency * (self._k1 + 1) / (frequency + length_norm)
            if total > 0:
                scores[key] = total
        return scores


class AgentRetriever:
    """Search and shortlist agents from the registry for a given message."""

    def __init__(self, registry: AgentRegistry, *, index_requests: Optional[bool] = None):
        self._registry = registry
        # None: follow the OPENPOKE_AGENT_INDEX_REQUESTS setting
        self._index_requests = index_requests

    @property
    def index_requests(self) -> bool:
        return _index_requests_by_default() if self._index_requests is None else self._index_requests

    # Rank all agents for a query: hard entity matches first, then BM25 with a small recency boost.
    # include_requests=False searches name, summary and entities only (the resolver always asks for that).
    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        statuses: Optional[Sequence[str]] = None,
        now: Optional[datetime] = None,
        include_requests: Optional[bool] = None,
    ) -> List[ScoredAgent]:
        use_requests = self.index_requests if include_requests is None else include_requests
        records = self._registry.list_agents(statuses, with_requests=use_requests)
        if not records or limit <= 0:
            return []

        now = now or self._registry.now()
        query_tokens = tokenize(query)
        hard_matches = self._hard_matches(query, records)
        documents = {record.name: agent_document_tokens(record) for record in records}
        index = BM25Index(documents)
        keyword_scores = index.score(query_tokens)
        query_terms = {term for term in query_tokens if index.idf(term) >= MIN_REASON_IDF}

        # Past requests: a separate index over agents that have new words in them, so its length
        # normalisation and word rarity are about request text only and can't dilute the main index
        request_documents: Dict[str, List[str]] = {}
        request_scores: Dict[str, float] = {}
        if use_requests:
            for record in records:
                tokens = request_tokens(record, documents[record.name])
                if tokens:
                    request_documents[record.name] = tokens
            if request_documents:
                request_scores = BM25Index(request_documents).score(query_tokens)

        results: List[ScoredAgent] = []
        for record in records:
            identity_score = keyword_scores.get(record.name, 0.0)
            request_score = request_scores.get(record.name, 0.0)
            keyword = identity_score + REQUEST_WEIGHT * request_score
            hard_reasons = hard_matches.get(record.name, [])
            if keyword <= 0 and not hard_reasons:
                continue

            reasons = list(hard_reasons)
            score = keyword * STATUS_WEIGHT.get(record.status, 1.0)
            score *= 1 + RECENCY_BOOST * self._recency(record, now)
            shared = sorted(query_terms & set(documents[record.name]))[:4]
            if identity_score > 0 and shared:
                reasons.append("keywords: " + ", ".join(shared))
            if request_score > 0:
                matched = set(query_tokens) & set(request_documents[record.name])
                reasons.append("past request: " + request_snippet(record.requests, matched))
            if hard_reasons:
                score += HARD_MATCH_BONUS
            results.append(ScoredAgent(record=record, score=score, reasons=reasons))

        results.sort(key=lambda item: (item.score, item.record.last_active_at), reverse=True)
        return results[:limit]

    # The most recently active agents, used to fill the prompt so "that email" still resolves
    def recent(self, *, limit: int = 4) -> List[ScoredAgent]:
        if limit <= 0:
            return []
        records = self._registry.list_agents([STATUS_ACTIVE])
        records.sort(key=lambda record: record.last_active_at, reverse=True)
        return [ScoredAgent(record=record, score=0.0, reasons=["recent"]) for record in records[:limit]]

    # What goes into the interaction agent's prompt: top matches for this message plus recent agents
    def shortlist(
        self,
        query: str,
        *,
        retrieved: int = 8,
        recent: int = 4,
        now: Optional[datetime] = None,
    ) -> List[ScoredAgent]:
        shortlist = self.search(query, limit=retrieved, now=now)
        chosen = {item.name for item in shortlist}
        added = 0
        # Fetch extra so `recent` new agents are added even if some are already shortlisted
        for item in self.recent(limit=recent + len(shortlist)):
            if added >= recent:
                break
            if item.name not in chosen:
                shortlist.append(item)
                chosen.add(item.name)
                added += 1
        return shortlist

    # Other agents of a person the message names, listed by name and topic only. The original prompt
    # showed every name, which let a model link "the new hire's first week" to "Onboarding Plan with
    # Vera" by meaning. Keywords can't, so this keeps that for the people in the message.
    def named_people_agents(
        self,
        query: str,
        *,
        exclude: Iterable[str] = (),
        limit: int = 12,
    ) -> List[AgentRecord]:
        # Names as written, not plural-folded: "Lucas" is not "Luca"
        query_tokens = set(tokenize(query, fold=False))
        if not query_tokens or limit <= 0:
            return []
        skip = set(exclude)
        matched = [
            record
            for record in self._registry.list_agents()
            if record.name not in skip
            and query_tokens & set(tokenize(" ".join(record.entities.get("person", [])), fold=False))
        ]
        matched.sort(key=lambda record: record.last_active_at, reverse=True)
        return matched[:limit]

    # The per-turn prompt: shortlist, the named people's other agents, and did-you-mean names
    def prompt_candidates(
        self,
        message: str,
        *,
        retrieved: int = 8,
        recent: int = 4,
        named_people: int = 12,
        suggest: bool = True,
        now: Optional[datetime] = None,
    ) -> PromptCandidates:
        suggestions = self.suggest_names(message) if suggest else []
        query = with_suggested_names(message, suggestions)
        shortlist = self.shortlist(query, retrieved=retrieved, recent=recent, now=now)
        related = self.named_people_agents(query, exclude=[item.name for item in shortlist], limit=named_people)
        return PromptCandidates(shortlist=shortlist, related=related, suggestions=suggestions)

    # "Sarha" when no contact is called Sarha but several are called Sarah
    def suggest_names(self, message: str) -> List[NameSuggestion]:
        """Words that are probably a misspelt first name of a known contact.

        A word counts only if it is in a name position (capitalised mid-sentence, or right after
        "to", "with", "ask" and the like), has 4+ letters, isn't a stopword, isn't a word any
        agent already uses, and starts with the same letter as the name. So an exact name, a
        normal word ("trace" vs Grace) or a sentence's first word doesn't trigger it.
        """

        candidates = [
            match for match in _WORD.finditer(message or "") if _in_name_position(message, match)
        ]
        candidates = [
            match for match in candidates
            if len(match.group()) >= NAME_SUGGEST_MIN_LENGTH
            and match.group().lower() not in STOPWORDS
            and match.group().lower() not in _CAPITALISED_WORDS
        ]
        if not candidates:
            return []

        name_words: set[str] = set()
        first_names: Dict[str, str] = {}  # lowercase -> as written
        people: Dict[str, List[str]] = {}  # lowercase first name -> full names, most recent first
        for person in self._registry.entity_values("person"):
            words = person.split()
            name_words.update(word.lower() for word in words)
            if not words or not words[0].isalpha():
                continue
            first = words[0].lower()
            first_names.setdefault(first, words[0])
            if len(words) > 1:
                people.setdefault(first, []).append(person)

        vocabulary: Optional[set[str]] = None  # built only when a word is close to a name
        suggestions: List[NameSuggestion] = []
        for match in candidates:
            typed = match.group()
            word = typed.lower()
            if word in name_words:
                continue  # a known name
            pool = [name for name in first_names if name[0] == word[0]]
            close = difflib.get_close_matches(word, pool, n=2, cutoff=NAME_SUGGEST_CUTOFF)
            if not close:
                continue
            if vocabulary is None:
                vocabulary = {
                    token for record in self._registry.list_agents() for token in agent_document_tokens(record)
                }
            if word in vocabulary or fold_plural(word) in vocabulary:
                continue  # a word the agents use, so probably not a misspelt name
            following = _next_word(message, match)
            for name in close:
                full = people.get(name, [])
                # "Sarha Okafor": the surname says which Sarah
                narrowed = [person for person in full if following and following in person.lower().split()[1:]]
                suggestions.append(NameSuggestion(typed=typed, name=first_names[name], people=narrowed or full))
        return suggestions

    @staticmethod
    def _hard_matches(query: str, records: Sequence[AgentRecord]) -> Dict[str, List[str]]:
        query_entities = extract_entities(query)
        matches: Dict[str, List[str]] = {}
        for kind in HARD_KINDS:
            for value in query_entities.get(kind, []):
                owners = [record.name for record in records if value in record.entities.get(kind, [])]
                if not owners or len(owners) > MAX_AGENTS_PER_HARD_ENTITY:
                    continue
                for owner in owners:
                    matches.setdefault(owner, []).append(f"{kind}: {value}")
        return matches

    @staticmethod
    def _recency(record: AgentRecord, now: datetime) -> float:
        age_days = max((now - record.last_active).total_seconds() / 86400.0, 0.0)
        return 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)


def _in_name_position(text: str, match: re.Match) -> bool:
    """Capitalised and not the first word of a sentence, or right after a word like "to"."""

    before = text[: match.start()].rstrip()
    previous = _WORD.findall(before[-20:])
    if previous and previous[-1].lower() in NAME_CUES:
        return True
    starts_sentence = not before or before[-1] in ".!?:;\n\"'(" or not previous
    return match.group()[0].isupper() and not starts_sentence


def _next_word(text: str, match: re.Match) -> str:
    following = _WORD.match(text[match.end():].lstrip())
    return following.group().lower() if following else ""


__all__ = [
    "AgentRetriever",
    "BM25Index",
    "HARD_MATCH_BONUS",
    "MAX_AGENTS_PER_HARD_ENTITY",
    "NameSuggestion",
    "PromptCandidates",
    "REQUEST_WEIGHT",
    "STATUS_WEIGHT",
    "ScoredAgent",
    "agent_document_tokens",
    "request_snippet",
    "request_tokens",
    "with_suggested_names",
]
