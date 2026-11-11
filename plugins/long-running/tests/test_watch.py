import json
import os
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import Thread, WatchProcess
from drive import cli, github, reads, render, slack, state, talk, watch

KEY = "Forge-AI/monorepo#42"
T0 = state.parse_time("2026-11-09T10:00:00-05:00")
GATE = "LTX Behavioral Gate (short)"
RUNNING = [{"name": "test", "conclusion": None}]
GREEN = [{"name": "test", "conclusion": "SUCCESS"}]
RED = [{"name": "test", "conclusion": "FAILURE"}]


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
    base = {"order": "o1", "kind": "builder", "goal": "watch", "prs": [], "branch": None, "waits": [], "questions": [], "state": "running", "said": None, "updated": state.stamp(T0)}
    return {**base, **overrides}


@pytest.fixture
def demo(state_root: Path) -> str:
    data = state.new_drive("demo", "coordinator", "session-under-test", "/work", "D123")
    data["orders"]["o1"] = order()
    data["jobs"]["j1"] = job(prs=[state.pr_url(KEY)])
    data["prs"][KEY] = state.new_pr(state.pr_url(KEY), "j1", "o1") | {"head": "aaaaaaa111", "title": "Old title"}
    state.create(data)
    return "demo"


def read(**overrides: object) -> dict:
    base = {
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": "aaaaaaa111",
        "mergeCommit": None,
        "checks": RUNNING,
        "reviewDecision": None,
        "mergeable": "MERGEABLE",
        "title": "Fix: the watch",
        "labels": [],
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


REAL_ENSURE = watch.ensure


@pytest.mark.parametrize("wants_tap", [False, True])
@pytest.mark.parametrize("job_state", ["running", "waiting"])
def test_a_prs_own_moves_wake_the_coordinator_only_when_it_is_green_and_its_job_is_not_running(
    demo: str, monkeypatch: pytest.MonkeyPatch, job_state: str, wants_tap: bool
) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = wants_tap
        data["jobs"]["j1"]["state"] = job_state
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks=GREEN, reviewDecision="APPROVED")})
    lines = watch.run_pass(watcher(demo), T0)
    assert lines == [
        f'PR {KEY}: new head bbbbbbb [o1 j1]',
        f'PR {KEY}: checks passed [o1 j1]',
        f'PR {KEY}: approved [o1 j1]',
    ] * (job_state == "waiting")
    pr = state.load(demo)["prs"][KEY]
    assert (pr["head"], pr["checks"], pr["review"], pr["title"]) == ("bbbbbbb222", "pass", "approved", "Fix: the watch")


