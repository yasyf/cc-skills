import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import render, state

OWN_TOOLS = {
    *(render.SLACK_TOOLS + tool for tool in render.COMMUNICATOR_SLACK),
    *(render.DRIVE_TOOLS + tool for tool in render.COMMUNICATOR_DRIVE),
    *render.COMMUNICATOR_AGENTS,
}
PICKUP = render.SLACK_TOOLS + "slack_pickup"
OWN_SKILLS = {"long-running", "long-running:long-running"}
OWN_COMMAND = re.compile(r"\s*(?:drive|cci|cc-slack)\s")
RESUME = re.compile(r"\bdrive job resume\b.*?\b(j\d+)\b")
FRESH_STATES = ("built", "done")
READ_WINDOW = timedelta(minutes=10)
TAIL_BYTES = 65536


def own_call(payload: dict) -> bool:
    """Whether a call is one the communicator makes itself: the ack, a +1, its DM's watch, Agent, SendMessage, ToolSearch, this skill, the drive's say, heard, record, order and job tools, and a `drive`, `cci` or `cc-slack` command in Bash, which starts it and stands in for a drive tool its session does not list."""
    name = payload["tool_name"]
    call = payload["tool_input"]
    if name == "Skill":
        return call.get("skill") in OWN_SKILLS
    if name == "Bash":
        return bool(OWN_COMMAND.match(call.get("command") or ""))
    return name in OWN_TOOLS


def communicator_call(payload: dict) -> str:
    if payload["tool_name"] == PICKUP:
        return f"Refused on purpose: {render.EYES} Go on with the ack."
    return (
        f"Refused on purpose: the communicator does not run {payload['tool_name']} itself, since work in its turn holds his"
        f" next message behind it: a read, draft or post of any kind is a handler's. Its own calls are {render.OWN_CALLS}."
        f" Start a background long-running:handler briefed with this step in its lines, {', '.join(render.HANDLER_LABELS)};"
        " it does the read, draft or post and returns one line."
    )


def target(payload: dict, data: dict) -> str | None:
    """The job a coordinator's message goes to: a local agent's by its SendMessage `to`, a remote lane's by `drive job resume`."""
    call = payload["tool_input"]
    if payload["tool_name"] == "SendMessage":
        return state.agent_job(data, call.get("to"))
    if payload["tool_name"] == "Bash" and (resumed := RESUME.search(call.get("command") or "")):
        return resumed[1] if resumed[1] in data["jobs"] else None
    return None


def resumed_builder(payload: dict, data: dict, job_id: str) -> str | None:
    """Why a SendMessage to a builder that returned BUILT or DONE carries no PR event: a resumed builder re-reads everything it ever did on every event, so a fresh one takes it; an incident's job is resumed for every event."""
    job = data["jobs"][job_id]
    if payload["tool_name"] != "SendMessage" or job["kind"] != "builder" or job["state"] not in FRESH_STATES or data["orders"][job["order"]]["incident"]:
        return None
    return (
        f"Refused on purpose: {job_id}'s builder already returned ({job['state']}), and a PR event after a builder returns goes"
        " to a fresh builder, since a resumed one re-reads everything it ever did on every event. Run `drive job add builder`"
        f" for order {job['order']} and its parts, `drive pr add <url> --job <new>` for each PR it takes over, brief it with a"
        f" `Handoff:` line naming the event and {job_id}'s transcript under `Transcripts:`, then"
        f' `drive job set {job_id} --state stopped --said "handed to <new>"`. His answer to an ASK goes back by SendMessage'
        " while the job waits."
    )


def recent_rows(path: Path, since: datetime) -> list[dict]:
    """The transcript's rows written since, read back from its end, since a coordinator's transcript grows past 300 MB."""
    with path.open("rb") as handle:
        end = handle.seek(0, os.SEEK_END)
        span = TAIL_BYTES
        while True:
            start = max(0, end - span)
            handle.seek(start)
            rows = [row for line in handle.read().splitlines()[1 if start else 0 :] if (row := json.loads(line)).get("timestamp")]
            if not start or (rows and datetime.fromisoformat(rows[0]["timestamp"]) < since):
                return [row for row in rows if datetime.fromisoformat(row["timestamp"]) >= since]
            span *= 2


def transcript_read(job_id: str, job: dict) -> re.Pattern[str]:
    """A command that reads the job's transcript: `drive job replies` on it, `cc-transcript` on its agent, or `orca orchestration worker-read` on its dispatch."""
    reads = [rf"\bdrive job replies\b.*\b{job_id}\b"]
    if job.get("agent"):
        reads.append(rf"\bcc-transcript\b.*{re.escape(job['agent'])}")
    if job.get("dispatch"):
        reads.append(rf"\bworker-read\b.*{re.escape(job['dispatch'])}")
    return re.compile("|".join(reads), re.DOTALL)


def read_job(path: Path, job_id: str, job: dict) -> bool:
    """Whether the session's own transcript shows, in the last 10 minutes, a Bash call that read the job's transcript and did not fail."""
    pattern = transcript_read(job_id, job)
    calls: set[str] = set()
    for row in recent_rows(path, datetime.now(UTC) - READ_WINDOW):
        content = (row.get("message") or {}).get("content")
        for part in content if isinstance(content, list) else []:
            if part.get("type") == "tool_use" and part.get("name") == "Bash" and pattern.search(part["input"].get("command") or ""):
                calls.add(part["id"])
            elif part.get("type") == "tool_result" and part.get("tool_use_id") in calls and not part.get("is_error"):
                return True
    return False


def unread_job(payload: dict, data: dict, job_id: str) -> str | None:
    """Why a message to a running job waits for a read of its transcript, earlier in the same command or in the last 10 minutes, when it tells the job what state it is in: an absent message is no evidence, and a state guessed reaches the job as fact. Jev judges whether the message claims a state, so a ruling, an answer or a refusal relayed goes through."""
    job = data["jobs"][job_id]
    call = payload["tool_input"]
    text = str(call.get("message") or "") if payload["tool_name"] == "SendMessage" else call.get("command") or ""
    resumed = RESUME.search(text)
    if job["state"] != "running" or (resumed and transcript_read(job_id, job).search(text[: resumed.start()])):
        return None
    if read_job(Path(payload["transcript_path"]), job_id, job):
        return None
    how = (
        f"`drive job replies {job_id}` or `orca orchestration worker-read --dispatch {job['dispatch']} --limit 50 --full --json`"
        if job.get("where") == "remote"
        else f"`cc-transcript` on agent {job['agent']}'s transcript"
    )
    return (
        f"A message that tells {job_id} it stopped, stalled, went quiet or is stuck needs a read of its transcript first, and"
        " your transcript shows none in the last 10 minutes, so that state is a guess that reaches the job as fact. Read it"
        f" with {how}, in an earlier call or before the resume in this command, then send."
    )


def deny_reason(payload: dict) -> str | None:
    """Why a drive session's own call does not run: the communicator's turn holds only the ack and what starts a handler, and the coordinator sends no PR event to a builder that returned and messages no running job before it reads that job's transcript."""
    if payload.get("agent_id"):
        return None
    session = payload.get("session_id") or ""
    names = state.session_drives(session)
    if not names:
        return None
    data = state.load(names[0])
    role = state.session_role(data, session)
    if role == "communicator":
        return None if own_call(payload) else communicator_call(payload)
    if (job_id := target(payload, data)) is None:
        return None
    return resumed_builder(payload, data, job_id) or unread_job(payload, data, job_id)


def main() -> None:
    if reason := deny_reason(json.load(sys.stdin)):
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )


if __name__ == "__main__":
    main()
