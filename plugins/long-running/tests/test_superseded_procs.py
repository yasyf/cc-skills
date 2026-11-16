import os

import pytest
from drive import procs, state, superseded_procs

PYTHON = "/venv/bin/python -I /cache/skills/long-running"


def test_the_watch_ends_only_parentless_reloads_and_long_proofs_an_older_install_left_on_its_drive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state, "VERSION", "1.0.1300")
    commands = {
        11: f"{PYTHON}/1.0.1094/bin/drive reload --seat coordinator --drive demo",
        12: f"{PYTHON}/1.0.1109/bin/drive job prove j1 --drive demo",
        13: f"{PYTHON}/1.0.1299/bin/drive job prove j2 --drive demo",
        14: f"{PYTHON}/1.0.1300/bin/drive reload --seat meta --drive demo",
        15: f"{PYTHON}/1.0.1094/bin/drive reload --seat meta --drive other",
        16: f"{PYTHON}/1.0.1094/bin/drive reload --seat meta --drive demo",
        17: f"{PYTHON}/1.0.1299/bin/drive pr check https://github.com/Forge-AI/monorepo/pull/5 --drive demo",
    }
    parents = {11: 1, 12: 1, 13: 1, 14: 1, 15: 1, 16: 4242, 17: 1}
    monkeypatch.setattr(procs, "table", lambda: [procs.Process(pid, parents[pid], pid, False) for pid in commands])
    monkeypatch.setattr(procs, "command", commands.get)
    monkeypatch.setattr(procs, "started", lambda pid: 0 if pid == 12 else 10_000)
    monkeypatch.setattr(superseded_procs.time, "time", lambda: 10_000.0)
    killed: list[int] = []
    monkeypatch.setattr(os, "kill", lambda pid, signum: killed.append(pid))
    detached: list[list[str]] = []
    monkeypatch.setattr(state, "detach", lambda name, args, log: detached.append([name, *args]) or 1)
    assert superseded_procs.end("demo", False) == [f"ended pid 12, left by an older install: {commands[12]}", f"ended pid 17, left by an older install: {commands[17]}"]
    assert (killed, detached) == ([12, 17], [])
    lines = superseded_procs.end("demo", True)
    assert (killed[2:], detached) == ([11, 12, 17], [["demo", "reload"]])
    assert lines == [f"ended pid {pid}, left by an older install: {commands[pid]}" for pid in (11, 12, 17)] + ["started drive reload on demo from long-running 1.0.1300"]


def test_the_drives_watch_ends_an_older_installs_handler_start_and_starts_no_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state, "VERSION", "1.0.1400")
    commands = {
        21: f"{PYTHON}/1.0.1349/bin/drive handler-start a1b2c3d4 --drive demo",
        22: f"{PYTHON}/1.0.1349/bin/drive handler-start b2c3d4e5 --drive demo",
        23: f"{PYTHON}/1.0.1400/bin/drive handler-start c3d4e5f6 --drive demo",
    }
    monkeypatch.setattr(procs, "table", lambda: [procs.Process(pid, 1, pid, False) for pid in commands])
    monkeypatch.setattr(procs, "command", commands.get)
    killed: list[int] = []
    monkeypatch.setattr(os, "kill", lambda pid, signum: killed.append(pid))
    detached: list[list[str]] = []
    monkeypatch.setattr(state, "detach", lambda name, args, log: detached.append([name, *args]) or 1)
    assert superseded_procs.end("demo", False) == []
    assert superseded_procs.end("demo", True) == [f"ended pid {pid}, left by an older install: {commands[pid]}" for pid in (21, 22)]
    assert (killed, detached) == ([21, 22], [])
