import io
import json
import os
import shlex
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
import role_rules
import scratch_folder
import session_start
from conftest import QUEUE_REASON, EnvsRelease, provide, ACME_CUT, ANSWERED_CUT, CUT_LINK, CUT_PICK, CUT_TAP, LEAD, RESEARCH, SANDSQL_NOTE, SHORT_ASK, WRITTEN_AT, answered_cut, cloned, drawn_card, kept_brief, unheld, Answers, Jev, WatchProcess, own_time
from drive import agents, brief, cli, github, land, limits, orca, place, render, settled, slack, state, watch

Run = Callable[..., tuple[int, str]]
PR = "https://github.com/Forge-AI/monorepo/pull/42"
ORIGIN = "git@github.com:Forge-AI/monorepo.git"
RELEASE_CUT_BUILD = "15495"
CLUSTER_JOB_BUILD = "15699"


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


@pytest.fixture
def release_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    started = {
        RELEASE_CUT_BUILD: {"RELEASE_START": json.dumps({"mode": "release", "envs": []})},
        CLUSTER_JOB_BUILD: {"CLUSTER_JOB": json.dumps({"cluster": "plat-usw2-prod", "cronJob": "memory-move-poetic", "reason": "move poetic's memories"})},
    }

    def build(pipeline: str, number: str) -> dict:
        if pipeline != "forge/release" or number not in started:
            raise github.GithubError(f"could not read {pipeline} build {number} from Buildkite: HTTP 404")
        return {"state": "passed", "env": started[number]}

    monkeypatch.setattr(github, "build", build)


def origin_is(monkeypatch: pytest.MonkeyPatch, remote: str) -> None:
    def answer(args: list[str], cwd: str) -> str:
        assert args == ["git", "remote", "get-url", "origin"]
        return remote

    monkeypatch.setattr(landing_guard, "run", answer)


def bash(command: str, cwd: str = "/work") -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd, "transcript_path": "/work/session.jsonl"}


def approve(key: str) -> None:
    with state.edit("demo") as data:
        data["prs"][key]["tap"] = {"label": "Approve", "at": state.stamp(), "head": "abc", "source": "slack", "picks": {}}


def hook_output(module: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stdin: str) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    module.main()
    return capsys.readouterr().out


def test_session_start_gives_each_role_its_own_context_and_registers_its_session_by_its_line(
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
    assert registered == [("demo-communicator", state.load("demo")["roles"]["communicator"]["cwd"], "talker")]


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
    assert watch_process.ensured == ["demo"]
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


def rules_at_next_call(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: dict) -> str | None:
    said = hook_output(role_rules, monkeypatch, capsys, json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}} | payload))
    return json.loads(said)["hookSpecificOutput"]["additionalContext"] if said else None


