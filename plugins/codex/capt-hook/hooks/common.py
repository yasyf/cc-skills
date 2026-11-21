from __future__ import annotations

import json
from pathlib import Path

from captain_hook import BaseHookEvent

__capt_hook_skip__ = True

TEAMMATE = "in_process_teammate"


def subagent_meta(evt: BaseHookEvent) -> dict:
    agent_id, transcript = evt._raw.get("agent_id"), evt._raw.get("transcript_path")
    if not agent_id or not transcript:
        return {}
    meta = Path(transcript).with_suffix("") / "subagents" / f"agent-{agent_id}.meta.json"
    return json.loads(meta.read_text()) if meta.is_file() else {}


def in_process_teammate(evt: BaseHookEvent) -> bool:
    return subagent_meta(evt).get("taskKind") == TEAMMATE


def spawned_agent_type(evt: BaseHookEvent) -> str | None:
    return subagent_meta(evt).get("spawnedAgentType")
