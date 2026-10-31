from __future__ import annotations

import ast
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from captain_hook.events import PreToolUseEvent
from captain_hook.testing.helpers import build_context
from captain_hook.util import reqenv
from long_running import ghread

from hooks import compaction_handoff as handoff, lane_landing
from test_check_pr import GRANT_TICKER, HEAD, TIMER_ANSWER_ROW, CheckShell, citing, pinned
from test_judge_request_owned import PROOF, SHAPE, child_identity, environment, logged, owner, response
from test_rulings import DIFF, REPO, home, package, registered, state
from test_rulings_owned_hooks import CALLERS, HOOKS


class RequestCheckShell(CheckShell):
    def __init__(self, request_cwd: Path, rows: dict[str, dict[str, str]], **kwargs) -> None:
        self.request_cwd = str(request_cwd)
        super().__init__(rows, **kwargs)

    def run(self, argv: list[str], stdin: str | None = None, cwd: Path | None = None) -> str:
        normalized = list(argv)
        if normalized[:2] in (["ccn", "-R"], ["git", "-C"]) and normalized[2] == self.request_cwd:
            normalized[2] = REPO
        return super().run(normalized, stdin, cwd)



def test_request_metadata_wrapper_preserves_gateway_authority(tmp_path: Path):
    shell = RequestCheckShell(tmp_path, {}, body="selected body")
    command = ghread.read("pr", "view", "12", "--repo", "acme/app", "--json", "body,baseRefName,comments", max_age=ghread.FRESH)
    original = list(command)
    assert json.loads(shell.run(command))["body"] == "selected body"
    assert command == original
    with pytest.raises(AssertionError, match="read outside"):
        shell.run(["gh", "pr", "view", "12", "--repo", "acme/app", "--json", "body,baseRefName,comments"])


def request(tmp_path: Path, package: Path, branch: str, backend: str, monkeypatch: pytest.MonkeyPatch):
    request_cwd = tmp_path / "checkout"
    request_cwd.mkdir()
    rows = pinned(package)
    key = "rule/bbbbbbb" if branch == "shape" else "rule/abcabca"
    if branch == "wait":
        rows[key] = dict(TIMER_ANSWER_ROW)
    else:
        rows[key]["words"] = "One pool process drains every run when it closes"
    rows[key]["proof"] = PROOF
    kept = tmp_path / "request-state"
    owner(kept)
    selected = environment(tmp_path / "request-home", kept, backend) | {"CLAUDE_LONG_RUNNING_DRIVE": "brook-drive", "PWD": str(request_cwd)}
    selected.pop("CLAUDE_LONG_RUNNING_LANE", None)
    registry = Path(selected["CLAUDE_CONFIG_DIR"]) / "long-running" / "drives" / "brook-drive.json"
    registry.write_text(json.dumps(json.loads(registry.read_text()) | {"repo": "acme/app", "checkout": str(request_cwd)}))
    shell = RequestCheckShell(request_cwd, rows, diff=SHAPE if branch == "shape" else DIFF + GRANT_TICKER, body=citing(f"- {key}: the pull request carries this ruling."))
    context = build_context(session_dir=tmp_path / "session")
    event = PreToolUseEvent(_raw={"session_id": "s-root", "cwd": str(request_cwd), "tool_name": "Bash", "tool_input": {"command": f"gh pr merge 12 -R acme/app --squash --match-head-commit {HEAD}"}}, ctx=context)
    module = handoff.rulings_module(handoff.script("long_running/__init__.py"))

    def unowned(*args, **kwargs):
        raise AssertionError("canonical judge escaped to its default CLI transport")

    monkeypatch.setattr(module.judge, "command", unowned)
    return event, shell, selected, kept


def overrides(selected):
    return reqenv.RequestOverrides(env=selected, cwd=selected["PWD"], client_ppid=os.getpid(), session_id="s-root")


def receipt(argv, cwd, env, timeout, selected, budget):
    assert cwd == selected["PWD"] and Path(cwd).is_dir() and 0 < timeout <= budget
    for name in ("HOME", "CLAUDE_CONFIG_DIR", "LONG_RUNNING_JUDGE", "CLAUDE_LONG_RUNNING_DRIVE"):
        assert env[name] == selected[name]
    parent, deadline = env[handoff.DEADLINE_ENV].split(":")
    assert parent == str(os.getpid()) and time.time() < float(deadline) <= time.time() + budget + 1
    assert argv[0] != sys.executable
    if argv[0] == "cc-present":
        assert argv[:3] == ["cc-present", "block", "show"] and timeout <= 10