def test_a_pr_that_starts_to_conflict_with_its_base_is_said_once_per_conflict(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    reads = {KEY: read(mergeable="CONFLICTING")}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    said = []
    for minute, mergeable in enumerate(["CONFLICTING", "CONFLICTING", "UNKNOWN", "CONFLICTING", "MERGEABLE", "CONFLICTING"]):
        reads[KEY] = read(mergeable=mergeable)
        said.append(watch.run_pass(watching, T0 + timedelta(minutes=2 * minute)))
    conflict = [f"PR {KEY}: conflicts with its base [o1 j1]"]
    assert said == [conflict, [], [], [], [], conflict]


def test_a_failed_check_being_rerun_never_reads_as_passed(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    reads = {KEY: read()}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    said = []
    for minute, gate in enumerate([[{"name": GATE, "conclusion": "FAILURE"}], [], [{"name": GATE, "conclusion": None}], [{"name": GATE, "conclusion": "FAILURE"}]]):
        reads[KEY] = read(checks=[*GREEN, *gate])
        said.append(watch.run_pass(watching, T0 + timedelta(minutes=minute)))
    failed = [f"PR {KEY}: checks failed [o1 j1]"]
    assert said == [failed, [], [], failed]
    assert state.load(demo)["prs"][KEY]["failed_checks"] == [GATE]


def test_unchanged_pr_emits_nothing(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {KEY: read()})
    assert watch.run_pass(watcher(demo), T0) == []


def test_merge_is_reported_and_the_pr_leaves_the_read_set(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = serve(monkeypatch, {KEY: read(state="MERGED", mergeCommit="ccc333")})
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == [f'PR {KEY}: merged [o1 j1]']
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


def test_a_restarted_watch_replays_events_written_while_none_ran(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    sent = posts(monkeypatch)
    state.append_event(demo, "shown", repo="Forge-AI/monorepo", number=7)
    run_watch(demo, monkeypatch)
    assert sent == []
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Approve")
    run_watch(demo, monkeypatch)
    assert posted(sent) == [("handoff", f"root@{demo}", f"TAP Approve {KEY} [o1 j1]")]
    assert state.watch_cursor(demo) == state.events_end(demo)
    run_watch(demo, monkeypatch)
    assert len(sent) == 1


def test_events_tail_from_the_offset_at_start(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    state.append_event(demo, "shown", repo="Forge-AI/monorepo", number=7)
    watching = watcher(demo)
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Approve")
    state.append_event(demo, "refused", repo="Forge-AI/monorepo", number=42, reason="stale")
    assert watch.run_pass(watching, T0) == [
        f"TAP Approve {KEY} [o1 j1]",
        "EVENT refused repo=Forge-AI/monorepo number=42 reason=stale [o1 j1]",
    ]
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []


def tooling_order(name: str, job_state: str) -> None:
    with state.edit(name) as data:
        data["orders"]["o1"]["text"] = "Drive tooling: the watch sends tooling lines to the communicator"
        data["jobs"]["j1"]["state"] = job_state


def posts(monkeypatch: pytest.MonkeyPatch, outcomes: list[bool] | None = None) -> list[list[str]]:
    sent, queue = [], list(outcomes or [])

    def post(argv: list[str], checkout: Path) -> str:
        if queue and not queue.pop(0):
            raise reads.ReadError("cci post exited 1: no drive demo")
        sent.append(argv)
        return ""

    monkeypatch.setattr(watch.reads, "run", post)
    return sent


def posted(sent: list[list[str]]) -> list[tuple[str, str, str]]:
    return [(argv[argv.index("--kind") + 1], argv[argv.index("--to") + 1], argv[argv.index("--text") + 1]) for argv in sent]


def test_a_drive_tooling_orders_lines_go_to_the_communicator_and_never_the_coordinator(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    tooling_order(demo, "waiting")
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks=RED)})
    sent = posts(monkeypatch)
    watching = watcher(demo)
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Approve")
    assert watch.run_pass(watching, T0) == []
    assert posted(sent) == [
        ("handoff", f"communicator@{demo}", line) for line in (f"PR {KEY}: checks failed [o1 j1]", f"TAP Approve {KEY} [o1 j1]")
    ]
    assert {argv[argv.index("--lane") + 1] for argv in sent} == {"watch"}


def test_a_tooling_prs_moves_while_its_builder_runs_are_the_builders_own(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    tooling_order(demo, "running")
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks=RED, state="MERGED", mergeCommit="ccc333")})
    sent = posts(monkeypatch)
    assert watch.run_pass(watcher(demo), T0) == []
    assert sent == []


def test_a_handoff_the_communicator_did_not_get_goes_again_and_says_so_from_the_third_failure(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    tooling_order(demo, "waiting")
    serve(monkeypatch, {KEY: read(checks=RED)})
    sent = posts(monkeypatch, [False, False, False, True])
    watching = watcher(demo)
    said = [watch.run_pass(watching, T0 + timedelta(seconds=s)) for s in (0, 5, 10, 15, 20)]
    failing = "WATCH handoff post to the communicator failed 3 times in a row: cci post exited 1: no drive demo"
    assert said == [[], [], [failing], [], []]
    assert [argv[-1] for argv in sent] == [f"PR {KEY}: checks failed [o1 j1]"]


def test_a_line_sent_him_wakes_no_one(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    watching = watcher(demo)
    state.append_event(demo, "said", text="shipped", thread=None, url="https://acme.slack.com/archives/D123/p1", card=None, order=None, visuals=[])
    assert watch.run_pass(watching, T0) == []


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


def said_due(watching: watch.Watcher, moment: datetime) -> list[str]:
    return [line for line in watch.run_pass(watching, moment) if line.startswith("DUE")]


def test_a_promised_time_is_said_every_ten_minutes_until_a_drive_post_is_in_its_thread_and_holds_the_order_open(
    demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: "Thread"
) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["orders"]["o1"].update(
            thread="1794200000.000100", due=state.stamp(T0 - timedelta(minutes=30)), promised=state.stamp(T0 - timedelta(minutes=60))
        )
        data["orders"]["o3"] = order(state="done", due=state.stamp(T0 - timedelta(hours=1)))
    slack_thread.post(T0 - timedelta(minutes=90), from_claude=True)
    slack_thread.post(T0 - timedelta(minutes=61), from_user=True)
    watching = watcher(demo)
    [line] = said_due(watching, T0)
    assert line.startswith('DUE o1 "ship the watch" was due ') and ": no line in its thread since " in line
    assert said_due(watching, T0 + timedelta(minutes=5)) == []
    assert said_due(watching, T0 + timedelta(minutes=10))[0].startswith("DUE o1")
    monkeypatch.setattr(state, "now", lambda: T0 + timedelta(minutes=11))
    assert cli.main(["order", "set", "o1", "--state", "done", "--drive", demo]) == 2
    assert cli.main(["order", "set", "o1", "--due", state.stamp(T0 + timedelta(hours=2)), "--drive", demo]) == 0
    assert said_due(watching, T0 + timedelta(minutes=20))[0].startswith("DUE o1")
    late_line = T0 + timedelta(minutes=21)
    slack_thread.post(late_line, from_claude=True)
    assert said_due(watching, T0 + timedelta(minutes=30)) == []
    moved = state.load(demo)["orders"]["o1"]
    assert (moved["due"], state.parse_time(moved["promised"]), moved["kept"]) == (state.stamp(T0 + timedelta(hours=2)), late_line, None)
    assert cli.main(["order", "set", "o1", "--state", "done", "--drive", demo]) == 2
    assert said_due(watching, T0 + timedelta(hours=2))[0].startswith("DUE o1")
    slack_thread.post(T0 + timedelta(hours=2, minutes=1), from_claude=True)
    assert cli.main(["order", "set", "o1", "--state", "done", "--drive", demo]) == 0
    assert state.load(demo)["orders"]["o1"]["state"] == "done"
    assert said_due(watching, T0 + timedelta(hours=3)) == []
    assert slack_thread.reads == [("--channel", "D123", "--thread", "1794200000.000100", "--limit", "50")] * 8


def test_a_drive_line_in_the_thread_of_the_orders_card_keeps_its_promised_time(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    card = f"{(T0 - timedelta(minutes=10)).timestamp():.6f}"
    threads: dict[str, list[dict]] = {"1794200000.000100": [], card: [{"ts": card, "from_claude": True}]}
    monkeypatch.setattr(slack, "thread", lambda *where: list(threads[where[3]]))
    with state.edit(demo) as data:
        data["orders"]["o1"].update(
            thread="1794200000.000100", due=state.stamp(T0 - timedelta(minutes=30)), promised=state.stamp(T0 - timedelta(minutes=60))
        )
        data["jobs"]["j1"]["questions"].append(state.new_question(None, None, state.stamp(T0), card=f"https://acme.slack.com/archives/D123/p{card.replace('.', '')}"))
    watching = watcher(demo)
    assert said_due(watching, T0)[0].startswith("DUE o1")
    threads[card].append({"ts": f"{(T0 + timedelta(minutes=5)).timestamp():.6f}", "thread_ts": card, "from_claude": True})
    assert said_due(watching, T0 + timedelta(minutes=10)) == []
    assert state.load(demo)["orders"]["o1"]["kept"] is not None


@pytest.mark.parametrize(
    ("source", "from_thread", "thread", "where"),
    [
        ("slack:C0B75AL4XEW/1794304978.209219", "1794304978.209219", "1794292846.190589", ("D123", "1794292846.190589")),
        ("slack:C0822AHFY3G/1794223506.402269", None, "1794223506.402269", ("C0822AHFY3G", "1794223506.402269")),
        ("slack:C0822AHFY3G/1794223506.402269", None, None, ("C0822AHFY3G", "1794223506.402269")),
        ("slack:C0CELUMJ6MU/1794341600.000200", "1794341529.595149", "1794341529.595149", ("C0CELUMJ6MU", "1794341529.595149")),
    ],
)
def test_a_due_order_from_another_channel_reads_its_thread_in_the_channel_the_thread_is_in(
    demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: "Thread", source: str, from_thread: str | None, thread: str | None, where: tuple[str, str]
) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["orders"]["o1"].update(
            {
                "from": source,
                "from_thread": from_thread,
                "thread": thread,
                "due": state.stamp(T0 - timedelta(minutes=30)),
                "promised": state.stamp(T0 - timedelta(minutes=60)),
            }
        )
    [line] = said_due(watcher(demo), T0)
    assert line.startswith('DUE o1 "ship the watch" was due ')
    assert slack_thread.reads == [("--channel", where[0], "--thread", where[1], "--limit", "50")]


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
        data["orders"]["o2"] = order(state="waiting", last_line=None, updated=state.stamp(T0 + timedelta(minutes=2)), jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2")
        data["orders"]["o3"] = order(last_line=state.stamp(T0 + timedelta(minutes=15)), jobs=["j3"])
        data["jobs"]["j3"] = job(order="o3", updated=state.stamp(T0 + timedelta(minutes=16)))
        data["orders"]["o4"] = order(state="open", updated=state.stamp(T0 + timedelta(minutes=2)), jobs=["j4"])
        data["jobs"]["j4"] = job(order="o4")
        data["orders"]["o5"] = order(jobs=["j5"], due=state.stamp(T0 + timedelta(minutes=45)))
        data["jobs"]["j5"] = job(order="o5")
        data["orders"]["o6"] = order(jobs=["j6"])
        data["jobs"]["j6"] = job(order="o6", updated=state.stamp(T0 + timedelta(minutes=3)))
        data["orders"]["o7"] = order(state="done", jobs=[], last_line=None, updated=state.stamp(T0 + timedelta(minutes=8)))
        data["orders"]["o8"] = order(state="done", jobs=[], last_line=None, updated=state.stamp(T0 - timedelta(hours=3)))
        data["orders"]["o9"] = order(text="Drive tooling: the watch sends tooling lines to the communicator", jobs=["j9"])
        data["jobs"]["j9"] = job(order="o9", updated=state.stamp(T0 + timedelta(minutes=3)))
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
        data["orders"]["o2"] = order(jobs=["j2"], updated=state.stamp(T0 - timedelta(minutes=5)))
        data["jobs"]["j2"] = job(order="o2", updated=state.stamp(T0 - timedelta(minutes=5)))
    serve(monkeypatch, {})
    watching = watcher(demo)
    monkeypatch.setattr(state, "now", lambda: T0 + timedelta(minutes=95))
    assert cli.main(["heard", "--drive", demo]) == 0
    said = capsys.readouterr().out.strip()
    assert said == f"CATCH-UP since {render.when(state.stamp(T0))}: o1"
    assert state.load(demo)["owner"] == {"heard": state.stamp(T0 + timedelta(minutes=95)), "away": False}
    assert watch.run_pass(watching, T0) == [f"EVENT catch-up dm=D123 since={render.when(state.stamp(T0))} orders=o1"]
    assert cli.main(["heard", "--drive", demo]) == 0
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
    state.pid_path(demo).write_text(f"{os.getppid()}\n")
    assert watch.main(demo) == 0
    assert capsys.readouterr().out == ""


def test_stale_pid_file_is_claimed_and_released(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    state.pid_path(demo).write_text("999999999\n")
    monkeypatch.setattr(watch, "run_pass", lambda watching, moment: [])
    run_watch(demo, monkeypatch)
    assert not state.pid_path(demo).exists()


def test_the_watch_waits_for_one_an_older_install_ran_under_a_monitor_and_sends_the_lines_it_held_once(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    passes = []
    monkeypatch.setattr(watch, "run_pass", lambda watching, moment: passes.append(moment) or [])
    sent = posts(monkeypatch)
    state.held_path(demo).write_text(f"PR {KEY}: approved [o1 j1]\n")
    state.monitor_pid_path(demo).write_text(f"{os.getppid()}\n")
    run_watch(demo, monkeypatch)
    assert (passes, sent) == ([], [])
    state.monitor_pid_path(demo).write_text("999999999\n")
    run_watch(demo, monkeypatch)
    run_watch(demo, monkeypatch)
    assert len(passes) == 2
    assert posted(sent) == [("evidence", f"root@{demo}", f"PR {KEY}: approved [o1 j1]")]


def test_a_crashed_watch_wakes_the_coordinator_before_it_ends(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(watching: watch.Watcher, moment: datetime) -> list[str]:
        raise OSError("disk full")

    monkeypatch.setattr(watch, "run_pass", crash)
    sent = posts(monkeypatch)
    with pytest.raises(OSError):
        watch.main(demo)
    assert posted(sent) == [("handoff", f"root@{demo}", "WATCH stopped: disk full; drive watch starts it again")]
    assert not state.pid_path(demo).exists()


def test_ensure_starts_the_watch_with_no_session_unless_one_runs(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    started = []

    class Started:
        pid = 4242

        def __init__(self, argv: list[str], **options: object) -> None:
            started.append((argv, options))

    monkeypatch.setattr(watch.subprocess, "Popen", Started)
    assert REAL_ENSURE(demo).startswith("started the watch as pid 4242;")
    [(argv, options)] = started
    assert argv == [str(watch.DRIVE), "watch", "--loop", "--drive", demo]
    assert "CLAUDE_CODE_SESSION_ID" not in options["env"] and options["start_new_session"]
    state.pid_path(demo).write_text(f"{os.getppid()}\n")
    assert REAL_ENSURE(demo).startswith(f"the watch runs as pid {os.getppid()} ")
    assert len(started) == 1


def test_drive_watch_starts_the_watch_and_points_only_the_coordinators_channel_at_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess, capsys: pytest.CaptureFixture[str]
) -> None:
    with state.edit(demo) as data:
        data["roles"]["communicator"] = {"session": "talker", "cwd": "/work", "started": state.stamp(T0)}
    assert cli.main(["watch", "--drive", demo]) == 0
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    assert cli.main(["watch", "--drive", demo]) == 0
    assert capsys.readouterr().out == f"the watch runs for {demo}\n" * 2
    assert (watch_process.ensured, watch_process.listened) == ([demo, demo], [(demo, "session-under-test")])


REAL_LOCAL_GAP = watch.local_gap
CARD = "https://acme.slack.com/archives/D123/p1794200000000100"


def files_order(data: dict, **overrides: object) -> None:
    data["orders"]["o2"] = order(text="build the files design", jobs=["j2"], **overrides)
    data["orders"]["o2"]["parts"] = {"stack 1": state.new_part(), "stack 2": state.new_part()}
    data["jobs"]["j2"] = job(order="o2", parts=["stack 1"])


def test_a_part_no_job_carries_is_an_orphan_five_minutes_after_its_order_moved_and_again_every_thirty_minutes(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        files_order(data)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == ['ORPHAN o2 "stack 2": no job']
    assert watch.run_pass(watching, T0 + timedelta(minutes=5, seconds=5)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=35)) == ['ORPHAN o2 "stack 2": no job']


def test_a_part_staffed_within_five_minutes_of_its_order_moving_is_never_an_orphan(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        files_order(data)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    with state.edit(demo) as data:
        data["jobs"]["j2"]["parts"].append("stack 2")
    assert watch.run_pass(watching, T0 + timedelta(seconds=30)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == []
    assert state.load(demo)["orders"]["o2"]["parts"]["stack 2"]["orphan_said"] is None


def test_due_names_every_part_nothing_carries(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        files_order(data, due=state.stamp(T0 + timedelta(minutes=10)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    lines = watch.run_pass(watching, T0 + timedelta(minutes=11))
    assert lines[0].startswith('DUE o2 "build the files design"')
    assert lines[1:] == ['ORPHAN o2 "stack 2": no job']


def test_a_part_another_orders_job_carries_is_no_orphan_until_that_job_is_done(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        files_order(data, updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == ['ORPHAN o2 "stack 2": no job']
    assert run("job", "set", "j1", "--carries", "o2", "--part", "stack 2")[0] == 0
    assert state.load(demo)["orders"]["o2"]["jobs"] == ["j2", "j1"]
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    with state.edit(demo) as data:
        state.take_result(data, "j1", "DONE carried it")
        data["orders"]["o2"]["updated"] = state.stamp(T0)
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == ['ORPHAN o2 "stack 2": no job']


def test_a_job_on_the_part_heals_the_orphan_once_its_handover_is_done(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        files_order(data, updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == ['ORPHAN o2 "stack 2": no job']
    with state.edit(demo) as data:
        data["jobs"]["j2"]["parts"].append("stack 2")
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    assert state.load(demo)["orders"]["o2"]["parts"]["stack 2"]["orphan_said"] == state.stamp(T0)
    assert watch.run_pass(watching, T0 + watch.HANDOFF) == []
    assert state.load(demo)["orders"]["o2"]["parts"]["stack 2"]["orphan_said"] is None


@pytest.mark.parametrize("kind", ["reader", "codex", "design"])
def test_a_done_builder_delivers_its_part_and_a_done_reader_codex_or_design_job_waits_for_the_posted_answer(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], slack_thread: "Thread", kind: str
) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="done", updated=earlier)
        data["orders"]["o2"] = order(text="why do old runs fail?", jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", kind=kind, state="done", updated=earlier)
    his = slack_thread.post(T0, from_user=True)
    answer = slack_thread.post(T0 + timedelta(seconds=1), from_claude=True)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == ['ORPHAN o2 "why do old runs fail?": j2 answered and the answer is not posted to him']
    assert cli.main(["order", "posted", "o2", answer, "--drive", demo]) == 2
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    assert cli.main(["start", demo, "--role", "communicator"]) == 0
    assert cli.main(["order", "posted", "o2", his, "--drive", demo]) == 2
    assert cli.main(["order", "posted", "o2", answer, "--drive", demo]) == 0
    capsys.readouterr()
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    part = state.load(demo)["orders"]["o2"]["parts"]["why do old runs fail?"]
    assert part == {"posted": answer, "orphan_said": None}


def test_a_waiting_job_lives_while_its_card_is_open_and_is_an_orphan_once_the_card_is_withdrawn(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    card = {"question": "Which store?", "options": ["Team", "Run"], "recommended": "Team", "asked": state.stamp(T0), "url": f"{CARD}?thread_ts=1.2", "card": {"title": "Which store?"}}
    monkeypatch.setattr(watch.view, "cards", lambda name: {"team_url": "", "questions": [card], "pr_cards": []})
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"].update(
            state="waiting", said="ASK which store?", questions=[state.new_question(None, "Which store?", state.stamp(T0), card=CARD)], updated=state.stamp(T0 - timedelta(minutes=10))
        )
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    assert watch.run_pass(watching, T0 + timedelta(seconds=60)) == [
        'ORPHAN o1 "ship the watch": j1 waits on "ASK which store?" and no open PR, card or due time can wake it'
    ]


def test_a_done_design_job_is_carried_by_its_open_card_and_its_part_needs_a_job_once_he_answers(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    card = {"question": "Which Mac owns a chat?", "options": ["First", "Last"], "recommended": "First", "asked": state.stamp(T0), "url": f"{CARD}?thread_ts=1.2", "card": {"title": "Which Mac owns a chat?", "waits": "demo:j2-7-a"}}
    monkeypatch.setattr(watch.view, "cards", lambda name: {"team_url": "", "questions": [card], "pr_cards": []})
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(text="options: only the owning machine writes", jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", kind="design", state="done", questions=[state.new_question(None, None, state.stamp(T0), card=CARD)], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    assert watch.run_pass(watching, T0 + timedelta(seconds=60)) == [
        'ORPHAN o2 "options: only the owning machine writes": j2\'s card is answered and no job carries his answer'
    ]
    with state.edit(demo) as data:
        data["orders"]["o2"]["jobs"].append("j3")
        data["jobs"]["j3"] = job(order="o2", kind="builder", parts=["options: only the owning machine writes"])
    assert watch.run_pass(watching, T0 + timedelta(seconds=120)) == []


def test_a_job_changed_in_the_last_two_minutes_is_still_being_handed_over(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"].update(state="waiting", said="ASK which store?")
    watching = watcher(demo)
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=2))[0].startswith('ORPHAN o1 "ship the watch": j1 waits on')


def test_a_running_job_whose_agent_is_gone_is_an_orphan_naming_its_open_pr(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(watch, "local_gap", lambda job_id, job, data, moment: f"{job_id}'s agent ended with an earlier session")
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=5)) == [
        'ORPHAN o1 "ship the watch": j1\'s agent ended with an earlier session; its PR Forge-AI/monorepo#42: checks pending, review none'
    ]


def write_transcript(config: Path, entries: list[dict], quiet: timedelta) -> None:
    path = config / "projects" / "-work" / "session-under-test" / "subagents" / "agent-a1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    stamp = (state.now() - quiet).timestamp()
    os.utime(path, (stamp, stamp))


FINISHED = {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "DONE shipped\nPR: x"}]}}
PARKED = {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Codex is still running. I'll wait for the notification."}]}}
CALLING = {"type": "assistant", "message": {"stop_reason": "tool_use", "content": [{"type": "tool_use", "name": "Bash"}]}}
RESULT = {"type": "user", "message": {"content": [{"type": "tool_result"}]}}


@pytest.mark.parametrize(
    ("entries", "quiet", "gap"),
    [
        ([CALLING, FINISHED], timedelta(minutes=10), 'j1\'s agent finished and its result is not recorded: "DONE shipped"'),
        ([FINISHED, {"type": "user", "message": {"content": "next event"}}], timedelta(minutes=10), None),
        ([CALLING, RESULT], timedelta(minutes=10), None),
        ([CALLING, RESULT], timedelta(minutes=45), "j1's agent has written nothing for 45 minutes"),
        ([CALLING, FINISHED], timedelta(seconds=30), None),
        ([CALLING, PARKED], timedelta(minutes=10), None),
        (
            [CALLING, PARKED],
            timedelta(minutes=45),
            'j1\'s agent ended its turn 45 minutes ago on "Codex is still running. I\'ll wait for the notification." with no result line: SendMessage wakes it',
        ),
    ],
)
def test_a_local_agent_is_judged_by_its_transcript(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: list[dict], quiet: timedelta, gap: str | None
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    write_transcript(tmp_path / "config", entries, quiet)
    job_of = {"session": "session-under-test", "agent": "a1", "kind": "builder"}
    assert REAL_LOCAL_GAP("j1", job_of, state.load(demo), state.now()) == gap


def test_a_local_agent_from_another_session_or_with_no_transcript_is_gone(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    data = state.load(demo)
    assert REAL_LOCAL_GAP("j1", {"session": "old", "agent": "a1"}, data, state.now()) == "j1's agent ended with an earlier session"
    assert REAL_LOCAL_GAP("j1", {"session": "session-under-test", "agent": "a1"}, data, state.now()) == "j1's agent a1 has no transcript"


def talking(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: list[dict], quiet: timedelta, heard: timedelta) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    path = tmp_path / "config" / "projects" / "-work" / "talker.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    stamp = (state.now() - quiet).timestamp()
    os.utime(path, (stamp, stamp))
    with state.edit(demo) as data:
        data["roles"]["communicator"] = {"session": "talker", "terminal": "term-7", "cwd": "/work", "started": state.stamp(T0)}
        data["owner"] = {"heard": state.stamp(state.now() - heard), "away": False}


def answered(tokens: int) -> dict:
    usage = {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": tokens - 10}
    return {"type": "assistant", "message": {"stop_reason": "end_turn", "usage": usage, "content": []}}


SUMMARY = {"type": "user", "isCompactSummary": True, "message": {"content": "summary"}}


@pytest.mark.parametrize(
    ("entries", "quiet", "heard", "why"),
    [
        ([answered(130_000)], timedelta(minutes=12), timedelta(minutes=20), "at 130000 tokens of context, quiet 10 minutes"),
        ([answered(130_000)], timedelta(minutes=12), timedelta(minutes=3), None),
        ([answered(130_000)], timedelta(minutes=4), timedelta(minutes=20), None),
        ([answered(60_000)], timedelta(minutes=12), timedelta(minutes=20), None),
        ([answered(130_000), SUMMARY], timedelta(minutes=12), timedelta(minutes=20), None),
        ([CALLING], timedelta(minutes=12), timedelta(minutes=20), None),
    ],
)
def test_the_communicator_compacts_only_past_the_mark_when_it_and_he_are_quiet(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: list[dict], quiet: timedelta, heard: timedelta, why: str | None
) -> None:
    talking(demo, tmp_path, monkeypatch, entries, quiet, heard)
    assert watch.compact_due(state.load(demo), state.now()) == why


def test_a_quiet_communicator_past_the_mark_gets_compact_typed_into_its_terminal(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    talking(demo, tmp_path, monkeypatch, [answered(130_000)], timedelta(minutes=12), timedelta(minutes=20))
    sent: list[tuple[str, ...]] = []
    monkeypatch.setattr(watch.orca, "request", lambda *args: sent.append(args) or {})
    assert watch.compact_lines(watcher(demo), state.now()) == [
        "WATCH compacted the communicator at 130000 tokens of context, quiet 10 minutes"
    ]
    assert sent == [("terminal", "send", "--terminal", "term-7", "--text", "/compact", "--enter")]


def test_a_communicator_outside_orca_is_never_compacted(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    talking(demo, tmp_path, monkeypatch, [answered(130_000)], timedelta(minutes=12), timedelta(minutes=20))
    with state.edit(demo) as data:
        data["roles"]["communicator"]["terminal"] = None
    assert watch.compact_due(state.load(demo), state.now()) is None


def worker(
    verdict: str = "live", outcome: str = "in_progress", attention: list[str] = (), quiet: timedelta = timedelta(), finished: timedelta = timedelta(minutes=10)
) -> dict:
    output = (T0 - quiet).timestamp() * 1000
    return {
        "dispatch": {"completedAt": state.stamp(T0 - finished)},
        "projection": {"liveness": {"verdict": verdict}, "outcome": outcome, "attention": {"categories": list(attention)}},
        "terminal": {"lastOutputAt": output},
    }


@pytest.mark.parametrize(
    ("shown", "gap"),
    [
        (worker(), None),
        (worker(outcome="succeeded"), "j4's lane succeeded and its result is not recorded"),
        (worker(outcome="succeeded", finished=timedelta(seconds=30)), None),
        (worker(attention=["input"], quiet=timedelta(minutes=5)), None),
        (worker(attention=["input"], quiet=timedelta(minutes=40)), "j4's lane has sat at its prompt for 40 minutes"),
    ],
)
def test_a_remote_lane_is_judged_by_its_dispatch(shown: dict, gap: str | None) -> None:
    assert watch.remote_gap("j4", shown, T0) == gap


def waiting_job(name: str) -> None:
    with state.edit(name) as data:
        data["jobs"]["j1"]["state"] = "waiting"


def test_a_pr_red_for_thirty_minutes_is_said(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    waiting_job(demo)
    serve(monkeypatch, {KEY: read(checks=RED)})
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == [f'PR {KEY}: checks failed [o1 j1]']
    assert watch.run_pass(watching, T0 + timedelta(minutes=29)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == [f'PR {KEY}: still red for 30 minutes [o1 j1]']
    assert watch.run_pass(watching, T0 + timedelta(minutes=31)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=300)) == []
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=301)) == []


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (lambda reads, data: reads.update({KEY: read(checks=RED, reviewDecision="CHANGES_REQUESTED")}), ["changes requested", "still red for 45 minutes"]),
        (lambda reads, data: data["jobs"]["j1"].update(state="done"), ["still red for 45 minutes"]),
        (lambda reads, data: data["prs"][KEY].update(tap={"label": "Hold", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "pressed"}), ["still red for 45 minutes"]),
        (lambda reads, data: data["jobs"]["j1"].update(state="running"), []),
    ],
)
def test_a_red_pr_is_said_again_on_each_change_and_only_then(
    demo: str, monkeypatch: pytest.MonkeyPatch, change: Callable[[dict, dict], None], said: list[str]
) -> None:
    waiting_job(demo)
    reads = {KEY: read(checks=RED)}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    pr = f'PR {KEY}:'
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == [f"{pr} still red for 30 minutes [o1 j1]"]
    with state.edit(demo) as data:
        change(reads, data)
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=45)) == [f"{pr} {line} [o1 j1]" for line in said]
    assert watch.run_pass(watching, T0 + timedelta(minutes=200)) == []


def waits_on(name: str, *waits: str) -> None:
    with state.edit(name) as data:
        data["prs"][KEY] |= {"wants_tap": True, "waits": list(waits)}


POSTED = "https://acme.slack.com/archives/D123/p1794200000000900"


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    posts: list[dict] = []
    monkeypatch.setattr(talk, "posted", lambda name, post: posts.append(post) or {"permalink": POSTED})
    return posts


def waiting(*cards: str) -> list[str]:
    since = render.when(state.stamp(T0))
    return [f"WAITING-ON-HIM {card} blocks j1, built on lane/built, since {since}, posted {POSTED}" for card in cards]


def paused(thread: str = "1.2") -> dict:
    return {"text": watch.PAUSED, "thread_ts": thread}


def question(waits: str) -> dict:
    return {"question": f"Pick {waits}?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(T0), "url": f"{CARD}?thread_ts=1.2", "card": {"title": f"Pick {waits}?", "waits": waits}}


def asking(monkeypatch: pytest.MonkeyPatch, name: str, *waits: str) -> None:
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question(waits) for waits in waits], "pr_cards": []})
    watch.view.refresh_cards(name)


def test_a_pr_waiting_on_his_open_cards_is_owed_no_card_and_is_freed_once_when_he_answers_the_last(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    waiting_job(demo)
    asking(monkeypatch, demo, "demo:j1-7-a", "demo:j1-7-b")
    waits_on(demo, "demo:j1-7-a", "demo:j1-7-b")
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == []
    asking(monkeypatch, demo, "demo:j1-7-b")
    assert watch.run_pass(watching, T0 + timedelta(minutes=31)) == []
    assert state.load(demo)["prs"][KEY]["waits"] == ["demo:j1-7-b"]
    asking(monkeypatch, demo)
    assert watch.run_pass(watching, T0 + timedelta(minutes=32)) == [f"FREE {KEY} j1 o1: demo:j1-7-b answered or done, nothing else holds it"]
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=33)) == []
    pr = f"PR {KEY}: green and approved with no card drawn for"
    assert watch.run_pass(watching, T0 + timedelta(minutes=62)) == [f"{pr} 30 minutes [o1 j1]"]


def test_a_red_pr_waiting_on_a_running_job_says_so_and_is_freed_when_the_job_ends(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j2"] = job(goal="real run")
    waits_on(demo, "j2")
    waiting_job(demo)
    serve(monkeypatch, {KEY: read(checks=RED)})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    pr = f"PR {KEY}:"
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == [f"{pr} still red for 30 minutes, waiting on j2 [o1 j1]"]
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=90)) == []
    with state.edit(demo) as data:
        data["jobs"]["j2"]["state"] = "done"
    assert watch.run_pass(watching, T0 + timedelta(minutes=120)) == [f"{pr} still red for 120 minutes [o1 j1]", f"FREE {KEY} j1 o1: j2 answered or done, nothing else holds it"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=121)) == []


def test_a_job_whose_wait_ends_is_freed_before_any_orphan_and_said_to_be_stranded_only_thirty_minutes_on(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    said = "WAITING release: #1 merged abc; rides the next release"
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j2"] = job(goal="release the service")
        data["jobs"]["j1"].update(state="waiting", said=said, waits=["j2"], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo, T0 + timedelta(hours=1))
    assert watch.run_pass(watching, T0) == []
    with state.edit(demo) as data:
        data["jobs"]["j2"]["state"] = "done"
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    assert watch.run_pass(watching, T0 + timedelta(seconds=60)) == ["FREE j1 o1: j2 answered or done, nothing else holds it"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=4)) == []
    with state.edit(demo) as data:
        data["jobs"]["j1"]["updated"] = state.stamp(T0 + timedelta(minutes=10))
    assert watch.run_pass(watching, T0 + timedelta(minutes=11)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=13)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=31)) == [
        f'ORPHAN o1 "ship the watch": j1 waits on "{said}" and no open PR, card or due time can wake it'
    ]


def release(number: int, published: str, components: str, name: str = "releases") -> dict:
    return {
        "name": f"{name}/2026-11-09/{number}",
        "tag_name": f"release/{name}-2026-11-09-{number}",
        "published_at": published,
        "draft": False,
        "prerelease": False,
        "body": f"Released from `c{number}` by [its build](https://ci.example/{number}).\n\nComponents: {components}",
    }


def test_a_job_waiting_on_a_release_is_freed_once_the_newest_release_shipping_its_service_holds_its_merged_pr(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    published = [release(1, "2026-11-09T14:00:00Z", "api, dashboard"), release(9, "2026-11-09T16:00:00Z", "api", name="rc")]
    statuses, calls = ["behind", "ahead"], []

    def gh(cmd: list[str]) -> str:
        calls.append(cmd[2])
        return json.dumps(published) if cmd[2].endswith("/releases?per_page=100") else f"{statuses.pop(0)}\n"

    monkeypatch.setattr(watch.github, "run", gh)
    wait = f"release:api:{KEY}"
    with state.edit(demo) as data:
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["jobs"]["j1"].update(state="waiting", said="WAITING release of api", waits=[wait], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo, T0 + timedelta(hours=1))
    assert watch.run_pass(watching, T0) == []
    assert calls[-1] == "repos/Forge-AI/monorepo/compare/abc123...release/releases-2026-11-09-1"
    published.append(release(2, "2026-11-09T15:00:00Z", "dashboard, api"))
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == [f"FREE j1 o1: release releases/2026-11-09/2 ships {KEY} on api, nothing else holds it"]
    assert calls[-1] == "repos/Forge-AI/monorepo/compare/abc123...release/releases-2026-11-09-2"
    assert state.load(demo)["jobs"]["j1"]["waits"] == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=2)) == []
    assert len(calls) == 4


def test_a_time_set_on_an_order_with_no_thread_is_said_once_and_kept_by_being_said(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    due = state.stamp(T0 - timedelta(minutes=1))
    with state.edit(demo) as data:
        data["orders"]["o1"].update(due=due, promised=state.stamp(T0 - timedelta(minutes=30)))
    watching = watcher(demo)
    assert said_due(watching, T0) == [f'DUE o1 "ship the watch" was due {render.when(due)}: {watch.UNTHREADED}']
    assert said_due(watching, T0 + timedelta(minutes=30)) == []
    assert state.load(demo)["orders"]["o1"]["kept"] == state.stamp(T0)


def test_a_built_job_is_told_once_under_each_card_it_waits_on_and_freed_once_when_he_answers_the_last(
    demo: str, monkeypatch: pytest.MonkeyPatch, told: list[dict]
) -> None:
    serve(monkeypatch, {})
    other = "https://acme.slack.com/archives/D123/p1794200000000300"
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question("demo:j1-7-a"), question("demo:j1-7-b") | {"url": other}], "pr_cards": []})
    watch.view.refresh_cards(demo)
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"] |= {"state": "built", "branch": "lane/built", "waits": ["demo:j1-7-a", "demo:j1-7-b"]}
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == waiting("demo:j1-7-a", "demo:j1-7-b")
    assert told == [paused(), paused("1794200000.000300")]
    asking(monkeypatch, demo, "demo:j1-7-a")
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []
    asking(monkeypatch, demo)
    assert watch.run_pass(watching, T0 + timedelta(minutes=2)) == ["FREE j1 o1: demo:j1-7-a answered or done, nothing else holds it, built on lane/built"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=3)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == []
    assert state.load(demo)["jobs"]["j1"]["waits"] == []


def test_a_built_job_asking_him_its_own_question_is_freed_only_once_he_answers_it_too(demo: str, monkeypatch: pytest.MonkeyPatch, told: list[dict]) -> None:
    serve(monkeypatch, {})
    asked = f"{CARD[:-1]}9"
    own = question("demo:j1-9") | {"url": asked}
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question("demo:j1-7-a"), own], "pr_cards": []})
    watch.view.refresh_cards(demo)
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"] |= {"state": "built", "branch": "lane/built", "waits": ["demo:j1-7-a"], "questions": [state.new_question("/asks/j1.md", "Pick?", state.stamp(T0), card=asked)]}
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == waiting("demo:j1-7-a")
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [own], "pr_cards": []})
    watch.view.refresh_cards(demo)
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []
    assert state.load(demo)["jobs"]["j1"]["waits"] == ["demo:j1-7-a"]
    asking(monkeypatch, demo)
    assert watch.run_pass(watching, T0 + timedelta(minutes=2)) == ["FREE j1 o1: demo:j1-7-a answered or done, nothing else holds it, built on lane/built"]


