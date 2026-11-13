import json
import os
import re
import shlex
import subprocess
import time
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self

from drive import install, limits, procs, reads, render, state, superseded
from drive.brief import BRIEF_JOB, carried, heavier

RECORD = re.compile(r"^Record: #?(\d+)\b", re.MULTILINE)
LINK = re.compile(r"^Link:\s*(\S+)", re.MULTILINE)
THREAD = re.compile(r"^Thread:[ \t]*(?:\S+[ \t]+)?(\d+\.\d+)", re.MULTILINE)
RETYPED = re.compile(r"\b(?:POSTED|EDITED|CARD)(?:\s+https://\S+)+\s*;?\s*")
HIS_KINDS = ("owner", "answer")
RE_HOPS = 8
CORRECTION_BYTES = 1 << 20
TRUNK_DRIVE = "trunk"
TRUNK_FAILED = "failed in Trunk's merge queue"
HANDLER = "long-running:handler"
SYNTHETIC = "<synthetic>"
OUTAGE_WAITS = (1, 2, 4, 8)
RESUMED = "Resumed: an API error ended your last turn before it was done, so do now what the prompt it answered asked, from where it stopped."
MESSAGE_ORIGIN = "coordinator"
DESIGNER = "long-running:designer"
HIS_WORDS = "Message (his words)"
ORDER = "Order"
IN_THREAD = "In his thread"
SCRATCH = re.compile(r"Scratch:\s*(\S+)")
LABEL = re.compile(rf"^({'|'.join(re.escape(label) for label in (*render.HANDLER_LABELS, ORDER, IN_THREAD))}):")
DATADOG_PLUGIN = "datadog@claude-plugins-official"
SESSION_SETTINGS = {
    "bashOutputMaxChars": 6000,
    "outputStyle": "default",
    "env": {"CLAUDE_CODE_SILENT_TURN_REMINDER": "0"},
    "permissions": {"deny": ["AskUserQuestion"]},
    "enabledPlugins": {DATADOG_PLUGIN: True},
}
ORCA = "orca@skills"
START_PAGE = Path(__file__).resolve().parents[2] / "skills" / "long-running" / "reference" / "start.md"
SESSION_ENV = "<session env>"
SESSION_SETTINGS_HOLE = "<session settings>"
DOUBLE_QUOTED = re.compile(r'[\\"$`]')
STUCK = re.compile(r"\b(j\d+)\b[^.;\n]*?\b(?:stuck|silent|stalled|hung)\b", re.IGNORECASE)
UNOWNED = re.compile(
    r"\b(?:no ?one|nobody|no (?:job|builder|agent|lane))(?:\s+\w+){0,2}?\s+(?:working|works|picked|taken|took|carries|owns|is on it)\b|\bunowned\b|\bnot (?:been )?picked up\b",
    re.IGNORECASE,
)
JOB_WORD = re.compile(r"\bj\d+\b")
UNOWNED_RULE = (
    "A line never says work sits with no one on it: the session that saw it puts a job on it that turn and the line names that job, by his words"
    ' 94c20c6 "its unnaceptbale to just say that no one is workiong on it, get someone workong in it, for all of these"; a line saying no one'
    " needs to work on something, or that a finished job is no longer on it, says no such thing."
)
READ_WINDOW = timedelta(minutes=10)
TRANSCRIPT_READ = ("cc-transcript", ".jsonl")
UNREAD_JOB = (
    "refused: this line calls {jobs} stuck or silent, and your transcript shows no read of {its} transcript in the last 10"
    " minutes; read it with `drive job replies <j>` or `cc-transcript`, then quote what it shows"
)
TAIL_BYTES = 4 << 20
TYPED_TIME_RULE = (
    "A clock time a session typed for work already done in words he reads, a post, a card or a record a handler posts to him,"
    " such as when a job started or a post went out, which the drive's stamps give, and a job's or record's stamp given as when a release, build or deploy started; a promise of a time to"
    " come, a time quoted as a tool printed it and a linked time pass, a release's, build's or deploy's start only on the line"
    " linking the build or tool it was quoted from."
)


class Ruled(str):
    """A refusal Jev judges by the one sentence naming what its check protects against, not by its own words, which name the fix; counted holds its findings a count or a mark decides, such as a post's word count or his reading as away, which refuse when Jev cannot finish."""

    rule: str
    counted: str

    def __new__(cls, reason: str, rule: str, counted: str = "") -> Self:
        made = super().__new__(cls, reason)
        made.rule = rule
        made.counted = counted
        return made


class Unjudged(str):
    """A refusal its check decides by the call's shape alone, which Jev never weighs, so the same call gets the same verdict every time."""


