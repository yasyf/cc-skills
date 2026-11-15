import io
import json
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import agent_brief
import handed_records
import one_line_result
import pytest
from conftest import LANE_SPEC, UNFROZEN, Thread
from drive import agents, orca, reads, slack, state, talk, tools, watch

Run = Callable[..., tuple[int, str]]
HIS_BRIEF = "Message (his words): why is this card asking again?\nThread: D123 1794380463.481959\nDrive: demo\nScratch: {scratch}\nDeliverable: his answer, in his thread"
LINE = "POSTED https://acme.slack.com/archives/D123/p1794380463481960"
BACKGROUND_JOB = "b7f2e0aa"
GONE = agents.gone
LAUNCHED = agents.launched
ALERT_THREAD = "1794625385.692939"


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    argvs: list[list[str]] = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: argvs.append(argv) or "")
    return argvs


@pytest.fixture(autouse=True)
def slack_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: calls.append(argv) or {})
    return calls


def start(run: Run, tmp_path: Path) -> str:
    code, out = run("handler", HIS_BRIEF.format(scratch=tmp_path / "scratch" / "h1"))
    assert code == 0
    return out.removeprefix("started ")


def bind(monkeypatch: pytest.MonkeyPatch, handler: str, session: str) -> None:
    monkeypatch.setenv(state.HANDLER_ENV, f"demo/{handler}")
    with state.edit("demo") as data:
        data["handlers"][handler]["session"] = session


def stop(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    session: str,
    transcript: Path,
    line: str,
    brief: str = HIS_BRIEF,
    event: str = "Stop",
    rows: tuple[dict, ...] = (),
    **fields: object,
) -> str:
    transcript.write_text("".join(json.dumps(row) + "\n" for row in [{"type": "user", "timestamp": EARLIER, "message": {"role": "user", "content": brief}}, *rows]))
    payload = {
        "session_id": session,
        "agent_type": agents.HANDLER,
        "hook_event_name": event,
        "transcript_path": str(transcript),
        "cwd": str(transcript.parent),
        "stop_hook_active": False,
        "last_assistant_message": line,
    } | fields
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    one_line_result.main()
    return capsys.readouterr().out


def test_drive_handler_starts_its_own_claude_bg_session_and_records_that_he_wrote(
    communicator: str, run: Run, tmp_path: Path, started: list[list[str]], launched_sessions: list[str]
) -> None:
    handler = start(run, tmp_path)
    data = state.load("demo")
    assert data["handlers"][handler]["session"] == launched_sessions[0]
    assert data["owner"]["heard"] is not None
    assert (tmp_path / "scratch" / "h1").is_dir()
    [argv] = started
    assert argv[:4] == ["claude", "--bg", "--agent", agents.HANDLER]
    env = json.loads(argv[argv.index("--settings") + 1])["env"]
    assert (env[state.HANDLER_ENV], env["CCI_PARENT_SESSION"], env["CLAUDE_LONG_RUNNING_DRIVE"]) == (f"demo/{handler}", os.environ["CLAUDE_CODE_SESSION_ID"], "demo")
    assert argv[-1] == HIS_BRIEF.format(scratch=tmp_path / "scratch" / "h1") + UNFROZEN
    assert tools.run("handler", "demo", {"brief": f"Record: 663488 Drive: demo Scratch: {tmp_path / 'h2'}"}).startswith("started ")
    assert state.load("demo")["owner"] == data["owner"]


