import json
import os
import plistlib
import re
import shlex
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import SHORT_ASK, CodexAsk, Draws, Judge, Redraws, Thread, WatchProcess, Withdrawals, commit_files, git_in, staged
from drive import card, cli, github, install, land, launchd, place, reads, render, served, slack, stage, state, superseded, talk, view, watch

KEY = "Forge-AI/monorepo#42"
OTHER = "Forge-AI/monorepo#43"
YASYF = "yasyf/cc-skills#7"
BASE_MOVED = "GraphQL: Base branch was modified. Review and try the merge again. (mergePullRequest)"
DIRTY = f"X Pull request {YASYF} is not mergeable: the merge commit cannot be cleanly created."
T0 = state.parse_time("2026-11-09T10:00:00-05:00")
GATE = "LTX Behavioral Gate (short)"
RUNNING = [{"name": "test", "conclusion": None}]
GREEN = [{"name": "test", "conclusion": "SUCCESS"}]
RED = [{"name": "test", "conclusion": "FAILURE"}]
ASKED = "Should Iris, desktop and the sand CLI show sign-in when an api session expires?"
REPORTED = "https://acme.slack.com/archives/C0REPORTS01/p1794400100000200"
UNRECORDED = 'ORPHAN o1 "ship the watch": j1\'s agent finished and its result is not recorded: "'
REPORT_IT = "; only drive job report j1 - sets it, or names why it refuses, so SendMessage from the session that started it wakes it to pipe its return there"
CHECKED = {"head": "aaaaaaa111", "orders": ["o1"], "patch": ""}
BUILD = "https://buildkite.com/forge/monorepo/builds/7"
REQUIRED = {"context": "buildkite/test", "targetUrl": BUILD}
INCIDENT_THREAD = "1794494004.895129"


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
    data["prs"][KEY] = state.new_pr(state.pr_url(KEY), "j1", "o1") | {"head": "aaaaaaa111", "title": "Old title", "wants_tap": False, "checked": CHECKED}
    state.create(data)
    return "demo"


def read(**overrides: object) -> dict:
    base = {
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": "aaaaaaa111",
        "baseRefName": "main",
        "headRefName": "the-watch",
        "mergeCommit": None,
        "checks": RUNNING,
        "reviewDecision": None,
        "approvals": [],
        "mergeable": "MERGEABLE",
        "title": "Fix: the watch",
        "author": "yasyf",
        "labels": [],
        "native": [],
        "required_checks": ["buildkite/test"],
        "required_reviews": 1,
    }
    return {**base, **overrides}


def serve(monkeypatch: pytest.MonkeyPatch, reads: dict[str, dict]) -> list[list[str]]:
    calls = []

    def fake(keys: list[str]) -> tuple[dict[str, dict], github.GithubError | None]:
        calls.append(keys)
        return {key: reads[key] for key in keys if key in reads}, None

    monkeypatch.setattr(github, "fetch", fake)
    return calls


def watcher(name: str, moment: datetime = T0) -> watch.Watcher:
    return watch.Watcher(name, offset=state.events_end(name), ticked_at=moment)


@pytest.fixture(autouse=True)
def ready_records(monkeypatch: pytest.MonkeyPatch) -> list[list[dict]]:
    from drive import tools

    sent: list[list[dict]] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: sent.append(records) or ["#9"])
    return sent


@pytest.fixture(autouse=True)
def closed_cards(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str, bool]]:
    closes: list[tuple[str, str, str, bool]] = []
    monkeypatch.setattr(superseded, "answer_card", lambda card, words, link, settled: closes.append((card, words, link, settled)) or {})
    return closes


REAL_ENSURE = watch.ensure
REAL_FETCH = github.fetch
REAL_LISTEN = watch.listen


@pytest.mark.parametrize("wants_tap", [False, True])
@pytest.mark.parametrize("job_state", ["running", "waiting"])
def test_a_prs_moves_are_said_whatever_its_jobs_state_and_wake_no_one_while_the_watch_draws_his_card_on_one_waiting_on_his_tap(
    demo: str, monkeypatch: pytest.MonkeyPatch, job_state: str, wants_tap: bool, draws: Draws
) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = wants_tap
        data["jobs"]["j1"]["state"] = job_state
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks=GREEN, reviewDecision="APPROVED")})
    lines = watch.run_pass(watcher(demo), T0)
    assert lines == [f"PR {KEY}: new head bbbbbbb [o1 j1]", f"PR {KEY}: checks passed [o1 j1]", f"PR {KEY}: approved [o1 j1]"]
    assert not any(watch.wakes(line, state.load(demo)) for line in lines)
    pr = state.load(demo)["prs"][KEY]
    assert (pr["head"], pr["checks"], pr["review"], pr["title"]) == ("bbbbbbb222", "pass", "approved", "Fix: the watch")
    assert (draws.keys, pr["card"], pr["stage"]["name"]) == ([[KEY]] * wants_tap, draws.link if wants_tap else None, "uncarded" if wants_tap else "author")


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


def test_a_builders_pushed_wakes_no_one_until_the_first_read_after_it_finds_the_required_checks_green(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"PUSHED {state.pr_url(KEY)}")
    reads = {KEY: read(checks=[*RUNNING, {**REQUIRED, "state": "SUCCESS"}])}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    said = [watch.run_pass(watching, T0)]
    reads[KEY] = read(checks=[*RUNNING, {**REQUIRED, "state": "PENDING"}])
    said.append(watch.run_pass(watching, T0 + timedelta(minutes=1)))
    reads[KEY] = read(checks=[*RUNNING, {**REQUIRED, "state": "SUCCESS"}])
    said.append(watch.run_pass(watching, T0 + timedelta(minutes=2)))
    ready = f"JOB j1 o1 READY {state.pr_url(KEY)}; {KEY} is at aaaaaaa on GitHub, grading, owed by Buildkite and forge-pr-reviewer: its checks run at aaaaaaa"
    assert said == [[], [], [ready]]
    data = state.load(demo)
    assert (watch.wakes(ready, data), data["jobs"]["j1"]["state"], data["jobs"]["j1"]["said"]) == (True, "waiting", f"READY {state.pr_url(KEY)}")


def test_a_pushed_waits_while_the_check_its_rules_require_has_not_posted_on_its_head_and_settles_once_it_passes(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"PUSHED {state.pr_url(KEY)}")
    reads = {KEY: read(checks=RUNNING)}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    said = [watch.run_pass(watching, T0 + timedelta(minutes=1))]
    reads[KEY] = read(checks=[*RUNNING, {**REQUIRED, "state": "PENDING"}])
    said.append(watch.run_pass(watching, T0 + timedelta(minutes=2)))
    reads[KEY] = read(checks=[*RUNNING, {**REQUIRED, "state": "SUCCESS"}])
    said.append(watch.run_pass(watching, T0 + timedelta(minutes=3)))
    assert said == [[], [], [f"JOB j1 o1 READY {state.pr_url(KEY)}; {KEY} is at aaaaaaa on GitHub, grading, owed by Buildkite and forge-pr-reviewer: its checks run at aaaaaaa"]]


def test_a_pushed_on_a_repository_whose_rules_require_no_check_settles_at_its_first_read_beside_its_own_red_check(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    url = state.pr_url(YASYF)
    with state.edit(demo) as data:
        data["prs"][YASYF] = state.new_pr(url, "j1", "o1") | {"head": "aaaaaaa111", "wants_tap": False, "checked": CHECKED}
        data["jobs"]["j1"].update(state="waiting", said=f"PUSHED {url}", prs=[url])
    serve(monkeypatch, {YASYF: read(checks=RED, required_checks=[])})
    said = watch.run_pass(watcher(demo), T0 + timedelta(minutes=1))
    assert f"PR {YASYF}: checks failed [o1 j1]" in said
    assert [line for line in said if line.startswith(f"JOB j1 o1 READY {url}; {YASYF} is at aaaaaaa on GitHub, red, owed by its builder: its checks fail")]
    assert state.load(demo)["jobs"]["j1"]["said"] == f"READY {url}"


def test_a_red_required_check_ends_a_pushed_with_its_build_and_its_red_stage_wakes_its_builder_not_the_coordinator(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"PUSHED {state.pr_url(KEY)}")
    serve(monkeypatch, {KEY: read(checks=[{**REQUIRED, "state": "FAILURE"}])})
    watching = watcher(demo)
    refused = f"PR {KEY}: READY refused, its required buildkite/test failed at aaaaaaa ({BUILD}) [o1 j1]"
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == [f"PR {KEY}: checks failed [o1 j1]", refused]
    assert watch.run_pass(watching, T0 + timedelta(minutes=2)) == []
    data = state.load(demo)
    assert (watch.wakes(refused, data), data["jobs"]["j1"]["said"]) == (False, f"READY refused: {KEY}'s required buildkite/test failed at aaaaaaa ({BUILD})")
    assert [(key, text.split(", owed by")[0]) for key, text in woken] == [(KEY, f"{KEY} red since {state.stamp(T0 + timedelta(minutes=1))}")]


def test_a_ready_waits_on_the_pr_it_names_and_its_job_takes_done_once_that_product_pr_merges_with_no_free_or_wake(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], ready_records: list[list[dict]]
) -> None:
    serve(monkeypatch, {KEY: read(state="MERGED", mergeCommit="ccc333")})
    with state.edit(demo) as data:
        state.take_result(data, "j1", f"READY {state.pr_url(KEY)}")
    assert state.load(demo)["jobs"]["j1"]["waits"] == [KEY]
    watching = watcher(demo)
    said = watch.run_pass(watching, T0) + watch.run_pass(watching, T0 + timedelta(seconds=61))
    assert f"PR {KEY}: merged [o1 j1]" in said
    assert not [line for line in said if line.startswith(("FREE ", "ORPHAN "))]
    data = state.load(demo)
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["said"], data["jobs"]["j1"]["waits"]) == ("done", f"DONE {state.pr_url(KEY)} merged: {KEY} at ccc333", [])
    assert not watch.wakes(f"PR {KEY}: merged [o1 j1]", data)
    assert [record["attr"] for records in ready_records for record in records if record.get("topic") == "merged-o1"] == [{"close": "o1"}]
    assert provers == []


def test_a_job_waiting_on_a_builders_change_waits_on_its_first_pr_and_the_watch_frees_it_once_that_pr_merges_and_its_line_names_the_wait_no_more(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = "Forge-AI/monorepo#43"
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["jobs"]["j2"] = job(goal="build the store")
        data["jobs"]["j3"] = job(goal="build on the store")
    assert cli.main(["job", "set", "j3", "--waits", "j2", "--drive", demo]) == 0
    assert (state.load(demo)["jobs"]["j3"]["state"], state.load(demo)["jobs"]["j3"]["said"]) == ("waiting", "waits on j2")
    watching = watcher(demo)
    assert not [line for line in watch.run_pass(watching, T0) if line.startswith("FREE ")]
    assert cli.main(["pr", "add", state.pr_url(built), "--job", "j2", "--drive", demo]) == 0
    assert cli.main(["pr", "add", state.pr_url(built), "--job", "j2", "--drive", demo]) == 0
    jobs = state.load(demo)["jobs"]
    assert (jobs["j3"]["waits"], jobs["j3"]["said"], jobs["j1"]["waits"]) == ([built], f"waits on {built}", [])
    serve(monkeypatch, {built: read(state="MERGED", mergeCommit="ccc333")})
    said = watch.run_pass(watching, T0 + timedelta(seconds=61)) + watch.run_pass(watching, T0 + timedelta(seconds=122))
    assert [line for line in said if line.startswith("FREE ")] == [f"FREE j3 o1: {built} answered or done, nothing else holds it"]
    assert (state.load(demo)["jobs"]["j3"]["waits"], state.load(demo)["jobs"]["j3"]["said"]) == ([], None)


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

    def fetch(keys: list[str]) -> tuple[dict[str, dict], github.GithubError | None]:
        calls.append(keys)
        return ({KEY: read()}, None) if queue.pop(0) else ({}, github.GithubError("HTTP 502"))

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


def test_a_refused_query_keeps_the_reads_of_the_others_and_a_finished_pr_is_never_read_again(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(demo) as data:
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"head": "aaaaaaa111", "wants_tap": False}
    calls = []

    def other_refused(keys: list[str]) -> tuple[dict[str, dict], github.GithubError | None]:
        calls.append(keys)
        reads = {KEY: read(state="MERGED", mergeCommit="ccc333")} if KEY in keys else {}
        return reads, github.GithubError("could not read 1 PRs of Forge-AI/monorepo from GitHub: gh: HTTP 502")

    monkeypatch.setattr(github, "fetch", other_refused)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == [f"PR {KEY}: merged [o1 j1]"]
    assert [watch.run_pass(watching, T0 + timedelta(seconds=s)) for s in (5, 10)] == [
        [],
        ["WATCH github read failed 3 times in a row: could not read 1 PRs of Forge-AI/monorepo from GitHub: gh: HTTP 502"],
    ]
    assert calls == [[KEY, OTHER], [OTHER], [OTHER]]


def launched(monkeypatch: pytest.MonkeyPatch, name: str, project: str | None = None) -> None:
    monkeypatch.setenv(launchd.LAUNCHED_ENV, watch.label(name, project))


def run_watch(name: str, monkeypatch: pytest.MonkeyPatch, project: str | None = None) -> None:
    def stop(seconds: float) -> None:
        raise KeyboardInterrupt

    launched(monkeypatch, name, project)
    monkeypatch.setattr(watch.time, "sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        watch.main(name, project)


def test_a_restarted_watch_replays_events_written_while_none_ran(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    sent = posts(monkeypatch)
    state.append_event(demo, "shown", repo="Forge-AI/monorepo", number=7)
    run_watch(demo, monkeypatch)
    assert sent == []
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Hold")
    run_watch(demo, monkeypatch)
    assert posted(sent) == [("handoff", f"root@{demo}", f"TAP Hold {KEY} [o1 j1]")]
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


def tooling_order(name: str, job_state: str, text: str = "Drive tooling: the watch sends tooling lines to the meta session", owner: str = "meta") -> None:
    with state.edit(name) as data:
        data["orders"]["o1"].update(text=text, owner=owner)
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


def test_the_watch_tells_the_communicator_once_each_time_the_coordinators_process_exits(
    demo: str, monkeypatch: pytest.MonkeyPatch, live_sessions: dict[str, int | None]
) -> None:
    serve(monkeypatch, {KEY: read()})
    sent = posts(monkeypatch)
    watching = watcher(demo)
    down = (
        "handoff",
        f"communicator@{demo}",
        "DOWN the coordinator's Claude Code process has exited, so nothing hears the drive's records: restart it now, with no word"
        " from him, by steps 5 to 7 of drive manual migration, Into an Orca terminal, creating the terminal resuming"
        " session-under-test in /work, and tell him it was restarted, never that it is down",
    )
    for moment, pid in enumerate((None, None, 4242, None)):
        live_sessions["session-under-test"] = pid
        watch.run_pass(watching, T0 + moment * watch.READ_EVERY)
    assert [entry for entry in posted(sent) if entry[1] == f"communicator@{demo}"] == [down, down]


def test_the_watch_hands_a_dashboard_no_launch_agent_keeps_to_one_and_tells_the_coordinator_once_each_time_it_does_not_start(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    kept = iter([False, True, False, False, True, False])
    starts: list[tuple[str, int]] = []

    def start(name: str, port: int) -> list[str]:
        starts.append((name, port))
        if len(starts) > 1:
            raise state.DriveError("the dashboard did not start; see its log")
        return []

    monkeypatch.setattr(watch.launchd, "kept", lambda label: next(kept))
    monkeypatch.setattr(watch.dashboard, "start", start)
    watching = watcher(demo)
    said = [watch.dashboard_lines(watching) for _ in range(6)]
    log = state.drive_dir(demo) / "dashboard.log"
    down = f"WATCH no launch agent kept the dashboard, and it did not start under one: the dashboard did not start; see its log; its log is {log}"
    assert said == [[], [], [down], [], [], [down]]
    assert starts == [(demo, watch.dashboard.port_of(demo))] * 4


def test_the_lines_of_an_order_the_communicator_holds_go_to_it_and_never_the_coordinator_and_a_prs_moves_go_to_no_holder(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    tooling_order(demo, "waiting", "I told him since you never did, fix that", "communicator")
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks=RED)})
    sent = posts(monkeypatch)
    watching = watcher(demo)
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Approve")
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Hold")
    assert watch.run_pass(watching, T0) == []
    assert posted(sent) == [("handoff", f"communicator@{demo}", f"TAP Hold {KEY} [o1 j1]")]
    assert {argv[argv.index("--lane") + 1] for argv in sent} == {"watch"}
    assert [key for key, _ in woken] == [KEY]


def test_the_lines_of_an_order_the_meta_session_holds_go_to_it_and_never_the_coordinator(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    tooling_order(demo, "running")
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks=RED)})
    sent = posts(monkeypatch)
    watching = watcher(demo)
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Hold")
    assert watch.run_pass(watching, T0) == []
    assert posted(sent) == [("handoff", f"meta@{demo}", f"TAP Hold {KEY} [o1 j1]")]


def test_a_handoff_the_meta_session_did_not_get_goes_again_and_says_so_from_the_third_failure(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    tooling_order(demo, "waiting")
    serve(monkeypatch, {KEY: read()})
    sent = posts(monkeypatch, [False, False, False, True])
    watching = watcher(demo)
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Hold")
    said = [watch.run_pass(watching, T0 + timedelta(seconds=s)) for s in (0, 5, 10, 15, 20)]
    failing = "WATCH handoff post to the meta session failed 3 times in a row: cci post exited 1: no drive demo"
    assert said == [[], [], [failing], [], []]
    assert [argv[-1] for argv in sent] == [f"TAP Hold {KEY} [o1 j1]"]


def test_a_line_sent_him_or_his_answer_kept_for_history_wakes_no_one(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    watching = watcher(demo)
    state.append_event(demo, "said", text="shipped", thread=None, url="https://acme.slack.com/archives/D123/p1", card=None, order=None, visuals=[])
    for kind in ("answered", "undone"):
        state.append_event(demo, kind, url="https://acme.slack.com/archives/D123/p2", label="Ring", question="Ring or knock?")
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
            thread=["D123", "1794200000.000100"], due=state.stamp(T0 - timedelta(minutes=30)), promised=state.stamp(T0 - timedelta(minutes=60))
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
    assert slack_thread.reads == [
        ("--channel", "D123", "--thread", "1794200000.000100", "--since-ts", str(int(promised.timestamp())), "--limit", "1000")
        for promised in [T0 - timedelta(minutes=60)] * 5 + [late_line] * 3
    ]


def test_a_drive_line_keeps_its_promised_time_however_many_of_his_messages_follow_it_in_the_thread(
    demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: "Thread"
) -> None:
    serve(monkeypatch, {})
    promised = T0 - timedelta(minutes=60)
    with state.edit(demo) as data:
        data["orders"]["o1"].update(thread=["D123", "1794200000.000100"], due=state.stamp(T0 - timedelta(minutes=30)), promised=state.stamp(promised))
    slack_thread.post(promised + timedelta(minutes=1), from_claude=True)
    for second in range(60):
        slack_thread.post(promised + timedelta(minutes=2, seconds=second), from_user=True)

    def read(*where: str) -> list[dict]:
        asked = dict(zip(where[::2], where[1::2]))
        since = float(asked.get("--since-ts", 0))
        return [message for message in slack_thread.messages if float(message["ts"]) > since][-int(asked["--limit"]) :]

    monkeypatch.setattr(slack, "thread", read)
    assert said_due(watcher(demo), T0) == []
    assert state.load(demo)["orders"]["o1"]["kept"] is not None


def test_a_drive_line_in_the_thread_of_the_orders_card_keeps_its_promised_time(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    card = f"{(T0 - timedelta(minutes=10)).timestamp():.6f}"
    threads: dict[str, list[dict]] = {"1794200000.000100": [], card: [{"ts": card, "from_claude": True}]}
    monkeypatch.setattr(slack, "thread", lambda *where: list(threads[where[3]]))
    with state.edit(demo) as data:
        data["orders"]["o1"].update(
            thread=["D123", "1794200000.000100"], due=state.stamp(T0 - timedelta(minutes=30)), promised=state.stamp(T0 - timedelta(minutes=60))
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
def test_a_due_order_an_older_install_saved_reads_its_thread_in_the_channel_that_install_read_it_in(
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
    assert slack_thread.reads == [("--channel", where[0], "--thread", where[1], "--since-ts", str(int((T0 - timedelta(minutes=60)).timestamp())), "--limit", "1000")]


def test_a_due_order_from_his_dm_whose_thread_is_an_alerts_reads_that_thread_in_the_alert_channel(
    demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: "Thread"
) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["orders"]["o1"].update(
            {
                "from": "slack:D123/1794495097.793329",
                "thread": ["C0822AHFY3G", "1794493064.110609"],
                "due": state.stamp(T0 - timedelta(minutes=30)),
                "promised": state.stamp(T0 - timedelta(minutes=60)),
            }
        )
    [line] = said_due(watcher(demo), T0)
    assert line.startswith('DUE o1 "ship the watch" was due ')
    assert slack_thread.reads == [("--channel", "C0822AHFY3G", "--thread", "1794493064.110609", "--since-ts", str(int((T0 - timedelta(minutes=60)).timestamp())), "--limit", "1000")]


def test_moved_due_fires_again(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["orders"]["o1"]["due"] = state.stamp(T0 + timedelta(minutes=20))
        data["orders"]["o1"]["due_said"] = state.stamp(T0 - timedelta(minutes=5))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=21))[0].startswith("DUE o1")


def quiet_incident(demo: str) -> None:
    with state.edit(demo) as data:
        data["owner"] = {"heard": state.stamp(T0), "away": True}
        data["orders"]["o1"].update(incident=True, joins=None, thread=["D123", INCIDENT_THREAD], note="674295", due=state.stamp(T0 + timedelta(minutes=10)), promised=state.stamp(T0))
        data["orders"]["o2"] = order(text="the same alert again", incident=True, joins="o1", jobs=["j2"], last_line=state.stamp(T0 + timedelta(minutes=2)))
        data["jobs"]["j2"] = job(order="o2", kind="codex")


def test_an_open_incident_with_no_line_for_five_minutes_owes_him_one_through_a_report_and_a_handler_the_watch_starts_while_he_is_away_too(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    quiet_incident(demo)
    calls = catching_up(monkeypatch, demo)
    watching = watcher(demo)
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=6, seconds=59)) == []
    assert calls == []
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=7)) == []
    [(report, body), (start, _)] = calls
    assert (report["text"], report["topic"], "to" in report) == ("POST incident o1 has had no line to him in 5 minutes: post where it stands now in its thread", "watch-quiet-o1", False)
    assert report["attr"] == {"order": "o1", "progress": "true", "quiet": "true", "thread": INCIDENT_THREAD}
    assert f"- {render.job_line('j2', state.load(demo)['jobs']['j2'])}" in body.splitlines()
    assert "Evidence record: 674295" in body.splitlines()
    assert start[-1] == f"Record: 901 Drive: {demo} Scratch: {state.drive_dir(demo) / 'scratch' / 'quiet-901'}"
    quiet = state.load(demo)["orders"]["o1"]["quiet"]
    assert (quiet["record"], quiet["at"], quiet["handler"] in state.load(demo)["handlers"]) == ("901", state.stamp(T0 + timedelta(minutes=7)), True)
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=11, seconds=59)) == []
    assert len(calls) == 2
    with state.edit(demo) as data:
        data["handlers"].clear()
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=12)) == []
    assert (len(calls), calls[3][0][-1]) == (4, f"Record: 903 Drive: {demo} Scratch: {state.drive_dir(demo) / 'scratch' / 'quiet-903'}")


