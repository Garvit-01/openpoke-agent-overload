"""Run the original OpenPoke next to this branch, without changing a line of its code.

The original is upstream commit 5b5f635 (this repo's `main` branch). Check it out into its own
folder once, then start it with this script:

    git worktree add "../openpoke-original" main
    python tools/run_original.py                  # http://localhost:8002

It makes two runtime-only changes, both needed for the original to start at all today:
- Windows has no `fcntl`, which the original roster imports. A stand-in module that does no file
  locking is put on the import path from a temp folder. Fine for one local demo process.
- The original hard-codes `anthropic/claude-sonnet-4`, which OpenRouter has retired. The model
  names are swapped in memory after import (default: Haiku 5.5, the same as tools/run_mine.py
  and the eval, so both apps in the demo always run on the same model).

API keys are read from this repo's `.env`. The original keeps its own data (roster.json, logs) in
its own folder, so the two apps never share state. `git status` in the original's folder stays clean.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DEFAULT_ORIGINAL = HERE.parent / "openpoke-original"
# One model for both apps in the demo; cheap, and the one the eval results use
DEMO_MODEL = "anthropic/claude-haiku-5.5"

FCNTL_STUB = '''"""Stand-in for Unix fcntl on Windows (added by tools/run_original.py). No real locking."""
LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 4, 8


def flock(fd, operation):
    return None
'''

MODEL_FIELDS = [
    "interaction_agent_model", "execution_agent_model", "execution_agent_search_model",
    "summarizer_model", "email_classifier_model",
]


def load_env(path: Path) -> None:
    """This repo's .env, so the original gets the same keys. Existing variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip("'\"")
            if key and value and key not in os.environ:
                os.environ[key] = value


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", type=Path, default=DEFAULT_ORIGINAL, help="folder with the original checked out")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--model", default=DEMO_MODEL, help="replaces the retired claude-sonnet-4 (use the same as run_mine.py)")
    args = parser.parse_args(argv)

    original = args.path.resolve()
    if not (original / "server" / "app.py").is_file():
        sys.exit(f'No original checkout at {original}. Run: git worktree add "{original}" main')

    load_env(HERE / ".env")

    # The original's own folder first, so `import server` is the original, not this branch
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() not in (HERE, HERE / "tools")]
    sys.path.insert(0, str(original))
    if os.name == "nt":
        stub_dir = Path(tempfile.mkdtemp(prefix="openpoke_fcntl_"))
        (stub_dir / "fcntl.py").write_text(FCNTL_STUB, encoding="utf-8")
        sys.path.insert(0, str(stub_dir))

    from server.app import app  # the original app
    from server.config import get_settings

    settings = get_settings()
    for field in MODEL_FIELDS:
        setattr(settings, field, args.model)
    assert Path(sys.modules["server"].__file__).resolve().is_relative_to(original), "imported the wrong server package"

    # Record tokens and time per model call for tools/demo.py (in memory; the original's code is unchanged)
    sys.path.append(str(HERE / "tools"))  # last, so it can never shadow the original's packages
    import usage_meter

    metered = usage_meter.install(original / "server" / "data")

    import uvicorn

    print(f"Original OpenPoke from {original} (metering {metered} model-call sites)")
    print(f"Model: {args.model}. Data: {original / 'server' / 'data'}. Listening on http://localhost:{args.port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
