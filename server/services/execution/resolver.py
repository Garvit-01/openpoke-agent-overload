"""Decide which agent a send_message_to_agent call should really go to.

The original code created a new agent whenever the requested name was not an exact
string match, so "Email to Sarah Chen" and "Sarah Chen thread" became two agents
with split context. This module stops that, but only merges on unambiguous evidence:

1. exact name                       -> reuse
2. same Gmail thread id             -> reuse that thread's agent
3. same email address               -> reuse that contact's agent (keywords pick among several)
4. same normalised name             -> reuse ("Email to Sarah Chen" == "Sarah Chen thread")
5. otherwise                        -> create, and report look-alikes as possible duplicates

Fuzzy keyword similarity alone never merges. It is only reported back to the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .entities import NAME_FILLER, extract_entities, tokenize
from .registry import AgentRegistry
from .retrieval import MAX_AGENTS_PER_HARD_ENTITY, AgentRetriever, agent_document_tokens

# An existing agent is reported as a possible duplicate when it shares at least this share of
# the requested name's meaningful words. A ratio (not a BM25 score) so it means the same thing
# with 5 agents or 5,000.
POSSIBLE_DUPLICATE_MIN_OVERLAP = 0.5


@dataclass
class Resolution:
    """Routing decision for one send_message_to_agent call."""

    agent_name: str
    created: bool
    reason: str
    requested_name: str
    possible_duplicates: List[str] = field(default_factory=list)

    @property
    def redirected(self) -> bool:
        return not self.created and self.agent_name != self.requested_name


def resolve_agent(
    requested_name: str,
    instructions: str,
    registry: AgentRegistry,
    retriever: Optional[AgentRetriever] = None,
) -> Resolution:
    """Pick the agent to use. Pure decision: the caller creates or touches the agent."""

    requested_name = (requested_name or "").strip() or "agent"
    retriever = retriever or AgentRetriever(registry)
    text = f"{requested_name}\n{instructions or ''}"

    if registry.exists(requested_name):
        return Resolution(requested_name, created=False, reason="exact_name", requested_name=requested_name)

    owner, reason, mentions_new_contact = _owner_by_entity(text, registry, retriever)
    if owner:
        return Resolution(owner, created=False, reason=reason, requested_name=requested_name)

    same_key = registry.find_by_name_key(requested_name)
    # A brand-new email address in the instructions means a new conversation, even if the name looks familiar
    if len(same_key) == 1 and not mentions_new_contact:
        return Resolution(same_key[0], created=False, reason="same_name_key", requested_name=requested_name)

    duplicates = list(same_key)
    name_terms = {term for term in tokenize(requested_name) if term not in NAME_FILLER}
    if name_terms:
        for candidate in retriever.search(text, limit=5):
            shared = name_terms & set(agent_document_tokens(candidate.record))
            if len(shared) / len(name_terms) >= POSSIBLE_DUPLICATE_MIN_OVERLAP and candidate.name not in duplicates:
                duplicates.append(candidate.name)

    return Resolution(
        requested_name,
        created=True,
        reason="new",
        requested_name=requested_name,
        possible_duplicates=duplicates[:3],
    )


def _owner_by_entity(
    text: str,
    registry: AgentRegistry,
    retriever: AgentRetriever,
) -> Tuple[Optional[str], str, bool]:
    """Find the agent that owns a thread id or email in the text.

    Returns (owner, reason, mentions_new_contact).
    """

    entities = extract_entities(text)

    # A thread id is the strongest signal there is
    for thread_id in entities.get("thread", []):
        owners = registry.find_by_entity("thread", thread_id)
        if owners:
            return owners[0], f"thread: {thread_id}", False

    emails = entities.get("email", [])
    owned: List[Tuple[str, List[str]]] = []
    mentions_new_contact = False
    for email in emails:
        owners = registry.find_by_entity("email", email)
        if not owners:
            mentions_new_contact = True
        elif len(owners) <= MAX_AGENTS_PER_HARD_ENTITY:
            owned.append((email, owners))

    # If the text also names a contact we have never dealt with, do not fold it into an old thread
    if mentions_new_contact or not owned:
        return None, "", mentions_new_contact

    email, owners = owned[0]
    if len(owners) == 1:
        return owners[0], f"email: {email}", False

    # Several agents share this contact: keywords decide among them, recency breaks ties
    ranked = [item.name for item in retriever.search(text, limit=50) if item.name in owners]
    chosen = ranked[0] if ranked else owners[0]
    return chosen, f"email: {email} (best of {len(owners)})", False


__all__ = ["POSSIBLE_DUPLICATE_MIN_OVERLAP", "Resolution", "resolve_agent"]
