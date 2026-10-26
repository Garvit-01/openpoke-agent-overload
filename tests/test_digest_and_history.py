from server.agents.execution_agent.agent import ExecutionAgent
from server.services.execution.digest import SUMMARY_MAX_CHARS, build_digest
from server.services.execution.log_store import ExecutionAgentLogStore


def test_digest_summarises_first_and_latest_activity():
    entries = [
        ("agent_request", "t1", "Reply to sarah.chen@acme.com about the Q3 invoice"),
        ("agent_action", "t2", 'Calling gmail_create_draft with: {"thread_id": "18c2f0a9b1d2e3f4"}'),
        ("tool_response", "t3", "found 20 emails from a@x.io, b@x.io, c@x.io"),
        ("agent_response", "t4", "Draft created"),
        ("agent_request", "t5", "Send it"),
        ("agent_response", "t6", "Sent"),
    ]
    summary, entities = build_digest("Email to Sarah Chen", entries)

    assert summary.startswith("Task: Reply to sarah.chen@acme.com about the Q3 invoice")
    assert "Latest ask: Send it" in summary and "Latest result: Sent" in summary
    assert entities["email"] == ["sarah.chen@acme.com"]
    assert entities["thread"] == ["18c2f0a9b1d2e3f4"]
    assert "Sarah Chen" in entities["person"]


def test_raw_tool_responses_do_not_add_entities():
    _summary, entities = build_digest("Search", [("tool_response", "t", "from: spam@x.io")])
    assert "email" not in entities


def test_digest_is_bounded():
    summary, _ = build_digest("A", [("agent_request", "t", "word " * 500), ("agent_response", "t", "done " * 500)])
    assert len(summary) <= SUMMARY_MAX_CHARS


def test_execution_history_keeps_only_recent_requests(tmp_path, monkeypatch):
    logs = ExecutionAgentLogStore(tmp_path)
    for index in range(25):
        logs.record_request("Long running", f"request number {index}")
        logs.record_agent_response("Long running", f"done {index}")

    agent = ExecutionAgent("Long running", conversation_limit=10)
    monkeypatch.setattr(agent, "_log_store", logs)
    monkeypatch.setattr(agent, "build_system_prompt", lambda: "BASE")
    prompt = agent.build_system_prompt_with_history()

    assert "(15 earlier requests omitted" in prompt
    assert "request number 14<" not in prompt
    assert "request number 15<" in prompt and "request number 24<" in prompt
    assert prompt.count("<agent_request") == 10
