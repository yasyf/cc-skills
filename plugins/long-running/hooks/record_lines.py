import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import settled, state

RECORD_CHARS = 120
WHOLE_KINDS = {"owner", "answer", "incident"}
WITH_FILE = (
    f"refused: a drive record's text is one line of at most {RECORD_CHARS} characters, and this record attaches a file"
    " as its body; put the rest of its text in that file."
)


def first_line(text: str) -> str:
    """A record's line as the other session sees it: its first line, cut at a word to the record limit."""
    line = text.strip().split("\n", 1)[0].strip()
    if len(line) <= RECORD_CHARS:
        return line
    return line[: RECORD_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def shortened(record: dict) -> dict | None:
    """The record with its text cut to one line and the whole text at the top of its body; his words and incidents stay whole."""
    text = (record.get("text") or "").strip()
    if (record.get("kind") or "").lower() in WHOLE_KINDS or (line := first_line(text)) == text:
        return None
    body = record.get("body")
    return record | {"text": line, "body": f"{text}\n\n{body}" if body else text}


def unsettled(record: dict, names: list[str], cwd: str) -> str | None:
    """Why an ask record may not go out: no kept ASK checked it, and a recorded answer of his may already settle its question."""
    attrs = record.get("attrs") or {}
    if (record.get("kind") or "").lower() != "ask" or settled.kept(names, attrs.get("ask")):
        return None
    return settled.refusal(record.get("text") or "", cwd, attrs.get("unsettled"), "the attr `unsettled` set to")


def decision(payload: dict) -> dict | None:
    """What a drive session's post becomes: refused while an ask in it may be settled already, else every record in it one line, the rest in its body for whoever acts on it."""
    names = state.session_drives(payload.get("session_id") or "")
    if not names:
        return None
    tool_input = payload["tool_input"]
    records = tool_input.get("records") or [tool_input]
    cwd = payload.get("cwd") or os.getcwd()
    if refused := next(filter(None, (unsettled(record, names, cwd) for record in records)), None):
        return {"permissionDecision": "deny", "permissionDecisionReason": refused}
    cut = [shortened(record) for record in records]
    if not any(cut):
        return None
    if any(new and old.get("path") for new, old in zip(cut, records)):
        return {"permissionDecision": "deny", "permissionDecisionReason": WITH_FILE}
    if tool_input.get("records"):
        return {"updatedInput": tool_input | {"records": [new or old for new, old in zip(cut, records)]}}
    return {"updatedInput": cut[0]}


def main() -> None:
    if made := decision(json.load(sys.stdin)):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **made}}))


if __name__ == "__main__":
    main()
