from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import rulings
from captain_hook.util import reqenv

from hooks import compaction_handoff as handoff
from test_compaction_handoff import bash, register_drive, session_start


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def owned_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    query = bin_dir / "ccn"
    pid = tmp_path / "query.pid"
    database = tmp_path / "query.sqlite"
    query.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sqlite3\n"
        "pathlib.Path(os.environ['QUERY_PID']).write_text(str(os.getpid()))\n"
        "sqlite3.connect(os.environ['QUERY_DB'], timeout=600).execute('BEGIN IMMEDIATE')\n"
        "print('[]')\n"
    )
    query.chmod(0o700)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("QUERY_PID", str(pid))
    monkeypatch.setenv("QUERY_DB", str(database))
    return query, database


def stop_owned_query(pid: int, query: Path) -> None:
    if not alive(pid):
        return
    row = subprocess.run(
        ["ps", "-ww", "-p", str(pid), "-o", "pid=,ppid=,pgid=,lstart=,tty=,command="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert str(query) in row, row
    print(f"owned query cleanup identity: {row.strip()}")
    os.kill(pid, signal.SIGKILL)


def test_register_query_timeout_leaves_no_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    register_drive(tmp_path, monkeypatch, checkout=str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("CLAUDE_LONG_RUNNING_DRIVE", "b7")
    monkeypatch.setattr(handoff, "CCN_TIMEOUT_SECONDS", 2)
    query, database = owned_query(tmp_path, monkeypatch)
    pid_path = tmp_path / "query.pid"
    session = tmp_path / "session"
    handoff.CompactionState(active=True, plan_path=str(tmp_path / "plan.md"), slug="durable-program").save(bash(session, cwd=str(tmp_path)))
    with sqlite3.connect(database) as writer:
        writer.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(subprocess.TimeoutExpired):
                handoff.reground(session_start(session, "compact"))
            assert pid_path.exists(), "the actual SQLite-blocked query never started"
            pid = int(pid_path.read_text())
            assert not alive(pid), f"query survived register budget: pid={pid}"
            with pytest.raises(ChildProcessError):
                os.waitpid(pid, os.WNOHANG)
        finally:
            if pid_path.exists():
                stop_owned_query(int(pid_path.read_text()), query)


def test_register_read_expected_exit_is_command_failure() -> None:
    reader = handoff.RegisterRead(SimpleNamespace(), {"drive": "b7"})
    failure = SystemExit("missing register ledger")
    with pytest.raises(subprocess.CalledProcessError) as caught:
        with reader.errors():
            raise failure
    assert caught.value.__cause__ is failure
    assert caught.value.returncode == 1
    assert caught.value.stderr == "missing register ledger\n"


@pytest.mark.parametrize("failure", [RuntimeError("unexpected register fault"), subprocess.CalledProcessError(7, ["ccn"]), subprocess.TimeoutExpired(["ccn"], 20)])
def test_register_read_fault_propagates_unchanged(failure: Exception) -> None:
    reader = handoff.RegisterRead(SimpleNamespace(), {"drive": "b7"})
    with pytest.raises(type(failure)) as caught:
        with reader.errors():
            raise failure
    assert caught.value is failure


def test_register_read_uses_one_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [10.0]
    timeouts = []

    def call_cli(argv, **kwargs):
        timeouts.append(kwargs["timeout"])
        clock[0] += 12
        return "result"

    monkeypatch.setattr(handoff, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    reader = handoff.RegisterRead(SimpleNamespace(ctx=SimpleNamespace(call_cli=call_cli)), {"drive": "b7"})
    assert reader.run(["ccn", "first"]) == "result"
    with pytest.raises(subprocess.TimeoutExpired):
        reader.run(["ccn", "second"])
    assert timeouts == [20, 8]


def test_abandoned_register_read_starts_no_query(tmp_path: Path) -> None:
    flag = reqenv.Cutoff()
    flag.close()
    calls = []
    reader = handoff.RegisterRead(SimpleNamespace(ctx=SimpleNamespace(call_cli=lambda *a, **kw: calls.append(a))), {"drive": "b7"})
    with reqenv.abandonable(flag), pytest.raises(reqenv.Abandoned):
        reader.run(["ccn", "doc", "list"])
    assert calls == []


def test_shared_root_slice_and_register_bytes_match_cli(tmp_path: Path) -> None:
    reads = handoff.script_module("long_running.register_reads")
    rows = {
        "rule/2": {"kind": "order", "lanes": '["root"]', "words": "Root wording", "status": "in-force", "implications": '[{"n":1,"text":"verbatim","direction":"requires","state":"pending"}]'},
        "rule/1": {"kind": "scope", "words": "Every scope", "status": "in-force"},
        "no/3": {"kind": "scope", "words": "Not a ruling"},
        "rule/4": {"kind": "scope", "words": "Superseded", "status": "superseded"},
    }
    loaded = rulings.Register(None, "/repo", "brook", tmp_path, "a" * 40, rows)
    assert reads.root_slice("brook", "a" * 40, rows) == rulings.root_slice(loaded)
    assert "Root wording" in reads.root_slice("brook", "a" * 40, rows)
    assert "Not a ruling" not in reads.root_slice("brook", "a" * 40, rows)
    docs = [{"id": "a", "body": "earlier", "updated_at": "1"}, {"id": "b", "body": "later", "updated_at": "2"}]
    run = lambda argv: json.dumps(docs)
    assert reads.register(run, "/repo", "brook") == rulings.register(SimpleNamespace(run=run), "/repo", "brook") == {"id": "b", "body": "later"}


def test_shared_module_load_does_not_change_import_path() -> None:
    before = list(sys.path)
    reads = handoff.script_module("long_running.register_reads")
    assert reads.program_of({"state_dir": "/state/program"}) == "program"
    assert sys.path == before


def test_register_read_cli_oracle(tmp_path: Path) -> None:
    path = os.environ.get("REGISTER_READ_ORACLE")
    if not path:
        pytest.skip("CI provides the immutable CLI oracle")
    fields = {
        "rule/1": {"kind": "scope", "status": "in-force", "words": "Every scope\nverbatim", "at": "2026-01-01", "implications": '[{"n":1,"text":"required","direction":"requires","state":"pending"}]'},
        "rule/2": {"kind": "order", "lanes": '["root"]', "status": "in-force", "words": "Root only", "repeats": '[{"at":"later","words":"again"}]'},
        "rule/3": {"kind": "scope", "status": "superseded", "words": "Excluded"},
    }
    docs = [{"id": "earlier", "body": "old", "updated_at": "1"}, {"id": "later", "body": "new", "updated_at": "2"}]
    shell = SimpleNamespace(run=lambda argv: json.dumps(docs))
    value = {"doc": rulings.register(shell, "/repo", "brook"), "root": rulings.root_slice(rulings.Register(shell, "/repo", "brook", tmp_path, "a" * 40, fields))}
    output = json.dumps(value, ensure_ascii=False)
    if os.environ.get("REGISTER_READ_ORACLE_WRITE") == "1":
        Path(path).write_text(output)
    else:
        assert output == Path(path).read_text()
    print(f"register read immutable oracle: {output}")


def test_lock_wait_obeys_the_same_register_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "register.lock"
    code = "import fcntl,sys; h=open(sys.argv[1],'a+'); fcntl.flock(h,fcntl.LOCK_EX); print('ready',flush=True); sys.stdin.read(1)"
    with subprocess.Popen([sys.executable, "-c", code, str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as holder:
        try:
            assert holder.stdout.readline() == "ready\n"
            monkeypatch.setattr(handoff, "CCN_TIMEOUT_SECONDS", 0.2)
            reader = handoff.RegisterRead(SimpleNamespace(), {"drive": "b7"})
            with pytest.raises(subprocess.TimeoutExpired):
                with reader.lock(path):
                    pytest.fail("the register lock was stolen from a live holder")
            assert holder.poll() is None
        finally:
            holder.communicate(input="x", timeout=5)


def test_reground_shares_one_budget_across_both_register_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    register_drive(tmp_path, monkeypatch, checkout=str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("CLAUDE_LONG_RUNNING_DRIVE", "b7")
    session = tmp_path / "session"
    handoff.CompactionState(active=True, plan_path=str(tmp_path / "plan.md"), slug="durable-program").save(bash(session))
    clock = [0.0]
    readers, remaining, blockers = [], [], []

    def register(reader):
        readers.append(reader)
        remaining.append(reader.remaining())
        clock[0] += 12
        return None

    def root_slice(reader):
        readers.append(reader)
        remaining.append(reader.remaining())
        clock[0] += 12
        reader.remaining()
        return "late slice"

    monkeypatch.setattr(handoff, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(handoff.RegisterRead, "register", register)
    monkeypatch.setattr(handoff.RegisterRead, "root_slice", root_slice)
    monkeypatch.setattr(handoff, "post_blocker", lambda evt, text: blockers.append(text))
    handoff.reground(session_start(session, "compact"))
    assert readers[0] is readers[1]
    assert remaining == [20, 8]
    assert len(blockers) == 1 and "timed out after 20s" in blockers[0]
    assert handoff.CompactionState.load(bash(session)).root_slice is None
