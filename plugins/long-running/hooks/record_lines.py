import json
import re
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, limits, settled, slack, state

UNKEPT_ASK = (
    "refused: an ask record names the ASK `drive job ask` kept, as the attr `ask` set to the path it printed, and the"
    " communicator draws the card from that file; keep the question with `drive job ask <j> <file>`, then post this"
    " record again with that path"
)
UNJOBBED = "refused: an ask record names the job that kept its ASK, which his answer resumes; post it again with `--attr job=<j>`, the j in the ASK's file name"
SILENT_LANES = {"long-running:reader", "long-running:designer"}
TO_OWNER = (
    "refused: a {agent} posts nothing to him, since the communicator's handler is the one voice he hears; keep what you found"
    " with `drive job report <j>` in your return, or send a tool's defect to communicator@<drive>"
)
AGENT_DEFECT = (
    "refused: a handler posts no defect record, so only one fix starts; return a `DEFECT <tool>: <what"
    " happened, any refusal verbatim>` line, and the communicator starts the fix"
)
OPENING = re.compile(r"(?:POST|FYI)\b")
UNOPENED = "refused: a report's text opens `POST`, for a handler to post, or `FYI`, kept as context; add the one it is"
CATCH_UP = re.compile(r"Catch-up since\b", re.IGNORECASE)
THREADED_CATCH_UP = "refused: a catch-up goes at his DM root, never in a thread, since it covers many orders; post it again without `--attr thread`"
OFF_THREAD = (
    "refused: this report finishes {orders}, and thread {thread} is none of its threads: {threads}. Name one of those threads,"
    " or add this one with `drive order set <o> --thread {thread}`"
)
SECOND_REPORT = (
    "refused: the coordinator sends one report per thread per turn, a turn running from a wake to the end of its response,"
    " and this turn already sent one for thread {thread}; put everything for that thread in that one report, and what"
    " does not fit waits for the next wake"
)
TAP_ASKED = re.compile(r"\btap (?:Approve|Hold)\b|\b(?:needs?|waits? (?:on|for)) (?:your|his|a) tap\b", re.IGNORECASE)
WAITS_ON_HIM = re.compile(r"\bwait(?:s|ing)? (?:on|for) (?:him|his|your|you)\b|\bneeds? (?:his|your) (?:tap|pick|answer|approval)\b", re.IGNORECASE)
CARD_NAMED = re.compile(r"slack\.com/archives/|\{card:|\b[\w-]+:j\d+-\d+\b|github\.com/[^/\s]+/[^/\s]+/pull/\d+")
UNCARDED = "refused: this report says something waits on him and names no card; add its permalink, the PR whose card it is, or its waits id with `--attr waits=<drive>:<j>-<seq>`, so the handler links the card"
WAITS_ON_OTHER = re.compile(r"\bwait(?:s|ing)? on (?!(?:him|his|your|you|he)\b)\S", re.IGNORECASE)
WAITED = re.compile(r"\b\d+ ?(?:s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?|d|days?)\b|\bsince \d", re.IGNORECASE)
RUNNING = re.compile(r"\b(?:running|queued|idle|stopped|started|starting|in progress|not started)\b", re.IGNORECASE)
VAGUE_WAIT = "refused: a line that says something waits on X says how long it has waited and whether X is running, such as \"waits on CI, 40 min, run 123 running\"; add both"
STUCK = re.compile(r"\b(j\d+)\b[^.;\n]*?\b(?:stuck|silent|stalled|hung)\b", re.IGNORECASE)
READ_WINDOW = timedelta(minutes=10)
TRANSCRIPT_READ = ("cc-transcript", ".jsonl")
UNREAD_JOB = (
    "refused: this report calls {jobs} stuck or silent, and your transcript shows no read of {its} transcript in the last 10"
    " minutes; read it with `drive job replies <j>` or `cc-transcript`, then quote what it shows"
)
KIND_NAMED = re.compile(r"\bport of #\d+|\bhis order o\d+\b|\bdrive tooling\b", re.IGNORECASE)
UNKINDED = "refused: a ready record says which kind its PRs are: `his order o<n>` before his quoted words, `Drive tooling: …`, or `port of #<n>`; add the one it is"
LIMIT_ATTR = "max_words"
LIFTED_LIMIT = (
    "refused: a record carries no `max_words` attr, since nothing reads it and no record lifts his limits: a line in his"
    " thread is at most {line} words and a summary of two or more features at most {summary}; post it again without it"
)
TAIL_BYTES = 4 << 20


def kind(record: dict) -> str:
    return (record.get("kind") or "").lower()


def attrs(record: dict) -> dict:
    return record.get("attrs") or {}


