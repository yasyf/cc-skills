from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import drive
import overview
import pytest
from conftest import DRIVE, LEDGER, FakeShell

NOW = datetime(2026, 10, 30, 2, 30, tzinfo=UTC)
ROOT_SESSION = "900424b6-7393-480c-a26a-f1bd21da6e57"


def doc() -> dict:
    return {
        "updated": "2026-10-30T02:14:42Z",
        "steps": [
            {"id": "gateway", "label": "Gateway", "title": "Model gateway for new chats", "state": "All 3 landed", "tone": "done", "blocker": None, "next": "Nothing left in this step", "prs": [101, 102, 103], "lanes": ["p8a-step7"], "delivers": "Model calls through the gateway.", "went_wrong": "#103 was replaced by #104, #105 and #106; p8a-step7 handed off twice."},
            {"id": "journal", "label": "Journal", "title": "Mac chat storage", "state": "All 2 landed", "tone": "done", "blocker": None, "next": "Nothing left in this step", "prs": [201, 202], "lanes": [], "delivers": "Every chat saved.", "went_wrong": None},
            {"id": "forks", "label": "Forks", "title": "Forking chats", "state": "1 of 2 landed", "tone": "moving", "blocker": "The conversion waits on its plan check", "next": "Queued when it clears", "unblocking": "The landing desk queues it once the finder passes", "prs": [301, 302], "lanes": ["p8c-ship-6"], "delivers": "Forks in the new app.", "went_wrong": "#301 broke forking and was fixed by #309."},
            {"id": "plan", "label": "Plan", "title": "Release plan", "state": "Every release runs manually", "tone": "moving", "blocker": None, "next": "The cloud release follows", "prs": [], "lanes": [], "delivers": "A gate for every release.", "went_wrong": None},
        ],
        "now": {
            "releases": [
                {"id": "R1", "name": "Old world kept whole", "state": "Live since 4:28 PM", "parts": ["api", "gateway", "app"], "blocker": None, "unblocking": None, "went_wrong": "#42716 broke forking.", "lanes": ["release-5"]},
                {"id": "R2", "name": "New code in the cloud", "state": "Not started", "parts": ["api"], "blocker": "Your approval for the cloud release", "unblocking": "release-5 asked for a time", "went_wrong": None, "lanes": ["release-5", "root"]},
            ]
        },
        "pr_state": {
            "101": {"state": "Landed", "since": "2026-10-29T20:00:00Z", "line": "Landed at 1:00 PM"},
            "102": {"state": "Landed", "since": "2026-10-29T20:00:00Z", "line": "Landed at 1:00 PM"},
            "201": {"state": "Landed", "since": "2026-10-29T20:00:00Z", "line": "Landed at 1:00 PM"},
            "202": {"state": "Grading", "since": "2026-10-30T01:14:00Z", "line": "Being graded since 6:14 PM"},
            "301": {"state": "Landed", "since": "2026-10-30T02:10:00Z", "line": "Landed at 7:10 PM"},
            "302": {"state": "Held", "since": "2026-10-29T22:00:00Z", "line": "Held since 3:00 PM"},
        },
        "history": {
            "pr:301": {"events": [{"ts": "2026-10-30T02:10:00Z", "kind": "landed", "text": "Landed after the finder passed.", "holder": None, "next": None}]},
            "lane:release-5": {"events": [{"ts": "2026-10-30T02:20:00Z", "kind": "state", "text": "release-5 asked the owner for the cloud release time.", "holder": None, "next": None}]},
            "lane:root": {"events": [{"ts": "2026-10-30T02:25:00Z", "kind": "decide", "text": "The coordinator decided something unrelated.", "holder": None, "next": None}]},
        },
    }


@pytest.fixture
def steps(tmp_path) -> Path:
    path = tmp_path / "steps.json"
    path.write_text(json.dumps(doc()))
    return path


def rendered(path: Path, **options) -> str:
    text, code = overview.render(path, now=NOW, **options)
    assert code == 0
    return text


def by_id(path: Path) -> dict[str, dict]:
    return {row["id"]: row for row in json.loads(rendered(path, as_json=True))["rows"]}


