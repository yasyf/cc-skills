from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from captain_hook.util import reqenv
from long_running import judge, rulings
from test_check_pr import GRANT_TICKER, HEAD, TIMER_ANSWER_ROW, CheckShell, citing, pinned
from test_compaction_handoff import session_start
from test_rulings import DIFF, REPO, home, package, registered, state
from test_rulings_dispatch import runtime

DIGEST = "a" * 16
PROOF = f"proof:board:{DIGEST}"
YES = {"verdict": "yes", "artifact_phrase": "changed invariant", "evidence_phrase": "owner words", "sentence": "It changes his invariant."}
SHAPE = DIFF + '''diff --git a/tests/test_pool_count.py b/tests/test_pool_count.py
--- /dev/null
+++ b/tests/test_pool_count.py
@@ -0,0 +1,3 @@
+def test_one_pool_process_per_run():
+    """rule/bbbbbbb: one pool process drains every run."""
+    assert pool_processes(runs=2) == 2
'''


def response(argv: list[str], card: str = "The card he answered") -> str:
    if argv[0] == "cc-present":
        return card
    if argv[0] == "claude":
        return json.dumps({"structured_output": YES, "total_cost_usd": 0.01, "usage": {"input_tokens": 10, "output_tokens": 2}})
    assert argv[:2] == ["uv", "run"]
    return json.dumps({"p_yes": 0.9, "tokens": 8})


def owner(state: Path) -> dict:
    state.mkdir(parents=True, exist_ok=True)
    (state / "owner-prompts.jsonl").write_text(json.dumps({"digest": DIGEST, "source": "board", "block": "question", "session": "s-root"}) + "\n")
    return {"words": "Owner words", "proof": PROOF}


def environment(root: Path, state: Path, backend: str) -> dict[str, str]:
    selected = dict(os.environ) | {"HOME": str(root), "CLAUDE_CONFIG_DIR": str(root / "config"), judge.BACKEND_ENV: backend}
    drives = root / "config" / "long-running" / "drives"
    drives.mkdir(parents=True, exist_ok=True)
    (drives / "brook-drive.json").write_text(json.dumps({"drive": "brook-drive", "program": "brook", "state_dir": str(state), "checkout": REPO, "cci_drive": "brook-cci", "sessions": ["s-root"]}))
    return selected


def logged(state: Path) -> list[dict]:
    return [json.loads(line) for line in (state / judge.CALLS).read_text().splitlines()]


@pytest.mark.parametrize("backend", judge.BACKENDS)
def test_default_cli_transport_and_explicit_capability_keep_the_same_cards_verdicts_commands_and_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str):
    selected = environment(tmp_path, tmp_path / "cli", backend)
    for name, value in selected.items():
        monkeypatch.setenv(name, value)
    cli_calls = []
    owned_calls = []

    def cli(argv, **kwargs):
        cli_calls.append((argv, kwargs.get("input"), kwargs["timeout"]))
        assert kwargs["capture_output"] and kwargs["text"] and kwargs["check"]
        assert kwargs["env"] is None
        return subprocess.CompletedProcess(argv, 0, response(argv), "ignored diagnostic")

    def owned(argv, *, input=None, timeout, env=None):
        owned_calls.append((argv, input, timeout))
        assert env == selected
        return response(argv)

    monkeypatch.setattr(judge.subprocess, "run", cli)
    cli_state, owned_state = tmp_path / "cli", tmp_path / "owned"
    evidence = judge.owner_words("rule/x", judge.said(owner(cli_state), cli_state))
    expected = judge.judge("mechanism", evidence, "artifact", backend=judge.backend_from(os.environ), drive="brook-drive")
    selected = environment(tmp_path, owned_state, backend)
    actual_evidence = judge.owner_words("rule/x", judge.said(owner(owned_state), owned_state, selected, run=owned))
    actual = judge.judge("mechanism", actual_evidence, "artifact", backend=judge.backend_from(selected), drive="brook-drive", env=selected, run=owned)
    assert actual == expected and actual_evidence == evidence
    assert owned_calls == cli_calls
    assert (owned_state / judge.CARDS / f"{DIGEST}.txt").read_bytes() == (cli_state / judge.CARDS / f"{DIGEST}.txt").read_bytes()
    normalize = lambda rows: [{k: v for k, v in row.items() if k not in {"at", "seconds"}} for row in rows]
    assert normalize(logged(owned_state)) == normalize(logged(cli_state))
    if backend != "jev":
        assert next(argv for argv, _, _ in cli_calls if argv[0] == "claude")[3:5] == ["claude-sonnet-5-5", "--effort"]


