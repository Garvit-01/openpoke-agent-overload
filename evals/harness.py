"""Run the original routing and the new routing on the same scenario and score both.

Two arms, one router.

The router stands in for the interaction agent's choice ("which agent is this about?").
By default it is SimulatedRouter: deterministic, free, offline. It applies the same rules
in both arms. What differs between arms is only what the system gives it:

- baseline: every agent name, nothing else; reuse only on an exact name match (original code)
- solution: a 12-agent shortlist with summaries; search_agents when nothing fits; the
  resolver's dedup when a new name is proposed (new code, imported, not re-implemented)

So the comparison measures the effect of the code change, not of a smarter model. The
optional LLM router (evals/llm.py) repeats the comparison with a real model.

Multi-turn conversations run the same way, except that state carries over: agents created
in turn 1 exist in turn 2, and a wrong pick in turn 1 makes "ok, send it" go to the wrong
agent too. The solution arm uses the real route_to_agent, so new agents get their profile.
"""

from __future__ import annotations

import shutil
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from server.agents.interaction_agent.agent import agent_topic, render_agent_shortlist
from server.services.execution.entities import EMAIL_PATTERN, extract_entities, tokenize
from server.services.execution.lifecycle import LifecyclePolicy, apply_lifecycle
from server.services.execution.phrases import PHRASE_KIND
from server.services.execution.registry import AgentRecord, AgentRegistry, to_iso
from server.services.execution.resolver import resolve_agent
from server.services.execution.retrieval import AgentRetriever, ScoredAgent
from server.services.execution.routing import route_to_agent

from .baseline import original_reuse, render_original_roster
from .scenarios import (
    COMMON_FIRST,
    COMPANIES,
    LAST_NAMES,
    NEW,
    NEW_LAST_NAMES,
    NEW_TOPICS,
    OTHER_FIRST,
    TOPICS,
    Case,
    Conversation,
    Scenario,
    Turn,
)

SHORTLIST_RETRIEVED = 8
SHORTLIST_RECENT = 4
NAMED_PEOPLE_LIMIT = 12
SUMMARY_CHARS = 200
CHARS_PER_TOKEN = 4  # rough English average; good enough to compare two prompts


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


# --------------------------------------------------------------------------- router