def test_a_quiet_incident_whose_thread_a_handler_holds_restarts_its_clock_with_no_second_handler_its_promised_time_says_no_due_and_its_live_fix_ends_it(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    quiet_incident(demo)
    calls = catching_up(monkeypatch, demo)

    def busy(name: str, brief: str) -> tuple[str, bool]:
        raise watch.agents.Busy("refused, no handler started: handler h1 is at work on his message")

    monkeypatch.setattr(watch.agents, "handle", busy)
    watching = watcher(demo)
    assert watch.due_lines(watching, T0 + timedelta(minutes=30)) == []
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=7)) == []
    assert [call["topic"] for call, _ in calls] == ["watch-quiet-o1"]
    assert state.load(demo)["orders"]["o1"]["quiet"] == {"record": "901", "at": state.stamp(T0 + timedelta(minutes=7)), "handler": None}
    with state.edit(demo) as data:
        data["orders"]["o2"]["last_line"] = state.stamp(T0 + timedelta(minutes=9))
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=13, seconds=59)) == []
    with state.edit(demo) as data:
        data["orders"]["o1"]["fixed"] = "j3 proved it live: turns run again"
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=30)) == []
    assert len(calls) == 1


def test_incidents_sharing_a_thread_owe_him_one_line_there_through_one_report_and_one_handler_on_the_threads_newest_line(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    quiet_incident(demo)
    with state.edit(demo) as data:
        data["orders"]["o3"] = order(text="checkout is down too", incident=True, joins=None, thread=["D123", INCIDENT_THREAD], note="674300", jobs=["j3"], last_line=state.stamp(T0 + timedelta(minutes=1)))
        data["jobs"]["j3"] = job(order="o3", kind="codex")
    calls = catching_up(monkeypatch, demo)
    watching = watcher(demo)
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=6, seconds=59)) == []
    assert calls == []
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=7)) == []
    [(report, body), (start, _)] = calls
    assert (report["text"], report["topic"]) == ("POST incidents o1, o3 have had no line to him in 5 minutes: post where each stands now in its thread", "watch-quiet-o1")
    assert {"Incident o1, o1, o2: ship the watch", "Incident o3, o3: checkout is down too", "Evidence record: 674300", f"- {render.job_line('j3', state.load(demo)['jobs']['j3'])}"} <= set(body.splitlines())
    assert start[-1] == f"Record: 901 Drive: {demo} Scratch: {state.drive_dir(demo) / 'scratch' / 'quiet-901'}"
    orders = state.load(demo)["orders"]
    assert orders["o1"]["quiet"] == orders["o3"]["quiet"] == {"record": "901", "at": state.stamp(T0 + timedelta(minutes=7)), "handler": orders["o1"]["quiet"]["handler"]}
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=11, seconds=59)) == []
    assert len(calls) == 2


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
        data["orders"]["o10"] = order(text="I told him since you never did, fix that", owner="communicator", jobs=["j10"])
        data["jobs"]["j10"] = job(order="o10", updated=state.stamp(T0 + timedelta(minutes=3)))
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


BACK = T0 + timedelta(minutes=95)


def catching_up(monkeypatch: pytest.MonkeyPatch, demo: str) -> list[tuple[list[str] | dict, str | None]]:
    """Each command the drive runs and each record it posts with its body, a post answering its seq."""
    from drive import tools

    with state.edit(demo) as data:
        data["roles"]["communicator"] = {"session": "communicator-session", "terminal": None, "cwd": "/work", "started": state.stamp(T0)}
    calls: list[tuple[list[str] | dict, str | None]] = []

    def run(argv: list[str], checkout: Path, env: dict | None = None) -> str:
        calls.append((argv, None))
        return ""

    def sent(name: str, lane: str, records: list[dict]) -> list[str]:
        [record] = records
        calls.append((record, record["body"]))
        return [str(900 + len(calls))]

    monkeypatch.setattr(reads, "run", run)
    monkeypatch.setattr(tools, "sent", sent)
    monkeypatch.setattr(state, "now", lambda: BACK)
    return calls


def heard_now(demo: str, capsys: pytest.CaptureFixture[str], *flags: str) -> str:
    assert cli.main(["heard", "--drive", demo, *flags]) == 0
    return capsys.readouterr().out.strip()


