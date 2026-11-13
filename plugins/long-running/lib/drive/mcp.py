import json
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import TextIO

from drive import state, tools
from drive.install import config_dir, installed

PLUGIN = "long-running@skills"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
CALL_SECONDS = 180
OWN_DRIVE = str(PLUGIN_ROOT / "bin" / "drive")
LISTED: dict[str, str] = {}
CALLS: dict[int | str, subprocess.Popen] = {}
LIST_CHANGED = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
INSTRUCTIONS = (
    "Every message to him goes through say: one call posts it to his Slack DM and shows it on the drive's dashboard."
    " Name a card as {card:<waits id>}, a PR the drive holds as {pr:<number>} and any other as"
    " {pr:<owner/repo#number>|<what it does>}, an order as {order:<id>}, a ruling of his as"
    " {answer:<id>} and the record that measured a cause or number as {record:<seq>} in its text, and the tool links each the way each surface"
    " shows it, under its title or under the words after a |, as {pr:298|the fix that draws charts}. When a job started or a"
    " record went out is {started:<job>} or {at:<seq>}, which say fills from the drive's stamps, {started:<job>} labelled as"
    " the job's start; neither is ever when a release, build or deploy started, which is quoted as its build or tool printed"
    " it, in backticks or its link's words, on the line linking it; only a time to come, as in \"next line by 5:40 AM\", is typed. Its hook finds a clock time typed for work already done, a line naming a cause"
    " with no {record:}, a line telling him the drive failed with no {order:} or {pr:} carrying how that is prevented, and"
    " each `cc-slack check-post` finding, and Jev judges each by the rule it stands for and his words, or, when"
    " Jev cannot finish, each only warns and the post goes out as written;"
    " on a handler's post Jev also checks each of its claims against the read it names, whole, and holds the post for a"
    " claim that read does not back and a line about code, a number or a state that claims leaves out, a comment,"
    " message, doc, PR body or earlier answer being a claim to test, never a source; it posts exactly the words the hook"
    " let out and adds none."
    " The drive's verbs are tools too: heard, handler, which starts a handler as its own session, record for the"
    " records between the drive's sessions, order, job and pr taking several ops a call, and status; each takes"
    " dry_run, and drive, the drive it acts on when this session is bound to none or several."
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
                ' compare; for arch, {"arch": 1, "name", "title", "nodes": [{"id", "kind", "name", "role"}], "edges": [{"from", "to",'
                ' "label"}]}, each id lowercase letters, digits and dashes, kind actor, app, service, worker, store, log, external,'
                ' artifact or doc, a store or log node adding "medium": memory, disk, bucket, keys or pages;'
                " an image's absolute path or URL"
            ),
        },
        "language": {"type": "string", "description": "The code's language, for its highlighting"},
        "caption": {"type": "string", "description": "One line naming what it shows: the picture's title in Slack and the line under it on the dashboard"},
    },
    "required": ["kind", "source"],
}
CLAIMS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "claim": {"type": "string", "description": "The post's words making the claim"},
            "read": {
                "type": "string",
                "description": (
                    "The read behind it: record:<seq> for a record, held whole, else words copied from one call of yours, such as the"
                    " path, command or query it ran, each word in its input, every such call handed to Jev with its whole result"
                ),
            },
        },
        "required": ["claim", "read"],
    },
    "description": (
        "On a handler's post, each line saying where code lives, what it does or why, or a number or state, beside the read"
        " behind it, a claim standing on several reads listed once beside each. Jev checks each claim against its reads"
        " together and holds the post for a claim they do not back, a read matching no call of yours, or such a line left"
        " out; your account of your own act, his words quoted back and the drive's state a reference such as {pr:} or"
        " {card:} names need none"
    ),
}
SAY = {
    "name": "say",
    "description": (
        "Post a line, an answer, a PR card (tap_prs) or a question card (card) to his Slack DM, with any visuals, and"
        " show it on the drive's dashboard, in one call; with channel_id, post a line, or a card's line linking the card it draws in"
        " his DM, in a thread outside his DM, such as an alert's, through cc-slack reply, or with a person's user id and no thread_ts a new post in the group DM of that person, him and the bot, through cc-slack send; cc-slack's Slack permission hook judges either as it judges slack_reply or slack_send. In text, {card:<waits id>}, {pr:<number, owner/repo#number or"
        " URL>} and {order:<id>} link under the words naming the card, PR or order, or the words after a |, as"
        " {pr:298|the fix that draws charts}; Slack draws the card under the link. {record:<seq>} links what that record"
        " measured, its url, under the first clause of its own words, at most ten, or under the words after a |. {answer:<id>}"
        " draws his recorded answer as when he said it and its first words, linked to the ack of them in his DM. {started:<job>}"
        " and {at:<seq>} write when a job started, as \"4:50 AM (job start)\", and when a record went out from the drive's stamps,"
        " since a clock time typed for work already done is refused and only a promise such as \"next line by 5:40 AM\" passes;"
        " a line saying when a release, build or deploy started takes the time its build or tool printed, in backticks or its"
        " link's words, on the line linking it, and is refused with either stamp in a clause naming no job or record. Returns POSTED"
        " <permalink>, CARD <permalink> for a card, the question card's own link first, recorded as the card of the"
        " question asked under its waits id, and with order"
        " the line recording the post on it. A refusal posts nothing and names its fix: a reference that names nothing,"
        " an order or job id such as j1700, a handler's claim the read it names in claims does not back, or a finding Jev read its rule as holding against, such as a line naming a cause (because, caused) with no {record:}, a"
        " line telling him a card, check, hook, session, job or tool of the drive failed, refused or disconnected with no"
        " {order:} or {pr:} on it carrying how that is prevented, or a word `cc-slack check-post` flags: make the fix and"
        " call again. A post cc-slack reports failing after it went out is read back from Slack,"
        " kept and recorded on its order, cc-slack's error after WARNING. A reference, link or mention written inside the"
        " words of a raw <url|words> link is refused outright, since Slack draws it as a broken link: close that link first and"
        " put the inner one after it. edit=<the permalink of a post of yours, in his DM or in a thread outside it>"
        " rewords that post in place with text, its references linked as a new post's are, and the dashboard shows the new"
        " words for it; cc-slack's Slack permission hook judges it as it judges slack_edit; it returns EDITED <permalink>. The communicator's ack to his message is one sentence: what he asked and what"
        " happens next."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The post's exact words, with its references, where a markdown table draws as Slack's own data table and the dashboard's table; none for a question card"},
            "thread_ts": {"type": "string", "description": "The thread the post goes in, in his DM unless channel_id names another channel, which a thread outside his DM, such as an alert's, always takes; none for his DM root"},
            "channel_id": {
                "type": "string",
                "description": (
                    "The channel of a thread outside his DM the post goes in, such as an alert's, with its thread_ts, or a person's user id with no"
                    " thread_ts for the group DM of that person, him and the bot; none for his DM. With card or tap_prs, the card goes up in his DM"
                    " and its line, linking it, in this thread. It takes no about, and an edit names its post by its permalink alone"
                ),
            },
            "asked": {
                "anyOf": [TEXT, {"type": "array", "items": TEXT}],
                "description": (
                    "Each message of his this post answers, word for word or by its permalink in his DM, one per entry, so the answer goes out"
                    " whole; a post outside the thread his message is in, such as a share he ordered to another person's group DM, passes its permalink"
                ),
            },
            "card": {"type": "object", "description": "A question card exactly as drive job card printed it"},
            "tap_prs": {
                "type": "array",
                "items": TEXT,
                "description": (
                    "The PRs on his tap, bottom of the stack first, each as its URL or owner/repo#number, drawn as one stack"
                    " card at his DM root, its line and link posted in the thread of each open order they carry out, one outside"
                    " his DM, such as an alert's, named with channel_id and thread_ts; refused while text leaves out the words of"
                    " a drive tooling order one of them serves"
                ),
            },
            "about": {"type": "string", "description": "The waits id of the card this reply is about"},
            "visuals": {"type": "array", "items": VISUAL},
            "order": {
                "type": "string",
                "description": "The order this post is on, which records it there, a clock time its words promise his next line by, such as \"Next line by 9:45\", becoming the order's due, but on an incident, whose next line the watch owes five quiet minutes after this one; refused, posting nothing, in none of its threads (its own, its incident's and every alert's joined to it with --joins, both ways, those he asked about it in, its cards' or its PRs' cards', or, when its record is about this order, the thread of the message of his the record answers or one his own message started, which the post adds to them), naming the order whose thread it is",
            },
            "record": {
                "type": "string",
                "description": (
                    "The seq of the report record this post carries. With order, the post delivers each part its part attr names"
                    " that Jev reads the post's own words as saying is done, its line and dry_run naming each it leaves undelivered,"
                    " `<o>:<part>` items separated by `;`, on this order and every other order an item names, or every part when its"
                    " close attr names the order; a record naming neither, as one with progress=true, is a progress line"
                    " delivering no part, whatever job it names, and so is a post with no record. The record is the order's holder's: on an"
                    " order the communicator holds, such as his read-only question, a report the handler posts from the communicator's"
                    " lane, and on one the meta session holds, a report from the meta lane, since a coordinator's report is refused"
                    " there, by the check drive order posted and drive order set --state done run too. catch_up=true makes it his catch-up, opening *Catch-up"
                    " since <when he was last heard>* in place of any title and closing with every open card waiting on him,"
                    " five a message, which say draws itself: its text names no card, and no word limit counts them. It posts only while the drive"
                    " owes him a catch-up, which posting it settles. A correction record"
                    " re a report held for his catch-up posts nothing, since the catch-up carries it, and one re a report his catch-up"
                    " carried names that post to reword"
                ),
            },
            "edit": {
                "type": "string",
                "description": (
                    "The permalink of a post of yours, in his DM or in a thread outside it such as an alert's, to reword in place"
                    " with text, in place of a new post; it takes text, asked and claims alone, since a card is redrawn with drive job card"
                ),
            },
            "claims": CLAIMS,
            "drive": tools.DRIVE,
            "dry_run": {"type": "boolean", "description": "Resolve the references and draw the visuals, print what would post, and post nothing, refused as the post would be"},
        },
    },
}


