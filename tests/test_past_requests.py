"""Past requests: every request routed to an agent is kept (last 20) and searchable at a lower weight.

They only help an agent get found. The resolver never reads them, so they can never merge agents.
"""

import asyncio

import pytest

from server.config import get_settings
from server.services.execution import log_store as log_store_module
from server.services.execution import roster as roster_module
from server.services.execution.log_store import ExecutionAgentLogStore
from server.services.execution.registry import (
    REQUEST_HISTORY_LIMIT,
    REQUEST_MAX_CHARS,
    REQUESTS_BACKFILLED_KEY,
    AgentRecord,
    AgentRegistry,
)
from server.services.execution.resolver import resolve_agent
from server.services.execution.retrieval import AgentRetriever, request_snippet
from server.services.execution.roster import backfill_requests_from_logs
from server.services.execution.routing import route_to_agent

from .conftest import add_agent


@pytest.fixture
def grocery(registry) -> AgentRegistry:
    """The live-demo case: "add 3 apples" was a middle request, so the summary never mentions it."""
    add_agent(
        registry,
        "Grocery list",
        summary="Task: start a grocery list with bread and milk | Latest ask: remove the milk | Latest result: list updated",
        days_ago=2,
    )
    add_agent(registry, "Email to Sarah", summary="Task: reply to Sarah Chen about the Q3 invoice", days_ago=1)
    add_agent(registry, "Reminder: pay rent", summary="Task: remind the user to pay rent", days_ago=0)
    for text in ["Start a grocery list with bread and milk", "Add 3 apples to the list", "Remove the milk"]:
        registry.add_request("Grocery list", text)
    return registry


def test_a_middle_request_makes_the_agent_findable(grocery):
    results = AgentRetriever(grocery, index_requests=True).search("how many apples did I want?")
    assert [item.name for item in results] == ["Grocery list"]
    assert results[0].reasons == ["past request: Add 3 apples to the list"]
    # Without past requests nothing mentions apples, which is the bug this fixes
    assert AgentRetriever(grocery, index_requests=False).search("how many apples did I want?") == []


