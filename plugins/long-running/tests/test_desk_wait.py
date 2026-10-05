from __future__ import annotations

import json
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
    assert result.stdout == "  R1 raw \\text  \n" + "x" * 399 + "…\n"
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


def message(read: bool) -> dict:
    return {"from": "team-lead", "text": "pick A", "read": read}


def mailbox_in(tmp_path: Path) -> Path:
    inboxes = tmp_path / "teams" / "session-1" / "inboxes"
    inboxes.mkdir(parents=True)
    return inboxes / "lane.json"


def wait_during(tmp_path: Path, mailbox: Path, write) -> tuple[str, float]:
    inbox = tmp_path / "inbox"
    inbox.write_text("")
    started = time.monotonic()
    with subprocess.Popen(
        [str(SCRIPT), "5", f"{inbox}={tmp_path / 'inbox.cursor'}", f"{mailbox}={tmp_path / 'mailbox.cursor'}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        time.sleep(0.5)
        appended = time.monotonic()
        write()
        stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stderr
    return stdout, time.monotonic() - (appended if stdout.startswith("MAILBOX") else started)


def test_an_unread_message_appended_during_a_wait_returns_within_two_seconds(tmp_path):
    mailbox = mailbox_in(tmp_path)
    mailbox.write_text(json.dumps([message(read=True)]))

    stdout, elapsed = wait_during(tmp_path, mailbox, lambda: mailbox.write_text(json.dumps([message(read=True), message(read=False)])))

    assert stdout == "MAILBOX 1 unread\n"
    assert elapsed < 2
    assert (tmp_path / "mailbox.cursor").read_text() == "2\n"


def test_a_read_message_does_not_wake_the_wait(tmp_path):
    mailbox = mailbox_in(tmp_path)
    mailbox.write_text("[]")

    stdout, elapsed = wait_during(tmp_path, mailbox, lambda: mailbox.write_text(json.dumps([message(read=True)])))

    assert stdout.startswith("QUIET ")
    assert elapsed >= 5


def test_a_missing_mailbox_does_not_wake_the_wait(tmp_path):
    mailbox = mailbox_in(tmp_path)

    stdout, elapsed = wait_during(tmp_path, mailbox, lambda: None)

    assert stdout.startswith("QUIET ")
    assert elapsed >= 5


def test_unread_messages_waiting_before_the_call_wake_it_once(tmp_path):
    mailbox = mailbox_in(tmp_path)
    cursor = tmp_path / "mailbox.cursor"
    mailbox.write_text(json.dumps([message(read=False), message(read=False)]))

    first = subprocess.run([str(SCRIPT), "1", f"{mailbox}={cursor}"], capture_output=True, text=True, timeout=5)
    again = subprocess.run([str(SCRIPT), "1", f"{mailbox}={cursor}"], capture_output=True, text=True, timeout=5)

    assert first.stdout == "MAILBOX 2 unread\n"
    assert again.stdout.startswith("QUIET ")


@pytest.mark.parametrize("args", [[], ["1"], ["bad", "inbox=cursor"], ["-1", "inbox=cursor"], ["1.5", "inbox=cursor"], ["1", "inbox"], ["1", "=cursor"], ["1", "inbox="], ["1", "--"], ["1", "inbox=cursor", "--"]])
def test_bad_arguments_print_usage_and_exit_two(tmp_path, args):
    result = subprocess.run([str(SCRIPT), *args], cwd=tmp_path, capture_output=True, text=True, timeout=3)

    assert result.returncode == 2
    assert result.stderr == "usage: desk-wait.sh <seconds> (<file>=<cursor-file> | cci:<drive>:<lane>)... [-- <command> [<arg>...]]\n"


def test_cci_source_prints_addressed_records_through_cci_tail(tmp_path):
    fake = tmp_path / "bin" / "cci"
    fake.parent.mkdir()
    calls = tmp_path / "calls"
    fake.write_text(f'#!/bin/sh\necho "$@" >> {calls}\necho "#12 9:01 PM GO root -> landing-desk land #30001"\n')
    fake.chmod(0o755)
    env = {"PATH": f"{fake.parent}:/usr/bin:/bin"}

    result = subprocess.run([str(SCRIPT), "3", "cci:release-v3:landing-desk"], capture_output=True, text=True, timeout=5, env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "#12 9:01 PM GO root -> landing-desk land #30001\n"
    assert calls.read_text() == "tail --drive release-v3 --cursor landing-desk --to landing-desk\n"


def test_quiet_cci_source_waits_for_the_deadline(tmp_path):
    fake = tmp_path / "bin" / "cci"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)

    result = subprocess.run([str(SCRIPT), "1", "cci:release-v3:landing-desk"], capture_output=True, text=True, timeout=5, env={"PATH": f"{fake.parent}:/usr/bin:/bin"})

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("QUIET ")


def guarded(tmp_path: Path, mailbox: Path, script: str, write=None, seconds: str = "10") -> tuple[subprocess.CompletedProcess, float]:
    started = time.monotonic()
    with subprocess.Popen(
        [str(SCRIPT), seconds, f"{mailbox}={tmp_path / 'mailbox.cursor'}", "--", "sh", "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        time.sleep(0.5)
        appended = time.monotonic()
        if write:
            write()
        stdout, stderr = process.communicate(timeout=15)
    result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    return result, time.monotonic() - (appended if write else started)


def test_a_command_that_finishes_first_passes_its_output_and_exit_status_through(tmp_path):
    mailbox = mailbox_in(tmp_path)
    mailbox.write_text("[]")

    result, elapsed = guarded(tmp_path, mailbox, "echo build passed; exit 3")

    assert result.returncode == 3
    assert result.stdout == "build passed\n"
    assert elapsed < 3


def test_an_unread_message_ends_a_running_command_within_two_seconds(tmp_path):
    mailbox = mailbox_in(tmp_path)
    mailbox.write_text("[]")
    marker = tmp_path / "survived"

    result, elapsed = guarded(
        tmp_path,
        mailbox,
        f"echo watching; sleep 30; touch {marker}",
        write=lambda: mailbox.write_text(json.dumps([message(read=False)])),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "watching\nMAILBOX 1 unread\n"
    assert elapsed < 2
    time.sleep(0.5)
    assert not marker.exists()


def test_a_command_that_ignores_sigterm_is_killed_after_the_grace_period(tmp_path):
    mailbox = mailbox_in(tmp_path)
    mailbox.write_text("[]")

    result, elapsed = guarded(
        tmp_path,
        mailbox,
        "trap '' TERM; while :; do sleep 1; done",
        write=lambda: mailbox.write_text(json.dumps([message(read=False)])),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "MAILBOX 1 unread\n"
    assert elapsed < 8


def test_the_deadline_ends_a_running_command_with_quiet(tmp_path):
    mailbox = mailbox_in(tmp_path)

    result, elapsed = guarded(tmp_path, mailbox, "sleep 30", seconds="1")

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("QUIET ")
    assert elapsed < 4


def test_a_read_message_does_not_end_a_running_command(tmp_path):
    mailbox = mailbox_in(tmp_path)
    mailbox.write_text("[]")

    result, _ = guarded(tmp_path, mailbox, "sleep 1.5; echo done", write=lambda: mailbox.write_text(json.dumps([message(read=True)])))

    assert result.stdout == "done\n"
