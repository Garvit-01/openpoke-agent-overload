"""Stable agent ids: different names never share a log file, and old slug logs are carried over."""

import json

from server.services.execution.ids import ID_PREFIX, agent_id_for
from server.services.execution.log_store import ExecutionAgentLogStore, _slugify
from server.services.execution.registry import AgentRecord
from server.services.execution.roster import migrate_legacy_roster
from server.services.execution.routing import route_to_agent

COLLIDING = ["Email to Sarah", "email to sarah", "Email-to-Sarah!", "Email to Sarah?"]


def test_names_that_shared_a_slug_get_different_ids():
    # The bug: the original store wrote all four of these to email-to-sarah.log
    assert len({_slugify(name) for name in COLLIDING}) == 1
    assert len({agent_id_for(name) for name in COLLIDING}) == len(COLLIDING)


def test_id_is_stable_and_file_safe():
    first = agent_id_for("Email to Sarah Chen")
    assert first == agent_id_for("Email to Sarah Chen")
    assert first.startswith(ID_PREFIX)
    assert first[len(ID_PREFIX):].isalnum()


def test_histories_of_colliding_names_stay_apart(tmp_path):
    logs = ExecutionAgentLogStore(tmp_path)
    for name in COLLIDING:
        logs.record_request(name, f"request for {name}")

    for name in COLLIDING:
        payloads = [payload for _tag, _ts, payload in logs.iter_entries(name)]
        assert payloads == [f"request for {name}"]
    assert sorted(logs.list_agents()) == sorted(agent_id_for(name) for name in COLLIDING)


def test_legacy_slug_log_is_moved_to_the_id_file(tmp_path):
    legacy = tmp_path / "email-to-sarah.log"
    legacy.write_text('<agent_request timestamp="t">old request</agent_request>\n', encoding="utf-8")
    logs = ExecutionAgentLogStore(tmp_path)

    # Oldest first: the first name to claim a shared old file keeps it
    assert logs.adopt_legacy_logs(["Email to Sarah", "email to sarah"]) == 1
    assert not legacy.exists()
    assert [p for _t, _ts, p in logs.iter_entries("Email to Sarah")] == ["old request"]
    assert list(logs.iter_entries("email to sarah")) == []
    # Running it again does nothing
    assert logs.adopt_legacy_logs(["Email to Sarah"]) == 0


def test_adoption_never_overwrites_an_existing_id_log(tmp_path):
    logs = ExecutionAgentLogStore(tmp_path)
    logs.record_request("Email to Sarah", "new request")
    (tmp_path / "email-to-sarah.log").write_text('<agent_request timestamp="t">old</agent_request>\n', encoding="utf-8")

    assert logs.adopt_legacy_logs(["Email to Sarah"]) == 0
    assert [p for _t, _ts, p in logs.iter_entries("Email to Sarah")] == ["new request"]


def test_roster_migration_reads_old_slug_logs(tmp_path, registry):
    roster_path = tmp_path / "roster.json"
    roster_path.write_text(json.dumps(["Email to Sarah"]), encoding="utf-8")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "email-to-sarah.log").write_text(
        '<agent_request timestamp="t">Reply to sarah.chen@acme.com about the Q3 invoice</agent_request>\n',
        encoding="utf-8",
    )
    logs = ExecutionAgentLogStore(log_dir)

    assert migrate_legacy_roster(registry, roster_path, logs) == 1
    assert (log_dir / f"{agent_id_for('Email to Sarah')}.log").exists()
    assert registry.get("Email to Sarah").entities["email"] == ["sarah.chen@acme.com"]


def test_record_and_routing_log_carry_the_id(registry, tmp_path):
    routing_log = tmp_path / "routing.jsonl"
    route_to_agent("Email to Sarah Chen", "Reply to sarah.chen@acme.com", registry=registry, routing_log_path=routing_log)

    record = registry.get("Email to Sarah Chen")
    assert record.agent_id == agent_id_for("Email to Sarah Chen")
    entry = json.loads(routing_log.read_text(encoding="utf-8").splitlines()[0])
    assert entry["agent_id"] == record.agent_id


def test_agent_record_id_matches_log_file_name(tmp_path):
    logs = ExecutionAgentLogStore(tmp_path)
    logs.record_request("Vercel Job Offer", "track the offer")
    assert logs.list_agents() == [AgentRecord(name="Vercel Job Offer").agent_id]
