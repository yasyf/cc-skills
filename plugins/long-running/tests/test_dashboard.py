from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from hooks import dashboard as hook

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "long-running" / "scripts" / "lr-dashboard.py"
spec = importlib.util.spec_from_file_location("lr_dashboard", SCRIPT)
dashboard = importlib.util.module_from_spec(spec)
sys.modules["lr_dashboard"] = dashboard
spec.loader.exec_module(dashboard)

MODIFIED = datetime(2026, 10, 5, 5, 20, tzinfo=UTC)

INBOX = """\
- R595 (deploy-experience-8, 15:58Z Oct 1): #28915 (AmiBake fde-checkout fix) landed f7969af1df
## R751 (2026-10-02 01:1xZ / 6:1x PM PT) — sandsql-api-errors-fix: evidence complete
lost-ask-capt-hook: READY 14:47Z https://github.com/yasyf/captain-hook/pull/230 0b81826f CI green
HOLD (9:03 PM PT) reco-bucket-2: #30388 do NOT enqueue.
R1086 (9:52 PM PT) orca-desk: launch hsbc-sanddb-2147-fix NOW sol xhigh brief=/x/fix-brief.md
21:53 LAUNCHED R1086 hsbc-sanddb-2147-fix: dispatch ctx_65c99a29432f
LANDED 10:02 PM PT rules-nudge-hook: yasyf/captain-hook#306 merged f327d1db
hsbc-sanddb-2147-evidence (22:02 PT) -> hsbc-sanddb-2147-fix: (2) stable 5m8qv: 0 log lines
OPENED compact-continuity (10:04 PM PT) yasyf/cc-skills#227 compact-continuity 765aef0: register
- landing-sweep-15 (10:04 PM PT) LANDED 0f7dbd244c #30414 (release: fix Platy messages)
GO root (10:04 PM PT) hsbc-sanddb-2147-fix durable: option A
  continuation with no stamp at all
"""


@pytest.fixture
def lines():
    return {line.number: line for line in dashboard.parse_inbox("deploy-go.md", INBOX, [(12, MODIFIED)])}


@pytest.mark.parametrize(
    ("number", "lane", "verb", "ref", "to"),
    [
        (1, "deploy-experience-8", None, "R595", None),
        (2, None, None, "R751", None),
        (3, "lost-ask-capt-hook", "READY", None, None),
        (4, "reco-bucket-2", "HOLD", None, None),
        (5, "orca-desk", "LAUNCH", "R1086", None),
        (6, None, "LAUNCHED", None, None),
        (7, None, "LANDED", None, None),
        (8, "hsbc-sanddb-2147-evidence", None, None, "hsbc-sanddb-2147-fix"),
        (9, "compact-continuity", "OPENED", None, None),
        (10, "landing-sweep-15", "LANDED", None, None),
        (11, "root", "GO", None, None),
    ],
)
def test_parse_line_reads_lane_verb_and_reference(lines, number, lane, verb, ref, to):
    line = lines[number]
    assert (line.lane, line.verb, line.ref, line.to) == (lane, verb, ref, to)


@pytest.mark.parametrize(
    ("number", "at"),
    [
        (11, "2026-10-05T05:04:00Z"),
        (8, "2026-10-05T05:02:00Z"),
        (7, "2026-10-05T05:02:00Z"),
        (6, "2026-10-05T04:53:00Z"),
        (5, "2026-10-05T04:52:00Z"),
        (4, "2026-10-05T04:03:00Z"),
        (3, "2026-10-04T14:47:00Z"),
        (2, "2026-10-02T01:10:00Z"),
        (1, "2026-10-01T15:58:00Z"),
    ],
)
def test_parse_inbox_places_each_clock_on_a_day_walking_back_from_the_mtime(lines, number, at):
    assert dashboard.iso(lines[number].at) == at


def test_a_line_without_a_clock_inherits_the_next_stamp_as_approximate(lines):
    line = lines[12]
    assert line.approx
    assert dashboard.iso(line.at) == dashboard.iso(MODIFIED)


def test_pr_references_keep_their_repository(lines):
    assert lines[7].prs == ["yasyf/captain-hook#306"]
    assert lines[10].prs == ["30414"]
    assert lines[3].prs == ["yasyf/captain-hook#230"]


def placed(text: str, modified: datetime = MODIFIED) -> list[str | None]:
    return [dashboard.iso(line.at) for line in dashboard.parse_inbox("x.md", text, [(len(text.splitlines()), modified)])]


def test_overnight_gaps_still_step_back_a_day():
    text = "a (9:00 AM PT) one\nb (5:00 PM PT) two\nc (9:00 AM PT) three\nd (5:00 PM PT) four\n"
    assert placed(text) == ["2026-10-03T16:00:00Z", "2026-10-04T00:00:00Z", "2026-10-04T16:00:00Z", "2026-10-05T00:00:00Z"]


