from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from captain_hook.events import PostToolUseEvent
from captain_hook.testing.helpers import build_context

from fire import fire
from hooks import desk_liveness

SESSION = "06923255-284c-4abb-9d9e-df56d5ca1e74"
LANE = "aalerts-desk-3-7dd760cda47127c2"
RESTART = "nohup desk-runner.py run --config /drive/runner.json --desk orca > /orca-runner.log 2>&1 < /dev/null &"


@pytest.fixture
def drive(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    state = tmp_path / "drive"
    drives = tmp_path / "config" / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / "drive.json").write_text(json.dumps({"drive": "drive", "sessions": [SESSION], "state_dir": str(state)}))
    (state / "inbox").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "cci").write_text(f"#!/bin/sh\necho \"$@\" >> {tmp_path / 'cci-calls'}\ncat {tmp_path / 'alive.jsonl'} 2>/dev/null || true\n")
    (bin_dir / "cci").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return state


def beat(state: Path, at: datetime) -> None:
    record = {"seq": 7, "drive": "drive", "lane": "desk-runner", "kind": "state", "topic": "orca", "to": ["drive-watchdog@drive"], "at": at.isoformat(), "text": "orca alive 1:59 PM pass 4", "fields": {"attrs": {"pid": "73091", "restart": RESTART}}}
    (state.parent / "alive.jsonl").write_text(json.dumps(record) + "\n")


def fire_bash(command: str, cwd: Path) -> list:
    cwd.mkdir(exist_ok=True)
    raw = {"session_id": SESSION, "agent_id": LANE, "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    return fire(desk_liveness, PostToolUseEvent(_raw=raw, ctx=build_context(session_dir=cwd)))


def append(state: Path, cwd: Path) -> list:
    return fire_bash(f"STATE={state}; echo 'orca-desk: incident plat-replica-unreadable reported=15:50' >> $STATE/inbox/orca-desk.md", cwd)


def post(state: Path, cwd: Path) -> list:
    return fire_bash("cci post --lane root --kind go --to=root,orca-desk --text 'R9 (2:00 PM) orca-desk: launch x NOW incident xhigh brief=/b.md'", cwd)


def test_a_lane_appending_through_a_shell_variable_reads_the_runners_alive_record_from_cci(drive, tmp_path):
    beat(drive, datetime.now(timezone.utc))

    assert append(drive, tmp_path / "lane") == []
    assert "--drive drive --since 0 --json --lane desk-runner --kind=state --topic=orca --to drive-watchdog@drive --limit 1" in (tmp_path / "cci-calls").read_text()


def test_a_runner_on_another_machine_with_no_heartbeat_file_here_is_running_while_its_alive_record_is_fresh(drive, tmp_path):
    """Oct 29: the runners must move off this Mac, so a pid or heartbeat file only this Mac sees cannot say whether one runs (cci #199401)."""
    beat(drive, datetime.now(timezone.utc) - timedelta(minutes=3))

    assert list((drive / "inbox").iterdir()) == []
    assert append(drive, tmp_path / "lane") == []
    assert post(drive, tmp_path / "lane") == []


@pytest.mark.parametrize("fires", [append, post], ids=["append", "cci-post"])
def test_a_silent_runner_names_its_last_alive_record_and_the_restart_it_posted(drive, tmp_path, fires):
    beat(drive, datetime(2026, 10, 10, 20, 59, tzinfo=timezone.utc))

    [result] = fires(drive, tmp_path / "lane")

    assert result.message.startswith("LAUNCH-FAILED: no orca alive record from the orca desk runner in the last five minutes (last 1:59 PM)")
    assert f"\n{RESTART}\n" in result.message


def test_a_drive_whose_orca_runner_never_posted_names_the_start_command(drive, tmp_path):
    [result] = post(drive, tmp_path / "lane")

    assert "(last never)" in result.message
    assert f"\n{desk_liveness.START}\n" in result.message


def test_a_cci_that_does_not_answer_says_nothing(drive, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))

    assert append(drive, tmp_path / "lane") == []


def test_a_post_to_another_lane_reads_nothing(drive, tmp_path):
    assert fire_bash("cci post --lane root --kind go --to landing-desk --text 'go #1'", tmp_path / "lane") == []
    assert not (tmp_path / "cci-calls").exists()
