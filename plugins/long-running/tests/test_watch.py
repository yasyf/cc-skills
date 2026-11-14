import json
import os
import plistlib
import re
import shlex
import signal
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import DRIVE_TERMINAL, LEAD, SHORT_ASK, Answers, CodexAsk, drawn_card, Draws, Jev, Judge, Receiver, Redraws, Thread, WatchProcess, Withdrawals, codex_reply, commit_files, git_in, staged
from drive import agents, card, cli, codex, github, install, land, launchd, orca, place, reads, receiver, render, served, slack, stage, stall, state, superseded, talk, view, watch

KEY = "Forge-AI/monorepo#42"
OTHER = "Forge-AI/monorepo#43"
YASYF = "yasyf/cc-skills#7"
BASE_MOVED = "GraphQL: Base branch was modified. Review and try the merge again. (mergePullRequest)"
DIRTY = f"X Pull request {YASYF} is not mergeable: the merge commit cannot be cleanly created."
MERGE_CONFLICTS = "GraphQL: Pull Request has merge conflicts (mergePullRequest)"
POLICY = f"X Pull request {YASYF} is not mergeable: the base branch policy prohibits the merge."
T0 = state.parse_time("2026-11-09T10:00:00-05:00")
GATE = "LTX Behavioral Gate (short)"
RUNNING = [{"name": "test", "conclusion": None}]
GREEN = [{"name": "test", "conclusion": "SUCCESS"}]
RED = [{"name": "test", "conclusion": "FAILURE"}]
ASKED = "Should Iris, desktop and the sand CLI show sign-in when an api session expires?"
REPORTED = "https://acme.slack.com/archives/C0REPORTS01/p1794400100000200"
UNRECORDED = 'ORPHAN o1 "ship the watch": j1\'s agent finished and its result is not recorded: "'
REPORT_IT = "; only drive job report j1 - sets it, or names why it refuses, so wake it to pipe its return there"
WOKEN_BY_ITS_SESSION = ": SendMessage it this line verbatim from root@demo, the session session-under-test that started its agent a1"
CHECKED = {"head": "aaaaaaa111", "read": "aaaaaaa111", "orders": ["o1"], "lines": {}}
REAL_PASS = watch.run_pass
BUILD = "https://buildkite.com/forge/monorepo/builds/7"
REQUIRED = {"context": "buildkite/test", "targetUrl": BUILD}
INCIDENT_THREAD = "1794494004.895129"
TEAMMATE_THREAD = "1794500000.000300"
NO_BUILDER = "no builder carries this work, only "
ANSWERED = f"answered by the post {REPORTED}, but it counts as work he ordered, which no post delivers, held by "
READ = ("reader", "done")
REPLAYED_685794 = [
    (685713, None, "Incident dd-334827919: API Envoy idle-socket 503s on agent asks", True, [READ], False, None),
    (685714, None, "status of every bug", True, [READ], True, None),
    (685715, None, "status of shipping the automatic split of large tables", True, [READ], True, None),
    (685716, None, "What's status of splitting agents into their own runners", True, [READ], False, None),
    (685717, None, "status of the File redesign", True, [READ], True, None),
    (685718, None, "Why closed?", True, [READ, ("builder", "stopped")], False, "j3 stopped"),
    (685719, None, "why the files changes took all day", True, [], True, None),
    (685720, None, "And if they don't land for whatever reason still message the reviewer", True, [("reader", "waiting")], False, None),
    (685721, None, "What is a restore, and why did it take 5m?", True, [READ], False, None),
    (685722, None, "measure today's release against 5 to 7 minutes and name every step past it", True, [READ], True, None),
    (685723, None, "why a release like the last hotfix is not self-serve, and what makes it so", False, [("reader", "waiting")], True, None),
    (685724, None, "Why so we have a lazy setting at all it should be the same everywhere", True, [("design", "done"), ("builder", "stopped")], False, "j3 stopped"),
    (685725, None, "What? I'm saying do the automated version we built", True, [], False, None),
    (685726, None, "delete the repairs stub outright", True, [("builder", "waiting")], True, "j2 waits on"),
    (685727, None, "whats status of pre-signed bucket file upload stuff?", True, [READ], False, None),
    (685728, None, "for “Which lock-free rule replaces the two locks #53487 kept?”, can this be built", True, [("design", "waiting")], False, "j2 waits on"),
    (685729, None, "for “One row per woken cloud agent is its hold and its placement.”, can we remove", True, [READ], False, None),
    (685730, None, "is a table cap under 2 GB ok", True, [], True, None),
    (685731, None, "why the 21 GB database is so big", True, [READ], True, None),
    (685732, None, "so lok into those ssql DBs they are talking about", True, [READ], False, None),
    (685733, None, "where retiring a field failed in a teammate's session", True, [("reader", "stopped")], True, None),
    (685734, None, "tell the reporter in his thread when it is all fixed", True, [], True, None),
    (685735, None, "bringing this to me with optins and diagnosis on why it OOMed", True, [READ], True, None),
    (685736, None, "what teh fuyck are these solutins, its a server-side s3 copy right?", True, [READ], False, None),
    (685737, None, "whats blocking blob service now?", True, [READ], False, None),
    (685738, "build", "auto partitioning on the same abstraction", True, [READ, ("design", "done")], True, f"{ANSWERED}reader j2 done, design j3 done: "),
    (685739, "build", "auto partitioning status", True, [("design", "done")], True, f"{ANSWERED}design j2 done: "),
    (685740, None, "bring me design options to make forks and restores as close to free as possible", True, [READ, ("design", "waiting"), ("builder", "waiting")], False, "j4 waits on"),
    (685741, "build", "increase the search worker's RAM", True, [], True, "no job"),
    (685742, "build", "answer: did we cap each indexed entry to 1024 tokens as the plan said", True, [READ, READ], True, f"{ANSWERED}reader j2 done, reader j3 done: "),
    (685743, "build", "look into S3's per-prefix request rate limiting", True, [READ], True, f"{ANSWERED}reader j2 done: "),
    (685744, None, "#54749 lands", True, [READ], True, None),
    (685745, None, "his pick on #52894 and #53584", False, [("reader", "waiting")], True, None),
    (685746, "build", "are we doing the ssql release foe it", True, [], True, "no job"),
    (685747, "build", "the release's npm publishing of @poeticapps/poetic removed for now", True, [("builder", "failed"), READ], True, "j2 failed"),
    (685748, "build", "a card asking whether to tell the reporter it is live", True, [READ, READ], True, f"{ANSWERED}reader j2 done, reader j3 done: "),
    (685749, None, "why a chat's subagents do not persist across a restart, answered in his thread", True, [READ], True, None),
    (685750, None, "status of the fixes", True, [READ], True, None),
    (685751, "build", "first check", True, [("codex", "failed")], True, "j2 failed"),
    (685752, "build", "> `_app_action_invocations` tables in 15 minutes", True, [READ], True, f"{ANSWERED}reader j2 done: "),
    (685753, "build", "what happened to the lost wiki pages and whether they can be recovered", False, [READ], False, f"{NO_BUILDER}reader j2 done: "),
    (685754, "build", "options for holding failed writes and replaying them", False, [READ], False, f"{NO_BUILDER}reader j2 done: "),
    (685755, "build", "first check", True, [READ], True, f"{ANSWERED}reader j2 done: "),
    (685756, "build", "status of the scratch and run-local databases, and whether a customer's runs could", True, [READ], True, f"{ANSWERED}reader j2 done: "),
    (685757, "build", "how the prompts and the compiler push big reads to a scratch database", True, [READ], True, f"{ANSWERED}reader j2 done: "),
    (685758, "build", "whether production runs still fail on SandSQL now, and which runs the stall", True, [READ], True, f"{ANSWERED}reader j2 done: "),
    (685759, "build", "fix this too", False, [READ, ("reader", "waiting")], False, f"{NO_BUILDER}reader j2 done, reader j3 waiting: "),
    (685760, "build", "a release plans restate on plat-usw2-prod past the route check", False, [("reader", "waiting")], False, f"{NO_BUILDER}reader j2 waiting: "),
    (685761, "build", "list the rest for me here", True, [], True, "no job"),
]


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
    base = {"order": "o1", "kind": "builder", "goal": "watch", "prs": [], "branch": None, "waits": [], "questions": [], "state": "running", "said": None, "created": state.stamp(T0), "updated": state.stamp(T0)}
    return {**base, **overrides}


@pytest.fixture
def demo(state_root: Path) -> str:
    data = state.new_drive("demo", "coordinator", "session-under-test", "/work")
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
        "ejected_at": None,
        "native": [],
        "trunk": None,
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


def watcher(name: str) -> watch.Watcher:
    return watch.Watcher(name, offset=state.events_end(name))


@pytest.fixture(autouse=True)
def ready_records(monkeypatch: pytest.MonkeyPatch) -> list[list[dict]]:
    from drive import tools

    sent: list[list[dict]] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: sent.append(records) or ["9"])
    return sent


@pytest.fixture(autouse=True)
def closed_cards(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str, bool]]:
    closes: list[tuple[str, str, str, bool]] = []
    monkeypatch.setattr(superseded, "answer_card", lambda card, words, link, settled: closes.append((card, words, link, settled)) or {})
    return closes


REAL_ENSURE = watch.ensure
REAL_FETCH = github.fetch
REAL_RUN_PASS = watch.run_pass
REAL_SESSION_ENV = launchd.session_env


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
    assert not any(watch.wakes(line, state.load(demo), NO_CARDS) for line in lines)
    pr = state.load(demo)["prs"][KEY]
    assert (pr["head"], pr["checks"], pr["review"], pr["title"]) == ("bbbbbbb222", "pass", "approved", "Fix: the watch")
    assert (draws.keys, view.tap_card(pr, view.live_cards(demo)), pr["stage"]["name"]) == ([[KEY]] * wants_tap, draws.link if wants_tap else None, "uncarded" if wants_tap else "author")


def test_a_pr_that_starts_to_conflict_with_its_base_is_said_once_per_conflict_through_githubs_unknown_at_its_head(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    reads = {KEY: read(mergeable="CONFLICTING")}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    said = []
    for minute, mergeable in enumerate(["CONFLICTING", "CONFLICTING", "UNKNOWN", "CONFLICTING", "MERGEABLE", "CONFLICTING"]):
        reads[KEY] = read(mergeable=mergeable)
        said.append(watch.run_pass(watching, T0 + timedelta(minutes=2 * minute)))
    conflict = [f"PR {KEY}: conflicts with its base [o1 j1]"]
    assert said == [conflict, [], [], [], [], conflict]


REPLAYED_55220 = "Forge-AI/monorepo#55220"
STACKED_BASE = "durable-agents-j1389-send-user-question"
ON_TRUNK = "dev, the trunk of its native stack"
CONFLICTS_WITH_TRUNK = f"it conflicts with {ON_TRUNK}: restack the whole stack onto dev with ccx vcs stack rebase"


def test_replayed_55220_a_conflicting_pr_pushed_one_ahead_of_its_base_waits_on_his_approve_while_github_reads_unknown_and_turns_red_once_it_answers_conflicting(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    conflicting_at = T0 + timedelta(minutes=2)
    with state.edit(demo) as data:
        data["prs"][REPLAYED_55220] = state.new_pr(state.pr_url(REPLAYED_55220), "j1", "o1") | {
            "head": "5e95abf000",
            "base": STACKED_BASE,
            "trunk": "dev",
            "checks": "pass",
            "review": "approved",
            "conflict": True,
            "checked": CHECKED | {"head": "5e95abf000"},
            "stage": {"name": "red", "owner": stage.BUILDER, "why": CONFLICTS_WITH_TRUNK, "since": state.stamp(T0 - timedelta(hours=2, minutes=55)), "woke": state.stamp(T0 - timedelta(minutes=22))},
        }
    pushed = {"headRefOid": "c388588e73", "baseRefName": STACKED_BASE, "checks": GREEN, "reviewDecision": "APPROVED", "native": [(key, "open") for key in REPLAYED_STACK_55221], "trunk": "dev"}
    reads = {REPLAYED_55220: read(**pushed, mergeable="UNKNOWN")}
    monkeypatch.setattr(watch.view, "cards", lambda drive: NO_CARDS | {"pr_cards": [pr_card(REPLAYED_55220, head="c388588e73")]})
    serve(monkeypatch, reads)
    watching = watcher(demo)
    assert f"PR {REPLAYED_55220}: new head c388588 [o1 j1]" in watch.run_pass(watching, T0)
    pr = state.load(demo)["prs"][REPLAYED_55220]
    assert (pr["conflict"], pr["stage"]["name"], pr["stage"]["owner"], pr["stage"]["why"], woken) == (False, "card", stage.HIM, "green, waits on his Approve", [])
    reads[REPLAYED_55220] = read(**pushed, mergeable="CONFLICTING")
    assert f"PR {REPLAYED_55220}: conflicts with its base [o1 j1]" in watch.run_pass(watching, conflicting_at)
    pr = state.load(demo)["prs"][REPLAYED_55220]
    assert (pr["stage"]["name"], pr["stage"]["why"]) == ("red", CONFLICTS_WITH_TRUNK)
    assert woken == [(REPLAYED_55220, f"{REPLAYED_55220} red since {state.stamp(conflicting_at)}, owed by its builder: {CONFLICTS_WITH_TRUNK} [o1 j1]")]


REPLAYED_54026 = "Forge-AI/monorepo#54026"
REPLAYED_UNKNOWN = (REPLAYED_55220, "Forge-AI/monorepo#55285", "Forge-AI/monorepo#55522")
REPLAYED_STACK_55221 = (REPLAYED_54026, "Forge-AI/monorepo#54916", "Forge-AI/monorepo#54904", *REPLAYED_UNKNOWN)
REPLAYED_HOLDING_CARD = "demo:j850-684800"


def test_replayed_54026_every_pr_above_the_lowest_pr_of_its_stack_conflicting_with_its_base_is_behind_it_owed_by_its_owner_and_wakes_no_builder_above_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    waiting_job(demo)
    asking(monkeypatch, demo, REPLAYED_HOLDING_CARD, taps=[pr_card(*REPLAYED_STACK_55221)])
    with state.edit(demo) as data:
        for key in REPLAYED_STACK_55221:
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"wants_tap": True, "checked": CHECKED}
        data["prs"][REPLAYED_54026]["parts"] = ["the inbox wake"]
        data["jobs"]["j1"]["questions"] = [state.new_question(None, "Does the inbox wake it?", state.stamp(T0), holds="the inbox wake") | {"waits": REPLAYED_HOLDING_CARD}]
    native = [(key, "open") for key in REPLAYED_STACK_55221]
    branches = ["durable-agents-j850", "durable-agents-j1389-a", STACKED_BASE, "durable-agents-j2279-a", "durable-agents-j2279-b", "durable-agents-j2279-c"]
    stacked = {
        key: {"baseRefName": base, "headRefName": branch, "checks": GREEN, "reviewDecision": "APPROVED", "native": native, "trunk": "dev"}
        for key, base, branch in zip(REPLAYED_STACK_55221, ["dev", *branches[:-1]], branches, strict=True)
    }
    reads = {key: read(**stacked[key], mergeable="CONFLICTING" if key == REPLAYED_54026 else "UNKNOWN" if key in REPLAYED_UNKNOWN else "MERGEABLE") for key in REPLAYED_STACK_55221}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    behind_54026 = ("behind", stage.BUILDER, f"{REPLAYED_54026} below it in its stack conflicts with {ON_TRUNK}, red: {CONFLICTS_WITH_TRUNK}")
    for minutes, mergeable in ((0, "UNKNOWN"), (2, "CONFLICTING"), (32, "CONFLICTING"), (242, "CONFLICTING")):
        for key in REPLAYED_UNKNOWN:
            reads[key] = read(**stacked[key], mergeable=mergeable)
        watch.run_pass(watching, T0 + timedelta(minutes=minutes))
        prs = state.load(demo)["prs"]
        assert [(prs[key]["stage"]["name"], prs[key]["stage"]["owner"], prs[key]["stage"]["why"]) for key in REPLAYED_STACK_55221[1:]] == [behind_54026] * 5
    assert [key for key, _ in woken] == [REPLAYED_54026] * 3
    reads[REPLAYED_54026] = read(**stacked[REPLAYED_54026], mergeable="MERGEABLE")
    restacked_at = T0 + timedelta(minutes=243)
    watch.run_pass(watching, restacked_at)
    prs = state.load(demo)["prs"]
    assert [prs[key]["stage"]["name"] for key in REPLAYED_STACK_55221[:4]] == ["held", "card", "card", "red"]
    behind_55220 = ("behind", stage.BUILDER, f"{REPLAYED_55220} below it in its stack conflicts with {ON_TRUNK}, red: {CONFLICTS_WITH_TRUNK}")
    assert [(prs[key]["stage"]["name"], prs[key]["stage"]["owner"], prs[key]["stage"]["why"]) for key in REPLAYED_UNKNOWN[1:]] == [behind_55220] * 2
    assert woken[3:] == [(REPLAYED_55220, f"{REPLAYED_55220} red since {state.stamp(restacked_at)}, owed by its builder: {CONFLICTS_WITH_TRUNK} [o1 j1]")]


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
    assert (watch.wakes(ready, data, NO_CARDS), data["jobs"]["j1"]["state"], data["jobs"]["j1"]["said"]) == (True, "waiting", f"READY {state.pr_url(KEY)}")


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
    assert (watch.wakes(refused, data, NO_CARDS), data["jobs"]["j1"]["said"]) == (False, f"READY refused: {KEY}'s required buildkite/test failed at aaaaaaa ({BUILD})")
    assert [(key, text.split(", owed by")[0]) for key, text in woken] == [(KEY, f"{KEY} red since {state.stamp(T0 + timedelta(minutes=1))}")]


def test_a_ready_waits_on_the_pr_it_names_and_its_job_takes_done_once_that_product_pr_merges_with_no_free_or_close_report(
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
    assert not watch.wakes(f"PR {KEY}: merged [o1 j1]", data, NO_CARDS)
    assert (data["orders"]["o1"]["proof"], [record for records in ready_records for record in records if (record.get("attr") or {}).get("close") == "o1"]) == (None, [])
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
    assert cli.main(["pr", "add", state.pr_url(built), "--job", "j2", "--drive", demo, *LEAD]) == 0
    assert cli.main(["pr", "add", state.pr_url(built), "--job", "j2", "--drive", demo, *LEAD]) == 0
    jobs = state.load(demo)["jobs"]
    assert (jobs["j3"]["waits"], jobs["j3"]["said"], jobs["j1"]["waits"]) == ([built], f"waits on {built}", [])
    serve(monkeypatch, {built: read(state="MERGED", mergeCommit="ccc333")})
    said = watch.run_pass(watching, T0 + timedelta(seconds=61)) + watch.run_pass(watching, T0 + timedelta(seconds=122))
    assert [line for line in said if line.startswith("FREE ")] == [f"FREE j3 o1: {built} answered or done, nothing else holds it"]
    assert (state.load(demo)["jobs"]["j3"]["waits"], state.load(demo)["jobs"]["j3"]["said"]) == ([], None)


def test_a_wait_on_another_builders_pr_closed_unmerged_moves_to_that_builder_then_its_next_pr_while_its_own_pr_closing_frees_it(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed, rebuilt = "Forge-AI/monorepo#43", "Forge-AI/monorepo#44"
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["jobs"]["j2"] = job(goal="build the store", state="built", branch="blobstore-refcounts")
        data["jobs"]["j3"] = job(goal="build on the store")
    assert cli.main(["pr", "add", state.pr_url(closed), "--job", "j2", "--drive", demo, *LEAD]) == 0
    assert cli.main(["job", "set", "j3", "--waits", "j2", "--drive", demo]) == 0
    with state.edit(demo) as data:
        data["jobs"]["j2"]["waits"] = [closed]
    assert state.load(demo)["jobs"]["j3"]["waits"] == [closed]
    serve(monkeypatch, {closed: read(state="CLOSED")})
    watching = watcher(demo)
    said = watch.run_pass(watching, T0) + watch.run_pass(watching, T0 + timedelta(seconds=61))
    assert [line for line in said if line.startswith("FREE ")] == [f"FREE j2 o1: {closed} answered or done, nothing else holds it, built on blobstore-refcounts"]
    jobs = state.load(demo)["jobs"]
    assert (jobs["j2"]["waits"], jobs["j3"]["waits"], jobs["j3"]["said"]) == ([], ["j2"], "waits on j2")
    assert cli.main(["pr", "add", state.pr_url(rebuilt), "--job", "j2", "--drive", demo, *LEAD]) == 0
    assert (state.load(demo)["jobs"]["j3"]["waits"], state.load(demo)["jobs"]["j3"]["said"]) == ([rebuilt], f"waits on {rebuilt}")
    serve(monkeypatch, {rebuilt: read(state="MERGED", mergeCommit="ccc333")})
    said = watch.run_pass(watching, T0 + timedelta(seconds=122)) + watch.run_pass(watching, T0 + timedelta(seconds=183))
    assert [line for line in said if line.startswith("FREE ")] == [f"FREE j3 o1: {rebuilt} answered or done, nothing else holds it"]


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


def test_github_is_read_for_every_pr_at_the_watchs_start_then_only_for_the_prs_a_delivery_names(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    with state.edit(demo) as data:
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"head": "aaaaaaa111", "wants_tap": False}
    calls = serve(monkeypatch, {KEY: read(), OTHER: read()})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    watch.run_pass(watching, T0 + timedelta(seconds=60))
    state.append_event(demo, receiver.DELIVERY, repo="Forge-AI/monorepo", numbers=[43], event="check_run", guid="g1")
    assert watch.run_pass(watching, T0 + timedelta(seconds=65)) == []
    assert calls == [[KEY, OTHER], [OTHER]]


def test_a_delivery_of_a_failed_check_is_said_in_the_pass_it_wakes(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    reads = {KEY: read()}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    reads[KEY] = read(checks=RED)
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    state.append_event(demo, receiver.DELIVERY, repo="Forge-AI/monorepo", numbers=[42], event="check_run", guid="g1")
    assert watch.run_pass(watching, T0 + timedelta(seconds=10)) == [f"PR {KEY}: checks failed [o1 j1]"]


def test_a_builders_pushed_owes_a_read_of_each_pr_it_names_github_has_not_answered_for_since_the_take(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    calls = serve(monkeypatch, {KEY: read()})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"PUSHED {state.pr_url(KEY)}", updated=state.stamp(T0 + timedelta(seconds=2)))
    watch.run_pass(watching, T0 + timedelta(seconds=5))
    watch.run_pass(watching, T0 + timedelta(seconds=10))
    assert calls == [[KEY], [KEY]]


def test_the_watch_hooks_each_repository_of_a_pr_it_hears_and_reads_its_prs_since_github_delivered_nothing_of_them_before(
    demo: str, monkeypatch: pytest.MonkeyPatch, receiving: Receiver
) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    calls = serve(monkeypatch, {KEY: read()})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    receiving.unhooked.add("Forge-AI/monorepo")
    watch.run_pass(watching, T0 + timedelta(seconds=60))
    watch.run_pass(watching, T0 + timedelta(seconds=120))
    assert (receiving.hooked, calls) == (["Forge-AI/monorepo"], [[KEY], [KEY]])


def test_a_repository_this_account_does_not_administer_is_asked_once_with_no_timed_read_and_a_hook_that_fails_is_said_without_holding_back_the_next(
    demo: str, monkeypatch: pytest.MonkeyPatch, receiving: Receiver
) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    broken, upstream = "Forge-AI/litestream#3", "stablyai/orca#7"
    with state.edit(demo) as data:
        for key in (broken, upstream):
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"head": "aaaaaaa111", "wants_tap": False}
    calls = serve(monkeypatch, {key: read() for key in (KEY, broken, upstream)})
    refusal = "gh api 'repos/Forge-AI/litestream/hooks?per_page=100': gh: Not Found (HTTP 404)"
    receiving.unhooked |= {"Forge-AI/litestream", "Forge-AI/monorepo", "stablyai/orca"}
    receiving.unadministered.add("stablyai/orca")
    receiving.refused["Forge-AI/litestream"] = github.GithubError(refusal)
    watching = watcher(demo)
    said = [line for minutes in range(3) for line in watch.run_pass(watching, T0 + timedelta(minutes=minutes)) if line.startswith("WATCH")]
    assert (said, receiving.tried, receiving.hooked, calls) == (
        [f"WATCH upkeep of the receiver failed 3 times in a row: {refusal}"],
        ["Forge-AI/litestream", "Forge-AI/monorepo", "stablyai/orca", "Forge-AI/litestream", "Forge-AI/litestream"],
        ["Forge-AI/monorepo"],
        [[KEY, broken, upstream], [KEY]],
    )


def test_a_receiver_that_does_not_answer_is_said_from_its_third_minute_in_a_row(demo: str, monkeypatch: pytest.MonkeyPatch, receiving: Receiver) -> None:
    serve(monkeypatch, {})
    receiving.answering = False
    watching = watcher(demo)
    assert [watch.run_pass(watching, T0 + timedelta(minutes=minutes)) for minutes in range(3)] == [
        [],
        [],
        [f"WATCH upkeep of the receiver failed 3 times in a row: nothing answers on its port {receiver.PORT}; its log is {receiver.log_path()}"],
    ]


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
    monkeypatch.setattr(watch, "time", SimpleNamespace(sleep=stop))
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

    def post(argv: list[str], checkout: Path, env: dict | None = None, stdin: str | None = None) -> str:
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
        " from him, by steps 5 to 7 of drive manual migration, Into an Orca terminal, creating the terminal that shows tmux session"
        f" {demo}-coordinator, where cci session run resumes session-under-test in /work, and tell him it was restarted, never that it is down",
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


@pytest.mark.parametrize(
    ("line", "holder"),
    [
        ("FREE j1 o1: start its agent", "meta"),
        (f"FREE {KEY} j1 o1: its card was answered", "meta"),
        ('ORPHAN o1 "ship the watch": no live job', "meta"),
        ('DUE o1 "ship the watch" was due 9:00 AM: nothing moved', "meta"),
        ('DUE j1 o1 "watch" was due 9:00 AM and still runs', "meta"),
        ("RECOVERED o1: its fix is live", "meta"),
        ("TELL o1 his answer is waiting", "meta"),
        ("JOB j1 o1 READY https://github.com/yasyf/cc-skills/pull/7", "meta"),
        ("LANE j1 o1 died on Sprite s1: its worker exited", "meta"),
        ("STUCK j1 o1: j1 unmoved 61 minutes: j1 waiting on j2", "meta"),
        (f"PR {KEY}: merged [o1 j1]", "meta"),
        (f"TAP Approve {KEY}", None),
        ("EVENT relaunch job=j1", None),
        ("WATCH post to the coordinator failed 3 times in a row", None),
    ],
)
def test_every_line_whose_header_names_an_order_goes_to_the_session_holding_it_with_no_list_of_line_kinds(demo: str, line: str, holder: str | None) -> None:
    tooling_order(demo, "running")
    assert watch.holder_line(line, state.load(demo)) == holder


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


def due_owed(monkeypatch: pytest.MonkeyPatch) -> list[dict | str]:
    """Each report the watch writes owing a promised line, then the brief of each handler it starts, a report answering 901."""
    said: list[dict | str] = []
    monkeypatch.setattr(watch, "reported", lambda watcher, records: said.extend(records) or ["901"])
    monkeypatch.setattr(watch.agents, "handle", lambda name, brief: said.append(brief) or ("h1", False))
    return said


def owed(watching: watch.Watcher, moment: datetime, said: list[dict | str]) -> list[dict | str]:
    said.clear()
    assert watch.due_lines(watching, moment) == []
    return list(said)


def test_a_promised_time_owes_him_a_report_and_a_handler_every_ten_minutes_until_a_drive_post_is_in_its_thread_and_holds_the_order_open(
    demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: "Thread"
) -> None:
    serve(monkeypatch, {})
    said = due_owed(monkeypatch)
    due = state.stamp(T0 - timedelta(minutes=30))
    with state.edit(demo) as data:
        data["orders"]["o1"].update(thread=["D123", "1794200000.000100"], due=due, promised=state.stamp(T0 - timedelta(minutes=60)))
        data["orders"]["o3"] = order(state="done", due=state.stamp(T0 - timedelta(hours=1)))
    slack_thread.post(T0 - timedelta(minutes=90), from_claude=True)
    slack_thread.post(T0 - timedelta(minutes=61), from_user=True)
    watching = watcher(demo)
    [report, brief] = owed(watching, T0, said)
    assert (report["text"], report["topic"]) == (
        f"POST order o1's line promised by {render.when(due)} is 30 minutes late: post where it stands now in its thread, saying it is late",
        "watch-due-o1",
    )
    assert report["attr"] == {"order": "o1", "progress": "true", "quiet": "true", "due": due, "thread": "1794200000.000100"}
    assert f"Its line was promised him by {render.when(due)}, 30 minutes ago." in report["body"].splitlines()
    assert brief == f"Record: 901 Drive: {demo} Scratch: {state.drive_dir(demo) / 'scratch' / 'due-901'}"
    assert owed(watching, T0 + timedelta(minutes=5), said) == []
    assert owed(watching, T0 + timedelta(minutes=10), said)[0]["text"].startswith("POST order o1's line promised by ")
    monkeypatch.setattr(state, "now", lambda: T0 + timedelta(minutes=11))
    assert cli.main(["order", "set", "o1", "--state", "done", "--drive", demo]) == 2
    assert cli.main(["order", "set", "o1", "--due", state.stamp(T0 + timedelta(hours=2)), "--drive", demo]) == 0
    assert owed(watching, T0 + timedelta(minutes=20), said)[0]["text"].startswith("POST order o1's line promised by ")
    late_line = T0 + timedelta(minutes=21)
    slack_thread.post(late_line, from_claude=True)
    assert owed(watching, T0 + timedelta(minutes=30), said) == []
    moved = state.load(demo)["orders"]["o1"]
    assert (moved["due"], state.parse_time(moved["promised"]), moved["kept"]) == (state.stamp(T0 + timedelta(hours=2)), late_line, None)
    assert cli.main(["order", "set", "o1", "--state", "done", "--drive", demo]) == 2
    [report, _] = owed(watching, T0 + timedelta(hours=2), said)
    assert report["text"] == f"POST order o1 owes him the line it promised by {render.when(moved['due'])}: post where it stands now in its thread"
    slack_thread.post(T0 + timedelta(hours=2, minutes=1), from_claude=True)
    assert cli.main(["order", "set", "o1", "--state", "done", "--drive", demo]) == 0
    assert state.load(demo)["orders"]["o1"]["state"] == "done"
    assert owed(watching, T0 + timedelta(hours=3), said) == []
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
    said = due_owed(monkeypatch)
    card = f"{(T0 - timedelta(minutes=10)).timestamp():.6f}"
    threads: dict[str, list[dict]] = {"1794200000.000100": [], card: [{"ts": card, "from_claude": True}]}
    monkeypatch.setattr(slack, "thread", lambda *where: list(threads[where[3]]))
    with state.edit(demo) as data:
        data["orders"]["o1"].update(
            thread=["D123", "1794200000.000100"], due=state.stamp(T0 - timedelta(minutes=30)), promised=state.stamp(T0 - timedelta(minutes=60))
        )
        data["jobs"]["j1"]["questions"].append(state.new_question(None, None, state.stamp(T0), card=f"https://acme.slack.com/archives/D123/p{card.replace('.', '')}"))
        state.join_thread(data["orders"]["o1"], ("D123", card), state.stamp(T0), "question")
    watching = watcher(demo)
    assert owed(watching, T0, said)[0]["topic"] == "watch-due-o1"
    threads[card].append({"ts": f"{(T0 + timedelta(minutes=5)).timestamp():.6f}", "thread_ts": card, "from_claude": True})
    assert owed(watching, T0 + timedelta(minutes=10), said) == []
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
    said = due_owed(monkeypatch)
    card = "1794350000.000500"
    with state.edit(demo) as data:
        data["orders"]["o1"].update(
            {
                "from": source,
                "from_thread": from_thread,
                "thread": thread,
                "threads": [["D123", card, state.stamp(T0 - timedelta(minutes=90)), "question"]],
                "due": state.stamp(T0 - timedelta(minutes=30)),
                "promised": state.stamp(T0 - timedelta(minutes=60)),
            }
        )
    [report, _] = owed(watcher(demo), T0, said)
    assert report["topic"] == "watch-due-o1"
    since = str(int((T0 - timedelta(minutes=60)).timestamp()))
    assert slack_thread.reads == [("--channel", channel, "--thread", ts, "--since-ts", since, "--limit", "1000") for channel, ts in (where, ("D123", card))]


def test_a_due_order_from_his_dm_whose_own_thread_is_an_alerts_and_keeps_none_of_his_dm_says_its_time_once_reading_no_thread(
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
    assert line.startswith('DUE o1 "ship the watch" was due ') and line.endswith(watch.UNTHREADED)
    assert slack_thread.reads == []


def test_a_promised_time_passing_while_he_is_away_owes_one_report_and_a_thread_it_cannot_read_is_said_as_a_failed_read_owing_none(
    demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: "Thread"
) -> None:
    serve(monkeypatch, {})
    said = due_owed(monkeypatch)
    with state.edit(demo) as data:
        data["owner"] = {"heard": state.stamp(T0 - timedelta(hours=3)), "away": True}
        data["orders"]["o1"].update(thread=["D123", "1794200000.000100"], due=state.stamp(T0 - timedelta(minutes=5)), promised=state.stamp(T0 - timedelta(minutes=60)))
    watching = watcher(demo)
    [report, _] = owed(watching, T0, said)
    assert report["text"].startswith("POST order o1 owes him the line it promised by ")
    assert owed(watching, T0 + timedelta(minutes=30), said) == []
    with state.edit(demo) as data:
        data["orders"]["o1"]["due_said"] = None

    def unread(*where: str) -> list[dict]:
        raise slack.SlackError("cc-slack thread exited 1: ratelimited")

    monkeypatch.setattr(slack, "thread", unread)
    for minute in range(2):
        assert owed(watching, T0 + timedelta(minutes=40 + minute), said) == []
    [failing] = watch.due_lines(watching, T0 + timedelta(minutes=42))
    assert failing.startswith("WATCH read of o1's threads for the line promised him by ") and "ratelimited" in failing
    assert said == []


def test_moved_due_fires_again(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["orders"]["o1"]["due"] = state.stamp(T0 + timedelta(minutes=20))
        data["orders"]["o1"]["due_said"] = state.stamp(T0 - timedelta(minutes=5))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=21))[0].startswith("DUE o1")


def shown_pr(demo: str, key: str) -> str:
    """The PR's line as drive status shows it: its stored row, with his card and its stage on it."""
    prs = cli.with_cards(demo, state.load(demo))["prs"]
    return f"- {render.pr_line(key, prs[key], prs[key]['card_state'])}"


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
    assert (report["text"], report["topic"], report["to"]) == ("POST incident o1 has had no line to him in 5 minutes: post where it stands now in its thread", "watch-quiet-o1", [f"communicator@{demo}"])
    assert report["attr"] == {"order": "o1", "progress": "true", "quiet": "true", "thread": INCIDENT_THREAD}
    assert {f"- {render.job_line('j2', state.load(demo)['jobs']['j2'])}", "PRs, as the watch last read them from GitHub: 1", shown_pr(demo, KEY)} <= set(body.splitlines())
    assert "674295" not in body
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
    assert {key: state.load(demo)["orders"]["o1"]["quiet"][key] for key in ("record", "at", "handler")} == {"record": "901", "at": state.stamp(T0 + timedelta(minutes=7)), "handler": None}
    with state.edit(demo) as data:
        data["orders"]["o2"]["last_line"] = state.stamp(T0 + timedelta(minutes=9))
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=13, seconds=59)) == []
    with state.edit(demo) as data:
        data["orders"]["o1"]["recovered"] = state.stamp(T0 + timedelta(minutes=14))
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=30)) == []
    assert len(calls) == 1


