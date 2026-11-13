import argparse
import functools
import io
import json
import re
import shlex
import sys
from collections.abc import Callable, Iterator
from itertools import takewhile
from pathlib import Path

import pytest
from conftest import LANE_SPEC
from drive import agents, cli, orca

SKILL = cli.PAGES.parent
PLUGIN = SKILL.parents[1]
PAGES = sorted([SKILL / "SKILL.md", *cli.PAGES.glob("*.md"), *(PLUGIN / "agents").glob("*.md")])
HOLE = "placeholder"
START_META = orca.start_meta
FENCE = re.compile(r" {0,3}(`{3,})[^`]*")
TICKS = re.compile(r"`+")
PLACEHOLDER = re.compile(r"<[^<>]*>")
SUBSTITUTION = re.compile(r"\$\(([^()]*)\)")
SHELL = re.compile(r"[a-z][\w.-]*(?: |$)")
DRIVE = re.compile(r"(?:^|[|&;(]\s*)drive\s")
NOTATION = re.compile(r"^\[+|(?:\.\.\.|…|\])+$")
SEPARATORS = {"|", "||", "&", "&&", ";"}
REDIRECTS = {"<", "<<", "<<<", ">", ">>", ">&", "&>"}
OPTIONAL = {None: "?", "+": "*"}


def blocks(text: str) -> Iterator[tuple[bool, str]]:
    fence, paragraph = None, []
    for line in [*text.splitlines(), ""]:
        if fence:
            if re.fullmatch(rf" {{0,3}}{fence}`*\s*", line):
                fence = None
            else:
                yield True, line.strip()
        elif opener := FENCE.fullmatch(line):
            fence = opener.group(1)
            yield False, " ".join(paragraph)
            paragraph = []
        elif line.strip():
            paragraph.append(line.strip())
        else:
            yield False, " ".join(paragraph)
            paragraph = []


def spans(paragraph: str) -> Iterator[str]:
    at = 0
    while opener := TICKS.search(paragraph, at):
        closer = re.compile(rf"(?<!`){opener.group()}(?!`)").search(paragraph, opener.end())
        at = closer.end() if closer else opener.end()
        if closer:
            yield paragraph[opener.end() : closer.start()].strip()


def commands(line: str) -> Iterator[list[str]]:
    while PLACEHOLDER.search(line):
        line = PLACEHOLDER.sub(HOLE, line)
    while inner := SUBSTITUTION.search(line):
        yield from commands(inner.group(1))
        line = f"{line[: inner.start()]}{HOLE}{line[inner.end() :]}"
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    segments: list[list[str]] = [[]]
    words = iter(lexer)
    for word in words:
        if word in REDIRECTS:
            next(words)
        elif word in SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(word)
    for segment in segments:
        argv = [bare for word in segment if (bare := NOTATION.sub("", word))]
        if argv[:1] == ["drive"] and len(argv) > 1:
            yield argv[1:]


def quoted(page: Path) -> Iterator[tuple[str, list[str]]]:
    for fenced, block in blocks(page.read_text()):
        for line in [block] if fenced else spans(block):
            if SHELL.match(line) and DRIVE.search(line):
                yield from ((line, words) for words in commands(line))


def holed(kind: Callable[[str], object]) -> Callable[[str], object]:
    @functools.wraps(kind)
    def parse(value: str) -> object:
        return value if value == HOLE else kind(value)

    return parse


def lenient(parser: argparse.ArgumentParser, whole: bool) -> None:
    parser.allow_abbrev = False
    for group in parser._mutually_exclusive_groups:
        group.required = group.required and whole
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in set(action.choices.values()):
                lenient(child, whole)
            continue
        action.required = action.required and whole
        action.nargs = OPTIONAL.get(action.nargs, action.nargs)
        if action.choices is not None:
            action.choices = [*action.choices, HOLE]
        if action.type is not None:
            action.type = holed(action.type)


