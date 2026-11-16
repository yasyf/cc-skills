import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, orca, providers, state

RESULT_CHARS = 500
REFUSAL = r"(?:access denied|permission denied|forbidden|\b403\b|unauthori[sz]ed)"
FAILURE = r"\b(?:couldn't|could not|can't|cannot|unable to|failed|refused)\b"
REFUSED_READ = re.compile(
    rf"{FAILURE}.{{0,60}}{REFUSAL}|{REFUSAL}.{{0,60}}{FAILURE}|\bdenied when\b|\bgot an? 403\b|(?-i:\b(?:UNPROVEN|BLOCKED)\b).*{REFUSAL}",
    re.IGNORECASE,
)


def block_reason(payload: dict) -> str | None:
    """Why a drive session's background agent may not stop yet: its final message lands whole in that session's context, so it is one line, kept first with `drive job report` by every job's agent."""
    if payload.get("stop_hook_active"):
        return None
    session = payload.get("session_id") or ""
    names = state.session_drives(session)
    if not names:
        return None
    agent = payload.get("agent_id")
    data = state.load(names[0], ("name", "roles", "subcoordinators", "communicators", "jobs", "orders")) if agent else state.saved(names[0])
    role = state.session_role(data, session)
    message = (payload.get("last_assistant_message") or "").strip()
    lines = message.splitlines()
    job_id = state.agent_job(data, agent) if agent else None
    if len(lines) <= 1 and len(message) <= RESULT_CHARS:
        if skipped := unread(payload, data, job_id, message):
            print(skipped, file=sys.stderr)
        return unkept(names[0], data, job_id, message)
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
    """Why a job's agent may not end on its one line yet: its whole return, a line per fact, is not kept in the drive, and only `drive job report` sets a job's state from it, so whoever acts on the job reads only that line and the job stays running; a reader's one line on an answer or incident order can be its whole return, and an ASK ends any job."""
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


def unbuilt(job_id: str, job: dict) -> None:
    """Let a running builder end on the ASK `drive job ask` kept, noting on stderr that it built nothing while he decides, since building the card's recommended option in the same run is usually what he wants."""
    if job["kind"] == "builder" and job["state"] == "running":
        print(f"one_line_result: {job_id} ends on an ASK and built nothing; building the recommended option while he decides, ending on BUILT or PUSHED, usually saves him a round trip.", file=sys.stderr)


def unread(payload: dict, data: dict, job_id: str | None, line: str) -> str | None:
    """The note a reader ending on a refused read gets on stderr: his asking authorizes the read, and a staff route usually opens it."""
    if not REFUSED_READ.search(line) or not (job_id and data["jobs"][job_id]["kind"] == "reader"):
        return None
    steps = (json.loads(entry).get("message") or {} for entry in agents.transcript(payload).read_text().splitlines())
    calls = (json.dumps(block["input"]) for step in steps for block in step.get("content") or [] if isinstance(block, dict) and block.get("type") == "tool_use")
    guard = providers.of(data).data
    if any(route in call for call in calls for route in {"break-glass", *guard.staff_routes}):
        return None
    return (
        "one_line_result: your line ends on a refused read, and the read his words or the order name is authorized"
        f" by his asking. In this turn take the staff route that opens it: {guard.staff_note}. Then read"
        " it and end on what it says; a read every route refuses goes on `UNPROVEN:` with each refusal."
    )


def main() -> None:
    payload = json.load(sys.stdin)
    if payload["hook_event_name"] == "StopFailure":
        if hit := agents.limit_hit(payload):
            agents.keep_limit_hit(payload, hit | {"dismissed": orca.dismiss_limit_dialog()})
        if wake := agents.resume(payload):
            print(wake, file=sys.stderr)
            sys.exit(2)
        return
    payload = agents.seen(payload)
    if "job_session" in payload and (pointed := agents.take_pointed(*os.environ[state.JOB_ENV].split("/")[:2])):
        reason = agents.Unjudged(f"Refused on purpose: {agents.pointed(pointed)}")
    else:
        agents.hold(payload)
        reason = block_reason(payload)
    if reason:
        print(json.dumps({"decision": "block", "reason": reason} | agents.ruled(reason)))


if __name__ == "__main__":
    main()