def teammate(moment: datetime, **fields: object) -> dict:
    return {"ts": f"{moment.timestamp():.6f}", "thread_ts": TEAMMATE_THREAD, "user_name": "Dana", **fields}


def test_a_thread_his_open_order_watches_starts_a_handler_on_each_new_teammate_message_once_and_none_once_the_order_closes(
    demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    calls = catching_up(monkeypatch, demo)
    with state.edit(demo) as data:
        data["orders"]["o1"].update(state="open", watch=state.new_watch(f"slack:C0TEAM0001/{TEAMMATE_THREAD}", T0))
    slack_thread.messages += [
        teammate(T0 - timedelta(minutes=1), text="answered before the watch began"),
        teammate(T0 + timedelta(seconds=20), text="can we run it again tonight?"),
        teammate(T0 + timedelta(seconds=30), from_user=True, text="looking"),
        teammate(T0 + timedelta(seconds=40), from_claude=True, text="on it"),
    ]
    watching = watcher(demo)
    assert watch.watched_lines(watching, T0 + timedelta(minutes=1)) == []
    [(report, body), (start, _)] = calls
    assert (report["text"], report["topic"], report["to"]) == ("POST 1 new in the thread o1 watches: answer it there", "watch-o1", [f"communicator@{demo}"])
    assert report["attr"] == {"order": "o1", "progress": "true", "watch": "true", "thread": TEAMMATE_THREAD}
    assert body.splitlines()[-2:] == ["New there from anyone but him, the drive and a bot: 1", f"- {slack_thread.messages[1]['ts']} Dana: can we run it again tonight?"]
    assert start[-1] == f"Record: 901 Drive: {demo} Scratch: {state.drive_dir(demo) / 'scratch' / 'watch-901'}"
    assert slack_thread.reads == [("--channel", "C0TEAM0001", "--thread", TEAMMATE_THREAD, "--since-ts", f"{T0.timestamp():.6f}", "--limit", "1000")]
    assert (state.load(demo)["orders"]["o1"]["watch"]["seen"], state.load(demo)["orders"]["o1"]["watch"]["record"]) == (slack_thread.messages[3]["ts"], None)
    assert not [key for key in watch.part_gaps(watching, state.load(demo), T0 + timedelta(hours=1)) if key[0] == "o1"]
    assert watch.watched_lines(watching, T0 + timedelta(minutes=2)) == []
    assert len(calls) == 2
    slack_thread.messages.append(teammate(T0 + timedelta(minutes=2, seconds=10), text="and tomorrow?"))
    with state.edit(demo) as data:
        data["orders"]["o1"]["state"] = "done"
    assert watch.watched_lines(watching, T0 + timedelta(minutes=3)) == []
    assert (len(calls), len(slack_thread.reads)) == (2, 2)


def test_a_watched_thread_that_cannot_be_read_keeps_its_cursor_and_is_said_from_its_third_failure_in_a_row(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(demo) as data:
        data["orders"]["o1"].update(state="open", watch=state.new_watch(f"slack:C0TEAM0001/{TEAMMATE_THREAD}", T0))

    def unreadable(*where: str) -> list[dict]:
        raise slack.SlackError("channel_not_found")

    monkeypatch.setattr(slack, "thread", unreadable)
    watching = watcher(demo)
    said = [watch.watched_lines(watching, T0 + timedelta(minutes=minute)) for minute in (1, 2, 3)]
    assert said == [[], [], [f"WATCH answer of o1's watched thread C0TEAM0001/{TEAMMATE_THREAD} failed 3 times in a row: channel_not_found"]]
    assert state.load(demo)["orders"]["o1"]["watch"]["seen"] == f"{T0.timestamp():.6f}"


def test_a_quiet_incidents_report_names_its_merged_fix_from_the_row_github_refreshed_never_the_note_typed_before_the_merge(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fix, writer = "Forge-AI/monorepo#55152", "Forge-AI/monorepo#54949"
    quiet_incident(demo)
    with state.edit(demo) as data:
        data["orders"]["o1"].update(text=f"cloud agent sessions fail to write; the writer change is unmerged {writer}", note="its fix is #55152, green and approved, waiting on his tap")
        data["orders"]["o4"] = order(text="the writer change", jobs=["j4"])
        data["jobs"]["j1"]["prs"] = [state.pr_url(fix)]
        data["jobs"]["j4"] = job(order="o4")
        data["prs"] = {key: state.new_pr(state.pr_url(key), job_id, order_id) | {"state": "merged", "checks": "pass", "review": "approved"} for key, job_id, order_id in ((fix, "j1", "o1"), (writer, "j4", "o4"))}
    calls = catching_up(monkeypatch, demo)
    assert watch.quiet_lines(watcher(demo), T0 + timedelta(minutes=7)) == []
    [(_, body), _] = calls
    lines = body.splitlines()
    prs = state.load(demo)["prs"]
    assert lines[1:4] == ["PRs, as the watch last read them from GitHub: 2", *(f"- {render.pr_line(key, prs[key], None)}" for key in (fix, writer))]
    assert " merged, checks pass, review approved" in lines[2]
    assert [line for line in lines if "waiting on his tap" in line or "green and approved" in line] == []


@pytest.mark.parametrize("part", ["stop", "rollback", None])
def test_a_quiet_incident_whose_users_are_proven_unblocked_owes_no_more_lines_and_stays_open_until_its_fix_is_live(
    demo: str, monkeypatch: pytest.MonkeyPatch, run: Callable[..., tuple[int, str]], part: str | None
) -> None:
    quiet_incident(demo)
    calls = catching_up(monkeypatch, demo)
    watching = watcher(demo)
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=7)) == []
    assert len(calls) == 2
    build = "https://buildkite.com/forge/release/builds/15020"
    if part:
        with state.edit(demo) as data:
            data["orders"]["o1"]["jobs"].append("j3")
            data["jobs"]["j3"] = job(parts=[part])
        with state.edit(demo) as data:
            state.take_result(data, "j3", f"DONE {build}: production task creation succeeds")
    else:
        assert run("order", "set", "o2", "--proof", build)[0] == 0
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=30)) == []
    assert len(calls) == 2
    data = state.load(demo)
    assert [data["orders"][order_id]["proof"] for order_id in ("o1", "o2")] == ([f"{build}: production task creation succeeds", None] if part else [None, build])
    assert (state.open_incidents(data, ["o1", "o2"]), data["orders"]["o1"]["recovered"]) == (["o1", "o2"], None)


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
    lines = body.splitlines()
    assert lines[: lines.index("Incident o3, o3: checkout is down too") + 2] == [
        "Incident o1, o1, o2: ship the watch",
        "PRs, as the watch last read them from GitHub: 1",
        shown_pr(demo, KEY),
        "Incident o3, o3: checkout is down too",
        "PRs, as the watch last read them from GitHub: 0",
    ]
    assert f"- {render.job_line('j3', state.load(demo)['jobs']['j3'])}" in lines
    assert start[-1] == f"Record: 901 Drive: {demo} Scratch: {state.drive_dir(demo) / 'scratch' / 'quiet-901'}"
    orders = state.load(demo)["orders"]
    assert [{key: orders[head]["quiet"][key] for key in ("record", "at", "handler")} for head in ("o1", "o3")] == 2 * [{"record": "901", "at": state.stamp(T0 + timedelta(minutes=7)), "handler": orders["o1"]["quiet"]["handler"]}]
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=11, seconds=59)) == []
    assert len(calls) == 2


def test_a_quiet_incident_owes_a_line_while_a_job_works_then_one_when_it_returns_or_his_card_is_answered_and_none_while_every_job_waits(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    card = "https://acme.slack.com/archives/D123/p1794494100000100"
    quiet_incident(demo)
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", questions=[state.new_question(None, "Which way should the fix go?", state.stamp(T0), card=card)])
    calls = catching_up(monkeypatch, demo)
    watching = watcher(demo)

    def posted(moment: datetime) -> None:
        with state.edit(demo) as data:
            data["orders"]["o1"]["last_line"] = state.stamp(moment)
            data["handlers"].clear()
        assert watch.quiet_lines(watching, moment) == []

    def moved(count: int) -> list[str]:
        assert len(calls) == count
        return [line for line in calls[-2][1].splitlines() if line.startswith("Moved since")]

    assert watch.quiet_lines(watching, T0 + timedelta(minutes=7)) == []
    assert moved(2) == ["Moved since the thread's last line or the watch's last report on it: nothing. At work: j2."]
    posted(T0 + timedelta(minutes=8))
    with state.edit(demo) as data:
        data["jobs"]["j2"].update(state="done", said="DONE first check: the newest dev build fails the same way")
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=12, seconds=59)) == []
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=13)) == []
    assert moved(4) == ["Moved since the thread's last line or the watch's last report on it: j2. At work: no job."]
    posted(T0 + timedelta(minutes=14))
    assert watch.quiet_lines(watching, T0 + timedelta(hours=3)) == []
    assert len(calls) == 4
    with state.edit(demo) as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("tap", "the first option", card)
    assert watch.quiet_lines(watching, T0 + timedelta(hours=3, seconds=5)) == []
    assert moved(6) == ["Moved since the thread's last line or the watch's last report on it: j1. At work: no job."]


O836_FIX = "Forge-AI/monorepo#55152"
O836_FIRST_LINE = state.parse_time("2026-11-12T20:17:04-08:00")
O836_LAST_LINE = state.parse_time("2026-11-13T00:28:10-08:00")
O836_WAIT_GAINED = state.parse_time("2026-11-12T23:24:38-08:00")
O836_JOBS = (
    ("j2244", "codex", "done", "NOT READY: codex prepared the fix but would not open a PR (its rules: Codex edits, Claude ships). No PR URL."),
    ("j2245", "codex", "done", "REPLY_FILE: codex-r-270490969"),
    ("j2266", "builder", "waiting", f"waits on {O836_FIX}"),
    ("j2405", "codex", "failed", "BLOCKED ci release plan refuses: platform is neither a target nor a component in release/targets.yaml"),
    ("j2441", "codex", "done", "NEEDS #55152 is not live; API releases 15158 and 15166 are running."),
    ("j2566", "builder", "failed", "BLOCKED on the production cloud-agent create proof only: #55152 is already live in api on plat-usw2-prod"),
    ("j2616", "reader", "done", "DONE NOT-DRIVE-CAUSED: monitor 329874305 alerted 18:03:05 PST"),
    ("j2742", "reader", "failed", "BLOCKED no cloud agent create since the fix went live, so a succeeded create is not shown"),
    ("j2763", "codex", "done", "REPLY_FILE: codex-r-567028048"),
    ("j2776", "builder", "waiting", "How should the release's tenant smoke remove the cloud agent it creates, when archiveAgent keeps every row createAgent wrote?"),
)


def test_replaying_o836_from_its_first_line_after_his_card_owes_one_line_when_j2266_gains_its_release_wait_and_none_of_the_thirty_it_asked_with_nothing_changed(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    question = state.new_question(None, O836_JOBS[-1][-1], "2026-11-12T20:04:40-08:00", card="https://acme.slack.com/archives/D123/p1794542897981259")
    with state.edit(demo) as data:
        data["orders"]["o836"] = order(
            text="Release gilded-mahfouz applies api migration 0530_iris_sessions_cloud_agent_rows to production, whose CHECK refuses the iris_sessions row api writes for a cloud agent",
            incident=True,
            joins=None,
            thread=["D123", INCIDENT_THREAD],
            jobs=[job_id for job_id, *_ in O836_JOBS],
            created="2026-11-12T13:59:34-08:00",
            last_line=state.stamp(O836_FIRST_LINE),
            quiet={"record": "685584", "at": "2026-11-12T20:15:00-08:00", "handler": None},
        )
        for job_id, kind, now, said in O836_JOBS:
            data["jobs"][job_id] = job(order="o836", kind=kind, state=now, said=said, prs=[state.pr_url(O836_FIX)] if job_id == "j2266" else [], questions=[question] if job_id == "j2776" else [])
        data["prs"][O836_FIX] = state.new_pr(state.pr_url(O836_FIX), "j2266", "o836") | {"state": "merged", "checks": "pass", "review": "approved", "wants_tap": False}
    calls = catching_up(monkeypatch, demo)
    watching = watcher(demo)
    lines = []
    for minute in range(int((O836_LAST_LINE - O836_FIRST_LINE) / timedelta(minutes=1)) + 1):
        moment = O836_FIRST_LINE + timedelta(minutes=minute)
        with state.edit(demo) as data:
            data["jobs"]["j2266"]["waits"] = [f"release:api:{O836_FIX}"] if moment >= O836_WAIT_GAINED else []
        before = len(calls)
        assert watch.quiet_lines(watching, moment) == []
        if len(calls) > before:
            lines.append(moment)
            with state.edit(demo) as data:
                data["orders"]["o836"]["last_line"] = state.stamp(moment)
                data["handlers"].clear()
    assert lines == [state.parse_time("2026-11-12T23:25:04-08:00")]
    [(report, body), _] = calls
    assert report["topic"] == "watch-quiet-o836"
    assert "Moved since the thread's last line or the watch's last report on it: j2266. At work: no job." in body.splitlines()


def threaded(demo: str) -> None:
    with state.edit(demo) as data:
        data["orders"]["o1"]["thread"] = ["D123", INCIDENT_THREAD]


def test_an_order_with_a_thread_owes_a_line_there_after_each_step_none_while_its_jobs_run_none_within_five_minutes_of_its_last_line_none_for_a_stage_relabel_and_none_for_news_a_handler_got_and_never_posted(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    threaded(demo)
    calls = catching_up(monkeypatch, demo)
    watching = watcher(demo)
    hour = T0 + timedelta(hours=1)
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=1)) == []
    with state.edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(kind="reader")
    assert watch.quiet_lines(watching, hour) == []
    assert calls == []
    with state.edit(demo) as data:
        data["jobs"]["j2"].update(state="done", said="DONE the watch reads each PR once a pass")
    assert watch.quiet_lines(watching, hour + timedelta(seconds=5)) == []
    [(report, body), (start, _)] = calls
    assert (report["text"], report["attr"]) == ("POST order o1 has news since the thread's last line: post what changed there", {"order": "o1", "progress": "true", "quiet": "true", "thread": INCIDENT_THREAD})
    assert "Moved since the thread's last line or the watch's last report on it: j2. At work: j1." in body.splitlines()
    assert start[-1] == f"Record: 901 Drive: {demo} Scratch: {state.drive_dir(demo) / 'scratch' / 'quiet-901'}"
    assert watch.quiet_lines(watching, hour + timedelta(minutes=5, seconds=4)) == []
    assert len(calls) == 2
    with state.edit(demo) as data:
        data["handlers"].clear()
    assert watch.quiet_lines(watching, hour + timedelta(minutes=5, seconds=5)) == []
    assert watch.quiet_lines(watching, hour + timedelta(minutes=5, seconds=50)) == []
    assert len(calls) == 2
    posted = hour + timedelta(minutes=6)
    with state.edit(demo) as data:
        data["orders"]["o1"]["last_line"] = state.stamp(posted)
        data["handlers"].clear()
    assert watch.quiet_lines(watching, posted) == []
    with state.edit(demo) as data:
        data["prs"][KEY]["ejected"] = True
        stage.stamp(data["prs"][KEY], stage.Stage("unlanded", stage.ORDER, "Trunk ejected it from the merge queue"), posted)
    assert watch.quiet_lines(watching, posted + timedelta(seconds=5)) == []
    assert watch.quiet_lines(watching, posted + timedelta(minutes=4, seconds=59)) == []
    assert len(calls) == 2
    assert watch.quiet_lines(watching, posted + timedelta(minutes=5)) == []
    assert len(calls) == 4
    assert f"Moved since the thread's last line or the watch's last report on it: {KEY}. At work: j1." in calls[-2][1].splitlines()
    with state.edit(demo) as data:
        data["handlers"].clear()
        data["jobs"]["j1"].update(state="done", said="DONE the watch keeps what each report carried")
    assert watch.quiet_lines(watching, posted + timedelta(minutes=9, seconds=59)) == []
    assert len(calls) == 4
    assert watch.quiet_lines(watching, posted + timedelta(minutes=10)) == []
    assert len(calls) == 6
    assert "Moved since the thread's last line or the watch's last report on it: j1. At work: no job." in calls[-2][1].splitlines()
    relined = posted + timedelta(minutes=11)
    with state.edit(demo) as data:
        data["orders"]["o1"]["last_line"] = state.stamp(relined)
        data["handlers"].clear()
    assert watch.quiet_lines(watching, relined) == []
    with state.edit(demo) as data:
        stage.stamp(data["prs"][KEY], stage.Stage("behind", stage.BUILDER, "a PR below it in its stack is red"), relined)
    assert watch.quiet_lines(watching, relined + timedelta(minutes=30)) == []
    assert len(calls) == 6


def test_a_release_ending_one_of_two_waits_of_a_waiting_job_owes_its_thread_no_line_and_its_last_wait_ending_owes_one(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    threaded(demo)
    calls = catching_up(monkeypatch, demo)
    watching = watcher(demo)
    hour = T0 + timedelta(hours=1)
    with state.edit(demo) as data:
        state.rewait(data["jobs"]["j1"], [f"release:api:{KEY}", f"release:eks:{KEY}"])
        data["jobs"]["j1"].update(state="waiting", said=state.waits_line(data["jobs"]["j1"]["waits"]))
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=1)) == []
    with state.edit(demo) as data:
        state.rewait(data["jobs"]["j1"], [f"release:eks:{KEY}"])
    assert watch.quiet_lines(watching, hour) == []
    assert calls == []
    with state.edit(demo) as data:
        state.rewait(data["jobs"]["j1"], [])
    assert watch.quiet_lines(watching, hour + timedelta(seconds=5)) == []
    [(_, body), _] = calls
    assert "Moved since the thread's last line or the watch's last report on it: j1. At work: no job." in body.splitlines()


def test_a_job_only_carrying_an_order_and_its_pr_owe_that_orders_thread_no_line_and_a_job_of_its_own_returning_owes_one(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    threaded(demo)
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(text="ship the dashboard", jobs=["j2"])
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(order="o2", state="waiting", carries={"o1": ["ship the watch"]}, prs=[state.pr_url(OTHER)])
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o2") | {"head": "bbbbbbb222"}
    calls = catching_up(monkeypatch, demo)
    watching = watcher(demo)
    hour = T0 + timedelta(hours=1)
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=1)) == []
    with state.edit(demo) as data:
        data["jobs"]["j2"].update(state="done", said="DONE the dashboard ships")
        data["prs"][OTHER].update(state="merged", checks="pass")
    assert watch.quiet_lines(watching, hour) == []
    assert calls == []
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="done", said="DONE the watch reads each PR once a pass")
    assert watch.quiet_lines(watching, hour + timedelta(seconds=5)) == []
    [(_, body), _] = calls
    assert "Moved since the thread's last line or the watch's last report on it: j1. At work: no job." in body.splitlines()


@pytest.mark.parametrize("held", ["away", "close_report", "closed_on"])
def test_an_order_owes_its_thread_no_line_while_he_is_away_until_he_is_back_nor_once_a_report_closing_it_is_kept(demo: str, monkeypatch: pytest.MonkeyPatch, held: str) -> None:
    threaded(demo)
    calls = catching_up(monkeypatch, demo)
    watching = watcher(demo)
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=1)) == []
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="done", said="DONE the watch reads each PR once a pass")
        if held == "away":
            data["owner"] = {"heard": state.stamp(T0), "away": True}
        else:
            data["orders"]["o1"][held] = {"kind": "report", "attr": {"close": "o1"}, "text": "POST o1 is done", "body": ""} if held == "close_report" else "685000"
    assert watch.quiet_lines(watching, T0 + timedelta(hours=2)) == []
    assert calls == []
    with state.edit(demo) as data:
        data["owner"] = {"heard": state.stamp(T0 + timedelta(hours=2)), "away": False}
    assert watch.quiet_lines(watching, T0 + timedelta(hours=2, seconds=5)) == []
    assert len(calls) == (2 if held == "away" else 0)


@pytest.mark.parametrize("many", [1, 100])
def test_the_first_pass_over_any_number_of_threaded_orders_saves_the_drive_once_and_a_step_on_one_owes_only_its_thread(demo: str, monkeypatch: pytest.MonkeyPatch, many: int) -> None:
    with state.edit(demo) as data:
        for n in range(1, many + 1):
            data["orders"][f"o{n}"] = order(thread=["D123", f"1794494004.{n:06d}"], jobs=[f"j{n}"])
            data["jobs"][f"j{n}"] = job(order=f"o{n}", state="waiting")
    calls = catching_up(monkeypatch, demo)
    saves: list[str] = []
    save = state.save
    monkeypatch.setattr(state, "save", lambda name, data: saves.append(name) or save(name, data))
    watching = watcher(demo)
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=1)) == []
    assert (saves, calls) == ([demo], [])
    with state.edit(demo) as data:
        data["jobs"][f"j{many}"].update(state="done", said="DONE the watch reads each PR once a pass")
    assert watch.quiet_lines(watching, T0 + timedelta(minutes=5)) == []
    [(report, body), _] = calls
    assert report["attr"] == {"order": f"o{many}", "progress": "true", "quiet": "true", "thread": f"1794494004.{many:06d}"}
    assert f"Moved since the thread's last line or the watch's last report on it: j{many}. At work: no job." in body.splitlines()


BACK = T0 + timedelta(minutes=95)


def catching_up(monkeypatch: pytest.MonkeyPatch, demo: str) -> list[tuple[list[str] | dict, str | None]]:
    """Each command the drive runs and each record it posts with its body, a post answering its seq."""
    from drive import tools

    with state.edit(demo) as data:
        data["roles"]["communicator"] = {"session": "communicator-session", "terminal": None, "cwd": "/work", "started": state.stamp(T0)}
    calls: list[tuple[list[str] | dict, str | None]] = []

    def run(argv: list[str], checkout: Path, env: dict | None = None, stdin: str | None = None) -> str:
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


def returned_while_away(demo: str) -> None:
    threaded(demo)
    with state.edit(demo) as data:
        data["owner"] = {"heard": state.stamp(T0), "away": True}
    assert watch.quiet_lines(watcher(demo), T0 + timedelta(minutes=1)) == []
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="done", said="DONE the watch reads each PR once a pass")