def test_current_source_has_all_fifteen_external_owned_capability_expressions():
    calls = []
    for module in (*CALLERS, "lane_landing"):
        for node in ast.walk(ast.parse((HOOKS / f"{module}.py").read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"rulings", "register_of"}:
                event = next(keyword.value for keyword in node.keywords if keyword.arg == "evt")
                expected = "reads.evt" if module == "board_text_gate" else "self.evt" if module == "owner_facing" else "evt"
                assert ast.unparse(event) == expected
                calls.append(module)
    assert len(calls) == 15 and calls.count("lane_landing") == 1
    assert "refusal_kinds_need_owner_answer" in calls and "narrowed_contracts" not in calls


@pytest.mark.parametrize("branch", ["shape", "wait"])
@pytest.mark.parametrize("backend", ["sonnet", "jev+sonnet"])
def test_real_lane_hook_dispatches_both_canonical_check_pr_judgments_through_its_owner_with_cold_and_warm_evidence(home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch: str, backend: str):
    event, shell, selected, kept = request(tmp_path, package, branch, backend, monkeypatch)
    monkeypatch.setenv("LONG_RUNNING_JUDGE", "jev" if backend == "sonnet" else "sonnet")
    calls = []
    context = event.ctx

    def call_cli(self, argv, *, input=None, cwd=None, env=None, timeout=None):
        assert self is context
        receipt(argv, cwd, env, timeout, selected, 60)
        calls.append((list(argv), input, timeout))
        if argv[0] in {"cc-present", "claude", "uv"}:
            return response(argv, f"owned {branch} card")
        return shell.run(argv, input, Path(cwd))

    monkeypatch.setattr(type(context), "call_cli", call_cli)
    before = dict(os.environ), os.getcwd(), list(sys.path), sys.stdin, sys.stdout, sys.stderr
    with reqenv.use_request(overrides(selected)):
        first = lane_landing.rulings_at_merge(event)
        count = len([argv for argv, _, _ in calls if argv[0] in {"claude", "uv"}])
        second = lane_landing.rulings_at_merge(event)
    assert first is not None and first.action.value == "warn" and second.message == first.message
    assert ("SHAPE-UNTESTED" if branch == "shape" else "MECHANISM-BANNED timer") in first.message
    assert any(f"owned {branch} card" in (stdin or "") for argv, stdin, _ in calls if argv[0] in {"claude", "uv"})
    assert len([argv for argv, _, _ in calls if argv[0] == "cc-present"]) == 1
    assert len([argv for argv, _, _ in calls if argv[0] in {"claude", "uv"}]) == count * (1 if branch == "shape" else 2)
    assert {row["backend"] for row in logged(kept)} == {backend}
    assert not (state / "judge" / "calls.jsonl").exists()
    assert (dict(os.environ), os.getcwd(), list(sys.path), sys.stdin, sys.stdout, sys.stderr) == before


@pytest.mark.parametrize("branch", ["shape", "wait"])
@pytest.mark.parametrize("leaf", ["board", "claude", "jev"])
@pytest.mark.parametrize("halt", ["timeout", "abandon"])
@pytest.mark.parametrize("entrypoint", ["lane", "generic"])
def test_real_owned_check_pr_chain_reaps_actual_card_and_model_children_and_preserves_owner_unwinds(home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch: str, leaf: str, halt: str, entrypoint: str):
    event, shell, selected, kept = request(tmp_path, package, branch, "jev" if leaf == "jev" else "sonnet", monkeypatch)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    name = {"board": "cc-present", "claude": "claude", "jev": "uv"}[leaf]
    executable = bin_dir / name
    pid_path = tmp_path / "child.pid"
    executable.write_text(f"#!{sys.executable}\nimport os, pathlib, time\npathlib.Path(os.environ['OWNED_JUDGE_PID']).write_text(str(os.getpid()))\ntime.sleep(60)\n")
    executable.chmod(0o700)
    selected |= {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "OWNED_JUDGE_PID": str(pid_path)}
    monkeypatch.setattr(lane_landing, "CHECK_PR_TIMEOUT_SECONDS", 2)
    context = event.ctx
    owned_cli = type(context).call_cli
    failures = []
    calls = []

    def call_cli(self, argv, *, input=None, cwd=None, env=None, timeout=None):
        assert self is context
        receipt(argv, cwd, env, timeout, selected, 2)
        calls.append(list(argv))
        if argv[0] == name:
            try:
                return owned_cli(self, argv, input=input, cwd=cwd, env=env, timeout=timeout)
            except (subprocess.TimeoutExpired, reqenv.Abandoned) as failure:
                failures.append(failure)
                raise
        if argv[0] in {"cc-present", "claude", "uv"}:
            return response(argv)
        return shell.run(argv, input, Path(cwd))

    monkeypatch.setattr(type(context), "call_cli", call_cli)
    flag = reqenv.Cutoff()

    def invoke():
        with reqenv.use_request(overrides(selected)), reqenv.abandonable(flag):
            if entrypoint == "lane":
                return lane_landing.rulings_at_merge(event)
            return handoff.rulings(selected["PWD"], "check-pr", "--drive", "brook-drive", "--pr", "acme/app#12", "--head", HEAD, evt=event, timeout=2)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(invoke)
        pid = None
        identity = None
        try:
            until = time.monotonic() + 5
            written = ""
            while not (pid_path.exists() and (written := pid_path.read_text().strip())):
                if pending.done():
                    failure = pending.exception()
                    records = logged(kept) if (kept / "judge" / "calls.jsonl").is_file() else []
                    diagnostic = {"returned": pending.result() if failure is None else repr(failure), "records": records, "commands": calls}
                    if isinstance(failure, subprocess.CalledProcessError):
                        diagnostic |= {"stdout": failure.stdout, "stderr": failure.stderr}
                    pytest.fail(repr(diagnostic))
                assert time.monotonic() < until
                time.sleep(0.01)
            pid = int(written)
            identity = child_identity(pid, executable)
            if halt == "abandon":
                flag.close()
            if halt == "timeout" and entrypoint == "lane":
                assert pending.result(timeout=6) is None
            else:
                with pytest.raises(reqenv.Abandoned if halt == "abandon" else subprocess.TimeoutExpired) as caught:
                    pending.result(timeout=6)
                if halt == "abandon":
                    assert caught.value is failures[0]
            assert failures
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
    assert len([argv for argv in calls if argv[0] == name]) == 1
    assert not (kept / "judge" / "calls.jsonl").exists()
    assert not (kept / "check-pr" / "judged").exists()
    if leaf == "board":
        assert not (kept / "judge" / "cards").exists()
