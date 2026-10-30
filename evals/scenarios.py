"""Synthetic OpenPoke history: hundreds of agents built up over ~10 weeks, plus labelled follow-ups.

Everything here is generated, not real user data. It is built to contain the failure modes
the OpenPoke explainer describes once agents pile up:
- several contacts who share a first name (four Sarahs, three Davids, ...)
- agent names in the loose styles a model produces ("Email to Sarah", "Acme Q3 invoice")
- the same contact on more than one thread
- old threads that come back after weeks of silence
- genuinely new requests that look a lot like old ones
- short multi-turn conversations where later turns only say "send it" or "the first one"

Each agent's summary and entities are produced by the real digest code from a synthetic log,
so the evaluator exercises the same profile-building path the server uses.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Set, Tuple

from server.services.execution.digest import build_digest
from server.services.execution.entities import tokenize as _app_tokenize


# The simulated router stands in for the model, so it reads words as written whatever OPENPOKE_FOLD_PLURALS
# says: a setting of the app's search must not also change the judge (evals/compare.py flips it per row).
def tokenize(text: str):
    return _app_tokenize(text, fold=False)

NEW = "__NEW__"
DEFAULT_NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)

# A few very common first names so collisions happen the way they do in a real inbox
COMMON_FIRST = ["Sarah", "David", "Michael", "Priya", "Emma", "Alex"]
OTHER_FIRST = [
    "Liam", "Noah", "Olivia", "Ava", "Mateo", "Zara", "Kenji", "Amara", "Diego", "Fatima",
    "Hannah", "Ibrahim", "Julia", "Kofi", "Lena", "Marco", "Nadia", "Omar", "Rosa", "Sven",
    "Tara", "Umar", "Vera", "Wei", "Yara", "Zane", "Chloe", "Ethan", "Grace", "Isaac",
    "Jasmine", "Kai", "Leah", "Mila", "Nico", "Paige", "Quinn", "Ravi", "Sofia", "Theo",
]
LAST_NAMES = [
    "Chen", "Patel", "Kim", "Lopez", "Nguyen", "Okafor", "Schmidt", "Rossi", "Silva", "Haddad",
    "Kowalski", "Murphy", "Tanaka", "Dubois", "Novak", "Larsen", "Mendes", "Fischer", "Ali", "Brown",
    "Garcia", "Ivanova", "Johansson", "Kaur", "Mwangi", "Park", "Reyes", "Sato", "Turner", "Weber",
    "Yilmaz", "Zhang", "Abbott", "Bianchi", "Costa", "Dimitrov", "Evans", "Fernandes", "Gupta", "Hughes",
]
COMPANIES = [
    "Acme", "Northwind", "Globex", "Initech", "Umbrella", "Hooli", "Stark", "Wayne", "Vandelay",
    "Soylent", "Tyrell", "Cyberdyne", "Wonka", "Gringotts", "Monarch", "Aperture", "Oscorp", "Pied Piper",
]
TOPICS = [
    "Q3 invoice", "venue contract", "offsite agenda", "job offer", "lease renewal", "insurance claim",
    "podcast interview", "design review", "board deck", "visa paperwork", "conference talk",
    "budget approval", "onboarding plan", "vendor quote", "travel itinerary", "contract redlines",
    "reference check", "product demo", "partnership proposal", "tax documents", "hiring plan",
    "security audit", "pricing proposal", "launch timeline",
]
# Reminders are capped at this many distinct tasks so "push my X reminder" always has one right answer
REMINDER_THINGS = [
    "pay rent", "renew passport", "call mom", "dentist appointment", "car insurance renewal",
    "cancel gym membership", "file expense report", "book flights to Lisbon", "submit timesheet",
    "water the plants", "pick up dry cleaning", "renew domain name", "quarterly tax payment",
    "vet appointment", "send birthday card to Leo", "return library books", "change air filter",
    "review 401k allocation", "schedule car service", "update resume", "pay phone bill",
    "renew parking permit", "book eye exam", "send invoice to landlord", "pick up passport photos",
    "check visa status", "renew driver license", "pay credit card", "call the bank", "sign lease addendum",
    "pay water bill", "order new glasses", "book team dinner", "submit reimbursement", "renew library card",
    "check flight prices", "pay daycare", "schedule annual physical", "replace smoke detector battery",
    "send thank you notes",
]
# How a person might refer to a topic without using any of its words. Keyword retrieval cannot
# bridge these; this category exists to show where the approach breaks, not to make it look good.
PARAPHRASES = {
    "Q3 invoice": "the bill for last quarter",
    "venue contract": "the event space agreement",
    "offsite agenda": "what we're doing at the team retreat",
    "job offer": "the position they want me for",
    "lease renewal": "extending the apartment rental",
    "insurance claim": "the coverage request after the accident",
    "podcast interview": "the show recording",
    "design review": "the mockup feedback session",
    "board deck": "the slides for the directors",
    "visa paperwork": "the immigration forms",
    "conference talk": "the speaking slot at the summit",
    "budget approval": "signing off on the spending",
    "onboarding plan": "the first week schedule for the new hire",
    "vendor quote": "the supplier pricing",
    "travel itinerary": "the trip schedule",
    "contract redlines": "the edits to the agreement",
    "reference check": "the background call about the candidate",
    "product demo": "the walkthrough of the app",
    "partnership proposal": "working together on something",
    "tax documents": "the forms for the accountant",
    "hiring plan": "how many people we're bringing on",
    "security audit": "the pentest",
    "pricing proposal": "the rates we pitched",
    "launch timeline": "when we go live",
}

# Used only for brand-new requests so they never collide with existing agents by construction
NEW_REMINDER_THINGS = [
    "buy concert tickets", "renew fishing license", "order printer ink", "book a haircut",
    "pay parking ticket", "refill prescription", "backup laptop", "sign permission slip",
    "plan anniversary dinner", "repaint the fence",
]
NEW_TOPICS = ["sponsorship deck", "warehouse lease", "audit findings", "speaker lineup", "grant application"]
NEW_LAST_NAMES = ["Achebe", "Bergstrom", "Castellanos", "Delacroix", "Esposito", "Farouk", "Grieve", "Holloway"]


@dataclass
class Contact:
    first: str
    last: str
    company: str

    @property
    def full_name(self) -> str:
        return f"{self.first} {self.last}"

    @property
    def email(self) -> str:
        domain = self.company.lower().replace(" ", "")
        return f"{self.first.lower()}.{self.last.lower()}@{domain}.com"


@dataclass
class SyntheticAgent:
    name: str
    kind: str  # "thread" | "reminder" | "search"
    summary: str
    entities: Dict[str, List[str]]
    created_days_ago: float
    active_days_ago: float
    has_trigger: bool = False
    contact: Optional[Contact] = None
    topic: str = ""
    thread_id: str = ""
    # Every request sent to the agent, oldest first. The summary above is built from the first one only
    # (see _add_request_histories for why the later ones are left out of it).
    requests: List[str] = field(default_factory=list)


@dataclass
class Case:
    id: str
    category: str
    message: str
    gold: str  # an agent name, or NEW when a new agent is the right call


@dataclass
class Turn:
    message: str
    gold: str  # an agent name, or NEW
    # When set, the right agent is whichever one the arm created at that turn (1-based),
    # e.g. "Ok, send it." after "Email a new person". Errors in earlier turns carry over.
    gold_from_turn: Optional[int] = None


@dataclass
class Conversation:
    id: str
    kind: str
    turns: List[Turn]


@dataclass
class Scenario:
    seed: int
    now: datetime
    agents: List[SyntheticAgent]
    cases: List[Case]
    vocabulary: Set[str] = field(default_factory=set)
    conversations: List[Conversation] = field(default_factory=list)

    def agent(self, name: str) -> SyntheticAgent:
        return next(agent for agent in self.agents if agent.name == name)


def _active_days_ago(rng: random.Random) -> float:
    roll = rng.random()
    if roll < 0.40:
        return rng.uniform(0, 7)
    if roll < 0.75:
        return rng.uniform(7, 30)
    return rng.uniform(30, 70)


def _thread_names(contact: Contact, topic: str) -> List[str]:
    """Naming styles a model produces when told to name agents by purpose, loosest first."""
    return [
        f"Email to {contact.first}",
        f"Email to {contact.full_name}",
        f"{contact.company} {topic.title()}",
        f"{topic.title()} with {contact.first}",
        f"Reply to {contact.first} re {topic}",
        f"{contact.full_name} {topic}",
    ]


def _digest(name: str, request: str, action: str, response: str) -> Tuple[str, Dict[str, List[str]]]:
    entries = [("agent_request", "", request), ("agent_action", "", action), ("agent_response", "", response)]
    return build_digest(name, entries)


def build_scenario(n_agents: int = 300, seed: int = 7, now: datetime = DEFAULT_NOW) -> Scenario:
    rng = random.Random(seed)
    n_threads = int(n_agents * 0.7)
    n_reminders = min(int(n_agents * 0.15), len(REMINDER_THINGS))
    n_search = n_agents - n_threads - n_reminders

    # Contacts: about one per 1.6 threads, so many contacts have more than one thread
    contacts: List[Contact] = []
    used_people: Set[Tuple[str, str]] = set()
    while len(contacts) < max(8, int(n_threads / 1.6)):
        first = rng.choice(COMMON_FIRST) if rng.random() < 0.35 else rng.choice(OTHER_FIRST)
        last = rng.choice(LAST_NAMES)
        if (first, last) in used_people:
            continue
        used_people.add((first, last))
        contacts.append(Contact(first, last, rng.choice(COMPANIES)))

    agents: List[SyntheticAgent] = []
    names: Set[str] = set()
    pairs: Set[Tuple[str, str]] = set()

    def add(agent: SyntheticAgent) -> None:
        names.add(agent.name)
        agents.append(agent)

    attempts = 0
    while sum(1 for a in agents if a.kind == "thread") < n_threads and attempts < n_threads * 50:
        attempts += 1
        contact = rng.choice(contacts)
        topic = rng.choice(TOPICS)
        if (contact.full_name, topic) in pairs:
            continue
        styles = _thread_names(contact, topic)
        preferred = rng.choices(styles[:5], weights=[25, 25, 20, 15, 15])[0]
        name = next((option for option in [preferred, *styles] if option not in names), None)
        if name is None:
            continue
        pairs.add((contact.full_name, topic))
        thread_id = "%016x" % rng.getrandbits(64)
        request = f"Draft a reply to {contact.full_name} ({contact.email}) about the {topic}"
        summary, entities = _digest(
            name,
            request,
            f'Calling gmail_create_draft with: {{"recipient_email": "{contact.email}", '
            f'"subject": "Re: {topic}", "thread_id": "{thread_id}"}}',
            f"Draft created for {contact.full_name} at {contact.company} about the {topic}. Waiting for the user to confirm.",
        )
        active = _active_days_ago(rng)
        add(
            SyntheticAgent(
                name=name, kind="thread", summary=summary, entities=entities,
                created_days_ago=active + rng.uniform(0, 20), active_days_ago=active,
                contact=contact, topic=topic, thread_id=thread_id, requests=[request],
            )
        )

    things = list(REMINDER_THINGS)
    rng.shuffle(things)
    for thing in things[:n_reminders]:
        name = f"Reminder: {thing}"
        request = f"Remind the user to {thing}"
        summary, entities = _digest(
            name,
            request,
            'Calling createTrigger with: {"payload": "' + thing + '"}',
            f"Reminder set: {thing}.",
        )
        active = _active_days_ago(rng)
        add(
            SyntheticAgent(
                name=name, kind="reminder", summary=summary, entities=entities,
                created_days_ago=active + rng.uniform(0, 20), active_days_ago=active,
                has_trigger=rng.random() < 0.7, topic=thing, requests=[request],
            )
        )

    for index in range(n_search):
        if index % 2 == 0:
            topic = TOPICS[(index // 2) % len(TOPICS)]
            name = f"Find {topic} emails"
            request = f"Find all emails about the {topic}"
        else:
            company = COMPANIES[(index // 2) % len(COMPANIES)]
            topic = company
            name = f"Summarize {company} updates"
            request = f"Summarize recent emails from {company}"
        if name in names:
            name = f"{name} ({index})"
        summary, entities = _digest(name, request, "Calling task_email_search", f"Found 6 emails. {request}: done.")
        active = _active_days_ago(rng)
        add(
            SyntheticAgent(
                name=name, kind="search", summary=summary, entities=entities,
                created_days_ago=active + rng.uniform(0, 20), active_days_ago=active, topic=topic,
                requests=[request],
            )
        )

    agents.sort(key=lambda agent: agent.created_days_ago, reverse=True)  # creation order
    cases = _build_cases(rng, agents, contacts)
    # A separate generator, so adding conversations never changes the single-turn cases
    conversations = _build_conversations(random.Random(seed * 1009 + 1), agents, contacts)
    # And another one for request histories, so they change neither of the above
    cases += _add_request_histories(random.Random(seed * 7907 + 3), agents, len(cases))
    return Scenario(
        seed=seed, now=now, agents=agents, cases=cases, vocabulary=_vocabulary(), conversations=conversations
    )


def _vocabulary() -> Set[str]:
    """Words that name people, companies, topics and tasks: what a follow-up is "about"."""
    words: List[str] = [*COMMON_FIRST, *OTHER_FIRST, *LAST_NAMES, *NEW_LAST_NAMES, *COMPANIES]
    words += TOPICS + REMINDER_THINGS + NEW_REMINDER_THINGS + NEW_TOPICS
    # No earlier case or conversation message uses these words (a test checks), so adding them
    # changes nothing for the other categories
    words += PAST_REQUEST_ITEMS
    return set(tokenize(" ".join(words)))


PAST_REQUEST = "followup_past_request"
PAST_REQUEST_CASES = 12
# Things asked for in the middle of a thread. Their words appear nowhere else in the scenario.
PAST_REQUEST_ITEMS = [
    "seating chart", "dietary restrictions", "wifi password", "shipping labels", "notarized copy",
    "cloakroom tokens", "wire receipt", "allergy list", "floor measurements", "fabric swatches",
    "loading dock hours", "spare keys", "projector cables", "visitor badges",
]
# Everyday follow-ups that make up most of a real history: noise for every other category.
# Some mention another contact, the way "cc Sarah" does in real requests.
HISTORY_NOISE = [
    "Ask {first} whether Thursday works for a call about the {topic}",
    "Send {first} a short nudge about the {topic}",
    "Move the {topic} call with {first} to next week",
    "Cc {other} on the next reply",
    "Check if {first} replied and summarise what they said",
]
HISTORY_SHARE = 0.2  # share of thread agents with more than one request


def _add_request_histories(rng: random.Random, agents: Sequence[SyntheticAgent], first_id: int) -> List[Case]:
    """Give some thread agents a longer history, and label follow-ups that only a middle request can answer.

    Every agent already holds its first request. About HISTORY_SHARE of the thread agents get one to
    three everyday follow-up requests (noise). PAST_REQUEST_CASES of them also get a request for a
    specific item in the middle of their history, and a case asks about that item later: half by item
    only ("Did the seating chart ever come through?"), half with the person's first name.

    Assumption: summaries stay as generated (first request and result), so every other category is
    measured on the same profiles as before. In the app the summary would also show the latest ask.
    That is why the item request is never the latest one: the summary would not show it either way.
    """

    threads = [agent for agent in agents if agent.kind == "thread"]
    if not threads:
        return []
    pool = list(threads)
    rng.shuffle(pool)
    with_history = pool[: max(PAST_REQUEST_CASES, int(len(threads) * HISTORY_SHARE))]
    targets = with_history[: min(PAST_REQUEST_CASES, len(PAST_REQUEST_ITEMS))]
    others = [agent.contact.full_name for agent in threads]

    def noise(agent: SyntheticAgent) -> str:
        template = rng.choice(HISTORY_NOISE)
        other = rng.choice([name for name in others if name != agent.contact.full_name] or others)
        return template.format(first=agent.contact.first, topic=agent.topic, other=other)

    cases: List[Case] = []
    for agent in with_history:
        middle = [noise(agent) for _ in range(rng.randint(0, 2))]
        if agent in targets:
            item = PAST_REQUEST_ITEMS[targets.index(agent)]
            middle.insert(rng.randint(0, len(middle)), f"Ask {agent.contact.first} to also send the {item} for the {agent.topic}")
            if len(cases) % 2 == 0:
                message = f"Did the {item} ever come through?"
            else:
                message = f"Has {agent.contact.first} sent the {item} yet?"
            cases.append(Case(id=f"{PAST_REQUEST}-{first_id + len(cases):03d}", category=PAST_REQUEST, message=message, gold=agent.name))
        # The latest request is always everyday noise, never the item
        agent.requests = [*agent.requests, *middle, noise(agent)]
    return cases


def _build_cases(rng: random.Random, agents: Sequence[SyntheticAgent], contacts: Sequence[Contact]) -> List[Case]:
    threads = [agent for agent in agents if agent.kind == "thread"]
    reminders = [agent for agent in agents if agent.kind == "reminder"]
    threads_per_contact: Dict[str, int] = {}
    for agent in threads:
        threads_per_contact[agent.contact.email] = threads_per_contact.get(agent.contact.email, 0) + 1
    cases: List[Case] = []

    def sample(pool: Sequence[SyntheticAgent], count: int) -> List[SyntheticAgent]:
        pool = list(pool)
        rng.shuffle(pool)
        return pool[:count]

    def unique_by(key) -> List[SyntheticAgent]:
        counts: Dict[Tuple, int] = {}
        for agent in threads:
            counts[key(agent)] = counts.get(key(agent), 0) + 1
        return [agent for agent in threads if counts[key(agent)] == 1]

    def add(category: str, message: str, gold: str) -> None:
        cases.append(Case(id=f"{category}-{len(cases):03d}", category=category, message=message, gold=gold))

    for agent in sample(threads, 30):
        template = rng.choice(["Did {name} reply about the {topic}?", "Can you check if {name} answered on the {topic}?"])
        add("followup_full_name", template.format(name=agent.contact.full_name, topic=agent.topic), agent.name)

    first_counts: Dict[str, int] = {}
    for contact in contacts:
        first_counts[contact.first] = first_counts.get(contact.first, 0) + 1
    shared_first = [a for a in unique_by(lambda a: (a.contact.first, a.topic)) if first_counts[a.contact.first] > 1]
    for agent in sample(shared_first, 30):
        add("followup_first_name_topic", f"Any update from {agent.contact.first} on the {agent.topic}?", agent.name)

    # Same shape, but the topic is paraphrased: no shared keywords with the agent's summary
    for agent in sample(shared_first, 15):
        add("followup_paraphrase", f"Any news from {agent.contact.first} about {PARAPHRASES[agent.topic]}?", agent.name)

    for agent in sample(unique_by(lambda a: (a.contact.company, a.topic)), 20):
        add("followup_company_topic", f"What's the latest on the {agent.contact.company} {agent.topic}?", agent.name)

    # Only contacts with a single thread, so the address alone names exactly one right agent
    single_thread = [agent for agent in threads if threads_per_contact[agent.contact.email] == 1]
    for agent in sample(single_thread, 20):
        add("followup_email", f"Reply to {agent.contact.email} and tell them Thursday works.", agent.name)

    for agent in sample(reminders, 15):
        add("followup_reminder", f"Push my {agent.topic} reminder to next Monday.", agent.name)

    stale = [agent for agent in threads if agent.active_days_ago > 45]
    for agent in sample(stale, 15):
        add(
            "followup_stale",
            f"Whatever happened with {agent.contact.full_name} and the {agent.topic}?",
            agent.name,
        )

    # New person who shares a first name (and often a topic) with existing threads: must NOT reuse
    shared_names = sorted({a.contact.first for a in threads if first_counts[a.contact.first] > 1})
    for index in range(20):
        first = shared_names[index % len(shared_names)] if shared_names else "Sarah"
        last = NEW_LAST_NAMES[index % len(NEW_LAST_NAMES)]
        topic = rng.choice(TOPICS)
        newcomer = Contact(first, last, rng.choice(COMPANIES))
        if index % 2 == 0:
            message = f"Email {newcomer.full_name} ({newcomer.email}) about the {topic}."
        else:
            message = f"Write to {newcomer.full_name} about the {topic}."
        add("new_same_first_name", message, NEW)

    for index in range(20):
        if index % 2 == 0:
            message = f"Remind me to {NEW_REMINDER_THINGS[index // 2 % len(NEW_REMINDER_THINGS)]} on Friday."
        else:
            message = f"Find any emails about the {NEW_TOPICS[index // 2 % len(NEW_TOPICS)]}."
        add("new_task", message, NEW)

    return cases


def _build_conversations(
    rng: random.Random, agents: Sequence[SyntheticAgent], contacts: Sequence[Contact]
) -> List[Conversation]:
    """Short conversations. Later turns lean on what was said before, the way people talk.

    - switch_and_return: talk about A, switch to B, then "go back to the first one"
    - correction: the first guess is wrong for a shared first name, the user corrects it
    - new_then_follow_up: a new contact, then "ok, send it" and "did they reply?"
    - same_person_new_topic: a known contact, then a new email to them on a new topic, which
      needs its own agent, then back to the old topic
    """

    threads = [agent for agent in agents if agent.kind == "thread"]
    conversations: List[Conversation] = []

    def count_by(key) -> Dict[Tuple, int]:
        counts: Dict[Tuple, int] = {}
        for agent in threads:
            counts[key(agent)] = counts.get(key(agent), 0) + 1
        return counts

    first_counts: Dict[str, int] = {}
    for contact in contacts:
        first_counts[contact.first] = first_counts.get(contact.first, 0) + 1
    by_first_topic = count_by(lambda a: (a.contact.first, a.topic))
    by_company_topic = count_by(lambda a: (a.contact.company, a.topic))
    by_email = count_by(lambda a: (a.contact.email,))

    # Same pools as the single-turn cases, so every label has exactly one right answer
    shared_first = [a for a in threads if by_first_topic[(a.contact.first, a.topic)] == 1 and first_counts[a.contact.first] > 1]
    company_topic = [a for a in threads if by_company_topic[(a.contact.company, a.topic)] == 1]
    single_thread = [a for a in threads if by_email[(a.contact.email,)] == 1]

    def add(kind: str, turns: List[Turn]) -> None:
        conversations.append(Conversation(id=f"{kind}-{len(conversations):02d}", kind=kind, turns=turns))

    # Each conversation opens on a different agent, so no two conversations test the same thing
    pool = list(shared_first)
    rng.shuffle(pool)
    opened: Set[str] = set()
    for first_agent in pool[:8]:
        opened.add(first_agent.name)
        others = [a for a in company_topic if a.contact.full_name != first_agent.contact.full_name]
        if not others:
            continue
        second = rng.choice(others)
        add("switch_and_return", [
            Turn(f"Any update from {first_agent.contact.first} on the {first_agent.topic}?", first_agent.name),
            Turn("Ok, draft a reply saying Thursday works.", first_agent.name),
            Turn(f"Also, what's the latest on the {second.contact.company} {second.topic}?", second.name),
            Turn("Go back to the first one and send that draft.", first_agent.name),
        ])

    pool = [agent for agent in shared_first if agent.name not in opened]
    rng.shuffle(pool)
    made = 0
    for guess in pool:
        if made == 6:
            break
        meant = [
            a for a in single_thread
            if a.contact.first == guess.contact.first and a.contact.full_name != guess.contact.full_name
        ]
        if not meant:
            continue
        target = rng.choice(meant)
        opened.add(guess.name)
        add("correction", [
            Turn(f"Any update from {guess.contact.first} on the {guess.topic}?", guess.name),
            Turn(f"Not that one. I meant {target.contact.full_name} at {target.contact.company}.", target.name),
            Turn("Tell them Thursday works.", target.name),
        ])
        made += 1

    shared_names = sorted(first for first, count in first_counts.items() if count > 1) or ["Sarah"]
    for index in range(6):
        newcomer = Contact(shared_names[index % len(shared_names)], NEW_LAST_NAMES[index % len(NEW_LAST_NAMES)], rng.choice(COMPANIES))
        add("new_then_follow_up", [
            Turn(f"Email {newcomer.full_name} ({newcomer.email}) about the {rng.choice(TOPICS)}.", NEW),
            Turn("Ok, send it.", NEW, gold_from_turn=1),
            Turn("Did they reply yet?", NEW, gold_from_turn=1),
        ])

    pool = [agent for agent in single_thread if agent.name not in opened]
    rng.shuffle(pool)
    for index, known in enumerate(pool[:6]):
        new_topic = NEW_TOPICS[index % len(NEW_TOPICS)]
        add("same_person_new_topic", [
            Turn(f"Did {known.contact.full_name} reply about the {known.topic}?", known.name),
            Turn(
                f"Also start a new email to {known.contact.full_name}"
                + (f" ({known.contact.email})" if index % 2 == 0 else "")
                + f" about the {new_topic}.",
                NEW,
            ),
            Turn("Ok, send it.", NEW, gold_from_turn=2),
            Turn(f"And did they answer on the {known.topic}?", known.name),
        ])

    return conversations


__all__ = [
    "Case",
    "Contact",
    "Conversation",
    "DEFAULT_NOW",
    "NEW",
    "PARAPHRASES",
    "PAST_REQUEST",
    "PAST_REQUEST_ITEMS",
    "Scenario",
    "SyntheticAgent",
    "Turn",
    "build_scenario",
]
