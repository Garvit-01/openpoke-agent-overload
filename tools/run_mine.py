"""Run this branch's server for the side-by-side demo, on the same model as tools/run_original.py.

    python tools/run_mine.py                 # http://localhost:8001, Haiku 5.5 for every role
    python tools/run_mine.py --model anthropic/claude-sonnet-5.5

Same as `python -m server.server`, except every model role (and the search-phrase writer) is
set to one model, so the demo compares the two apps on equal terms. The app's own defaults are
unchanged.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_original import DEMO_MODEL  # noqa: E402  (one model for both apps)

MODEL_VARIABLES = [
    "OPENPOKE_INTERACTION_MODEL", "OPENPOKE_EXECUTION_MODEL", "OPENPOKE_SEARCH_MODEL",
    "OPENPOKE_SUMMARIZER_MODEL", "OPENPOKE_CLASSIFIER_MODEL", "OPENPOKE_PHRASES_MODEL",
]


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--model", default=DEMO_MODEL, help="use the same as run_original.py")
    args = parser.parse_args(argv)

    # Settings read these when server.config is first imported, so set them before importing the app
    for variable in MODEL_VARIABLES:
        os.environ[variable] = args.model
    sys.path.insert(0, str(HERE))

    from server.app import app
    from server.config import get_settings

    settings = get_settings()
    assert settings.interaction_agent_model == args.model, "model setting didn't take effect"

    import uvicorn

    print(f"This branch from {HERE}")
    print(f"Model: {args.model}. Data: {HERE / 'server' / 'data'}. Listening on http://localhost:{args.port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
