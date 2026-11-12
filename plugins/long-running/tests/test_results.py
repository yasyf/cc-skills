import io
import json
import os
from collections.abc import Callable
from pathlib import Path

import no_file_tools
import one_line_result
import pytest
import quiet_turn
from conftest import RESEARCH, CodexAsk
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    run("job", "set", "j1", "--agent", "a-7")
    run("pr", "add", "https://github.com/yasyf/cc-skills/pull/1", "--job", "j1")
    waiting = "WAITING CI on https://github.com/yasyf/cc-skills/pull/1"
    reason = one_line_result.block_reason(agent_stop(f"{waiting}\nPR: https://github.com/yasyf/cc-skills/pull/1\nCHECK: build 12", tmp_path))
    assert "lands whole in the communicator's context" in reason
    assert "keep your whole return first, if you have not, by piping it into `drive job report j1 -`" in reason
    assert one_line_result.block_reason(agent_stop(waiting, tmp_path)).startswith("Refused on purpose: j1's return is not kept in the drive")
    keep(run, monkeypatch, "j1", f"{waiting}\nPR: https://github.com/yasyf/cc-skills/pull/1\n")
    assert one_line_result.block_reason(agent_stop(waiting, tmp_path)) is None
    assert state.load(communicator)["jobs"]["j1"]["waits"] == ["yasyf/cc-skills#1"]


def test_a_reader_on_a_build_order_keeps_its_researched_report_and_one_on_an_answer_order_keeps_its_one_line_answer(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "--kind", "build", "ship the fix")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "--kind", "answer", "why is it slow")
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
    answer = "DONE it waits on the store lock"
    assert one_line_result.block_reason(agent_stop(answer, tmp_path, agent_id="a-8")).startswith("Refused on purpose: j2's return is not kept in the drive")
    keep(run, monkeypatch, "j2", answer)
    assert one_line_result.block_reason(agent_stop(answer, tmp_path, agent_id="a-8")) is None
    assert state.load(demo)["jobs"]["j2"]["state"] == "done"


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
    nothing = "NOTHING for 662342: the card is already the newest message in thread 1794387242.405329"
    assert one_line_result.block_reason(agent_stop(nothing, tmp_path, **HANDLER)) is None


def test_an_ordinary_subagent_in_the_communicators_session_is_no_handler(communicator: str, tmp_path: Path) -> None:
    assert one_line_result.block_reason(agent_stop("DONE found parser", tmp_path, agent_type="Explore")) is None
    assert one_line_result.block_reason(agent_stop("DONE found parser", tmp_path, **HANDLER)).startswith("Refused on purpose: a handler's line opens with what it did")


def test_a_handler_or_reader_ending_on_a_refused_read_is_sent_down_the_staff_route_first(
    communicator: str, run: Run, tmp_path: Path
) -> None:
    blocked = "BLOCKED access denied reading the bucket"
    reason = one_line_result.block_reason(agent_stop(blocked, tmp_path, called("Bash", command="gsutil ls gs://acme-backups"), **HANDLER))
    assert reason.startswith("Refused on purpose: your line ends on a refused read")
    assert "the `db-read` skill, or `drive break-glass`, which runs it under this Mac's machine profile with no sign-in" in reason
    assert "`UNPROVEN:`" in reason
    handled = f"POSTED {POST}; UNPROVEN: access denied reading the bucket"
    assert one_line_result.block_reason(agent_stop(handled, tmp_path, called("Skill", skill="break-glass", args="his words"), **HANDLER)) is None
    granted = called("Bash", command='drive break-glass o1 --stack storage/plat-usw2-prod --reason "list the backups" -- aws s3 ls s3://acme-backups')
    assert one_line_result.block_reason(agent_stop(handled, tmp_path, granted, **HANDLER)) is None
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "what is in the bucket")
    run("job", "add", "reader", "--order", "o1", "--goal", "read the bucket")
    run("job", "set", "j1", "--agent", "a-7")
    assert one_line_result.block_reason(agent_stop(f"DONE {blocked}", tmp_path)).startswith("Refused on purpose: your line ends on a refused read")
    assert one_line_result.block_reason(agent_stop("DONE the bucket holds 3 backups", tmp_path)).startswith("Refused on purpose: j1's return is not kept")


def test_a_reader_explaining_a_403_has_not_ended_on_a_refused_read(demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "--kind", "answer", "why the 403")
    run("job", "add", "reader", "--order", "o1", "--goal", "answer why the 403")
    run("job", "set", "j1", "--agent", "a-7")
    explained = "DONE The 403 is generated by require_team_member in api.py:42."
    keep(run, monkeypatch, "j1", explained)
    assert one_line_result.block_reason(agent_stop(explained, tmp_path, agent_type="long-running:reader")) is None
    refused = one_line_result.block_reason(agent_stop("DONE got a 403 reading the api logs", tmp_path, agent_type="long-running:reader"))
    assert refused.startswith("Refused on purpose: your line ends on a refused read")



