from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest
from captain_hook.events import PostToolUseEvent, PreToolUseEvent, StopEvent
from captain_hook.testing.helpers import build_context

import cut_gate
from fire import fire
from hooks import register_gates
from hooks.compaction_handoff import CompactionState
from hooks.tests.register_fixtures import LEDGER, ROWS

SESSION = "0123456789abcdef"
DRIVE = "brook-drive"
CLASSIFIED = "c4936ff0405c2d0f"
UNCLASSIFIED = "629293ae14388dc9"
PLUGIN = Path(__file__).parents[1]
REGISTER_SOURCES = [*sorted((PLUGIN / "capt-hook" / "hooks").glob("register_*.py")), *(PLUGIN / "skills" / "long-running" / "scripts" / name for name in ("rulings.py", "cut_gate.py"))]
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
REPO = "example/web"
FILTERS = frozenset({"--target", "--kind", "--pr", "--topic"})
WATCH = {"kind": "gate", "status": "owed", "blocks": json.dumps(["sand"]), "words": "Wait out the previous release's watch."}
TESTING_BAR = {"kind": "gate", "status": "owed", "blocks": json.dumps(["widen"]), "words": "Run every scenario before widening."}
RESERVED = {"kind": "reserved", "status": "broken", "words": "Nothing deletes user data."}
A, B = "a" * 40, "b" * 40


class Root:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.state_dir = home / "scratch" / "brook"
        (self.state_dir / "register").mkdir(parents=True)
        self.session_dir = home / "session"
        self.calls: list[tuple[str, ...]] = []
        self.rows: str | tuple[int, str] = json.dumps(
            {"ledger": LEDGER, "program": "brook", "state_dir": str(self.state_dir), "rows": {}, "classified": [CLASSIFIED]}
        )
        CompactionState(active=True, slug="brook").save(self.event(PostToolUseEvent, tool_name="Bash"))

    def event(self, cls, **raw):
        payload = {"session_id": SESSION, "cwd": str(self.home)}
        return cls(_raw=payload | raw, ctx=build_context(session_dir=self.session_dir))

    def run(self, args, *pargs, **kwargs) -> subprocess.CompletedProcess:
        argv = tuple(str(arg) for arg in args)
        self.calls.append(argv)
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

    def blockers(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[:2] == ("cci", "post") and "blocker" in call]

    def question(self, digest: str, text: str, blocks: list[str]) -> Path:
        path = self.state_dir / "register" / f"question-{digest}.json"
        path.write_text(json.dumps({"digest": digest, "text": text, "term": "", "sentence": "", "rows": [], "blocks": blocks}))
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


def test_an_unclassified_owner_capture_posts_one_blocker_and_never_refuses(root):
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
    assert option(blocker, "--to") == "root"
    assert option(blocker, "--text").startswith(f'Owner capture {UNCLASSIFIED} (Oct 23 4:20 AM) has no row in register brook: "keep the nightly report".')


def test_a_register_read_error_passes_the_call_and_posts_one_blocker(root):
    root.rows = (1, "ccn: no ledger titled register: brook")
    go = "cci post --lane root --kind go --to walker --text 'cut web'"

    assert root.bash(PreToolUseEvent, go) == []
    assert root.bash(PreToolUseEvent, go) == []
    assert root.fire(PreToolUseEvent, tool_name="Agent", tool_input={"name": "lane-x", "prompt": "Fix it."}) == []

    texts = [option(blocker, "--text") for blocker in root.blockers()]
    assert [text.split()[0] for text in texts] == ["register_gate", "owner_question_gate"]
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


def test_a_closed_question_no_longer_gates_a_writer_spawn(root):
    root.question("0123456789abcdef", "what is the merge card?", [])
    spawn = {"name": "lane-x", "prompt": "Lane lane-x: fix the flaky api test."}

    [refusal] = root.fire(PreToolUseEvent, tool_name="Agent", tool_input=spawn)
    root.fire(StopEvent)

    assert refusal.startswith("This reply or brief does not quote the open owner question below.")
    assert root.fire(PreToolUseEvent, tool_name="Agent", tool_input=spawn) == []



@pytest.mark.parametrize("path", REGISTER_SOURCES, ids=lambda path: path.name)
def test_no_register_script_or_hook_watches_slack(path):
    text = path.read_text()

    assert [call for call in ("slack_watch", "drive.py thread", "drive.py watch") if call in text] == []


def stamp(minutes_ago: int) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


class Bus:
    def __init__(self, records: list[dict], rows: list[dict]) -> None:
        self.records = [{"seq": seq, "refs": {}, **record} for seq, record in enumerate(records, start=1)]
        self.rows = rows

    def run(self, argv: list[str]) -> str:
        if argv[:2] == ["cci", "tail"]:
            since = int(argv[argv.index("--since") + 1])
            filters = {flag: value for flag, value in zip(argv, argv[1:], strict=False) if flag in FILTERS}
            found = [record for record in self.records if record["seq"] > since and self.matches(record, filters)]
            return "".join(json.dumps(record) + "\n" for record in found)
        assert argv[:3] == [sys.executable, str(cut_gate.LEDGER_SCRIPT), "list"]
        return json.dumps(self.rows)

    @staticmethod
    def matches(record: dict, filters: dict[str, str]) -> bool:
        refs = record["refs"]
        return (
            ("--target" not in filters or filters["--target"] in refs.get("targets", []))
            and ("--kind" not in filters or record["kind"] == filters["--kind"])
            and ("--pr" not in filters or filters["--pr"] in refs.get("prs", []))
            and ("--topic" not in filters or record.get("topic") == filters["--topic"])
        )


