import io
import json
import os
from collections.abc import Callable
from pathlib import Path

import agent_brief
import one_line_result
import pytest
import session_start
from drive import agents, reads, state, tools

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
    assert json.loads(argv[argv.index("--settings") + 1])["env"][state.HANDLER_ENV] == f"demo/{handler}"
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
    assert data["handlers"][handler] | {"started": None} == {"session": "h-session", "transcript": str(transcript), "started": None}
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
    run("order", "add", "--project", "The drive", "--workstream", "Answers", "why is this card asking again?", "--kind", "answer")
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
    assert stop(monkeypatch, capsys, "h-session", transcript, LINE) == ""
    assert started[-2] == [
        "cci", "post", "--drive", "demo", "--lane", "handler", "--kind", "done", "--to", "communicator@demo", "--id", f"handler-{handler}", "--text", LINE,
    ]
    assert removed == [["claude", "rm", "b7f2"]]
    data = state.load("demo")
    assert data["handlers"] == {}
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["transcript"]) == ("done", str(transcript))
    assert state.own_transcript(data, data["jobs"]["j1"]) == str(transcript)
