"""The landing desk's Go port run against a test's live FakeShell: with LONG_RUNNING_LANDING=go, the tests of test_desk_runner.py that desk/testdata/landing-go-tests.txt names run `run_landing` as `$LONG_RUNNING_LANDING_BIN loop`, every Shell call it makes served here over a unix socket, and the rest of that module is deselected."""

from __future__ import annotations

import dataclasses
import inspect
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

ALLOWLIST = Path(__file__).resolve().parents[1] / "desk" / "testdata" / "landing-go-tests.txt"
MODULE = "test_desk_runner.py"
BRIDGE = "LONG_RUNNING_BRIDGE"
PLUGIN_ROOT = "LONG_RUNNING_PLUGIN_ROOT"
PYTHON = "LONG_RUNNING_PYTHON"
DISCOVER = "LONG_RUNNING_LANDING_DISCOVER"
EXIT_RAISED = 86
RAN: set[str] = set()
PASSED: set[str] = set()


def enabled() -> bool:
    return os.environ.get("LONG_RUNNING_LANDING") == "go"


def discovering() -> str:
    """The file a discovery run writes the ids of the tests that ran the Go desk and passed to, or empty."""
    return os.environ.get(DISCOVER, "") if enabled() else ""


def key(item: pytest.Item) -> str:
    return f"{item.path.name}::{item.nodeid.partition('::')[2]}"


def nodeid_key(nodeid: str) -> str:
    path, _, name = nodeid.partition("::")
    return f"{Path(path).name}::{name}"


def landing_driven(item: pytest.Item) -> bool:
    function = getattr(item, "function", None)
    source = inspect.getsource(function) if function else ""
    return "landing_pass(" in source or "run_landing(" in source


