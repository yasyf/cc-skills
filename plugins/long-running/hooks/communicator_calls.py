import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, limits, place, render, shell, state, superseded

OWN_TOOLS = {
    *(render.SLACK_TOOLS + tool for tool in render.COMMUNICATOR_SLACK),
    *(render.DRIVE_TOOLS + tool for tool in render.COMMUNICATOR_DRIVE),
    "SendMessage",
    "ToolSearch",
}
PICKUP = render.SLACK_TOOLS + "slack_pickup"
POSTS = {render.SLACK_TOOLS + tool for tool in ("slack_dm_status", "slack_reply", "slack_send", "slack_edit")}
OWN_SKILLS = {"long-running", "long-running:long-running"}
OWN_PROGRAMS = (["drive"], ["cc-slack"], ["cci", "drive", "use"])
RESUME = re.compile(r"\bdrive job resume\b.*?\b(j\d+)\b")
STATE_CLAIM = re.compile(
    r"(?<!\bif )(?<!\bwhen )(?<!\bonce )(?<!\bunless )\byou(?:'ve| have| are|'re| seem(?: to be)?| look| were| got)?(?: been| gone| went)?"
    r" (?:stopped|stalled|stuck|idle|silent|hung|quiet|dead|died|crashed|late|overdue)\b"
    r"|\bno (?:word|line|update|reply|output|report)s? (?:back )?from you\b|\b(?:have ?n[o']t|not) heard (?:back )?from you\b"
    r"|\byour (?:lane|worker|session) (?:has |is |was |seems )?(?:died|dead|stopped|stalled|stuck|idle|hung|quiet|silent|ended)\b"
    r"|\bare you (?:still )?(?:working|there|alive|stuck|running|on it)\b"
    r"|(?:\byour |(?<!\w\s)(?<!\w))(?:status|any (?:update|progress|news)\s*|still (?:working|there|alive|running))\?",
    re.IGNORECASE,
)
READ_WINDOW = timedelta(minutes=10)


def program(command: str) -> list[str]:
    """The words of the first simple command a Bash call runs, its program named without the path or quotes that call it, as `"/x/bin/cc-slack" thread` runs `cc-slack thread`; none when the shell cannot read the call."""
    try:
        commands = shell.simple_commands(shell.runnable(command))
    except ValueError:
        return []
    return [Path(commands[0][0]).name, *commands[0][1:]] if commands else []


def own_call(payload: dict) -> bool:
    """Whether a call is one the communicator makes itself: the ack through say, a +1, its DM's watch, SendMessage, ToolSearch, this skill, the drive's heard, handler, record, order and job tools, a `drive` or `cc-slack` command in Bash by whatever path calls it, which starts it and stands in for a drive tool its session does not list, and `cci drive use`, which its start block orders."""
    name = payload["tool_name"]
    call = payload["tool_input"]
    if name == "Skill":
        return call.get("skill") in OWN_SKILLS
    if name == "Bash":
        words = program(call.get("command") or "")
        return any(words[: len(own)] == own for own in OWN_PROGRAMS)
    return name in OWN_TOOLS


def communicator_call(payload: dict) -> agents.Unjudged:
    """Why the communicator's turn does not run a call that is not its own, decided by the tool's name alone, so Jev never weighs it and the same call meets the same refusal every time."""
    name = payload["tool_name"]
    if name.startswith(render.CCI_TOOLS) or name == "Bash" and program(payload["tool_input"].get("command") or "")[:1] == ["cci"]:
        return agents.Unjudged(f"Refused on purpose: {render.RECORDING}")
    if name == PICKUP:
        return agents.Unjudged(f"Refused on purpose: {render.EYES} Go on with the ack.")
    if name in POSTS:
        return agents.Unjudged(
            f"Refused on purpose: {name} holds the post until cc-slack's Slack permission check has read it,"
            " seconds past the 5 to 10 his ack has. Post the ack with say(thread_ts=<his thread>, text=<the ack>),"
            " which Jev reads in about 0.2 s before it posts, and reword a post of yours with say(edit=<its permalink>, text=…)."
        )
    if name == "Agent":
        return agents.Unjudged(
            "Refused on purpose: the communicator starts no agent with the Agent tool. His order for a subagent of the communicator,"
            ' such as "do it in a subagent you start right now that is opus and is a personal subagent of the communicator" or'
            ' "the communicator should spawn a read only subagent", is a handler: start it with the drive\'s handler tool, its brief'
            f" his words verbatim in its lines, {', '.join(f'{label}:' for label in render.HANDLER_LABELS)}, and no owner record, since"
            " the handler does that work in its own claude --bg session and its line comes back as one cci record. That is his"
            ' Nov 11 pick, "drive handler starts each as a claude --bg session that ends by posting one cci line: compacting every'
            ' 6.5 hours, 1.6 GB while one runs", and the Agent tool\'s launch result and finish notice added 2,232 characters to'
            " the communicator's context per handler, 76,000 of the 147,000 tokens it grew an hour."
        )
    return agents.Unjudged(
        f"Refused on purpose: the communicator does not run {name} itself, since work in its turn holds his"
        f" next message behind it: a read, draft or post of any kind is a handler's. Its own calls are {render.OWN_CALLS}."
        f" Start a handler with the drive's handler tool, briefed with this step in its lines, {', '.join(render.HANDLER_LABELS)};"
        " it does the read, draft or post and its line comes back as one cci record."
    )


