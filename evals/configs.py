"""The versions evals/compare.py and tools/demo.py compare: main, each approach on its own, and all three.

Kept apart from compare.py because it imports nothing from the app: tools/run_mine.py sets these
in the environment before the app reads its settings.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

OFF = {
    "OPENPOKE_FOLD_PLURALS": "0",
    "OPENPOKE_AGENT_NAME_HINTS": "0",
    "OPENPOKE_AGENT_INDEX_REQUESTS": "0",
    "OPENPOKE_AGENT_RETRIEVER": "bm25",
}
# (key, label, settings on top of OFF)
CONFIGS: List[Tuple[str, str, Dict[str, str]]] = [
    ("main", "agent-overload (main)", {}),
    ("plurals_typos", "+ plurals and typo hints", {"OPENPOKE_FOLD_PLURALS": "1", "OPENPOKE_AGENT_NAME_HINTS": "1"}),
    ("past_requests", "+ past-request search", {"OPENPOKE_AGENT_INDEX_REQUESTS": "1"}),
    ("embeddings", "+ meaning search (hybrid)", {"OPENPOKE_AGENT_RETRIEVER": "hybrid"}),
    ("default", "plurals, typos and past requests (meaning search off)", {
        "OPENPOKE_FOLD_PLURALS": "1", "OPENPOKE_AGENT_NAME_HINTS": "1", "OPENPOKE_AGENT_INDEX_REQUESTS": "1",
    }),
    ("all", "all three together", {
        "OPENPOKE_FOLD_PLURALS": "1", "OPENPOKE_AGENT_NAME_HINTS": "1",
        "OPENPOKE_AGENT_INDEX_REQUESTS": "1", "OPENPOKE_AGENT_RETRIEVER": "hybrid",
    }),
]