def tool_list() -> list[dict]:
    return [SAY, *tools.verb_tools()]


def serve(stdin: TextIO, stdout: TextIO) -> int:
    """Answer MCP requests read from stdin, each in its own thread, so a slow post never holds the next call, holding no install's lock for the session's life, since each call runs as its own verb of the newest installed drive."""
    state.let_go_install()
    writing = threading.Lock()
    for line in stdin:
        if line.strip():
            threading.Thread(target=respond, args=(json.loads(line), stdout, writing)).start()
    return 0


def respond(request: dict, stdout: TextIO, writing: threading.Lock) -> None:
    if "id" not in request:
        if request["method"] == "notifications/cancelled" and (child := CALLS.pop(request["params"]["requestId"], None)):
            child.kill()
        return
    reply = {"jsonrpc": "2.0", "id": request["id"]}
    match request["method"]:
        case "initialize":
            reply["result"] = {
                "protocolVersion": request["params"]["protocolVersion"],
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "drive", "version": state.VERSION},
                "instructions": INSTRUCTIONS,
            }
        case "tools/list":
            LISTED["drive"] = newest_drive()
            reply["result"] = {"tools": listed(LISTED["drive"])}
        case "tools/call":
            result = call(request["id"], request["params"]["name"], request["params"].get("arguments") or {})
            if result is None:
                return
            reply["result"] = result
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