def test_setting_off_restores_todays_behaviour(grocery, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_index_requests", False)
    retriever = AgentRetriever(grocery)
    assert not retriever.index_requests
    assert retriever.search("how many apples did I want?") == []
    # Same ranking and scores as a retriever that never heard of past requests
    for query in ["did sarah reply about the invoice", "grocery list", "pay the rent"]:
        default = [(item.name, round(item.score, 9), item.reasons) for item in retriever.search(query)]
        explicit = AgentRetriever(grocery, index_requests=False).search(query)
        assert default == [(item.name, round(item.score, 9), item.reasons) for item in explicit]

    monkeypatch.setattr(get_settings(), "agent_index_requests", True)
    assert AgentRetriever(grocery).search("apples")[0].name == "Grocery list"


def test_words_already_in_the_profile_are_not_counted_again(grocery):
    # "grocery", "bread" and "milk" are in the summary; the requests repeat them but must not add to the score
    with_requests = AgentRetriever(grocery, index_requests=True).search("grocery bread milk")
    without = AgentRetriever(grocery, index_requests=False).search("grocery bread milk")
    assert with_requests[0].score == pytest.approx(without[0].score)
    assert not any(reason.startswith("past request") for reason in with_requests[0].reasons)


def test_a_long_history_does_not_outrank_a_name_match(registry):
    add_agent(registry, "Acme invoice", summary="Task: pay the Acme invoice", days_ago=5)
    add_agent(registry, "Busy assistant", summary="Task: misc errands", days_ago=0)
    for index in range(REQUEST_HISTORY_LIMIT):
        registry.add_request("Busy assistant", f"errand {index}: check the acme portal and the invoice folder")
    results = AgentRetriever(registry, index_requests=True).search("acme invoice")
    assert [item.name for item in results] == ["Acme invoice", "Busy assistant"]


def test_history_is_capped_and_repeats_are_not_duplicated(registry):
    registry.ensure_agent("Daily digest")
    for index in range(REQUEST_HISTORY_LIMIT + 5):
        registry.add_request("Daily digest", f"request number {index} about topic{index}")
    stored = registry.requests_for("Daily digest")
    assert len(stored) == REQUEST_HISTORY_LIMIT
    assert stored[0] == "request number 5 about topic5" and stored[-1].endswith("topic24")
    retriever = AgentRetriever(registry, index_requests=True)
    assert retriever.search("topic3") == []  # dropped with the oldest requests
    assert retriever.search("topic7")[0].name == "Daily digest"

    # A trigger that fires the same payload every day keeps one copy, moved to the newest position
    registry.add_request("Daily digest", "request number 6 about topic6")
    stored = registry.requests_for("Daily digest")
    assert len(stored) == REQUEST_HISTORY_LIMIT and stored.count("request number 6 about topic6") == 1
    assert stored[-1] == "request number 6 about topic6"


def test_requests_are_clipped_and_need_an_existing_agent(registry):
    registry.ensure_agent("Notes")
    assert registry.add_request("Notes", "word " * 500)
    assert len(registry.requests_for("Notes")[0]) <= REQUEST_MAX_CHARS
    assert not registry.add_request("Notes", "   ")
    assert not registry.add_request("No such agent", "hello")


def test_records_carry_their_requests_through_import_and_clear(registry):
    registry.import_records([AgentRecord(name="Imported", requests=["first ask", "second ask"])])
    assert registry.get("Imported").requests == ["first ask", "second ask"]
    assert registry.list_agents()[0].requests == []  # not loaded unless asked for
    assert registry.list_agents(with_requests=True)[0].requests == ["first ask", "second ask"]
    registry.clear()
    registry.ensure_agent("Imported")
    assert registry.requests_for("Imported") == []


def test_backfill_from_an_existing_log(tmp_path, registry):
    logs = ExecutionAgentLogStore(tmp_path / "logs")
    registry.ensure_agent("Grocery list")
    registry.ensure_agent("Empty agent")
    for text in ["Start a grocery list", "Add 3 apples", "Add oat milk", "Add 3 apples"]:
        logs.record_request("Grocery list", text)
    logs.record_agent_response("Grocery list", "List updated")

    assert backfill_requests_from_logs(registry, logs) == 1
    assert registry.requests_for("Grocery list") == ["Start a grocery list", "Add oat milk", "Add 3 apples"]
    assert registry.requests_for("Empty agent") == []
    assert registry.get_meta(REQUESTS_BACKFILLED_KEY)
    assert AgentRetriever(registry, index_requests=True).search("apples")[0].name == "Grocery list"

    # Once only: a second start does nothing, even if the log grew
    logs.record_request("Grocery list", "Add bananas")
    assert backfill_requests_from_logs(registry, logs) == 0
    assert "Add bananas" not in registry.requests_for("Grocery list")


def test_backfill_keeps_only_the_newest_requests(tmp_path, registry):
    logs = ExecutionAgentLogStore(tmp_path / "logs")
    registry.ensure_agent("Long running")
    for index in range(REQUEST_HISTORY_LIMIT + 10):
        logs.record_request("Long running", f"request {index}")
    backfill_requests_from_logs(registry, logs)
    stored = registry.requests_for("Long running")
    assert stored == [f"request {index}" for index in range(10, REQUEST_HISTORY_LIMIT + 10)]


def test_server_startup_runs_the_backfill_once():
    # conftest points the registry path and the log store at a temp folder
    first = AgentRegistry(roster_module._REGISTRY_PATH)
    first.ensure_agent("Grocery list")
    log_store_module.get_execution_agent_logs().record_request("Grocery list", "Add 3 apples")

    registry = roster_module.get_agent_registry()
    assert registry.requests_for("Grocery list") == ["Add 3 apples"]
    assert registry.get_meta(REQUESTS_BACKFILLED_KEY)


def test_chat_path_stores_each_routed_request(registry, tmp_path):
    log = tmp_path / "routing.jsonl"
    route_to_agent("Grocery list", "Start a grocery list with bread", registry=registry, routing_log_path=log)
    route_to_agent("Grocery list", "Add 3 apples", registry=registry, routing_log_path=log)
    route_to_agent("Grocery list", "Remove the bread", registry=registry, routing_log_path=log)
    assert registry.requests_for("Grocery list") == [
        "Start a grocery list with bread", "Add 3 apples", "Remove the bread",
    ]


def test_trigger_path_stores_the_payload(registry, monkeypatch):
    from server.agents.execution_agent.runtime import ExecutionResult
    from server.services import trigger_scheduler as scheduler_module
    from server.services.execution import routing as routing_module
    from server.services.triggers.models import TriggerRecord

    registry.ensure_agent("Reminder: water the plants")
    monkeypatch.setattr(routing_module, "get_agent_registry", lambda: registry)
    ran = []

    class FakeManager:
        async def execute_agent(self, agent_name, instructions):
            ran.append(agent_name)
            return ExecutionResult(agent_name=agent_name, success=True, response="done")

    class FakeService:
        def schedule_next_occurrence(self, trigger, fired_at):
            pass

    monkeypatch.setattr(scheduler_module, "ExecutionBatchManager", FakeManager)
    scheduler = scheduler_module.TriggerScheduler()
    scheduler._service = FakeService()
    trigger = TriggerRecord(
        id=1, agent_name="Reminder: water the plants", payload="Water the fern and the basil",
        status="active", created_at="2026-10-01T00:00:00Z", updated_at="2026-10-01T00:00:00Z",
    )
    asyncio.run(scheduler._execute_trigger(trigger))
    asyncio.run(scheduler._execute_trigger(trigger))
    assert ran == ["Reminder: water the plants"] * 2
    # The payload only (no "Trigger fired at" lines), and one copy however often it fires
    assert registry.requests_for("Reminder: water the plants") == ["Water the fern and the basil"]


def test_past_requests_never_merge_agents(grocery):
    # A new name whose instructions only match another agent's past request: still a new agent,
    # and not even reported as a possible duplicate
    resolution = resolve_agent("Fruit order", "order 3 apples from the market", grocery)
    assert resolution.created
    assert resolution.possible_duplicates == []
    # Search does find it, so the model sees it on the shortlist and can choose it by name
    assert AgentRetriever(grocery, index_requests=True).search("order 3 apples")[0].name == "Grocery list"


def test_past_requests_never_pick_between_agents_that_share_an_address(registry):
    add_agent(registry, "Acme invoice", summary="invoice payment", entities={"email": ["ap@acme.com"]}, days_ago=0)
    add_agent(registry, "Acme contract", summary="contract renewal", entities={"email": ["ap@acme.com"]}, days_ago=1)
    registry.add_request("Acme contract", "ask whether the wire transfer cleared")
    # Only the address and the profile decide; the contract agent's old request about a wire does not
    resolution = resolve_agent("Acme", "ask ap@acme.com about the wire transfer", registry)
    assert resolution.agent_name == "Acme invoice"
    assert AgentRetriever(registry, index_requests=True).search("wire transfer")[0].name == "Acme contract"


def test_snippet_shows_the_words_that_matched():
    requests = ["Start the list", "Please also add three bags of apples, the green ones, and some pears for Sunday lunch"]
    assert request_snippet(requests, {"pears"}).startswith("...")
    assert "pears" in request_snippet(requests, {"pears"})
    assert request_snippet(requests, {"start"}) == "Start the list"