def ruled(reason: str) -> dict:
    """The fields the capt-hook pack reads off a refusal: the rule Jev judges it by and the findings that refuse when Jev cannot finish, or that no Jev judges it."""
    if isinstance(reason, Unjudged):
        return {"judged": False}
    if not isinstance(reason, Ruled):
        return {}
    return {"rule": reason.rule} | ({"counted": reason.counted} if reason.counted else {})


def transcript(payload: dict) -> Path:
    """The transcript of the agent whose step fired a hook: a handler's own session's, else the one beside its session's."""
    if path := payload.get("agent_transcript_path"):
        return Path(path)
    return Path(payload["transcript_path"]).with_suffix("") / "subagents" / f"agent-{payload['agent_id']}.jsonl"


def transcript_of(payload: dict) -> Path:
    """The transcript of whoever made the call that fired a hook: its agent's, or its session's own."""
    return transcript(payload) if payload.get("agent_id") else Path(payload["transcript_path"])


def tail(path: Path) -> list[dict]:
    """The transcript's newest rows, as many as its last few megabytes hold."""
    with path.open("rb") as handle:
        start = max(0, handle.seek(0, 2) - TAIL_BYTES)
        handle.seek(start)
        lines = handle.read().split(b"\n")
    return [json.loads(line) for line in lines[1 if start else 0 :] if line.strip()]


def parts(row: dict) -> list[dict]:
    content = (row.get("message") or {}).get("content")
    return content if isinstance(content, list) else []


def unread_job(payload: dict, text: str) -> str | None:
    """Why a line calling a job stuck or silent, a report to the communicator or a post to him, waits for a read of that job's transcript: the poster's own calls in the last 10 minutes show none."""
    if not (named := dict.fromkeys(STUCK.findall(text))):
        return None
    since = datetime.now(UTC) - READ_WINDOW
    calls = " ".join(
        json.dumps(part.get("input"))
        for row in tail(transcript_of(payload))
        if row.get("timestamp") and datetime.fromisoformat(row["timestamp"]) > since
        for part in parts(row)
        if part.get("type") == "tool_use"
    )
    jobs = [job for job in named if f"replies {job}" not in calls and not any(read in calls for read in TRANSCRIPT_READ)]
    if not jobs:
        return None
    return UNREAD_JOB.format(jobs=", ".join(jobs), its="their" if len(jobs) > 1 else "its")


def unowned_work(text: str) -> Ruled | None:
    """Why a line, a report to the communicator or a post to him, says work has no one on it while it names no job on it now: the session that saw it puts one on it that turn, by his words 94c20c6."""
    if not (found := UNOWNED.search(text)) or JOB_WORD.search(text):
        return None
    return Ruled(
        f'refused: "{found[0]}" says work has no one on it and the line names no job: put one on it now, drive job add for a part no job holds or'
        " drive job resume for the one holding it, and name that job in the line, as {started:<j>} in a post to him",
        UNOWNED_RULE,
    )


def typed_time(text: str, data: dict) -> Ruled | None:
    """Why a session's record or message types a clock time for work already done, which Jev judges by the rule, so a promise its words put in another form than "by 5:40 AM" still passes."""
    refusal = limits.typed_time(text, data)
    return refusal and Ruled(f"refused: {refusal}", TYPED_TIME_RULE)


def seen(payload: dict) -> dict:
    """A hook's payload as the drive's checks read it: a handler's own `claude --bg` session reads as an agent of the communicator's session, as a handler ran before it had a session of its own, so every check on a handler binds it unchanged."""
    if payload.get("agent_id") or payload.get("agent_type") != HANDLER or not (names := state.session_drives(payload["session_id"])):
        return payload
    data = state.saved(names[0])
    return payload | {
        "session_id": data["roles"]["communicator"]["session"],
        "agent_id": state.handler_id(data, payload["session_id"]),
        "agent_transcript_path": payload["transcript_path"],
        "handler_session": payload["session_id"],
    }


def beyond_brief(prompt: str) -> str | None:
    """The first line of a handler's prompt that is no line of its brief: each line opens with a brief label, his words may run on below their label, and Context runs to at most three lines."""
    label, context = None, 0
    for line in prompt.strip().splitlines():
        if found := LABEL.match(line):
            label, context = found[1], 1
        elif line.strip() and not (label in (HIS_WORDS, IN_THREAD) or (label == "Context" and (context := context + 1) <= 3)):
            return line.strip()
    return None


def his_words(prompt: str) -> str:
    """His words as a brief carries them, from their label to the next line opening with a label."""
    lines = prompt.strip().splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith(f"{HIS_WORDS}:"))
    end = next((index for index in range(start + 1, len(lines)) if LABEL.match(lines[index])), len(lines))
    return "\n".join([lines[start].removeprefix(f"{HIS_WORDS}:"), *lines[start + 1 : end]]).strip()


