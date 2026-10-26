"""The outbound guard: sends need a draft the user saw and answered."""

from types import SimpleNamespace

import pytest

from server.agents.execution_agent.tools import gmail
from server.agents.interaction_agent import tools as interaction_tools
from server.services.safety.outbound import MODE_OFF, OutboundGuard


class Ticker:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def tick(self, seconds=1.0):
        self.now += seconds


@pytest.fixture
def ticker():
    return Ticker()


@pytest.fixture
def guard(ticker):
    return OutboundGuard(clock=ticker, approval_ttl_seconds=600)


def show_and_confirm(guard, ticker, to):
    guard.note_draft_shown(to)
    ticker.tick()
    guard.note_user_message()
    ticker.tick()


def test_send_without_any_draft_is_blocked(guard):
    decision = guard.check("gmail_reply_to_thread", ["sarah.chen@acme.com"])
    assert not decision.allowed
    assert "sarah.chen@acme.com" in decision.reason


def test_draft_shown_but_not_answered_is_blocked(guard, ticker):
    guard.note_draft_shown("sarah.chen@acme.com")
    ticker.tick()
    assert not guard.check("gmail_reply_to_thread", ["sarah.chen@acme.com"]).allowed


def test_draft_shown_then_user_replied_is_allowed_once(guard, ticker):
    show_and_confirm(guard, ticker, "Sarah Chen <Sarah.Chen@acme.com>")
    assert guard.check("gmail_reply_to_thread", ["sarah.chen@acme.com"]).allowed
    # One confirmation, one send
    assert not guard.check("gmail_reply_to_thread", ["sarah.chen@acme.com"]).allowed


def test_injected_recipient_is_blocked_even_after_a_real_confirmation(guard, ticker):
    show_and_confirm(guard, ticker, "sarah.chen@acme.com")
    decision = guard.check("gmail_forward_email", ["attacker@evil.example"])
    assert not decision.allowed
    assert "attacker@evil.example" in decision.reason


def test_extra_cc_that_was_not_in_the_draft_blocks_the_send(guard, ticker):
    show_and_confirm(guard, ticker, "sarah.chen@acme.com")
    assert not guard.check("gmail_reply_to_thread", ["sarah.chen@acme.com", "attacker@evil.example"]).allowed


def test_confirmation_expires(guard, ticker):
    show_and_confirm(guard, ticker, "sarah.chen@acme.com")
    ticker.tick(601)
    assert not guard.check("gmail_reply_to_thread", ["sarah.chen@acme.com"]).allowed


def test_user_message_before_the_draft_does_not_count(guard, ticker):
    guard.note_user_message()
    ticker.tick()
    guard.note_draft_shown("sarah.chen@acme.com")
    ticker.tick()
    assert not guard.check("gmail_reply_to_thread", ["sarah.chen@acme.com"]).allowed


def test_send_draft_by_id_needs_an_answered_draft(guard, ticker):
    assert not guard.check("gmail_execute_draft", []).allowed
    show_and_confirm(guard, ticker, "sarah.chen@acme.com")
    assert guard.check("gmail_execute_draft", []).allowed
    assert not guard.check("gmail_execute_draft", []).allowed


def test_guard_can_be_disabled(ticker):
    assert OutboundGuard(mode=MODE_OFF, clock=ticker).check("gmail_forward_email", ["x@y.io"]).allowed


def test_gmail_tools_return_an_error_instead_of_sending(monkeypatch, guard):
    sent = []
    monkeypatch.setattr(gmail, "get_outbound_guard", lambda: guard)
    monkeypatch.setattr(gmail, "get_active_gmail_user_id", lambda: "user-1")
    monkeypatch.setattr(gmail, "execute_gmail_tool", lambda *args, **kwargs: sent.append(args) or {"ok": True})
    monkeypatch.setattr(gmail._LOG_STORE, "record_action", lambda *args, **kwargs: None)

    result = gmail.gmail_forward_email(message_id="m1", recipient_email="attacker@evil.example")

    assert "blocked" in result["error"]
    assert sent == []


def test_gmail_tools_send_after_confirmation(monkeypatch, guard, ticker):
    sent = []
    monkeypatch.setattr(gmail, "get_outbound_guard", lambda: guard)
    monkeypatch.setattr(gmail, "get_active_gmail_user_id", lambda: "user-1")
    monkeypatch.setattr(gmail, "execute_gmail_tool", lambda *args, **kwargs: sent.append(args) or {"ok": True})
    monkeypatch.setattr(gmail._LOG_STORE, "record_action", lambda *args, **kwargs: None)

    show_and_confirm(guard, ticker, "sarah.chen@acme.com")
    result = gmail.gmail_reply_to_thread(
        thread_id="18c2f0a9b1d2e3f4", recipient_email="sarah.chen@acme.com", message_body="Thursday works"
    )

    assert result == {"ok": True}
    assert sent[0][0] == "GMAIL_REPLY_TO_THREAD"


def test_interaction_send_draft_tool_registers_the_recipients(monkeypatch, guard, ticker):
    monkeypatch.setattr(interaction_tools, "get_outbound_guard", lambda: guard)
    monkeypatch.setattr(interaction_tools, "get_conversation_log", lambda: SimpleNamespace(record_reply=lambda _m: None))

    interaction_tools.send_draft(to="sarah.chen@acme.com", subject="Re: Q3 invoice", body="Thursday works")
    ticker.tick()
    guard.note_user_message()
    ticker.tick()

    assert guard.check("gmail_reply_to_thread", ["sarah.chen@acme.com"]).allowed