def test_a_session_gets_only_the_rule_an_install_changed_once_at_its_next_call(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    assert rules_at_next_call(monkeypatch, capsys, {"session_id": "talker"}) is None
    changed = (*render.RULES["communicator"][:3], "Pass answer alone.", *render.RULES["communicator"][4:])
    monkeypatch.setitem(render.RULES, "communicator", changed)
    context = rules_at_next_call(monkeypatch, capsys, {"session_id": "talker"})
    assert context.splitlines() == [f"Your communicator rules or held channels changed since this session's block drew them: long-running {state.VERSION}'s changed lines follow, and win where any older text disagrees.", "4. Pass answer alone."]
    assert rules_at_next_call(monkeypatch, capsys, {"session_id": "talker"}) is None


def test_a_communicator_holds_each_channel_drive_channels_made_from_its_next_call_and_a_sub_coordinator_still_holds_none(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    assert rules_at_next_call(monkeypatch, capsys, {"session_id": "talker"}) is None
    made = {"him-drive-demo": {"id": "G0DRIVE", "name": "him-drive-demo"}, "him-drive-demo-tooling": {"id": "G0TOOLS", "name": "him-drive-demo-tooling"}}
    monkeypatch.setattr(slack, "cc_slack", lambda verb, channel: made[channel])
    monkeypatch.setattr(slack, "operator_channels", lambda: list(made.values()))
    run("channels", "--make")
    context = rules_at_next_call(monkeypatch, capsys, {"session_id": "talker"})
    assert context.splitlines()[1] == "Hold with slack_watch: his DM D123, #him-drive-demo G0DRIVE, #him-drive-demo-tooling G0TOOLS"
    assert rules_at_next_call(monkeypatch, capsys, {"session_id": "talker"}) is None
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", "demo", "--role", "coordinator", "--project", "Iris")
    with state.edit("demo") as data:
        del data["subcoordinators"]["Iris"]["rules"]
    assert rules_at_next_call(monkeypatch, capsys, {"session_id": "iris-1"}).splitlines()[1] == "Hold no Slack channel: the top coordinator holds the drive's."


def test_a_session_gets_its_repositorys_new_rules_once_at_its_next_call(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    assert rules_at_next_call(monkeypatch, capsys, {"session_id": "talker"}) is None
    (tmp_path / ".claude").mkdir()
    (tmp_path / state.REPO_RULES).write_text('[[rule]]\nid = "staging-claim"\ntext = "Post in the staging channel before you freeze it."\n')
    context = rules_at_next_call(monkeypatch, capsys, {"session_id": "talker"})
    assert context.endswith(f"{render.REPO_RULES_HEADING}\n- staging-claim: Post in the staging channel before you freeze it.")
    assert rules_at_next_call(monkeypatch, capsys, {"session_id": "talker"}) is None


def test_a_seat_bound_before_its_rules_were_kept_gets_them_once_and_a_drawn_block_holds_them(demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    coordinator = os.environ["CLAUDE_CODE_SESSION_ID"]
    with state.edit("demo") as data:
        del data["roles"]["coordinator"]["rules"]
    assert rules_at_next_call(monkeypatch, capsys, {"session_id": coordinator, "agent_id": "a1", "agent_type": "long-running:builder"}) is None
    assert rules_at_next_call(monkeypatch, capsys, {"session_id": "no-drive"}) is None
    assert "\n1. " in rules_at_next_call(monkeypatch, capsys, {"session_id": coordinator})
    assert rules_at_next_call(monkeypatch, capsys, {"session_id": coordinator}) is None
    monkeypatch.setitem(render.RULES, "coordinator", (*render.RULES["coordinator"], "One more rule."))
    assert "One more rule." in session_start.respond({"session_id": coordinator, "source": "compact"})["hookSpecificOutput"]["additionalContext"]
    assert rules_at_next_call(monkeypatch, capsys, {"session_id": coordinator}) is None


def test_a_compaction_brings_the_newest_installed_skill_page_as_a_hook_output_of_its_own_beside_the_role_block_unless_the_session_loaded_that_page(
    demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "source": "compact"}
    loaded = tmp_path / "skills" / "long-running" / "SKILL.md"
    loaded.parent.mkdir(parents=True)
    loaded.write_text("---\nname: long-running\n---\nAn older page.\n")
    monkeypatch.setenv("LONG_RUNNING_LOADED_PLUGIN", str(tmp_path))
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
    monkeypatch.setenv("LONG_RUNNING_LOADED_PLUGIN", str(compacted_skill.PLUGIN))
    assert compacted_skill.respond(payload) is None


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
    assert no_file_tools.deny_reason(file_call("Edit", "talker")) is None
    assert no_file_tools.deny_reason(file_call("Read", "talker", agent_id="a-1")) is None
    assert hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Write", "someone-else"))) == ""


def test_a_drive_role_reading_slack_itself_hears_what_it_costs_and_its_agents_and_other_sessions_hear_nothing(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    thread = "mcp__plugin_cc-slack_cc-slack__slack_thread"
    output = json.loads(hook_output(no_slack_reads, monkeypatch, capsys, json.dumps(file_call(thread))))
    assert "permissionDecision" not in output["hookSpecificOutput"]
    assert output["hookSpecificOutput"]["additionalContext"].startswith(f"the coordinator reads Slack itself with {thread}: the read goes")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", "demo", "--role", "communicator")
    history = "mcp__plugin_cc-slack_cc-slack__slack_history"
    assert no_slack_reads.note(file_call(history, "talker")) is None
    assert no_slack_reads.note(file_call(thread, "talker", agent_id="a-1")) is None
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
    capsys.readouterr()
    assert one_line_result.block_reason(agent_stop("ASK /scratch/ask.md")) is None
    assert capsys.readouterr().err.startswith("one_line_result: j1 ends on an ASK and built nothing")
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{waiting}\nPR: https://github.com/yasyf/cc-skills/pull/1\n"))
    run("job", "report", "j1", "-")
    assert one_line_result.block_reason(agent_stop(waiting)) is None
    assert "is 1 line and 501 characters" in one_line_result.block_reason(agent_stop("x" * 501))


def test_a_drive_agent_is_held_to_one_line_and_other_sessions_are_left_alone(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{pushed}\nCHANGE: x\nSTAGING: none: it changes no path AGENTS.md calls dangerous\n"))
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


def test_a_builder_asking_his_card_may_end_on_its_ask_with_a_note_and_ends_built_once_it_builds_the_recommended_option(
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
        SHORT_ASK.replace(SHORT_ASK.splitlines()[0], f"ASK {J2776_QUESTION}").replace("Holds: none: no option changes code any job builds", "Holds: Short title, built on `j2776-delete-agent`")
    )
    asked = run("job", "ask", "j2776", str(block))[1].splitlines()[0]
    job = state.load("durable-agents")["jobs"]["j2776"]
    assert (job["state"], job["said"], [question["waits"] for question in job["questions"]]) == ("running", J2776_QUESTION, ["durable-agents:j2776-7"])
    lane = stepped(tmp_path, "Drive: durable-agents, order o1, job j2776", prompted=EARLIER)
    assert hook_output(one_line_result, monkeypatch, capsys, json.dumps(agent_stop(f"ASK {asked}", **lane))) == ""
    assert land.open_cards(state.load("durable-agents"), "j2776", None) == [drawn_card(1)]
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
    assert [state.load("demo")["jobs"][job_id]["agent"] for job_id in ("j1", "j2")] == [None, None]
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


def test_a_burst_of_builder_starts_here_fills_the_cap_before_any_agents_first_step_and_that_step_keeps_its_slot(
    demo: str, run: Run, tmp_path: Path, no_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "tidy the docs", "--parts", *(f"part {number}" for number in range(1, 10)))
    for number in range(1, 10):
        run("job", "add", "builder", "--order", "o1", "--part", f"part {number}", "--goal", f"build part {number}")
    order = "Order (his words): tidy the docs\nServes: his order\nGoal: build it\nDrive: demo, order o1, job "
    starts = [started_by_role(os.environ["CLAUDE_CODE_SESSION_ID"], subagent_type="long-running:builder", prompt=kept_brief(f"{order}j{number}")) for number in range(1, 10)]
    assert [agent_brief.deny_reason(start) for start in starts[:8]] == [None] * 8
    assert agent_brief.deny_reason(starts[8]).startswith("refused: 8 builders already run on this Mac, its cap of 8 on builders that compile here, where a staging tester, a merged PR's prover or another builder marked --builds-nothing holds no slot (j1 on o1, its agent launching since ")
    monkeypatch.setattr(watch, "last_write", lambda job, data: (state.now(), None))
    monkeypatch.setattr(watch, "local_gap", lambda job_id, job, data, moment: None)
    assert hook_output(landing_guard, monkeypatch, capsys, json.dumps(bash("git status") | stepped(tmp_path, f"{order}j1"))) == ""
    held = state.load(demo)
    assert (held["jobs"]["j1"]["agent"], held["jobs"]["j1"]["launching"]) == ("a-9", None)
    assert list(place.local_builders(held, state.now())) == [f"j{number}" for number in range(1, 9)]
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
    assert [(jobs[job_id]["agent"], jobs[job_id]["where"], jobs[job_id]["state"]) for job_id in ("j1", "j2")] == [(None, "local", "waiting"), ("a-1", "local", "done")]


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


@pytest.mark.parametrize(
    ("incident", "agent_type", "holder"), [(True, "codex:codex-wrapper", None), (True, "long-running:builder", "a-9"), (False, "long-running:builder", None)]
)
def test_only_an_incidents_second_lane_builder_holds_the_codex_job_its_brief_names_so_none_runs_beside_a_standing_pass_as_j5103s_line_asked(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], incident: bool, agent_type: str, holder: str | None
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "errors on the api", *(["--incident", "--parts", "fix"] if incident else []))
    if not incident:
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
    assert [(jobs[job_id]["agent"], jobs[job_id]["state"]) for job_id in ("j1", "j2")] == [("a-1", "running"), ("a-2", "stopped")]


def test_a_fresh_agent_started_on_a_stopped_jobs_brief_leaves_it_stopped_and_its_return_is_kept_setting_nothing(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    proof = tmp_path / "proof.sh"
    proof.write_text("#!/bin/sh\nexit 0\n")
    proof.chmod(0o755)
    with state.edit("demo") as data:
        place.queue(data["jobs"]["j1"], state.now())
        data["jobs"]["j1"]["repro"] = str(proof)
    run("job", "set", "j1", "--state", "stopped")
    hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Edit") | stepped(tmp_path, "Handoff: 681835\nDrive: demo, order o1, job j1")))
    job = state.load("demo")["jobs"]["j1"]
    assert (job["agent"], job["state"]) == ("a-9", "stopped")
    monkeypatch.setattr("sys.stdin", io.StringIO("DONE installed 1.0.549\nCHECK: https://github.com/yasyf/cc-skills/actions/runs/12\n"))
    code, out = run("job", "report", "j1", "-")
    assert (code, out.splitlines()[-1]) == (0, "j1 stays stopped: this return sets nothing on it")
    assert state.load("demo")["jobs"]["j1"]["state"] == "stopped"


def test_the_agent_a_stop_left_on_its_job_keeps_its_last_return_and_ends_on_its_first_stop_while_the_job_stays_stopped_where_j92_could_not(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--agent", "a-7")
    dropped = "his answer dropped every part it delivered"
    run("job", "set", "j1", "--state", "stopped", "--said", dropped)
    handoff = "HANDOFF /scratch/j1/left.md"
    assert one_line_result.block_reason(agent_stop(handoff)).startswith("Refused on purpose: j1's return is not kept in the drive")
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{handoff}\nBranch: lr-move, commit 7667b812e9c5, unpushed\n"))
    assert run("job", "report", "j1", "-")[0] == 0
    assert one_line_result.block_reason(agent_stop(handoff)) is None
    job = state.load("demo")["jobs"]["j1"]
    assert (job["agent"], job["state"], job["said"]) == ("a-7", "stopped", dropped)


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


def test_resume_from_a_session_that_did_not_start_the_jobs_ended_agent_hands_the_job_to_that_sessions_fresh_agent(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "meta-before-restart")
    run("start", "demo", "--role", "meta")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a verb for owners")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--agent", "a-1")
    run("job", "brief", "j1", "--serves", "drive tooling", "--done-when", "merged", "--where", "yasyf/cc-skills main")
    assert cli.main(["job", "launch", "j1"]) == 2
    assert capsys.readouterr().err == "drive: j1 runs on this Mac as agent a-1; resume it with SendMessage\n"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "meta-session")
    run("start", "demo", "--role", "meta")
    monkeypatch.setattr("sys.stdin", io.StringIO("TAP Approve"))
    code, out = run("job", "resume", "j1", "-")
    handoff = state.drive_dir("demo") / "jobs" / "j1.handoff.md"
    assert (code, out.splitlines()[0]) == (0, f"Read your brief first: {handoff}")
    assert "## Taking over from an agent of another session\n\nThe agent that held this job, a-1 of session meta-before-restart," in handoff.read_text()
    assert unheld(handoff.read_text()).endswith("The message it never read, which you act on now:\n\nTAP Approve")
    assert state.load("demo")["jobs"]["j1"]["agent"] == "a-1"
    hook_output(no_file_tools, monkeypatch, capsys, json.dumps(file_call("Edit") | stepped(tmp_path, out.splitlines()[0], session="meta-session")))
    job = state.load("demo")["jobs"]["j1"]
    assert (job["agent"], job["session"], job["state"]) == ("a-9", "meta-session", "running")


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
    state.save(demo, saved)
    code, out = run("job", "resume", "j1", "PR checks failed")
    handoff = state.drive_dir(demo) / "jobs" / "j1.handoff.md"
    assert (code, out.splitlines()[0]) == (0, f"Read your brief first: {handoff}")
    assert unheld(handoff.read_text()).endswith("The message it never read, which you act on now:\n\nPR checks failed")


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


def test_a_drive_roles_agent_runs_unnamed_with_fable_on_its_designer_alone_and_a_builder_without_scope_starts_with_a_note(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    coordinator = os.environ["CLAUDE_CODE_SESSION_ID"]
    handoff = "Handoff: 661200 Drive: demo Scratch: /s"
    assert agent_brief.deny_reason(started_by_role(coordinator, subagent_type="long-running:builder", prompt=handoff)).startswith(
        "refused: a long-running:builder starts only on a brief the drive wrote: `drive job add` it"
    )
    named = started_by_role(coordinator, subagent_type="long-running:builder", name="desk", prompt=kept_brief(handoff))
    unnamed = {key: value for key, value in named["tool_input"].items() if key != "name"}
    output = json.loads(hook_output(agent_brief, monkeypatch, capsys, json.dumps(named)))
    assert output == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": unnamed}}
    fable = started_by_role(coordinator, subagent_type="long-running:builder", model="fable", prompt="Serves: his order")
    assert agents.shaped(fable) == {key: value for key, value in fable["tool_input"].items() if key != "model"}
    designer = started_by_role(coordinator, subagent_type="long-running:designer", model="fable")
    assert agents.shaped(designer) == designer["tool_input"]
    for effort, kept in (("xhigh", "high"), ("max", "high"), ("high", "high"), ("medium", "medium")):
        assert agents.shaped(started_by_role(coordinator, subagent_type="long-running:designer", model="fable", effort=effort))["effort"] == kept
    unscoped = started_by_role(coordinator, subagent_type="long-running:builder", prompt=kept_brief("Order (his words): fix it\nGoal: fix it"))
    assert agent_brief.deny_reason(unscoped) is None
    assert agent_brief.note(unscoped).startswith("This builder's brief carries no `Serves:` line")
    assert agent_brief.deny_reason(started_by_role(coordinator, subagent_type="long-running:builder", prompt=kept_brief(handoff))) is None
    tooling = kept_brief("Order: Drive tooling: x\nServes: drive tooling")
    assert agent_brief.deny_reason(started_by_role(coordinator, subagent_type="long-running:builder", prompt=tooling)) == (
        "refused: a builder serving drive tooling is the meta session's, never the coordinator's: send a defect record to"
        " meta@demo naming the tool and its refusal verbatim, and the meta session's builder takes it over"
    )
    written = state.drive_dir("demo") / "jobs" / "j1.brief.md"
    written.parent.mkdir(exist_ok=True)
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
    hook_output(scratch_folder, monkeypatch, capsys, agent_call("long-running:reader", f"Record: 658250 Drive: demo Scratch: {record}"))
    assert record.is_dir()
    message = tmp_path / "scratch" / "handler-2"
    prompt = f'Message (his words): "ship it"\nDrive: demo. Scratch: {message}. Deliverable: an owner record'
    hook_output(scratch_folder, monkeypatch, capsys, agent_call("long-running:reader", prompt))
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
    folder = tmp_path / "j2"
    bare = f"Order: Drive tooling: copy one store\nServes: drive tooling\nDrive: demo, order o1, job j2\nScratch: {folder}"
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", bare)) is None
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", f"Researched: j2\n{bare}")) == (
        "refused, no agent started: Researched: j2 names no reader or design of j2's part of o1 that kept a Researched: line and under it rulings:, exists: and open: lines in its report"
        "; a reader or design of another order that already kept that block on this work is this part's research once it carries the part, with no second reader: drive job set <that reader or design> --carries o1 --part \"Short title\""
    )
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", f"Researched: j1\n{bare}", "sonnet")) is None
    assert "model" not in agents.shaped(drive_agent("long-running:builder", f"Researched: j1\n{bare}", "sonnet"))
    assert hook_output(scratch_folder, monkeypatch, capsys, json.dumps(drive_agent("long-running:builder", f"Researched: j1\n{bare}"))) == ""
    assert folder.is_dir()
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", f"Handoff: 812\nDrive: demo, order o1, job j2\nScratch: {folder}")) is None
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", bare, session="another-session")) is None
    code, out = run("job", "brief", "j2", "--serves", "drive tooling", "--done-when", "merged", "--where", "yasyf/cc-skills main")
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
    brief = "Order (his words): test hybrid search on a pass box\nGoal: {}\nResearched: j0\nDrive: demo, order o1, job j1"
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
    planned = "Order (his words): re-send the stuck runs\nGoal: hand repair the 128 stuck runs\nRuns as: {}\nResearched: j0\nDrive: demo, order o1, job j1"
    refused = {
        "this Mac's machine-profile break-glass, by the incidents page: it re-sends each stuck run": "machine-profile",
        "drive break-glass j1 --stack plat/prod with no SSO sign-in": "break-glass",
        "the BreakGlassAccess role on plat-prod": "BreakGlassAccess",
    }
    for line, route in refused.items():
        reason = brief.brief_refusal("demo", state.load("demo"), "j1", planned.format(line))
        assert reason.startswith(f'j1\'s Runs as line writes a team\'s data under "{route}", which runs on this Mac'), line
        assert limits.CLUSTER_JOB in reason
    passing = [
        "a Job in plat-usw2-prod from the suspended search-wake-repair CronJob via tools/ci cluster-job, never under drive break-glass from this Mac",
        'the search-wake-repair CronJob via tools/ci cluster-job, by his words "repair his stuck ones with machine profile break glass" superseded',
        "none: an infra apply under drive break-glass that writes no team's data",
    ]
    for line in passing:
        assert brief.brief_refusal("demo", state.load("demo"), "j1", planned.format(line)) is None, line
    tooling = planned.format("drive break-glass from this Mac") + "\nServes: drive tooling"
    assert brief.brief_refusal("demo", state.load("demo"), "j1", tooling) is None


def test_a_tooling_builders_brief_whose_goal_names_a_step_after_the_install_starts_only_once_its_after_install_line_declares_it(meta: str, run: Run, jev: Jev) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: redraw the stale card")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix the redraw")
    goal = (
        "build in long-running, one kept gate: limits.question_problems returns no shape problems once a block parses with kept=True;"
        " install, then redraw j961 through drive tool say and run j4430's prove.sh"
    )
    planned = f"Order (his words): Drive tooling: redraw the stale card\nServes: drive tooling\nGoal: {goal}\nResearched: j0\n{{}}Drive: demo, order o1, job j1"
    jev.picks("after")
    for placed in ("", "Mac: redraw j961 through drive tool say once installed\n"):
        assert brief.brief_refusal("demo", state.load("demo"), "j1", planned.format(placed)).startswith(
            f'j1\'s Goal "{brief.cut(goal, 160)}" names a step to take once its change is installed, as Jev reads it, and no After install: line declares it:'
            " a yasyf/* merge reaches no lane, so drive job prove runs each After install: command"
        )
    [asked, _] = [asked for asked in jev.asked if "after" in asked["questions"]]
    assert asked["state"] == {"goal": goal}
    declared = planned.format("After install:\n- drive cards redraw --waits durable-agents:j961-664395 --post\n- drive job prove j4430\n")
    assert brief.brief_refusal("demo", state.load("demo"), "j1", declared) is None
    unworded = planned.format("").replace(goal, "build the redraw fix")
    assert brief.brief_refusal("demo", state.load("demo"), "j1", unworded) is None
    assert len([asked for asked in jev.asked if "after" in asked["questions"]]) == 2
    jev.picks("none")
    assert brief.brief_refusal("demo", state.load("demo"), "j1", planned.format("")) is None
    jev.picks("after", 0.5)
    assert brief.brief_refusal("demo", state.load("demo"), "j1", planned.format("")) is None


def test_a_build_orders_codex_lane_is_added_at_once_and_started_only_on_its_readers_research_and_its_immediate_fix_at_once(
    home_repo: None,
    meta: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build", "--parts", "the copy", "immediate fix")
    assert run("job", "add", "codex", "--order", "o1", "--part", "immediate fix", "--goal", "stop it") == (0, "j1")
    run("job", "add", "reader", "--order", "o1", "--part", "the copy", "--goal", "research: the copy")
    assert run("job", "add", "codex", "--order", "o1", "--part", "the copy", "--goal", "build it", "--waits", "j2") == (
        0,
        'j3\nj3 carried "the copy" and no worker took it, so this add folded into it, a part or branch having one change job: its goal is this add\'s and its waits gained this add\'s\n'
        "j3 waits on j2: start no agent for it until the watch's FREE line says its last wait ended",
    )
    assert state.load("demo")["jobs"]["j3"]["kind"] == "codex"
    bare = "Order: Drive tooling: copy one store\nDrive: demo, order o1, job {}"
    assert scratch_folder.deny_reason(drive_agent("codex:codex-wrapper", bare.format("j1"))) == (
        "refused, no agent started: j1 is a codex job, whose codex-ask run drive job launch j1 starts itself on its brief,"
        " its reply's state a field codex cannot leave out, so no agent relays it"
    )
    assert brief.brief_refusal("demo", state.load("demo"), "j1", bare.format("j1")) is None
    assert brief.brief_refusal("demo", state.load("demo"), "j3", bare.format("j3")) is None
    monkeypatch.setattr("sys.stdin", io.StringIO(RESEARCH))
    run("job", "report", "j2", "-")
    assert brief.brief_refusal("demo", state.load("demo"), "j3", f"Researched: j2\n{bare.format('j3')}") is None


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
    brief = f"Serves: his order\nResearched: j0\nDrive: demo, order o1, job j1\nScratch: {tmp_path / 'j1'}"
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", brief)) is None
    with state.edit("demo") as data:
        data["orders"]["o1"]["owner"] = "communicator"
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", brief)) == (
        "refused, no agent started: o1 is the communicator's order, session not started: only the communicator adds its jobs,"
        " starts its agents, checks its PRs and posts its reports; send the communicator the step as a handoff record on the drive's record"
        ' tool instead, to "communicator" and attrs {"order": "o1"}'
    )


def test_a_builder_serving_drive_tooling_on_the_coordinators_order_starts_from_the_meta_session_alone(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
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
    typed = f"Order (his words): I told him since you never did, fix that\nServes: drive tooling\nResearched: j1\nDrive: demo, order o1, job j2\nScratch: {tmp_path / 'j2'}"
    assert agent_brief.deny_reason(drive_agent("long-running:builder", typed)) == (
        f"refused: a long-running:builder starts only on a brief the drive wrote: start it on `Read your brief first: {state.drive_dir('demo') / 'jobs' / 'j2.brief.md'}`"
    )
    assert agent_brief.deny_reason(drive_agent("long-running:builder", kept_brief(typed))) == (
        "refused: a builder serving drive tooling is the meta session's, never the coordinator's: `drive job brief j2 --serves"
        ' "drive tooling"` gives o1 to the meta session, then send meta@demo a defect record naming j2 and its'
        " `Read your brief first:` line, and the meta session starts it"
    )
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", typed, session="tooler")) == (
        "refused, no agent started: o1 is the coordinator's order, but a builder serving drive tooling is the meta session's alone:"
        ' `drive job brief j2 --serves "drive tooling"` gives o1 to the meta session, then start it again'
    )
    code, out = run("job", "brief", "j2", "--serves", "drive tooling", "--done-when", "merged and installed", "--where", "yasyf/cc-skills main")
    assert code == 0
    assert out.splitlines()[-1] == (
        "o1 is the meta session's order now, since a builder serving drive tooling is its alone: only the meta session starts j2"
        " and its agents and posts its reports; from the coordinator, start no agent on it and send meta@demo a defect"
        " record naming j2 and its `Read your brief first:` line"
    )
    assert state.load("demo")["orders"]["o1"]["owner"] == "meta"
    prompt = out.splitlines()[0]
    for session, starter in ((coordinator, "coordinator"), ("talker", "communicator")):
        refused = drive_agent("long-running:builder", prompt, session=session)
        assert agent_brief.deny_reason(refused) == (
            f"refused: a builder serving drive tooling is the meta session's, never the {starter}'s, and o1 is the meta session's"
            " order, session tooler: send meta@demo a defect record naming j2 and its `Read your brief first:` line, and the"
            " meta session starts it"
        )
        assert scratch_folder.deny_reason(refused).startswith("refused, no agent started: o1 is the meta session's order, session tooler")
    metas = drive_agent("long-running:builder", prompt, session="tooler")
    assert agent_brief.deny_reason(metas) is None
    assert scratch_folder.deny_reason(metas) is None
    assert cli.main(["job", "brief", "j2"]) == 2
    assert "drive: j2: refused, nothing written, sent or started: o1 is the meta session's order, session tooler" in capsys.readouterr().err
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "tooler")
    assert run("job", "brief", "j2")[1].splitlines()[-1] == "kept from its earlier brief: --serves, --done-when, --where"


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
    reads = (
        "tools/ci release rollback releases/2026-11-11/3 --plan",
        "tools/ci release status",
        "tools/ci release watch 4242",
        "tools/ci release start --help",
        "tools/ci release approve -h",
    )
    assert [landing_guard.deny_reason(bash(command) | session) for session in (sub, top) for command in reads] == [None] * len(reads) * 2
    assert [landing_guard.deny_reason(bash(command) | top) for command in cuts] == [landing_guard.RELEASE_BY_HAND.format(command=cuts[0]), None, None]


def test_a_drive_job_cancels_no_release_it_started_and_returns_it_held_for_the_coordinator(demo: str, no_calls: list[list[str]]) -> None:
    cancel = "tools/ci release cancel electric-racine"
    job = {"session_id": "session-under-test", "agent_id": "a4da3dca63d05a40d", "agent_type": "long-running:builder"}
    lane = {"session_id": "a-lane-of-its-own", "agent_type": "long-running:builder"}
    assert [landing_guard.deny_reason(bash(cancel) | who) for who in (job, lane)] == [landing_guard.RELEASE_CANCEL] * 2
    assert "return with the release held for the coordinator" in landing_guard.RELEASE_CANCEL
    reads = ("tools/ci release status electric-racine", "tools/ci release watch electric-racine", f"{cancel} --help")
    assert [landing_guard.deny_reason(bash(command) | job) for command in reads] == [None] * len(reads)
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


@pytest.fixture
def release_starts(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Each drive job release a --release add starts, kept for the test to run in its own process, whose pid the add's job waits on."""
    starts: list[list[str]] = []
    detach = state.detach
    monkeypatch.setattr(state, "detach", lambda name, args, log: starts.append(args) or os.getpid() if args[:2] == ["job", "release"] else detach(name, args, log))
    return starts


@pytest.fixture
def release_posts(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Each cci post a release start that named no build sends, kept rather than run, every other read running as it does."""
    posts: list[list[str]] = []
    runs = cli.reads.run

    def run(argv: list[str], checkout: Path, env: dict | None = None, bounded: bool = True, stdin: str | None = None) -> str:
        if argv[0] == "cci":
            posts.append(argv)
            return json.dumps({"seq": 731100})
        return runs(argv, checkout, env, bounded, stdin)

    monkeypatch.setattr(cli.reads, "run", run)
    return posts


@pytest.fixture
def release_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    own_time(monkeypatch, cli, sleep=slept.append)
    return slept


def posted_to(argv: list[str]) -> list[str]:
    return [argv[at + 1] for at, arg in enumerate(argv) if arg == "--to"]


def starting(demo: str, job_id: str, command: str) -> str:
    return (
        f"{job_id} starts `{command}` in process {os.getpid()}, which keeps the build it starts as {job_id}'s Release build: line and writes its brief,"
        f" its lines in {state.drive_dir(demo) / cli.RELEASES_LOG}: start no agent for it until the watch's FREE line on {job_id} names that brief"
    )


def test_drive_job_add_release_starts_it_on_his_answer_in_a_process_of_its_own_that_briefs_the_job_on_its_build_and_a_start_naming_no_build_frees_the_job_saying_why(
    demo: str,
    run: Run,
    tmp_path: Path,
    no_calls: list[list[str]],
    recorded: Answers,
    jev: Jev,
    release_starts: list[list[str]],
    release_posts: list[list[str]],
    release_sleeps: list[float],
    capsys: pytest.CaptureFixture[str],
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "release the clock")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the clock")
    started = released(demo, f"echo 'waiting on the operator' >&2\necho '{STARTED}'\nexec sleep 600")
    recorded.shown = {answer["id"][:7]: answer for answer in (CLOCK_PICK, PR_TAP)}
    add = ["job", "add", "builder", "--order", "o1", "--goal", "release the merged clock"]
    refusals = (
        (
            J2666_START.replace("5419ffd", "ac61348"),
            f'refused: `--order ac61348` names his answer to "{PR_TAP["title"]}", "Approve at 4aca314", and Opus does not read it as approving this release. {brief.RELEASE_APPROVAL}',
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
    jev.picks("5419ffd")
    code, out = run(*add, "--release", J2666_START)
    assert (code, out.splitlines()[:2]) == (0, ["j2", starting(demo, "j2", J2666_START)])
    assert (release_starts, state.load(demo)["jobs"]["j2"]["waits"], started.exists()) == ([["job", "release", "j2", "--session", os.environ["CLAUDE_CODE_SESSION_ID"]]], state.pid_waits(f"pid:{os.getpid()}"), False)
    assert jev.asked[-1]["state"] == {
        "action": f"start this release on {settled.PRODUCTION}: `{J2666_START}`",
        "answer": {"asked": CLOCK_PICK["title"], "said": CLOCK_PICK["body"]},
    }
    code, out = run("job", "release", "j2")
    assert started.read_text() == "release start platform runtime-api-next restate --order 5419ffd --json\n"
    written = state.drive_dir(demo) / "jobs" / "j2.brief.md"
    assert (code, out.splitlines()[:2]) == (
        0,
        [f"j2 started `{J2666_START}` as {BUILD} and left it running: its agent follows it with `ci release watch {BUILD}` and reads its approve hold", f"Read your brief first: {written}"],
    )
    assert run("job", "brief", "j2", "--serves", "his order", "--done-when", "live", "--where", "Forge-AI/monorepo dev")[0] == 0
    assert f"\nRelease: {J2666_START}\nRelease build: {BUILD}\n" in written.read_text()
    assert landing_guard.deny_reason(bash(J2666_START) | stepped(tmp_path, out)) == landing_guard.RELEASE_BY_HAND.format(command=J2666_START)
    released(demo, """case "$1" in bk) echo '{"b0": {"state": "PASSED"}}'; exit;; esac\ncat <<'EOF'\n{\n  "refused": "a freeze holds platform",\n  "code": "frozen"\n}\nEOF\nexit 1""")
    with state.edit(demo) as data:
        for job_id in ("j1", "j2"):
            data["jobs"][job_id]["state"] = "stopped"
        data["jobs"]["j3"] = state.new_job("o1", "design", "cards: the clock", data["jobs"]["j1"]["parts"], state.stamp()) | {"state": "waiting", "said": "DESIGN the clock"}
        data["orders"]["o1"]["jobs"].append("j3")
    assert run(*add, "--release", J2666_START)[1].splitlines()[:2] == ["j4", starting(demo, "j4", J2666_START)]
    assert cli.main(["job", "release", "j4"]) == 2
    assert capsys.readouterr().err.strip() == "drive: j4: its release did not start: a freeze holds platform"
    after = state.load(demo)
    assert after["dropped_waits"]["j4"] == dict.fromkeys(state.pid_waits(f"pid:{os.getpid()}"), "its release did not start: a freeze holds platform; drive job set j4 --state stopped ends it")
    assert (after["jobs"]["j3"]["state"], after["jobs"]["j4"]["release_build"], (state.drive_dir(demo) / "jobs" / "j4.brief.md").exists()) == ("waiting", None, False)
    [post] = release_posts
    assert (posted_to(post), post[post.index("--kind") + 1], release_sleeps) == (["root@demo"], "handoff", [])
    assert post[-1] == f"RELEASE j4 o1: `{J2666_START}` did not start: a freeze holds platform; j4 did not start, and drive job release j4 starts it again"


def test_723003_a_release_keeps_its_build_whatever_fails_after_its_start_and_the_same_release_never_starts_while_a_job_holds_it_unbuilt_or_its_build_runs(
    home_repo: None,
    demo: str, run: Run, no_calls: list[list[str]], recorded: Answers, jev: Jev, release_starts: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "release the clock")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the clock")
    started = released(demo, f"""case "$1" in bk) cat "$(dirname "$0")/../bk"; exit;; esac\necho '{STARTED}'""")
    recorded.shown = {CLOCK_PICK["id"][:7]: CLOCK_PICK}
    jev.picks("5419ffd")
    add = ["job", "add", "builder", "--order", "o1", "--goal", "release the merged clock", "--release", J2666_START]
    written, briefs = state.drive_dir(demo) / "jobs" / "j2.brief.md", cli.write_brief
    assert run(*add)[0] == 0
    assert cli.main(add) == 2
    assert capsys.readouterr().err.strip() == (
        f"drive: refused, no job added: j2 holds the same release and has kept no build: its start's lines are in {state.drive_dir(demo) / cli.RELEASES_LOG},"
        " the watch's FREE line on j2 names its brief once that start ends, and drive job set j2 --state stopped ends a job whose release did not start"
    )

    def dies(name: str, job_id: str, named: dict) -> tuple[str, dict]:
        raise state.DriveError("the demo drive was saved by long-running 9.9.9, newer than this one")

    monkeypatch.setattr(cli, "write_brief", dies)
    assert cli.main(["job", "release", "j2"]) == 2
    assert capsys.readouterr().err.strip() == "drive: the demo drive was saved by long-running 9.9.9, newer than this one"
    assert (state.load(demo)["jobs"]["j2"]["release_build"], written.exists()) == (BUILD, False)
    monkeypatch.setattr(cli, "write_brief", briefs)
    (started.parent / "bk").write_text(json.dumps({"b0": {"state": "BLOCKED"}}))
    assert cli.main(add) == 2
    assert capsys.readouterr().err.strip() == f"drive: refused, no job added: j2 started the same release as {BUILD}, which Buildkite reads BLOCKED: its job follows it, and this one starts once it ends"
    code, out = run("job", "brief", "j2", "--serves", "his order")
    assert (code, out.splitlines()[0]) == (0, f"Read your brief first: {written}")
    assert f"\nRelease: {J2666_START}\nRelease build: {BUILD}\n" in written.read_text()
    (started.parent / "bk").write_text(json.dumps({"b0": {"state": "PASSED"}}))
    assert run(*add)[1].splitlines()[:2] == ["j3", starting(demo, "j3", J2666_START)]
    assert [start[:3] for start in release_starts] == [["job", "release", "j2"], ["job", "release", "j3"]]
    assert cli.main(["job", "brief", "j3"]) == 2
    assert capsys.readouterr().err.strip() == (
        "drive: j3: its brief names the build of the release its add started, which that add's drive job release writes once the release names it:"
        f" its lines are in {state.drive_dir(demo) / cli.RELEASES_LOG}, and drive job set j3 --state stopped ends a job whose release did not start"
    )


GITHUB_500 = "the operator failed the start: POST /app/installations/1/access_tokens failed: 500"


@pytest.mark.parametrize("fails", [1, 2])
def test_730933_a_release_start_failing_on_a_github_5xx_starts_once_more_30_seconds_on_and_one_still_failing_tells_the_coordinator_and_the_orders_seat(
    demo: str,
    run: Run,
    no_calls: list[list[str]],
    recorded: Answers,
    jev: Jev,
    release_starts: list[list[str]],
    release_posts: list[list[str]],
    release_sleeps: list[float],
    capsys: pytest.CaptureFixture[str],
    fails: int,
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "release the clock")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the clock")
    started = released(demo, f"""if [ $(($(wc -l < "$(dirname "$0")/../started"))) -le {fails} ]; then echo '{GITHUB_500}' >&2; exit 1; fi\necho '{STARTED}'""")
    recorded.shown = {CLOCK_PICK["id"][:7]: CLOCK_PICK}
    jev.picks("5419ffd")
    assert run("job", "add", "builder", "--order", "o1", "--goal", "release the merged clock", "--release", J2666_START)[0] == 0
    code = cli.main(["job", "release", "j2"])
    assert (code, len(started.read_text().splitlines()), release_sleeps) == (0 if fails == 1 else 2, 2, [cli.RELEASE_RETRY_SECONDS])
    if fails == 1:
        assert (release_posts, state.load(demo)["jobs"]["j2"]["release_build"]) == ([], BUILD)
        return
    assert capsys.readouterr().err.strip().splitlines()[-1] == f"drive: j2: its release did not start: {GITHUB_500}"
    [post] = release_posts
    assert (posted_to(post), post[-1]) == (["root@demo"], f"RELEASE j2 o1: `{J2666_START}` did not start: {GITHUB_500}; j2 did not start, and drive job release j2 starts it again")
    assert state.load(demo)["jobs"]["j2"]["release_build"] is None


def test_a_release_starts_from_the_drives_checkout_at_its_default_branchs_head_never_the_coordinators_own_tree(
    demo: str, run: Run, tmp_path: Path, no_calls: list[list[str]], recorded: Answers, jev: Jev, release_starts: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "release the clock")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the clock")
    started = released(demo, f"echo '{STARTED}'")
    behind = tmp_path / "behind"
    behind.mkdir()
    with state.edit(demo) as data:
        data["roles"]["coordinator"]["cwd"] = str(behind)
    monkeypatch.setattr(orca, "checkout", lambda drive, clone, wait=0: started.parent if clone == str(behind) else pytest.fail(clone))
    recorded.shown = {CLOCK_PICK["id"][:7]: CLOCK_PICK}
    jev.picks("5419ffd")
    assert run("job", "add", "builder", "--order", "o1", "--goal", "release the merged clock", "--release", J2666_START)[0] == 0
    assert run("job", "release", "j2")[0] == 0
    assert started.read_text() == "release start platform runtime-api-next restate --order 5419ffd --json\n"


def test_a_staging_release_for_production_starts_at_once_through_a_staging_slot_a_test_holds_and_leaves_that_slot_to_the_test(
    demo: str, run: Run, no_calls: list[list[str]], recorded: Answers, jev: Jev, release_starts: list[list[str]]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "release the clock")
    run("job", "add", "builder", "--order", "o1", "--goal", "test the clock on staging")
    released(demo, f"echo '{STARTED}'")
    recorded.shown = {CLOCK_PICK["id"][:7]: CLOCK_PICK}
    jev.picks("5419ffd")
    with state.edit(demo) as data:
        state.slot_claim(data, "plat-usw2-staging", "j1", 30, [], None, state.now())
    start = f"{J2666_START} --for-production --env plat-usw2-staging"
    assert run("job", "add", "builder", "--order", "o1", "--goal", "release the merged clock", "--release", start)[1].splitlines()[:2] == ["j2", starting(demo, "j2", start)]
    slot = state.load(demo)["slots"]["plat-usw2-staging"]
    assert (slot["holder"]["job"], slot["queue"]) == ("j1", [])


MANUAL_START = "tools/ci release start --stack sandsql/plat-usw2-sandbox --commit b79714bb452c562078e4446680106140f721e8ed --manual --emergency --order 5419ffd"
MANUAL_BUILD = "https://buildkite.com/forge/release/builds/16621"
MANUAL_SAID = (
    "release #16620 cuts the deploy branch: https://buildkite.com/forge/release/builds/16620",
    "running    :scissors: cut the deploy branch",
    f"release #16621: {MANUAL_BUILD} — follow it from anywhere: tools/ci release watch --manual 16621",
)


def test_717905_a_manual_release_starts_with_no_json_and_its_job_keeps_the_deploy_build_its_cut_triggered_and_a_local_one_is_refused(
    demo: str, run: Run, no_calls: list[list[str]], recorded: Answers, jev: Jev, release_starts: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "release the sandbox")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the sandbox")
    printed = "\n".join(f"echo '{line}'" for line in MANUAL_SAID)
    started = released(demo, f"""case "$*" in *--json*) echo "ci release: Unknown option '--json'" >&2; exit 1;; esac\n{printed}\nexec sleep 600""")
    recorded.shown = {CLOCK_PICK["id"][:7]: CLOCK_PICK}
    jev.picks("5419ffd")
    add = ["job", "add", "builder", "--order", "o1", "--goal", "release the sandbox"]
    local = MANUAL_START.replace("--manual", "--local").replace(" --emergency", "")
    assert cli.main([*add, "--release", local]) == 2
    assert capsys.readouterr().err.strip() == (
        f"drive: refused, no job added: --release `{local}` deploys `--local`, whose plan and apply run in the shell that starts it and start no build its"
        " job could follow, so no job starts it: a `--manual` or regular start names the build its job follows"
    )
    assert not started.exists()
    assert run(*add, "--release", MANUAL_START)[0] == 0
    code, out = run("job", "release", "j2")
    assert code == 0
    assert started.read_text() == MANUAL_START.removeprefix("tools/ci ") + "\n"
    assert out.splitlines()[0] == (
        f"j2 started `{MANUAL_START}` as {MANUAL_BUILD} and left it running: its agent follows it with `tools/ci release watch --manual 16621` and reads its approve hold"
    )
    assert state.load(demo)["jobs"]["j2"]["release_build"] == MANUAL_BUILD


PREBUILD = "https://buildkite.com/forge/prebuild/builds/19600"
PREBUILT = json.dumps({"branch": "releases/prebuild-20261116T080000Z", "commit": "a6f826f", "build": {"number": 19600, "url": PREBUILD}})
STAGED_START = "tools/ci release start platform --from a6f826f --emergency --env plat-usw2-staging,plat-usw2-sandbox"


class PrebuildRelease(EnvsRelease):
    def prebuild(self, commit: str) -> str | None:
        return self.json("tools/ci release prebuild {commit} --json", commit=commit)["build"]["url"]


def test_a_start_on_staging_kicks_off_the_prebuild_of_its_commit_and_keeps_its_build_on_the_job_while_a_production_start_kicks_off_none_and_a_failed_kick_off_keeps_the_release(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, no_calls: list[list[str]], recorded: Answers, jev: Jev, release_starts: list[list[str]]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "release the clock")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the clock")
    started = released(demo, f"""case "$2" in prebuild) echo '{PREBUILT}'; exit;; esac\necho '{STARTED}'""")
    checkout = started.parent
    provide(monkeypatch, checkout, release=PrebuildRelease)
    (checkout / "infra" / "envs").mkdir(parents=True)
    for env, marked in (("plat-usw2-staging", "staging"), ("plat-usw2-sandbox", "sandbox"), ("plat-usw2-prod", "prod\nprod: true")):
        (checkout / "infra" / "envs" / f"{env}.yaml").write_text(f"id: {env}\nstage: {marked}\n")
    recorded.shown = {CLOCK_PICK["id"][:7]: CLOCK_PICK}
    jev.picks("5419ffd")
    add = ["job", "add", "builder", "--order", "o1", "--goal", "release the merged clock", "--release"]
    assert run(*add, J2666_START)[0] == 0
    code, out = run("job", "release", "j2")
    assert (code, started.read_text(), state.load(demo)["jobs"]["j2"]["prebuild_build"]) == (0, "release start platform runtime-api-next restate --order 5419ffd --json\n", None)
    started.unlink()
    assert run(*add, STAGED_START)[0] == 0
    code, out = run("job", "release", "j3")
    assert (code, started.read_text().splitlines()) == (0, [f"{STAGED_START.removeprefix('tools/ci ')} --json", "release prebuild a6f826f --json"])
    assert out.splitlines()[1] == f"j3 kicked off {PREBUILD}, which builds every image and app a release at a6f826f adopts, so the production start at a6f826f reuses them"
    assert (state.load(demo)["jobs"]["j3"]["release_build"], state.load(demo)["jobs"]["j3"]["prebuild_build"]) == (BUILD, PREBUILD)
    released(demo, f"""case "$2" in prebuild) echo 'buildkite: 503' >&2; exit 1;; esac\necho '{STARTED}'""")
    assert run(*add, STAGED_START.replace("a6f826f", "b79714b"))[0] == 0
    code, out = run("job", "release", "j4")
    assert (code, out.splitlines()[1]) == (0, "j4's prebuild at a6f826f did not start, so a release at a6f826f builds what it would have: `tools/ci release prebuild a6f826f --json` exited 1: buildkite: 503")
    assert (state.load(demo)["jobs"]["j4"]["release_build"], state.load(demo)["jobs"]["j4"]["prebuild_build"], (state.drive_dir(demo) / "jobs" / "j4.brief.md").exists()) == (BUILD, None, True)


def test_the_top_starts_and_hears_the_release_job_on_a_sub_coordinators_order_and_the_sub_coordinator_none_of_it(
    demo: str, run: Run, no_calls: list[list[str]], recorded: Answers, jev: Jev, release_starts: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
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
    assert run("job", "release", "j2")[0] == 0
    where = ("--serves", "his order", "--done-when", "live", "--where", "Forge-AI/monorepo dev")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "platform-1")
    assert cli.main(["job", "brief", "j2", *where]) == 2
    assert capsys.readouterr().err.strip() == f"drive: j2: refused, nothing written, sent or started: {held}"
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


def test_the_top_starts_the_agent_of_a_sub_coordinators_job_only_once_it_started_the_agent_running_it_as_the_job_verbs_let_it(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Agents", "build the clock")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "platform-1")
    run("start", "demo", "--role", "coordinator", "--project", "Platform")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the clock")
    where = ("--serves", "his order", "--done-when", "live", "--where", "Forge-AI/monorepo dev")
    code, out = run("job", "brief", "j1", *where)
    assert code == 0
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-under-test")
    assert cli.main(["job", "brief", "j1", *where]) == 2
    held = capsys.readouterr().err.strip().removeprefix("drive: j1: refused, nothing written, sent or started: ")
    assert scratch_folder.deny_reason(drive_agent("long-running:builder", out)) == f"refused, no agent started: {held}"
    with state.edit(demo) as data:
        data["jobs"]["j1"] |= {"agent": "a-1", "session": "session-under-test"}
    assert [scratch_folder.deny_reason(drive_agent("long-running:builder", out, session=session)) for session in ("session-under-test", "platform-1")] == [None, None]


RELEASE_PICK ={"id": "5d0c4a1" + "0" * 33, "title": "Release the memory fix to production now?", "body": "Release now (Recommended)\nNotes:\nA session's reading, not his words: the fix ships on the next cut.", "tags": ["scope:durable"]}
INCIDENT_ORDER = {"id": "9e1f2b3" + "0" * 33, "title": "sand runs fail on core-usw2", "body": "runs are failing, fix it", "tags": ["scope:durable", "drive:demo", "incident"]}
PR_TAP = {"id": "ac61348" + "0" * 33, "title": "Tap: #50203, canary diagnostics on the search", "body": "Approve at 4aca314\nNotes:\nTapped on the PR card.", "tags": ["scope:durable"]}


def test_a_release_starts_on_order_naming_an_incidents_answer_or_one_jev_reads_as_his_pick_to_release(demo: str, recorded: Answers, jev: Jev, capsys: pytest.CaptureFixture[str]) -> None:
    recorded.shown = {answer["id"][:7]: answer for answer in (RELEASE_PICK, INCIDENT_ORDER, PR_TAP)}
    start = "tools/ci release start sand --only sand --emergency --order {}"
    jev.picks("5d0c4a1")
    assert brief.approval_refusal(start.format("5d0c4a1"), state.load("demo")) is None
    [asked] = jev.asked
    assert asked["state"] == {
        "action": f"start this release on {settled.PRODUCTION}, shipping only sand: `{start.format('5d0c4a1')}`",
        "answer": {"asked": RELEASE_PICK["title"], "said": "Release now (Recommended)", "notes": "A session's reading, not his words: the fix ships on the next cut."},
    }
    assert asked["questions"] == {"orders.5d0c4a1": settled.ORDERS, "more": settled.SHIPS_MORE}
    assert brief.approval_refusal(start.format("9e1f2b3"), state.load("demo")) is None
    assert len(jev.asked) == 1
    jev.picks("none")
    assert brief.approval_refusal(start.format("ac61348"), state.load("demo")) == (
        'refused: `--order ac61348` names his answer to "Tap: #50203, canary diagnostics on the search", "Approve at 4aca314", and Opus'
        f" does not read it as approving this release. {brief.RELEASE_APPROVAL}"
    )
    jev.answer = None
    assert brief.approval_refusal(start.format("ac61348"), state.load("demo")).startswith(
        "refused: Opus did not settle whether his answer ac61348 approves this release (no jev decision within 20s)"
    )
    assert len(jev.asked) == 3
    reading = 'his words "Approve at 4aca314" approve this release'
    assert brief.approval_refusal(start.format("ac61348"), state.load("demo"), reading) is None
    jev.answer = 0.5
    assert brief.approval_refusal(start.format("ac61348"), state.load("demo")).startswith(
        "refused: Opus did not settle whether his answer ac61348 approves this release (Opus is unsure whether his answer ac61348 approves this release)"
    )
    assert brief.approval_refusal(start.format("ac61348"), state.load("demo"), reading) is None
    jev.picks("none")
    capsys.readouterr()
    assert brief.approval_refusal(start.format("ac61348"), state.load("demo"), reading) is None
    assert f"drive job add: `--order ac61348` names his answer to \"{PR_TAP['title']}\", \"Approve at 4aca314\", and Opus does not read it as approving this release; it starts on the top coordinator's reading \"{reading}\"" in capsys.readouterr().err
    assert brief.approval_refusal(start.removesuffix(" --order {}"), state.load("demo"), reading) is None
    assert "drive job add: it names no `--order`; it starts on the top coordinator's reading" in capsys.readouterr().err


def test_the_release_gate_reads_a_start_against_the_drive_release_notes_in_its_range_at_its_default_branch_head_never_refusing_and_parses_a_manual_starts_stack_and_commit(
    demo: str, tmp_path: Path, jev: Jev, release_noted: list[dict], pr_reads: dict[str, dict]
) -> None:
    merge = cloned(tmp_path, "demo", {"catalog.rs": "the catalog change"})
    head = cloned(tmp_path, "demo", {"other.rs": "a later change"})
    data = state.load("demo")
    pr_reads[f"{github.checkout_repo(data['roles']['coordinator']['cwd'])}#55980"] = {"state": "MERGED", "mergeCommit": merge, "base_pr": None}
    release_noted.append(SANDSQL_NOTE)
    start, hotfix, fix = (f"tools/ci release {release} --order 5d0c4a1" for release in ("start --targets sandsql", "hotfix 56001", "hotfix 55980"))
    assert brief.release_notes([start, hotfix, fix], data) == []
    jev.speaks = {SANDSQL_NOTE["title"]: 0.9}
    [line] = brief.release_notes([start, hotfix, fix], data)
    assert line.startswith(
        f'Jev reads release note 3e6822d, "{SANDSQL_NOTE["title"]}", as speaking to `{start}` (at main\'s head {head[:12]}: holds #55980 ({merge[:12]}));'
        f" `{fix}` (it hotfixes #55980). Nothing is refused"
    )
    assert sorted(jev.asked[-1]["questions"]) == ["speaks.n1.s1", "speaks.n1.s3"]
    jev.answer = None
    assert brief.release_notes([start], data) == [
        f"the drive's release notes were not read against `{start}` (Jev gave no reading: no jev decision within 20s): read each note"
        " `ccn note list --label drive:demo --label release-order` and `--label release` list before you offer or start one"
    ]
    assert brief.started("tools/ci release start --manual --stack runtime-api-next/plat-usw2-prod --commit 55b9c39c --rollback", {}) == [
        {"manual": ("true",), "rollback": ("true",), "targets": ("runtime-api-next",), "env": ("plat-usw2-prod",), "from": ("55b9c39c",)}
    ]


def test_the_release_gate_reads_every_release_note_however_many_one_jev_request_could_hold_naming_only_a_note_too_large_for_any(
    demo: str, tmp_path: Path, jev: Jev, release_noted: list[dict], pr_reads: dict[str, dict]
) -> None:
    merge = cloned(tmp_path, "demo", {"catalog.rs": "the catalog change"})
    head = cloned(tmp_path, "demo", {"other.rs": "a later change"})
    data = state.load("demo")
    pr_reads[f"{github.checkout_repo(data['roles']['coordinator']['cwd'])}#55980"] = {"state": "MERGED", "mergeCommit": merge, "base_pr": None}
    newer = [
        {"id": f"{number:07d}" + "0" * 33, "title": f"Release-order hazard {number}: no cloudrunner cut before its fix ships", "body": "Why the cut waits on its fix. " * 48, "tags": ["drive:demo", "release-order"]}
        for number in range(1, 14)
    ]
    huge = {"id": "9" * 40, "title": "Release-order hazard: the longest note", "body": "Why. " * 2400, "tags": ["drive:demo", "release-order"]}
    release_noted.extend([*newer, huge, SANDSQL_NOTE])
    jev.speaks = {note["title"]: 0.9 for note in release_noted}
    start = "tools/ci release start --targets sandsql --order 5d0c4a1"
    [unread, *lines] = brief.release_notes([start], data)
    assert unread.startswith(f"the drive's release notes were not read against `{start}` (no room in a Jev request for {huge['id'][:7]})")
    assert [line.partition(",")[0] for line in lines] == [f"Jev reads release note {note['id'][:7]}" for note in [*newer, SANDSQL_NOTE]]
    assert f"(at main's head {head[:12]}: holds #55980 ({merge[:12]}))" in lines[-1]
    assert len(jev.asked) > 1
    assert all(settled.size(asked["state"], asked["questions"]) <= settled.REQUEST_CHARS for asked in jev.asked)
    assert sorted(asked["state"]["note"]["title"] for asked in jev.asked) == sorted(note["title"] for note in [*newer, SANDSQL_NOTE])


def test_the_release_gate_refuses_a_start_whose_commit_lacks_a_merged_pr_the_card_his_answer_answers_names_and_names_its_merge_to_start_from(
    demo: str, run: Run, tmp_path: Path, recorded: Answers, pr_reads: dict[str, dict]
) -> None:
    older = cloned(tmp_path, "demo", {"monitors.ts": "the old monitor rows"})
    merge = cloned(tmp_path, "demo", {"monitors.ts": "the tuned monitor rows"})
    cloned(tmp_path, "demo", {"other.rs": "a later change"})
    repo = github.checkout_repo(state.load("demo")["roles"]["coordinator"]["cwd"])
    answered_cut(run, demo, [CUT_PICK, CUT_TAP], ANSWERED_CUT.replace("Holds: none: no option changes code any job builds", f"Holds: https://github.com/{repo}/pull/7; #8"))
    recorded.shown = {answer["id"][:7]: answer for answer in (CUT_PICK, CUT_TAP, RELEASE_PICK)}
    pr_reads |= {f"{repo}#7": {"state": "MERGED", "mergeCommit": merge, "base_pr": None}, f"{repo}#8": {"state": "OPEN", "mergeCommit": None, "base_pr": None}}
    data = state.load(demo)
    asked = f'`--order 7ab1e20` names his answer to the card asking "{CUT_PICK["title"]}", which names #7, and'
    then = f": it starts as given, since his answer may leave one out; to carry each, start from a commit holding them, such as `--from {merge}`, the newest of their merge commits"
    starts = {
        "tools/ci release start --order 7ab1e20": f"{asked} this start names no `--from` or `--commit`, so the release tool picks the commit it ships, which may lack one{then}",
        f"tools/ci release start --from {older} --order 7ab1e20": f"{asked} at `{older}` it lacks #7 ({merge[:12]}){then}",
        f"tools/ci release start --manual --stack datadog/plat-usw2-prod --commit {older} --order 7ab1e20": f"{asked} at `{older}` it lacks #7 ({merge[:12]}){then}",
        f"tools/ci release start --from {merge} --order 7ab1e20": None,
        "tools/ci release start --landed 7 --order 7ab1e20": None,
        f"tools/ci release start --from {older} --rollback --order 7ab1e20": None,
        "tools/ci release start --hotfix 101 --only api --without tnt-usw2-acme --order 5d17d9c": None,
        f"tools/ci release start sand --from {older} --order 5d0c4a1": None,
        "tools/ci release start dashboard --env plat-usw2-staging": None,
    }
    assert {start: brief.uncarried(start, data) for start in starts} == starts
    del pr_reads[f"{repo}#8"]
    assert brief.uncarried(f"tools/ci release start --from {merge} --order 7ab1e20", data) == (
        "whether this release's commit holds each merged pull request the card his `--order` answer answers names could not be read: GitHub did not read #8"
    )


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
MOVES_ORDER = {
    "id": "b5763df89e6e6d0ee4cd19fa27b46eceeafb32a3",
    "title": "The memory moves are held by two guards: run them anyway?",
    "body": "Just run all the moves and make memories work 100% again",
    "tags": ["scope:durable", "drive:demo"],
    "created_at": "2026-11-14T06:08:00Z",
}
DRY_RUNS_HELD = {
    "id": "34c9f219cfd57167234ad1dda51bbf9b686ea77e",
    "title": "May old-run dry runs start in staging, plat-usw2-prod, tnt-usw2-0ddq7rb and tnt-usw2-1frg9c7 while you hold releases?",
    "body": "Start the staging dry run only, the other three after your hold lifts",
    "tags": ["drive:demo", "from:owner", "scope:durable"],
    "created_at": "2026-11-13T22:15:56Z",
}
PASS_TONIGHT = {
    "id": "c4701c972a8980dd37cee0fcb9e9f4cc36838e93",
    "title": "Should the one-time journal pass now raise every landed run journal on plat, AIG's and SoFi's clusters to format 15?",
    "body": "Cluster by cluster tonight: plat, then AIG's, then SoFi's, each write after its dry run.",
    "tags": ["drive:demo", "scope:durable"],
    "created_at": "2026-11-15T19:47:24Z",
}
DRY_RUN_JOB = {
    "cluster": "plat-usw2-prod",
    "cronJob": "journal-pass-dry-run",
    "reason": (
        "Journal pass dry run on plat-usw2-prod for each team's landed run journal count; writes nothing. Owner's pick c4701c9:"
        " Cluster by cluster tonight, plat first, each write after its dry run (drive durable-agents j3299)"
    ),
}
DRY_RUN_UNBLOCKED = "2026-11-15T20:26:37+00:00"
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


def test_while_he_holds_every_release_a_drive_runs_none_but_on_his_exception_recorded_since(
    demo: str, no_calls: list[list[str]], recorded: Answers, jev: Jev, release_builds: None
) -> None:
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
        "action": f"start this release on {settled.PRODUCTION}: `{start.format('8b2e4f7')}`",
        "answer": {"asked": SSQL_EXCEPTION["title"], "said": SSQL_EXCEPTION["body"]},
    }
    asked = len(jev.asked)
    refused = [landing_guard.deny_reason(bash(command) | top) for command in HELD_VERBS]
    assert refused[0] == held("tools/ci release resume 15495", "it names no `--order`, and neither does a `Release:` line of its job's brief")
    assert [reason.startswith(f'refused: he holds every release reaching past the rehearsal envs of the release provider, by his words "{RELEASE_HOLD["body"]}" (15c4de1, {HOLD_LINK})') for reason in refused] == [True] * len(HELD_VERBS)
    assert len(jev.asked) == asked
    jev.picks("none")
    assert brief.approval_refusal(start.format("8b2e4f7"), data) == held(
        start.format("8b2e4f7"), '`--order 8b2e4f7` names his answer to "Release the SandSQL fix to production?", and Opus does not read it as approving this release'
    )
    by_hand = ("tools/ci release hotfix 55960 --only executor", "drive break-glass j1 --stack sandsql/plat-usw2-prod --reason stuck -- tools/ci release start sandsql")
    assert [landing_guard.deny_reason(bash(command) | top) for command in by_hand] == [
        landing_guard.RELEASE_BY_HAND.format(command=command) for command in ("tools/ci release hotfix 55960 --only executor", "tools/ci release start sandsql")
    ]
    jev.answer = None
    assert brief.approval_refusal(start.format("8b2e4f7"), data) == held(
        start.format("8b2e4f7"), "Opus did not settle whether his answer 8b2e4f7 approves it (no jev decision within 20s)"
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


PLAN_THREAD = "https://acme.slack.com/archives/D1/p{}?thread_ts=1794774184.502879&cid=D1"
RELEASE_PLAN = {
    "id": "42a0764" + "0" * 33,
    "title": "Owner 2:32 PM Nov 15 (ruling, his release card's thread): how should the whole-platform release go out without hurting production?",
    "body": (
        "we need to get this out without fucking yo production so ideally we do 1) hotfx release with his fix only, 2) release with everything other than ssql, 3) ssql once we figure out how to not pause production"
        '\nNotes: "his bug fix" is the run-secrets fix, #56539, which the 11:40 AM order linked.'
    ),
    "tags": ["scope:durable", "drive:demo", "ruling"],
    "created_at": "2026-11-15T22:33:40Z",
}
HOTFIX_GO = {
    "id": "9bce65a" + "0" * 33,
    "title": "Nov 15 2:50 PM, his release card's thread: is the production hotfix of the run-secrets fix #56539 approved, and how do the second and third releases go?",
    "body": "Approved that one. Work on the second release then, everything but ssql",
    "tags": ["scope:durable", "drive:demo"],
    "created_at": "2026-11-15T22:54:43Z",
}
ALL_THREE = {
    "id": "304ffb2" + "0" * 33,
    "title": "Nov 15 3:00 PM, his release card's thread: are the hotfix, the second release and the third release approved, and does anything in them wait on him?",
    "body": "All 3 of these are approved given my constraints and top priority don't block on me for anything in them just get it done",
    "tags": ["scope:durable", "drive:demo"],
    "created_at": "2026-11-15T23:01:30Z",
}


def test_his_answer_pointing_at_releases_his_earlier_answers_in_its_thread_name_orders_each_while_a_dangerous_start_still_waits_for_him_once_he_is_quiet_15_minutes(
    demo: str, recorded: Answers, jev: Jev
) -> None:
    with state.edit("demo") as data:
        data["answers"]["15c4de1"] = {"words": RELEASE_HOLD["body"], "link": HOLD_LINK, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {}}
        for answer, message in ((RELEASE_PLAN, "1794781948643349"), (HOTFIX_GO, "1794782083000100"), (ALL_THREE, "1794782490000100")):
            data["answers"][answer["id"]] = {"words": settled.his(answer["body"]), "link": PLAN_THREAD.format(message), "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {}}
        data["owner"]["heard"] = state.stamp(state.now() - timedelta(minutes=15))
    recorded.listed = [RELEASE_HOLD, SSQL_EXCEPTION, RELEASE_PLAN, HOTFIX_GO, ALL_THREE]
    recorded.shown = {answer["id"][:7]: answer for answer in recorded.listed}
    data = state.load("demo")
    plan = {"asked": RELEASE_PLAN["title"], "said": settled.his(RELEASE_PLAN["body"]), "notes": '"his bug fix" is the run-secrets fix, #56539, which the 11:40 AM order linked.'}
    releases = [f"tools/ci release {release} --order 304ffb2" for release in ("hotfix 56539", "start --without sandsql", "start sandsql")]
    jev.picks("304ffb2")
    assert [brief.approval_refusal(release, data) for release in releases] == [None] * 3
    assert [asked["state"]["earlier"] for asked in jev.asked] == [{"9bce65a": {"asked": HOTFIX_GO["title"], "said": HOTFIX_GO["body"], "notes": ""}, "42a0764": plan}] * 3
    assert [list(asked["state"]["earlier"]) for asked in jev.asked] == [["9bce65a", "42a0764"]] * 3
    jev.picks("9bce65a")
    assert brief.approval_refusal("tools/ci release hotfix 56539 --order 9bce65a", data) is None
    assert jev.asked[-1]["state"]["earlier"] == {"42a0764": plan}
    assert brief.away_refusal("start", releases[2], data, state.now()).startswith(
        "refused: he last wrote or tapped 15 minutes ago, and while he is not present a production release starts or resumes only as a hotfix"
    )


def test_a_cluster_job_build_is_no_release_cut_so_any_drive_session_opens_its_gate_on_his_answer_since_ordering_it_while_he_holds_every_release(
    drive_pr: str, run: Run, no_calls: list[list[str]], recorded: Answers, jev: Jev, release_builds: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit("demo") as data:
        data["answers"]["15c4de1"] = {"words": RELEASE_HOLD["body"], "link": HOLD_LINK, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {}}
    recorded.listed = [RELEASE_HOLD]
    recorded.shown = {"15c4de1": RELEASE_HOLD, "b5763df": MOVES_ORDER}
    recorded.matched = [{"id": "b5763df", "asked": MOVES_ORDER["title"], "said": MOVES_ORDER["body"], "link": None}]
    jev.picks("b5763df")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "drive-sub")
    assert run("start", "demo", "--role", "coordinator", "--project", "The drive")[0] == 0
    top, sub = {"session_id": "session-under-test"}, {"session_id": "drive-sub"}
    job = {"session_id": "session-under-test", "agent_id": "a4da3dca63d05a40d", "agent_type": "long-running:builder"}
    gate = "tools/ci bk api -X PUT /pipelines/release/builds/{}/jobs/01a1d8aa/unblock"
    moves = (
        gate.format(CLUSTER_JOB_BUILD),
        f"tools/ci bk rerun release {CLUSTER_JOB_BUILD} --unit cluster-job",
        "tools/ci cluster-job --cluster plat-usw2-prod --cronjob memory-move-poetic --reason 'move poetic memories'",
    )
    assert [landing_guard.deny_reason(bash(command) | who) for who in (top, sub, job) for command in moves] == [None] * 9
    assert landing_guard.deny_reason(bash(f"tools/ci bk cancel release {CLUSTER_JOB_BUILD}") | job) is None
    cut = gate.format(RELEASE_CUT_BUILD)
    assert landing_guard.deny_reason(bash(cut) | top) == held(cut, "it names no `--order`, and neither does a `Release:` line of its job's brief")
    assert landing_guard.deny_reason(bash(cut) | sub).startswith("ci bk api: the sub-coordinator for The drive starts, approves and rolls back no production cut")
    assert landing_guard.deny_reason(bash(f"tools/ci bk cancel release {RELEASE_CUT_BUILD}") | job) == landing_guard.RELEASE_CANCEL
    unread = gate.format("15700")
    monkeypatch.setattr(landing_guard, "UNJUDGED", [])
    unreadable = "could not read forge/release build 15700 from Buildkite: HTTP 404"
    assert landing_guard.deny_reason(bash(unread) | top) == landing_guard.JOB_UNREAD.format(command=unread, error=unreadable)
    assert list(dict.fromkeys(landing_guard.UNJUDGED)) == [landing_guard.CUT_UNRESOLVED.format(command=unread, build="15700", error=unreadable)]


def test_a_forge_release_build_created_with_its_env_inline_is_judged_by_that_env_so_a_tester_starts_a_staging_cluster_job_at_a_prs_commit_while_he_holds_every_release(
    drive_pr: str, no_calls: list[list[str]], recorded: Answers, jev: Jev, release_builds: None
) -> None:
    with state.edit("demo") as data:
        data["answers"]["15c4de1"] = {"words": RELEASE_HOLD["body"], "link": HOLD_LINK, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {}}
    recorded.listed = [RELEASE_HOLD]
    recorded.shown = {"15c4de1": RELEASE_HOLD}
    top = {"session_id": "session-under-test"}
    job = {"session_id": "session-under-test", "agent_id": "a4da3dca63d05a40d", "agent_type": "long-running:builder"}

    def body(env: dict) -> str:
        return json.dumps({"branch": "dev", "commit": "79955168ce1a75e2878561170a0828b0ab941d6f", "env": env, "ignore_pipeline_branch_filters": True})

    def created(env: dict, flag: str = "-d ") -> str:
        return f"tools/ci bk api -X POST {flag}{shlex.quote(body(env))} /pipelines/release/builds"

    staging = {"CLUSTER_JOB": json.dumps(DRY_RUN_JOB | {"cluster": "plat-usw2-staging"})}
    starts = (
        *(created(staging, flag) for flag in ("-d ", "-d=", "--d ", "--d=")),
        f'body={shlex.quote(body(staging))}; tools/ci bk api -X POST -d "$body" /pipelines/release/builds',
    )
    assert [landing_guard.deny_reason(bash(command) | who) for who in (top, job) for command in starts] == [None] * 10
    production = created({"CLUSTER_JOB": json.dumps(DRY_RUN_JOB)})
    started = f"tools/ci cluster-job --cluster plat-usw2-prod --cronjob journal-pass-dry-run --reason {shlex.quote(DRY_RUN_JOB['reason'])}"
    refused = landing_guard.deny_reason(bash(started) | top)
    assert "so the journal-pass-dry-run Job on plat-usw2-prod, a production cluster, starts only on an answer of his recorded since" in refused
    assert landing_guard.deny_reason(bash(production) | top) == refused
    cuts = (
        created(staging | {"RELEASE_START": json.dumps({"mode": "release", "envs": []})}),
        created(staging | {"RELEASE_CLEAN": "1"}),
        created({"CLUSTER_JOB": json.dumps({"cronJob": "journal-pass-dry-run", "reason": "names no cluster"})}),
        created({"CLUSTER_JOB": json.dumps(DRY_RUN_JOB | {"cluster": "plat-usw2-staging", "reason": 7})}),
        'tools/ci bk api -X POST -d "$(cat /work/build.json)" /pipelines/release/builds',
        "tools/ci bk api -X POST /pipelines/release/builds",
    )
    unordered = "it names no `--order`, and neither does a `Release:` line of its job's brief"
    assert [landing_guard.deny_reason(bash(command) | top) for command in cuts] == [held(" ".join(shlex.split(command)), unordered) for command in cuts]


def test_a_staging_cluster_job_waits_out_another_drives_staging_claim_so_a_tester_starts_no_dry_run_inside_a_window_that_drive_claimed_and_froze(
    drive_pr: str, no_calls: list[list[str]], recorded: Answers, jev: Jev, monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit("demo") as data:
        data["answers"]["15c4de1"] = {"words": RELEASE_HOLD["body"], "link": HOLD_LINK, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {}}
    recorded.listed = [RELEASE_HOLD]
    recorded.shown = {"15c4de1": RELEASE_HOLD}
    ts, ends = "1794784882.120469", state.now() + timedelta(hours=2)
    link = f"https://example.slack.com/archives/C0STAGING1/p{ts.replace('.', '')}"
    claimant = state.new_drive("durable-agents", "coordinator", "session-of-durable-agents", "/durable-agents")
    claimant["orders"]["o2046"] = state.new_order("test the agent runtime on staging", "terminal", None, {}, state.stamp()) | {
        "claims": {ts: {"channel": "C0STAGING1", "link": link, "until": state.stamp(ends), "since": ts, "said": None, "replies_read": None}}
    }
    state.create(claimant)
    staging = DRY_RUN_JOB | {"cluster": "plat-usw2-staging"}
    builds = ("16992", "16994", "16997", "16998", "17001")
    monkeypatch.setattr(github, "build", lambda pipeline, number: {"state": "passed", "env": {"CLUSTER_JOB": json.dumps(staging)}})
    body = json.dumps({"branch": "dev", "commit": "79955168ce1a75e2878561170a0828b0ab941d6f", "env": {"CLUSTER_JOB": json.dumps(staging)}, "ignore_pipeline_branch_filters": True})
    starts = (
        f"tools/ci cluster-job --cluster plat-usw2-staging --cronjob journal-pass-dry-run --reason {shlex.quote(staging['reason'])}",
        f"tools/ci bk api -X POST -d {shlex.quote(body)} /pipelines/release/builds",
        *(f"tools/ci bk rerun release {number}" for number in builds),
        f"tools/ci bk api -X PUT /pipelines/release/builds/{builds[0]}/jobs/01a1e0e3-6926-4d67-abb9-9ed1e56a7549/unblock",
    )
    top = {"session_id": "session-under-test"}
    job = {"session_id": "session-under-test", "agent_id": "a4da3dca63d05a40d", "agent_type": "long-running:builder"}
    claimed = brief.CLAIMED_JOB.format(drive="durable-agents", order="o2046", until=state.stamp(ends), link=link, cronjob="journal-pass-dry-run", cluster="plat-usw2-staging")
    assert [landing_guard.deny_reason(bash(command) | who) for who in (top, job) for command in starts] == [claimed] * 16
    assert landing_guard.deny_reason(bash(starts[0].replace("plat-usw2-staging", "plat-usw2-sandbox")) | job) is None
    monkeypatch.setattr(state, "now", lambda: ends + timedelta(minutes=1))
    assert [landing_guard.deny_reason(bash(command) | job) for command in starts] == [None] * 8


def test_while_he_holds_every_release_build_16916s_gate_stays_shut_since_no_answer_of_his_since_orders_its_production_dry_run_at_that_moment(
    drive_pr: str, run: Run, no_calls: list[list[str]], recorded: Answers, jev: Jev, monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit("demo") as data:
        data["answers"]["15c4de1"] = {"words": RELEASE_HOLD["body"], "link": HOLD_LINK, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {}}
    tap = PR_TAP | {"created_at": "2026-11-14T01:00:00Z"}
    earlier = RELEASE_PICK | {"created_at": EARLIER}
    recorded.listed = [RELEASE_HOLD]
    recorded.shown = {answer["id"][:7]: answer for answer in (RELEASE_HOLD, DRY_RUNS_HELD, PASS_TONIGHT, tap, earlier)}
    recorded.matched = [{"id": answer["id"][:7], "asked": answer["title"], "said": settled.his(answer["body"]), "link": None} for answer in (DRY_RUNS_HELD, tap, earlier)]
    monkeypatch.setattr(github, "build", lambda pipeline, number: {"state": "canceled", "env": {"CLUSTER_JOB": json.dumps(DRY_RUN_JOB)}})
    monkeypatch.setattr(state, "now", lambda: state.parse_time(DRY_RUN_UNBLOCKED))
    jev.breaks("about", "34c9f21")
    top = {"session_id": "session-under-test"}
    starts = (
        "tools/ci bk api -X PUT /pipelines/release/builds/16916/jobs/01a1e0e3-6926-4d67-abb9-9ed1e56a7549/unblock",
        "tools/ci bk rerun release 16916",
        f'tools/ci cluster-job --cluster plat-usw2-prod --cronjob journal-pass-dry-run --reason "{DRY_RUN_JOB["reason"]}"',
    )
    unordered = "which Opus does not read as ordering it now"
    why = (
        f'his answer c4701c9 to "{PASS_TONIGHT["title"]}", "{PASS_TONIGHT["body"]}", {unordered};'
        f' his answer 34c9f21 to "{DRY_RUNS_HELD["title"]}", "{DRY_RUNS_HELD["body"]}", {unordered}'
    )
    refused = brief.JOB_HELD.format(said=RELEASE_HOLD["body"], where=f"15c4de1, {HOLD_LINK}", cronjob="journal-pass-dry-run", cluster="plat-usw2-prod", why=why)
    assert [landing_guard.deny_reason(bash(command) | top) for command in starts] == [refused] * len(starts)
    assert "15c4de1" in refused and "34c9f21" in refused
    assert jev.asked[-1]["state"] == {
        "job": {"cronjob": "journal-pass-dry-run", "cluster": "plat-usw2-prod", "reason": DRY_RUN_JOB["reason"], "now": "Sunday Nov 15 2026, 12:26 PM Pacific"},
        "hold": {"said": RELEASE_HOLD["body"], "at": "Friday Nov 13 2026, 11:09 AM Pacific"},
        "answers": {
            "c4701c9": {"id": "c4701c9", "asked": PASS_TONIGHT["title"], "said": PASS_TONIGHT["body"], "at": "Sunday Nov 15 2026, 11:47 AM Pacific", "card": ""},
            "34c9f21": {"id": "34c9f21", "asked": DRY_RUNS_HELD["title"], "said": DRY_RUNS_HELD["body"], "at": "Friday Nov 13 2026, 2:15 PM Pacific", "card": ""},
            "ac61348": {"id": "ac61348", "asked": PR_TAP["title"], "said": "Approve at 4aca314", "at": "Friday Nov 13 2026, 5:00 PM Pacific", "card": ""},
        },
    }
    assert sorted(jev.asked[-1]["questions"]) == sorted(f"{question}.{slot}" for question in ("orders", "about") for slot in ("c4701c9", "34c9f21", "ac61348"))
    assert run("job", "held", "j1", "--cluster", "plat-usw2-prod", "--cronjob", "journal-pass-dry-run", "--reason", DRY_RUN_JOB["reason"]) == (0, refused)
    asked = len(jev.asked)
    staging = "tools/ci cluster-job --cluster plat-usw2-staging --cronjob journal-pass-dry-run --reason 'dry run on staging'"
    assert landing_guard.deny_reason(bash(staging) | top) is None
    assert len(jev.asked) == asked
    older = "tools/ci cluster-job --cluster plat-usw2-prod --cronjob journal-pass-dry-run --reason 'dry run on his answer 5d0c4a1'"
    assert landing_guard.deny_reason(bash(older) | top) == brief.JOB_HELD.format(
        said=RELEASE_HOLD["body"],
        where=f"15c4de1, {HOLD_LINK}",
        cronjob="journal-pass-dry-run",
        cluster="plat-usw2-prod",
        why=f'its reason names his answer 5d0c4a1 to "{RELEASE_PICK["title"]}", recorded before the hold; his answer 34c9f21 to "{DRY_RUNS_HELD["title"]}", "{DRY_RUNS_HELD["body"]}", {unordered}',
    )
    jev.answer = None
    assert landing_guard.deny_reason(bash(starts[0]) | top) == brief.JOB_HELD.format(
        said=RELEASE_HOLD["body"],
        where=f"15c4de1, {HOLD_LINK}",
        cronjob="journal-pass-dry-run",
        cluster="plat-usw2-prod",
        why="Opus did not settle whether his answers since order it (no jev decision within 20s)",
    )
    read = "tools/ci cluster-job --cluster plat-usw2-prod --cronjob journal-pass-dry-run --reason 'c4701c9 reading: his words order the dry run tonight'"
    assert landing_guard.deny_reason(bash(read) | top) is None
    jev.picks("c4701c9")
    assert [landing_guard.deny_reason(bash(command) | top) for command in starts] == [None] * len(starts)


def test_a_job_runs_the_release_its_brief_names_on_his_exception_while_he_holds_every_release(
    demo: str, tmp_path: Path, no_calls: list[list[str]], recorded: Answers, jev: Jev, release_builds: None
) -> None:
    recorded.listed = [RELEASE_HOLD, SSQL_EXCEPTION, RELEASE_PICK | {"created_at": EARLIER}]
    recorded.shown = {answer["id"][:7]: answer for answer in recorded.listed}
    release = "tools/ci release start sandsql --order 8b2e4f7"
    step = stepped(tmp_path, f"Release: {release}\nRelease build: {BUILD}\nDrive: demo, order o1, job j1")
    jev.picks("8b2e4f7")
    own = ("tools/ci release approve releases/2026-11-13/2 --via codex", "tools/ci release resume 15495", "tools/ci bk api -X PUT organizations/forge/pipelines/release/builds/15495/jobs/0190a1b2/unblock")
    assert [landing_guard.deny_reason(bash(command) | step) for command in own] == [None] * len(own)
    assert landing_guard.deny_reason(bash(release) | step) == landing_guard.RELEASE_BY_HAND.format(command=release)
    assert {asked["state"]["action"] for asked in jev.asked} == {f"start this release on {settled.PRODUCTION}: `{release}`"}
    jev.picks("none")
    assert landing_guard.deny_reason(bash(own[0]) | step) == brief.RELEASE_HELD.format(
        said=RELEASE_HOLD["body"], where="15c4de1", hold="15c4de1", release=release, why='`--order 8b2e4f7` names his answer to "Release the SandSQL fix to production?", and Opus does not read it as approving this release'
    )
    stale = "tools/ci release start sandsql --order 5d0c4a1"
    briefed = stepped(tmp_path, f"Release: {stale}\nDrive: demo, order o1, job j1")
    assert landing_guard.deny_reason(bash(own[0]) | briefed) == brief.RELEASE_HELD.format(
        said=RELEASE_HOLD["body"], where="15c4de1", hold="15c4de1", release=stale, why='`--order 5d0c4a1` names his answer to "Release the memory fix to production now?", recorded before the hold'
    )
    assert landing_guard.deny_reason(bash("tools/ci bk cancel release 15495") | briefed) == landing_guard.RELEASE_CANCEL


def test_a_watchdogs_rerun_of_a_release_build_meets_his_hold_on_the_release_its_job_started_and_passes_on_the_pass_that_job_kept(
    demo: str, run: Run, recorded: Answers, jev: Jev, monkeypatch: pytest.MonkeyPatch, release_builds: None
) -> None:
    recorded.listed = [RELEASE_HOLD, SSQL_EXCEPTION]
    recorded.shown = {answer["id"][:7]: answer for answer in recorded.listed}
    release = "tools/ci release start sandsql --order 8b2e4f7"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "release the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "release the fix")
    with state.edit(demo) as data:
        data["jobs"]["j1"] |= {"brief_flags": {"release": release}, "release_build": f"https://buildkite.com/forge/release/builds/{RELEASE_CUT_BUILD}"}
    monkeypatch.setenv(state.DRIVE_ENV, demo)
    rerun = bash(f"tools/ci bk rerun release {RELEASE_CUT_BUILD}") | {"session_id": "watchdog-session"}
    jev.picks("none")
    refused = landing_guard.deny_reason(rerun)
    assert refused.startswith("refused: he holds every release") and f"`{release}` runs only on an answer of his" in refused
    with state.edit(demo) as data:
        data["jobs"]["j1"]["release_pass"] = {"release": release, "at": state.stamp(), "reading": None}
    asked = len(jev.asked)
    assert landing_guard.deny_reason(rerun) is None
    assert len(jev.asked) == asked


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
    assert [asked["state"]["action"] for asked in jev.asked] == [f"start this release on {settled.PRODUCTION}: `{release}`"]


def test_a_running_codex_jobs_resume_and_wake_hold_each_text_its_run_hears_once_at_its_next_bash_call(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_calls: list[list[str]]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix the incident")
    run("job", "add", "codex", "--order", "o1", "--goal", "fix the incident")
    with state.edit("demo") as data:
        state.start(data["jobs"]["j1"])
        data["jobs"]["j1"]["codex_run"] = str(tmp_path / "codex")
    held = "j1: held under From the coordinator since: for its codex-ask run, which landing_guard hands it at the run's next Bash call; the watch's next launch carries it if the run ends first"
    resumed, woken, refused = "Block 3 is the router's retry loop: drop it", "No commit in 30 minutes", "Leave the release running"
    assert run("job", "resume", "j1", resumed) == (0, held)
    assert run("job", "wake", "j1", "--text", woken) == (0, held)
    monkeypatch.setenv(state.OWN_JOB_ENV, "j1")
    monkeypatch.setenv(state.DRIVE_ENV, "demo")
    call = bash("git status") | {"session_id": "a-codex-session", "transcript_path": str(tmp_path / "rollout.jsonl")}
    told = "From the coordinator since:\n- "
    assert json.loads(hook_output(landing_guard, monkeypatch, capsys, json.dumps(call))) == {
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": f"{told}{resumed}\n- {woken}"}
    }
    assert state.load("demo")["jobs"]["j1"]["undelivered"] == []
    assert hook_output(landing_guard, monkeypatch, capsys, json.dumps(call)) == ""
    assert run("job", "resume", "j1", refused) == (0, held)
    cancel = call | {"tool_input": {"command": "tools/ci release cancel releases/2026-11-13/2"}}
    denied = landing_guard.denial(landing_guard.RELEASE_CANCEL)
    denied["hookSpecificOutput"]["additionalContext"] = f"{told}{refused}"
    assert json.loads(hook_output(landing_guard, monkeypatch, capsys, json.dumps(cancel))) == denied
    assert run("job", "resume", "j1", resumed) == (0, held)
    with state.edit("demo") as data:
        data["jobs"]["j1"]["state"] = "stopped"
    assert hook_output(landing_guard, monkeypatch, capsys, json.dumps(call)) == ""
    assert state.load("demo")["jobs"]["j1"]["undelivered"] == []
    with state.edit("demo") as data:
        data["jobs"]["j1"]["state"] = "waiting"
    assert run("job", "wake", "j1", "--text", woken)[0] == 2
    assert state.load("demo")["jobs"]["j1"]["undelivered"] == []


def test_a_codex_runs_cci_post_to_another_jobs_id_is_held_on_that_job_and_one_to_a_lane_nobody_reads_is_refused(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_calls: list[list[str]]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix the incident")
    run("job", "add", "codex", "--order", "o1", "--goal", "fix the incident")
    run("order", "add", "--title", "Other title", "--project", "The drive", "--workstream", "Drive tooling", "review the fix")
    asked = run("job", "add", "codex", "--order", "o2", "--goal", "review the fix")[1].splitlines()[0]
    with state.edit("demo") as data:
        state.start(data["jobs"]["j1"])
    monkeypatch.setenv(state.OWN_JOB_ENV, "j1")
    monkeypatch.setenv(state.DRIVE_ENV, "demo")
    call = bash(f"cci post --kind ask --to {asked} 'which port does the router retry on?'") | {"session_id": "a-codex-session", "transcript_path": str(tmp_path / "rollout.jsonl")}
    assert hook_output(landing_guard, monkeypatch, capsys, json.dumps(call)) == ""
    assert [(held["words"], held["sender"], held["seq"]) for held in state.load("demo")["jobs"][asked]["undelivered"]] == [
        ("ask record from j1: which port does the router retry on?", "j1", None)
    ]
    assert state.load("demo")["jobs"]["j1"]["undelivered"] == []
    unread = call | {"tool_input": {"command": "cci post --kind ask --to coordinator 'which port?'"}}
    denied = json.loads(hook_output(landing_guard, monkeypatch, capsys, json.dumps(unread)))["hookSpecificOutput"]["permissionDecisionReason"]
    assert denied.startswith("refused, nothing posted: to coordinator: no session of drive demo reads that lane")


def test_drive_job_add_starts_no_release_his_hold_holds_and_starts_his_exception(
    demo: str, run: Run, recorded: Answers, jev: Jev, release_starts: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "release the fix")
    recorded.listed = [RELEASE_HOLD, SSQL_EXCEPTION, RELEASE_PICK | {"created_at": EARLIER}]
    recorded.shown = {answer["id"][:7]: answer for answer in recorded.listed}
    jev.picks("5d0c4a1")
    stale = "tools/ci release start sandsql --order 5d0c4a1"
    why = '`--order 5d0c4a1` names his answer to "Release the memory fix to production now?", recorded before the hold'
    assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", "release the fix", "--release", stale]) == 2
    assert capsys.readouterr().err.strip() == "drive: refused, no job added: " + brief.RELEASE_HELD.format(said=RELEASE_HOLD["body"], where="15c4de1", hold="15c4de1", release=stale, why=why)
    jev.picks("8b2e4f7")
    excepted = "tools/ci release start sandsql --order 8b2e4f7"
    assert run("job", "add", "builder", "--order", "o1", "--goal", "release the fix", "--release", excepted)[1].splitlines()[:2] == ["j1", starting(demo, "j1", excepted)]
    kept = state.load(demo)["jobs"]["j1"]["release_pass"]
    assert (kept["release"], kept["reading"]) == (excepted, None)
    assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", "read it", "--reading", "his words order it"]) == 2
    assert "--reading is the top coordinator's own reading of his words ordering the release --release starts, taken only from its session" in capsys.readouterr().err


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
    assert [(asked["state"]["answer"], list(asked["questions"])) for asked in jev.asked] == [({"asked": pick["title"], "said": pick["body"]}, [f"orders.{pick['id'][:7]}", "more"]) for pick in picks]
    jev.picks("none")
    assert brief.approval_refusal(J2666_START.replace("5419ffd", "6881a3a"), state.load("demo")) == (
        f'refused: `--order 6881a3a` names his answer to "{QUEUE_PICK["title"]}", "Enqueue it (Recommended)", and Opus does not read it'
        f" as approving this release. {brief.RELEASE_APPROVAL}"
    )


@pytest.mark.usefixtures("envs_release")
def test_his_number_on_a_kept_card_orders_the_release_its_option_names_on_the_places_his_words_past_it_name_and_no_other_release_with_no_jev_read(
    demo: str, run: Run, recorded: Answers, jev: Jev
) -> None:
    unread = CUT_PICK | {"id": "3c0ffee" + "0" * 33, "body": "Ship whichever is safest"}
    answered_cut(run, demo, [CUT_PICK, CUT_TAP, unread])
    with state.edit(demo) as data:
        data["answers"]["15c4de1"] = {"words": RELEASE_HOLD["body"], "link": HOLD_LINK, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {}}
    recorded.shown = {answer["id"][:7]: answer for answer in (CUT_PICK, CUT_TAP, unread)}
    data = state.load(demo)
    option = "`ci release start --hotfix 101 --only api --without tnt-usw2-acme`"
    picks = f'refused: `--order 5d17d9c` names his answer to "{CUT_PICK["title"]}", "{CUT_PICK["body"]}", and it picks option 1 of the card it answers, {option}'
    releases = {
        ACME_CUT.format("5d17d9c"): None,
        "tools/ci release start --hotfix 101 --only api --without tnt-usw2-acme --order 5d17d9c": None,
        "tools/ci release start --order 5d17d9c": f"{picks}, and this release ships another change. {brief.RELEASE_APPROVAL}",
        "tools/ci release start --hotfix 202 --only sandsql --order 5d17d9c": f"{picks}, and this release ships another change. {brief.RELEASE_APPROVAL}",
        "tools/ci release start --hotfix 101 --only api --env tnt-usw2-acme,tnt-usw2-north --order 5d17d9c": None,
        "tools/ci release start --hotfix 101 --only api --without plat-usw2-prod,tnt-usw2-north,tnt-usw2-acme --order 7ab1e20": (
            f'refused: `--order 7ab1e20` names his answer to "{CUT_TAP["title"]}", "The full release", and it picks option 2 of the card it answers,'
            f" `ci release start`, and this release ships another change. {brief.RELEASE_APPROVAL}"
        ),
        "tools/ci release start --order 7ab1e20": None,
        "tools/ci release start --only api --order 7ab1e20": (
            f'refused: `--order 7ab1e20` names his answer to "{CUT_TAP["title"]}", "The full release", and it picks option 2 of the card it answers,'
            f" `ci release start`, and this release ships another change. {brief.RELEASE_APPROVAL}"
        ),
    }
    assert {release: brief.approval_refusal(release, data) for release in releases} == releases
    assert jev.asked == []
    jev.picks("none")
    assert brief.approval_refusal("tools/ci release start --order 3c0ffee", data) == (
        f'refused: `--order 3c0ffee` names his answer to "{unread["title"]}", "{unread["body"]}", and Opus does not read it as approving this release. {brief.RELEASE_APPROVAL}'
    )
    [asked] = jev.asked
    assert (asked["state"]["action"], asked["state"]["answer"]) == (f"start this release on {settled.PRODUCTION}: `tools/ci release start --order 3c0ffee`", {"asked": unread["title"], "said": unread["body"]})
    assert asked["state"]["card"].splitlines()[:3] == [f"Question: {CUT_PICK['title']}", "Drive tooling: your pick decides which cut ships the api hotfix.", "Option 1 (recommended): Api alone everywhere but Acme's cluster -- Starts `release start --hotfix 101 --only api --without tnt-usw2-acme`, the hotfix's api change on every place but Acme's cluster."]
    recorded.listed = [RELEASE_HOLD, CUT_PICK]
    assert brief.approval_refusal(ACME_CUT.format("5d17d9c"), data) is None
    fewer = "tools/ci release start --hotfix 101 --only api --without plat-usw2-staging,tnt-usw2-acme --order 5d17d9c"
    assert brief.approval_refusal(fewer, data) is None
    assert brief.approval_refusal("tools/ci release start --order 5d17d9c", data) == held(
        "tools/ci release start --order 5d17d9c", f'`--order 5d17d9c` names his answer to "{CUT_PICK["title"]}", and it picks option 1 of the card it answers, {option}, and this release ships another change'
    )
    assert len(jev.asked) == 1


SOAK_CARD = """ASK Should the run-argument fix you approved soak on staging before it merges, or land on dev now as the incident's fix?
Drive tooling: your pick decides when the run-argument fix merges.
- Soak the branch on staging first: it merges only after real staging runs pass (recommended)
  The branch's cloudrunner-next build deploys to plat-usw2-staging, the half-hourly promote-staging schedule is frozen so dev does not replace it, and a staging team runs the six cases.
