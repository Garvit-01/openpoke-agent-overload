"""A hand-written paraphrase set: the known weak spot, tested without the generator's vocabulary.

The generated cases share words with the agent summaries because the same code wrote both.
Here each message was written by hand to avoid every content word of its agent's name and
summary, except the person's first name. A test enforces that. Real users would phrase things
differently again, so this is still not real data, but it can't be passed by keyword overlap.

Each case adds one agent on a topic the generator never uses, to the same 300-agent history.
First names come from the generator's pool on purpose, so they collide with existing agents
the way they would in a real inbox. 16 messages name the person; 8 name nobody, so only the
topic can find the agent.

    python -m evals.run_eval --set paraphrase                 # simulated, free
    python -m evals.run_eval --set paraphrase --router llm    # real model, about $0.20 on Haiku
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List

from .scenarios import Case, Contact, Scenario, SyntheticAgent, _digest, build_scenario

NAMED = "handwritten_names_person"
TOPIC_ONLY = "handwritten_topic_only"


@dataclass(frozen=True)
class Handwritten:
    first: str
    last: str
    company: str
    topic: str
    name: str
    message: str
    active_days_ago: float
    names_person: bool = True


HANDWRITTEN: List[Handwritten] = [
    Handwritten("Sarah", "Whitfield", "Lumen", "kitchen renovation quote", "Email to Sarah Whitfield",
                "Did Sarah get back to us on how much redoing the cooking area will cost?", 6),
    Handwritten("David", "Okonkwo", "Brightline", "data center migration", "Data Center Migration with David",
                "Any word from David about moving our servers to the new facility?", 3),
    Handwritten("Michael", "Ferreira", "Halcyon", "parental leave plan", "Reply to Michael re parental leave plan",
                "Ping Michael again about his time off when the baby arrives.", 21),
    Handwritten("Priya", "Lindqvist", "Meridian", "trade show booth", "Meridian Trade Show Booth",
                "What did Priya say about the stand we're putting up at the exhibition?", 9),
    Handwritten("Emma", "Vasquez", "Corbel", "wedding photographer", "Email to Emma Vasquez",
                "Has Emma locked in who's taking pictures at the ceremony?", 30),
    Handwritten("Alex", "Moreau", "Ridgeway", "car lease return", "Email to Alex Moreau",
                "Where are we with Alex on handing back the vehicle when the rental period ends?", 12),
    Handwritten("Liam", "Ostrowski", "Bramble", "annual stocktake", "Email to Liam Ostrowski",
                "Did Liam finish counting everything on the shelves for year end?", 50),
    Handwritten("Olivia", "Brandt", "Sequoia", "fundraising gala", "Fundraising Gala with Olivia",
                "Is Olivia still on for the charity dinner next month?", 4),
    Handwritten("Kenji", "Albrecht", "Tidewater", "patent filing", "Reply to Kenji re patent filing",
                "Has Kenji sent over the paperwork to protect our invention?", 17),
    Handwritten("Fatima", "Rahimi", "Northgate", "dental insurance enrollment", "Email to Fatima Rahimi",
                "Did Fatima sort out signing me up for the teeth cover?", 8),
    Handwritten("Marco", "Lindgren", "Pinecrest", "website redesign", "Email to Marco Lindgren",
                "Any update from Marco on giving our homepage a fresh look?", 2),
    Handwritten("Nadia", "Kovacs", "Fairhaven", "employee survey results", "Employee Survey Results with Nadia",
                "Has Nadia shared what staff said in the questionnaire?", 25),
    Handwritten("Omar", "Castillo", "Brightwater", "office move", "Brightwater Office Move",
                "Any progress from Omar on relocating us to the other building?", 14),
    Handwritten("Grace", "Halvorsen", "Larkspur", "scholarship application", "Reply to Grace re scholarship application",
                "Did Grace hear back about the funding for her studies?", 60),
    Handwritten("Ravi", "Desai", "Kestrel", "server outage postmortem", "Email to Ravi Desai",
                "What did Ravi conclude about why the site went down last week?", 5),
    Handwritten("Sofia", "Marchetti", "Quillon", "book manuscript", "Email to Sofia Marchetti",
                "Has Sofia finished going through the chapters of my novel?", 40),
    # No person named: only the topic can find these
    Handwritten("Theo", "Brennan", "Ashgrove", "home insurance renewal", "Ashgrove Home Insurance Renewal",
                "When is the policy on the house up again?", 19, names_person=False),
    Handwritten("Jasmine", "Albright", "Copperfield", "school enrollment", "School Enrollment with Jasmine",
                "Did the kids get a place at the academy?", 7, names_person=False),
    Handwritten("Isaac", "Ferrand", "Bluewater", "solar panel installation", "Bluewater Solar Panel Installation",
                "When are they putting the rooftop power system in?", 35, names_person=False),
    Handwritten("Leah", "Moretti", "Saltmarsh", "puppy adoption", "Reply to Leah re puppy adoption",
                "Any news on bringing the dog home?", 11, names_person=False),
    Handwritten("Wei", "Hartmann", "Orchard", "manual translation", "Orchard Manual Translation",
                "Is the Japanese version of the instructions booklet ready?", 28, names_person=False),
    Handwritten("Yara", "Lindberg", "Foxglove", "catering order", "Catering Order with Yara",
                "Did the food for the launch party get booked?", 3, names_person=False),
    Handwritten("Zane", "Whitaker", "Ironbridge", "laptop refresh", "Ironbridge Laptop Refresh",
                "Where are we on replacing everyone's old computers?", 48, names_person=False),
    Handwritten("Chloe", "Ashworth", "Riverbend", "retirement party", "Reply to Chloe re retirement party",
                "Is the farewell for the colleague leaving after thirty years still on Friday?", 16, names_person=False),
]


def handwritten_agent(item: Handwritten, thread_id: str) -> SyntheticAgent:
    """The agent as the generator would have recorded it, so its summary looks like every other one."""
    contact = Contact(item.first, item.last, item.company)
    summary, entities = _digest(
        item.name,
        f"Draft a reply to {contact.full_name} ({contact.email}) about the {item.topic}",
        f'Calling gmail_create_draft with: {{"recipient_email": "{contact.email}", '
        f'"subject": "Re: {item.topic}", "thread_id": "{thread_id}"}}',
        f"Draft created for {contact.full_name} at {contact.company} about the {item.topic}. "
        "Waiting for the user to confirm.",
    )
    return SyntheticAgent(
        name=item.name, kind="thread", summary=summary, entities=entities,
        created_days_ago=item.active_days_ago + 5, active_days_ago=item.active_days_ago,
        contact=contact, topic=item.topic, thread_id=thread_id,
    )


def build_paraphrase_scenario(n_agents: int = 300, seed: int = 7) -> Scenario:
    """The usual generated history plus the hand-written agents; only the hand-written messages are cases."""
    base = build_scenario(n_agents, seed=seed)
    rng = random.Random(seed * 7919)
    taken = {agent.name for agent in base.agents}
    agents = list(base.agents)
    cases: List[Case] = []
    for index, item in enumerate(HANDWRITTEN):
        if item.name in taken:
            raise ValueError(f"hand-written agent name collides with a generated one: {item.name}")
        agents.append(handwritten_agent(item, "%016x" % rng.getrandbits(64)))
        category = NAMED if item.names_person else TOPIC_ONLY
        cases.append(Case(id=f"hw{index:02d}", category=category, message=item.message, gold=item.name))
    agents.sort(key=lambda agent: agent.created_days_ago, reverse=True)
    return Scenario(seed=seed, now=base.now, agents=agents, cases=cases, vocabulary=base.vocabulary)


__all__ = ["HANDWRITTEN", "NAMED", "TOPIC_ONLY", "Handwritten", "build_paraphrase_scenario", "handwritten_agent"]
