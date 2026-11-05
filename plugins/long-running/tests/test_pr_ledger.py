from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from captain_hook.context import HookContext
from captain_hook.events import PostToolUseEvent
from captain_hook.snapshots.client import EvidenceIncomplete
from captain_hook.testing.fixtures import T
from captain_hook.testing.helpers import build_context, fixture_line

from hooks.call_dir import UnknownDirectory, known_path, located, run_dir, unknown_dir
from hooks.compaction_handoff import drive_entry
from hooks.refusal_kinds_need_owner_answer import lane_brief
from hooks.tests.ledger_fixtures import FIXTURES
from hooks.pr_ledger import OpenedPr, lane_name, opened_prs, opener_cwd, response_text, settled_prs

SESSION = "900424b6-7393-480c-a26a-f1bd21da6e57"


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("ccx-ship-gt.txt", [OpenedPr("28534", "Forge-AI/monorepo", "1caa098d30a8", "yasyf/v3-deploy-experience/since", "yasyf/v3-deploy-experience/errors", "ci: ✨ tools/deploy --since deploys exactly the stacks a landed range reaches")]),
        (
            "ccx-stack-submit.txt",
            [
                OpenedPr("28557", None, "03afab1d4802", "yasyf/v3-deploy-experience/tenant-branch-lists", "dev", "api: 🐛 the tenant routes admit any deploy selection in the branch, lists and hashes included"),
                OpenedPr("28558", None, "af664caed71b", "yasyf/v3-deploy-experience/since", "yasyf/v3-deploy-experience/tenant-branch-lists", "ci: ✨ tools/deploy --since deploys exactly the stacks a landed range reaches"),
            ],
        ),
        ("ccx-ship-git.txt", [OpenedPr("123", "yasyf/cc-skills")]),
        ("gh-pr-create.txt", [OpenedPr("28612", "Forge-AI/monorepo")]),
        ("gt-submit.txt", [OpenedPr("28560", "Forge-AI/monorepo"), OpenedPr("28562", "Forge-AI/monorepo")]),
    ],
)
def test_opened_prs_reads_only_the_prs_the_command_submitted(fixture, expected):
    assert opened_prs((FIXTURES / fixture).read_text()) == expected


def test_a_pr_url_the_command_did_not_submit_records_nothing():
    output = 'committed 1caa098d30 "ci: ✨ revert https://github.com/Forge-AI/monorepo/pull/7" · pushed nothing'
    assert opened_prs(output) == []


def test_a_pr_spec_carries_its_repo_and_head():
    assert [pr.spec for pr in opened_prs((FIXTURES / "ccx-ship-gt.txt").read_text())] == ["Forge-AI/monorepo#28534=1caa098d30a8"]


def test_response_text_reads_a_bash_result_and_its_stderr():
    assert response_text({"stdout": "out", "stderr": "err", "interrupted": False}) == "out\nerr"