def test_his_next_message_after_away_owes_his_catch_up_on_each_order_whose_news_moved_which_no_thread_line_repeats_and_the_watch_posts_through_a_handler_once_each_time_he_is_heard(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(thread=["D123", TEAMMATE_THREAD], jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", state="done", said="DONE it")
    returned_while_away(demo)
    calls = catching_up(monkeypatch, demo)
    watching = watcher(demo)
    assert heard_now(demo, capsys) == "heard"
    assert state.load(demo)["catch_up"] == state.new_catch_up(state.stamp(T0), ["o1"])
    assert watch.catch_up_lines(watching, BACK) == []
    assert watch.quiet_lines(watching, BACK + timedelta(minutes=6)) == []
    [(report, body), (start, _)] = calls
    assert report["text"] == f"POST his catch-up since {render.when(state.stamp(T0))}: 1 orders with news, 0 reports held for it"
    assert (report["to"], report["kind"], report["attr"]) == ([f"communicator@{demo}"], "report", {"catch_up": "true"})
    assert f"- {render.order_line(state.load(demo), 'o1', state.load(demo)['orders']['o1'])}" in body.splitlines()
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
    returned_while_away(demo)
    calls = catching_up(monkeypatch, demo)
    answered = reads.run

    def run(argv: list[str], checkout: Path, env: dict | None = None, stdin: str | None = None) -> str:
        if argv[0] == "claude" and len(calls) == 1:
            calls.append((argv, None))
            raise reads.ReadError("claude exited 1")
        return answered(argv, checkout, env)

    monkeypatch.setattr(reads, "run", run)
    watching = watcher(demo)
    assert heard_now(demo, capsys) == "heard"
    assert watch.catch_up_lines(watching, BACK) == []
    assert (state.load(demo)["catch_up"]["record"], state.load(demo)["handlers"]) == ("901", {})
    assert watch.catch_up_lines(watching, BACK) == []
    assert [call["kind"] if isinstance(call, dict) else call[0] for call, _ in calls] == ["report", "claude", "claude"]
    assert calls[2][0][-1].startswith("Record: 901 ")


def test_a_report_held_while_he_was_away_and_its_correction_go_on_his_catch_up(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cci_amendments: dict[str, dict[str, list[str]]]
) -> None:
    held_at = state.stamp(T0 + timedelta(minutes=10))
    with state.edit(demo) as data:
        data["owner"] = {"heard": state.stamp(T0), "away": True}
        data["orders"]["o2"] = order(jobs=["j2"], updated=state.stamp(T0 - timedelta(minutes=5)))
        data["jobs"]["j2"] = job(order="o2", updated=state.stamp(T0 - timedelta(minutes=5)))
        data["held"]["669950"] = {"order": "o2", "at": held_at, "told": "669957"}
    cci_amendments["correction"]["669950"] = ["669952"]
    calls = catching_up(monkeypatch, demo)
    assert cli.main(["status", "--drive", demo]) == 0
    assert f"- report 669950 (o2), held {render.when(held_at)}, its writer told in record 669957, corrected by record 669952" in capsys.readouterr().out.splitlines()
    assert heard_now(demo, capsys) == "heard"
    assert state.load(demo)["catch_up"]["orders"] == ["o2"]
    assert watch.catch_up_lines(watcher(demo), BACK) == []
    [(report, body), _] = calls
    assert report["text"].endswith(": 1 orders with news, 1 reports held for it")
    assert "- report 669950 on o2, corrected by record 669952" in body.splitlines()


def test_a_held_report_its_writer_withdrew_reads_withdrawn_in_drive_status_and_stays_out_of_his_catch_up(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cci_amendments: dict[str, dict[str, list[str]]]
) -> None:
    held_at = state.stamp(T0 + timedelta(minutes=10))
    with state.edit(demo) as data:
        data["owner"] = {"heard": state.stamp(T0), "away": True}
        data["orders"]["o2"] = order(jobs=["j2"], updated=state.stamp(T0 - timedelta(minutes=5)))
        data["jobs"]["j2"] = job(order="o2", updated=state.stamp(T0 - timedelta(minutes=5)))
        data["held"] |= {"669950": {"order": "o2", "at": held_at, "told": "669957"}, "669960": {"order": "o2", "at": held_at, "told": "669961"}}
    cci_amendments["withdraw"]["669950"] = ["669970"]
    calls = catching_up(monkeypatch, demo)
    assert cli.main(["status", "--drive", demo]) == 0
    assert (
        f"- report 669950 (o2), held {render.when(held_at)}, its writer told in record 669957, withdrawn by record 669970, so the catch-up leaves it out and closes no order on it"
        in capsys.readouterr().out.splitlines()
    )
    assert heard_now(demo, capsys) == "heard"
    assert watch.catch_up_lines(watcher(demo), BACK) == []
    [(report, body), _] = calls
    assert report["text"].endswith(": 1 orders with news, 1 reports held for it")
    assert [line for line in body.splitlines() if line.startswith("- report")] == ["- report 669960 on o2"]


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
    monkeypatch.setattr(watch, "passed", lambda watching, moment: [])
    run_watch(demo, monkeypatch)
    assert not state.pid_path(demo).exists()


def test_a_watch_speaks_to_orca_as_the_drives_own_terminal_whatever_pane_started_it(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCA_PANE_KEY", "tab:starter")
    with state.edit(demo) as data:
        data["roles"]["coordinator"]["terminal"] = "term_coordinator"
    watch.run_pass(watcher(demo), T0)
    assert (os.environ["ORCA_TERMINAL_HANDLE"], os.environ.get("ORCA_PANE_KEY")) == (DRIVE_TERMINAL, None)


def test_a_watch_orca_opens_no_terminal_for_says_so_on_its_third_try_running_and_reads_on(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(drive: str) -> str:
        raise orca.OrcaError("orca terminal create: runtime_unavailable: Orca is not running")

    monkeypatch.setattr(orca, "drive_terminal", refused)
    watching = watcher(demo)
    said = [watch.run_pass(watching, T0 + timedelta(seconds=5 * poll)) for poll in range(3)]
    assert said == [[], [], ["WATCH drive terminal read failed 3 times in a row: orca terminal create: runtime_unavailable: Orca is not running"]]


def test_a_watch_no_launch_agent_started_lets_its_pid_file_go_loads_the_agent_and_ends(
    demo: str, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess, capsys: pytest.CaptureFixture[str]
) -> None:
    state.pid_path(demo).write_text(f"{os.getpid()}\n")
    monkeypatch.setattr(watch, "passed", lambda watching, moment: pytest.fail("a pass ran outside the watch's launch agent"))
    assert watch.main(demo) == 0
    assert (watch_process.ensured, capsys.readouterr().out, state.pid_path(demo).exists()) == ([demo], f"the watch runs for {demo}\n", False)


def test_the_watch_waits_for_one_an_older_install_ran_under_a_monitor_and_sends_the_lines_it_held_once(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    passes = []
    monkeypatch.setattr(watch, "passed", lambda watching, moment: passes.append(moment) or [])
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

    monkeypatch.setattr(watch, "passed", crash)
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


def test_a_pass_that_finds_the_drive_saved_by_a_newer_install_ends_the_watch_once_it_posted_each_line_its_earlier_stages_made_so_the_watch_after_it_says_none_again(
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
    prover = f'JOB j2 o1 added, proves {KEY}, waits on no release: start its agent now, run_in_background, on the prompt "Read your brief first: {state.stored(demo)["jobs"]["j2"]["brief"]}"'
    assert (posted(sent), state.stored(demo)["prs"][KEY]["state"], state.held_path(demo).read_text(), state.pid_path(demo).exists()) == (
        [("evidence", f"root@{demo}", merged), ("handoff", f"root@{demo}", prover)],
        "merged",
        "",
        False,
    )
    saved_by(demo, state.VERSION)
    monkeypatch.setattr(watch, "upgraded", lambda: None)
    passes = []
    monkeypatch.setattr(watch, "passed", lambda watching, moment: passes.append(moment) or [])
    run_watch(demo, monkeypatch)
    assert (len(sent), len(passes), state.held_path(demo).exists()) == (2, 1, False)


def test_a_line_whose_post_failed_goes_to_the_watch_its_launch_agent_starts_on_the_newer_install_it_ended_for_between_passes(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upgrades(monkeypatch, tmp_path, [False, True])
    tapped = f"TAP Hold {KEY} [o1 j1]"
    monkeypatch.setattr(watch, "passed", lambda watching, moment: [[tapped]])
    monkeypatch.setattr(watch.time, "sleep", lambda seconds: None)
    sent = posts(monkeypatch, [False])
    launched(monkeypatch, demo)
    assert watch.main(demo) == 0
    assert (sent, state.held_path(demo).read_text()) == ([], f"{tapped}\n")
    monkeypatch.setattr(watch, "upgraded", lambda: None)
    monkeypatch.setattr(watch, "passed", lambda watching, moment: [])
    run_watch(demo, monkeypatch)
    assert posted(sent) == [("handoff", f"root@{demo}", tapped)]


def test_a_watch_that_crashes_mid_pass_has_posted_the_lines_its_earlier_stages_made_so_the_watch_its_launch_agent_starts_again_says_none_again(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"READY {state.pr_url(KEY)}")
    serve(monkeypatch, {KEY: read(state="MERGED", mergeCommit="ccc333")})
    mid_pass(monkeypatch, lambda: state.load("gone"))
    sent = posts(monkeypatch)
    launched(monkeypatch, demo)
    with pytest.raises(state.DriveError):
        watch.main(demo)
    assert ([text.split(":")[0] for _, _, text in posted(sent)], state.held_path(demo).read_text()) == (
        [f"PR {KEY}", f"JOB j2 o1 added, proves {KEY}, waits on no release", "WATCH stopped on long-running " + state.VERSION],
        "",
    )
    monkeypatch.setattr(watch, "passed", lambda watching, moment: [])
    run_watch(demo, monkeypatch)
    assert len(sent) == 3


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


def test_a_launched_watch_takes_the_env_of_this_macs_claude_settings_so_each_read_it_runs_carries_the_keys_a_session_here_carries(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.json").write_text(json.dumps({"model": "opus", "env": {"BUILDKITE_API_TOKEN": "the settings token"}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("BUILDKITE_API_TOKEN", "the token the watch inherited")
    monkeypatch.setattr(launchd, "session_env", REAL_SESSION_ENV)
    seen = []
    monkeypatch.setattr(watch, "passed", lambda watching, moment: seen.append(reads.run(["printenv", "BUILDKITE_API_TOKEN"], tmp_path)) or [])
    run_watch(demo, monkeypatch)
    assert seen == ["the settings token\n"]


def test_a_drive_under_long_running_state_gets_no_watch_process_and_a_watch_started_there_ends(
    demo: str, state_root: Path, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess, capsys: pytest.CaptureFixture[str]
) -> None:
    started = []
    monkeypatch.setenv("LONG_RUNNING_STATE", str(state_root))
    monkeypatch.setattr(watch, "ensure", REAL_ENSURE)
    monkeypatch.setattr(launchd, "keep", lambda *args: started.append(args))
    monkeypatch.setattr(watch, "passed", lambda watching, moment: pytest.fail("a pass ran on a drive under LONG_RUNNING_STATE"))
    assert cli.main(["watch", "--drive", demo]) == 0
    assert "watch.run_pass" in capsys.readouterr().out
    assert watch.main(demo) == 0
    assert (started, state.pid_path(demo).exists()) == ([], False)


def test_drive_watch_starts_the_watch_from_any_roles_session(
    demo: str, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess, capsys: pytest.CaptureFixture[str]
) -> None:
    with state.edit(demo) as data:
        data["roles"]["communicator"] = {"session": "talker", "cwd": "/work", "started": state.stamp(T0)}
    assert cli.main(["watch", "--drive", demo]) == 0
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    assert cli.main(["watch", "--drive", demo]) == 0
    assert capsys.readouterr().out == f"the watch runs for {demo}\n" * 2
    assert watch_process.ensured == [demo, demo]


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
    iris, top = watch.Watcher(demo, offset=state.events_end(demo), project="Iris"), watcher(demo)
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
    iris = watch.Watcher(demo, offset=state.watch_cursor(demo, "Iris"), project="Iris")
    reads[KEY] = read(state="MERGED", mergeCommit="ccc333", checks=RED)
    later = T0 + timedelta(minutes=2)
    assert watch.run_pass(top, later) == [f"TAP Hold {KEY} [o1 j1]"]
    assert watch.run_pass(iris, later) == [
        f"PR {KEY}: merged [o1 j1]",
        f"TAP Approve {KEY} [o1 j1]",
        'ORPHAN o1 "ship the watch": j1 waits on "nothing named" and no open PR, card or due time can wake it',
    ]
    assert calls == [[KEY], [KEY]]


def test_a_free_line_reaches_the_watch_of_the_session_whose_agent_runs_the_job_and_the_watch_hearing_its_order_once_none_does(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """His pick "Send them to the agent's starter": FREE 682910 on j2600 reached platform, which could not SendMessage the top's agent, and its hand-off to the top took 8 minutes 21 seconds."""
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j2"] = job(goal="release the service")
        data["jobs"]["j1"].update(state="waiting", waits=["j2"], agent="agent-j1", where="local", session="session-under-test")
    sub_coordinated(demo)
    iris, top = watch.Watcher(demo, offset=state.events_end(demo), project="Iris"), watcher(demo)
    assert (watch.run_pass(iris, T0), watch.run_pass(top, T0)) == ([], [])
    with state.edit(demo) as data:
        data["jobs"]["j2"]["state"] = "done"
    freed = T0 + timedelta(seconds=60)
    assert (watch.run_pass(iris, freed), watch.run_pass(top, freed)) == ([], ["FREE j1 o1: j2 answered or done, nothing else holds it"])
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", waits=["j2"], session="gone")
        data["jobs"]["j2"]["state"] = "running"
    assert (watch.run_pass(iris, freed + timedelta(minutes=1)), watch.run_pass(top, freed + timedelta(minutes=1))) == ([], [])
    with state.edit(demo) as data:
        data["jobs"]["j2"]["state"] = "done"
    later = freed + timedelta(minutes=2)
    assert (watch.run_pass(iris, later), watch.run_pass(top, later)) == (["FREE j1 o1: j2 answered or done, nothing else holds it"], [])


def test_a_projects_watch_hands_a_stuck_or_lane_line_to_the_session_that_started_its_jobs_agent_and_keeps_the_rest(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub_coordinated(demo, "Platform", "platform-1")
    with state.edit(demo) as data:
        data["jobs"]["j1"] |= {"agent": "a4667d5c611bb5233", "session": "session-under-test"}
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(agent="a-gone", session="ended-session")
    sent = posts(monkeypatch)
    platform = watch.Watcher(demo, offset=state.events_end(demo), project="Platform")
    stuck = 'STUCK j1 o1: its agent\'s Bash call "gh pr checks" has had no result for 30 minutes'
    died = 'LANE j1 o1 died as agent a4667d5c611bb5233 on this Mac: its transcript ended, while it worked on "watch"'
    kept = [f"PR {KEY}: checks failed [o1 j1]", 'STUCK j2 o1: its agent\'s Bash call "gh pr checks" has had no result for 30 minutes']
    assert watch.handed(platform, [stuck, died, *kept], T0) == kept
    assert posted(sent) == [("evidence", f"root@{demo}", stuck), ("handoff", f"root@{demo}", died)]
    assert platform.unsent == {"root": []}


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

    monkeypatch.setattr(watch, "passed", crash)
    with pytest.raises(OSError):
        watch.main(demo, "Iris")
    kind, to, text = posted(sent)[-1]
    assert (kind, to) == ("handoff", f"iris@{demo}")
    log = re.escape(str(state.watch_log_path(demo, "Iris")))
    assert re.fullmatch(rf"WATCH stopped on long-running {re.escape(state.VERSION)}: OSError at watch\.py:\d+ in watching: disk full; its traceback is in {log}; its launch agent starts it again within 10 seconds", text)


def test_drive_watch_project_starts_only_from_its_sub_coordinators_session(
    demo: str, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess, capsys: pytest.CaptureFixture[str]
) -> None:
    sub_coordinated(demo)
    assert cli.main(["watch", "--drive", demo, "--project", "Iris"]) == 2
    assert "only the sub-coordinator for Iris starts its watch, from its own session (iris-1)" in capsys.readouterr().err
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    assert cli.main(["watch", "--drive", demo, "--project", "Iris"]) == 0
    assert cli.main(["watch", "--drive", demo]) == 0
    assert watch_process.ensured == [f"{demo} --project Iris", demo]


REAL_LOCAL_GAP = watch.local_gap
REAL_CODEX_GAP = watch.codex_gap
CARD = "https://acme.slack.com/archives/D123/p1794200000000100"


def files_order(data: dict, **overrides: object) -> None:
    data["orders"]["o2"] = order(text="build the files design", jobs=["j2"], **overrides)
    data["orders"]["o2"]["parts"] = {"stack 1": state.new_part(), "stack 2": state.new_part()}
    data["jobs"]["j2"] = job(order="o2", parts=["stack 1"])


def test_a_part_no_job_carries_is_an_orphan_said_once_five_minutes_after_its_order_moved_which_drive_status_lists_until_it_heals(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        files_order(data)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == ['ORPHAN o2 "stack 2": no job']
    assert [watch.run_pass(watching, T0 + timedelta(minutes=minutes)) for minutes in (6, 35, 65, 95)] == [[], [], [], []]
    assert render.orphans(state.load(demo)["orders"]["o2"], 80) == [f'  ORPHAN "stack 2", said {render.when(state.stamp(T0 + timedelta(minutes=5)))}: no job']
    with state.edit(demo) as data:
        data["jobs"]["j2"]["parts"] = ["stack 1", "stack 2"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=96)) == []
    assert render.orphans(state.load(demo)["orders"]["o2"], 80) == []


def test_a_stranded_jobs_orphans_name_once_the_pr_its_line_names_moved_to_another_job_and_the_job_it_names_finished_as_686978_and_686979_missed(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    parts = ["auto partitioning on the same abstraction", "auto partitioning status"]
    moved = "Forge-AI/monorepo#55175"
    said = f"READY {state.pr_url(moved)} waits on j4's runbook"
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="done", updated=earlier)
        data["orders"]["o2"] = order(text=parts[0], jobs=["j2", "j3", "j4"], kind="build", parts={part: state.new_part() for part in parts}, updated=earlier)
        data["jobs"]["j2"] = job(order="o2", state="waiting", said=said, parts=parts, updated=earlier)
        data["jobs"]["j3"] = job(order="o2", parts=[], updated=earlier)
        data["jobs"]["j4"] = job(order="o2", state="done", said="DONE census reads 0 unlinked moved tables", parts=[], updated=earlier)
        data["prs"][moved] = state.new_pr(state.pr_url(moved), "j3", "o2")
    stranded = f'j2 waits on "{said}" and no open PR, card or due time can wake it; of what its line names, {moved} moved to j3, j4 done'
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == [f'ORPHAN o2 "{part}": {stranded}' for part in parts]
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == []


def test_each_change_a_design_lists_with_no_job_is_an_orphan_whatever_earlier_change_it_waits_on_but_while_he_picks_an_option(
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
    with state.edit(demo) as data:
        assert state.add_changes(demo, data, "j2", stack) == []
        data["orders"]["o2"]["updated"] = before
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    with state.edit(demo) as data:
        data["orders"]["o2"]["options_only"] = False
    unbuilt = ("the reads through it", "the writes through it", "the old tables dropped")
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == [f'ORPHAN o2 "{change}": no job' for change in unbuilt]


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
        'ORPHAN o2 "redesign the moved tables": no builder carries this work, only design j2 done: start one with drive job add builder --order o2'
        " --part 'redesign the moved tables' --goal '<what it builds>', or add it to a builder with drive job set <builder> --part 'redesign the moved tables', adding --carries o2 for another order's builder; when it asks only for an answer,"
        " drive order set o2 --kind answer --part 'redesign the moved tables'; when a merged PR delivered it, drive order posted o2 <its link> --part 'redesign the moved tables'",
        'ORPHAN o2 "the moved-table interface": j3 stopped',
    ]


def test_a_text_held_on_a_lane_is_said_undelivered_ten_minutes_on_and_every_ten_minutes_after(demo: str) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(where="remote", dispatch="ctx_lane1")
        data["jobs"]["j1"]["undelivered"] = [{"seq": "665156", "words": "ship the narrower fix", "at": state.stamp(T0), "said": None, "request": "req-held"}]
    watching = watcher(demo)
    line = (
        f'ANSWER undelivered j1: "ship the narrower fix", held at {render.when(state.stamp(T0))}, has not reached j1; the watch sends it to a worker that'
        " takes messages, hands it to the next worker of one that returned and launches a builder never started with it, and drive job resume j1 delivers it now [o1 j1]"
    )
    assert watch.undelivered_lines(watching, T0 + timedelta(minutes=9)) == []
    assert watch.undelivered_lines(watching, T0 + timedelta(minutes=10)) == [line]
    assert watch.undelivered_lines(watching, T0 + timedelta(minutes=15)) == []
    assert watch.undelivered_lines(watching, T0 + timedelta(minutes=20)) == [line]


def test_a_text_held_on_an_agent_on_this_mac_names_its_sessions_sendmessage_and_none_is_said_on_a_builder_its_launch_carries_it_to(demo: str) -> None:
    held = [{"seq": "699075", "words": "drift only in a window you pick", "at": state.stamp(T0), "said": None, "request": "req-held"}]
    with state.edit(demo) as data:
        data["orders"]["o1"]["jobs"] += ["j2", "j3"]
        data["jobs"]["j1"].update(agent="agent-j1", where="local", session="session-under-test", undelivered=held)
        data["jobs"]["j2"] = job(state="waiting", waits=["demo:j1-7"], undelivered=held)
        data["jobs"]["j3"] = job(state="waiting", waits=["demo:j1-7", "j2"], undelivered=held)
    assert watch.undelivered_lines(watcher(demo), T0 + timedelta(minutes=10)) == [
        f'ANSWER undelivered j1: "drift only in a window you pick", held at {render.when(state.stamp(T0))}, has not reached j1; the watch sends it to a worker that'
        " takes messages, hands it to the next worker of one that returned and launches a builder never started with it; SendMessage it his words verbatim from"
        " root@demo, the session session-under-test that started its agent agent-j1 [o1 j1]"
    ]


def held_tap(demo: str, tapped: datetime) -> None:
    words = "drift only in a window you pick"
    closed = state.new_close("answered", words, CARD, seq="699075", at=state.stamp(tapped))
    held = {"seq": "699075", "words": f'His answer on card demo:j1-7 ({CARD}), verbatim: "{words}"', "at": state.stamp(tapped), "said": None, "request": "req-held"}
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(agent="agent-j1", where="local", session="session-under-test", undelivered=[held])
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "When may it drift?", state.stamp(tapped), card=CARD, closed=closed) | {"waits": "demo:j1-7"})


@pytest.mark.parametrize("waiting", [False, True])
def test_a_sendmessage_relaying_his_words_to_an_agent_on_this_mac_takes_his_answer_off_its_job_so_the_watch_never_says_it_undelivered(
    demo: str, send_message: Callable[..., None], waiting: bool
) -> None:
    held_tap(demo, T0)
    send_message(demo, "j1", "He tapped: drift only in a window you pick. Go on with that.", waiting)
    assert watch.undelivered_lines(watcher(demo), T0 + timedelta(minutes=10)) == []
    assert state.load(demo)["jobs"]["j1"]["undelivered"] == []


def test_a_sendmessage_written_before_he_gave_his_words_leaves_his_answer_undelivered(demo: str, send_message: Callable[..., None]) -> None:
    tapped = state.now() + timedelta(minutes=5)
    held_tap(demo, tapped)
    send_message(demo, "j1", "If he taps it: drift only in a window you pick")
    assert [line.split(":")[0] for line in watch.undelivered_lines(watcher(demo), tapped + timedelta(minutes=10))] == ["ANSWER undelivered j1"]


def test_the_watch_carries_held_texts_to_a_lane_that_returned_and_launches_a_builder_never_started_once_its_waits_end_leaving_an_agent_its_starter_reaches_to_its_sendmessage(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    detached = []

    def detach(name: str, args: list[str], log: str) -> int:
        detached.append(args)
        done = subprocess.Popen(["true"])
        done.wait()
        return done.pid

    monkeypatch.setattr(state, "detach", detach)
    held = [{"seq": "9", "words": 'His answer on card demo:j1-7, verbatim: "Ring"', "at": state.stamp(T0), "said": None, "request": "req-ring"}]
    with state.edit(demo) as data:
        data["orders"]["o1"]["jobs"] += ["j2", "j3", "j4", "j5"]
        data["jobs"]["j1"]["undelivered"] = held
        data["jobs"]["j2"] = job(state="built", where="remote", dispatch="ctx_lane2", undelivered=held)
        data["jobs"]["j3"] = job(state="waiting", waits=["j1"], undelivered=held)
        data["jobs"]["j4"] = job(state="built", where="local", agent="agent-j4", session="session-under-test", undelivered=held)
        data["jobs"]["j5"] = job(state="built", where="local", agent="agent-j5", session="ended-session", undelivered=held)
    watching = watcher(demo)
    watch.carry_held(watching, T0)
    watch.carry_held(watching, T0 + timedelta(minutes=5))
    assert detached == [["job", "resume", "j2"], ["job", "launch", "j5"]]
    with state.edit(demo) as data:
        data["jobs"]["j1"]["state"] = "done"
    watch.carry_held(watching, T0 + timedelta(minutes=11))
    assert detached == [["job", "resume", "j2"], ["job", "launch", "j5"], ["job", "resume", "j2"], ["job", "launch", "j3"], ["job", "launch", "j5"]]


def test_the_watch_starts_no_launch_of_a_codex_job_while_its_codex_ask_run_goes_and_launches_it_with_its_held_text_once_it_returned(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    detached = []

    def detach(name: str, args: list[str], log: str) -> int:
        detached.append(args)
        done = subprocess.Popen(["true"])
        done.wait()
        return done.pid

    monkeypatch.setattr(state, "detach", detach)
    held = [{"seq": "9", "words": 'His answer on card demo:j2-7, verbatim: "Ring"', "at": state.stamp(T0), "said": None, "request": "req-ring"}]
    with state.edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(kind="codex", codex_run="/drive/scratch/j2/codex", undelivered=held)
    watching = watcher(demo)
    watch.carry_held(watching, T0)
    watch.carry_held(watching, T0 + timedelta(minutes=11))
    assert detached == []
    with state.edit(demo) as data:
        data["jobs"]["j2"]["state"] = "waiting"
    watch.carry_held(watching, T0 + timedelta(minutes=12))
    assert detached == [["job", "launch", "j2"]]


def test_his_tap_on_a_lane_questions_card_leads_its_orphan_line_until_his_undo_reopens_the_question_his_card_asks_again(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
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
    tap = (
        f'"{render.cut(f"His answer on card {CARD}, verbatim: \"Ring\"", 80)}", held at {render.when(state.stamp(tapped))}, has not reached j1; the watch sends it'
        " to a worker that takes messages, hands it to the next worker of one that returned and launches a builder never started with it; drive job launch j1 on its FREE line,"
        " which carries each answer of his it has not heard to its first worker"
    )
    assert watch.run_pass(watching, T0 + timedelta(minutes=15)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == [f'ORPHAN o1 "ship the watch": {tap}, answering its lane\'s question "{asked}" 30 minutes ago']
    with monkeypatch.context() as clock:
        clock.setattr(state, "now", lambda: T0 + timedelta(minutes=31))
        card.question_answered({"waits": "demo:j1-7", "label": "Ring", "url": CARD, "undone": True})
    assert view.open_cards(state.load(demo)["jobs"]["j1"]) == [CARD]
    assert watch.run_pass(watching, T0 + timedelta(minutes=60)) == []


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


def test_a_part_another_orders_builder_carries_is_delivered_by_that_builders_done(
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
    data = state.load(demo)
    assert (data["jobs"]["j1"]["carries"], data["orders"]["o2"]["jobs"], state.delivered(data, "o2", "stack 2")) == ({"o2": ["stack 2"]}, ["j2", "j1"], True)
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == []


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
        data["jobs"]["j2"] = job(session="session-under-test", parts=["ship the watch"])
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
        data["orders"]["o2"] = order(text="why do old runs fail?", jobs=["j2"], kind="answer")
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


def test_work_he_ordered_is_carried_only_by_a_builder_never_by_research_his_card_or_a_post(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    merge = "get the inbox work merged"
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="done", updated=earlier)
        data["orders"]["o2"] = order(text=merge, jobs=["j2"], kind="build", parts={merge: state.new_part() | {"posted": REPORTED}}, updated=earlier)
        data["jobs"]["j2"] = job(order="o2", kind="reader", parts=[merge], updated=earlier)
    unbuilt = (
        f"when it asked only for an answer, drive order set o2 --kind answer --part '{merge}' closes it; when it asks for a change,"
        f" start its builder with drive job add builder --order o2 --part '{merge}' --goal '<what it builds>'{{waits}}"
    )
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == [f'ORPHAN o2 "{merge}": {ANSWERED}reader j2 running: {unbuilt.format(waits=" --waits j2")}']
    card = {"question": "Which inbox lands first?", "options": ["First", "Last"], "recommended": "First", "asked": state.stamp(T0), "url": f"{CARD}?thread_ts=1.2", "card": {"title": "Which inbox lands first?", "waits": "demo:j2-7-a"}}
    monkeypatch.setattr(watch.view, "cards", lambda name: {"team_url": "", "questions": [card], "closed": [], "pr_cards": []})
    with state.edit(demo) as data:
        data["jobs"]["j2"].update(kind="design", state="done", questions=[state.new_question(None, None, state.stamp(T0), card=CARD, holds=None)])
        data["orders"]["o2"]["parts"][merge]["orphan_said"] = None
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == [f'ORPHAN o2 "{merge}": {ANSWERED}design j2 done, his card {CARD} open: {unbuilt.format(waits=f" --waits {CARD}")}']
    with state.edit(demo) as data:
        data["orders"]["o2"]["jobs"].append("j3")
        data["jobs"]["j3"] = job(order="o2", kind="codex", parts=[merge], updated=earlier)
    assert watch.run_pass(watching, T0 + timedelta(seconds=10)) == []
    with state.edit(demo) as data:
        data["jobs"]["j3"]["state"] = "done"
    assert state.delivered(state.load(demo), "o2", merge)


@pytest.mark.parametrize(("kind", "part", "posted", "held", "built", "said"), [case[1:] for case in REPLAYED_685794], ids=[str(case[0]) for case in REPLAYED_685794])
def test_a_part_of_an_order_of_no_kind_only_research_ever_held_is_his_question_its_post_answers_while_a_build_orders_part_stays_work(
    demo: str, monkeypatch: pytest.MonkeyPatch, kind: str | None, part: str, posted: bool, held: list[tuple[str, str]], built: bool, said: str | None
) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    other = "ship the other fix"
    jobs = [f"j{number}" for number in range(2, len(held) + 2 + built)]
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="done", updated=earlier)
        parts = {part: state.new_part() | {"posted": REPORTED if posted else None}} | ({other: state.new_part()} if built else {})
        data["orders"]["o2"] = order(text=part, jobs=jobs, kind=kind, parts=parts, updated=earlier)
        for job_id, (job_kind, job_state) in zip(jobs, held, strict=False):
            due = state.stamp(T0 + timedelta(hours=1)) if (job_kind, job_state) == ("reader", "waiting") else None
            data["jobs"][job_id] = job(order="o2", kind=job_kind, state=job_state, parts=[part], due=due, updated=earlier)
        if built:
            data["jobs"][jobs[-1]] = job(order="o2", state="done", parts=[other], updated=earlier)
    drawn = [line for line in watch.run_pass(watcher(demo), T0) if line.startswith("ORPHAN o2 ")]
    assert [line.startswith(f'ORPHAN o2 "{render.cut(part, 80)}": {said}') for line in drawn] == ([True] if said else [])


def test_a_part_sorted_as_an_answer_is_delivered_by_the_post_it_holds_and_one_a_builder_works_on_is_never_sorted(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(state, "now", lambda: T0)
    earlier = state.stamp(T0 - timedelta(minutes=10))
    asked, fix = "why do old runs fail?", "fix them"
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="done", updated=earlier)
        data["orders"]["o2"] = order(text=asked, jobs=["j2", "j3"], kind="build", parts={asked: state.new_part() | {"posted": REPORTED}, fix: state.new_part()}, updated=earlier)
        data["jobs"]["j2"] = job(order="o2", kind="reader", state="done", parts=[asked], updated=earlier)
        data["jobs"]["j3"] = job(order="o2", parts=[fix], updated=earlier)
    watching = watcher(demo)
    [line] = watch.run_pass(watching, T0)
    assert line.startswith(f'ORPHAN o2 "{asked}": {ANSWERED}reader j2 done: ')
    assert f"when it asked only for an answer, drive order set o2 --kind answer --part '{asked}' closes it;" in line
    assert cli.main(["order", "set", "o2", "--part", asked, "--drive", demo]) == 2
    assert "--part: refused, it sorts a part of o2 with --kind answer" in capsys.readouterr().err
    assert cli.main(["order", "set", "o2", "--kind", "answer", "--part", fix, "--drive", demo]) == 2
    assert f'--part "{fix}": refused, builder j3 works on it, so it is work under way, never an answer' in capsys.readouterr().err
    assert cli.main(["order", "set", "o2", "--kind", "answer", "--part", asked, "--drive", demo]) == 0
    sorted_order = state.load(demo)["orders"]["o2"]
    assert (sorted_order["kind"], sorted_order["parts"][asked]["answer"], sorted_order["parts"][fix]["answer"]) == ("build", True, False)
    with state.edit(demo) as data:
        data["orders"]["o2"]["parts"][asked]["orphan_said"] = None
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    assert state.delivered(state.load(demo), "o2", asked)


def test_a_merged_pr_delivers_a_work_part_through_drive_order_posted_which_refuses_a_post_and_an_open_pr(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    lands, landed = "#43 lands", state.pr_url(OTHER)
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="done", updated=earlier)
        data["orders"]["o2"] = order(text=lands, jobs=["j2"], kind="build", parts={lands: state.new_part()}, updated=earlier)
        data["jobs"]["j2"] = job(order="o2", kind="reader", state="done", parts=[lands], updated=earlier)
        data["prs"][OTHER] = state.new_pr(landed, "j1", "o1") | {"state": "merged"}
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == [
        f'ORPHAN o2 "{lands}": no builder carries this work, only reader j2 done: start one with drive job add builder --order o2 --part \'{lands}\''
        f" --goal '<what it builds>', or add it to a builder with drive job set <builder> --part '{lands}', adding --carries o2 for another order's builder; {OTHER} merged, so drive order posted o2 {landed} --part '{lands}' delivers it"
    ]
    assert cli.main(["order", "posted", "o2", REPORTED, "--part", lands, "--drive", demo]) == 2
    assert f'--part "{lands}": refused, ' + state.UNPOSTED_WORK.format(order="o2", part=lands, holder="root@demo") in capsys.readouterr().err
    serve(monkeypatch, {YASYF: read(), OTHER: read(state="MERGED")})
    assert cli.main(["order", "posted", "o2", state.pr_url(YASYF), "--part", lands, "--drive", demo]) == 2
    assert f'--part "{lands}": refused, GitHub reads {YASYF} open, and only a merged PR delivers work he ordered' in capsys.readouterr().err
    assert cli.main(["order", "posted", "o2", landed, "--part", lands, "--drive", demo]) == 0
    assert capsys.readouterr().out == f'o2 "{lands}": posted {landed}, the merged PR that delivered it\n'
    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["orders"]["o2"]["parts"][lands]["orphan_said"] = None
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []


@pytest.mark.parametrize(
    ("now", "said"),
    [
        ("MERGED", f"{OTHER} merged, so drive order posted o2 {state.pr_url(OTHER)} --part 'port the fixes' delivers it"),
        ("CLOSED", f"{OTHER} closed unmerged, carrying nothing of j2's work: start its builder with drive job add builder --order o2 --part 'port the fixes' --goal 'port the fixes'"),
    ],
)
def test_a_pr_the_drive_does_not_track_carries_the_part_of_the_stopped_builder_no_agent_took_that_keeps_it_while_open_and_its_delivery_says_how_it_ended(
    demo: str, monkeypatch: pytest.MonkeyPatch, now: str, said: str
) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    monkeypatch.setattr(state, "now", lambda: T0 - timedelta(minutes=10))
    calls = serve(monkeypatch, {OTHER: read()})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    port = "port the fixes"
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="done", updated=earlier)
        data["orders"]["o2"] = order(text=port, jobs=["j2"], kind="build", parts={port: state.new_part()}, updated=earlier)
        data["jobs"]["j2"] = job(order="o2", goal=port, state="stopped", parts=[port], updated=earlier)
    assert cli.main(["job", "set", "j2", "--pr", state.pr_url(OTHER), "--drive", demo]) == 0
    watching = watcher(demo)
    assert (watch.run_pass(watching, T0), watch.run_pass(watching, T0 + timedelta(seconds=5)), calls) == ([], [], [[KEY, OTHER]])
    assert render.part_line(state.load(demo), "o2", port) == f'"{port}": builder j2 stopped with {state.pr_url(OTHER)} carrying it'
    calls = serve(monkeypatch, {OTHER: read(state=now)})
    state.append_event(demo, receiver.DELIVERY, repo="Forge-AI/monorepo", numbers=[43], event="pull_request", guid="g1")
    assert watch.run_pass(watching, T0 + timedelta(seconds=10)) == [f'ORPHAN o2 "{port}": {said}']
    assert (watch.run_pass(watching, T0 + timedelta(seconds=15)), calls) == ([], [[OTHER]])


def test_a_stopped_builder_no_agent_took_holds_its_part_as_work_under_way_only_while_it_keeps_a_pr(demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(state, "now", lambda: T0)
    asked = "is the port already written?"
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(text=asked, jobs=["j2"], kind="build", parts={asked: state.new_part()})
        data["jobs"]["j2"] = job(order="o2", state="stopped", prs=[state.pr_url(OTHER)], parts=[asked])
    assert cli.main(["order", "set", "o2", "--kind", "answer", "--part", asked, "--drive", demo]) == 2
    assert f'--part "{asked}": refused, builder j2 works on it, so it is work under way, never an answer' in capsys.readouterr().err
    with state.edit(demo) as data:
        data["jobs"]["j2"]["prs"] = []
    assert cli.main(["order", "set", "o2", "--kind", "answer", "--part", asked, "--drive", demo]) == 0
    assert state.load(demo)["orders"]["o2"]["parts"][asked]["answer"]


@pytest.mark.parametrize(("finished", "pr"), [("stopped", "merged"), ("failed", "closed"), ("done", None)])
def test_a_part_whose_codex_job_finished_with_no_open_pr_sorts_as_an_answer_while_a_running_one_or_an_open_pr_holds_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], finished: str, pr: str | None
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(state, "now", lambda: T0)
    asked = "why do old runs fail?"
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(text=asked, jobs=["j2"], kind="build", parts={asked: state.new_part()})
        data["jobs"]["j2"] = job(order="o2", kind="codex", parts=[asked])
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o2")
    assert cli.main(["order", "set", "o2", "--kind", "answer", "--part", asked, "--drive", demo]) == 2
    assert f'--part "{asked}": refused, codex j2, {OTHER} works on it, so it is work under way, never an answer' in capsys.readouterr().err
    with state.edit(demo) as data:
        data["jobs"]["j2"]["state"] = finished
    assert cli.main(["order", "set", "o2", "--kind", "answer", "--part", asked, "--drive", demo]) == 2
    assert f'--part "{asked}": refused, {OTHER} works on it, so it is work under way, never an answer' in capsys.readouterr().err
    with state.edit(demo) as data:
        if pr:
            data["prs"][OTHER]["state"] = pr
        else:
            del data["prs"][OTHER]
    assert cli.main(["order", "set", "o2", "--kind", "answer", "--part", asked, "--drive", demo]) == 0
    assert state.load(demo)["orders"]["o2"]["parts"][asked]["answer"]


def test_only_the_session_an_orders_orphan_lines_reach_runs_drive_order_posted_and_each_refusal_names_that_session(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sub_coordinated(demo)
    lands, landed = "#43 lands", state.pr_url(OTHER)
    with state.edit(demo) as data:
        data["orders"]["o2"] = order(text=lands, jobs=["j2"], kind="build", parts={lands: state.new_part()})
        data["jobs"]["j2"] = job(order="o2", state="failed", prs=[landed], parts=[lands])
        data["prs"][OTHER] = state.new_pr(landed, "j2", "o2") | {"state": "merged"}
    serve(monkeypatch, {OTHER: read(state="MERGED")})
    posted = ["order", "posted", "o2", landed, "--part", lands, "--drive", demo]
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    assert cli.main(posted) == 2
    assert "work a merged PR delivered, is marked from the root@demo session, the one its ORPHAN lines reach" in capsys.readouterr().err
    with state.edit(demo) as data:
        data["orders"]["o2"]["project"] = "Iris"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-under-test")
    assert cli.main(posted) == 2
    assert "work a merged PR delivered, is marked from the iris@demo session, the one its ORPHAN lines reach" in capsys.readouterr().err
    assert state.load(demo)["orders"]["o2"]["parts"][lands]["posted"] is None
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    assert cli.main(posted) == 0
    assert state.load(demo)["orders"]["o2"]["parts"][lands]["posted"] == landed


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
        f'ORPHAN o2 "fix": no fix job: j2 ended {ended} with no open or merged PR; start the second fix job now, an Opus 5.5 long-running:builder on j2\'s brief:'
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


def test_a_built_job_whose_card_did_not_draw_is_said_to_wait_on_a_card_never_posted_while_the_watch_draws_it_again(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)

    def locked(name: str, job_id: str, file: Path, card: dict) -> str:
        raise slack.SlackError("dm-status: database is locked")

    monkeypatch.setattr(cli, "ask_card", lambda name, job_id, file: {"waits": "demo:j1-7"})
    monkeypatch.setattr(cli, "post_ask", locked)
    asked = "How many clone processes may a SandSQL server run at once?"
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        question = state.new_question("/drive/asks/j1-ab.md", asked, state.stamp(T0)) | {"waits": "demo:j1-7"}
        data["jobs"]["j1"].update(state="built", branch="clone-cap", questions=[question], updated=state.stamp(T0 - timedelta(minutes=10)))
    assert watch.run_pass(watcher(demo), T0) == [
        f'ORPHAN o1 "ship the watch": j1 waits on a card never posted: its question "{asked}" kept in /drive/asks/j1-ab.md did not draw, and the watch draws it again on its next read'
    ]


@pytest.mark.parametrize(
    ("builder", "reader", "said", "card"),
    [
        ("j2851", "j2848", "BUILT drive-refused-stack-merge, waits on j2848's card", "durable-agents:j2848-686260"),
        ("j2846", "j2837", "BUILT long-running/repeat-tooling-orders-join, waits on j2837's card", "durable-agents:j2837-686130"),
        ("j2900", "j2899", "BUILT drive/records-follow-job-session, waits on his o1018 card, j2899's DESIGN asking which session a coordinator's records reach", "durable-agents:j2899-687044"),
    ],
)
def test_a_builder_built_beside_its_readers_open_card_waits_on_that_card_whatever_words_its_line_names_it_in(
    demo: str, monkeypatch: pytest.MonkeyPatch, told: list[dict], builder: str, reader: str, said: str, card: str
) -> None:
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    with state.edit(demo) as data:
        data["prs"].pop(KEY)
        parts = data["jobs"].pop("j1")["parts"]
        asked = state.new_question(f"/drive/asks/{reader}-ab.md", "Which shape?", state.stamp(T0), holds=None) | {"waits": card, "card": drawn_card(1)}
        data["jobs"][reader] = job(kind="reader", state="done", parts=parts, questions=[asked])
        data["jobs"][builder] = job(parts=parts, researched=[reader])
        data["orders"]["o1"]["jobs"] = [reader, builder]
    with state.edit(demo) as data:
        state.take_result(data, builder, said)
        data["jobs"][builder]["updated"] = state.stamp(T0 - timedelta(minutes=10))
    assert state.load(demo)["jobs"][builder]["waits"] == [card]
    branch = said.removeprefix("BUILT ").partition(",")[0]
    assert watch.run_pass(watcher(demo), T0) == [f"WAITING-ON-HIM {card} blocks {builder}, built on {branch}, since {render.when(state.stamp(T0))}, posted {POSTED}"]


def test_a_builder_waiting_on_its_readers_card_that_drive_job_card_refused_is_said_to_wait_on_a_card_never_posted(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    refused = 'the Show me picture "1 · Before" does not draw: Parse error on line 7'
    with state.edit(demo) as data:
        data["prs"].pop(KEY)
        parts = data["jobs"].pop("j1")["parts"]
        asked = state.new_question("/drive/asks/j2612-ab.md", "Which shape?", state.stamp(T0), refused=refused, holds=None) | {"waits": "durable-agents:j2612-684681"}
        data["jobs"]["j2612"] = job(kind="reader", state="done", parts=parts, questions=[asked])
        data["jobs"]["j2649"] = job(parts=parts, researched=["j2612"])
        data["orders"]["o1"]["jobs"] = ["j2612", "j2649"]
    with state.edit(demo) as data:
        state.take_result(data, "j2649", "BUILT j2649-sparse-replace, waits on j2612's card")
        data["jobs"]["j2649"]["updated"] = state.stamp(T0 - timedelta(minutes=10))
    assert watch.run_pass(watcher(demo), T0) == [
        f'ORPHAN o1 "ship the watch": j2649 waits on a card never posted: drive job card refused j2612\'s question "Which shape?" kept in /drive/asks/j2612-ab.md: {refused}'
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
        data["orders"]["o2"] = order(text="options: only the owning machine writes", jobs=["j2"], options_only=True)
        data["jobs"]["j2"] = job(
            order="o2",
            kind="design",
            state="done",
            questions=[state.new_question(None, None, state.stamp(T0), card=CARD)],
            undelivered=[{"seq": "9", "words": "First", "at": state.stamp(T0 - timedelta(minutes=20)), "said": None, "request": "req-held"}],
            updated=state.stamp(T0 - timedelta(minutes=10)),
        )
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    with state.edit(demo) as data:
        data["jobs"]["j2"]["questions"][0]["closed"] = state.new_close("answered", "First", None, seq="9", at=state.stamp(T0 - timedelta(minutes=10)))
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
OVERLOADED = {
    "type": "assistant",
    "timestamp": "2026-11-12T20:44:05.907Z",
    "isApiErrorMessage": True,
    "message": {"model": "<synthetic>", "stop_reason": "stop_sequence", "content": [{"type": "text", "text": "API Error: 529 Overloaded"}]},
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
            [CALLING, RESULT, OVERLOADED],
            timedelta(minutes=45),
            'j1\'s agent stopped 45 minutes ago on stop_sequence: "API Error: 529 Overloaded"; drive job resume j1 <the event> relaunches it on a Sprite with a handoff',
        ),
        (
            [CALLING, PARKED],
            timedelta(minutes=45),
            'j1\'s agent ended its turn 45 minutes ago on "Codex is still running. I\'ll wait for the notification." with no result line, so wake it',
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


@pytest.mark.parametrize(
    ("ran", "gap"),
    [
        ("running", None),
        ("pending", None),
        ("completed", "j1's codex-ask run completed and its result is not recorded"),
        ("died", "j1's codex-ask run died and its result is not recorded"),
    ],
)
def test_a_codex_job_is_judged_by_the_codex_ask_run_its_launch_recorded(demo: str, codex_ask: CodexAsk, ran: str, gap: str | None) -> None:
    job_of = {"kind": "codex", "codex_run": str(codex_ask.run(ran, codex_reply("DONE", "reviewed")))}
    assert REAL_CODEX_GAP("j1", job_of) == gap


def test_a_codex_job_no_launch_started_names_the_launch_that_starts_it(demo: str) -> None:
    assert REAL_CODEX_GAP("j1", {"kind": "codex"}) == "j1 has no codex-ask run: drive job launch j1 starts it on its brief"


@pytest.mark.parametrize(
    ("printed", "gap"),
    [
        ("", "j1's codex-ask run could not be read: codex-ask --collect printed no record:"),
        ("codex-ask 1.15.3\n", "j1's codex-ask run could not be read: codex-ask --collect printed no record: codex-ask 1.15.3"),
    ],
)
def test_a_codex_ask_collect_printing_no_record_of_the_run_ends_no_watch_pass(demo: str, tmp_path: Path, codex_ask: CodexAsk, printed: str, gap: str) -> None:
    folder = codex_ask.run("completed", codex_reply("DONE", "reviewed"))
    (tmp_path / "codex-plugin" / "bin" / "codex-ask").write_text(f"#!/bin/sh\nprintf '%s' '{printed}'\n")
    assert REAL_CODEX_GAP("j1", {"kind": "codex", "codex_run": str(folder)}) == gap


def test_a_codex_job_whose_run_codex_ask_cannot_read_names_its_error(demo: str, tmp_path: Path, codex_ask: CodexAsk) -> None:
    folder = codex_ask.run("completed", codex_reply("DONE", "reviewed"))
    (tmp_path / "codex-plugin" / "bin" / "codex-ask").write_text("#!/bin/sh\necho 'codex-ask: no run at that folder' >&2\nexit 2\n")
    assert REAL_CODEX_GAP("j1", {"kind": "codex", "codex_run": str(folder)}) == "j1's codex-ask run could not be read: codex-ask: no run at that folder"


def test_a_codex_job_run_again_after_its_run_was_taken_waits_on_its_next_launch_rather_than_retaking_the_old(demo: str, codex_ask: CodexAsk) -> None:
    folder = codex_ask.run("completed", codex_reply("DONE", "reviewed"))
    reply = folder / "codex-r-1"
    taken = state.stamp(datetime.fromtimestamp(reply.stat().st_mtime).astimezone() + timedelta(seconds=1))
    job_of = {"kind": "codex", "codex_run": str(folder), "returned": None}
    assert REAL_CODEX_GAP("j1", job_of) == "j1's codex-ask run completed and its result is not recorded"
    assert REAL_CODEX_GAP("j1", job_of | {"returned": taken}) == "j1's codex-ask run completed before its last return: drive job launch j1 starts its next"


def test_an_incidents_second_lane_builder_is_judged_by_its_transcript_until_the_codex_run_beside_it_ends(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk
) -> None:
    monkeypatch.setattr(watch, "local_gap", REAL_LOCAL_GAP)
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    write_transcript(tmp_path / "config", [CALLING, FINISHED], timedelta(minutes=10))
    folder = codex_ask.run("running")
    job_of = {"order": "o1", "where": "local", "session": "session-under-test", "agent": "a1", "kind": "codex", "codex_run": str(folder)}
    assert watch.agent_gap("j1", job_of, state.load(demo), state.now()) == 'j1\'s agent finished and its result is not recorded: "DONE shipped"' + REPORT_IT + WOKEN_BY_ITS_SESSION
    codex_ask.end(folder, "completed", codex_reply("DONE", "reviewed"))
    assert isinstance(watch.agent_gap("j1", job_of, state.load(demo), state.now()), watch.Answered)


def codex_running(demo: str, folder: Path) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"].update(kind="codex", agent=None, codex_run=str(folder), updated=state.stamp(state.now() - timedelta(minutes=10)))


def test_the_watch_takes_a_codex_jobs_result_from_the_state_line_and_report_its_completed_run_returned(
    demo: str, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk
) -> None:
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    codex_running(demo, codex_ask.run("completed", codex_reply("DONE", "two of three findings hold", "Finding 1\n")))
    assert watch.run_pass(watcher(demo), state.now()) == ["JOB j1 o1 DONE two of three findings hold"]
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], job["said"], state.report_text(demo, "j1")) == ("done", "DONE two of three findings hold", "DONE two of three findings hold\nFinding 1\n")


def test_the_watch_tells_the_coordinator_of_a_codex_run_that_died_before_its_first_turn(demo: str, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk) -> None:
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    died = "Error: thread/start: thread/start failed: error creating thread: Fatal error: Failed to initialize session: required MCP servers failed to initialize: datadog: MCP client startup timed out after 30s (code -32603)"
    codex_running(demo, codex_ask.run("failed", log=f"Reading prompt from stdin...\n{died}\n"))
    assert watch.run_pass(watcher(demo), state.now()) == [f"JOB j1 o1 BLOCKED codex-ask failed: {died}"]
    assert state.load(demo)["jobs"]["j1"]["state"] == "failed"


def test_a_codex_lane_that_built_its_recommended_option_has_the_ask_block_it_named_kept_as_its_run_is_taken_so_his_card_goes_up_as_it_returns(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk
) -> None:
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    monkeypatch.setattr(watch.view, "cards", lambda name: view.NO_CARDS)
    block = tmp_path / "scratch" / "ask.md"
    block.parent.mkdir()
    block.write_text(SHORT_ASK)
    codex_running(demo, codex_ask.run("completed", codex_reply("DONE", "built one snapshot file per finished run on branch snapshot-file", f"ASK: {block}")))
    kept = state.drive_dir(demo) / "asks" / f"j1-{state.ask_hash(SHORT_ASK)}.md"
    assert watch.run_pass(watcher(demo), state.now()) == [
        f"JOB j1 o1 DONE built one snapshot file per finished run on branch snapshot-file; its ASK is kept in {kept},"
        f" his card {drawn_card(1)} asks it under demo:j1-7, and the recommended option it built ships meanwhile"
    ]
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], [question["file"] for question in job["questions"]], kept.read_text()) == ("waiting", [str(kept)], SHORT_ASK)


def incident_codex_running(demo: str, folder: Path, part: str) -> None:
    codex_running(demo, folder)
    with state.edit(demo) as data:
        data["orders"]["o1"].update(incident=True, parts={part: state.new_part()})
        data["jobs"]["j1"].update(goal="fix: runs never start", parts=[part], brief="/briefs/j1.md")


@pytest.mark.parametrize(
    ("reply", "ended"),
    [
        (codex_reply("HANDOFF", "edits left uncommitted in worktree fix-runs on branch fix-runs; commit, PR, merge and release are left"), "stopped"),
        (codex_reply("DONE", "fixed runs never starting on branch fix-runs; it needs the caller to commit, merge and release it"), "done"),
    ],
)
def test_an_incident_codex_fix_jobs_return_with_its_fix_not_live_adds_the_claude_builder_that_ships_it_named_for_the_coordinator_to_start_that_turn(
    demo: str, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk, reply: str, ended: str
) -> None:
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    incident_codex_running(demo, codex_ask.run("completed", reply), state.INCIDENT_FIX)
    lines = staged(watch.agent_lines, watcher(demo), state.now())
    data = state.load(demo)
    written = state.drive_dir(demo) / "jobs" / "j2.brief.md"
    assert lines == [
        f'JOB j1 o1 {data["jobs"]["j1"]["said"]}; its fix is not live, so j2, an Opus 5.5 long-running:builder the drive added on "fix" to ship it:'
        f' start its agent now, run_in_background, on the prompt "Read your brief first: {written}"'
    ]
    assert (data["jobs"]["j1"]["state"], data["orders"]["o1"]["jobs"]) == (ended, ["j1", "j2"])
    assert (data["jobs"]["j2"]["kind"], data["jobs"]["j2"]["parts"], data["jobs"]["j2"]["brief"]) == ("builder", ["fix"], str(written))
    assert "Handoff: drive job report j1, the codex fix job whose work this builder ships, on its brief /briefs/j1.md\n" in written.read_text()


def test_an_incident_codex_jobs_return_on_a_part_other_than_its_fix_adds_no_builder(demo: str, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk) -> None:
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    incident_codex_running(demo, codex_ask.run("completed", codex_reply("HANDOFF", "the first check is left on branch first-check")), "first check")
    lines = staged(watch.agent_lines, watcher(demo), state.now())
    data = state.load(demo)
    assert (lines, list(data["jobs"])) == ([f"JOB j1 o1 {data['jobs']['j1']['said']}"], ["j1"])


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


def test_a_local_agents_bash_call_still_running_at_nine_minutes_is_said_nothing(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    serve(monkeypatch, {})
    asked = state.now()
    call = {"type": "tool_use", "id": "toolu_waiting", "name": "Bash", "input": {"command": "zsh /scratch/j1/wait.sh", "timeout": 570000}}
    write_transcript(tmp_path / "config", [{"type": "assistant", "timestamp": state.stamp(asked), "message": {"stop_reason": "tool_use", "content": [call]}}], timedelta(0))
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(session="session-under-test", agent="a1")
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
    [line] = watch.run_pass(watcher(demo), state.now())
    assert (line.startswith(UNRECORDED), line.endswith(REPORT_IT + WOKEN_BY_ITS_SESSION)) == (True, True)
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


def test_an_uncarded_pr_carrying_the_hold_label_reads_held_by_him_never_uncarded_so_the_watch_owes_no_draw_his_card_refuses(demo: str) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"wants_tap": True, "hold_label": True}
    now = stage.of(state.load(demo), KEY, NO_CARDS)
    assert (now.name, now.owner, state.asks_tap(state.load(demo)["prs"][KEY])) == ("held", stage.HIM, False)


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
    stage.shown(data, NO_CARDS, [KEY])
    assert (data["prs"][KEY]["stage"]["name"], data["prs"][KEY]["stage"]["since"]) == ("grading", state.stamp(T0))
    data["prs"][KEY]["checks"] = "fail"
    stage.shown(data, NO_CARDS, [KEY])
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
    return {"question": f"Pick {waits}?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(T0), "url": f"{CARD}?thread_ts=1.2", "card": {"title": f"Pick {waits}?", "body": "", "options": ["Yes", "No"], "recommended": "Yes", "waits": waits}}


def asking(monkeypatch: pytest.MonkeyPatch, name: str, *waits: str, taps: list[dict] = ()) -> None:
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question(waits) for waits in waits], "closed": [], "pr_cards": list(taps)})
    watch.view.refresh_cards(name)


def test_a_pr_his_open_cards_hold_is_held_by_him_its_card_redrawn_as_each_closes_until_he_answers_the_last(
    demo: str, monkeypatch: pytest.MonkeyPatch, redraws: Redraws
) -> None:
    waiting_job(demo)
    asking(monkeypatch, demo, "demo:j1-7-a", "demo:j1-7-b", taps=[pr_card(KEY)])
    with state.edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
        data["jobs"]["j1"]["questions"] = [state.new_question(None, f"Pick {waits}?", state.stamp(T0), holds=None) | {"waits": waits} for waits in ("demo:j1-7-a", "demo:j1-7-b")]
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == []
    assert (state.load(demo)["prs"][KEY]["stage"]["why"], redraws.asked) == ("his cards demo:j1-7-a, demo:j1-7-b on its work are open", [state.pr_url(KEY)])
    for answered, (kept, why) in enumerate((("demo:j1-7-b", "his cards demo:j1-7-b on its work are open"), (None, "green, waits on his Approve"))):
        with state.edit(demo) as data:
            data["jobs"]["j1"]["questions"][answered]["closed"] = state.new_close("answered", "Yes", None, seq=str(9 + answered))
        asking(monkeypatch, demo, *filter(None, [kept]), taps=[pr_card(KEY)])
        assert watch.run_pass(watching, T0 + timedelta(minutes=31 + answered)) == []
        assert (state.load(demo)["prs"][KEY]["stage"]["why"], redraws.asked) == (why, [state.pr_url(KEY)] * (2 + answered))
    assert watch.run_pass(watching, T0 + timedelta(minutes=62)) == []


def test_a_pr_he_approved_that_waits_on_a_running_job_waits_on_it_and_is_freed_when_the_job_ends(demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j2"] = job(goal="real run")
    waits_on(demo, "j2")
    waiting_job(demo)
    with state.edit(demo) as data:
        data["prs"][KEY]["tap"] = {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack"}
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


def test_a_pr_he_approved_that_waits_on_a_part_of_an_order_lands_only_once_a_builders_done_delivers_it_never_on_a_done_at_its_merge(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    with state.edit(demo) as data:
        files_order(data)
    waits_on(demo, "o2:stack 1")
    waiting_job(demo)
    with state.edit(demo) as data:
        data["prs"][KEY]["tap"] = {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack"}
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    shown = state.load(demo)["prs"][KEY]["stage"]
    assert (shown["name"], shown["why"], landers) == ("waiting", "it waits on o2:stack 1, and the watch frees it once each ends", [])
    with state.edit(demo) as data:
        state.take_result(data, "j2", f"DONE {state.pr_url(OTHER)} merged: {OTHER} at ccc333ddd444")
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=30)) if line.startswith("FREE ")]
    assert state.load(demo)["prs"][KEY]["waits"] == ["o2:stack 1"]
    with state.edit(demo) as data:
        state.take_result(data, "j2", "DONE stack 1 ran on every cluster")
    assert f"FREE {KEY} j1 o1: o2:stack 1 answered or done, nothing else holds it" in watch.run_pass(watching, T0 + timedelta(minutes=60))
    assert landers == [(KEY,)]


def test_a_job_whose_wait_ends_is_freed_before_any_orphan_and_said_to_be_stranded_once_a_change_to_it_settles(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    said = "WAITING release: #1 merged abc; rides the next release"
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j2"] = job(goal="release the service")
        data["jobs"]["j1"].update(state="waiting", said=said, waits=["j2"], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    with state.edit(demo) as data:
        data["jobs"]["j2"]["state"] = "done"
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    assert watch.run_pass(watching, T0 + timedelta(seconds=60)) == ["FREE j1 o1: j2 answered or done, nothing else holds it"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=4)) == []
    with state.edit(demo) as data:
        data["jobs"]["j1"]["updated"] = state.stamp(T0 + timedelta(minutes=10))
    assert watch.run_pass(watching, T0 + timedelta(minutes=11)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=13)) == [
        f'ORPHAN o1 "ship the watch": j1 waits on "{said}" and no open PR, card or due time can wake it'
    ]
    assert watch.run_pass(watching, T0 + timedelta(minutes=31)) == []


def completed_run(folder: Path, reply: str) -> codex.Run:
    folder.mkdir(parents=True)
    (folder / "codex-r-1").write_text(reply)
    return codex.Run("completed", folder / "codex-r-1", folder / "codex-q-1.log")


def test_a_job_waiting_on_a_codex_job_holds_through_its_handoff_a_pointer_reported_on_it_and_a_stop_until_the_work_it_was_given_is_delivered(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    handoff = "HANDOFF j3 is unfinished with a stopped implementation pending a scope decision, and no PR or release exists."
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["orders"]["o2"] = order(text="build the page kit", parts={"page kit": state.new_part()}, jobs=["j2", "j3"])
        data["jobs"]["j2"] = job(order="o2", kind="codex", goal="build the page kit", parts=["page kit"], state="stopped", said="handed to j3")
        data["jobs"]["j3"] = job(order="o2", kind="codex", goal="build the page kit", parts=["page kit"])
        data["jobs"]["j1"].update(state="waiting", said="waits on j3", waits=["j3"], updated=state.stamp(T0 - timedelta(minutes=10)))
        data["jobs"]["j4"] = job(goal="build on the page kit", state="waiting", said="waits on j2", waits=["j2"])
        data["orders"]["o1"]["jobs"].append("j4")
    with state.edit(demo) as data:
        codex.take(demo, data, "j3", completed_run(tmp_path / "codex-ask.1", codex_reply("HANDOFF", handoff.removeprefix("HANDOFF "))))
    data = state.load(demo)
    assert (data["jobs"]["j3"]["state"], data["jobs"]["j3"]["said"]) == ("stopped", handoff)
    pointer = "REPLY_FILE: /runs/codex-ask.1/codex-r-1"
    assert state.result_refusal(data, "j3", pointer, f"{pointer}\n").startswith("j3 is stopped, so its work goes on in another job")
    watching = watcher(demo)
    for minute in (0, 2, 4):
        assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=minute)) if line.startswith("FREE ")]
    with state.edit(demo) as data:
        state.start(data["jobs"]["j3"])
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=6)) if line.startswith("FREE ")]
    assert [state.load(demo)["jobs"][job_id]["waits"] for job_id in ("j1", "j4")] == [["j3"], ["j2"]]
    with state.edit(demo) as data:
        codex.take(demo, data, "j3", completed_run(tmp_path / "codex-ask.2", codex_reply("DONE", "built the page kit")))
    assert [line for line in watch.run_pass(watching, T0 + timedelta(minutes=8)) if line.startswith("FREE ")] == [
        "FREE j1 o1: j3 answered or done, nothing else holds it",
        "FREE j4 o1: j2 answered or done, nothing else holds it",
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
    watching = watcher(demo)
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


TENANT_API = {"component": "api", "env": "tnt-usw2-0ddq7rb"}
PROD_SANDSQL = {"component": "sandsql", "env": "plat-usw2-prod"}


def production_reads(monkeypatch: pytest.MonkeyPatch, behind: dict[str, list[dict]]) -> list[list[str]]:
    calls = []

    def run(argv: list[str], checkout: Path, env: dict | None = None, stdin: str | None = None) -> str:
        calls.append(argv[3:-1])
        return json.dumps([{"number": int(number), "liveInProduction": not behind[number], "productionBehind": behind[number]} for number in argv[3:-1]])

    monkeypatch.setattr(watch.reads, "run", run)
    monkeypatch.setattr(watch.orca, "checkout", lambda name, clone: DRIVE_CHECKOUT)
    return calls


def test_a_release_wait_frees_once_no_production_cluster_of_its_service_is_behind_its_pr_and_holds_again_while_its_job_runs_once_one_goes_behind(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    behind = {"42": [TENANT_API, PROD_SANDSQL]}
    calls = production_reads(monkeypatch, behind)
    wait = f"release:api:{KEY}"
    freed = f"FREE j1 o1: every production cluster of api serves {KEY} with no release running there, nothing else holds it"
    with state.edit(demo) as data:
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["jobs"]["j1"].update(state="waiting", said="WAITING release of api", waits=[wait], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    behind["42"] = [PROD_SANDSQL]
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == [freed]
    assert [state.load(demo)["jobs"]["j1"][field] for field in ("waits", "shipped")] == [[], [wait]]
    behind["42"] = [TENANT_API]
    assert watch.run_pass(watching, T0 + timedelta(minutes=2)) == [f"REWAIT j1 o1: {wait} holds again: release pull reads tnt-usw2-0ddq7rb behind it"]
    assert [state.load(demo)["jobs"]["j1"][field] for field in ("waits", "shipped")] == [[wait], []]
    behind["42"] = []
    assert watch.run_pass(watching, T0 + timedelta(minutes=3)) == [freed]
    with state.edit(demo) as data:
        data["jobs"]["j1"]["state"] = "done"
    behind["42"] = [TENANT_API]
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=4)) if line.startswith(("REWAIT", "FREE"))]
    assert (calls, state.load(demo)["jobs"]["j1"]["waits"]) == ([["42"]] * 4, [])


def test_a_release_wait_on_a_pr_outside_the_monorepo_is_never_read_and_holds(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    calls = production_reads(monkeypatch, {})
    waited = "yasyf/cc-slack#355"
    with state.edit(demo) as data:
        data["prs"][waited] = state.new_pr(state.pr_url(waited), "j1", "o1") | {"state": "merged", "merged_sha": "c355"}
        data["jobs"]["j1"].update(state="waiting", said="WAITING release of cc-slack", waits=[f"release:cc-slack:{waited}"], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert [watch.run_pass(watching, T0 + timedelta(minutes=minute)) for minute in range(3)] == [[], [], []]
    assert (calls, state.load(demo)["jobs"]["j1"]["waits"]) == ([], [f"release:cc-slack:{waited}"])


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
    iris = watch.Watcher(demo, offset=state.events_end(demo), project="Iris")
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
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("answered", "Yes", asked, seq="9")
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


def test_a_card_a_built_job_names_by_its_link_and_by_its_waits_id_is_one_wait_and_one_reply(demo: str, monkeypatch: pytest.MonkeyPatch, told: list[dict]) -> None:
    serve(monkeypatch, {})
    asking(monkeypatch, demo, "demo:j2-7")
    link = f"{CARD}?thread_ts=1.2"
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["orders"]["o1"] |= {"jobs": ["j1", "j2"], "parts": {"ship the watch": state.new_part()}}
        data["jobs"]["j1"] |= {"parts": ["ship the watch"], "researched": ["j2"]}
        asked = state.new_question("/asks/j2.md", "Pick?", state.stamp(T0), card=link, holds=None) | {"waits": "demo:j2-7"}
        data["jobs"]["j2"] = job(kind="reader", state="waiting", parts=["ship the watch"], questions=[asked])
        state.take_result(data, "j1", "BUILT lane/built, waits on c096bde8")
    assert state.load(demo)["jobs"]["j1"]["waits"] == ["demo:j2-7"]
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == waiting("demo:j2-7")
    for named in ("demo:j2-7", link):
        assert cli.main(["job", "set", "j1", "--waits", named, "--drive", demo]) == 0
    assert state.load(demo)["jobs"]["j1"]["waits"] == ["demo:j2-7"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []
    assert told == [paused()]


def test_a_built_job_holding_one_card_under_its_link_and_its_waits_id_is_told_once_whichever_name_it_was_told_under(
    demo: str, monkeypatch: pytest.MonkeyPatch, told: list[dict]
) -> None:
    serve(monkeypatch, {})
    asking(monkeypatch, demo, "demo:j1-7-a")
    link = f"{CARD}?thread_ts=1.2"
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"] |= {"state": "built", "branch": "lane/built", "waits": [link, "demo:j1-7-a"]}
    assert watch.run_pass(watcher(demo), T0) == waiting("demo:j1-7-a")
    with state.edit(demo) as data:
        data["jobs"]["j1"]["waiting_said"] = [link]
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=1)) == []
    assert told == [paused()]


def test_pr_set_waits_adds_a_job_refuses_his_card_naming_the_holds_line_and_anything_else_and_no_waits_clears_them(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["pr", "set", KEY, "--waits", "j9", "--drive", demo]) == 2
    assert "--waits j9" in capsys.readouterr().err
    assert cli.main(["pr", "set", KEY, "--waits", "j1", "--drive", demo]) == 0
    assert ", waits on j1; " in capsys.readouterr().out
    asking(monkeypatch, demo, "demo:j1-7-a")
    assert cli.main(["pr", "set", KEY, "--waits", "demo:j1-7-a", "--waits", CARD, "--drive", demo]) == 2
    assert f"--waits demo:j1-7-a, {CARD}: refused, nothing set: a card of his holds a PR from landing only by its question's Holds: line" in capsys.readouterr().err
    assert state.load(demo)["prs"][KEY]["waits"] == ["j1"]
    assert cli.main(["pr", "set", KEY, "--no-waits", "--drive", demo]) == 0
    assert state.load(demo)["prs"][KEY]["waits"] == []


def test_a_wait_added_to_a_pr_redraws_his_open_card_in_place_never_withdraws_it_and_a_fresh_tap_draws_its_card_while_it_holds(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], withdrawals: Withdrawals, redraws: Redraws
) -> None:
    asking(monkeypatch, demo, "demo:j1-7-a")
    assert cli.main(["pr", "set", KEY, "--waits", "j1", "--drive", demo]) == 0
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


def test_a_prs_release_wait_whose_release_is_already_out_is_stored_and_freed_on_the_next_read(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    shipped = "Forge-AI/monorepo#43"
    with state.edit(demo) as data:
        data["prs"][shipped] = state.new_pr(state.pr_url(shipped), "j1", "o1") | {"state": "merged", "merged_sha": "abc123"}
    production_reads(monkeypatch, {"43": [{"component": "api", "env": "plat-usw2-prod"}]})
    assert cli.main(["pr", "set", KEY, "--waits", f"release:dashboard:{shipped}", "--waits", f"release:api:{shipped}", "--drive", demo]) == 0
    assert state.load(demo)["prs"][KEY]["waits"] == [f"release:dashboard:{shipped}", f"release:api:{shipped}"]
    watching = watcher(demo)
    assert (watch.production_lines(watching, T0), watch.waits_lines(watching, T0)) == ([], [])
    assert state.load(demo)["prs"][KEY]["waits"] == [f"release:api:{shipped}"]


def test_job_held_names_his_open_cards_holding_a_jobs_pr_until_he_answers_or_it_has_an_open_pr(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asking(monkeypatch, demo, "demo:j1-7-a")
    with state.edit(demo) as data:
        data["jobs"]["j1"]["questions"] = [state.new_question(None, "Pick demo:j1-7-a?", state.stamp(T0), holds=None) | {"waits": "demo:j1-7-a"}]
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
    assert cli.main(["pr", "add", state.pr_url(KEY), "--job", "j1", "--drive", demo, *LEAD]) == 0
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
    assert cli.main(["pr", "add", url, "--job", "j1", "--drive", demo, *LEAD]) == 0
    assert cli.main(["pr", "set", url, "--waits", "demo:j1-7-a", "--drive", demo]) == 2
    assert "--waits demo:j1-7-a: refused, nothing set: a card of his holds a PR from landing only by its question's Holds: line" in capsys.readouterr().err
    assert cli.main(["pr", "set", url, "--waits", "j1", "--drive", demo]) == 0


def test_a_job_on_an_open_incident_is_held_from_opening_its_pr_only_by_a_card_asking_him_to_lift_a_ruling_of_his(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asking(monkeypatch, demo, "demo:j1-7-a")
    with state.edit(demo) as data:
        data["orders"]["o1"]["incident"] = True
        state.take_result(data, "j1", "BUILT lane/built, waits on demo:j1-7-a")
        data["prs"].pop(KEY, None)
        data["jobs"]["j1"]["prs"] = []
        data["jobs"]["j1"]["questions"] = [state.new_question(None, "Pick demo:j1-7-a?", state.stamp(T0), holds=None) | {"waits": "demo:j1-7-a"}]
    capsys.readouterr()
    assert cli.main(["job", "held", "j1", "--drive", demo]) == 0
    assert capsys.readouterr().out == "j1: no open card of his holds its PR\n"
    with state.edit(demo) as data:
        data["jobs"]["j1"]["questions"][0]["unsettled"] = "9992100: this card asks whether the incident overrides his wait"
    assert cli.main(["job", "held", "j1", "--drive", demo]) == 0
    assert capsys.readouterr().out == "demo:j1-7-a\n"


def test_job_set_moves_a_builder_off_a_card_his_words_replaced_onto_a_reader_that_just_started_and_refuses_only_a_finished_job(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asking(monkeypatch, demo, "demo:j1-7-a")
    with state.edit(demo) as data:
        data["jobs"]["j2"] = state.new_job("o1", "builder", "build his pick on the card", [], state.stamp(T0)) | {"waits": ["demo:j1-7-a"]}
        state.hold_unstarted(data["jobs"]["j2"])
        data["jobs"]["j3"] = job(kind="reader", goal="research: the audit his later words asked for", agent="a1")
        data["jobs"]["j4"] = job(kind="design", goal="asked him", state="waiting", questions=[state.new_question(None, "Pick?", state.stamp(T0)) | {"waits": "demo:j1-7-a"}])
        data["jobs"]["j5"] = job(kind="reader", goal="research: done", state="done")
    assert (state.load(demo)["jobs"]["j2"]["state"], state.load(demo)["jobs"]["j2"]["said"]) == ("waiting", "waits on demo:j1-7-a")
    assert cli.main(["job", "set", "j2", "--waits", "j3", "--drive", demo]) == 0
    added = state.load(demo)["jobs"]["j2"]
    assert (added["waits"], added["said"]) == (["demo:j1-7-a", "j3"], "waits on demo:j1-7-a, j3")
    assert cli.main(["job", "set", "j2", "--no-waits", "--waits", "j3", "--drive", demo]) == 0
    moved = state.load(demo)["jobs"]["j2"]
    assert (moved["state"], moved["waits"], moved["said"]) == ("waiting", ["j3"], "waits on j3")
    assert cli.main(["job", "set", "j2", "--waits", "j4", "--drive", demo]) == 0
    assert state.load(demo)["jobs"]["j2"]["waits"] == ["j3", "j4"]
    assert cli.main(["job", "set", "j2", "--waits", "j5", "--drive", demo]) == 2
    assert capsys.readouterr().err.strip().endswith("--waits j5: j5 is done, so nothing of it is left to wait on")
    assert cli.main(["job", "set", "j2", "--waits", "j1", "--drive", demo]) == 0
    assert state.load(demo)["jobs"]["j2"]["waits"] == ["j3", "j4", KEY]


def test_a_pr_whose_head_moved_to_a_change_nobody_judged_gets_its_card_and_its_check_against_his_words_at_that_head_whatever_its_jobs_state(
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str], draws: Draws
) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"wants_tap": True, "checked": CHECKED | {"lines": {"api/run.ts": "a change judged before"}}}
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert (draws.keys, checkers) == ([[KEY]], [KEY])
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=30)) if line.startswith(("PR ", "STAGE "))]
    assert (state.load(demo)["prs"][KEY]["checked"]["read"], state.load(demo)["prs"][KEY]["stage"]["name"]) == ("bbbbbbb222", "card")
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
    data = state.load(demo)
    assert (draws.keys, view.live_cards(demo)["pr_cards"], stage.of(data, KEY, view.live_cards(demo)).name) == ([[KEY]] * 3, [], "uncarded")
    assert [entry for entry in posted(sent) if entry[1] == f"meta@{demo}"] == [
        ("handoff", f"meta@{demo}", f"WATCH draw of his card on {KEY} failed 3 times in a row: cc-slack exited 1")
    ]
    draws.failure = None
    watch.run_pass(watching, T0 + timedelta(minutes=3))
    watch.run_pass(watching, T0 + timedelta(minutes=4))
    pr = state.load(demo)["prs"][KEY]
    assert (len(draws.keys), view.tap_card(pr, view.live_cards(demo)), pr["stage"]["name"]) == (4, draws.link, "card")


def test_a_pr_whose_card_draw_goes_out_with_no_open_card_at_its_head_is_drawn_once_while_it_stays_uncarded(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    drawn: list[list[str]] = []
    monkeypatch.setattr(talk, "draw", lambda name, keys, again=False: drawn.append(keys) or "https://forge.slack.com/archives/D123/p1794379200123456")
    watching = watcher(demo)
    for minute in range(3):
        watch.run_pass(watching, T0 + timedelta(minutes=minute))
    assert (drawn, state.load(demo)["prs"][KEY]["stage"]["name"]) == ([[KEY]], "uncarded")


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
    judge.against = [{"order": "o1", "words": "ship the watch", "file": "watch.py", "change": "watch.py stops the watch"}]
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
    assert not watch.wakes(checked, state.load(demo), NO_CARDS)
    assert (state.load(demo)["prs"][KEY]["checked"]["head"], watch.run_pass(watching, T0 + timedelta(minutes=7)), checkers) == ("aaaaaaa111", [], [KEY, KEY])


def test_a_not_ready_on_a_pr_he_approved_is_drawn_on_his_card_and_wakes_no_builder(
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str], judge: Judge, woken: list[tuple[str, str]]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(state, "now", lambda: T0 + timedelta(minutes=1))
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"PUSHED {state.pr_url(KEY)}", returned=state.stamp(T0))
        data["prs"][KEY] |= {"wants_tap": True, "checked": None, "tap": state.new_tap("Approve", "aaaaaaa111", "pressed the button", "1.2")}
    watching = watcher(demo)
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    judge.against = [{"order": "o1", "words": "ship the watch", "file": "watch.py", "change": "watch.py stops the watch"}]
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    watch.run_pass(watching, T0 + timedelta(minutes=2))
    assert (woken, state.against(state.load(demo)["prs"][KEY])) == ([], 'o1 "ship the watch": watch.py stops the watch')


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
    judge.against = [{"order": "o1", "words": "ship the watch", "file": "watch.py", "change": "watch.py stops the watch"}]
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


def test_a_not_ready_a_return_of_its_builder_judges_again_at_the_same_head_wakes_it_once_and_its_card_shows_the_newest_objection(
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str], judge: Judge, draws: Draws, woken: list[tuple[str, str]]
) -> None:
    reads = {KEY: read(checks=GREEN, reviewDecision="APPROVED")}
    serve(monkeypatch, reads)
    monkeypatch.setattr(state, "now", lambda: T0)
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"PUSHED {state.pr_url(KEY)}", returned=state.stamp(T0))
        data["prs"][KEY] |= {"wants_tap": True, "checked": None}
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    judge.against = [{"order": "o1", "words": "ship the watch", "file": "watch.py", "change": "watch.py stops the watch"}]
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    with state.edit(demo) as data:
        data["jobs"]["j1"]["returned"] = state.stamp(T0 + timedelta(minutes=2))
    monkeypatch.setattr(state, "now", lambda: T0 + timedelta(minutes=2))
    watch.run_pass(watching, T0 + timedelta(minutes=2))
    judge.against = [{"order": "o1", "words": "ship the watch", "file": "watch.py", "change": "watch.py ends the watch it should keep"}]
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    watch.run_pass(watching, T0 + timedelta(minutes=3))
    refused = [text for _, text in woken if f"PR {KEY}: NOT READY: " in text]
    assert (checkers, len(refused), "watch.py stops the watch" in refused[0]) == ([KEY, KEY], 1, True)
    assert state.against(state.load(demo)["prs"][KEY]) == 'o1 "ship the watch": watch.py ends the watch it should keep'
    reads[KEY] = read(headRefOid="bbbbbbb222", checks=GREEN, reviewDecision="APPROVED")
    watch.run_pass(watching, T0 + timedelta(minutes=4))
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    watch.run_pass(watching, T0 + timedelta(minutes=5))
    assert (checkers, len([text for _, text in woken if f"PR {KEY}: NOT READY: " in text])) == ([KEY] * 3, 2)


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
    wasm = {"order": "o1", "words": "ship the watch", "file": "", "change": "no PR gives Sprites the wasm toolchain"}
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
        data["prs"][KEY] |= {"wants_tap": True, "checked": None, "port_of": 50974, "tap": {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack"}}
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
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: sent.append((lane, records)) or ["9"])
    watching = watcher(demo)
    lines = watch.run_pass(watching, T0)
    assert lines == [f"PR {KEY}: checks passed [o1 j1]", f"PR {KEY}: approved [o1 j1]"]
    assert not any(watch.wakes(line, state.load(demo), NO_CARDS) for line in lines)
    assert (draws.keys, sent) == ([[KEY]], [])
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []
    assert (draws.keys, view.tap_card(state.load(demo)["prs"][KEY], view.live_cards(demo))) == ([[KEY]], draws.link)


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


def test_a_live_bugs_stack_waiting_on_his_card_is_asked_again_every_30_minutes_once_for_its_card(demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws) -> None:
    three_stacked(demo, monkeypatch)
    with state.edit(demo) as data:
        for order_id in ("o1", "o2"):
            data["orders"][order_id]["reporters"] = [state.reporter(REPORTED)]
    watching = watcher(demo)
    for minutes in (0, 10, 39, 40, 69, 70):
        watch.run_pass(watching, T0 + timedelta(minutes=minutes))
    assert (draws.keys, draws.again) == ([list(STACKED)] * 3, [False, True, True])


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
        data["prs"][KEY]["wants_tap"] = True
        state.rework(data["prs"][KEY], "o2", "Topology goes in the meta table")
    shown = [pr_card(KEY)]
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [], "closed": [], "pr_cards": list(shown)})
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    withdrawals.cards = [CARD]
    watch.run_pass(watcher(demo), T0)
    shown.clear()
    reason = 'Being reworked to your words "Topology goes in the meta table". A fresh card asks your tap once its builder says how they are met'
    pr = state.load(demo)["prs"][KEY]
    assert (withdrawals.asked, pr["stage"]["name"], [key for key, _ in woken], draws.keys) == ([(state.pr_url(KEY), reason)], "rework", [KEY], [])
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
    assert watch.wakes(line, state.load(demo), NO_CARDS)
    assert line not in watch.run_pass(watching, T0 + timedelta(minutes=29))
    assert line in watch.run_pass(watching, T0 + timedelta(minutes=30))
    assert "checks pass, review approved, no tap needed, exempt: incident fix; unlanded since " in render.pr_line(KEY, state.load(demo)["prs"][KEY], None)
    assert landers == []


@pytest.mark.parametrize(
    ("exempt", "path", "refused", "remedy"),
    [
        ("ci-only", "api/src/index.ts", 'is no "ci-only"', ["--no-exempt", "--tap"]),
        ("flake fix", "go/sand/next/internal/vm_run/vmruntest/vmruntest.go", 'is a "flake fix", which lands with no tap only in test or fixture code', ["--tap"]),
    ],
)
def test_a_try_its_files_put_outside_its_exempt_class_stops_standing_once_drive_pr_set_puts_it_on_his_card_and_his_approve_lands_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], exempt: str, path: str, refused: str, remedy: list[str]
) -> None:
    waiting_job(demo)
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"exempt": exempt, "wants_tap": False, "checks": "pass", "review": "approved"}
    diff = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+b\n"
    monkeypatch.setattr(land.served, "change", lambda key: {"title": "Fix: pin the release image digest", "body": "", "head": "aaaaaaa111", "diff": diff.encode()})
    assert land.run(demo, [KEY]).startswith(f"not landed: {KEY} {refused}")
    assert cli.main(["pr", "set", KEY, *remedy, "--drive", demo]) == 0
    assert state.load(demo)["prs"][KEY]["exempt"] == (None if "--no-exempt" in remedy else exempt)
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [], "closed": [], "pr_cards": [pr_card(KEY)]})
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    for moment in (T0, T0 + timedelta(minutes=30)):
        assert not [line for line in watch.run_pass(watching, moment) if line.startswith("STAGE")]
        assert (now := stage.of(state.load(demo), KEY, watching.cards)).name == "card" and now.owner == stage.HIM
    with state.edit(demo) as data:
        pr = data["prs"][KEY]
        pr["tap"] = state.new_tap("Approve", pr["head"], "pressed the button", at=state.stamp(state.parse_time(pr["lander"]["at"]) + timedelta(minutes=1)))
    assert stage.of(state.load(demo), KEY, NO_CARDS).name == "ready"
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    assert landers == [(KEY,)]