- Land it on dev now as the incident's fix: it merges tonight, proven by CI alone
  The lander squash-merges the fix onto dev, and promote-staging ships dev to staging at its next half-hour tick.

Findings:
Root cause: none: a pick of when the fix merges
Simplest: Land it on dev now as the incident's fix
Holds: none: no option changes code any job builds
"""
SOAK_PICK = {
    "id": "0a09bcc" + "0" * 33,
    "title": "Should the run-argument fix you approved soak on staging before it merges, or land on dev now as the incident's fix?",
    "body": "1 and then aggressively manually test it that should be the playbook",
    "tags": ["scope:durable"],
    "created_at": "2026-11-14T07:52:00Z",
}
SOAK = "tools/ci release start {targets} --from 216f90221a4708e2f694823ea207ae738992e829 --emergency --env {env} --order {order}"


@pytest.mark.usefixtures("envs_release")
def test_his_number_on_a_kept_card_orders_the_release_its_option_names_in_prose_with_no_jev_read_and_jev_reads_any_release_past_its_words(
    demo: str, run: Run, recorded: Answers, jev: Jev
) -> None:
    acme = SOAK_PICK | {"id": "0a1ace0" + "0" * 33, "body": "1 and then get Acme to the same"}
    answered_cut(run, demo, [SOAK_PICK, acme], SOAK_CARD)
    recorded.shown = {answer["id"][:7]: answer for answer in (SOAK_PICK, acme)}
    data = state.load(demo)
    assert brief.approval_refusal(SOAK.format(targets="cloudrunner-next", env="plat-usw2-staging,tnt-usw2-acme", order="0a1ace0"), data) is None
    assert jev.asked == []
    read = (
        (SOAK.format(targets="cloudrunner-next", env="plat-usw2-prod", order="0a09bcc"), SOAK_PICK),
        (SOAK.format(targets="cloudrunner-next,runtime-api-next", env="plat-usw2-staging,tnt-usw2-acme", order="0a1ace0"), acme),
        ("tools/ci release start cloudrunner-next --from 216f90221a4708e2f694823ea207ae738992e829 --emergency --order 0a09bcc", SOAK_PICK),
        (SOAK.format(targets="cloudrunner-next", env="plat-usw2-staging,tnt-usw2-acme", order="0a1ace0") + " --landed 56278", acme),
        (SOAK.format(targets="cloudrunner-next", env="plat-usw2-staging,tnt-usw2-acme", order="0a09bcc"), SOAK_PICK),
    )
    assert [brief.approval_refusal(release, data) for release, _ in read] == [
        f'refused: `--order {answer["id"][:7]}` names his answer to "{answer["title"]}", "{answer["body"]}", and Opus does not read it as approving this release. {brief.RELEASE_APPROVAL}'
        for _, answer in read
    ]
    staging = "plat-usw2-staging (stage staging), tnt-usw2-acme"
    places = ("plat-usw2-prod (stage prod)", staging, settled.PRODUCTION, staging, staging)
    assert [asked["state"]["action"] for asked in jev.asked] == [f"start this release on {where}: `{release}`" for where, (release, _) in zip(places, read, strict=True)]



LISTED_RELEASES = {
    "id": "304ffb2" + "0" * 33,
    "title": "Nov 15 3:00 PM: are his 2:32 PM plan's 3 releases approved? 1) `tools/ci release hotfix 56539 --only api --without plat-usw2-staging,plat-gbl-staging`, #56539 alone; 2) all but SandSQL; 3) SandSQL's HA release",
    "body": "All 3 of these are approved given my constraints and top priority don't block on me for anything in them just get it done",
    "tags": ["scope:durable"],
    "created_at": "2026-11-15T23:01:30Z",
}
RELEASE_TARGETS = """components:
  api:
    title: API
