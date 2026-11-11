import io
import json
import os
from collections.abc import Callable
from pathlib import Path

import agent_brief
import bind_agent
import landing_guard
import no_file_tools
import no_slack_reads
import one_line_result
import pre_compact
import pytest
import quiet_turn
import scratch_folder
import session_start
from drive import state

Run = Callable[..., tuple[int, str]]
PR = "https://github.com/Forge-AI/monorepo/pull/42"
ORIGIN = "git@github.com:Forge-AI/monorepo.git"


@pytest.fixture
def drive_pr(run: Run, demo: str) -> str:
    run("order", "add", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    run("pr", "add", PR, "--job", "j1", "--tap")
    return state.pr_key(PR)


@pytest.fixture
def no_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def record(args: list[str], cwd: str) -> str:
        calls.append(args)
        raise AssertionError(f"unexpected call: {args}")

    monkeypatch.setattr(landing_guard, "run", record)
    return calls


def origin_is(monkeypatch: pytest.MonkeyPatch, remote: str) -> None:
    def answer(args: list[str], cwd: str) -> str:
        assert args == ["git", "remote", "get-url", "origin"]
        return remote

    monkeypatch.setattr(landing_guard, "run", answer)


def bash(command: str, cwd: str = "/work") -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd}


def approve(key: str) -> None:
    with state.edit("demo") as data:
        data["prs"][key]["tap"] = {"label": "Approve", "at": state.stamp(), "head": "abc", "source": "slack"}


def hook_output(module: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stdin: str) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    module.main()
    return capsys.readouterr().out


