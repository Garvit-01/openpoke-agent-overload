from server.services.execution.resolver import resolve_agent
from server.services.execution.retrieval import MAX_AGENTS_PER_HARD_ENTITY

from .conftest import add_agent


def test_exact_name_is_reused(two_sarahs):
    resolution = resolve_agent("Vercel Job Offer", "accept the offer", two_sarahs)
    assert resolution.agent_name == "Vercel Job Offer"
    assert not resolution.created and resolution.reason == "exact_name"


def test_exact_name_wins_over_entities(two_sarahs):
    # The model explicitly chose this agent; respect it even if another agent owns the address
    resolution = resolve_agent("Vercel Job Offer", "cc sarah.chen@acme.com", two_sarahs)
    assert resolution.agent_name == "Vercel Job Offer"


def test_same_thread_id_reuses_the_thread_owner(two_sarahs):
    resolution = resolve_agent("Venue follow up", "reply on thread_id: 18c2f0a9b1d2e3f4", two_sarahs)
    assert resolution.agent_name == "Sarah venue contract"
    assert resolution.reason.startswith("thread:")
    assert resolution.redirected


def test_same_email_reuses_the_contact_owner(two_sarahs):
    resolution = resolve_agent("Sarah Chen payment", "Tell sarah.chen@acme.com we paid", two_sarahs)
    assert resolution.agent_name == "Email to Sarah"
    assert resolution.reason == "email: sarah.chen@acme.com"


def test_several_agents_on_one_contact_are_ranked_by_keywords(registry):
    add_agent(registry, "Acme invoice", summary="invoice payment", entities={"email": ["ap@acme.com"]}, days_ago=1)
    add_agent(registry, "Acme contract", summary="contract renewal", entities={"email": ["ap@acme.com"]}, days_ago=0)
    resolution = resolve_agent("Acme", "ask ap@acme.com about the invoice payment", registry)
    assert resolution.agent_name == "Acme invoice"
    assert "best of 2" in resolution.reason


def test_new_contact_in_instructions_prevents_folding_into_an_old_thread(two_sarahs):
    # "Email to Sarah" exists, but this Sarah has an address we have never seen
    resolution = resolve_agent("Sarah email", "Email sarah.okafor@newco.com about onboarding", two_sarahs)
    assert resolution.created
    assert resolution.agent_name == "Sarah email"
    assert "Email to Sarah" in resolution.possible_duplicates


def test_reworded_name_is_merged(registry):
    add_agent(registry, "Email to Sarah Chen")
    resolution = resolve_agent("Sarah Chen thread", "did she reply?", registry)
    assert resolution.agent_name == "Email to Sarah Chen"
    assert resolution.reason == "same_name_key"


def test_ambiguous_reworded_name_is_not_merged(registry):
    add_agent(registry, "Email to Sarah")
    add_agent(registry, "Sarah email")
    resolution = resolve_agent("Sarah thread", "ping her", registry)
    assert resolution.created
    assert set(resolution.possible_duplicates) >= {"Email to Sarah", "Sarah email"}


def test_keyword_similarity_alone_never_merges(two_sarahs):
    resolution = resolve_agent("Invoice reminder", "remind me about the Q3 invoice", two_sarahs)
    assert resolution.created
    assert resolution.possible_duplicates == ["Email to Sarah"]


def test_user_own_address_does_not_pull_everything_into_one_agent(registry):
    for index in range(MAX_AGENTS_PER_HARD_ENTITY + 1):
        add_agent(registry, f"Task {index}", entities={"email": ["me@user.com"]})
    resolution = resolve_agent("Dentist", "book a dentist and email me@user.com the time", registry)
    assert resolution.created


def test_completely_new_task_creates_an_agent(two_sarahs):
    resolution = resolve_agent("Dentist appointment", "book a cleaning next week", two_sarahs)
    assert resolution.created and resolution.reason == "new"
    assert resolution.possible_duplicates == []