def relay_ran(reply: Path) -> list[dict]:
    """A codex relay's codex-ask call and the output codex-ask printed into its tool result as the run started."""
    printed = f"REPLY_FILE: {reply}\nLOG_FILE: {reply.parent / 'codex-q-1.log'}\nAWAIT: codex-ask --await {reply.parent}"
    return [called("Bash", command="codex-ask - <<'QUESTION'"), {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": printed}]}}]


def codex_job(run: Run) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "review the fix")
    run("job", "add", "codex", "--order", "o1", "--goal", "review", "--agent", "a-7")


@pytest.mark.parametrize(
    ("ran", "reply", "log", "taken", "said"),
    [
        ("completed", "UNPROVEN — the column is null on 3 of 40 rows\nThe rest of the answer\n", "", "done", "UNPROVEN — the column is null on 3 of 40 rows"),
        ("completed", "\nBLOCKED codex edits, Claude ships\n", "", "failed", "BLOCKED codex edits, Claude ships"),
        ("completed", "ASK Should the fix drop the column?\n", "", "done", "ASK Should the fix drop the column?"),
        ("died", "", '{"type":"turn.started"}\n{"type":"error","message":"stream disconnected"}\n', "failed", 'BLOCKED codex-ask died: {"type":"error","message":"stream disconnected"}'),
        ("running", "", "", "running", None),
    ],
)
def test_a_codex_jobs_state_comes_from_the_codex_ask_run_its_relay_asked_never_the_relays_words(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], codex_ask: CodexAsk, ran: str, reply: str, log: str, taken: str, said: str | None
) -> None:
    codex_job(run)
    replied = codex_ask.run(ran, reply, log)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(agent_stop(f"REPLY_FILE: {replied}\nCodex answered.", tmp_path, *relay_ran(replied)))))
    one_line_result.main()
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], job["said"], state.report_text(demo, "j1").strip()) == (taken, said, reply.strip() if ran == "completed" else "")


@pytest.mark.parametrize(
    ("reply", "taken", "joins"),
    [
        ("UNPROVEN the handover delays the mint\n", "failed", None),
        ("UNPROVEN the handover delays the mint\n**FIX:** o2\n", "done", "o1"),
    ],
)
def test_a_codex_cause_reply_naming_no_fix_is_taken_blocked_and_one_naming_it_reads_that_order_live(
    demo: str, run: Run, tmp_path: Path, codex_ask: CodexAsk, reply: str, taken: str, joins: str | None
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "SandSQL mint endpoint failing", "--incident", "--parts", "fix", "cause")
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the handover", "--kind", "build")
    run("job", "set", "j2", "--agent", "a-7")
    one_line_result.take_codex(agent_stop(reply.splitlines()[0], tmp_path, *relay_ran(codex_ask.run("completed", reply))))
    data = state.load(demo)
    job = data["jobs"]["j2"]
    assert (job["state"], data["orders"]["o2"]["joins"], state.report_text(demo, "j2").strip()) == (taken, joins, reply.strip())
    if taken == "failed":
        assert job["said"].startswith(f"BLOCKED codex-ask completed, its reply refused: j2 traces the cause of o1's live bug, so {state.FIX_RULE}"[:200])


def test_a_codex_relay_reading_back_a_reply_that_quotes_another_run_keeps_its_own(demo: str, run: Run, tmp_path: Path, codex_ask: CodexAsk) -> None:
    codex_job(run)
    own = codex_ask.run("completed", "DONE reviewed: no findings\n")
    other = codex_ask.run("completed", "BLOCKED another relay's run\n")
    read_back = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": f"DONE reviewed\nIt quoted:\nREPLY_FILE: {other}\n"}]}}
    one_line_result.take_codex(agent_stop("DONE reviewed: no findings", tmp_path, *relay_ran(own), called("Bash", command=f"cat {own}"), read_back))
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("done", "DONE reviewed: no findings")


def test_a_codex_relay_whose_lane_folder_holds_a_later_run_sets_nothing(demo: str, run: Run, tmp_path: Path, codex_ask: CodexAsk) -> None:
    codex_job(run)
    own = codex_ask.run("completed", "DONE the first relay's answer\n")
    (own.parent / "meta").write_text(f"{own.parent / 'codex-r-later'}\n{own.parent / 'codex-q-later.log'}\n{{}}\n")
    one_line_result.take_codex(agent_stop("DONE the first relay's answer", tmp_path, *relay_ran(own)))
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("running", None)


def test_a_codex_relay_whose_run_codex_ask_cannot_read_is_still_held_to_one_line_and_left_to_the_watch(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], codex_ask: CodexAsk
) -> None:
    codex_job(run)
    own = codex_ask.run("completed", "DONE reviewed\n")
    (tmp_path / "codex-plugin" / "bin" / "codex-ask").write_text("#!/bin/sh\necho 'codex-ask: no run at that folder' >&2\nexit 2\n")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(agent_stop(f"REPLY_FILE: {own}\nCodex answered.", tmp_path, *relay_ran(own)))))
    one_line_result.main()
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
    assert state.load(demo)["jobs"]["j1"]["state"] == "running"


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
