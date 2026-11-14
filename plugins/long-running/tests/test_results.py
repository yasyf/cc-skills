import io
import json
import os
import shlex
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import no_file_tools
import one_line_result
import pytest
import quiet_turn
from conftest import LEAD, RESEARCH, CodexAsk, codex_reply
from drive import cli, codex, render, state, watch

Run = Callable[..., tuple[int, str]]
POST = "https://acme.slack.com/archives/D1/p1"
HANDLER = {"agent_type": "long-running:handler"}
REAL_CODEX_GAP = watch.codex_gap
TOOL_RESULT = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}
SAVED = "/Users/me/.claude/projects/p/s/tool-results/b6.txt"


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
    run("pr", "add", "https://github.com/yasyf/cc-skills/pull/1", "--job", "j1", *LEAD)
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
    run("job", "set", "j3", "--agent", "a-8")
    done = RESEARCH.splitlines()[0]
    reason = one_line_result.block_reason(agent_stop(done, tmp_path))
    assert reason.startswith("Refused on purpose: j1's return is not kept in the drive")
    assert "Pipe your whole return with its `Researched:` block" in reason
    assert "`drive job report j1 -`" in reason
    keep(run, monkeypatch, "j1", RESEARCH)
    assert one_line_result.block_reason(agent_stop(done, tmp_path)) is None
    answer = "DONE it waits on the store lock"
    assert one_line_result.block_reason(agent_stop(answer, tmp_path, agent_id="a-8")).startswith("Refused on purpose: j3's return is not kept in the drive")
    keep(run, monkeypatch, "j3", answer)
    assert one_line_result.block_reason(agent_stop(answer, tmp_path, agent_id="a-8")) is None
    assert state.load(demo)["jobs"]["j3"]["state"] == "done"


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



def codex_job(run: Run, *flags: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "review the fix", *flags)
    run("job", "add", "codex", "--order", "o1", "--goal", "review")


def launched(demo: str, run: Run, tmp_path: Path) -> Path:
    brief = tmp_path / "j1.brief.md"
    brief.write_text(f"Order (his words): review the fix\nGoal: review\nDrive: {demo}, order o1, job j1\n")
    assert run("job", "launch", "j1", str(brief))[0] == 0
    return Path(state.load(demo)["jobs"]["j1"]["codex_run"])


def watched(demo: str, job_id: str = "j1") -> dict:
    with state.edit(demo) as data:
        if isinstance(gap := REAL_CODEX_GAP(job_id, data["jobs"][job_id]), watch.Answered):
            gap.take(demo, data, job_id)
    return state.load(demo)["jobs"][job_id]


def test_drive_job_launch_starts_a_codex_jobs_codex_ask_run_on_its_brief_with_its_final_message_held_to_a_state_line_and_report(
    demo: str, run: Run, tmp_path: Path, capsys: pytest.CaptureFixture[str], codex_ask: CodexAsk
) -> None:
    codex_job(run)
    folder = launched(demo, run, tmp_path)
    scratch = state.drive_dir(demo) / "scratch" / "j1"
    assert folder == scratch / "codex"
    assert (folder / "argv").read_text().splitlines() == ["--dispatch", "-s", str(folder), "--schema", str(scratch / codex.SCHEMA_FILE), "-"]
    assert (folder / "question").read_text() == (tmp_path / "j1.brief.md").read_text()
    schema = json.loads((scratch / codex.SCHEMA_FILE).read_text())
    assert (schema["required"], schema["properties"]["state"]["enum"]) == (["state", "line", "report"], [*state.RESULT_STATES, "BUILT"])
    assert (state.load(demo)["jobs"]["j1"]["state"], watched(demo)["state"]) == ("running", "running")
    assert cli.main(["job", "launch", "j1", str(tmp_path / "j1.brief.md")]) == 2
    assert f"j1: refused, not launched: codex-ask: lane {folder} is busy" in capsys.readouterr().err


def test_a_codex_jobs_launch_carries_each_text_it_holds_and_each_answer_it_has_not_heard_under_his_answers_since_and_drops_them_off_the_job(
    demo: str, run: Run, tmp_path: Path, codex_ask: CodexAsk
) -> None:
    codex_job(run)
    ring, knock, lazy = 'His answer on card demo:j1-7, verbatim: "Ring"', 'His answer on card demo:j1-8, verbatim: "Knock"', "the lazy copy, no sweeper"
    with state.edit(demo) as data:
        data["jobs"]["j1"]["undelivered"] = [
            {"seq": "9", "words": ring, "at": state.stamp(), "said": None, "request": "req-ring"},
            {"seq": "10", "words": knock, "at": state.stamp(), "said": None, "request": "req-knock"},
        ]
        data["answers"]["a1"] = {"words": lazy, "link": POST, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {"j1": None}}
    brief = tmp_path / "j1.brief.md"
    brief.write_text(f"Order (his words): review the fix\nGoal: review\nDecisions that bind: {knock}\nDrive: {demo}, order o1, job j1\n")
    assert run("job", "launch", "j1", str(brief))[0] == 0
    data = state.load(demo)
    question = (Path(data["jobs"]["j1"]["codex_run"]) / "question").read_text()
    assert question == f'{brief.read_text()}His answers since:\n- {ring}\n- "{lazy}" ({POST})\n'
    assert (Path(data["jobs"]["j1"]["brief"]).read_text(), data["jobs"]["j1"]["undelivered"], data["answers"]["a1"]["jobs"]["j1"]) == (question, [], {"mark": "sent"})


