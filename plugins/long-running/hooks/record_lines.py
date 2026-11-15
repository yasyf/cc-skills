import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, brief, install, limits, settled, state, superseded
from drive.reports import CALL_FIELDS, addressed, attrs, hand_ask, kind, listed, posted, relayed_time, report_refusals, stray_parts, unprevented

SILENT_LANES = {"long-running:reader", "long-running:designer"}
TO_OWNER = (
    "refused: a {agent} posts nothing to him, since the communicator's handler is the one voice he hears; keep what you found"
    " with `drive job report <j>` in your return, or send a tool's defect to meta@<drive>"
)
AGENT_DEFECT = (
    "refused: a handler posts no defect record, so only one fix starts; return a `DEFECT <tool>: <what"
    " happened, any refusal verbatim>` line, and the session holding drive tooling starts the fix. A step the session"
    " holding an order owes on it, such as redrawing its card, is a `handoff` record on the drive's `record` tool, its"
    " order as `attrs: {order: <o>}`, which reaches that session"
)
CATCH_UP = re.compile(r"Catch-up since\b", re.IGNORECASE)
READY_GONE = (
    "refused: no ready record draws his card: drive pr add draws it when the PR is registered, and the watch draws it on"
    " a PR still uncarded and redraws it in place as its stage moves; send none, and `drive status <pr>` names its stage"
)
LIMIT_ATTR = "max_words"


def unready(record: dict) -> str | None:
    """Why a ready record goes nowhere: his card on a PR is drawn by drive pr add and by the watch as the PR's stage moves, so no record draws it."""
    return READY_GONE if kind(record) == "ready" else None


def trimmed(record: dict) -> dict:
    """The record without the attrs it never carries: a `max_words`, since nothing reads it and his word limits for a post never yield to it, a catch-up report's `thread`, since a catch-up covers many orders and goes at his DM root, and a `thread` of none, which names no thread, so its post goes where one with no thread attr goes, as the meta session's rule "thread or none" has it."""
    named = attrs(record)
    catch_up = kind(record) == "report" and addressed(record, "communicator") and (named.get("catch_up") or CATCH_UP.search(record.get("text") or ""))
    kept = {key: value for key, value in named.items() if key != LIMIT_ATTR and not (key == "thread" and (catch_up or str(value).casefold() == "none"))}
    return record if kept == named else record | {"attrs": kept}


def amended(drive: dict, lane: str | None, record: dict) -> dict:
    """The record as cci keeps it: a report to the communicator re the seq of a POST report its own lane sent there corrects that report, whatever kind its writer named, so the watch starts the handler that rewords the post it went out in, and no rule of reports, one a thread a turn included, holds it to the next turn."""
    named = listed(record.get("re"))
    if kind(record) != "report" or not addressed(record, "communicator") or len(named) != 1 or not named[0].isdigit():
        return record
    earlier = agents.shown(named[0])
    if earlier["lane"] != lane or earlier["kind"] != "report" or not agents.posting(drive["name"], earlier):
        return record
    return record | {"kind": "correction"}


def misaddressed(payload: dict, record: dict) -> str | None:
    """Why an agent's record goes to the wrong place: a reader or designer posting to him, or a handler posting a defect its return carries."""
    if (agent := payload.get("agent_type")) in SILENT_LANES and addressed(record, "owner"):
        return TO_OWNER.format(agent=agent.removeprefix("long-running:"))
    if payload.get("agent_type") == agents.HANDLER and kind(record) == "defect":
        return AGENT_DEFECT
    return None


def refusals(payload: dict, names: list[str], drives: dict[str, dict], records: list[dict]) -> Iterator[str | None]:
    """Each check of each record, as cci stores it, in turn, so a refusal stops the rest from running."""
    drive = drives[names[0]]
    for index, record in enumerate(records):
        yield misaddressed(payload, record)
        yield state.unread(drives, listed(record.get("to")), payload["tool_input"].get("drive") or names[0])
        yield stray_parts(kind(record), attrs(record))
        yield limits.misnamed(kind(record), attrs(record))
        yield hand_ask(record)
        yield superseded.unclosed_refusal(names, kind(record), attrs(record), record.get("topic"), listed(record.get("re")), record.get("url"))
        yield state.unabout(kind(record), attrs(record))
        yield agents.Unjudged(unordered) if (unordered := state.unrecorded(state.session_role(drive, payload.get("session_id")), kind(record), attrs(record))) else None
        yield unready(record)
        yield from report_refusals(payload, drive, records, index)
        yield relayed_time(record, drive)


def record_words(record: dict) -> str:
    return "\n".join(filter(None, [record.get("text"), record.get("body"), record.get("path") and Path(record["path"]).read_text()]))


