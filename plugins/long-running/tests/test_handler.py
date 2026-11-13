import io
import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import agent_brief
import handed_records
import one_line_result
import pytest
import session_start
from conftest import LANE_SPEC, Thread
from drive import agents, reads, slack, state, talk, tools

Run = Callable[..., tuple[int, str]]
HIS_BRIEF = "Message (his words): why is this card asking again?\nThread: D123 1794380463.481959\nDrive: demo\nScratch: {scratch}\nDeliverable: his answer, in his thread"
LINE = "POSTED https://acme.slack.com/archives/D123/p1794380463481960"
BACKGROUND_JOB = "b7f2e0aa"
GONE = agents.gone


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


def stop(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], session: str, transcript: Path, line: str, brief: str = HIS_BRIEF, event: str = "Stop"
) -> str:
    transcript.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": brief}}) + "\n")
    payload = {
        "session_id": session,
        "agent_type": agents.HANDLER,
        "hook_event_name": event,
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
    code, out = run("handler", QUESTION_BRIEF.format(scratch=tmp_path / "h2"), "--answer", "Copy fix shipped", "--project", "The drive", "--workstream", "Copies")
    assert code == 0 and posted[-1][-1].startswith(f"BLOCKED handler {out.removeprefix('started ')} did not start: claude --bg: workspace not trusted")
    assert run("handler", QUESTION_BRIEF.format(scratch=tmp_path / "h3"), "--answer", "Copy fix shipped")[0] == 2
    assert run("handler", HIS_BRIEF.format(scratch=tmp_path / "h4"), "--answer", "Copy fix shipped", "--project", "The drive", "--workstream", "Copies")[0] == 2
    data = state.load("demo")
    assert (data["handlers"], data["orders"], data["jobs"]) == ({}, {}, {})


THREAD = "1794200000.000100"
QUESTION_LINK = f"https://acme.slack.com/archives/D123/p1794200100000100?thread_ts={THREAD}&cid=D123"
QUESTION_BRIEF = f"Message (his words): did the copy fix ship?\nLink: {QUESTION_LINK}\nThread: D123 {THREAD}\nDrive: demo\nScratch: {{scratch}}"


def test_his_read_only_questions_handler_starts_on_its_answer_order_and_one_say_answers_and_closes_it(
    communicator: str, run: Run, tmp_path: Path, started: list[list[str]], slack_thread: Thread, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Copy fix", "--project", "The drive", "--workstream", "Copies", "fix the copy", "--from", f"https://acme.slack.com/archives/D123/p{THREAD.replace('.', '')}")
    run("job", "add", "reader", "--order", "o1", "--goal", "trace the copy")
    run("order", "add", "--title", "Run page", "--project", "The drive", "--workstream", "Runs", "show a run's history", "--from", "https://acme.slack.com/archives/D123/p1794100000000100")
    brief = QUESTION_BRIEF.format(scratch=tmp_path / "h1")
    handler = tools.run("handler", "demo", {"brief": brief, "answer": "Copy fix shipped", "project": "The drive", "workstream": "Copies"}).removeprefix("started ")
    data = state.load("demo")
    order, job = data["orders"]["o3"], data["jobs"]["j2"]
    assert (order["text"], order["kind"], order["owner"], order["thread"], order["title"]) == ("did the copy fix ship?", "answer", "communicator", ["D123", THREAD], "Copy fix shipped")
    assert (job["kind"], job["order"], job["agent"], job["state"]) == ("reader", "o3", handler, "running")
    [argv] = started
    prompt = argv[-1]
    assert prompt.startswith(f"{brief}\nOrder: o3, job j2\nIn his thread:\n- o1 ")
    assert "  - j1 " in prompt and "o2" not in prompt and agents.beyond_brief(prompt) is None

    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session", transcript)
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
        line = talk.say("demo", {"text": text, "thread_ts": THREAD, "order": "o3", "close": True})
    result = f"POSTED {answer}"
    assert line == f'{result}; o3 "did the copy fix ship?": posted {answer}; o3 closed on report 700001; j2 keeps {result} as its return, the line you end on'
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
    bind(monkeypatch, handler, "h-session", transcript)
    data = state.load("demo")
    holds = {"message": None, "link": None, "thread": "1794380463.481959", "record": None, "re": []}
    assert data["handlers"][handler] | {"started": None} == {"session": "h-session", "transcript": str(transcript), "started": None, "holds": holds, "handed": [], "took": [], "posted": []}
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
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: started.append(argv) or "")
    monkeypatch.setattr(agents, "background_job", lambda session: (BACKGROUND_JOB, {"sessionId": session}) if session == "h-session" else None)
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{LINE}\n"))
    assert run("job", "report", "j1", "-")[0] == 0
    assert stop(monkeypatch, capsys, "h-session", transcript, LINE) == ""
    assert started[-1] == [
        "cci", "post", "--drive", "demo", "--lane", "handler", "--kind", "done", "--to", "communicator@demo", "--id", f"handler-{handler}", "--text", LINE,
    ]
    assert removed == [["claude", "rm", BACKGROUND_JOB]]
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
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: started.append(argv) or "")
    monkeypatch.setattr(agents, "background_job", lambda session: (BACKGROUND_JOB, {"sessionId": session}))
    assert stop(monkeypatch, capsys, "h-session", transcript, "POSTED https://acme.slack.com/archives/D123/p1794479609881679; waits id demo:j1-7") == ""
    assert started[-1][-1] == f"POSTED {reply}; waits id demo:j1-7"


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
    with pytest.raises(state.DriveError, match=f"handler {handler} is at work on {re.escape(HIS_LINK)}.*; this copy needs no handler of its own"):
        tools.run("handler", "demo", {"brief": message})
    handed = [f"Record: {seq} Drive: demo Scratch: {tmp_path / seq}" for seq in ("668291", "668300", "668288")]
    assert [tools.run("handler", "demo", {"brief": brief}) for brief in handed] == [f"handed to {handler}"] * 3
    other = tools.run("handler", "demo", {"brief": f"Record: 668400 Drive: demo Scratch: {tmp_path / '668400'}"}).removeprefix("started ")
    with pytest.raises(state.DriveError, match=f"handler {other} is at work on record 668400.*; this copy needs no handler of its own"):
        tools.run("handler", "demo", {"brief": f"Record: 668400 Drive: demo Scratch: {tmp_path / '668400'}"})
    assert len([argv for argv in started if argv[:2] == ["claude", "--bg"]]) == 2

    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session", transcript)
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


