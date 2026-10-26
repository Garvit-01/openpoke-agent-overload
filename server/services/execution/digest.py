"""Build an agent's searchable profile (summary + entities) from its execution log.

This is deterministic on purpose: it costs nothing, runs offline in tests, and never
invents facts. The summary is "what was it first asked to do" plus "what happened
last", trimmed. An LLM-written summary would read better; that is listed as a next step.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

from .entities import extract_entities, merge_entities

LogEntry = Tuple[str, str, str]  # (tag, timestamp, payload) as yielded by ExecutionAgentLogStore

# Raw tool responses (e.g. search results listing 20 senders) are left out on purpose:
# they would tag the agent with every address in the inbox and cause false hard matches
_ENTITY_TAGS = {"agent_request", "agent_action", "agent_response"}

SUMMARY_MAX_CHARS = 280
_PART_MAX_CHARS = 110


def _clip(text: str, limit: int) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 3].rstrip() + "..."


# Summarise an agent as its first request plus its latest request and result
def build_digest(agent_name: str, entries: Iterable[LogEntry]) -> Tuple[str, Dict[str, List[str]]]:
    requests: List[str] = []
    responses: List[str] = []
    entity_text: List[str] = [agent_name]

    for tag, _timestamp, payload in entries:
        if tag == "agent_request":
            requests.append(payload)
        elif tag == "agent_response":
            responses.append(payload)
        if tag in _ENTITY_TAGS:
            entity_text.append(payload)

    parts: List[str] = []
    if requests:
        parts.append(f"Task: {_clip(requests[0], _PART_MAX_CHARS)}")
    if len(requests) > 1:
        parts.append(f"Latest ask: {_clip(requests[-1], _PART_MAX_CHARS)}")
    if responses:
        parts.append(f"Latest result: {_clip(responses[-1], _PART_MAX_CHARS)}")

    summary = _clip(" | ".join(parts), SUMMARY_MAX_CHARS)
    entities = merge_entities(extract_entities("\n".join(entity_text)))
    return summary, entities


__all__ = ["SUMMARY_MAX_CHARS", "build_digest"]
