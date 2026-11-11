import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import limits, settled, state

RECORD_CHARS = 120
WHOLE_KINDS = {"owner", "answer", "incident"}
UNKEPT_ASK = (
    "refused: an ask record names the ASK `drive job ask` kept, as the attr `ask` set to the path it printed, and the"
    " communicator draws the card from that file; keep the question with `drive job ask <j> <file>`, then post this"
    " record again with that path"
)


def first_line(text: str) -> str:
    """A record's line as the other session sees it: its first line, cut at a word to the record limit."""
    line = text.strip().split("\n", 1)[0].strip()
    if len(line) <= RECORD_CHARS:
        return line
    return line[: RECORD_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def shortened(record: dict) -> dict | None:
    """The record with its text cut to one line and the whole text at the top of its body, or for a record that attaches a file as its body, which holds the rest, the line alone; his words and incidents stay whole."""
    text = (record.get("text") or "").strip()
    if (record.get("kind") or "").lower() in WHOLE_KINDS or (line := first_line(text)) == text:
        return None
    if record.get("path"):
        return record | {"text": line}
    body = record.get("body")
    return record | {"text": line, "body": f"{text}\n\n{body}" if body else text}


def unkept(record: dict, names: list[str]) -> str | None:
    """Why an ask record may not go out: it names no ASK `drive job ask` kept, which the communicator draws its card from."""
    if (record.get("kind") or "").lower() != "ask" or settled.kept(names, (record.get("attrs") or {}).get("ask")):
        return None
    return UNKEPT_ASK


def untapped(record: dict, prs: dict[str, dict]) -> str | None:
    """Why a ready record, which asks him to tap each PR it names, names one the drive owes no tap on."""
    if (record.get("kind") or "").lower() != "ready":
        return None
    named = [record.get("text") or "", record.get("body") or "", record.get("url") or "", *map(str, record.get("pr") or [])]
    return limits.tap_refusal("\n".join(named), prs)


def decision(payload: dict) -> dict | None:
    """What a drive session's post becomes: refused while an ask in it names no kept ASK or a record in it asks for a tap no PR it names is owed, else every record in it one line, the rest in its body for whoever acts on it."""
    names = state.session_drives(payload.get("session_id") or "")
    if not names:
        return None
    tool_input = payload["tool_input"]
    records = tool_input.get("records") or [tool_input]
    prs = {key: pr for name in names for key, pr in state.load(name)["prs"].items()}
    checks = (check for record in records for check in (unkept(record, names), untapped(record, prs)))
    if refused := next(filter(None, checks), None):
        return {"permissionDecision": "deny", "permissionDecisionReason": refused}
    cut = [shortened(record) for record in records]
    if not any(cut):
        return None
    if tool_input.get("records"):
        return {"updatedInput": tool_input | {"records": [new or old for new, old in zip(cut, records)]}}
    return {"updatedInput": cut[0]}


def main() -> None:
    if made := decision(json.load(sys.stdin)):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **made}}))


if __name__ == "__main__":
    main()
