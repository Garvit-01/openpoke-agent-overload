from server.services.execution.lifecycle import LifecyclePolicy, apply_lifecycle
from server.services.execution.registry import STATUS_ACTIVE, STATUS_ARCHIVED, STATUS_DORMANT

from .conftest import add_agent

POLICY = LifecyclePolicy(dormant_after_days=14, archive_after_days=45)


def no_triggers(_name: str) -> int:
    return 0


def statuses(registry):
    return {record.name: record.status for record in registry.list_agents()}


def test_idle_agents_become_dormant_then_archived(registry):
    add_agent(registry, "fresh", days_ago=2)
    add_agent(registry, "idle", days_ago=20)
    add_agent(registry, "stale", days_ago=60)

    report = apply_lifecycle(registry, active_trigger_count=no_triggers, policy=POLICY)

    assert statuses(registry) == {"fresh": STATUS_ACTIVE, "idle": STATUS_DORMANT, "stale": STATUS_ARCHIVED}
    assert report.dormant == ["idle"] and report.archived == ["stale"]


def test_dormant_agent_is_archived_once_it_passes_the_archive_age(registry, clock):
    add_agent(registry, "idle", days_ago=20)
    apply_lifecycle(registry, active_trigger_count=no_triggers, policy=POLICY)
    clock.advance(days=30)
    apply_lifecycle(registry, active_trigger_count=no_triggers, policy=POLICY)
    assert statuses(registry)["idle"] == STATUS_ARCHIVED


def test_agents_with_active_triggers_are_never_demoted(registry):
    add_agent(registry, "Reminder: rent", days_ago=200)
    add_agent(registry, "Reminder: old, demoted earlier", days_ago=200, status=STATUS_ARCHIVED)

    report = apply_lifecycle(registry, active_trigger_count=lambda name: 1, policy=POLICY)

    assert set(statuses(registry).values()) == {STATUS_ACTIVE}
    assert sorted(report.kept_for_triggers) == ["Reminder: old, demoted earlier", "Reminder: rent"]
    assert report.changed == 0


def test_using_an_archived_agent_revives_it(registry):
    add_agent(registry, "stale", days_ago=60)
    apply_lifecycle(registry, active_trigger_count=no_triggers, policy=POLICY)
    registry.ensure_agent("stale")
    assert statuses(registry)["stale"] == STATUS_ACTIVE


def test_lifecycle_is_idempotent(registry):
    add_agent(registry, "idle", days_ago=20)
    apply_lifecycle(registry, active_trigger_count=no_triggers, policy=POLICY)
    second = apply_lifecycle(registry, active_trigger_count=no_triggers, policy=POLICY)
    assert second.changed == 0
