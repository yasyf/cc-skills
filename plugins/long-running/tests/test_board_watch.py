"""board_watch.py over a recorded cc-present stream: every owner interaction becomes a capture and one bus record, and each event line is kept verbatim."""

from __future__ import annotations

import fcntl
import hashlib
import json
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

import board_watch
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "board_watch"
SESSION = "board-1"
ARRIVED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
CONFIRMED = "download the update in the background, show the banner, and only restart when i click it"


class RecordedShell(board_watch.Shell):
    def __init__(self, bus: list[dict] | None = None, doc: dict | None = None, refused: bool = False) -> None:
        super().__init__()
        self.bus = bus or []
        self.doc = doc or json.loads((FIXTURES / "outcomes.json").read_text())["doc"]
        self.refused = refused
        self.posts: list[list[str]] = []
        self.tasks: list[tuple[str, list[str]]] = []
        self.root = SESSION

    def watch(self, session: str):
        assert session == SESSION
        yield from (FIXTURES / "stream.jsonl").read_text().splitlines(keepends=True)

    def document(self, session: str, block: str) -> dict | None:
        return self.doc

    def spawn(self, target, *args) -> None:
        self.spawned = [*getattr(self, "spawned", []), target.__name__]

    def cci(self, argv: list[str]) -> subprocess.CompletedProcess:
        if argv[0] == "grep":
            return subprocess.CompletedProcess(argv, 0, "".join(json.dumps(record) + "\n" for record in self.bus), "")
        self.posts.append(argv)
        return subprocess.CompletedProcess(argv, 1 if self.refused else 0, "", "refused" if self.refused else "")

    def now(self) -> datetime:
        return ARRIVED

    def root_session(self, drive_name: str) -> str | None:
        return self.root

    def entry(self, drive_name: str) -> dict | None:
        return {"drive": drive_name, "program": drive_name, "checkout": "/checkouts/brook", "sessions": [SESSION]}

    def ccn(self, checkout: str, argv: list[str]) -> subprocess.CompletedProcess:
        self.tasks.append((checkout, argv))
        return subprocess.CompletedProcess(argv, 0, json.dumps({"id": f"task{len(self.tasks)}abcdef", "title": argv[2]}), "")

    def posted(self, name: str, kind: str = "owner") -> list[str]:
        return [post[post.index(name) + 1] for post in self.posts if name in post and post[post.index("--kind") + 1] == kind]


def run(state: Path, shell: board_watch.Shell | None = None) -> list[dict]:
    assert board_watch.main(["start", "--session", SESSION, "--program", "brook", "--state-dir", str(state)], shell or RecordedShell()) == 0
    return [json.loads(line) for line in (state / "owner-prompts.jsonl").read_text().splitlines()]


def test_every_owner_interaction_is_captured_in_order(tmp_path: Path) -> None:
    captured = run(tmp_path)

    assert [(capture["block"], capture["text"]) for capture in captured] == [
        ("share-open-pick", "only for the team, and only read-only"),
        ("share-open-pick", "Yes, read-only"),
        ("board-notes", "also check the share menu"),
        ("share-open-pick", "say who can open it"),
        ("update-confirm-ok", CONFIRMED),
        ("banner-ok", "rejected"),
        ("banner-ok", "keep the old banner text"),
        ("share-open-pick", "Yes, read-only"),
    ]
    assert {capture["source"] for capture in captured} == {"board"}
    assert {capture["at"] for capture in captured} == {"2026-01-02T03:04:05Z"}


def test_a_write_in_then_a_pick_on_one_block_yields_two_captures(tmp_path: Path) -> None:
    picks = [capture for capture in run(tmp_path) if capture["block"] == "share-open-pick"][:2]

    assert [capture["text"] for capture in picks] == ["only for the team, and only read-only", "Yes, read-only"]