def test_his_next_message_after_away_owes_his_catch_up_which_the_watch_posts_through_a_handler_once_each_time_he_is_heard(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with state.edit(demo) as data:
        data["owner"] = {"heard": state.stamp(T0), "away": True}
        data["jobs"]["j1"]["updated"] = state.stamp(T0 + timedelta(minutes=5))
        data["orders"]["o2"] = order(jobs=["j2"], updated=state.stamp(T0 - timedelta(minutes=5)))
        data["jobs"]["j2"] = job(order="o2", updated=state.stamp(T0 - timedelta(minutes=5)))
    calls = catching_up(monkeypatch, demo)
    watching = watcher(demo, BACK)
    assert heard_now(demo, capsys) == "heard"
    assert state.load(demo)["catch_up"] == state.new_catch_up(state.stamp(T0), ["o1"])
    assert watch.catch_up_lines(watching, BACK) == []
    [(report, body), (start, _)] = calls
    assert report["text"] == f"POST his catch-up since {render.when(state.stamp(T0))}: 1 orders with news, 0 reports held for it"
    assert ("to" not in report, report["kind"], report["attr"]) == (True, "report", {"catch_up": "true"})
    assert f"- {render.order_line('o1', state.load(demo)['orders']['o1'])}" in body.splitlines()
    assert start[:4] == ["claude", "--bg", "--agent", "long-running:handler"]
    assert start[-1] == f"Record: 901 Drive: {demo} Scratch: {state.drive_dir(demo) / 'scratch' / 'catch-up-901'}"
    owed = state.load(demo)["catch_up"]
    assert (owed["tried"], state.load(demo)["handlers"][owed["handler"]]["holds"]["record"]) == (state.stamp(BACK), "901")
    assert watch.catch_up_lines(watching, BACK) == []
    monkeypatch.setattr(state, "now", lambda: BACK + timedelta(minutes=1))
    assert heard_now(demo, capsys) == "heard"
    assert watch.catch_up_lines(watching, BACK) == []
    assert (len(calls), state.load(demo)["catch_up"]["tried"]) == (2, state.stamp(BACK + timedelta(minutes=1)))
    with state.edit(demo) as data:
        del data["handlers"][owed["handler"]]
    monkeypatch.setattr(state, "now", lambda: BACK + timedelta(minutes=2))
    assert heard_now(demo, capsys) == "heard"
    assert watch.catch_up_lines(watching, BACK) == []
    assert (len(calls), calls[2][0][-1]) == (3, start[-1])
    with state.edit(demo) as data:
        data["handlers"].clear()
    monkeypatch.setattr(state, "now", lambda: BACK + timedelta(minutes=3))
    assert heard_now(demo, capsys, "--away") == "away"
    assert watch.catch_up_lines(watching, BACK) == []
    assert len(calls) == 3


def test_a_catch_up_whose_handler_failed_to_start_starts_one_next_pass_on_the_report_already_written(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with state.edit(demo) as data:
        data["owner"] = {"heard": state.stamp(T0), "away": True}
        data["jobs"]["j1"]["updated"] = state.stamp(T0 + timedelta(minutes=5))
    calls = catching_up(monkeypatch, demo)
    answered = reads.run

    def run(argv: list[str], checkout: Path, env: dict | None = None) -> str:
        if argv[0] == "claude" and len(calls) == 1:
            calls.append((argv, None))
            raise reads.ReadError("claude exited 1")
        return answered(argv, checkout, env)

    monkeypatch.setattr(reads, "run", run)
    watching = watcher(demo, BACK)
    assert heard_now(demo, capsys) == "heard"
    assert watch.catch_up_lines(watching, BACK) == []
    assert (state.load(demo)["catch_up"]["record"], state.load(demo)["handlers"]) == ("901", {})
    assert watch.catch_up_lines(watching, BACK) == []
    assert [call["kind"] if isinstance(call, dict) else call[0] for call, _ in calls] == ["report", "claude", "claude"]
    assert calls[2][0][-1].startswith("Record: 901 ")


def test_a_report_held_while_he_was_away_and_its_correction_go_on_his_catch_up(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cci_corrections: dict[str, list[str]]
) -> None:
    held_at = state.stamp(T0 + timedelta(minutes=10))
    with state.edit(demo) as data:
        data["owner"] = {"heard": state.stamp(T0), "away": True}
        data["orders"]["o2"] = order(jobs=["j2"], updated=state.stamp(T0 - timedelta(minutes=5)))
        data["jobs"]["j2"] = job(order="o2", updated=state.stamp(T0 - timedelta(minutes=5)))
        data["held"]["669950"] = {"order": "o2", "at": held_at, "told": "669957"}
    cci_corrections["669950"] = ["669952"]
    calls = catching_up(monkeypatch, demo)
    assert cli.main(["status", "--drive", demo]) == 0
    assert f"- report 669950 (o2), held {render.when(held_at)}, its writer told in record 669957, corrected by record 669952" in capsys.readouterr().out.splitlines()
    assert heard_now(demo, capsys) == "heard"
    assert state.load(demo)["catch_up"]["orders"] == ["o2"]
    assert watch.catch_up_lines(watcher(demo, BACK), BACK) == []
    [(report, body), _] = calls
    assert report["text"].endswith(": 1 orders with news, 1 reports held for it")
    assert "- report 669950 on o2, corrected by record 669952" in body.splitlines()


def test_a_drive_an_older_install_saved_with_reports_held_owes_his_catch_up_since_the_first(demo: str) -> None:
    with state.edit(demo) as data:
        data["held"] = {
            "700": {"order": "o2", "at": state.stamp(T0 + timedelta(minutes=20)), "told": "701"},
            "702": {"order": None, "at": state.stamp(T0 + timedelta(minutes=10)), "told": "703"},
        }
    saved = state.stored(demo)
    del saved["catch_up"]
    state.store(demo, saved)
    assert state.load(demo)["catch_up"] == state.new_catch_up(state.stamp(T0 + timedelta(minutes=10)), ["o2"])


def test_an_event_with_line_breaks_stays_one_line(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    watching = watcher(demo)
    state.append_event(demo, "refused", question="first line\nsecond\r\nthird\rfourth\n\n" + "x" * 300)
    assert watch.run_pass(watching, T0) == ["EVENT refused question=first line second third fourth " + "x" * 300]


def test_second_instance_exits_quietly(demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    launched(monkeypatch, demo)
    state.pid_path(demo).write_text(f"{os.getppid()}\n")
    assert watch.main(demo) == 0
    assert capsys.readouterr().out == ""


def test_stale_pid_file_is_claimed_and_released(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    state.pid_path(demo).write_text("999999999\n")
    monkeypatch.setattr(watch, "run_pass", lambda watching, moment: [])
    run_watch(demo, monkeypatch)
    assert not state.pid_path(demo).exists()


def test_a_watch_speaks_to_orca_as_the_terminal_of_the_coordinator_it_serves_whatever_pane_started_it(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    spoke = []
    monkeypatch.setattr(watch, "run_pass", lambda watching, moment: spoke.append((os.environ.get("ORCA_TERMINAL_HANDLE"), os.environ.get("ORCA_PANE_KEY"))) or [])
    monkeypatch.setenv("ORCA_PANE_KEY", "tab:starter")
    run_watch(demo, monkeypatch)
    sub_coordinated(demo)
    with state.edit(demo) as data:
        data["roles"]["coordinator"]["terminal"] = "term_coordinator"
        data["subcoordinators"]["Iris"]["terminal"] = "term_iris"
    run_watch(demo, monkeypatch)
    run_watch(demo, monkeypatch, "Iris")
    assert spoke == [("term_outer", "tab:starter"), ("term_coordinator", None), ("term_iris", None)]


def test_a_watch_no_launch_agent_started_lets_its_pid_file_go_loads_the_agent_and_ends(
    demo: str, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess, capsys: pytest.CaptureFixture[str]
) -> None:
    state.pid_path(demo).write_text(f"{os.getpid()}\n")
    monkeypatch.setattr(watch, "run_pass", lambda watching, moment: pytest.fail("a pass ran outside the watch's launch agent"))
    assert watch.main(demo) == 0
    assert (watch_process.ensured, capsys.readouterr().out, state.pid_path(demo).exists()) == ([demo], f"the watch runs for {demo}\n", False)


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
    assert (passes, posted(sent)) == ([], [("evidence", f"root@{demo}", f"PR {KEY}: approved [o1 j1]")])
    run_watch(demo, monkeypatch)
    assert (len(passes), len(sent)) == (1, 1)


def test_a_crashed_watch_wakes_the_coordinator_naming_its_version_and_the_line_of_the_drives_code_that_raised(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(watching: watch.Watcher, moment: datetime) -> list[str]:
        state.load("gone")
        return []

    monkeypatch.setattr(watch, "run_pass", crash)
    sent = posts(monkeypatch)
    launched(monkeypatch, demo)
    with pytest.raises(state.DriveError):
        watch.main(demo)
    [(kind, to, text)] = posted(sent)
    assert (kind, to) == ("handoff", f"root@{demo}")
    log = re.escape(str(state.watch_log_path(demo)))
    assert re.fullmatch(rf"WATCH stopped on long-running {re.escape(state.VERSION)}: DriveError at state\.py:\d+ in stored_json: no drive named gone; its traceback is in {log}; its launch agent starts it again within 10 seconds", text)
    assert not state.pid_path(demo).exists()


def upgrades(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, installs: list[bool]) -> None:
    newer = tmp_path / "cache" / "9.9.9" / "bin" / "drive"
    found = iter([newer if newest else None for newest in installs])
    monkeypatch.setattr(watch, "upgraded", lambda: next(found))


def saved_by(name: str, saver: str) -> None:
    state.store(name, state.stored(name) | {"saved_by": saver})


def mid_pass(monkeypatch: pytest.MonkeyPatch, before_lane_lines: Callable[[], object]) -> None:
    real = watch.lane_lines
    monkeypatch.setattr(watch, "lane_lines", lambda watching, moment: before_lane_lines() or real(watching, moment))


def test_a_pass_that_finds_the_drive_saved_by_a_newer_install_ends_the_watch_holding_every_line_it_made_for_the_one_its_launch_agent_starts_there(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"READY {state.pr_url(KEY)}")
    serve(monkeypatch, {KEY: read(state="MERGED", mergeCommit="ccc333")})
    upgrades(monkeypatch, tmp_path, [False, True])
    mid_pass(monkeypatch, lambda: saved_by(demo, "9.9.9"))
    sent = posts(monkeypatch)
    merged = f"PR {KEY}: merged [o1 j1]"
    launched(monkeypatch, demo)
    assert watch.main(demo) == 0
    assert (sent, state.stored(demo)["prs"][KEY]["state"], state.held_path(demo).read_text(), state.pid_path(demo).exists()) == ([], "merged", f"{merged}\n", False)
    saved_by(demo, state.VERSION)
    monkeypatch.setattr(watch, "upgraded", lambda: None)
    passes = []
    monkeypatch.setattr(watch, "run_pass", lambda watching, moment: passes.append(moment) or [])
    run_watch(demo, monkeypatch)
    assert (posted(sent), passes, state.held_path(demo).exists()) == ([("evidence", f"root@{demo}", merged)], [], False)


def test_a_line_whose_post_failed_goes_to_the_watch_its_launch_agent_starts_on_the_newer_install_it_ended_for_between_passes(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upgrades(monkeypatch, tmp_path, [False, True])
    tapped = f"TAP Hold {KEY} [o1 j1]"
    monkeypatch.setattr(watch, "run_pass", lambda watching, moment: [tapped])
    monkeypatch.setattr(watch.time, "sleep", lambda seconds: None)
    sent = posts(monkeypatch, [False])
    launched(monkeypatch, demo)
    assert watch.main(demo) == 0
    assert (sent, state.held_path(demo).read_text()) == ([], f"{tapped}\n")
    monkeypatch.setattr(watch, "upgraded", lambda: None)
    monkeypatch.setattr(watch, "run_pass", lambda watching, moment: [])
    run_watch(demo, monkeypatch)
    assert posted(sent) == [("handoff", f"root@{demo}", tapped)]


def test_a_crashed_watch_keeps_the_lines_its_pass_made_for_the_watch_its_launch_agent_starts_again(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"READY {state.pr_url(KEY)}")
    serve(monkeypatch, {KEY: read(state="MERGED", mergeCommit="ccc333")})
    mid_pass(monkeypatch, lambda: state.load("gone"))
    sent = posts(monkeypatch)
    launched(monkeypatch, demo)
    with pytest.raises(state.DriveError):
        watch.main(demo)
    merged = f"PR {KEY}: merged [o1 j1]"
    assert ([text.split(":")[0] for _, _, text in posted(sent)], state.held_path(demo).read_text()) == (["WATCH stopped on long-running " + state.VERSION], f"{merged}\n")
    monkeypatch.setattr(watch, "run_pass", lambda watching, moment: [])
    run_watch(demo, monkeypatch)
    assert posted(sent)[1:] == [("evidence", f"root@{demo}", merged)]


def test_ensure_loads_a_launch_agent_that_starts_the_watch_again_after_any_exit_with_no_session_or_secret_unless_one_is_loaded(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[tuple[str, ...]] = []
    loaded = iter([False, True])
    monkeypatch.setenv("OPENAI_API_KEY", "a session secret")
    monkeypatch.setattr(launchd, "kept", lambda label: next(loaded))
    monkeypatch.setattr(launchd, "launchctl", lambda *args: ran.append(args))
    monkeypatch.setattr(launchd, "LAUNCH_AGENTS", tmp_path / "LaunchAgents")
    assert REAL_ENSURE(demo).startswith(f"started the watch under its launch agent com.yasyf.long-running.watch.{demo}, which starts it again within 10 seconds of any exit")
    plist = tmp_path / "LaunchAgents" / f"com.yasyf.long-running.watch.{demo}.plist"
    agent = plistlib.loads(plist.read_bytes())
    assert ran == [("bootstrap", f"gui/{os.getuid()}", str(plist))]
    assert (agent["KeepAlive"], agent["RunAtLoad"], agent["ThrottleInterval"]) == (True, True, launchd.RESTART_SECONDS)
    assert (agent["ProgramArguments"][-4:], agent["WorkingDirectory"]) == (["watch", "--loop", "--drive", demo], str(state.drive_dir(demo)))
    assert agent["StandardOutPath"] == agent["StandardErrorPath"] == str(state.watch_log_path(demo))
    env = agent["EnvironmentVariables"]
    assert (env[state.DRIVE_ENV], env[launchd.LAUNCHED_ENV], "CLAUDE_CODE_SESSION_ID" in env, "OPENAI_API_KEY" in env) == (demo, watch.label(demo), False, False)
    assert REAL_ENSURE(demo).startswith("the watch runs under its launch agent")
    assert len(ran) == 1


def test_a_drive_under_long_running_state_gets_no_watch_process_or_channel_and_a_watch_started_there_ends(
    demo: str, state_root: Path, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess, capsys: pytest.CaptureFixture[str]
) -> None:
    started = []
    monkeypatch.setenv("LONG_RUNNING_STATE", str(state_root))
    monkeypatch.setattr(watch, "ensure", REAL_ENSURE)
    monkeypatch.setattr(launchd, "keep", lambda *args: started.append(args))
    monkeypatch.setattr(watch, "run_pass", lambda watching, moment: pytest.fail("a pass ran on a drive under LONG_RUNNING_STATE"))
    assert cli.main(["watch", "--drive", demo]) == 0
    assert "watch.run_pass" in capsys.readouterr().out
    assert watch.main(demo) == 0
    assert (started, watch_process.listened, state.pid_path(demo).exists()) == ([], [], False)


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


def sub_coordinated(name: str, project: str = "Iris", session: str = "iris-1", cwd: str = "/work") -> None:
    with state.edit(name) as data:
        data["orders"]["o1"]["project"] = project
        cli.bind_subcoordinator(data, project, {"session": session, "terminal": None, "cwd": cwd, "started": state.stamp(T0)})


def test_a_projects_watch_hears_only_its_orders_jobs_prs_and_orphans_and_the_drives_watch_the_rest(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sub_coordinated(demo)
    still = state.stamp(T0 - timedelta(minutes=10))
    with state.edit(demo) as data:
        data["jobs"]["j1"]["state"] = "waiting"
        data["orders"]["o2"] = order(text="fix the alert", jobs=["j2"], project="Platform")
        data["jobs"]["j2"] = job(order="o2", state="waiting", prs=[state.pr_url(OTHER)])
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o2") | {"head": "aaaaaaa111"}
        data["orders"]["o3"] = order(text="the logs page", jobs=[], project="Iris", updated=still)
        data["orders"]["o4"] = order(text="a page for alerts", jobs=[], project=None, updated=still)
    calls = serve(monkeypatch, {KEY: read(checks=RED), OTHER: read(checks=RED)})
    iris, top = watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0, project="Iris"), watcher(demo)
    for number, order_id in ((42, "o1"), (43, "o2")):
        state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=number, label="Approve", lane=state.heard_lane(state.load(demo), order_id))
    state.append_event(demo, "refused", question="Which?", pick="Yes")
    assert watch.run_pass(iris, T0) == [f"PR {KEY}: checks failed [o1 j1]", f"TAP Approve {KEY} [o1 j1]", 'ORPHAN o3 "the logs page": no job']
    assert watch.run_pass(top, T0) == [
        f"PR {OTHER}: checks failed [o2 j2]",
        f"TAP Approve {OTHER} [o2 j2]",
        "EVENT refused question=Which? pick=Yes",
        'ORPHAN o4 "a page for alerts": no job',
    ]
    assert calls == [[KEY], [OTHER]]
    assert (iris.lane, top.lane) == ("iris", "root")


def test_the_drives_watch_keeps_a_projects_lines_until_a_sub_coordinator_is_bound_for_it_and_that_ones_watch_takes_them_from_there(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit(demo) as data:
        data["orders"]["o1"]["project"] = "Iris"
        data["jobs"]["j1"]["state"] = "waiting"
    reads = {KEY: read(checks=RED)}
    calls = serve(monkeypatch, reads)
    top = watcher(demo)
    assert watch.run_pass(top, T0) == [f"PR {KEY}: checks failed [o1 j1]"]
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Hold", lane=state.heard_lane(state.load(demo), "o1"))
    sub_coordinated(demo)
    assert state.load(demo)["subcoordinators"]["Iris"]["from"] == state.events_end(demo)
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Approve", lane=state.heard_lane(state.load(demo), "o1"))
    iris = watch.Watcher(demo, offset=state.watch_cursor(demo, "Iris"), ticked_at=T0, project="Iris")
    reads[KEY] = read(state="MERGED", mergeCommit="ccc333", checks=RED)
    later = T0 + timedelta(minutes=2)
    assert watch.run_pass(top, later) == [f"TAP Hold {KEY} [o1 j1]"]
    assert watch.run_pass(iris, later) == [
        f"PR {KEY}: merged [o1 j1]",
        f"TAP Approve {KEY} [o1 j1]",
        'ORPHAN o1 "ship the watch": j1 waits on "nothing named" and no open PR, card or due time can wake it',
    ]
    assert calls == [[KEY], [KEY]]


def test_a_projects_watch_runs_as_its_own_process_from_where_the_drives_watch_stands_and_posts_to_its_sub_coordinators_lane(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub_coordinated(demo)
    started = []
    monkeypatch.setattr(launchd, "kept", lambda label: False)
    monkeypatch.setattr(launchd, "keep", lambda label, args, log, env, cwd: started.append((label, args, log)))
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Hold")
    state.save_watch_cursor(demo, state.events_end(demo))
    drives_cursor = state.watch_cursor(demo)
    assert REAL_ENSURE(demo, "Iris") == (
        f"started the watch for Iris under its launch agent com.yasyf.long-running.watch.{demo}.iris, which starts it again within 10 seconds of any exit,"
        " crash included; it posts its lines to the sub-coordinator for Iris over cci, so arm no Monitor for it"
    )
    assert started == [(f"com.yasyf.long-running.watch.{demo}.iris", ["watch", "--loop", "--drive", demo, "--project", "Iris"], state.drive_dir(demo) / "watch-iris.log")]
    assert (state.cursor_path(demo, "Iris").exists(), state.watch_cursor(demo, "Iris")) == (False, state.load(demo)["subcoordinators"]["Iris"]["from"])
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Hold", lane="iris")
    serve(monkeypatch, {})
    sent = posts(monkeypatch)
    run_watch(demo, monkeypatch, "Iris")
    assert posted(sent) == [("handoff", f"iris@{demo}", f"TAP Hold {KEY} [o1 j1]")]
    assert (state.watch_cursor(demo, "Iris"), state.pid_path(demo, "Iris").name, state.pid_path(demo, "Iris").exists()) == (state.events_end(demo), "watchd-iris.pid", False)
    assert state.watch_cursor(demo) == drives_cursor

    def crash(watching: watch.Watcher, moment: datetime) -> list[str]:
        raise OSError("disk full")

    monkeypatch.setattr(watch, "run_pass", crash)
    with pytest.raises(OSError):
        watch.main(demo, "Iris")
    kind, to, text = posted(sent)[-1]
    assert (kind, to) == ("handoff", f"iris@{demo}")
    log = re.escape(str(state.watch_log_path(demo, "Iris")))
    assert re.fullmatch(rf"WATCH stopped on long-running {re.escape(state.VERSION)}: OSError at watch\.py:\d+ in watching: disk full; its traceback is in {log}; its launch agent starts it again within 10 seconds", text)


def test_drive_watch_project_starts_only_from_its_sub_coordinators_session_and_points_that_channel_at_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess, capsys: pytest.CaptureFixture[str]
) -> None:
    sub_coordinated(demo)
    assert cli.main(["watch", "--drive", demo, "--project", "Iris"]) == 2
    assert "only the sub-coordinator for Iris starts its watch, from its own session (iris-1)" in capsys.readouterr().err
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    assert cli.main(["watch", "--drive", demo, "--project", "Iris"]) == 0
    assert cli.main(["watch", "--drive", demo]) == 0
    assert (watch_process.ensured, watch_process.listened) == ([f"{demo} --project Iris", demo], [(demo, "iris-1", "Iris")])
    subscribed = []
    monkeypatch.setattr(watch.reads, "run", lambda argv, checkout, env: subscribed.append((argv, env["CLAUDE_CODE_SESSION_ID"])))
    REAL_LISTEN(demo, "iris-1", "Iris")
    assert subscribed == [([*shlex.split(render.subscribe("coordinator", "Iris")), "--drive", demo], "iris-1")]
    assert render.subscribe("coordinator", "Iris").startswith("cci subscribe --reader iris --cursor coordinator-iris --to-kind ")


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


def test_each_change_a_design_lists_is_an_orphan_with_no_builder_once_each_change_it_waits_on_is_delivered(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    before = state.stamp(T0 - timedelta(minutes=10))
    stack = (
        "DESIGN moved-table abstraction\nChange: the moved-table interface\nChange: the reads through it\nChange: the writes through it (waits on change 2)\n"
        "Change: the old tables dropped (waits on change 2 and change 3 live on every cluster)\n"
    )
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(text="redesign the moved tables", jobs=["j2", "j3"], options_only=True)
        data["orders"]["o2"]["parts"] = {"moved-table abstraction": state.new_part()}
        data["jobs"]["j2"] = job(order="o2", kind="design", parts=["moved-table abstraction"], updated=before)
        data["jobs"]["j3"] = job(order="o2", parts=["moved-table abstraction", "the moved-table interface"], updated=before)
        state.add_changes(data, "j2", stack)
        data["orders"]["o2"]["updated"] = before
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    with state.edit(demo) as data:
        data["orders"]["o2"]["options_only"] = False
    unbuilt = "j2's design lists this change and no builder carries it: start one with drive job add builder --part, or add it to its builder with drive job set --part"
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == [f'ORPHAN o2 "the reads through it": {unbuilt}']
    with state.edit(demo) as data:
        data["orders"]["o2"]["jobs"].append("j4")
        data["jobs"]["j4"] = job(order="o2", parts=["the reads through it"], state="done", updated=before)
    assert watch.run_pass(watching, T0 + timedelta(seconds=10)) == [f'ORPHAN o2 "the writes through it": {unbuilt}']
    with state.edit(demo) as data:
        data["orders"]["o2"]["jobs"].append("j5")
        data["jobs"]["j5"] = job(order="o2", parts=["the writes through it"], state="done", updated=before)
    assert watch.run_pass(watching, T0 + timedelta(seconds=15)) == [f'ORPHAN o2 "the old tables dropped": {unbuilt}']


def test_a_done_design_leaves_its_own_part_with_the_builder_carrying_one_of_its_changes(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    before = state.stamp(T0 - timedelta(minutes=10))
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(text="redesign the moved tables", jobs=["j2", "j3"], updated=before)
        data["orders"]["o2"]["parts"] = {"redesign the moved tables": state.new_part(), "the moved-table interface": state.new_part() | {"design": "j2"}}
        data["jobs"]["j2"] = job(order="o2", kind="design", state="done", said="carried by builder j3", parts=["redesign the moved tables"], updated=before)
        data["jobs"]["j3"] = job(order="o2", parts=["the moved-table interface"], updated=before)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    with state.edit(demo) as data:
        data["jobs"]["j3"].update(state="stopped")
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == [
        'ORPHAN o2 "redesign the moved tables": j2 answered and the answer is not posted to him',
        'ORPHAN o2 "the moved-table interface": j3 stopped',
    ]


def test_his_answer_kept_on_a_job_is_said_undelivered_ten_minutes_on_and_every_ten_minutes_after(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["jobs"]["j1"]["undelivered"] = [{"seq": "#665156", "words": "ship the narrower fix", "at": state.stamp(T0), "said": None}]
    watching = watcher(demo)
    line = (
        f'ANSWER undelivered j1: his answer #665156 "ship the narrower fix" of {render.when(state.stamp(T0))} has not reached j1;'
        ' drive job resume j1 "<his answer>" carries it [o1 j1]'
    )
    assert watch.run_pass(watching, T0 + timedelta(minutes=9)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=10)) == [line]
    assert watch.run_pass(watching, T0 + timedelta(minutes=15)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=20)) == [line]


def test_his_tap_on_a_lane_questions_card_leads_its_orphan_and_tick_lines_until_his_undo_reopens_the_question_his_card_asks_again(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    asked = "Which fixture keeps the hold test deterministic?"
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["orders"]["o1"]["updated"] = state.stamp(T0 + timedelta(minutes=1))
        data["jobs"]["j1"].update(state="waiting", ask="msg_q1", ask_at=state.stamp(T0), said=asked, report=asked, questions=[state.new_question(None, asked, state.stamp(T0), card=CARD)])
    tapped = T0 + timedelta(minutes=5)
    with monkeypatch.context() as clock:
        clock.setattr(state, "now", lambda: tapped)
        card.question_answered({"waits": "demo:j1-7", "label": "Ring", "url": CARD})
    watching = watcher(demo)
    tap = f'his answer #9 "Ring" of {render.when(state.stamp(tapped))} has not reached j1; drive job resume j1 "<his answer>" carries it'
    assert watch.run_pass(watching, T0 + timedelta(minutes=15)) == [f"ANSWER undelivered j1: {tap} [o1 j1]"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == [
        f"ANSWER undelivered j1: {tap} [o1 j1]",
        f'ORPHAN o1 "ship the watch": {tap}, answering its lane\'s question "{asked}" 30 minutes ago',
        f"TICK o1, and {tap}",
    ]
    with monkeypatch.context() as clock:
        clock.setattr(state, "now", lambda: T0 + timedelta(minutes=31))
        card.question_answered({"waits": "demo:j1-7", "label": "Ring", "url": CARD, "undone": True})
    assert view.open_cards(state.load(demo)["jobs"]["j1"]) == [CARD]
    assert watch.run_pass(watching, T0 + timedelta(minutes=60)) == ["TICK o1"]


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


def test_a_local_job_added_with_no_agent_is_an_orphan_a_minute_on_though_its_part_was_said_since_its_last_tick(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(watch, "local_gap", REAL_LOCAL_GAP)
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="failed", updated=state.stamp(T0 - timedelta(minutes=20)))
        data["orders"]["o1"]["parts"]["ship the watch"]["orphan_said"] = state.stamp(T0 - timedelta(minutes=10))
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(kind="reader", session="session-under-test", parts=["ship the watch"])
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == ['ORPHAN o1 "ship the watch": j2 has no agent recorded']
    assert watch.run_pass(watching, T0 + timedelta(minutes=2)) == []


def test_a_reader_an_older_install_started_on_a_prs_head_holds_no_part_and_is_never_an_answer_owed_him(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="failed", updated=earlier)
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(kind="reader", state="done", parts=["ship the watch"], checks={KEY: "aaaaaaa111"}, updated=earlier)
    assert watch.run_pass(watcher(demo), T0) == ['ORPHAN o1 "ship the watch": j1 failed']


def test_a_job_on_the_immediate_fix_carries_the_orders_other_parts_until_it_is_done(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    with state.edit(demo) as data:
        data["orders"]["o1"]["parts"][state.IMMEDIATE_FIX] = state.new_part()
        data["orders"]["o1"]["updated"] = earlier
        data["jobs"]["j1"].update(parts=[state.IMMEDIATE_FIX], state="waiting", updated=earlier)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    with state.edit(demo) as data:
        data["jobs"]["j1"]["state"] = "done"
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == ['ORPHAN o1 "ship the watch": no job']


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
    monkeypatch.setattr(cli.reads, "run", lambda argv, checkout: json.dumps({"seq": argv[2], "kind": "report", "lane": "root", "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": {"job": "j2", "part": "why do old runs fail?"}}}))
    assert cli.main(["order", "posted", "o2", answer, "--record", "661300", "--drive", demo]) == 0
    capsys.readouterr()
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    part = state.load(demo)["orders"]["o2"]["parts"]["why do old runs fail?"]
    assert part == state.new_part() | {"posted": answer}


@pytest.mark.parametrize("ended", ["failed", "done"])
def test_an_open_incidents_fix_whose_jobs_ended_with_no_pr_has_no_fix_job_and_names_the_second_to_start_until_one_runs(
    demo: str, monkeypatch: pytest.MonkeyPatch, ended: str
) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="done", updated=earlier)
        data["orders"]["o2"] = order(text="runs never start", incident=True, jobs=["j2"], parts={state.INCIDENT_FIX: state.new_part()}, updated=earlier)
        data["jobs"]["j2"] = job(order="o2", kind="codex", goal="fix: runs never start", state=ended, parts=[state.INCIDENT_FIX], updated=earlier)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == [
        f'ORPHAN o2 "fix": no fix job: j2 ended {ended} with no open PR; start the second fix job now, an Opus 5.5 long-running:builder on j2\'s brief:'
        " drive job add builder --order o2 --part fix --goal 'fix: runs never start' (incidents: Clocks)"
    ]
    with state.edit(demo) as data:
        data["orders"]["o2"]["jobs"].append("j3")
        data["jobs"]["j3"] = job(order="o2", goal="fix: runs never start", parts=[state.INCIDENT_FIX])
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    assert watch.run_pass(watching, T0 + watch.HANDOFF) == []
    assert state.load(demo)["orders"]["o2"]["parts"][state.INCIDENT_FIX]["orphan_said"] is None


def test_a_waiting_job_lives_while_its_question_is_open_and_is_an_orphan_once_it_is_withdrawn_whose_card_the_watch_then_closes_once(
    demo: str, monkeypatch: pytest.MonkeyPatch, closed_cards: list[tuple[str, str, str, bool]]
) -> None:
    card = {"question": "Which store?", "options": ["Team", "Run"], "recommended": "Team", "asked": state.stamp(T0), "url": f"{CARD}?thread_ts=1.2", "card": {"title": "Which store?"}}
    shown = {"team_url": "", "questions": [card], "closed": [], "pr_cards": []}
    monkeypatch.setattr(watch.view, "cards", lambda name: shown)
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"].update(
            state="waiting", said="ASK which store?", questions=[state.new_question(None, "Which store?", state.stamp(T0), card=CARD)], updated=state.stamp(T0 - timedelta(minutes=10))
        )
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    assert watch.run_pass(watching, T0 + timedelta(seconds=60)) == []
    monkeypatch.setattr(watch.view, "cards", lambda name: shown)
    with state.edit(demo) as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("withdrawn", "his order replaced it", REPORTED, at=state.stamp(T0 - timedelta(minutes=10)))
    assert watch.run_pass(watching, T0 + timedelta(seconds=120)) == [
        f"CARD {CARD}?thread_ts=1.2 closed in place: its question withdrawn",
        'ORPHAN o1 "ship the watch": j1 waits on "ASK which store?" and no open PR, card or due time can wake it',
    ]
    assert closed_cards == [(f"{CARD}?thread_ts=1.2", "his order replaced it", REPORTED, False)]
    shown["questions"] = []
    assert watch.run_pass(watching, T0 + timedelta(seconds=180)) == []
    assert len(closed_cards) == 1


def test_a_built_job_whose_ask_drive_job_ask_refused_is_said_to_wait_on_a_card_never_posted(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    refused = "refused: these recorded answers of his may already settle this question. Read each with `ccn answer show <id>`"
    asked = "How many clone processes may a SandSQL server run at once?"
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"].update(
            state="built", branch="clone-cap", questions=[state.new_question("/drive/asks/j1-ab.md", asked, state.stamp(T0), refused=refused)], updated=state.stamp(T0 - timedelta(minutes=10))
        )
    assert watch.run_pass(watcher(demo), T0) == [
        f'ORPHAN o1 "ship the watch": j1 waits on a card never posted: drive job ask refused its question "{asked}" kept in /drive/asks/j1-ab.md:'
        " these recorded answers of his may already settle this question. Read each with `ccn answer show <id>`"
    ]


def test_a_refused_question_his_recorded_answer_since_settled_no_longer_waits_on_a_card_never_posted(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    refused = "refused: his recorded answer 5630432 already decides this question."
    asked = "Which credential reads Slack as him?"
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"].update(
            state="waiting",
            said=asked,
            due=state.stamp(T0 + timedelta(minutes=80)),
            questions=[state.new_question("/drive/asks/j1-cd.md", asked, state.stamp(T0), refused=refused, closed=state.new_close("answered", "the browser session token", None, answer="5630432"))],
            updated=state.stamp(T0 - timedelta(minutes=10)),
        )
    assert watch.run_pass(watcher(demo), T0) == []


def test_a_waiting_job_lives_while_its_own_clock_is_to_come(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"].update(state="waiting", said="WAITING on the release", due=state.stamp(T0 + timedelta(minutes=80)), updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    with state.edit(demo) as data:
        data["jobs"]["j1"]["due"] = None
    assert watch.run_pass(watching, T0 + timedelta(seconds=60)) == [
        'ORPHAN o1 "ship the watch": j1 waits on "WAITING on the release" and no open PR, card or due time can wake it'
    ]


def test_a_done_design_job_is_carried_by_its_open_card_and_its_part_needs_a_job_once_he_answers(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    card = {"question": "Which Mac owns a chat?", "options": ["First", "Last"], "recommended": "First", "asked": state.stamp(T0), "url": f"{CARD}?thread_ts=1.2", "card": {"title": "Which Mac owns a chat?", "waits": "demo:j2-7-a"}}
    monkeypatch.setattr(watch.view, "cards", lambda name: {"team_url": "", "questions": [card], "closed": [], "pr_cards": []})
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(text="options: only the owning machine writes", jobs=["j2"])
        data["jobs"]["j2"] = job(
            order="o2",
            kind="design",
            state="done",
            questions=[state.new_question(None, None, state.stamp(T0), card=CARD)],
            undelivered=[{"seq": "#9", "words": "First", "at": state.stamp(T0 - timedelta(minutes=20)), "said": None}],
            updated=state.stamp(T0 - timedelta(minutes=10)),
        )
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    with state.edit(demo) as data:
        data["jobs"]["j2"]["questions"][0]["closed"] = state.new_close("answered", "First", None, seq="#9", at=state.stamp(T0 - timedelta(minutes=10)))
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
REFUSED_TEXT = "API Error: Opus 5.5's safeguards flagged this message (https://www.anthropic.com/legal/aup). This sometimes happens with safe, normal conversations."
REFUSED = {
    "type": "assistant",
    "timestamp": "2026-11-12T20:44:05.907Z",
    "message": {"model": "<synthetic>", "stop_reason": "refusal", "content": [{"type": "text", "text": REFUSED_TEXT}]},
}


@pytest.mark.parametrize(
    ("entries", "quiet", "gap"),
    [
        ([CALLING, FINISHED], timedelta(minutes=10), 'j1\'s agent finished and its result is not recorded: "DONE shipped"' + REPORT_IT),
        ([FINISHED, {"type": "user", "message": {"content": "next event"}}], timedelta(minutes=10), None),
        ([CALLING, RESULT], timedelta(minutes=10), None),
        ([CALLING, RESULT], timedelta(minutes=45), "j1's agent has written nothing for 45 minutes"),
        ([CALLING, FINISHED], timedelta(seconds=30), None),
        ([CALLING, PARKED], timedelta(minutes=10), None),
        ([CALLING, RESULT, REFUSED], timedelta(minutes=10), None),
        (
            [CALLING, RESULT, REFUSED],
            timedelta(minutes=45),
            'j1\'s agent stopped 45 minutes ago on refusal: "API Error: Opus 5.5\'s safeguards flagged this message (https://www.anthropic.com/legal/aup). This…";'
            " drive job resume j1 <the event> relaunches it on a Sprite with a handoff",
        ),
        (
            [CALLING, PARKED],
            timedelta(minutes=45),
            'j1\'s agent ended its turn 45 minutes ago on "Codex is still running. I\'ll wait for the notification." with no result line: SendMessage from the session that started it wakes it, and drive job resume j1 <the event> from any other',
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


def relayed(reply: Path, ended: str) -> list[dict]:
    """A codex relay's transcript: its codex-ask call, the output codex-ask printed as the run started, and the line it ended its turn on."""
    printed = {"type": "user", "message": {"content": [{"type": "tool_result", "content": f"REPLY_FILE: {reply}\nLOG_FILE: {reply.parent / 'codex-q-1.log'}"}]}}
    return [CALLING, printed, ended_on(ended)]


@pytest.mark.parametrize(
    ("ran", "quiet", "gap"),
    [
        ("running", timedelta(minutes=45), None),
        ("pending", timedelta(minutes=10), None),
        ("completed", timedelta(minutes=10), "j1's codex-ask run completed and its result is not recorded"),
        ("died", timedelta(minutes=10), "j1's codex-ask run died and its result is not recorded"),
    ],
)
def test_a_codex_relay_that_ended_its_turn_is_judged_by_the_codex_ask_run_it_asked(
    demo: str, tmp_path: Path, codex_ask: CodexAsk, ran: str, quiet: timedelta, gap: str | None
) -> None:
    write_transcript(tmp_path / "config", relayed(codex_ask.run(ran, "DONE reviewed\n"), "Codex is still running. I'll wait for the notification."), quiet)
    job_of = {"session": "session-under-test", "agent": "a1", "kind": "codex"}
    assert REAL_LOCAL_GAP("j1", job_of, state.load(demo), state.now()) == gap


def test_a_codex_relay_whose_run_codex_ask_cannot_read_names_its_error(demo: str, tmp_path: Path, codex_ask: CodexAsk) -> None:
    reply = codex_ask.run("completed", "DONE reviewed\n")
    (tmp_path / "codex-plugin" / "bin" / "codex-ask").write_text("#!/bin/sh\necho 'codex-ask: no run at that folder' >&2\nexit 2\n")
    write_transcript(tmp_path / "config", relayed(reply, f"REPLY_FILE: {reply}"), timedelta(minutes=4))
    job_of = {"session": "session-under-test", "agent": "a1", "kind": "codex"}
    assert REAL_LOCAL_GAP("j1", job_of, state.load(demo), state.now()) == "j1's codex-ask run could not be read: codex-ask: no run at that folder"


def test_a_codex_relay_that_read_back_a_reply_naming_a_scratch_file_is_judged_by_its_codex_ask_run(demo: str, tmp_path: Path, codex_ask: CodexAsk) -> None:
    run = codex_ask.run("completed", "DONE reviewed\n")
    scratch = tmp_path / "scratch" / "j1"
    scratch.mkdir(parents=True)
    (scratch / "reply.md").write_text("DONE reviewed\n")
    read_back = {"type": "user", "message": {"content": [{"type": "tool_result", "content": f"REPLY_FILE: {scratch / 'reply.md'}\nDONE reviewed"}]}}
    write_transcript(tmp_path / "config", [*relayed(run, "Reading the reply.")[:2], read_back, ended_on("Codex is done.")], timedelta(minutes=10))
    job_of = {"session": "session-under-test", "agent": "a1", "kind": "codex"}
    assert REAL_LOCAL_GAP("j1", job_of, state.load(demo), state.now()) == "j1's codex-ask run completed and its result is not recorded"


@pytest.mark.parametrize(
    ("printed", "gap"),
    [
        ("", None),
        ('{"reply_file":"/a"}\n{"reply_file":"/b"}\n', None),
        ("codex-ask 1.15.3\n", "j1's codex-ask run could not be read: codex-ask --collect printed no record: codex-ask 1.15.3"),
    ],
)
def test_a_codex_ask_collect_printing_no_record_of_the_reply_ends_no_watch_pass(
    demo: str, tmp_path: Path, codex_ask: CodexAsk, printed: str, gap: str | None
) -> None:
    reply = codex_ask.run("completed", "DONE reviewed\n")
    (tmp_path / "codex-plugin" / "bin" / "codex-ask").write_text(f"#!/bin/sh\nprintf '%s' '{printed}'\n")
    write_transcript(tmp_path / "config", relayed(reply, "Codex is still running."), timedelta(minutes=10))
    job_of = {"session": "session-under-test", "agent": "a1", "kind": "codex"}
    assert REAL_LOCAL_GAP("j1", job_of, state.load(demo), state.now()) == gap


def test_a_codex_relay_that_asked_codex_nothing_is_parked_like_any_agent(demo: str, tmp_path: Path, codex_ask: CodexAsk) -> None:
    write_transcript(tmp_path / "config", [CALLING, RESULT, PARKED], timedelta(minutes=45))
    job_of = {"session": "session-under-test", "agent": "a1", "kind": "codex"}
    assert REAL_LOCAL_GAP("j1", job_of, state.load(demo), state.now()) == (
        'j1\'s agent ended its turn 45 minutes ago on "Codex is still running. I\'ll wait for the notification." with no codex-ask run: SendMessage from the session that started it wakes it, and drive job resume j1 <the event> from any other'
    )


def test_a_codex_job_run_again_after_its_run_was_taken_waits_on_a_new_run_rather_than_retaking_the_old(demo: str, tmp_path: Path, codex_ask: CodexAsk) -> None:
    reply = codex_ask.run("completed", "DONE reviewed\n")
    write_transcript(tmp_path / "config", relayed(reply, "Codex is done."), timedelta(minutes=45))
    taken = state.stamp(datetime.fromtimestamp(reply.stat().st_mtime).astimezone() + timedelta(seconds=1))
    job_of = {"session": "session-under-test", "agent": "a1", "kind": "codex", "returned": None}
    assert REAL_LOCAL_GAP("j1", job_of, state.load(demo), state.now()) == "j1's codex-ask run completed and its result is not recorded"
    assert REAL_LOCAL_GAP("j1", job_of | {"returned": taken}, state.load(demo), state.now()) == (
        'j1\'s agent ended its turn 45 minutes ago on "Codex is done." with no codex-ask run: SendMessage from the session that started it wakes it, and drive job resume j1 <the event> from any other'
    )

def test_the_watch_takes_a_codex_jobs_result_from_its_completed_run_whatever_its_relay_ended_on(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk
) -> None:
    monkeypatch.setattr(watch, "local_gap", REAL_LOCAL_GAP)
    reply = codex_ask.run("completed", "UNPROVEN two of three findings hold\nFinding 1\n")
    write_transcript(tmp_path / "config", relayed(reply, f"REPLY_FILE: {reply}"), timedelta(minutes=4))
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"].update(kind="codex", session="session-under-test", agent="a1", updated=state.stamp(state.now() - timedelta(minutes=10)))
    assert watch.run_pass(watcher(demo, state.now()), state.now()) == []
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], job["said"], state.report_text(demo, "j1")) == ("done", "UNPROVEN two of three findings hold", "UNPROVEN two of three findings hold\nFinding 1\n")


def test_a_codex_lane_that_built_its_recommended_option_has_the_ask_block_it_named_kept_as_its_run_is_taken_so_his_card_goes_up_as_it_returns(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk
) -> None:
    monkeypatch.setattr(watch, "local_gap", REAL_LOCAL_GAP)
    monkeypatch.setattr(watch.view, "cards", lambda name: view.NO_CARDS)
    block = tmp_path / "scratch" / "ask.md"
    block.parent.mkdir()
    block.write_text(SHORT_ASK)
    reply = codex_ask.run("completed", f"DONE built one snapshot file per finished run on branch snapshot-file\nASK: {block}\n")
    write_transcript(tmp_path / "config", relayed(reply, f"REPLY_FILE: {reply}"), timedelta(minutes=4))
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"].update(kind="codex", session="session-under-test", agent="a1", updated=state.stamp(state.now() - timedelta(minutes=10)))
    kept = state.drive_dir(demo) / "asks" / f"j1-{state.ask_hash(SHORT_ASK)}.md"
    assert watch.run_pass(watcher(demo, state.now()), state.now()) == [
        f"JOB j1 o1 DONE built one snapshot file per finished run on branch snapshot-file; its ASK is kept: send its ask record, path {kept},"
        " and the recommended option it built ships meanwhile"
    ]
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], [question["file"] for question in job["questions"]], kept.read_text()) == ("waiting", [str(kept)], SHORT_ASK)


def test_a_local_agent_a_sub_coordinator_started_is_judged_by_its_transcript_under_that_session(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    path = tmp_path / "config" / "projects" / "-iris" / "iris-1" / "subagents" / "agent-a1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in (CALLING, FINISHED)))
    stamp = (state.now() - timedelta(minutes=10)).timestamp()
    os.utime(path, (stamp, stamp))
    sub_coordinated(demo, cwd="/iris")
    job_of = {"session": "iris-1", "agent": "a1", "kind": "builder"}
    assert REAL_LOCAL_GAP("j1", job_of, state.load(demo), state.now()) == 'j1\'s agent finished and its result is not recorded: "DONE shipped"' + REPORT_IT


def test_a_local_agents_tool_call_with_no_result_for_five_minutes_is_said_stuck_once(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    serve(monkeypatch, {})
    asked = state.now()
    call = {"type": "tool_use", "id": "toolu_held", "name": "Bash", "input": {"command": 'tables="a,b"; echo ${(s:,:)tables}'}}
    write_transcript(tmp_path / "config", [{"type": "assistant", "timestamp": state.stamp(asked), "message": {"stop_reason": "tool_use", "content": [call]}}], timedelta(0))
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(session="session-under-test", agent="a1")
    assert staged(watch.agent_lines, watcher(demo), asked + timedelta(minutes=4)) == []
    assert staged(watch.agent_lines, watcher(demo), asked + timedelta(minutes=7)) == [
        'STUCK j1 o1: its agent\'s Bash call "tables="a,b"; echo ${(s:,:)tables}" has had no result for 7 minutes, so a permission prompt nobody'
        " sees holds it: TaskStop a1, then hand the job to a fresh agent whose brief runs that call as a scratch script"
    ]
    assert state.load(demo)["jobs"]["j1"]["stuck_call"] == "toolu_held"
    assert staged(watch.agent_lines, watcher(demo), asked + timedelta(minutes=9)) == []


def test_a_local_agent_from_another_session_or_with_no_transcript_is_gone(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    data = state.load(demo)
    assert REAL_LOCAL_GAP("j1", {"session": "old", "agent": "a1"}, data, state.now()) == "j1's agent ended with an earlier session"
    assert REAL_LOCAL_GAP("j1", {"session": "session-under-test", "agent": "a1"}, data, state.now()) == "j1's agent a1 has no transcript"


def ended_on(text: str) -> dict:
    return {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}}


def returned(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, text: str, report: str | None) -> None:
    monkeypatch.setattr(watch, "local_gap", REAL_LOCAL_GAP)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    card = {"question": ASKED, "options": ["Renew", "Sign in"], "recommended": "Renew", "asked": state.stamp(T0), "url": f"{CARD}?thread_ts=1.2", "card": {"title": ASKED}}
    monkeypatch.setattr(watch.view, "cards", lambda name: {"team_url": "", "questions": [card], "closed": [], "pr_cards": []})
    kept = state.drive_dir(demo) / "asks" / "j1-4d8033be.md"
    write_transcript(tmp_path / "config", [CALLING, RESULT, ended_on(text.format(kept=kept))], timedelta(minutes=4))
    if report:
        state.keep_report(demo, "j1", state.opening(report), report)
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"].update(
            kind=kind,
            session="session-under-test",
            agent="a1",
            said=ASKED,
            questions=[state.new_question(str(kept), ASKED, state.stamp(T0), card=CARD)],
            updated=state.stamp(state.now() - timedelta(minutes=10)),
        )


@pytest.mark.parametrize(
    ("kind", "text", "report"),
    [
        ("builder", "DONE shipped\nPR: x", None),
        ("reader", "ASK {kept}.old", None),
        ("reader", "ASK {kept}\n\nThe card asks whether sessions renew while used.", None),
        ("builder", "DONE shipped, reworded at the stop", "DONE shipped\nPR: x\n"),
        ("reader", "My report was kept before your message arrived, so I did not resubmit.", "DONE ccx stages its sweep\nResearched:\n"),
    ],
)
def test_a_running_local_agent_that_ended_on_a_result_line_is_an_orphan_whatever_it_kept_since_the_watch_takes_no_result(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, text: str, report: str | None
) -> None:
    returned(demo, tmp_path, monkeypatch, kind, text, report)
    [line] = watch.run_pass(watcher(demo, state.now()), state.now())
    assert (line.startswith(UNRECORDED), line.endswith(REPORT_IT)) == (True, True)
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("running", ASKED)


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


def test_a_red_pr_wakes_its_builder_as_it_turns_red_and_every_thirty_minutes_it_stays_red_whatever_its_jobs_state(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    serve(monkeypatch, {KEY: read(checks=RED)})
    watching = watcher(demo)
    woke, said = [], []
    for minutes, job_state in ((0, "running"), (29, "running"), (30, "waiting"), (31, "running"), (60, "running")):
        with state.edit(demo) as data:
            data["jobs"]["j1"]["state"] = job_state
        said.append([line for line in watch.run_pass(watching, T0 + timedelta(minutes=minutes)) if line.startswith(("PR ", "STAGE "))])
        woke.append(len(woken))
    assert (said, woke) == ([[f"PR {KEY}: checks failed [o1 j1]"], [], [], [], []], [1, 1, 2, 2, 3])
    assert woken[0] == (KEY, f"{KEY} red since {state.stamp(T0)}, owed by its builder: its checks fail at aaaaaaa: test [o1 j1]")
    watch.run_pass(watcher(demo), T0 + timedelta(minutes=61))
    assert (len(woken), state.load(demo)["prs"][KEY]["stage"]["woke"]) == (3, state.stamp(T0 + timedelta(minutes=60)))


def test_a_pr_whose_github_read_failed_keeps_its_stage_and_wakes_nobody_until_a_read_answers_for_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    reads = {KEY: read(checks=RED)}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    kept = state.load(demo)["prs"][KEY]["stage"]
    reads.clear()
    watch.run_pass(watching, T0 + timedelta(minutes=40))
    assert (state.load(demo)["prs"][KEY]["stage"], len(woken)) == (kept, 1)
    reads[KEY] = read(checks=RED)
    watch.run_pass(watching, T0 + timedelta(minutes=41))
    assert (state.load(demo)["prs"][KEY]["stage"]["woke"], len(woken)) == (state.stamp(T0 + timedelta(minutes=41)), 2)


@pytest.mark.parametrize(("required", "named"), [(1, "red"), (0, "ready")])
def test_a_green_approved_pr_forge_pr_reviewer_approved_nothing_on_reads_red_only_where_its_repositorys_rules_require_an_approving_review(
    demo: str, required: int, named: str
) -> None:
    approve(demo, KEY)
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "none", "reviews_required": required}
    assert stage.of(state.load(demo), KEY, NO_CARDS).name == named


def test_a_builders_line_carries_the_head_github_holds_for_each_open_pr_it_names_with_its_stage_and_each_commit_it_names_that_none_is_at(demo: str) -> None:
    said = f"PUSHED {state.pr_url(KEY)} at bbbbbbb"
    assert stage.heads(state.load(demo), said, "o1", NO_CARDS) == (
        f"; {KEY} is at aaaaaaa on GitHub, grading, owed by Buildkite and forge-pr-reviewer: its checks run at aaaaaaa; GitHub holds none of them at bbbbbbb"
    )
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
    assert stage.heads(state.load(demo), said, "o1", NO_CARDS) == "; GitHub holds none of them at bbbbbbb"


def test_drive_status_shows_each_open_prs_stage_now_keeping_its_since_only_while_the_watch_kept_that_stage(demo: str) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY]["stage"] = {"name": "grading", "owner": "", "why": "", "since": state.stamp(T0), "woke": None}
    data = state.load(demo)
    stage.shown(data, NO_CARDS)
    assert (data["prs"][KEY]["stage"]["name"], data["prs"][KEY]["stage"]["since"]) == ("grading", state.stamp(T0))
    data["prs"][KEY]["checks"] = "fail"
    stage.shown(data, NO_CARDS)
    assert (data["prs"][KEY]["stage"]["name"], data["prs"][KEY]["stage"]["since"], state.load(demo)["prs"][KEY]["stage"]["name"]) == ("red", None, "grading")


def test_a_yasyf_stack_lands_its_ready_bottom_pr_alone_since_gh_pr_merge_merges_one_pr_at_a_time(demo: str) -> None:
    above = "yasyf/cc-skills#8"
    with state.edit(demo) as data:
        for key, head, base, branch in ((YASYF, "aaaaaaa111", "main", "the-watch"), (above, "bbbbbbb222", "the-watch", "above-the-watch")):
            tap = {"label": "Approve", "at": state.stamp(T0), "head": head, "source": "pressed the button"}
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {
                "head": head, "base": base, "branch": branch, "checks": "pass", "review": "approved", "tap": tap, "checked": CHECKED | {"head": head}
            }
    data = state.load(demo)
    assert (land.landing_stacks(data)[above], land.units(data, [YASYF, above], NO_CARDS)) == ((YASYF, above), [(YASYF,)])


@pytest.mark.parametrize(
    ("change", "named", "woke"),
    [
        (lambda reads, data: reads.update({KEY: read(checks=RED, reviewDecision="CHANGES_REQUESTED")}), "red", [1, 2]),
        (lambda reads, data: data["jobs"]["j1"].update(state="done"), "red", [1, 2]),
        (lambda reads, data: data["prs"][KEY].update(tap={"label": "Hold", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "pressed"}), "held", [1, 1]),
        (lambda reads, data: reads.update({KEY: read(checks=GREEN, reviewDecision="APPROVED")}), "author", [1, 1]),
    ],
)
def test_a_red_prs_builder_is_woken_again_only_while_its_stage_stays_red(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], change: Callable[[dict, dict], None], named: str, woke: list[int]
) -> None:
    reads = {KEY: read(checks=RED)}
    serve(monkeypatch, reads)
    watch.run_pass(watcher(demo), T0)
    with state.edit(demo) as data:
        change(reads, data)
    counts = []
    for minutes in (15, 45):
        watch.run_pass(watcher(demo), T0 + timedelta(minutes=minutes))
        counts.append(len(woken))
    assert (state.load(demo)["prs"][KEY]["stage"]["name"], counts) == (named, woke)


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
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question(waits) for waits in waits], "closed": [], "pr_cards": []})
    watch.view.refresh_cards(name)


def test_a_pr_waiting_on_his_open_cards_is_held_by_him_and_is_freed_once_when_he_answers_the_last(
    demo: str, monkeypatch: pytest.MonkeyPatch, redraws: Redraws
) -> None:
    waiting_job(demo)
    asking(monkeypatch, demo, "demo:j1-7-a", "demo:j1-7-b")
    waits_on(demo, "demo:j1-7-a", "demo:j1-7-b")
    with state.edit(demo) as data:
        data["prs"][KEY]["card"] = CARD
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == []
    assert (state.load(demo)["prs"][KEY]["stage"]["why"], redraws.asked) == ("it waits on his cards demo:j1-7-a, demo:j1-7-b", [state.pr_url(KEY)])
    asking(monkeypatch, demo, "demo:j1-7-b")
    assert watch.run_pass(watching, T0 + timedelta(minutes=31)) == []
    assert (state.load(demo)["prs"][KEY]["waits"], redraws.asked) == (["demo:j1-7-b"], [state.pr_url(KEY)] * 2)
    asking(monkeypatch, demo)
    redraws.failure = "cc-slack exited 1"
    assert watch.run_pass(watching, T0 + timedelta(minutes=32)) == [
        f"FREE {KEY} j1 o1: demo:j1-7-b answered or done, nothing else holds it",
        f"WATCH his open card for {state.pr_url(KEY)} still names the waits that ended, since its redraw failed: cc-slack exited 1",
    ]
    assert (state.load(demo)["prs"][KEY]["stage"]["name"], redraws.asked) == ("card", [state.pr_url(KEY)] * 4)
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=33)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=62)) == []


def test_a_pr_he_approved_that_waits_on_a_running_job_waits_on_it_and_is_freed_when_the_job_ends(demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j2"] = job(goal="real run")
    waits_on(demo, "j2")
    waiting_job(demo)
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"card": CARD, "tap": {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack"}}
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    shown = state.load(demo)["prs"][KEY]["stage"]
    assert (shown["name"], shown["owner"], shown["why"], landers) == ("waiting", "what it waits on", "it waits on j2, and the watch frees it once each ends", [])
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=90)) == []
    with state.edit(demo) as data:
        data["jobs"]["j2"]["state"] = "done"
    assert f"FREE {KEY} j1 o1: j2 answered or done, nothing else holds it" in watch.run_pass(watching, T0 + timedelta(minutes=120))
    assert watch.run_pass(watching, T0 + timedelta(minutes=121)) == []
    assert landers == [(KEY,)]


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


def test_a_builder_waiting_its_turn_for_a_slot_is_no_orphan_and_the_watch_frees_the_earliest_due_once_as_each_slot_opens_while_old_held_work_holds_none(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(watch, "local_gap", lambda job_id, job, data, moment: None if job.get("agent") else REAL_LOCAL_GAP(job_id, job, data, moment))
    with state.edit(demo) as data:
        data["prs"].clear()
        data["jobs"]["j1"].update(prs=[], agent="a1", session="session-under-test")
        for number in range(2, place.LOCAL_BUILDERS + 1):
            data["jobs"][f"j{number}"] = job(agent=f"a{number}", session="session-under-test")
        data["orders"]["o1"]["jobs"] = [f"j{number}" for number in range(1, place.LOCAL_BUILDERS + 2)] + ["j12"]
        months_ago = state.stamp(T0 - timedelta(days=90))
        data["jobs"]["j12"] = job(state="waiting", questions=[state.new_question(None, "Which store?", months_ago, card=CARD)], created=months_ago, updated=months_ago)
        data["orders"]["o2"] = order(text="fix the card", jobs=["j10"])
        data["orders"]["o3"] = order(text="fix the watch", jobs=["j11"])
        for job_id, order_id, added in (("j9", "o1", 10), ("j10", "o2", 20), ("j11", "o3", 30)):
            data["jobs"][job_id] = job(order=order_id, session="session-under-test", created=state.stamp(T0 - timedelta(minutes=added)), updated=state.stamp(T0 - timedelta(minutes=10)))
            place.queue(data["jobs"][job_id], T0 - timedelta(minutes=10))
    watching = watcher(demo, T0 + timedelta(hours=1))
    assert watch.run_pass(watching, T0) == []
    assert place.positions(state.load(demo)) == {"j11": "1 of 3 in line", "j10": "2 of 3 in line", "j9": "3 of 3 in line"}
    assert watch.run_pass(watching, T0 + timedelta(minutes=31)) == []
    with state.edit(demo) as data:
        data["jobs"]["j2"]["state"] = "done"
    freed = T0 + timedelta(minutes=33)
    assert watch.run_pass(watching, freed) == ["FREE j11 o3: its turn came as a builder slot opened on this Mac, 7 of its 8 held, nothing else holds it"]
    data = state.load(demo)
    assert {field: data["jobs"]["j11"][field] for field in ("state", "waits", "agent", "slot")} == {"state": "waiting", "waits": [], "agent": None, "slot": state.stamp(freed)}
    assert place.turns(data, freed) == ([], place.LOCAL_BUILDERS)
    assert "j12" not in place.local_builders(data, freed)
    assert watch.run_pass(watching, T0 + timedelta(minutes=35)) == []
    with state.edit(demo) as data:
        data["jobs"]["j11"].update(state="running", agent="a11")
    assert watch.run_pass(watching, T0 + timedelta(minutes=37)) == []
    with state.edit(demo) as data:
        data["jobs"]["j3"]["state"] = "done"
    assert watch.run_pass(watching, T0 + timedelta(minutes=39)) == ["FREE j10 o2: its turn came as a builder slot opened on this Mac, 7 of its 8 held, nothing else holds it"]
    assert place.waiting_builders(state.load(demo)) == ["j9"]


def release(number: int, published: str, components: str, production: str | None = None, day: str = "2026-11-09") -> dict:
    return {
        "name": f"releases/{day}/{number}",
        "tag_name": f"release/releases-{day}-{number}",
        "published_at": published,
        "draft": False,
        "prerelease": False,
        "body": f"Released from `c{number}` by [its build](https://ci.example/{number}).\n\nComponents: {components}\n\nProduction: {production or components}",
    }


def test_a_job_waiting_on_a_release_is_freed_once_the_newest_release_shipping_its_service_holds_its_merged_pr(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    published = [release(1, "2026-11-09T14:00:00Z", "api, dashboard"), release(9, "2026-11-09T16:00:00Z", "api", production="none")]
    statuses, calls = ["behind", "ahead"], []

    def gh(cmd: list[str], **_: object) -> str:
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


def test_a_staging_only_release_frees_no_release_wait_and_the_next_release_reaching_production_frees_it(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    staged = release(27, "2026-11-12T22:01:54Z", "sandsql, api, dashboard", production="none", day="2026-11-12")
    as_published = staged | {"body": staged["body"].removesuffix("\n\nProduction: none")}
    published, calls = [as_published], []

    def gh(cmd: list[str], **_: object) -> str:
        calls.append(cmd[2])
        return json.dumps(published) if cmd[2].endswith("/releases?per_page=100") else "ahead\n"

    monkeypatch.setattr(watch.github, "run", gh)
    with state.edit(demo) as data:
        data["prs"][KEY].update(state="merged", merged_sha="5273c9b2")
        data["jobs"]["j1"].update(state="waiting", said="WAITING release of sandsql", waits=[f"release:sandsql:{KEY}"], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo, T0 + timedelta(hours=1))
    assert watch.run_pass(watching, T0) == []
    published[0] = staged
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []
    assert not any("/compare/" in call for call in calls)
    published.append(release(28, "2026-11-12T22:17:37Z", "sandsql", day="2026-11-12"))
    assert watch.run_pass(watching, T0 + timedelta(minutes=2)) == [f"FREE j1 o1: release releases/2026-11-12/28 ships {KEY} on sandsql, nothing else holds it"]
    assert calls[-1] == "repos/Forge-AI/monorepo/compare/5273c9b2...release/releases-2026-11-12-28"


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
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question("demo:j1-7-a"), question("demo:j1-7-b") | {"url": other}], "closed": [], "pr_cards": []})
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


def test_a_built_job_is_told_once_though_its_sub_coordinator_is_bound_while_the_drives_watch_posts(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question("demo:j1-7-a")], "closed": [], "pr_cards": []})
    watch.view.refresh_cards(demo)
    with state.edit(demo) as data:
        data["orders"]["o1"]["project"] = "Iris"
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"] |= {"state": "built", "branch": "lane/built", "waits": ["demo:j1-7-a"]}
    iris = watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0, project="Iris")
    posts: list[dict] = []
    nested: list[list[str]] = []

    def post(name: str, said: dict) -> dict:
        posts.append(said)
        if not nested:
            nested.append([])
            sub_coordinated(demo)
            nested[0] = watch.run_pass(iris, T0)
        return {"permalink": POSTED}

    monkeypatch.setattr(talk, "posted", post)
    assert watch.run_pass(watcher(demo), T0) == waiting("demo:j1-7-a")
    assert (posts, nested) == ([paused()], [[]])


def test_a_built_job_whose_watch_died_after_claiming_its_card_and_before_posting_is_told_by_the_next_watch(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    asking(monkeypatch, demo, "demo:j1-7-a")
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"] |= {"state": "built", "branch": "lane/built", "waits": ["demo:j1-7-a"]}
    posts: list[dict] = []

    def post(name: str, said: dict) -> dict:
        posts.append(said)
        if len(posts) == 1:
            raise OSError("the watch was killed")
        return {"permalink": POSTED}

    monkeypatch.setattr(talk, "posted", post)
    gone = {"pid": 999999999, "started": "Mon Nov  9 08:00:00 2026"}
    with monkeypatch.context() as dying, pytest.raises(OSError):
        dying.setattr(watch.os, "getpid", lambda: gone["pid"])
        dying.setattr(watch, "started", lambda pid: gone["started"])
        watch.run_pass(watcher(demo), T0)
    assert state.load(demo)["jobs"]["j1"]["waiting_claims"] == {"demo:j1-7-a": gone}
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=1)) == waiting("demo:j1-7-a")
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=2)) == []
    assert posts == [paused()] * 2
    job = state.load(demo)["jobs"]["j1"]
    assert (job["waiting_said"], job["waiting_claims"]) == (["demo:j1-7-a"], {})


