"""Rank execution agents for a message.

Rule: exact entity matches (same email address, same Gmail thread id) are hard
signals and always rank first. Keyword similarity (BM25 over name, summary and
entities) only orders agents within those groups. Similarity can say "what is
this about", but only an exact entity can say "which thread is this".

Hybrid mode (OPENPOKE_AGENT_RETRIEVER=hybrid, needs the optional fastembed package) adds
search by meaning without disturbing that order: the keyword ranking keeps its top
results, and the last DENSE_SLOTS places go to the agents most similar in meaning that
keyword search did not already pick. Hard matches are never pushed out. Meaning only
decides what the model gets to see; it never decides a merge (the resolver calls
keyword_search).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Sequence

from .entities import HARD_KINDS, extract_entities, tokenize
from .registry import (
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_DORMANT,
    AgentRecord,
    AgentRegistry,
)

if TYPE_CHECKING:  # embeddings needs numpy; import it only when hybrid search is used
    from .embeddings import Embedder

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

RETRIEVER_BM25 = "bm25"
RETRIEVER_HYBRID = "hybrid"
RETRIEVERS = (RETRIEVER_BM25, RETRIEVER_HYBRID)
# Hybrid: places in each search result reserved for meaning matches keyword search missed.
# 3 of 8 keeps keyword ranks 1-5, where the right agent sits when it shares words with the message
# (see docs/APPROACH_EMBEDDINGS.md), and gives meaning enough room to surface a paraphrased one.
DENSE_SLOTS = 3
# A floor for clear noise only; the place then goes back to keyword results. With bge-small, real
# paraphrase matches scored 0.52-0.76 but so did "ok send it" (0.65), so no floor separates them.
# The fixed slot count is what bounds the cost of a bad meaning match: at most 3 extra agents shown.
DENSE_MIN_SIMILARITY = 0.5


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

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        mode: Optional[str] = None,
        embedder: Optional["Embedder"] = None,
        dense_slots: int = DENSE_SLOTS,
        dense_min_similarity: float = DENSE_MIN_SIMILARITY,
    ):
        """mode: "bm25" or "hybrid"; None reads OPENPOKE_AGENT_RETRIEVER. embedder: override the shared one."""
        self._registry = registry
        self._mode = mode
        self._embedder = embedder
        self.dense_slots = dense_slots
        self.dense_min_similarity = dense_min_similarity

    @property
    def mode(self) -> str:
        if self._mode is not None:
            return self._mode
        from ...config import get_settings

        return get_settings().agent_retriever

    def _dense_embedder(self) -> Optional["Embedder"]:
        if self.mode != RETRIEVER_HYBRID:
            return None
        if self._embedder is not None:
            return self._embedder
        from .embeddings import get_embedder

        return get_embedder()

    # Rank agents for a query. bm25: hard entity matches, then keywords. hybrid: the same, with the
    # last places given to the best meaning matches keyword search missed (see the module docstring).
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
        keyword = self._keyword_ranking(query, records, now)
        embedder = self._dense_embedder()
        if embedder is None or self.dense_slots <= 0:
            return keyword[:limit]
        try:
            from .embeddings import store_for

            similarity = store_for(self._registry.db_path).similarities(query, records, embedder)
        except Exception as exc:  # meaning search is an extra; never fail routing over it
            from ...logging_config import logger

            logger.warning(f"Meaning search failed, using keywords only: {exc}")
            return keyword[:limit]
        return self._fill_with_dense(keyword, records, similarity, limit)

    # Embed every agent now, blocking (evals do this so every search sees all vectors). No-op for bm25.
    def prepare(self) -> int:
        embedder = self._dense_embedder()
        if embedder is None:
            return 0
        from .embeddings import store_for

        store = store_for(self._registry.db_path)
        before = store.embedded
        store.vectors(self._registry.list_agents(), embedder)
        return store.embedded - before

    # Keyword search only: hard matches, then BM25. The resolver uses this, so similarity never merges.
    def keyword_search(
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
        return self._keyword_ranking(query, records, now)[:limit]

    def _fill_with_dense(
        self,
        keyword: List[ScoredAgent],
        records: Sequence[AgentRecord],
        similarity: Dict[str, float],
        limit: int,
    ) -> List[ScoredAgent]:
        """Keep the keyword top (limit - dense_slots), then the best meaning matches not yet chosen.

        Hard matches always stay. If too few agents pass the similarity floor, the remaining keyword
        results take the places back, so a message with no meaning match gives today's results.
        """
        hard = sum(1 for item in keyword if item.score >= HARD_MATCH_BONUS)
        head = keyword[: min(limit, max(limit - self.dense_slots, hard))]
        chosen = {item.name for item in head}
        by_name = {item.name: item for item in keyword}
        extras: List[ScoredAgent] = []
        room = min(self.dense_slots, limit - len(head))
        for record in sorted(records, key=lambda r: (similarity.get(r.name, 0.0), r.last_active_at), reverse=True):
            if len(extras) >= room:
                break
            value = similarity.get(record.name, 0.0)
            if value < self.dense_min_similarity:
                break
            if record.name in chosen:
                continue
            reasons = list(by_name[record.name].reasons) if record.name in by_name else []
            reasons.append(f"similar meaning ({value:.2f})")
            extras.append(ScoredAgent(record=record, score=value, reasons=reasons))
            chosen.add(record.name)
        results = head + extras
        for item in keyword[len(head):]:
            if len(results) >= limit:
                break
            if item.name not in chosen:
                results.append(item)
                chosen.add(item.name)
        return results

    # Every agent with a keyword or hard match, best first, with a small recency boost
    def _keyword_ranking(
        self, query: str, records: Sequence[AgentRecord], now: Optional[datetime]
    ) -> List[ScoredAgent]:
        now = now or self._registry.now()
        query_tokens = tokenize(query)
        hard_matches = self._hard_matches(query, records)
        documents = {record.name: agent_document_tokens(record) for record in records}
        index = BM25Index(documents)
        keyword_scores = index.score(query_tokens)
        query_terms = {term for term in query_tokens if index.idf(term) >= MIN_REASON_IDF}

        results: List[ScoredAgent] = []
        for record in records:
            keyword = keyword_scores.get(record.name, 0.0)
            hard_reasons = hard_matches.get(record.name, [])
            if keyword <= 0 and not hard_reasons:
                continue

            reasons = list(hard_reasons)
            score = keyword * STATUS_WEIGHT.get(record.status, 1.0)
            score *= 1 + RECENCY_BOOST * self._recency(record, now)
            shared = sorted(query_terms & set(documents[record.name]))[:4]
            if keyword > 0 and shared:
                reasons.append("keywords: " + ", ".join(shared))
            if hard_reasons:
                score += HARD_MATCH_BONUS
            results.append(ScoredAgent(record=record, score=score, reasons=reasons))

        results.sort(key=lambda item: (item.score, item.record.last_active_at), reverse=True)
        return results

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
        query_tokens = set(tokenize(query))
        if not query_tokens or limit <= 0:
            return []
        skip = set(exclude)
        matched = [
            record
            for record in self._registry.list_agents()
            if record.name not in skip
            and query_tokens & set(tokenize(" ".join(record.entities.get("person", []))))
        ]
        matched.sort(key=lambda record: record.last_active_at, reverse=True)
        return matched[:limit]

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
    "DENSE_MIN_SIMILARITY",
    "DENSE_SLOTS",
    "HARD_MATCH_BONUS",
    "RETRIEVERS",
    "RETRIEVER_BM25",
    "RETRIEVER_HYBRID",
    "MAX_AGENTS_PER_HARD_ENTITY",
    "STATUS_WEIGHT",
    "ScoredAgent",
    "agent_document_tokens",
]