targets:
  platform:
    title: Platform
    components: [sanddb, api, forge-dns, router]
  dashboard:
    title: Dashboard
    components: [dashboard]
  sandsql:
    title: SandSQL
    components: [sandsql]
  infra:
    title: Infrastructure
    components:
    - accounts
    - storage
"""


@pytest.mark.usefixtures("envs_release")
def test_his_all_to_a_question_listing_releases_orders_each_listed_release_in_code_and_jev_reads_one_mixing_them(demo: str, run: Run, recorded: Answers, jev: Jev) -> None:
    answered_cut(run, demo, [CUT_PICK])
    data = state.load(demo)
    (Path(data["roles"]["coordinator"]["cwd"]) / "release").mkdir()
    (Path(data["roles"]["coordinator"]["cwd"]) / "release" / "targets.yaml").write_text(RELEASE_TARGETS)
    with state.edit(demo) as data:
        data["answers"][LISTED_RELEASES["id"]] = {"words": LISTED_RELEASES["body"], "link": drawn_card(99), "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {}}
    recorded.shown = {"304ffb2": LISTED_RELEASES}
    data = state.load(demo)
    ordered = (
        "tools/ci release hotfix 56539 --only api --without plat-usw2-staging,plat-gbl-staging --order 304ffb2",
        "tools/ci release start platform dashboard infra --from 216f90221a4708e2f694823ea207ae738992e829 --without plat-usw2-staging,plat-gbl-staging --order 304ffb2",
        "tools/ci release start --targets platform,sandsql --exclude sandsql --from 216f90221a4708e2f694823ea207ae738992e829 --order 304ffb2",
        "tools/ci release start sandsql --from 216f90221a4708e2f694823ea207ae738992e829 --order 304ffb2",
        "tools/ci release start sandsql --only sandsql/plat-usw2-prod --from 216f90221a4708e2f694823ea207ae738992e829 --order 304ffb2",
    )
    assert [brief.approval_refusal(release, data) for release in ordered] == [None] * len(ordered)
    assert jev.asked == []
    read = (
        "tools/ci release start platform sandsql --from 216f90221a4708e2f694823ea207ae738992e829 --order 304ffb2",
        "tools/ci release hotfix 56540 --order 304ffb2",
        "tools/ci release start nosuch --from 216f90221a4708e2f694823ea207ae738992e829 --order 304ffb2",
        "tools/ci release hotfix 56539 --only api,rsw-cp --order 304ffb2",
    )
    refused = f'refused: `--order 304ffb2` names his answer to "{brief.cut(LISTED_RELEASES["title"], 200)}", "{LISTED_RELEASES["body"]}", and Opus does not read it as approving this release. {brief.RELEASE_APPROVAL}'
    assert [brief.approval_refusal(release, data) for release in read] == [refused] * len(read)
    assert len(jev.asked) == len(read)
    turned = LISTED_RELEASES | {"id": "304ffb3" + "0" * 33, "body": "All 3 of these are not approved yet"}
    recorded.shown["304ffb3"] = turned
    assert brief.approval_refusal(ordered[3].replace("304ffb2", "304ffb3"), data) is not None

STANDING_SOAK_CARD = """ASK May the drive soak each dangerous branch on frozen staging under your playbook unasked, or is each soak your exception?
Drive tooling: your pick decides whether a staging-only soak waits for your word each time.
- A standing yes: the drive soaks any dangerous branch on staging alone, while production keeps waiting for your word (recommended)
  The session holding a dangerous branch freezes its component's staging stacks, starts it there with `tools/ci release start --emergency --env plat-usw2-staging`, runs its cases, then unfreezes.