def test_a_text_resumed_to_a_codex_job_waits_for_its_next_run_while_one_goes_and_rides_the_run_its_resume_starts_once_it_returned_as_j4303s_answer_did_not(
    demo: str, run: Run, tmp_path: Path, codex_ask: CodexAsk
) -> None:
    codex_job(run)
    folder = launched(demo, run, tmp_path)
    answer = "build option 2, the lazy copy"
    assert run("job", "resume", "j1", answer) == (
        0,
        "j1: held for its next codex-ask run under His answers since:, since the run that goes reads no text; the watch starts that run with it once this one returns with the job still open",
    )
    assert ([held["words"] for held in state.load(demo)["jobs"]["j1"]["undelivered"]], "His answers since:" in (folder / "question").read_text()) == ([answer], False)
    codex_ask.end(folder, "completed", codex_reply("ASK", "Should the fix drop the column?"))
    assert watched(demo)["state"] == "waiting"
    assert run("job", "resume", "j1") == (0, f"j1: the message went to its next codex-ask run, started in {folder}, under His answers since:")
    assert ((folder / "question").read_text().endswith(f"His answers since:\n- {answer}\n"), state.load(demo)["jobs"]["j1"]["undelivered"]) == (True, [])


def test_a_codex_jobs_run_carries_its_job_and_codex_runs_landing_guard_on_its_bash_calls_trusted_in_its_config(demo: str, run: Run, tmp_path: Path, codex_ask: CodexAsk) -> None:
    home = tmp_path / "codex-home"
    hooks, config = home / "hooks.json", home / "config.toml"
    theirs = {"matcher": "^Bash$", "hooks": [{"type": "command", "command": "lr codex-guard check", "timeout": 10}]}
    older = {"matcher": "^Bash$", "hooks": [{"type": "command", "command": "exec python3 -I /old/long-running/hooks/landing_guard.py", "timeout": 60}]}
    hooks.write_text(json.dumps({"hooks": {"PreToolUse": [theirs, older]}}))
    config.write_text(f'model = "gpt-6.1-sol"\n\n[hooks.state."{hooks}:pre_tool_use:1:0"]\ntrusted_hash = "sha256:{"0" * 64}"\n')
    config.chmod(0o600)
    codex_job(run)
    folder = launched(demo, run, tmp_path)
    assert (folder / "env").read_text() == f"j1 {demo}\n"
    groups = json.loads(hooks.read_text())["hooks"]["PreToolUse"]
    guard = f'[ -z "$LONG_RUNNING_OWN_JOB" ] || exec {shlex.quote(sys.executable)} -I {shlex.quote(str(codex.GUARD))}'
    assert groups == [theirs, {"matcher": "^Bash$", "hooks": [{"type": "command", "command": guard, "timeout": codex.GUARD_SECONDS}]}]
    written = tomllib.loads(config.read_text())
    trusted = written["hooks"]["state"][f"{hooks}:pre_tool_use:1:0"]["trusted_hash"]
    assert (written["model"], len(written["hooks"]["state"]), trusted.startswith("sha256:"), len(trusted), trusted != f"sha256:{'0' * 64}") == ("gpt-6.1-sol", 1, True, 71, True)
    assert config.stat().st_mode & 0o777 == 0o600
    kept = (hooks.read_text(), config.read_text())
    codex.guarded()
    assert (hooks.read_text(), config.read_text()) == kept


