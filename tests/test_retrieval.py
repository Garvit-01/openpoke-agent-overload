from server.services.execution.registry import STATUS_ARCHIVED, STATUS_DORMANT
from server.services.execution.retrieval import (
    HARD_MATCH_BONUS,
    MAX_AGENTS_PER_HARD_ENTITY,
    AgentRetriever,
)

from .conftest import add_agent


def test_exact_email_ranks_first_even_when_keywords_point_elsewhere(two_sarahs):
    # "venue" points at Sarah Patel by keywords, but the address is Sarah Chen's
    results = AgentRetriever(two_sarahs).search("tell sarah.chen@acme.com the venue is booked")
    assert results[0].name == "Email to Sarah"
    assert results[0].score >= HARD_MATCH_BONUS
    assert "email: sarah.chen@acme.com" in results[0].reasons


def test_topic_disambiguates_two_people_with_the_same_first_name(two_sarahs):
    retriever = AgentRetriever(two_sarahs)
    assert retriever.search("did sarah say anything about the invoice")[0].name == "Email to Sarah"
    assert retriever.search("any news from sarah on the venue contract")[0].name == "Sarah venue contract"


def test_unrelated_query_returns_nothing(two_sarahs):
    assert AgentRetriever(two_sarahs).search("book a dentist appointment") == []


def test_archived_agents_stay_searchable_but_rank_below_active_ones(registry):
    add_agent(registry, "Acme invoice (old)", summary="acme invoice payment", status=STATUS_ARCHIVED, days_ago=1)
    add_agent(registry, "Acme invoice", summary="acme invoice payment", days_ago=1)
    results = AgentRetriever(registry).search("acme invoice")
    assert [item.name for item in results] == ["Acme invoice", "Acme invoice (old)"]


def test_dormant_status_is_a_penalty_not_a_filter(registry):
    add_agent(registry, "Passport renewal", summary="renew passport", status=STATUS_DORMANT, days_ago=30)
    assert AgentRetriever(registry).search("passport")[0].name == "Passport renewal"


def test_email_shared_by_many_agents_is_not_treated_as_a_hard_match(registry):
    for index in range(MAX_AGENTS_PER_HARD_ENTITY + 1):
        add_agent(registry, f"Task {index}", entities={"email": ["me@user.com"]})
    results = AgentRetriever(registry).search("forward this to me@user.com")
    assert all(item.score < HARD_MATCH_BONUS for item in results)


def test_shortlist_adds_recent_agents_and_never_duplicates(two_sarahs):
    shortlist = AgentRetriever(two_sarahs).shortlist("the invoice", retrieved=1, recent=2)
    names = [item.name for item in shortlist]
    assert names[0] == "Email to Sarah"
    assert "Sarah venue contract" in names  # most recently active
    assert len(names) == len(set(names)) == 3


def test_shortlist_for_a_contentless_message_falls_back_to_recent(two_sarahs):
    shortlist = AgentRetriever(two_sarahs).shortlist("ok send it", retrieved=8, recent=2)
    assert [item.name for item in shortlist] == ["Sarah venue contract", "Email to Sarah"]
    assert all(item.reasons == ["recent"] for item in shortlist)


def test_recent_slots_skip_dormant_and_archived(registry):
    add_agent(registry, "Active", days_ago=5)
    add_agent(registry, "Dormant", status=STATUS_DORMANT, days_ago=1)
    assert [item.name for item in AgentRetriever(registry).recent(limit=5)] == ["Active"]


def test_archived_agent_with_a_clearly_better_match_still_wins(registry):
    # Regression: a multiplicative archive penalty used to push the only "Acme" agent below weaker matches
    add_agent(registry, "Q3 Invoice with Omar", summary="Q3 invoice for Omar at Acme", status=STATUS_ARCHIVED, days_ago=60)
    for index in range(5):
        add_agent(registry, f"Q3 invoice {index}", summary=f"Q3 invoice for contact {index}", days_ago=0)
    assert AgentRetriever(registry).search("what's the latest on the Acme Q3 invoice")[0].name == "Q3 Invoice with Omar"


def test_near_universal_words_are_not_listed_as_reasons(registry):
    for index in range(6):
        add_agent(registry, f"Agent {index}", summary=f"Latest result: topic{index}")
    results = AgentRetriever(registry).search("latest on topic3")
    assert results[0].name == "Agent 3"
    assert results[0].reasons == ["keywords: topic3"]
