import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from drive import cli, github, state, watch

KEY = "Forge-AI/monorepo#42"
T0 = state.parse_time("2026-11-09T10:00:00-05:00")


def order(**overrides: object) -> dict:
    base = {
        "text": "ship the watch",
        "from": "terminal",
        "thread": None,
        "state": "working",
        "due": None,
        "due_said": None,
        "jobs": ["j1"],
        "proof": None,
        "note": None,
        "created": state.stamp(T0),
        "updated": state.stamp(T0),
        "last_line": state.stamp(T0),
    }
    return {**base, **overrides}


@pytest.fixture
def demo(state_root: Path) -> str:
    data = state.new_drive("demo", "session-under-test", "/work", "D123")
    data["orders"]["o1"] = order()
    data["jobs"]["j1"] = {"order": "o1", "kind": "builder", "goal": "watch", "prs": [state.pr_url(KEY)]}
    data["prs"][KEY] = cli.new_pr(state.pr_url(KEY), "j1", "o1") | {"head": "aaaaaaa111", "title": "Old title"}
    state.create(data)
    return "demo"


def read(**overrides: object) -> dict:
    base = {
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": "aaaaaaa111",
        "mergeCommit": None,
        "checks": "PENDING",
        "reviewDecision": None,
        "title": "Fix: the watch",
    }
    return {**base, **overrides}


def serve(monkeypatch: pytest.MonkeyPatch, reads: dict[str, dict]) -> list[list[str]]:
    calls = []

    def fake(keys: list[str]) -> dict[str, dict]:
        calls.append(keys)
        return {key: reads[key] for key in keys if key in reads}

    monkeypatch.setattr(github, "fetch", fake)
    return calls


def watcher(name: str, moment: datetime = T0) -> watch.Watcher:
    return watch.Watcher(name, offset=state.events_end(name), ticked_at=moment)


def test_github_pass_emits_one_line_per_change_and_stores_fields(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks="SUCCESS", reviewDecision="APPROVED")})
    lines = watch.run_pass(watcher(demo), T0)
    assert lines == [
        f'PR {KEY} "Fix: the watch": new head bbbbbbb [o1 j1]',
        f'PR {KEY} "Fix: the watch": checks passed [o1 j1]',
        f'PR {KEY} "Fix: the watch": approved [o1 j1]',
    ]
    pr = state.load(demo)["prs"][KEY]
    assert (pr["head"], pr["checks"], pr["review"], pr["title"]) == ("bbbbbbb222", "pass", "approved", "Fix: the watch")


def test_unchanged_pr_emits_nothing(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {KEY: read()})
    assert watch.run_pass(watcher(demo), T0) == []


def test_merge_is_reported_and_the_pr_leaves_the_read_set(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = serve(monkeypatch, {KEY: read(state="MERGED", mergeCommit="ccc333")})
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == [f'PR {KEY} "Fix: the watch": merged [o1 j1]']
    assert state.load(demo)["prs"][KEY]["merged_sha"] == "ccc333"
    watch.run_pass(watching, T0 + timedelta(seconds=61))
    assert calls == [[KEY]]


def test_github_reads_at_most_once_a_minute(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = serve(monkeypatch, {KEY: read()})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    watch.run_pass(watching, T0 + timedelta(seconds=30))
    watch.run_pass(watching, T0 + timedelta(seconds=60))
    assert len(calls) == 2


def test_failed_read_reports_at_most_every_ten_minutes(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(keys: list[str]) -> dict:
        raise github.GithubError("HTTP 502")

    monkeypatch.setattr(github, "fetch", broken)
    with state.edit(demo) as data:
        data["orders"]["o1"]["state"] = "open"
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == ["WATCH github read failed: HTTP 502"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=10)) == ["WATCH github read failed: HTTP 502"]


def test_events_tail_from_the_offset_at_start(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    state.append_event(demo, "shown", repo="Forge-AI/monorepo", number=7)
    watching = watcher(demo)
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Approve")
    state.append_event(demo, "refused", repo="Forge-AI/monorepo", number=42, reason="stale")
    assert watch.run_pass(watching, T0) == [
        f"TAP Approve {KEY} [o1 j1]",
        "EVENT refused repo=Forge-AI/monorepo number=42 reason=stale",
    ]
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []


def test_partial_event_line_waits_for_its_newline(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    watching = watcher(demo)
    path = state.events_path(demo)
    with open(path, "a") as handle:
        handle.write(json.dumps({"kind": "note", "at": state.stamp(T0)}))
    assert watch.run_pass(watching, T0) == []
    with open(path, "a") as handle:
        handle.write("\n")
    assert watch.run_pass(watching, T0) == ["EVENT note"]


def test_due_fires_once_and_stamps_due_said(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["orders"]["o1"]["due"] = state.stamp(T0 - timedelta(minutes=30))
        data["orders"]["o2"] = order(text="later", due=state.stamp(T0 + timedelta(hours=1)))
        data["orders"]["o3"] = order(state="done", due=state.stamp(T0 - timedelta(hours=1)))
    watching = watcher(demo)
    lines = watch.run_pass(watching, T0)
    assert len(lines) == 1
    assert lines[0].startswith('DUE o1 "ship the watch" was due ')
    assert lines[0].endswith("9:30 AM")
    assert state.load(demo)["orders"]["o1"]["due_said"] == state.stamp(T0)
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []


def test_moved_due_fires_again(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["orders"]["o1"]["due"] = state.stamp(T0 + timedelta(minutes=20))
        data["orders"]["o1"]["due_said"] = state.stamp(T0 - timedelta(minutes=5))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=21))[0].startswith("DUE o1")


def test_tick_names_working_orders_without_a_recent_line(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(state="waiting", last_line=None)
        data["orders"]["o3"] = order(last_line=state.stamp(T0 + timedelta(minutes=5)))
        data["orders"]["o4"] = order(state="open")
    watching = watcher(demo)
    assert watch.run_pass(watching, T0 + timedelta(minutes=9)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=10)) == ["TICK o1 o2"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=11)) == []


def test_lines_are_capped_at_three_hundred_chars(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    watching = watcher(demo)
    state.append_event(demo, "unheard", question="x" * 500)
    assert len(watch.run_pass(watching, T0)[0]) == 300


def test_an_event_with_line_breaks_stays_one_line(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    watching = watcher(demo)
    state.append_event(demo, "unheard", question="first line\nsecond\r\nthird\rfourth\n\n" + "x" * 300)
    [line] = watch.run_pass(watching, T0)
    assert line.startswith("EVENT unheard question=first line second third fourth xxx")
    assert len(line) == 300
    assert "\n" not in line and "\r" not in line


def test_second_instance_exits_quietly(demo: str, capsys: pytest.CaptureFixture[str]) -> None:
    state.pid_path(demo).write_text(f"{os.getpid()}\n")
    assert watch.main(demo) == 0
    assert capsys.readouterr().out == ""


def test_no_drive_exits_quietly(state_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert watch.main(None) == 0
    assert capsys.readouterr().out == ""


def test_stale_pid_file_is_claimed_and_released(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    state.pid_path(demo).write_text("999999999\n")

    def stop(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(watch, "run_pass", lambda watching, moment: [])
    monkeypatch.setattr(watch.time, "sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        watch.main(demo)
    assert not state.pid_path(demo).exists()
