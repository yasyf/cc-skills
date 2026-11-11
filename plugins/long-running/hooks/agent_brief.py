import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, brief, state

DESIGNER = "long-running:designer"
BUILDER = "long-running:builder"
HANDLER = "long-running:handler"
SCOPED = re.compile(r"^(?:Serves|Handoff):", re.MULTILINE)
SERVES = re.compile(r"^Serves:(.*)$", re.MULTILINE)
SERVES_TOOLING = re.compile(r"^Serves:[ \t]*drive tooling\b", re.MULTILINE | re.IGNORECASE)
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
LABELS = ("Record", "Job", "Message (his words)", "Link", "Thread", "Drive", "Paths", "Scratch", "Deliverable", "Context", "Waits")
LABEL = re.compile(rf"^({'|'.join(re.escape(label) for label in LABELS)}):")
LIGHT_RECORD = re.compile(r"^\d+ (?:ready|report \S+(?: \[[^\]\n]*\])? \+ POST\b)", re.MULTILINE)


def beyond_brief(prompt: str) -> str | None:
    """The first line of a handler's prompt that is no line of its brief: each line opens with a brief label, his words may run on below their label, and Context runs to at most three lines."""
    label, context = None, 0
    for line in prompt.strip().splitlines():
        if found := LABEL.match(line):
            label, context = found[1], 1
        elif line.strip() and not (label == "Message (his words)" or (label == "Context" and (context := context + 1) <= 3)):
            return line.strip()
    return None


def light_record(prompt: str) -> bool:
    """Whether a handler's prompt is the one Record: line of a `ready` record or a `POST` report, the two items Sonnet handles."""
    lines = prompt.strip().splitlines()
    return len(lines) == 1 and bool(found := agents.RECORD.match(lines[0])) and bool(LIGHT_RECORD.search(agents.record(found[1])))


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
    return (
        f"refused: this builder's Goal reads as a pass over a team's data (`{found[0].strip()}`) and its brief has no `Runs as:`"
        " line; a pass, migration, backfill or bulk repair over a team's data runs as a service account in that team's"
        " cluster, started from a pipeline or an in-cluster schedule, never under a person's sign-in: write the brief again"
        f" with drive job brief {job[1] if job else '<j>'} --runs-as \"<service account, how it starts>\", or --runs-as"
        " \"none: <why>\" when the job runs no pass"
    )


def deny_reason(payload: dict) -> str | None:
    """Why a drive's coordinator or communicator may not start this agent: a named agent joins the agent team and never wakes on events, Fable runs only the coordinator and the design lane, a handler's prompt is its brief and runs on Opus but for the two light records, a job's agent fits its kind, a builder's brief says its scope, and drive tooling is the communicator's alone."""
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
    if kind == HANDLER and (extra := beyond_brief(prompt)):
        return (
            f"refused: a handler's prompt is its brief alone, each line opening with one of {', '.join(LABELS)}, his words"
            f" running on below theirs and Context at most three lines; `{extra}` is more. Its rules load with its agent"
            " definition, and a rule typed into a prompt overrides it: drop the line"
        )
    if kind == HANDLER and any(word in (call.get("model") or "") for word in brief.LIGHT_MODELS) and not light_record(prompt):
        return (
            "refused: a handler runs on Opus for his words, an ask, a card or any judgment, and Sonnet or Haiku only for a `ready`"
            " record or a `POST` report briefed as its one `Record:` line; pass no model"
        )
    text = brief.carried(prompt)
    if kind == BUILDER and role == "coordinator" and SERVES_TOOLING.search(text):
        return (
            "refused: a builder serving drive tooling is the communicator's, never the coordinator's: send a defect record to"
            f" communicator@{names[0]} naming the tool and its refusal verbatim, and the communicator's builder takes it over"
        )
    if refused := job_refusal(names[0], data, kind, text):
        return refused
    return builder_refusal(text) if kind == BUILDER else None


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
