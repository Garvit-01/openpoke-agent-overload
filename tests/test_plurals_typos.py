"""Two failures from the live demo with 300 agents.

1. "send the updated groceries" didn't find "Grocery list": plurals weren't folded.
2. "send the updated grocery to Sarha" found nothing: no contact is called Sarha, and the
   model wasn't told it was probably one of the four Sarahs. It should ask which one.
"""

import sqlite3

from server.agents.interaction_agent import agent as interaction_agent
from server.services.execution.entities import extract_entities, fold_plural, normalize_agent_name, tokenize
from server.services.execution.resolver import resolve_agent
from server.services.execution.retrieval import AgentRetriever, with_suggested_names
from server.services.execution.registry import AgentRegistry
from server.services.execution.roster import set_agent_registry

from .conftest import add_agent

SARAHS = [
    ("Okafor", "job offer"),
    ("Murphy", "tax documents"),
    ("Dubois", "venue booking"),
    ("Evans", "school fundraiser"),
]


def four_sarahs(registry):
    for days, (last, topic) in enumerate(SARAHS, start=1):
        add_agent(
            registry,
            f"Email to Sarah {last}",
            summary=f"Task: reply to Sarah {last} about the {topic}",
            entities={"person": [f"Sarah {last}"], "subject": [f"Re: {topic}"]},
            days_ago=days,
        )
    add_agent(registry, "Grocery list", summary="Task: keep the weekly grocery list and send it when asked")
    add_agent(registry, "Email to Grace Kim", summary="Task: reply to Grace Kim about the lease",
              entities={"person": ["Grace Kim"]}, days_ago=9)
    add_agent(registry, "Reminder: renew car insurance", summary="Task: remind the user to renew car insurance")
    return registry


# ------------------------------------------------------------------------- plurals


def test_plurals_fold_to_the_singular():
    pairs = {
        "groceries": "grocery", "lists": "list", "boxes": "box", "matches": "match", "wishes": "wish",
        "taxes": "tax", "invoices": "invoice", "cases": "case", "licenses": "license", "sizes": "size",
        "addresses": "address", "classes": "class", "houses": "house", "replies": "reply",
    }
    for plural, singular in pairs.items():
        assert fold_plural(plural) == singular, plural
        assert fold_plural(singular) == singular, singular


def test_words_that_only_look_plural_are_left_alone():
    for word in ["address", "status", "analysis", "bus", "news", "series", "gas", "yes", "q3s", "this"]:
        assert fold_plural(word) == word


def test_groceries_finds_the_grocery_list_and_back(registry):
    four_sarahs(registry)
    add_agent(registry, "Party snacks", summary="Task: order the chips and dips for the party")
    retriever = AgentRetriever(registry)
    assert retriever.search("send the updated groceries", limit=1)[0].name == "Grocery list"
    assert tokenize("the grocery lists") == tokenize("grocery list")
    hit = retriever.search("what snack did we order", limit=1)[0]
    assert hit.name == "Party snacks"
    assert "snack" in hit.reasons[0].removeprefix("keywords: ").split(", "), "reasons show a readable word"


def test_entity_values_are_not_folded(registry):
    entities = extract_entities("Reply to orders@shops.com about the Lucas invoices")
    assert entities["email"] == ["orders@shops.com"]
    add_agent(registry, "Shop orders", entities={"email": ["orders@shops.com"]})
    assert AgentRetriever(registry).search("did orders@shops.com reply?", limit=1)[0].reasons[0] == "email: orders@shops.com"


def test_people_are_matched_on_unfolded_names(registry):
    add_agent(registry, "Email to Luca Rossi", entities={"person": ["Luca Rossi"]})
    add_agent(registry, "Email to Lucas Martin", entities={"person": ["Lucas Martin"]})
    names = [record.name for record in AgentRetriever(registry).named_people_agents("did Lucas reply?")]
    assert names == ["Email to Lucas Martin"]


