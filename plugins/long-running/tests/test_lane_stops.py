import io
import json
from datetime import UTC, datetime
from pathlib import Path

import one_line_result
import pytest
from conftest import LANE_SPEC, Thread, edit, own_time
from drive import agents, answer, install, reads, slack, state, talk

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


def test_the_watchs_own_live_line_of_a_proved_order_posts_through_report_line_from_no_session_and_closes_its_order_on_its_report(
    communicator: str, run, started: list[list[str]], slack_thread: Thread, cci_records: dict[str, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Copy fix", "--project", "The drive", "--workstream", "Copies", "fix the copy", "--from", f"https://acme.slack.com/archives/D123/p{THREAD.replace('.', '')}")
    kept = {"id": "install-proved-j1", "kind": "report", "to": ["communicator@demo"], "topic": "install-proved-o1", "attr": {"close": "o1", "thread": THREAD}, "text": "POST Copy fix is live in long-running 1.0.3", "body": "Fixed: Copy fix\n"}
    with edit("demo") as data:
        data["orders"]["o1"] |= {"close_report": kept | {"seq": "700002"}, "closed_on": "700002"}
    report = {"seq": 700002, "kind": "report", "lane": "watch", "at": state.stamp(), "text": kept["text"], "refs": {}, "fields": {"attrs": {"close": "o1", "thread": THREAD}}}
    cci_records["700002"] = report
    monkeypatch.setattr(reads, "run", lambda argv, checkout, env=None: started.append(argv) or (json.dumps(report) if argv[1] == "show" else ""))
    live = slack_thread.post(state.now(), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": live, "thread_ts": THREAD})
    asked: list[str] = []
    monkeypatch.setattr(answer, "ask", lambda rows, words, system, tag: asked.append(rows) or (json.dumps({"text": "Copy fix is live in long-running 1.0.3.", "place": "thread"}), {}))
    monkeypatch.setattr(slack, "post_findings", lambda text, draws_card: [])
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    line = talk.report_line("demo", "700002")
    assert (line.startswith(f"POSTED {live}; o1 "), line.endswith("; o1 closed on report 700002")) == (True, True)
    assert asked[0].splitlines()[:2] == ["Report 700002, a report:", "Copy fix is live in long-running 1.0.3"]
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

    own_time(monkeypatch, agents, sleep=wait)
    assert failed(monkeypatch, capsys, "lane-session", transcript, (DIED,)) == ((2, f"{agents.RESUMED}\n") if resumed else (0, ""))
    assert waits == ([60] if lane else [])


def test_an_orca_lane_a_models_safeguards_stopped_is_never_resumed(demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(state.REMOTE_LANE_ENV, "1")
    own_time(monkeypatch, agents, sleep=lambda seconds: pytest.fail("a safeguards stop waits on no resume"))
    assert failed(monkeypatch, capsys, "lane-session", tmp_path / "lane.jsonl", (SAFEGUARDED,)) == (0, "")


def test_a_lane_session_the_drive_starts_on_this_mac_runs_with_an_opus_advisor_at_high_effort_on_the_lane_rules_in_place_of_the_users_claude_md(communicator: str) -> None:
    settings, flags = agents.lane_session({state.DRIVE_ENV: "demo"}, agents.BUILDER, Path("/code/monorepo"))
    lane = json.loads(LANE_SPEC.read_text())
    assert (flags, settings["env"][agents.COMPACT_ENV], "CLAUDE_CODE_DISABLE_ADVISOR_TOOL" in settings["env"]) == ((*agents.LANE_RULES_ARGS, "--strict-mcp-config", "--effort", "high", "--advisor", "opus"), lane["env"][agents.COMPACT_ENV], False)
    assert (settings["claudeMdExcludes"], settings["env"]["CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"]) == ([str(install.config_dir() / "CLAUDE.md")], "1")
    assert {key for key, on in settings["enabledPlugins"].items() if on} == {"long-running@skills", "captain-hook@captain-hook", "open-pr@skills"}
    assert (Path(agents.LANE_RULES_ARGS[1]) / "CLAUDE.md").is_file()


def test_a_builder_lists_each_checkout_skill_it_never_uses_by_name_alone_and_a_watchdog_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    for skill in ("submit-pr", "break-glass"):
        (tmp_path / ".claude" / "skills" / skill).mkdir(parents=True)
        (tmp_path / ".claude" / "skills" / skill / "SKILL.md").write_text(f"---\nname: {skill}\ndescription: d\n---\n")
    (tmp_path / ".claude" / "commands").mkdir()
    (tmp_path / ".claude" / "commands" / "fde.md").write_text("tips")
    builder = agents.light({"env": {}, "enabledPlugins": {}}, agents.BUILDER, tmp_path)
    watchdog = agents.light({"env": {}, "enabledPlugins": {}}, agents.WATCHDOG, tmp_path)
    assert (builder["skillOverrides"], builder["skillListingMaxDescChars"]) == ({"break-glass": "name-only", "fde": "name-only"}, agents.LISTED_CHARS)
    assert watchdog["skillOverrides"] == {"break-glass": "off", "fde": "off", "submit-pr": "off"}


def test_a_sprite_builder_starts_on_a_builders_plugins_and_skill_listing_with_none_of_this_macs_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / ".claude" / "commands").mkdir(parents=True)
    (tmp_path / ".claude" / "commands" / "fde.md").write_text("tips")
    builder = agents.light({"env": {}, "enabledPlugins": {}}, agents.BUILDER, tmp_path)
    assert agents.sprite_lane(tmp_path) == {key: value for key, value in builder.items() if key not in ("env", "claudeMdExcludes")}
    assert agents.sprite_lane(tmp_path)["skillOverrides"] == {"fde": "name-only"}

