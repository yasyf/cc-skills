"""drive.py lane-status and worktree against real git worktrees, with cci and gh faked."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from long_running import drive
from long_running import lanes
from long_running import ledger
import pytest
from conftest import LEDGER, gateway

ROOT_SESSION = "900424b6-7393-480c-a26a-f1bd21da6e57"
LANE = "inc-liam-load-opus"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


class LaneShell(ledger.Shell):
    def __init__(self) -> None:
        self.records: list[dict] = []
        self.pulls: dict[str, list[dict]] = {}
        self.heads: dict[str, str] = {}
        self.posts: list[list[str]] = []

    def run(self, argv: list[str], stdin: str | None = None) -> str:
        argv = gateway(argv)
        if argv[:2] == ["cci", "tail"]:
            since = argv[argv.index("--since") + 1]
            kinds = {arg.removeprefix("--kind=") for arg in argv if arg.startswith("--kind=")}
            lanes = {argv[index + 1] for index, arg in enumerate(argv) if arg == "--lane"}
            return "".join(
                json.dumps(record) + "\n"
                for record in self.records
                if (not since.isdigit() or record["seq"] > int(since)) and (not kinds or record["kind"] in kinds) and (not lanes or record["lane"] in lanes)
            )
        if argv[:2] == ["cci", "post"]:
            self.posts.append(argv)
            return f"#{len(self.posts)}\n"
        if argv[:3] == ["gh", "api", "graphql"]:
            heads = {f"p{pr}": {"headRefName": self.heads[pr]} for pr in re.findall(r"p(\d+): pullRequest\(number:", argv[4])}
            pulls = {
                alias: {"nodes": [{"number": pull["number"], "state": pull["state"], "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": pull["statusCheckRollup"]}}}}]}} for pull in self.pulls.get(json.loads(branch), [])]}
                for alias, branch in re.findall(r'(b\d+): pullRequests\(headRefName: ("[^"]*")', argv[4])
            }
            return json.dumps({"data": {"repository": heads | pulls}})
        return super().run(argv, stdin)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(drive.DRIVE_ENV, raising=False)
    monkeypatch.setenv(drive.SESSION_ENV, ROOT_SESSION)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "dev", str(origin)], check=True)
    checkout = tmp_path / "monorepo"
    subprocess.run(["git", "init", "-q", "-b", "dev", str(checkout)], check=True)
    git(checkout, "remote", "add", "origin", str(origin))
    git(checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "dev: start")
    git(checkout, "push", "-q", "-u", "origin", "dev")
    git(checkout, "remote", "set-head", "origin", "dev")
    monkeypatch.chdir(checkout)
    assert drive.main(["start", "--ledger", LEDGER, "--cci-drive", "d1"]) == 0
    return home


def worktree(home: Path, name: str, *add: str) -> Path:
    path = home / ".claude" / "worktrees" / "monorepo" / name
    git(Path.cwd(), "worktree", "add", "-q", *add, str(path), "dev")
    return path


def commit(path: Path, message: str) -> None:
    git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", message)


def lane_status(shell: LaneShell, lane: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = drive.main(["lane-status", lane], shell)
    return code, capsys.readouterr().out


def test_a_detached_worktree_named_for_the_lane_reports_its_dirty_files_and_that_nothing_is_pushed(home, capsys):
    path = worktree(home, LANE, "--detach")
    (path / "chain.rs").write_text("fn copy_chain() {}\n")

    code, out = lane_status(LaneShell(), LANE, capsys)

    assert code == 0
    assert out.startswith(f"- monorepo: {path} (git worktree list): HEAD (no branch), 1 dirty file: chain.rs, 0 ahead of trunk, last commit 0 min ago ")
    assert out.rstrip().endswith("dev: start, not pushed, no PR")


def test_a_worktree_named_for_its_branch_is_found_from_the_lanes_worktree_record_and_its_pr(home, capsys):
    path = worktree(home, "inc-liam-fork-parent-grant", "-b", "inc-liam-fork-parent-grant")
    commit(path, "sandsql-server: 🐛 copy through the parent grant")
    git(path, "push", "-q", "-u", "origin", "inc-liam-fork-parent-grant")
    shell = LaneShell()
    shell.records = [
        {"seq": 1, "drive": "d1", "lane": LANE, "kind": "state", "text": f"WORKTREE {LANE} {path} inc-liam-fork-parent-grant", "fields": {"attrs": {"worktree": str(path)}}},
        {"seq": 2, "drive": "d1", "lane": LANE, "kind": "opened", "text": "opened #41461", "refs": {"prs": ["Forge-AI/monorepo#41461"]}},
    ]
    shell.heads["41461"] = "inc-liam-fork-parent-grant"
    shell.pulls["inc-liam-fork-parent-grant"] = [{"number": 41461, "state": "OPEN", "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]}]

    code, out = lane_status(shell, LANE, capsys)

    assert code == 0
    assert out.startswith(f"- monorepo: {path} (cci record, PR #41461 head): inc-liam-fork-parent-grant...origin/inc-liam-fork-parent-grant, 0 dirty files, 1 ahead of trunk, ")
    assert out.rstrip().endswith("sandsql-server: 🐛 copy through the parent grant, pushed, PR #41461 open CI red")


def test_a_commit_past_the_pushed_head_reads_as_not_pushed(home, capsys):
    path = worktree(home, LANE, "-b", LANE)
    git(path, "push", "-q", "-u", "origin", LANE)
    commit(path, "api: 🐛 the fix")

    _, out = lane_status(LaneShell(), LANE, capsys)

    assert f", HEAD not pushed, origin at {git(path, 'rev-parse', 'origin/dev')[:10]}, no PR" in out


def test_a_lane_worktree_in_another_pool_repo_is_found_and_the_runner_checkout_never_is(home, tmp_path, capsys):
    other = tmp_path / "cc-skills"
    subprocess.run(["git", "init", "-q", "-b", "main", str(other)], check=True)
    git(other, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "main: start")
    tooling = home / ".claude" / "worktrees" / "cc-skills" / "stop-hook-warn"
    git(other, "worktree", "add", "-q", "-b", "long-running/stop-hook-warn", str(tooling), "main")
    runner = worktree(home, "durable-agents-rebuild-runner", "--detach")
    shell = LaneShell()
    shell.records = [
        {"seq": 1, "drive": "d1", "lane": LANE, "kind": "state", "text": f"checked the queue from {runner}"},
        {"seq": 2, "drive": "d1", "lane": LANE, "kind": "opened", "text": "opened #1071", "refs": {"prs": ["yasyf/cc-skills#1071"]}},
    ]
    shell.heads["1071"] = "long-running/stop-hook-warn"
    drive_state = Path(drive.find(None, ROOT_SESSION)["state_dir"])
    drive_state.mkdir(parents=True, exist_ok=True)
    (drive_state / "runner.json").write_text(json.dumps({"landing": {"checkout": str(runner)}}))

    code, out = lane_status(shell, LANE, capsys)

    assert code == 0
    assert out.startswith(f"- cc-skills: {tooling} (PR #1071 head): long-running/stop-hook-warn")
    assert str(runner) not in out


def test_a_lane_with_no_worktree_says_so_and_exits_nonzero(home, capsys):
    worktree(home, "inc-other-fix", "--detach")

    assert lane_status(LaneShell(), LANE, capsys) == (1, f"no worktree found for lane {LANE}, and no in-process teammate of the drive's root sessions\n")


def test_worktree_posts_one_record_naming_the_lanes_worktree_and_branch(home, tmp_path):
    path = worktree(home, "inc-liam-fork-parent-grant", "-b", "inc-liam-fork-parent-grant")
    (path / "src").mkdir()
    shell = LaneShell()

    assert drive.main(["worktree", "--lane", LANE, "--path", str(path / "src")], shell) == 0
    elsewhere = tmp_path / "elsewhere"
    subprocess.run(["git", "init", "-q", str(elsewhere)], check=True)
    assert drive.main(["worktree", "--lane", LANE, "--path", str(elsewhere)], shell) == drive.OUTSIDE_REPO

    [post] = shell.posts
    flags = dict(zip(post[2::2], post[3::2], strict=False))
    assert flags["--id"] == f"worktree:{LANE}:{path}" and flags["--kind"] == "state" and flags["--drive"] == "d1"
    assert flags["--text"] == f"WORKTREE {LANE} {path} inc-liam-fork-parent-grant"
    assert [post[index + 1] for index, arg in enumerate(post) if arg == "--attr"] == [f"worktree={path}", "branch=inc-liam-fork-parent-grant"]


def pr_owed(shell: LaneShell, lane: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = drive.main(["pr-owed", lane], shell)
    return code, capsys.readouterr().out.strip()


def posted(seq: int, lane: str, kind: str, text: str, **fields) -> dict:
    return {"seq": seq, "drive": "d1", "lane": lane, "kind": kind, "text": text, **fields}


def test_pr_owed_names_a_mechanism_no_lane_has_answered(home, capsys):
    shell = LaneShell()
    shell.records = [posted(5, "inc-chats-load-fix-2", "mechanism", "SAND-3K partial mechanism: live 500 open loop"), posted(6, "inc-sync-fix", "opened", "OPENED pr#41470 [SAND-3Y] the sync toast")]

    assert pr_owed(shell, "inc-chats-load-fix-2", capsys) == (0, "owed: MECHANISM #5")


@pytest.mark.parametrize(
    "answer",
    [
        posted(7, "inc-sand3k-fix", "opened", "SAND-3K fix PR #41471 at 40cc7d1d277"),
        posted(7, "inc-other-fix", "head", "HEAD pr#41471", re=5),
        posted(7, "root", "go", "GO inc-sand3k-fix: ship the SAND3K fix"),
        posted(4, "inc-malformed-row-5a", "opened", "OPENED pr#41468", topic="SAND-3K"),
    ],
)
def test_pr_owed_reads_a_pr_from_any_lane_for_the_same_incident_as_the_answer(home, capsys, answer):
    shell = LaneShell()
    shell.records = [posted(5, "inc-chats-load-fix-2", "mechanism", "SAND-3K partial mechanism"), answer]

    assert pr_owed(shell, "inc-chats-load-fix-2", capsys) == (1, "not owed: no mechanism of inc-chats-load-fix-2 waits on a PR")


def test_pr_owed_never_holds_a_closeout_or_proof_lane(home, capsys):
    shell = LaneShell()
    shell.records = [posted(5, "incident-closeout", "mechanism", "SAND5A exact 50566ea9")]

    assert pr_owed(shell, "incident-closeout", capsys) == (1, "not owed: incident-closeout is an evidence, proof, closeout, desk, or audit lane")


def test_pr_owed_reads_a_root_ruling_addressed_to_the_lane_as_the_answer(home, capsys):
    shell = LaneShell()
    shell.records = [posted(5, "inc-llmgw-fix", "mechanism", "333417098 fired on OpenAI 499s"), posted(6, "root", "decide", "CLEAR, NOT counted", to=["inc-llmgw-fix", "alerts-desk"], re=5)]

    assert pr_owed(shell, "inc-llmgw-fix", capsys) == (1, "not owed: no mechanism of inc-llmgw-fix waits on a PR")


def task(task_id: str, created: str, title: str, spec: str = "") -> dict:
    return {"id": task_id, "created_at": created, "task_title": title, "display_name": title, "spec": spec}


def dispatch(dispatch_id: str, task_id: str, outcome: str) -> dict:
    return {"dispatchId": dispatch_id, "taskId": task_id, "projection": {"outcome": outcome}}


TASKS = [
    task("task_launch", "2026-10-27 09:00:00", "node-test", "Lane node-test: read the whole brief at /home/agent/briefs/node-test.md"),
    task("task_followup", "2026-10-28 08:10:44", "node-test follow-up", "Follow-up for lane node-test under this new Dispatch: read the whole file"),
    task("task_spec_only", "2026-10-28 09:00:00", "", "Follow-up for lane node-test under this new Dispatch: read the whole file"),
    task("task_sibling", "2026-10-28 18:00:00", "node-test-2", "Lane node-test-2: read the whole brief at /home/agent/briefs/node-test-2.md"),
    task("task_prefixed", "2026-10-28 18:30:00", "dagger-node-test", "Lane dagger-node-test: read the whole brief at /home/agent/briefs/dagger-node-test.md"),
]


def test_an_orca_task_names_a_lane_by_its_title_its_follow_up_title_or_its_spec_never_a_sibling_lane():
    assert [entry["id"] for entry in TASKS if "node-test" in lanes.task_lanes(entry)] == ["task_launch", "task_followup", "task_spec_only"]


def test_a_lane_reports_every_in_progress_dispatch_of_its_tasks_newest_first():
    workers = [
        dispatch("ctx_launch", "task_launch", "succeeded"),
        dispatch("ctx_followup", "task_followup", "in_progress"),
        dispatch("ctx_spec_only", "task_spec_only", "in_progress"),
        dispatch("ctx_sibling", "task_sibling", "in_progress"),
    ]
    assert lanes.lane_dispatches("node-test", TASKS, workers) == ["ctx_spec_only", "ctx_followup"]


def test_a_lane_with_no_dispatch_in_progress_reports_its_newest_tasks_dispatch():
    workers = [dispatch("ctx_launch", "task_launch", "succeeded"), dispatch("ctx_followup", "task_followup", "failed"), dispatch("ctx_prefixed", "task_prefixed", "in_progress")]
    assert lanes.lane_dispatches("node-test", TASKS, workers) == ["ctx_followup"]


def test_a_dispatch_id_is_its_own_lane_and_an_unknown_lane_reports_nothing():
    assert lanes.lane_dispatches("ctx_4a4f4a384983", [], []) == ["ctx_4a4f4a384983"]
    assert lanes.lane_dispatches("no-such-lane", TASKS, [dispatch("ctx_launch", "task_launch", "in_progress")]) == []


TEAM = {"members": [
    {"agentId": "team-lead@session-d0f40a25", "name": "team-lead"},
    {"agentId": "scale-out@session-d0f40a25", "name": "scale-out"},
    {"agentId": "scale-out-2@session-d0f40a25", "name": "scale-out-2"},
]}


def test_a_teammate_is_named_by_its_name_or_its_agent_id_never_by_a_prefix():
    assert [member["name"] for member in lanes.team_members("scale-out", TEAM)] == ["scale-out"]
    assert lanes.team_members("scale", TEAM) == []


NOW = datetime(2026, 10, 28, 23, 45, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("last", "state"),
    [
        ({"type": "assistant", "timestamp": "2026-10-27T09:36:26Z", "message": {"stop_reason": "end_turn"}}, "idle"),
        ({"type": "assistant", "timestamp": "2026-10-28T23:42:12Z", "message": {"stop_reason": "tool_use"}}, "running"),
        ({"type": "user", "timestamp": "2026-10-28T23:40:00Z", "message": {"content": [{"type": "tool_result"}]}}, "running"),
        ({"type": "user", "timestamp": "2026-10-27T07:12:19Z", "message": {"content": [{"type": "tool_result"}]}}, "stalled mid-turn"),
        (None, "state unknown"),
    ],
)
def test_a_teammate_whose_last_turn_ended_is_idle_and_one_mid_turn_runs_until_an_hour_passes(last, state):
    assert lanes.turn_state(last, NOW) == state