@pytest.mark.parametrize("branch", ["shape", "wait"])
@pytest.mark.parametrize("backend", ["sonnet", "jev+sonnet"])
def test_check_pr_reaches_real_cold_and_warm_board_and_model_paths_with_request_roots(home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch: str, backend: str):
    rows = pinned(package)
    key = "rule/bbbbbbb" if branch == "shape" else "rule/abcabca"
    if branch == "wait":
        rows[key] = dict(TIMER_ANSWER_ROW)
    rows[key]["proof"] = PROOF
    request_state = tmp_path / "request-state"
    owner(request_state)
    selected = environment(tmp_path / "request-home", request_state, backend)
    monkeypatch.setenv(judge.BACKEND_ENV, "jev" if backend == "sonnet" else "sonnet")
    shell = CheckShell(rows, diff=SHAPE if branch == "shape" else DIFF + GRANT_TICKER, body=citing("- abcabca: the signal keeps its timer."))
    calls = []

    def run(argv, *, input=None, timeout, env=None):
        assert env == selected
        calls.append((argv, input, timeout))
        return response(argv, f"request {branch} card")

    def forbidden(*args, **kwargs):
        raise AssertionError("request escaped to raw subprocess")

    monkeypatch.setattr(judge.subprocess, "run", forbidden)
    request = replace(runtime(tmp_path / "runtime"), drives=Path(selected["CLAUDE_CONFIG_DIR"]) / "long-running" / "drives", run=run, env=selected)
    argv = ["check-pr", "--drive", "brook-drive", "--repo", REPO, "--pr", "acme/app#12", "--head", HEAD]
    assert rulings.dispatch(argv, shell, request) == 1
    first = request.stdout.getvalue()
    assert ("SHAPE-UNTESTED" if branch == "shape" else "MECHANISM-BANNED timer") in first
    assert any("request " + branch + " card" in (stdin or "") for command, stdin, _ in calls if command[0] != "cc-present")
    model_calls = len([command for command, _, _ in calls if command[0] != "cc-present"])
    request.stdout.seek(0)
    request.stdout.truncate()
    assert rulings.dispatch(argv, shell, request) == 1
    assert request.stdout.getvalue() == first
    assert len([command for command, _, _ in calls if command[0] == "cc-present"]) == 1
    assert len([command for command, _, _ in calls if command[0] != "cc-present"]) == model_calls * (1 if branch == "shape" else 2)
    assert {row["backend"] for row in logged(request_state)} == {backend}
    assert not (state / judge.CALLS).exists()
    assert request.stderr.getvalue() == ""


def test_concurrent_judgments_keep_cards_backends_environment_and_record_roots_local(tmp_path: Path):
    before = dict(os.environ), os.getcwd(), list(sys.path), sys.stdout, sys.stderr
    barrier = threading.Barrier(2)

    def request(index: int):
        state = tmp_path / str(index) / "state"
        selected = environment(tmp_path / str(index), state, "sonnet" if index == 0 else "jev")
        calls = []

        def run(argv, *, input=None, timeout, env=None):
            assert env == selected
            calls.append(argv[0])
            if argv[0] == "cc-present":
                barrier.wait(timeout=5)
            return response(argv, f"card {index}")

        evidence = judge.owner_words("rule/x", judge.said(owner(state), state, selected, run=run))
        assert f"card {index}" in evidence
        assert judge.judge("mechanism", evidence, "artifact", backend=judge.backend_from(selected), drive="brook-drive", env=selected, run=run).flagged
        return calls, logged(state)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(request, [0, 1]))
    assert results[0][0] == ["cc-present", "claude", "claude"]
    assert results[1][0] == ["cc-present", "uv"]
    assert [rows[0]["backend"] for _, rows in results] == ["sonnet", "jev"]
    assert (dict(os.environ), os.getcwd(), list(sys.path), sys.stdout, sys.stderr) == before


