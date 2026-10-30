from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from captain_hook.events import PreToolUseEvent
from captain_hook.testing.helpers import build_context, fixture_session
from captain_hook.types import Action

from fire import fire
from hooks import lane_landing
from hooks.compaction_handoff import CompactionState

SESSION = "900424b6-7393-480c-a26a-f1bd21da6e57"
ENQUEUE = "stack-enqueue 36023 --hold 30082"


def subagent(tmp_path: Path, agent_id: str, **meta: str) -> None:
    subagents = tmp_path / SESSION / "subagents"
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta))
    (subagents / f"agent-{agent_id}.jsonl").write_text("")


def run(tmp_path: Path, agent_id: str, command: str = ENQUEUE, *, active: bool = True) -> list[str]:
    transcript = tmp_path / f"{SESSION}.jsonl"
    transcript.write_text("")
    lane = tmp_path / SESSION / "subagents" / f"agent-{agent_id}.jsonl"
    raw = {"session_id": SESSION, "transcript_path": str(transcript), "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path), "agent_id": agent_id}
    state = tmp_path / "state"
    evt = PreToolUseEvent(_raw=raw, ctx=build_context(fixture_session([], path=lane), None, tmp_path, state))
    CompactionState(active=active).save(evt)
    return [result.message for result in fire(lane_landing, evt)]


@pytest.fixture(autouse=True)
def no_lane_env(monkeypatch):
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)


def test_a_named_lane_self_enqueue_is_refused_and_names_the_landing_desk(tmp_path):
    subagent(tmp_path, "a1f2e3d4", name="L28-ssql-session-target", agentType="long-running:lane-ship")

    assert run(tmp_path, "a1f2e3d4") == [lane_landing.REFUSAL.format(lane="L28-ssql-session-target", verb="stack-enqueue")]


def test_an_unnamed_helper_of_a_lane_is_refused_under_the_lane_name(tmp_path):
    subagent(tmp_path, "arelease-fix-1a2b", name="release-fix", agentType="long-running:lane-ship")
    subagent(tmp_path, "a3d0ef77", agentType="ship-pr", parentAgentId="arelease-fix-1a2b")

    assert run(tmp_path, "a3d0ef77", "gh pr edit 36039 --add-label merge-fast") == [
        lane_landing.REFUSAL.format(lane="release-fix", verb="gh pr edit --add-label")
    ]


def test_an_incident_lane_enqueue_is_refused_and_names_the_plain_squash(tmp_path):
    subagent(tmp_path, "a1f2e3d4", name="incident-api-500s", agentType="long-running:lane-ship")

    assert run(tmp_path, "a1f2e3d4", "stack-enqueue 36050 --fast") == [lane_landing.INCIDENT_REFUSAL.format(lane="incident-api-500s")]
    assert run(tmp_path, "a1f2e3d4", "gh pr merge 36050 --squash") == []


def test_an_unnamed_helper_of_the_landing_desk_enqueues(tmp_path):
    subagent(tmp_path, "alanding-desk-1a2b", name="landing-desk", agentType="long-running:lane")
    subagent(tmp_path, "a9c8b7a6", agentType="general-purpose", parentAgentId="alanding-desk-1a2b")

    assert run(tmp_path, "a9c8b7a6") == []


def test_a_subagent_outside_a_drive_enqueues(tmp_path):
    subagent(tmp_path, "a1f2e3d4", name="ship-it", agentType="general-purpose")

    assert run(tmp_path, "a1f2e3d4", active=False) == []


def git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


