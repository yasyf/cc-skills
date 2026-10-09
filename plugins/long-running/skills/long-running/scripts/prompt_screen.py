from __future__ import annotations

RULE = "─"
PROMPT = "❯"
MODE_INDENT = 2
MARGIN = 2


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
