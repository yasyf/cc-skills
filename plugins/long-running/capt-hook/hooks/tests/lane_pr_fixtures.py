from __future__ import annotations

import json
from pathlib import Path

LANE = "inc-liam-load-opus"
AGENT = f"a{LANE}-0123456789abcdef"
DRIVE = {"CLAUDE_LONG_RUNNING_DRIVE": "900424b6", "CLAUDE_CONFIG_DIR": str(Path(__file__).parent / "fixtures" / "incident-lane-config")}
TAIL = "cci tail --drive release-v3 --lane"
STATUS = "git -C"


def record(seq: int, kind: str, text: str) -> str:
    return json.dumps({"seq": seq, "drive": "release-v3", "lane": LANE, "kind": kind, "text": text})


MECHANISM = record(107495, "mechanism", "liam-load: the fork reads the parent through the child's grant")
OPENED = record(107511, "opened", "opened #41461")
BLOCKER = record(107512, "state", "BLOCKER the grant needs an owner ruling")
