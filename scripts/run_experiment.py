#!/usr/bin/env python3
"""GEOS eval harness CLI — see src/runner/cli.py."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from runner.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
