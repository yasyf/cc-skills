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


def test_a_verb_started_anywhere_under_the_watch_ends_once_the_watch_ends_and_one_no_watch_started_runs_on() -> None:
    script = f"import sys, time; sys.path.insert(0, {str(LIB)!r}); from drive import children; children.die_with_watch(); time.sleep(30)"
    loop = f"import subprocess, sys; print(subprocess.Popen([sys.executable, '-c', {script!r}], start_new_session=True).pid, flush=True)"
    watch = subprocess.Popen(
        [sys.executable, "-c", f"import subprocess, sys, time; sys.path.insert(0, {str(LIB)!r}); from drive import children; children.mark_watch(); subprocess.run([sys.executable, '-c', {loop!r}]); time.sleep(2)"],
        stdout=subprocess.PIPE,
        text=True,
    )
    started = int(watch.stdout.readline())
    other = subprocess.Popen([sys.executable, "-c", script], env={key: value for key, value in os.environ.items() if key != children.WATCH_PID_ENV}, start_new_session=True)
    assert state.alive(started)
    watch.wait()
    deadline = time.time() + 10
    while state.alive(started) and time.time() < deadline:
        time.sleep(0.2)
    assert not state.alive(started)
    assert other.poll() is None
    other.kill()


def test_a_capped_verb_waits_for_its_slot_before_the_cli_reads_anything_and_any_other_verb_runs_as_it_is(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(children, "SLOTS", 1)
    monkeypatch.delenv(children.WATCH_PID_ENV, raising=False)
    held = []

    @children.capped
    def capped(args: object) -> int:
        held.append(taken(state.root() / "slots"))
        return 0

    with children.held(capped):
        capped(None)
    with children.held(lambda args: 0):
        held.append(taken(state.root() / "slots") if (state.root() / "slots").exists() else [])
    assert held == [[0], []]
