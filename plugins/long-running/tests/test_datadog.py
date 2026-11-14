import io
import json
import threading
from collections.abc import Callable, Iterator
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from drive import agents, cli, datadog, reads, state

API_KEY = "a" * 32
APP_KEY = "b" * 40
TOOLS = [
    {"name": "execute_code", "inputSchema": {"type": "object", "required": ["code"]}, "annotations": {"title": "Execute Code", "readOnlyHint": True}},
    {"name": "create_datadog_notebook", "inputSchema": {"type": "object"}, "annotations": {"title": "Create Notebook", "readOnlyHint": False}},
    {"name": "search_datadog_logs", "description": "Search logs.", "inputSchema": {"type": "object", "required": ["query"]}, "annotations": {"title": "Search Logs", "readOnlyHint": True}},
]


class Datadog(BaseHTTPRequestHandler):
    """The Datadog MCP over streamable HTTP: initialize opens session s1, tools/list answers in two pages, and tools/call streams a progress notification before its answer."""

    seen: list[tuple[Message, dict]] = []
    answer: dict = {}

    def do_POST(self) -> None:
        message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Datadog.seen.append((self.headers, message))
        if "id" not in message:
            self.send_response(202)
            self.end_headers()
            return
        if message["method"] == "tools/call":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            progress = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}}
            self.wfile.write(f"event: message\ndata: {json.dumps(progress)}\n\nevent: message\ndata: {json.dumps(Datadog.answer | {'id': message['id']})}\n\n".encode())
            return
        if message["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "datadog", "version": "1"}}
        else:
            result = {"tools": TOOLS[2:], "nextCursor": None} if message["params"].get("cursor") else {"tools": TOOLS[:2], "nextCursor": "page-2"}
        body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if message["method"] == "initialize":
            self.send_header("Mcp-Session-Id", "s1")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def mcp(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[Message, dict]]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Datadog)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    plugin = {
        "type": "http",
        "url": f"http://${{DD_MCP_DOMAIN:-127.0.0.1:{server.server_port}}}/v1/mcp?referrer_ide=claude-code-plugin",
        "headers": {"DD_API_KEY": "${DD_API_KEY:-}", "DD_APPLICATION_KEY": "${DD_APPLICATION_KEY:-}", "X-Datadog-MCP-Toolsets": "${DD_MCP_TOOLSETS:-}"},
    }
    monkeypatch.setattr(agents, "datadog_servers", lambda: {"mcpServers": {"plugin:datadog:mcp": plugin}})
    monkeypatch.delenv("DD_MCP_DOMAIN", raising=False)
    monkeypatch.setenv("DD_API_KEY", "the session's own, never sent")
    parameters = [{"Name": "/ci/ai-oncall/datadog-app-key", "Value": APP_KEY}, {"Name": "/ci/ai-oncall/datadog-api-key", "Value": API_KEY}]
    monkeypatch.setattr(reads, "run", lambda argv, checkout, **_: json.dumps({"Parameters": parameters}) if argv == datadog.KEYS_READ else pytest.fail(f"ran {argv}"))
    Datadog.seen = []
    Datadog.answer = {"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": "monitor 337919227 is OK"}], "isError": False}}
    yield Datadog.seen
    server.shutdown()


def calls(seen: list[tuple[Message, dict]]) -> list[str]:
    return [message["method"] for _, message in seen]


def test_a_datadog_read_runs_the_tool_on_one_session_keyed_by_ai_oncalls_keys_and_prints_its_answer_past_the_streams_notifications(
    mcp: list[tuple[Message, dict]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = {"code": "return 1", "telemetry": {"intent": "monitor state for the PROVE line"}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(arguments)))
    assert run("datadog", "execute_code", "-") == (0, "monitor 337919227 is OK")
    assert calls(mcp) == ["initialize", "notifications/initialized", "tools/list", "tools/list", "tools/call"]
    assert all((headers["DD_API_KEY"], headers["DD_APPLICATION_KEY"]) == (API_KEY, APP_KEY) for headers, _ in mcp)
    assert all((headers["Mcp-Session-Id"], headers["MCP-Protocol-Version"]) == ("s1", "2025-06-18") for headers, _ in mcp[1:])
    assert mcp[-1][1]["params"] == {"name": "execute_code", "arguments": arguments}


def test_every_request_asks_for_the_core_and_dbm_toolsets_over_the_sessions_own(mcp: list[tuple[Message, dict]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_MCP_TOOLSETS", "core")
    assert run("datadog", "tools")[0] == 0
    assert [headers["X-Datadog-MCP-Toolsets"] for headers, _ in mcp] == ["core,dbm"] * len(mcp)


def test_a_tool_the_datadog_mcp_marks_as_writing_is_refused_and_never_called(
    mcp: list[tuple[Message, dict]], state_root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert cli.main(["datadog", "create_datadog_notebook", "-"]) == 2
    assert capsys.readouterr().err.startswith("drive: datadog create_datadog_notebook: the Datadog MCP marks create_datadog_notebook as writing")
    assert "tools/call" not in calls(mcp)


def test_an_answer_the_tool_marks_as_an_error_prints_and_exits_1(mcp: list[tuple[Message, dict]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    Datadog.answer = {"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": "query parse error"}], "isError": True}}
    monkeypatch.setattr("sys.stdin", io.StringIO('{"query": "service:("}'))
    assert run("datadog", "search_datadog_logs", "-") == (1, "query parse error")


def test_tools_lists_every_read_only_tool_across_the_listings_pages_and_names_the_writers_it_refuses(mcp: list[tuple[Message, dict]], run: Callable[..., tuple[int, str]]) -> None:
    code, said = run("datadog", "tools")
    assert code == 0
    assert said.splitlines()[:2] == ["execute_code: Execute Code", "search_datadog_logs: Search Logs"]
    assert said.endswith("Refused, since the Datadog MCP marks them as writing: create_datadog_notebook.")


def test_a_tool_named_with_no_stdin_prints_its_description_and_input_schema_and_calls_nothing(mcp: list[tuple[Message, dict]], run: Callable[..., tuple[int, str]]) -> None:
    code, said = run("datadog", "search_datadog_logs")
    assert code == 0
    assert said.startswith("search_datadog_logs: Search Logs\n\nSearch logs.\n\nInput schema:\n")
    assert '"required": [\n  "query"\n ]' in said
    assert "tools/call" not in calls(mcp)


def test_a_sprite_lane_runs_its_datadog_read_through_the_drive_that_asks_this_mac(
    mcp: list[tuple[Message, dict]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(reads.SPRITE_LANE, "1")
    monkeypatch.setenv(state.OWN_JOB_ENV, "j7")
    monkeypatch.setenv(state.DRIVE_ENV, "demo")
    monkeypatch.setattr("sys.stdin", io.StringIO('{"code": "return 1", "telemetry": {"intent": "x"}}'))
    assert run("datadog", "execute_code", "-") == (0, "monitor 337919227 is OK")


def test_ssm_missing_a_key_is_refused_naming_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reads, "run", lambda argv, checkout, **_: json.dumps({"Parameters": [{"Name": "/ci/ai-oncall/datadog-api-key", "Value": API_KEY}]}))
    with pytest.raises(reads.ReadError, match="SSM holds no /ci/ai-oncall/datadog-app-key the core-auto profile reads"):
        datadog.keys()
