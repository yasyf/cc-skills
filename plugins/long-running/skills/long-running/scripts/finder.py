#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""`sprite-job.sh` runs a grader by its script path. The pass itself is `lr grade finder`."""

from __future__ import annotations

import os
import sys
from pathlib import Path

if __name__ == "__main__":
    lr = Path(__file__).resolve().parents[3] / "bin" / "lr"
    os.execv(lr, [str(lr), "grade", "finder", *sys.argv[1:]])
