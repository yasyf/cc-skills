from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from captain_hook.events import PostToolUseEvent
from captain_hook.testing.helpers import build_context

from hooks.tests.ledger_fixtures import FIXTURES
from hooks.pr_ledger import OpenedPr, lane_name, opened_prs, opener_cwd, response_text

SESSION = "900424b6-7393-480c-a26a-f1bd21da6e57"


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("ccx-ship-gt.txt", [OpenedPr("28534", "Forge-AI/monorepo", "1caa098d30a8")]),
        ("ccx-stack-submit.txt", [OpenedPr("28557", None, "03afab1d4802"), OpenedPr("28558", None, "af664caed71b")]),
        ("ccx-ship-git.txt", [OpenedPr("123", "yasyf/cc-skills")]),
        ("gh-pr-create.txt", [OpenedPr("28612", "Forge-AI/monorepo")]),
        ("gt-submit.txt", [OpenedPr("28560", "Forge-AI/monorepo"), OpenedPr("28562", "Forge-AI/monorepo")]),
    ],
)
def test_opened_prs_reads_only_the_prs_the_command_submitted(fixture, expected):
    assert opened_prs((FIXTURES / fixture).read_text()) == expected


def test_a_pr_url_the_command_did_not_submit_records_nothing():
    output = 'committed 1caa098d30 "ci: ✨ revert https://github.com/Forge-AI/monorepo/pull/7" · pushed nothing'
    assert opened_prs(output) == []


def test_a_pr_spec_carries_its_repo_and_head():
    assert [pr.spec for pr in opened_prs((FIXTURES / "ccx-ship-gt.txt").read_text())] == ["Forge-AI/monorepo#28534=1caa098d30a8"]


def test_response_text_reads_a_bash_result_and_its_stderr():
    assert response_text({"stdout": "out", "stderr": "err", "interrupted": False}) == "out\nerr"


def event(tmp_path: Path, agent_id: str | None = None, command: str = "ccx vcs ship") -> PostToolUseEvent:
    transcript = tmp_path / f"{SESSION}.jsonl"
    transcript.write_text("")
    lane = tmp_path / SESSION / "subagents" / f"agent-{agent_id}.jsonl" if agent_id else transcript
    lane.parent.mkdir(parents=True, exist_ok=True)
    lane.touch()
    raw = {"session_id": SESSION, "transcript_path": str(transcript), "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)}
    return PostToolUseEvent(_raw=raw | ({"agent_id": agent_id} if agent_id else {}), ctx=build_context(None, lane, tmp_path, tmp_path))


def test_a_named_subagent_records_under_its_own_name(tmp_path):
    meta = tmp_path / SESSION / "subagents" / "agent-adeploy-1a2b.meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"name": "deploy-experience", "agentType": "deploy-experience"}))
    meta.with_name("agent-adeploy-1a2b.jsonl").write_text("")

    assert lane_name(event(tmp_path, "adeploy-1a2b")) == "deploy-experience"


def test_an_unnamed_session_falls_back_to_its_lane_env_then_its_session_id(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    assert lane_name(event(tmp_path, "aship-pr-9f9f")) == SESSION

    monkeypatch.setenv("CLAUDE_LONG_RUNNING_LANE", "orca-desk")
    assert lane_name(event(tmp_path)) == "orca-desk"


def test_the_ship_runs_in_the_directory_its_command_cd_into(tmp_path):
    (tmp_path / "cc-skills").mkdir()
    assert opener_cwd(event(tmp_path, command=f"cd {tmp_path}/cc-skills && ccx vcs ship -m x")) == tmp_path / "cc-skills"
    assert opener_cwd(event(tmp_path, command="gt submit --stack")) == tmp_path


def test_a_failed_fork_names_the_unrecorded_prs_instead_of_faulting(tmp_path, monkeypatch):
    from hooks import pr_ledger

    def exhausted(*args, **kwargs):
        raise BlockingIOError(35, "Resource temporarily unavailable")

    monkeypatch.setattr(pr_ledger.subprocess, "run", exhausted)
    evt = event(tmp_path)
    evt._raw["tool_response"] = (FIXTURES / "ccx-ship-gt.txt").read_text()

    result = pr_ledger.record_opened_prs(evt)

    assert result is not None
    assert result.message == pr_ledger.UNRECORDED


@pytest.mark.parametrize(("lane", "expected"), [("merge-walker-r2", "no drive claims session"), (None, None)], ids=["lane", "no-lane"])
def test_a_lane_in_no_drive_is_told_its_pr_was_not_recorded(tmp_path, monkeypatch, lane, expected):
    from hooks import pr_ledger

    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    if lane:
        monkeypatch.setenv("CLAUDE_LONG_RUNNING_LANE", lane)
    monkeypatch.setattr(
        pr_ledger.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", "no drive claims session x (lane y)"),
    )
    evt = event(tmp_path)
    evt._raw["tool_response"] = (FIXTURES / "ccx-ship-gt.txt").read_text()

    result = pr_ledger.record_opened_prs(evt)

    assert (result.message if result else None) == (expected and "no drive claims session x (lane y)")
