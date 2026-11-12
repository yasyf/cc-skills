import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, limits, settled, state, superseded
from drive.reports import addressed, attrs, kind, listed, report_refusals

SILENT_LANES = {"long-running:reader", "long-running:designer"}
TO_OWNER = (
    "refused: a {agent} posts nothing to him, since the communicator's handler is the one voice he hears; keep what you found"
    " with `drive job report <j>` in your return, or send a tool's defect to meta@<drive>"
)
AGENT_DEFECT = (
    "refused: a handler posts no defect record, so only one fix starts; return a `DEFECT <tool>: <what"
    " happened, any refusal verbatim>` line, and the session holding drive tooling starts the fix"
)
CATCH_UP = re.compile(r"Catch-up since\b", re.IGNORECASE)
TAP_ASKED = re.compile(r"\btap (?:Approve|Hold)\b|\b(?:needs?|waits? (?:on|for)) (?:your|his|a) tap\b", re.IGNORECASE)
UNKINDED = "refused: a ready record says which kind its PRs are: `his order o<n>` before his quoted words, `Drive tooling: …`, or `port of #<n>`; add the one it is"
LIMIT_ATTR = "max_words"


def untapped(record: dict, prs: dict[str, dict]) -> str | None:
    """Why a ready record, which asks him to tap each PR it names, or a report or ask that asks for a tap, names a PR the drive owes no tap on."""
    if kind(record) not in ("ready", "report", "ask") or (kind(record) != "ready" and not TAP_ASKED.search(record.get("text") or "")):
        return None
    named = [record.get("text") or "", record.get("body") or "", record.get("url") or "", *map(str, record.get("pr") or [])]
    return limits.tap_refusal("\n".join(named), prs)


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
    """The record without the attrs it never carries: a `max_words`, since nothing reads it and his word limits for a post never yield to it, and a catch-up report's `thread`, since a catch-up covers many orders and goes at his DM root."""
    named = attrs(record)
    catch_up = kind(record) == "report" and addressed(record, "communicator") and (named.get("catch_up") or CATCH_UP.search(record.get("text") or ""))
    kept = {key: value for key, value in named.items() if key != LIMIT_ATTR and not (catch_up and key == "thread")}
    return record if kept == named else record | {"attrs": kept}


def misaddressed(payload: dict, record: dict) -> str | None:
    """Why an agent's record goes to the wrong place: a reader or designer posting to him, or a handler posting a defect its return carries."""
    if (agent := payload.get("agent_type")) in SILENT_LANES and addressed(record, "owner"):
        return TO_OWNER.format(agent=agent.removeprefix("long-running:"))
    if payload.get("agent_type") == agents.HANDLER and kind(record) == "defect":
        return AGENT_DEFECT
    return None


def refusals(payload: dict, names: list[str], records: list[dict]) -> Iterator[str | None]:
    """Each check of each record in turn, so a refusal stops the rest from running."""
    drive = state.load(names[0])
    prs = {key: pr for name in names for key, pr in state.load(name)["prs"].items()}
    for index, record in enumerate(records):
        yield misaddressed(payload, record)
        yield limits.misnamed(kind(record), attrs(record))
        yield settled.ask_refusal(names, attrs(record), record.get("text") or "") if kind(record) == "ask" else None
        yield superseded.unclosed_refusal(names, kind(record), attrs(record), record.get("url"), listed(record.get("re")))
        yield state.unabout(kind(record), attrs(record))
        yield unkinded(record)
        yield untapped(record, prs)
        yield from report_refusals(payload, drive, records, index)


def readdressed(drives: dict[str, dict], session: str | None, record: dict) -> dict:
    """The record addressed where its drive routes it: a defect, and the communicator's owner record about the drive, to the meta session, and the communicator's other owner, answer, posted or refused record to the session hearing the order it serves."""
    entries = listed(record.get("to"))
    to = state.routed(drives, session, kind(record), attrs(record), entries)
    return record if to == entries else record | {"to": to}


def decision(payload: dict) -> dict | None:
    """What a drive session's post becomes: refused with the fix while a record in it breaks a rule its kind keeps, else every record in it completed from what the drive knows, trimmed of the attrs it never carries and one line, the rest in its body for whoever acts on it, and each record sent where its drive routes it."""
    names = state.session_drives(payload.get("session_id") or "")
    if not names:
        return None
    tool_input = payload["tool_input"]
    given = tool_input.get("records") or [tool_input]
    drive = state.load(names[0])
    records = [trimmed(completed(names, drive, record)) for record in given]
    if refused := next(filter(None, refusals(payload, names, records)), None):
        return {"permissionDecision": "deny", "permissionDecisionReason": refused}
    drives = state.bound_drives(names)
    made = [limits.shortened(routed) or routed for routed in (readdressed(drives, payload.get("session_id"), record) for record in records)]
    if made == given:
        return None
    if tool_input.get("records"):
        return {"updatedInput": tool_input | {"records": made}}
    return {"updatedInput": made[0]}


def main() -> None:
    if made := decision(agents.seen(json.load(sys.stdin))):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **made}} | agents.ruled(made.get("permissionDecisionReason") or "")))


if __name__ == "__main__":
    main()