def test_a_codex_job_runs_the_model_its_order_names_and_astra_only_on_an_order_whose_words_name_it(
    demo: str, run: Run, tmp_path: Path, capsys: pytest.CaptureFixture[str], codex_ask: CodexAsk
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "design the handoff from scratch with Fable and Astra")
    run("order", "add", "--title", "Other title", "--project", "The drive", "--workstream", "Drive tooling", "review the fix")
    assert run("job", "add", "codex", "--order", "o1", "--goal", "design", "--model", "astra")[0] == 0
    brief = tmp_path / "j1.brief.md"
    brief.write_text(f"Order (his words): design the handoff from scratch with Fable and Astra\nGoal: design\nDrive: {demo}, order o1, job j1\n")
    assert run("job", "launch", "j1", str(brief))[0] == 0
    folder = Path(state.load(demo)["jobs"]["j1"]["codex_run"])
    assert (folder / "argv").read_text().splitlines() == ["--dispatch", "-s", str(folder), "--schema", str(folder.parent / codex.SCHEMA_FILE), "-m", "astra", "-"]
    assert cli.main(["job", "add", "codex", "--order", "o2", "--goal", "review", "--model", "astra"]) == 2
    assert "refused, no job added: --model astra runs only on an order whose words name astra, which o2's do not" in capsys.readouterr().err
    assert cli.main(["job", "add", "reader", "--order", "o2", "--goal", "read", "--model", "sol"]) == 2
    assert "--model names the codex-ask model a codex job runs" in capsys.readouterr().err
    assert len(state.load(demo)["jobs"]) == 1


def test_an_incidents_codex_job_launches_on_codex_asks_fast_tier_once_its_brief_carries_the_incident_rule(
    demo: str, run: Run, tmp_path: Path, capsys: pytest.CaptureFixture[str], codex_ask: CodexAsk
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "runs never start", "--incident", "--parts", "fix", "cause")
    brief = tmp_path / "j1.brief.md"
    brief.write_text("Order: runs never start\nIncident: record 674295\n4. Build the fix and open its PR, then return READY <pr url>\nDrive: demo, order o1, job j1\n")
    assert cli.main(["job", "launch", "j1", str(brief)]) == 2
    assert "j1: refused, not launched: j1 works on o1, an incident, which a choice never stops" in capsys.readouterr().err
    assert "codex_run" not in state.load(demo)["jobs"]["j1"]
    brief.write_text(brief.read_text().replace("record 674295", f"record 674295; {state.INCIDENT_CHOICE['codex']}"))
    assert run("job", "launch", "j1", str(brief))[0] == 0
    assert "--incident" in (Path(state.load(demo)["jobs"]["j1"]["codex_run"]) / "argv").read_text().splitlines()


@pytest.mark.parametrize(
    ("ran", "reply", "log", "taken", "said"),
    [
        (
            "completed",
            codex_reply("HANDOFF", "the keyed SandSQL page builds in ~/wt/keyed-pages on branch keyed-pages, uncommitted: a builder ships it", "SLUG: sandsql\nCHANGE: dashboard/src/iris/pages keyed by slug"),
            "",
            "stopped",
            "HANDOFF the keyed SandSQL page builds in ~/wt/keyed-pages on branch keyed-pages, uncommitted: a builder ships it",
        ),
        (
            "completed",
            codex_reply("DONE", "the SoFi LLM gateway rolled back to 0.41.2", "CHECK: /healthz answers 200 on 0.41.2"),
            "",
            "done",
            "DONE the SoFi LLM gateway rolled back to 0.41.2",
        ),
        (
            "completed",
            "REPLY_FILE: /scratch/j3010/keyed-pages/reply.txt\nSLUG: sandsql\n",
            "",
            "failed",
            "BLOCKED codex-ask completed, its reply refused: it is not the JSON its schema requires, a state, line and report",
        ),
        ("completed", codex_reply("BLOCKED", "codex edits, Claude ships"), "", "failed", "BLOCKED codex edits, Claude ships"),
        ("completed", codex_reply("ASK", "Should the fix drop the column?"), "", "waiting", None),
        ("died", "", '{"type":"turn.started"}\n{"type":"error","message":"stream disconnected"}\n', "failed", 'BLOCKED codex-ask died: {"type":"error","message":"stream disconnected"}'),
        ("running", "", "", "running", None),
    ],
)
def test_a_codex_jobs_state_comes_from_the_state_field_of_the_run_its_launch_started_and_its_return_lands_on_its_own_job(
    demo: str, run: Run, tmp_path: Path, codex_ask: CodexAsk, ran: str, reply: str, log: str, taken: str, said: str | None
) -> None:
    codex_job(run)
    codex_ask.end(launched(demo, run, tmp_path), ran, reply, log)
    job = watched(demo)
    kept = state.report_text(demo, "j1").strip()
    assert (job["state"], job["said"]) == (taken, said)
    if ran != "completed":
        assert kept == ""
    elif reply.startswith("{"):
        answer = json.loads(reply)
        assert kept == f"{answer['state']} {answer['line']}\n{answer['report']}".strip()
    else:
        assert kept == reply.strip()


