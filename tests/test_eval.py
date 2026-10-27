"""Tests for the evaluator itself: are the labels sound, does the router follow its rules,
and does the end-to-end comparison hold on a small scenario."""

import asyncio
import json

import pytest

from evals.harness import SimulatedRouter, run_simulated
from evals.llm import NO_ROUTE, cost_lines, cost_per_1k, evaluate_llm, sample_cases, summarise
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
    # Tokens and calls are summed over every call: the solution arm searched first (900) then routed (3000)
    assert summary["solution"]["input_tokens_per_decision"] == 3900
    assert summary["solution"]["calls_per_decision"] == 2
    assert summary["baseline"]["input_tokens_per_decision"] == 3000
    assert summary["baseline"]["calls_per_decision"] == 1


def test_cost_tables_price_the_measured_tokens():
    arm = {"runs": 10, "input_tokens_per_decision": 4000, "output_tokens_per_decision": 100, "calls_per_decision": 1.0}
    small = {**arm, "input_tokens_per_decision": 2000}
    # $1 per million input, $5 per million output: 4,000 x 1e-6 + 100 x 5e-6 = $0.0045 per decision
    assert cost_per_1k(arm, (1e-6, 5e-6)) == pytest.approx(4.5)

    prices = {"anthropic/claude-haiku-5.5": (1e-6, 5e-6), "anthropic/claude-opus-5.5": (4e-6, 2e-5)}
    lines = "\n".join(cost_lines({"baseline": arm, "solution": small}, prices, "anthropic/claude-haiku-5.5"))
    assert "| Cost per 1,000 routing decisions | $4.50 | $2.50 |" in lines
    assert "| anthropic/claude-opus-5.5 |" in lines
    assert "44%" in lines  # 1 - 2.5 / 4.5
    assert cost_lines({"baseline": arm, "solution": small}, {}, "x") == []


def test_conversation_labels_are_sound(scenario):
    names = {agent.name for agent in scenario.agents}
    kinds = {conversation.kind for conversation in scenario.conversations}
    assert kinds == {"switch_and_return", "correction", "new_then_follow_up", "same_person_new_topic"}
    openers = [conversation.turns[0].gold for conversation in scenario.conversations if conversation.turns[0].gold != NEW]
    assert len(openers) == len(set(openers)), "each conversation should open on a different agent"
    for conversation in scenario.conversations:
        for turn in conversation.turns:
            if turn.gold_from_turn is not None:
                # It must point back at an earlier turn whose right answer was a new agent
                assert conversation.turns[turn.gold_from_turn - 1].gold == NEW
            elif turn.gold != NEW:
                assert turn.gold in names


def test_router_resolves_references_from_the_conversation(scenario):
    router = SimulatedRouter(scenario.vocabulary)
    memory = ["Email to Sarah", "Vercel Job Offer"]
    assert router.refer_back("Ok, send it.", memory) == "Vercel Job Offer"
    assert router.refer_back("Go back to the first one and send that draft.", memory) == "Email to Sarah"
    assert router.refer_back("What about the other one?", memory) == "Email to Sarah"
    # A turn that names someone or something is not a reference, and there is nothing to refer to at first
    assert router.refer_back("Did Sarah Chen reply?", memory) is None
    assert router.refer_back("Ok, send it.", []) is None


def test_a_wrong_first_turn_carries_over(scenario):
    from evals.harness import _turn_correct
    from evals.scenarios import Turn

    follow_up = Turn("Ok, send it.", NEW, gold_from_turn=1)
    assert _turn_correct(follow_up, "Email to Sarah Achebe", False, {1: "Email to Sarah Achebe"})
    # Turn 1 reused an old agent instead of creating one, so "send it" cannot be right either
    assert not _turn_correct(follow_up, "Email to Sarah", False, {})


def test_conversation_comparison(scenario):
    conversations = run_simulated(scenario)["conversations"]
    base, new, no_flag = conversations["baseline"], conversations["solution"], conversations["solution_without_new_task"]
    assert new.conversation_success > base.conversation_success + 0.3
    assert new.later_turn_accuracy >= 0.9
    # Without the new_task flag, a known contact's email pulls the new topic into the old thread
    assert (
        no_flag.by_kind["same_person_new_topic"]["conversation_success"]
        < new.by_kind["same_person_new_topic"]["conversation_success"]
    )