def test_a_card_holding_a_built_job_is_told_once_in_its_own_thread_and_again_after_a_failed_post(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    asking(monkeypatch, demo, "demo:j1-7-a")
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["orders"]["o1"]["thread"] = "1794200000.000100"
        data["jobs"]["j1"] |= {"state": "built", "branch": "lane/built", "waits": ["demo:j1-7-a"]}
    posts: list[dict] = []

    def post(name: str, sent: dict) -> dict:
        posts.append(sent)
        if len(posts) == 1:
            raise slack.SlackError("Slack is down")
        return {"permalink": POSTED}

    monkeypatch.setattr(talk, "posted", post)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == waiting("demo:j1-7-a")
    assert posts == [paused()] * 2
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=2)) == []
    assert state.load(demo)["jobs"]["j1"]["waiting_said"] == ["demo:j1-7-a"]


def test_pr_set_waits_adds_a_job_or_his_open_card_refuses_anything_else_and_no_waits_clears_them(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["pr", "set", KEY, "--waits", "j9", "--drive", demo]) == 2
    assert "--waits j9" in capsys.readouterr().err
    assert cli.main(["pr", "set", KEY, "--waits", "j1", "--drive", demo]) == 0
    assert capsys.readouterr().out.strip().endswith(", waits on j1")
    asking(monkeypatch, demo, "demo:j1-7-a")
    assert cli.main(["pr", "set", KEY, "--waits", "demo:gone-1", "--drive", demo]) == 2
    assert "--waits demo:gone-1" in capsys.readouterr().err
    assert cli.main(["pr", "set", KEY, "--waits", "demo:j1-7-a", "--waits", CARD, "--drive", demo]) == 0
    assert state.load(demo)["prs"][KEY]["waits"] == ["j1", "demo:j1-7-a", CARD]
    assert cli.main(["pr", "set", KEY, "--no-waits", "--drive", demo]) == 0
    assert state.load(demo)["prs"][KEY]["waits"] == []


