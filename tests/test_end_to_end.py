"""A full turn through the real runtimes with only the model calls faked.

user message -> interaction agent -> send_message_to_agent -> batch manager -> execution
agent -> profile refresh -> result back to the interaction agent -> next turn's prompt.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from server.agents.execution_agent import runtime as execution_runtime
from server.agents.interaction_agent import agent as interaction_agent
from server.agents.interaction_agent import runtime as interaction_runtime
from server.config import get_settings
from server.services.conversation import log as conversation_log_module
from server.services.conversation.log import ConversationLog
from server.services.execution import log_store as log_store_module
from server.services.execution import routing
from server.services.execution.log_store import ExecutionAgentLogStore
from server.services.execution.roster import set_agent_registry


def tool_call(call_id, name, **arguments):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def reply(content="", calls=None):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return {"choices": [{"message": message}]}


class FakeModels:
    """Scripted model: each interaction turn routes to a given agent name, then stops."""

    def __init__(self):
        self.route_to = None
        self.interaction_prompts = []
        self.execution_prompts = []

    async def interaction(self, *, model, messages, system, api_key, tools):
        if messages[-1]["role"] == "user":
            self.interaction_prompts.append(messages[0]["content"])
            if "<new_user_message>" in messages[0]["content"] and self.route_to:
                name, instructions = self.route_to
                return reply(calls=[
                    tool_call("a", "send_message_to_user", message="On it."),
                    tool_call("b", "send_message_to_agent", agent_name=name, instructions=instructions),
                ])
        return reply(content="Done.")

    async def execution(self, *, model, messages, system, api_key, tools):
        self.execution_prompts.append(system)
        return reply(content="Draft created for Sarah Chen (sarah.chen@acme.com) about the Q3 invoice.")


@pytest.fixture
def server_state(registry, tmp_path, monkeypatch):
    set_agent_registry(registry)
    monkeypatch.setattr(routing, "_ROUTING_LOG_PATH", tmp_path / "routing.jsonl")
    monkeypatch.setattr(log_store_module, "_execution_agent_logs", ExecutionAgentLogStore(tmp_path / "logs"))
    monkeypatch.setattr(conversation_log_module, "_conversation_log", ConversationLog(tmp_path / "conversation.log"))
    monkeypatch.setattr(interaction_runtime, "get_working_memory_log", lambda: SimpleNamespace(render_transcript=lambda: ""))
    # The hourly lifecycle pass would read the real triggers database; it has its own tests
    monkeypatch.setattr(interaction_agent, "maybe_apply_lifecycle", lambda **kwargs: None)
    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "conversation_summary_threshold", 0)

    models = FakeModels()
    monkeypatch.setattr(interaction_runtime, "request_chat_completion", models.interaction)
    monkeypatch.setattr(execution_runtime, "request_chat_completion", models.execution)
    yield SimpleNamespace(registry=registry, models=models)
    set_agent_registry(None)


async def user_turn(text):
    result = await interaction_runtime.InteractionAgentRuntime().execute(text)
    # Let the background execution (and its report back to the interaction agent) finish
    for _ in range(50):
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        if not pending:
            break
        await asyncio.gather(*pending)
    return result


def test_a_turn_creates_an_agent_and_the_next_turn_finds_it(server_state):
    registry, models = server_state.registry, server_state.models

    async def conversation():
        models.route_to = ("Email to Sarah Chen", "Draft a reply to sarah.chen@acme.com about the Q3 invoice")
        first = await user_turn("Reply to Sarah Chen about the Q3 invoice")
        # The model picks a reworded name; the resolver should route it to the same agent
        models.route_to = ("Sarah Chen invoice thread", "Has sarah.chen@acme.com replied?")
        second = await user_turn("Did Sarah Chen get back to us on the invoice?")
        return first, second

    first, second = asyncio.run(conversation())

    assert first.success and second.success
    assert registry.names() == ["Email to Sarah Chen"]
    record = registry.get("Email to Sarah Chen")
    assert record.run_count == 2
    assert "Draft created for Sarah Chen" in record.summary
    assert record.entities["email"] == ["sarah.chen@acme.com"]

    # Second user turn: the prompt carried the agent and its summary, not just a name
    second_user_prompt = [p for p in models.interaction_prompts if "Did Sarah Chen" in p][0]
    assert '<active_agents total="1" shown="1">' in second_user_prompt
    assert "Draft created for Sarah Chen" in second_user_prompt
    # And the execution agent was told what it had done before
    assert "What this agent has done so far" in models.execution_prompts[-1]
