import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn, Self

from drive import install, limits, procs, reads, render, slack, state, superseded, view
from drive.brief import BRIEF_JOB, carried, heavier, releases

HIS_KINDS = ("owner", "answer")
RECORD = re.compile(r"^Record: (\d+)\b", re.MULTILINE)
LINK = re.compile(r"^Link:\s*(\S+)", re.MULTILINE)
THREAD = re.compile(r"^Thread:[ \t]*(?:\S+[ \t]+)?(\d+\.\d+)", re.MULTILINE)
SAY_RESULT = re.compile(r"\b(POSTED|EDITED|CARD)((?:\s+https://[^\s;]+)+)\s*;?\s*")
RE_HOPS = 8
AMENDING = ("correction", "withdraw")
AMENDMENT_BYTES = 1 << 20
TRUNK_DRIVE = "trunk"
POSTS_CURSOR = "watch-posts"
POSTED_KINDS = ("report", "correction")
TRUNK_FAILED = "failed in Trunk's merge queue"
HANDLER = "long-running:handler"
SYNTHETIC = "<synthetic>"
OUTAGE_WAITS = (1, 2, 4, 8)
START_WINDOW = timedelta(minutes=5)
BATCH_WINDOW = timedelta(minutes=1)
EFFORT_CEILING = "high"
ABOVE_CEILING = ("xhigh", "max")
HANDLER_SKILLS = ("db-read", "iris-thread")
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
LEAF_PLUGINS_OFF = ("cc-present@cc-present", "codex@skills", "design-doc@skills", "incident-retro@skills", "open-pr@skills", "plans@skills", "slop-cop@skills", "slop-cop@slop-cop")
RESUMED = "Resumed: an API error ended your last turn before it was done, so do now what the prompt it answered asked, from where it stopped."
MESSAGE_ORIGIN = "coordinator"
DESIGNER = "long-running:designer"
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
UNPOSTED = "Context: the handler before you on this record ended without posting:"
VERDICT = re.compile(r"^(?:NOTHING|RECORDED)\b")
DIED = "no line, since its session ended before it returned one"
ORDER = "Order"
PAIRS = "Pairs in use"
IN_THREAD = "In his thread"
RELEASES = "Release freezes"
SCRATCH = re.compile(r"Scratch:\s*(\S+)")
LABEL = re.compile(rf"^({'|'.join(re.escape(label) for label in (*render.HANDLER_LABELS, ORDER, PAIRS, IN_THREAD, RELEASES))}):")
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
ADVISOR_FLAG = "--advisor"
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
    "A clock time a session typed for work already done in words he reads, a post, a card or a record a handler posts to him,"
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
    """A hook's payload as the drive's checks read it: a handler's own `claude --bg` session reads as an agent of the communicator's session, as a handler ran before it had a session of its own, so every check on a handler binds it unchanged; on a drive whose roles hold no communicator, it reads as an agent of its own session, which holds the communicator's role by its handler's id. A job's agent `drive job start` started as its own session reads, from the JOB_ENV its launch set, as the agent it was recorded as of the session holding its order then, as an Agent start of that session's would, so every check on a job's agent binds it unchanged, with no read of the drive."""
    if not payload.get("agent_id") and (started := os.environ.get(state.JOB_ENV)):
        _, _, agent, session = started.split("/")
        return payload | {"session_id": session, "agent_id": agent, "agent_transcript_path": payload["transcript_path"], "job_session": payload["session_id"]}
    if payload.get("agent_id") or payload.get("agent_type") != HANDLER or not (names := state.session_drives(payload["session_id"])):
        return payload
    data = state.saved(names[0])
    communicator = data["roles"].get("communicator")
    return payload | {
        "session_id": communicator["session"] if communicator else payload["session_id"],
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
    """The message of his a record carries or answers: an owner or answer record's own permalink, else that of the record its re names, followed up the chain, unless the record's post goes outside that message's thread, in another thread its thread attr names or at the root of a channel outside his DM as rooted reads it, since that post answers no message of his there; the record's thread, its attrs and each record the chain names either way."""
    record = shown(seq)
    attrs = record["fields"].get("attrs", {})
    thread = attrs.get("thread")
    chain: list[str] = []
    for _ in range(RE_HOPS):
        if record["kind"] in HIS_KINDS and (found := state.CARD_LINK.search((record.get("refs") or {}).get("url") or "")):
            message = f"{found[2]}.{found[3]}"
            if chain and (rooted(attrs) or thread not in (None, found[4] or message)):
                break
            return {"message": message, "link": record["refs"]["url"], "thread": thread or found[4] or message, "re": chain, "attrs": attrs}
        if not record.get("re"):
            break
        chain.append(str(record["re"]))
        record = shown(chain[-1])
    return {"message": None, "link": None, "thread": thread, "re": chain, "attrs": attrs}


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


def posts(name: str, since: int) -> tuple[list[tuple[str, datetime, str | None]], int]:
    """The seq of each `POST` report or correction to communicator@<drive> after since, oldest first, with when cci took it and the thread its thread attr names, read in one cci tail, so a correction of a report already posted gets the handler that rewords that post, and the newest seq the read reached, which is short of the drive's newest when the read held more than AMENDMENT_BYTES, since cci then prints the oldest records and the next read goes on from there."""
    argv = ["cci", "tail", "--drive", name, "--to", "communicator", *(flag for kind in POSTED_KINDS for flag in ("--kind", kind)), "--since", f"#{since}", "--budget", str(AMENDMENT_BYTES), "--json"]
    try:
        records = [json.loads(line) for line in reads.run(argv, Path.cwd()).splitlines()]
    except reads.ReadError as error:
        raise state.DriveError(f"the POST reports to communicator@{name} could not be read: {error}") from None
    found = [(str(record["seq"]), state.parse_time(record["at"]), record["fields"].get("attrs", {}).get("thread")) for record in records if posting(name, record)]
    return found, max((record["seq"] for record in records), default=since)


def seeded(name: str) -> int:
    """Where the read of a drive's POST reports starts on a drive that keeps no mark yet: where POSTS_CURSOR stands, the cci cursor an older install's read moved past each report it started a handler on, else at the drive's newest record, as that cursor was seeded, so no report goes unread and none replays."""
    try:
        return int(re.search(r"#(\d+)", reads.run(["cci", "cursor", "show", POSTS_CURSOR, "--drive", name], Path.cwd()))[1])
    except reads.ReadError as error:
        if "has not been set" not in str(error):
            raise state.DriveError(f"the POST reports' cursor could not be read: {error}") from None
    try:
        newest = reads.run(["cci", "tail", "--drive", name, "--since", "#1", "-n", "1", "--json"], Path.cwd())
    except reads.ReadError as error:
        raise state.DriveError(f"the newest record of {name} could not be read: {error}") from None
    return json.loads(newest)["seq"] if newest.strip() else 0


def posting(name: str, record: dict) -> bool:
    """Whether a record cci holds is a `POST` report or correction to communicator@<drive>, one the drive owes a post until one carries it."""
    return bool({"communicator", f"communicator@{name}"} & set(record.get("to", ()))) and record["kind"] in POSTED_KINDS and record["text"].startswith("POST")


def owing(data: dict, seq: str) -> bool:
    """Whether the drive still owes a post to a `POST` report or correction: neither a post, his catch-up or a handler's given-up done record carries it, nor is it held for his catch-up."""
    return seq not in data["carried"] and seq not in data["held"]


def start_posts(name: str, moment: datetime) -> None:
    """Start a handler on each POST report the drive owes a post from its mark on, but one a live handler holds, by its record or a brief handed to it, as the watch's read of them: each stays owed until a post carries it, so one whose handler did not start, never started, died or ended posting nothing gets a fresh one on the next read, and the mark moves past a report only once it is carried or held, in one save after every start, so a read that dies anywhere leaves each report it read owed. A report cci took less than BATCH_WINDOW before the moment waits for a later read unless a live handler, one this read started included, holds the thread its thread attr names, so the reports of one thread inside a minute go to one handler, by the Nov 15 pick "Handlers batched + trimmed". A report whose handler died or never started after the one before it ended without posting is given up, by give_up, as a second such end of a handler's turn is. A handler that did not start, whatever its start raised, raises a refusal naming its report and its error, once the rest started."""
    data = state.load(name)
    since = seeded(name) if data["posts"]["mark"] is None else data["posts"]["mark"]
    read, newest = posts(name, since)
    seqs = [seq for seq, _, _ in read]
    data = state.load(name)
    holders = [(gone(entry), held_records(entry)) for entry in data["handlers"].values()]
    live = {seq for ended, records in holders if not ended for seq in records}
    dead = {seq for ended, records in holders if ended for seq in records}
    errors = []
    for seq, at, thread in read:
        if not owing(data, seq) or seq in live:
            continue
        if at + BATCH_WINDOW > moment and not live_thread(state.load(name), thread):
            continue
        if seq in dead and (line := data["posts"]["unposted"].get(seq)):
            try:
                give_up(name, seq, line)
            except state.DriveError as error:
                errors.append(str(error))
            continue
        try:
            handle(name, f"Record: {seq} Drive: {name} Scratch: {state.drive_dir(name) / 'scratch' / f'post-{seq}'}", owed=True)
        except Carried:
            continue
        except Exception as error:
            errors.append(f"the handler on POST report {seq} did not start: {start_error(error)}")
    with state.edit(name) as data:
        owed = [seq for seq in seqs if owing(data, seq)]
        mark = data["posts"]["mark"] = int(owed[0]) - 1 if owed else newest
        data["posts"]["unposted"] = {seq: line for seq, line in data["posts"]["unposted"].items() if int(seq) > mark}
    if errors:
        raise state.DriveError("; ".join(errors))


def live_thread(data: dict, thread: str | None) -> bool:
    """Whether a handler whose turn is not over holds the thread."""
    return bool(thread) and any(entry["holds"] and entry["holds"].get("thread") == thread and not gone(entry) for entry in data["handlers"].values())


def held_records(entry: dict) -> list[str]:
    """Each record a handler holds: its own, then each one a brief handed to it names, taken or not."""
    return list(dict.fromkeys([*filter(None, [(entry["holds"] or {}).get("record")]), *(named[1] for brief in [*entry["handed"], *entry["took"]] if (named := RECORD.search(brief)))]))


class Carried(state.DriveError):
    """A refusal to start a handler on a `POST` report a post already carries."""


def give_up(name: str, seq: str, line: str) -> None:
    """Give up a `POST` report whose handler died or never started after the one before it ended without posting, as end_handler gives up a report on a second such end: a done record to the report's writer too, naming the line the one before ended on, carries it."""
    try:
        done = done_record(name, Path.cwd(), f"posts-given-up-{seq}", f"UNPOSTED {seq}; its handler ended before it returned a line, after the handler before it ended without posting: {line}", [shown(seq)["lane"]])
    except reads.ReadError as error:
        raise state.DriveError(f"POST report {seq} could not be given up: {error}") from None
    carry(name, [seq], done)


def carry(name: str, seqs: list[str], done: str) -> None:
    """Keep the done record that gave up each of seqs as what carries it, so the drive owes it no post."""
    with state.edit(name) as data:
        for seq in seqs:
            data["carried"].setdefault(seq, f"done record {done}")
            data["posts"]["unposted"].pop(seq, None)


def trunk_failures(keys: list[str], since: str) -> list[dict]:
    """The records of each failure of keys in Trunk's merge queue since then, newest first, which Trunk's webhook relays into cci's trunk drive, read in one cci grep."""
    pattern = "|".join(f"{re.escape(key)} {TRUNK_FAILED}" for key in keys)
    try:
        lines = reads.run(["cci", "grep", pattern, "--drive", TRUNK_DRIVE, "--kind", "queue", "--since", since, "--json"], Path.cwd()).splitlines()
    except reads.ReadError as error:
        raise state.DriveError(f"Trunk's queue records of {', '.join(keys)} could not be read: {error}") from None
    return [json.loads(line) for line in lines]


def holder(data: dict, held: dict) -> str | None:
    """The handler already at work on what a new brief holds, whichever kind of brief either is: the same message of his, the same thread, so a thread has one handler at a time, which takes every brief in it, or for a record the same record, one its re chain names or whose chain names it."""
    for handler_id, entry in data["handlers"].items():
        other = entry["holds"] or {}
        if (held["message"] and held["message"] == other.get("message")) or (held["thread"] and held["thread"] == other.get("thread")):
            return handler_id
        if held["record"] and (held["record"] == other.get("record") or other.get("record") in held["re"] or held["record"] in (other.get("re") or ())):
            return handler_id
    return None


def gone(entry: dict) -> bool:
    """Whether a handler's turn is over, whatever its Stop hook did, read from the records Claude Code keeps of the session its launch named: no running process's `sessions/<pid>.json`, the record Claude Code keeps of each session's process, names that session with a status but idle, and its background job, which `claude --bg` writes `working` before it returns, ended a turn done, blocked, failed or stopped, rests idle or blocked, as one stuck on a startup dialog does, or `claude rm` took it; so a launch whose job ended blocked before it ran a turn holds nothing. A handler whose launch has not named its session yet is in flight for START_WINDOW, many times the seconds a `claude --bg` launch takes, and past it never started, as when an install or a stopped watch ended its start between the drive's save of its entry and its launch's save of the session; one whose turn an API error ended is not gone until the resume that wakes it."""
    if (wakes := entry.get("wakes")) and state.parse_time(wakes) > state.now():
        return False
    if not entry["session"]:
        return state.parse_time(entry["started"]) + START_WINDOW <= state.now()
    if busy(entry["session"], session_records()):
        return False
    found = background_job(entry["session"])
    return found is None or found[1]["state"] in ("done", "blocked", "failed", "stopped") or found[1].get("tempo") in ("idle", "blocked")


def busy(session: str, records: list[tuple[int, dict]]) -> bool:
    """Whether a running process's record names the session with a status but idle."""
    return bool(running([(pid, record.get("procStartFt") or record.get("procStart")) for pid, record in records if record.get("sessionId") == session and record.get("status") != "idle"]))


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


def handle(name: str, brief: str, detached: bool = False, answer: str | None = None, briefs: bool = False, owed: bool = False) -> tuple[str, bool]:
    """Start a handler on its brief, unless a live one already holds what the brief holds, since a message of his or a thread has one handler at a time, but for a message of his reaching a handler whose session has not started, as one waiting its turn on this Mac, which starts a handler of its own at once, since his answer never waits on a report's start: a brief for another record or another message of his, on that message, in that thread or on its record's re chain, is handed to that handler, which takes it at its next step, and a copy, the same record or the same message of his, is handed to it with nothing added, since that handler already holds it; the id of the handler at work on the brief, and whether it was handed. A record whose brief, attrs and re chain name no thread and reach no message of his is in the thread its order's lines go in, as the thread attr the drive stamps on its own records names it, and one whose post goes elsewhere than that thread or the thread of the message of his its chain reaches, at the root of a channel outside his DM such as a staging claim or in another thread its thread attr names, holds that place alone, so it waits on no handler of his messages there. A handler whose turn is over holds nothing, however it ended: it leaves the drive, and each brief handed to it that it never took goes to the handler this brief reaches, but one for the record this brief names; one that never started leaves its own brief with them, by forget. With briefs, the brief a new handler starts on is written from its id by cli.briefing, with answer its --answer title, in the process that starts it, recording what it adds, so the call returns before those writes and reads take the drive's lock; a handler that does not start leaves no entry, and no job it was given nor the order made for that job. Detached, as the handler tool and the watch's hand of each message of his cc-slack owes run it, the handler's session starts after the call returns, from the brief kept for `drive handler-start`; the watch's other stages start it in the call, so a start that fails is tried again at their next read. Owed, as the handler tool's and the watch's read's start on a `POST` report is, a report the drive owes no post is refused, naming the post or done record that carries it or his catch-up holding it, and one whose last handler ended posting nothing, died or never started starts on a brief closing on the line that handler ended on, checked in the same save that starts it, a handler that died or never started keeping DIED as its line, and the brief briefed writes closes on that line too. A brief for a record a post carries goes to no handler."""
    read = holds(brief)
    asked = brief
    handler_id = uuid.uuid4().hex[:8]
    with state.edit(name) as data:
        if owed and not owing(data, read["record"]):
            went = f"{post} carries it" if (post := data["carried"].get(read["record"])) else "its line is held for his return" if read["record"] in data["held"] else "the line its handler ended on carries it"
            raise Carried(f"refused, no handler started: POST report {read['record']} needs no handler, since {went}")
        held_now = threaded(data, read)
        left: list[str] = []
        lost: list[str] = []
        while (held := holder(data, held_now)) and gone(data["handlers"][held]):
            lost += held_records(data["handlers"][held])
            left += forget(data, held)
        if held and his_message(held_now) and not data["handlers"][held]["session"]:
            held = None
        left = [one for one in dict.fromkeys(left) if one != brief.strip() and (not (named := RECORD.search(one)) or (named[1] != held_now["record"] and named[1] not in data["carried"]))]
        if owed and read["record"] in lost:
            data["posts"]["unposted"].setdefault(read["record"], DIED)
        context = f"\n{UNPOSTED} {line}" if owed and (line := data["posts"]["unposted"].get(read["record"])) and UNPOSTED not in brief else ""
        brief = f"{brief.strip()}{context}" if context else brief
        if held:
            entry = data["handlers"][held]
            same = "record" if held_now["record"] else "message"
            kept = not held_now[same] or held_now[same] != entry["holds"][same]
            entry["handed"] += [one for one in [*left, *([brief.strip()] if kept else [])] if one not in entry["handed"]]
        else:
            data["handlers"][handler_id] = {"session": None, "started": state.stamp(), "brief": brief.strip(), "holds": held_now, "handed": left, "took": [], "posted": []}
    if held:
        return held, True
    later = {"brief": asked, "answer": answer, "context": context} if briefs else None
    try:
        state.after_save(name, lambda: started(name, handler_id, brief, brief, detached, later))
    except Exception as error:
        unstarted(name, handler_id)
        raise (state.DriveError(f"claude --bg started no handler: {error}") if isinstance(error, reads.ReadError) else error) from None
    return handler_id, False


def started(name: str, handler_id: str, brief: str, prompt: str, detached: bool, later: dict | None = None) -> list[str]:
    """Start the handler's session on its prompt once the drive keeps its entry, its brief's scratch folder made first: in this call while this Mac admits a local Claude lane, by place.admission, or at once for a message of his, by his_message, or detached from the prompt kept for `drive handler-start`, which waits its turn, beside later, what that start writes its brief from, when the brief is written there."""
    from drive import place

    if folder := scratch(brief):
        folder.mkdir(parents=True, exist_ok=True)
    if not detached and later is None and (his_message(holds(brief)) or not place.admission(state.load(name), None, "local", state.now())):
        start_handler(name, handler_id, prompt)
        return []
    kept = brief_path(name, handler_id)
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(prompt)
    if later is not None:
        kept.with_suffix(".json").write_text(json.dumps(later))
    state.detach(name, ["handler-start", handler_id], "handlers.log")
    return []


def his_message(held: dict) -> bool:
    """Whether a handler's brief, as holds reads it, answers a message of his rather than a record, which starts at once past this Mac's load gate and caps, by his words through the main session "the owner's answers must never queue behind lane load"; a report's or a digest's handler, which works on a record, waits its turn."""
    return not held["record"]


def threaded(data: dict, held: dict) -> dict:
    """What a brief holds, as holds read it, but its record's attrs: a record whose brief, attrs and re chain name no thread and reach no message of his is in the thread its order's lines go in, as the thread attr the drive stamps on its own records names it, but for one whose post goes at the root of a channel outside his DM as rooted reads it, such as a staging claim, which starts a thread of its own and so holds none."""
    found = {key: value for key, value in held.items() if key != "attrs"}
    attrs = held.get("attrs", {})
    if not found["thread"] and not rooted(attrs) and (order_id := state.report_order(data, attrs)):
        found |= state.thread_attr(data["orders"][order_id])
    return found


def forget(data: dict, handler: str) -> list[str]:
    """Take a handler off the drive, and the briefs it leaves to the handler the next brief reaches: each one handed to it that it never took, and for one whose launch never named its session, which never started, its own brief first, its job and that job's order leaving with it."""
    entry = data["handlers"].pop(handler)
    if entry["session"]:
        return entry["handed"]
    if job_id := state.agent_job(data, handler):
        del data["orders"][data["jobs"].pop(job_id)["order"]]
    return [*filter(None, [entry["brief"]]), *entry["handed"]]


def unstarted(name: str, handler: str) -> None:
    """Take a handler that did not start off the drive, with the job it was given and the order made for that job."""
    with state.edit(name) as data:
        if handler in data["handlers"]:
            forget(data, handler)


def brief_path(name: str, handler: str) -> Path:
    return state.drive_dir(name) / "handlers" / f"{handler}.md"


def start_kept(name: str, handler: str) -> str:
    """Start the handler `handle` kept, in its own process after the handler call returned, since `claude --bg` takes seconds, once this Mac admits a local Claude lane, by place.waited_turn, so a handler waits its turn while this Mac is loaded, short of memory or at its local cap, and never fails for it; one that fails to start leaves the drive's handlers, with the job it was given and the order made for that job, and tells the session it runs beside in a done record from lane handler, naming the error and its kept brief. Its line in handlers.log is stamped and says how long this process took to begin after the brief was kept, how long it waited its turn and how long its start took, so a slow launch names which of the three it was."""
    from drive import place

    kept = brief_path(name, handler)
    began = time.time()
    try:
        prompt = kept_prompt(name, handler)
        briefed = time.time()
        with place.waited_turn(name, lambda: waits_turn(name, handler), his_message(holds(kept.read_text()))):
            admitted = time.time()
            start_handler(name, handler, prompt)
    except Exception as error:
        unstarted(name, handler)
        line = f"BLOCKED handler {handler} did not start: {start_error(error)}; its brief is kept in {kept}: start it again with the handler tool"
        reads.run(["cci", "post", "--drive", name, "--lane", "handler", "--kind", "done", "--to", f"{beside(state.saved(name))[0]}@{name}", "--id", f"handler-start-{handler}", "--text", line], Path.cwd(), reads.posting())
        return line
    return f"{state.stamp()} started {handler}: began {began - kept.stat().st_mtime:.1f}s after its brief was kept, wrote its brief in {briefed - began:.1f}s, waited {admitted - briefed:.1f}s for its turn, its start took {time.time() - admitted:.1f}s"


def kept_prompt(name: str, handler: str) -> str:
    """The prompt a kept handler starts on: its kept brief, or the brief cli.briefing writes from what handle kept beside it, closing on the context line handle added."""
    from drive import cli

    kept = brief_path(name, handler)
    if not (later := kept.with_suffix(".json")).exists():
        return kept.read_text()
    held = json.loads(later.read_text())
    briefed = cli.briefing(name, held["brief"], held["answer"])
    return f"{briefed(handler).strip()}{held['context']}" if briefed else kept.read_text()


def waits_turn(name: str, handler: str) -> None:
    """Keep a handler whose start waits its turn on this Mac read as not gone, by its wakes past the next read of place.waited_turn, so no read takes it for a start that never came."""
    from drive import place

    with state.edit(name) as data:
        if entry := data["handlers"].get(handler):
            entry["wakes"] = state.stamp(state.now() + timedelta(seconds=2 * place.TURN_SECONDS))


def start_error(error: Exception) -> str:
    """Why a handler did not start: a refusal's or a failed command's own words, else the error as render.raised names one the drive did not expect."""
    return str(error) if isinstance(error, state.DriveError | reads.ReadError) else render.raised(error)


def beside(data: dict) -> tuple[str, dict]:
    """The lane a drive's handlers run beside and end their turn to, and its role: the communicator's, or on a drive whose roles hold none, as one started from a terminal holds none, the coordinator's, so his lines still reach a handler that posts them in his DM."""
    return ("communicator", data["roles"]["communicator"]) if "communicator" in data["roles"] else ("root", data["roles"]["coordinator"])


def start_handler(name: str, handler: str, prompt: str) -> None:
    """Start a handler as its own `claude --bg` session on its agent definition, which sets its model, in the drive's checkout at the head of its default branch, wherever the session it runs beside, the communicator's or on a drive with none the coordinator's, still runs, whether that session or the drive's watch starts it, `LONG_RUNNING_HANDLER` naming it, so every check on the communicator's handlers finds it from its first step, its entry keeping the session `claude --bg` names, by which the drive reads whether its turn is over, `CCI_PARENT_SESSION` naming that session, so cci binds the handler's session to no lane and it posts as the communicator's lanes, and `CLAUDE_LONG_RUNNING_DRIVE` naming the drive, so each cci call that names no drive resolves this one; the watch's own lane stays out of its environment. Its prompt closes on the release freezes and his release hold as they stand at this start, in place of the line an earlier start of it carried. It runs on the trimmed settings."""
    from drive import orca

    data = state.saved(name)
    _, held = beside(data)
    lines = [line for line in prompt.strip().splitlines() if not line.startswith(f"{RELEASES}:")]
    prompt = "\n".join([*lines, f"{RELEASES}: {releases(name, data)}"])
    env = {state.HANDLER_ENV: f"{name}/{handler}", "CCI_PARENT_SESSION": held["session"], state.DRIVE_ENV: name}
    checkout = orca.checkout(name, held["cwd"])
    settings = trimmed(session_settings(env), checkout)
    argv = ["claude", "--bg", "--agent", HANDLER, "--permission-mode", "bypassPermissions", "--settings", json.dumps(settings), *LANE_RULES_ARGS, "-n", f"{name} handler {handler}", prompt]
    session = launched(reads.run(argv, checkout, {key: value for key, value in os.environ.items() if key != "CLAUDE_LONG_RUNNING_LANE"} | settings["env"]))
    with state.edit(name) as data:
        data["handlers"][handler]["session"] = session


def trimmed(settings: dict, checkout: Path) -> dict:
    """The settings of a session below the top roots, as leaf gives them, that reads and answers and writes no code, a handler or a watchdog: no auto-memory, so the repository's MEMORY.md stays out, no CLAUDE.md or AGENTS.md at the checkout's root, every skill but HANDLER_SKILLS that a user, the checkout or claude.ai sync installed turned off in the listing, and the plugins of LEAF_PLUGINS_OFF off with their skills, hooks and servers, since its own definition carries what it does, by the Nov 15 pick "Handlers batched + trimmed"; a plugin's skills stay listed while it is on, since skillOverrides cannot turn them off."""
    names = {found[1] if (found := SKILL_NAME.search(path.read_text())) else path.parent.name for root in (install.config_dir() / "skills", checkout / ".claude" / "skills") for path in [*root.glob("*/SKILL.md"), *root.glob("synced/*/*/SKILL.md")]}
    leafed = leaf(settings)
    return leafed | {
        "autoMemoryEnabled": False,
        "claudeMdExcludes": [*leafed["claudeMdExcludes"], str(checkout / "CLAUDE.md"), str(checkout / "AGENTS.md")],
        "skillOverrides": {skill: "off" for skill in sorted(names - set(HANDLER_SKILLS))},
        "enabledPlugins": leafed["enabledPlugins"] | dict.fromkeys(LEAF_PLUGINS_OFF, False),
    }


def start_job(name: str, job_id: str, agent: str, call: dict, held: dict, cwd: Path) -> str:
    """Start a builder's or reader's agent as its own `claude --bg` session on the Agent call its starter would make, its type, model and prompt, in cwd, the drive's checkout at the head of its default branch, as a handler starts, JOB_ENV naming the drive, the job, the agent id it is recorded as and the session holding its order, by which seen binds every hook of it as that session's agent, `CCI_PARENT_SESSION` naming that session, so cci posts from it as that session's lanes, and `CLAUDE_LONG_RUNNING_DRIVE` naming the drive; the session `claude --bg` names."""
    env = {state.JOB_ENV: f"{name}/{job_id}/{agent}/{held['session']}", "CCI_PARENT_SESSION": held["session"], state.DRIVE_ENV: name}
    settings = session_settings(env)
    model = ["--model", call["model"]] if call.get("model") else []
    argv = ["claude", "--bg", "--agent", call["subagent_type"], *model, "--permission-mode", "bypassPermissions", "--settings", json.dumps(settings), "-n", f"{name} {job_id}", call["prompt"]]
    return launched(reads.run(argv, cwd, {key: value for key, value in os.environ.items() if key != "CLAUDE_LONG_RUNNING_LANE"} | settings["env"]))


def resume_job(bg: dict, text: str) -> None:
    """Continue a job's own `claude --bg` session whose turn ended on text, in the background under the same session and in the checkout it started in, as `claude --bg --resume` continues one, so a text to an agent the watch started wakes it with no SendMessage."""
    reads.run(["claude", "--bg", "--resume", bg["session"], text], Path(bg["cwd"]), {key: value for key, value in os.environ.items() if key != "CLAUDE_LONG_RUNNING_LANE"})


def take_pointed(name: str, job_id: str) -> list[str]:
    """Take the pointer lines a job's own session has not been handed yet, each naming a cci record a resume posted to its address while its turn ran, for its next step or its stop to hand it."""
    if not (state.saved_row(name, "jobs", job_id).get("bg") or {}).get("pointed"):
        return []
    with state.edit(name) as data:
        bg = data["jobs"][job_id]["bg"]
        pointed, bg["pointed"] = bg["pointed"], []
    return pointed


def leaf(settings: dict) -> dict:
    """The settings of a session the drive starts below its top roots, the coordinator, communicator and meta session, which keep the user's whole ~/.claude/CLAUDE.md: that file excluded and the plugin's lane-rules/CLAUDE.md loaded in its place, as the directory LANE_RULES_ARGS adds once LANE_RULES_ENV lets Claude Code read an added directory's CLAUDE.md, so the session carries only the rules a lane needs, by his Nov 15 words "consider that we can exclude this claude.md for some of the middle or leaf nodes of our tree specifically for long-running"; each launch puts LANE_RULES_ARGS before another flag, since `--add-dir` takes every word after it up to the next one, a prompt included."""
    return settings | {"claudeMdExcludes": [*settings.get("claudeMdExcludes", []), str(install.config_dir() / "CLAUDE.md")], "env": settings["env"] | LANE_RULES_ENV}


def watchdog(name: str, brief: str, cwd: str, out: Path) -> int:
    """Start a watchdog agent on a stall's brief as its own `claude -p` process on its agent definition, which sets its model and tools, in the drive's checkout, its answer written to out and its spend capped at WATCHDOG_USD; its environment names the drive and marks it the watchdog, by which the bash_guard hook lets it run WATCHDOG_RUNS alone, the reads and the two acts of his pick "Report, plus two safe acts, rerunning a failed build and messaging the owning job"; its pid."""
    settings = trimmed(session_settings({state.WATCHDOG_ENV: name, state.DRIVE_ENV: name, **ADVISOR_OFF}), Path(cwd))
    argv = ["claude", "-p", "--agent", WATCHDOG, "--permission-mode", "bypassPermissions", *LANE_RULES_ARGS, "--max-budget-usd", WATCHDOG_USD, "--settings", json.dumps(settings), brief]
    out.parent.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if key not in ("CLAUDE_LONG_RUNNING_LANE", "CLAUDE_CODE_SESSION_ID")} | settings["env"]
    with out.open("w") as written:
        return subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=written, stderr=subprocess.STDOUT, start_new_session=True).pid


