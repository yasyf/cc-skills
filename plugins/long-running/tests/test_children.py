import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from drive import children, state

LIB = Path(__file__).resolve().parents[1] / "lib"


def taken(folder: Path) -> list[int]:
    held = []
    for index in range(children.SLOTS):
        with (folder / f"child-{index}.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                held.append(index)
    return held


def test_each_check_or_wake_holds_one_of_the_slots_every_watch_shares_until_it_ends(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(children, "SLOTS", 2)
    folder = state.root() / "slots"
    with children.slot():
        assert taken(folder) == [0]
        with children.slot():
            assert taken(folder) == [0, 1]
        assert taken(folder) == [0]
    assert taken(folder) == []


def test_a_verb_the_watch_started_ends_once_the_watch_ends_and_one_anything_else_started_runs_on(tmp_path: Path) -> None:
    script = f"import sys, time; sys.path.insert(0, {str(LIB)!r}); from drive import children; children.die_with_watch(); time.sleep(30)"
    watch = subprocess.Popen(
        [sys.executable, "-c", f"import os, subprocess, sys; os.environ[{children.WATCH_PID_ENV!r}] = str(os.getpid()); print(subprocess.Popen([sys.executable, '-c', {script!r}], start_new_session=True).pid, flush=True); import time; time.sleep(2)"],
        stdout=subprocess.PIPE,
        text=True,
    )
    started = int(watch.stdout.readline())
    other = subprocess.Popen([sys.executable, "-c", script], env={**os.environ, children.WATCH_PID_ENV: "1"}, start_new_session=True)
    watch.wait()
    deadline = time.time() + 10
    while state.alive(started) and time.time() < deadline:
        time.sleep(0.2)
    assert not state.alive(started)
    assert other.poll() is None
    other.kill()
