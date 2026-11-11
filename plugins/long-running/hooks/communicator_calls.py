import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import state

SLACK = "mcp__plugin_cc-slack_cc-slack__"
DRIVE = "mcp__plugin_long-running_drive__"
OWN_TOOLS = {
    SLACK + "slack_dm_status",
    SLACK + "slack_reply",
    SLACK + "slack_react",
    SLACK + "slack_watch",
    DRIVE + "heard",
    DRIVE + "record",
    DRIVE + "job",
    "Agent",
    "SendMessage",
    "ToolSearch",
}
OWN_SKILLS = {"long-running", "long-running:long-running"}
OWN_COMMAND = re.compile(r"\s*(?:drive|cci|cc-slack)\s")
RESUME = re.compile(r"\bdrive job resume\b.*?\b(j\d+)\b")
FRESH_STATES = ("built", "done")
READ_WINDOW = timedelta(minutes=10)
TAIL_BYTES = 65536


def own_call(payload: dict) -> bool:
    """Whether a call is one the communicator makes itself: the ack, a +1, its DM's watch, Agent, SendMessage, ToolSearch, this skill, the drive's heard, record and job tools, and a `drive`, `cci` or `cc-slack` command in Bash, which starts it and stands in for a drive tool its session does not list."""
    name = payload["tool_name"]
    call = payload["tool_input"]
    if name == "Skill":
        return call.get("skill") in OWN_SKILLS
    if name == "Bash":
        return bool(OWN_COMMAND.match(call.get("command") or ""))
    return name in OWN_TOOLS


def communicator_call(payload: dict) -> str:
    return (
        f"Refused on purpose: the communicator does not run {payload['tool_name']} itself, since work in its turn holds his"
        " next message behind it. Its own calls are the ack, a +1, Agent, SendMessage to a builder of its own, ToolSearch,"
        " and the drive's heard, record and job tools. Start a background long-running:handler briefed with this step;"
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


def read_job(path: Path, job_id: str, job: dict) -> bool:
    """Whether the session's own transcript shows, in the last 10 minutes, a Bash call that read the job's transcript and did not fail: `drive job replies` on it, `cc-transcript` on its agent, or `orca orchestration worker-read` on its dispatch."""
    reads = [rf"\bdrive job replies\b.*\b{job_id}\b"]
    if job.get("agent"):
        reads.append(rf"\bcc-transcript\b.*{re.escape(job['agent'])}")
    if job.get("dispatch"):
        reads.append(rf"\bworker-read\b.*{re.escape(job['dispatch'])}")
    pattern = re.compile("|".join(reads), re.DOTALL)
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
    """Why a message to a running job waits for a read of its transcript: an absent message is no evidence, and one sent blind can repeat or contradict what the job did."""
    job = data["jobs"][job_id]
    if job["state"] != "running" or read_job(Path(payload["transcript_path"]), job_id, job):
        return None
    how = (
        f"`drive job replies {job_id}` or `orca orchestration worker-read --dispatch {job['dispatch']} --limit 50 --full --json`"
        if job.get("where") == "remote"
        else f"`cc-transcript` on agent {job['agent']}'s transcript"
    )
    return (
        f"Refused on purpose: your transcript shows no read of {job_id}'s transcript in the last 10 minutes, and a message sent"
        f" blind can repeat or contradict what the job did. Read it with {how}, then send."
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
