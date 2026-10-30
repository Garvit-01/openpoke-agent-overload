"""Run this branch's server for the side-by-side demo, on the same model as tools/run_original.py.

    python tools/run_mine.py                 # http://localhost:8001, Haiku 5.5 for every role
    python tools/run_mine.py --model anthropic/claude-sonnet-5.5
    python tools/run_mine.py --variant past_requests   # one version from evals/configs.py, own port and data

Same as `python -m server.server`, except every model role (and the search-phrase writer) is
set to one model, so the demo compares the two apps on equal terms. The app's own defaults are
unchanged.

--variant runs one version (main, plurals_typos, past_requests, embeddings, default or all) with only its
settings on, on its own port (8011 to 8016) and its own data folder (server/data_variants/<name>),
so several can run at once. tools/demo.py --variants talks to all of them.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo import VARIANT_PORTS, variant_data  # noqa: E402
from run_original import DEMO_MODEL  # noqa: E402  (one model for both apps)

MODEL_VARIABLES = [
    "OPENPOKE_INTERACTION_MODEL", "OPENPOKE_EXECUTION_MODEL", "OPENPOKE_SEARCH_MODEL",
    "OPENPOKE_SUMMARIZER_MODEL", "OPENPOKE_CLASSIFIER_MODEL", "OPENPOKE_PHRASES_MODEL",
]


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, help="default 8001, or the variant's own port")
    parser.add_argument("--model", default=DEMO_MODEL, help="use the same as run_original.py")
    parser.add_argument("--variant", choices=list(VARIANT_PORTS), help="run one version with its own port and data")
    args = parser.parse_args(argv)

    # Settings read these when server.config is first imported, so set them before importing the app
    for variable in MODEL_VARIABLES:
        os.environ[variable] = args.model
    sys.path.insert(0, str(HERE))
    data = HERE / "server" / "data"
    if args.variant:
        from evals.configs import CONFIGS, OFF

        os.environ.update({**OFF, **next(values for key, _, values in CONFIGS if key == args.variant)})
        data = variant_data(args.variant)
        os.environ["OPENPOKE_DATA_DIR"] = str(data)
    port = args.port or (VARIANT_PORTS[args.variant] if args.variant else 8001)

    from server.app import app
    from server.config import get_settings

    settings = get_settings()
    assert settings.interaction_agent_model == args.model, "model setting didn't take effect"

    # Record tokens and time per model call for tools/demo.py, the same way as for the original
    import usage_meter

    metered = usage_meter.install(data)

    import uvicorn

    from server.paths import DATA_DIR

    assert DATA_DIR == data, "data folder setting didn't take effect"
    print(f"This branch from {HERE} (metering {metered} model-call sites)")
    if args.variant:
        print(f"Version: {args.variant}. Retriever {settings.agent_retriever}, plurals {os.environ['OPENPOKE_FOLD_PLURALS']}, "
              f"name hints {settings.agent_name_hints}, past requests {settings.agent_index_requests}")
    print(f"Model: {args.model}. Data: {data}. Listening on http://localhost:{port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
