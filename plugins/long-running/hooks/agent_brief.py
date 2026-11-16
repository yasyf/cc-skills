import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, brief, place, state, view
from drive.render import cut

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
    """Why a builder's brief may not start it: its Goal's own action is a pass over a team's data, not a PR or check named for one, with no Runs as line."""
    served = SERVES.search(text)
    task = GOAL.search(text)
    found = DATA_PASS.search(task[1]) if task and not RUNS_AS.search(text) and not (served and "drive tooling" in served[1].casefold()) else None
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


def unscoped(text: str) -> str | None:
    """A note on a builder whose brief names no scope, or whose Serves line names none of the three nor words of its Order line: it starts on its starter's reading of what it serves."""
    if not SCOPED.search(text):
        return "This builder's brief carries no `Serves:` line (`port of #N`, `drive tooling` or `his order`) and no `Handoff:` line, so the drive records no order it serves; it starts on your reading of its scope."
    served = SERVES.search(text)
    value = served[1].strip().strip("\"“”").casefold() if served else ""
    if served and not (SERVED.search(value) or (value and any(value in words.casefold() for words in ORDER_WORDS.findall(text)))):
        return f"`Serves:{served[1]}` names none of `port of #N`, `drive tooling` or `his order`, nor words of the brief's Order line; it starts on your reading of its scope."
    return None


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
    """A note on a builder started while a wait of its job still holds, by the one view.start_refusal drive job launch reads: the start goes ahead, ahead of the watch, which starts it once the last wait ends; its turn for a builder slot on this Mac is crowded's to name."""
    match = brief.BRIEF_JOB.search(text)
    if not (match and match[1] in data["jobs"] and view.start_refusal(data, match[1], False)):
        return None
    waits = [wait for wait in view.start_waits(data, data["jobs"][match[1]], None) if wait != state.SLOT_WAIT]
    return f"{match[1]} still waits on {', '.join(waits)}; it starts now on your call, ahead of the watch, which starts it once the last wait ends."


def overloaded(data: dict, text: str) -> str | None:
    """Why a builder of the drive's home repository, by state.home_repo, starts on a Sprite, not here: this Mac is at or over its load gate's limit and its brief declares no step only this Mac runs, by place.mac_step, which starts here at any load, as a priority order's builder does, by state.first_in_line, and one that builds nothing, by state.builds_nothing, so no launch delays it."""
    match = brief.BRIEF_JOB.search(text)
    job = data["jobs"].get(match[1]) if match else None
    if job is None or not state.home_repo(data, job.get("repo") or "") or place.mac_step(text) or state.first_in_line(data, job) or state.builds_nothing(job):
        return None
    where, why = place.verdict(place.busy(), place.limit(), {})
    if where == "local":
        return None
    return agents.Unjudged(
        f"refused: {why}, so {match[1]} starts on a Sprite: drive job launch {match[1]} <brief>. A brief declaring a step only this Mac runs,"
        f" on {place.MAC_LINES}, starts here at any load"
    )


def crowded(name: str, data: dict, text: str) -> str | None:
    """Why no builder starts on this Mac now: a job waiting its turn for a builder slot starts only on the watch's FREE line naming it, and any other only while a slot is open, its cap's builders holding every other one, each named, and no builder waits its turn ahead of it, unless it builds for an open incident, an open priority order or an immediate fix, which starts at once. A job let start is stamped launching, so place.local_builders counts its slot before its agent's first step records the agent, and a burst of starts fills the cap rather than passing it. A refused job whose brief this Mac alone runs, by place.mac_step, and that no agent runs and nothing else holds is held waiting its turn, so the watch reads it waiting, never abandoned, and frees the earliest due as each slot opens; any other starts on a Sprite at once."""
    match = brief.BRIEF_JOB.search(text)
    job_id = match[1] if match and match[1] in data["jobs"] else None
    moment = state.now()
    if job_id in (places := place.positions(data)):
        why = f"{job_id} waits its turn for a builder slot on this Mac, {places[job_id]}"
    elif not (why := place.crowded(data, job_id, moment)):
        if job_id:
            state.update(name, (("jobs", job_id, "launching"),), lambda data: data["jobs"][job_id].update(launching=state.stamp(moment)))
        place.counted_again()
        return None
    if job_id and not place.mac_step(text):
        return agents.Unjudged(f"refused: {why}, so {job_id} starts on a Sprite at once, since its brief declares no step only this Mac runs on {place.MAC_LINES}: write this brief to a file and run drive job launch {job_id} <file>")
    if job_id in places:
        return agents.Unjudged(f"refused: {why}{line(data)}")
    if job_id is None or not place.queueable(data, job_id):
        return agents.Unjudged(f"refused: {why}, so this one waits: start it once a slot opens here with no builder waiting its turn{line(data)}")

    def queued(data: dict) -> dict:
        place.queue(data["jobs"][job_id], moment)
        return data

    data = state.update(name, (("jobs", job_id),), queued)
    return agents.Unjudged(f"refused: {why}, so {job_id} waits its turn for a builder slot on this Mac, {place.positions(data)[job_id]}{line(data)}")


