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


def job(**overrides: object) -> dict:
    base = {"order": "o1", "kind": "builder", "goal": "watch", "prs": [], "state": "running", "said": None, "updated": state.stamp(T0)}
    return {**base, **overrides}


@pytest.fixture
def demo(state_root: Path) -> str:
    data = state.new_drive("demo", "coordinator", "session-under-test", "/work", "D123")
    data["orders"]["o1"] = order()
    data["jobs"]["j1"] = job(prs=[state.pr_url(KEY)])
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


def fetch_outcomes(monkeypatch: pytest.MonkeyPatch, outcomes: list[bool]) -> list[list[str]]:
    calls, queue = [], list(outcomes)

    def fetch(keys: list[str]) -> dict[str, dict]:
        calls.append(keys)
        if queue.pop(0):
            return {KEY: read()}
        raise github.GithubError("HTTP 502")

    monkeypatch.setattr(github, "fetch", fetch)
    return calls


def test_failed_read_retries_next_poll_and_says_so_from_the_third_failure_in_a_row(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = fetch_outcomes(monkeypatch, [False] * 5)
    watching = watcher(demo)
    said = "WATCH github read failed {} times in a row: HTTP 502"
    assert [watch.run_pass(watching, T0 + timedelta(seconds=s)) for s in (0, 5, 10, 15)] == [[], [], [said.format(3)], []]
    assert len(calls) == 3
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=10, seconds=10)) == [said.format(5)]


def test_a_good_read_starts_the_failure_count_over(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_outcomes(monkeypatch, [False, True, False, False, False])
    watching = watcher(demo)
    lines = [watch.run_pass(watching, T0 + timedelta(seconds=s)) for s in (0, 5, 65, 70, 75)]
    assert lines == [[], [], [], [], ["WATCH github read failed 3 times in a row: HTTP 502"]]


def run_watch(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def stop(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(watch.time, "sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        watch.main(name)


def test_a_restarted_watch_replays_events_written_while_none_ran(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(monkeypatch, {})
    state.append_event(demo, "shown", repo="Forge-AI/monorepo", number=7)
    run_watch(demo, monkeypatch)
    assert capsys.readouterr().out == ""
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Approve")
    run_watch(demo, monkeypatch)
    assert capsys.readouterr().out == f"TAP Approve {KEY} [o1 j1]\n"
    assert state.watch_cursor(demo) == state.events_end(demo)
    run_watch(demo, monkeypatch)
    assert capsys.readouterr().out == ""


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


def test_tick_names_only_orders_with_news_since_their_last_line(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(state="waiting", last_line=None, updated=state.stamp(T0 + timedelta(minutes=2)))
        data["orders"]["o3"] = order(last_line=state.stamp(T0 + timedelta(minutes=15)), jobs=["j3"])
        data["jobs"]["j3"] = job(order="o3", updated=state.stamp(T0 + timedelta(minutes=16)))
        data["orders"]["o4"] = order(state="open", updated=state.stamp(T0 + timedelta(minutes=2)))
        data["orders"]["o5"] = order(jobs=["j5"], due=state.stamp(T0 + timedelta(minutes=45)))
        data["jobs"]["j5"] = job(order="o5")
        data["orders"]["o6"] = order(jobs=["j6"])
        data["jobs"]["j6"] = job(order="o6", updated=state.stamp(T0 + timedelta(minutes=3)))
        data["orders"]["o7"] = order(state="done", jobs=[], last_line=None, updated=state.stamp(T0 + timedelta(minutes=8)))
        data["orders"]["o8"] = order(state="done", jobs=[], last_line=None, updated=state.stamp(T0 - timedelta(hours=3)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0 + timedelta(minutes=29)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == ["TICK o2 o5 o6 o7"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=31)) == []


@pytest.mark.parametrize("owner", [{"heard": state.stamp(T0), "away": True}, {"heard": state.stamp(T0 - timedelta(minutes=40)), "away": False}])
def test_no_tick_while_he_is_away(demo: str, monkeypatch: pytest.MonkeyPatch, owner: dict) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["owner"] = owner
        data["jobs"]["j1"]["updated"] = state.stamp(T0 + timedelta(minutes=5))
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=30)) == []


def test_his_next_message_after_away_lists_the_orders_his_catch_up_covers(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with state.edit(demo) as data:
        data["owner"] = {"heard": state.stamp(T0), "away": True}
        data["jobs"]["j1"]["updated"] = state.stamp(T0 + timedelta(minutes=5))
        data["orders"]["o2"] = order(jobs=[], updated=state.stamp(T0 - timedelta(minutes=5)))
    serve(monkeypatch, {})
    watching = watcher(demo)
    monkeypatch.setattr(state, "now", lambda: T0 + timedelta(minutes=95))
    assert cli.main(["heard", "--drive", demo, "--thread", "1794.2"]) == 0
    said = capsys.readouterr().out.strip()
    assert said.startswith("CATCH-UP since ") and said.endswith(": o1")
    assert state.load(demo)["owner"] == {"heard": state.stamp(T0 + timedelta(minutes=95)), "away": False}
    assert watch.run_pass(watching, T0) == ["EVENT catch-up thread=1794.2 orders=o1"]
    assert cli.main(["heard", "--drive", demo, "--thread", "1794.3"]) == 0
    assert capsys.readouterr().out.strip() == "heard"
    assert watch.run_pass(watching, T0) == []


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


def test_only_the_coordinator_runs_the_watch(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with state.edit(demo) as data:
        data["roles"]["communicator"] = {"session": "talker", "cwd": "/work", "started": state.stamp(T0)}
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    assert watch.main(demo) == 0
    assert capsys.readouterr().out == ""
    assert not state.pid_path(demo).exists()


def test_no_drive_exits_quietly(state_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert watch.main(None) == 0
    assert capsys.readouterr().out == ""


def test_stale_pid_file_is_claimed_and_released(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    state.pid_path(demo).write_text("999999999\n")
    monkeypatch.setattr(watch, "run_pass", lambda watching, moment: [])
    run_watch(demo, monkeypatch)
    assert not state.pid_path(demo).exists()