def test_a_built_jobs_claim_whose_watch_died_and_whose_pid_now_runs_another_process_is_taken_by_the_next_watch(
    demo: str, monkeypatch: pytest.MonkeyPatch, told: list[dict]
) -> None:
    serve(monkeypatch, {})
    asking(monkeypatch, demo, "demo:j1-7-a")
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"] |= {"state": "built", "branch": "lane/built", "waits": ["demo:j1-7-a"], "waiting_claims": {"demo:j1-7-a": {"pid": os.getpid(), "started": "Mon Nov  9 08:00:00 2026"}}}
    assert watch.run_pass(watcher(demo), T0) == waiting("demo:j1-7-a")
    assert told == [paused()]
    with state.edit(demo) as data:
        data["jobs"]["j1"] |= {"waiting_said": [], "waiting_claims": {"demo:j1-7-a": {"pid": os.getpid(), "started": watch.started(os.getpid())}}}
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=1)) == []
    assert told == [paused()]


def test_a_built_job_asking_him_its_own_question_is_freed_only_once_he_answers_it_too(demo: str, monkeypatch: pytest.MonkeyPatch, told: list[dict]) -> None:
    serve(monkeypatch, {})
    asked = f"{CARD[:-1]}9"
    own = question("demo:j1-9") | {"url": asked}
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question("demo:j1-7-a"), own], "closed": [], "pr_cards": []})
    watch.view.refresh_cards(demo)
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"] |= {"state": "built", "branch": "lane/built", "waits": ["demo:j1-7-a"], "questions": [state.new_question("/asks/j1.md", "Pick?", state.stamp(T0), card=asked)]}
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == waiting("demo:j1-7-a")
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [own], "closed": [], "pr_cards": []})
    watch.view.refresh_cards(demo)
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []
    assert state.load(demo)["jobs"]["j1"]["waits"] == ["demo:j1-7-a"]
    asking(monkeypatch, demo)
    assert watch.run_pass(watching, T0 + timedelta(minutes=2)) == []
    with state.edit(demo) as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("answered", "Yes", asked, seq="#9")
    assert watch.run_pass(watching, T0 + timedelta(minutes=3)) == ["FREE j1 o1: demo:j1-7-a answered or done, nothing else holds it, built on lane/built"]


