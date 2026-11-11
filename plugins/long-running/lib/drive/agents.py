import json
from pathlib import Path

from drive import state
from drive.brief import BRIEF_JOB


def transcript(payload: dict) -> Path:
    """The transcript of the agent whose step fired a hook, beside its session's."""
    return Path(payload["transcript_path"]).with_suffix("") / "subagents" / f"agent-{payload['agent_id']}.jsonl"


def brief(payload: dict) -> str:
    """The prompt the session started the agent whose step fired a hook with; empty for the session's own steps."""
    if not payload.get("agent_id") or not (path := transcript(payload)).is_file():
        return ""
    with path.open() as handle:
        content = json.loads(handle.readline())["message"]["content"]
    return content if isinstance(content, str) else " ".join(part.get("text", "") for part in content)


def free(data: dict, job_id: str) -> bool:
    """Whether an agent at work on the job takes it: the job exists, nobody stopped it, and no agent runs it already."""
    job = data["jobs"].get(job_id)
    return bool(job) and job["state"] != "stopped" and not (job["state"] == "running" and job["agent"])


def hold(payload: dict) -> None:
    """Record on the job its brief's Drive line names the agent whose step fired this hook, and run the job: a session runs the newest copy of each hook it registered at start but no hook registered since, so the drive hears of an agent from the hooks its own steps fire, its first step and its first after each wake."""
    match = BRIEF_JOB.search(brief(payload))
    names = state.session_drives(payload["session_id"]) if match else []
    if not names or not free(state.load(names[0]), match[1]):
        return
    with state.edit(names[0]) as data:
        if free(data, match[1]):
            data["jobs"][match[1]].update(
                agent=payload["agent_id"], where="local", session=payload["session_id"], state="running", updated=state.stamp()
            )