def overruled_handoffs(drive: dict, records: list[dict]) -> tuple[str | None, list[str]]:
    """Why a handoff record may not go, and what the session hears when it goes unread: a line of a list in a handoff's body says no ruling of his covers, puts up for change or asks him again an item a recorded answer of his orders or keeps, as settled.overruled reads it, naming that answer's id and words; a handoff Jev gives no reading of goes with a warning."""
    if not (lines := [line for record in records if kind(record) == "handoff" for line in settled.list_lines(record_words(record))]):
        return None, []
    try:
        picks = settled.overruled(list(dict.fromkeys(lines)), drive)
    except settled.Unheard as failure:
        return None, [f"record_lines: this handoff goes as written; {settled.UNREAD_LINES.format(failure=failure)}"]
    if not picks:
        return None, []
    found = settled.overruling(picks)
    return agents.Ruled(f"refused: as Jev reads it, {found}. His words stand until he lifts them: keep each item as his answer orders, saying so, or leave it out", settled.OVERRULED_RULE, found=found), []


def readdressed(drives: dict[str, dict], session: str | None, record: dict) -> dict:
    """The record addressed where its drive routes it: a defect, and the communicator's owner record about the drive, to the meta session, the communicator's answer, handoff or refused record naming a job a coordinator session's agent runs on this Mac to that session, its answer, handoff or refused record naming no job to the coordinator that wrote the record it answers, and its other owner, answer, handoff or refused record to the session holding the order it serves."""
    entries = listed(record.get("to"))
    to = state.routed(drives, session, kind(record), attrs(record), entries, listed(record.get("re")))
    return record if to == entries else record | {"to": to}


def posting_lane(data: dict, payload: dict, records: list[dict]) -> str:
    """The cci lane a drive session's post goes out as, whatever lane it names: the session's own, a sub-coordinator's its project's, so a record a job's agent writes reads as the session's that started it and a reply reaches that session; an agent's post holding a record every `to` of which names that lane goes out as the agent's own type, such as builder, since cci refuses a record from a lane addressed only to itself."""
    lane = state.session_lane(data, payload.get("session_id"))
    agent = payload.get("agent_type")
    own = {lane, f"{lane}@{data['name']}"}
    if agent and any((to := set(listed(record.get("to")))) and to <= own for record in records):
        return agent.removeprefix("long-running:")
    return lane


def decision(payload: dict) -> dict | None:
    """What a drive session's post becomes: refused with the fix while a record in it, read with the text, kind, lanes and attrs cci stores for it, breaks a rule its kind keeps, an ask record carrying his card's ASK block, which only `drive job ask` keeps and draws, included, while any other ask, a question to another session's lane, goes out, or a handoff whose list line goes against a recorded answer of his, by overruled_handoffs, else posted, but a record brief.release_asks reads as asking a session for a step of a production release, which stays out while the rest of its post goes with its refusal said, the post refused once it holds no other, each report re a POST report its own lane sent going out as the correction it is, with a warning on each POST line telling him a failure of the drive's with no word on how it is prevented, and every record in it trimmed of the attrs it never carries and one line, the rest in its body for whoever acts on it, each record sent where its drive routes it, and the call posted as posting_lane names, a records list rewritten with each record whole, since a field cci filled in beside the list would put back what the record dropped."""
    names = state.session_drives(payload.get("session_id") or "")
    if not names:
        return None
    tool_input = payload["tool_input"]
    given = posted(tool_input)
    drives = state.bound_drives(names)
    drive = drives[names[0]]
    writer = state.session_lane(drive, payload.get("session_id"))
    records = [amended(drive, writer, trimmed(record)) for record in given]
    stored = [limits.as_stored(record) for record in records]
    if refused := next(filter(None, refusals(payload, names, drives, stored)), None):
        return {"permissionDecision": "deny", "permissionDecisionReason": refused}
    asked = brief.release_asks([(kind(record), listed(record.get("to")), record_words(record)) for record in stored], drive)
    if len(asked) == len(stored):
        return {"permissionDecision": "deny", "permissionDecisionReason": agents.Unjudged("; ".join(asked.values()))}
    records, stored = ([record for index, record in enumerate(kept) if index not in asked] for kept in (records, stored))
    overruled, unread = overruled_handoffs(drive, stored)
    if overruled:
        return {"permissionDecision": "deny", "permissionDecisionReason": overruled}
    warned = [*(f"record_lines: the rest went out, but not {why}" for why in asked.values()), *unread, *(warning for record in stored if (warning := unprevented(drive, record)))]
    context = {"additionalContext": "\n".join(warned)} if warned else {}
    made = [limits.shortened(routed) or routed for routed in (readdressed(drives, payload.get("session_id"), record) for record in records)]
    lane = posting_lane(drive, payload, made)
    if made == given and tool_input.get("lane") == lane:
        return context or None
    if tool_input.get("records"):
        return {"updatedInput": {key: value for key, value in tool_input.items() if key in CALL_FIELDS} | {"lane": lane, "records": made}} | context
    return {"updatedInput": made[0] | {"lane": lane}} | context


def main() -> None:
    payload = agents.seen(json.load(sys.stdin))
    try:
        made = decision(payload)
    except state.NewerDrive:
        if install.rechecked(Path(__file__).name, payload):
            return
        raise
    if made:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **made}} | agents.ruled(made.get("permissionDecisionReason") or "")))


if __name__ == "__main__":
    main()
