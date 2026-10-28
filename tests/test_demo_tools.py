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


def test_move_aside_never_deletes(tmp_path):
    demo = load("demo")
    data = tmp_path / "data"
    (data / "execution_agents").mkdir(parents=True)
    (data / "execution_agents" / "registry.db").write_text("keep me", encoding="utf-8")
    backup = demo.move_aside(data)
    assert not data.exists()
    assert (backup / "execution_agents" / "registry.db").read_text(encoding="utf-8") == "keep me"
    assert demo.move_aside(tmp_path / "missing") is None