def listed(value: object) -> list[str]:
    return [] if value is None else [str(item) for item in (value if isinstance(value, list) else [value])]


def addressed(record: dict, lane: str) -> bool:
    return any(entry.split("@", 1)[0] == lane for entry in listed(record.get("to")))


def unkept(record: dict, names: list[str]) -> str | None:
    """Why an ask record may not go out: it names no ASK `drive job ask` kept, which the communicator draws its card from, or no job his answer resumes."""
    if kind(record) != "ask":
        return None
    if not settled.kept(names, attrs(record).get("ask")):
        return UNKEPT_ASK
    return None if attrs(record).get("job") else UNJOBBED


def untapped(record: dict, prs: dict[str, dict]) -> str | None:
    """Why a ready record, which asks him to tap each PR it names, or a report or ask that asks for a tap, names a PR the drive owes no tap on."""
    if kind(record) not in ("ready", "report", "ask") or (kind(record) != "ready" and not TAP_ASKED.search(record.get("text") or "")):
        return None
    named = [record.get("text") or "", record.get("body") or "", record.get("url") or "", *map(str, record.get("pr") or [])]
    return limits.tap_refusal("\n".join(named), prs)


def unkinded(record: dict) -> str | None:
    """Why a ready record does not say which kind of work its PRs are, the first thing he asks of a PR card."""
    return UNKINDED if kind(record) == "ready" and not KIND_NAMED.search(record.get("text") or "") else None


def completed(names: list[str], drive: dict, record: dict) -> dict:
    """The record with what the drive already knows filled in: an ask's kept file as its ask attr when it attaches that file as its path, and a ready record's kind, read from the order its order attr names, when its text names none."""
    named = attrs(record)
    if kind(record) == "ask" and not named.get("ask") and settled.kept(names, record.get("path")):
        return record | {"attrs": named | {"ask": record["path"]}}
    order = drive["orders"].get(named.get("order") or "")
    if kind(record) != "ready" or order is None or KIND_NAMED.search(record.get("text") or ""):
        return record
    words = order["text"] if order["text"].startswith(state.TOOLING) else f'his order {named["order"]} "{order["text"]}"'
    return record | {"text": f"{record['text']}; {words}"}


def lifted(record: dict) -> str | None:
    """Why a record names its own word limit, which nothing reads and his limits for a post never yield to."""
    if LIMIT_ATTR not in attrs(record):
        return None
    return LIFTED_LIMIT.format(line=limits.LINE_WORDS, summary=limits.SUMMARY_WORDS)


def misaddressed(payload: dict, record: dict) -> str | None:
    """Why an agent's record goes to the wrong place: a reader or designer posting to him, or a handler posting a defect its return carries."""
    if (agent := payload.get("agent_type")) in SILENT_LANES and addressed(record, "owner"):
        return TO_OWNER.format(agent=agent.removeprefix("long-running:"))
    if payload.get("agent_type") == agents.HANDLER and kind(record) == "defect":
        return AGENT_DEFECT
    return None


def report_refusals(payload: dict, drive: dict, role: str | None, records: list[dict], index: int) -> Iterator[str | None]:
    """Why a report to the communicator does not go out as written, each check run only once the ones before it pass."""
    record = records[index]
    text = (record.get("text") or "").strip()
    thread = attrs(record).get("thread")
    yield None if OPENING.match(text) else UNOPENED
    yield THREADED_CATCH_UP if thread and (attrs(record).get("catch_up") or CATCH_UP.search(text)) else None
    yield off_thread(drive, record)
    if text.startswith("POST") and (long := limits.post_refusal({"text": OPENING.sub("", text, count=1).lstrip()})):
        yield f"refused: a POST report's facts become one post, so they keep its length: {long.removeprefix('refused: ')}"
    yield UNCARDED if WAITS_ON_HIM.search(text) and not attrs(record).get("waits") and not CARD_NAMED.search(f"{text}\n{record.get('body') or ''}") else None
    yield VAGUE_WAIT if WAITS_ON_OTHER.search(text) and not (WAITED.search(text) and RUNNING.search(text)) else None
    if thread and role == "coordinator" and not payload.get("agent_id"):
        yield second_report(payload, records, index, thread)
    if STUCK.search(text):
        yield unread_job(payload, text)


def off_thread(drive: dict, record: dict) -> str | None:
    """Why a report that finishes an order, or delivers a part of one, names a thread that is none of that order's, where the line would keep no promised time."""
    named = attrs(record)
    if not (thread := named.get("thread")) or not (named.get("close") or named.get("part")):
        return None
    job = drive["jobs"].get(named.get("job") or "")
    orders = [order for order in [*re.split(r"[\s,]+", named.get("close") or ""), job and job["order"]] if order and order in drive["orders"]]
    threads = list(dict.fromkeys(ts for order in orders for _, ts in slack.order_threads(drive, drive["orders"][order])))
    if not orders or thread in threads:
        return None
    return OFF_THREAD.format(orders=", ".join(orders), thread=thread, threads=", ".join(threads) or "none yet")


