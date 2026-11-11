import io
import json
import os
from collections.abc import Callable
from pathlib import Path

import no_file_tools
import one_line_result
import pytest
import quiet_turn
from conftest import RESEARCH
from drive import render, state

Run = Callable[..., tuple[int, str]]
POST = "https://acme.slack.com/archives/D1/p1"
HANDLER = {"agent_type": "long-running:handler"}
TOOL_RESULT = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}


def asked(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def said(text: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def called(name: str, **tool_input: object) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": name, "input": tool_input}]}}


def transcript(path: Path, *entries: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    return str(path)


def agent_stop(message: str, tmp_path: Path, *steps: dict, **fields: object) -> dict:
    transcript(tmp_path / "session" / "subagents" / "agent-a-7.jsonl", asked("Goal: do it"), *steps)
    return {
        "session_id": os.environ["CLAUDE_CODE_SESSION_ID"],
        "hook_event_name": "SubagentStop",
        "transcript_path": str(tmp_path / "session.jsonl"),
        "agent_id": "a-7",
        "stop_hook_active": False,
        "last_assistant_message": message,
        **fields,
    }


def keep(run: Run, monkeypatch: pytest.MonkeyPatch, job_id: str, report: str) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(report))
    run("job", "report", job_id, "-")


def test_a_builder_the_communicator_started_is_told_to_keep_its_return_before_it_ends(
    communicator: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    run("job", "set", "j1", "--agent", "a-7")
    waiting = "WAITING CI on https://github.com/yasyf/cc-skills/pull/1"
    reason = one_line_result.block_reason(agent_stop(f"{waiting}\nPR: https://github.com/yasyf/cc-skills/pull/1\nCHECK: build 12", tmp_path))
    assert "lands whole in the communicator's context" in reason
    assert "keep your whole return first, if you have not, by piping it into `drive job report j1 -`" in reason
    assert one_line_result.block_reason(agent_stop(waiting, tmp_path)).startswith("Refused on purpose: j1's return is not kept in the drive")
    keep(run, monkeypatch, "j1", f"{waiting}\nPR: https://github.com/yasyf/cc-skills/pull/1\n")
    assert one_line_result.block_reason(agent_stop(waiting, tmp_path)) is None


def test_a_reader_on_a_build_order_keeps_its_researched_report_and_one_on_an_answer_order_ends_on_its_answer(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "--kind", "build", "ship the fix")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "--kind", "answer", "why is it slow")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: what the fix needs")
    run("job", "add", "reader", "--order", "o2", "--goal", "answer why it is slow")
    run("job", "set", "j1", "--agent", "a-7")
    run("job", "set", "j2", "--agent", "a-8")
    done = RESEARCH.splitlines()[0]
    reason = one_line_result.block_reason(agent_stop(done, tmp_path))
    assert reason.startswith("Refused on purpose: j1's return is not kept in the drive")
    assert "Pipe your whole return with its `Researched:` block" in reason
    assert "`drive job report j1 -`" in reason
    keep(run, monkeypatch, "j1", RESEARCH)
    assert one_line_result.block_reason(agent_stop(done, tmp_path)) is None
    assert one_line_result.block_reason(agent_stop("DONE it waits on the store lock", tmp_path, agent_id="a-8")) is None


def test_a_handlers_line_carries_at_most_three_facts_past_its_results(communicator: str, tmp_path: Path) -> None:
    reason = one_line_result.block_reason(agent_stop(f"POSTED {POST}; waits w12; record 4410; job j7; /x/a.md", tmp_path, **HANDLER))
    assert reason.startswith("Refused on purpose: your line carries 4 facts past its results, and a handler's carries at most 3")
    assert "Cut it to the 3" in reason
    assert one_line_result.block_reason(agent_stop(f"POSTED {POST}; POSTED {POST}2; waits w12; record 4410; job j7", tmp_path, **HANDLER)) is None


def test_a_handlers_line_opens_with_what_it_did_never_a_message_to_him(communicator: str, tmp_path: Path) -> None:
    reason = one_line_result.block_reason(agent_stop("I filed your answer and your ruling; 662062; 662063", tmp_path, **HANDLER))
    assert reason.startswith("Refused on purpose: a handler's line opens with what it did, `POSTED <permalink>` for each post, `RECORDED <seq>`")
    assert one_line_result.block_reason(agent_stop("RECORDED 662062; RECORDED 662063; card j810-661541 closed with his words", tmp_path, **HANDLER)) is None
    assert one_line_result.block_reason(agent_stop("DEFECT say: refused; a ; in its refusal", tmp_path, **HANDLER)) is None


def test_an_ordinary_subagent_in_the_communicators_session_is_no_handler(communicator: str, tmp_path: Path) -> None:
    assert one_line_result.block_reason(agent_stop("DONE found parser", tmp_path, agent_type="Explore")) is None
    assert one_line_result.block_reason(agent_stop("DONE found parser", tmp_path, **HANDLER)).startswith("Refused on purpose: a handler's line opens with what it did")


def test_a_handler_or_reader_ending_on_a_refused_read_is_sent_down_the_staff_route_first(
    communicator: str, run: Run, tmp_path: Path
) -> None:
    blocked = "BLOCKED access denied reading the bucket"
    reason = one_line_result.block_reason(agent_stop(blocked, tmp_path, called("Bash", command="gsutil ls gs://acme-backups"), **HANDLER))
    assert reason.startswith("Refused on purpose: your line ends on a refused read")
    assert "the `db-read` skill, or the `break-glass` skill with his words as its reason" in reason
    assert "`UNPROVEN:`" in reason
    handled = f"POSTED {POST}; UNPROVEN: access denied reading the bucket"
    assert one_line_result.block_reason(agent_stop(handled, tmp_path, called("Skill", skill="break-glass", args="his words"), **HANDLER)) is None
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "what is in the bucket")
    run("job", "add", "reader", "--order", "o1", "--goal", "read the bucket")
    run("job", "set", "j1", "--agent", "a-7")
    assert one_line_result.block_reason(agent_stop(f"DONE {blocked}", tmp_path)).startswith("Refused on purpose: your line ends on a refused read")
    assert one_line_result.block_reason(agent_stop("DONE the bucket holds 3 backups", tmp_path)) is None


def test_a_reader_explaining_a_403_has_not_ended_on_a_refused_read(demo: str, run: Run, tmp_path: Path) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "--kind", "answer", "why the 403")
    run("job", "add", "reader", "--order", "o1", "--goal", "answer why the 403")
    run("job", "set", "j1", "--agent", "a-7")
    explained = "DONE The 403 is generated by require_team_member in api.py:42."
    assert one_line_result.block_reason(agent_stop(explained, tmp_path, agent_type="long-running:reader")) is None
    refused = one_line_result.block_reason(agent_stop("DONE got a 403 reading the api logs", tmp_path, agent_type="long-running:reader"))
    assert refused.startswith("Refused on purpose: your line ends on a refused read")


