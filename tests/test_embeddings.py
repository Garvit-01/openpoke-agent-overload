"""Hybrid agent search: keywords plus a local embedding model.

Most tests use a tiny fake embedder, so they run without fastembed. The last few use the real
model and are skipped unless fastembed is installed and the model is already downloaded
(they never download it and never call an LLM).
"""

import os
import re

import numpy as np
import pytest

from server.services.execution import embeddings as embeddings_module
from server.services.execution.embeddings import EmbeddingStore, agent_text, get_embedder, set_embedder
from server.services.execution.resolver import resolve_agent
from server.services.execution.retrieval import HARD_MATCH_BONUS, AgentRetriever

from .conftest import add_agent

# Words that mean the same thing share a dimension, like an embedding model would place them close
CONCEPTS = [
    {"computer", "computers", "laptop", "laptops", "machines"},
    {"school", "academy", "enrollment", "kids", "place"},
    {"translation", "japanese", "version", "manual", "booklet"},
    {"invoice", "billing", "payment"},
    {"venue", "contract", "offsite"},
    {"insurance", "policy", "renew"},
]


class FakeEmbedder:
    """Bag of concepts: deterministic, instant, and good enough to test the fusion rule."""

    model_name = "fake-concepts"

    def __init__(self):
        self.documents = 0
        self.queries = 0

    def _vector(self, text):
        vector = np.full(len(CONCEPTS) + 1, 0.05, dtype=np.float32)  # small shared part, like real models
        for word in re.findall(r"[a-z]+", text.lower()):
            for index, concept in enumerate(CONCEPTS):
                if word in concept:
                    vector[index] += 1.0
        return vector / np.linalg.norm(vector)

    def embed_documents(self, texts):
        self.documents += len(texts)
        return np.vstack([self._vector(text) for text in texts])

    def embed_query(self, text):
        self.queries += 1
        return self._vector(text)


@pytest.fixture
def fake():
    return FakeEmbedder()


@pytest.fixture
def office(registry):
    """Agents a keyword search can only find by name; meaning finds the laptop one."""
    add_agent(registry, "Ironbridge Laptop Refresh", summary="Task: order new laptops for the Ironbridge team", days_ago=40)
    add_agent(registry, "Email to Sarah", summary="Task: reply to Sarah Chen about the Q3 invoice",
              entities={"email": ["sarah.chen@acme.com"]}, days_ago=3)
    add_agent(registry, "Sarah venue contract", summary="Task: negotiate the venue contract with Sarah Patel", days_ago=1)
    add_agent(registry, "Reminder: renew car insurance", summary="Task: remind the user to renew car insurance", days_ago=20)
    for index in range(8):
        add_agent(registry, f"Old printers survey {index}", summary=f"Task: count desks on floor {index}", days_ago=10)
    return registry


def names(results):
    return [item.name for item in results]


def test_bm25_mode_gives_todays_results(office, fake):
    query = "where are we on replacing everyone's old computers"
    keyword = names(AgentRetriever(office).keyword_search(query))
    assert names(AgentRetriever(office, mode="bm25", embedder=fake).search(query)) == keyword
    assert fake.queries == 0 and fake.documents == 0  # bm25 never touches the embedder


def test_hybrid_without_fastembed_is_keyword_search(office, monkeypatch):
    monkeypatch.setattr(embeddings_module, "get_embedder", lambda: None)
    query = "where are we on replacing everyone's old computers"
    assert names(AgentRetriever(office, mode="hybrid").search(query)) == names(AgentRetriever(office).keyword_search(query))


