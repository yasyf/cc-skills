import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, state

RESULT_CHARS = 500
HANDLER_FACTS = 3
HANDLER_RESULT = re.compile(r"(POSTED|RECORDED|NOTHING|MISSING)\b")
HANDLER_OPENING = re.compile(r"(?:POSTED|RECORDED|NOTHING|MISSING|DEFECT)\b")
REFUSAL = r"(?:access denied|permission denied|forbidden|\b403\b|unauthori[sz]ed)"
FAILURE = r"\b(?:couldn't|could not|can't|cannot|unable to|failed|refused)\b"
REFUSED_READ = re.compile(
    rf"{FAILURE}.{{0,60}}{REFUSAL}|{REFUSAL}.{{0,60}}{FAILURE}|\bdenied when\b|\bgot an? 403\b|(?-i:\b(?:UNPROVEN|BLOCKED)\b).*{REFUSAL}",
    re.IGNORECASE,
)
STAFF_ROUTES = ("break-glass", "db-read", "pass-box")


def block_reason(payload: dict) -> str | None:
    """Why a drive session's background agent, a handler's own session included, may not stop yet: its final message lands whole in that session's context, a handler's as its cci record, so it is one line, kept first with `drive job report` by every job's agent."""
    if payload.get("stop_hook_active"):
        return None
    session = payload.get("session_id") or ""
    names = state.session_drives(session)
    if not names:
        return None
    data = state.load(names[0])
    role = state.session_role(data, session)
    message = (payload.get("last_assistant_message") or "").strip()
    lines = message.splitlines()
    job_id = state.agent_job(data, payload.get("agent_id"))
    if len(lines) <= 1 and len(message) <= RESULT_CHARS:
        handler = payload.get("agent_type") == agents.HANDLER
        return (
            unread(payload, data, handler, job_id, message)
            or unkept(names[0], data, job_id, message)
            or overfull(handler, message)
            or unopened(handler, message)
        )
    keep = (
        f"keep your whole return first, if you have not, by piping it into `drive job report {job_id or '<your job>'} -`"
        " from a quoted heredoc, then end"
        if role == "coordinator" or job_id
        else "end"
    )
    return agents.Unjudged(
        f"Your final message is {len(lines)} {'line' if len(lines) == 1 else 'lines'} and {len(message)} characters,"
        f" and it lands whole in the {role}'s"
        f" context, which every compaction has to clear. A final message is one line of at most {RESULT_CHARS}"
        f" characters: {keep} with that one line alone."
    )


def unkept(name: str, data: dict, job_id: str | None, line: str) -> str | None:
    """Why a job's agent may not end on its one line yet: its whole return, a line per fact, is not kept in the drive, and only `drive job report` sets a job's state from it, so whoever acts on the job reads only that line and the job stays running; a reader's one line on an answer or incident order can be its whole return, and an ASK ends any job but a running builder."""
    if not job_id:
        return None
    job = data["jobs"][job_id]
    if line.startswith("ASK "):
        return unbuilt(job_id, job)
    if job["state"] != "running" and state.kept_return(name, job_id, job, line):
        return None
    order = data["orders"][job["order"]]
    researched = job["kind"] == "reader" and order["kind"] == "build" and not order["incident"]
    block = " with its `Researched:` block, which the order's builder is briefed from," if researched else ""
    return agents.Unjudged(
        f"Refused on purpose: {job_id}'s return is not kept in the drive, so its state is unset and whoever acts on it"
        f" would read only this line. Pipe your whole return{block} into `drive job report {job_id} -` from a quoted"
        " heredoc, its first line exactly this one, then end again with this one line; when it refuses, fix what its"
        " refusal names and pipe it again."
    )


