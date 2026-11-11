import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, state

RESULT_CHARS = 500


def block_reason(payload: dict) -> str | None:
    """Why a drive session's background agent may not stop yet: its final message lands whole in that session's context, so it is one line."""
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
    if len(lines) <= 1 and len(message) <= RESULT_CHARS:
        return None
    job = state.agent_job(data, payload.get("agent_id")) or "<your job>"
    keep = (
        f"keep your whole return first, if you have not, by piping it into `drive job report {job} -` from a quoted"
        " heredoc, then end"
        if role == "coordinator"
        else "end"
    )
    return (
        f"Your final message is {len(lines)} {'line' if len(lines) == 1 else 'lines'} and {len(message)} characters,"
        f" and it lands whole in the {role}'s"
        f" context, which every compaction has to clear. A final message is one line of at most {RESULT_CHARS}"
        f" characters: {keep} with that one line alone."
    )


def take_result(payload: dict) -> None:
    """Set the job a drive session's background agent holds from the line it returned, so nobody types its state."""
    names = state.session_drives(payload.get("session_id") or "")
    lines = (payload.get("last_assistant_message") or "").strip().splitlines()
    if not names or not lines:
        return
    if job_id := state.agent_job(state.load(names[0]), payload.get("agent_id")):
        with state.edit(names[0]) as data:
            state.take_result(data, job_id, lines[0].strip())


def main() -> None:
    payload = json.load(sys.stdin)
    agents.hold(payload)
    if reason := block_reason(payload):
        print(json.dumps({"decision": "block", "reason": reason}))
        return
    take_result(payload)


if __name__ == "__main__":
    main()
