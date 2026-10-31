"""Which lane briefs owe a `Scale:` line, read the same way by the Agent spawn hook and the orca desk runner, which each warn and never refuse.

A new lane brief carries one; a handoff writer (`<lane>-handoff`), a successor (`<lane>-<n>` with n >= 2, or a brief
with a `Replaces: <lane>` line), and a brief file last written before the rule existed never owe it, since each
re-spawns work an older brief already scoped. STDLIB ONLY.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

SCALE_HEADER = re.compile(r"^[ \t>*_-]*Scale:", re.MULTILINE)
REPLACES = re.compile(r"^Replaces: `?[\w.-]+`?\s*$", re.MULTILINE)
SUCCESSOR = re.compile(r".+-(\d+)")
HANDOFF = "-handoff"
SCALE_RULE = datetime(2026, 10, 30, 13, 11, 35, tzinfo=timezone.utc)
NO_SCALE = "brief {brief} has no `Scale:` line; add one line to it: `Scale: no N`, or the N its paths run at, such as `Scale: chats on one Mac, up to 1,000`"


def owes_scale(lane: str, text: str, brief: Path | None) -> bool:
    if SCALE_HEADER.search(text) or lane.endswith(HANDOFF) or REPLACES.search(text):
        return False
    if (numbered := SUCCESSOR.fullmatch(lane)) and int(numbered[1]) >= 2:
        return False
    return brief is None or datetime.fromtimestamp(brief.stat().st_mtime, timezone.utc) >= SCALE_RULE


def no_scale(brief: Path | str) -> str:
    return NO_SCALE.format(brief=brief)
