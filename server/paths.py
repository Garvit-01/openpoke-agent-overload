"""Where the app keeps its data: server/data, or the folder in OPENPOKE_DATA_DIR.

tools/demo.py sets OPENPOKE_DATA_DIR to run several copies of the app side by side, one per setting.
"""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.getenv("OPENPOKE_DATA_DIR") or Path(__file__).resolve().parent / "data")
