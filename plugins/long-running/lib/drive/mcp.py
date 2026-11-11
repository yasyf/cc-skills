import json
import subprocess
import threading
from pathlib import Path
from typing import TextIO

from drive import tools
from drive.install import config_dir, installed

PLUGIN = "long-running@skills"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
CALL_SECONDS = 180
OWN_DRIVE = str(PLUGIN_ROOT / "bin" / "drive")
LISTED: dict[str, str] = {}
LIST_CHANGED = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
INSTRUCTIONS = (
    "Every message to him goes through say: one call posts it to his Slack DM and shows it on the drive's dashboard."
    " Name a card as {card:<waits id>}, a PR as {pr:<number or owner/repo#number>}, an order as {order:<id>} and the"
    " record that measured a cause or number as {record:<seq>} in its text, and the tool links each the way each surface"
    " shows it, under its title or under the words after a |, as {pr:298|the fix that draws charts}. Its hook finds a line"
    " naming a cause with no {record:} and each `cc-slack check-post` finding, and Jev judges them against his words;"
    " it posts exactly the words the hook let out and adds none."
    " The drive's verbs are tools too: heard, record for the records between the drive's sessions, order, job and pr taking"
    " several ops a call, and status; each takes dry_run."
)
TEXT = {"type": "string"}
VISUAL = {
    "type": "object",
    "description": "One visual: a picture in Slack, or Slack's own chart for a chart, and drawn live from its source on the dashboard",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["code", "arch", "mermaid", "chart", "compare", "dot", "image"],
            "description": "An arch map for arch, a before-and-after for compare, Graphviz for dot",
        },
        "source": {
            "type": "string",
            "description": (
                "What it draws, as in a Show me fence: the code; Mermaid or dot source; a Vega-Lite JSON spec, data inline, for"
                ' a chart; {"options": [{"name", "recommended", "language", "code", "points": ["+ a gain", "- a cost"]}]} for a'
                " compare; an arch/v1 JSON document; an image's absolute path or URL"
            ),
        },
        "language": {"type": "string", "description": "The code's language, for its highlighting"},
        "caption": {"type": "string", "description": "One line naming what it shows: the picture's title in Slack and the line under it on the dashboard"},
    },
    "required": ["kind", "source"],
}
SAY = {
    "name": "say",
    "description": (
        "Post a line, an answer, a PR card (tap_prs) or a question card (card) to his Slack DM, with any visuals, and"
        " show it on the drive's dashboard, in one call. In text, {card:<waits id>}, {pr:<number, owner/repo#number or"
        " URL>} and {order:<id>} link under the words naming the card, PR or order, or the words after a |, as"
        " {pr:298|the fix that draws charts}; Slack draws the card under the link. {record:<seq>} links what that record"
        " links under the first clause of its own words, at most ten, or under the words after a |. Returns POSTED"
        " <permalink>, CARD <permalink> for a card, the question card's own link first, recorded as the card of the"
        " question asked under its waits id, and with order"
        " the line recording the post on it. A refusal posts nothing and names its fix: a reference that names nothing,"
        " or a finding Jev upheld against his words, such as a line naming a cause (because, caused) with no {record:}"
        " or a word `cc-slack check-post` flags: make the fix and call again. The communicator's ack to his message is one sentence: what he asked and what"
        " happens next."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The post's exact words, with its references; none for a question card"},
            "thread_ts": {"type": "string", "description": "His thread the post goes in; none for his DM root"},
            "asked": {
                "anyOf": [TEXT, {"type": "array", "items": TEXT}],
                "description": "Each message of his this post answers, word for word, one per entry, so the answer goes out whole",
            },
            "card": {"type": "object", "description": "A question card exactly as drive job card printed it"},
            "tap_prs": {
                "type": "array",
                "items": TEXT,
                "description": (
                    "The PRs on his tap, bottom of the stack first, each as its URL or owner/repo#number, drawn as one stack"
                    " card; refused while text leaves out the words of a drive tooling order one of them serves"
                ),
            },
            "about": {"type": "string", "description": "The waits id of the card this reply is about"},
            "visuals": {"type": "array", "items": VISUAL},
            "order": {
                "type": "string",
                "description": "The order this post is on, which records it there; refused, posting nothing, in none of its threads (its own, its incident's and every alert's joined to it with --joins, both ways, its cards' or its PRs' cards'), naming the order whose thread it is",
            },
            "record": {
                "type": "string",
                "description": (
                    "The seq of the report record this post carries. With order, the post delivers the part its part attr names,"
                    " or every part when its close attr names the order; a record naming neither is a progress line delivering"
                    " no part, whatever job it names, and so is a post with no record. The record is the order's holder's: on an"
                    " order the communicator holds, such as his read-only question, a report the handler posts from the communicator's"
                    " lane, and on one the meta session holds, a report from the meta lane, since a coordinator's report is refused"
                    " there. catch_up=true makes it his catch-up, opening *Catch-up"
                    " since <when he was last heard>* in place of any title"
                ),
            },
            "dry_run": {"type": "boolean", "description": "Resolve the references and draw the visuals, print what would post, and post nothing, refused as the post would be"},
        },
    },
}


def tool_list() -> list[dict]:
    return [SAY, *tools.verb_tools()]


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
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "drive", "version": json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]},
                "instructions": INSTRUCTIONS,
            }
        case "tools/list":
            LISTED["drive"] = newest_drive()
            reply["result"] = {"tools": listed(LISTED["drive"])}
        case "tools/call":
            reply["result"] = call(request["params"]["name"], request["params"].get("arguments") or {})
        case "ping":
            reply["result"] = {}
        case method:
            reply["error"] = {"code": -32601, "message": f"no method {method}"}
    with writing:
        stdout.write(json.dumps(reply) + "\n")
        if request["method"] == "tools/call" and LISTED.get("drive") not in (None, newest_drive()):
            del LISTED["drive"]
            stdout.write(json.dumps(LIST_CHANGED) + "\n")
        stdout.flush()


def listed(drive: str) -> list[dict]:
    """The tools the newest installed drive serves, so an install reaches a live session through list_changed with no reconnect."""
    if drive == OWN_DRIVE:
        return tool_list()
    return json.loads(subprocess.run([drive, "mcp", "--list"], capture_output=True, text=True, check=True, timeout=CALL_SECONDS).stdout)


def call(tool: str, arguments: dict) -> dict:
    """Run the tool in the newest installed drive, so a live session's call runs the plugin installed now, as its hooks do; a refusal keeps the lines of the ops that ran before it."""
    try:
        done = subprocess.run([newest_drive(), "tool", tool], input=json.dumps(arguments), capture_output=True, text=True, check=False, timeout=CALL_SECONDS)
    except subprocess.TimeoutExpired:
        return {"content": [{"type": "text", "text": f"drive tool {tool} did not finish in {CALL_SECONDS} seconds"}], "isError": True}
    said = done.stdout + done.stderr if done.returncode else done.stdout
    return {"content": [{"type": "text", "text": said.strip()}], "isError": bool(done.returncode)}


def newest_drive() -> str:
    root = PLUGIN_ROOT
    if root.is_relative_to((config_dir() / "plugins" / "cache").resolve()):
        root = Path(installed(PLUGIN)["installPath"])
    return str(root / "bin" / "drive")
