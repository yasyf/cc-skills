from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/long-running/scripts"

ORCA = """#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_STATE"]
with open(os.path.join(state, "calls"), "a") as calls:
    calls.write(json.dumps(sys.argv[1:]) + "\\n")
key = "_".join(sys.argv[1:3])
queue = os.path.join(state, key)
replies = json.load(open(queue)) if os.path.exists(queue) else [{"rc": 1, "out": ""}]
reply = replies.pop(0) if len(replies) > 1 else replies[0]
json.dump(replies, open(queue, "w"))
if reply.get("mkdir"):
    os.makedirs(reply["mkdir"], exist_ok=True)
sys.stdout.write(reply["out"] if isinstance(reply["out"], str) else json.dumps(reply["out"]))
sys.exit(reply["rc"])
"""

SLEEP = """#!/bin/sh
echo "$1" >> "$FAKE_STATE/sleeps"
"""


class Orca:
    def __init__(self, root: Path):
        self.root = root
        self.state = root / "state"
        self.state.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for tool, body in {"orca": ORCA, "sleep": SLEEP}.items():
            (bin_dir / tool).write_text(body)
            (bin_dir / tool).chmod(0o755)
        self.receipts = root / "receipts"
        self.parent = root / "workspaces" / "coordinator"
        self.parent.mkdir(parents=True)
        self.brief = root / "specs" / "lane-a.full.md"
        self.brief.parent.mkdir()
        self.brief.write_text("# brief\n")
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_STATE": str(self.state),
            "ORCA_LAUNCH_RUN": "run_1",
            "ORCA_LAUNCH_REPO": "repo-1",
            "ORCA_LAUNCH_PARENT": str(self.parent),
            "ORCA_LAUNCH_PREFIX": "v3-",
            "ORCA_LAUNCH_BASE": "origin/dev",
            "ORCA_LAUNCH_STATE": str(self.receipts),
            "ORCA_LAUNCH_CLAUDE_ARGS": "--channels plugin:cc-review@cc-review",
            "ORCA_CHECK_STATE": str(self.receipts),
        }

    @property
    def worktree(self) -> Path:
        return self.parent.parent / "v3-lane-a-base"

    def reply(self, command: str, *replies: dict) -> None:
        (self.state / command.replace(" ", "_")).write_text(json.dumps(list(replies)))

    def calls(self, command: str | None = None) -> list[list[str]]:
        path = self.state / "calls"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [row for row in rows if command is None or " ".join(row[:2]) == command]

    def sleeps(self) -> list[str]:
        path = self.state / "sleeps"
        return path.read_text().split() if path.exists() else []

    def run(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(SCRIPTS / script), *args], env=self.env, capture_output=True, text=True)

    def healthy(self, state: str = "ready", screen: str = "⏵⏵ bypass permissions on (shift+tab to cycle)") -> None:
        self.reply("worktree create", {"rc": 0, "out": {"ok": True, "result": {"worktree": {"path": str(self.worktree)}}}, "mkdir": str(self.worktree)})
        self.reply("terminal create", {"rc": 0, "out": {"ok": True, "result": {"terminal": {"handle": "term_a"}}}})
        self.reply("orchestration worker-start", {"rc": 0, "out": {"ok": True, "result": {"state": state, "taskId": "task_a", "dispatchId": "ctx_a"}}})
        self.reply("terminal read", {"rc": 0, "out": {"ok": True, "result": {"terminal": {"tail": ["❯", screen]}}}})

    def launch(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.run("orca-launch.sh", *(args or ("lane-a", "opus", "high", str(self.brief))))


@pytest.fixture
def orca() -> Iterator[Orca]:
    with tempfile.TemporaryDirectory(dir="/tmp") as root:
        yield Orca(Path(root))


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_launch_creates_a_child_worktree_and_a_bypass_terminal(orca):
    orca.healthy()
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"lane-a ready task=task_a dispatch=ctx_a terminal=term_a worktree={orca.worktree}"
    [worktree] = orca.calls("worktree create")
    assert flag(worktree, "--name") == "v3-lane-a-base"
    assert flag(worktree, "--parent-worktree") == f"path:{orca.parent}"
    assert flag(worktree, "--base-branch") == "origin/dev"
    [terminal] = orca.calls("terminal create")
    assert flag(terminal, "--command") == (
        "claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions"
        " --disallowedTools AskUserQuestion,EnterPlanMode,ExitPlanMode"
        " --channels plugin:cc-review@cc-review --model claude-opus-5-5 --effort high"
    )
    [start] = orca.calls("orchestration worker-start")
    assert flag(start, "--terminal") == "term_a"
    assert flag(start, "--run") == "run_1"
    assert "--model" not in start and "--effort" not in start
    spec = flag(start, "--spec")
    assert str(orca.brief) in spec and len(spec) <= 300
    assert (orca.receipts / "lane-a.terminal").read_text().strip() == "term_a"


def test_relaunch_retries_the_recorded_dispatch_in_the_existing_worktree(orca):
    orca.healthy()
    orca.worktree.mkdir()
    orca.receipts.mkdir()
    (orca.receipts / "lane-a.json").write_text(json.dumps({"result": {"taskId": "task_old", "dispatchId": "ctx_old"}}))
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    assert orca.calls("worktree create") == []
    [start] = orca.calls("orchestration worker-start")
    assert flag(start, "--task") == "task_old"
    assert flag(start, "--retry-of") == "ctx_old"
    assert "--spec" not in start


def test_launch_retries_a_dropped_worktree_create(orca):
    orca.healthy()
    orca.reply(
        "worktree create",
        {"rc": 1, "out": "connection lost"},
        {"rc": 0, "out": {"ok": True, "result": {"worktree": {"path": str(orca.worktree)}}}, "mkdir": str(orca.worktree)},
    )
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(orca.calls("worktree create")) == 2
    assert orca.sleeps()[0] == "30"


def test_launch_refuses_a_spec_pointer_over_300_characters(orca):
    orca.healthy()
    brief = orca.root / ("x" * 200) / "lane-a.full.md"
    brief.parent.mkdir()
    brief.write_text("# brief\n")
    result = orca.launch("lane-a", "opus", "high", str(brief))
    assert result.returncode == 1
    assert "over 300" in result.stdout
    assert orca.calls() == []


def test_launch_fails_when_the_receipt_is_not_ready(orca):
    orca.healthy(state="failed")
    result = orca.launch()
    assert result.returncode == 1
    assert result.stdout.startswith("lane-a failed worker-start state=failed")


def test_launch_fails_when_the_terminal_is_not_in_bypass_mode(orca):
    orca.healthy(screen="⏸ plan mode on (shift+tab to cycle)")
    result = orca.launch()
    assert result.returncode == 1
    assert "shift-tab" in result.stdout
    assert len(orca.calls("terminal read")) == 5


def test_a_failed_start_keeps_its_dispatch_for_the_relaunch(orca):
    orca.healthy(state="failed")
    assert orca.launch().returncode == 1
    orca.healthy()
    assert orca.launch().returncode == 0
    first, second = orca.calls("orchestration worker-start")
    assert "--spec" in first
    assert flag(second, "--task") == "task_a"
    assert flag(second, "--retry-of") == "ctx_a"


def test_relaunch_reuses_the_worktree_orca_created(orca):
    elsewhere = orca.root / "elsewhere" / "lane-a"
    orca.healthy()
    orca.reply("worktree create", {"rc": 0, "out": {"ok": True, "result": {"worktree": {"path": str(elsewhere)}}}, "mkdir": str(elsewhere)})
    first = orca.launch()
    assert first.stdout.strip().endswith(f"worktree={elsewhere}")
    assert f"Worktree {elsewhere}," in flag(orca.calls("orchestration worker-start")[0], "--spec")
    orca.healthy()
    assert orca.launch().returncode == 0
    assert len(orca.calls("worktree create")) == 1
    assert flag(orca.calls("terminal create")[1], "--worktree") == f"path:{elsewhere}"


def test_launch_points_at_the_absolute_brief(orca):
    orca.healthy()
    result = subprocess.run(
        [str(SCRIPTS / "orca-launch.sh"), "lane-a", "opus", "high", "specs/lane-a.full.md"],
        env=orca.env, capture_output=True, text=True, cwd=orca.root,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"read {orca.brief.resolve()} in full" in flag(orca.calls("orchestration worker-start")[0], "--spec")


def test_launch_rejects_an_unknown_effort(orca):
    result = orca.launch("lane-a", "opus", "extreme", str(orca.brief))
    assert result.returncode == 2
    assert orca.calls() == []


def message(msg_id: str, kind: str, sender: str, subject: str, body: str) -> dict:
    return {"id": msg_id, "type": kind, "from_handle": sender, "subject": subject, "body": body}


def test_check_prints_each_non_heartbeat_message_and_the_delivery(orca):
    orca.receipts.mkdir()
    (orca.receipts / "lane-a.terminal").write_text("term_a\n")
    orca.reply(
        "orchestration check",
        {
            "rc": 0,
            "out": {
                "ok": True,
                "result": {
                    "runId": "run_1",
                    "deliveryId": "delivery_2",
                    "messages": [
                        message("msg_1", "heartbeat", "term_a", "alive", ""),
                        message("msg_2", "worker_done", "term_a", "done", "Opened #12.\nGreen."),
                        message("msg_3", "question", "term_b", "scope", "A or B?"),
                    ],
                    "timedOut": False,
                    "connectionLost": False,
                },
            },
        },
    )
    result = orca.run("orca-check.sh", "--ack", "delivery_1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "msg_2 worker_done lane-a done: Opened #12. Green.",
        "msg_3 question term_b scope: A or B?",
        "delivery delivery_2 heartbeats=1",
    ]
    [check] = orca.calls("orchestration check")
    assert flag(check, "--ack") == "delivery_1"
    assert flag(check, "--types") == "worker_done,escalation,question"
    assert flag(check, "--timeout-ms") == "60000"
    assert "--wait" in check


def test_check_prints_timeout_for_an_empty_wait(orca):
    orca.reply("orchestration check", {"rc": 0, "out": {"ok": True, "result": {"runId": "run_1", "messages": [], "timedOut": True}}})
    result = orca.run("orca-check.sh")
    assert result.stdout.strip() == "timeout"


def test_check_retries_a_lost_connection_once(orca):
    orca.reply(
        "orchestration check",
        {"rc": 0, "out": {"ok": True, "result": {"runId": "run_1", "messages": [], "connectionLost": True}}},
        {"rc": 0, "out": {"ok": True, "result": {"runId": "run_1", "messages": [], "timedOut": True}}},
    )
    result = orca.run("orca-check.sh")
    assert result.stdout.strip() == "timeout"
    assert orca.sleeps() == ["30"]


def test_check_retries_an_unavailable_runtime(orca):
    orca.reply(
        "orchestration check",
        {"rc": 1, "out": {"ok": False, "error": {"code": "runtime_unavailable", "message": "socket closed"}}},
        {"rc": 0, "out": {"ok": True, "result": {"runId": "run_1", "messages": [], "timedOut": True}}},
    )
    result = orca.run("orca-check.sh")
    assert result.stdout.strip() == "timeout"
    assert orca.sleeps() == ["30"]


def test_check_returns_to_the_caller_after_one_retry(orca):
    orca.reply("orchestration check", {"rc": 1, "out": ""})
    result = orca.run("orca-check.sh")
    assert result.returncode == 1
    assert result.stdout.strip() == "connection-lost"
    assert len(orca.calls("orchestration check")) == 2


def test_check_reports_an_orca_error_without_retrying(orca):
    orca.reply("orchestration check", {"rc": 1, "out": {"ok": False, "error": {"code": "consumer_fenced", "message": "not bound"}}})
    result = orca.run("orca-check.sh")
    assert result.returncode == 1
    assert result.stdout.strip() == "error consumer_fenced: not bound"
    assert orca.sleeps() == []


def test_check_peek_reads_without_waiting(orca):
    orca.reply("orchestration check", {"rc": 0, "out": {"ok": True, "result": {"runId": "run_1", "messages": [message("msg_9", "dispatch", "term_root", "note", "hi")]}}})
    result = orca.run("orca-check.sh", "--peek", "--", "--terminal", "term_me")
    assert result.stdout.strip() == "msg_9 dispatch term_root note: hi"
    [check] = orca.calls("orchestration check")
    assert check[2:] == ["--peek", "--terminal", "term_me", "--json"]