def test_a_wait_added_to_a_pr_withdraws_his_open_card_and_a_tap_is_refused_while_it_holds(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asking(monkeypatch, demo, "demo:j1-7-a")
    withdrawn = []
    monkeypatch.setattr(cli.slack, "withdraw_pr_card", lambda url, why: withdrawn.append((url, why)) or ["1794200000.000200"])
    assert cli.main(["pr", "set", KEY, "--waits", "demo:j1-7-a", "--drive", demo]) == 0
    assert withdrawn == [(state.pr_url(KEY), "it waits on demo:j1-7-a before his tap")]
    assert cli.main(["pr", "set", KEY, "--tap", "--serves", "o1", "--design", "none", "--drive", demo]) == 2
    assert "it waits on demo:j1-7-a, so it draws no Approve" in capsys.readouterr().err
    assert state.load(demo)["prs"][KEY]["wants_tap"] is False


def test_a_job_waiting_on_his_open_card_puts_up_no_pr_until_he_answers(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asking(monkeypatch, demo, "demo:j1-7-a")
    url = "https://github.com/Forge-AI/monorepo/pull/43"
    with state.edit(demo) as data:
        state.take_result(data, "j1", "BUILT lane/built, waits on demo:j1-7-a")
    assert render.job_line("j1", state.load(demo)["jobs"]["j1"]) == "j1 builder built on lane/built: watch; waits on demo:j1-7-a"
    assert cli.main(["pr", "add", url, "--job", "j1", "--drive", demo]) == 2
    assert "j1 waits on his answer to demo:j1-7-a" in capsys.readouterr().err
    assert cli.main(["pr", "add", state.pr_url(KEY), "--job", "j1", "--drive", demo]) == 0
    asking(monkeypatch, demo)
    assert cli.main(["pr", "add", url, "--job", "j1", "--drive", demo]) == 0


def test_a_job_on_an_open_incident_puts_up_its_pr_on_the_recommendation_and_the_pr_takes_no_wait_on_his_card(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asking(monkeypatch, demo, "demo:j1-7-a")
    url = "https://github.com/Forge-AI/monorepo/pull/43"
    with state.edit(demo) as data:
        data["orders"]["o1"]["incident"] = True
        state.take_result(data, "j1", "BUILT lane/built, waits on demo:j1-7-a")
    assert cli.main(["pr", "add", url, "--job", "j1", "--drive", demo]) == 0
    assert cli.main(["pr", "set", url, "--waits", "demo:j1-7-a", "--drive", demo]) == 2
    assert "Forge-AI/monorepo#43 serves incident o1, whose fix lands on the recommended option while his card stays open" in capsys.readouterr().err
    assert cli.main(["pr", "set", url, "--waits", "j1", "--drive", demo]) == 0
    with state.edit(demo) as data:
        data["orders"]["o1"]["state"] = "done"
    assert cli.main(["pr", "set", url, "--waits", "demo:j1-7-a", "--drive", demo]) == 0


def test_a_job_waits_only_on_his_cards_so_his_tap_frees_it(demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    asking(monkeypatch, demo, "demo:j1-7-a")
    with state.edit(demo) as data:
        data["jobs"]["j2"] = job(goal="root fix", state="waiting")
    assert cli.main(["job", "set", "j2", "--waits", "j1", "--drive", demo]) == 2
    assert "--waits j1: a job waits only on a card of his" in capsys.readouterr().err
    assert cli.main(["job", "set", "j2", "--waits", "demo:j1-7-a", "--drive", demo]) == 0
    assert state.load(demo)["jobs"]["j2"]["waits"] == ["demo:j1-7-a"]


def test_a_green_approved_pr_that_wants_a_tap_with_no_card_for_thirty_minutes_is_said(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    waiting_job(demo)
    with state.edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    line = f"PR {KEY}: green and approved with no card drawn for 30 minutes [o1 j1]"
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == [line]
    assert watch.wakes(line, state.load(demo))


@pytest.mark.parametrize("wants_tap", [False, True])
def test_a_green_approved_exempt_pr_not_landed_for_thirty_minutes_is_said_and_wakes_the_coordinator(
    demo: str, monkeypatch: pytest.MonkeyPatch, wants_tap: bool
) -> None:
    waiting_job(demo)
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"wants_tap": wants_tap, "exempt": "incident fix"}
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    line = f"PR {KEY}: green, approved and exempt, and not landed for 30 minutes [o1 j1]"
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == [line]
    assert watch.wakes(line, state.load(demo))
    assert render.pr_line(KEY, state.load(demo)["prs"][KEY]).endswith("checks pass, review approved, no tap needed, exempt: incident fix")


def test_a_pr_waiting_on_another_prs_merge_is_freed_when_it_merges_while_that_prs_job_runs_on(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    other = "Forge-AI/monorepo#43"
    assert cli.main(["pr", "set", KEY, "--waits", other, "--drive", demo]) == 2
    assert f"--waits {other}: the drive tracks no PR {other}; drive pr add it to its job first" in capsys.readouterr().err
    with state.edit(demo) as data:
        data["jobs"]["j2"] = job(goal="fix the shm lock and prove it live")
        data["prs"][other] = state.new_pr(state.pr_url(other), "j2", "o1")
    assert cli.main(["pr", "set", KEY, "--waits", other, "--drive", demo]) == 0
    with state.edit(demo) as data:
        data["prs"][KEY]["exempt"] = "incident fix"
    waiting_job(demo)
    reads = {KEY: read(checks=GREEN, reviewDecision="APPROVED"), other: read()}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    reads[other] = read(state="MERGED", mergeCommit="ccc333")
    freed = f"FREE {KEY} j1 o1: {other} answered or done, nothing else holds it"
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == [f"PR {other}: merged [o1 j2]", freed]
    assert state.load(demo)["jobs"]["j2"]["state"] == "running"
    assert watch.wakes(freed, state.load(demo))


@pytest.mark.parametrize(("ended", "said"), [("MERGED", "merged"), ("CLOSED", "closed")])
def test_a_pr_that_wants_no_tap_is_never_said_to_have_no_card_and_only_its_failures_and_end_are_said(demo: str, monkeypatch: pytest.MonkeyPatch, ended: str, said: str) -> None:
    reads = {KEY: read(checks=GREEN, reviewDecision="APPROVED")}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    pr = f'PR {KEY}:'
    assert watch.run_pass(watching, T0) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=300)) == []
    reads[KEY] = read(checks=RED, reviewDecision="APPROVED")
    assert watch.run_pass(watching, T0 + timedelta(minutes=301)) == [f"{pr} checks failed [o1 j1]"]
    reads[KEY] = read(checks=GREEN, reviewDecision="APPROVED")
    assert watch.run_pass(watching, T0 + timedelta(minutes=302)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=400)) == []
    reads[KEY] = read(state=ended, checks=GREEN, reviewDecision="APPROVED")
    assert watch.run_pass(watching, T0 + timedelta(minutes=401)) == [f"{pr} {said} [o1 j1]"]


def hold(name: str) -> None:
    with state.edit(name) as data:
        data["prs"][KEY] |= {"wants_tap": True, "tap": {"label": "Hold", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "pressed the button"}}


def test_a_held_green_approved_pr_is_never_said_to_have_no_card(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    hold(demo)
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=300)) == []


def test_a_held_red_pr_is_said_once_until_its_head_moves_or_the_hold_lifts(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    hold(demo)
    waiting_job(demo)
    reads = {KEY: read(checks=RED)}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    still = f'PR {KEY}: still red for'
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == [f"{still} 30 minutes [o1 j1]"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=90)) == []
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=247)) == []
    reads[KEY] = read(checks=RED, headRefOid="bbbbbbb222")
    assert watch.run_pass(watching, T0 + timedelta(minutes=250)) == [f"{still} 250 minutes [o1 j1]"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=400)) == []
    with state.edit(demo) as data:
        data["prs"][KEY]["tap"] = None
    assert watch.run_pass(watching, T0 + timedelta(minutes=430)) == [f"{still} 430 minutes [o1 j1]"]


