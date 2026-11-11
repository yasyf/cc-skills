import json
import subprocess
import threading
from pathlib import Path
from typing import TextIO

from drive.install import config_dir, installed

PLUGIN = "long-running@skills"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
CALL_SECONDS = 180
INSTRUCTIONS = (
    "Every message to him goes through say: one call posts it to his Slack DM and shows it on the drive's dashboard."
    " Name a card as {card:<waits id>} and a PR as {pr:<number or owner/repo#number>} in its text, and the tool links"
    " each the way each surface shows it."
)
TEXT = {"type": "string"}
VISUAL = {
    "type": "object",
    "description": "One visual the post carries: drawn as a picture for Slack, drawn live from its source on the dashboard",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["code", "arch", "mermaid", "chart", "compare", "dot", "image"],
            "description": "Code highlighted, an arch map, a Mermaid diagram, a chart, a before-and-after compare, Graphviz dot, or an image",
        },
        "source": {"type": "string", "description": "The code or the diagram's source, as in a Show me fence, or an image's absolute path or URL"},
        "language": {"type": "string", "description": "The code's language, for its highlighting"},
        "caption": {"type": "string", "description": "One line naming what it shows"},
    },
    "required": ["kind", "source"],
}
TOOLS = [
    {
        "name": "say",
        "description": (
            "Post one message to his Slack DM and show it on the drive's dashboard, in one call: a line, an answer, a PR"
            " card through tap_prs or a question card through card, with any visuals. In text, {card:<waits id>} and"
            " {pr:<number, owner/repo#number or URL>} become a link under the words that name the card or PR, which"
            " Slack draws the card under, and a link on the dashboard; a reference that names nothing of the drive's"
            " refuses the post. Prints POSTED <permalink>, plus CARD <permalink> for a card; with order it also records"
            " the post on that order."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The post's exact words, with any {card:…} and {pr:…} references; none for a question card"},
                "thread_ts": {"type": "string", "description": "His thread the post goes in; none for his DM root"},
                "asked": {
                    "anyOf": [TEXT, {"type": "array", "items": TEXT}],
                    "description": "Each message of his this post answers, word for word, one per entry, so the answer goes out whole",
                },
                "card": {"type": "object", "description": "A question card exactly as drive job card printed it"},
                "tap_prs": {"type": "array", "items": TEXT, "description": "Each PR of a stack waiting on his tap, bottom first"},
                "about": {"type": "string", "description": "The waits id of the card this reply is about"},
                "visuals": {"type": "array", "items": VISUAL},
                "order": {"type": "string", "description": "The order whose thread this post is in, which records the post on it"},
                "part": {"type": "string", "description": "The part of that order this post delivers"},
            },
        },
    },
]


def serve(stdin: TextIO, stdout: TextIO) -> int:
    """Answer MCP requests read from stdin, each in its own thread, so a slow post never holds the next call."""
    writing = threading.Lock()
    for line in stdin:
        if line.strip():
            threading.Thread(target=respond, args=(json.loads(line), stdout, writing)).start()
    return 0


def respond(request: dict, stdout: TextIO, writing: threading.Lock) -> None:
    if "id" not in request:
        return
    reply = {"jsonrpc": "2.0", "id": request["id"]}
    match request["method"]:
        case "initialize":
            reply["result"] = {
                "protocolVersion": request["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "drive", "version": json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]},
                "instructions": INSTRUCTIONS,
            }
        case "tools/list":
            reply["result"] = {"tools": TOOLS}
        case "tools/call":
            reply["result"] = call(request["params"]["name"], request["params"].get("arguments") or {})
        case "ping":
            reply["result"] = {}
        case method:
            reply["error"] = {"code": -32601, "message": f"no method {method}"}
    with writing:
        stdout.write(json.dumps(reply) + "\n")
        stdout.flush()


def call(tool: str, arguments: dict) -> dict:
    """Run the tool in the newest installed drive, so a live session's call runs the plugin installed now, as its hooks do."""
    try:
        done = subprocess.run([newest_drive(), "tool", tool], input=json.dumps(arguments), capture_output=True, text=True, check=False, timeout=CALL_SECONDS)
    except subprocess.TimeoutExpired:
        return {"content": [{"type": "text", "text": f"drive tool {tool} did not finish in {CALL_SECONDS} seconds"}], "isError": True}
    said = done.stderr if done.returncode else done.stdout
    return {"content": [{"type": "text", "text": said.strip()}], "isError": bool(done.returncode)}


def newest_drive() -> str:
    root = PLUGIN_ROOT
    if root.is_relative_to((config_dir() / "plugins" / "cache").resolve()):
        root = Path(installed(PLUGIN)["installPath"])
    return str(root / "bin" / "drive")
