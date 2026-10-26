"""Stable agent ids.

The original log store named each agent's file after a slug of its name, so "Email to
Sarah", "email to sarah" and "Email-to-Sarah!" all wrote to email-to-sarah.log and read
each other's history. The id here comes from the exact name, so two different names never
share a file, and the same name always maps to the same id across restarts without a lookup.
"""

from __future__ import annotations

import hashlib

ID_PREFIX = "agt_"
_ID_HEX_CHARS = 16  # 64 bits: collisions are not a practical concern at thousands of agents


def agent_id_for(agent_name: str) -> str:
    """Return the stable id for an exact agent name (case and punctuation matter)."""

    digest = hashlib.sha256(agent_name.encode("utf-8")).hexdigest()
    return f"{ID_PREFIX}{digest[:_ID_HEX_CHARS]}"


__all__ = ["ID_PREFIX", "agent_id_for"]
