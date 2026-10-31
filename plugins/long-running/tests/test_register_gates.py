from __future__ import annotations

import argparse
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest
from captain_hook.events import PostToolUseEvent, PreToolUseEvent, StopEvent
from captain_hook.testing.helpers import build_context

from fire import fire
from hooks import register_gates
from rulings_fixture import rulings_command_fixture
from hooks.compaction_handoff import CompactionState
from hooks.tests.register_fixtures import LEDGER, ROWS

SESSION = "0123456789abcdef"
DRIVE = "brook-drive"
PLAN_STEM = "users-plan-stem-of-brook"
CLASSIFIED = "c4936ff0405c2d0f"
UNCLASSIFIED = "629293ae14388dc9"
PLUGIN = Path(__file__).parents[1]
REGISTER_SOURCES = [*sorted((PLUGIN / "capt-hook" / "hooks").glob("register_*.py")), PLUGIN / "skills" / "long-running" / "scripts" / "rulings.py"]


@pytest.fixture(autouse=True)
def rulings_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(register_gates, "rulings", rulings_command_fixture)


class Root:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.state_dir = home / "scratch" / "brook"
        (self.state_dir / "register").mkdir(parents=True)
        self.session_dir = home / "session"
        self.calls: list[tuple[str, ...]] = []
        self.live: set[str] = set()
        self.rows: str | tuple[int, str] = json.dumps(
            {"ledger": LEDGER, "program": "brook", "state_dir": str(self.state_dir), "rows": {}, "classified": [CLASSIFIED]}
        )
        CompactionState(active=True, slug=PLAN_STEM).save(self.event(PostToolUseEvent, tool_name="Bash"))

    def event(self, cls, **raw):
        payload = {"session_id": SESSION, "cwd": str(self.home)}
        return cls(_raw=payload | raw, ctx=build_context(session_dir=self.session_dir))

    def run(self, args, *pargs, **kwargs) -> subprocess.CompletedProcess:
        argv = tuple(str(arg) for arg in args)
        self.calls.append(argv)
        if argv[:2] == ("cci", "who"):
            outcome = "".join(json.dumps({"lane": lane, "live": True, "announced_at": f"2026-10-27T{hour:02d}:00:00Z"}) + "\n" for hour, lane in enumerate(sorted(self.live)))
        else:
            outcome = self.rows if argv[: len(ROWS.split())] == tuple(ROWS.split()) else "#1"
        code, out, err = (0, outcome, "") if isinstance(outcome, str) else (outcome[0], "", outcome[1])
        if kwargs.get("check") and code:
            raise subprocess.CalledProcessError(code, args, out, err)
        return subprocess.CompletedProcess(args, code, stdout=out, stderr=err)

    @contextmanager
    def shell(self):
        with mock.patch.object(subprocess, "run", self.run):
            yield

    def fire(self, cls, **raw) -> list[str]:
        with self.shell():
            return [result.message for result in fire(register_gates, self.event(cls, **raw))]

    def bash(self, cls, command: str) -> list[str]:
        return self.fire(cls, tool_name="Bash", tool_input={"command": command})

    def configure(self, **fields: str) -> None:
        path = self.home / "config" / "long-running" / "drives" / f"{DRIVE}.json"
        path.write_text(json.dumps(json.loads(path.read_text()) | fields))

    def blockers(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[:2] == ("cci", "post") and "blocker" in call]

    def question(self, digest: str, text: str, blocks: list[str], term: str = "") -> Path:
        path = self.state_dir / "register" / f"question-{digest}.json"
        path.write_text(json.dumps({"digest": digest, "text": text, "term": term, "sentence": "", "rows": [], "blocks": blocks}))
        return path


