import io
import json
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from drive import cli, mcp


def answered(request: dict) -> str:
    out = io.StringIO()
    mcp.respond(request, out, threading.Lock())
    return out.getvalue()


def test_a_session_starting_the_server_gets_say_and_the_drive_verbs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp, "LISTED", {})
    started = json.loads(answered({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}))
    assert (started["id"], started["result"]["protocolVersion"], started["result"]["serverInfo"]["name"]) == (1, "2025-06-18", "drive")
    assert started["result"]["capabilities"] == {"tools": {"listChanged": True}}
    listed = json.loads(answered({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
    assert [tool["name"] for tool in listed["result"]["tools"]] == list(cli.MCP_TOOLS)


def test_the_list_follows_the_newest_install_and_the_first_call_after_an_install_says_it_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    newest = [mcp.OWN_DRIVE]
    newer = "/plugins/long-running/9.9.9/bin/drive"
    ran: list[list[str]] = []
    monkeypatch.setattr(mcp, "LISTED", {})
    monkeypatch.setattr(mcp, "newest_drive", lambda: newest[0])
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: ran.append(argv) or subprocess.CompletedProcess(argv, 0, '[{"name": "say"}]', ""))
    monkeypatch.setattr(mcp, "call", lambda request_id, tool, arguments: {"content": [], "isError": False})
    answered({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "heard", "arguments": {}}}
    assert len(answered(call).splitlines()) == 1
    newest[0] = newer
    assert json.loads(answered(call).splitlines()[1]) == {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
    assert len(answered(call).splitlines()) == 1
    assert json.loads(answered({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}))["result"]["tools"] == [{"name": "say"}]
    assert ran[-1] == [newer, "mcp", "--list"]


def test_an_unknown_method_is_an_error_and_a_notification_gets_no_reply() -> None:
    assert json.loads(answered({"jsonrpc": "2.0", "id": 3, "method": "resources/list"}))["error"]["code"] == -32601
    assert answered({"jsonrpc": "2.0", "method": "notifications/initialized"}) == ""


def newest_drive_runs(body: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    drive = tmp_path / "drive"
    drive.write_text(f"#!{sys.executable}\nimport os, stat, sys, time\n{body}\n")
    drive.chmod(0o755)
    monkeypatch.setattr(mcp, "LISTED", {})
    monkeypatch.setattr(mcp, "newest_drive", lambda: str(drive))


def tool_call(request_id: int, tool: str, arguments: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}


def test_a_call_runs_the_newest_drive_and_its_refusal_comes_back_as_the_tools_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    newest_drive_runs('sys.exit(f"drive: {sys.argv[1:]} read {sys.stdin.read()}")', tmp_path, monkeypatch)
    called = json.loads(answered(tool_call(4, "say", {"text": "{card:x}"})))
    assert called["result"] == {"content": [{"type": "text", "text": "drive: ['tool', 'say'] read {\"text\": \"{card:x}\"}"}], "isError": True}


def test_a_child_that_dies_before_reading_its_arguments_answers_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    newest_drive_runs('sys.exit("Traceback\\n" * 7_000 + f"stdin is a file: {stat.S_ISREG(os.fstat(0).st_mode)}")', tmp_path, monkeypatch)
    called = json.loads(answered(tool_call(5, "job", {"ops": "x" * 4_096})))
    assert called["result"]["isError"]
    assert called["result"]["content"][0]["text"].endswith("Traceback\nstdin is a file: True")


def test_a_call_past_its_time_kills_the_child_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    newest_drive_runs("time.sleep(60)", tmp_path, monkeypatch)
    monkeypatch.setattr(mcp, "CALL_SECONDS", 0.5)
    called = json.loads(answered(tool_call(6, "status", {})))
    assert called["result"] == {"content": [{"type": "text", "text": "drive tool status did not finish in 0.5 seconds"}], "isError": True}


def test_a_cancelled_call_kills_its_child_and_gets_no_reply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    newest_drive_runs("time.sleep(60)", tmp_path, monkeypatch)
    out = io.StringIO()
    calling = threading.Thread(target=mcp.respond, args=(tool_call(7, "order", {}), out, threading.Lock()))
    calling.start()
    started = time.monotonic()
    while 7 not in mcp.CALLS and time.monotonic() - started < 10:
        time.sleep(0.01)
    child = mcp.CALLS[7]
    assert answered({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 7, "reason": "aborted"}}) == ""
    calling.join(10)
    assert (calling.is_alive(), child.returncode, out.getvalue()) == (False, -signal.SIGKILL, "")