def lane_session(env: dict[str, str]) -> tuple[dict, tuple[str, ...]]:
    """The `--settings` value and the flags a lane the drive starts as its own Claude Code session on this Mac runs on, such as a builder or reader the watch starts with `claude --bg`: leaf's settings with no advisor, the 350,000-token window of lib/lane.json, and EFFORT_CEILING, the flags going before any other flag or prompt, as LANE_RULES_ARGS must."""
    return leaf(session_settings(env | ADVISOR_OFF)), (*LANE_RULES_ARGS, "--effort", EFFORT_CEILING)


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
        window = COMMUNICATOR_WINDOW if seat and seat[1] == "communicator" else ROOT_WINDOW
        settings = session_settings({COMPACT_ENV: window})
        below = bool(seat) and seat[1] not in TOP_SEATS
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


def handed(briefs: list[str]) -> str:
    """What a handler reads of the briefs drive handler handed it while it worked: each brief, his words or a record, in the thread, on the message or on the record it is at work on, to cover in its own post."""
    return (
        f"drive handler handed you {len(briefs)} more {'brief' if len(briefs) == 1 else 'briefs'} in the thread, on the message or on the record you are at work on,"
        f" since a thread has one handler at a time: {' | '.join(briefs)}. Read each brief, and the record it names, as your brief says and cover it"
        " in the post you are writing; once you have posted, post one more line in his thread with only what your posts left out,"
        " or nothing when they already said it. Your last line names what you did for each."
    )


