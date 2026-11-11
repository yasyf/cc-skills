import json
import re
import subprocess
from pathlib import Path
from typing import Self

from drive import reads, render, state
from drive.brief import BRIEF_JOB, carried

RECORD = re.compile(r"^Record: (\d+)\b", re.MULTILINE)
HANDLER = "long-running:handler"
HIS_WORDS = "Message (his words)"
SCRATCH = re.compile(r"Scratch:\s*(\S+)")
LABEL = re.compile(rf"^({'|'.join(re.escape(label) for label in render.HANDLER_LABELS)}):")
HANDLER_SETTINGS = {"bashOutputMaxChars": 6000, "outputStyle": "default", "env": {"CLAUDE_CODE_SILENT_TURN_REMINDER": "0"}}


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
    """The transcript of the agent whose step fired a hook: a handler's own session's, else the one beside its session's."""
    if path := payload.get("agent_transcript_path"):
        return Path(path)
    return Path(payload["transcript_path"]).with_suffix("") / "subagents" / f"agent-{payload['agent_id']}.jsonl"


def seen(payload: dict) -> dict:
    """A hook's payload as the drive's checks read it: a handler's own `claude --bg` session reads as an agent of the communicator's session, as a handler ran before it had a session of its own, so every check on a handler binds it unchanged."""
    if payload.get("agent_id") or payload.get("agent_type") != HANDLER or not (names := state.session_drives(payload["session_id"])):
        return payload
    data = state.saved(names[0])
    return payload | {
        "session_id": data["roles"]["communicator"]["session"],
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
        elif line.strip() and not (label == HIS_WORDS or (label == "Context" and (context := context + 1) <= 3)):
            return line.strip()
    return None


def scratch(prompt: str) -> Path | None:
    """The folder a drive agent's brief names as its Scratch, which must exist before the agent's first write."""
    match = SCRATCH.search(prompt)
    return Path(match[1].rstrip(".,;")).expanduser() if match else None


def start_handler(name: str, handler: str, prompt: str) -> None:
    """Start a handler as its own `claude --bg` session on its agent definition, which sets its model, in this session's checkout, its SessionStart hook binding it to the drive by the id in its environment."""
    settings = HANDLER_SETTINGS | {"env": HANDLER_SETTINGS["env"] | {state.HANDLER_ENV: f"{name}/{handler}"}}
    argv = ["claude", "--bg", "--agent", HANDLER, "--permission-mode", "bypassPermissions", "--settings", json.dumps(settings), "-n", f"{name} handler {handler}", prompt]
    reads.run(argv, Path.cwd())


def bind_handler(payload: dict, handler: str) -> None:
    """Bind a handler's session to the drive the moment it starts, before its first step, so every check on the communicator's handlers finds it."""
    name, _, handler_id = handler.partition("/")
    with state.edit(name) as data:
        data["handlers"][handler_id].update(session=payload["session_id"], transcript=payload["transcript_path"])


def end_handler(payload: dict) -> None:
    """Hand the line a handler ended on to the communicator as one cci record and remove its session, which would otherwise sit idle holding its memory: `claude rm` takes the short id `claude agents` lists for it. A job the handler held keeps its transcript's path, since the drive forgets the handler."""
    name = state.session_drives(payload["session_id"])[0]
    with state.edit(name) as data:
        del data["handlers"][payload["agent_id"]]
        if job_id := state.agent_job(data, payload["agent_id"]):
            data["jobs"][job_id]["transcript"] = payload["agent_transcript_path"]
    line = (payload.get("last_assistant_message") or "").strip()
    cwd = Path(payload["cwd"])
    reads.run(["cci", "post", "--drive", name, "--lane", "handler", "--kind", "done", "--to", f"communicator@{name}", "--id", f"handler-{payload['agent_id']}", "--text", line], cwd)
    listed = json.loads(reads.run(["claude", "agents", "--json"], cwd))
    own = next(row["id"] for row in listed if row.get("sessionId") == payload["handler_session"])
    subprocess.Popen(["claude", "rm", own], cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def ended(row: dict) -> bool:
    """Whether a transcript row is a response's last: the model stopped on its own rather than for a tool call, so the session's turn is over and its next wake starts another."""
    return row.get("type") == "assistant" and row["message"].get("stop_reason") == "end_turn"


def brief(payload: dict) -> str:
    """The prompt the session started the agent whose step fired a hook with, the brief file it names and the cci record its Record: line names read in; empty for the session's own steps."""
    if not payload.get("agent_id") or not (path := transcript(payload)).is_file():
        return ""
    with path.open() as handle:
        content = next(row for line in handle if (row := json.loads(line)).get("type") == "user")["message"]["content"]
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
                agent=payload["agent_id"], where="local", session=payload["session_id"], undelivered=[], state="running", updated=state.stamp()
            )
