"""The original OpenPoke routing behaviour, copied so it can be measured next to the new one.

Source: upstream commit 5b5f635 (shlokkhemani/openpoke)
- server/agents/interaction_agent/agent.py  _render_active_agents(): every name, every turn
- server/agents/interaction_agent/tools.py  send_message_to_agent(): reuse only on an exact name match
"""

from __future__ import annotations

import copy
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Sequence

ORIGINAL_SYSTEM_PROMPT = (Path(__file__).parent / "baseline_assets" / "interaction_system_prompt.md").read_text(
    encoding="utf-8"
).strip()

ORIGINAL_SEND_DESCRIPTION = (
    "Deliver instructions to a specific execution agent. Creates a new agent if the name doesn't exist "
    "in the roster, or reuses an existing one."
)


def render_original_roster(names: Sequence[str]) -> str:
    """Exactly what the original prompt contained: one tag per agent ever created."""
    if not names:
        body = "None"
    else:
        body = "\n".join(f'<agent name="{escape(name or "agent", quote=True)}" />' for name in names)
    return f"<active_agents>\n{body}\n</active_agents>"


def original_reuse(requested_name: str, existing: set) -> bool:
    """Original rule: reuse if and only if the exact string is already in the roster."""
    return requested_name in existing


def original_tool_schemas(current: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The tool list the original interaction agent had (no search_agents, original wording)."""
    schemas = [copy.deepcopy(tool) for tool in current if tool["function"]["name"] != "search_agents"]
    for tool in schemas:
        if tool["function"]["name"] == "send_message_to_agent":
            tool["function"]["description"] = ORIGINAL_SEND_DESCRIPTION
    return schemas


__all__ = ["ORIGINAL_SYSTEM_PROMPT", "original_reuse", "original_tool_schemas", "render_original_roster"]