def test_a_handler_session_starts_with_every_orca_lanes_env_settings_and_denied_tools_and_this_macs_lane_settings_over_them_but_the_datadog_plugin(
    communicator: str, run: Run, tmp_path: Path, started: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    plugins = {"cc-review@cc-review": False, agents.DATADOG_PLUGIN: False}
    monkeypatch.setenv("CLAUDE_LANE_SETTINGS", json.dumps({"enabledPlugins": plugins, "verbose": True}))
    start(run, tmp_path)
    [argv] = started
    settings = json.loads(argv[argv.index("--settings") + 1])
    lane = json.loads(LANE_SPEC.read_text())
    assert {key: settings["env"][key] for key in lane["env"]} == lane["env"]
    assert (settings["enabledPlugins"], settings["verbose"], settings["statusLine"], settings["bashOutputMaxChars"]) == (plugins | {agents.DATADOG_PLUGIN: True}, True, lane["settings"]["statusLine"], 6000)
    assert settings["permissions"]["deny"] == lane["deniedTools"]


def test_drive_handler_refuses_a_brief_line_its_definition_already_carries(communicator: str, run: Run, tmp_path: Path, started: list[list[str]]) -> None:
    retyped = HIS_BRIEF.format(scratch="/x") + "\nDo not answer the card; he picked nothing."
    with pytest.raises(state.DriveError, match="`Do not answer the card; he picked nothing.` is more") as refused:
        tools.run("handler", "demo", {"brief": retyped})
    assert str(refused.value).endswith("drop the line")
    context = "\nContext: he replies to the card drawn at 11:21 PM\nits question: the bucket copy\nits options: skip or wait"
    assert agents.beyond_brief(HIS_BRIEF.replace("again?", "again?\nand again?") + context) is None
    assert agents.beyond_brief(f"{HIS_BRIEF}{context}\nand the drive's own rules") == "and the drive's own rules"
    assert started == []
    assert state.load("demo")["handlers"] == {}


def test_a_handler_claude_bg_could_not_start_leaves_nothing_behind_and_tells_the_communicator(communicator: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[list[str]] = []

    def refuse(argv: list[str], checkout: Path, env: dict | None = None) -> str:
        if argv[:2] == ["claude", "--bg"]:
            raise reads.ReadError("claude --bg: workspace not trusted")
        posted.append(argv)
        return ""

    monkeypatch.setattr(reads, "run", refuse)
    code, out = run("handler", f"Record: 663488 Drive: demo Scratch: {tmp_path / 'h1'}")
    handler = out.removeprefix("started ")
    assert (code, state.load("demo")["handlers"]) == (0, {})
    [post] = posted
    assert post[:2] == ["cci", "post"] and post[post.index("--to") + 1] == "communicator@demo"
    assert post[-1].startswith(f"BLOCKED handler {handler} did not start: claude --bg: workspace not trusted")
    code, out = run("handler", QUESTION_BRIEF.format(scratch=tmp_path / "h2"), "--answer", "Copy fix shipped")
    assert code == 0 and posted[-1][-1].startswith(f"BLOCKED handler {out.removeprefix('started ')} did not start: claude --bg: workspace not trusted")
    assert run("handler", HIS_BRIEF.format(scratch=tmp_path / "h4"), "--answer", "Copy fix shipped")[0] == 2
    data = state.load("demo")
    assert (data["handlers"], data["orders"], data["jobs"]) == ({}, {}, {})


def test_a_handler_is_keyed_at_launch_by_the_session_of_the_job_claude_bg_prints_and_a_launch_naming_no_job_claude_code_keeps_leaves_nothing_behind(
    communicator: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "claude"
    (config / "jobs" / "a81dc737").mkdir(parents=True)
    (config / "jobs" / "a81dc737" / "state.json").write_text(json.dumps({"sessionId": "a81dc737-4f1e-4883-b14e-3c520f1f2737", "state": "working", "tempo": "active"}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setattr(agents, "launched", LAUNCHED)
    printed = iter(["backgrounded · a81dc737 · demo handler 1a2b3c4d\n  claude agents             list sessions\n", "backgrounded · 0badf00d · demo handler 2b3c4d5e\n"])
    posted: list[list[str]] = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: next(printed) if argv[:2] == ["claude", "--bg"] else posted.append(argv) or "")
    handler = start(run, tmp_path)
    assert state.load("demo")["handlers"][handler]["session"] == "a81dc737-4f1e-4883-b14e-3c520f1f2737"
    assert state.session_drives("a81dc737-4f1e-4883-b14e-3c520f1f2737") == ["demo"]
    code, out = run("handler", f"Record: 663488 Drive: demo Scratch: {tmp_path / 'h2'}")
    assert (code, list(state.load("demo")["handlers"])) == (0, [handler])
    assert posted[-1][-1].startswith(f"BLOCKED handler {out.removeprefix('started ')} did not start: claude --bg named no background job Claude Code keeps: backgrounded · 0badf00d")


THREAD = "1794200000.000100"
QUESTION_LINK = f"https://acme.slack.com/archives/D123/p1794200100000100?thread_ts={THREAD}&cid=D123"
QUESTION_BRIEF = f"Message (his words): did the copy fix ship?\nLink: {QUESTION_LINK}\nThread: D123 {THREAD}\nDrive: demo\nScratch: {{scratch}}"


def test_his_read_only_questions_handler_starts_on_its_answer_order_and_one_say_answers_and_closes_it(
    communicator: str, run: Run, tmp_path: Path, started: list[list[str]], slack_thread: Thread, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Copy fix", "--project", "The drive", "--workstream", "Copies", "fix the copy", "--from", f"https://acme.slack.com/archives/D123/p{THREAD.replace('.', '')}", "--kind", "answer")
    run("job", "add", "reader", "--order", "o1", "--goal", "trace the copy")
    run("order", "add", "--title", "Run page", "--project", "The drive", "--workstream", "Runs", "show a run's history", "--from", "https://acme.slack.com/archives/D123/p1794100000000100")
    brief = QUESTION_BRIEF.format(scratch=tmp_path / "h1")
    handler = tools.run("handler", "demo", {"brief": brief, "answer": "Copy fix shipped"}).removeprefix("started ")
    data = state.load("demo")
    order, job = data["orders"]["o3"], data["jobs"]["j2"]
    assert (order["text"], order["kind"], order["owner"], order["thread"], order["title"]) == ("did the copy fix ship?", "answer", "communicator", ["D123", THREAD], "Copy fix shipped")
    assert (order["project"], order["workstream"]) == (None, None)
    assert (job["kind"], job["order"], job["agent"], job["state"]) == ("reader", "o3", handler, "running")
    [argv] = started
    prompt = argv[-1]
    assert prompt.startswith(f"{brief}\nOrder: o3, job j2\nPairs in use: The drive / Copies; The drive / Runs\nIn his thread:\n- o1 ")
    assert "  - j1 " in prompt and "o2" not in prompt and agents.beyond_brief(prompt) is None

    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session")
    answer = slack_thread.post(state.now(), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": answer, "thread_ts": THREAD})
    report = {"seq": 700001, "kind": "report", "lane": "communicator", "at": state.stamp(), "fields": {"attrs": {"close": "o3"}}}
    replies = {"post": json.dumps({"seq": 700001}), "show": json.dumps(report)}
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: started.append(argv) or replies.get(argv[1], ""))
    monkeypatch.setattr(agents, "background_job", lambda session: (BACKGROUND_JOB, {"sessionId": session}))
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: None)
    text = "It shipped in 1.0.540."
    with monkeypatch.context() as scoped:
        scoped.setenv("CLAUDE_CODE_SESSION_ID", "h-session")
        with pytest.raises(state.DriveError, match="^nothing posted: o3 is filed under no project and workstream yet: run drive order set o3 --project"):
            talk.say("demo", {"text": text, "thread_ts": THREAD, "close": True})
        assert run("order", "set", "o3", "--project", "The drive", "--workstream", "Copies")[0] == 0
        line = talk.say("demo", {"text": text, "thread_ts": THREAD, "close": True})
    result = f"POSTED {answer}"
    assert line == f'{result}; o3 "Copy fix shipped": posted {answer}; o3 closed on report 700001; j2 keeps {result} as its return, the line you end on'
    assert ["cci", "post", "--drive", "demo", "--lane", "communicator", "--kind", "report", "--attr", "close=o3", "--json", "--text", text] in started
    data = state.load("demo")
    assert (data["orders"]["o3"]["state"], data["jobs"]["j2"]["state"], data["jobs"]["j2"]["said"]) == ("done", "done", result)
    assert stop(monkeypatch, capsys, "h-session", transcript, result) == ""
    assert not [argv for argv in started if argv[:2] == ["drive", "status"] or argv[0] in ("orca", "cc-slack")]


def test_a_handlers_own_session_reads_as_an_agent_of_the_communicators(
    communicator: str, run: Run, tmp_path: Path, started: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = start(run, tmp_path)
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session")
    data = state.load("demo")
    holds = {"message": None, "link": None, "thread": "1794380463.481959", "record": None, "re": []}
    assert data["handlers"][handler] | {"started": None} == {
        "session": "h-session",
        "started": None,
        "brief": HIS_BRIEF.format(scratch=tmp_path / "scratch" / "h1"),
        "holds": holds,
        "handed": [],
        "took": [],
        "posted": [],
    }
    assert state.session_drives("h-session") == ["demo"]
    assert state.session_role(data, "h-session") == "communicator"
    step = {"session_id": "h-session", "agent_type": agents.HANDLER, "transcript_path": str(transcript), "tool_name": "Bash"}
    assert agents.seen(step) == step | {
        "session_id": os.environ["CLAUDE_CODE_SESSION_ID"],
        "agent_id": handler,
        "agent_transcript_path": str(transcript),
        "handler_session": "h-session",
    }
    assert agents.seen(step | {"session_id": "someone-else"})["session_id"] == "someone-else"
    assert agents.transcript(agents.seen(step)) == transcript
    assert agent_brief.deny_reason(agents.seen(step | {"tool_input": {"subagent_type": agents.HANDLER, "prompt": "Record: 1"}})) is None
    kept = tmp_path / "claude" / "projects" / "-src-demo" / "h-session.jsonl"
    kept.parent.mkdir(parents=True)
    kept.touch()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    assert state.own_transcript(data, {"agent": handler}) == str(kept)


def test_drive_handler_starts_the_handler_in_the_drives_checkout_while_the_communicator_still_runs_in_an_older_one(
    communicator: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    behind, kept = tmp_path / "durable-agents", tmp_path / "drive-demo"
    with state.edit("demo") as data:
        data["roles"]["communicator"]["cwd"] = str(behind)
    monkeypatch.setattr(orca, "checkout", lambda drive, clone: kept if (drive, clone) == ("demo", str(behind)) else pytest.fail(clone))
    ran: list[tuple[list[str], Path]] = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: ran.append((argv, checkout)) or "")
    start(run, tmp_path)
    [(argv, checkout)] = ran
    assert (argv[:4], checkout) == (["claude", "--bg", "--agent", agents.HANDLER], kept)


def test_on_a_drive_with_no_communicator_drive_handler_starts_the_handler_beside_the_coordinator_and_its_line_reaches_the_coordinator(
    demo: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[tuple[list[str], Path]] = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: ran.append((argv, checkout)) or "")
    coordinator = state.load("demo")["roles"]["coordinator"]
    assert "communicator" not in state.load("demo")["roles"]
    handler = start(run, tmp_path)
    [(argv, checkout)] = ran
    assert (argv[:4], checkout) == (["claude", "--bg", "--agent", agents.HANDLER], Path(coordinator["cwd"]))
    assert json.loads(argv[argv.index("--settings") + 1])["env"]["CCI_PARENT_SESSION"] == coordinator["session"]
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session")
    step = {"session_id": "h-session", "agent_type": agents.HANDLER, "transcript_path": str(transcript), "tool_name": "Bash"}
    assert agents.seen(step) == step | {"agent_id": handler, "agent_transcript_path": str(transcript), "handler_session": "h-session"}
    assert state.session_role(state.load("demo"), "h-session") == "communicator"
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: None)
    monkeypatch.setattr(agents, "background_job", lambda session: (BACKGROUND_JOB, {"sessionId": session}))
    monkeypatch.setattr(one_line_result, "block_reason", lambda payload: None)
    assert stop(monkeypatch, capsys, "h-session", transcript, LINE) == ""
    assert ran[-1][0] == ["cci", "post", "--drive", "demo", "--lane", "handler", "--kind", "done", "--to", "root@demo", "--id", f"handler-{handler}", "--text", LINE]
    assert state.load("demo")["handlers"] == {}


def test_the_agent_tool_starts_no_handler(communicator: str) -> None:
    call = {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "tool_name": "Agent", "tool_input": {"subagent_type": agents.HANDLER, "prompt": "Record: 1", "description": "d"}}
    refused = agent_brief.deny_reason(call)
    assert refused.startswith("refused: a handler starts with the drive's handler tool")
    assert agents.ruled(refused) == {"judged": False}
    assert agent_brief.deny_reason(call | {"tool_input": call["tool_input"] | {"subagent_type": "long-running:reader"}}).startswith(
        "refused: a long-running:reader starts only on a brief the drive wrote"
    )


def test_a_handler_ending_hands_its_line_to_the_communicator_and_removes_its_session_which_stops_hearing_his_dm(
    communicator: str,
    run: Run,
    tmp_path: Path,
    started: list[list[str]],
    slack_calls: list[tuple[str, ...]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    handler = start(run, tmp_path)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Answers", "why is this card asking again?", "--kind", "answer")
    run("job", "add", "reader", "--order", "o1", "--goal", "answer him", "--agent", handler)
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session")
    removed: list[list[str]] = []
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: removed.append(argv))
    refused = stop(monkeypatch, capsys, "h-session", transcript, "I answered him in his thread")
    assert json.loads(refused)["decision"] == "block"
    assert len(started) == 1 and removed == []
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: started.append(argv) or "")
    monkeypatch.setattr(agents, "background_job", lambda session: (BACKGROUND_JOB, {"sessionId": session}) if session == "h-session" else None)
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{LINE}\n"))
    assert run("job", "report", "j1", "-")[0] == 0
    assert stop(monkeypatch, capsys, "h-session", transcript, LINE) == ""
    assert started[-1] == [
        "cci", "post", "--drive", "demo", "--lane", "handler", "--kind", "done", "--to", "communicator@demo", "--id", f"handler-{handler}", "--text", LINE,
    ]
    assert removed == [["claude", "rm", BACKGROUND_JOB]]
    assert slack_calls[-1] == ("unfollow", "--channel", "D123", "--session", "h-session", "--cwd", str(tmp_path))
    data = state.load("demo")
    assert data["handlers"] == {}
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["transcript"]) == ("done", str(transcript))
    assert state.own_transcript(data, data["jobs"]["j1"]) == str(transcript)


def test_a_handler_whose_session_still_runs_a_background_task_or_wakeup_may_not_end_whatever_stop_hook_active_says_since_both_end_with_its_session(
    communicator: str, run: Run, tmp_path: Path, started: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    handler = start(run, tmp_path)
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session")
    removed: list[list[str]] = []
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: removed.append(argv))
    monkeypatch.setattr(agents, "background_job", lambda session: (BACKGROUND_JOB, {"sessionId": session}))
    wait = {"id": "bw0snn3ln", "type": "shell", "status": "running", "description": "wait", "command": "until [ $(date +%s) -ge 1794677171 ]; do sleep 5; done; echo due"}
    wakeup = {"id": "c7", "schedule": "26 9 14 11 *", "recurring": False, "prompt": "read the claim's thread"}
    for held, named in (({"background_tasks": [wait]}, f"`bw0snn3ln` ({wait['command']})"), ({"session_crons": [wakeup]}, "`c7` (26 9 14 11 *: read the claim's thread)")):
        reason = json.loads(stop(monkeypatch, capsys, "h-session", transcript, LINE, stop_hook_active=True, **held))["reason"]
        assert reason.startswith(f"Refused on purpose: your session still holds {named}.")
        assert "`TaskStop`" in reason and "`CronDelete`" in reason
    assert (len(started), removed, list(state.load("demo")["handlers"])) == (1, [], [handler])
    assert stop(monkeypatch, capsys, "h-session", transcript, LINE, stop_hook_active=True, background_tasks=[], session_crons=[]) == ""
    assert started[-1][-1] == LINE and removed == [["claude", "rm", BACKGROUND_JOB]]
    assert state.load("demo")["handlers"] == {}


def test_a_handlers_done_record_carries_its_facts_then_each_post_once_as_say_returned_it_whatever_the_handler_retyped(
    communicator: str, run: Run, tmp_path: Path, started: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    handler = start(run, tmp_path)
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session")
    reply = "https://acme.slack.com/archives/D123/p1794479609881679?thread_ts=1794478810.620779&cid=D123"
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": reply, "thread_ts": "1794478810.620779"})
    with monkeypatch.context() as scoped:
        scoped.setenv("CLAUDE_CODE_SESSION_ID", "h-session")
        assert talk.say("demo", {"text": "It copies on the new tree only.", "thread_ts": "1794478810.620779"}) == f"POSTED {reply}"
        assert talk.say("demo", {"edit": reply, "text": "It copies on the new tree alone."}) == f"EDITED {reply}"
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: None)
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: started.append(argv) or "")
    monkeypatch.setattr(agents, "background_job", lambda session: (BACKGROUND_JOB, {"sessionId": session}))
    assert stop(monkeypatch, capsys, "h-session", transcript, "POSTED https://acme.slack.com/archives/D123/p1794479609881679; waits id demo:j1-7") == ""
    assert started[-1][-1] == f"waits id demo:j1-7; POSTED {reply}"


def test_a_handlers_done_line_names_each_permalink_once_where_it_first_appears() -> None:
    post = "https://acme.slack.com/archives/D123/p1794479609881679?thread_ts=1794478810.620779&cid=D123"
    card, other = "https://acme.slack.com/archives/D123/p1794479700000100", "https://acme.slack.com/archives/D123/p1794479800000100"
    posted = [f"POSTED {post} CARD {post} {card}", f"EDITED {post}", f"EDITED {card}", f"POSTED {other}"]
    assert agents.named_once(posted) == [f"POSTED {post} CARD {card}", f"POSTED {other}"]


HIS_LINK = "https://acme.slack.com/archives/D123/p1794446196220659?thread_ts=1794440505.483109&cid=D123"
HIS_MESSAGE = f"Message (his words): Why is that only happening now?\nLink: {HIS_LINK}\nThread: D123 1794440505.483109\nDrive: demo\nScratch: {{scratch}}"


def test_a_record_for_a_busy_handlers_thread_reaches_that_handler_at_its_next_step(
    communicator: str,
    run: Run,
    tmp_path: Path,
    started: list[list[str]],
    cci_records: dict[str, dict],
    ended_sessions: set[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    thread = {"fields": {"attrs": {"thread": "1794440505.483109"}}}
    cci_records |= {
        "668288": {"seq": 668288, "kind": "owner", "lane": "communicator", "refs": {"url": HIS_LINK}} | thread,
        "668291": {"seq": 668291, "kind": "report", "lane": "root"} | thread,
        "668300": {"seq": 668300, "kind": "report", "lane": "root", "re": 668288, "fields": {"attrs": {}}},
        "668400": {"seq": 668400, "kind": "report", "lane": "root", "fields": {"attrs": {"thread": "1794400000.000100"}}},
    }
    message = HIS_MESSAGE.format(scratch=tmp_path / "h1")
    handler = tools.run("handler", "demo", {"brief": message}).removeprefix("started ")
    assert tools.run("handler", "demo", {"brief": message}) == f"handed to {handler}"
    handed = [f"Record: {seq} Drive: demo Scratch: {tmp_path / seq}" for seq in ("668291", "668300", "668288")]
    assert [tools.run("handler", "demo", {"brief": brief}) for brief in handed] == [f"handed to {handler}"] * 3
    other = tools.run("handler", "demo", {"brief": f"Record: 668400 Drive: demo Scratch: {tmp_path / '668400'}"}).removeprefix("started ")
    assert tools.run("handler", "demo", {"brief": f"Record: 668400 Drive: demo Scratch: {tmp_path / '668400'}"}) == f"handed to {other}"
    assert len([argv for argv in started if argv[:2] == ["claude", "--bg"]]) == 2

    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session")
    capsys.readouterr()
    handed_records.main()
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("drive handler handed you 3 more briefs") and all(brief in context for brief in handed)
    handed_records.main()
    assert capsys.readouterr().out == ""

    late = f"Record: 668291 Drive: demo Scratch: {tmp_path / 'late'}"
    assert tools.run("handler", "demo", {"brief": late}) == f"handed to {handler}"
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: None)
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: started.append(argv) or "")
    monkeypatch.setattr(agents, "background_job", lambda session: (BACKGROUND_JOB, {"sessionId": session}))
    refused = json.loads(stop(monkeypatch, capsys, "h-session", transcript, LINE))
    assert refused["decision"] == "block" and late in refused["reason"] and refused["judged"] is False
    assert handler in state.load("demo")["handlers"] and not [argv for argv in started if argv[:2] == ["cci", "post"]]
    assert stop(monkeypatch, capsys, "h-session", transcript, LINE) == ""
    [post] = [argv for argv in started if argv[:2] == ["cci", "post"]]
    assert post[-1] == LINE
    assert handler not in state.load("demo")["handlers"]

    restarted = tools.run("handler", "demo", {"brief": message}).removeprefix("started ")
    with state.edit("demo") as data:
        data["handlers"][restarted]["session"] = "died-session"
    ended_sessions.add("died-session")
    assert tools.run("handler", "demo", {"brief": message}).startswith("started ")
    assert restarted not in state.load("demo")["handlers"]


def test_a_handlers_done_record_leads_with_the_ts_of_each_message_of_his_it_took_by_handover_ahead_of_its_posts(
    communicator: str, tmp_path: Path, started: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    handler = tools.run("handler", "demo", {"brief": HIS_MESSAGE.format(scratch=tmp_path / "h1")}).removeprefix("started ")
    status = "https://acme.slack.com/archives/D123/p1794446901330512?thread_ts=1794440505.483109&cid=D123"
    later = f"Message (his words): Status?\nLink: {status}\nThread: D123 1794440505.483109\nDrive: demo\nScratch: {tmp_path / 'h2'}"
    assert tools.run("handler", "demo", {"brief": later}) == f"handed to {handler}"
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session")
    handed_records.main()
    capsys.readouterr()
    reply = "https://acme.slack.com/archives/D123/p1794447012004417?thread_ts=1794440505.483109&cid=D123"
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": reply, "thread_ts": "1794440505.483109"})
    with monkeypatch.context() as scoped:
        scoped.setenv("CLAUDE_CODE_SESSION_ID", "h-session")
        talk.say("demo", {"text": "It only happens on the new tree.", "thread_ts": "1794440505.483109"})
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: None)
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: started.append(argv) or "")
    monkeypatch.setattr(agents, "background_job", lambda session: (BACKGROUND_JOB, {"sessionId": session}))
    assert stop(monkeypatch, capsys, "h-session", transcript, "POSTED https://acme.slack.com/archives/D123/p1794447012004417; his Status? too", HIS_MESSAGE.format(scratch=tmp_path / "h1")) == ""
    assert started[-1][-1] == f"TOOK 1794446901.330512; his Status? too; POSTED {reply}"


def test_the_watchs_brief_names_its_record_by_the_bare_seq_the_communicators_brief_names_so_both_hold_the_same_record(
    communicator: str, tmp_path: Path, started: list[list[str]], cci_records: dict[str, dict]
) -> None:
    handler = tools.run("handler", "demo", {"brief": f"Record: 680562 Drive: demo Scratch: {tmp_path / 'quiet-680562'}"}).removeprefix("started ")
    assert state.load("demo")["handlers"][handler]["holds"]["record"] == "680562"
    assert tools.run("handler", "demo", {"brief": f"Record: 680562 Drive: demo Scratch: {tmp_path / 'handler'}"}) == f"handed to {handler}"
    assert state.load("demo")["handlers"][handler]["handed"] == []
    assert len([argv for argv in started if argv[:2] == ["claude", "--bg"]]) == 1


def test_a_post_report_gets_one_handler_whichever_of_the_handler_tool_and_the_watchs_read_comes_first_however_soon_the_first_ends(
    communicator: str, tmp_path: Path, started: list[list[str]], cci_records: dict[str, dict], posts_to_communicator: list[str]
) -> None:
    for seq in ("706844", "706845"):
        cci_records[seq] = {"seq": int(seq), "kind": "report", "lane": "iris", "to": ["communicator@demo"], "text": "POST the release shipped", "fields": {"attrs": {}}}
    watching = watch.Watcher("demo", offset=state.events_end("demo"))
    posts_to_communicator.append("706844")
    assert tools.run("handler", "demo", {"brief": f"Record: 706844 Drive: demo Scratch: {tmp_path / 'handler-706844'}"}).startswith("started ")
    with state.edit("demo") as data:
        data["handlers"].clear()
    posts_to_communicator.append("706845")
    assert watch.post_lines(watching, state.now()) == []
    [live] = state.load("demo")["handlers"]
    assert tools.run("handler", "demo", {"brief": f"Record: 706845 Drive: demo Scratch: {tmp_path / 'handler-706845'}"}) == f"handed to {live}"
    with state.edit("demo") as data:
        data["handlers"].clear()
    assert watch.post_lines(watching, state.now()) == []
    with pytest.raises(state.DriveError, match="the drive read POST report 706845 before this call, and the handler it started then has ended on it"):
        tools.run("handler", "demo", {"brief": f"Record: 706845 Drive: demo Scratch: {tmp_path / 'handler-706845'}"})
    assert [argv[-1].split()[1] for argv in started if argv[:2] == ["claude", "--bg"]] == ["706844", "706845"]


LIVE_RECORDS = {
    "678142": {"kind": "report", "attrs": {"order": "o761", "progress": "true", "quiet": "true", "thread": "1794494004.895129"}},
    "678158": {"kind": "report", "attrs": {"incident": "true", "order": "o761", "progress": "true", "thread": "1794494004.895129"}},
    "683754": {"kind": "handoff", "attrs": {"order": "o896", "thread": "1794528532.773759"}},
    "683805": {"kind": "report", "re": 683754, "attrs": {"job": "j2600", "order": "o896", "progress": "true", "thread": "1794528532.773759"}},
    "683812": {"kind": "correction", "re": 683805, "attrs": {"job": "j2600", "order": "o896", "thread": "1794528532.773759"}},
    "683922": {"kind": "report", "attrs": {"job": "j2698", "order": "o968", "progress": "true", "thread": "1794536668.729819"}},
    "683946": {"kind": "report", "re": 683922, "attrs": {"job": "j2698", "order": "o968", "progress": "true", "thread": "1794536668.729819"}},
    "690001": {"kind": "report", "attrs": {"order": "o9"}},
    "690002": {"kind": "correction", "re": 690001, "attrs": {}},
    "690839": {"kind": "done", "attrs": {}},
    "700930": {"kind": "handoff", "attrs": {}},
    "701014": {"kind": "evidence", "re": 700930, "attrs": {}},
    "701026": {"kind": "handoff", "re": 701014, "attrs": {}},
    "701042": {"kind": "report", "attrs": {"order": "o1433", "quiet": "true", "thread": ALERT_THREAD}},
    "701049": {"kind": "evidence", "re": 701026, "attrs": {}},
    "701054": {"kind": "report", "re": 701049, "attrs": {"incident": "true", "order": "o1433", "progress": "true", "urgent": "true"}},
    "701056": {"kind": "report", "re": 700999, "attrs": {"job": "j4247", "order": "o1433", "progress": "true", "replaces": "700999"}},
    "701099": {"kind": "report", "attrs": {"order": "o1433", "quiet": "true", "thread": ALERT_THREAD}},
    "701102": {"kind": "report", "re": 701056, "attrs": {"job": "j4244", "order": "o1433", "progress": "true"}},
}
WATCH_QUIET = "Record: 678142 Drive: demo Scratch: {scratch}/quiet-678142"
COMMUNICATOR_QUIET = (
    "Record: 678142\nMessage (his words): none\nLink: none\nThread: 1794494004.895129\nDrive: demo\nPaths: none\nScratch: {scratch}/handler"
    "\nDeliverable: read incident o761's order and post where it stands now\nContext: watch-quiet report\nWaits: none"
)


HELD_THREAD = "1794557485.395319"
HIS_FIRST = (
    f"Message (his words): explain why one change touches every client\nLink: https://acme.slack.com/archives/D123/p1794564987410009?thread_ts={HELD_THREAD}&cid=D123"
    f"\nThread: {HELD_THREAD}\nDrive: demo\nScratch: {{scratch}}/first"
)
HIS_FOLLOW_UP = f"Message (his words): and?\nLink: https://acme.slack.com/archives/D123/p1794565618666599?thread_ts={HELD_THREAD}&cid=D123\nThread: {HELD_THREAD}\nDrive: demo\nScratch: {{scratch}}/and"
HIS_UNLINKED = f"Message (his words): and?\nThread: D123 {HELD_THREAD}\nDrive: demo\nScratch: {{scratch}}/unlinked"
THREAD_LINE = f"Record: 690839 Drive: demo Scratch: {{scratch}}/690839\nThread: {HELD_THREAD}"
SINCE_COMPACTION = f"Thread: D123 {HELD_THREAD}\nContext: he waits on a handler whose done record has not come\nDrive: demo\nScratch: {{scratch}}/compacted"
HIS_IN_INCIDENT = "Message (his words): is it still down?\nLink: https://acme.slack.com/archives/D123/p1794494100000100?thread_ts=1794494004.895129&cid=D123\nThread: D123 1794494004.895129\nDrive: demo\nScratch: {scratch}/down"


def brief_of(seq: str) -> str:
    return f"Record: {seq} Drive: demo Scratch: {{scratch}}/communicator"


def watch_quiet(seq: str) -> str:
    return f"Record: {seq} Drive: demo Scratch: {{scratch}}/quiet-{seq}"


@pytest.mark.parametrize(
    ("first", "then", "met"),
    [
        pytest.param(WATCH_QUIET, COMMUNICATOR_QUIET, "copy", id="o761-watch-a4fa7ba2-then-communicator-7aa7631a"),
        pytest.param(COMMUNICATOR_QUIET, WATCH_QUIET, "copy", id="o761-communicator-then-watch"),
        pytest.param(WATCH_QUIET, brief_of("678158"), "handed", id="o761-roots-678158-in-the-watch-handlers-thread"),
        pytest.param(brief_of("683805"), brief_of("683812"), "handed", id="683816-correction-683812-while-8d7559f6-held-683805"),
        pytest.param(brief_of("683922"), brief_of("683946"), "handed", id="683948-correction-683946-while-e5faab45-held-683922"),
        pytest.param(brief_of("690001"), brief_of("690002"), "handed", id="a-threadless-correction-whose-re-a-handler-holds"),
        pytest.param(brief_of("690002"), brief_of("690001"), "handed", id="a-threadless-record-the-held-correction-re-names"),
        pytest.param(brief_of("683922"), WATCH_QUIET, "started", id="another-thread-and-no-re"),
        pytest.param(HIS_FIRST, HIS_FOLLOW_UP, "handed", id="o1153-his-and-b04e97f3-while-8712a691-held-his-message-in-its-thread"),
        pytest.param(HIS_FIRST, THREAD_LINE, "handed", id="o1153-threadless-690839-38104256-whose-thread-line-names-8712a691s-thread"),
        pytest.param(HIS_FIRST, HIS_UNLINKED, "handed", id="his-words-with-no-link-in-a-held-thread-is-no-copy"),
        pytest.param(SINCE_COMPACTION, THREAD_LINE, "handed", id="o1502-be5951bd-record-in-the-thread-9206db80s-thread-and-context-brief-held"),
        pytest.param(WATCH_QUIET, HIS_IN_INCIDENT, "handed", id="his-words-in-the-thread-the-watchs-quiet-handler-holds"),
        pytest.param(watch_quiet("701042"), brief_of("701054"), "handed", id="o1433-threadless-701054-while-92404de6-held-701042-in-its-orders-alert-thread"),
        pytest.param(watch_quiet("701042"), brief_of("701056"), "handed", id="o1433-threadless-701056-re-700999-while-92404de6-held-701042"),
        pytest.param(watch_quiet("701099"), brief_of("701102"), "handed", id="o1433-threadless-701102-re-701056-while-da98a680-held-701099"),
        pytest.param(brief_of("701054"), watch_quiet("701042"), "handed", id="o1433-the-watchs-701042-while-a-handler-held-threadless-701054"),
    ],
)
def test_a_brief_in_a_live_handlers_thread_or_on_its_re_chain_is_handed_to_it_whichever_session_briefed_either_and_a_copy_adds_nothing(
    communicator: str, tmp_path: Path, started: list[list[str]], cci_records: dict[str, dict], first: str, then: str, met: str
) -> None:
    cci_records |= {seq: {"seq": int(seq), "kind": kept["kind"], "lane": "root", "re": kept.get("re"), "fields": {"attrs": kept["attrs"]}} for seq, kept in LIVE_RECORDS.items()}
    with state.edit("demo") as data:
        alert = state.new_order("an alert's runs are refused", f"https://acme.slack.com/archives/C0ALERT1/p{ALERT_THREAD.replace('.', '')}", ("C0ALERT1", ALERT_THREAD), {}, state.stamp())
        data["orders"]["o1433"] = alert | {"incident": True}
    handler = tools.run("handler", "demo", {"brief": first.format(scratch=tmp_path)}).removeprefix("started ")
    assert tools.run("handler", "demo", {"brief": then.format(scratch=tmp_path)}).startswith("started " if met == "started" else f"handed to {handler}")
    handlers = state.load("demo")["handlers"]
    assert len(handlers) == len([argv for argv in started if argv[:2] == ["claude", "--bg"]]) == (2 if met == "started" else 1)
    assert handlers[handler]["handed"] == ([then.format(scratch=tmp_path)] if met == "handed" else [])


def test_a_handler_is_gone_once_no_running_process_names_its_session_at_work_and_its_job_ended_a_turn_rests_or_was_removed_and_one_its_launch_has_not_keyed_once_its_start_window_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "claude"
    (config / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    spare = subprocess.Popen(["sleep", "60"])
    for pid, session, status in ((os.getpid(), "busy", "busy"), (spare.pid, "idle", "idle"), (1, "died", "busy")):
        (config / "sessions" / f"{pid}.json").write_text(json.dumps({"sessionId": session, "kind": "bg", "status": status}))
    jobs = {
        "e5f6a7b8": {"sessionId": "busy", "state": "done", "tempo": "idle"},
        "a1b2c3d4": {"sessionId": "idle", "state": "working", "tempo": "idle"},
        "b8c9d0e1": {"sessionId": "died", "state": "stopped", "tempo": "idle"},
        "2a74ab93": {"sessionId": "blocked", "state": "blocked", "tempo": "blocked", "firstTerminalAt": None},
        "e8ee9c52": {"sessionId": "dialog", "state": "working", "tempo": "blocked", "firstTerminalAt": None},
        "14548b14": {"sessionId": "failed", "state": "failed"},
        "260762ba": {"sessionId": "done", "state": "done", "tempo": "idle"},
        "a81dc737": {"sessionId": "launching", "state": "working", "tempo": "active", "firstTerminalAt": None},
    }
    for short, kept in jobs.items():
        (config / "jobs" / short).mkdir(parents=True)
        (config / "jobs" / short / "state.json").write_text(json.dumps(kept))
    entries = {session: {"session": session, "started": state.stamp()} for session in [*(kept["sessionId"] for kept in jobs.values()), "removed"]}
    try:
        assert {session: GONE(entry) for session, entry in entries.items()} == {
            "busy": False,
            "idle": True,
            "died": True,
            "blocked": True,
            "dialog": True,
            "failed": True,
            "done": True,
            "launching": False,
            "removed": True,
        }
        assert GONE({"session": None, "started": state.stamp()}) is False
        assert GONE({"session": None, "started": state.stamp(state.now() - agents.START_WINDOW)}) is True
        assert GONE(entries["done"] | {"wakes": state.stamp(state.now() + timedelta(minutes=1))}) is False
    finally:
        spare.kill()
    assert agents.background_job("blocked") == ("2a74ab93", jobs["2a74ab93"])


def test_a_handler_whose_stop_hook_meets_a_drive_a_newer_install_saved_still_ends_and_the_next_brief_in_its_thread_starts_a_fresh_handler_handed_what_it_never_took(
    communicator: str,
    tmp_path: Path,
    started: list[list[str]],
    cci_records: dict[str, dict],
    ended_sessions: set[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cci_records |= {seq: {"seq": int(seq), "kind": "report", "lane": "root", "fields": {"attrs": {"thread": "1794527558.312319"}}} for seq in ("684116", "684835", "684840")}
    briefs = {seq: f"Record: {seq} Drive: demo Scratch: {tmp_path / seq}" for seq in cci_records}
    handler = tools.run("handler", "demo", {"brief": briefs["684116"]}).removeprefix("started ")
    assert [tools.run("handler", "demo", {"brief": briefs[seq]}) for seq in ("684835", "684840")] == [f"handed to {handler}"] * 2
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session")
    major, minor, patch = state.VERSION.split(".")
    state.store("demo", state.stored("demo") | {"saved_by": f"{major}.{minor}.{int(patch) + 1}"})
    monkeypatch.setattr(agents, "hold", lambda payload: pytest.fail("a handler's Stop hook holds no job"))
    removed: list[list[str]] = []
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: removed.append(argv))
    monkeypatch.setattr(agents, "background_job", lambda session: (BACKGROUND_JOB, {"sessionId": session}))
    assert stop(monkeypatch, capsys, "h-session", transcript, LINE) == ""
    assert started[-1] == ["cci", "post", "--drive", "demo", "--lane", "handler", "--kind", "done", "--to", "communicator@demo", "--id", f"handler-{handler}", "--text", LINE]
    assert removed == [["claude", "rm", BACKGROUND_JOB]]

    state.store("demo", state.stored("demo") | {"saved_by": state.VERSION})
    ended_sessions.add("h-session")
    fresh = tools.run("handler", "demo", {"brief": briefs["684835"]}).removeprefix("started ")
    assert {key: entry["handed"] for key, entry in state.load("demo")["handlers"].items()} == {fresh: [briefs["684840"]]}



@pytest.mark.parametrize(
    ("job", "process", "fresh"),
    [
        ({"state": "blocked", "tempo": "blocked", "firstTerminalAt": None}, None, True),
        ({"state": "working", "tempo": "blocked", "firstTerminalAt": None}, None, True),
        ({"state": "failed", "tempo": "idle"}, None, True),
        ({"state": "done", "tempo": "idle"}, "idle", True),
        (None, None, True),
        ({"state": "working", "tempo": "active", "firstTerminalAt": None}, None, False),
        ({"state": "done", "tempo": "idle"}, "busy", False),
    ],
    ids=["2a74ab93-blocked-on-its-last-words", "e8ee9c52-stuck-on-a-startup-dialog", "14548b14-failed-before-init", "260762ba-idle-after-its-turn", "removed", "launching", "at-work"],
)
def test_a_handler_holds_its_thread_only_while_its_launchs_session_or_job_is_at_work_and_his_next_message_there_starts_a_fresh_handler_handed_what_it_never_took(
    communicator: str,
    tmp_path: Path,
    started: list[list[str]],
    cci_records: dict[str, dict],
    launched_sessions: list[str],
    monkeypatch: pytest.MonkeyPatch,
    job: dict | None,
    process: str | None,
    fresh: bool,
) -> None:
    config = tmp_path / "claude"
    (config / "sessions").mkdir(parents=True)
    (config / "jobs" / "2a74ab93").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setattr(agents, "gone", GONE)
    cci_records |= {seq: {"seq": int(seq), "kind": "report", "lane": "root", "fields": {"attrs": {"thread": "1794600871.442519"}}} for seq in ("704420", "704431", "704447")}
    briefs = {seq: f"Record: {seq} Drive: demo Scratch: {tmp_path / seq}" for seq in cci_records}
    handler = tools.run("handler", "demo", {"brief": briefs["704420"]}).removeprefix("started ")
    kept = config / "jobs" / "2a74ab93" / "state.json"
    kept.write_text(json.dumps({"sessionId": launched_sessions[0], "state": "working", "tempo": "active", "firstTerminalAt": None}))
    assert tools.run("handler", "demo", {"brief": briefs["704431"]}) == f"handed to {handler}"
    if job:
        kept.write_text(json.dumps({"sessionId": launched_sessions[0]} | job))
    else:
        kept.unlink()
    spare = subprocess.Popen(["sleep", "60"])
    if process:
        (config / "sessions" / f"{spare.pid}.json").write_text(json.dumps({"sessionId": launched_sessions[0], "kind": "bg", "status": process}))
    try:
        then = tools.run("handler", "demo", {"brief": briefs["704447"]})
    finally:
        spare.kill()
    handlers = state.load("demo")["handlers"]
    if not fresh:
        assert (then, handlers[handler]["handed"]) == (f"handed to {handler}", [briefs["704431"], briefs["704447"]])
        return
    assert {key: entry["handed"] for key, entry in handlers.items()} == {then.removeprefix("started "): [briefs["704431"]]}


GHOST = "c52c88a1"
GHOST_THREAD = "1794638766.436089"


@pytest.mark.parametrize("kept", [True, False], ids=["c52c88a1-left-mid-start-by-an-install", "saved-before-entries-kept-their-brief"])
def test_a_handler_whose_launch_never_named_its_session_holds_its_thread_only_through_its_start_window_then_a_rerun_of_a_report_handed_to_it_or_his_next_message_starts_a_fresh_handler_on_all_it_held(
    communicator: str, tmp_path: Path, started: list[list[str]], cci_records: dict[str, dict], monkeypatch: pytest.MonkeyPatch, kept: bool
) -> None:
    monkeypatch.setattr(agents, "gone", GONE)
    cci_records |= {
        seq: {"seq": int(seq), "kind": "report", "lane": "root", "to": ["communicator@demo"], "text": f"POST the spill test, {seq}", "fields": {"attrs": {"order": "o1512"}}}
        for seq in ("715733", "716199", "716309")
    }
    briefs = {seq: brief_of(seq).format(scratch=tmp_path) for seq in cci_records}
    his = HIS_FOLLOW_UP.replace(HELD_THREAD, GHOST_THREAD).format(scratch=tmp_path)

    def left_mid_start(at: datetime) -> None:
        with state.edit("demo") as data:
            spill = state.new_order("the spill test", f"https://acme.slack.com/archives/C0ALERT1/p{GHOST_THREAD.replace('.', '')}", ("C0ALERT1", GHOST_THREAD), {}, state.stamp())
            data["orders"]["o1512"] = spill | {"incident": True}
            holds = {"message": None, "link": None, "thread": GHOST_THREAD, "record": "715733", "re": []}
            data["handlers"] = {GHOST: {"session": None, "started": state.stamp(at), "brief": briefs["715733"] if kept else None, "holds": holds, "handed": [briefs["716199"]], "took": [], "posted": []}}

    left_mid_start(state.now())
    assert [tools.run("handler", "demo", {"brief": brief}) for brief in (briefs["716309"], his)] == [f"handed to {GHOST}"] * 2

    left_mid_start(state.now() - agents.START_WINDOW)
    rerun = tools.run("handler", "demo", {"brief": briefs["716199"]}).removeprefix("started ")
    assert {key: entry["handed"] for key, entry in state.load("demo")["handlers"].items()} == {rerun: [briefs["715733"]] if kept else []}

    left_mid_start(state.now() - agents.START_WINDOW)
    fresh = tools.run("handler", "demo", {"brief": his}).removeprefix("started ")
    assert {key: entry["handed"] for key, entry in state.load("demo")["handlers"].items()} == {fresh: [*([briefs["715733"]] if kept else []), briefs["716199"]]}
    assert [argv[-1] for argv in started if argv[:2] == ["claude", "--bg"]] == [briefs["716199"] + UNFROZEN, his + UNFROZEN]


API_ERROR ="Please run /login · API Error: 403 Attention Required! | Cloudflare"
EARLIER = "2026-11-13T09:46:00.000Z"
DIED = {
    "type": "assistant",
    "timestamp": EARLIER,
    "isApiErrorMessage": True,
    "error": "authentication_failed",
    "message": {"role": "assistant", "model": agents.SYNTHETIC, "stop_reason": "stop_sequence", "content": [{"type": "text", "text": API_ERROR}]},
}
WOKEN = {
    "type": "user",
    "timestamp": EARLIER,
    "origin": {"kind": "task-notification", "producer": "session-task"},
    "message": {
        "role": "user",
        "content": "<task-notification>\n<summary>Stop hook feedback</summary>\n</task-notification>\n<system-reminder>\n"
        f'Stop hook blocking error from command "StopFailure:authentication_failed": {agents.RESUMED}\n\n</system-reminder>',
    },
}
ANSWERED = {"type": "assistant", "timestamp": EARLIER, "message": {"role": "assistant", "model": "claude-opus-5-5", "stop_reason": "end_turn", "content": [{"type": "text", "text": LINE}]}}
HIS_DM = "https://poetic-technology.slack.com/archives/D0C4HQWPLCC"
ANSWER_THREAD = "1794565600.000100"


def record_brief(seq: str) -> str:
    return f"Record: {seq} Drive: demo Scratch: {{scratch}}"


def his_brief(words: str, message: str, thread: str | None = None) -> str:
    link = f"{HIS_DM}/p{message.replace('.', '')}" + (f"?thread_ts={thread}&cid=D0C4HQWPLCC" if thread else "")
    return f"Message (his words): {words}\nLink: {link}\nThread: D0C4HQWPLCC {thread or message}\nDrive: demo\nScratch: {{scratch}}"


def outage(resumes: int) -> tuple[dict, ...]:
    return (DIED, *(row for _ in range(resumes) for row in (WOKEN, DIED)))


def failed(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], session: str, transcript: Path, rows: tuple[dict, ...]) -> tuple[int, str]:
    try:
        out = stop(monkeypatch, capsys, session, transcript, API_ERROR, event="StopFailure", rows=rows)
    except SystemExit as exit:
        return exit.code, capsys.readouterr().err
    return 0, out


def said(monkeypatch: pytest.MonkeyPatch, session: str, permalink: str) -> str:
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": permalink, "thread_ts": ANSWER_THREAD})
    with monkeypatch.context() as scoped:
        scoped.setenv("CLAUDE_CODE_SESSION_ID", session)
        return talk.say("demo", {"text": "It is answered.", "thread_ts": ANSWER_THREAD})


DIED_ON_THE_403 = [
    pytest.param(record_brief("690458"), True, id="4063bb1c-record-690458-after-its-say"),
    pytest.param(record_brief("690461"), True, id="53dea792-record-690461-after-its-say"),
    pytest.param(record_brief("690498"), False, id="6c82d296-record-690498"),
    pytest.param(record_brief("690503"), False, id="1a449873-record-690503"),
    pytest.param(record_brief("690504"), False, id="fdc934ab-record-690504"),
    pytest.param(record_brief("690532"), False, id="0c251e9b-record-690532"),
    pytest.param(his_brief("his message", "1794564987.410009", "1794557485.395319"), False, id="34adbd2b-his-message-in-a-thread"),
    pytest.param(record_brief("690570"), False, id="53631dc1-record-690570"),
    pytest.param(record_brief("690575"), False, id="adc6ff38-record-690575"),
    pytest.param(record_brief("690587"), False, id="6d0871cb-record-690587"),
    pytest.param(his_brief("his message", "1794565058.724059"), False, id="c003ff53-his-message-at-his-dm-root"),
    pytest.param(record_brief("689871"), True, id="5f0775f9-report-689871-after-its-say"),
    pytest.param(his_brief("his share of an alert", "1794563392.336679"), False, id="6c496312-his-share"),
    pytest.param(his_brief("do a sweep of the times I've had to corect you", "1794563477.395439"), False, id="646becde-his-message"),
    pytest.param(record_brief("690049"), False, id="4e9e029f-report-690049"),
]


@pytest.mark.parametrize(("brief", "posted_first"), DIED_ON_THE_403)
def test_a_handler_whose_four_resumes_an_api_error_ended_too_starts_again_as_itself_on_its_brief_and_posts_one_done_once_a_turn_answers(
    communicator: str,
    tmp_path: Path,
    started: list[list[str]],
    launched_sessions: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    brief: str,
    posted_first: bool,
) -> None:
    brief = brief.format(scratch=tmp_path / "h")
    handler = tools.run("handler", "demo", {"brief": brief}).removeprefix("started ")
    held = state.load("demo")["handlers"][handler]["holds"]
    removed: list[list[str]] = []
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: removed.append(argv))
    monkeypatch.setattr(agents, "background_job", lambda session: (BACKGROUND_JOB, {"sessionId": session}))
    posts: list[str] = []
    for attempt, session in enumerate(("h-403", "h-503"), start=1):
        transcript = tmp_path / f"{session}.jsonl"
        bind(monkeypatch, handler, session)
        if posted_first and attempt == 1:
            posts.append(said(monkeypatch, session, f"{HIS_DM}/p1794565600000200?thread_ts={ANSWER_THREAD}&cid=D0C4HQWPLCC"))
        assert stop(monkeypatch, capsys, session, transcript, API_ERROR, brief, "StopFailure", outage(len(agents.OUTAGE_WAITS))) == ""
        starts = [argv for argv in started if argv[:2] == ["claude", "--bg"]]
        assert [argv[-1] for argv in starts] == [brief.strip() + UNFROZEN] * (attempt + 1)
        assert json.loads(starts[-1][starts[-1].index("--settings") + 1])["env"][state.HANDLER_ENV] == f"demo/{handler}"
        entry = state.load("demo")["handlers"][handler]
        assert (entry["session"], entry["holds"], entry["posted"]) == (launched_sessions[attempt], held, posts)
        assert removed == [["claude", "rm", BACKGROUND_JOB]] * attempt
    assert agents.brief_path("demo", handler).read_text() == brief
    assert not [argv for argv in started if "done" in argv]

    transcript = tmp_path / "h-answered.jsonl"
    bind(monkeypatch, handler, "h-answered")
    posts.append(said(monkeypatch, "h-answered", f"{HIS_DM}/p1794565600000300?thread_ts={ANSWER_THREAD}&cid=D0C4HQWPLCC"))
    assert stop(monkeypatch, capsys, "h-answered", transcript, posts[-1], brief) == ""
    [done] = [argv for argv in started if "done" in argv]
    assert done[-3:] == [f"handler-{handler}", "--text", "; ".join(posts)]
    assert handler not in state.load("demo")["handlers"]


def test_a_handler_an_api_error_ended_past_its_four_resumes_is_handed_again_each_brief_it_took_and_no_copy_takes_what_it_holds_meanwhile(
    communicator: str, tmp_path: Path, started: list[list[str]], cci_records: dict[str, dict], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cci_records |= {seq: {"seq": int(seq), "kind": "report", "lane": "root", "fields": {"attrs": {"thread": "1794557485.395319"}}} for seq in ("690458", "690461", "690475")}
    briefs = {seq: f"Record: {seq} Drive: demo Scratch: {tmp_path / seq}" for seq in cci_records}
    handler = tools.run("handler", "demo", {"brief": briefs["690458"]}).removeprefix("started ")
    assert tools.run("handler", "demo", {"brief": briefs["690461"]}) == f"handed to {handler}"
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session")
    capsys.readouterr()
    handed_records.main()
    assert briefs["690461"] in capsys.readouterr().out
    assert tools.run("handler", "demo", {"brief": briefs["690475"]}) == f"handed to {handler}"
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: None)
    monkeypatch.setattr(agents, "background_job", lambda session: (BACKGROUND_JOB, {"sessionId": session}))
    assert stop(monkeypatch, capsys, "h-session", transcript, API_ERROR, briefs["690458"], "StopFailure", outage(len(agents.OUTAGE_WAITS))) == ""
    entry = state.load("demo")["handlers"][handler]
    assert (entry["handed"], entry["took"]) == ([briefs["690461"], briefs["690475"]], [])
    assert tools.run("handler", "demo", {"brief": briefs["690458"]}) == f"handed to {handler}"
    assert state.load("demo")["handlers"][handler]["handed"] == [briefs["690461"], briefs["690475"]]


def test_a_handler_whose_turn_an_api_error_ended_resumes_in_its_own_session_after_1_2_4_and_8_minutes_holding_its_thread_and_counts_again_after_a_turn_that_ends(
    communicator: str, run: Run, tmp_path: Path, started: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    handler = start(run, tmp_path)
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session")
    capsys.readouterr()
    removed: list[list[str]] = []
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: removed.append(argv))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    waits: list[tuple[float, str | None, bool]] = []

    def slept(seconds: float) -> None:
        entry = state.load("demo")["handlers"][handler]
        waits.append((seconds, entry["session"], GONE(entry)))

    monkeypatch.setattr(agents.time, "sleep", slept)
    for resumes in range(len(agents.OUTAGE_WAITS)):
        assert failed(monkeypatch, capsys, "h-session", transcript, outage(resumes)) == (2, f"{agents.RESUMED}\n")
        assert state.load("demo")["handlers"][handler]["session"] == "h-session"
    assert failed(monkeypatch, capsys, "h-session", transcript, (*outage(3), WOKEN, ANSWERED, DIED)) == (2, f"{agents.RESUMED}\n")
    assert waits == [(seconds, "h-session", False) for seconds in (60, 120, 240, 480, 60)]
    assert (len([argv for argv in started if argv[:2] == ["claude", "--bg"]]), [argv for argv in started if "done" in argv], removed) == (1, [], [])


@pytest.mark.parametrize(("lane", "woken", "resumed"), [(True, False, True), (True, True, False), (False, False, False)], ids=["orca-lane", "orca-lane-woken-meanwhile", "no-drive-session"])
def test_a_session_no_drive_binds_resumes_after_an_api_error_only_as_an_orca_lane_nothing_woke_while_it_waited(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], lane: bool, woken: bool, resumed: bool
) -> None:
    monkeypatch.delenv(state.REMOTE_LANE_ENV, raising=False)
    if lane:
        monkeypatch.setenv(state.REMOTE_LANE_ENV, "1")
    transcript = tmp_path / "lane.jsonl"
    waits: list[float] = []

    def wait(seconds: float) -> None:
        waits.append(seconds)
        if woken:
            with transcript.open("a") as handle:
                handle.write(json.dumps({"type": "user", "timestamp": datetime.now(UTC).isoformat(), "message": {"role": "user", "content": "his answer"}}) + "\n")

    monkeypatch.setattr(agents.time, "sleep", wait)
    assert failed(monkeypatch, capsys, "lane-session", transcript, outage(0)) == ((2, f"{agents.RESUMED}\n") if resumed else (0, ""))
    assert waits == ([60] if lane else [])


