"""The side-by-side demo tools: they must read the original's files the way the original writes them."""

import importlib.util
import json
from pathlib import Path

from server.services.execution.log_store import _slugify

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_names_logs_like_the_original():
    # The original names log files by slug; this branch kept _slugify only to adopt those files
    demo = load("demo")
    for name in ["Email to Sarah", "Reminder: pay rent", "Reply to Michael re venue contract", "  ?? "]:
        assert demo.slugify(name) == _slugify(name)


def test_both_demo_apps_default_to_the_same_cheap_model():
    import sys

    sys.path.insert(0, str(TOOLS))
    original, mine = load("run_original"), load("run_mine")
    assert original.DEMO_MODEL == mine.DEMO_MODEL == "anthropic/claude-haiku-5.5"
    assert "OPENPOKE_INTERACTION_MODEL" in mine.MODEL_VARIABLES and "OPENPOKE_PHRASES_MODEL" in mine.MODEL_VARIABLES


def test_fcntl_stand_in_has_what_the_original_uses():
    namespace = {}
    exec(load("run_original").FCNTL_STUB, namespace)
    assert namespace["flock"](3, namespace["LOCK_EX"] | namespace["LOCK_NB"]) is None
    assert {"LOCK_EX", "LOCK_NB", "LOCK_UN"} <= namespace.keys()


def test_original_state_reads_roster_and_new_logs(tmp_path):
    demo = load("demo")
    folder = tmp_path / "server" / "data" / "execution_agents"
    folder.mkdir(parents=True)
    (folder / "roster.json").write_text(json.dumps(["Email to Sarah", "Email to Sarah Chen"]), encoding="utf-8")
    (folder / "email-to-sarah.log").write_text("x", encoding="utf-8")
    roster, logs = demo.original_state(tmp_path)
    assert roster == ["Email to Sarah", "Email to Sarah Chen"]
    assert set(logs) == {"email-to-sarah"}


def test_usage_meter_records_every_call(tmp_path, monkeypatch):
    import asyncio
    import sys

    import server.agents.interaction_agent.runtime as interaction_runtime
    import server.openrouter_client.client as client

    sys.path.insert(0, str(TOOLS))
    meter = load("usage_meter")

    async def fake(**kwargs):
        return {"usage": {"prompt_tokens": 9854, "completion_tokens": 461,
                          "completion_tokens_details": {"reasoning_tokens": 178}}}

    # Two places hold the function, like in the real app; monkeypatch restores both afterwards
    monkeypatch.setattr(client, "request_chat_completion", fake)
    monkeypatch.setattr(interaction_runtime, "request_chat_completion", fake)
    assert meter.install(tmp_path) == 2

    asyncio.run(interaction_runtime.request_chat_completion(model="m", messages=[]))
    rows = meter.read(tmp_path)
    assert len(rows) == 1
    assert (rows[0]["prompt_tokens"], rows[0]["completion_tokens"], rows[0]["reasoning_tokens"]) == (9854, 461, 178)
    assert rows[0]["model"] == "m"
    assert meter.read(tmp_path, since=rows[0]["ts"] + 1) == []
    assert meter.part_of("server.agents.interaction_agent.runtime") == "interaction agent (routing)"
    assert meter.part_of("server.services.execution.phrases") == "search phrases"


def test_usage_cost_and_time_lines():
    demo = load("demo")
    rows = [
        {"model": "m", "part": "interaction agent (routing)", "prompt_tokens": 1_000_000, "completion_tokens": 0, "reasoning_tokens": 0},
        {"model": "m", "part": "execution agents", "prompt_tokens": 0, "completion_tokens": 100_000, "reasoning_tokens": 10},
    ]
    text = "\n".join(demo.usage_lines(rows, {"m": (0.1e-6, 0.5e-6)}, 12.34))
    assert "cost: $0.1500" in text and "model calls: 2" in text and "time to final reply: 12.3s" in text
    assert "interaction agent (routing): 1 call(s), 1,000,000 in, 0 out, $0.1000" in text
    assert "no model calls recorded" in "\n".join(demo.usage_lines([], {}, 1.0))