def test_a_card_holding_a_built_job_is_told_once_in_its_own_thread_and_again_after_a_failed_post(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    asking(monkeypatch, demo, "demo:j1-7-a")
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["orders"]["o1"]["thread"] = ["D123", "1794200000.000100"]
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
    assert ", waits on j1; " in capsys.readouterr().out
    asking(monkeypatch, demo, "demo:j1-7-a")
    assert cli.main(["pr", "set", KEY, "--waits", "demo:gone-1", "--drive", demo]) == 2
    assert "--waits demo:gone-1" in capsys.readouterr().err
    assert cli.main(["pr", "set", KEY, "--waits", "demo:j1-7-a", "--waits", CARD, "--drive", demo]) == 0
    assert state.load(demo)["prs"][KEY]["waits"] == ["j1", "demo:j1-7-a", CARD]
    assert cli.main(["pr", "set", KEY, "--no-waits", "--drive", demo]) == 0
    assert state.load(demo)["prs"][KEY]["waits"] == []


def test_a_wait_added_to_a_pr_redraws_his_open_card_in_place_never_withdraws_it_and_a_fresh_tap_draws_its_card_while_it_holds(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], withdrawals: Withdrawals, redraws: Redraws
) -> None:
    asking(monkeypatch, demo, "demo:j1-7-a")
    assert cli.main(["pr", "set", KEY, "--waits", "demo:j1-7-a", "--drive", demo]) == 0
    assert (withdrawals.asked, redraws.asked) == ([], [])
    tap_card = pr_card(KEY, url="https://acme.slack.com/archives/D123/p1794200000000200")
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question("demo:j1-7-a")], "closed": [], "pr_cards": [tap_card]})
    assert cli.main(["pr", "set", KEY, "--waits", "j1", "--drive", demo]) == 0
    assert (withdrawals.asked, redraws.asked) == ([], [state.pr_url(KEY)])
    assert capsys.readouterr().out.strip().endswith("redrawing in place the 1 open card(s) of his carrying it, Approve and Hold kept, to say what it waits on")
    redraws.failure = "cc-slack exited 1"
    assert cli.main(["pr", "set", KEY, "--waits", "j1", "--drive", demo]) == 2
    assert "redrawing his open card in place failed, so it does not name them yet: cc-slack exited 1; run this again" in capsys.readouterr().err
    assert cli.main(["pr", "set", KEY, "--tap", "--serves", "o1", "--design", "none", "--drive", demo]) == 0
    assert state.load(demo)["prs"][KEY]["wants_tap"] is True


def test_a_release_wait_whose_release_is_already_out_is_never_stored(demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    shipped = "Forge-AI/monorepo#43"
    with state.edit(demo) as data:
        data["prs"][shipped] = state.new_pr(state.pr_url(shipped), "j1", "o1") | {"state": "merged", "merged_sha": "abc123"}
    published = [release(2, "2026-11-09T15:00:00Z", "dashboard")]
    monkeypatch.setattr(watch.github, "run", lambda cmd, **_: json.dumps(published) if cmd[2].endswith("/releases?per_page=100") else "ahead\n")
    assert cli.main(["pr", "set", KEY, "--waits", f"release:dashboard:{shipped}", "--waits", f"release:api:{shipped}", "--drive", demo]) == 0
    assert f"\nrelease releases/2026-11-09/2 ships {shipped} on dashboard, so it is not a wait" in capsys.readouterr().out
    assert state.load(demo)["prs"][KEY]["waits"] == [f"release:api:{shipped}"]


def test_job_held_names_his_open_cards_holding_a_jobs_pr_until_he_answers_or_it_has_an_open_pr(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asking(monkeypatch, demo, "demo:j1-7-a")
    with state.edit(demo) as data:
        state.take_result(data, "j1", "BUILT lane/built, waits on demo:j1-7-a")
        data["prs"].pop(KEY, None)
        data["jobs"]["j1"]["prs"] = []
    assert render.job_line("j1", state.load(demo)["jobs"]["j1"]) == "j1 builder built on lane/built: watch; waits on demo:j1-7-a"
    capsys.readouterr()
    assert cli.main(["job", "held", "j1", "--drive", demo]) == 0
    assert capsys.readouterr().out == "demo:j1-7-a\n"
    monkeypatch.setenv(state.OWN_JOB_ENV, "j1")
    assert cli.main(["job", "held", "--json", "--drive", demo]) == 0
    assert json.loads(capsys.readouterr().out) == {"id": "j1", "cards": ["demo:j1-7-a"]}
    assert cli.main(["pr", "add", state.pr_url(KEY), "--job", "j1", "--drive", demo]) == 0
    capsys.readouterr()
    assert cli.main(["job", "held", "j1", "--drive", demo]) == 0
    assert capsys.readouterr().out == "j1: no open card of his holds its PR\n"


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


def test_job_set_never_waits_on_a_job_and_the_refusal_names_the_card_or_pr_to_wait_on(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asking(monkeypatch, demo, "demo:j1-7-a")
    with state.edit(demo) as data:
        data["jobs"]["j2"] = job(goal="root fix", state="waiting")
        data["jobs"]["j3"] = job(kind="design", goal="asked him", state="waiting", questions=[state.new_question(None, "Pick?", state.stamp(T0)) | {"waits": "demo:j1-7-a"}])
    assert cli.main(["job", "set", "j2", "--waits", "j3", "--drive", demo]) == 2
    assert capsys.readouterr().err.strip().endswith(
        "--waits j3: a job waits on his card, a PR the drive tracks, a release or the change a builder or codex job builds, and on another job only"
        " as a build order's designer, builder or codex lane added waiting on its reader, drive job add <kind> --order <o> --waits <the reader>;"
        " open on j3: demo:j1-7-a; wait on the one this job needs, as --waits demo:j1-7-a"
    )
    assert cli.main(["job", "set", "j2", "--waits", "j1", "--drive", demo]) == 2
    assert capsys.readouterr().err.strip().endswith(f"--waits j1: j1 already opened {KEY}, so wait on the PR this job builds on, as --waits {KEY}")
    assert cli.main(["job", "set", "j2", "--waits", KEY, "--waits", "demo:j1-7-a", "--drive", demo]) == 0
    assert state.load(demo)["jobs"]["j2"]["waits"] == [KEY, "demo:j1-7-a"]


def test_a_pr_whose_head_moved_to_a_change_nobody_judged_gets_its_card_and_its_check_against_his_words_at_that_head_whatever_its_jobs_state(
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str], draws: Draws
) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"wants_tap": True, "checked": CHECKED | {"patch": "a change judged before"}}
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert (draws.keys, checkers) == ([[KEY]], [KEY])
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=30)) if line.startswith(("PR ", "STAGE "))]
    assert (state.load(demo)["prs"][KEY]["checked"], state.load(demo)["prs"][KEY]["stage"]["name"]) == (None, "card")
    with state.edit(demo) as data:
        data["prs"][KEY]["checked"] = CHECKED | {"head": "bbbbbbb222"}
        data["jobs"]["j1"]["carries"]["o2"] = ["no staff flag"]
    assert not state.checked(state.load(demo), state.load(demo)["prs"][KEY])


def test_a_pr_whose_card_draw_fails_stays_uncarded_and_is_drawn_again_each_read_said_to_the_meta_session_from_the_third_failure(
    demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws
) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    sent = posts(monkeypatch)
    draws.failure = "cc-slack exited 1"
    watching = watcher(demo)
    for minute in range(3):
        watch.run_pass(watching, T0 + timedelta(minutes=minute))
    pr = state.load(demo)["prs"][KEY]
    assert (draws.keys, pr["card"], pr["stage"]["name"]) == ([[KEY]] * 3, None, "uncarded")
    assert [entry for entry in posted(sent) if entry[1] == f"meta@{demo}"] == [
        ("handoff", f"meta@{demo}", f"WATCH draw of his card on {KEY} failed 3 times in a row: cc-slack exited 1")
    ]
    draws.failure = None
    watch.run_pass(watching, T0 + timedelta(minutes=3))
    watch.run_pass(watching, T0 + timedelta(minutes=4))
    pr = state.load(demo)["prs"][KEY]
    assert (len(draws.keys), pr["card"], pr["stage"]["name"]) == (4, draws.link, "card")


@pytest.mark.parametrize("said", [f"PUSHED {state.pr_url(KEY)}", f"WAITING {state.pr_url(KEY)} to land and release"])
@pytest.mark.parametrize("job_state", ["running", "waiting"])
def test_an_unchecked_pr_starts_one_check_against_his_words_whatever_its_jobs_state_whose_checked_line_wakes_no_one_and_whose_not_ready_line_goes_to_its_builder(
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str], judge: Judge, said: str, job_state: str, woken: list[tuple[str, str]]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(state, "now", lambda: T0 + timedelta(minutes=1))
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state=job_state, said=said, returned=state.stamp(T0))
        data["prs"][KEY] |= {"wants_tap": True, "checked": None}
    watching = watcher(demo)
    assert (watch.run_pass(watching, T0 + timedelta(minutes=1)), checkers) == ([], [KEY])
    assert (watch.run_pass(watching, T0 + timedelta(minutes=2)), checkers) == ([], [KEY])
    judge.against = [{"order": "o1", "words": "ship the watch", "change": "watch.py stops the watch"}]
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    assert watch.run_pass(watching, T0 + timedelta(minutes=3)) == []
    [(key, refused)] = woken
    assert key == KEY and refused.endswith(" [o1 j1]")
    assert refused.startswith(f'PR {KEY}: NOT READY: {KEY} go against his words, judged from each PR\'s title and diff: o1 "ship the watch": watch.py stops the watch.')
    assert (watch.run_pass(watching, T0 + timedelta(minutes=4)), checkers) == ([], [KEY])
    with state.edit(demo) as data:
        data["jobs"]["j1"] |= {"said": f"READY {state.pr_url(KEY)}", "returned": state.stamp(T0 + timedelta(minutes=5))}
    assert (watch.run_pass(watching, T0 + timedelta(minutes=5)), checkers) == ([], [KEY, KEY])
    judge.against = []
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    checked = f"PR {KEY}: CHECKED against the words of o1 at aaaaaaa111, so his Approve lands it once it is green [o1 j1]"
    assert watch.run_pass(watching, T0 + timedelta(minutes=6)) == [checked]
    assert not watch.wakes(checked, state.load(demo))
    assert (state.load(demo)["prs"][KEY]["checked"]["head"], watch.run_pass(watching, T0 + timedelta(minutes=7)), checkers) == ("aaaaaaa111", [], [KEY, KEY])


def test_a_green_pr_waiting_on_his_tap_gets_its_card_at_once_while_its_check_runs_or_says_not_ready_and_its_new_head_is_checked_again(
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str], judge: Judge, draws: Draws, woken: list[tuple[str, str]]
) -> None:
    reads = {KEY: read(checks=GREEN, reviewDecision="APPROVED")}
    serve(monkeypatch, reads)
    monkeypatch.setattr(state, "now", lambda: T0)
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"READY {state.pr_url(KEY)}", returned=state.stamp(T0))
        data["prs"][KEY] |= {"wants_tap": True, "checked": None}
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert (draws.keys, checkers) == ([[KEY]], [KEY])
    judge.against = [{"order": "o1", "words": "ship the watch", "change": "watch.py stops the watch"}]
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    refused = [key for key, text in woken if f"PR {KEY}: NOT READY: " in text]
    assert (refused, state.load(demo)["prs"][KEY]["stage"]["name"], draws.keys) == ([KEY], "card", [[KEY]])
    reads[KEY] = read(headRefOid="bbbbbbb222", checks=GREEN, reviewDecision="APPROVED")
    watch.run_pass(watching, T0 + timedelta(minutes=2))
    assert checkers == [KEY, KEY]
    judge.against = []
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    watch.run_pass(watching, T0 + timedelta(minutes=3))
    assert (state.load(demo)["prs"][KEY]["checked"]["head"], draws.keys) == ("bbbbbbb222", [[KEY]])


def test_a_check_names_every_pr_its_job_and_the_job_it_was_handed_from_carry_with_merged_ones_marked_so_a_part_a_merged_pr_delivers_gets_no_not_ready(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    merged, closed = "yasyf/cc-skills#4484", "yasyf/cc-skills#4490"
    with state.edit(demo) as data:
        data["jobs"]["j1"]["prs"] += [state.pr_url(merged), state.pr_url(closed), state.pr_url(OTHER)]
        data["jobs"]["j2"] = job(prs=[state.pr_url(KEY)])
        data["prs"][KEY] |= {"job": "j2", "wants_tap": True, "checked": None}
        data["prs"][merged] = state.new_pr(state.pr_url(merged), "j1", "o1") | {"state": "merged", "title": "Fix: a Sprite lane gets the wasm toolchain"}
        data["prs"][closed] = state.new_pr(state.pr_url(closed), "j1", "o1") | {"state": "closed", "title": "Fix: the toolchain, abandoned"}
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"title": "Fix: the stack's top"}
    delivered = f'its job\'s merged PRs already deliver their share of these orders: {merged} "Fix: a Sprite lane gets the wasm toolchain"'
    told: list[str] = []
    wasm = {"order": "o1", "words": "ship the watch", "change": "no PR gives Sprites the wasm toolchain"}
    monkeypatch.setattr(served, "judged", lambda name, said, changes: told.append(said) or ([] if delivered in said else [wasm]))
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    [said] = told
    assert f'{delivered}\nits job\'s other open PRs, each judged on its own, deliver their share of these orders: {OTHER} "Fix: the stack\'s top"' in said
    assert closed not in said
    assert state.load(demo)["prs"][KEY]["checker"]["outcome"].startswith(f"{served.CHECKED} against the words of o1 at aaaaaaa111")


def test_a_check_whose_judge_failed_is_said_nowhere_and_tried_again_ten_minutes_after_it_started(
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(state, "now", lambda: T0)
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"READY {state.pr_url(KEY)}")
        data["prs"][KEY] |= {"wants_tap": True, "checked": None}

    def failing(name: str, said: str, changes: dict) -> list[dict]:
        raise state.DriveError("the judge timed out")

    monkeypatch.setattr(served, "judged", failing)
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    assert state.load(demo)["prs"][KEY]["checker"]["outcome"] == "check failed: the judge timed out"
    assert (watch.run_pass(watching, T0 + timedelta(minutes=9)), checkers) == ([], [KEY])
    assert (watch.run_pass(watching, T0 + timedelta(minutes=10)), checkers) == ([], [KEY, KEY])


def test_a_ports_change_after_his_approve_lands_at_its_first_green_head_and_starts_no_check_against_his_words(
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str], landers: list[tuple[str, ...]]
) -> None:
    state.keep_report(demo, "j1", f"READY {state.pr_url(KEY)}", f"READY {state.pr_url(KEY)}\nSERVES: port of #50974\n")
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"READY {state.pr_url(KEY)}")
        data["prs"][KEY] |= {"wants_tap": True, "checked": None, "port_of": 50974, "card": CARD, "tap": {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack"}}
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks=GREEN, reviewDecision="APPROVED")})
    watch.run_pass(watcher(demo), T0)
    assert (checkers, landers) == ([], [(KEY,)])


def test_a_pr_waiting_on_his_tap_draws_his_card_from_the_watch_on_its_first_read_with_no_coordinator_turn_and_no_record(
    demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws
) -> None:
    from drive import tools

    waiting_job(demo)
    with state.edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    sent: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: sent.append((lane, records)) or ["#9"])
    watching = watcher(demo)
    lines = watch.run_pass(watching, T0)
    assert lines == [f"PR {KEY}: checks passed [o1 j1]", f"PR {KEY}: approved [o1 j1]"]
    assert not any(watch.wakes(line, state.load(demo)) for line in lines)
    assert (draws.keys, sent) == ([[KEY]], [])
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []
    assert (draws.keys, state.load(demo)["prs"][KEY]["card"]) == ([[KEY]], draws.link)


THIRD = "Forge-AI/monorepo#44"
STACKED = (KEY, OTHER, THIRD)


def three_stacked(demo: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    waiting_job(demo)
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(text="split the stack", jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", prs=[state.pr_url(OTHER), state.pr_url(THIRD)], state="waiting")
        data["prs"][KEY]["wants_tap"] = True
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o2") | {"checked": CHECKED | {"head": "bbbbbbb222", "orders": ["o2"]}}
        data["prs"][THIRD] = state.new_pr(state.pr_url(THIRD), "j2", "o2") | {"checked": CHECKED | {"head": "ccccccc333", "orders": ["o2"]}}
    monkeypatch.setattr(watch.view, "cards", lambda drive: watch.view.NO_CARDS)
    reads = {key: stacked_read(head) for key, head in zip(STACKED, ("aaaaaaa111", "bbbbbbb222", "ccccccc333"))}
    serve(monkeypatch, reads)
    return reads


def stacked_read(head: str, **overrides: object) -> dict:
    return read(**{"headRefOid": head, "checks": GREEN, "reviewDecision": "APPROVED", "native": [(key, "open") for key in STACKED]} | overrides)


def test_the_prs_of_one_native_stack_waiting_on_his_tap_draw_one_card_and_one_reworked_to_his_words_draws_its_own_once_its_rework_ends(
    demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws
) -> None:
    three_stacked(demo, monkeypatch)
    with state.edit(demo) as data:
        state.rework(data["prs"][THIRD], "o2", "Split the stack by its native GitHub stack")
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert draws.keys == [[KEY, OTHER]]
    with state.edit(demo) as data:
        data["prs"][THIRD]["rework"] = None
    watch.run_pass(watching, T0 + timedelta(minutes=10))
    assert draws.keys == [[KEY, OTHER], [THIRD]]


def test_a_restack_moving_a_head_under_his_card_draws_no_other_card(demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws) -> None:
    reads = three_stacked(demo, monkeypatch)
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    watch.run_pass(watching, T0 + timedelta(minutes=10))
    assert draws.keys == [list(STACKED)]
    reads[OTHER] = stacked_read("ddddddd444")
    watch.run_pass(watching, T0 + timedelta(minutes=20))
    watch.run_pass(watching, T0 + timedelta(minutes=30))
    assert (draws.keys, state.load(demo)["prs"][OTHER]["head"]) == ([list(STACKED)], "ddddddd444")


def test_a_pr_on_another_open_prs_branch_draws_one_card_with_it_before_either_is_checked(demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws) -> None:
    waiting_job(demo)
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(text="a cloud agent signs in as its own principal", jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", prs=[state.pr_url(OTHER)], state="waiting")
        data["prs"][KEY] |= {"wants_tap": True, "checked": None}
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o2")
    serve(
        monkeypatch,
        {
            OTHER: read(headRefOid="bbbbbbb222", baseRefName="dev", headRefName="cloud-agent-identity", checks=GREEN, reviewDecision="APPROVED"),
            KEY: read(baseRefName="cloud-agent-identity", headRefName="cloud-agent-pod-identity", checks=GREEN, reviewDecision="APPROVED"),
        },
    )
    watch.run_pass(watcher(demo), T0)
    assert [sorted(keys) for keys in draws.keys] == [sorted([KEY, OTHER])]


def test_sibling_prs_of_one_order_in_no_stack_draw_one_card_and_a_pr_of_another_order_its_own(demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws) -> None:
    waiting_job(demo)
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(text="a cloud agent signs in as its own principal", jobs=["j2"])
        data["jobs"]["j1"]["prs"].append(state.pr_url(OTHER))
        data["jobs"]["j2"] = job(order="o2", prs=[state.pr_url(THIRD)], state="waiting")
        data["prs"][KEY]["wants_tap"] = True
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1")
        data["prs"][THIRD] = state.new_pr(state.pr_url(THIRD), "j2", "o2")
    heads = {KEY: "aaaaaaa111", OTHER: "bbbbbbb222", THIRD: "ccccccc333"}
    serve(monkeypatch, {key: read(headRefOid=head, baseRefName="dev", headRefName=f"cutover-{key.split('#')[1]}", checks=GREEN, reviewDecision="APPROVED") for key, head in heads.items()})
    watch.run_pass(watcher(demo), T0)
    assert draws.keys == [[KEY, OTHER], [THIRD]]


def test_a_pr_reworked_to_his_words_loses_the_card_left_up_wakes_its_builder_and_draws_a_fresh_card_once_its_builders_ready_says_how_they_are_met(
    demo: str, monkeypatch: pytest.MonkeyPatch, withdrawals: Withdrawals, draws: Draws, woken: list[tuple[str, str]]
) -> None:
    waiting_job(demo)
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"wants_tap": True, "card": CARD}
        state.rework(data["prs"][KEY], "o2", "Topology goes in the meta table")
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    withdrawals.cards = [CARD]
    watch.run_pass(watcher(demo), T0)
    reason = 'Being reworked to your words "Topology goes in the meta table". A fresh card asks your tap once its builder says how they are met'
    pr = state.load(demo)["prs"][KEY]
    assert (withdrawals.asked, pr["card"], pr["stage"]["name"], [key for key, _ in woken], draws.keys) == ([(state.pr_url(KEY), reason)], None, "rework", [KEY], [])
    watch.run_pass(watcher(demo), T0 + timedelta(minutes=5))
    assert (len(withdrawals.asked), len(woken), draws.keys) == (1, 1, [])
    with state.edit(demo) as data:
        state.take_result(data, "j1", f"READY {state.pr_url(KEY)}", f"READY {state.pr_url(KEY)}\nCHANGE: it reads the meta table")
    watch.run_pass(watcher(demo), T0 + timedelta(minutes=10))
    assert (state.load(demo)["prs"][KEY]["rework"], draws.keys) == (None, [[KEY]])


@pytest.mark.parametrize("wants_tap", [False, True])
def test_an_exempt_pr_whose_landers_try_ended_unlanded_wakes_the_session_holding_its_order_at_once_and_every_thirty_minutes(
    demo: str, monkeypatch: pytest.MonkeyPatch, wants_tap: bool, landers: list[tuple[str, ...]]
) -> None:
    waiting_job(demo)
    tried = {"head": "aaaaaaa111", "pid": 1, "at": state.stamp(T0), "outcome": "not landed: stack-enqueue --watch exited 1: checks failed", "below": []}
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"wants_tap": wants_tap, "exempt": "incident fix", "lander": tried}
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    line = (
        f"STAGE {KEY} unlanded since {state.stamp(T0)}, owed by the session holding its order: its lander's try at aaaaaaa, started {state.stamp(T0)},"
        " ended not landed: stack-enqueue --watch exited 1: checks failed [o1 j1]"
    )
    assert line in watch.run_pass(watching, T0)
    assert watch.wakes(line, state.load(demo))
    assert line not in watch.run_pass(watching, T0 + timedelta(minutes=29))
    assert line in watch.run_pass(watching, T0 + timedelta(minutes=30))
    assert "checks pass, review approved, no tap needed, exempt: incident fix; unlanded since " in render.pr_line(KEY, state.load(demo)["prs"][KEY], None)
    assert landers == []


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
    assert watch.run_pass(watching, T0) == [f"PR {KEY}: checks passed [o1 j1]", f"PR {KEY}: approved [o1 j1]"]
    reads[other] = read(state="MERGED", mergeCommit="ccc333")
    freed = f"FREE {KEY} j1 o1: {other} answered or done, nothing else holds it"
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == [f"PR {other}: merged [o1 j2]", freed]
    assert state.load(demo)["jobs"]["j2"]["state"] == "running"
    assert watch.wakes(freed, state.load(demo))


@pytest.mark.parametrize(("ended", "said"), [("MERGED", "merged"), ("CLOSED", "closed")])
def test_a_pr_that_wants_no_tap_is_never_said_to_have_no_card_and_only_its_moves_and_end_are_said(demo: str, monkeypatch: pytest.MonkeyPatch, ended: str, said: str) -> None:
    reads = {KEY: read(checks=GREEN, reviewDecision="APPROVED")}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    pr = f'PR {KEY}:'
    assert watch.run_pass(watching, T0) == [f"{pr} checks passed [o1 j1]", f"{pr} approved [o1 j1]"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=300)) == []
    reads[KEY] = read(checks=RED, reviewDecision="APPROVED")
    assert watch.run_pass(watching, T0 + timedelta(minutes=301)) == [f"{pr} checks failed [o1 j1]"]
    reads[KEY] = read(checks=GREEN, reviewDecision="APPROVED")
    assert watch.run_pass(watching, T0 + timedelta(minutes=302)) == [f"{pr} checks passed [o1 j1]"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=400)) == []
    reads[KEY] = read(state=ended, checks=GREEN, reviewDecision="APPROVED")
    assert watch.run_pass(watching, T0 + timedelta(minutes=401)) == [f"{pr} {said} [o1 j1]"]


