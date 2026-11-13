import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, brief, place, state
from drive.render import cut, when

BUILDER = "long-running:builder"
SCOPED = re.compile(r"^(?:Serves|Handoff):", re.MULTILINE)
SERVES = re.compile(r"^Serves:(.*)$", re.MULTILINE)
SERVED = re.compile(r"port of #\d+|drive tooling|his order", re.IGNORECASE)
ORDER_WORDS = re.compile(r"^Order(?: \(his words\))?:(.*)$", re.MULTILINE)
GOAL = re.compile(r"^Goal:(.*)$", re.MULTILINE)
NOT_A_PR_NAME = r"(?!['’]s\b|\s+(?:stack|PRs?|checks?|tests?|cards?)\b)"
DATA_PASS = re.compile(
    rf"^\s*(?:[\w-]+:\s+)?(?:back-?fill|bulk[- ]repair|repair|migrate|convert)\b{NOT_A_PR_NAME}"
    rf"|^\s*(?:[\w-]+:\s+)?(?:re-?)?run\b(?:\W+\w+){{0,4}}?\W+(?:back-?fill|bulk[- ]repair|cleanup|migration|conversion|repair)s?\b{NOT_A_PR_NAME}"
    r"|\b(?:is|are|gets?)\s+(?:back-?filled|bulk[- ]repaired|migrated|converted|repaired)\b",
    re.IGNORECASE,
)
RUNS_AS = re.compile(r"^Runs as:[ \t]*\S", re.MULTILINE)
DATA_PASS_RULE = "A builder whose Goal itself runs a pass, migration, backfill or bulk repair over a team's data, rather than building a PR or check named for one."


def builder_refusal(text: str) -> str | None:
    """Why a builder's brief may not start it: it names no scope, its Serves line names none of the three, or its Goal's own action is a pass over a team's data, not a PR or check named for one, with no Runs as line."""
    if not SCOPED.search(text):
        return (
            "refused: a builder's brief, in its prompt or the brief file the prompt names, carries a `Serves:` line, `port of"
            " #N`, `drive tooling` or `his order`, or a `Handoff:` line for a PR event, so work no order asked for is caught"
            " before it starts; add the line, or start no builder when none of the three fits"
        )
    served = SERVES.search(text)
    value = served[1].strip().strip("\"“”").casefold() if served else ""
    if served and not (SERVED.search(value) or (value and any(value in words.casefold() for words in ORDER_WORDS.findall(text)))):
        return (
            f"refused: `Serves:{served[1]}` names none of `port of #N`, `drive tooling` or `his order`, nor words of the"
            " brief's Order line; a job for anything else is never started: write the brief again with the one that fits,"
            " or start no builder"
        )
    task = GOAL.search(text)
    found = DATA_PASS.search(task[1]) if task and not RUNS_AS.search(text) and "drive tooling" not in value else None
    if found is None:
        return None
    job = brief.BRIEF_JOB.search(text)
    return agents.Ruled(
        f"refused: this builder's Goal reads as a pass over a team's data (`{found[0].strip()}`) and its brief has no `Runs as:`"
        " line; a pass runs in the team's cluster, never as a person:"
        f" `drive job brief {job[1] if job else '<j>'} --runs-as \"<service account, how it starts>\"`, or"
        " `--runs-as \"none: <why>\"` when the job runs no pass",
        DATA_PASS_RULE,
    )


def tooling_refusal(name: str, data: dict, text: str, role: str) -> str:
    """Why a session other than the meta session starts no builder serving drive tooling: the meta session alone starts it, on the job its brief names once that job's order is its own, and on its own order for a brief that names none."""
    starter = state.title(role)
    match = brief.BRIEF_JOB.search(text)
    job = data["jobs"].get(match[1]) if match else None
    if job is None:
        return (
            f"refused: a builder serving drive tooling is the meta session's, never the {starter}'s: send a defect record to"
            f" meta@{name} naming the tool and its refusal verbatim, and the meta session's builder takes it over"
        )
    order_id = job["order"]
    session = (data["roles"].get("meta") or {}).get("session") or "not started"
    given = data["orders"][order_id]["owner"] == "meta"
    held = f", and {order_id} is the meta session's order, session {session}" if given else ""
    step = "" if given else f"{brief.gives_tooling(match[1], order_id)}, then "
    return (
        f"refused: a builder serving drive tooling is the meta session's, never the {starter}'s{held}: {step}send"
        f" meta@{name} a defect record naming {match[1]} and its `Read your brief first:` line, and the meta session starts it"
    )