@pytest.mark.parametrize("leaf", ["board", "claude", "jev"])
@pytest.mark.parametrize("failure", [subprocess.CalledProcessError(7, ["leaf"], stderr="unavailable"), OSError("missing"), subprocess.TimeoutExpired(["leaf"], 10)])
def test_ordinary_leaf_failures_and_timeouts_still_fail_open(tmp_path: Path, leaf: str, failure: Exception):
    def run(*args, **kwargs):
        raise failure

    if leaf == "board":
        assert judge.said(owner(tmp_path), tmp_path, run=run)[0]["answered"] is None
    else:
        selected = environment(tmp_path, tmp_path / "state", "sonnet" if leaf == "claude" else "jev")
        assert judge.judge("mechanism", "evidence", "artifact", backend=judge.backend_from(selected), drive="brook-drive", env=selected, run=run) is None
        assert logged(tmp_path / "state")[0]["error"].startswith(type(failure).__name__)


@pytest.mark.parametrize("leaf", ["board", "claude", "jev"])
@pytest.mark.parametrize("failure", [SystemExit("leaf exit"), RuntimeError("engine fault"), reqenv.Abandoned()])
def test_leaf_unwinds_are_not_reclassified(tmp_path: Path, leaf: str, failure: BaseException):
    def run(*args, **kwargs):
        raise failure

    with pytest.raises(type(failure)) as caught:
        if leaf == "board":
            judge.said(owner(tmp_path), tmp_path, run=run)
        else:
            judge.judge("mechanism", "evidence", "artifact", backend="sonnet" if leaf == "claude" else "jev", run=run)
    assert caught.value is failure


def test_cards_and_confirmation_calls_share_one_owner_budget_and_do_not_commit_after_expiry(tmp_path: Path):
    clock = [0.0]
    expired = subprocess.TimeoutExpired(["check-pr"], 60)
    limits = []

    def checkpoint():
        if clock[0] >= 60:
            raise expired

    def run(argv, *, input=None, timeout, env=None):
        checkpoint()
        limits.append(min(timeout, 60 - clock[0]))
        clock[0] += 9 if argv[0] == "cc-present" else min(36, 60 - clock[0])
        checkpoint()
        return response(argv)

    selected = environment(tmp_path, tmp_path / "state", "sonnet")
    evidence = judge.owner_words("rule/x", judge.said(owner(tmp_path / "state"), tmp_path / "state", selected, run=run, checkpoint=checkpoint))
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        judge.judge("mechanism", evidence, "artifact", backend="sonnet", drive="brook-drive", env=selected, run=run, checkpoint=checkpoint)
    assert caught.value is expired and limits == [10, 51, 15]
    assert not (tmp_path / "state" / judge.CALLS).exists()


@pytest.mark.parametrize("leaf", ["board", "claude", "jev"])
def test_abandonment_after_a_positive_response_prevents_cache_or_record_commit(tmp_path: Path, leaf: str):
    flag = reqenv.Cutoff()

    def run(argv, **kwargs):
        flag.close()
        return response(argv)

    with reqenv.abandonable(flag), pytest.raises(reqenv.Abandoned):
        if leaf == "board":
            judge.said(owner(tmp_path), tmp_path, run=run, checkpoint=reqenv.checkpoint)
        else:
            selected = environment(tmp_path, tmp_path / "state", "sonnet" if leaf == "claude" else "jev")
            judge.judge("mechanism", "evidence", "artifact", backend=judge.backend_from(selected), drive="brook-drive", env=selected, run=run, checkpoint=reqenv.checkpoint)
    assert not (tmp_path / judge.CARDS / f"{DIGEST}.txt").exists()
    assert not (tmp_path / "state" / judge.CALLS).exists()