def hold_rules(name: str, session: str) -> str | None:
    """Keep on the session's seat the digest of its role's rules in this install, returning that role when the seat held other rules or none, so the session is due them whole; None when it held these or the session holds no seat, as a handler's does."""
    if not due_rules(state.saved(name), session):
        return None
    with state.edit(name) as data:
        if role := due_rules(data, session):
            state.seat(data, session)["rules"] = render.rules_digest(data, role)
    return role


def due_rules(data: dict, session: str) -> str | None:
    """The role whose rules in this install the session's seat does not hold, or None."""
    held = state.seat(data, session)
    if held is None or held.get("rules") == render.rules_digest(data, role := state.session_role(data, session)):
        return None
    return role


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
    """End a handler's turn once refused, the Stop hook's checks of its line, lets it: hand the line it ended on to the session it runs beside, the communicator or on a drive with none the coordinator, as one cci record led by TOOK and the ts of each message of his it took by handover, each post's POSTED, EDITED and CARD links in it say's own result, whole with its thread_ts, in place of any the handler typed, each permalink named once where it first appears, and remove its session. A job the handler held keeps its transcript's path, since the drive forgets the handler. A handler drive handler handed briefs since its last step does not end: they come back as the reason its stop is refused, taken in the one edit that would forget it, so no brief lands on a handler that has ended. In that edit its posts carry each record it held or took, and with none each `POST` report it held or took stays owed, by settled; the done record of each report given up goes to that report's writer too, led by UNPOSTED and its seq, and carries it. A check that raises still posts the handler's line before its error goes on."""
    name = state.session_drives(payload["session_id"])[0]
    given_up: dict[str, str] = {}
    try:
        if reason := refused(payload):
            return reason
        ending = state.saved(name)["handlers"][payload["agent_id"]]
        writers = {} if ending["posted"] else {seq: record["lane"] for seq in held_records(ending) if posting(name, record := shown(seq))}
        with state.edit(name) as data:
            ended_handler = data["handlers"][payload["agent_id"]]
            if briefs := ended_handler["handed"]:
                ended_handler["handed"] = []
                ended_handler["took"] += briefs
            else:
                data["handlers"].pop(payload["agent_id"])
                if job_id := state.agent_job(data, payload["agent_id"]):
                    data["jobs"][job_id]["transcript"] = payload["agent_transcript_path"]
                given_up = settled(data, ended_handler, writers, " ".join((payload.get("last_assistant_message") or "").split()))
        if briefs:
            return Unjudged(f"Refused on purpose: {handed(briefs)}")
    except Exception:
        post_done(name, payload, state.saved(name)["handlers"][payload["agent_id"]])
        raise
    done = post_done(name, payload, ended_handler, given_up)
    if given_up:
        carry(name, list(given_up), done)


