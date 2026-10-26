"""send_message_to_agent / search_agents with a fake execution layer (no LLM, no Gmail)."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from server.agents.interaction_agent import tools
from server.services.execution import routing
from server.services.execution.log_store import ExecutionAgentLogStore
from server.services.execution.roster import set_agent_registry


@pytest.fixture
def wired(registry, tmp_path, monkeypatch):
    set_agent_registry(registry)
    routing_log = tmp_path / "routing.jsonl"
    monkeypatch.setattr(routing, "_ROUTING_LOG_PATH", routing_log)
    logs = ExecutionAgentLogStore(tmp_path / "logs")
    monkeypatch.setattr(tools, "get_execution_agent_logs", lambda: logs)

    dispatched = []

    async def fake_execute(agent_name, instructions):
        dispatched.append(agent_name)
        return SimpleNamespace(success=True)

    monkeypatch.setattr(tools._EXECUTION_BATCH_MANAGER, "execute_agent", fake_execute)
    yield SimpleNamespace(registry=registry, dispatched=dispatched, logs=logs, routing_log=routing_log)
    set_agent_registry(None)


def call_tool(name, **arguments):
    """Run a tool inside an event loop and let its background task finish."""

    async def main():
        result = tools.handle_tool_call(name, json.dumps(arguments))
        await asyncio.sleep(0)
        return result

    return asyncio.run(main())


def test_first_message_creates_an_agent_with_a_searchable_profile(wired):
    result = call_tool(
        "send_message_to_agent",
        agent_name="Email to Sarah Chen",
        instructions="Draft a reply to sarah.chen@acme.com about the Q3 invoice",
    )
    assert result.success
    assert result.payload["new_agent_created"] is True
    assert wired.dispatched == ["Email to Sarah Chen"]

    record = wired.registry.get("Email to Sarah Chen")
    assert "Q3 invoice" in record.summary
    assert record.entities["email"] == ["sarah.chen@acme.com"]


def test_reworded_follow_up_is_routed_to_the_existing_agent(wired):
    call_tool(
        "send_message_to_agent",
        agent_name="Email to Sarah Chen",
        instructions="Draft a reply to sarah.chen@acme.com about the Q3 invoice",
    )
    result = call_tool(
        "send_message_to_agent",
        agent_name="Sarah invoice follow-up",
        instructions="Ask sarah.chen@acme.com whether the invoice was paid",
    )

    assert result.payload["new_agent_created"] is False
    assert result.payload["agent_name"] == "Email to Sarah Chen"
    assert result.payload["requested_agent_name"] == "Sarah invoice follow-up"
    assert "Use this name" in result.payload["note"]
    assert wired.registry.count() == 1
    assert wired.dispatched == ["Email to Sarah Chen", "Email to Sarah Chen"]
    # The request is logged under the agent that actually ran it
    requests = [payload for tag, _, payload in wired.logs.iter_entries("Email to Sarah Chen") if tag == "agent_request"]
    assert len(requests) == 2


def test_every_routing_decision_is_logged(wired):
    call_tool("send_message_to_agent", agent_name="Vercel Job Offer", instructions="check the offer")
    call_tool("send_message_to_agent", agent_name="Vercel Job Offer", instructions="accept it")
    lines = [json.loads(line) for line in wired.routing_log.read_text(encoding="utf-8").splitlines()]
    assert [line["reason"] for line in lines] == ["new", "exact_name"]
    assert lines[1]["created"] is False


def test_search_agents_returns_matches_with_reasons(two_sarahs, wired):
    result = call_tool("search_agents", query="sarah venue")
    matches = result.payload["matches"]
    assert result.payload["total_agents"] == 4
    assert matches[0]["agent_name"] == "Sarah venue contract"
    assert matches[0]["matched"].startswith("keywords:")
    assert "summary" in matches[0]


def test_search_agents_requires_a_query(wired):
    result = call_tool("search_agents")
    assert not result.success


def test_result_carries_the_agent_id(wired):
    from server.services.execution.ids import agent_id_for

    result = call_tool("send_message_to_agent", agent_name="Vercel Job Offer", instructions="check the offer")
    assert result.payload["agent_id"] == agent_id_for("Vercel Job Offer")


def test_new_task_flag_starts_a_separate_agent_for_a_known_contact(wired):
    call_tool(
        "send_message_to_agent",
        agent_name="Email to Sarah Chen",
        instructions="Draft a reply to sarah.chen@acme.com about the Q3 invoice",
    )
    result = call_tool(
        "send_message_to_agent",
        agent_name="Email to Sarah Chen",
        instructions="Start a new email to sarah.chen@acme.com about the audit findings",
        new_task=True,
    )

    assert result.payload["new_agent_created"] is True
    assert result.payload["agent_name"] == "Email to Sarah Chen (2)"
    assert "another agent's name" in result.payload["note"]
    assert wired.registry.count() == 2
    lines = [json.loads(line) for line in wired.routing_log.read_text(encoding="utf-8").splitlines()]
    assert lines[-1]["new_task"] is True