def test_a_ci_only_pr_changing_go_ci_the_infra_ci_pipeline_definitions_and_the_tools_ci_shims_is_in_its_class(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = [
        "go/ci/cmd/devbox-broker/main_test.go",
        "go/ci/image.go",
        "go/ci/internal/cache/tree.go",
        "go/ci/internal/contract/builds.go",
        "go/ci/internal/release/images/images.go",
        "go/ci/internal/release/images/images_test.go",
        "infra/ci/src/pipelines/test/images.ts",
        "infra/ci/test/image-builds.test.ts",
        "tools/ci",
        "tools/ci-ts",
    ]
    with state.edit(demo) as data:
        data["prs"][KEY]["exempt"] = "ci-only"
    diff = "".join(f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+b\n" for path in paths)
    monkeypatch.setattr(land.served, "change", lambda key: {"title": "Fix: build CI images from one contract", "body": "", "head": "aaaaaaa111", "diff": diff.encode()})
    assert land.unexempt(state.load(demo), KEY) is None

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
    assert watch.wakes(freed, state.load(demo), NO_CARDS)


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
        data["prs"][KEY]["tap"] = None
    monkeypatch.setattr(watch.view, "cards", lambda drive: NO_CARDS | {"pr_cards": [pr_card(KEY)]})
    watch.run_pass(watching, T0 + timedelta(minutes=31))
    assert (state.load(demo)["prs"][KEY]["stage"]["name"], [key for key, _ in woken]) == ("red", [KEY])


def test_a_red_pr_his_card_holds_wakes_its_builder_at_once_and_every_thirty_minutes_while_his_card_stays_up(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    waiting_job(demo)
    asking(monkeypatch, demo, HOLDING_CARD, taps=[pr_card(KEY)])
    holding_job(demo, HOLDING_CARD)
    holding_asks(demo, BUSY_WAIT)
    with state.edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
    serve(monkeypatch, {KEY: read(checks=RED)})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    watch.run_pass(watching, T0 + timedelta(minutes=30))
    kept = state.load(demo)["prs"][KEY]["stage"]
    assert (kept["name"], kept["owner"], [key for key, _ in woken]) == ("red", stage.BUILDER, [KEY, KEY])
    assert stage.held(state.load(demo), state.load(demo)["prs"][KEY])


def his_card_row(name: str, key: str) -> dict:
    entry = state.load(name)["prs"][key]
    known = {key: (name, entry)}
    shown = {"title": entry["title"], "body": "", "comments": [], "state": "OPEN", "isDraft": False, "headRefOid": entry["head"], "labels": [], "url": entry["url"], "reviewDecision": "REVIEW_REQUIRED"}
    return card.render(shown, entry | {"now": card.standing(known, key)}, (), tuple(card.holding(*known[key]).values()))["prs"][0]


def test_replayed_55175_a_red_pr_whose_builder_waits_on_a_fix_that_waits_on_his_tap_names_and_links_that_fix_and_its_card_on_his_card(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], redraws: Redraws
) -> None:
    red_pr, fix, fix_card = "Forge-AI/monorepo#55175", "Forge-AI/monorepo#55582", "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794549855666519"
    titles = {red_pr: "New: the change dev left red", fix: "Fix: the flaky test dev broke"}
    heads = {red_pr: "75e6d42aaa", fix: "55582aaaaa"}
    with state.edit(demo) as data:
        data["orders"] |= {"o688": order(jobs=["j2661"]), "o689": order(jobs=["j2662"])}
        data["jobs"] |= {"j2661": job(order="o688", prs=[state.pr_url(red_pr)]), "j2662": job(order="o689", prs=[state.pr_url(fix)], state="waiting")}
        for key, job_id in ((red_pr, "j2661"), (fix, "j2662")):
            order_id = data["jobs"][job_id]["order"]
            data["prs"][key] = state.new_pr(state.pr_url(key), job_id, order_id) | {
                "head": heads[key],
                "title": titles[key],
                "wants_tap": True,
                "checked": CHECKED | {"head": heads[key], "orders": [order_id]},
            }
    taps = [pr_card(red_pr, head=heads[red_pr]), pr_card(fix, url=fix_card, head=heads[fix])]
    monkeypatch.setattr(watch.view, "cards", lambda drive: NO_CARDS | {"pr_cards": taps})
    reads = {
        red_pr: read(headRefOid=heads[red_pr], title=titles[red_pr], checks=[{"name": "buildkite/test", "conclusion": "FAILURE"}]),
        fix: read(headRefOid=heads[fix], title=titles[fix], checks=GREEN, reviewDecision="APPROVED"),
    }
    serve(monkeypatch, reads)
    passes = []

    def at(minutes: int) -> None:
        drawn = redraws.asked.count(state.pr_url(red_pr))
        watch.run_pass(watcher(demo), T0 + timedelta(minutes=minutes))
        kept = state.load(demo)["prs"][red_pr]["stage"]
        passes.append((minutes, kept["name"], kept["owner"], [key for key, _ in woken], redraws.asked.count(state.pr_url(red_pr)) > drawn))
        woken.clear()

    at(0)
    with state.edit(demo) as data:
        state.take_result(data, "j2661", f"WAITING #55175 waits on {state.pr_url(fix)} to merge")
    at(16)
    fix_link = view.pr_link(state.load(demo)["prs"][fix])
    asked_why = f"its checks fail at 75e6d42: buildkite/test, and its builder waits on {fix_link}, which waits on his Approve on [his card]({fix_card})"
    assert state.load(demo)["prs"][red_pr]["stage"]["why"] == asked_why
    lands = f"{fix_link} lands, which waits on your Approve on [its card]({fix_card})"
    assert his_card_row(demo, red_pr)["lands"] == f"you approve, CI passes and {lands}"
    for minutes in (46, 76, 106):
        at(minutes)
    approve(demo, red_pr)
    assert his_card_row(demo, red_pr)["blocks"] == f"Approved by you. CI failed; it lands once {lands}"
    approve(demo, fix)
    at(144)
    assert his_card_row(demo, red_pr)["blocks"] == f"Approved by you. CI failed; it lands once {fix_link} lands"
    reads[fix] = read(state="MERGED", mergeCommit="ddd444", headRefOid=heads[fix], title=titles[fix], checks=GREEN, reviewDecision="APPROVED")
    at(150)
    assert passes == [
        (0, "red", stage.BUILDER, [red_pr], True),
        (16, "blocked", stage.HIM, [], True),
        (46, "blocked", stage.HIM, [], False),
        (76, "blocked", stage.HIM, [], False),
        (106, "blocked", stage.HIM, [], False),
        (144, "blocked", stage.WAITED, [], True),
        (150, "red", stage.BUILDER, [red_pr], True),
    ]


def release_out(data: dict, release: str) -> None:
    data["jobs"]["j1180"]["waits"].remove(release)


@pytest.mark.parametrize(
    "end",
    [
        lambda reads, data, other, release: reads.update({other: read(state="MERGED", mergeCommit="ccc333", headRefOid="53810aaaaa")}),
        lambda reads, data, other, release: release_out(data, release),
    ],
)
def test_replayed_j1180_a_red_pr_whose_builder_waits_on_other_prs_and_releases_wakes_it_only_once_one_ends_or_its_red_changes(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], end: Callable[[dict, dict, str, str], None]
) -> None:
    own, other, shipped = ("Forge-AI/monorepo#54225", "Forge-AI/monorepo#54428"), "Forge-AI/monorepo#53810", "Forge-AI/monorepo#54237"
    release, failing = f"release:api:{shipped}", [{"name": "buildkite/test", "conclusion": "FAILURE"}]
    with state.edit(demo) as data:
        data["orders"] |= {"o448": order(jobs=["j1180"]), "o40": order(jobs=["j586"])}
        data["jobs"] |= {"j1180": job(order="o448", prs=[state.pr_url(key) for key in own]), "j586": job(order="o40", prs=[state.pr_url(other)])}
        for key, job_id, head in ((own[0], "j1180", "89c2464aaa"), (own[1], "j1180", "54428aaaaa"), (other, "j586", "53810aaaaa"), (shipped, "j1180", "54237aaaaa")):
            data["prs"][key] = state.new_pr(state.pr_url(key), job_id, data["jobs"][job_id]["order"]) | {"head": head, "wants_tap": False, "checked": CHECKED | {"head": head}}
        data["prs"][shipped]["state"] = "merged"
    reads = {
        own[0]: read(headRefOid="89c2464aaa", checks=failing),
        own[1]: read(headRefOid="54428aaaaa", checks=failing),
        other: read(headRefOid="53810aaaaa", checks=GREEN, reviewDecision="APPROVED"),
    }
    serve(monkeypatch, reads)
    waiting = f"WAITING readers-first on {own[0]} and {own[1]}: each lands once {other} lands and {release} is out"
    passes = []

    def at(minutes: int) -> None:
        watch.run_pass(watcher(demo), T0 + timedelta(minutes=minutes))
        passes.append((minutes, [key for key, _ in woken]))
        woken.clear()

    at(0)
    with state.edit(demo) as data:
        state.take_result(data, "j1180", waiting)
    for minutes in (30, 60, 90):
        at(minutes)
    reads[own[0]] = read(headRefOid="ce6d321aaa", checks=failing)
    at(91)
    with state.edit(demo) as data:
        state.take_result(data, "j1180", waiting)
    for minutes in (92, 122, 152):
        at(minutes)
    kept = state.load(demo)["prs"][own[0]]["stage"]
    with state.edit(demo) as data:
        end(reads, data, other, release)
    at(153)
    assert passes == [(0, list(own)), (30, []), (60, []), (90, []), (91, [own[0]]), (92, []), (122, []), (152, []), (153, list(own))]
    named = view.pr_link(state.load(demo)["prs"][other])
    assert (kept["name"], kept["owner"], kept["why"]) == ("blocked", stage.WAITED, f"its checks fail at ce6d321: buildkite/test, and its builder waits on {release}, {named}")
    assert [state.load(demo)["prs"][key]["stage"]["name"] for key in own] == ["red", "red"]


def test_a_red_prs_builder_whose_waiting_names_only_its_own_prs_is_woken_every_thirty_minutes(demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]) -> None:
    serve(monkeypatch, {KEY: read(checks=RED)})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    with state.edit(demo) as data:
        state.take_result(data, "j1", f"WAITING readers-first on {KEY}")
    watch.run_pass(watching, T0 + timedelta(minutes=30))
    assert (state.load(demo)["prs"][KEY]["stage"]["name"], len(woken)) == ("red", 2)