def test_the_watchs_brief_naming_a_record_as_cci_post_prints_it_holds_the_same_record_as_the_communicators(
    communicator: str, tmp_path: Path, started: list[list[str]], cci_records: dict[str, dict]
) -> None:
    handler = tools.run("handler", "demo", {"brief": f"Record: #680562 Drive: demo Scratch: {tmp_path / 'quiet-#680562'}"}).removeprefix("started ")
    assert state.load("demo")["handlers"][handler]["holds"]["record"] == "680562"
    with pytest.raises(state.DriveError, match=f"handler {handler} is at work on record 680562.*; this copy needs no handler of its own"):
        tools.run("handler", "demo", {"brief": f"Record: 680562 Drive: demo Scratch: {tmp_path / 'handler'}"})
    assert len([argv for argv in started if argv[:2] == ["claude", "--bg"]]) == 1


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
}
WATCH_QUIET = "Record: #678142 Drive: demo Scratch: {scratch}/quiet-#678142"
COMMUNICATOR_QUIET = (
    "Record: 678142\nMessage (his words): none\nLink: none\nThread: 1794494004.895129\nDrive: demo\nPaths: none\nScratch: {scratch}/handler"
    "\nDeliverable: read incident o761's order and post where it stands now\nContext: watch-quiet report\nWaits: none"
)


def brief_of(seq: str) -> str:
    return f"Record: {seq} Drive: demo Scratch: {{scratch}}/communicator"


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
    ],
)
def test_a_record_in_a_live_handlers_thread_or_on_its_re_chain_is_handed_to_it_whichever_session_briefed_either_and_a_copy_is_refused(
    communicator: str, tmp_path: Path, started: list[list[str]], cci_records: dict[str, dict], first: str, then: str, met: str
) -> None:
    cci_records |= {seq: {"seq": int(seq), "kind": kept["kind"], "lane": "root", "re": kept.get("re"), "fields": {"attrs": kept["attrs"]}} for seq, kept in LIVE_RECORDS.items()}
    handler = tools.run("handler", "demo", {"brief": first.format(scratch=tmp_path)}).removeprefix("started ")
    if met == "copy":
        with pytest.raises(state.DriveError, match=f"handler {handler} is at work on record 678142; this copy needs no handler of its own"):
            tools.run("handler", "demo", {"brief": then.format(scratch=tmp_path)})
    else:
        assert tools.run("handler", "demo", {"brief": then.format(scratch=tmp_path)}).startswith(f"handed to {handler}" if met == "handed" else "started ")
    handlers = state.load("demo")["handlers"]
    assert len(handlers) == len([argv for argv in started if argv[:2] == ["claude", "--bg"]]) == (2 if met == "started" else 1)
    assert handlers[handler]["handed"] == ([then.format(scratch=tmp_path)] if met == "handed" else [])