def test_running_build_reads_the_newest_request_shape_and_model(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    lines = [
        {"type": "api-request-shape", "version": "2.1.319"},
        {"type": "assistant", "message": {"model": "claude-opus-5-5"}},
        {"type": "api-request-shape", "version": "2.1.321"},
        {"type": "assistant", "message": {"model": "claude-fable-5-2"}},
        {"type": "user"},
    ]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    assert watch.running_build(path) == ("2.1.321", "claude-fable-5-2")


def test_unknown_model_flags_only_a_build_without_the_model(tmp_path: Path) -> None:
    (tmp_path / "2.1.319").write_bytes(b"claude-opus-5-5 claude-fable-5-1")
    (tmp_path / "2.1.321").write_bytes(b"claude-opus-5-5 claude-fable-5-2")
    assert watch.unknown_model("2.1.319", "claude-fable-5-2", tmp_path).startswith("runs claude-fable-5-2 on Claude Code 2.1.319, which has no entry")
    assert watch.unknown_model("2.1.321", "claude-fable-5-2", tmp_path) is None
    assert watch.unknown_model("2.1.319", "claude-opus-5-5", tmp_path) is None
    assert watch.unknown_model("2.1.400", "claude-fable-5-2", tmp_path) is None


def test_a_line_the_coordinator_acts_on_now_wakes_it_and_the_rest_wait_for_its_batch(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = posts(monkeypatch)
    waking = [
        f"TAP Approve {KEY} [o1 j1]",
        'DUE o1 "ship" was due 10:00 AM: no line in its thread since 9:00 AM',
        'ORPHAN o1 "ship": no job',
        'ANSWER a1 "one new tool": 1 card with no mark',
        "JOB j1 o1 READY https://github.com/Forge-AI/monorepo/pull/42",
        'LANE j1 o1 died on Sprite s1: gone, while it worked on "watch".',
        "EVENT catch-up dm=D123 since=9:00 AM orders=o1",
        "EVENT unheard question=Which? pick=Yes",
        "FREE j1 o1: demo:j1-7-a answered or done, nothing else holds it, built on lane/built",
    ]
    batched = [
        f"PR {KEY}: checks passed [o1 j1]",
        f"PR {KEY}: approved [o1 j1]",
        f"PR {KEY}: checks failed [o1 j1]",
        "EVENT note",
        "WAITING-ON-HIM demo:j1-7-a blocks j1, built on lane/built, since 9:00 AM, posted https://acme.slack.com/archives/D123/p1",
    ]
    assert watch.sent(watcher(demo), "root", waking + batched, T0) == []
    assert posted(sent) == [("handoff", f"root@{demo}", line) for line in waking] + [("evidence", f"root@{demo}", line) for line in batched]
    assert len({argv[argv.index("--topic") + 1] for argv in sent}) == len(sent)


@pytest.mark.parametrize(("job_state", "kind"), [("running", "evidence"), ("waiting", "handoff"), ("done", "handoff")])
def test_a_prs_merge_or_red_wakes_the_coordinator_only_when_no_running_job_takes_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, job_state: str, kind: str
) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"]["state"] = job_state
    sent = posts(monkeypatch)
    moves = [f"PR {KEY}: {move} [o1 j1]" for move in ("merged", "checks failed", "conflicts with its base", "changes requested", "still red for 30 minutes")]
    watch.sent(watcher(demo), "root", moves, T0)
    assert posted(sent) == [(kind, f"root@{demo}", line) for line in moves]


@pytest.mark.parametrize(
    ("wants_tap", "exempt", "kind"),
    [(True, "incident fix", "handoff"), (False, "incident fix", "handoff"), (True, None, "handoff"), (False, None, "evidence")],
)
def test_a_pr_going_green_and_approved_wakes_the_coordinator_when_the_drive_owes_it_his_card_or_its_exempt_landing(
    demo: str, monkeypatch: pytest.MonkeyPatch, wants_tap: bool, exempt: str | None, kind: str
) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"wants_tap": wants_tap, "exempt": exempt, "review": "approved"}
        data["jobs"]["j1"]["state"] = "waiting"
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    sent = posts(monkeypatch)
    watching = watcher(demo)
    assert watch.sent(watching, "root", watch.run_pass(watching, T0), T0) == []
    assert posted(sent) == [(kind, f"root@{demo}", f"PR {KEY}: checks passed [o1 j1]")]