def unbuilt(job_id: str, job: dict) -> str | None:
    """Why a running builder may not end on the ASK `drive job ask` kept: his card's recommended option is built in the same run while he decides, so the builder ends on BUILT, or PUSHED on an open incident, and only an ASK his words ask no build for ends it waiting, once `drive job report` keeps it."""
    if job["kind"] != "builder" or job["state"] != "running":
        return None
    return agents.Unjudged(
        f"Refused on purpose: {job_id} put his card up and built nothing, and the recommended option is built in this run while he"
        f" decides. Build it on your branch, push it with `ccx vcs ship --no-pr`, keep `BUILT <branch>, waits on <the card's waits id,"
        f" which drive status {job_id} shows>` and its lines with `drive job report {job_id} -`, and end on that line; on an open"
        " incident open its PR, which lands on that option while his card stays open, and end on `PUSHED <its url>`. Only when his"
        f" words ask for options and no build, or the recommended option is no change you build, keep this ASK with `drive job report"
        f" {job_id} -` and end on it again."
    )


def overfull(handler: bool, line: str) -> str | None:
    """Why a handler may not end on its line yet: past its results it carries at most three facts, since the line lands whole in the communicator's context."""
    if not handler or "DEFECT " in line:
        return None
    facts = [part for part in line.split(";") if part.strip() and not HANDLER_RESULT.match(part.strip())]
    if len(facts) <= HANDLER_FACTS:
        return None
    return agents.Unjudged(
        f"Refused on purpose: your line carries {len(facts)} facts past its results, and a handler's carries at most"
        f" {HANDLER_FACTS}, since it lands whole in the communicator's context. Cut it to the {HANDLER_FACTS} his next"
        " message needs that Slack and the drive do not already hold, each after `; `, then end again with that line."
    )


def unopened(handler: bool, line: str) -> str | None:
    """Why a handler may not end on a line that opens with words: only the communicator reads it, so it opens with what the handler did, never a message to him."""
    if not handler or HANDLER_OPENING.match(line):
        return None
    return agents.Unjudged(
        "Refused on purpose: a handler's line opens with what it did, `POSTED <permalink>` for each post, `RECORDED <seq>`"
        " for each record, `NOTHING <why>` when it posted and recorded nothing, `MISSING <path>` or `DEFECT <tool>: <its refusal>`,"
        " since only the communicator reads it and his answer is already in Slack or a record. Open the line with those, then"
        " end again with that line."
    )


def unread(payload: dict, data: dict, handler: bool, job_id: str | None, line: str) -> str | None:
    """Why a handler or reader may not end on a refused read: his asking authorizes the read, so a refusal sends it down the staff route that opens it before it gives up."""
    if not REFUSED_READ.search(line) or not (handler or (job_id and data["jobs"][job_id]["kind"] == "reader")):
        return None
    steps = (json.loads(entry).get("message") or {} for entry in agents.transcript(payload).read_text().splitlines())
    calls = (json.dumps(block["input"]) for step in steps for block in step.get("content") or [] if isinstance(block, dict) and block.get("type") == "tool_use")
    if any(route in call for call in calls for route in STAFF_ROUTES):
        return None
    return (
        "Refused on purpose: your line ends on a refused read, and the read his words or the order name is authorized"
        " by his asking. In this turn take the staff route that opens it: an admin view, the"
        " `db-read` skill, or `drive break-glass`, which runs it under this Mac's machine profile with no sign-in, and for a team's"
        " data, which none of those reads, a service account on a pass box, by docs/pass-boxes.md in Forge-AI/monorepo. Then read"
        " it and end on what it says; a read every route refuses goes on `UNPROVEN:` with each refusal."
    )


def main() -> None:
    payload = json.load(sys.stdin)
    if payload["hook_event_name"] == "StopFailure":
        if wake := agents.resume(payload):
            print(wake, file=sys.stderr)
            sys.exit(2)
        return
    payload = agents.seen(payload)
    if "handler_session" in payload:
        reason = agents.end_handler(payload, block_reason)
    else:
        agents.hold(payload)
        reason = block_reason(payload)
    if reason:
        print(json.dumps({"decision": "block", "reason": reason} | agents.ruled(reason)))


if __name__ == "__main__":
    main()
