import json
import os
import re
import subprocess
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self

from drive import limits, reads, render, state
from drive.brief import BRIEF_JOB, carried, heavier

RECORD = re.compile(r"^Record: (\d+)\b", re.MULTILINE)
LINK = re.compile(r"^Link:\s*(\S+)", re.MULTILINE)
THREAD = re.compile(r"^Thread:\s*\S+\s+(\d+\.\d+)", re.MULTILINE)
RETYPED = re.compile(r"\b(?:POSTED|EDITED|CARD)(?:\s+https://\S+)+\s*;?\s*")
HIS_KINDS = ("owner", "answer")
RE_HOPS = 8
CORRECTION_BYTES = 1 << 20
HANDLER = "long-running:handler"
DESIGNER = "long-running:designer"
HIS_WORDS = "Message (his words)"
SCRATCH = re.compile(r"Scratch:\s*(\S+)")
LABEL = re.compile(rf"^({'|'.join(re.escape(label) for label in render.HANDLER_LABELS)}):")
SESSION_SETTINGS = {
    "bashOutputMaxChars": 6000,
    "outputStyle": "default",
    "env": {"CLAUDE_CODE_SILENT_TURN_REMINDER": "0"},
    "permissions": {"deny": ["AskUserQuestion"]},
}
STUCK = re.compile(r"\b(j\d+)\b[^.;\n]*?\b(?:stuck|silent|stalled|hung)\b", re.IGNORECASE)
READ_WINDOW = timedelta(minutes=10)
TRANSCRIPT_READ = ("cc-transcript", ".jsonl")
UNREAD_JOB = (
    "refused: this line calls {jobs} stuck or silent, and your transcript shows no read of {its} transcript in the last 10"
    " minutes; read it with `drive job replies <j>` or `cc-transcript`, then quote what it shows"
)
TAIL_BYTES = 4 << 20
TYPED_TIME_RULE = (
    "A clock time a session typed for work already done, such as when a job started or a post went out, which the drive's"
    " stamps give, and a job's or record's stamp given as when a release, build or deploy started; a promise of a time to"
    " come, a time quoted as a tool printed it and a linked time pass, a release's, build's or deploy's start only on the line"
    " linking the build or tool it was quoted from."
)


class Ruled(str):
    """A refusal Jev judges by the one sentence naming what its check protects against, not by its own words, which name the fix."""

    rule: str

    def __new__(cls, reason: str, rule: str) -> Self:
        made = super().__new__(cls, reason)
        made.rule = rule
        return made


class Unjudged(str):
    """A refusal its check decides by the call's shape alone, which Jev never weighs, so the same call gets the same verdict every time."""


def ruled(reason: str) -> dict:
    """The fields the capt-hook pack reads off a refusal: the rule Jev judges it by, or that no Jev judges it."""
    if isinstance(reason, Unjudged):
        return {"judged": False}
    return {"rule": reason.rule} if isinstance(reason, Ruled) else {}


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
        elif line.strip() and not (label == HIS_WORDS or (label == "Context" and (context := context + 1) <= 3)):
            return line.strip()
    return None


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
    """What of his a handler's brief works on: the message of his it names, by the Link of his words or for a record by the message it carries or answers, the thread that message is in, and the record it reads in."""
    if re.search(rf"^{re.escape(HIS_WORDS)}:", prompt, re.MULTILINE):
        link = linked[1] if (linked := LINK.search(prompt)) else None
        found = state.CARD_LINK.search(link or "")
        message = f"{found[2]}.{found[3]}" if found else None
        thread = (found and found[4]) or (named[1] if (named := THREAD.search(prompt)) else message)
        return {"message": message, "link": link, "thread": thread, "record": None}
    if named := RECORD.search(prompt):
        return answered(named[1]) | {"record": named[1]}
    return {"message": None, "link": None, "thread": None, "record": None}


def answered(seq: str) -> dict:
    """The message of his a record carries or answers: an owner or answer record's own permalink, else that of the record its re names, followed up the chain; the record's thread either way."""
    record = shown(seq)
    thread = record["fields"].get("attrs", {}).get("thread")
    for _ in range(RE_HOPS):
        if record["kind"] in HIS_KINDS and (found := state.CARD_LINK.search((record.get("refs") or {}).get("url") or "")):
            return {"message": f"{found[2]}.{found[3]}", "link": record["refs"]["url"], "thread": thread or found[4] or f"{found[2]}.{found[3]}"}
        if not record.get("re"):
            break
        record = shown(str(record["re"]))
    return {"message": None, "link": None, "thread": thread}


def asked_thread(seqs: Iterable[str]) -> tuple[str, str] | None:
    """The channel and thread of his message that the records seqs name carry or answer, read up each one's re chain, from the first that leads to one."""
    return next((state.link_thread(link) for seq in seqs if (link := answered(seq)["link"])), None)


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


