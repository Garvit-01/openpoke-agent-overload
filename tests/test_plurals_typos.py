"""A failure from the live demo with 300 agents.

"send the updated groceries" didn't find "Grocery list": plurals weren't folded.
"""

import sqlite3

from server.services.execution.entities import extract_entities, fold_plural, normalize_agent_name, tokenize
from server.services.execution.resolver import resolve_agent
from server.services.execution.retrieval import AgentRetriever
from server.services.execution.registry import AgentRegistry

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