class SimulatedRouter:
    """Deterministic stand-in for the model picking an agent from what it can see.

    It tries to behave like a reasonable model, not a strawman, and applies the same
    rules in both arms. In priority order:

    1. Email: if the message has an address that is visible for an agent, use that agent.
    2. Person: if the message names someone (first name, maybe surname), the candidates are
       visible agents that show that name. An agent that shows the same first name with a
       different surname is not a candidate ("Sarah Achebe" is not "Sarah Murphy").
       One candidate: use it. Several: the one showing most of the other key terms wins,
       ties go to the one listed first. (With bare names that is often a coin flip.)
    3. No person named: coverage = share of key terms (people, companies, topics, tasks)
       visible for an agent. Use the best agent if coverage >= threshold.
    4. Otherwise propose a new agent name the way a model would ("Email to Sarah Achebe").

    In a conversation, a turn that names nobody and nothing ("ok, send it", "go back to the
    first one") is resolved from the conversation (refer_back). Other turns check the agents
    already used in the conversation before the roster, as a model would. A turn that asks
    for something new ("start a new email to ...") gets a new agent named after its topic.
    """

    def __init__(self, vocabulary: Set[str], threshold: float = 0.6):
        self.vocabulary = vocabulary
        self.threshold = threshold
        self.first_names = set(tokenize(" ".join([*COMMON_FIRST, *OTHER_FIRST])))
        self.surnames = set(tokenize(" ".join([*LAST_NAMES, *NEW_LAST_NAMES])))
        self.subject_words = set(tokenize(" ".join([*COMPANIES, *TOPICS, *NEW_TOPICS])))

    def key_terms(self, message: str) -> List[str]:
        return list(dict.fromkeys(term for term in tokenize(message) if term in self.vocabulary))

    @staticmethod
    def asks_for_new(message: str) -> bool:
        lowered = message.lower()
        return any(cue in lowered for cue in ("a new email", "new thread", "separate email"))

    def refer_back(self, message: str, memory: Sequence[str]) -> Optional[str]:
        """Resolve "send it" / "the first one" from the agents used earlier in this conversation.

        Only for turns that name no person, company, topic or email; task words such as
        "send" do not count as naming something.
        """

        if not memory or EMAIL_PATTERN.search(message):
            return None
        names = self.first_names | self.surnames | self.subject_words
        if any(term in names for term in tokenize(message)):
            return None
        lowered = message.lower()
        if "first one" in lowered:
            return memory[0]
        if "other one" in lowered:
            earlier = [name for name in memory if name != memory[-1]]
            return earlier[-1] if earlier else memory[-1]
        return memory[-1]

    def choose(self, message: str, visible: Sequence[Tuple[str, str]]) -> Optional[str]:
        key = self.key_terms(message)
        emails = [email.lower() for email in EMAIL_PATTERN.findall(message)]
        for name, text in visible:
            if emails and any(email in text.lower() for email in emails):
                return name

        firsts = set(key) & self.first_names
        lasts = set(key) & self.surnames
        if firsts:
            return self._choose_by_person(key, firsts, lasts, visible)
        return self._choose_by_coverage(key, visible)

    def _choose_by_person(self, key, firsts, lasts, visible) -> Optional[str]:
        others = set(key) - firsts - lasts
        best_name, best_matched = None, -1
        for name, text in visible:
            tokens = set(tokenize(text))
            if not firsts <= tokens:
                continue
            if lasts and not lasts <= tokens and tokens & self.surnames:
                continue  # same first name, different person
            if lasts and not lasts <= tokens and not (others & tokens):
                continue  # bare first name and nothing else in common: too weak
            matched = len(others & tokens) + len(lasts & tokens)
            if matched > best_matched:
                best_name, best_matched = name, matched
        return best_name

    def _choose_by_coverage(self, key, visible) -> Optional[str]:
        if not key:
            return None
        best_name, best = None, (0.0, 0)
        for name, text in visible:
            matched = len(set(key) & set(tokenize(text)))
            score = (matched / len(key), matched)
            if score > best:
                best_name, best = name, score
        return best_name if best[0] >= self.threshold else None

    def new_agent_name(self, message: str) -> str:
        key = self.key_terms(message)
        if "remind" in message.lower():
            return "Reminder: " + " ".join(key or tokenize(message)[:4])
        people = [term.title() for term in key if term in self.first_names | self.surnames]
        if people and self.asks_for_new(message):
            subject = [term for term in key if term in self.subject_words]
            return "Email to " + " ".join(people[:2]) + (" re " + " ".join(subject) if subject else "")
        if people:
            return "Email to " + " ".join(people[:2])
        return "Task: " + " ".join((key or tokenize(message))[:4])


# ----------------------------------------------------------------------------- arms


@dataclass
class Decision:
    case_id: str
    category: str
    gold: str
    chosen: str  # agent name, or NEW if a new agent was created
    reason: str
    roster_tokens: int
    latency_ms: float = 0.0
    in_shortlist: Optional[bool] = None
    in_search: Optional[bool] = None
    used_search: bool = False

    @property
    def correct(self) -> bool:
        return self.chosen == self.gold


class BaselineArm:
    label = "baseline"

    def __init__(self, scenario: Scenario):
        self.names = [agent.name for agent in scenario.agents]  # creation order, like roster.json
        self.existing = set(self.names)
        self.roster = render_original_roster(self.names)

    def visible(self) -> List[Tuple[str, str]]:
        return [(name, name) for name in self.names]

    def apply(self, case: Case, requested_name: str) -> Tuple[str, str]:
        if original_reuse(requested_name, self.existing):
            return requested_name, "exact_name"
        return NEW, "new"

    def route(self, case: Case, router: SimulatedRouter) -> Decision:
        started = time.perf_counter()
        choice = router.choose(case.message, self.visible())
        chosen, reason = self.apply(case, choice or router.new_agent_name(case.message))
        return Decision(
            case_id=case.id, category=case.category, gold=case.gold, chosen=chosen, reason=reason,
            roster_tokens=estimate_tokens(self.roster),
            latency_ms=(time.perf_counter() - started) * 1000,
        )