def transcript_of(payload: dict) -> Path:
    return agents.transcript(payload) if payload.get("agent_id") else Path(payload["transcript_path"])


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


def report_threads(tool_input: dict) -> set[str]:
    return {attrs(record).get("thread") for record in tool_input.get("records") or [tool_input] if kind(record) == "report" and addressed(record, "communicator")} - {None}


def second_report(payload: dict, records: list[dict], index: int, thread: str) -> str | None:
    """Why the coordinator's report for a thread waits for its next turn: the batch, or a post since its last response ended, already carried one for that thread."""
    if thread in report_threads({"records": records[:index]}):
        return SECOND_REPORT.format(thread=thread)
    sent: dict[str, set[str]] = {}
    posted: set[str] = set()
    for row in tail(transcript_of(payload)):
        if agents.ended(row):
            sent, posted = {}, set()
        for part in parts(row):
            if part.get("type") == "tool_use" and part.get("name") == payload["tool_name"] and part.get("id") != payload.get("tool_use_id"):
                sent[part["id"]] = report_threads(part.get("input") or {})
            elif part.get("type") == "tool_result" and part.get("tool_use_id") in sent and not part.get("is_error"):
                posted.add(part["tool_use_id"])
    return SECOND_REPORT.format(thread=thread) if any(thread in sent[call] for call in posted) else None


def unread_job(payload: dict, text: str) -> str | None:
    """Why a report calling a job stuck or silent waits for a read of its transcript: the poster's own calls in the last 10 minutes show none."""
    since = datetime.now(UTC) - READ_WINDOW
    calls = " ".join(
        json.dumps(part.get("input"))
        for row in tail(transcript_of(payload))
        if row.get("timestamp") and datetime.fromisoformat(row["timestamp"]) > since
        for part in parts(row)
        if part.get("type") == "tool_use"
    )
    jobs = [job for job in dict.fromkeys(STUCK.findall(text)) if f"replies {job}" not in calls and not any(read in calls for read in TRANSCRIPT_READ)]
    if not jobs:
        return None
    return UNREAD_JOB.format(jobs=", ".join(jobs), its="their" if len(jobs) > 1 else "its")


def refusals(payload: dict, names: list[str], records: list[dict]) -> Iterator[str | None]:
    """Each check of each record in turn, so a refusal stops the rest from running."""
    drive = state.load(names[0])
    role = state.session_role(drive, payload.get("session_id"))
    prs = {key: pr for name in names for key, pr in state.load(name)["prs"].items()}
    for index, record in enumerate(records):
        yield misaddressed(payload, record)
        yield lifted(record)
        yield limits.misnamed(kind(record), attrs(record))
        yield unkept(record, names)
        yield unkinded(record)
        yield untapped(record, prs)
        if kind(record) == "report" and addressed(record, "communicator"):
            yield from report_refusals(payload, drive, role, records, index)


def readdressed(drives: dict[str, dict], record: dict) -> dict:
    """The communicator's owner, answer, posted or refused record with each coordinator lane it names at one of its drives replaced by the lane of that drive's coordinator session hearing the order it serves."""
    entries = listed(record.get("to"))
    to = state.routed(drives, kind(record), attrs(record), entries)
    return record if to == entries else record | {"to": to}


def decision(payload: dict) -> dict | None:
    """What a drive session's post becomes: refused with the fix while a record in it breaks a rule its kind keeps, else every record in it completed from what the drive knows and one line, the rest in its body for whoever acts on it, and each of the communicator's records for a coordinator sent to the coordinator session hearing the order it serves."""
    names = state.session_drives(payload.get("session_id") or "")
    if not names:
        return None
    tool_input = payload["tool_input"]
    given = tool_input.get("records") or [tool_input]
    drive = state.load(names[0])
    records = [completed(names, drive, record) for record in given]
    if refused := next(filter(None, refusals(payload, names, records)), None):
        return {"permissionDecision": "deny", "permissionDecisionReason": refused}
    drives = state.communicated(names, payload.get("session_id"))
    made = [limits.shortened(routed) or routed for routed in (readdressed(drives, record) for record in records)]
    if made == given:
        return None
    if tool_input.get("records"):
        return {"updatedInput": tool_input | {"records": made}}
    return {"updatedInput": made[0]}


def main() -> None:
    if made := decision(json.load(sys.stdin)):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **made}}))


if __name__ == "__main__":
    main()