def unadmitted(name: str, data: dict, text: str) -> str | None:
    """Why a reader or designer starts no agent on this Mac now: it is loaded, short of memory or its local Claude lanes fill their machine-wide cap, by place.admission, so it starts on a Sprite instead; one it lets through is stamped launching, which counts it as a lane here at once."""
    match = brief.BRIEF_JOB.search(text)
    job_id = match[1] if match and match[1] in data["jobs"] else None
    moment = state.now()
    with place.admitting():
        if why := place.admission(data, job_id, "local", moment):
            return agents.Unjudged(f"refused: {why}, so {job_id or 'it'} starts on a Sprite: write this brief to a file and run drive job launch {job_id or '<j>'} <file>")
        if job_id:
            state.update(name, (("jobs", job_id, "launching"),), lambda data: data["jobs"][job_id].update(launching=state.stamp(moment)))
        place.counted_again()
    return None


def started_alone(data: dict, text: str) -> str | None:
    """Why no Agent starts on a job whose agent `drive job start` started as its own session: that start is in flight or at work, by agents.gone on its bg entry, so a second agent would run beside it."""
    match = brief.BRIEF_JOB.search(text)
    if not (match and (bg := (data["jobs"].get(match[1]) or {}).get("bg")) and not agents.gone(bg)):
        return None
    return agents.Unjudged(f"refused: the watch started {match[1]} as its own claude --bg session {bg['session'] or '(starting)'} at {bg['started']}, so start no agent for it: drive job resume {match[1]} \"<text>\" reaches it")


def line(data: dict) -> str:
    waiting = "; ".join(in_line(data, job_id) for job_id in place.waiting_builders(data))
    return (
        f". The watch's FREE line names each job waiting its turn as a slot opens, oldest first: {waiting or 'none'}."
        f" Only a builder whose brief declares a step only this Mac runs, on {place.MAC_LINES}, waits here; start any other on a Sprite at once with drive job launch <j> <brief>"
    )


def in_line(data: dict, job_id: str) -> str:
    job = data["jobs"][job_id]
    return f'{job_id} "{cut(job["goal"], 60)}"'


def deny_reason(payload: dict) -> str | None:
    """Why a drive session may not start this agent, in the shape `agents.shaped` gives its call: a reader, builder or designer starts only on a brief the drive wrote, a build order's codex lane waits on its reader's research, a reader or designer starts here only while this Mac admits a local Claude lane, by unadmitted, drive tooling is the meta session's alone, a builder's Goal that is a pass over a team's data names what it runs as, a monorepo builder starts here over this Mac's load limit only when its brief declares a step only this Mac runs, and a builder starts here only in its turn for one of this Mac's builder slots."""
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
    if kind in BRIEFED and (refused := unbriefed(names[0], data, prompt, kind)):
        return refused
    text = brief.carried(prompt)
    if kind in BRIEFED and (refused := started_alone(data, text)):
        return refused
    if kind == BUILDER and role not in (None, "meta") and brief.SERVES_TOOLING.search(text):
        return tooling_refusal(names[0], data, text, role)
    if kind == BUILDER:
        return builder_refusal(text) or overloaded(data, text) or crowded(names[0], data, text)
    return unadmitted(names[0], data, text) if kind in BRIEFED else None


def note(payload: dict) -> str | None:
    """What a drive session's builder start that goes ahead is told: its brief names no scope the drive knows, by unscoped, and a wait of its job still holds, by waiting."""
    call = payload["tool_input"]
    if payload.get("agent_id") or call.get("subagent_type") != BUILDER or not (names := state.session_drives(payload.get("session_id") or "")):
        return None
    text = brief.carried(call.get("prompt") or "")
    return "\n".join(filter(None, [unscoped(text), waiting(state.load(names[0]), text)])) or None


def main() -> None:
    payload = agents.seen(json.load(sys.stdin))
    made = agents.shaped(payload)
    if reason := deny_reason(payload | {"tool_input": made}):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}} | agents.ruled(reason)))
        return
    told = note(payload | {"tool_input": made})
    output = ({"updatedInput": made} if made != payload["tool_input"] else {}) | ({"additionalContext": told} if told else {})
    if output:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse"} | output}))


if __name__ == "__main__":
    main()