def test_replayed_55571_a_red_pr_whose_builder_waits_on_its_own_open_question_wakes_it_once_per_red_and_once_his_answer_closes_the_question(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    red_pr, card_link, waits = "Forge-AI/monorepo#55571", "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794577515737969", "demo:j2268-1794577515737"
    url = state.pr_url(red_pr)
    asked = state.new_question(None, "What writes a workflow run that has no agent?", state.stamp(T0), card=card_link, holds=url) | {"waits": waits}
    with state.edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j2268")
        data["jobs"]["j2268"] = job(prs=[url], state="waiting", said=asked["title"], questions=[asked])
        data["prs"][red_pr] = state.new_pr(url, "j2268", "o1") | {"head": "a2d5a98aaa", "wants_tap": True, "checked": CHECKED | {"head": "a2d5a98aaa"}}
    shown = {"head": "a2d5a98aaa"}
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question(waits) | {"url": card_link}], "closed": [], "pr_cards": [pr_card(red_pr, head=shown["head"])]})
    monkeypatch.setattr(watch.slack, "redraw_pr_cards", lambda pr: shown.update(head=state.load(demo)["prs"][red_pr]["head"]) or 1)
    reads: dict[str, dict] = {}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    waiting = f"WAITING {red_pr} waits on {card_link}"
    passes = []

    def at(minutes: int, **fetched: object) -> None:
        reads[red_pr] = read(**{"headRefOid": "a2d5a98aaa", "baseRefName": "dev", "mergeable": "CONFLICTING"} | fetched)
        watch.run_pass(watching, T0 + timedelta(minutes=minutes))
        kept = state.load(demo)["prs"][red_pr]["stage"]
        passes.append((minutes, kept["name"], kept["owner"], [key for key, _ in woken]))
        woken.clear()

    at(0)
    with state.edit(demo) as data:
        state.take_result(data, "j2268", waiting)
    at(2)
    blocked_why = state.load(demo)["prs"][red_pr]["stage"]["why"]
    at(4, mergeable="UNKNOWN")
    at(6)
    at(40)
    at(42, headRefOid="f222db8aaa", mergeable="UNKNOWN")
    at(44, headRefOid="f222db8aaa")
    at(46, headRefOid="f222db8aaa", checks=RED)
    with state.edit(demo) as data:
        state.take_result(data, "j2268", waiting)
    at(48, headRefOid="f222db8aaa", checks=RED)
    with state.edit(demo) as data:
        data["jobs"]["j2268"]["questions"][-1]["closed"] = state.new_close("answered", "option 1", None, seq="9")
    at(50, headRefOid="f222db8aaa", checks=RED)
    assert blocked_why == f"it conflicts with its base dev, and its builder waits on his answer on [his question card]({card_link})"
    assert passes == [
        (0, "red", stage.BUILDER, [red_pr]),
        (2, "blocked", stage.HIM, []),
        (4, "blocked", stage.HIM, []),
        (6, "blocked", stage.HIM, []),
        (40, "blocked", stage.HIM, []),
        (42, "uncarded", stage.WATCH, []),
        (44, "blocked", stage.HIM, []),
        (46, "red", stage.BUILDER, [red_pr]),
        (48, "blocked", stage.HIM, []),
        (50, "red", stage.BUILDER, [red_pr]),
    ]


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
        f'DUE o1 "ship" was due 10:00 AM: {watch.UNTHREADED}',
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


def stacked(name: str, native: list[str] | None = None, cards: list[list[str]] | None = None) -> None:
    with state.edit(name) as data:
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"head": "bbbbbbb222", "checked": CHECKED | {"head": "bbbbbbb222"}}
        for card in cards or [[KEY, OTHER]]:
            for key in card:
                data["prs"][key] |= {"stack": card, "native": [KEY, OTHER] if native is None else native, "checks": "pass", "review": "approved"}


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


ONE_CARD, TWO_CARDS = [[KEY, OTHER]], [[KEY], [OTHER]]


@pytest.mark.parametrize(
    ("approved", "cards", "card", "unit"),
    [
        ((KEY, OTHER), ONE_CARD, None, (KEY, OTHER)),
        ((KEY,), ONE_CARD, None, None),
        ((KEY,), TWO_CARDS, None, (KEY,)),
        ((OTHER,), TWO_CARDS, None, None),
        ((KEY, OTHER), ONE_CARD, "demo:j1-7-a", None),
    ],
)
def test_a_lander_queues_the_prs_his_card_drew_whole_with_those_below_them_never_a_pr_above_on_its_own_card_and_nothing_while_his_card_holds_it(
    demo: str, landers: list[tuple[str, ...]], approved: tuple[str, ...], cards: list[list[str]], card: str | None, unit: tuple[str, ...] | None
) -> None:
    stacked(demo, cards=cards)
    approve(demo, *approved)
    cards = NO_CARDS
    if card:
        with state.edit(demo) as data:
            data["jobs"]["j1"]["questions"] = [state.new_question(None, "Which store?", state.stamp(T0), holds=None) | {"waits": card}]
        cards = NO_CARDS | {"questions": [question(card)]}
    assert land.start(demo, [KEY, OTHER], cards) == ([unit] if unit else [])
    assert landers == ([unit] if unit else [])
    assert land.start(demo, [KEY, OTHER], cards) == []


@pytest.mark.parametrize(
    ("asker", "holds", "held"),
    [
        ("j1", "", False),
        ("j1", None, True),
        ("j2", "", False),
        ("j2", None, False),
        ("j2", "the dashboard", False),
        ("j1", "ship the watch", True),
        ("j2", "Ship  the WATCH, once its store is picked", False),
        ("j2", "ship the watch, which builder j1 builds", True),
        ("j2", "ship the watch, where #42 lands", True),
        ("j3", "ship the watch", True),
        ("j4", "ship the watch", True),
        ("j4", None, True),
        ("j4", "", False),
        ("j4", "the dashboard", False),
        ("j5", "ship the watch", False),
        ("j5", None, False),
    ],
)
def test_an_open_question_holds_a_stack_from_landing_only_while_its_holds_line_names_a_change_the_stacks_job_builds_or_it_has_none_and_that_job_asked_builds_the_design_or_research_of_or_is_named_by(
    demo: str, landers: list[tuple[str, ...]], asker: str, holds: str | None, held: bool
) -> None:
    stacked(demo)
    approve(demo, KEY, OTHER)
    waits = f"demo:{asker}-1-a"
    with state.edit(demo) as data:
        data["orders"]["o1"]["jobs"].extend(["j2", "j3", "j4", "j5"])
        data["orders"]["o1"]["parts"]["the dashboard"] = state.new_part()
        data["jobs"]["j2"] = job(kind="design", parts=["the dashboard"], carries={}, state="done")
        data["jobs"]["j3"] = job(kind="design", parts=["ship the watch"], carries={}, state="done")
        data["jobs"]["j4"] = job(kind="reader", parts=["ship the watch"], carries={}, state="done")
        data["jobs"]["j5"] = job(kind="reader", parts=["ship the watch"], carries={}, state="done")
        data["jobs"]["j1"]["researched"] = ["j4"]
        data["jobs"][asker]["questions"].append(state.new_question(None, "Which OpenAI endpoint?", state.stamp(T0), holds=holds) | {"waits": waits})
    cards = NO_CARDS | {"questions": [question(waits)]}
    drive = state.load(demo)
    assert land.open_cards(drive, "j1", drive["prs"][KEY]) == ([waits] if held else [])
    assert land.start(demo, [KEY, OTHER], cards) == ([] if held else [(KEY, OTHER)])
    with state.edit(demo) as data:
        data["jobs"][asker]["questions"][-1]["closed"] = state.new_close("answered", "Team", None, seq="9")
    assert land.start(demo, [KEY, OTHER], cards) == ([(KEY, OTHER)] if held else [])


def test_a_question_kept_before_holds_lines_holds_every_change_of_its_own_job_so_its_approved_pr_lands_once_he_answers(demo: str, landers: list[tuple[str, ...]]) -> None:
    approve(demo, KEY)
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved"}
        data["jobs"]["j1"] |= {"waits": [KEY], "questions": [{"file": None, "title": "Which OpenAI endpoint?", "asked": state.stamp(T0), "unsettled": None, "waits": "demo:j1-1-a", "card": None}]}
    cards = NO_CARDS | {"questions": [question("demo:j1-1-a")]}
    assert state.load(demo)["jobs"]["j1"]["questions"][0]["holds"] is None
    assert land.unready(state.load(demo), KEY, cards) == "held: his cards demo:j1-1-a on its work are open"
    assert land.start(demo, [KEY], cards) == []
    with state.edit(demo) as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("answered", "Team", None, seq="9")
    assert land.start(demo, [KEY], cards) == [(KEY,)]


def test_a_built_line_waits_on_the_cards_holding_its_work_by_the_landers_rule_never_one_whose_holds_line_reads_none_or_a_reader_it_was_not_built_on(demo: str) -> None:
    with state.edit(demo) as data:
        data["orders"]["o1"]["jobs"].extend(["j2", "j3"])
        data["jobs"]["j2"] = job(kind="reader", parts=["ship the watch"], carries={}, state="done")
        data["jobs"]["j2"]["questions"] = [
            state.new_question(None, "Which store?", state.stamp(T0), holds="ship the watch") | {"waits": "demo:j2-1-a"},
            state.new_question(None, "Which limiter?", state.stamp(T0), holds="") | {"waits": "demo:j2-2-a"},
        ]
        data["jobs"]["j3"] = job(kind="reader", parts=["ship the watch"], carries={}, state="done")
        data["jobs"]["j3"]["questions"] = [state.new_question(None, "Which queue?", state.stamp(T0), holds="ship the watch") | {"waits": "demo:j3-1-a"}]
        data["jobs"]["j1"]["researched"] = ["j2"]
        state.take_result(data, "j1", "BUILT j1-watch, waits on demo:j9-1-a")
    built = state.load(demo)
    assert built["jobs"]["j1"]["waits"] == ["demo:j9-1-a", "demo:j2-1-a"]
    assert (state.part_cards(built, "j1"), land.open_cards(built, "j1", built["prs"][KEY])) == (["demo:j2-1-a"], ["demo:j2-1-a"])


@pytest.mark.parametrize(("holds", "held"), [("", False), (None, True)])
def test_a_builder_whose_built_line_names_its_own_holds_none_card_leaves_its_approved_green_pr_at_stage_ready_and_one_kept_before_holds_lines_holds_it(
    demo: str, landers: list[tuple[str, ...]], holds: str | None, held: bool
) -> None:
    approve(demo, KEY)
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved"}
        data["jobs"]["j1"]["questions"] = [state.new_question(None, "Should a long chat open on its turns since the last compaction?", state.stamp(T0), holds=holds) | {"waits": "demo:j1-1-a"}]
        state.take_result(data, "j1", "BUILT j1-windowed-open, waits on demo:j1-1-a")
    cards = NO_CARDS | {"questions": [question("demo:j1-1-a")]}
    built = state.load(demo)
    assert (built["jobs"]["j1"]["waits"], built["prs"][KEY]["waits"], land.pr_cards(built, built["prs"][KEY])) == (["demo:j1-1-a"], [], ["demo:j1-1-a"] if held else [])
    assert stage.of(built, KEY, cards).name == ("held" if held else "ready")
    assert land.start(demo, [KEY], cards) == ([] if held else [(KEY,)])


def test_a_flake_fix_goes_up_and_lands_while_his_card_holds_its_jobs_design_work(demo: str, landers: list[tuple[str, ...]]) -> None:
    approve(demo, KEY)
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved", "exempt": state.FLAKE_FIX}
        data["jobs"]["j1"]["questions"] = [state.new_question(None, "Which store?", state.stamp(T0), holds="ship the watch") | {"waits": "demo:j1-1-a"}]
    cards = NO_CARDS | {"questions": [question("demo:j1-1-a")]}
    drive = state.load(demo)
    assert (land.open_cards(drive, "j1", drive["prs"][KEY]), land.open_cards(drive, "j1", drive["prs"][KEY] | {"exempt": None})) == ([], ["demo:j1-1-a"])
    assert land.start(demo, [KEY], cards) == [(KEY,)]
    with state.edit(demo) as data:
        data["jobs"]["j1"]["prs"] = []
    unshipped = state.load(demo)
    assert (land.ship_held(unshipped, "j1"), land.ship_held(unshipped, "j1", state.FLAKE_FIX)) == (["demo:j1-1-a"], [])


def test_a_drive_saved_before_research_links_keeps_each_jobs_researched_readers_and_marks_each_ask_kept_with_no_holds_line_once(demo: str, tmp_path: Path) -> None:
    brief = tmp_path / "j1.brief.md"
    brief.write_text("Order (his words): ship the watch\nHandoff: Researched: j7 (drive job report j7)\nResearched: j2\n")
    unheld, none, named = tmp_path / "unheld.md", tmp_path / "none.md", tmp_path / "named.md"
    unheld.write_text("ASK Which store?\nFindings:\nRoot cause: none: a pick\n")
    none.write_text("ASK Which limiter?\nFindings:\nHolds: none: no option alters what j1 builds\n")
    named.write_text("ASK Which queue?\nFindings:\nHolds: ship the watch\n")
    with state.edit(demo) as data:
        data["jobs"]["j1"] |= {"brief": str(brief), "researched": []}
        data["jobs"]["j1"]["questions"] = [state.new_question(str(file), title, state.stamp(T0), holds=holds) for file, title, holds in ((unheld, "Which store?", ""), (none, "Which limiter?", ""), (named, "Which queue?", "ship the watch"))]
        data.pop("research_linked")
    linked = state.load(demo)
    assert (linked["research_linked"], linked["jobs"]["j1"]["researched"], [question["holds"] for question in linked["jobs"]["j1"]["questions"]]) == (True, ["j2"], [None, "", "ship the watch"])


HOLDING_CARD = "demo:j2-5-a"
BUSY_WAIT = "get rid of this busy 30s wait"
HELD_WHY = f"his cards {HOLDING_CARD} on its work are open"


def holding_job(name: str, *waits: str) -> None:
    with state.edit(name) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved", "checked": CHECKED | {"orders": ["o1", "o2"]}}
        data["orders"]["o2"] = order(text=BUSY_WAIT, jobs=["j2"], parts={BUSY_WAIT: state.new_part()})
        data["jobs"]["j2"] = job(order="o2", goal="change #42", parts=[BUSY_WAIT], carries={}, prs=[state.pr_url(KEY)], waits=[KEY, *waits])


def holding_asks(name: str, holds: str | None) -> None:
    with state.edit(name) as data:
        data["jobs"]["j2"]["questions"].append(state.new_question(None, "Do pause and resume stay in #42?", state.stamp(T0), holds=holds) | {"waits": HOLDING_CARD})


def holding_answered(name: str) -> None:
    with state.edit(name) as data:
        data["jobs"]["j2"]["questions"][-1]["closed"] = state.new_close("answered", "Keep them", None, seq="9")


@pytest.mark.parametrize(
    ("waits", "holds", "held"),
    [
        ((HOLDING_CARD,), None, True),
        ((HOLDING_CARD,), "", False),
        ((), f'"land #42 once #43 is in": each option changes what #42 carries when it lands; {BUSY_WAIT}', True),
        ((), "the dashboard", False),
    ],
)
def test_his_open_card_holds_a_pr_from_landing_only_while_its_holds_line_names_the_change_of_a_job_carrying_it_whichever_job_waits_on_that_card(
    demo: str, landers: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch, waits: tuple[str, ...], holds: str | None, held: bool
) -> None:
    approve(demo, KEY)
    holding_job(demo, *waits)
    holding_asks(demo, holds)
    asking(monkeypatch, demo, HOLDING_CARD)
    cards = view.live_cards(demo)
    assert land.unready(state.load(demo), KEY, cards) == (f"held: {HELD_WHY}" if held else None)
    if held:
        assert land.run(demo, [KEY]) == f"{land.HELD}: {KEY}: {HELD_WHY}"
        assert land.start(demo, [KEY], cards) == []
        holding_answered(demo)
    assert land.start(demo, [KEY], cards) == [(KEY,)]


def test_the_watch_stops_a_lander_when_a_card_opens_on_the_work_of_a_job_holding_its_pr_and_the_pr_lands_once_he_answers(
    demo: str, landers: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    approve(demo, KEY)
    holding_job(demo)
    asking(monkeypatch, demo)
    lander = subprocess.Popen(["sleep", "600"], start_new_session=True)
    with state.edit(demo) as data:
        land.mark(data, (KEY,), lander.pid)
    watching = watcher(demo)
    watching.cards = view.live_cards(demo)
    watch.landed(watching)
    assert (lander.poll(), stage.of(state.load(demo), KEY, watching.cards).name) == (None, "landing")
    holding_asks(demo, BUSY_WAIT)
    asking(monkeypatch, demo, HOLDING_CARD)
    watching.cards = view.live_cards(demo)
    watch.landed(watching)
    assert lander.wait(timeout=10) == -signal.SIGTERM
    assert state.load(demo)["prs"][KEY]["lander"]["outcome"] == f"{land.HELD}: {KEY}: {HELD_WHY}"
    assert (land.unready(state.load(demo), KEY, watching.cards), landers) == (f"held: {HELD_WHY}", [])
    holding_answered(demo)
    watch.landed(watching)
    assert landers == [(KEY,)]


def test_a_lander_the_watch_stops_dequeues_each_pr_it_queued_and_removes_its_worktree(demo: str, tmp_path: Path) -> None:
    approve(demo, KEY)
    calls = tmp_path / "calls"
    on_main(tmp_path, demo, f'#!/bin/sh\necho "$*" >> {calls}\n[ "$1" = --watch ] && kill -TERM $PPID && sleep 5\nexit 0\n')
    before = signal.getsignal(signal.SIGTERM)
    try:
        with pytest.raises(land.Halted):
            land.run(demo, [KEY])
    finally:
        signal.signal(signal.SIGTERM, before)
    assert calls.read_text().splitlines() == ["--check 42", "--watch 42", "--dequeue 42"]
    assert not (state.drive_dir(demo) / land.LANDINGS / str(os.getpid())).exists()


@pytest.mark.parametrize(("cards", "first"), [(ONE_CARD, []), (TWO_CARDS, [(KEY,)])])
def test_a_pr_above_waiting_on_an_open_pr_holds_the_bottom_of_its_own_card_and_no_other_card_and_lands_once_that_pr_merges(
    demo: str, landers: list[tuple[str, ...]], cards: list[list[str]], first: list[tuple[str, ...]]
) -> None:
    stacked(demo, cards=cards)
    approve(demo, KEY, OTHER)
    with state.edit(demo) as data:
        data["prs"][THIRD] = state.new_pr(state.pr_url(THIRD), "j1", "o1")
        data["jobs"]["j1"]["waits"] = [KEY, OTHER, THIRD]
        data["prs"][OTHER]["waits"] = [THIRD]
    assert land.start(demo, [KEY, OTHER], NO_CARDS) == first
    with state.edit(demo) as data:
        data["prs"][THIRD]["state"] = "merged"
        if first:
            data["prs"][KEY]["state"] = "merged"
    assert land.start(demo, [KEY, OTHER], NO_CARDS) == ([(OTHER,)] if first else [(KEY, OTHER)])


@pytest.mark.parametrize(
    ("cards", "name", "why", "unit"), [(ONE_CARD, "behind", f"{OTHER} on his card with it, red: ", []), (TWO_CARDS, "ready", "green and approved by him", [(KEY,)])]
)
def test_a_red_pr_above_holds_the_green_approved_bottom_of_its_own_card_and_never_one_of_another(
    demo: str, landers: list[tuple[str, ...]], cards: list[list[str]], name: str, why: str, unit: list[tuple[str, ...]]
) -> None:
    stacked(demo, cards=cards)
    approve(demo, KEY, OTHER)
    with state.edit(demo) as data:
        data["prs"][OTHER]["checks"] = "fail"
    now = stage.of(state.load(demo), KEY, NO_CARDS)
    assert (now.name, now.why.startswith(why)) == (name, True)
    assert land.start(demo, [KEY, OTHER], NO_CARDS) == unit


def test_his_approve_of_49869_and_53227_lands_them_while_55258_stacked_later_on_its_own_card_grades_and_passes_none_of_it_to_55258(
    demo: str, landers: list[tuple[str, ...]]
) -> None:
    bottom, renewal, reparent = "Forge-AI/monorepo#49869", "Forge-AI/monorepo#53227", "Forge-AI/monorepo#55258"
    shape = {
        bottom: ("bottom", "dev", [bottom, renewal]),
        renewal: ("on-bottom-1", "bottom", [bottom, renewal]),
        reparent: ("on-bottom-2", "bottom", [reparent]),
    }
    with state.edit(demo) as data:
        for key, (branch, base, card) in shape.items():
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {
                "head": "aaaaaaa111",
                "checked": CHECKED,
                "branch": branch,
                "base": base,
                "stack": card,
                "checks": "pass",
                "review": "approved",
            }
        data["prs"][reparent] |= {"checks": "pending", "added": None}
    approve(demo, bottom, renewal)
    data = state.load(demo)
    cards = NO_CARDS | {"pr_cards": [pr_card(bottom, renewal), pr_card(reparent, url="https://acme.slack.com/archives/D123/p2")]}
    assert data["prs"][reparent]["tap"] is None
    assert [stage.of(data, key, cards).name for key in shape] == ["ready", "ready", "grading"]
    assert land.units(data, list(shape), cards) == [(bottom, renewal)]
    with state.edit(demo) as data:
        data["prs"][reparent]["checks"] = "pass"
        data["prs"][renewal]["checks"] = "fail"
    approve(demo, reparent)
    behind = stage.of(state.load(demo), reparent, cards)
    assert (behind.name, behind.why.startswith(f"{renewal} on his card with {bottom}, red: ")) == ("behind", True)
    assert land.start(demo, list(shape), cards) == []
    with state.edit(demo) as data:
        data["prs"][renewal]["checks"] = "pass"
    assert land.start(demo, list(shape), cards) == [(bottom, renewal, reparent)]
    assert landers == [(bottom, renewal, reparent)]


def test_a_tap_stage_adopt_copied_onto_an_open_pr_is_dropped_and_one_he_gave_is_kept(demo: str) -> None:
    approve(demo, KEY)
    with state.edit(demo) as data:
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"tap": data["prs"][KEY]["tap"] | {"via": KEY}}
    data = state.load(demo)
    assert (data["prs"][KEY]["tap"]["label"], data["prs"][OTHER]["tap"]) == ("Approve", None)


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
    assert (watch.wakes(lines[0], state.load(demo), NO_CARDS), stage.of(state.load(demo), KEY, NO_CARDS).name) == (False, "unlanded" if code else "landing")


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


