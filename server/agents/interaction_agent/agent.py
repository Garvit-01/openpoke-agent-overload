"""Interaction agent helpers for prompt construction."""

from html import escape
from pathlib import Path
from typing import Dict, List, Sequence

from ...config import get_settings
from ...services.execution import AgentRetriever, ScoredAgent, get_agent_registry
from ...services.execution.lifecycle import LifecyclePolicy
from ...services.execution.routing import maybe_apply_lifecycle

_prompt_path = Path(__file__).parent / "system_prompt.md"
SYSTEM_PROMPT = _prompt_path.read_text(encoding="utf-8").strip()


# Load and return the pre-defined system prompt from markdown file
def build_system_prompt() -> str:
    """Return the static system prompt for the interaction agent."""
    return SYSTEM_PROMPT


# Build structured message with conversation history, active agents, and current turn
def prepare_message_with_history(
    latest_text: str,
    transcript: str,
    message_type: str = "user",
) -> List[Dict[str, str]]:
    """Compose a message that bundles history, roster, and the latest turn."""
    sections: List[str] = []

    sections.append(_render_conversation_history(transcript))
    sections.append(_render_active_agents(latest_text))
    sections.append(_render_current_turn(latest_text, message_type))

    content = "\n\n".join(sections)
    return [{"role": "user", "content": content}]


# Format conversation transcript into XML tags for LLM context
def _render_conversation_history(transcript: str) -> str:
    history = transcript.strip()
    if not history:
        history = "None"
    return f"<conversation_history>\n{history}\n</conversation_history>"


# Show a fixed-size shortlist of agents relevant to this turn instead of the whole roster
def _render_active_agents(latest_text: str) -> str:
    settings = get_settings()
    registry = get_agent_registry()
    maybe_apply_lifecycle(
        registry=registry,
        policy=LifecyclePolicy(
            dormant_after_days=settings.agent_dormant_after_days,
            archive_after_days=settings.agent_archive_after_days,
        ),
    )

    total = registry.count()
    if not total:
        return render_agent_shortlist([], total=0)

    shortlist = AgentRetriever(registry).shortlist(
        latest_text,
        retrieved=settings.agent_shortlist_retrieved,
        recent=settings.agent_shortlist_recent,
    )
    return render_agent_shortlist(shortlist, total=total, summary_chars=settings.agent_summary_chars)


# Render shortlisted agents with status, last use, why they matched, and a short summary
def render_agent_shortlist(
    shortlist: Sequence[ScoredAgent],
    *,
    total: int,
    summary_chars: int = 200,
) -> str:
    if not shortlist:
        body = "None"
    else:
        rendered: List[str] = []
        for item in shortlist:
            record = item.record
            summary = " ".join(record.summary.split())
            if len(summary) > summary_chars:
                summary = summary[: summary_chars - 3].rstrip() + "..."
            attributes = [
                f'name="{escape(record.name or "agent", quote=True)}"',
                f'status="{record.status}"',
                f'last_active="{record.last_active_at[:10]}"',
            ]
            if item.reasons:
                attributes.append(f'matched="{escape("; ".join(item.reasons), quote=True)}"')
            rendered.append(f"<agent {' '.join(attributes)}>{escape(summary, quote=False)}</agent>")
        body = "\n".join(rendered)
    return f'<active_agents total="{total}" shown="{len(shortlist)}">\n{body}\n</active_agents>'


# Wrap the current message in appropriate XML tags based on sender type
def _render_current_turn(latest_text: str, message_type: str) -> str:
    tag = "new_agent_message" if message_type == "agent" else "new_user_message"
    body = latest_text.strip()
    return f"<{tag}>\n{body}\n</{tag}>"
