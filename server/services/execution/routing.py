"""Glue between the interaction agent and the agent registry.

- ``route_to_agent``: resolve the agent for a send_message_to_agent call, create or
  revive it, seed its profile, and log the decision.
- ``refresh_agent_profile``: rebuild an agent's summary/entities after it runs.
- ``maybe_apply_lifecycle``: demote idle agents, at most once per interval.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

from ...logging_config import logger
from .digest import SUMMARY_MAX_CHARS, build_digest
from .entities import extract_entities
from .ids import agent_id_for
from .lifecycle import LifecyclePolicy, LifecycleReport, apply_lifecycle, trigger_service_counter
from .log_store import get_execution_agent_logs
from .registry import AgentRegistry, to_iso
from .resolver import Resolution, resolve_agent
from .retrieval import AgentRetriever
from .roster import _EXECUTION_DIR, get_agent_registry

_ROUTING_LOG_PATH = _EXECUTION_DIR / "routing_decisions.jsonl"
_routing_log_lock = threading.Lock()


# Append one routing decision as a JSON line (what was asked, what was chosen, and why)
def record_routing_decision(entry: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = path or _ROUTING_LOG_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with _routing_log_lock, target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # pragma: no cover - logging must never break routing
        logger.warning(f"Failed to write routing decision: {exc}")


# Resolve the target agent, create or revive it, and seed a new agent's profile from its first ask
def route_to_agent(
    requested_name: str,
    instructions: str,
    *,
    registry: Optional[AgentRegistry] = None,
    routing_log_path: Optional[Path] = None,
    new_task: bool = False,
) -> Resolution:
    registry = registry or get_agent_registry()
    resolution = resolve_agent(
        requested_name, instructions, registry, AgentRetriever(registry), new_task=new_task
    )
    record, _created = registry.ensure_agent(resolution.agent_name)

    if resolution.created:
        registry.update_profile(
            record.name,
            summary=f"Task: {' '.join(instructions.split())[:SUMMARY_MAX_CHARS - 6]}",
            entities=extract_entities(f"{record.name}\n{instructions}"),
        )
    # Searchable right away, before the run finishes (the summary only updates after it)
    record_agent_request(record.name, instructions, registry=registry)

    record_routing_decision(
        {
            "at": to_iso(registry.now()),
            "requested_name": resolution.requested_name,
            "agent_name": resolution.agent_name,
            "agent_id": agent_id_for(resolution.agent_name),
            "created": resolution.created,
            "new_task": new_task,
            "reason": resolution.reason,
            "possible_duplicates": resolution.possible_duplicates,
            "instructions_preview": " ".join(instructions.split())[:200],
        },
        routing_log_path,
    )
    if resolution.redirected:
        logger.info(
            f"Routed '{resolution.requested_name}' to existing agent '{resolution.agent_name}'",
            extra={"reason": resolution.reason},
        )
    return resolution


# Keep a request in the agent's searchable history (chat turns via route_to_agent, triggers directly).
# Stored even when indexing is off, so turning it on later has the history.
def record_agent_request(agent_name: str, text: str, *, registry: Optional[AgentRegistry] = None) -> bool:
    try:
        return (registry or get_agent_registry()).add_request(agent_name, text)
    except Exception as exc:  # pragma: no cover - search history must never break routing
        logger.warning(f"Failed to store request for {agent_name}: {exc}")
        return False


# Rebuild an agent's searchable summary and entities from its full log after a run
def refresh_agent_profile(agent_name: str, *, registry: Optional[AgentRegistry] = None, log_store=None) -> None:
    registry = registry or get_agent_registry()
    log_store = log_store or get_execution_agent_logs()
    try:
        summary, entities = build_digest(agent_name, log_store.iter_entries(agent_name))
        registry.ensure_agent(agent_name)
        registry.update_profile(agent_name, summary=summary or None, entities=entities, count_run=True)
    except Exception as exc:  # pragma: no cover - profile refresh must never fail an execution
        logger.warning(f"Failed to refresh agent profile for {agent_name}: {exc}")


_lifecycle_lock = threading.Lock()
_last_lifecycle_run = 0.0


# Run the lifecycle pass at most once per interval; called from the prompt builder
def maybe_apply_lifecycle(
    *,
    registry: Optional[AgentRegistry] = None,
    policy: Optional[LifecyclePolicy] = None,
    interval_seconds: float = 3600.0,
) -> Optional[LifecycleReport]:
    global _last_lifecycle_run
    with _lifecycle_lock:
        now = time.monotonic()
        if _last_lifecycle_run and now - _last_lifecycle_run < interval_seconds:
            return None
        _last_lifecycle_run = now
    try:
        return apply_lifecycle(
            registry or get_agent_registry(),
            active_trigger_count=trigger_service_counter(),
            policy=policy,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Agent lifecycle pass failed: {exc}")
        return None


def resolution_payload(resolution: Resolution) -> Dict[str, Any]:
    """Serialisable form of a routing decision for tool results."""
    return asdict(resolution)


__all__ = [
    "maybe_apply_lifecycle",
    "record_agent_request",
    "record_routing_decision",
    "refresh_agent_profile",
    "resolution_payload",
    "route_to_agent",
]