def child_identity(pid: int, executable: Path) -> str:
    row = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "pid=,ppid=,pgid=,tty=,lstart=,command="], capture_output=True, text=True, check=True).stdout.strip()
    fields = row.split(None, 9)
    assert len(fields) == 10 and int(fields[0]) == pid and int(fields[1]) == os.getpid()
    assert int(fields[2]) > 0 and fields[3] and " ".join(fields[4:9])
    assert str(executable) in fields[9]
    return row


@pytest.mark.parametrize("leaf", ["board", "claude", "jev"])
@pytest.mark.parametrize("halt", ["timeout", "abandon"])
def test_owned_card_and_model_commands_are_reaped_on_owner_timeout_or_abandonment(tmp_path: Path, leaf: str, halt: str):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    name = {"board": "cc-present", "claude": "claude", "jev": "uv"}[leaf]
    executable = bin_dir / name
    pid_path = tmp_path / "child.pid"
    executable.write_text(f"#!{sys.executable}\nimport os, pathlib, time\npathlib.Path(os.environ['OWNED_JUDGE_PID']).write_text(str(os.getpid()))\ntime.sleep(60)\n")
    executable.chmod(0o700)
    selected = environment(tmp_path, tmp_path / "state", "sonnet" if leaf != "jev" else "jev") | {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "OWNED_JUDGE_PID": str(pid_path)}
    evt = session_start(tmp_path / "session", "compact")
    flag = reqenv.Cutoff()
    deadline = time.monotonic() + (2 if halt == "timeout" else 10)
    expired = subprocess.TimeoutExpired(["check-pr"], 2)

    def remaining():
        reqenv.checkpoint()
        left = deadline - time.monotonic()
        if left <= 0:
            raise expired
        return left

    def checkpoint():
        remaining()

    def run(argv, *, input=None, timeout, env=None):
        checkpoint()
        result = evt.ctx.call_cli(argv, input=input, cwd=str(tmp_path), env=dict(env), timeout=min(timeout, remaining()))
        checkpoint()
        return result

    def call():
        with reqenv.abandonable(flag):
            if leaf == "board":
                return judge.said(owner(tmp_path / "state"), tmp_path / "state", selected, run=run, checkpoint=checkpoint)
            return judge.judge("mechanism", "evidence", "artifact", backend=judge.backend_from(selected), drive="brook-drive", env=selected, run=run, checkpoint=checkpoint)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(call)
        pid = None
        identity = None
        try:
            until = time.monotonic() + 5
            written = ""
            while not (pid_path.exists() and (written := pid_path.read_text().strip())):
                if pending.done():
                    pending.result()
                assert time.monotonic() < until
                time.sleep(0.01)
            pid = int(written)
            identity = child_identity(pid, executable)
            if halt == "abandon":
                flag.close()
            with pytest.raises(reqenv.Abandoned if halt == "abandon" else subprocess.TimeoutExpired):
                pending.result(timeout=6)
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            with pytest.raises(ChildProcessError):
                os.waitpid(pid, os.WNOHANG)
        finally:
            flag.close()
            if pid is not None:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    assert child_identity(pid, executable) == identity
                    os.kill(pid, signal.SIGKILL)
    assert not (tmp_path / "state" / judge.CALLS).exists()
    assert not (tmp_path / "state" / judge.CARDS / f"{DIGEST}.txt").exists()


def test_explicit_environment_uses_its_home_when_no_drive_registry_entry_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path / "process-home"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    selected = {"HOME": str(tmp_path / "request-home")}
    judge.record("unregistered", "mechanism", "sonnet", "no", {}, env=selected)
    assert (tmp_path / "request-home" / ".claude" / "scratch" / "unregistered" / judge.CALLS).is_file()
    assert not (tmp_path / "process-home" / ".claude").exists()


def test_default_record_lookup_keeps_home_lazy_when_the_registry_names_the_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    selected = environment(tmp_path, tmp_path / "state", "sonnet")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", selected["CLAUDE_CONFIG_DIR"])

    def unavailable():
        raise RuntimeError("home is unavailable")

    monkeypatch.setattr(Path, "home", unavailable)
    judge.record("brook-drive", "mechanism", "sonnet", "no", {})
    assert logged(tmp_path / "state")[0]["verdict"] == "no"
