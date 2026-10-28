"""Run the Aria demo server: ``python demo/run.py`` → uvicorn on :8000.

Single worker (the demo shares one SQLite DB / one LanceDB process).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure ``import app.*`` works from the demo directory.
DEMO = Path(__file__).resolve().parent
if str(DEMO) not in sys.path:
    sys.path.insert(0, str(DEMO))

import uvicorn  # noqa: E402

from app.main import create_app  # noqa: E402


def main() -> None:
    host = os.environ.get("ARIA_HOST", "0.0.0.0")
    port = int(os.environ.get("ARIA_PORT", "8000"))
    app = create_app()
    uvicorn.run(app, host=host, port=port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
