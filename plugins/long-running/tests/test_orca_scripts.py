from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/long-running/scripts"
BIN = Path(__file__).resolve().parents[1] / "bin"

ORCA = """#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_STATE"]
with open(os.path.join(state, "calls"), "a") as calls:
    calls.write(json.dumps(sys.argv[1:]) + "\\n")
queue = os.path.join(state, "_".join(sys.argv[1:3]))
replies = json.load(open(queue)) if os.path.exists(queue) else [{"rc": 1, "out": ""}]
reply = replies.pop(0) if len(replies) > 1 else replies[0]
json.dump(replies, open(queue, "w"))
sys.stdout.write(reply["out"] if isinstance(reply["out"], str) else json.dumps(reply["out"]))
sys.exit(reply["rc"])
"""

SLEEP = """#!/bin/sh
echo "$1" >> "$FAKE_STATE/sleeps"
"""


class Orca:
    def __init__(self, root: Path):
        self.state = root / "state"
        self.state.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for tool, body in {"orca": ORCA, "sleep": SLEEP}.items():
            (bin_dir / tool).write_text(body)
            (bin_dir / tool).chmod(0o755)
        self.receipts = root / "receipts"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_STATE": str(self.state),
            "ORCA_CHECK_STATE": str(self.receipts),
            "ORCA_TERMINAL_HANDLE": "term_root",
            "HOME": str(root / "home"),
        }
        self.env.pop("XDG_STATE_HOME", None)

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


@pytest.fixture
def orca() -> Iterator[Orca]:
    with tempfile.TemporaryDirectory(dir="/tmp") as root:
        yield Orca(Path(root))


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def message(msg_id: str, kind: str, sender: str, subject: str, body: str) -> dict:
    return {"id": msg_id, "type": kind, "from_handle": sender, "subject": subject, "body": body}


def test_check_prints_each_non_heartbeat_message_and_the_delivery(orca):
    orca.receipts.mkdir(exist_ok=True)
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
    result = orca.run("orca-check.sh", "--", "--terminal", "term_root", "--ack", "delivery_1")
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


@pytest.mark.parametrize("args", [("--json",), ("--ack", "delivery_1")])
def test_check_rejects_removed_runner_modes(orca, args):
    result = orca.run("orca-check.sh", *args)
    assert result.returncode == 2
    assert result.stderr.startswith("usage: orca-check.sh [--peek] [-- <orca check args>...]")
    assert orca.calls() == []


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
    assert check[2:] == ["--peek", "--terminal", "term_me", "--full", "--json"]


def test_the_plugin_bin_launches_every_script_by_name():
    launchers = [path for path in BIN.iterdir() if path.suffix != ".binrun"]
    shimmed = set(re.findall(r'^\s*(\S+)\) exec "\$ROOT/bin/lr" ', (BIN.parent / "scripts" / "bin-shim.sh").read_text(), re.MULTILINE))
    assert sorted(path.name for path in launchers if path.name not in shimmed) == sorted(["lr", "cc-remote", *(path.name for path in SCRIPTS.iterdir() if path.name not in shimmed and (path.suffix in (".py", ".sh") or path.name.startswith("cci-")) and (path.read_text().startswith("#!") or "__main__" in path.read_text()))])
    for launcher in launchers:
        assert os.access(launcher, os.X_OK)
        assert os.readlink(launcher) == "../scripts/bin-shim.sh"


@pytest.mark.parametrize("script", ["ledger.py", "drive.py"])
def test_a_cli_runs_by_name_from_any_directory_with_the_plugin_bin_on_path(script, tmp_path):
    env = {**os.environ, "PATH": f"{BIN}:{os.environ['PATH']}"}
    result = subprocess.run([script, "--help"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"usage: {script}")
