import io
import json
import os
import re
from collections.abc import Callable
from pathlib import Path

import agent_brief
import one_line_result
import pytest
import session_start
from drive import agents, reads, slack, state, talk, tools

Run = Callable[..., tuple[int, str]]
HIS_BRIEF = "Message (his words): why is this card asking again?\nThread: D123 1794380463.481959\nDrive: demo\nScratch: {scratch}\nDeliverable: his answer, in his thread"
LINE = "POSTED https://acme.slack.com/archives/D123/p1794380463481960"


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    argvs: list[list[str]] = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: argvs.append(argv) or "")
    return argvs


def start(run: Run, tmp_path: Path) -> str:
    code, out = run("handler", HIS_BRIEF.format(scratch=tmp_path / "scratch" / "h1"))
    assert code == 0
    return out.removeprefix("started ")


def bind(monkeypatch: pytest.MonkeyPatch, handler: str, session: str, transcript: Path) -> None:
    monkeypatch.setenv(state.HANDLER_ENV, f"demo/{handler}")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": session, "transcript_path": str(transcript), "source": "startup"})))
    session_start.main()


def stop(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], session: str, transcript: Path, line: str) -> str:
    transcript.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": HIS_BRIEF}}) + "\n")
    payload = {
        "session_id": session,
        "agent_type": agents.HANDLER,
        "hook_event_name": "Stop",
        "transcript_path": str(transcript),
        "cwd": str(transcript.parent),
        "stop_hook_active": False,
        "last_assistant_message": line,
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    one_line_result.main()
    return capsys.readouterr().out


def test_drive_handler_starts_its_own_claude_bg_session_and_records_that_he_wrote(
    communicator: str, run: Run, tmp_path: Path, started: list[list[str]]
) -> None:
    handler = start(run, tmp_path)
    data = state.load("demo")
    assert data["handlers"][handler]["session"] is None
    assert data["owner"]["heard"] is not None
    assert (tmp_path / "scratch" / "h1").is_dir()
    [argv] = started
    assert argv[:4] == ["claude", "--bg", "--agent", agents.HANDLER]
    env = json.loads(argv[argv.index("--settings") + 1])["env"]
    assert (env[state.HANDLER_ENV], env["CCI_PARENT_SESSION"], env["CLAUDE_LONG_RUNNING_DRIVE"]) == (f"demo/{handler}", os.environ["CLAUDE_CODE_SESSION_ID"], "demo")
    assert argv[-1] == HIS_BRIEF.format(scratch=tmp_path / "scratch" / "h1")
    assert tools.run("handler", "demo", {"brief": f"Record: 663488 Drive: demo Scratch: {tmp_path / 'h2'}"}).startswith("started ")
    assert state.load("demo")["owner"] == data["owner"]


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


def test_a_handler_claude_bg_could_not_start_leaves_nothing_behind(communicator: str, run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(argv: list[str], checkout: Path, env: dict | None = None) -> str:
        raise reads.ReadError("claude --bg: workspace not trusted")

    monkeypatch.setattr(reads, "run", refuse)
    assert run("handler", f"Record: 663488 Drive: demo Scratch: {tmp_path / 'h1'}")[0] == 2
    assert state.load("demo")["handlers"] == {}


def test_a_handlers_own_session_reads_as_an_agent_of_the_communicators(
    communicator: str, run: Run, tmp_path: Path, started: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = start(run, tmp_path)
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session", transcript)
    data = state.load("demo")
    holds = {"message": None, "link": None, "thread": "1794380463.481959", "record": None}
    assert data["handlers"][handler] | {"started": None} == {"session": "h-session", "transcript": str(transcript), "started": None, "holds": holds, "held_back": [], "posted": []}
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


def test_the_agent_tool_starts_no_handler(communicator: str) -> None:
    call = {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "tool_name": "Agent", "tool_input": {"subagent_type": agents.HANDLER, "prompt": "Record: 1", "description": "d"}}
    refused = agent_brief.deny_reason(call)
    assert refused.startswith("refused: a handler starts with the drive's handler tool")
    assert agents.ruled(refused) == {"judged": False}
    assert agent_brief.deny_reason(call | {"tool_input": call["tool_input"] | {"subagent_type": "long-running:reader"}}) is None


def test_a_handler_ending_hands_its_line_to_the_communicator_and_removes_its_session(
    communicator: str, run: Run, tmp_path: Path, started: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    handler = start(run, tmp_path)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Answers", "why is this card asking again?", "--kind", "answer")
    run("job", "add", "reader", "--order", "o1", "--goal", "answer him", "--agent", handler)
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session", transcript)
    removed: list[list[str]] = []
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: removed.append(argv))
    refused = stop(monkeypatch, capsys, "h-session", transcript, "I answered him in his thread")
    assert json.loads(refused)["decision"] == "block"
    assert len(started) == 1 and removed == []
    monkeypatch.setattr(
        reads,
        "run",
        lambda argv, checkout, env=None: started.append(argv) or (json.dumps([{"id": "b7f2", "sessionId": "h-session"}, {"id": "c1", "sessionId": "x"}]) if argv[1] == "agents" else ""),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{LINE}\n"))
    assert run("job", "report", "j1", "-")[0] == 0
    assert stop(monkeypatch, capsys, "h-session", transcript, LINE) == ""
    assert started[-2] == [
        "cci", "post", "--drive", "demo", "--lane", "handler", "--kind", "done", "--to", "communicator@demo", "--id", f"handler-{handler}", "--text", LINE,
    ]
    assert removed == [["claude", "rm", "b7f2"]]
    data = state.load("demo")
    assert data["handlers"] == {}
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["transcript"]) == ("done", str(transcript))
    assert state.own_transcript(data, data["jobs"]["j1"]) == str(transcript)


def test_a_handlers_done_record_carries_each_post_as_say_returned_it_whatever_the_handler_retyped(
    communicator: str, run: Run, tmp_path: Path, started: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    handler = start(run, tmp_path)
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session", transcript)
    reply = "https://acme.slack.com/archives/D123/p1794479609881679?thread_ts=1794478810.620779&cid=D123"
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": reply, "thread_ts": "1794478810.620779"})
    with monkeypatch.context() as scoped:
        scoped.setenv("CLAUDE_CODE_SESSION_ID", "h-session")
        assert talk.say("demo", {"text": "It copies on the new tree only.", "thread_ts": "1794478810.620779"}) == f"POSTED {reply}"
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: None)
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: started.append(argv) or (json.dumps([{"id": "b7f2", "sessionId": "h-session"}]) if argv[1] == "agents" else ""))
    assert stop(monkeypatch, capsys, "h-session", transcript, "POSTED https://acme.slack.com/archives/D123/p1794479609881679; waits id demo:j1-7") == ""
    assert started[-2][-1] == f"POSTED {reply}; waits id demo:j1-7"


HIS_LINK = "https://acme.slack.com/archives/D123/p1794446196220659?thread_ts=1794440505.483109&cid=D123"
HIS_MESSAGE = f"Message (his words): Why is that only happening now?\nLink: {HIS_LINK}\nThread: D123 1794440505.483109\nDrive: demo\nScratch: {{scratch}}"


def test_one_message_of_his_has_one_handler_at_a_time_whichever_record_names_it(
    communicator: str,
    run: Run,
    tmp_path: Path,
    started: list[list[str]],
    cci_records: dict[str, dict],
    ended_sessions: set[str],
    monkeypatch: pytest.MonkeyPatch,
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
    with pytest.raises(state.DriveError, match=f"handler {handler} is at work on {re.escape(HIS_LINK)}.*; this copy needs no handler of its own"):
        tools.run("handler", "demo", {"brief": message})
    for seq in ("668291", "668300", "668288"):
        with pytest.raises(state.DriveError, match=f"refused, no handler started: handler {handler} is at work on {re.escape(HIS_LINK)}.*its done record names this brief"):
            tools.run("handler", "demo", {"brief": f"Record: {seq} Drive: demo Scratch: {tmp_path / seq}"})
    other = tools.run("handler", "demo", {"brief": f"Record: 668400 Drive: demo Scratch: {tmp_path / '668400'}"}).removeprefix("started ")
    with pytest.raises(state.DriveError, match=f"handler {other} is at work on record 668400.*; this copy needs no handler of its own"):
        tools.run("handler", "demo", {"brief": f"Record: 668400 Drive: demo Scratch: {tmp_path / '668400'}"})
    held = [f"Record: {seq} Drive: demo Scratch: {tmp_path / seq}" for seq in ("668291", "668300", "668288")]
    assert state.load("demo")["handlers"][handler]["held_back"] == held
    assert len([argv for argv in started if argv[:2] == ["claude", "--bg"]]) == 2

    with state.edit("demo") as data:
        data["handlers"][handler]["session"] = "h-session"
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: None)
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: started.append(argv) or (json.dumps([{"id": "b7f2", "sessionId": "h-session"}]) if argv[1] == "agents" else ""))
    agents.end_handler(
        {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "agent_id": handler, "agent_transcript_path": "/x/t.jsonl", "cwd": "/", "handler_session": "h-session", "last_assistant_message": LINE}
    )
    [post] = [argv for argv in started if argv[:2] == ["cci", "post"]]
    assert post[-1] == f"{LINE} | held back for this handler, start each now: {' | '.join(held)}"

    restarted = tools.run("handler", "demo", {"brief": message}).removeprefix("started ")
    with state.edit("demo") as data:
        data["handlers"][restarted]["session"] = "died-session"
    ended_sessions.add("died-session")
    assert tools.run("handler", "demo", {"brief": message}).startswith("started ")
    assert restarted not in state.load("demo")["handlers"]


def test_the_watchs_brief_naming_a_record_as_cci_post_prints_it_holds_the_same_record_as_the_communicators(
    communicator: str, tmp_path: Path, started: list[list[str]], cci_records: dict[str, dict]
) -> None:
    handler = tools.run("handler", "demo", {"brief": f"Record: #680562 Drive: demo Scratch: {tmp_path / 'quiet-#680562'}"}).removeprefix("started ")
    assert state.load("demo")["handlers"][handler]["holds"]["record"] == "680562"
    with pytest.raises(state.DriveError, match=f"handler {handler} is at work on record 680562.*; this copy needs no handler of its own"):
        tools.run("handler", "demo", {"brief": f"Record: 680562 Drive: demo Scratch: {tmp_path / 'handler'}"})
    assert len([argv for argv in started if argv[:2] == ["claude", "--bg"]]) == 1