def test_a_step_counts_the_changes_it_still_tracks_and_reads_done_only_when_all_landed(steps):
    rows = by_id(steps)
    assert (rows["gateway"]["state"], rows["gateway"]["done"], rows["gateway"]["blocker"]) == ("landed", True, "")
    assert (rows["journal"]["state"], rows["journal"]["done"]) == ("in progress: 1 of 2 changes landed", False)
    assert rows["journal"]["blocker"] == "Its open change is: being graded since 6:14 PM"
    assert rows["forks"]["blocker"] == "The conversion waits on its plan check"
    assert (rows["plan"]["state"], rows["plan"]["done"], rows["plan"]["unblocking"]) == ("in progress: Every release runs manually", False, "The cloud release follows")
    assert (rows["R1"]["state"], rows["R2"]["state"]) == ("Live since 4:28 PM", "Not started")


def test_a_milestone_is_done_once_live_and_says_what_it_updates(steps):
    rows = by_id(steps)
    assert (rows["R1"]["step"], rows["R1"]["delivers"], rows["R1"]["done"]) == ("Milestone 1: Old world kept whole", "Updates the api, gateway and app", True)
    assert (rows["R2"]["delivers"], rows["R2"]["done"], rows["R2"]["unblocking"]) == ("Updates the api", False, "a worker asked for a time")


def test_the_report_names_no_change_number_lane_or_drive_word(steps):
    text = rendered(steps)
    assert overview.DRIVE_WORD.search(text) is None and re.search(r"#\d+", text) is None
    assert "A change was replaced by 3 changes; a worker handed off twice." in text
    assert "The merge worker queues it once the automated code review passes" in text


def test_left_until_completion_lists_every_unfinished_row_with_its_gate_in_plan_order(steps):
    text = rendered(steps)
    assert "1 of 4 plan steps done; 1 of 2 milestones live." in text
    assert text.split("## Left until completion\n\n", 1)[1].splitlines() == [
        "- **2. Mac chat storage** waits on: Its open change is: being graded since 6:14 PM",
        "- **3. Forking chats** waits on: The conversion waits on its plan check",
        "- **4. Release plan** waits on: nothing; the cloud release follows",
        "- **Milestone 2: New code in the cloud** waits on: Your approval for the cloud release",
    ]


@pytest.mark.parametrize(("name", "found"), [("forks", "forks"), ("Forks", "forks"), ("Forking chats", "forks"), ("2", "R2"), ("milestone 2", "R2"), ("r2", "R2")])
def test_one_step_by_id_label_title_or_milestone_number(steps, name, found):
    assert [row["id"] for row in json.loads(rendered(steps, as_json=True, step=name))["rows"]] == [found]


def test_an_unknown_step_exits_one(steps):
    assert overview.render(steps, step="nope", now=NOW) == ("no plan step or milestone named nope", 1)


def test_since_keeps_the_rows_that_moved_and_says_what_moved(steps):
    payload = json.loads(rendered(steps, as_json=True, after="30m"))
    moved = {row["id"]: row["moved"] for row in payload["rows"]}
    assert moved == {"forks": "1 change landed; latest at 7:10 PM: Landed after the automated code review passed", "R1": "latest at 7:20 PM: a worker asked the owner for the cloud release time", "R2": "latest at 7:20 PM: a worker asked the owner for the cloud release time"}
    assert "left" not in payload
    assert "| What moved since 7:00 PM Oct 29 |" in rendered(steps, after="30m")


@pytest.mark.parametrize(
    ("value", "moment"),
    [
        ("90m", datetime(2026, 10, 30, 1, 0, tzinfo=UTC)),
        ("2h", datetime(2026, 10, 30, 0, 30, tzinfo=UTC)),
        ("7:05 PM", datetime(2026, 10, 30, 2, 5, tzinfo=UTC)),
        ("8:00 pm", datetime(2026, 10, 29, 3, 0, tzinfo=UTC)),
        ("2026-10-30T01:00:00Z", datetime(2026, 10, 30, 1, 0, tzinfo=UTC)),
    ],
)
def test_since_reads_a_duration_a_past_pacific_clock_or_an_iso_time(value, moment):
    assert overview.since(value, NOW) == moment


@pytest.fixture
def registered(tmp_path, monkeypatch) -> dict:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv(drive.DRIVE_ENV, raising=False)
    monkeypatch.setenv(drive.SESSION_ENV, ROOT_SESSION)
    entry = {"drive": DRIVE, "sessions": ["older-session", ROOT_SESSION], "orca_run": None, "ledger": LEDGER, "repo": "Forge-AI/monorepo", "checkout": str(tmp_path), "state_dir": str(tmp_path / "state"), "cci_drive": DRIVE}
    drive.write_atomic(drive.drives_dir() / f"{DRIVE}.json", entry)
    return entry


