"""Search phrases let keyword search find an agent when the user's words don't match its own."""

import asyncio

from server.config import get_settings
from server.services.execution.phrases import PHRASE_KIND, maybe_add_phrases, parse_phrases
from server.services.execution.resolver import resolve_agent
from server.services.execution.retrieval import AgentRetriever

from .conftest import add_agent

QUERY = "when is the policy on the house up again?"


def insurance_roster(registry):
    add_agent(
        registry,
        "Ashgrove Home Insurance Renewal",
        summary="Task: Draft a reply to Theo Brennan about the home insurance renewal",
        entities={"person": ["Theo Brennan"], "email": ["theo.brennan@ashgrove.com"]},
        days_ago=20,
    )
    add_agent(registry, "Reminder: water the plants", summary="Task: remind the user to water the plants")


def fake_model(text, calls=None):
    async def complete(*, model, messages, system, api_key, tools):
        if calls is not None:
            calls.append(messages[0]["content"])
        return {"choices": [{"message": {"content": text}}]}

    return complete


def test_parse_phrases_cleans_model_output():
    text = "1. house policy\n- Home cover renewal\n* \"property insurance\"\n\nhouse policy\n" + "a " * 12
    assert parse_phrases(text) == ["house policy", "home cover renewal", "property insurance"]


def test_keywords_alone_miss_the_paraphrase(registry):
    insurance_roster(registry)
    assert "Ashgrove Home Insurance Renewal" not in [item.name for item in AgentRetriever(registry).search(QUERY)]


def test_phrases_make_it_findable(registry, monkeypatch):
    insurance_roster(registry)
    monkeypatch.setattr(get_settings(), "agent_search_phrases", True)
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")
    calls = []
    added = asyncio.run(maybe_add_phrases(
        "Ashgrove Home Insurance Renewal", registry=registry,
        complete=fake_model("house policy renewal\nproperty cover\nhomeowners policy", calls),
    ))

    assert added == 3
    assert "Ashgrove Home Insurance Renewal" in calls[0], "the model only sees the agent's own name and summary"
    assert registry.get("Ashgrove Home Insurance Renewal").entities[PHRASE_KIND] == [
        "homeowners policy", "house policy renewal", "property cover"
    ]
    top = AgentRetriever(registry).search(QUERY)[0]
    assert top.name == "Ashgrove Home Insurance Renewal"


def test_written_once_and_off_without_a_key(registry, monkeypatch):
    insurance_roster(registry)
    monkeypatch.setattr(get_settings(), "agent_search_phrases", True)
    monkeypatch.setattr(get_settings(), "openrouter_api_key", None)
    assert asyncio.run(maybe_add_phrases("Ashgrove Home Insurance Renewal", registry=registry,
                                         complete=fake_model("x y"))) == 0

    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")
    calls = []
    asyncio.run(maybe_add_phrases("Ashgrove Home Insurance Renewal", registry=registry, complete=fake_model("a b", calls)))
    asyncio.run(maybe_add_phrases("Ashgrove Home Insurance Renewal", registry=registry, complete=fake_model("c d", calls)))
    assert len(calls) == 1


def test_a_failing_model_never_breaks_anything(registry, monkeypatch):
    insurance_roster(registry)
    monkeypatch.setattr(get_settings(), "agent_search_phrases", True)
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")

    async def broken(**kwargs):
        raise RuntimeError("provider down")

    assert asyncio.run(maybe_add_phrases("Ashgrove Home Insurance Renewal", registry=registry, complete=broken)) == 0


def test_phrases_never_merge_agents(registry):
    insurance_roster(registry)
    registry.update_profile("Ashgrove Home Insurance Renewal", entities={PHRASE_KIND: ["house policy"]})
    resolution = resolve_agent("House policy question", "When is the house policy up again?", registry)
    assert resolution.created
