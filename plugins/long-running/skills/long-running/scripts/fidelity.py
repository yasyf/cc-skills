#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""What desk-runner.py still reads about a fidelity pass: its lane and its job's file names. The pass itself is `lr grade fidelity`, which this script execs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

LANE = "fidelity-pipeline"
JOB = "job.json"
SOURCES = "sources.md"
LOG = "job.log"
READER = "fidelity: reader"


if __name__ == "__main__":
    lr = Path(__file__).resolve().parents[3] / "bin" / "lr"
    os.execv(lr, [str(lr), "grade", "fidelity", *sys.argv[1:]])