def test_a_line_the_coordinator_did_not_get_goes_again_and_says_so_from_the_third_failure(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = posts(monkeypatch, [False, False, False, True])
    watching = watcher(demo)
    line = f"TAP Approve {KEY} [o1 j1]"
    said = [watch.sent(watching, "root", [line] if second == 0 else [], T0 + timedelta(seconds=second)) for second in (0, 5, 10, 15, 20)]
    assert said == [[], [], ["WATCH post to the coordinator failed 3 times in a row: cci post exited 1: no drive demo"], [], []]
    assert [argv[-1] for argv in sent] == [line]


def test_a_prs_move_resumes_the_remote_lane_waiting_on_it_and_reaches_the_coordinator_when_the_resume_fails(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(where="remote", dispatch="d-1", state="waiting")
    calls, outcomes = [], [True, False]

    def resume(argv: list[str], checkout: Path) -> str:
        calls.append(argv)
        if not outcomes.pop(0):
            raise reads.ReadError("orca exited 1")
        return ""

    monkeypatch.setattr(watch.reads, "run", resume)
    watching = watcher(demo)
    red, approved, merged = (f"PR {KEY}: {move} [o1 j1]" for move in ("checks failed", "approved", "merged"))
    assert watch.handed(watching, [red, approved], T0) == [approved]
    assert calls == [[str(watch.DRIVE), "job", "resume", "j1", red, "--drive", demo]]
    assert watch.handed(watching, [merged], T0) == [f"{merged}; drive job resume j1 failed: orca exited 1"]
    with state.edit(demo) as data:
        data["jobs"]["j1"]["state"] = "running"
    assert watch.handed(watching, [merged], T0) == [merged]
    assert len(calls) == 2


def test_an_answer_leaving_a_card_unmarked_says_so_at_once_every_ten_minutes_until_it_is_marked(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    asking(monkeypatch, demo, "demo:j1-7-a")
    with state.edit(demo) as data:
        data["answers"]["a1"] = {"words": "one new tool", "link": f"{CARD}9", "at": state.stamp(T0), "said": state.stamp(T0), "cards": {"demo:j1-7-a": None}}
    watching = watcher(demo)

    def said(minutes: int) -> list[str]:
        return [line for line in watch.run_pass(watching, T0 + timedelta(minutes=minutes)) if line.startswith("ANSWER")]

    line = 'ANSWER a1 "one new tool": 1 card with no mark, standing, superseded or reopened by it; drive answer a1 lists them'
    assert (said(5), said(10), said(15), said(20)) == ([], [line], [], [line])
    assert watch.wakes(line, state.load(demo))
    with state.edit(demo) as data:
        data["answers"]["a1"]["cards"]["demo:j1-7-a"] = {"mark": "stands"}
    assert said(40) == []


def test_an_open_incident_order_is_told_to_close_once_a_release_ships_its_fix_and_another_order_is_not(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    published = [release(2, "2026-11-09T15:00:00Z", "api")]
    monkeypatch.setattr(watch.github, "run", lambda cmd: json.dumps(published) if cmd[2].endswith("/releases?per_page=100") else "ahead\n")
    wait = f"release:api:{KEY}"
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(text="ship the dashboard", jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", state="waiting", said="WAITING release of api", waits=[wait], updated=state.stamp(T0 - timedelta(minutes=10)))
        data["orders"]["o1"]["incident"] = True
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["jobs"]["j1"].update(state="waiting", said="WAITING release of api", waits=[wait], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo, T0 + timedelta(hours=1))
    shipped = f"release releases/2026-11-09/2 ships {KEY} on api"
    live = f"LIVE o1: {shipped}, so its fix is live and the incident order is still open: close it with drive order set o1 --state done so the strip shows live incidents only"
    assert watch.run_pass(watching, T0) == [f"FREE j1 o1: {shipped}, nothing else holds it", f"FREE j2 o2: {shipped}, nothing else holds it", live]
    assert watch.wakes(live, state.load(demo))
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []


def test_an_incident_order_waiting_on_two_releases_is_told_its_fix_is_live_only_once_both_shipped(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    published = [release(2, "2026-11-09T15:00:00Z", "api")]
    monkeypatch.setattr(watch.github, "run", lambda cmd: json.dumps(published) if cmd[2].endswith("/releases?per_page=100") else "ahead\n")
    with state.edit(demo) as data:
        data["orders"]["o1"]["incident"] = True
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        waits = [f"release:api:{KEY}", f"release:worker:{KEY}"]
        data["jobs"]["j1"].update(state="waiting", said="WAITING releases of api and worker", waits=waits, updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo, T0 + timedelta(hours=1))
    assert watch.run_pass(watching, T0) == []
    assert state.load(demo)["jobs"]["j1"]["waits"] == [f"release:worker:{KEY}"]
    published.append(release(3, "2026-11-09T16:00:00Z", "worker"))
    shipped = f"release releases/2026-11-09/3 ships {KEY} on worker"
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == [
        f"FREE j1 o1: {shipped}, nothing else holds it",
        f"LIVE o1: {shipped}, so its fix is live and the incident order is still open: close it with drive order set o1 --state done so the strip shows live incidents only",
    ]


def pr_card(*keys: str, url: str = CARD) -> dict:
    return {"url": url, "prs": [state.pr_url(key) for key in keys], "card": {"title": "Fix: the watch"}}


@pytest.mark.parametrize(("ended", "said"), [("MERGED", "merged"), ("CLOSED", "closed")])
def test_his_open_pr_card_is_settled_in_place_once_every_pr_it_carries_merged_or_closed(
    demo: str, monkeypatch: pytest.MonkeyPatch, ended: str, said: str
) -> None:
    other = "Forge-AI/monorepo#43"
    with state.edit(demo) as data:
        data["prs"][other] = state.new_pr(state.pr_url(other), "j1", "o1") | {"head": "ddddddd444"}
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [], "pr_cards": [pr_card(KEY, other)]})
    settled = []
    monkeypatch.setattr(watch.slack, "withdraw_pr_card", lambda url, why: settled.append((url, why)) or [CARD])
    reads = {KEY: read(state=ended, mergeCommit="ccc333"), other: read(headRefOid="ddddddd444")}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == [f"PR {KEY}: {said} [o1 j1]"]
    assert settled == []
    reads[other] = read(state="MERGED", headRefOid="ddddddd444", mergeCommit="eee555")
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == [f"PR {other}: merged [o1 j1]", f"CARD {CARD} settled in place: {KEY} {said}, {other} merged"]
    assert settled == [(state.pr_url(KEY), f"{KEY} {said}, {other} merged")]


def test_a_merged_pr_settles_its_own_card_only_once_every_other_card_carrying_it_has_no_pr_open(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    other, stack = "Forge-AI/monorepo#43", "https://acme.slack.com/archives/D123/p1794200000000200"
    with state.edit(demo) as data:
        data["prs"][other] = state.new_pr(state.pr_url(other), "j1", "o1") | {"head": "ddddddd444"}
    shown = [pr_card(KEY), pr_card(KEY, other, url=stack)]
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [], "pr_cards": list(shown)})
    asked = []

    def withdraw(url: str, why: str) -> list[str]:
        gone = [card for card in shown if url in card["prs"]]
        shown[:] = [card for card in shown if card not in gone]
        asked.append((url, why))
        return [card["url"] for card in gone]

    monkeypatch.setattr(watch.slack, "withdraw_pr_card", withdraw)
    reads = {KEY: read(state="MERGED", mergeCommit="ccc333"), other: read(headRefOid="ddddddd444")}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == [f"PR {KEY}: merged [o1 j1]"]
    assert (asked, [card["url"] for card in shown]) == ([], [CARD, stack])
    reads[other] = read(state="MERGED", headRefOid="ddddddd444", mergeCommit="eee555")
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == [
        f"PR {other}: merged [o1 j1]",
        f"CARD {stack} settled in place: {KEY} merged, {other} merged",
        f"CARD {CARD} settled in place: {KEY} merged",
    ]
    assert (asked, shown) == ([(state.pr_url(other), f"{KEY} merged, {other} merged"), (state.pr_url(KEY), f"{KEY} merged")], [])


def test_a_job_clock_is_said_once_when_it_runs_out_while_its_job_still_runs_and_ends_unsaid_for_one_that_returned(demo: str) -> None:
    due = T0 + timedelta(minutes=10)
    with state.edit(demo) as data:
        data["jobs"]["j1"]["due"] = state.stamp(due)
        data["jobs"]["j2"] = job(goal="first check", state="done", due=state.stamp(due))
    watching = watcher(demo)
    assert watch.clock_lines(watching, due - timedelta(minutes=1)) == []
    line = (
        f'DUE j1 o1 "watch" was due {render.when(state.stamp(due))} and still runs: read its transcript, and on a miss start a second job'
        " on its brief and set its clock (incidents: Clocks)"
    )
    assert watch.clock_lines(watching, due) == [line]
    assert watch.clock_lines(watching, due + timedelta(minutes=10)) == []
    assert [job["due"] for job in state.load(demo)["jobs"].values()] == [None, None]
    assert watch.wakes(line, state.load(demo))
    tooling_order(demo, "running")
    assert watch.tooling(line, state.load(demo))


def test_a_job_clock_renewed_after_the_watch_read_it_is_kept_and_not_said(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    due, renewed = T0 + timedelta(minutes=10), state.stamp(T0 + timedelta(minutes=30))
    with state.edit(demo) as data:
        data["jobs"]["j1"]["due"] = state.stamp(due)
    watching, loaded, renewals = watcher(demo), state.load, []

    def renewing(name: str) -> dict:
        data = loaded(name)
        if not renewals:
            renewals.append(name)
            with state.edit(name) as fresh:
                fresh["jobs"]["j1"]["due"] = renewed
        return data

    monkeypatch.setattr(state, "load", renewing)
    assert watch.clock_lines(watching, due) == []
    assert state.load(demo)["jobs"]["j1"]["due"] == renewed
