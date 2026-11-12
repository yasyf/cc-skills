import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, limits, settled, state, superseded
from drive.reports import addressed, attrs, kind, listed, report_refusals, unprevented

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
UNKINDED = "refused: a ready record says which kind its PRs are: `his order o<n>` before his quoted words, `Drive tooling: …`, or `port of #<n>`; add the one it is"
LIMIT_ATTR = "max_words"


def untapped(record: dict, prs: dict[str, dict]) -> str | None:
    """Why a ready record to the communicator, which draws his card asking him to tap each PR it names, names a PR the drive owes no tap on."""
    if kind(record) != "ready" or not addressed(record, "communicator"):
        return None
    return limits.tap_refusal(naming(record), prs)


def unchecked(record: dict, drives: list[dict]) -> str | None:
    """Why a ready record to the communicator asks his tap on a drive PR whose change at its head nobody checked against his words, so its line would call ready a change nobody judged, naming who checks it: its order's holder."""
    if kind(record) != "ready" or not addressed(record, "communicator"):
        return None
    found = [
        f"{key} at {pr['head'][:7]} (order {pr['order']}, held by the {state.title(data['orders'][pr['order']]['owner'] or 'coordinator')})"
        for data in drives
        for key in state.named_prs(data, "", naming(record))
        if state.asks_tap(pr := data["prs"][key]) and not state.checked(data, pr)
    ]
    if not found:
        return None
    return (
        f"refused: this ready record asks his tap on {'; '.join(found)}, whose change at that head is not checked against his words."
        " Its builder's READY checks it through drive job report, or its order's holder runs"
        " drive pr set <url> --serves <o> --design <asked:<his answer> or none>; the watch sends the ready record itself once each PR is checked"
    )


def naming(record: dict) -> str:
    return "\n".join([record.get("text") or "", record.get("body") or "", record.get("url") or "", *map(str, record.get("pr") or [])])


def unkinded(record: dict) -> str | None:
    """Why a ready record does not say which kind of work its PRs are, the first thing he asks of a PR card."""
    return UNKINDED if kind(record) == "ready" and not limits.KIND_NAMED.search(record.get("text") or "") else None


def completed(names: list[str], drive: dict, record: dict) -> dict:
    """The record with what the drive already knows filled in: an ask's kept file as its ask attr when it attaches that file as its path, and, for a job on an open incident order, incident=true and the incident's thread; and a ready record's kind, read from the order its order attr names, when its text names none."""
    named = attrs(record)
    if kind(record) == "ask":
        filled = named | ({"ask": record["path"]} if not named.get("ask") and settled.keeper(names, record.get("path")) else {})
        filled |= settled.incident_marks(names, filled)
        return record if filled == named else record | {"attrs": filled}
    order = drive["orders"].get(named.get("order") or "")
    if kind(record) != "ready" or order is None or limits.KIND_NAMED.search(record.get("text") or ""):
        return record
    words = order["text"] if order["text"].startswith(state.TOOLING) else f'his order {named["order"]} "{order["text"]}"'
    return record | {"text": f"{record['text']}; {words}"}


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
    drives = [state.load(name) for name in names]
    drive = drives[0]
    prs = {key: pr for data in drives for key, pr in data["prs"].items()}
    for index, record in enumerate(records):
        yield misaddressed(payload, record)
        yield limits.misnamed(kind(record), attrs(record))
        yield settled.ask_refusal(names, attrs(record), record.get("text") or "") if kind(record) == "ask" else None
        yield superseded.unclosed_refusal(names, kind(record), attrs(record), record.get("topic"), listed(record.get("re")))
        yield state.unabout(kind(record), attrs(record))
        yield agents.Unjudged(unordered) if (unordered := state.unrecorded(state.session_role(drive, payload.get("session_id")), kind(record), attrs(record))) else None
        yield unkinded(record)
        yield untapped(record, prs)
        yield unchecked(record, drives)
        yield from report_refusals(payload, drive, records, index)
        yield None if kind(record) in agents.HIS_KINDS else agents.typed_time(f"{record.get('text') or ''}\n{record.get('body') or ''}", drive)


def readdressed(drives: dict[str, dict], session: str | None, record: dict) -> dict:
    """The record addressed where its drive routes it: a defect, and the communicator's owner record about the drive, to the meta session, and the communicator's other owner, answer, handoff, posted or refused record to the session holding the order it serves."""
    entries = listed(record.get("to"))
    to = state.routed(drives, session, kind(record), attrs(record), entries)
    return record if to == entries else record | {"to": to}


def decision(payload: dict) -> dict | None:
    """What a drive session's post becomes: refused with the fix while a record in it, read with the text cci stores for it, breaks a rule its kind keeps, else posted with a warning on each POST line telling him a failure of the drive's with no word on how it is prevented, and every record in it completed from what the drive knows, trimmed of the attrs it never carries and one line, the rest in its body for whoever acts on it, and each record sent where its drive routes it."""
    names = state.session_drives(payload.get("session_id") or "")
    if not names:
        return None
    tool_input = payload["tool_input"]
    given = tool_input.get("records") or [tool_input]
    drive = state.load(names[0])
    records = [trimmed(completed(names, drive, record)) for record in given]
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
        return {"updatedInput": tool_input | {"records": made}} | context
    return {"updatedInput": made[0]} | context


def main() -> None:
    if made := decision(agents.seen(json.load(sys.stdin))):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **made}} | agents.ruled(made.get("permissionDecisionReason") or "")))


if __name__ == "__main__":
    main()