def holder(data: dict, held: dict) -> str | None:
    """The handler already at work on what a new brief holds: the same record, the same message of his, or, for a record naming no message of his, one at work on his message in the record's thread, so one message of his has one handler at a time whichever record names it."""
    for handler_id, entry in data["handlers"].items():
        other = entry["holds"] or {}
        if held["record"] and held["record"] == other.get("record"):
            return handler_id
        if held["message"] and held["message"] == other.get("message"):
            return handler_id
        if held["record"] and not held["message"] and held["thread"] and other.get("message") and other.get("thread") == held["thread"]:
            return handler_id
    return None


def gone(session: str | None) -> bool:
    """Whether a bound handler's session is no longer one `claude agents` lists, as when it died before its Stop hook ended it."""
    return bool(session) and session not in {row.get("sessionId") for row in json.loads(reads.run(["claude", "agents", "--json"], Path.cwd()))}


class Busy(state.DriveError):
    """A live handler holds what a brief holds, so no second one starts: one at work on another record takes the brief when it ends."""


def handle(name: str, brief: str) -> str:
    """Start a handler on its brief, unless a live one already holds what the brief holds, since one message of his has one handler at a time: a refused brief for another record is kept for that handler's done record to name; the started handler's id."""
    held_now = holds(brief)
    data = state.load(name)
    if (held := holder(data, held_now)) and gone(data["handlers"][held]["session"]):
        with state.edit(name) as data:
            data["handlers"].pop(held, None)
    handler_id = uuid.uuid4().hex[:8]
    with state.edit(name) as data:
        if held := holder(data, held_now):
            entry = data["handlers"][held]
            his = entry["holds"]["link"] or f"record {entry['holds']['record']}"
            kept = held_now["record"] and held_now["record"] != entry["holds"]["record"]
            if kept and brief.strip() not in entry["held_back"]:
                entry["held_back"].append(brief.strip())
        else:
            data["handlers"][handler_id] = {"session": None, "started": state.stamp(), "holds": held_now, "held_back": [], "posted": []}
    if held:
        then = f"its done record names this brief, to start then and post only what {held} left out" if kept else "this copy needs no handler of its own"
        raise Busy(f"refused, no handler started: handler {held} is at work on {his}, and one message of his has one handler at a time; {then}")
    if folder := scratch(brief):
        folder.mkdir(parents=True, exist_ok=True)
    try:
        start_handler(name, handler_id, brief)
    except reads.ReadError as error:
        with state.edit(name) as data:
            del data["handlers"][handler_id]
        raise state.DriveError(f"claude --bg started no handler: {error}") from None
    return handler_id


def start_handler(name: str, handler: str, prompt: str) -> None:
    """Start a handler as its own `claude --bg` session on its agent definition, which sets its model, in the communicator's checkout, whether the communicator or the drive's watch starts it, its SessionStart hook binding it to the drive by the id in its environment, `CCI_PARENT_SESSION` naming the communicator's session, so cci binds the handler's session to no lane and it posts as the communicator's lanes, and `CLAUDE_LONG_RUNNING_DRIVE` naming the drive, so each cci call that names no drive resolves this one; the watch's own lane stays out of its environment."""
    communicator = state.saved(name)["roles"]["communicator"]
    env = {state.HANDLER_ENV: f"{name}/{handler}", "CCI_PARENT_SESSION": communicator["session"], state.DRIVE_ENV: name}
    settings = SESSION_SETTINGS | {"env": SESSION_SETTINGS["env"] | env}
    argv = ["claude", "--bg", "--agent", HANDLER, "--permission-mode", "bypassPermissions", "--settings", json.dumps(settings), "-n", f"{name} handler {handler}", prompt]
    reads.run(argv, Path(communicator["cwd"]), {key: value for key, value in os.environ.items() if key != "CLAUDE_LONG_RUNNING_LANE"})


def bind_handler(payload: dict, handler: str) -> None:
    """Bind a handler's session to the drive the moment it starts, before its first step, so every check on the communicator's handlers finds it."""
    name, _, handler_id = handler.partition("/")
    with state.edit(name) as data:
        data["handlers"][handler_id].update(session=payload["session_id"], transcript=payload["transcript_path"])


