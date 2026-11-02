from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, T, on

from .compaction_handoff import CompactionState
from .root_replies import SYSTEM_PREFIXES, owner_text

ACTIVE = [CompactionState(active=True, slug="brook")]
CORRECTION = re.compile(
    r"\bwhy (?:did|didn'?t|are|do|would|have|haven'?t) you\b|\byou (?:still|again|keep|never|didn'?t|did not|refuse|continue to)\b"
    r"|\bhow many times\b|\bi (?:already (?:said|told)|told you)\b|\bstop (?:doing|being)\b|\bnot what i (?:asked|said)\b|\bwtf\b|\bself-?impro\w*",
    re.IGNORECASE,
)
DEFECT_WORDS = re.compile(r"\b(?:dead|silent|stuck|defect|broke|broken|wrong|failed|misfired|no record)\b", re.IGNORECASE)
MECHANISM = re.compile(r"\bmechanism:", re.IGNORECASE)
CHANGE = re.compile(r"\bchange:(?P<change>.*?)(?=\bfix-live:|\Z)", re.IGNORECASE | re.DOTALL)
FIX_LIVE = re.compile(r"\bfix-live:", re.IGNORECASE)
MACHINERY = re.compile(r"[\w./-]+\.(?:py|sh|md|toml|go|ts|tsx|json)\b|\bhook [\w-]+|\b[a-z]+_[a-z_]+\b")
LANDING = re.compile(r"(?:\b[\w.-]+(?:/[\w.-]+)?)?#\d+\b|\binstall", re.IGNORECASE)
LOOP_FIX = re.compile(r"\b(?:yasyf/)?(?:cc-skills|cc-inbox)#(?P<pr>\d+)\b")
LOOP = re.compile(r"\b(?:loop|outage|incident|alarm|re-?alarms?|recurr\w*)\b", re.IGNORECASE)
INSTALL = re.compile(r"\binstall", re.IGNORECASE)
TRIGGER_KINDS = frozenset({"defect", "blocker"})
LOG_APPEND = "__log_append"
OWNER_WORDS = "every time sometjing goes wrong you need to self-imprvie, and yet you continue to refuse to do that"
UNIMPROVED = (
    "After a correction, repeat, defect stand-down or tooling defect, root posts a `defect` record with `Mechanism:`, "
    "`Change:` naming the file or hook plus its PR or install order, and `FIX-LIVE:`, and adds the retro row. Post both, then stop again."
    f"\n```\nOwner, 10:29 PM Nov 1: {OWNER_WORDS}\n```"
)
UNLANDED = (
    "A cc-skills or cc-inbox PR named as the fix for a live loop arms auto-merge and carries an install order in the same turn. "
    "Run `gh pr merge <n> --repo yasyf/<repo> --auto --squash` and post the install order, then stop again."
)
GOOD_DEFECT = (
    "cci post --drive d1 --lane root --kind defect --text 'Mechanism: root answered the freeze with a worker. "
    "Change: hooks/self_improve.py blocks the turn, cc-skills#1690. FIX-LIVE: the hook blocks one root turn in a test.'"
)
LANE_ONLY = "cci post --drive d1 --lane root --kind defect --text 'Mechanism: root froze releases. Change: spawn release-fix-1. FIX-LIVE: releases flow.'"
RETRO_ROW = "ccn log append d30e365 --text 'what failed: froze releases; what stops it: hooks/self_improve.py, cc-skills#1690'"
LOOP_DECIDE = "cci post --drive d1 --lane root --kind decide --text 'cc-skills#1658 fixes the dev-health alarm loop'"
ARMED = "gh pr merge 1658 --repo yasyf/cc-skills --auto --squash"
INSTALL_ORDER = "cci post --drive d1 --lane root --kind go --to mq-health-9 --text 'install long-running once #1658 merges: claude plugin update long-running'"


def turn(prompt: str, *commands: str) -> list:
    return [T.user(prompt), *(T.assistant(T.tool("Bash", command=command)) for command in commands), T.assistant("Done.")]


def option(argv: tuple[str, ...], flag: str) -> str:
    return next((argv[i + 1] for i, word in enumerate(argv[:-1]) if word == flag), "")