def crowded(name: str, data: dict, text: str) -> str | None:
    """Why no builder starts on this Mac now: a job waiting its turn for a builder slot starts only on the watch's FREE line naming it, and any other only while a slot is open, its cap's builders holding every other one, each named, and no builder waits its turn ahead of it, unless it builds for an open incident or an immediate fix, which starts at once; a refused job no agent runs is held waiting its turn, so the watch reads it waiting, never abandoned, and frees the earliest due as each slot opens."""
    match = brief.BRIEF_JOB.search(text)
    job_id = match[1] if match and match[1] in data["jobs"] else None
    moment = state.now()
    if job_id in (places := place.positions(data)):
        return f"refused: {job_id} waits its turn for a builder slot on this Mac, {places[job_id]}{line(data)}"
    if not (why := place.crowded(data, job_id, moment)):
        return None
    job = data["jobs"].get(job_id) or {}
    if job.get("kind") != "builder" or job["state"] in state.FINISHED_JOB_STATES or job.get("where") == "remote" or job.get("agent") or job.get("dispatch"):
        return f"refused: {why}, so this one waits: start it once a slot opens here with no builder waiting its turn{line(data)}"
    with state.edit(name) as data:
        place.queue(data["jobs"][job_id], moment)
    return f"refused: {why}, so {job_id} waits its turn for a builder slot on this Mac, {place.positions(data)[job_id]}{line(data)}"


def line(data: dict) -> str:
    waiting = "; ".join(in_line(data, job_id) for job_id in place.waiting_builders(data))
    return (
        f". The watch's FREE line names each job waiting its turn as a slot opens, earliest due first: {waiting or 'none'}."
        " A Forge-AI/monorepo builder need not wait: start it on a Sprite with drive job launch <j> <brief>"
    )


def in_line(data: dict, job_id: str) -> str:
    job = data["jobs"][job_id]
    due = place.promised(data, job)
    return f'{job_id} ({f"due {when(due)}" if due else "no time promised"}) "{cut(job["goal"], 60)}"'


def deny_reason(payload: dict) -> str | None:
    """Why a drive session may not start this agent, in the shape `agents.shaped` gives its call: a handler starts with the drive's handler tool, a build order's codex lane waits on its reader's research, a builder's brief says its scope, drive tooling is the meta session's alone, and a builder starts here only in its turn for one of this Mac's builder slots."""
    if payload.get("agent_id"):
        return None
    session = payload.get("session_id") or ""
    names = state.session_drives(session)
    if not names:
        return None
    data = state.load(names[0])
    role = state.session_role(data, session)
    call = payload["tool_input"]
    kind = call.get("subagent_type") or ""
    prompt = call.get("prompt") or ""
    if kind == agents.HANDLER:
        return agents.Unjudged(
            "refused: a handler starts with the drive's handler tool, this prompt its brief, as its own claude --bg session"
            " whose line comes back as one cci record, never through the Agent tool, whose launch and finish notices add"
            " 2,232 characters to your context"
        )
    text = brief.carried(prompt)
    if kind == BUILDER and role not in (None, "meta") and brief.SERVES_TOOLING.search(text):
        return tooling_refusal(names[0], data, text, role)
    return builder_refusal(text) or crowded(names[0], data, text) if kind == BUILDER else None


def main() -> None:
    payload = agents.seen(json.load(sys.stdin))
    made = agents.shaped(payload)
    if reason := deny_reason(payload | {"tool_input": made}):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}} | agents.ruled(reason)))
    elif made != payload["tool_input"]:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": made}}))


if __name__ == "__main__":
    main()
