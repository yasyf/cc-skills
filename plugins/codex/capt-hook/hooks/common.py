from __future__ import annotations

import json
from pathlib import Path

from captain_hook import BaseHookEvent

__capt_hook_skip__ = True

TEAMMATE = "in_process_teammate"


def in_process_teammate(evt: BaseHookEvent) -> bool:
    agent_id, transcript = evt._raw.get("agent_id"), evt._raw.get("transcript_path")
    if not agent_id or not transcript:
        return False
    meta = Path(transcript).with_suffix("") / "subagents" / f"agent-{agent_id}.meta.json"
    return meta.is_file() and json.loads(meta.read_text()).get("taskKind") == TEAMMATE
