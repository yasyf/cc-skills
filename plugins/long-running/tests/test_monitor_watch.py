from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/monitor-watch.py"

PUP = """#!/bin/sh
cat "$FAKE_STATE/monitors.json"
"""

CCI = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_STATE/cci.log"
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
        (bin_dir / "cci").write_text(CCI)
        (bin_dir / "cci").chmod(0o755)
        self.state = root / "state.json"
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_STATE": str(root)}

    def once(self, *monitors: dict, args: tuple[str, ...] = ("--tag", "release-target:*")) -> list[str]:
        (self.root / "monitors.json").write_text(json.dumps(list(monitors)))
        result = subprocess.run(
            [str(SCRIPT), "once", "--state", str(self.state), *args], env=self.env, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        return [line.split(" ", 1)[1] for line in result.stdout.splitlines()]


def alerts(posts: Path) -> list[str]:
    return [line for line in posts.read_text().splitlines() if "--kind note" in line] if posts.exists() else []


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


def test_a_move_into_alert_or_warn_posts_one_alert_record_to_the_desk_on_cci(watch):
    posts = watch.root / "cci.log"
    args = ("--tag", "release-target:*", "--alert-drive", "d1")
    watch.once(monitor(1, "OK"), monitor(2, "OK"), monitor(3, "OK"), monitor(4, "Alert"), args=args)
    assert alerts(posts) == []
    watch.once(monitor(1, "Alert"), monitor(2, "Warn"), monitor(3, "No Data"), monitor(4, "OK"), args=args)
    assert alerts(posts) == [
        "post --drive d1 --lane monitor-watch --kind note --to orca-desk --text orca-desk: alert dd-1 https://app.datadoghq.com/monitors/1 :: Datadog OK -> Alert: monitor 1",
        "post --drive d1 --lane monitor-watch --kind note --to orca-desk --text orca-desk: alert dd-2 https://app.datadoghq.com/monitors/2 :: Datadog OK -> Warn: monitor 2",
    ]


def test_a_read_posts_one_state_record_at_most_every_fifteen_minutes_across_runs(watch):
    posts = watch.root / "cci.log"
    args = ("--tag", "release-target:*", "--alert-drive", "d1")
    watch.once(monitor(1, "OK"), monitor(2, "OK"), args=args)
    watch.once(monitor(1, "OK"), monitor(2, "OK"), args=args)
    state = "post --drive d1 --lane monitor-watch --kind state --text monitor-watch: watching 2 monitors"
    assert posts.read_text().splitlines() == [state]
    marker = Path(str(watch.state) + ".alive")
    aged = marker.stat().st_mtime - 15 * 60
    os.utime(marker, (aged, aged))
    watch.once(monitor(1, "OK"), monitor(2, "OK"), args=args)
    assert posts.read_text().splitlines() == [state, state]


def test_a_failed_read_posts_no_state_record_so_a_blind_watcher_goes_quiet(watch):
    (watch.root / "monitors.json").write_text("rate limited")
    subprocess.run(
        [str(SCRIPT), "once", "--state", str(watch.state), "--tag", "release-target:*", "--alert-drive", "d1"],
        env=watch.env,
        capture_output=True,
        text=True,
    )
    assert not (watch.root / "cci.log").exists()


def test_a_host_without_pup_prints_api_fail_instead_of_crashing(watch: Watch) -> None:
    (watch.root / "bin" / "pup").unlink()
    watch.env["PATH"] = f"{watch.root / 'bin'}:{Path(shutil.which('uv')).parent}:{Path(sys.executable).parent}:/usr/bin:/bin"
    lines = watch.once(monitor(1, "Alert"))
    assert len(lines) == 1 and lines[0].startswith("API-FAIL pup is not installed"), lines
