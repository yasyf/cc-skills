"""Every drive hook fails open: replay each of its inline tests with each read it makes failing in turn, and a call that passed never becomes a block."""

from __future__ import annotations

import builtins
import importlib
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import FrameType
from unittest import mock

import pytest
from captain_hook import Allow, Input
from captain_hook.app import _state
from captain_hook.testing import helpers
from spawnllm import proc

from hooks.tests.rulings_consumers import MIGRATED
from test_rulings_consumers import stage

PLUGIN = Path(__file__).parents[1]
HOOKS = PLUGIN / "capt-hook" / "hooks"
FIXTURES = HOOKS / "tests"
SOURCES = (HOOKS, PLUGIN / "skills")
MODULES = tuple(importlib.import_module(f"hooks.{path.stem}") for path in sorted(HOOKS.glob("*.py")))
UNREAD = "read failed"
COMMAND, FILE = "command", "file"
EXITED, TIMED_OUT, NOT_RUN, DENIED = "exited 1", "timed out", "did not start", "denied"
FAILURES = {COMMAND: (EXITED, TIMED_OUT, NOT_RUN), FILE: (DENIED,)}
KEPT = {
    "owner_login_verdicts.owner_login_verdicts_cite_the_board": "answer 8c73884: an approval under his login stands only on his verdict, and an unread verdict is no verdict",
    "gate5_release.sand_next_release_needs_gate5_pass": "cci 369335: publishing next past a failed gate read has no way back across a server migration, while --without sand-next costs one flag and needs no read",
}
REAL_STUBS = helpers.stubbed_commands


@dataclass
class Reads:
    failing: int | None = None
    failure: str = ""
    seen: list[str] = field(default_factory=list)

    def fails(self, kind: str) -> bool:
        self.seen.append(kind)
        return len(self.seen) - 1 == self.failing

    def by_pack(self, caller: FrameType) -> bool:
        path = Path(caller.f_code.co_filename)
        return any(path.is_relative_to(source) for source in SOURCES) and not path.is_relative_to(FIXTURES)

    @contextmanager
    def intercepted(self, commands: dict | None) -> Iterator[None]:
        with REAL_STUBS(commands):
            with mock.patch.object(subprocess, "run", self.run(subprocess.run)), mock.patch.object(proc, "run_cli", self.run_cli(proc.run_cli)):
                with self.files():
                    yield

    def run(self, stubbed):
        def run(args, *pargs, **kwargs):
            if not self.fails(COMMAND):
                return stubbed(args, *pargs, **kwargs)
            if self.failure == TIMED_OUT:
                raise subprocess.TimeoutExpired(args, 1)
            if self.failure == NOT_RUN:
                raise FileNotFoundError(UNREAD)
            if kwargs.get("check"):
                raise subprocess.CalledProcessError(1, args, "", UNREAD)
            text = any(kwargs.get(key) for key in ("text", "universal_newlines", "encoding", "errors"))
            return subprocess.CompletedProcess(args, 1, stdout="" if text else b"", stderr=UNREAD if text else UNREAD.encode())

        return run

    def run_cli(self, stubbed):
        def run_cli(args, **kwargs):
            return stubbed(args, **(kwargs | {"checkpoint": None}))

        return run_cli

    @contextmanager
    def files(self) -> Iterator[None]:
        read_text, read_bytes, path_open, opened = Path.read_text, Path.read_bytes, Path.open, builtins.open

        def guarded(real):
            def read(*args, **kwargs):
                if self.by_pack(sys._getframe(1)) and self.fails(FILE):
                    raise PermissionError(UNREAD)
                return real(*args, **kwargs)

            return read

        with (
            mock.patch.object(Path, "read_text", guarded(read_text)),
            mock.patch.object(Path, "read_bytes", guarded(read_bytes)),
            mock.patch.object(Path, "open", guarded(path_open)),
            mock.patch.object(builtins, "open", guarded(opened)),
        ):
            yield


def label(entry) -> str:
    return f"{Path(str(entry.source_file)).stem}.{entry.handler.__name__ if entry.handler else entry.name}"


def fire(entry, inp: Input, reads: Reads, patch: pytest.MonkeyPatch):
    results = []
    patch.setattr(_state, "hooks", [replace(entry, spec=replace(entry.spec, tests={inp: Allow()}))])
    patch.setattr(helpers, "assert_result", lambda result, *_: results.append(result))
    patch.setattr(helpers, "stubbed_commands", reads.intercepted)
    helpers.run_inline_tests()
    return results[0] if results else None


def blocked(result) -> bool:
    return result is not None and result.action == "block"


def hooks() -> list:
    return [entry for entry in _state.hooks if Path(str(entry.source_file)).parent == HOOKS and label(entry) not in KEPT and inputs(entry)]


def inputs(entry) -> list[Input]:
    return [inp for inp in (*(entry.spec.tests or ()), *MIGRATED.get(label(entry), ())) if isinstance(inp, Input)]


def test_the_kept_list_names_real_hooks():
    assert set(KEPT) <= {label(entry) for entry in _state.hooks}


@pytest.mark.parametrize("entry", hooks(), ids=label)
def test_a_failed_read_never_turns_a_pass_into_a_block(entry, tmp_path_factory: pytest.TempPathFactory):
    offenders = []
    for inp in inputs(entry):
        with pytest.MonkeyPatch.context() as patch:
            staged = stage(entry, inp, patch, tmp_path_factory.mktemp("stage")) if inp in MIGRATED.get(label(entry), ()) else inp
            baseline = Reads()
            if blocked(fire(entry, staged, baseline, patch)):
                continue
            offenders += [
                f"{label(entry)}: read {index} ({kind}) {failure} blocks: {result.message!r:.200} on {inp!r:.200}"
                for index, kind in enumerate(baseline.seen)
                for failure in FAILURES[kind]
                if blocked(result := fire(entry, staged, Reads(index, failure), patch))
            ]
    assert not offenders, "\n".join(dict.fromkeys(offenders))
