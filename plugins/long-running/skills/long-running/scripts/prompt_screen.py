from __future__ import annotations

import re
from dataclasses import dataclass

RULE = "─"
PROMPT = "❯"
MODE_INDENT = 2
MARGIN = 2
APPROVAL = "approval"
QUESTION = "question"
DIALOG_ROWS = 30
CODEX_ROWS = 4
KEY_HINT = r"[\w↑↓/+ ]+ to [\w ]+"
FOOTER = re.compile(rf"^\s*{KEY_HINT}(?:\s*·\s*{KEY_HINT})*\s*$")
CANCEL = re.compile(r"\besc to cancel\b", re.IGNORECASE)
PICKER = re.compile(r"\bto (?:select|navigate)\b", re.IGNORECASE)
CURSOR = re.compile(r"^\s*[❯›]\s*\S")
NUMBERED = re.compile(r"^\s*[❯›]?\s*\d+\.\s+\S")
CODEX_COMPOSER = re.compile(r"^›(?:\s|$)")
IDLE = "idle"
BUSY = "busy"
WAITING = "waiting"
RUNNING = re.compile(r"\besc to interrupt\b|\((?:\d+h )?(?:\d+m )?\d+s [·•]", re.IGNORECASE)
RUNNING_ROWS = 12
FRAME = "│☐ "
WORD = re.compile(r"\w")


@dataclass(frozen=True)
class Dialog:
    kind: str
    asked: str


def indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def idle_prompt(terminal: dict) -> bool:
    """True only when an `orca terminal read --screen` result ends in Claude Code's plain prompt.

    That is an empty `❯` line between the last two rules, then at most the mode line and notices
    right-aligned to the rule's margin, so typed keys never land in a picker, dialog, or agent list.
    """
    if terminal.get("draft") or terminal.get("source") != "screen":
        return False
    tail = [line.rstrip() for line in terminal.get("tail") or []]
    rules = [i for i, line in enumerate(tail) if line and set(line.strip()) == {RULE}]
    if len(rules) < 2 or rules[-1] - rules[-2] != 2 or tail[rules[-2] + 1].strip() != PROMPT:
        return False
    width = len(tail[rules[-1]])
    return all(
        not line
        or (row == 0 and indent(line) == MODE_INDENT)
        or (indent(line) > MODE_INDENT and len(line) == width - MARGIN)
        for row, line in enumerate(tail[rules[-1] + 1 :])
    )


def is_rule(line: str) -> bool:
    return bool(line.strip()) and set(line.strip()) == {RULE}


def composer(tail: list[str]) -> int | None:
    """The row where the agent's own input box starts, Claude Code's ruled `❯` box or Codex's `›` line, or None when the screen shows neither."""
    rules = [i for i, line in enumerate(tail) if is_rule(line)]
    if len(rules) >= 2 and tail[rules[-2] + 1].lstrip().startswith(PROMPT) and not NUMBERED.match(tail[rules[-2] + 1]):
        return rules[-2]
    shown = [i for i, line in enumerate(tail) if line.strip()][-CODEX_ROWS:]
    return next((i for i in reversed(shown) if CODEX_COMPOSER.match(tail[i]) and not NUMBERED.match(tail[i])), None)


def dialog(terminal: dict) -> Dialog | None:
    """The dialog an `orca terminal read --screen` result holds open, or None.

    A dialog is a key-hint footer naming `Esc to cancel` with a selection cursor above it and no input
    box below it; a footer followed by the agent's input box is scrollback. A picker's footer offers to
    select or navigate and makes a question; any other footer makes an approval. What it asks is every
    row of text from the dialog's top to its footer, less a row that shows a running timer, so two
    dialogs read alike only when their text and options match.
    """
    if terminal.get("source") != "screen":
        return None
    tail = [line.rstrip() for line in terminal.get("tail") or []]
    footer = next((i for i in reversed(range(len(tail))) if FOOTER.match(tail[i]) and CANCEL.search(tail[i])), None)
    if footer is None or ((box := composer(tail)) is not None and box > footer):
        return None
    start = max(0, footer - DIALOG_ROWS)
    if (cursor := next((i for i in reversed(range(start, footer)) if CURSOR.match(tail[i])), None)) is None:
        return None
    top = next((i + 1 for i in reversed(range(start, cursor)) if is_rule(tail[i])), start)
    rows = [line.strip(FRAME) for line in tail[top:footer] if WORD.search(line) and not RUNNING.search(line)]
    return Dialog(QUESTION if PICKER.search(tail[footer]) else APPROVAL, " / ".join(rows))


def input_state(terminal: dict) -> str | None:
    """What a line typed into this screen would meet, or None when the screen shows no dialog and no input box.

    `idle` is Claude Code's plain empty prompt or Codex's `›` line, with no draft and no turn in flight,
    so the line starts a turn. `waiting` is an open dialog. `busy` is an input box with a turn running,
    a draft, or a list under it, where the line would queue or land in the wrong place.
    """
    if terminal.get("source") != "screen":
        return None
    if dialog(terminal):
        return WAITING
    tail = [line.rstrip() for line in terminal.get("tail") or []]
    if (box := composer(tail)) is None:
        return None
    empty = idle_prompt(terminal) or (bool(CODEX_COMPOSER.match(tail[box])) and not terminal.get("draft"))
    return IDLE if empty and not any(RUNNING.search(line) for line in tail[-RUNNING_ROWS:]) else BUSY