def test_plural_rewording_of_a_name_reuses_the_agent(registry):
    assert normalize_agent_name("Grocery lists") == normalize_agent_name("grocery list")
    assert normalize_agent_name("Email to Sarah re invoices") == normalize_agent_name("Sarah invoice email")
    assert normalize_agent_name("Grocery list") != normalize_agent_name("Grocery budget")
    add_agent(registry, "Grocery list")
    resolution = resolve_agent("Grocery lists", "add milk", registry)
    assert (resolution.agent_name, resolution.reason) == ("Grocery list", "same_name_key")


def test_old_name_keys_are_refreshed_when_the_registry_opens(tmp_path):
    path = tmp_path / "registry.db"
    registry = AgentRegistry(path)
    add_agent(registry, "Grocery lists")
    with sqlite3.connect(path) as conn:  # the key as 104bc97 stored it, before plurals were folded
        conn.execute("UPDATE agents SET name_key = 'grocery lists'")
    assert AgentRegistry(path).find_by_name_key("grocery list") == ["Grocery lists"]


# --------------------------------------------------------------------------- typos


def test_misspelt_name_suggests_every_sarah(registry):
    four_sarahs(registry)
    suggestions = AgentRetriever(registry).suggest_names("send the updated grocery to Sarha")
    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert (suggestion.typed, suggestion.name) == ("Sarha", "Sarah")
    assert sorted(suggestion.people) == sorted(f"Sarah {last}" for last, _ in SARAHS)
    assert suggestion.hint() == (
        '"Sarha" isn\'t a known contact. Closest: Sarah (Sarah Okafor, Sarah Murphy, Sarah Dubois, '
        "Sarah Evans). Ask the user which one they mean before acting."
    )


def test_lowercase_typo_after_to_also_counts(registry):
    four_sarahs(registry)
    assert [s.name for s in AgentRetriever(registry).suggest_names("send the list to sarha")] == ["Sarah"]


def test_suggested_name_reaches_retrieval_and_named_people(registry):
    four_sarahs(registry)
    retriever = AgentRetriever(registry)
    message = "send the updated grocery to Sarha"
    assert retriever.named_people_agents(message) == []
    query = with_suggested_names(message, retriever.suggest_names(message))
    names = {record.name for record in retriever.named_people_agents(query)}
    assert names == {f"Email to Sarah {last}" for last, _ in SARAHS}


def test_exact_names_and_ordinary_words_do_not_trigger(registry):
    four_sarahs(registry)
    retriever = AgentRetriever(registry)
    for message in [
        "send the updated grocery to Sarah",
        "send the updated grocery to Sarah Okafor",
        "Trace the package and tell me when it lands",  # "trace" is close to Grace, but not a name here
        "did the parcel trace come back?",
        "what is the status of the car insurance?",
        "send the Grocery list",  # capitalised, but a word the agents already use
        "send it to Sam",  # too short to guess at
    ]:
        assert retriever.suggest_names(message) == [], message


def test_hint_is_in_the_per_turn_prompt(registry, monkeypatch):
    four_sarahs(registry)
    set_agent_registry(registry)
    monkeypatch.setattr(interaction_agent, "maybe_apply_lifecycle", lambda **kwargs: None)
    try:
        content = interaction_agent.prepare_message_with_history("send the updated grocery to Sarha", "")[0]["content"]
    finally:
        set_agent_registry(None)
    assert "<contact_name_hints>" in content
    assert '"Sarha" isn\'t a known contact. Closest: Sarah (' in content
    assert content.index("<contact_name_hints>") < content.index("<new_user_message>")
    for last, topic in SARAHS:
        assert f"Email to Sarah {last}" in content and topic in content
    assert "Sarha" not in interaction_agent.build_system_prompt()
    assert "did you mean" in interaction_agent.build_system_prompt().lower()


def test_no_hint_block_when_names_are_known(registry, monkeypatch):
    four_sarahs(registry)
    set_agent_registry(registry)
    monkeypatch.setattr(interaction_agent, "maybe_apply_lifecycle", lambda **kwargs: None)
    try:
        content = interaction_agent.prepare_message_with_history("send the grocery list to Sarah Okafor", "")[0]["content"]
    finally:
        set_agent_registry(None)
    assert "<contact_name_hints>" not in content