def build_registry(scenario: Scenario, path: Path) -> AgentRegistry:
    """Load the synthetic history into a real AgentRegistry and run the real lifecycle pass."""

    registry = AgentRegistry(path, clock=lambda: scenario.now)
    registry.import_records(
        AgentRecord(
            name=agent.name,
            summary=agent.summary,
            entities=agent.entities,
            created_at=to_iso(scenario.now - timedelta(days=agent.created_days_ago)),
            last_active_at=to_iso(scenario.now - timedelta(days=agent.active_days_ago)),
        )
        for agent in scenario.agents
    )
    triggers = {agent.name for agent in scenario.agents if agent.has_trigger}
    apply_lifecycle(
        registry,
        active_trigger_count=lambda name: 1 if name in triggers else 0,
        policy=LifecyclePolicy(),
    )
    return registry


def visible_text(item: ScoredAgent) -> str:
    """What the solution prompt shows for one agent (name, summary, why it matched)."""
    return " ".join([item.name, item.record.summary, *item.reasons])


class SolutionArm:
    label = "solution"

    def __init__(self, scenario: Scenario, workdir: Path, phrases: Optional[Dict[str, List[str]]] = None):
        self.registry = build_registry(scenario, workdir / "registry.db")
        # Search phrases as the app would have written them after each agent's first run
        for name, values in (phrases or {}).items():
            if values and self.registry.get(name):
                self.registry.update_profile(name, entities={PHRASE_KIND: values})
        self.retriever = AgentRetriever(self.registry)
        self.total = self.registry.count()

    def shortlist(self, message: str) -> List[ScoredAgent]:
        return self.retriever.shortlist(message, retrieved=SHORTLIST_RETRIEVED, recent=SHORTLIST_RECENT)

    def related(self, message: str, shortlist: Sequence[ScoredAgent]) -> List[AgentRecord]:
        exclude = [item.name for item in shortlist]
        return self.retriever.named_people_agents(message, exclude=exclude, limit=NAMED_PEOPLE_LIMIT)

    def roster_section(self, message: str) -> str:
        shortlist = self.shortlist(message)
        return render_agent_shortlist(
            shortlist, total=self.total, summary_chars=SUMMARY_CHARS, related=self.related(message, shortlist)
        )

    def search(self, query: str) -> List[ScoredAgent]:
        return self.retriever.search(query, limit=8)

    def apply(self, case: Case, requested_name: str, instructions: str, *, new_task: bool = False) -> Tuple[str, str]:
        resolution = resolve_agent(requested_name, instructions, self.registry, self.retriever, new_task=new_task)
        return (NEW if resolution.created else resolution.agent_name), resolution.reason

    def route(self, case: Case, router: SimulatedRouter) -> Decision:
        started = time.perf_counter()
        shortlist = self.shortlist(case.message)
        related = self.related(case.message, shortlist)
        roster = render_agent_shortlist(shortlist, total=self.total, summary_chars=SUMMARY_CHARS, related=related)
        visible = [(item.name, visible_text(item)) for item in shortlist]
        visible += [(record.name, f"{record.name} {agent_topic(record)}") for record in related]
        choice = router.choose(case.message, visible)
        used_search = False
        if choice is None:
            # Nothing on the shortlist fits: the prompt tells the model to call search_agents
            used_search = True
            choice = router.choose(case.message, [(item.name, visible_text(item)) for item in self.search(case.message)])
        chosen, reason = self.apply(case, choice or router.new_agent_name(case.message), case.message)
        latency_ms = (time.perf_counter() - started) * 1000

        in_shortlist = in_search = None
        if case.gold != NEW:
            # In the prompt at all: the detailed shortlist or the named-people list
            in_shortlist = case.gold in {item.name for item in shortlist} | {record.name for record in related}
            in_search = case.gold in {item.name for item in self.search(case.message)}
        return Decision(
            case_id=case.id, category=case.category, gold=case.gold, chosen=chosen, reason=reason,
            roster_tokens=estimate_tokens(roster), latency_ms=latency_ms,
            in_shortlist=in_shortlist, in_search=in_search, used_search=used_search,
        )