def hold(name: str) -> None:
    with state.edit(name) as data:
        data["prs"][KEY] |= {"wants_tap": True, "tap": {"label": "Hold", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "pressed the button"}}


@pytest.mark.parametrize(("count", "queries"), [(1, [1]), (120, [25, 25, 25, 25, 20])])
def test_a_watch_pass_reads_github_twenty_five_open_prs_a_call_however_many_it_watches(
    demo: str, monkeypatch: pytest.MonkeyPatch, count: int, queries: list[int]
) -> None:
    with state.edit(demo) as data:
        for number in range(1, count):
            key = f"Forge-AI/monorepo#{1000 + number}"
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"head": f"head{number}"}
    asked: list[list[str]] = []
    monkeypatch.setattr(github, "fetch", REAL_FETCH)
    monkeypatch.setattr(github, "run", lambda cmd, **_: asked.append(cmd) or json.dumps({"data": {}}))
    watch.run_pass(watcher(demo), T0)
    assert [(cmd[:3], cmd[4].count("pullRequest(")) for cmd in asked] == [(["gh", "api", "graphql"], size) for size in queries]


def test_a_held_green_approved_pr_is_never_said_to_have_no_card(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    hold(demo)
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=300)) == []


def test_a_held_red_pr_wakes_no_builder_while_his_hold_stands_and_its_builder_at_once_when_the_hold_lifts(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    hold(demo)
    waiting_job(demo)
    serve(monkeypatch, {KEY: read(checks=RED)})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == []
    assert (state.load(demo)["prs"][KEY]["stage"]["name"], woken) == ("held", [])
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"tap": None, "card": CARD}
    watch.run_pass(watching, T0 + timedelta(minutes=31))
    assert (state.load(demo)["prs"][KEY]["stage"]["name"], [key for key, _ in woken]) == ("red", [KEY])


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
        f"TAP Hold {KEY} [o1 j1]",
        'DUE o1 "ship" was due 10:00 AM: no line in its thread since 9:00 AM',
        'ORPHAN o1 "ship": no job',
        'ANSWER a1 "one new tool": 1 card with no mark',
        "JOB j1 o1 READY https://github.com/Forge-AI/monorepo/pull/42",
        'LANE j1 o1 died on Sprite s1: gone, while it worked on "watch".',
        "FREE j1 o1: demo:j1-7-a answered or done, nothing else holds it, built on lane/built",
    ]
    batched = [
        f"TAP Approve {KEY} [o1 j1]",
        f"PR {KEY}: checks passed [o1 j1]",
        f"PR {KEY}: approved [o1 j1]",
        f"PR {KEY}: checks failed [o1 j1]",
        "EVENT note",
        "WAITING-ON-HIM demo:j1-7-a blocks j1, built on lane/built, since 9:00 AM, posted https://acme.slack.com/archives/D123/p1",
    ]
    assert watch.sent(watcher(demo), "root", waking + batched, T0) == []
    assert posted(sent) == [("handoff", f"root@{demo}", line) for line in waking] + [("evidence", f"root@{demo}", line) for line in batched]
    assert len({argv[argv.index("--topic") + 1] for argv in sent}) == len(sent)


@pytest.mark.parametrize(("job_state", "merged_kind"), [("running", "handoff"), ("waiting", "handoff"), ("done", "evidence")])
def test_a_prs_red_wakes_no_coordinator_since_its_stage_wakes_its_builder_and_its_merge_wakes_it_until_its_job_is_done(
    demo: str, monkeypatch: pytest.MonkeyPatch, job_state: str, merged_kind: str
) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"]["state"] = job_state
    sent = posts(monkeypatch)
    merged = f"PR {KEY}: merged [o1 j1]"
    reds = [f"PR {KEY}: {move} [o1 j1]" for move in ("checks failed", "conflicts with its base", "changes requested")]
    watch.sent(watcher(demo), "root", [merged, *reds], T0)
    assert posted(sent) == [(merged_kind, f"root@{demo}", merged), *[("evidence", f"root@{demo}", line) for line in reds]]


@pytest.mark.parametrize(("wants_tap", "exempt"), [(True, "incident fix"), (False, "incident fix"), (True, None), (False, None)])
def test_a_monorepo_pr_going_green_and_approved_wakes_no_one_since_the_lander_queues_its_exempt_landing_and_the_watch_draws_his_card(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], draws: Draws, wants_tap: bool, exempt: str | None
) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"wants_tap": wants_tap, "exempt": exempt, "review": "approved"}
        data["jobs"]["j1"]["state"] = "waiting"
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    sent = posts(monkeypatch)
    watching = watcher(demo)
    assert watch.sent(watching, "root", watch.run_pass(watching, T0), T0) == []
    assert posted(sent) == [("evidence", f"root@{demo}", f"PR {KEY}: checks passed [o1 j1]")]
    assert (draws.keys, landers) == ([[KEY]] * (wants_tap and not exempt), [(KEY,)] * bool(exempt))


NO_CARDS = {"team_url": "", "questions": [], "closed": [], "pr_cards": []}


def approve(name: str, *keys: str) -> None:
    with state.edit(name) as data:
        for key in keys:
            data["prs"][key] |= {"wants_tap": True, "tap": {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "pressed the button"}}


def stacked(name: str, native: list[str] | None = None) -> None:
    with state.edit(name) as data:
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"head": "bbbbbbb222", "checked": CHECKED | {"head": "bbbbbbb222"}}
        for key in (KEY, OTHER):
            data["prs"][key] |= {"stack": [KEY, OTHER], "native": [KEY, OTHER] if native is None else native, "checks": "pass", "review": "approved"}


def test_his_approve_starts_one_lander_once_its_pr_is_green_and_conflict_free_and_again_only_on_a_head_no_lander_tried(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    approve(demo, KEY)
    monkeypatch.setattr(watch.view, "cards", lambda drive: NO_CARDS)
    reads: dict[str, dict] = {}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    started = []
    for minute, (checks, mergeable) in enumerate([(RED, "MERGEABLE"), (GREEN, "CONFLICTING"), (GREEN, "MERGEABLE"), (GREEN, "MERGEABLE")]):
        reads[KEY] = read(checks=checks, mergeable=mergeable, reviewDecision="APPROVED")
        watch.run_pass(watching, T0 + timedelta(minutes=minute))
        started.append(len(landers))
    assert started == [0, 0, 1, 1]
    with state.edit(demo) as data:
        data["prs"][KEY]["lander"]["outcome"] = "not landed: stack-enqueue --watch exited 1: checks failed"
    watch.run_pass(watching, T0 + timedelta(minutes=5))
    assert len(landers) == 1
    reads[KEY] = read(headRefOid="ccccccc333", checks=GREEN, reviewDecision="APPROVED")
    watch.run_pass(watching, T0 + timedelta(minutes=6))
    assert landers == [(KEY,), (KEY,)]
    assert state.load(demo)["prs"][KEY]["lander"]["head"] == "ccccccc333"


@pytest.mark.parametrize(
    ("approved", "card", "unit"),
    [((KEY, OTHER), None, (KEY, OTHER)), ((KEY,), None, (KEY,)), ((OTHER,), None, None), ((KEY, OTHER), "demo:j1-7-a", None)],
)
def test_a_lander_queues_each_pr_he_approved_with_those_below_it_never_one_above_and_nothing_while_his_card_holds_it(
    demo: str, landers: list[tuple[str, ...]], approved: tuple[str, ...], card: str | None, unit: tuple[str, ...] | None
) -> None:
    stacked(demo)
    approve(demo, *approved)
    cards = NO_CARDS
    if card:
        waits_on(demo, card)
        cards = NO_CARDS | {"questions": [question(card)]}
    assert land.start(demo, [KEY, OTHER], cards) == ([unit] if unit else [])
    assert landers == ([unit] if unit else [])
    assert land.start(demo, [KEY, OTHER], cards) == []


@pytest.mark.parametrize(
    ("asker", "holds", "held"),
    [("j1", "", False), ("j2", "", False), ("j2", "the dashboard", False), ("j1", "ship the watch", True), ("j2", "Ship  the WATCH, once its store is picked", True)],
)
def test_an_open_question_holds_a_stack_from_landing_only_while_its_holds_line_names_a_change_the_stacks_job_builds(
    demo: str, landers: list[tuple[str, ...]], asker: str, holds: str, held: bool
) -> None:
    stacked(demo)
    approve(demo, KEY, OTHER)
    waits = f"demo:{asker}-1-a"
    with state.edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j2")
        data["orders"]["o1"]["parts"]["the dashboard"] = state.new_part()
        data["jobs"]["j2"] = job(kind="design", parts=["the dashboard"], carries={}, state="done")
        data["jobs"][asker]["questions"].append(state.new_question(None, "Which OpenAI endpoint?", state.stamp(T0), holds=holds) | {"waits": waits})
    cards = NO_CARDS | {"questions": [question(waits)]}
    assert land.open_cards(state.load(demo), "j1", [], cards) == ([waits] if held else [])
    assert land.start(demo, [KEY, OTHER], cards) == ([] if held else [(KEY, OTHER)])
    with state.edit(demo) as data:
        data["jobs"][asker]["questions"][-1]["closed"] = state.new_close("answered", "Team", None, seq="#9")
    assert land.start(demo, [KEY, OTHER], cards) == ([(KEY, OTHER)] if held else [])


def test_a_question_kept_before_holds_lines_holds_nothing_so_the_approved_pr_of_its_own_job_lands(demo: str, landers: list[tuple[str, ...]]) -> None:
    approve(demo, KEY)
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved"}
        data["jobs"]["j1"] |= {"waits": [KEY], "questions": [{"file": None, "title": "Which OpenAI endpoint?", "asked": state.stamp(T0), "unsettled": None, "waits": "demo:j1-1-a", "card": None}]}
    cards = NO_CARDS | {"questions": [question("demo:j1-1-a")]}
    assert state.load(demo)["jobs"]["j1"]["questions"][0]["holds"] == ""
    assert land.unready(state.load(demo), KEY, cards) is None
    assert land.start(demo, [KEY], cards) == [(KEY,)]


def test_a_lander_queues_a_stacks_ready_bottom_while_a_pr_above_it_waits_on_an_open_pr_and_that_pr_once_it_merges(demo: str, landers: list[tuple[str, ...]]) -> None:
    stacked(demo)
    approve(demo, KEY, OTHER)
    with state.edit(demo) as data:
        data["prs"][THIRD] = state.new_pr(state.pr_url(THIRD), "j1", "o1")
        data["jobs"]["j1"]["waits"] = [KEY, OTHER, THIRD]
        data["prs"][OTHER]["waits"] = [THIRD]
    assert land.start(demo, [KEY, OTHER], NO_CARDS) == [(KEY,)]
    with state.edit(demo) as data:
        data["prs"][THIRD]["state"] = "merged"
        data["prs"][KEY]["state"] = "merged"
    assert land.start(demo, [KEY, OTHER], NO_CARDS) == [(OTHER,)]
    assert landers == [(KEY,), (OTHER,)]


def test_a_lander_queues_a_stacks_green_approved_bottom_while_a_pr_above_it_is_red(demo: str, landers: list[tuple[str, ...]]) -> None:
    stacked(demo)
    approve(demo, KEY, OTHER)
    with state.edit(demo) as data:
        data["prs"][OTHER]["checks"] = "fail"
    assert land.start(demo, [KEY, OTHER], NO_CARDS) == [(KEY,)]
    assert landers == [(KEY,)]


def test_his_approve_under_a_pr_the_hold_label_holds_queues_it_with_the_prs_under_it_and_leaves_the_held_pr_open(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    reads = three_stacked(demo, monkeypatch)
    reads[THIRD] = stacked_read("ccccccc333", labels=[github.HOLD_LABEL])
    approve(demo, KEY, OTHER)
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    watch.run_pass(watching, T0 + timedelta(minutes=10))
    assert landers == [(KEY, OTHER)]
    assert state.load(demo)["prs"][THIRD]["lander"] is None


def test_his_approve_on_a_branch_of_a_stack_tree_beside_an_unapproved_one_queues_that_branch_with_its_root_as_its_tap_line_and_dry_run_say(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    fourth, fifth = "Forge-AI/monorepo#45", "Forge-AI/monorepo#46"
    shape = {KEY: ("root", "main"), OTHER: ("middle", "root"), THIRD: ("top", "middle"), fourth: ("left", "root"), fifth: ("right", "root")}
    with state.edit(demo) as data:
        for key, (branch, base) in shape.items():
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"head": "aaaaaaa111", "branch": branch, "base": base, "checks": "pass", "review": "approved"}
    approve(demo, KEY, THIRD, fourth, fifth)
    head = enqueue_exiting(tmp_path, demo, 0)
    monkeypatch.setattr(land.github, "below", lambda key: {THIRD: [KEY, OTHER], fourth: [KEY], fifth: [KEY]}.get(key, []))
    behind = stage.of(state.load(demo), THIRD, NO_CARDS)
    assert (behind.name, behind.why.startswith(f"{OTHER} below it in its stack, ")) == ("behind", True)
    assert land.run(demo, [THIRD], dry_run=True) == land.held_below([THIRD], [OTHER])
    assert stage.of(state.load(demo), fourth, NO_CARDS).name == "ready"
    assert land.run(demo, [fourth], dry_run=True).startswith(f"would queue {fourth} through stack-enqueue --watch from main at {head[:12]}")
    assert land.start(demo, list(shape), NO_CARDS) == [(KEY, fourth)]
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["prs"][fourth]["lander"]["outcome"] = land.QUEUED
    assert land.start(demo, list(shape), NO_CARDS) == [(fifth,)]
    assert landers == [(KEY, fourth), (fifth,)]


def test_a_native_stack_on_another_tracked_prs_branch_joins_its_landing_stack_and_lands_with_it_once_he_approves_each(
    demo: str, landers: list[tuple[str, ...]]
) -> None:
    with state.edit(demo) as data:
        for key, head in ((OTHER, "bbbbbbb222"), (THIRD, "ccccccc333")):
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"head": head, "checked": CHECKED | {"head": head}}
        for key, native, branch, base in ((KEY, [KEY], "chat-organize", "main"), (OTHER, [OTHER, THIRD], "chat-pins", "chat-organize"), (THIRD, [OTHER, THIRD], "chat-pins-2", "chat-pins")):
            data["prs"][key] |= {"native": native, "branch": branch, "base": base, "checks": "pass", "review": "approved"}
    assert land.landing_stacks(state.load(demo)) == dict.fromkeys(STACKED, STACKED)
    approve(demo, OTHER, THIRD)
    assert land.start(demo, list(STACKED), NO_CARDS) == []
    behind = stage.of(state.load(demo), THIRD, NO_CARDS)
    assert (behind.name, behind.why.startswith(f"{KEY} below it in its stack, ")) == ("behind", True)
    approve(demo, KEY)
    assert land.start(demo, list(STACKED), NO_CARDS) == [STACKED]
    assert landers == [STACKED]


def test_his_approve_above_a_pr_he_has_not_approved_reads_behind_it_owed_by_him_says_nothing_and_lands_once_he_approves_that_pr(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    three_stacked(demo, monkeypatch)
    approve(demo, OTHER, THIRD)
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    kept = state.load(demo)["prs"][OTHER]["stage"]
    assert (kept["name"], kept["owner"], kept["why"].startswith(f"{KEY} below it in its stack, card: green, waits on his Approve")) == ("behind", "him", True)
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=40)) if line.startswith(("STAGE ", "TAP "))]
    assert landers == []
    approve(demo, KEY)
    watch.run_pass(watching, T0 + timedelta(minutes=41))
    assert landers == [STACKED]


def test_his_approve_on_a_card_of_one_orders_prs_in_no_native_stack_starts_a_lander_on_each_since_stack_enqueue_refuses_them_named_together(
    demo: str, landers: list[tuple[str, ...]]
) -> None:
    stacked(demo, native=[])
    approve(demo, KEY, OTHER)
    assert land.start(demo, [KEY, OTHER], NO_CARDS) == [(KEY,), (OTHER,)]
    assert landers == [(KEY,), (OTHER,)]


def on_main(tmp_path: Path, name: str, script: str) -> str:
    """Commit script as stack-enqueue on main of an origin that the coordinator's checkout is a clone of, both made on the first call; the commit."""
    origin, clone = tmp_path / "origin", tmp_path / "clone"
    if not origin.exists():
        origin.mkdir()
        git_in(origin, "init", "-q", "-b", "main")
        (origin / land.ENQUEUE).parent.mkdir(parents=True)
        (origin / land.ENQUEUE).touch(mode=0o755)
    head = commit_files(origin, "stack-enqueue", {str(land.ENQUEUE): script})
    if not clone.exists():
        git_in(tmp_path, "clone", "-q", str(origin), str(clone))
        with state.edit(name) as data:
            data["roles"]["coordinator"]["cwd"] = str(clone)
    return head


def enqueue_exiting(tmp_path: Path, name: str, code: int) -> str:
    return on_main(tmp_path, name, f'#!/bin/sh\necho "trunk said $*"\nexit {code}\n')


@pytest.mark.parametrize("code", [0, 3])
def test_a_landers_outcome_is_said_on_its_prs_line_waking_no_one_and_a_failure_reads_unlanded_which_wakes_the_session_holding_its_order(
    demo: str, tmp_path: Path, code: int
) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    enqueue_exiting(tmp_path, demo, code)
    watching = watcher(demo)
    failed = f"not landed: stack-enqueue --watch exited {code}: trunk said --watch 42; its whole output is in {state.drive_dir(demo) / land.LOG}"
    change = failed if code else land.QUEUED
    assert land.run(demo, [KEY]) == change
    pr = state.load(demo)["prs"][KEY]
    assert pr["lander"]["outcome"] == change
    assert land.landing(pr) == (not code)
    lines = watch.event_lines(watching)
    assert lines == [f"PR {KEY}: {change} [o1 j1]"]
    assert (watch.wakes(lines[0], state.load(demo)), stage.of(state.load(demo), KEY, NO_CARDS).name) == (False, "unlanded" if code else "landing")


