import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, place, render, state

OWN_TOOLS = {
    *(render.SLACK_TOOLS + tool for tool in render.COMMUNICATOR_SLACK),
    *(render.DRIVE_TOOLS + tool for tool in render.COMMUNICATOR_DRIVE),
    "SendMessage",
    "ToolSearch",
}
PICKUP = render.SLACK_TOOLS + "slack_pickup"
POSTS = {render.SLACK_TOOLS + tool for tool in ("slack_dm_status", "slack_reply", "slack_send", "slack_edit")}
OWN_SKILLS = {"long-running", "long-running:long-running"}
OWN_COMMAND = re.compile(r"\s*(?:drive|cci|cc-slack)\s")
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
STATE_RULE = "A message that states or asks a running job's own state, such as that it stopped, stalled or went quiet, sent with no read of its transcript."
FRESH_STATES = ("built", "done")
READ_WINDOW = timedelta(minutes=10)


def own_call(payload: dict) -> bool:
    """Whether a call is one the communicator makes itself: the ack through say, a +1, its DM's watch, SendMessage, ToolSearch, this skill, the drive's heard, handler, record, order and job tools, and a `drive`, `cci` or `cc-slack` command in Bash, which starts it and stands in for a drive tool its session does not list."""
    name = payload["tool_name"]
    call = payload["tool_input"]
    if name == "Skill":
        return call.get("skill") in OWN_SKILLS
    if name == "Bash":
        return bool(OWN_COMMAND.match(call.get("command") or ""))
    return name in OWN_TOOLS


def communicator_call(payload: dict) -> str:
    if payload["tool_name"] == PICKUP:
        return f"Refused on purpose: {render.EYES} Go on with the ack."
    if payload["tool_name"] in POSTS:
        return agents.Unjudged(
            f"Refused on purpose: {payload['tool_name']} holds the post until cc-slack's Slack permission check and Jev"
            " have read it, seconds past the 5 to 10 his ack has. Post the ack with say(thread_ts=<his thread>, text=<the ack>),"
            " which posts at once and has Jev read it afterwards, and reword a post of yours with say(edit=<its permalink>, text=…)."
        )
    return (
        f"Refused on purpose: the communicator does not run {payload['tool_name']} itself, since work in its turn holds his"
        f" next message behind it: a read, draft or post of any kind is a handler's. Its own calls are {render.OWN_CALLS}."
        f" Start a handler with the drive's handler tool, briefed with this step in its lines, {', '.join(render.HANDLER_LABELS)};"
        " it does the read, draft or post and its line comes back as one cci record."
    )


def aimed(payload: dict) -> bool:
    """Whether the call may message a job, which messages names from the drive: a SendMessage or a Bash `drive job resume`."""
    return payload["tool_name"] == "SendMessage" or (payload["tool_name"] == "Bash" and bool(RESUME.search(payload["tool_input"].get("command") or "")))


def messages(payload: dict, data: dict) -> list[tuple[str, str, str]]:
    """Each job a coordinator's call messages, with the words it says to that job and the command before them: a local agent's by its SendMessage `to`, with the whole message, and each remote lane a `drive job resume` names, with that resume's own words up to the next one."""
    call = payload["tool_input"]
    if payload["tool_name"] == "SendMessage":
        job_id = state.agent_job(data, call.get("to"))
        return [(job_id, str(call.get("message") or ""), "")] if job_id else []
    command = call.get("command") or ""
    resumes = list(RESUME.finditer(command))
    return [
        (resumed[1], command[resumed.end() : following.start() if following else None], command[: resumed.start()])
        for resumed, following in zip(resumes, [*resumes[1:], None], strict=True)
        if resumed[1] in data["jobs"]
    ]


def resumed_builder(payload: dict, data: dict, job_id: str) -> str | None:
    """Why a SendMessage to a builder that returned BUILT or DONE carries no PR event: a resumed builder re-reads everything it ever did on every event, so a fresh one takes it; an incident's job is resumed for every event."""
    job = data["jobs"][job_id]
    if payload["tool_name"] != "SendMessage" or job["kind"] != "builder" or job["state"] not in FRESH_STATES or data["orders"][job["order"]]["incident"]:
        return None
    return (
        f"Refused on purpose: {job_id}'s builder already returned ({job['state']}), and a PR event after a builder returns goes"
        " to a fresh builder, since a resumed one re-reads everything it ever did on every event. Run `drive job add builder`"
        f" for order {job['order']} and its parts, `drive pr add <url> --job <new>` for each PR it takes over, brief it with a"
        f" `Handoff:` line naming the event and {job_id}'s transcript under `Transcripts:`, then"
        f' `drive job set {job_id} --state stopped --said "handed to <new>"`. His answers need no message: the drive holds each'
        " on every builder it frees, and the watch carries it to them."
    )


def woken_builder(payload: dict, data: dict, job_id: str) -> str | None:
    """Why a SendMessage does not wake a builder on this Mac whose turn ended: the wake takes one of this Mac's builder slots, which place.woken gives by the rule every start here goes through."""
    if payload["tool_name"] != "SendMessage" or not (why := place.woken(data, job_id, state.now())):
        return None
    return agents.Unjudged(
        f"Refused on purpose: {job_id}'s agent on this Mac takes no message now: {why}, so this message would start it again in a slot it has no turn for."
        f' `drive job resume {job_id} "<the message>"` hands it to a lane on a Sprite with the message in its handoff, or, when its'
        f" brief declares a step only this Mac runs on {place.MAC_LINES}, refuses until a slot opens; when its work holds no checkout or"
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
    """Why a message to a running job waits for a read of its transcript, earlier in the same command or in the last 10 minutes, when it tells the job what state it is in or asks it: an absent message is no evidence, and a state guessed reaches the job as fact. An order, a ruling, an answer or a refusal relayed states no state, even one that says stop, land or stuck, so it goes through unread, and so does a question whose subject is another job or CI, or another resume's words in the same command, since neither is about this job; Jev judges each refusal by its rule."""
    job = data["jobs"][job_id]
    claim = STATE_CLAIM.search(said)
    if job["state"] != "running" or not claim or transcript_read(job_id, job).search(before):
        return None
    if read_job(Path(payload["transcript_path"]), job_id, job):
        return None
    how = (
        f"`drive job replies {job_id}` or `orca orchestration worker-read --dispatch {job['dispatch']} --limit 50 --full --json`"
        if job.get("where") == "remote"
        else f"`cc-transcript` on agent {job['agent']}'s transcript"
    )
    return agents.Ruled(
        f"A message that tells {job_id} it stopped, stalled, went quiet or is stuck, or asks it whether it is, needs a read of"
        f" its transcript first (`{claim[0]}`), and your transcript shows none in the last 10 minutes, so"
        f" that state is a guess that reaches the job as fact. Read it with {how}, in an earlier call or before the resume in"
        " this command, then send.",
        STATE_RULE,
    )


def deny_reason(payload: dict) -> str | None:
    """Why a drive session's own call does not run: the communicator's turn holds only the ack and what starts a handler, and the coordinator and the meta session send no PR event to a builder that returned, wake no builder on this Mac whose turn ended while its builder slots are full, and tell no running job its state, or ask it, before they read that job's transcript."""
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
    for job_id, said, before in messages(payload, data):
        if why := resumed_builder(payload, data, job_id) or woken_builder(payload, data, job_id) or unread_job(payload, data, job_id, said, before):
            return why
    return None


def main() -> None:
    if reason := deny_reason(agents.seen(json.load(sys.stdin))):
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


if __name__ == "__main__":
    main()