def test_a_landing_whose_worktree_checkout_could_not_fetch_a_blob_is_said_once_to_the_meta_session_as_the_landers_defect_and_tried_again_with_no_new_tap_until_a_good_worktree_queues_it(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved"}
    enqueue_exiting(tmp_path, demo, 0)
    origin, blobless = tmp_path / "origin", tmp_path / "blobless"
    git_in(origin, "config", "uploadpack.allowFilter", "true")
    git_in(tmp_path, "clone", "-q", "--filter=blob:none", f"file://{origin}", str(blobless))
    git_in(blobless, "config", "remote.origin.uploadpack", "git-upload-pack 2>/dev/null")
    with state.edit(demo) as data:
        data["roles"]["coordinator"]["cwd"] = str(blobless)
    commit_files(origin, "admin", {"admin.rs": "fn admin() {}\n"})
    blob = git_in(origin, "rev-parse", "HEAD:admin.rs")
    loose = origin / ".git" / "objects" / blob[:2] / blob[2:]
    withheld = loose.read_bytes()
    loose.unlink()
    watching = watcher(demo)
    failed = land.run(demo, [KEY])
    sha = git_in(origin, "rev-parse", "HEAD")
    assert failed.startswith(f"{land.NO_WORKTREE} Forge-AI/monorepo's default branch to queue {KEY} from: git worktree add at {sha[:12]} failed: ")
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
    assert lines == [f"{watch.LANDER}{failed}", f"PR {KEY}: {land.QUEUED} [o1 j1]"]
    assert [watch.wakes(line, state.load(demo), NO_CARDS) for line in lines] == [False, False]
    sent = posts(monkeypatch)
    assert watch.handed(watching, lines, T0) == [f"PR {KEY}: {land.QUEUED} [o1 j1]"]
    assert posted(sent) == [("defect", f"meta@{demo}", f"{watch.LANDER}{failed}")]


@pytest.mark.parametrize("mode", ["--check", "--watch"])
def test_a_landing_whose_stack_enqueue_failed_on_its_own_is_said_once_to_the_meta_session_as_the_landers_defect_and_tried_again_with_no_new_tap_until_it_queues(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], mode: str
) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved"}
    on_main(tmp_path, demo, f"#!/bin/sh\n[ \"$1\" = {mode} ] || exit 0\necho \"stack-enqueue failed: [Errno 2] No such file or directory: 'ccx'\" >&2\nexit 5\n")
    watching = watcher(demo)
    failed = land.run(demo, [KEY])
    assert failed == (
        f"{land.NO_ENQUEUE} {mode} on {KEY}, which exited 5 on a failure of its own:"
        f" stack-enqueue failed: [Errno 2] No such file or directory: 'ccx'; its whole output is in {state.drive_dir(demo) / land.LOG}"
    )
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    assert land.run(demo, [KEY]) == failed
    enqueue_exiting(tmp_path, demo, 0)
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    assert land.run(demo, [KEY]) == land.QUEUED
    assert land.start(demo, [KEY], NO_CARDS) == []
    assert landers == [(KEY,), (KEY,)]
    lines = watch.event_lines(watching)
    assert lines == [f"{watch.LANDER}{failed}", f"PR {KEY}: {land.QUEUED} [o1 j1]"]
    sent = posts(monkeypatch)
    assert watch.handed(watching, lines, T0) == [f"PR {KEY}: {land.QUEUED} [o1 j1]"]
    assert posted(sent) == [("defect", f"meta@{demo}", f"{watch.LANDER}{failed}")]


def test_replayed_55635_a_landing_whose_diff_read_timed_out_reads_ready_naming_that_try_and_the_next_pass_lands_it_with_no_new_tap_or_defect(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    waiting_job(demo)
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"exempt": "pure port", "wants_tap": False, "checks": "pass", "review": "approved"}
    enqueue_exiting(tmp_path, demo, 0)
    timeout = f'could not read {KEY}\'s diff from GitHub: gh: Get "https://api.github.com/repos/Forge-AI/monorepo/compare": net/http: TLS handshake timeout'

    def timed_out(key: str) -> dict:
        raise state.DriveError(f"{key}: {timeout}; run this again")

    monkeypatch.setattr(land.served, "change", timed_out)
    watching = watcher(demo)
    failed = land.run(demo, [KEY])
    assert failed == f"{land.UNREAD}: {KEY}: {timeout}; run this again"
    assert stage.of(state.load(demo), KEY, NO_CARDS) == stage.Stage("ready", stage.LANDER, f'green, its exempt class "pure port" lands it; its lander\'s last try ended {failed}')
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    monkeypatch.setattr(land.served, "change", lambda key: {"title": "Port: the run page", "body": "", "head": "aaaaaaa111", "diff": b""})
    assert land.run(demo, [KEY]) == land.QUEUED
    assert landers == [(KEY,)]
    lines = watch.event_lines(watching)
    assert lines == [f"PR {KEY}: {failed} [o1 j1]", f"PR {KEY}: {land.QUEUED} [o1 j1]"]
    assert [watch.wakes(line, state.load(demo), NO_CARDS) for line in lines] == [False, False]
    sent = posts(monkeypatch)
    assert watch.handed(watching, lines, T0) == lines
    assert posted(sent) == []


def test_a_lander_that_outlived_an_install_reopens_on_the_newest_drive_which_records_its_outcome_and_logs_it_under_its_pr(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    enqueue_exiting(tmp_path, demo, 0)
    watching = watcher(demo)
    newer = tmp_path / "cache" / "9.9.9" / "bin" / "drive"
    queued = land.enqueued

    def queued_then_installed(name: str, clone: Path, keys: list[str], prs: dict, asked: dict[str, str], dry_run: bool = False, flake: bool = False) -> str:
        outcome = queued(name, clone, keys, prs, asked, dry_run, flake)
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


def enqueue_checking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, check: str, watch: str = 'echo "trunk said $*"') -> Path:
    """A stack-enqueue whose --check prints check and exits 1, and whose --watch runs watch, which queues by default, beside a gh that records each call; the file gh writes."""
    on_main(tmp_path, name, f'#!/bin/sh\nif [ "$1" = --check ]; then printf "%s\\n" {shlex.quote(check)}; exit 1; fi\n{watch}\n')
    return gh_recording(tmp_path, monkeypatch)


def gh_recording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A gh first on PATH that records each call and answers with a comment's link; the file it writes."""
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


def test_an_install_that_saves_the_drive_while_the_lander_checks_a_pr_ends_no_landing_and_the_newest_install_records_its_outcome_and_the_head_it_asked_reviewed(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approve(demo, KEY)
    calls = enqueue_checking(tmp_path, monkeypatch, demo, "#42 BLOCKED 34087d1264 awaiting approval on head from forge-pr-reviewer[bot] (approved 316c682c69, on an older head)")
    newer = tmp_path / "cache" / "9.9.9" / "bin" / "drive"
    fetched = github.fetched

    def installed_while_checking(clone: str, branch: str) -> str:
        state.store(demo, state.stored(demo) | {"saved_by": "9.9.9"})
        return fetched(clone, branch)

    class Execed(Exception):
        pass

    def execv(drive: Path, argv: list[str]) -> None:
        raise Execed(drive, argv)

    monkeypatch.setattr(github, "fetched", installed_while_checking)
    monkeypatch.setattr(install, "upgraded", lambda: newer)
    monkeypatch.setattr(install.os, "execv", execv)
    with pytest.raises(Execed) as execed:
        land.run(demo, [KEY])
    reopened = [str(newer), "pr", "land", state.pr_url(KEY), "--drive", demo, "--outcome", land.QUEUED, "--asked", KEY, "34087d1264"]
    assert execed.value.args == (newer, reopened)
    assert calls.read_text() == "pr comment 42 -R Forge-AI/monorepo --body @forge-pr-reviewer review\n"
    state.store(demo, state.stored(demo) | {"saved_by": state.VERSION})
    monkeypatch.setattr(install, "upgraded", lambda: None)
    assert cli.main(reopened[1:]) == 0
    pr = state.load(demo)["prs"][KEY]
    assert (pr["lander"]["outcome"], pr["review_asked"]) == (land.QUEUED, "34087d1264")


def test_a_head_grading_an_hour_gets_one_review_ask_from_the_watch_which_records_it_so_its_next_deadline_wakes_its_builder(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"PUSHED {state.pr_url(KEY)}")
    serve(monkeypatch, {KEY: read(checks=[*RUNNING, {**REQUIRED, "state": "SUCCESS"}])})
    calls = gh_recording(tmp_path, monkeypatch)
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    watch.run_pass(watching, T0 + timedelta(minutes=61))
    watch.run_pass(watching, T0 + timedelta(minutes=122))
    pr = state.load(demo)["prs"][KEY]
    assert (calls.read_text(), pr["review_asked"]) == ("pr comment 42 -R Forge-AI/monorepo --body @forge-pr-reviewer review\n", pr["head"])


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


def test_a_lander_whose_check_reads_github_still_computing_mergeability_waits_it_out_in_watch_and_queues(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approve(demo, KEY)
    calls = enqueue_checking(tmp_path, monkeypatch, demo, "#42 BLOCKED 34087d1264 GitHub still computing mergeability; GitHub merge state UNKNOWN")
    assert land.run(demo, [KEY]) == land.QUEUED
    assert not calls.exists()


def test_a_lander_whose_watch_stops_on_a_fix_says_its_blocked_line_having_asked_a_review_of_the_head_approved_only_on_an_older_one(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approve(demo, KEY)
    blocked = "#42 BLOCKED 34087d1264 awaiting approval on head from poetic-svc (approved 316c682c69, on an older head); GitHub mergeability CONFLICTING; run ccx vcs stack submit for conflicts"
    stops = "watch stops: a PR needs a fix, not a wait"
    calls = enqueue_checking(tmp_path, monkeypatch, demo, blocked, f'printf "%s\\n" "{stops}" {shlex.quote(blocked)}; exit 1')
    assert land.run(demo, [KEY]) == f"not landed: stack-enqueue --watch exited 1: {stops}; {blocked}; its whole output is in {state.drive_dir(demo) / land.LOG}"
    assert calls.read_text() == "pr comment 42 -R Forge-AI/monorepo --body @forge-pr-reviewer review\n"


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
        data["prs"][KEY] |= {"head": "aaaaaaa111", "lander": {"head": "aaaaaaa111", "pid": 1, "at": state.stamp(T0), "outcome": land.QUEUED, "below": []}}
    serve(monkeypatch, {KEY: read(labels=[github.EJECTED_LABEL], ejected_at=state.stamp(T0 + timedelta(minutes=1)))})
    watching = watcher(demo)
    unlanded = f"STAGE {KEY} unlanded since {state.stamp(T0)}, owed by the session holding its order: Trunk ejected it from the merge queue [o1 j1]"
    lines = watch.run_pass(watching, T0)
    assert lines == [f"PR {KEY}: ejected from the merge queue [o1 j1]", unlanded]
    assert (watch.wakes(lines[0], state.load(demo), NO_CARDS), watch.wakes(unlanded, state.load(demo), NO_CARDS)) == (False, True)
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []


def test_trunk_failing_a_stack_at_its_top_pr_reads_every_pr_its_lander_queued_ejected_until_one_is_queued_again_alone(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stacked(demo)
    approve(demo, KEY, OTHER)
    enqueue_exiting(tmp_path, demo, 0)
    assert land.run(demo, [KEY, OTHER]) == land.QUEUED
    queued = max(state.parse_time(state.load(demo)["prs"][key]["lander"]["at"]) for key in (KEY, OTHER))
    reads = {key: read(headRefOid=head, checks=GREEN, reviewDecision="APPROVED", native=[(KEY, "open"), (OTHER, "open")]) for key, head in ((KEY, "aaaaaaa111"), (OTHER, "bbbbbbb222"))}
    reads[OTHER] |= {"labels": [github.EJECTED_LABEL], "ejected_at": state.stamp(queued + timedelta(minutes=5))}
    serve(monkeypatch, reads)
    assert watch.github_lines(watcher(demo), T0) == [f"PR {KEY}: ejected from the merge queue [o1 j1]", f"PR {OTHER}: ejected from the merge queue [o1 j1]"]
    ejected = stage.Stage("unlanded", stage.ORDER, "Trunk ejected it from the merge queue")
    assert [stage.of(state.load(demo), key, NO_CARDS) for key in (KEY, OTHER)] == [ejected, ejected]
    assert land.run(demo, [KEY]) == land.QUEUED
    assert watch.github_lines(watcher(demo), T0 + timedelta(minutes=1)) == []
    assert [stage.of(state.load(demo), key, NO_CARDS).name for key in (KEY, OTHER)] == ["landing", "unlanded"]


def test_a_trunk_failed_label_left_from_before_its_landers_try_at_a_pushed_head_is_no_ejection(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    tried = "2026-11-12T21:08:30-08:00"
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"head": "566abed000", "lander": {"head": "566abed000", "pid": 1, "at": tried, "outcome": land.QUEUED, "below": [], "entry": KEY}}
    serve(monkeypatch, {KEY: read(headRefOid="566abed000", labels=["externally-merged", github.EJECTED_LABEL], ejected_at="2026-11-12T13:48:03Z")})
    assert watch.github_lines(watcher(demo), state.parse_time(tried) + timedelta(minutes=1)) == []
    assert stage.of(state.load(demo), KEY, NO_CARDS).name == "landing"


EJECTED = "Forge-AI/monorepo#55582"
FLAKE_FIX = "Forge-AI/monorepo#55677"
FLAKY = "TestCancelledFanOutInsideAnUnjournaledFnRecordsItsUsageOnce"
TRUNK_FAILED = {"context": "Trunk Merge Queue (dev)", "state": "FAILURE"}


def ejected_on_a_flake(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(name) as data:
        data["jobs"]["j1"]["prs"] += [state.pr_url(EJECTED), state.pr_url(FLAKE_FIX)]
        data["prs"][EJECTED] = state.new_pr(state.pr_url(EJECTED), "j1", "o1") | {
            "head": "bb28d680bf",
            "checks": "pass",
            "review": "approved",
            "tap": state.new_tap("Approve", "bb28d680bf", "pressed the button", at=state.stamp(T0 - timedelta(minutes=5))),
            "lander": {"head": "bb28d680bf", "pid": 1, "at": state.stamp(T0), "outcome": land.QUEUED, "below": [], "entry": EJECTED},
        }
        data["prs"][FLAKE_FIX] = state.new_pr(state.pr_url(FLAKE_FIX), "j1", "o1") | {"head": "9a882a9000", "exempt": "test-only", "wants_tap": False}
    trunk = read(headRefOid="bb28d680bf", checks=[*GREEN, TRUNK_FAILED], reviewDecision="APPROVED", approvals=["bb28d680bf"], labels=[github.EJECTED_LABEL])
    serve(monkeypatch, {EJECTED: trunk | {"ejected_at": state.stamp(T0 + timedelta(minutes=1))}})
    assert f"PR {EJECTED}: ejected from the merge queue [o1 j1]" in watch.github_lines(watcher(name), T0 + timedelta(minutes=2))
    assert stage.of(state.load(name), EJECTED, NO_CARDS) == stage.Stage("unlanded", stage.ORDER, "Trunk ejected it from the merge queue")


def test_replayed_55582_a_pr_trunk_ejected_on_a_flaky_test_outside_its_diff_is_queued_again_at_the_same_commit_once_that_tests_fix_merges_and_only_once(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], landers: list[tuple[str, ...]]
) -> None:
    ejected_on_a_flake(demo, monkeypatch)
    assert cli.main(["pr", "set", EJECTED, "--flaky", FLAKY, "Forge-AI/monorepo#99999", "--drive", demo]) == 2
    assert "the drive tracks no such PR" in capsys.readouterr().err
    assert cli.main(["pr", "set", KEY, "--flaky", FLAKY, state.pr_url(FLAKE_FIX), "--drive", demo]) == 2
    assert "Trunk ejected no try of its lander at aaaaaaa" in capsys.readouterr().err
    assert cli.main(["pr", "set", EJECTED, "--flaky", FLAKY, state.pr_url(FLAKE_FIX), "--drive", demo]) == 0
    waiting = f"Trunk ejected it from the merge queue at bb28d68 on its flaky test {FLAKY}; the lander requeues that commit once {FLAKE_FIX}, that test's fix, merges"
    assert stage.of(state.load(demo), EJECTED, NO_CARDS) == stage.Stage("waiting", stage.WAITED, waiting)
    assert land.start(demo, [EJECTED], NO_CARDS) == []
    with state.edit(demo) as data:
        data["prs"][FLAKE_FIX]["state"] = "merged"
    assert stage.of(state.load(demo), EJECTED, NO_CARDS) == stage.Stage("ready", stage.LANDER, "green and approved by him at bb28d68")
    assert land.start(demo, [EJECTED], NO_CARDS) == [(EJECTED,)]
    enqueue_exiting(tmp_path, demo, 0)
    capsys.readouterr()
    assert land.run(demo, [EJECTED]) == land.QUEUED
    said = capsys.readouterr().out
    assert ("trunk said --check --flake 55582" in said, "trunk said --watch --flake 55582" in said) == (True, True)
    requeued = state.load(demo)["prs"][EJECTED]
    assert requeued["head"] == "bb28d680bf"
    landing = f"queued for merge at bb28d68 since {requeued['lander']['at']}, requeued once {FLAKE_FIX}, the fix of its flaky test {FLAKY}, merged"
    assert stage.of(state.load(demo), EJECTED, NO_CARDS) == stage.Stage("landing", stage.LANDER, landing)
    again = state.stamp(state.parse_time(requeued["lander"]["at"]) + timedelta(minutes=5))
    trunk = read(headRefOid="bb28d680bf", checks=[*GREEN, TRUNK_FAILED], reviewDecision="APPROVED", approvals=["bb28d680bf"], labels=[github.EJECTED_LABEL])
    serve(monkeypatch, {EJECTED: trunk | {"ejected_at": again}})
    assert f"PR {EJECTED}: ejected from the merge queue [o1 j1]" in watch.github_lines(watcher(demo), state.parse_time(again))
    final = f"Trunk ejected it from the merge queue again after the lander requeued bb28d68 once {FLAKE_FIX}, the fix of its flaky test {FLAKY}, merged"
    assert stage.of(state.load(demo), EJECTED, NO_CARDS) == stage.Stage("unlanded", stage.ORDER, final)
    assert land.start(demo, [EJECTED], NO_CARDS) == []


def test_a_pr_trunk_ejected_on_a_flaky_test_reads_unlanded_again_once_that_tests_fix_closes_unmerged(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    ejected_on_a_flake(demo, monkeypatch)
    assert cli.main(["pr", "set", EJECTED, "--flaky", FLAKY, FLAKE_FIX, "--drive", demo]) == 0
    with state.edit(demo) as data:
        data["prs"][FLAKE_FIX]["state"] = "closed"
    closed = f"Trunk ejected it from the merge queue on its flaky test {FLAKY}, and {FLAKE_FIX}, that test's fix, closed unmerged"
    assert stage.of(state.load(demo), EJECTED, NO_CARDS) == stage.Stage("unlanded", stage.ORDER, closed)


BOTTOM, TOP = "Forge-AI/monorepo#49869", "Forge-AI/monorepo#53227"
REFUSED_BY_GITHUB = f"{land.REFUSED} once Trunk's merge queue took {TOP}: GITHUB_API_MERGE_PR_FAILED, with no failing check"


def trunk_failed(key: str, reason: str, at: str, failing: str = "") -> dict:
    return {"seq": "7", "kind": "queue", "at": at, "text": f"{key} {agents.TRUNK_FAILED}: {reason}", "fields": {"outcome": "failed", "attrs": {"reason": reason, "failing": failing, "target": "dev"}}, "refs": {"prs": [key]}}


def queued_stack(name: str, monkeypatch: pytest.MonkeyPatch, at: datetime = T0) -> None:
    waiting_job(name)
    with state.edit(name) as data:
        for key, head in ((BOTTOM, "aaaaaaa111"), (TOP, "bbbbbbb222")):
            lander = {"head": head, "pid": 1, "at": state.stamp(at), "outcome": land.QUEUED, "was": None, "below": [], "entry": TOP}
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {
                "head": head,
                "checked": CHECKED | {"head": head},
                "native": [BOTTOM, TOP],
                "checks": "pass",
                "review": "approved",
                "lander": lander,
            }
    approve(name, BOTTOM, TOP)
    native = [(BOTTOM, "open"), (TOP, "open")]
    ejected = read(
        headRefOid="bbbbbbb222", checks=[*GREEN, TRUNK_FAILED], reviewDecision="APPROVED", native=native, labels=[github.EJECTED_LABEL], ejected_at=state.stamp(at + timedelta(minutes=5))
    )
    serve(monkeypatch, {BOTTOM: read(checks=GREEN, reviewDecision="APPROVED", native=native), TOP: ejected})


def test_replayed_53227_a_stack_github_refused_once_trunk_tested_it_green_reads_merge_refused_never_checks_failed_and_lands_one_pr_at_a_time_bottom_first(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], trunk_queue: list[dict]
) -> None:
    queued_stack(demo, monkeypatch)
    trunk_queue.append(trunk_failed(TOP, "GITHUB_API_MERGE_PR_FAILED", "2026-11-09T15:20:00Z"))
    watching = watcher(demo)
    assert watch.github_lines(watching, T0 + timedelta(minutes=30)) == [f"PR {key}: ejected from the merge queue [o1 j1]" for key in (BOTTOM, TOP)]
    data = state.load(demo)
    assert [(data["prs"][key]["checks"], data["prs"][key]["lander"]["outcome"], stage.of(data, key, NO_CARDS).name) for key in (BOTTOM, TOP)] == [("pass", REFUSED_BY_GITHUB, "ready")] * 2
    assert watch.event_lines(watching) == [f"PR {key}: {REFUSED_BY_GITHUB} [o1 j1]" for key in (BOTTOM, TOP)]
    assert land.start(demo, [BOTTOM, TOP], NO_CARDS) == [(BOTTOM,)]
    with state.edit(demo) as data:
        data["prs"][BOTTOM] |= {"state": "merged", "lander": data["prs"][BOTTOM]["lander"] | {"outcome": land.QUEUED}}
    assert land.start(demo, [TOP], NO_CARDS) == [(TOP,)]
    assert landers == [(BOTTOM,), (TOP,)]


def test_a_second_refusal_of_a_pr_landed_alone_at_the_same_head_reads_unlanded_waking_the_session_holding_its_order(
    demo: str, monkeypatch: pytest.MonkeyPatch, trunk_queue: list[dict]
) -> None:
    alone = T0 + timedelta(hours=1)
    queued_stack(demo, monkeypatch, alone)
    with state.edit(demo) as data:
        data["prs"][BOTTOM] |= {"state": "merged", "lander": data["prs"][BOTTOM]["lander"] | {"entry": BOTTOM}}
        data["prs"][TOP]["lander"]["was"] = REFUSED_BY_GITHUB
    trunk_queue.extend([trunk_failed(TOP, "GITHUB_API_MERGE_PR_FAILED", "2026-11-09T16:20:00Z"), trunk_failed(TOP, "GITHUB_API_MERGE_PR_FAILED", "2026-11-09T15:20:00Z")])
    moment = T0 + timedelta(hours=2)
    lines = watch.run_pass(watcher(demo), moment)
    unlanded = (
        f"STAGE {TOP} unlanded since {state.stamp(moment)}, owed by the session holding its order:"
        f" its lander's try at bbbbbbb, started {state.stamp(alone)}, ended {REFUSED_BY_GITHUB} [o1 j1]"
    )
    assert unlanded in lines
    assert watch.wakes(unlanded, state.load(demo), NO_CARDS)


@pytest.mark.parametrize(
    ("reason", "failing", "at"),
    [
        ("TEST_RUN_REQUIRED_STATUS_FAILED", "buildkite/test", "2026-11-09T15:20:00Z"),
        ("GITHUB_API_MERGE_PR_FAILED", "buildkite/test", "2026-11-09T15:20:00Z"),
        ("GITHUB_API_MERGE_PR_FAILED", "", "2026-11-09T14:20:00Z"),
    ],
)
def test_a_stack_trunk_ejected_on_a_failing_check_or_refused_only_before_its_try_reads_unlanded_and_lands_nothing_again(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], trunk_queue: list[dict], reason: str, failing: str, at: str
) -> None:
    queued_stack(demo, monkeypatch)
    trunk_queue.append(trunk_failed(TOP, reason, at, failing))
    watch.github_lines(watcher(demo), T0 + timedelta(minutes=30))
    ejected = stage.Stage("unlanded", stage.ORDER, "Trunk ejected it from the merge queue")
    assert [stage.of(state.load(demo), key, NO_CARDS) for key in (BOTTOM, TOP)] == [ejected, ejected]
    assert land.start(demo, [BOTTOM, TOP], NO_CARDS) == []


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
        ("waiting", f"{install.PROOF_WAITS}: https://github.com/yasyf/cc-skills/pull/7: /p.sh exits 2: WAIT S1 no exchange since the install", False),
        ("waiting", "merged", True),
        ("done", "merged", False),
    ],
)
def test_a_merge_its_proof_proved_wakes_no_one_and_a_failed_proof_wakes_the_holder(demo: str, state_after: str, change: str, wakes: bool) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"]["state"] = state_after
        data["prs"][KEY]["state"] = "merged"
    assert watch.wakes(f"PR {KEY}: {change} [o1 j1]", state.load(demo), NO_CARDS) is wakes


MERGED_PR = "yasyf/cc-skills#4987"
REFUSED_BEFORE_ITS_MERGE = (
    f"not landed: gh pr merge --squash --admin refused it: X Pull request {MERGED_PR} is not mergeable: the merge commit cannot be cleanly created."
    " To have the pull request merged after all the requirements have been met, add the `--auto` flag."
)


@pytest.mark.parametrize(
    ("row", "change", "wakes"),
    [
        ("merged", REFUSED_BEFORE_ITS_MERGE, False),
        ("merged", "new head f7b34dd", False),
        ("merged", "checks failed", False),
        ("merged", "conflicts with its base", False),
        ("merged", "merged, but its merge commit did not read, so nothing was installed: gh exited 1", True),
        ("merged", f"{install.PROOF_FAILED}: https://github.com/{MERGED_PR.replace('#', '/pull/')}: /p.sh exits 1", True),
        ("closed", "closed", True),
        ("closed", "checks failed", False),
    ],
)
def test_a_line_on_a_pr_no_longer_open_wakes_the_holder_only_when_it_says_how_the_pr_ended_never_for_a_move_made_while_it_was_open(
    demo: str, row: str, change: str, wakes: bool
) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"]["state"] = "done"
        data["prs"][MERGED_PR] = state.new_pr(state.pr_url(MERGED_PR), "j1", "o1") | {"state": row}
    assert watch.wakes(f"PR {MERGED_PR}: {change} [o1 j1]", state.load(demo), NO_CARDS) is wakes


def test_a_stage_line_asks_a_step_only_while_stage_of_still_names_its_stage_so_one_made_before_the_landers_next_try_or_the_merge_asks_none(demo: str) -> None:
    waiting_job(demo)
    tried(demo, f"not landed: gh pr merge --squash --admin refused it: {POLICY}")
    data = state.load(demo)
    line = f"STAGE {stage.line(YASYF, data['prs'][YASYF], stage.of(data, YASYF, NO_CARDS))} [o1 j1]"
    assert line.startswith(f"STAGE {YASYF} unlanded, owed by the session holding its order: its lander's try at aaaaaaa")
    assert watch.wakes(line, data, NO_CARDS)
    with state.edit(demo) as data:
        data["prs"][YASYF] |= {"head": "bbbbbbb222", "lander": {"head": "bbbbbbb222", "pid": os.getpid(), "at": state.stamp(T0), "outcome": None}}
    assert not watch.wakes(line, state.load(demo), NO_CARDS)
    with state.edit(demo) as data:
        data["prs"][YASYF]["state"] = "merged"
    assert not watch.wakes(line, state.load(demo), NO_CARDS)
    assert watch.wakes("STAGE not landed: #7 waits on a review of head bbbbbbb [o1 j1]", state.load(demo), NO_CARDS)


def test_a_line_held_for_the_meta_session_is_judged_as_it_posts_so_a_pr_that_merged_meanwhile_hands_over_only_how_it_ended(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    tooling_order(demo, "done")
    with state.edit(demo) as data:
        data["prs"][MERGED_PR] = state.new_pr(state.pr_url(MERGED_PR), "j1", "o1") | {"state": "merged"}
    sent = posts(monkeypatch)
    watching = watcher(demo)
    failed = f"PR {MERGED_PR}: {install.PROOF_FAILED}: /p.sh exits 1 [o1 j1]"
    watching.unsent["meta"] = [f"PR {MERGED_PR}: {REFUSED_BEFORE_ITS_MERGE} [o1 j1]", failed]
    assert watch.sent(watching, "meta", [], T0) == []
    assert (posted(sent), watching.unsent["meta"]) == ([("handoff", f"meta@{demo}", failed)], [])


@pytest.mark.parametrize(("key", "mergeable", "handed"), [(YASYF, "CONFLICTING", False), (YASYF, "MERGEABLE", True), (KEY, "CONFLICTING", True)])
def test_a_ready_on_a_yasyf_pr_whose_builder_owes_its_fix_wakes_that_builder_and_hands_the_meta_session_no_second_one(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], key: str, mergeable: str, handed: bool
) -> None:
    url = state.pr_url(key)
    tooling_order(demo, "waiting")
    with state.edit(demo) as data:
        data["prs"][key] = state.new_pr(url, "j1", "o1") | {"head": "aaaaaaa111", "wants_tap": False, "checked": CHECKED}
        data["jobs"]["j1"].update(said=f"PUSHED {url}", prs=[url])
    serve(monkeypatch, {key: read(required_checks=[], mergeable=mergeable)})
    sent = posts(monkeypatch)
    watch.run_pass(watcher(demo), T0 + timedelta(minutes=1))
    readies = [text for _, to, text in posted(sent) if to == f"meta@{demo}" and text.startswith(f"JOB j1 o1 READY {url}")]
    assert (len(readies), [woke for woke, _ in woken]) == (int(handed), [key] if mergeable == "CONFLICTING" else [])


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
    assert not watch.wakes(f"PR {YASYF}: merged at ccc333ddd444; long-running@skills 1.0.4 installed [o1 j1]", state.load(demo), NO_CARDS)


def test_a_merged_ready_job_waits_for_its_lander_before_its_proof_starts(demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, YASYF, proof_file(tmp_path))
    with state.edit(demo) as data:
        data["prs"][YASYF]["lander"] = {"head": "aaaaaaa111", "pid": os.getpid(), "at": state.stamp(T0), "outcome": None}
    assert (watch.run_pass(watcher(demo), T0), provers) == ([], [])


SUPERSEDED = "yasyf/cc-skills#8"


@pytest.mark.parametrize(("said", "started"), [(f"READY {state.pr_url(YASYF)}", ["j1"]), (f"READY {state.pr_url(YASYF)} {state.pr_url(SUPERSEDED)}", [])])
def test_a_merged_ready_jobs_proof_counts_the_prs_its_line_names_so_a_closed_pr_the_line_leaves_out_holds_nothing_and_one_it_names_holds_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path, said: str, started: list[str]
) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, YASYF, proof_file(tmp_path))
    with state.edit(demo) as data:
        data["prs"][SUPERSEDED] = state.new_pr(state.pr_url(SUPERSEDED), "j1", "o1") | {"state": "closed"}
        data["jobs"]["j1"].update(said=said, prs=[state.pr_url(YASYF), state.pr_url(SUPERSEDED)])
    lines = watch.run_pass(watcher(demo), T0)
    assert (provers, any(line.startswith("ORPHAN ") and f"{SUPERSEDED} closed" in line for line in lines)) == (started, not started)


def test_a_merged_ready_tooling_job_keeping_no_proof_file_takes_blocked_at_its_merge_and_its_part_is_said_orphaned_once(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, YASYF, None)
    monkeypatch.setattr(state, "now", lambda: T0)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    blocked = (
        f"BLOCKED {state.pr_url(YASYF)} merged, and j1 keeps no proof file to run, so nothing proves it live:"
        " keep one with drive job set j1 --repro <file>, written from its PROVE: line, then drive job prove j1 proves it on j1"
    )
    assert (state.load(demo)["jobs"]["j1"]["state"], state.load(demo)["jobs"]["j1"]["said"]) == ("failed", blocked)
    assert watch.run_pass(watching, T0 + timedelta(minutes=3)) == ['ORPHAN o1 "ship the watch": j1 failed']
    with state.edit(demo) as data:
        data["jobs"]["j1"]["repro"] = proof_file(tmp_path)
    assert [watch.run_pass(watching, T0 + timedelta(minutes=minutes)) for minutes in (31, 62)] == [[], []]
    assert provers == []


def test_a_merged_ready_product_job_takes_done_at_its_merge_and_the_watch_adds_the_builder_proving_it_live_waiting_on_each_release_its_paths_reach(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path, ready_records: list[list[dict]]
) -> None:
    serve(monkeypatch, {})
    asked = []
    production_reads(monkeypatch, {"43": [{"component": "api", "env": "plat-usw2-prod"}]})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: asked.append(keys) or {OTHER: {"components": ["api", "web"], "touched": ["api"]}})
    merged_ready(demo, OTHER, proof_file(tmp_path))
    watching = watcher(demo)
    merged = f"{OTHER} at ccc333ddd444"
    lines = watch.run_pass(watching, T0)
    said = [line for line in lines if line.startswith("JOB ")]
    assert said == [f"JOB j2 o1 added, proves {OTHER}, waits on 1 release: drive status j2"] and watch.wakes(said[0], state.load(demo), NO_CARDS)
    assert asked == [[OTHER]]
    assert not [line for line in lines if line.startswith(("FREE ", "ORPHAN "))]
    data = state.load(demo)
    builder = data["jobs"]["j2"]
    assert (builder["kind"], builder["state"], builder["waits"], builder["parts"], builder["builds_nothing"], "j2" in data["orders"]["o1"]["jobs"]) == (
        "builder",
        "waiting",
        [f"release:api:{OTHER}"],
        data["jobs"]["j1"]["parts"],
        True,
        True,
    )
    assert f"Handoff: {merged} merged: install it and prove it live by the INSTALL: and PROVE: lines of j1's return before its DONE, in reports/j1.md" in Path(builder["brief"]).read_text()
    assert (data["jobs"]["j1"]["said"], data["orders"]["o1"]["proof"], data["orders"]["o1"]["close_report"]) == (f"DONE {state.pr_url(OTHER)} merged: {merged}", None, None)
    assert not [record for records in ready_records for record in records if (record.get("attr") or {}).get("close") == "o1"]
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=31)) if line.startswith(("FREE ", "ORPHAN "))]
    assert provers == []


def test_a_done_at_its_merge_delivers_no_part_so_the_part_is_owed_until_the_builder_proving_it_live_returns_done(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {OTHER: {"components": ["api"], "touched": ["api"]}})
    merged_ready(demo, OTHER, None)
    watch.run_pass(watcher(demo), T0)
    data = state.load(demo)
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j2"]["state"], state.delivered(data, "o1", "ship the watch")) == ("done", "waiting", False)
    with state.edit(demo) as data:
        state.take_result(data, "j2", f"DONE {state.pr_url(OTHER)} live on the api release")
    assert state.delivered(state.load(demo), "o1", "ship the watch")


def test_an_old_merge_never_proven_live_across_two_parts_gets_one_prover_waiting_on_its_release_in_place_of_their_orphans_and_a_second_pass_adds_none(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str]
) -> None:
    serve(monkeypatch, {})
    production_reads(monkeypatch, {"42": [{"component": "api", "env": "plat-usw2-prod"}]})
    asked = []
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: asked.append(keys) or {KEY: {"components": ["api", "web"], "touched": ["api"]}})
    earlier, carried, merged = state.stamp(T0 - timedelta(days=2)), "ship the dashboard", f"{KEY} at ccc333ddd444"
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"state": "merged", "merged_sha": "ccc333ddd444eee"}
        data["orders"]["o2"] = order(text=carried, jobs=["j1"], parts={carried: state.new_part()}, updated=earlier)
        data["jobs"]["j1"].update(state="done", said=f"DONE {state.pr_url(KEY)} merged: {merged}", carries={"o2": [carried]}, updated=earlier)
    watching = watcher(demo)
    lines = watch.run_pass(watching, T0)
    said = [line for line in lines if line.startswith("JOB ")]
    assert said == [f"JOB j2 o1 added, proves {KEY}, waits on 1 release: drive status j2"] and watch.wakes(said[0], state.load(demo), NO_CARDS)
    assert not [line for line in lines if line.startswith(("FREE ", "ORPHAN "))]
    data = state.load(demo)
    builder = data["jobs"]["j2"]
    assert (builder["kind"], builder["state"], builder["waits"], builder["parts"], builder["carries"]) == ("builder", "waiting", [f"release:api:{KEY}"], ["ship the watch"], {"o2": [carried]})
    assert f"Handoff: {merged} merged: install it and prove it live by the INSTALL: and PROVE: lines of j1's return before its DONE, in reports/j1.md" in Path(builder["brief"]).read_text()
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=31)) if line.startswith(("FREE ", "ORPHAN "))]
    assert (list(state.load(demo)["jobs"]), asked, provers) == (["j1", "j2"], [[KEY]], [])


