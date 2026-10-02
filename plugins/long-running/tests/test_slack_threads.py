from __future__ import annotations

import json
import subprocess
from pathlib import Path

from captain_hook.context import HookContext
from captain_hook.events import PostToolUseEvent
from captain_hook.snapshots.client import EvidenceIncomplete
from captain_hook.testing.helpers import build_context

from hooks import slack_threads
from hooks.slack_threads import CC_SLACK_REPLY, register_posted_thread

SESSION = "900424b6-7393-480c-a26a-f1bd21da6e57"
AGENT = "aowner-links-comms-1a2b"


def exhausted(self):
    raise EvidenceIncomplete("deadline", "foreground transcript deadline exhausted")


def test_a_lane_registers_its_post_after_the_transcript_deadline(tmp_path, monkeypatch):
    transcript = tmp_path / f"{SESSION}.jsonl"
    transcript.write_text("")
    meta = tmp_path / SESSION / "subagents" / f"agent-{AGENT}.meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"name": "owner-links-comms"}))
    raw = {
        "session_id": SESSION,
        "transcript_path": str(transcript),
        "agent_id": AGENT,
        "tool_name": "Bash",
        "tool_input": {"command": "cc-slack reply --channel C0AAAAAAAA1 --thread 1790901035.467689 --text hi"},
        "tool_response": {"stdout": CC_SLACK_REPLY, "stderr": ""},
        "cwd": str(tmp_path),
    }
    evt = PostToolUseEvent(_raw=raw, ctx=build_context(None, transcript, tmp_path, tmp_path))
    monkeypatch.setattr(HookContext, "t", property(exhausted))
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(slack_threads.subprocess, "run", run)

    assert register_posted_thread(evt) is None
    assert [argv[argv.index("--lane") + 1 :] for argv in calls] == [
        [SESSION, "--channel", "C0AAAAAAAA1", "--thread-ts", "1790901035.467689", "--posted-ts", "1790901997.422529"]
    ]