# -------------------------------------------------------------------- conversations


@dataclass
class TurnDecision:
    conversation_id: str
    kind: str
    turn: int
    message: str
    expected: str  # agent name, "new agent", or "agent created in turn N"
    chosen: str  # the agent the turn went to
    created: bool
    correct: bool
    reason: str


def _turn_correct(turn: Turn, chosen: str, created: bool, created_at: Dict[int, str]) -> bool:
    if turn.gold_from_turn is not None:
        target = created_at.get(turn.gold_from_turn)
        return target is not None and chosen == target and not created
    if turn.gold == NEW:
        return created
    return chosen == turn.gold and not created


def _expected_label(turn: Turn) -> str:
    if turn.gold_from_turn is not None:
        return f"agent created in turn {turn.gold_from_turn}"
    return "new agent" if turn.gold == NEW else turn.gold


def run_baseline_conversation(scenario: Scenario, conversation: Conversation, router: SimulatedRouter) -> List[TurnDecision]:
    """Original behaviour over several turns: every name visible, exact-name reuse, new names persist."""

    names = [agent.name for agent in scenario.agents]
    existing = set(names)
    memory: List[str] = []
    created_at: Dict[int, str] = {}
    decisions: List[TurnDecision] = []
    for number, turn in enumerate(conversation.turns, start=1):
        referred = router.refer_back(turn.message, memory)
        choice = None if router.asks_for_new(turn.message) else (
            referred
            or router.choose(turn.message, [(name, name) for name in dict.fromkeys(memory)])
            or router.choose(turn.message, [(name, name) for name in names])
        )
        requested = choice or router.new_agent_name(turn.message)
        created = not original_reuse(requested, existing)
        if created:
            names.append(requested)
            existing.add(requested)
            created_at[number] = requested
        memory.append(requested)
        decisions.append(TurnDecision(
            conversation_id=conversation.id, kind=conversation.kind, turn=number, message=turn.message,
            expected=_expected_label(turn), chosen=requested, created=created,
            correct=_turn_correct(turn, requested, created, created_at),
            reason="conversation" if referred else ("new" if created else "exact_name"),
        ))
    return decisions


