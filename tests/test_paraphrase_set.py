"""The hand-written paraphrase set must not be passable by keyword overlap."""

import pytest

from evals.paraphrase_set import HANDWRITTEN, TOPIC_ONLY, build_paraphrase_scenario
from server.services.execution.entities import tokenize


@pytest.fixture(scope="module")
def scenario():
    return build_paraphrase_scenario(300, seed=7)


def test_messages_share_no_content_words_with_their_agent(scenario):
    agents = {agent.name: agent for agent in scenario.agents}
    for item, case in zip(HANDWRITTEN, scenario.cases):
        agent = agents[case.gold]
        agent_words = set(tokenize(" ".join([agent.name, agent.summary, *sum(agent.entities.values(), [])])))
        shared = set(tokenize(case.message)) & agent_words - {item.first.lower()}
        assert not shared, f"{case.message!r} shares {shared} with {agent.name!r}"


def test_person_is_named_only_where_the_category_says_so(scenario):
    for item, case in zip(HANDWRITTEN, scenario.cases):
        named = item.first.lower() in tokenize(case.message)
        assert named == item.names_person, case.message
        assert (case.category == TOPIC_ONLY) == (not item.names_person)


def test_topics_are_new_and_labels_point_at_one_agent(scenario):
    generated_topics = {agent.topic for agent in scenario.agents if agent.name not in {i.name for i in HANDWRITTEN}}
    assert not {item.topic for item in HANDWRITTEN} & generated_topics
    names = [agent.name for agent in scenario.agents]
    assert len(names) == len(set(names))
    assert len(scenario.cases) == 24 and len(scenario.agents) == 324


def test_first_names_collide_with_generated_agents(scenario):
    # The point is realism: "Sarah" should not be a giveaway
    generated_people = " ".join(
        " ".join(agent.entities.get("person", [])) for agent in scenario.agents
        if agent.name not in {item.name for item in HANDWRITTEN}
    ).lower().split()
    colliding = [item.first for item in HANDWRITTEN if item.names_person and item.first.lower() in generated_people]
    assert len(colliding) >= 10