def settled(data: dict, entry: dict, writers: dict[str, str], line: str) -> dict[str, str]:
    """Settle each record a handler that ended held or took: its first post carries every one; with none, each `POST` report of writers, by the lane that wrote it, still owed keeps the line it ended on for the fresh handler the watch's next read starts on it, but one whose last handler ended posting nothing too, or each on a NOTHING or RECORDED verdict, which a fresh handler would only repeat: those, by their writers, to give up."""
    if entry["posted"]:
        link = SAY_RESULT.search(entry["posted"][0])[2].split()[0]
        for seq in held_records(entry):
            data["carried"].setdefault(seq, link)
            data["posts"]["unposted"].pop(seq, None)
        return {}
    owed = {seq: writer for seq, writer in writers.items() if owing(data, seq)}
    given_up = {seq: writer for seq, writer in owed.items() if seq in data["posts"]["unposted"] or VERDICT.match(line)}
    data["posts"]["unposted"] |= {seq: line or "no line" for seq in owed if seq not in given_up}
    return given_up


def post_done(name: str, payload: dict, ended_handler: dict, given_up: dict[str, str] | None = None) -> str:
    """Post the line a handler ended on as its done record to the lane it runs beside, and to the writer of each report it gives up, readdressed to its heir when that lane stood down; the seq cci gave a done record that gives reports up."""
    line = (payload.get("last_assistant_message") or "").strip()
    if posted := ended_handler["posted"]:
        line = "; ".join(filter(None, [SAY_RESULT.sub("", line).strip(" ;"), *named_once(posted)]))
    if given_up:
        line = f"UNPOSTED {' '.join(given_up)}; {line}"
    if took := [f"{found[2]}.{found[3]}" for brief in ended_handler["took"] if HIS_WORDS in brief and (linked := LINK.search(brief)) and (found := state.CARD_LINK.search(linked[1]))]:
        line = f"TOOK {' '.join(took)}; {line}"
    done = done_record(name, Path(payload["cwd"]), f"handler-{payload['agent_id']}", line, list((given_up or {}).values()))
    remove_session(name, payload)
    return done