@pytest.mark.parametrize(("where", "blocked"), [("drive", True), ("other", False)])
def test_a_lane_merges_outside_the_drive_repo_but_never_inside_it(tmp_path, monkeypatch, where, blocked):
    drive_repo, other_repo = git_repo(tmp_path / "drive"), git_repo(tmp_path / "other")
    drives = tmp_path / "config" / "long-running" / "drives"
    drives.mkdir(parents=True)
    common = subprocess.run(["git", "-C", str(drive_repo), "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True, check=True).stdout.strip()
    (drives / "d1.json").write_text(json.dumps({"drive": "d1", "sessions": [SESSION], "repo": "Forge-AI/monorepo", "git_common_dir": common}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    subagent(tmp_path, "a1f2e3d4", name="tool-drive-hardening", agentType="long-running:lane-ship")
    cwd = drive_repo if where == "drive" else other_repo

    assert bool(run(tmp_path, "a1f2e3d4", f"cd {cwd} && gt merge")) is blocked


HEAD = "cccc333333dddd444444eeee555555ffff666666"


def drive_registry(tmp_path: Path, monkeypatch) -> None:
    drives = tmp_path / "config" / "long-running" / "drives"
    drives.mkdir(parents=True)
    state = tmp_path / "drive-state"
    state.mkdir()
    (state / "runner.json").write_text("{}")
    (drives / "d1.json").write_text(json.dumps({"drive": "d1", "sessions": [SESSION], "repo": "Forge-AI/monorepo", "git_common_dir": "/repo/.git", "state_dir": str(state)}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))


def check_pr_stub(monkeypatch, code: int, out: str, viewed: str = "41700") -> list[list[str]]:
    calls: list[list[str]] = []
    real = subprocess.run

    def run(argv, **kwargs):
        if argv[:3] == ["gh", "pr", "view"]:
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, f"{viewed} {HEAD}\n", "")
        if len(argv) > 2 and argv[2] == "check-pr":
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, code, out, "")
        return real(argv, **kwargs)

    monkeypatch.setattr(lane_landing.subprocess, "run", run)
    return calls


def merge_results(tmp_path: Path, command: str, agent_id: str | None = None) -> list:
    transcript = tmp_path / f"{SESSION}.jsonl"
    transcript.write_text("")
    raw = {"session_id": SESSION, "transcript_path": str(transcript), "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)}
    lane = transcript
    if agent_id:
        raw["agent_id"] = agent_id
        lane = tmp_path / SESSION / "subagents" / f"agent-{agent_id}.jsonl"
    evt = PreToolUseEvent(_raw=raw, ctx=build_context(fixture_session([], path=lane), None, tmp_path, tmp_path / "state"))
    return [result for result in fire(lane_landing, evt) if result.message]


def advise(tmp_path: Path, command: str, agent_id: str | None = None) -> list[str]:
    results = merge_results(tmp_path, command, agent_id)
    assert all(result.action != Action.block for result in results)
    return [result.message for result in results]


def test_a_root_merge_carrying_a_refusal_is_advised_and_never_blocked(tmp_path, monkeypatch):
    drive_registry(tmp_path, monkeypatch)
    calls = check_pr_stub(monkeypatch, 1, "RULINGS-UNCITED rule/abc1234\nNAME-HIT scope/progress go/x.go:12 progress bar\n")

    [advice] = advise(tmp_path, "gh pr merge 41700 -R Forge-AI/monorepo --squash")

    assert advice == lane_landing.RULINGS_ADVICE.format(causes="RULINGS-UNCITED rule/abc1234; NAME-HIT scope/progress go/x.go:12 progress bar", pr="41700", head=HEAD[:10])
    check = calls[-1]
    assert check[check.index("--pr") + 1] == "Forge-AI/monorepo#41700" and check[check.index("--head") + 1] == HEAD and "--incident" not in check


def test_an_incident_lane_merge_is_refused_while_one_of_the_three_incident_checks_fails(tmp_path, monkeypatch):
    drive_registry(tmp_path, monkeypatch)
    subagent(tmp_path, "a1f2e3d4", name="incident-api-500s", agentType="long-running:lane-ship")
    calls = check_pr_stub(monkeypatch, 1, "RESERVED-NO-ANSWER reserved/user-data\tUser data stays where it is.\nFINDER-MAJOR cccc333\n")

    [refusal] = merge_results(tmp_path, f"gh pr merge https://github.com/Forge-AI/monorepo/pull/41700 --squash --match-head-commit {HEAD}", "a1f2e3d4")

    assert refusal.action == Action.block
    assert refusal.message == lane_landing.INCIDENT_RULINGS_REFUSAL.format(pr="41700", head=HEAD[:10], lane="incident-api-500s", causes="RESERVED-NO-ANSWER reserved/user-data\tUser data stays where it is.")
    assert [call[:3] for call in calls if call[:3] == ["gh", "pr", "view"]] == [] and "--incident" in calls[-1]


def test_an_incident_lane_merge_of_a_head_check_pr_names_nothing_for_passes(tmp_path, monkeypatch):
    drive_registry(tmp_path, monkeypatch)
    subagent(tmp_path, "a1f2e3d4", name="incident-api-500s", agentType="long-running:lane-ship")
    check_pr_stub(monkeypatch, 0, "")

    assert merge_results(tmp_path, f"gh pr merge 41700 -R Forge-AI/monorepo --squash --match-head-commit {HEAD}", "a1f2e3d4") == []


def test_an_incident_lane_merge_passes_when_only_checks_outside_the_three_fire(tmp_path, monkeypatch):
    drive_registry(tmp_path, monkeypatch)
    subagent(tmp_path, "a1f2e3d4", name="incident-api-500s", agentType="long-running:lane-ship")
    check_pr_stub(monkeypatch, 1, "FINDER-MAJOR cccc333\n")

    assert advise(tmp_path, f"gh pr merge 41700 -R Forge-AI/monorepo --squash --match-head-commit {HEAD}", "a1f2e3d4") == []


@pytest.mark.parametrize(
    ("code", "out"),
    [(2, "usage: rulings.py: invalid choice: 'check-pr'\n"), (1, "SOMETHING-NEW rule/x\n"), (0, "BLOCKER gh pr view failed\n"), (0, ""), (0, "RULINGS-UNCITED rule/abc1234\n")],
)
def test_a_check_pr_run_the_hook_cannot_read_or_that_names_no_refusal_passes_silently(tmp_path, monkeypatch, code, out):
    drive_registry(tmp_path, monkeypatch)
    check_pr_stub(monkeypatch, code, out)

    assert advise(tmp_path, "gh pr merge 41700 -R Forge-AI/monorepo --squash") == []


def test_every_merge_in_a_command_is_advised_whichever_selector_names_it(tmp_path, monkeypatch):
    drive_registry(tmp_path, monkeypatch)
    calls = check_pr_stub(monkeypatch, 1, "RULINGS-UNCITED rule/abc1234\n", viewed="41711")

    [advice] = advise(tmp_path, f"gh pr merge 41700 -R Forge-AI/monorepo --squash --match-head-commit {HEAD} && gh pr merge fix/sand-read -R Forge-AI/monorepo --squash")

    assert advice.splitlines() == [
        lane_landing.RULINGS_ADVICE.format(causes="RULINGS-UNCITED rule/abc1234", pr=pr, head=HEAD[:10]) for pr in ("41700", "41711")
    ]
    [view] = [call for call in calls if call[:3] == ["gh", "pr", "view"]]
    assert view[3:6] == ["fix/sand-read", "--repo", "Forge-AI/monorepo"]
    assert [call[call.index("--pr") + 1] for call in calls if "check-pr" in call] == ["Forge-AI/monorepo#41700", "Forge-AI/monorepo#41711"]


def test_a_merge_of_the_current_branch_asks_gh_for_its_pr_in_the_working_directory(tmp_path, monkeypatch):
    drive_registry(tmp_path, monkeypatch)
    calls = check_pr_stub(monkeypatch, 1, "RULINGS-UNCITED rule/abc1234\n", viewed="41711")

    [advice] = advise(tmp_path, "gh pr merge --squash")

    assert advice == lane_landing.RULINGS_ADVICE.format(causes="RULINGS-UNCITED rule/abc1234", pr="41711", head=HEAD[:10])
    [view] = [call for call in calls if call[:3] == ["gh", "pr", "view"]]
    assert view[3:5] == ["--json", "number,headRefOid"]


FINDER_PENDING = "#43243 e949262e91 finder fail FINDER-PENDING #43243: no `finder:` review record names this head"


def gate_stub(monkeypatch, code: int, out: str) -> list[list[str]]:
    calls: list[list[str]] = []
    real = subprocess.run

    def run(argv, **kwargs):
        if len(argv) > 1 and str(argv[1]).endswith("mq.py"):
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, code, out, "")
        return real(argv, **kwargs)

    monkeypatch.setattr(lane_landing.subprocess, "run", run)
    return calls


@pytest.mark.parametrize(("code", "out", "warned"), [(1, f"{FINDER_PENDING}\n", True), (0, "", False), (2, "usage: mq.py: invalid choice: 'gate'\n", False)])
def test_a_landing_desk_hand_enqueue_is_warned_only_when_the_runners_gate_names_a_failing_head(tmp_path, monkeypatch, code, out, warned):
    drive_registry(tmp_path, monkeypatch)
    subagent(tmp_path, "alanding-desk-1a2b", name="landing-desk", agentType="long-running:lane")
    calls = gate_stub(monkeypatch, code, out)

    assert advise(tmp_path, "stack-enqueue 43246 43243 --partial", "alanding-desk-1a2b") == ([lane_landing.HAND_ENQUEUE_WARNING.format(lines=FINDER_PENDING)] if warned else [])
    [call] = calls
    assert call[2:] == ["--config", str(tmp_path / "drive-state" / "runner.json"), "gate", "43246", "43243", "--partial"]


TAP_REQUIRED = "#44705 cbc3f005e3 merge-mode pending TAP-REQUIRED rule/82317b0: it lands only on the owner's tap at this head, and a root go is no tap; no approval on the root's board names cbc3f005e3"


def test_a_hand_enqueue_of_a_head_the_runner_refuses_for_the_owners_tap_is_refused_with_the_runners_reason(tmp_path, monkeypatch):
    drive_registry(tmp_path, monkeypatch)
    subagent(tmp_path, "alanding-desk-1a2b", name="landing-desk", agentType="long-running:lane")
    gate_stub(monkeypatch, 1, f"{FINDER_PENDING}\n{TAP_REQUIRED}\n")

    [refusal] = merge_results(tmp_path, "stack-enqueue 44705 43243 --partial", "alanding-desk-1a2b")

    assert refusal.action == Action.block
    assert refusal.message == lane_landing.HAND_ENQUEUE_TAP_REFUSAL.format(lines=TAP_REQUIRED)


@pytest.mark.parametrize("command,verb", [("tools/ci trunk submit 36023", "ci trunk submit"), ("gh stack merge 36023", "gh stack merge")])
def test_native_queue_and_stack_merge_keep_the_landing_desk_as_only_enqueuer(tmp_path, command, verb):
    subagent(tmp_path, "a1f2e3d4", name="native-stack-fix", agentType="long-running:lane-ship")
    assert run(tmp_path, "a1f2e3d4", command) == [lane_landing.REFUSAL.format(lane="native-stack-fix", verb=verb)]


@pytest.mark.parametrize("verb", ["dequeue", "restart", "prioritize"])
def test_only_the_root_steers_trunk_directly(tmp_path, verb):
    subagent(tmp_path, "a1f2e3d4", name="native-stack-fix", agentType="long-running:lane-ship")
    messages = run(tmp_path, "a1f2e3d4", f"tools/ci trunk {verb} 36023")
    assert len(messages) == 1
    assert f"ci trunk {verb}" in messages[0]
