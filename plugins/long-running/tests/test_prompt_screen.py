from __future__ import annotations

import prompt_screen
import pytest

WIDTH = 80
RULE = "─" * WIDTH
MODE = "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
IDLE = ("⏺ Done.", RULE, "❯ ", RULE, MODE.ljust(WIDTH), "447569 tokens".rjust(WIDTH - 2))
AGENT_LIST = (
    *IDLE,
    "  ⏺ main",
    "  ◯ fix-core                     Fix hacks: core".ljust(WIDTH - 12) + "3h 54m 14s",
    "  ↓ 19 more",
)
ASK_USER_QUESTION = (
    RULE,
    " ☐ Test page",
    "│ Should I kick off the end-to-end test page now?",
    "❯ 1. Kick it off now (Recommended)",
    "  2. After release starts work again",
    "  3. Type something.",
    RULE,
    "  4. Chat about this",
    "Enter to select · ↑/↓ to navigate · Esc to cancel",
)
PERMISSION = (
    "⏺ Computer Use[request_access]",
    RULE,
    "  Computer Use wants to control these apps",
    "     ◉ Slack",
    "   ❯ Deny, and tell Claude what to do differently (esc)",
    "     Allow for this session (1 app)",
    "  Enter to confirm · Esc to cancel",
)
TRUST = (
    RULE,
    " Accessing workspace:",
    " Quick safety check: Is this a project you created or one you trust?",
    " ❯ No, exit",
    "   Yes, I trust this folder",
    " Enter to confirm · Esc to cancel",
)


def screen(*tail: str, **extra: str) -> dict:
    return {"source": "screen", "tail": list(tail), **extra}


@pytest.mark.parametrize(
    ("terminal", "idle"),
    [
        (screen(*IDLE), True),
        (screen("⏺ done", RULE, "❯\xa0", RULE), True),
        (screen("❯ update the plan again", "  ⎿ ok", RULE, "❯", RULE, MODE, "", "new task? /clear to save 448.8k tokens".rjust(WIDTH - 2)), True),
        (screen(RULE, "❯", RULE, "  ⏸ plan mode on (shift+tab to cycle)", "current: 2.1.287 · latest: 2.1.288 ✔ Update installed".rjust(WIDTH - 2)), True),
        (screen(*AGENT_LIST), False),
        (screen(*ASK_USER_QUESTION), False),
        (screen(*PERMISSION), False),
        (screen(*TRUST), False),
        (screen(RULE, "❯", RULE, MODE, "  /compact          Clear conversation history but keep a summary"), False),
        (screen(RULE, "❯ half-typed ask", RULE, MODE), False),
        (screen(RULE, "❯ first line", "  second line", RULE, MODE), False),
        (screen("❯ 1. My other session owns it", "  2. Go ahead"), False),
        (screen(*IDLE, draft="/compact  <optional custom summarization instructions>"), False),
        ({"source": "screen-unavailable", "tail": list(IDLE)}, False),
    ],
)
def test_only_the_plain_empty_prompt_is_idle(terminal: dict, idle: bool) -> None:
    assert prompt_screen.idle_prompt(terminal) is idle
