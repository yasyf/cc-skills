from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from hooks import dashboard as hook
from livedash import registry
from livedash.context import Context

PACK = Path(__file__).resolve().parents[1] / "dashboard"
registry.load_packs({"lr": str(PACK)})
inboxlines = sys.modules["livedash_pack_lr.inboxlines"]
pack = sys.modules["livedash_pack_lr.components"]
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
    return {line.number: line for line in inboxlines.parse_inbox("deploy-go.md", INBOX, [(12, MODIFIED)])}


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
    assert inboxlines.iso(lines[number].at) == at


def test_a_line_without_a_clock_inherits_the_next_stamp_as_approximate(lines):
    line = lines[12]
    assert line.approx
    assert inboxlines.iso(line.at) == inboxlines.iso(MODIFIED)


def test_pr_references_keep_their_repository(lines):
    assert lines[7].prs == ["yasyf/captain-hook#306"]
    assert lines[10].prs == ["30414"]
    assert lines[3].prs == ["yasyf/captain-hook#230"]


def placed(text: str, modified: datetime = MODIFIED) -> list[str | None]:
    return [inboxlines.iso(line.at) for line in inboxlines.parse_inbox("x.md", text, [(len(text.splitlines()), modified)])]


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
    assert [inboxlines.iso(line.at) for line in inboxlines.parse_inbox("x.md", text, anchors)] == [
        "2026-10-04T05:00:00Z",
        "2026-10-04T16:00:00Z",
        "2026-10-05T04:30:00Z",
    ]


def test_a_month_day_stamp_after_the_cursor_belongs_to_the_previous_year():
    assert placed("a (23:58Z Dec 31) x", datetime(2027, 1, 1, 0, 5, tzinfo=UTC)) == ["2026-12-31T23:58:00Z"]


def decide_line(at: str, lane: str | None, text: str, verb: str = "DECIDE") -> dict:
    return {"at": at, "lane": lane, "verb": verb, "text": text}


def test_an_owner_decision_stays_open_until_its_lane_moves_on():
    lines = [
        decide_line("2026-10-05T05:00:00Z", "merge-walker", "STATE merge-walker (10:00 PM PT) walking", "STATE"),
        decide_line("2026-10-05T04:30:00Z", None, "21:30 DECIDE msg_ab12 alerts-api-0803-fix: rollback or forward? needs the owner"),
        decide_line("2026-10-05T04:00:00Z", "merge-walker", "DECIDE merge-walker (9:00 PM PT) A or B, needs the owner"),
        decide_line("2026-10-05T03:00:00Z", "valkey-fold-2", "DECIDE valkey-fold-2 (8:00 PM PT) runbook cannot run as written, ask the owner"),
        decide_line("2026-10-05T02:00:00Z", "other", "DECIDE other (7:00 PM PT) routine pick for the root"),
        decide_line("2026-10-01T02:00:00Z", "old", "DECIDE old (7:00 PM PT) stale, needs the owner"),
    ]
    waiting = pack.open_decisions(lines, "2026-10-04T00:00:00Z")
    assert [line["text"][:24] for line in waiting] == ["21:30 DECIDE msg_ab12 al", "DECIDE valkey-fold-2 (8:"]


def test_a_later_line_naming_the_lane_answers_its_decision():
    lines = [
        decide_line("2026-10-05T05:00:00Z", "root", "GO root (10:00 PM PT) valkey-fold-2 option A", "GO"),
        decide_line("2026-10-05T03:00:00Z", "valkey-fold-2", "DECIDE valkey-fold-2 (8:00 PM PT) A or B, needs the owner"),
    ]
    assert pack.open_decisions(lines, "2026-10-04T00:00:00Z") == []


