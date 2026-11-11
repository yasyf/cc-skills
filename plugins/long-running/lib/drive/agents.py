import json
import re
from pathlib import Path
from typing import Self

from drive import reads, state
from drive.brief import BRIEF_JOB, carried

RECORD = re.compile(r"^Record: (\d+)\b", re.MULTILINE)
HANDLER = "long-running:handler"


class Ruled(str):
    """A refusal Jev judges by the one sentence naming what its check protects against, not by its own words, which name the fix."""

    rule: str

    def __new__(cls, reason: str, rule: str) -> Self:
        made = super().__new__(cls, reason)
        made.rule = rule
        return made


class Unjudged(str):
    """A refusal its check decides by the call's shape alone, which Jev never weighs, so the same call gets the same verdict every time."""


def ruled(reason: str) -> dict:
    """The fields the capt-hook pack reads off a refusal: the rule Jev judges it by, or that no Jev judges it."""
    if isinstance(reason, Unjudged):
        return {"judged": False}
    return {"rule": reason.rule} if isinstance(reason, Ruled) else {}


def transcript(payload: dict) -> Path:
    """The transcript of the agent whose step fired a hook, beside its session's."""
    return Path(payload["transcript_path"]).with_suffix("") / "subagents" / f"agent-{payload['agent_id']}.jsonl"


def ended(row: dict) -> bool:
    """Whether a transcript row is a response's last: the model stopped on its own rather than for a tool call, so the session's turn is over and its next wake starts another."""
    return row.get("type") == "assistant" and row["message"].get("stop_reason") == "end_turn"


def brief(payload: dict) -> str:
    """The prompt the session started the agent whose step fired a hook with, the brief file it names and the cci record its Record: line names read in; empty for the session's own steps."""
    if not payload.get("agent_id") or not (path := transcript(payload)).is_file():
        return ""
    with path.open() as handle:
        content = json.loads(handle.readline())["message"]["content"]
    prompt = carried(content if isinstance(content, str) else " ".join(part.get("text", "") for part in content))
    return f"{prompt}\n{record(named[1])}" if (named := RECORD.search(prompt)) else prompt


def record(seq: str) -> str:
    """A cci record's whole text, its body file read in."""
    return reads.run(["cci", "show", seq], Path.cwd())


def free(data: dict, job_id: str) -> bool:
    """Whether an agent at work on the job takes it: the job exists, nobody stopped it, and no agent runs it already but one that ended with its session, which no role holds any more."""
    job = data["jobs"].get(job_id)
    return bool(job) and job["state"] != "stopped" and not (job["state"] == "running" and job["agent"] and not state.agent_gone(data, job))


def hold(payload: dict) -> None:
    """Record on the job its brief's Drive line names the agent whose step fired this hook, and run the job: a session runs the newest copy of each hook it registered at start but no hook registered since, so the drive hears of an agent from the hooks its own steps fire, its first step and its first after each wake. A handler is never held: it works one item for the communicator, and a job its brief names, by a `Job:` line, a brief file or the record it reads in, is that job's own agent's."""
    if payload.get("agent_type") == HANDLER:
        return
    match = BRIEF_JOB.search(brief(payload))
    names = state.session_drives(payload["session_id"]) if match else []
    if not names or not free(state.load(names[0]), match[1]):
        return
    with state.edit(names[0]) as data:
        if free(data, match[1]):
            data["jobs"][match[1]].update(
                agent=payload["agent_id"], where="local", session=payload["session_id"], state="running", updated=state.stamp()
            )
