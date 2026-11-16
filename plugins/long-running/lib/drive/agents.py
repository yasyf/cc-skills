import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn, Self

from drive import install, limits, procs, reads, render, state, superseded, view
from drive.brief import BRIEF_JOB, carried, heavier

HIS_KINDS = ("owner", "answer")
RECORD = re.compile(r"^Record: (\d+)\b", re.MULTILINE)
LINK = re.compile(r"^Link:\s*(\S+)", re.MULTILINE)
THREAD = re.compile(r"^Thread:[ \t]*(?:\S+[ \t]+)?(\d+\.\d+)", re.MULTILINE)
RE_HOPS = 8
SETTLE_WINDOW = timedelta(seconds=30)
LANE_WATCH = "watch"
AMENDING = ("correction", "withdraw")
AMENDMENT_BYTES = 1 << 20
TRUNK_DRIVE = "trunk"
TRUNK_FAILED = "failed in Trunk's merge queue"
SYNTHETIC = "<synthetic>"
OUTAGE_WAITS = (1, 2, 4, 8)
RATE_LIMITED = "would exceed your account's rate limit"
START_WINDOW = timedelta(minutes=5)
REMOVE = ["sh", "-c", 'sleep 2; claude stop "$1"; claude rm "$1"', "remove"]
STOP = ["sh", "-c", 'sleep 2; claude stop "$1"', "stop"]
EFFORT_CEILING = "high"
ABOVE_CEILING = ("xhigh", "max")
SKILL_NAME = re.compile(r"^name:[ \t]*(\S+)", re.MULTILINE)
COMPACT_ENV = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
ROOT_WINDOW = "400000"
COMMUNICATOR_WINDOW = "300000"
ADVISOR_OFF = {"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}
LANE_RULES_ARGS = ("--add-dir", str(Path(__file__).resolve().parents[2] / "lane-rules"))
LANE_RULES_ENV = {"CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD": "1"}
LANE_RULES_HOLE = "<lane rules>"
TOP_SEATS = ("coordinator", "communicator", "meta")
SEAT = re.compile(r"^cci session register <drive>-(\S+) ")
RESUMED = "Resumed: an API error ended your last turn before it was done, so do now what the prompt it answered asked, from where it stopped."
MESSAGE_ORIGIN = "coordinator"
DESIGNER = "long-running:designer"
BUILDER = "long-running:builder"
WATCHDOG = "long-running:watchdog"
WATCHDOG_RUNS = (
    ("drive", "status"),
    ("drive", "job", "replies"),
    ("cc-transcript",),
    ("cci", "grep"),
    ("gh", "pr", "view"),
    ("gh", "pr", "checks"),
    ("gh", "run", "view"),
    ("ci", "bk", "log"),
    ("head",),
    ("tail",),
    ("grep",),
    ("jq",),
    ("gh", "run", "rerun"),
    ("ci", "bk", "rerun"),
    ("drive", "job", "wake"),
)
WATCHDOG_USD = "2"
HIS_WORDS = "Message (his words)"
SCRATCH = re.compile(r"Scratch:\s*(\S+)")
DATADOG_PLUGIN = "datadog@claude-plugins-official"
SESSION_SETTINGS = {
    "bashOutputMaxChars": 6000,
    "outputStyle": "default",
    "disabledMcpjsonServers": ["slack"],
    "env": {"CLAUDE_CODE_SILENT_TURN_REMINDER": "0"},
    "permissions": {"deny": ["AskUserQuestion"]},
    "enabledPlugins": {DATADOG_PLUGIN: True},
}
ORCA = "orca@skills"
START_PAGE = Path(__file__).resolve().parents[2] / "skills" / "long-running" / "reference" / "start.md"
SESSION_ENV = "<session env>"
SESSION_SETTINGS_HOLE = "<session settings>"
ADVISOR_FLAG = "--advisor"
LEAF_ENV = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
DRIVE_PLUGINS = ("long-running@skills", *(f"{needed['name']}@{needed['marketplace']}" for needed in json.loads((Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json").read_text())["dependencies"]))
GUARD_PLUGINS = ("cc-guides@cc-guides", *DRIVE_PLUGINS)
CODE_PLUGINS = (*GUARD_PLUGINS, "cc-context@cc-context", "open-pr@skills")
ROLE_PLUGINS = {WATCHDOG: GUARD_PLUGINS}
READ_ROLES = (WATCHDOG,)
STRICT_MCP = ("--strict-mcp-config",)
ADVISOR_PROBE_SECONDS = 60
DOUBLE_QUOTED = re.compile(r'[\\"$`]')
STUCK = re.compile(r"\b(j\d+)\b[^.;\n]*?\b(?:stuck|silent|stalled|hung)\b", re.IGNORECASE)
UNOWNED = re.compile(
    r"\b(?:no ?one|nobody|no (?:job|builder|agent|lane))(?:\s+\w+){0,2}?\s+(?:working|works|picked|taken|took|carries|owns|is on it)\b|\bunowned\b|\bnot (?:been )?picked up\b",
    re.IGNORECASE,
)
JOB_WORD = re.compile(r"\bj\d+\b")
PROMISED_CARD = re.compile(
    r"\b(?:cards?|buttons?)\b(?:\s+[\w-]+){0,5}?\s+(?:(?:is|are)\s+)?(?:coming|on (?:its|the) way|follows|will (?:follow|come|go up|be drawn))\b"
    r"|\b(?:will|['’]ll|going to|about to)\s+(?:\w+\s+)?(?:draw|send|post|put up|add|give)\s+(?:[\w'’-]+\s+){0,4}?(?:cards?|buttons?)\b"
    r"|\b(?:is|are|am|['’]m|['’]re)\s+(?:now\s+)?(?:drawing|sending|posting|putting up|adding|giving)\s+(?:[\w'’-]+\s+){0,4}?(?:cards?|buttons?)\b"
    r"|\b(?:you(?:['’]ll| will) (?:get|see)|expect)\s+(?:[\w'’-]+\s+){0,3}?(?:cards?|buttons?)\b",
    re.IGNORECASE,
)
DENIED = re.compile(r"\b(?:no|not|never|nor|without)\b|n['’]t\b", re.IGNORECASE)
CLAUSE_END = re.compile(r"[,;:.!?](?=\s|$)")
UNPROMISED = (
    'refused: "{line}" tells him a card is coming and names no open card of his as {{card:<waits id>}}; a card not drawn yet may never'
    " come, as when an answer of his already settled its question, so draw it first, with `drive job ask` or a post's tap_prs, and name"
    ' it on that line, or drop the promise, by his Nov 15 9:06 AM words "you\'re saying in threads you\'re giving me approve buttons and then'
    ' there\'s no such thing"'
)
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
RECENT_BYTES = 65536
TYPED_TIME_RULE = (
    "A clock time a session typed for work already done in words he reads, a post, a card or a record posted to him,"
    " such as when a job started or a post went out, which the drive's stamps give, and a job's or record's stamp given as when a release, build or deploy started; a time after the write,"
    " on the day written beside it or else the day nearest the write, a time quoted as a tool printed it, a time on a line linking the tool or read that measured it and a linked time pass, a release's, build's or deploy's start only on the line"
    " linking the build or tool it was quoted from."
)
PLAIN_WORDS_RULE = (
    "Write every post for the person reading the thread, in plain words: no internal shorthand such as tracking ids, agent"
    " lane names, raw commit shas or object ULIDs, and code, files, products and alerts by their proper names, each table,"
    " column, function, file, migration or flag with a clause saying what it is the first time it appears. A multi-agent"
    " term such as desk, walker, parity or fence is shorthand, but never inside a function, file, path, product or"
    " alert name or in a code span, and one the reader used in the message the post answers passes in the answer."
)


class Ruled(str):
    """A refusal Jev judges by the one sentence naming what its check protects against, not by its own words, which name the fix; findings each rule beside what its check found under it, one line naming no fix, which Jev weighs one Noul a finding: its own when its check found one thing, and every check's when `every` joins them."""

    rule: str
    findings: list[dict]

    def __new__(cls, reason: str, rule: str, found: str = "", findings: list[dict] | None = None) -> Self:
        made = super().__new__(cls, reason)
        made.rule = rule
        made.findings = findings or [{"rule": rule, "found": found or reason.removeprefix("refused: ")}]
        return made


class Unjudged(str):
    """A refusal Jev never weighs, so the same call gets the same verdict every time: one its check decides by the call's shape alone, or a check that could not run, whose words say the call does not go ahead."""


def ruled(reason: str) -> dict:
    """The fields the capt-hook pack reads off a refusal: the rule Jev judges it by and each rule beside what its check found, or that no Jev judges it."""
    if isinstance(reason, Unjudged):
        return {"judged": False}
    if not isinstance(reason, Ruled):
        return {}
    return {"rule": reason.rule, "findings": reason.findings}


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


def recent_rows(path: Path, since: datetime) -> list[dict]:
    """The transcript's rows written since, read back from its end, since a coordinator's transcript grows past 300 MB; a row still being written is left for the next read."""
    with path.open("rb") as handle:
        end = handle.seek(0, os.SEEK_END)
        span = RECENT_BYTES
        while True:
            start = max(0, end - span)
            handle.seek(start)
            rows = [row for line in handle.read().split(b"\n")[1 if start else 0 : -1] if (row := json.loads(line)).get("timestamp")]
            if not start or (rows and datetime.fromisoformat(rows[0]["timestamp"]) < since):
                return [row for row in rows if datetime.fromisoformat(row["timestamp"]) >= since]
            span *= 2


def parts(row: dict) -> list[dict]:
    content = (row.get("message") or {}).get("content")
    return content if isinstance(content, list) else []


def unread_job(payload: dict, text: str) -> str | None:
    """Why a line calling a job stuck or silent, a report to the communicator or a post to him, waits for a read of that job's transcript: the poster's own calls in the last 10 minutes show none."""
    if not (jobs := unread_jobs(payload, text)):
        return None
    return UNREAD_JOB.format(jobs=", ".join(jobs), its="their" if len(jobs) > 1 else "its")


def unread_jobs(payload: dict, text: str) -> list[str]:
    """Each job a line calls stuck or silent whose transcript the poster's own calls in the last 10 minutes show no read of."""
    if not (named := dict.fromkeys(STUCK.findall(text))):
        return []
    since = datetime.now(UTC) - READ_WINDOW
    calls = " ".join(
        json.dumps(part.get("input"))
        for row in tail(transcript_of(payload))
        if row.get("timestamp") and datetime.fromisoformat(row["timestamp"]) > since
        for part in parts(row)
        if part.get("type") == "tool_use"
    )
    return [job for job in named if f"replies {job}" not in calls and not any(read in calls for read in TRANSCRIPT_READ)]


def unowned_work(text: str) -> Ruled | None:
    """Why a line, a report to the communicator or a post to him, says work has no one on it while it names no job on it now: the session that saw it puts one on it that turn, by his words 94c20c6."""
    if not (found := UNOWNED.search(text)) or JOB_WORD.search(text):
        return None
    return Ruled(
        f'refused: "{found[0]}" says work has no one on it and the line names no job: put one on it now, drive job add for a part no job holds or'
        " drive job resume for the one holding it, and name that job in the line, as {started:<j>} in a post to him",
        UNOWNED_RULE,
        found=f'"{found[0]}" says work has no one on it, and the line names no job',
    )


def unpromised(name: str, text: str) -> Unjudged | None:
    """Why a line, a report to the communicator or a post to him, tells him a card is coming while it names no card of his open now as {card:<waits id>}, with his cards unread counting every card as open: a promised card the drive never draws never comes."""
    for line in text.splitlines():
        if promises_card(limits.REF.sub("it", line)) and not any(view.card_open(state.waits_of(name, ref[2]), view.live_cards(name)) for ref in limits.REF.finditer(line) if ref[1] == "card"):
            return Unjudged(UNPROMISED.format(line=line.strip()))
    return None


def promises_card(line: str) -> bool:
    """Whether the line says a card is coming in a clause that denies none of it, as "no card follows" does."""
    return any(
        not DENIED.search(line, max((end.end() for end in CLAUSE_END.finditer(line, 0, found.start())), default=0), found.end())
        for found in PROMISED_CARD.finditer(line)
    )


def typed_time(text: str, data: dict) -> Ruled | None:
    """Why a session's record or message types a clock time for work already done, which Jev judges by the rule, so a time to come its words date in another form than "tomorrow" or "Nov 9", such as "Friday 6:54 AM", still passes."""
    refusal = limits.typed_time(text, data)
    return refusal and Ruled(f"refused: {refusal}", TYPED_TIME_RULE, found=refusal.found)


def seen(payload: dict) -> dict:
    """A hook's payload as the drive's checks read it: a job's agent `drive job start` started as its own session reads, from the JOB_ENV its launch set, as the agent it was recorded as of the session holding its order then, as an Agent start of that session's would, so every check on a job's agent binds it unchanged, with no read of the drive."""
    if not payload.get("agent_id") and (started := os.environ.get(state.JOB_ENV)):
        _, _, agent, session = started.split("/")
        return payload | {"session_id": session, "agent_id": agent, "agent_transcript_path": payload["transcript_path"], "job_session": payload["session_id"]}
    return payload


def shaped(payload: dict) -> dict:
    """The Agent call a drive session makes, in the one shape the drive runs it: a role's call without `name`, since a named agent joins the agent team and never wakes on events, a design job as the designer, the only definition that keeps it read only and its forks in his cards' shape, and Fable on the designer alone; a lane whose order needs its definition's Opus or Fable without the Sonnet or Haiku passed for it; and any effort above EFFORT_CEILING lowered to it, by his Nov 15 words "nothing should be running at xhigh"."""
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
    if made.get("effort") in ABOVE_CEILING:
        made["effort"] = EFFORT_CEILING
    if job_id and heavier(data, job_id, text, made.get("model")):
        del made["model"]
    return made


def scratch(prompt: str) -> Path | None:
    """The folder a drive agent's brief names as its Scratch, which must exist before the agent's first write."""
    match = SCRATCH.search(prompt)
    return Path(match[1].rstrip(".,;")).expanduser() if match else None


def holds(prompt: str) -> dict:
    """What a handler's brief works on: for a brief naming a Record, whatever its other lines say, that record, its attrs, each record its re chain names, the message of his the chain reaches while the record's post goes in that message's thread, as answered reads it, and the record's thread, else the one its Thread: line names; for any other brief, his words or none, the message of his its Link names and the thread that message is in, else the one its Thread: line names."""
    thread = line[1] if (line := THREAD.search(prompt)) else None
    if named := RECORD.search(prompt):
        held = answered(named[1]) | {"record": named[1]}
        return held | {"thread": held["thread"] or thread}
    link = linked[1] if (linked := LINK.search(prompt)) else None
    found = state.CARD_LINK.search(link or "")
    message = f"{found[2]}.{found[3]}" if found else None
    return {"message": message, "link": link, "thread": (found and found[4]) or thread or message, "record": None, "re": []}


def answered(seq: str) -> dict:
    """The message of his a record carries or answers: an owner or answer record's own permalink, else that of the record its re names, followed up the chain, unless the record's post goes outside that message's thread, in another thread its thread attr names or at the root of a channel outside his DM as rooted reads it, since that post answers no message of his there; the record's thread, its attrs, the record itself as report, for talk.unhandled, and each record the chain names either way."""
    record = report = shown(seq)
    attrs = record["fields"].get("attrs", {})
    thread = attrs.get("thread")
    chain: list[str] = []
    for _ in range(RE_HOPS):
        if record["kind"] in HIS_KINDS and (found := state.CARD_LINK.search((record.get("refs") or {}).get("url") or "")):
            message = f"{found[2]}.{found[3]}"
            if chain and (rooted(attrs) or thread not in (None, found[4] or message)):
                break
            return {"message": message, "link": record["refs"]["url"], "thread": thread or found[4] or message, "re": chain, "attrs": attrs, "report": report}
        if not record.get("re"):
            break
        chain.append(str(record["re"]))
        record = shown(chain[-1])
    return {"message": None, "link": None, "thread": thread, "re": chain, "attrs": attrs, "report": report}


def rooted(attrs: dict) -> bool:
    """Whether a report's post goes at the root of a channel outside his DM, such as a staging claim: its until attr names the end of what that post claims, which say takes only on a post to a channel's members, and no thread attr puts it in that post's thread."""
    return bool(attrs.get("until")) and not attrs.get("thread")


def shown(seq: str) -> dict:
    try:
        return json.loads(reads.run(["cci", "show", seq, "--json"], Path.cwd()).splitlines()[0])
    except reads.ReadError as error:
        raise state.DriveError(f"record {seq} could not be read: {error}") from None


def amendments(name: str, seqs: Iterable[str], lane: str | None = None) -> dict[str, dict[str, list[str]]]:
    """By kind, correction or withdraw, the seqs of the records of that kind cci holds re each of seqs, oldest first, read in one cci grep from the lowest of them, given lane only those that lane posted; a record none answers is left out."""
    found: dict[str, dict[str, list[str]]] = {kind: {} for kind in AMENDING}
    wanted = set(seqs)
    if not wanted:
        return found
    argv = ["cci", "grep", ".", "--drive", name, *(flag for kind in AMENDING for flag in ("--kind", kind)), *(("--lane", lane) if lane else ()), "--since", min(wanted, key=int), "--budget", str(AMENDMENT_BYTES), "--json"]
    try:
        lines = reads.run(argv, Path.cwd()).splitlines()
    except reads.ReadError as error:
        raise state.DriveError(f"the corrections and withdrawals of records {', '.join(sorted(wanted, key=int))} could not be read: {error}") from None
    for record in map(json.loads, reversed(lines)):
        if (answered := str(record.get("re"))) in wanted:
            found[record["kind"]].setdefault(answered, []).append(str(record["seq"]))
    return found


def answering(name: str, seq: str) -> list[dict]:
    """Each record cci holds re seq, oldest first, read in one cci grep from it."""
    argv = ["cci", "grep", seq, "--drive", name, "--since", seq, "--budget", str(AMENDMENT_BYTES), "--json"]
    try:
        lines = reads.run(argv, Path.cwd()).splitlines()
    except reads.ReadError as error:
        raise state.DriveError(f"the records re {seq} could not be read: {error}") from None
    return [record for record in map(json.loads, reversed(lines)) if str(record.get("re")) == seq]


def posts(name: str, since: int) -> tuple[list[tuple[str, datetime]], int]:
    """The seq of each `POST` report or correction to posts@<drive>, where the drive routes every report, or to a communicator's lane, the drive's or a seat's, where a writer still sends one, after since, oldest first, with when cci took it, read in one cci tail a lane, and the newest seq both reads reached, which is short of the drive's newest when a read held more than AMENDMENT_BYTES, since cci then prints the oldest records and the next read goes on from there."""
    found, reached = {}, []
    lanes = (state.POSTS_LANE, *state.communicator_lanes(state.saved(name)))
    for lane in lanes:
        argv = ["cci", "tail", "--drive", name, "--to", lane, "--kind", "report", "--kind", "correction", "--since", f"#{since}", "--budget", str(AMENDMENT_BYTES), "--json"]
        try:
            records = [json.loads(line) for line in reads.run(argv, Path.cwd()).splitlines()]
        except reads.ReadError as error:
            raise state.DriveError(f"the POST reports to {lane}@{name} could not be read: {error}") from None
        found |= {str(record["seq"]): state.parse_time(record["at"]) for record in records if posting(name, record, lanes)}
        reached += [max(record["seq"] for record in records)] if records else []
    return sorted(found.items(), key=lambda item: int(item[0])), min(reached, default=since)


def posting(name: str, record: dict, lanes: tuple[str, ...] = (state.POSTS_LANE, "communicator")) -> bool:
    """Whether a record cci holds is a `POST` report or correction to posts@<drive> or a communicator's lane, the drive's or a seat's."""
    return bool(set(record.get("to", ())) & {*lanes, *(f"{lane}@{name}" for lane in lanes)}) and record["kind"] in ("report", "correction") and record["text"].startswith("POST")


def seeded(name: str) -> int:
    """Where the read of a drive's POST reports starts on a drive that keeps no mark yet: its newest record, so none replays."""
    try:
        newest = reads.run(["cci", "tail", "--drive", name, "--since", "#1", "-n", "1", "--json"], Path.cwd())
    except reads.ReadError as error:
        raise state.DriveError(f"the newest record of {name} could not be read: {error}") from None
    return json.loads(newest)["seq"] if newest.strip() else 0


def handled_post(seq: str) -> bool:
    """Whether a `POST` report goes out: every one its writer sent, since the writer decides from its role's rules when a line is worth his read, by his words "get rid of hard-coded stuff, give it the rules, and let it make the call", but a progress line the watch wrote on no watched thread, which no session judged."""
    read = answered(seq)
    return not (read["report"]["lane"] == LANE_WATCH and read["attrs"].get("progress") == "true" and read["attrs"].get("watch") != "true")


def start_posts(name: str, moment: datetime) -> None:
    """Post each `POST` report handled_post keeps from a process the watch starts, `drive report-post`, by talk.report_post, so his messages never queue behind the communicator posting reports and it hears only of one that process could not post, once, from the drive's mark on, and carry every other as progress, so no report is handed twice and none waits on a post; the watch's own close reports it posts itself, by state.watch_posting, and a report cci took under SETTLE_WINDOW ago waits for the next read, so the watch's save of a close report's seq lands first."""
    data = state.load(name, ("posts",))
    since = seeded(name) if data["posts"]["mark"] is None else data["posts"]["mark"]
    read, newest = posts(name, since)
    if not read:
        def start_posts_write(data: dict):
            data["posts"]["mark"] = newest
            return data

        data = state.update(name, None, start_posts_write)
        return
    data = state.load(name)
    errors, carried, young = [], {}, []
    for seq, at in read:
        if at + SETTLE_WINDOW > moment:
            young.append(int(seq))
            continue
        if seq in data["carried"] or seq in state.watch_posting(data):
            continue
        if not handled_post(seq):
            carried[seq] = "progress: the dashboard and records alone"
            continue
        if answered(seq)["attrs"].get("watch") == "true":
            carried[seq] = "the communicator, by the note on its watched thread"
            continue
        carried[seq] = "the drive"
        try:
            state.detach(name, ["report-post", seq], "posts.log")
        except OSError as error:
            errors.append(f"POST report {seq}'s post did not start: {start_error(error)}")
    def marked(data: dict) -> None:
        for seq, why in carried.items():
            data["carried"].setdefault(seq, why)
        data["posts"]["mark"] = min(young) - 1 if young else newest

    state.update(name, None, marked)
    if errors:
        raise state.DriveError("; ".join(errors))


def trunk_failures(keys: list[str], since: str) -> list[dict]:
    """The records of each failure of keys in Trunk's merge queue since then, newest first, which Trunk's webhook relays into cci's trunk drive, read in one cci grep."""
    pattern = "|".join(f"{re.escape(key)} {TRUNK_FAILED}" for key in keys)
    try:
        lines = reads.run(["cci", "grep", pattern, "--drive", TRUNK_DRIVE, "--kind", "queue", "--since", since, "--json"], Path.cwd()).splitlines()
    except reads.ReadError as error:
        raise state.DriveError(f"Trunk's queue records of {', '.join(keys)} could not be read: {error}") from None
    return [json.loads(line) for line in lines]


def gone(entry: dict) -> bool:
    """Whether the turn of a job's own `claude --bg` session is over, whatever its Stop hook did, read from the records Claude Code keeps of the session its launch named, a background job Claude Code still lists as running counting as over once no running process, by pid and start time, names its session, as j6063's session 429b9597 died with four resumes posted to it unread: no running process's `sessions/<pid>.json`, the record Claude Code keeps of each session's process, names that session with a status but idle, and its background job, which `claude --bg` writes `working` before it returns, ended a turn done, blocked, failed or stopped, rests idle or blocked, as one stuck on a startup dialog does, or `claude rm` took it; so a launch whose job ended blocked before it ran a turn holds nothing. A launch that has not named its session yet is in flight for START_WINDOW, many times the seconds a `claude --bg` launch takes, and past it never started."""
    if not entry["session"]:
        return state.parse_time(entry["started"]) + START_WINDOW <= state.now()
    records = session_records()
    if busy(entry["session"], records):
        return False
    found = background_job(entry["session"])
    return found is None or found[1]["state"] in ("done", "blocked", "failed", "stopped") or found[1].get("tempo") in ("idle", "blocked") or not alive(entry["session"], records)


def busy(session: str, records: list[tuple[int, dict]]) -> bool:
    """Whether a running process's record names the session with a status but idle."""
    return bool(running([(pid, record.get("procStartFt") or record.get("procStart")) for pid, record in records if record.get("sessionId") == session and record.get("status") != "idle"]))


def alive(session: str, records: list[tuple[int, dict]]) -> bool:
    """Whether a running process's record names the session, whatever its status, as a pool handler resting between briefs keeps its process."""
    return bool(running([(pid, record.get("procStartFt") or record.get("procStart")) for pid, record in records if record.get("sessionId") == session]))


def launched(out: str) -> str:
    """The session `claude --bg` started, by the id it prints, `backgrounded · <id> · <name>`, which names the background job Claude Code writes for that session before it returns, never the id of a note before it, as the one a resume of a session still listed prints naming the session it copies."""
    printed = re.search(r"^backgrounded\W+([0-9a-f]{8})\b", out, re.MULTILINE)
    if not (printed and (job := registry_record(install.config_dir() / "jobs" / printed[1] / "state.json"))):
        raise state.DriveError(f"claude --bg named no background job Claude Code keeps: {out.strip() or 'no output'}")
    return job["sessionId"]


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


def background_jobs() -> list[tuple[str, dict]]:
    """Each background job Claude Code keeps in `jobs/<id>/state.json`, its id the folder's name, the one `claude rm` takes, and its state."""
    return [(path.parent.name, kept) for path in (install.config_dir() / "jobs").glob("*/state.json") if (kept := registry_record(path)) is not None]


def background_job(session: str) -> tuple[str, dict] | None:
    """The background job Claude Code keeps for a session."""
    return next(((own, kept) for own, kept in background_jobs() if kept.get("sessionId") == session), None)


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


def handle(name: str, brief: str, lane: str = "communicator") -> tuple[str, bool]:
    """Hand a brief to a communicator, the drive's or the lane of a seat's communicator holding the channel the brief's message is in, which answers him itself, as one cci note from lane watch keyed by the brief so a pass repeating it sends nothing new, by his ruling "get rid of handlers": a `Record:` brief names a record for it to post, and a message of his one it owes an answer."""
    key = hashlib.sha256(brief.strip().encode()).hexdigest()[:16]
    reads.run(["cci", "post", "--drive", name, "--lane", "watch", "--kind", "note", "--to", f"{lane}@{name}", "--id", f"hand-{key}", "--text", f"ANSWER THIS: {brief.strip()}"], Path.cwd(), reads.posting())
    return lane, True


def start_error(error: Exception) -> str:
    """Why a hand-off did not go out: a refusal's or a failed command's own words, else the error as render.raised names one the drive did not expect."""
    return str(error) if isinstance(error, state.DriveError | reads.ReadError) else render.raised(error)


def beside(data: dict) -> tuple[str, dict]:
    """The lane a drive's handlers run beside and end their turn to, and its role: the communicator's, or on a drive whose roles hold none, as one started from a terminal holds none, the coordinator's, so his lines still reach a handler that posts them in his DM."""
    return ("communicator", data["roles"]["communicator"]) if "communicator" in data["roles"] else ("root", data["roles"]["coordinator"])


def light(settings: dict, role: str, checkout: Path) -> dict:
    """The settings of a leaf session the drive starts as role, a builder, reader, designer or watchdog, which runs headless: leaf's, with no auto-memory, LEAF_ENV's headless switches, every skill a user or claude.ai sync installed turned off in the listing, and only the plugins ROLE_PLUGINS names for the role, CODE_PLUGINS for a role it does not name, on, GUARD_PLUGINS holding this plugin and every plugin its manifest depends on, since Claude Code loads no plugin whose dependency is off, so a disabled plugin's skills and hooks stay out while the guards of GUARD_PLUGINS apply, by his Nov 15 words "make them as lightweight as possible"; a watchdog, which reads and answers and writes no code, also drops the checkout's own skills and its root CLAUDE.md and AGENTS.md, since its definition carries what it does."""
    reads = role in READ_ROLES
    roots = (install.config_dir() / "skills", *((checkout / ".claude" / "skills",) if reads else ()))
    names = {found[1] if (found := SKILL_NAME.search(path.read_text())) else path.parent.name for root in roots for path in [*root.glob("*/SKILL.md"), *root.glob("synced/*/*/SKILL.md")]}
    plugins = ROLE_PLUGINS.get(role, CODE_PLUGINS)
    leafed = leaf(settings)
    return leafed | {
        "autoMemoryEnabled": False,
        "claudeMdExcludes": [*leafed["claudeMdExcludes"], *((str(checkout / "CLAUDE.md"), str(checkout / "AGENTS.md")) if reads else ())],
        "skillOverrides": {skill: "off" for skill in sorted(names)},
        "enabledPlugins": leafed["enabledPlugins"] | {key: key in plugins for key in plugin_keys()},
        "env": leafed["env"] | LEAF_ENV,
    }


def plugin_keys() -> list[str]:
    """Each plugin installed in any scope, by its installed_plugins.json key."""
    return list(json.loads((install.config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"])


def start_job(name: str, job_id: str, agent: str, call: dict, held: dict, cwd: Path) -> str:
    """Start a builder's or reader's agent as its own `claude --bg` session on the Agent call its starter would make, its type, model and prompt, in cwd, the drive's checkout at the head of its default branch, as a handler starts, JOB_ENV naming the drive, the job, the agent id it is recorded as and the session holding its order, by which seen binds every hook of it as that session's agent, `CCI_PARENT_SESSION` naming that session, so cci posts from it as that session's lanes, and `CLAUDE_LONG_RUNNING_DRIVE` naming the drive, on lane_session's settings and flags for its type, so it compacts, loads its rules and runs its effort as an Orca lane does, with no advisor, headless and on only the plugins and servers its type needs; the session `claude --bg` names."""
    env = {state.JOB_ENV: f"{name}/{job_id}/{agent}/{held['session']}", "CCI_PARENT_SESSION": held["session"], state.DRIVE_ENV: name}
    settings, flags = lane_session(env, call["subagent_type"], cwd)
    model = ["--model", call["model"]] if call.get("model") else []
    argv = ["claude", "--bg", "--agent", call["subagent_type"], *model, *flags, "--permission-mode", "bypassPermissions", "--settings", json.dumps(settings), "-n", f"{name} {job_id}", call["prompt"]]
    return launched(reads.run(argv, cwd, {key: value for key, value in os.environ.items() if key != "CLAUDE_LONG_RUNNING_LANE"} | settings["env"]))


def resume_job(name: str, job_id: str, bg: dict, text: str) -> None:
    """Continue a job's own `claude --bg` session whose turn ended on text, in the background under the same session and in the checkout it started in, as `claude --bg --resume` continues one, so a text to an agent the watch started wakes it with no SendMessage: its idle background job stopped first, as a pool handler's wake stops its own, since a resume of a job still up starts a copy beside it; a copy Claude Code starts anyway becomes the job's session and the old job is removed."""
    found = background_job(bg["session"])
    if found:
        subprocess.run(["claude", "stop", found[0]], cwd=bg["cwd"], capture_output=True, check=False)
    resumed = launched(reads.run(["claude", "--bg", "--resume", bg["session"], text], Path(bg["cwd"]), {key: value for key, value in os.environ.items() if key != "CLAUDE_LONG_RUNNING_LANE"}))
    if resumed != bg["session"]:
        def resume_job_write(data: dict):
            data["jobs"][job_id]["bg"]["session"] = resumed

        state.update(name, None, resume_job_write)
        if found:
            subprocess.Popen(["claude", "rm", found[0]], cwd=bg["cwd"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def end_bg(session: str, cwd: Path, removed: bool) -> None:
    """Stop a session's background job once its turn is over, which would otherwise sit idle holding its memory, and with removed `claude rm` it too, so nothing resumes it; a session whose job Claude Code no longer keeps while its process still runs, as an orphaned handler's start can leave, ended with SIGTERM to that process."""
    if found := background_job(session):
        subprocess.Popen([*(REMOVE if removed else STOP), found[0]], cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return
    for pid, record in session_records():
        if record.get("sessionId") == session and running([(pid, record.get("procStartFt") or record.get("procStart"))]):
            os.kill(pid, signal.SIGTERM)


def take_pointed(name: str, job_id: str) -> list[str]:
    """Take the pointer lines a job's own session has not been handed yet, each naming a cci record a resume posted to its address while its turn ran, for its next step or its stop to hand it."""
    if not (state.saved_row(name, "jobs", job_id).get("bg") or {}).get("pointed"):
        return []
    def take_pointed_write(data: dict):
        bg = data["jobs"][job_id]["bg"]
        pointed, bg["pointed"] = bg["pointed"], []
        return pointed

    pointed = state.update(name, None, take_pointed_write)
    return pointed


def leaf(settings: dict) -> dict:
    """The settings of a session the drive starts below its top roots, the coordinator, communicator and meta session, which keep the user's whole ~/.claude/CLAUDE.md: that file excluded and the plugin's lane-rules/CLAUDE.md loaded in its place, as the directory LANE_RULES_ARGS adds once LANE_RULES_ENV lets Claude Code read an added directory's CLAUDE.md, so the session carries only the rules a lane needs, by his Nov 15 words "consider that we can exclude this claude.md for some of the middle or leaf nodes of our tree specifically for long-running"; each launch puts LANE_RULES_ARGS before another flag, since `--add-dir` takes every word after it up to the next one, a prompt included."""
    return settings | {"claudeMdExcludes": [*settings.get("claudeMdExcludes", []), str(install.config_dir() / "CLAUDE.md")], "env": settings["env"] | LANE_RULES_ENV}


def watchdog(name: str, brief: str, cwd: str, out: Path) -> int:
    """Start a watchdog agent on a stall's brief as its own `claude -p` process on its agent definition, which sets its model and tools, in the drive's checkout, its brief closing on the repository's rules, by render.repo_block, its answer written to out and its spend capped at WATCHDOG_USD; its environment names the drive and marks it the watchdog, by which the bash_guard hook lets it run WATCHDOG_RUNS alone, the reads and the two acts of his pick "Report, plus two safe acts, rerunning a failed build and messaging the owning job"; its pid."""
    settings = light(session_settings({state.WATCHDOG_ENV: name, state.DRIVE_ENV: name, **ADVISOR_OFF}), WATCHDOG, Path(cwd))
    brief = "\n\n".join([brief, *filter(None, ["\n".join(render.repo_block(state.load(name)))])])
    argv = ["claude", "-p", "--agent", WATCHDOG, "--permission-mode", "bypassPermissions", *LANE_RULES_ARGS, *STRICT_MCP, "--max-budget-usd", WATCHDOG_USD, "--settings", json.dumps(settings), brief]
    out.parent.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if key not in ("CLAUDE_LONG_RUNNING_LANE", "CLAUDE_CODE_SESSION_ID")} | settings["env"]
    with out.open("w") as written:
        return subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=written, stderr=subprocess.STDOUT, start_new_session=True).pid


def lane_session(env: dict[str, str], role: str, checkout: Path) -> tuple[dict, tuple[str, ...]]:
    """The `--settings` value and the flags a lane the drive starts as its own Claude Code session on this Mac runs on, such as a builder or reader the watch starts with `claude --bg`: light's settings for role with no advisor, the 350,000-token window of lib/lane.json, mcp_flags' servers for role, and EFFORT_CEILING, the flags going before any other flag or prompt, as LANE_RULES_ARGS must."""
    return light(session_settings(env | ADVISOR_OFF), role, checkout), (*LANE_RULES_ARGS, *STRICT_MCP, "--effort", EFFORT_CEILING)


def session_settings(env: dict[str, str]) -> dict:
    """What a session the drive starts runs on: every Orca lane's env, settings and denied tools from the orca plugin's lib/lane.json, the file orca-worker-launch.sh reads, CLAUDE_LANE_SETTINGS merged over those settings as that launcher merges it, then the drive's own SESSION_SETTINGS and env on top, its plugins over the lane's plugin by plugin, so the Datadog plugin, whose MCP is a drive session's one Datadog read, stays on whatever CLAUDE_LANE_SETTINGS turns off."""
    lane = lane_spec()
    settings = lane["settings"] | json.loads(os.environ.get("CLAUDE_LANE_SETTINGS") or "{}")
    denied = list(dict.fromkeys([*lane["deniedTools"], *SESSION_SETTINGS["permissions"]["deny"]]))
    plugins = settings.get("enabledPlugins", {}) | SESSION_SETTINGS["enabledPlugins"]
    disabled = list(dict.fromkeys([*settings.get("disabledMcpjsonServers", []), *SESSION_SETTINGS["disabledMcpjsonServers"]]))
    return settings | SESSION_SETTINGS | {"env": lane["env"] | SESSION_SETTINGS["env"] | env, "permissions": {"deny": denied}, "enabledPlugins": plugins, "disabledMcpjsonServers": disabled}


def lane_spec() -> dict:
    """The newest installed orca plugin's lib/lane.json: the env, settings and denied tools every Orca lane starts with."""
    return json.loads((Path(install.installed(ORCA)["installPath"]) / "lib" / "lane.json").read_text())


def datadog_servers() -> dict:
    """The `--mcp-config` an Orca lane on this Mac starts with, since the launcher's `--strict-mcp-config` loads no plugin's servers: the newest installed Datadog plugin's servers, each under the name Claude Code gives a plugin's server, `plugin:<plugin>:<server>`, under which this Mac's Datadog sign-in is kept, so the lane gets the `mcp__plugin_datadog_mcp__*` tools a drive session's Datadog read names and starts no other server."""
    root = Path(install.installed(DATADOG_PLUGIN)["installPath"])
    manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
    servers = json.loads((root / manifest["mcpServers"]).read_text())["mcpServers"]
    return {"mcpServers": {f"plugin:{manifest['name']}:{key}": server for key, server in servers.items()}}


def env_words(settings: dict) -> list[str]:
    """The `env` words a root session's command starts with: every variable its settings carry, so the `claude daemon` its `claude --bg` starts, and each spare that daemon forks, inherits them."""
    return ["env", *(f"{key}={value}" for key, value in settings["env"].items())]


def filled(page: str) -> str:
    """A skill page with each `orca terminal create` and `cci session register` line's `<session env>`, `<session settings>` and `<lane rules>` filled from session_settings, quoted for the double-quoted `--command` they sit in: `env` with every variable a session the drive starts carries, so the `claude daemon` its `claude --bg` starts inherits them too, the one `--settings` value it runs on, and the lane rules' directory; each root compacts at ROOT_WINDOW and the communicator at COMMUNICATOR_WINDOW, by his Nov 15 words "also roots should have 400k compaction, communicator 300k, and builders/readers/all else 350k", and a sub-coordinator, the one root below the top three, runs on leaf's settings."""

    def holes(line: str) -> dict[str, str]:
        seat = SEAT.search(line)
        speaks = bool(seat) and (seat[1] == "communicator" or seat[1].endswith("-communicator"))
        window = COMMUNICATOR_WINDOW if speaks else ROOT_WINDOW
        settings = session_settings({COMPACT_ENV: window})
        below = bool(seat) and seat[1] not in TOP_SEATS and not speaks
        settings = leaf(settings) if below else settings
        words = {
            SESSION_ENV: shlex.join(env_words(settings)),
            SESSION_SETTINGS_HOLE: shlex.quote(json.dumps(settings, separators=(",", ":"))),
            LANE_RULES_HOLE: shlex.join(LANE_RULES_ARGS) if below else "",
        }
        return {hole: DOUBLE_QUOTED.sub(r"\\\g<0>", said) for hole, said in words.items()}

    def fill(line: str) -> str:
        if not line.lstrip().startswith(("orca terminal create", "cci session register")):
            return line
        for hole, words in holes(line).items():
            line = line.replace(f" {hole}", f" {words}" if words else "").replace(hole, words)
        return line

    return "\n".join(fill(line) for line in page.split("\n"))


def launch(name: str, role: str, project: str | None, worktree: str) -> list[str]:
    """The `cci session register` line of Start or resume that starts the role's session, or a project's sub-coordinator's, under its alias in the worktree, and the `orca terminal create` line that shows it in Orca, as `drive manual start` fills them."""
    lines = filled(START_PAGE.read_text()).splitlines()
    seat = ("<lane>-communicator" if role == "communicator" else "<lane>") if project else role
    register = next(line for line in lines if line.startswith(f"cci session register <drive>-{seat} "))
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


def advisor_refusal(command: list[str]) -> str | None:
    """What Claude Code prints refusing the command's `--advisor` at launch, by a `claude -p` on the same model and advisor running only /advisor, which makes no model call; None when it takes the advisor."""
    model, advisor = (command[command.index(flag) + 1] for flag in ("--model", ADVISOR_FLAG))
    try:
        probe = subprocess.run(
            ["claude", "-p", "--model", model, ADVISOR_FLAG, advisor, "--no-session-persistence", "/advisor"],
            capture_output=True,
            text=True,
            timeout=ADVISOR_PROBE_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"claude -p {ADVISOR_FLAG} {advisor} gave no answer in {ADVISOR_PROBE_SECONDS} seconds"
    return f"{probe.stderr}{probe.stdout}".strip() if probe.returncode else None


def advised(name: str, lane: str, command: list[str]) -> NoReturn:
    """Exec the root session's claude command with its advisor once Claude Code takes it, else without the advisor, saying so in the terminal and to the meta session once a day per seat, since Claude Code exits at launch on an advisor it refuses, such as Fable before the account's one-time usage-credits consent, and cci would start it again on every exit."""
    from drive import tools

    if refused := advisor_refusal(command):
        at = command.index(ADVISOR_FLAG)
        advisor = command[at + 1]
        command = [*command[:at], *command[at + 2 :]]
        text = (
            f"drive advised: the {lane} session of drive {name} started without its {advisor} advisor, since Claude Code refused"
            f" {ADVISOR_FLAG} {advisor}: {refused}. Run /model {advisor} once in any session and accept the usage-credits consent, then restart the seat on his word."
        )
        print(f"\n{'!' * 72}\n{text}\n{'!' * 72}\n", file=sys.stderr, flush=True)
        try:
            tools.sent(name, lane, [{"kind": "defect", "to": [f"meta@{name}"], "text": text, "id": f"advisor-refused-{name}-{lane}-{datetime.now(UTC).date()}"}])
        except state.DriveError as error:
            print(f"drive advised: the meta session was not told: {error}", file=sys.stderr, flush=True)
    os.execvp(command[0], command)


def pointed(lines: list[str]) -> str:
    """What a job's agent running as its own session reads of the texts a resume posted to its cci address while it worked: each pointer line, in the order posted, to act on as a SendMessage from its starter would have it act."""
    return f"Texts for your job reached your cci address while you worked, as your starter's SendMessage would hand them: {' | '.join(lines)}"


def hold_rules(name: str, session: str) -> list[str]:
    """Keep on the session's seat a digest of each line of its block its role's rules draw in this install, returning each line the seat did not hold, so the session is due those alone; none when it held them all or the session holds no seat."""
    if not due_rules(state.bound(state.stored(name, (*state.BINDINGS, "slack"))) | {"name": name}, session):
        return []

    def held_rules(data: dict) -> list[str]:
        if due := due_rules(data, session):
            state.seat(data, session)["rules"] = render.rules_digests(data, state.session_role(data, session), state.session_seat(data, session))
        return due

    return state.update(name, None, held_rules)


def due_rules(data: dict, session: str) -> list[str]:
    """Each line of the session's block in this install whose digest its seat does not hold: every line for a seat that keeps none, or keeps the one digest of the whole block an older install kept."""
    if (held := state.seat(data, session)) is None:
        return []
    kept = set(held["rules"]) if isinstance(held.get("rules"), list) else set()
    return [line for line in render.rule_units(data, state.session_role(data, session), state.session_seat(data, session)) if render.rule_digest(line) not in kept]


def limit_hit(payload: dict) -> dict | None:
    """The usage limit a stop of a session on this Mac met, by state.usage_limit_hit on the notice Claude Code wrote as its last response; None for any other stop and on a Sprite, whose lane runs on its own key."""
    if os.environ.get(state.REMOTE_LANE_ENV):
        return None
    last = next((row for row in reversed(tail(Path(payload["transcript_path"]))) if row["type"] in ("user", "assistant")), {})
    return state.usage_limit_hit(notice(last), state.now()) if last else None


def keep_limit_hit(payload: dict, hit: dict) -> str | None:
    return state.keep_limit_hit(state.session_drives(payload["session_id"]), payload["session_id"], payload["cwd"], hit)


def resume(payload: dict) -> str | None:
    names = state.session_drives(payload["session_id"])
    if not names and not os.environ.get(state.REMOTE_LANE_ENV):
        return None
    transcript = Path(payload["transcript_path"])
    rows = tail(transcript)
    if refused(next((row for row in reversed(rows) if row["type"] in ("user", "assistant")), {})):
        return None
    tries = resumes(rows)
    if tries >= len(OUTAGE_WAITS):
        return None
    since = datetime.now(UTC)
    time.sleep(OUTAGE_WAITS[tries] * 60)
    if any(state.parse_time(row["timestamp"]) > since for row in tail(transcript) if row["type"] in ("user", "assistant")):
        return None
    return RESUMED


def resumes(rows: list[dict]) -> int:
    """How many RESUMED prompts a transcript's rows hold since its last response that ended its turn whole, one for each resume an API error met again."""
    calm = max((at for at, row in enumerate(rows) if ended(row) and not died(row)), default=-1)
    return sum(row["type"] == "user" and RESUMED in str(row["message"]["content"]) for row in rows[calm + 1 :])


def ended(row: dict) -> bool:
    """Whether a transcript row is a response's last: the model stopped rather than for a tool call or a paused server tool, so the session's turn is over and its next wake starts another."""
    return row.get("type") == "assistant" and row["message"].get("stop_reason") not in (None, "tool_use", "pause_turn")


def died(row: dict) -> bool:
    """Whether a transcript row is a response that ended on an API error or a refusal, the row Claude Code writes itself under model `<synthetic>` or one stopped for any reason but the end of its turn, so the agent's turn ended without its work and a SendMessage meets the same stop."""
    return ended(row) and (row["message"].get("stop_reason") != "end_turn" or row["message"].get("model") == SYNTHETIC)


def limited(row: dict) -> bool:
    """Whether a transcript row is a response a Claude usage limit stopped, Claude Code's notice "You've hit your weekly limit · resets …" or the API's "would exceed your account's rate limit": no outage to wait out, since Escape on its dialog keeps this Mac working on paid usage, by his words "Keep working on paid usage", so the watch restarts the agent or lane it stopped at once."""
    return died(row) and bool(state.USAGE_LIMIT.search(text := notice(row)) or RATE_LIMITED in text)


def refused(row: dict) -> bool:
    """Whether a transcript row is a response a model's safeguards stopped, the one stop_reason the Messages API gives its safety classifiers alone: a false positive on normal work that one context usually draws, so the watch restarts the agent in a fresh Claude session, never resuming the one flagged, as it resumes the stop of an API error."""
    return ended(row) and row["message"].get("stop_reason") == "refusal"


def notice(row: dict) -> str:
    """The text of a transcript row's response on one line, such as the notice Claude Code writes on a stop."""
    return " ".join(" ".join(part.get("text", "") for part in parts(row) if part.get("type") == "text").split())


def brief(payload: dict) -> str:
    """The prompt that started whoever made the call that fired a hook, an agent of the session or an Orca lane's session itself, by opened."""
    return opened(transcript_of(payload))


def opened(transcript: Path) -> str:
    """The prompt a transcript opens with, the brief file it names and the cci record its Record: line names read in; empty for a transcript not written yet."""
    if not transcript.is_file():
        return ""
    prompt = carried(opening(transcript))
    return f"{prompt}\n{record(named[1])}" if (named := RECORD.search(prompt)) else prompt


def opening(transcript: Path) -> str:
    """The prompt a session's transcript opens with, the brief it started on."""
    content = opening_row(transcript)["message"]["content"]
    return content if isinstance(content, str) else " ".join(part.get("text", "") for part in content)


def opening_row(transcript: Path) -> dict:
    """The row a session's transcript opens with, holding the prompt it started on and when that prompt reached it."""
    with transcript.open() as handle:
        return next(row for line in handle if (row := json.loads(line)).get("type") == "user")


def record(seq: str) -> str:
    """A cci record's whole text, its body file read in."""
    return reads.run(["cci", "show", seq], Path.cwd())


def takes(name: str, job_id: str, payload: dict) -> bool:
    """Whether the job a brief names may take the agent whose step fired the hook, as hold decides it, read from that job's row, its order's and the drive's bindings alone, so an agent's every later step reads no whole drive; true for a row an older install saved without a field free reads, which only the whole drive's shaping fills in."""
    if (job := state.saved_row(name, "jobs", job_id)) is None:
        return False
    if not {"state", "agent", "where"} <= job.keys():
        return True
    return free(name, {"jobs": {job_id: job}, "orders": {job["order"]: state.saved_row(name, "orders", job["order"])}} | state.saved(name), job_id, payload["agent_id"]) and payload.get("agent_type") in state.JOB_AGENTS[job["kind"]]


def free(name: str, data: dict, job_id: str, agent: str) -> bool:
    """Whether an agent at work on the job takes it: the job exists, it is not the agent a stop left on the job, which keeps the stop, no Orca lane holds it by its dispatch, which drive job resume restarts once that lane dies, nor will once the launch starting one now records it, by state.launch_in_flight, it is no codex job but an incident's, whose codex-ask run is its worker and beside which only an incident's second-lane builder runs, and no agent runs it already but one that ended with its session, which no role holds any more."""
    job = data["jobs"].get(job_id)
    if not job or (job["state"] == "stopped" and job["agent"] == agent) or (job["where"] == "remote" and job.get("dispatch")) or state.launch_in_flight(name, job_id, job) or (job["kind"] == "codex" and not data["orders"][job["order"]]["incident"]):
        return False
    return not (job["state"] == "running" and job["agent"] and state.session_role(data, job.get("session")) is not None)


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
    if not job_id or not free(names[0], known, job_id, payload["agent_id"]) or payload.get("agent_type") not in state.JOB_AGENTS[known["jobs"][job_id]["kind"]]:
        return
    returned = known["jobs"][job_id]["state"] != "running" and bool(known["jobs"][job_id]["returned"])
    if returned and not (resumed := woken(payload, known["jobs"][job_id])):
        return
    def hold_write(data: dict):
        if free(names[0], data, job_id, payload["agent_id"]):
            job = data["jobs"][job_id]
            job.update(agent=payload["agent_id"], where="local", session=payload["session_id"], undelivered=[], launching=None)
            if returned:
                job["resumed"] = state.stamp(resumed.astimezone())
            state.start(job)
            superseded.hear(data, job_id, prompt, state.parse_time(opening_row(transcript_of(payload))["timestamp"]))

    state.update(names[0], None, hold_write)
