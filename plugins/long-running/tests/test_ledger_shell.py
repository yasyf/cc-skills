from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from long_running import cci
from long_running import drive
from long_running import ledger
import pytest

HUNG = "import os, signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(60)"
SCRIPT = """import subprocess, sys
sys.path.insert(0, {scripts!r})
from long_running import drive, ledger
drive.end_commands_with_script()
if len(sys.argv) > 3:
    open(sys.argv[3], "w").write(str(subprocess.Popen(["sleep", "60"], start_new_session=True).pid))
depth = int(sys.argv[2])
ledger.Shell().run([sys.executable, __file__, sys.argv[1], str(depth - 1)] if depth else [sys.executable, "-c", {hung!r}, sys.argv[1]])
"""


def nesting_script(tmp_path: Path) -> Path:
    """A plugin script that runs itself through `ledger.Shell` `depth` times, then a command that swallows SIGTERM and hangs, as a `ccn` stuck on cc-notes does, writing its pid to the first argument."""
    path = tmp_path / "nesting.py"
    path.write_text(SCRIPT.format(scripts=str(Path(ledger.__file__).parent.parent), hung=HUNG))
    return path


def written(path: Path) -> int:
    started = time.monotonic()
    while not (path.is_file() and path.read_text()):
        assert time.monotonic() - started < 30, f"{path.name} was never written"
        time.sleep(0.05)
    return int(path.read_text())


def running(pid: int) -> bool:
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def ended(pid: int) -> bool:
    started = time.monotonic()
    while running(pid) and time.monotonic() - started < 10:
        time.sleep(0.05)
    return not running(pid)


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


def test_a_timed_out_command_takes_everything_under_it_down(tmp_path, monkeypatch):
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


def test_a_script_stopped_by_sigterm_kills_every_command_under_it_but_leaves_a_detached_daemon_running(tmp_path):
    """On 2026-10-30, `ccn` reads hung on cc-notes ran on under PID 1 after `timeout 110 standing.py lint` SIGTERMed the script that started them: a hung `ccn` swallows SIGTERM, and `standing.py` sits two scripts above one of its `ccn` reads."""
    script, hung, detached = nesting_script(tmp_path), tmp_path / "hung", tmp_path / "detached"
    top = subprocess.Popen([sys.executable, str(script), str(hung), "1", str(detached)])
    hung_pid = written(hung)

    top.send_signal(signal.SIGTERM)

    assert top.wait(timeout=30) == -signal.SIGTERM
    assert ended(hung_pid)
    assert running(daemon := written(detached))
    os.kill(daemon, signal.SIGKILL)


def test_a_script_past_the_deadline_its_caller_set_kills_its_commands_and_says_so(tmp_path):
    script, hung = nesting_script(tmp_path), tmp_path / "hung"
    top = subprocess.Popen([sys.executable, str(script), str(hung), "0"], stderr=subprocess.PIPE, text=True, env=os.environ | drive.deadline_env(5))
    hung_pid = written(hung)

    _, stderr = top.communicate(timeout=30)

    assert top.returncode == -signal.SIGALRM
    assert stderr.splitlines()[-1] == "nesting.py reached the deadline its caller set and was killed with every command it started"
    assert ended(hung_pid)


def test_a_script_command_past_the_timeout_kills_its_own_commands_first_and_reads_as_the_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "COMMAND_TIMEOUT_SECONDS", drive.REAP_SECONDS + 5)
    script, hung = nesting_script(tmp_path), tmp_path / "hung"

    with pytest.raises(subprocess.TimeoutExpired):
        ledger.Shell.once([sys.executable, str(script), str(hung), "0"], None)

    assert ended(written(hung))


def test_a_deadline_another_process_set_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv(drive.DEADLINE_ENV, f"{os.getpid()}:{time.time() - 60:.3f}")
    script = tmp_path / "quiet.py"
    script.write_text(f"import sys, time\nsys.path.insert(0, {str(Path(drive.__file__).parent.parent)!r})\nfrom long_running import drive\ndrive.end_commands_with_script()\ntime.sleep(1)\nprint('done')\n")

    done = subprocess.run(["sh", "-c", f"{sys.executable} {script}; exit $?"], capture_output=True, text=True, timeout=30)

    assert (done.returncode, done.stdout) == (0, "done\n")


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