@pytest.fixture
def root(tmp_path, monkeypatch) -> Root:
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    drives = tmp_path / "config" / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / f"{DRIVE}.json").write_text(json.dumps({"drive": DRIVE, "sessions": [SESSION], "cci_drive": DRIVE}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    return Root(tmp_path)


def option(call: tuple[str, ...], name: str) -> str:
    return call[call.index(name) + 1]


@pytest.mark.parametrize(
    ("configured", "live", "to", "note"),
    [
        ({}, set(), "root", ""),
        ({"register_lane": "register-desk"}, {"register-desk"}, "register-desk", ""),
        ({"register_lane": "register-desk-5"}, {"register-desk-5-5", "register-desk-6", "register-desks"}, "register-desk-6", ""),
        ({"register_lane": "register-desk"}, {"register-desk", "register-desk-2"}, "register-desk", ""),
        (
            {"register_lane": "register-desk"},
            set(),
            "root",
            (
                " `cci who` lists no live register lane `register-desk` nor a numbered successor of it, so this blocker goes to the root:"
                " relaunch the register lane as its next numbered successor from its brief to classify this capture."
            ),
        ),
    ],
)
def test_an_unclassified_owner_capture_posts_one_blocker_to_a_running_register_lane_else_the_root(root, configured, live, to, note):
    root.configure(**configured)
    root.live = live
    captures = [
        {"digest": CLASSIFIED, "at": "Oct 23 4:12 AM", "session": SESSION, "source": "prompt", "text": "rename the export button"},
        {"digest": UNCLASSIFIED, "at": "Oct 23 4:20 AM", "session": SESSION, "source": "prompt", "text": "keep the nightly report"},
    ]
    (root.state_dir / "owner-prompts.jsonl").write_text("".join(json.dumps(capture) + "\n" for capture in captures))
    go = "cci post --lane root --kind go --to walker --text 'cut web'"

    assert root.bash(PreToolUseEvent, go) == []
    assert root.bash(PreToolUseEvent, go) == []

    [blocker] = root.blockers()
    assert option(blocker, "--drive") == DRIVE
    assert option(blocker, "--lane") == "root"
    assert option(blocker, "--to") == to
    assert option(blocker, "--text").startswith(f'Owner capture {UNCLASSIFIED} (Oct 23 4:20 AM) has no row in register brook: "keep the nightly report".')
    assert option(blocker, "--text").endswith(f"before the next key moment.{note}")


@pytest.mark.parametrize(
    ("live", "to", "note"),
    [
        ({"register-desk"}, "register-desk", ""),
        (
            set(),
            "root",
            (
                " `cci who` lists no live register lane `register-desk` nor a numbered successor of it, so this blocker goes to the root:"
                " relaunch the register lane as its next numbered successor from its brief to classify these captures."
            ),
        ),
    ],
)
def test_unclassified_owner_captures_of_one_pass_post_one_blocker_listing_each(root, live, to, note):
    root.configure(register_lane="register-desk")
    root.live = live
    prompts = root.state_dir / "owner-prompts.jsonl"
    long = "split the nightly report into one file per team and mail each team its own file every morning before standup"
    captures = [
        {"digest": CLASSIFIED, "at": "Oct 23 4:12 AM", "session": SESSION, "source": "prompt", "text": "rename the export button"},
        {"digest": UNCLASSIFIED, "at": "Oct 23 4:20 AM", "session": SESSION, "source": "prompt", "text": "keep the\nnightly report"},
        {"digest": "1f2e3d4c5b6a7980", "at": "Oct 23 4:21 AM", "session": SESSION, "source": "prompt", "text": long},
    ]
    prompts.write_text("".join(json.dumps(capture) + "\n" for capture in captures))
    go = "cci post --lane root --kind go --to walker --text 'cut web'"

    assert root.bash(PreToolUseEvent, go) == []
    assert root.bash(PreToolUseEvent, go) == []

    [blocker] = root.blockers()
    assert option(blocker, "--to") == to
    assert option(blocker, "--text") == (
        f'2 owner captures have no row in register brook: {UNCLASSIFIED} (Oct 23 4:20 AM) "keep the nightly report"; '
        f'1f2e3d4c5b6a7980 (Oct 23 4:21 AM) "{long[:80]}". '
        f"Classify them with `rulings.py classify` before the next key moment.{note}"
    )

    later = {"digest": "0a9b8c7d6e5f4321", "at": "Oct 23 4:30 AM", "session": SESSION, "source": "prompt", "text": "drop the beta flag"}
    prompts.write_text(prompts.read_text() + json.dumps(later) + "\n")
    assert root.bash(PreToolUseEvent, go) == []

    [_, single] = root.blockers()
    assert option(single, "--text").startswith('Owner capture 0a9b8c7d6e5f4321 (Oct 23 4:30 AM) has no row in register brook: "drop the beta flag".')


def test_a_drive_whose_plan_stem_differs_from_its_program_reads_the_program_register(root):
    root.bash(PreToolUseEvent, "cci post --lane root --kind go --to walker --text 'cut web'")

    reads = [call for call in root.calls if "rows" in call and call[call.index("rows") - 1].endswith("rulings.py")]
    assert reads and all(call[call.index("rows") + 1 : call.index("rows") + 3] == ("--drive", DRIVE) for call in reads)
    assert not any(PLAN_STEM in call for call in root.calls)


def test_a_register_read_error_passes_the_call_and_posts_one_blocker(root):
    root.rows = (1, "ccn: no ledger titled register: brook")
    go = "cci post --lane root --kind go --to walker --text 'cut web'"

    assert root.bash(PreToolUseEvent, go) == []
    assert root.bash(PreToolUseEvent, go) == []
    assert root.fire(PreToolUseEvent, tool_name="Agent", tool_input={"name": "lane-x", "prompt": "Fix it."}) == []

    texts = [option(blocker, "--text") for blocker in root.blockers()]
    assert [text.split()[0] for text in texts] == ["register_gate", "go_and_cut_gate", "owner_question_gate"]
    assert all("could not read the register (ccn: no ledger titled register: brook)" in text for text in texts)


def test_a_reply_on_a_block_closes_only_the_question_asked_on_it(root):
    loops = root.question("5f1d2c3b4a596877", "what is a relay slot? why is this here?", ["c7-timers"])
    row = root.question("6e2f3d4c5b6a7988", "why does it need this lookup?", ["c6-lookup"])
    chat = root.question("0123456789abcdef", "what is the merge card?", [])

    root.bash(PostToolUseEvent, "cc-present reply --block c7-timers --body 'No laptop writes a server-started job.'")

    assert json.loads(loops.read_text())["closed"] == "reply c7-timers"
    assert "closed" not in json.loads(row.read_text())
    assert "closed" not in json.loads(chat.read_text())


def test_the_turn_end_closes_chat_questions_and_leaves_board_questions_open(root):
    loops = root.question("5f1d2c3b4a596877", "what is a relay slot? why is this here?", ["c7-timers"])
    chat = root.question("0123456789abcdef", "what is the merge card?", [])

    root.fire(StopEvent)

    assert json.loads(chat.read_text())["closed"] == "turn end"
    assert "closed" not in json.loads(loops.read_text())


def test_a_closed_question_no_longer_warns_a_writer_spawn(root):
    root.question("0123456789abcdef", "what is the merge card?", [], "merge card")
    root.question("fedcba9876543210", "what is a relay slot?", ["c7-timers"])
    spawn = {"name": "lane-x", "prompt": "Lane lane-x: redraw the merge card."}

    [warning] = root.fire(PreToolUseEvent, tool_name="Agent", tool_input=spawn)
    root.fire(StopEvent)

    assert warning.startswith("An owner question is open and this reply or brief touches it without quoting it.")
    assert root.fire(PreToolUseEvent, tool_name="Agent", tool_input=spawn) == []



@pytest.mark.parametrize("path", REGISTER_SOURCES, ids=lambda path: path.name)
def test_no_register_script_or_hook_watches_slack(path):
    text = path.read_text()

    assert [call for call in ("slack_watch", "drive.py thread", "drive.py watch") if call in text] == []


def test_acceptance_reads_the_contract_missing_line_a_failing_accepted_run_prints(monkeypatch):
    def refused(cwd, *args, **_):
        raise subprocess.CalledProcessError(1, ["rulings.py", *args], output="CONTRACT-MISSING lane-a: the newest grade, #9, is missing: rule/bbbbbbb\n")

    def crashed(cwd, *args, **_):
        raise subprocess.CalledProcessError(2, ["rulings.py", *args], output="Traceback (most recent call last):\n")

    evt = argparse.Namespace(cwd=Path("/repo"))
    monkeypatch.setattr(register_gates, "rulings", refused)
    assert register_gates.acceptance(evt, "lane-a", DRIVE) == "CONTRACT-MISSING lane-a: the newest grade, #9, is missing: rule/bbbbbbb\n"
    monkeypatch.setattr(register_gates, "rulings", crashed)
    with pytest.raises(subprocess.CalledProcessError):
        register_gates.acceptance(evt, "lane-a", DRIVE)


def test_a_named_ship_helper_is_gated_as_the_lane_that_spawned_it(tmp_path):
    subagents = tmp_path / SESSION / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-arelease-fix-1a2b.meta.json").write_text(json.dumps({"name": "release-fix", "agentType": "long-running:lane-ship"}))
    (subagents / "agent-aship-602-1a2b.meta.json").write_text(json.dumps({"name": "ship-602", "agentType": "ship-pr", "parentAgentId": "arelease-fix-1a2b"}))
    helper = subagents / "agent-aship-602-1a2b.jsonl"
    helper.write_text("")
    transcript = tmp_path / f"{SESSION}.jsonl"
    transcript.write_text("")
    raw = {"session_id": SESSION, "transcript_path": str(transcript), "tool_name": "Bash", "tool_input": {"command": "gh pr create"}, "cwd": str(tmp_path), "agent_id": "aship-602-1a2b"}

    assert register_gates.owning_lane(PreToolUseEvent(_raw=raw, ctx=build_context(None, helper, tmp_path, tmp_path))) == "release-fix"
