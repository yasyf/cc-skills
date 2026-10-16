from __future__ import annotations

import json
import os
import subprocess
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
    return state


def beat(state: Path, at: datetime, pid: int) -> None:
    payload = {"pid": pid, "at": at.isoformat(), "config": str(state / "runner.json"), "restart": RESTART}
    (state / "inbox" / desk_liveness.HEARTBEAT).write_text(json.dumps(payload) + "\n")


def append(state: Path, cwd: Path) -> list:
    command = f"STATE={state}; echo 'orca-desk: incident plat-replica-unreadable reported=15:50' >> $STATE/inbox/orca-desk.md"
    cwd.mkdir(exist_ok=True)
    raw = {"session_id": SESSION, "agent_id": LANE, "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    return fire(desk_liveness, PostToolUseEvent(_raw=raw, ctx=build_context(session_dir=cwd)))


def test_a_lane_appending_through_a_shell_variable_reads_the_live_runners_heartbeat(drive, tmp_path):
    beat(drive, datetime.now(timezone.utc), os.getpid())

    assert append(drive, tmp_path / "lane") == []


def dead_pid() -> int:
    child = subprocess.Popen(["true"])
    child.wait()
    return child.pid


def test_a_dead_runner_names_its_last_pass_and_the_restart_it_recorded(drive, tmp_path):
    beat(drive, datetime(2026, 10, 10, 20, 59, tzinfo=timezone.utc), dead_pid())

    [result] = append(drive, tmp_path / "lane")

    assert result.message.startswith("LAUNCH-FAILED: orca desk runner not running (last pass 1:59 PM)")
    assert f"\n{RESTART}\n" in result.message


def test_a_dead_runner_with_a_fresh_heartbeat_still_reports_not_running(drive, tmp_path):
    beat(drive, datetime.now(timezone.utc), dead_pid())

    [result] = append(drive, tmp_path / "lane")

    assert result.message.startswith("LAUNCH-FAILED: orca desk runner not running (last pass ")


def test_a_live_runner_mid_pass_past_its_last_beat_is_running(drive, tmp_path):
    beat(drive, datetime.now(timezone.utc) - timedelta(minutes=3), os.getpid())

    assert append(drive, tmp_path / "lane") == []


def test_a_live_runner_with_no_pass_past_the_command_timeout_is_wedged(drive, tmp_path):
    beat(drive, datetime.now(timezone.utc) - timedelta(minutes=40), os.getpid())

    [result] = append(drive, tmp_path / "lane")

    assert result.message.startswith(f"LAUNCH-FAILED: orca desk runner pid {os.getpid()} is alive but has finished no pass in over 32 minutes")
    assert RESTART not in result.message
