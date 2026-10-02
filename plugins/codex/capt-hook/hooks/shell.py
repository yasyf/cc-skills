from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import dropwhile
from pathlib import Path

from captain_hook import BaseHookEvent, CustomCondition, ast_grep
from captain_hook.cmd import Call, Cmd
from captain_hook.types import TOOL_EVENTS

__capt_hook_skip__ = True

SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ash", "fish", "csh", "tcsh"})
INTERPRETER = re.compile(r"python[0-9.]*|uv|uvx")
WRAPPER_SUBCOMMANDS = {"retro.py": "prose", "design.py": "plainify"}


def expanded_calls(cmd: Cmd) -> Iterator[tuple[Call, bool]]:
    for call in cmd.calls():
        if call.name != "setsid":
            yield call, "nohup" in call.wrappers
            continue
        launched = dropwhile(lambda word: (word.value or "").startswith("-"), call.command.words[1:])
        if (inner := Cmd.parse(" ".join(word.raw for word in launched))) is not None:
            for nested, _ in expanded_calls(inner):
                yield nested, True


def head_name(call: Call) -> str:
    words = call.command.words
    return Path(words[0].value or words[0].raw).name.casefold() if words else ""


def invocations(cmd: Cmd, program: str) -> tuple[Call, ...]:
    return tuple(call for call, _ in expanded_calls(cmd) if head_name(call) == program)


def runs_prose_wrapper(call: Call) -> bool:
    words = call.command.words
    for word, following in zip(words, words[1:]):
        script = word.value or word.raw
        if following.value in WRAPPER_SUBCOMMANDS.values() and (
            WRAPPER_SUBCOMMANDS.get(Path(script).name) == following.value
            or (word.value is None and script.startswith("$"))
        ):
            return word is words[0] or INTERPRETER.fullmatch(head_name(call)) is not None
    return False


def prose_wrappers(cmd: Cmd) -> tuple[Call, ...]:
    return tuple(call for call, _ in expanded_calls(cmd) if runs_prose_wrapper(call))


def shell_payloads(cmd: Cmd) -> Iterator[str]:
    for call, _ in expanded_calls(cmd):
        if call.name == "eval" and call.args:
            yield " ".join(call.args)
        elif call.name in SHELLS:
            for index, arg in enumerate(call.args[:-1]):
                if arg.startswith("-") and not arg.startswith("--") and arg.endswith("c"):
                    yield call.args[index + 1]
                    break


def backgrounded_by_ampersand(cmd: Cmd, selects: Callable[[Call], bool]) -> bool:
    for source in (cmd.raw, *shell_payloads(cmd)):
        for node in ast_grep.parse(source, "bash").descendants():
            if node.kind != "&" or (before := node.raw.prev()) is None:
                continue
            if (inner := Cmd.parse(before.text())) is not None and any(selects(call) for call, _ in expanded_calls(inner)):
                return True
    return False


@dataclass(frozen=True)
class Invokes(CustomCondition):
    program: str
    valid_events = TOOL_EVENTS

    def check(self, evt: BaseHookEvent) -> bool:
        return bool(invocations(evt.command, self.program))


class InvokesProseWrapper(CustomCondition):
    valid_events = TOOL_EVENTS

    def check(self, evt: BaseHookEvent) -> bool:
        return bool(prose_wrappers(evt.command))


@dataclass(frozen=True)
class Detached(CustomCondition):
    selects: Callable[[Call], bool]
    valid_events = TOOL_EVENTS

    def check(self, evt: BaseHookEvent) -> bool:
        return any(
            detached and self.selects(call) for call, detached in expanded_calls(evt.command)
        ) or backgrounded_by_ampersand(evt.command, self.selects)


@dataclass(frozen=True)
class LaneSelected(CustomCondition):
    valid_events = TOOL_EVENTS

    def check(self, evt: BaseHookEvent) -> bool:
        return any("--lane" in call.args for call in invocations(evt.command, "codex-ask"))
