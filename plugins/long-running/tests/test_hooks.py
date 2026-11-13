import io
import json
import os
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import agent_brief
import compacted_skill
import landing_guard
import no_file_tools
import no_slack_reads
import no_terminal_questions
import one_line_result
import pre_compact
import pytest
import quiet_turn
import scratch_folder
import session_start
from conftest import LEAD, RESEARCH, SHORT_ASK, WRITTEN_AT, drawn_card, Answers, Jev, WatchProcess
from drive import agents, brief, cli, land, limits, place, render, settled, state, watch

Run = Callable[..., tuple[int, str]]
PR = "https://github.com/Forge-AI/monorepo/pull/42"
EARLIER = "2026-01-01T00:00:00+00:00"
ORIGIN = "git@github.com:Forge-AI/monorepo.git"


@pytest.fixture
def drive_pr(run: Run, demo: str) -> str:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    run("pr", "add", PR, "--job", "j1", *LEAD)
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
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd, "transcript_path": "/work/session.jsonl"}


def approve(key: str) -> None:
    with state.edit("demo") as data:
        data["prs"][key]["tap"] = {"label": "Approve", "at": state.stamp(), "head": "abc", "source": "slack"}


def hook_output(module: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stdin: str) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    module.main()
    return capsys.readouterr().out