def end_handler(payload: dict) -> None:
    """Hand the line a handler ended on to the communicator as one cci record, each post's POSTED, EDITED and CARD links in it say's own result, whole with its thread_ts, in place of any the handler typed, naming each brief drive handler held back while it worked on his message, and remove its session, which would otherwise sit idle holding its memory: `claude rm` takes the short id `claude agents` lists for it. A job the handler held keeps its transcript's path, since the drive forgets the handler."""
    name = state.session_drives(payload["session_id"])[0]
    with state.edit(name) as data:
        ended_handler = data["handlers"].pop(payload["agent_id"])
        if job_id := state.agent_job(data, payload["agent_id"]):
            data["jobs"][job_id]["transcript"] = payload["agent_transcript_path"]
    line = (payload.get("last_assistant_message") or "").strip()
    if posted := ended_handler["posted"]:
        line = "; ".join(filter(None, [*posted, RETYPED.sub("", line).strip(" ;")]))
    if held_back := ended_handler["held_back"]:
        line += f" | held back for this handler, start each now: {' | '.join(held_back)}"
    cwd = Path(payload["cwd"])
    reads.run(["cci", "post", "--drive", name, "--lane", "handler", "--kind", "done", "--to", f"communicator@{name}", "--id", f"handler-{payload['agent_id']}", "--text", line], cwd)
    listed = json.loads(reads.run(["claude", "agents", "--json"], cwd))
    own = next(row["id"] for row in listed if row.get("sessionId") == payload["handler_session"])
    subprocess.Popen(["claude", "rm", own], cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def ended(row: dict) -> bool:
    """Whether a transcript row is a response's last: the model stopped on its own rather than for a tool call, so the session's turn is over and its next wake starts another."""
    return row.get("type") == "assistant" and row["message"].get("stop_reason") == "end_turn"


def brief(payload: dict) -> str:
    """The prompt the session started the agent whose step fired a hook with, the brief file it names and the cci record its Record: line names read in; empty for the session's own steps."""
    if not payload.get("agent_id") or not (path := transcript(payload)).is_file():
        return ""
    with path.open() as handle:
        content = next(row for line in handle if (row := json.loads(line)).get("type") == "user")["message"]["content"]
    prompt = carried(content if isinstance(content, str) else " ".join(part.get("text", "") for part in content))
    return f"{prompt}\n{record(named[1])}" if (named := RECORD.search(prompt)) else prompt


def record(seq: str) -> str:
    """A cci record's whole text, its body file read in."""
    return reads.run(["cci", "show", seq], Path.cwd())


def free(data: dict, job_id: str, session: str) -> bool:
    """Whether an agent of the session at work on the job takes it: the job exists, nobody stopped it, no Orca lane holds it by its dispatch, which drive job resume restarts once that lane dies, and no agent runs it already but one that ended with its session, which no role holds any more, or one drive job resume handed over to this session."""
    job = data["jobs"].get(job_id)
    if not job or job["state"] == "stopped" or (job["where"] == "remote" and job.get("dispatch")):
        return False
    return job.get("handed_to") == session or not (job["state"] == "running" and job["agent"] and not state.agent_gone(data, job))


def woken(payload: dict, job: dict) -> bool:
    """Whether the agent whose step fired a hook runs the job: it runs already, no return set its state, or a prompt such as its brief or a SendMessage reached the agent after that return, to the second the drive stamps, so a call an agent makes after its return leaves that return standing."""
    if job["state"] == "running" or not job["returned"]:
        return True
    returned = state.parse_time(job["returned"])
    with transcript(payload).open() as handle:
        rows = [row for line in handle if (row := json.loads(line)).get("type") == "user" and row.get("timestamp") and not row.get("isMeta")]
    prompts = (row for row in rows if isinstance(content := row["message"]["content"], str) or any(part.get("type") == "text" for part in content))
    return any(state.parse_time(row["timestamp"]).replace(microsecond=0) > returned for row in prompts)


def hold(payload: dict) -> None:
    """Record on the job its brief's Drive line names, else the job already recorded with this agent, the agent whose step fired this hook, and run the job: a session runs the newest copy of each hook it registered at start but no hook registered since, so the drive hears of an agent from the hooks its own steps fire, its first step and its first after each wake. Only an agent of a type the job's kind runs as is held, so a handler working one item for the communicator, an orca-doctor reading a lane's brief or an Explore agent whose prompt names a done job never takes a job its brief names, by a `Job:` line, a brief file or the record it reads in."""
    if not payload.get("agent_id"):
        return
    names = state.session_drives(payload["session_id"])
    if not names:
        return
    known = state.load(names[0])
    match = BRIEF_JOB.search(brief(payload))
    job_id = match[1] if match else state.agent_job(known, payload["agent_id"])
    if not job_id or not free(known, job_id, payload["session_id"]) or payload.get("agent_type") not in state.JOB_AGENTS[known["jobs"][job_id]["kind"]] or not woken(payload, known["jobs"][job_id]):
        return
    with state.edit(names[0]) as data:
        if free(data, job_id, payload["session_id"]):
            data["jobs"][job_id].update(
                agent=payload["agent_id"], where="local", session=payload["session_id"], handed_to=None, undelivered=[], state="running", updated=state.stamp()
            )
