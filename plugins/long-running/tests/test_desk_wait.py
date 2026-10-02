from __future__ import annotations

import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "skills/long-running/scripts/desk-wait.sh"


@pytest.mark.parametrize("script", [SCRIPT, PLUGIN / "bin/desk-wait.sh"])
def test_new_lines_advance_the_cursor_and_return_before_the_deadline(tmp_path, script):
    inbox = tmp_path / "inbox file"
    cursor = tmp_path / "cursor file"
    inbox.write_text("consumed\n  R1 raw \\text  \n\n" + "x" * 2600 + "\n")
    cursor.write_text("1\n")

    started = time.monotonic()
    result = subprocess.run([str(script), "3", f"{inbox}={cursor}"], capture_output=True, text=True, timeout=5)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "  R1 raw \\text  \n" + "x" * 2500 + "\n"
    assert cursor.read_text() == "4\n"
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("exists", [True, False])
def test_no_new_lines_prints_quiet_in_los_angeles_time(tmp_path, exists):
    inbox = tmp_path / "inbox"
    cursor = tmp_path / "cursor"
    if exists:
        inbox.write_text("consumed\n")
        cursor.write_text("1\n")
    zone = ZoneInfo("America/Los_Angeles")
    before = datetime.now(zone).strftime("%I:%M %p").lstrip("0")

    result = subprocess.run([str(SCRIPT), "1", f"{inbox}={cursor}"], capture_output=True, text=True, timeout=3)

    after = datetime.now(zone).strftime("%I:%M %p").lstrip("0")
    assert result.returncode == 0, result.stderr
    assert result.stdout in {f"QUIET {before}\n", f"QUIET {after}\n"}
    if exists:
        assert cursor.read_text() == "1\n"
    else:
        assert not cursor.exists()


@pytest.mark.parametrize("first", ["R1 inbox\n", ""])
def test_reports_new_lines_from_every_file_in_the_pass(tmp_path, first):
    inbox = tmp_path / "inbox"
    mailbox = tmp_path / "mailbox"
    inbox_cursor = tmp_path / "inbox.cursor"
    mailbox_cursor = tmp_path / "mailbox.cursor"
    inbox.write_text(first)
    mailbox.write_text("worker done\n")

    result = subprocess.run(
        [str(SCRIPT), "3", f"{inbox}={inbox_cursor}", f"{mailbox}={mailbox_cursor}"],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == first + "worker done\n"
    assert mailbox_cursor.read_text() == "1\n"
    if first:
        assert inbox_cursor.read_text() == "1\n"


def test_missing_cursor_starts_at_zero(tmp_path):
    inbox = tmp_path / "inbox"
    cursor = tmp_path / "cursor"
    inbox.write_text("R1 first\nR2 second\n")

    result = subprocess.run([str(SCRIPT), "3", f"{inbox}={cursor}"], capture_output=True, text=True, timeout=5)

    assert result.returncode == 0, result.stderr
    assert result.stdout == inbox.read_text()
    assert cursor.read_text() == "2\n"


def test_append_during_wait_returns_before_the_deadline(tmp_path):
    inbox = tmp_path / "inbox"
    cursor = tmp_path / "cursor"
    inbox.write_text("")
    started = time.monotonic()
    with subprocess.Popen([str(SCRIPT), "3", f"{inbox}={cursor}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
        time.sleep(0.2)
        inbox.write_text("R1 arrived\n")
        stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == 0, stderr
    assert stdout == "R1 arrived\n"
    assert cursor.read_text() == "1\n"
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("args", [[], ["1"], ["bad", "inbox=cursor"], ["-1", "inbox=cursor"], ["1.5", "inbox=cursor"], ["1", "inbox"], ["1", "=cursor"], ["1", "inbox="]])
def test_bad_arguments_print_usage_and_exit_two(tmp_path, args):
    result = subprocess.run([str(SCRIPT), *args], cwd=tmp_path, capture_output=True, text=True, timeout=3)

    assert result.returncode == 2
    assert result.stderr == "usage: desk-wait.sh <seconds> <file>=<cursor-file>...\n"
