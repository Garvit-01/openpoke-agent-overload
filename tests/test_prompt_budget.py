"""The roster section of the interaction prompt must stay the same size as agents pile up."""

from datetime import timedelta

from server.agents.interaction_agent.agent import render_agent_shortlist
from server.services.execution.registry import AgentRecord, to_iso
from server.services.execution.retrieval import AgentRetriever

from .conftest import add_agent


def baseline_render(names):
    """The original OpenPoke rendering: every agent name, every turn."""
    return "\n".join(f'<agent name="{name}" />' for name in names)


def fill(registry, count):
    stamp = registry.now()
    registry.import_records(
        AgentRecord(
            name=f"Email to Contact {index}",
            summary=f"Task: follow up with contact {index} about project {index % 17} and the shared budget sheet",
            entities={"email": [f"contact{index}@example.com"]},
            created_at=to_iso(stamp - timedelta(days=index % 90)),
            last_active_at=to_iso(stamp - timedelta(days=index % 90)),
        )
        for index in range(registry.count(), count)
    )


def test_shortlist_size_is_fixed_as_the_roster_grows(registry):
    sizes = {}
    for count in (20, 200, 1000):
        fill(registry, count)
        shortlist = AgentRetriever(registry).shortlist("any update on project 3 budget?", retrieved=8, recent=4)
        rendered = render_agent_shortlist(shortlist, total=registry.count(), summary_chars=200)
        assert len(shortlist) == 12
        assert f'total="{count}"' in rendered
        sizes[count] = len(rendered)

    # 50x more agents, but the section only varies by the length of the names/numbers shown
    assert max(sizes.values()) <= min(sizes.values()) * 1.25


def test_baseline_grows_linearly(registry):
    fill(registry, 1000)
    names = registry.names()
    assert len(baseline_render(names[:1000])) > 9 * len(baseline_render(names[:100]))


def test_summaries_are_truncated_and_escaped(registry):
    add_agent(registry, 'Quote "test" <agent>', summary="x" * 500 + " <tag>")
    shortlist = AgentRetriever(registry).recent(limit=1)
    rendered = render_agent_shortlist(shortlist, total=1, summary_chars=50)
    assert 'name="Quote &quot;test&quot; &lt;agent&gt;"' in rendered
    assert "x" * 48 not in rendered
    assert "<tag>" not in rendered


def test_empty_roster_renders_none():
    assert render_agent_shortlist([], total=0) == '<active_agents total="0" shown="0">\nNone\n</active_agents>'