def test_session_start_gives_each_role_its_own_context(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "ship the fix")
    output = session_start.respond({"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "source": "resume"})
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    context = output["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("You are the coordinator of drive demo: ")
    assert "ship the fix" not in context
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    context = session_start.respond({"session_id": "talker", "source": "compact"})["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("You are the communicator of drive demo: you talk to him.")


def test_session_start_names_the_channels_each_role_holds(run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    run("start", "demo", "--role", "coordinator", "--channel", "D123", "--watch", "C0B75AL4XEW", "--watch", "C0822AHFY3G")
    output = session_start.respond({"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "source": "compact"})
    context = output["hookSpecificOutput"]["additionalContext"]
    assert 'Hold with slack_subscribe(kinds=["alerts", "releases", "posts"]): C0B75AL4XEW, C0822AHFY3G' in context
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    context = session_start.respond({"session_id": "talker"})["hookSpecificOutput"]["additionalContext"]
    assert "Hold with slack_watch: his DM D123" in context


def test_session_start_gives_a_subagent_of_a_role_no_role_context(demo: str) -> None:
    payload = {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "source": "compact"}
    assert session_start.respond(payload) is not None
    assert session_start.respond({**payload, "agent_id": "a64e718803d352214", "agent_type": "long-running:builder"}) is None


def test_session_start_prints_nothing_for_another_session(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert session_start.respond({"session_id": "someone-else"}) is None
    assert hook_output(session_start, monkeypatch, capsys, json.dumps({"session_id": "someone-else"})) == ""


def test_session_start_main_prints_hook_json(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = json.loads(hook_output(session_start, monkeypatch, capsys, json.dumps({"session_id": os.environ["CLAUDE_CODE_SESSION_ID"]})))
    assert "drive demo" in output["hookSpecificOutput"]["additionalContext"]


def test_pre_compact_prints_each_roles_summary_instructions(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "trigger": "auto"}
    out = hook_output(pre_compact, monkeypatch, capsys, json.dumps(payload))
    assert "summary of the coordinator of drive demo is at most 3000 characters" in out
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    out = hook_output(pre_compact, monkeypatch, capsys, json.dumps({"session_id": "talker", "trigger": "manual"}))
    assert "summary of the communicator of drive demo is at most 1500 characters" in out
    assert hook_output(pre_compact, monkeypatch, capsys, json.dumps({"session_id": "someone-else"})) == ""


def file_call(tool: str, session: str | None = None, **fields: object) -> dict:
    return {
        "session_id": session or os.environ["CLAUDE_CODE_SESSION_ID"],
        "tool_name": tool,
        "tool_input": {"file_path": "/x/reference/jobs.md"},
        **fields,
    }


def test_a_drive_role_opens_no_file_and_its_agents_and_other_sessions_do(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = json.loads(hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Read"))))
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert reason.startswith("refused: the coordinator opens no file with Read")
    assert "`drive manual <page>`" in reason
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    assert no_file_tools.deny_reason(file_call("Edit", "talker")).startswith("refused: the communicator opens no file with Edit")
    assert no_file_tools.deny_reason(file_call("Read", "talker", agent_id="a-1")) is None
    assert hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Write", "someone-else"))) == ""


def test_a_drive_role_reads_no_slack_thread_and_its_agents_and_other_sessions_do(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    thread = "mcp__plugin_cc-slack_cc-slack__slack_thread"
    output = json.loads(hook_output(no_slack_reads, monkeypatch, capsys, json.dumps(file_call(thread))))
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert output["hookSpecificOutput"]["permissionDecisionReason"].startswith(
        f"refused: the coordinator reads no Slack thread or channel itself with {thread}"
    )
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    history = "mcp__plugin_cc-slack_cc-slack__slack_history"
    assert no_slack_reads.deny_reason(file_call(history, "talker")).startswith("refused: the communicator reads no Slack")
    assert no_slack_reads.deny_reason(file_call(thread, "talker", agent_id="a-1")) is None
    assert hook_output(no_slack_reads, monkeypatch, capsys, json.dumps(file_call(history, "someone-else"))) == ""


def agent_stop(message: str, session: str | None = None, **fields: object) -> dict:
    return {
        "session_id": session or os.environ["CLAUDE_CODE_SESSION_ID"],
        "hook_event_name": "SubagentStop",
        "agent_id": "a-7",
        "stop_hook_active": False,
        "last_assistant_message": message,
        **fields,
    }


def test_a_drive_job_with_a_long_final_message_is_sent_back_to_keep_it_in_its_report(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    run("job", "set", "j1", "--agent", "a-7")
    long = "WAITING CI on the PR\nPR: https://github.com/yasyf/cc-skills/pull/1\nCHECK: build 12"
    output = json.loads(hook_output(one_line_result, monkeypatch, capsys, json.dumps(agent_stop(long))))
    assert output["decision"] == "block"
    assert output["reason"].startswith("Your final message is 3 lines and 82 characters")
    assert "`drive job report j1 -`" in output["reason"]
    assert one_line_result.block_reason(agent_stop("WAITING CI on https://github.com/yasyf/cc-skills/pull/1")) is None
    assert "is 1 line and 501 characters" in one_line_result.block_reason(agent_stop("x" * 501))


def test_a_handler_is_held_to_one_line_and_other_sessions_are_left_alone(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    reason = one_line_result.block_reason(agent_stop("POSTED https://acme.slack.com/archives/D1/p1\nI posted it", "talker"))
    assert "lands whole in the communicator's context" in reason
    assert "drive job report" not in reason
    assert one_line_result.block_reason(agent_stop("a\nb", "someone-else")) is None
    assert one_line_result.block_reason(agent_stop("a\nb", "talker", stop_hook_active=True)) is None


def test_a_drive_jobs_returned_line_sets_its_state_once_the_line_is_let_through(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    run("job", "set", "j1", "--agent", "a-7")
    returned = "WAITING: CI on #1\nPR: https://github.com/yasyf/cc-skills/pull/1"
    hook_output(one_line_result, monkeypatch, capsys, json.dumps(agent_stop(returned)))
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    assert hook_output(one_line_result, monkeypatch, capsys, json.dumps(agent_stop(returned, stop_hook_active=True))) == ""
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("waiting", "WAITING: CI on #1")


def started(prompt: str, session: str | None = None) -> dict:
    return {
        "session_id": session or os.environ["CLAUDE_CODE_SESSION_ID"],
        "hook_event_name": "PostToolUse",
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "long-running:builder", "run_in_background": True, "prompt": prompt},
        "tool_response": {"isAsync": True, "status": "async_launched", "agentId": "a-9"},
    }


def test_an_agent_started_on_its_briefs_job_is_recorded_there_and_a_message_to_it_runs_it_again(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--remote")
    run("job", "add", "reader", "--order", "o1", "--goal", "read it")
    bind_agent.bind(started("Drive: demo, orders o1 and o2, jobs j1 j2"))
    bind_agent.bind(started("Drive: demo, order o1, job j1", "someone-else"))
    assert [job["agent"] for job in state.load("demo")["jobs"].values()] == [None, None]
    assert hook_output(bind_agent, monkeypatch, capsys, json.dumps(started("Goal: fix it\nDrive: demo, order o1, job j1"))) == ""
    job = state.load("demo")["jobs"]["j1"]
    assert (job["agent"], job["where"], job["session"], job["state"]) == ("a-9", "local", os.environ["CLAUDE_CODE_SESSION_ID"], "running")
    run("job", "set", "j1", "--state", "waiting")
    woken = {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "tool_name": "SendMessage", "tool_input": {"to": "a-9", "message": "his answer"}}
    bind_agent.bind(woken)
    bind_agent.bind(woken | {"tool_input": {"to": "a-unknown", "message": "x"}})
    assert [job["state"] for job in state.load("demo")["jobs"].values()] == ["running", "running"]


def turn_stop(message: str, **fields: object) -> dict:
    return {
        "session_id": os.environ["CLAUDE_CODE_SESSION_ID"],
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "last_assistant_message": message,
        **fields,
    }


def test_a_communicator_turn_ends_on_one_word_and_other_sessions_are_left_alone(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    assert quiet_turn.block_reason(turn_stop("Posted the ack and started a handler.")) is None
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    reason = quiet_turn.block_reason(turn_stop("Acked and started a handler for his question.", session_id="talker"))
    assert reason.startswith("Your turn ended on 8 words in this terminal")
    assert "`Sent.`" in reason
    assert quiet_turn.block_reason(turn_stop("Sent.", session_id="talker")) is None
    assert quiet_turn.block_reason(turn_stop("a b", session_id="talker", stop_hook_active=True)) is None
    assert quiet_turn.block_reason(turn_stop("a b", session_id="talker", agent_id="a-7")) is None


def agent_call(subagent_type: str, prompt: str) -> str:
    return json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": subagent_type, "prompt": prompt, "description": "d"}})


def started_by_role(session: str, **call: object) -> dict:
    return {"session_id": session, "tool_name": "Agent", "tool_input": {"description": "d", "prompt": "Record: 1 Drive: demo", **call}}


def test_a_drive_role_starts_no_named_agent_no_fable_but_its_designer_and_no_builder_without_scope(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    coordinator = os.environ["CLAUDE_CODE_SESSION_ID"]
    named = started_by_role(coordinator, subagent_type="long-running:handler", name="desk")
    output = json.loads(hook_output(agent_brief, monkeypatch, capsys, json.dumps(named)))
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert output["hookSpecificOutput"]["permissionDecisionReason"].startswith("refused: the coordinator starts no named agent")
    fable = started_by_role(coordinator, subagent_type="long-running:builder", model="fable", prompt="Serves: his order")
    assert agent_brief.deny_reason(fable).startswith("refused: Fable runs only the drive's two sessions")
    assert agent_brief.deny_reason(started_by_role(coordinator, subagent_type="long-running:designer", model="fable")) is None
    unscoped = started_by_role(coordinator, subagent_type="long-running:builder", prompt="Order (his words): fix it\nGoal: fix it")
    assert agent_brief.deny_reason(unscoped).startswith("refused: a builder's brief carries a `Serves:` line")
    for brief in ("Order: Drive tooling: x\nServes: drive tooling", "Handoff: 661200 Drive: demo Scratch: /s"):
        assert agent_brief.deny_reason(started_by_role(coordinator, subagent_type="long-running:builder", prompt=brief)) is None
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    assert agent_brief.deny_reason(started_by_role("talker", subagent_type="long-running:handler", name="desk")).startswith(
        "refused: the communicator starts no named agent"
    )
    assert agent_brief.deny_reason({**named, "agent_id": "a-1"}) is None
    assert hook_output(agent_brief, monkeypatch, capsys, json.dumps(started_by_role("someone-else", name="desk"))) == ""


def test_scratch_folder_exists_before_a_drive_agent_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    record = tmp_path / "scratch" / "drive-communicator"
    hook_output(scratch_folder, monkeypatch, capsys, agent_call("long-running:handler", f"Record: 658250 Drive: demo Scratch: {record}"))
    assert record.is_dir()
    message = tmp_path / "scratch" / "handler-2"
    prompt = f'Message (his words): "ship it"\nDrive: demo. Scratch: {message}. Deliverable: an owner record'
    hook_output(scratch_folder, monkeypatch, capsys, agent_call("long-running:handler", prompt))
    assert message.is_dir()
    job = tmp_path / "jobs" / "j7"
    hook_output(scratch_folder, monkeypatch, capsys, agent_call("long-running:builder", f"Order: o1\nScratch: {job}\nDrive: demo"))
    assert job.is_dir()


def test_scratch_folder_leaves_other_agents_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "elsewhere"
    assert hook_output(scratch_folder, monkeypatch, capsys, agent_call("Explore", f"Scratch: {folder}")) == ""
    assert hook_output(scratch_folder, monkeypatch, capsys, agent_call("long-running:reader", "no folder named")) == ""
    assert not folder.exists()


def test_guard_denies_an_untapped_drive_pr(
    drive_pr: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    origin_is(monkeypatch, ORIGIN)
    output = json.loads(hook_output(landing_guard, monkeypatch, capsys, json.dumps(bash("gh pr merge 42 --squash"))))
    decision = output["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"
    assert decision["permissionDecisionReason"].startswith("Forge-AI/monorepo#42: no tap from him yet")
    assert "slack_dm_status tap_prs" in decision["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    [
        f"gh pr merge {PR} --squash",
        "cd /work/monorepo && gh pr merge 42 -R Forge-AI/monorepo --auto",
        "gh pr edit 42 --repo Forge-AI/monorepo --add-label merge",
        "GH_TOKEN=x ccx vcs merge 42 --repo Forge-AI/monorepo",
    ],
)
def test_guard_denies_every_landing_form(drive_pr: str, no_calls: list[list[str]], command: str) -> None:
    assert landing_guard.deny_reason(bash(command)) is not None


def test_guard_allows_a_tapped_drive_pr(drive_pr: str, no_calls: list[list[str]]) -> None:
    approve(drive_pr)
    assert landing_guard.deny_reason(bash(f"gh pr merge {PR} --squash")) is None


def test_guard_denies_a_held_drive_pr(drive_pr: str, no_calls: list[list[str]]) -> None:
    with state.edit("demo") as data:
        data["prs"][drive_pr]["tap"] = {"label": "Hold", "at": state.stamp(), "head": "abc", "source": "slack"}
    assert "held by him" in landing_guard.deny_reason(bash(f"gh pr merge {PR} --squash"))


def test_guard_allows_an_exempt_drive_pr(drive_pr: str, run: Run, no_calls: list[list[str]]) -> None:
    run("pr", "set", PR, "--exempt", "test-only")
    assert landing_guard.deny_reason(bash(f"gh pr merge {PR} --squash")) is None


def test_guard_allows_a_pr_the_drive_does_not_know(drive_pr: str, no_calls: list[list[str]]) -> None:
    assert landing_guard.deny_reason(bash("gh pr merge 999 -R Forge-AI/monorepo --squash")) is None


def test_guard_denies_admin_on_forge_even_when_tapped(drive_pr: str, no_calls: list[list[str]]) -> None:
    approve(drive_pr)
    reason = landing_guard.deny_reason(bash(f"gh pr merge {PR} --squash --admin"))
    assert reason == landing_guard.ADMIN_REASON


def test_guard_allows_a_repo_outside_forge(drive_pr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    origin_is(monkeypatch, "https://github.com/yasyf/cc-skills.git")
    assert landing_guard.deny_reason(bash("gh pr merge 42 --squash --admin")) is None


def test_guard_ignores_commands_that_do_not_land(drive_pr: str, no_calls: list[list[str]]) -> None:
    assert landing_guard.deny_reason(bash("gh pr view 42 -R Forge-AI/monorepo && echo 'gh pr merge 42'")) is None


def test_guard_resolves_a_branch_through_gh(drive_pr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def answer(args: list[str], cwd: str) -> str:
        assert args[:3] == ["gh", "pr", "view"]
        return PR

    monkeypatch.setattr(landing_guard, "run", answer)
    assert landing_guard.deny_reason(bash("gh pr merge my-branch -R Forge-AI/monorepo")) is not None


@pytest.mark.parametrize("stdin", ["not json", "{}", json.dumps({"tool_input": {"command": "gh pr merge 'unclosed"}})])
def test_guard_allows_malformed_input(
    drive_pr: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stdin: str
) -> None:
    assert hook_output(landing_guard, monkeypatch, capsys, stdin) == ""


@pytest.mark.parametrize(
    "command",
    [
        "gh -R Forge-AI/monorepo pr merge 42 --squash",
        "gh --repo Forge-AI/monorepo pr merge 42",
        "gh --repo=Forge-AI/monorepo pr merge 42",
        "gh pr -R Forge-AI/monorepo merge 42",
        "gh pr merge 42 -RForge-AI/monorepo",
        "gh pr merge 42 -sR Forge-AI/monorepo",
        "gh pr merge 42 -R Forge-AI/monorepo -s=TRUE -d=1",
        "gh pr merge 42 --repo=Forge-AI/monorepo",
        "gh pr merge -b 'body text' 42 -R Forge-AI/monorepo",
        f"env GH_TOKEN=x gh pr merge {PR}",
        f"env -i -u HOME GH_TOKEN=x /usr/local/bin/gh pr merge {PR}",
        f"VAR=x gh pr merge {PR}",
        f"command gh pr merge {PR}",
        "command -- gh pr merge 42 -R Forge-AI/monorepo",
        "exec -a landing gh pr merge 42 -R Forge-AI/monorepo",
        "nice -n 5 time -p nohup gh pr merge 42 -R Forge-AI/monorepo",
        "GH_REPO=Forge-AI/monorepo gh pr merge 42 --squash",
        "env GH_REPO=Forge-AI/monorepo gh pr merge 42",
        "export GH_REPO=Forge-AI/monorepo && gh pr merge 42",
        f"if gh pr merge {PR} --squash; then echo landed; fi",
        "bash -lc 'gh pr merge 42 -R Forge-AI/monorepo'",
        "gh pr edit 42 -R Forge-AI/monorepo -m June --add-label=ready,merge",
        "ci trunk submit '#42'",
    ],
)
def test_guard_sees_a_landing_however_it_is_phrased(drive_pr: str, no_calls: list[list[str]], command: str) -> None:
    assert landing_guard.deny_reason(bash(command)).startswith(f"{drive_pr}: no tap from him yet")


@pytest.mark.parametrize("flag", ["--admin", "--admin=true", "--admin=1", "--admin=T", "--admin=TRUE"])
def test_guard_denies_admin_however_it_is_spelled(drive_pr: str, no_calls: list[list[str]], flag: str) -> None:
    approve(drive_pr)
    assert landing_guard.deny_reason(bash(f"gh pr merge {PR} --squash {flag}")) == landing_guard.ADMIN_REASON


def test_guard_reads_admin_false_as_no_admin(drive_pr: str, no_calls: list[list[str]]) -> None:
    approve(drive_pr)
    assert landing_guard.deny_reason(bash(f"gh pr merge {PR} --admin=false")) is None


SIBLINGS = ("https://github.com/Forge-AI/monorepo/pull/41", "https://github.com/Forge-AI/monorepo/pull/43")


@pytest.fixture
def stack(drive_pr: str, run: Run) -> None:
    for url in SIBLINGS:
        run("pr", "add", url, "--job", "j1", "--tap")
    approve("Forge-AI/monorepo#41")
    approve(drive_pr)


@pytest.mark.parametrize(
    "command",
    [
        "stack-enqueue 41 42 43",
        "cd /elsewhere && .agents/skills/submit-pr/scripts/stack-enqueue --watch 41 42 43",
        "ci trunk submit 41 '#42' 43 --priority high",
    ],
)
def test_guard_checks_every_pr_a_queue_submit_names(stack: None, no_calls: list[list[str]], command: str) -> None:
    assert landing_guard.deny_reason(bash(command)).startswith("Forge-AI/monorepo#43: no tap from him yet")


@pytest.mark.parametrize(
    "command",
    [
        "stack-enqueue 41 42",
        "stack-enqueue --check 41 42 43",
        "stack-enqueue --status 43",
        "gh pr merge 43 -R Forge-AI/monorepo --disable-auto",
    ],
)
def test_guard_allows_tapped_queues_reads_and_cancelled_auto_merge(
    stack: None, no_calls: list[list[str]], command: str
) -> None:
    assert landing_guard.deny_reason(bash(command)) is None


def test_guard_still_denies_when_disable_auto_is_false(drive_pr: str, no_calls: list[list[str]]) -> None:
    assert landing_guard.deny_reason(bash("gh pr merge 42 -R Forge-AI/monorepo --auto --disable-auto=false")) is not None


@pytest.mark.parametrize(
    "command", ["GH_REPO=Forge-AI/monorepo gh pr merge --squash", "ccx vcs merge --match-head-commit abc --repo Forge-AI/monorepo"]
)
def test_guard_looks_up_a_merge_naming_no_pr_in_the_selected_repo(
    drive_pr: str, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    calls: list[list[str]] = []

    def answer(args: list[str], cwd: str) -> str:
        calls.append(args)
        if args[:2] == ["git", "branch"]:
            return "fix-branch"
        return PR if "-R" in args else "https://github.com/yasyf/cc-skills/pull/42"

    monkeypatch.setattr(landing_guard, "run", answer)
    assert landing_guard.deny_reason(bash(command)) is not None
    assert calls == [
        ["git", "branch", "--show-current"],
        ["gh", "pr", "view", "fix-branch", "-R", "Forge-AI/monorepo", "--json", "url", "-q", ".url"],
    ]


def test_guard_looks_up_a_merge_naming_no_pr_or_repo_in_the_checkout(
    drive_pr: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def answer(args: list[str], cwd: str) -> str:
        calls.append(args)
        return ORIGIN if args[0] == "git" else PR

    monkeypatch.setattr(landing_guard, "run", answer)
    assert landing_guard.deny_reason(bash("gh pr merge --squash")) is not None
    assert calls == [["git", "remote", "get-url", "origin"], ["gh", "pr", "view", "--json", "url", "-q", ".url"]]


def started_at(name: str, moment: str) -> None:
    with state.edit(name) as data:
        data["roles"]["coordinator"]["started"] = moment


def test_guard_judges_a_shared_pr_by_the_newest_drive_that_knows_it(
    drive_pr: str, run: Run, no_calls: list[list[str]]
) -> None:
    approve(drive_pr)
    run("start", "later", "--role", "coordinator")
    run("order", "add", "land it again", "--drive", "later")
    run("job", "add", "builder", "--order", "o1", "--goal", "again", "--drive", "later")
    run("pr", "add", PR, "--job", "j1", "--tap", "--drive", "later")
    run("start", "unrelated", "--role", "coordinator")
    started_at("demo", "2026-11-09T08:00:00-05:00")
    started_at("later", "2026-11-09T09:00:00-05:00")
    started_at("unrelated", "2026-11-09T12:00:00-05:00")
    assert landing_guard.deny_reason(bash(f"gh pr merge {PR}")).startswith(f"{drive_pr}: no tap from him yet")
    started_at("demo", "2026-11-09T07:30:00-08:00")
    assert landing_guard.deny_reason(bash(f"gh pr merge {PR}")) is None


def test_guard_reads_gh_repo_from_the_session_environment(
    drive_pr: str, no_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_REPO", "Forge-AI/monorepo")
    assert landing_guard.deny_reason(bash("gh pr merge 42 --admin")) == landing_guard.ADMIN_REASON


def test_gh_repo_does_not_select_the_repo_for_ccx(drive_pr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    origin_is(monkeypatch, "https://github.com/yasyf/cc-skills.git")
    assert landing_guard.deny_reason(bash("GH_REPO=Forge-AI/monorepo ccx vcs merge 42 --admin")) is None


@pytest.mark.parametrize("command", ["env -C /work/monorepo gh pr merge 42", "env --chdir=monorepo gh pr merge 42"])
def test_guard_reads_the_origin_where_env_runs_the_command(
    drive_pr: str, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    def answer(args: list[str], cwd: str) -> str:
        assert args == ["git", "remote", "get-url", "origin"]
        return ORIGIN if cwd == "/work/monorepo" else "https://github.com/yasyf/cc-skills.git"

    monkeypatch.setattr(landing_guard, "run", answer)
    assert landing_guard.deny_reason(bash(command)) is not None


def asked_from_bash(command: str, session: str) -> dict:
    return bash(command) | {"session_id": session}


def test_bash_posts_a_drive_ask_only_when_it_carries_a_kept_ask(demo: str, no_calls: list[list[str]]) -> None:
    session = os.environ["CLAUDE_CODE_SESSION_ID"]
    kept = state.drive_dir(demo) / "asks" / "j1.md"
    for command in (
        "cci post --kind ask --lane root --to communicator@demo 'Close these sessions?'",
        "cd /work && cci post --lane root --kind=ASK --attr job=j1 'Close these sessions?'",
        "zsh -c \"cci post --kind ask 'Close these sessions?'\"",
    ):
        assert landing_guard.deny_reason(asked_from_bash(command, session)) == landing_guard.ASK_BY_TOOL
    carried = f"cci post --kind ask --attr ask={kept} --attr job=j1 'Close these sessions?'"
    assert landing_guard.deny_reason(asked_from_bash(carried, session)) is None
    assert landing_guard.deny_reason(asked_from_bash("cci post --kind report 'merged'", session)) is None
    assert landing_guard.deny_reason(asked_from_bash("cci post --kind ask 'Close these sessions?'", "another-session")) is None