def run_solution_conversation(
    scenario: Scenario,
    conversation: Conversation,
    router: SimulatedRouter,
    template_db: Path,
    workdir: Path,
    *,
    use_new_task: bool = True,
) -> List[TurnDecision]:
    """New behaviour over several turns, through the real route_to_agent on a copy of the registry.

    use_new_task=False plays a model that never sets the new_task flag, to show what the
    resolver alone does with "a new email to a known contact".
    """

    db_path = workdir / f"{conversation.id}{'' if use_new_task else '-noflag'}.db"
    shutil.copyfile(template_db, db_path)
    registry = AgentRegistry(db_path, clock=lambda: scenario.now)
    retriever = AgentRetriever(registry)
    memory: List[str] = []
    created_at: Dict[int, str] = {}
    decisions: List[TurnDecision] = []
    for number, turn in enumerate(conversation.turns, start=1):
        referred = router.refer_back(turn.message, memory)
        new_task = router.asks_for_new(turn.message)
        choice = referred
        if choice is None and memory and not new_task:
            # Earlier tool results gave the model these agents' names and summaries
            seen = [registry.get(name) for name in dict.fromkeys(memory)]
            choice = router.choose(turn.message, [(r.name, f"{r.name} {r.summary}") for r in seen if r])
        if choice is None and not new_task:
            shortlist = retriever.shortlist(turn.message, retrieved=SHORTLIST_RETRIEVED, recent=SHORTLIST_RECENT)
            visible = [(item.name, visible_text(item)) for item in shortlist]
            related = retriever.named_people_agents(
                turn.message, exclude=[item.name for item in shortlist], limit=NAMED_PEOPLE_LIMIT
            )
            visible += [(record.name, f"{record.name} {agent_topic(record)}") for record in related]
            choice = router.choose(turn.message, visible)
        if choice is None and not new_task:
            found = retriever.search(turn.message, limit=8)
            choice = router.choose(turn.message, [(item.name, visible_text(item)) for item in found])
        resolution = route_to_agent(
            choice or router.new_agent_name(turn.message),
            turn.message,
            registry=registry,
            routing_log_path=workdir / "routing.jsonl",
            new_task=new_task and use_new_task,
        )
        chosen, created = resolution.agent_name, resolution.created
        if created:
            created_at[number] = chosen
        # The tool result names the agent actually used, so that is what the model remembers
        memory.append(chosen)
        decisions.append(TurnDecision(
            conversation_id=conversation.id, kind=conversation.kind, turn=number, message=turn.message,
            expected=_expected_label(turn), chosen=chosen, created=created,
            correct=_turn_correct(turn, chosen, created, created_at),
            reason="conversation" if referred else resolution.reason,
        ))
    return decisions


@dataclass
class ConversationMetrics:
    arm: str
    conversations: int
    turns: int
    turn_accuracy: float
    later_turn_accuracy: float  # turns 2+, where the conversation matters
    conversation_success: float  # every turn right
    by_kind: Dict[str, Dict[str, float]] = field(default_factory=dict)


def score_conversations(arm: str, decisions: Sequence[TurnDecision]) -> ConversationMetrics:
    grouped: Dict[str, List[TurnDecision]] = {}
    for decision in decisions:
        grouped.setdefault(decision.conversation_id, []).append(decision)
    kinds: Dict[str, List[List[TurnDecision]]] = {}
    for turns in grouped.values():
        kinds.setdefault(turns[0].kind, []).append(turns)
    later = [d for d in decisions if d.turn > 1]
    return ConversationMetrics(
        arm=arm,
        conversations=len(grouped),
        turns=len(decisions),
        turn_accuracy=_rate(sum(d.correct for d in decisions), len(decisions)),
        later_turn_accuracy=_rate(sum(d.correct for d in later), len(later)),
        conversation_success=_rate(sum(all(d.correct for d in turns) for turns in grouped.values()), len(grouped)),
        by_kind={
            kind: {
                "conversations": len(convs),
                "turn_accuracy": _rate(
                    sum(d.correct for turns in convs for d in turns), sum(len(turns) for turns in convs)
                ),
                "conversation_success": _rate(sum(all(d.correct for d in turns) for turns in convs), len(convs)),
            }
            for kind, convs in sorted(kinds.items())
        },
    )


# -------------------------------------------------------------------------- metrics