def test_missing_package_turns_hybrid_off_quietly(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_fastembed(name, *args, **kwargs):
        if name == "fastembed":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    set_embedder(None)
    monkeypatch.setattr(builtins, "__import__", no_fastembed)
    try:
        assert get_embedder() is None
    finally:
        set_embedder(None)


def test_hybrid_fills_the_last_places_with_meaning_matches(office, fake):
    query = "where are we on replacing everyone's old computers"
    keyword = names(AgentRetriever(office).keyword_search(query))
    assert "Ironbridge Laptop Refresh" not in keyword  # no shared word
    hybrid = AgentRetriever(office, mode="hybrid", embedder=fake).search(query, limit=8)
    assert names(hybrid)[:5] == keyword[:5]  # keyword top results keep their places
    assert "Ironbridge Laptop Refresh" in names(hybrid)
    found = next(item for item in hybrid if item.name == "Ironbridge Laptop Refresh")
    assert any(reason.startswith("similar meaning") for reason in found.reasons)
    assert len(hybrid) <= 8 and len(set(names(hybrid))) == len(hybrid)


def test_exact_email_and_name_hits_still_rank_first(office, fake):
    retriever = AgentRetriever(office, mode="hybrid", embedder=fake)
    # Meaning points at the venue contract, the address at Sarah Chen
    results = retriever.search("tell sarah.chen@acme.com the venue contract and offsite are booked")
    assert results[0].name == "Email to Sarah" and results[0].score >= HARD_MATCH_BONUS
    # A keyword-exact name stays on top even when meaning prefers other agents
    assert retriever.search("Sarah venue contract")[0].name == "Sarah venue contract"


def test_hard_matches_are_never_pushed_out(registry, fake):
    for index in range(5):
        add_agent(registry, f"Shared contact {index}", entities={"email": ["team@corp.com"]})
    add_agent(registry, "Laptop order", summary="new laptops")
    results = AgentRetriever(registry, mode="hybrid", embedder=fake, dense_slots=3).search(
        "laptops for team@corp.com", limit=6
    )
    assert sum(item.score >= HARD_MATCH_BONUS for item in results) == 5


def test_no_meaning_match_gives_the_places_back_to_keywords(office, fake):
    retriever = AgentRetriever(office, mode="hybrid", embedder=fake, dense_min_similarity=1.01)
    query = "Old printers survey"
    assert names(retriever.search(query)) == names(AgentRetriever(office).keyword_search(query))


def test_vectors_are_reused_while_the_text_is_unchanged(office, fake, tmp_path):
    retriever = AgentRetriever(office, mode="hybrid", embedder=fake)
    retriever.search("old computers")
    first = fake.documents
    assert first == office.count()
    retriever.search("the invoice")
    assert fake.documents == first  # nothing changed, nothing re-embedded

    office.update_profile("Email to Sarah", summary="Task: reply to Sarah Chen about the Q4 invoice")
    retriever.search("the invoice")
    assert fake.documents == first + 1  # only the changed agent

    # A fresh store on the same file (a restart) reads the stored vectors instead of embedding again
    store = EmbeddingStore(office.db_path)
    store.vectors(office.list_agents(), fake)
    assert store.embedded == 0


def test_agent_text_holds_name_summary_and_phrases(registry):
    add_agent(registry, "Ironbridge Laptop Refresh", summary="Task: order laptops",
              entities={"phrase": ["new computers", "it hardware"], "email": ["zane@ironbridge.com"]})
    text = agent_text(registry.get("Ironbridge Laptop Refresh"))
    assert "Ironbridge Laptop Refresh" in text and "order laptops" in text and "new computers" in text
    assert "zane@ironbridge.com" not in text  # exact ids are for hard matches, not meaning


def test_similarity_never_merges_agents(office, fake):
    retriever = AgentRetriever(office, mode="hybrid", embedder=fake)
    # Meaning says this is the laptop agent; no exact name, email, thread or name key matches it
    resolution = resolve_agent("New computers for the design team", "buy laptops for the designers", office, retriever)
    assert resolution.created and resolution.agent_name == "New computers for the design team"
    assert fake.queries == 0  # the resolver only uses keyword search and exact entities


# ------------------------------------------------------------------ real model (skipped if not available)


@pytest.fixture(scope="module")
def real_embedder():
    pytest.importorskip("fastembed")
    from server.services.execution.embeddings import DEFAULT_MODEL, FastEmbedEmbedder

    try:  # local files only: the test suite never downloads anything
        return FastEmbedEmbedder(DEFAULT_MODEL, cache_dir=os.getenv("OPENPOKE_EMBEDDING_CACHE"), local_files_only=True)
    except Exception as exc:
        pytest.skip(f"embedding model not downloaded: {exc}")


PARAPHRASE_MISSES = {
    "Did the kids get a place at the academy?": "School Enrollment with Jasmine",
    "Is the Japanese version of the instructions booklet ready?": "Orchard Manual Translation",
    "Where are we on replacing everyone's old computers?": "Ironbridge Laptop Refresh",
}


@pytest.fixture(scope="module")
def paraphrase_registry(real_embedder, tmp_path_factory):
    """40 generated agents plus the three hand-written ones keyword search could not find.

    Built once per module, so the agents are embedded once (later tests reuse the stored vectors).
    """
    from evals.paraphrase_set import HANDWRITTEN, handwritten_agent
    from evals.scenarios import build_scenario
    from server.services.execution.registry import AgentRegistry

    from .conftest import FakeClock

    registry = AgentRegistry(tmp_path_factory.mktemp("paraphrase") / "registry.db", clock=FakeClock())

    def insert(agent):
        add_agent(registry, agent.name, summary=agent.summary, entities=agent.entities, days_ago=agent.active_days_ago)

    for agent in build_scenario(40, seed=7).agents:
        insert(agent)
    for index, item in enumerate(item for item in HANDWRITTEN if item.name in PARAPHRASE_MISSES.values()):
        insert(handwritten_agent(item, f"{index:016x}"))
    return registry


@pytest.mark.parametrize("message", list(PARAPHRASE_MISSES))
def test_real_model_finds_the_paraphrases_keywords_missed(paraphrase_registry, real_embedder, message):
    gold = PARAPHRASE_MISSES[message]
    assert gold not in names(AgentRetriever(paraphrase_registry, mode="bm25").search(message, limit=8))
    hybrid = AgentRetriever(paraphrase_registry, mode="hybrid", embedder=real_embedder)
    assert gold in names(hybrid.search(message, limit=8))
    assert gold in names(hybrid.shortlist(message, retrieved=8, recent=4))


def test_real_model_keeps_exact_hits_first(paraphrase_registry, real_embedder):
    hybrid = AgentRetriever(paraphrase_registry, mode="hybrid", embedder=real_embedder)
    record = paraphrase_registry.get("Ironbridge Laptop Refresh")
    email = record.entities["email"][0]
    assert hybrid.search(f"reply to {email} about the school place")[0].name == "Ironbridge Laptop Refresh"
    assert hybrid.search("Orchard Manual Translation")[0].name == "Orchard Manual Translation"