def aimed(payload: dict) -> bool:
    """Whether the call may message a job, which messages names from the drive: a SendMessage or a Bash `drive job resume`."""
    return payload["tool_name"] == "SendMessage" or (payload["tool_name"] == "Bash" and bool(RESUME.search(payload["tool_input"].get("command") or "")))


def messages(payload: dict, data: dict) -> list[tuple[str, str, str]]:
    """Each job a coordinator's call messages, with the words it says to that job and the command before them: a local agent's by its SendMessage `to`, with the whole message, and each remote lane a `drive job resume` names, with that resume's own words up to the next one as the job reads them."""
    call = payload["tool_input"]
    if payload["tool_name"] == "SendMessage":
        job_id = state.agent_job(data, call.get("to"))
        return [(job_id, str(call.get("message") or ""), "")] if job_id else []
    command = call.get("command") or ""
    resumes = list(RESUME.finditer(command))
    return [
        (resumed[1], spoken(command[resumed.end() : following.start() if following else None]), command[: resumed.start()])
        for resumed, following in zip(resumes, [*resumes[1:], None], strict=True)
        if resumed[1] in data["jobs"]
    ]


def spoken(words: str) -> str:
    """Shell words as the program they run receives them: the lines unquoted, so `\\"` inside double quotes is a plain quote, and each heredoc body they open as written, its quotes and apostrophes kept."""
    lines, bodies = shell.scanned(words)
    return "\n".join([shell.expanded(shell.template(lines), {}), *bodies])


def resumed_builder(payload: dict, data: dict, job_id: str, said: str) -> str | None:
    """What a SendMessage to a builder carries back while it goes: the step superseded.delivery names for its text, the one every watch line names, is a fresh builder, as for a PR event to a builder that returned BUILT or DONE outside an incident, which the session holding its order adds; the SendMessage a resume prints pointing the agent at the record it posted carries nothing."""
    job = data["jobs"][job_id]
    if payload["tool_name"] != "SendMessage" or superseded.pointed(data, job_id, said) or not (fresh := superseded.fresh_builder(data, job_id, job, said, "this message")):
        return None
    lane, step = fresh
    return (
        f"{step}. This message still goes to its agent. {lane}@{data['name']}, the session holding {job['order']}, adds its jobs, and another session hands it"
        f" this step as a handoff record. Each text it holds, his answers included, reaches it by `drive job resume {job_id}` with no text, which the watch's"
        " ANSWER undelivered line names: it posts each whole to the agent's cci address and prints one SendMessage a record pointing the agent at it."
    )


def woken_builder(payload: dict, data: dict, job_id: str) -> str | None:
    """Why a SendMessage does not wake a builder on this Mac whose turn ended: the wake takes one of this Mac's builder slots, which place.woken gives by the rule every start here goes through."""
    if payload["tool_name"] != "SendMessage" or not (why := place.woken(data, job_id, state.now())):
        return None
    return agents.Unjudged(
        f"Refused on purpose: {job_id}'s agent on this Mac takes no message now: {why}, so this message would start it again in a slot it has no turn for."
        f' `drive job resume {job_id} "<the message>"` hands it to a lane on a Sprite with the message in its handoff, or, when its'
        f" brief declares a step only this Mac runs on {place.MAC_LINES}, holds the message while the job waits its turn for a builder slot here; when its work holds no checkout or"
        f" compiler, such as a dry run or a read, `drive job set {job_id} --builds-nothing` marks it to take the message here at once"
    )