def done_record(name: str, cwd: Path, key: str, line: str, writers: list[str]) -> str:
    """Post a done record from lane handler, idempotent on key, to the session the drive's handlers run beside, and to each writer of a report it gives up, readdressed to its heir when that lane stood down; the seq cci gave a record that gives reports up."""
    to = [flag for lane in dict.fromkeys([beside(state.saved(name))[0], *writers]) for flag in ("--to", f"{lane}@{name}")]
    gives = ["--or-heir", "--json"] if writers else []
    out = reads.run(["cci", "post", "--drive", name, "--lane", "handler", "--kind", "done", *to, "--id", key, "--text", line, *gives], cwd, reads.posting())
    return str(json.loads(out)["seq"]) if writers else ""


def named_once(posted: list[str]) -> list[str]:
    """Each say result a handler's posts returned, naming each permalink only where it first appears: an edit of a post already named, or a card's line in its own post's thread, adds no link the line already holds, and a result left with none drops out."""
    seen: set[str] = set()
    kept = []
    for said in posted:
        parts = []
        for verb, links in SAY_RESULT.findall(said):
            if fresh := [link for link in links.split() if link not in seen]:
                seen.update(fresh)
                parts.append(" ".join([verb, *fresh]))
        if parts:
            kept.append(" ".join(parts))
    return kept


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
    handler = seen(payload)
    if refused(last := next((row for row in reversed(rows) if row["type"] in ("user", "assistant")), {})):
        if "handler_session" in handler:
            end_handler(handler | {"last_assistant_message": notice(last)}, lambda _: None)
        return None
    tries = resumes(rows)
    if tries >= len(OUTAGE_WAITS):
        if "handler_session" in handler:
            restart_handler(names[0], handler)
        return None
    since = datetime.now(UTC)
    if "handler_session" in handler:
        with state.edit(names[0]) as data:
            data["handlers"][handler["agent_id"]]["wakes"] = state.stamp(since + timedelta(minutes=OUTAGE_WAITS[tries]))
    time.sleep(OUTAGE_WAITS[tries] * 60)
    if any(state.parse_time(row["timestamp"]) > since for row in tail(transcript) if row["type"] in ("user", "assistant")):
        return None
    return RESUMED


