import io
import json
import os
from collections.abc import Callable
from pathlib import Path

import agent_brief
import landing_guard
import no_file_tools
import no_slack_reads
import one_line_result
import pre_compact
import pytest
import quiet_turn
import scratch_folder
import session_start
from conftest import RESEARCH, WatchProcess
from drive import state

Run = Callable[..., tuple[int, str]]
PR = "https://github.com/Forge-AI/monorepo/pull/42"
ORIGIN = "git@github.com:Forge-AI/monorepo.git"


@pytest.fixture
def drive_pr(run: Run, demo: str) -> str:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
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


def test_session_start_gives_each_role_its_own_context(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    watch_process.ensured.clear()
    output = session_start.respond({"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "source": "resume"})
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    context = output["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("You are the coordinator of drive demo: ")
    assert "ship the fix" not in context
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    context = session_start.respond({"session_id": "talker", "source": "compact"})["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("You are the communicator of drive demo: you talk to him.")
    assert (watch_process.ensured, watch_process.listened) == (["demo"], [("demo", "session-under-test")])


def test_session_start_gives_a_sub_coordinator_its_projects_context_and_starts_and_subscribes_it_to_its_projects_watch(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess
) -> None:
    run("order", "add", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", "demo", "--role", "coordinator", "--project", "Iris")
    watch_process.ensured.clear()
    context = session_start.respond({"session_id": "iris-1", "source": "resume"})["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("You are the sub-coordinator for project Iris of drive demo: ")
    assert "Subscribe: cci drive use demo, then cci subscribe --reader iris --cursor coordinator-iris --to-kind " in context
    assert (watch_process.ensured, watch_process.listened) == (["demo --project Iris"], [("demo", "iris-1", "Iris")])


def test_session_start_names_the_channels_each_role_holds(run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    run("start", "demo", "--role", "coordinator", "--channel", "D123", "--watch", "C0B75AL4XEW", "--watch", "C0822AHFY3G")
    output = session_start.respond({"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "source": "compact"})
    context = output["hookSpecificOutput"]["additionalContext"]
    assert 'Hold with cc-slack subscribe --kind alerts,releases,posts --channel <id>: C0B75AL4XEW, C0822AHFY3G' in context
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    context = session_start.respond({"session_id": "talker"})["hookSpecificOutput"]["additionalContext"]
    assert "Hold with slack_watch: his DM D123" in context


def test_session_start_after_a_compaction_adds_the_newest_installed_skill_page_without_its_frontmatter(demo: str) -> None:
    payload = {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"]}
    page = (session_start.PLUGIN / "skills" / "long-running" / "SKILL.md").read_text()
    body = page.split("\n---\n", 1)[1].strip()
    compacted = session_start.respond(payload | {"source": "compact"})["hookSpecificOutput"]["additionalContext"]
    resumed = session_start.respond(payload | {"source": "resume"})["hookSpecificOutput"]["additionalContext"]
    assert compacted.startswith("You are the coordinator of drive demo: ")
    assert compacted.rstrip().endswith(body)
    assert "name: long-running" not in compacted
    assert body not in resumed


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
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    run("job", "set", "j1", "--agent", "a-7")
    long = "WAITING CI on the PR\nPR: https://github.com/yasyf/cc-skills/pull/1\nCHECK: build 12"
    output = json.loads(hook_output(one_line_result, monkeypatch, capsys, json.dumps(agent_stop(long))))
    assert output["decision"] == "block"
    assert output["reason"].startswith("Your final message is 3 lines and 82 characters")
    assert "`drive job report j1 -`" in output["reason"]
    waiting = "WAITING CI on https://github.com/yasyf/cc-skills/pull/1"
    assert one_line_result.block_reason(agent_stop(waiting)).startswith("Refused on purpose: j1's return is not kept in the drive")
    assert one_line_result.block_reason(agent_stop("ASK /scratch/ask.md")) is None
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


def test_a_drive_jobs_returned_line_sets_its_state_once_the_line_is_let_through(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    run("job", "set", "j1", "--agent", "a-7")
    returned = "WAITING: CI on #1\nPR: https://github.com/yasyf/cc-skills/pull/1"
    hook_output(one_line_result, monkeypatch, capsys, json.dumps(agent_stop(returned)))
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    assert hook_output(one_line_result, monkeypatch, capsys, json.dumps(agent_stop(returned, stop_hook_active=True))) == ""
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("waiting", "WAITING: CI on #1")


def test_an_ask_its_agent_returns_leaves_its_job_waiting_on_the_question_drive_job_ask_set(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(state="waiting", said="Which store?", questions=[state.new_question("/x.md", "Which store?", state.stamp())])
    stop = agent_stop("ASK /x.md", **stepped(tmp_path, "Drive: demo, order o1, job j1"))
    assert hook_output(one_line_result, monkeypatch, capsys, json.dumps(stop)) == ""
    job = state.load("demo")["jobs"]["j1"]
    assert (job["agent"], job["state"], job["said"]) == ("a-9", "waiting", "Which store?")


def stepped(tmp_path: Path, brief: str, session: str | None = None, **fields: object) -> dict:
    lane = tmp_path / "session" / "subagents" / "agent-a-9.jsonl"
    lane.parent.mkdir(parents=True, exist_ok=True)
    lane.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": brief}}) + "\n")
    return {
        "session_id": session or os.environ["CLAUDE_CODE_SESSION_ID"],
        "transcript_path": str(tmp_path / "session.jsonl"),
        "agent_id": "a-9",
        **fields,
    }


def test_an_agent_is_recorded_on_its_briefs_job_by_its_own_steps_and_a_step_after_a_wake_runs_it_again(
    demo: str, run: Run, tmp_path: Path, no_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
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
        data["jobs"]["j1"]["state"] = "waiting"
    assert hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Read") | stepped(tmp_path, brief))) == ""
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    assert no_calls == []


def test_a_step_leaves_a_stopped_job_and_a_job_another_agent_runs_as_they_are(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--agent", "a-1")
    run("job", "add", "reader", "--order", "o1", "--goal", "read it")
    run("job", "set", "j2", "--state", "stopped")
    for job_id in ("j1", "j2"):
        hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Edit") | stepped(tmp_path, f"Drive: demo, order o1, job {job_id}")))
    jobs = state.load("demo")["jobs"]
    assert [(job["agent"], job["state"]) for job in jobs.values()] == [("a-1", "running"), (None, "stopped")]


def test_a_fresh_agent_on_a_running_jobs_brief_takes_it_over_once_its_old_session_is_gone(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--agent", "a-1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "after-restart")
    run("start", "demo", "--role", "coordinator")
    hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Edit") | stepped(tmp_path, "Drive: demo, order o1, job j1")))
    data = state.load("demo")
    job = data["jobs"]["j1"]
    assert (job["agent"], job["session"], job["state"]) == ("a-9", "after-restart", "running")
    assert state.agent_job(data, "a-9") == "j1"


def test_an_agent_that_stops_before_any_step_is_recorded_and_its_line_sets_its_job(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "reader", "--order", "o1", "--goal", "read it")
    stop = agent_stop("j1 is answered: the hook never ran", **stepped(tmp_path, "Drive: demo, order o1, job j1"))
    assert hook_output(one_line_result, monkeypatch, capsys, json.dumps(stop)) == ""
    job = state.load("demo")["jobs"]["j1"]
    assert (job["agent"], job["state"], job["said"]) == ("a-9", "done", "j1 is answered: the hook never ran")


def test_an_agent_started_on_a_brief_drive_wrote_is_recorded_on_that_briefs_job(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    code, out = run("job", "brief", "j1", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main")
    assert code == 0
    hook_output(landing_guard, monkeypatch, capsys, json.dumps(bash("git status") | stepped(tmp_path, out.splitlines()[0])))
    job = state.load("demo")["jobs"]["j1"]
    assert (job["agent"], job["state"], job["brief"]) == ("a-9", "running", str(state.drive_dir("demo") / "jobs" / "j1.brief.md"))


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
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    coordinator = os.environ["CLAUDE_CODE_SESSION_ID"]
    named = started_by_role(coordinator, subagent_type="long-running:handler", name="desk")
    output = json.loads(hook_output(agent_brief, monkeypatch, capsys, json.dumps(named)))
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert output["hookSpecificOutput"]["permissionDecisionReason"].startswith("refused: the coordinator starts no named agent")
    fable = started_by_role(coordinator, subagent_type="long-running:builder", model="fable", prompt="Serves: his order")
    assert agent_brief.deny_reason(fable).startswith("refused: Fable runs only the coordinator and long-running:designer")
    assert agent_brief.deny_reason(started_by_role(coordinator, subagent_type="long-running:designer", model="fable")) is None
    unscoped = started_by_role(coordinator, subagent_type="long-running:builder", prompt="Order (his words): fix it\nGoal: fix it")
    assert agent_brief.deny_reason(unscoped).startswith("refused: a builder's brief, in its prompt or the brief file the prompt names")
    assert agent_brief.deny_reason(started_by_role(coordinator, subagent_type="long-running:builder", prompt="Handoff: 661200 Drive: demo Scratch: /s")) is None
    tooling = "Order: Drive tooling: x\nServes: drive tooling"
    assert agent_brief.deny_reason(started_by_role(coordinator, subagent_type="long-running:builder", prompt=tooling)) == (
        "refused: a builder serving drive tooling is the communicator's, never the coordinator's: send a defect record to"
        " communicator@demo naming the tool and its refusal verbatim, and the communicator's builder takes it over"
    )
    written = tmp_path / "jobs" / "j1.brief.md"
    written.parent.mkdir()
    written.write_text("Order (his words): fix it\nServes: his order\n")
    assert agent_brief.deny_reason(started_by_role(coordinator, subagent_type="long-running:builder", prompt=f"Read your brief first: {written}")) is None
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    assert agent_brief.deny_reason(started_by_role("talker", subagent_type="long-running:handler", name="desk")).startswith(
        "refused: the communicator starts no named agent"
    )
    assert agent_brief.deny_reason(started_by_role("talker", subagent_type="long-running:builder", prompt=tooling)) is None
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


def drive_agent(subagent_type: str, prompt: str, model: str | None = None, session: str | None = None) -> dict:
    tool_input = {"subagent_type": subagent_type, "prompt": prompt, "description": "d"} | ({"model": model} if model else {})
    return {"session_id": session or os.environ["CLAUDE_CODE_SESSION_ID"], "tool_name": "Agent", "tool_input": tool_input}


def test_a_build_orders_builder_starts_only_on_a_brief_naming_its_readers_research(
    communicator: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    monkeypatch.setattr("sys.stdin", io.StringIO(RESEARCH))
    run("job", "report", "j1", "-")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    folder = tmp_path / "j2"
    bare = f"Order: Drive tooling: copy one store\nServes: drive tooling\nDrive: demo, order o1, job j2\nScratch: {folder}"
    denied = json.loads(hook_output(scratch_folder, monkeypatch, capsys, json.dumps(drive_agent("long-running:builder", bare))))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert denied["hookSpecificOutput"]["permissionDecisionReason"].startswith(
        "refused, no agent started: j2 builds on o1, a build order: its brief carries the line Researched: <its reader's job>"
    )
    assert not folder.exists()
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", f"Researched: j2\n{bare}")) == (
        "refused, no agent started: Researched: j2 names no reader of o1 that kept a Researched: line and under it rulings:, exists: and open: lines in its report"
    )
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", f"Researched: j1\n{bare}", "sonnet")) == (
        "refused, no agent started: o1 is a build order: its reader and builder run on Opus and its designer on Fable; pass no model, or opus"
    )
    assert hook_output(scratch_folder, monkeypatch, capsys, json.dumps(drive_agent("long-running:builder", f"Researched: j1\n{bare}"))) == ""
    assert folder.is_dir()
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", f"Handoff: 812\nDrive: demo, order o1, job j2\nScratch: {folder}")) is None
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", bare, session="another-session")) is None
    code, out = run("job", "brief", "j2", "--serves", "drive tooling", "--done-when", "merged", "--where", "yasyf/cc-skills main")
    prompt = out.splitlines()[0]
    assert code == 0
    assert "\nResearched: j1\n" in Path(prompt.removeprefix("Read your brief first: ")).read_text()
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", prompt)) is None
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", prompt, "sonnet")) == (
        "refused, no agent started: o1 is a build order: its reader and builder run on Opus and its designer on Fable; pass no model, or opus"
    )


def test_a_build_orders_designer_on_a_light_model_is_told_to_run_on_fable(communicator: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    monkeypatch.setattr("sys.stdin", io.StringIO(RESEARCH))
    run("job", "report", "j1", "-")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the copy")
    assert scratch_folder.deny_reason(drive_agent("long-running:designer", "Researched: j1\nDrive: demo, order o1, job j2", "sonnet")) == (
        "refused, no agent started: o1 is a build order: its reader and builder run on Opus and its designer on Fable; pass no model, or fable"
    )


def test_a_session_starts_no_agent_on_an_order_the_other_session_holds(demo: str, run: Run, tmp_path: Path) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "fix the watch")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    brief = f"Serves: his order\nDrive: demo, order o1, job j1\nScratch: {tmp_path / 'j1'}"
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", brief)) is None
    with state.edit("demo") as data:
        data["orders"]["o1"]["owner"] = "communicator"
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", brief)) == (
        "refused, no agent started: o1 is the communicator's order, session not started: only the communicator adds its jobs,"
        " starts its agents and posts its reports; send the communicator a record instead"
    )


def test_an_answer_orders_lane_does_its_own_research_on_opus_when_the_order_is_complex(demo: str, run: Run) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "why did the copy stall?", "--kind", "answer", "--complex")
    run("job", "add", "reader", "--order", "o1", "--goal", "answer him")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "which flag sets the cap?", "--kind", "answer")
    run("job", "add", "reader", "--order", "o2", "--goal", "answer him")
    assert scratch_folder.deny_reason(drive_agent("long-running:reader", "Researched: j9\nDrive: demo, order o1, job j1")) == (
        "refused, no agent started: j1 answers o1, an answer order: its one lane does its own research, reading code,"
        " transcripts, records and his answers itself, so its brief carries no Researched: line"
    )
    assert scratch_folder.deny_reason(drive_agent("long-running:reader", "Drive: demo, order o1, job j1", "sonnet")) == (
        "refused, no agent started: o1 is a complex answer order: its lane runs on Opus; pass no model, or opus"
    )
    assert scratch_folder.deny_reason(drive_agent("long-running:reader", "Drive: demo, order o1, job j1", "opus")) is None
    assert scratch_folder.deny_reason(drive_agent("long-running:reader", "Drive: demo, order o2, job j2", "sonnet")) is None


def test_a_light_reader_waits_on_an_answer_order_asking_several_questions_until_it_is_marked(demo: str, run: Run) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "tell me about the copy", "--kind", "answer")
    run("job", "add", "reader", "--order", "o1", "--goal", "why the copy stalls, who owns the bucket, when it started")
    run("job", "add", "reader", "--order", "o1", "--goal", "what is the head sha of the copy fix")
    assert state.load("demo")["orders"]["o1"]["complex"] is None
    assert scratch_folder.deny_reason(drive_agent("long-running:reader", "Drive: demo, order o1, job j1", "sonnet")) == (
        "refused, no agent started: o1 is an answer order asking 3 questions with no complex mark, and a sonnet lane answers only"
        " a one-fact lookup: mark it drive order set o1 --complex and pass no model, or drive order set o1 --no-complex when"
        " one lookup answers them all"
    )
    assert scratch_folder.deny_reason(drive_agent("long-running:reader", "Drive: demo, order o1, job j2", "sonnet")) is None
    assert scratch_folder.deny_reason(drive_agent("long-running:reader", "Drive: demo, order o1, job j1")) is None
    run("order", "set", "o1", "--no-complex")
    assert scratch_folder.deny_reason(drive_agent("long-running:reader", "Drive: demo, order o1, job j1", "sonnet")) is None
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "why the copy stalls\nwho owns the bucket\nwhen it started", "--kind", "answer")
    run("job", "add", "reader", "--order", "o2", "--goal", "answer him")
    assert "o2 is an answer order asking 3 questions with no complex mark" in scratch_folder.deny_reason(
        drive_agent("long-running:reader", "Drive: demo, order o2, job j3", "sonnet")
    )


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
    assert [landing_guard.deny_reason(bash(command) | top) for command in cuts] == [None, None, None]


def test_guard_denies_admin_on_forge_even_when_tapped(drive_pr: str, no_calls: list[list[str]]) -> None:
    approve(drive_pr)
    reason = landing_guard.deny_reason(bash(f"gh pr merge {PR} --squash --admin"))
    assert reason == landing_guard.ADMIN_REASON


def test_guard_allows_a_repo_outside_forge(drive_pr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    origin_is(monkeypatch, "https://github.com/yasyf/cc-slack.git")
    assert landing_guard.deny_reason(bash("gh pr merge 42 --squash --admin")) is None


def test_guard_holds_a_cc_skills_merge_until_every_check_on_its_head_is_green(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    running = [
        {"__typename": "CheckRun", "name": "test", "status": "IN_PROGRESS", "conclusion": ""},
        {"__typename": "StatusContext", "context": "Socket Security", "state": "SUCCESS"},
    ]
    red = [{"__typename": "CheckRun", "name": "test", "status": "COMPLETED", "conclusion": "FAILURE"}, {"__typename": "StatusContext", "context": "Socket Security", "state": "PENDING"}]
    green = [{"__typename": "CheckRun", "name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}, {"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "SKIPPED"}]
    rollups = [running, red, green]

    def answer(args: list[str], cwd: str) -> str:
        assert args == ["gh", "pr", "view", "4153", "-R", "yasyf/cc-skills", "--json", "statusCheckRollup"]
        return json.dumps({"statusCheckRollup": rollups.pop(0)})

    monkeypatch.setattr(landing_guard, "run", answer)
    merge = bash("gh pr merge 4153 -R yasyf/cc-skills --squash --admin")
    assert landing_guard.deny_reason(merge) == (
        "yasyf/cc-skills#4153: test not green on its head yet; its CI takes about a minute, so merge once every check passes, fixing a red one first"
    )
    assert landing_guard.deny_reason(merge).startswith("yasyf/cc-skills#4153: test, Socket Security not green")
    assert landing_guard.deny_reason(merge) is None


@pytest.mark.parametrize("command", ["gh pr merge 4153 -R yasyf/cc-skills --squash", "gh pr merge my-branch -R Forge-AI/monorepo --squash"])
def test_guard_lets_a_landing_run_with_a_warning_when_github_does_not_answer_its_read(
    drive_pr: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    floor = "ccx: GitHub rate limit floor: user graphql has 550 of 5000 left, under the floor of 1000, until 2:53AM"

    def floored(args: list[str], cwd: str) -> str:
        raise landing_guard.github.GithubError(floor)

    monkeypatch.setattr(landing_guard, "run", floored)
    output = json.loads(hook_output(landing_guard, monkeypatch, capsys, json.dumps(bash(command))))["hookSpecificOutput"]
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


def updated_at(name: str, key: str, moment: str) -> None:
    with state.edit(name) as data:
        data["prs"][key]["updated"] = moment


def copy_of_demo(run: Run, name: str) -> None:
    run("start", name, "--role", "coordinator")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "land it again", "--drive", name)
    run("job", "add", "builder", "--order", "o1", "--goal", "again", "--drive", name)
    run("pr", "add", PR, "--job", "j1", "--tap", "--drive", name)


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


def test_bash_posts_a_drive_ask_only_when_it_carries_a_kept_ask(demo: str, no_calls: list[list[str]]) -> None:
    session = os.environ["CLAUDE_CODE_SESSION_ID"]
    kept = state.drive_dir(demo) / "asks" / "j1.md"
    for command in (
        "cci post --kind ask --lane root --to communicator@demo 'Close these sessions?'",
        "cd /work && cci post --lane root --kind=ASK --attr job=j1 'Close these sessions?'",
        "zsh -c \"cci post --kind ask 'Close these sessions?'\"",
    ):
        assert landing_guard.deny_reason(asked_from_bash(command, session)) == landing_guard.UNKEPT_ASK
    carried = f"cci post --kind ask --attr ask={kept} --attr job=j1 'Close these sessions?'"
    assert landing_guard.deny_reason(asked_from_bash(carried, session)) is None
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


def test_a_communicators_bash_cci_post_naming_a_lane_other_than_the_session_hearing_its_order_is_refused(
    run: Run, demo: str, no_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", demo, "--role", "coordinator", "--project", "Iris")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", demo, "--role", "communicator")
    handler = {"session_id": "talker", "agent_id": "a1", "agent_type": "long-running:handler"}
    posted = "cci post --kind posted --lane communicator --to root@demo --resolves 8 --attr waits=demo:j1-8 'card up demo:j1-8'"
    assert landing_guard.deny_reason(bash(posted) | handler) == (
        "A communicator's posted record goes to the coordinator session hearing the order it serves, and root@demo is not its"
        " lane; post it again with `--to iris@demo`, or on the cci `post` tool, whose hook addresses it"
    )
    refused = "zsh -c \"cci post --kind=refused --lane communicator --to=root@demo,owner --attr job=j1 'cc-slack refused the card'\""
    assert "`--to iris@demo --to owner`" in landing_guard.deny_reason(bash(refused) | handler)
    sent = (
        "cci post --kind posted --lane communicator --to iris@demo --attr waits=demo:j1-8 'card up demo:j1-8'",
        "cci post --kind owner --lane communicator --to root@demo 'a new order'",
        "cci post --kind report --lane communicator --attr close=o1 'FYI j1 merged'",
    )
    assert [landing_guard.deny_reason(bash(command) | handler) for command in sent] == [None, None, None]
    assert landing_guard.deny_reason(bash(posted) | {"session_id": "iris-1"}) is None
