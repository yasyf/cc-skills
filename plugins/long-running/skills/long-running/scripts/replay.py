#!/usr/bin/env python3
import os
import sys
from pathlib import Path

lr = Path(__file__).resolve().parents[3] / "bin" / "lr"
os.execv(lr, [str(lr), "replay", *sys.argv[1:]])
