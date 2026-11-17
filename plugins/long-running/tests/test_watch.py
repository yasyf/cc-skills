import fcntl
import functools
import io
import json
import os
import plistlib
import re
import shlex
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import (
    DRIVE_TERMINAL,
    HOME,
    LEAD,
    REAL_SCRATCH,
    RESEARCH,
    SHORT_ASK,
    Answers,
    CodexAsk,
    Draws,
    EnvsRelease,
    HomeBuild,
    HomeRepo,
    Jev,
    Judge,
    MonorepoPaths,
    NamedRepo,
    Receiver,
    Redraws,
    SlotStaging,
    Thread,
    WatchProcess,
    Withdrawals,
    codex_reply,
    commit_files,
    dequeuer_exited,
    drawn_card,
    edit,
    git_in,
    lander_exited,
    own_time,
    provide,
    said_lines,
    staged,
)
from drive import (
    agents,
    card,
    cli,
    codex,
    dashboard,
    github,
    install,
    land,
    launchd,
    limits,
    orca,
    owing,
    place,
    providers,
    reads,
    receiver,
    render,
    served,
    settled,
    slack,
    stage,
    stall,
    state,
    superseded,
    talk,
    tools,
    train,
    view,
    watch,
    work,
)

KEY = "Forge-AI/monorepo#42"
OTHER = "Forge-AI/monorepo#43"
YASYF = "yasyf/cc-skills#7"
STAGING_ENV = "plat-usw2-staging"
RELEASED = {"release": "tools/ci release start sandsql --emergency --env plat-usw2-staging --from c5dca80ed9be --order 23eefb9"}
REPLAYED_5547 = "yasyf/cc-skills#5547"
REPLAYED_5442 = "yasyf/cc-skills#5442"
HEAD_5547 = "0106ac2aaa"
BASE_5547 = "j5396-unstarted-jobs"
LEGACY = "ssql-litestream-legacy"
BASE_MOVED = "GraphQL: Base branch was modified. Review and try the merge again. (mergePullRequest)"
DIRTY = f"X Pull request {YASYF} is not mergeable: the merge commit cannot be cleanly created."
MERGE_CONFLICTS = "GraphQL: Pull Request has merge conflicts (mergePullRequest)"
POLICY = f"X Pull request {YASYF} is not mergeable: the base branch policy prohibits the merge."
ROWS = "| `drive a` | runs a. Then b. Then c. |\n| `drive d` | runs d. |\n"
T0 = state.parse_time("2026-11-09T10:00:00-05:00")
LOADER_AT, DEFAULT_HEAD = providers.at, providers.default_head
GATE = "LTX Behavioral Gate (short)"
RUNNING = [{"name": "test", "conclusion": None}]
GREEN = [{"name": "test", "conclusion": "SUCCESS"}]
RED = [{"name": "test", "conclusion": "FAILURE"}]
CHECKOUT_FAILED = [{"name": "Set up job", "conclusion": "success"}, {"name": "Run actions/checkout@3d3c42e5", "conclusion": "failure"}, {"name": "Label the PR", "conclusion": "failure"}]
TEST_FAILED = [{"name": "Set up job", "conclusion": "success"}, {"name": "Run actions/checkout@3d3c42e5", "conclusion": "success"}, {"name": "Run tests", "conclusion": "failure"}]
ASKED = "Should Iris, desktop and the sand CLI show sign-in when an api session expires?"
REPORTED = "https://acme.slack.com/archives/C0REPORTS01/p1794400100000200"
BOX_GOAL = "test runs on the new tree on the devbox under key o2-codex-box, round after round"
UNRECORDED = 'ORPHAN o1 "ship the watch": j1\'s agent finished and its result is not recorded: "'
REPORT_IT = "; only drive job report j1 - sets it, or names why it refuses, so wake it to pipe its return there"
WOKEN_BY_ITS_SESSION = ": drive job resume j1 with this line verbatim"
CHECKED = {"head": "aaaaaaa111", "read": "aaaaaaa111", "orders": ["o1"], "lines": {}, "beside": {}}
BUILD = "https://buildkite.com/forge/monorepo/builds/7"
REQUIRED = {"context": "buildkite/test", "targetUrl": BUILD}
TEAMMATE_THREAD = "1794500000.000300"
ASKED_TS = f"{T0.timestamp():.6f}"
ASKED_TEAMMATE = f"https://acme.slack.com/archives/C0TEAM0001/p{ASKED_TS.replace('.', '')}"
STAGING_TOPIC = "o78-staging-collision"
NO_BUILDER = "no builder carries this work, only "
READ = ("reader", "done")
PROVEN = "DONE ship it live: repro/j9.sh exits 0 on long-running@skills 1.0.4"
REPLAYED_685794 = [
    (685713, None, "Incident dd-334827919: API Envoy idle-socket 503s on agent asks", True, [READ], False, None),
    (685714, None, "status of every bug", True, [READ], True, None),
    (685715, None, "status of shipping the automatic split of large tables", True, [READ], True, None),
    (685716, None, "What's status of splitting agents into their own runners", True, [READ], False, None),
    (685717, None, "status of the File redesign", True, [READ], True, None),
    (685718, None, "Why closed?", True, [READ, ("builder", "stopped")], False, f"{NO_BUILDER}reader j2 done, builder j3 stopped: "),
    (685719, None, "why the files changes took all day", True, [], True, None),
    (685720, None, "And if they don't land for whatever reason still message the reviewer", True, [("reader", "waiting")], False, None),
    (685721, None, "What is a restore, and why did it take 5m?", True, [READ], False, None),
    (685722, None, "measure today's release against 5 to 7 minutes and name every step past it", True, [READ], True, None),
    (685723, None, "why a release like the last hotfix is not self-serve, and what makes it so", False, [("reader", "waiting")], True, None),
    (685724, None, "Why so we have a lazy setting at all it should be the same everywhere", True, [("design", "done"), ("builder", "stopped")], False, f"{NO_BUILDER}design j2 done, builder j3 stopped: "),
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
    (685738, "build", "auto partitioning on the same abstraction", True, [READ, ("design", "done")], True, f"{NO_BUILDER}reader j2 done, design j3 done: "),
    (685739, "build", "auto partitioning status", True, [("design", "done")], True, f"{NO_BUILDER}design j2 done: "),
    (685740, None, "bring me design options to make forks and restores as close to free as possible", True, [READ, ("design", "waiting"), ("builder", "waiting")], False, "j4 waits on"),
    (685741, "build", "increase the search worker's RAM", True, [], True, "no job"),
    (685742, "build", "answer: did we cap each indexed entry to 1024 tokens as the plan said", True, [READ, READ], True, f"{NO_BUILDER}reader j2 done, reader j3 done: "),
    (685743, "build", "look into S3's per-prefix request rate limiting", True, [READ], True, f"{NO_BUILDER}reader j2 done: "),
    (685744, None, "#54749 lands", True, [READ], True, None),
    (685745, None, "his pick on #52894 and #53584", False, [("reader", "waiting")], True, None),
    (685746, "build", "are we doing the ssql release foe it", True, [], True, "no job"),
    (685747, "build", "the release's npm publishing of @poeticapps/poetic removed for now", True, [("builder", "failed"), READ], True, "j2 failed"),
    (685748, "build", "a card asking whether to tell the reporter it is live", True, [READ, READ], True, f"{NO_BUILDER}reader j2 done, reader j3 done: "),
    (685749, None, "why a chat's subagents do not persist across a restart, answered in his thread", True, [READ], True, None),
    (685750, None, "status of the fixes", True, [READ], True, None),
    (685751, "build", "first check", True, [("codex", "failed")], True, "j2 failed"),
    (685752, "build", "> `_app_action_invocations` tables in 15 minutes", True, [READ], True, f"{NO_BUILDER}reader j2 done: "),
    (685753, "build", "what happened to the lost wiki pages and whether they can be recovered", False, [READ], False, f"{NO_BUILDER}reader j2 done: "),
    (685754, "build", "options for holding failed writes and replaying them", False, [READ], False, f"{NO_BUILDER}reader j2 done: "),
    (685755, "build", "first check", True, [READ], True, f"{NO_BUILDER}reader j2 done: "),
    (685756, "build", "status of the scratch and run-local databases, and whether a customer's runs could", True, [READ], True, f"{NO_BUILDER}reader j2 done: "),
    (685757, "build", "how the prompts and the compiler push big reads to a scratch database", True, [READ], True, f"{NO_BUILDER}reader j2 done: "),
    (685758, "build", "whether production runs still fail on SandSQL now, and which runs the stall", True, [READ], True, f"{NO_BUILDER}reader j2 done: "),
    (685759, "build", "fix this too", False, [READ, ("reader", "waiting")], False, f"{NO_BUILDER}reader j2 done, reader j3 waiting: "),
    (685760, "build", "a release plans restate on plat-usw2-prod past the route check", False, [("reader", "waiting")], False, f"{NO_BUILDER}reader j2 waiting: "),
    (685761, "build", "list the rest for me here", True, [], True, "no job"),
]


ENQUEUE = Path(".agents/skills/submit-pr/scripts/stack-enqueue")


def proven(data: dict, job_id: str = "j1", **fields: object) -> None:
    data["jobs"][job_id].update(state="done", said=PROVEN, **fields)
    state.deliver_proven(data, job_id, PROVEN.removeprefix("DONE "))


def order(**overrides: object) -> dict:
    base = {
        "text": "ship the watch",
        "from": "terminal",
        "thread": None,
        "state": "working",
        "jobs": ["j1"],
        "incident": False,
        "every": 0,
        "recovered": None,
        "reporters": [],
        "owner": None,
        "project": None,
        "priority": None,
        "title": None,
        "proof": None,
        "proof_by": None,
        "close_report": None,
        "closed_on": None,
        "note": None,
        "created": state.stamp(T0),
        "updated": state.stamp(T0),
        "last_line": state.stamp(T0),
    }
    return {**base, **overrides}


def job(**overrides: object) -> dict:
    base = {"order": "o1", "kind": "builder", "goal": "watch", "prs": [], "branch": None, "waits": [], "questions": [], "state": "running", "said": None, "created": state.stamp(T0), "updated": state.stamp(T0), "cut_misses": {}, "watch_said": [], "undelivered": []}
    return {**base, **overrides}


@pytest.fixture
def demo(state_root: Path) -> str:
    data = state.new_drive("demo", "coordinator", "session-under-test", "/work")
    data["orders"]["o1"] = order()
    data["jobs"]["j1"] = job(prs=[state.pr_url(KEY)])
    data["prs"][KEY] = state.new_pr(state.pr_url(KEY), "j1", "o1") | {"head": "aaaaaaa111", "base": "main", "title": "Old title", "wants_tap": False, "checked": CHECKED}
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
        "reviews": {},
        "mergeable": "MERGEABLE",
        "title": "Fix: the watch",
        "author": "yasyf",
        "labels": [],
        "ejected_at": None,
        "cancelled_at": None,
        "native": [],
        "trunk": None,
        "required_checks": ["buildkite/test"],
        "required_reviews": 1,
        "merge_method": "squash",
        "permission": "ADMIN",
        "default": "main",
        "landing_bases": [],
        "under": None,
        "base_ahead": None,
        "base_pr": None,
        "signature": "kept",
    }
    return {**base, **overrides}


def serve(monkeypatch: pytest.MonkeyPatch, reads: dict[str, dict]) -> list[list[str]]:
    calls = []

    def fake(keys: list[str], into: dict[str, str] | None = None) -> tuple[dict[str, dict], github.GithubError | None]:
        calls.append(keys)
        return {key: reads[key] for key in keys if key in reads}, None

    monkeypatch.setattr(github, "fetch", fake)
    remerging(monkeypatch, reads)
    return calls


def remerging(monkeypatch: pytest.MonkeyPatch, reads: dict[str, dict]) -> list[list[str]]:
    calls = []

    def fake(keys: list[str]) -> tuple[dict[str, dict], github.GithubError | None]:
        calls.append(keys)
        return {key: {field: reads[key][field] for field in ("state", "headRefOid", "mergeable")} for key in keys if key in reads}, None

    monkeypatch.setattr(github, "mergeability", fake)
    return calls


def watcher(name: str) -> watch.Watcher:
    return watch.Watcher(name, offset=state.events_end(name))


def deliver(event: str, payload: dict, repo: str = "Forge-AI/monorepo") -> None:
    receiver.home().mkdir(parents=True, exist_ok=True)
    receiver.took(repo, 9, "g1", event, payload)


def checked(number: int) -> dict:
    return {"check_run": {"head_sha": "zzz", "pull_requests": [{"number": number}]}}


@pytest.fixture(autouse=True)
def ready_records(monkeypatch: pytest.MonkeyPatch) -> list[list[dict]]:
    from drive import tools

    sent: list[list[dict]] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: sent.append(records) or ["9"])
    return sent


class LiveRelease(EnvsRelease):
    """The monorepo's release reads, its live read run in the checkout the drive's watch moves, as the watch's own read did."""

    def live(self) -> list[dict]:
        checkout = orca.checkout("demo", str(self.clone), wait=0)
        return json.loads(reads.run([str(checkout / "tools" / "ci"), "release", "live", "--json"], checkout))


@pytest.fixture(autouse=True)
def monorepo_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every drive's providers the monorepo's fixture ones, each on the drive's clone, unless a test names its own by provide()."""

    def built(clone: Path) -> providers.Providers:
        roles = HOME | {"repo": MonorepoPaths, "release": LiveRelease, "staging": SlotStaging}
        return providers.Providers(None, {}, **{role: made(clone, clone, {}) for role, made in roles.items()})

    monkeypatch.setattr(providers, "of", lambda data: built(Path(next(iter(state.bound_sessions(data).values()))["cwd"])))
    monkeypatch.setattr(providers, "at", lambda clone, checkout=None: built(clone))


class HomePaths(MonorepoPaths, HomeRepo):
    """The HomeRepo fake with the monorepo's path reads, so its dangerous, CI config and checkout tool paths stay the monorepo's."""


class NamedPaths(MonorepoPaths, NamedRepo):
    """The monorepo's path reads under its name, with no merge queue."""


@pytest.fixture
def named_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every drive's providers the monorepo_providers ones under the monorepo's name, so its PRs' staging rows and commit links name it."""

    def built(clone: Path) -> providers.Providers:
        roles = HOME | {"repo": NamedPaths, "release": LiveRelease, "staging": SlotStaging}
        return providers.Providers(None, {}, **{role: made(clone, clone, {}) for role, made in roles.items()})

    monkeypatch.setattr(providers, "of", lambda data: built(Path(next(iter(state.bound_sessions(data).values()))["cwd"])))
    monkeypatch.setattr(providers, "at", lambda clone, checkout=None: built(clone))


@pytest.fixture
def home_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    """The drive's repository the HomeRepo fake with the monorepo's path reads, its release reads LiveRelease's on the drive's clone, over the HOME fakes, with no PR reaching any release unless the test reads its reach itself."""

    def built(clone: Path) -> providers.Providers:
        roles = HOME | {"repo": HomePaths, "release": LiveRelease}
        return providers.Providers(None, {}, **{role: made(clone, clone, {}) for role, made in roles.items()})

    monkeypatch.setattr(providers, "of", lambda data: built(Path(next(iter(state.bound_sessions(data).values()))["cwd"])))
    monkeypatch.setattr(providers, "at", lambda clone, checkout=None: built(clone))
    monkeypatch.setattr(providers, "default_head", lambda clone: "0" * 40)
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    monkeypatch.setattr(github, "files", lambda key: [])


@pytest.fixture(autouse=True)
def unposted_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    """talk.report_line refusing every report, as the drive does for one cci cannot read, so no test reads cci or asks Sonnet for a post; a test of a post stubs it."""

    def unposted(name: str, seq: str) -> str:
        raise state.DriveError(f"record {seq} could not be read: no cci in tests")

    monkeypatch.setattr(talk, "report_line", unposted)


@pytest.fixture(autouse=True)
def closed_cards(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str, bool]]:
    closes: list[tuple[str, str, str, bool]] = []
    monkeypatch.setattr(superseded, "answer_card", lambda card, words, link, settled, replaces=None: closes.append((card, words, link, settled)) or {})
    return closes


REAL_ENSURE = watch.ensure
REAL_FETCH = github.fetch
REAL_MERGEABILITY = github.mergeability
REAL_RUN_PASS = watch.run_pass
REAL_SESSION_ENV = launchd.session_env


def test_the_watch_reads_the_changed_files_of_each_head_no_read_saw_once_and_stages_each_pr_whose_head_changes_a_dangerous_class(
    home_repo: None,
    demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / state.REPO_RULES).write_text('[[dangerous]]\nclass = "a builtin"\npaths = ["go/sand/internal/builtin/*"]\n')
    with edit(demo) as data:
        data["roles"]["coordinator"]["cwd"] = str(tmp_path)
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"head": "ccccccc333"}
    asked: list[list[str]] = []
    changed = {KEY: ("aaaaaaa111", ["go/sand/internal/builtin/http.go"]), OTHER: ("ccccccc333", ["api/src/run.ts"])}
    monkeypatch.setattr(github, "paths", lambda keys: asked.append(keys) or {key: changed[key] for key in keys})
    [line] = watch.dangerous_lines(watcher(demo), T0)
    data, part = state.load(demo), state.STAGING_TEST.format(number=42)
    (tester_id,) = [job_id for job_id, job in data["jobs"].items() if state.builds_nothing(job)]
    assert line.startswith(f'PR {KEY}: {tester_id} builder on "{part}"') and line.endswith(" [o1 j1]")
    assert (asked, data["prs"][KEY]["waits"], data["prs"][OTHER]["waits"], data["prs"][OTHER]["paths_at"]) == ([[KEY, OTHER]], [f"o1:{part}"], [], "ccccccc333")
    assert "changes a builtin: go/sand/internal/builtin/http.go, each a dangerous change" in data["jobs"][tester_id]["goal"]
    assert (watch.dangerous_lines(watcher(demo), T0), asked) == ([], [[KEY, OTHER]])
    with edit(demo) as data:
        data["prs"][OTHER]["head"] = "ddddddd444"
    changed[OTHER] = ("ddddddd444", ["go/sand/internal/builtin/fetch.go"])
    [line] = watch.dangerous_lines(watcher(demo), T0)
    assert (asked[-1], state.load(demo)["prs"][OTHER]["waits"]) == ([OTHER], [f"o1:{state.STAGING_TEST.format(number=43)}"])


def test_the_watch_takes_each_pr_the_retired_reach_staged_off_its_test_unless_it_is_dangerous_whoever_took_it_up_so_his_approve_stands(
    home_repo: None, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifted, kept = state.STAGING_TEST.format(number=42), state.STAGING_TEST.format(number=43)
    approve = state.new_tap("Approve", "aaaaaaa111", "slack", at=state.stamp(T0), card=[KEY])
    with edit(demo) as data:
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"head": "ccccccc333"}
        for key in (KEY, OTHER):
            state.wait_on_staging(data, key)
            data["prs"][key]["reached"] = True
        data["jobs"]["j2"] = state.new_job("o1", "builder", "test #42 on staging", [lifted], state.stamp(T0)) | {"agent": "a2"}
        data["prs"][KEY]["tap"] = approve
    asked: list[list[str]] = []
    changed = {KEY: ["dashboard/src/iris/components/Sidebar.tsx"], OTHER: ["go/sand/next/internal/runtime-api/memory/routes.go"]}
    monkeypatch.setattr(github, "files", lambda key: asked.append(key) or changed[key])
    [line] = watch.reach_lines(watcher(demo), T0)
    data = state.load(demo)
    assert line == f"PR {KEY}: {state.UNDANGEROUS_MERGES.format(key=KEY)}; its files: dashboard/src/iris/components/Sidebar.tsx, and j2 heard its part left {watch.ids(data['prs'][KEY])}"
    assert (lifted in data["orders"]["o1"]["parts"], data["prs"][KEY]["waits"], data["prs"][KEY]["reached"], data["prs"][KEY]["tap"], data["jobs"]["j2"]["state"] != "stopped") == (False, [], False, approve, True)
    assert (kept in data["orders"]["o1"]["parts"], data["prs"][OTHER]["waits"], data["prs"][OTHER]["reached"]) == (True, [f"o1:{kept}"], False)
    assert (watch.reach_lines(watcher(demo), T0), asked) == ([], [KEY, OTHER])


def test_the_watch_classifies_a_head_through_a_provider_the_loader_executes_from_its_commit_whose_class_has_no_file_inspect_can_read(
    demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clone = tmp_path / "clone"
    (clone / ".claude" / "long-running").mkdir(parents=True)
    (clone / ".claude" / "long-running.toml").write_text('[providers]\nmodule = ".claude/long-running/providers.py"\nrepo = "Repo"\n')
    (clone / ".claude" / "long-running" / "providers.py").write_text(
        "from drive.providers import RepoProvider\n\n\nclass Repo(RepoProvider):\n    name = 'Forge-AI/monorepo'\n\n    def dangerous_paths(self, paths):\n        return [path for path in paths if '/builtins/' in path]\n"
    )
    for argv in (["init", "-q", "-b", "main"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "providers"], ["update-ref", "refs/remotes/origin/HEAD", "HEAD"]):
        subprocess.run(["git", "-C", str(clone), *argv], check=True)
    monkeypatch.setattr(providers, "default_head", DEFAULT_HEAD)
    loaded = LOADER_AT(clone)
    monkeypatch.setattr(providers, "of", lambda data: loaded)
    monkeypatch.setattr(state, "repo_config", lambda data: {})
    with edit(demo) as data:
        data["roles"]["coordinator"]["cwd"] = str(clone)
    changed = {KEY: ("aaaaaaa111", ["go/sand/internal/vm/builtins/browserpkg/extract.go"])}
    monkeypatch.setattr(github, "paths", lambda keys: {key: changed[key] for key in keys if key in changed})
    [line] = watch.dangerous_lines(watcher(demo), T0)
    assert line.startswith(f"PR {KEY}: ") and state.load(demo)["prs"][KEY]["paths_by"] == state.classifier(state.load(demo))


def test_a_head_read_under_an_older_classifier_is_read_again_once_the_classifier_changes_as_57562s_was_not(home_repo: None, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    changed = {KEY: ("aaaaaaa111", ["go/sand/next/internal/vm/builtins/browserpkg/extract.go"])}
    monkeypatch.setattr(github, "paths", lambda keys: {key: changed[key] for key in keys if key in changed})
    monkeypatch.setattr(state, "classifier", lambda data: "old")
    monkeypatch.setattr(state, "repo_config", lambda data: {})
    monkeypatch.setattr(HomePaths, "dangerous_paths", lambda self, paths: [])
    assert watch.dangerous_lines(watcher(demo), T0) == []
    assert (state.load(demo)["prs"][KEY]["paths_by"], state.unmarked(state.load(demo))) == ("old", [])
    monkeypatch.setattr(state, "classifier", lambda data: "new")
    monkeypatch.setattr(HomePaths, "dangerous_paths", lambda self, paths: [path for path in paths if "/builtins/" in path])
    assert KEY in state.unmarked(state.load(demo))
    [line] = watch.dangerous_lines(watcher(demo), T0)
    assert line.startswith(f"PR {KEY}: ") and state.load(demo)["prs"][KEY]["waits"] == [f"o1:{state.STAGING_TEST.format(number=42)}"]


def test_a_pr_whose_head_changes_a_path_the_repository_providers_dangerous_paths_names_is_staged_with_no_dangerous_table_as_57530_and_57528_were_not(
    home_repo: None, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BuiltinsRepo(HomeRepo):
        def dangerous_paths(self, paths: list[str]) -> list[str]:
            return [path for path in paths if "/builtins/" in path]

    provide(monkeypatch, Path(), repo=BuiltinsRepo)
    changed = {KEY: ("aaaaaaa111", ["go/sand/next/internal/vm/builtins/browserpkg/extract.go", "go/sand/next/internal/iris/run_pages.go"])}
    monkeypatch.setattr(github, "paths", lambda keys: {key: changed[key] for key in keys if key in changed})
    [line] = watch.dangerous_lines(watcher(demo), T0)
    data, part = state.load(demo), state.STAGING_TEST.format(number=42)
    (tester_id,) = [job_id for job_id, job in data["jobs"].items() if state.builds_nothing(job)]
    assert line.startswith(f'PR {KEY}: {tester_id} builder on "{part}"')
    assert data["prs"][KEY]["waits"] == [f"o1:{part}"]
    assert f"changes {state.PROVIDER_DANGEROUS}: go/sand/next/internal/vm/builtins/browserpkg/extract.go, each a dangerous change" in data["jobs"][tester_id]["goal"]


@pytest.mark.parametrize("wants_tap", [False, True])
@pytest.mark.parametrize("job_state", ["running", "waiting"])
def test_a_prs_moves_are_said_whatever_its_jobs_state_and_wake_no_one_while_the_watch_draws_his_card_on_one_waiting_on_his_tap(
    demo: str, monkeypatch: pytest.MonkeyPatch, job_state: str, wants_tap: bool, draws: Draws
) -> None:
    with edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = wants_tap
        data["jobs"]["j1"]["state"] = job_state
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks=GREEN, reviewDecision="APPROVED")})
    lines = watch.run_pass(watcher(demo), T0)
    assert lines == [f"PR {KEY}: new head bbbbbbb [o1 j1]", f"PR {KEY}: checks passed [o1 j1]", f"PR {KEY}: approved [o1 j1]"]
    assert not any(watch.wakes(line, state.load(demo), NO_CARDS) for line in lines)
    data = state.load(demo)
    pr = data["prs"][KEY]
    assert (pr["head"], pr["checks"], pr["review"], pr["title"]) == ("bbbbbbb222", "pass", "approved", "Fix: the watch")
    assert (draws.keys, view.tap_card(data, pr, view.live_cards(demo)), pr["stage"]["name"]) == ([[KEY]] * wants_tap, draws.link if wants_tap else None, "uncarded" if wants_tap else "author")


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
REPLAYED_54904 = "Forge-AI/monorepo#54904"
STACKED_BASE = "durable-agents-j1389-send-user-question"
ON_TRUNK = "dev, the trunk of its native stack"
CONFLICTS_WITH_TRUNK = f"it conflicts with {ON_TRUNK}: the watch restacks it, so a conflict left is a real one: resolve it with ccx vcs stack rebase onto dev"


def test_replayed_55220_a_conflicting_pr_pushed_one_ahead_of_its_base_waits_on_his_approve_while_github_reads_unknown_and_turns_red_once_it_answers_conflicting(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    conflicting_at = T0 + timedelta(minutes=2)
    with edit(demo) as data:
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
        data["prs"][REPLAYED_54904] = state.new_pr(state.pr_url(REPLAYED_54904), "j1", "o1") | {"wants_tap": False}
    native = [(key, "open") for key in REPLAYED_STACK_55221]
    pushed = {"headRefOid": "c388588e73", "baseRefName": STACKED_BASE, "under": REPLAYED_54904, "checks": GREEN, "reviewDecision": "APPROVED", "native": native, "trunk": "dev", "default": "dev"}
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
REPLAYED_STACK_55221 = (REPLAYED_54026, "Forge-AI/monorepo#54916", REPLAYED_54904, *REPLAYED_UNKNOWN)
REPLAYED_HOLDING_CARD = "demo:j850-684800"


def test_replayed_54026_every_pr_above_the_lowest_pr_of_its_stack_conflicting_with_its_base_is_behind_it_owed_by_its_owner_and_wakes_no_builder_above_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    waiting_job(demo)
    asking(monkeypatch, demo, REPLAYED_HOLDING_CARD, taps=[pr_card(*REPLAYED_STACK_55221)])
    with edit(demo) as data:
        for key in REPLAYED_STACK_55221:
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"wants_tap": True, "checked": CHECKED}
        data["prs"][REPLAYED_54026]["parts"] = ["the inbox wake"]
        data["jobs"]["j1"]["questions"] = [state.new_question(None, "Does the inbox wake it?", state.stamp(T0), holds="the inbox wake, on #54026") | {"waits": REPLAYED_HOLDING_CARD}]
    native = [(key, "open") for key in REPLAYED_STACK_55221]
    branches = ["durable-agents-j850", "durable-agents-j1389-a", STACKED_BASE, "durable-agents-j2279-a", "durable-agents-j2279-b", "durable-agents-j2279-c"]
    stacked = {
        key: {"baseRefName": base, "headRefName": branch, "under": under, "checks": GREEN, "reviewDecision": "APPROVED", "native": native, "trunk": "dev", "default": "dev"}
        for key, base, branch, under in zip(REPLAYED_STACK_55221, ["dev", *branches[:-1]], branches, [None, *REPLAYED_STACK_55221[:-1]], strict=True)
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
    with edit(demo) as data:
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
    with edit(demo) as data:
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


def test_a_pushed_on_a_repository_whose_rules_require_no_check_settles_at_its_first_read_beside_a_check_failing_on_its_own_change_red_and_owed_by_its_builder(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], woken: list[tuple[str, str]]
) -> None:
    url = state.pr_url(YASYF)
    with edit(demo) as data:
        del data["prs"][KEY]
        data["prs"][YASYF] = state.new_pr(url, "j1", "o1") | {"head": "aaaaaaa111", "wants_tap": False, "checked": CHECKED}
        data["jobs"]["j1"].update(state="waiting", said=f"PUSHED {url}", prs=[url])
    serve(monkeypatch, {YASYF: read(checks=RED, required_checks=[])})
    said = watch.run_pass(watcher(demo), T0 + timedelta(minutes=1))
    own = "its own change fails test at aaaaaaa, which the default branch does not fail on the same tests where the PR's run of it merged its head"
    assert [f"PR {YASYF}: checks failed [o1 j1]", f"JOB j1 o1 READY {url}; {YASYF} is at aaaaaaa on GitHub, red, owed by its builder: {own}"] == [line for line in said if line.startswith((f"PR {YASYF}", "JOB j1"))]
    assert (state.load(demo)["jobs"]["j1"]["said"], landers, [key for key, _ in woken]) == (f"READY {url}", [], [YASYF])


def test_a_red_required_check_ends_a_pushed_with_its_build_and_its_red_stage_wakes_its_builder_not_the_coordinator(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    with edit(demo) as data:
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
    with edit(demo) as data:
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


def test_a_pr_merged_into_another_open_prs_branch_reads_folded_holding_its_wait_and_its_job_takes_done_with_that_prs_merge_commit_once_it_lands(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    folded = read(state="MERGED", mergeCommit="ccc333", baseRefName="the-base")
    asked = []

    def fetch(parent: dict) -> None:
        monkeypatch.setattr(github, "fetch", lambda keys, into=None: asked.append((keys, into)) or ({KEY: folded | {"base_pr": parent | {"key": OTHER}}}, None))

    fetch(read(headRefName="the-base"))
    with edit(demo) as data:
        state.take_result(data, "j1", f"READY {state.pr_url(KEY)}")
    watching = watcher(demo)
    fold = f"PR {KEY}: merged into the-base (#43), not main [o1 j1]"
    assert fold in watch.run_pass(watching, T0)
    data = state.load(demo)
    assert (data["prs"][KEY]["state"], data["prs"][KEY]["into"], data["prs"][KEY]["merged_sha"], data["jobs"]["j1"]["state"]) == ("folded", OTHER, None, "waiting")
    assert view.holding(data, [KEY], None) == [KEY]
    assert not watch.wakes(fold, data, NO_CARDS)
    fetch(read(state="MERGED", mergeCommit="ddd444"))
    assert f"PR {KEY}: merged [o1 j1]" in watch.run_pass(watching, T0 + timedelta(seconds=61))
    assert ([KEY], {KEY: OTHER}) in asked
    data = state.load(demo)
    assert (data["prs"][KEY]["state"], data["prs"][KEY]["merged_sha"], data["jobs"]["j1"]["state"]) == ("merged", "ddd444", "done")
    assert data["jobs"]["j1"]["said"] == f"DONE {state.pr_url(KEY)} merged: {KEY} at ddd444"


def test_a_job_waiting_on_a_builders_change_is_free_once_that_builder_registers_its_pr_never_at_its_merge_its_brief_naming_the_branch_to_stack_on_with_no_free_line_since_the_watch_starts_it(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = "Forge-AI/monorepo#43"
    serve(monkeypatch, {})
    briefed = str(state.drive_dir(demo) / "jobs" / "j3.brief.md")
    with edit(demo) as data:
        data["jobs"]["j2"] = job(goal="build the store")
        data["jobs"]["j3"] = state.new_job("o1", "builder", "build on the store", [], state.stamp(T0)) | {"brief": briefed, "brief_flags": {"plan": "j1's design"}}
    assert cli.main(["job", "set", "j3", "--waits", "j2", "--drive", demo]) == 0
    assert (state.load(demo)["jobs"]["j3"]["state"], state.load(demo)["jobs"]["j3"]["said"]) == ("waiting", state.NOT_STARTED)
    watching = watcher(demo)
    assert not [line for line in watch.run_pass(watching, T0) if line.startswith("FREE ")]
    assert cli.main(["pr", "add", state.pr_url(built), "--job", "j2", "--drive", demo, *LEAD]) == 0
    assert state.load(demo)["jobs"]["j3"]["waits"] == ["j2"]
    said = watch.run_pass(watching, T0 + timedelta(seconds=61))
    assert not [line for line in said if line.startswith("FREE ")]
    waiting = state.load(demo)["jobs"]["j3"]
    assert (waiting["waits"], waiting["stacks_on"], state.load(demo)["prs"][built]["state"]) == ([], [built], "open")
    assert f"Where: Cut your branch on land-it ({built}), so your PR stacks on the changes it waited on.\n" in Path(briefed).read_text()


def test_a_job_freed_by_several_earlier_changes_stacks_on_the_one_with_the_most_open_prs_under_it_and_merges_in_each_other(demo: str) -> None:
    bottom, top, apart = "Forge-AI/monorepo#43", "Forge-AI/monorepo#44", "Forge-AI/monorepo#45"
    with edit(demo) as data:
        for key, job_id, branch, under in ((bottom, "j2", "the-store", None), (top, "j3", "the-reads", bottom), (apart, "j4", "the-writes", None)):
            data["prs"][key] = state.new_pr(state.pr_url(key), job_id, "o1") | {"branch": branch, "under": under}
        data["jobs"]["j5"] = job(goal="build on all three", stacks_on=[apart, top, bottom])
        assert state.stacked_on(data, data["jobs"]["j5"]) == f"on the-reads ({top}), with the-writes ({apart}) merged in"
        data["prs"][top].update(state="folded", into=bottom)
        assert state.stacked_on(data, data["jobs"]["j5"]) == f"on the-writes ({apart}), with the-store ({bottom}) merged in"


def test_a_wait_on_another_builders_pr_closed_unmerged_moves_to_that_builder_and_ends_once_it_registers_its_next_pr_while_its_own_pr_closing_frees_it(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed, rebuilt = "Forge-AI/monorepo#43", "Forge-AI/monorepo#44"
    serve(monkeypatch, {})
    with edit(demo) as data:
        data["jobs"]["j2"] = job(goal="build the store", state="built", branch="blobstore-refcounts")
        data["jobs"]["j3"] = state.new_job("o1", "builder", "build on the store", [], state.stamp(T0))
    assert cli.main(["pr", "add", state.pr_url(closed), "--job", "j2", "--drive", demo, *LEAD]) == 0
    with edit(demo) as data:
        data["jobs"]["j2"]["waits"] = [closed]
        data["jobs"]["j3"].update(state="waiting", said=f"waits on {closed}", waits=[closed])
    serve(monkeypatch, {closed: read(state="CLOSED")})
    watching = watcher(demo)
    said = watch.run_pass(watching, T0) + watch.run_pass(watching, T0 + timedelta(seconds=61))
    assert [line for line in said if line.startswith("FREE ")] == [f"FREE j2 o1: {closed} answered or done, nothing else holds it, built on blobstore-refcounts"]
    jobs = state.load(demo)["jobs"]
    assert (jobs["j2"]["waits"], jobs["j3"]["waits"]) == ([], ["j2"])
    assert cli.main(["pr", "add", state.pr_url(rebuilt), "--job", "j2", "--drive", demo, *LEAD]) == 0
    serve(monkeypatch, {})
    said = watch.run_pass(watching, T0 + timedelta(seconds=122))
    freed = state.load(demo)["jobs"]["j3"]
    assert ([line for line in said if line.startswith("FREE ")], "j2" in freed["waits"], freed["stacks_on"]) == ([], False, [rebuilt])


def test_a_release_wait_on_a_pr_closed_unmerged_frees_the_job_that_opened_it_and_moves_every_other_waiter_to_that_jobs_change(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    closed, rebuilt = "Forge-AI/monorepo#43", "Forge-AI/monorepo#44"
    release_wait = f"release:api:{closed}"
    serve(monkeypatch, {})
    with edit(demo) as data:
        data["jobs"]["j2"] = job(goal="build the store", state="built", branch="blobstore-refcounts")
        data["jobs"]["j3"] = job(goal="build on the store")
    assert cli.main(["pr", "add", state.pr_url(closed), "--job", "j2", "--drive", demo, *LEAD]) == 0
    with edit(demo) as data:
        data["jobs"]["j2"]["waits"] = [release_wait]
        data["jobs"]["j3"].update(state="waiting", waits=[release_wait])
        data["prs"][KEY]["waits"] = [release_wait]
    serve(monkeypatch, {closed: read(state="CLOSED")})
    watching = watcher(demo)
    said = watch.run_pass(watching, T0) + watch.run_pass(watching, T0 + timedelta(seconds=61))
    assert [line for line in said if line.startswith("FREE ")] == [f"FREE j2 o1: {release_wait} answered or done, nothing else holds it, built on blobstore-refcounts"]
    data = state.load(demo)
    assert (data["jobs"]["j2"]["waits"], data["jobs"]["j3"]["waits"], data["prs"][KEY]["waits"]) == ([], ["j2"], ["j2"])
    assert cli.main(["pr", "add", state.pr_url(rebuilt), "--job", "j2", "--drive", demo, *LEAD]) == 0
    serve(monkeypatch, {})
    said = watch.run_pass(watching, T0 + timedelta(seconds=122))
    data = state.load(demo)
    assert ([line for line in said if line.startswith("FREE ")], "j2" in data["jobs"]["j3"]["waits"], data["jobs"]["j3"]["stacks_on"]) == ([], False, [rebuilt])
    assert data["prs"][KEY]["waits"] == ["j2"]


def test_a_wait_on_a_staging_testers_pr_closed_unmerged_ends_and_never_moves_onto_the_tester_whose_test_holds_no_such_pr(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    closed = "Forge-AI/monorepo#43"
    serve(monkeypatch, {})
    with edit(demo) as data:
        data["jobs"]["j2"] = job(goal="run the staging test of Forge-AI/monorepo#44", state="waiting", parts=[state.STAGING_TEST.format(number=44)], builds_nothing=True)
        data["jobs"]["j3"] = job(goal="build on the fix")
    assert cli.main(["pr", "add", state.pr_url(closed), "--job", "j2", "--drive", demo, *LEAD]) == 0
    with edit(demo) as data:
        data["jobs"]["j3"].update(state="waiting", waits=[closed])
        data["prs"][KEY]["waits"] = [closed]
    serve(monkeypatch, {closed: read(state="CLOSED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    watch.run_pass(watching, T0 + timedelta(seconds=61))
    data = state.load(demo)
    assert (data["jobs"]["j3"]["waits"], data["jobs"]["j3"]["claim"]["worker"], data["prs"][KEY]["waits"]) == (["j4"], "j4", [])
    assert not state.waits_on_job(data, data["prs"][KEY], "j2", data["jobs"]["j2"])


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


def run_landing_loop(name: str, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    def stop(seconds: float) -> None:
        raise KeyboardInterrupt

    lines, given = os.pipe()
    monkeypatch.setattr(watch, "time", SimpleNamespace(sleep=stop))
    monkeypatch.setattr(watch, "signal", SimpleNamespace(signal=lambda signum, handler: None, SIGTERM=signal.SIGTERM, SIG_BLOCK=signal.SIG_BLOCK, SIG_UNBLOCK=signal.SIG_UNBLOCK, pthread_sigmask=lambda how, mask: set()))
    with pytest.raises(KeyboardInterrupt):
        watch.landing_loop(name, None, given)
    with os.fdopen(lines) as written:
        return [json.loads(line) for line in written]


def test_github_is_read_for_every_pr_at_a_landing_loops_start_none_before_it_handed_its_owed_reads_to_and_otherwise_only_for_the_prs_those_reads_and_the_deliveries_since_name(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    with edit(demo) as data:
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"head": "aaaaaaa111", "wants_tap": False}
    calls = serve(monkeypatch, {KEY: read(), OTHER: read()})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    watch.run_pass(watching, T0 + timedelta(seconds=60))
    deliver("check_run", checked(43))
    assert watch.run_pass(watching, T0 + timedelta(seconds=65)) == []
    watch.keep_unposted(watching)
    deliver("pull_request", {"pull_request": {"number": 42}})
    run_landing_loop(demo, monkeypatch)
    run_landing_loop(demo, monkeypatch)
    state.take_held_reads(demo)
    run_landing_loop(demo, monkeypatch)
    assert calls == [[KEY, OTHER], [OTHER], [KEY], [KEY, OTHER]]


def test_a_landing_loop_reading_github_by_another_query_than_the_one_that_handed_it_its_reads_reads_every_pr_whole(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    with edit(demo) as data:
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"head": "aaaaaaa111", "wants_tap": False}
    calls = serve(monkeypatch, {KEY: read(), OTHER: read()})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    watch.keep_unposted(watching)
    monkeypatch.setattr(state, "READ_MARK", "the query of a newer install")
    run_landing_loop(demo, monkeypatch)
    assert calls == [[KEY, OTHER], [KEY, OTHER]]


def test_a_push_to_their_base_reads_only_the_mergeability_of_the_prs_it_names_saying_a_conflict_it_starts_and_reads_whole_one_whose_head_moved(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    with edit(demo) as data:
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"head": "aaaaaaa111", "wants_tap": False}
    reads = {KEY: read(), OTHER: read()}
    calls = serve(monkeypatch, reads)
    merges = remerging(monkeypatch, reads)
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    reads |= {KEY: read(mergeable="CONFLICTING"), OTHER: read(headRefOid="bbbbbbb222")}
    deliver("push", {"ref": "refs/heads/main"})
    said = watch.run_pass(watching, T0 + timedelta(seconds=5))
    assert (said, calls, merges) == ([f"PR {OTHER}: new head bbbbbbb [o1 j1]", f"PR {KEY}: {watch.CONFLICTS} [o1 j1]"], [[KEY, OTHER], [OTHER]], [[KEY, OTHER]])
    assert (state.load(demo)["prs"][KEY]["conflict"], watching.merging) == (True, set())


def test_a_logged_delivery_owes_a_read_of_a_pr_the_drive_holds_closed_and_a_carried_ones_close_but_none_of_a_merged_pr_nor_of_a_carried_ones_check_run(demo: str) -> None:
    merged, carried = "Forge-AI/monorepo#41", "Forge-AI/monorepo#44"
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "closed"
        data["prs"][merged] = state.new_pr(state.pr_url(merged), "j1", "o1") | {"head": "aaaaaaa111", "state": "merged"}
        data["jobs"]["j2"] = state.new_job("o1", "builder", "port the fixes", [], state.stamp()) | {"state": "stopped", "prs": [state.pr_url(carried)]}
    watching = watch.Watcher(demo, offset=state.events_end(demo), owed=set())
    deliver("pull_request", {"action": "reopened", "pull_request": {"number": 42}})
    deliver("pull_request", {"action": "edited", "pull_request": {"number": 41}})
    deliver("check_run", checked(44))
    deliver("pull_request", {"action": "closed", "pull_request": {"number": 44}})
    assert (watch.take_deliveries(watching), watching.owed, watch.take_deliveries(watching)) == ([KEY, carried], {KEY, carried}, None)


def test_a_build_delivery_has_the_watch_read_again_each_build_it_holds_that_it_names_and_every_one_it_holds_for_a_missed_one(demo: str) -> None:
    held, other = "https://buildkite.com/forge/release/builds/14769", "https://buildkite.com/forge/release/builds/15020"
    watching = watch.Watcher(demo, offset=state.events_end(demo), builds={held: None, other: None})
    receiver.home().mkdir(parents=True)
    receiver.took_builds("build.finished", [held, "https://buildkite.com/forge/release/builds/1"])
    assert (watch.take_deliveries(watching), watching.builds_heard) == ([], {held})
    receiver.took_builds("missed", None)
    watch.take_deliveries(watching)
    assert watching.builds_heard == {held, other}


PULLED = re.compile(r"p(\d+): pullRequest\(number: \d+\)")
NAMED_REPOSITORY = re.compile(r'repository\(owner: "([^"]+)", name: "([^"]+)"\)')
MERGES = {"squashMergeAllowed": True, "rebaseMergeAllowed": True, "mergeCommitAllowed": True, "viewerPermission": "ADMIN", "landing": None, "defaultBranchRef": {"name": "dev", "rules": {"nodes": []}}}


def pull_node(head: str) -> dict:
    return {
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": head,
        "baseRefName": "dev",
        "headRefName": "the-watch",
        "title": "Fix: the watch",
        "lastEditedAt": None,
        "author": {"login": "yasyf"},
        "reviewDecision": None,
        "latestOpinionatedReviews": {"nodes": []},
        "reviews": {"nodes": []},
        "mergeable": "MERGEABLE",
        "mergeCommit": None,
        "mergedAt": None,
        "labels": {"nodes": []},
        "timelineItems": {"nodes": []},
        "commits": {"nodes": []},
        "stack": None,
        "baseRef": None,
        "updatedAt": "2026-11-16T10:00:00Z",
        "signatureChecks": {"nodes": []},
    }


def graphql_counted(monkeypatch: pytest.MonkeyPatch, heads: dict[str, str]) -> list[tuple[int, int, int]]:
    asked = []
    monkeypatch.setattr(github, "fetch", REAL_FETCH)
    monkeypatch.setattr(github, "mergeability", REAL_MERGEABILITY)

    def answer(cmd: list[str], **_: object) -> str:
        repositories, numbers, whole = NAMED_REPOSITORY.findall(cmd[4]), PULLED.findall(cmd[4]), "statusCheckRollup" in cmd[4]
        asked.append((len(repositories), len(numbers) * whole, len(numbers) * (not whole)))
        repo = "/".join(repositories[0])
        pulls = {f"p{number}": pull_node(heads[f"{repo}#{number}"]) if whole else {"state": "OPEN", "headRefOid": heads[f"{repo}#{number}"], "mergeable": "MERGEABLE"} for number in numbers}
        return json.dumps({"data": {"r0": MERGES | pulls}})

    monkeypatch.setattr(github, "run", answer)
    return asked


@pytest.mark.parametrize("tracked", [1, 100])
def test_a_watch_handed_its_owed_reads_asks_github_one_query_a_repository_for_the_prs_its_deliveries_name_alone_whether_the_drive_tracks_one_pr_or_a_hundred(
    demo: str, monkeypatch: pytest.MonkeyPatch, tracked: int
) -> None:
    numbers = [42, *range(1000, 1000 + tracked - 1)]
    keys = [f"Forge-AI/monorepo#{number}" for number in numbers]
    with edit(demo) as data:
        for key in [*keys, YASYF]:
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"head": "aaaaaaa111", "base": "dev", "wants_tap": False, "required": {"checks": "none", "failed": {}, "read": state.stamp(T0)}}
    asked = graphql_counted(monkeypatch, dict.fromkeys([*keys, YASYF], "aaaaaaa111"))
    watching = watch.Watcher(demo, offset=state.events_end(demo), owed=set())
    deliver("check_run", checked(42))
    watch.event_lines(watching)
    watch.github_lines(watching, T0)
    deliver("push", {"ref": "refs/heads/dev"})
    deliver("push", {"ref": "refs/heads/dev"}, repo="yasyf/cc-skills")
    watch.event_lines(watching)
    watch.github_lines(watching, T0 + timedelta(seconds=5))
    assert asked == [(1, 1, 0), (1, 0, tracked), (1, 0, 1)]


def test_a_delivery_of_a_failed_check_is_said_in_the_pass_it_wakes(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    reads = {KEY: read()}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    reads[KEY] = read(checks=RED)
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    deliver("check_run", checked(42))
    assert watch.run_pass(watching, T0 + timedelta(seconds=10)) == [f"PR {KEY}: checks failed [o1 j1]"]


@pytest.mark.parametrize(
    ("jobs", "rerun"),
    [
        ({70: (1, CHECKOUT_FAILED)}, True),
        ({70: (2, CHECKOUT_FAILED)}, False),
        ({70: (1, TEST_FAILED)}, False),
        ({70: (1, CHECKOUT_FAILED), 71: (1, TEST_FAILED)}, False),
    ],
)
def test_a_github_actions_run_whose_every_failed_job_failed_at_its_checkout_on_its_first_attempt_is_rerun_once_and_reads_grading_waking_no_builder(
    demo: str, monkeypatch: pytest.MonkeyPatch, jobs: dict[int, tuple[int, list[dict]]], rerun: bool
) -> None:
    ran = []

    def answer(cmd: list[str], **_: object) -> str:
        ran.append(cmd)
        if cmd[1] != "api":
            return ""
        attempt, steps = jobs[int(cmd[2].rsplit("/", 1)[1])]
        return json.dumps({"run_attempt": attempt, "steps": steps})

    monkeypatch.setattr(github, "run", answer)
    failed = [{"name": f"pr-events {job}", "conclusion": "FAILURE", "detailsUrl": f"https://github.com/Forge-AI/monorepo/actions/runs/7/job/{job}", "workflowName": "PR Events"} for job in jobs]
    serve(monkeypatch, {KEY: read(checks=[*GREEN, *failed])})
    lines = watch.github_lines(watcher(demo), T0)
    assert [cmd for cmd in ran if cmd[1] == "run"] == ([["gh", "run", "rerun", "7", "--failed", "-R", "Forge-AI/monorepo"]] if rerun else [])
    assert lines == [f"PR {KEY}: reran PR Events, whose checkout failed before any of its code ran [o1 j1]" if rerun else f"PR {KEY}: checks failed [o1 j1]"]
    assert stage.of(state.load(demo), KEY, NO_CARDS).name == ("grading" if rerun else "red")


def test_a_builders_pushed_owes_a_read_of_each_pr_it_names_github_has_not_answered_for_since_the_take(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    calls = serve(monkeypatch, {KEY: read()})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    with edit(demo) as data:
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
    assert (receiving.hooked, calls) == (["Forge-AI/monorepo"], [[KEY], [KEY], [KEY]])


def test_a_repository_this_account_does_not_administer_is_asked_once_and_a_hook_that_fails_is_said_without_holding_back_the_next_while_each_open_pr_no_hook_delivers_is_read_each_minute(
    demo: str, monkeypatch: pytest.MonkeyPatch, receiving: Receiver
) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    broken, upstream, shut = "Forge-AI/litestream#3", "stablyai/orca#7", "stablyai/orca#8"
    with edit(demo) as data:
        for key in (broken, upstream, shut):
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"head": "aaaaaaa111", "wants_tap": False}
    calls = serve(monkeypatch, {key: read() for key in (KEY, broken, upstream)} | {shut: read(state="CLOSED")})
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
        [[KEY, broken, upstream, shut], [KEY], [broken, upstream], [broken, upstream]],
    )


def test_a_pr_on_a_repository_no_hook_delivers_from_says_its_checks_passed_within_a_minute_of_github_reading_them_green(
    demo: str, monkeypatch: pytest.MonkeyPatch, receiving: Receiver
) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    docs = "Forge-AI/design-docs#366"
    with edit(demo) as data:
        data["prs"][docs] = state.new_pr(state.pr_url(docs), "j1", "o1") | {"head": "aaaaaaa111", "wants_tap": False}
    reads = {KEY: read(), docs: read()}
    serve(monkeypatch, reads)
    receiving.unhooked.add("Forge-AI/design-docs")
    receiving.unadministered.add("Forge-AI/design-docs")
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    reads[docs] = read(checks=GREEN)
    quiet = watch.run_pass(watching, T0 + timedelta(seconds=5))
    said = watch.run_pass(watching, T0 + timedelta(seconds=60))
    assert ([line for line in [*quiet, *said] if line.startswith("PR ")], state.load(demo)["prs"][docs]["required"]["read"]) == (
        [f"PR {docs}: checks passed [o1 j1]"],
        state.stamp(T0 + timedelta(seconds=60)),
    )


def test_a_receiver_that_does_not_answer_three_minutes_in_a_row_is_replaced_and_said_once_each_time_it_stops_answering_never_again_while_it_stays_down(
    demo: str, monkeypatch: pytest.MonkeyPatch, receiving: Receiver
) -> None:
    kicked: list[tuple[str, ...]] = []
    monkeypatch.setattr(launchd, "launchctl", lambda *args: kicked.append(args))
    serve(monkeypatch, {})
    watching = watcher(demo)
    said = []
    for minutes, answering in enumerate([False] * 13 + [True] + [False] * 3):
        receiving.answering = answering
        said.append([line for line in watch.run_pass(watching, T0 + timedelta(minutes=minutes)) if line.startswith("WATCH")])
    service = f"gui/{os.getuid()}/com.yasyf.long-running.receiver"
    replaced = f"WATCH the receiver took no delivery and answered nothing on its port {receiver.PORT} 3 passes in a row, so the watch replaced it with `launchctl kickstart -k {service}` and reads every open PR from GitHub each minute until it takes a delivery or answers; its log is {receiver.log_path()}"
    assert (said, kicked) == ([[], [], [replaced], *[[]] * 10, [], [], [], [replaced]], [("kickstart", "-k", service)] * 2)


def test_a_receiver_that_cannot_be_replaced_says_why(demo: str, monkeypatch: pytest.MonkeyPatch, receiving: Receiver) -> None:
    def refuse(*args: str) -> None:
        raise state.DriveError(f"launchctl {' '.join(args)} exited 113: Could not find service")

    monkeypatch.setattr(launchd, "launchctl", refuse)
    serve(monkeypatch, {})
    receiving.answering = False
    watching = watcher(demo)
    assert [watch.run_pass(watching, T0 + timedelta(minutes=minutes)) for minutes in range(3)][-1] == [
        f"WATCH the receiver took no delivery and answered nothing on its port {receiver.PORT} 3 passes in a row, and the watch could not replace it: launchctl kickstart -k gui/{os.getuid()}/com.yasyf.long-running.receiver exited 113: Could not find service; its log is {receiver.log_path()}"
    ]


def test_a_receiver_that_took_a_delivery_since_the_last_pass_counts_as_answering_so_only_passes_taking_none_count_toward_its_replacement(
    demo: str, monkeypatch: pytest.MonkeyPatch, receiving: Receiver
) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    kicked: list[tuple[str, ...]] = []
    monkeypatch.setattr(launchd, "launchctl", lambda *args: kicked.append(args))
    calls = serve(monkeypatch, {KEY: read()})
    watching = watcher(demo)
    said = []
    for minutes, last in enumerate(["g0", "g1", "g2", "g3", "g3", "g3", "g3"]):
        with receiver.hooks_kept() as hooked:
            hooked["Forge-AI/monorepo"] = {"id": 9, "last": last, "events": receiver.EVENTS}
        receiving.answering = minutes == 0
        said += [line for line in watch.run_pass(watching, T0 + timedelta(minutes=minutes)) if line.startswith("WATCH")]
    assert (said, len(kicked), calls) == ([f"WATCH the receiver took no delivery and answered nothing on its port {receiver.PORT} 3 passes in a row, so the watch replaced it with `launchctl kickstart -k gui/{os.getuid()}/com.yasyf.long-running.receiver` and reads every open PR from GitHub each minute until it takes a delivery or answers; its log is {receiver.log_path()}"], 1, [[KEY], [KEY], [KEY]])


def test_each_open_pr_on_a_hooked_repository_is_read_each_minute_while_the_receiver_does_not_answer(demo: str, monkeypatch: pytest.MonkeyPatch, receiving: Receiver) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    calls = serve(monkeypatch, {KEY: read()})
    watching = watcher(demo)
    for moment, answering in ((T0, True), (T0 + timedelta(seconds=60), False), (T0 + timedelta(seconds=65), False), (T0 + timedelta(seconds=120), False), (T0 + timedelta(seconds=180), True), (T0 + timedelta(seconds=240), True)):
        receiving.answering = answering
        watch.run_pass(watching, moment)
    assert calls == [[KEY], [KEY], [KEY]]


def fetch_outcomes(monkeypatch: pytest.MonkeyPatch, outcomes: list[bool]) -> list[list[str]]:
    calls, queue = [], list(outcomes)

    def fetch(keys: list[str], into: dict[str, str] | None = None) -> tuple[dict[str, dict], github.GithubError | None]:
        calls.append(keys)
        return ({KEY: read()}, None) if queue.pop(0) else ({}, github.GithubError("HTTP 502"))

    monkeypatch.setattr(github, "fetch", fetch)
    return calls


def test_failed_read_retries_next_poll_and_says_so_once_at_the_third_failure_in_a_row(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = fetch_outcomes(monkeypatch, [False] * 5)
    watching = watcher(demo)
    said = "WATCH github read failed {} times in a row: HTTP 502"
    assert [watch.run_pass(watching, T0 + timedelta(seconds=s)) for s in (0, 5, 10, 15)] == [[], [], [said.format(3)], []]
    assert len(calls) == 3
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=10, seconds=10)) == []


def test_a_good_read_starts_the_failure_count_over(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_outcomes(monkeypatch, [False, True, False, False, False])
    watching = watcher(demo)
    lines = [watch.run_pass(watching, T0 + timedelta(seconds=s)) for s in (0, 5, 65, 70, 75)]
    assert lines == [[], [], [], [], ["WATCH github read failed 3 times in a row: HTTP 502"]]


def test_a_refused_query_keeps_the_reads_of_the_others_and_a_finished_pr_is_never_read_again(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with edit(demo) as data:
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"head": "aaaaaaa111", "wants_tap": False}
    calls = []

    def other_refused(keys: list[str], into: dict[str, str] | None = None) -> tuple[dict[str, dict], github.GithubError | None]:
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


def test_a_watch_that_ends_ends_its_responder_last_so_lanes_get_readers_while_the_relay_and_landing_loop_end(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    ended: list[str] = []

    def spawn(watcher: watch.Watcher, flag: str, lines: int, project: str | None = None) -> subprocess.Popen:
        child = subprocess.Popen(["sleep", "3600"], pass_fds=(lines,))
        real = child.terminate
        monkeypatch.setattr(child, "terminate", lambda: ended.append(flag) or real())
        return child

    monkeypatch.setattr(watch, "spawn", spawn)
    with watch.relaying(watcher(demo)):
        pass
    assert ended == ["--land", "--relay"]


def test_a_watch_that_stops_ends_its_relay_process_before_it_returns(demo: str, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess) -> None:
    serve(monkeypatch, {})
    posts(monkeypatch)
    run_watch(demo, monkeypatch)
    assert [child.returncode for child in watch_process.relays] == [-signal.SIGTERM, -signal.SIGTERM]
    assert [thread.name for thread in threading.enumerate() if thread.name.endswith("(relayed_lines)")] == []


def test_the_watch_hands_on_each_line_its_relay_process_writes_keeps_the_untracked_prs_its_landing_loop_writes_and_starts_again_one_that_ended_unasked(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[tuple[str, subprocess.Popen]] = []
    written = {
        "--relay": ["os.write({lines}, b'DUE o1 was due\\nDUE o2 was due\\n'); raise SystemExit(2)", "os.write({lines}, b'DUE o3 was due\\n'); time.sleep(60)"],
        "--land": ['os.write({lines}, b\'{{"yasyf/cc-skills#9": "MERGED"}}\\n\'); time.sleep(60)'],
    }

    def spawn(watcher: watch.Watcher, flag: str, lines: int, project: str | None = None) -> subprocess.Popen:
        child = subprocess.Popen([sys.executable, "-c", "import os, time; " + written[flag].pop(0).format(lines=lines)], pass_fds=(lines,))
        started.append((flag, child))
        return child

    again, landed = threading.Event(), threading.Event()
    took = watch.took_untracked
    monkeypatch.setattr(watch, "took_untracked", lambda watcher, line: (took(watcher, line), landed.set()))

    class Relayed(list[str]):
        def append(self, line: str) -> None:
            super().append(line)
            if line == "DUE o3 was due":
                again.set()

    monkeypatch.setattr(watch, "spawn", spawn)
    monkeypatch.setattr(launchd, "RESTART_SECONDS", 0)
    watching = watcher(demo)
    watching.relayed = Relayed()
    with watch.relaying(watching):
        assert again.wait(30) and landed.wait(30)
    [first, second] = [child for flag, child in started if flag == "--relay"]
    assert (list(watching.relayed), watching.untracked) == (
        ["DUE o1 was due", "DUE o2 was due", f"WATCH the relay, process {first.pid}, exited 2; the watch starts it again within 0 seconds; its output is in {state.watch_log_path(demo)}", "DUE o3 was due"],
        {"yasyf/cc-skills#9": "MERGED"},
    )
    assert [(flag, child.returncode) for flag, child in started] == [("--relay", 2), ("--land", -signal.SIGTERM), ("--relay", -signal.SIGTERM)]


def test_the_watchs_own_pass_reads_no_github_and_lands_nothing_leaving_both_to_its_landing_loop_whatever_the_pass_holds(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = serve(monkeypatch, {KEY: read(state="MERGED", mergeCommit="ccc333")})
    assert ([line for lines in watch.passed(watcher(demo), T0) for line in lines], calls) == ([], [])
    landing = watcher(demo)
    assert [line for lines in watch.passed(landing, T0, staged=watch.landing_pass) for line in lines] == [f"PR {KEY}: merged [o1 j1]"]
    assert (calls, state.load(demo)["prs"][KEY]["merged_sha"]) == ([[KEY]], "ccc333")


def test_the_landing_loop_holds_the_reads_it_owes_before_it_runs_on_a_newer_install_in_its_place_so_the_one_there_reads_each_delivery_since(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = serve(monkeypatch, {KEY: read()})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    watch.keep_unposted(watching)
    deliver("check_run", checked(42))
    lines, given = os.pipe()
    newer = tmp_path / "cache" / "9.9.9" / "bin" / "drive"
    monkeypatch.setattr(watch, "upgraded", lambda: newer)
    monkeypatch.setattr(install, "upgraded", lambda: newer)
    monkeypatch.setattr(watch, "signal", SimpleNamespace(signal=lambda signum, handler: None, SIGTERM=signal.SIGTERM, SIG_BLOCK=signal.SIG_BLOCK, SIG_UNBLOCK=signal.SIG_UNBLOCK, pthread_sigmask=lambda how, mask: set()))

    def execv(path: Path, argv: list[str]) -> None:
        raise SystemExit((path, argv))

    monkeypatch.setattr(os, "execv", execv)
    with pytest.raises(SystemExit) as exec_ran:
        watch.landing_loop(demo, None, given)
    os.close(lines)
    assert exec_ran.value.code == (newer, [str(newer), "watch", "--land", str(given), "--drive", demo])
    monkeypatch.setattr(watch, "upgraded", lambda: None)
    assert run_landing_loop(demo, monkeypatch) == [{}]
    assert calls == [[KEY], [KEY]]


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
    with edit(name) as data:
        data["orders"]["o1"].update(text=text, owner=owner)
        data["jobs"]["j1"]["state"] = job_state


def posts(monkeypatch: pytest.MonkeyPatch, outcomes: list[bool] | None = None) -> list[list[str]]:
    sent, queue = [], list(outcomes or [])

    def post(argv: list[str], checkout: Path, env: dict | None = None, stdin: str | None = None) -> str:
        if queue and not queue.pop(0):
            raise reads.ReadError("cci post exited 1: no drive demo")
        sent.append([stdin if stdin is not None and argv[at - 1] == "--path" else arg for at, arg in enumerate(argv)])
        return ""

    monkeypatch.setattr(watch.reads, "run", post)
    monkeypatch.setattr(watch, "untaken", lambda watcher, data, lane: False)
    return sent


def posted(sent: list[list[str]]) -> list[tuple[str, str, str]]:
    return [
        (argv[argv.index("--kind") + 1], argv[argv.index("--to") + 1], line)
        for argv in sent
        for line in (
            [row for row in argv[argv.index("--path") + 1].splitlines() if not row.startswith("  ")]
            if re.match(r"\d+ lines: ", argv[argv.index("--text") + 1])
            else [argv[argv.index("--text") + 1]]
        )
    ]


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
        ("JOB j1 o1 READY https://github.com/yasyf/cc-skills/pull/7", "meta"),
        ("LANE j1 o1 died on Sprite s1: its worker exited", "meta"),
        ("STUCK j1 o1: j1 unmoved 61 minutes: j1 waiting on j2", "meta"),
        (f"PR {KEY}: merged [o1 j1]", "meta"),
        ('ANSWER undelivered j1: "ship it", held at 9:00 AM, has not reached j1 [o1 j1]', "meta"),
        ('ANSWER 8427b3c o1 "ship it": 1 card with no mark; drive answer 8427b3c lists them', "meta"),
        ('ANSWER 8427b3c j1 "ship it": j1 not sent his words; drive answer 8427b3c lists them', "meta"),
        ('ANSWER 8427b3c "ship it": 1 card with no mark; drive answer 8427b3c lists them', None),
        (f"TAP Approve {KEY}", None),
        ("EVENT relaunch job=j1", None),
        ("WATCH post to the coordinator failed 3 times in a row", None),
    ],
)
def test_every_line_naming_a_job_or_an_order_goes_to_the_session_holding_it_with_no_list_of_line_kinds(demo: str, line: str, holder: str | None) -> None:
    tooling_order(demo, "running")
    assert watch.line_lane(line, state.load(demo)) == holder


def test_a_job_unsent_his_answer_holds_his_words_for_its_next_worker_from_any_watch_with_no_line(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sub_coordinated(demo)
    with edit(demo) as data:
        data["jobs"]["j2"] = job(agent="a-2", where="local", session="session-under-test")
        data["orders"]["o1"]["jobs"].append("j2")
        data["answers"]["a1"] = {"words": "one new tool", "link": f"{CARD}9", "at": state.stamp(T0), "said": state.stamp(T0), "cards": {}, "jobs": {"j1": None, "j2": None}}
        data["jobs"]["j1"].update(agent="a-1", where="local", session="iris-1")
    sent = posts(monkeypatch)
    watching = watcher(demo)
    watching.cards = NO_CARDS
    assert watch.answer_lines(watching, T0 + timedelta(minutes=10)) == []
    data = state.load(demo)
    assert data["answers"]["a1"]["jobs"] == {"j1": {"mark": "sent"}, "j2": {"mark": "sent"}}
    assert [[held["words"] for held in data["jobs"][job_id]["undelivered"]] for job_id in ("j1", "j2")] == [['His answer a1, verbatim: "one new tool"']] * 2
    prover = "JOB j1 o1 added, proves Forge-AI/monorepo#42: the watch starts its agent"
    assert watch.handed(watching, [prover], T0) == []
    assert posted(sent) == [("handoff", f"iris@{demo}", prover)]


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


def test_a_claim_whose_end_passed_with_no_closing_line_owes_one_claim_item_met_once_a_drive_post_closes_the_claim(demo: str, slack_thread: "Thread") -> None:
    claim, moved = "1794200000.000100", "1794203600.000200"
    link = "https://acme.slack.com/archives/C0STAGING1/p1794200000000100"
    with edit(demo) as data:
        data["orders"]["o2"] = order(text="test the batch writer on staging", jobs=[])
    with edit(demo) as data:
        data["orders"]["o1"]["state"] = "done"
        state.claim(data, data["orders"]["o1"], ("C0STAGING1", claim), link, state.stamp(T0), claim)
    watching = watcher(demo)
    assert watch.claim_lines(watching, T0 - timedelta(minutes=1)) == []
    assert watch.claim_lines(watching, T0) == []
    [item] = state.load(demo)["orders"]["o1"]["owed"]
    assert (item["kind"], item["done_when"], item["due"]) == (owing.CLAIM, {"claim": claim}, state.stamp(T0))
    assert item["what"].startswith(f"the closing line of claim {link}, which ended {render.when(state.stamp(T0))}: once the work ends, free what it claimed")
    [line] = watch.owing_lines(watching, T0 + timedelta(minutes=1))
    assert line.startswith(f'DUE o1 owed w1: claim "the closing line of claim {link}')
    slack_thread.post(T0 + timedelta(minutes=9), from_user=True)
    assert watch.claim_lines(watching, T0 + timedelta(minutes=10)) == []
    assert len(state.load(demo)["orders"]["o1"]["owed"]) == 1
    with edit(demo) as data:
        state.claim(data, data["orders"]["o2"], ("C0STAGING1", claim), link, state.stamp(T0 + timedelta(hours=1)), moved)
    assert watch.claim_lines(watching, T0 + timedelta(minutes=30)) == []
    slack_thread.post(T0 + timedelta(minutes=70), from_claude=True)
    assert watch.claim_lines(watching, T0 + timedelta(minutes=71)) == []
    assert state.load(demo)["orders"]["o1"]["claims"] == {}
    watch.owing_lines(watching, T0 + timedelta(minutes=72))
    assert state.load(demo)["orders"]["o1"]["owed"][0]["closed"]["by"] == "met"
    assert slack_thread.reads == [("--channel", "C0STAGING1", "--thread", claim, "--since-ts", since, "--limit", "1000") for since in (claim, claim, moved)]


def test_a_claim_5_minutes_old_tells_its_order_once_that_its_work_may_start_or_quotes_each_reply_but_the_drives_and_a_bots(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    quiet, replied = (f"{(T0 + timedelta(minutes=minute)).timestamp():.6f}" for minute in (0, 1))
    link = {ts: f"https://acme.slack.com/archives/C0STAGING1/p{ts.replace('.', '')}" for ts in (quiet, replied)}
    threads = {
        quiet: [{"ts": quiet, "from_claude": True}],
        replied: [
            {"ts": replied, "from_claude": True},
            {"ts": f"{(T0 + timedelta(minutes=2)).timestamp():.6f}", "from_claude": True, "text": "Running until 11:30 now."},
            {"ts": f"{(T0 + timedelta(minutes=3)).timestamp():.6f}", "from_bot": True, "text": "Deploy started."},
            {"ts": f"{(T0 + timedelta(minutes=4)).timestamp():.6f}", "user_name": "demo-user", "text": "I'm soaking the router there until noon"},
        ],
    }
    monkeypatch.setattr(slack, "thread", lambda *where: list(threads[where[3]]))
    with edit(demo) as data:
        data["orders"]["o2"] = order(text="test the batch writer on staging", jobs=[])
    with edit(demo) as data:
        for order_id, ts in (("o1", quiet), ("o2", replied)):
            state.claim(data, data["orders"][order_id], ("C0STAGING1", ts), link[ts], state.stamp(T0 + timedelta(hours=1)), ts)
    watching = watcher(demo)
    assert watch.claim_reply_lines(watching, T0 + timedelta(minutes=4, seconds=59)) == []
    [line] = watch.claim_reply_lines(watching, T0 + timedelta(minutes=5))
    assert line == f"DUE o1 claim {link[quiet]} is 5 minutes old with no reply in its thread: the work it claims may start, as the repository's staging-claim rule says"
    assert watch.ABOUT.match(line)["named"] == "o1"
    [line] = watch.claim_reply_lines(watching, T0 + timedelta(minutes=6))
    assert line == (
        f'DUE o2 claim {link[replied]} is 5 minutes old with 1 reply in its thread, demo-user: "I\'m soaking the router there until noon":'
        f" start nothing it claims until a POST report with --attr thread={replied} answers it there and nobody objects, as the repository's staging-claim rule says"
    )
    assert watch.claim_reply_lines(watching, T0 + timedelta(minutes=30)) == []
    claims = {ts: held for each in state.load(demo)["orders"].values() for ts, held in each["claims"].items()}
    assert {ts: held["replies_read"] for ts, held in claims.items()} == {quiet: state.stamp(T0 + timedelta(minutes=5)), replied: state.stamp(T0 + timedelta(minutes=6))}


def shown_pr(demo: str, key: str) -> str:
    """The PR's line as drive status shows it: its stored row, with his card and its stage on it."""
    data = cli.with_cards(demo, state.load(demo))
    return f"- {render.pr_line(data, key, data['prs'][key]['card_state'])}"


def teammate(moment: datetime, **fields: object) -> dict:
    return {"ts": f"{moment.timestamp():.6f}", "thread_ts": TEAMMATE_THREAD, "user_name": "Dana", **fields}


def test_a_thread_his_open_order_watches_starts_a_handler_on_each_new_teammate_message_once_and_none_once_the_order_closes(
    demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    calls = catching_up(monkeypatch, demo)
    with edit(demo) as data:
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
    assert (report["text"], report["topic"], report["to"]) == ("POST 1 new in the thread o1 watches: answer it there", "watch-o1", [f"{state.POSTS_LANE}@{demo}"])
    assert report["attr"] == {"order": "o1", "progress": "true", "watch": "true", "thread": TEAMMATE_THREAD}
    assert body.splitlines()[-2:] == ["New there from anyone but him, the drive and a bot: 1", f"- {slack_thread.messages[1]['ts']} Dana: can we run it again tonight?"]
    assert start[-1] == "ANSWER THIS: " + f"Record: 901 Drive: {demo} Scratch: {state.drive_dir(demo) / 'scratch' / 'watch-901'}"
    assert slack_thread.reads == [("--channel", "C0TEAM0001", "--thread", TEAMMATE_THREAD, "--since-ts", f"{T0.timestamp():.6f}", "--limit", "1000")]
    assert (state.load(demo)["orders"]["o1"]["watch"]["seen"], state.load(demo)["orders"]["o1"]["watch"]["record"]) == (slack_thread.messages[3]["ts"], None)
    assert not [key for key in watch.part_gaps(watching, state.load(demo), T0 + timedelta(hours=1)) if key[0] == "o1"]
    assert watch.watched_lines(watching, T0 + timedelta(minutes=2)) == []
    assert len(calls) == 2
    slack_thread.messages.append(teammate(T0 + timedelta(minutes=2, seconds=10), text="and tomorrow?"))
    with edit(demo) as data:
        data["orders"]["o1"]["state"] = "done"
    assert watch.watched_lines(watching, T0 + timedelta(minutes=3)) == []
    assert (len(calls), len(slack_thread.reads)) == (2, 2)


HIS_THREAD = "1794200000.000100"
REAL_OWED = watch.owed_lines


@pytest.fixture
def owed_replies(demo: str, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """The messages of his cc-slack dm-owed lists, while the session the demo drive's handlers run beside holds his DM."""
    owed: list[dict] = []
    answer = slack.cc_slack
    monkeypatch.setattr(slack, "cc_slack", lambda *argv, **flags: {"replies": list(owed), "holder_session": agents.beside(state.load(demo))[1]["session"]} if argv == ("dm-owed",) else answer(*argv, **flags))
    return owed


def owed_reply(moment: datetime, text: str, thread: str | None = HIS_THREAD) -> dict:
    ts = f"{moment.timestamp():.6f}"
    link = f"https://acme.slack.com/archives/D123/p{ts.replace('.', '')}"
    return {"ts": ts, "thread_ts": thread or ts, "text": text, "permalink": f"{link}?thread_ts={thread}&cid=D123" if thread else link}


def test_a_message_of_his_cc_slack_owes_is_kept_while_owed_and_sends_the_communicator_nothing(demo: str, monkeypatch: pytest.MonkeyPatch, owed_replies: list[dict]) -> None:
    calls = catching_up(monkeypatch, demo)
    asked = owed_reply(T0, "why did the release stop?")
    owed_replies.append(asked)
    watching = watcher(demo)
    assert REAL_OWED(watching, T0 + timedelta(minutes=30)) == []
    assert calls == []
    assert state.load(demo)["owed_replies"] == {asked["ts"]: {"handed": False, "lined": None}}
    owed_replies.clear()
    assert REAL_OWED(watching, T0 + timedelta(minutes=31)) == []
    assert state.load(demo)["owed_replies"] == {}


def test_a_dm_owed_read_that_fails_is_said_from_its_third_failure_in_a_row_and_a_project_watch_reads_none(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    reads_made: list[str] = []

    def refused(*argv: str, **flags: object) -> dict:
        reads_made.append(argv[0])
        raise slack.SlackError("daemon not running")

    monkeypatch.setattr(slack, "cc_slack", refused)
    watching = watcher(demo)
    assert [REAL_OWED(watching, T0 + timedelta(minutes=minute)) for minute in range(3)] == [[], [], ["WATCH read of the replies cc-slack owes him failed 3 times in a row: daemon not running"]]
    assert REAL_OWED(watch.Watcher(demo, offset=0, project="platform"), T0) == []
    assert reads_made == ["dm-owed"] * 3


def test_a_message_of_his_owed_while_another_session_holds_his_dm_is_left_to_that_session(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = catching_up(monkeypatch, demo)
    asked = owed_reply(T0, "why did the release stop?")
    monkeypatch.setattr(slack, "cc_slack", lambda *argv, **flags: {"replies": [asked], "holder_session": "another-drives-communicator"})
    assert REAL_OWED(watcher(demo), T0 + timedelta(minutes=30)) == []
    assert (calls, state.load(demo)["owed_replies"]) == ([], {})


FOLLOWED = "https://buildkite.com/forge/release/builds/14185"
SMOKE = "Watch the tenant smoke on plat-usw2-staging"


def release(build_state: str, finished: datetime | None = None, done: int = 1) -> dict:
    return {
        "state": build_state,
        "url": FOLLOWED,
        "message": "Staging release plangent-macdiarmid\n\nships 12 PRs",
        "commit": "abc1234def",
        "createdAt": state.stamp(T0 - timedelta(minutes=1)),
        "startedAt": state.stamp(T0),
        "finishedAt": finished and state.stamp(finished),
        "blocks": {"edges": []},
        "failed": {"edges": [{"node": {"label": SMOKE, "retried": False}}] if build_state == "FAILED" else []},
        "running": {"edges": [] if finished else [{"node": {"label": SMOKE, "startedAt": state.stamp(T0 + timedelta(minutes=1))}}]},
        "steps": {"count": 3},
        "done": {"count": done},
    }


def following(demo: str) -> str:
    with edit(demo) as data:
        data["orders"]["o1"]["state"] = "open"
        data["jobs"]["j1"]["state"] = "done"
        [part] = data["orders"]["o1"]["parts"]
        data["orders"]["o1"]["parts"][part]["follow"] = {"build": FOLLOWED, "thread": "1794200000.000100", "message": None, "words": None, "ended": None, "record": None}
    return part


def test_a_build_his_ask_follows_keeps_one_status_message_current_from_the_watchs_read_and_posts_the_end_line_that_delivers_the_part_itself(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = catching_up(monkeypatch, demo)
    part = following(demo)
    posts: list[dict] = []
    monkeypatch.setattr(talk, "posted", lambda name, post: posts.append(post) or {"permalink": POSTED})
    lined: list[str] = []
    monkeypatch.setattr(talk, "report_line", lambda name, seq: lined.append(seq) or f"POSTED {POSTED}")
    watching = watcher(demo)
    watching.builds = {FOLLOWED: release("RUNNING")}
    title = f"<{FOLLOWED}|Staging release plangent-macdiarmid>"
    assert watch.status_lines(watching, T0 + timedelta(minutes=5)) == []
    assert posts == [{"text": f"{title}: {SMOKE}, 4 min; 1 of 3 steps done", "thread_ts": "1794200000.000100"}]
    assert watch.status_lines(watching, T0 + timedelta(minutes=5, seconds=30)) == []
    assert len(posts) == 1
    assert watch.status_lines(watching, T0 + timedelta(minutes=6)) == []
    assert posts[-1] == {"edit": POSTED, "text": f"{title}: {SMOKE}, 5 min; 1 of 3 steps done"}
    assert watch.part_gap(watching, state.load(demo), "o1", part, T0 + timedelta(hours=1)) is None
    assert calls == []
    watching.builds = {FOLLOWED: release("FAILED", T0 + timedelta(minutes=20), done=3)}
    assert watch.status_lines(watching, T0 + timedelta(minutes=21)) == []
    ended = f'{title}: failed after 20 min; 3 of 3 steps done; did not pass: "{SMOKE}"'
    assert posts[-1] == {"edit": POSTED, "text": ended}
    [(report, body)] = calls
    assert (report["text"], body.startswith(f"POST {ended}\n\n"), report["topic"], report["id"]) == (limits.first_line(f"POST {ended}"), True, "follow-o1", f"follow-end-o1-{FOLLOWED}")
    assert report["attr"] == {"order": "o1", "part": f"o1:{part}", "thread": "1794200000.000100"}
    assert f'The build: {FOLLOWED}, FAILED, at commit abc1234def, its steps that did not pass "{SMOKE}"' in body.splitlines()
    assert lined == ["901"]
    data = state.load(demo)
    follow = data["orders"]["o1"]["parts"][part]["follow"]
    assert (follow["ended"], follow["record"], follow["message"]) == (state.stamp(T0 + timedelta(minutes=21)), None, POSTED)
    assert watch.status_lines(watching, T0 + timedelta(minutes=22)) == []
    assert (len(posts), len(calls), len(lined), state.followed_builds(data["orders"])) == (3, 1, 1, set())


def test_a_followed_build_is_read_once_as_first_held_and_one_buildkite_answers_none_for_posts_nothing_and_is_said_from_its_third_pass(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = buildkite_reads(monkeypatch, {"forge/release/14185": None})
    heard_from_buildkite()
    part = following(demo)
    monkeypatch.setattr(talk, "posted", lambda name, post: pytest.fail("posted with no build read"))
    watching = watcher(demo)
    said = []
    for minute in (1, 2, 3):
        assert watch.build_lines(watching, T0 + timedelta(minutes=minute)) == []
        said.append(watch.status_lines(watching, T0 + timedelta(minutes=minute)))
    assert said == [[], [], [f"WATCH follow of {FOLLOWED} for o1 failed 3 times in a row: Buildkite answers no build at {FOLLOWED}"]]
    assert calls == [["forge/release/14185"]]
    assert state.load(demo)["orders"]["o1"]["parts"][part]["follow"]["message"] is None


def test_a_watched_thread_that_cannot_be_read_keeps_its_cursor_and_is_said_from_its_third_failure_in_a_row(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with edit(demo) as data:
        data["orders"]["o1"].update(state="open", watch=state.new_watch(f"slack:C0TEAM0001/{TEAMMATE_THREAD}", T0))

    def unreadable(*where: str) -> list[dict]:
        raise slack.SlackError("channel_not_found")

    monkeypatch.setattr(slack, "thread", unreadable)
    watching = watcher(demo)
    said = [watch.watched_lines(watching, T0 + timedelta(minutes=minute)) for minute in (1, 2, 3)]
    assert said == [[], [], [f"WATCH answer of o1's watched thread C0TEAM0001/{TEAMMATE_THREAD} failed 3 times in a row: channel_not_found"]]
    assert state.load(demo)["orders"]["o1"]["watch"]["seen"] == f"{T0.timestamp():.6f}"


def at_top(moment: datetime, **fields: object) -> dict:
    ts = f"{moment.timestamp():.6f}"
    return {"ts": ts, "user": "U0DANA", "user_name": "Dana", "permalink": f"https://acme.slack.com/archives/C0TEAM0001/p{ts.replace('.', '')}", **fields}


def asked_teammate(demo: str, monkeypatch: pytest.MonkeyPatch, top: list[dict]) -> list[tuple[str, ...]]:
    histories: list[tuple[str, ...]] = []
    monkeypatch.setattr(watch.slack, "cc_slack", lambda *argv: histories.append(argv) or {"messages": list(top)})
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", waits=[f"reply:{ASKED_TEAMMATE}"])
    return histories


def test_a_teammates_first_message_after_the_drives_question_frees_the_job_waiting_on_their_reply_and_reaches_the_job_alone(
    demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    calls = catching_up(monkeypatch, demo)
    answer = at_top(T0 + timedelta(minutes=9), text="yes, run it tonight")
    top = [
        at_top(T0 + timedelta(minutes=12), text="and the dashboard too"),
        answer,
        at_top(T0, user="U0BOT", user_name="claude", from_claude=True, text="Can we run the migration tonight?"),
        at_top(T0 - timedelta(minutes=5), text="morning"),
    ]
    histories = asked_teammate(demo, monkeypatch, top)
    watching = watcher(demo)
    assert watch.waits_lines(watching, T0 + timedelta(minutes=13)) == []
    words = f'Dana replied {answer["permalink"]} to the question the drive asked them for j1 at {ASKED_TEAMMATE}: "yes, run it tonight"'
    assert calls == []
    data = state.load(demo)
    job = data["jobs"]["j1"]
    assert (job["waits"], job["said"], [held["words"] for held in job["undelivered"]], work.item_of(data, job)) == ([], None, [words], work.KIND[work.BUILD])
    assert (histories, slack_thread.reads) == ([("history", "--channel", "C0TEAM0001", "--limit", "100", "--json")], [])
    assert watch.waits_lines(watching, T0 + timedelta(minutes=14)) == []
    assert (calls, len(histories)) == ([], 1)


def test_a_reply_wait_ends_on_a_teammates_reply_in_the_questions_thread_never_on_the_drives_or_a_bots_message(
    demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    calls = catching_up(monkeypatch, demo)
    top = [
        at_top(T0 + timedelta(minutes=3), user="U0CI", user_name="buildkite", from_bot=True, text="build passed"),
        at_top(T0, user="U0BOT", user_name="claude", from_claude=True, text="Can we run the migration tonight?", latest_reply=f"{(T0 + timedelta(minutes=2)).timestamp():.6f}"),
    ]
    histories = asked_teammate(demo, monkeypatch, top)
    slack_thread.messages += [
        {"ts": ASKED_TS, "thread_ts": ASKED_TS, "user_name": "claude", "from_claude": True, "text": "Can we run the migration tonight?"},
        {"ts": f"{(T0 + timedelta(minutes=2)).timestamp():.6f}", "thread_ts": ASKED_TS, "user_name": "claude", "from_claude": True, "text": "It holds o1."},
    ]
    watching = watcher(demo)
    assert watch.waits_lines(watching, T0 + timedelta(minutes=4)) == []
    assert (calls, state.load(demo)["jobs"]["j1"]["waits"]) == ([], [f"reply:{ASKED_TEAMMATE}"])
    reply_ts = f"{(T0 + timedelta(minutes=6)).timestamp():.6f}"
    slack_thread.messages.append({"ts": reply_ts, "thread_ts": ASKED_TS, "user": "U0DANA", "user_name": "Dana", "text": "after 9 works"})
    top[1]["latest_reply"] = reply_ts
    reply_link = f"https://acme.slack.com/archives/C0TEAM0001/p{reply_ts.replace('.', '')}?thread_ts={ASKED_TS}&cid=C0TEAM0001"
    assert watch.waits_lines(watching, T0 + timedelta(minutes=7)) == []
    data = state.load(demo)
    assert (data["jobs"]["j1"]["waits"], [held["words"].split(" to the question")[0] for held in data["jobs"]["j1"]["undelivered"]], work.item_of(data, data["jobs"]["j1"])) == ([], [f"Dana replied {reply_link}"], work.KIND[work.BUILD])
    assert (calls, len(histories)) == ([], 2)
    assert slack_thread.reads == [("--channel", "C0TEAM0001", "--thread", ASKED_TS, "--since-ts", ASKED_TS, "--limit", "1000")] * 2


def cci_record(seq: int, **fields: object) -> dict:
    return {"seq": seq, "drive": "durable-agents", "lane": "root", "kind": "note", "topic": STAGING_TOPIC, "text": f"record {seq}", "refs": {}, **fields}


def cci_store(monkeypatch: pytest.MonkeyPatch, records: list[dict]) -> list[list[str]]:
    """cci as the watch reads it, each cci show and cci grep it runs kept: show prints the records at its seqs, grep, newest first, those of its drive on its topics after its --since seq."""
    reads_made: list[list[str]] = []

    def run(argv: list[str], checkout: Path, env: dict | None = None, bounded: bool = True, stdin: str | None = None) -> str:
        if argv[0] != "cci":
            return ""
        reads_made.append(argv)
        if argv[1] == "show":
            return "".join(json.dumps(record) + "\n" for seq in argv[3:] for record in records if str(record["seq"]) == seq)
        drive, since = argv[argv.index("--drive") + 1], int(argv[argv.index("--since") + 1])
        topics = [argv[at + 1] for at, flag in enumerate(argv) if flag == "--topic"]
        return "".join(json.dumps(record) + "\n" for record in sorted(records, key=lambda record: -record["seq"]) if record["drive"] == drive and record.get("topic") in topics and record["seq"] > since)

    monkeypatch.setattr(reads, "run", run)
    return reads_made


def grep_of(drive: str, since: int, *topics: str) -> list[str]:
    return ["cci", "grep", ".", "--drive", drive, *(flag for topic in topics for flag in ("--topic", topic)), "--since", str(since), "--budget", str(agents.AMENDMENT_BYTES), "--json"]


def test_another_drives_first_record_to_the_waiting_job_after_the_one_it_waits_after_on_its_topic_frees_it_with_its_words_and_posts_nothing_to_him_as_j276s_free_staging_line_needed(
    demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = catching_up(monkeypatch, demo)
    body = tmp_path / "body.txt"
    body.write_text("FREE staging is yours: build 17137 left it, start your window now\n")
    records = [
        cci_record(722598, text="before it"),
        cci_record(722599, kind="answer", text="Start no staging run before my FREE line on this topic"),
        cci_record(722640, topic="o2046-claims", text="before j2's record"),
        cci_record(722650, topic="o2046-claims", text="staging is claimed"),
        cci_record(722655, lane="j4486", to=["j6720"], text="HEAD line for another lane"),
        cci_record(722660, to=[f"j1@{demo}"], text="FREE staging is yours: build 17137…", refs={"path": str(body)}),
        cci_record(722670, to=[f"j1@{demo}"], text="a later line"),
    ]
    reads_made = cci_store(monkeypatch, records)
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", waits=["reply:cci:722599"])
        data["jobs"]["j2"] = job(state="waiting", waits=["reply:cci:722650"])
    watching = watcher(demo)
    assert watch.waits_lines(watching, T0 + timedelta(minutes=1)) == []
    words = f"note record 722660 from root@durable-agents, the first on topic {STAGING_TOPIC} after 722599 that j1 waited on: FREE staging is yours: build 17137 left it, start your window now"
    data = state.load(demo)
    assert (data["jobs"]["j1"]["waits"], [(held["words"], held["seq"], held["sender"]) for held in data["jobs"]["j1"]["undelivered"]], work.item_of(data, data["jobs"]["j1"])) == (
        [],
        [(words, "722660", state.WATCH_SENDER)],
        work.KIND[work.BUILD],
    )
    assert (data["jobs"]["j2"]["waits"], data["jobs"]["j2"]["undelivered"]) == (["reply:cci:722650"], [])
    assert reads_made == [["cci", "show", "--json", "722599", "722650"], grep_of("durable-agents", 722599, STAGING_TOPIC, "o2046-claims")]
    records.append(cci_record(722680, topic="o2046-claims", to=[f"j2@{demo}"], text="staging is free"))
    assert watch.waits_lines(watching, T0 + timedelta(minutes=2)) == []
    assert reads_made[2:] == [["cci", "show", "--json", "722650"], grep_of("durable-agents", 722650, "o2046-claims")]
    assert watch.waits_lines(watching, T0 + timedelta(minutes=3)) == []
    assert (calls, len(reads_made)) == ([], 4)


def test_725715_a_record_on_the_other_drive_whose_lane_names_the_waiting_drive_is_its_own_line_and_frees_nothing_while_the_other_drives_next_one_does(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    catching_up(monkeypatch, demo)
    records = [
        cci_record(725268, kind="defect", text="o78 and o2046 both claim staging"),
        cci_record(725697, kind="answer", lane=f"meta@{demo}", text="o78 holds staging until 3 PM"),
    ]
    cci_store(monkeypatch, records)
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", waits=["reply:cci:725268"])
    watching = watcher(demo)
    assert watch.waits_lines(watching, T0 + timedelta(minutes=1)) == []
    assert state.load(demo)["jobs"]["j1"]["waits"] == ["reply:cci:725268"]
    records.append(cci_record(725720, to=[f"j1@{demo}"], text="FREE staging is yours"))
    assert watch.waits_lines(watching, T0 + timedelta(minutes=2)) == []
    data = state.load(demo)
    assert (data["jobs"]["j1"]["waits"], [held["seq"] for held in data["jobs"]["j1"]["undelivered"]], work.item_of(data, data["jobs"]["j1"])) == ([], ["725720"], work.KIND[work.BUILD])


def test_j182_a_busy_topics_records_to_other_lanes_wake_no_job_while_one_to_its_lane_or_answering_its_record_does(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    catching_up(monkeypatch, demo)
    records = [
        cci_record(736559, lane="platform", kind="ask", to=["root"], text="Start o2316's SandDB cut now?"),
        cci_record(736583, lane="j4486", kind="head", to=["j6720"], text="HEAD 3 PRs"),
        cci_record(736590, lane=f"j2@{demo}", kind="ask", to=["platform"], text="my place in the queue?"),
        cci_record(736604, lane="platform", to=["root"], text="queue moved"),
    ]
    cci_store(monkeypatch, records)
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", waits=["reply:cci:736583"])
        data["jobs"]["j2"] = job(state="waiting", waits=["reply:cci:736559"])
    watching = watcher(demo)
    assert watch.waits_lines(watching, T0 + timedelta(minutes=1)) == []
    data = state.load(demo)
    assert [(data["jobs"][job_id]["waits"], data["jobs"][job_id]["undelivered"]) for job_id in ("j1", "j2")] == [(["reply:cci:736583"], []), (["reply:cci:736559"], [])]
    records.append(cci_record(736607, lane="platform", re=736590, to=["root"], text="you are third"))
    watch.waits_lines(watching, T0 + timedelta(minutes=2))
    data = state.load(demo)
    assert [(data["jobs"][job_id]["waits"], [held["seq"] for held in data["jobs"][job_id]["undelivered"]]) for job_id in ("j1", "j2")] == [(["reply:cci:736583"], []), ([], ["736607"])]
    records.append(cci_record(736625, lane="platform", kind="answer", to=[f"j1@{demo}"], text="GO: your place is now"))
    assert watch.waits_lines(watching, T0 + timedelta(minutes=3)) == []
    data = state.load(demo)
    assert (data["jobs"]["j1"]["waits"], [held["seq"] for held in data["jobs"]["j1"]["undelivered"]]) == ([], ["736625"])


@pytest.mark.parametrize("waiting", [1, 100])
def test_the_watch_reads_every_job_waiting_on_another_drives_record_in_one_cci_show_and_one_cci_grep_a_drive_whatever_their_number(demo: str, monkeypatch: pytest.MonkeyPatch, waiting: int) -> None:
    catching_up(monkeypatch, demo)
    anchors = [cci_record(723000 + 2 * n, topic=f"topic-{n}") for n in range(waiting)]
    reads_made = cci_store(monkeypatch, [*anchors, *(cci_record(anchor["seq"] + 1, topic=anchor["topic"], to=[f"j{100 + 2 * n}@{demo}"]) for n, anchor in enumerate(anchors[::2]))])
    with edit(demo) as data:
        for n, anchor in enumerate(anchors):
            data["jobs"][f"j{100 + n}"] = job(state="waiting", waits=[f"reply:cci:{anchor['seq']}"])
    watch.waits_lines(watcher(demo), T0 + timedelta(minutes=1))
    data = state.load(demo)
    assert [argv[1] for argv in reads_made] == ["show", "grep"]
    assert [bool(data["jobs"][f"j{100 + n}"]["waits"]) for n in range(waiting)] == [n % 2 == 1 for n in range(waiting)]


def test_a_record_cci_cannot_read_that_a_job_waits_after_frees_nothing_and_is_said_from_its_third_failure_in_a_row(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    catching_up(monkeypatch, demo)

    def unreadable(argv: list[str], checkout: Path, env: dict | None = None, bounded: bool = True, stdin: str | None = None) -> str:
        raise reads.ReadError("cci show exited 1: cci: show: no record #722599")

    monkeypatch.setattr(reads, "run", unreadable)
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", waits=["reply:cci:722599"])
    watching = watcher(demo)
    said = [watch.waits_lines(watching, T0 + timedelta(minutes=minute)) for minute in (1, 2, 3)]
    unread = "cci could not read the record at 722599 that reply:cci:<seq> follows: cci show exited 1: cci: show: no record #722599"
    assert said == [[], [], [f"WATCH read of the records reply:cci:722599 wait on failed 3 times in a row: {unread}"]]
    assert state.load(demo)["jobs"]["j1"]["waits"] == ["reply:cci:722599"]


def test_incident_pr_lines_stage_only_the_named_prs_and_reuse_one_projection_index(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with edit(demo) as data:
        data["orders"]["o4"] = order(text="unrelated", jobs=["j4"])
        data["jobs"]["j4"] = job(order="o4")
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j4", "o4")
    data = cli.with_cards(demo, state.load(demo))
    jobs = state.orders_jobs(data, ["o1"])
    keys = watch.incident_prs(data, ["o1"], jobs, render.prs_by_job(data))
    expected = [f"PRs, as the watch last read them from GitHub: {len(keys)}", *(f"- {render.pr_line(data, key, data['prs'][key]['card_state'])}" for key in keys)]
    staged: list[str] = []
    indexed: list[bool] = []
    of, graph = stage.of, state.pr_graph

    def stage_one(data: dict, key: str, *args: object) -> stage.Stage:
        staged.append(key)
        return of(data, key, *args)

    def graph_of(data: dict) -> object:
        indexed.append(state.index_of(data) is not None)
        return graph(data)

    monkeypatch.setattr(stage, "of", stage_one)
    monkeypatch.setattr(state, "pr_graph", graph_of)
    assert watch.incident_pr_lines(demo, ["o1"]) == expected
    assert staged == [key for key in keys if data["prs"][key]["state"] == "open"]
    assert OTHER not in staged
    assert indexed and all(indexed)


O1114_THREAD = "1794562114.050739"
O768_THREAD = "1794509787.694279"
O768_WORDS = "replay the SandSQL folder list on one staging pass box"


def joined_from_another_thread(demo: str) -> None:
    """o1114 and o768 as 720284 found them: o768, an order that is no incident with its own thread in his DM, joined to o1114 by a FIX line naming its PR, each with a job at work."""
    with edit(demo) as data:
        data["orders"]["o1"].update(thread=["D123", O1114_THREAD])
        data["orders"]["o2"] = order(text=O768_WORDS, joins="o1", thread=["D123", O768_THREAD], jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", kind="reader")


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


BACK = T0 + timedelta(minutes=95)


def catching_up(monkeypatch: pytest.MonkeyPatch, demo: str) -> list[tuple[list[str] | dict, str | None]]:
    """Each command the drive runs and each record it posts with its body, a post answering its seq."""
    from drive import tools

    with edit(demo) as data:
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
    monkeypatch.setattr(watch, "passed", lambda watching, moment, until: [])
    run_watch(demo, monkeypatch)
    assert not state.pid_path(demo).exists()


def test_a_watch_speaks_to_orca_as_the_drives_own_terminal_whatever_pane_started_it(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCA_PANE_KEY", "tab:starter")
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
    monkeypatch.setattr(watch, "passed", lambda watching, moment, until: pytest.fail("a pass ran outside the watch's launch agent"))
    assert watch.main(demo) == 0
    assert (watch_process.ensured, capsys.readouterr().out, state.pid_path(demo).exists()) == ([demo], f"the watch runs for {demo}\n", False)


def test_the_watch_waits_for_one_an_older_install_ran_under_a_monitor_and_sends_the_lines_it_held_once(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    passes = []
    monkeypatch.setattr(watch, "passed", lambda watching, moment, until: passes.append(moment) or [])
    sent = posts(monkeypatch)
    state.held_path(demo).write_text('ORPHAN o1 "ship": no job\n')
    state.monitor_pid_path(demo).write_text(f"{os.getppid()}\n")
    run_watch(demo, monkeypatch)
    assert (passes, sent) == ([], [])
    state.monitor_pid_path(demo).write_text("999999999\n")
    run_watch(demo, monkeypatch)
    assert (passes, posted(sent)) == ([], [("handoff", f"root@{demo}", 'ORPHAN o1 "ship": no job')])
    run_watch(demo, monkeypatch)
    assert (len(passes), len(sent)) == (1, 1)


def test_a_crashed_watch_wakes_the_coordinator_naming_its_version_and_the_line_of_the_drives_code_that_raised(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(watching: watch.Watcher, moment: datetime, until: Callable[[], object]) -> list[str]:
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


def mid_pass(monkeypatch: pytest.MonkeyPatch, before_lane_lines: Callable[[], object]) -> None:
    real = watch.lane_lines
    monkeypatch.setattr(watch, "lane_lines", lambda watching, moment: before_lane_lines() or real(watching, moment))


def test_a_line_whose_post_failed_goes_to_the_watch_its_launch_agent_starts_on_the_newer_install_it_ended_for_between_passes(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upgrades(monkeypatch, tmp_path, [True])
    tapped = f"TAP Hold {KEY} [o1 j1]"
    monkeypatch.setattr(watch, "passed", lambda watching, moment, until: [[tapped]])
    monkeypatch.setattr(watch.time, "sleep", lambda seconds: None)
    sent = posts(monkeypatch, [False])
    launched(monkeypatch, demo)
    assert watch.main(demo) == 0
    assert (sent, state.held_path(demo).read_text()) == ([], f"{tapped}\n")
    monkeypatch.setattr(watch, "upgraded", lambda: None)
    monkeypatch.setattr(watch, "passed", lambda watching, moment, until: [])
    run_watch(demo, monkeypatch)
    assert posted(sent) == [("handoff", f"root@{demo}", tapped)]


def spied(monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...]) -> list[str]:
    ran: list[str] = []
    for name in names:
        real = getattr(watch, name)
        monkeypatch.setattr(watch, name, functools.wraps(real)(lambda *args, real=real, name=name, **kwargs: ran.append(name) or real(*args, **kwargs)))
    return ran


def test_a_watch_that_finds_a_newer_install_between_two_stages_of_a_pass_runs_that_pass_to_its_end_and_ends_before_the_next(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(monkeypatch, {})
    posts(monkeypatch)
    install = tmp_path / "cache" / "9.9.9" / "bin" / "drive"
    newer: list[Path] = []
    monkeypatch.setattr(watch, "upgraded", lambda: newer[-1] if newer else None)
    ran = spied(monkeypatch, ("settle_lines", "release_lanes", "standing_lines"))
    mid_pass(monkeypatch, lambda: newer.append(install))
    monkeypatch.setattr(watch.time, "sleep", lambda seconds: None)
    launched(monkeypatch, demo)
    assert watch.main(demo) == 0
    assert (ran, state.resume_path(demo).exists()) == (["settle_lines", "release_lanes", "standing_lines"], False)
    assert f"ended on long-running {state.VERSION} for {install}, before its next pass" in capsys.readouterr().out.splitlines()


def test_spawn_lines_starts_each_worker_the_queue_records_on_the_drives_own_watch_every_pass_and_a_projects_watch_starts_none(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[list[str]] = []
    kinds: list[list[str]] = []
    monkeypatch.setattr(watch, "start_verb", lambda watcher, job_id, args, moment: started.append(args) or 1)
    monkeypatch.setattr(work.Kind, "wanted", lambda self, data, where, moment: self.name == work.RESEARCH and where == "remote")
    monkeypatch.setattr(work, "spawned", lambda data, moment, given: kinds.append([kind.name for kind in given]) or (["WORK j9 records a research worker on a Sprite, its first group claimed"], [["work", "start", "j9"]]))
    assert watch.spawn_lines(watch.Watcher(demo, offset=state.events_end(demo), project="Iris"), state.now()) == []
    assert watch.spawn_lines(watcher(demo), state.now()) == ["WORK j9 records a research worker on a Sprite, its first group claimed"]
    assert started == [["work", "start", "j9"]]


def test_a_merge_prover_carries_only_the_parts_its_merged_prs_deliver_never_another_prs_staging_test(named_repo: None) -> None:
    merged = "Forge-AI/monorepo#56521"
    data = {"prs": {merged: {"order": "o1482", "parts": []}, "Forge-AI/monorepo#7": {"order": "o1", "parts": ["the fix"]}}}
    work = "Dream ledger votes keep no memory sentence or reason text in Postgres"
    assert [part for part in (work, "staging test of #56306 before it lands", "staging test of #56521 before it lands") if watch.merge_delivers(data, [merged], "o1482", part)] == [work, "staging test of #56521 before it lands"]
    assert (watch.merge_delivers(data, ["Forge-AI/monorepo#7"], "o1", "the fix"), watch.merge_delivers(data, ["Forge-AI/monorepo#7"], "o1", "another part")) == (True, False)


def test_a_builder_that_builds_nothing_takes_no_slot_and_skips_the_load_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(place, "strained", lambda: "this Mac's 1-minute load is 49")
    job = {"kind": "builder", "order": "o1", "carries": {}, "parts": ["staging test of #42 before it lands"], "goal": "test #42 on staging", "priority": None, "builds_nothing": False}
    data = {"orders": {"o1": {"text": "test #42 on staging", "state": "open", "priority": None, "incident": False, "parts": job["parts"], "urgent": []}}, "jobs": {"j1": job}}
    monkeypatch.setattr(place, "preempts", lambda data, job: False)
    assert place.builder_cap(data, "j1", state.now()) is None
    job["parts"] = ["build the fix"]
    assert place.builder_cap(data, "j1", state.now()) == "this Mac's 1-minute load is 49"


def test_a_watch_hands_off_to_a_newer_install_only_once_it_ran_hand_off_every_or_hand_off_latest_with_a_pass_cut_short_or_while_an_incident_is_open(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, "HAND_OFF_EVERY", timedelta(minutes=10))
    monkeypatch.setattr(watch, "HAND_OFF_LATEST", timedelta(minutes=20))
    watching = watcher(demo)
    assert (watch.handing_off(watching, 599.0), watch.handing_off(watching, 600.0)) == (False, True)
    watching.resume = "release_lanes"
    assert (watch.handing_off(watching, 600.0), watch.handing_off(watching, 1199.0), watch.handing_off(watching, 1200.0)) == (False, False, True)
    watching.resume = None
    with edit(demo) as data:
        data["orders"]["o1"]["incident"] = True
    assert watch.handing_off(watching, 0.0) is True


@pytest.mark.parametrize(
    "operation,fields",
    [
        ("digest", ("slack",)),
        ("slots", ("slots",)),
        ("seats", ("orders",)),
        ("handoff", ("orders",)),
        ("idle_communicators", ("orders", "communicators")),
        ("retired_seats", ("orders", "subcoordinators")),
        ("compaction", ("roles", "communicators", "owed_replies")),
        ("down", ()),
    ],
)
def test_watch_metadata_reads_do_not_decode_unrelated_collections(demo: str, operation: str, fields: tuple[str, ...], monkeypatch: pytest.MonkeyPatch) -> None:
    from drive import picture

    monkeypatch.setattr(watch, "HAND_OFF_EVERY", timedelta(minutes=10))
    monkeypatch.setattr(watch, "HAND_OFF_LATEST", timedelta(minutes=20))

    with edit(demo) as data:
        data["slack"]["digest"] = {"day": T0.astimezone(view.ZONE).date().isoformat()}
        data["orders"]["o1"]["priority"] = "Priority o1"
    watching = watcher(demo)
    watching.cards = NO_CARDS
    priority = watcher(demo)
    priority.project = "Priority o1"
    watching.others = {priority.project: priority}
    for mark in (state.drive_dir(demo) / "pictures" / picture.SOURCED, state.drive_dir(demo) / "asks" / state.REBUILT_ASKS):
        mark.parent.mkdir(exist_ok=True)
        mark.touch()
    state.load(demo)
    path = state.database_path(demo)
    cached = state.WHOLE_READS[(path, path.stat().st_ino)].shaped
    assert cached
    original = dict(cached.fields), dict(cached.collections)
    cached.fields.update({key: b"" for key in cached.fields if key not in fields})
    cached.collections.update({key: {"unrequested": b""} for key in cached.collections if key not in fields})
    try:
        if operation == "digest":
            assert watch.digest_lines(watching, T0) == []
        elif operation == "slots":
            assert watch.slot_lines(watching, T0) == []
        elif operation == "seats":
            assert watch.seated_first(watching) == [priority, watching]
        elif operation == "handoff":
            assert watch.handing_off(watching, 0.0) is False
        elif operation == "idle_communicators":
            assert watch.communicators_idle(watching, T0) == []
        elif operation == "retired_seats":
            monkeypatch.setattr(state, "scratch", lambda: False)
            assert watch.seats_retired(demo) == {}
        elif operation == "compaction":
            monkeypatch.setattr(watch, "session_transcript", lambda held: state.drive_dir(demo) / "missing-transcript.jsonl")
            assert watch.compact_lines(watching, T0) == []
        else:
            monkeypatch.setattr(orca, "live_pid", lambda session: 123)
            assert watch.down_lines(watching, T0) == []
    finally:
        cached.fields, cached.collections = original


def test_a_held_resume_naming_no_stage_of_this_install_runs_the_whole_pass(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    posts(monkeypatch)
    state.hold_resume(demo, None, "renamed_lines")
    ran = spied(monkeypatch, ("settle_lines", "release_lanes", "standing_lines"))
    run_watch(demo, monkeypatch)
    assert ran == ["settle_lines", "release_lanes", "standing_lines"]


def test_a_watch_that_crashes_mid_pass_posts_its_stop_but_not_the_job_line_saying_the_watch_starts_the_prover_itself_and_the_watch_its_launch_agent_starts_again_says_none_again(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"READY {state.pr_url(KEY)}")
    serve(monkeypatch, {KEY: read(state="MERGED", mergeCommit="ccc333")})
    beat = watch.beat
    monkeypatch.setattr(watch, "beat", lambda step, staged=False: state.load("gone") if step == "lane_lines" else beat(step, staged))
    sent = posts(monkeypatch)
    run_landing_loop(demo, monkeypatch)
    launched(monkeypatch, demo)
    with pytest.raises(state.DriveError):
        watch.main(demo)
    assert (state.load(demo)["jobs"]["j2"]["goal"], [text.split(":")[0] for _, _, text in posted(sent)], state.held_path(demo).read_text()) == (
        f"prove {KEY} live",
        ["WATCH stopped on long-running " + state.VERSION],
        "",
    )
    stopped = list(sent)
    monkeypatch.setattr(watch, "passed", lambda watching, moment, until: [])
    run_watch(demo, monkeypatch)
    assert sent == stopped


def test_ensure_loads_a_launch_agent_that_starts_the_watch_again_after_any_exit_with_no_session_or_secret_unless_one_is_loaded(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[tuple[str, ...]] = []
    loaded = iter([False, True])
    monkeypatch.setenv("OPENAI_API_KEY", "a session secret")
    monkeypatch.setenv("PATH", f"{tmp_path}/mise/installs/cc-remote/0.27.26:{tmp_path}/worktree/tools/bin:{os.environ['PATH']}")
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "mise"))
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
    assert env["PATH"].split(os.pathsep)[:2] == [str(tmp_path / "mise" / "shims"), str(Path.home() / ".local" / "bin")]
    assert ("/mise/installs/" in env["PATH"], "/worktree/tools/bin" in env["PATH"]) == (False, False)
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
    monkeypatch.setattr(watch, "passed", lambda watching, moment, until: seen.append(reads.run(["printenv", "BUILDKITE_API_TOKEN"], tmp_path)) or [])
    run_watch(demo, monkeypatch)
    assert seen == ["the settings token\n"]


@pytest.mark.parametrize("moved_by", ["LONG_RUNNING_STATE", "XDG_STATE_HOME"])
def test_a_drive_whose_state_root_is_moved_gets_no_watch_process_and_a_watch_started_there_ends(
    demo: str, state_root: Path, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess, capsys: pytest.CaptureFixture[str], moved_by: str
) -> None:
    started = []
    if moved_by == "LONG_RUNNING_STATE":
        monkeypatch.setenv("LONG_RUNNING_STATE", str(state_root))
    monkeypatch.setattr(state, "scratch", REAL_SCRATCH)
    monkeypatch.setattr(watch, "ensure", REAL_ENSURE)
    monkeypatch.setattr(launchd, "keep", lambda *args: started.append(args))
    monkeypatch.setattr(watch, "passed", lambda watching, moment, until: pytest.fail(f"a pass ran on a drive {moved_by} moves"))
    assert cli.main(["watch", "--drive", demo]) == 0
    assert "watch.run_pass" in capsys.readouterr().out
    assert watch.main(demo) == 0
    assert (started, state.pid_path(demo).exists()) == ([], False)


def test_drive_watch_starts_the_watch_from_any_roles_session(
    demo: str, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess, capsys: pytest.CaptureFixture[str]
) -> None:
    with edit(demo) as data:
        data["roles"]["communicator"] = {"session": "talker", "cwd": "/work", "started": state.stamp(T0)}
    assert cli.main(["watch", "--drive", demo]) == 0
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    assert cli.main(["watch", "--drive", demo]) == 0
    assert capsys.readouterr().out == f"the watch runs for {demo}\n" * 2
    assert watch_process.ensured == [demo, demo]


def sub_coordinated(name: str, project: str = "Iris", session: str = "iris-1", cwd: str = "/work") -> None:
    with edit(name) as data:
        data["orders"]["o1"]["project"] = project
        cli.bind_subcoordinator(data, project, {"session": session, "terminal": None, "cwd": cwd, "started": state.stamp(T0)})


def moved(orders: str, lane: str, to: str, jobs: str, own: str = "none") -> str:
    return (
        f"MOVED {orders} from {lane} to {to}: unfinished jobs {jobs}; agents of your session among them: {own}, which run on and take SendMessage from you alone."
        " Add, brief and land nothing more on these orders, and send their holder, as drive status names it, one handoff record of what you hold on them"
        " outside the drive file: holds, waits, jobs you meant to add or brief, promises to him"
    )


def test_a_projects_watch_hears_only_its_orders_jobs_prs_and_orphans_and_the_drives_watch_the_rest(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sub_coordinated(demo)
    still = state.stamp(T0 - timedelta(minutes=10))
    with edit(demo) as data:
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
    assert watch.run_pass(iris, T0) == [f"TAP Approve {KEY} [o1 j1]", f"PR {KEY}: checks failed [o1 j1]", 'ORPHAN o3 "the logs page": no job']
    assert watch.run_pass(top, T0) == [
        f"TAP Approve {OTHER} [o2 j2]",
        "EVENT refused question=Which? pick=Yes",
        f"PR {OTHER}: checks failed [o2 j2]",
        'ORPHAN o4 "a page for alerts": no job',
    ]
    assert calls == [[KEY], [OTHER]]
    assert (iris.lane, top.lane) == ("iris", "root")


def test_the_drives_watch_keeps_a_projects_lines_until_a_sub_coordinator_is_bound_for_it_and_that_ones_watch_takes_them_from_there(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with edit(demo) as data:
        data["orders"]["o1"]["project"] = "Iris"
        data["jobs"]["j1"]["state"] = "waiting"
    reads = {KEY: read(checks=RED)}
    calls = serve(monkeypatch, reads)
    top = watcher(demo)
    assert watch.run_pass(top, T0) == [f"PR {KEY}: checks failed [o1 j1]"]
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Hold", lane=state.heard_lane(state.load(demo), "o1"))
    sub_coordinated(demo)
    bound = state.load(demo)["subcoordinators"]["Iris"]["from"]
    assert [(event["kind"], event["lane"], event["orders"], event["to"]) for event, _ in state.read_events(demo, bound)] == [(state.ORDERS_MOVED, "root", ["o1"], ["iris"])]
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Approve", lane=state.heard_lane(state.load(demo), "o1"))
    iris = watch.Watcher(demo, offset=state.watch_cursor(demo, "Iris"), project="Iris")
    reads[KEY] = read(state="MERGED", mergeCommit="ccc333", checks=RED)
    later = T0 + timedelta(minutes=2)
    sent = posts(monkeypatch)
    assert watch.run_pass(top, later) == [moved("o1", "root", "iris", "j1")]
    assert posted(sent) == [("handoff", f"iris@{demo}", f"TAP Hold {KEY} [o1 j1]")]
    assert watch.run_pass(iris, later) == [
        f"TAP Approve {KEY} [o1 j1]",
        f"PR {KEY}: merged [o1 j1]",
        'ORPHAN o1 "ship the watch": j1 waits on "nothing named" and no open PR, card or due time can wake it',
    ]
    assert calls == [[KEY], [KEY]]


def test_a_free_line_reaches_the_watch_of_the_session_whose_agent_runs_the_job_and_the_watch_hearing_its_order_once_none_does(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """His pick "Send them to the agent's starter": FREE 682910 on j2600 reached platform, which could not SendMessage the top's agent, and its hand-off to the top took 8 minutes 21 seconds."""
    serve(monkeypatch, {})
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j2"] = job(goal="release the service")
        data["jobs"]["j1"].update(state="waiting", waits=["j2"], agent="agent-j1", where="local", session="session-under-test")
    sub_coordinated(demo)
    iris, top = watch.Watcher(demo, offset=state.events_end(demo), project="Iris"), watcher(demo)
    assert (watch.run_pass(iris, T0), watch.run_pass(top, T0)) == ([], [])
    with edit(demo) as data:
        data["jobs"]["j2"]["state"] = "done"
    freed = T0 + timedelta(seconds=60)
    assert (watch.run_pass(iris, freed), watch.run_pass(top, freed)) == ([], ["FREE j1 o1: j2 answered or done, nothing else holds it"])
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", waits=["j2"], session="gone")
        data["jobs"]["j2"]["state"] = "running"
    assert (watch.run_pass(iris, freed + timedelta(minutes=1)), watch.run_pass(top, freed + timedelta(minutes=1))) == ([], [])
    with edit(demo) as data:
        data["jobs"]["j2"]["state"] = "done"
    later = freed + timedelta(minutes=2)
    assert (watch.run_pass(iris, later), watch.run_pass(top, later)) == (["FREE j1 o1: j2 answered or done, nothing else holds it"], [])


def test_a_projects_watch_hands_a_stuck_or_lane_line_to_the_session_that_started_its_jobs_agent_and_keeps_the_rest(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub_coordinated(demo, "Platform", "platform-1")
    with edit(demo) as data:
        data["jobs"]["j1"] |= {"agent": "a4667d5c611bb5233", "session": "session-under-test"}
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(agent="a-gone", session="ended-session")
    sent = posts(monkeypatch)
    platform = watch.Watcher(demo, offset=state.events_end(demo), project="Platform")
    stuck = 'STUCK j1 o1: its agent\'s Bash call "gh pr checks" has had no result for 30 minutes'
    died = 'LANE j1 o1 died as agent a4667d5c611bb5233 on this Mac: its transcript ended, while it worked on "watch"'
    kept = [f"PR {KEY}: checks failed [o1 j1]", 'STUCK j2 o1: its agent\'s Bash call "gh pr checks" has had no result for 30 minutes']
    assert watch.handed(platform, [stuck, died, *kept], T0) == kept
    assert (posted(sent), [argv[argv.index("--text") + 1] for argv in sent]) == ([("handoff", f"root@{demo}", stuck), ("handoff", f"root@{demo}", died)], ["2 lines: 1 STUCK, 1 LANE"])
    assert platform.unsent == {"root": []}


def test_a_save_that_moves_open_orders_hands_each_session_losing_them_one_moved_line_and_status_names_the_session_whose_agent_runs_a_moved_job(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j2"] = job(goal="release the service")
        data["jobs"]["j1"].update(state="waiting", waits=["j2"], agent="agent-j1", where="local", session="session-under-test")
        data["orders"]["o2"] = order(text="fix the hook", jobs=["j3", "j4"], owner="meta")
        data["jobs"]["j3"] = job(order="o2", state="waiting", waits=["j2"])
        data["jobs"]["j4"] = job(order="o2", state="done")
    sent = posts(monkeypatch)
    top = watcher(demo)
    with edit(demo) as data:
        data["jobs"]["j2"]["goal"] = "release the service today"
    sub_coordinated(demo)
    with edit(demo) as data:
        data["orders"]["o2"]["owner"] = None
    assert watch.sent(top, "root", watch.run_pass(top, T0), T0) == []
    assert posted(sent) == [
        ("handoff", f"meta@{demo}", moved("o2", "meta", "root", "j3")),
        ("handoff", f"root@{demo}", moved("o1", "root", "iris", "j1", "j1")),
    ]
    data = state.load(demo)
    assert [render.job_line(data, job_id, data["jobs"][job_id]) for job_id in ("j1", "j3")] == [
        "j1 builder waiting agent agent-j1 of root's session, whose SendMessage alone reaches it, so drive job resume posts a text there: watch; waits on j2",
        "j3 builder waiting: watch; waits on j2",
    ]


def test_the_drives_watch_runs_each_projects_passes_from_where_the_project_stands_and_posts_them_to_its_sub_coordinators_lane(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub_coordinated(demo)
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Hold")
    state.save_watch_cursor(demo, state.events_end(demo))
    assert (state.cursor_path(demo, "Iris").exists(), state.watch_cursor(demo, "Iris")) == (False, state.load(demo)["subcoordinators"]["Iris"]["from"])
    state.append_event(demo, "tap", repo="Forge-AI/monorepo", number=42, label="Hold", lane="iris")
    serve(monkeypatch, {})
    sent = posts(monkeypatch)
    run_watch(demo, monkeypatch)
    assert posted(sent) == [("handoff", f"iris@{demo}", f"TAP Hold {KEY} [o1 j1]")]
    assert (state.watch_cursor(demo, "Iris"), state.pid_path(demo, "Iris").exists(), state.pid_path(demo).exists()) == (state.events_end(demo), False, False)

    def crash(watching: watch.Watcher, moment: datetime, until: Callable[[], object]) -> list[str]:
        if watching.project:
            raise OSError("disk full")
        return []

    monkeypatch.setattr(watch, "passed", crash)
    with pytest.raises(OSError):
        watch.main(demo)
    kind, to, text = posted(sent)[-1]
    assert (kind, to) == ("handoff", f"iris@{demo}")
    log = re.escape(str(state.watch_log_path(demo)))
    assert re.fullmatch(rf"WATCH stopped on long-running {re.escape(state.VERSION)}: OSError at watch\.py:\d+ in watching: disk full; its traceback is in {log}; its launch agent starts it again within 10 seconds", text)


def test_a_project_watch_an_older_install_loaded_takes_its_launch_agent_down_and_leaves_its_project_to_the_drives_watch(
    demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, watch_process: WatchProcess
) -> None:
    sub_coordinated(demo)
    agent = watch.label(demo, "Iris")
    plist = tmp_path / f"{agent}.plist"
    plist.write_text("")
    state.pid_path(demo, "Iris").write_text(f"{os.getpid()}\n")
    called = []
    monkeypatch.setattr(launchd, "LAUNCH_AGENTS", tmp_path)
    monkeypatch.setattr(launchd, "kept", lambda label: label == agent)
    monkeypatch.setattr(launchd, "launchctl", lambda *args: called.append(args))
    launched(monkeypatch, demo, "Iris")
    assert watch.main(demo, "Iris") == 0
    assert (plist.exists(), state.pid_path(demo, "Iris").exists(), called, watch_process.ensured) == (False, False, [("bootout", launchd.service(agent))], [demo])
    assert cli.main(["watch", "--drive", demo, "--project", "Iris"]) == 0
    assert watch_process.ensured == [demo, demo]


REAL_LOCAL_GAP = watch.local_gap
REAL_CODEX_GAP = watch.codex_gap
CARD = "https://acme.slack.com/archives/D123/p1794200000000100"


def files_order(data: dict, **overrides: object) -> None:
    data["orders"]["o2"] = order(text="build the files design", jobs=["j2"], **overrides)
    data["orders"]["o2"]["parts"] = {"stack 1": state.new_part(), "stack 2": state.new_part()}
    data["jobs"]["j2"] = job(order="o2", parts=["stack 1"])


def test_a_part_no_job_carries_is_an_orphan_said_once_five_minutes_after_its_order_moved_which_drive_status_lists_until_a_job_joins_it_before_any_pass(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    with edit(demo) as data:
        files_order(data)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == ['ORPHAN o2 "stack 2": no job']
    assert [watch.run_pass(watching, T0 + timedelta(minutes=minutes)) for minutes in (6, 35, 65, 95)] == [[], [], [], []]
    assert render.orphans(state.load(demo), "o2", 80) == [f'  ORPHAN "stack 2", said {render.when(state.stamp(T0 + timedelta(minutes=5)))}: no job']
    joined = T0 + timedelta(minutes=96)
    with edit(demo) as data:
        data["jobs"]["j2"].update(parts=["stack 1", "stack 2"], updated=state.stamp(joined))
    assert render.orphans(state.load(demo), "o2", 80) == []
    assert state.load(demo)["orders"]["o2"]["parts"]["stack 2"]["orphan_said"] == state.stamp(T0 + timedelta(minutes=5))
    assert [watch.run_pass(watching, joined + timedelta(minutes=minutes)) for minutes in (0, 2)] == [[], []]
    assert state.load(demo)["orders"]["o2"]["parts"]["stack 2"]["orphan_said"] is None


def test_a_part_of_an_order_he_has_not_scheduled_is_no_orphan_and_heals_the_one_said_until_he_schedules_it(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with edit(demo) as data:
        files_order(data)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == ['ORPHAN o2 "stack 2": no job']
    with edit(demo) as data:
        data["orders"]["o2"]["state"] = "waiting"
    assert [watch.run_pass(watching, T0 + timedelta(minutes=minutes)) for minutes in (6, 40)] == [[], []]
    assert state.load(demo)["orders"]["o2"]["parts"]["stack 2"]["orphan_said"] is None
    assert render.orphans(state.load(demo), "o2", 80) == []
    scheduled = T0 + timedelta(minutes=41)
    with edit(demo) as data:
        data["orders"]["o2"].update(state="open", updated=state.stamp(scheduled))
    assert watch.run_pass(watching, scheduled + timedelta(minutes=5)) == ['ORPHAN o2 "stack 2": no job']


def standing_order(data: dict, jobs: list[str]) -> None:
    data["orders"]["o2"] = order(text="keep testing runs on the new tree", jobs=jobs, parts={"codex test box": state.new_part() | {"standing": BOX_GOAL}})


def launches(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    detached: list[list[str]] = []
    monkeypatch.setattr(state, "detach", lambda name, args, log: detached.append(args) or 1)
    return detached


def test_a_standing_pass_that_failed_on_its_proof_hands_the_next_pass_the_last_lines_that_proof_printed(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    launches(monkeypatch)
    blocked, missing = "BLOCKED j2: /drive/repro/j2.py exits 1: it printed no FAIL line", "FileNotFoundError: [Errno 2] No such file or directory: '/drive/repro/manifest.json'"
    with edit(demo) as data:
        standing_order(data, ["j2"])
        data["jobs"]["j2"] = job(order="o2", kind="codex", goal=BOX_GOAL, parts=["codex test box"])
    with edit(demo) as data:
        data["jobs"]["j2"].update(state="failed", said=blocked)
    state.keep_report(demo, "j2", blocked, f"{blocked}\nCHECK: {state.proof_path(demo, 'j2')}\n")
    state.proof_path(demo, "j2").parent.mkdir(exist_ok=True)
    state.proof_path(demo, "j2").write_text(f"Traceback (most recent call last):\n{missing}\n")
    staged(watch.standing_lines, watcher(demo), T0 + timedelta(hours=1))
    brief_text = Path(state.load(demo)["jobs"]["j3"]["brief"]).read_text()
    assert f"> {blocked}\n> CHECK: {state.proof_path(demo, 'j2')}\n>\n> Its proof failed, the last lines it printed:\n> Traceback (most recent call last):\n> {missing}\n" in brief_text


def test_a_standing_part_its_running_pass_holds_starts_none_and_once_that_pass_ends_the_watch_adds_and_launches_the_next_on_its_goal_quoting_its_return(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    detached = launches(monkeypatch)
    said = "HANDOFF /drive/scratch/j2/handoff.md: box i-0ee4 under o2-codex-box, round 4 ran, staging untested"
    with edit(demo) as data:
        standing_order(data, ["j2"])
        data["jobs"]["j2"] = job(order="o2", kind="codex", goal=BOX_GOAL, parts=["codex test box"], brief_flags={"where": "this Mac and one EC2 devbox", "handoff": "round 3"})
    watching = watcher(demo)
    assert (staged(watch.standing_lines, watching, T0 + timedelta(hours=1)), detached) == ([], [])
    with edit(demo) as data:
        data["jobs"]["j2"].update(state="stopped", said=said)
    assert staged(watch.standing_lines, watching, T0 + timedelta(hours=1)) == [
        f'STANDING j3 o2 "codex test box": j2 ended stopped: "{said}"; the watch added j3, a codex job on this Mac on its standing goal,'
        " and the work queue starts its codex-ask run"
    ]
    data = state.load(demo)
    pass_job = data["jobs"]["j3"]
    brief_text = Path(pass_job["brief"]).read_text()
    assert (detached, data["orders"]["o2"]["jobs"], pass_job["kind"], pass_job["goal"], pass_job["parts"]) == ([], ["j2", "j3"], "codex", BOX_GOAL, ["codex test box"])
    assert "Where: this Mac and one EC2 devbox\n" in brief_text
    assert f"Handoff: The watch started this pass of o2's standing part \"codex test box\" once its last pass, j2, ended stopped. Its return, verbatim:\n> {said}\n" in brief_text
    assert render.standings(data, "o2", data["orders"]["o2"]) == [
        f'  STANDING "codex test box": standing on "{render.cut(BOX_GOAL, 80)}", its live pass codex j3 waiting, its last pass codex j2 stopped: "{render.cut(said, 80)}"'
    ]
    assert (staged(watch.standing_lines, watching, T0 + timedelta(hours=2)), detached) == ([], [])


def test_a_standing_pass_added_with_no_brief_is_briefed_and_launched_on_the_next_pass_as_j7852_was_not(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    detached = launches(monkeypatch)
    said = "DONE j2 live: round 4 ran"
    with edit(demo) as data:
        standing_order(data, ["j2", "j3"])
        data["jobs"]["j2"] = job(order="o2", kind="codex", goal=BOX_GOAL, parts=["codex test box"], state="done", said=said, brief_flags={"where": "this Mac and one EC2 devbox"})
        data["jobs"]["j3"] = state.new_job("o2", "codex", BOX_GOAL, ["codex test box"], state.stamp(T0))
    lines = staged(watch.standing_lines, watcher(demo), T0 + timedelta(hours=1))
    assert lines == [f'STANDING j3 o2 "codex test box": j2 ended done: "{said}"; the watch added j3, a codex job on this Mac on its standing goal, and the work queue starts its codex-ask run']
    brief = Path(state.load(demo)["jobs"]["j3"]["brief"]).read_text()
    assert (detached, f"its last pass, j2, ended done. Its return, verbatim:\n> {said}\n" in brief) == ([], True)


def test_a_standing_pass_that_ended_at_once_starts_the_next_only_thirty_minutes_after_it_was_added_and_its_part_is_never_delivered_nor_an_orphan(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    detached = launches(monkeypatch)
    with edit(demo) as data:
        standing_order(data, ["j2"])
        data["jobs"]["j2"] = job(order="o2", kind="codex", goal=BOX_GOAL, parts=["codex test box"], state="done", said="DONE round 1 ran")
    watching = watcher(demo)
    early = T0 + state.STANDING_EVERY - timedelta(minutes=1)
    assert (staged(watch.standing_lines, watching, early), watch.orphan_lines(watching, early), state.delivered(state.load(demo), "o2", "codex test box")) == ([], [], False)
    assert watch.orphan_lines(watching, T0 + state.STANDING_EVERY) == []
    assert [line.split(":")[0] for line in staged(watch.standing_lines, watching, T0 + state.STANDING_EVERY)] == ['STANDING j3 o2 "codex test box"']
    assert detached == []


def test_the_first_pass_of_a_standing_part_no_job_ever_held_starts_at_once_and_says_no_pass_ran_before(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    detached = launches(monkeypatch)
    with edit(demo) as data:
        standing_order(data, [])
    assert staged(watch.standing_lines, watcher(demo), T0) == [
        'STANDING j2 o2 "codex test box": no pass of it ran before; the watch added j2, a codex job on this Mac on its standing goal,'
        " and the work queue starts its codex-ask run"
    ]
    brief_text = Path(state.load(demo)["jobs"]["j2"]["brief"]).read_text()
    assert (detached, f"Goal: {BOX_GOAL}\n" in brief_text) == ([], True)
    assert "Handoff: The watch started the first pass of o2's standing part \"codex test box\": no pass of it ran before.\n" in brief_text
    assert f"Done when: {watch.PASS_DONE}\n" in brief_text


def test_a_standing_part_kept_on_a_check_runs_it_detached_every_thirty_minutes_with_no_codex_pass_and_a_failed_run_is_its_orphan_line(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    detached = launches(monkeypatch)
    command = "python3 -I tools/port_gap.py --post"
    with edit(demo) as data:
        standing_order(data, [])
        data["orders"]["o2"]["parts"]["codex test box"]["check"] = state.new_check(command)
    watching = watcher(demo)
    checking = ["order", "check", "o2", "--part", "codex test box"]
    assert (staged(watch.standing_lines, watching, T0), detached, state.load(demo)["orders"]["o2"]["jobs"]) == ([], [checking], [])
    assert (staged(watch.standing_lines, watching, T0 + timedelta(minutes=40)), detached) == ([], [checking])
    with edit(demo) as data:
        data["orders"]["o2"]["parts"]["codex test box"]["check"].update(exit=0, said="port-gap at dev 6596e76b12fa: 0 gap")
    assert (staged(watch.standing_lines, watching, T0 + timedelta(minutes=10)), watch.orphan_lines(watching, T0 + timedelta(minutes=10))) == ([], [])
    data = state.load(demo)
    assert render.standings(data, "o2", data["orders"]["o2"]) == [
        f'  STANDING "codex test box": standing on "{render.cut(BOX_GOAL, 80)}", checked by `{command}`, last run {render.when(state.stamp(T0))} exited 0: "port-gap at dev 6596e76b12fa: 0 gap"'
    ]
    assert (staged(watch.standing_lines, watching, T0 + state.STANDING_EVERY), detached) == ([], [checking, checking])
    with edit(demo) as data:
        data["orders"]["o2"]["parts"]["codex test box"]["check"].update(exit=1, said="git fetch: could not read from remote")
    late = T0 + state.STANDING_EVERY + timedelta(minutes=5)
    assert watch.orphan_lines(watching, late) == [f'ORPHAN o2 "codex test box": its check `{command}` exited 1: git fetch: could not read from remote']


def test_a_standing_parts_next_pass_holds_the_texts_and_owes_the_answers_its_last_pass_held_so_its_launch_carries_them_and_none_waits_on_a_pass_that_ended(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    launches(monkeypatch)
    held = {"seq": None, "words": "run round 5 against staging-2", "sender": "coordinator", "at": state.stamp(T0), "said": None, "request": "r-1"}
    with edit(demo) as data:
        standing_order(data, ["j2"])
        data["jobs"]["j2"] = job(order="o2", kind="codex", goal=BOX_GOAL, parts=["codex test box"], state="stopped", said="HANDOFF round 4 ran", undelivered=[held])
        data["answers"]["a1"] = {"words": "keep box i-0ee4 for round 5", "link": f"{CARD}9", "at": state.stamp(T0), "said": state.stamp(T0), "cards": {}, "jobs": {"j1": None, "j2": None}}
    assert [line.split(":")[0] for line in staged(watch.standing_lines, watcher(demo), T0 + timedelta(hours=1))] == ['STANDING j3 o2 "codex test box"']
    data = state.load(demo)
    assert (data["jobs"]["j3"]["undelivered"], data["jobs"]["j2"]["undelivered"], data["answers"]["a1"]["jobs"]) == ([held], [], {"j1": None, "j3": None})
    carried = cli.answers_since(data, "j3", Path(data["jobs"]["j3"]["brief"]).read_text())
    assert (f"From the coordinator since:\n- {held['words']}" in carried, f'- "keep box i-0ee4 for round 5" ({CARD}9)' in carried) == (True, True)


def test_a_stranded_jobs_orphans_name_once_the_pr_its_line_names_moved_to_another_job_and_the_job_it_names_finished_as_686978_and_686979_missed(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    parts = ["auto partitioning on the same abstraction", "auto partitioning status"]
    moved = "Forge-AI/monorepo#55175"
    said = f"READY {state.pr_url(moved)} waits on j4's runbook"
    with edit(demo) as data:
        proven(data, updated=earlier)
        data["orders"]["o2"] = order(text=parts[0], jobs=["j2", "j3", "j4"], kind="build", parts={part: state.new_part() for part in parts}, updated=earlier)
        data["jobs"]["j2"] = job(order="o2", state="waiting", said=said, parts=parts, updated=earlier)
        data["jobs"]["j3"] = job(order="o2", parts=[], updated=earlier)
        data["jobs"]["j4"] = job(order="o2", state="done", said="DONE census reads 0 unlinked moved tables", parts=[], updated=earlier)
        data["prs"][moved] = state.new_pr(state.pr_url(moved), "j3", "o2")
    stranded = f'j2 waits on "{said}" and no open PR, card or due time can wake it; of what its line names, {moved} moved to j3, j4 done'
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == [f'ORPHAN o2 "{part}": {stranded}' for part in parts]
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == ["WORK j5 records a build worker on a Sprite, its first group claimed"]


def test_each_change_a_design_lists_with_no_job_is_an_orphan_whatever_earlier_change_it_waits_on_but_while_he_picks_an_option(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    before = state.stamp(T0 - timedelta(minutes=10))
    stack = (
        "DESIGN moved-table abstraction\nChange: the moved-table interface\nChange: the reads through it\nChange: the writes through it (waits on change 2)\n"
        "Change: the old tables dropped (waits on change 2 and change 3 live on every cluster)\n"
    )
    with edit(demo) as data:
        data["orders"]["o2"] = order(text="redesign the moved tables", jobs=["j2", "j3"], options_only=True)
        data["orders"]["o2"]["parts"] = {"moved-table abstraction": state.new_part()}
        data["jobs"]["j2"] = job(order="o2", kind="design", parts=["moved-table abstraction"], updated=before)
        data["jobs"]["j3"] = job(order="o2", parts=["moved-table abstraction", "the moved-table interface"], updated=before)
    with edit(demo) as data:
        assert state.add_changes(demo, data, "j2", stack) == []
        data["orders"]["o2"]["updated"] = before
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    with edit(demo) as data:
        data["orders"]["o2"]["options_only"] = False
    unbuilt = ("the reads through it", "the writes through it", "the old tables dropped")
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == [f'ORPHAN o2 "{change}": no job' for change in unbuilt]


def test_a_done_design_leaves_its_own_part_with_the_builder_carrying_one_of_its_changes(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    before = state.stamp(T0 - timedelta(minutes=10))
    with edit(demo) as data:
        data["orders"]["o2"] = order(text="redesign the moved tables", jobs=["j2", "j3"], updated=before)
        data["orders"]["o2"]["parts"] = {"redesign the moved tables": state.new_part(), "the moved-table interface": state.new_part() | {"design": "j2"}}
        data["jobs"]["j2"] = job(order="o2", kind="design", state="done", said="carried by builder j3", parts=["redesign the moved tables"], updated=before)
        data["jobs"]["j3"] = job(order="o2", parts=["the moved-table interface"], updated=before)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    with edit(demo) as data:
        data["jobs"]["j3"].update(state="stopped")
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == [
        'ORPHAN o2 "redesign the moved tables": no builder carries this work, only design j2 done: start one with drive job add builder --order o2'
        " --part 'redesign the moved tables' --goal '<what it builds>', or add it to a builder with drive job set <builder> --part 'redesign the moved tables', adding --carries o2 for another order's builder; when it asks only for an answer,"
        " drive order set o2 --kind answer --part 'redesign the moved tables'",
        'ORPHAN o2 "the moved-table interface": no builder carries this work, only builder j3 stopped: start one with drive job add builder --order o2'
        " --part 'the moved-table interface' --goal '<what it builds>', or add it to a builder with drive job set <builder> --part 'the moved-table interface', adding --carries o2 for another order's builder; when it asks only for an answer,"
        " drive order set o2 --kind answer --part 'the moved-table interface'",
    ]


def test_a_text_held_on_a_lane_is_said_undelivered_ten_minutes_on_and_every_ten_minutes_after(demo: str) -> None:
    with edit(demo) as data:
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


def test_a_text_held_on_a_lane_orca_cannot_reach_while_another_verb_holds_its_lock_names_the_local_launch_that_reaches_it_until_the_lock_frees(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with edit(demo) as data:
        data["jobs"]["j1"].update(where="remote", dispatch="ctx_99318cf15be2")
        data["jobs"]["j1"]["undelivered"] = [{"seq": "717002", "words": "ship the narrower fix", "at": state.stamp(T0), "said": None, "request": "req-held"}]
    down = "orca orchestration worker-show: remote_runtime_unavailable: Could not connect to the remote Orca runtime at ws://127.0.0.1:53245"

    def worker(dispatch: str) -> dict:
        raise orca.OrcaError(down)

    monkeypatch.setattr(orca, "worker", worker)
    monkeypatch.setattr(state, "holders", lambda path: f"pid 49041 drive job resume j1 on {path.name}")
    watching = watcher(demo)
    held = (
        f'ANSWER undelivered j1: "ship the narrower fix", held at {render.when(state.stamp(T0))}, has not reached j1; the watch sends it to a worker that'
        " takes messages, hands it to the next worker of one that returned and launches a builder never started with it"
    )
    with open(state.job_lock_path(demo, "j1"), "a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        assert watch.undelivered_lines(watching, T0 + timedelta(minutes=10)) == [
            f"{held}; drive job resume j1 is refused now: another drive verb holds its lock (pid 49041 drive job resume j1 on j1.lock) while Orca cannot reach its lane on its Sprite ({down}),"
            " so drive job launch j1 --local moves it to a fresh worktree on this Mac now, the verb holding its lock standing down at its next step, and its handoff carries every text the job holds [o1 j1]"
        ]
    assert watch.undelivered_lines(watching, T0 + timedelta(minutes=20)) == [f"{held}, and drive job resume j1 delivers it now [o1 j1]"]


def test_a_text_held_on_a_lane_whose_own_card_still_asks_him_is_never_said_undelivered_until_his_answer_ends_it(demo: str) -> None:
    with edit(demo) as data:
        data["jobs"]["j1"].update(where="remote", dispatch="ctx_lane1", state="waiting", waits=["demo:j1-7"])
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "When may it drift?", state.stamp(T0), card=CARD) | {"waits": "demo:j1-7"})
        data["jobs"]["j1"]["undelivered"] = [{"seq": "712192", "words": "Expand on 1 and 2", "at": state.stamp(T0), "said": None, "request": "req-held"}]
    watching = watcher(demo)
    assert watch.undelivered_lines(watching, T0 + timedelta(minutes=10)) == []
    with edit(demo) as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("answered", "drift only in a window you pick", CARD, at=state.stamp(T0))
    assert [line.split(":")[0] for line in watch.undelivered_lines(watching, T0 + timedelta(minutes=20))] == ["ANSWER undelivered j1"]


def test_a_text_held_on_an_agent_on_this_mac_names_its_resume_and_none_is_said_on_a_builder_its_launch_carries_it_to(demo: str) -> None:
    held = [{"seq": "699075", "words": "drift only in a window you pick", "at": state.stamp(T0), "said": None, "request": "req-held"}]
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"] += ["j2", "j3"]
        data["jobs"]["j1"].update(agent="agent-j1", where="local", session="session-under-test", undelivered=held)
        data["jobs"]["j2"] = job(state="waiting", waits=["demo:j1-7"], undelivered=held)
        data["jobs"]["j3"] = job(state="waiting", waits=["demo:j1-7", "j2"], undelivered=held)
    assert watch.undelivered_lines(watcher(demo), T0 + timedelta(minutes=10)) == [
        f'ANSWER undelivered j1: "drift only in a window you pick", held at {render.when(state.stamp(T0))}, has not reached j1; the watch sends it to a worker that'
        " takes messages, hands it to the next worker of one that returned and launches a builder never started with it, and drive job resume j1 delivers it now [o1 j1]"
    ]


def test_a_text_held_on_a_builder_that_returned_done_drops_but_his_words_and_one_held_while_its_done_waits_on_its_prs_names_the_orders_holder_never_resume(demo: str) -> None:
    meta = {"seq": "735797", "words": "release your claim on it", "at": state.stamp(T0), "said": None, "request": "req-meta", "sender": "meta"}
    his = {"seq": "735800", "words": "ship the narrower fix", "at": state.stamp(T0), "said": None, "request": "req-his", "sender": state.HIM}
    with edit(demo) as data:
        data["jobs"]["j1"].update(agent="agent-j1", where="local", session="session-under-test", undelivered=[meta, his])
        state.take_result(data, "j1", "DONE shipped the narrower fix")
        assert [kept["seq"] for kept in data["jobs"]["j1"]["undelivered"]] == ["735800"]
        data["jobs"]["j1"].update(state="waiting", said="WAITING https://github.com/acme/widgets/pull/1 to merge: DONE shipped the narrower fix", waits=[], undelivered=[meta])
        lane = state.holding_lane(data, data["orders"]["o1"])
    assert watch.undelivered_lines(watcher(demo), T0 + timedelta(minutes=10)) == [
        f'ANSWER undelivered j1: "release your claim on it", held at {render.when(state.stamp(T0))}, has not reached j1; the watch sends it to a worker that'
        " takes messages, hands it to the next worker of one that returned and launches a builder never started with it; j1 returned DONE, so drive job resume refuses it:"
        f" it goes to {lane}, the session holding order o1, which acts on any work it asks, and `drive job set j1 --heard` drops it [o1 j1]"
    ]


def held_tap(demo: str, tapped: datetime) -> None:
    words = "drift only in a window you pick"
    closed = state.new_close("answered", words, CARD, seq="699075", at=state.stamp(tapped))
    held = {"seq": "699075", "words": f'His answer on card demo:j1-7 ({CARD}), verbatim: "{words}"', "at": state.stamp(tapped), "said": None, "request": "req-held"}
    with edit(demo) as data:
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


def test_the_watch_carries_held_texts_to_a_lane_that_returned_and_leaves_a_builder_never_started_to_the_work_queue_and_an_agent_its_starter_reaches_to_its_sendmessage(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    detached = []

    def detach(name: str, args: list[str], log: str) -> int:
        detached.append(args)
        done = subprocess.Popen(["true"])
        done.wait()
        return done.pid

    monkeypatch.setattr(state, "detach", detach)
    held = [{"seq": "9", "words": 'His answer on card demo:j1-7, verbatim: "Ring"', "at": state.stamp(T0), "said": None, "request": "req-ring"}]
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["jobs"]["j1"]["state"] = "done"
    watch.carry_held(watching, T0 + timedelta(minutes=11))
    assert detached == [["job", "resume", "j2"], ["job", "launch", "j5"], ["job", "resume", "j2"], ["job", "launch", "j5"]]


def test_the_watch_starts_no_launch_of_a_codex_job_while_its_codex_ask_run_goes_and_launches_it_with_its_held_text_once_it_returned(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    detached = []

    def detach(name: str, args: list[str], log: str) -> int:
        detached.append(args)
        done = subprocess.Popen(["true"])
        done.wait()
        return done.pid

    monkeypatch.setattr(state, "detach", detach)
    held = [{"seq": "9", "words": 'His answer on card demo:j2-7, verbatim: "Ring"', "at": state.stamp(T0), "said": None, "request": "req-ring"}]
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(kind="codex", codex_run="/drive/scratch/j2/codex", undelivered=held)
    watching = watcher(demo)
    watch.carry_held(watching, T0)
    watch.carry_held(watching, T0 + timedelta(minutes=11))
    assert detached == []
    with edit(demo) as data:
        data["jobs"]["j2"]["state"] = "waiting"
    watch.carry_held(watching, T0 + timedelta(minutes=12))
    assert detached == [["job", "launch", "j2"]]


def test_a_codex_job_waiting_on_a_process_it_started_on_this_mac_holds_while_it_runs_and_its_end_starts_its_next_run_with_its_last_report(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    detached = []

    def detach(name: str, args: list[str], log: str) -> int:
        detached.append(args)
        done = subprocess.Popen(["true"])
        done.wait()
        return done.pid

    monkeypatch.setattr(state, "detach", detach)
    observer = subprocess.Popen(["sleep", "600"])
    report = f"WAITING the checkpoint pass runs, waits on pid:{observer.pid}\nCHECK: /drive/scratch/j2/checkpoint.log\n"
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(kind="codex", codex_run="/drive/scratch/j2/codex", state="waiting", said=state.opening(report), waits=state.pid_waits(report))
    state.keep_report(demo, "j2", state.opening(report), report)
    watching = watcher(demo)
    try:
        assert watch.waits_lines(watching, T0) == []
        watch.carry_held(watching, T0)
        assert detached == []
    finally:
        observer.kill()
        observer.wait()
    assert watch.waits_lines(watching, T0 + timedelta(minutes=1)) == [
        f"FREE j2 o1: process {observer.pid} on this Mac ended, nothing else holds it, and the watch now starts its next codex-ask run carrying its last report"
    ]
    [held] = state.load(demo)["jobs"]["j2"]["undelivered"]
    assert held["words"] == f"The process on this Mac j2 waited on ended, crashed or finished: pid:{observer.pid}. Your last report:\n{report.strip()}"
    watch.carry_held(watching, T0 + timedelta(minutes=1))
    assert detached == [["job", "launch", "j2"]]


def test_a_briefed_never_started_builder_whose_waits_end_holding_his_answers_is_a_ready_build_item_with_no_free_line_or_launch_so_only_the_queue_starts_it(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    detached = []

    def detach(name: str, args: list[str], log: str) -> int:
        detached.append(args)
        return 1

    monkeypatch.setattr(state, "detach", detach)
    written = tmp_path / "j2.brief.md"
    written.write_text("Goal: build the copy\nDrive: demo, order o1, job j2\n")
    observer = subprocess.Popen(["sleep", "600"])
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = state.new_job("o1", "builder", "build the copy", [], state.stamp(T0)) | {"brief": str(written), "waits": state.pid_waits(f"pid:{observer.pid}")}
        state.hold_text(data["jobs"]["j2"], "ship the narrower fix", state.HIM)
    watching = watcher(demo)
    observer.kill()
    observer.wait()
    assert watch.waits_lines(watching, T0 + timedelta(minutes=1)) == []
    watch.carry_held(watching, T0 + timedelta(minutes=1))
    data = state.load(demo)
    assert (detached, data["jobs"]["j2"]["waits"], work.KIND[work.BUILD].ready(data, "j2")) == ([], [], True)


def test_a_job_waiting_on_a_job_that_returned_built_is_freed_on_its_branch_and_head_and_told_to_cut_from_it_while_a_pr_waiting_on_it_holds_as_j5696_did_not(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    detached, asked = [], []

    def detach(name: str, args: list[str], log: str) -> int:
        detached.append(args)
        done = subprocess.Popen(["true"])
        done.wait()
        return done.pid

    def graphql(text: str, what: str, max_age: str = "0s") -> dict:
        asked.append(text)
        return {"data": {"r0": {"h0": {"target": {"oid": "299fb6f36add5e1b0c4d"}}, "h1": None}}}

    monkeypatch.setattr(state, "detach", detach)
    monkeypatch.setattr(github, "graphql", graphql)
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"] += ["j2", "j3", "j4"]
        data["jobs"]["j2"] = job(state="built", repo="Forge-AI/monorepo", branch="durable-agents-j2, durable-agents-j2-docs", said="BUILT durable-agents-j2, durable-agents-j2-docs, waits on demo:j2-1")
        data["jobs"]["j3"] = job(state="waiting", said="waits on j2", waits=["j2"])
        data["jobs"]["j4"] = job(state="waiting", said="waits on j2", waits=["j2"])
        data["prs"][KEY]["waits"] = ["j2"]
    watching = watcher(demo)
    built = "j2 built on durable-agents-j2 at 299fb6f36add, durable-agents-j2-docs, which Forge-AI/monorepo does not hold"
    assert watch.waits_lines(watching, T0) == []
    data = state.load(demo)
    held = [[kept["words"] for kept in data["jobs"][job_id]["undelivered"]] for job_id in ("j3", "j4")]
    based = f"{built}, the change yours builds on, its PR held on his answer: cut your branch from that head, so your PR stacks on it"
    assert (data["jobs"]["j3"]["waits"], data["jobs"]["j4"]["waits"], held, data["prs"][KEY]["waits"], len(asked)) == ([], [], [[based], [based]], ["j2"], 1)
    assert watch.waits_lines(watching, T0 + timedelta(minutes=1)) == []
    watch.carry_held(watching, T0 + timedelta(minutes=1))
    assert (detached, len(asked)) == ([], 1)


def test_a_tester_whose_return_names_a_built_job_gets_no_stack_on_line_and_a_builder_named_it_again_hears_it_once_as_j7599_did_every_20_minutes(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(state, "detach", lambda name, args, log: 1)
    monkeypatch.setattr(github, "graphql", lambda text, what, max_age="0s": {"data": {"r0": {"h0": {"target": {"oid": "eb6227aa4f3c55aa"}}}}})
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"] += ["j2", "j3", "j4"]
        data["jobs"]["j2"] = job(state="built", repo="Forge-AI/monorepo", branch="durable-agents-j2-no-old-flip", said="BUILT durable-agents-j2-no-old-flip")
        data["jobs"]["j3"] = job(state="waiting", said="WAITING j8: a tip carrying j2's eb6227aa4f3c", waits=["j2"], builds_nothing=True)
        data["jobs"]["j4"] = job(state="waiting", said="waits on j2", waits=["j2"])
    watching = watcher(demo)
    based = "j2 built on durable-agents-j2-no-old-flip at eb6227aa4f3c, the change yours builds on, its PR held on his answer: cut your branch from that head, so your PR stacks on it"
    watch.waits_lines(watching, T0)
    data = state.load(demo)
    assert ([kept["words"] for kept in data["jobs"]["j3"]["undelivered"]], [kept["words"] for kept in data["jobs"]["j4"]["undelivered"]]) == ([], [based])
    with edit(demo) as data:
        for job_id in ("j3", "j4"):
            data["jobs"][job_id].update(state="waiting", waits=["j2"], undelivered=[])
    watch.waits_lines(watching, T0 + timedelta(minutes=20))
    data = state.load(demo)
    assert [data["jobs"][job_id]["undelivered"] for job_id in ("j3", "j4")] == [[], []]


def test_his_tap_on_a_lane_questions_card_goes_into_its_build_items_brief_for_the_queues_worker_and_his_undo_reopens_the_question_his_card_asks_again(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    asked = "Which fixture keeps the hold test deterministic?"
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["orders"]["o1"]["updated"] = state.stamp(T0 + timedelta(minutes=1))
        data["jobs"]["j1"].update(state="waiting", ask="msg_q1", ask_at=state.stamp(T0), said=asked, report=asked, questions=[state.new_question(None, asked, state.stamp(T0), card=CARD)])
    tapped = T0 + timedelta(minutes=5)
    with monkeypatch.context() as clock:
        clock.setattr(state, "now", lambda: tapped)
        card.question_answered({"waits": "demo:j1-7", "label": "Ring", "url": CARD})
    watching = watcher(demo)
    assert watch.run_pass(watching, T0 + timedelta(minutes=15)) == ["WORK j2 records a build worker on a Sprite, its first group claimed"]
    item = state.load(demo)["jobs"]["j1"]
    assert (item["claim"]["worker"], item["undelivered"], "Ring" in Path(item["brief"]).read_text()) == ("j2", [], True)
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=30)) if line.startswith("ORPHAN ")]
    with monkeypatch.context() as clock:
        clock.setattr(state, "now", lambda: T0 + timedelta(minutes=31))
        card.question_answered({"waits": "demo:j1-7", "label": "Ring", "url": CARD, "undone": True})
    assert view.open_cards(state.load(demo)["jobs"]["j1"]) == [CARD]
    assert [line.split(":")[0] for line in watch.run_pass(watching, T0 + timedelta(minutes=60)) if line.startswith("DUE ")] == ["DUE o1 owed w1"]


def test_a_part_staffed_within_five_minutes_of_its_order_moving_is_never_an_orphan(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with edit(demo) as data:
        files_order(data)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    with edit(demo) as data:
        data["jobs"]["j2"]["parts"].append("stack 2")
    assert watch.run_pass(watching, T0 + timedelta(seconds=30)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == []
    assert state.load(demo)["orders"]["o2"]["parts"]["stack 2"]["orphan_said"] is None


def merged_other(job_id: str, order_id: str) -> dict:
    return state.new_pr(state.pr_url(OTHER), job_id, order_id) | {"state": "merged", "merged_sha": "c0ffee1234", "head": "bbbbbbb222"}


def test_a_part_another_orders_builder_carries_is_delivered_by_the_passing_proof_of_that_builders_done_as_its_own_part_is(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    with edit(demo) as data:
        files_order(data, updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == ['ORPHAN o2 "stack 2": no job']
    assert run("job", "set", "j1", "--carries", "o2", "--part", "stack 2")[0] == 0
    assert state.load(demo)["orders"]["o2"]["jobs"] == ["j2", "j1"]
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"]["repro"] = proof_file(tmp_path)
        state.take_result(data, "j1", "DONE carried it")
    assert not state.delivered(state.load(demo), "o2", "stack 2")
    with edit(demo) as data:
        data["prs"][OTHER] = merged_other("j1", "o1")
        data["jobs"]["j1"]["prs"].append(state.pr_url(OTHER))
        state.take_result(data, "j1", f"DONE {state.pr_url(OTHER)} merged")
        data["orders"]["o2"]["updated"] = state.stamp(T0)
    data = state.load(demo)
    assert (data["jobs"]["j1"]["carries"], data["orders"]["o2"]["jobs"], state.delivered(data, "o2", "stack 2")) == ({"o2": ["stack 2"]}, ["j2", "j1"], False)
    assert watch.run_pass(watching, T0 + watch.HANDOFF) == []
    assert provers == ["j1"]
    with edit(demo) as data:
        install.finish(demo, data, "j1", f"DONE j1 live: {proof_file(tmp_path)} exits 0 on no plugin of this Mac", [], "", "")
    data = state.load(demo)
    assert [state.delivered(data, order_id, part) for order_id, part in (("o2", "stack 2"), ("o2", "stack 1"), ("o1", "ship the watch"))] == [True, False, True]


def test_a_passing_proof_naming_no_pr_delivers_its_own_orders_part_and_the_one_it_carries_for_another_order(demo: str) -> None:
    with edit(demo) as data:
        files_order(data)
        data["orders"]["o2"]["jobs"].append("j1")
        data["jobs"]["j1"]["carries"] = {"o2": ["stack 2"]}
    with edit(demo) as data:
        proven(data)
    data = state.load(demo)
    assert [state.delivered(data, order_id, part) for order_id, part in (("o1", "ship the watch"), ("o2", "stack 2"))] == [True, True]


def test_a_job_on_the_part_heals_the_orphan_once_its_handover_is_done(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with edit(demo) as data:
        files_order(data, updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == ['ORPHAN o2 "stack 2": no job']
    with edit(demo) as data:
        data["jobs"]["j2"]["parts"].append("stack 2")
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    assert state.load(demo)["orders"]["o2"]["parts"]["stack 2"]["orphan_said"] == state.stamp(T0)
    assert watch.run_pass(watching, T0 + watch.HANDOFF) == []
    assert state.load(demo)["orders"]["o2"]["parts"]["stack 2"]["orphan_said"] is None


def test_a_job_drive_job_add_recorded_is_not_started_until_its_worker_records_itself_and_an_orphan_a_minute_on_by_the_clock_however_long_the_pass_though_its_part_was_said_since_its_last_tick(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    clock = [T0]
    monkeypatch.setattr(state, "now", lambda: clock[0])
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="failed", updated=state.stamp(T0 - timedelta(minutes=20)))
        data["orders"]["o1"]["parts"]["ship the watch"]["orphan_said"] = state.stamp(T0 - timedelta(minutes=10))
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = state.new_job("o1", "builder", "watch", ["ship the watch"], state.stamp(T0)) | {"brief": "/briefs/j2.md"}
    added = state.load(demo)
    assert (added["jobs"]["j2"]["state"], render.job_line(added, "j2", added["jobs"]["j2"]), view.feature_state(added["orders"]["o1"], [added["jobs"]["j2"]], [])) == (
        "waiting",
        "j2 builder not started: watch",
        "not started",
    )
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    clock[0] = T0 + timedelta(minutes=1)
    assert watch.run_pass(watching, T0) == ['ORPHAN o1 "ship the watch": j2 not started 1 minutes after drive job add; the watch starts its agent on its brief /briefs/j2.md, so start no agent for it; drive job resume reaches it']
    clock[0] = T0 + timedelta(minutes=2)
    assert watch.run_pass(watching, T0 + timedelta(minutes=2)) == []
    with edit(demo) as data:
        state.start(data["jobs"]["j2"])
    started = state.load(demo)["jobs"]["j2"]
    assert (started["state"], started["said"]) == ("running", None)


def test_a_watch_process_a_newer_install_ends_at_its_first_read_still_says_a_never_started_jobs_orphan_and_heals_no_gap_its_lanes_were_not_read_for(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(state, "now", lambda: T0 + timedelta(minutes=2))
    read: list[str] = []
    monkeypatch.setattr(orca, "speak_as_drive", read.append)
    with edit(demo) as data:
        data["jobs"]["j1"]["updated"] = state.stamp(T0 - timedelta(minutes=10))
        data["orders"]["o1"]["parts"]["ship the watch"].update(orphan_said=state.stamp(T0 - timedelta(minutes=5)), orphan_gap="j1 has no agent recorded")
        files_order(data)
        data["jobs"]["j2"] = state.new_job("o2", "builder", "files", ["stack 1"], state.stamp(T0)) | {"brief": "/briefs/j2.md"}
    said = [line for lines in watch.passed(watcher(demo), T0 + timedelta(minutes=2), until=lambda: True) for line in lines]
    assert (said, read) == (['ORPHAN o2 "stack 1": j2 not started 2 minutes after drive job add; the watch starts its agent on its brief /briefs/j2.md, so start no agent for it; drive job resume reaches it'], [demo])
    assert state.load(demo)["orders"]["o1"]["parts"]["ship the watch"]["orphan_said"] == state.stamp(T0 - timedelta(minutes=5))


def test_a_reader_an_older_install_started_on_a_prs_head_holds_no_part_and_is_never_an_answer_owed_him(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="failed", updated=earlier)
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(kind="reader", state="done", parts=["ship the watch"], checks={KEY: "aaaaaaa111"}, updated=earlier)
    assert watch.run_pass(watcher(demo), T0) == ['ORPHAN o1 "ship the watch": j1 failed']


def test_j5802_a_newer_builders_failed_proof_takes_back_the_part_an_older_proof_delivered_which_reads_failed_until_a_newer_job_holds_it(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    with edit(demo) as data:
        proven(data, updated=earlier)
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(state="failed", said="BLOCKED repro/j2.sh exits 1", parts=["ship the watch"], updated=earlier)
    watching = watcher(demo)
    assert (state.delivered(state.load(demo), "o1", "ship the watch"), watch.run_pass(watching, T0)) == (False, ['ORPHAN o1 "ship the watch": j2 failed'])
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j3")
        data["jobs"]["j3"] = job(parts=["ship the watch"])
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    assert watch.run_pass(watching, T0 + watch.HANDOFF) == []


def test_a_job_on_the_immediate_fix_carries_the_orders_other_parts_until_it_is_done(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    with edit(demo) as data:
        data["orders"]["o1"]["parts"][state.IMMEDIATE_FIX] = state.new_part()
        data["orders"]["o1"]["updated"] = earlier
        data["jobs"]["j1"].update(parts=[state.IMMEDIATE_FIX], state="waiting", updated=earlier)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    with edit(demo) as data:
        proven(data)
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == ['ORPHAN o1 "ship the watch": no job']


@pytest.mark.parametrize("kind", ["reader", "codex", "design"])
def test_a_proven_builder_delivers_its_part_and_a_done_reader_codex_or_design_job_waits_for_the_posted_answer(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], slack_thread: "Thread", kind: str
) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    with edit(demo) as data:
        proven(data, updated=earlier)
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


def test_work_he_ordered_is_carried_only_by_a_builder_never_by_research_his_card_or_a_post_and_a_done_naming_no_merged_pr_delivers_none_of_it(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    merge = "get the inbox work merged"
    with edit(demo) as data:
        proven(data, updated=earlier)
        data["orders"]["o2"] = order(text=merge, jobs=["j2"], kind="build", parts={merge: state.new_part()}, updated=earlier)
        data["jobs"]["j2"] = job(order="o2", kind="reader", parts=[merge], updated=earlier)
    unbuilt = (
        f"start one with drive job add builder --order o2 --part '{merge}' --goal '<what it builds>'{{waits}}, or add it to a builder with drive job set <builder> --part '{merge}',"
        f" adding --carries o2 for another order's builder; when it asks only for an answer, drive order set o2 --kind answer --part '{merge}'"
    )
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == [f'ORPHAN o2 "{merge}": {NO_BUILDER}reader j2 running: {unbuilt.format(waits=" --waits j2")}']
    card = {"question": "Which inbox lands first?", "options": ["First", "Last"], "recommended": "First", "asked": state.stamp(T0), "url": f"{CARD}?thread_ts=1.2", "card": {"title": "Which inbox lands first?", "waits": "demo:j2-7-a"}}
    monkeypatch.setattr(watch.view, "cards", lambda name: {"team_url": "", "questions": [card], "closed": [], "pr_cards": []})
    with edit(demo) as data:
        data["jobs"]["j2"].update(kind="design", state="done", questions=[state.new_question(None, None, state.stamp(T0), card=CARD, holds=None)])
        data["orders"]["o2"]["parts"][merge]["orphan_said"] = None
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == [f'ORPHAN o2 "{merge}": {NO_BUILDER}design j2 done, his card {CARD} open: {unbuilt.format(waits="")}']
    with edit(demo) as data:
        data["orders"]["o2"]["jobs"].append("j3")
        data["jobs"]["j3"] = job(order="o2", kind="codex", parts=[merge], updated=earlier)
    assert watch.run_pass(watching, T0 + timedelta(seconds=10)) == []
    with edit(demo) as data:
        data["jobs"]["j3"].update(state="done", said="DONE merged it")
    assert not state.delivered(state.load(demo), "o2", merge)


@pytest.mark.parametrize(("kind", "part", "posted", "held", "built", "said"), [case[1:] for case in REPLAYED_685794], ids=[str(case[0]) for case in REPLAYED_685794])
def test_a_part_of_an_order_of_no_kind_only_research_ever_held_is_his_question_its_post_answers_while_a_build_orders_part_stays_work(
    demo: str, monkeypatch: pytest.MonkeyPatch, kind: str | None, part: str, posted: bool, held: list[tuple[str, str]], built: bool, said: str | None
) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    other = "ship the other fix"
    jobs = [f"j{number}" for number in range(2, len(held) + 2 + built)]
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="done", updated=earlier)
        parts = {part: state.new_part() | {"posted": REPORTED if posted else None}} | ({other: state.new_part() | {"posted": state.pr_url(KEY)}} if built else {})
        data["orders"]["o2"] = order(text=part, jobs=jobs, kind=kind, parts=parts, updated=earlier)
        for job_id, (job_kind, job_state) in zip(jobs, held, strict=False):
            due = state.stamp(T0 + timedelta(hours=1)) if (job_kind, job_state) == ("reader", "waiting") else None
            data["jobs"][job_id] = job(order="o2", kind=job_kind, state=job_state, parts=[part], due=due, updated=earlier)
        if built:
            data["jobs"][jobs[-1]] = job(order="o2", state="done", said=PROVEN, parts=[other], updated=earlier)
    with edit(demo) as data:
        state.post_proofs(data)
    drawn = [line for line in watch.run_pass(watcher(demo), T0) if line.startswith("ORPHAN o2 ")]
    assert [line.startswith(f'ORPHAN o2 "{render.cut(part, 80)}": {said}') for line in drawn] == ([True] if said else [])


O1_SPIKES = {
    "Spike base: DuckDB link test and dataset seeder": [("j2", "builder", "stopped"), ("j7", "builder", "stopped"), ("j35", "builder", "done")],
    "Snapshot cost spike": [("j2", "builder", "stopped"), ("j8", "builder", "done")],
    "Worker startup, sandbox and typing spikes": [("j2", "builder", "stopped"), ("j11", "builder", "done"), ("j62", "builder", "done")],
    "Scan path spikes and page-reader correctness": [
        ("j2", "builder", "stopped"),
        ("j12", "builder", "done"),
        ("j13", "builder", "done"),
        ("j57", "builder", "stopped"),
        ("j59", "builder", "done"),
        ("j61", "builder", "done"),
        ("j78", "builder", "done"),
    ],
    "Wire format spike": [("j2", "builder", "stopped"), ("j14", "builder", "done")],
    "Columnar shadow spike": [("j2", "builder", "stopped"), ("j15", "builder", "done"), ("j59", "builder", "done")],
    "Pass-box bench runs": [("j2", "builder", "stopped"), ("j16", "codex", "done"), ("j70", "codex", "done")],
}


def test_each_o1_spike_part_its_done_builders_delivered_names_their_undelivered_work_never_the_stopped_builder_j2_j7_or_j57_beside_them_as_715907_to_715913_did(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    held = {part: [("j1", "reader", "done"), *jobs] for part, jobs in O1_SPIKES.items()}
    with edit(demo) as data:
        data["prs"].clear()
        data["jobs"].clear()
        for part, jobs in held.items():
            for job_id, kind, job_state in jobs:
                data["jobs"].setdefault(job_id, job(kind=kind, state=job_state, parts=[], undelivered=[], updated=earlier))["parts"].append(part)
        ids = sorted(data["jobs"], key=lambda job_id: int(job_id[1:]))
        data["orders"]["o1"] = order(kind="build", jobs=ids, parts={part: state.new_part() for part in O1_SPIKES}, updated=earlier)
    watching, data = watcher(demo), state.load(demo)
    gaps = {part: watch.part_gap(watching, data, "o1", part, T0) for part in O1_SPIKES}
    for part, jobs in held.items():
        listed = ", ".join(f"{kind} {job_id} {job_state}" for job_id, kind, job_state in jobs)
        finished = " and ".join(job_id for job_id, kind, job_state in jobs if kind != "reader" and job_state == "done")
        assert gaps[part].startswith(f"{NO_BUILDER}{listed}; {finished} done with no passing proof delivering it: start one with drive job add builder --order o1 --part {shlex.quote(part)} "), part


def test_a_part_sorted_as_an_answer_is_delivered_by_its_post_and_one_a_builder_works_on_is_never_sorted(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(state, "now", lambda: T0)
    earlier = state.stamp(T0 - timedelta(minutes=10))
    asked, fix = "why do old runs fail?", "fix them"
    with edit(demo) as data:
        proven(data, updated=earlier)
        data["orders"]["o2"] = order(text=asked, jobs=["j2", "j3"], kind="build", parts={asked: state.new_part(), fix: state.new_part()}, updated=earlier)
        data["jobs"]["j2"] = job(order="o2", kind="reader", state="done", parts=[asked], updated=earlier)
        data["jobs"]["j3"] = job(order="o2", parts=[fix], updated=earlier)
    watching = watcher(demo)
    [line] = watch.run_pass(watching, T0)
    assert line.startswith(f'ORPHAN o2 "{asked}": {NO_BUILDER}reader j2 done: ')
    assert line.endswith(f"when it asks only for an answer, drive order set o2 --kind answer --part '{asked}'")
    assert cli.main(["order", "set", "o2", "--part", asked, "--drive", demo]) == 2
    assert "--part: refused, it sorts a part of o2 with --kind answer" in capsys.readouterr().err
    assert cli.main(["order", "set", "o2", "--kind", "answer", "--part", fix, "--drive", demo]) == 2
    assert f'--part "{fix}": refused, builder j3 works on it, so it is work under way, never an answer' in capsys.readouterr().err
    assert cli.main(["order", "set", "o2", "--kind", "answer", "--part", asked, "--drive", demo]) == 0
    sorted_order = state.load(demo)["orders"]["o2"]
    assert (sorted_order["kind"], sorted_order["parts"][asked]["answer"], sorted_order["parts"][fix]["answer"]) == ("build", True, False)
    with edit(demo) as data:
        data["orders"]["o2"]["parts"][asked].update(orphan_said=None, posted=REPORTED)
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    assert state.delivered(state.load(demo), "o2", asked)


def test_a_merged_pr_delivers_no_work_part_and_its_orphan_names_the_prover_while_drive_order_posted_refuses_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    lands, landed = "#43 lands", state.pr_url(OTHER)
    with edit(demo) as data:
        proven(data, updated=earlier)
        data["orders"]["o2"] = order(text=lands, jobs=["j2"], kind="build", parts={lands: state.new_part()}, updated=earlier)
        data["jobs"]["j2"] = job(order="o2", kind="reader", state="done", parts=[lands], updated=earlier)
        data["prs"][OTHER] = state.new_pr(landed, "j1", "o1") | {"state": "merged"}
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == [
        f'ORPHAN o2 "{lands}": no builder carries this work, only reader j2 done: start one with drive job add builder --order o2 --part \'{lands}\''
        f" --goal '<what it builds>', or add it to a builder with drive job set <builder> --part '{lands}', adding --carries o2 for another order's builder; {watch.unproven_merge('o2', lands, OTHER)}"
    ]
    for link in (REPORTED, landed):
        assert cli.main(["order", "posted", "o2", link, "--part", lands, "--drive", demo]) == 2
        assert f'--part "{lands}": refused, ' + state.UNPOSTED_WORK.format(order="o2", part=lands) in capsys.readouterr().err
    assert not state.delivered(state.load(demo), "o2", lands)


@pytest.mark.parametrize(
    ("now", "said"),
    [
        ("MERGED", watch.unproven_merge("o2", "port the fixes", OTHER)),
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
    with edit(demo) as data:
        proven(data, updated=earlier)
        data["orders"]["o2"] = order(text=port, jobs=["j2"], kind="build", parts={port: state.new_part()}, updated=earlier)
        data["jobs"]["j2"] = job(order="o2", goal=port, state="stopped", parts=[port], updated=earlier)
    assert cli.main(["job", "set", "j2", "--pr", state.pr_url(OTHER), "--drive", demo]) == 0
    watching = watcher(demo)
    assert (watch.run_pass(watching, T0), watch.run_pass(watching, T0 + timedelta(seconds=5)), calls) == ([], [], [[KEY, OTHER]])
    assert render.part_line(state.load(demo), "o2", port) == f'"{port}": builder j2 stopped with {state.pr_url(OTHER)} carrying it'
    calls = serve(monkeypatch, {OTHER: read(state=now)})
    deliver("pull_request", {"pull_request": {"number": 43}})
    assert watch.run_pass(watching, T0 + timedelta(seconds=10)) == [f'ORPHAN o2 "{port}": {said}']
    assert (watch.run_pass(watching, T0 + timedelta(seconds=15)), calls) == ([], [[OTHER]])


def test_a_waiting_job_keeping_a_pr_the_drive_does_not_track_waits_on_it_while_github_reads_it_open_and_is_freed_once_it_merges(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    serve(monkeypatch, {KEY: read(), OTHER: read()})
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(prs=[state.pr_url(OTHER)])
    with edit(demo) as data:
        state.take_result(data, "j2", f"WAITING {state.pr_url(OTHER)}, a teammate's open PR that already does this work")
    assert state.load(demo)["jobs"]["j2"]["waits"] == [OTHER]
    watching = watcher(demo)
    assert [line for line in watch.run_pass(watching, T0) if line.startswith(("FREE ", "ORPHAN "))] == []
    calls = serve(monkeypatch, {OTHER: read(state="MERGED")})
    deliver("pull_request", {"pull_request": {"number": 43}})
    freed = [line for line in watch.run_pass(watching, T0 + watch.READ_EVERY) if line.startswith("FREE ")]
    assert (freed, state.load(demo)["jobs"]["j2"]["waits"], calls) == ([f"FREE j2 o1: {OTHER} merged, nothing else holds it"], [], [[OTHER]])


def test_a_stopped_builder_no_agent_took_holds_its_part_as_work_under_way_only_while_it_keeps_a_pr(demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(state, "now", lambda: T0)
    asked = "is the port already written?"
    with edit(demo) as data:
        data["orders"]["o2"] = order(text=asked, jobs=["j2"], kind="build", parts={asked: state.new_part()})
        data["jobs"]["j2"] = job(order="o2", state="stopped", prs=[state.pr_url(OTHER)], parts=[asked])
    assert cli.main(["order", "set", "o2", "--kind", "answer", "--part", asked, "--drive", demo]) == 2
    assert f'--part "{asked}": refused, builder j2 works on it, so it is work under way, never an answer' in capsys.readouterr().err
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["orders"]["o2"] = order(text=asked, jobs=["j2"], kind="build", parts={asked: state.new_part()})
        data["jobs"]["j2"] = job(order="o2", kind="codex", parts=[asked])
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o2")
    assert cli.main(["order", "set", "o2", "--kind", "answer", "--part", asked, "--drive", demo]) == 2
    assert f'--part "{asked}": refused, codex j2, {OTHER} works on it, so it is work under way, never an answer' in capsys.readouterr().err
    with edit(demo) as data:
        data["jobs"]["j2"]["state"] = finished
    assert cli.main(["order", "set", "o2", "--kind", "answer", "--part", asked, "--drive", demo]) == 2
    assert f'--part "{asked}": refused, {OTHER} works on it, so it is work under way, never an answer' in capsys.readouterr().err
    with edit(demo) as data:
        if pr:
            data["prs"][OTHER]["state"] = pr
        else:
            del data["prs"][OTHER]
    assert cli.main(["order", "set", "o2", "--kind", "answer", "--part", asked, "--drive", demo]) == 0
    assert state.load(demo)["orders"]["o2"]["parts"][asked]["answer"]


def test_sorting_as_an_answer_a_part_the_done_codex_job_that_posted_its_answer_holds_takes_it_off_that_job_and_delivers_it_so_its_orphan_heals(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(state, "now", lambda: T0)
    earlier = state.stamp(T0 - timedelta(minutes=10))
    asked = "why do old runs fail?"
    with edit(demo) as data:
        proven(data, updated=earlier)
        data["orders"]["o2"] = order(text=asked, jobs=["j2"], kind="build", parts={asked: state.new_part()}, updated=earlier)
        data["jobs"]["j2"] = job(order="o2", kind="codex", state="done", said=f"POSTED {REPORTED}", parts=[asked], updated=earlier)
    watching = watcher(demo)
    [line] = [line for line in watch.run_pass(watching, T0) if line.startswith("ORPHAN ")]
    assert line.endswith(f"when it asks only for an answer, drive order set o2 --kind answer --part '{asked}'")
    assert cli.main(["order", "set", "o2", "--kind", "answer", "--part", asked, "--drive", demo]) == 0
    assert f'o2 "{asked}": sorted as an answer, taken off codex j2, done, whose hold kept it work he ordered; delivered' in capsys.readouterr().out.splitlines()
    data = state.load(demo)
    assert (data["jobs"]["j2"]["parts"], data["orders"]["o2"]["parts"][asked]["posted"], state.work(data, "o2", asked)) == ([], REPORTED, False)
    assert [line for line in watch.run_pass(watching, T0 + timedelta(seconds=5)) if line.startswith("ORPHAN ")] == []


def test_sorting_as_an_answer_a_part_a_done_codex_job_answered_by_its_done_takes_it_off_that_job_and_names_the_drive_order_posted_that_delivers_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(state, "now", lambda: T0)
    asked = "why do old runs fail?"
    with edit(demo) as data:
        data["orders"]["o2"] = order(text=asked, jobs=["j2"], kind="build", parts={asked: state.new_part()})
        data["jobs"]["j2"] = job(order="o2", kind="codex", state="done", said="DONE: old runs fail on a stale cache", parts=[asked])
    assert cli.main(["order", "posted", "o2", REPORTED, "--part", asked, "--drive", demo]) == 2
    assert state.UNPOSTED_WORK.format(order="o2", part=asked) in capsys.readouterr().err
    assert cli.main(["order", "set", "o2", "--kind", "answer", "--part", asked, "--drive", demo]) == 0
    assert (
        f"o2 \"{asked}\": sorted as an answer, taken off codex j2, done, whose hold kept it work he ordered; no job carries it,"
        f" so drive order posted o2 <the drive's post that answered it> --part '{asked}' delivers it"
    ) in capsys.readouterr().out.splitlines()
    data = state.load(demo)
    assert (data["jobs"]["j2"]["parts"], state.work(data, "o2", asked), state.delivered(data, "o2", asked)) == ([], False, False)


def test_only_the_session_an_orders_orphan_lines_reach_runs_drive_order_posted_and_each_refusal_names_that_session(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sub_coordinated(demo)
    lands, landed = "#43 lands", state.pr_url(OTHER)
    with edit(demo) as data:
        data["orders"]["o2"] = order(text=lands, jobs=["j2"], kind="build", parts={lands: state.new_part()})
        data["jobs"]["j2"] = job(order="o2", state="failed", prs=[landed], parts=[lands])
        data["prs"][OTHER] = state.new_pr(landed, "j2", "o2") | {"state": "merged"}
    serve(monkeypatch, {OTHER: read(state="MERGED")})
    posted = ["order", "posted", "o2", landed, "--part", lands, "--drive", demo]
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    assert cli.main(posted) == 2
    assert "a part a post of the drive already answered is marked from the root@demo session, the one its ORPHAN lines reach" in capsys.readouterr().err
    with edit(demo) as data:
        data["orders"]["o2"]["project"] = "Iris"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-under-test")
    assert cli.main(posted) == 2
    assert "a part a post of the drive already answered is marked from the iris@demo session, the one its ORPHAN lines reach" in capsys.readouterr().err
    assert state.load(demo)["orders"]["o2"]["parts"][lands]["posted"] is None
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    assert cli.main(posted) == 2
    assert f'--part "{lands}": refused, ' + state.UNPOSTED_WORK.format(order="o2", part=lands) in capsys.readouterr().err
    assert state.load(demo)["orders"]["o2"]["parts"][lands]["posted"] is None


@pytest.mark.parametrize("ended", ["failed", "done"])
def test_an_open_incidents_fix_whose_jobs_ended_with_no_pr_has_no_fix_job_and_names_the_second_to_start_until_one_runs(
    demo: str, monkeypatch: pytest.MonkeyPatch, ended: str
) -> None:
    serve(monkeypatch, {})
    earlier = state.stamp(T0 - timedelta(minutes=10))
    with edit(demo) as data:
        proven(data, updated=earlier)
        data["orders"]["o2"] = order(text="runs never start", incident=True, jobs=["j2"], parts={state.INCIDENT_FIX: state.new_part()}, updated=earlier)
        data["jobs"]["j2"] = job(order="o2", kind="codex", goal="fix: runs never start", state=ended, parts=[state.INCIDENT_FIX], updated=earlier)
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == [
        f'ORPHAN o2 "fix": no fix job: j2 ended {ended} with no open or merged PR; start the second fix job now, an Opus 5.5 long-running:builder on j2\'s brief:'
        " drive job add builder --order o2 --part fix --goal 'fix: runs never start' (incidents: Clocks)"
    ]
    with edit(demo) as data:
        data["orders"]["o2"]["jobs"].append("j3")
        data["jobs"]["j3"] = job(order="o2", goal="fix: runs never start", parts=[state.INCIDENT_FIX])
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    assert watch.run_pass(watching, T0 + watch.HANDOFF) == []
    assert state.load(demo)["orders"]["o2"]["parts"][state.INCIDENT_FIX]["orphan_said"] is None


def test_an_open_incident_with_no_fix_part_whose_jobs_ended_with_no_pr_card_or_proof_has_no_fix_job_and_names_its_newest_change_jobs_brief(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    stopped = "runs stopped for one team at 05:01:42Z"
    earlier = state.stamp(T0 - timedelta(minutes=10))
    with edit(demo) as data:
        proven(data, updated=earlier)
        data["orders"]["o2"] = order(text=stopped, incident=True, jobs=["j2", "j3", "j4"], parts={stopped: state.new_part()}, updated=earlier)
        for job_id, kind, goal, minutes in (("j2", "builder", "stop the stopped runs", 30), ("j3", "builder", "fix the stopped runs", 20), ("j4", "codex", "cause, read only, at once", 40)):
            data["jobs"][job_id] = job(order="o2", kind=kind, goal=goal, state="done", parts=[stopped], created=state.stamp(T0 - timedelta(minutes=minutes)), updated=earlier)
    watching = watcher(demo)
    idle = (
        "no fix job: j3 ended done with no open or merged PR, no job of o2 is at work or has his card open, and it keeps no proof users are unblocked;"
        " start the second fix job now, an Opus 5.5 long-running:builder on j3's brief:"
        f" drive job add builder --order o2 --part '{stopped}' --goal 'fix the stopped runs' (incidents: Clocks)"
    )
    assert watch.run_pass(watching, T0) == [f'ORPHAN o2 "{stopped}": {idle}']
    with edit(demo) as data:
        data["orders"]["o2"]["jobs"].append("j5")
        data["jobs"]["j5"] = job(order="o2", kind="reader", goal="read the queue", state="done", parts=[stopped], created=state.stamp(T0 - timedelta(minutes=5)), updated=earlier)
    assert watch.part_gap(watching, state.load(demo), "o2", stopped, T0) == idle
    unbuilt = (
        f"{NO_BUILDER}builder j2 done, builder j3 done, codex j4 done, reader j5 done; j2 and j3 and j4 done with no passing proof delivering it:"
        f" start one with drive job add builder --order o2 --part '{stopped}' --goal '<what it builds>', or add it to a builder with drive job set <builder> --part '{stopped}',"
        f" adding --carries o2 for another order's builder; when it asks only for an answer, drive order set o2 --kind answer --part '{stopped}'"
    )
    with edit(demo) as data:
        data["jobs"]["j2"]["questions"] = [state.new_question(None, "Which store?", earlier, card=CARD)]
    assert watch.part_gap(watching, state.load(demo), "o2", stopped, T0) == unbuilt
    with edit(demo) as data:
        data["jobs"]["j2"]["questions"] = []
        data["orders"]["o2"]["parts"]["Status?"] = state.new_part()
        data["orders"]["o2"]["jobs"].append("j6")
        data["jobs"]["j6"] = job(order="o2", kind="reader", goal="where it stands", parts=["Status?"])
    assert watch.part_gap(watching, state.load(demo), "o2", stopped, T0) == unbuilt
    with edit(demo) as data:
        data["jobs"]["j6"]["state"] = "done"
        data["jobs"]["j3"]["prs"] = [state.pr_url(OTHER)]
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j3", "o2")
    assert watch.part_gap(watching, state.load(demo), "o2", stopped, T0) == unbuilt
    with edit(demo) as data:
        data["prs"][OTHER]["state"] = "closed"
    assert watch.part_gap(watching, state.load(demo), "o2", stopped, T0) == idle
    with edit(demo) as data:
        data["orders"]["o2"]["proof"] = "https://buildkite.com/forge/release/builds/15020"
    assert watch.part_gap(watching, state.load(demo), "o2", stopped, T0) == unbuilt


def test_a_waiting_job_lives_while_its_question_is_open_and_is_an_orphan_once_it_is_withdrawn_whose_card_the_watch_then_closes_once(
    demo: str, monkeypatch: pytest.MonkeyPatch, closed_cards: list[tuple[str, str, str, bool]]
) -> None:
    card = {"question": "Which store?", "options": ["Team", "Run"], "recommended": "Team", "asked": state.stamp(T0), "url": f"{CARD}?thread_ts=1.2", "card": {"title": "Which store?"}}
    shown = {"team_url": "", "questions": [card], "closed": [], "pr_cards": []}
    monkeypatch.setattr(watch.view, "cards", lambda name: shown)
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"].update(
            state="waiting", said="ASK which store?", questions=[state.new_question(None, "Which store?", state.stamp(T0), card=CARD)], updated=state.stamp(T0 - timedelta(minutes=10))
        )
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    assert watch.run_pass(watching, T0 + timedelta(seconds=60)) == []
    monkeypatch.setattr(watch.view, "cards", lambda name: shown)
    with edit(demo) as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("withdrawn", "his order replaced it", REPORTED, at=state.stamp(T0 - timedelta(minutes=10)))
    assert watch.run_pass(watching, T0 + timedelta(seconds=120)) == [
        f"CARD {CARD}?thread_ts=1.2 closed in place: its question withdrawn",
        'ORPHAN o1 "ship the watch": j1 waits on "ASK which store?" and no open PR, card or due time can wake it',
    ]
    assert closed_cards == [(f"{CARD}?thread_ts=1.2", "his order replaced it", REPORTED, False)]
    shown["questions"] = []
    assert watch.run_pass(watching, T0 + timedelta(seconds=180)) == []
    assert len(closed_cards) == 1


def test_the_watch_closes_his_card_again_with_his_words_in_place_of_his_tap_they_replaced_until_cc_slack_shows_his_words(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tap, other_tap = "1794200011.000100", "1794200021.000300"
    other_card = "https://acme.slack.com/archives/D123/p1794200001000100"
    words = "https://acme.slack.com/archives/D123/p1794200016000200?thread_ts=1794200000.000100"
    replaced = state.new_close("answered", "Ring", None, seq="51", tap=tap)
    with edit(demo) as data:
        data["jobs"]["j1"]["questions"] = [
            state.new_question(None, "Which ring?", state.stamp(T0), card=CARD, closed=state.new_close("answered", "Knock, the bell is broken", words, answer="a1", replaces=replaced)) | {"waits": "demo:j1-7"}
        ]
        data["jobs"]["j2"] = job(state="done", questions=[state.new_question(None, "Which door?", state.stamp(T0), card=other_card, closed=state.new_close("answered", "Side", None, seq="53", tap=other_tap)) | {"waits": "demo:j2-8"}])

    def shown_tap(waits: str, url: str, at: str, label: str) -> dict:
        return {"waits": waits, "title": "", "prs": [], "asked": state.stamp(T0), "url": url, "answer": {"at": at, "label": label}, "tapped": f"✓ {label}", "replaced_by": ""}

    tapped = shown_tap("demo:j1-7", CARD, datetime.fromtimestamp(float(tap)).astimezone().isoformat(), "Ring")
    standing = shown_tap("demo:j2-8", other_card, datetime.fromtimestamp(float(other_tap)).astimezone().isoformat(), "Side")
    closes: list[tuple[str, str, str, bool, str | None]] = []
    down = [slack.SlackError("cc-slack daemon not running")]

    def answer_card(card: str, words: str, link: str, settled: bool, replaces: str | None = None) -> dict:
        closes.append((card, words, link, settled, replaces))
        if down:
            raise down.pop()
        return {}

    monkeypatch.setattr(superseded, "answer_card", answer_card)
    watching = watcher(demo)
    watching.cards = {**view.NO_CARDS, "closed": [tapped, standing]}
    assert watch.follow_lines(watching, T0) == []
    assert watch.follow_lines(watching, T0 + timedelta(seconds=60)) == [f"CARD {CARD} closed in place: its question answered, his words in place of his tap"]
    assert closes == [(CARD, "Knock, the bell is broken", words, True, tap)] * 2
    tapped["answer"] = {"at": state.stamp(T0), "label": "Knock, the bell is broken", "other": True}
    assert watch.follow_lines(watching, T0 + timedelta(seconds=120)) == []
    assert len(closes) == 2


def test_a_built_job_whose_ask_drive_job_ask_refused_is_said_to_wait_on_a_card_never_posted(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    refused = "refused: these recorded answers of his may already settle this question. Read each with `ccn answer show <id>`"
    asked = "How many clone processes may a SandSQL server run at once?"
    with edit(demo) as data:
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
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["prs"].pop(KEY)
        parts = data["jobs"].pop("j1")["parts"]
        asked = state.new_question(f"/drive/asks/{reader}-ab.md", "Which shape?", state.stamp(T0), holds=None) | {"waits": card, "card": drawn_card(1)}
        data["jobs"][reader] = job(kind="reader", state="done", parts=parts, questions=[asked])
        data["jobs"][builder] = job(parts=parts, researched=[reader])
        data["orders"]["o1"]["jobs"] = [reader, builder]
    with edit(demo) as data:
        state.take_result(data, builder, said)
        data["jobs"][builder]["updated"] = state.stamp(T0 - timedelta(minutes=10))
    assert state.load(demo)["jobs"][builder]["waits"] == [card]
    branch = said.removeprefix("BUILT ").partition(",")[0]
    assert watch.run_pass(watcher(demo), T0) == [f"WAITING-ON-HIM {card} blocks {builder}, built on {branch}, since {render.when(state.stamp(T0))}, posted {POSTED}"]


def test_a_builder_waiting_on_its_readers_card_that_drive_job_card_refused_is_said_to_wait_on_a_card_never_posted(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    refused = 'the Show me picture "1 · Before" does not draw: Parse error on line 7'
    with edit(demo) as data:
        data["prs"].pop(KEY)
        parts = data["jobs"].pop("j1")["parts"]
        asked = state.new_question("/drive/asks/j2612-ab.md", "Which shape?", state.stamp(T0), refused=refused, holds=None) | {"waits": "durable-agents:j2612-684681"}
        data["jobs"]["j2612"] = job(kind="reader", state="done", parts=parts, questions=[asked])
        data["jobs"]["j2649"] = job(parts=parts, researched=["j2612"])
        data["orders"]["o1"]["jobs"] = ["j2612", "j2649"]
    with edit(demo) as data:
        state.take_result(data, "j2649", "BUILT j2649-sparse-replace, waits on j2612's card")
        data["jobs"]["j2649"]["updated"] = state.stamp(T0 - timedelta(minutes=10))
    assert watch.run_pass(watcher(demo), T0) == [
        f'ORPHAN o1 "ship the watch": j2649 waits on a card never posted: drive job card refused j2612\'s question "Which shape?" kept in /drive/asks/j2612-ab.md: {refused}'
    ]


def test_a_refused_question_his_recorded_answer_since_settled_no_longer_waits_on_a_card_never_posted(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    refused = "refused: his recorded answer 5630432 already decides this question."
    asked = "Which credential reads Slack as him?"
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"].update(state="waiting", said="WAITING on the release", due=state.stamp(T0 + timedelta(minutes=80)), updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    with edit(demo) as data:
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
    with edit(demo) as data:
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
    assert watch.run_pass(watching, T0) == ["HELD j2: his held words went to root@demo, which holds its order, as record 9 to read"]
    monkeypatch.setattr(watch.view, "cards", lambda name: watch.view.NO_CARDS)
    with edit(demo) as data:
        data["jobs"]["j2"]["questions"][0]["closed"] = state.new_close("answered", "First", None, seq="9", at=state.stamp(T0 - timedelta(minutes=10)))
    assert watch.run_pass(watching, T0 + timedelta(seconds=60)) == [
        'ORPHAN o2 "options: only the owning machine writes": j2\'s card is answered and no job carries his answer'
    ]
    with edit(demo) as data:
        data["orders"]["o2"]["jobs"].append("j3")
        data["jobs"]["j3"] = job(order="o2", kind="builder", parts=["options: only the owning machine writes"])
    assert watch.run_pass(watching, T0 + timedelta(seconds=120)) == []


def test_a_job_changed_in_the_last_two_minutes_is_still_being_handed_over(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with edit(demo) as data:
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


def test_a_running_job_fails_only_once_every_read_for_the_reap_bound_found_no_live_worker(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(watch, "local_gap", lambda job_id, job, data, moment: f"{job_id}'s agent ended with an earlier session")
    with edit(demo) as data:
        data["jobs"]["j1"].update(session="old", agent="a1")
    lost = T0 + timedelta(hours=7)
    assert staged(watch.agent_lines, watcher(demo), lost) == []
    assert state.load(demo)["jobs"]["j1"]["lost_since"] == state.stamp(lost)
    assert staged(watch.agent_lines, watcher(demo), lost + watch.REAP_AFTER - timedelta(minutes=1)) == []
    assert state.load(demo)["jobs"]["j1"]["state"] == "running"
    lines = staged(watch.agent_lines, watcher(demo), lost + watch.REAP_AFTER)
    said = "failed: the watch found no live worker on every read since " + watch.when(state.stamp(lost)) + " (no bound session holds its agent a1)"
    assert lines == [f"LANE j1 o1 {said}; its part's ORPHAN line names who starts its next worker"]
    assert (state.load(demo)["jobs"]["j1"]["state"], state.load(demo)["jobs"]["j1"]["said"], state.load(demo)["jobs"]["j1"]["lost_since"]) == ("failed", said, None)


def test_a_live_read_clears_the_loss_a_running_job_was_stamped_with(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(watch, "local_gap", lambda job_id, job, data, moment: None)
    with edit(demo) as data:
        data["jobs"]["j1"].update(session="old", agent="a1")
    assert staged(watch.agent_lines, watcher(demo), T0) == []
    with edit(demo) as data:
        data["jobs"]["j1"]["session"] = "session-under-test"
    assert staged(watch.agent_lines, watcher(demo), T0 + watch.REAP_AFTER) == []
    assert (state.load(demo)["jobs"]["j1"]["state"], state.load(demo)["jobs"]["j1"]["lost_since"]) == ("running", None)


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
        ([CALLING, FINISHED], timedelta(minutes=10), 'j1\'s agent finished and its result is not recorded: "DONE shipped"'),
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
    taken = state.stamp(datetime.fromtimestamp((folder / "status").stat().st_mtime).astimezone() + timedelta(seconds=1))
    job_of = {"kind": "codex", "codex_run": str(folder), "returned": None}
    assert REAL_CODEX_GAP("j1", job_of) == "j1's codex-ask run completed and its result is not recorded"
    assert REAL_CODEX_GAP("j1", job_of | {"returned": taken}) == "j1's codex-ask run ended before its last return: drive job launch j1 starts its next"


def test_a_codex_run_whose_process_is_gone_with_no_status_written_reads_as_ended(demo: str, codex_ask: CodexAsk) -> None:
    folder = codex_ask.run("died")
    (folder / "status").unlink()
    gone = subprocess.Popen(["true"])
    gone.wait()
    (folder / "pid").write_text(str(gone.pid))
    assert REAL_CODEX_GAP("j1", {"kind": "codex", "codex_run": str(folder)}) == "j1's codex-ask run died and its result is not recorded"


def test_an_incidents_second_lane_builder_is_judged_by_its_transcript_until_the_codex_run_beside_it_ends(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk
) -> None:
    monkeypatch.setattr(watch, "local_gap", REAL_LOCAL_GAP)
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    write_transcript(tmp_path / "config", [CALLING, FINISHED], timedelta(minutes=10))
    folder = codex_ask.run("running")
    job_of = {"order": "o1", "where": "local", "session": "session-under-test", "agent": "a1", "kind": "codex", "codex_run": str(folder)}
    assert watch.agent_gap("j1", job_of, state.load(demo), state.now()) == 'j1\'s agent finished and its result is not recorded: "DONE shipped"'
    codex_ask.end(folder, "completed", codex_reply("DONE", "reviewed"))
    assert isinstance(watch.agent_gap("j1", job_of, state.load(demo), state.now()), watch.Answered)


def test_a_relaunched_codex_job_is_judged_by_its_new_run_never_by_the_agent_an_earlier_lane_left_on_it_as_j3122s_was(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk
) -> None:
    monkeypatch.setattr(watch, "local_gap", REAL_LOCAL_GAP)
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    write_transcript(tmp_path / "config", [CALLING, FINISHED], timedelta(minutes=10))
    brief = tmp_path / "j1.brief.md"
    brief.write_text(f"Order (his words): ship the watch\nGoal: watch\nResearched: j2\nDrive: {demo}, order o1, job j1\n")
    state.keep_report(demo, "j2", state.opening(RESEARCH), RESEARCH)
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"].update(kind="codex", state="waiting", session="session-under-test", agent="a1")
        data["jobs"]["j2"] = job(kind="reader", goal="research: the watch", parts=data["jobs"]["j1"]["parts"], state="done")
        data["orders"]["o1"]["jobs"].append("j2")
    assert cli.main(["job", "launch", "j1", str(brief), "--drive", demo]) == 0
    relaunched = state.load(demo)["jobs"]["j1"]
    assert (relaunched["state"], relaunched["agent"], relaunched["session"]) == ("running", None, "session-under-test")
    assert watch.run_pass(watcher(demo), state.now() + watch.HANDOFF) == []


def codex_running(demo: str, folder: Path) -> None:
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"].update(kind="codex", agent=None, codex_run=str(folder), updated=state.stamp(state.now() - timedelta(minutes=10)))


def codex_taken(watching: watch.Watcher) -> list[str]:
    watch.codex_takes(watching)
    return watch.event_lines(watching)


def test_the_watch_takes_a_codex_jobs_result_from_the_state_line_and_report_its_completed_run_returned(
    demo: str, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk
) -> None:
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    codex_running(demo, codex_ask.run("completed", codex_reply("DONE", "two of three findings hold", "Finding 1\n")))
    assert codex_taken(watcher(demo)) == ["JOB j1 o1 DONE two of three findings hold"]
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], job["said"], state.report_text(demo, "j1")) == ("done", "DONE two of three findings hold", "DONE two of three findings hold\nFinding 1\n")


def test_the_watch_tells_the_coordinator_of_a_codex_run_that_died_before_its_first_turn(demo: str, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk) -> None:
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    died = "Error: thread/start: thread/start failed: error creating thread: Fatal error: Failed to initialize session: required MCP servers failed to initialize: datadog: MCP client startup timed out after 30s (code -32603)"
    codex_running(demo, codex_ask.run("failed", log=f"Reading prompt from stdin...\n{died}\n"))
    assert codex_taken(watcher(demo)) == [f"JOB j1 o1 BLOCKED codex-ask failed: {died}"]
    assert state.load(demo)["jobs"]["j1"]["state"] == "failed"


def test_a_codex_job_running_with_no_run_of_its_own_left_to_take_is_taken_as_blocked_by_the_relay_never_left_running(
    demo: str, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk
) -> None:
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    folder = codex_ask.run("completed", codex_reply("DONE", "reviewed"))
    codex_running(demo, folder)
    with edit(demo) as data:
        data["jobs"]["j1"]["returned"] = state.stamp(datetime.fromtimestamp((folder / "status").stat().st_mtime).astimezone() + timedelta(seconds=1))
    gone = "BLOCKED j1's codex-ask run ended before its last return: drive job launch j1 starts its next"
    assert codex_taken(watcher(demo)) == [f"JOB j1 o1 {gone}"]
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("failed", gone)


def test_a_codex_run_that_died_with_no_reply_after_its_jobs_last_return_is_taken_as_its_failure(demo: str, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk) -> None:
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    folder = codex_ask.run("died", log="Reading prompt from stdin...\nkilled\n")
    (folder / "codex-r-1").unlink()
    codex_running(demo, folder)
    with edit(demo) as data:
        data["jobs"]["j1"]["returned"] = state.stamp(state.now() - timedelta(hours=1))
    assert codex_taken(watcher(demo)) == ["JOB j1 o1 BLOCKED codex-ask died: killed"]
    assert state.load(demo)["jobs"]["j1"]["state"] == "failed"


def test_a_codex_job_whose_run_codex_ask_cannot_read_counts_lost_from_the_first_pass_that_finds_it_so_and_fails_once_reaped(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk
) -> None:
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    codex_running(demo, codex_ask.run("completed", codex_reply("DONE", "reviewed")))
    (tmp_path / "codex-plugin" / "bin" / "codex-ask").write_text("#!/bin/sh\necho 'codex-ask: no run at that folder' >&2\nexit 2\n")
    moment = state.now()
    watch.run_pass(watcher(demo), moment)
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], job["lost_since"]) == ("running", state.stamp(moment))
    watch.run_pass(watcher(demo), moment + watch.REAP_AFTER)
    job = state.load(demo)["jobs"]["j1"]
    assert job["state"] == "failed"
    assert job["said"].endswith("(j1's codex-ask run could not be read: codex-ask: no run at that folder)")


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
    assert codex_taken(watcher(demo)) == [
        f"JOB j1 o1 DONE built one snapshot file per finished run on branch snapshot-file; its ASK is kept in {kept},"
        f" his card {drawn_card(1)} asks it under demo:j1-7, and the recommended option it built ships meanwhile"
    ]
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], [question["file"] for question in job["questions"]], kept.read_text()) == ("waiting", [str(kept)], SHORT_ASK)


def test_a_codex_job_that_said_nothing_before_its_run_returned_an_ask_is_heard_on_its_ask_line_alone_rather_than_stopping_the_watch_as_j6704s_did(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk
) -> None:
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    block = tmp_path / "scratch" / "ask.md"
    codex_running(demo, codex_ask.run("completed", codex_reply("ASK", str(block))))
    with edit(demo) as data:
        data["jobs"]["j1"]["said"] = None
    assert codex_taken(watcher(demo)) == [
        f"JOB j1 o1 its ASK: {block} is not kept: [Errno 2] No such file or directory: '{block}'; keep it with drive job ask j1 {block}"
    ]
    assert state.load(demo)["jobs"]["j1"]["state"] == "waiting"


def incident_codex_running(demo: str, folder: Path, part: str) -> None:
    codex_running(demo, folder)
    with edit(demo) as data:
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
    lines = codex_taken(watcher(demo))
    data = state.load(demo)
    written = state.drive_dir(demo) / "jobs" / "j2.brief.md"
    assert lines == [
        f'JOB j1 o1 {data["jobs"]["j1"]["said"]}; its fix is not live, so j2, an Opus 5.5 long-running:builder the drive added on "fix" to ship it:'
        f' the watch starts its agent on its brief {written}, so start no agent for it; drive job resume reaches it'
    ]
    assert (data["jobs"]["j1"]["state"], data["orders"]["o1"]["jobs"]) == (ended, ["j1", "j2"])
    assert (data["jobs"]["j2"]["kind"], data["jobs"]["j2"]["parts"], data["jobs"]["j2"]["brief"]) == ("builder", ["fix"], str(written))
    assert "Handoff: drive job report j1, the codex fix job whose work this builder ships, on its brief /briefs/j1.md\n" in written.read_text()


def test_an_incident_codex_fix_jobs_handoff_reaches_its_session_naming_the_builder_added_and_drops_the_orphan_line_its_part_still_waited_with_as_o2316s_did(
    demo: str, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk
) -> None:
    sent = posts(monkeypatch)
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    incident_codex_running(demo, codex_ask.run("completed", codex_reply("HANDOFF", "edits left uncommitted in worktree fix-runs on branch fix-runs")), state.INCIDENT_FIX)
    stale = 'ORPHAN o1 "fix": j1 has no brief 4 minutes after drive job add, so nothing starts it: `drive job brief j1`, then `drive job launch j1`'
    with edit(demo) as data:
        data["orders"]["o1"]["parts"][state.INCIDENT_FIX].update(orphan_said=state.stamp(T0), orphan_gap=stale.partition('": ')[2])
    watching = watcher(demo)
    watch.keep_posted(demo, "root", T0)
    watching.owed_at["root"] = T0
    lines = codex_taken(watching)
    assert watch.sent(watching, "root", [stale, *lines], T0 + timedelta(minutes=1)) == []
    assert posted(sent) == [("handoff", f"root@{demo}", lines[0])]
    assert "j2, an Opus 5.5 long-running:builder the drive added" in lines[0]
    assert state.load(demo)["orders"]["o1"]["parts"][state.INCIDENT_FIX]["orphan_said"] is None


def test_a_held_line_waiting_on_the_watcher_posts_no_step_once_its_agent_took_a_message_naming_its_record_in_other_words_as_j4834s_did(
    demo: str, monkeypatch: pytest.MonkeyPatch, send_message: Callable[..., None]
) -> None:
    sent = posts(monkeypatch)
    with edit(demo) as data:
        data["jobs"]["j1"].update(agent="a1", where="local", session="session-under-test")
    folder = state.drive_dir(demo) / "jobs"
    folder.mkdir(parents=True, exist_ok=True)
    held = (state.now() - timedelta(minutes=30)).timestamp()
    for seq in ("736226", "736227"):
        (folder / f"j1.sent-{seq}.md").write_text("The staging test of #49378 also runs")
        os.utime(folder / f"j1.sent-{seq}.md", (held, held))
    send_message(demo, "j1", "New staging cases for the test you run: read cci record 736226 whole and add them without starting over.")
    told, owed = (f'HELD j1: posted to agent a1\'s cci address as handoff record {seq}: j1: owed, send it now: SendMessage a1 "Read cci record {seq} whole and act on it" [o1 j1]' for seq in ("736226", "736227"))
    assert watch.sent(watcher(demo), "root", [told, owed], T0) == []
    assert [line for _, _, line in posted(sent)] == [owed]


def test_an_incident_codex_jobs_return_on_a_part_other_than_its_fix_adds_no_builder(demo: str, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk) -> None:
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    incident_codex_running(demo, codex_ask.run("completed", codex_reply("HANDOFF", "the first check is left on branch first-check")), "first check")
    lines = codex_taken(watcher(demo))
    data = state.load(demo)
    assert (lines, list(data["jobs"])) == ([f"JOB j1 o1 {data['jobs']['j1']['said']}"], ["j1"])


def test_agent_lines_takes_no_codex_run_since_the_relay_takes_each(demo: str, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk) -> None:
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    codex_running(demo, codex_ask.run("completed", codex_reply("DONE", "reviewed")))
    assert said_lines(watch.agent_lines, watcher(demo), state.now()) == []
    assert state.load(demo)["jobs"]["j1"]["state"] == "running"


def test_only_the_watch_hearing_a_codex_job_takes_its_ended_run(demo: str, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk) -> None:
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    codex_running(demo, codex_ask.run("completed", codex_reply("DONE", "reviewed")))
    sub_coordinated(demo)
    assert codex_taken(watcher(demo)) == []
    assert codex_taken(watch.Watcher(demo, offset=state.events_end(demo), project="Iris")) == ["JOB j1 o1 DONE reviewed"]


@pytest.mark.parametrize("going", [1, 100])
def test_a_relay_pass_runs_codex_ask_collect_only_on_the_run_that_ended_however_many_codex_runs_go(
    demo: str, monkeypatch: pytest.MonkeyPatch, codex_ask: CodexAsk, going: int
) -> None:
    monkeypatch.setattr(watch, "codex_gap", REAL_CODEX_GAP)
    collected = []
    collect = codex.run_of
    monkeypatch.setattr(codex, "run_of", lambda job_of: collected.append(job_of["codex_run"]) or collect(job_of))
    ended = codex_ask.run("completed", codex_reply("DONE", "reviewed"))
    codex_running(demo, ended)
    with edit(demo) as data:
        for at in range(going):
            data["jobs"][f"j{100 + at}"] = data["jobs"]["j1"] | {"prs": [], "codex_run": str(codex_ask.run("running"))}
            data["orders"]["o1"]["jobs"].append(f"j{100 + at}")
    assert (codex_taken(watcher(demo)), collected) == (["JOB j1 o1 DONE reviewed"], [str(ended)])


def test_a_local_agent_a_sub_coordinator_started_is_judged_by_its_transcript_under_that_session(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    path = tmp_path / "config" / "projects" / "-iris" / "iris-1" / "subagents" / "agent-a1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in (CALLING, FINISHED)))
    stamp = (state.now() - timedelta(minutes=10)).timestamp()
    os.utime(path, (stamp, stamp))
    sub_coordinated(demo, cwd="/iris")
    job_of = {"session": "iris-1", "agent": "a1", "kind": "builder"}
    assert REAL_LOCAL_GAP("j1", job_of, state.load(demo), state.now()) == 'j1\'s agent finished and its result is not recorded: "DONE shipped"'


def test_a_local_agents_bash_call_still_running_at_nine_minutes_is_said_nothing(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    serve(monkeypatch, {})
    asked = state.now()
    call = {"type": "tool_use", "id": "toolu_waiting", "name": "Bash", "input": {"command": "zsh /scratch/j1/wait.sh", "timeout": 570000}}
    write_transcript(tmp_path / "config", [{"type": "assistant", "timestamp": state.stamp(asked), "message": {"stop_reason": "tool_use", "content": [call]}}], timedelta(0))
    with edit(demo) as data:
        data["jobs"]["j1"].update(session="session-under-test", agent="a1")
    assert staged(watch.agent_lines, watcher(demo), asked + timedelta(minutes=9)) == []


def test_a_local_agent_from_another_session_or_with_no_transcript_is_gone(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    data = state.load(demo)
    assert REAL_LOCAL_GAP("j1", {"session": "old", "agent": "a1"}, data, state.now()) == "j1's agent ended with an earlier session"
    assert REAL_LOCAL_GAP("j1", {"session": "session-under-test", "agent": "a1"}, data, state.now()) == "j1's agent a1 has no transcript"


@pytest.mark.parametrize("session", [None, "session-under-test", "46723e7e-879a-45ac-a21c-71b7ab11db5a"], ids=["no-session", "bound-session", "a-lanes-session"])
@pytest.mark.parametrize(
    ("brief", "start"),
    [
        (None, "brief it with drive job brief j1, then start its agent on the line that prints"),
        ("/drive/jobs/j1.brief.md", "the watch starts its agent on its brief /drive/jobs/j1.brief.md, so start no agent for it; drive job resume reaches it"),
    ],
    ids=["unbriefed", "briefed"],
)
def test_j135_a_local_job_no_worker_started_never_started_whatever_session_added_it(demo: str, session: str | None, brief: str | None, start: str) -> None:
    unstarted = {"session": session, "agent": None, "worker_started": None, "brief": brief}
    assert REAL_LOCAL_GAP("j1", unstarted, state.load(demo), state.now()) == f"j1 never started: no agent has taken it, so {start}"


def test_j135_a_local_job_a_worker_started_whose_agent_is_gone_reads_by_its_session_never_as_not_started(demo: str) -> None:
    data = state.load(demo)
    started = state.stamp(T0)
    assert REAL_LOCAL_GAP("j1", {"session": "old", "agent": None, "worker_started": started}, data, state.now()) == "j1's agent ended with an earlier session"
    assert REAL_LOCAL_GAP("j1", {"session": "session-under-test", "agent": None, "worker_started": started}, data, state.now()) == "j1 has no agent recorded"


def test_j135_his_card_a_once_only_pass_ends_on_unstarted_change_builders_frees_each_with_the_passs_reason_once_and_sets_no_state(demo: str) -> None:
    waits = "demo:j2-1794725828071"
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"] += ["j2", "j3", "j4"]
        data["jobs"]["j2"] = job(kind="design", goal="cards: the copy", state="waiting", said="DESIGN the copy", questions=[state.new_question("/asks/j2-a.md", ASKED, state.stamp(T0)) | {"waits": waits}])
        data["jobs"]["j3"] = job(goal="build j2's change: the reads", state="waiting", said=f"waits on {waits}", waits=[waits])
        data["jobs"]["j4"] = job(goal="build j2's change: the writes", state="waiting", said=f"waits on {waits}, j3", waits=[waits, "j3"])
        del data["change_builders_unwaited"]
    watching = watcher(demo)
    assert watch.waits_lines(watching, T0) == []
    drive = state.load(demo)
    assert work.item_of(drive, drive["jobs"]["j3"]) is work.KIND[work.BUILD]
    assert {job_id: (drive["jobs"][job_id]["state"], drive["jobs"][job_id]["waits"], drive["jobs"][job_id]["said"]) for job_id in ("j3", "j4")} == {
        "j3": ("waiting", [], state.NOT_STARTED),
        "j4": ("waiting", ["j3"], state.NOT_STARTED),
    }
    assert (drive["dropped_waits"], drive["change_builders_unwaited"], watch.waits_lines(watching, T0 + timedelta(minutes=1))) == ({}, True, [])


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
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"].update(
            kind=kind,
            session="session-under-test",
            agent="a1",
            said=ASKED,
            questions=[state.new_question(str(kept), ASKED, state.stamp(T0), card=CARD)],
            updated=state.stamp(state.now() - timedelta(minutes=10)),
        )


def test_a_running_local_reader_that_ended_on_a_line_opening_with_no_result_word_is_an_orphan_whatever_it_kept(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    returned(demo, tmp_path, monkeypatch, "reader", "My report was kept before your message arrived, so I did not resubmit.", "DONE ccx stages its sweep\nResearched:\n")
    [line] = watch.run_pass(watcher(demo), state.now())
    assert (line.startswith(UNRECORDED), line.endswith(REPORT_IT + WOKEN_BY_ITS_SESSION)) == (True, True)
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("running", ASKED)


def ended_return(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> Path:
    monkeypatch.setattr(watch, "local_gap", REAL_LOCAL_GAP)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    with edit(demo) as data:
        data["jobs"]["j1"].update(kind="builder", session="session-under-test", agent="a1", updated=state.stamp(state.now() - timedelta(minutes=10)))
    write_transcript(tmp_path / "config", [CALLING, RESULT, ended_on(text)], timedelta(minutes=4))
    return tmp_path / "config" / "projects" / "-work" / "session-under-test" / "subagents" / "agent-a1.jsonl"


def test_the_watch_takes_a_local_agents_unrecorded_return_from_its_transcript_as_drive_job_report_would(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ended_return(demo, tmp_path, monkeypatch, "BLOCKED the brief names no repo to build in\nNEXT: name the repo")
    watching = watcher(demo)
    watch.agent_lines(watching, state.now())
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], job["said"], watching.agent_gaps["j1"]) == ("failed", "BLOCKED the brief names no repo to build in", None)


def test_a_local_agents_return_drive_job_report_refuses_stays_kept_with_its_refusal_on_its_job_line_and_is_taken_once(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = ended_return(demo, tmp_path, monkeypatch, "DONE shipped\nREPRO: /tmp/proof.sh")
    watching = watcher(demo)
    assert said_lines(watch.agent_lines, watching, state.now()) == [
        f"JOB j1 o1 DONE shipped; its agent ended without keeping it, so the watch kept it from the end of its transcript {path},"
        " its state unset since drive job report j1 - refuses it: j1: refused, nothing kept: its REPRO: line names /tmp/proof.sh, no file here,"
        ' and j1 keeps no proof file: name the file its proof runs; drive job resume j1 "<the refusal>" sends it back'
    ]
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], state.report_text(demo, "j1"), watching.agent_gaps["j1"]) == ("running", "DONE shipped\nREPRO: /tmp/proof.sh\n", None)
    monkeypatch.setattr(cli, "take_report", lambda *args: pytest.fail("a kept return is taken no second time"))
    assert watch.local_gap("j1", job, state.load(demo), state.now()) == 'j1 reads running while its agent ended its turn on its kept return "DONE shipped", so wake it'


def test_a_running_local_job_whose_agent_ended_on_its_kept_return_is_never_set_by_it_again(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ended_return(demo, tmp_path, monkeypatch, "DONE shipped\nPR: x")
    state.keep_report(demo, "j1", "DONE shipped", "DONE shipped\nPR: x")
    monkeypatch.setattr(cli, "take_report", lambda *args: pytest.fail("a kept return is taken no second time"))
    watch.agent_lines(watcher(demo), state.now())
    assert (state.load(demo)["jobs"]["j1"]["state"], len(state.replies(demo, "j1"))) == ("running", 1)



@pytest.mark.parametrize("read_it", [True, False])
def test_j182_a_running_local_job_whose_agent_read_its_text_and_ended_on_its_same_kept_waiting_goes_back_to_waiting_with_no_line_and_one_that_has_not_read_it_is_woken(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_it: bool
) -> None:
    waiting = "WAITING reply:cci:736559: the packer's GO for my place"
    ended_return(demo, tmp_path, monkeypatch, waiting)
    path = tmp_path / "config" / "projects" / "-work" / "session-under-test" / "subagents" / "agent-a1.jsonl"
    ended = state.now() - timedelta(minutes=4)
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    entries[-1]["timestamp"] = state.stamp(ended)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    os.utime(path, (ended.timestamp(), ended.timestamp()))
    state.keep_report(demo, "j1", waiting, waiting)
    given = ended - timedelta(minutes=1) if read_it else ended + timedelta(minutes=1)
    with edit(demo) as data:
        data["jobs"]["j1"].update(said=waiting, given=[{"words": "a record on staging-slots", "at": state.stamp(given)}])
    monkeypatch.setattr(cli, "take_report", lambda *args: pytest.fail("a kept return is taken no second time"))
    watching = watcher(demo)
    watch.agent_lines(watching, state.now())
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], len(state.replies(demo, "j1"))) == ("waiting" if read_it else "running", 1)
    assert (watching.agent_gaps["j1"] is None) is read_it

def talking(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: list[dict], quiet: timedelta, owed: bool = False) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    path = tmp_path / "config" / "projects" / "-work" / "talker.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    stamp = (state.now() - quiet).timestamp()
    os.utime(path, (stamp, stamp))
    with edit(demo) as data:
        data["roles"]["communicator"] = {"session": "talker", "cwd": "/work", "started": state.stamp(T0)}
        data["owed_replies"] = {f"{state.now().timestamp():.6f}": {"handed": False, "lined": None}, "1794000000.000001": {"handed": False, "lined": None}} if owed else {"1794000000.000001": {"handed": False, "lined": None}}


def answered(tokens: int) -> dict:
    usage = {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": tokens - 10}
    return {"type": "assistant", "message": {"stop_reason": "end_turn", "usage": usage, "content": []}}


SUMMARY = {"type": "user", "isCompactSummary": True, "message": {"content": "summary"}}
IDLE = "at 130000 tokens of context, idle with no message of his owed"


@pytest.mark.parametrize(
    ("entries", "quiet", "owed", "why"),
    [
        ([answered(130_000)], timedelta(minutes=2), False, IDLE),
        ([answered(130_000)], timedelta(minutes=2), True, None),
        ([answered(130_000)], timedelta(seconds=20), False, None),
        ([answered(60_000)], timedelta(minutes=2), False, None),
        ([answered(130_000), SUMMARY], timedelta(minutes=2), False, None),
        ([CALLING], timedelta(minutes=2), False, None),
    ],
)
def test_the_communicator_compacts_past_the_mark_once_idle_a_minute_with_no_message_of_his_owed(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: list[dict], quiet: timedelta, owed: bool, why: str | None
) -> None:
    talking(demo, tmp_path, monkeypatch, entries, quiet, owed)
    data = state.load(demo)
    assert watch.compact_due(data, data["roles"]["communicator"], state.now()) == why


def test_an_idle_communicator_past_the_mark_gets_compact_typed_into_the_terminal_it_runs_in(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runs_in: dict[str, list[dict]]
) -> None:
    talking(demo, tmp_path, monkeypatch, [answered(130_000)], timedelta(minutes=2))
    sent: list[tuple[str, ...]] = []
    runs_in["talker"] = [{"handle": "term-7", "orphaned": False}]
    monkeypatch.setattr(watch.orca, "request", lambda *args: sent.append(args) or {})
    assert watch.compact_lines(watcher(demo), state.now()) == [f"WATCH compacted the communicator {IDLE}"]
    assert sent == [("terminal", "send", "--terminal", "term-7", "--text", "/compact", "--enter")]


def test_a_communicator_no_orca_terminal_runs_is_never_compacted(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    talking(demo, tmp_path, monkeypatch, [answered(130_000)], timedelta(minutes=2))
    sent: list[tuple[str, ...]] = []
    monkeypatch.setattr(watch.orca, "request", lambda *args: sent.append(args) or {})
    assert watch.compact_lines(watcher(demo), state.now()) == []
    assert sent == []


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
    with edit(name) as data:
        data["jobs"]["j1"]["state"] = "waiting"


def test_a_red_pr_wakes_its_builder_as_it_turns_red_and_every_thirty_minutes_it_stays_red_whatever_its_jobs_state(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    serve(monkeypatch, {KEY: read(checks=RED)})
    watching = watcher(demo)
    woke, said = [], []
    for minutes, job_state in ((0, "running"), (29, "running"), (30, "waiting"), (31, "running"), (60, "running")):
        with edit(demo) as data:
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
    with edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "none", "reviews_required": required}
    assert stage.of(state.load(demo), KEY, NO_CARDS).name == named


def test_an_uncarded_pr_carrying_the_hold_label_reads_uncarded_so_the_watch_draws_his_card_offering_approve(demo: str) -> None:
    with edit(demo) as data:
        data["prs"][KEY] |= {"wants_tap": True, "hold_label": True}
    now = stage.of(state.load(demo), KEY, NO_CARDS)
    assert (now.name, now.owner, state.asks_tap(data := state.load(demo), data["prs"][KEY])) == ("uncarded", stage.WATCH, True)


def test_a_builders_line_carries_the_head_github_holds_for_each_open_pr_it_names_with_its_stage_and_each_commit_it_names_that_none_is_at(demo: str) -> None:
    said = f"PUSHED {state.pr_url(KEY)} at bbbbbbb"
    assert stage.heads(state.load(demo), said, "o1", NO_CARDS) == (
        f"; {KEY} is at aaaaaaa on GitHub, grading, owed by Buildkite and forge-pr-reviewer: its checks run at aaaaaaa; GitHub holds none of them at bbbbbbb"
    )
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
    assert stage.heads(state.load(demo), said, "o1", NO_CARDS) == "; GitHub holds none of them at bbbbbbb"


def test_drive_status_shows_each_open_prs_stage_now_keeping_its_since_only_while_the_watch_kept_that_stage(demo: str) -> None:
    with edit(demo) as data:
        data["prs"][KEY]["stage"] = {"name": "grading", "owner": "", "why": "", "since": state.stamp(T0), "woke": None}
    data = state.load(demo)
    stage.shown(data, NO_CARDS, [KEY])
    assert (data["prs"][KEY]["stage"]["name"], data["prs"][KEY]["stage"]["since"]) == ("grading", state.stamp(T0))
    data["prs"][KEY]["checks"] = "fail"
    stage.shown(data, NO_CARDS, [KEY])
    assert (data["prs"][KEY]["stage"]["name"], data["prs"][KEY]["stage"]["since"], state.load(demo)["prs"][KEY]["stage"]["name"]) == ("red", None, "grading")


def test_a_yasyf_stack_lands_its_ready_bottom_pr_alone_since_gh_pr_merge_merges_one_pr_at_a_time(demo: str) -> None:
    above = "yasyf/cc-skills#8"
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        for key, head, base, branch, under in ((YASYF, "aaaaaaa111", "main", "the-watch", None), (above, "bbbbbbb222", "the-watch", "above-the-watch", YASYF)):
            tap = {"label": "Approve", "at": state.stamp(T0), "head": head, "source": "pressed the button", "picks": {}, "card": None}
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {
                "head": head, "base": base, "branch": branch, "under": under, "checks": "pass", "review": "approved", "tap": tap, "checked": CHECKED | {"head": head}
            }
    data = state.load(demo)
    assert (land.landing_stacks(data)[above], land.units(data, [YASYF, above], NO_CARDS)) == ((YASYF, above), [(YASYF,)])


def test_a_yasyf_pr_its_builder_registers_below_the_one_its_taken_ready_names_ships_with_it_bottom_first_and_the_ready_reaches_its_holder_as_j4861s_did_not(demo: str) -> None:
    below = "yasyf/cc-skills#8"
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["prs"][YASYF] = state.new_pr(state.pr_url(YASYF), "j1", "o1") | {
            "head": "aaaaaaa111", "base": "below-the-watch", "branch": "the-watch", "under": below, "checks": "pass", "wants_tap": False, "checked": CHECKED
        }
        data["jobs"]["j1"]["prs"].append(state.pr_url(YASYF))
        state.take_result(data, "j1", f"READY {state.pr_url(YASYF)}")
        data["prs"][below] = state.new_pr(state.pr_url(below), "j1", "o1") | {
            "head": "bbbbbbb222", "base": "main", "branch": "below-the-watch", "checks": "pass", "wants_tap": False, "checked": CHECKED | {"head": "bbbbbbb222"}
        }
        data["jobs"]["j1"]["prs"].append(state.pr_url(below))
    data = state.load(demo)
    line = f"JOB j1 o1 READY {state.pr_url(YASYF)}"
    assert ([stage.of(data, key, NO_CARDS).name for key in (below, YASYF)], land.units(data, [YASYF, below], NO_CARDS), watch.fixing(line, data, NO_CARDS)) == (
        ["ready", "ready"],
        [(below,)],
        False,
    )


@pytest.mark.parametrize(
    ("change", "named", "woke"),
    [
        (lambda reads, data: reads.update({KEY: read(checks=RED, reviewDecision="CHANGES_REQUESTED")}), "red", [1, 2]),
        (lambda reads, data: data["jobs"]["j1"].update(state="done"), "red", [1, 2]),
        (lambda reads, data: data["prs"][KEY].update(tap={"label": "Hold", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "pressed"}), "red", [1, 2]),
        (lambda reads, data: reads.update({KEY: read(checks=GREEN, reviewDecision="APPROVED")}), "author", [1, 2]),
        (lambda reads, data: reads.update({KEY: read(checks=GREEN, reviewDecision="APPROVED", permission="READ")}), "upstream", [1, 1]),
    ],
)
def test_a_red_prs_builder_is_woken_again_only_while_its_stage_stays_red(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], change: Callable[[dict, dict], None], named: str, woke: list[int]
) -> None:
    reads = {KEY: read(checks=RED)}
    serve(monkeypatch, reads)
    watch.run_pass(watcher(demo), T0)
    with edit(demo) as data:
        change(reads, data)
    counts = []
    for minutes in (15, 45):
        watch.run_pass(watcher(demo), T0 + timedelta(minutes=minutes))
        counts.append(len(woken))
    assert (state.load(demo)["prs"][KEY]["stage"]["name"], counts) == (named, woke)


def waits_on(name: str, *waits: str) -> None:
    with edit(name) as data:
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


def test_a_pr_his_open_cards_hold_is_held_by_him_until_he_answers_the_last_and_its_card_is_drawn_once(
    demo: str, monkeypatch: pytest.MonkeyPatch, redraws: Redraws
) -> None:
    waiting_job(demo)
    asking(monkeypatch, demo, "demo:j1-7-a", "demo:j1-7-b", taps=[pr_card(KEY)])
    with edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
        data["jobs"]["j1"]["questions"] = [state.new_question(None, f"Pick {waits}?", state.stamp(T0), holds="the windowed open, on #42") | {"waits": waits} for waits in ("demo:j1-7-a", "demo:j1-7-b")]
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == []
    assert (state.load(demo)["prs"][KEY]["stage"]["why"], redraws.drawn) == ("his cards demo:j1-7-a, demo:j1-7-b on its work are open", [state.pr_url(KEY)])
    for answered, (kept, why) in enumerate((("demo:j1-7-b", "his cards demo:j1-7-b on its work are open"), (None, "green, waits on his Approve"))):
        with edit(demo) as data:
            data["jobs"]["j1"]["questions"][answered]["closed"] = state.new_close("answered", "Yes", None, seq=str(9 + answered))
        asking(monkeypatch, demo, *filter(None, [kept]), taps=[pr_card(KEY)])
        assert watch.run_pass(watching, T0 + timedelta(minutes=31 + answered)) == []
        assert (state.load(demo)["prs"][KEY]["stage"]["why"], redraws.drawn) == (why, [state.pr_url(KEY)])
    assert watch.run_pass(watching, T0 + timedelta(minutes=62)) == []


def test_a_pr_he_approved_that_waits_on_a_running_job_waits_on_it_and_is_freed_when_the_job_ends(demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]) -> None:
    with edit(demo) as data:
        data["jobs"]["j2"] = job(goal="real run")
    waits_on(demo, "j2")
    waiting_job(demo)
    with edit(demo) as data:
        data["prs"][KEY]["tap"] = {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack", "picks": {}, "card": None}
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    shown = state.load(demo)["prs"][KEY]["stage"]
    assert (shown["name"], shown["owner"], shown["why"], landers) == ("waiting", "j2", "it waits on j2, and the watch frees it once each ends", [])
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=90)) == []
    with edit(demo) as data:
        data["jobs"]["j2"]["state"] = "done"
    assert f"FREE {KEY} j1 o1: j2 answered or done, nothing else holds it" in watch.run_pass(watching, T0 + timedelta(minutes=120))
    assert watch.run_pass(watching, T0 + timedelta(minutes=121)) == []


def test_a_pr_he_approved_that_waits_on_a_staging_test_is_owed_by_him_while_its_tester_waits_on_his_card_then_by_the_tester_which_no_deadline_wakes_while_it_runs(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], landers: list[tuple[str, ...]]
) -> None:
    part = "staging test of #42 before it lands"
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j4")
        data["orders"]["o1"]["parts"][part] = state.new_part()
        data["jobs"]["j4"] = job(parts=[part], state="waiting", waits=["demo:j4-1"], questions=[state.new_question(None, "Soak it a day?", state.stamp(T0)) | {"waits": "demo:j4-1"}])
    waits_on(demo, f"o1:{part}")
    waiting_job(demo)
    with edit(demo) as data:
        data["prs"][KEY]["tap"] = {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack", "picks": {}, "card": None}
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert (state.load(demo)["prs"][KEY]["stage"]["name"], state.load(demo)["prs"][KEY]["stage"]["owner"]) == ("blocked", stage.HIM)
    with edit(demo) as data:
        data["jobs"]["j4"] |= {"state": "running", "waits": []}
        data["jobs"]["j4"]["questions"][0]["closed"] = state.new_close("answered", "Yes", None, seq="9")
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    kept = state.load(demo)["prs"][KEY]["stage"]
    assert (kept["name"], kept["owner"], woken, landers) == ("waiting", "j4", [], [])
    jobs = []
    monkeypatch.setattr(stage, "wake", lambda name, key, text, job_id=None, marks=(), at=None: jobs.append((key, job_id)))
    watch.run_pass(watching, T0 + timedelta(minutes=61))
    assert (jobs, stage.testing(state.load(demo), KEY, NO_CARDS)) == ([], True)


def test_a_pr_he_approved_that_waits_on_a_part_of_an_order_lands_only_once_a_proof_delivers_it_never_on_a_done_at_its_merge_or_of_its_own(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    with edit(demo) as data:
        files_order(data)
    waits_on(demo, "o2:stack 1")
    waiting_job(demo)
    with edit(demo) as data:
        data["prs"][KEY]["tap"] = {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack", "picks": {}, "card": None}
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    shown = state.load(demo)["prs"][KEY]["stage"]
    assert (shown["name"], shown["why"], landers) == ("waiting", "it waits on o2:stack 1, and the watch frees it once each ends", [])
    with edit(demo) as data:
        state.take_result(data, "j2", f"DONE {state.pr_url(OTHER)} merged: {OTHER} at ccc333ddd444")
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=30)) if line.startswith("FREE ")]
    assert state.load(demo)["prs"][KEY]["waits"] == ["o2:stack 1"]
    with edit(demo) as data:
        data["prs"][OTHER] = merged_other("j2", "o2")
        data["jobs"]["j2"]["prs"].append(state.pr_url(OTHER))
        state.take_result(data, "j2", f"DONE {state.pr_url(OTHER)} merged, stack 1 ran on every cluster")
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=45)) if line.startswith("FREE ")]
    with edit(demo) as data:
        state.deliver_proven(data, "j2", "j2 live: repro/j2.sh exits 0 on no plugin of this Mac")
    assert f"FREE {KEY} j1 o1: o2:stack 1 answered or done, nothing else holds it" in watch.run_pass(watching, T0 + timedelta(minutes=60))
    assert landers == [(KEY,)]


def test_a_part_open_prs_of_two_jobs_outside_one_stack_wait_on_frees_neither_on_any_done_while_a_part_of_a_prs_own_frees_it_on_its_builders_done(
    named_paths: None,
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    with edit(demo) as data:
        files_order(data)
        data["orders"]["o2"]["jobs"] = ["j2", "j4", "j5"]
        data["orders"]["o2"]["parts"] |= {"staging test of #42 before it lands": state.new_part(), "staging test of #43 before it lands": state.new_part()}
        data["jobs"] |= {
            "j3": job(prs=[state.pr_url(OTHER)]),
            "j4": job(order="o2", parts=["staging test of #42 before it lands"]),
            "j5": job(order="o2", parts=["staging test of #43 before it lands"]),
        }
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j3", "o1") | {"head": "bbbbbbb222", "wants_tap": True, "waits": ["o2:stack 1"]}
    waits_on(demo, "o2:stack 1")
    waiting_job(demo)
    with edit(demo) as data:
        data["prs"][KEY]["tap"] = {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack", "picks": {}, "card": None}
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED"), OTHER: read(checks=GREEN, headRefOid="bbbbbbb222", headRefName="other-fix")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    with edit(demo) as data:
        state.take_result(data, "j2", "DONE followed the staging release; freeze may lift")
        data["orders"]["o2"]["state"] = "done"
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=30)) if line.startswith("FREE ")]
    data = state.load(demo)
    assert ([data["prs"][key]["waits"] for key in (KEY, OTHER)], view.shared(data, "o2:stack 1"), landers) == ([["o2:stack 1"]] * 2, True, [])
    with edit(demo) as data:
        data["orders"]["o2"]["state"] = "working"
        data["prs"][KEY]["waits"] = ["o2:staging test of #42 before it lands"]
        data["prs"][OTHER]["waits"] = ["o2:staging test of #43 before it lands"]
        passed = "DONE every staging case of #42 passed on its cut"
        state.take_result(data, "j4", passed, f"{passed}\nCHECK: https://buildkite.com/forge/release/builds/16164\nCASE 1 release: pass\n")
    freed = [line for line in watch.run_pass(watching, T0 + timedelta(minutes=60)) if line.startswith("FREE ")]
    watch.run_pass(watching, T0 + timedelta(minutes=61))
    kept = state.load(demo)["prs"]
    assert (freed, kept[OTHER]["waits"], kept[KEY]["tap"]["at"], kept[KEY]["fundamental"], landers) == (
        [f"FREE {KEY} j1 o1: o2:staging test of #42 before it lands answered or done, nothing else holds it"],
        ["o2:staging test of #43 before it lands"],
        state.stamp(T0),
        None,
        [(KEY,)],
    )


def test_a_builders_done_while_its_own_pr_is_open_waits_on_that_pr_its_change_undelivered_while_its_check_delivers_the_prs_staging_test_so_the_pr_lands_as_j1308s_did_not(
    named_paths: None,
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    staging, proof = "staging test of #42 before it lands", "https://buildkite.com/forge/release/builds/16121"
    with edit(demo) as data:
        [change] = data["orders"]["o1"]["parts"]
        data["orders"]["o1"]["parts"][staging] = state.new_part()
        data["jobs"]["j1"]["parts"] = [change, staging]
    waits_on(demo, f"o1:{staging}")
    waiting_job(demo)
    with edit(demo) as data:
        data["prs"][KEY]["tap"] = {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack", "picks": {}, "card": None}
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    done = f"DONE {proof} passed every staging case of #42 at aaaaaaa111"
    with edit(demo) as data:
        state.take_result(data, "j1", done, f"{done}\nCHECK: {proof} https://buildkite.com/forge/release/builds/16062\nCASE a rollback refused: pass {proof}\n")
    data = state.load(demo)
    builder = data["jobs"]["j1"]
    assert (builder["state"], builder["said"], builder["waits"]) == ("waiting", f"WAITING {state.pr_url(KEY)} to merge: {done}", [KEY])
    assert ([state.delivered(data, "o1", part) for part in (change, staging)], data["orders"]["o1"]["parts"][staging]["posted"]) == ([False, True], proof)
    freed = [line for line in watch.run_pass(watching, T0 + timedelta(minutes=30)) if line.startswith("FREE ")]
    watch.run_pass(watching, T0 + timedelta(minutes=31))
    kept = state.load(demo)["prs"][KEY]
    assert (freed, kept["tap"]["at"], kept["fundamental"], landers) == (
        [f"FREE {KEY} j1 o1: o1:{staging} answered or done, nothing else holds it"],
        state.stamp(T0),
        None,
        [(KEY,)],
    )


def test_a_done_tester_whose_staging_test_ran_no_full_pass_leaves_its_part_waiting_on_a_rerun_naming_each_case_that_did_not_pass(home_repo: None, demo: str) -> None:
    staging = "staging test of #43 before it lands"
    failed = "DONE #43 failed a case on cut 1"
    with edit(demo) as data:
        data["orders"]["o1"]["parts"][staging] = state.new_part()
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(parts=[staging], carries={})
        state.take_result(data, "j2", failed, f"{failed}\nCHECK: https://buildkite.com/forge/release/builds/16164\nCASE 1 a write lands: fail: the writer kept the old row\n")
    data = state.load(demo)
    assert watch.part_gap(watcher(demo), data, "o1", staging, state.now() + timedelta(hours=1)) == (
        f"j2's DONE is no pass of Forge-AI/monorepo#43's staging test: CASE 1 a write lands: fail: the writer kept the old row, so the part waits on a rerun: drive job add builder --order o1 --part '{staging}' --goal '<rerun it>'"
    )


def test_a_staging_parts_gap_names_only_its_testers_never_a_job_running_its_release_nor_one_whose_done_only_followed_it_even_held_for_its_own_prs_merge_while_a_tester_ran_its_cases(home_repo: None, demo: str) -> None:
    staging = "staging test of #43 before it lands"
    followed = "DONE APPLIED: radiant-tudor serves sandsql on plat-usw2-staging at 2c18705b7744"
    with edit(demo) as data:
        data["orders"]["o1"]["parts"][staging] = state.new_part()
        data["orders"]["o1"]["jobs"] += ["j2", "j3"]
        data["jobs"]["j2"] = job(parts=[staging], carries={}, brief_flags={"release": "radiant-tudor"}, builds_nothing=True)
        data["jobs"]["j3"] = job(parts=[staging], carries={}, builds_nothing=True)
        state.take_result(data, "j3", followed, f"{followed}\nCHECK: https://buildkite.com/forge/release/builds/16720\nCASE 1 a write lands: not exercised: this job only followed the release\n")
    unrun = watch.part_gap(watcher(demo), state.load(demo), "o1", staging, state.now() + timedelta(hours=1))
    failed = "DONE #43 failed a case on cut 1"
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j4")
        data["jobs"]["j4"] = job(parts=[staging], carries={})
        state.take_result(data, "j4", failed, f"{failed}\nCHECK: https://buildkite.com/forge/release/builds/16164\nCASE 1 a write lands: fail: the writer kept the old row\n")
        data["jobs"]["j3"] |= {"state": "waiting", "said": f"WAITING {state.pr_url(KEY)} to merge: {followed}"}
    add = f"drive job add builder --order o1 --part '{staging}'"
    assert [unrun, watch.part_gap(watcher(demo), state.load(demo), "o1", staging, state.now() + timedelta(hours=1))] == [
        f"j3's DONE ran no case of Forge-AI/monorepo#43's staging test, so the part waits on a run of it: {add} --goal '<run it>'",
        f"j4's DONE is no pass of Forge-AI/monorepo#43's staging test: CASE 1 a write lands: fail: the writer kept the old row, so the part waits on a rerun: {add} --goal '<rerun it>'",
    ]


def test_a_lane_its_launch_left_waiting_on_admission_opens_an_asks_blocked_step_after_ten_minutes_and_the_watch_runs_its_launch_again_once_admitted(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    detached = []
    monkeypatch.setattr(state, "detach", lambda name, args, log: detached.append(args) or 0)
    why = "30 Sprite lanes run across this Mac's drives, at its cap of 30"
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", waits=["admission:sprite"], admission={"why": why, "argv": ["job", "launch", "j1"], "at": state.stamp(T0)})
    monkeypatch.setattr(place, "admission", lambda data, job_id, where, moment: why)
    monkeypatch.setattr(state, "class_rank", lambda data, job: 0)
    assert (watch.spawn_lines(watcher(demo), T0 + timedelta(minutes=11)), detached) == ([], [])
    assert owing.waiting(state.load(demo), T0 + timedelta(minutes=11))["j1"] == ("admission:sprite", f"its lane has waited since {render.when(state.stamp(T0))} on admission:sprite: {why}")
    monkeypatch.setattr(place, "admission", lambda data, job_id, where, moment: None)
    watch.spawn_lines(watcher(demo), T0 + timedelta(minutes=12))
    job = state.load(demo)["jobs"]["j1"]
    assert (detached, job["waits"], job["admission"]) == ([["job", "launch", "j1"]], [], None)


@pytest.mark.parametrize("ran", [None, "/drive/scratch/j1/codex"], ids=["never-launched", "run-ended"])
def test_a_freed_codex_job_is_launched_by_the_watch_or_its_queue_with_no_free_line_to_wake_a_session_whether_or_not_a_codex_ask_run_ended_since_its_launch(
    demo: str, monkeypatch: pytest.MonkeyPatch, ran: str | None
) -> None:
    serve(monkeypatch, {})
    detached = []
    monkeypatch.setattr(state, "detach", lambda name, args, log: detached.append(args) or 0)
    monkeypatch.setattr(place, "admission", lambda data, job_id, where, moment: None)
    monkeypatch.setattr(place, "admitted_room", lambda where, moment, priority=False: 1)
    monkeypatch.setattr(state, "now", lambda: T0 + timedelta(seconds=60))
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j2"] = job(goal="release the service")
        data["jobs"]["j1"].update(kind="codex", codex_run=ran, brief=str(Path(demo) / "j1.md"), state="waiting", said="WAITING j2", waits=["j2"], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    with edit(demo) as data:
        data["jobs"]["j2"]["state"] = "done"
    lines = [*watch.run_pass(watching, T0 + timedelta(seconds=5)), *watch.run_pass(watching, T0 + timedelta(seconds=60))]
    assert ([line for line in lines if not line.startswith("WORK j1 ")], detached) == ([], [["job", "launch", "j1"]])


def test_a_job_whose_wait_ends_is_freed_before_any_orphan_and_said_to_be_stranded_once_a_change_to_it_settles(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    said = "WAITING release: #1 merged abc; rides the next release"
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j2"] = job(goal="release the service")
        data["jobs"]["j1"].update(state="waiting", said=said, waits=["j2"], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    with edit(demo) as data:
        data["jobs"]["j2"]["state"] = "done"
    assert watch.run_pass(watching, T0 + timedelta(seconds=5)) == []
    assert watch.run_pass(watching, T0 + timedelta(seconds=60)) == ["FREE j1 o1: j2 answered or done, nothing else holds it"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=4)) == []
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["orders"]["o2"] = order(text="build the page kit", parts={"page kit": state.new_part()}, jobs=["j2", "j3"])
        data["jobs"]["j2"] = job(order="o2", kind="codex", goal="build the page kit", parts=["page kit"], state="stopped", said="handed to j3")
        data["jobs"]["j3"] = job(order="o2", kind="codex", goal="build the page kit", parts=["page kit"])
        data["jobs"]["j1"].update(state="waiting", said="waits on j3", waits=["j3"], updated=state.stamp(T0 - timedelta(minutes=10)))
        data["jobs"]["j4"] = job(goal="build on the page kit", state="waiting", said="waits on j2", waits=["j2"])
        data["orders"]["o1"]["jobs"].append("j4")
    with edit(demo) as data:
        codex.take(demo, data, "j3", completed_run(tmp_path / "codex-ask.1", codex_reply("HANDOFF", handoff.removeprefix("HANDOFF "))))
    data = state.load(demo)
    assert (data["jobs"]["j3"]["state"], data["jobs"]["j3"]["said"]) == ("stopped", handoff)
    pointer = "REPLY_FILE: /runs/codex-ask.1/codex-r-1"
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{pointer}\n"))
    assert cli.main(["job", "report", "j3", "-", "--drive", demo]) == 0
    stopped = state.load(demo)["jobs"]["j3"]
    assert (stopped["state"], stopped["said"], state.opening(state.report_text(demo, "j3"))) == ("stopped", handoff, pointer)
    watching = watcher(demo)
    for minute in (0, 2, 4):
        assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=minute)) if line.startswith("FREE ")]
    with edit(demo) as data:
        state.start(data["jobs"]["j3"])
    assert state.load(demo)["jobs"]["j3"]["state"] == "stopped"
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=6)) if line.startswith("FREE ")]
    assert [state.load(demo)["jobs"][job_id]["waits"] for job_id in ("j1", "j4")] == [["j3"], ["j2"]]
    with edit(demo) as data:
        data["jobs"]["j5"] = job(order="o2", kind="codex", goal="build the page kit", parts=["page kit"])
        data["orders"]["o2"]["jobs"].append("j5")
        data["prs"][OTHER] = merged_other("j5", "o2")
        data["jobs"]["j5"]["prs"].append(state.pr_url(OTHER))
    with edit(demo) as data:
        codex.take(demo, data, "j5", completed_run(tmp_path / "codex-ask.2", codex_reply("DONE", f"built the page kit in {state.pr_url(OTHER)}, merged")))
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=8)) if line.startswith("FREE ")]
    with edit(demo) as data:
        state.deliver_proven(data, "j5", "j5 live: repro/j5.sh exits 0 on no plugin of this Mac")
    freed = [line for line in watch.run_pass(watching, T0 + timedelta(minutes=10)) if line.startswith("FREE ")]
    data = state.load(demo)
    assert (freed, "j2" in data["jobs"]["j4"]["waits"], work.item_of(data, data["jobs"]["j4"])) == (["FREE j1 o1: j3 answered or done, nothing else holds it"], False, work.KIND[work.BUILD])


def test_the_queue_ranks_first_the_workstream_he_moved_last_ahead_of_the_one_he_touched_longest_ago(demo: str) -> None:
    with edit(demo) as data:
        data["orders"]["o2"] = order(text="fix the card", jobs=["j10"]) | {"project": "Iris", "workstream": "Run page", "updated": state.stamp(T0 - timedelta(days=2))}
        data["orders"]["o3"] = order(text="fix the watch", jobs=["j11"]) | {"project": "Iris", "workstream": "Search", "updated": state.stamp(T0)}
        for job_id, order_id in (("j10", "o2"), ("j11", "o3")):
            data["jobs"][job_id] = job(order=order_id, session="session-under-test", created=state.stamp(T0 - timedelta(minutes=10)))
    data = state.load(demo)
    assert sorted(("j10", "j11"), key=lambda job_id: work.rank(data, job_id)) == ["j11", "j10"]


def test_a_staging_test_whose_freeze_gave_way_to_a_staging_release_for_production_is_told_once_to_rerun_its_slots_lease_starting_again(
    demo: str, monkeypatch: pytest.MonkeyPatch, freezes: list[dict]
) -> None:
    serve(monkeypatch, {})
    live_reads(monkeypatch)
    launched = launches(monkeypatch)
    build = "https://buildkite.com/forge/release/builds/16950"
    soak = "staging:api/plat-usw2-staging@044e8552b694"
    with edit(demo) as data:
        data["prs"].clear()
        data["jobs"]["j1"].update(state="waiting", said=f"WAITING {soak}", waits=[soak], prs=[])
        data["jobs"]["j2"] = job(state="waiting", said=f"WAITING {soak}", waits=[soak])
        data["jobs"]["j3"] = job(state="waiting", said="WAITING release", release_build=build)
        data["orders"]["o1"]["jobs"] += ["j2", "j3"]
        data["slots"][STAGING_ENV] = {"holder": state.leased({"job": "j1", "prs": [], "minutes": 30, "cases": None, "at": state.stamp(T0 - timedelta(minutes=20)), "paths": None, "stacks": None, "starts": False}, T0 - timedelta(minutes=20), [])}
    gave = {"id": "bypassed-0190", "kind": "bypassed", "starter": "root@example.com", "at": "2026-11-15T23:10:00Z", "picks": ["platform"], "build": "0190", "forProduction": True, "url": build}
    text = "freeze f8erwzvg6c: api/plat-usw2-staging until Nov 15, 5:00 PM for fork cases, set by Ada Lovelace. Staging runs only for its holders."
    freezes.append({"freeze": {"id": "f8erwzvg6c", "holders": [f"{demo}/j1", f"{demo}/j2", f"{demo}/j3"], "notices": [{**gave, "id": "bypassed-0189", "forProduction": False}, gave]}, "text": text})
    watching = watcher(demo)
    assert [line for line in watch.run_pass(watching, T0) if line.startswith("FREE ")] == []
    data = state.load(demo)
    leased, unleased = (watch.gave_way_text(freezes[0]["freeze"], gave, held) for held in (True, False))
    assert [[kept["words"] for kept in data["jobs"][job_id]["undelivered"]] for job_id in ("j1", "j2", "j3")] == [[leased], [unleased], []]
    assert leased.startswith(f"Release freeze f8erwzvg6c, which names you its holder on staging, gave way at `2026-11-15T23:10:00Z` to root@example.com's staging release for production of platform, {build},")
    assert f"return `WAITING {build}`, and again on a wake that finds that build stopped at an approve step, until it passed, failed or was canceled." in leased
    assert leased.endswith("lease started again at the give-way and holds while you wait on that build, so then start your cut again and run every case.")
    assert "`<ci> release unfreeze f8erwzvg6c`" in unleased and unleased.endswith("return WAITING on that build: the work queue's next cut runs every case again.")
    holder = data["slots"][STAGING_ENV]["holder"]
    assert (holder["job"], holder["since"], holder["until"], holder["extended"], holder["gave_way_to"]) == ("j1", state.stamp(T0), state.stamp(T0 + state.CLAIM_WAIT + timedelta(minutes=30)), False, build)
    assert [(data["jobs"][job_id]["waits"], data["jobs"][job_id]["gave_way"]) for job_id in ("j1", "j2", "j3")] == [([], ["f8erwzvg6c/bypassed-0190"])] * 2 + [([], [])]
    assert ["job", "launch", "j1"] not in launched and ["job", "launch", "j2"] not in launched
    with edit(demo) as data:
        for job_id in ("j1", "j2"):
            data["jobs"][job_id]["undelivered"].clear()
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=2)) if line.startswith("FREE ")]
    assert [state.load(demo)["jobs"][job_id]["undelivered"] for job_id in ("j1", "j2")] == [[], []]


def live_reads(monkeypatch: pytest.MonkeyPatch, serving: list[dict] | None = None, carried: set[str] | None = None) -> list[list[str]]:
    calls = []

    def run(argv: list[str], checkout: Path, env: dict | None = None, stdin: str | None = None) -> str:
        calls.append(argv[1:])
        return json.dumps(serving or [])

    monkeypatch.setattr(watch.reads, "run", run)
    monkeypatch.setattr(watch.orca, "checkout", lambda name, clone, wait=0: DRIVE_CHECKOUT)
    monkeypatch.setattr(watch.github, "carrier", lambda clone: lambda build, merge, number: build in (carried or set()))
    return calls


def test_a_release_wait_frees_once_every_cluster_of_its_service_serves_its_pr_never_on_staging_alone_while_an_env_wait_frees_once_its_env_does(
    home_repo: None,
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    serving = [{"target": "api", "env": env, "commit": "9f00d1"} for env in ("plat-usw2-prod", "plat-usw2-staging", "tnt-usw2-0ddq7rb")]
    calls = live_reads(monkeypatch, serving, {"abc123"})
    wait, staged = f"release:api:{KEY}", f"release:api:{KEY}@plat-usw2-staging"
    with edit(demo) as data:
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["jobs"]["j1"].update(state="waiting", said="WAITING release of api", waits=[wait], prs=[], updated=state.stamp(T0 - timedelta(minutes=10)))
        data["jobs"]["j2"] = job(state="waiting", said="WAITING staging of api", waits=[staged], updated=state.stamp(T0 - timedelta(minutes=10)))
        data["orders"]["o1"]["jobs"].append("j2")
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    serving[1]["commit"] = "abc123"
    assert watch.run_pass(watching, T0 + watch.LIVE_EVERY) == []
    assert [state.load(demo)["jobs"][job_id]["waits"] for job_id in ("j1", "j2")] == [[wait], []]
    assert watch.release_line(watching, state.load(demo), wait) == f"{wait}: api on plat-usw2-prod, tnt-usw2-0ddq7rb is behind {KEY}'s merge commit abc123, running an older build"
    serving[0]["commit"] = "abc123"
    assert [line for line in watch.run_pass(watching, T0 + 2 * watch.LIVE_EVERY) if not line.startswith("WORK ")] == []
    assert wait in state.load(demo)["jobs"]["j1"]["waits"]
    serving[2]["commit"] = "abc123"
    assert [line for line in watch.run_pass(watching, T0 + 3 * watch.LIVE_EVERY) if not line.startswith("WORK ")] == []
    data = state.load(demo)
    assert (calls, [wait in data["jobs"]["j1"]["waits"], staged in data["jobs"]["j2"]["waits"]]) == ([["release", "live", "--json"]] * 4, [False, False])


def test_a_release_wait_frees_once_every_cluster_running_its_service_production_or_not_serves_its_pr_and_at_once_while_no_cluster_or_env_runs_it(
    home_repo: None,
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    merged = "Forge-AI/monorepo#43"
    serving = [
        {"target": "iris-computer", "component": "iris", "env": "plat-usw2-staging", "commit": "2349e3617ce1"},
        {"target": "api", "component": "platform", "env": "plat-usw2-staging", "commit": "6c751b8b1015"},
        {"target": "api", "component": "platform", "env": "plat-usw2-prod", "commit": "6c751b8b1015"},
    ]
    holding = {"6c751b8b1015"}
    live_reads(monkeypatch, serving, holding)
    computer, api, smoke = (f"release:{service}:{merged}" for service in ("iris-computer", "api", "staging-smoke"))
    nowhere = f"release:api:{merged}@core-usw2-auto"
    with edit(demo) as data:
        data["prs"][merged] = state.new_pr(state.pr_url(merged), "j1", "o1") | {"state": "merged", "merged_sha": "ade22e0af2fd"}
        data["jobs"]["j1"].update(state="waiting", said="WAITING release of iris-computer", waits=[computer, api, smoke, nowhere], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    data = state.load(demo)
    assert data["jobs"]["j1"]["waits"] == [computer]
    assert [watch.release_line(watching, data, wait) for wait in (computer, api, smoke, nowhere)] == [
        f"{computer}: iris-computer on plat-usw2-staging is behind {merged}'s merge commit ade22e0af2fd, running an older build",
        f"{api}: the release live read finds api on plat-usw2-prod, plat-usw2-staging serving {merged}",
        f"{smoke}: {merged} merged, and the release live read finds staging-smoke on no cluster, so nothing follows its release",
        f"{nowhere}: {merged} merged, and the release live read finds api on no env core-usw2-auto, so nothing follows its release there",
    ]
    serving[0]["commit"] = "8f0e1d2c3b4a"
    holding.add("8f0e1d2c3b4a")
    assert watch.run_pass(watching, T0 + timedelta(minutes=4)) == []
    assert state.load(demo)["jobs"]["j1"]["waits"] == [computer]
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == [f"FREE j1 o1: the release live read finds iris-computer on plat-usw2-staging serving {merged}, nothing else holds it"]


def test_a_staging_wait_frees_once_release_live_reads_its_env_running_its_target_at_its_soak_commit_and_never_on_another_envs_row(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    serving = [{"target": "sandsql", "env": "plat-usw2-staging", "commit": "6c751b8b1015"}, {"target": "sandsql", "env": "plat-usw2-prod", "commit": "044e8552b694"}]
    live_reads(monkeypatch, serving)
    wait = "staging:sandsql/plat-usw2-staging@044e8552b694"
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"WAITING {wait}", waits=[wait], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    serving[0]["commit"] = "044e8552b6941c3e"
    assert watch.run_pass(watching, T0 + timedelta(minutes=4)) == []
    assert state.load(demo)["jobs"]["j1"]["waits"] == [wait]
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == [
        "FREE j1 o1: the release live read finds plat-usw2-staging's sandsql serving 044e8552b694 or a build carrying it, nothing else holds it"
    ]
    assert state.load(demo)["jobs"]["j1"]["waits"] == []


def test_a_staging_wait_on_a_merge_commit_frees_once_its_env_serves_a_later_build_carrying_that_merge_and_holds_while_it_serves_one_that_does_not(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    carried = {"4368afde1574"}
    live_reads(monkeypatch, [{"target": "api", "env": "plat-usw2-staging", "commit": "4f2c41333799"}], carried)
    wait = "staging:api/plat-usw2-staging@8c186ae8e2dd1d8b24346ac4633db9000e822448"
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"WAITING {wait}", waits=[wait], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert watch.run_pass(watching, T0) == []
    assert state.load(demo)["jobs"]["j1"]["waits"] == [wait]
    carried.add("4f2c41333799")
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == [
        "FREE j1 o1: the release live read finds plat-usw2-staging's api serving 8c186ae8e2dd or a build carrying it, nothing else holds it"
    ]


def test_a_release_wait_on_a_pr_outside_the_monorepo_is_never_read_and_frees_at_its_merge_naming_why(home_repo: None, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    calls = live_reads(monkeypatch)
    waited = "yasyf/cc-slack#355"
    wait = f"release:cc-slack:{waited}"
    with edit(demo) as data:
        data["prs"][waited] = state.new_pr(state.pr_url(waited), "j1", "o1") | {"state": "merged", "merged_sha": "c355"}
        data["jobs"]["j1"].update(state="waiting", said="WAITING release of cc-slack", waits=[wait], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    unmerged = state.load(demo)
    unmerged["prs"][waited]["merged_sha"] = None
    assert watch.out_because(watching, unmerged, wait) is None
    assert watch.run_pass(watching, T0) == [f"FREE j1 o1: {waited} merged, and the release live read finds only Forge-AI/monorepo, so nothing follows its release, nothing else holds it"]
    assert (calls, state.load(demo)["jobs"]["j1"]["waits"]) == ([], [])


RELEASE_BUILD = "https://buildkite.com/forge/release/builds/14769"
NO_BUILD = "https://buildkite.com/forge/release/builds/99999999"
BUILD_RUNNING = {"state": "RUNNING", "blocks": {"edges": []}, "failed": {"edges": []}}
APPROVE_STEP = ":kubernetes: Approve starting the cluster job this build names"


def buildkite_reads(monkeypatch: pytest.MonkeyPatch, answers: dict[str, dict | None]) -> list[list[str]]:
    calls = []

    def builds(self: providers.BuildProvider, links: list[str], fields: str) -> dict[str, dict | None]:
        slugs = [f"{found['pipeline']}/{found['number']}" for found in map(self.build_url.fullmatch, links)]
        calls.append(slugs)
        return {link: answers[slug] for link, slug in zip(links, slugs, strict=True)}

    monkeypatch.setattr(HomeBuild, "builds", builds)
    monkeypatch.setattr(watch.orca, "checkout", lambda name, clone, wait=0: DRIVE_CHECKOUT)
    return calls


def heard_from_buildkite() -> None:
    receiver.home().mkdir(parents=True, exist_ok=True)
    receiver.took_builds("ping", [])


LAUNCH_BUILD = "https://buildkite.com/forge/release/builds/17468"
TRIGGERED_BUILD = "https://buildkite.com/forge/release/builds/17469"
DEPLOY_STEP = ":pulumi: Deploy the service"
CAUSE = "The service's deploy: too few of its pods became ready before it timed out."


def test_a_release_job_follows_the_build_its_launch_triggered_and_it_and_the_coordinator_are_told_once_in_one_record_when_that_build_fails(
    home_repo: None,
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = {"forge/release/17468": {**BUILD_RUNNING, "state": "PASSED"}, "forge/release/17469": BUILD_RUNNING}
    builds: list[list[str]] = []
    posts: list[tuple[list[str], str | None]] = []

    def run(argv: list[str], checkout: Path, env: dict | None = None, stdin: str | None = None) -> str:
        if argv[0] == "cci":
            posts.append((argv, stdin))
            return json.dumps({"seq": 731000})
        slugs = re.findall(r'build\(slug: "([^"]+)"\)', argv[3])
        builds.append(slugs)
        return json.dumps({f"b{at}": answers[slug] for at, slug in enumerate(slugs)})

    monkeypatch.setattr(watch.reads, "run", run)
    monkeypatch.setattr(watch.orca, "checkout", lambda name, clone, wait=0: DRIVE_CHECKOUT)
    failure = {"step": DEPLOY_STEP, "said": CAUSE, "job": f"{TRIGGERED_BUILD}#01a1"}
    records = {LAUNCH_BUILD: {"name": "auric-aristides", "build": TRIGGERED_BUILD}, TRIGGERED_BUILD: {"name": "auric-aristides", "build": TRIGGERED_BUILD, "failures": [failure]}}
    monkeypatch.setattr(watch.brief, "release_record", lambda release, data: records[release])
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = state.new_job("o1", "builder", "follow the release", [], state.stamp(T0)) | {"brief_flags": RELEASED, "release_build": LAUNCH_BUILD, "waits": [LAUNCH_BUILD]}
    watching = watcher(demo)
    monkeypatch.setattr(watch.brief, "release_record", lambda release, data: None)
    assert watch.build_lines(watching, T0) == []
    assert (watch.release_end_lines(watching, T0), state.load(demo)["jobs"]["j2"]["release_build"], posts) == ([], LAUNCH_BUILD, [])
    monkeypatch.setattr(watch.brief, "release_record", lambda release, data: records[release])
    for minute in (1, 2):
        assert watch.build_lines(watching, T0 + timedelta(minutes=minute)) == []
        assert watch.release_end_lines(watching, T0 + timedelta(minutes=minute)) == []
    assert (state.load(demo)["jobs"]["j2"]["release_build"], state.load(demo)["jobs"]["j2"]["waits"], posts) == (TRIGGERED_BUILD, [TRIGGERED_BUILD], [])
    answers["forge/release/17469"] = {**BUILD_RUNNING, "state": "FAILED", "failed": {"edges": [{"node": {"label": DEPLOY_STEP, "retried": False}}]}}
    for minute in (3, 4):
        assert watch.build_lines(watching, T0 + timedelta(minutes=minute)) == []
        assert watch.release_end_lines(watching, T0 + timedelta(minutes=minute)) == []
    text = f'RELEASE j2 o1: release auric-aristides {TRIGGERED_BUILD} failed at "{DEPLOY_STEP}": {CAUSE} {TRIGGERED_BUILD}#01a1'
    [(argv, body)] = posts
    assert [argv[at + 1] for at, arg in enumerate(argv) if arg == "--to"] == ["j2@demo", "root@demo"]
    assert (argv[argv.index("--kind") + 1], argv[argv.index("--id") + 1], argv[-1]) == ("handoff", f"release-{TRIGGERED_BUILD}-FAILED", text)
    assert body == f'"{DEPLOY_STEP}": {CAUSE} {TRIGGERED_BUILD}#01a1\n{TRIGGERED_BUILD} reads FAILED, its steps that did not pass "{DEPLOY_STEP}"'
    job = state.load(demo)["jobs"]["j2"]
    assert (job["release_heard"]["told"], [held["seq"] for held in job["undelivered"]]) == ("731000", ["731000"])
    assert builds == [["forge/release/17468"], ["forge/release/17468"], ["forge/release/17469"], ["forge/release/17469"], ["forge/release/17469"]]


def test_a_release_build_stopped_at_its_approve_step_is_told_once_it_has_stood_there_half_an_hour(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = {**BUILD_RUNNING, "state": "BLOCKED", "blocks": {"edges": [{"node": {"label": APPROVE_STEP}}]}}
    buildkite_reads(monkeypatch, {"forge/release/17469": blocked})
    told: list[tuple[str, str]] = []
    monkeypatch.setattr(watch, "release_told", lambda watcher, job_id, read, moment: told.append((job_id, read["state"])))
    with edit(demo) as data:
        data["jobs"]["j2"] = state.new_job("o1", "builder", "follow the release", [], state.stamp(T0)) | {"brief_flags": RELEASED, "release_build": TRIGGERED_BUILD}
    watching = watcher(demo)
    for minute in (0, 29, 30):
        watch.build_lines(watching, T0 + timedelta(minutes=minute))
        watch.release_end_lines(watching, T0 + timedelta(minutes=minute))
    assert (told, state.load(demo)["jobs"]["j2"]["release_heard"]["since"]) == ([("j2", "BLOCKED")], state.stamp(T0))


def test_an_open_incidents_release_stopped_at_its_approve_step_is_told_and_posted_in_its_thread_the_pass_the_watch_reads_it(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = {**BUILD_RUNNING, "state": "BLOCKED", "blocks": {"edges": [{"node": {"label": APPROVE_STEP}}]}}
    buildkite_reads(monkeypatch, {"forge/release/17469": blocked})
    monkeypatch.setattr(watch.brief, "release_record", lambda release, data: {"name": "luminous-awoonor"})
    monkeypatch.setattr(watch, "release_post", lambda name, data, job_id, text, body, record_id, kept=None, topic="release": "731000")
    lines: list[dict] = []
    monkeypatch.setattr(talk, "posted", lambda name, post, order=None: lines.append(post) or {"permalink": "https://poetic-technology.slack.com/archives/C0INC/p1794800000000200"})
    monkeypatch.setattr(slack, "homes", lambda data: {"C0INC"})
    with edit(demo) as data:
        data["orders"]["o1"] |= {"incident": True, "thread": ["C0INC", "1794800000.000100"]}
        data["jobs"]["j2"] = state.new_job("o1", "builder", "follow the release", [], state.stamp(T0)) | {"brief_flags": RELEASED, "release_build": TRIGGERED_BUILD}
    watching = watcher(demo)
    watch.build_lines(watching, T0)
    watch.release_end_lines(watching, T0)
    [line] = lines
    assert (line["channel_id"], line["thread_ts"]) == ("C0INC", "1794800000.000100")
    assert f"[luminous-awoonor]({TRIGGERED_BUILD}) stopped 0 min at its approve step" in line["text"]
    assert "{order:o1} is not live yet" in line["text"]


def test_730544_a_release_job_whose_waits_moved_on_from_its_release_build_is_never_told_of_that_build_and_one_still_waiting_on_it_is(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = buildkite_reads(monkeypatch, {"forge/release/17468": {**BUILD_RUNNING, "state": "PASSED"}, "forge/release/17469": {**BUILD_RUNNING, "state": "PASSED"}})
    told: list[tuple[str, str]] = []
    monkeypatch.setattr(watch, "release_told", lambda watcher, job_id, read, moment: told.append((job_id, read["state"])))
    staging = "staging:llm-gateway/plat-usw2-staging@950c935749448e741572047cc4feb173863255ab"
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"] += ["j2", "j3"]
        data["jobs"]["j2"] = state.new_job("o1", "builder", "follow the release", [], state.stamp(T0)) | {"brief_flags": RELEASED, "release_build": LAUNCH_BUILD, "waits": [staging]}
        data["jobs"]["j3"] = state.new_job("o1", "builder", "follow the release", [], state.stamp(T0)) | {"brief_flags": RELEASED, "release_build": TRIGGERED_BUILD, "waits": [TRIGGERED_BUILD, staging]}
    watching = watcher(demo)
    assert (watch.build_lines(watching, T0), watch.release_end_lines(watching, T0)) == ([], [])
    assert (told, calls, state.load(demo)["jobs"]["j2"]["release_heard"]) == ([("j3", "PASSED")], [["forge/release/17469"]], None)


APPROVE_CARD = "https://poetic-technology.slack.com/archives/C0PRIO/p1794800000000100"


class ApprovingRelease(LiveRelease):
    approve_command = "ci release approve {release}"
    show_command = "ci release status {release}"


@pytest.fixture
def approving(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every drive's providers the monorepo_providers ones with a release provider naming approve and show commands, so a build stopped at its approve step draws his card."""

    def built(clone: Path) -> providers.Providers:
        roles = HOME | {"repo": MonorepoPaths, "release": ApprovingRelease, "staging": SlotStaging}
        return providers.Providers(None, {}, **{role: made(clone, clone, {}) for role, made in roles.items()})

    monkeypatch.setattr(providers, "of", lambda data: built(Path(next(iter(state.bound_sessions(data).values()))["cwd"])))
    monkeypatch.setattr(providers, "at", lambda clone, checkout=None: built(clone))


def test_a_build_a_job_waits_on_stopped_at_an_approve_step_puts_one_approve_card_up_in_its_orders_channel_at_the_first_read_and_withdraws_it_once_the_build_moves_on_untapped(
    demo: str, monkeypatch: pytest.MonkeyPatch, approving: None
) -> None:
    answers = {"forge/release/17469": {**BUILD_RUNNING, "state": "BLOCKED", "blocks": {"edges": [{"node": {"label": APPROVE_STEP}}]}}}
    buildkite_reads(monkeypatch, answers)
    record = {"name": "auric-aristides", "build": TRIGGERED_BUILD, "migrations": ["0531 adds the runs index"], "replaces": [], "deletes": ["the old blobs bucket"]}
    monkeypatch.setattr(watch.brief, "release_record", lambda release, data: record)
    posts: list[dict] = []
    monkeypatch.setattr(talk, "posted", lambda name, post, order=None, receipt=None: posts.append(post) or {"permalink": APPROVE_CARD})
    monkeypatch.setattr(watch, "release_card_channel", lambda name, data, job: "C0PRIO")
    monkeypatch.setattr(watch.slack, "dm", lambda: "D0DM")
    answered: list[tuple] = []
    monkeypatch.setattr(superseded, "answer_card", lambda card, words, link, settled, replaces=None: answered.append((card, words, link, settled, replaces)) or {})
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = state.new_job("o1", "builder", "follow his release", [], state.stamp(T0)) | {"waits": [TRIGGERED_BUILD]}
    watching = watcher(demo)
    said = []
    for minute in (0, 1):
        watch.build_lines(watching, T0 + timedelta(minutes=minute))
        said.append(watch.release_card_lines(watching, T0 + timedelta(minutes=minute)))
    assert said == [[f'CARD {APPROVE_CARD} asks his Approve on release auric-aristides {TRIGGERED_BUILD} at its approve step "{APPROVE_STEP}"'], []]
    [post] = posts
    drawn = post["card"]
    assert (post["channel_id"], drawn["options"], drawn["recommended"], drawn["waits"].startswith(f"{demo}:j2-")) == ("C0PRIO", ["Approve", "Hold"], "Approve", True)
    assert ["- Migrations: 0531 adds the runs index", "- Replaces: none", "- Deletes: the old blobs bucket", "`ci release status auric-aristides` shows the whole release."] == drawn["body"].splitlines()[-4:]
    assert "`ci release approve auric-aristides`" in drawn["hints"]["Approve"]
    answers["forge/release/17469"] = BUILD_RUNNING
    for minute in (2, 3):
        watch.build_lines(watching, T0 + timedelta(minutes=minute))
        watch.release_card_lines(watching, T0 + timedelta(minutes=minute))
    assert answered == [(APPROVE_CARD, f'Withdrawn: the build moved past "{APPROVE_STEP}" with no tap here, and reads running', TRIGGERED_BUILD, False, None)]
    assert len(posts) == 1


def test_his_approve_on_a_release_card_runs_the_repositorys_approve_command_once_tells_the_job_and_shows_its_failure_in_place_of_his_tap(demo: str, monkeypatch: pytest.MonkeyPatch, approving: None) -> None:
    waits = f"{demo}:j2-1794800000000"
    entry = {"build": TRIGGERED_BUILD, "step": APPROVE_STEP, "release": "auric-aristides", "covers": None, "waits": waits, "url": APPROVE_CARD, "asked": state.stamp(T0), "tap": None, "result": None, "shown": None}
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = state.new_job("o1", "builder", "follow his release", [], state.stamp(T0)) | {"waits": [TRIGGERED_BUILD], "release_cards": {f"{TRIGGERED_BUILD} {APPROVE_STEP}": entry}}
    ran: list[str] = []
    monkeypatch.setattr(card.subprocess, "run", lambda command, **kwargs: ran.append(command) or subprocess.CompletedProcess(command, 1, "", "approve: listed authors still owe acks: ada\n"))
    told: list[tuple[str, str]] = []
    monkeypatch.setattr(watch, "release_post", lambda name, data, job_id, text, body, record_id, kept=None: told.append((job_id, record_id)) or "731900")
    tap = {"waits": waits, "label": "Approve", "url": APPROVE_CARD, "action_ts": "1794800100.000100", "source": "slack"}
    failure = "Approve failed: approve: listed authors still owe acks: ada"
    assert card.question_answered(tap) == {"ok": True, "seq": "731900"}
    assert card.question_answered(tap) == {"ok": True, "result": failure}
    assert card.question_answered(tap | {"undone": True})["ok"] is False
    assert (ran, told) == (["ci release approve auric-aristides"], [("j2", f"release-tap-{waits}")])
    answered: list[tuple] = []
    monkeypatch.setattr(superseded, "answer_card", lambda card, words, link, settled, replaces=None: answered.append((card, words, link, settled, replaces)) or {})
    watching = watcher(demo)
    assert watch.release_card_lines(watching, T0) == [f"CARD {APPROVE_CARD} closed in place: {failure}"]
    assert watch.release_card_lines(watching, T0) == []
    assert answered == [(APPROVE_CARD, failure, TRIGGERED_BUILD, True, "1794800100.000100")]


def test_a_buildkite_build_wait_is_read_once_as_first_held_then_only_on_a_delivery_naming_it_holding_while_it_runs_and_freeing_at_its_approve_step(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    answers = {"forge/release/14769": BUILD_RUNNING, "forge/release/99999999": None}
    calls = buildkite_reads(monkeypatch, answers)
    heard_from_buildkite()
    with edit(demo) as data:
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["orders"]["o1"]["jobs"] = ["j1", "j2"]
        data["jobs"]["j1"].update(state="waiting", said=f"WAITING {RELEASE_BUILD} stops at its approve step", waits=[RELEASE_BUILD], prs=[], updated=state.stamp(T0 - timedelta(minutes=10)))
        data["jobs"]["j2"] = job(state="waiting", said=f"WAITING {NO_BUILD}", waits=[NO_BUILD], updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watcher(demo)
    assert [watch.run_pass(watching, T0 + timedelta(minutes=minute)) for minute in range(3)] == [[], ["WORK j3 records a build worker on a Sprite, its first group claimed"], []]
    assert state.load(demo)["jobs"]["j2"]["claim"]["worker"] == "j3"
    assert stall.chains(state.load(demo), NO_CARDS, False, T0)["j1"] == [("j1", f"j1 waiting on {RELEASE_BUILD}", False), (RELEASE_BUILD, f"Buildkite build 14769 of forge/release {RELEASE_BUILD}", True)]
    answers["forge/release/14769"] = {
        "state": "BLOCKED",
        "blocks": {"edges": [{"node": {"label": APPROVE_STEP}}]},
        "failed": {"edges": [{"node": {"label": ":pytest: Test", "retried": True}}, {"node": {"label": ":docker: Image", "retried": False}}]},
    }
    receiver.home().mkdir(parents=True, exist_ok=True)
    receiver.took_builds("build.finished", [RELEASE_BUILD])
    assert [line for minute in (3, 4) for line in watch.run_pass(watching, T0 + timedelta(minutes=minute)) if not line.startswith("WORK ")] == []
    assert (calls, RELEASE_BUILD in state.load(demo)["jobs"]["j1"]["waits"], watching.builds) == ([["forge/release/14769", "forge/release/99999999"], ["forge/release/14769"]], False, {})


def test_a_built_job_is_told_once_under_each_card_it_waits_on_and_freed_once_when_he_answers_the_last(
    demo: str, monkeypatch: pytest.MonkeyPatch, told: list[dict]
) -> None:
    serve(monkeypatch, {})
    other = "https://acme.slack.com/archives/D123/p1794200000000300"
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question("demo:j1-7-a"), question("demo:j1-7-b") | {"url": other}], "closed": [], "pr_cards": []})
    watch.view.refresh_cards(demo)
    with edit(demo) as data:
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
    with edit(demo) as data:
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
    assert watch.run_pass(watcher(demo), T0) == [*waiting("demo:j1-7-a"), moved("o1", "root", "iris", "j1")]
    assert (posts, nested) == ([paused()], [[]])


def test_a_built_job_whose_watch_died_after_claiming_its_card_and_before_posting_is_told_by_the_next_watch(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    asking(monkeypatch, demo, "demo:j1-7-a")
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"] |= {"state": "built", "branch": "lane/built", "waits": ["demo:j1-7-a"]}
    posts: list[dict] = []

    def post(name: str, said: dict) -> dict:
        posts.append(said)
        if len(posts) == 1:
            raise SystemExit("the watch was killed")
        return {"permalink": POSTED}

    monkeypatch.setattr(talk, "posted", post)
    gone = {"pid": 999999999, "started": "Mon Nov  9 08:00:00 2026"}
    with monkeypatch.context() as dying, pytest.raises(SystemExit):
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
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"] |= {"state": "built", "branch": "lane/built", "waits": ["demo:j1-7-a"], "waiting_claims": {"demo:j1-7-a": {"pid": os.getpid(), "started": "Mon Nov  9 08:00:00 2026"}}}
    assert watch.run_pass(watcher(demo), T0) == waiting("demo:j1-7-a")
    assert told == [paused()]
    with edit(demo) as data:
        data["jobs"]["j1"] |= {"waiting_said": [], "waiting_claims": {"demo:j1-7-a": {"pid": os.getpid(), "started": watch.started(os.getpid())}}}
    assert watch.run_pass(watcher(demo), T0 + timedelta(minutes=1)) == []
    assert told == [paused()]


def test_a_built_job_asking_him_its_own_question_is_freed_only_once_he_answers_it_too(demo: str, monkeypatch: pytest.MonkeyPatch, told: list[dict]) -> None:
    serve(monkeypatch, {})
    asked = f"{CARD[:-1]}9"
    own = question("demo:j1-9") | {"url": asked}
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question("demo:j1-7-a"), own], "closed": [], "pr_cards": []})
    watch.view.refresh_cards(demo)
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("answered", "Yes", asked, seq="9")
    assert watch.run_pass(watching, T0 + timedelta(minutes=3)) == ["FREE j1 o1: demo:j1-7-a answered or done, nothing else holds it, built on lane/built"]


def test_a_card_holding_a_built_job_is_told_once_in_its_own_thread_and_again_after_a_failed_post(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    asking(monkeypatch, demo, "demo:j1-7-a")
    with edit(demo) as data:
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
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["jobs"]["j1"] |= {"state": "built", "branch": "lane/built", "waits": [link, "demo:j1-7-a"]}
    assert watch.run_pass(watcher(demo), T0) == waiting("demo:j1-7-a")
    with edit(demo) as data:
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


def carded(name: str, *shown: dict, closed: tuple[dict, ...] = ()) -> watch.Watcher:
    """A watch of the drive that read his PR cards as cc-slack lists them."""
    watching = watcher(name)
    watching.cards = NO_CARDS | {"pr_cards": list(shown), "closed": list(closed)}
    return watching


def test_a_wait_added_to_a_pr_he_approved_never_withdraws_or_redraws_his_card_and_a_fresh_tap_draws_its_card_while_it_holds(
    demo: str, withdrawals: Withdrawals, redraws: Redraws
) -> None:
    with edit(demo) as data:
        data["prs"][KEY]["tap"] = {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack", "picks": {}, "card": None}
    watching = carded(demo, drawn_pr_card(demo, KEY))
    assert (watch.redraw_lines(watching, T0), redraws.asked, redraws.drawn) == ([], [], [])
    assert cli.main(["pr", "set", KEY, "--waits", "j1", "--drive", demo]) == 0
    assert (watch.redraw_lines(watching, T0), withdrawals.asked, redraws.asked, redraws.drawn) == ([], [], [], [])
    assert cli.main(["pr", "set", KEY, "--tap", "--serves", "o1", "--design", "none", "--drive", demo]) == 0
    assert state.load(demo)["prs"][KEY]["wants_tap"] is True


def test_his_pr_card_whose_row_went_stale_has_its_lines_drawn_in_place_once_with_no_whole_redraw(demo: str, redraws: Redraws) -> None:
    stale = pr_card(KEY)
    stale["card"]["prs"][0] |= card.drawn_lines(state.load(demo), KEY, view.live_cards(demo)) | {"head": "0000000aaa"}
    watching = carded(demo, stale)
    assert (watch.redraw_lines(watching, T0), redraws.asked, redraws.drawn) == ([], [], [state.pr_url(KEY)])
    assert redraws.rows[state.pr_url(KEY)] == card.drawn_lines(state.load(demo), KEY, view.live_cards(demo))
    assert (watch.redraw_lines(watching, T0 + timedelta(minutes=1)), redraws.asked, redraws.drawn) == ([], [], [state.pr_url(KEY)])


def test_the_redraw_of_his_stale_pr_cards_reads_the_drive_and_his_cards_as_often_for_100_prs_as_for_one(demo: str, monkeypatch: pytest.MonkeyPatch, redraws: Redraws) -> None:
    reads: list[str] = []
    load, stored = state.load, view.stored_cards
    monkeypatch.setattr(state, "load", lambda name: reads.append("drive") or load(name))
    monkeypatch.setattr(view, "stored_cards", lambda name: reads.append("cards") or stored(name))

    def redrawn(count: int) -> list[str]:
        keys = [f"Forge-AI/monorepo#{1000 + number}" for number in range(count)]
        with edit(demo) as data:
            for key in keys:
                data["prs"][key] = data["prs"][KEY] | {"url": state.pr_url(key)}
        drawn = len(redraws.drawn)
        reads.clear()
        assert watch.redraw_lines(carded(demo, pr_card(*keys)), T0) == []
        assert redraws.drawn[drawn:] == [state.pr_url(key) for key in keys]
        return sorted(reads)

    assert redrawn(1) == redrawn(100)


def test_his_open_card_drawn_asking_his_tap_on_a_pr_that_no_longer_asks_it_is_redrawn_whole_once_until_his_cards_are_read_again_and_asked_again_while_that_fails(demo: str, redraws: Redraws) -> None:
    with edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
    asked = drawn_pr_card(demo, KEY)
    with edit(demo) as data:
        data["prs"][KEY]["tap"] = {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack", "picks": {}, "card": None}
    watching = carded(demo, asked)
    redraws.failure = "cc-slack exited 1"
    assert (asked["card"]["waits"].startswith("merge:42:"), watch.redraw_lines(watching, T0), redraws.asked, redraws.drawn) == (True, [], [state.pr_url(KEY)], [])
    redraws.failure = None
    assert (watch.redraw_lines(watching, T0), redraws.asked, redraws.drawn) == ([], [state.pr_url(KEY)] * 2, [])
    assert (watch.redraw_lines(watching, T0 + timedelta(minutes=1)), redraws.asked) == ([], [state.pr_url(KEY)] * 2)


def test_his_answered_pr_card_cc_slack_still_draws_is_redrawn_in_place_but_never_one_it_replaced_withdrew_or_whose_pr_ended(demo: str, redraws: Redraws) -> None:
    answered = {"waits": f"merge:42:{state.ask_hash('aaaaaaa111')}", "url": CARD, "prs": [state.pr_url(KEY)], "answer": {"label": "Approve"}, "replaced_by": "", "card": pr_card(KEY)["card"]}
    retired = {field: value for field, value in answered.items() if field != "card"}
    assert (watch.redraw_lines(carded(demo, closed=(retired,)), T0), redraws.drawn) == ([], [])
    assert (watch.redraw_lines(carded(demo, closed=(answered,)), T0), redraws.asked, redraws.drawn) == ([], [], [state.pr_url(KEY)])
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
    assert (watch.redraw_lines(carded(demo, closed=(answered,)), T0), redraws.drawn) == ([], [state.pr_url(KEY)])


def test_a_failed_redraw_of_his_stale_pr_card_is_tried_again_on_the_next_pass_and_said_from_its_third_failure(demo: str, redraws: Redraws) -> None:
    watching = carded(demo, pr_card(KEY))
    redraws.failure = "cc-slack exited 1"
    said = [watch.redraw_lines(watching, T0 + timedelta(minutes=minute)) for minute in range(3)]
    assert (said[:2], redraws.asked, len(redraws.drawn)) == ([[], []], [], 3)
    assert said[2] == [f"WATCH redraw of his card on {KEY} failed 3 times in a row: cc-slack exited 1"]


def test_a_prs_release_wait_whose_release_is_already_out_is_stored_and_freed_on_the_next_read(home_repo: None, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    shipped = "Forge-AI/monorepo#43"
    with edit(demo) as data:
        data["prs"][shipped] = state.new_pr(state.pr_url(shipped), "j1", "o1") | {"state": "merged", "merged_sha": "abc123"}
    serving = [{"target": "dashboard", "env": "plat-usw2-prod", "commit": "abc123"}, {"target": "api", "env": "plat-usw2-prod", "commit": "abc123"}, {"target": "api", "env": "plat-usw2-staging", "commit": "9f00d1"}]
    live_reads(monkeypatch, serving, {"abc123"})
    staged = f"release:api:{shipped}@plat-usw2-staging"
    assert cli.main(["pr", "set", KEY, "--waits", f"release:dashboard:{shipped}", "--waits", staged, "--drive", demo]) == 0
    assert state.load(demo)["prs"][KEY]["waits"] == [f"release:dashboard:{shipped}", staged]
    watching = watcher(demo)
    assert (watch.serving_lines(watching, T0), watch.waits_lines(watching, T0)) == ([], [])
    assert state.load(demo)["prs"][KEY]["waits"] == [staged]


def test_job_held_holds_no_jobs_pr_on_his_open_cards(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asking(monkeypatch, demo, "demo:j1-7-a")
    with edit(demo) as data:
        data["jobs"]["j1"]["questions"] = [state.new_question(None, "Pick demo:j1-7-a?", state.stamp(T0), holds=None) | {"waits": "demo:j1-7-a"}]
        state.take_result(data, "j1", "BUILT lane/built, waits on demo:j1-7-a")
        data["prs"].pop(KEY, None)
        data["jobs"]["j1"]["prs"] = []
    assert render.job_line(state.load(demo), "j1", state.load(demo)["jobs"]["j1"]) == "j1 builder built on lane/built: watch; waits on demo:j1-7-a"
    capsys.readouterr()
    assert cli.main(["job", "held", "j1", "--drive", demo]) == 0
    assert capsys.readouterr().out == "j1: nothing holds its PR\n"
    monkeypatch.setenv(state.OWN_JOB_ENV, "j1")
    assert cli.main(["job", "held", "--json", "--drive", demo]) == 0
    assert json.loads(capsys.readouterr().out) == {"id": "j1", "main": ""}


def test_a_job_on_an_open_incident_puts_up_its_pr_on_the_recommendation_and_the_pr_takes_no_wait_on_his_card(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asking(monkeypatch, demo, "demo:j1-7-a")
    url = "https://github.com/Forge-AI/monorepo/pull/43"
    with edit(demo) as data:
        data["orders"]["o1"]["incident"] = True
        state.take_result(data, "j1", "BUILT lane/built, waits on demo:j1-7-a")
    assert cli.main(["pr", "add", url, "--job", "j1", "--drive", demo, *LEAD]) == 0
    assert cli.main(["pr", "set", url, "--waits", "demo:j1-7-a", "--drive", demo]) == 2
    assert "--waits demo:j1-7-a: refused, nothing set: a card of his holds a PR from landing only by its question's Holds: line" in capsys.readouterr().err
    assert cli.main(["pr", "set", url, "--waits", "j1", "--drive", demo]) == 0


def test_a_job_on_an_open_incident_is_held_from_landing_only_by_a_card_asking_him_to_lift_a_ruling_of_his(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asking(monkeypatch, demo, "demo:j1-7-a")
    with edit(demo) as data:
        data["orders"]["o1"]["incident"] = True
        state.take_result(data, "j1", "BUILT lane/built, waits on demo:j1-7-a")
        data["prs"].pop(KEY, None)
        data["jobs"]["j1"]["prs"] = []
        data["jobs"]["j1"]["questions"] = [state.new_question(None, "Pick demo:j1-7-a?", state.stamp(T0), holds=None) | {"waits": "demo:j1-7-a"}]
    assert land.open_cards(state.load(demo), "j1", None) == []
    with edit(demo) as data:
        data["jobs"]["j1"]["questions"][0]["unsettled"] = "9992100: this card asks whether the incident overrides his wait"
    assert land.open_cards(state.load(demo), "j1", None) == ["demo:j1-7-a"]


def test_job_set_moves_a_builder_off_a_card_his_words_replaced_onto_a_reader_that_just_started_and_refuses_only_a_finished_job(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asking(monkeypatch, demo, "demo:j1-7-a")
    with edit(demo) as data:
        data["jobs"]["j2"] = state.new_job("o1", "builder", "build his pick on the card", [], state.stamp(T0)) | {"waits": ["demo:j1-7-a"]}
        data["jobs"]["j3"] = job(kind="reader", goal="research: the audit his later words asked for", agent="a1")
        data["jobs"]["j4"] = job(kind="design", goal="asked him", state="waiting", questions=[state.new_question(None, "Pick?", state.stamp(T0)) | {"waits": "demo:j1-7-a"}])
        data["jobs"]["j5"] = job(kind="reader", goal="research: done", state="done")
    assert cli.main(["job", "set", "j2", "--waits", "j3", "--drive", demo]) == 0
    added = state.load(demo)["jobs"]["j2"]
    assert (added["waits"], added["said"]) == (["demo:j1-7-a", "j3"], state.NOT_STARTED)
    assert cli.main(["job", "set", "j2", "--no-waits", "--waits", "j3", "--drive", demo]) == 0
    moved = state.load(demo)["jobs"]["j2"]
    assert (moved["state"], moved["waits"], moved["said"]) == ("waiting", ["j3"], state.NOT_STARTED)
    assert cli.main(["job", "set", "j2", "--waits", "j4", "--drive", demo]) == 0
    assert state.load(demo)["jobs"]["j2"]["waits"] == ["j3", "j4"]
    assert cli.main(["job", "set", "j2", "--waits", "j5", "--drive", demo]) == 2
    assert capsys.readouterr().err.strip().endswith("--waits j5: j5 is done, so nothing of it is left to wait on")
    assert cli.main(["job", "set", "j2", "--waits", "j1", "--drive", demo]) == 0
    assert state.load(demo)["jobs"]["j2"]["waits"] == ["j3", "j4", "j1"]


def test_a_pr_whose_head_moved_to_a_change_nobody_judged_gets_its_card_and_its_check_against_his_words_at_that_head_whatever_its_jobs_state(
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str], draws: Draws
) -> None:
    with edit(demo) as data:
        data["prs"][KEY] |= {"wants_tap": True, "checked": CHECKED | {"lines": {"api/run.ts": "a change judged before"}}}
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert (draws.keys, checkers) == ([[KEY]], [KEY])
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=30)) if line.startswith(("PR ", "STAGE "))]
    assert (state.load(demo)["prs"][KEY]["checked"]["read"], state.load(demo)["prs"][KEY]["stage"]["name"]) == ("bbbbbbb222", "card")
    with edit(demo) as data:
        data["prs"][KEY]["checked"] = CHECKED | {"head": "bbbbbbb222"}
        data["jobs"]["j1"]["carries"]["o2"] = ["no staff flag"]
    assert not state.checked(state.load(demo), state.load(demo)["prs"][KEY])


def test_a_pr_whose_card_draw_fails_stays_uncarded_and_is_drawn_again_each_read_said_to_the_meta_session_from_the_third_failure(
    demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws
) -> None:
    with edit(demo) as data:
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
    data = state.load(demo)
    pr = data["prs"][KEY]
    assert (len(draws.keys), view.tap_card(data, pr, view.live_cards(demo)), pr["stage"]["name"]) == (4, draws.link, "card")


def test_a_pr_whose_card_draw_goes_out_with_no_open_card_at_its_head_is_a_failed_draw_drawn_again_each_read_said_to_the_meta_session_from_the_third(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    sent = posts(monkeypatch)
    link = "https://forge.slack.com/archives/D123/p1794379200123456"
    drawn: list[list[str]] = []
    monkeypatch.setattr(talk, "draw", lambda name, keys: drawn.append(keys) or link)
    watching = watcher(demo)
    for minute in range(3):
        watch.run_pass(watching, T0 + timedelta(minutes=minute))
    assert (drawn, stage.of(state.load(demo), KEY, view.live_cards(demo)).name) == ([[KEY]] * 3, "uncarded")
    assert [entry for entry in posted(sent) if entry[1] == f"meta@{demo}"] == [
        ("handoff", f"meta@{demo}", f"WATCH draw of his card on {KEY} failed 3 times in a row: it went out as {link}, which offers him no Approve at the head of {KEY}")
    ]


@pytest.mark.parametrize("said", [f"PUSHED {state.pr_url(KEY)}", f"WAITING {state.pr_url(KEY)} to land and release"])
@pytest.mark.parametrize("job_state", ["running", "waiting"])
def test_an_unchecked_pr_starts_one_check_against_his_words_whatever_its_jobs_state_whose_checked_line_wakes_no_one_and_whose_not_ready_line_goes_to_its_builder(
    named_paths: None,
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str], judge: Judge, said: str, job_state: str, woken: list[tuple[str, str]]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(state, "now", lambda: T0 + timedelta(minutes=1))
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["jobs"]["j1"] |= {"said": f"READY {state.pr_url(KEY)}", "returned": state.stamp(T0 + timedelta(minutes=5))}
    assert (watch.run_pass(watching, T0 + timedelta(minutes=5)), checkers) == ([], [KEY])
    judge.against = []
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    [checked] = watch.run_pass(watching, T0 + timedelta(minutes=6))
    assert checked.startswith(f"PR {KEY}: CHECKED against the words of o1 at aaaaaaa111; ") and checked.endswith("; Not on staging: no staging test is set for it [o1 j1]")
    assert not watch.wakes(checked, state.load(demo), NO_CARDS)
    assert (state.load(demo)["prs"][KEY]["checked"]["head"], watch.run_pass(watching, T0 + timedelta(minutes=7)), checkers) == ("aaaaaaa111", [], [KEY])


def test_a_not_ready_on_a_pr_he_approved_is_drawn_on_his_card_and_wakes_no_builder(
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str], judge: Judge, woken: list[tuple[str, str]]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(state, "now", lambda: T0 + timedelta(minutes=1))
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"PUSHED {state.pr_url(KEY)}", returned=state.stamp(T0))
        data["prs"][KEY] |= {"wants_tap": True, "checked": None, "tap": state.new_tap("Approve", "aaaaaaa111", "pressed the button", "1.2")}
    watching = watcher(demo)
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    judge.against = [{"order": "o1", "words": "ship the watch", "file": "watch.py", "change": "watch.py stops the watch"}]
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    watch.run_pass(watching, T0 + timedelta(minutes=2))
    assert (woken, state.against(state.load(demo)["prs"][KEY])) == ([], 'o1 "ship the watch": watch.py stops the watch')


def test_a_not_ready_on_a_stack_wakes_each_job_it_faults_once_a_head_under_his_hold_and_none_while_his_approve_is_on_any_pr_of_the_stack(demo: str, woken: list[tuple[str, str]]) -> None:
    refused = f"{served.NOT_READY}: {OTHER}, {THIRD} go against his words"
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"] = ["j1", "j2"]
        data["jobs"]["j2"] = job(prs=[state.pr_url(OTHER), state.pr_url(THIRD)])
        for key, job_id, branch, base, under in [(KEY, "j1", "root", "main", None), (OTHER, "j2", "middle", "root", KEY), (THIRD, "j2", "top", "middle", OTHER)]:
            data["prs"][key] = state.new_pr(state.pr_url(key), job_id, "o1") | {
                "head": "aaaaaaa111", "branch": branch, "base": base, "under": under, "wants_tap": True,
                "checker": {"head": "aaaaaaa111", "pid": 1, "at": state.stamp(T0), "orders": ["o1"], "answers": [], "outcome": refused, "against": "o1 \"ship the watch\": it stops", "faulted": [OTHER, THIRD]},
            }
        data["prs"][KEY]["tap"] = state.new_tap("Approve", "aaaaaaa111", "pressed the button", "1.2")
    watching = watcher(demo)
    watch.objections(watching)
    assert (woken, [state.load(demo)["prs"][key]["objected"] for key in (KEY, OTHER, THIRD)]) == ([], [None] * 3)
    with edit(demo) as data:
        data["prs"][KEY]["tap"] = state.new_tap("Hold", "aaaaaaa111", "pressed the button", "1.2")
    watch.objections(watching)
    watch.objections(watching)
    assert (woken, [state.load(demo)["prs"][key]["objected"] for key in (KEY, OTHER, THIRD)]) == ([(OTHER, f"PR {OTHER}: {refused} [o1 j2]")], [None, "aaaaaaa111", "aaaaaaa111"])


def test_a_green_pr_waiting_on_his_tap_gets_its_card_at_once_while_its_check_runs_or_says_not_ready_and_its_new_head_is_checked_again(
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str], judge: Judge, draws: Draws, woken: list[tuple[str, str]]
) -> None:
    reads = {KEY: read(checks=GREEN, reviewDecision="APPROVED")}
    serve(monkeypatch, reads)
    monkeypatch.setattr(state, "now", lambda: T0)
    with edit(demo) as data:
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
    watching.checking.add(KEY)
    watch.run_pass(watching, T0 + timedelta(minutes=2))
    assert checkers == [KEY, KEY]
    judge.against = []
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    watch.run_pass(watching, T0 + timedelta(minutes=3))
    assert (state.load(demo)["prs"][KEY]["checked"]["head"], draws.keys) == ("bbbbbbb222", [[KEY]])


def test_a_not_ready_keeps_its_head_however_often_its_builder_returns_and_is_judged_again_once_an_answer_of_his_binds_its_job_or_its_head_moves_telling_its_builder_once_a_head(
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str], judge: Judge, draws: Draws, woken: list[tuple[str, str]]
) -> None:
    reads = {KEY: read(checks=GREEN, reviewDecision="APPROVED")}
    serve(monkeypatch, reads)
    monkeypatch.setattr(state, "now", lambda: T0)
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"PUSHED {state.pr_url(KEY)}", returned=state.stamp(T0))
        data["prs"][KEY] |= {"wants_tap": True, "checked": None}
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    judge.against = [{"order": "o1", "words": "ship the watch", "file": "watch.py", "change": "watch.py stops the watch"}]
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    with edit(demo) as data:
        data["jobs"]["j1"] |= {"said": f"READY {state.pr_url(KEY)}", "returned": state.stamp(T0 + timedelta(minutes=2))}
    monkeypatch.setattr(state, "now", lambda: T0 + timedelta(minutes=2))
    watch.run_pass(watching, T0 + timedelta(minutes=2))
    refused = [text for _, text in woken if f"PR {KEY}: NOT READY: " in text]
    assert (checkers, len(refused), "watch.py stops the watch" in refused[0]) == ([KEY], 1, True)
    with edit(demo) as data:
        data["jobs"]["j1"]["brief_flags"] = {"ruling": ["fd06b56"]}
    watching.checking.add(KEY)
    watch.run_pass(watching, T0 + timedelta(minutes=3))
    judge.against = [{"order": "o1", "words": "ship the watch", "file": "watch.py", "change": "watch.py ends the watch it should keep"}]
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    watch.run_pass(watching, T0 + timedelta(minutes=4))
    assert (checkers, len([text for _, text in woken if f"PR {KEY}: NOT READY: " in text])) == ([KEY, KEY], 1)
    assert state.against(state.load(demo)["prs"][KEY]) == 'o1 "ship the watch": watch.py ends the watch it should keep'
    reads[KEY] = read(headRefOid="bbbbbbb222", checks=GREEN, reviewDecision="APPROVED")
    watching.checking.add(KEY)
    watch.run_pass(watching, T0 + timedelta(minutes=5))
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    watch.run_pass(watching, T0 + timedelta(minutes=6))
    assert (checkers, len([text for _, text in woken if f"PR {KEY}: NOT READY: " in text])) == ([KEY] * 3, 2)


def test_a_check_names_every_pr_its_job_and_the_job_it_was_handed_from_carry_with_merged_ones_marked_so_a_part_a_merged_pr_delivers_gets_no_not_ready(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    merged, closed = "yasyf/cc-skills#4484", "yasyf/cc-skills#4490"
    with edit(demo) as data:
        data["jobs"]["j1"]["prs"] += [state.pr_url(merged), state.pr_url(closed), state.pr_url(OTHER)]
        data["jobs"]["j2"] = job(prs=[state.pr_url(KEY)])
        data["prs"][KEY] |= {"job": "j2", "wants_tap": True, "checked": None}
        data["prs"][merged] = state.new_pr(state.pr_url(merged), "j1", "o1") | {"state": "merged", "title": "Fix: a Sprite lane gets the wasm toolchain"}
        data["prs"][closed] = state.new_pr(state.pr_url(closed), "j1", "o1") | {"state": "closed", "title": "Fix: the toolchain, abandoned"}
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"title": "Fix: the stack's top"}
    delivered = f'its job\'s merged PRs already deliver their share of these orders: {merged} "Fix: a Sprite lane gets the wasm toolchain"'
    told: list[str] = []
    wasm = {"order": "o1", "words": "ship the watch", "file": "", "change": "no PR gives Sprites the wasm toolchain"}
    monkeypatch.setattr(served, "judged", lambda name, said, changes: told.append(said) or {"against": [] if delivered in said else [wasm]})
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    [said] = told
    assert f'{delivered}\nits job\'s other open PRs, each judged on its own, deliver their share of these orders: {OTHER} "Fix: the stack\'s top"' in said
    assert closed not in said
    assert state.load(demo)["prs"][KEY]["checker"]["outcome"].startswith(f"{served.CHECKED} against the words of o1 at aaaaaaa111")


def test_a_check_whose_judge_failed_is_said_nowhere_and_tried_again_on_the_next_sweep(
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(state, "now", lambda: T0)
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"READY {state.pr_url(KEY)}")
        data["prs"][KEY] |= {"wants_tap": True, "checked": None}

    def failing(name: str, said: str, changes: dict) -> dict:
        raise state.DriveError("the judge timed out")

    monkeypatch.setattr(served, "judged", failing)
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert cli.main(["pr", "check", state.pr_url(KEY), "--drive", demo]) == 0
    assert state.load(demo)["prs"][KEY]["checker"]["outcome"] == "check failed: the judge timed out"
    assert (watch.run_pass(watching, T0 + timedelta(minutes=10)), checkers) == ([], [KEY])
    assert (watch.run_pass(watching, T0 + watch.CHECK_SWEEP), checkers) == ([], [KEY, KEY])


def test_a_ports_change_after_his_approve_lands_at_its_first_green_head_and_starts_no_check_against_his_words(
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str], landers: list[tuple[str, ...]]
) -> None:
    state.keep_report(demo, "j1", f"READY {state.pr_url(KEY)}", f"READY {state.pr_url(KEY)}\nSERVES: port of #50974\n")
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", said=f"READY {state.pr_url(KEY)}")
        data["prs"][KEY] |= {"wants_tap": True, "checked": None, "port_of": 50974, "tap": {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack", "picks": {}, "card": None}}
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks=GREEN, reviewDecision="APPROVED")})
    watch.run_pass(watcher(demo), T0)
    assert (checkers, landers) == ([], [(KEY,)])


def test_a_pr_waiting_on_his_tap_draws_his_card_from_the_watch_on_its_first_read_with_no_coordinator_turn_and_no_record(
    demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws
) -> None:
    from drive import tools

    waiting_job(demo)
    with edit(demo) as data:
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
    assert (draws.keys, view.tap_card(data := state.load(demo), data["prs"][KEY], view.live_cards(demo))) == ([[KEY]], draws.link)


def test_a_pr_held_on_a_teammates_review_draws_his_card_only_once_their_newest_review_is_of_its_head_opening_with_that_review(
    demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws, woken: list[tuple[str, str]]
) -> None:
    waiting_job(demo)
    with edit(demo) as data:
        data["prs"][KEY] |= {"wants_tap": True, "waits": ["review:Octocat"]}
    reads = {KEY: read(checks=GREEN, reviewDecision="APPROVED", reviews={"octocat": {"login": "octocat", "head": "0ld0000", "state": "APPROVED"}})}
    serve(monkeypatch, reads)
    watch.run_pass(watcher(demo), T0)
    pr = state.load(demo)["prs"][KEY]
    assert (draws.keys, pr["waits"], pr["stage"]["name"], pr["stage"]["owner"], woken) == ([], ["review:Octocat"], "reviewing", stage.OUTSIDE, [])
    reads[KEY]["reviews"] = {"octocat": {"login": "octocat", "head": "aaaaaaa111", "state": "COMMENTED"}}
    lines = watch.run_pass(watcher(demo), T0 + timedelta(minutes=1))
    data = state.load(demo)
    assert (f"PR {KEY}: octocat reviewed aaaaaaa [o1 j1]" in lines, draws.keys, data["prs"][KEY]["waits"]) == (True, [[KEY]], [])
    assert card.lead_words(data["prs"][KEY]).splitlines()[0] == "**Reviewed by octocat:** commented on aaaaaaa"


THIRD = "Forge-AI/monorepo#44"
STACKED = (KEY, OTHER, THIRD)


def three_stacked(demo: str, monkeypatch: pytest.MonkeyPatch, native: bool = True) -> dict[str, dict]:
    waiting_job(demo)
    with edit(demo) as data:
        data["orders"]["o2"] = order(text="split the stack", jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", prs=[state.pr_url(OTHER), state.pr_url(THIRD)], state="waiting")
        data["prs"][KEY]["wants_tap"] = True
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o2") | {"checked": CHECKED | {"head": "bbbbbbb222", "orders": ["o2"]}}
        data["prs"][THIRD] = state.new_pr(state.pr_url(THIRD), "j2", "o2") | {"checked": CHECKED | {"head": "ccccccc333", "orders": ["o2"]}}
    monkeypatch.setattr(watch.view, "cards", lambda drive: watch.view.NO_CARDS)
    reads = {key: stacked_read(head) for key, head in zip(STACKED, ("aaaaaaa111", "bbbbbbb222", "ccccccc333"))}
    if not native:
        for key, below, (base, branch) in zip(STACKED, (None, *STACKED[:2]), (("main", "move-1"), ("move-1", "move-2"), ("move-2", "move-3"))):
            reads[key] |= {"native": [], "baseRefName": base, "headRefName": branch, "under": below}
    serve(monkeypatch, reads)
    return reads


def stacked_read(head: str, **overrides: object) -> dict:
    return read(**{"headRefOid": head, "checks": GREEN, "reviewDecision": "APPROVED", "native": [(key, "open") for key in STACKED]} | overrides)


def test_the_prs_of_one_native_stack_waiting_on_his_tap_draw_one_card_and_one_reworked_to_his_words_joins_them_on_one_fresh_card_once_its_rework_ends(
    demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws
) -> None:
    three_stacked(demo, monkeypatch)
    with edit(demo) as data:
        state.rework(data["prs"][THIRD], "o2", "Split the stack by its native GitHub stack", "https://acme.slack.com/archives/D123/p1794300000000600")
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    watch.run_pass(watching, T0 + timedelta(minutes=10))
    assert draws.keys == [[KEY, OTHER]]
    with edit(demo) as data:
        data["prs"][THIRD]["rework"] = None
    watch.run_pass(watching, T0 + timedelta(minutes=20))
    watch.run_pass(watching, T0 + timedelta(minutes=30))
    assert draws.keys == [[KEY, OTHER], list(STACKED)]


@pytest.mark.parametrize("native", [True, False])
def test_replayed_56774_his_words_reworking_the_middle_pr_of_a_three_pr_stack_draw_his_card_on_the_pr_below_alone_and_none_for_the_pr_above_which_owes_no_tap_and_reads_behind_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws, native: bool
) -> None:
    three_stacked(demo, monkeypatch, native)
    with edit(demo) as data:
        state.rework(data["prs"][OTHER], "o2", "Refuse past 256 MiB", "https://acme.slack.com/archives/D123/p1794300000000600")
    watching = watcher(demo)
    lines = [*watch.run_pass(watching, T0), *watch.run_pass(watching, T0 + timedelta(minutes=10)), *watch.run_pass(watching, T0 + timedelta(minutes=40))]
    prs = state.load(demo)["prs"]
    assert draws.keys == [[KEY]]
    assert [line for line in lines if "draw of his card" in line] == []
    assert (prs[THIRD]["stage"]["name"], prs[THIRD]["stage"]["owner"], prs[THIRD]["stage"]["why"]) == ("behind", stage.BUILDER, f'{OTHER} below it in its stack, rework: being reworked to his words "Refuse past 256 MiB"')
    assert limits.tap_owed_not(prs, THIRD, state.PrGraph.read(prs)) == f'{OTHER} below it in its stack is being reworked to his words "Refuse past 256 MiB" until its builder\'s READY says how they are met'
    assert limits.tap_owed_not(prs, KEY, state.PrGraph.read(prs)) == ""


@pytest.mark.parametrize("native", [True, False])
def test_a_stack_whose_prs_each_sit_on_a_card_of_their_own_draws_one_fresh_card_for_the_stack_once(demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws, native: bool) -> None:
    reads = three_stacked(demo, monkeypatch, native)
    alone = [
        {
            "prs": [state.pr_url(key)],
            "asked": state.stamp(T0),
            "url": f"https://acme.slack.com/archives/D123/p179459540534553{index}",
            "card": {"title": "Fix: approve it?", "waits": f"merge:{key}", "prs": [{"url": state.pr_url(key), "head": reads[key]["headRefOid"]}]},
        }
        for index, key in enumerate(STACKED)
    ]
    monkeypatch.setattr(watch.view, "cards", lambda drive: watch.view.NO_CARDS | {"pr_cards": alone})
    watching = watcher(demo)
    for minutes in (0, 10, 20, 30):
        watch.run_pass(watching, T0 + timedelta(minutes=minutes))
    assert draws.keys == [list(STACKED)]


def test_a_stack_waiting_on_his_tap_draws_its_first_card_only_once_no_pr_of_it_entered_uncarded_for_ten_minutes(
    demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws, stack_quiet: Callable[[timedelta], None]
) -> None:
    stack_quiet(timedelta(minutes=10))
    three_stacked(demo, monkeypatch)
    watching = watcher(demo)
    lines = [*watch.run_pass(watching, T0), *watch.run_pass(watching, T0 + timedelta(minutes=5))]
    assert (draws.keys, len([line for line in lines if line.startswith("HELD his card on")])) == ([], 1)
    watch.run_pass(watching, T0 + timedelta(minutes=16))
    assert draws.keys == [list(STACKED)]


def test_a_held_stack_waiting_on_the_cutover_he_has_not_scheduled_draws_no_card_however_many_passes_and_one_card_for_the_whole_stack_once_he_schedules_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws
) -> None:
    reads = three_stacked(demo, monkeypatch)
    with edit(demo) as data:
        data["orders"]["o3"] = order(text="cut over to the new tree", title="The cutover", state="waiting", jobs=[])
        for job_id in ("j1", "j2"):
            data["jobs"][job_id]["waits"] = ["o3"]
        for key in STACKED:
            data["prs"][key]["tap"] = {"label": "Hold", "at": state.stamp(T0), "head": reads[key]["headRefOid"], "source": "pressed the button"}
            reads[key] |= {"labels": [github.HOLD_LABEL]}
    watching = watcher(demo)
    for minutes in (0, 10, 40, 120):
        watch.run_pass(watching, T0 + timedelta(minutes=minutes))
    data = state.load(demo)
    assert (draws.keys, [data["prs"][key]["stage"]["name"] for key in STACKED]) == ([], ["unscheduled"] * 3)
    with edit(demo) as data:
        data["orders"]["o3"]["state"] = "open"
    watch.run_pass(watching, T0 + timedelta(minutes=130))
    assert draws.keys == [list(STACKED)]


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


def test_a_live_bugs_stack_waiting_on_his_card_is_drawn_once_and_never_asked_again(demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws) -> None:
    three_stacked(demo, monkeypatch)
    with edit(demo) as data:
        for order_id in ("o1", "o2"):
            data["orders"][order_id]["reporters"] = [state.reporter(REPORTED)]
    watching = watcher(demo)
    for minutes in (0, 10, 39, 40, 69, 70):
        watch.run_pass(watching, T0 + timedelta(minutes=minutes))
    assert draws.keys == [list(STACKED)]


def test_the_one_post_in_each_place_saying_what_waits_on_him_goes_up_once_a_card_waits_there_is_pinned_once_and_is_edited_in_place_as_its_cards_change(
    demo: str, monkeypatch: pytest.MonkeyPatch, unposted_waits: Callable[..., dict[str, str]]
) -> None:
    monkeypatch.setattr(talk, "waiting_posts", unposted_waits)
    monkeypatch.setattr(slack, "operator_channels", lambda: [{"id": "C777", "name": "him-drive-demo-iris"}, {"id": "C888", "name": "him-drive-demo-sandsql"}])
    posts: list[dict] = []
    monkeypatch.setattr(talk, "posted", lambda name, post: posts.append(post) or {"permalink": post.get("edit") or f"https://acme.slack.com/archives/{post.get('channel_id', 'D123')}/p1794400200000100"})
    pins: list[str] = []
    monkeypatch.setattr(slack, "pin", pins.append)
    with edit(demo) as data:
        data["slack"]["channels"] = {"Iris": {"id": "C777", "name": "him-drive-demo-iris"}, "SandSQL": {"id": "C888", "name": "him-drive-demo-sandsql"}}
        data["orders"]["o2"] = order(project="Iris", jobs=["j2"])
        data["orders"]["o3"] = order(jobs=["j3"], reporters=[state.reporter(REPORTED)])
        data["jobs"]["j2"] = job(order="o2")
        data["jobs"]["j3"] = job(order="o3")
    waits = [*(f"demo:j1-{number}-a" for number in range(1, 6)), "demo:j3-1-a", "demo:j2-1-a"]
    cards = {each: question(each) | {"url": f"https://acme.slack.com/archives/D123/p179440010000010{index}"} for index, each in enumerate(waits)}
    watching = watcher(demo)
    board = dashboard.link(demo)

    def passes(*shown: str) -> list[dict]:
        watching.cards = view.NO_CARDS | {"questions": [cards[each] for each in shown]}
        posts.clear()
        assert watch.waiting_post_lines(watching, T0) == []
        return posts

    in_dm = f"*<{board}#/queue/all|Waiting on you: 6 questions>*\n" + "\n".join(f"{{card:{each}}}" for each in [waits[5], *waits[:4]])
    in_iris = f"*<{board}#/queue/all?project=Iris|Waiting on you: 1 question>*\n{{card:demo:j2-1-a}}"
    assert passes(*waits) == [{"text": in_iris, "channel_id": "C777"}, {"text": in_dm}]
    dm_post, iris_post = "https://acme.slack.com/archives/D123/p1794400200000100", "https://acme.slack.com/archives/C777/p1794400200000100"
    assert pins == [iris_post, dm_post]
    assert passes(*waits) == []
    fixed = f"*<{board}#/queue/all|Waiting on you: 5 questions>*\n" + "\n".join(f"{{card:{each}}}" for each in waits[:5])
    assert passes(*waits[:5], waits[6]) == [{"edit": dm_post, "text": fixed}]
    assert passes() == [{"edit": iris_post, "text": talk.NO_CARDS}, {"edit": dm_post, "text": talk.NO_CARDS}]
    assert pins == [iris_post, dm_post]
    assert state.load(demo)["slack"]["waiting"] == {"C777": {"message": iris_post, "words": talk.NO_CARDS, "pinned": True}, "D123": {"message": dm_post, "words": talk.NO_CARDS, "pinned": True}}


def test_his_question_cards_asked_over_a_day_ago_go_out_once_a_day_in_one_digest_and_leave_their_orders_news_lines(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    posts: list[dict] = []
    monkeypatch.setattr(talk, "posted", lambda name, post: posts.append(post) or {"permalink": "https://acme.slack.com/archives/D123/p1794400200000100"})
    old, fresh = question("demo:j1-1-a") | {"asked": state.stamp(T0 - timedelta(days=2))}, question("demo:j1-2-a")
    watching = watcher(demo)
    watching.cards = view.NO_CARDS | {"questions": [fresh, old]}
    assert watch.digest_lines(watching, T0) == ["DIGEST 2026-11-09: 1 of his question cards asked more than a day ago, linked in https://acme.slack.com/archives/D123/p1794400200000100"]
    assert (len(posts), posts[0]["request_key"].split("-", 1)[1], "{card:demo:j1-1-a}" in posts[0]["text"], "{card:demo:j1-2-a}" in posts[0]["text"]) == (1, f"{demo}.digest.2026-11-09", True, False)
    assert watch.digest_lines(watching, T0 + timedelta(hours=1)) == [] and len(posts) == 1
    data = state.load(demo)
    assert data["slack"]["digest"]["waits"] == ["demo:j1-1-a"]
    assert watch.digest_lines(watching, T0 + timedelta(days=1)) != [] and len(posts) == 2


def test_a_waiting_post_kept_unpinned_is_pinned_on_the_next_pass_with_no_new_post_and_a_refused_pin_goes_again(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    dm_post = "https://acme.slack.com/archives/D123/p1794400200000100"
    with edit(demo) as data:
        data["slack"]["waiting"]["D123"] = {"message": dm_post, "words": talk.NO_CARDS}
    posts: list[dict] = []
    monkeypatch.setattr(talk, "posted", lambda name, post: posts.append(post) or {"permalink": dm_post})
    monkeypatch.setattr(talk, "waiting_posts", lambda name, data, found, moment: {"D123": talk.NO_CARDS})
    refusals = [slack.SlackError("cc-slack can't pin here (ratelimited)")]
    pins: list[str] = []

    def pin(link: str) -> None:
        if refusals:
            raise refusals.pop()
        pins.append(link)

    monkeypatch.setattr(slack, "pin", pin)
    watching = watcher(demo)
    watching.cards = view.NO_CARDS
    assert watch.waiting_post_lines(watching, T0) == []
    assert (pins, state.load(demo)["slack"]["waiting"]["D123"]["pinned"]) == ([], False)
    assert watch.waiting_post_lines(watching, T0 + timedelta(minutes=1)) == []
    assert (posts, pins, state.load(demo)["slack"]["waiting"]["D123"]["pinned"]) == ([], [dm_post], True)
    assert watch.waiting_post_lines(watching, T0 + timedelta(minutes=2)) == []
    assert pins == [dm_post]


def test_a_pr_on_another_open_prs_branch_draws_one_card_with_it_before_either_is_checked(demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws) -> None:
    waiting_job(demo)
    with edit(demo) as data:
        data["orders"]["o2"] = order(text="a cloud agent signs in as its own principal", jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", prs=[state.pr_url(OTHER)], state="waiting")
        data["prs"][KEY] |= {"wants_tap": True, "checked": None}
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o2")
    serve(
        monkeypatch,
        {
            OTHER: read(headRefOid="bbbbbbb222", baseRefName="dev", default="dev", headRefName="cloud-agent-identity", checks=GREEN, reviewDecision="APPROVED"),
            KEY: read(baseRefName="cloud-agent-identity", under=OTHER, default="dev", headRefName="cloud-agent-pod-identity", checks=GREEN, reviewDecision="APPROVED"),
        },
    )
    watch.run_pass(watcher(demo), T0)
    assert [sorted(keys) for keys in draws.keys] == [sorted([KEY, OTHER])]


def test_sibling_prs_of_one_order_in_no_stack_draw_one_card_and_a_pr_of_another_order_its_own(demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws) -> None:
    waiting_job(demo)
    with edit(demo) as data:
        data["orders"]["o2"] = order(text="a cloud agent signs in as its own principal", jobs=["j2"])
        data["jobs"]["j1"]["prs"].append(state.pr_url(OTHER))
        data["jobs"]["j2"] = job(order="o2", prs=[state.pr_url(THIRD)], state="waiting")
        data["prs"][KEY]["wants_tap"] = True
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1")
        data["prs"][THIRD] = state.new_pr(state.pr_url(THIRD), "j2", "o2")
    heads = {KEY: "aaaaaaa111", OTHER: "bbbbbbb222", THIRD: "ccccccc333"}
    serve(monkeypatch, {key: read(headRefOid=head, baseRefName="dev", default="dev", headRefName=f"cutover-{key.split('#')[1]}", checks=GREEN, reviewDecision="APPROVED") for key, head in heads.items()})
    watch.run_pass(watcher(demo), T0)
    assert draws.keys == [[KEY, OTHER], [THIRD]]


def test_a_pr_reworked_to_his_words_loses_the_card_left_up_wakes_its_builder_and_draws_a_fresh_card_once_its_builders_ready_says_how_they_are_met(
    demo: str, monkeypatch: pytest.MonkeyPatch, withdrawals: Withdrawals, draws: Draws, woken: list[tuple[str, str]]
) -> None:
    waiting_job(demo)
    with edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
        state.rework(data["prs"][KEY], "o2", "Topology goes in the meta table", "https://acme.slack.com/archives/D123/p1794300000000600")
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
    with edit(demo) as data:
        state.take_result(data, "j1", f"READY {state.pr_url(KEY)}", f"READY {state.pr_url(KEY)}\nCHANGE: it reads the meta table")
    watch.run_pass(watcher(demo), T0 + timedelta(minutes=10))
    assert (state.load(demo)["prs"][KEY]["rework"], draws.keys) == (None, [[KEY]])


def test_a_pr_waiting_on_an_order_he_has_not_scheduled_loses_the_card_left_up_wakes_no_one_and_draws_a_fresh_card_once_he_schedules_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, withdrawals: Withdrawals, draws: Draws, woken: list[tuple[str, str]]
) -> None:
    waiting_job(demo)
    with edit(demo) as data:
        data["orders"]["o2"] = order(text="cut over to the new tree", title="The cutover", state="waiting", jobs=[])
        data["prs"][KEY] |= {"wants_tap": True, "waits": ["o2"]}
    shown = [pr_card(KEY)]
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [], "closed": [], "pr_cards": list(shown)})
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    withdrawals.cards = [CARD]
    said = watch.run_pass(watcher(demo), T0)
    shown.clear()
    data = state.load(demo)
    reason = 'Waits on your order "The cutover", which you have not scheduled. A fresh card asks your tap once you do'
    assert (withdrawals.asked, data["prs"][KEY]["stage"]["name"], woken, draws.keys) == ([(state.pr_url(KEY), reason)], "unscheduled", [], [])
    assert state.tap_refused(data, data["prs"][KEY]) == f"it waits on o2, work he has not scheduled, so no card asks him on it until he does, by his answer {state.UNSCHEDULED_RULING}"
    assert not [line for line in said if line.startswith("STAGE ")]
    watch.run_pass(watcher(demo), T0 + timedelta(hours=2))
    assert (len(withdrawals.asked), woken, draws.keys) == (1, [], [])
    with edit(demo) as data:
        data["orders"]["o2"]["state"] = "open"
    watch.run_pass(watcher(demo), T0 + timedelta(hours=2, minutes=1))
    assert draws.keys == [[KEY]]


def test_a_job_waiting_on_an_order_he_has_not_scheduled_holds_its_pr_from_his_card_as_the_pr_s_own_wait_does() -> None:
    data = {"orders": {"o2": order(state="waiting")}, "jobs": {"j1": job(waits=["o2"])}, "prs": {KEY: state.new_pr(state.pr_url(KEY), "j1", "o1")}}
    assert state.unscheduled_waits(data, data["prs"][KEY]) == ["o2"]
    data["orders"]["o2"]["state"] = "open"
    assert state.unscheduled_waits(data, data["prs"][KEY]) == []


@pytest.mark.parametrize("wants_tap", [False, True])
def test_an_exempt_pr_whose_landers_try_ended_unlanded_wakes_the_session_holding_its_order_once_and_never_again_in_that_stage(
    demo: str, monkeypatch: pytest.MonkeyPatch, wants_tap: bool, landers: list[tuple[str, ...]]
) -> None:
    waiting_job(demo)
    tried = {"head": "aaaaaaa111", "pid": 1, "at": state.stamp(T0), "outcome": "not landed: stack-enqueue --watch exited 1: checks failed", "below": []}
    with edit(demo) as data:
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
    assert line not in watch.run_pass(watching, T0 + timedelta(minutes=30))
    assert "checks pass, review approved, no tap needed, exempt: incident fix; unlanded since " in render.pr_line(state.load(demo), KEY, None)
    assert landers == []


def test_replayed_design_docs_363_a_lander_try_that_merged_the_pr_reads_merged_owed_by_nobody_and_wakes_no_one_by_its_own_read_back_before_the_watch_reads_it_again(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    monkeypatch.setattr(watch, "run_pass", REAL_RUN_PASS)
    docs, old, new = "Forge-AI/design-docs#363", "0c14eb34a1", "febb6084f2"
    behind = f"not landed: gh pr merge --rebase --match-head-commit {old} refused it: X Pull request {docs} is not mergeable: the head branch is not up to date with the base branch."
    merged = "merged at 967f1e30740f; it changes no plugin installed on this Mac"
    waiting_job(demo)
    with edit(demo) as data:
        data["jobs"]["j1"]["prs"].append(state.pr_url(docs))
        data["prs"][docs] = state.new_pr(state.pr_url(docs), "j1", "o1") | {
            "head": old,
            "base": "main",
            "merge_method": "rebase",
            "tap": state.new_tap("Approve", old, "pressed the button", at=state.stamp(T0 - timedelta(minutes=1))),
            "lander": {"head": old, "pid": 1, "at": state.stamp(T0), "outcome": behind, "below": []},
        }
    serve(monkeypatch, {docs: read(headRefOid=old, checks=GREEN, required_reviews=0, merge_method="rebase")})
    unlanded = f"STAGE {docs} unlanded since {state.stamp(T0)}, owed by the session holding its order: its lander's try at 0c14eb3, started {state.stamp(T0)}, ended {behind} [o1 j1]"
    assert unlanded in watch.run_pass(watcher(demo), T0)
    assert watch.wakes(unlanded, state.load(demo), NO_CARDS)
    calls = serve(monkeypatch, {docs: read(headRefOid=new, checks=GREEN, required_reviews=0, merge_method="rebase")})
    watching = watcher(demo)
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    assert landers == [(docs,)]
    with monkeypatch.context() as merging:
        merging.setattr(land.subprocess, "run", lambda argv, **kwargs: land.subprocess.CompletedProcess(argv, 0, "", ""))
        merging.setattr(github, "fetch", lambda keys, into=None: ({docs: read(state="MERGED", mergeCommit="967f1e30740f", headRefOid=new)}, None))
        merging.setattr(github, "files", lambda key: ["README.md"])
        merging.setattr(install, "touched", lambda repo, files: [])
        assert land.run(demo, [docs]) == merged
    fetched = len(calls)
    lines = watch.run_pass(watching, T0 + timedelta(minutes=2))
    assert (any(docs in keys for keys in calls[fetched:]), state.load(demo)["prs"][docs]["state"]) == (False, "merged")
    assert not [line for line in lines if line.startswith(f"STAGE {docs}")]
    assert stage.of(state.load(demo), docs, NO_CARDS) == stage.Stage("merged", stage.NOBODY, "it is merged")
    assert not watch.wakes(unlanded, state.load(demo), NO_CARDS)
    assert landers == [(docs,)]


def test_a_merge_github_reads_open_after_gh_pr_merge_said_it_merged_is_not_landed_installs_nothing_and_leaves_the_conflict_its_builder_owes(
    demo: str, monkeypatch: pytest.MonkeyPatch, pr_reads: dict[str, dict]
) -> None:
    docs, credited = "Forge-AI/design-docs#5", "merged at 6813a504aa12; long-running@skills 1.0.4 installed"
    tap = state.new_tap("Approve", "ddddddd444", "pressed the button", at=state.stamp(T0))
    with edit(demo) as data:
        data["prs"][docs] = state.new_pr(state.pr_url(docs), "j1", "o1") | {"head": "ddddddd444", "base": "main", "default": "main", "tap": tap, "conflict": True}
        data["prs"][docs]["lander"] = {"head": "ddddddd444", "base": "main", "pid": 1, "at": state.stamp(T0), "outcome": credited, "below": []}
    before = stage.of(state.load(demo), docs, NO_CARDS)
    assert (before.name, before.owner) == ("red", stage.BUILDER)
    runs: list[list[str]] = []
    pr_reads[docs] = read(headRefOid="ddddddd444", mergeable="CONFLICTING")
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: runs.append(argv) or land.subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(github, "files", lambda key: ["plugins/long-running/lib/drive/land.py"])
    monkeypatch.setattr(install, "touched", lambda repo, files: ["long-running@skills"])
    assert land.run(demo, [docs]) == "not landed: GitHub reads it open after gh pr merge said it merged, so nothing was installed"
    assert runs == [["gh", "pr", "merge", "5", "-R", "Forge-AI/design-docs", "--squash", "--match-head-commit", "ddddddd444"]]
    after = stage.of(state.load(demo), docs, NO_CARDS)
    assert ({field: state.load(demo)["prs"][docs][field] for field in ("state", "merged_sha")}, after.name, after.owner) == ({"state": "open", "merged_sha": None}, "red", stage.BUILDER)


def test_a_lander_outside_forge_ai_monorepo_lands_one_pr_and_credits_no_other_with_its_merge(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    docs, other = "Forge-AI/design-docs#5", "Forge-AI/design-docs#6"
    with edit(demo) as data:
        for key in (docs, other):
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"head": "ddddddd444", "tap": state.new_tap("Approve", "ddddddd444", "pressed the button", at=state.stamp(T0))}
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: pytest.fail(f"ran {argv}"))
    with pytest.raises(state.DriveError, match="gh pr merge merges one PR at a time"):
        land.run(demo, [docs, other])
    assert [state.load(demo)["prs"][key]["lander"] for key in (docs, other)] == [None, None]


@pytest.mark.parametrize(
    ("exempt", "path", "refused", "remedy"),
    [
        ("ci-only", "api/src/index.ts", 'is no "ci-only" at its head: it changes api/src/index.ts, outside CI configuration and CI tooling; its class comes off', []),
        ("flake fix", "go/sand/next/internal/vm_run/vmruntest/vmruntest.go", 'is a "flake fix", which lands with no tap only in test or fixture code', ["--tap"]),
    ],
)
def test_a_try_its_files_put_outside_its_exempt_class_stops_standing_once_his_card_asks_his_approve_a_ci_only_one_with_no_session_word_and_his_approve_lands_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], exempt: str, path: str, refused: str, remedy: list[str]
) -> None:
    waiting_job(demo)
    with edit(demo) as data:
        data["prs"][KEY] |= {"exempt": exempt, "wants_tap": False, "checks": "pass", "review": "approved"}
    diff = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+b\n"
    monkeypatch.setattr(land.served, "change", lambda key: {"title": "Fix: pin the release image digest", "body": "", "head": "aaaaaaa111", "diff": diff.encode()})
    monkeypatch.setattr(github, "files", lambda key: [])
    assert land.run(demo, [KEY]).startswith(f"not landed: {KEY} {refused}")
    if remedy:
        assert cli.main(["pr", "set", KEY, *remedy, "--drive", demo]) == 0
    assert (state.load(demo)["prs"][KEY]["exempt"], state.load(demo)["prs"][KEY]["wants_tap"]) == (None if exempt == "ci-only" else exempt, True)
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [], "closed": [], "pr_cards": [pr_card(KEY)]})
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    for moment in (T0, T0 + timedelta(minutes=30)):
        assert not [line for line in watch.run_pass(watching, moment) if line.startswith("STAGE")]
        assert (now := stage.of(state.load(demo), KEY, watching.cards)).name == "card" and now.owner == stage.HIM
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["prs"][KEY]["exempt"] = "ci-only"
    diff = "".join(f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+b\n" for path in paths)
    monkeypatch.setattr(land.served, "change", lambda key: {"title": "Fix: build CI images from one contract", "body": "", "head": "aaaaaaa111", "diff": diff.encode()})
    assert land.unexempt(state.load(demo), KEY) is None

def test_a_pr_waiting_on_another_prs_merge_is_freed_when_it_merges_while_that_prs_job_runs_on(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    other = "Forge-AI/monorepo#43"
    assert cli.main(["pr", "set", KEY, "--waits", other, "--drive", demo]) == 2
    assert f"--waits {other}: the drive tracks no PR {other}; drive pr add {state.pr_url(other)} --job <j> tracks it first" in capsys.readouterr().err
    with edit(demo) as data:
        data["jobs"]["j2"] = job(goal="fix the shm lock and prove it live")
        data["prs"][other] = state.new_pr(state.pr_url(other), "j2", "o1")
    assert cli.main(["pr", "set", KEY, "--waits", other, "--drive", demo]) == 0
    with edit(demo) as data:
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
    with edit(name) as data:
        data["prs"][KEY] |= {"wants_tap": True, "tap": {"label": "Hold", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "pressed the button"}}


@pytest.mark.parametrize(("count", "queries"), [(1, [1]), (120, [25, 25, 25, 25, 20])])
def test_a_watch_pass_reads_github_twenty_five_open_prs_a_call_however_many_it_watches(
    demo: str, monkeypatch: pytest.MonkeyPatch, count: int, queries: list[int]
) -> None:
    with edit(demo) as data:
        for number in range(1, count):
            key = f"Forge-AI/monorepo#{1000 + number}"
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"head": f"head{number}", "base": "main"}
    asked: list[list[str]] = []
    monkeypatch.setattr(github, "fetch", REAL_FETCH)
    monkeypatch.setattr(github, "run", lambda cmd, **_: asked.append(cmd) or json.dumps({"data": {}}))
    watch.run_pass(watcher(demo), T0)
    assert sorted((cmd[:3], cmd[4].count("pullRequest(")) for cmd in asked) == sorted((["gh", "api", "graphql"], size) for size in queries)


def test_a_held_green_approved_pr_is_never_said_to_have_no_card(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    hold(demo)
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=300)) == []


@pytest.mark.parametrize(
    ("tap", "labels", "field", "says"),
    [
        ({"label": "Hold", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "pressed the button"}, [], "lands", f"you approve, lifting your Hold of {render.when(state.stamp(T0))} and CI passes"),
        ({"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack", "picks": {}, "card": None}, [github.HOLD_LABEL], "blocks", "Approved by you. Held by the hold label on GitHub. CI failed; the agent is fixing it"),
    ],
    ids=["his hold", "the hold label on a pr he approved"],
)
def test_a_red_pr_his_hold_or_the_hold_label_holds_reads_red_and_wakes_its_builder_every_thirty_minutes_while_no_lander_starts_and_the_dashboard_says_what_lifts_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], landers: list[tuple[str, ...]], tap: dict | None, labels: list[str], field: str, says: str
) -> None:
    waiting_job(demo)
    with edit(demo) as data:
        data["prs"][KEY] |= {"wants_tap": True, "tap": tap}
    serve(monkeypatch, {KEY: read(checks=RED, labels=labels)})
    monkeypatch.setattr(watch.view, "cards", lambda drive: NO_CARDS | {"pr_cards": [pr_card(KEY)]})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    watch.run_pass(watching, T0 + timedelta(minutes=30))
    data = state.load(demo)
    kept = data["prs"][KEY]["stage"]
    assert (kept["name"], kept["owner"], [key for key, _ in woken], landers) == ("red", stage.BUILDER, [KEY, KEY], [])
    assert land.held(data, [KEY]) == f"{KEY}: {stage.stopped(data['prs'][KEY])}"
    assert dashboard_lines(demo, KEY)[field] == says


def test_a_red_pr_his_card_holds_wakes_its_builder_at_once_and_every_thirty_minutes_while_his_card_stays_up(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    waiting_job(demo)
    asking(monkeypatch, demo, HOLDING_CARD, taps=[pr_card(KEY)])
    holding_job(demo, HOLDING_CARD)
    holding_asks(demo, "pause and resume, which #42 carries")
    with edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
    serve(monkeypatch, {KEY: read(checks=RED)})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    watch.run_pass(watching, T0 + timedelta(minutes=30))
    kept = state.load(demo)["prs"][KEY]["stage"]
    assert (kept["name"], kept["owner"], [key for key, _ in woken]) == ("red", stage.BUILDER, [KEY, KEY])
    assert stage.held(state.load(demo), state.load(demo)["prs"][KEY])


def dashboard_lines(name: str, key: str) -> dict[str, str]:
    data = state.load(name)
    entry = data["prs"][key]
    shown = {"title": entry["title"], "body": "", "comments": [], "state": "OPEN", "isDraft": False, "headRefOid": entry["head"], "labels": [], "url": entry["url"], "reviewDecision": "REVIEW_REQUIRED", "files": []}
    return card.standing(shown, card.drawn_entry(data, entry, view.live_cards(name)))


def test_replayed_55175_a_red_pr_whose_builder_waits_on_a_fix_that_waits_on_his_tap_names_and_links_that_fix_and_its_card_in_its_stage(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    red_pr, fix, fix_card = "Forge-AI/monorepo#55175", "Forge-AI/monorepo#55582", "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794549855666519"
    titles = {red_pr: "New: the change dev left red", fix: "Fix: the flaky test dev broke"}
    heads = {red_pr: "75e6d42aaa", fix: "55582aaaaa"}
    with edit(demo) as data:
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
        watch.run_pass(watcher(demo), T0 + timedelta(minutes=minutes))
        kept = state.load(demo)["prs"][red_pr]["stage"]
        passes.append((minutes, kept["name"], kept["owner"], [key for key, _ in woken]))
        woken.clear()

    at(0)
    with edit(demo) as data:
        state.take_result(data, "j2661", f"WAITING #55175 waits on {state.pr_url(fix)} to merge")
    at(16)
    fix_link = view.pr_link(state.load(demo)["prs"][fix])
    asked_why = f"its checks fail at 75e6d42: buildkite/test, and its builder waits on {fix_link}, which waits on his Approve on [his card]({fix_card})"
    assert state.load(demo)["prs"][red_pr]["stage"]["why"] == asked_why
    for minutes in (46, 76, 106):
        at(minutes)
    approve(demo, red_pr)
    approve(demo, fix)
    at(144)
    reads[fix] = read(state="MERGED", mergeCommit="ddd444", headRefOid=heads[fix], title=titles[fix], checks=GREEN, reviewDecision="APPROVED")
    at(150)
    assert passes == [
        (0, "red", stage.BUILDER, [red_pr]),
        (16, "blocked", stage.HIM, []),
        (46, "blocked", stage.HIM, []),
        (76, "blocked", stage.HIM, []),
        (106, "blocked", stage.HIM, []),
        (144, "blocked", "j2662", []),
        (150, "red", stage.BUILDER, [red_pr]),
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
    with edit(demo) as data:
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
        passes.append((minutes, [key for key, _ in woken if key in own]))
        woken.clear()

    at(0)
    with edit(demo) as data:
        state.take_result(data, "j1180", waiting)
    for minutes in (30, 60, 90):
        at(minutes)
    reads[own[0]] = read(headRefOid="ce6d321aaa", checks=failing)
    at(91)
    with edit(demo) as data:
        state.take_result(data, "j1180", waiting)
    for minutes in (92, 122, 152):
        at(minutes)
    kept = state.load(demo)["prs"][own[0]]["stage"]
    with edit(demo) as data:
        end(reads, data, other, release)
    at(153)
    assert passes == [(0, list(own)), (30, []), (60, []), (90, []), (91, [own[0]]), (92, []), (122, []), (152, []), (153, list(own))]
    named = view.pr_link(state.load(demo)["prs"][other])
    assert (kept["name"], kept["owner"], kept["why"]) == ("blocked", stage.ORDER, f"its checks fail at ce6d321: buildkite/test, and its builder waits on {release}, {named}")
    assert [state.load(demo)["prs"][key]["stage"]["name"] for key in own] == ["red", "red"]


def test_a_red_prs_builder_whose_waiting_names_only_its_own_prs_is_woken_every_thirty_minutes(demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]) -> None:
    serve(monkeypatch, {KEY: read(checks=RED)})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    with edit(demo) as data:
        state.take_result(data, "j1", f"WAITING readers-first on {KEY}")
    watch.run_pass(watching, T0 + timedelta(minutes=30))
    assert (state.load(demo)["prs"][KEY]["stage"]["name"], len(woken)) == ("red", 2)


def test_replayed_55591_a_red_pr_waiting_on_the_open_pr_its_builder_named_as_the_fix_wakes_no_one_until_that_pr_lands_or_its_head_moves(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    red_pr, fix = "Forge-AI/monorepo#55591", "Forge-AI/monorepo#55687"
    card_link, waits = "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794600000000001", "demo:j6448-1794600000000"
    asked = state.new_question(None, "May the oncall fix land under your release hold?", state.stamp(T0), card=card_link, holds=state.pr_url(fix)) | {"waits": waits}
    with edit(demo) as data:
        data["orders"]["o573"] = order(jobs=["j6428", "j6448"])
        data["jobs"] |= {"j6428": job(order="o573", prs=[state.pr_url(red_pr), state.pr_url(fix)]), "j6448": job(order="o573", state="waiting", said=asked["title"], questions=[asked])}
        for key, head in ((red_pr, "2806e35aaa"), (fix, "c6bad1caaa")):
            data["prs"][key] = state.new_pr(state.pr_url(key), "j6428", "o573") | {"head": head, "wants_tap": False, "checked": CHECKED | {"head": head, "orders": ["o573"]}}
        data["prs"][red_pr]["waits"] = [fix]
    monkeypatch.setattr(watch.view, "cards", lambda drive: NO_CARDS | {"questions": [question(waits) | {"url": card_link}]})
    failing = [{"name": "buildkite/test", "conclusion": "FAILURE"}]
    reads = {fix: read(headRefOid="c6bad1caaa", checks=GREEN, reviewDecision="APPROVED")}
    serve(monkeypatch, reads)
    waiting = f"WAITING {red_pr} waits on {state.pr_url(fix)}, whose stacked fix clears its eval gate"
    passes = []

    def at(minutes: int, head: str = "2806e35aaa") -> None:
        reads[red_pr] = read(headRefOid=head, checks=failing)
        watch.run_pass(watcher(demo), T0 + timedelta(minutes=minutes))
        kept = state.load(demo)["prs"][red_pr]["stage"]
        passes.append((minutes, kept["name"], kept["owner"], [key for key, _ in woken]))
        woken.clear()

    at(0)
    with edit(demo) as data:
        state.take_result(data, "j6428", waiting)
    at(30)
    behind_why = state.load(demo)["prs"][red_pr]["stage"]["why"]
    for minutes in (60, 90, 120, 150):
        at(minutes)
    at(151, head="31a7e01aaa")
    with edit(demo) as data:
        state.take_result(data, "j6428", waiting)
    at(152, head="31a7e01aaa")
    reads[fix] = read(state="MERGED", mergeCommit="ddd555", headRefOid="c6bad1caaa", checks=GREEN, reviewDecision="APPROVED")
    at(153, head="31a7e01aaa")
    assert behind_why == f"its checks fail at 2806e35: buildkite/test, which its builder waits on {fix} to fix, held: his cards {card_link} on its work are open"
    assert passes == [
        (0, "red", stage.BUILDER, [red_pr]),
        (30, "behind", stage.HIM, []),
        (60, "behind", stage.HIM, []),
        (90, "behind", stage.HIM, []),
        (120, "behind", stage.HIM, []),
        (150, "behind", stage.HIM, []),
        (151, "red", stage.BUILDER, [red_pr]),
        (152, "behind", stage.HIM, []),
        (153, "red", stage.BUILDER, [red_pr]),
    ]


def test_replayed_55571_a_red_pr_whose_builder_waits_on_its_own_open_question_wakes_it_once_per_red_and_once_his_answer_closes_the_question(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    red_pr, card_link, waits = "Forge-AI/monorepo#55571", "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794577515737969", "demo:j2268-1794577515737"
    url = state.pr_url(red_pr)
    asked = state.new_question(None, "What writes a workflow run that has no agent?", state.stamp(T0), card=card_link, holds=url) | {"waits": waits}
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j2268")
        data["jobs"]["j2268"] = job(prs=[url], state="waiting", said=asked["title"], questions=[asked])
        data["prs"][red_pr] = state.new_pr(url, "j2268", "o1") | {"head": "a2d5a98aaa", "wants_tap": True, "checked": CHECKED | {"head": "a2d5a98aaa"}}
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question(waits) | {"url": card_link}], "closed": [], "pr_cards": [pr_card(red_pr, head="a2d5a98aaa")]})
    reads: dict[str, dict] = {}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    waiting = f"WAITING {red_pr} waits on {card_link}"
    passes = []

    def at(minutes: int, **fetched: object) -> None:
        reads[red_pr] = read(**{"headRefOid": "a2d5a98aaa", "baseRefName": "dev", "default": "dev", "mergeable": "CONFLICTING"} | fetched)
        watch.run_pass(watching, T0 + timedelta(minutes=minutes))
        kept = state.load(demo)["prs"][red_pr]["stage"]
        passes.append((minutes, kept["name"], kept["owner"], [key for key, _ in woken]))
        woken.clear()

    at(0)
    with edit(demo) as data:
        state.take_result(data, "j2268", waiting)
    at(2)
    blocked_why = state.load(demo)["prs"][red_pr]["stage"]["why"]
    at(4, mergeable="UNKNOWN")
    at(6)
    at(40)
    at(42, headRefOid="f222db8aaa", mergeable="UNKNOWN")
    at(44, headRefOid="f222db8aaa")
    at(46, headRefOid="f222db8aaa", checks=RED)
    with edit(demo) as data:
        state.take_result(data, "j2268", waiting)
    at(48, headRefOid="f222db8aaa", checks=RED)
    with edit(demo) as data:
        data["jobs"]["j2268"]["questions"][-1]["closed"] = state.new_close("answered", "option 1", None, seq="9")
    at(50, headRefOid="f222db8aaa", checks=RED)
    assert blocked_why == f"it conflicts with its base dev, and its builder waits on his answer on [his question card]({card_link})"
    assert passes == [
        (0, "red", stage.BUILDER, [red_pr]),
        (2, "blocked", stage.HIM, []),
        (4, "blocked", stage.HIM, []),
        (6, "blocked", stage.HIM, []),
        (40, "blocked", stage.HIM, []),
        (42, "held", stage.HIM, []),
        (44, "blocked", stage.HIM, []),
        (46, "blocked", stage.HIM, []),
        (48, "blocked", stage.HIM, []),
        (50, "red", stage.BUILDER, [red_pr]),
    ]


@pytest.mark.parametrize("returned", ["ASK", "WAITING"])
def test_replayed_56316_a_pr_reworked_to_his_words_whose_builder_waits_on_its_open_question_reads_blocked_on_him_until_his_answer_or_new_words_wake_it_once(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], returned: str
) -> None:
    reworked, card_link, waits = "Forge-AI/monorepo#56316", "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794675864869599", "demo:j4783-1794675861699"
    url = state.pr_url(reworked)
    asked = state.new_question("/drive/asks/j4783-pool.md", "How does the staging test get a pool only a platform admin can create?", state.stamp(T0), card=card_link) | {"waits": waits}
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j4783")
        data["jobs"]["j4783"] = job(prs=[url], said=asked["title"], questions=[asked])
        data["prs"][reworked] = state.new_pr(url, "j4783", "o1") | {"head": "aaaaaaa111", "wants_tap": True, "checked": CHECKED}
        state.rework(data["prs"][reworked], "o2", "The point of using staging is not to hold it there", "https://acme.slack.com/archives/D123/p1794300000000600")
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question(waits) | {"url": card_link}], "closed": [], "pr_cards": []})
    serve(monkeypatch, {reworked: read(checks=GREEN)})
    watching = watcher(demo)
    line = f"WAITING {url} waits on {waits}" if returned == "WAITING" else f"ASK {asked['file']}"
    passes = []

    def at(minutes: int) -> None:
        watch.run_pass(watching, T0 + timedelta(minutes=minutes))
        kept = state.load(demo)["prs"][reworked]["stage"]
        passes.append((minutes, kept["name"], kept["owner"], [key for key, _ in woken]))
        woken.clear()

    def report() -> None:
        with edit(demo) as data:
            state.take_result(data, "j4783", line)

    at(0)
    report()
    at(2)
    blocked_why = state.load(demo)["prs"][reworked]["stage"]["why"]
    at(32)
    at(62)
    with edit(demo) as data:
        state.rework(data["prs"][reworked], "o2", "Run the staging test from its pipeline", "https://acme.slack.com/archives/D123/p1794300000000600")
    at(64)
    report()
    at(66)
    with edit(demo) as data:
        data["jobs"]["j4783"]["questions"][-1]["closed"] = state.new_close("answered", "option 1", None, seq="9")
    at(68)
    at(70)
    assert blocked_why == f'being reworked to his words "The point of using staging is not to hold it there", and its builder waits on his answer on [his question card]({card_link})'
    assert passes == [
        (0, "rework", stage.BUILDER, [reworked]),
        (2, "blocked", stage.HIM, []),
        (32, "blocked", stage.HIM, []),
        (62, "blocked", stage.HIM, []),
        (64, "rework", stage.BUILDER, [reworked]),
        (66, "blocked", stage.HIM, []),
        (68, "rework", stage.BUILDER, [reworked]),
        (70, "rework", stage.BUILDER, []),
    ]


def test_j4802_an_approved_pr_waiting_on_a_job_whose_open_question_no_wait_names_reads_blocked_on_his_answer_on_that_card(demo: str) -> None:
    with edit(demo) as data:
        data["orders"]["o2"] = order(jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", state="waiting", waits=[OTHER], questions=[state.new_question(None, "Which store?", state.stamp(T0), card=CARD) | {"waits": "demo:j2-1"}])
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o2")
        data["prs"][KEY] |= {"checks": "pass", "review": "approved", "waits": ["j2"]}
    approve(demo, KEY)
    assert stage.of(state.load(demo), KEY, NO_CARDS) == stage.Stage("blocked", stage.HIM, f"it waits on j2, his answer on {view.link('his question card', CARD)}")


@pytest.mark.parametrize(
    ("subject", "holds", "owner", "named"),
    [
        (OTHER, "", "j2", ""),
        (OTHER, state.pr_url(OTHER), stage.HIM, f", his answer on {view.link('his question card', CARD)}"),
        (KEY, "", stage.HIM, f"his answer on {view.link('his question card', CARD)}"),
    ],
)
def test_a_waiting_line_saying_another_jobs_pr_waits_on_his_card_binds_that_card_to_that_pr_never_to_the_jobs_own_red_pr(
    demo: str, subject: str, holds: str, owner: str, named: str
) -> None:
    waits = "demo:j2-1794700000000"
    with edit(demo) as data:
        data["orders"]["o2"] = order(jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", state="waiting", prs=[state.pr_url(OTHER)], questions=[state.new_question(None, "Which store?", state.stamp(T0), card=CARD, holds=holds) | {"waits": waits}])
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o2") | {"head": "bbbbbbb222"}
        data["prs"][KEY] |= {"checks": "fail", "failed_checks": ["buildkite/test"]}
        state.take_result(data, "j1", f"WAITING {state.pr_url(subject)} waits on {waits}")
    data = state.load(demo)
    waited_on = "" if subject == KEY else view.pr_link(data["prs"][OTHER])
    assert stage.of(data, KEY, NO_CARDS) == stage.Stage("blocked", owner, f"its checks fail at aaaaaaa: buildkite/test, and its builder waits on {waited_on}{named}")
    assert (waits in data["jobs"]["j1"]["waits"]) == (subject == KEY)


def test_a_said_line_set_by_hand_that_drops_his_card_moves_the_jobs_red_pr_off_him_at_once(demo: str) -> None:
    waits = "demo:j2-1794700000000"
    with edit(demo) as data:
        data["orders"]["o2"] = order(jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", state="waiting", prs=[state.pr_url(OTHER)], questions=[state.new_question(None, "Which store?", state.stamp(T0), card=CARD, holds="") | {"waits": waits}])
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o2") | {"head": "bbbbbbb222"}
        data["prs"][KEY] |= {"checks": "fail", "failed_checks": ["buildkite/test"]}
        state.take_result(data, "j1", f"WAITING {state.pr_url(KEY)} waits on {waits}")
    assert stage.of(state.load(demo), KEY, NO_CARDS).owner == stage.HIM
    assert cli.main(["job", "set", "j1", "--said", f"WAITING {state.pr_url(OTHER)}", "--drive", demo]) == 0
    data = state.load(demo)
    assert stage.of(data, KEY, NO_CARDS) == stage.Stage("blocked", "j2", f"its checks fail at aaaaaaa: buildkite/test, and its builder waits on {view.pr_link(data['prs'][OTHER])}")


def test_his_pr_card_is_redrawn_whole_once_when_its_stage_owner_moves_off_him(demo: str, redraws: Redraws) -> None:
    waits = "demo:j2-1794700000000"
    with edit(demo) as data:
        data["orders"]["o2"] = order(jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", state="waiting", prs=[state.pr_url(OTHER)], questions=[state.new_question(None, "Which store?", state.stamp(T0), card=CARD, holds="") | {"waits": waits}])
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o2") | {"head": "bbbbbbb222"}
        data["prs"][KEY] |= {"checks": "fail", "failed_checks": ["buildkite/test"], "required": ["buildkite/test"]}
        state.take_result(data, "j1", f"WAITING {state.pr_url(KEY)} waits on {waits}")
    watching = carded(demo, pr_card(KEY, url="https://acme.slack.com/archives/D123/p1794200000000200"))
    watch.stage_lines(watching, T0)
    assert (state.load(demo)["prs"][KEY]["stage"]["owner"], redraws.asked) == (stage.HIM, [])
    assert cli.main(["job", "set", "j1", "--said", f"WAITING {state.pr_url(OTHER)}", "--drive", demo]) == 0
    for minutes in (1, 2):
        watch.stage_lines(watching, T0 + timedelta(minutes=minutes))
        assert (state.load(demo)["prs"][KEY]["stage"]["owner"], redraws.asked) == ("j2", [state.pr_url(KEY)])


def test_a_pr_waits_only_on_its_own_staging_test_never_on_one_another_pr_of_its_job_waits_on_as_56813_did(demo: str) -> None:
    part = state.STAGING_TEST.format(number=42)
    with edit(demo) as data:
        data["orders"]["o1"]["parts"][part] = state.new_part()
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(goal="run it twice on staging", parts=[part], builds_nothing=True)
        data["jobs"]["j1"].update(state="waiting", said=f"PUSHED {state.pr_url(OTHER)}", prs=[state.pr_url(KEY), state.pr_url(OTHER)])
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"head": "bbbbbbb222", "checks": "pass", "review": "approved"}
        data["prs"][KEY] |= {"waits": [f"o1:{part}"]}
    data = state.load(demo)
    assert (f"o1:{part}" in state.job_waits(data, data["prs"][KEY]), state.job_waits(data, data["prs"][OTHER])) == (True, [])


@pytest.mark.parametrize("freed", [{"state": "merged"}, {"state": "closed"}, {"waits": [YASYF]}], ids=["it merged", "it closed", "it waits on the yasyf PR"])
def test_a_reported_yasyf_pr_waits_on_each_open_forge_ai_pr_of_its_job_and_lands_once_that_pr_merges_closes_or_waits_on_it_as_5530_shipped_hours_before_56740(demo: str, freed: dict) -> None:
    cards = NO_CARDS | {"pr_cards": [pr_card(KEY)]}
    with edit(demo) as data:
        data["prs"][KEY] |= {"wants_tap": True, "checks": "pass"}
        data["prs"][YASYF] = state.new_pr(state.pr_url(YASYF), "j1", "o1") | {"head": "aaaaaaa111", "base": "main", "checks": "pass", "wants_tap": False, "checked": CHECKED}
        data["jobs"]["j1"]["prs"].append(state.pr_url(YASYF))
        state.take_result(data, "j1", f"READY {state.pr_url(YASYF)}")
    data = state.load(demo)
    held = stage.Stage("blocked", stage.HIM, f"it waits on {view.pr_link(data['prs'][KEY])}, which waits on his Approve on {view.link('his card', CARD)}")
    assert (stage.of(data, YASYF, cards), land.units(data, [YASYF], cards)) == (held, [])
    with edit(demo) as data:
        data["prs"][KEY] |= freed
    data = state.load(demo)
    ready = stage.Stage("ready", stage.LANDER, "its builder reported it and every check at aaaaaaa finished, so the lander merges it with --admin unless one fails on its own change")
    assert (stage.of(data, YASYF, cards), land.units(data, [YASYF], cards)) == (ready, [(YASYF,)])


@pytest.mark.parametrize(
    ("waits", "said", "owner"),
    [
        ([OTHER], f"WAITING {state.pr_url(OTHER)} to merge: then it soaks both on staging", "j4542"),
    ],
    ids=["its tester waits on the other PR it tests"],
)
def test_replayed_56550_an_approved_pr_waiting_only_on_its_staging_tester_reads_waiting_on_that_run_never_blocked_on_his_approve_of_the_tester_s_other_pr(
    demo: str, waits: list[str], said: str, owner: str
) -> None:
    with edit(demo) as data:
        data["orders"]["o2"] = order(jobs=["j4542"])
        data["jobs"]["j4542"] = job(order="o2", state="waiting", goal="soak #42 and #43 on staging", builds_nothing=True, waits=waits, said=said)
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"head": "aaaaaaa111", "checks": "pass", "review": "approved", "waits": ["j4542"]}
        data["prs"][KEY] |= {"checks": "pass", "review": "approved", "waits": ["j4542"]}
    approve(demo, KEY)
    data, cards = state.load(demo), NO_CARDS | {"pr_cards": [pr_card(OTHER)]}
    assert stage.of(data, OTHER, cards).owner == stage.HIM
    assert stage.of(data, KEY, cards) == stage.Stage("waiting", owner, f"it waits on its staging run by j4542, {said}")


@pytest.mark.parametrize("looped", [False, True], ids=["the stale wait leads to an idle PR", "the stale wait loops back through its own tester and a second tester waits too"])
def test_replayed_720960_a_pr_waiting_on_its_staging_test_is_owed_by_that_tester_never_by_where_the_testers_own_stale_wait_leads(demo: str, looped: bool) -> None:
    tested, shared, stale = "Forge-AI/monorepo#53471", ["Forge-AI/monorepo#56326", "Forge-AI/monorepo#56329"], "Forge-AI/monorepo#53304"
    part = {key: state.STAGING_TEST.format(number=key.split("#")[1]) for key in [tested, stale, *shared]}
    with edit(demo) as data:
        data["orders"]["o1513"] = order(jobs=["j4830", "j4831", "j4832"], parts={part[tested]: state.new_part(), **({part[stale]: state.new_part()} if looped else {})})
        data["orders"]["o143"] = order(jobs=["j4486", "j4487", *(["j6720"] if looped else [])], parts={part[key]: state.new_part() for key in shared})
        if looped:
            data["jobs"]["j4830"] = job(order="o1513", goal="test #53304 on staging", parts=[part[stale]], builds_nothing=True, state="waiting", waits=[tested], said=f"WAITING {state.pr_url(tested)} to take the slot after it")
            data["jobs"]["j6720"] = job(order="o143", goal="take over j4486's staging tests", parts=[part[key] for key in shared], builds_nothing=True, state="waiting", waits=[tested], said=f"WAITING {state.pr_url(tested)} for the next cut")
        else:
            data["jobs"]["j4830"] = job(order="o1513", prs=[state.pr_url(stale)])
        data["jobs"]["j4831"] = job(order="o1513", goal="test #53471 on staging", parts=[part[tested]], builds_nothing=True, state="waiting", waits=[stale], said=f"WAITING {state.pr_url(stale)} for the one staging slot")
        data["jobs"]["j4832"] = job(order="o1513", prs=[state.pr_url(tested)])
        data["jobs"]["j4486"] = job(order="o143", goal="test #56326 and #56329 on staging", parts=[part[key] for key in shared], builds_nothing=True, state="waiting", waits=[tested], said=f"WAITING {state.pr_url(tested)} to share its cut")
        data["jobs"]["j4487"] = job(order="o143", prs=[state.pr_url(key) for key in shared])
        data["prs"][stale] = state.new_pr(state.pr_url(stale), "j4830", "o1513") | {"head": "aaaaaaa111", **({"waits": [f"o1513:{part[stale]}"]} if looped else {})}
        data["prs"][tested] = state.new_pr(state.pr_url(tested), "j4832", "o1513") | {"head": "aaaaaaa111", "checks": "pass", "review": "approved", "waits": [f"o1513:{part[tested]}"]}
        for key in shared:
            data["prs"][key] = state.new_pr(state.pr_url(key), "j4487", "o143") | {"head": "aaaaaaa111", "checks": "pass", "review": "approved", "waits": [f"o143:{part[key]}"]}
    approve(demo, tested, *shared)
    data = state.load(demo)
    owners = {key: (now.name, now.owner, now.why.split(", ")[0]) for key in [tested, *shared] if (now := stage.of(data, key, NO_CARDS))}
    assert owners == {
        tested: ("waiting", "j4831", "it waits on its staging run by j4831"),
        shared[0]: ("waiting", "j4486", "it waits on its staging run by j4486"),
        shared[1]: ("waiting", "j4486", "it waits on its staging run by j4486"),
    }


@pytest.mark.parametrize("older", [False, True], ids=["replaced now", "replaced under an older install"])
def test_replayed_722384_a_pr_waiting_on_its_staging_test_is_owed_by_the_live_tester_that_replaced_a_failed_one_never_the_stopped_one(demo: str, older: bool) -> None:
    part = state.STAGING_TEST.format(number=KEY.split("#")[1])
    with edit(demo) as data:
        data["orders"]["o2"] = order(jobs=["j5559", "j6760"], parts={part: state.new_part()})
        data["jobs"]["j5559"] = job(order="o2", goal="test #42 on staging", parts=[part], state="stopped" if older else "failed", said="replaced by j6760" if older else "BLOCKED the soak never served")
        data["jobs"]["j6760"] = job(order="o2", goal="test #42 on staging again", parts=[part])
        data["jobs"]["j2"] = job(goal="build on the soak", state="waiting", waits=["j5559"])
        data["orders"]["o1"]["jobs"].append("j2")
        data["prs"][KEY] |= {"checks": "pass", "review": "approved", "waits": [f"o2:{part}"]}
        if older:
            del data["stopped_handed_off"]
    if not older:
        with edit(demo) as data:
            state.end_replaced(data, ["o2"], state.stamp(T0))
    approve(demo, KEY)
    data = state.load(demo)
    now = stage.of(data, KEY, NO_CARDS)
    assert (now.name, now.owner, now.why.split(", ")[0]) == ("waiting", "j6760", "it waits on its staging run by j6760")
    assert (data["jobs"]["j5559"]["state"], data["jobs"]["j5559"]["parts"], data["jobs"]["j5559"]["carries"], data["jobs"]["j2"]["waits"]) == ("stopped", [], {}, ["j6760"])


@pytest.mark.parametrize("named", [True, False], ids=["its waits name his cards", "no wait names his cards"])
def test_replayed_56278_an_approved_pr_whose_waits_end_at_his_open_cards_reads_blocked_on_him_over_an_older_unlanded_try_and_wakes_no_one_until_they_close(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], landers: list[tuple[str, ...]], named: bool
) -> None:
    pr, release = "Forge-AI/monorepo#56278", "release:cloudrunner-next:Forge-AI/monorepo#56278"
    links = {
        "demo:j4413-1794643689439": "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794643689439001",
        "demo:j4413-1794644613322": "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794644613322001",
    }
    tried = {"head": "aaaaaaa111", "pid": 1, "at": state.stamp(T0 + timedelta(minutes=3)), "outcome": "not landed: Forge-AI/monorepo#56232 sits below it with neither his Approve nor an exempt class", "below": ["Forge-AI/monorepo#56232"]}
    with edit(demo) as data:
        data["orders"]["o2"] = order(jobs=["j4413"])
        data["jobs"]["j1"]["prs"].append(state.pr_url(pr))
        data["jobs"]["j4413"] = job(order="o2", state="waiting", waits=[pr, release, *(links if named else [])], questions=[state.new_question(None, f"Pick {waits}?", state.stamp(T0), card=link) | {"waits": waits} for waits, link in links.items()])
        data["prs"][pr] = state.new_pr(state.pr_url(pr), "j1", "o1") | {"head": "aaaaaaa111", "base": "main", "checked": CHECKED, "waits": ["j4413"], "lander": tried}
    approve(demo, pr)
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question(waits) | {"url": link} for waits, link in links.items()], "closed": [], "pr_cards": [pr_card(pr)]})
    serve(monkeypatch, {pr: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    passes = []

    def at(minutes: int) -> None:
        said = [line for line in watch.run_pass(watching, T0 + timedelta(minutes=minutes)) if line.startswith(f"STAGE {pr}")]
        kept = state.load(demo)["prs"][pr]["stage"]
        passes.append((minutes, kept["name"], kept["owner"], said, [key for key, _ in woken]))
        woken.clear()

    for minutes in (5, 35, 64):
        at(minutes)
    kept = state.load(demo)["prs"][pr]
    assert kept["stage"]["why"] == "it waits on j4413, " + ", ".join(f"his answer on {view.link('his question card', link)}" for link in links.values())
    assert card.picks(state.load(demo), kept, view.live_cards(demo)) == {}
    found = view.live_cards(demo)
    assert stall.chain(state.load(demo), found, False, pr, set()) is None
    with edit(demo) as data:
        data["jobs"]["j4413"]["state"] = "running"
        assert stage.of(data, pr, found) == stage.Stage("waiting", "j4413", "it waits on j4413, and the watch frees it once each ends")
        data["jobs"]["j4413"]["state"] = "waiting"
        for asked in data["jobs"]["j4413"]["questions"]:
            asked["closed"] = state.new_close("answered", "Yes", None, seq="9")
    at(65)
    unlanded = (
        f"STAGE {pr} unlanded since {state.stamp(T0 + timedelta(minutes=65))}, owed by the session holding its order: its lander's try at aaaaaaa, started {tried['at']},"
        f" ended {tried['outcome']} [o1 j1]"
    )
    assert passes == [
        (5, "blocked", stage.HIM, [], []),
        (35, "blocked", stage.HIM, [], []),
        (64, "blocked", stage.HIM, [], []),
        (65, "unlanded", stage.ORDER, [unlanded], []),
    ]
    assert landers == []


def test_replayed_56278_an_unlanded_pr_whose_own_job_waits_on_his_open_cards_reads_blocked_on_him_and_raises_no_stall_until_he_answers(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], landers: list[tuple[str, ...]]
) -> None:
    pr = "Forge-AI/monorepo#56278"
    links = {
        "demo:j4413-1794644613322": "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794644616374239",
        "demo:j4413-1794651715431": "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794651717356579",
    }
    tried = {"head": "aaaaaaa111", "pid": 1, "at": state.stamp(T0 + timedelta(minutes=3)), "outcome": "not landed: Forge-AI/monorepo#56232 sits below it with neither his Approve nor an exempt class", "below": ["Forge-AI/monorepo#56232"]}
    with edit(demo) as data:
        data["orders"]["o2"] = order(jobs=["j4413"])
        data["jobs"]["j4413"] = job(
            order="o2",
            state="waiting",
            prs=[state.pr_url(pr)],
            said=f"WAITING {state.pr_url(pr)} waits on {', '.join(links)}",
            waits=list(links),
            questions=[state.new_question(None, f"Pick {waits}?", state.stamp(T0), card=link) | {"waits": waits} for waits, link in links.items()],
        )
        data["prs"][pr] = state.new_pr(state.pr_url(pr), "j4413", "o2") | {"head": "aaaaaaa111", "base": "main", "checked": CHECKED, "lander": tried}
    approve(demo, pr)
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [question(waits) | {"url": link} for waits, link in links.items()], "closed": [], "pr_cards": [pr_card(pr)]})
    serve(monkeypatch, {pr: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    passes = []

    def at(minutes: int) -> None:
        said = [line for line in watch.run_pass(watching, T0 + timedelta(minutes=minutes)) if line.startswith((f"STAGE {pr}", "STUCK"))]
        kept = state.load(demo)["prs"][pr]
        passes.append((minutes, kept["stage"]["name"], kept["stage"]["owner"], said, [key for key, _ in woken]))
        woken.clear()

    for minutes in (5, 35, 64, 95):
        at(minutes)
    assert pr not in state.load(demo)["stalls"]
    assert state.load(demo)["prs"][pr]["stage"]["why"] == "its builder j4413 waits on " + ", ".join(f"his answer on {view.link('his question card', link)}" for link in links.values())
    assert stall.chain(state.load(demo), view.live_cards(demo), False, pr, set()) is None
    with edit(demo) as data:
        for asked in data["jobs"]["j4413"]["questions"]:
            asked["closed"] = state.new_close("answered", "Yes", None, seq="9")
    at(96)
    unlanded = (
        f"STAGE {pr} unlanded since {state.stamp(T0 + timedelta(minutes=96))}, owed by the session holding its order: its lander's try at aaaaaaa, started {tried['at']},"
        f" ended {tried['outcome']} [o2 j4413]"
    )
    assert passes == [
        (5, "blocked", stage.HIM, [], []),
        (35, "blocked", stage.HIM, [], []),
        (64, "blocked", stage.HIM, [], []),
        (95, "blocked", stage.HIM, [], []),
        (96, "unlanded", stage.ORDER, [unlanded], []),
    ]
    assert landers == []


def test_a_test_a_yasyf_merges_own_run_broke_on_main_wakes_its_builder_until_main_passes_it(demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]) -> None:
    merged, run, test = "yasyf/cc-skills#5", "https://github.com/yasyf/cc-skills/actions/runs/9", "plugins/long-running/tests/test_watch.py::test_a"
    with edit(demo) as data:
        data["orders"]["o3"] = order(jobs=["j3"])
        data["jobs"]["j3"] = job(order="o3", prs=[state.pr_url(merged)], state="done")
        data["prs"][merged] = state.new_pr(state.pr_url(merged), "j3", "o3") | {"state": "merged", "head": "bbbbbbb222", "merged_sha": "ccc333ddd444", "checks": "fail"}
    since, marks, main = "2026-11-09T14:50:00Z", {}, {"after": None}
    monkeypatch.setattr(receiver, "heard", lambda repo, kind: marks.get(repo))
    monkeypatch.setattr(install, "head_failures", lambda repo, head: {test: {"run": run, "workflow": 1}})
    monkeypatch.setattr(install, "merged_at", lambda repo, sha: since)
    monkeypatch.setattr(install, "main_failures", lambda repo, workflow, created: [] if created == f"<{since}" else main["after"])
    monkeypatch.setattr(land, "base_failures", lambda repo, run: [])
    serve(monkeypatch, {})
    watching, passes = watcher(demo), []
    for minutes, mark, after in ((0, "m1", None), (10, "m1", None), (30, "m1", None), (31, "m2", [])):
        marks["yasyf/cc-skills"], main["after"] = mark, after
        watch.run_pass(watching, T0 + timedelta(minutes=minutes))
        passes.append((minutes, [wake for wake in woken if wake[0] == merged]))
        woken.clear()
    fix = f"{merged}: yasyf/cc-skills's main fails {test} since it merged, as its own run {run} failed them; fix them forward in a PR of your own from a fresh main, since no other builder is asked to"
    assert passes == [(0, [(merged, fix)]), (10, []), (30, [(merged, fix)]), (31, [])]
    assert state.load(demo)["mains"] == {"yasyf/cc-skills": {}}


def test_a_merge_owns_only_the_tests_its_own_run_failed_that_main_passed_before_it_and_has_not_passed_since(monkeypatch: pytest.MonkeyPatch) -> None:
    run = "https://github.com/yasyf/cc-skills/actions/runs/9"
    monkeypatch.setattr(install, "head_failures", lambda repo, head: {test: {"run": run, "workflow": 1} for test in ("t.py::old", "t.py::fixed", "t.py::new")})
    monkeypatch.setattr(install, "merged_at", lambda repo, sha: "2026-11-09T14:50:00Z")
    monkeypatch.setattr(install, "main_failures", lambda repo, workflow, created: ["t.py::old"] if created.startswith("<") else ["t.py::old", "t.py::new"])
    monkeypatch.setattr(land, "base_failures", lambda repo, run: [])
    pr = state.new_pr(state.pr_url("yasyf/cc-skills#5"), "j3", "o3") | {"state": "merged", "head": "bbbbbbb222", "merged_sha": "ccc333ddd444"}
    assert watch.broken("yasyf/cc-skills", pr | {"checks": "fail"}) == {"t.py::new": {"run": run, "workflow": 1, "since": "2026-11-09T14:50:00Z"}}
    assert watch.broken("yasyf/cc-skills", pr | {"checks": "pass"}) == {}


def test_a_merge_over_a_red_main_owns_no_test_mains_run_at_its_base_already_failed_as_6056_was_blamed_for_36cf915ecs_reds(monkeypatch: pytest.MonkeyPatch) -> None:
    run = "https://github.com/yasyf/cc-skills/actions/runs/43861127742"
    monkeypatch.setattr(install, "head_failures", lambda repo, head: {test: {"run": run, "workflow": 1} for test in ("t.py::red_at_base", "t.py::new")})
    monkeypatch.setattr(install, "merged_at", lambda repo, sha: "2026-11-16T11:40:00Z")
    monkeypatch.setattr(install, "main_failures", lambda repo, workflow, created: [] if created.startswith("<") else ["t.py::red_at_base", "t.py::new"])
    monkeypatch.setattr(land, "base_failures", lambda repo, run: ["t.py::red_at_base"])
    pr = state.new_pr(state.pr_url("yasyf/cc-skills#6056"), "j6739", "o1") | {"state": "merged", "head": "c8248da507", "merged_sha": "8e1f00aa", "checks": "fail"}
    assert list(watch.broken("yasyf/cc-skills", pr)) == ["t.py::new"]


@pytest.mark.parametrize(("main", "owned"), [("FAILURE", ["t.py::new"]), ("SUCCESS", ["t.py::new", "t.py::red_at_base"])], ids=["main-below-the-skip-ci-base-fails-it", "main-below-the-skip-ci-base-passes-it"])
def test_a_merge_owns_no_test_main_failed_below_the_skip_ci_base_its_run_merged_onto_as_6057_was_handed_8_of_636fe7701s_reds(monkeypatch: pytest.MonkeyPatch, main: str, owned: list[str]) -> None:
    run, base = f"{CC_SKILLS_RUNS}/43864078576", "7b4ec2094b0572bbd7e07a803ace1bd8442f72ff"
    answers = {
        "repos/yasyf/cc-skills/actions/runs/43864078576/jobs?filter=latest&per_page=100": json.dumps({"id": 1, "workflow_name": "long-running CI"}),
        "repos/yasyf/cc-skills/actions/jobs/1/logs": f"2026-11-16T12:10:31.0000000Z HEAD is now at b8b201b Merge a33a3441c{'0' * 31} into {base}\n{pytest_failed('t.py::red_at_base', 't.py::new')}",
        "repos/yasyf/cc-skills/actions/jobs/2/logs": pytest_failed("t.py::red_at_base") if main == "FAILURE" else "",
    }

    def gh(cmd: list[str], **_: object) -> str:
        if cmd[2] == "graphql":
            assert f'object(oid: "{base}")' in cmd[-1]
            return branch_history([("skip ci", None), ("ran", main)])
        return answers[cmd[2]]

    monkeypatch.setattr(github, "run", gh)
    monkeypatch.setattr(install, "head_failures", lambda repo, head: {test: {"run": run, "workflow": 366540480} for test in ("t.py::red_at_base", "t.py::new")})
    monkeypatch.setattr(install, "merged_at", lambda repo, sha: "2026-11-16T12:29:44Z")
    monkeypatch.setattr(install, "main_failures", lambda repo, workflow, created: [] if created.startswith("<") else ["t.py::red_at_base", "t.py::new"])
    pr = state.new_pr(state.pr_url("yasyf/cc-skills#6057"), "j339", "o125") | {"state": "merged", "head": "a33a3441c8", "merged_sha": "55970ea8d", "checks": "fail"}
    assert sorted(watch.broken("yasyf/cc-skills", pr)) == owned


def test_a_merge_owns_no_test_while_mains_last_run_before_it_names_none_as_6041_was_handed_the_166_main_failed_already(monkeypatch: pytest.MonkeyPatch) -> None:
    run = "https://github.com/yasyf/cc-skills/actions/runs/43837881567"
    monkeypatch.setattr(install, "head_failures", lambda repo, head: {test: {"run": run, "workflow": 1} for test in ("t.py::old", "t.py::new")})
    monkeypatch.setattr(install, "merged_at", lambda repo, sha: "2026-11-16T09:19:59Z")
    monkeypatch.setattr(install, "main_failures", lambda repo, workflow, created: None if created.startswith("<") else ["t.py::old", "t.py::new"])
    monkeypatch.setattr(land, "base_failures", lambda repo, run: [])
    pr = state.new_pr(state.pr_url("yasyf/cc-skills#6041"), "j334", "o36") | {"state": "merged", "head": "55b16b5a9f47", "merged_sha": "323748450b82", "checks": "fail"}
    assert watch.broken("yasyf/cc-skills", pr) == {}


def test_a_test_main_fails_has_one_fixer_the_first_job_to_claim_it_and_every_other_main_fix_or_merge_is_refused_naming_it(demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]) -> None:
    repo, run, test, at = "yasyf/cc-skills", "https://github.com/yasyf/cc-skills/actions/runs/9", "plugins/long-running/tests/test_watch.py::test_replayed_5076_a", state.stamp(T0)
    fix, rival, merged = "yasyf/cc-skills#5715", "yasyf/cc-skills#5716", "yasyf/cc-skills#5684"
    failing = {test: {"run": run, "workflow": 1}}
    with edit(demo) as data:
        data["orders"]["o3"] = order(jobs=["j3", "j4", "j5"])
        for job_id in ("j3", "j4", "j5"):
            data["jobs"][job_id] = job(order="o3", state="done")
        data["prs"][fix] = state.new_pr(state.pr_url(fix), "j3", "o3") | {"exempt": state.MAIN_FIX}
        data["prs"][rival] = state.new_pr(state.pr_url(rival), "j4", "o3") | {"exempt": state.MAIN_FIX}
        data["prs"][merged] = state.new_pr(state.pr_url(merged), "j5", "o3") | {"state": "merged", "head": "bbbbbbb222", "merged_sha": "ccc333ddd444", "checks": "fail"}
        assert state.claim_mains(data, repo, "j3", failing, None, at) == ""
        assert state.claim_mains(data, repo, "j3", failing, fix, at) == ""
        refused = state.claim_mains(data, repo, "j4", failing, rival, at)
    assert refused == (
        f"{repo}'s main fails only tests another job already owns: {test}, claimed by j3 on {state.pr_url(fix)} since {at}."
        " The first job to claim a failing test owns it until main passes it, so build no second fix: return WAITING naming that job's fix, or send its holder what you found"
    )
    assert state.load(demo)["mains"][repo] == {test: {"run": run, "workflow": 1, "since": at, "owner": fix, "job": "j3", "woke": at, "waking": None}}
    monkeypatch.setattr(receiver, "heard", lambda repo, kind: None)
    monkeypatch.setattr(install, "head_failures", lambda repo, head: failing)
    monkeypatch.setattr(install, "merged_at", lambda repo, sha: "2026-11-09T14:50:00Z")
    monkeypatch.setattr(install, "main_failures", lambda repo, workflow, created: [] if created.startswith("<") else [test])
    monkeypatch.setattr(land, "base_failures", lambda repo, run: [])
    watching = watcher(demo)
    for minutes in (0, 31, 62):
        watch.main_lines(watching, T0 + timedelta(minutes=minutes))
    assert woken == []
    assert state.load(demo)["prs"][merged]["graded"] is True
    assert state.load(demo)["mains"][repo][test]["owner"] == fix
    with edit(demo) as data:
        data["jobs"]["j3"]["state"] = "stopped"
        assert state.claim_mains(data, repo, "j4", failing, rival, at) == ""
    assert state.load(demo)["mains"][repo][test] == {"run": run, "workflow": 1, "since": at, "owner": rival, "job": "j4", "woke": at, "waking": None}


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
    watching = watcher(demo)
    assert [watch.sent(watching, "root", [line], T0 + n * watch.COALESCE) for n, line in enumerate(waking + batched)] == [[]] * len(waking + batched)
    assert posted(sent) == [("handoff", f"root@{demo}", line) for line in waking] + [("evidence", f"root@{demo}", "EVENT note")]
    assert len({argv[argv.index("--topic") + 1] for argv in sent}) == len(sent)


def test_a_line_said_to_a_lane_is_said_once_however_often_a_pass_makes_it_again_its_elapsed_minutes_aside(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = posts(monkeypatch)
    watching = watcher(demo)
    lines = [f'ORPHAN o1 "ship": j1\'s agent stopped {minutes} minutes ago' for minutes in (5, 10, 15)]
    assert [watch.sent(watching, lane, [line], T0 + timedelta(minutes=n)) for n, line in enumerate(lines) for lane in ("root", "meta")] == [[]] * 6
    assert posted(sent) == [("handoff", f"root@{demo}", lines[0]), ("handoff", f"meta@{demo}", lines[0])]
    watch.sent(watching, "root", [lines[0]], T0 + watch.SAID_FOR)
    assert posted(sent)[-1] == ("handoff", f"root@{demo}", lines[0])


def test_a_line_waits_on_its_watcher_no_longer_than_coalesce_however_often_another_watcher_of_the_drive_posts_its_lane(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = posts(monkeypatch)
    landing, watching = watcher(demo), watcher(demo)
    asked = "JOB j1 o1 j1 build request: please start buildkite/test on 08c39d02"
    watch.sent(landing, "root", ["EVENT one"], T0)
    assert watch.sent(watching, "root", [asked], T0 + timedelta(minutes=1)) == []
    watch.sent(landing, "root", ["EVENT two"], T0 + watch.COALESCE)
    watch.sent(watching, "root", [], T0 + watch.COALESCE + timedelta(minutes=1))
    assert [line for _, _, line in posted(sent)] == ["EVENT one", "EVENT two", asked]


def test_a_line_a_watch_killed_with_no_hand_off_left_unposted_goes_out_from_the_watch_after_it(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = posts(monkeypatch)
    killed = watch.Watcher(demo, offset=state.events_end(demo), outbox=watch.WATCH_OUTBOX)
    watch.sent(killed, "root", ["EVENT one"], T0)
    watch.sent(killed, "root", ["EVENT two"], T0 + timedelta(minutes=1))
    watch.outboxed(killed)
    after = watch.outboxed(watch.Watcher(demo, offset=state.events_end(demo), made=watch.taken_lines(demo, None, watch.WATCH_OUTBOX), outbox=watch.WATCH_OUTBOX))
    watch.sent(after, "root", after.made, T0 + watch.COALESCE)
    after.made = []
    watch.outboxed(after)
    assert ([line for _, _, line in posted(sent)], state.outbox_path(demo, None, watch.WATCH_OUTBOX).exists()) == (["EVENT one", "EVENT two"], False)


def test_the_lines_one_stage_makes_for_a_lane_go_as_one_record_counting_them_by_kind_its_body_holding_each_and_a_lane_message_named_whole_under_its_line(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = posts(monkeypatch)
    orphans = [f'ORPHAN o1 "part {n}": no job' for n in range(35)]
    checks = ["EVENT one", "EVENT two"]
    later = ["EVENT three", "EVENT four"]
    monkeypatch.setattr(watch, "whole_message", lambda name, line: "the status\nsaid whole" if line in (checks[0], later[0]) else None)
    watching = watcher(demo)
    assert (watch.sent(watching, "root", orphans + checks, T0), watch.sent(watching, "root", later, T0 + watch.COALESCE)) == ([], [])
    assert [(argv[argv.index("--kind") + 1], argv[argv.index("--text") + 1], argv[argv.index("--path") + 1]) for argv in sent] == [
        ("handoff", "37 lines: 35 ORPHAN, 2 EVENT", "".join(f"{line}\n" for line in orphans) + f"{checks[0]}\n  the status\n  said whole\n{checks[1]}\n"),
        ("evidence", "2 lines: 2 EVENT", f"{later[0]}\n  the status\n  said whole\n{later[1]}\n"),
    ]


@pytest.mark.parametrize(
    ("standing", "job_state", "said", "clause", "kind"),
    [
        (True, "stopped", "HANDOFF /drive/scratch/j2/handoff.md: box i-0ee4 under o2-codex-box, round 4 ran, staging untested", "", None),
        (True, "done", "DONE round 4 ran on box i-0ee4; round 5 starts at staging", "", None),
        (True, "failed", "BLOCKED codex-ask failed: its log is empty", "", "handoff"),
        (True, "stopped", "HANDOFF /drive/scratch/j2/handoff.md: round 4 ran", f"; {watch.UNKEPT_ASK.format(job='j2')}", "handoff"),
        (False, "stopped", "HANDOFF /drive/scratch/j2/handoff.md: round 4 ran", "", "handoff"),
    ],
)
def test_a_standing_pass_ending_its_round_wakes_no_coordinator_since_the_watch_alone_starts_the_next_while_its_block_or_a_clause_the_drive_adds_still_wakes_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, standing: bool, job_state: str, said: str, clause: str, kind: str | None
) -> None:
    sent = posts(monkeypatch)
    with edit(demo) as data:
        standing_order(data, ["j2"])
        if not standing:
            data["orders"]["o2"]["parts"]["codex test box"]["standing"] = None
        data["jobs"]["j2"] = job(order="o2", kind="codex", goal=BOX_GOAL, parts=["codex test box"], state=job_state, said=said)
    line = f"JOB j2 o2 {said}{clause}"
    assert watch.sent(watcher(demo), "root", [line], T0) == []
    assert posted(sent) == ([(kind, f"root@{demo}", line)] if kind else [])


@pytest.mark.parametrize(("job_state", "merged_kind"), [("running", "handoff"), ("waiting", "handoff"), ("done", None)])
def test_a_prs_red_wakes_no_coordinator_since_its_stage_wakes_its_builder_and_its_merge_wakes_it_until_its_job_is_done(
    demo: str, monkeypatch: pytest.MonkeyPatch, job_state: str, merged_kind: str | None
) -> None:
    with edit(demo) as data:
        data["jobs"]["j1"]["state"] = job_state
    sent = posts(monkeypatch)
    merged = f"PR {KEY}: merged [o1 j1]"
    reds = [f"PR {KEY}: {move} [o1 j1]" for move in ("checks failed", "conflicts with its base", "changes requested")]
    watching = watcher(demo)
    assert (watch.sent(watching, "root", [merged], T0), watch.sent(watching, "root", reds, T0)) == ([], [])
    assert posted(sent) == ([(merged_kind, f"root@{demo}", merged)] if merged_kind else [])


@pytest.mark.parametrize(("wants_tap", "exempt"), [(True, "incident fix"), (False, "incident fix"), (True, None), (False, None)])
def test_a_monorepo_pr_going_green_and_approved_wakes_no_one_since_the_lander_queues_its_exempt_landing_and_the_watch_draws_his_card(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], draws: Draws, wants_tap: bool, exempt: str | None
) -> None:
    with edit(demo) as data:
        data["prs"][KEY] |= {"wants_tap": wants_tap, "exempt": exempt, "review": "approved"}
        data["jobs"]["j1"]["state"] = "waiting"
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    sent = posts(monkeypatch)
    watching = watcher(demo)
    assert watch.sent(watching, "root", watch.run_pass(watching, T0), T0) == []
    assert posted(sent) == []
    assert (draws.keys, landers) == ([[KEY]] * (wants_tap and not exempt), [(KEY,)] * bool(exempt))


NO_CARDS = {"team_url": "", "questions": [], "closed": [], "pr_cards": []}


def approve(name: str, *keys: str) -> None:
    with edit(name) as data:
        for key in keys:
            data["prs"][key] |= {"wants_tap": True, "tap": state.new_tap("Approve", "aaaaaaa111", "pressed the button", at=state.stamp(T0), card=data["prs"][key].get("stack") or [key])}


def stacked(name: str, native: list[str] | None = None, cards: list[list[str]] | None = None) -> None:
    with edit(name) as data:
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"head": "bbbbbbb222", "base": "main", "checked": CHECKED | {"head": "bbbbbbb222"}}
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
    with edit(demo) as data:
        data["prs"][KEY]["lander"]["outcome"] = "not landed: stack-enqueue --watch exited 1: checks failed"
    lander_exited(demo, KEY)
    watch.run_pass(watching, T0 + timedelta(minutes=5))
    assert len(landers) == 1
    reads[KEY] = read(headRefOid="ccccccc333", checks=GREEN, reviewDecision="APPROVED")
    watch.run_pass(watching, T0 + timedelta(minutes=6))
    assert landers == [(KEY,), (KEY,)]
    assert state.load(demo)["prs"][KEY]["lander"]["head"] == "ccccccc333"


def test_a_halted_landers_child_still_dequeuing_after_its_hold_was_recorded_keeps_its_prs_from_the_next_lander_until_it_ends(demo: str) -> None:
    cleanup = subprocess.Popen(["sleep", "600"], start_new_session=True)
    try:
        with edit(demo) as data:
            first = land.mark(data, (KEY,))
            data["prs"][KEY]["lander"] |= {"child": state.born(cleanup.pid), "state": "adopted", "outcome": f"{land.HELD}: {KEY}: his Hold"}
        with edit(demo) as data:
            assert land.mark(data, (KEY,)) is None
    finally:
        cleanup.terminate()
        cleanup.wait()
    with edit(demo) as data:
        assert land.mark(data, (KEY,)).token != first.token


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
    named_paths: None,
    demo: str, landers: list[tuple[str, ...]], approved: tuple[str, ...], cards: list[list[str]], card: str | None, unit: tuple[str, ...] | None
) -> None:
    stacked(demo, cards=cards)
    approve(demo, *approved)
    cards = NO_CARDS
    if card:
        with edit(demo) as data:
            data["jobs"]["j1"]["questions"] = [state.new_question(None, "Which store?", state.stamp(T0), holds="the store, on #42 and #43") | {"waits": card}]
        cards = NO_CARDS | {"questions": [question(card)]}
    assert land.start(demo, [KEY, OTHER], cards) == ([unit] if unit else [])
    assert landers == ([unit] if unit else [])
    assert land.start(demo, [KEY, OTHER], cards) == []


@pytest.mark.parametrize(
    ("asker", "holds", "held"),
    [
        ("j1", "", False),
        ("j1", None, False),
        ("j1", "ship the watch, on #42", True),
        ("j1", "ship the watch, on `j1-watch`", True),
        ("j1", "ship the watch, on `j1-watch-store`", False),
        ("j2", "", False),
        ("j2", None, False),
        ("j2", "the dashboard", False),
        ("j1", "ship the watch", False),
        ("j2", "Ship  the WATCH, once its store is picked", False),
        ("j2", "the dashboard, on `j1-watch`", True),
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
def test_an_open_question_holds_a_stack_from_landing_while_its_holds_line_names_a_pr_of_it_by_number_or_whole_branch_or_another_jobs_names_a_change_the_stacks_job_builds_the_design_or_research_of_or_is_named_by(
    named_paths: None,
    demo: str, landers: list[tuple[str, ...]], asker: str, holds: str | None, held: bool
) -> None:
    stacked(demo)
    approve(demo, KEY, OTHER)
    waits = f"demo:{asker}-1-a"
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"].extend(["j2", "j3", "j4", "j5"])
        data["orders"]["o1"]["parts"]["the dashboard"] = state.new_part()
        data["jobs"]["j2"] = job(kind="design", parts=["the dashboard"], carries={}, state="done")
        data["jobs"]["j3"] = job(kind="design", parts=["ship the watch"], carries={}, state="done")
        data["jobs"]["j4"] = job(kind="reader", parts=["ship the watch"], carries={}, state="done")
        data["jobs"]["j5"] = job(kind="reader", parts=["ship the watch"], carries={}, state="done")
        data["jobs"]["j1"]["researched"] = ["j4"]
        data["prs"][KEY]["branch"] = "j1-watch"
        data["jobs"][asker]["questions"].append(state.new_question(None, "Which OpenAI endpoint?", state.stamp(T0), holds=holds) | {"waits": waits})
    cards = NO_CARDS | {"questions": [question(waits)]}
    drive = state.load(demo)
    assert land.open_cards(drive, "j1", drive["prs"][KEY]) == ([waits] if held else [])
    assert land.start(demo, [KEY, OTHER], cards) == ([] if held else [(KEY, OTHER)])
    with edit(demo) as data:
        data["jobs"][asker]["questions"][-1]["closed"] = state.new_close("answered", "Team", None, seq="9")
    assert land.start(demo, [KEY, OTHER], cards) == ([(KEY, OTHER)] if held else [])


def test_a_question_kept_before_holds_lines_holds_its_own_jobs_unbuilt_work_and_none_of_its_prs_so_its_approved_pr_lands_while_his_card_is_open(demo: str, landers: list[tuple[str, ...]]) -> None:
    approve(demo, KEY)
    with edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved"}
        data["jobs"]["j1"] |= {"waits": [KEY], "questions": [{"file": None, "title": "Which OpenAI endpoint?", "asked": state.stamp(T0), "unsettled": None, "waits": "demo:j1-1-a", "card": None}]}
    cards = NO_CARDS | {"questions": [question("demo:j1-1-a")]}
    drive = state.load(demo)
    assert drive["jobs"]["j1"]["questions"][0]["holds"] is None
    assert (land.open_cards(drive, "j1", None), land.unready(drive, KEY, cards)) == (["demo:j1-1-a"], None)
    assert land.start(demo, [KEY], cards) == [(KEY,)]


def test_a_built_line_waits_on_the_cards_holding_its_work_by_the_landers_rule_never_one_whose_holds_line_reads_none_or_a_reader_it_was_not_built_on(demo: str) -> None:
    with edit(demo) as data:
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


def test_a_design_card_whose_holds_line_names_one_of_its_changes_holds_that_changes_builder_from_opening_and_landing_its_pr_while_another_builder_carries_the_design_first(
    demo: str, landers: list[tuple[str, ...]]
) -> None:
    approve(demo, KEY)
    with edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved"}
        data["orders"]["o1"]["jobs"].extend(["j2", "j3"])
        data["orders"]["o1"]["parts"] |= {"the watch store": state.new_part() | {"design": "j2"}, "the watch page": state.new_part() | {"design": "j2"}}
        data["jobs"]["j1"]["parts"] = ["the watch page"]
        asked = state.new_question(None, "Which page?", state.stamp(T0), holds="the watch page") | {"waits": "demo:j2-1-a"}
        data["jobs"]["j2"] = job(kind="design", parts=["ship the watch"], carries={}, state="done", questions=[asked])
        data["jobs"]["j3"] = job(parts=["the watch store"], carries={}, state="done")
    drive = state.load(demo)
    assert (state.design_builder(drive, "j2"), state.design_builders(drive, "j2")) == ("j3", ["j3", "j1"])
    assert (land.open_cards(drive, "j1", drive["prs"][KEY]), land.open_cards(drive, "j3", None)) == (["demo:j2-1-a"], [])
    assert land.start(demo, [KEY], NO_CARDS | {"questions": [question("demo:j2-1-a")]}) == []
    with edit(demo) as data:
        data["jobs"]["j1"]["prs"] = []
    assert land.open_cards(state.load(demo), "j1", None) == ["demo:j2-1-a"]


@pytest.mark.parametrize(("holds", "held"), [("", False), (None, False), ("the windowed open, on #42", True)])
def test_a_builder_whose_built_line_names_its_own_card_leaves_its_approved_green_pr_at_stage_ready_unless_the_cards_holds_line_names_that_pr(
    demo: str, landers: list[tuple[str, ...]], holds: str | None, held: bool
) -> None:
    approve(demo, KEY)
    with edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved"}
        data["jobs"]["j1"]["questions"] = [state.new_question(None, "Should a long chat open on its turns since the last compaction?", state.stamp(T0), holds=holds) | {"waits": "demo:j1-1-a"}]
        state.take_result(data, "j1", "BUILT j1-windowed-open, waits on demo:j1-1-a")
    cards = NO_CARDS | {"questions": [question("demo:j1-1-a")]}
    built = state.load(demo)
    assert (built["jobs"]["j1"]["waits"], built["prs"][KEY]["waits"], land.pr_cards(built, built["prs"][KEY])) == (["demo:j1-1-a"], [], ["demo:j1-1-a"] if held else [])
    assert stage.of(built, KEY, cards).name == ("held" if held else "ready")
    assert land.start(demo, [KEY], cards) == ([] if held else [(KEY,)])


def test_replayed_56082_a_builders_own_card_its_built_line_waits_on_holds_only_the_pr_of_the_branch_its_holds_line_names_so_its_other_approved_pr_lands(
    demo: str, landers: list[tuple[str, ...]]
) -> None:
    approve(demo, KEY)
    with edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved", "branch": "j1-datadog-cannot-block"}
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"branch": "j1-datadog-cannot-block-remote-config"}
        data["jobs"]["j1"]["prs"].append(state.pr_url(OTHER))
        asked = state.new_question(None, "Which Remote Config?", state.stamp(T0), holds="`j1-datadog-cannot-block-remote-config`, building the recommended option")
        data["jobs"]["j1"]["questions"] = [asked | {"waits": "demo:j1-1-a"}]
        state.take_result(data, "j1", "BUILT j1-datadog-cannot-block-remote-config, waits on demo:j1-1-a")
    cards = NO_CARDS | {"questions": [question("demo:j1-1-a")]}
    built = state.load(demo)
    assert built["jobs"]["j1"]["waits"] == ["demo:j1-1-a"]
    assert [land.open_cards(built, "j1", built["prs"][key]) for key in (KEY, OTHER)] == [[], ["demo:j1-1-a"]]
    assert land.start(demo, [KEY], cards) == [(KEY,)]


def test_replayed_cc_slack_422_a_test_only_fix_of_a_job_whose_own_card_names_only_the_orders_one_part_is_held_by_no_card_while_the_card_holds_its_unbuilt_work(
    demo: str,
) -> None:
    fix = "yasyf/cc-slack#422"
    with edit(demo) as data:
        data["prs"][fix] = state.new_pr(state.pr_url(fix), "j1", "o1") | {"checks": "pass", "branch": "j4362-reply-test"}
        data["jobs"]["j1"]["prs"].append(state.pr_url(fix))
        data["jobs"]["j1"]["questions"] = [state.new_question(None, "Which reply path?", state.stamp(T0), holds="ship the watch") | {"waits": "demo:j1-1-a"}]
    drive = state.load(demo)
    assert (land.pr_cards(drive, drive["prs"][fix]), stage.held(drive, drive["prs"][fix]), state.part_cards(drive, "j1")) == ([], "", ["demo:j1-1-a"])


REPLAYED_5076 = "yasyf/cc-skills#5076"
RELEASE_WAIT_CARD = "durable-agents:j3790-1794592574142"
RELEASE_WAIT_PART = "Drive tooling: a job waiting on a release of a change keeps waiting after the ch"
REPLAYED_5076_DESIGN = (
    f"DESIGN: {RELEASE_WAIT_CARD}, his open card on which environments must serve the merge before a release wait ends; this rule is that card's"
    ' "every env runs it" option, set by the coordinator\'s follow-up, not the option the card recommends (any production env)'
)


@pytest.mark.parametrize(
    ("design", "holds", "answered", "held"),
    [
        (REPLAYED_5076_DESIGN, RELEASE_WAIT_PART, False, True),
        (REPLAYED_5076_DESIGN, RELEASE_WAIT_PART, True, False),
        (REPLAYED_5076_DESIGN, "", False, False),
        (f"DESIGN: none: every option on {RELEASE_WAIT_CARD} includes this change", RELEASE_WAIT_PART, False, False),
    ],
)
def test_replayed_5076_a_pr_whose_design_line_cites_an_open_card_another_orders_reader_asked_is_held_on_it_until_he_answers_unless_its_holds_line_or_the_design_line_reads_none(
    demo: str, landers: list[tuple[str, ...]], design: str, holds: str, answered: bool, held: bool
) -> None:
    url = state.pr_url(REPLAYED_5076)
    with edit(demo) as data:
        data["orders"]["o1"]["text"] = "Drive tooling: a release wait ends once every env runs the merge"
        data["orders"]["o2"] = order(text=RELEASE_WAIT_PART, jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", kind="reader", parts=[RELEASE_WAIT_PART], carries={}, state="done")
        asked = state.new_question(None, "Which environments must run a commit holding a PR's merge before its release wait ends?", state.stamp(T0), card=CARD, holds=holds)
        data["jobs"]["j2"]["questions"] = [asked | {"waits": RELEASE_WAIT_CARD, "closed": state.new_close("answered", "Any production env", None, seq="9") if answered else None}]
        data["jobs"]["j1"].update(state="waiting", said=f"PUSHED {url}", prs=[url])
        del data["prs"][KEY]
        data["prs"][REPLAYED_5076] = state.new_pr(url, "j1", "o1") | {"head": "aaaaaaa111", "wants_tap": False, "checked": CHECKED, "checks": "pass", "design": design}
    drive = state.load(demo)
    pr = drive["prs"][REPLAYED_5076]
    assert (state.part_cards(drive, "j1"), view.stored_waits(drive, land.pr_cards(drive, pr))) == ([], [RELEASE_WAIT_CARD] if held else [])
    assert (stage.held(drive, pr), stage.of(drive, REPLAYED_5076, NO_CARDS).name) == ((f"his cards {CARD} on its work are open", "held") if held else ("", "ready"))
    assert (land.start(demo, [REPLAYED_5076], NO_CARDS), landers) == (([], []) if held else ([(REPLAYED_5076,)], [(REPLAYED_5076,)]))


@pytest.mark.parametrize(("text", "held"), [("ship the watch", False), ("Drive tooling: the lander lands a stack bottom first", True)])
def test_replayed_56251_a_drive_tooling_card_a_design_line_cites_holds_only_a_drive_tooling_pr_so_his_product_pr_lands_on_its_tap(
    demo: str, landers: list[tuple[str, ...]], text: str, held: bool
) -> None:
    part = "Drive tooling: every PR of a stack draws on one card"
    approve(demo, KEY)
    with edit(demo) as data:
        data["orders"]["o1"]["text"] = text
        data["orders"]["o2"] = order(text=part, jobs=["j2"])
        data["jobs"]["j2"] = job(order="o2", kind="reader", parts=[part], carries={}, state="done")
        asked = state.new_question(None, "Should every PR of a stack draw on one card?", state.stamp(T0), card=CARD, holds=part)
        data["jobs"]["j2"]["questions"] = [asked | {"waits": "demo:j2-7"}]
        data["prs"][KEY] |= {"checks": "pass", "review": "approved", "design": "DESIGN: demo:j2-7, his open card on drawing a stack on one card"}
    drive = state.load(demo)
    assert (land.pr_cards(drive, drive["prs"][KEY]), stage.of(drive, KEY, NO_CARDS).name) == (([CARD], "held") if held else ([], "ready"))
    assert (land.start(demo, [KEY], NO_CARDS), landers) == (([], []) if held else ([(KEY,)], [(KEY,)]))


def test_a_flake_fix_goes_up_and_lands_while_his_card_holds_its_jobs_design_work(demo: str, landers: list[tuple[str, ...]]) -> None:
    approve(demo, KEY)
    with edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved", "exempt": state.FLAKE_FIX}
        data["jobs"]["j1"]["questions"] = [state.new_question(None, "Which store?", state.stamp(T0), holds="ship the watch, on #42") | {"waits": "demo:j1-1-a"}]
    cards = NO_CARDS | {"questions": [question("demo:j1-1-a")]}
    drive = state.load(demo)
    assert (land.open_cards(drive, "j1", drive["prs"][KEY]), land.open_cards(drive, "j1", drive["prs"][KEY] | {"exempt": None})) == ([], ["demo:j1-1-a"])
    assert land.start(demo, [KEY], cards) == [(KEY,)]
    with edit(demo) as data:
        data["jobs"]["j1"]["prs"] = []
    unshipped = state.load(demo)
    assert land.open_cards(unshipped, "j1", None) == ["demo:j1-1-a"]


def test_a_drive_saved_before_research_links_keeps_each_jobs_researched_readers_and_marks_each_ask_kept_with_no_holds_line_once(demo: str, tmp_path: Path) -> None:
    brief = tmp_path / "j1.brief.md"
    brief.write_text("Order (his words): ship the watch\nHandoff: Researched: j7 (drive job report j7)\nResearched: j2\n")
    unheld, none, named = tmp_path / "unheld.md", tmp_path / "none.md", tmp_path / "named.md"
    unheld.write_text("ASK Which store?\nFindings:\nRoot cause: none: a pick\n")
    none.write_text("ASK Which limiter?\nFindings:\nHolds: none: no option alters what j1 builds\n")
    named.write_text("ASK Which queue?\nFindings:\nHolds: ship the watch\n")
    with edit(demo) as data:
        data["jobs"]["j1"] |= {"brief": str(brief), "researched": []}
        data["jobs"]["j1"]["questions"] = [state.new_question(str(file), title, state.stamp(T0), holds=holds) for file, title, holds in ((unheld, "Which store?", ""), (none, "Which limiter?", ""), (named, "Which queue?", "ship the watch"))]
        data.pop("research_linked")
    linked = state.load(demo)
    assert (linked["research_linked"], linked["jobs"]["j1"]["researched"], [question["holds"] for question in linked["jobs"]["j1"]["questions"]]) == (True, ["j2"], [None, "", "ship the watch"])


def test_an_order_an_older_install_kept_with_no_kind_loads_as_a_build_whose_parts_only_research_held_are_answers_and_whose_builder_keeps_its_readers_research(demo: str) -> None:
    state.keep_report(demo, "j2", state.opening(RESEARCH), RESEARCH)
    with edit(demo) as data:
        data["orders"]["o2"] = order(text="why does the watch stall, then fix it", jobs=["j2", "j3", "j4"], parts={part: state.new_part() for part in ("why", "fix", "proof")})
        data["jobs"]["j2"] = job(order="o2", kind="reader", goal="research: the stall", parts=["why", "fix"], state="done")
        data["jobs"]["j3"] = job(order="o2", goal="fix the stall", parts=["fix"], state="waiting")
        data["jobs"]["j4"] = job(order="o2", goal="prove it", parts=["proof"], state="stopped")
    loaded = state.load(demo)
    folded = loaded["orders"]["o2"]
    assert (folded["kind"], {part: held["answer"] for part, held in folded["parts"].items()}, loaded["jobs"]["j3"]["researched"]) == ("build", {"why": True, "fix": False, "proof": False}, ["j2"])
    assert [state.work(loaded, "o2", part) for part in ("why", "fix", "proof")] == [False, True, True]
    loaded["jobs"]["j5"] = job(order="o2", goal="fix the reader's find", parts=["why"], state="stopped")
    folded["jobs"].append("j5")
    assert state.work(loaded, "o2", "why") is False
    loaded["jobs"]["j5"]["state"] = "done"
    assert state.work(loaded, "o2", "why") is True


HOLDING_CARD = "demo:j2-5-a"
BUSY_WAIT = "get rid of this busy 30s wait"
HELD_WHY = f"his cards {HOLDING_CARD} on its work are open"


def holding_job(name: str, *waits: str) -> None:
    with edit(name) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved", "checked": CHECKED | {"orders": ["o1", "o2"]}}
        data["orders"]["o2"] = order(text=BUSY_WAIT, jobs=["j2"], parts={BUSY_WAIT: state.new_part()})
        data["jobs"]["j2"] = job(order="o2", goal="change #42", parts=[BUSY_WAIT], carries={}, prs=[], waits=list(waits))
        state.take_pr(data, "j2", KEY)


def holding_asks(name: str, holds: str | None) -> None:
    with edit(name) as data:
        data["jobs"]["j2"]["questions"].append(state.new_question(None, "Do pause and resume stay in #42?", state.stamp(T0), holds=holds) | {"waits": HOLDING_CARD})


def holding_answered(name: str) -> None:
    with edit(name) as data:
        data["jobs"]["j2"]["questions"][-1]["closed"] = state.new_close("answered", "Keep them", None, seq="9")


@pytest.mark.parametrize(
    ("waits", "holds", "held"),
    [
        ((HOLDING_CARD,), None, False),
        ((HOLDING_CARD,), "", False),
        ((HOLDING_CARD,), BUSY_WAIT, False),
        ((HOLDING_CARD,), f"{BUSY_WAIT}, which #42 carries", True),
        ((), f'"land #42 once #43 is in": each option changes what #42 carries when it lands; {BUSY_WAIT}', True),
        ((), "the dashboard", False),
    ],
)
def test_his_open_card_on_the_job_a_pr_moved_to_holds_it_from_landing_only_while_its_holds_line_names_it_whichever_job_waits_on_that_card(
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


def test_a_card_naming_a_pr_on_the_job_it_moved_off_holds_it_no_more_and_no_card_holds_a_main_fix(
    demo: str, landers: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    approve(demo, KEY)
    holding_job(demo)
    card = "demo:j1-5-a"
    with edit(demo) as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "Does #42 land before #43?", state.stamp(T0), holds="#42") | {"waits": card})
    asking(monkeypatch, demo, card)
    cards = view.live_cards(demo)
    moved = state.load(demo)
    assert (state.pr_jobs(moved, moved["prs"][KEY]), land.pr_cards(moved, moved["prs"][KEY])) == (["j2", "j1"], [])
    assert land.start(demo, [KEY], cards) == [(KEY,)]
    with edit(demo) as data:
        state.take_pr(data, "j1", KEY)
    back = state.load(demo)
    assert land.pr_cards(back, back["prs"][KEY]) == [card]
    with edit(demo) as data:
        data["prs"][KEY]["exempt"] = state.MAIN_FIX
    fixed = state.load(demo)
    assert land.pr_cards(fixed, fixed["prs"][KEY]) == []


def test_the_watch_stops_a_lander_when_a_card_opens_on_the_work_of_a_job_holding_its_pr_and_the_pr_lands_once_he_answers(
    named_paths: None,
    demo: str, landers: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    approve(demo, KEY)
    holding_job(demo)
    asking(monkeypatch, demo)
    lander = subprocess.Popen(["sleep", "600"], start_new_session=True)
    with edit(demo) as data:
        land.mark(data, (KEY,))
        data["prs"][KEY]["lander"] |= {"child": state.born(lander.pid), "state": "adopted"}
    watching = watcher(demo)
    watching.cards = view.live_cards(demo)
    watch.landed(watching, T0)
    assert (lander.poll(), stage.of(state.load(demo), KEY, watching.cards).name) == (None, "landing")
    holding_asks(demo, f"{BUSY_WAIT}, which #42 carries")
    asking(monkeypatch, demo, HOLDING_CARD)
    watching.cards = view.live_cards(demo)
    watch.landed(watching, T0)
    assert lander.wait(timeout=10) == -signal.SIGTERM
    assert state.load(demo)["prs"][KEY]["lander"]["outcome"] == f"{land.HELD}: {KEY}: {HELD_WHY}"
    assert (land.unready(state.load(demo), KEY, watching.cards), landers) == (f"held: {HELD_WHY}", [])
    holding_answered(demo)
    watch.landed(watching, T0)
    assert landers == [(KEY,)]


def test_a_lander_the_watch_stops_dequeues_each_pr_it_queued_and_removes_its_worktree(home_repo: None, demo: str, tmp_path: Path) -> None:
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


def queued(name: str) -> None:
    with edit(name) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved", "lander": {"head": "aaaaaaa111", "pid": 1, "at": state.stamp(T0), "outcome": land.QUEUED, "below": []}}


def test_his_hold_on_a_pr_trunk_queued_after_its_lander_ended_reads_held_and_the_watch_starts_one_dequeue_of_it(named_paths: None, demo: str, landers: list[tuple[str, ...]]) -> None:
    approve(demo, KEY)
    queued(demo)
    watching = watcher(demo)
    watching.cards = NO_CARDS
    watch.landed(watching, T0)
    assert (stage.alone(state.load(demo), KEY, NO_CARDS).name, landers) == ("landing", [])
    with edit(demo) as data:
        data["prs"][KEY]["tap"] = state.new_tap("Hold", "aaaaaaa111", "slack")
    assert stage.alone(state.load(demo), KEY, NO_CARDS).name == "held"
    watch.landed(watching, T0)
    watch.landed(watching, T0)
    assert landers == [("--dequeue", KEY)]
    assert state.holder(state.load(demo)["prs"][KEY]["lander"]["dequeue"]) == os.getpid()


def test_a_staging_test_started_on_a_pr_trunk_queued_after_its_lander_ended_reads_waiting_and_the_watch_starts_one_dequeue_of_it(home_repo: None, demo: str, landers: list[tuple[str, ...]]) -> None:
    approve(demo, KEY)
    queued(demo)
    part = state.STAGING_TEST.format(number=42)
    with edit(demo) as data:
        data["orders"]["o2"] = state.new_order("test #42 on staging", "terminal", None, {part: state.new_part()}, state.stamp()) | {"jobs": ["j2"]}
        data["jobs"]["j2"] = state.new_job("o2", "builder", "soak #42 on staging", [part], state.stamp()) | {"builds_nothing": True}
    watching = watcher(demo)
    watching.cards = NO_CARDS
    watch.landed(watching, T0)
    watch.landed(watching, T0)
    assert (stage.alone(state.load(demo), KEY, NO_CARDS).name, landers) == ("waiting", [("--dequeue", KEY)])


def test_a_queued_pr_put_back_on_its_staging_test_leaves_the_merge_queue_in_the_same_call_with_no_watch_pass(home_repo: None, demo: str, landers: list[tuple[str, ...]]) -> None:
    approve(demo, KEY)
    queued(demo)
    state.write(demo, lambda data: state.wait_on_staging(data, KEY))
    assert landers == [("--dequeue", KEY)]
    assert (stage.alone(state.load(demo), KEY, NO_CARDS).name, state.holder(state.load(demo)["prs"][KEY]["lander"]["dequeue"])) == ("red", os.getpid())


def test_a_dangerous_pr_trunk_queued_before_he_went_quiet_leaves_the_queue_while_he_is_away_and_lands_again_at_his_next_word(
    home_repo: None,
    demo: str, tmp_path: Path, landers: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    on_main(tmp_path, demo, '#!/bin/sh\necho "#42 DEQUEUED aaaaaaa"\n')
    approve(demo, KEY)
    queued(demo)
    monkeypatch.setattr(github, "files", {KEY: ["go/sand/sandsql/write.go"]}.__getitem__)
    whys: list[str] = []
    monkeypatch.setattr(land, "unqueue", lambda name, keys, why, token: whys.append(why) or os.getpid())
    watching = watcher(demo)
    watching.cards = NO_CARDS
    with edit(demo) as data:
        data["owner"]["heard"] = state.stamp(state.now() - timedelta(minutes=5))
    watch.landed(watching, T0)
    assert whys == []
    with edit(demo) as data:
        data["owner"]["heard"] = state.stamp(state.now() - timedelta(minutes=20))
    watch.landed(watching, T0)
    watch.landed(watching, T0)
    assert len(whys) == 1 and whys[0].startswith(f"{land.AWAY}: he last wrote or tapped 20 minutes ago, and it changes go/sand/sandsql/write.go")
    land.dequeue(demo, [KEY], whys[0])
    assert stage.of(state.load(demo), KEY, NO_CARDS) == stage.Stage("uncarded", stage.WATCH, "his card on it is not drawn yet")
    dequeuer_exited(demo, KEY)
    with edit(demo) as data:
        data["owner"]["heard"] = state.stamp(state.now())
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]


def test_a_dequeue_takes_the_unit_out_of_trunks_queue_from_main_records_why_and_it_lands_again_on_his_approve(home_repo: None, demo: str, tmp_path: Path, landers: list[tuple[str, ...]]) -> None:
    calls = tmp_path / "calls"
    on_main(tmp_path, demo, f'#!/bin/sh\necho "$*" >> {calls}\necho "#42 DEQUEUED aaaaaaa"\n')
    queued(demo)
    watching = watcher(demo)
    assert land.halt(demo, [KEY], NO_CARDS) == [KEY]
    why = f"{land.HELD}: {KEY}: nothing clears it now, since his Approve came off it"
    assert land.dequeue(demo, [KEY], why) == f"{why}; stack-enqueue --dequeue said #42 DEQUEUED aaaaaaa"
    assert (calls.read_text().splitlines(), land.landing(state.load(demo)["prs"][KEY])) == (["--dequeue 42"], False)
    assert watch.event_lines(watching) == [f"PR {KEY}: {why}; stack-enqueue --dequeue said #42 DEQUEUED aaaaaaa [o1 j1]"]
    approve(demo, KEY)
    assert land.start(demo, [KEY], NO_CARDS) == []
    dequeuer_exited(demo, KEY)
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    assert landers == [("--dequeue", KEY), (KEY,)]


def test_a_lander_reservation_no_lander_adopted_leaves_its_head_to_land_again(home_repo: None, demo: str, tmp_path: Path, landers: list[tuple[str, ...]]) -> None:
    on_main(tmp_path, demo, '#!/bin/sh\necho "#42 DEQUEUED aaaaaaa"\n')
    queued(demo)
    assert land.halt(demo, [KEY], NO_CARDS) == [KEY]
    land.dequeue(demo, [KEY], f"{land.HELD}: {KEY}: nothing clears it now, since his Approve came off it")
    approve(demo, KEY)
    dequeuer_exited(demo, KEY)
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    lander = state.load(demo)["prs"][KEY]["lander"]
    state.cancel(demo, state.Reservation(lander["token"], tuple(map(tuple, lander["members"]))))
    assert (land.unlaunched(state.load(demo)["prs"][KEY]["lander"]), stage.unlanded(state.load(demo), KEY, NO_CARDS)) == (True, None)
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    assert landers == [("--dequeue", KEY), (KEY,), (KEY,)]


def test_a_dequeue_stack_enqueue_fails_on_its_own_keeps_the_pr_queued_says_so_as_the_landers_failure_and_the_next_pass_dequeues_again(
    home_repo: None,
    demo: str, tmp_path: Path, landers: list[tuple[str, ...]]
) -> None:
    on_main(tmp_path, demo, '#!/bin/sh\necho "trunk is down"\nexit 5\n')
    queued(demo)
    assert land.halt(demo, [KEY], NO_CARDS) == [KEY]
    watching = watcher(demo)
    failed = f"{land.NO_DEQUEUE} {KEY} out of Trunk's merge queue: trunk is down; its whole output is in {state.drive_dir(demo) / land.LOG}"
    assert land.dequeue(demo, [KEY], "held") == failed
    lander = state.load(demo)["prs"][KEY]["lander"]
    assert (lander["outcome"], "dequeue" in lander, watch.event_lines(watching)) == (land.QUEUED, False, [f"{watch.LANDER}{failed}"])
    assert land.halt(demo, [KEY], NO_CARDS) == [KEY]
    assert landers == [("--dequeue", KEY), ("--dequeue", KEY)]


def test_a_dequeue_trunk_answers_not_queued_for_as_56900s_after_its_tests_passed_is_already_out_so_it_keeps_the_hold_drops_the_dequeuer_and_says_nothing(
    home_repo: None,
    demo: str, tmp_path: Path, landers: list[tuple[str, ...]]
) -> None:
    on_main(tmp_path, demo, '#!/bin/sh\necho "#42 NOT_QUEUED"\nexit 2\n')
    queued(demo)
    with edit(demo) as data:
        data["prs"][KEY]["tap"] = state.new_tap("Hold", "aaaaaaa111", "slack")
    watching = watcher(demo)
    assert land.halt(demo, [KEY], NO_CARDS) == [KEY]
    why = f"{land.HELD}: {KEY}: he holds it"
    assert land.dequeue(demo, [KEY], why) == why
    lander = state.load(demo)["prs"][KEY]["lander"]
    assert (lander["outcome"], "dequeue" in lander, watch.event_lines(watching)) == (why, False, [])
    assert stage.alone(state.load(demo), KEY, NO_CARDS).name == "held"
    assert land.halt(demo, [KEY], NO_CARDS) == []
    assert landers == [("--dequeue", KEY)]


def test_a_halt_never_dequeues_a_pr_the_drive_reads_as_merged_and_a_unit_whose_prs_all_merged_dequeues_nothing(
    home_repo: None,
    demo: str, landers: list[tuple[str, ...]]
) -> None:
    stacked(demo)
    queued(demo)
    with edit(demo) as data:
        data["prs"][OTHER]["lander"] = data["prs"][KEY]["lander"] | {"head": "bbbbbbb222"}
        data["prs"][KEY]["state"] = "merged"
    assert (land.halt(demo, [KEY, OTHER], NO_CARDS), landers) == ([OTHER], [("--dequeue", OTHER)])
    with edit(demo) as data:
        data["prs"][OTHER]["state"] = "merged"
        data["prs"][OTHER]["lander"].pop("dequeue")
    assert (land.halt(demo, [KEY, OTHER], NO_CARDS), landers) == ([], [("--dequeue", OTHER)])


def test_a_dequeue_passes_no_pr_the_drive_reads_as_merged_to_the_queues_script(home_repo: None, demo: str, tmp_path: Path, landers: list[tuple[str, ...]]) -> None:
    calls = tmp_path / "calls"
    on_main(tmp_path, demo, f'#!/bin/sh\necho "$*" >> {calls}\necho "#43 DEQUEUED bbbbbbb"\n')
    stacked(demo)
    queued(demo)
    with edit(demo) as data:
        data["prs"][OTHER]["lander"] = data["prs"][KEY]["lander"] | {"head": "bbbbbbb222"}
    assert land.halt(demo, [KEY, OTHER], NO_CARDS) == [KEY, OTHER]
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
    land.dequeue(demo, [KEY, OTHER], "held")
    assert calls.read_text().splitlines() == ["--dequeue 43"]
    with edit(demo) as data:
        data["prs"][OTHER]["state"] = "merged"
        for key in (KEY, OTHER):
            data["prs"][key]["lander"]["dequeue"] = {"at": state.stamp()}
    watching = watcher(demo)
    assert land.dequeue(demo, [KEY, OTHER], "held") == "held"
    assert (calls.read_text().splitlines(), watch.event_lines(watching), ["dequeue" in state.load(demo)["prs"][key]["lander"] for key in (KEY, OTHER)]) == (["--dequeue 43"], [], [False, False])


@pytest.mark.parametrize(("cards", "first"), [(ONE_CARD, []), (TWO_CARDS, [(KEY,)])])
def test_a_pr_above_waiting_on_an_open_pr_holds_the_bottom_of_its_own_card_and_no_other_card_and_lands_once_that_pr_merges(
    named_paths: None,
    demo: str, landers: list[tuple[str, ...]], cards: list[list[str]], first: list[tuple[str, ...]]
) -> None:
    stacked(demo, cards=cards)
    approve(demo, KEY, OTHER)
    with edit(demo) as data:
        data["prs"][THIRD] = state.new_pr(state.pr_url(THIRD), "j1", "o1")
        data["jobs"]["j1"]["waits"] = [KEY, OTHER, THIRD]
        data["prs"][OTHER]["waits"] = [THIRD]
    assert land.start(demo, [KEY, OTHER], NO_CARDS) == first
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["prs"][OTHER]["checks"] = "fail"
    now = stage.of(state.load(demo), KEY, NO_CARDS)
    assert (now.name, now.why.startswith(why)) == (name, True)
    assert land.start(demo, [KEY, OTHER], NO_CARDS) == unit


def test_his_approve_of_56802_on_its_card_alone_lands_it_alone_once_56935_joins_that_card_after_his_press_which_asks_his_approve_for_56935_alone(
    demo: str, landers: list[tuple[str, ...]]
) -> None:
    stacked(demo, cards=TWO_CARDS)
    approve(demo, KEY)
    stacked(demo)
    data = state.load(demo)
    now = stage.of(data, KEY, NO_CARDS)
    assert (data["prs"][KEY]["stack"], data["prs"][KEY]["tap"]["card"], now.name, now.why.startswith("green and approved by him")) == ([KEY, OTHER], [KEY], "ready", True)
    assert drawn_pr_card(demo, KEY, OTHER)["card"]["waits"].startswith("merge:43:")
    assert land.start(demo, [KEY, OTHER], NO_CARDS) == [(KEY,)]
    assert landers == [(KEY,)]


def test_his_approve_in_words_of_the_bottom_of_a_card_lands_it_while_a_red_pr_above_on_that_card_holds_none_of_it_as_56919_held_56773(
    demo: str, run: Callable[..., tuple[int, str]], landers: list[tuple[str, ...]]
) -> None:
    stacked(demo)
    approve(demo, OTHER)
    assert run("pr", "set", state.pr_url(KEY), "--tapped", "Approve", "--tap-source", "https://acme.slack.com/archives/D123/p1794783600000100")[0] == 0
    with edit(demo) as data:
        data["prs"][OTHER]["checks"] = "fail"
    data = state.load(demo)
    assert (data["prs"][KEY]["tap"]["card"], stage.of(data, KEY, NO_CARDS).name) == ([KEY], "ready")
    assert land.start(demo, [KEY, OTHER], NO_CARDS) == [(KEY,)]
    assert landers == [(KEY,)]


def test_his_approve_of_49869_and_53227_lands_them_while_55258_stacked_later_on_its_own_card_grades_and_passes_none_of_it_to_55258(
    named_paths: None,
    demo: str, landers: list[tuple[str, ...]]
) -> None:
    bottom, renewal, reparent = "Forge-AI/monorepo#49869", "Forge-AI/monorepo#53227", "Forge-AI/monorepo#55258"
    shape = {
        bottom: ("bottom", "dev", None, [bottom, renewal]),
        renewal: ("on-bottom-1", "bottom", bottom, [bottom, renewal]),
        reparent: ("on-bottom-2", "bottom", bottom, [reparent]),
    }
    with edit(demo) as data:
        for key, (branch, base, under, card) in shape.items():
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {
                "head": "aaaaaaa111",
                "checked": CHECKED,
                "branch": branch,
                "base": base,
                "under": under,
                "stack": card,
                "checks": "pass",
                "review": "approved",
            }
        data["prs"][reparent] |= {"checks": "pending"}
    approve(demo, bottom, renewal)
    data = state.load(demo)
    cards = NO_CARDS | {"pr_cards": [pr_card(bottom, renewal), pr_card(reparent, url="https://acme.slack.com/archives/D123/p2")]}
    assert data["prs"][reparent]["tap"] is None
    assert [stage.of(data, key, cards).name for key in shape] == ["ready", "ready", "grading"]
    assert land.units(data, list(shape), cards) == [(bottom, renewal)]
    with edit(demo) as data:
        data["prs"][reparent]["checks"] = "pass"
        data["prs"][renewal]["checks"] = "fail"
    approve(demo, reparent)
    behind = stage.of(state.load(demo), reparent, cards)
    assert (behind.name, behind.why.startswith(f"{renewal} on his card with {bottom}, red: ")) == ("behind", True)
    assert land.start(demo, list(shape), cards) == []
    with edit(demo) as data:
        data["prs"][renewal]["checks"] = "pass"
    assert land.start(demo, list(shape), cards) == [(bottom, renewal, reparent)]
    assert landers == [(bottom, renewal, reparent)]


def test_a_tap_stage_adopt_copied_onto_an_open_pr_is_dropped_and_one_he_gave_is_kept(demo: str) -> None:
    approve(demo, KEY)
    with edit(demo) as data:
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"tap": data["prs"][KEY]["tap"] | {"via": KEY}}
    data = state.load(demo)
    assert (data["prs"][KEY]["tap"]["label"], data["prs"][OTHER]["tap"]) == ("Approve", None)


def test_his_approve_under_a_pr_the_hold_label_holds_queues_it_with_the_prs_under_it_and_leaves_the_held_pr_open(
    named_paths: None,
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
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    fourth, fifth = "Forge-AI/monorepo#45", "Forge-AI/monorepo#46"
    shape = {KEY: ("root", "main", None), OTHER: ("middle", "root", KEY), THIRD: ("top", "middle", OTHER), fourth: ("left", "root", KEY), fifth: ("right", "root", KEY)}
    with edit(demo) as data:
        for key, (branch, base, under) in shape.items():
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"head": "aaaaaaa111", "branch": branch, "base": base, "under": under, "checks": "pass", "review": "approved"}
    approve(demo, KEY, THIRD, fourth, fifth)
    head = enqueue_exiting(tmp_path, demo, 0)
    monkeypatch.setattr(land.github, "below", lambda key: {THIRD: [KEY, OTHER], fourth: [KEY], fifth: [KEY]}.get(key, []))
    behind = stage.of(state.load(demo), THIRD, NO_CARDS)
    assert (behind.name, behind.why.startswith(f"{OTHER} below it in its stack, ")) == ("behind", True)
    assert land.run(demo, [THIRD], dry_run=True) == land.held_below([THIRD], [OTHER])
    assert stage.of(state.load(demo), fourth, NO_CARDS).name == "ready"
    assert land.run(demo, [fourth], dry_run=True).startswith(f"would queue {fourth} through stack-enqueue --watch from main at {head[:12]}")
    assert land.start(demo, list(shape), NO_CARDS) == [(KEY, fourth)]
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["prs"][fourth]["lander"]["outcome"] = land.QUEUED
    assert land.start(demo, list(shape), NO_CARDS) == [(fifth,)]
    assert landers == [(KEY, fourth), (fifth,)]


def test_a_native_stack_on_another_tracked_prs_branch_joins_its_landing_stack_and_lands_with_it_once_he_approves_each(
    named_paths: None,
    demo: str, landers: list[tuple[str, ...]]
) -> None:
    with edit(demo) as data:
        for key, head in ((OTHER, "bbbbbbb222"), (THIRD, "ccccccc333")):
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"head": head, "checked": CHECKED | {"head": head}}
        for key, native, branch, base, under in ((KEY, [KEY], "chat-organize", "main", None), (OTHER, [OTHER, THIRD], "chat-pins", "chat-organize", KEY), (THIRD, [OTHER, THIRD], "chat-pins-2", "chat-pins", OTHER)):
            data["prs"][key] |= {"native": native, "branch": branch, "base": base, "under": under, "checks": "pass", "review": "approved"}
    assert land.landing_stacks(state.load(demo)) == dict.fromkeys(STACKED, STACKED)
    approve(demo, OTHER, THIRD)
    assert land.start(demo, list(STACKED), NO_CARDS) == []
    behind = stage.of(state.load(demo), THIRD, NO_CARDS)
    assert (behind.name, behind.why.startswith(f"{KEY} below it in its stack, ")) == ("behind", True)
    approve(demo, KEY)
    assert land.start(demo, list(STACKED), NO_CARDS) == [STACKED]
    assert landers == [STACKED]


def test_his_approve_above_a_pr_he_has_not_approved_reads_behind_it_owed_by_him_says_nothing_and_lands_once_he_approves_that_pr(
    named_paths: None,
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
        (origin / ENQUEUE).parent.mkdir(parents=True)
        (origin / ENQUEUE).touch(mode=0o755)
    head = commit_files(origin, "stack-enqueue", {str(ENQUEUE): script})
    if not clone.exists():
        git_in(tmp_path, "clone", "-q", str(origin), str(clone))
        with edit(name) as data:
            data["roles"]["coordinator"]["cwd"] = str(clone)
    return head


def enqueue_exiting(tmp_path: Path, name: str, code: int) -> str:
    return on_main(tmp_path, name, f'#!/bin/sh\necho "trunk said $*"\nexit {code}\n')


def test_a_lander_lands_an_approved_green_pr_changing_nothing_dangerous_on_green_ci_with_no_staging_test_however_far_its_paths_reach(
    home_repo: None,
    demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    approve(demo, KEY)
    with edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved"}
    enqueue_exiting(tmp_path, demo, 0)
    monkeypatch.setattr(github, "files", lambda key: ["api/src/upload.ts"])
    assert land.run(demo, [KEY]) == land.QUEUED
    data = state.load(demo)
    assert (data["prs"][KEY]["waits"], state.STAGING_TEST.format(number=42) in data["orders"]["o1"]["parts"]) == ([], False)


def dropped_by_answer(demo: str, part: str) -> list[str]:
    with edit(demo) as data:
        data["answers"]["a1"] = {"words": "merge it, then test it", "link": f"{CARD}9", "at": state.stamp(T0), "said": state.stamp(T0), "cards": {}, "jobs": {}}
        return superseded.drop(data, "a1", "o1", part)


def test_his_answer_dropping_an_open_prs_staging_test_keeps_it_and_its_tester_since_a_dangerous_change_merges_only_after_it_passes_as_57676_needed(home_repo: None, demo: str) -> None:
    part = state.STAGING_TEST.format(number=42)
    with edit(demo) as data:
        data["orders"]["o1"]["parts"][part] = state.new_part()
        data["prs"][KEY]["waits"] = [f"o1:{part}"]
    assert dropped_by_answer(demo, part) == [f"o1:{part} kept: {KEY} changes something dangerous, which merges only after its staging test passes, an incident fix included"]
    data = state.load(demo)
    assert (part in data["orders"]["o1"]["parts"], data["prs"][KEY]["waits"]) == (True, [f"o1:{part}"])


def test_a_load_puts_back_on_its_staging_test_each_open_pr_an_older_drop_part_let_merge_first_and_leaves_his_answers_lift(home_repo: None, demo: str) -> None:
    with edit(demo) as data:
        data["prs"][KEY] |= {"paths_at": "abc", "unstaged": {"why": "drive order set --drop-part took its staging test off its order", "head": "abc", "merges_first": True}}
        data["prs"]["Forge-AI/monorepo#43"] = data["prs"][KEY] | {"unstaged": {"why": "his answer took its staging test off its order", "head": "abc", "merges_first": True}}
        data["untested_restaged"] = False
    data = state.load(demo)
    assert (data["prs"][KEY]["unstaged"], data["prs"][KEY]["paths_at"], data["prs"]["Forge-AI/monorepo#43"]["unstaged"]["why"]) == (None, None, "his answer took its staging test off its order")
    assert KEY in state.unmarked(data)


@pytest.mark.parametrize("code", [0, 3])
def test_a_landers_outcome_is_said_on_its_prs_line_waking_no_one_and_a_failure_reads_unlanded_which_wakes_the_session_holding_its_order(
    home_repo: None,
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
    home_repo: None,
    demo: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    approve(demo, KEY)
    enqueue_exiting(tmp_path, demo, 3)
    clone = tmp_path / "clone"
    (clone / ENQUEUE).write_text('#!/bin/sh\necho "the checkout said $*"\nexit 3\n')
    head = on_main(tmp_path, demo, '#!/bin/sh\necho "main said $*"\n')
    untouched = (git_in(clone, "rev-parse", "HEAD"), git_in(clone, "status", "--porcelain"))
    assert land.run(demo, [KEY]) == land.QUEUED
    said = capsys.readouterr().out
    checkout = state.drive_dir(demo) / land.LANDINGS / str(os.getpid())
    assert f"{KEY}: stack-enqueue from {checkout}, a fresh worktree of Forge-AI/monorepo's main at {head}" in said
    assert ("main said --check 42" in said, "main said --watch 42" in said, "the checkout said" in said) == (True, True, False)
    assert (git_in(clone, "rev-parse", "HEAD"), git_in(clone, "status", "--porcelain")) == untouched
    landings = state.root() / land.CLONES / "Forge-AI/monorepo"
    assert (len(git_in(clone, "worktree", "list").splitlines()), len(git_in(landings, "worktree", "list").splitlines()), checkout.exists()) == (1, 1, False)


def test_a_landing_runs_stack_enqueue_trusting_the_mise_configs_of_the_worktree_it_made_so_a_mise_shim_starting_its_interpreter_never_refuses_them(
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(land.MISE_TRUST, raising=False)
    approve(demo, KEY)
    shim = f'[ -n "${land.MISE_TRUST}" ] && [ "$(cd "${land.MISE_TRUST}" && pwd -P)" = "$(pwd -P)" ] || {{ echo "mise ERROR Config files in $(pwd -P)/mise.toml are not trusted." >&2; exit 1; }}'
    on_main(tmp_path, demo, f'#!/bin/sh\n{shim}\necho "main said $*"\n')
    assert land.run(demo, [KEY]) == land.QUEUED
    said = capsys.readouterr().out
    assert ("main said --check 42" in said, "main said --watch 42" in said, "not trusted" in said) == (True, True, False)


def test_a_landing_checks_out_from_this_macs_clone_with_no_blob_filter_so_a_blob_origin_withholds_from_a_fetch_by_id_never_stops_it_and_a_failed_fetch_lands_again_at_the_next_pass(
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    with edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved"}
    enqueue_exiting(tmp_path, demo, 0)
    origin, blobless, away = tmp_path / "origin", tmp_path / "blobless", tmp_path / "away"
    git_in(origin, "config", "uploadpack.allowFilter", "true")
    git_in(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    git_in(tmp_path, "clone", "-q", "--filter=blob:none", f"file://{origin}", str(blobless))
    with edit(demo) as data:
        data["roles"]["coordinator"]["cwd"] = str(blobless)
    admin = "rust/crates/sandsql-server/src/admin.rs"
    head = commit_files(origin, "admin", {admin: "fn admin() {}\n" * 18186})
    blob = git_in(origin, "rev-parse", f"HEAD:{admin}")
    asked, withhold, config = tmp_path / "asked", tmp_path / "withhold", tmp_path / "gitconfig"
    withhold.write_text(f'#!/bin/sh\ntee -a {asked} | grep -v "^{blob}$" | "$@"\n')
    withhold.chmod(0o755)
    config.write_text(f"[uploadpack]\n\tpackObjectsHook = {withhold}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    origin.rename(away)
    assert land.run(demo, [KEY]).startswith(f"{land.UNREAD}: ")
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    away.rename(origin)
    assert land.run(demo, [KEY]) == land.QUEUED
    clone = state.root() / land.CLONES / "Forge-AI/monorepo"
    made = clone.stat().st_ino
    newer = commit_files(origin, "newer admin", {admin: "fn newer() {}\n" * 18186})
    assert land.run(demo, [KEY]) == land.QUEUED
    assert (clone.stat().st_ino, git_in(clone, "rev-parse", "refs/remotes/origin/main")) == (made, newer)
    assert [git_in(clone, "cat-file", "-t", f"{sha}:{admin}") for sha in (head, newer)] == ["blob", "blob"]
    assert "promisor" not in git_in(clone, "config", "--local", "--list")
    assert blob not in asked.read_text().split()
    assert landers == [(KEY,)]
    git_in(blobless, "fetch", "-q", "origin", "+refs/heads/main:refs/remotes/origin/main")
    coordinators = subprocess.run(["git", "-C", str(blobless), "worktree", "add", "-q", "--detach", str(tmp_path / "from-blobless"), head], capture_output=True, text=True)
    assert f"could not fetch {blob} from promisor remote" in coordinators.stderr


@pytest.mark.parametrize("mode", ["--check", "--watch"])
def test_a_landing_whose_stack_enqueue_failed_on_its_own_is_said_once_to_the_meta_session_as_the_landers_defect_and_tried_again_with_no_new_tap_until_it_queues(
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], mode: str
) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    with edit(demo) as data:
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
    lander_exited(demo, KEY)
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


@pytest.mark.parametrize("blip", ["fatal: unable to access 'https://github.com/Forge-AI/monorepo/': Recv failure: Operation timed out", "fatal: unable to access 'https://github.com/Forge-AI/monorepo/': Could not resolve host: github.com"])
def test_a_landing_whose_check_failed_on_one_network_blip_tries_again_with_no_word_and_only_the_same_failure_on_the_next_pass_is_the_landers_defect_as_57608s_was_not(
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], blip: str
) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    with edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved"}
    on_main(tmp_path, demo, f'#!/bin/sh\n[ "$1" = --check ] || exit 0\necho "stack-enqueue failed: git ls-remote origin: {blip}" >&2\nexit 5\n')
    watching = watcher(demo)
    failed = land.run(demo, [KEY])
    assert failed.startswith(f"{land.NO_ENQUEUE} --check on {KEY}, which exited 5") and land.blip(failed)
    assert watch.event_lines(watching) == []
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    assert land.run(demo, [KEY]) == failed
    assert watch.event_lines(watching) == [f"{watch.LANDER}{failed}"]
    lander_exited(demo, KEY)
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    assert land.run(demo, [KEY]) == failed
    assert watch.event_lines(watching) == []
    lander_exited(demo, KEY)
    enqueue_exiting(tmp_path, demo, 0)
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    assert land.run(demo, [KEY]) == land.QUEUED
    assert watch.event_lines(watching) == [f"PR {KEY}: {land.QUEUED} [o1 j1]"]


@pytest.mark.parametrize("mode", ["--check", "--watch"])
def test_replayed_56720_a_landing_whose_stack_enqueue_exited_before_it_printed_an_answer_is_the_landers_own_failure_said_once_a_head_to_the_meta_session_and_tried_again_until_it_queues(
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], mode: str
) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    with edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved"}
    untrusted = "mise ERROR Config files in ~/.local/state/long-running/demo/landing/$$/mise.toml are not trusted."
    on_main(tmp_path, demo, f'#!/bin/sh\n[ "$1" = {mode} ] || exit 0\necho "{untrusted}" >&2\nexit 1\n')
    watching = watcher(demo)
    failed = land.run(demo, [KEY])
    assert failed.startswith(f"{land.NO_ENQUEUE} {mode} on {KEY}, which exited 1 before it printed an answer: mise ERROR Config files in ")
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    again = land.run(demo, [KEY])
    assert again != failed
    lander_exited(demo, KEY)
    enqueue_exiting(tmp_path, demo, 0)
    assert land.start(demo, [KEY], NO_CARDS) == [(KEY,)]
    assert land.run(demo, [KEY]) == land.QUEUED
    lines = watch.event_lines(watching)
    assert lines == [f"{watch.LANDER}{failed}", f"PR {KEY}: {land.QUEUED} [o1 j1]"]
    sent = posts(monkeypatch)
    assert watch.handed(watching, lines, T0) == [f"PR {KEY}: {land.QUEUED} [o1 j1]"]
    assert posted(sent) == [("defect", f"meta@{demo}", f"{watch.LANDER}{failed}")]


def test_replayed_55635_a_landing_whose_diff_read_timed_out_reads_ready_naming_that_try_and_the_next_pass_lands_it_with_no_new_tap_or_defect(
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    waiting_job(demo)
    with edit(demo) as data:
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
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    enqueue_exiting(tmp_path, demo, 0)
    watching = watcher(demo)
    newer = tmp_path / "cache" / "9.9.9" / "bin" / "drive"

    class Execed(Exception):
        pass

    def execv(drive: Path, argv: list[str]) -> None:
        raise Execed(drive, argv)

    monkeypatch.setattr(install, "upgraded", lambda: newer)
    monkeypatch.setattr(install.os, "execv", execv)
    with pytest.raises(Execed) as execed:
        land.run(demo, [KEY])
    assert execed.value.args == (newer, [str(newer), "pr", "land", state.pr_url(KEY), "--drive", demo, "--outcome", land.QUEUED])
    assert state.stored(demo)["prs"][KEY]["lander"] is None
    monkeypatch.setattr(install, "upgraded", lambda: None)
    assert cli.main(["pr", "land", state.pr_url(KEY), "--drive", demo, "--outcome", land.QUEUED]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == f"{KEY}: {land.QUEUED}"
    assert state.load(demo)["prs"][KEY]["lander"]["outcome"] == land.QUEUED
    assert watch.event_lines(watching) == [f"PR {KEY}: {land.QUEUED} [o1 j1]"]


def test_an_approval_on_an_older_head_reads_approved_on_an_older_head_yet_his_approve_starts_its_lander(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    waiting_job(demo)
    with edit(demo) as data:
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
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approve(demo, KEY)
    stale = "awaiting approval on head from {} (approved 316c682c69, on an older head)"
    calls = enqueue_checking(tmp_path, monkeypatch, demo, f"#42 BLOCKED 34087d1264 {stale.format('forge-pr-reviewer[bot]')}; {stale.format('poetic-svc')}")
    assert land.run(demo, [KEY]) == land.QUEUED
    assert land.run(demo, [KEY]) == land.QUEUED
    assert calls.read_text() == "pr comment 42 -R Forge-AI/monorepo --body @forge-pr-reviewer review\n"
    assert state.load(demo)["prs"][KEY]["review_asked"] == "34087d1264"


def test_an_install_that_lands_while_the_lander_checks_a_pr_ends_no_landing_and_the_newest_install_records_its_outcome_and_the_head_it_asked_reviewed(
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approve(demo, KEY)
    calls = enqueue_checking(tmp_path, monkeypatch, demo, "#42 BLOCKED 34087d1264 awaiting approval on head from forge-pr-reviewer[bot] (approved 316c682c69, on an older head)")
    newer = tmp_path / "cache" / "9.9.9" / "bin" / "drive"

    class Execed(Exception):
        pass

    def execv(drive: Path, argv: list[str]) -> None:
        raise Execed(drive, argv)

    monkeypatch.setattr(install, "upgraded", lambda: newer)
    monkeypatch.setattr(install.os, "execv", execv)
    with pytest.raises(Execed) as execed:
        land.run(demo, [KEY])
    reopened = [str(newer), "pr", "land", state.pr_url(KEY), "--drive", demo, "--outcome", land.QUEUED, "--asked", KEY, "34087d1264"]
    assert execed.value.args == (newer, reopened)
    assert calls.read_text() == "pr comment 42 -R Forge-AI/monorepo --body @forge-pr-reviewer review\n"
    monkeypatch.setattr(install, "upgraded", lambda: None)
    assert cli.main(reopened[1:]) == 0
    pr = state.load(demo)["prs"][KEY]
    assert (pr["lander"]["outcome"], pr["review_asked"]) == (land.QUEUED, "34087d1264")


def test_a_head_grading_an_hour_gets_one_review_ask_from_the_watch_which_records_it_so_its_next_deadline_wakes_its_builder(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with edit(demo) as data:
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
    home_repo: None,
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
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approve(demo, KEY)
    calls = enqueue_checking(tmp_path, monkeypatch, demo, "#42 BLOCKED 34087d1264 GitHub still computing mergeability; GitHub merge state UNKNOWN")
    assert land.run(demo, [KEY]) == land.QUEUED
    assert not calls.exists()


def test_a_lander_whose_watch_stops_on_a_fix_says_its_blocked_line_having_asked_a_review_of_the_head_approved_only_on_an_older_one(
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approve(demo, KEY)
    blocked = "#42 BLOCKED 34087d1264 awaiting approval on head from poetic-svc (approved 316c682c69, on an older head); GitHub mergeability CONFLICTING; run ccx vcs stack submit for conflicts"
    stops = "watch stops: a PR needs a fix, not a wait"
    calls = enqueue_checking(tmp_path, monkeypatch, demo, blocked, f'printf "%s\\n" "{stops}" {shlex.quote(blocked)}; exit 1')
    assert land.run(demo, [KEY]) == f"not landed: stack-enqueue --watch exited 1: {stops}; {blocked}; its whole output is in {state.drive_dir(demo) / land.LOG}"
    assert calls.read_text() == "pr comment 42 -R Forge-AI/monorepo --body @forge-pr-reviewer review\n"


def test_a_lander_queues_nothing_while_a_pr_below_it_in_its_native_stack_lacks_his_approve_and_tries_again_once_he_approves_it(
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    stacked(demo)
    with edit(demo) as data:
        for key in (KEY, OTHER):
            data["prs"][key]["stack"] = [key]
    approve(demo, OTHER)
    head = enqueue_exiting(tmp_path, demo, 0)
    monkeypatch.setattr(land.github, "below", lambda key: ["Forge-AI/monorepo#41", KEY] if key == OTHER else [])
    held = (
        f"not landed: Forge-AI/monorepo#41, {KEY} sits below {OTHER} in its stack with neither his Approve nor an exempt class, and the merge queue queues a stack"
        " from its bottom, so landing a PR above would land it too; it lands once he approves each of them or they merge"
    )
    assert land.run(demo, [OTHER], dry_run=True) == held
    assert state.load(demo)["prs"][OTHER]["lander"] is None
    assert land.run(demo, [OTHER]) == held
    assert state.load(demo)["prs"][OTHER]["lander"]["below"] == ["Forge-AI/monorepo#41", KEY]
    assert land.start(demo, [OTHER], NO_CARDS) == []
    with edit(demo) as data:
        data["prs"]["Forge-AI/monorepo#41"] = state.new_pr(state.pr_url("Forge-AI/monorepo#41"), "j1", "o1") | {"state": "merged"}
    approve(demo, KEY)
    assert land.start(demo, [OTHER], NO_CARDS) == [(KEY, OTHER)]
    assert landers == [(KEY, OTHER)]
    monkeypatch.setattr(land.github, "below", lambda key: ["Forge-AI/monorepo#41"] if key == OTHER else [])
    with edit(demo) as data:
        data["prs"]["Forge-AI/monorepo#41"]["state"] = "open"
    assert land.run(demo, [KEY, OTHER], dry_run=True).startswith("not landed: Forge-AI/monorepo#41 sits below")
    monkeypatch.setattr(land.github, "below", lambda key: [KEY] if key == OTHER else [])
    assert land.run(demo, [KEY, OTHER], dry_run=True) == (
        f"would queue {KEY}, {OTHER} through stack-enqueue --watch from main at {head[:12]}: every PR below it is merged, approved by him or exempt"
    )


@pytest.mark.parametrize("verdict", ["unchecked", "objected"])
def test_his_approve_lands_a_green_pr_whatever_the_check_against_his_words_says(home_repo: None, demo: str, tmp_path: Path, landers: list[tuple[str, ...]], verdict: str) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    enqueue_exiting(tmp_path, demo, 0)
    with edit(demo) as data:
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
    with pytest.raises(state.DriveError, match="the drive lands a yasyf/\\* PR, or a PR he approved or an exempt class lands, a pure port only once its original merged or he approved it, and no other"):
        land.run(demo, [KEY])


ORIGINAL, ITS_PORT, ABOVE_IT = "Forge-AI/litestream#30", "Forge-AI/litestream#31", "Forge-AI/litestream#32"


@pytest.mark.parametrize("ends", ["his Approve", "its merge"])
def test_replayed_litestream_a_pure_port_of_a_pr_waiting_on_his_approve_reads_blocked_on_his_card_on_it_and_lands_only_once_he_approves_it_or_it_merges(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], ends: str
) -> None:
    green = {"checks": "pass", "review": "approved", "checked": CHECKED}
    with edit(demo) as data:
        data["jobs"]["j1"]["prs"] += [state.pr_url(ORIGINAL), state.pr_url(ITS_PORT)]
        data["prs"][ORIGINAL] = state.new_pr(state.pr_url(ORIGINAL), "j1", "o1") | green | {"head": "aaaaaaa111", "title": "Fix: retry the WAL upload"}
        data["prs"][ITS_PORT] = state.new_pr(state.pr_url(ITS_PORT), "j1", "o1") | green | {"head": "bbbbbbb222", "title": "Port: retry the WAL upload", "port_of": 30, "exempt": "pure port", "wants_tap": False}
    monkeypatch.setattr(land.github, "below", lambda key: [ITS_PORT])
    cards = NO_CARDS | {"pr_cards": [pr_card(ORIGINAL)]}
    data = state.load(demo)
    waits = f"it waits on {view.link('retry the WAL upload', state.pr_url(ORIGINAL))}, which waits on his Approve on {view.link('his card', CARD)}"
    assert (stage.of(data, ITS_PORT, cards), land.uncleared_below(data, [ABOVE_IT])) == (stage.Stage("blocked", stage.HIM, waits), [ITS_PORT])
    assert (land.start(demo, [ITS_PORT], cards), landers) == ([], [])
    with pytest.raises(state.DriveError, match=f"{ITS_PORT}: .*a pure port only once its original merged or he approved it"):
        land.run(demo, [ITS_PORT])
    assert cli.tap_verdict(data, data["prs"][ITS_PORT]) == (False, f"a pure port of {ORIGINAL}, which waits on its merge or his Approve on it")
    if ends == "his Approve":
        approve(demo, ORIGINAL)
    else:
        with edit(demo) as data:
            data["prs"][ORIGINAL]["state"] = "merged"
    data = state.load(demo)
    assert (stage.of(data, ITS_PORT, cards), land.uncleared_below(data, [ABOVE_IT])) == (stage.Stage("ready", stage.LANDER, 'green, its exempt class "pure port" lands it'), [])
    assert cli.tap_verdict(data, data["prs"][ITS_PORT]) == (True, "exempt: pure port")
    assert land.start(demo, [ITS_PORT], cards) == [(ITS_PORT,)]


def test_a_lander_merges_a_pr_he_approved_in_another_repository_with_gh_pr_merge_at_the_head_its_stage_read_ready_by_the_method_its_repository_allows(
    demo: str, monkeypatch: pytest.MonkeyPatch, pr_reads: dict[str, dict]
) -> None:
    docs = "Forge-AI/design-docs#5"
    tap = {"label": "Approve", "at": state.stamp(T0), "head": "ddddddd444", "source": "pressed the button", "picks": {}, "card": None}
    with edit(demo) as data:
        data["prs"][docs] = state.new_pr(state.pr_url(docs), "j1", "o1") | {"head": "ddddddd444", "tap": tap, "merge_method": "rebase"}
    runs: list[list[str]] = []
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: runs.append(argv) or land.subprocess.CompletedProcess(argv, 0, "", ""))
    pr_reads[docs] = read(state="MERGED", mergeCommit="eee555fff666")
    monkeypatch.setattr(github, "files", lambda key: ["README.md"])
    monkeypatch.setattr(install, "touched", lambda repo, files: [])
    assert land.run(demo, [docs]) == "merged at eee555fff666; it changes no plugin installed on this Mac"
    assert land.run(demo, [docs], dry_run=True) == "would merge it with gh pr merge --rebase --match-head-commit ddddddd444"
    assert runs == [["gh", "pr", "merge", "5", "-R", "Forge-AI/design-docs", "--rebase", "--match-head-commit", "ddddddd444"]]


def test_an_exempt_monorepo_pr_is_queued_once_green_with_no_tap_unless_its_files_put_it_outside_its_class(
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["prs"][KEY] |= {"checks": checks, "conflict": conflict}
    sent = posts(monkeypatch)
    line = f"TAP Approve {KEY} [o1 j1]"
    watch.sent(watcher(demo), "root", [line], T0)
    assert posted(sent) == []


def test_a_pr_trunk_ejects_from_the_merge_queue_is_said_once_and_reads_unlanded_waking_the_session_holding_its_order(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    waiting_job(demo)
    approve(demo, KEY)
    with edit(demo) as data:
        data["prs"][KEY] |= {"head": "aaaaaaa111", "lander": {"head": "aaaaaaa111", "pid": 1, "at": state.stamp(T0), "outcome": land.QUEUED, "below": []}}
    serve(monkeypatch, {KEY: read(labels=[github.EJECTED_LABEL], ejected_at=state.stamp(T0 + timedelta(minutes=1)))})
    watching = watcher(demo)
    unlanded = f"STAGE {KEY} unlanded since {state.stamp(T0)}, owed by the session holding its order: Trunk ejected it from the merge queue [o1 j1]"
    lines = watch.run_pass(watching, T0)
    assert lines == [f"PR {KEY}: ejected from the merge queue [o1 j1]", unlanded]
    assert (watch.wakes(lines[0], state.load(demo), NO_CARDS), watch.wakes(unlanded, state.load(demo), NO_CARDS)) == (False, True)
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []


def test_trunk_failing_a_stack_at_its_top_pr_reads_every_pr_its_lander_queued_ejected_until_one_is_queued_again_alone(
    home_repo: None,
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
    with edit(demo) as data:
        data["prs"][KEY] |= {"head": "566abed000", "lander": {"head": "566abed000", "pid": 1, "at": tried, "outcome": land.QUEUED, "below": [], "entry": KEY}}
    serve(monkeypatch, {KEY: read(headRefOid="566abed000", labels=["externally-merged", github.EJECTED_LABEL], ejected_at="2026-11-12T13:48:03Z")})
    assert watch.github_lines(watcher(demo), state.parse_time(tried) + timedelta(minutes=1)) == []
    assert stage.of(state.load(demo), KEY, NO_CARDS).name == "landing"


def test_replayed_monorepo_56508_a_pr_trunk_cancelled_after_its_landers_try_queued_it_is_out_of_the_queue_and_waits_on_its_own_wait_never_landing(
    named_paths: None,
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    approve(demo, KEY)
    queued = state.stamp(T0)
    with edit(demo) as data:
        data["jobs"]["j2"] = job()
        data["prs"][KEY] |= {"checks": "pass", "review": "approved", "waits": ["j2"], "lander": {"head": "aaaaaaa111", "pid": 1, "at": queued, "outcome": land.QUEUED, "below": [], "entry": KEY}}
    assert stage.of(state.load(demo), KEY, NO_CARDS).name == "waiting"
    cancelled = {"context": "Trunk Merge Queue (dev)", "state": "CANCELLED"}
    serve(monkeypatch, {KEY: read(checks=[*GREEN, cancelled], reviewDecision="APPROVED", approvals=["aaaaaaa111"], labels=["externally-merged", github.CANCELLED_LABEL], cancelled_at=state.stamp(T0 + timedelta(minutes=50)))})
    assert watch.github_lines(watcher(demo), T0 + timedelta(hours=2)) == [f"PR {KEY}: {land.DEQUEUED} [o1 j1]"]
    pr = state.load(demo)["prs"][KEY]
    assert (pr["lander"]["outcome"], land.landing(pr), stage.of(state.load(demo), KEY, NO_CARDS).name) == (land.DEQUEUED, False, "waiting")
    with edit(demo) as data:
        data["jobs"]["j2"]["state"] = "done"
    assert stage.of(state.load(demo), KEY, NO_CARDS) == stage.Stage("unlanded", stage.ORDER, f"its lander's try at aaaaaaa, started {queued}, ended {land.DEQUEUED}")
    assert watch.github_lines(watcher(demo), T0 + timedelta(hours=3)) == []


@pytest.mark.parametrize("labels", [[], [github.EJECTED_LABEL]])
def test_a_pr_its_landers_try_left_queued_for_merge_reads_red_and_wakes_its_builder_once_github_reads_it_conflicting_whatever_trunk_did_with_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], landers: list[tuple[str, ...]], labels: list[str]
) -> None:
    approve(demo, KEY)
    monkeypatch.setattr(watch.view, "cards", lambda drive: NO_CARDS)
    queued = state.stamp(T0)
    with edit(demo) as data:
        data["prs"][KEY] |= {"checks": "pass", "review": "approved", "lander": {"head": "aaaaaaa111", "pid": 1, "at": queued, "outcome": land.QUEUED, "below": [], "entry": KEY}}
    assert stage.of(state.load(demo), KEY, NO_CARDS) == stage.Stage("landing", stage.LANDER, f"queued for merge at aaaaaaa since {queued}")
    ejected_at = state.stamp(T0 + timedelta(minutes=5)) if labels else None
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED", approvals=["aaaaaaa111"], mergeable="CONFLICTING", labels=labels, ejected_at=ejected_at)})
    moment = T0 + timedelta(minutes=10)
    watch.run_pass(watcher(demo), moment)
    assert stage.of(state.load(demo), KEY, NO_CARDS) == stage.Stage("red", stage.BUILDER, "it conflicts with its base main")
    assert (woken, landers) == ([(KEY, f"{KEY} red since {state.stamp(moment)}, owed by its builder: it conflicts with its base main [o1 j1]")], [])


EJECTED = "Forge-AI/monorepo#55582"
FLAKE_FIX = "Forge-AI/monorepo#55677"
FLAKY = "TestCancelledFanOutInsideAnUnjournaledFnRecordsItsUsageOnce"
TRUNK_FAILED = {"context": "Trunk Merge Queue (dev)", "state": "FAILURE"}


def ejected_on_a_flake(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with edit(name) as data:
        data["jobs"]["j1"]["prs"] += [state.pr_url(EJECTED), state.pr_url(FLAKE_FIX)]
        data["prs"][EJECTED] = state.new_pr(state.pr_url(EJECTED), "j1", "o1") | {
            "head": "bb28d680bf",
            "base": "main",
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


def test_a_forge_ai_pr_he_approved_changing_sandsql_waits_for_his_next_word_while_he_is_away_and_one_changing_only_its_tests_lands(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    files = {EJECTED: ["go/sand/internal/sandsql/write.go", "go/sand/internal/sandsql/write_test.go"], FLAKE_FIX: ["go/sand/internal/sandsql/write_test.go"]}
    monkeypatch.setattr(github, "files", files.__getitem__)
    with edit(demo) as data:
        data["owner"]["heard"] = state.stamp(T0)
        data["jobs"]["j1"]["prs"] += [state.pr_url(EJECTED), state.pr_url(FLAKE_FIX)]
        data["prs"][EJECTED] = state.new_pr(state.pr_url(EJECTED), "j1", "o1") | {"head": "bb28d680bf", "checks": "pass", "review": "approved", "tap": state.new_tap("Approve", "bb28d680bf", "pressed the button", at=state.stamp(T0))}
        data["prs"][FLAKE_FIX] = state.new_pr(state.pr_url(FLAKE_FIX), "j1", "o1") | {"head": "9a882a9000", "checks": "pass", "review": "approved", "exempt": "test-only", "wants_tap": False}
    away = T0 + timedelta(minutes=20)
    refused = land.unattended(state.load(demo), EJECTED, away)
    assert refused == (
        "he last wrote or tapped 20 minutes ago, and it changes go/sand/internal/sandsql/write.go, which can break FDE or customer runs or lose their data,"
        f" so it lands only while he is present, his word or tap within 15 minutes, at his next word, by his words {state.PRESENCE_WORDS}"
    )
    assert land.unattended(state.load(demo), FLAKE_FIX, away) is None
    assert land.unattended(state.load(demo), EJECTED, T0 + timedelta(minutes=10)) is None
    monkeypatch.setattr(state, "now", lambda: away)
    with edit(demo) as data:
        data["prs"][EJECTED]["lander"] = {"head": "bb28d680bf", "base": data["prs"][EJECTED]["base"], "pid": 1, "at": state.stamp(away), "outcome": f"{land.AWAY}: {refused}", "below": [], "entry": EJECTED}
    assert stage.of(state.load(demo), EJECTED, NO_CARDS) == stage.Stage("uncarded", stage.WATCH, "his card on it is not drawn yet")
    assert stage.of(state.load(demo), FLAKE_FIX, NO_CARDS).name == "ready"
    with edit(demo) as data:
        data["owner"]["heard"] = state.stamp(away)
    assert stage.of(state.load(demo), EJECTED, NO_CARDS).name == "ready"


def test_a_dangerous_pr_he_approved_waiting_on_his_word_while_he_is_away_gets_a_card_whose_tap_is_his_word_and_lands_it(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "files", {EJECTED: ["go/sand/internal/sandsql/write.go"]}.__getitem__)
    with edit(demo) as data:
        data["owner"]["heard"] = state.stamp(T0)
        data["jobs"]["j1"]["prs"].append(state.pr_url(EJECTED))
        data["prs"][EJECTED] = state.new_pr(state.pr_url(EJECTED), "j1", "o1") | {"head": "bb28d680bf", "checks": "pass", "review": "approved", "tap": state.new_tap("Approve", "bb28d680bf", "pressed the button", at=state.stamp(T0))}
    away = T0 + timedelta(minutes=20)
    monkeypatch.setattr(state, "now", lambda: away)
    data = state.load(demo)
    assert state.tap_refused(data, data["prs"][EJECTED]) == "he approved it already"
    refused = land.unattended(data, EJECTED, away)
    with edit(demo) as data:
        data["prs"][EJECTED]["lander"] = {"head": "bb28d680bf", "base": data["prs"][EJECTED]["base"], "pid": 1, "at": state.stamp(away), "outcome": f"{land.AWAY}: {refused}", "below": [], "entry": EJECTED}
    data = state.load(demo)
    read = {"url": state.pr_url(EJECTED), "headRefOid": "bb28d680bf"}
    assert state.asks_tap(data, data["prs"][EJECTED])
    assert stage.of(data, EJECTED, NO_CARDS) == stage.Stage("uncarded", stage.WATCH, "his card on it is not drawn yet")
    assert card.ask_waits([(read, data["prs"][EJECTED])]) != card.ask_waits([(read, {"tap": None})])
    card.hear_tap(demo)
    data = state.load(demo)
    assert not state.asks_tap(data, data["prs"][EJECTED])
    assert stage.of(data, EJECTED, NO_CARDS).name == "ready"

@pytest.mark.parametrize(
    ("part", "posted", "said"),
    [
        (state.STAGING_TEST.format(number=42), "https://poetic.slack.com/archives/C0STAGING/p1794600000000100", f"WAITING {state.pr_url(KEY)} to merge: DONE it soaked twice on staging"),
        ("Has this been tested on staging? It's approved once it is", f"{state.pr_url(KEY)} live: repro/j5420.sh exits 0 on long-running@skills 1.0.3", f"WAITING {state.pr_url(KEY)} to merge: DONE its proof answered him"),
    ],
    ids=["its staging test part holds its tester's proof", "its tester's proof delivered his question part"],
)
def test_replayed_717646_an_approved_dangerous_pr_whose_last_wait_ended_on_a_delivered_part_while_its_tester_returned_waiting_lands_only_at_his_next_word(
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, part: str, posted: str, said: str
) -> None:
    monkeypatch.setattr(github, "files", {KEY: ["go/sand/internal/sandsql/write.go"]}.__getitem__)
    enqueue_exiting(tmp_path, demo, 0)
    with edit(demo) as data:
        data["owner"]["heard"] = state.stamp(T0)
        data["orders"]["o1"]["parts"][part] = state.new_part() | {"posted": posted, "posted_by": "j5420"}
        data["orders"]["o1"]["jobs"].append("j5420")
        data["jobs"]["j5420"] = job(goal="soak #42 on staging", parts=[part], builds_nothing=True, state="waiting", said=said)
        data["prs"][KEY] |= {"checks": "pass", "review": "approved", "waits": [f"o1:{part}"]}
    approve(demo, KEY)
    away = T0 + timedelta(minutes=20)
    monkeypatch.setattr(state, "now", lambda: away)
    data = state.load(demo)
    assert (state.delivered(data, "o1", part), land.staged(data, data["prs"][KEY]), stage.of(data, KEY, NO_CARDS).name) == (True, [], "ready")
    held = f"{land.AWAY}: {land.unattended(data, KEY, away)}"
    assert land.run(demo, [KEY]) == held
    assert stage.of(state.load(demo), KEY, NO_CARDS) == stage.Stage("uncarded", stage.WATCH, "his card on it is not drawn yet")
    with edit(demo) as data:
        data["owner"]["heard"] = state.stamp(away)
    assert land.run(demo, [KEY]) == land.QUEUED


def test_a_pr_he_approved_before_he_went_quiet_whose_builder_staged_it_waits_for_his_next_word_once_its_staging_test_passes_whatever_its_paths(home_repo: None, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "files", {EJECTED: ["infra/rows/router/router.ts"]}.__getitem__)
    part = state.STAGING_TEST.format(number=55582)
    with edit(demo) as data:
        data["owner"]["heard"] = state.stamp(T0)
        data["orders"]["o1"]["parts"][part] = state.new_part()
        data["jobs"]["j1"]["prs"] += [state.pr_url(EJECTED)]
        data["prs"][EJECTED] = state.new_pr(state.pr_url(EJECTED), "j1", "o1") | {"head": "bb28d680bf", "checks": "pass", "review": "approved", "tap": state.new_tap("Approve", "bb28d680bf", "pressed the button", at=state.stamp(T0))}
    assert land.dangerous(state.load(demo), github.files(EJECTED)) == []
    assert land.unattended(state.load(demo), EJECTED, T0 + timedelta(minutes=20)) == (
        f"he last wrote or tapped 20 minutes ago, and o1 holds its {part} part, given only to a change its builder read as dangerous, which can break FDE or customer runs"
        f" or lose their data, so it lands only while he is present, his word or tap within 15 minutes, at his next word, by his words {state.PRESENCE_WORDS}"
    )
    assert land.unattended(state.load(demo), EJECTED, T0 + timedelta(minutes=10)) is None
    with edit(demo) as data:
        del data["orders"]["o1"]["parts"][part]
    assert land.unattended(state.load(demo), EJECTED, T0 + timedelta(minutes=20)) is None


def test_replayed_55582_a_pr_trunk_ejected_on_a_flaky_test_outside_its_diff_is_queued_again_at_the_same_commit_once_that_tests_fix_merges_and_only_once(
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], landers: list[tuple[str, ...]]
) -> None:
    ejected_on_a_flake(demo, monkeypatch)
    assert cli.main(["pr", "set", EJECTED, "--flaky", FLAKY, "Forge-AI/monorepo#99999", "--drive", demo]) == 2
    assert "--flaky Forge-AI/monorepo#99999: the drive tracks no PR Forge-AI/monorepo#99999" in capsys.readouterr().err
    assert cli.main(["pr", "set", KEY, "--flaky", FLAKY, state.pr_url(FLAKE_FIX), "--drive", demo]) == 2
    assert "at aaaaaaa none of its checks fail and Trunk ejected no try of its lander" in capsys.readouterr().err
    assert cli.main(["pr", "set", EJECTED, "--flaky", FLAKY, state.pr_url(FLAKE_FIX), "--drive", demo]) == 0
    waiting = f"Trunk ejected it from the merge queue at bb28d68 on its flaky test {FLAKY}; the lander requeues that commit once {FLAKE_FIX}, that test's fix, merges"
    assert stage.of(state.load(demo), EJECTED, NO_CARDS) == stage.Stage("waiting", "j1", waiting)
    assert land.start(demo, [EJECTED], NO_CARDS) == []
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["prs"][FLAKE_FIX]["state"] = "closed"
    closed = f"Trunk ejected it from the merge queue on its flaky test {FLAKY}, and {FLAKE_FIX}, that test's fix, closed unmerged"
    assert stage.of(state.load(demo), EJECTED, NO_CARDS) == stage.Stage("unlanded", stage.ORDER, closed)


def test_replayed_56971_a_pr_trunk_ejected_on_a_test_that_fails_only_as_the_date_changes_is_queued_again_at_the_same_commit_at_once_by_its_cause_and_only_once(
    home_repo: None,
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], landers: list[tuple[str, ...]]
) -> None:
    ejected_on_a_flake(demo, monkeypatch)
    midnight = "its two renders straddled midnight UTC, and each reads the date"
    with edit(demo) as data:
        data["prs"][EJECTED]["checks"] = "fail"
    assert cli.main(["pr", "set", EJECTED, "--flaky", FLAKY, midnight, "--drive", demo]) == 2
    assert f"--flaky {FLAKY} names its cause and no fix PR, which queues again only a green head Trunk ejected, and its own checks fail at bb28d68" in capsys.readouterr().err
    with edit(demo) as data:
        data["prs"][EJECTED]["checks"] = "pass"
    assert cli.main(["pr", "set", EJECTED, "--flaky", FLAKY, midnight, "--drive", demo]) == 0
    assert stage.of(state.load(demo), EJECTED, NO_CARDS) == stage.Stage("ready", stage.LANDER, "green and approved by him at bb28d68")
    assert land.start(demo, [EJECTED], NO_CARDS) == [(EJECTED,)]
    enqueue_exiting(tmp_path, demo, 0)
    capsys.readouterr()
    assert land.run(demo, [EJECTED]) == land.QUEUED
    said = capsys.readouterr().out
    assert ("trunk said --check --flake 55582" in said, "trunk said --watch --flake 55582" in said) == (True, True)
    requeued = state.load(demo)["prs"][EJECTED]
    landing = f"queued for merge at bb28d68 since {requeued['lander']['at']}, requeued on its flaky test {FLAKY} ({midnight})"
    assert stage.of(state.load(demo), EJECTED, NO_CARDS) == stage.Stage("landing", stage.LANDER, landing)
    again = state.stamp(state.parse_time(requeued["lander"]["at"]) + timedelta(minutes=5))
    trunk = read(headRefOid="bb28d680bf", checks=[*GREEN, TRUNK_FAILED], reviewDecision="APPROVED", approvals=["bb28d680bf"], labels=[github.EJECTED_LABEL])
    serve(monkeypatch, {EJECTED: trunk | {"ejected_at": again}})
    assert f"PR {EJECTED}: ejected from the merge queue [o1 j1]" in watch.github_lines(watcher(demo), state.parse_time(again))
    final = f"Trunk ejected it from the merge queue again after the lander requeued bb28d68 on its flaky test {FLAKY} ({midnight})"
    assert stage.of(state.load(demo), EJECTED, NO_CARDS) == stage.Stage("unlanded", stage.ORDER, final)
    assert land.start(demo, [EJECTED], NO_CARDS) == []


RED_AT_ITS_HEAD = "Forge-AI/litestream#23"
ITS_FLAKE_FIX = "Forge-AI/litestream#25"
ITS_FLAKY = "TestDB_AWriterDuringACheckpointOfALargeDatabaseWaitsUnderItsBusyTimeout"
VFS_RED = [{"name": "VFS Unit Test", "conclusion": "FAILURE"}, {"name": "Build", "conclusion": "SUCCESS"}]
WAITS_ON_ITS_FLAKE_FIX = stage.Stage(
    "waiting",
    "j1",
    f"its checks fail at bc4822d: VFS Unit Test, on its flaky test {ITS_FLAKY} outside its diff; its builder restacks it onto {ITS_FLAKE_FIX}, that test's fix, once it merges",
)


def red_on_a_flake(name: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]) -> dict[str, dict]:
    with edit(name) as data:
        data["jobs"]["j1"]["prs"] += [state.pr_url(RED_AT_ITS_HEAD), state.pr_url(ITS_FLAKE_FIX)]
        for key, head in ((RED_AT_ITS_HEAD, "bc4822d7b4"), (ITS_FLAKE_FIX, "746b968c20")):
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"head": head, "wants_tap": False, "checked": CHECKED | {"head": head}}
    reads = {
        RED_AT_ITS_HEAD: read(headRefOid="bc4822d7b4", checks=VFS_RED, required_checks=[], required_reviews=0),
        ITS_FLAKE_FIX: read(headRefOid="746b968c20", checks=GREEN, required_checks=[], required_reviews=0),
    }
    serve(monkeypatch, reads)
    watch.run_pass(watcher(name), T0)
    assert stage.of(state.load(name), RED_AT_ITS_HEAD, NO_CARDS) == stage.Stage("red", stage.BUILDER, "its checks fail at bc4822d: VFS Unit Test")
    assert [key for key, _ in woken] == [RED_AT_ITS_HEAD]
    assert cli.main(["pr", "set", RED_AT_ITS_HEAD, "--flaky", ITS_FLAKY, state.pr_url(ITS_FLAKE_FIX), "--drive", name]) == 0
    assert state.load(name)["prs"][RED_AT_ITS_HEAD]["flaky"]["failed_checks"] == ["VFS Unit Test"]
    return reads


@pytest.mark.parametrize(
    ("ended", "then"),
    [("MERGED", "merged, so restack it onto that fix"), ("CLOSED", "closed unmerged, so fix that test at its cause")],
)
def test_replayed_litestream_23_a_pr_red_at_its_own_head_on_a_flaky_test_waits_on_that_tests_fix_waking_the_fixs_job_hourly_then_wakes_its_builder_once_the_fix_ends(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], ended: str, then: str
) -> None:
    reads = red_on_a_flake(demo, monkeypatch, woken)
    for minutes, wakes in ((1, 1), (31, 1), (61, 2), (91, 2)):
        watch.run_pass(watcher(demo), T0 + timedelta(minutes=minutes))
        assert (stage.of(state.load(demo), RED_AT_ITS_HEAD, NO_CARDS), [key for key, _ in woken].count(RED_AT_ITS_HEAD)) == (WAITS_ON_ITS_FLAKE_FIX, wakes)
    reads[ITS_FLAKE_FIX] = read(headRefOid="746b968c20", checks=GREEN, required_checks=[], required_reviews=0, state=ended)
    watch.run_pass(watcher(demo), T0 + timedelta(minutes=95))
    red = f"its checks fail at bc4822d: VFS Unit Test; {ITS_FLAKE_FIX}, the fix of its flaky test {ITS_FLAKY}, {then}"
    data = state.load(demo)
    assert (stage.of(data, RED_AT_ITS_HEAD, NO_CARDS), land.requeues(data, data["prs"][RED_AT_ITS_HEAD])) == (stage.Stage("red", stage.BUILDER, red), False)
    assert [wake for wake in woken if wake[0] == RED_AT_ITS_HEAD][2:] == [(RED_AT_ITS_HEAD, f"{RED_AT_ITS_HEAD} red since {state.stamp(T0 + timedelta(minutes=95))}, owed by its builder: {red} [o1 j1]")]
    watch.run_pass(watcher(demo), T0 + timedelta(minutes=100))
    assert [key for key, _ in woken].count(RED_AT_ITS_HEAD) == 3


def test_a_pr_red_at_its_own_head_on_a_flaky_test_reads_red_again_once_a_check_its_flaky_record_lacks_fails_there(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]]
) -> None:
    reads = red_on_a_flake(demo, monkeypatch, woken)
    watch.run_pass(watcher(demo), T0 + timedelta(minutes=1))
    assert stage.of(state.load(demo), RED_AT_ITS_HEAD, NO_CARDS) == WAITS_ON_ITS_FLAKE_FIX
    reads[RED_AT_ITS_HEAD] = read(headRefOid="bc4822d7b4", checks=[*VFS_RED, {"name": "Lint", "conclusion": "FAILURE"}], required_checks=[], required_reviews=0)
    watch.run_pass(watcher(demo), T0 + timedelta(minutes=2))
    assert stage.of(state.load(demo), RED_AT_ITS_HEAD, NO_CARDS) == stage.Stage("red", stage.BUILDER, "its checks fail at bc4822d: Lint, VFS Unit Test")
    assert [key for key, _ in woken] == [RED_AT_ITS_HEAD, RED_AT_ITS_HEAD]


BOTTOM, TOP = "Forge-AI/monorepo#49869", "Forge-AI/monorepo#53227"
REFUSED_BY_GITHUB = f"{land.REFUSED} once Trunk's merge queue took {TOP}: GITHUB_API_MERGE_PR_FAILED, with no failing check"


def trunk_failed(key: str, reason: str, at: str, failing: str = "") -> dict:
    return {"seq": "7", "kind": "queue", "at": at, "text": f"{key} {agents.TRUNK_FAILED}: {reason}", "fields": {"outcome": "failed", "attrs": {"reason": reason, "failing": failing, "target": "dev"}}, "refs": {"prs": [key]}}


def queued_stack(name: str, monkeypatch: pytest.MonkeyPatch, at: datetime = T0) -> None:
    waiting_job(name)
    with edit(name) as data:
        for key, head in ((BOTTOM, "aaaaaaa111"), (TOP, "bbbbbbb222")):
            lander = {"head": head, "pid": 1, "at": state.stamp(at), "outcome": land.QUEUED, "was": None, "below": [], "entry": TOP}
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {
                "head": head,
                "base": "main",
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
    with edit(demo) as data:
        data["prs"][BOTTOM] |= {"state": "merged", "lander": data["prs"][BOTTOM]["lander"] | {"outcome": land.QUEUED}}
    assert land.start(demo, [TOP], NO_CARDS) == [(TOP,)]
    assert landers == [(BOTTOM,), (TOP,)]


def test_a_second_refusal_of_a_pr_landed_alone_at_the_same_head_reads_unlanded_waking_the_session_holding_its_order(
    demo: str, monkeypatch: pytest.MonkeyPatch, trunk_queue: list[dict]
) -> None:
    alone = T0 + timedelta(hours=1)
    queued_stack(demo, monkeypatch, alone)
    with edit(demo) as data:
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


@pytest.mark.parametrize("mark", ["incident", "priority"])
def test_a_yasyf_lander_merges_with_admin_records_its_merge_commit_and_installs_at_once_each_plugin_a_merge_for_an_incident_or_priority_order_changes(
    demo: str, monkeypatch: pytest.MonkeyPatch, pr_reads: dict[str, dict], mark: str
) -> None:
    runs: list[list[str]] = []
    pr_reads["yasyf/cc-skills#7"] = read(state="MERGED", mergeCommit="ccc333ddd444eee")
    monkeypatch.setattr(github, "files", lambda key: ["plugins/long-running/lib/drive/land.py", "README.md"])
    monkeypatch.setattr(install, "touched", lambda repo, files: ["long-running@skills"] if "plugins/long-running/lib/drive/land.py" in files else [])
    monkeypatch.setattr(
        land.subprocess, "run", lambda argv, **kwargs: runs.append(argv) or land.subprocess.CompletedProcess(argv, 0, "long-running@skills 1.0.4 installed\n", "")
    )
    with edit(demo) as data:
        data["orders"]["o1"][mark] = True if mark == "incident" else state.priority_seat("o1")
        data["prs"]["yasyf/cc-skills#7"] = state.new_pr("https://github.com/yasyf/cc-skills/pull/7", "j1", "o1")
    assert land.merged(demo, "yasyf/cc-skills#7", "squash", ["--admin"]) == "merged at ccc333ddd444; long-running@skills 1.0.4 installed"
    assert {field: state.load(demo)["prs"]["yasyf/cc-skills#7"][field] for field in ("state", "merged_sha")} == {"state": "merged", "merged_sha": "ccc333ddd444eee"}
    assert runs == [
        ["gh", "pr", "merge", "7", "-R", "yasyf/cc-skills", "--squash", "--admin"],
        [str(land.DRIVE), "install", "long-running@skills", "--sha", "ccc333ddd444eee", "--drive", "demo"],
    ]
    assert train.read()["boarded"] == {}


def test_a_yasyf_merge_for_any_other_order_boards_the_install_train_and_installs_nothing_until_it_leaves(demo: str, monkeypatch: pytest.MonkeyPatch, pr_reads: dict[str, dict]) -> None:
    runs: list[list[str]] = []
    pr_reads["yasyf/cc-skills#7"] = read(state="MERGED", mergeCommit="ccc333ddd444eee")
    monkeypatch.setattr(github, "files", lambda key: ["plugins/long-running/lib/drive/land.py"])
    monkeypatch.setattr(install, "touched", lambda repo, files: ["long-running@skills"])
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: runs.append(argv) or land.subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(state, "now", lambda: T0)
    with edit(demo) as data:
        data["prs"]["yasyf/cc-skills#7"] = state.new_pr("https://github.com/yasyf/cc-skills/pull/7", "j1", "o1")
    assert land.merged(demo, "yasyf/cc-skills#7", "squash", ["--admin"]) == f"merged at ccc333ddd444; long-running@skills boarded the install train, which leaves by {T0.strftime('%-I:%M %p')}"
    assert runs == [["gh", "pr", "merge", "7", "-R", "yasyf/cc-skills", "--squash", "--admin"]]
    assert train.read()["boarded"] == {"long-running@skills": {"sha": "ccc333ddd444eee", "repo": "yasyf/cc-skills", "merges": {"yasyf/cc-skills#7": "ccc333ddd444eee"}}}


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
    with edit(demo) as data:
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
        ("merged", f"{install.PROOF_FAILED}: https://github.com/{MERGED_PR.replace('#', '/pull/')}: /p.sh exits 1", True),
        ("closed", "closed", True),
        ("closed", "checks failed", False),
    ],
)
def test_a_line_on_a_pr_no_longer_open_wakes_the_holder_only_when_it_says_how_the_pr_ended_never_for_a_move_made_while_it_was_open(
    demo: str, row: str, change: str, wakes: bool
) -> None:
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["prs"][YASYF] |= {"head": "bbbbbbb222", "lander": {"head": "bbbbbbb222", "child": state.born(os.getpid()), "state": "adopted", "at": state.stamp(T0), "outcome": None}}
    assert not watch.wakes(line, state.load(demo), NO_CARDS)
    with edit(demo) as data:
        data["prs"][YASYF]["state"] = "merged"
    assert not watch.wakes(line, state.load(demo), NO_CARDS)
    assert watch.wakes("STAGE not landed: #7 waits on a review of head bbbbbbb [o1 j1]", state.load(demo), NO_CARDS)


def test_a_line_held_for_the_meta_session_is_judged_as_it_posts_so_a_pr_that_merged_meanwhile_hands_over_only_how_it_ended(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    tooling_order(demo, "done")
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["prs"][key] = state.new_pr(url, "j1", "o1") | {"head": "aaaaaaa111", "wants_tap": False, "checked": CHECKED}
        data["jobs"]["j1"].update(said=f"PUSHED {url}", prs=[url])
    serve(monkeypatch, {key: read(required_checks=[], mergeable=mergeable)})
    sent = posts(monkeypatch)
    watch.run_pass(watcher(demo), T0 + timedelta(minutes=1))
    readies = [text for _, to, text in posted(sent) if to == f"meta@{demo}" and text.startswith(f"JOB j1 o1 READY {url}")]
    assert (len(readies), [woke for woke, _ in woken]) == (int(handed), [key] if mergeable == "CONFLICTING" else [])


def merged_ready(name: str, key: str, repro: str | None) -> None:
    with edit(name) as data:
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
    proof = state.load(demo)["jobs"]["j1"]["proof"]
    assert ({field: proof[field] for field in ("starter", "child", "state", "at", "said", "version")}, state.holder(proof)) == (
        {"starter": state.born(os.getpid()), "child": state.born(os.getpid()), "state": "reserved", "at": state.stamp(T0), "said": said, "version": state.VERSION},
        os.getpid(),
    )
    assert watch.run_pass(watching, T0 + timedelta(minutes=31)) == []
    assert provers == ["j1"]
    assert not watch.wakes(f"PR {YASYF}: merged at ccc333ddd444; long-running@skills 1.0.4 installed [o1 j1]", state.load(demo), NO_CARDS)


def test_a_merged_ready_job_waits_for_its_lander_before_its_proof_starts(demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, YASYF, proof_file(tmp_path))
    with edit(demo) as data:
        data["prs"][YASYF]["lander"] = {"head": "aaaaaaa111", "child": state.born(os.getpid()), "state": "adopted", "at": state.stamp(T0), "outcome": None}
    assert (watch.run_pass(watcher(demo), T0), provers) == ([], [])


def test_a_merged_ready_job_whose_merge_rides_the_install_train_gets_its_proof_only_once_the_trains_install_of_it_ended(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, YASYF, proof_file(tmp_path))
    train.board("long-running@skills", "yasyf/cc-skills", YASYF, "ccc333ddd444eee")
    watching = watcher(demo)
    assert (watch.run_pass(watching, T0), provers) == ([], [])
    assert watch.proving(state.load(demo), "j1")
    with train.boarding() as board:
        board.update(boarded={}, left=state.stamp(T0), carried={"long-running@skills": board["boarded"]["long-running@skills"] | {"pid": os.getpid()}})
    assert (watch.run_pass(watching, T0 + timedelta(minutes=1)), provers) == ([], [])
    ended = subprocess.Popen(["true"])
    ended.wait()
    with train.boarding() as board:
        board["carried"]["long-running@skills"]["pid"] = ended.pid
    watch.run_pass(watching, T0 + timedelta(minutes=2))
    assert provers == ["j1"]


def test_a_merged_ready_job_beside_a_product_merge_hands_it_and_the_parts_it_carries_to_a_prover_before_its_own_proof_proves_the_yasyf_merge_alone(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    live_reads(monkeypatch)
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {KEY: {"components": ["api"], "touched": ["api"], "dev": [], "releases": ["api"]}})
    merged_ready(demo, YASYF, proof_file(tmp_path))
    with edit(demo) as data:
        parts = data["jobs"]["j1"]["parts"]
        data["prs"][KEY] |= {"merged_sha": "fff666aaa777bbb"}
        data["jobs"]["j1"].update(said=f"READY {state.pr_url(YASYF)} {state.pr_url(KEY)}", prs=[state.pr_url(YASYF), state.pr_url(KEY)])
    watching = watcher(demo)
    lines = watch.run_pass(watching, T0)
    data = state.load(demo)
    assert [line.partition(":")[0] for line in lines if line.startswith("JOB ")] == [f"JOB j2 o1 added, proves {KEY}, waits on the next production release cut, which the watch binds it to once a cut carries its merges"]
    assert (data["jobs"]["j2"]["parts"], provers) == (parts, [])
    assert [state.tester_of(data, "o1", part) for part in parts] == ["j2"] * len(parts)
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    assert (provers, install.proved_prs(state.load(demo), "j1")) == (["j1"], [YASYF])


SUPERSEDED = "yasyf/cc-skills#8"


@pytest.mark.parametrize(("said", "started"), [(f"READY {state.pr_url(YASYF)}", ["j1"]), (f"READY {state.pr_url(YASYF)} {state.pr_url(SUPERSEDED)}", [])])
def test_a_merged_ready_jobs_proof_counts_the_prs_its_line_names_so_a_closed_pr_the_line_leaves_out_holds_nothing_and_one_it_names_holds_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path, said: str, started: list[str]
) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, YASYF, proof_file(tmp_path))
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["jobs"]["j1"]["repro"] = proof_file(tmp_path)
    assert [watch.run_pass(watching, T0 + timedelta(minutes=minutes)) for minutes in (31, 62)] == [[], []]
    assert provers == []


def test_a_merged_ready_product_job_takes_done_at_its_merge_and_the_watch_adds_the_builder_proving_it_live_waiting_on_the_next_release_cut(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path, ready_records: list[list[dict]]
) -> None:
    serve(monkeypatch, {})
    asked = []
    live_reads(monkeypatch)
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: asked.append(keys) or {OTHER: {"components": ["api", "web"], "touched": ["api"], "dev": [], "releases": ["api"], "staged": ["api"]}})
    merged_ready(demo, OTHER, proof_file(tmp_path))
    watching = watcher(demo)
    merged = f"{OTHER} at ccc333ddd444"
    lines = watch.run_pass(watching, T0)
    data = state.load(demo)
    said = [line for line in lines if line.startswith("JOB ")]
    assert said == [f"JOB j2 o1 added, proves {OTHER}, waits on the next production release cut, which the watch binds it to once a cut carries its merges: drive status j2"]
    assert asked == [[OTHER]]
    assert not [line for line in lines if line.startswith(("FREE ", "ORPHAN "))]
    builder = data["jobs"]["j2"]
    assert (builder["kind"], builder["state"], builder["waits"], builder["parts"], builder["builds_nothing"], "j2" in data["orders"]["o1"]["jobs"]) == (
        "builder",
        "waiting",
        [state.CUT_WAIT],
        data["jobs"]["j1"]["parts"],
        True,
        True,
    )
    assert f"Handoff: {merged} merged: it goes live on the next production release cut, which this job waits on" in Path(builder["brief"]).read_text()
    assert (data["jobs"]["j1"]["said"], data["orders"]["o1"]["proof"], data["orders"]["o1"]["close_report"]) == (f"DONE {state.pr_url(OTHER)} merged: {merged}", None, None)
    assert not [record for records in ready_records for record in records if (record.get("attr") or {}).get("close") == "o1"]
    assert provers == []


@pytest.mark.parametrize("outcome", [None, f"{served.FAILED}: the judge timed out", f"{served.CHECKED} against the words of o1"], ids=["ended with no outcome", "failed", "passed"])
def test_a_merged_pr_starts_no_check_at_its_merged_head_whatever_its_last_check_found_and_its_job_takes_done_at_its_merge(
    demo: str, monkeypatch: pytest.MonkeyPatch, checkers: list[str], tmp_path: Path, outcome: str | None
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    merged_ready(demo, OTHER, proof_file(tmp_path))
    with edit(demo) as data:
        data["prs"][OTHER]["checker"] = {"head": "", "pid": 999999999, "at": state.stamp(T0 - timedelta(hours=1)), "outcome": outcome, "orders": None, "answers": None, "against": ""}
    watching = watcher(demo)
    for moment in (T0, T0 + served.RETRY + timedelta(minutes=1)):
        watch.run_pass(watching, moment)
    assert (checkers, state.load(demo)["jobs"]["j1"]["said"].startswith(f"DONE {state.pr_url(OTHER)} merged: ")) == ([], True)


def test_a_done_at_its_merge_delivers_no_part_so_the_part_is_owed_until_the_builder_proving_it_live_returns_done(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str]
) -> None:
    serve(monkeypatch, {})
    live_reads(monkeypatch)
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {OTHER: {"components": ["api"], "touched": ["api"], "dev": [], "releases": ["api"], "staged": ["api"]}})
    merged_ready(demo, OTHER, None)
    watch.run_pass(watcher(demo), T0)
    data = state.load(demo)
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j2"]["state"], state.delivered(data, "o1", "ship the watch")) == ("done", "waiting", False)
    with edit(demo) as data:
        state.take_result(data, "j2", f"DONE {state.pr_url(OTHER)} live on the api release")
    assert not state.delivered(state.load(demo), "o1", "ship the watch")
    with edit(demo) as data:
        state.deliver_proven(data, "j2", f"{state.pr_url(OTHER)} live: repro/j2.sh exits 0 on no plugin of this Mac")
    assert state.delivered(state.load(demo), "o1", "ship the watch")


def test_an_old_merge_never_proven_live_across_two_parts_gets_one_prover_waiting_on_the_next_release_cut_for_the_parts_it_carries_and_a_second_pass_adds_none(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], checkers: list[str]
) -> None:
    serve(monkeypatch, {})
    live_reads(monkeypatch)
    asked = []
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: asked.append(keys) or {KEY: {"components": ["api", "web"], "touched": ["api"], "dev": [], "releases": ["api"], "staged": ["api"]}})
    earlier, carried, merged = state.stamp(T0 - timedelta(days=2)), "ship the dashboard", f"{KEY} at ccc333ddd444"
    with edit(demo) as data:
        data["prs"][KEY] |= {"state": "merged", "merged_sha": "ccc333ddd444eee"}
        data["orders"]["o2"] = order(text=carried, jobs=["j1"], parts={carried: state.new_part()}, updated=earlier)
        data["jobs"]["j1"].update(state="done", said=f"DONE {state.pr_url(KEY)} merged: {merged}", carries={"o2": [carried]}, updated=earlier)
    watching = watcher(demo)
    lines = watch.run_pass(watching, T0)
    data = state.load(demo)
    builder = data["jobs"]["j2"]
    said = [line for line in lines if line.startswith("JOB ")]
    assert said == [f"JOB j2 o1 added, proves {KEY}, waits on the next production release cut, which the watch binds it to once a cut carries its merges: drive status j2"]
    assert not [line for line in lines if line.startswith(("FREE ", "ORPHAN "))]
    assert (builder["kind"], builder["state"], builder["waits"], builder["parts"], builder["carries"]) == ("builder", "waiting", [state.CUT_WAIT], ["ship the watch"], {"o2": [carried]})
    assert f"Handoff: {merged} merged: it goes live on the next production release cut, which this job waits on" in Path(builder["brief"]).read_text()
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=31)) if line.startswith("JOB ")]
    assert (list(state.load(demo)["jobs"]), asked, provers, checkers) == (["j1", "j2"], [[KEY]], [], [])


def test_a_merged_product_job_over_several_prs_gets_one_prover_said_in_one_line_waiting_on_the_next_release_cut(demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str]) -> None:
    serve(monkeypatch, {})
    live_reads(monkeypatch)
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {KEY: {"components": ["api"], "touched": ["api"], "dev": [], "releases": ["api"], "staged": ["api"]}, OTHER: {"components": ["web"], "touched": ["web"], "dev": [], "releases": ["web"], "staged": ["web"]}})
    merged_ready(demo, OTHER, None)
    with edit(demo) as data:
        data["prs"][KEY] |= {"merged_sha": "aaa111bbb222ccc"}
        data["jobs"]["j1"].update(said=f"READY {state.pr_url(KEY)} {state.pr_url(OTHER)}", prs=[state.pr_url(KEY), state.pr_url(OTHER)])
    lines = watch.run_pass(watcher(demo), T0)
    data = state.load(demo)
    assert [line for line in lines if line.startswith("JOB ")] == [
        f"JOB j2 o1 added, proves {KEY}, {OTHER}, waits on the next production release cut, which the watch binds it to once a cut carries its merges: drive status j2"
    ]
    assert (list(data["jobs"]), data["jobs"]["j2"]["waits"], data["jobs"]["j2"]["cut"], provers) == (["j1", "j2"], [state.CUT_WAIT], True, [])


def test_merges_of_two_orders_share_the_one_prover_of_the_next_release_cut_which_waits_on_the_drives_cut_carrying_them_once_it_starts(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str]
) -> None:
    serve(monkeypatch, {})
    live_reads(monkeypatch, carried={"fff000111222"})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {key: {"components": ["api"], "touched": ["api"], "dev": [], "releases": ["api"], "staged": ["api"]} for key in keys})
    merged_ready(demo, OTHER, None)
    carried = "ship the dashboard"
    with edit(demo) as data:
        data["prs"][KEY] = state.new_pr(state.pr_url(KEY), "j9", "o9") | {"state": "merged", "merged_sha": "aaa111bbb222ccc"}
        data["orders"]["o9"] = order(text=carried, jobs=["j9"], parts={carried: state.new_part()})
        data["jobs"]["j9"] = data["jobs"]["j1"] | {"order": "o9", "parts": [carried], "said": f"READY {state.pr_url(KEY)}", "prs": [state.pr_url(KEY)]}
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    data = state.load(demo)
    [prover] = [job_id for job_id, job in data["jobs"].items() if job["goal"].startswith("prove ")]
    assert (sorted(install.proves(data, prover)), data["jobs"][prover]["waits"]) == (sorted([KEY, OTHER]), [state.CUT_WAIT])
    assert {data["jobs"][prover]["order"], *data["jobs"][prover]["carries"]} == {"o1", "o9"}
    with edit(demo) as data:
        data["jobs"]["j20"] = state.new_job("o1", "builder", "cut the release", [], state.stamp(T0)) | {
            "state": "running",
            "agent": "a20",
            "brief_flags": {"release": "tools/ci release start --targets api --from fff000111222"},
            "release_build": "https://buildkite.com/forge/release/builds/1",
        }
        data["orders"]["o1"]["jobs"].append("j20")
    monkeypatch.setattr(watch, "read_builds", lambda clone, checkout, links: dict.fromkeys(links))
    lines = watch.run_pass(watching, T0 + timedelta(minutes=31))
    job = state.load(demo)["jobs"][prover]
    assert (job["waits"], job["cut"]) == (["j20"], "j20")
    assert any(line.startswith(f"JOB {prover} o") and "bound to the release cut j20 starts" in line for line in lines)


def test_a_merged_product_job_with_no_staging_test_reaching_a_target_staging_runs_gets_a_prover_starting_now_on_its_after_merge_pass_waiting_on_no_release(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    calls = live_reads(monkeypatch, [{"target": "api", "env": "plat-usw2-staging", "commit": "bbb222"}, {"target": "web", "env": "plat-usw2-prod", "commit": "bbb222"}])
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {OTHER: {"components": ["api", "web"], "touched": ["api", "web"], "dev": [], "releases": ["api", "web"], "staged": ["api", "web"]}})
    merged_ready(demo, OTHER, proof_file(tmp_path))
    watching = watcher(demo)
    lines = watch.run_pass(watching, T0)
    data = state.load(demo)
    builder = data["jobs"]["j2"]
    assert [line for line in lines if line.startswith("JOB ")] == [
        f'JOB j2 o1 added, proves {OTHER}, owes its after-merge staging pass first: the watch starts its agent on its brief {builder["brief"]}, so start no agent for it; drive job resume reaches it'
    ]
    assert (builder["waits"], [call for call in calls if call[:2] == ["release", "live"]], watching.staged) == ([], [["release", "live", "--json"]], {"api"})
    assert (
        "holding no staging slot and claiming nothing: wait on staging:api/plat-usw2-staging@ccc333ddd444eee until the promotion deploys a build holding its merge, run its cases on what staging serves, then install it"
        in Path(builder["brief"]).read_text()
    )


def test_a_merged_pr_changing_only_tests_gets_a_prover_waiting_on_no_release_that_proves_it_by_its_tests_on_dev(
    demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    (tmp_path / ".claude").mkdir()
    provide(monkeypatch, tmp_path, repo=HomeRepo)
    with edit(demo) as data:
        data["roles"]["coordinator"]["cwd"] = str(tmp_path)
    monkeypatch.setattr(github, "checkout_repo", lambda cwd: "Forge-AI/monorepo")
    monkeypatch.setattr(orca, "checkout", lambda name, cwd, wait=0: tmp_path)
    monkeypatch.setattr(github, "files", lambda key: ["go/sand/internal/runtime-api/memory/project_test.go", "go/sand/next/internal/runtime-api/memory/project_test.go"])
    merged_ready(demo, OTHER, None)
    lines = watch.run_pass(watcher(demo), T0)
    data = state.load(demo)
    prover = data["jobs"]["j2"]
    assert (data["jobs"]["j1"]["state"], prover["waits"], prover["builds_nothing"]) == ("done", [], True)
    assert prover["brief_flags"]["handoff"] == f"{OTHER} at ccc333ddd444 merged, changing only tests or CI config, which no release ships: prove it by its tests passing on dev at or after its merge commit"
    assert [line for line in lines if line.startswith("JOB ")] == [
        f'JOB j2 o1 added, proves {OTHER}, waits on no release: the watch starts its agent on its brief {prover["brief"]}, so start no agent for it; drive job resume reaches it'
    ]


def test_a_merged_product_job_whose_own_order_is_done_takes_done_at_its_merge_and_its_prover_goes_on_the_open_order_it_carries(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    merged_ready(demo, OTHER, None)
    carried = "ship the dashboard"
    with edit(demo) as data:
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
        f'JOB j2 o2 added, proves {OTHER}, waits on no release: the watch starts its agent on its brief {prover["brief"]}, so start no agent for it; drive job resume reaches it'
    ]


@pytest.mark.parametrize(
    "named",
    [
        {"said": f"DONE {state.pr_url(OTHER)} merged: {OTHER} at ccc333ddd444"},
        {"said": "DONE live on release jubilant-heaney", "brief_flags": {"handoff": f"{OTHER} at ccc333ddd444 merged: install it and prove it live"}},
        {"said": f"DONE 1,216 PRs checked at 27c34883bf69: 0 gaps; no readers added. #41015 held by its o2 part and j1/#{OTHER.rpartition('#')[2]}"},
    ],
)
def test_a_merge_done_a_merge_handoff_or_a_done_only_citing_a_merged_pr_proves_nothing_so_its_merged_ready_job_takes_done_at_its_merge_with_a_prover(
    demo: str, monkeypatch: pytest.MonkeyPatch, named: dict
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    merged_ready(demo, OTHER, None)
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j9")
        data["jobs"]["j9"] = state.new_job("o1", "builder", "prove it", [], state.stamp()) | {"state": "done"} | named
    lines = watch.run_pass(watcher(demo), T0)
    data = state.load(demo)
    assert (data["jobs"]["j1"]["state"], list(data["jobs"]), data["jobs"]["j10"]["parts"]) == ("done", ["j1", "j9", "j10"], data["jobs"]["j1"]["parts"])
    assert [line for line in lines if line.startswith("JOB ")] == [
        f'JOB j10 o1 added, proves {OTHER}, waits on no release: the watch starts its agent on its brief {data["jobs"]["j10"]["brief"]}, so start no agent for it; drive job resume reaches it'
    ]


@pytest.mark.parametrize("hand", [False, True])
@pytest.mark.parametrize(
    "named",
    [
        {
            "goal": f"staging test of {OTHER} before it lands: run its 22 cases",
            "said": f"DONE #{OTHER.rpartition('#')[2]} answered all 22 staging cases exactly as dev's code did",
            "brief_flags": {"handoff": f"{OTHER} is open on dev and owes its staging test before it lands, the part this job carries"},
        },
        {
            "goal": f"release dev head 833e4cd4388f to staging's SandSQL alone, the baseline build of the staging test of {OTHER}",
            "said": "DONE j9's repair live: repro/j9.sh exits 0 on long-running 1.0.1175",
            "brief_flags": RELEASED,
        },
        {
            "goal": f"release c5dca80ed9be, the head of {OTHER}, to staging's SandSQL alone, the second build of its staging test",
            "said": "DONE https://buildkite.com/forge/release/builds/16698 applied sandsql/plat-usw2-staging at `2026-11-15T16:40:09Z`",
            "brief_flags": RELEASED,
            "builds_nothing": True,
        },
    ],
    ids=["staging tester", "release follower", "release follower marked building nothing"],
)
def test_a_merge_whose_pr_its_done_staging_tester_or_release_follower_names_gets_its_own_prover_and_neither_is_widened_nor_proven_again_nor_folds_a_hand_added_prover(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path, named: dict, hand: bool
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    merged_ready(demo, OTHER, None)
    staging, earlier = f"staging test of #{OTHER.rpartition('#')[2]} before it lands", state.stamp(T0 - timedelta(hours=6))
    with edit(demo) as data:
        data["orders"]["o1"]["parts"][staging] = state.new_part()
        data["orders"]["o1"]["jobs"] += ["j9", "j10"] if hand else ["j9"]
        data["jobs"]["j9"] = state.new_job("o1", "builder", named["goal"], [staging], earlier) | {"state": "done", "repro": proof_file(tmp_path), "updated": earlier} | named
        if hand:
            data["jobs"]["j10"] = unstarted_prover([OTHER], [], [], f"{OTHER} merged: prove it live", created=state.stamp(), updated=state.stamp())
    watching = watcher(demo)
    lines = watch.run_pass(watching, T0)
    data = state.load(demo)
    prover = data["jobs"]["j10"]
    change = "widened to the parts of j1" if hand else "added"
    assert [line for line in lines if line.startswith("JOB ")] == [
        f'JOB j10 o1 {change}, proves {OTHER}, waits on no release: the watch starts its agent on its brief {prover["brief"]}, so start no agent for it; drive job resume reaches it'
    ]
    assert (list(data["jobs"]), prover["state"], prover["parts"], data["jobs"]["j9"]["state"], data["jobs"]["j9"]["parts"]) == (
        ["j1", "j9", "j10"],
        "waiting",
        data["jobs"]["j1"]["parts"],
        "done",
        [staging],
    )
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=31)) if line.startswith(("JOB ", 'ORPHAN o1 "ship the watch"'))]
    assert (watch.doubled(state.load(demo)), provers) == ([], [])


@pytest.mark.parametrize("ended", ["failed", "stopped"])
def test_an_old_merge_whose_prover_failed_or_stopped_gets_no_second_and_its_part_is_said_orphaned(demo: str, monkeypatch: pytest.MonkeyPatch, ended: str) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    earlier = state.stamp(T0 - timedelta(days=2))
    with edit(demo) as data:
        data["prs"][KEY] |= {"state": "merged", "merged_sha": "ccc333ddd444eee"}
        data["jobs"]["j1"].update(state="done", said=f"DONE {state.pr_url(KEY)} merged: {KEY} at ccc333ddd444", updated=earlier)
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = state.new_job("o1", "builder", f"prove {KEY} live", data["jobs"]["j1"]["parts"], earlier) | {"state": ended, "builds_nothing": True, "updated": earlier}
    watching = watcher(demo)
    lines = [line for minutes in (0, 3) for line in watch.run_pass(watching, T0 + timedelta(minutes=minutes))]
    assert (list(state.load(demo)["jobs"]), [line for line in lines if line.startswith("JOB ")]) == (["j1", "j2"], [])
    assert [line for line in lines if line.startswith('ORPHAN o1 "ship the watch": ')]


def unstarted_prover(keys: list[str], parts: list[str], waits: list[str], handoff: str, **overrides: object) -> dict:
    earlier = state.stamp(T0 - timedelta(days=2))
    return state.new_job("o1", "builder", f"prove {', '.join(keys)} live", parts, earlier) | {
        "waits": waits,
        "builds_nothing": True,
        "brief_flags": {"handoff": handoff},
        "session": None,
        "updated": earlier,
    } | overrides


def test_an_old_merge_whose_unstarted_prover_holds_one_of_its_parts_widens_that_prover_to_the_others_and_a_second_pass_adds_no_job(
    home_repo: None,
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    live_reads(monkeypatch, [{"target": "api", "env": "plat-usw2-prod", "commit": "0ld0000"}])
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {KEY: {"components": ["api"], "touched": ["api"], "dev": [], "releases": ["api"], "staged": ["api"]}})
    earlier, second = state.stamp(T0 - timedelta(days=2)), "ship the dashboard"
    handoff = f"{KEY} at ccc333ddd444 merged: install it by the INSTALL: line of j1's return, in reports/j1.md, write its PROVE: line as a proof file, keep it with drive job set <your job> --repro <file> and run drive job prove <your job>, which runs that file, and only its exit 0 delivers the part"
    with edit(demo) as data:
        data["prs"][KEY] |= {"state": "merged", "merged_sha": "ccc333ddd444eee"}
        data["orders"]["o1"]["parts"][second] = state.new_part()
        data["jobs"]["j1"].update(state="done", said=f"DONE {state.pr_url(KEY)} merged: {KEY} at ccc333ddd444", parts=["ship the watch", second], updated=earlier)
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = unstarted_prover([KEY], ["ship the watch"], [f"release:api:{KEY}"], handoff)
    watching = watcher(demo)
    lines = watch.run_pass(watching, T0)
    assert [line for line in lines if line.startswith("JOB ")] == [f"JOB j2 o1 widened to the parts of j1, proves {KEY}, waits on 1 release: drive status j2"]
    data = state.load(demo)
    prover = data["jobs"]["j2"]
    assert (list(data["jobs"]), prover["parts"], prover["waits"], prover["brief_flags"]["handoff"]) == (["j1", "j2"], ["ship the watch", second], [f"release:api:{KEY}"], handoff)
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=31)) if line.startswith(("JOB ", "ORPHAN "))]
    assert list(state.load(demo)["jobs"]) == ["j1", "j2"]


def test_a_merge_sharing_a_pr_with_an_unstarted_prover_widens_it_to_its_parts_and_other_prs_adding_no_release_wait(
    home_repo: None,
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, {})
    live_reads(monkeypatch, [{"target": "api", "env": "plat-usw2-prod", "commit": "0ld0000"}])
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {KEY: {"components": ["api", "sandsql"], "touched": ["api", "sandsql"], "dev": [], "releases": ["api", "sandsql"], "staged": ["api", "sandsql"]}, OTHER: {"components": ["web"], "touched": ["web"], "dev": [], "releases": ["web"], "staged": ["web"]}})
    earlier, carried = state.stamp(T0 - timedelta(days=2)), "ship the dashboard"
    first = f"{KEY} at ccc333ddd444 merged: install it by the INSTALL: line of j1's return, in reports/j1.md, write its PROVE: line as a proof file, keep it with drive job set <your job> --repro <file> and run drive job prove <your job>, which runs that file, and only its exit 0 delivers the part"
    with edit(demo) as data:
        data["prs"][KEY] |= {"state": "merged", "merged_sha": "ccc333ddd444eee"}
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j3", "o2") | {"state": "merged", "merged_sha": "aaa111bbb222ccc"}
        data["orders"]["o2"] = order(text=carried, jobs=["j3"], parts={carried: state.new_part()}, updated=earlier)
        data["jobs"]["j1"].update(state="done", said=f"DONE {state.pr_url(KEY)} merged: {KEY} at ccc333ddd444", updated=earlier)
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = unstarted_prover([KEY], data["jobs"]["j1"]["parts"], [f"release:api:{KEY}"], first)
        data["jobs"]["j3"] = state.new_job("o2", "builder", carried, [carried], earlier) | {
            "state": "done",
            "said": f"DONE {state.pr_url(KEY)} {state.pr_url(OTHER)} merged: {KEY} at ccc333ddd444, {OTHER} at aaa111bbb222",
            "session": None,
            "updated": earlier,
        }
    watching = watcher(demo)
    lines = watch.run_pass(watching, T0)
    assert [line for line in lines if line.startswith("JOB ")] == [f"JOB j2 o1 widened to the parts of j3, proves {KEY}, {OTHER}, waits on 1 release: drive status j2"]
    data = state.load(demo)
    prover = data["jobs"]["j2"]
    assert (list(data["jobs"]), prover["goal"], prover["carries"], prover["waits"]) == (["j1", "j2", "j3"], f"prove {KEY}, {OTHER} live", {"o2": [carried]}, [f"release:api:{KEY}"])
    second = f"{KEY} at ccc333ddd444, {OTHER} at aaa111bbb222 merged: install it by the INSTALL: line of j3's return, in reports/j3.md, write its PROVE: line as a proof file, keep it with drive job set <your job> --repro <file> and run drive job prove <your job>, which runs that file, and only its exit 0 delivers the part"
    assert (prover["brief_flags"]["handoff"], "j2" in data["orders"]["o2"]["jobs"]) == (f"{first}; {second}", True)
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=31)) if line.startswith(("JOB ", "ORPHAN "))]
    assert list(state.load(demo)["jobs"]) == ["j1", "j2", "j3"]


@pytest.mark.parametrize("marked", [True, False])
def test_a_second_unstarted_prover_of_a_pr_marked_or_not_folds_into_the_first_which_takes_its_parts_prs_and_handoff_and_a_second_pass_changes_nothing(
    home_repo: None,
    demo: str, monkeypatch: pytest.MonkeyPatch, marked: bool
) -> None:
    serve(monkeypatch, {})
    live_reads(monkeypatch, [{"target": service, "env": "plat-usw2-prod", "commit": "0ld0000"} for service in ("api", "web")])
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    earlier, carried = state.stamp(T0 - timedelta(days=2)), "ship the dashboard"
    first, second = (f"{merged} merged: install it by the INSTALL: line of j1's return" for merged in (KEY, f"{KEY}, {OTHER}"))
    with edit(demo) as data:
        data["prs"][KEY] |= {"state": "merged", "merged_sha": "ccc333ddd444eee"}
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"state": "merged", "merged_sha": "aaa111bbb222ccc"}
        data["orders"]["o2"] = order(text=carried, jobs=["j1", "j3"], parts={carried: state.new_part()}, updated=earlier)
        data["jobs"]["j1"].update(
            state="done", said=f"DONE {state.pr_url(KEY)} {state.pr_url(OTHER)} merged: {KEY} at ccc333ddd444, {OTHER} at aaa111bbb222", carries={"o2": [carried]}, updated=earlier
        )
        data["orders"]["o1"]["jobs"] += ["j2", "j3"]
        data["jobs"]["j2"] = unstarted_prover([KEY], ["ship the watch"], [f"release:api:{KEY}"], first, builds_nothing=marked)
        data["jobs"]["j3"] = unstarted_prover([KEY, OTHER], ["ship the watch"], [f"release:api:{KEY}", f"release:web:{OTHER}"], second, carries={"o2": [carried]}, builds_nothing=marked)
    watching = watcher(demo)
    lines = watch.run_pass(watching, T0)
    assert [line for line in lines if line.startswith("JOB ")] == [f"JOB j2 o1 took the parts of j3, a duplicate now stopped, proves {KEY}, {OTHER}, waits on 1 release: drive status j2"]
    data = state.load(demo)
    prover, duplicate = data["jobs"]["j2"], data["jobs"]["j3"]
    assert (prover["state"], prover["goal"], prover["parts"], prover["carries"], prover["waits"], prover["brief_flags"]["handoff"]) == (
        "waiting",
        f"prove {KEY}, {OTHER} live",
        ["ship the watch"],
        {"o2": [carried]},
        [f"release:api:{KEY}"],
        f"{first}; {second}",
    )
    assert (duplicate["state"], duplicate["said"], list(data["jobs"])) == ("stopped", "folded into j2", ["j1", "j2", "j3"])
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=31)) if line.startswith(("JOB ", "ORPHAN "))]
    assert state.load(demo)["jobs"]["j2"]["waits"] == [f"release:api:{KEY}"]


def test_a_prover_is_freed_only_once_a_production_target_that_ships_its_prs_paths_serves_its_merge_never_another_target_carrying_the_commit_as_j7876_was(
    demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = "Forge-AI/monorepo#57077"
    with edit(demo) as data:
        data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"state": "merged", "merged_sha": "aaa111"}
        data["jobs"]["j9"] = state.new_job("o1", "builder", f"prove {key} live", [], state.stamp()) | {"waits": [state.CUT_WAIT]}
    monkeypatch.setattr(orca, "checkout", lambda name, cwd, wait=0: tmp_path)
    monkeypatch.setattr(github, "carrier", lambda checkout: lambda commit, sha, repo: commit == "408ec512")
    monkeypatch.setattr(watch, "drive_cuts", lambda data, checkout: {})
    monkeypatch.setattr(watch.settled, "prod", lambda env, checkout: True)
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {key: {"components": ["api"], "touched": ["api"], "dev": [], "releases": ["api"], "staged": ["api"]}})
    watching = watcher(demo)
    watching.live = [{"target": "platy", "env": "core-usw2-auto", "commit": "408ec512"}, {"target": "api", "env": "plat-usw2-prod", "commit": "c7063939"}]
    assert watch.cut_binds(watching, state.load(demo), ["j9"]) == {}
    watching.live[1]["commit"] = "408ec512"
    assert watch.cut_binds(watching, state.load(demo), ["j9"]) == {"j9": None}


@pytest.mark.parametrize("out", [False, True])
def test_a_merged_product_job_waiting_on_its_own_release_takes_done_at_its_merge_with_no_free_and_its_prover_waits_on_the_next_release_cut(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], out: bool
) -> None:
    serve(monkeypatch, {})
    live_reads(monkeypatch, [{"target": "api", "env": "plat-usw2-prod", "commit": "ccc333ddd444eee"}] if out else None, {"ccc333ddd444eee"})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {OTHER: {"components": ["api", "web"], "touched": ["web"], "dev": [], "releases": ["web"], "staged": ["web"]}})
    merged_ready(demo, OTHER, None)
    wait = f"release:api:{OTHER}"
    with edit(demo) as data:
        data["jobs"]["j1"].update(said=f"WAITING {wait}", waits=[wait])
    lines = watch.run_pass(watcher(demo), T0)
    data = state.load(demo)
    assert not [line for line in lines if line.startswith(("FREE ", "ORPHAN "))]
    assert [line for line in lines if line.startswith("JOB ")] == [f"JOB j2 o1 added, proves {OTHER}, waits on the next production release cut, which the watch binds it to once a cut carries its merges: drive status j2"]
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["waits"], data["jobs"]["j2"]["waits"], provers) == ("done", [], [state.CUT_WAIT], [])


def test_a_merge_whose_pr_an_unmarked_prover_at_work_on_another_order_proves_gives_that_prover_its_parts_and_adds_no_second(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    merged_ready(demo, OTHER, None)
    carried = "prove the dashboard"
    with edit(demo) as data:
        data["orders"]["o2"] = order(text=carried, jobs=["j9"], parts={carried: state.new_part()})
        data["jobs"]["j9"] = state.new_job("o2", "builder", f"prove {OTHER} live", [carried], state.stamp()) | {"state": "running", "agent": "a-9"}
    watching = watcher(demo)
    lines = watch.run_pass(watching, T0)
    assert [line for line in lines if line.startswith("JOB ")] == [f"JOB j9 o2 widened to the parts of j1, proves {OTHER}, already running: drive status j9"]
    data = state.load(demo)
    assert (data["jobs"]["j1"]["state"], list(data["jobs"]), data["jobs"]["j9"]["carries"], "j9" in data["orders"]["o1"]["jobs"]) == ("done", ["j1", "j9"], {"o1": ["ship the watch"]}, True)
    assert not [line for line in watch.run_pass(watching, T0 + timedelta(minutes=31)) if line.startswith("JOB ")]


def test_a_merged_ready_product_job_whose_paths_reach_no_release_has_its_builder_briefed_to_start_in_the_turn_its_line_wakes(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    merged_ready(demo, OTHER, proof_file(tmp_path))
    said = [line for line in watch.run_pass(watcher(demo), T0) if line.startswith("JOB ")]
    data = state.load(demo)
    assert (data["jobs"]["j2"]["state"], data["jobs"]["j2"]["waits"]) == ("waiting", [])
    assert said == [f'JOB j2 o1 added, proves {OTHER}, waits on no release: the watch starts its agent on its brief {data["jobs"]["j2"]["brief"]}, so start no agent for it; drive job resume reaches it']
    assert provers == []


@pytest.mark.parametrize("proved", [False, True])
def test_a_merged_ready_product_job_on_a_done_order_or_whose_pr_a_job_proved_live_takes_done_at_its_merge_and_gets_no_prover(
    demo: str, monkeypatch: pytest.MonkeyPatch, proved: bool
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    merged_ready(demo, OTHER, None)
    with edit(demo) as data:
        if proved:
            data["orders"]["o1"]["jobs"].append("j9")
            data["jobs"]["j9"] = state.new_job("o1", "builder", f"prove {OTHER} live", data["jobs"]["j1"]["parts"], state.stamp()) | {"state": "done", "said": f"DONE {OTHER} live on release jubilant-heaney"}
        else:
            data["orders"]["o1"]["state"] = "done"
    lines = watch.run_pass(watcher(demo), T0)
    data = state.load(demo)
    assert not [line for line in lines if line.startswith(("JOB ", "FREE "))]
    assert (data["jobs"]["j1"]["state"], list(data["jobs"])) == ("done", ["j1", "j9"] if proved else ["j1"])


@pytest.mark.parametrize("tooling", [False, True])
def test_a_pushed_whose_prs_all_merged_settles_nothing_so_no_ready_goes_out_after_the_merge(demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tooling: bool) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, YASYF if tooling else OTHER, proof_file(tmp_path))
    with edit(demo) as data:
        data["jobs"]["j1"]["said"] = f"PUSHED {state.pr_url(YASYF if tooling else OTHER)}"
    data = state.load(demo)
    assert (watch.settles(watcher(demo), data, "j1"), watch.pushed_lines(watcher(demo), T0)) == (None, [])


@pytest.mark.parametrize(("fix", "sha"), [("Forge-AI/monorepo#55420", "485f9f408150"), ("Forge-AI/monorepo#55448", "6ec574a4f779")])
def test_a_fix_merged_to_dev_that_no_release_holds_is_never_proven_live_so_its_reporter_is_owed_no_fix_live_note(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], ready_records: list[list[dict]], fix: str, sha: str
) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, fix, None)
    with edit(demo) as data:
        data["prs"][fix]["merged_sha"] = sha
        data["orders"]["o1"]["reporters"] = [state.reporter(REPORTED)]
    watching = watcher(demo)
    lines = [line for minutes in (0, 11, 40) for line in watch.run_pass(watching, T0 + timedelta(minutes=minutes))]
    assert [line for line in lines if line.startswith(f"JOB j2 o1 added, proves {fix}, waits on ")]
    data = state.load(demo)
    assert (data["jobs"]["j1"]["state"], data["orders"]["o1"]["proof"], [item for item in data["orders"]["o1"]["owed"] if item["kind"] == owing.FIX_LIVE]) == ("done", None, [])
    assert not [record for records in ready_records for record in records if (record.get("attr") or {}).get("close") == "o1"]
    assert render.reporter_said(data, "o1", data["orders"]["o1"]) == f", reported in {REPORTED}, which hears it is fixed once its fix is proven live"
    assert provers == []


@pytest.mark.parametrize("sha", ["485f9f408150", "6ec574a4f779"])
def test_an_open_order_a_merge_commit_once_proved_loses_that_proof_and_its_close_while_a_closed_one_or_a_yasyf_commit_keeps_them(demo: str, sha: str) -> None:
    commit, tooling = f"https://github.com/Forge-AI/monorepo/commit/{sha}", "https://github.com/yasyf/cc-skills/commit/ccc333ddd444"
    with edit(demo) as data:
        data["orders"]["o1"] |= {"proof": commit, "closed_on": "689162", "reporters": [state.reporter(REPORTED)]}
        data["orders"]["o2"] = order(state="done", proof=commit, closed_on="689100", jobs=[])
        data["orders"]["o3"] = order(proof=tooling, jobs=[])
    data = state.load(demo)
    reported = data["orders"]["o1"]
    assert (reported["proof"], reported["closed_on"], reported["close_report"]) == (None, None, None)
    assert (data["orders"]["o2"]["proof"], data["orders"]["o2"]["closed_on"], data["orders"]["o3"]["proof"]) == (commit, "689100", tooling)


def test_a_merged_ready_product_job_on_an_open_incidents_fix_takes_done_at_its_merge_like_any_other_and_its_prover_takes_the_fix(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str]
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
    merged_ready(demo, OTHER, None)
    with edit(demo) as data:
        data["orders"]["o1"].update(incident=True, parts={"fix": state.new_part()})
        data["jobs"]["j1"]["parts"] = ["fix"]
    lines = watch.run_pass(watcher(demo), T0)
    data = state.load(demo)
    assert [line for line in lines if line.startswith(f"JOB j2 o1 added, proves {OTHER}")]
    assert not [line for line in lines if line.startswith('ORPHAN o1 "fix"')]
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j2"]["parts"], data["jobs"]["j2"]["waits"], provers) == ("done", ["fix"], [], [])


@pytest.mark.parametrize(("version", "result", "started"), [(state.VERSION, None, []), ("1.0.1", None, ["j1"]), ("1.0.1", "BLOCKED j1: repro/j1.sh exits 1", []), (state.VERSION, "BLOCKED j1: repro/j1.sh exits 1", [])])
def test_a_proof_that_ended_with_no_result_is_started_again_by_a_newer_watch_and_one_that_took_a_result_never_is_once_its_job_is_put_back_on_its_line(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path, version: str, result: str | None, started: list[str]
) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, YASYF, proof_file(tmp_path))
    ended = subprocess.Popen(["true"])
    ended.wait()
    with edit(demo) as data:
        data["jobs"]["j1"]["proof"] = {"pid": ended.pid, "at": state.stamp(T0), "said": f"READY {state.pr_url(YASYF)}", "version": version} | ({"result": result} if result else {})
    lines = watch.run_pass(watcher(demo), T0)
    assert provers == started
    rerun = f"j1's proof, drive job prove pid {ended.pid}, ended with no result, its output in the drive's {watch.PROOFS_LOG}: drive job prove j1 runs it again"
    put_back = f"j1 was put back on the line its proof, drive job prove pid {ended.pid}, took a result on, so no install runs it again: drive job prove j1 runs it again"
    assert lines == ([] if started else [f'ORPHAN o1 "ship the watch": {put_back if result else rerun}'])


@pytest.mark.parametrize(("returned", "started"), [(T0 - timedelta(hours=1), []), (T0 + timedelta(hours=1), ["j1"])])
def test_a_jobs_own_done_taken_before_the_drives_own_done_proofs_since_gets_no_first_proof_and_no_orphan_while_one_taken_after_gets_its_proof(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path, returned: datetime, started: list[str]
) -> None:
    serve(monkeypatch, {})
    with edit(demo) as data:
        del data["prs"][KEY]
        data["own_done_proofs_since"] = state.stamp(T0)
        data["jobs"]["j1"].update(state="done", said="DONE checked it by hand", prs=[], repro=proof_file(tmp_path), returned=state.stamp(returned), updated=state.stamp(returned))
    data = state.load(demo)
    assert (install.done_unproven(data, "j1"), install.done_before_proofs(data, "j1")) == (bool(started), not started)
    assert (watch.run_pass(watcher(demo), T0 + timedelta(hours=2)), provers) == ([], started)


def test_a_done_builder_owing_work_whose_said_line_an_older_install_kept_without_its_done_reads_its_newest_return_so_one_opening_done_is_done_before_proofs(demo: str) -> None:
    said, returned = "#53689 live: tools/ci db-read --json waited 1:03 for its build, printed rows, exit 0", T0 - timedelta(hours=1)
    with edit(demo) as data:
        data["own_done_proofs_since"] = state.stamp(T0)
        data["jobs"]["j1"].update(state="done", said=said, returned=state.stamp(returned), updated=state.stamp(returned))
    state.keep_report(demo, "j1", f"BLOCKED {said}", f"BLOCKED {said}", returned - timedelta(minutes=5))
    assert not install.done_before_proofs(state.load(demo), "j1")
    state.keep_report(demo, "j1", f"DONE {said}", f"DONE {said}\nPROVE: tools/ci db-read --json", returned)
    assert install.done_before_proofs(state.load(demo), "j1")


def test_a_proof_waiting_on_his_next_word_holds_its_job_with_no_orphan_and_runs_again_only_once_he_is_heard_after_it_started(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    merged_ready(demo, YASYF, proof_file(tmp_path))
    ended = subprocess.Popen(["true"])
    ended.wait()
    wait = "WAIT S1 no exchange since the install: reply in a DM thread within 60s of a handler's post there, then run this again"
    with edit(demo) as data:
        data["jobs"]["j1"]["proof"] = {"pid": ended.pid, "at": state.stamp(T0), "said": f"READY {state.pr_url(YASYF)}", "version": "1.0.1", "waits": wait}
        data["owner"] = {"heard": state.stamp(T0 - timedelta(minutes=5)), "away": state.stamp(T0 + timedelta(minutes=55))}
    watching = watcher(demo)
    assert [watch.run_pass(watching, T0 + timedelta(minutes=minutes)) for minutes in (0, 31, 62)] == [[], [], []]
    assert provers == []
    watch.hear(demo, T0 + timedelta(minutes=63))
    assert watch.run_pass(watching, T0 + timedelta(minutes=64)) == []
    assert provers == ["j1"]
    assert "waits" not in state.load(demo)["jobs"]["j1"]["proof"]


def test_a_repair_waiting_on_the_proof_it_marked_holds_with_no_free_or_orphan_and_runs_again_only_once_he_is_heard_after_it_started(
    demo: str, monkeypatch: pytest.MonkeyPatch, provers: list[str], tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    ended = subprocess.Popen(["true"])
    ended.wait()
    wait = "WAIT he writes a new message in his DM thread"
    said = f"WAITING {install.PROOF_WAITS}: {wait}"
    with edit(demo) as data:
        del data["prs"][KEY]
        data["jobs"]["j1"].update(state="waiting", said=said, prs=[], repro=proof_file(tmp_path), updated=state.stamp(T0 - timedelta(hours=1)))
        data["jobs"]["j1"]["proof"] = {"pid": ended.pid, "at": state.stamp(T0), "said": said, "version": state.VERSION, "waits": wait}
        data["owner"] = {"heard": state.stamp(T0 - timedelta(minutes=5)), "away": state.stamp(T0 + timedelta(minutes=55))}
    watching = watcher(demo)
    assert [watch.run_pass(watching, T0 + timedelta(minutes=minutes)) for minutes in (0, 31, 62)] == [[], [], []]
    assert provers == []
    watch.hear(demo, T0 + timedelta(minutes=63))
    assert watch.run_pass(watching, T0 + timedelta(minutes=64)) == []
    assert provers == ["j1"]


def tried(name: str, outcome: str) -> None:
    with edit(name) as data:
        data["prs"][KEY]["state"] = "merged"
        lander = {"head": "aaaaaaa111", "pid": os.getpid(), "at": state.stamp(T0), "outcome": outcome}
        data["prs"][YASYF] = state.new_pr(state.pr_url(YASYF), "j1", "o1") | {"head": "aaaaaaa111", "base": "main", "wants_tap": False, "lander": lander}


def test_a_yasyf_merge_whose_base_moved_merges_again_on_the_new_base_and_never_updates_its_branch(demo: str, monkeypatch: pytest.MonkeyPatch, pr_reads: dict[str, dict]) -> None:
    merges = iter([(1, "", BASE_MOVED), (1, "", BASE_MOVED), (0, "", "")])
    runs: list[list[str]] = []
    pr_reads[YASYF] = read(state="MERGED", mergeCommit="ccc333ddd444eee")
    monkeypatch.setattr(github, "files", lambda key: ["README.md"])
    monkeypatch.setattr(install, "touched", lambda repo, files: [])
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: runs.append(argv) or land.subprocess.CompletedProcess(argv, *next(merges)))
    tried(demo, "")
    assert land.merged(demo, YASYF, "squash", ["--admin"]) == "merged at ccc333ddd444; it changes no plugin installed on this Mac"
    assert runs == [["gh", "pr", "merge", "7", "-R", "yasyf/cc-skills", "--squash", "--admin"]] * 3


@pytest.mark.parametrize(("refusal", "mergeable"), [(DIRTY, "CONFLICTING"), (MERGE_CONFLICTS, "CONFLICTING"), (MERGE_CONFLICTS, "UNKNOWN")])
def test_a_yasyf_merge_gh_refuses_for_a_conflict_in_code_reads_red_and_wakes_its_builder_with_no_stage_line_while_github_reads_it_conflicting_or_works_it_out(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], landers: list[tuple[str, ...]], refusal: str, mergeable: str
) -> None:
    outcome = f"not landed: gh pr merge --squash --admin refused it: {refusal}; tool.py conflict past a clause-by-clause merge, so its builder rebases it"
    waiting_job(demo)
    tried(demo, "")
    land.record(demo, [YASYF], outcome, [], {})
    serve(monkeypatch, {YASYF: read(mergeable=mergeable)})
    lines = watch.run_pass(watcher(demo), T0)
    assert [line for line in lines if line.startswith("STAGE")] == []
    assert woken == [(YASYF, f"{YASYF} red since {state.stamp(T0)}, owed by its builder: it conflicts with its base main [o1 j1]")]
    assert landers == []


def test_a_yasyf_merge_gh_refused_for_a_conflict_lands_again_once_github_reads_that_head_mergeable_never_telling_its_builder_to_restack(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], landers: list[tuple[str, ...]]
) -> None:
    waiting_job(demo)
    tried(demo, "")
    pushed(demo, YASYF)
    land.record(demo, [YASYF], f"not landed: gh pr merge --squash --admin refused it: {DIRTY}; the-watch already sits on main at ccc333ddd444, so a rebase changes nothing", [], {})
    assert stage.red(state.load(demo), YASYF) == "it conflicts with its base main"
    serve(monkeypatch, {YASYF: read(mergeable="MERGEABLE", checks=GREEN)})
    watch.run_pass(watcher(demo), T0)
    assert (woken, landers, stage.red(state.load(demo), YASYF)) == ([], [(YASYF,)], "")


@pytest.mark.parametrize("said", [POLICY, f"ccx: GitHub secondary rate limit: holding every request on the app token until 12:05 PM\n{POLICY}"])
def test_a_yasyf_merge_gh_refuses_for_any_other_reason_is_not_landed_and_reads_unlanded_for_the_session_holding_its_order(demo: str, monkeypatch: pytest.MonkeyPatch, said: str) -> None:
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: land.subprocess.CompletedProcess(argv, 1, "", said + "\n"))
    outcome = land.merged(demo, YASYF, "squash", ["--admin"])
    assert outcome == f"not landed: gh pr merge --squash --admin refused it: {' '.join(said.split())}"
    waiting_job(demo)
    tried(demo, outcome)
    assert not watch.wakes(f"PR {YASYF}: {outcome} [o1 j1]", state.load(demo), NO_CARDS)
    assert stage.of(state.load(demo), YASYF, NO_CARDS).name == "unlanded"


@pytest.mark.parametrize(
    "said",
    [
        'Post "https://api.github.com/graphql": net/http: TLS handshake timeout',
        "HTTP 502: 502 Bad Gateway (https://api.github.com/graphql)",
        "ccx: vcs gh: merge with gh pr merge; the merge hooks gate that form: gh pr merge 7 -R yasyf/cc-skills --squash --admin",
    ],
)
def test_a_yasyf_merge_github_never_answered_or_ccx_refused_is_the_landers_own_failure_said_once_to_the_meta_session_and_merged_again_at_the_next_pass(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], said: str
) -> None:
    with monkeypatch.context() as merging:
        merging.setattr(land.subprocess, "run", lambda argv, **kwargs: land.subprocess.CompletedProcess(argv, 1, "", said + "\n"))
        outcome = land.merged(demo, YASYF, "squash", ["--admin", "--match-head-commit", "aaaaaaa111"])
    assert outcome == f"{land.OWN_FAILURE} get GitHub's answer on {YASYF} to gh pr merge --squash --admin --match-head-commit aaaaaaa111: {said}; the next watch pass tries it again"
    waiting_job(demo)
    tried(demo, "")
    with edit(demo) as data:
        data["jobs"]["j1"] |= {"said": f"PUSHED {state.pr_url(YASYF)}", "returned": state.stamp(T0)}
        data["prs"][YASYF]["checks"] = "pass"
    watching = watcher(demo)
    land.record(demo, [YASYF], outcome, [], {})
    now = stage.of(state.load(demo), YASYF, NO_CARDS)
    assert (now.name, now.owner) == ("ready", stage.LANDER)
    assert land.start(demo, [YASYF], NO_CARDS) == [(YASYF,)]
    land.record(demo, [YASYF], outcome, [], {})
    lines = watch.event_lines(watching)
    assert lines == [f"{watch.LANDER}{outcome}"]
    sent = posts(monkeypatch)
    assert watch.handed(watching, lines, T0) == []
    assert posted(sent) == [("defect", f"meta@{demo}", f"{watch.LANDER}{outcome}")]


def test_replayed_433_a_yasyf_try_recorded_before_the_lander_said_github_never_answered_its_merge_reads_ready_and_the_next_pass_lands_it_again(demo: str, landers: list[tuple[str, ...]]) -> None:
    waiting_job(demo)
    tried(demo, 'not landed: gh pr merge --squash --admin --match-head-commit aaaaaaa111 refused it: Post "https://api.github.com/graphql": net/http: TLS handshake timeout')
    with edit(demo) as data:
        data["jobs"]["j1"] |= {"said": f"PUSHED {state.pr_url(YASYF)}", "returned": state.stamp(T0)}
        data["prs"][YASYF]["checks"] = "pass"
    assert stage.of(state.load(demo), YASYF, NO_CARDS).name == "ready"
    assert land.start(demo, [YASYF], NO_CARDS) == [(YASYF,)]


def conflicting(demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ours: dict[str, str], theirs: dict[str, str]) -> tuple[Path, str]:
    """yasyf/cc-skills on an origin whose main and the PR's change branch each commit files over one base, GitHub refusing the PR's merge on that conflict, and the PR's row at the change's head conflicting with main; the origin and main's head."""
    work, origin = tmp_path / "work", tmp_path / "yasyf" / "cc-skills.git"
    git_in(tmp_path, "init", "-q", "-b", "main", str(work))
    commit_files(work, "base", {"verbs.md": ROWS, "tool.py": "x = 1\n"})
    git_in(work, "switch", "-q", "-c", "change")
    head = commit_files(work, "Fix: drive a runs fast", theirs)
    git_in(work, "switch", "-q", "main")
    main = commit_files(work, "Fix: drive c runs twice", ours)
    git_in(tmp_path, "clone", "-q", "--bare", str(work), str(origin))
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "t")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "t@example.com")
    monkeypatch.setattr(land, "CLONE_URL", f"file://{tmp_path}/{{}}.git")
    monkeypatch.setattr(land, "merged_sync", lambda key, method, flags: f"gh pr merge --squash --admin refused it: {DIRTY}")
    tried(demo, "")
    with edit(demo) as data:
        pr = data["prs"][YASYF]
        pr |= {"head": head, "base": "main", "branch": "change", "title": "Fix: drive a runs fast", "conflict": True, "lander": pr["lander"] | {"head": head, "base": "main"}}
    return origin, main


def test_a_yasyf_merge_github_refuses_on_a_conflict_in_prose_alone_is_rebased_onto_main_as_one_commit_that_the_lander_merges_once_the_watch_reads_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, landers: list[tuple[str, ...]]
) -> None:
    origin, main = conflicting(demo, monkeypatch, tmp_path, {"verbs.md": ROWS.replace("Then c.", "Then c twice.")}, {"verbs.md": ROWS.replace("runs a.", "runs a fast.")})
    outcome = land.merged(demo, YASYF, "squash", ["--admin"])
    rebased = git_in(origin, "rev-parse", "change")
    assert outcome == (
        f"{land.REBASED} main at {main[:12]} as {rebased[:12]} after GitHub refused its merge on a conflict,"
        " verbs.md merged clause by clause with git merge-file; the lander merges that head once the watch reads it"
    )
    assert git_in(origin, "rev-parse", "change^") == main
    assert git_in(origin, "show", "change:verbs.md") == ROWS.replace("runs a.", "runs a fast.").replace("Then c.", "Then c twice.").rstrip("\n")
    assert git_in(origin, "log", "-1", "--format=%B", "change") == "Fix: drive a runs fast"
    with edit(demo) as data:
        data["prs"][YASYF]["lander"]["outcome"] = outcome
    assert stage.of(state.load(demo), YASYF, NO_CARDS).name == "landing"
    waiting_job(demo)
    with edit(demo) as data:
        data["jobs"]["j1"] |= {"said": f"PUSHED {state.pr_url(YASYF)}", "returned": state.stamp(T0)}
        data["prs"][YASYF] |= {"head": rebased, "conflict": False}
    assert stage.of(state.load(demo), YASYF, NO_CARDS).name == "grading"
    with edit(demo) as data:
        data["prs"][YASYF]["checks"] = "pass"
    assert stage.of(state.load(demo), YASYF, NO_CARDS).name == "ready"
    assert land.start(demo, [YASYF], NO_CARDS) == [(YASYF,)]


def test_a_yasyf_merge_by_rebase_github_refuses_on_a_conflict_in_prose_is_not_landed_with_its_commits_left_as_its_builder_made_them(
    demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    origin, _ = conflicting(demo, monkeypatch, tmp_path, {"verbs.md": ROWS.replace("Then c.", "Then c twice.")}, {"verbs.md": ROWS.replace("runs a.", "runs a fast.")})
    head = git_in(origin, "rev-parse", "change")
    assert land.merged(demo, YASYF, "rebase", ["--admin"]) == f"not landed: gh pr merge --squash --admin refused it: {DIRTY}"
    assert git_in(origin, "rev-parse", "change") == head


def test_a_yasyf_merge_github_refuses_on_a_conflict_in_code_is_not_landed_and_reads_red_for_its_builder_with_its_branch_untouched(
    demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    origin, _ = conflicting(demo, monkeypatch, tmp_path, {"tool.py": "x = 2\n"}, {"tool.py": "x = 3\n"})
    head = git_in(origin, "rev-parse", "change")
    outcome = land.merged(demo, YASYF, "squash", ["--admin"])
    assert outcome == f"not landed: gh pr merge --squash --admin refused it: {DIRTY}; tool.py conflict past a clause-by-clause merge, so its builder rebases it"
    assert git_in(origin, "rev-parse", "change") == head
    land.record(demo, [YASYF], outcome, [], {})
    now = stage.of(state.load(demo), YASYF, NO_CARDS)
    assert (now.name, now.owner) == ("red", stage.BUILDER)


@pytest.mark.parametrize(
    ("theirs", "merged"),
    [
        (ROWS.replace("Then c.", "Then c twice."), ROWS.replace("runs a.", "runs a fast.").replace("Then c.", "Then c twice.")),
        (ROWS.replace("runs a.", "runs a slow."), None),
        (ROWS.replace("Then b.", "Then b too."), None),
    ],
)
def test_a_markdown_row_merges_clause_by_clause_only_where_no_clause_or_its_neighbour_changed_on_both_sides(theirs: str, merged: str | None) -> None:
    assert land.clause_merged(ROWS, ROWS.replace("runs a.", "runs a fast."), theirs) == merged


@pytest.mark.parametrize("method", ["squash", "rebase"])
def test_a_stacked_yasyf_pr_gh_pr_merge_refuses_merges_through_githubs_merge_async_past_its_rules_by_its_method_and_reads_its_result_until_it_merged(
    demo: str, monkeypatch: pytest.MonkeyPatch, pr_reads: dict[str, dict], method: str
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
        return json.dumps(next(results))

    pr_reads[stacked_pr] = read(state="MERGED", mergeCommit="ccc333ddd444eee")
    monkeypatch.setattr(github, "run", gh)
    monkeypatch.setattr(github, "files", lambda key: ["README.md"])
    monkeypatch.setattr(land.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(install, "touched", lambda repo, files: [])
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: runs.append(argv) or land.subprocess.CompletedProcess(argv, *next(merges)))
    with edit(demo) as data:
        data["prs"][stacked_pr] = state.new_pr(state.pr_url(stacked_pr), "j1", "o1") | {"native": [stacked_pr, "yasyf/cc-skills#4720", "yasyf/cc-skills#4730"]}
    assert land.merged(demo, stacked_pr, method, ["--admin"]) == "merged at ccc333ddd444; it changes no plugin installed on this Mac"
    assert runs == [
        ["gh", "pr", "merge", "4706", "-R", "yasyf/cc-skills", f"--{method}", "--admin"],
        ["gh", "api", "-X", "PUT", endpoint, "-f", f"merge_method={method}", "-f", "merge_action=direct_merge", "-F", "bypass_rules=true"],
    ]
    assert [cmd for cmd in reads_ if land.MERGE_ASYNC in " ".join(cmd)] == [["gh", "api", f"{endpoint}/630b9d5e-3f2a-4f7e-8b0c-2d5f9a8c1e42"]] * 2
    assert state.load(demo)["prs"][stacked_pr]["merged_sha"] == "ccc333ddd444eee"


def test_a_stacked_pr_githubs_merge_async_ends_failed_is_not_landed_with_githubs_message(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    refusal = f"GraphQL: This pull request is part of a stack and {land.STACKED}. (mergePullRequest)"
    failed = {"status": "failed", "details": {"message": "Required status check \"test\" is expected."}}
    merges = iter([(1, "", refusal + "\n"), (0, json.dumps(failed), "")])
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: land.subprocess.CompletedProcess(argv, *next(merges)))
    head = "aaaaaaa111bbbbbbb222"
    assert land.merged(demo, "Forge-AI/litestream#12", "squash", ["--match-head-commit", head]) == f'not landed: GitHub\'s {land.MERGE_ASYNC} ended it failed: Required status check "test" is expected.'


def pushed(name: str, *keys: str) -> None:
    with edit(name) as data:
        state.take_result(data, "j1", f"PUSHED {' '.join(map(state.pr_url, keys))}")


def test_a_reported_yasyf_pr_whose_head_moved_past_its_landers_merges_at_the_next_read_free_of_conflicts_with_no_builder_registering_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    tried(demo, f"not landed: gh pr merge --squash --admin refused it: {DIRTY}")
    pushed(demo, YASYF)
    reads = {KEY: read(), YASYF: read(checks=GREEN)}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    started = []
    for minute, (head, mergeable) in enumerate([("aaaaaaa111", "CONFLICTING"), ("bbbbbbb222", "CONFLICTING"), ("bbbbbbb222", "MERGEABLE"), ("bbbbbbb222", "MERGEABLE")]):
        reads[YASYF] = read(headRefOid=head, mergeable=mergeable, checks=GREEN)
        watch.run_pass(watching, T0 + timedelta(minutes=minute))
        started.append(len(landers))
    assert started == [0, 0, 1, 1]
    assert landers == [(YASYF,)]
    lander = state.load(demo)["prs"][YASYF]["lander"]
    assert (lander["head"], lander["outcome"]) == ("bbbbbbb222", None)


def test_a_yasyf_pr_on_the_head_of_a_pr_the_drive_does_not_track_reads_behind_it_waking_no_one_and_lands_once_github_moves_it_onto_main_as_5547_on_5442_did_not(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], landers: list[tuple[str, ...]]
) -> None:
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        lander = {"head": HEAD_5547, "base": BASE_5547, "pid": os.getpid(), "at": state.stamp(T0), "outcome": f"not landed: it sits on {BASE_5547}, not main"}
        data["prs"][REPLAYED_5547] = state.new_pr(state.pr_url(REPLAYED_5547), "j1", "o1") | {"head": HEAD_5547, "base": BASE_5547, "wants_tap": False, "lander": lander}
    pushed(demo, REPLAYED_5547)
    reads = {KEY: read(), REPLAYED_5547: read(headRefOid=HEAD_5547, baseRefName=BASE_5547, headRefName="o43-card-waits", under=REPLAYED_5442, checks=GREEN)}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    said = [line for minutes in (0, 30, 61, 90) for line in watch.run_pass(watching, T0 + timedelta(minutes=minutes)) if line.startswith((f"STAGE {REPLAYED_5547}", "STUCK"))]
    data = state.load(demo)
    behind = data["prs"][REPLAYED_5547]["stage"]
    assert (behind["name"], behind["owner"], behind["why"]) == (
        "behind",
        stage.OUTSIDE,
        f"it sits on {BASE_5547}, the head of {REPLAYED_5442}, which the drive does not track, so it lands once that PR merges and GitHub moves it onto main",
    )
    assert (said, woken, landers, REPLAYED_5547 in stall.chains(data, NO_CARDS, False, T0 + timedelta(minutes=90))) == ([], [], [], False)
    reads[REPLAYED_5547] = read(headRefOid=HEAD_5547, headRefName="o43-card-waits", checks=GREEN)
    watch.run_pass(watching, T0 + timedelta(minutes=91))
    assert (state.load(demo)["prs"][REPLAYED_5547]["lander"]["base"], landers, woken) == ("main", [(REPLAYED_5547,)], [])


def test_a_pr_on_a_base_no_open_pr_heads_reads_red_and_wakes_its_builder_to_restack_it_onto_its_default_branch(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], landers: list[tuple[str, ...]]
) -> None:
    serve(monkeypatch, {KEY: read(baseRefName=BASE_5547, checks=GREEN)})
    watch.run_pass(watcher(demo), T0)
    red = state.load(demo)["prs"][KEY]["stage"]
    assert ((red["name"], red["owner"], red["why"]), [key for key, _ in woken], landers) == (
        ("red", stage.BUILDER, f"it sits on {BASE_5547}, which no open PR heads, so restack it onto main"),
        [KEY],
        [],
    )


REPLAYED_56856 = "Forge-AI/monorepo#56856"
OPENED_J94 = "Forge-AI/monorepo#56990"
BUILT_J94 = "j94-sandsql-analytics-set"
CARDS_J94 = {"demo:j94-1794746302727": "https://acme.slack.com/archives/D0OWNER01/p1794746303937229", "demo:j94-1794749653081": "https://acme.slack.com/archives/D0OWNER01/p1794749654424739"}
ON_J94 = f"it sits on {BUILT_J94}, the branch of j94, which "
NO_PR_ON_J94 = f"it sits on {BUILT_J94}, the branch of j94, which has opened no PR there yet, so his card asks Approve on it once that PR opens under it"
REPLAYED_56907 = "Forge-AI/monorepo#56907"
RUNNING_J6258 = "durable-agents-j6258"


def staged_lines(watching: watch.Watcher, key: str, minutes: tuple[int, ...]) -> list[str]:
    return [line for minute in minutes for line in watch.run_pass(watching, T0 + timedelta(minutes=minute)) if line.startswith((f"STAGE {key}", "STUCK"))]


def test_replayed_56856_a_pr_on_j94s_built_branch_reads_behind_j94_owed_by_him_then_by_j94_waking_its_builder_only_for_its_own_red_and_gets_his_card_once_j94s_pr_opens_under_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], draws: Draws
) -> None:
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["orders"]["o1"]["jobs"] += ["j94", "j187"]
        asked = [state.new_question(None, f"Pick {waits}?", state.stamp(T0)) | {"waits": waits, "card": link} for waits, link in CARDS_J94.items()]
        data["jobs"]["j94"] = job(state="built", branch=BUILT_J94, waits=list(CARDS_J94), questions=asked)
        data["jobs"]["j187"] = job(state="waiting", prs=[state.pr_url(REPLAYED_56856)])
        data["prs"][REPLAYED_56856] = state.new_pr(state.pr_url(REPLAYED_56856), "j187", "o1") | {"head": "dc7f158000", "base": BUILT_J94, "wants_tap": True, "checked": CHECKED | {"head": "dc7f158000"}}
    stacked = {"baseRefName": BUILT_J94, "headRefName": "yasyf/j187-duck-data-host", "default": "dev", "reviewDecision": "APPROVED"}
    reads = {REPLAYED_56856: read(headRefOid="dc7f158000", checks=RED, **stacked)}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    assert staged_lines(watching, REPLAYED_56856, (0,)) == []
    assert woken == [(REPLAYED_56856, f"{REPLAYED_56856} red since {state.stamp(T0)}, owed by its builder: its checks fail at dc7f158: test [o1 j187]")]
    with edit(demo) as data:
        data["prs"][REPLAYED_56856]["checked"] = CHECKED | {"head": "e26db8d000"}
    reads[REPLAYED_56856] = read(headRefOid="e26db8d000", checks=GREEN, **stacked)
    said = staged_lines(watching, REPLAYED_56856, (1, 31, 62, 93))
    data = state.load(demo)
    kept = data["prs"][REPLAYED_56856]["stage"]
    cards = ", ".join(f"his answer on [his question card]({link})" for link in CARDS_J94.values())
    assert ((kept["name"], kept["owner"], kept["why"]), said, len(woken), draws.keys) == (("behind", stage.HIM, f"{ON_J94}waits on {cards} and has opened no PR there yet; it lands with that PR once it opens"), [], 1, [])
    assert (state.tap_refused(data, data["prs"][REPLAYED_56856]), REPLAYED_56856 in stall.chains(data, NO_CARDS, False, T0 + timedelta(minutes=93))) == (NO_PR_ON_J94, False)
    with edit(demo) as data:
        for seq, question in enumerate(data["jobs"]["j94"]["questions"]):
            question["closed"] = state.new_close("answered", "Yes", None, seq=str(9 + seq))
    said = staged_lines(watching, REPLAYED_56856, (94, 125, 150))
    data = state.load(demo)
    kept = data["prs"][REPLAYED_56856]["stage"]
    assert ((kept["name"], kept["owner"], kept["why"]), said, len(woken), draws.keys) == (("behind", "j94", f"{ON_J94}waits on j188 and has opened no PR there yet; it lands with that PR once it opens"), [], 1, [])
    chained = stall.chains(data, NO_CARDS, False, T0 + timedelta(minutes=150))[REPLAYED_56856]
    assert ([link for link, _, _ in chained], state.tap_refused(data, data["prs"][REPLAYED_56856])) == ([REPLAYED_56856, "j94", "j188"], NO_PR_ON_J94)
    with edit(demo) as data:
        data["jobs"]["j94"].update(state="waiting", prs=[state.pr_url(OPENED_J94)])
        data["prs"][OPENED_J94] = state.new_pr(state.pr_url(OPENED_J94), "j94", "o1") | {"head": "fc4544b027", "base": "dev", "wants_tap": True, "checked": CHECKED | {"head": "fc4544b027"}}
    reads[OPENED_J94] = read(headRefOid="fc4544b027", baseRefName="dev", headRefName=BUILT_J94, default="dev", checks=GREEN, reviewDecision="APPROVED")
    reads[REPLAYED_56856] = read(headRefOid="e26db8d000", checks=GREEN, under=OPENED_J94, **stacked)
    watch.run_pass(watching, T0 + timedelta(minutes=151))
    data = state.load(demo)
    assert (state.built_base(data, data["prs"][REPLAYED_56856]), state.tap_refused(data, data["prs"][REPLAYED_56856]), [sorted(keys) for keys in draws.keys], len(woken)) == (
        None,
        None,
        [sorted([OPENED_J94, REPLAYED_56856])],
        1,
    )


def test_replayed_56907_a_pr_on_a_running_builders_branch_reads_behind_that_builder_wakes_only_its_own_builder_for_its_own_red_and_once_that_builder_is_done_opens_its_branchs_pr_while_that_branch_holds_commits_dev_lacks(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], draws: Draws
) -> None:
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["orders"]["o1"]["jobs"] += ["j6258", "j6317"]
        data["jobs"]["j6258"] = job(branch=RUNNING_J6258)
        data["jobs"]["j6317"] = job(prs=[state.pr_url(REPLAYED_56907)])
        data["prs"][REPLAYED_56907] = state.new_pr(state.pr_url(REPLAYED_56907), "j6317", "o1") | {"head": "362d2966fb", "base": RUNNING_J6258, "wants_tap": True, "checked": CHECKED | {"head": "362d2966fb"}}
    stacked = {"headRefOid": "362d2966fb", "baseRefName": RUNNING_J6258, "headRefName": "durable-agents-j6317", "default": "dev", "reviewDecision": "APPROVED"}
    reads = {REPLAYED_56907: read(checks=RED, **stacked)}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    assert staged_lines(watching, REPLAYED_56907, (0,)) == []
    reads[REPLAYED_56907] = read(checks=GREEN, **stacked)
    said = staged_lines(watching, REPLAYED_56907, (1, 31, 62, 93))
    data = state.load(demo)
    kept = data["prs"][REPLAYED_56907]["stage"]
    assert ((kept["name"], kept["owner"], kept["why"]), said, woken, draws.keys) == (
        ("behind", "j6258", f"it sits on {RUNNING_J6258}, the branch of j6258, which is running and has opened no PR there yet; it lands with that PR once it opens"),
        [],
        [(REPLAYED_56907, f"{REPLAYED_56907} red since {state.stamp(T0)}, owed by its builder: its checks fail at 362d296: test [o1 j6317]")],
        [],
    )
    assert (REPLAYED_56907 in stall.chains(data, NO_CARDS, False, T0 + timedelta(minutes=93)), state.asks_tap(data, data["prs"][REPLAYED_56907])) == (False, False)
    with edit(demo) as data:
        data["jobs"]["j6258"]["state"] = "done"
    reads[REPLAYED_56907] = read(checks=GREEN, base_ahead=10, **stacked)
    for minute in (94, 95):
        watch.run_pass(watching, T0 + timedelta(minutes=minute))
    red = state.load(demo)["prs"][REPLAYED_56907]["stage"]
    assert ((red["name"], red["owner"], red["why"]), draws.keys) == (
        (
            "red",
            stage.BUILDER,
            f"it sits on {RUNNING_J6258}, the branch of j6258, which no open PR heads and which holds 10 commits dev lacks, so open {RUNNING_J6258}'s PR first: a restack onto dev would drop them",
        ),
        [[REPLAYED_56907]],
    )
    reads[REPLAYED_56907] = read(checks=GREEN, base_ahead=0, **stacked)
    watch.run_pass(watching, T0 + timedelta(minutes=96))
    assert stage.red(state.load(demo), REPLAYED_56907) == f"it sits on {RUNNING_J6258}, which no open PR heads, so restack it onto dev"


def test_a_pr_on_a_release_line_its_repository_names_a_landing_base_wakes_no_builder_and_its_lander_lands_it_there_as_litestream_36_on_its_legacy_line_did_not(
    demo: str, monkeypatch: pytest.MonkeyPatch, woken: list[tuple[str, str]], landers: list[tuple[str, ...]]
) -> None:
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["prs"][REPLAYED_5547] = state.new_pr(state.pr_url(REPLAYED_5547), "j1", "o1") | {"wants_tap": False}
    pushed(demo, REPLAYED_5547)
    serve(monkeypatch, {KEY: read(), REPLAYED_5547: read(headRefOid=HEAD_5547, baseRefName=LEGACY, landing_bases=[LEGACY], checks=GREEN)})
    watch.run_pass(watcher(demo), T0)
    assert (state.load(demo)["prs"][REPLAYED_5547]["lander"]["base"], landers, woken) == (LEGACY, [(REPLAYED_5547,)], [])
    assert (land.unmergeable(read(baseRefName=LEGACY, landing_bases=[LEGACY])), land.unmergeable(read(baseRefName=LEGACY))) == (
        None,
        f"it sits on {LEGACY}, not main or a landing base its .claude/long-running.toml names",
    )


REPLAYED_4745_4756 = {"yasyf/cc-skills#4745": ("aa8d008", "FAILURE"), "yasyf/cc-skills#4756": ("ed25f62", "FAILURE"), "yasyf/cc-skills#4755": ("1120acd", None)}
SOCKET = {"name": "Socket Security: Pull Request Alerts", "conclusion": "SUCCESS"}
CC_SKILLS_RUNS = "https://github.com/yasyf/cc-skills/actions/runs"
TESTED_4755 = "3021b51a8b9f346321a534e0e2405c88707cb510"
MERGED_4755 = "merged at 390c80f416be; it changes no plugin installed on this Mac"
PAST_4755 = "merged at 390c80f416be past test red at 1120acd, which main failed on the same {} at {}; it changes no plugin installed on this Mac"
MERGE_4755 = ["gh", "pr", "merge", "4755", "-R", "yasyf/cc-skills", "--squash", "--admin", "--match-head-commit", "1120acd"]
OWN_RED_4755 = "not landed: its own change fails {} at 1120acd, which the default branch does not fail on the same tests where the PR's run of it merged its head"


def pytest_failed(*tests: str) -> str:
    return "".join(f"2026-11-15T08:55:11.5194263Z FAILED {test} - AssertionError: assert 'grading' == 'ready'\n" for test in tests)


def checked_out_4755(log: str) -> str:
    return f"2026-11-15T08:52:40.4070541Z HEAD is now at 1eccac1 Merge 1120acd{'0' * 33} into {TESTED_4755}\n{log}"


def long_running_test(conclusion: str | None) -> dict:
    return {"name": "test", "workflowName": "long-running CI", "conclusion": conclusion, "detailsUrl": f"{CC_SKILLS_RUNS}/43376582330/job/1"}


def branch_history(commits: list[tuple[str, str | None]]) -> str:
    nodes = [
        {"oid": f"{index + 1}" * 40, "checkSuites": {"nodes": [{"workflowRun": {"workflow": {"name": "long-running CI"}}, "checkRuns": {"nodes": [{"name": "test", "conclusion": conclusion, "databaseId": 2}]}}] if ran == "ran" else []}}
        for index, (ran, conclusion) in enumerate(commits)
    ]
    return json.dumps({"data": {"repository": {"object": {"history": {"nodes": nodes}}}}})


def merged_4755(reads: dict[str, dict], runs: list[list[str]], argv: list[str]) -> land.subprocess.CompletedProcess:
    runs.append(argv)
    reads["yasyf/cc-skills#4755"] |= {"state": "MERGED", "mergeCommit": "390c80f416be"}
    return land.subprocess.CompletedProcess(argv, 0, "", "")


def replayed(demo: str, monkeypatch: pytest.MonkeyPatch, checks: dict[str, list[dict]]) -> dict[str, dict]:
    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        for key in REPLAYED_4745_4756:
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"wants_tap": False}
    pushed(demo, *REPLAYED_4745_4756)
    reads = {key: read(headRefOid=head, checks=checks.get(key, [long_running_test(conclusion), SOCKET]), required_checks=[], required_reviews=0) for key, (head, conclusion) in REPLAYED_4745_4756.items()}
    serve(monkeypatch, reads)
    return reads


def test_reported_yasyf_prs_start_no_lander_while_a_check_runs_as_4755_did_and_one_failing_on_its_own_change_reads_red_owed_by_its_builder_at_the_read_that_found_it_as_5629_did_not(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], woken: list[tuple[str, str]]
) -> None:
    reads = replayed(demo, monkeypatch, {"yasyf/cc-skills#4756": [long_running_test("FAILURE") | {"detailsUrl": f"{CC_SKILLS_RUNS}/43376582331/job/3"}, SOCKET]})
    answers = {
        "repos/yasyf/cc-skills/actions/jobs/1": json.dumps({"run_attempt": 1, "steps": TEST_FAILED}),
        "repos/yasyf/cc-skills/actions/jobs/1/logs": checked_out_4755(pytest_failed("t.py::a")),
        "repos/yasyf/cc-skills/actions/jobs/2/logs": pytest_failed("t.py::b"),
        "repos/yasyf/cc-skills/actions/jobs/3": json.dumps({"run_attempt": 1, "steps": TEST_FAILED}),
        "repos/yasyf/cc-skills/actions/jobs/3/logs": checked_out_4755(pytest_failed("t.py::b")),
    }
    monkeypatch.setattr(github, "run", lambda cmd, **_: branch_history([("ran", "FAILURE")]) if cmd[2] == "graphql" else answers[cmd[2]])
    watching = watcher(demo)
    for minute in (0, 1, 9):
        watch.run_pass(watching, T0 + timedelta(minutes=minute))
    data = state.load(demo)
    assert (landers, [key for key, _ in woken if key.startswith("yasyf/")]) == ([("yasyf/cc-skills#4756",)], ["yasyf/cc-skills#4745"])
    assert stage.of(data, "yasyf/cc-skills#4745", NO_CARDS) == stage.Stage(
        "red", stage.BUILDER, "its own change fails test at aa8d008, which the default branch does not fail on the same tests where the PR's run of it merged its head"
    )
    assert stage.of(data, "yasyf/cc-skills#4755", NO_CARDS) == stage.Stage("grading", HomeBuild.graders, "its checks run at 1120acd")
    reads["yasyf/cc-skills#4755"]["checks"] = [long_running_test("SUCCESS"), SOCKET]
    watch.run_pass(watching, T0 + timedelta(minutes=10))
    assert landers[1:] == [("yasyf/cc-skills#4755",)]


@pytest.mark.parametrize(
    ("checks", "log", "history", "main_log", "outcome", "waits"),
    [
        pytest.param([long_running_test(None), SOCKET], "", [], "", "not landed: its checks still run at 1120acd", 0, id="its-test-still-runs"),
        pytest.param([long_running_test("SUCCESS"), SOCKET], "", [], "", MERGED_4755, 0, id="every-check-passed"),
        pytest.param([long_running_test("FAILURE"), SOCKET], checked_out_4755(pytest_failed("t.py::a")), [[("ran", "SUCCESS")]], "", OWN_RED_4755.format("test"), 0, id="the-default-branch-passed-its-test"),
        pytest.param([long_running_test("FAILURE"), SOCKET], checked_out_4755(pytest_failed("t.py::a")), [[("ran", "FAILURE")]], pytest_failed("t.py::a", "t.py::b"), PAST_4755.format("test", "1111111"), 0, id="the-default-branch-fails-its-failing-test-too"),
        pytest.param(
            [long_running_test("FAILURE"), SOCKET], checked_out_4755(pytest_failed("t.py::a", "t.py::b")), [[("ran", "FAILURE")]], pytest_failed("t.py::a", "t.py::b"), PAST_4755.format("2 tests", "1111111"), 0, id="the-default-branch-fails-each-of-its-failing-tests-too"
        ),
        pytest.param(
            [long_running_test("FAILURE"), SOCKET], checked_out_4755(pytest_failed("t.py::a", "t.py::b")), [[("ran", "FAILURE")]], pytest_failed("t.py::a"), OWN_RED_4755.format("test"), 0, id="the-default-branch-fails-only-one-of-its-tests"
        ),
        pytest.param(
            [long_running_test("FAILURE"), SOCKET],
            checked_out_4755("2026-11-15T07:56:38.3636240Z {\"Action\":\"output\",\"Test\":\"TestReadModelReadsItsOwnWritesUnderLoad\",\"Output\":\"--- FAIL: TestReadModelReadsItsOwnWritesUnderLoad (2.81s)\\n\"}\n"),
            [[("ran", "FAILURE")]],
            "2026-11-15T01:31:02.1000000Z     --- FAIL: TestTailBodyContractReference/cursor (0.02s)\n",
            OWN_RED_4755.format("test"),
            0,
            id="the-default-branch-fails-another-step-under-the-same-check-name",
        ),
        pytest.param([long_running_test("FAILURE"), SOCKET], checked_out_4755("2026-11-15T08:53:02.2000000Z ##[error]Process completed with exit code 1.\n"), [], "", OWN_RED_4755.format("test"), 0, id="its-log-names-no-failing-test"),
        pytest.param([long_running_test("FAILURE"), SOCKET], checked_out_4755(pytest_failed("t.py::a")), [[("ran", None)], [("ran", "SUCCESS")]], "", OWN_RED_4755.format("test"), 1, id="it-waits-on-the-default-branchs-test-which-passes"),
        pytest.param([long_running_test("FAILURE"), SOCKET], checked_out_4755(pytest_failed("t.py::a")), [[("ran", None)], [("ran", "FAILURE")]], pytest_failed("t.py::a"), PAST_4755.format("test", "1111111"), 1, id="it-waits-on-the-default-branchs-test-which-fails-it-too"),
        pytest.param([long_running_test("FAILURE"), SOCKET], checked_out_4755(pytest_failed("t.py::a")), [[("skip ci", None), ("ran", "FAILURE")]], pytest_failed("t.py::a"), PAST_4755.format("test", "2222222"), 0, id="tested-on-a-skip-ci-commit-whose-parent-fails-its-test-too"),
        pytest.param([long_running_test("SUCCESS"), SOCKET | {"conclusion": "FAILURE"}], "", [], "", OWN_RED_4755.format(SOCKET["name"]), 0, id="a-check-no-workflow-ran-fails"),
    ],
)
def test_a_yasyf_lander_rereads_its_checks_and_merges_with_admin_at_that_head_once_every_one_finished_and_none_fails_a_test_the_default_branch_passed(
    demo: str, monkeypatch: pytest.MonkeyPatch, checks: list[dict], log: str, history: list[list[tuple[str, str | None]]], main_log: str, outcome: str, waits: int
) -> None:
    histories = iter(history)
    logs = {"repos/yasyf/cc-skills/actions/jobs/1/logs": log, "repos/yasyf/cc-skills/actions/jobs/2/logs": main_log}

    def gh(cmd: list[str], **_: object) -> str:
        if cmd[2] == "graphql":
            assert f'object(oid: "{TESTED_4755}")' in cmd[-1]
            return branch_history(next(histories))
        if cmd[2] in logs:
            return logs[cmd[2]]
        return "plugins/long-running/hooks/message_limits.py\n"

    reads = replayed(demo, monkeypatch, {"yasyf/cc-skills#4755": checks})
    runs: list[list[str]] = []
    slept: list[float] = []
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: merged_4755(reads, runs, argv))
    own_time(monkeypatch, land, sleep=slept.append)
    monkeypatch.setattr(github, "run", gh)
    monkeypatch.setattr(github, "files", lambda key: ["plugins/long-running/hooks/message_limits.py"])
    monkeypatch.setattr(install, "touched", lambda repo, files: [])
    assert (land.run(demo, ["yasyf/cc-skills#4755"]), runs, len(slept)) == (outcome, [MERGE_4755] if outcome.startswith("merged") else [], waits)


FLOOR_4755 = "ccx: GitHub rate limit floor: user core has {} of 5000 left, under the floor of 1000, until 1:55AM (2026-11-15T01:55:01-08:00); top callers: drive job prove 223"


def test_a_yasyf_lander_under_ccxs_rate_limit_floor_merges_a_green_pr_reading_its_default_branch_from_its_fresh_graphql_read(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    reads = replayed(demo, monkeypatch, {"yasyf/cc-skills#4755": [long_running_test("SUCCESS"), SOCKET]})
    runs: list[list[str]] = []
    monkeypatch.setattr(land.subprocess, "run", lambda argv, **kwargs: merged_4755(reads, runs, argv))
    monkeypatch.setattr(github, "default_branch", lambda repo: pytest.fail(f"read {repo}'s default branch through GitHub's REST API, which the floor refuses"))
    monkeypatch.setattr(github, "files", lambda key: ["plugins/long-running/hooks/message_limits.py"])
    monkeypatch.setattr(install, "touched", lambda repo, files: [])
    assert (land.run(demo, ["yasyf/cc-skills#4755"]), runs) == (MERGED_4755, [MERGE_4755])


@pytest.mark.parametrize(("fetched", "what"), [pytest.param(False, "1 PRs of yasyf/cc-skills", id="its-fresh-read"), pytest.param(True, "yasyf/cc-skills's job 1 log", id="the-log-of-its-failing-check")])
def test_a_yasyf_landing_whose_read_ccxs_rate_limit_floor_refuses_ends_unread_said_once_and_lands_again_at_the_next_pass(
    demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], fetched: bool, what: str
) -> None:
    key = "yasyf/cc-skills#4755"
    replayed(demo, monkeypatch, {key: [long_running_test("FAILURE"), SOCKET]})
    if not fetched:
        monkeypatch.setattr(github, "fetch", REAL_FETCH)
    with edit(demo) as data:
        data["prs"][key] |= {"head": "1120acd", "checks": "fail", "failed_checks": ["test"]}
    floors = iter([828, 735])

    def floored(cmd: list[str], **_: object) -> str:
        raise github.GithubError(FLOOR_4755.format(next(floors)))

    monkeypatch.setattr(github, "run", floored)
    watching = watcher(demo)
    first = land.run(demo, [key])
    assert first == f"{land.UNREAD}: could not read {what} from GitHub: {FLOOR_4755.format(828)}"
    assert stage.of(state.load(demo), key, NO_CARDS).name == "ready"
    assert land.start(demo, [key], NO_CARDS) == [(key,)]
    assert land.run(demo, [key]) == f"{land.UNREAD}: could not read {what} from GitHub: {FLOOR_4755.format(735)}"
    assert watch.event_lines(watching) == [f"PR {key}: {first} [o1 j1]"]


def test_a_line_the_coordinator_did_not_get_goes_again_and_says_so_from_the_third_failure(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = posts(monkeypatch, [False, False, False, True])
    watching = watcher(demo)
    line = 'ORPHAN o1 "ship": no job'
    said = [watch.sent(watching, "root", [line] if second == 0 else [], T0 + timedelta(seconds=second)) for second in (0, 5, 10, 15, 20)]
    assert said == [[], [], ["WATCH post to the coordinator failed 3 times in a row: cci post exited 1: no drive demo"], [], []]
    assert [argv[-1] for argv in sent] == [line]


def test_a_line_holding_a_credential_posts_once_with_it_withheld_rather_than_being_refused_on_every_pass(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = posts(monkeypatch)
    watching = watcher(demo)
    line = f"EVENT j1's proof was killed running env ANTHROPIC_API_KEY=sk-ant-{'a1' * 12} probe --live"
    assert (watch.sent(watching, "root", [line], T0), watch.sent(watching, "root", [], T0 + timedelta(seconds=5))) == ([], [])
    assert posted(sent) == [("evidence", f"root@{demo}", "EVENT j1's proof was killed running env ANTHROPIC_API_KEY=<an sk- API key withheld> probe --live")]


def test_an_answer_leaving_a_card_unmarked_says_so_every_ten_minutes_until_it_is_marked_and_a_job_unsent_his_words_holds_them_with_no_line(
    demo: str, monkeypatch: pytest.MonkeyPatch, send_message: Callable[..., None]
) -> None:
    serve(monkeypatch, {})
    asking(monkeypatch, demo, "demo:j1-7-a")
    with edit(demo) as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "Pick demo:j1-7-a?", state.stamp(T0), card=question("demo:j1-7-a")["url"]) | {"waits": "demo:j1-7-a"})
        data["answers"]["a1"] = {"words": "one new tool", "link": f"{CARD}9", "at": state.stamp(T0), "said": state.stamp(T0), "cards": {"demo:j1-7-a": None}, "jobs": {}}
    watching = watcher(demo)

    def said(minutes: int) -> list[str]:
        return [line for line in watch.run_pass(watching, T0 + timedelta(minutes=minutes)) if line.startswith("ANSWER")]

    line = 'ANSWER a1 o1 "one new tool": 1 card with no mark, standing, settled, dropped, superseded or reopened by it; drive answer a1 lists them'
    assert (said(5), said(10), said(15), said(20)) == ([], [line], [], [line])
    assert watch.wakes(line, state.load(demo), NO_CARDS)
    with edit(demo) as data:
        data["answers"]["a1"]["cards"]["demo:j1-7-a"] = {"mark": "stands"}
    assert said(40) == []
    with edit(demo) as data:
        data["jobs"]["j1"].update(agent="a1", where="local", session="session-under-test")
        data["answers"]["a1"]["jobs"]["j1"] = None
    assert said(50) == []
    assert state.load(demo)["answers"]["a1"]["jobs"] == {"j1": {"mark": "sent"}}
    assert [held["words"] for held in state.load(demo)["jobs"]["j1"]["undelivered"]] == ['His answer a1, verbatim: "one new tool"']


O852_MONITOR = "https://app.datadoghq.com/monitors/341360574"
CAP, IMAGE_FIX = "Forge-AI/monorepo#55166", "Forge-AI/monorepo#55225"
DRIVE_CHECKOUT = Path("/drive-checkout")
LATCHED = "j2322 proved it live: Datadog read of monitor 341360574's query: 0 OpenAI /images/edits 400s from principal ci since ac3194dc merged"


def incident_reads(monkeypatch: pytest.MonkeyPatch, statuses: dict[str, str], live: dict[str, bool], checkout: Path) -> list[list[str]]:
    """The drive's checkout, whose infra/envs marks plat-usw2-prod production and plat-usw2-staging not, and release live reading api on both, production serving each fix PR live names and staging none, an older build."""
    calls = []
    (checkout / "infra" / "envs").mkdir(parents=True)
    (checkout / "infra" / "envs" / "plat-usw2-prod.yaml").write_text("id: plat-usw2-prod\nstage: prod\nprod: true\n")
    (checkout / "infra" / "envs" / "plat-usw2-staging.yaml").write_text("id: plat-usw2-staging\nstage: staging\n")

    def run(argv: list[str], at: Path, env: dict | None = None, stdin: str | None = None) -> str:
        calls.append(argv)
        if argv[0] == "pup":
            return json.dumps([{"id": int(monitor), "status": status} for monitor, status in statuses.items()])
        if argv[1:3] == ["release", "live"]:
            return json.dumps([{"target": "api", "component": "platform", "env": env, "commit": commit} for env, commit in (("plat-usw2-prod", "c0ffee"), ("plat-usw2-staging", "01d5ca1e"))])
        return ""

    monkeypatch.setattr(watch.reads, "run", run)
    monkeypatch.setattr(watch.orca, "checkout", lambda name, clone, wait=0: checkout)
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {key: {"components": ["api"], "touched": ["api"], "dev": [], "releases": ["api"], "staged": ["api"]} for key in keys})
    monkeypatch.setattr(watch.github, "carrier", lambda clone: lambda build, merge, number: build == "c0ffee" and live[str(number)])
    return calls


def o852(demo: str) -> None:
    with edit(demo) as data:
        data["orders"]["o852"] = order(
            text="LLM gateway 400s on image edits", incident=True, thread=["D0C4HQWPLCC", "1794522468.515959"], alerts=[O852_MONITOR], jobs=["j2322", "j2344"], parts={"fix": state.new_part()}
        )
        data["jobs"]["j2322"] = job(order="o852", parts=["fix"], prs=[state.pr_url(CAP)])
        data["jobs"]["j2344"] = job(order="o852", parts=["fix"], prs=[state.pr_url(IMAGE_FIX)], state="failed")
        data["prs"][CAP] = state.new_pr(state.pr_url(CAP), "j2322", "o852") | {"state": "merged", "merged_sha": "ac3194dc"}
        data["prs"][IMAGE_FIX] = state.new_pr(state.pr_url(IMAGE_FIX), "j2344", "o852") | {"state": "merged", "merged_sha": "ad20b74e"}
    with edit(demo) as data:
        state.take_result(data, "j2322", f"DONE {LATCHED.removeprefix('j2322 proved it live: ')}")


def test_an_incident_with_no_monitor_held_by_its_open_fix_pr_closes_with_no_datadog_read_once_its_merged_fix_pr_runs_on_each_production_stack(
    home_repo: None,
    demo: str, monkeypatch: pytest.MonkeyPatch, ready_records: list[list[dict]], tmp_path: Path
) -> None:
    served = {OTHER: read()}
    serve(monkeypatch, served)
    live = {"42": False}
    calls = incident_reads(monkeypatch, {}, live, tmp_path)
    lined: list[str] = []
    monkeypatch.setattr(talk, "report_line", lambda name, seq: lined.append(seq) or f"POSTED {POSTED}; o1 closed on report {seq}")
    with edit(demo) as data:
        data["orders"]["o1"].update(incident=True, parts={"fix": state.new_part()}, jobs=["j1", "j2"])
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["jobs"]["j1"].update(parts=["fix"], state="done")
        data["jobs"]["j2"] = job(parts=["fix"], prs=[state.pr_url(OTHER)])
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j2", "o1") | {"head": "aaaaaaa111"}
    watching = watcher(demo)
    assert [line for line in watch.run_pass(watching, T0) if line.startswith("RECOVERED")] == []
    served[OTHER] = read(state="CLOSED")
    with edit(demo) as data:
        data["jobs"]["j2"]["state"] = "failed"
        data["prs"][OTHER]["state"] = "closed"
    live["42"] = True
    fixed = f"{state.pr_url(KEY)} live in production"
    recovered = f"RECOVERED o1: {fixed}, so the drive posted its Live line: POSTED {POSTED}; o1 closed on report 9"
    assert [line for line in watch.run_pass(watching, T0 + timedelta(minutes=1)) if line.startswith("RECOVERED")] == [recovered]
    assert not watch.wakes(recovered, state.load(demo), NO_CARDS)
    assert [(record["text"].startswith("Live: ") and record["text"].endswith(" is over, its fix is live"), record["body"]) for records in ready_records for record in records] == [
        (
            True,
            f"Incident: ship the watch\nFix live: {fixed}\nPRs, as the watch last read them from GitHub: 2\n{shown_pr(demo, KEY)}\n{shown_pr(demo, OTHER)}\n",
        )
    ]
    assert lined == ["9"]
    data = state.load(demo)
    assert (view.incidents(data), data["orders"]["o1"]["proof"]) == ([], fixed)
    assert [argv[1:] for argv in calls if argv[1:3] == ["release", "live"]] == [["release", "live", "--json"]]


def test_o761s_incident_on_a_surface_he_uses_holds_its_live_line_with_one_prove_line_until_a_builders_done_keeps_a_recording_from_there(
    home_repo: None,
    demo: str, monkeypatch: pytest.MonkeyPatch, ready_records: list[list[dict]], tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    monkeypatch.setattr(watch.agents, "handle", lambda name, brief, **_: ("h1", False))
    monkeypatch.setattr(talk, "report_line", lambda name, seq: f"POSTED {POSTED}; o1 closed on report {seq}")
    incident_reads(monkeypatch, {}, {"42": True}, tmp_path)
    surface, recording = "the Iris chat on a Mac", "/tmp/proofs/o761-chat-answers.mov"
    fixed = f"{state.pr_url(KEY)} live in production"
    with edit(demo) as data:
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
    assert (watch.wakes(lines[0], data, NO_CARDS), ready_records, data["orders"]["o1"]["recovered"], state.may_tell(data, "o1")) == (True, [], None, False)
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"].append("j2")
        data["jobs"]["j2"] = job(parts=["fix"], brief_flags={"native": surface})
    with edit(demo) as data:
        state.take_result(data, "j2", "DONE the Iris chat answers again", f"DONE the Iris chat answers again\nCHECK: {recording}\n")
    data = state.load(demo)
    assert (data["orders"]["o1"]["proof"], state.may_tell(data, "o1")) == (f"{fixed}; {recording}", True)
    assert [line for line in watch.run_pass(watching, T0 + timedelta(minutes=3)) if line.startswith(("PROVE", "RECOVERED"))] == [
        f"RECOVERED o1: {fixed}, so the drive posted its Live line: POSTED {POSTED}; o1 closed on report 9"
    ]
    assert state.load(demo)["orders"]["o1"]["proof"] == f"{fixed}; {recording}"


def test_idle_owner_page_stage_does_not_load_the_drive(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with edit(demo) as data:
        for order in data["orders"].values():
            order.update(incident=False)
    watching = watcher(demo)
    monkeypatch.setattr(state, "load", lambda *args, **kwargs: pytest.fail("an idle owner-page stage must not load the drive"))
    assert watch.owner_page_lines(watching, T0) == []


def test_a_page_for_real_users_failing_while_he_is_away_starts_its_fix_and_diagnosis_and_goes_out_though_slack_refuses_its_line_which_the_watch_posts_in_the_drive_channel_next_pass_once(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    posts: list[dict] = []
    down = [True]

    class PagingRepo(providers.RepoProvider):
        page_owner = "tools/ci oncall page me"

        def run(self, template: str, stdin: str | None = None, timeout: int = 0, **fields: str) -> str:
            calls.append(shlex.split(template.format_map({key: shlex.quote(value) for key, value in fields.items()})))
            return "Paged Datadog user u-ym at high urgency: page p-1\n"

    def posted(name: str, post: dict, order: str | None = None) -> dict:
        if down:
            raise slack.SlackError("cc-slack send: Slack is down")
        posts.append(post | {"order": order})
        return {"permalink": POSTED}

    provide(monkeypatch, DRIVE_CHECKOUT, repo=PagingRepo)
    monkeypatch.setattr(watch.orca, "checkout", lambda name, data, wait=0: DRIVE_CHECKOUT)
    monkeypatch.setattr(talk, "posted", posted)
    with edit(demo) as data:
        data["slack"]["channels"] = {"": {"id": "C0DRIVE01", "name": "yasyf-drive-demo"}}
        data["owner"]["heard"] = state.stamp(T0)
        data["orders"]["o1"].update(incident=True, title="SandSQL writes down", created=state.stamp(T0 + timedelta(minutes=5)), users=732569, thread=["C0ALERTS1", "1794200000.000100"])
        data["orders"]["o2"] = order(text="raw_sql reads fail", incident=True, created=state.stamp(T0 - timedelta(minutes=5)), users=732570)
        data["orders"]["o3"] = order(text="the purpose proof fails its reads", incident=True, created=state.stamp(T0 + timedelta(minutes=6)))
    watching = watcher(demo)
    assert (watch.owner_page_lines(watching, T0 + timedelta(minutes=10)), calls) == ([], [])
    away = T0 + timedelta(minutes=20)
    paged = watch.owner_page_lines(watching, away)
    data = state.load(demo)
    fix, cause = (next(job_id for job_id in data["orders"]["o1"]["jobs"] if data["jobs"][job_id]["parts"] == [part]) for part in ("fix", "cause"))
    starts = [state.start_line(demo, "o1", fix, "fix"), state.start_line(demo, "o1", cause, "cause")]
    line = f'🚨 SandSQL writes down: real users failing, by record 732569. Fixing it: {fix} on "fix" starting now; {cause} on "cause" starting now. Paged him.'
    assert paged == [
        "WATCH paged him for incident o1 while he is away: its line did not post, so the page carries it and the watch posts it next pass: cc-slack send: Slack is down; Paged Datadog user u-ym at high urgency: page p-1",
        f"PAGED o1: he is paged and no worker runs its fix or diagnosis, so launch each now: {'; '.join(starts)}; {state.INCIDENT_BRIEF}",
    ]
    assert watch.wakes(paged[1], data, NO_CARDS)
    assert [argv[1:6] for argv in calls] == [["oncall", "page", "me", "--title", "o1 SandSQL writes down"]]
    assert calls[0][-1].split("\n")[:2] == [line, "real users failing: record 732569"]
    assert (data["orders"]["o1"]["paged"], data["orders"]["o1"]["page_post"], posts) == (state.stamp(away), {"text": line, "link": None}, [])
    down.clear()
    assert watch.owner_page_lines(watching, away + timedelta(minutes=1)) == []
    assert posts == [{"text": line, "channel_id": "C0DRIVE01", "order": "o1"}]
    assert watch.owner_page_lines(watching, away + timedelta(minutes=2)) == []
    assert (len(posts), len(calls), state.load(demo)["orders"]["o1"]["page_post"]["link"]) == (1, 1, POSTED)


def test_an_incident_whose_drive_checkout_cannot_move_to_the_default_branch_head_closes_nothing_and_says_so_from_the_third_failed_read(home_repo: None, demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    serve(monkeypatch, {})
    calls = incident_reads(monkeypatch, {}, {"42": True}, tmp_path)

    def unmoved(name: str, clone: str, wait: float = 0) -> Path:
        raise state.DriveError("the drive's checkout was not moved to the head of its default branch")

    monkeypatch.setattr(watch.orca, "checkout", unmoved)
    with edit(demo) as data:
        data["orders"]["o1"].update(incident=True, parts={"fix": state.new_part()})
        data["prs"][KEY].update(state="merged", merged_sha="abc123")
        data["jobs"]["j1"].update(parts=["fix"], state="done")
    watching = watcher(demo)
    lines = [line for minute in range(3) for line in watch.run_pass(watching, T0 + timedelta(minutes=minute)) if line.startswith(("RECOVERED", "WATCH release live"))]
    assert (lines, calls, state.load(demo)["orders"]["o1"]["recovered"]) == (
        ["WATCH release live read failed 3 times in a row: the drive's checkout was not moved to the head of its default branch"],
        [],
        None,
    )

def test_o852_the_deleted_latch_closed_with_its_fix_not_live_is_back_on_the_strip_once_drive_json_loads_and_a_closed_incident_keeps_its_close(demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    serve(monkeypatch, {})
    o852(demo)
    closed_at = state.stamp(T0 - timedelta(minutes=1))
    with edit(demo) as data:
        data["orders"]["o852"].update(fixed=LATCHED, recovered=closed_at, proof=f"{O852_MONITOR} read OK at 5:45pm; {LATCHED}")
        data["orders"]["o1"].update(incident=True, state="done", fixed=LATCHED, recovered=closed_at, proof="read OK")
    incident_reads(monkeypatch, {"341360574": "OK"}, {"55166": True, "55225": False}, tmp_path)
    data = state.load(demo)
    assert ("fixed" in data["orders"]["o852"], data["orders"]["o852"]["recovered"], data["orders"]["o852"]["proof"]) == (False, None, None)
    assert ("fixed" in data["orders"]["o1"], data["orders"]["o1"]["recovered"], data["orders"]["o1"]["proof"]) == (False, closed_at, "read OK")
    assert "o852" in [entry["id"] for entry in view.incidents(data)]
    assert [line for line in watch.run_pass(watcher(demo), T0) if line.startswith("RECOVERED")] == []


CLOSE_REPORT = {"id": "install-proved-j1", "kind": "report", "to": ["posts@demo"], "topic": "install-proved-o1", "attr": {"close": "o1"}, "text": "POST Fix: the watch is live in long-running 1.0.3", "body": "Fixed: Fix: the watch\n"}


@pytest.mark.parametrize("order_state", ["working", "done", "dropped"])
def test_empty_close_report_stage_does_not_load_the_drive(demo: str, monkeypatch: pytest.MonkeyPatch, order_state: str) -> None:
    serve(monkeypatch, {})
    with edit(demo) as data:
        for order in data["orders"].values():
            order.update(state=order_state, close_report=None if order_state == "working" else CLOSE_REPORT)
    watching = watcher(demo)
    monkeypatch.setattr(state, "load", lambda *args, **kwargs: pytest.fail("an empty close-report stage must not load the drive"))
    assert watch.close_report_lines(watching, T0) == []


def test_a_close_report_drive_install_kept_posts_from_the_watch_once_cci_takes_it_and_survives_a_refusal_to_the_next_watch(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from drive import tools

    serve(monkeypatch, {})
    with edit(demo) as data:
        data["orders"]["o1"]["close_report"] = CLOSE_REPORT
    posts: list[list[dict]] = []

    def refused(name: str, lane: str, records: list[dict]) -> list[str]:
        posts.append(records)
        raise state.DriveError("cci: --body-file: not a JSON value")

    monkeypatch.setattr(tools, "sent", refused)
    first = watcher(demo)
    for minute in range(3):
        said = staged(watch.close_report_lines, first, T0 + timedelta(minutes=minute))
    assert said == ["WATCH close report post failed 3 times in a row: cci: --body-file: not a JSON value"]
    assert (posts, state.load(demo)["orders"]["o1"]["close_report"]) == ([[CLOSE_REPORT]] * 3, CLOSE_REPORT)
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: posts.append(records) or ["9"])
    second = watcher(demo)
    assert staged(watch.close_report_lines, second, T0 + timedelta(minutes=3)) == []
    assert (posts[3:], state.load(demo)["orders"]["o1"]["close_report"]) == ([[CLOSE_REPORT]], None)
    assert staged(watch.close_report_lines, second, T0 + timedelta(minutes=4)) == []
    assert len(posts) == 4


def test_a_close_report_an_older_install_kept_on_drive_tooling_a_session_opened_posts_nothing_and_closes_its_order_done_on_its_proof(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from drive import tools

    serve(monkeypatch, {})
    his = CLOSE_REPORT | {"id": "install-proved-j2", "topic": "install-proved-o2", "attr": {"close": "o2"}}
    with edit(demo) as data:
        data["orders"]["o1"] |= {"text": "Drive tooling: the port census reads a settled port", "from": "cci:710171", "proof": "/tmp/proof.txt", "close_report": CLOSE_REPORT}
        data["orders"]["o2"] = order(jobs=[], proof="/tmp/proof.txt", close_report=his)
    posts: list[list[dict]] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: posts.append(records) or ["9"])
    assert staged(watch.close_report_lines, watcher(demo), T0) == []
    orders = state.load(demo)["orders"]
    assert (posts, [(orders[order_id]["state"], orders[order_id]["proof"], orders[order_id]["close_report"]) for order_id in ("o1", "o2")]) == (
        [[his]],
        [("done", "/tmp/proof.txt", None), ("working", "/tmp/proof.txt", None)],
    )


def test_the_watchs_close_report_is_its_orders_one_close_so_the_cci_post_hook_refuses_a_second_while_it_waits_and_once_it_is_posted(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import record_lines
    from drive import tools

    serve(monkeypatch, {})
    with edit(demo) as data:
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
        " send no close of o1",
    )
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: ["688113"])
    assert staged(watch.close_report_lines, watcher(demo), T0) == []
    data = state.load(demo)
    assert (data["orders"]["o1"]["closed_on"], data["orders"]["o1"]["close_report"]) == ("688113", None)
    assert record_lines.decision(meta)["permissionDecisionReason"].startswith("refused, report 688113 already closes o1, so this second close of it posts nothing")
    watchs = {"close": "o1", "seq": "688113"}
    assert (state.second_close(data, "o1", watchs), cli.claim_close(demo, watchs)) == (None, [])


LIVE_LINE = "https://acme.slack.com/archives/D123/p1794200000000400"


@dataclass
class Proved:
    sent: list[list[dict]] = field(default_factory=list)
    said: list[tuple[str, set[str]]] = field(default_factory=list)
    closes: list[tuple[str, str]] = field(default_factory=list)


def proved(monkeypatch: pytest.MonkeyPatch) -> Proved:
    """The watch's own post of a proved order's live line with cci, the report-post Sonnet call and Slack stubbed: each batch it sends, each report seq talk.report_line posts beside the reports the watch posts itself as it posts it, and each close, which report_line makes on the report's close attr."""
    from drive import tools

    kept = Proved()
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: kept.sent.append(records) or ["9"])

    def report_line(name: str, seq: str) -> str:
        kept.said.append((seq, state.watch_posting(state.load(name))))
        with edit(name) as data:
            data["carried"][seq] = LIVE_LINE
        return f"POSTED {LIVE_LINE}{talk.closed(name, 'o1', seq)}"

    def closed(name: str, order_id: str, seq: str) -> str:
        kept.closes.append((order_id, seq))
        with edit(name) as data:
            data["orders"][order_id]["state"] = "done"
        return f"; {order_id} closed on report {seq}"

    monkeypatch.setattr(talk, "report_line", report_line)
    monkeypatch.setattr(talk, "closed", closed)
    return kept


def test_the_watch_posts_a_proved_orders_live_line_through_report_line_from_its_report_and_closes_the_order_with_no_handler(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with edit(demo) as data:
        data["orders"]["o1"]["close_report"] = CLOSE_REPORT
    kept = proved(monkeypatch)
    assert staged(watch.close_report_lines, watcher(demo), T0) == []
    assert (kept.sent, kept.said, kept.closes) == ([[CLOSE_REPORT]], [("9", {"9"})], [("o1", "9")])
    data = state.load(demo)
    assert (data["orders"]["o1"]["close_report"], data["orders"]["o1"]["closed_on"], state.watch_posting(data)) == (None, "9", set())
    assert (state.drive_dir(demo) / watch.POSTS_LOG).read_text().endswith(f"o1 live line posted by the drive: POSTED {LIVE_LINE}; o1 closed on report 9\n")


def test_a_live_line_whose_order_did_not_close_keeps_its_report_so_the_next_pass_closes_it_posting_nothing_more(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with edit(demo) as data:
        data["orders"]["o1"]["close_report"] = CLOSE_REPORT
    kept = proved(monkeypatch)
    monkeypatch.setattr(talk, "closed", lambda name, order_id, seq: f"; {order_id} not closed on report {seq}: drive is locked")
    watching = watcher(demo)
    assert [line for minute in range(3) for line in staged(watch.close_report_lines, watching, T0 + timedelta(minutes=minute))] == [
        f"WATCH close of o1 on its live line failed 3 times in a row: o1 live line posted by the drive: CARRIED {LIVE_LINE}; o1 not closed on report 9: drive is locked"
    ]
    assert (kept.sent, len(kept.said), state.load(demo)["orders"]["o1"]["close_report"]) == ([[CLOSE_REPORT]], 1, CLOSE_REPORT | {"seq": "9"})


def test_a_watch_that_ended_after_its_live_line_posted_closes_the_order_on_its_next_pass_posting_nothing_more(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with edit(demo) as data:
        data["orders"]["o1"]["close_report"] = CLOSE_REPORT | {"seq": "9"}
        data["carried"]["9"] = LIVE_LINE
    kept = proved(monkeypatch)
    assert staged(watch.close_report_lines, watcher(demo), T0) == []
    assert (kept.sent, kept.said, kept.closes, state.load(demo)["orders"]["o1"]["close_report"]) == ([], [], [("o1", "9")], None)


def test_a_proved_order_with_a_reporter_starts_a_report_post_of_its_line_in_their_threads_from_the_watch(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with edit(demo) as data:
        data["orders"]["o1"] |= {"close_report": CLOSE_REPORT, "reporters": [state.reporter(REPORTED)]}
    kept = proved(monkeypatch)
    detached: list[list[str]] = []
    monkeypatch.setattr(state, "detach", lambda name, args, log: detached.append(args) or 1)
    assert staged(watch.close_report_lines, watcher(demo), T0) == []
    assert (kept.sent, kept.said, state.load(demo)["orders"]["o1"]["close_report"]) == ([[CLOSE_REPORT]], [], None)
    assert detached == [["report-post", "9"]]


def test_a_proved_incident_posts_its_live_line_from_the_watch_with_no_handler(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    with edit(demo) as data:
        data["orders"]["o1"] |= {"close_report": CLOSE_REPORT, "incident": True}
    kept = proved(monkeypatch)
    monkeypatch.setattr(watch.agents, "handle", lambda name, brief, **_: pytest.fail(f"handler started on {brief}"))
    assert staged(watch.close_report_lines, watcher(demo), T0) == []
    assert (kept.sent, kept.said, kept.closes) == ([[CLOSE_REPORT]], [("9", {"9"})], [("o1", "9")])


@pytest.mark.parametrize("count", [1, 100])
def test_the_watch_reads_where_every_open_incidents_merged_fix_prs_run_in_one_release_live_read_and_their_monitors_in_one_search(
    home_repo: None,
    demo: str, monkeypatch: pytest.MonkeyPatch, count: int, tmp_path: Path
) -> None:
    serve(monkeypatch, {})
    numbers = [str(1000 + number) for number in range(count)]
    calls = incident_reads(monkeypatch, {}, dict.fromkeys(numbers, True), tmp_path)
    with edit(demo) as data:
        for number in numbers:
            key = f"Forge-AI/monorepo#{number}"
            data["orders"][f"o{number}"] = order(text=f"alert {number}", jobs=[f"j{number}"], incident=True, alerts=[f"https://app.datadoghq.com/monitors/{number}"], parts={"fix": state.new_part()})
            data["jobs"][f"j{number}"] = job(order=f"o{number}", parts=["fix"], state="done", prs=[state.pr_url(key)])
            data["prs"][key] = state.new_pr(state.pr_url(key), f"j{number}", f"o{number}") | {"state": "merged"}
    watching = watcher(demo)
    for read in range(2):
        watch.run_pass(watching, T0 + read * watch.LIVE_EVERY)
    assert [argv[1:] for argv in calls if argv[1:3] == ["release", "live"]] == [["release", "live", "--json"]] * 2
    assert [argv[argv.index("--query") + 1] for argv in calls if argv[0] == "pup"] == [f"id:({' OR '.join(numbers)})"] * 2


def pr_card(*keys: str, url: str = CARD, head: str = "aaaaaaa111") -> dict:
    return {"url": url, "prs": [state.pr_url(key) for key in keys], "asked": state.stamp(T0), "card": {"title": "Fix: the watch", "prs": [{"url": state.pr_url(key), "head": head} for key in keys]}}


def drawn_pr_card(name: str, *keys: str, url: str = CARD) -> dict:
    """His card on the PRs as drive card draws it now."""
    data = state.load(name)
    shown = pr_card(*keys, url=url)
    shown["card"]["prs"] = [row | card.drawn_lines(data, key, view.live_cards(name)) for row, key in zip(shown["card"]["prs"], keys, strict=True)]
    if asked := [({"url": row["url"], "headRefOid": row["head"]}, data["prs"][key]) for row, key in zip(shown["card"]["prs"], keys, strict=True) if state.asks_tap(data, data["prs"][key])]:
        shown["card"]["waits"] = card.ask_waits(asked)
    return shown


@pytest.mark.parametrize(("ended", "said"), [("MERGED", "merged"), ("CLOSED", "closed")])
def test_his_open_pr_card_is_settled_in_place_once_every_pr_it_carries_merged_or_closed(
    demo: str, monkeypatch: pytest.MonkeyPatch, ended: str, said: str
) -> None:
    other = "Forge-AI/monorepo#43"
    with edit(demo) as data:
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
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
    shown, shut = [pr_card(KEY)], []
    settled = {"waits": "merge:Forge-AI:monorepo:42", "title": "Fix: the watch", "prs": [url], "asked": state.stamp(T0), "url": CARD, "answer": None, "tapped": "", "replaced_by": "", "card": pr_card(KEY)["card"]}
    monkeypatch.setattr(watch.view, "cards", lambda drive: {"team_url": "", "questions": [], "closed": list(shut), "pr_cards": list(shown)})
    monkeypatch.setattr(watch.slack, "withdraw_pr_card", lambda pr, why: shown.clear() or shut.append(settled) or [CARD])
    reads = {KEY: read(checks=GREEN, reviewDecision="APPROVED")}
    calls = serve(monkeypatch, reads)
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    assert (redraws.asked, redraws.drawn, state.load(demo)["prs"][KEY]["stage"]["name"]) == ([], [url], "card")
    reads[KEY] = read(state="CLOSED", checks=GREEN, reviewDecision="APPROVED")
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == [f"PR {KEY}: closed [o1 j1]", f"CARD {CARD} settled in place: {KEY} closed"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=2)) == []
    assert (redraws.asked, state.load(demo)["prs"][KEY]["stage"]["name"]) == ([], "closed")
    reads[KEY] = read(checks=GREEN, reviewDecision="APPROVED")
    reopened = watch.run_pass(watching, T0 + timedelta(minutes=3))
    data = state.load(demo)
    assert (reopened, watch.wakes(reopened[0], data, NO_CARDS)) == ([f"PR {KEY}: open [o1 j1]"], False)
    assert (redraws.asked, data["prs"][KEY]["state"], stage.of(data, KEY, view.live_cards(demo)).name) == ([url], "open", "uncarded")
    assert calls == [[KEY]] * 4
    shown[:], shut[:] = [pr_card(KEY)], []
    watch.run_pass(watching, T0 + timedelta(minutes=4))
    assert state.load(demo)["prs"][KEY]["stage"]["name"] == "card"


def test_his_card_cc_slack_keeps_closed_in_place_on_a_pr_that_asks_his_tap_again_reads_uncarded_and_is_redrawn_in_place_each_read_until_it_offers_approve_never_drawn_anew(
    demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws, redraws: Redraws
) -> None:
    url = state.pr_url(KEY)
    with edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
    shut = {"waits": "merge:Forge-AI:monorepo:42", "title": "Fix: the watch", "prs": [url], "asked": state.stamp(T0), "url": CARD, "answer": None, "tapped": "This PR no longer waits on your tap.", "replaced_by": "", "card": pr_card(KEY)["card"]}
    shown = {"team_url": "", "questions": [], "closed": [shut], "pr_cards": []}
    monkeypatch.setattr(watch.view, "cards", lambda drive: shown)
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    sent = posts(monkeypatch)
    watching = watcher(demo)
    for minute in range(3):
        watch.run_pass(watching, T0 + timedelta(minutes=minute))
    data = state.load(demo)
    now = stage.of(data, KEY, shown)
    assert (now.name, now.why, redraws.asked, draws.keys) == ("uncarded", "his card on it is closed, since Approve came off it while it waited on no tap, so the watch redraws it in place", [url] * 3, [])
    assert view.tap_card(data, data["prs"][KEY], shown) is None
    assert [entry for entry in posted(sent) if entry[1] == f"meta@{demo}"] == [
        ("handoff", f"meta@{demo}", f"WATCH draw of his card on {KEY} failed 3 times in a row: it was redrawn in place, which offers him no Approve at the head of {KEY}")
    ]
    shown.update(closed=[], pr_cards=[pr_card(KEY)])
    watch.run_pass(watching, T0 + timedelta(minutes=3))
    data = state.load(demo)
    assert (data["prs"][KEY]["stage"]["name"], draws.keys) == ("card", [])
    assert view.tap_card(data, data["prs"][KEY], shown) == CARD


def test_a_pr_left_uncarded_by_an_older_install_is_redrawn_in_place_once_past_the_uncarded_deadline_and_wakes_no_session(
    demo: str, monkeypatch: pytest.MonkeyPatch, draws: Draws, woken: list[tuple[str, str]]
) -> None:
    url = state.pr_url(KEY)
    shut = {"waits": "merge:Forge-AI:monorepo:42", "title": "Fix: the watch", "prs": [url], "asked": state.stamp(T0), "url": CARD, "answer": None, "tapped": "✓ *Approve* · answered 2:59am", "replaced_by": "", "card": pr_card(KEY)["card"]}
    shown = {"team_url": "", "questions": [], "closed": [shut], "pr_cards": []}
    monkeypatch.setattr(watch.view, "cards", lambda drive: shown)
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    with edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
        stage.stamp(data["prs"][KEY], stage.of(data, KEY, shown), T0)
    asked: list[str] = []
    monkeypatch.setattr(watch.slack, "redraw_pr_cards", lambda pr: asked.append(pr) or shown.update(closed=[], pr_cards=[pr_card(KEY)]) or 1)
    watching = watcher(demo)
    watch.run_pass(watching, T0 + timedelta(minutes=29))
    assert asked == []
    assert [line for line in watch.run_pass(watching, T0 + timedelta(minutes=30)) if line.startswith("STAGE ")] == []
    assert (asked, draws.keys, woken, stage.of(state.load(demo), KEY, shown).name) == ([url], [], [], "card")


def test_a_new_head_on_a_pr_reworked_to_his_words_redraws_nothing_until_its_builders_ready(demo: str, monkeypatch: pytest.MonkeyPatch, redraws: Redraws) -> None:
    with edit(demo) as data:
        data["prs"][KEY]["wants_tap"] = True
        state.rework(data["prs"][KEY], "o2", "bind it as @parent", "https://acme.slack.com/archives/D123/p1794300000000600")
    monkeypatch.setattr(watch.view, "cards", lambda drive: watch.view.NO_CARDS)
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    serve(monkeypatch, {KEY: read(headRefOid="bbbbbbb222", checks=GREEN, reviewDecision="APPROVED")})
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    watch.run_pass(watching, T0 + timedelta(minutes=2))
    assert (redraws.asked, redraws.drawn, state.load(demo)["prs"][KEY]["rework"]["by"]) == ([], [], "o2")


def test_his_open_card_that_left_a_show_me_picture_off_redraws_in_place_once_github_reads_the_body_edited_since_its_draw(
    demo: str, monkeypatch: pytest.MonkeyPatch, redraws: Redraws
) -> None:
    why = 'monorepo PR 42: the Show me picture "The fork snapshot share" does not draw: node "stream": a store node needs "medium", one of memory, disk, bucket, keys, pages, so his card went out without it'
    with edit(demo) as data:
        data["prs"][KEY]["undrawn"] = {"why": why, "at": state.stamp(T0)}
    shown = {"team_url": "", "questions": [], "closed": [], "pr_cards": []}
    monkeypatch.setattr(watch.view, "cards", lambda drive: shown)
    before, after = "2026-11-09T14:59:00Z", "2026-11-09T15:02:00Z"
    reads = {KEY: read(lastEditedAt=after)}
    serve(monkeypatch, reads)
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    shown["pr_cards"] = [drawn_pr_card(demo, KEY)]
    reads[KEY] = read(lastEditedAt=before)
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    assert redraws.asked == []
    reads[KEY] = read(lastEditedAt=after)
    watch.run_pass(watching, T0 + timedelta(minutes=2))
    assert redraws.asked == [state.pr_url(KEY)]


def test_a_step_owed_by_6_30_wakes_the_orders_holder_once_then_the_coordinator_then_him_and_only_the_post_telling_its_outcome_ends_it(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    post = "https://example.slack.com/archives/C1/p1794817827631909"
    opened, by = state.parse_time("2026-11-09T05:07:00-08:00"), state.parse_time("2026-11-09T06:30:00-08:00")
    reports = []
    monkeypatch.setattr(watch, "reported", lambda watcher, records: reports.extend(records) or ["1"] * len(records))
    with edit(demo) as data:
        assert owing.posted(data, "o1", post, by, "its staging test result", [], opened) == "w1"
    watching = watcher(demo)
    assert watch.owing_lines(watching, by - timedelta(minutes=1)) == []
    [first] = watch.owing_lines(watching, by + timedelta(minutes=1))
    assert first.startswith(f'DUE o1 owed w1: step "its staging test result" was due {render.when(state.stamp(by))}, from {post}')
    assert watch.owing_lines(watching, by + timedelta(minutes=30)) == []
    [second] = watch.owing_lines(watching, by + timedelta(minutes=62))
    assert second.startswith("DUE w1 of o1 missed twice")
    assert (watch.owing_lines(watching, by + timedelta(minutes=123)), len(reports)) == ([], 1)
    assert "its staging test result was due" in reports[0]["text"]
    with edit(demo) as data:
        assert owing.posted(data, "o1", "https://example.slack.com/archives/C1/p1794817900000000", None, None, [], by + timedelta(hours=3)) is None
        assert [item["id"] for item in owing.still_open(data["orders"]["o1"])] == ["w1"]
        assert owing.posted(data, "o1", "https://example.slack.com/archives/C1/p1794817920000000", by + timedelta(hours=4), "a reworded status", [], by + timedelta(hours=3)) == "w2"
        assert [item["id"] for item in owing.still_open(data["orders"]["o1"])] == ["w1", "w2"]
        misses = data["orders"]["o1"]["owed"][0]["misses"]
        assert owing.posted(data, "o1", "https://example.slack.com/archives/C1/p1794817930000000", by + timedelta(hours=5), None, [], by + timedelta(hours=3), ["w1"]) is None
        assert (data["orders"]["o1"]["owed"][0]["due"], data["orders"]["o1"]["owed"][0]["misses"], len(data["orders"]["o1"]["owed"])) == (state.stamp(by + timedelta(hours=5)), misses, 2)
        assert owing.posted(data, "o1", "https://example.slack.com/archives/C1/p1794817950000000", None, None, ["w1"], by + timedelta(hours=3)) is None
        assert ([item["id"] for item in owing.still_open(data["orders"]["o1"])], data["orders"]["o1"]["owed"][0]["closed"]["by"]) == (["w2"], "delivered")


def test_closing_an_order_owing_an_open_item_warns_and_the_item_stays_open_in_status(demo: str) -> None:
    moment = state.parse_time("2026-11-09T05:07:00-08:00")
    with edit(demo) as data:
        owing.add(data, "o1", "part", "the docs part", None, {"part_posted": ["o1", "docs"]}, "o1", moment)
        data["orders"]["o1"]["state"] = "done"
        assert owing.close_warning(data["orders"]["o1"], "o1").startswith('WARNING o1 closed still owing w1 part "the docs part" no due')
        assert owing.status_lines(data, moment) == ['o1: w1 part "the docs part" no due']


def test_a_job_clock_is_said_once_when_it_runs_out_while_its_job_still_runs_and_ends_unsaid_for_one_that_returned(demo: str) -> None:
    due = T0 + timedelta(minutes=10)
    with edit(demo) as data:
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
    assert watch.line_lane(line, state.load(demo)) == "meta"
    with edit(demo) as data:
        data["orders"]["o2"] = order(text='Drive tooling: adopt the stale lane, "Do this autonomously without asking me anything"')
        data["jobs"]["j1"].update(due=state.stamp(due), carries={"o2": ["adopt"]})
    assert watch.clock_lines(watching, due) == [f'{line}; his words on o2: "Do this autonomously without asking me anything"']


def test_an_answer_order_whose_last_part_a_report_posted_closes_on_the_watchs_pass_once_its_jobs_end(demo: str) -> None:
    posted = state.new_part() | {"posted": "https://acme.slack.com/archives/D123/p1794200300000100"}
    with edit(demo) as data:
        data["orders"]["o2"] = order(text="why do old runs fail?", kind="answer", jobs=["j2"], parts={"why do old runs fail?": posted})
        data["jobs"]["j2"] = job(order="o2", kind="reader")
    watching = watcher(demo)
    watch.close_answered(watching, T0)
    assert [state.load(demo)["orders"][order_id]["state"] for order_id in ("o1", "o2")] == ["working", "working"]
    with edit(demo) as data:
        data["jobs"]["j2"]["state"] = "done"
    watch.close_answered(watching, T0 + timedelta(seconds=5))
    assert [state.load(demo)["orders"][order_id]["state"] for order_id in ("o1", "o2")] == ["working", "done"]


def test_an_answer_posted_to_him_whose_asker_has_not_heard_it_owes_a_fix_live_item_due_ten_minutes_after_his_line_that_wakes_its_holder_once_late(demo: str) -> None:
    with edit(demo) as data:
        data["orders"]["o1"] |= {"kind": "answer", "reporters": [state.reporter(REPORTED)], "last_line": state.stamp(T0)}
    watching = watcher(demo)
    assert (watch.owing_lines(watching, T0 + timedelta(minutes=1)), state.load(demo)["orders"]["o1"]["owed"]) == ([], [])
    with edit(demo) as data:
        for part in data["orders"]["o1"]["parts"].values():
            part["posted"] = "https://acme.slack.com/archives/D123/p1794200300000100"
    assert owing.tell_due(state.load(demo), "o1") == T0 + owing.TELL_AFTER
    assert watch.owing_lines(watching, T0 + timedelta(minutes=9)) == []
    [line] = watch.owing_lines(watching, T0 + timedelta(minutes=11))
    assert line.startswith(f'DUE o1 owed w1: fix_live "its answer is posted, so post the answer in {REPORTED}: send its report with --attr close=o1')
    assert watch.wakes(line, state.load(demo), NO_CARDS)
    with edit(demo) as data:
        data["orders"]["o1"]["reporters"][0]["told"] = "https://acme.slack.com/archives/C0REPORTS01/p1794400200000300"
    assert watch.owing_lines(watching, T0 + timedelta(minutes=12)) == []
    assert state.load(demo)["orders"]["o1"]["owed"][0]["closed"]["by"] == "met"


def test_a_fix_proven_live_owes_one_fix_live_item_naming_every_untold_reporters_thread_and_a_reporter_added_after_it_closed_owes_another(demo: str) -> None:
    second, later = "https://acme.slack.com/archives/C0REPORTS02/p1794593225751189", "https://acme.slack.com/archives/C0REPORTS03/p1794600000000100"
    told = state.reporter("https://acme.slack.com/archives/C0REPORTS04/p1794500000000100") | {"told": "https://acme.slack.com/archives/C0REPORTS04/p1794500060000100"}
    with edit(demo) as data:
        data["orders"]["o1"] |= {"reporters": [state.reporter(REPORTED), told, state.reporter(second)], "proof": "https://buildkite.com/forge/release/builds/14185", "updated": state.stamp(T0)}
    watching = watcher(demo)
    assert watch.owing_lines(watching, T0) == []
    [item] = state.load(demo)["orders"]["o1"]["owed"]
    assert (item["kind"], item["due"], item["what"]) == (
        owing.FIX_LIVE,
        state.stamp(T0 + owing.TELL_AFTER),
        f"its fix is proven live by https://buildkite.com/forge/release/builds/14185, so post the fix-live note in {REPORTED} and {second}: send its report with --attr close=o1,"
        " which the communicator posts and records with drive order told o1 <its permalink>",
    )
    with edit(demo) as data:
        for held in data["orders"]["o1"]["reporters"]:
            held["told"] = held["told"] or "https://acme.slack.com/archives/C0REPORTS01/p1794400200000300"
    watch.owing_lines(watching, T0 + timedelta(minutes=5))
    with edit(demo) as data:
        data["orders"]["o1"]["reporters"].append(state.reporter(later))
        data["orders"]["o1"]["updated"] = state.stamp(T0 + timedelta(minutes=6))
    watch.owing_lines(watching, T0 + timedelta(minutes=6))
    owed = state.load(demo)["orders"]["o1"]["owed"]
    assert [(each["id"], each["closed"] and each["closed"]["by"], later in each["what"]) for each in owed] == [("w1", "met", False), ("w2", None, True)]


def test_a_passing_job_proof_on_a_pr_of_the_order_owes_the_one_fix_live_note_though_the_order_keeps_no_proof_and_the_order_carrying_it_owes_none(demo: str) -> None:
    proof = f"{state.pr_url(KEY)} live: /tmp/prove.sh exits 0 on long-running 1.0.1"
    release = "https://acme.slack.com/archives/C0REPORTS01/p1794400160000300?thread_ts=1794400100.000200&cid=C0REPORTS01"
    with edit(demo) as data:
        data["orders"]["o1"] |= {"reporters": [state.reporter(REPORTED)], "jobs": ["j1", "j2"]}
        data["orders"]["o2"] = order(text="release the watch", jobs=["j2"], reporters=[state.reporter(release)])
        data["jobs"]["j2"] = job(order="o2", goal="follow its release and prove it live", carries={"o1": ["ship the watch"]}, state="done", said=f"DONE {proof}")
    data = state.load(demo)
    assert data["orders"]["o2"]["reporters"] == [{"link": release, "channel": "C0REPORTS01", "thread": "1794400100.000200", "kept_by": "o1"}]
    assert (owing.tell_due(data, "o1") is not None, owing.tell_due(data, "o2")) == (True, None)
    assert owing.tell_what(data, "o1").startswith(f"its fix is proven live by {proof}, so post the fix-live note in {REPORTED}")


def test_items_take_their_asks_class_and_escalate_an_incidents_before_an_asked_one_and_a_process_one_last(demo: str) -> None:
    moment = state.parse_time("2026-11-09T05:07:00-08:00")
    with edit(demo) as data:
        data["orders"]["o1"]["class"] = "process"
        owing.add(data, "o1", owing.STEP, "the process step", moment, {"post_on": "o1"}, "o1", moment)
        data["orders"]["o1"]["class"] = "incident"
        owing.add(data, "o1", owing.STEP, "the incident step", moment + timedelta(minutes=5), {"post_on": "o1"}, "o1", moment)
        assert [item["class"] for item in data["orders"]["o1"]["owed"]] == ["process", "incident"]
        assert [item["what"] for item in owing.overdue_first(data["orders"]["o1"], moment + timedelta(hours=1))] == ["the incident step", "the process step"]


def test_a_card_one_session_asked_another_for_closes_once_its_job_draws_one_of_his_cards(demo: str) -> None:
    moment = state.parse_time("2026-11-09T05:07:00-08:00")
    with edit(demo) as data:
        owing.add(data, "o1", owing.CARD, "the rollout question", moment + timedelta(minutes=15), {"card": "j1"}, "735999", moment, "j1")
        assert not owing.settle(data, moment)
        data["jobs"]["j1"]["questions"] = [state.new_question("asks/j1-1.md", "Roll out now?", "Roll out now?") | {"waits": "w-j1-1"}]
        assert owing.settle(data, moment)
        assert data["orders"]["o1"]["owed"][0]["closed"]["by"] == "met"


def test_a_job_clock_renewed_after_the_watch_read_it_is_kept_and_not_said(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    due, renewed = T0 + timedelta(minutes=10), state.stamp(T0 + timedelta(minutes=30))
    with edit(demo) as data:
        data["jobs"]["j1"]["due"] = state.stamp(due)
    watching, loaded, renewals = watcher(demo), state.load, []

    def renewing(name: str) -> dict:
        data = loaded(name)
        if not renewals:
            renewals.append(name)
            with edit(name) as fresh:
                fresh["jobs"]["j1"]["due"] = renewed
        return data

    monkeypatch.setattr(state, "load", renewing)
    assert watch.clock_lines(watching, due) == []
    assert state.load(demo)["jobs"]["j1"]["due"] == renewed


def test_the_watch_drops_a_pr_someone_else_opened_that_no_order_of_his_names_once_his_card_on_it_is_withdrawn(
    demo: str, monkeypatch: pytest.MonkeyPatch, withdrawals: Withdrawals
) -> None:
    with edit(demo) as data:
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
    with edit(demo) as data:
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
    with edit(demo) as data:
        data["prs"][KEY]["tap"] = {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack", "picks": {}, "card": None}
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
APPROVED = "green and approved by him at aaaaaaa"
UNLANDED = {"name": "ready", "owner": stage.LANDER, "why": APPROVED, "since": state.stamp(T0), "woke": None}
STALLED_CHAIN = (
    f"{KEY} red, owed by its builder: {READERS_FIRST}; j1 waiting on j2; j2 waiting on {FOOTER}, {FOOTER_STACKED};"
    f" {FOOTER} ready, owed by the lander: {APPROVED}; {FOOTER_STACKED} ready, owed by the lander: {APPROVED}"
)
STALLED_CARD = (
    f"{KEY} red, owed by its builder: {READERS_FIRST}; {FOOTER} ready, owed by the lander: {APPROVED};"
    f" {FOOTER_STACKED} ready, owed by the lander: {APPROVED}"
)
WATCHDOG_ANSWER = f"WHY: {READERS_FIRST}, and j2 waits on {FOOTER}, which the lander has not merged\nWHO: the lander\nNEXT: drive pr land {FOOTER}\nUNREAD: nothing\n"


@pytest.fixture
def watchdogs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Path]]:
    """Each watchdog agent the watch starts, by its brief and answer file; one runs until its answer file is written."""
    started: list[tuple[str, Path]] = []
    monkeypatch.setattr(watch.agents, "watchdog", lambda name, brief, cwd, out: started.append((brief, out)) or len(started))
    monkeypatch.setattr(watch.state, "alive", lambda pid: not started[pid - 1][1].exists())
    monkeypatch.setattr(watch.agents, "handle", lambda name, brief, **_: ("h1", False))
    return started


def stalling(name: str) -> watch.Watcher:
    with edit(name) as data:
        data["orders"]["o1"]["jobs"] = ["j1", "j2"]
        data["jobs"]["j1"] |= {"state": "waiting", "waits": ["j2"]}
        data["jobs"]["j2"] = job(goal="the release footer", state="waiting", waits=[FOOTER, FOOTER_STACKED], prs=[state.pr_url(FOOTER), state.pr_url(FOOTER_STACKED)])
        data["prs"][KEY]["stage"] = RED_STAGE
        for key in (FOOTER, FOOTER_STACKED):
            data["prs"][key] = state.new_pr(state.pr_url(key), "j2", "o1") | {"stage": UNLANDED}
    watching = watcher(name)
    watching.cards = NO_CARDS
    return watching


def watchdog_answered(out: Path, answer: str = WATCHDOG_ANSWER) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"I read the checks and both transcripts.\n\n```text\n{answer}```\n")


def test_a_red_pr_behind_a_builder_waiting_on_prs_the_lander_has_not_landed_gets_one_watchdog_at_an_hour_one_stuck_line_and_no_line_to_him(
    demo: str, watchdogs: list[tuple[str, Path]], ready_records: list[list[dict]]
) -> None:
    watching = stalling(demo)
    said = {minutes: watch.stall_lines(watching, T0 + timedelta(minutes=minutes)) for minutes in (0, 59, 60, 61)}
    assert said == {0: [], 59: [], 60: [], 61: []}
    [(brief, out)] = watchdogs
    assert brief.splitlines()[:8] == [
        f"Stall: {KEY}, which holds j1, j2, unmoved 60 minutes",
        "Job: j1 of o1: ship the watch",
        "Holder: the coordinator, session session-under-test",
        f"Chain: {STALLED_CHAIN}",
        "Drive: demo",
        "Read:",
        "- cci grep '(o1|j1|#42|j2|#53645|#53648)\\b' --drive demo --limit 30",
        f"- drive status o1 j1 {KEY} j2 {FOOTER} {FOOTER_STACKED} --drive demo",
    ]
    assert {f"- gh pr view {state.pr_url(key)} --json state,mergedAt,headRefOid,statusCheckRollup" for key in (KEY, FOOTER)} <= set(brief.splitlines())
    watchdog_answered(out)
    assert watch.stall_lines(watching, T0 + timedelta(minutes=68)) == [
        f"STUCK j1 o1: {KEY}, which holds j1, j2, unmoved 68 minutes: {STALLED_CARD}. WHY: {READERS_FIRST}, and j2 waits on {FOOTER}, which the lander has not merged; NEXT: drive pr land {FOOTER};"
        f" its whole answer, WHO and UNREAD with it, in {out}"
    ]
    for minutes in (69, 97, 98, 99, 150):
        assert watch.stall_lines(watching, T0 + timedelta(minutes=minutes)) == []
    assert (ready_records, len(watchdogs)) == ([], 1)
    with edit(demo) as data:
        data["prs"][KEY]["stage"] = RED_STAGE | {"why": "it fails readers-first at bbbbbbb"}
    watch.stall_lines(watching, T0 + timedelta(minutes=151))
    data = state.load(demo)
    assert (list(data["stalls"]), data["stalls"][KEY]["since"], data["stalls"][KEY]["agent"], data["stalls"][KEY]["said"]) == ([KEY], state.stamp(T0 + timedelta(minutes=151)), None, None)
    assert [brief.splitlines()[0] for brief, _ in watchdogs] == [f"Stall: {KEY}, which holds j1, j2, unmoved 60 minutes"]


def test_jobs_waiting_on_one_pr_the_lander_has_not_landed_make_one_stall_one_watchdog_and_one_stuck_line_naming_each(
    demo: str, watchdogs: list[tuple[str, Path]], ready_records: list[list[dict]]
) -> None:
    def waiting(data: dict, job_id: str) -> None:
        data["orders"]["o1"]["jobs"].append(job_id)
        data["jobs"][job_id] = job(state="waiting", waits=[OTHER])

    with edit(demo) as data:
        data["prs"][KEY]["state"] = "merged"
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"stage": UNLANDED}
        data["jobs"]["j1"] |= {"state": "waiting", "waits": [OTHER]}
        for job_id in ("j2", "j3"):
            waiting(data, job_id)
    watching = watcher(demo)
    watching.cards = NO_CARDS
    for minutes in (0, 60):
        watch.stall_lines(watching, T0 + timedelta(minutes=minutes))
    [(brief, out)] = watchdogs
    assert brief.splitlines()[0] == f"Stall: {OTHER}, which holds j1, j2, j3, unmoved 60 minutes"
    watchdog_answered(out)
    unlanded = f"{OTHER} ready, owed by the lander: {APPROVED}"
    assert watch.stall_lines(watching, T0 + timedelta(minutes=68)) == [
        f"STUCK j1 o1: {OTHER}, which holds j1, j2, j3, unmoved 68 minutes: {unlanded}. WHY: {READERS_FIRST}, and j2 waits on {FOOTER}, which the lander has not merged; NEXT: drive pr land {FOOTER};"
        f" its whole answer, WHO and UNREAD with it, in {out}"
    ]
    with edit(demo) as data:
        waiting(data, "j4")
    for minutes in (70, 98, 99, 130):
        assert watch.stall_lines(watching, T0 + timedelta(minutes=minutes)) == []
    assert (ready_records, len(watchdogs), list(state.load(demo)["stalls"])) == ([], 1, [OTHER])


STAGING_TARGETS = ("api", "rsw-cp", "rsw-dp", "rsw-go", "rsw-search", "executor", "staging-smoke", "sandsql", "runtime-api-next", "cloudrunner-next", "dashboard")
STAGING_LIVE = [
    *({"target": target, "env": STAGING_ENV, "commit": "afb881f92076aa", "release": "rc/2026-11-15/4"} for target in STAGING_TARGETS[:7]),
    {"target": "sandsql", "env": STAGING_ENV, "commit": "f0f336aa2315bb", "release": "release-emergency/2026-11-15/3"},
    *({"target": target, "env": STAGING_ENV, "commit": "8ab0e2526f06cc", "release": "release-emergency/2026-11-15/4"} for target in STAGING_TARGETS[8:]),
]


def staged_on(target: str, commit: str) -> str:
    return f"staging:{target}/{STAGING_ENV}@{commit}"


def test_23_staging_waits_of_4_jobs_on_one_env_make_one_stall_one_watchdog_and_one_stuck_line_and_a_lone_service_left_undeployed_its_own(
    named_paths: None,
    demo: str, watchdogs: list[tuple[str, Path]], ready_records: list[list[dict]]
) -> None:
    waits = {
        "j1": [staged_on(target, "6d44e8728ec1") for target in STAGING_TARGETS if target not in ("staging-smoke", "sandsql")],
        "j2": [staged_on("sandsql", "15a282a50022")],
        "j3": [staged_on(target, "5f986a0") for target in STAGING_TARGETS],
        "j4": [staged_on("api", "c5dca80ed9be"), staged_on("dashboard", "2c18705b7744")],
    }
    thing, serving = f"staging:{STAGING_ENV}", "the watch's last release live read finds it serving"
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"] = list(waits)
        data["jobs"]["j1"] |= {"state": "waiting", "waits": waits["j1"]}
        data["jobs"] |= {"j2": job(state="waiting", waits=waits["j2"]), "j3": job(), "j4": job(state="waiting", waits=waits["j4"])}
    watching = watcher(demo)
    watching.cards, watching.live = NO_CARDS, STAGING_LIVE
    assert watch.stall_lines(watching, T0) == []
    with edit(demo) as data:
        data["jobs"]["j3"] |= {"state": "waiting", "waits": waits["j3"]}
    for minutes in (26, 59):
        assert watch.stall_lines(watching, T0 + timedelta(minutes=minutes)) == []
    kept = state.load(demo)["stalls"]
    assert (sum(map(len, waits.values())), list(kept), kept[thing]["since"], len(kept[thing]["print"].split("; "))) == (23, [thing], state.stamp(T0), 23)
    assert watch.stall_lines(watching, T0 + timedelta(minutes=60)) == []
    [(brief, out)] = watchdogs
    lines = brief.splitlines()
    assert lines[0] == f"Stall: {thing}, which holds j1, j2, j3, j4, unmoved 60 minutes"
    staging = [line for line in lines if line.startswith("Staging:")]
    assert len(staging) == len(STAGING_TARGETS)
    assert {
        f"Staging: api on {STAGING_ENV}, awaited at 6d44e8728ec1, 5f986a0, c5dca80ed9be: {serving} afb881f92076 of rc/2026-11-15/4",
        f"Staging: sandsql on {STAGING_ENV}, awaited at 15a282a50022, 5f986a0: {serving} f0f336aa2315 of release-emergency/2026-11-15/3",
        f"Staging: staging-smoke on {STAGING_ENV}, awaited at 5f986a0: {serving} afb881f92076 of rc/2026-11-15/4",
        f"Staging: dashboard on {STAGING_ENV}, awaited at 6d44e8728ec1, 5f986a0, 2c18705b7744: {serving} 8ab0e2526f06 of release-emergency/2026-11-15/4",
    } <= set(staging)
    watchdog_answered(out)
    [stuck] = watch.stall_lines(watching, T0 + timedelta(minutes=68))
    assert stuck.startswith(f"STUCK j1 o1: {thing}, which holds j1, j2, j3, j4, unmoved 68 minutes: {STAGING_ENV} running api at 6d44e8728ec1 or a build carrying it")
    for minutes in (69, 97, 98, 99):
        assert watch.stall_lines(watching, T0 + timedelta(minutes=minutes)) == []
    assert (ready_records, len(watchdogs), list(state.load(demo)["stalls"])) == ([], 1, [thing])
    lone = staged_on("cloudrunner-next", "5f986a0")
    with edit(demo) as data:
        for job_id in ("j1", "j2", "j4"):
            data["jobs"][job_id] |= {"state": "done", "waits": []}
        data["jobs"]["j3"]["waits"] = [lone]
    assert watch.stall_lines(watching, T0 + timedelta(minutes=100)) == []
    assert state.load(demo)["stalls"][thing]["since"] == state.stamp(T0 + timedelta(minutes=100))
    watch.stall_lines(watching, T0 + timedelta(minutes=160))
    [_, (brief, out)] = watchdogs
    assert brief.splitlines()[0] == f"Stall: {thing}, which holds j3, unmoved 60 minutes"
    assert [line for line in brief.splitlines() if line.startswith("Staging:")] == [
        f"Staging: cloudrunner-next on {STAGING_ENV}, awaited at 5f986a0: {serving} 8ab0e2526f06 of release-emergency/2026-11-15/4"
    ]
    watchdog_answered(out)
    assert watch.stall_lines(watching, T0 + timedelta(minutes=168)) == [
        f"STUCK j3 o1: {thing}, which holds j3, unmoved 68 minutes: {STAGING_ENV} running cloudrunner-next at 5f986a0 or a build carrying it https://github.com/Forge-AI/monorepo/commit/5f986a0."
        f" WHY: {READERS_FIRST}, and j2 waits on {FOOTER}, which the lander has not merged; NEXT: drive pr land {FOOTER}; its whole answer, WHO and UNREAD with it, in {out}"
    ]


@pytest.mark.parametrize("count", [2, 16])
def test_stall_chains_resolve_shared_waits_once_per_snapshot(demo: str, monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    data = state.load(demo)
    data["prs"] = {}
    normalized = data["jobs"]["j1"] | {"state": "waiting", "prs": []}
    data["jobs"] = {f"j{n}": dict(normalized) for n in range(1, count + 1)}
    resolved = []
    link = stall.link

    def resolve(data: dict, cards: dict, subject: str) -> tuple[str, list[str]]:
        resolved.append(subject)
        return link(data, cards, subject)

    monkeypatch.setattr(stall, "link", resolve)
    for commit in ("6d44e8728ec1", "5d44e8728ec1"):
        wait = staged_on("api", commit)
        for held in data["jobs"].values():
            held["waits"] = [wait]
        resolved.clear()
        with state.indexed(data):
            found = stall.chains(data, NO_CARDS, False, T0)
        assert list(found) == list(data["jobs"])
        for job_id, links in found.items():
            assert len(links) == 2
            assert links[0] == (job_id, f"{job_id} waiting on {wait}", False)
            assert (links[-1][0], links[-1][2]) == (wait, True)
        assert sorted(resolved) == sorted([*data["jobs"], wait])


@pytest.mark.parametrize("count", [2, 16])
def test_stall_projection_indexes_questions_and_release_cuts_once_per_pass(demo: str, monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    wait = staged_on("api", "6d44e8728ec1")
    with edit(demo) as data:
        data["jobs"] = {f"j{n}": job(state="waiting", waits=[wait]) for n in range(1, count + 1)}
        data["orders"]["o1"]["jobs"] = list(data["jobs"])
    scans = []
    unindexed = []
    scanned_cuts, drawn_on = state.scanned_cuts, view.drawn_on

    def cuts(data: dict) -> list[tuple[str, str]]:
        scans.append(len(data["jobs"]))
        return scanned_cuts(data)

    def questions(data: dict, *cards: str) -> list[tuple[str, dict]]:
        if state.index_of(data) is None:
            unindexed.append(cards)
        return drawn_on(data, *cards)

    monkeypatch.setattr(state, "scanned_cuts", cuts)
    monkeypatch.setattr(view, "drawn_on", questions)
    watching = watcher(demo)
    watching.cards = NO_CARDS
    for minute in (0, 1):
        assert watch.stall_lines(watching, T0 + timedelta(minutes=minute)) == []
        assert scans == [count] * (minute + 1)
        assert unindexed == []
    kept = state.load(demo)["stalls"]
    assert list(kept) == [f"staging:{STAGING_ENV}"]
    assert kept[f"staging:{STAGING_ENV}"]["since"] == state.stamp(T0)


def test_release_waits_fold_into_one_stall_per_pr_and_env_and_staging_waits_into_one_per_env(demo: str) -> None:
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"] = ["j1", "j2"]
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1")
        data["jobs"]["j1"] |= {"state": "waiting", "waits": [f"release:api:{OTHER}", f"release:rsw-go:{OTHER}", f"release:api:{OTHER}@{STAGING_ENV}", staged_on("api", "6d44e8728ec1")]}
        data["jobs"]["j2"] = job(state="waiting", waits=[f"release:executor:{OTHER}", f"release:api:{KEY}", staged_on("rsw-go", "5f986a0")])
    data = state.load(demo)
    found = stall.things(data, stall.chains(data, NO_CARDS, False, T0), [])
    assert {thing: (held["members"], held["subjects"], held["joins"]) for thing, held in found.items()} == {
        f"release:{OTHER}": ([f"release:api:{OTHER}", f"release:rsw-go:{OTHER}", f"release:executor:{OTHER}"], ["j1", "j2"], True),
        f"release:{OTHER}@{STAGING_ENV}": ([f"release:api:{OTHER}@{STAGING_ENV}"], ["j1"], True),
        f"staging:{STAGING_ENV}": ([staged_on("api", "6d44e8728ec1"), staged_on("rsw-go", "5f986a0")], ["j1", "j2"], True),
        f"release:{KEY}": ([f"release:api:{KEY}"], ["j2"], True),
    }


def test_a_staging_or_release_stall_keeps_its_hour_while_waits_join_and_restarts_it_once_one_ends_while_a_pr_stack_restarts_on_any_change() -> None:
    kept = stall.aged(None, "a; b", True, T0)
    assert stall.aged(kept, "c; a; b", True, T0 + timedelta(minutes=30)) == kept | {"print": "c; a; b"}
    assert stall.aged(kept, "a; c", True, T0 + timedelta(minutes=30))["since"] == state.stamp(T0 + timedelta(minutes=30))
    assert stall.aged(kept, "a; b; c", False, T0 + timedelta(minutes=30))["since"] == state.stamp(T0 + timedelta(minutes=30))


def claimed_by(name: str, claims: dict[str, dict[str, datetime]]) -> None:
    """A drive of its own on this Mac whose orders each hold a staging claim by its post's ts and end, as its watch keeps a claim no closing line ended yet."""
    data = state.new_drive(name, "coordinator", f"session-of-{name}", f"/{name}")
    for order_id, held in claims.items():
        data["orders"][order_id] = order(
            jobs=[],
            claims={ts: {"channel": "C0STAGING1", "link": staging_claim(ts), "until": state.stamp(until), "since": ts, "said": None, "replies_read": state.stamp(T0)} for ts, until in held.items()}
        )
    state.create(data)


def staging_claim(ts: str) -> str:
    return f"https://example.slack.com/archives/C0STAGING1/p{ts.replace('.', '')}"


def test_a_staging_env_another_drives_staging_claim_holds_stands_behind_it_with_no_watchdog_stuck_line_or_line_to_him_until_its_end_starts_its_hour(
    demo: str, watchdogs: list[tuple[str, Path]], ready_records: list[list[dict]]
) -> None:
    thing, ends = f"staging:{STAGING_ENV}", T0 + timedelta(minutes=90)
    with edit(demo) as data:
        data["jobs"]["j1"] |= {"state": "waiting", "waits": [staged_on("api", "6d44e8728ec1"), staged_on("rsw-go", "6d44e8728ec1")]}
        data["orders"]["o1"]["claims"] = {"1794746488.439069": {"channel": "C0STAGING1", "link": staging_claim("1794746488.439069"), "until": state.stamp(T0 + timedelta(hours=4)), "since": "1794746488.439069", "said": None, "replies_read": state.stamp(T0)}}
    claimed_by("durable", {"o717": {"1794762734.416779": ends}, "o1718": {"1794735942.908509": T0 - timedelta(minutes=50), "1794754129.998849": T0 + timedelta(minutes=45)}})
    watching = watcher(demo)
    watching.cards, watching.live = NO_CARDS, STAGING_LIVE
    for minutes in (0, 60, 61, 89):
        assert watch.stall_lines(watching, T0 + timedelta(minutes=minutes)) == []
    kept = state.load(demo)["stalls"][thing]
    behind = f"behind the staging claim {staging_claim('1794762734.416779')} of o717 on drive durable, which ends {state.stamp(ends)}"
    assert (watchdogs, kept["since"], kept["print"].split("; ")[-1], kept["agent"]) == ([], state.stamp(T0), behind, None)
    assert watch.stall_lines(watching, T0 + timedelta(minutes=90)) == []
    kept = state.load(demo)["stalls"][thing]
    assert (kept["since"], "behind" in kept["print"]) == (state.stamp(ends), False)
    assert watch.stall_lines(watching, T0 + timedelta(minutes=149)) == []
    assert watchdogs == []
    watch.stall_lines(watching, T0 + timedelta(minutes=150))
    [(brief, out)] = watchdogs
    assert brief.splitlines()[0] == f"Stall: {thing}, which holds j1, unmoved 60 minutes"
    watchdog_answered(out)
    [stuck] = watch.stall_lines(watching, T0 + timedelta(minutes=158))
    assert stuck.startswith(f"STUCK j1 o1: {thing}, which holds j1, unmoved 68 minutes: {STAGING_ENV} running api at 6d44e8728ec1")
    with edit("durable") as data:
        data["orders"]["o717"]["claims"]["1794762734.416779"] |= {"until": state.stamp(T0 + timedelta(hours=5)), "since": "1794790000.000001"}
    for minutes in (160, 188, 200, 250):
        assert watch.stall_lines(watching, T0 + timedelta(minutes=minutes)) == []
    assert (ready_records, len(watchdogs)) == ([], 1)


def test_a_stall_whose_stuck_line_another_live_watch_claimed_says_nothing_and_one_whose_claimer_died_says_it_once(
    demo: str, monkeypatch: pytest.MonkeyPatch, watchdogs: list[tuple[str, Path]], tmp_path: Path
) -> None:
    watching = stalling(demo)
    out = tmp_path / "stall.md"
    watchdog_answered(out)
    live = {"pid": 4242, "started": "Mon Nov  9 10:00:00 2026"}
    monkeypatch.setattr(watch, "started", lambda pid: live["started"] if pid == live["pid"] else "now")
    with edit(demo) as data:
        data["stalls"][KEY] = {
            "print": STALLED_CARD,
            "since": state.stamp(T0),
            "agent": {"pid": 1, "out": str(out), "at": state.stamp(T0 + timedelta(minutes=60))},
            "found": None,
            "said": None,
            "claim": live,
        }
    assert watch.stall_lines(watching, T0 + timedelta(minutes=98)) == []
    live["pid"] = 0
    said = [watch.stall_lines(watching, T0 + timedelta(minutes=minutes)) for minutes in (99, 100)]
    assert ([len(lines) for lines in said], [line.split(":")[0] for line in said[0]], watchdogs) == ([1, 0], ["STUCK j1 o1"], [])
    assert (state.load(demo)["stalls"][KEY]["said"], state.load(demo)["stalls"][KEY]["claim"]) == (state.stamp(T0 + timedelta(minutes=99)), None)


def test_a_stall_on_an_order_the_meta_session_holds_reaches_it_in_one_hop_and_its_watchdog_reads_it_and_wakes_its_agent_by_its_resume(
    demo: str, monkeypatch: pytest.MonkeyPatch, watchdogs: list[tuple[str, Path]], ready_records: list[list[dict]]
) -> None:
    watching = stalling(demo)
    tooling_order(demo, "waiting")
    with edit(demo) as data:
        data["roles"]["meta"] = {"session": "meta-session", "terminal": None, "cwd": "/meta", "started": state.stamp(T0)}
        data["jobs"]["j1"] |= {"agent": "agent-j1", "where": "local", "session": "meta-session"}
    for minutes in (0, 60):
        watch.stall_lines(watching, T0 + timedelta(minutes=minutes))
    [(brief, out)] = watchdogs
    lines = brief.splitlines()
    assert (
        "Holder: the meta session, session meta-session; j1 runs on this Mac as agent agent-j1 of session meta-session; to wake it:"
        " drive job resume j1 with the message verbatim"
    ) in lines
    assert f"- cc-transcript {watch.session_transcript(state.load(demo)['roles']['meta'])}, from {watch.when(state.stamp(T0))} on" in lines
    assert "session-under-test" not in brief
    watchdog_answered(out)
    [stuck] = watch.stall_lines(watching, T0 + timedelta(minutes=68))
    sent = posts(monkeypatch)
    assert watch.handed(watching, [stuck], T0 + timedelta(minutes=68)) == []
    assert posted(sent) == [("handoff", f"meta@{demo}", stuck)]


def test_j4802_a_stalls_watchdog_brief_says_its_jobs_remote_lane_ended_so_it_offers_no_wake(demo: str, watchdogs: list[tuple[str, Path]]) -> None:
    watching = stalling(demo)
    with edit(demo) as data:
        data["jobs"]["j1"] |= {"where": "remote", "dispatch": "ctx_26d8fa72c939", "deaths": [{"dispatch": "ctx_26d8fa72c939", "workspace": "w1", "why": "ended by drive job end", "at": state.stamp(T0)}]}
    for minutes in (0, 60):
        watch.stall_lines(watching, T0 + timedelta(minutes=minutes))
    [(brief, _)] = watchdogs
    assert (
        f"Holder: the coordinator, session session-under-test; j1's lane ended at {watch.when(state.stamp(T0))}: ended by drive job end,"
        " so neither drive job wake nor SendMessage reaches it, and only the holder's drive job resume j1 starts its next worker"
    ) in brief.splitlines()


def test_a_sessions_transcript_is_read_from_the_folder_it_first_ran_in_after_its_role_moved_to_the_drives_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    first = tmp_path / "projects" / "-Users-me-src-monorepo" / "s-1.jsonl"
    first.parent.mkdir(parents=True)
    first.write_text("{}\n")
    moved = {"session": "s-1", "cwd": "/Users/me/worktrees/monorepo/drive-demo"}
    assert watch.session_transcript(moved) == first
    assert watch.session_transcript(moved | {"session": "s-2"}) == tmp_path / "projects" / "-Users-me-worktrees-monorepo-drive-demo" / "s-2.jsonl"


def test_at_most_two_watchdog_agents_run_at_once_oldest_stall_first(demo: str, watchdogs: list[tuple[str, Path]]) -> None:
    released = {"j1": OTHER, "j2": "Forge-AI/monorepo#44", "j3": "Forge-AI/monorepo#45"}
    releases = {job_id: f"release:{service}:{released[job_id]}" for job_id, service in (("j1", "api"), ("j2", "web"), ("j3", "sand"))}
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"] = ["j1", "j2", "j3"]
        data["jobs"] |= {"j2": job(), "j3": job(state="waiting", waits=[releases["j3"]])}
        for key in released.values():
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1")
    watching = watcher(demo)
    watching.cards = NO_CARDS
    watch.stall_lines(watching, T0)
    with edit(demo) as data:
        for job_id in ("j1", "j2"):
            data["jobs"][job_id] |= {"state": "waiting", "waits": [releases[job_id]]}
    for minutes in (5, 65, 66):
        watch.stall_lines(watching, T0 + timedelta(minutes=minutes))
    assert len(watchdogs) == 2
    for _, out in watchdogs:
        watchdog_answered(out)
    said = watch.stall_lines(watching, T0 + timedelta(minutes=67))
    assert [line.split(":")[0] for line in said] == ["STUCK j3 o1", "STUCK j1 o1"]
    assert len(watchdogs) == 2
    watch.stall_lines(watching, T0 + timedelta(minutes=68))
    assert [brief.splitlines()[0] for brief, _ in watchdogs] == [
        f"Stall: release:{released['j3']}, which holds j3, unmoved 65 minutes",
        f"Stall: release:{released['j1']}, which holds j1, unmoved 60 minutes",
        f"Stall: release:{released['j2']}, which holds j2, unmoved 63 minutes",
    ]


def test_a_job_on_a_release_his_hold_on_every_release_holds_keeps_no_stall_and_once_he_lifts_it_its_watchdog_reads_the_watchs_release_read_and_his_cards_naming_its_service(
    home_repo: None,
    demo: str, monkeypatch: pytest.MonkeyPatch, watchdogs: list[tuple[str, Path]], ready_records: list[list[dict]], recorded: Answers
) -> None:
    wait = f"release:sand-next:{KEY}"
    recorded.listed = [{"id": "15c4de1aa", "title": "Are releases frozen?", "body": "no releases until I say we can again", "tags": ["release-hold"], "created_at": "2026-11-13T19:10:39Z"}]
    stood = {
        "print": f"the release of sand-next carrying {KEY} {state.pr_url(KEY)}",
        "since": state.stamp(T0 - timedelta(hours=12)),
        "agent": {"pid": 1, "out": "/s/stall.md", "at": state.stamp(T0 - timedelta(hours=11))},
        "found": {"WHO": "him", "NEXT": "Lift the hold, or approve an exception"},
        "said": state.stamp(T0 - timedelta(hours=11)),
        "claim": None,
    }
    asked = "When should dashboard, sand-next and sand ship?"
    on_it = {"question": asked, "options": ["Now", "Later"], "recommended": "Now", "asked": state.stamp(T0), "url": CARD, "card": {"title": asked, "body": ""}}
    elsewhere = {"question": "Which store should the sand CLI read?", "options": ["Team", "Run"], "recommended": "Team", "asked": state.stamp(T0), "url": f"{CARD[:-3]}200", "card": {"title": "Which store?", "body": "sandsql"}}
    with edit(demo) as data:
        data["prs"][KEY].update(state="merged", merged_sha="bd434a0f1b2c3d4e")
        data["jobs"]["j1"] |= {"state": "waiting", "waits": [wait]}
        data["stalls"][f"release:{KEY}"] = stood
    watching = watcher(demo)
    watching.cards = NO_CARDS | {"questions": [on_it, elsewhere]}
    watching.serving = {wait: {"plat-usw2-prod": False, "plat-usw2-staging": False}}
    for minutes in (0, 60, 120):
        assert watch.stall_lines(watching, T0 + timedelta(minutes=minutes)) == []
    assert (watchdogs, ready_records, state.load(demo)["stalls"]) == ([], [], {})
    recorded.listed = []
    for minutes in (121, 181):
        watch.stall_lines(watching, T0 + timedelta(minutes=minutes))
    [(brief, _)] = watchdogs
    assert brief.splitlines()[0] == f"Stall: release:{KEY}, which holds j1, unmoved 60 minutes"
    assert [line for line in brief.splitlines() if line.startswith(("Release:", "Hold:", "Card:"))] == [
        f"Release: {wait}: sand-next on plat-usw2-prod, plat-usw2-staging is behind {KEY}'s merge commit bd434a0f1b2c, running an older build",
        f'Card: "{asked}" {CARD}, asked by no job of the drive',
    ]


def test_replayed_705676_and_705742_jobs_waiting_on_one_pr_on_his_approve_keep_no_stall_and_send_him_no_line(
    demo: str, watchdogs: list[tuple[str, Path]], ready_records: list[list[dict]]
) -> None:
    stood = {
        "print": f"{FOOTER} card, owed by him: green, waits on his Approve",
        "since": state.stamp(T0 - timedelta(minutes=443)),
        "agent": {"pid": 1, "out": "/s/stall.md", "at": state.stamp(T0 - timedelta(minutes=383))},
        "found": {"WHO": "him", "NEXT": "He answers the open cards"},
        "said": state.stamp(T0 - timedelta(minutes=31)),
        "claim": None,
    }
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"] = ["j1", "j2", "j3", "j4"]
        data["jobs"]["j1"] |= {"state": "waiting", "waits": [FOOTER]}
        data["stalls"][FOOTER] = stood
        data["jobs"] |= {"j2": job(state="waiting", waits=["j3", "j4"]), "j3": job(state="waiting", waits=[FOOTER]), "j4": job(state="waiting", waits=[FOOTER])}
        data["prs"][FOOTER] = state.new_pr(state.pr_url(FOOTER), "j3", "o1") | {"stage": ON_HIS_APPROVE}
    watching = watcher(demo)
    watching.cards = NO_CARDS
    for minutes in (0, 60, 120):
        assert watch.stall_lines(watching, T0 + timedelta(minutes=minutes)) == []
    assert (watchdogs, ready_records, state.load(demo)["stalls"]) == ([], [], {})


def test_a_stalls_watchdog_is_handed_his_recorded_answers_ccn_ranks_nearest_its_jobs_goal_and_each_service_its_release_waits_wait_on(
    demo: str, monkeypatch: pytest.MonkeyPatch, watchdogs: list[tuple[str, Path]]
) -> None:
    wait = f"release:sand-next:{KEY}"
    answer = {"id": "7030890", "asked": "May sand-next ship to Macs past the hold?", "said": "Keep the hold: Macs get none of the fixes until you lift it", "link": CARD}
    ranked: list[list[str]] = []
    monkeypatch.setattr(settled, "matched", lambda texts, data: ranked.append(texts) or [answer])
    with edit(demo) as data:
        data["prs"][KEY].update(state="merged", merged_sha="bd434a0f1b2c3d4e")
        data["jobs"]["j1"] |= {"state": "waiting", "waits": [wait], "goal": "ship the Mac fixes"}
    watching = watcher(demo)
    watching.cards = NO_CARDS
    for minutes in (0, 60):
        watch.stall_lines(watching, T0 + timedelta(minutes=minutes))
    [(brief, _)] = watchdogs
    assert [line for line in brief.splitlines() if line.startswith("Answered:")] == [f'Answered: 7030890 "{answer["asked"]}": "{answer["said"]}" {CARD}']
    assert ranked == [["ship the Mac fixes", "sand-next"]]


def test_a_stalls_watchdog_is_handed_the_watchs_last_buildkite_read_of_each_build_it_waits_on(demo: str, monkeypatch: pytest.MonkeyPatch, watchdogs: list[tuple[str, Path]]) -> None:
    buildkite_reads(monkeypatch, {"forge/release/14769": BUILD_RUNNING})
    with edit(demo) as data:
        data["jobs"]["j1"] |= {"state": "waiting", "waits": [RELEASE_BUILD]}
    watching = watcher(demo)
    watching.cards = NO_CARDS
    assert watch.build_lines(watching, T0) == []
    for minutes in (0, 60):
        watch.stall_lines(watching, T0 + timedelta(minutes=minutes))
    [(brief, _)] = watchdogs
    assert [line for line in brief.splitlines() if line.startswith("Build:")] == [
        f"Build: {RELEASE_BUILD} reads RUNNING at the watch's last Buildkite read, which it makes again once a delivery of its CI's to the receiver names it"
    ]


def test_watchdog_agents_count_those_still_searching_and_those_started_within_the_hour() -> None:
    def kept(started: int, said: int | None = None) -> dict:
        return {"agent": {"pid": 1, "out": "/s/a.md", "at": state.stamp(T0 - timedelta(minutes=started))}, "said": None if said is None else state.stamp(T0 - timedelta(minutes=said))}

    stalls = {"j1": kept(5), "j2": kept(12), "j3": kept(30, 0), "j4": {"agent": None, "said": None}, "j5": kept(3, 35), KEY: kept(61, 0)}
    assert stall.starts(stalls, T0) == (2, 4)


@pytest.mark.parametrize(
    ("stops", "on_hold", "subjects"),
    [
        (lambda data: None, False, [KEY, "j1"]),
        (lambda data: None, True, []),
        (lambda data: data["prs"][KEY].update(hold_label=True), False, [KEY, "j1"]),
        (lambda data: data["prs"][KEY].update(tap={"label": "Hold", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "pressed"}), False, [KEY, "j1"]),
        (lambda data: data["prs"][KEY].update(stage=ON_HIS_APPROVE), False, ["j1"]),
        (lambda data: data["jobs"]["j1"].update(waits=[OTHER]) or data["prs"][OTHER].update(stage=ON_HIS_APPROVE), False, []),
        (
            lambda data: data["jobs"]["j1"].update(waits=[OTHER])
            or data["prs"][OTHER].update(tap={"label": "Hold", "at": state.stamp(T0), "head": "bbbbbbb222", "source": "pressed"}, stage={"name": "held", "owner": stage.HIM, "why": f"held by his Hold of {state.stamp(T0)}", "since": state.stamp(T0), "woke": None}),
            False,
            [],
        ),
        (lambda data: data["jobs"]["j1"].update(waits=[CARD]), False, []),
        (lambda data: data["jobs"]["j1"]["questions"].append(state.new_question(None, "Which store?", state.stamp(T0), card=CARD) | {"waits": "demo:j1-1"}), False, []),
        (
            lambda data: data["jobs"]["j1"].update(waits=[OTHER])
            or data["orders"]["o1"]["jobs"].append("j2")
            or data["jobs"].update(j2=job(state="done", questions=[state.new_question(None, "Which store?", state.stamp(T0), card=CARD, holds=OTHER) | {"waits": "demo:j2-1"}])),
            False,
            [],
        ),
        (lambda data: data["jobs"]["j1"].update(due=state.stamp(T0 + timedelta(hours=2))), False, []),
        (lambda data: data["jobs"].update(j2=job()) or data["jobs"]["j1"].update(waits=["j2"]), False, []),
    ],
    ids=["unstopped", "his hold on every release", "red under the hold label", "red under his hold", "his card", "a pr he owes", "a green pr he holds", "his question card", "his question card no wait names", "his question card holding the pr", "due ahead", "running job"],
)
def test_no_chain_is_aged_past_a_step_of_his_a_time_to_come_or_a_running_job(demo: str, stops: Callable[[dict], None], on_hold: bool, subjects: list[str]) -> None:
    with edit(demo) as data:
        data["jobs"]["j1"] |= {"state": "waiting", "waits": [f"release:sand:{OTHER}"]}
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1")
        data["prs"][KEY]["stage"] = RED_STAGE
        stops(data)
    asked = {"question": "Which store should the sand CLI read?", "url": CARD, "card": {"title": "Which store?", "body": ""}}
    assert list(stall.chains(state.load(demo), NO_CARDS | {"questions": [asked]}, on_hold, T0)) == subjects


REAL_SWEEP = watch.sweep_lines


def test_the_daily_sweep_files_a_drive_tooling_order_per_bar_crossed_and_an_order_still_open_on_a_bar_takes_its_new_measure(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = T0 - timedelta(minutes=20)
    ts = f"{asked.timestamp():.6f}"
    owed = {"thread_ts": ts, "ts": ts, "text": "is it up?", "permalink": f"https://acme.slack.com/archives/D123/p{ts.replace('.', '')}"}
    fresh = {**owed, "ts": f"{(T0 - timedelta(minutes=5)).timestamp():.6f}"}
    monkeypatch.setattr(slack, "owed", lambda session: [fresh, owed])
    with edit(demo) as data:
        data["prs"][KEY]["tap"] = state.new_tap("Approve", "aaaaaaa111", "card", at=state.stamp(T0 - timedelta(hours=25)))
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"tap": state.new_tap("Approve", "bbbbbbb222", "card", at=state.stamp(T0 - timedelta(hours=1)))}
    watching = watcher(demo)
    old_card = {"question": ASKED, "url": CARD, "asked": state.stamp(T0 - timedelta(hours=49))}
    watching.cards = NO_CARDS | {"questions": [{**old_card, "url": POSTED, "asked": state.stamp(T0 - timedelta(hours=1))}, old_card]}
    end = state.events_end(demo)
    assert REAL_SWEEP(watching, T0) == []
    first = state.load(demo)
    assert [(order_id, order["title"], order["owner"], order["note"]) for order_id, order in first["orders"].items() if order_id != "o1"] == [
        ("o2", "Message unanswered past 10 minutes", "meta", f"1 owed, the oldest since {render.when(slack.sent_at(owed).isoformat())}: {owed['permalink']}"),
        ("o3", "Approved pull request open past 24 hours", "meta", f"1 PRs: {KEY}"),
    ]
    assert first["orders"]["o2"]["text"] == "Drive tooling: the daily Slack sweep found a message of yours still unanswered after 10 minutes"
    assert first["slack"]["swept"] == {"at": state.stamp(T0), "offset": end}
    posted = T0 + timedelta(hours=2, microseconds=600000)
    monkeypatch.setattr(state, "now", lambda: posted)
    for wait, url in ((1, POSTED), (2, f"{POSTED}1"), (6, f"{POSTED}2")):
        state.append_event(demo, "said", text="It is up.", channel=None, thread=None, url=url, card=None, order=None, visuals=[], prs=[], edited=False, answers=[f"{(posted - timedelta(minutes=wait)).timestamp():.6f}"])
    state.append_event(demo, "said", text="It is in.", channel=None, thread=None, url=POSTED, card=None, order=None, visuals=[], prs=[], edited=True, answers=[f"{(posted - timedelta(minutes=50)).timestamp():.6f}"])
    end = state.events_end(demo)
    assert REAL_SWEEP(watching, T0 + timedelta(hours=23)) == []
    assert len(state.load(demo)["orders"]) == 3
    assert REAL_SWEEP(watching, T0 + timedelta(days=1)) == []
    second = state.load(demo)
    assert [(order_id, order["title"], order["note"]) for order_id, order in second["orders"].items() if order_id not in first["orders"]] == [
        ("o4", "Answers slower than 2 minutes", f"3 answers, 90th percentile 6.0 min, slowest 6.0 min: {POSTED}2"),
    ]
    assert second["orders"]["o3"]["note"] == f"2 PRs: {KEY}, {OTHER}"
    assert second["slack"]["swept"] == {"at": state.stamp(T0 + timedelta(days=1)), "offset": end}


def test_the_daily_sweep_ends_his_open_drive_tooling_cards_jev_reads_as_details_on_their_recommended_option_and_takes_his_tap_off_a_flake_fix_of_test_code_alone(
    demo: str, recorded: Answers, closed_cards: list[tuple[str, str, str, bool]], monkeypatch: pytest.MonkeyPatch
) -> None:
    ruling = "43aa6658b12e48506f920bb19cb32e353fa64a28"
    message = "https://acme.slack.com/archives/D123/p1794300000000900?thread_ts=1794300000.000800"
    recorded.shown["43aa665"] = {"id": ruling, "title": "Should an outage report be marked?", "body": f"{settled.TOOLING_SAID}\nNotes: typed in a card's thread", "tags": ["drive:demo", "scope:durable"]}
    tooling_order(demo, "running")
    detail = SHORT_ASK.replace("ASK How should", "ASK Where should")
    kept = {}
    for text in (SHORT_ASK, detail):
        kept[text] = state.drive_dir(demo) / "asks" / f"j1-{state.ask_hash(text)}.md"
        kept[text].parent.mkdir(exist_ok=True)
        kept[text].write_text(text)
    title = detail.splitlines()[0].removeprefix("ASK ")
    with edit(demo) as data:
        data["answers"][ruling] = {"words": settled.TOOLING_SAID, "link": message, "at": state.stamp(T0), "said": state.stamp(T0), "cards": {}, "jobs": {}}
        data["jobs"]["j1"]["questions"] = [
            state.new_question(str(kept[SHORT_ASK]), SHORT_ASK.splitlines()[0].removeprefix("ASK "), state.stamp(T0), card=CARD) | {"waits": "demo:j1-1"},
            state.new_question(str(kept[detail]), title, state.stamp(T0), card=POSTED) | {"waits": "demo:j1-2"},
        ]
        data["prs"][KEY] |= {"exempt": state.FLAKE_FIX, "wants_tap": True}
        data["prs"][OTHER] = state.new_pr(state.pr_url(OTHER), "j1", "o1") | {"head": "bbbbbbb222", "exempt": state.FLAKE_FIX}
    changed = {KEY: ["rust/crates/sandsql-server/src/admin/tests.rs"], OTHER: ["api/src/run.ts", "api/src/run.test.ts"]}
    monkeypatch.setattr(github, "files", lambda key: changed[key])
    monkeypatch.setattr(settled, "detail", lambda case, words, leave: "Where should" in case)
    monkeypatch.setattr(slack, "owed", lambda session: [])
    watching = watcher(demo)
    watching.cards = NO_CARDS | {"questions": [{"question": text.splitlines()[0], "url": card, "card": {"waits": waits}, "asked": state.stamp(T0)} for text, card, waits in ((SHORT_ASK, CARD, "demo:j1-1"), (detail, POSTED, "demo:j1-2"))]}
    option = "One snapshot file per finished run, written when it ends, old runs backfilled: opens in 3 requests"
    decided = settled.TOOLING_DETAILS.decided.format(option=option)
    lines = REAL_SWEEP(watching, T0)
    swept = state.load(demo)
    assert (lines[0], lines[-1]) == (
        f'j1\'s question "{render.cut(title, 80)}" ended decided on its recommended option, "{option}", under his ruling 43aa665 on tooling details [o1 j1]',
        f"PR {KEY}: waits on his tap no more, its flake fix changing only test and fixture code at aaaaaaa, which lands with no tap [o1 j1]",
    )
    closed = swept["jobs"]["j1"]["questions"][-1]["closed"]
    assert ((closed["how"], closed["words"], closed["link"], closed["ruling"]), swept["jobs"]["j1"]["questions"][0]["closed"]) == (("answered", decided, message, "43aa665"), None)
    assert [card for card, *_ in closed_cards] == [POSTED]
    assert (swept["prs"][KEY]["wants_tap"], swept["prs"][OTHER]["wants_tap"], swept["slack"]["swept"]["at"]) == (False, True, state.stamp(T0))


def test_the_daily_sweep_counts_his_owed_messages_only_while_the_session_this_drives_handlers_run_beside_holds_his_dm(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    ts = f"{(T0 - timedelta(minutes=20)).timestamp():.6f}"
    owed = {"thread_ts": ts, "ts": ts, "text": "is it up?", "permalink": f"https://acme.slack.com/archives/D123/p{ts.replace('.', '')}"}
    holder = {"session": "another-drives-communicator"}
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"replies": [owed], "holder_session": holder["session"]})
    watching = watcher(demo)
    watching.cards = NO_CARDS
    unanswered = "Message unanswered past 10 minutes"
    assert REAL_SWEEP(watching, T0) == []
    assert unanswered not in [order["title"] for order in state.load(demo)["orders"].values()]
    holder["session"] = agents.beside(state.load(demo))[1]["session"]
    assert REAL_SWEEP(watching, T0 + timedelta(days=1)) == []
    assert [order["note"] for order in state.load(demo)["orders"].values() if order["title"] == unanswered] == [f"1 owed, the oldest since {render.when(slack.sent_at(owed).isoformat())}: {owed['permalink']}"]


def test_a_sweep_waits_for_a_pass_that_read_his_cards_and_a_failed_owed_read_is_said_as_a_failed_read_is(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(session: str) -> list[dict]:
        raise slack.SlackError("cc-slack daemon not running")

    monkeypatch.setattr(slack, "owed", refused)
    watching = watcher(demo)
    assert REAL_SWEEP(watching, T0) == []
    assert watching.failures == {}
    watching.cards = NO_CARDS
    assert [REAL_SWEEP(watching, T0) for _ in range(watch.FAILURES_BEFORE_SAYING)][-1] == ["WATCH daily Slack sweep failed 3 times in a row: cc-slack daemon not running"]
    assert state.load(demo)["slack"]["swept"] is None


def test_a_wake_writes_the_marks_the_watch_asked_for_only_once_it_delivered_its_line(demo: str) -> None:
    asked = state.stamp(T0)
    with edit(demo) as data:
        data["prs"][KEY]["stage"] = {"name": "red", "owner": stage.BUILDER, "why": "", "since": asked, "woke": None, "waking": asked}
        data["prs"][KEY]["objecting"] = {"head": "aaaaaaa111", "at": asked}
        data["mains"]["Forge-AI/monorepo"] = {"test_one": {"owner": KEY, "woke": None, "waking": asked, "run": "", "workflow": "", "since": asked}}
    assert (stage.waking(asked, T0 + stage.WAKE_GRACE - timedelta(seconds=1)), stage.waking(asked, T0 + stage.WAKE_GRACE)) == (True, False)
    stage.mark_woken(demo, [f"stage:{KEY}@aaaaaaa111", f"objected:{KEY}@aaaaaaa111", "main:Forge-AI/monorepo@test_one"], asked)
    pr = state.load(demo)["prs"][KEY]
    assert ((pr["stage"]["woke"], pr["stage"]["waking"]), pr["objected"], pr["objecting"]) == ((asked, None), "aaaaaaa111", None)
    assert (state.load(demo)["mains"]["Forge-AI/monorepo"]["test_one"]["woke"], state.load(demo)["mains"]["Forge-AI/monorepo"]["test_one"]["waking"]) == (asked, None)


def test_a_builders_staging_line_after_its_staging_test_passed_writes_no_wait_so_the_watch_says_free_once(named_paths: None, demo: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]) -> None:
    part = "staging test of #42 before it lands"
    proof = "https://buildkite.com/forge/release/builds/16121"
    with edit(demo) as data:
        [change] = data["orders"]["o1"]["parts"]
        data["orders"]["o1"]["parts"][part] = state.new_part()
        data["jobs"]["j1"]["parts"] = [change, part]
    waits_on(demo, f"o1:{part}")
    waiting_job(demo)
    with edit(demo) as data:
        data["prs"][KEY]["tap"] = {"label": "Approve", "at": state.stamp(T0), "head": "aaaaaaa111", "source": "slack", "picks": {}, "card": None}
    serve(monkeypatch, {KEY: read(checks=GREEN, reviewDecision="APPROVED")})
    watching = watcher(demo)
    watch.run_pass(watching, T0)
    done = f"DONE {proof} passed every staging case of #42 at aaaaaaa111"
    with edit(demo) as data:
        state.take_result(data, "j1", done, f"{done}\nCHECK: {proof}\nCASE a rollback refused: pass {proof}\n")
    said = [line for line in watch.run_pass(watching, T0 + timedelta(minutes=30)) if line.startswith("FREE ")]
    waits = []
    for n in range(1, 4):
        with edit(demo) as data:
            state.staging_testers(data, "j1", f"PUSHED {state.pr_url(KEY)}\nSTAGING: #42 a foreign-table read through the query route is refused for another principal\n")
            waits.append(data["prs"][KEY]["waits"])
        said += [line for line in watch.run_pass(watching, T0 + timedelta(minutes=30 + 2 * n)) if line.startswith("FREE ")]
    assert (len(said), waits) == (1, [[], [], []])


def test_a_proof_rerun_on_the_same_wait_line_says_nothing_on_its_prs_and_a_changed_one_is_said(demo: str) -> None:
    with edit(demo) as data:
        data["jobs"]["j9"] = state.new_job("o1", "builder", "repair the staging-claim rule", [], state.stamp()) | {"state": "waiting", "repro": "/p.sh"}
        data["orders"]["o1"]["jobs"].append("j9")
    first = "WAIT his Approve on Forge-AI/monorepo#57038, the rule edit"
    changed = "WAIT yasyf/cc-skills#5832 landing and installing"
    for version, wait in (("1.0.1308", first), ("1.0.1308", first), ("1.0.1311", first), ("1.0.1312", changed)):
        install.proof_kept(demo, "j9", f"BLOCKED j9's repair: /p.sh exits 1 on {version}: {wait}", f"checked the rule\n{wait}\n", version, 1, None)
    proofs = [event["change"] for event, _ in state.read_events(demo) if event["kind"] == "proof" and event.get("job") == "j9"]
    assert proofs == [f"{install.PROOF_WAITS}: j9's repair: /p.sh exits 1 on 1.0.1308: {first}", f"{install.PROOF_WAITS}: j9's repair: /p.sh exits 1 on 1.0.1312: {changed}"]


def test_a_stage_wake_built_at_a_head_a_push_moved_the_pr_off_sends_nothing_and_writes_no_mark(demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    from drive import tools

    posted: list[dict] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, posts: posted.extend(posts) or ["9"])
    kept = {"name": "red", "owner": "j1", "why": "its required check test failed at bbbbbbb", "since": state.stamp(T0), "woke": None, "waking": state.stamp(T0)}
    with edit(demo) as data:
        data["prs"][KEY]["stage"] = dict(kept)
    line = f"{KEY} red, owed by j1: its required check test failed at bbbbbbb [o1 j1]"
    assert run("pr", "wake", state.pr_url(KEY), "--text", line, "--woke", f"stage:{KEY}@bbbbbbb222", "--woke-at", state.stamp(T0)) == (
        0,
        f"{KEY}: sent nothing, since its line was built at bbbbbbb and a push moved it to aaaaaaa; the watch's next pass reads its stage there",
    )
    assert (posted, state.load(demo)["prs"][KEY]["stage"]) == ([], kept)


def test_a_github_read_ccxs_rate_limit_floor_refuses_is_said_once_and_no_read_is_tried_again_until_the_reset_it_names(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    refusal = f"could not read Forge-AI/monorepo's pull requests from GitHub: {FLOOR_4755.format(0)}"
    calls = []

    def refused(keys: list[str], into: dict[str, str] | None = None) -> tuple[dict[str, dict], github.GithubError | None]:
        calls.append(keys)
        return {}, github.GithubError(refusal)

    monkeypatch.setattr(github, "fetch", refused)
    remerging(monkeypatch, {})
    watching = watcher(demo)
    reset = datetime.fromisoformat("2026-11-15T01:55:01-08:00")
    before = [watch.github_lines(watching, reset - timedelta(minutes=minutes)) for minutes in (16, 15, 1)]
    after = [watch.github_lines(watching, reset + timedelta(minutes=minutes)) for minutes in (1, 2)]
    assert before == [[f"WATCH ccx's GitHub rate-limit floor refused the watch's GitHub reads, so it reads none until {reset.isoformat()}, then each it owes: {refusal}"], [], []]
    assert (after, calls) == ([[], []], [[KEY], [KEY], [KEY]])


def test_a_lane_on_this_mac_with_no_transcript_since_its_worker_started_is_unprompted_once_the_bound_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worktree = tmp_path / "lane"
    monkeypatch.setattr(watch, "lane_transcript", lambda path: transcripts.get(path))
    transcripts: dict[str, Path] = {}
    job = {"route": state.LOCAL_ROUTE, "worker_started": state.stamp(T0), "terminal": "term_1"}
    worker = {"terminal": {"worktreePath": str(worktree)}}
    assert (watch.unprompted(job, worker, T0 + watch.UNPROMPTED - timedelta(seconds=1)), watch.unprompted(job, worker, T0 + watch.UNPROMPTED)) == (False, True)
    written = tmp_path / "session.jsonl"
    written.write_text("{}\n")
    os.utime(written, (T0.timestamp() + 60, T0.timestamp() + 60))
    transcripts[str(worktree)] = written
    assert watch.unprompted(job, worker, T0 + watch.UNPROMPTED) is False
    os.utime(written, (T0.timestamp() - 60, T0.timestamp() - 60))
    assert watch.unprompted(job, worker, T0 + watch.UNPROMPTED) is True
    assert watch.unprompted(job | {"route": None}, worker, T0 + watch.UNPROMPTED) is False


def test_a_sprite_a_reclaim_kept_is_asked_for_again_after_an_hour() -> None:
    kept = state.stamp(T0)
    assert (watch.kept_lately(kept, T0 + watch.KEPT_RETRY - timedelta(seconds=1)), watch.kept_lately(kept, T0 + watch.KEPT_RETRY), watch.kept_lately(None, T0)) == (True, False, False)


def test_the_watch_counts_as_stalled_past_two_minutes_on_a_rest_and_past_the_pass_cut_off_and_a_minute_on_a_stage() -> None:
    rest = watch.Pulse("rest", 1000.0, False)
    stage = watch.Pulse("lane_lines", 1000.0, True)
    assert not watch.stalled(rest, 1000.0 + watch.STALL_FOR.total_seconds())
    assert watch.stalled(rest, 1001.0 + watch.STALL_FOR.total_seconds())
    assert not watch.stalled(stage, 1000.0 + (watch.PASS_FOR + watch.SLOW_STAGE).total_seconds())
    assert watch.stalled(stage, 1001.0 + (watch.PASS_FOR + watch.SLOW_STAGE).total_seconds())


def test_a_stall_whose_cause_the_drives_rows_name_reads_its_why_who_and_next_from_them_and_starts_no_watchdog_agent_but_a_staging_env_or_release_starts_one(demo: str) -> None:
    data = state.load(demo)
    data["jobs"]["j1"] = state.new_job("o1", "builder", "build it", [], state.stamp()) | {"where": "remote", "dispatch": "ctx_1", "deaths": [{"dispatch": "ctx_1"}]}
    assert stall.cause(data, "staging:plat-usw2", {"members": ["staging:svc/plat-usw2@abcdef1"]}) is None
    assert stall.cause(data, "release:Forge-AI/monorepo#9@prod", {"members": []}) is None
    assert stall.cause(data, state.CUT_WAIT, {"members": [state.CUT_WAIT]})["NEXT"] == "nothing to run: its FREE line starts it"
    assert stall.cause(data, "j1", {"members": ["j1"]})["NEXT"] == "drive job resume j1 restarts it on a fresh Sprite"
    data["jobs"]["j1"]["deaths"] = []
    assert stall.cause(data, "j1", {"members": ["j1"]}) is None
    data["jobs"]["j1"]["waits"] = ["durable-agents:j1-1794673711565"]
    assert stall.cause(data, "j1", {"members": ["j1"]})["WHY"] == "waits on his card durable-agents:j1-1794673711565"
    data["jobs"]["j1"]["waits"] = ["release:api:Forge-AI/monorepo#9"]
    assert stall.cause(data, "j1", {"members": ["j1"]}) is None
    held = {"agent": {"pid": None, "out": "/x.md", "at": state.stamp()}, "said": None, "told": None}
    assert (stall.searching(held, state.now()), stall.starts({"t": held}, state.now())) == (False, (0, 0))


def test_a_waiting_or_pushed_return_reaches_the_coordinator_as_evidence_waking_nobody_unless_the_drive_added_a_clause(demo: str) -> None:
    data = state.load(demo)
    data["jobs"]["j1"] = state.new_job("o1", "builder", "build it", [], state.stamp()) | {"said": "WAITING release:api:Forge-AI/monorepo#9"}
    data["jobs"]["j2"] = state.new_job("o1", "builder", "build it", [], state.stamp()) | {"said": "PUSHED https://github.com/Forge-AI/monorepo/pull/9"}
    data["jobs"]["j3"] = state.new_job("o1", "builder", "build it", [], state.stamp()) | {"said": "READY https://github.com/Forge-AI/monorepo/pull/9"}
    assert watch.wakes("JOB j1 o1 WAITING release:api:Forge-AI/monorepo#9", data, None) is False
    assert watch.wakes("JOB j2 o1 PUSHED https://github.com/Forge-AI/monorepo/pull/9", data, None) is False
    assert watch.wakes("JOB j2 o1 PUSHED https://github.com/Forge-AI/monorepo/pull/9; its ASK was not kept", data, None) is True
    assert watch.wakes("JOB j3 o1 READY https://github.com/Forge-AI/monorepo/pull/9", data, None) is True


def test_a_relaunch_that_ended_with_no_next_worker_is_tried_again_after_five_minutes_three_times_in_all(monkeypatch: pytest.MonkeyPatch) -> None:
    moment = datetime(2026, 11, 15, 17, 13, tzinfo=UTC)
    monkeypatch.setattr(state, "alive", lambda pid: pid == 1)
    ran = {"why": "its worker exited", "at": state.stamp(moment), "relaunch": {"pid": 2, "at": state.stamp(moment)}}
    assert (watch.relaunchable(None, moment), watch.relaunchable({"why": "its worker exited"}, moment)) == (True, True)
    assert watch.relaunchable(ran, moment + timedelta(minutes=4)) is False
    assert watch.relaunchable(ran, moment + timedelta(minutes=5)) is True
    assert watch.relaunchable(ran | {"relaunch": {"pid": 1, "at": state.stamp(moment)}}, moment + timedelta(minutes=5)) is False
    assert watch.relaunchable(ran | {"relaunch": {"pid": 2, "at": state.stamp(moment), "tries": 3}}, moment + timedelta(hours=1)) is False


def test_the_drives_watch_retires_a_priority_seat_no_open_order_holds_and_keeps_one_an_open_order_holds(
    demo: str, monkeypatch: pytest.MonkeyPatch, seats_end_in_memory: list[tuple[str, str]]
) -> None:
    monkeypatch.setattr(launchd, "launchctl", lambda *args: None)
    with edit(demo) as data:
        data["subcoordinators"]["Priority o1"] = {"session": "seat-session-1", "cwd": "/tmp", "from": 0}
        data["subcoordinators"]["Priority o2"] = {"session": "seat-session-2", "cwd": "/tmp", "from": 0}
        data["launched"]["sub-coordinator Priority o1"] = "term_1"
        data["orders"]["o1"]["priority"] = None
        data["orders"]["o2"] = data["orders"]["o1"] | {"priority": "Priority o2", "state": "open"}
    said = watch.retired_seats(watcher(demo), T0)
    data = state.load(demo)
    assert (len(said), sorted(data["subcoordinators"]), "sub-coordinator Priority o1" in data["launched"]) == (1, ["Priority o2"], False)
    assert seats_end_in_memory == [(state.alias(demo, "coordinator", "Priority o1"), "seat-session-1")]


def test_the_relay_passes_a_project_again_only_once_the_drive_moved_or_its_idle_time_ran_out(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    project = watch.Watcher(demo, offset=0, project="Platform")
    monkeypatch.setattr(watch.state, "unchanged_since", lambda name, version: version == 7)
    assert watch.due_again(watch.Watcher(demo, offset=0))
    own = watch.Watcher(demo, offset=0, passed_at=watch.monotonic())
    assert not watch.due_again(own)
    assert watch.due_again(project)
    project.passed_version, project.passed_at = 7, watch.monotonic()
    assert not watch.due_again(project)
    project.passed_version = 6
    assert watch.due_again(project)
    project.passed_version, project.passed_at = 7, watch.monotonic() - watch.PROJECT_RELAY_IDLE
    assert watch.due_again(project)


def test_the_relay_reads_post_reports_at_its_first_pass_then_only_once_cci_watch_prints_one_or_its_idle_time_ran_out(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    class Running:
        def poll(self) -> None:
            return None

    heard = watch.Heard(Running(), threading.Event(), watch.monotonic())
    monkeypatch.setattr(watch, "HEARD_POSTS", {})
    monkeypatch.setattr(watch, "posts_heard", lambda name: heard)
    assert watch.posts_due(demo)
    assert not watch.posts_due(demo)
    heard.arrived.set()
    assert watch.posts_due(demo)
    assert not watch.posts_due(demo)
    heard.at = watch.monotonic() - watch.POSTS_IDLE
    assert watch.posts_due(demo)


def test_a_stage_that_raises_is_logged_and_skipped_and_its_third_failure_in_a_row_reaches_the_meta_session_once(demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    posts: list[tuple[str, str, list[str]]] = []
    monkeypatch.setattr(watch, "posted_record", lambda name, lane, kind, lines, checkout: posts.append((lane, kind, lines)))
    watching = watcher(demo)

    def broken(watching: watch.Watcher, moment: datetime) -> list[str]:
        raise KeyError("Forge-AI/monorepo#55820")

    stage = functools.partial(broken, watching, T0)
    said = [watch.contained(watching, stage, T0 + timedelta(minutes=minute)) for minute in range(4)]
    assert (said, [(lane, kind) for lane, kind, _ in posts]) == ([[], [], [], []], [("meta", "incident")])
    assert posts[0][2][0].startswith("WATCH stage broken failed 3 times in a row: ")
    assert capsys.readouterr().out.count("stage broken of the drive raised KeyError") == 4
    assert watch.contained(watching, functools.partial(lambda watching, moment: ["FREE j1"], watching, T0), T0) == ["FREE j1"]
    assert watching.failures["stage broken"] == 4


def test_a_pr_is_checked_on_the_first_pass_then_only_once_a_delivery_names_it_his_rulings_change_or_the_sweep_comes_round(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[list[str]] = []
    monkeypatch.setattr(served, "start", lambda name, keys: started.append(keys))
    monkeypatch.setattr(watch, "check_owed", lambda name, key, pr, data, unit, moment: True)
    monkeypatch.setattr(watch, "hears", lambda watcher, data, order: True)
    monkeypatch.setattr(watch, "objections", lambda watcher: None)
    with edit(demo) as data:
        data["prs"] = {KEY: state.new_pr(state.pr_url(KEY), "j1", "o1"), "Forge-AI/monorepo#43": state.new_pr(state.pr_url("Forge-AI/monorepo#43"), "j1", "o1")}
    watching = watcher(demo)
    watching.cards = NO_CARDS
    watch.checks(watching, T0)
    watch.checks(watching, T0 + timedelta(minutes=1))
    watching.checking.add(KEY)
    watch.checks(watching, T0 + timedelta(minutes=2))
    with edit(demo) as data:
        data["answers"]["a1"] = {}
    watch.checks(watching, T0 + timedelta(minutes=3))
    watch.checks(watching, T0 + timedelta(minutes=3) + watch.CHECK_SWEEP)
    assert started == [["Forge-AI/monorepo#42", "Forge-AI/monorepo#43"], [KEY], ["Forge-AI/monorepo#42", "Forge-AI/monorepo#43"], ["Forge-AI/monorepo#42", "Forge-AI/monorepo#43"]]


def test_a_usage_limit_hit_on_this_mac_is_told_him_once_as_a_milestone_whichever_watch_reads_it_and_one_that_reset_is_never_told(
    demo: str, monkeypatch: pytest.MonkeyPatch, ready_records: list[list[dict]]
) -> None:
    lined: list[str] = []
    monkeypatch.setattr(talk, "report_line", lambda name, seq: lined.append(seq) or f"POSTED {POSTED}")
    notice = "You've hit your weekly limit · resets Nov 17 at 11pm (America/Los_Angeles)"
    log = state.root() / state.USAGE_LIMIT_LOG
    rows = [{"limit": "weekly", "until": state.stamp(T0 - timedelta(hours=1)), "notice": notice, "at": state.stamp(T0 - timedelta(days=7))}, {"limit": "weekly", "until": state.stamp(T0 + timedelta(days=2)), "notice": notice, "at": state.stamp(T0)}]
    log.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert [watch.limit_lines(watcher(demo), T0) for _ in range(2)] == [[], []]
    words = f'POST Claude Code hit its weekly limit on this Mac: "{notice}". Sessions here dismiss its dialog and keep working on paid usage, and the watch restarts each agent and lane it stopped'
    assert [(record["text"], record["body"]) for records in ready_records for record in records] == [(limits.first_line(words), f"{words}\n\n{notice}\n")]
    assert lined == ["9"]


def test_a_coordinator_session_that_wrote_nothing_since_the_watchs_last_handoff_gets_no_record_until_it_writes_then_one_with_each_line_once(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    posted: list[tuple[str, str, list[str]]] = []
    monkeypatch.setattr(watch, "posted_record", lambda name, lane, kind, lines, checkout: lines and posted.append((lane, kind, lines)))
    transcript = watch.session_transcript(state.load(demo)["roles"]["coordinator"])
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("{}\n")
    written = datetime.fromtimestamp(transcript.stat().st_mtime).astimezone()
    watching = watcher(demo)
    watching.handed_at["root"] = written + timedelta(seconds=1)
    lines = ['ORPHAN o1 "ship the watch": no live job', 'ORPHAN o1 "ship the watch": no live job', 'ORPHAN o2 "fix the dashboard": no live job']
    assert (watch.sent(watching, "root", lines, T0), posted, watching.unsent["root"]) == ([], [], list(dict.fromkeys(lines)))
    assert (watch.sent(watching, "root", [], T0 + timedelta(minutes=1)), posted) == ([], [])
    os.utime(transcript, (written.timestamp() + 60, written.timestamp() + 60))
    assert watch.sent(watching, "root", [], T0 + timedelta(minutes=2)) == []
    assert [(lane, sent) for lane, _, sent in posted] == [("root", list(dict.fromkeys(lines)))]


def test_a_watchdog_answer_whose_labels_open_lists_below_them_keeps_each_whole_block_as_one_line() -> None:
    text = "WHY:\n- his Approve on #56961\n- its staging test\nWHO: j154\nNEXT:\n- drive job resume j154\nUNREAD: none"
    assert stall.answer(text) == {"WHY": "- his Approve on #56961 - its staging test", "WHO": "j154", "NEXT": "- drive job resume j154", "UNREAD": "none"}


def test_a_coordinator_hears_the_watch_at_most_once_every_ten_minutes_in_one_record_of_all_it_made_but_an_urgent_line_goes_at_once(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = posts(monkeypatch)
    watching = watcher(demo)
    first, second, third = 'ORPHAN o1 "ship": no job', 'ORPHAN o1 "test": no job', 'ORPHAN o1 "land": no job'
    assert watch.sent(watching, "root", [first], T0) == []
    assert watch.sent(watching, "root", [second], T0 + timedelta(minutes=1)) == []
    assert watch.sent(watching, "root", [third], T0 + timedelta(minutes=2)) == []
    assert posted(sent) == [("handoff", f"root@{demo}", first)]
    hold = f"TAP Hold {KEY} [o1 j1]"
    watch.sent(watching, "root", [hold], T0 + timedelta(minutes=3))
    assert [argv[argv.index("--text") + 1] for argv in sent[1:]] == ["3 lines: 2 ORPHAN, 1 TAP"]
    assert watch.sent(watching, "meta", [first], T0 + timedelta(minutes=3)) == []
    assert posted(sent)[-1] == ("handoff", f"meta@{demo}", first)


def test_a_held_text_his_answer_removes_a_premise_of_is_taken_off_the_jobs_it_touches_in_one_jev_request_and_his_words_stay(demo: str, jev: Jev) -> None:
    stale = {"seq": None, "words": "The staging test of Forge-AI/monorepo#42 also runs: the rollback on sandsql", "at": state.stamp(T0), "said": None, "request": "req-stale", "sender": None}
    kept = {"seq": None, "words": "From the coordinator: PR #7 merged", "at": state.stamp(T0), "said": None, "request": "req-kept", "sender": "coordinator"}
    his = {"seq": None, "words": 'His answer a1, verbatim: "ship everything except ssql"', "at": state.stamp(T0), "said": None, "request": "req-his", "sender": state.WATCH_SENDER}
    with edit(demo) as data:
        data["jobs"]["j1"]["undelivered"] = [stale, kept, his]
        data["answers"]["a1"] = {"words": "ship everything except ssql", "link": f"{CARD}9", "at": state.stamp(T0), "said": state.stamp(T0), "cards": {}, "jobs": {"j1": {"mark": "sent"}}}
    jev.chosen = ("t1", 0.95)
    watching = watcher(demo)
    assert watch.stale_texts(watching, T0 + timedelta(minutes=1)) == []
    assert [held["request"] for held in state.load(demo)["jobs"]["j1"]["undelivered"]] == ["req-kept", "req-his"]
    assert [list(asked["questions"]) for asked in jev.asked] == [["t1", "t2"]]
    assert watch.stale_texts(watching, T0 + timedelta(minutes=2)) == []
    assert len(jev.asked) == 1


def test_a_wait_on_a_stopped_job_reads_as_one_on_the_job_that_took_its_part_and_a_part_none_took_names_the_job_add_that_takes_it_never_a_resume(demo: str) -> None:
    with edit(demo) as data:
        part = next(iter(data["orders"]["o1"]["parts"]))
        data["orders"]["o1"]["jobs"] = ["j1", "j2", "j3"]
        data["jobs"]["j1"].update(kind="reader", state="waiting", waits=["j2"], prs=[], parts=[part])
        data["jobs"]["j2"] = job(state="stopped", parts=[part], where="remote", dispatch="ctx_1", deaths=[{"dispatch": "ctx_1"}])
        data["jobs"]["j3"] = job(state="waiting", parts=[part], waits=[KEY])
    data = state.load(demo)
    found = stall.things(data, stall.chains(data, NO_CARDS, False, T0), [])
    assert (list(found), found[KEY]["subjects"]) == ([KEY], ["j1", "j3"])
    data["jobs"]["j3"]["state"] = "stopped"
    found = stall.things(data, stall.chains(data, NO_CARDS, False, T0), [])
    assert list(found) == ["j2"]
    assert stall.cause(data, "j2", found["j2"]) == {
        "WHY": f"j2 stopped, and no job carries its part {part!r} of o1",
        "WHO": "the session holding its order",
        "NEXT": f"drive job add builder --order o1 --goal watch --part {shlex.quote(part)} takes it on",
    }


def test_each_new_alert_in_a_staging_envs_channel_goes_once_to_each_job_testing_on_what_it_serves_never_to_a_cut_item_not_yet_started_as_j8242_was(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with edit(demo) as data:
        data["jobs"]["j1"].update(state="waiting", waits=["j9"], claim={"worker": "j9", "attempt": 1, "at": state.stamp(T0)})
        data["jobs"]["j2"] = job(state="waiting", waits=["j9", "j4"], claim={"worker": "j9", "attempt": 1, "at": state.stamp(T0)})
        data["jobs"]["j3"] = job(state="done")
        data["jobs"]["j5"] = job(waits=[f"staging:api/plat-usw2-staging@{'a' * 40}"])
        data["slots"]["plat-usw2-staging"] = {"holder": {"job": "j1", "riders": [{"job": "j2"}, {"job": "j3"}], "work": "j9", "released": False}}
        data["slack"]["env_alerts"]["plat-usw2-staging"] = {"channel": "C0ALERTS", "seen": "1800000000.000000"}
    history = [
        {"ts": "1800000200.000000", "user_name": "Sentry", "from_claude": False, "from_user": None, "text": "[api] TypeError: boom\nmore", "permalink": "https://acme.slack.com/archives/C0ALERTS/p1800000200000000"},
        {"ts": "1800000100.000000", "user_name": "Yasyf", "from_claude": False, "from_user": True, "text": "looking", "permalink": "https://acme.slack.com/archives/C0ALERTS/p1800000100000000"},
        {"ts": "1799999999.000000", "user_name": "Sentry", "from_claude": False, "from_user": None, "text": "old", "permalink": "https://acme.slack.com/archives/C0ALERTS/p1799999999000000"},
    ]
    monkeypatch.setattr(watch.slack, "cc_slack", lambda *argv, **_: {"messages": history})
    sent: list[list[str]] = []
    monkeypatch.setattr(watch.reads, "run", lambda argv, checkout, env=None, stdin=None: sent.append(argv) or json.dumps({"seq": len(sent)}))
    watcher = watch.Watcher(demo, offset=state.events_end(demo))

    lines = watch.env_alert_lines(watcher, T0)
    assert lines == ["WATCH alert in plat-usw2-staging handed to j5, j1: https://acme.slack.com/archives/C0ALERTS/p1800000200000000"]
    assert [argv[argv.index("--id") + 1] for argv in sent] == ["alert-plat-usw2-staging-1800000200.000000-j5", "alert-plat-usw2-staging-1800000200.000000-j1"]
    assert [argv[argv.index("--to") + 1] for argv in sent] == ["j5@demo", "j1@demo"]
    assert sent[0][sent[0].index("--text") + 1].startswith("ALERT in plat-usw2-staging, which you are testing on: Sentry: [api] TypeError: boom https://acme.slack.com/archives/C0ALERTS/p1800000200000000")
    assert state.load(demo)["slack"]["env_alerts"]["plat-usw2-staging"]["seen"] == "1800000200.000000"
    assert watch.env_alert_lines(watcher, T0 + watch.ENV_ALERT_EVERY) == [] and len(sent) == 2


def test_a_restarted_watch_reads_whole_only_the_prs_whose_github_signature_moved_since_its_kept_read(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = serve(monkeypatch, {KEY: read(signature="first")})
    watch.github_lines(watcher(demo), T0)
    assert calls == [[KEY]]
    monkeypatch.setattr(github, "signatures", lambda keys: ({key: "first" for key in keys}, None))
    assert watch.github_lines(watcher(demo), T0 + timedelta(minutes=1)) == []
    assert calls == [[KEY]]
    monkeypatch.setattr(github, "signatures", lambda keys: ({key: "moved" for key in keys}, None))
    watch.github_lines(watcher(demo), T0 + timedelta(minutes=2))
    assert calls == [[KEY], [KEY]]


def test_a_part_its_orders_session_delivers_every_thirty_minutes_is_no_orphan_while_its_last_line_is_younger_and_is_once_it_is_overdue() -> None:
    moment = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)
    order = {"last_line": state.stamp(moment - timedelta(minutes=35))}
    assert watch.reported_gap(order, 30, moment) is None
    order["last_line"] = state.stamp(moment - timedelta(minutes=41))
    assert watch.reported_gap(order, 30, moment) == f"its line to him every 30 minutes is overdue: the order's last went out at {order['last_line']}; its session posts it now"
    assert watch.reported_gap({"last_line": None}, 30, moment).startswith("its line to him every 30 minutes is overdue: the order's last went out never")


@pytest.mark.parametrize(("stage", "project"), [("applet_lines", None), ("applet_lines", "Platform")])
def test_idle_relay_stages_materialize_no_drive(stage: str, project: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    from drive import applet

    monkeypatch.setattr(applet, "unannounced", lambda name, moment: [])
    monkeypatch.setattr(state, "load", lambda *args: pytest.fail("an idle relay stage materialized the drive"))
    monkeypatch.setattr(watch.dashboard, "link", lambda *args: pytest.fail("an idle relay stage resolved the dashboard"))
    assert getattr(watch, stage)(watch.Watcher("absent", offset=0, project=project), T0) == []


def test_a_relay_stage_that_raises_is_contained_so_post_lines_after_it_still_runs(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    for quiet in ("clocks_drawn", "codex_takes", "limit_lines", "applet_lines", "check_disks", "slot_lines"):
        monkeypatch.setattr(watch, quiet, lambda *args: [])
    monkeypatch.setattr(watch, "post_lines", lambda watcher, moment: ["POST started"])
    monkeypatch.setattr(watch, "failed", lambda *args: None)
    assert watch.relay_pass(watcher(demo), T0) == ["POST started"]


def test_a_pr_waiting_only_on_a_staging_run_its_tester_runs_gets_no_wake_until_that_tester_ends_without_a_pass_as_j5170s_window_was_interrupted(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    part = state.STAGING_TEST.format(number=42)
    with edit(demo) as data:
        data["orders"]["o2"] = order(jobs=["j5170"], parts={part: state.new_part()})
        data["jobs"]["j5170"] = job(order="o2", state="running", goal="test #42 on staging", parts=[part], builds_nothing=True)
        data["prs"][KEY] |= {"checks": "pass", "review": "approved", "waits": ["j5170", f"o2:{part}"]}
    approve(demo, KEY)
    woken: list[tuple[str, str | None]] = []
    monkeypatch.setattr(stage, "wake", lambda name, key, text, job_id=None, marks=(), at=None: woken.append((key, job_id)))
    monkeypatch.setattr(slack, "redraw_pr_cards", lambda url: None)
    watching = carded(demo)
    data = state.load(demo)
    now = stage.of(data, KEY, NO_CARDS)
    assert (now.name, now.why.count("its staging run by j5170"), stage.testing(data, KEY, NO_CARDS)) == ("waiting", 1, True)
    for minutes in (0, 61, 122):
        watch.stage_lines(watching, T0 + timedelta(minutes=minutes))
    assert woken == []
    with edit(demo) as data:
        data["jobs"]["j5170"].update(state="failed", said="BLOCKED staging froze for an incident")
    assert stage.testing(state.load(demo), KEY, NO_CARDS) is False


def test_a_retired_seat_communicator_is_told_its_seat_is_retired_and_how_to_reach_him(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: sent.append((lane, records)) or ["1"])
    monkeypatch.setattr(watch.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", ""))
    with edit(demo) as data:
        data["slack"]["channels"]["Priority o9"] = {"id": "C9", "name": "prio-o9"}
        data["communicators"]["Priority o9"] = {"session": "seat-session", "started": state.stamp()}
    watch.retire_communicator(demo, "Priority o9")
    assert sent == [(watch.LANE, [{"kind": "note", "to": [f"{state.communicator_lane('Priority o9')}@{demo}"], "text": watch.RETIRED_SEAT}])]
    assert "Priority o9" not in state.load(demo, ("communicators",))["communicators"]


def test_a_landers_failure_quotes_the_line_that_failed_never_the_steps_its_script_printed_around_it_as_734994_did() -> None:
    failed = "stack-enqueue failed: git ls-remote failed with exit 128: fatal: unable to access 'https://github.com/Forge-AI/monorepo/': Recv failure: Operation timed out"
    steps = [f"#56314 CHECK 758efba032 merge preflight 758efba032 with dev f9e87ddafa: {step}" for step in ("bun install --cwd infra --frozen-lockfile", "bun infra/generate.ts", "infra/ci/node_modules/.bin/tsc --noEmit -p infra/ci/tsconfig.json")]
    quoted = land.said("\n".join([*steps, failed, *steps[:1]]))
    outcome = f"{land.NO_ENQUEUE} --check on {KEY}, which exited 5 on a failure of its own: {quoted}"
    assert (quoted, land.blip(outcome), land.said("\n".join(steps))) == (failed, True, land.cut("; ".join(steps), land.SAID_CHARS))


def test_a_watch_text_reaches_a_job_once_until_its_next_return_and_a_possessive_job_on_its_waiting_names_no_wait(demo: str) -> None:
    with edit(demo) as data:
        data["orders"]["o1"]["jobs"] += ["j2", "j3"]
        data["jobs"]["j1"].update(kind="reader", prs=[], state="running")
        data["jobs"]["j2"] = job(kind="reader", goal="the tip")
        data["jobs"]["j3"] = job(kind="reader", goal="the bridge")
    stop = "STOP: stop every staging command on plat-usw2-staging at once, which j2 runs on now"
    with edit(demo) as data:
        assert state.hold_text(data["jobs"]["j1"], stop, state.WATCH_SENDER)
        data["jobs"]["j1"]["undelivered"] = []
        assert not state.hold_text(data["jobs"]["j1"], stop, state.WATCH_SENDER)
        assert state.hold_text(data["jobs"]["j1"], stop, state.HIM)
        data["jobs"]["j1"]["undelivered"] = []
    with edit(demo) as data:
        state.take_result(data, "j1", "WAITING j2: its tip, carrying j3's eb6227aa4f3c and j3’s seat lines")
    data = state.load(demo)
    assert [wait.partition("@")[0] for wait in data["jobs"]["j1"]["waits"] if state.LINE_JOB.match(wait)] == ["j2"]
    with edit(demo) as data:
        assert state.hold_text(data["jobs"]["j1"], stop, state.WATCH_SENDER)


def test_a_hotfix_cut_wakes_only_the_provers_of_the_prs_it_hotfixes_as_j8088_woke_on_hotfix_j8115() -> None:
    created = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    reach = {"Forge-AI/monorepo#57290": {"releases": ["cloudrunner-next"]}, "Forge-AI/monorepo#57616": {"releases": ["api"]}}
    hotfix = [{"hotfix": ("57616",), "without": ("plat-usw2-staging",)}]
    assert watch.cut_carries(hotfix, created, "everything", {"Forge-AI/monorepo#57290": "a2ee567"}, lambda commit, merges: True, reach) is False
    assert watch.cut_carries(hotfix, created, "everything", {"Forge-AI/monorepo#57616": "de40bfd"}, lambda commit, merges: True, reach) is True


def test_a_cut_wakes_only_the_provers_whose_prs_reach_a_target_it_ships_as_j8159_woke_on_desktop_cut_j8149() -> None:
    created = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    reach = {"Forge-AI/monorepo#57499": {"releases": ["llm-gateway"]}}
    merges = {"Forge-AI/monorepo#57499": "abc1234"}
    holds = lambda commit, shipped: bool(shipped)
    assert watch.cut_carries([{"targets": ("desktop",), "from": ("4ebbdc5",)}], created, "everything", merges, holds, reach) is False
    assert watch.cut_carries([{"targets": ("llm-gateway",), "from": ("4ebbdc5",)}], created, "everything", merges, holds, reach) is True
    assert watch.cut_carries([{"targets": ("everything",), "from": ("4ebbdc5",)}], created, "everything", merges, holds, reach) is True


def test_drive_job_prove_runs_on_a_prover_the_prove_queue_handed_out_its_wait_on_its_worker_ignored(demo: str) -> None:
    key = "Forge-AI/monorepo#57077"
    with edit(demo) as data:
        data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"state": "merged", "merged_sha": "aaa111"}
        data["jobs"]["j9"] = state.new_job("o1", "builder", f"prove {key} live", [], state.stamp()) | {"waits": ["j10"], "claim": {"worker": "j10", "attempt": 1, "at": state.stamp()}}
    assert install.handed_prs(state.load(demo), "j9") == [key]
    with edit(demo) as data:
        data["jobs"]["j9"]["waits"] = ["j11"]
    assert install.handed_prs(state.load(demo), "j9") == []


def test_his_undo_tap_owes_a_revert_on_the_job_whose_card_it_answered_and_each_job_carrying_the_answer_due_a_span_on_and_none_when_no_job_acted(demo: str) -> None:
    moment = state.parse_time("2026-11-09T05:07:00-08:00")
    card = "https://acme.slack.com/archives/D123/p1794800000000100"
    with edit(demo) as data:
        data["answers"]["a1"] = state.new_answer("roll it out", None, state.stamp(moment), {}, {})
        data["jobs"]["j2"] = job(answers=["a1"])
        opened = owing.reverted(data, "a1", card, moment + owing.SPAN_FLOOR, moment, ["j1"])
        reverts = [item for order in data["orders"].values() for item in order["owed"] if item["kind"] == owing.REVERT]
        assert ([item["id"] for item in reverts], [item["job"] for item in reverts], reverts[0]["due"], data["answers"]["a1"]["undone"]["link"]) == (opened, ["j1", "j2"], state.stamp(moment + owing.SPAN_FLOOR), card)
        assert owing.reverted(data, "demo:j9-1", card, moment + owing.SPAN_FLOOR, moment, []) == []


def test_a_prover_waits_on_no_staging_deploy_once_a_production_cluster_of_every_target_shipping_its_pr_serves_its_merge_and_never_on_a_hotfix_naming_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = "Forge-AI/monorepo#57634"
    with edit(demo) as data:
        data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"state": "merged", "merged_sha": "bbb222", "head": "bbb111", "base": "main"}
        data["jobs"]["j9"] = state.new_job("o1", "builder", f"prove {key} live", [], state.stamp()) | {"waits": [f"staging:dashboard/plat-usw2-staging@{'b' * 40}"]}
        data["jobs"]["j20"] = state.new_job("o1", "builder", "hotfix it", [], state.stamp()) | {"state": "done", "brief_flags": {"release": "tools/ci release hotfix 57634 --order 14e8441"}, "release_build": "https://buildkite.com/forge/release/builds/1"}
    monkeypatch.setattr(orca, "checkout", lambda name, cwd, wait=0: tmp_path)
    monkeypatch.setattr(github, "contains", lambda checkout: lambda build, merge, head, base: build == "c0ffee12")
    monkeypatch.setattr(watch.settled, "prod", lambda env, checkout: env.endswith("prod"))
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {key: {"releases": ["dashboard", "sand-next"]}})
    watching = watcher(demo)
    watching.live = [{"target": "desktop", "env": "plat-usw2-prod", "commit": "c0ffee12"}, {"target": "dashboard", "env": "plat-usw2-prod", "commit": "c0ffee12"}]
    assert (watch.shipped(watching, state.load(demo), {key}), watch.shipped_lines(watching, T0)) == ({}, [])
    watching.live.append({"target": "sand-next", "env": "plat-usw2-prod", "commit": "c0ffee12"})
    assert watch.shipped(watching, state.load(demo), {key}) == {key: "dashboard on plat-usw2-prod serves c0ffee12; sand-next on plat-usw2-prod serves c0ffee12"}
    [line] = watch.shipped_lines(watching, T0)
    assert "waits on no staging deploy: production already carries" in line
    assert list(state.load(demo)["dropped_waits"]["j9"]) == [f"staging:dashboard/plat-usw2-staging@{'b' * 40}"]

def test_a_gate_refusal_on_his_ask_owes_one_blocked_step_naming_its_findings_and_the_episode_ends_once_the_refusal_is_older_than_gate_holds(demo: str) -> None:
    moment = state.parse_time("2026-11-09T05:07:00-08:00")
    with edit(demo) as data:
        data["orders"]["o1"]["gated"] = {"release": {"at": state.stamp(moment), "findings": ["it names no answer of his"]}}
        assert owing.settle(data, moment)
        [item] = [item for item in data["orders"]["o1"]["owed"] if item["done_when"] == {"blocked": "o1"}]
        assert (data["orders"]["o1"]["blocked"], item["what"].endswith("the drive refused release: it names no answer of his")) == ("gate:release", True)
        assert not owing.settle(data, moment + timedelta(minutes=5))
        assert owing.settle(data, moment + owing.GATE_HOLDS + timedelta(minutes=1))
        assert data["orders"]["o1"]["blocked"] is None


def test_a_landing_loop_takes_its_watchers_by_the_turn_each_last_finished_so_a_restart_mid_round_reaches_the_projects_it_never_got_to_as_sandsqls_rows_waited_from_1135(demo: str) -> None:
    watchers = [watch.Watcher(demo, 0, project=project) for project in (None, "Iris", "Platform", "SandSQL", "Priority o2296")]
    assert [one.project for one in watch.by_turn(demo, watchers)] == [None, "Iris", "Platform", "SandSQL", "Priority o2296"]
    for project in (None, "Iris", "Platform"):
        watch.took_turn(demo, project)
    assert [one.project for one in watch.by_turn(demo, watchers)] == ["SandSQL", "Priority o2296", None, "Iris", "Platform"]