- Each soak is your exception: every staging deploy of a branch waits on its own card
  Before each staging deploy, its job draws a card naming the branch and its start command, and nothing deploys until you tap it.

Findings:
Root cause: none: a pick of who approves a staging soak
Simplest: A standing yes: the drive soaks any dangerous branch on staging alone, while production keeps waiting for your word
Holds: none: no option changes code any job builds
"""
STANDING_SOAK = {
    "id": "23eefb9" + "0" * 33,
    "title": "May the drive soak each dangerous branch on frozen staging under your playbook unasked, or is each soak your exception?",
    "body": "A standing yes: the drive soaks any dangerous branch on staging alone, while production keeps waiting for your word",
    "tags": ["scope:durable"],
    "created_at": "2026-11-14T15:39:29Z",
}
SOAK_CASES = {
    "id": "3bfcce8" + "0" * 33,
    "title": "What must each staging soak of a dangerous branch involve beyond its own cases?",
    "body": "Each one should also involve some manual agent-led testing the truly tests all scenarios that could break on prod",
    "tags": ["scope:durable"],
    "created_at": "2026-11-14T16:02:00Z",
}
REHEARSALS = (
    "tools/ci release start runtime-api-next cloudrunner-next --from e299550554d12e3ad79acb5b736f9d998734f7be --emergency --env plat-usw2-staging",
    "tools/ci release start db platform restate runtime-api-next cloudrunner-next executor dashboard --from 044e8552b6941d1b184aca11b8e201fd0ade2e31 --emergency --env plat-usw2-staging",
    "tools/ci release start restate --only rsw-search --from 28f00bc3506ef72c80cbe879b21182da80c0a71b --emergency --hotfix 55865 --env plat-usw2-staging,plat-usw2-sandbox",
    "tools/ci release start platform --only api --from d28a1ecb7a48 --emergency --env plat-usw2-staging",
    "tools/ci release start --stack runtime-api-next/plat-usw2-staging,cloudrunner-next/plat-usw2-staging,dashboard/plat-usw2-staging,sandsql/plat-usw2-staging"
    " --commit afb881f9207650b5a87f1e2e10c6b4d53c2448d0 --manual --emergency",
    "tools/ci release start runtime-api-next cloudrunner-next dashboard sandsql --from 8ab0e2526f06587a3086ca246aff87f7b4ac005b --emergency --env plat-usw2-staging",
    "tools/ci release start --manual --stack rsw-search/plat-usw2-sandbox --commit 5a6f43db99dd9671fd132bce1c939e33ac3a2b81 --rollback",
    "tools/ci release start --stack sandsql/plat-usw2-sandbox --commit 3ebaa825dbd7116cb00a2f799c91861371d9e4d8 --manual --emergency",
)


@pytest.mark.usefixtures("envs_release")
def test_a_release_on_staging_or_sandbox_alone_runs_under_his_hold_with_no_order_and_no_jev_read_and_one_reaching_past_them_waits_on_his_word(
    demo: str, run: Run, recorded: Answers, jev: Jev
) -> None:
    answered_cut(run, demo, [STANDING_SOAK], STANDING_SOAK_CARD)
    with state.edit(demo) as data:
        data["answers"]["15c4de1"] = {"words": RELEASE_HOLD["body"], "link": HOLD_LINK, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {}}
    recorded.listed = [RELEASE_HOLD, STANDING_SOAK, SOAK_CASES]
    recorded.shown = {answer["id"][:7]: answer for answer in recorded.listed}
    data = state.load(demo)
    hold = settled.hold(data["roles"]["coordinator"]["cwd"])
    releases = [gated for release in REHEARSALS for gated in (release, f"{release} --order 3bfcce8")]
    assert [(brief.release_refusal(release, data), brief.approval_refusal(release, data), brief.hold_refusal(release, data, hold)) for release in releases] == [(None, None, None)] * len(releases)
    assert jev.asked == []
    first = REHEARSALS[0]
    past = [
        *(first.replace("plat-usw2-staging", place) for place in ("plat-usw2-prod", "plat-usw2-staging,plat-usw2-prod", "plat-gbl-staging")),
        first.replace(" --env plat-usw2-staging", ""),
        REHEARSALS[-1].replace("sandsql/plat-usw2-sandbox", "sandsql/plat-usw2-sandbox,sandsql/plat-usw2-prod"),
    ]
    assert [brief.release_refusal(release, data) for release in past] == [f"--release names no --order. {brief.RELEASE_APPROVAL}"] * len(past)
    assert [brief.release_refusal(release, data, "his words order it") for release in past] == [None] * len(past)
    assert [brief.hold_refusal(release, data, hold) for release in past] == [held(release, "it names no `--order`, and neither does a `Release:` line of its job's brief") for release in past]
    assert [brief.approval_refusal(release, data, "his words order it") for release in past] == [held(release, "it names no `--order`, and neither does a `Release:` line of its job's brief") for release in past]
    tested = f"{first} --for-production"
    assert (brief.release_refusal(tested, data), brief.hold_refusal(tested, data, hold)) == (
        f"--release names no --order. {brief.RELEASE_APPROVAL}",
        held(tested, "it names no `--order`, and neither does a `Release:` line of its job's brief"),
    )
    picks = f'`--order 23eefb9` names his answer to "{STANDING_SOAK["title"]}", and it picks option 1 of the card it answers, `tools/ci release start --emergency --env plat-usw2-staging`'
    ordered = [f"{release} --order 23eefb9" for release in past]
    assert [brief.approval_refusal(release, data) for release in ordered] == [held(release, f"{picks}, and this release ships another change") for release in ordered]
    assert jev.asked == []
    jev.picks("none")
    unapproved = f"{past[0]} --order 3bfcce8"
    assert brief.approval_refusal(unapproved, data) == held(unapproved, f'`--order 3bfcce8` names his answer to "{SOAK_CASES["title"]}", and Opus does not read it as approving this release')
    assert [asked["state"]["action"] for asked in jev.asked] == [f"start this release on plat-usw2-prod (stage prod): `{unapproved}`"]


def test_his_pick_on_a_card_another_drive_keeps_orders_its_release_on_this_drive_and_no_other_release_with_no_jev_read(
    demo: str, run: Run, recorded: Answers, jev: Jev
) -> None:
    picked = CUT_PICK | {"tags": ["scope:durable", f"drive:{demo}"]}
    answered_cut(run, demo, [picked])
    run("start", "ssql-duckdb", "--role", "coordinator")
    recorded.listed = [RELEASE_HOLD, picked]
    recorded.shown = {answer["id"][:7]: answer for answer in recorded.listed}
    data = state.load("ssql-duckdb")
    assert picked["id"] not in data["answers"]
    cut = "tools/ci release start --hotfix 101 --only api --without tnt-usw2-acme --order 5d17d9c"
    everything = "tools/ci release start --order 5d17d9c"
    picks = f'`--order 5d17d9c` names his answer to "{CUT_PICK["title"]}", and it picks option 1 of the card it answers, `ci release start --hotfix 101 --only api --without tnt-usw2-acme`'
    held_here = brief.RELEASE_HELD.format(said=RELEASE_HOLD["body"], where="15c4de1", hold="15c4de1", release=everything, why=f"{picks}, and this release ships another change")
    assert [brief.approval_refusal(release, data) for release in (cut, everything)] == [None, held_here]
    assert jev.asked == []
    recorded.shown["5d17d9c"] = picked | {"tags": ["scope:durable", "drive:gone"]}
    unread = f'`--order 5d17d9c` names his answer to "{CUT_PICK["title"]}", and {settled.UNORDERED}'
    assert brief.approval_refusal(cut, data) == brief.RELEASE_HELD.format(said=RELEASE_HOLD["body"], where="15c4de1", hold="15c4de1", release=cut, why=unread)
    assert len(jev.asked) == 1


SANDBOX_TESTS = {
    "id": "077d93a" + "0" * 33,
    "title": "What should happen to the from-scratch SandSQL handoff stack while he sleeps, and who decides its merges?",
    "body": "Heading to sleep but get everything built and tested on sandbox/staging and ready to merge and I can make any final decision in the morning",
    "tags": ["scope:durable", "drive:demo"],
    "created_at": "2026-11-15T12:04:59Z",
}


@pytest.mark.usefixtures("envs_release")
def test_jev_reads_a_stack_release_on_the_places_its_stacks_name_so_a_production_stack_reads_as_production(demo: str, run: Run, recorded: Answers, jev: Jev) -> None:
    answered_cut(run, demo, [STANDING_SOAK], STANDING_SOAK_CARD)
    with state.edit(demo) as data:
        data["answers"]["15c4de1"] = {"words": RELEASE_HOLD["body"], "link": HOLD_LINK, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {}}
    recorded.listed = [RELEASE_HOLD, SANDBOX_TESTS]
    recorded.shown = {answer["id"][:7]: answer for answer in recorded.listed}
    production = f'{REHEARSALS[-1].replace("plat-usw2-sandbox", "plat-usw2-prod")} --order 077d93a'
    jev.picks("none")
    assert brief.approval_refusal(production, state.load(demo)) == held(
        production, f'`--order 077d93a` names his answer to "{SANDBOX_TESTS["title"]}", and Opus does not read it as approving this release'
    )
    assert [asked["state"]["action"] for asked in jev.asked] == [f"start this release on plat-usw2-prod (stage prod): `{production}`"]


FOR_PRODUCTION_START = "tools/ci release start platform dashboard --from afb881f92076 --for-production --env plat-gbl-staging,plat-usw2-staging --order {order}"
WHOLE_PLATFORM = {
    "id": "f3633ed" + "0" * 33,
    "title": "What production release does he order past his release hold on Nov 15, and how are its commit and test chosen?",
    "body": "find a safe sha that has this fix and has all the stuff tested in staging, make sure we do a thorough test again at that commit of anything that can go wrong, then cut a release of whole platform",
    "tags": ["scope:durable", "drive:demo"],
    "created_at": "2026-11-15T19:51:37Z",
}
WHERE_CARD = """ASK Where should the whole platform go at the commit staging proved?
Drive tooling: your pick decides which production places get the whole platform at that commit.
- Every production place (recommended)
  The top coordinator starts `tools/ci release start platform dashboard --from afb881f92076` once its staging test passes.