@pytest.mark.parametrize(
    ("text", "at"),
    [
        ("a (2026-10-04T09:00:00+00:00) x", "2026-10-04T09:00:00Z"),
        ("a (2026-10-04 09:00 UTC) x", "2026-10-04T09:00:00Z"),
        ("a (2026-10-04 09:00 PT) x", "2026-10-04T16:00:00Z"),
        ("a (2026-10-04T09:00-07:00) x", "2026-10-04T16:00:00Z"),
    ],
)
def test_iso_stamps_keep_their_zone(text, at):
    assert placed(text) == [at]


def test_a_stamp_quoting_an_old_date_does_not_drag_earlier_lines_back():
    text = "a (9:00 PM PT) first\nb (2026-09-04 10:40Z) quoted\nc (9:30 PM PT) last\n"
    assert placed(text) == ["2026-10-05T04:00:00Z", "2026-09-04T10:40:00Z", "2026-10-05T04:30:00Z"]


def test_an_anchor_bounds_every_line_it_covers():
    text = "a (10:00 PM PT) misread as today\nb (9:00 AM PT) seen this morning\nc (9:30 PM PT) last\n"
    anchors = [(2, datetime(2026, 10, 4, 16, 30, tzinfo=UTC)), (3, MODIFIED)]
    assert [dashboard.iso(line.at) for line in dashboard.parse_inbox("x.md", text, anchors)] == [
        "2026-10-04T05:00:00Z",
        "2026-10-04T16:00:00Z",
        "2026-10-05T04:30:00Z",
    ]


def test_the_collector_records_an_anchor_each_time_a_file_grows(tmp_path):
    collector = dashboard.Collector({"state_dir": str(tmp_path)})
    assert collector.anchors("x.md", 3, MODIFIED) == [(3, MODIFIED), (3, MODIFIED)]
    later = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)
    assert collector.anchors("x.md", 5, later) == [(3, MODIFIED), (5, later), (5, later)]
    reloaded = dashboard.Collector({"state_dir": str(tmp_path)})
    assert reloaded.anchors("x.md", 5, later) == [(3, MODIFIED), (5, later), (5, later)]
    assert reloaded.anchors("x.md", 2, later) == [(2, later), (2, later)]


def test_a_month_day_stamp_after_the_cursor_belongs_to_the_previous_year():
    assert placed("a (23:58Z Dec 31) x", datetime(2027, 1, 1, 0, 5, tzinfo=UTC)) == ["2026-12-31T23:58:00Z"]


def test_parse_manual_reads_scalar_and_mapping_items():
    text = """\
# pinned by the root
owner:
  - Approve the HSBC cutover window
  - text: Read the release-simplify keep table
    url: https://example.com/board
pinned:
  - "release-v3 freeze lifts at 6 AM PT"
"""
    assert dashboard.parse_manual(text) == {
        "owner": [
            {"text": "Approve the HSBC cutover window"},
            {"text": "Read the release-simplify keep table", "url": "https://example.com/board"},
        ],
        "pinned": [{"text": "release-v3 freeze lifts at 6 AM PT"}],
    }


def test_parse_boards_reads_the_sessions_table():
    text = """\
cc-present daemon 0.37.4 · port 61118

SUBJECT                           SLUG                                     SESSION                               STATUS  EVENTS  URL
4c5e6e7d754112883cd3de9cf7e5cf26  pulumi-duplication-audit--13a0f5ae       67c0e5da-38e8-4ce7-aada-27f9a0b7aeaf  open    16      http://127.0.0.1:61118/p/pulumi-duplication-audit--13a0f5ae
9208735d2c7b243b7c5bad25a2b820a5  structural-fixes--a396800d               -                                     closed  9       http://127.0.0.1:61118/p/structural-fixes--a396800d
"""
    assert dashboard.parse_boards(text) == [
        {
            "subject": "4c5e6e7d754112883cd3de9cf7e5cf26",
            "slug": "pulumi-duplication-audit--13a0f5ae",
            "session": "67c0e5da-38e8-4ce7-aada-27f9a0b7aeaf",
            "status": "open",
            "events": 16,
            "url": "http://127.0.0.1:61118/p/pulumi-duplication-audit--13a0f5ae",
        },
        {
            "subject": "9208735d2c7b243b7c5bad25a2b820a5",
            "slug": "structural-fixes--a396800d",
            "session": None,
            "status": "closed",
            "events": 9,
            "url": "http://127.0.0.1:61118/p/structural-fixes--a396800d",
        },
    ]


def write_task(directory: Path, task: dict, mtime: float) -> None:
    path = directory / f"{task['id']}.json"
    path.write_text(json.dumps(task))
    os.utime(path, (mtime, mtime))


