from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from captain_hook.events import PreToolUseEvent
from captain_hook.testing.helpers import build_context

from fire import fire
from hooks import lane_landing
from hooks.compaction_handoff import CompactionState

SESSION = "900424b6-7393-480c-a26a-f1bd21da6e57"
ENQUEUE = "stack-enqueue 36023 --hold 30082"


def subagent(tmp_path: Path, agent_id: str, **meta: str) -> None:
    subagents = tmp_path / SESSION / "subagents"
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta))
    (subagents / f"agent-{agent_id}.jsonl").write_text("")


def run(tmp_path: Path, agent_id: str, command: str = ENQUEUE, *, active: bool = True) -> list[str]:
    transcript = tmp_path / f"{SESSION}.jsonl"
    transcript.write_text("")
    lane = tmp_path / SESSION / "subagents" / f"agent-{agent_id}.jsonl"
    raw = {"session_id": SESSION, "transcript_path": str(transcript), "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path), "agent_id": agent_id}
    state = tmp_path / "state"
    evt = PreToolUseEvent(_raw=raw, ctx=build_context(None, lane, tmp_path, state))
    CompactionState(active=active).save(evt)
    return [result.message for result in fire(lane_landing, evt)]


@pytest.fixture(autouse=True)
def no_lane_env(monkeypatch):
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)


def test_a_named_lane_self_enqueue_is_refused_and_names_the_landing_desk(tmp_path):
    subagent(tmp_path, "a1f2e3d4", name="L28-ssql-session-target", agentType="long-running:lane-ship")

    assert run(tmp_path, "a1f2e3d4") == [lane_landing.REFUSAL.format(lane="L28-ssql-session-target", verb="stack-enqueue")]


def test_an_unnamed_helper_of_a_lane_is_refused_under_the_lane_name(tmp_path):
    subagent(tmp_path, "arelease-fix-1a2b", name="release-fix", agentType="long-running:lane-ship")
    subagent(tmp_path, "a3d0ef77", agentType="ship-pr", parentAgentId="arelease-fix-1a2b")

    assert run(tmp_path, "a3d0ef77", "gh pr edit 36039 --add-label merge-fast") == [
        lane_landing.REFUSAL.format(lane="release-fix", verb="gh pr edit --add-label")
    ]


def test_an_unnamed_helper_of_the_landing_desk_enqueues(tmp_path):
    subagent(tmp_path, "alanding-desk-1a2b", name="landing-desk", agentType="long-running:lane")
    subagent(tmp_path, "a9c8b7a6", agentType="general-purpose", parentAgentId="alanding-desk-1a2b")

    assert run(tmp_path, "a9c8b7a6") == []


def test_a_subagent_outside_a_drive_enqueues(tmp_path):
    subagent(tmp_path, "a1f2e3d4", name="ship-it", agentType="general-purpose")

    assert run(tmp_path, "a1f2e3d4", active=False) == []


def git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


@pytest.mark.parametrize(("where", "blocked"), [("drive", True), ("other", False)])
def test_a_lane_merges_outside_the_drive_repo_but_never_inside_it(tmp_path, monkeypatch, where, blocked):
    drive_repo, other_repo = git_repo(tmp_path / "drive"), git_repo(tmp_path / "other")
    drives = tmp_path / "config" / "long-running" / "drives"
    drives.mkdir(parents=True)
    common = subprocess.run(["git", "-C", str(drive_repo), "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True, check=True).stdout.strip()
    (drives / "d1.json").write_text(json.dumps({"drive": "d1", "sessions": [SESSION], "repo": "Forge-AI/monorepo", "git_common_dir": common}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    subagent(tmp_path, "a1f2e3d4", name="tool-drive-hardening", agentType="long-running:lane-ship")
    cwd = drive_repo if where == "drive" else other_repo

    assert bool(run(tmp_path, "a1f2e3d4", f"cd {cwd} && gt merge")) is blocked
