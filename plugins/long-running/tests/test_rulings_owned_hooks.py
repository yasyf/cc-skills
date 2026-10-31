from __future__ import annotations

import ast
import copy
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from captain_hook.util import reqenv
from long_running import rulings

from hooks import compaction_handoff as handoff
from test_compaction_handoff import register_drive, session_start
from test_register_reads import alive, owned_query, stop_owned_query
from test_rulings import REPO, RegisterShell, home, registered, state

HOOKS = Path(handoff.__file__).parent
CALLERS = (
    "board_text_gate", "narrowed_contracts", "owner_facing", "register_capture",
    "register_gates", "register_injection", "ruling_judge", "settled_questions", "ruling_misread",
)


def caller_expressions():
    result = []
    for module in CALLERS:
        path = HOOKS / f"{module}.py"
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"rulings", "register_of"}:
                result.append(pytest.param(module, node, id=f"{module}:{node.lineno}"))
    assert len(result) == 14
    return result


@pytest.mark.parametrize("module,node", caller_expressions())
def test_all_current_hook_call_expressions_reach_real_dispatch_without_a_python_intermediary(module, node, home: Path, registered: Path, state: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    traces = []
    backend = RegisterShell({}, records=[{"seq": 8, "kind": "state", "text": "not a contract"}])

    def call_cli(argv, *, input=None, cwd=None, env=None, timeout=None):
        assert timeout > 0
        assert cwd == REPO
        assert env[handoff.DEADLINE_ENV]
        traces.append((list(argv), input, cwd))
        return backend.run(argv, input, Path(cwd))

    evt = SimpleNamespace(cwd=Path(REPO), ctx=SimpleNamespace(call_cli=call_cli))
    which = ["--drive", "brook-drive"]
    reads = SimpleNamespace(repo=REPO, which=which, evt=evt)
    brief = tmp_path / "brief.md"
    original_brief = "Register slice: lane=lane-a step=1\nold\n"
    brief.write_text(original_brief)
    scope = {
        "rulings": lambda cwd, *args, **kwargs: (cwd, args, kwargs),
        "register_of": lambda cwd, *args, **kwargs: (cwd, ("register", *args), kwargs),
        "evt": evt, "reads": reads, "self": reads, "which": which,
        "flags": [] if module == "board_text_gate" else which,
        "drive": which if module in {"narrowed_contracts", "ruling_misread"} else "brook-drive",
        "seq": "8", "text": "owner words", "key": "owner words",
        "lane": "lane-a", "copy": brief, "worker": "brook-drive",
        "CANDIDATES": 3, "CANDIDATE_BYTES": 1024, "excluded": [],
        "MOMENT_CHARS": 1024, "MATCH_TIMEOUT_SECONDS": 2, "reqenv": reqenv,
        "ROWS_PER_ARTIFACT": 2, "MATCH_BYTES": 6000, "MATCH_CHARS": 2000,
    }
    expression = compile(ast.Expression(copy.deepcopy(node)), str(HOOKS / f"{module}.py"), "eval")
    cwd, args, kwargs = eval(expression, scope)
    expected = io.StringIO()
    code = 0
    try:
        code = rulings.main([*args, "--repo", cwd], RegisterShell({}, records=[{"seq": 8, "kind": "state", "text": "not a contract"}]), stdin=io.StringIO(kwargs.get("stdin", "")), stdout=expected)
    except SystemExit as error:
        code = error.code
    captured = capsys.readouterr()
    assert captured.out == ""
    shutil.rmtree(state)
    state.mkdir()
    brief.write_text(original_brief)
    scope.update(rulings=handoff.rulings, register_of=handoff.register_of)
    if code:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            eval(expression, scope)
        assert caught.value.returncode == code
        assert caught.value.stdout == expected.getvalue()
        assert caught.value.stderr == captured.err
    else:
        actual = eval(expression, scope)
        assert actual == (json.loads(expected.getvalue()) if node.func.id == "register_of" else expected.getvalue())
    assert traces
    assert all(argv[0] != sys.executable for argv, _, _ in traces)
    if module == "register_injection" and args[0] == "slice":
        assert "slice-hash" in brief.read_text()
    assert capsys.readouterr() == ("", "")


def test_generic_owned_dispatch_timeout_reaps_the_actual_sqlite_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    register_drive(tmp_path, monkeypatch, checkout=str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("CLAUDE_LONG_RUNNING_DRIVE", "b7")
    query, database = owned_query(tmp_path, monkeypatch)
    pid_path = tmp_path / "query.pid"
    evt = session_start(tmp_path / "session", "compact")
    with sqlite3.connect(database) as writer:
        writer.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(subprocess.TimeoutExpired):
                handoff.rulings(str(tmp_path), "register", "--drive", "b7", evt=evt, timeout=2)
            assert pid_path.exists()
            pid = int(pid_path.read_text())
            assert not alive(pid), f"direct owned query survived its budget: {pid}"
            with pytest.raises(ChildProcessError):
                os.waitpid(pid, os.WNOHANG)
        finally:
            if pid_path.exists():
                stop_owned_query(int(pid_path.read_text()), query)


def wait_for_query(path: Path):
    deadline = time.monotonic() + 5
    while not (path.exists() and (value := path.read_text().strip())):
        assert time.monotonic() < deadline, "the actual SQLite reader did not start"
        time.sleep(0.01)
    return int(value)


def test_removed_python_intermediary_loses_its_sqlite_child_on_owner_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    register_drive(tmp_path, monkeypatch, checkout=str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    query, database = owned_query(tmp_path, monkeypatch)
    pid_path = tmp_path / "query.pid"
    argv = [sys.executable, str(handoff.script("rulings.py")), "register", "--drive", "b7", "--repo", str(tmp_path)]
    with sqlite3.connect(database) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with subprocess.Popen(argv, cwd=tmp_path, env=handoff.deadline_env(20), stdout=subprocess.PIPE, stderr=subprocess.PIPE) as helper:
            try:
                pid = wait_for_query(pid_path)
                identity = subprocess.run(["ps", "-ww", "-p", str(helper.pid), "-o", "pid=,ppid=,pgid=,tty=,lstart=,command="], capture_output=True, text=True, check=True).stdout.strip()
                fields = identity.split(None, 9)
                assert int(fields[0]) == helper.pid and int(fields[1]) == os.getpid()
                assert int(fields[2]) == os.getpgrp() and fields[3]
                assert len(fields) == 10 and " ".join(fields[4:9])
                assert str(handoff.script("rulings.py")) in fields[9] and str(tmp_path) in fields[9]
                assert helper.poll() is None
                print(f"owned historical intermediary identity: {identity}")
                helper.terminate()
                helper.communicate(timeout=5)
                assert alive(pid), "the fixture did not expose the removed intermediary's orphaning failure"
            finally:
                if pid_path.exists():
                    stop_owned_query(int(pid_path.read_text()), query)


def test_concurrent_owned_dispatches_keep_cwd_environment_roots_and_streams_request_local(tmp_path: Path):
    barrier = threading.Barrier(2)
    environment = dict(os.environ)
    import_path = list(sys.path)
    roots = [tmp_path / str(index) for index in range(2)]
    for root in roots:
        drives = root / ".claude" / "long-running" / "drives"
        drives.mkdir(parents=True)
        (drives / "same.json").write_text(json.dumps({"drive": "same", "program": "brook", "state_dir": str(root / "state"), "checkout": REPO}))

    def call(index):
        root = roots[index]
        selected = {"HOME": str(root), "CLAUDE_CONFIG_DIR": str(root / ".claude"), "CLAUDE_LONG_RUNNING_DRIVE": "same"}
        backend = RegisterShell({"rule/1": {"kind": "scope", "status": "in-force", "words": str(index)}})

        def call_cli(argv, *, input=None, cwd=None, env=None, timeout=None):
            assert cwd == REPO and env["HOME"] == str(root)
            assert env["CLAUDE_CONFIG_DIR"] == str(root / ".claude")
            if argv[0] == "git":
                barrier.wait(timeout=5)
            return backend.run(argv, input, Path(cwd))

        evt = SimpleNamespace(cwd=Path(REPO), ctx=SimpleNamespace(call_cli=call_cli))
        with reqenv.use_request(reqenv.RequestOverrides(env=selected, cwd=REPO)):
            return json.loads(handoff.rulings(REPO, "rows", "--json", evt=evt))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(call, [0, 1]))
    assert [result["state_dir"] for result in results] == [str(root / "state") for root in roots]
    assert [result["rows"]["rule/1"]["words"] for result in results] == ["0", "1"]
    assert os.environ == environment
    assert sys.path == import_path


def test_generic_owned_dispatch_cancellation_reaps_its_direct_sqlite_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    register_drive(tmp_path, monkeypatch, checkout=str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    query, database = owned_query(tmp_path, monkeypatch)
    pid_path = tmp_path / "query.pid"
    evt = session_start(tmp_path / "session", "compact")
    flag = reqenv.Cutoff()

    def call():
        with reqenv.abandonable(flag):
            return handoff.rulings(str(tmp_path), "register", "--drive", "b7", evt=evt)

    with sqlite3.connect(database) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(call)
            try:
                pid = wait_for_query(pid_path)
                identity = subprocess.run(["ps", "-p", str(pid), "-o", "ppid="], capture_output=True, text=True, check=True).stdout.strip()
                assert int(identity) == os.getpid(), "a Python intermediary still owns the actual query"
                flag.close()
                with pytest.raises(reqenv.Abandoned):
                    result.result(timeout=5)
                assert not alive(pid)
                with pytest.raises(ChildProcessError):
                    os.waitpid(pid, os.WNOHANG)
            finally:
                flag.close()
                if pid_path.exists():
                    stop_owned_query(int(pid_path.read_text()), query)
