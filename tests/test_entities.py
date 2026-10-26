from server.services.execution.entities import (
    extract_entities,
    merge_entities,
    normalize_agent_name,
    tokenize,
)


def test_extracts_emails_lowercased_and_deduplicated():
    entities = extract_entities("Reply to Sarah.Chen@Acme.com and cc sarah.chen@acme.com, bob@x.io")
    assert entities["email"] == ["sarah.chen@acme.com", "bob@x.io"]


def test_thread_id_needs_a_label_so_random_hex_is_ignored():
    assert extract_entities('{"thread_id": "18C2F0A9B1D2E3F4"}')["thread"] == ["18c2f0a9b1d2e3f4"]
    assert "thread" not in extract_entities("commit 18c2f0a9b1d2e3f4 fixed it")


def test_extracts_subject_and_person():
    entities = extract_entities('Email to Sarah Chen, subject: "Q3 invoice dispute"')
    assert entities["subject"] == ["Q3 invoice dispute"]
    assert "Sarah Chen" in entities["person"]


def test_normalized_name_ignores_filler_words_and_order():
    assert normalize_agent_name("Email to Sarah Chen") == normalize_agent_name("Sarah Chen thread")
    assert normalize_agent_name("Email to Sarah Chen") != normalize_agent_name("Email to Sarah Patel")
    assert normalize_agent_name("Email") == ""


def test_tokenize_drops_stopwords_and_single_characters():
    assert tokenize("Did Sarah reply to the invoice? a b") == ["sarah", "reply", "invoice"]


def test_person_names_do_not_span_lines():
    assert extract_entities("Email to Sarah Chen\nReply soon")["person"] == ["Sarah Chen"]


def test_merge_entities_keeps_order_and_removes_duplicates():
    merged = merge_entities({"email": ["a@x.io"]}, {"email": ["b@x.io", "a@x.io"], "person": ["Al"]})
    assert merged == {"email": ["a@x.io", "b@x.io"], "person": ["Al"]}
