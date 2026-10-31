from __future__ import annotations

import fcntl
import json
import os
import subprocess
import threading
import time

import cci
import ledger
import pytest


class TimingOutShell(ledger.Shell):
    def run(self, argv, stdin=None):
        raise subprocess.TimeoutExpired(argv, ledger.COMMAND_TIMEOUT_SECONDS)


def test_an_object_check_larger_than_any_pipe_buffer_answers_every_head(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    heads = [f"{n:040x}" for n in range(1, 40001)]

    assert ledger.absent_heads(ledger.Shell(), tmp_path, heads) == frozenset(heads)


def test_a_command_past_the_timeout_is_killed(monkeypatch):
    monkeypatch.setattr(ledger, "COMMAND_TIMEOUT_SECONDS", 0.2)

    with pytest.raises(subprocess.TimeoutExpired):
        ledger.Shell.once(["sleep", "5"], None)


def test_a_timed_out_command_takes_its_whole_process_group_down(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "COMMAND_TIMEOUT_SECONDS", 0.5)
    pidfile = tmp_path / "grandchild"

    with pytest.raises(subprocess.TimeoutExpired):
        ledger.Shell.once(["sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"], None)

    grandchild = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail(f"grandchild {grandchild} outlived its timed-out parent")


def test_a_command_dies_before_the_callers_deadline(monkeypatch):
    monkeypatch.setenv(ledger.DEADLINE_ENV, str(time.time() + ledger.DEADLINE_MARGIN_SECONDS + 0.5))
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        ledger.Shell.once(["sleep", "30"], None)

    assert time.monotonic() - started < ledger.DEADLINE_MARGIN_SECONDS


def test_a_timed_out_command_fails_the_verb_and_names_the_command(capsys):
    assert ledger.main(["init", "--title", "open PRs"], TimingOutShell()) == 1

    assert f"ccn ledger add open PRs --json timed out after {ledger.COMMAND_TIMEOUT_SECONDS}s and was killed" in capsys.readouterr().err


def test_a_held_lock_refuses_after_the_wait_and_names_its_holder(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "LOCK_WAIT_SECONDS", 0.3)
    path = tmp_path / "ledger.lock"
    with path.open("a+") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        holder.write("pid 4242 since 2026-10-15T08:00:00Z\n")
        holder.flush()

        with pytest.raises(SystemExit) as refused, ledger.locked(path):
            pytest.fail("acquired a lock another process holds")

    assert str(refused.value.code) == f"{path} is still held after 0.3s by pid 4242 since 2026-10-15T08:00:00Z; nothing written, rerun once it finishes"


def test_the_lock_names_its_holder_only_while_held(tmp_path):
    path = tmp_path / "ledger.lock"

    with ledger.locked(path):
        assert path.read_text().startswith(f"pid {os.getpid()} since ")

    assert path.read_text() == ""


class StopAtCcn(ledger.Shell):
    def __init__(self) -> None:
        self.cwd = ""
        self.argv: list[str] = []

    def run(self, argv, stdin=None, cwd=None):
        if argv[0] == "ccn":
            self.cwd, self.argv = os.getcwd(), argv
            raise SystemExit("stopped at ccn")
        return super().run(argv, stdin, cwd)


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin", "https://github.com/acme/app.git"], check=True)
    drives = tmp_path / "config" / "long-running" / "drives"
    drives.mkdir(parents=True)
    entry = {"drive": "brook", "ledger": "5b91baca4e233792d0bb", "cci_drive": "brook-bus", "checkout": str(checkout), "sessions": ["root-session"]}
    (drives / "brook.json").write_text(json.dumps(entry))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    monkeypatch.delenv("ORCA_LAUNCH_RUN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "other-session")
    monkeypatch.chdir(scratch)
    return checkout


def test_a_command_outside_any_checkout_runs_in_its_registered_drives_checkout(checkout):
    shell = StopAtCcn()

    with pytest.raises(SystemExit, match="stopped at ccn"):
        ledger.main(["hold", "--ledger", "5b91baca4e233792d0bb", "--pr", "12", "--reason", "r", "--hours", "1"], shell)

    assert shell.cwd == str(checkout.resolve())


def test_a_verb_without_ledger_uses_the_ledger_of_the_drive_this_session_runs_in(checkout, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    shell = StopAtCcn()

    with pytest.raises(SystemExit, match="stopped at ccn"):
        ledger.main(["reassign", "--pr", "12", "--lane", "quiet"], shell)

    assert shell.argv[:4] == ["ccn", "ledger", "show", "5b91baca4e233792d0bb"]
    assert shell.cwd == str(checkout.resolve())


@pytest.mark.parametrize("named", ["brook", "brook-bus"])
def test_drive_by_id_or_cci_drive_names_the_ledger(checkout, named):
    shell = StopAtCcn()

    with pytest.raises(SystemExit, match="stopped at ccn"):
        ledger.main(["reassign", "--drive", named, "--pr", "12", "--lane", "quiet"], shell)

    assert shell.argv[:4] == ["ccn", "ledger", "show", "5b91baca4e233792d0bb"]


def test_a_message_verb_takes_its_cci_drive_from_the_drive_its_ledger_names(checkout):
    args = ledger.build_parser().parse_args(["inbox", "--ledger", "5b91baca"])

    ledger.default_from_drive(args)

    assert (args.ledger, args.drive) == ("5b91baca", "brook-bus")


def test_a_verb_outside_any_drive_refuses_and_names_the_flags(checkout):
    with pytest.raises(SystemExit, match="matches this session; pass --ledger ID and --drive D"):
        ledger.main(["reassign", "--pr", "12", "--lane", "quiet"], StopAtCcn())


def test_a_binary_missing_for_seconds_while_it_reinstalls_runs_once_it_is_back(tmp_path, monkeypatch):
    """Root #233454, #233635: a cci cask reinstall left /opt/homebrew/bin/cci missing for seconds, stall_watch.py died on FileNotFoundError, and no stall alarm posted."""
    monkeypatch.setattr(cci, "INSTALL_RETRY_SECONDS", 0.1)
    tool = tmp_path / "cci"
    linked = threading.Timer(0.5, lambda: (tool.write_text("#!/bin/sh\necho back\n"), tool.chmod(0o755)))
    linked.start()

    assert ledger.Shell().run([str(tool)]) == "back\n"
    linked.join()


def test_a_binary_that_stays_missing_past_the_install_window_still_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(cci, "INSTALL_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(cci, "INSTALL_WINDOW_SECONDS", 0.3)
    started = time.monotonic()

    with pytest.raises(FileNotFoundError):
        ledger.Shell().run([str(tmp_path / "gone")])
    assert 0.3 <= time.monotonic() - started < 2


def test_a_missing_working_directory_fails_at_once_rather_than_waiting_out_the_install_window(tmp_path):
    started = time.monotonic()

    with pytest.raises(FileNotFoundError):
        ledger.Shell().run(["true"], cwd=tmp_path / "gone")
    assert time.monotonic() - started < 1