- Production's own cluster alone
  The top coordinator starts `tools/ci release start platform dashboard --from afb881f92076 --env plat-usw2-prod` once its staging test passes.
- Every place but Acme's cluster
  The top coordinator starts `tools/ci release start platform dashboard --from afb881f92076 --without tnt-usw2-acme` once its staging test passes.

Findings:
Root cause: none: a pick of where the release goes
Simplest: Every production place
Holds: none: no option changes code any job builds
"""
WHERE_PICK = {
    "title": "Where should the whole platform go at the commit staging proved?",
    "tags": ["scope:durable", "drive:demo"],
    "created_at": "2026-11-15T20:00:00Z",
}


@pytest.mark.usefixtures("envs_release")
def test_a_staging_release_for_production_starts_on_his_pick_of_the_production_release_it_tests_wherever_that_goes_and_never_one_shipping_more_with_no_jev_read(
    demo: str, run: Run, recorded: Answers, jev: Jev
) -> None:
    picks = [WHERE_PICK | {"id": f"f0ca00{number}" + "0" * 33, "body": str(number)} for number in (1, 2, 3)]
    answered_cut(run, demo, picks, WHERE_CARD)
    with state.edit(demo) as data:
        data["answers"]["15c4de1"] = {"words": RELEASE_HOLD["body"], "link": HOLD_LINK, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {}}
    recorded.listed = [RELEASE_HOLD, *picks]
    recorded.shown = {answer["id"][:7]: answer for answer in recorded.listed}
    data = state.load(demo)
    assert [brief.approval_refusal(FOR_PRODUCTION_START.format(order=pick["id"][:7]), data) for pick in picks] == [None] * len(picks)
    wider = FOR_PRODUCTION_START.replace("dashboard", "dashboard sandsql").format(order="f0ca001")
    tested = settled.TESTED.format(
        release="tools/ci release start platform dashboard sandsql --from afb881f92076 --order f0ca001",
        why="it picks option 1 of the card it answers, `tools/ci release start platform dashboard --from afb881f92076`, and this release ships another change",
    )
    assert brief.approval_refusal(wider, data) == held(wider, f'`--order f0ca001` names his answer to "{WHERE_PICK["title"]}", and {tested}')
    assert jev.asked == []
    assert brief.tested_release("cd /work && tools/ci release start platform --env=plat-gbl-staging,plat-usw2-staging --for-production --from afb881f92076", {}) == "tools/ci release start platform --from afb881f92076"


@pytest.mark.usefixtures("envs_release")
def test_his_standing_yes_to_soak_and_his_words_to_test_on_staging_order_no_staging_release_for_production_and_jev_reads_his_other_words_against_the_production_release_it_tests(
    demo: str, run: Run, recorded: Answers, jev: Jev
) -> None:
    answered_cut(run, demo, [STANDING_SOAK], STANDING_SOAK_CARD)
    with state.edit(demo) as data:
        data["answers"]["15c4de1"] = {"words": RELEASE_HOLD["body"], "link": HOLD_LINK, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {}}
    recorded.listed = [RELEASE_HOLD, STANDING_SOAK, SANDBOX_TESTS, WHOLE_PLATFORM]
    recorded.shown = {answer["id"][:7]: answer for answer in recorded.listed}
    data = state.load(demo)
    soaked = FOR_PRODUCTION_START.format(order="23eefb9")
    unsoaked = settled.TESTED.format(
        release="tools/ci release start platform dashboard --from afb881f92076 --order 23eefb9",
        why=f"it picks option 1 of the card it answers, `tools/ci release start --emergency --env plat-usw2-staging`, {settled.UNTESTED}",
    )
    assert brief.approval_refusal(soaked, data) == held(soaked, f'`--order 23eefb9` names his answer to "{STANDING_SOAK["title"]}", and {unsoaked}')
    assert jev.asked == []
    jev.picks("orders")
    staging = FOR_PRODUCTION_START.replace(" --for-production", "").format(order="077d93a")
    assert [brief.approval_refusal(release, data) for release in (staging, FOR_PRODUCTION_START.format(order="f3633ed"))] == [None, None]
    jev.picks("none")
    tested = FOR_PRODUCTION_START.format(order="077d93a")
    untested = settled.TESTED.format(release="tools/ci release start platform dashboard --from afb881f92076 --order 077d93a", why=settled.UNORDERED)
    assert brief.approval_refusal(tested, data) == held(tested, f'`--order 077d93a` names his answer to "{SANDBOX_TESTS["title"]}", and {untested}')
    assert [asked["state"]["action"] for asked in jev.asked] == [
        f"start this release on plat-gbl-staging (stage staging), plat-usw2-staging (stage staging): `{staging}`",
        f"start this release on {settled.PRODUCTION}: `tools/ci release start platform dashboard --from afb881f92076 --order f3633ed`",
        f"start this release on {settled.PRODUCTION}: `tools/ci release start platform dashboard --from afb881f92076 --order 077d93a`",
    ]


def test_a_stack_names_its_project_as_a_target_and_its_env_as_a_place_beside_env_so_a_manual_deploy_ships_what_a_manual_option_names(
    demo: str, run: Run, recorded: Answers
) -> None:
    answered_cut(run, demo, [CUT_PICK])
    cwd = state.load(demo)["roles"]["coordinator"]["cwd"]
    [option] = brief.started("tools/ci release start sandsql --commit 3ebaa825dbd7 --manual --emergency --env plat-usw2-staging", {})
    manual = "tools/ci release start --stack sandsql/plat-usw2-staging --commit 3ebaa825dbd7 --manual --emergency"
    assert brief.started(f"{manual} --env plat-usw2-sandbox", {})[0] == {
        "targets": ("sandsql",),
        "env": ("plat-usw2-sandbox", "plat-usw2-staging"),
        "from": ("3ebaa825dbd7",),
        "manual": ("true",),
        "emergency": ("true",),
    }
    assert {
        release: settled.placed(brief.started(release, {})[0], option, "", cwd)
        for release in (manual, manual.replace("sandsql/", "restate/"), manual.replace("sandsql/plat-usw2-staging", "sandsql/plat-usw2-staging,sandsql/plat-gbl-staging"))
    } == {
        manual: None,
        manual.replace("sandsql/", "restate/"): "and this release ships another change",
        manual.replace("sandsql/plat-usw2-staging", "sandsql/plat-usw2-staging,sandsql/plat-gbl-staging"): "and this release also runs on plat-gbl-staging, which his words past its number do not name",
    }


@pytest.mark.usefixtures("envs_release")
def test_his_words_past_the_number_order_only_the_places_they_name(demo: str, run: Run, recorded: Answers) -> None:
    answered_cut(run, demo, [CUT_PICK])
    recorded.shown = {"5d17d9c": CUT_PICK}
    data = state.load(demo)
    release = "tools/ci release start --hotfix 101 --only api --env tnt-usw2-acme,tnt-usw2-north --without tnt-usw2-north --order 5d17d9c"
    assert brief.approval_refusal(release, data) is None
    option = {"hotfix": ("101",), "only": ("api",), "env": ("plat-usw2-prod",)}
    assert settled.beyond({"hotfix": ("101",), "only": ("api",), "env": ("plat-usw2-prod", "tnt-usw2-acme", "tnt-usw2-north")}, option) == {"tnt-usw2-acme", "tnt-usw2-north"}
    assert settled.placed({"hotfix": ("101",), "only": ("api",), "env": ("plat-usw2-prod", "tnt-usw2-acme", "tnt-usw2-north")}, option, " and then get Acme to the same", data["roles"]["coordinator"]["cwd"]) == (
        "and this release also runs on tnt-usw2-north, which his words past its number do not name"
    )
    assert settled.placed({"hotfix": ("101",), "only": ("api",)}, option, " and Acme too", data["roles"]["coordinator"]["cwd"]) == (
        "and this release runs on every place it does not leave out, where that option names its own"
    )
    assert settled.team_names("tnt-usw2-acme", data["roles"]["coordinator"]["cwd"]) == ["acme", "Acme"]
    assert settled.team_names("tnt-usw2-north", data["roles"]["coordinator"]["cwd"]) == []


HOTFIX_CARD = """ASK Should the production hotfix of the run-secrets fix alone start now?
Drive tooling: your pick is your exception to your release hold for this one start.
The fix changes only the api. The hotfix ships the api alone; SandSQL and every other stack stay as they are.
- Start it: the hotfix starts once its preview shows this fix alone, with 0 deletes and 0 replaces (recommended)
  `tools/ci release hotfix 101 --without plat-usw2-staging,plat-gbl-staging`, production only. Any change beyond this fix comes back to you before a start.
