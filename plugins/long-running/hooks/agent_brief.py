import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, brief, place, render, state
from drive.render import cut, when

DESIGNER = "long-running:designer"
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
LABELS = render.HANDLER_LABELS
LABEL = re.compile(rf"^({'|'.join(re.escape(label) for label in LABELS)}):")


def beyond_brief(prompt: str) -> str | None:
    """The first line of a handler's prompt that is no line of its brief: each line opens with a brief label, his words may run on below their label, and Context runs to at most three lines."""
    label, context = None, 0
    for line in prompt.strip().splitlines():
        if found := LABEL.match(line):
            label, context = found[1], 1
        elif line.strip() and not (label == "Message (his words)" or (label == "Context" and (context := context + 1) <= 3)):
            return line.strip()
    return None


def job_refusal(name: str, data: dict, kind: str, text: str) -> str | None:
    """Why the job a brief's Drive line names may not start this agent: a design job runs only as the designer, and a codex lane on a build order waits on its reader's research like the builder."""
    match = brief.BRIEF_JOB.search(text)
    job = data["jobs"].get(match[1]) if match else None
    if job is None:
        return None
    order_id = job["order"]
    order = data["orders"][order_id]
    if job["kind"] == "design" and kind != DESIGNER:
        return (
            f"refused: {match[1]} is a design job, which runs only as {DESIGNER}, since only that definition keeps it read"
            f" only and its forks in his cards' shape; start it again with subagent_type {DESIGNER}"
        )
    if job["kind"] != "codex" or order["kind"] != "build" or order["incident"] or brief.HANDOFF.search(text) or brief.research(name, data, order_id):
        return None
    return (
        f"refused: {match[1]} is a codex lane on {order_id}, a build order, which starts only once an Opus reader has kept"
        f" {brief.BLOCK} in its report: drive job add reader --order {order_id} --goal \"research: <what>\", then this"
        " lane on that research"
    )


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
    """Why a session other than the one holding drive tooling starts no builder serving it: that session alone starts it, the meta session once one is bound, else the communicator, on the job its brief names once that job's order is its own, and on its own order for a brief that names none."""
    holder = state.tooling_holder(data)
    held_by, starter = state.title(holder), state.title(role)
    match = brief.BRIEF_JOB.search(text)
    job = data["jobs"].get(match[1]) if match else None
    if job is None:
        return (
            f"refused: a builder serving drive tooling is the {held_by}'s, never the {starter}'s: send a defect record to"
            f" {holder}@{name} naming the tool and its refusal verbatim, and the {held_by}'s builder takes it over"
        )
    order_id = job["order"]
    session = (data["roles"].get(holder) or {}).get("session") or "not started"
    given = data["orders"][order_id]["owner"] == holder
    held = f", and {order_id} is the {held_by}'s order, session {session}" if given else ""
    step = "" if given else f"{brief.gives_tooling(match[1], order_id, holder)}, then "
    return (
        f"refused: a builder serving drive tooling is the {held_by}'s, never the {starter}'s{held}: {step}send"
        f" {holder}@{name} a defect record naming {match[1]} and its `Read your brief first:` line, and the {held_by} starts it"
    )


def crowded(data: dict) -> str | None:
    """Why no builder starts on this Mac now: its cap of builders already runs here, naming the builder jobs that wait for one, oldest due first."""
    running = place.local_builders(data, state.now())
    if running < place.LOCAL_BUILDERS:
        return None
    waiting = "; ".join(in_line(data, job_id) for job_id in place.waiting_builders(data))
    return (
        f"refused: {running} builders already run on this Mac, its cap of {place.LOCAL_BUILDERS}, so this one waits: start no"
        f" builder here until one finishes, then start the first job waiting, oldest due first: {waiting or 'none is added yet'}."
        " A Forge-AI/monorepo builder need not wait: start it on a Sprite with drive job launch <j> <brief>"
    )


def in_line(data: dict, job_id: str) -> str:
    job = data["jobs"][job_id]
    due = place.promised(data, job)
    return f'{job_id} ({f"due {when(due)}" if due else "no time promised"}) "{cut(job["goal"], 60)}"'


def deny_reason(payload: dict) -> str | None:
    """Why a drive session may not start this agent: a named agent joins the agent team and never wakes on events, Fable runs only the coordinator and the design lane, a handler's prompt is its brief, a job's agent fits its kind, a builder's brief says its scope, drive tooling is the meta session's alone, or the communicator's while none is bound, and a builder starts here only under this Mac's cap of builders."""
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
    if call.get("name"):
        return (
            f"refused: the {role} starts no named agent, since agent teams are on and a named agent becomes a teammate that"
            " never wakes on events; start it again without `name`"
        )
    if "fable" in (call.get("model") or "").lower() and kind != DESIGNER:
        return (
            f"refused: Fable runs only the coordinator and {DESIGNER}; start {kind or 'this agent'} on its own default"
            " model, or `sonnet` for a one-fact lookup"
        )
    if kind == agents.HANDLER and (extra := beyond_brief(prompt)):
        return (
            f"refused: a handler's prompt is its brief alone, each line opening with one of {', '.join(LABELS)}, his words"
            f" running on below theirs and Context at most three lines; `{extra}` is more. Its rules load with its agent"
            " definition, and a rule typed into a prompt overrides it: drop the line"
        )
    text = brief.carried(prompt)
    if kind == BUILDER and role not in (None, state.tooling_holder(data)) and brief.SERVES_TOOLING.search(text):
        return tooling_refusal(names[0], data, text, role)
    if refused := job_refusal(names[0], data, kind, text):
        return refused
    return builder_refusal(text) or crowded(data) if kind == BUILDER else None


def heavier(payload: dict) -> dict | None:
    """The Agent call a drive session meant: a handler passed Sonnet or Haiku runs on its own Opus, the model dropped, since even a ready record draws his PR card."""
    call = payload["tool_input"]
    if payload.get("agent_id") or call.get("subagent_type") != agents.HANDLER or not state.session_drives(payload.get("session_id") or ""):
        return None
    if not any(word in (call.get("model") or "") for word in brief.LIGHT_MODELS):
        return None
    return {key: value for key, value in call.items() if key != "model"}


def main() -> None:
    payload = json.load(sys.stdin)
    if reason := deny_reason(payload):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}} | agents.ruled(reason)))
    elif made := heavier(payload):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": made}}))


if __name__ == "__main__":
    main()