def test_overview_reads_the_progress_record_in_the_drives_state_dir(registered, capsys):
    path = Path(registered["state_dir"]) / overview.STEPS
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(doc()))
    assert drive.main(["plan-status", "--drive", DRIVE, "--step", "forks"]) == 0
    assert "| 3. Forking chats | Forks in the new app. | In progress: 1 of 2 changes landed |" in capsys.readouterr().out


def test_overview_without_a_progress_record_says_where_it_looked(registered, capsys):
    assert drive.main(["overview", "--drive", DRIVE]) == 1
    assert "progress-desk/steps.json; the progress desk writes it" in capsys.readouterr().err


class BoardShell(FakeShell):
    def __init__(self, refuse: str = ""):
        super().__init__()
        self.refuse = refuse
        self.pushed: list[tuple[list[str], dict, str]] = []

    def run(self, argv, stdin=None, cwd=None):
        if argv[0] != "cc-present":
            return super().run(argv, stdin, cwd)
        if self.refuse:
            raise subprocess.CalledProcessError(1, argv, stderr=f"dropped pack\n{self.refuse}\n")
        self.pushed.append((argv, json.loads(stdin), str(cwd)))
        return "ok\n"


def ask(shell: FakeShell, *extra: str) -> int:
    argv = ["ask-owner", "--drive", DRIVE, "--title", "Ship the chat-list fix tonight?", "--lede", "It fixes the empty chat list after launch.", "--option", "Ship now", "--option", "Wait for the morning build", "--recommend", "2", *extra]
    return drive.main(argv, shell)


@pytest.mark.parametrize("number", ["#1", "#100@remote"])
def test_ask_owner_puts_the_card_on_the_root_sessions_board_and_opens_an_ask_for_the_waiting_lanes(registered, capsys, monkeypatch, number):
    shell = BoardShell()
    cci = shell._cci

    def response(argv):
        output = cci(argv)
        return json.dumps(json.loads(output) | {"num": number}) if argv[1] == "post" and "--json" in argv else output

    monkeypatch.setattr(shell, "_cci", response)
    assert ask(shell, "--for", "release-5", "--for", "root") == 0
    [(argv, block, cwd)] = shell.pushed
    assert argv == ["cc-present", "update-block", "-", "--session", ROOT_SESSION, "--round", "current"]
    assert cwd == registered["checkout"]
    assert block == {
        "id": "card-ship-the-chat-list-fix-tonight",
        "type": "card",
        "title": "Ship the chat-list fix tonight?",
        "summary": "It fixes the empty chat list after launch.",
        "children": [{"id": "ship-the-chat-list-fix-tonight-choice", "type": "choice", "options": [
            {"id": "ship-the-chat-list-fix-tonight-1", "label": "Ship now"},
            {"id": "ship-the-chat-list-fix-tonight-2", "label": "Wait for the morning build", "recommended": True},
        ]}],
    }
    post = next(call for call in shell.calls if call[:2] == ["cci", "post"])
    assert post[post.index("--to") + 1] == "release-5,root"
    assert "--force" not in post
    assert [record["kind"] for record in shell.cci] == ["ask"]
    assert shell.cci[0]["text"] == "Owner card card-ship-the-chat-list-fix-tonight is on his board: Ship the chat-list fix tonight? Recommended: Wait for the morning build"
    assert capsys.readouterr().out == f"card card-ship-the-chat-list-fix-tonight is on the owner's board; ask {number} stays open until his tap\n"


@pytest.mark.parametrize(
    ("extra", "said"),
    [
        (("--option", "Fold it into #45306"), "the card names #45306"),
        (("--option", "Three", "--option", "Four", "--option", "Five"), "2 to 4 options; got 5"),
        (("--recommend", "3"), "--recommend 3 names no option"),
    ],
)
def test_ask_owner_refuses_a_card_before_pushing_anything(registered, extra, said):
    shell = BoardShell()
    with pytest.raises(SystemExit, match=said):
        ask(shell, *extra)
    assert shell.pushed == [] and shell.cci == []


def test_a_refused_push_opens_no_ask(registered, capsys):
    shell = BoardShell(refuse="card-x: choice needs options")
    assert ask(shell) == 1
    assert shell.cci == []
    assert capsys.readouterr().err == "card card-ship-the-chat-list-fix-tonight not pushed: card-x: choice needs options\n"