def test_read_tasks_merges_live_files_and_the_archive(tmp_path):
    write_task(tmp_path, {"id": "641", "subject": "ship", "status": "in_progress", "owner": "lr-dashboard"}, MODIFIED.timestamp())
    (tmp_path / ".archive.ndjson").write_text(json.dumps({"id": "6", "subject": "old", "status": "completed", "archivedAt": "2026-10-05T04:51:14Z"}) + "\n")
    tasks = sorted(dashboard.read_tasks(tmp_path), key=lambda task: int(task["id"]))
    assert tasks == [
        {"id": "6", "subject": "old", "status": "completed", "archivedAt": "2026-10-05T04:51:14Z", "archived": True, "updated_at": "2026-10-05T04:51:14Z"},
        {"id": "641", "subject": "ship", "status": "in_progress", "owner": "lr-dashboard", "updated_at": "2026-10-05T05:20:00Z"},
    ]


def test_task_list_dir_follows_the_newest_teammate_team(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTAIN_HOOK_TASKS_DIR", str(tmp_path / "tasks"))
    transcript = tmp_path / "project" / "900424b6-7393.jsonl"
    subagents = transcript.with_suffix("") / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-a.meta.json").write_text(json.dumps({"teamName": "session-1111aaaa"}))
    (subagents / "agent-b.meta.json").write_text(json.dumps({"teamName": "session-67c0e5da"}))
    os.utime(subagents / "agent-a.meta.json", (1, 1))
    (tmp_path / "tasks" / "session-67c0e5da").mkdir(parents=True)
    (tmp_path / "tasks" / "session-900424b6").mkdir(parents=True)
    assert dashboard.task_list_dir("900424b6-7393", transcript) == tmp_path / "tasks" / "session-67c0e5da"


def test_an_explicit_task_list_id_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTAIN_HOOK_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", "shared-list")
    (tmp_path / "tasks" / "shared-list").mkdir(parents=True)
    (tmp_path / "tasks" / "session-900424b6").mkdir(parents=True)
    assert dashboard.task_list_dir("900424b6-7393", tmp_path / "p" / "900424b6-7393.jsonl") == tmp_path / "tasks" / "shared-list"


def test_compactions_in_reads_boundaries_from_an_offset(tmp_path):
    transcript = tmp_path / "s.jsonl"
    boundary = {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-10-05T04:31:57.813Z", "sessionId": "s", "compactMetadata": {"trigger": "auto", "preTokens": 566722, "postTokens": 38598}}
    transcript.write_text(json.dumps({"type": "user"}) + "\n" + json.dumps(boundary, separators=(",", ":")) + "\n")
    found, offset = dashboard.compactions_in(transcript, 0)
    assert found == [{"at": "2026-10-05T04:31:57.813Z", "session": "s", "trigger": "auto", "pre_tokens": 566722, "post_tokens": 38598}]
    assert offset == transcript.stat().st_size
    assert dashboard.compactions_in(transcript, offset) == ([], offset)


def test_project_slug_matches_the_claude_projects_directory():
    assert dashboard.project_slug("/Users/yasyf/.orca/workspaces/monorepo/monorepo/sole") == "-Users-yasyf--orca-workspaces-monorepo-monorepo-sole"


def test_the_hook_finds_the_drive_that_claims_the_session(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    drives = tmp_path / hook.DRIVES
    drives.mkdir(parents=True)
    (drives / "900424b6.json").write_text(json.dumps({"drive": "900424b6", "sessions": ["900424b6-0000"]}))
    assert hook.drive_of(SimpleNamespace(session_id="900424b6-0000")) == "900424b6"
    assert hook.drive_of(SimpleNamespace(session_id="5e55-0000")) is None


@pytest.mark.parametrize(
    ("calls", "starts"),
    [
        ([("drive.py", ("start", "--ledger", "x"))], True),
        ([("/p/bin/drive.py", ("start",))], True),
        ([("python3", ("/p/scripts/drive.py", "start", "--ledger", "x"))], True),
        ([("drive.py", ("list",))], False),
        ([("ledger.py", ("start",))], False),
    ],
)
def test_the_hook_recognizes_drive_start(calls, starts):
    evt = SimpleNamespace(cmd=SimpleNamespace(calls=lambda: [SimpleNamespace(name=name, args=args) for name, args in calls]))
    assert hook.starts_drive(evt) is starts


def test_inbox_files_include_rotated_archives(tmp_path):
    (tmp_path / "deploy-go.md").write_text("x\n")
    (tmp_path / "deploy-go.md.archive").mkdir()
    (tmp_path / "deploy-go.md.archive" / "2026-10-04.md").write_text("y\n")
    (tmp_path / ".inbox-watch.json").write_text("{}")
    assert [str(path.relative_to(tmp_path)) for path in dashboard.inbox_files(tmp_path)] == ["deploy-go.md", "deploy-go.md.archive/2026-10-04.md"]
