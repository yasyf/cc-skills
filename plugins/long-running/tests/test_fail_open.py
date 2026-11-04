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
from captain_hook import Allow, Block, Input
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
LABEL_CHARS = 42
COMMAND, FILE = "command", "file"
EXITED, TIMED_OUT, NOT_RUN, DENIED = "exited 1", "timed out", "did not start", "denied"
FAILURES = {COMMAND: (EXITED, TIMED_OUT, NOT_RUN), FILE: (DENIED,)}
KEPT = {
    "owner_login_verdicts.owner_login_verdicts_cite_the_board": "answer 8c73884: an approval under his login stands only on his verdict, and an unread verdict is no verdict",
    "gate5_release.sand_next_release_needs_gate5_pass": "cci 369335: publishing next past a failed gate read has no way back across a server migration, while a release that does not name sand-next needs no read",
}
PARKS = "AskUserQuestion parks the session, so a warning lands too late (answer 1fe20ee)"
STOP = "a warning on Stop reaches nobody, and {what}"
KEPT_REFUSALS = KEPT | {
    "blocking_asks.drive_roots_never_park_on_asks": PARKS,
    "chat_formats.reply_formats_are_0_or_1": "the owner ordered the refusal: he reads a sessions migration number as a chat format",
    "chat_formats.chat_formats_are_0_or_1": STOP.format(what="the owner would read a sessions migration number as a chat format"),
    "thread_owner_relay.thread_replies_reach_their_card_owner": "the owner ordered the refusal: a warned forward still reaches the wrong drive's lane, which acts on his reply to another session's card (cci 399129)",
    "communicator_chat.communicator_chat:llm_gate_communicator_replies_carry_owner_news": STOP.format(what="the owner would read internal bookkeeping as his update"),
    "communicator_chat.A do-nothing Bash call ends no turn, and t": "the call has already shown in the owner's window when a warning lands",
    "desk_ownership.The coordinator owns the relay and every d": "trust: a runner or desk started from the communicator",
    "no_credential_on_a_shell.no_credential_on_a_shell": "the owner ordered the refusal: a credential in a transcript cannot be taken back",
    "incident_accepts_findings.incident_accepts_findings": "a push restarts the incident PR's CI the moment it lands, so a warning comes too late (cci 382428)",
    "incident_commands.release_undo_cites_its_cause": "trust: a rollback without the owner's word",
    "landing_approval.Only the owner changes who approves landin": "trust: a change to who approves landings",
    "lane_rotation.stand_downs_go_through_the_registry": "trust: session protection",
    "lane_slack_status.lanes_post_no_release_status": "a Slack post cannot be unsent, and the Slack permission rule stays (answer 8f1682a)",
    "lane_landing.hand_landings_meet_the_runner_checks": "card-hand-merge-rule (answer fd9233e): the owner picked a block for a hand landing with no tap at its head, since a merge cannot be recalled",
    "lane_spawns.lanes_never_spawn": "the lane waits inside the Agent call, so a warning lands too late",
    "local_builds.no_local_builds_in_a_drive": "the run already saturates the Mac when a warning lands (answer 7e82747)",
    "mechanism_lines_cite.mechanism_lines_cite": "the owner ordered the refusal: he reads an uncited mechanism sentence as fact",
    "mechanism_lines_cite.mechanism_replies_cite": STOP.format(what="the owner would read an uncited mechanism as fact"),
    "merge_own_red.merge_never_lands_past_its_own_red": "a merge cannot be recalled",
    "owner_answers.owner_notes_get_answers": STOP.format(what="the owner's note would wait"),
    "owner_ledger.owner_messages_close_before_stop": STOP.format(what="the owner's message would stay open"),
    "owner_ledger.The communicator never calls `AskUserQuest": PARKS,
    "owner_ledger.The `owner_ledger` hook records every owne": "the write to the rulings file lands before a warning does",
    "owner_quotes.pr_text_paraphrases_the_owner": "a quote in a public repo is published the moment it posts",
    "owner_lines_skip_refused_topics.owner_lines_skip_refused_topics": "the owner ordered the refusal: this class never reaches him, and a warning lands after it has",
    "owner_lines_skip_refused_topics.owner_replies_skip_refused_topics": STOP.format(what="the owner would read a class he ruled is never reported"),
    "owner_replies_carry_news.owner_replies_carry_news": "his notification fires the moment the reply posts, so a warning comes too late",
    "owner_replies_stay_short.owner_writes_stay_short": "the owner ordered the refusal: his notification fires the moment a chatty reply posts, so a warning comes too late",
    "owner_replies_stay_short.owner_replies_stay_short": STOP.format(what="the owner would read the chatty reply as sent"),
    "owner_returns.communicator_reply_follows_the_owner_rules": STOP.format(what="the owner would read the reply as sent"),
    "owner_returns.The communicator reads only the Slack thre": "the owner corrected the communicator's warned wide searches twice, so a warning did not stop them",
    "owner_slack_links.owner_slack_links_get_routed": STOP.format(what="a thread the owner linked would go unanswered"),
    "register_gates.hook_edit_guard": "trust: hook-file edits",
    "release_cards.release_leaves_out_what_his_open_cards_gate": "an apply cannot be recalled and sandsql cannot roll back, so a warning on the start lands after the stack ships (cci 386973)",
    "root_context.The communicator runs no search inline: as": "the owner corrected the communicator's warned wide searches twice, so a warning did not stop them",
    "root_replies.tap_claims_match_the_board": STOP.format(what="the owner would read a false claim about what waits on him"),
    "shell_script_file.BLOCKED: Claude Code reads `$$(`, `$${` or": "the call would hold on a permission prompt, so a warning lands too late",
    "not_ours_holds.not_ours_holds": "the owner ordered the refusal: a hold or card on another person's PR reaches lanes and his board the moment it posts",
    "urgent_lines_name_the_harm.urgent_lines_name_the_harm": "the owner ordered the refusal: he acts on an urgent line the moment it reaches him",
    "urgent_lines_name_the_harm.urgent_replies_name_the_harm": STOP.format(what="the owner would act on an urgent line that names no harm"),
    "vet_before_push.lanes_vet_before_they_push": "the owner ordered that every lane runs go build, go vet and the typecheck over what it touched before it pushes, held by a hook rather than goodwill, and a warning lands after the push has already turned CI red",
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
    return f"{Path(str(entry.source_file)).stem}.{entry.handler.__name__ if entry.handler else (entry.spec.message or entry.name)[:LABEL_CHARS]}"


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


def test_the_kept_lists_name_real_hooks():
    assert set(KEPT_REFUSALS) <= {label(entry) for entry in _state.hooks}


def test_every_refusal_names_why_a_warning_would_not_do():
    refusing = {
        label(entry)
        for entry in _state.hooks
        if Path(str(entry.source_file)).parent == HOOKS and any(isinstance(expected, Block) for expected in {**(entry.spec.tests or {}), **MIGRATED.get(label(entry), {})}.values())
    }
    assert refusing <= set(KEPT_REFUSALS)


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
