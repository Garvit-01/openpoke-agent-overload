"""Search phrases: other words a user might use for an agent's task.

Keyword search can't link "the policy on the house" to "Home Insurance Renewal". Research on
tool retrieval gets large gains by having a model write example requests for each item and
indexing them alongside it (Re-Invoke, EMNLP 2024; Toolshed, ICAART 2025). Here a model
writes about 10 short phrases per agent, once, after the agent's first run, off the reply path.

They are stored as "phrase" entities, which the BM25 index already reads. They only help an
agent get found: only email addresses and thread ids are hard matches, so a phrase can never
merge two agents. The model sees the agent's own name and summary, nothing else.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ...logging_config import logger
from .registry import AgentRegistry

PHRASE_KIND = "phrase"
MAX_PHRASES = 12
MAX_WORDS = 8

Complete = Callable[..., Awaitable[Dict[str, Any]]]

PROMPT = """You index tasks for a personal assistant so they can be found again later.
The user may come back to this task in their own words, without using any words from its name or summary.

Write {count} short phrases (2 to 6 words each) the user might use for it: synonyms, everyday wording,
related things and the broader category. Avoid repeating words from the task name. One phrase per line,
no numbering, nothing else.

Task name: {name}
What it is about: {summary}"""


def parse_phrases(text: str, limit: int = MAX_PHRASES) -> List[str]:
    """One phrase per line; drop numbering, bullets and quotes; keep short, distinct phrases."""
    phrases: List[str] = []
    for line in (text or "").splitlines():
        phrase = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip().strip("\"'").strip().lower()
        if not phrase or len(phrase.split()) > MAX_WORDS or phrase in phrases:
            continue
        phrases.append(phrase)
        if len(phrases) >= limit:
            break
    return phrases


async def generate_phrases(
    name: str,
    summary: str,
    *,
    complete: Complete,
    model: str,
    api_key: Optional[str] = None,
    count: int = 10,
) -> List[str]:
    response = await complete(
        model=model,
        messages=[{"role": "user", "content": PROMPT.format(count=count, name=name, summary=summary or name)}],
        system=None,
        api_key=api_key,
        tools=None,
    )
    message = ((response.get("choices") or [{}])[0]).get("message") or {}
    return parse_phrases(message.get("content") or "")


def has_phrases(registry: AgentRegistry, name: str) -> bool:
    record = registry.get(name)
    return bool(record and record.entities.get(PHRASE_KIND))


# Called after an agent's run; does nothing if the agent already has phrases or the feature is off
async def maybe_add_phrases(
    agent_name: str,
    *,
    registry: Optional[AgentRegistry] = None,
    complete: Optional[Complete] = None,
) -> int:
    from ...config import get_settings

    settings = get_settings()
    if not settings.agent_search_phrases or not settings.openrouter_api_key:
        return 0
    if registry is None:
        from .roster import get_agent_registry

        registry = get_agent_registry()
    record = registry.get(agent_name)
    if record is None or has_phrases(registry, agent_name):
        return 0
    if complete is None:
        from ...openrouter_client import request_chat_completion as complete
    try:
        phrases = await generate_phrases(
            record.name, record.summary, complete=complete,
            model=settings.agent_phrases_model, api_key=settings.openrouter_api_key,
        )
    except Exception as exc:  # phrases are a nice-to-have; never fail a run over them
        logger.warning(f"Failed to write search phrases for {agent_name}: {exc}")
        return 0
    if phrases:
        registry.update_profile(agent_name, entities={PHRASE_KIND: phrases})
    return len(phrases)


__all__ = ["MAX_PHRASES", "PHRASE_KIND", "PROMPT", "generate_phrases", "has_phrases", "maybe_add_phrases", "parse_phrases"]