def test_a_lander_runs_stack_enqueue_as_the_default_branchs_head_holds_it_from_a_worktree_it_removes_never_from_the_coordinators_checkout(
    demo: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    approve(demo, KEY)
    enqueue_exiting(tmp_path, demo, 3)
    clone = tmp_path / "clone"
    (clone / land.ENQUEUE).write_text('#!/bin/sh\necho "the checkout said $*"\nexit 3\n')
    head = on_main(tmp_path, demo, '#!/bin/sh\necho "main said $*"\n')
    untouched = (git_in(clone, "rev-parse", "HEAD"), git_in(clone, "status", "--porcelain"))
    assert land.run(demo, [KEY]) == land.QUEUED
    said = capsys.readouterr().out
    checkout = state.drive_dir(demo) / land.LANDINGS / str(os.getpid())
    assert f"{KEY}: stack-enqueue from {checkout}, a fresh worktree of Forge-AI/monorepo's main at {head}" in said
    assert ("main said --check 42" in said, "main said --watch 42" in said, "the checkout said" in said) == (True, True, False)
    assert (git_in(clone, "rev-parse", "HEAD"), git_in(clone, "status", "--porcelain")) == untouched
    assert (len(git_in(clone, "worktree", "list").splitlines()), checkout.exists()) == (1, False)


def test_a_landing_whose_worktree_checkout_could_not_fetch_a_blob_is_said_once_and_tried_again_with_no_new_tap_until_a_good_worktree_queues_it(
    demo: str, tmp_path: Path, landers: list[tuple[str, ...]]
) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved"}
    enqueue_exiting(tmp_path, demo, 0)
    origin, blobless = tmp_path / "origin", tmp_path / "blobless"
    git_in(origin, "config", "uploadpack.allowFilter", "true")
    git_in(tmp_path, "clone", "-q", "--filter=blob:none", f"file://{origin}", str(blobless))
    with state.edit(demo) as data:
        data["roles"]["coordinator"]["cwd"] = str(blobless)
    commit_files(origin, "admin", {"admin.rs": "fn admin() {}\n"})
    blob = git_in(origin, "rev-parse", "HEAD:admin.rs")
    loose = origin / ".git" / "objects" / blob[:2] / blob[2:]
    withheld = loose.read_bytes()
    loose.unlink()
    watching = watcher(demo)
    failed = land.run(demo, [KEY])
    assert failed.startswith(f"{land.NO_WORKTREE} of Forge-AI/monorepo's default branch to run stack-enqueue from: ")
    assert f"could not fetch {blob} from promisor remote" in failed
    assert (len(git_in(blobless, "worktree", "list").splitlines()), (state.drive_dir(demo) / land.LANDINGS / str(os.getpid())).exists()) == (1, False)
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    assert land.run(demo, [KEY]) == failed
    loose.write_bytes(withheld)
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    assert land.run(demo, [KEY]) == land.QUEUED
    assert land.start(demo, [KEY], NO_CARDS) == []
    assert landers == [(KEY,), (KEY,)]
    lines = watch.event_lines(watching)
    assert lines == [f"PR {KEY}: {failed} [o1 j1]", f"PR {KEY}: {land.QUEUED} [o1 j1]"]
    assert [watch.wakes(line, state.load(demo)) for line in lines] == [False, False]


def test_a_lander_that_outlived_an_install_reopens_on_the_newest_drive_which_records_its_outcome_and_logs_it_under_its_pr(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    enqueue_exiting(tmp_path, demo, 0)
    watching = watcher(demo)
    newer = tmp_path / "cache" / "9.9.9" / "bin" / "drive"
    queued = land.enqueued

    def queued_then_installed(name: str, clone: Path, keys: list[str], dry_run: bool = False) -> str:
        outcome = queued(name, clone, keys, dry_run)
        state.store(demo, state.stored(demo) | {"saved_by": "9.9.9"})
        return outcome

    class Execed(Exception):
        pass

    def execv(drive: Path, argv: list[str]) -> None:
        raise Execed(drive, argv)

    monkeypatch.setattr(land, "enqueued", queued_then_installed)
    monkeypatch.setattr(install, "upgraded", lambda: newer)
    monkeypatch.setattr(install.os, "execv", execv)
    with pytest.raises(Execed) as execed:
        land.run(demo, [KEY])
    assert execed.value.args == (newer, [str(newer), "pr", "land", state.pr_url(KEY), "--drive", demo, "--outcome", land.QUEUED])
    assert state.stored(demo)["prs"][KEY]["lander"] is None
    state.store(demo, state.stored(demo) | {"saved_by": state.VERSION})
    monkeypatch.setattr(install, "upgraded", lambda: None)
    assert cli.main(["pr", "land", state.pr_url(KEY), "--drive", demo, "--outcome", land.QUEUED]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == f"{KEY}: {land.QUEUED}"
    assert state.load(demo)["prs"][KEY]["lander"]["outcome"] == land.QUEUED
    assert watch.event_lines(watching) == [f"PR {KEY}: {land.QUEUED} [o1 j1]"]


def test_an_approval_on_an_older_head_reads_approved_on_an_older_head_yet_his_approve_starts_its_lander(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    waiting_job(demo)
    with state.edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
    monkeypatch.setattr(watch.view, "cards", lambda drive: NO_CARDS)
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks=GREEN, reviewDecision="APPROVED", approvals=["bbbbbbb222", "aaaaaaa111"])})
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == [f"PR {KEY}: new head bbbbbbb [o1 j1]", f"PR {KEY}: checks passed [o1 j1]"]
    pr = state.load(demo)["prs"][KEY]
    assert (pr["review"], watch.view.tap_detail(pr), landers) == ("stale", "green, approved on an older head; uncarded, owed by the watch: his card on it is not drawn yet", [])
    approve(demo, KEY)
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    assert landers == [(KEY,)]


def enqueue_checking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, check: str) -> Path:
    """A stack-enqueue whose --check prints check and exits 1, and whose --watch queues, beside a gh that records each call; the file gh writes."""
    on_main(tmp_path, name, f'#!/bin/sh\nif [ "$1" = --check ]; then printf "%s\\n" {shlex.quote(check)}; exit 1; fi\necho "trunk said $*"\n')
    calls = tmp_path / "gh-calls"
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(f'#!/bin/sh\necho "$*" >> {calls}\necho https://github.com/Forge-AI/monorepo/pull/42#issuecomment-1\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{gh.parent}{os.pathsep}{os.environ['PATH']}")
    return calls


def test_a_lander_whose_only_gap_is_an_approval_on_an_older_head_asks_forge_pr_reviewer_once_a_head_and_watches(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approve(demo, KEY)
    stale = "awaiting approval on head from {} (approved 316c682c69, on an older head)"
    calls = enqueue_checking(tmp_path, monkeypatch, demo, f"#42 BLOCKED 34087d1264 {stale.format('forge-pr-reviewer[bot]')}; {stale.format('poetic-svc')}")
    assert land.run(demo, [KEY]) == land.QUEUED
    assert land.run(demo, [KEY]) == land.QUEUED
    assert calls.read_text() == "pr comment 42 -R Forge-AI/monorepo --body @forge-pr-reviewer review\n"
    assert state.load(demo)["prs"][KEY]["review_asked"] == "34087d1264"


def test_a_lander_whose_only_gaps_are_waits_to_retarget_once_the_pr_under_them_lands_asks_no_review_and_watches(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approve(demo, KEY)
    waits = [
        "#42 BLOCKED 34087d1264 sits in a native stack on chat-organize and retargets onto dev once #41 lands",
        "#43 BLOCKED 9a1c55e2b0 targets chat-pins without a native GitHub stack and retargets onto dev once #41 lands",
    ]
    calls = enqueue_checking(tmp_path, monkeypatch, demo, "\n".join(waits))
    assert land.run(demo, [KEY]) == land.QUEUED
    assert not calls.exists()


def test_a_lander_stops_on_any_other_gap_with_stack_enqueues_blocked_line_and_never_watches(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    approve(demo, KEY)
    blocked = "#42 BLOCKED 34087d1264 awaiting approval on head from poetic-svc (approved 316c682c69, on an older head); required check test is FAILURE"
    calls = enqueue_checking(tmp_path, monkeypatch, demo, blocked)
    assert land.run(demo, [KEY]) == f"not landed: {blocked}; its whole output is in {state.drive_dir(demo) / land.LOG}"
    assert not calls.exists()


def test_a_lander_queues_nothing_while_a_pr_below_it_in_its_native_stack_lacks_his_approve_and_tries_again_once_he_approves_it(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    stacked(demo)
    with state.edit(demo) as data:
        for key in (KEY, OTHER):
            data["prs"][key]["stack"] = [key]
    approve(demo, OTHER)
    head = enqueue_exiting(tmp_path, demo, 0)
    monkeypatch.setattr(land.github, "below", lambda key: ["Forge-AI/monorepo#41", KEY] if key == OTHER else [])
    held = (
        f"not landed: Forge-AI/monorepo#41, {KEY} sits below {OTHER} in its stack with neither his Approve nor an exempt class, and stack-enqueue queues a stack"
        " from its bottom, so landing a PR above would land it too; it lands once he approves each of them or they merge"
    )
    assert land.run(demo, [OTHER], dry_run=True) == held
    assert state.load(demo)["prs"][OTHER]["lander"] is None
    assert land.run(demo, [OTHER]) == held
    assert state.load(demo)["prs"][OTHER]["lander"]["below"] == ["Forge-AI/monorepo#41", KEY]
    assert land.start(demo, [OTHER], NO_CARDS) == []
    with state.edit(demo) as data:
        data["prs"]["Forge-AI/monorepo#41"] = state.new_pr(state.pr_url("Forge-AI/monorepo#41"), "j1", "o1") | {"state": "merged"}
    approve(demo, KEY)
    assert land.start(demo, [OTHER], NO_CARDS) == [(KEY, OTHER)]
    assert landers == [(KEY, OTHER)]
    monkeypatch.setattr(land.github, "below", lambda key: ["Forge-AI/monorepo#41"] if key == OTHER else [])
    with state.edit(demo) as data:
        data["prs"]["Forge-AI/monorepo#41"]["state"] = "open"
    assert land.run(demo, [KEY, OTHER], dry_run=True).startswith("not landed: Forge-AI/monorepo#41 sits below")
    monkeypatch.setattr(land.github, "below", lambda key: [KEY] if key == OTHER else [])
    assert land.run(demo, [KEY, OTHER], dry_run=True) == (
        f"would queue {KEY}, {OTHER} through stack-enqueue --watch from main at {head[:12]}: every PR below it is merged, approved by him or exempt"
    )


@pytest.mark.parametrize("verdict", ["unchecked", "objected"])
def test_his_approve_lands_a_green_pr_whatever_the_check_against_his_words_says(demo: str, tmp_path: Path, landers: list[tuple[str, ...]], verdict: str) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    enqueue_exiting(tmp_path, demo, 0)
    with state.edit(demo) as data:
        pr = data["prs"][KEY]
        pr |= {"checks": "pass", "review": "approved", "checked": None}
        if verdict == "objected":
            pr["checker"] = {"head": pr["head"], "pid": 1, "at": state.stamp(T0), "outcome": f"{served.NOT_READY}: it drops the flag", "against": 'o1 "keep the flag": it drops the flag'}
    assert land.unready(state.load(demo), KEY, NO_CARDS) is None
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    assert land.run(demo, [KEY]) == land.QUEUED
    assert state.load(demo)["prs"][KEY]["tap"]["label"] == "Approve"


def test_a_lander_refuses_a_pr_the_drive_does_not_track_and_one_his_approve_does_not_cover(demo: str) -> None:
    with pytest.raises(state.DriveError, match="Forge-AI/monorepo#99: the drive tracks no such PR, so it lands none"):
        land.run(demo, ["Forge-AI/monorepo#99"])
    with pytest.raises(state.DriveError, match="the drive lands a yasyf/\\* PR, or a PR he approved or an exempt class lands, and no other"):
        land.run(demo, [KEY])


def test_a_lander_merges_a_pr_he_approved_in_another_repository_with_gh_pr_merge_at_the_head_its_stage_read_ready(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    docs = "Forge-AI/design-docs#5"
    tap = {"label": "Approve", "at": state.stamp(T0), "head": "ddddddd444", "source": "pressed the button"}
    with state.edit(demo) as data:
        data["prs"][docs] = state.new_pr(state.pr_url(docs), "j1", "o1") | {"head": "ddddddd444", "tap": tap}
    runs: list[list[str]] = []
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: runs.append(argv) or land.subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(github, "run", lambda cmd, **_: "eee555fff666\n" if cmd[2] == "view" else "README.md\n")
    monkeypatch.setattr(install, "touched", lambda repo, files: [])
    assert land.run(demo, [docs]) == "merged at eee555fff666; it changes no plugin installed on this Mac"
    assert runs == [["gh", "pr", "merge", "5", "-R", "Forge-AI/design-docs", "--squash", "--match-head-commit", "ddddddd444"]]


def test_an_exempt_monorepo_pr_is_queued_once_green_with_no_tap_unless_its_files_put_it_outside_its_class(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"exempt": "test-only", "checks": "pass", "review": "approved"}
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    enqueue_exiting(tmp_path, demo, 0)
    diff = "diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -1 +1 @@\n-a\n+b\n"
    outside = (
        f'not landed: {KEY} is no "test-only": it changes api/src/upload.ts, outside test and fixture code; it lands on his Approve'
        f" once drive pr set {state.pr_url(KEY)} --no-exempt --tap puts it on his card"
    )
    for path, outcome in (("api/src/upload.ts", outside), ("api/src/upload.test.ts", land.QUEUED)):
        monkeypatch.setattr(land.served, "change", lambda key, path=path: {"title": "Fix: retry uploads", "body": "", "head": "aaaaaaa111", "diff": diff.format(path).encode()})
        assert land.run(demo, [KEY]) == outcome


@pytest.mark.parametrize(("checks", "conflict"), [("pass", False), ("pending", False), ("fail", False), ("pass", True)])
def test_his_approve_wakes_no_coordinator_whatever_its_pr_reads_since_the_lander_lands_it_and_its_stage_wakes_whoever_owes_its_next_step(
    demo: str, monkeypatch: pytest.MonkeyPatch, checks: str, conflict: bool
) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"checks": checks, "conflict": conflict}
    sent = posts(monkeypatch)
    line = f"TAP Approve {KEY} [o1 j1]"
    watch.sent(watcher(demo), "root", [line], T0)
    assert posted(sent) == [("evidence", f"root@{demo}", line)]


def test_a_pr_trunk_ejects_from_the_merge_queue_is_said_once_and_reads_unlanded_waking_the_session_holding_its_order(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"head": "aaaaaaa111", "card": CARD, "lander": {"head": "aaaaaaa111", "pid": 1, "at": state.stamp(T0), "outcome": land.QUEUED, "below": []}}
    serve(monkeypatch, {KEY: read(labels=[github.EJECTED_LABEL])})
    watching = watcher(demo)
    unlanded = f"STAGE {KEY} unlanded since {state.stamp(T0)}, owed by the session holding its order: Trunk ejected it from the merge queue [o1 j1]"
    lines = watch.run_pass(watching, T0)
    assert lines == [f"PR {KEY}: ejected from the merge queue [o1 j1]", unlanded]
    assert (watch.wakes(lines[0], state.load(demo)), watch.wakes(unlanded, state.load(demo))) == (False, True)
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []


def test_a_yasyf_lander_merges_with_admin_records_its_merge_commit_and_installs_each_plugin_its_merge_changes(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {"view": "ccc333ddd444eee\n", "api": "plugins/long-running/lib/drive/land.py\nREADME.md\n"}

    def gh(cmd: list[str], cwd: str | None = None, timeout: int = 120, max_age: str = "0s") -> str:
        return answers[cmd[2] if cmd[1] == "pr" else cmd[1]]

    runs: list[list[str]] = []
    monkeypatch.setattr(github, "run", gh)
    monkeypatch.setattr(install, "touched", lambda repo, files: ["long-running@skills"] if "plugins/long-running/lib/drive/land.py" in files else [])
    monkeypatch.setattr(
        land.subprocess, "run", lambda argv, **kwargs: runs.append(argv) or land.subprocess.CompletedProcess(argv, 0, "long-running@skills 1.0.4 installed\n", "")
    )
    with state.edit(demo) as data:
        data["prs"]["yasyf/cc-skills#7"] = state.new_pr("https://github.com/yasyf/cc-skills/pull/7", "j1", "o1")
    assert land.merged(demo, "yasyf/cc-skills#7", ["--admin"]) == "merged at ccc333ddd444; long-running@skills 1.0.4 installed"
    assert state.load(demo)["prs"]["yasyf/cc-skills#7"]["merged_sha"] == "ccc333ddd444eee"
    assert runs == [
        ["gh", "pr", "merge", "7", "-R", "yasyf/cc-skills", "--squash", "--admin"],
        [str(land.DRIVE), "install", "long-running@skills", "--sha", "ccc333ddd444eee", "--drive", "demo"],
    ]


@pytest.mark.parametrize(
    ("state_after", "change", "wakes"),
    [
        ("waiting", "merged at ccc333ddd444; long-running@skills 1.0.4 installed", True),
        ("done", "merged at ccc333ddd444; long-running@skills 1.0.4 installed", False),
        ("done", f"{install.PROVEN} on long-running@skills 1.0.4: /p.sh exits 0", False),
        ("failed", "merged at ccc333ddd444; long-running@skills 1.0.4 installed", False),
        ("failed", f"{install.PROOF_FAILED}: https://github.com/yasyf/cc-skills/pull/7: /p.sh exits 1", True),
        ("waiting", "merged", True),
        ("done", "merged", False),
        ("done", f"{install.MERGED_DONE}: {KEY} at ccc333ddd444", False),
    ],
)
def test_a_merge_its_proof_proved_wakes_no_one_and_a_failed_proof_wakes_the_holder(demo: str, state_after: str, change: str, wakes: bool) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"]["state"] = state_after
        data["prs"][KEY]["state"] = "merged"
    assert watch.wakes(f"PR {KEY}: {change} [o1 j1]", state.load(demo)) is wakes


def merged_ready(name: str, key: str, repro: str | None) -> None:
    with state.edit(name) as data:
        data["prs"][KEY]["state"] = "merged"
        data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"state": "merged", "merged_sha": "ccc333ddd444eee"}
        data["jobs"]["j1"].update(state="waiting", said=f"READY {state.pr_url(key)}", prs=[state.pr_url(key)], repro=repro, updated=state.stamp(T0 - timedelta(hours=1)))


def proof_file(tmp_path: Path) -> str:
    script = tmp_path / "proof.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    return str(script)


def test_a_merged_ready_job_keeping_a_proof_file_gets_one_proof_from_the_watch_and_no_orphan_or_wake_while_it_runs(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, YASYF, proof_file(tmp_path))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    assert provers == ["j1"]
    said = f"READY {state.pr_url(YASYF)}"
    assert state.load(demo)["jobs"]["j1"]["proof"] == {"pid": os.getpid(), "at": state.stamp(T0), "said": said, "version": state.VERSION}
    assert watch.run_pass(watching, T0 + timedelta(minutes=31)) == []
    assert provers == ["j1"]
    assert not watch.wakes(f"PR {YASYF}: merged at ccc333ddd444; long-running@skills 1.0.4 installed [o1 j1]", state.load(demo))


def test_a_merged_ready_job_waits_for_its_lander_before_its_proof_starts(demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, YASYF, proof_file(tmp_path))
    with state.edit(demo) as data:
        data["prs"][YASYF]["lander"] = {"head": "aaaaaaa111", "pid": os.getpid(), "at": state.stamp(T0), "outcome": None}
    assert (watch.run_pass(watcher(demo), T0), provers) == ([], [])


def test_a_merged_ready_tooling_job_keeping_no_proof_file_is_said_orphaned_once_and_proved_once_it_keeps_one(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, YASYF, None)
    watching = watcher(demo)
    why = (
        "j1 keeps no proof file to run: keep one with drive job set j1 --repro <file>, which the watch runs on the installed plugins,"
        " or start a fresh builder with a merge handoff that proves it from its INSTALL: and PROVE: lines"
    )
    assert watch.run_pass(watching, T0) == [f'ORPHAN o1 "ship the watch": j1\'s PRs all merged and nothing proves them live: {why}']
    assert watch.run_pass(watching, T0 + timedelta(minutes=31)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=62)) == []
    assert provers == []
    with state.edit(demo) as data:
        data["jobs"]["j1"]["repro"] = proof_file(tmp_path)
    assert watch.run_pass(watching, T0 + timedelta(minutes=63)) == []
    assert provers == ["j1"]


def test_a_merged_ready_product_job_takes_done_at_its_merge_with_no_proof_or_orphan(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path, ready_records: list[list[dict]]
) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, OTHER, proof_file(tmp_path))
    with state.edit(demo) as data:
        data["prs"][OTHER]["title"] = "Fix: the page"
    watching = watcher(demo)
    merged = f"{OTHER} at ccc333ddd444"
    closing = "its close report of o1 waits on the order for the watch, which posts it to the communicator on its next read and again each read until cci takes it"
    lines = watch.run_pass(watching, T0)
    assert f"PR {OTHER}: {install.MERGED_DONE}: {merged}; {closing} [o1 j1]" in lines
    assert not [line for line in lines if line.startswith(("FREE ", "ORPHAN "))]
    assert state.load(demo)["jobs"]["j1"]["said"] == f"DONE {state.pr_url(OTHER)} merged: {merged}"
    assert [record["text"] for records in ready_records for record in records if record.get("topic") == "merged-o1"] == [f"POST Fix: the page merged: {merged}"]
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=31)) if line.startswith(("FREE ", "ORPHAN "))]
    assert provers == []


def test_a_merged_ready_product_job_on_an_open_incidents_fix_waits_for_its_release_and_is_said_orphaned(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, OTHER, None)
    with state.edit(demo) as data:
        data["orders"]["o1"].update(incident=True, parts={"fix": state.new_part()})
        data["jobs"]["j1"]["parts"] = ["fix"]
    why = (
        f"{OTHER} merged outside yasyf/*, where a merge goes live only in its repository's release, which no drive install puts on this Mac:"
        " a fresh builder with a merge handoff proves it from its INSTALL: and PROVE: lines once that release is out"
    )
    assert f'ORPHAN o1 "fix": j1\'s PRs all merged and nothing proves them live: {why}' in watch.run_pass(watcher(demo), T0)
    assert (state.load(demo)["jobs"]["j1"]["state"], provers) == ("waiting", [])


@pytest.mark.parametrize(("version", "started"), [(state.VERSION, []), ("1.0.1", ["j1"])])
def test_a_proof_that_ended_with_no_result_is_said_with_its_rerun_and_started_again_by_a_newer_watch(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path, version: str, started: list[str]
) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, YASYF, proof_file(tmp_path))
    ended = subprocess.Popen(["true"])
    ended.wait()
    with state.edit(demo) as data:
        data["jobs"]["j1"]["proof"] = {"pid": ended.pid, "at": state.stamp(T0), "said": f"READY {state.pr_url(YASYF)}", "version": version}
    lines = watch.run_pass(watcher(demo), T0)
    assert provers == started
    rerun = f"j1's proof, drive job prove pid {ended.pid}, ended with no result, its output in the drive's {watch.PROOFS_LOG}: drive job prove j1 runs it again"
    assert lines == ([] if started else [f'ORPHAN o1 "ship the watch": {rerun}'])


def tried(name: str, outcome: str) -> None:
    with state.edit(name) as data:
        lander = {"head": "aaaaaaa111", "pid": os.getpid(), "at": state.stamp(T0), "outcome": outcome}
        data["prs"][YASYF] = state.new_pr(state.pr_url(YASYF), "j1", "o1") | {"head": "aaaaaaa111", "wants_tap": False, "lander": lander}


def test_a_yasyf_merge_whose_base_moved_merges_again_on_the_new_base_and_never_updates_its_branch(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    merges = iter([(1, "", BASE_MOVED), (1, "", BASE_MOVED), (0, "", "")])
    runs: list[list[str]] = []
    monkeypatch.setattr(github, "run", lambda cmd, **kwargs: "ccc333ddd444eee\n" if cmd[2] == "view" else "README.md\n")
    monkeypatch.setattr(install, "touched", lambda repo, files: [])
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: runs.append(argv) or land.subprocess.CompletedProcess(argv, *next(merges)))
    tried(demo, "")
    assert land.merged(demo, YASYF, ["--admin"]) == "merged at ccc333ddd444; it changes no plugin installed on this Mac"
    assert runs == [["gh", "pr", "merge", "7", "-R", "yasyf/cc-skills", "--squash", "--admin"]] * 3


def test_a_yasyf_merge_gh_refuses_for_any_other_reason_is_not_landed_and_reads_unlanded_for_the_session_holding_its_order(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[list[str]] = []
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: runs.append(argv) or land.subprocess.CompletedProcess(argv, 1, "", DIRTY + "\n"))
    outcome = land.merged(demo, YASYF, ["--admin"])
    assert outcome == f"not landed: gh pr merge --squash --admin refused it: {DIRTY}"
    assert runs == [["gh", "pr", "merge", "7", "-R", "yasyf/cc-skills", "--squash", "--admin"]]
    waiting_job(demo)
    tried(demo, outcome)
    assert not watch.wakes(f"PR {YASYF}: {outcome} [o1 j1]", state.load(demo))
    assert stage.of(state.load(demo), YASYF, NO_CARDS).name == "unlanded"


def test_a_yasyf_pr_whose_head_moved_past_its_landers_merges_at_the_next_read_free_of_conflicts_with_no_builder_registering_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    tried(demo, f"not landed: gh pr merge --squash --admin refused it: {DIRTY}")
    reads = {KEY: read(), YASYF: read()}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    started = []
    for minute, (head, mergeable) in enumerate([("aaaaaaa111", "MERGEABLE"), ("bbbbbbb222", "CONFLICTING"), ("bbbbbbb222", "MERGEABLE"), ("bbbbbbb222", "MERGEABLE")]):
        reads[YASYF] = read(headRefOid=head, mergeable=mergeable)
        watch.run_pass(watching, T0 + timedelta(minutes=minute))
        started.append(len(landers))
    assert started == [0, 0, 1, 1]
    assert landers == [(YASYF,)]
    lander = state.load(demo)["prs"][YASYF]["lander"]
    assert (lander["head"], lander["outcome"]) == ("bbbbbbb222", None)