def test_a_communicator_turn_writes_no_text_between_its_tool_calls(communicator: str, tmp_path: Path) -> None:
    stop = {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": "Sent."}
    chatty = transcript(tmp_path / "chatty.jsonl", asked("his message"), said("Let me check the thread."), called("Agent", prompt="x"), TOOL_RESULT, said("Sent."))
    reason = quiet_turn.block_reason(stop | {"transcript_path": chatty})
    assert reason.startswith('Refused on purpose: this turn wrote 1 message of text between its tool calls in this terminal, which he never reads, the first: "Let me check the thread."')
    assert "End this turn with `Sent.` alone." in reason
    quiet = transcript(
        tmp_path / "quiet.jsonl", asked("his earlier message"), said("Old narration."), asked("his message"), called("Agent", prompt="x"), TOOL_RESULT, said("Sent.")
    )
    assert quiet_turn.block_reason(stop | {"transcript_path": quiet}) is None


def bash_call(command: str, **fields: object) -> dict:
    return {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "tool_name": "Bash", "tool_input": {"command": command}, **fields}


def test_a_drive_role_reaches_no_memory_file_or_whole_saved_output_through_bash(demo: str) -> None:
    memory = no_file_tools.deny_reason(bash_call("cat >> ~/.claude/projects/x/memory/a.md <<'EOF'\nlesson\nEOF"))
    assert memory.startswith("refused: the coordinator reads and writes no memory file, with Bash or any tool")
    assert "a cc-notes note (`ccn note add`)" in memory
    whole = no_file_tools.deny_reason(bash_call("cat /private/tmp/claude-501/x/tasks/b1.output"))
    assert whole.startswith("refused: the coordinator prints no whole saved tool result or background output")
    assert "`tail -c 2000 <file>`" in whole
    assert no_file_tools.deny_reason(bash_call("tail -c 2000 /private/tmp/claude-501/x/tasks/b1.output")) is None
    assert no_file_tools.deny_reason(bash_call("drive status o288")) is None
    assert no_file_tools.deny_reason(bash_call("cat >> ~/.claude/projects/x/memory/a.md", agent_id="a-1")) is None


def test_the_coordinators_compaction_summary_names_the_manual_row_of_its_step(demo: str) -> None:
    text = render.compact_instructions(state.load(demo), "coordinator")
    assert "The step you were in the middle of, in one line, with the `drive manual <page> <row>` that states it." in text
