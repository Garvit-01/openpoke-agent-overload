import json

from server.services.execution.log_store import ExecutionAgentLogStore
from server.services.execution.registry import STATUS_ACTIVE, STATUS_ARCHIVED, AgentRegistry
from server.services.execution.roster import AgentRoster, load_legacy_roster, migrate_legacy_roster

from .conftest import add_agent


def test_ensure_agent_creates_once_then_reuses(registry, clock):
    record, created = registry.ensure_agent("Email to Sarah")
    assert created and record.status == STATUS_ACTIVE

    clock.advance(days=2)
    again, created_again = registry.ensure_agent("Email to Sarah")
    assert not created_again
    assert again.last_active > record.last_active
    assert registry.count() == 1


def test_reusing_an_archived_agent_revives_it(registry):
    add_agent(registry, "Old thread", status=STATUS_ARCHIVED, days_ago=90)
    record, created = registry.ensure_agent("Old thread")
    assert not created
    assert record.status == STATUS_ACTIVE


def test_profile_and_entity_lookup(registry):
    registry.ensure_agent("Email to Sarah")
    registry.update_profile(
        "Email to Sarah",
        summary="Invoice thread",
        entities={"email": ["sarah.chen@acme.com"], "thread": ["18c2f0a9b1d2e3f4"]},
        count_run=True,
    )
    record = registry.get("Email to Sarah")
    assert record.summary == "Invoice thread"
    assert record.run_count == 1
    assert registry.find_by_entity("email", "Sarah.Chen@acme.com") == ["Email to Sarah"]
    assert registry.find_by_entity("thread", "18c2f0a9b1d2e3f4") == ["Email to Sarah"]
    assert registry.find_by_entity("email", "nobody@x.io") == []


def test_find_by_name_key_matches_reworded_names(registry):
    registry.ensure_agent("Email to Sarah Chen")
    assert registry.find_by_name_key("Sarah Chen thread") == ["Email to Sarah Chen"]
    assert registry.find_by_name_key("Sarah Patel thread") == []


def test_registry_persists_across_instances(tmp_path):
    path = tmp_path / "registry.db"
    AgentRegistry(path).ensure_agent("Vercel Job Offer")
    assert AgentRegistry(path).names() == ["Vercel Job Offer"]


def test_clear_removes_agents_and_entities(registry):
    add_agent(registry, "A", entities={"email": ["a@x.io"]})
    registry.clear()
    assert registry.count() == 0
    assert registry.find_by_entity("email", "a@x.io") == []


def test_roster_facade_keeps_the_old_interface(registry):
    roster = AgentRoster(registry)
    roster.load()
    roster.add_agent("Email to Sarah")
    roster.add_agent("Email to Sarah")
    assert roster.get_agents() == ["Email to Sarah"]
    roster.clear()
    assert roster.get_agents() == []


def test_migrates_legacy_roster_json_with_summaries_from_logs(tmp_path, registry):
    roster_path = tmp_path / "roster.json"
    roster_path.write_text(json.dumps(["Email to Sarah", "Vercel Job Offer"]), encoding="utf-8")
    logs = ExecutionAgentLogStore(tmp_path / "logs")
    logs.record_request("Email to Sarah", "Reply to sarah.chen@acme.com about the Q3 invoice")
    logs.record_agent_response("Email to Sarah", "Draft created for Sarah Chen")

    assert load_legacy_roster(roster_path) == ["Email to Sarah", "Vercel Job Offer"]
    assert migrate_legacy_roster(registry, roster_path, logs) == 2

    sarah = registry.get("Email to Sarah")
    assert "Q3 invoice" in sarah.summary
    assert sarah.entities["email"] == ["sarah.chen@acme.com"]
    assert registry.get("Vercel Job Offer").summary == ""
    # The old file is kept (renamed), so migration runs once and nothing is lost
    assert not roster_path.exists()
    assert (tmp_path / "roster.json.migrated").exists()
    assert migrate_legacy_roster(registry, roster_path, logs) == 0


def test_bad_legacy_roster_is_ignored(tmp_path, registry):
    roster_path = tmp_path / "roster.json"
    roster_path.write_text("{not json", encoding="utf-8")
    assert migrate_legacy_roster(registry, roster_path) == 0
    assert registry.count() == 0
