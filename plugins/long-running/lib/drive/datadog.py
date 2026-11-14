import itertools
import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from drive import agents, reads

KEYS = {"DD_API_KEY": "/ci/ai-oncall/datadog-api-key", "DD_APPLICATION_KEY": "/ci/ai-oncall/datadog-app-key"}
KEYS_READ = ["aws", "ssm", "get-parameters", "--profile", "core-auto", "--region", "us-west-2", "--with-decryption", "--output", "json", "--names", *KEYS.values()]
PLACEHOLDER = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")
PROTOCOL = "2025-06-18"
SECONDS = 240
LISTING = "tools"
TOOLSETS = {"DD_MCP_TOOLSETS": "core,dbm"}


def run(tool: str, arguments: dict | None) -> tuple[int, str]:
    """What one read-only tool of the Datadog MCP answers the arguments, exiting 1 when its answer says it failed; with no arguments its description and input schema; `tools` a line for each tool it runs and the writing tools it refuses. A tool runs only when the Datadog MCP's own listing marks it read-only, as it marks `execute_code`, since ai-oncall's keys reach writes too."""
    send = connect()
    tools = listed(send)
    if tool == LISTING:
        return 0, listing(tools)
    found = next((each for each in tools if each["name"] == tool), None)
    if found is None:
        raise reads.ReadError(f"the Datadog MCP lists no tool {tool}; drive datadog tools lists those it runs")
    if not read_only(found):
        raise reads.ReadError(f"the Datadog MCP marks {tool} as writing, and drive datadog runs only the tools it marks read-only")
    if arguments is None:
        return 0, described(found)
    answer = send("tools/call", {"name": tool, "arguments": arguments})
    return int(bool(answer.get("isError"))), "\n".join(item["text"] if item["type"] == "text" else json.dumps(item) for item in answer["content"])


def connect() -> Callable[..., dict]:
    """A request function on a fresh session of the installed Datadog plugin's MCP server, the one agents.datadog_servers gives a lane on this Mac, its `${VAR:-default}` placeholders filled from this process's environment over ai-oncall's Datadog keys and the `core` and `dbm` toolsets, so each call runs as ai-oncall's service account, the keys stay in this process's memory, and the server lists Database Monitoring's query sample, plan and performance tools beside its default ones, which no `execute_code` API reaches."""
    server = next(iter(agents.datadog_servers()["mcpServers"].values()))
    env = os.environ | keys() | TOOLSETS
    url = filled(server["url"], env)
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"} | {name: filled(value, env) for name, value in server["headers"].items()}
    numbers = itertools.count(1)

    def send(method: str, params: dict | None = None) -> dict:
        number = None if method.startswith("notifications/") else next(numbers)
        message = {"jsonrpc": "2.0", "method": method} | ({"id": number} if number else {}) | ({"params": params} if params is not None else {})
        try:
            with urllib.request.urlopen(urllib.request.Request(url, json.dumps(message).encode(), headers), timeout=SECONDS) as response:
                if session := response.headers.get("Mcp-Session-Id"):
                    headers["Mcp-Session-Id"] = session
                if number is None:
                    return {}
                said = streamed(response, number) if response.headers.get_content_type() == "text/event-stream" else json.load(response)
        except urllib.error.HTTPError as error:
            raise reads.ReadError(f"the Datadog MCP answered HTTP {error.code} to {method}: {error.read().decode(errors='replace').strip()}") from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise reads.ReadError(f"the Datadog MCP at {url} did not answer {method}: {error}") from None
        if "error" in said:
            raise reads.ReadError(f"the Datadog MCP refused {method}: {said['error']['message']}")
        return said["result"]

    started = send("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "drive", "version": "1"}})
    headers["MCP-Protocol-Version"] = started["protocolVersion"]
    send("notifications/initialized")
    return send


def keys() -> dict[str, str]:
    """ai-oncall's Datadog API and application keys under the variables the Datadog plugin's server headers name, read from SSM on this Mac's core-auto profile, as cc-remote reads the Trunk key, by his pick "Reuse existing keys"."""
    values = {each["Name"]: each["Value"] for each in json.loads(reads.run(KEYS_READ, Path.home()))["Parameters"]}
    if missing := [name for name in KEYS.values() if name not in values]:
        raise reads.ReadError(f"SSM holds no {', '.join(missing)} the core-auto profile reads")
    return {variable: values[name] for variable, name in KEYS.items()}


def filled(text: str, env: Mapping[str, str]) -> str:
    """The text with each `${VAR}` or `${VAR:-default}` Claude Code expands in a plugin's server config filled as it fills it: VAR's value, else the default, else nothing."""
    return PLACEHOLDER.sub(lambda found: env.get(found[1]) or found[2] or "", text)


def streamed(lines: Iterable[bytes], number: int) -> dict:
    """The JSON-RPC answer to request `number` in a text/event-stream response, each event's data lines joined, past the notifications the server sends before it."""
    data: list[str] = []
    for raw in itertools.chain(lines, [b""]):
        line = raw.decode().rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line.removeprefix("data:").removeprefix(" "))
        elif not line and data:
            said = json.loads("\n".join(data))
            data = []
            if said.get("id") == number:
                return said
    raise reads.ReadError(f"the Datadog MCP's event stream ended with no answer to request {number}")


def listed(send: Callable[..., dict]) -> list[dict]:
    tools: list[dict] = []
    cursor = None
    while True:
        page = send("tools/list", {"cursor": cursor} if cursor else {})
        tools += page["tools"]
        if not (cursor := page.get("nextCursor")):
            return tools


def read_only(tool: dict) -> bool:
    return bool(tool.get("annotations", {}).get("readOnlyHint"))


def title(tool: dict) -> str:
    return tool.get("title") or tool.get("annotations", {}).get("title") or ""


def listing(tools: list[dict]) -> str:
    runs = [f"{tool['name']}: {title(tool)}" for tool in tools if read_only(tool)]
    refused = [tool["name"] for tool in tools if not read_only(tool)]
    return "\n".join(
        [
            *runs,
            "",
            "drive datadog <tool> prints a tool's description and input schema, and drive datadog <tool> - calls it with its JSON arguments on stdin.",
            f"Refused, since the Datadog MCP marks them as writing: {', '.join(refused) or 'none'}.",
        ]
    )


def described(tool: dict) -> str:
    return "\n\n".join(
        [
            f"{tool['name']}: {title(tool)}",
            tool.get("description", "").strip(),
            f"Input schema:\n{json.dumps(tool['inputSchema'], indent=1)}",
            f"Call it with drive datadog {tool['name']} -, its JSON arguments on stdin.",
        ]
    )