def test_the_digest_joins_session_text_block_and_event_line_with_nul(tmp_path: Path) -> None:
    approval = next(capture for capture in run(tmp_path) if capture["block"] == "update-confirm-ok")

    assert approval["digest"] == hashlib.sha256("\0".join([SESSION, CONFIRMED, "update-confirm-ok", "6"]).encode()).hexdigest()[:16]
    assert approval["session"] == SESSION


def test_only_an_approvals_prompt_is_marked_approved(tmp_path: Path) -> None:
    verdicts = {capture["text"]: capture.get("verdict") for capture in run(tmp_path) if capture["block"] in ("update-confirm-ok", "banner-ok")}

    assert verdicts == {CONFIRMED: "approved", "rejected": None, "keep the old banner text": None}


def test_each_event_line_is_kept_verbatim(tmp_path: Path) -> None:
    run(tmp_path)

    assert (tmp_path / "boards" / f"{SESSION}-events.jsonl").read_text() == (FIXTURES / "stream.jsonl").read_text()


class BurstShell(RecordedShell):
    def watch(self, session: str):
        for option in ("snapshot", "transcript", "snapshot"):
            yield json.dumps({"blockId": "q8-choice", "optionIds": [f"q8-{option}"], "schemaVersion": 1, "type": "choice.selected"}) + "\n"


BURST_DOC = {"blocks": [{"id": "q8-choice", "type": "choice", "options": [{"id": "q8-snapshot", "label": "The transcript plus the latest snapshot"}, {"id": "q8-transcript", "label": "Only the transcript"}]}]}


def test_a_burst_that_returns_to_an_earlier_pick_captures_every_tap_in_order(tmp_path: Path) -> None:
    captured = run(tmp_path, BurstShell(doc=BURST_DOC))

    assert [capture["text"] for capture in captured] == ["The transcript plus the latest snapshot", "Only the transcript", "The transcript plus the latest snapshot"]
    assert {capture["at"] for capture in captured} == {"2026-01-02T03:04:05Z"}
    assert len({capture["digest"] for capture in captured}) == 3