- Hold: nothing starts in production, and the fix waits for the next release
  Runs reading a secret keep getting a 403 until then.

Findings:
Root cause: none: a pick of whether the hotfix starts
Simplest: Start it
Holds: none: it decides only whether the production hotfix starts
"""
HOTFIX_TAP = {
    "id": "0a3bed1" + "0" * 33,
    "title": "Should the production hotfix of the run-secrets fix alone start now?",
    "body": "Start it: the hotfix starts once its preview shows this fix alone, with 0 deletes and 0 replaces",
    "tags": ["scope:durable"],
    "created_at": "2026-11-15T22:47:49Z",
}


def test_his_tap_on_a_card_redrawn_for_a_newer_question_orders_its_release_narrowed_by_only_to_the_components_the_card_says_ship_alone_with_no_jev_read(
    demo: str, run: Run, recorded: Answers, jev: Jev
) -> None:
    answered_cut(run, demo, [HOTFIX_TAP], HOTFIX_CARD)
    older = state.drive_dir(demo) / "asks" / "j1-older.md"
    older.write_text(ANSWERED_CUT)
    with state.edit(demo) as data:
        del data["answers"][HOTFIX_TAP["id"]]
        [newer] = data["jobs"]["j1"]["questions"]
        newer["closed"]["noted"] = HOTFIX_TAP["id"]
        replaced = state.new_question(str(older), CUT_PICK["title"], newer["asked"], card=CUT_LINK, closed=dict(newer["closed"]))
        replaced["replaced"] = newer["file"]
        data["jobs"]["j1"]["questions"].insert(0, replaced)
    recorded.shown = {"0a3bed1": HOTFIX_TAP}
    data = state.load(demo)
    picks = f'refused: `--order 0a3bed1` names his answer to "{HOTFIX_TAP["title"]}", "{HOTFIX_TAP["body"]}", and it picks option 1 of the card it answers, `tools/ci release hotfix 101 --without plat-usw2-staging,plat-gbl-staging`'
    hotfix = "tools/ci release hotfix 101 {only}--without plat-usw2-staging,plat-gbl-staging --order 0a3bed1"
    releases = {
        hotfix.format(only=""): None,
        hotfix.format(only="--only api "): None,
        hotfix.format(only="--only api,sandsql "): f"{picks}, and this release ships another change. {brief.RELEASE_APPROVAL}",
        hotfix.format(only="--only sandsql "): f"{picks}, and this release ships another change. {brief.RELEASE_APPROVAL}",
    }
    assert {release: brief.approval_refusal(release, data) for release in releases} == releases
    assert jev.asked == []
    narrow = {"hotfix": ("101",), "only": ("api",)}
    worded = ("It ships only the api.", "The api stack alone goes out.", "The fix changes only the api.", "It ships not only the api.", "It never ships only the api.", "The hotfix does not ship the api alone.", "The full release.")
    assert [settled.narrowed(narrow, {"hotfix": ("101",)}, words) for words in worded] == [{"hotfix": ("101",)}, {"hotfix": ("101",)}, narrow, narrow, narrow, narrow, narrow]
    assert settled.narrowed(narrow, narrow, "It ships the api alone.") == narrow


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
def test_guard_sees_a_landing_however_it_is_phrased(home_repo: None, drive_pr: str, no_calls: list[list[str]], command: str) -> None:
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
def test_guard_checks_every_pr_a_queue_submit_names(home_repo: None, stack: None, no_calls: list[list[str]], command: str) -> None:
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
    home_repo: None,
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
    home_repo: None,
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
    home_repo: None,
    drive_pr: str, no_calls: list[list[str]], command: str
) -> None:
    assert landing_guard.deny_reason(bash(command)).startswith(f"{drive_pr}: no tap from him yet")
    approve(drive_pr)
    assert landing_guard.deny_reason(bash(command)) is None
    driving = bash(command) | {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"]}
    assert landing_guard.deny_reason(driving) == QUEUE_REASON.format(key=drive_pr)
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


def test_bash_posts_no_ask_carrying_his_cards_ask_block_since_drive_job_ask_posts_it_and_a_lane_question_goes_out(run: Run, demo: str, no_calls: list[list[str]]) -> None:
    session = os.environ["CLAUDE_CODE_SESSION_ID"]
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "close the idle sessions")
    kept = state.drive_dir(demo) / "asks" / "j1-0.md"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("ASK Close these sessions?\nThe idle ones hold a slot each.\n")
    with state.edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "close them", [], state.stamp())
        data["jobs"]["j1"]["questions"] = [state.new_question(str(kept), "Close these sessions?", state.stamp())]
        data["subcoordinators"]["SandSQL"] = {"session": "sandsql-1", "terminal": None, "cwd": "/work", "started": state.stamp(), "from": 0}
    for command in (
        "cci post --kind ask --lane root --to communicator@demo 'ASK Close these sessions?'",
        "cd /work && cci post --lane root --kind=ASK --attr job=j1 'ASK Close these sessions?'",
        "zsh -c \"cci post --kind ask 'ASK Close these sessions?'\"",
        f"cci post --kind ask --attr ask={kept} --attr job=j1 --path {kept}",
        f"cci post --kind ask --attr ask={kept} --attr job=j1 --attr incident=true 'Close these sessions?'",
        "cci post --kind ask --lane root --to communicator@demo --attr waits=demo:j1-0 'Close these sessions?'",
    ):
        assert landing_guard.deny_reason(asked_from_bash(command, session)) == settled.HAND_ASK
    for command in (
        "cci post --kind ask --lane root --to sandsql@demo --attr job=j1 'Is the migration live on staging?'",
        "cci post --kind ask --lane root --to communicator@demo 'Is he present right now?'",
    ):
        assert landing_guard.deny_reason(asked_from_bash(command, session)) is None, command
    assert landing_guard.deny_reason(asked_from_bash("cci post --kind report 'merged'", session)) is None
    assert landing_guard.deny_reason(asked_from_bash("cci post --kind ask 'ASK Close these sessions?'", "another-session")) is None


def test_a_bash_report_naming_an_attr_a_separator_or_one_letter_from_one_the_drive_reads_is_refused_naming_it(demo: str, no_calls: list[list[str]]) -> None:
    session = os.environ["CLAUDE_CODE_SESSION_ID"]
    progressed = "cci post --drive demo --kind report --lane root --to posts@demo --attr progres=true 'POST j1 merged'"
    reason = landing_guard.deny_reason(asked_from_bash(progressed, session))
    assert "no `progres` attr on a report" in reason and "`--attr progress=true`" in reason
    closes = "zsh -c \"cci post --kind=REPORT --lane root --attr closes=o1 'FYI j1 merged'\""
    assert "`--attr close=" in landing_guard.deny_reason(asked_from_bash(closes, session))
    assert landing_guard.deny_reason(asked_from_bash("cci post --kind report --lane root --attr progress=true 'POST j1 merged'", session)) is None
    assert landing_guard.deny_reason(asked_from_bash(progressed, "another-session")) is None


def test_a_bash_post_report_typing_when_past_work_happened_is_refused_and_a_record_between_sessions_typing_it_passes(
    demo: str, no_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    session = os.environ["CLAUDE_CODE_SESSION_ID"]
    monkeypatch.setattr(limits, "now", lambda: WRITTEN_AT.replace(hour=22, minute=35))
    typed = (
        "cci post --kind report --lane root --to posts@demo --attr progress=true"
        " 'POST A fix job with apply authority and a diagnosis job started at 10:33 PM; next line by 10:38 PM.'"
    )
    assert landing_guard.deny_reason(asked_from_bash(typed, session)).startswith('refused: "10:33 PM" is a time typed for work already done')
    assert landing_guard.deny_reason(asked_from_bash(typed.replace("at 10:33 PM", "at {started:j1}"), session)) is None
    assert landing_guard.deny_reason(asked_from_bash(typed.replace("'POST ", "'FYI "), session)) is None
    defect = "cci post --kind defect --lane root --to meta@demo 'record refused his 3:40 PM pick, nothing posted'"
    assert landing_guard.deny_reason(asked_from_bash(defect, session)) is None


def test_a_bash_cci_post_of_another_sessions_report_to_a_communicator_lane_is_refused_for_the_posts_lane_and_a_communicators_record_goes_to_the_lanes_it_names(
    run: Run, demo: str, no_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", demo, "--role", "coordinator", "--project", "Iris")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", demo, "--role", "communicator")
    agent = {"session_id": "talker", "agent_id": "a1"}
    reported = "cci post --kind report --lane iris --to communicator@demo 'POST j1 merged'"
    assert landing_guard.deny_reason(bash(reported) | {"session_id": "iris-1"}) == (
        "This report record goes to posts@demo: another session's report for a communicator lane goes to the posts lane the drive"
        " posts from, since the communicator's input is his messages, taps and relayed words, a report on a teammate's watched"
        " thread excepted, which the communicator carries; every other record goes to the lanes it names; post it again with"
        " `--to posts@demo`, or on the cci `post` tool, whose hook addresses it"
    )
    held = "cci post --kind refused --lane communicator --to root@demo --resolves 8 --attr job=j1 'the report names no card'"
    refused = "zsh -c \"cci post --kind=refused --lane communicator --to=root@demo,owner --attr job=j1 'cc-slack refused the card'\""
    sent = (
        held,
        refused,
        "cci post --kind refused --lane communicator --to iris@demo --resolves 8 --attr job=j1 'the report names no card'",
        "cci post --kind report --lane communicator --attr close=o1 'FYI j1 merged'",
    )
    assert [landing_guard.deny_reason(bash(command) | agent) for command in sent] == [None] * 4
    unparted = landing_guard.deny_reason(bash("cci post --kind owner --lane communicator --to root@demo --attr 'about=his work' 'a new order'") | agent)
    assert unparted.startswith("refused, nothing posted: his words go out on the drive's `record` tool, kind owner")
    unsaid = landing_guard.deny_reason(bash("cci post --kind owner --lane communicator --to root@demo 'a new order'") | agent)
    assert unsaid.startswith('refused, nothing posted: an owner record names what his words are about, about "drive" for drive tooling')
    for to in ("meta@demo", "root@demo"):
        unordered = landing_guard.deny_reason(bash(f"cci post --kind owner --lane communicator --to {to} --attr about=drive 'the dashboard drops my cards'") | agent)
        assert unordered.startswith("refused, nothing posted: his words about the drive go out on the drive's `record` tool")
    assert landing_guard.deny_reason(bash(held) | {"session_id": "talker"}) == f"Refused on purpose: {render.RECORDING}"
    assert landing_guard.deny_reason(bash(held) | {"session_id": "iris-1"}) is None