def transcript_read(job_id: str, job: dict) -> re.Pattern[str]:
    """A command that reads the job's transcript: `drive job replies` on it, `cc-transcript` on its agent, or `orca orchestration worker-read` on its dispatch."""
    reads = [rf"\bdrive job replies\b.*\b{job_id}\b"]
    if job.get("agent"):
        reads.append(rf"\bcc-transcript\b.*{re.escape(job['agent'])}")
    if job.get("dispatch"):
        reads.append(rf"\bworker-read\b.*{re.escape(job['dispatch'])}")
    return re.compile("|".join(reads), re.DOTALL)


def read_job(path: Path, job_id: str, job: dict) -> bool:
    """Whether the session's own transcript shows, in the last 10 minutes, a Bash call that read the job's transcript and did not fail."""
    pattern = transcript_read(job_id, job)
    calls: set[str] = set()
    for row in agents.recent_rows(path, datetime.now(UTC) - READ_WINDOW):
        content = (row.get("message") or {}).get("content")
        for part in content if isinstance(content, list) else []:
            if part.get("type") == "tool_use" and part.get("name") == "Bash" and pattern.search(part["input"].get("command") or ""):
                calls.add(part["id"])
            elif part.get("type") == "tool_result" and part.get("tool_use_id") in calls and not part.get("is_error"):
                return True
    return False


def unread_job(payload: dict, data: dict, job_id: str, said: str, before: str) -> str | None:
    """What a message to a running job carries back when it tells the job what state it is in or asks it with no read of its transcript, earlier in the same command or in the last 10 minutes: the message goes, and the note says the state may be a guess. An order, a ruling, an answer or a refusal relayed states no state, even one that says stop, land or stuck, and neither does a question whose subject is another job or CI, words the message quotes, such as his about another session stopping, or another resume's words in the same command, since none is about this job."""
    job = data["jobs"][job_id]
    claim = STATE_CLAIM.search(limits.SAID.sub("", said))
    if job["state"] != "running" or not claim or transcript_read(job_id, job).search(before):
        return None
    if read_job(Path(payload["transcript_path"]), job_id, job):
        return None
    how = (
        f"`drive job replies {job_id}` or `orca orchestration worker-read --dispatch {job['dispatch']} --limit 50 --full --json`"
        if job.get("where") == "remote"
        else f"`cc-transcript` on agent {job['agent']}'s transcript"
    )
    return (
        f"This message tells {job_id} it stopped, stalled, went quiet or is stuck, or asks it whether it is (`{claim[0]}`), and your"
        f" transcript shows no read of its transcript in the last 10 minutes, so that state may be a guess the job takes as fact; {how} shows it."
    )


def deny_reason(payload: dict) -> str | None:
    """Why a drive session's own call does not run: the communicator's turn holds only the ack and what starts a handler, and the coordinator and the meta session wake no builder on this Mac whose turn ended while its builder slots are full."""
    if payload.get("agent_id"):
        return None
    session = payload.get("session_id") or ""
    names = state.session_drives(session)
    if not names:
        return None
    if state.session_role(state.saved(names[0]), session) == "communicator":
        return None if own_call(payload) else communicator_call(payload)
    if not aimed(payload):
        return None
    data = state.load(names[0])
    return next(filter(None, (woken_builder(payload, data, job_id) for job_id, _, _ in messages(payload, data))), None)


def note(payload: dict) -> str | None:
    """What the coordinator's or the meta session's message to a job carries back as context while it goes: a PR event sent to a builder that returned, which a fresh builder takes, and a state told or asked of a running job before a read of its transcript."""
    session = payload.get("session_id") or ""
    if payload.get("agent_id") or not aimed(payload) or not (names := state.session_drives(session)) or state.session_role(state.saved(names[0]), session) == "communicator":
        return None
    data = state.load(names[0])
    return "\n".join(filter(None, (resumed_builder(payload, data, job_id, said) or unread_job(payload, data, job_id, said, before) for job_id, said, before in messages(payload, data)))) or None


def main() -> None:
    payload = agents.seen(json.load(sys.stdin))
    if reason := deny_reason(payload):
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
                | agents.ruled(reason)
            )
        )
    elif told := note(payload):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": told}}))


if __name__ == "__main__":
    main()