def test_a_merged_product_job_over_several_prs_gets_one_prover_said_in_one_line_counting_their_releases(demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str]) -> None:
    serve(monkeypatch, {})
    production_reads(monkeypatch, {"42": [{"component": "api", "env": "plat-usw2-prod"}], "43": [{"component": "web", "env": "plat-usw2-prod"}]})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {KEY: {"components": ["api"], "touched": ["api"]}, OTHER: {"components": ["web"], "touched": ["web"]}})
    merged_ready(demo, OTHER, None)
    with state.edit(demo) as data:
        data["prs"][KEY]["merged_sha"] = "aaa111bbb222ccc"
        data["jobs"]["j1"].update(said=f"READY {state.pr_url(KEY)} {state.pr_url(OTHER)}", prs=[state.pr_url(KEY), state.pr_url(OTHER)])
    lines = watch.run_pass(watcher(demo), T0)
    assert [line for line in lines if line.startswith("JOB ")] == [f"JOB j2 o1 added, proves {KEY}, {OTHER}, waits on 2 releases: drive status j2"]
    assert (list(state.load(demo)["jobs"]), state.load(demo)["jobs"]["j2"]["waits"], provers) == (["j1", "j2"], [f"release:api:{KEY}", f"release:web:{OTHER}"], [])


def test_a_merged_pr_changing_only_tests_gets_a_prover_waiting_on_no_release_that_proves_it_by_its_tests_on_dev(
    demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "ci").write_text("")
    with state.edit(demo) as data:
        data["roles"]["coordinator"]["cwd"] = str(tmp_path)
    monkeypatch.setattr(github, "checkout_repo", lambda cwd: "Forge-AI/monorepo")
    monkeypatch.setattr(orca, "checkout", lambda name, cwd: tmp_path)
    monkeypatch.setattr(github, "files", lambda key: ["go/sand/internal/runtime-api/memory/project_test.go", "go/sand/next/internal/runtime-api/memory/project_test.go"])
    merged_ready(demo, OTHER, None)
    lines = watch.run_pass(watcher(demo), T0)
    data = state.load(demo)
    prover = data["jobs"]["j2"]
    assert (data["jobs"]["j1"]["state"], prover["waits"], prover["builds_nothing"]) == ("done", [], True)
    assert prover["brief_flags"]["handoff"] == f"{OTHER} at ccc333ddd444 merged, changing only tests or CI config, which no release ships: prove it by its tests passing on dev at or after its merge commit"
    assert [line for line in lines if line.startswith("JOB ")] == [
        f'JOB j2 o1 added, proves {OTHER}, waits on no release: start its agent now, run_in_background, on the prompt "Read your brief first: {prover["brief"]}"'
    ]


def test_a_merged_product_job_whose_own_order_is_done_takes_done_at_its_merge_and_its_prover_goes_on_the_open_order_it_carries(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    merged_ready(demo, OTHER, None)
    carried = "ship the dashboard"
    with state.edit(demo) as data:
        data["orders"]["o1"]["state"] = "done"
        data["orders"]["o2"] = order(text=carried, jobs=["j1"], parts={carried: state.new_part()})
        data["jobs"]["j1"]["carries"] = {"o2": [carried]}
    lines = watch.run_pass(watcher(demo), T0)
    data = state.load(demo)
    prover = data["jobs"]["j2"]
    assert (data["jobs"]["j1"]["state"], prover["order"], prover["parts"], prover["carries"], prover["builds_nothing"], "j2" in data["orders"]["o2"]["jobs"]) == (
        "done",
        "o2",
        [carried],
        {},
        True,
        True,
    )
    assert [line for line in lines if line.startswith("JOB ")] == [
        f'JOB j2 o2 added, proves {OTHER}, waits on no release: start its agent now, run_in_background, on the prompt "Read your brief first: {prover["brief"]}"'
    ]


@pytest.mark.parametrize(
    "named",
    [
        {"said": f"DONE {state.pr_url(OTHER)} merged: {OTHER} at ccc333ddd444"},
        {"said": "DONE live on release jubilant-heaney", "brief_flags": {"handoff": f"{OTHER} at ccc333ddd444 merged: install it and prove it live"}},
    ],
)
def test_a_merge_done_or_a_merge_handoff_naming_a_merged_pr_proves_nothing_so_its_merged_ready_job_takes_done_at_its_merge_with_a_prover(
    demo: str, monkeypatch: pytest.MonkeyPatch, named: dict
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    merged_ready(demo, OTHER, None)
    with state.edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j9")
        data["jobs"]["j9"] = state.new_job("o1", "builder", "prove it", [], state.stamp()) | {"state": "done"} | named
    lines = watch.run_pass(watcher(demo), T0)
    data = state.load(demo)
    assert (data["jobs"]["j1"]["state"], list(data["jobs"]), data["jobs"]["j10"]["parts"]) == ("done", ["j1", "j9", "j10"], data["jobs"]["j1"]["parts"])
    assert [line for line in lines if line.startswith("JOB ")] == [
        f'JOB j10 o1 added, proves {OTHER}, waits on no release: start its agent now, run_in_background, on the prompt "Read your brief first: {data["jobs"]["j10"]["brief"]}"'
    ]


@pytest.mark.parametrize("ended", ["failed", "stopped"])
def test_an_old_merge_whose_prover_failed_or_stopped_gets_no_second_and_its_part_is_said_orphaned(demo: str, monkeypatch: pytest.MonkeyPatch, ended: str) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    earlier = state.stamp(T0 - timedelta(days=2))
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"state": "merged", "merged_sha": "ccc333ddd444eee"}
        data["jobs"]["j1"].update(state="done", said=f"DONE {state.pr_url(KEY)} merged: {KEY} at ccc333ddd444", updated=earlier)
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = state.new_job("o1", "builder", f"prove {KEY} live", data["jobs"]["j1"]["parts"], earlier) | {"state": ended, "builds_nothing": True, "updated": earlier}
    watching = watcher(demo)
    lines = [line for minutes in (0, 3) for line in watch.run_pass(watching, T0 + timedelta(minutes=minutes))]
    assert (list(state.load(demo)["jobs"]), [line for line in lines if line.startswith("JOB ")]) == (["j1", "j2"], [])
    assert [line for line in lines if line.startswith('ORPHAN o1 "ship the watch": ')]


def test_a_merged_ready_product_job_whose_paths_reach_no_release_has_its_builder_briefed_to_start_in_the_turn_its_line_wakes(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    merged_ready(demo, OTHER, proof_file(tmp_path))
    said = [line for line in watch.run_pass(watcher(demo), T0) if line.startswith("JOB ")]
    data = state.load(demo)
    assert (data["jobs"]["j2"]["state"], data["jobs"]["j2"]["waits"]) == ("running", [])
    assert said == [f'JOB j2 o1 added, proves {OTHER}, waits on no release: start its agent now, run_in_background, on the prompt "Read your brief first: {data["jobs"]["j2"]["brief"]}"']
    assert provers == []


@pytest.mark.parametrize("proved", [False, True])
def test_a_merged_ready_product_job_on_a_done_order_or_whose_pr_a_job_proved_live_takes_no_done_at_its_merge_and_gets_no_second_prover(
    demo: str, monkeypatch: pytest.MonkeyPatch, proved: bool
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    merged_ready(demo, OTHER, None)
    with state.edit(demo) as data:
        if proved:
            data["orders"]["o1"]["jobs"].append("j9")
            data["jobs"]["j9"] = state.new_job("o1", "builder", "prove it", data["jobs"]["j1"]["parts"], state.stamp()) | {"state": "done", "said": f"DONE {OTHER} live on release jubilant-heaney"}
        else:
            data["orders"]["o1"]["state"] = "done"
    lines = watch.run_pass(watcher(demo), T0)
    data = state.load(demo)
    assert not [line for line in lines if line.startswith("JOB ")]
    assert (data["jobs"]["j1"]["state"], list(data["jobs"])) == ("waiting", ["j1", "j9"] if proved else ["j1"])


@pytest.mark.parametrize(("fix", "sha"), [("Forge-AI/monorepo#55420", "485f9f408150"), ("Forge-AI/monorepo#55448", "6ec574a4f779")])
def test_a_fix_merged_to_dev_that_no_release_holds_is_never_proven_live_so_its_reporter_is_owed_no_fix_live_note(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], ready_records: list[list[dict]], fix: str, sha: str
) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, fix, None)
    with state.edit(demo) as data:
        data["prs"][fix]["merged_sha"] = sha
        data["orders"]["o1"]["reporters"] = [state.reporter(REPORTED)]
    watching = watcher(demo)
    lines = [line for minutes in (0, 11, 40) for line in watch.run_pass(watching, T0 + timedelta(minutes=minutes))]
    assert [line for line in lines if line.startswith(f"JOB j2 o1 added, proves {fix}, waits on ")]
    assert not [line for line in lines if line.startswith("TELL ")]
    data = state.load(demo)
    assert (data["jobs"]["j1"]["state"], data["orders"]["o1"]["proof"], data["orders"]["o1"]["reporters"][0]["tell_said"]) == ("done", None, None)
    assert not [record for records in ready_records for record in records if (record.get("attr") or {}).get("close") == "o1"]
    assert render.reporter_said(data, data["orders"]["o1"]) == f", reported in {REPORTED}, which hears it is fixed once its fix is proven live"
    assert provers == []


@pytest.mark.parametrize("sha", ["485f9f408150", "6ec574a4f779"])
def test_an_open_order_a_merge_commit_once_proved_loses_that_proof_and_its_close_while_a_closed_one_or_a_yasyf_commit_keeps_them(demo: str, sha: str) -> None:
    commit, tooling = f"https://github.com/Forge-AI/monorepo/commit/{sha}", "https://github.com/yasyf/cc-skills/commit/ccc333ddd444"
    with state.edit(demo) as data:
        data["orders"]["o1"] |= {"proof": commit, "closed_on": "689162", "reporters": [state.reporter(REPORTED) | {"tell_said": state.stamp(T0)}]}
        data["orders"]["o2"] = order(state="done", proof=commit, closed_on="689100", jobs=[])
        data["orders"]["o3"] = order(proof=tooling, jobs=[])
    data = state.load(demo)
    reported = data["orders"]["o1"]
    assert (reported["proof"], reported["closed_on"], reported["close_report"], reported["reporters"][0]["tell_said"]) == (None, None, None, None)
    assert (data["orders"]["o2"]["proof"], data["orders"]["o2"]["closed_on"], data["orders"]["o3"]["proof"]) == (commit, "689100", tooling)


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
        " a fresh builder with a merge handoff, added with drive job add builder --builds-nothing, proves it from its INSTALL: and PROVE: lines once that release is out"
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



def test_a_proof_waiting_on_his_next_word_holds_its_job_with_no_orphan_and_runs_again_only_once_he_is_heard_after_it_started(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, YASYF, proof_file(tmp_path))
    ended = subprocess.Popen(["true"])
    ended.wait()
    wait = "WAIT S1 no exchange since the install: reply in a DM thread within 60s of a handler's post there, then run this again"
    with state.edit(demo) as data:
        data["jobs"]["j1"]["proof"] = {"pid": ended.pid, "at": state.stamp(T0), "said": f"READY {state.pr_url(YASYF)}", "version": "1.0.1", "waits": wait}
        data["owner"]["heard"] = state.stamp(T0 - timedelta(minutes=5))
    watching = watcher(demo)
    assert [watch.run_pass(watching, T0 + timedelta(minutes=minutes)) for minutes in (0, 31, 62)] == [[], [], []]
    assert provers == []
    watch.hear(demo, T0 + timedelta(minutes=63))
    assert watch.run_pass(watching, T0 + timedelta(minutes=64)) == []
    assert provers == ["j1"]
    assert "waits" not in state.load(demo)["jobs"]["j1"]["proof"]


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


@pytest.mark.parametrize(("refusal", "mergeable"), [(DIRTY, "MERGEABLE"), (MERGE_CONFLICTS, "CONFLICTING"), (MERGE_CONFLICTS, "UNKNOWN")])
def test_a_yasyf_merge_gh_refuses_for_a_conflict_reads_red_and_wakes_its_builder_with_no_stage_line_whatever_github_last_read_of_its_mergeability(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], landers: list[tuple[str, ...]], refusal: str, mergeable: str
) -> None:
    runs: list[list[str]] = []
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: runs.append(argv) or land.subprocess.CompletedProcess(argv, 1, "", refusal + "\n"))
    outcome = land.merged(demo, YASYF, ["--admin"])
    assert outcome == f"not landed: gh pr merge --squash --admin refused it: {refusal}"
    assert runs == [["gh", "pr", "merge", "7", "-R", "yasyf/cc-skills", "--squash", "--admin"]]
    waiting_job(demo)
    tried(demo, outcome)
    serve(monkeypatch, {YASYF: read(mergeable=mergeable)})
    lines = watch.run_pass(watcher(demo), T0)
    assert [line for line in lines if line.startswith("STAGE")] == []
    assert woken == [(YASYF, f"{YASYF} red since {state.stamp(T0)}, owed by its builder: it conflicts with its base main [o1 j1]")]
    assert landers == []


def test_a_yasyf_merge_gh_refuses_for_any_other_reason_is_not_landed_and_reads_unlanded_for_the_session_holding_its_order(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: land.subprocess.CompletedProcess(argv, 1, "", POLICY + "\n"))
    outcome = land.merged(demo, YASYF, ["--admin"])
    assert outcome == f"not landed: gh pr merge --squash --admin refused it: {POLICY}"
    waiting_job(demo)
    tried(demo, outcome)
    assert not watch.wakes(f"PR {YASYF}: {outcome} [o1 j1]", state.load(demo), NO_CARDS)
    assert stage.of(state.load(demo), YASYF, NO_CARDS).name == "unlanded"


def test_a_stacked_yasyf_pr_gh_pr_merge_refuses_merges_through_githubs_merge_async_past_its_rules_and_reads_its_result_until_it_merged(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    stacked_pr = "yasyf/cc-skills#4706"
    refusal = f"GraphQL: This pull request is part of a stack and {land.STACKED}. (mergePullRequest)"
    endpoint = "repos/yasyf/cc-skills/pulls/4706/merge-async"
    pending = {"status": "pending", "details": {"message": "Merge request is in progress.", "uuid": "630b9d5e-3f2a-4f7e-8b0c-2d5f9a8c1e42", "bypass_rules": True}}
    results = iter([pending, {"status": "merged", "details": {"sha": "ccc333ddd444eee"}}])
    merges = iter([(1, "", refusal + "\n"), (0, json.dumps(pending), "")])
    runs: list[list[str]] = []
    reads_: list[list[str]] = []

    def gh(cmd: list[str], **kwargs: object) -> str:
        reads_.append(cmd)
        if cmd[1] == "api" and land.MERGE_ASYNC in cmd[2]:
            return json.dumps(next(results))
        return "ccc333ddd444eee\n" if cmd[2] == "view" else "README.md\n"

    monkeypatch.setattr(github, "run", gh)
    monkeypatch.setattr(land.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(install, "touched", lambda repo, files: [])
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: runs.append(argv) or land.subprocess.CompletedProcess(argv, *next(merges)))
    with state.edit(demo) as data:
        data["prs"][stacked_pr] = state.new_pr(state.pr_url(stacked_pr), "j1", "o1") | {"native": [stacked_pr, "yasyf/cc-skills#4720", "yasyf/cc-skills#4730"]}
    assert land.merged(demo, stacked_pr, ["--admin"]) == "merged at ccc333ddd444; it changes no plugin installed on this Mac"
    assert runs == [
        ["gh", "pr", "merge", "4706", "-R", "yasyf/cc-skills", "--squash", "--admin"],
        ["gh", "api", "-X", "PUT", endpoint, "-f", "merge_method=squash", "-f", "merge_action=direct_merge", "-F", "bypass_rules=true"],
    ]
    assert [cmd for cmd in reads_ if land.MERGE_ASYNC in " ".join(cmd)] == [["gh", "api", f"{endpoint}/630b9d5e-3f2a-4f7e-8b0c-2d5f9a8c1e42"]] * 2
    assert state.load(demo)["prs"][stacked_pr]["merged_sha"] == "ccc333ddd444eee"


def test_a_stacked_pr_githubs_merge_async_ends_failed_is_not_landed_with_githubs_message(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    refusal = f"GraphQL: This pull request is part of a stack and {land.STACKED}. (mergePullRequest)"
    failed = {"status": "failed", "details": {"message": "Required status check \"test\" is expected."}}
    merges = iter([(1, "", refusal + "\n"), (0, json.dumps(failed), "")])
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: land.subprocess.CompletedProcess(argv, *next(merges)))
    head = "aaaaaaa111bbbbbbb222"
    assert land.merged(demo, "Forge-AI/litestream#12", ["--match-head-commit", head]) == f'not landed: GitHub\'s {land.MERGE_ASYNC} ended it failed: Required status check "test" is expected.'


def pushed(name: str, *keys: str) -> None:
    with state.edit(name) as data:
        state.take_result(data, "j1", f"PUSHED {' '.join(map(state.pr_url, keys))}")


def test_a_reported_yasyf_pr_whose_head_moved_past_its_landers_merges_at_the_next_read_free_of_conflicts_with_no_builder_registering_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    tried(demo, f"not landed: gh pr merge --squash --admin refused it: {DIRTY}")
    pushed(demo, YASYF)
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


REPLAYED_4745_4756 = {"yasyf/cc-skills#4745": ("aa8d008", "FAILURE"), "yasyf/cc-skills#4756": ("ed25f62", "FAILURE"), "yasyf/cc-skills#4755": ("1120acd", None)}
SOCKET = {"name": "Socket Security: Pull Request Alerts", "conclusion": "SUCCESS"}


def replayed(demo: str, monkeypatch: pytest.MonkeyPatch, conclusions: dict[str, str | None]) -> None:
    with state.edit(demo) as data:
        for key in REPLAYED_4745_4756:
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"wants_tap": False}
    pushed(demo, *REPLAYED_4745_4756)
    serve(
        monkeypatch,
        {
            key: read(headRefOid=head, checks=[{"name": "test", "conclusion": conclusions.get(key, conclusion)}, SOCKET], required_checks=[], required_reviews=0)
            for key, (head, conclusion) in REPLAYED_4745_4756.items()
        },
    )


def test_reported_yasyf_prs_failing_their_own_checks_never_merge_as_4745_and_4756_did_while_4755_merges_as_its_checks_run(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    replayed(demo, monkeypatch, {})
    watching = watcher(demo)
    for minute in (0, 1, 9):
        watch.run_pass(watching, T0 + timedelta(minutes=minute))
    data = state.load(demo)
    assert [(now := stage.of(data, key, NO_CARDS)).name + ": " + now.why for key in REPLAYED_4745_4756] == [
        "red: its checks fail at aa8d008: test",
        "red: its checks fail at ed25f62: test",
        "landing: lander pid " + str(os.getpid()) + " lands 1120acd, started " + data["prs"]["yasyf/cc-skills#4755"]["lander"]["at"],
    ]
    assert landers == [("yasyf/cc-skills#4755",)]


@pytest.mark.parametrize(("conclusion", "merges"), [(None, True), ("FAILURE", False)])
def test_a_yasyf_lander_rereads_its_checks_and_merges_with_admin_at_that_head_only_while_none_fails(
    demo: str, monkeypatch: pytest.MonkeyPatch, conclusion: str | None, merges: bool
) -> None:
    replayed(demo, monkeypatch, {"yasyf/cc-skills#4755": conclusion})
    runs: list[list[str]] = []
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: runs.append(argv) or land.subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(github, "run", lambda cmd, **_: "390c80f416be\n" if cmd[2] == "view" else "plugins/long-running/hooks/message_limits.py\n")
    monkeypatch.setattr(install, "touched", lambda repo, files: [])
    outcome = land.run(demo, ["yasyf/cc-skills#4755"])
    assert (outcome, runs) == (
        ("merged at 390c80f416be; it changes no plugin installed on this Mac", [["gh", "pr", "merge", "4755", "-R", "yasyf/cc-skills", "--squash", "--admin", "--match-head-commit", "1120acd"]])
        if merges
        else ("not landed: its checks fail at 1120acd: test", [])
    )


def test_a_line_the_coordinator_did_not_get_goes_again_and_says_so_from_the_third_failure(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = posts(monkeypatch, [False, False, False, True])
    watching = watcher(demo)
    line = f"TAP Approve {KEY} [o1 j1]"
    said = [watch.sent(watching, "root", [line] if second == 0 else [], T0 + timedelta(seconds=second)) for second in (0, 5, 10, 15, 20)]
    assert said == [[], [], ["WATCH post to the coordinator failed 3 times in a row: cci post exited 1: no drive demo"], [], []]
    assert [argv[-1] for argv in sent] == [line]


def test_a_line_holding_a_credential_posts_once_with_it_withheld_rather_than_being_refused_on_every_pass(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = posts(monkeypatch)
    watching = watcher(demo)
    line = f"EVENT j1's proof was killed running env ANTHROPIC_API_KEY=sk-ant-{'a1' * 12} probe --live"
    assert (watch.sent(watching, "root", [line], T0), watch.sent(watching, "root", [], T0 + timedelta(seconds=5))) == ([], [])
    assert posted(sent) == [("evidence", f"root@{demo}", "EVENT j1's proof was killed running env ANTHROPIC_API_KEY=<an sk- API key withheld> probe --live")]


def test_an_answer_leaving_a_card_unmarked_or_a_job_unsent_his_words_says_so_every_ten_minutes_until_each_is_marked(
    demo: str, monkeypatch: pytest.MonkeyPatch, send_message: Callable[..., None]
) -> None:
    serve(monkeypatch, {})
    asking(monkeypatch, demo, "demo:j1-7-a")
    with state.edit(demo) as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "Pick demo:j1-7-a?", state.stamp(T0), card=question("demo:j1-7-a")["url"]) | {"waits": "demo:j1-7-a"})
        data["answers"]["a1"] = {"words": "one new tool", "link": f"{CARD}9", "at": state.stamp(T0), "said": state.stamp(T0), "cards": {"demo:j1-7-a": None}, "jobs": {}}
    watching = watcher(demo)

    def said(minutes: int) -> list[str]:
        return [line for line in watch.run_pass(watching, T0 + timedelta(minutes=minutes)) if line.startswith("ANSWER")]

    line = 'ANSWER a1 "one new tool": 1 card with no mark, standing, settled, dropped, superseded or reopened by it; drive answer a1 lists them'
    assert (said(5), said(10), said(15), said(20)) == ([], [line], [], [line])
    assert watch.wakes(line, state.load(demo), NO_CARDS)
    with state.edit(demo) as data:
        data["answers"]["a1"]["cards"]["demo:j1-7-a"] = {"mark": "stands"}
    assert said(40) == []
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(agent="a1", where="local", session="session-under-test")
        data["answers"]["a1"]["jobs"]["j1"] = None
    assert said(50) == ['ANSWER a1 "one new tool": j1 not sent his words; drive answer a1 lists them']
    send_message(demo, "j1", "He wants one new tool")
    assert said(70) == []
    assert state.load(demo)["answers"]["a1"]["jobs"] == {"j1": {"mark": "sent"}}


MONITOR = "https://app.datadoghq.com/monitors/315028134"
O852_MONITOR = "https://app.datadoghq.com/monitors/341360574"
CAP, IMAGE_FIX = "Forge-AI/monorepo#55166", "Forge-AI/monorepo#55225"
DRIVE_CHECKOUT = Path("/drive-checkout")
LATCHED = "j2322 proved it live: Datadog read of monitor 341360574's query: 0 OpenAI /images/edits 400s from principal ci since ac3194dc merged"


def incident_reads(monkeypatch: pytest.MonkeyPatch, statuses: dict[str, str], live: dict[str, bool]) -> list[list[str]]:
    calls = []

    def run(argv: list[str], checkout: Path, env: dict | None = None, stdin: str | None = None) -> str:
        calls.append(argv)
        if argv[0] == "pup":
            return json.dumps([{"id": int(monitor), "status": status} for monitor, status in statuses.items()])
        if argv[1:3] == ["release", "pull"]:
            return json.dumps([{"number": int(number), "liveInProduction": live[number]} for number in argv[3:-1]])
        return ""

    monkeypatch.setattr(watch.reads, "run", run)
    monkeypatch.setattr(watch.orca, "checkout", lambda name, clone: DRIVE_CHECKOUT)
    return calls


def o852(demo: str) -> None:
    with state.edit(demo) as data:
        data["orders"]["o852"] = order(
            text="LLM gateway 400s on image edits", incident=True, thread=["D0C4HQWPLCC", "1794522468.515959"], alerts=[O852_MONITOR], jobs=["j2322", "j2344"], parts={"fix": state.new_part()}
        )
        data["jobs"]["j2322"] = job(order="o852", parts=["fix"], prs=[state.pr_url(CAP)])
        data["jobs"]["j2344"] = job(order="o852", parts=["fix"], prs=[state.pr_url(IMAGE_FIX)], state="failed")
        data["prs"][CAP] = state.new_pr(state.pr_url(CAP), "j2322", "o852") | {"state": "merged", "merged_sha": "ac3194dc"}
        data["prs"][IMAGE_FIX] = state.new_pr(state.pr_url(IMAGE_FIX), "j2344", "o852") | {"state": "merged", "merged_sha": "ad20b74e"}
    with state.edit(demo) as data:
        state.take_result(data, "j2322", f"DONE {LATCHED.removeprefix('j2322 proved it live: ')}")


def test_an_incident_with_no_monitor_held_by_its_open_fix_pr_closes_with_no_datadog_read_once_its_merged_fix_pr_runs_on_each_production_stack(
    demo: str, monkeypatch: pytest.MonkeyPatch, ready_records: list[list[dict]]
) -> None:
    served = {OTHER: read()}
    serve(monkeypatch, served)
    live = {"42": False}
    calls = incident_reads(monkeypatch, {}, live)
    with state.edit(demo) as data:
        data["orders"]["o1"].update(incident=True, parts={"fix": state.new_part()}, jobs=["j1", "j2"])
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["jobs"]["j1"].update(parts=["fix"], state="done")
        data["jobs"]["j2"] = job(parts=["fix"], prs=[state.pr_url(OTHER)])
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o1") | {"head": "aaaaaaa111"}
    watching = watcher(demo)
    assert [line for line in watch.run_pass(watching, T0) if line.startswith("RECOVERED")] == []
    served[OTHER] = read(state="CLOSED")
    with state.edit(demo) as data:
        data["jobs"]["j2"]["state"] = "failed"
        data["prs"][OTHER]["state"] = "closed"
    live["42"] = True
    fixed = f"{state.pr_url(KEY)} live in production"
    recovered = f"RECOVERED o1: {fixed}, so the communicator posts its Live line and closes o1"
    assert [line for line in watch.run_pass(watching, T0 + timedelta(minutes=1)) if line.startswith("RECOVERED")] == [recovered]
    assert not watch.wakes(recovered, state.load(demo), NO_CARDS)
    assert [(record["text"], record["body"]) for records in ready_records for record in records] == [
        (
            "POST incident o1 is over: its fix is live; post the Live line in its thread",
            f"Incident: ship the watch\nFix live: {fixed}\nPRs, as the watch last read them from GitHub: 2\n{shown_pr(demo, KEY)}\n{shown_pr(demo, OTHER)}\n",
        )
    ]
    data = state.load(demo)
    assert (view.incidents(data), data["orders"]["o1"]["proof"]) == ([], fixed)
    assert [argv[1:] for argv in calls if argv[1:3] == ["release", "pull"]] == [["release", "pull", "42", "--json"]]


def test_o761s_incident_on_a_surface_he_uses_holds_its_live_line_with_one_prove_line_until_a_builders_done_keeps_a_recording_from_there(
    demo: str, monkeypatch: pytest.MonkeyPatch, ready_records: list[list[dict]]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(watch.agents, "handle", lambda name, brief: ("h1", False))
    incident_reads(monkeypatch, {}, {"42": True})
    surface, recording = "the Iris chat on a Mac", "/tmp/proofs/o761-chat-answers.mov"
    fixed = f"{state.pr_url(KEY)} live in production"
    with state.edit(demo) as data:
        data["orders"]["o1"].update(incident=True, parts={"fix": state.new_part()}, proof=fixed)
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["jobs"]["j1"].update(parts=["fix"], state="done", brief_flags={"native": surface})
    watching = watcher(demo)
    lines = [line for minute in range(3) for line in watch.run_pass(watching, T0 + timedelta(minutes=minute)) if line.startswith(("PROVE", "RECOVERED"))]
    add = shlex.join(["drive", "job", "add", "builder", "--builds-nothing", "--order", "o1", "--part", "fix", "--goal", f"prove {state.pr_url(KEY)} live on {surface} with a recording of it"])
    assert lines == [
        f"PROVE o1: {fixed}, but o1 builds what he uses on {surface}, so its Live line waits on a recording or live post from there: add the builder"
        f" that proves it, {add}, brief it with --native and --interactions for {surface} and start it; its DONE keeps that recording as the proof"
        " of o1, and the watch then closes o1"
    ]
    data = state.load(demo)
    assert (watch.wakes(lines[0], data, NO_CARDS), ready_records, data["orders"]["o1"]["recovered"], state.may_tell(data, data["orders"]["o1"])) == (True, [], None, False)
    with state.edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(parts=["fix"], brief_flags={"native": surface})
    with state.edit(demo) as data:
        state.take_result(data, "j2", "DONE the Iris chat answers again", f"DONE the Iris chat answers again\nCHECK: {recording}\n")
    data = state.load(demo)
    assert (data["orders"]["o1"]["proof"], state.may_tell(data, data["orders"]["o1"])) == (f"{fixed}; {recording}", True)
    assert [line for line in watch.run_pass(watching, T0 + timedelta(minutes=3)) if line.startswith(("PROVE", "RECOVERED"))] == [
        f"RECOVERED o1: {fixed}, so the communicator posts its Live line and closes o1"
    ]
    assert state.load(demo)["orders"]["o1"]["proof"] == f"{fixed}; {recording}"


def test_an_incident_whose_drive_checkout_cannot_move_to_the_default_branch_head_closes_nothing_and_says_so_from_the_third_failed_read(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    calls = incident_reads(monkeypatch, {}, {"42": True})

    def unmoved(name: str, clone: str) -> Path:
        raise state.DriveError("the drive's checkout was not moved to the head of its default branch")

    monkeypatch.setattr(watch.orca, "checkout", unmoved)
    with state.edit(demo) as data:
        data["orders"]["o1"].update(incident=True, parts={"fix": state.new_part()})
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["jobs"]["j1"].update(parts=["fix"], state="done")
    watching = watcher(demo)
    lines = [line for minute in range(3) for line in watch.run_pass(watching, T0 + timedelta(minutes=minute)) if line.startswith(("RECOVERED", "WATCH production landing"))]
    assert (lines, calls, state.load(demo)["orders"]["o1"]["recovered"]) == (
        ["WATCH production landing read failed 3 times in a row: the drive's checkout was not moved to the head of its default branch"],
        [],
        None,
    )

def test_o852s_close_in_record_682673_replayed_stays_open_with_its_five_minute_line_while_its_monitor_reads_ok_and_closes_once_both_fix_prs_run_in_production(
    demo: str, monkeypatch: pytest.MonkeyPatch, ready_records: list[list[dict]]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(watch.agents, "handle", lambda name, brief: ("h1", False))
    o852(demo)
    live = {"55166": True, "55225": False}
    calls = incident_reads(monkeypatch, {"341360574": "OK"}, live)
    watching = watcher(demo)
    assert [line for line in watch.run_pass(watching, T0) if line.startswith("RECOVERED")] == []
    data = state.load(demo)
    assert (data["jobs"]["j2322"]["state"], "fixed" in data["orders"]["o852"], data["orders"]["o852"]["recovered"]) == ("done", False, None)
    assert "o852" in [entry["id"] for entry in view.incidents(data)]
    assert [line for line in watch.run_pass(watching, T0 + timedelta(minutes=6)) if line.startswith("RECOVERED")] == []
    data = state.load(demo)
    topics = [record["topic"] for records in ready_records for record in records]
    assert (data["orders"]["o852"]["proof"], bool(data["orders"]["o852"]["quiet"]), "watch-recovered-o852" in topics) == (None, True, False)
    live["55225"] = True
    fixed = f"{state.pr_url(CAP)}, {state.pr_url(IMAGE_FIX)} live in production"
    closed_at = T0 + timedelta(minutes=7)
    assert [line for line in watch.run_pass(watching, closed_at) if line.startswith("RECOVERED")] == [
        f"RECOVERED o852: {O852_MONITOR} read OK and {fixed}, so the communicator posts its Live line and closes o852"
    ]
    assert state.load(demo)["orders"]["o852"]["proof"] == f"{O852_MONITOR} read OK at {render.when(state.stamp(closed_at))}; {fixed}"
    assert [argv for argv in calls if argv[1:3] == ["release", "pull"]] == [[str(DRIVE_CHECKOUT / "tools" / "ci"), "release", "pull", "55166", "55225", "--json"]] * 3


def test_o852_the_deleted_latch_closed_with_its_fix_not_live_is_back_on_the_strip_once_drive_json_loads_and_a_closed_incident_keeps_its_close(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    o852(demo)
    closed_at = state.stamp(T0 - timedelta(minutes=1))
    with state.edit(demo) as data:
        data["orders"]["o852"].update(fixed=LATCHED, recovered=closed_at, proof=f"{O852_MONITOR} read OK at 5:45pm; {LATCHED}")
        data["orders"]["o1"].update(incident=True, state="done", fixed=LATCHED, recovered=closed_at, proof="read OK")
    incident_reads(monkeypatch, {"341360574": "OK"}, {"55166": True, "55225": False})
    data = state.load(demo)
    assert ("fixed" in data["orders"]["o852"], data["orders"]["o852"]["recovered"], data["orders"]["o852"]["proof"]) == (False, None, None)
    assert ("fixed" in data["orders"]["o1"], data["orders"]["o1"]["recovered"], data["orders"]["o1"]["proof"]) == (False, closed_at, "read OK")
    assert "o852" in [entry["id"] for entry in view.incidents(data)]
    assert [line for line in watch.run_pass(watcher(demo), T0) if line.startswith("RECOVERED")] == []


@pytest.mark.parametrize(
    ("status", "live", "part", "closes"),
    [("Alert", True, "fix", False), ("OK", False, "fix", False), ("OK", True, "stop", False), ("OK", True, "fix", True)],
)
def test_an_incident_closes_once_its_monitor_reads_ok_and_its_merged_fix_pr_runs_on_each_production_stack_moving_the_follow_ups_of_each_of_its_orders_to_a_plain_order(
    demo: str, monkeypatch: pytest.MonkeyPatch, ready_records: list[list[dict]], status: str, live: bool, part: str, closes: bool
) -> None:
    reads = {OTHER: read()}
    serve(monkeypatch, reads)
    calls = incident_reads(monkeypatch, {"315028134": status}, {"42": live})
    with state.edit(demo) as data:
        parts = {name: state.new_part() for name in ("fix", "stop", "cause", "follow-up")}
        data["orders"]["o1"].update(incident=True, thread=["D123", "1794200000.000100"], note="record 5f6b2b5", alerts=[MONITOR], jobs=["j1", "j2"], parts=parts)
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["jobs"]["j1"].update(kind="codex", parts=[part], state="done", updated=state.stamp(T0 - timedelta(minutes=10)))
        data["jobs"]["j2"] = job(parts=["follow-up"], prs=[state.pr_url(OTHER)])
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o1") | {"head": "aaaaaaa111"}
        data["orders"]["o2"] = order(text="also the migration check should not stop a sand that can read", jobs=[], incident=True, joins="o1", parts={"the migration check": state.new_part()})
    watching = watcher(demo)
    lines = [line for line in watch.run_pass(watching, T0) if line.startswith("RECOVERED")]
    data = state.load(demo)
    searches = [argv for argv in calls if argv[0] == "pup"]
    assert [argv[argv.index("--query") + 1] for argv in searches] == ["id:(315028134)"] * (live and part == "fix")
    if not closes:
        assert (lines, ready_records, data["orders"]["o1"]["recovered"], data["jobs"]["j2"]["order"]) == ([], [], None, "o1")
        assert [entry["id"] for entry in view.incidents(data)] == ["o1", "o2"]
        return
    fixed = f"{state.pr_url(KEY)} live in production"
    assert lines == [f"RECOVERED o1: {MONITOR} read OK and {fixed}, so the communicator posts its Live line and closes o1, o2; its live jobs, open PRs and parts left moved to the plain order o3"]
    assert ready_records == [
        [
            {
                "kind": "report",
                "to": ["communicator@demo"],
                "topic": "watch-recovered-o1",
                "attr": {"close": "o1,o2", "thread": "1794200000.000100"},
                "text": "POST incident o1 is over: its monitors read OK and its fix is live; post the Live line in its thread",
                "body": f"Incident: ship the watch\nRecovered: {MONITOR} read OK\nFix live: {fixed}\nPRs, as the watch last read them from GitHub: 2\n{shown_pr(demo, KEY)}\n{shown_pr(demo, OTHER)}\n",
            }
        ]
    ]
    incident, joined, follow = data["orders"]["o1"], data["orders"]["o2"], data["orders"]["o3"]
    assert (list(incident["parts"]), incident["jobs"], incident["recovered"]) == (["fix", "stop", "cause"], ["j1"], state.stamp(T0))
    assert (list(joined["parts"]), joined["recovered"]) == ([], state.stamp(T0))
    assert incident["proof"] == f"{MONITOR} read OK at {render.when(state.stamp(T0))}; {fixed}"
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
    monkeypatch.setattr(watch.agents, "handle", lambda name, brief: ("h1", False))
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=1)) if line.startswith("RECOVERED")]
    assert ready_records[1:] == []
    reads[OTHER] = read(headRefOid="bbbbbbb222")
    watch.run_pass(watching, T0 + watch.QUIET_THREAD)
    assert ([argv[1:3] if argv[0] != "pup" else ["pup"] for argv in calls], [records[0]["attr"] for records in ready_records[1:]]) == (
        [["release", "pull"], ["pup"]],
        [{"order": "o3", "progress": "true", "quiet": "true", "thread": "1794200000.000100"}],
    )


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
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: posts.append(records) or ["9"])
    second = watcher(demo)
    assert staged(watch.close_report_lines, second, T0 + timedelta(minutes=3)) == []
    assert (posts[3:], state.load(demo)["orders"]["o1"]["close_report"]) == ([[CLOSE_REPORT]], None)
    assert staged(watch.close_report_lines, second, T0 + timedelta(minutes=4)) == []
    assert len(posts) == 4


def test_the_watchs_close_report_is_its_orders_one_close_so_the_cci_post_hook_refuses_a_second_while_it_waits_and_once_it_is_posted(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import record_lines
    from drive import tools

    serve(monkeypatch, {})
    with state.edit(demo) as data:
        data["orders"]["o1"]["close_report"] = CLOSE_REPORT
    meta = {
        "session_id": "session-under-test",
        "tool_name": "mcp__plugin_cc-inbox_cci__post",
        "tool_input": {"kind": "report", "to": ["communicator@demo"], "attrs": {"close": "o1"}, "text": "POST captain-hook 12.121.76 is installed"},
    }
    waiting = record_lines.decision(meta)
    assert (waiting["permissionDecision"], waiting["permissionDecisionReason"]) == (
        "deny",
        "refused, report install-proved-j1, which the watch posts on its next read, already closes o1, so this second close of it posts nothing:"
        " send no close of o1, and a handler passed this one returns NOTHING o1 closes on report install-proved-j1",
    )
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: ["688113"])
    assert staged(watch.close_report_lines, watcher(demo), T0) == []
    data = state.load(demo)
    assert (data["orders"]["o1"]["closed_on"], data["orders"]["o1"]["close_report"]) == ("688113", None)
    assert record_lines.decision(meta)["permissionDecisionReason"].startswith("refused, report 688113 already closes o1, so this second close of it posts nothing")
    watchs = {"close": "o1", "seq": "688113"}
    assert (state.second_close(data, "o1", watchs), cli.claim_close(demo, watchs)) == (None, [])


@pytest.mark.parametrize("count", [1, 100])
def test_the_watch_reads_where_every_open_incidents_merged_fix_prs_run_in_one_release_pull_and_their_monitors_in_one_search_a_pass(
    demo: str, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    serve(monkeypatch, {})
    numbers = [str(1000 + number) for number in range(count)]
    calls = incident_reads(monkeypatch, {}, dict.fromkeys(numbers, True))
    with state.edit(demo) as data:
        for number in numbers:
            key = f"Forge-AI/monorepo#{number}"
            data["orders"][f"o{number}"] = order(text=f"alert {number}", jobs=[f"j{number}"], incident=True, alerts=[f"https://app.datadoghq.com/monitors/{number}"], parts={"fix": state.new_part()})
            data["jobs"][f"j{number}"] = job(order=f"o{number}", parts=["fix"], state="done", prs=[state.pr_url(key)])
            data["prs"][key] = state.new_pr(state.pr_url(key), f"j{number}", f"o{number}") | {"state": "merged"}
    watching = watcher(demo)
    for minute in range(2):
        watch.run_pass(watching, T0 + timedelta(minutes=minute))
    assert [argv[1:] for argv in calls if argv[1:3] == ["release", "pull"]] == [["release", "pull", *numbers, "--json"]] * 2
    assert [argv[argv.index("--query") + 1] for argv in calls if argv[0] == "pup"] == [f"id:({' OR '.join(numbers)})"] * 2


def pr_card(*keys: str, url: str = CARD, head: str = "aaaaaaa111") -> dict:
    return {"url": url, "prs": [state.pr_url(key) for key in keys], "asked": state.stamp(T0), "card": {"title": "Fix: the watch", "prs": [{"url": state.pr_url(key), "head": head} for key in keys]}}


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


def test_a_pr_github_closes_and_reopens_settles_his_card_at_the_close_read_and_is_read_while_closed_so_its_reopen_redraws_that_card_in_place(
    demo: str, monkeypatch: pytest.MonkeyPatch, redraws: Redraws
) -> None:
    url = state.pr_url(KEY)
    with state.edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
    shown, shut = [pr_card(KEY)], []
    settled = {"waits": "merge:Forge-AI:monorepo:42", "title": "Fix: the watch", "prs": [url], "asked": state.stamp(T0), "url": CARD, "answer": None, "tapped": "", "replaced_by": ""}
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [], "closed": list(shut), "pr_cards": list(shown)})
    monkeypatch.setattr(watch.slack, "withdraw_pr_card", lambda pr, why: shown.clear() or shut.append(settled) or [CARD])
    reads = {KEY: read(checks=GREEN, reviewDecision="APPROVED")}
    calls = serve(monkeypatch, reads)
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert (redraws.asked, state.load(demo)["prs"][KEY]["stage"]["name"]) == ([url], "card")
    reads[KEY] = read(state="CLOSED", checks=GREEN, reviewDecision="APPROVED")
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == [f"PR {KEY}: closed [o1 j1]", f"CARD {CARD} settled in place: {KEY} closed"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=2)) == []
    assert (redraws.asked, state.load(demo)["prs"][KEY]["stage"]["name"]) == ([url], "closed")
    reads[KEY] = read(checks=GREEN, reviewDecision="APPROVED")
    reopened = watch.run_pass(watching, T0 + timedelta(minutes=3))
    data = state.load(demo)
    assert (reopened, watch.wakes(reopened[0], data, NO_CARDS)) == ([f"PR {KEY}: open [o1 j1]"], False)
    assert (redraws.asked, data["prs"][KEY]["state"], data["prs"][KEY]["stage"]["name"]) == ([url, url], "open", "uncarded")
    assert calls == [[KEY]] * 4
    shown[:], shut[:] = [pr_card(KEY)], []
    watch.run_pass(watching, T0 + timedelta(minutes=4))
    assert state.load(demo)["prs"][KEY]["stage"]["name"] == "card"


