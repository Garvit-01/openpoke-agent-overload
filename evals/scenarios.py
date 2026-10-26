"""Synthetic OpenPoke history: hundreds of agents built up over ~10 weeks, plus labelled follow-ups.

Everything here is generated, not real user data. It is built to contain the failure modes
the OpenPoke explainer describes once agents pile up:
- several contacts who share a first name (four Sarahs, three Davids, ...)
- agent names in the loose styles a model produces ("Email to Sarah", "Acme Q3 invoice")
- the same contact on more than one thread
- old threads that come back after weeks of silence
- genuinely new requests that look a lot like old ones

Each agent's summary and entities are produced by the real digest code from a synthetic log,
so the evaluator exercises the same profile-building path the server uses.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Set, Tuple

from server.services.execution.digest import build_digest
from server.services.execution.entities import tokenize

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


@dataclass
class Case:
    id: str
    category: str
    message: str
    gold: str  # an agent name, or NEW when a new agent is the right call


@dataclass
class Scenario:
    seed: int
    now: datetime
    agents: List[SyntheticAgent]
    cases: List[Case]
    vocabulary: Set[str] = field(default_factory=set)

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
        summary, entities = _digest(
            name,
            f"Draft a reply to {contact.full_name} ({contact.email}) about the {topic}",
            f'Calling gmail_create_draft with: {{"recipient_email": "{contact.email}", '
            f'"subject": "Re: {topic}", "thread_id": "{thread_id}"}}',
            f"Draft created for {contact.full_name} at {contact.company} about the {topic}. Waiting for the user to confirm.",
        )
        active = _active_days_ago(rng)
        add(
            SyntheticAgent(
                name=name, kind="thread", summary=summary, entities=entities,
                created_days_ago=active + rng.uniform(0, 20), active_days_ago=active,
                contact=contact, topic=topic, thread_id=thread_id,
            )
        )

    things = list(REMINDER_THINGS)
    rng.shuffle(things)
    for thing in things[:n_reminders]:
        name = f"Reminder: {thing}"
        summary, entities = _digest(
            name,
            f"Remind the user to {thing}",
            'Calling createTrigger with: {"payload": "' + thing + '"}',
            f"Reminder set: {thing}.",
        )
        active = _active_days_ago(rng)
        add(
            SyntheticAgent(
                name=name, kind="reminder", summary=summary, entities=entities,
                created_days_ago=active + rng.uniform(0, 20), active_days_ago=active,
                has_trigger=rng.random() < 0.7, topic=thing,
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
            )
        )

    agents.sort(key=lambda agent: agent.created_days_ago, reverse=True)  # creation order
    cases = _build_cases(rng, agents, contacts)
    return Scenario(seed=seed, now=now, agents=agents, cases=cases, vocabulary=_vocabulary())


def _vocabulary() -> Set[str]:
    """Words that name people, companies, topics and tasks: what a follow-up is "about"."""
    words: List[str] = [*COMMON_FIRST, *OTHER_FIRST, *LAST_NAMES, *NEW_LAST_NAMES, *COMPANIES]
    words += TOPICS + REMINDER_THINGS + NEW_REMINDER_THINGS + NEW_TOPICS
    return set(tokenize(" ".join(words)))


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


__all__ = [
    "Case",
    "Contact",
    "DEFAULT_NOW",
    "NEW",
    "PARAPHRASES",
    "Scenario",
    "SyntheticAgent",
    "build_scenario",
]
