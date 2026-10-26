"""Agent lifecycle: active -> dormant -> archived, and back to active when used again.

Nothing is deleted. Dormant and archived agents drop out of the "recent" slots in the
prompt and rank a little lower in search, but an exact match still finds them and
using one revives it (see AgentRegistry.ensure_agent).

An agent that owns an active trigger (a reminder that will fire later) is never
demoted, because the trigger scheduler wakes that agent by name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from ...logging_config import logger
from .registry import STATUS_ACTIVE, STATUS_ARCHIVED, STATUS_DORMANT, AgentRegistry

ActiveTriggerCount = Callable[[str], int]


@dataclass
class LifecyclePolicy:
    dormant_after_days: int = 14
    archive_after_days: int = 45


@dataclass
class LifecycleReport:
    dormant: List[str] = field(default_factory=list)
    archived: List[str] = field(default_factory=list)
    kept_for_triggers: List[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.dormant) + len(self.archived)


# Demote idle agents; agents holding active triggers stay (or become) active
def apply_lifecycle(
    registry: AgentRegistry,
    *,
    active_trigger_count: ActiveTriggerCount,
    policy: Optional[LifecyclePolicy] = None,
    now: Optional[datetime] = None,
) -> LifecycleReport:
    policy = policy or LifecyclePolicy()
    now = now or registry.now()
    dormant_cutoff = now - timedelta(days=policy.dormant_after_days)
    archive_cutoff = now - timedelta(days=policy.archive_after_days)
    report = LifecycleReport()

    for record in registry.list_agents():
        if active_trigger_count(record.name) > 0:
            if record.status != STATUS_ACTIVE:
                registry.set_status(record.name, STATUS_ACTIVE)
            report.kept_for_triggers.append(record.name)
            continue

        last_active = record.last_active
        if last_active <= archive_cutoff and record.status != STATUS_ARCHIVED:
            registry.set_status(record.name, STATUS_ARCHIVED)
            report.archived.append(record.name)
        elif archive_cutoff < last_active <= dormant_cutoff and record.status == STATUS_ACTIVE:
            registry.set_status(record.name, STATUS_DORMANT)
            report.dormant.append(record.name)

    if report.changed:
        logger.info(
            "agent lifecycle applied",
            extra={"dormant": len(report.dormant), "archived": len(report.archived)},
        )
    return report


# Count a given agent's triggers that are still scheduled to fire
def trigger_service_counter() -> ActiveTriggerCount:
    from ..triggers import get_trigger_service

    service = get_trigger_service()

    def _count(agent_name: str) -> int:
        try:
            return sum(1 for trigger in service.list_triggers(agent_name=agent_name) if trigger.status == "active")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("trigger lookup failed; keeping agent active", extra={"error": str(exc)})
            return 1

    return _count


__all__ = [
    "LifecyclePolicy",
    "LifecycleReport",
    "apply_lifecycle",
    "trigger_service_counter",
]
