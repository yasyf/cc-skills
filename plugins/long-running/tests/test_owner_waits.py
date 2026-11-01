"""owner_waits.py over a recorded board, board-watch snapshots, ledger rows and cci records: what it prints is what the root quotes to the owner."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import owner_waits
import pytest

NOW = datetime(2026, 10, 30, 11, 40, tzinfo=timezone.utc)
DRIVE = "d1"
SESSION = "s-root"
REPO = "Forge-AI/monorepo"
HEAD = "632c4a5aa1b2c3d4e5f60718293a4b5c6d7e8f90"
OLD = "ae169925d2c2dd267d574f8b9138b107f212d4ba"
PARENT = "1111111aa1b2c3d4e5f60718293a4b5c6d7e8f90"


def stamp(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def record(seq: int, lane: str, kind: str, text: str, minutes_ago: float, pr: str = "45507", **extra) -> dict:
    return {"seq": seq, "drive": DRIVE, "lane": lane, "kind": kind, "at": stamp(minutes_ago), "text": text, "to": ["root"], "refs": {"prs": [f"{REPO}#{pr}"]} if pr else {}} | extra


def snapshot(seq: int, minutes_ago: float, rows: list[tuple[str, str]], cards: list[str] = ()) -> dict:
    board = {"blocks": ["merge-prs"], "rows": [[pr, head, ""] for pr, head in rows], "merges": [], "cards": [[card, [], []] for card in cards]}
    return record(seq, "board-watch", "note", "board", minutes_ago, pr="", topic="board-rows", fields={"attrs": {"session": SESSION, "board": json.dumps(board)}})


def doc(*rows: tuple[str, str], cards: tuple[dict, ...] = ()) -> dict:
    groups = [{"id": "g", "title": "Fixes", "prs": [{"number": int(pr), "head": head[:7], "title": f"PR {pr}"} for pr, head in rows]}]
    return {"blocks": [{"id": "sec", "type": "section", "children": [{"id": "merge-prs", "type": "pr-groups", "groups": groups}]}, *cards]}


def card(card_id: str, choice: str, title: str) -> dict:
    return {"id": card_id, "type": "card", "title": title, "children": [{"id": choice, "type": "choice", "prompt": "Pick one", "options": [{"id": "a", "label": "A"}]}]}


def ledger(pr: str, head: str = HEAD, **fields) -> dict:
    return {"pr": pr, "head": head, "lane": "next-red-tests-1", "branch": f"b{pr}", "base": "dev", "test_state": "success"} | fields


class Runner:
    def __init__(self, outcomes: dict, snapshots: list[dict], rows: list[dict], records: list[dict], comments: list[dict] = ()) -> None:
        self.outcomes = outcomes
        self.snapshots = snapshots
        self.rows = rows
        self.records = records
        self.comments = list(comments)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        if argv[:2] == ["cc-present", "outcomes"]:
            return done(json.dumps(self.outcomes))
        if argv[:2] == ["cci", "tail"]:
            if "--lane" in argv:
                return done("".join(json.dumps(found) + "\n" for found in self.snapshots))
            return done("".join(json.dumps(found) + "\n" for found in self.records))
        if argv[:3] == ["ccx", "vcs", "gh"]:
            return done(json.dumps({"comments": self.comments}))
        if Path(argv[1]).name == "ledger.py":
            return done(json.dumps(self.rows))
        raise AssertionError(f"unexpected command {argv}")


def done(out: str, code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, out, "")


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    state = tmp_path / "state"
    state.mkdir()
    (state / "runner.json").write_text(json.dumps({"drive": DRIVE, "landing": {"repo": REPO, "ledger": "abc", "checkout": str(tmp_path), "fidelity_lane": ["fidelity-pipeline", "fidelity-desk"]}}))
    entry = home / "long-running/drives/d1.json"
    entry.parent.mkdir(parents=True)
    entry.write_text(json.dumps({"drive": "d1", "cci_drive": DRIVE, "sessions": ["s-old", SESSION], "state_dir": str(state)}))
    return state


def printed(capsys: pytest.CaptureFixture[str], runner: Runner, *argv: str) -> str:
    assert owner_waits.main(["--drive", DRIVE, *argv], runner) == 0
    return capsys.readouterr().out.strip()


def test_it_lists_exactly_the_rows_and_picks_awaiting_the_owner_with_when_each_went_up(registry, capsys):
    outcomes = {
        "doc": doc(("45541", HEAD), ("45339", OLD), cards=(card("card-routing", "routing-choice", "Land the four routing PRs in this order?"), card("card-done", "done-choice", "Answered already"))),
        "interactions": {"triage": {"merge-prs": {"45339": {"verdict": "approved", "head": OLD[:7], "at": stamp(5)}}}, "choices": {"done-choice": {"optionIds": ["a"]}}},
    }
    snapshots = [snapshot(1, 90, []), snapshot(2, 50, [("45541", HEAD)], ["card-routing"]), snapshot(3, 30, [("45541", HEAD), ("45339", OLD)], ["card-routing", "card-done"])]

    out = printed(capsys, Runner(outcomes, snapshots, [], []))

    assert out.splitlines() == [
        "tap merge-prs #45541 at 632c4a5, up since 3:50 AM",
        "pick card-routing routing-choice: Land the four routing PRs in this order?, up since 3:50 AM",
    ]


def test_an_empty_board_says_nothing_waits_on_him(registry, capsys):
    assert printed(capsys, Runner({"doc": doc(), "interactions": {}}, [], [], [])) == owner_waits.NOTHING


def test_a_pr_with_no_row_names_its_unanswered_missing_grade_instead_of_a_tap(registry, capsys):
    records = [
        record(10, "next-red-tests-1", "head", f"HEAD #45507 {HEAD}", 120),
        record(11, "fidelity-desk", "review", f"fidelity: missing {HEAD} exception/E12 restores GET content without the 180 MiB cap", 98),
        record(12, "finder-desk", "review", f"finder: ok {HEAD} no majors", 97),
    ]

    out = printed(capsys, Runner({"doc": doc(), "interactions": {}}, [], [ledger("45507")], records), "--pr", "45507")

    assert out == "#45507 at 632c4a5: not waiting on you: the board holds no row for it; its fidelity grade by fidelity-desk reads missing since 3:02 AM (cci #11), unanswered"


def test_an_answer_by_lane_record_or_human_comment_is_named_with_no_regrade_and_a_bot_comment_answers_nothing(registry, capsys):
    grade = record(11, "fidelity-desk", "review", f"fidelity: missing {HEAD} exception/E12", 98)
    bot = {"author": {"login": "forge-pr-reviewer[bot]"}, "createdAt": stamp(90), "body": "Held"}
    human = {"author": {"login": "yasyf"}, "createdAt": stamp(81), "body": "Why the plan holds"}
    by_comment = Runner({"doc": doc(), "interactions": {}}, [], [ledger("45507")], [grade], [bot, human])
    by_record = Runner({"doc": doc(), "interactions": {}}, [], [ledger("45507")], [grade, record(12, "next-red-tests-1", "report", "the cap lives in the relay", 70, re=11)], [bot])

    assert printed(capsys, by_comment, "--pr", "45507").endswith("reads missing since 3:02 AM (cci #11); a PR comment by yasyf answered it at 3:19 AM and no grade has followed")
    assert printed(capsys, by_record, "--pr", "45507").endswith("reads missing since 3:02 AM (cci #11); next-red-tests-1 in cci #12 answered it at 3:30 AM and no grade has followed")


def test_a_row_at_its_head_is_the_only_tap_wait_and_one_at_an_older_head_is_not(registry, capsys):
    rows = [ledger("45507"), ledger("45541")]
    outcomes = {"doc": doc(("45507", OLD), ("45541", HEAD)), "interactions": {}}
    records = [record(11, "fidelity-desk", "review", f"fidelity: ok {HEAD}", 60), record(12, "fidelity-desk", "review", f"fidelity: ok {HEAD}", 60, pr="45541")]

    out = printed(capsys, Runner(outcomes, [snapshot(1, 20, [("45507", OLD), ("45541", HEAD)])], rows, records), "--pr", "45507", "--pr", "45541", "--json")

    assert [(found["pr"], found["waiting"], found["line"]) for found in json.loads(out)] == [
        ("45507", False, "#45507 at 632c4a5: not waiting on you: its board row names ae16992, not its head"),
        ("45541", True, "#45541 at 632c4a5: waits on your tap on merge-prs, up since 4:20 AM"),
    ]


def test_a_red_build_and_an_open_parent_are_named_and_his_approval_is_no_wait(registry, capsys):
    rows = [ledger("45521", PARENT), ledger("45524", HEAD, base="b45521", test_state="failure", red_at=stamp(40))]
    outcomes = {"doc": doc(("45524", HEAD)), "interactions": {"triage": {"merge-prs": {"45524": {"verdict": "approved", "head": HEAD[:7], "at": stamp(10)}}}}}
    records = [record(11, "fidelity-desk", "review", f"fidelity: ok {HEAD}", 60, pr="45524")]

    out = printed(capsys, Runner(outcomes, [], rows, records), "--pr", "45524")

    assert out == "#45524 at 632c4a5: not waiting on you: you approved it at 632c4a5 at 4:30 AM; its build is red since 4:00 AM; it is stacked on open #45521"


def test_a_missing_grade_root_closed_or_a_desk_ok_outranks_holds_nothing(registry, capsys):
    pipeline = record(11, "fidelity-pipeline", "review", f"fidelity: missing {HEAD} exception/E12", 98)
    desk_ok = record(12, "fidelity-desk", "review", f"fidelity: ok {HEAD}", 90)
    desk_missing = record(13, "fidelity-desk", "review", f"fidelity: missing {HEAD} exception/E14", 80)
    closed = record(14, "root", "decide", "accept the E14 gap on #45507; the plan holds", 70, resolves=[13])

    outranked = Runner({"doc": doc(), "interactions": {}}, [], [ledger("45507")], [pipeline, desk_ok])
    accepted = Runner({"doc": doc(), "interactions": {}}, [], [ledger("45507")], [pipeline, desk_missing, closed])

    assert printed(capsys, outranked, "--pr", "45507") == "#45507 at 632c4a5: not waiting on you: the board holds no row for it"
    assert printed(capsys, accepted, "--pr", "45507") == "#45507 at 632c4a5: not waiting on you: the board holds no row for it; its fidelity grade by fidelity-pipeline reads missing since 3:02 AM (cci #11), unanswered"


@pytest.mark.parametrize(
    "text",
    [f"fidelity-override 45507 at {HEAD[:7]}: 174488", f"fidelity-override #45507 at {HEAD[:7]}: 174488@f3c89519", f"9:38 AM (a). The caller trace counts. fidelity-override #45507 at {HEAD[:7]}: 11."],
    ids=["bare-pr", "id-at-origin", "mid-text-drive-copy"],
)
def test_a_missing_grade_root_overrode_at_the_head_holds_nothing(registry, capsys, text):
    """Root #286877 posted `fidelity-override #47225 at 1f99697: 174488`, and the stall watch kept raising the missing grade it overrode."""
    pipeline = record(11, "fidelity-pipeline", "review", f"fidelity: missing {HEAD} plan:116 no caller trace", 98)
    overridden = record(14, "root", "decide", text, 70)
    stale = record(15, "root", "decide", "fidelity-override #45507 at ae16992: 11", 60)

    assert printed(capsys, Runner({"doc": doc(), "interactions": {}}, [], [ledger("45507")], [pipeline, overridden]), "--pr", "45507") == "#45507 at 632c4a5: not waiting on you: the board holds no row for it"
    assert printed(capsys, Runner({"doc": doc(), "interactions": {}}, [], [ledger("45507")], [pipeline, stale]), "--pr", "45507").endswith("reads missing since 3:02 AM (cci #11), unanswered")