def test_a_second_watch_of_the_same_board_exits_at_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "boards").mkdir()
    with (tmp_path / "boards" / f"{SESSION}.lock").open("a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert board_watch.main(["start", "--session", SESSION, "--program", "brook", "--state-dir", str(tmp_path)], RecordedShell()) == 0

    assert "another watch holds" in capsys.readouterr().out
    assert not (tmp_path / "owner-prompts.jsonl").exists()


def test_the_state_dir_resolves_as_rulings_resolves_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    run_state = tmp_path / ".claude" / "scratch" / "brook"

    assert board_watch.main(["start", "--session", SESSION, "--program", "brook"], RecordedShell()) == 0
    assert (run_state / "owner-prompts.jsonl").is_file()


class MissingBlockShell(RecordedShell):
    def watch(self, session: str):
        yield '{"blockId":"gone","optionIds":["yes"],"schemaVersion":1,"type":"choice.selected"}\n'
        yield '{"blockId":"gone","note":"ship it","schemaVersion":1,"type":"decision.created","verdict":"approved"}\n'
        yield '{"blockId":"banner-ok","note":"rejected","schemaVersion":1,"type":"decision.created","verdict":"rejected"}\n'


def test_a_block_missing_from_the_document_gets_no_stand_in_capture(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    captured = run(tmp_path, MissingBlockShell())

    assert [(capture["block"], capture["text"]) for capture in captured] == [("gone", "ship it"), ("banner-ok", "rejected")]
    assert "gone is not in the board's document" in capsys.readouterr().out


class TriageShell(RecordedShell):
    def watch(self, session: str):
        yield '{"blockId":"merge-triage","schemaVersion":1,"type":"triage.decided","verdicts":{"merge-row-101":{"verdict":"approved"}}}\n'
        yield '{"blockId":"merge-triage","schemaVersion":1,"type":"triage.decided","verdicts":{"merge-row-101":{"verdict":"approved","note":"group these three"},"merge-row-102":{"verdict":"rejected","note":"group these three"}}}\n'


def test_each_note_on_a_triage_row_is_captured_with_its_row_and_verdict(tmp_path: Path) -> None:
    captured = run(tmp_path, TriageShell())

    assert [(capture["block"], capture["row"], capture["verdict"], capture["text"], capture["at"]) for capture in captured] == [
        ("merge-triage", "merge-row-101", "approved", "group these three", "2026-01-02T03:04:05Z"),
        ("merge-triage", "merge-row-102", "rejected", "group these three", "2026-01-02T03:04:05Z"),
    ]
    assert captured[0]["digest"] == hashlib.sha256("\0".join([SESSION, "group these three", "merge-row-101", "2"]).encode()).hexdigest()[:16]


def test_a_credential_in_a_board_note_is_redacted_in_its_capture() -> None:
    event = {"type": "triage.decided", "blockId": "notes", "verdicts": {"r1": {"verdict": "ok", "note": "token api-0f3c2b1a-9d8e-4f7a-b6c5-1a2b3c4d5e6f works"}}}

    [capture] = board_watch.captures(event, SESSION, None, ARRIVED, 1)

    assert capture["text"] == "token [credential redacted] works"
    dotted = {**event, "verdicts": {"r1": {"verdict": "ok", "note": "token ghs_1aB2cD3eF4gH5iJ6kL7m.Nb8Pq9-Rs0Tu1Vw2 works"}}}
    assert board_watch.captures(dotted, SESSION, None, ARRIVED, 1)[0]["text"] == "token [credential redacted] works"


SHARE = "Owner on board board-1, Should a shared note open in the browser? (share-open-pick)"


def test_each_owner_interaction_reaches_the_bus_once_with_the_card_and_his_words(tmp_path: Path) -> None:
    shell = RecordedShell()
    run(tmp_path, shell)

    assert shell.posted("--text") == [
        f"{SHARE}: wrote \u201conly for the team, and only read-only\u201d, at {{at}}",
        f"{SHARE}: picked \u201cYes, read-only\u201d, at {{at}}",
        "Owner on board board-1, board-notes: wrote \u201calso check the share menu\u201d, at {at}",
        f"{SHARE}: noted \u201csay who can open it\u201d, at {{at}}",
        f"Owner on board board-1, Confirm your words as written? (update-confirm-ok): approved \u201c{CONFIRMED}\u201d, at {{at}}",
        "Owner on board board-1, banner-ok: rejected noted \u201ckeep the old banner text\u201d, at {at}",
        f"{SHARE}: picked \u201cYes, read-only\u201d, at {{at}}",
    ]
    assert {tuple(shell.posted(flag)) for flag in ("--kind", "--to", "--lane", "--drive")} == {("owner",) * 7, ("all",) * 7, ("board-watch",) * 7, ("brook",) * 7}


def test_a_restart_keys_events_after_the_lines_already_recorded(tmp_path: Path) -> None:
    first, second = RecordedShell(), RecordedShell()
    run(tmp_path, first)
    run(tmp_path, second)

    keys = first.posted("--id") + second.posted("--id")
    assert [key.split(":")[2] for key in first.posted("--id")] == ["2", "3", "4", "5", "6", "7", "8"]
    assert len(set(keys)) == len(keys) == 14


class MergeShell(RecordedShell):
    def watch(self, session: str):
        yield '{"blockId":"merge-triage","schemaVersion":1,"type":"triage.decided","verdicts":{"merge-row-101":{"verdict":"approved"}}}\n'
        yield '{"blockId":"merge-prs","schemaVersion":1,"type":"triage.decided","verdicts":{"202":{"verdict":"rejected","head":"413b38e","note":"split it"}}}\n'


MERGE_DOC = {"blocks": [{"id": "card-merge", "type": "card", "title": "Merge these?", "children": [{"id": "merge-triage", "type": "triage", "items": [{"id": "merge-row-101", "hint": "commit abc1234, built on dev"}]}]}]}


def ask(seq: int, text: str, kind: str = "ask", status: str = "open", drive: str = "brook", lane: str = "root") -> dict:
    return {"seq": seq, "lane": lane, "kind": kind, "status": status, "drive": drive, "text": text}


def test_a_merge_tap_names_the_head_and_resolves_only_open_asks_on_that_row(tmp_path: Path) -> None:
    bus = [
        ask(11, "waiting on merge-row-101"),
        ask(12, "hold merge:202:413b38e until he taps", kind="hold"),
        ask(13, "merge-row-101 again", status="closed"),
        ask(14, "merge-row-1010 waits", kind="blocker"),
        ask(15, "merge-row-101 elsewhere", drive="other"),
        ask(16, "#202 is red"),
    ]
    shell = MergeShell(bus, MERGE_DOC)
    run(tmp_path, shell)

    assert shell.posted("--text") == [
        "Owner on board board-1, Merge these? (merge-triage): merge-row-101 approved at abc1234, at {at}",
        "Owner on board board-1, merge-prs: 202 rejected at 413b38e noted \u201csplit it\u201d, at {at}",
    ]
    assert shell.posted("--resolves-seq") == ["11", "12"]
    assert [[arg for arg in post if arg.startswith("--pr=")] for post in shell.posts] == [["--pr=101"], ["--pr=202"]]


def test_a_tap_resolves_open_asks_naming_its_block_or_card_but_not_a_longer_id(tmp_path: Path) -> None:
    bus = [ask(21, "parked on share-open"), ask(22, "share-open-pick needs his word", kind="blocker"), ask(23, "parked on share-open-2")]
    shell = RecordedShell(bus)
    run(tmp_path, shell)

    assert shell.posted("--resolves-seq") == ["21,22"] * 4


def test_each_pick_and_write_in_on_the_roots_board_asks_root_for_a_lane_and_files_a_task(tmp_path: Path) -> None:
    """Root #225532: three owner picks sat two hours with no lane, so each pick and write-in asks root for one the moment it lands."""
    shell = RecordedShell()
    run(tmp_path, shell)

    assert shell.posted("--topic", "ask") == ["needs-lane:share-open-pick", "needs-lane:share-open-pick", "needs-lane:board-notes", "needs-lane:share-open-pick"]
    assert shell.posted("--to", "ask") == ["root"] * 4
    assert shell.posted("--id", "ask") == [f"needs-lane:{key}" for key in shell.posted("--id") if key.split(":")[2] in ("2", "3", "4", "8")]
    assert shell.posted("--text", "ask")[0] == "NEEDS-LANE Should a shared note open in the browser? (share-open-pick) on board board-1: the owner wrote \u201conly for the team, and only read-only\u201d. Spawn a lane whose brief names share-open-pick, or have a lane post a claim naming it; task task1ab"
    assert shell.posted("--text", "ask")[2].startswith("NEEDS-LANE board-notes on board board-1: the owner wrote \u201calso check the share menu\u201d.")
    assert [attrs(post) for post in shell.posts if post[post.index("--kind") + 1] == "ask"][1] == {"session": SESSION, "block": "share-open-pick", "task": "task2abcdef"}
    checkout, argv = shell.tasks[0]
    assert (checkout, argv[:3]) == ("/checkouts/brook", ["task", "add", "Give the owner's pick on Should a shared note open in the browser? (share-open-pick) a lane"])
    assert (argv[3], argv[5:]) == ("--body", ["--backlog", "--label", "program:brook", "--label", "needs-lane", "--json"])
    assert len(shell.tasks) == 4


@pytest.mark.parametrize(
    ("bus", "root", "topics"),
    [
        ([ask(21, "parked on share-open-pick", lane="api-lane")], SESSION, ["needs-lane:board-notes"]),
        ([ask(21, "share-open-pick needs a lane", lane="root")], SESSION, ["needs-lane:share-open-pick", "needs-lane:share-open-pick", "needs-lane:board-notes", "needs-lane:share-open-pick"]),
        ([], "lane-board", []),
    ],
)
def test_a_pick_that_answers_a_lanes_own_ask_or_lands_on_a_lanes_board_already_has_its_lane(tmp_path: Path, bus: list[dict], root: str, topics: list[str]) -> None:
    shell = RecordedShell(bus)
    shell.root = root
    run(tmp_path, shell)

    assert shell.posted("--topic", "ask") == topics
    assert len(shell.tasks) == len(topics)


def test_a_refused_post_is_reported_and_the_watch_keeps_recording(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    captured = run(tmp_path, RecordedShell(refused=True))

    assert len(captured) == 8
    assert capsys.readouterr().out.count("failed: refused") == 7


def test_a_triage_tap_posts_one_record_per_row_with_its_block_row_verdict_and_head(tmp_path: Path) -> None:
    shell = MergeShell([], MERGE_DOC)
    shell.watch = lambda session: iter(['{"blockId":"merge-prs","schemaVersion":1,"type":"triage.decided","verdicts":{"202":{"verdict":"approved","head":"413b38e"},"merge-row-101":{"verdict":"rejected"}}}\n'])
    run(tmp_path, shell)

    assert [attrs(post) for post in shell.posts] == [
        {"event": "triage.decided", "session": SESSION, "block": "merge-prs", "row": "202", "verdict": "approved", "head": "413b38e"},
        {"event": "triage.decided", "session": SESSION, "block": "merge-prs", "row": "merge-row-101", "verdict": "rejected", "head": "abc1234"},
    ]
    assert [[arg for arg in post if arg.startswith("--pr=")] for post in shell.posts] == [["--pr=202"], ["--pr=101"]]
    assert sorted(shell.spawned) == ["apply_writes", "keep_snapshot"]


def flags_of(argv: list[str]) -> dict[str, str]:
    found, tokens = {}, iter(argv[1:])
    for token in tokens:
        if "=" not in token:
            found[token] = next(tokens)
    return found


def attrs(argv: list[str]) -> dict[str, str]:
    return dict(value.split("=", 1) for flag, value in zip(argv, argv[1:]) if flag == "--attr")


class BoardShell(RecordedShell):
    def __init__(self, doc: dict | None = None, interactions: dict | None = None, root: str | None = SESSION, writes: list[dict] | None = None, refusals: list[str] | None = None, stored: bool = True) -> None:
        super().__init__([], doc or MERGE_DOC)
        self.interactions = interactions or {}
        self.root = root
        self.pending = writes or []
        self.refusals = refusals or []
        self.stored = stored
        self.records: list[dict] = []
        self.presented: list[tuple[list[str], str | None]] = []

    def outcomes(self, session: str) -> dict | None:
        return {"doc": self.doc, "interactions": self.interactions}

    def root_session(self, drive_name: str) -> str | None:
        return self.root

    def writes(self, drive_name: str, cursor: str):
        assert cursor == f"board-applier-{SESSION}"
        yield from (json.dumps(record) + "\n" for record in self.pending)

    def present(self, argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
        self.presented.append((argv, stdin))
        if argv[1] != "update-block" and self.refusals:
            return subprocess.CompletedProcess(argv, 1, "", self.refusals.pop(0))
        return subprocess.CompletedProcess(argv, 0, "posted 1793.5\n" if argv[1] == "ask" else "carried\n", "")

    def cci(self, argv: list[str]) -> subprocess.CompletedProcess:
        if argv[0] == "tail":
            lane = argv[argv.index("--lane") + 1]
            found = [record for record in self.records if record["lane"] == lane and all(record.get(name) == value for arg in argv if arg.startswith("--") and "=" in arg for name, value in [arg[2:].split("=", 1)])]
            return subprocess.CompletedProcess(argv, 0, "".join(json.dumps(record) + "\n" for record in found if record["seq"] > int(argv[argv.index("--since-seq" if "--since-seq" in argv else "--since") + 1])), "")
        self.posts.append(argv)
        if self.stored and argv[0] == "post":
            flags = flags_of(argv)
            self.records.append({"seq": len(self.records) + 1, "drive": flags["--drive"], "lane": flags["--lane"], "kind": flags["--kind"], "topic": flags.get("--topic"), "at": "2026-10-30T07:00:00Z", "text": flags["--text"], "refs": {}, "fields": {"attrs": attrs(argv)}})
        return subprocess.CompletedProcess(argv, 0, "#1\n", "")


SNAPSHOT_DOC = {
    "blocks": [
        {"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "prs": [{"number": 202, "head": "413b38e", "textAt": "2026-10-30T06:00:00Z"}]}]},
        {"id": "merge:303:abcdef1", "type": "card", "children": []},
        {"id": "card-eviction", "type": "card", "children": [{"id": "eviction-route-choice", "type": "choice"}, {"id": "eviction-pr", "type": "pr", "number": 401}]},
    ]
}


def test_the_root_boards_watch_posts_what_the_board_shows_once_until_it_changes(tmp_path: Path) -> None:
    shell = BoardShell(SNAPSHOT_DOC)
    bus, posted = board_watch.Bus("brook", 0), {}
    board_watch.post_snapshot(shell, bus, SESSION, posted)
    board_watch.post_snapshot(shell, bus, SESSION, posted)

    [post] = shell.posts
    assert (post[post.index("--kind") + 1], post[post.index("--topic") + 1], attrs(post)["session"]) == ("note", "board-rows", SESSION)
    assert json.loads(attrs(post)["board"]) == {"blocks": ["merge-prs"], "rows": [["202", "413b38e", "2026-10-30T06:00:00Z"]], "merges": [["303", "abcdef1"]], "cards": [["merge:303:abcdef1", [], []], ["card-eviction", ["eviction-route-choice"], ["401"]]]}
    assert board_watch.board_rows.snapshot_of_records(shell.records) == json.loads(attrs(post)["board"]) | {"session": SESSION}


class Reexecuted(Exception):
    pass


class StaleShell(BoardShell):
    def stale(self) -> bool:
        return True

    def reexec(self) -> None:
        raise Reexecuted


def test_a_watch_older_than_the_installed_plugin_reexecutes_before_it_posts_a_snapshot() -> None:
    shell, stop = StaleShell(SNAPSHOT_DOC), threading.Event()
    stop.set()

    with pytest.raises(Reexecuted):
        board_watch.keep_snapshot(shell, board_watch.Bus("brook", 0), SESSION, stop)

    assert shell.posts == []


def test_a_reexecuted_watch_ends_the_streams_its_older_image_started(tmp_path: Path) -> None:
    from test_owned_reexec import board_reexec

    board_reexec(tmp_path)


def test_a_board_that_is_not_the_roots_newest_posts_no_snapshot(tmp_path: Path) -> None:
    shell = BoardShell(SNAPSHOT_DOC, root="newer-board")
    board_watch.post_snapshot(shell, board_watch.Bus("brook", 0), SESSION, {})

    assert shell.posts == []


def write(seq: int, **fields: str) -> dict:
    return {"seq": seq, "drive": "brook", "lane": "desk-runner", "kind": "note", "topic": "board-write", "text": "carry", "fields": {"attrs": {"session": SESSION, **fields}}}


def test_the_applier_carries_a_tap_reopening_a_closed_round_and_answers_each_write_once(tmp_path: Path) -> None:
    carry = {"op": "carry", "block": "merge-prs", "row": "202", "head": "5555555aaa", "note": "patch-identical restack"}
    shell = BoardShell(SNAPSHOT_DOC, writes=[write(7, **carry), write(8, **carry, session="other-board"), write(9, op="ask", card='{"id": "merge:303:abcdef1"}')], refusals=['cc-present: block "merge-prs" belongs to closed round 31'])
    board_watch.apply_writes(shell, board_watch.Bus("brook", 0), SESSION)

    carry_argv = ["cc-present", "verdict", "carry", "--session", SESSION, "--block", "merge-prs", "--row", "202", "--head", "5555555aaa", "--note", "patch-identical restack"]
    assert [argv for argv, _ in shell.presented] == [carry_argv, ["cc-present", "update-block", "-", "--round", "current", "--session", SESSION], carry_argv, ["cc-present", "ask", "-", "--session", SESSION]]
    assert json.loads(shell.presented[1][1])["id"] == "merge-prs" and shell.presented[3][1] == '{"id": "merge:303:abcdef1"}'
    assert [(post[post.index("--kind") + 1], post[post.index("--re-seq") + 1], post[post.index("--id") + 1]) for post in shell.posts] == [("applied", "7", "applied:7"), ("applied", "9", "applied:9")]


def test_a_carry_cc_present_refuses_is_answered_as_a_defect_to_the_runner_and_root(tmp_path: Path) -> None:
    shell = BoardShell(SNAPSHOT_DOC, writes=[write(7, op="carry", block="merge-prs", row="202", head="5555555aaa")], refusals=["cc-present: no verdict on row 202"])
    board_watch.apply_writes(shell, board_watch.Bus("brook", 0), SESSION)

    [post] = shell.posts
    assert (post[post.index("--kind") + 1], post[post.index("--to") + 1]) == ("defect", "desk-runner,root")
    assert post[post.index("--text") + 1].endswith("failed: cc-present: no verdict on row 202")


TAPS = {
    "triage": {
        "merge-prs": {
            "202": {"verdict": "approved", "head": "413b38e", "at": "2026-10-26T19:02:36Z"},
            "303": {"verdict": "approved", "head": "9999999", "at": "2026-10-27T02:00:00Z", "carriedFrom": "8888888"},
            "404": {"verdict": "rejected", "head": "7777777", "at": "2026-10-27T03:00:00Z"},
        }
    },
    "choices": {"eviction-route-choice": {"optionIds": ["eviction-route-app"]}, "unanswered-choice": {}},
}


def test_backfill_records_every_verdict_and_pick_the_board_holds_then_verifies_them(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = BoardShell(SNAPSHOT_DOC, interactions=TAPS)
    shell.records.append({"seq": 1, "drive": "brook", "lane": "board-watch", "kind": "owner", "topic": "merge-prs", "at": "2026-10-27T03:00:00Z", "text": "404 rejected", "refs": {}, "fields": {"attrs": {"block": "merge-prs", "row": "404", "verdict": "rejected", "head": "7777777"}}})

    assert board_watch.main(["backfill", "--session", SESSION, "--program", "brook"], shell) == 0

    assert sorted((found["row"], found["verdict"], found["head"], found["at"], found.get("carried_from", "")) for post in shell.posts if (found := attrs(post)).get("row")) == [
        ("202", "approved", "413b38e", "2026-10-26T19:02:36Z", ""),
        ("303", "approved", "9999999", "2026-10-27T02:00:00Z", "8888888"),
    ]
    assert [attrs(post)["picked"] for post in shell.posts if attrs(post).get("event") == "choice.selected"] == ["eviction-route-app"]
    assert {post[post.index("--to") + 1] for post in shell.posts} == {"desk-runner"}
    assert board_watch.board_rows.verdicts_of_records(shell.records)["merge-prs"]["303"]["carriedFrom"] == "8888888"
    assert capsys.readouterr().out.startswith("verified: every one of 3 verdicts")


def test_backfill_exits_1_naming_each_verdict_the_records_still_lack(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = BoardShell(SNAPSHOT_DOC, interactions=TAPS, stored=False)

    assert board_watch.main(["backfill", "--session", SESSION, "--program", "brook"], shell) == 1
    assert capsys.readouterr().out.splitlines() == ["UNRECORDED merge-prs 202 approved at 413b38e", "UNRECORDED merge-prs 303 approved at 9999999", "UNRECORDED merge-prs 404 rejected at 7777777", "UNRECORDED eviction-route-choice pick"]
