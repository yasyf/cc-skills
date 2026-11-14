import contextlib
import importlib
import json
import os
import pkgutil
import select
import signal
import subprocess
import sys
import tempfile
import threading
import traceback
import uuid
from pathlib import Path
from typing import TextIO

import drive
from drive import cli, install, reads, state, tools
from drive.install import config_dir, installed

PLUGIN = "long-running@skills"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
CALL_SECONDS = 180
OWN_DRIVE = str(PLUGIN_ROOT / "bin" / "drive")
LISTED: dict[str, str] = {}
CALLS: dict[int | str, "subprocess.Popen | WarmCall"] = {}
WARM: dict[str, "Warm"] = {}
WARMING = threading.Lock()
KILL_SECONDS = 10
LIST_CHANGED = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
INSTRUCTIONS = (
    "Every message to him goes through say, each picture or screen recording he is sent included: one call posts it to his Slack DM and"
    " shows it on the drive's dashboard."
    " Name a card as {card:<waits id>}, a PR the drive holds as {pr:<number>} and any other as"
    " {pr:<owner/repo#number>|<what it does>}, an order as {order:<id>}, a ruling of his as"
    " {answer:<id>} and the record that measured a cause or number as {record:<seq>} in its text, and the tool links each the way each surface"
    " shows it, under its title or under the words after a |, as {pr:298|the fix that draws charts}. When a job started or a"
    " record went out is {started:<job>} or {at:<seq>}, which say fills from the drive's stamps, {started:<job>} labelled as"
    " the job's start; neither is ever when a release, build or deploy started, which is quoted as its build or tool printed"
    " it, in backticks or its link's words, on the line linking it; a time a tool or query measured, such as an outage window, is typed in Pacific on the line linking it, and otherwise only a time after the write, as in \"next line by 5:40 AM\", is typed, naming its day when it falls on another, as \"6:54 AM tomorrow\". Its hook finds a clock time typed for work already done, a line naming a cause"
    " with no {record:}, a line of a session posting on its own telling him work is fixed, merged, queued, live or covers his"
    " case with no {record:} of the read that measured it and no unverified on it, a line telling him the drive failed with no {order:} or {pr:} carrying how that is prevented, and"
    " each `cc-slack check-post` finding, and Jev judges each by the rule it stands for and his words once say has"
    " posted it, so say stays fast: a post Jev holds stays in his DM as written, and the session that posted it hears Jev's"
    " reason and rewords it in place with say(edit=<its permalink>, text=…) before its turn ends; a post with a finding"
    " a count or a mark decides, the post's word count or his reading as away, and a dry run are judged before say runs,"
    " and when Jev cannot finish that finding still refuses, while each other finding only warns and the post stays as"
    " written; on a handler's post Jev also checks each of its claims against the read it names, whole, and holds the post"
    " for a claim that read does not back and a line about code, a number or a state that claims leaves out, a comment,"
    " message, doc, PR body or earlier answer being a claim to test, never a source; it posts exactly the words it is given"
    " and adds none."
    " The drive's verbs are tools too: heard, handler, which starts a handler as its own session, record for the"
    " records between the drive's sessions, order, job and pr taking several ops a call, and status; each takes"
    " dry_run, and drive, the drive it acts on when this session is bound to none or several."
)
TEXT = {"type": "string"}
VISUAL = {
    "type": "object",
    "description": (
        "One visual: a picture in Slack, or Slack's own chart for a chart, and drawn live from its source on the dashboard;"
        " a screen recording uploads to Slack as its file and plays on the dashboard"
    ),
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["code", "arch", "mermaid", "chart", "compare", "dot", "image"],
            "description": "An arch map for arch, a before-and-after for compare, Graphviz for dot, a picture or screen recording file for image",
        },
        "source": {
            "type": "string",
            "description": (
                "What it draws, as in a Show me fence: the code; Mermaid or dot source; a Vega-Lite JSON spec, data inline, for"
                ' a chart; {"options": [{"name", "recommended", "language", "code", "points": ["+ a gain", "- a cost"]}]} for a'
                ' compare; for arch, {"arch": 1, "name", "title", "nodes": [{"id", "kind", "name", "role"}], "edges": [{"from", "to",'
                ' "label"}]}, each id lowercase letters, digits and dashes, kind actor, app, service, worker, store, log, external,'
                ' artifact or doc, a store or log node adding "medium": memory, disk, bucket, keys or pages;'
                " for image, the absolute path or URL of a picture or of a screen recording, such as a .mov or .mp4"
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
        "Post a line, an answer, a PR card (tap_prs) or a question card (card) to his Slack DM, with any visuals, pictures and screen"
        " recordings included, and"
        " show it on the drive's dashboard, in one call; with channel_id, post a line, or a card's line linking the card it draws in"
        " his DM, in a thread outside his DM, such as an alert's, through cc-slack reply, or with a person's user id and no thread_ts a new post in the group DM of that person, him and the bot, through cc-slack send; cc-slack's Slack permission hook judges either as it judges slack_reply or slack_send. In text, {card:<waits id>}, {pr:<number, owner/repo#number or"
        " URL>} and {order:<id>} link under the words naming the card, PR or order, or the words after a |, as"
        " {pr:298|the fix that draws charts}; Slack draws the card under the link. {record:<seq>} links what that record"
        " measured, its url, under the first clause of its own words, at most ten, or under the words after a |. {answer:<id>}"
        " draws his recorded answer as when he said it and its first words, linked to the ack of them in his DM. {started:<job>}"
        " and {at:<seq>} write when a job started, as \"4:50 AM (job start)\", and when a record went out from the drive's stamps,"
        " since a clock time typed for work already done is refused, while a time a tool or query printed, such as a GitHub check's or an"
        " outage window Datadog measured, passes written in Pacific on the line linking that tool or read, or in backticks as it"
        " printed it, and a time after the write, such as \"next line by 5:40 AM\" or \"6:54 AM tomorrow\", passes;"
        " a line saying when a release, build or deploy started takes the time its build or tool printed, in backticks or its"
        " link's words, on the line linking it, and is refused with either stamp in a clause naming no job or record. Returns POSTED"
        " <permalink>, CARD <permalink> for a card, the question card's own link first, recorded as the card of the"
        " question asked under its waits id, and the line recording the post on the one order say decides, which no caller names:"
        " a post carrying a record goes on the order its report is about, a handler's post with none on the order of the message"
        " of his or the record it holds, and an edit, a card, a PR card, his catch-up and an answer in a teammate's watched thread"
        " on none; it is recorded wherever it goes, a thread the order holds not yet becoming one of its threads, and a clock time"
        " its words promise his next line by, such as \"Next line by 9:45\" or \"Next line by 10:00 AM tomorrow\", becomes the order's due, from which the watch starts a handler on the line it owes until one is posted, but on an incident, whose"
        " next line the watch owes five quiet minutes after this one once it has news. A refusal posts nothing and names its fix: a reference that names nothing,"
        " an order or job id such as j1700, or a post over its length, to him while he is away, or out of turn in the thread"
        " a bug report or question came from, that Jev does not lift: make the fix and call again. Jev judges every other post once say has posted it, and"
        " a post Jev holds stays in his DM as written beside its POSTED line, with Jev's reason and the fix: a handler's claim"
        " the read it names in claims does not back, or a finding Jev read its rule as holding against, such as a line"
        " naming a cause (because, caused) with no {record:}, a session's own line telling him work is fixed, merged, queued, live or covers"
        " his case with no {record:} of the read that measured it in this turn and no unverified on it, a line telling him a card, check, hook, session, job or tool of"
        " the drive failed, refused or disconnected with no {order:} or {pr:} on it carrying how that is prevented, a handler's line saying a job"
        " is being started with no {started:<j>} and no unanswered handoff asking for one on its order, or a"
        " word `cc-slack check-post` flags: reword that post in place with edit=<its permalink> before your turn ends."
        " A post cc-slack reports failing after it went out is read back from Slack,"
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
            "close": {
                "type": "boolean",
                "description": (
                    "With no record: this post is the answer that finishes the order of the message of his or the record your handler"
                    " holds, so say writes its report from the"
                    " communicator's lane with the post's words, closes the order done on it, and keeps POSTED <permalink> as the return"
                    " of the reader job your handler holds on it, the line you end on; one call in place of a report, a say and drive"
                    " order set"
                ),
            },
            "record": {
                "type": "string",
                "description": (
                    "The seq of the report record this post carries, which puts the post on the order the report is about, where it"
                    " delivers each part its part attr names,"
                    " `<o>:<part>` items separated by `;`, on that order and every other order an item names, or every part when its"
                    " close attr names the order, but no work he ordered, every part of a build order or an incident, but an incident's first check and cause, and a part of an order of no"
                    " kind that a builder, codex or design job holds or held or a design lists, unless drive order set --kind answer --part sorted"
                    " it as an answer, which only a builder or codex job's DONE or a merged PR"
                    " delivers; a record naming neither, as one with progress=true, is a progress line"
                    " delivering no part, whatever job it names, and so is a post with no record. The record is the order's holder's: on an"
                    " order the communicator holds, such as his read-only question, a report the handler posts from the communicator's"
                    " lane, and on one the meta session holds, a report from the meta lane, since a coordinator's report is refused"
                    " there, by the check drive order posted and drive order set --state done run too. catch_up=true makes it his catch-up, opening *Catch-up"
                    " since <when he was last heard>* in place of any title and closing with a count of every open card waiting on him,"
                    " linking the dashboard's queue, then each card asked since he was last heard, five a message, which say draws itself:"
                    " its text names no card, and no word limit counts them. It posts only while the drive"
                    " owes him a catch-up, which posting it settles. A correction record"
                    " re a report held for his catch-up posts nothing, since the catch-up carries it, and one re a report his catch-up"
                    " carried names that post to reword. A held report its writer withdrew goes out in no catch-up and gives back the"
                    " close of each open order it claimed"
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


def tool_named(name: str) -> dict:
    return next(tool for tool in tool_list() if tool["name"] == name)


def serve(stdin: TextIO, stdout: TextIO) -> int:
    """Answer MCP requests read from stdin, each in its own thread, so a slow post never holds the next call, each call running as its own process of the newest installed drive."""
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
    return json.loads(subprocess.run([sys.executable, "-I", drive, "mcp", "--list"], capture_output=True, text=True, check=True, timeout=CALL_SECONDS).stdout)


def call(request_id: int | str, tool: str, arguments: dict) -> dict | None:
    """Run the tool in the newest installed drive, so a live session's call runs the plugin installed now, as its hooks do; a refusal names every op the check refused, or the op that refused while the call ran.
    The call is its own process either way: forked by that drive's warm process, which has the drive imported already, so it pays for no exec, import or parser, or on a Sprite lane its own `drive tool`, whose arguments go in a file rather than a pipe the server writes, so a child that fails before reading them cannot hold the call past CALL_SECONDS. A call Claude Code cancels has its process killed and gets no reply."""
    if not os.environ.get(reads.SPRITE_LANE):
        return warm_call(request_id, tool, arguments)
    with tempfile.TemporaryFile("w+") as given:
        json.dump(arguments, given)
        given.seek(0)
        with subprocess.Popen([sys.executable, "-I", newest_drive(), "tool", tool], stdin=given, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
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


def warm_call(request_id: int | str, tool: str, arguments: dict) -> dict | None:
    run = WarmCall(tool, arguments)
    CALLS[request_id] = run
    try:
        warm = warmed(newest_drive(), run)
        if not run.done.wait(CALL_SECONDS):
            run.timed_out = True
            warm.kill(run)
            if not run.done.wait(KILL_SECONDS):
                warm.abandon()
                run.done.wait()
        out, err = Path(run.out).read_text(), Path(run.err).read_text()
    finally:
        os.unlink(run.out)
        os.unlink(run.err)
    if CALLS.pop(request_id, None) is None:
        return None
    if run.timed_out:
        return {"content": [{"type": "text", "text": f"{out}{err}drive tool {tool} did not finish in {CALL_SECONDS} seconds".strip()}], "isError": True}
    if run.code is None:
        return {"content": [{"type": "text", "text": f"{out}{err}drive tool {tool}: the drive's warm process ended before the call did".strip()}], "isError": True}
    said = out + err if run.code else out
    return {"content": [{"type": "text", "text": said.strip()}], "isError": bool(run.code)}


class WarmCall:
    """One call a warm process forks: its files for stdout and stderr, the warm process running it, and its exit code once reaped, None when the warm process ended first."""

    def __init__(self, tool: str, arguments: dict) -> None:
        self.key = uuid.uuid4().hex
        self.tool, self.arguments = tool, arguments
        self.out, self.err = (kept(tool) for _ in range(2))
        self.done = threading.Event()
        self.warm: Warm | None = None
        self.code: int | None = None
        self.timed_out = False

    def ended(self, code: int | None) -> None:
        self.code = code
        self.done.set()

    def kill(self) -> None:
        if self.warm:
            self.warm.kill(self)


class Warm:
    """One install's `drive mcp --warm`, single-threaded so it may fork, and the calls it runs, each told its exit code on its stdout once reaped."""

    def __init__(self, drive_path: str) -> None:
        self.drive_path = drive_path
        self.process = subprocess.Popen([drive_path, "mcp", "--warm"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.guard = threading.Lock()
        self.writing = threading.Lock()
        self.running: dict[str, WarmCall] = {}
        threading.Thread(target=self.heard, daemon=True).start()

    def start(self, run: WarmCall) -> None:
        run.warm = self
        with self.guard:
            self.running[run.key] = run
        if not self.sent({"key": run.key, "tool": run.tool, "arguments": run.arguments, "out": run.out, "err": run.err}):
            with self.guard:
                self.running.pop(run.key, None)
            run.ended(None)

    def kill(self, run: WarmCall) -> None:
        """Have the warm process kill the call's process while it still runs, so no pid it reaped and the system reused is signalled."""
        self.sent({"kill": run.key})

    def sent(self, line: dict) -> bool:
        with self.writing:
            try:
                self.process.stdin.write(json.dumps(line) + "\n")
                self.process.stdin.flush()
            except (BrokenPipeError, ValueError):
                return False
        return True

    def heard(self) -> None:
        for line in self.process.stdout:
            said = json.loads(line)
            with self.guard:
                run = self.running.pop(said["key"], None)
            if run:
                run.ended(said["code"])
        with self.guard:
            left, self.running = list(self.running.values()), {}
        for run in left:
            run.ended(None)

    def retire(self) -> None:
        with self.writing:
            self.process.stdin.close()

    def abandon(self) -> None:
        """Kill a warm process that did not answer a kill within KILL_SECONDS, ending every call it runs."""
        with WARMING:
            if WARM.get(self.drive_path) is self:
                del WARM[self.drive_path]
        self.process.kill()


def warmed(drive_path: str, run: WarmCall) -> "Warm":
    """Start the call in the warm process of this install, started now when it has none or its last one ended; an older install's is retired, finishing the calls it runs."""
    with WARMING:
        for older in [path for path in WARM if path != drive_path]:
            WARM.pop(older).retire()
        if (held := WARM.get(drive_path)) is None or held.process.poll() is not None:
            WARM[drive_path] = held = Warm(drive_path)
    held.start(run)
    return held


def kept(tool: str) -> str:
    handle, path = tempfile.mkstemp(prefix=f"drive-{tool}-")
    os.close(handle)
    return path


def warm() -> int:
    """Fork one process for each call read from stdin, each running its tool as `drive tool` would, kill one a kill line names while it runs, and say on stdout each one's exit code once reaped; end once stdin closes and every call it forked has. It builds the parser before its first fork, so no call pays for it."""
    for module in pkgutil.iter_modules(drive.__path__):
        importlib.import_module(f"drive.{module.name}")
    cli.parser()
    woken, wake = os.pipe()
    os.set_blocking(woken, False)
    os.set_blocking(wake, False)
    signal.set_wakeup_fd(wake)
    signal.signal(signal.SIGCHLD, lambda *_: None)
    running: dict[int, str] = {}
    pids: dict[str, int] = {}
    pending, reading = b"", True
    while reading or running:
        ready, _, _ = select.select([0, woken] if reading else [woken], [], [])
        with contextlib.suppress(BlockingIOError):
            while os.read(woken, 512):
                pass
        while running and (reaped := os.waitpid(-1, os.WNOHANG))[0]:
            del pids[key := running.pop(reaped[0])]
            told(key, code=os.waitstatus_to_exitcode(reaped[1]))
        if 0 not in ready:
            continue
        if not (chunk := os.read(0, 1 << 16)):
            reading = False
            continue
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            request = json.loads(line)
            if "kill" in request:
                if pid := pids.get(request["kill"]):
                    os.kill(pid, signal.SIGKILL)
                continue
            if (pid := os.fork()) == 0:
                forked(request, woken, wake)
            running[pid], pids[request["key"]] = request["key"], pid
    return 0


def told(key: str, code: int) -> None:
    os.write(1, (json.dumps({"key": key, "code": code}) + "\n").encode())


def forked(request: dict, woken: int, wake: int) -> None:
    """The forked call: its tool run as `drive tool` runs it, its lines and warnings written to the files the server reads, then exit with its code; one a newer install's save refused before it changed anything, by state.WROTE, runs again as the newest install's `drive tool` in this process."""
    signal.set_wakeup_fd(-1)
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    os.close(woken)
    os.close(wake)
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    os.dup2(os.open(request["out"], os.O_WRONLY | os.O_TRUNC), 1)
    os.dup2(os.open(request["err"], os.O_WRONLY | os.O_TRUNC), 2)
    code = 1
    try:
        print(cli.run_tool(request["tool"], request["arguments"]))
        code = 0
    except state.NewerDrive as error:
        if not state.WROTE:
            install.reopen(["tool", request["tool"]], json.dumps(request["arguments"]))
        print(f"drive: {error}", file=sys.stderr)
        code = 2
    except cli.VERB_ERRORS as error:
        print(f"drive: {error}", file=sys.stderr)
        code = 2
    except SystemExit as stop:
        code = stop.code if isinstance(stop.code, int) else 1
    except BaseException:
        traceback.print_exc()
    finally:
        for thread in threading.enumerate():
            if thread is not threading.main_thread() and not thread.daemon:
                thread.join()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)


def newest_drive() -> str:
    root = PLUGIN_ROOT
    if root.is_relative_to((config_dir() / "plugins" / "cache").resolve()):
        root = Path(installed(PLUGIN)["installPath"])
    return str(root / "bin" / "drive")
