"""Each fixture replays through this pack as its fix decides, the install gate refuses what the old pack never did, and recurrences are counted for a day after an install."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import replay

FIXTURES = sorted(replay.FIXTURES.glob("*.json"))
NOW = datetime(2026, 11, 1, 20, 0, tzinfo=UTC)
SECTION_LINE = re.compile(r'\s*(?:\[+[^\]]+\]+|"[^"]+":\s*\{|\},?)\s*$')


class Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def iso(at: datetime) -> str:
    return replay.iso(at)


@pytest.mark.parametrize("path", FIXTURES, ids=[path.stem for path in FIXTURES])
def test_each_fixture_replays_to_what_its_fix_decides(path: Path, tmp_path: Path):
    fixture = json.loads(path.read_text())
    given = tmp_path / "input.json"
    given.write_text(json.dumps(fixture["input"]))

    decided = replay.run_pack(replay.SCRIPTS, given)

    assert fixture["class"] == path.stem
    assert {key: decided[key] for key in fixture["expect"]} == fixture["expect"]


@pytest.mark.parametrize("path", FIXTURES, ids=[path.stem for path in FIXTURES])
def test_a_fixture_carries_only_what_the_checks_read(path: Path):
    """The fixtures live in a public repository, so a record keeps only the text the lanes mover reads and a patch only a manifest's or lockfile's shape."""
    given = json.loads(path.read_text())["input"]
    for record in given.get("records") or []:
        assert replay.scrubbed_text(record["text"]) == record["text"]
    for entry in given.get("prs") or []:
        for patch in filter(None, entry["patches"].values()):
            assert all(line == "@@" or line[1:] == "x" or SECTION_LINE.match(line[1:]) for line in patch.splitlines())


def test_scrubbing_keeps_the_lines_the_mover_reads_and_drops_the_prose():
    assert replay.scrubbed_text("SPAWNED takeover-500-tooling-fix /Users/x/.claude/scratch/drive/takeover-500-tooling/brief.md") == "SPAWNED takeover-500-tooling-fix /scratch/takeover-500-tooling/brief.md"
    assert replay.scrubbed_text("OUTCOME msg_585d takeover-500-tooling-fix: worker_done succeeded dispatch=ctx_ea33b9fa3728: Source fix merged; deploy owner-gated /Users/x/run") == "OUTCOME msg_585d takeover-500-tooling-fix: worker_done succeeded dispatch=ctx_ea33b9fa3728"
    assert replay.scrubbed_text("3:07 AM stand-down conflicts-2: false relaunch of a customer's lane") == "stand-down conflicts-2"
    assert replay.scrubbed_text("the customer cluster's api is down for SoFi") == ""


def test_a_row_reads_as_it_stood_at_each_tick():
    rows = [
        {"lane": "a", "live": False, "seen_at": iso(NOW), "announced_at": iso(NOW - timedelta(hours=2)), "stood_down": True},
        {"lane": "b", "live": True, "seen_at": iso(NOW), "announced_at": iso(NOW - timedelta(minutes=5)), "stood_down": False},
    ]
    orca = [("c", NOW - timedelta(hours=1), NOW - timedelta(minutes=30))]
    then = NOW - timedelta(hours=1)

    assert replay.who_at(rows, orca, {"a": NOW - timedelta(minutes=10)}, then) == [
        {"lane": "c", "live": True, "seen_at": iso(then)},
        {"lane": "a", "live": True, "seen_at": iso(then), "announced_at": iso(NOW - timedelta(hours=2)), "stood_down": False},
    ]
    assert replay.who_at(rows, orca, {"a": NOW - timedelta(minutes=10)}, NOW)[0] == {"lane": "c", "live": False, "seen_at": iso(NOW - timedelta(hours=1))}
    assert replay.who_at(rows, orca, {"a": NOW - timedelta(minutes=10)}, NOW)[1]["stood_down"] is True


def gate_args(**fields) -> argparse.Namespace:
    return argparse.Namespace(**{"drive": "d1", "repo": "o/r", "old": "0.7.820", "new": "0.7.821"} | fields)


def stub_gate(monkeypatch, old: dict, new: dict, added: dict[str, dict]) -> list[tuple]:
    posts: list[tuple] = []
    record = {"seq": 9, "lane": "desk-runner", "kind": "note", "at": iso(datetime.now(UTC)), "text": 'EXEMPT-ADMITTED #47425 at a1b2c3d4e5: fidelity grade cci #9 says "test-only or CI-only, no non-test code"', "refs": {"prs": [47425]}}
    monkeypatch.setattr(replay.cci, "paged", lambda run, drive, filters, since: [record])
    monkeypatch.setattr(replay, "who_rows", lambda drive: [])
    monkeypatch.setattr(replay, "changed_files", lambda repo, pr: {"go/go.mod": None})
    monkeypatch.setattr(replay, "run_pack", lambda scripts, given: new if "0.7.821" in str(scripts) else old)
    monkeypatch.setattr(replay, "classes", lambda version: added if version == "0.7.821" else {})
    monkeypatch.setattr(replay, "post", lambda *args: posts.append(args))
    return posts


def test_the_gate_refuses_a_version_that_relaunches_or_refuses_what_the_old_one_did_not(monkeypatch, capsys):
    posts = stub_gate(monkeypatch, {"relaunched": ["a"], "held": [], "refused": []}, {"relaunched": ["a", "b"], "held": [], "refused": [47425]}, {})

    assert replay.cmd_gate(gate_args()) == replay.REFUSED

    [(drive, kind, key, text, *attrs)] = posts
    assert (drive, kind, key) == ("d1", "blocker", "replay-refused:0.7.821")
    assert "relaunches b; refuses #47425's test-only claim, which 0.7.820 did not" in text and "relaunches a;" not in text
    assert "relaunches b" in capsys.readouterr().out


def test_the_gate_passes_a_version_that_decides_no_more_and_names_the_classes_it_adds(monkeypatch):
    fixture = {"defect": 293707, "fix": "yasyf/cc-skills#1"}
    posts = stub_gate(monkeypatch, {"relaunched": ["a"], "held": [], "refused": [47425]}, {"relaunched": [], "held": ["a"], "refused": []}, {"class-check-unclassed-file": fixture})

    assert replay.cmd_gate(gate_args()) == 0

    [(drive, kind, key, text, *attrs)] = posts
    assert (kind, key) == ("state", "replay-passed:0.7.821") and text.startswith(replay.PASSED)
    assert "class-check-unclassed-file (defect #293707, yasyf/cc-skills#1)" in text and attrs == ["--attr", "version=0.7.821", "--attr", "classes=class-check-unclassed-file"]


def test_recurrences_are_counted_once_the_24_hours_after_an_install_have_run(monkeypatch):
    installed = NOW - timedelta(hours=25)
    records = [
        {"seq": 1, "lane": "replay", "kind": "state", "at": iso(installed), "text": f"4:00 PM {replay.PASSED} 0.7.821 ...", "fields": {"attrs": {"version": "0.7.821", "classes": "supervisor-finished-lane"}}},
        {"seq": 2, "lane": "root", "kind": "stand-down", "at": iso(installed + timedelta(hours=3)), "text": "stand-down x-2: false relaunch"},
        {"seq": 3, "lane": "root", "kind": "note", "at": iso(installed + timedelta(hours=4)), "text": "false relaunch, noted"},
        {"seq": 4, "lane": "root", "kind": "defect", "at": iso(installed + timedelta(hours=24, minutes=30)), "text": "false relaunch again"},
        {"seq": 5, "lane": "replay", "kind": "state", "at": iso(NOW - timedelta(hours=2)), "text": f"{replay.PASSED} 0.7.822", "fields": {"attrs": {"version": "0.7.822", "classes": "supervisor-live-fix-alias"}}},
    ]
    posts: list[tuple] = []
    monkeypatch.setattr(replay.cci, "paged", lambda run, drive, filters, since: records)
    monkeypatch.setattr(replay, "post", lambda *args: posts.append(args))
    monkeypatch.setattr(replay, "datetime", Frozen)
    monkeypatch.setattr(replay, "classes", lambda version: {path.stem: json.loads(path.read_text()) for path in FIXTURES})

    assert replay.cmd_recur(argparse.Namespace(drive="d1")) == 0

    [(drive, kind, key, text, *flags)] = posts
    assert (kind, key) == ("report", "recurrence:0.7.821:supervisor-finished-lane")
    assert text == "RECURRENCE supervisor-finished-lane: 1 in the 24 hours after 0.7.821 installed (defect #283488, fix yasyf/cc-skills#1575): #2"
    assert flags == ["--attr", "class=supervisor-finished-lane", "--count", "recurrences=1"]