def shaped(payload: dict) -> dict:
    """The Agent call a drive session makes, in the one shape the drive runs it: a role's call without `name`, since a named agent joins the agent team and never wakes on events, a design job as the designer, the only definition that keeps it read only and its forks in his cards' shape, and Fable on the designer alone; and a lane whose order needs its definition's Opus or Fable without the Sonnet or Haiku passed for it."""
    call = payload["tool_input"]
    if not (names := state.session_drives(payload.get("session_id") or "")):
        return call
    data = state.load(names[0])
    text = carried(call.get("prompt") or "")
    job_id = named[1] if (named := BRIEF_JOB.search(text)) and named[1] in data["jobs"] else None
    made = dict(call)
    if not payload.get("agent_id"):
        made.pop("name", None)
        if job_id and data["jobs"][job_id]["kind"] == "design":
            made["subagent_type"] = DESIGNER
        if "fable" in (made.get("model") or "").lower() and made.get("subagent_type") != DESIGNER:
            del made["model"]
    if job_id and heavier(data, job_id, text, made.get("model")):
        del made["model"]
    return made


def scratch(prompt: str) -> Path | None:
    """The folder a drive agent's brief names as its Scratch, which must exist before the agent's first write."""
    match = SCRATCH.search(prompt)
    return Path(match[1].rstrip(".,;")).expanduser() if match else None


def holds(prompt: str) -> dict:
    """What a handler's brief works on: for a brief naming a Record, whatever its other lines say, that record, each record its re chain names, the message of his the chain reaches and the record's thread; else the message of his its words' Link names and the thread that message is in."""
    if named := RECORD.search(prompt):
        return answered(named[1]) | {"record": named[1]}
    if re.search(rf"^{re.escape(HIS_WORDS)}:", prompt, re.MULTILINE):
        link = linked[1] if (linked := LINK.search(prompt)) else None
        found = state.CARD_LINK.search(link or "")
        message = f"{found[2]}.{found[3]}" if found else None
        thread = (found and found[4]) or (named[1] if (named := THREAD.search(prompt)) else message)
        return {"message": message, "link": link, "thread": thread, "record": None, "re": []}
    return {"message": None, "link": None, "thread": None, "record": None, "re": []}


def answered(seq: str) -> dict:
    """The message of his a record carries or answers: an owner or answer record's own permalink, else that of the record its re names, followed up the chain; the record's thread and each record the chain names either way."""
    record = shown(seq)
    thread = record["fields"].get("attrs", {}).get("thread")
    chain: list[str] = []
    for _ in range(RE_HOPS):
        if record["kind"] in HIS_KINDS and (found := state.CARD_LINK.search((record.get("refs") or {}).get("url") or "")):
            return {"message": f"{found[2]}.{found[3]}", "link": record["refs"]["url"], "thread": thread or found[4] or f"{found[2]}.{found[3]}", "re": chain}
        if not record.get("re"):
            break
        chain.append(str(record["re"]))
        record = shown(chain[-1])
    return {"message": None, "link": None, "thread": thread, "re": chain}


def shown(seq: str) -> dict:
    try:
        return json.loads(reads.run(["cci", "show", seq, "--json"], Path.cwd()).splitlines()[0])
    except reads.ReadError as error:
        raise state.DriveError(f"record {seq} could not be read: {error}") from None


def corrections(name: str, seqs: Iterable[str]) -> dict[str, list[str]]:
    """The seqs of the correction records cci holds re each of seqs, oldest first, read in one cci grep from the lowest of them; a record no correction answers is left out."""
    wanted = set(seqs)
    if not wanted:
        return {}
    argv = ["cci", "grep", ".", "--drive", name, "--kind", "correction", "--since", min(wanted, key=int), "--budget", str(CORRECTION_BYTES), "--json"]
    try:
        lines = reads.run(argv, Path.cwd()).splitlines()
    except reads.ReadError as error:
        raise state.DriveError(f"the corrections of records {', '.join(sorted(wanted, key=int))} could not be read: {error}") from None
    found: dict[str, list[str]] = {}
    for record in map(json.loads, reversed(lines)):
        if (answered := str(record.get("re"))) in wanted:
            found.setdefault(answered, []).append(str(record["seq"]))
    return found


def trunk_failures(keys: list[str], since: str) -> list[dict]:
    """The records of each failure of keys in Trunk's merge queue since then, newest first, which Trunk's webhook relays into cci's trunk drive, read in one cci grep."""
    pattern = "|".join(f"{re.escape(key)} {TRUNK_FAILED}" for key in keys)
    try:
        lines = reads.run(["cci", "grep", pattern, "--drive", TRUNK_DRIVE, "--kind", "queue", "--since", since, "--json"], Path.cwd()).splitlines()
    except reads.ReadError as error:
        raise state.DriveError(f"Trunk's queue records of {', '.join(keys)} could not be read: {error}") from None
    return [json.loads(line) for line in lines]


