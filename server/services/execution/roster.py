"""Agent roster access: the process-wide AgentRegistry plus migration from roster.json.

The original roster was a JSON list of names guarded by ``fcntl`` (Unix only, so the
server could not even import on Windows). It is replaced by the SQLite registry. The
``AgentRoster`` class keeps its old methods so existing call sites still work.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import List, Optional

from ...logging_config import logger
from .digest import build_digest
from .registry import (
    REQUEST_HISTORY_LIMIT,
    REQUESTS_BACKFILLED_KEY,
    AgentRecord,
    AgentRegistry,
    clip_request,
    to_iso,
)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_EXECUTION_DIR = _DATA_DIR / "execution_agents"
_LEGACY_ROSTER_PATH = _EXECUTION_DIR / "roster.json"
_REGISTRY_PATH = _EXECUTION_DIR / "registry.db"


# Read the old roster.json format: a plain JSON list of agent names
def load_legacy_roster(roster_path: Path) -> List[str]:
    if not roster_path.exists():
        return []
    try:
        data = json.loads(roster_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"Failed to read legacy roster.json: {exc}")
        return []
    if not isinstance(data, list):
        return []
    return [str(name) for name in data if str(name).strip()]


# Import every name from roster.json, building summaries from each agent's existing log
def migrate_legacy_roster(registry: AgentRegistry, roster_path: Path, log_store=None) -> int:
    """One-time import. The old file is renamed to roster.json.migrated, never deleted."""

    names = load_legacy_roster(roster_path)
    if not names:
        return 0

    # The old roster has no timestamps, so migrated agents start their idle clock now
    stamp = to_iso(registry.now())
    if log_store is not None:
        # Old logs are named by slug; move them to id-keyed files before reading them
        log_store.adopt_legacy_logs(names)
    records: List[AgentRecord] = []
    for name in names:
        summary, entities = "", {}
        if log_store is not None:
            summary, entities = build_digest(name, log_store.iter_entries(name))
        records.append(
            AgentRecord(name=name, summary=summary, entities=entities, created_at=stamp, last_active_at=stamp)
        )

    inserted = registry.import_records(records)
    try:
        roster_path.replace(roster_path.with_name(roster_path.name + ".migrated"))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Migrated roster.json but could not rename it: {exc}")
    logger.info("Migrated legacy roster", extra={"agents": inserted})
    return inserted


# Fill each agent's searchable request history from its log, once per registry (marked in registry_meta).
# Agents that already have stored requests are left alone, so a rerun can't duplicate anything.
def backfill_requests_from_logs(registry: AgentRegistry, log_store) -> int:
    """Returns how many agents got requests. Reads every agent's log once, the first time only."""

    if registry.get_meta(REQUESTS_BACKFILLED_KEY):
        return 0
    filled = 0
    for name in registry.names():
        if registry.requests_for(name):
            continue
        texts = [clip_request(payload) for tag, _stamp, payload in log_store.iter_entries(name) if tag == "agent_request"]
        # Same rule as add_request: a repeated request counts at its latest position
        newest = list(dict.fromkeys(reversed([text for text in texts if text])))[:REQUEST_HISTORY_LIMIT]
        stored = [registry.add_request(name, text) for text in reversed(newest)]
        filled += any(stored)
    registry.set_meta(REQUESTS_BACKFILLED_KEY, to_iso(registry.now()))
    if filled:
        logger.info("Backfilled past requests from agent logs", extra={"agents": filled})
    return filled


class AgentRoster:
    """Old roster interface, now a thin view over the registry."""

    def __init__(self, registry: AgentRegistry):
        self._registry = registry

    def load(self) -> None:
        """No-op: the registry reads from SQLite on every call."""

    def add_agent(self, agent_name: str) -> None:
        self._registry.ensure_agent(agent_name)

    def get_agents(self) -> List[str]:
        return self._registry.names()

    def clear(self) -> None:
        self._registry.clear()


_registry: Optional[AgentRegistry] = None
_registry_lock = threading.Lock()


# Lazily open the registry (and migrate roster.json once) so importing this module has no side effects
def get_agent_registry() -> AgentRegistry:
    global _registry
    with _registry_lock:
        if _registry is None:
            from .log_store import get_execution_agent_logs

            _registry = AgentRegistry(_REGISTRY_PATH)
            logs = get_execution_agent_logs()
            if _LEGACY_ROSTER_PATH.exists():
                migrate_legacy_roster(_registry, _LEGACY_ROSTER_PATH, logs)
            # Cheap when there is nothing to move: one exists() check per agent, once per process
            logs.adopt_legacy_logs(_registry.names())
            try:
                backfill_requests_from_logs(_registry, logs)
            except Exception as exc:  # pragma: no cover - search history must never stop the server
                logger.warning(f"Could not backfill past requests: {exc}")
        return _registry


# Swap the process-wide registry (tests and the evaluator use a temporary database)
def set_agent_registry(registry: Optional[AgentRegistry]) -> None:
    global _registry
    with _registry_lock:
        _registry = registry


def get_agent_roster() -> AgentRoster:
    """Get the roster view over the process-wide registry."""
    return AgentRoster(get_agent_registry())


__all__ = [
    "AgentRoster",
    "backfill_requests_from_logs",
    "get_agent_registry",
    "get_agent_roster",
    "load_legacy_roster",
    "migrate_legacy_roster",
    "set_agent_registry",
]
