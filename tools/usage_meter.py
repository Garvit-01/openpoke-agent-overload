"""Record every model call an app makes: tokens, thinking tokens, seconds and which part of the app made it.

Used by tools/run_mine.py and tools/run_original.py for the side-by-side demo. It wraps the one
function both apps call the model through (`request_chat_completion`), in memory only, after the
app is imported, so neither app's code changes. Each call appends one line to `usage.jsonl` in that
app's own data folder; tools/demo.py adds up the lines for each message.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict

USAGE_FILE = "usage.jsonl"

# Which part of the app made the call, from the calling module's name
PARTS = {
    "interaction_agent": "interaction agent (routing)",
    "execution_agent": "execution agents",
    "phrases": "search phrases",
    "summarization": "conversation summarizer",
    "gmail": "email classifier",
}


def part_of(module: str) -> str:
    return next((label for key, label in PARTS.items() if key in module), module or "other")


def install(data_dir: Path) -> int:
    """Wrap request_chat_completion everywhere the app holds a reference to it. Returns how many."""
    import server.openrouter_client.client as client

    original: Callable[..., Any] = client.request_chat_completion
    path = data_dir / USAGE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    async def metered(*args, **kwargs) -> Dict[str, Any]:
        caller = sys._getframe(1).f_globals.get("__name__", "") if sys._getframe(1) else ""
        started = time.time()
        response = await original(*args, **kwargs)
        usage = response.get("usage") or {}
        record = {
            "ts": started,
            "seconds": round(time.time() - started, 3),
            "part": part_of(caller),
            "model": kwargs.get("model"),
            "prompt_tokens": usage.get("prompt_tokens") or 0,
            "completion_tokens": usage.get("completion_tokens") or 0,
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0,
        }
        with lock, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        return response

    patched = 0
    for name, module in list(sys.modules.items()):
        if (name == "server" or name.startswith("server.")) and getattr(module, "request_chat_completion", None) is original:
            module.request_chat_completion = metered
            patched += 1
    return patched


def read(data_dir: Path, since: float = 0.0) -> list:
    path = data_dir / USAGE_FILE
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [row for row in rows if row["ts"] >= since]


__all__ = ["USAGE_FILE", "install", "part_of", "read"]
