import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import state

BRIEF_JOB = re.compile(r"^Drive:[^\n]*?\bjob (j\d+)\b", re.MULTILINE)


def bind(payload: dict) -> None:
    """Record on its job the agent an Agent call started for the job its brief's Drive line names, and mark a job running again when SendMessage wakes its agent."""
    names = state.session_drives(payload.get("session_id") or "")
    if not names:
        return
    tool_input = payload["tool_input"]
    if payload["tool_name"] == "Agent":
        match = BRIEF_JOB.search(tool_input.get("prompt") or "")
        if match:
            with state.edit(names[0]) as data:
                data["jobs"][match[1]].update(
                    agent=payload["tool_response"]["agentId"], where="local", session=payload["session_id"], state="running", updated=state.stamp()
                )
        return
    data = state.load(names[0])
    job_id = state.agent_job(data, tool_input.get("to"))
    if job_id and data["jobs"][job_id]["state"] != "running":
        with state.edit(names[0]) as data:
            data["jobs"][job_id].update(state="running", updated=state.stamp())


def main() -> None:
    bind(json.load(sys.stdin))


if __name__ == "__main__":
    main()
