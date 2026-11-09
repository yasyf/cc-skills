from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import Iterator
from functools import cache
from pathlib import Path

import pytest

PACK = Path(__file__).resolve().parents[1]
LR = PACK / "bin" / "lr"
TEXT = ("agents", "capt-hook", "scripts", "skills")
SPAN = re.compile(r"`([^`\n]+)`")
FENCE = re.compile(r"^```.*?\n(.*?)^```", re.MULTILINE | re.DOTALL)
COMMAND = re.compile(r"""(?:^\s*|[|;&(]\s*|\bexec\s+|/)\{?lr\b["'}]?((?:[ \t]+[a-z][a-z-]*)+)""")
END = re.compile(r"`|\n|;|&&|\| | -- ")
FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")
SECTION = re.compile(r"^(\w[\w ]*):\n((?:  .*\n)+)", re.MULTILINE)
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def segments(suffix: str, text: str, pattern: re.Pattern[str] = COMMAND) -> Iterator[str]:
    if suffix == ".md":
        for block in FENCE.findall(text):
            yield from block.splitlines()
        yield from SPAN.findall(FENCE.sub("", text))
    elif suffix == ".py":
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if pattern.match(node.value):
                    yield node.value
                yield from SPAN.findall(node.value)
    else:
        yield from (line for line in text.splitlines() if not line.lstrip().startswith("#"))


def commands(suffix: str, text: str, pattern: re.Pattern[str] = COMMAND) -> Iterator[tuple[str, ...]]:
    for segment in segments(suffix, text, pattern):
        for match in pattern.finditer(segment):
            yield (*match[1].split(), *FLAG.findall(QUOTED.sub("", END.split(segment[match.end() :], maxsplit=1)[0])))


def named() -> Iterator[tuple[Path, tuple[str, ...]]]:
    for root in TEXT:
        for path in sorted((PACK / root).rglob("*")):
            if path.suffix in {".md", ".py", ".sh"} and not path.is_symlink() and not {"tests", "fixtures"} & set(path.parts):
                for words in commands(path.suffix, path.read_text()):
                    yield path, words


@cache
def command(binary: str, *path: str) -> tuple[frozenset[str], bool, frozenset[str]]:
    shown = subprocess.run([binary, *path, "--help"], capture_output=True, text=True, timeout=120, check=True).stdout
    sections = dict(SECTION.findall(shown))
    subcommands = frozenset(line.split()[0] for line in sections.get("Available Commands", "").splitlines())
    runnable = any(not line.endswith("[command]") for line in sections["Usage"].splitlines())
    flags = frozenset(FLAG.findall(sections.get("Flags", "") + sections.get("Global Flags", "")))
    return subcommands, runnable, flags


@cache
def unlisted(binary: str, *path: str) -> bool:
    shown = subprocess.run([binary, *path, "--help"], capture_output=True, text=True, timeout=120)
    sections = dict(SECTION.findall(shown.stdout))
    usage = f"{Path(binary).name} {' '.join(path)}" in sections.get("Usage", "")
    return shown.returncode == 0 and (usage or path[-1] in sections.get("Aliases", "").replace(",", " ").split())


@cache
def takes(binary: str, path: tuple[str, ...], flag: str) -> bool:
    shown = subprocess.run([binary, *path, "--help", flag], capture_output=True, text=True, timeout=120)
    return "unknown flag" not in shown.stdout + shown.stderr


def missing(words: tuple[str, ...], binary: str = str(LR)) -> str | None:
    path: tuple[str, ...] = ()
    for word in (word for word in words if not word.startswith("--")):
        subcommands, runnable, _ = command(binary, *path)
        if word not in subcommands and not unlisted(binary, *path, word):
            if path and runnable:
                break
            return " ".join((*path, word))
        path += (word,)
    unknown = [word for word in words if word.startswith("--") and word not in command(binary, *path)[2] and not takes(binary, path, word)]
    return " ".join((*path, *unknown)) if unknown else None


@pytest.mark.parametrize(
    ("suffix", "text", "words"),
    [
        (".md", "run `lr wait 60 mailbox:x -- gh pr checks --watch` once", [("wait",)]),
        (".md", "```sh\nlr drive sub start <name> --objective y\n```\n", [("drive", "sub", "start", "--objective")]),
        (".md", "the lr verb for a chore, or `for meta: lr verb for <chore>`", []),
        (".py", 'MARK = "`{lr} owner mark {row} responded|addressed|due [--routed <seq>] [--reply <id>] [--by <clock>] --drive {drive}`"', [("owner", "mark", "--routed", "--reply", "--by", "--drive")]),
        (".py", 'NEXT = "lr coordinator next --drive d"', [("coordinator", "next", "--drive")]),
        (".py", "from lr import loop", []),
        (".py", '"""Every pass runs `lr stop-sprites`; lr decides which lanes ended."""', [("stop-sprites",)]),
        (".sh", '  finder.py) exec "$ROOT/bin/lr" grade finder "$@" ;;', [("grade", "finder")]),
        (".sh", "# lr nonesuch is prose here", []),
    ],
)
def test_a_command_is_lr_where_it_would_run_never_lr_in_prose(suffix: str, text: str, words: list[tuple[str, ...]]) -> None:
    assert list(commands(suffix, text)) == words


@pytest.mark.parametrize(
    ("words", "lacked"),
    [
        (("wait",), None),
        (("owner", "mark", "--routed", "--drive"), None),
        (("launch", "coordinator"), None),
        (("lanes", "root", "meta", "--json"), None),
        (("nonesuch",), "nonesuch"),
        (("owner", "nonesuch"), "owner nonesuch"),
        (("owner", "mark", "--drive", "--nonesuch"), "owner mark --nonesuch"),
    ],
)
def test_a_verb_or_flag_is_missing_unless_the_pinned_lr_takes_it(words: tuple[str, ...], lacked: str | None) -> None:
    assert missing(words) == lacked


def test_the_check_reads_hooks_scripts_skill_text_and_brief_templates() -> None:
    read = {(path.relative_to(PACK).as_posix(), words) for path, words in named()}

    assert ("capt-hook/hooks/lane_waits.py", ("wait",)) in read
    assert ("capt-hook/hooks/owner_ledger.py", ("owner", "audit", "--drive")) in read
    assert ("scripts/bin-shim.sh", ("board", "waits")) in read
    assert ("skills/long-running/reference/sub-drives.md", ("drive", "sub", "start", "--objective", "--reason")) in read
    assert ("skills/long-running/reference/chores-lane-brief.md", ("launch", "chores")) in read


def test_every_lr_verb_and_flag_the_pack_names_is_in_the_lr_it_pins() -> None:
    lacked = sorted({f"{path.relative_to(PACK)}: lr {verb}" for path, words in named() if (verb := missing(words))})

    assert not lacked, "bin/lr.binrun pins an lr without these; land them and their pin first, then the text naming them:\n" + "\n".join(lacked)