def test_a_handler_is_gone_once_no_running_process_claude_code_keeps_names_its_session_at_work_whatever_its_job_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "claude"
    (config / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    spare = subprocess.Popen(["sleep", "60"])
    for pid, session, status in ((os.getpid(), "busy", "busy"), (spare.pid, "idle", "idle"), (1, "died", "busy")):
        (config / "sessions" / f"{pid}.json").write_text(json.dumps({"sessionId": session, "kind": "bg", "status": status}))
    jobs = {
        "a1b2c3d4": {"sessionId": "idle", "state": "working", "tempo": "idle"},
        "c3d4e5f6": {"sessionId": "blocked", "state": "blocked", "tempo": "blocked"},
    }
    for short, kept in jobs.items():
        (config / "jobs" / short).mkdir(parents=True)
        (config / "jobs" / short / "state.json").write_text(json.dumps(kept))
    try:
        assert {session: GONE(session) for session in ("busy", "idle", "died", "blocked", "unknown")} == {"busy": False, "idle": True, "died": True, "blocked": True, "unknown": True}
    finally:
        spare.kill()
    assert GONE(None) is False
    assert agents.background_job("blocked") == ("c3d4e5f6", jobs["c3d4e5f6"])


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
    bind(monkeypatch, handler, "h-session", transcript)
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


API_ERROR = "Please run /login · API Error: 403 Attention Required! | Cloudflare"
HIS_DM = "https://poetic-technology.slack.com/archives/D0C4HQWPLCC"
ANSWER_THREAD = "1794565600.000100"


def record_brief(seq: str) -> str:
    return f"Record: {seq} Drive: demo Scratch: {{scratch}}"


def his_brief(words: str, message: str, thread: str | None = None) -> str:
    link = f"{HIS_DM}/p{message.replace('.', '')}" + (f"?thread_ts={thread}&cid=D0C4HQWPLCC" if thread else "")
    return f"Message (his words): {words}\nLink: {link}\nThread: D0C4HQWPLCC {thread or message}\nDrive: demo\nScratch: {{scratch}}"


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
def test_a_handler_whose_turn_an_api_error_ended_starts_again_at_once_as_itself_on_its_brief_and_posts_one_done_once_a_turn_answers(
    communicator: str, tmp_path: Path, started: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], brief: str, posted_first: bool
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
        bind(monkeypatch, handler, session, transcript)
        if posted_first and attempt == 1:
            posts.append(said(monkeypatch, session, f"{HIS_DM}/p1794565600000200?thread_ts={ANSWER_THREAD}&cid=D0C4HQWPLCC"))
        assert stop(monkeypatch, capsys, session, transcript, API_ERROR, brief, "StopFailure") == ""
        starts = [argv for argv in started if argv[:2] == ["claude", "--bg"]]
        assert [argv[-1] for argv in starts] == [brief] * (attempt + 1)
        assert json.loads(starts[-1][starts[-1].index("--settings") + 1])["env"][state.HANDLER_ENV] == f"demo/{handler}"
        entry = state.load("demo")["handlers"][handler]
        assert (entry["session"], entry["holds"], entry["posted"]) == (None, held, posts)
        assert removed == [["claude", "rm", BACKGROUND_JOB]] * attempt
    assert agents.brief_path("demo", handler).read_text() == brief
    assert not [argv for argv in started if "done" in argv]

    transcript = tmp_path / "h-answered.jsonl"
    bind(monkeypatch, handler, "h-answered", transcript)
    posts.append(said(monkeypatch, "h-answered", f"{HIS_DM}/p1794565600000300?thread_ts={ANSWER_THREAD}&cid=D0C4HQWPLCC"))
    assert stop(monkeypatch, capsys, "h-answered", transcript, posts[-1], brief) == ""
    [done] = [argv for argv in started if "done" in argv]
    assert done[-3:] == [f"handler-{handler}", "--text", "; ".join(posts)]
    assert handler not in state.load("demo")["handlers"]


def test_a_handler_an_api_error_ended_is_handed_again_each_brief_it_took_and_no_copy_takes_what_it_holds_meanwhile(
    communicator: str, tmp_path: Path, started: list[list[str]], cci_records: dict[str, dict], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cci_records |= {seq: {"seq": int(seq), "kind": "report", "lane": "root", "fields": {"attrs": {"thread": "1794557485.395319"}}} for seq in ("690458", "690461", "690475")}
    briefs = {seq: f"Record: {seq} Drive: demo Scratch: {tmp_path / seq}" for seq in cci_records}
    handler = tools.run("handler", "demo", {"brief": briefs["690458"]}).removeprefix("started ")
    assert tools.run("handler", "demo", {"brief": briefs["690461"]}) == f"handed to {handler}"
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session", transcript)
    capsys.readouterr()
    handed_records.main()
    assert briefs["690461"] in capsys.readouterr().out
    assert tools.run("handler", "demo", {"brief": briefs["690475"]}) == f"handed to {handler}"
    monkeypatch.setattr(agents.subprocess, "Popen", lambda argv, **kwargs: None)
    monkeypatch.setattr(agents, "background_job", lambda session: (BACKGROUND_JOB, {"sessionId": session}))
    assert stop(monkeypatch, capsys, "h-session", transcript, API_ERROR, briefs["690458"], "StopFailure") == ""
    entry = state.load("demo")["handlers"][handler]
    assert (entry["handed"], entry["took"]) == ([briefs["690461"], briefs["690475"]], [])
    with pytest.raises(state.DriveError, match=f"handler {handler} is at work on record 690458"):
        tools.run("handler", "demo", {"brief": briefs["690458"]})


def test_a_handler_whose_stop_check_raises_posts_its_done_before_the_error_goes_on(
    communicator: str, run: Run, tmp_path: Path, started: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    handler = start(run, tmp_path)
    transcript = tmp_path / "h-session.jsonl"
    bind(monkeypatch, handler, "h-session", transcript)
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