def narrow(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deselect every test of `MODULE` the allowlist does not name, refusing an allowlist that is empty or names a test the module does not have; a discovery run keeps every test of `MODULE` that runs a landing pass instead."""
    if not enabled() or not any(item.path.name == MODULE for item in items):
        return
    if discovering():
        kept = [item for item in items if item.path.name != MODULE or landing_driven(item)]
        config.hook.pytest_deselected(items=[item for item in items if item not in kept])
        items[:] = kept
        return
    named = [line.strip() for line in ALLOWLIST.read_text().splitlines() if line.strip()]
    if not named:
        raise pytest.UsageError(f"{ALLOWLIST} names no test")
    collected = {key(item) for item in items if item.path.name == MODULE}
    if missing := [name for name in named if name not in collected]:
        raise pytest.UsageError(f"{ALLOWLIST} names tests {MODULE} does not have: {', '.join(missing)}")
    kept = [item for item in items if item.path.name != MODULE or key(item) in named]
    config.hook.pytest_deselected(items=[item for item in items if item not in kept])
    items[:] = kept


class Bridge:
    """A unix socket serving one JSON request per line, each dispatched to `shell`, one thread per connection; the newest exception a call raised is kept for the test to re-raise."""

    def __init__(self, shell, module):
        self.shell, self.module = shell, module
        self.handles: dict[int, object] = {}
        self.raised: BaseException | None = None
        self.directory = tempfile.mkdtemp(prefix="lrb-", dir="/tmp")
        self.path = os.path.join(self.directory, "shell.sock")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(64)
        threading.Thread(target=self.accept, name="landing-bridge", daemon=True).start()

    def accept(self) -> None:
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(connection,), daemon=True).start()

    def serve(self, connection: socket.socket) -> None:
        with connection, connection.makefile("rb") as requests:
            for line in requests:
                request = json.loads(line)
                try:
                    reply = {"result": self.dispatch(request)}
                except BaseException as error:
                    self.raised = error
                    reply = {"raised": {"type": type(error).__name__, "message": str(error)}}
                connection.sendall(json.dumps(reply).encode() + b"\n")

    def dispatch(self, request: dict):
        shell, op = self.shell, request["op"]
        if op == "run":
            return done(shell.run(request["argv"], stdin=request["stdin"], env=request["env"], cwd=request["cwd"]))
        if op == "cci_query":
            return done(shell.cci_query(request["argv"], env=request["env"], cwd=request["cwd"]))
        if op == "pass_cost":
            return shell.pass_cost()
        if op == "in_flight":
            flight = shell.in_flight()
            return {"name": flight[0], "seconds": flight[1]} if flight else None
        if op == "adopt":
            return shell.adopt(request["values"])
        if op == "reexec":
            return shell.reexec(request["argv"])
        if op == "launchctl":
            return done(shell.launchctl(*request["args"]))
        if op == "spawn":
            process = shell.spawn(request["argv"], Path(request["out"]), request["env"], append=request["append"])
            handle = len(self.handles) + 1
            self.handles[handle] = process
            return {"handle": handle, "pid": getattr(process, "pid", 0)}
        if op == "poll":
            return self.handles[request["handle"]].poll()
        if op == "runs_job":
            return shell.runs_job(request["pid"], Path(request["directory"]))
        if op == "stat":
            stat = shell.stat(Path(request["path"]))
            return None if stat is None else json.dumps(stat)
        if op == "now":
            return shell.now().isoformat()
        if op == "monotonic":
            return shell.now().timestamp()
        if op == "sleep":
            return shell.sleep(request["seconds"])
        if op == "load":
            return shell.load()
        if op == "cores":
            return shell.cores()
        if op == "env":
            return shell.env(request["name"])
        if op == "getenv":
            return os.environ.get(request["name"], "")
        if op == "pid":
            return os.getpid()
        if op == "write":
            stream = getattr(sys, request["stream"])
            stream.write(request["text"])
            stream.flush()
            return None
        if op == "judgment":
            verdict = self.module.judge.judge(request["kind"], request["evidence"], request["artifact"], backend=self.module.judge.backend_from(os.environ), drive=request["drive"])
            return None if verdict is None else dataclasses.asdict(verdict)
        raise ValueError(f"the landing bridge serves no op {op}")

    def close(self) -> None:
        self.server.close()
        shutil.rmtree(self.directory)


def done(result) -> dict:
    return {"code": result.code, "out": result.out, "err": result.err}


def run_landing(module, binary: str):
    """`module.run_landing` as the Go binary at `binary`, its Shell the runner's own; its output reaches sys.stdout and sys.stderr, its exit code is the return, and an exception the Shell raised that the desk did not handle is raised here."""

    def run(runner, once: bool) -> int:
        RAN.add(nodeid_key(os.environ["PYTEST_CURRENT_TEST"].rpartition(" ")[0]))
        bridge = Bridge(runner.shell, module)
        try:
            child = subprocess.run([binary, "loop", "--config", str(runner.config.source), *(["--once"] if once else [])], env={**os.environ, BRIDGE: bridge.path, PLUGIN_ROOT: str(module.SCRIPTS.parents[2]), PYTHON: sys.executable}, capture_output=True, text=True)
        finally:
            bridge.close()
        sys.stdout.write(child.stdout)
        sys.stderr.write(child.stderr)
        if child.returncode == EXIT_RAISED:
            raise bridge.raised or RuntimeError(f"{binary} exited {EXIT_RAISED} with no exception raised through its Shell")
        return child.returncode

    return run


def logreport(report: pytest.TestReport) -> None:
    """Keep each discovered test whose call ran the Go desk and passed, dropping it again when its teardown fails."""
    if not discovering():
        return
    name = nodeid_key(report.nodeid)
    if report.when == "call" and report.passed and name in RAN:
        PASSED.add(name)
    elif report.failed:
        PASSED.discard(name)


def write_discovered() -> None:
    if path := discovering():
        Path(path).write_text("".join(f"{name}\n" for name in sorted(PASSED)))
