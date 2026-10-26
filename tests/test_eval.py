"""Tests for the evaluator itself: are the labels sound, does the router follow its rules,
and does the end-to-end comparison hold on a small scenario."""

import asyncio
import json

import pytest

from evals.harness import SimulatedRouter, run_simulated
from evals.llm import NO_ROUTE, evaluate_llm, sample_cases, summarise
from evals.scenarios import NEW, build_scenario


@pytest.fixture(scope="module")
def scenario():
    return build_scenario(200, seed=3)


def test_scenario_is_deterministic():
    first, second = build_scenario(120, seed=5), build_scenario(120, seed=5)
    assert [c.message for c in first.cases] == [c.message for c in second.cases]
    assert [a.name for a in first.agents] == [a.name for a in second.agents]


def test_labels_are_sound(scenario):
    names = {agent.name for agent in scenario.agents}
    assert len(names) == len(scenario.agents), "agent names must be unique"
    for case in scenario.cases:
        if case.gold == NEW:
            continue
        assert case.gold in names
    # An email follow-up must point at exactly one agent, or the label would be a guess
    for case in (c for c in scenario.cases if c.category == "followup_email"):
        address = case.message.split()[2]
        owners = [a.name for a in scenario.agents if address in a.entities.get("email", [])]
        assert owners == [case.gold]


def test_new_cases_name_people_who_do_not_exist(scenario):
    people = {a.contact.full_name for a in scenario.agents if a.contact}
    for case in (c for c in scenario.cases if c.category == "new_same_first_name"):
        assert not any(person in case.message for person in people)


def test_router_prefers_an_exact_email(scenario):
    router = SimulatedRouter(scenario.vocabulary)
    visible = [("Email to Sarah", "Email to Sarah sarah.kim@acme.com"), ("Other", "Other")]
    assert router.choose("reply to sarah.kim@acme.com", visible) == "Email to Sarah"


def test_router_skips_a_different_person_with_the_same_first_name(scenario):
    router = SimulatedRouter(scenario.vocabulary)
    visible = [("Email to Sarah", "Email to Sarah Chen about the Q3 invoice")]
    assert router.choose("Email Sarah Achebe about the Q3 invoice", visible) is None
    assert router.choose("Did Sarah Chen reply?", visible) == "Email to Sarah"


def test_router_uses_topic_to_pick_between_people_with_one_name(scenario):
    router = SimulatedRouter(scenario.vocabulary)
    visible = [("Email to Sarah", "Email to Sarah"), ("Sarah venue contract", "Sarah venue contract")]
    assert router.choose("Any update from Sarah on the venue contract?", visible) == "Sarah venue contract"


def test_small_end_to_end_comparison(scenario):
    result = run_simulated(scenario)
    base, new = result["baseline"], result["solution"]
    assert new.accuracy > base.accuracy + 0.2
    assert new.duplicate_rate == 0.0
    assert new.roster_tokens_mean < base.roster_tokens_mean
    assert new.recall_shortlist >= 0.9


def test_llm_loop_with_a_fake_model(scenario, tmp_path):
    """The LLM path, minus the network: search first in the solution arm, then route."""

    async def fake_complete(*, model, messages, system, api_key, tools):
        tool_names = {tool["function"]["name"] for tool in tools}
        last = messages[-1]
        if "search_agents" in tool_names and last["role"] == "user":
            call = {"id": "1", "function": {"name": "search_agents", "arguments": json.dumps({"query": "x"})}}
            return {"choices": [{"message": {"content": "", "tool_calls": [call]}}], "usage": {"prompt_tokens": 900}}
        if last["role"] == "tool":
            first = json.loads(last["content"])["result"]["matches"]
            name = first[0]["agent_name"] if first else "Brand new"
        else:
            name = "Brand new"
        call = {"id": "2", "function": {"name": "send_message_to_agent",
                                        "arguments": json.dumps({"agent_name": name, "instructions": "go"})}}
        return {"choices": [{"message": {"content": "", "tool_calls": [call]}}], "usage": {"prompt_tokens": 3000}}

    cases = sample_cases(scenario, 6, seed=1)
    assert len({case.category for case in cases}) == 6
    outcomes = asyncio.run(
        evaluate_llm(scenario, cases, complete=fake_complete, model="fake", api_key=None, repeats=2, workdir=tmp_path)
    )
    assert len(outcomes) == 6 * 2 * 2
    assert all(o.chosen == NEW for o in outcomes if o.arm == "baseline")
    summary = summarise(outcomes)
    assert summary["solution"]["search_rate"] == 1.0
    assert summary["solution"]["prompt_tokens_mean"] == 900
    assert summary["baseline"]["no_route_rate"] == 0.0
    assert NO_ROUTE not in {o.chosen for o in outcomes}
