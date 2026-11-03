"""owner_update.py over a recorded registry, task list, ledger, retro table, board and cci: the update the owner reads and the dashboard steps built from the same rows."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import owner_update
import pytest

DRIVE = "d1"
SESSION = "5e5510aa-0000-0000-0000-000000000000"
HEAD = "632c4a5aa1b2c3d4e5f60718293a4b5c6d7e8f90"
NOW = datetime.now(timezone.utc)


def stamp(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def record(seq: int, lane: str, kind: str, text: str, minutes_ago: float) -> dict:
    return {"seq": seq, "drive": DRIVE, "lane": lane, "kind": kind, "at": stamp(minutes_ago), "text": text, "to": ["root"], "refs": {}}


def task(task_id: str, owner: str, status: str, subject: str) -> dict:
    return {"id": task_id, "subject": subject, "status": status, "owner": owner, "description": "", "blocks": [], "blockedBy": []}


def ask(seq: int, lane: str, text: str, minutes_ago: float, **fields: str) -> dict:
    return {"key": f"ask/{seq:06d}", "fields": {"lane": lane, "text": text, "asked_at": stamp(minutes_ago), "accept": "-"} | fields}


class Runner:
    def __init__(self, records: list[dict], asks: list[dict] = (), board: dict | None = None, incidents: list[dict] = ()) -> None:
        self.records, self.asks, self.board, self.incidents = records, list(asks), board, list(incidents)

    def __call__(self, argv: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
        if argv[:2] == ["cc-present", "outcomes"]:
            return done(json.dumps(self.board)) if self.board is not None else done("", 1, "no board")
        if argv[:2] == ["cci", "tail"]:
            return done("" if "--lane" in argv else "".join(json.dumps(found) + "\n" for found in self.records))
        if argv[:2] == ["cci", "digest"]:
            return done(json.dumps({"open_incidents": self.incidents}))
        if argv[0] == "ccn" and "ledger" in argv:
            return done(json.dumps({"rows": self.asks}))
        raise AssertionError(f"unexpected command {argv}")


def done(out: str, code: int = 0, err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, out, err)


@pytest.fixture
def tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    entry = home / "long-running/drives/d1.json"
    entry.parent.mkdir(parents=True)
    entry.write_text(json.dumps({"drive": DRIVE, "cci_drive": DRIVE, "sessions": [SESSION], "ledger": "abc", "checkout": str(tmp_path), "state_dir": str(tmp_path)}))
    folder = home / "tasks" / f"session-{SESSION[:8]}"
    folder.mkdir(parents=True)
    return folder


def write(folder: Path, *rows: dict) -> None:
    for row in rows:
        (folder / f"{row['id']}.json").write_text(json.dumps(row))


def found(runner: Runner, since_minutes: float = 120) -> dict:
    return owner_update.update(DRIVE, NOW - timedelta(minutes=since_minutes), runner)


def test_each_open_task_lane_with_a_report_since_the_last_update_is_delivered_once_at_its_newest(tasks):
    write(
        tasks,
        task("10", "devbox-explore-7", "pending", "Scenario list"),
        task("11", "devbox-explore-7", "in_progress", "Perf worker scenarios"),
        task("12", "old-lane-1", "completed", "Finished long ago"),
        task("13", "quiet-lane-1", "in_progress", "Quiet work"),
    )
    records = [
        record(1, "devbox-explore-7", "report", "scenario 2 measured", 90),
        record(2, "devbox-explore-7", "report", "scenario 3 measured, see #47525", 30),
        record(3, "devbox-explore-7", "state", "still measuring", 5),
        record(4, "old-lane-1", "report", "done again", 10),
        record(5, "quiet-lane-1", "report", "before the last update", 200),
    ]

    delivered = found(Runner(records, board={"doc": {}, "interactions": {}}))["delivered"]

    assert [(item["task"], item["lane"], item["seq"], item["prs"]) for item in delivered] == [("11", "devbox-explore-7", 2, ["47525"])]
    assert delivered[0]["line"].startswith("delivered #11 devbox-explore-7: Perf worker scenarios | report #2 ")


def test_in_flight_names_each_lane_last_record_or_none_and_asks_skip_answered_ones(tasks):
    write(tasks, task("11", "devbox-explore-7", "in_progress", "Perf worker scenarios"), task("13", "quiet-lane-1", "in_progress", "Quiet work"))
    records = [record(2, "devbox-explore-7", "head", "pushed", 30)]
    asks = [ask(1, "devbox-explore-7", "measure idle resources", 300), ask(2, "quiet-lane-1", "already answered", 300, answered_at=stamp(100))]

    result = found(Runner(records, asks, board={"doc": {}, "interactions": {}}))

    assert [item["line"].split(" | ")[1].split()[:2] for item in result["in_flight"]] == [["last", "head"], ["no", "record"]]
    assert [item["key"] for item in result["asks"]] == ["ask/000001"]
    assert result["asks"][0]["line"].startswith("ask LOST ask/000001 devbox-explore-7: measure idle resources | last head ")


def test_a_board_that_does_not_read_is_named_and_everything_else_still_prints(tasks):
    write(tasks, task("11", "devbox-explore-7", "in_progress", "Perf worker scenarios"))
    incident = record(9, "inc-api-fix", "incident", "SCOPE active yes", 20)

    result = found(Runner([], board=None, incidents=[incident]))

    assert result["board"][0]["line"].startswith("board unread: cc-present outcomes exited 1")
    assert result["incidents"][0]["line"] == f"incident #9 inc-api-fix {owner_update.when(incident)}: SCOPE active yes"
    assert [item["task"] for item in result["in_flight"]] == ["11"]


def test_a_board_row_names_its_pr_head_and_the_owner_verdict_at_that_head(tasks):
    doc = {"blocks": [{"id": "sec", "type": "section", "children": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "title": "Fixes", "prs": [{"number": 47321, "head": HEAD[:7], "title": "PR"}]}]}]}]}
    interactions = {"triage": {"merge-prs": {"47321": {"verdict": "approved", "head": HEAD[:7], "at": stamp(5)}}}}

    board = found(Runner([], board={"doc": doc, "interactions": interactions}))["board"]

    assert [item["line"] for item in board] == ["board #47321 at 632c4a5: approved"]


def test_a_written_update_misses_each_lane_ask_and_pr_it_does_not_name_whole():
    names = ["tooling-codify-1", "release-5", "ask:abc123", "#48401", "tooling-codify-1"]
    assert owner_update.missing("tooling-codify-1 landed #48401; release-5-2 still runs", names) == ["release-5", "ask:abc123"]


def test_a_lane_sits_under_the_plan_step_its_brief_names_by_number_and_its_sub_steps():
    step = owner_update.plans.Step("7", "7. B3: the reader and writer")
    assert owner_update.on_step({"step": "7"}, step) and owner_update.on_step({"step": "7.7"}, step)
    assert not owner_update.on_step({"step": "17"}, step) and not owner_update.on_step({"step": "reader"}, step) and not owner_update.on_step({"step": "all"}, step)


def lane(name: str, state: str, minutes_ago: float | None, **fields) -> dict:
    return {"lane": name, "state": state, "heard": stamp(minutes_ago) if minutes_ago is not None else None, "prs": [], "step": "", "standing": "", "brief": f"/b/{name}.md", "retired_by": None} | fields


def test_the_update_names_every_lane_posting_within_a_day_registered_or_not_and_every_standing_one():
    rows = [
        lane("memory-siblings-2", "desired", 60, step="memory"),
        lane("red-sweep-3", "unregistered", 30),
        lane("root", "unregistered", 1),
        lane("cohort-flag-1", "desired", 30 * 60),
        lane("devbox-perf-1", "launching", None, standing="devbox perf and bug hunting"),
        lane("devbox-explore-7", "retired", 600, standing="devbox perf and bug hunting"),
        lane("old-lane-1", "retired", 5),
    ]

    assert [row["lane"] for row in owner_update.working(rows, NOW)] == ["memory-siblings-2", "red-sweep-3", "devbox-perf-1"]


def sources(*rows: dict, latest: dict | None = None, waits: dict | None = None) -> owner_update.Sources:
    return owner_update.Sources({row["lane"]: row for row in rows}, latest or {}, waits or {}, NOW)


def test_a_standing_duty_reads_the_end_of_its_successor_chain_and_no_successor_once_that_end_retired_alone():
    found = sources(
        lane("devbox-explore-7", "retired", 600, standing="devbox perf and bug hunting", retired_by="done"),
        lane("board-refresher-6", "retired", 90, standing="refresh the board", retired_by="board-refresher-7"),
        lane("board-refresher-7", "desired", 2, standing="refresh the board"),
    )

    devbox, board = owner_update.duty_rows(found)

    assert (devbox.who, devbox.blocks, devbox.unblock, devbox.unowned) == ("**nobody**", "devbox-explore-7 retired by done with no successor", "root launches its successor from /b/devbox-explore-7.md this turn", True)
    assert (board.name, board.who, board.unowned) == ("refresh the board", "board-refresher-7", False)


def test_work_is_held_only_by_lanes_heard_within_two_hours_with_their_next_record_due_and_root_waits_named():
    found = sources(
        lane("release-5", "desired", 10),
        lane("old-desk-1", "desired", 300),
        lane("root", "unregistered", 1),
        latest={"release-5": record(7, "release-5", "head", "pushed #48700", 10)},
        waits={"release-5": record(8, "release-5", "blocker", "needs the owner's word on deletes", 5)},
    )

    who, blocks, unblock, wrong = found.work(["release-5", "old-desk-1", "root"])
    nobody = found.work(["old-desk-1", "root"])

    assert who == "release-5" and blocks.startswith("release-5 waits on root's answer to blocker #8 since ")
    assert unblock.startswith("release-5: head ") and "; next record due by " in unblock
    assert wrong.startswith("old-desk-1 quiet since ")
    assert nobody[0] == "**nobody**" and nobody[2] == "nobody is on it, so root starts a lane on it this turn"


def test_a_plan_with_no_numbered_steps_rows_each_piece_its_title_lists_and_a_lane_joins_a_piece_by_its_word():
    plan = owner_update.plans.Plan("731649a5", "SandSQL five pieces: ssql-server (two modes), ssql-replicator, ssql-signal; migration R0-R8", "draft", [owner_update.plans.Step("unmapped", "No step label")])

    assert owner_update.pieces(plan) == ["ssql-server", "ssql-replicator", "ssql-signal"]
    assert owner_update.on_piece({"lane": "uploads-to-replicator-1"}, "ssql-replicator") and owner_update.on_piece({"lane": "x-1", "deliverable": "read the sandsql-server supervisor"}, "ssql-server")
    assert not owner_update.on_piece({"lane": "signal-valkey-2"}, "ssql-replicator")


def test_a_step_with_every_task_done_is_never_unowned_and_an_open_one_with_nobody_is():
    task = lambda state: owner_update.plans.Task("t", "Port the consumer", state, None, None, "#45723 review" if state == "blocked" else None, 0)
    done = owner_update.step_row("Step 2", "2. Data", [task("done")], [], sources())
    open_step = owner_update.step_row("Step 0", "0. The base", [task("done"), task("blocked")], [], sources())

    assert not done.unowned and open_step.unowned
    assert open_step.state == "1 of 2 tasks done, 0 in progress, 1 blocked" and open_step.blocks == "task Port the consumer waits on #45723 review"


def test_a_table_cell_never_breaks_its_row():
    row = owner_update.Row("#48401", "a | b", "red", "**nobody**", "waits: red", "x", "its head is red")
    assert owner_update.table([row])[2] == "| #48401 | a / b | red | **nobody** | waits: red | x | its head is red |"


@pytest.mark.parametrize(
    ("lane_name", "text", "to", "named"),
    [
        ("root", "6:11 PM root INCIDENT OPEN ssql-cred-rotation: prod errors", ["dev-health-desk-1"], "ssql-cred-rotation"),
        ("root", "6:25 AM root: user-creds closed", ["drive-watchdog-2@d1"], "drive-watchdog-2"),
        ("run-creds-fix-2", "PR up", ["root"], "run-creds-fix-2"),
    ],
)
def test_an_incident_record_is_filed_under_the_slug_it_opens_else_its_lane_else_the_lane_a_coordinator_addresses(lane_name, text, to, named):
    assert owner_update.incident_name({"lane": lane_name, "text": text, "to": to}) == named


def test_a_lane_row_says_what_went_wrong_with_the_prevention_of_each_open_retro_row_it_owns():
    found = owner_update.Sources(
        {"release-5": lane("release-5", "desired", 10)}, {}, {}, NOW,
        ({"owner": "release-5", "failure": "A cancel mid-apply broke prod", "mechanism": "the release tool refuses a cancel after apply starts", "state": "building", "due": stamp(-30)},
         {"owner": "release-5", "failure": "Closed long ago", "mechanism": "fixed", "state": "live"},
         {"owner": "other-1", "failure": "Not this lane", "mechanism": "-", "state": "open"}),
    )

    wrong = found.work(["release-5"])[3]

    assert wrong.startswith("A cancel mid-apply broke prod, prevented by the release tool refuses a cancel after apply starts (building, due ")
    assert "Closed long ago" not in wrong and "Not this lane" not in wrong


def test_owner_words_name_the_record_that_carries_each_and_flag_the_ones_nothing_carries():
    records = [
        record(11, "owner-relay", "ask", "fix the five services blockers", 30),
        record(12, "root", "go", "spawned five-fix-1", 20) | {"re": 11},
        record(13, "owner-relay", "ask", "and the devbox", 15),
        record(14, "owner-relay", "ask", "and the gateway", 10),
        record(15, "root", "decide", "gateway-2 owns it", 5) | {"resolves": 14},
        record(16, "owner-relay", "ask", "and #1 in the list", 4),
        record(17, "root", "go", "see #160 for the list", 3),
        record(18, "owner-relay", "ask", "and the SQL tab", 2),
        record(19, "root", "go", "sql-tab-fix-1 owns it", 1) | {"re": 18},
        record(20, "owner-relay", "ask", "Fix the gateway, and keep every provider the platform supports working the whole time while you move the keys over. Do not deploy.", 1),
        record(21, "root", "state", "PR #20 is still red", 0),
    ]

    lines, gaps = owner_update.owner_words(records)

    assert [line.split(" | ")[1] for line in lines] == ["carried by root #12", "**no record carries this yet**", "carried by root #15", "**no record carries this yet**", "carried by root #19", "**no record carries this yet**"]
    assert lines[0].endswith('"fix the five services blockers" | carried by root #12')
    assert gaps == ["owner words no record carries: #13", "owner words no record carries: #16", "owner words no record carries: #20"]
    assert lines[-1].endswith('while you move the keys over. Do not deploy." | **no record carries this yet**')


def test_since_you_last_looked_names_new_rows_and_what_moved_and_nothing_that_stayed():
    before = {"Step 1|Step 1": ["release-5", "nothing recorded", "open"], "Step 2|Step 2": ["x-1", "nothing recorded", "open"]}
    now = {"Step 1|Step 1": ["**nobody**", "nothing recorded", "open"], "Step 2|Step 2": ["x-1", "nothing recorded", "open"], "Step 3|Step 3": ["y-1", "nothing recorded", "open"]}

    assert owner_update.since_lines(now, before) == ["- Step 1: who release-5 -> **nobody**", "- new: Step 3"]
    assert owner_update.since_lines(before, before) == []
    assert owner_update.since_lines({"Step 2|Step 2": before["Step 2|Step 2"]}, before | {"Open PRs|#1940": ["fix-1", "nothing recorded", "done"]}) == ["- Step 1: no longer open (landed, answered or closed)"]


def test_a_dashboard_step_says_its_blocker_next_step_and_failures_in_plain_words_and_keeps_the_rows_cells_for_the_drill_in():
    row = owner_update.Row("Step 3", "3. Switch the chat writer", "1 of 2 tasks done, 1 in progress, 0 blocked", "chat-1, chat-2", "chat-1 waits on root's answer to ask #9", "chat-1: head 6:01 PM: pushed; next record due by 6:31 PM", "None recorded")
    lanes = [lane("chat-1", "desired", 5, prs=[48700, 48701]), lane("chat-2", "desired", 15, prs=[48701])]
    wait = record(9, "chat-1", "ask", "which writer?", 20)
    found = owner_update.Sources({item["lane"]: item for item in lanes}, {}, {"chat-1": wait}, NOW, ({"owner": "chat-2", "failure": "The writer shipped a layout no reader knew.", "mechanism": "readers first", "state": "building"},))

    step = owner_update.step_entry(4, "3. Switch the chat writer", "Plan, step 3", row, lanes, found)

    due = owner_update.owner_waits.pacific(NOW - timedelta(minutes=15) + owner_update.STALE_AFTER)
    assert (step["id"], step["label"], step["tone"], step["who"], step["prs"], step["lanes"]) == ("s4", "3. Switch", "moving", ["chat-1", "chat-2"], [48700, 48701], ["chat-1", "chat-2"])
    assert step["blocker"] == f"Waiting on a coordinator decision since {owner_update.when(wait)}."
    assert step["next"] == step["unblocking"] == f"2 workers are on it; the next word is due by {due}."
    assert step["went_wrong"] == "The writer shipped a layout no reader knew. Prevention: readers first."
    assert step["detail"] == {"blocker": row.blocks, "next": row.unblock, "went_wrong": "None recorded"}
    nobody = owner_update.step_entry(0, "x", "s", owner_update.Row("x", "", "", "**nobody**", "nothing recorded", "", ""), [], found)
    assert (nobody["tone"], nobody["who"], nobody["blocker"], nobody["next"]) == ("later", [], None, "Nobody is on it; the coordinator puts someone on it next.")


class DriveRunner:
    def __init__(self, records: list[dict], lanes: list[dict]) -> None:
        self.records, self.lanes = records, lanes
        self.ledgers = {
            "abc": [ask(1, "ssql-replicator-1", "measure idle resources", 300), {"key": "48401", "fields": {"state": "open", "title": "replicator: upload", "lane": "ssql-replicator-1"}}],
            "rl": [{"key": "retro/000001", "fields": {"failure": "The five services dropped from two updates", "mechanism": "pieces rows from the plan", "owner": "ssql-replicator-1", "due": stamp(-60), "state": "building"}}],
        }

    def __call__(self, argv: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
        if argv[1].endswith("drive_supervisor.py"):
            return done("".join(json.dumps(row) + "\n" for row in self.lanes))
        if argv[:2] == ["cc-present", "outcomes"]:
            return done(json.dumps({"doc": {}, "interactions": {}}))
        if argv[:2] == ["cci", "tail"]:
            return done("" if "--lane" in argv else "".join(json.dumps(found) + "\n" for found in self.records))
        if argv[:2] == ["cci", "digest"]:
            return done(json.dumps({"open_incidents": [], "open_asks": [], "open_blockers": []}))
        if argv[3:5] == ["ledger", "show"]:
            return done(json.dumps({"rows": self.ledgers[argv[5]]}))
        return done("[]")


@pytest.fixture
def drive_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    entry = home / "long-running/drives/d1.json"
    entry.parent.mkdir(parents=True)
    pins = [{"name": "The next build", "delivers": "Bugs fixed against open, and the platform team's flip back", "words": ["next-build"]}]
    entry.write_text(json.dumps({"drive": DRIVE, "cci_drive": DRIVE, "sessions": [SESSION], "ledger": "abc", "checkout": str(tmp_path), "state_dir": str(tmp_path), "program": "prog", "pinned_rows": pins}))
    (tmp_path / "retro.json").write_text(json.dumps({"ledger": "rl"}))
    return tmp_path


def test_the_update_opens_with_what_moved_and_his_words_ends_with_its_gaps_and_publishes_the_snapshot_it_diffs_against(drive_home):
    runner = DriveRunner(
        [
            record(10, "ssql-replicator-1", "state", "fixing the red test", 5),
            record(11, "owner-relay", "ask", "fix the five services blockers", 30),
            record(12, "root", "go", "spawned five-fix-1", 20) | {"re": 11},
            record(13, "owner-relay", "ask", "and the devbox", 15),
            record(14, "release-5", "release", "llm-gateway live", 60),
            record(15, "owner-relay", "note", "Bug fixes: the chat list fix ships in the next release", 25) | {"topic": "fyi"},
            record(16, "root", "note", "Lanes: board-refresher-7 recycled", 24) | {"topic": "fyi"},
        ],
        [lane("ssql-replicator-1", "desired", 10, prs=[48401], deliverable="The replicator uploads every chat")],
    )

    text, names, model = owner_update.report(DRIVE, runner, NOW)

    titles = [line for line in text.splitlines() if line.startswith("## ")]
    assert titles[:5] == [f"## Since you last looked ({owner_update.owner_waits.pacific(NOW - owner_update.DEFAULT_SINCE)})", "## Decisions made for you (FYI)", "## Your words since the last update", "## Unowned work", "## Pinned for you"]
    assert "- **Bug fixes**" in text and ": the chat list fix ships in the next release" in text and "board-refresher-7 recycled" not in text
    assert "| The next build | Bugs fixed against open, and the platform team's flip back |" in text
    assert [entry["text"] for entry in model["steps"]["fyi"]] == ["the chat list fix ships in the next release"]
    assert model["steps"]["steps"][0]["title"] == "The next build" and model["steps"]["steps"][0]["tone"] == "later"
    assert titles[-1] == "## Gaps in this update" and "- owner words no record carries: #13" in text
    assert '"fix the five services blockers" | carried by root #12' in text and "- The first update from this generator: every row below is new." in text
    assert "The five services dropped from two updates, prevented by pieces rows from the plan (building, due " in text
    assert owner_update.missing(text, names) == [] and model["gaps"] == ["owner words no record carries: #13"]
    assert model["steps"]["now"]["release"]["name"] == "release-5"

    (drive_home / owner_update.STEPS).parent.mkdir(parents=True, exist_ok=True)
    (drive_home / owner_update.STEPS).write_text(json.dumps({"steps": [], "pr_state": {"48401": {"state": "Open"}}, "history": {"lane:x-1": {"events": []}}}))
    owner_update.publish(model, sent=False)
    published = json.loads((drive_home / owner_update.STEPS).read_text())
    assert (published["pr_state"], published["history"]) == ({"48401": {"state": "Open"}}, {"lane:x-1": {"events": []}}) and not (drive_home / owner_update.SNAPSHOT).exists()
    from long_running import overview
    assert [row.id for row in overview.rows(published)][-1] == "release:release-5"
    owner_update.publish(model, sent=True)
    again, _, _ = owner_update.report(DRIVE, runner, NOW)

    assert again.split("## Since you last looked", 1)[1].split("\n## ", 1)[0].strip().splitlines()[-1] == owner_update.NOTHING


def test_fyis_are_only_the_bridges_notes_on_topic_fyi_grouped_by_the_words_before_the_first_colon_newest_first():
    records = [
        record(1, "owner-relay", "note", "Bug fixes: the share link no longer waits on uploads", 50) | {"topic": "fyi"},
        record(2, "owner-relay", "note", "A plain sentence with no group", 40) | {"topic": "fyi"},
        record(3, "root", "note", "Lanes: red-sweep-3 recycled", 30) | {"topic": "fyi"},
        record(4, "owner-relay", "note", "not an fyi", 20),
        record(5, "owner-relay", "note", "Bug fixes: the SQL tab opens again", 10) | {"topic": "fyi"},
    ]

    entries = owner_update.fyi_entries(records)

    assert [(entry["seq"], entry["group"], entry["text"]) for entry in entries] == [(5, "Bug fixes", "the SQL tab opens again"), (2, "Other", "A plain sentence with no group"), (1, "Bug fixes", "the share link no longer waits on uploads")]
    lines = owner_update.fyi_lines(entries)
    assert lines[0] == "- **Bug fixes**" and lines[1].endswith(": the SQL tab opens again") and lines[2].endswith(": the share link no longer waits on uploads") and lines[3] == "- **Other**"


def test_a_pinned_row_is_held_by_every_working_lane_its_words_name_and_by_nobody_when_none_does():
    found = sources(lane("devbox-perf-1", "desired", 10), lane("next-build-tester-2", "desired", 10, deliverable="run the next build"))
    entry = {"pinned_rows": [{"name": "Devbox speed and bug hunting", "delivers": "The perf harness and the creative tester", "words": ["devbox"]}, {"name": "Gateway", "delivers": "Every provider", "words": ["gateway"]}]}
    lanes = list(found.by_lane.values())

    (devbox, mine), (gateway, none) = owner_update.pinned_rows(entry, lanes, found)

    assert (devbox.name, devbox.who, [lane["lane"] for lane in mine]) == ("Devbox speed and bug hunting", "devbox-perf-1", ["devbox-perf-1"])
    assert (gateway.who, gateway.unowned, none) == ("**nobody**", True, [])


def test_a_full_row_only_where_a_blocker_or_a_failure_lives_and_one_line_naming_the_rest():
    blocked = owner_update.Row("red-sweep-3", "reds", "desired", "red-sweep-3", "red-sweep-3 waits on root's answer to ask #9", "red-sweep-3: state 6:01 PM", "None recorded")
    plain = owner_update.Row("flake-1", "flakes", "desired", "flake-1", "nothing recorded", "flake-1: state 6:05 PM: fixed", "None recorded")
    quiet = owner_update.Row("old-lane-1", "x", "desired, last report 2:38 PM", "**nobody**", "nothing recorded", "nobody is on it", "old-lane-1 quiet since 2:38 PM")
    step = owner_update.Row("Step 3", "3. The writer", "0 of 2 tasks done", "flake-1, red-sweep-3", "nothing recorded", "-", "None recorded")
    closed = owner_update.Row("user-creds", "creds", "closed since 1:15 AM", "**nobody**", "nothing recorded", "-", "-", done=True)
    red = owner_update.Row("#46107", "sand: unarchive a chat", "red", "**nobody**", "held by cci #1", "-", "its head is red")
    held = owner_update.Row("#45623", "iris: a model pick", "red", "p8a-step7", "held by cci #2", "-", "-")

    lanes = owner_update.rendered(owner_update.LANES_ON_NO_STEP, [blocked, plain, quiet])
    steps = owner_update.rendered("Plan, step 3", [step, blocked, plain, quiet], step=True)

    assert [line.split(" |")[0] for line in lanes[2:]] == ["| red-sweep-3", "- flake-1: flake-1: state 6:05 PM: fixed", "", "Quiet over 2 h, nobody on them now: old-lane-1 (desired, last report 2:38 PM)"]
    assert [line.split(" |")[0] for line in steps[2:]] == ["| Step 3", "| red-sweep-3", "", "Quiet over 2 h, nobody on them now: old-lane-1 (desired, last report 2:38 PM)"]
    assert owner_update.rendered(owner_update.INCIDENTS, [closed]) == ["", "Closed today: user-creds (closed since 1:15 AM)"]
    assert owner_update.rendered(owner_update.OPEN_PRS, [red, held]) == ["- red (2): #46107 sand: unarchive a chat; #45623 iris: a model pick [p8a-step7]"]
    assert owner_update.unowned_lines([(owner_update.OPEN_PRS, [red, held]), (owner_update.INCIDENTS, [closed])]) == ["- Open PRs (1): #46107"]
    assert owner_update.cell("x" * 150) == "x" * 99 + "…"
