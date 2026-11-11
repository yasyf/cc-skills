import io
import json
import subprocess
import threading

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


def test_a_call_runs_the_newest_drive_and_its_refusal_comes_back_as_the_tools_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp, "LISTED", {})
    ran: list[tuple[list[str], str]] = []
    newest = "/plugins/long-running/9.9.9/bin/drive"
    monkeypatch.setattr(mcp, "newest_drive", lambda: newest)
    refused = subprocess.CompletedProcess([], 2, "", "drive: {card:x} names no card of the drive's\n")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: ran.append((argv, kwargs["input"])) or refused)
    called = json.loads(answered({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "say", "arguments": {"text": "{card:x}"}}}))
    assert called["result"] == {"content": [{"type": "text", "text": "drive: {card:x} names no card of the drive's"}], "isError": True}
    assert ran == [([newest, "tool", "say"], '{"text": "{card:x}"}')]
