#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""What desk-runner.py still reads about a fidelity pass: its lane. The pass itself is `lr grade fidelity`."""

from __future__ import annotations

import os
import sys
from pathlib import Path

LANE = "fidelity-pipeline"


if __name__ == "__main__":
    lr = Path(__file__).resolve().parents[3] / "bin" / "lr"
    os.execv(lr, [str(lr), "grade", "fidelity", *sys.argv[1:]])
