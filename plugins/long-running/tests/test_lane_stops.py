import io
import json
from datetime import UTC, datetime
from pathlib import Path

import one_line_result
import pytest
from conftest import LANE_SPEC, Thread
from drive import agents, install, reads, slack, state, talk

THREAD = "1794200000.000100"
QUESTION_LINK = f"https://acme.slack.com/archives/D123/p1794200100000100?thread_ts={THREAD}&cid=D123"
API_ERROR = "Please run /login · API Error: 403 Attention Required! | Cloudflare"
EARLIER = "2026-11-13T09:46:00.000Z"
DIED = {
    "type": "assistant",
    "timestamp": EARLIER,
    "isApiErrorMessage": True,
    "error": "authentication_failed",
    "message": {"role": "assistant", "model": agents.SYNTHETIC, "stop_reason": "stop_sequence", "content": [{"type": "text", "text": API_ERROR}]},
}
NOTICE = "API Error: Opus 5.5's safeguards flagged this message (https://www.anthropic.com/legal/aup). This sometimes happens with safe, normal conversations."
SAFEGUARDED = {
    "type": "assistant",
    "timestamp": EARLIER,
    "isApiErrorMessage": True,
    "error": "invalid_request",
    "message": {"role": "assistant", "model": agents.SYNTHETIC, "stop_reason": "refusal", "content": [{"type": "text", "text": NOTICE}]},
}


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    argvs: list[list[str]] = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: argvs.append(argv) or "")
    return argvs


def failed(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], session: str, transcript: Path, rows: tuple[dict, ...]) -> tuple[int, str]:
    transcript.write_text("".join(json.dumps(row) + "\n" for row in [{"type": "user", "timestamp": EARLIER, "message": {"role": "user", "content": "Goal: x"}}, *rows]))
    payload = {"session_id": session, "hook_event_name": "StopFailure", "transcript_path": str(transcript), "cwd": str(transcript.parent), "stop_hook_active": False, "last_assistant_message": API_ERROR}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    try:
        one_line_result.main()
    except SystemExit as exit:
        return exit.code, capsys.readouterr().err
    return 0, capsys.readouterr().out


def test_the_watchs_own_live_line_of_a_proved_order_posts_through_say_from_no_session_and_closes_its_order_on_its_report(
    communicator: str, run, started: list[list[str]], slack_thread: Thread, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Copy fix", "--project", "The drive", "--workstream", "Copies", "fix the copy", "--from", f"https://acme.slack.com/archives/D123/p{THREAD.replace('.', '')}")
    kept = {"id": "install-proved-j1", "kind": "report", "to": ["communicator@demo"], "topic": "install-proved-o1", "attr": {"close": "o1", "thread": THREAD}, "text": "POST Copy fix is live in long-running 1.0.3", "body": "Fixed: Copy fix\n"}
    with state.edit("demo") as data:
        data["orders"]["o1"] |= {"close_report": kept | {"seq": "700002"}, "closed_on": "700002"}
    report = {"seq": 700002, "kind": "report", "lane": "watch", "at": state.stamp(), "fields": {"attrs": {"close": "o1", "thread": THREAD}}}
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: started.append(argv) or (json.dumps(report) if argv[1] == "show" else ""))
    live = slack_thread.post(state.now(), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": live, "thread_ts": THREAD})
    monkeypatch.setattr(talk, "hook_refusal", lambda name, data, post: None)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    line = talk.proved_line("demo", {"text": "Copy fix is live in long-running 1.0.3.", "record": "700002", "thread_ts": THREAD})
    assert (line.startswith(f"POSTED {live}; o1 "), line.endswith("; o1 closed on report 700002")) == (True, True)
    data = state.load("demo")
    assert (data["orders"]["o1"]["state"], data["orders"]["o1"]["closed_on"], data["carried"]["700002"]) == ("done", "700002", live)


@pytest.mark.parametrize("asked", [QUESTION_LINK, "1794200100.000100", " did the copy  fix ship? "])
def test_a_post_whose_asked_names_his_message_that_started_an_answer_order_closes_it(
    asked: str, communicator: str, run, started: list[list[str]], slack_thread: Thread, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _ = run("order", "add", "did the copy fix ship?", "--from", QUESTION_LINK, "--kind", "answer", "--complex", "--title", "Copy fix shipped", "--project", "The drive", "--workstream", "Copies")
    assert code == 0
    answer = slack_thread.post(state.now(), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": answer, "thread_ts": THREAD})
    report = {"seq": 700001, "kind": "report", "lane": "communicator", "at": state.stamp(), "fields": {"attrs": {"close": "o1"}}}
    replies = {"post": json.dumps({"seq": 700001}), "show": json.dumps(report)}
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: started.append(argv) or replies.get(argv[1], ""))
    line = talk.say("demo", {"text": "It shipped in 1.0.540.", "thread_ts": THREAD, "asked": [asked]})
    assert line == f'POSTED {answer}; o1 "Copy fix shipped": posted {answer}; o1 closed on report 700001'
    assert ["cci", "post", "--drive", "demo", "--lane", "communicator", "--kind", "report", "--attr", "close=o1", "--json", "--text", "It shipped in 1.0.540."] in started
    assert state.load("demo")["orders"]["o1"]["state"] == "done"


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
    assert failed(monkeypatch, capsys, "lane-session", transcript, (DIED,)) == ((2, f"{agents.RESUMED}\n") if resumed else (0, ""))
    assert waits == ([60] if lane else [])


def test_an_orca_lane_a_models_safeguards_stopped_is_never_resumed(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(state.REMOTE_LANE_ENV, "1")
    monkeypatch.setattr(agents.time, "sleep", lambda seconds: pytest.fail("a safeguards stop waits on no resume"))
    assert failed(monkeypatch, capsys, "lane-session", tmp_path / "lane.jsonl", (SAFEGUARDED,)) == (0, "")


def test_a_lane_session_the_drive_starts_on_this_mac_runs_with_no_advisor_at_high_effort_on_the_lane_rules_in_place_of_the_users_claude_md(communicator: str) -> None:
    settings, flags = agents.lane_session({state.DRIVE_ENV: "demo"}, agents.BUILDER, Path("/code/monorepo"))
    lane = json.loads(LANE_SPEC.read_text())
    assert (flags, settings["env"][agents.COMPACT_ENV], settings["env"]["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"]) == ((*agents.LANE_RULES_ARGS, "--strict-mcp-config", "--effort", "high"), lane["env"][agents.COMPACT_ENV], "1")
    assert (settings["claudeMdExcludes"], settings["env"]["CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"]) == ([str(install.config_dir() / "CLAUDE.md")], "1")
    assert {key for key, on in settings["enabledPlugins"].items() if on} == {"long-running@skills", "captain-hook@captain-hook", "open-pr@skills"}
    assert (Path(agents.LANE_RULES_ARGS[1]) / "CLAUDE.md").is_file()
