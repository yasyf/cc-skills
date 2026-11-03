#!/usr/bin/env python3
import os
import sys
from pathlib import Path

LR = Path(__file__).resolve().parents[3] / "bin" / "lr"
SUPERVISOR_VERBS = ("run", "install", "uninstall")

argv = sys.argv[1:]
os.execv(LR, [str(LR), *(["supervisor"] if argv[:1] and argv[0] in SUPERVISOR_VERBS else []), *argv])
