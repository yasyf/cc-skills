"""board_watch.py over a recorded cc-present stream: every owner interaction becomes a capture and one bus record, and each event line is kept verbatim."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import board_watch
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "board_watch"
SESSION = "board-1"
ARRIVED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
CONFIRMED = "download the update in the background, show the banner, and only restart when i click it"


def answer_kind(event: dict) -> str:
    match event["type"]:
        case "choice.selected":
            return "free-text" if event.get("other") else "structured" if event.get("optionIds") else "none"
        case "decision.created":
            return "noted" if event.get("note") else "structured"
        case "triage.decided":
            return "noted" if any(row.get("note") for row in event["verdicts"].values()) else "structured"
        case "pack.interaction":
            return "structured" if event.get("payload") else "none"
        case "submit":
            return "none"
    return "free-text"


class RecordedShell(board_watch.Shell):
    def __init__(self, bus: list[dict] | None = None, doc: dict | None = None, refused: bool = False) -> None:
        super().__init__()
        self.bus = bus or []
        self.doc = doc or json.loads((FIXTURES / "outcomes.json").read_text())["doc"]
        self.refused = refused
        self.posts: list[list[str]] = []
        self.tasks: list[tuple[str, list[str]]] = []
        self.rows: list[dict] = []
        self.topics: dict[str, dict[str, str]] = {}
        self.answers: dict[str, list[dict]] = {}
        self.root = SESSION
        self.seq = ""
        self.ledger: list[tuple[list[str], str]] = []
        self.rechecks: list[list[str]] = []
        self.wakes: list[list[str]] = []
        self.classifies = True

    def lr(self, argv: list[str], stdin: str) -> subprocess.CompletedProcess:
        if argv[:2] == ["board", "classify"]:
            return subprocess.CompletedProcess(argv, 0 if self.classifies else 1, answer_kind(json.loads(stdin)) + "\n", "" if self.classifies else "unknown command")
        if argv[:3] == ["board", "cards", "recheck"]:
            self.rechecks.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["board", "wake"]:
            self.wakes.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        self.ledger.append((argv, stdin))
        return subprocess.CompletedProcess(argv, 0, "m1\n", "")

    def watch(self, session: str):
        assert session == SESSION
        yield from (FIXTURES / "stream.jsonl").read_text().splitlines(keepends=True)

    def document(self, session: str, block: str) -> dict | None:
        return self.doc

    def shown(self, session: str, block: str) -> dict | None:
        return {"doc": self.doc, "onBoard": True, "answers": self.answers.get(block, [])}

    def outcomes(self, session: str) -> dict | None:
        return {"doc": self.doc, "interactions": {}}

    def spawn(self, target, *args) -> None:
        self.spawned = [*getattr(self, "spawned", []), target.__name__]

    def cci(self, argv: list[str]) -> subprocess.CompletedProcess:
        if argv[0] == "grep":
            return subprocess.CompletedProcess(argv, 0, "".join(json.dumps(record) + "\n" for record in self.bus), "")
        self.posts.append(argv)
        return subprocess.CompletedProcess(argv, 1 if self.refused else 0, self.seq, "refused" if self.refused else "")

    def now(self) -> datetime:
        return ARRIVED

    def root_session(self, drive_name: str) -> str | None:
        return self.root

    def entry(self, drive_name: str) -> dict | None:
        return {"drive": drive_name, "program": drive_name, "checkout": "/checkouts/brook", "sessions": [SESSION]}

    def ccn(self, checkout: str, argv: list[str]) -> subprocess.CompletedProcess:
        if argv[:2] == ["ledger", "list"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps([{"id": "vocabulary", "updated_at": "2026-11-03T12:00:00Z"}] if self.topics else []), "")
        if argv[:2] == ["ledger", "show"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"rows": [{"key": key, "fields": fields} for key, fields in self.topics.items()]}), "")
        if argv[:2] == ["answer", "list"]:
            wanted = {value for flag, value in zip(argv, argv[1:]) if flag == "--label"}
            return subprocess.CompletedProcess(argv, 0, json.dumps([row for row in self.rows if wanted <= set(row["tags"])]), "")
        if argv[:2] == ["answer", "add"]:
            self.rows.append({"checkout": checkout, "title": argv[2], "body": argv[argv.index("--body") + 1], "tags": [value for flag, value in zip(argv, argv[1:]) if flag == "--label"], "dirs": [value for flag, value in zip(argv, argv[1:]) if flag == "--dir"]})
            return subprocess.CompletedProcess(argv, 0, "{}", "")
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


def test_a_proof_boards_taps_post_no_owner_record_and_no_capture(tmp_path: Path) -> None:
    shell = RecordedShell(doc=json.loads((FIXTURES / "outcomes.json").read_text())["doc"] | {"proof": True})

    assert run(tmp_path, shell) == []

    assert shell.posts == []
    assert shell.rows == []
    assert shell.ledger == []
    assert (tmp_path / "boards" / f"{SESSION}-events.jsonl").read_text() == (FIXTURES / "stream.jsonl").read_text()


def test_the_same_taps_on_an_unmarked_board_post_owner_records(tmp_path: Path) -> None:
    shell = RecordedShell()

    run(tmp_path, shell)

    assert shell.posted(board_watch.BUS_LANE)


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


def test_a_watch_a_sessions_hook_starts_runs_as_board_watch_and_not_as_that_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_LONG_RUNNING_LANE", "root")
    monkeypatch.setenv("CCI_AGENT_ID", "aroot-0c1d")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)

    run(tmp_path)

    assert (os.environ["CLAUDE_LONG_RUNNING_LANE"], os.environ["CCI_AGENT_ID"], os.environ["CLAUDE_CODE_SESSION_ID"]) == ("board-watch", "", "")


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
        f"{SHARE}: wrote \u201conly for the team, and only read-only\u201d",
        f"{SHARE}: picked \u201cYes, read-only\u201d",
        "Owner on board board-1, board-notes: wrote \u201calso check the share menu\u201d",
        f"{SHARE}: noted \u201csay who can open it\u201d",
        f"Owner on board board-1, Confirm your words as written? (update-confirm-ok): approved \u201c{CONFIRMED}\u201d",
        "Owner on board board-1, banner-ok: rejected noted \u201ckeep the old banner text\u201d",
        f"{SHARE}: picked \u201cYes, read-only\u201d",
    ]
    assert {tuple(shell.posted(flag)) for flag in ("--kind", "--lane", "--drive")} == {("owner",) * 7, ("board-watch",) * 7, ("brook",) * 7}
    assert shell.posted("--to") == ["owner-relay", "root", "owner-relay", "owner-relay", "root", "root,owner-relay", "root"]


GROUP_DOC = {"blocks": [{"id": "card-fixes", "type": "card", "title": "Tooling fixes the watchers propose", "children": [{"id": "fix-rows", "type": "choice", "prompt": "9. Release rows read the release itself", "options": [{"id": "fix-rows-build", "label": "Build"}]}, {"id": "fix-base", "type": "choice", "prompt": "2. Stale bases are fixed automatically", "options": [{"id": "fix-base-build", "label": "Build"}]}]}]}


def test_two_picks_on_one_card_of_many_questions_each_name_their_own_question() -> None:
    """Owner 10:21 PM Nov 1: eight identical "Tooling fixes ...: Build" rows hid eight separate picks; each names its own question."""
    texts = [board_watch.announced({"type": "choice.selected", "blockId": block, "optionIds": [f"{block}-build"]}, "board-1", GROUP_DOC) for block in ("fix-rows", "fix-base")]

    assert texts == [
        "Owner on board board-1, Tooling fixes the watchers propose, 9. Release rows read the release itself (fix-rows): picked \u201cBuild\u201d",
        "Owner on board board-1, Tooling fixes the watchers propose, 2. Stale bases are fixed automatically (fix-base): picked \u201cBuild\u201d",
    ]


def test_a_pick_names_the_head_its_card_asked_about() -> None:
    """A pick on a card whose PR moved names the head it was made on, so no reader counts it for the new head."""
    event = {"type": "choice.selected", "blockId": "fix-rows", "optionIds": ["fix-rows-build"], "heads": {"org/repo#48855": "abc1234"}}

    assert board_watch.announced(event, "board-1", GROUP_DOC) == (
        "Owner on board board-1, Tooling fixes the watchers propose, 9. Release rows read the release itself (fix-rows): picked \u201cBuild\u201d at org/repo#48855 abc1234"
    )


def test_a_record_of_his_answer_names_the_card_it_answers_and_a_submit_names_none(tmp_path: Path) -> None:
    shell = RecordedShell(doc=GROUP_DOC)
    shell.watch = lambda session: iter(['{"blockId":"fix-rows","optionIds":["fix-rows-build"],"schemaVersion":1,"type":"choice.selected"}\n', SUBMIT])
    run(tmp_path, shell)

    owner = [post for post in shell.posts if post[post.index("--kind") + 1] == "owner"]
    assert [(attrs(post)["event"], attrs(post).get("card")) for post in owner] == [("choice.selected", "card-fixes"), ("submit", None)]


BUILT_DOC = {"blocks": [{"id": "card-rmw5myh8a", "type": "card", "title": "A chat killed mid-create never reopens: which fix?", "children": [{"id": "rmw5myh8a-choice", "type": "choice", "options": [
    {"id": "rmw5myh8a-1", "label": "Retire it behind a tombstone", "recommended": True, "build": {"repo": "Forge-AI/monorepo", "branch": "redo-epoch-reopen"}},
    {"id": "rmw5myh8a-2", "label": "Leave it Creating until the next boot"},
]}]}]}


def test_a_pick_of_the_option_a_branch_builds_names_that_branch_and_no_other_pick_does(tmp_path: Path) -> None:
    shell = RecordedShell(doc=BUILT_DOC)
    shell.watch = lambda session: iter([
        '{"blockId":"rmw5myh8a-choice","optionIds":["rmw5myh8a-1"],"heads":{"Forge-AI/monorepo#52060":"03d865a3a4ec4cf119f5e90be37aa7804cdfa315"},"schemaVersion":1,"type":"choice.selected"}\n',
        '{"blockId":"rmw5myh8a-choice","optionIds":["rmw5myh8a-2"],"schemaVersion":1,"type":"choice.selected"}\n',
    ])
    run(tmp_path, shell)

    picks = [attrs(post) for post in shell.posts if attrs(post).get("event") == "choice.selected"]
    assert [(pick["picked"], pick.get("built", ""), pick.get("heads", "")) for pick in picks] == [
        ("rmw5myh8a-1", "Forge-AI/monorepo:redo-epoch-reopen", "Forge-AI/monorepo#52060=03d865a3a4ec4cf119f5e90be37aa7804cdfa315"),
        ("rmw5myh8a-2", "", ""),
    ]


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
        "Owner on board board-1, Merge these? (merge-triage): merge-row-101 approved at abc1234",
        "Owner on board board-1, merge-prs: 202 rejected at 413b38e noted \u201csplit it\u201d",
    ]
    assert shell.posted("--resolves-seq") == ["11", "12"]
    assert [[arg for arg in post if arg.startswith("--pr=")] for post in shell.posts] == [["--pr=101"], ["--pr=202"]]


def test_a_tap_resolves_open_asks_naming_its_block_or_card_but_not_a_longer_id(tmp_path: Path) -> None:
    bus = [ask(21, "parked on share-open"), ask(22, "share-open-pick needs his word", kind="blocker"), ask(23, "parked on share-open-2")]
    shell = RecordedShell(bus)
    run(tmp_path, shell)

    assert shell.posted("--resolves-seq") == ["21,22"] * 2


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_each_answer_wakes_the_lane_that_asked_and_never_posts_a_needs_lane_to_root(tmp_path: Path) -> None:
    """Owner order 408821: a lane parked on his pick sat 50 minutes because the pick reached only the coordinator."""
    shell = RecordedShell()
    shell.seq = "#77"
    run(tmp_path, shell)

    assert shell.posted("--to", "ask") == []
    assert shell.tasks == []
    picks = [argv for argv in shell.wakes if argv[2] != "late" and flag(argv, "--block") == "share-open-pick"]
    assert len(picks) >= 2
    assert (flag(picks[0], "--drive"), flag(picks[0], "--session"), flag(picks[0], "--lane"), flag(picks[0], "--owner-seq")) == ("brook", SESSION, "board-watch", "77")
    assert any("\u201cYes, read-only\u201d" in flag(argv, "--said") for argv in picks)
    assert flag(picks[0], "--key").startswith(f"board:{SESSION}:")


@pytest.mark.parametrize(("root", "unclaimed"), [(SESSION, True), ("lane-board", False)])
def test_only_a_structured_pick_on_the_roots_board_may_ask_meta_desk_for_a_lane(tmp_path: Path, root: str, unclaimed: bool) -> None:
    shell = streamed(tmp_path, TAP, root=root)

    assert ["--unclaimed" in argv for argv in shell.wakes] == [unclaimed]


def test_a_write_in_wakes_the_asking_lane_but_never_asks_for_a_new_one(tmp_path: Path) -> None:
    shell = streamed(tmp_path, WRITE_IN)

    assert ["--unclaimed" in argv for argv in shell.wakes] == [False]
    assert "only for the team" in flag(shell.wakes[0], "--said")


def test_a_submit_answers_nothing_so_it_wakes_nobody(tmp_path: Path) -> None:
    assert streamed(tmp_path, SUBMIT).wakes == []


def test_a_wake_that_fails_leaves_the_answer_recorded(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    class Refused(RecordedShell):
        def lr(self, argv: list[str], stdin: str) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(argv, 1, "", "no such verb") if argv[:2] == ["board", "wake"] else super().lr(argv, stdin)

    shell = Refused()
    shell.watch = lambda session: iter([TAP])
    run(tmp_path, shell)

    assert shell.posted("--kind") == ["owner"]
    assert "was not woken: no such verb" in capsys.readouterr().out


@pytest.mark.parametrize(("held", "stale"), [(None, False), (Path("/cache/long-running/0.7.1"), False), (Path("/cache/long-running/0.7.9"), True)])
def test_the_snapshot_pass_reads_the_installed_version_from_the_install_link(monkeypatch: pytest.MonkeyPatch, held: Path | None, stale: bool) -> None:
    """Deleting drive.installed_version left this one caller, whose AttributeError ended the snapshot thread on its first pass."""
    monkeypatch.setattr(board_watch.drive.release, "held", lambda: held)
    monkeypatch.setattr(board_watch.drive, "plugin_manifest", lambda: {"version": "0.7.1"})

    assert board_watch.Shell().stale() is stale


def test_each_snapshot_pass_escalates_late_wakes_through_lr(tmp_path: Path) -> None:
    shell = RecordedShell()
    board_watch.wake_late(shell, board_watch.Bus("brook", 0), SESSION)

    assert shell.wakes == [["board", "wake", "late", "--drive", "brook", "--session", SESSION, "--lane", "board-watch"]]


TAP = '{"blockId":"share-open-pick","optionIds":["yes"],"schemaVersion":1,"type":"choice.selected"}\n'
WRITE_IN = '{"blockId":"share-open-pick","optionIds":[],"other":"only for the team","schemaVersion":1,"type":"choice.selected"}\n'
NOTED_VERDICT = '{"blockId":"banner-ok","note":"keep the old banner text","schemaVersion":1,"type":"decision.created","verdict":"rejected"}\n'
SUBMIT = '{"revision":3,"schemaVersion":1,"type":"submit"}\n'
QUESTION = "Owner on board board-1, Should a shared note open in the browser? (share-open-pick)"


def streamed(tmp_path: Path, line: str, bus: list[dict] | None = None, **attributes: object) -> RecordedShell:
    shell = RecordedShell(bus)
    for name, value in attributes.items():
        setattr(shell, name, value)
    shell.watch = lambda session: iter([line])
    run(tmp_path, shell)
    return shell


def test_a_structured_tap_reaches_root_and_not_owner_relay_as_an_owner_record_with_a_closed_row(tmp_path: Path) -> None:
    shell = streamed(tmp_path, TAP, [ask(21, "parked on share-open-pick", lane="api-lane")], seq="#77\n")

    assert (shell.posted("--to"), shell.posted("--kind"), shell.posted("--resolves-seq")) == (["root"], ["owner"], ["21"])
    assert attrs(shell.posts[0])["answer"] == "structured"
    assert shell.ledger == [(["owner", "add", "--drive", "brook", "--state", "addressed", "--routed", "77"], f"{QUESTION}: picked \u201cYes, read-only\u201d")]


def test_ten_taps_reach_owner_relay_as_one_summary_line_and_the_eleventh_waits_for_its_batch(tmp_path: Path) -> None:
    shell = RecordedShell()
    shell.watch = lambda session: iter([TAP] * 11)
    run(tmp_path, shell)

    assert shell.posted("--to") == ["root"] * 11
    assert (shell.posted("--to", "note"), shell.posted("--topic", "note")) == (["owner-relay"], ["tap-batch"])
    assert shell.posted("--text", "note") == ["The owner made 10 taps on board board-1, each already with the coordinator: " + "; ".join([f"{QUESTION.removeprefix('Owner on board board-1, ')}: picked \u201cYes, read-only\u201d"] * 10)]
    assert (tmp_path / "boards" / f"{SESSION}-taps.jsonl").read_text().count("\n") == 1


def test_a_write_in_reaches_owner_relay_alone_resolves_nothing_and_opens_a_row(tmp_path: Path) -> None:
    shell = streamed(tmp_path, WRITE_IN, [ask(21, "parked on share-open-pick", lane="api-lane")], seq="#77\n")

    assert (shell.posted("--to"), shell.posted("--resolves-seq"), shell.posted("--topic", "ask")) == (["owner-relay"], [], [])
    assert attrs(shell.posts[0])["answer"] == "free-text"
    assert shell.ledger == [(["owner", "received", "--drive", "brook", "--routed", "77"], f"{QUESTION}: wrote \u201conly for the team\u201d")]


def test_a_verdict_with_a_note_acts_at_once_and_still_opens_a_row_for_the_note(tmp_path: Path) -> None:
    shell = streamed(tmp_path, NOTED_VERDICT, [ask(21, "parked on banner-ok")])

    assert (shell.posted("--to"), shell.posted("--resolves-seq")) == (["root,owner-relay"], ["21"])
    assert [argv[1] for argv, _ in shell.ledger] == ["received"]


def test_a_submit_reaches_root_and_adds_no_row(tmp_path: Path) -> None:
    shell = streamed(tmp_path, SUBMIT)

    assert (shell.posted("--to"), shell.ledger, shell.rechecks) == (["root"], [], [])


def test_a_pick_rechecks_the_cards_resting_on_the_card_it_answers_the_same_event(tmp_path: Path) -> None:
    shell = streamed(tmp_path, TAP)
    event = json.loads(TAP)
    card = board_watch.card_of(shell.doc, event["blockId"])["id"]

    assert shell.rechecks == [["board", "cards", "recheck", "--drive", "brook", "--session", SESSION, "--lane", board_watch.BUS_LANE, "--pick", card, "--said", board_watch.told(event, shell.doc)]]


def test_an_answer_lr_cannot_classify_goes_to_owner_relay_with_an_open_row(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = streamed(tmp_path, TAP, classifies=False)

    assert shell.posted("--to") == ["owner-relay"]
    assert [argv[1] for argv, _ in shell.ledger] == ["received"]
    assert "lr board classify did not run" in capsys.readouterr().out


def test_a_refused_post_is_reported_and_the_watch_keeps_recording(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    captured = run(tmp_path, RecordedShell(refused=True))

    assert len(captured) == 8
    assert capsys.readouterr().out.count("failed: refused") == 7


def test_a_triage_tap_posts_one_record_per_row_with_its_block_row_verdict_and_head(tmp_path: Path) -> None:
    shell = MergeShell([], MERGE_DOC)
    shell.watch = lambda session: iter(['{"blockId":"merge-prs","schemaVersion":1,"type":"triage.decided","verdicts":{"202":{"verdict":"approved","head":"413b38e"},"merge-row-101":{"verdict":"rejected"}}}\n'])
    run(tmp_path, shell)

    assert [attrs(post) for post in shell.posts] == [
        {"event": "triage.decided", "session": SESSION, "block": "merge-prs", "row": "202", "verdict": "approved", "head": "413b38e", "answer": "structured"},
        {"event": "triage.decided", "session": SESSION, "block": "merge-prs", "row": "merge-row-101", "verdict": "rejected", "head": "abc1234", "answer": "structured"},
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


def test_the_supervisors_carry_stands_as_his_tap_and_reaches_the_board(tmp_path: Path) -> None:
    carry = write(7, op="carry", block="merge-prs", row="202", head="5555555aaa", note="restack") | {"lane": "supervisor", "at": "2026-11-03T09:00:00Z"}
    shell = BoardShell(SNAPSHOT_DOC, writes=[carry])
    board_watch.apply_writes(shell, board_watch.Bus("brook", 0), SESSION)

    assert [argv[:3] for argv, _ in shell.presented] == [["cc-present", "verdict", "carry"]]
    assert board_watch.board_rows.verdicts_of_records([carry])["merge-prs"]["202"]["head"] == "5555555aaa"


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


def test_blanket_approves_the_rows_ready_at_the_owners_word_and_leaves_the_rest_for_his_tap(capsys: pytest.CaptureFixture[str]) -> None:
    shell = BoardShell(SNAPSHOT_DOC)
    def row(number: int, head: str, head_at: str, ready: bool = True, tapped: bool = False) -> dict:
        return {"number": number, "head": head, "head_at": head_at, "ready_for_tap": ready, "tapped": tapped}
    rows = [row(202, "413b38e", "2026-10-30T06:00:00Z"), row(505, "5050505", "2026-10-30T07:30:00Z"), row(606, "6060606", "2026-10-30T06:00:00Z", ready=False), row(707, "7070707", "2026-10-30T06:00:00Z", tapped=True)]
    shell.lr = lambda argv, stdin=None: subprocess.CompletedProcess(argv, 0, json.dumps(rows), "")

    for _ in range(2):
        assert board_watch.main(["blanket", "--session", SESSION, "--word", "312144", "--at", "2026-10-30T07:00:00Z", "--program", "brook"], shell) == 0

    verdicts = board_watch.board_rows.verdicts_of_records(shell.records)["merge-prs"]
    assert {pr: (entry["verdict"], entry["head"], entry["at"]) for pr, entry in verdicts.items()} == {"202": ("approved", "413b38e", "2026-10-30T07:00:00Z")}
    assert [attrs(post)["word"] for post in shell.posts] == ["312144"]
    assert capsys.readouterr().out.splitlines() == ["blanket 312144: approved #202 at 413b38e, the rows ready for his tap with a head from by 2026-10-30T07:00:00Z"] * 2


def test_blanket_stops_when_the_rows_cannot_be_read(capsys: pytest.CaptureFixture[str]) -> None:
    shell = BoardShell(SNAPSHOT_DOC)
    shell.lr = lambda argv, stdin=None: subprocess.CompletedProcess(argv, 1, "", "no store")

    assert board_watch.main(["blanket", "--session", SESSION, "--word", "312144", "--at", "2026-10-30T07:00:00Z", "--program", "brook"], shell) == 1

    assert shell.posts == []
    assert "lr board rows failed: no store" in capsys.readouterr().err


def test_each_owner_answer_gets_a_ccn_answer_row_with_his_words_card_and_time(tmp_path: Path) -> None:
    """Owner 2:25 PM Nov 2: a write-in on a card later removed read as unanswered to two lanes; every OWNER record now writes its ccn answer row at once."""
    shell = RecordedShell()
    run(tmp_path, shell)

    assert [row["tags"][-2].removeprefix("board-block:") for row in shell.rows] == ["share-open-pick", "share-open-pick", "board-notes", "share-open-pick", "update-confirm-ok", "banner-ok", "share-open-pick"]
    first = shell.rows[0]
    assert first["checkout"] == "/checkouts/brook"
    assert first["body"].splitlines()[0] == "wrote \u201conly for the team, and only read-only\u201d"
    assert first["body"].splitlines()[-1].endswith(f"block share-open-pick, board {SESSION}, Jan 1 7:04 PM PT.")
    assert {"from:owner", "board-answer", "program:brook", f"board-session:{SESSION}"} <= set(first["tags"])
    assert len({row["tags"][-1] for row in shell.rows}) == 7


def test_an_answer_row_carries_the_cards_words_so_the_process_guard_can_find_his_tap(tmp_path: Path) -> None:
    shell = RecordedShell()
    doc = {"blocks": [{"id": "card-rec", "type": "card", "title": "May I restart the recorder?", "summary": "meta-desk restarts pid 74452.", "children": [{"id": "rec-choice", "type": "choice", "options": [{"id": "rec-1", "label": "Yes, restart it", "hint": "Ends it now."}]}, {"id": "rec-more", "type": "markdown", "md": "It runs board_watch.py.\nNo answer is lost."}]}]}
    board_watch.register({"type": "choice.selected", "blockId": "rec-choice", "optionIds": ["rec-1"]}, "board:board-1:9:abcd1234", SESSION, doc, shell, "brook")

    body = shell.rows[0]["body"].splitlines()
    assert body[0] == "picked \u201cYes, restart it\u201d"
    assert body[2] == "Card said: meta-desk restarts pid 74452. It runs board_watch.py. No answer is lost. Ends it now."
    assert body[-1].startswith("Card card-rec, block rec-choice")


def test_an_answer_row_carries_the_label_and_dirs_of_each_topic_his_words_or_the_card_name(tmp_path: Path) -> None:
    shell = RecordedShell()
    shell.topics = {"share-links": {"words": r"\bshare (menu|links?)\b", "dirs": '["dashboard/src/iris/share"]'}, "old-chats": {"words": r"\bold chats?\b", "dirs": "[]"}}
    board_watch.register({"type": "feedback.created", "blockId": "board-notes", "text": "also check the share menu"}, "board:board-1:9:abcd1234", SESSION, shell.doc, shell, "brook")

    assert [(row["tags"][0], row["dirs"]) for row in shell.rows] == [("topic:share-links", ["dashboard/src/iris/share"])]
    assert "old-chats" not in " ".join(shell.rows[0]["tags"])


def test_a_pick_on_a_slack_permission_card_labels_its_row_by_the_label_the_board_showed(tmp_path: Path) -> None:
    card = "slack:C0APPFOLLOW:1791859635.513139:0a1b2c3d4e5f"

    def slack_tags(picked: str, options: list[dict]) -> list[str]:
        doc = {"blocks": [{"id": card, "type": "card", "title": "Post this in the #app-followers thread Dana started?", "children": [{"id": f"{card}-answer", "type": "choice", "options": options}]}]}
        shell = RecordedShell(doc=doc)
        board_watch.register({"type": "choice.selected", "blockId": f"{card}-answer", "optionIds": [picked]}, "board:board-1:5:abcd1234", SESSION, doc, shell, "brook")
        return [tag for tag in shell.rows[0]["tags"] if tag.startswith("slack-")]

    shown = [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}]
    assert slack_tags("yes", shown) == ["slack-grant:C0APPFOLLOW/1791859635.513139"]
    assert slack_tags("no", shown) == ["slack-revoke:C0APPFOLLOW/1791859635.513139"]
    assert slack_tags("yes", [{"id": "yes", "label": "No"}, {"id": "no", "label": "Yes"}]) == ["slack-revoke:C0APPFOLLOW/1791859635.513139"]


def test_an_answer_row_is_written_once_per_owner_record(tmp_path: Path) -> None:
    shell = RecordedShell()
    event = {"type": "choice.selected", "blockId": "share-open-pick", "optionIds": [], "other": "only read-only"}
    for _ in range(2):
        board_watch.register(event, "board:board-1:3:abcd1234", SESSION, shell.doc, shell, "brook")

    assert len(shell.rows) == 1


def test_a_pack_pick_reaches_the_bus_and_gets_a_row(tmp_path: Path) -> None:
    shell = RecordedShell()
    shell.watch = lambda session: iter(['{"blockId":"doc-fork","payload":{"fork":"b"},"schemaVersion":1,"type":"pack.interaction"}\n'])
    run(tmp_path, shell)

    assert shell.posted("--text") == ['Owner on board board-1, doc-fork: picked \u201c{"fork":"b"}\u201d']
    assert [argv[2] for argv in shell.wakes] == ["doc-fork"]
    assert [row["body"].splitlines()[0] for row in shell.rows] == ['picked \u201c{"fork":"b"}\u201d']


class CiteShell(RecordedShell):
    def __init__(self, owners: list[dict], cites: list[int]) -> None:
        super().__init__()
        self.owners, self.cites, self.asked = owners, cites, []
        self.answer = subprocess.CompletedProcess([], 0, "".join(f"{seq}\n" for seq in cites), "")

    def lr(self, argv: list[str], stdin: str) -> subprocess.CompletedProcess:
        self.asked.append(argv)
        return self.answer

    def cci(self, argv: list[str]) -> subprocess.CompletedProcess:
        if argv[0] == "tail":
            return subprocess.CompletedProcess(argv, 0, "".join(json.dumps(record) + "\n" for record in self.owners), "")
        self.posts.append(argv)
        return subprocess.CompletedProcess(argv, 0, "#40\n", "")


def pick(seq: int, block: str, minutes: int, event: str = "choice.selected", session: str = SESSION) -> dict:
    at = (ARRIVED - timedelta(minutes=minutes)).isoformat()
    return {"seq": seq, "drive": "brook", "lane": "board-watch", "kind": "owner", "at": at, "text": f"Owner on board {session[:8]}, {block}: picked \u201cYes\u201d, at {at}", "fields": {"attrs": {"event": event, "session": session, "block": block, "picked": "yes"}}}


def test_an_owner_pick_no_record_names_is_reposted_at_30_minutes_and_escalated_at_60_asking_lr_once_a_pass() -> None:
    """Coordinator 2:42 PM Nov 2 (board-audit-1): a pick no lane picks up is re-posted at 30 minutes and goes to the coordinator as a blocker at 60."""
    owners = [pick(5, "q-thirty", 35), pick(6, "q-sixty", 65), pick(7, "q-cited", 65), pick(8, "q-fresh", 10), pick(9, "q-other", 65, session="other-board"), pick(10, "q-pack", 40, event="pack.interaction"), pick(11, "q-seq", 40)]
    shell, reposted = CiteShell(owners, [7, 11]), set()
    bus = board_watch.Bus("brook", 0)
    board_watch.repost_uncited(shell, bus, SESSION, reposted)
    board_watch.repost_uncited(shell, bus, SESSION, reposted)

    assert shell.asked == [
        ["findings", "--drive", "brook", "cited", "5=q-thirty", "6=q-sixty", "7=q-cited", "10=q-pack", "11=q-seq"],
        ["findings", "--drive", "brook", "cited", "7=q-cited", "11=q-seq"],
    ]
    assert [(post[post.index("--kind") + 1], post[post.index("--to") + 1], post[post.index("--id") + 1], post[post.index("--re-seq") + 1]) for post in shell.posts] == [
        ("ask", "meta-desk", "uncited:5", "5"),
        ("blocker", "meta-desk", "uncited-blocker:6", "6"),
        ("ask", "meta-desk", "uncited:10", "10"),
    ]
    assert shell.posts[1][shell.posts[1].index("--text") + 1].startswith("UNCITED owner pick #6 on board board-1: no record names q-sixty 65 minutes after it.")


def test_a_failed_cited_read_reposts_nothing_and_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    shell = CiteShell([pick(5, "q-thirty", 35)], [])
    shell.answer = subprocess.CompletedProcess([], 1, "", "no drive")
    board_watch.repost_uncited(shell, board_watch.Bus("brook", 0), SESSION, set())

    assert shell.posts == []
    assert "lr findings cited failed, so no pick is re-posted this pass: no drive" in capsys.readouterr().out


TIMED = {"type": "choice.selected", "origin": "human", "at": "2026-10-27T02:00:00-07:00", "round": 3, "payload": {"blockId": "eviction-route-choice", "optionIds": ["eviction-route-app"], "type": "choice.selected", "schemaVersion": 1}}


def test_backfill_writes_a_row_for_each_answered_block_that_lacks_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = BoardShell(SNAPSHOT_DOC, interactions={"choices": {"eviction-route-choice": {"optionIds": ["eviction-route-app"]}}})
    shell.answers = {"eviction-route-choice": [TIMED]}

    assert board_watch.main(["backfill", "--session", SESSION, "--program", "brook"], shell) == 0

    [row] = shell.rows
    assert "board-card:card-eviction" in row["tags"] and "board-block:eviction-route-choice" in row["tags"]
    assert row["body"].splitlines()[-1] == f"Card card-eviction, block eviction-route-choice, board {SESSION}, Oct 27 2:00 AM PT."
    assert board_watch.main(["backfill", "--session", SESSION, "--program", "brook"], shell) == 0
    assert len(shell.rows) == 1


def test_a_thread_that_crashes_restarts_the_whole_recorder_instead_of_dying_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    restarted = threading.Event()
    argvs: list[list[str]] = []

    def record(argv: list[str]) -> None:
        argvs.append(argv)
        restarted.set()

    def crash() -> None:
        raise AttributeError("module has no such helper")

    monkeypatch.setenv(board_watch.drive.CALLER_VERSION_ENV, "0")
    monkeypatch.setattr(board_watch.time, "sleep", lambda _: None)
    monkeypatch.setattr(board_watch.drive, "run_installed", lambda *_, **__: None)
    monkeypatch.setattr(board_watch.drive, "reexec", record)

    board_watch.Shell().spawn(crash)

    assert restarted.wait(5)
    assert argvs[0][1:] == board_watch.sys.argv