def test_session_start_gives_each_role_its_own_context(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess, registered: list[tuple[str, str, str]]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    watch_process.ensured.clear()
    registered.clear()
    coordinator = os.environ["CLAUDE_CODE_SESSION_ID"]
    output = session_start.respond({"session_id": coordinator, "source": "resume"})
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    context = output["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("You are the coordinator of drive demo: ")
    assert "ship the fix" not in context
    assert registered == [("demo-coordinator", state.load("demo")["roles"]["coordinator"]["cwd"], coordinator)]
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    registered.clear()
    context = session_start.respond({"session_id": "talker", "source": "compact"})["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("You are the communicator of drive demo: you talk to him.")
    assert watch_process.ensured == ["demo"]
    assert registered == []


def test_session_start_gives_a_sub_coordinator_its_projects_context_and_starts_its_projects_watch(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess, registered: list[tuple[str, str, str]]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", "demo", "--role", "coordinator", "--project", "Iris")
    watch_process.ensured.clear()
    registered.clear()
    context = session_start.respond({"session_id": "iris-1", "source": "resume"})["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("You are the sub-coordinator for project Iris of drive demo: ")
    assert "cci: run cci drive use demo, and no cci subscribe: your cci alias demo-iris owns your subscription." in context
    assert watch_process.ensured == ["demo --project Iris"]
    assert registered == [("demo-iris", state.load("demo")["subcoordinators"]["Iris"]["cwd"], "iris-1")]


def test_session_start_keeps_a_coordinators_block_and_ends_it_with_cci_refusing_to_register_the_session(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    refusal = "cci did not register session session-under-test as the coordinator's, so nothing keeps it running or hearing its records: cci session register exited 1: no drive demo"

    def refuse(name: str, role: str, project: str | None, worktree: str, session: str) -> str:
        raise state.DriveError(refusal)

    monkeypatch.setattr(agents, "register", refuse)
    context = session_start.respond({"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "source": "compact"})["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("You are the coordinator of drive demo: ")
    assert context.endswith(f"\n\n{refusal}")


def test_session_start_names_the_channels_each_role_holds(run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    run("start", "demo", "--role", "coordinator", "--watch", "C0B75AL4XEW", "--watch", "C0822AHFY3G")
    output = session_start.respond({"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "source": "compact"})
    context = output["hookSpecificOutput"]["additionalContext"]
    assert 'Hold with cc-slack subscribe --kind alerts,releases,posts --channel <id>: C0B75AL4XEW, C0822AHFY3G' in context
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    context = session_start.respond({"session_id": "talker"})["hookSpecificOutput"]["additionalContext"]
    assert "Hold with slack_watch: his DM D123" in context


def test_a_compaction_brings_the_newest_installed_skill_page_as_a_hook_output_of_its_own_beside_the_role_block(demo: str) -> None:
    payload = {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "source": "compact"}
    page = (compacted_skill.PLUGIN / "skills" / "long-running" / "SKILL.md").read_text()
    body = page.split("\n---\n", 1)[1].strip()
    skill = compacted_skill.respond(payload)["hookSpecificOutput"]
    block = session_start.respond(payload)["hookSpecificOutput"]["additionalContext"]
    assert (skill["hookEventName"], skill["additionalContext"].strip()) == ("SessionStart", body)
    assert body not in block and block.startswith("You are the coordinator of drive demo: ")
    assert compacted_skill.respond({**payload, "agent_id": "a64e718803d352214"}) is None
    assert compacted_skill.respond({**payload, "session_id": "someone-else"}) is None
    hooks = json.loads((compacted_skill.PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]["SessionStart"]
    assert [group["matcher"] for group in hooks if group["hooks"][0]["command"].endswith(" compacted_skill.py")] == ["compact"]


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
    assert "a bounded Bash slice such as `tail -c 2000 <file>`" in reason
    assert "a lesson is a cc-notes note (`ccn note add`)" in reason
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


def test_no_session_bound_to_a_drive_asks_in_its_terminal_and_a_session_bound_to_none_does(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    question = {"questions": [{"question": "Which commit ships?", "options": [{"label": "9b6130cd"}]}]}
    output = json.loads(hook_output(no_terminal_questions, monkeypatch, capsys, json.dumps(file_call("AskUserQuestion", tool_input=question))))
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert output["judged"] is False
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason.startswith("refused: no drive session asks in its terminal")
    assert "`drive job ask <job> <file>`" in reason
    assert "which draws his card on it for demo" in reason
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    assert no_terminal_questions.deny_reason(file_call("AskUserQuestion", "talker")) is not None
    assert no_terminal_questions.deny_reason(file_call("AskUserQuestion", "talker", agent_id="a-1", agent_type="long-running:builder")) is not None
    with state.edit(demo) as data:
        data["handlers"]["h1"] = {"session": "handler-own", "started": state.stamp(), "holds": None, "handed": []}
    assert no_terminal_questions.deny_reason(file_call("AskUserQuestion", "handler-own", agent_type="long-running:handler")) is not None
    assert hook_output(no_terminal_questions, monkeypatch, capsys, json.dumps(file_call("AskUserQuestion", "someone-else"))) == ""


def test_a_drive_job_asking_for_screen_control_access_is_refused_at_once_and_told_to_return_the_check_unproven(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    access = {"apps": ["Slack"], "reason": "Open the Slack tab to see the card"}
    call = file_call("mcp__computer-use__request_access", agent_id="a-1", agent_type="long-running:reader", tool_input=access)
    output = json.loads(hook_output(no_terminal_questions, monkeypatch, capsys, json.dumps(call)))
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert output["judged"] is False
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason.startswith("refused: mcp__computer-use__request_access waits on a person approving it in a dialog in this terminal")
    assert "`UNPROVEN: <the check>: <the proof that settles it>`" in reason
    assert hook_output(no_terminal_questions, monkeypatch, capsys, json.dumps(file_call("mcp__computer-use__request_access", "someone-else", tool_input=access))) == ""


def agent_stop(message: str, session: str | None = None, **fields: object) -> dict:
    return {
        "session_id": session or os.environ["CLAUDE_CODE_SESSION_ID"],
        "hook_event_name": "SubagentStop",
        "transcript_path": "session.jsonl",
        "agent_id": "a-7",
        "stop_hook_active": False,
        "last_assistant_message": message,
        **fields,
    }


def test_a_drive_job_with_a_long_final_message_is_sent_back_to_keep_it_in_its_report(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    run("job", "set", "j1", "--agent", "a-7")
    long = "WAITING CI on the PR\nPR: https://github.com/yasyf/cc-skills/pull/1\nCHECK: build 12"
    run("pr", "add", "https://github.com/yasyf/cc-skills/pull/1", "--job", "j1", *LEAD)
    output = json.loads(hook_output(one_line_result, monkeypatch, capsys, json.dumps(agent_stop(long))))
    assert (output["decision"], output["judged"]) == ("block", False)
    assert output["reason"].startswith("Your final message is 3 lines and 82 characters")
    assert "`drive job report j1 -`" in output["reason"]
    waiting = "WAITING CI on https://github.com/yasyf/cc-skills/pull/1"
    assert one_line_result.block_reason(agent_stop(waiting)).startswith("Refused on purpose: j1's return is not kept in the drive")
    assert one_line_result.block_reason(agent_stop("ASK /scratch/ask.md")).startswith("Refused on purpose: j1 put his card up and built nothing")
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{waiting}\nPR: https://github.com/yasyf/cc-skills/pull/1\n"))
    run("job", "report", "j1", "-")
    assert one_line_result.block_reason(agent_stop(waiting)) is None
    assert "is 1 line and 501 characters" in one_line_result.block_reason(agent_stop("x" * 501))


def test_a_handler_is_held_to_one_line_and_other_sessions_are_left_alone(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    reason = one_line_result.block_reason(agent_stop("POSTED https://acme.slack.com/archives/D1/p1\nI posted it", "talker"))
    assert "lands whole in the communicator's context" in reason
    assert "drive job report" not in reason
    assert one_line_result.block_reason(agent_stop("a\nb", "someone-else")) is None
    assert one_line_result.block_reason(agent_stop("a\nb", "talker", stop_hook_active=True)) is None


def test_a_job_agent_stops_once_its_newest_return_opens_with_its_line_whatever_its_said_line_reads(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://github.com/Forge-AI/monorepo/pull/8"
    pushed = f"PUSHED {url}"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--agent", "a-7")
    run("pr", "add", url, "--job", "j1", *LEAD)
    run("job", "set", "j1", "--said", "pushing the fix")
    assert one_line_result.block_reason(agent_stop(pushed)).startswith("Refused on purpose: j1's return is not kept in the drive")
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{pushed}\nCHANGE: x\n"))
    run("job", "report", "j1", "-")
    run("job", "set", "j1", "--said", "READY refused: Forge-AI/monorepo#8's required test failed")
    assert one_line_result.block_reason(agent_stop(pushed)) is None
    monkeypatch.setattr("sys.stdin", io.StringIO("BLOCKED the branch is gone\n"))
    run("job", "report", "j1", "-")
    assert one_line_result.block_reason(agent_stop(pushed)).startswith("Refused on purpose: j1's return is not kept in the drive")


def test_a_drive_jobs_stop_sets_no_state_whatever_line_it_lets_through(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    run("job", "set", "j1", "--agent", "a-7")
    returned = "WAITING: CI on #1\nPR: https://github.com/yasyf/cc-skills/pull/1"
    hook_output(one_line_result, monkeypatch, capsys, json.dumps(agent_stop(returned)))
    assert hook_output(one_line_result, monkeypatch, capsys, json.dumps(agent_stop(returned, stop_hook_active=True))) == ""
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("running", None)


J2776_QUESTION = "How should the release's tenant smoke remove the cloud agent it creates, when archiveAgent keeps every row createAgent wrote?"
J2776_PART = "the release's tenant smoke creates one cloud agent and deletes it"


def test_a_builder_asking_his_card_builds_its_recommended_option_in_the_same_run_and_ends_built_where_j2776_ended_waiting_with_nothing_built(
    run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("start", "durable-agents", "--role", "coordinator")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", J2776_PART)
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--agent", "a-9")
    with state.edit("durable-agents") as data:
        data["jobs"]["j2776"] = data["jobs"].pop("j1")
        data["orders"]["o1"]["jobs"] = ["j2776"]
    block = tmp_path / "ask.md"
    block.write_text(
        SHORT_ASK.replace(SHORT_ASK.splitlines()[0], f"ASK {J2776_QUESTION}").replace("Holds: none: no option changes code any job builds", f"Holds: {J2776_PART}")
    )
    asked = run("job", "ask", "j2776", str(block))[1].splitlines()[0]
    job = state.load("durable-agents")["jobs"]["j2776"]
    assert (job["state"], job["said"], [question["waits"] for question in job["questions"]]) == ("running", J2776_QUESTION, ["durable-agents:j2776-7"])
    lane = stepped(tmp_path, "Drive: durable-agents, order o1, job j2776", prompted=EARLIER)
    refused = json.loads(hook_output(one_line_result, monkeypatch, capsys, json.dumps(agent_stop(f"ASK {asked}", **lane))))
    assert refused["reason"].startswith("Refused on purpose: j2776 put his card up and built nothing")
    assert "`ccx vcs ship --no-pr`" in refused["reason"]
    assert land.ship_held(state.load("durable-agents"), "j2776", {}) == [drawn_card(1)]
    built = "BUILT j2776-delete-agent, waits on durable-agents:j2776-7"
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{built}\nCHECK: the recommended deleteAgent mutation, pushed on j2776-delete-agent\n"))
    run("job", "report", "j2776", "-")
    job = state.load("durable-agents")["jobs"]["j2776"]
    assert (job["state"], job["branch"], job["waits"]) == ("built", "j2776-delete-agent", ["durable-agents:j2776-7"])
    assert hook_output(one_line_result, monkeypatch, capsys, json.dumps(agent_stop(built, **lane))) == ""


def test_a_builder_whose_words_ask_for_options_and_no_build_ends_on_the_ask_drive_job_report_kept(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "bring me options")
    run("job", "add", "builder", "--order", "o1", "--goal", "bring options", "--agent", "a-9")
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    asked = run("job", "ask", "j1", str(block))[1].splitlines()[0]
    monkeypatch.setattr("sys.stdin", io.StringIO(f"ASK {asked}\n"))
    run("job", "report", "j1", "-")
    stop = agent_stop(f"ASK {asked}", **stepped(tmp_path, "Drive: demo, order o1, job j1", prompted=EARLIER))
    assert hook_output(one_line_result, monkeypatch, capsys, json.dumps(stop)) == ""
    job = state.load("demo")["jobs"]["j1"]
    assert (job["agent"], job["state"], job["said"]) == ("a-9", "waiting", "How should a finished run's files be laid out so its page opens without guessing?")


def stepped(tmp_path: Path, brief: str, session: str | None = None, prompted: str | None = None, **fields: object) -> dict:
    lane = tmp_path / "session" / "subagents" / "agent-a-9.jsonl"
    lane.parent.mkdir(parents=True, exist_ok=True)
    at = prompted or (state.now() + timedelta(minutes=1)).isoformat()
    lane.write_text(json.dumps({"type": "user", "timestamp": at, "message": {"role": "user", "content": brief}}) + "\n")
    return {
        "session_id": session or os.environ["CLAUDE_CODE_SESSION_ID"],
        "transcript_path": str(tmp_path / "session.jsonl"),
        "agent_id": "a-9",
        "agent_type": "long-running:builder",
        **fields,
    }


def test_an_agent_is_recorded_on_its_briefs_job_by_its_own_steps_and_a_step_after_a_wake_runs_it_again(
    demo: str, run: Run, tmp_path: Path, no_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--remote")
    run("job", "add", "reader", "--order", "o1", "--goal", "read it")
    brief = "Goal: fix it\nDrive: demo, order o1, job j1\nScratch: /x"
    hook_output(landing_guard, monkeypatch, capsys, json.dumps(bash("git status") | stepped(tmp_path, "Drive: demo, orders o1 and o2, jobs j1 j2")))
    hook_output(landing_guard, monkeypatch, capsys, json.dumps(bash("git status") | stepped(tmp_path, brief, "someone-else")))
    hook_output(landing_guard, monkeypatch, capsys, json.dumps(bash("git status") | {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"]}))
    assert [job["agent"] for job in state.load("demo")["jobs"].values()] == [None, None]
    assert hook_output(landing_guard, monkeypatch, capsys, json.dumps(bash("git status") | stepped(tmp_path, brief))) == ""
    job = state.load("demo")["jobs"]["j1"]
    assert (job["agent"], job["where"], job["session"], job["state"]) == ("a-9", "local", os.environ["CLAUDE_CODE_SESSION_ID"], "running")
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(state="waiting", returned=state.stamp())
    assert hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Read") | stepped(tmp_path, brief, prompted=EARLIER))) == ""
    assert state.load("demo")["jobs"]["j1"]["state"] == "waiting"
    assert hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Read") | stepped(tmp_path, brief))) == ""
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    assert no_calls == []


def test_a_reader_that_kept_its_done_stays_done_through_its_later_calls_and_stop_until_a_wake(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "--kind", "answer", "why is it slow")
    run("job", "add", "reader", "--order", "o1", "--goal", "answer why it is slow", "--agent", "a-9")
    findings = "The store lock is held for each copy\nsupervisor/copies.rs:42 takes it\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(f"DONE it waits on the store lock\n{findings}"))
    assert run("job", "report", "j1", "-")[0] == 0
    steps = stepped(tmp_path, "Drive: demo, order o1, job j1", prompted=EARLIER, agent_type="long-running:reader")
    assert hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Read") | steps)) == ""
    assert hook_output(one_line_result, monkeypatch, capsys, json.dumps(agent_stop("DONE it waits on the store lock", **steps))) == ""
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("done", "DONE it waits on the store lock")
    assert hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Read") | stepped(tmp_path, "Drive: demo, order o1, job j1", agent_type="long-running:reader"))) == ""
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"


def test_a_sendmessage_after_its_return_runs_the_job_again_resumed_until_its_next_line_and_a_stop_hooks_feedback_leaves_it_waiting(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The top coordinator's SendMessage resumed j2666 after its last line, yet j2666 still read waiting, so the iris session relayed his answer and handoff to work that was already running."""
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "release the clock")
    run("job", "add", "builder", "--order", "o1", "--goal", "release it", "--agent", "a-9")
    returned = state.now() - timedelta(minutes=5)
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(state="waiting", said="WAITING his answer on the SandSQL handover card", returned=state.stamp(returned))
    steps = stepped(tmp_path, "Drive: demo, order o1, job j1", prompted=EARLIER)
    woke = returned + timedelta(minutes=2)
    rows = [
        {"type": "user", "isMeta": True, "timestamp": (returned + timedelta(minutes=1)).isoformat(), "message": {"role": "user", "content": [{"type": "text", "text": "Stop hook feedback: end on one line"}]}},
        {"type": "user", "isMeta": True, "origin": {"kind": "coordinator"}, "timestamp": woke.isoformat(), "message": {"role": "user", "content": [{"type": "text", "text": "Resume j1 now: plan, then start, the clock release"}]}},
    ]
    lane = tmp_path / "session" / "subagents" / "agent-a-9.jsonl"
    with lane.open("a") as handle:
        handle.write(json.dumps(rows[0]) + "\n")
    assert hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Read") | steps)) == ""
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["resumed"]) == ("waiting", None)
    with lane.open("a") as handle:
        handle.write(json.dumps(rows[1]) + "\n")
    assert hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Read") | steps)) == ""
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["resumed"]) == ("running", state.stamp(woke))
    assert f"  - j1 builder running agent a-9: release it; resumed {render.when(state.stamp(woke))} by a message after its last line" in run("status", "j1")[1].splitlines()
    assert run("job", "replies", "j1") == (0, f"{state.stamp(woke)} resumed: a message woke its agent a-9 after its last return, and it runs on toward its next line")
    with state.edit("demo") as data:
        state.take_result(data, "j1", "WAITING the clock release, planned next")
    assert "resumed" not in run("status", "j1")[1]
    assert run("job", "replies", "j1") == (0, "j1 has sent nothing yet")


def test_an_agent_whose_brief_names_no_job_runs_the_job_recorded_with_it_after_a_wake(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--agent", "a-9")
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(state="waiting", said="Which store?", returned=state.stamp())
    unnamed = stepped(tmp_path, "Goal: fix it\nDrive: demo, order o1.\nScratch: /x")
    assert hook_output(landing_guard, monkeypatch, capsys, json.dumps(bash("git status") | unnamed)) == ""
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"


def test_a_handlers_steps_never_bind_it_to_the_job_its_brief_names(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--remote")
    handled = stepped(tmp_path, "Record: 661301\nDrive: demo, order o1, job j1\nScratch: /x", agent_type="long-running:handler")
    assert hook_output(landing_guard, monkeypatch, capsys, json.dumps(bash("git status") | handled)) == ""
    assert state.load("demo")["jobs"]["j1"]["agent"] is None


@pytest.mark.parametrize("agent_type", ["orca:orca-doctor", "Explore", "general-purpose", "long-running:reader"])
def test_an_agent_of_another_kind_never_holds_the_job_its_brief_names(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], agent_type: str
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    run("job", "add", "design", "--order", "o1", "--goal", "design it", "--agent", "a-1")
    with state.edit("demo") as data:
        data["jobs"]["j2"]["state"] = "done"
    for job_id in ("j1", "j2"):
        kept = tmp_path / "jobs" / f"{job_id}.brief.md"
        kept.parent.mkdir(exist_ok=True)
        kept.write_text(f"Goal: fix it\nDrive: demo, order o1, job {job_id}\n")
        named = stepped(tmp_path, f"Why did the lane whose brief is {kept} stop?", agent_type=agent_type)
        hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Read") | named))
        hook_output(one_line_result, monkeypatch, capsys, json.dumps(agent_stop(f"{job_id} looks stuck", **named)))
    jobs = state.load("demo")["jobs"]
    assert [(job["agent"], job["where"], job["state"]) for job in jobs.values()] == [(None, "local", "running"), ("a-1", "local", "done")]


def test_a_step_never_replaces_a_remote_jobs_dispatch(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--remote")
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(dispatch="ctx_lane0001", workspace="pool-one")
    hook_output(landing_guard, monkeypatch, capsys, json.dumps(bash("git status") | stepped(tmp_path, "Drive: demo, order o1, job j1")))
    job = state.load("demo")["jobs"]["j1"]
    assert (job["agent"], job["where"], job["dispatch"]) == (None, "remote", "ctx_lane0001")


@pytest.mark.parametrize(("agent_type", "holder"), [("codex:codex-wrapper", None), ("long-running:builder", "a-9")])
def test_an_incidents_second_lane_builder_holds_the_codex_job_its_brief_names_and_a_codex_agent_never_does(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], agent_type: str, holder: str | None
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "codex", "--order", "o1", "--goal", "fix it")
    hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Read") | stepped(tmp_path, "Drive: demo, order o1, job j1", agent_type=agent_type)))
    assert state.load("demo")["jobs"]["j1"]["agent"] == holder


def test_a_step_leaves_a_job_another_agent_runs_and_a_stopped_job_the_agent_its_stop_left_on_it_as_they_are(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--agent", "a-1")
    run("job", "add", "reader", "--order", "o1", "--goal", "read it", "--agent", "a-2")
    run("job", "set", "j2", "--state", "stopped")
    lane = str(tmp_path / "session" / "subagents" / "agent-a-9.jsonl")
    hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Edit") | stepped(tmp_path, "Drive: demo, order o1, job j1")))
    stopped_own = stepped(tmp_path, "Drive: demo, order o1, job j2", agent_type="long-running:reader", agent_id="a-2", agent_transcript_path=lane)
    hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Edit") | stopped_own))
    jobs = state.load("demo")["jobs"]
    assert [(job["agent"], job["state"]) for job in jobs.values()] == [("a-1", "running"), ("a-2", "stopped")]


def test_a_fresh_agent_started_on_a_stopped_jobs_brief_runs_it_past_its_builder_slot_wait_and_its_return_is_kept(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    with state.edit("demo") as data:
        place.queue(data["jobs"]["j1"], state.now())
    run("job", "set", "j1", "--state", "stopped")
    hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Edit") | stepped(tmp_path, "Handoff: 681835\nDrive: demo, order o1, job j1")))
    job = state.load("demo")["jobs"]["j1"]
    assert (job["agent"], job["state"], job["waits"]) == ("a-9", "running", [])
    monkeypatch.setattr("sys.stdin", io.StringIO("DONE installed 1.0.549\nCHECK: https://github.com/yasyf/cc-skills/actions/runs/12\n"))
    assert run("job", "report", "j1", "-")[0] == 0
    assert state.load("demo")["jobs"]["j1"]["state"] == "done"


def test_a_fresh_agent_on_a_running_jobs_brief_takes_it_over_once_its_old_session_is_gone(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--agent", "a-1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "after-restart")
    run("start", "demo", "--role", "coordinator")
    hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Edit") | stepped(tmp_path, "Drive: demo, order o1, job j1")))
    data = state.load("demo")
    job = data["jobs"]["j1"]
    assert (job["agent"], job["session"], job["state"]) == ("a-9", "after-restart", "running")
    assert state.agent_job(data, "a-9") == "j1"


def test_resume_from_a_session_that_did_not_start_the_jobs_agent_hands_the_job_to_that_sessions_fresh_agent(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "meta-before-restart")
    run("start", "demo", "--role", "meta")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a verb for owners")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--agent", "a-1")
    run("job", "brief", "j1", "--serves", "drive tooling", "--done-when", "merged", "--where", "yasyf/cc-skills main")
    assert cli.main(["job", "resume", "j1", "go"]) == 2
    assert capsys.readouterr().err == "drive: j1 runs on this Mac as agent a-1; resume it with SendMessage\n"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "meta-session")
    run("start", "demo", "--role", "meta")
    monkeypatch.setattr("sys.stdin", io.StringIO("TAP Approve"))
    code, out = run("job", "resume", "j1", "-")
    handoff = state.drive_dir("demo") / "jobs" / "j1.handoff.md"
    assert (code, out.splitlines()[0]) == (0, f"Read your brief first: {handoff}")
    assert "## Taking over from an agent of another session\n\nThe agent that held this job, a-1 of session meta-before-restart," in handoff.read_text()
    assert handoff.read_text().endswith("The message it never read, which you act on now:\n\nTAP Approve\n")
    assert state.load("demo")["jobs"]["j1"]["agent"] == "a-1"
    hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Edit") | stepped(tmp_path, out.splitlines()[0], session="meta-session")))
    job = state.load("demo")["jobs"]["j1"]
    assert (job["agent"], job["session"], job["handed_to"], job["state"]) == ("a-9", "meta-session", None, "running")


def test_resume_hands_a_job_saved_before_jobs_recorded_where_they_run_and_their_session_to_this_sessions_fresh_agent(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "meta-session")
    run("start", "demo", "--role", "meta")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a verb for owners")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--agent", "a-1")
    run("job", "brief", "j1", "--serves", "drive tooling", "--done-when", "merged", "--where", "yasyf/cc-skills main")
    saved = state.stored(demo)
    for field in ("where", "session"):
        del saved["jobs"]["j1"][field]
    state.store(demo, saved)
    code, out = run("job", "resume", "j1", "PR checks failed")
    handoff = state.drive_dir(demo) / "jobs" / "j1.handoff.md"
    assert (code, out.splitlines()[0]) == (0, f"Read your brief first: {handoff}")
    assert handoff.read_text().endswith("The message it never read, which you act on now:\n\nPR checks failed\n")


def test_an_agent_that_stops_before_any_step_is_recorded_and_sent_back_to_keep_its_line(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "reader", "--order", "o1", "--goal", "read it")
    stop = agent_stop("j1 is answered: the hook never ran", **stepped(tmp_path, "Drive: demo, order o1, job j1", agent_type="long-running:reader"))
    output = json.loads(hook_output(one_line_result, monkeypatch, capsys, json.dumps(stop)))
    assert output["reason"].startswith("Refused on purpose: j1's return is not kept in the drive")
    job = state.load("demo")["jobs"]["j1"]
    assert (job["agent"], job["state"], job["said"]) == ("a-9", "running", None)


def test_an_agent_started_on_a_brief_drive_wrote_is_recorded_on_that_briefs_job(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    code, out = run("job", "brief", "j1", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main")
    assert code == 0
    hook_output(landing_guard, monkeypatch, capsys, json.dumps(bash("git status") | stepped(tmp_path, out.splitlines()[0])))
    job = state.load("demo")["jobs"]["j1"]
    assert (job["agent"], job["state"], job["brief"]) == ("a-9", "running", str(state.drive_dir("demo") / "jobs" / "j1.brief.md"))


def test_a_handler_whose_brief_or_record_names_a_job_never_holds_that_job_and_files_no_return_on_it(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--remote")
    run("job", "brief", "j1", "--serves", "drive tooling", "--done-when", "merged", "--where", "Forge-AI/monorepo dev")
    written = state.load("demo")["jobs"]["j1"]["brief"]
    launch = f"drive job launch j1 {written}"
    monkeypatch.setattr(agents, "record", lambda seq: f"{seq} report root>communicator [job=j1 order=o1] POST j1 kept\n{Path(written).read_text()}")
    for prompt in (f"Context: j1, a remote builder, brief at {written} Drive: demo Deliverable: `{launch}`", "Record: 7"):
        handler = stepped(tmp_path, prompt, agent_type=agents.HANDLER)
        assert hook_output(no_file_tools, monkeypatch, capsys, json.dumps(bash(launch) | handler)) == ""
        assert hook_output(one_line_result, monkeypatch, capsys, json.dumps(agent_stop("RECORDED none; j1 started on a Sprite", **handler))) == ""
        job = state.load("demo")["jobs"]["j1"]
        assert (job["agent"], job["where"], job["said"]) == (None, "remote", None)
    builder = stepped(tmp_path, f"Read your brief first: {written}", agent_type="long-running:builder")
    assert hook_output(no_file_tools, monkeypatch, capsys, json.dumps(bash("git status") | builder)) == ""
    assert state.load("demo")["jobs"]["j1"]["agent"] == "a-9"


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


def test_a_drive_roles_agent_runs_unnamed_with_fable_on_its_designer_alone_and_no_builder_without_scope(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    coordinator = os.environ["CLAUDE_CODE_SESSION_ID"]
    named = started_by_role(coordinator, subagent_type="long-running:builder", name="desk", prompt="Handoff: 661200 Drive: demo Scratch: /s")
    unnamed = {key: value for key, value in named["tool_input"].items() if key != "name"}
    output = json.loads(hook_output(agent_brief, monkeypatch, capsys, json.dumps(named)))
    assert output == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": unnamed}}
    fable = started_by_role(coordinator, subagent_type="long-running:builder", model="fable", prompt="Serves: his order")
    assert agents.shaped(fable) == {key: value for key, value in fable["tool_input"].items() if key != "model"}
    designer = started_by_role(coordinator, subagent_type="long-running:designer", model="fable")
    assert agents.shaped(designer) == designer["tool_input"]
    unscoped = started_by_role(coordinator, subagent_type="long-running:builder", prompt="Order (his words): fix it\nGoal: fix it")
    assert agent_brief.deny_reason(unscoped).startswith("refused: a builder's brief, in its prompt or the brief file the prompt names")
    assert agent_brief.deny_reason(started_by_role(coordinator, subagent_type="long-running:builder", prompt="Handoff: 661200 Drive: demo Scratch: /s")) is None
    tooling = "Order: Drive tooling: x\nServes: drive tooling"
    assert agent_brief.deny_reason(started_by_role(coordinator, subagent_type="long-running:builder", prompt=tooling)) == (
        "refused: a builder serving drive tooling is the meta session's, never the coordinator's: send a defect record to"
        " meta@demo naming the tool and its refusal verbatim, and the meta session's builder takes it over"
    )
    written = tmp_path / "jobs" / "j1.brief.md"
    written.parent.mkdir()
    written.write_text("Order (his words): fix it\nServes: his order\nDrive: demo, order o1, job j1\n")
    assert agent_brief.deny_reason(started_by_role(coordinator, subagent_type="long-running:builder", prompt=f"Read your brief first: {written}")) is None
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    assert "name" not in agents.shaped(started_by_role("talker", subagent_type="long-running:builder", name="desk"))
    assert agent_brief.deny_reason(started_by_role("talker", subagent_type="long-running:builder", prompt=tooling)).startswith(
        "refused: a builder serving drive tooling is the meta session's, never the communicator's"
    )
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "tooler")
    run("start", "demo", "--role", "meta")
    assert agent_brief.deny_reason(started_by_role("talker", subagent_type="long-running:builder", prompt=tooling)) == (
        "refused: a builder serving drive tooling is the meta session's, never the communicator's: send a defect record to"
        " meta@demo naming the tool and its refusal verbatim, and the meta session's builder takes it over"
    )
    assert agent_brief.deny_reason(started_by_role("tooler", subagent_type="long-running:builder", prompt=tooling)) is None
    assert agents.shaped({**named, "agent_id": "a-1"}) == named["tool_input"]
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


def drive_agent(subagent_type: str, prompt: str, model: str | None = None, session: str | None = None) -> dict:
    tool_input = {"subagent_type": subagent_type, "prompt": prompt, "description": "d"} | ({"model": model} if model else {})
    return {"session_id": session or os.environ["CLAUDE_CODE_SESSION_ID"], "tool_name": "Agent", "tool_input": tool_input}


def test_a_build_orders_builder_starts_only_on_a_brief_naming_its_readers_research(
    meta: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    monkeypatch.setattr("sys.stdin", io.StringIO(RESEARCH))
    run("job", "report", "j1", "-")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    folder = tmp_path / "j3"
    bare = f"Order: Drive tooling: copy one store\nServes: drive tooling\nDrive: demo, order o1, job j3\nScratch: {folder}"
    denied = json.loads(hook_output(scratch_folder, monkeypatch, capsys, json.dumps(drive_agent("long-running:builder", bare))))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert denied["hookSpecificOutput"]["permissionDecisionReason"].startswith(
        "refused, no agent started: j3 builds on o1, a build order: its brief carries the line Researched: <its reader's job>"
    )
    assert not folder.exists()
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", f"Researched: j3\n{bare}")) == (
        "refused, no agent started: Researched: j3 names no reader of j3's part of o1 that kept a Researched: line and under it rulings:, exists: and open: lines in its report"
    )
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", f"Researched: j1\n{bare}", "sonnet")) is None
    assert "model" not in agents.shaped(drive_agent("long-running:builder", f"Researched: j1\n{bare}", "sonnet"))
    assert hook_output(scratch_folder, monkeypatch, capsys, json.dumps(drive_agent("long-running:builder", f"Researched: j1\n{bare}"))) == ""
    assert folder.is_dir()
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", f"Handoff: 812\nDrive: demo, order o1, job j3\nScratch: {folder}")) is None
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", bare, session="another-session")) is None
    code, out = run("job", "brief", "j3", "--serves", "drive tooling", "--done-when", "merged", "--where", "yasyf/cc-skills main")
    prompt = out.splitlines()[0]
    assert code == 0
    assert "\nResearched: j1\n" in Path(prompt.removeprefix("Read your brief first: ")).read_text()
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", prompt)) is None
    assert agents.shaped(drive_agent("long-running:builder", prompt, "sonnet")) == {"subagent_type": "long-running:builder", "prompt": prompt, "description": "d"}


def test_an_incident_lanes_brief_carries_the_rule_its_kind_follows_on_a_choice_and_no_line_stopping_it_on_one(demo: str, run: Run) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Alerts", "chat turns fail on sand 0.162.3983", "--incident", "--parts", "fix", "first check")
    run("job", "add", "builder", "--order", "o1", "--part", "fix", "--goal", "ship the fix the codex lane built")
    stopping = "Order: chat turns fail on sand 0.162.3983\nIncident: record 674295\n4. If something is lost, stop and return `BLOCKED <what is lost>` with 2 to 4 options, before any PR\nDrive: demo, order o1, job j1"
    reason = brief.brief_refusal("demo", state.load("demo"), "j1", stopping)
    assert reason.startswith("j1 works on o1, an incident, which a choice never stops, by his words \"fix ur tooling")
    assert reason.endswith(f'its brief\'s Incident: line carries, word for word, "{state.INCIDENT_CHOICE["codex"]}"')
    ruled = stopping.replace("record 674295", f"record 674295; {state.INCIDENT_CHOICE['codex']}")
    assert brief.brief_refusal("demo", state.load("demo"), "j1", ruled).startswith(
        "j1's brief stops it on a choice, \"If something is lost, stop and return `BLOCKED <what is lost>` with 2 to 4 options, before any PR\""
    )
    building = ruled.replace("4. If something is lost, stop and return `BLOCKED <what is lost>` with 2 to 4 options, before any PR", "4. Build the fix and open its PR, then return READY <pr url>")
    assert brief.brief_refusal("demo", state.load("demo"), "j1", building) is None
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", building.replace("job j1", "job j3"))).startswith("refused, no agent started: j3 works on o1, an incident")
    builder = building.replace(state.INCIDENT_CHOICE["codex"], state.INCIDENT_CHOICE["builder"]).replace("job j1", "job j3")
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", builder)) is None
    older = "a choice never stops it: return ASK <path> the moment the fork shows, which puts his card up, and build its recommended option when the coordinator resumes you in that turn; never BLOCKED on a choice"
    kept = builder.replace(state.INCIDENT_CHOICE["builder"], f"{older}; {state.INCIDENT_CHOICE['builder']}")
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", kept)) is None


def test_a_brief_planning_his_touch_id_tap_for_an_aws_action_starts_no_agent_and_a_drive_tooling_brief_naming_one_does(demo: str, run: Run) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Search", "test hybrid search on a pass box")
    run("job", "add", "builder", "--order", "o1", "--goal", "run the test")
    brief = "Order (his words): test hybrid search on a pass box\nGoal: {}\nDrive: demo, order o1, job j1"
    planned = drive_agent("long-running:builder", brief.format("start the pass box and read its key; he taps Touch ID once for cc-sudo"))
    assert scratch_folder.deny_reason(planned).startswith(
        "refused, no agent started: j1's brief plans a sign-in of his: its Goal line asks him to sign in with \"he taps Touch ID\":"
        " this Mac's machine profile runs it with no sign-in, Touch ID or cc-sudo"
    )
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", brief.format("start the pass box and read its key under the machine profile"))) is None
    tooling = brief.format("refuse a card asking him for Touch ID on a pass box key read") + "\nServes: drive tooling"
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", tooling)) is None


def test_a_brief_whose_runs_as_line_writes_a_teams_data_under_break_glass_starts_no_agent_and_one_ruling_it_out_does(demo: str, run: Run) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Search", "re-send the stuck runs")
    run("job", "add", "builder", "--order", "o1", "--goal", "hand repair the stuck runs")
    planned = "Order (his words): re-send the stuck runs\nGoal: hand repair the 128 stuck runs\nRuns as: {}\nDrive: demo, order o1, job j1"
    refused = {
        "this Mac's machine-profile break-glass, by the incidents page: it re-sends each stuck run": "machine-profile",
        "drive break-glass j1 --stack plat/prod with no SSO sign-in": "break-glass",
        "the BreakGlassAccess role on plat-prod": "BreakGlassAccess",
    }
    for line, route in refused.items():
        reason = brief.brief_refusal("demo", state.load("demo"), "j1", planned.format(line))
        assert reason.startswith(f'j1\'s Runs as line writes a team\'s data under "{route}", which runs on this Mac'), line
        assert "`tools/ci cluster-job --cluster <release env> --cronjob <name>" in reason
    passing = [
        "a Job in plat-usw2-prod from the suspended search-wake-repair CronJob via tools/ci cluster-job, never under drive break-glass from this Mac",
        'the search-wake-repair CronJob via tools/ci cluster-job, by his words "repair his stuck ones with machine profile break glass" superseded',
        "none: an infra apply under drive break-glass that writes no team's data",
    ]
    for line in passing:
        assert brief.brief_refusal("demo", state.load("demo"), "j1", planned.format(line)) is None, line
    tooling = planned.format("drive break-glass from this Mac") + "\nServes: drive tooling"
    assert brief.brief_refusal("demo", state.load("demo"), "j1", tooling) is None


def test_a_build_orders_codex_lane_is_added_and_started_only_on_its_readers_research_and_its_immediate_fix_at_once(
    meta: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build", "--parts", "the copy", "immediate fix")
    assert cli.main(["job", "add", "codex", "--order", "o1", "--part", "the copy", "--goal", "build it"]) == 2
    assert "o1 is a build order, so its codex starts only once an Opus reader of its part" in capsys.readouterr().err
    assert run("job", "add", "codex", "--order", "o1", "--part", "immediate fix", "--goal", "stop it") == (0, "j1")
    run("job", "add", "reader", "--order", "o1", "--part", "the copy", "--goal", "research: the copy")
    assert run("job", "add", "codex", "--order", "o1", "--part", "the copy", "--goal", "build it", "--waits", "j2") == (
        0,
        "j4\nj4 waits on j2: start no agent for it until the watch's FREE line says its last wait ended\n"
        'j2 reader running on "the copy": research: the copy\n'
        'j3 builder waiting on "the copy": build what j2\'s research finds: the copy',
    )
    bare = "Order: Drive tooling: copy one store\nDrive: demo, order o1, job {}"
    assert scratch_folder.deny_reason(drive_agent("codex:codex-wrapper", bare.format("j1"))) == (
        "refused, no agent started: j1 is a codex job, whose codex-ask run drive job launch j1 starts itself on its brief,"
        " its reply's state a field codex cannot leave out, so no agent relays it"
    )
    assert brief.brief_refusal("demo", state.load("demo"), "j1", bare.format("j1")) is None
    assert brief.brief_refusal("demo", state.load("demo"), "j4", bare.format("j4")).startswith(
        "j4 builds on o1, a build order: its brief carries the line Researched: <its reader's job>"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(RESEARCH))
    run("job", "report", "j2", "-")
    assert brief.brief_refusal("demo", state.load("demo"), "j4", f"Researched: j2\n{bare.format('j4')}") is None


def test_a_build_orders_designer_on_a_light_model_runs_on_its_own_fable(
    meta: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    monkeypatch.setattr("sys.stdin", io.StringIO(RESEARCH))
    run("job", "report", "j1", "-")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the copy")
    light = drive_agent("long-running:designer", "Researched: j1\nDrive: demo, order o1, job j3", "sonnet")
    assert scratch_folder.deny_reason(light) is None
    assert agents.shaped(light) == {key: value for key, value in light["tool_input"].items() if key != "model"}
    own = drive_agent("long-running:designer", "Researched: j1\nDrive: demo, order o1, job j3", "fable")
    assert agents.shaped(own) == own["tool_input"]
    folder = tmp_path / "j3"
    hook_output(scratch_folder, monkeypatch, capsys, json.dumps(drive_agent("general-purpose", f"Researched: j1\nDrive: demo, order o1, job j3\nScratch: {folder}")))
    assert folder.is_dir()


def test_a_session_starts_no_agent_on_an_order_the_other_session_holds(demo: str, run: Run, tmp_path: Path) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "fix the watch")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    brief = f"Serves: his order\nDrive: demo, order o1, job j1\nScratch: {tmp_path / 'j1'}"
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", brief)) is None
    with state.edit("demo") as data:
        data["orders"]["o1"]["owner"] = "communicator"
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", brief)) == (
        "refused, no agent started: o1 is the communicator's order, session not started: only the communicator adds its jobs,"
        " starts its agents, checks its PRs and posts its reports; send the communicator the step as a handoff record on the drive's record"
        ' tool instead, to "communicator" and attrs {"order": "o1"}'
    )


def test_a_builder_serving_drive_tooling_on_the_coordinators_order_starts_from_the_meta_session_alone(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = os.environ["CLAUDE_CODE_SESSION_ID"]
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Messages", "I told him since you never did, fix that", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the reporter's thread")
    monkeypatch.setattr("sys.stdin", io.StringIO(RESEARCH))
    run("job", "report", "j1", "-")
    run("job", "add", "builder", "--order", "o1", "--goal", "the reporter hears the fix is live")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "tooler")
    run("start", "demo", "--role", "meta")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", coordinator)
    typed = f"Order (his words): I told him since you never did, fix that\nServes: drive tooling\nResearched: j1\nDrive: demo, order o1, job j3\nScratch: {tmp_path / 'j3'}"
    assert agent_brief.deny_reason(drive_agent("long-running:builder", typed)) == (
        "refused: a builder serving drive tooling is the meta session's, never the coordinator's: `drive job brief j3 --serves"
        ' "drive tooling"` gives o1 to the meta session, then send meta@demo a defect record naming j3 and its'
        " `Read your brief first:` line, and the meta session starts it"
    )
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", typed, session="tooler")) == (
        "refused, no agent started: o1 is the coordinator's order, but a builder serving drive tooling is the meta session's alone:"
        ' `drive job brief j3 --serves "drive tooling"` gives o1 to the meta session, then start it again'
    )
    code, out = run("job", "brief", "j3", "--serves", "drive tooling", "--done-when", "merged and installed", "--where", "yasyf/cc-skills main")
    assert code == 0
    assert out.splitlines()[-1] == (
        "o1 is the meta session's order now, since a builder serving drive tooling is its alone: only the meta session starts j3"
        " and its agents and posts its reports; from the coordinator, start no agent on it and send meta@demo a defect"
        " record naming j3 and its `Read your brief first:` line"
    )
    assert state.load("demo")["orders"]["o1"]["owner"] == "meta"
    prompt = out.splitlines()[0]
    for session, starter in ((coordinator, "coordinator"), ("talker", "communicator")):
        refused = drive_agent("long-running:builder", prompt, session=session)
        assert agent_brief.deny_reason(refused) == (
            f"refused: a builder serving drive tooling is the meta session's, never the {starter}'s, and o1 is the meta session's"
            " order, session tooler: send meta@demo a defect record naming j3 and its `Read your brief first:` line, and the"
            " meta session starts it"
        )
        assert scratch_folder.deny_reason(refused).startswith("refused, no agent started: o1 is the meta session's order, session tooler")
    metas = drive_agent("long-running:builder", prompt, session="tooler")
    assert agent_brief.deny_reason(metas) is None
    assert scratch_folder.deny_reason(metas) is None
    assert run("job", "brief", "j3")[1].splitlines()[-1] == "kept from its earlier brief: --serves, --done-when, --where"


def test_an_answer_orders_lane_does_its_own_research_on_opus_when_the_order_is_complex(demo: str, run: Run) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why did the copy stall?", "--kind", "answer", "--complex")
    run("job", "add", "reader", "--order", "o1", "--goal", "answer him")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "which flag sets the cap?", "--kind", "answer")
    run("job", "add", "reader", "--order", "o2", "--goal", "answer him")
    assert scratch_folder.deny_reason(drive_agent("long-running:reader", "Researched: j9\nDrive: demo, order o1, job j1")) == (
        "refused, no agent started: j1 answers o1, an answer order: its one lane does its own research, reading code,"
        " transcripts, records and his answers itself, so its brief carries no Researched: line"
    )
    assert "model" not in agents.shaped(drive_agent("long-running:reader", "Drive: demo, order o1, job j1", "sonnet"))
    for kept in (drive_agent("long-running:reader", "Drive: demo, order o1, job j1", "opus"), drive_agent("long-running:reader", "Drive: demo, order o2, job j2", "sonnet")):
        assert agents.shaped(kept) == kept["tool_input"]


def test_a_light_reader_keeps_its_model_on_an_answer_order_the_communicator_left_unmarked(demo: str, run: Run) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "tell me about the copy", "--kind", "answer")
    run("job", "add", "reader", "--order", "o1", "--goal", "why the copy stalls, who owns the bucket, when it started")
    assert state.load("demo")["orders"]["o1"]["complex"] is None
    light = drive_agent("long-running:reader", "Drive: demo, order o1, job j1", "sonnet")
    assert scratch_folder.deny_reason(light) is None
    assert agents.shaped(light) == light["tool_input"]
    run("order", "set", "o1", "--complex")
    assert "model" not in agents.shaped(light)


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
    assert landing_guard.deny_reason(bash("stack-enqueue 42")) is None


def test_guard_denies_a_held_drive_pr(drive_pr: str, no_calls: list[list[str]]) -> None:
    with state.edit("demo") as data:
        data["prs"][drive_pr]["tap"] = {"label": "Hold", "at": state.stamp(), "head": "abc", "source": "slack"}
    assert "held by him" in landing_guard.deny_reason(bash(f"gh pr merge {PR} --squash"))


def test_guard_allows_an_exempt_drive_pr(drive_pr: str, run: Run, no_calls: list[list[str]]) -> None:
    run("pr", "set", PR, "--exempt", "test-only")
    assert landing_guard.deny_reason(bash("stack-enqueue 42")) is None


def test_guard_allows_a_pr_the_drive_does_not_know(drive_pr: str, no_calls: list[list[str]]) -> None:
    assert landing_guard.deny_reason(bash("gh pr merge 999 -R Forge-AI/monorepo --squash")) is None


def test_a_sub_coordinator_lands_a_tapped_pr_of_its_project_and_leaves_starting_approving_and_rolling_back_cuts_to_the_top(
    drive_pr: str, run: Run, no_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    approve(drive_pr)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "drive-sub")
    assert run("start", "demo", "--role", "coordinator", "--project", "The drive")[0] == 0
    sub, top = {"session_id": "drive-sub"}, {"session_id": "session-under-test"}
    assert [landing_guard.deny_reason(bash("stack-enqueue 42") | session) for session in (sub, top)] == [None, None]
    cuts = ("tools/ci release start api dashboard", "tools/ci release approve releases/2026-11-11/3", "cd /work && ci release rollback releases/2026-11-11/3 api")
    refused = [landing_guard.deny_reason(bash(command) | sub) for command in cuts]
    assert refused[0] == (
        "ci release start: the sub-coordinator for The drive starts, approves and rolls back no production cut; the top coordinator"
        " of drive demo (session session-under-test) does, one at a time. Send it a record at root@demo naming the merged changes"
        " the cut must carry"
    )
    assert [reason.split(":")[0] for reason in refused] == ["ci release start", "ci release approve", "ci release rollback"]
    reads = ("tools/ci release rollback releases/2026-11-11/3 --plan", "tools/ci release status", "tools/ci release watch 4242")
    assert [landing_guard.deny_reason(bash(command) | sub) for command in reads] == [None, None, None]
    assert [landing_guard.deny_reason(bash(command) | top) for command in cuts] == [landing_guard.RELEASE_BY_HAND.format(command=cuts[0]), None, None]


def test_a_drive_job_cancels_no_release_it_started_and_returns_it_held_for_the_coordinator(demo: str, no_calls: list[list[str]]) -> None:
    cancel = "tools/ci release cancel electric-racine"
    job = {"session_id": "session-under-test", "agent_id": "a4da3dca63d05a40d", "agent_type": "long-running:builder"}
    lane = {"session_id": "a-lane-of-its-own", "agent_type": "long-running:builder"}
    assert [landing_guard.deny_reason(bash(cancel) | who) for who in (job, lane)] == [landing_guard.RELEASE_CANCEL] * 2
    assert "return with the release held for the coordinator" in landing_guard.RELEASE_CANCEL
    reads = ("tools/ci release status electric-racine", "tools/ci release watch electric-racine")
    assert [landing_guard.deny_reason(bash(command) | job) for command in reads] == [None, None]
    assert landing_guard.deny_reason(bash(cancel) | {"session_id": "session-under-test"}) is None



BRIEFED_HOTFIX = "tools/ci release hotfix 55225 --only cloudrunner-next,executor"
UNENVED_START = "tools/ci release start cloudrunner-next executor"
J2508_START = "go/ci/.build/ci.dev release start cloudrunner-next executor --from ad20b74ee450e78db034ebde0be3f97bbb4ed7e8 --order 6f774730eb87"


def test_no_drive_session_or_agent_starts_a_release_by_hand_and_each_still_plans_watches_and_approves(demo: str, run: Run, tmp_path: Path, no_calls: list[list[str]]) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "hotfix only the cloud runner and executor images")
    callers = (
        stepped(tmp_path, "Goal: hotfix it\nDrive: demo, order o1, job j1"),
        {"session_id": "session-under-test"},
        {"session_id": "a-lane-of-its-own", "agent_type": "long-running:builder"},
    )
    starts = (BRIEFED_HOTFIX, J2508_START, f"env -u AWS_CONFIG_FILE -u AWS_PROFILE {UNENVED_START}")
    assert [landing_guard.deny_reason(bash(command) | caller) for command in starts for caller in callers] == [
        landing_guard.RELEASE_BY_HAND.format(command=command) for command in (BRIEFED_HOTFIX, J2508_START, UNENVED_START) for _ in callers
    ]
    reads = ("tools/ci release plan --hotfix 55225 --only executor", f"{J2508_START} --dry-run", "tools/ci release watch 4242", "tools/ci release approve releases/2026-11-13/1 --via a-9")
    assert [landing_guard.deny_reason(bash(command) | caller) for command in reads for caller in callers] == [None] * 12
    assert landing_guard.deny_reason(bash(BRIEFED_HOTFIX) | {"session_id": "outside-any-drive"}) is None


J2666_START = "tools/ci release start platform runtime-api-next restate --order 5419ffd"
BUILD = "https://buildkite.com/forge/release/builds/4242"
STARTED = json.dumps({"branch": "releases/2026-11-13/1", "commit": "a6f826f", "build": {"number": 4242, "url": BUILD}, "follow": f"ci release watch {BUILD}"})
CLOCK_PICK = {
    "id": "5419ffd" + "0" * 33,
    "title": "How should the merged clock and its CLOCK_URL reach production?",
    "body": "Wait for green dev tests and the landing order, then one normal release with clock schedules",
    "tags": ["scope:durable"],
}


def released(demo: str, script: str) -> Path:
    checkout = Path(state.load(demo)["roles"]["coordinator"]["cwd"])
    ci = checkout / "tools" / "ci"
    ci.parent.mkdir(exist_ok=True)
    ci.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{checkout}/started"\n{script}\n')
    ci.chmod(0o755)
    return checkout / "started"


def test_drive_job_add_release_starts_it_on_his_answer_from_the_tops_checkout_and_briefs_the_job_on_its_build(
    demo: str, run: Run, tmp_path: Path, no_calls: list[list[str]], recorded: Answers, jev: Jev, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "release the clock")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the clock")
    started = released(demo, f"echo 'waiting on the operator' >&2\necho '{STARTED}'\nexec sleep 600")
    recorded.shown = {answer["id"][:7]: answer for answer in (CLOCK_PICK, PR_TAP)}
    add = ["job", "add", "builder", "--order", "o1", "--goal", "release the merged clock"]
    refusals = (
        (
            J2666_START.replace("5419ffd", "ac61348"),
            f'refused: `--order ac61348` names his answer to "{PR_TAP["title"]}", "{PR_TAP["body"]}", and Jev does not read it as approving this release. {brief.RELEASE_APPROVAL}',
        ),
        (J2666_START.removesuffix(" --order 5419ffd"), f"--release names no --order. {brief.RELEASE_APPROVAL}"),
        (
            f"cd go && {J2666_START}",
            f"--release `cd go && {J2666_START}` is not one `tools/ci release start` or `tools/ci release hotfix` alone: name the one release to start, with its flags as it runs",
        ),
    )
    for command, why in refusals:
        assert cli.main([*add, "--release", command]) == 2
        assert capsys.readouterr().err.strip() == f"drive: refused, no job added: {why}"
    assert not started.exists()
    jev.picks("5419ffd")
    code, out = run(*add, "--release", J2666_START)
    assert code == 0
    assert started.read_text() == "release start platform runtime-api-next restate --order 5419ffd --json\n"
    written = state.drive_dir(demo) / "jobs" / "j2.brief.md"
    assert out.splitlines()[:3] == [
        "j2",
        f"j2 started `{J2666_START}` as {BUILD} and left it running: its agent follows it with `ci release watch {BUILD}` and reads its approve hold",
        f"Read your brief first: {written}",
    ]
    assert jev.asked[-1]["state"] == {
        "action": f"start this production release: `{J2666_START}`",
        "answers": [{"id": "5419ffd", "asked": CLOCK_PICK["title"], "said": CLOCK_PICK["body"]}],
    }
    assert run("job", "brief", "j2", "--serves", "his order", "--done-when", "live", "--where", "Forge-AI/monorepo dev")[0] == 0
    assert f"\nRelease: {J2666_START}\nRelease build: {BUILD}\n" in written.read_text()
    assert landing_guard.deny_reason(bash(J2666_START) | stepped(tmp_path, out)) == landing_guard.RELEASE_BY_HAND.format(command=J2666_START)
    released(demo, """cat <<'EOF'\n{\n  "refused": "a freeze holds platform",\n  "code": "frozen"\n}\nEOF\nexit 1""")
    assert cli.main([*add, "--release", J2666_START]) == 2
    assert capsys.readouterr().err.strip() == "drive: j3 added and stopped, since its release did not start: a freeze holds platform"
    job = state.load(demo)["jobs"]["j3"]
    assert (job["state"], job["said"], job["release_build"]) == ("stopped", "its release did not start: a freeze holds platform", None)
    assert not (state.drive_dir(demo) / "jobs" / "j3.brief.md").exists()


def test_the_top_starts_and_hears_the_release_job_on_a_sub_coordinators_order_and_the_sub_coordinator_none_of_it(
    demo: str, run: Run, no_calls: list[list[str]], recorded: Answers, jev: Jev, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Agents", "release the clock", "--parts", "clock", "release")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "platform-1")
    run("start", "demo", "--role", "coordinator", "--project", "Platform")
    run("job", "add", "builder", "--order", "o1", "--part", "clock", "--goal", "build the clock")
    held = (
        "a job starting a production cut on o1 is the top coordinator's, session session-under-test, whatever order it serves, since the top"
        " alone starts, approves and rolls back cuts: send it a release record at root@demo naming the merged changes the cut must carry"
    )
    cut = ["job", "add", "builder", "--order", "o1", "--part", "release", "--goal", "release the merged clock", "--release", J2666_START]
    recorded.shown = {CLOCK_PICK["id"][:7]: CLOCK_PICK}
    jev.picks("5419ffd")
    assert cli.main(cut) == 2
    assert capsys.readouterr().err.strip() == f"drive: refused, no job added: {held}"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-under-test")
    assert cli.main(["job", "add", "builder", "--order", "o1", "--part", "release", "--goal", "build it"]) == 2
    assert "only the sub-coordinator for Platform adds its jobs" in capsys.readouterr().err
    released(demo, f"echo '{STARTED}'")
    assert run(*cut)[1].splitlines()[0] == "j2"
    where = ("--serves", "his order", "--done-when", "live", "--where", "Forge-AI/monorepo dev")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "platform-1")
    assert cli.main(["job", "brief", "j2", *where]) == 2
    assert capsys.readouterr().err.strip() == f"drive: j2: {held}"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-under-test")
    code, out = run("job", "brief", "j2", *where)
    assert code == 0
    assert [scratch_folder.deny_reason(drive_agent("long-running:builder", out, session=session)) for session in ("session-under-test", "platform-1")] == [
        None,
        f"refused, no agent started: {held}",
    ]
    data = state.load("demo")
    data["jobs"]["j2"] |= {"agent": "a-9", "session": "session-under-test"}
    assert [state.job_lane(data, data["jobs"][job_id]) for job_id in ("j1", "j2")] == ["platform", "root"]
    assert state.agent_lane(data, data["jobs"]["j2"]) == "root"
    watchers = (watch.Watcher(demo, offset=0), watch.Watcher(demo, offset=0, project="Platform"))
    assert [[watch.heard_job(watcher, data, data["jobs"][job_id]) for job_id in ("j1", "j2")] for watcher in watchers] == [[False, True], [True, False]]


RELEASE_PICK ={"id": "5d0c4a1" + "0" * 33, "title": "Release the memory fix to production now?", "body": "Release now (Recommended)", "tags": ["scope:durable"]}
INCIDENT_ORDER = {"id": "9e1f2b3" + "0" * 33, "title": "sand runs fail on core-usw2", "body": "runs are failing, fix it", "tags": ["scope:durable", "drive:demo", "incident"]}
PR_TAP = {"id": "ac61348" + "0" * 33, "title": "Tap: #50203, canary diagnostics on the search", "body": "Approve at 4aca314", "tags": ["scope:durable"]}


def test_a_release_starts_on_order_naming_an_incidents_answer_or_one_jev_reads_as_his_pick_to_release(demo: str, recorded: Answers, jev: Jev) -> None:
    recorded.shown = {answer["id"][:7]: answer for answer in (RELEASE_PICK, INCIDENT_ORDER, PR_TAP)}
    start = "tools/ci release start sand --only sand --emergency --order {}"
    jev.picks("5d0c4a1")
    assert brief.approval_refusal(start.format("5d0c4a1"), state.load("demo")) is None
    [asked] = jev.asked
    assert asked["state"] == {
        "action": f"start this production release: `{start.format('5d0c4a1')}`",
        "answers": [{"id": "5d0c4a1", "asked": RELEASE_PICK["title"], "said": RELEASE_PICK["body"]}],
    }
    assert list(asked["questions"]["decides"]["label"]["options"]) == ["none", "5d0c4a1"]
    assert brief.approval_refusal(start.format("9e1f2b3"), state.load("demo")) is None
    assert len(jev.asked) == 1
    jev.picks("none")
    assert brief.approval_refusal(start.format("ac61348"), state.load("demo")) == (
        'refused: `--order ac61348` names his answer to "Tap: #50203, canary diagnostics on the search", "Approve at 4aca314", and Jev'
        f" does not read it as approving this release. {brief.RELEASE_APPROVAL}"
    )
    jev.answer = None
    assert brief.approval_refusal(start.format("ac61348"), state.load("demo")).startswith(
        "refused: Jev did not say whether his answer ac61348 approves this release (no jev decision within 20s)"
    )
    assert len(jev.asked) == 3


RELEASE_HOLD = {
    "id": "15c4de1" + "0" * 33,
    "title": "Are releases frozen now that a teammate set a freeze in #releases?",
    "body": "we dont have a literal freeze on but no releases until I say we can again, only I can give exceptions",
    "tags": ["scope:durable", "drive:demo", "release-hold"],
    "created_at": "2026-11-13T19:09:39Z",
}
HOLD_LINK = "https://acme.slack.com/archives/D1/p1794600579000100"
SSQL_EXCEPTION = {
    "id": "8b2e4f7" + "0" * 33,
    "title": "Release the SandSQL fix to production?",
    "body": "also, we are lifting the freeze just so we can release this. if we release ssql, release it whole",
    "tags": ["scope:durable", "drive:demo"],
    "created_at": "2026-11-13T19:40:00Z",
}
EARLIER = "2026-11-12T10:00:00Z"
HELD_VERBS = (
    "tools/ci release resume 15495",
    "tools/ci release approve releases/2026-11-13/2 --via codex",
    "cd /work && ci release rollback releases/2026-11-13/2 sandsql",
    "tools/ci bk rerun release 15495 --unit sandsql",
    "tools/ci bk api -X PUT organizations/forge/pipelines/release/builds/15495/jobs/0190a1b2/unblock",
    "tools/ci bk api --X=POST organizations/forge/pipelines/release/builds -d '{\"commit\":\"HEAD\"}'",
    "drive break-glass j1 --stack sandsql/plat-usw2-prod --reason stuck -- tools/ci release resume 15495",
    "timeout 600 tools/ci release resume 15495",
)


def held(release: str, why: str) -> str:
    return brief.RELEASE_HELD.format(said=RELEASE_HOLD["body"], where=f"15c4de1, {HOLD_LINK}", hold="15c4de1", release=release, why=why)


def test_while_he_holds_every_release_a_drive_runs_none_but_on_his_exception_recorded_since(demo: str, no_calls: list[list[str]], recorded: Answers, jev: Jev) -> None:
    top = {"session_id": "session-under-test"}
    with state.edit("demo") as data:
        data["answers"]["15c4de1"] = {"words": RELEASE_HOLD["body"], "link": HOLD_LINK, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {}}
    recorded.listed = [RELEASE_HOLD, SSQL_EXCEPTION, RELEASE_PICK | {"created_at": EARLIER}, INCIDENT_ORDER | {"created_at": EARLIER}, PR_TAP | {"created_at": "2026-11-13T19:50:00Z", "tags": ["scope:durable", "kind:approval"]}]
    recorded.shown = {answer["id"][:7]: answer for answer in recorded.listed}
    start = "tools/ci release start sandsql --order {}"
    data = state.load("demo")
    assert [brief.approval_refusal(start.format(order), data) for order in ("5d0c4a1", "9e1f2b3")] == [
        held(start.format("5d0c4a1"), '`--order 5d0c4a1` names his answer to "Release the memory fix to production now?", recorded before the hold'),
        held(start.format("9e1f2b3"), '`--order 9e1f2b3` names his answer to "sand runs fail on core-usw2", recorded before the hold'),
    ]
    assert jev.asked == []
    jev.picks("8b2e4f7")
    assert brief.approval_refusal(start.format("8b2e4f7"), data) is None
    assert jev.asked[-1]["state"] == {
        "action": f"start this production release: `{start.format('8b2e4f7')}`",
        "answers": [{"id": "8b2e4f7", "asked": SSQL_EXCEPTION["title"], "said": SSQL_EXCEPTION["body"]}],
    }
    asked = len(jev.asked)
    refused = [landing_guard.deny_reason(bash(command) | top) for command in HELD_VERBS]
    assert refused[0] == held("tools/ci release resume 15495", "it names no `--order`, and neither does a `Release:` line of its job's brief")
    assert [reason.startswith(f'refused: he holds every release, by his words "{RELEASE_HOLD["body"]}" (15c4de1, {HOLD_LINK})') for reason in refused] == [True] * len(HELD_VERBS)
    assert len(jev.asked) == asked
    jev.picks("none")
    assert brief.approval_refusal(start.format("8b2e4f7"), data) == held(
        start.format("8b2e4f7"), 'Jev does not read his answer 8b2e4f7 to "Release the SandSQL fix to production?" as approving it'
    )
    by_hand = ("tools/ci release hotfix 55960 --only executor", "drive break-glass j1 --stack sandsql/plat-usw2-prod --reason stuck -- tools/ci release start sandsql")
    assert [landing_guard.deny_reason(bash(command) | top) for command in by_hand] == [
        landing_guard.RELEASE_BY_HAND.format(command=command) for command in ("tools/ci release hotfix 55960 --only executor", "tools/ci release start sandsql")
    ]
    jev.answer = None
    assert brief.approval_refusal(start.format("8b2e4f7"), data) == held(
        start.format("8b2e4f7"), "Jev did not say whether his answer 8b2e4f7 approves it (no jev decision within 20s); run it again once Jev answers"
    )
    reads = (
        "tools/ci release start sandsql --dry-run",
        "tools/ci release plan sandsql",
        "tools/ci release status",
        "tools/ci release watch 15495",
        "tools/ci bk api organizations/forge/pipelines/release/builds/15495",
        "tools/ci bk api -X GET organizations/forge/pipelines/release/builds/15495",
        "tools/ci bk api -X PUT organizations/forge/pipelines/forge-monorepo/builds/7/jobs/0190a1b2/unblock",
        "tools/ci bk rerun forge-monorepo 7 --unit test",
    )
    assert [landing_guard.deny_reason(bash(command) | top) for command in reads] == [None] * len(reads)
    assert landing_guard.deny_reason(bash(start.format("5d0c4a1"))) is None
    recorded.listed = recorded.listed[1:]
    jev.picks("5d0c4a1")
    assert [brief.approval_refusal(start.format("5d0c4a1"), data), landing_guard.deny_reason(bash(HELD_VERBS[0]) | top)] == [None, None]


def test_a_job_runs_the_release_its_brief_names_on_his_exception_while_he_holds_every_release(
    demo: str, tmp_path: Path, no_calls: list[list[str]], recorded: Answers, jev: Jev
) -> None:
    recorded.listed = [RELEASE_HOLD, SSQL_EXCEPTION, RELEASE_PICK | {"created_at": EARLIER}]
    recorded.shown = {answer["id"][:7]: answer for answer in recorded.listed}
    release = "tools/ci release start sandsql --order 8b2e4f7"
    step = stepped(tmp_path, f"Release: {release}\nRelease build: {BUILD}\nDrive: demo, order o1, job j1")
    jev.picks("8b2e4f7")
    own = ("tools/ci release approve releases/2026-11-13/2 --via codex", "tools/ci release resume 15495", "tools/ci bk api -X PUT organizations/forge/pipelines/release/builds/15495/jobs/0190a1b2/unblock")
    assert [landing_guard.deny_reason(bash(command) | step) for command in own] == [None] * len(own)
    assert landing_guard.deny_reason(bash(release) | step) == landing_guard.RELEASE_BY_HAND.format(command=release)
    assert {asked["state"]["action"] for asked in jev.asked} == {f"start this production release: `{release}`"}
    jev.picks("none")
    assert landing_guard.deny_reason(bash(own[0]) | step) == brief.RELEASE_HELD.format(
        said=RELEASE_HOLD["body"], where="15c4de1", hold="15c4de1", release=release, why='Jev does not read his answer 8b2e4f7 to "Release the SandSQL fix to production?" as approving it'
    )
    stale = "tools/ci release start sandsql --order 5d0c4a1"
    briefed = stepped(tmp_path, f"Release: {stale}\nDrive: demo, order o1, job j1")
    assert landing_guard.deny_reason(bash(own[0]) | briefed) == brief.RELEASE_HELD.format(
        said=RELEASE_HOLD["body"], where="15c4de1", hold="15c4de1", release=stale, why='`--order 5d0c4a1` names his answer to "Release the memory fix to production now?", recorded before the hold'
    )
    assert landing_guard.deny_reason(bash("tools/ci bk cancel release 15495") | briefed) == landing_guard.RELEASE_CANCEL


def test_a_codex_lane_runs_a_release_verb_only_on_the_exception_its_jobs_kept_brief_names_while_he_holds_every_release(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_calls: list[list[str]], recorded: Answers, jev: Jev
) -> None:
    recorded.listed = [RELEASE_HOLD, SSQL_EXCEPTION]
    recorded.shown = {answer["id"][:7]: answer for answer in recorded.listed}
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "release the fix")
    run("job", "add", "codex", "--order", "o1", "--goal", "release the fix")
    kept = tmp_path / "j1.md"
    kept.write_text("Drive: demo, order o1, job j1\n")
    with state.edit("demo") as data:
        data["jobs"]["j1"]["brief"] = str(kept)
    monkeypatch.setenv(state.OWN_JOB_ENV, "j1")
    monkeypatch.setenv(state.DRIVE_ENV, "demo")
    lane = bash(HELD_VERBS[0]) | {"session_id": "a-codex-session", "transcript_path": str(tmp_path / "rollout.jsonl")}
    refused = brief.RELEASE_HELD.format(said=RELEASE_HOLD["body"], where="15c4de1", hold="15c4de1", release=HELD_VERBS[0], why="it names no `--order`, and neither does a `Release:` line of its job's brief")
    assert landing_guard.deny_reason(lane) == refused
    assert json.loads(hook_output(landing_guard, monkeypatch, capsys, json.dumps(lane))) == landing_guard.denial(refused)
    assert landing_guard.deny_reason(lane | {"tool_input": {"command": "tools/ci release cancel releases/2026-11-13/2"}}) == landing_guard.RELEASE_CANCEL
    assert jev.asked == []
    release = "tools/ci release start sandsql --order 8b2e4f7"
    kept.write_text(f"Release: {release}\nDrive: demo, order o1, job j1\n")
    jev.picks("8b2e4f7")
    assert landing_guard.deny_reason(lane) is None
    assert [asked["state"]["action"] for asked in jev.asked] == [f"start this production release: `{release}`"]


def test_drive_job_add_starts_no_release_his_hold_holds_and_starts_his_exception(demo: str, run: Run, recorded: Answers, jev: Jev, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "release the fix")
    recorded.listed = [RELEASE_HOLD, SSQL_EXCEPTION, RELEASE_PICK | {"created_at": EARLIER}]
    recorded.shown = {answer["id"][:7]: answer for answer in recorded.listed}
    jev.picks("5d0c4a1")
    stale = "tools/ci release start sandsql --order 5d0c4a1"
    why = '`--order 5d0c4a1` names his answer to "Release the memory fix to production now?", recorded before the hold'
    assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", "release the fix", "--release", stale]) == 2
    assert capsys.readouterr().err.strip() == "drive: refused, no job added: " + brief.RELEASE_HELD.format(said=RELEASE_HOLD["body"], where="15c4de1", hold="15c4de1", release=stale, why=why)
    jev.picks("8b2e4f7")
    released(demo, f"echo '{STARTED}'")
    excepted = "tools/ci release start sandsql --order 8b2e4f7"
    assert run("job", "add", "builder", "--order", "o1", "--goal", "release the fix", "--release", excepted)[1].splitlines()[:2] == [
        "j1",
        f"j1 started `{excepted}` as {BUILD} and left it running: its agent follows it with `ci release watch {BUILD}` and reads its approve hold",
    ]


HANDOVER_PICK = {
    "id": "7fe7efc" + "0" * 33,
    "title": "Should your normal clock release hand SandSQL's primaries over, since a footer pulls sandsql into it?",
    "body": "Yes: the normal release hands each SandSQL primary over on its own node, and they come back following in process",
    "tags": ["scope:durable"],
}
POLICY_PICK = {
    "id": "cb96889" + "0" * 33,
    "title": "When should the production release carrying the clock, CLOCK_URL and clock schedules start?",
    "body": "Start after the network policy fix merges: one release ships clock, CLOCK_URL, schedules and policy; timers fire",
    "tags": ["scope:durable"],
}
QUEUE_PICK = {
    "id": "6881a3a" + "0" * 33,
    "title": "PR #46370 (a release order that contradicts an earlier one is now dropped) is green and approved. Should I put it in the merge queue?",
    "body": "Enqueue it (Recommended)",
    "tags": ["scope:ephemeral"],
}


def test_the_clock_release_starts_on_his_picks_of_how_when_and_with_what_it_ships_and_never_on_his_pick_to_enqueue_a_pr(demo: str, recorded: Answers, jev: Jev) -> None:
    picks = (CLOCK_PICK, HANDOVER_PICK, POLICY_PICK)
    recorded.shown = {answer["id"][:7]: answer for answer in (*picks, QUEUE_PICK)}
    for pick in picks:
        jev.picks(pick["id"][:7])
        assert brief.approval_refusal(J2666_START.replace("5419ffd", pick["id"][:7]), state.load("demo")) is None
    assert [asked["state"]["answers"] for asked in jev.asked] == [[{"id": pick["id"][:7], "asked": pick["title"], "said": pick["body"]}] for pick in picks]
    jev.picks("none")
    assert brief.approval_refusal(J2666_START.replace("5419ffd", "6881a3a"), state.load("demo")) == (
        f'refused: `--order 6881a3a` names his answer to "{QUEUE_PICK["title"]}", "Enqueue it (Recommended)", and Jev does not read it'
        f" as approving this release. {brief.RELEASE_APPROVAL}"
    )


def test_guard_denies_admin_on_forge_even_when_tapped(drive_pr: str, no_calls: list[list[str]]) -> None:
    approve(drive_pr)
    reason = landing_guard.deny_reason(bash(f"gh pr merge {PR} --squash --admin"))
    assert reason == landing_guard.ADMIN_REASON


def test_guard_allows_a_repo_outside_forge(drive_pr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    origin_is(monkeypatch, "https://github.com/yasyf/cc-slack.git")
    assert landing_guard.deny_reason(bash("gh pr merge 42 --squash --admin")) is None


def test_guard_lets_a_cc_skills_merge_run_past_its_ci_with_no_github_read(drive_pr: str, no_calls: list[list[str]]) -> None:
    assert landing_guard.deny_reason(bash("gh pr merge 4153 -R yasyf/cc-skills --squash --admin")) is None


def test_guard_lets_a_landing_run_with_a_warning_when_github_does_not_answer_its_read(
    drive_pr: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    floor = "ccx: GitHub rate limit floor: user graphql has 550 of 5000 left, under the floor of 1000, until 2:53AM"

    def floored(args: list[str], cwd: str) -> str:
        raise landing_guard.github.GithubError(floor)

    monkeypatch.setattr(landing_guard, "run", floored)
    output = json.loads(hook_output(landing_guard, monkeypatch, capsys, json.dumps(bash("gh pr merge my-branch -R Forge-AI/monorepo --squash"))))["hookSpecificOutput"]
    assert "permissionDecision" not in output
    assert output["additionalContext"].startswith("landing_guard let this run unchecked, since a read it needed failed")
    assert output["additionalContext"].endswith(f"The read said: {floor}")


def test_guard_warns_rather_than_raising_when_git_cannot_name_the_repo(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    stdin = json.dumps(bash("gh pr merge 42 --squash", cwd=str(tmp_path)))
    output = json.loads(hook_output(landing_guard, monkeypatch, capsys, stdin))["hookSpecificOutput"]
    assert "not a git repository" in output["additionalContext"]


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
        run("pr", "add", url, "--job", "j1", *LEAD)
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


def updated_at(name: str, key: str, moment: str) -> None:
    with state.edit(name) as data:
        data["prs"][key]["updated"] = moment


def copy_of_demo(run: Run, name: str) -> None:
    run("start", name, "--role", "coordinator")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it again", "--drive", name)
    run("job", "add", "builder", "--order", "o1", "--goal", "again", "--drive", name)
    run("pr", "add", PR, "--job", "j1", "--drive", name, *LEAD)


def test_guard_judges_a_shared_pr_by_the_entry_updated_last_not_the_drive_started_last(
    drive_pr: str, run: Run, no_calls: list[list[str]]
) -> None:
    approve(drive_pr)
    copy_of_demo(run, "copy")
    with state.edit("copy") as data:
        data["roles"]["coordinator"]["session"] = "the-copy's-session"
    updated_at("demo", drive_pr, "2026-11-11T00:42:12-08:00")
    updated_at("copy", drive_pr, "2026-11-11T00:20:00-08:00")
    assert landing_guard.deny_reason(bash("stack-enqueue 42")) is None
    updated_at("copy", drive_pr, "2026-11-11T00:43:00-08:00")
    assert landing_guard.deny_reason(bash("stack-enqueue 42")).startswith(f"{drive_pr}: no tap from him yet")


def test_guard_judges_a_shared_pr_by_the_sessions_own_drive_first(
    drive_pr: str, run: Run, no_calls: list[list[str]]
) -> None:
    copy_of_demo(run, "copy")
    approve(drive_pr)
    with state.edit("copy") as data:
        data["roles"]["coordinator"]["session"] = "the-copy's-session"
    updated_at("demo", drive_pr, "2026-11-11T00:20:00-08:00")
    updated_at("copy", drive_pr, "2026-11-11T00:43:00-08:00")
    mine = bash("stack-enqueue 42") | {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"]}
    assert landing_guard.deny_reason(mine) is None
    assert landing_guard.deny_reason(mine | {"session_id": "the-copy's-session"}).startswith(f"{drive_pr}: no tap from him yet")


@pytest.mark.parametrize(
    "command",
    [
        "gh api -X PUT repos/Forge-AI/monorepo/pulls/42/merge-async -f sha=abc -f merge_method=squash -f merge_action=direct_merge",
        "gh api --method=put /repos/Forge-AI/monorepo/pulls/42/merge -f merge_method=squash",
    ],
)
def test_guard_holds_a_merge_through_the_api_to_his_tap_and_the_incident_exemption(
    drive_pr: str, no_calls: list[list[str]], command: str
) -> None:
    assert landing_guard.deny_reason(bash(command)).startswith(f"{drive_pr}: no tap from him yet")
    approve(drive_pr)
    assert landing_guard.deny_reason(bash(command)) is None
    driving = bash(command) | {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"]}
    assert landing_guard.deny_reason(driving) == landing_guard.QUEUE_REASON.format(key=drive_pr)
    with state.edit("demo") as data:
        data["prs"][drive_pr]["exempt"] = "incident fix"
    assert landing_guard.deny_reason(driving) is None


def test_guard_lets_an_api_read_of_a_merge_through(drive_pr: str, no_calls: list[list[str]]) -> None:
    assert landing_guard.deny_reason(bash("gh api repos/Forge-AI/monorepo/pulls/42/merge")) is None
    assert landing_guard.deny_reason(bash("gh api -X PUT repos/Forge-AI/monorepo/pulls/42/requested_reviewers")) is None


def test_guard_reads_gh_repo_from_the_session_environment(
    drive_pr: str, no_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_REPO", "Forge-AI/monorepo")
    assert landing_guard.deny_reason(bash("gh pr merge 42 --admin")) == landing_guard.ADMIN_REASON
    assert (landing_guard.judged_reason(bash("gh pr merge 42 --admin")), landing_guard.guarded_reason(bash("gh pr merge 42 --admin"))) == (
        None,
        landing_guard.ADMIN_REASON,
    )


def test_gh_repo_does_not_select_the_repo_for_ccx(drive_pr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    origin_is(monkeypatch, "https://github.com/yasyf/cc-slack.git")
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


def test_bash_posts_no_drive_ask_since_drive_job_ask_posts_it(run: Run, demo: str, no_calls: list[list[str]]) -> None:
    session = os.environ["CLAUDE_CODE_SESSION_ID"]
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "close the idle sessions")
    kept = state.drive_dir(demo) / "asks" / "j1-0.md"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("ASK Close these sessions?\nThe idle ones hold a slot each.\n")
    with state.edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "close them", [], state.stamp())
        data["jobs"]["j1"]["questions"] = [state.new_question(str(kept), "Close these sessions?", state.stamp())]
    for command in (
        "cci post --kind ask --lane root --to communicator@demo 'Close these sessions?'",
        "cd /work && cci post --lane root --kind=ASK --attr job=j1 'Close these sessions?'",
        "zsh -c \"cci post --kind ask 'Close these sessions?'\"",
        f"cci post --kind ask --attr ask={kept} --attr job=j1 --path {kept}",
        f"cci post --kind ask --attr ask={kept} --attr job=j1 --attr incident=true 'Close these sessions?'",
    ):
        assert landing_guard.deny_reason(asked_from_bash(command, session)) == settled.HAND_ASK
    assert landing_guard.deny_reason(asked_from_bash("cci post --kind report 'merged'", session)) is None
    assert landing_guard.deny_reason(asked_from_bash("cci post --kind ask 'Close these sessions?'", "another-session")) is None


def test_a_bash_report_naming_an_attr_a_separator_or_one_letter_from_one_the_drive_reads_is_refused_naming_it(demo: str, no_calls: list[list[str]]) -> None:
    session = os.environ["CLAUDE_CODE_SESSION_ID"]
    catch_up = "cci post --drive demo --kind report --lane root --to communicator@demo --attr 'catchup=1:46 AM-3:10 AM' 'POST his catch-up'"
    reason = landing_guard.deny_reason(asked_from_bash(catch_up, session))
    assert "no `catchup` attr on a report" in reason and "`--attr catch_up=true`" in reason
    closes = "zsh -c \"cci post --kind=REPORT --lane root --attr closes=o1 'FYI j1 merged'\""
    assert "`--attr close=" in landing_guard.deny_reason(asked_from_bash(closes, session))
    assert landing_guard.deny_reason(asked_from_bash("cci post --kind report --lane root --attr catch_up=true 'POST his catch-up'", session)) is None
    assert landing_guard.deny_reason(asked_from_bash(catch_up, "another-session")) is None


def test_a_bash_post_report_typing_when_past_work_happened_is_refused_and_a_record_between_sessions_typing_it_passes(
    demo: str, no_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    session = os.environ["CLAUDE_CODE_SESSION_ID"]
    monkeypatch.setattr(limits, "now", lambda: WRITTEN_AT.replace(hour=22, minute=35))
    typed = (
        "cci post --kind report --lane root --to communicator@demo --attr progress=true"
        " 'POST A fix job with apply authority and a diagnosis job started at 10:33 PM; next line by 10:38 PM.'"
    )
    assert landing_guard.deny_reason(asked_from_bash(typed, session)).startswith('refused: "10:33 PM" is a time typed for work already done')
    assert landing_guard.deny_reason(asked_from_bash(typed.replace("at 10:33 PM", "at {started:j1}"), session)) is None
    assert landing_guard.deny_reason(asked_from_bash(typed.replace("'POST ", "'FYI "), session)) is None
    defect = "cci post --kind defect --lane root --to meta@demo 'record refused his 3:40 PM pick, nothing posted'"
    assert landing_guard.deny_reason(asked_from_bash(defect, session)) is None


def test_a_communicators_bash_cci_post_naming_a_lane_other_than_the_session_hearing_its_order_is_refused(
    run: Run, demo: str, no_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", demo, "--role", "coordinator", "--project", "Iris")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", demo, "--role", "communicator")
    handler = {"session_id": "talker", "agent_id": "a1", "agent_type": "long-running:handler"}
    held = "cci post --kind refused --lane communicator --to root@demo --resolves 8 --attr job=j1 'the report names no card'"
    assert landing_guard.deny_reason(bash(held) | handler) == (
        "This refused record goes to iris@demo: a defect, and the communicator's owner record about the drive, to the meta"
        " session, the communicator's answer, handoff or refused record naming no job to the coordinator that wrote"
        " the record it answers, its other owner, answer, handoff or refused record, and any other session's handoff,"
        " to the session holding the order it serves; post it again with `--to iris@demo`, or on the cci `post` tool, whose hook addresses it"
    )
    refused = "zsh -c \"cci post --kind=refused --lane communicator --to=root@demo,owner --attr job=j1 'cc-slack refused the card'\""
    assert "`--to iris@demo --to owner`" in landing_guard.deny_reason(bash(refused) | handler)
    sent = (
        "cci post --kind refused --lane communicator --to iris@demo --resolves 8 --attr job=j1 'the report names no card'",
        "cci post --kind report --lane communicator --attr close=o1 'FYI j1 merged'",
    )
    assert [landing_guard.deny_reason(bash(command) | handler) for command in sent] == [None, None]
    unparted = landing_guard.deny_reason(bash("cci post --kind owner --lane communicator --to root@demo --attr 'about=his work' 'a new order'") | handler)
    assert unparted.startswith("refused, nothing posted: his words go out on the drive's `record` tool, kind owner")
    unsaid = landing_guard.deny_reason(bash("cci post --kind owner --lane communicator --to root@demo 'a new order'") | handler)
    assert unsaid.startswith('refused, nothing posted: an owner record names what his words are about, about "drive" for drive tooling')
    for to in ("meta@demo", "root@demo"):
        unordered = landing_guard.deny_reason(bash(f"cci post --kind owner --lane communicator --to {to} --attr about=drive 'the dashboard drops my cards'") | handler)
        assert unordered.startswith("refused, nothing posted: his words about the drive go out on the drive's `record` tool")
    assert landing_guard.deny_reason(bash(held) | {"session_id": "iris-1"}) is None