def resumes(rows: list[dict]) -> int:
    """How many RESUMED prompts a transcript's rows hold since its last response that ended its turn whole, one for each resume an API error met again."""
    calm = max((at for at, row in enumerate(rows) if ended(row) and not died(row)), default=-1)
    return sum(row["type"] == "user" and RESUMED in str(row["message"]["content"]) for row in rows[calm + 1 :])


def restart_handler(name: str, payload: dict) -> None:
    """Start a handler whose turn an API error ended again once its four resumes met the error too, as the same handler on the brief its transcript opens with, kept for `drive handler-start`, so what it holds, its job, each post it made and each brief it was handed stay with it, those it took handed again, until a turn that answers posts its done; it reads as in flight until its new launch names its session, so the session it replaces, whose job `claude rm` takes, never frees what it holds and no brief meanwhile takes it. A restart the error meets again resumes and starts again the same way."""
    kept = brief_path(name, payload["agent_id"])
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(opening(Path(payload["agent_transcript_path"])))
    with state.edit(name) as data:
        entry = data["handlers"][payload["agent_id"]]
        entry.update(session=None, started=state.stamp(), handed=[*entry["took"], *entry["handed"]], took=[])
    state.detach(name, ["handler-start", payload["agent_id"]], "handlers.log")
    remove_session(name, payload)