@pytest.mark.parametrize(
    ("report", "taken", "joins"),
    [
        ("", "failed", None),
        ("**FIX:** o2", "done", "o1"),
    ],
)
def test_a_codex_cause_reply_naming_no_fix_is_taken_blocked_and_one_naming_it_reads_that_order_live(
    demo: str, run: Run, codex_ask: CodexAsk, report: str, taken: str, joins: str | None
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "SandSQL mint endpoint failing", "--incident", "--parts", "fix", "cause")
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the handover", "--kind", "build")
    with state.edit(demo) as data:
        data["jobs"]["j2"]["codex_run"] = str(codex_ask.run("completed", codex_reply("DONE", "the handover delays the mint", report)))
    job = watched(demo, "j2")
    assert (job["state"], state.load(demo)["orders"]["o2"]["joins"], state.report_text(demo, "j2").strip()) == (taken, joins, f"DONE the handover delays the mint\n{report}".strip())
    if taken == "failed":
        assert job["said"].startswith(f"BLOCKED codex-ask completed, its reply refused: j2 traces the cause of o1's live bug, so {state.FIX_RULE}"[:200])


def test_a_reply_file_pointer_reported_on_a_codex_job_sets_no_state(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    codex_job(run)
    monkeypatch.setattr("sys.stdin", io.StringIO("REPLY_FILE: /runs/codex-ask.1/codex-r-1\nCodex answered.\n"))
    assert cli.main(["job", "report", "j1", "-"]) == 2
    assert "j1: refused, nothing kept: " in capsys.readouterr().err
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("running", None)


@pytest.mark.parametrize(("pictures", "taken"), [(1, "failed"), (2, "done")])
def test_a_codex_done_on_a_surface_its_brief_names_is_taken_only_with_a_picture_of_each_user_story(
    demo: str, run: Run, tmp_path: Path, codex_ask: CodexAsk, pictures: int, taken: str
) -> None:
    codex_job(run)
    proof = tmp_path / "proof.sh"
    proof.write_text("#!/bin/sh\n")
    shots = [tmp_path / f"story{number}.png" for number in range(1, pictures + 1)]
    for shot in shots:
        shot.write_bytes(b"\0")
    reply = codex_reply("DONE", "the queue redrawn", f"CHECK: {' '.join(map(str, shots))}")
    with state.edit(demo) as data:
        data["jobs"]["j1"] |= {
            "brief_flags": {"native": "the dashboard's queue", "interactions": "press j and the next card opens; hover a card and its title shows"},
            "repro": str(proof),
            "codex_run": str(codex_ask.run("completed", reply)),
        }
    job = watched(demo)
    assert job["state"] == taken
    if taken == "failed":
        assert job["said"].startswith("BLOCKED codex-ask completed, its reply refused: its brief names 2 user stories and its proof shows 1 of them")


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


@pytest.mark.parametrize(
    ("command", "refused"),
    [
        (f"{{ printf x; cat {SAVED}; }} > /s/x.md", False),
        (f'{{ echo "## 4 (times UTC)"; cat {SAVED}; echo; cat $d/4.txt; }} > $d/evidence.md; wc -c $d/evidence.md', False),
        ("( cat /tmp/c/tasks/b1.output; echo ) >> /s/x.md", False),
        (f"{{\n  echo head\n  cat {SAVED}\n}} > /s/x.md", False),
        (f"{{ cat {SAVED}; echo ${{x}}; }} > /s/x.md", False),
        (f'( cat {SAVED}; echo "$(date)" ) > /s/x.md', False),
        (f"{{ cat {SAVED}; echo x }} > /s/x.md", False),
        (f"cat > /s/run.sh <<'EOF'\ncat {SAVED}\nEOF", False),
        (f"cat {SAVED} > /s/x.md", False),
        (f"{{ cat {SAVED}; echo; }}", True),
        (f"( cat {SAVED}; echo )", True),
        (f"cat {SAVED}; {{ echo; }} > /s/x.md", True),
        (f"cat {SAVED}; echo \\}} > /s/x.md", True),
        (f"{{ cat {SAVED}; }} > /s/x.md; cat {SAVED}", True),
        (f"{{ cat {SAVED}; }} >&2", True),
        (f"{{ cat {SAVED}; }} 2>/dev/null", True),
        (f"{{ cat {SAVED}; }} > /dev/stdout", True),
        (f"bash <<'EOF'\ncat {SAVED}\nEOF", True),
        (f'echo "$(cat {SAVED})"', True),
    ],
)
def test_a_drive_role_cats_a_whole_saved_output_only_where_its_stdout_reaches_no_session(demo: str, command: str, refused: bool) -> None:
    assert (no_file_tools.deny_reason(bash_call(command)) is not None) is refused


def test_the_coordinators_compaction_summary_names_the_manual_row_of_its_step(demo: str) -> None:
    text = render.compact_instructions(state.load(demo), "coordinator")
    assert "The step you were in the middle of, in one line, with the `drive manual <page> <row>` that states it." in text
