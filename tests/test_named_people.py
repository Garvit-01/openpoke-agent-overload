"""A named person's other agents are listed by name and topic, so a paraphrase can still be matched.

The case comes from the real-model eval: "first week schedule for the new hire" belongs to
"Onboarding Plan with Vera", but keywords ranked reminders and other Vera agents above it.
"""

from server.agents.interaction_agent.agent import agent_topic, render_agent_shortlist
from server.services.execution.registry import STATUS_ARCHIVED
from server.services.execution.retrieval import AgentRetriever

from .conftest import add_agent

MESSAGE = "Any news from Vera about the first week schedule for the new hire?"


def vera_roster(registry):
    add_agent(
        registry,
        "Onboarding Plan with Vera",
        summary="Task: Draft a reply to Vera Kowalski about the onboarding plan",
        entities={"person": ["Vera", "Vera Kowalski"], "subject": ["Re: onboarding plan"]},
        days_ago=60,
        status=STATUS_ARCHIVED,
    )
    for index, topic in enumerate(["hiring plan", "vendor quote", "pricing proposal", "board deck", "budget"]):
        add_agent(
            registry,
            f"Email to Vera {index}",
            summary=f"Task: Draft a reply to Vera Person{index} about the {topic}",
            entities={"person": [f"Vera Person{index}"], "subject": [f"Re: {topic}"]},
            days_ago=index + 1,
        )
    for reminder in ["order new glasses", "schedule annual physical", "schedule car service"]:
        add_agent(registry, f"Reminder: {reminder}", summary=f"Task: remind the user to {reminder}")
    add_agent(registry, "Email to Michael", summary="Task: reply to Michael", entities={"person": ["Michael Brown"]})


def test_paraphrased_agent_is_missing_from_the_keyword_shortlist(registry):
    vera_roster(registry)
    shortlist = AgentRetriever(registry).shortlist(MESSAGE, retrieved=4, recent=2)
    assert "Onboarding Plan with Vera" not in {item.name for item in shortlist}


def test_named_person_agents_fill_the_gap(registry):
    vera_roster(registry)
    retriever = AgentRetriever(registry)
    shortlist = retriever.shortlist(MESSAGE, retrieved=4, recent=2)
    related = retriever.named_people_agents(MESSAGE, exclude=[item.name for item in shortlist])

    names = [record.name for record in related]
    assert "Onboarding Plan with Vera" in names
    assert not set(names) & {item.name for item in shortlist}, "never listed twice"
    assert "Email to Michael" not in names, "only people the message names"
    assert not any(name.startswith("Reminder") for name in names)

    rendered = render_agent_shortlist(shortlist, total=registry.count(), related=related)
    assert "Onboarding Plan with Vera | onboarding plan | archived" in rendered
    assert f'shown="{len(shortlist) + len(related)}"' in rendered


def test_no_named_person_adds_nothing(registry):
    vera_roster(registry)
    assert AgentRetriever(registry).named_people_agents("remind me about the car service") == []


def test_list_is_capped_most_recent_first(registry):
    for index in range(30):
        add_agent(registry, f"Email to Sam {index}", entities={"person": [f"Sam Person{index}"]}, days_ago=index)
    related = AgentRetriever(registry).named_people_agents("did Sam reply?", limit=5)
    assert [record.name for record in related] == [f"Email to Sam {index}" for index in range(5)]


def test_topic_prefers_the_subject_then_the_summary(registry):
    vera_roster(registry)
    assert agent_topic(registry.get("Onboarding Plan with Vera")) == "onboarding plan"
    assert agent_topic(registry.get("Reminder: schedule car service")) == "remind the user to schedule car service"
