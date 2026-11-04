from __future__ import annotations

import hashlib
import io
import json
import platform
import re
import tarfile
import tempfile
import urllib.request
from collections.abc import Iterator
from functools import cache
from pathlib import Path

import pytest

from test_lr_verbs import END, FLAG, PACK, SPAN, TEXT, commands, missing

COMMAND = re.compile(r"""(?:^\s*|[|;&(]\s*|\bexec\s+|/)cci\b((?:[ \t]+[a-z][a-z-]*)+)(?=[^`\n]*?\s--[a-z])""")
ARGV = re.compile(r'(?:\[\]string\{|\b(?:[Rr]un|capture|Command(?:Context)?)\((?:[^()"]*,\s*)?)"cci"\s*,(.*)')
LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
VERB = re.compile(r"[a-z][a-z-]*")
RELEASE = "https://github.com/yasyf/cc-inbox/releases/download/v{}/"


def floor() -> str:
    dependencies = json.loads((PACK / ".claude-plugin" / "plugin.json").read_text())["dependencies"]
    return next(dep["version"].removeprefix(">=") for dep in dependencies if dep["name"] == "cc-inbox")


@cache
def cci() -> str:
    version = floor()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}[platform.machine().lower()]
    archive = f"cci_{version}_{platform.system().lower()}_{arch}.tar.gz"
    with urllib.request.urlopen(RELEASE.format(version) + "checksums.txt", timeout=60) as response:
        sums = {name: digest for digest, name in (line.split() for line in response.read().decode().splitlines())}
    with urllib.request.urlopen(RELEASE.format(version) + archive, timeout=120) as response:
        data = response.read()
    assert hashlib.sha256(data).hexdigest() == sums[archive], f"{archive} does not match cc-inbox v{version}'s checksums.txt"
    out = Path(tempfile.mkdtemp())
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(out, filter="data")
    return str(out / "cci")


def argv(text: str) -> Iterator[tuple[str, ...]]:
    for match in ARGV.finditer(text):
        literals = LITERAL.findall(match[1])
        first = next((i for i, word in enumerate(literals) if word.startswith("-")), len(literals))
        yield (*(word for word in literals[:first] if VERB.fullmatch(word)), *(flag for word in literals[first:] for flag in FLAG.findall(word)))


def go(text: str) -> Iterator[tuple[str, ...]]:
    yield from argv(text)
    for segment in SPAN.findall(text):
        for match in COMMAND.finditer(segment):
            yield (*match[1].split(), *FLAG.findall(END.split(segment[match.end() :], maxsplit=1)[0]))


def extension(words: tuple[str, ...]) -> bool:
    return bool(words) and (PACK / "bin" / f"cci-{words[0]}").exists()


def named() -> Iterator[tuple[Path, tuple[str, ...]]]:
    for path in sorted((PACK / "lr").rglob("*.go")):
        if not path.name.endswith("_test.go") and "testdata" not in path.parts:
            for words in go(path.read_text()):
                yield path, words
    for root in TEXT:
        for path in sorted((PACK / root).rglob("*")):
            if path.suffix in {".md", ".py", ".sh"} and not path.is_symlink() and not {"tests", "fixtures"} & set(path.parts):
                for words in commands(path.suffix, path.read_text(), COMMAND):
                    yield path, words


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ('argv := []string{"cci", "tail", "--drive", s.drive, "--cursor", s.lane + "-wake", "--seed", s.lane, "--to", s.lane}', [("tail", "--drive", "--cursor", "--seed", "--to")]),
        ('status, errs, code := w.capture("cci", "relay", "status", "--json")', [("relay", "status", "--json")]),
        ('d.run(ctx, "cci", "tail", "--kind=state", "--topic="+runner, "--to", "x")', [("tail", "--kind", "--topic", "--to")]),
        ('e.Run(ctx, "cci", "post", "--drive", d, "--kind", "handoff", "--text", "a review")', [("post", "--drive", "--kind", "--text")]),
        ('cmd := exec.CommandContext(ctx, "cci", argv...)', [()]),
        ("joinRelay = `~/.local/bin/cci relay join --instance --drive \"$1\" && sprite-env services create cci --cmd x`", [("relay", "join", "--instance", "--drive")]),
        ('return errors.New("cci relay status failed")', []),
        ('return filepath.Join(d.Config.Receipts, "cci", "messages", id+".md")', []),
    ],
)
def test_lr_names_cci_where_it_runs_it(text: str, words: list[tuple[str, ...]]) -> None:
    assert list(go(text)) == words


def test_the_check_reads_lr_and_the_pack_text() -> None:
    read = {(path.relative_to(PACK).as_posix(), words) for path, words in named()}

    assert ("lr/internal/wait/wait.go", ("tail", "--drive", "--cursor", "--seed", "--to", "--mail")) in read
    assert ("lr/internal/orca/worker.go", ("relay", "join", "--instance", "--drive", "--url", "--ttl", "--owner")) in read


def test_every_cci_verb_and_flag_the_pack_names_is_in_the_cci_it_requires() -> None:
    lacked = sorted({f"{path.relative_to(PACK)}: cci {verb}" for path, words in named() if not extension(words) and (verb := missing(words, cci()))})

    assert not lacked, f"plugin.json requires cc-inbox >={floor()}, whose cci lacks these; raise the floor to a release that has them:\n" + "\n".join(lacked)
