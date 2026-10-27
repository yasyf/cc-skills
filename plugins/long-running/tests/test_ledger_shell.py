from __future__ import annotations

import fcntl
import json
import os
import subprocess

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

    def run(self, argv, stdin=None, cwd=None):
        if argv[0] == "ccn":
            self.cwd = os.getcwd()
            raise SystemExit("stopped at ccn")
        return super().run(argv, stdin, cwd)


def test_a_command_outside_any_checkout_runs_in_its_registered_drives_checkout(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin", "https://github.com/acme/app.git"], check=True)
    drives = tmp_path / "config" / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / "brook.json").write_text(json.dumps({"drive": "brook", "ledger": "5b91baca4e233792d0bb", "cci_drive": "brook-bus", "checkout": str(checkout)}))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.chdir(scratch)
    shell = StopAtCcn()

    with pytest.raises(SystemExit, match="stopped at ccn"):
        ledger.main(["hold", "--ledger", "5b91baca4e233792d0bb", "--pr", "12", "--reason", "r", "--hours", "1"], shell)

    assert shell.cwd == str(checkout.resolve())
