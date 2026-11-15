import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, brief, place, state, view

BUILDER = "long-running:builder"
BRIEFED = ("long-running:reader", BUILDER, agents.DESIGNER)
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
DATA_PASS_RULE = "A builder whose Goal itself runs a pass, migration, backfill, bulk or hand repair or any other write over a team's data, rather than building a PR or check named for one."


def unbriefed(name: str, data: dict, prompt: str, kind: str) -> str | None:
    """Why a drive agent may not start on this prompt: it reads in no brief file of the drive's jobs folder, where drive job brief, a handoff and a launch write every brief the drive writes, so an agent briefed by a prompt typed whole, or told to read a path nothing wrote, never starts without the lines its brief carries."""
    jobs = (state.drive_dir(name) / "jobs").resolve()
    read = brief.brief_file(prompt)
    if read and read[0].resolve().is_relative_to(jobs):
        return None
    missing = [named for named in dict.fromkeys(brief.BRIEF_FILE.findall(prompt)) if not Path(named).expanduser().is_file()]
    found = brief.BRIEF_JOB.search(f"{prompt}\n{read[1]}" if read else prompt)
    job_id = found[1] if found else next((Path(named).name.removesuffix(".brief.md") for named in missing), None)
    why = f"; {', '.join(missing)} {'does' if len(missing) == 1 else 'do'} not exist" if missing else ""
    if read:
        why += f"; {read[0]} is not in the drive's jobs folder"
    refused = f"refused: a {kind} starts only on a brief the drive wrote{why}"
    if job_id not in data["jobs"]:
        return agents.Unjudged(f"{refused}: `drive job add` it, then `drive job brief <j>`, and start it on the line that prints")
    if (held := data["jobs"][job_id].get("brief")) and Path(held).is_file():
        return agents.Unjudged(f"{refused}: start it on `Read your brief first: {held}`")
    return agents.Unjudged(f"{refused}: `drive job brief {job_id}`, then start it on the line that prints")


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
        f"refused: the Goal reads as a pass over a team's data (`{found[0].strip()}`) with no `Runs as:` line; it runs as a"
        " service account in the team's cluster, never from this Mac:"
        f" `drive job brief {job[1] if job else '<j>'} --runs-as \"<service account, how it starts>\"`, or"
        " `--runs-as \"none: <why>\"` when it writes no team's data",
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


def waiting(data: dict, text: str) -> str | None:
    """Why a builder starts no agent while a wait of its holds, by the one view.start_refusal drive job launch reads, so it starts on the watch's FREE line here as on a Sprite."""
    match = brief.BRIEF_JOB.search(text)
    refused = view.start_refusal(data, match[1]) if match and match[1] in data["jobs"] else None
    return refused and agents.Unjudged(f"refused: {refused}")


def overloaded(data: dict, text: str) -> str | None:
    """Why a Forge-AI/monorepo builder starts on a Sprite, not here: this Mac is at or over its load gate's limit and its brief declares no step only this Mac runs, by place.mac_step, which starts here at any load."""
    match = brief.BRIEF_JOB.search(text)
    job = data["jobs"].get(match[1]) if match else None
    if job is None or (job.get("repo") or "").casefold() != place.MONOREPO.casefold() or place.mac_step(text):
        return None
    where, why = place.verdict(place.busy(), place.limit(), {})
    if where == "local":
        return None
    return agents.Unjudged(
        f"refused: {why}, so {match[1]} starts on a Sprite: drive job launch {match[1]} <brief>. A brief declaring a step only this Mac runs,"
        f" on {place.MAC_LINES}, starts here at any load"
    )


def crowded(name: str, data: dict, text: str) -> str | None:
    """Why no builder starts on this Mac now: its cap's builders hold every other slot, each named, unless its brief declares a step only this Mac runs, by place.mac_step, or it builds for an open incident or an immediate fix, any of which starts at once; a refused one starts on a Sprite at once. A job let start is stamped launching, so place.local_builders counts its slot before its agent's first step records the agent, and a burst of starts fills the cap rather than passing it."""
    match = brief.BRIEF_JOB.search(text)
    job_id = match[1] if match and match[1] in data["jobs"] else None
    moment = state.now()
    if why := place.crowded(data, job_id, moment, place.mac_step(text)):
        return agents.Unjudged(f"refused: {why}, so {job_id or 'it'} starts on a Sprite at once: write this brief to a file and run drive job launch {job_id or '<j>'} <file>")
    if job_id:
        with state.edit(name) as data:
            data["jobs"][job_id]["launching"] = state.stamp(moment)
    return None


def deny_reason(payload: dict) -> str | None:
    """Why a drive session may not start this agent, in the shape `agents.shaped` gives its call: a handler starts with the drive's handler tool, a reader, builder or designer starts only on a brief the drive wrote, a build order's codex lane waits on its reader's research, a builder's brief says its scope, drive tooling is the meta session's alone, a builder starts on the watch's FREE line once its last wait ends, a monorepo builder starts here over this Mac's load limit only when its brief declares a step only this Mac runs, and a builder starts here only while one of this Mac's builder slots is open, unless its brief declares a step only this Mac runs."""
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
    if kind in BRIEFED and (refused := unbriefed(names[0], data, prompt, kind)):
        return refused
    text = brief.carried(prompt)
    if kind == BUILDER and role not in (None, "meta") and brief.SERVES_TOOLING.search(text):
        return tooling_refusal(names[0], data, text, role)
    return builder_refusal(text) or waiting(data, text) or overloaded(data, text) or crowded(names[0], data, text) if kind == BUILDER else None


def main() -> None:
    payload = agents.seen(json.load(sys.stdin))
    made = agents.shaped(payload)
    if reason := deny_reason(payload | {"tool_input": made}):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}} | agents.ruled(reason)))
    elif made != payload["tool_input"]:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": made}}))


if __name__ == "__main__":
    main()