def call(request_id: int | str, tool: str, arguments: dict) -> dict | None:
    """Run the tool in the newest installed drive, so a live session's call runs the plugin installed now, as its hooks do; a refusal names every op the check refused, or the op that refused while the call ran.
    Its arguments go in a file rather than a pipe the server writes, so a child that fails before reading them cannot hold the call past CALL_SECONDS; a call Claude Code cancels has its child killed and gets no reply."""
    with tempfile.TemporaryFile("w+") as given:
        json.dump(arguments, given)
        given.seek(0)
        with subprocess.Popen([newest_drive(), "tool", tool], stdin=given, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
            CALLS[request_id] = child
            try:
                out, err = child.communicate(timeout=CALL_SECONDS)
            except subprocess.TimeoutExpired:
                child.kill()
                out, err = "", f"drive tool {tool} did not finish in {CALL_SECONDS} seconds"
    if CALLS.pop(request_id, None) is None:
        return None
    said = out + err if child.returncode else out
    return {"content": [{"type": "text", "text": said.strip()}], "isError": bool(child.returncode)}


def newest_drive() -> str:
    root = PLUGIN_ROOT
    if root.is_relative_to((config_dir() / "plugins" / "cache").resolve()):
        root = Path(installed(PLUGIN)["installPath"])
    return str(root / "bin" / "drive")
