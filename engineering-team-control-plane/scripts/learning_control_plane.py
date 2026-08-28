#!/usr/bin/env python3
"""Source-tree launcher; production installs the package before invocation."""
from __future__ import annotations
import sys
from pathlib import Path
SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))
from learning_control_plane.cli import main  # noqa: E402
if __name__ == "__main__": raise SystemExit(main())
