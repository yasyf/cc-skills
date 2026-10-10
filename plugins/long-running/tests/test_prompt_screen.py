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


HOOK_APPROVAL = (
    RULE,
    " Bash command · from the startup-speed agent",
    " │ Time the current single-stream fetch with its SHA-256 check on the measurement Sprite as a same-host control",
    " This shell -c script runs rm and could not be checked",
    " Do you want to proceed?",
    " ❯ 1. Yes",
    "   2. No",
    " Esc to cancel · Tab to amend",
)
AGENT_PANEL = ("", "  ⏺ main", "  ◯ startup-speed             Speed up remote lane startup".ljust(WIDTH), "  ↓ 3 more")
CODEX_APPROVAL = (
    "  Would you like to run the following command?",
    "",
    "  $ rm -rf build/out",
    "",
    "› 1. Yes, proceed (y)",
    "  2. No, and tell Codex what to do differently (esc)",
    "",
    "  Press enter to confirm or esc to cancel",
)
CODEX_WORKING = ("• Working (12s • esc to interrupt)", "› Ask Codex to do anything", "  GPT-6.1-Sol xhigh · ~/app", "  ? for shortcuts")
PRINTED_DIALOG = (" Do you want to proceed?", " ❯ 1. Yes", "   2. No", " Esc to cancel · Tab to amend")


@pytest.mark.parametrize(
    ("terminal", "kind", "excerpt"),
    [
        (screen(*HOOK_APPROVAL), "approval", "Time the current single-stream fetch with its SHA-256 check on the measurement Sprite as a same-host control / This shell -c script runs rm and could not be checked / Do you want to proceed?"),
        (screen("⏺ Two lanes report in.", *HOOK_APPROVAL, *AGENT_PANEL), "approval", "Time the current single-stream fetch with its SHA-256 check on the measurement Sprite as a same-host control / This shell -c script runs rm and could not be checked / Do you want to proceed?"),
        (screen(*ASK_USER_QUESTION), "question", "Test page / Should I kick off the end-to-end test page now?"),
        (screen(RULE, " ☐ Rebase", "  1. Rebase now", "❯ 2. Wait", RULE, "Enter to select · ↑/↓ to navigate · Esc to cancel"), "question", "Rebase"),
        (screen(*PERMISSION), "approval", "Computer Use wants to control these apps / ◉ Slack"),
        (screen(*TRUST), "approval", "Accessing workspace: / Quick safety check: Is this a project you created or one you trust?"),
        (screen(*CODEX_APPROVAL), "approval", "Would you like to run the following command? / $ rm -rf build/out"),
    ],
)
def test_a_dialog_is_a_cancel_footer_under_a_selection_cursor(terminal: dict, kind: str, excerpt: str) -> None:
    assert prompt_screen.dialog(terminal) == prompt_screen.Dialog(kind, excerpt)


@pytest.mark.parametrize(
    "terminal",
    [
        screen(*IDLE),
        screen(*AGENT_LIST),
        screen(*PRINTED_DIALOG, *IDLE),
        screen(*PRINTED_DIALOG, *CODEX_WORKING),
        screen('  25 "Enter to select · ↑/↓ to navigate · Esc to cancel",', "❯ 1. Yes"),
        screen("❯ 1. Yes", "  2. No", "Press esc to cancel the run: it is still going"),
        screen(" Do you want to proceed?", "   1. Yes", "   2. No", " Esc to cancel · Tab to amend"),
        {"source": "screen-unavailable", "tail": list(HOOK_APPROVAL)},
        {"source": "screen", "tail": []},
    ],
)
def test_a_footer_above_the_input_box_or_with_no_cursor_is_no_dialog(terminal: dict) -> None:
    assert prompt_screen.dialog(terminal) is None


@pytest.mark.parametrize(
    ("tail", "box"),
    [
        (IDLE, 1),
        (AGENT_LIST, 1),
        ((RULE, "❯ half-typed ask", RULE, MODE), 0),
        (CODEX_WORKING, 1),
        (HOOK_APPROVAL, None),
        (ASK_USER_QUESTION, None),
        ((RULE, "❯ 1. Yes", "  2. No", RULE), None),
        (CODEX_APPROVAL, None),
        ((" Resume Session", "", "   Loading conversations…"), None),
    ],
)
def test_the_input_box_is_claudes_ruled_prompt_or_codexs_composer_line(tail: tuple[str, ...], box: int | None) -> None:
    assert prompt_screen.composer(list(tail)) == box
