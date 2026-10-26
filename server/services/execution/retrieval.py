"""Rank execution agents for a message.

Rule: exact entity matches (same email address, same Gmail thread id) are hard
signals and always rank first. Keyword similarity (BM25 over name, summary and
entities) only orders agents within those groups. Similarity can say "what is
this about", but only an exact entity can say "which thread is this".
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence

from .entities import HARD_KINDS, extract_entities, tokenize
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
# Dormant and archived agents stay searchable but rank a little lower than active ones
STATUS_WEIGHT = {STATUS_ACTIVE: 1.0, STATUS_DORMANT: 0.85, STATUS_ARCHIVED: 0.6}
# Recency is only a tie-breaker: at most +0.5 for an agent used right now
RECENCY_WEIGHT = 0.5
RECENCY_HALF_LIFE_DAYS = 14.0


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

    def __init__(self, registry: AgentRegistry):
        self._registry = registry

    # Rank all agents for a query: hard entity matches first, then BM25 with a small recency boost
    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        statuses: Optional[Sequence[str]] = None,
        now: Optional[datetime] = None,
    ) -> List[ScoredAgent]:
        records = self._registry.list_agents(statuses)
        if not records or limit <= 0:
            return []

        now = now or self._registry.now()
        query_tokens = tokenize(query)
        hard_matches = self._hard_matches(query, records)
        documents = {record.name: agent_document_tokens(record) for record in records}
        keyword_scores = BM25Index(documents).score(query_tokens)
        query_terms = set(query_tokens)

        results: List[ScoredAgent] = []
        for record in records:
            keyword = keyword_scores.get(record.name, 0.0)
            hard_reasons = hard_matches.get(record.name, [])
            if keyword <= 0 and not hard_reasons:
                continue

            reasons = list(hard_reasons)
            score = keyword * STATUS_WEIGHT.get(record.status, 1.0)
            if keyword > 0:
                shared = sorted(query_terms & set(documents[record.name]))[:4]
                reasons.append("keywords: " + ", ".join(shared))
            if hard_reasons:
                score += HARD_MATCH_BONUS
            score += RECENCY_WEIGHT * self._recency(record, now)
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


__all__ = [
    "AgentRetriever",
    "BM25Index",
    "HARD_MATCH_BONUS",
    "MAX_AGENTS_PER_HARD_ENTITY",
    "ScoredAgent",
    "agent_document_tokens",
]