def verb(parser: argparse.ArgumentParser, words: list[str]) -> tuple[argparse.ArgumentParser, list[str]]:
    for at, word in enumerate(words):
        verbs = next((action.choices for action in parser._actions if isinstance(action, argparse._SubParsersAction)), {})
        if word not in verbs:
            return parser, words[at:]
        parser = verbs[word]
    return parser, []


def whole(parser: argparse.ArgumentParser, words: list[str]) -> bool:
    leaf, rest = verb(parser, words)
    given = len(list(takewhile(lambda word: not word.startswith("--"), rest)))
    return given < len(rest) and given >= sum(action.required for action in leaf._actions if not action.option_strings)


def every_quote() -> dict[tuple[str, str, tuple[str, ...]], list[str]]:
    return {(page.relative_to(PLUGIN).as_posix(), line, tuple(words)): words for page in PAGES for line, words in quoted(page)}


def test_the_pages_quote_drive_commands() -> None:
    assert len(every_quote()) > 150


def session_starts(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip().startswith("orca terminal create") and " claude " in line]


def test_each_session_start_the_pages_quote_leaves_its_env_and_one_settings_to_drive_manual() -> None:
    starts = [line for page in PAGES for line in session_starts(page.read_text())]
    assert len(starts) == 5
    holed = f'--command "{agents.SESSION_ENV} claude '
    assert [line for line in starts if holed not in line or line.count("--settings") != 1 or f"--settings {agents.SESSION_SETTINGS_HOLE} " not in line] == []


@pytest.mark.parametrize("page", ["start", "migration"])
def test_drive_manual_fills_each_session_start_with_every_lane_variable_and_one_valid_settings(page: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.manual(argparse.Namespace(page=page, row=None)) == 0
    starts = session_starts(capsys.readouterr().out)
    assert starts
    lane = json.loads(LANE_SPEC.read_text())
    for line in starts:
        script = shlex.split(line)
        session = shlex.split(script[script.index("--command") + 1])
        claude = session.index("claude")
        assert session[0] == "env"
        assert dict(word.split("=", 1) for word in session[1:claude]) == lane["env"] | agents.SESSION_SETTINGS["env"]
        assert session.count("--settings") == 1
        settings = json.loads(session[session.index("--settings") + 1])
        assert settings == agents.session_settings({})
        assert (settings["env"]["DISABLE_AUTOUPDATER"], settings["spinnerTipsEnabled"], settings["bashOutputMaxChars"]) == ("1", False, 6000)
        assert settings["permissions"]["deny"] == lane["deniedTools"]
        assert "-p" not in session and "--print" not in session


@pytest.mark.parametrize("words", [pytest.param(words, id=f"{page}: {line}") for (page, line, _), words in every_quote().items()])
def test_every_quoted_drive_command_parses(words: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    parser = cli.parser()
    lenient(parser, whole(parser, words))
    try:
        parser.parse_args(words if verb(parser, words)[1] else [*words, "--help"])
    except SystemExit as refusal:
        assert refusal.code == 0


def test_the_meta_session_drive_start_opens_runs_the_command_start_or_resume_prints(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    created: list[tuple[str, ...]] = []
    monkeypatch.setattr(orca, "request", lambda *args: created.append(args) or {"terminal": {"handle": "term_meta"}})
    assert START_META("demo", "/w/demo", None) == "term_meta"
    [request] = created
    assert request[:6] == ("terminal", "create", "--worktree", "path:/w/demo", "--title", "demo-meta")
    assert cli.manual(argparse.Namespace(page="start", row=None)) == 0
    [quoted] = [line for line in session_starts(capsys.readouterr().out) if "--title <drive>-meta " in line]
    script = shlex.split(quoted.replace("<drive>", "demo"))
    assert shlex.split(request[request.index("--command") + 1]) == shlex.split(script[script.index("--command") + 1])