def test_his_card_cc_slack_keeps_closed_in_place_on_a_pr_that_asks_his_tap_again_reads_uncarded_and_is_redrawn_in_place_never_drawn_anew(
    demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws, redraws: Redraws
) -> None:
    url = state.pr_url(KEY)
    with state.edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
    shut = {"waits": "merge:Forge-AI:monorepo:42", "title": "Fix: the watch", "prs": [url], "asked": state.stamp(T0), "url": CARD, "answer": None, "tapped": "This PR no longer waits on your tap.", "replaced_by": ""}
    shown = {"team_url": "", "questions": [], "closed": [shut], "pr_cards": []}
    monkeypatch.setattr(watch.view, "cards", lambda drive: shown)
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    data = state.load(demo)
    assert (data["prs"][KEY]["stage"]["name"], redraws.asked, draws.keys) == ("uncarded", [url], [])
    assert data["prs"][KEY]["stage"]["why"] == "his card on it is closed, since Approve came off it while it waited on no tap, so the watch redraws it in place"
    assert view.wait_words(data, KEY, shown) == f"{view.pr_link(data['prs'][KEY])} lands"
    shown.update(closed=[], pr_cards=[pr_card(KEY)])
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    data = state.load(demo)
    assert (data["prs"][KEY]["stage"]["name"], draws.keys) == ("card", [])
    assert view.wait_words(data, KEY, shown) == f"{view.pr_link(data['prs'][KEY])} lands, which waits on your Approve on {view.link('its card', CARD)}"


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


def test_his_open_card_that_left_a_show_me_picture_off_redraws_in_place_once_github_reads_the_body_edited_since_its_draw(
    demo: str, monkeypatch: pytest.MonkeyPatch, redraws: Redraws
) -> None:
    why = 'monorepo PR 42: the Show me picture "The fork snapshot share" does not draw: node "stream": a store node needs "medium", one of memory, disk, bucket, keys, pages, so his card went out without it'
    with state.edit(demo) as data:
        data["prs"][KEY]["undrawn"] = {"why": why, "at": state.stamp(T0)}
    shown = {"team_url": "", "questions": [], "closed": [], "pr_cards": []}
    monkeypatch.setattr(watch.view, "cards", lambda drive: shown)
    before, after = "2026-11-09T14:59:00Z", "2026-11-09T15:02:00Z"
    reads = {KEY: read(lastEditedAt=after)}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    shown["pr_cards"] = [pr_card(KEY)]
    reads[KEY] = read(lastEditedAt=before)
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    assert redraws.asked == []
    reads[KEY] = read(lastEditedAt=after)
    watch.run_pass(watching, T0 + timedelta(minutes=2))
    assert redraws.asked == [state.pr_url(KEY)]


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
    assert watch.wakes(line, state.load(demo), NO_CARDS)
    tooling_order(demo, "running")
    assert watch.holder_line(line, state.load(demo)) == "meta"


def test_an_answer_posted_to_him_whose_asker_has_not_heard_it_is_said_once_ten_minutes_after_his_line(demo: str) -> None:
    with state.edit(demo) as data:
        data["orders"]["o1"] |= {"kind": "answer", "reporters": [state.reporter(REPORTED)], "updated": state.stamp(T0 - timedelta(hours=5))}
    watching = watcher(demo)
    assert watch.tell_lines(watching, T0 + timedelta(minutes=40)) == []
    with state.edit(demo) as data:
        for part in data["orders"]["o1"]["parts"].values():
            part["posted"] = "https://acme.slack.com/archives/D123/p1794200300000100"
    assert watch.tell_lines(watching, T0 + timedelta(minutes=9)) == []
    line = (
        f"TELL o1: its answer is posted and nothing moved on it for 10 minutes, but {REPORTED}, the thread its question was asked in,"
        " has not heard the answer: send its report with --attr close=o1, whose handler posts the answer there and records it with"
        " drive order told o1 <its permalink>; done refuses until then"
    )
    assert watch.tell_lines(watching, T0 + timedelta(minutes=10)) == [line]
    assert watch.tell_lines(watching, T0 + timedelta(minutes=40)) == []


def test_a_fix_proven_live_whose_bug_report_has_not_heard_it_is_fixed_is_said_once_ten_minutes_on(demo: str) -> None:
    with state.edit(demo) as data:
        data["orders"]["o1"] |= {"reporters": [state.reporter(REPORTED)], "proof": "https://buildkite.com/forge/release/builds/14185"}
    watching = watcher(demo)
    assert watch.tell_lines(watching, T0 + timedelta(minutes=9)) == []
    line = (
        "TELL o1: its fix is proven live by https://buildkite.com/forge/release/builds/14185 and nothing moved on it for 10 minutes,"
        f" but {REPORTED}, the thread its bug was reported in,"
        " has not heard it is fixed: send its report with --attr close=o1, whose handler posts the fix-live note there and records it"
        " with drive order told o1 <its permalink>; done refuses until then"
    )
    assert watch.tell_lines(watching, T0 + timedelta(minutes=10)) == [line]
    assert watch.tell_lines(watching, T0 + timedelta(minutes=40)) == []
    assert watch.wakes(line, state.load(demo), NO_CARDS)
    tooling_order(demo, "running")
    assert watch.holder_line(line, state.load(demo)) == "meta"


def test_a_fix_proven_live_names_every_reporters_thread_not_yet_told_on_one_tell_and_a_reporter_added_later_on_its_own(demo: str) -> None:
    second, later = "https://acme.slack.com/archives/C0REPORTS02/p1794593225751189", "https://acme.slack.com/archives/C0REPORTS03/p1794600000000100"
    told = state.reporter("https://acme.slack.com/archives/C0REPORTS04/p1794500000000100") | {"told": "https://acme.slack.com/archives/C0REPORTS04/p1794500060000100"}
    with state.edit(demo) as data:
        data["orders"]["o1"] |= {"reporters": [state.reporter(REPORTED), told, state.reporter(second)], "proof": "https://buildkite.com/forge/release/builds/14185"}
    watching = watcher(demo)
    assert watch.tell_lines(watching, T0 + timedelta(minutes=10)) == [
        "TELL o1: its fix is proven live by https://buildkite.com/forge/release/builds/14185 and nothing moved on it for 10 minutes,"
        f" but {REPORTED} and {second}, the threads its bug was reported in, have not heard it is fixed: send its report with"
        " --attr close=o1, whose handler posts the fix-live note in each and records it with drive order told o1 <its permalink>;"
        " done refuses until then"
    ]
    with state.edit(demo) as data:
        data["orders"]["o1"]["reporters"].append(state.reporter(later))
    assert watch.tell_lines(watching, T0 + timedelta(minutes=40)) == [
        "TELL o1: its fix is proven live by https://buildkite.com/forge/release/builds/14185 and nothing moved on it for 10 minutes,"
        f" but {later}, the thread its bug was reported in, has not heard it is fixed: send its report with --attr close=o1, whose"
        " handler posts the fix-live note there and records it with drive order told o1 <its permalink>; done refuses until then"
    ]
    assert watch.tell_lines(watching, T0 + timedelta(minutes=70)) == []


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


FOOTER = "Forge-AI/monorepo#53645"
FOOTER_STACKED = "Forge-AI/monorepo#53648"
READERS_FIRST = "it fails readers-first"
ON_HIS_APPROVE = {"name": "card", "owner": stage.HIM, "why": "green, waits on his Approve", "since": state.stamp(T0), "woke": None}
RED_STAGE = {"name": "red", "owner": stage.BUILDER, "why": READERS_FIRST, "since": state.stamp(T0), "woke": state.stamp(T0)}
REPLAYED_53519_CHAIN = (
    f"{KEY} red, owed by its builder: {READERS_FIRST}; j1 waiting on j2; j2 waiting on {FOOTER}, {FOOTER_STACKED};"
    f" {FOOTER} card, owed by him: green, waits on his Approve; {FOOTER_STACKED} card, owed by him: green, waits on his Approve"
)
WATCHDOG_ANSWER = f"WHY: {READERS_FIRST}, and j2 waits on his Approve of {FOOTER}\nWHO: him\nNEXT: tap Approve on {FOOTER}\nUNREAD: nothing\n"


@pytest.fixture
def watchdogs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Path]]:
    """Each watchdog agent the watch starts, by its brief and answer file; one runs until its answer file is written."""
    started: list[tuple[str, Path]] = []
    monkeypatch.setattr(watch.agents, "watchdog", lambda name, brief, cwd, out: started.append((brief, out)) or len(started))
    monkeypatch.setattr(watch.state, "alive", lambda pid: not started[pid - 1][1].exists())
    monkeypatch.setattr(watch.agents, "handle", lambda name, brief: ("h1", False))
    return started


def stalling(name: str) -> watch.Watcher:
    with state.edit(name) as data:
        data["orders"]["o1"]["jobs"] = ["j1", "j2"]
        data["jobs"]["j1"] |= {"state": "waiting", "waits": ["j2"]}
        data["jobs"]["j2"] = job(goal="the release footer", state="waiting", waits=[FOOTER, FOOTER_STACKED], prs=[state.pr_url(FOOTER), state.pr_url(FOOTER_STACKED)])
        data["prs"][KEY]["stage"] = RED_STAGE
        for key in (FOOTER, FOOTER_STACKED):
            data["prs"][key] = state.new_pr(state.pr_url(key), "j2", "o1") | {"stage": ON_HIS_APPROVE}
    watching = watcher(name)
    watching.cards = NO_CARDS
    return watching


def watchdog_answered(out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"I read the checks and both transcripts.\n\n```text\n{WATCHDOG_ANSWER}```\n")


def test_replayed_53519_a_red_pr_behind_a_builder_waiting_on_his_card_gets_one_watchdog_at_an_hour_one_stuck_line_and_one_line_to_him_30_minutes_on(
    demo: str, watchdogs: list[tuple[str, Path]], ready_records: list[list[dict]]
) -> None:
    watching = stalling(demo)
    said = {minutes: watch.stall_lines(watching, T0 + timedelta(minutes=minutes)) for minutes in (0, 59, 60, 61)}
    assert said == {0: [], 59: [], 60: [], 61: []}
    [(brief, out)] = watchdogs
    assert brief.splitlines()[:7] == [
        f"Stall: {KEY}, unmoved 60 minutes",
        "Job: j1 of o1: ship the watch",
        "Holder: the coordinator, session session-under-test",
        f"Chain: {REPLAYED_53519_CHAIN}",
        "Drive: demo",
        "Read:",
        f"- drive status o1 j1 {KEY} j2 {FOOTER} {FOOTER_STACKED} --drive demo",
    ]
    assert {f"- gh pr checks {state.pr_url(KEY)}", f"- gh pr checks {state.pr_url(FOOTER)}"} <= set(brief.splitlines())
    watchdog_answered(out)
    assert watch.stall_lines(watching, T0 + timedelta(minutes=68)) == [
        f"STUCK j1 o1: {KEY} unmoved 68 minutes: {REPLAYED_53519_CHAIN}. WHY: {READERS_FIRST}, and j2 waits on his Approve of {FOOTER}; NEXT: tap Approve on {FOOTER};"
        f" its whole answer, WHO and UNREAD with it, in {out}"
    ]
    for minutes in (69, 97, 98, 150):
        assert watch.stall_lines(watching, T0 + timedelta(minutes=minutes)) == []
    [[report]] = ready_records
    assert (report["attr"], report["to"]) == ({"order": "o1", "job": "j1", "stall": "true"}, ["communicator@demo"])
    assert report["text"] == f"POST {KEY} has not moved in 98 minutes and the coordinator was told 30 minutes ago: post one line on it in its thread"
    assert ["WHO: him", f"NEXT: tap Approve on {FOOTER}"] == [line for line in report["body"].splitlines() if line.startswith(("WHO:", "NEXT:"))]
    with state.edit(demo) as data:
        data["prs"][KEY]["stage"] = RED_STAGE | {"why": "it fails readers-first at bbbbbbb"}
    watch.stall_lines(watching, T0 + timedelta(minutes=151))
    kept = state.load(demo)["prs"][KEY]["stall"]
    assert (kept["since"], kept["agent"], kept["said"]) == (state.stamp(T0 + timedelta(minutes=151)), None, None)
    assert [brief.splitlines()[0] for brief, _ in watchdogs] == [f"Stall: {KEY}, unmoved 60 minutes", "Stall: j1, unmoved 151 minutes"]


def test_a_stall_on_an_order_the_meta_session_holds_reaches_it_in_one_hop_and_its_watchdog_reads_it_and_wakes_its_agent_by_its_sendmessage(
    demo: str, monkeypatch: pytest.MonkeyPatch, watchdogs: list[tuple[str, Path]], ready_records: list[list[dict]]
) -> None:
    watching = stalling(demo)
    tooling_order(demo, "waiting")
    with state.edit(demo) as data:
        data["roles"]["meta"] = {"session": "meta-session", "terminal": None, "cwd": "/meta", "started": state.stamp(T0)}
        data["jobs"]["j1"] |= {"agent": "agent-j1", "where": "local", "session": "meta-session"}
    for minutes in (0, 60):
        watch.stall_lines(watching, T0 + timedelta(minutes=minutes))
    [(brief, out)] = watchdogs
    lines = brief.splitlines()
    assert (
        "Holder: the meta session, session meta-session; j1 runs on this Mac as agent agent-j1 of session meta-session; to wake it:"
        " SendMessage it the message verbatim from meta@demo, the session meta-session that started its agent agent-j1"
    ) in lines
    assert f"- cc-transcript {watch.session_transcript(state.load(demo)['roles']['meta'])}, from {watch.when(state.stamp(T0))} on" in lines
    assert "session-under-test" not in brief
    watchdog_answered(out)
    [stuck] = watch.stall_lines(watching, T0 + timedelta(minutes=68))
    sent = posts(monkeypatch)
    assert watch.handed(watching, [stuck], T0 + timedelta(minutes=68)) == []
    assert posted(sent) == [("handoff", f"meta@{demo}", stuck)]
    watch.stall_lines(watching, T0 + timedelta(minutes=98))
    [[report]] = ready_records
    assert report["text"] == f"POST {KEY} has not moved in 98 minutes and the meta session was told 30 minutes ago: post one line on it in its thread"


def test_a_sessions_transcript_is_read_from_the_folder_it_first_ran_in_after_its_role_moved_to_the_drives_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    first = tmp_path / "projects" / "-Users-me-src-monorepo" / "s-1.jsonl"
    first.parent.mkdir(parents=True)
    first.write_text("{}\n")
    moved = {"session": "s-1", "cwd": "/Users/me/worktrees/monorepo/drive-demo"}
    assert watch.session_transcript(moved) == first
    assert watch.session_transcript(moved | {"session": "s-2"}) == tmp_path / "projects" / "-Users-me-worktrees-monorepo-drive-demo" / "s-2.jsonl"


def test_a_stall_the_coordinator_sat_on_reaches_him_once_he_is_back_never_while_he_is_away(
    demo: str, watchdogs: list[tuple[str, Path]], ready_records: list[list[dict]]
) -> None:
    watching = stalling(demo)
    said_at = T0 + timedelta(minutes=68)
    with state.edit(demo) as data:
        data["owner"]["away"] = True
        data["prs"][KEY]["stall"] = {
            "print": REPLAYED_53519_CHAIN,
            "since": state.stamp(T0),
            "agent": {"pid": 1, "out": "/s/stall.md", "at": state.stamp(T0 + timedelta(minutes=60))},
            "found": {"WHY": READERS_FIRST},
            "said": state.stamp(said_at),
            "told": None,
        }
    for minutes in (98, 140):
        assert watch.stall_lines(watching, T0 + timedelta(minutes=minutes)) == []
    assert ready_records == []
    with state.edit(demo) as data:
        data["owner"]["away"] = False
    for minutes in (141, 142):
        watch.stall_lines(watching, T0 + timedelta(minutes=minutes))
    assert [record["text"] for [record] in ready_records] == [f"POST {KEY} has not moved in 141 minutes and the coordinator was told 73 minutes ago: post one line on it in its thread"]
    assert watchdogs == []


@pytest.mark.parametrize(("kept", "thread"), [([["D123", "1794439546.145729", state.stamp(T0), "question"]], "1794439546.145729"), ([], None)])
def test_a_stall_on_an_order_from_a_channel_thread_names_the_thread_of_his_dm_it_keeps_never_that_channel_thread(
    demo: str, watchdogs: list[tuple[str, Path]], ready_records: list[list[dict]], kept: list[list[str]], thread: str | None
) -> None:
    watching = stalling(demo)
    with state.edit(demo) as data:
        data["owner"]["away"] = False
        data["orders"]["o1"].update(thread=["C0B75AL4XEW", "1794436805.002349"], threads=[["C0B75AL4XEW", "1794438322.192619", state.stamp(T0), "posted"], *kept])
        data["prs"][KEY]["stall"] = {
            "print": REPLAYED_53519_CHAIN,
            "since": state.stamp(T0),
            "agent": {"pid": 1, "out": "/s/stall.md", "at": state.stamp(T0 + timedelta(minutes=60))},
            "found": {"WHY": READERS_FIRST},
            "said": state.stamp(T0 + timedelta(minutes=68)),
            "told": None,
        }
    watch.stall_lines(watching, T0 + timedelta(minutes=141))
    [[record]] = ready_records
    assert (record["attr"]["stall"], record["attr"].get("thread")) == ("true", thread)


def test_a_stall_on_a_drive_started_from_a_terminal_with_no_communicator_starts_its_handler_beside_the_coordinator_once(
    demo: str, monkeypatch: pytest.MonkeyPatch, ready_records: list[list[dict]], posts_to_communicator: list[str]
) -> None:
    ran: list[tuple[list[str], Path]] = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None, stdin=None: ran.append((argv, checkout)) or "")
    watching = stalling(demo)
    told_at = T0 + timedelta(minutes=98)
    with state.edit(demo) as data:
        data["prs"][KEY]["stall"] = {
            "print": REPLAYED_53519_CHAIN,
            "since": state.stamp(T0),
            "agent": {"pid": 1, "out": "/s/stall.md", "at": state.stamp(T0 + timedelta(minutes=60))},
            "found": {"WHY": READERS_FIRST},
            "said": state.stamp(T0 + timedelta(minutes=68)),
            "told": None,
        }
    coordinator = state.load(demo)["roles"]["coordinator"]
    assert "communicator" not in state.load(demo)["roles"]
    assert watch.stall_lines(watching, told_at) == []
    [[report]] = ready_records
    assert (report["text"], report["to"]) == (f"POST {KEY} has not moved in 98 minutes and the coordinator was told 30 minutes ago: post one line on it in its thread", [f"communicator@{demo}"])
    [(argv, checkout)] = ran
    assert (argv[:4], checkout) == (["claude", "--bg", "--agent", "long-running:handler"], Path(coordinator["cwd"]))
    assert json.loads(argv[argv.index("--settings") + 1])["env"]["CCI_PARENT_SESSION"] == coordinator["session"]
    assert argv[-1] == f"Record: 9 Drive: {demo} Scratch: {state.drive_dir(demo) / 'scratch' / 'stall-9'}"
    data = state.load(demo)
    assert ([entry["holds"]["record"] for entry in data["handlers"].values()], data["prs"][KEY]["stall"]["told"]) == (["9"], state.stamp(told_at))
    posts_to_communicator.append("9")
    assert watch.post_lines(watching, told_at) == []
    assert watch.stall_lines(watching, told_at + timedelta(minutes=1)) == []
    assert (len(ready_records), len(ran)) == (1, 1)


def test_the_watch_starts_a_handler_on_each_post_report_to_a_communicator_its_drive_lacks_once_and_reads_again_one_that_did_not_start(
    demo: str, monkeypatch: pytest.MonkeyPatch, posts_to_communicator: list[str]
) -> None:
    ran: list[list[str]] = []
    unread: list[str] = []
    refused = {"697102"}

    def run(argv: list[str], checkout: Path, env: dict | None = None, stdin: str | None = None) -> str:
        if argv[:2] == ["claude", "--bg"] and argv[-1].split()[1] in refused:
            raise reads.ReadError("claude --bg: workspace not trusted")
        ran.append(argv)
        return ""

    monkeypatch.setattr(reads, "run", run)
    monkeypatch.setattr(watch.agents, "unread", lambda name, seq: unread.append(seq))
    watching = watcher(demo)
    posts_to_communicator += ["697061", "697102", "697160"]
    assert watch.post_lines(watching, T0) == []
    assert ([argv[-1] for argv in ran], unread) == ([f"Record: 697061 Drive: {demo} Scratch: {state.drive_dir(demo) / 'scratch' / 'post-697061'}"], ["697102"])
    refused.clear()
    posts_to_communicator += ["697102", "697160"]
    assert watch.post_lines(watching, T0 + timedelta(seconds=5)) == []
    assert [argv[-1].split()[1] for argv in ran] == ["697061", "697102", "697160"]
    assert sorted(entry["holds"]["record"] for entry in state.load(demo)["handlers"].values()) == ["697061", "697102", "697160"]
    with state.edit(demo) as data:
        data["roles"]["communicator"] = {"session": "communicator-session", "terminal": None, "cwd": "/work", "started": state.stamp(T0)}
    posts_to_communicator.append("697200")
    assert watch.post_lines(watching, T0 + timedelta(seconds=10)) == []
    assert (len(ran), posts_to_communicator) == (3, ["697200"])


def test_at_most_two_watchdog_agents_run_at_once_oldest_stall_first(demo: str, watchdogs: list[tuple[str, Path]]) -> None:
    release = f"release:sand:{OTHER}"
    with state.edit(demo) as data:
        data["orders"]["o1"]["jobs"] = ["j1", "j2", "j3"]
        data["jobs"] |= {"j2": job(), "j3": job(state="waiting", waits=[release])}
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1")
    watching = watcher(demo)
    watching.cards = NO_CARDS
    watch.stall_lines(watching, T0)
    with state.edit(demo) as data:
        for job_id in ("j1", "j2"):
            data["jobs"][job_id] |= {"state": "waiting", "waits": [release]}
    for minutes in (5, 65, 66):
        watch.stall_lines(watching, T0 + timedelta(minutes=minutes))
    assert len(watchdogs) == 2
    for _, out in watchdogs:
        watchdog_answered(out)
    said = watch.stall_lines(watching, T0 + timedelta(minutes=67))
    assert [line.split(":")[0] for line in said] == ["STUCK j3 o1", "STUCK j1 o1"]
    assert len(watchdogs) == 2
    watch.stall_lines(watching, T0 + timedelta(minutes=68))
    assert [brief.splitlines()[0] for brief, _ in watchdogs] == ["Stall: j3, unmoved 65 minutes", "Stall: j1, unmoved 60 minutes", "Stall: j2, unmoved 63 minutes"]


def test_a_stalls_watchdog_is_handed_the_watchs_own_read_of_each_release_wait_his_hold_and_his_cards_naming_the_service_it_waits_on(
    demo: str, monkeypatch: pytest.MonkeyPatch, watchdogs: list[tuple[str, Path]], recorded: Answers
) -> None:
    wait = f"release:sand-next:{KEY}"
    recorded.listed = [{"id": "15c4de1aa", "title": "Are releases frozen?", "body": "no releases until I say we can again", "tags": ["release-hold"], "created_at": "2026-11-13T19:10:39Z"}]
    asked = "When should dashboard, sand-next and sand ship?"
    on_it = {"question": asked, "options": ["Now", "Later"], "recommended": "Now", "asked": state.stamp(T0), "url": CARD, "card": {"title": asked, "body": ""}}
    elsewhere = {"question": "Which store should the sand CLI read?", "options": ["Team", "Run"], "recommended": "Team", "asked": state.stamp(T0), "url": f"{CARD[:-3]}200", "card": {"title": "Which store?", "body": "sandsql"}}
    with state.edit(demo) as data:
        data["prs"][KEY].update(state="merged", merged_sha="bd434a0f1b2c3d4e")
        data["jobs"]["j1"] |= {"state": "waiting", "waits": [wait]}
    watching = watcher(demo)
    watching.cards = NO_CARDS | {"questions": [on_it, elsewhere]}
    watching.production = {KEY: {"number": 42, "liveInProduction": False, "productionBehind": [{"component": "sand-next", "env": "plat-usw2-prod"}, {"component": "api", "env": "tnt-usw2-0ddq7rb"}]}}
    for minutes in (0, 60):
        watch.stall_lines(watching, T0 + timedelta(minutes=minutes))
    [(brief, _)] = watchdogs
    assert [line for line in brief.splitlines() if line.startswith(("Release:", "Hold:", "Card:"))] == [
        f"Release: {wait}: tools/ci release pull reads sand-next on plat-usw2-prod behind {KEY}'s merge commit bd434a0f1b2c, running an older build or a release still running there",
        'Hold: he holds every release, by his words "no releases until I say we can again" (15c4de1): a release runs only on an answer of his recorded since that approves that one release',
        f'Card: "{asked}" {CARD}, asked by no job of the drive',
    ]


def test_a_watchdogs_next_asking_its_holder_for_a_release_step_the_release_check_refuses_reaches_the_stuck_line_and_him_as_that_refusal(
    demo: str, watchdogs: list[tuple[str, Path]], ready_records: list[list[dict]], jev: Jev
) -> None:
    watching = stalling(demo)
    for minutes in (0, 60):
        watch.stall_lines(watching, T0 + timedelta(minutes=minutes))
    [(_, out)] = watchdogs
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("WHY: no sand-next release carries it\nWHO: the holder\nNEXT: start a sand-next production release now\nUNREAD: nothing\n")
    jev.picks("asks")
    [stuck] = watch.stall_lines(watching, T0 + timedelta(minutes=68))
    assert f"; NEXT: {watch.cut(watch.brief.RELEASE_UNNAMED, 200)}; its whole answer" in stuck
    watch.stall_lines(watching, T0 + timedelta(minutes=98))
    [[report]] = ready_records
    assert f"NEXT: {watch.brief.RELEASE_UNNAMED}" in report["body"].splitlines()


def test_watchdog_agents_count_those_still_searching_and_those_started_within_the_hour() -> None:
    def kept(started: int, said: bool) -> dict:
        return {"stall": {"agent": {"pid": 1, "out": "/s/a.md", "at": state.stamp(T0 - timedelta(minutes=started))}, "said": state.stamp(T0) if said else None}}

    data = {"jobs": {"j1": kept(5, False), "j2": kept(12, False), "j3": kept(30, True), "j4": {"stall": {"agent": None, "said": None}}, "j5": {"stall": None}}, "prs": {KEY: kept(61, True)}}
    assert stall.starts(data, T0) == (1, 3)


@pytest.mark.parametrize(
    ("stops", "subjects"),
    [
        (lambda data: None, [KEY, "j1"]),
        (lambda data: data["prs"][KEY].update(hold_label=True), ["j1"]),
        (lambda data: data["prs"][KEY].update(stage=ON_HIS_APPROVE), ["j1"]),
        (lambda data: data["orders"]["o1"].update(due=state.stamp(T0 + timedelta(hours=2))), []),
        (lambda data: data["jobs"].update(j2=job()) or data["jobs"]["j1"].update(waits=["j2"]), []),
    ],
    ids=["unstopped", "hold label", "his card", "due ahead", "running job"],
)
def test_no_chain_is_aged_past_his_hold_his_card_a_time_to_come_or_a_running_job(demo: str, stops: Callable[[dict], None], subjects: list[str]) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"] |= {"state": "waiting", "waits": [f"release:sand:{OTHER}"]}
        data["prs"][KEY]["stage"] = RED_STAGE
        stops(data)
    assert list(stall.chains(state.load(demo), NO_CARDS, T0, lambda order_id: True)) == subjects