def test_compactions_in_reads_boundaries_from_an_offset(tmp_path):
    transcript = tmp_path / "s.jsonl"
    boundary = {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-10-05T04:31:57.813Z", "sessionId": "s", "compactMetadata": {"trigger": "auto", "preTokens": 566722, "postTokens": 38598}}
    transcript.write_text(json.dumps({"type": "user"}) + "\n" + json.dumps(boundary, separators=(",", ":")) + "\n")
    found, offset = pack.compactions_in(transcript, 0)
    assert found == [{"at": "2026-10-05T04:31:57.813Z", "session": "s", "trigger": "auto", "pre_tokens": 566722, "post_tokens": 38598}]
    assert offset == transcript.stat().st_size
    assert pack.compactions_in(transcript, offset) == ([], offset)


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
    assert [str(path.relative_to(tmp_path)) for path in inboxlines.inbox_files(tmp_path)] == ["deploy-go.md", "deploy-go.md.archive/2026-10-04.md"]


def test_a_leading_after_midnight_clock_keeps_its_meridiem():
    text = """\
lane-a (12:37 AM PT) DONE first
12:37 AM PT MECHANISM quoted Oct 4 at 7:52 PM
12:38 AM PT sandsql-handoff MECHANISM: timings
RELEASE lane-b (12:39 AM PT) receiver PASSED
"""
    end = datetime(2026, 10, 5, 7, 39, 14, tzinfo=UTC)
    lines = inboxlines.parse_inbox("deploy-go.md", text, [(4, end)])
    assert [line.at.astimezone(inboxlines.PACIFIC).strftime("%m-%d %H:%M") for line in lines] == ["10-05 00:37", "10-05 00:37", "10-05 00:38", "10-05 00:39"]
    assert lines[1].verb == "MECHANISM"


def test_owner_bullets_keep_their_lines_and_never_swallow_the_next(tmp_path):
    path = tmp_path / "asks.md"
    path.write_text("# asks\n- **Title only**\n- **Second**: detail\n")
    assert pack.owner_bullets(path) == [{"title": "Title only", "detail": "", "line": 2}, {"title": "Second", "detail": "detail", "line": 3}]


def test_the_inbox_records_an_anchor_each_time_a_file_grows(tmp_path):
    reader = inboxlines.Inbox(tmp_path / "inbox", tmp_path / "seen.json")
    assert reader.anchors("x.md", 3, MODIFIED) == [(3, MODIFIED), (3, MODIFIED)]
    later = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)
    assert reader.anchors("x.md", 5, later) == [(3, MODIFIED), (5, later), (5, later)]
    reloaded = inboxlines.Inbox(tmp_path / "inbox", tmp_path / "seen.json")
    assert reloaded.anchors("x.md", 5, later) == [(3, MODIFIED), (5, later), (5, later)]
    assert reloaded.anchors("x.md", 2, later) == [(2, later), (2, later)]


class Posting(Context):
    def json(self, argv, timeout=None, cwd=None):
        self.posted = argv
        return {"seq": 41, "at": "2026-10-06T07:00:00Z"}


def posting(tmp_path) -> Posting:
    return Posting(tmp_path, {"id": "d", "cci_drive": "release-v3"}, MODIFIED, 30.0)


def test_an_owner_action_posts_one_owner_record_to_main_and_remembers_it(tmp_path):
    ctx = posting(tmp_path)
    assert pack.acting("question")(ctx, {"cite": "task:7", "title": "grant actions:write"}, "still needed?") == "Question sent to main as cci #41"
    assert ctx.posted == ["cci", "post", "--drive", "release-v3", "--lane", "owner", "--kind", "owner", "--to", "main", "--text", "Question: grant actions:write [task:7] — still needed?", "--json"]
    assert pack.actions(tmp_path) == [{"cite": "task:7", "action": "question", "text": "still needed?", "at": "2026-10-06T07:00:00Z", "seq": 41}]


def test_a_long_item_is_clipped_so_the_owner_text_fits(tmp_path):
    ctx = posting(tmp_path)
    pack.acting("complete")(ctx, {"cite": "ask:a", "title": "x" * 600}, "")
    sent = ctx.posted[ctx.posted.index("--text") + 1]
    assert len(sent) == pack.CCI_TEXT and sent.startswith("Mark complete: xxx") and sent.endswith("… [ask:a]")


def test_a_question_or_reply_needs_a_note(tmp_path):
    with pytest.raises(ValueError, match="Reply needs a note"):
        pack.acting("reply")(posting(tmp_path), {"cite": "ask:a", "title": "x"}, "")


def test_the_lr_pack_registers_under_its_prefix():
    assert {"lr.needs-owner", "lr.inbox-feed", "lr.compactions", "lr.watches", "lr.drive"} <= set(registry.REGISTRY)
    assert sorted(registry.REGISTRY["lr.needs-owner"].actions) == ["complete", "question", "reply"]