def holder(data: dict, held: dict) -> str | None:
    """The handler already at work on what a new brief holds: the same message of his, or for a record the same record, one its re chain names or whose chain names it, or its thread, so a thread has one handler at a time, which takes every record in it."""
    for handler_id, entry in data["handlers"].items():
        other = entry["holds"] or {}
        if held["message"] and held["message"] == other.get("message"):
            return handler_id
        if held["record"] and (
            held["record"] == other.get("record")
            or other.get("record") in held["re"]
            or held["record"] in (other.get("re") or ())
            or (held["thread"] and held["thread"] == other.get("thread"))
        ):
            return handler_id
    return None


def gone(session: str | None) -> bool:
    """Whether a bound handler's turn is over, whatever its Stop hook did: no running process's `sessions/<pid>.json`, the record Claude Code keeps of each session's process, names its session with a status but idle, so an idle `claude --bg` session that outlived its turn, as one does until `claude rm` takes it, holds nothing; its background job's state is no test, since Claude Code sets it from the words of its last message. A handler not bound yet has not started its turn, so it is not gone."""
    if not session:
        return False
    return not running([(pid, record.get("procStartFt") or record.get("procStart")) for pid, record in session_records() if record.get("sessionId") == session and record.get("status") != "idle"])


def registry_record(path: Path) -> dict | None:
    """A record Claude Code keeps, None once it is removed or while it is half written, as `claude agents` skips one it cannot parse."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def session_records() -> list[tuple[int, dict]]:
    """Each `sessions/<pid>.json` Claude Code keeps, by the pid its name gives, which can outlive its process."""
    return [
        (int(path.stem), record)
        for path in (install.config_dir() / "sessions").glob("*.json")
        if path.stem.isdigit() and (record := registry_record(path)) is not None
    ]


def background_job(session: str) -> tuple[str, dict] | None:
    """The background job Claude Code keeps for a session in `jobs/<id>/state.json`, its id the folder's name, the one `claude rm` takes, and its state."""
    return next(
        ((path.parent.name, kept) for path in (install.config_dir() / "jobs").glob("*/state.json") if (kept := registry_record(path) or {}).get("sessionId") == session),
        None,
    )


def running(started: list[tuple[int, str | None]]) -> set[tuple[int, str | None]]:
    """Each pid and the start its record keeps whose process runs and started then, as `claude agents` checks each record on its own: signal 0 reaches the pid, and its start, read in this process and printed as `TZ=UTC ps -o lstart=` prints it, is the one the record keeps, or the record keeps none, or no start can be read."""
    reached = [(pid, start) for pid, start in started if pid > 1 and signalled(pid)]
    starts = {pid: procs.lstart(seconds, utc=True) for pid in {pid for pid, start in reached if start} if (seconds := procs.started(pid)) is not None}
    return {(pid, start) for pid, start in reached if not start or starts.get(pid, start) == start}