@dataclass
class ArmMetrics:
    arm: str
    cases: int
    accuracy: float
    duplicate_rate: float
    wrong_agent_rate: float
    false_reuse_rate: float
    roster_tokens_mean: float
    latency_ms_p50: float
    latency_ms_p95: float
    recall_shortlist: Optional[float] = None
    recall_search: Optional[float] = None
    search_rate: Optional[float] = None
    by_category: Dict[str, float] = field(default_factory=dict)
    recall_by_category: Dict[str, float] = field(default_factory=dict)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def score(arm: str, decisions: Sequence[Decision]) -> ArmMetrics:
    """Routing accuracy plus the three ways to be wrong.

    - duplicate: the right agent existed but a new one was created
    - wrong agent: the right agent existed but a different existing one was used
    - false reuse: a new agent was right but an existing one was used
    """

    existing = [d for d in decisions if d.gold != NEW]
    new = [d for d in decisions if d.gold == NEW]
    latencies = sorted(d.latency_ms for d in decisions)
    categories: Dict[str, List[Decision]] = {}
    for decision in decisions:
        categories.setdefault(decision.category, []).append(decision)

    shortlist_known = [d for d in existing if d.in_shortlist is not None]
    return ArmMetrics(
        arm=arm,
        cases=len(decisions),
        accuracy=_rate(sum(d.correct for d in decisions), len(decisions)),
        duplicate_rate=_rate(sum(d.chosen == NEW for d in existing), len(existing)),
        wrong_agent_rate=_rate(sum(d.chosen not in (NEW, d.gold) for d in existing), len(existing)),
        false_reuse_rate=_rate(sum(d.chosen != NEW for d in new), len(new)),
        roster_tokens_mean=statistics.fmean(d.roster_tokens for d in decisions) if decisions else 0.0,
        latency_ms_p50=latencies[len(latencies) // 2] if latencies else 0.0,
        latency_ms_p95=latencies[int(len(latencies) * 0.95) - 1] if latencies else 0.0,
        recall_shortlist=_rate(sum(bool(d.in_shortlist) for d in shortlist_known), len(shortlist_known))
        if shortlist_known else None,
        recall_search=_rate(sum(bool(d.in_search) for d in shortlist_known), len(shortlist_known))
        if shortlist_known else None,
        search_rate=_rate(sum(d.used_search for d in decisions), len(decisions)) if shortlist_known else None,
        by_category={name: _rate(sum(d.correct for d in items), len(items)) for name, items in sorted(categories.items())},
        recall_by_category={
            name: _rate(sum(bool(d.in_shortlist) for d in items), len(items))
            for name, items in sorted(categories.items())
            if items and items[0].in_shortlist is not None
        },
    )


def run_simulated(
    scenario: Scenario, *, threshold: float = 0.6, phrases: Optional[Dict[str, List[str]]] = None
) -> Dict[str, object]:
    """Run both arms on one scenario. Returns metrics plus every decision for inspection."""

    router = SimulatedRouter(scenario.vocabulary, threshold=threshold)
    baseline = BaselineArm(scenario)
    with tempfile.TemporaryDirectory() as workdir:
        solution = SolutionArm(scenario, Path(workdir), phrases=phrases)
        base_decisions = [baseline.route(case, router) for case in scenario.cases]
        new_decisions = [solution.route(case, router) for case in scenario.cases]
        # Each conversation starts from the same history; the solution arm gets a fresh copy
        base_turns: List[TurnDecision] = []
        new_turns: List[TurnDecision] = []
        no_flag_turns: List[TurnDecision] = []
        for conversation in scenario.conversations:
            base_turns += run_baseline_conversation(scenario, conversation, router)
            new_turns += run_solution_conversation(
                scenario, conversation, router, solution.registry.db_path, Path(workdir)
            )
            no_flag_turns += run_solution_conversation(
                scenario, conversation, router, solution.registry.db_path, Path(workdir), use_new_task=False
            )
    return {
        "agents": len(scenario.agents),
        "threshold": threshold,
        "baseline": score("baseline", base_decisions),
        "solution": score("solution", new_decisions),
        "decisions": {
            "baseline": [asdict(d) for d in base_decisions],
            "solution": [asdict(d) for d in new_decisions],
        },
        "conversations": {
            "baseline": score_conversations("baseline", base_turns),
            "solution": score_conversations("solution", new_turns),
            "solution_without_new_task": score_conversations("solution_without_new_task", no_flag_turns),
            "turns": {
                "baseline": [asdict(d) for d in base_turns],
                "solution": [asdict(d) for d in new_turns],
            },
        },
    }


__all__ = [
    "ArmMetrics",
    "BaselineArm",
    "ConversationMetrics",
    "Decision",
    "SimulatedRouter",
    "SolutionArm",
    "build_registry",
    "estimate_tokens",
    "TurnDecision",
    "run_baseline_conversation",
    "run_simulated",
    "run_solution_conversation",
    "score",
    "score_conversations",
]