NOTICE = "API Error: Opus 5.5's safeguards flagged this message (https://www.anthropic.com/legal/aup). This sometimes happens with safe, normal conversations."
SAFEGUARDED = {
    "type": "assistant",
    "timestamp": EARLIER,
    "isApiErrorMessage": True,
    "error": "invalid_request",
    "message": {"role": "assistant", "model": agents.SYNTHETIC, "stop_reason": "refusal", "content": [{"type": "text", "text": NOTICE}]},
}


def test_a_handler_a_models_safeguards_stopped_is_never_resumed_and_posts_one_done_carrying_claude_codes_notice(
    communicator: str, run: Run, tmp_path: Path, started: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    handler = start(run, tmp_path)
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session")
    removed: list[list[str]] = []
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: removed.append(argv))
    monkeypatch.setattr(agents, "background_job", lambda session: (BACKGROUND_JOB, {"sessionId": session}))
    monkeypatch.setattr(agents.time, "sleep", lambda seconds: pytest.fail("a safeguards stop waits on no resume"))
    assert stop(monkeypatch, capsys, "h-session", transcript, NOTICE, event="StopFailure", rows=(SAFEGUARDED,)) == ""
    assert [argv for argv in started if "done" in argv] == [
        ["cci", "post", "--drive", "demo", "--lane", "handler", "--kind", "done", "--to", "communicator@demo", "--id", f"handler-{handler}", "--text", NOTICE]
    ]
    assert (handler in state.load("demo")["handlers"], removed, len([argv for argv in started if argv[:2] == ["claude", "--bg"]])) == (False, [["claude", "rm", BACKGROUND_JOB]], 1)