def turn_argvs(evt: BaseHookEvent) -> Iterator[tuple[str, ...]]:
    for use in evt.ctx.turn.tool_calls.named("Bash").items:
        for command in use.call.command_line:
            if command.argv:
                yield (Path(command.argv[0]).name, *command.argv[1:])


def root_posts(evt: BaseHookEvent) -> list[tuple[str, str]]:
    return [(option(argv, "--kind"), option(argv, "--text")) for argv in turn_argvs(evt) if argv[:2] == ("cci", "post") and option(argv, "--lane") in ("", "root", "main")]


def corrected(evt: BaseHookEvent, posts: list[tuple[str, str]]) -> bool:
    prompts = [text for event in evt.ctx.turn.events if (text := owner_text(event)) and not text.lstrip().startswith(SYSTEM_PREFIXES)]
    return (
        any(CORRECTION.search(text) for text in prompts)
        or any(kind in TRIGGER_KINDS for kind, _ in posts)
        or any(kind == "stand-down" and DEFECT_WORDS.search(text) for kind, text in posts)
    )


def improved(text: str) -> bool:
    change = CHANGE.search(text)
    return bool(MECHANISM.search(text) and FIX_LIVE.search(text) and change and MACHINERY.search(change["change"]) and LANDING.search(change["change"]))


def retro_row(evt: BaseHookEvent) -> bool:
    appended = any(use.call.name.endswith(LOG_APPEND) for use in evt.ctx.turn.tool_calls.items)
    return appended or any(("log", "append") == argv[i : i + 2] or (word == "retro.py" and "add" in argv) for argv in turn_argvs(evt) for i, word in enumerate(argv))


def unlanded_loop_fixes(evt: BaseHookEvent, posts: list[tuple[str, str]]) -> list[str]:
    named = {found["pr"] for _, text in posts if LOOP.search(text) for found in LOOP_FIX.finditer(text)}
    armed = {argv[3] for argv in turn_argvs(evt) if argv[:3] == ("gh", "pr", "merge") and len(argv) > 3 and "--auto" in argv}
    ordered = any(INSTALL.search(text) for _, text in posts)
    return sorted(pr for pr in named if pr not in armed or not ordered)


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    tests={
        Input(transcript=turn("why did you freeze releases, stop doing that"), state=ACTIVE): Block(pattern=r"`Mechanism:`"),
        Input(transcript=turn("why did you freeze releases, stop doing that", GOOD_DEFECT, RETRO_ROW), state=ACTIVE): Allow(),
        Input(transcript=turn("why did you freeze releases, stop doing that", GOOD_DEFECT), state=ACTIVE): Block(pattern=r"retro row"),
        Input(transcript=turn("why did you freeze releases, stop doing that", LANE_ONLY, RETRO_ROW), state=ACTIVE): Block(pattern=r"`Change:`"),
        Input(transcript=turn("status?"), state=ACTIVE): Allow(),
        Input(transcript=turn("ok", "cci post --drive d1 --lane root --kind defect --text 'the runner misfired again'"), state=ACTIVE): Block(pattern=r"`FIX-LIVE:`"),
        Input(transcript=turn("ok", LOOP_DECIDE), state=ACTIVE): Block(pattern=r"--auto --squash"),
        Input(transcript=turn("ok", LOOP_DECIDE, ARMED, INSTALL_ORDER), state=ACTIVE): Allow(),
        Input(transcript=turn("why did you freeze releases, stop doing that")): Allow(),
        Input(transcript=turn("why did you freeze releases, stop doing that"), agent_id="a1b2c3", state=ACTIVE): Allow(),
    },
)
def self_improve(evt: BaseHookEvent) -> HookResult | None:
    """The root's turn that held an owner correction or repeat, a stand-down for a defect, or a root defect or blocker record stops only once it posted a root `defect` record naming the mechanism, the machinery change with its PR or install order, and the FIX-LIVE check, and wrote the retro row; a cc-skills or cc-inbox PR named as a live loop's fix also needs auto-merge armed and an install order (owner, 10:29 PM Nov 1)."""
    if not CompactionState.load(evt).active:
        return None
    posts = root_posts(evt)
    if corrected(evt, posts) and not (any(kind == "defect" and improved(text) for kind, text in posts) and retro_row(evt)):
        return evt.block(UNIMPROVED)
    return evt.block(UNLANDED) if unlanded_loop_fixes(evt, posts) else None
