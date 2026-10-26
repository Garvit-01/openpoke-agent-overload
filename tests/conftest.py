"""Shared fixtures. Every test gets its own temporary registry; nothing touches server/data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pytest

from server.services.execution.registry import STATUS_ACTIVE, AgentRecord, AgentRegistry, to_iso

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


class FakeClock:
    """A clock the test can move forward."""

    def __init__(self, start: datetime = NOW):
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **delta) -> None:
        self.current += timedelta(**delta)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def registry(tmp_path, clock) -> AgentRegistry:
    return AgentRegistry(tmp_path / "registry.db", clock=clock)


def add_agent(
    registry: AgentRegistry,
    name: str,
    *,
    summary: str = "",
    entities: Optional[Dict[str, List[str]]] = None,
    days_ago: float = 0,
    status: str = STATUS_ACTIVE,
) -> None:
    """Insert one agent as if it was last used ``days_ago`` days ago."""

    stamp = to_iso(registry.now() - timedelta(days=days_ago))
    registry.import_records(
        [
            AgentRecord(
                name=name,
                summary=summary,
                entities=entities or {},
                status=status,
                created_at=stamp,
                last_active_at=stamp,
            )
        ]
    )


@pytest.fixture
def two_sarahs(registry) -> AgentRegistry:
    """A small roster with the classic trap: two different people called Sarah."""

    add_agent(
        registry,
        "Email to Sarah",
        summary="Task: reply to Sarah Chen about the Q3 invoice dispute | Latest result: draft sent",
        entities={"email": ["sarah.chen@acme.com"], "person": ["Sarah Chen"], "subject": ["Q3 invoice"]},
        days_ago=3,
    )
    add_agent(
        registry,
        "Sarah venue contract",
        summary="Task: negotiate the venue contract with Sarah Patel for the offsite",
        entities={"email": ["sarah.patel@venuehub.io"], "person": ["Sarah Patel"], "thread": ["18c2f0a9b1d2e3f4"]},
        days_ago=1,
    )
    add_agent(
        registry,
        "Vercel Job Offer",
        summary="Task: track the Vercel job offer and reply to the recruiter",
        entities={"email": ["recruiting@vercel.com"]},
        days_ago=10,
    )
    add_agent(
        registry,
        "Reminder: renew car insurance",
        summary="Task: remind the user to renew car insurance on Nov 15",
        days_ago=20,
    )
    return registry