def test_an_orca_lane_a_models_safeguards_stopped_is_never_resumed(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(state.REMOTE_LANE_ENV, "1")
    monkeypatch.setattr(agents.time, "sleep", lambda seconds: pytest.fail("a safeguards stop waits on no resume"))
    assert failed(monkeypatch, capsys, "lane-session", tmp_path / "lane.jsonl", (SAFEGUARDED,)) == (0, "")


def test_a_handler_whose_stop_check_raises_posts_its_done_before_the_error_goes_on(
    communicator: str, run: Run, tmp_path: Path, started: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    handler = start(run, tmp_path)
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session")
    removed: list[list[str]] = []
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: removed.append(argv))
    monkeypatch.setattr(agents, "background_job", lambda session: (BACKGROUND_JOB, {"sessionId": session}))

    def unreadable(payload: dict) -> str | None:
        raise state.DriveError("record 684115 could not be read: cci show exited 1")

    monkeypatch.setattr(one_line_result, "block_reason", unreadable)
    with pytest.raises(state.DriveError, match="record 684115 could not be read"):
        stop(monkeypatch, capsys, "h-session", transcript, LINE)
    assert started[-1] == ["cci", "post", "--drive", "demo", "--lane", "handler", "--kind", "done", "--to", "communicator@demo", "--id", f"handler-{handler}", "--text", LINE]
    assert removed == [["claude", "rm", BACKGROUND_JOB]]
