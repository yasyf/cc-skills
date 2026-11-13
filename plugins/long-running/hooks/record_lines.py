import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, limits, settled, state, superseded
from drive.reports import CALL_FIELDS, addressed, attrs, kind, listed, posted, relayed_time, report_refusals, stray_parts, unprevented

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


def misaddressed(payload: dict, record: dict) -> str | None:
    """Why an agent's record goes to the wrong place: a reader or designer posting to him, or a handler posting a defect its return carries."""
    if (agent := payload.get("agent_type")) in SILENT_LANES and addressed(record, "owner"):
        return TO_OWNER.format(agent=agent.removeprefix("long-running:"))
    if payload.get("agent_type") == agents.HANDLER and kind(record) == "defect":
        return AGENT_DEFECT
    return None


def refusals(payload: dict, names: list[str], records: list[dict]) -> Iterator[str | None]:
    """Each check of each record, as cci stores it, in turn, so a refusal stops the rest from running."""
    drive = state.load(names[0])
    for index, record in enumerate(records):
        yield misaddressed(payload, record)
        yield stray_parts(kind(record), attrs(record))
        yield limits.misnamed(kind(record), attrs(record))
        yield settled.HAND_ASK if kind(record) == "ask" else None
        yield superseded.unclosed_refusal(names, kind(record), attrs(record), record.get("topic"), listed(record.get("re")), record.get("url"))
        yield state.unabout(kind(record), attrs(record))
        yield agents.Unjudged(unordered) if (unordered := state.unrecorded(state.session_role(drive, payload.get("session_id")), kind(record), attrs(record))) else None
        yield unready(record)
        yield from report_refusals(payload, drive, records, index)
        yield relayed_time(record, drive)


def readdressed(drives: dict[str, dict], session: str | None, record: dict) -> dict:
    """The record addressed where its drive routes it: a defect, and the communicator's owner record about the drive, to the meta session, the communicator's answer, handoff or refused record naming no job to the coordinator that wrote the record it answers, and its other owner, answer, handoff or refused record to the session holding the order it serves."""
    entries = listed(record.get("to"))
    to = state.routed(drives, session, kind(record), attrs(record), entries, listed(record.get("re")))
    return record if to == entries else record | {"to": to}


def decision(payload: dict) -> dict | None:
    """What a drive session's post becomes: refused with the fix while a record in it, read with the text, kind, lanes and attrs cci stores for it, breaks a rule its kind keeps, an ask record, which no session posts since `drive job ask` draws his card itself, included, else posted with a warning on each POST line telling him a failure of the drive's with no word on how it is prevented, and every record in it trimmed of the attrs it never carries and one line, the rest in its body for whoever acts on it, and each record sent where its drive routes it, a records list rewritten with each record whole, since a field cci filled in beside the list would put back what the record dropped."""
    names = state.session_drives(payload.get("session_id") or "")
    if not names:
        return None
    tool_input = payload["tool_input"]
    given = posted(tool_input)
    drive = state.load(names[0])
    records = [trimmed(record) for record in given]
    stored = [limits.as_stored(record) for record in records]
    if refused := next(filter(None, refusals(payload, names, stored)), None):
        return {"permissionDecision": "deny", "permissionDecisionReason": refused}
    warned = [warning for record in stored if (warning := unprevented(drive, record))]
    context = {"additionalContext": "\n".join(warned)} if warned else {}
    drives = state.bound_drives(names)
    made = [limits.shortened(routed) or routed for routed in (readdressed(drives, payload.get("session_id"), record) for record in records)]
    if made == given:
        return context or None
    if tool_input.get("records"):
        return {"updatedInput": {key: value for key, value in tool_input.items() if key in CALL_FIELDS} | {"records": made}} | context
    return {"updatedInput": made[0]} | context


def main() -> None:
    if made := decision(agents.seen(json.load(sys.stdin))):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **made}} | agents.ruled(made.get("permissionDecisionReason") or "")))


if __name__ == "__main__":
    main()
