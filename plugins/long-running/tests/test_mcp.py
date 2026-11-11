import io
import json
import subprocess
import threading

import pytest
from drive import mcp


def answered(request: dict) -> str:
    out = io.StringIO()
    mcp.respond(request, out, threading.Lock())
    return out.getvalue()


def test_a_session_starting_the_server_gets_the_say_tool() -> None:
    started = json.loads(answered({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}))
    assert (started["id"], started["result"]["protocolVersion"], started["result"]["serverInfo"]["name"]) == (1, "2025-06-18", "drive")
    listed = json.loads(answered({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
    assert [tool["name"] for tool in listed["result"]["tools"]] == ["say"]


def test_an_unknown_method_is_an_error_and_a_notification_gets_no_reply() -> None:
    assert json.loads(answered({"jsonrpc": "2.0", "id": 3, "method": "resources/list"}))["error"]["code"] == -32601
    assert answered({"jsonrpc": "2.0", "method": "notifications/initialized"}) == ""


def test_a_call_runs_the_newest_drive_and_its_refusal_comes_back_as_the_tools_error(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[tuple[list[str], str]] = []
    newest = "/plugins/long-running/9.9.9/bin/drive"
    monkeypatch.setattr(mcp, "newest_drive", lambda: newest)
    refused = subprocess.CompletedProcess([], 2, "", "drive: {card:x} names no card of the drive's\n")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: ran.append((argv, kwargs["input"])) or refused)
    called = json.loads(answered({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "say", "arguments": {"text": "{card:x}"}}}))
    assert called["result"] == {"content": [{"type": "text", "text": "drive: {card:x} names no card of the drive's"}], "isError": True}
    assert ran == [([newest, "tool", "say"], '{"text": "{card:x}"}')]