@pytest.mark.parametrize(
    "settled",
    [
        {"text": f"finder-override #45507 at {HEAD[:7]}: 164915. Grounds: an edge case nobody hits"},
        {"text": f"finder-override 45507 at {HEAD[:7]}: 283952 (finder 164915)"},
        {"text": f"finder-override #45507 at {HEAD[:7]}: 164915@f3c89519"},
        {"text": f"finder-override #45507 at {HEAD[:7]}: 30"},
        {"text": "the race costs one reopen", "resolves": 164915},
        {"text": "the race costs one reopen", "resolves": 30},
    ],
    ids=["override-text", "override-bare-pr", "override-id-at-origin", "override-drive-copy", "resolves-finder-desk-seq", "resolves-drive-copy"],
)
def test_a_finder_major_root_settled_by_either_seq_is_no_wait(registry, capsys, settled):
    major = record(30, "finder-desk", "review", f"finder: major {HEAD} 1 open over delta: the release races an explicit-id create", 20, origin_seq=164915, num="#164915@f3c89519")
    records = [
        record(10, "next-red-tests-1", "head", f"HEAD #45507 {HEAD}", 120),
        record(11, "fidelity-desk", "review", f"fidelity: ok {HEAD} matches the plan", 98),
        major,
        record(31, "root", "decide", settled["text"], 10, **{key: value for key, value in settled.items() if key != "text"}),
    ]

    out = printed(capsys, Runner({"doc": doc(), "interactions": {}}, [], [ledger("45507")], records), "--pr", "45507")

    assert out == "#45507 at 632c4a5: not waiting on you: the board holds no row for it"


def test_an_unsettled_finder_major_still_holds_the_pr(registry, capsys):
    records = [
        record(10, "next-red-tests-1", "head", f"HEAD #45507 {HEAD}", 120),
        record(11, "fidelity-desk", "review", f"fidelity: ok {HEAD} matches the plan", 98),
        record(30, "finder-desk", "review", f"finder: major {HEAD} 1 open over delta: the release races an explicit-id create", 20, origin_seq=164915),
        record(31, "root", "decide", "finder-override #45507 at ae16992: 164915", 10),
    ]

    out = printed(capsys, Runner({"doc": doc(), "interactions": {}}, [], [ledger("45507")], records), "--pr", "45507")

    assert out == "#45507 at 632c4a5: not waiting on you: the board holds no row for it; the finder found majors at 632c4a5 (cci #30)"
