from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/monitor-watch.py"

PUP = """#!/bin/sh
cat "$FAKE_STATE/monitors.json"
"""


def monitor(id: int, state: str, tags: list[str] | None = None) -> dict:
    return {
        "id": id,
        "name": f"monitor {id}",
        "overall_state": state,
        "overall_state_modified": "2026-09-30T19:00:00+00:00",
        "tags": tags if tags is not None else ["release-target:api"],
    }


class Watch:
    def __init__(self, root: Path):
        self.root = root
        bin_dir = root / "bin"
        bin_dir.mkdir()
        (bin_dir / "pup").write_text(PUP)
        (bin_dir / "pup").chmod(0o755)
        self.state = root / "state.json"
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_STATE": str(root)}

    def once(self, *monitors: dict, args: tuple[str, ...] = ("--tag", "release-target:*")) -> list[str]:
        (self.root / "monitors.json").write_text(json.dumps(list(monitors)))
        result = subprocess.run(
            [str(SCRIPT), "once", "--state", str(self.state), *args], env=self.env, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        return [line.split(" ", 1)[1] for line in result.stdout.splitlines()]


@pytest.fixture
def watch(tmp_path: Path) -> Watch:
    return Watch(tmp_path)


def test_the_first_read_reports_only_what_is_already_alerting(watch):
    assert watch.once(monitor(1, "OK"), monitor(2, "Alert")) == [
        "2 start -> Alert at 2026-09-30T19:00:00+00:00 | monitor 2"
    ]


def test_an_unchanged_state_prints_nothing(watch):
    watch.once(monitor(1, "Alert"))
    assert watch.once(monitor(1, "Alert")) == []


def test_alert_warn_no_data_and_recovery_are_transitions(watch):
    watch.once(monitor(1, "OK"), monitor(2, "OK"), monitor(3, "OK"))
    assert watch.once(monitor(1, "Alert"), monitor(2, "Warn"), monitor(3, "No Data")) == [
        "1 OK -> Alert at 2026-09-30T19:00:00+00:00 | monitor 1",
        "2 OK -> Warn at 2026-09-30T19:00:00+00:00 | monitor 2",
        "3 OK -> No Data at 2026-09-30T19:00:00+00:00 | monitor 3",
    ]
    assert watch.once(monitor(1, "OK"), monitor(2, "Warn"), monitor(3, "OK")) == [
        "1 Alert -> OK at 2026-09-30T19:00:00+00:00 | monitor 1",
        "3 No Data -> OK at 2026-09-30T19:00:00+00:00 | monitor 3",
    ]


def test_only_tagged_or_named_monitors_are_watched(watch):
    args = ("--tag", "release-target:*", "--id", "7")
    lines = watch.once(monitor(1, "Alert"), monitor(7, "Alert", tags=[]), monitor(8, "Alert", tags=["team:x"]), args=args)
    assert [line.split()[0] for line in lines] == ["1", "7"]


def test_a_failed_read_reports_and_keeps_the_state(watch):
    watch.once(monitor(1, "Alert"))
    (watch.root / "monitors.json").write_text("rate limited")
    result = subprocess.run(
        [str(SCRIPT), "once", "--state", str(watch.state), "--tag", "release-target:*"],
        env=watch.env,
        capture_output=True,
        text=True,
    )
    assert "API-FAIL" in result.stdout
    assert json.loads(watch.state.read_text()) == {"1": "Alert"}
