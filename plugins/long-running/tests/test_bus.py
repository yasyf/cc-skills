"""A fake :class:`bus.Shell` over an in-memory `ccn log`: add, append, and entry list, plus a clock the watch sleeps on."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bus
import pytest

BUS = "4f7443bcf3235ffffa3bd3b3ca0f7a3645be94f9"
HEAD = "3f3acff97aa11bb22cc33dd44ee55ff667788990"
LANE = "pr-plans"
OTHER = "iam-structural"


class FakeShell(bus.Shell):
    def __init__(self):
        self.logs: dict[str, list[dict]] = {BUS: []}
        self.calls: list[list[str]] = []
        self.contended = 0
        self.unreachable = False
        self.clock = 0.0
        self.slept: list[float] = []
        self.on_sleep = None

    def run(self, argv, stdin=None):
        self.calls.append(list(argv))
        assert argv[0] == "ccn", argv
        argv = argv[3:] if argv[1] == "-R" else argv[1:]
        if self.unreachable:
            raise subprocess.CalledProcessError(1, ["ccn", *argv], stderr="open repository: not a git repository\n")
        if argv[:2] == ["log", "add"]:
            self.logs[BUS[::-1]] = []
            return json.dumps({"id": BUS[::-1], "title": argv[2], "entry_count": 0})
        if argv[:2] == ["log", "append"]:
            if self.contended:
                self.contended -= 1
                raise subprocess.CalledProcessError(1, ["ccn", *argv], stderr="conflict: append to refs/cc-notes/logs/x: ref contended\n")
            entries = self.logs[argv[2]]
            entries.append({"author": "yasyf", "ts": self.stamp(), "text": argv[3]})
            return json.dumps({"id": argv[2], "entry_count": len(entries)})
        if argv[:3] == ["log", "entry", "list"]:
            return json.dumps(self.logs[argv[3]])
        raise AssertionError(f"unexpected ccn call: {argv}")

    def stamp(self) -> str:
        return (datetime(2026, 9, 29, 20, 0, tzinfo=timezone.utc) + timedelta(seconds=self.clock)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.clock += seconds
        if self.on_sleep:
            self.on_sleep(self)

    def monotonic(self):
        return self.clock

    def texts(self) -> list[dict]:
        return [json.loads(record["text"]) for record in self.logs[BUS]]

    def append(self, kind: str, topic: str, sender: str, to: list[str], text: str) -> None:
        body = {"kind": kind, "topic": topic, "from": sender, "to": to, "text": text, "re": None}
        self.logs[BUS].append({"author": "yasyf", "ts": self.stamp(), "text": json.dumps(body)})


@pytest.fixture
def shell() -> FakeShell:
    return FakeShell()


@pytest.fixture
def cursor(tmp_path) -> Path:
    return tmp_path / "cursors" / f"{LANE}.cursor"


def run(shell, *argv) -> int:
    return bus.main(list(argv), shell)


def post(shell, kind, text, sender=OTHER, topic="iam-contract", *extra) -> int:
    return run(shell, "post", "--bus", BUS, "--from", sender, "--kind", kind, "--topic", topic, "--text", text, "--lock", "/tmp/bus-test.lock", *extra)


def read(shell, cursor, *extra, capsys=None) -> list[str]:
    capsys.readouterr()
    assert run(shell, "read", "--bus", BUS, "--lane", LANE, "--cursor", str(cursor), *extra) == 0
    return capsys.readouterr().out.splitlines()


def test_init_prints_the_new_log_id(shell, capsys):
    assert run(shell, "init", "--title", "bus: release drive") == 0

    assert capsys.readouterr().out.strip() == BUS[::-1]
    assert shell.calls == [["ccn", "log", "add", "bus: release drive", "--json"]]


def test_post_appends_one_json_entry_and_prints_its_seq(shell, capsys):
    assert post(shell, "decision", "IAM wave ships before the pre-hold", OTHER, "iam-contract", "--to", LANE, "--to", "release-5m") == 0

    assert capsys.readouterr().out == f"#1 decision iam-contract {OTHER} -> {LANE},release-5m\n"
    assert shell.texts() == [{"kind": "decision", "topic": "iam-contract", "from": OTHER, "to": [LANE, "release-5m"], "text": "IAM wave ships before the pre-hold", "re": None}]


def test_post_passes_the_repo_to_ccn(shell):
    assert post(shell, "decision", "x", OTHER, "t", "--repo", "/repo/worktree") == 0

    assert shell.calls[-1][:3] == ["ccn", "-R", "/repo/worktree"]


def test_a_head_is_a_full_sha(shell):
    with pytest.raises(SystemExit, match="full 40-hex sha"):
        post(shell, "head", HEAD[:9])

    assert post(shell, "head", HEAD) == 0


def test_post_needs_a_topic_unless_it_replies(shell):
    with pytest.raises(SystemExit, match="needs --topic"):
        run(shell, "post", "--bus", BUS, "--from", OTHER, "--kind", "decision", "--text", "x", "--lock", "/tmp/bus-test.lock")


def test_post_retries_a_contended_append_then_gives_up(shell, capsys):
    shell.contended = 2
    assert post(shell, "decision", "x") == 0
    assert shell.slept == [0.2, 0.4]
    assert len(shell.logs[BUS]) == 1

    shell.contended = 5
    assert post(shell, "decision", "y") == 1
    assert "bus unreachable, wrote nothing: conflict: append" in capsys.readouterr().err
    assert len(shell.logs[BUS]) == 1


def test_an_answer_needs_its_ask_and_goes_back_to_the_asker(shell, capsys):
    post(shell, "ask", "may pr-plans land before the IAM wave?", LANE, "iam-contract", "--to", OTHER)
    post(shell, "decision", "unrelated")

    with pytest.raises(SystemExit, match="needs --re"):
        run(shell, "post", "--bus", BUS, "--from", OTHER, "--kind", "answer", "--text", "yes", "--lock", "/tmp/bus-test.lock")
    with pytest.raises(SystemExit, match="#2 is a decision, not an ask"):
        run(shell, "post", "--bus", BUS, "--from", OTHER, "--kind", "answer", "--text", "yes", "--re", "2", "--lock", "/tmp/bus-test.lock")
    with pytest.raises(SystemExit, match="no entry #9"):
        run(shell, "post", "--bus", BUS, "--from", OTHER, "--kind", "answer", "--text", "yes", "--re", "9", "--lock", "/tmp/bus-test.lock")
    capsys.readouterr()

    assert run(shell, "post", "--bus", BUS, "--from", OTHER, "--kind", "answer", "--text", "yes, land it", "--re", "1", "--lock", "/tmp/bus-test.lock") == 0

    assert capsys.readouterr().out == f"#3 answer iam-contract {OTHER} -> {LANE}\n"
    assert shell.texts()[-1] == {"kind": "answer", "topic": "iam-contract", "from": OTHER, "to": [LANE], "text": "yes, land it", "re": 1}


def test_only_the_poster_withdraws_and_only_once(shell):
    post(shell, "decision", "verdict: artifact reads are declared", "artifact-contract", "artifacts", "--to", "partial-release")

    with pytest.raises(SystemExit, match="only its poster withdraws it"):
        run(shell, "post", "--bus", BUS, "--from", LANE, "--kind", "withdraw", "--text", "no", "--re", "1", "--lock", "/tmp/bus-test.lock")
    assert run(shell, "post", "--bus", BUS, "--from", "artifact-contract", "--kind", "withdraw", "--text", "verdict retracted", "--re", "1", "--lock", "/tmp/bus-test.lock") == 0
    with pytest.raises(SystemExit, match="already withdrawn by #2"):
        run(shell, "post", "--bus", BUS, "--from", "artifact-contract", "--kind", "withdraw", "--text", "again", "--re", "1", "--lock", "/tmp/bus-test.lock")

    assert shell.texts()[-1] == {"kind": "withdraw", "topic": "artifacts", "from": "artifact-contract", "to": ["partial-release"], "text": "verdict retracted", "re": 1}


def test_re_belongs_to_replies_only(shell):
    with pytest.raises(SystemExit, match="--re is for answer and withdraw"):
        post(shell, "decision", "x", OTHER, "t", "--re", "1")


def test_read_delivers_addressed_and_subscribed_entries_and_advances_the_cursor(shell, cursor, capsys):
    post(shell, "decision", "own post", LANE, "pr-plans")
    post(shell, "decision", "addressed to me", OTHER, "elsewhere", "--to", LANE)
    post(shell, "decision", "addressed elsewhere", OTHER, "iam-contract", "--to", "release-5m")
    post(shell, "head", HEAD, OTHER, "27510")
    post(shell, "blocker", "plan job red on 27510", "landing-desk", "27510")
    post(shell, "contract", "AWS contract v2", OTHER, "iam-contract")

    assert read(shell, cursor, capsys=capsys) == [
        f"#2 20:00Z decision elsewhere {OTHER} -> {LANE}: addressed to me",
        f"#4 20:00Z head 27510 {OTHER}: {HEAD}",
        "#5 20:00Z blocker 27510 landing-desk: plan job red on 27510",
        f"#6 20:00Z contract iam-contract {OTHER}: AWS contract v2",
    ]
    assert cursor.read_text() == "6"
    assert read(shell, cursor, capsys=capsys) == ["nothing new since #6"]

    assert read(shell, cursor, "--all", "--topic", "27510", capsys=capsys) == [
        f"#2 20:00Z decision elsewhere {OTHER} -> {LANE}: addressed to me",
        f"#4 20:00Z head 27510 {OTHER}: {HEAD}",
        "#5 20:00Z blocker 27510 landing-desk: plan job red on 27510",
    ]
    assert read(shell, cursor, "--since", "3", "--kind", "contract", capsys=capsys) == [f"#6 20:00Z contract iam-contract {OTHER}: AWS contract v2"]
    assert read(shell, cursor, "--all", "--topic", "iam-contract", "--kind", "blocker", capsys=capsys)[1:] == [
        "#5 20:00Z blocker 27510 landing-desk: plan job red on 27510",
        f"#6 20:00Z contract iam-contract {OTHER}: AWS contract v2",
    ]


def test_peek_and_json_leave_the_cursor_and_render_records(shell, cursor, capsys):
    post(shell, "decision", "x", OTHER, "t")

    lines = read(shell, cursor, "--peek", "--json", capsys=capsys)

    assert json.loads(lines[0]) == [{"seq": 1, "at": "2026-09-29T20:00:00Z", "kind": "decision", "topic": "t", "from": OTHER, "to": [], "text": "x", "re": None}]
    assert not cursor.exists()
    assert read(shell, cursor, "--json", capsys=capsys) == [json.dumps([json.loads(lines[0])[0]])]
    assert cursor.read_text() == "1"


def test_read_marks_a_withdrawn_verdict_and_an_answered_ask(shell, cursor, capsys):
    post(shell, "decision", "verdict: declared reads only", "artifact-contract", "artifacts", "--to", LANE)
    post(shell, "ask", "is #27510 clear to label?", LANE, "27510", "--to", OTHER)
    run(shell, "post", "--bus", BUS, "--from", "artifact-contract", "--kind", "withdraw", "--text", "verdict retracted, contract changed", "--re", "1", "--lock", "/tmp/bus-test.lock")
    run(shell, "post", "--bus", BUS, "--from", OTHER, "--kind", "answer", "--text", "yes", "--re", "2", "--lock", "/tmp/bus-test.lock")

    assert read(shell, cursor, capsys=capsys) == [
        f"#1 20:00Z decision artifacts artifact-contract -> {LANE}: verdict: declared reads only [WITHDRAWN #3]",
        f"#3 20:00Z withdraw artifacts artifact-contract -> {LANE} re #1: verdict retracted, contract changed",
        f"#4 20:00Z answer 27510 {OTHER} -> {LANE} re #2: yes",
    ]


def test_read_reports_an_unreachable_bus_and_leaves_the_cursor(shell, cursor, capsys):
    shell.unreachable = True

    assert run(shell, "read", "--bus", BUS, "--lane", LANE, "--cursor", str(cursor)) == 1

    assert capsys.readouterr().err == "bus unreachable, wrote nothing: open repository: not a git repository\n"
    assert not cursor.exists()


def test_watch_prints_each_new_delivery_once_and_exits_at_its_deadline(shell, cursor, capsys):
    post(shell, "decision", "before the watch", OTHER, "t", "--to", LANE)
    bus.write_cursor(cursor, 1)

    def land(fake: FakeShell):
        if len(fake.slept) == 1:
            fake.append("blocker", "27510", "landing-desk", [LANE], "CI red on 27510")
            fake.append("decision", "t", OTHER, ["release-5m"], "not for me")
        if len(fake.slept) == 2:
            fake.append("head", "27510", OTHER, [], HEAD)

    shell.on_sleep = land
    capsys.readouterr()

    assert run(shell, "watch", "--bus", BUS, "--lane", LANE, "--cursor", str(cursor), "--interval", "30", "--for", "75") == 0

    assert capsys.readouterr().out.splitlines() == [
        "#2 20:00Z blocker 27510 landing-desk -> pr-plans: CI red on 27510",
        f"#4 20:01Z head 27510 {OTHER}: {HEAD}",
    ]
    assert shell.slept == [30.0, 30.0, 30.0]
    assert cursor.read_text() == "4"


def test_watch_says_so_when_the_bus_vanishes(shell, cursor, capsys):
    shell.unreachable = True

    assert run(shell, "watch", "--bus", BUS, "--lane", LANE, "--cursor", str(cursor), "--for", "60") == 1

    assert capsys.readouterr().out == "bus unreachable: open repository: not a git repository\n"


def test_state_folds_to_the_latest_live_head_and_contract_per_lane_and_topic(shell, capsys):
    post(shell, "head", "0" * 40, OTHER, "27510")
    post(shell, "head", HEAD, OTHER, "27510")
    post(shell, "contract", "AWS contract v1", OTHER, "iam-contract")
    post(shell, "contract", "artifact reads declared", "artifact-contract", "artifacts")
    post(shell, "head", "1" * 40, LANE, "27520")
    run(shell, "post", "--bus", BUS, "--from", "artifact-contract", "--kind", "withdraw", "--text", "retracted", "--re", "4", "--lock", "/tmp/bus-test.lock")
    capsys.readouterr()

    assert run(shell, "state", "--bus", BUS) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"head 27510 {OTHER} {HEAD} #2",
        f"contract iam-contract {OTHER}: AWS contract v1 #3",
        f"head 27520 {LANE} {'1' * 40} #5",
    ]

    assert run(shell, "state", "--bus", BUS, "--lane", LANE) == 0
    assert capsys.readouterr().out == f"head 27520 {LANE} {'1' * 40} #5\n"
    assert run(shell, "state", "--bus", BUS, "--topic", "artifacts") == 0
    assert capsys.readouterr().out == "no live heads or contracts\n"


def test_summary_counts_and_names_every_open_ask_and_blocker_and_the_latest_decisions(shell, capsys):
    post(shell, "ask", "answered", LANE, "27510", "--to", OTHER)
    post(shell, "ask", "still open", LANE, "27510", "--to", OTHER)
    post(shell, "blocker", "cleared", "landing-desk", "27510")
    post(shell, "blocker", "CI red on 27520", "landing-desk", "27520")
    post(shell, "decision", "older", OTHER, "iam-contract")
    post(shell, "decision", "newer", OTHER, "iam-contract")
    post(shell, "head", HEAD, OTHER, "27510")
    post(shell, "contract", "AWS contract v1", OTHER, "iam-contract")
    run(shell, "post", "--bus", BUS, "--from", OTHER, "--kind", "answer", "--text", "yes", "--re", "1", "--lock", "/tmp/bus-test.lock")
    run(shell, "post", "--bus", BUS, "--from", "landing-desk", "--kind", "withdraw", "--text", "green now", "--re", "3", "--lock", "/tmp/bus-test.lock")
    capsys.readouterr()

    assert run(shell, "summary", "--bus", BUS, "--window-seconds", "0") == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(f"bus {BUS[:8]} ")
    assert lines[0].endswith("| entries 10 | last 0m 0 | open asks 1 | open blockers 1 | heads 1 | contracts 1")
    assert [line.split(", ")[0] for line in lines[1:]] == [f"ASK #2 {LANE} -> {OTHER} (27510", "BLOCKER #4 landing-desk (27520"]
    assert [line.split("): ")[1] for line in lines[1:]] == ["still open", "CI red on 27520"]


def test_summary_lists_decisions_newest_first_inside_the_window_and_caps_at_ten_lines(shell, capsys):
    for index in range(12):
        post(shell, "decision", f"decision {index}", OTHER, "iam-contract")
    capsys.readouterr()

    assert run(shell, "summary", "--bus", BUS) == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 10
    assert lines[1] == f"decision #12 {OTHER} (iam-contract): decision 11"
    assert lines[9] == "... 4 more lines in bus.py read --all"