def event(tmp_path: Path, agent_id: str | None = None, command: str = "ccx vcs ship") -> PostToolUseEvent:
    transcript = tmp_path / f"{SESSION}.jsonl"
    transcript.write_text("")
    lane = tmp_path / SESSION / "subagents" / f"agent-{agent_id}.jsonl" if agent_id else transcript
    lane.parent.mkdir(parents=True, exist_ok=True)
    lane.touch()
    raw = {"session_id": SESSION, "transcript_path": str(transcript), "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)}
    return PostToolUseEvent(_raw=raw | ({"agent_id": agent_id} if agent_id else {}), ctx=build_context(None, lane, tmp_path, tmp_path))


def test_a_named_subagent_records_under_its_own_name(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    meta = tmp_path / SESSION / "subagents" / "agent-adeploy-1a2b.meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"name": "deploy-experience", "agentType": "deploy-experience"}))
    meta.with_name("agent-adeploy-1a2b.jsonl").write_text("")

    assert lane_name(event(tmp_path, "adeploy-1a2b")) == "deploy-experience"


def test_an_unnamed_subagent_records_under_its_named_parent(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    subagents = tmp_path / SESSION / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-arelease-fix-1a2b.meta.json").write_text(json.dumps({"name": "release-fix", "agentType": "long-running:lane-ship"}))
    (subagents / "agent-a3d0ef77.meta.json").write_text(json.dumps({"agentType": "ship-pr", "parentAgentId": "arelease-fix-1a2b"}))
    (subagents / "agent-a3d0ef77.jsonl").write_text("")

    assert lane_name(event(tmp_path, "a3d0ef77")) == "release-fix"


def test_a_named_worker_records_under_the_lane_that_spawned_it(tmp_path, monkeypatch):
    """p8a-step7 #201056: a worker's own name claimed #45465 before its lane registered it, and route and stall records for the row went nowhere."""
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    subagents = tmp_path / SESSION / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-ap8a-step7-1a2b3c4d5e6f7a8b.meta.json").write_text(json.dumps({"name": "p8a-step7", "agentType": "long-running:lane-ship"}))
    (subagents / "agent-ap8a7-summary-rows-9f8e7d6c5b4a3f2e.meta.json").write_text(
        json.dumps({"name": "p8a7-summary-rows", "agentType": "general-purpose", "parentAgentId": "ap8a-step7-1a2b3c4d5e6f7a8b"})
    )

    assert lane_name(event(tmp_path, "ap8a7-summary-rows-9f8e7d6c5b4a3f2e")) == "p8a-step7"


def test_a_named_worker_in_an_orca_lane_session_records_under_the_session_lane(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_LONG_RUNNING_LANE", "p8a-step7")
    meta = tmp_path / SESSION / "subagents" / "agent-ap8a7-c40-9f8e7d6c5b4a3f2e.meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"name": "p8a7-c40", "agentType": "general-purpose"}))

    assert lane_name(event(tmp_path, "ap8a7-c40-9f8e7d6c5b4a3f2e")) == "p8a-step7"


def test_an_in_process_teammate_of_an_orca_lane_records_its_ship_under_its_own_lane(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_LONG_RUNNING_LANE", "meta-desk-3")
    subagents = tmp_path / SESSION / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-ahost-tap-fold-30fc5ffd702ffbf8.meta.json").write_text(
        json.dumps({"name": "host-tap-fold", "agentType": "host-tap-fold", "taskKind": "in_process_teammate", "spawnedAgentType": "long-running:lane-ship"})
    )
    (subagents / "agent-a3d0ef77.meta.json").write_text(json.dumps({"agentType": "ship-pr", "parentAgentId": "ahost-tap-fold-30fc5ffd702ffbf8"}))
    (subagents / "agent-a3d0ef77.jsonl").write_text("")

    assert [lane_name(event(tmp_path, agent)) for agent in ("ahost-tap-fold-30fc5ffd702ffbf8", "a3d0ef77")] == ["host-tap-fold", "host-tap-fold"]


IN_PROCESS_LANE = "ap8a-step5-5-e7b153ce1ea9184c"


def in_process_lane_with_unloadable_transcript(tmp_path, monkeypatch) -> PostToolUseEvent:
    lanes = tmp_path / SESSION / "subagents"
    lanes.mkdir(parents=True)
    (lanes / f"agent-{IN_PROCESS_LANE}.meta.json").write_text(
        json.dumps({"name": "p8a-step5-5", "agentType": "p8a-step5-5", "taskKind": "in_process_teammate", "spawnedAgentType": "long-running:lane-ship"})
    )
    evt = event(tmp_path, IN_PROCESS_LANE)

    def deadline(self):
        raise EvidenceIncomplete("deadline", "foreground transcript deadline exhausted")

    monkeypatch.setattr(HookContext, "t", property(deadline))
    return evt


def test_an_in_process_lane_is_named_without_loading_its_transcript(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)

    assert lane_name(in_process_lane_with_unloadable_transcript(tmp_path, monkeypatch)) == "p8a-step5-5"


def test_a_pr_an_in_process_lane_shipped_registers_under_that_lane_without_a_warning(tmp_path, monkeypatch):
    from hooks import pr_ledger

    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    calls = []

    def record(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "registered p8a-step5-5 #28534", "")

    monkeypatch.setattr(pr_ledger.subprocess, "run", record)
    monkeypatch.setattr(pr_ledger, "post_row_records", lambda evt, prs: [])
    evt = in_process_lane_with_unloadable_transcript(tmp_path, monkeypatch)
    evt._raw["tool_response"] = (FIXTURES / "ccx-ship-gt.txt").read_text()

    assert pr_ledger.record_opened_prs(evt) is None
    assert calls[0][calls[0].index("--lane") + 1] == "p8a-step5-5"


def test_an_unnamed_session_falls_back_to_its_lane_env_then_the_root_lane(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    assert lane_name(event(tmp_path, "aship-pr-9f9f")) == "root"

    monkeypatch.setenv("CLAUDE_LONG_RUNNING_LANE", "orca-desk")
    assert lane_name(event(tmp_path)) == "orca-desk"


def test_the_unnamed_note_names_a_ledger_script_that_has_the_reassign_verb():
    from hooks import pr_ledger
    from hooks.compaction_handoff import script

    ledger = script("ledger.py")
    assert f"{ledger} reassign " in pr_ledger.UNNAMED
    done = subprocess.run([pr_ledger.sys.executable, str(ledger), "reassign", "--help"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "--lane" in done.stdout


@pytest.mark.parametrize(("agent_id", "told"), [("aship-pr-9f9f", True), (None, False)], ids=["unnamed-subagent", "root-session"])
def test_a_pr_no_named_lane_opened_registers_under_root_and_says_so_only_to_a_subagent(tmp_path, monkeypatch, agent_id, told):
    from hooks import pr_ledger

    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    calls = []

    def record(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "registered root #28534", "")

    monkeypatch.setattr(pr_ledger.subprocess, "run", record)
    monkeypatch.setattr(pr_ledger, "post_row_records", lambda evt, prs: [])
    evt = event(tmp_path, agent_id)
    evt._raw["tool_response"] = (FIXTURES / "ccx-ship-gt.txt").read_text()

    result = pr_ledger.record_opened_prs(evt)

    assert calls[0][calls[0].index("--lane") + 1] == "root"
    assert (result.message if result else None) == (pr_ledger.UNNAMED if told else None)


def test_the_ship_runs_in_the_directory_its_command_cd_into(tmp_path):
    (tmp_path / "cc-skills").mkdir()
    assert opener_cwd(event(tmp_path, command=f"cd {tmp_path}/cc-skills && ccx vcs ship -m x")) == tmp_path / "cc-skills"
    assert opener_cwd(event(tmp_path, command="gt submit --stack")) == tmp_path


def test_a_ship_after_a_cd_the_hook_cannot_follow_has_no_directory(tmp_path):
    assert opener_cwd(event(tmp_path, command=". /env.sh; cd $A; ccx vcs ship -m x")) is None


def test_every_hook_reads_an_unknown_directory_as_unknown_never_the_session(tmp_path):
    (tmp_path / "body.md").write_text("x")
    evt = event(tmp_path, command=f". /env.sh; cd $A; gh pr edit 1 -F body.md; gh pr edit 2 -F {tmp_path}/body.md")
    relative, absolute = evt.cmd.calls("gh")

    assert unknown_dir(evt, relative)
    assert known_path(evt, relative, "body.md") is None
    assert known_path(evt, absolute, f"{tmp_path}/body.md") == tmp_path / "body.md"
    with pytest.raises(UnknownDirectory, match=r"`gh` runs after a `cd` this hook cannot follow"):
        run_dir(evt, relative)
    with pytest.raises(UnknownDirectory):
        located(evt, relative, "body.md")


def test_a_followed_cd_reads_its_own_directory_and_no_cd_reads_the_session(tmp_path):
    (tmp_path / "lane").mkdir()
    evt = event(tmp_path, command=f"gh pr edit 1 -F a.md; cd {tmp_path}/lane; gh pr edit 2 -F a.md")
    session, moved = evt.cmd.calls("gh")

    assert not unknown_dir(evt, session)
    assert run_dir(evt, session) == str(tmp_path)
    assert located(evt, moved, "a.md") == tmp_path / "lane" / "a.md"


def test_a_failed_fork_names_the_unrecorded_prs_instead_of_faulting(tmp_path, monkeypatch):
    from hooks import pr_ledger

    def exhausted(*args, **kwargs):
        raise BlockingIOError(35, "Resource temporarily unavailable")

    monkeypatch.setattr(pr_ledger.subprocess, "run", exhausted)
    evt = event(tmp_path)
    evt._raw["tool_response"] = (FIXTURES / "ccx-ship-gt.txt").read_text()

    result = pr_ledger.record_opened_prs(evt)

    assert result is not None
    assert result.message.startswith("The opened PRs were not recorded in the drive ledger: [Errno 35] Resource temporarily unavailable. Rerun `")
    assert f"drive.py record --session {SESSION} " in result.message


def test_a_record_past_its_timeout_finishes_detached_and_tells_the_lane_nothing(tmp_path, monkeypatch):
    from hooks import pr_ledger

    def stalled(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    spawned = []
    monkeypatch.setattr(pr_ledger.subprocess, "run", stalled)
    monkeypatch.setattr(pr_ledger.subprocess, "Popen", lambda argv, **kwargs: spawned.append((argv, kwargs)))
    monkeypatch.setattr(pr_ledger, "DETACHED_LOG", tmp_path / "logs" / "detached.log")
    evt = event(tmp_path)
    prs = opened_prs((FIXTURES / "ccx-ship-gt.txt").read_text())

    assert pr_ledger.ledger_note(evt, prs) is None

    assert len(spawned) == 1
    argv, kwargs = spawned[0]
    assert argv[1].endswith("drive.py") and argv[2] == "record"
    assert kwargs["start_new_session"] is True


def test_a_record_that_cannot_detach_still_names_the_unrecorded_prs(tmp_path, monkeypatch):
    from hooks import pr_ledger

    def stalled(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    def exhausted(*args, **kwargs):
        raise BlockingIOError(35, "Resource temporarily unavailable")

    monkeypatch.setattr(pr_ledger.subprocess, "run", stalled)
    monkeypatch.setattr(pr_ledger.subprocess, "Popen", exhausted)
    monkeypatch.setattr(pr_ledger, "DETACHED_LOG", tmp_path / "detached.log")
    prs = opened_prs((FIXTURES / "ccx-ship-gt.txt").read_text())

    assert pr_ledger.ledger_note(event(tmp_path), prs).startswith("The opened PRs were not recorded in the drive ledger: [Errno 35]")


@pytest.mark.parametrize(("lane", "expected"), [("merge-walker-r2", "no drive claims session"), (None, None)], ids=["lane", "no-lane"])
def test_a_lane_in_no_drive_is_told_its_pr_was_not_recorded(tmp_path, monkeypatch, lane, expected):
    from hooks import pr_ledger

    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    if lane:
        monkeypatch.setenv("CLAUDE_LONG_RUNNING_LANE", lane)
    monkeypatch.setattr(
        pr_ledger.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", "no drive claims session x (lane y)"),
    )
    monkeypatch.setattr(pr_ledger, "post_row_records", lambda evt, prs: [])
    evt = event(tmp_path)
    evt._raw["tool_response"] = (FIXTURES / "ccx-ship-gt.txt").read_text()

    result = pr_ledger.record_opened_prs(evt)

    assert (result.message if result else None) == (expected and "no drive claims session x (lane y)")


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("cd /x && ~/.local/bin/cci post --drive release-v3 --lane l --kind landed --pr 31390 --to main --text 'LANDED #31390'", ["31390"]),
        ("cci post --lane l --kind=landed --pr 31375,31392 --pr=31376 --text x", ["31375", "31392", "31376"]),
        ("cci post --lane l --kind opened --pr 31390 --text x", []),
        ("gh pr close 31159 --comment superseded", ["31159"]),
        ("gh pr close https://github.com/Forge-AI/monorepo/pull/31054", ["31054"]),
        ("gh pr close yasyf/some-branch", []),
    ],
    ids=["cci-landed", "cci-landed-lists", "cci-opened", "gh-close-number", "gh-close-url", "gh-close-branch"],
)
def test_settled_prs_reads_the_prs_a_landing_record_or_a_close_names(tmp_path, command, expected):
    assert settled_prs(event(tmp_path, command=command)) == expected


def test_a_landing_the_drive_cannot_settle_names_the_prs(tmp_path, monkeypatch):
    from hooks import pr_ledger

    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    monkeypatch.setattr(pr_ledger.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "", "forge unreachable"))

    result = pr_ledger.settle_landed_prs(event(tmp_path, command="cci post --lane l --kind landed --pr 31390 --text x"))

    assert result is not None
    assert result.message.startswith("The landed or closed PRs #31390 were not settled in the drive ledger: forge unreachable. Rerun `")
    assert f"drive.py landed --session {SESSION} --pr 31390`" in result.message


def test_a_landing_that_times_out_names_the_command_that_settles_it(tmp_path, monkeypatch):
    from hooks import pr_ledger

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, pr_ledger.RECORD_TIMEOUT_SECONDS)

    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    monkeypatch.setattr(pr_ledger.subprocess, "run", slow)

    result = pr_ledger.settle_landed_prs(event(tmp_path, command="gh pr close 31159"))

    assert result is not None
    assert f"drive.py timed out after {pr_ledger.RECORD_TIMEOUT_SECONDS}s" in result.message
    assert result.message.endswith(f"drive.py landed --session {SESSION} --pr 31159`; it reads the ledger id and checkout from the drive registry.")


SHA = "1caa098d30a81caa098d30a81caa098d30a81caa"
ROW = json.dumps({"seq": 1, "kind": "opened", "fields": {"attrs": {"repo": "Forge-AI/monorepo", "head": "abc1234"}}})
HAND_POSTED = json.dumps({"seq": 2, "kind": "opened", "fields": {}})
GT_SHIP = (FIXTURES / "ccx-ship-gt.txt").read_text()
GT_REF = "Forge-AI/monorepo#28534"
VIEW = json.dumps({"url": "https://github.com/Forge-AI/monorepo/pull/28534", "title": "x: y", "headRefName": "lane/fix", "baseRefName": "dev", "headRefOid": SHA, "body": "from github", "state": "OPEN"})


@pytest.fixture
def tools(monkeypatch):
    from hooks import pr_ledger

    monkeypatch.setenv("CLAUDE_LONG_RUNNING_DRIVE", "d")
    monkeypatch.setenv("CLAUDE_LONG_RUNNING_LANE", "lane-a")
    calls = []
    answers = {("git", "rev-parse"): SHA, ("git", "symbolic-ref"): "origin/dev", ("cci", "grep"): "", ("cci", "post"): "#9", ("gh", "pr"): VIEW}

    def run(argv, **kwargs):
        calls.append((argv, kwargs.get("input")))
        answer = answers.get(tuple(argv[:2]), "")
        return subprocess.CompletedProcess(argv, *answer) if isinstance(answer, tuple) else subprocess.CompletedProcess(argv, 0, answer, "")

    monkeypatch.setattr(pr_ledger.subprocess, "run", run)
    monkeypatch.setattr(pr_ledger, "ledger_note", lambda evt, prs: None)
    return calls, answers


def posted(calls: list) -> list[tuple[dict[str, list[str]], dict | None]]:
    records = []
    for argv, stdin in calls:
        if argv[:2] != ["cci", "post"]:
            continue
        flags: dict[str, list[str]] = {}
        for name, value in zip(argv[2:], argv[3:]):
            if name.startswith("--") and not value.startswith("--"):
                flags.setdefault(name, []).append(value)
        records.append((flags, json.loads(stdin) if stdin else None))
    return records


def viewed(calls: list) -> list:
    return [argv for argv, _ in calls if argv[:3] == ["gh", "pr", "view"]]


def shipped(tmp_path: Path, output: str = GT_SHIP, command: str = "ccx vcs ship -m x") -> PostToolUseEvent:
    evt = event(tmp_path, command=command)
    evt._raw["tool_response"] = output
    return evt


def test_a_new_pr_posts_opened_with_the_named_file_as_its_whole_body_and_reads_no_github(tmp_path, tools):
    from hooks import pr_ledger

    calls, _ = tools
    body = "## Summary\n" + "word " * 200
    (tmp_path / "body.md").write_text(body)

    assert pr_ledger.record_opened_prs(shipped(tmp_path, command="ccx vcs ship -m x --pr-body-file body.md")) is None

    assert viewed(calls) == []
    [(flags, carried)] = posted(calls)
    assert flags["--kind"] == ["opened"]
    assert flags["--pr"] == [GT_REF]
    assert carried == {"body": body}
    assert flags["--attr"] == [
        "repo=Forge-AI/monorepo",
        "branch=yasyf/v3-deploy-experience/since",
        "base=yasyf/v3-deploy-experience/errors",
        f"head={SHA}",
        "parent=yasyf/v3-deploy-experience/errors",
        "title=ci: ✨ tools/deploy --since deploys exactly the stacks a landed range reaches",
    ]
    assert len(flags["--text"][0]) <= 400


def test_a_new_pr_with_no_named_file_reads_its_body_once_from_github(tmp_path, tools):
    from hooks import pr_ledger

    calls, _ = tools

    assert pr_ledger.record_opened_prs(shipped(tmp_path)) is None

    assert len(viewed(calls)) == 1
    [(flags, carried)] = posted(calls)
    assert flags["--kind"] == ["opened"]
    assert carried == {"body": "from github"}


def test_a_pr_named_only_by_its_url_makes_no_row_once_github_says_it_is_not_open(tmp_path, tools):
    from hooks import pr_ledger

    calls, answers = tools
    answers["gh", "pr"] = json.dumps(json.loads(VIEW) | {"url": "https://github.com/Forge-AI/monorepo/pull/50", "state": "MERGED"})

    pr_ledger.record_opened_prs(shipped(tmp_path, output="https://github.com/Forge-AI/monorepo/pull/50\n", command="gh pr create --fill"))

    assert len(viewed(calls)) == 1
    assert posted(calls) == []


def test_a_lanes_hand_posted_opened_record_is_not_a_row(tmp_path, tools):
    from hooks import pr_ledger

    calls, answers = tools
    answers["cci", "grep"] = HAND_POSTED + "\n"

    pr_ledger.record_opened_prs(shipped(tmp_path))

    assert [flags["--kind"] for flags, _ in posted(calls)] == [["opened"]]


def test_a_new_head_on_a_known_row_posts_head_with_no_body_and_no_github_read(tmp_path, tools):
    from hooks import pr_ledger

    calls, answers = tools
    answers["cci", "grep"] = ROW + "\n"

    assert pr_ledger.record_opened_prs(shipped(tmp_path)) is None

    assert viewed(calls) == []
    [(flags, carried)] = posted(calls)
    assert flags["--kind"] == ["head"]
    assert flags["--head"] == [f"{GT_REF}={SHA}"]
    assert carried is None


def test_a_changed_body_at_the_same_head_is_a_new_record(tmp_path, tools):
    from hooks import pr_ledger

    calls, answers = tools
    answers["cci", "grep"] = ROW + "\n"
    (tmp_path / "body.md").write_text("first")
    command = "ccx vcs ship --no-commit --pr-body-file body.md"
    pr_ledger.record_opened_prs(shipped(tmp_path, command=command))
    (tmp_path / "body.md").write_text("second")
    pr_ledger.record_opened_prs(shipped(tmp_path, command=command))
    pr_ledger.record_opened_prs(shipped(tmp_path))

    records = posted(calls)
    assert [carried for _, carried in records] == [{"body": "first"}, {"body": "second"}, None]
    assert len({flags["--id"][0] for flags, _ in records}) == 3


def test_a_pr_on_trunk_has_no_parent_and_a_stacked_pr_names_its_base(tmp_path, tools):
    from hooks import pr_ledger

    calls, answers = tools
    answers["gh", "pr"] = json.dumps({"url": "https://github.com/Forge-AI/monorepo/pull/1", "title": "x: y", "headRefName": "x", "baseRefName": "dev", "headRefOid": SHA, "body": "b", "state": "OPEN"})

    pr_ledger.record_opened_prs(shipped(tmp_path, (FIXTURES / "ccx-stack-submit.txt").read_text(), "ccx vcs stack submit"))

    [(trunk, _), (stacked, _)] = posted(calls)
    assert not [attr for attr in trunk["--attr"] if attr.startswith("parent=")]
    assert "parent=yasyf/v3-deploy-experience/tenant-branch-lists" in stacked["--attr"]


def test_a_branch_scoped_file_reaches_only_its_branch(tmp_path, tools):
    from hooks import pr_ledger

    calls, answers = tools
    answers["cci", "grep"] = ROW + "\n"
    (tmp_path / "body.md").write_text("for the lower one")
    command = "ccx vcs ship --no-commit --pr-body-file yasyf/v3-deploy-experience/tenant-branch-lists=body.md"

    pr_ledger.record_opened_prs(shipped(tmp_path, (FIXTURES / "ccx-stack-submit.txt").read_text(), command))

    assert [carried for _, carried in posted(calls)] == [{"body": "for the lower one"}, None]


@pytest.mark.parametrize("failing", [("cci", "post"), ("cci", "grep"), ("gh", "pr")], ids=["post", "row-read", "github-read"])
def test_a_failed_read_or_post_warns_and_never_refuses(tmp_path, tools, failing):
    from hooks import pr_ledger

    _, answers = tools
    answers[failing] = (1, "", "HTTP 502: Bad Gateway")

    result = pr_ledger.record_opened_prs(shipped(tmp_path))

    assert result.action != "block"
    assert "#28534 has no fresh row" in result.message
    assert "HTTP 502" in result.message


def test_an_edited_body_on_a_known_row_posts_head_with_the_new_body(tmp_path, tools):
    from hooks import pr_ledger

    calls, answers = tools
    answers["cci", "grep"] = ROW + "\n"

    assert pr_ledger.record_edited_bodies(shipped(tmp_path, "https://github.com/Forge-AI/monorepo/pull/28534", "gh pr edit 28534 --body-file body.md")) is None

    [(flags, carried)] = posted(calls)
    assert flags["--kind"] == ["head"]
    assert carried == {"body": "from github"}


def test_an_edited_body_posts_githubs_head_and_not_a_local_branch_left_behind(tmp_path, tools):
    from hooks import pr_ledger

    calls, answers = tools
    answers["cci", "grep"] = ROW + "\n"
    answers["git", "rev-parse"] = "187ae7708209c68dc58c1cf1d8cec1049739b89e"

    assert pr_ledger.record_edited_bodies(shipped(tmp_path, "https://github.com/Forge-AI/monorepo/pull/28534", "gh pr edit 28534 --body-file body.md")) is None

    [(flags, _)] = posted(calls)
    assert flags["--head"] == [f"{GT_REF}={SHA}"]


def test_an_edited_body_on_a_pr_with_no_row_posts_nothing(tmp_path, tools):
    from hooks import pr_ledger

    calls, _ = tools

    assert pr_ledger.record_edited_bodies(shipped(tmp_path, "https://github.com/Forge-AI/monorepo/pull/28534", "gh pr edit 28534 --body hello")) is None

    assert posted(calls) == []


def test_a_named_drive_reads_only_its_own_registry_file(tmp_path, monkeypatch):
    drives = tmp_path / "config" / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / "other.json").write_text("not json")
    (drives / "mine.json").write_text(json.dumps({"drive": "mine", "sessions": []}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))

    monkeypatch.setenv("CLAUDE_LONG_RUNNING_DRIVE", "mine")
    assert drive_entry(event(tmp_path))["drive"] == "mine"

    monkeypatch.setenv("CLAUDE_LONG_RUNNING_DRIVE", "gone")
    assert drive_entry(event(tmp_path)) is None


def test_a_session_with_no_drive_named_finds_the_drive_that_holds_it(tmp_path, monkeypatch):
    drives = tmp_path / "config" / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / "other.json").write_text(json.dumps({"drive": "other", "sessions": []}))
    (drives / "mine.json").write_text(json.dumps({"drive": "mine", "sessions": [SESSION]}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)

    assert drive_entry(event(tmp_path))["drive"] == "mine"


def launched_with(tmp_path: Path, prompt: str) -> SimpleNamespace:
    transcript = tmp_path / f"{SESSION}.jsonl"
    transcript.write_text(json.dumps(fixture_line(0, T.user(prompt))) + "\n")
    return SimpleNamespace(ctx=build_context(None, transcript, tmp_path, tmp_path))


@pytest.mark.parametrize("name", ["orca-launch/c9efe004", "lane/brief.md"])
def test_the_judges_read_the_end_of_the_brief_the_session_was_launched_with(tmp_path, name):
    brief = tmp_path / name
    brief.parent.mkdir(parents=True)
    brief.write_text("x" * 7000 + "a monitor that has passed has a bound after its final card")
    said = lane_brief(launched_with(tmp_path, f"Lane a: read {brief} in full first."))

    assert said.endswith("a bound after its final card")
    assert len(said) < 6200


def test_a_session_launched_with_no_brief_gives_the_judges_none(tmp_path):
    assert lane_brief(launched_with(tmp_path, "fix the login page")) == ""