def remove_session(name: str, payload: dict) -> None:
    """Remove a handler's ended session, which would otherwise sit idle holding its memory: `claude rm` takes the id of the background job Claude Code keeps for it. The session then stops hearing his DM, so cc-slack hands each reply of his it never took to the session holding his DM at once rather than once the process exits."""
    own, _ = background_job(payload["handler_session"])
    subprocess.Popen(["claude", "rm", own], cwd=Path(payload["cwd"]), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    slack.cc_slack("unfollow", "--channel", slack.dm(), "--session", payload["handler_session"], "--cwd", payload["cwd"])


def ended(row: dict) -> bool:
    """Whether a transcript row is a response's last: the model stopped rather than for a tool call or a paused server tool, so the session's turn is over and its next wake starts another."""
    return row.get("type") == "assistant" and row["message"].get("stop_reason") not in (None, "tool_use", "pause_turn")


def died(row: dict) -> bool:
    """Whether a transcript row is a response that ended on an API error or a refusal, the row Claude Code writes itself under model `<synthetic>` or one stopped for any reason but the end of its turn, so the agent's turn ended without its work and a SendMessage meets the same stop."""
    return ended(row) and (row["message"].get("stop_reason") != "end_turn" or row["message"].get("model") == SYNTHETIC)


def refused(row: dict) -> bool:
    """Whether a transcript row is a response a model's safeguards stopped, the one stop_reason the Messages API gives its safety classifiers alone: Claude Code's notice on that stop asks for it to go to the user and for no one to do the agent's work, so nothing resumes or restarts the agent, as the stop of an API error is."""
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
    with state.edit(names[0]) as data:
        if free(names[0], data, job_id, payload["agent_id"]):
            job = data["jobs"][job_id]
            job.update(agent=payload["agent_id"], where="local", session=payload["session_id"], undelivered=[], launching=None)
            if returned:
                job["resumed"] = state.stamp(resumed.astimezone())
            state.start(job)
            superseded.hear(data, job_id, prompt, state.parse_time(opening_row(transcript_of(payload))["timestamp"]))