def released(minutes_ago: int, topic: str = "") -> dict:
    return {"kind": "release", "text": "RELEASED sand 0.1.2", "at": stamp(minutes_ago), "refs": {"targets": ["sand"]}, **({"topic": topic} if topic else {})}


def landed(pr: str, minutes_ago: int, step: str, head: str) -> dict:
    return {"pr": pr, "state": "landed", "landed_at": stamp(minutes_ago), "head": head, "step": step}


def graded(pr: str, head: str, *keys: str) -> dict:
    return {"kind": "review", "text": f"fidelity: ok {head[:7]} checked {','.join(keys) or 'rule/0a1b2c3'}", "at": stamp(1), "refs": {"prs": [f"{REPO}#{pr}"]}}


def cut(bus: Bus, rows: dict, verb: str = "cut", incident: bool = False, repairs: tuple[str, ...] = ()) -> dict:
    args = argparse.Namespace(
        verb=verb, target=["sand"], drive="brook-drive", ledger="9e8d7c6", repo=REPO, started=stamp(600), incident=incident, repair=list(repairs)
    )
    return cut_gate.check(args, bus.run, {"rows": rows}, NOW)


def keys(found: list[dict]) -> list[str]:
    return [finding["key"] for finding in found]



def test_c227_outside_an_incident_the_cut_waits_on_an_owed_watch():
    bus = Bus([released(300)], [landed("41", 200, "1.5", A)])
    bus.records.append({"seq": 99, "kind": "review", **graded("41", A)})

    assert keys(cut(bus, {"rule/1a2b3c4": WATCH})["refusals"]) == ["gate:rule/1a2b3c4"]


def test_c227_after_r1b_a_cut_spanning_two_plan_steps_is_refused():
    bus = Bus([released(300, "R1b"), graded("41", A), graded("42", B)], [landed("41", 20, "1.5", A), landed("42", 10, "1.9", B)])

    assert keys(cut(bus, {})["refusals"]) == ["steps"]


def test_c227_r1as_cut_carries_two_steps_and_passes():
    bus = Bus([released(300, "R1a"), graded("41", A), graded("42", B)], [landed("41", 20, "1.5", A), landed("42", 10, "1.9", B)])

    assert cut(bus, {}) == {"refusals": [], "blockers": []}


def test_c227_a_head_ungraded_thirty_minutes_after_merge_is_refused():
    bus = Bus([released(300)], [landed("41", 45, "1.5", A), landed("42", 10, "1.5", B)])

    assert keys(cut(bus, {})["refusals"]) == ["ungraded:41"]


def test_c227_a_broken_reserved_row_is_refused_until_a_riding_head_is_graded_against_it():
    rows = {"rule/5b6c7d8": RESERVED}
    unchecked = Bus([released(300), graded("41", A, "rule/0a1b2c3")], [landed("41", 20, "1.5", A)])
    checked = Bus([released(300), graded("41", A, "rule/0a1b2c3", "rule/5b6c7d8")], [landed("41", 20, "1.5", A)])

    assert keys(cut(unchecked, rows)["refusals"]) == ["broken:rule/5b6c7d8"]
    assert cut(checked, rows)["refusals"] == []


def test_c227_the_testing_bar_blocks_the_flag_widening_and_not_the_cut():
    bus = Bus([released(300)], [])
    rows = {"rule/7e8f901": TESTING_BAR}

    assert keys(cut(bus, rows, verb="widen")["refusals"]) == ["gate:rule/7e8f901"]
    assert cut(bus, rows)["refusals"] == []


def test_c227_prs_landed_before_the_targets_last_release_do_not_ride():
    bus = Bus([released(30, "R1b")], [landed("41", 200, "1.5", A), landed("42", 100, "1.9", B)])

    assert cut(bus, {}) == {"refusals": [], "blockers": []}


def test_c228_during_an_incident_a_cut_carrying_the_declared_fix_passes_with_a_blocker():
    bus = Bus([released(300)], [landed("41555", 45, "1.5", A)])

    outcome = cut(bus, {"rule/1a2b3c4": WATCH}, incident=True, repairs=("41555",))

    assert outcome["refusals"] == []
    assert keys(outcome["blockers"]) == ["gate:rule/1a2b3c4", "ungraded:41555"]


def test_c228_during_an_incident_a_cut_without_the_declared_fix_is_refused():
    bus = Bus([released(300), graded("41", A)], [landed("41", 20, "1.5", A)])

    outcome = cut(bus, {}, incident=True, repairs=("41555",))

    assert keys(outcome["refusals"]) == ["fix:41555"]
    assert outcome["blockers"] == []