def test_routing_comparison_is_per_call():
    # The original made 4 routing calls and this branch 1: the headline ratio must not count that as savings
    demo = load("demo")
    routing = "interaction agent (routing)"
    original = [{"part": routing, "prompt_tokens": 14_000}] * 4 + [{"part": "execution agents", "prompt_tokens": 9_999}]
    ours = [{"part": routing, "prompt_tokens": 10_000}]
    text = "\n".join(demo.routing_lines(original, ours))
    assert "per call: original 14,000 vs this branch 10,000 (1.4x)" in text
    assert "original 56,000 over 4 call(s) vs this branch 10,000 over 1 call(s)" in text
    assert demo.routing_lines(original, []) == []


def test_ask_stops_after_a_single_direct_reply(monkeypatch):
    import time

    import httpx

    demo = load("demo")
    monkeypatch.setattr(demo, "POLL_SECONDS", 0.02)
    monkeypatch.setattr(demo, "QUIET_AFTER_ONE_REPLY", 0.2)
    sent_at = {}
    conversation = []

    def fake_history(base):
        # The app answers directly, with one reply, 0.1s after the message arrives
        if sent_at and time.time() - sent_at["t"] > 0.1 and len(conversation) == 1:
            conversation.append({"role": "assistant", "content": "Who is Sarha?"})
        return list(conversation)

    def fake_post(url, json, timeout):
        sent_at["t"] = time.time()
        conversation.append({"role": "user", "content": json["messages"][0]["content"]})

    monkeypatch.setattr(demo, "history", fake_history)
    monkeypatch.setattr(httpx, "post", fake_post)

    started = time.time()
    replies, error, seconds = demo.send_and_wait("http://app", "send it to Sarha", timeout=30)
    assert replies == ["Who is Sarha?"] and error is None
    # Generous upper bounds: the suite also runs while evals load the machine
    assert time.time() - started < 5, "didn't wait for a second reply that never comes"
    assert 0.05 < seconds < 1.5, "time to the reply, roughly"


def test_move_aside_never_deletes(tmp_path):
    demo = load("demo")
    data = tmp_path / "data"
    (data / "execution_agents").mkdir(parents=True)
    (data / "execution_agents" / "registry.db").write_text("keep me", encoding="utf-8")
    backup = demo.move_aside(data)
    assert not data.exists()
    assert (backup / "execution_agents" / "registry.db").read_text(encoding="utf-8") == "keep me"
    assert demo.move_aside(tmp_path / "missing") is None


def test_data_folder_setting_moves_every_store(tmp_path):
    # Several copies of the app run side by side only if none of them writes into server/data
    import os
    import subprocess
    import sys

    code = (
        "from server.services.execution import roster, routing; from server.services import timezone_store; "
        "import server.services.triggers as triggers; from server.services.conversation import log; "
        "print(roster._REGISTRY_PATH, routing._ROUTING_LOG_PATH, timezone_store._TIMEZONE_PATH, "
        "triggers._default_db_path, log._CONVERSATION_LOG_PATH, sep=chr(10))"
    )
    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "OPENPOKE_DATA_DIR": str(tmp_path)}
    result = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    paths = result.stdout.splitlines()
    assert len(paths) == 5 and all(Path(path).is_relative_to(tmp_path) for path in paths)


def test_variants_get_their_own_port_and_data():
    import argparse

    from evals.configs import CONFIGS

    demo = load("demo")
    assert set(demo.VARIANT_PORTS) == {key for key, _, _ in CONFIGS}
    apps = demo.apps_for(argparse.Namespace(variants="every"))
    assert len({app.url for app in apps}) == len({app.data for app in apps}) == len(CONFIGS)
    assert all(app.data != demo.OURS_DATA for app in apps)
    assert [app.label for app in demo.apps_for(argparse.Namespace(variants="main,all"))] == ["agent-overload (main)", "all three together"]
    assert demo.apps_for(argparse.Namespace(variants=None))[0].settings is None


def test_demo_scores_new_and_existing_answers():
    from evals.scenarios import NEW

    demo = load("demo")
    assert demo.is_right("Email to Sarah Okafor", [("email to sarah okafor", False)]) is True
    assert demo.is_right("Email to Sarah Okafor", [("Email to Sarah Murphy", False)]) is False
    assert demo.is_right(NEW, [("Email to Sarah Lee", True)]) is True
    assert demo.is_right(NEW, [("Email to Sarah Okafor", False)]) is False
    assert demo.is_right("Email to Sarah Okafor", []) is False
    assert demo.is_right(None, [("anything", False)]) is None
    text = "\n".join(demo.score_lines([{"original": False, "all": True}, {"original": True, "all": True}, {"original": None, "all": None}], "x"))
    assert "original  1/2" in text and "all       2/2" in text