def test_a_line_the_coordinator_did_not_get_goes_again_and_says_so_from_the_third_failure(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = posts(monkeypatch, [False, False, False, True])
    watching = watcher(demo)
    line = f"TAP Approve {KEY} [o1 j1]"
    said = [watch.sent(watching, "root", [line] if second == 0 else [], T0 + timedelta(seconds=second)) for second in (0, 5, 10, 15, 20)]
    assert said == [[], [], ["WATCH post to the coordinator failed 3 times in a row: cci post exited 1: no drive demo"], [], []]
    assert [argv[-1] for argv in sent] == [line]


def test_an_answer_leaving_a_card_unmarked_or_a_job_unsent_his_words_says_so_every_ten_minutes_until_each_is_marked(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    asking(monkeypatch, demo, "demo:j1-7-a")
    with state.edit(demo) as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "Pick demo:j1-7-a?", state.stamp(T0), card=question("demo:j1-7-a")["url"]) | {"waits": "demo:j1-7-a"})
        data["answers"]["a1"] = {"words": "one new tool", "link": f"{CARD}9", "at": state.stamp(T0), "said": state.stamp(T0), "cards": {"demo:j1-7-a": None}, "jobs": {}}
    watching = watcher(demo)

    def said(minutes: int) -> list[str]:
        return [line for line in watch.run_pass(watching, T0 + timedelta(minutes=minutes)) if line.startswith("ANSWER")]

    line = 'ANSWER a1 "one new tool": 1 card with no mark, standing, settled, superseded or reopened by it; drive answer a1 lists them'
    assert (said(5), said(10), said(15), said(20)) == ([], [line], [], [line])
    assert watch.wakes(line, state.load(demo))
    with state.edit(demo) as data:
        data["answers"]["a1"]["cards"]["demo:j1-7-a"] = {"mark": "stands"}
    assert said(40) == []
    with state.edit(demo) as data:
        data["answers"]["a1"]["jobs"]["j1"] = None
    assert said(50) == ['ANSWER a1 "one new tool": j1 not sent his words; drive answer a1 lists them']
    with state.edit(demo) as data:
        data["answers"]["a1"]["jobs"]["j1"] = {"mark": "sent"}
    assert said(70) == []


def test_an_incident_with_no_monitor_closes_with_no_datadog_read_once_a_release_ships_its_fix_and_another_order_on_that_release_stays_open(
    demo: str, monkeypatch: pytest.MonkeyPatch, ready_records: list[list[dict]]
) -> None:
    serve(monkeypatch, {})
    published = [release(2, "2026-11-09T15:00:00Z", "api")]
    monkeypatch.setattr(watch.github, "run", lambda cmd, **_: json.dumps(published) if cmd[2].endswith("/releases?per_page=100") else "ahead\n")
    calls = monitors_read(monkeypatch, {})
    wait = f"release:api:{KEY}"
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(text="ship the dashboard", jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", state="waiting", said="WAITING release of api", waits=[wait], updated=state.stamp(T0 - timedelta(minutes=10)))
        data["orders"]["o1"].update(incident=True, parts={"fix": state.new_part()})
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["jobs"]["j1"].update(parts=["fix"], state="waiting", said="WAITING release of api", waits=[wait], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo, T0 + timedelta(hours=1))
    shipped = f"release releases/2026-11-09/2 ships {KEY} on api"
    recovered = f"RECOVERED o1: {shipped}, so the communicator posts its Live line and closes o1"
    assert watch.run_pass(watching, T0) == [f"FREE j1 o1: {shipped}, nothing else holds it", f"FREE j2 o2: {shipped}, nothing else holds it", recovered]
    assert not watch.wakes(recovered, state.load(demo))
    assert [(record["text"], record["body"]) for records in ready_records for record in records] == [
        ("POST incident o1 is over: its fix is live; post the Live line in its thread", f"Incident: ship the watch\nFix live: {shipped}, {state.pr_url(KEY)}\n")
    ]
    data = state.load(demo)
    assert (view.incidents(data), data["orders"]["o1"]["proof"], data["orders"]["o2"]["recovered"]) == ([], shipped, None)
    assert (watch.run_pass(watching, T0 + timedelta(minutes=1)), [argv for argv in calls if argv[0] == "pup"]) == ([], [])


def test_a_release_out_as_a_sub_coordinator_is_bound_mid_read_closes_the_incident_once_from_the_watch_dropping_its_wait(
    demo: str, monkeypatch: pytest.MonkeyPatch, ready_records: list[list[dict]]
) -> None:
    serve(monkeypatch, {})
    published = [release(2, "2026-11-09T15:00:00Z", "api")]
    iris = watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0, project="Iris")
    nested: list[list[str]] = []

    def github_run(cmd: list[str], **_: object) -> str:
        if not nested:
            nested.append([])
            sub_coordinated(demo)
            nested[0] = watch.run_pass(iris, T0)
        return json.dumps(published) if cmd[2].endswith("/releases?per_page=100") else "ahead\n"

    monkeypatch.setattr(watch.github, "run", github_run)
    with state.edit(demo) as data:
        data["orders"]["o1"].update(incident=True, project="Iris", parts={"fix": state.new_part()})
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["jobs"]["j1"].update(parts=["fix"], state="waiting", said="WAITING release of api", waits=[f"release:api:{KEY}"], updated=state.stamp(T0 - timedelta(minutes=10)))
    shipped = f"release releases/2026-11-09/2 ships {KEY} on api"
    assert watch.run_pass(watcher(demo), T0) == []
    assert nested == [[f"FREE j1 o1: {shipped}, nothing else holds it", f"RECOVERED o1: {shipped}, so the communicator posts its Live line and closes o1"]]
    assert len(ready_records) == 1


def test_an_incident_whose_fix_waits_on_two_releases_closes_only_once_both_shipped(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    published = [release(2, "2026-11-09T15:00:00Z", "api")]
    monkeypatch.setattr(watch.github, "run", lambda cmd, **_: json.dumps(published) if cmd[2].endswith("/releases?per_page=100") else "ahead\n")
    with state.edit(demo) as data:
        data["orders"]["o1"].update(incident=True, parts={"fix": state.new_part()})
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        waits = [f"release:api:{KEY}", f"release:worker:{KEY}"]
        data["jobs"]["j1"].update(parts=["fix"], state="waiting", said="WAITING releases of api and worker", waits=waits, updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo, T0 + timedelta(hours=1))
    assert watch.run_pass(watching, T0) == []
    data = state.load(demo)
    assert (data["jobs"]["j1"]["waits"], data["orders"]["o1"]["fixed"]) == ([f"release:worker:{KEY}"], None)
    published.append(release(3, "2026-11-09T16:00:00Z", "worker"))
    shipped = f"release releases/2026-11-09/3 ships {KEY} on worker"
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == [
        f"FREE j1 o1: {shipped}, nothing else holds it",
        f"RECOVERED o1: {shipped}, so the communicator posts its Live line and closes o1",
    ]
    assert watch.run_pass(watching, T0 + timedelta(minutes=2)) == []


def test_an_incident_whose_fix_jobs_last_release_wait_the_drives_watch_drops_is_closed_once_by_the_watch_hearing_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, ready_records: list[list[dict]]
) -> None:
    serve(monkeypatch, {})
    published = [release(2, "2026-11-09T15:00:00Z", "worker")]
    monkeypatch.setattr(watch.github, "run", lambda cmd, **_: json.dumps(published) if cmd[2].endswith("/releases?per_page=100") else "ahead\n")
    sub_coordinated(demo)
    still = state.stamp(T0 - timedelta(minutes=10))
    with state.edit(demo) as data:
        data["orders"]["o1"].update(incident=True, jobs=["j1", "j2"], parts={"fix": state.new_part(), "ship the watch": state.new_part()})
        data["orders"]["o2"] = order(text="ship the dashboard", jobs=[])
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["jobs"]["j1"].update(parts=["fix"], state="waiting", carries={"o2": ["ship the dashboard"]}, waits=[f"release:api:{KEY}"], updated=still)
        data["jobs"]["j2"] = job(state="waiting", parts=["ship the watch"], waits=[f"release:worker:{KEY}", f"release:api:{KEY}"], updated=still)
    top, iris = watcher(demo), watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0, project="Iris")
    assert (watch.run_pass(top, T0), watch.run_pass(iris, T0)) == ([], [])
    published.append(release(3, "2026-11-09T16:00:00Z", "api"))
    api = f"release releases/2026-11-09/3 ships {KEY} on api"
    assert watch.run_pass(top, T0 + timedelta(minutes=1)) == [f"FREE j1 o1: {api}, nothing else holds it"]
    assert (state.load(demo)["orders"]["o1"]["fixed"], ready_records) == (api, [])
    assert watch.run_pass(iris, T0 + timedelta(minutes=1)) == [
        f"FREE j2 o1: {api}, nothing else holds it",
        f"RECOVERED o1: {api}, so the communicator posts its Live line and closes o1; its live jobs, open PRs and parts left moved to the plain order o3",
    ]
    assert (watch.run_pass(top, T0 + timedelta(minutes=2)), watch.run_pass(iris, T0 + timedelta(minutes=2)), len(ready_records)) == ([], [], 1)


def test_a_builders_done_on_an_incidents_fix_records_it_live_so_the_watch_closes_the_incident_and_a_done_on_its_stop_does_not(
    demo: str, monkeypatch: pytest.MonkeyPatch, ready_records: list[list[dict]]
) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["orders"]["o1"].update(incident=True, jobs=["j1", "j2"], parts={"fix": state.new_part(), "stop": state.new_part()})
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["jobs"]["j1"]["parts"] = ["stop"]
        data["jobs"]["j2"] = job(parts=["fix"])
    with state.edit(demo) as data:
        state.take_result(data, "j1", "DONE the rollback is live")
    watching = watcher(demo)
    assert ([line for line in watch.run_pass(watching, T0) if line.startswith("RECOVERED")], state.load(demo)["orders"]["o1"]["fixed"]) == ([], None)
    with state.edit(demo) as data:
        state.take_result(data, "j2", f"DONE {state.pr_url(KEY)} live: runs succeed on release 14903")
    fixed = f"j2 proved it live: {state.pr_url(KEY)} live: runs succeed on release 14903"
    assert [line for line in watch.run_pass(watching, T0 + timedelta(minutes=1)) if line.startswith("RECOVERED")] == [
        f"RECOVERED o1: {fixed}, so the communicator posts its Live line and closes o1"
    ]
    assert [record["body"] for records in ready_records for record in records] == [f"Incident: ship the watch\nFix live: {fixed}\n"]
    assert view.incidents(state.load(demo)) == []


MONITOR = "https://app.datadoghq.com/monitors/315028134"


def monitors_read(monkeypatch: pytest.MonkeyPatch, statuses: dict[str, str]) -> list[list[str]]:
    calls = []

    def run(argv: list[str], checkout: Path) -> str:
        calls.append(argv)
        return json.dumps([{"id": int(monitor), "status": status} for monitor, status in statuses.items()]) if argv[0] == "pup" else ""

    monkeypatch.setattr(watch.reads, "run", run)
    return calls


@pytest.mark.parametrize(
    ("status", "out", "part", "closes"),
    [("Alert", True, "fix", False), ("OK", False, "fix", False), ("OK", True, "stop", False), ("OK", True, "fix", True)],
)
def test_an_incident_closes_once_its_monitor_reads_ok_and_a_release_shipped_its_fix_moving_the_follow_ups_of_each_of_its_orders_to_a_plain_order(
    demo: str, monkeypatch: pytest.MonkeyPatch, ready_records: list[list[dict]], status: str, out: bool, part: str, closes: bool
) -> None:
    serve(monkeypatch, {OTHER: read()})
    published = [release(2, "2026-11-09T15:00:00Z", "api")] if out else []
    monkeypatch.setattr(watch.github, "run", lambda cmd, **_: json.dumps(published) if cmd[2].endswith("/releases?per_page=100") else "ahead\n")
    calls = monitors_read(monkeypatch, {"315028134": status})
    with state.edit(demo) as data:
        parts = {name: state.new_part() for name in ("fix", "stop", "cause", "follow-up")}
        data["orders"]["o1"].update(incident=True, thread=["D123", "1794200000.000100"], note="record 5f6b2b5", alerts=[MONITOR], jobs=["j1", "j2"], parts=parts)
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["jobs"]["j1"].update(kind="codex", parts=[part], state="waiting", waits=[f"release:api:{KEY}"], updated=state.stamp(T0 - timedelta(minutes=10)))
        data["jobs"]["j2"] = job(parts=["follow-up"], prs=[state.pr_url(OTHER)])
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o1") | {"head": "aaaaaaa111"}
        data["orders"]["o2"] = order(text="also the migration check should not stop a sand that can read", jobs=[], incident=True, joins="o1", parts={"the migration check": state.new_part()})
    watching = watcher(demo, T0 + timedelta(hours=1))
    lines = [line for line in watch.run_pass(watching, T0) if line.startswith("RECOVERED")]
    data = state.load(demo)
    searches = [argv for argv in calls if argv[0] == "pup"]
    assert [argv[argv.index("--query") + 1] for argv in searches] == ["id:(315028134)"] * (out and part == "fix")
    if not closes:
        assert (lines, ready_records, data["orders"]["o1"]["recovered"], data["jobs"]["j2"]["order"]) == ([], [], None, "o1")
        assert [entry["id"] for entry in view.incidents(data)] == ["o1", "o2"]
        return
    shipped = f"release releases/2026-11-09/2 ships {KEY} on api"
    assert lines == [f"RECOVERED o1: {MONITOR} read OK and {shipped}, so the communicator posts its Live line and closes o1, o2; its live jobs, open PRs and parts left moved to the plain order o3"]
    assert ready_records == [
        [
            {
                "kind": "report",
                "to": ["communicator@demo"],
                "topic": "watch-recovered-o1",
                "attr": {"close": "o1,o2", "thread": "1794200000.000100"},
                "text": "POST incident o1 is over: its monitors read OK and its fix is live; post the Live line in its thread",
                "body": f"Incident: ship the watch\nRecovered: {MONITOR} read OK\nFix live: {shipped}, {state.pr_url(KEY)}\nNote: record 5f6b2b5; follow-up o3\n",
            }
        ]
    ]
    incident, joined, follow = data["orders"]["o1"], data["orders"]["o2"], data["orders"]["o3"]
    assert (list(incident["parts"]), incident["jobs"], incident["fixed"], incident["recovered"]) == (["fix", "stop", "cause"], ["j1"], shipped, state.stamp(T0))
    assert (list(joined["parts"]), joined["recovered"]) == ([], state.stamp(T0))
    assert incident["proof"] == f"{MONITOR} read OK at {render.when(state.stamp(T0))}; {shipped}"
    assert (follow["incident"], follow["kind"], list(follow["parts"]), follow["jobs"], follow["thread"], follow["note"]) == (
        False,
        "build",
        ["follow-up", "the migration check"],
        ["j2"],
        ["D123", "1794200000.000100"],
        "follow-up of incident o1",
    )
    assert (data["jobs"]["j2"]["order"], data["prs"][OTHER]["order"]) == ("o3", "o3")
    assert view.incidents(data) == []
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=1)) if line.startswith("RECOVERED")]
    assert ([argv[0] for argv in calls], len(ready_records)) == (["pup"], 1)


CLOSE_REPORT = {"id": "install-proved-j1", "kind": "report", "to": ["communicator@demo"], "topic": "install-proved-o1", "attr": {"close": "o1"}, "text": "POST Fix: the watch is live in long-running 1.0.3", "body": "Fixed: Fix: the watch\n"}


def test_a_close_report_drive_install_kept_posts_from_the_watch_once_cci_takes_it_and_survives_a_refusal_to_the_next_watch(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from drive import tools

    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["orders"]["o1"]["close_report"] = CLOSE_REPORT
    posts: list[list[dict]] = []

    def refused(name: str, lane: str, records: list[dict]) -> list[str]:
        posts.append(records)
        raise state.DriveError("cci: --body-file: not a JSON value")

    monkeypatch.setattr(tools, "sent", refused)
    first = watcher(demo)
    for minute in range(3):
        said = staged(watch.close_report_lines, first, T0 + timedelta(minutes=minute))
    assert said == ["WATCH close report post to the communicator failed 3 times in a row: cci: --body-file: not a JSON value"]
    assert (posts, state.load(demo)["orders"]["o1"]["close_report"]) == ([[CLOSE_REPORT]] * 3, CLOSE_REPORT)
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: posts.append(records) or ["#9"])
    second = watcher(demo)
    assert staged(watch.close_report_lines, second, T0 + timedelta(minutes=3)) == []
    assert (posts[3:], state.load(demo)["orders"]["o1"]["close_report"]) == ([[CLOSE_REPORT]], None)
    assert staged(watch.close_report_lines, second, T0 + timedelta(minutes=4)) == []
    assert len(posts) == 4


@pytest.mark.parametrize("count", [1, 100])
def test_the_watch_reads_the_monitors_of_every_open_incident_whose_fix_is_live_in_one_search_a_pass(demo: str, monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    serve(monkeypatch, {})
    calls = monitors_read(monkeypatch, {})
    with state.edit(demo) as data:
        for number in range(count):
            data["orders"][f"o{number + 2}"] = order(
                text=f"alert {number}", jobs=[], incident=True, alerts=[f"https://app.datadoghq.com/monitors/{1000 + number}"], fixed=f"release releases/2026-11-09/{number} ships {KEY} on api"
            )
    watching = watcher(demo)
    for minute in range(2):
        watch.run_pass(watching, T0 + timedelta(minutes=minute))
    queries = [argv[argv.index("--query") + 1] for argv in calls if argv[0] == "pup"]
    assert queries == [f"id:({' OR '.join(str(1000 + number) for number in range(count))})"] * 2


def pr_card(*keys: str, url: str = CARD) -> dict:
    return {"url": url, "prs": [state.pr_url(key) for key in keys], "asked": state.stamp(T0), "card": {"title": "Fix: the watch"}}


@pytest.mark.parametrize(("ended", "said"), [("MERGED", "merged"), ("CLOSED", "closed")])
def test_his_open_pr_card_is_settled_in_place_once_every_pr_it_carries_merged_or_closed(
    demo: str, monkeypatch: pytest.MonkeyPatch, ended: str, said: str
) -> None:
    other = "Forge-AI/monorepo#43"
    with state.edit(demo) as data:
        data["prs"][other] = state.new_pr(state.pr_url(other), "j1", "o1") | {"head": "ddddddd444"}
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [], "closed": [], "pr_cards": [pr_card(KEY, other)]})
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
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [], "closed": [], "pr_cards": list(shown)})
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


def test_a_new_head_on_a_pr_reworked_to_his_words_redraws_nothing_until_its_builders_ready(demo: str, monkeypatch: pytest.MonkeyPatch, redraws: Redraws) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
        state.rework(data["prs"][KEY], "o2", "bind it as @parent")
    monkeypatch.setattr(watch.view, "cards", lambda drive: watch.view.NO_CARDS)
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks=GREEN, reviewDecision="APPROVED")})
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    watch.run_pass(watching, T0 + timedelta(minutes=2))
    assert (redraws.asked, state.load(demo)["prs"][KEY]["rework"]["by"]) == ([], "o2")


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
    assert watch.holder_line(line, state.load(demo)) == "meta"


def test_a_fix_proven_live_whose_bug_report_has_not_heard_it_is_fixed_is_said_once_ten_minutes_on(demo: str) -> None:
    with state.edit(demo) as data:
        data["orders"]["o1"] |= {"reporter": state.reporter(REPORTED), "proof": "https://buildkite.com/forge/release/builds/14185"}
    watching = watcher(demo)
    assert watch.tell_lines(watching, T0 + timedelta(minutes=9)) == []
    line = (
        f"TELL o1: its fix is proven live and nothing moved on it for 10 minutes, but {REPORTED}, the thread its bug was reported in,"
        " has not heard it is fixed: send its report with --attr close=o1, whose handler posts the fix-live note there and records it"
        " with drive order told o1 <its permalink>; done refuses until then"
    )
    assert watch.tell_lines(watching, T0 + timedelta(minutes=10)) == [line]
    assert watch.tell_lines(watching, T0 + timedelta(minutes=40)) == []
    assert watch.wakes(line, state.load(demo))
    tooling_order(demo, "running")
    assert watch.holder_line(line, state.load(demo)) == "meta"


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


def test_the_watch_drops_a_pr_someone_else_opened_that_no_order_of_his_names_once_his_card_on_it_is_withdrawn(
    demo: str, monkeypatch: pytest.MonkeyPatch, withdrawals: Withdrawals
) -> None:
    with state.edit(demo) as data:
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"head": "ddddddd444", "stack": [KEY, OTHER]}
        data["prs"][KEY]["stack"] = [KEY, OTHER]
        data["jobs"]["j1"].update(prs=[state.pr_url(KEY), state.pr_url(OTHER)], waits=[f"release:api:{OTHER}"])
    withdrawals.cards.append(CARD)
    serve(monkeypatch, {KEY: read(), OTHER: read(headRefOid="ddddddd444", author="rowan")})
    assert watch.run_pass(watcher(demo), T0) == [f"DROPPED {OTHER}: {state.foreign(OTHER, 'rowan', [])} [o1 j1]", f"CARD {CARD} withdrawn: {OTHER} is dropped"]
    assert withdrawals.asked == [(state.pr_url(OTHER), "Dropped from the drive: rowan opened it, and the drive tracks no one else's PR unless your order names it")]
    data = state.load(demo)
    assert (list(data["prs"]), data["prs"][KEY]["stack"], data["jobs"]["j1"]["prs"], data["jobs"]["j1"]["waits"]) == ([KEY], [KEY], [state.pr_url(KEY)], [])


@pytest.mark.parametrize("named", ["#43", state.pr_url(OTHER), OTHER])
def test_the_watch_keeps_a_pr_someone_else_opened_that_an_order_of_his_it_serves_names(
    demo: str, monkeypatch: pytest.MonkeyPatch, withdrawals: Withdrawals, named: str
) -> None:
    with state.edit(demo) as data:
        data["orders"]["o1"]["text"] = f"land {named} once it is green"
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"wants_tap": False}
    serve(monkeypatch, {KEY: read(), OTHER: read(headRefOid="ddddddd444", author="rowan")})
    assert watch.run_pass(watcher(demo), T0) == []
    assert (state.load(demo)["prs"][OTHER]["author"], withdrawals.asked) == ("rowan", [])


def test_a_pr_someone_else_opened_stays_refused_while_his_card_on_it_cannot_be_withdrawn_and_drops_on_a_later_read(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(url: str, why: str) -> list[str]:
        raise slack.SlackError("cc-slack is down")

    monkeypatch.setattr(watch.slack, "withdraw_pr_card", down)
    serve(monkeypatch, {KEY: read(author="rowan")})
    watching = watcher(demo)
    for minute in range(watch.FAILURES_BEFORE_SAYING):
        watch.run_pass(watching, T0 + timedelta(minutes=minute))
    with state.edit(demo) as data:
        data["prs"][KEY]["tap"] = {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack"}
    assert state.foreign_pr(state.load(demo), KEY) == state.foreign(KEY, "rowan", [])
    with pytest.raises(state.DriveError, match="opened on GitHub by rowan"):
        land.run(demo, [KEY])
    monkeypatch.setattr(watch.slack, "withdraw_pr_card", lambda url, why: [])
    assert watch.run_pass(watching, T0 + timedelta(minutes=9)) == [f"DROPPED {KEY}: {state.foreign(KEY, 'rowan', [])} [o1 j1]"]
    assert KEY not in state.load(demo)["prs"]