def signalled(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class Busy(state.DriveError):
    """A live handler holds the same record or message of his a brief holds, so the brief is a copy that needs no handler of its own."""


def handle(name: str, brief: str, detached: bool = False, briefed: Callable[[str], str] | None = None) -> tuple[str, bool]:
    """Start a handler on its brief, unless a live one already holds what the brief holds, since a message of his or a thread has one handler at a time: a brief for another record on that message, in that thread or on its record's re chain is handed to that handler, which takes it at its next step; the id of the handler at work on the brief, and whether it was handed. A handler whose turn is over holds nothing, however it ended: it leaves the drive, and each brief handed to it that it never took goes to the handler this brief reaches, but one for the record this brief names. briefed writes the brief a new handler starts on from its id, recording what it adds; a handler that does not start leaves no entry, and no job it was given nor the order made for that job. Detached, as the handler tool runs it, the handler's session starts after the call returns, from the brief kept for `drive handler-start`; the watch starts it in the call, since nothing waits on the watch."""
    held_now = holds(brief)
    handler_id = uuid.uuid4().hex[:8]
    with state.edit(name) as data:
        left: list[str] = []
        while (held := holder(data, held_now)) and gone(data["handlers"][held]["session"]):
            left += data["handlers"].pop(held)["handed"]
        left = [one for one in dict.fromkeys(left) if RECORD.search(one)[1] != held_now["record"]]
        if held:
            entry = data["handlers"][held]
            his = entry["holds"]["link"] or f"record {entry['holds']['record']}"
            kept = held_now["record"] and held_now["record"] != entry["holds"]["record"]
            entry["handed"] += [one for one in [*left, *([brief.strip()] if kept else [])] if one not in entry["handed"]]
        else:
            data["handlers"][handler_id] = {"session": None, "started": state.stamp(), "holds": held_now, "handed": left, "took": [], "posted": []}
    if held and kept:
        return held, True
    if held:
        raise Busy(f"refused, no handler started: handler {held} is at work on {his}; this copy needs no handler of its own")
    if folder := scratch(brief):
        folder.mkdir(parents=True, exist_ok=True)
    try:
        prompt = briefed(handler_id) if briefed else brief
        if not detached:
            start_handler(name, handler_id, prompt)
            return handler_id, False
        kept = brief_path(name, handler_id)
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(prompt)
        state.detach(name, ["handler-start", handler_id], "handlers.log")
    except (reads.ReadError, state.DriveError) as error:
        unstarted(name, handler_id)
        raise (error if isinstance(error, state.DriveError) else state.DriveError(f"claude --bg started no handler: {error}")) from None
    return handler_id, False


def unstarted(name: str, handler: str) -> None:
    """Take a handler that did not start off the drive, with the job it was given and the order made for that job."""
    with state.edit(name) as data:
        data["handlers"].pop(handler, None)
        if job_id := state.agent_job(data, handler):
            del data["orders"][data["jobs"].pop(job_id)["order"]]


def brief_path(name: str, handler: str) -> Path:
    return state.drive_dir(name) / "handlers" / f"{handler}.md"


def start_kept(name: str, handler: str) -> str:
    """Start the handler `handle` kept, in its own process after the handler call returned, since `claude --bg` takes seconds; one that fails to start leaves the drive's handlers, with the job it was given and the order made for that job, and tells the communicator in a done record from lane handler, naming the error and its kept brief."""
    kept = brief_path(name, handler)
    try:
        start_handler(name, handler, kept.read_text())
    except reads.ReadError as error:
        unstarted(name, handler)
        line = f"BLOCKED handler {handler} did not start: {error}; its brief is kept in {kept}: start it again with the handler tool"
        reads.run(["cci", "post", "--drive", name, "--lane", "handler", "--kind", "done", "--to", f"communicator@{name}", "--id", f"handler-start-{handler}", "--text", line], Path.cwd())
        return line
    return f"started {handler}"


def start_handler(name: str, handler: str, prompt: str) -> None:
    """Start a handler as its own `claude --bg` session on its agent definition, which sets its model, in the communicator's checkout, whether the communicator or the drive's watch starts it, its SessionStart hook binding it to the drive by the id in its environment, `CCI_PARENT_SESSION` naming the communicator's session, so cci binds the handler's session to no lane and it posts as the communicator's lanes, and `CLAUDE_LONG_RUNNING_DRIVE` naming the drive, so each cci call that names no drive resolves this one; the watch's own lane stays out of its environment."""
    communicator = state.saved(name)["roles"]["communicator"]
    env = {state.HANDLER_ENV: f"{name}/{handler}", "CCI_PARENT_SESSION": communicator["session"], state.DRIVE_ENV: name}
    settings = session_settings(env)
    argv = ["claude", "--bg", "--agent", HANDLER, "--permission-mode", "bypassPermissions", "--settings", json.dumps(settings), "-n", f"{name} handler {handler}", prompt]
    reads.run(argv, Path(communicator["cwd"]), {key: value for key, value in os.environ.items() if key != "CLAUDE_LONG_RUNNING_LANE"} | settings["env"])


def session_settings(env: dict[str, str]) -> dict:
    """What a session the drive starts runs on: every Orca lane's env, settings and denied tools from the orca plugin's lib/lane.json, the file orca-worker-launch.sh reads, CLAUDE_LANE_SETTINGS merged over those settings as that launcher merges it, then the drive's own SESSION_SETTINGS and env on top, its plugins over the lane's plugin by plugin, so the Datadog plugin, whose MCP is a drive session's one Datadog read, stays on whatever CLAUDE_LANE_SETTINGS turns off."""
    lane = lane_spec()
    settings = lane["settings"] | json.loads(os.environ.get("CLAUDE_LANE_SETTINGS") or "{}")
    denied = list(dict.fromkeys([*lane["deniedTools"], *SESSION_SETTINGS["permissions"]["deny"]]))
    plugins = settings.get("enabledPlugins", {}) | SESSION_SETTINGS["enabledPlugins"]
    return settings | SESSION_SETTINGS | {"env": lane["env"] | SESSION_SETTINGS["env"] | env, "permissions": {"deny": denied}, "enabledPlugins": plugins}


def lane_spec() -> dict:
    """The newest installed orca plugin's lib/lane.json: the env, settings and denied tools every Orca lane starts with."""
    return json.loads((Path(install.installed(ORCA)["installPath"]) / "lib" / "lane.json").read_text())


def env_words(settings: dict) -> list[str]:
    """The `env` words a root session's command starts with: every variable its settings carry, so the `claude daemon` its `claude --bg` starts, and each spare that daemon forks, inherits them."""
    return ["env", *(f"{key}={value}" for key, value in settings["env"].items())]


def filled(page: str) -> str:
    """A skill page with each `orca terminal create` and `cci session register` line's `<session env>` and `<session settings>` filled from session_settings, quoted for the double-quoted `--command` they sit in: `env` with every variable a session the drive starts carries, so the `claude daemon` its `claude --bg` starts inherits them too, and the one `--settings` value it runs on."""
    settings = session_settings({})
    holes = {
        SESSION_ENV: shlex.join(env_words(settings)),
        SESSION_SETTINGS_HOLE: shlex.quote(json.dumps(settings, separators=(",", ":"))),
    }
    quoted = {hole: DOUBLE_QUOTED.sub(r"\\\g<0>", words) for hole, words in holes.items()}

    def fill(line: str) -> str:
        if not line.lstrip().startswith(("orca terminal create", "cci session register")):
            return line
        for hole, words in quoted.items():
            line = line.replace(hole, words)
        return line

    return "\n".join(fill(line) for line in page.split("\n"))


def launch(name: str, role: str, project: str | None, worktree: str) -> list[str]:
    """The `cci session register` line of Start or resume that starts the role's session, or a project's sub-coordinator's, under its alias in the worktree, and the `orca terminal create` line that shows it in Orca, as `drive manual start` fills them."""
    lines = filled(START_PAGE.read_text()).splitlines()
    register = next(line for line in lines if line.startswith(f"cci session register <drive>-{'<lane>' if project else role} "))
    show = next(line for line in lines if line.startswith("orca terminal create --worktree path:<worktree> --title <alias> "))
    named = {"<alias>": state.alias(name, role, project), "<drive>": name, "<worktree>": shlex.quote(worktree), "<lane>": state.project_lane(project or ""), "<project>": project or ""}
    for hole, value in named.items():
        register, show = register.replace(hole, value), show.replace(hole, value)
    return [register, show]


def register(name: str, role: str, project: str | None, worktree: str, session: str) -> str:
    """Register the session with cci as the role's, or a project's sub-coordinator's, by its line in Start or resume naming the session in place of `--new`, so its alias owns its cci subscription and cci session run keeps it running in tmux once it exits; cci's line saying where it runs."""
    words = shlex.split(launch(name, role, project, worktree)[0])
    words[words.index("--new") : words.index("--new") + 1] = ["--session", session]
    try:
        return reads.run(words, Path(worktree)).strip()
    except reads.ReadError as error:
        raise state.DriveError(f"cci did not register session {session} as the {role}'s, so nothing keeps it running or hearing its records: {error}") from None


def bind_handler(payload: dict, handler: str) -> None:
    """Bind a handler's session to the drive the moment it starts, before its first step, so every check on the communicator's handlers finds it."""
    name, _, handler_id = handler.partition("/")
    with state.edit(name) as data:
        data["handlers"][handler_id].update(session=payload["session_id"], transcript=payload["transcript_path"])


def handed(briefs: list[str]) -> str:
    """What a handler reads of the briefs drive handler handed it while it worked: each record in the thread or on the record it is at work on, to cover in its own post."""
    return (
        f"drive handler handed you {len(briefs)} more {'brief' if len(briefs) == 1 else 'briefs'} in the thread or on the record you are at work on,"
        f" since a thread has one handler at a time: {' | '.join(briefs)}. Read each record as your brief says and cover it"
        " in the post you are writing; once you have posted, post one more line in his thread with only what your posts left out,"
        " or nothing when they already said it. Your last line names what you did for each."
    )


def take_handed(name: str, handler: str) -> list[str]:
    """The briefs drive handler handed the handler since its last step, moved to the ones it took so each reaches it once and a restart hands them again."""
    if not state.saved(name)["handlers"][handler]["handed"]:
        return []
    with state.edit(name) as data:
        entry = data["handlers"][handler]
        briefs, entry["handed"] = entry["handed"], []
        entry["took"] += briefs
    return briefs


def end_handler(payload: dict, refused: Callable[[dict], str | None]) -> str | None:
    """End a handler's turn once refused, the Stop hook's checks of its line, lets it: hand the line it ended on to the communicator as one cci record each post's POSTED, EDITED and CARD links in it say's own result, whole with its thread_ts, in place of any the handler typed, and remove its session. A job the handler held keeps its transcript's path, since the drive forgets the handler. A handler drive handler handed briefs since its last step does not end: they come back as the reason its stop is refused, taken in the one edit that would forget it, so no brief lands on a handler that has ended. A drive a newer install saved, which this install may neither check nor write, still ends the handler on its line with the posts its saved bindings hold, its entry left to drive handler, which takes it off with each brief it was handed once its turn is over, and a check that raises still posts that line before its error goes on."""
    name = state.session_drives(payload["session_id"])[0]
    try:
        if reason := refused(payload):
            return reason
        with state.edit(name) as data:
            ended_handler = data["handlers"][payload["agent_id"]]
            if briefs := ended_handler["handed"]:
                ended_handler["handed"] = []
                ended_handler["took"] += briefs
            else:
                data["handlers"].pop(payload["agent_id"])
                if job_id := state.agent_job(data, payload["agent_id"]):
                    data["jobs"][job_id]["transcript"] = payload["agent_transcript_path"]
        if briefs:
            return Unjudged(f"Refused on purpose: {handed(briefs)}")
    except state.NewerDrive:
        ended_handler = state.saved(name)["handlers"][payload["agent_id"]]
    except Exception:
        post_done(name, payload, state.saved(name)["handlers"][payload["agent_id"]])
        raise
    post_done(name, payload, ended_handler)


def post_done(name: str, payload: dict, ended_handler: dict) -> None:
    line = (payload.get("last_assistant_message") or "").strip()
    if posted := ended_handler["posted"]:
        line = "; ".join(filter(None, [*posted, RETYPED.sub("", line).strip(" ;")]))
    reads.run(["cci", "post", "--drive", name, "--lane", "handler", "--kind", "done", "--to", f"communicator@{name}", "--id", f"handler-{payload['agent_id']}", "--text", line], Path(payload["cwd"]))
    remove_session(payload)


def resume(payload: dict) -> str | None:
    names = state.session_drives(payload["session_id"])
    if not names and not os.environ.get(state.REMOTE_LANE_ENV):
        return None
    transcript = Path(payload["transcript_path"])
    rows = tail(transcript)
    calm = max((at for at, row in enumerate(rows) if ended(row) and not died(row)), default=-1)
    tries = sum(row["type"] == "user" and RESUMED in str(row["message"]["content"]) for row in rows[calm + 1 :])
    handler = seen(payload)
    if tries >= len(OUTAGE_WAITS):
        if "handler_session" in handler:
            restart_handler(names[0], handler)
        return None
    since = datetime.now(UTC)
    if "handler_session" in handler:
        with state.edit(names[0]) as data:
            data["handlers"][handler["agent_id"]]["session"] = None
    time.sleep(OUTAGE_WAITS[tries] * 60)
    if "handler_session" in handler:
        bind_handler(payload, f"{names[0]}/{handler['agent_id']}")
    if any(state.parse_time(row["timestamp"]) > since for row in tail(transcript) if row["type"] in ("user", "assistant")):
        return None
    return RESUMED


def restart_handler(name: str, payload: dict) -> None:
    """Start a handler whose turn an API error ended again once its four resumes met the error too, as the same handler on the brief its transcript opens with, kept for `drive handler-start`, so what it holds, its job, each post it made and each brief it was handed stay with it, those it took handed again, until a turn that answers posts its done; it reads as not started until its new session binds, so no brief meanwhile takes what it holds. A restart the error meets again resumes and starts again the same way."""
    kept = brief_path(name, payload["agent_id"])
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(opening(Path(payload["agent_transcript_path"])))
    with state.edit(name) as data:
        entry = data["handlers"][payload["agent_id"]]
        entry.update(session=None, handed=[*entry["took"], *entry["handed"]], took=[])
    state.detach(name, ["handler-start", payload["agent_id"]], "handlers.log")
    remove_session(payload)


def remove_session(payload: dict) -> None:
    """Remove a handler's ended session, which would otherwise sit idle holding its memory: `claude rm` takes the id of the background job Claude Code keeps for it."""
    own, _ = background_job(payload["handler_session"])
    subprocess.Popen(["claude", "rm", own], cwd=Path(payload["cwd"]), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def ended(row: dict) -> bool:
    """Whether a transcript row is a response's last: the model stopped rather than for a tool call or a paused server tool, so the session's turn is over and its next wake starts another."""
    return row.get("type") == "assistant" and row["message"].get("stop_reason") not in (None, "tool_use", "pause_turn")


def died(row: dict) -> bool:
    """Whether a transcript row is a response that ended on an API error or a refusal, the row Claude Code writes itself under model `<synthetic>` or one stopped for any reason but the end of its turn, so the agent's turn ended without its work and a SendMessage meets the same stop."""
    return ended(row) and (row["message"].get("stop_reason") != "end_turn" or row["message"].get("model") == SYNTHETIC)


def brief(payload: dict) -> str:
    """The prompt that started whoever made the call that fired a hook, an agent of the session or an Orca lane's session itself, the brief file it names and the cci record its Record: line names read in."""
    if not (path := transcript_of(payload)).is_file():
        return ""
    prompt = carried(opening(path))
    return f"{prompt}\n{record(named[1])}" if (named := RECORD.search(prompt)) else prompt


def opening(transcript: Path) -> str:
    """The prompt a session's transcript opens with, the brief it started on."""
    with transcript.open() as handle:
        content = next(row for line in handle if (row := json.loads(line)).get("type") == "user")["message"]["content"]
    return content if isinstance(content, str) else " ".join(part.get("text", "") for part in content)


def record(seq: str) -> str:
    """A cci record's whole text, its body file read in."""
    return reads.run(["cci", "show", seq], Path.cwd())


def takes(name: str, job_id: str, payload: dict) -> bool:
    """Whether the job a brief names may take the agent whose step fired the hook, as hold decides it, read from that job's row and the drive's bindings alone, so an agent's every later step reads no whole drive; true for a row an older install saved without a field free reads, which only the whole drive's shaping fills in."""
    if (job := state.saved_job(name, job_id)) is None:
        return False
    if not {"state", "agent", "where"} <= job.keys():
        return True
    return free({"jobs": {job_id: job}} | state.saved(name), job_id, payload["session_id"], payload["agent_id"]) and payload.get("agent_type") in state.JOB_AGENTS[job["kind"]]


def free(data: dict, job_id: str, session: str, agent: str) -> bool:
    """Whether an agent of the session at work on the job takes it: the job exists, it is not the agent a stop left on the job, which keeps the stop, no Orca lane holds it by its dispatch, which drive job resume restarts once that lane dies, and no agent runs it already but one that ended with its session, which no role holds any more, or one drive job resume handed over to this session."""
    job = data["jobs"].get(job_id)
    if not job or (job["state"] == "stopped" and job["agent"] == agent) or (job["where"] == "remote" and job.get("dispatch")):
        return False
    return job.get("handed_to") == session or not (job["state"] == "running" and job["agent"] and not state.agent_gone(data, job))


def woken(payload: dict, job: dict) -> datetime | None:
    """When a prompt, such as its brief or a SendMessage, first reached the agent whose step fired a hook after the job's return, to the second the drive stamps, so a call an agent makes after its return, or a Stop hook's feedback on it, leaves that return standing; None when none did. Claude Code writes a SendMessage it delivers to an agent as a meta row of origin coordinator, and its other meta rows, such as a Stop hook's feedback or a skill's body, with no origin."""
    returned = state.parse_time(job["returned"])
    with transcript(payload).open() as handle:
        rows = [row for line in handle if (row := json.loads(line)).get("type") == "user" and row.get("timestamp") and (not row.get("isMeta") or (row.get("origin") or {}).get("kind") == MESSAGE_ORIGIN)]
    prompts = (row for row in rows if isinstance(content := row["message"]["content"], str) or any(part.get("type") == "text" for part in content))
    return next((moment for row in prompts if (moment := state.parse_time(row["timestamp"]).replace(microsecond=0)) > returned), None)


def hold(payload: dict) -> None:
    """Record on the job its brief's Drive line names, else the job already recorded with this agent, the agent whose step fired this hook, and run the job, stamped resumed at the prompt that woke its agent after its return: a session runs the newest copy of each hook it registered at start but no hook registered since, so the drive hears of an agent from the hooks its own steps fire, its first step and its first after each wake. Only an agent of a type the job's kind runs as is held, so a handler working one item for the communicator, an orca-doctor reading a lane's brief or an Explore agent whose prompt names a done job never takes a job its brief names, by a `Job:` line, a brief file or the record it reads in."""
    if not payload.get("agent_id") or not any(payload.get("agent_type") in kinds for kinds in state.JOB_AGENTS.values()):
        return
    names = state.session_drives(payload["session_id"])
    if not names:
        return
    prompt = brief(payload)
    match = BRIEF_JOB.search(prompt)
    if match and not takes(names[0], match[1], payload):
        return
    known = state.load(names[0])
    job_id = match[1] if match else state.agent_job(known, payload["agent_id"])
    if not job_id or not free(known, job_id, payload["session_id"], payload["agent_id"]) or payload.get("agent_type") not in state.JOB_AGENTS[known["jobs"][job_id]["kind"]]:
        return
    returned = known["jobs"][job_id]["state"] != "running" and bool(known["jobs"][job_id]["returned"])
    if returned and not (resumed := woken(payload, known["jobs"][job_id])):
        return
    with state.edit(names[0]) as data:
        if free(data, job_id, payload["session_id"], payload["agent_id"]):
            job = data["jobs"][job_id]
            job.update(agent=payload["agent_id"], where="local", session=payload["session_id"], handed_to=None, undelivered=[])
            if returned:
                job["resumed"] = state.stamp(resumed.astimezone())
            state.start(job)
            superseded.hear(data, job_id, prompt)
