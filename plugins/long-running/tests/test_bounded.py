from __future__ import annotations

import random
import signal
import subprocess
import time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
BOUNDED = PLUGIN / "bin/bounded.py"


def marker() -> str:
    return f"sleep 900.{random.randrange(10**6):06d}"


def survivors(sleep: str) -> str:
    time.sleep(0.5)
    return subprocess.run(["pgrep", "-f", sleep], capture_output=True, text=True).stdout


def test_the_deadline_ends_piped_children_of_a_nested_timeout():
    sleep = marker()

    result = subprocess.run([str(BOUNDED), "1", "--", "sh", "-c", f'timeout 60 sh -c "{sleep} | cat" & wait'], capture_output=True, text=True, timeout=15)

    assert result.returncode == 124, result.stderr
    assert survivors(sleep) == ""


def test_a_nested_timeout_orphaned_before_the_deadline_still_ends_with_it():
    sleep = marker()
    with subprocess.Popen([str(BOUNDED), "3", "--", "sh", "-c", f'timeout 1 sh -c "timeout 60 {sleep} & wait"; sleep 30']) as owner:
        time.sleep(2)
        assert survivors(sleep) != ""
        owner.wait(timeout=15)

    assert survivors(sleep) == ""


def test_killing_the_owner_ends_the_whole_tree():
    sleep = marker()
    with subprocess.Popen([str(BOUNDED), "30", "--", "sh", "-c", f'timeout 60 sh -c "{sleep} | cat" & wait']) as owner:
        time.sleep(1)
        owner.send_signal(signal.SIGTERM)
        assert owner.wait(timeout=15) == 128 + signal.SIGTERM

    assert survivors(sleep) == ""


def test_a_command_that_finishes_passes_its_status_through_and_leaves_no_background_child():
    sleep = marker()

    result = subprocess.run([str(BOUNDED), "10", "--", "sh", "-c", f"({sleep} &); echo done; exit 4"], capture_output=True, text=True, timeout=15)

    assert (result.returncode, result.stdout) == (4, "done\n")
    assert survivors(sleep) == ""


def test_a_command_already_running_above_itself_is_refused(tmp_path):
    loop = tmp_path / "loop.sh"
    loop.write_text(f'echo level\n"{BOUNDED}" 10 -- sh "{loop}"\necho "inner $?"\n')

    result = subprocess.run([str(BOUNDED), "10", "--", "sh", str(loop)], capture_output=True, text=True, timeout=15)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "level\ninner 3\n"
    assert "already runs above this process" in result.stderr


def test_bad_arguments_print_usage_and_exit_two():
    result = subprocess.run([str(BOUNDED), "1", "sleep", "1"], capture_output=True, text=True, timeout=5)

    assert result.returncode == 2
    assert result.stderr.startswith("usage: bounded.py")
