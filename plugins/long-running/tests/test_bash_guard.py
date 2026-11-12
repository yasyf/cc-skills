import io
import json
import plistlib
from collections.abc import Callable
from pathlib import Path

import bash_guard
import pytest
from conftest import SESSION
from drive import github, install, land, orca, settled, state, view
from drive.orca import OrcaError

Run = Callable[..., tuple[int, str]]
RESUMED = "d8677ab9-1e8c-4adb-a3bb-3f661dc45b77"
CREATE = f'orca terminal create --worktree path:/work --title demo-coordinator --command "claude --resume {RESUMED} -n demo-coordinator" --json'
CACHED = "~/.claude/plugins/cache/x/long-running/1.0.1/skills/long-running/reference"
LIVE_PID = orca.live_pid


@pytest.fixture(autouse=True)
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "config"
    (root / "sessions").mkdir(parents=True)
    (root / "plugins").mkdir()
    installs = {"long-running@x": [{"version": "1.0.181", "installPath": str(tmp_path), "lastUpdated": "2026-11-10T00:00:00Z"}]}
    (root / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": installs}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    return root


def bash(command: str, session: str = SESSION, call: dict | None = None, **fields: object) -> dict:
    return {"session_id": session, "tool_name": "Bash", "tool_input": {"command": command, **(call or {})}, "cwd": "/work", **fields}


def running(monkeypatch: pytest.MonkeyPatch, config: Path, pids: dict[int, int]) -> None:
    (config / "sessions" / "4123.json").write_text(json.dumps({"sessionId": RESUMED}))
    monkeypatch.setattr(orca, "parents", lambda: pids)
    monkeypatch.setattr(orca, "live_pid", LIVE_PID)


def test_a_builder_opens_its_pr_the_repositorys_way(demo: str) -> None:
    builder = {"agent_id": "a-1", "agent_type": "long-running:builder"}
    reason = bash_guard.deny_reason(bash("gh pr create --base main --head fix --title X --body-file /s/body.md", **builder))
    assert reason.startswith("refused: a builder opens its PR the repository's way, never a bare `gh pr create`")
    assert "`ccx vcs ship --pr-title-file <title> --pr-body-file <body>`" in reason
    assert bash_guard.deny_reason(bash("ccx vcs ship --no-watch --pr-title-file /s/t --pr-body-file /s/b", **builder)) is None
    assert bash_guard.deny_reason(bash("gh pr create --title X", session="ordinary")) is None


def test_a_builder_opens_no_pr_while_his_open_cards_hold_its_job(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Landing", "ship the watch")
    assert run("job", "add", "builder", "--order", "o1", "--goal", "ship it")[1] == "j1"
    with state.edit(demo) as data:
        data["jobs"]["j1"]["agent"] = "a-1"
    cards = ["demo:j2-1-a"]
    monkeypatch.setattr(view, "live_cards", lambda name: {})
    monkeypatch.setattr(land, "open_cards", lambda data, job_id, waits, live: cards if job_id == "j1" else [])
    builder = {"agent_id": "a-1", "agent_type": "long-running:builder"}
    opening = "cd /work && ccx vcs ship --pr-title-file /s/t --pr-body-file /s/b"
    reason = bash_guard.deny_reason(bash(opening, **builder))
    assert reason.startswith("refused: j1's work waits on his answer to demo:j2-1-a, and this ship would open its PR")
    assert "`ccx vcs ship --no-pr`" in reason
    assert bash_guard.deny_reason(bash("ccx vcs ship --no-pr", **builder)) is None
    assert bash_guard.deny_reason(bash(f"{opening} --dry-run", **builder)) is None
    assert bash_guard.deny_reason(bash(opening, **(builder | {"agent_id": "a-2"}))) is None
    url = "https://github.com/yasyf/cc-skills/pull/9"
    with state.edit(demo) as data:
        data["prs"][state.pr_key(url)] = state.new_pr(url, "j1", "o1")
        data["jobs"]["j1"]["prs"].append(url)
    assert bash_guard.deny_reason(bash(opening, **builder)) is None
    with state.edit(demo) as data:
        data["prs"][state.pr_key(url)]["state"] = "merged"
    assert bash_guard.deny_reason(bash(opening, **builder)).startswith("refused: j1's work waits on his answer")
    cards = []
    assert bash_guard.deny_reason(bash(opening, **builder)) is None

def test_a_designer_opens_no_pr_and_pushes_nothing(demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    designer = {"agent_id": "a-1", "agent_type": "long-running:designer"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bash('gh pr create --title "Design"', **designer))))
    bash_guard.main()
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert output["permissionDecisionReason"].startswith("refused: the designer is read only, so it runs no `gh pr create`")
    assert "`drive job report <your job> -`" in output["permissionDecisionReason"]
    assert "`git push`" in bash_guard.deny_reason(bash("git -C /work push origin lr-design", **designer))
    assert bash_guard.deny_reason(bash("gh pr view 4244", **designer)) is None


def test_a_drive_session_reads_no_skill_page_from_the_plugin_cache(demo: str) -> None:
    reason = bash_guard.deny_reason(bash(f"wc -l {CACHED}/*.md"))
    assert reason.startswith("refused: this reads the skill's pages from long-running 1.0.1's copy in Claude Code's plugin cache")
    assert "1.0.181 is installed now" in reason
    assert "`drive manual <page> <row>`" in reason
    read = {"session_id": SESSION, "agent_id": "a-1", "tool_name": "Read", "tool_input": {"file_path": f"{CACHED}/coordinator.md"}}
    assert bash_guard.deny_reason(read).startswith("refused: this reads the skill's pages")
    assert bash_guard.deny_reason(bash('drive manual coordinator "TAP Approve"')) is None
    assert bash_guard.deny_reason(bash(f"wc -l {CACHED}/*.md", session="elsewhere")) is None
    installed = CACHED.replace("1.0.1", "1.0.181")
    assert bash_guard.deny_reason(bash(f"grep -n Handoff {installed}/landing.md")) is None
    assert bash_guard.deny_reason(bash(f"cat > /s/read.py <<'EOF'\nopen('{installed}/coordinator.md')\nEOF")) is None


def test_a_session_outside_any_drive_and_its_agents_are_not_checked(demo: str) -> None:
    command = 'orca terminal create --worktree path:/work --command "claude; exec zsh -l"'
    assert bash_guard.deny_reason(bash(command, session="ordinary")) is None
    assert bash_guard.deny_reason(bash('drive job resume j3 "go"', session="ordinary", call={"run_in_background": True})) is None
    assert bash_guard.foreground(bash('drive job resume j3 "go"', session="ordinary", call={"run_in_background": True})) is None
    builder = {"agent_id": "a-1", "agent_type": "long-running:builder"}
    assert bash_guard.deny_reason(bash(command, session="ordinary", **builder)).startswith("refused: the terminal's --command runs `zsh` after claude")


def test_claude_codes_permission_prompt_on_a_drive_agents_command_is_refused(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    unreadable = 'tables="a,b"; for t in ${(s:,:)tables}; do echo $t; done'
    builder = {"agent_id": "a-1", "agent_type": "long-running:builder"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bash(unreadable, hook_event_name="PermissionRequest", **builder))))
    bash_guard.main()
    decided = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert decided == {"hookEventName": "PermissionRequest", "decision": {"behavior": "deny", "message": bash_guard.UNSEEN_PROMPT}}
    assert "write the command to a script in your brief's Scratch folder" in bash_guard.UNSEEN_PROMPT
    assert bash_guard.deny_reason(bash(unreadable, hook_event_name="PreToolUse", **builder)) is None
    assert bash_guard.deny_reason(bash(unreadable, hook_event_name="PermissionRequest")) is None
    assert bash_guard.deny_reason(bash(unreadable, session="ordinary", hook_event_name="PermissionRequest", **builder)) == bash_guard.UNSEEN_PROMPT
    elsewhere = {"agent_id": "a-1", "agent_type": "general-purpose"}
    assert bash_guard.deny_reason(bash(unreadable, session="ordinary", hook_event_name="PermissionRequest", **elsewhere)) is None


def test_drive_job_resume_runs_in_the_foreground_however_it_is_called(demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    resume = 'drive job resume j3 "go"'
    foreground = {"command": resume, "run_in_background": False, "timeout": 20000}
    for call in ({"run_in_background": True}, {}, {"timeout": 600000}):
        assert bash_guard.deny_reason(bash(resume, call=call)) is None
        assert bash_guard.foreground(bash(resume, call=call)) == foreground
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bash(f"cd /work && {resume}", call={"run_in_background": True}))))
    bash_guard.main()
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"] == {"hookEventName": "PreToolUse", "updatedInput": foreground | {"command": f"cd /work && {resume}"}}
    assert bash_guard.foreground(bash(resume, call={"timeout": 20000})) is None
    assert bash_guard.foreground(bash("drive job report j3", call={"run_in_background": True})) is None
    builder = {"agent_id": "a-1", "agent_type": "long-running:builder"}
    assert bash_guard.foreground(bash(resume, session="ordinary", call={"run_in_background": True}, **builder)) == foreground


def test_a_session_is_resumed_only_once_its_process_exits(demo: str, config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    running(monkeypatch, config, {4123: 1})
    reason = bash_guard.deny_reason(bash(CREATE))
    assert reason.startswith(f"refused: session {RESUMED} still runs as pid 4123, and a resume while it runs copies it")
    assert "wait until `ps -p 4123` lists nothing" in reason
    assert bash_guard.deny_reason(bash(CREATE.replace("-n demo", "--fork-session -n demo"))) is None
    running(monkeypatch, config, {})
    assert bash_guard.deny_reason(bash(CREATE)) is None


def test_a_role_whose_terminal_orca_only_hides_is_shown_never_resumed(communicator: str, config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(communicator) as data:
        data["roles"]["coordinator"].update(session=RESUMED, terminal="term_c", cwd="/work")
    monkeypatch.setattr(orca, "terminal", lambda handle: {"orphaned": True})
    monkeypatch.setattr(orca, "main_checkout", lambda worktree: Path("/repo"))
    running(monkeypatch, config, {4123: 1})
    reason = bash_guard.deny_reason(bash(CREATE))
    assert reason.startswith(f"refused: session {RESUMED} still runs as pid 4123, in a terminal Orca only hides")
    assert "`orca repo set --repo path:/repo --external-worktree-visibility show`" in reason

    def gone(handle: str) -> dict:
        raise OrcaError(f"no terminal {handle}")

    monkeypatch.setattr(orca, "terminal", gone)
    assert "wait until `ps -p 4123` lists nothing" in bash_guard.deny_reason(bash(CREATE))
    running(monkeypatch, config, {})
    assert bash_guard.deny_reason(bash(CREATE)) is None


def test_a_sub_coordinators_session_is_found_like_a_roles_by_its_session_and_terminal(
    communicator: str, config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit(communicator) as data:
        top = data["roles"]["coordinator"]
        data["subcoordinators"]["Iris"] = {"session": RESUMED, "terminal": "term_i", "cwd": "/work", "started": top["started"], "from": 0}
    monkeypatch.setattr(orca, "terminal", lambda handle: {"orphaned": True})
    monkeypatch.setattr(orca, "main_checkout", lambda worktree: Path("/repo"))
    running(monkeypatch, config, {4123: 1})
    assert bash_guard.deny_reason(bash(CREATE)).startswith(f"refused: session {RESUMED} still runs as pid 4123, in a terminal Orca only hides")


def test_a_terminal_command_ends_with_the_claude_command(demo: str, live_sessions: dict[str, int | None]) -> None:
    live_sessions["x"] = None
    reason = bash_guard.deny_reason(bash('orca terminal create --worktree path:/work --command "claude --resume x; exec zsh -l"'))
    assert reason.startswith("refused: the terminal's --command runs `zsh` after claude")
    assert bash_guard.deny_reason(bash('orca terminal create --worktree path:/work --command "claude --resume x"')) is None


def test_no_session_is_moved_while_an_incident_order_is_open(demo: str, run: Run) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Incidents", "the API is down")
    with state.edit(demo) as data:
        data["orders"]["o1"]["incident"] = True
        data["roles"]["coordinator"]["terminal"] = "term_c"
    send = "orca terminal send --terminal term_c --text /exit --enter"
    reason = bash_guard.deny_reason(bash(send))
    assert reason.startswith("refused: o1 is an open incident order, and ending the coordinator now stops it mid-incident")
    assert "`drive status o1` reads done" in reason
    assert bash_guard.deny_reason(bash("orca terminal send --terminal term_other --text /exit --enter")) is None
    with state.edit(demo) as data:
        data["orders"]["o1"]["state"] = "done"
    assert bash_guard.deny_reason(bash(send)) is None


def test_a_drive_call_retrying_past_a_failing_check_is_refused_naming_the_log_read(demo: str) -> None:
    for command, step in (
        ("cd /work && .agents/skills/submit-pr/scripts/stack-enqueue 4244 --flake", "stack-enqueue --flake"),
        ("tools/ci bk rerun 0190a1b2-job", "ci bk rerun"),
        ("gh run rerun 123 --failed -R yasyf/cc-skills", "gh run rerun"),
    ):
        reason = bash_guard.deny_reason(bash(command))
        assert reason.startswith(f"refused: `{step}` retries past a failing check, and a check that fails is fixed at its cause")
        assert "`tools/ci bk log <job>`" in reason
    assert bash_guard.deny_reason(bash("tools/ci bk log 0190a1b2-job")) is None
    assert bash_guard.deny_reason(bash(".agents/skills/submit-pr/scripts/stack-enqueue 4244")) is None
    assert bash_guard.deny_reason(bash("tools/ci bk rerun 0190a1b2-job", session="ordinary")) is None


HELPER_CARD = "Captain-hook update, your Mac: install {} now? It restarts the hook helper once"
HELPER_OPTIONS = "Options: Install now (recommended) | Installs never need me | Not now"


def holding(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, release: str) -> Path:
    app = tmp_path / "Captain Hook.app" / "Contents" / "Info.plist"
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_bytes(plistlib.dumps({"CFBundleShortVersionString": release}))
    monkeypatch.setattr(install, "APP_PLIST", app)
    return app


def test_a_hook_helper_install_runs_only_on_his_install_now_for_the_newest_release_once(demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    helper = {"newest": "v12.121.70"}
    answers = [{"id": "1352576", "title": HELPER_CARD.format("12.121.63"), "body": f"Install now: the misfire stops for every session on this Mac.\n{HELPER_OPTIONS}", "tags": []}]
    monkeypatch.setattr(github, "read", lambda what, cmd, max_age="0s", text=True: f"{helper['newest']}\n")
    monkeypatch.setattr(settled, "ccn", lambda argv, text, cwd, failure: json.dumps(answers))
    holding(monkeypatch, tmp_path, "12.121.69")
    reason = bash_guard.deny_reason(bash("uvx capt-hook helper install"))
    assert reason.startswith("refused: `capt-hook helper install` installs captain-hook 12.121.70, the newest release, and restarts the hook helper")
    assert f'"{HELPER_CARD.format("12.121.70")}"' in reason
    answers.append({"id": "a1", "title": HELPER_CARD.format("12.121.70"), "body": f"Not now\n{HELPER_OPTIONS}", "tags": []})
    assert bash_guard.deny_reason(bash("capt-hook helper install")).startswith("refused: `capt-hook helper install` installs captain-hook 12.121.70")
    answers.append({"id": "a2", "title": HELPER_CARD.format("12.121.70"), "body": f"Install now\n{HELPER_OPTIONS}", "tags": []})
    assert bash_guard.deny_reason(bash("uvx --from capt-hook capt-hook helper install")) is None
    holding(monkeypatch, tmp_path, "12.121.70")
    assert bash_guard.deny_reason(bash("capt-hook helper install")).startswith("refused: Captain Hook.app already holds captain-hook 12.121.70")
    assert bash_guard.deny_reason(bash("capt-hook helper status")) is None


def test_any_sessions_hook_helper_restart_waits_for_his_install_now_tap_on_the_card_naming_its_release(demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    answers: list[dict] = []
    monkeypatch.setattr(github, "read", lambda what, cmd, max_age="0s", text=True: "v12.121.70\n")
    monkeypatch.setattr(settled, "ccn", lambda argv, text, cwd, failure: json.dumps(answers))
    holding(monkeypatch, tmp_path, "12.121.68")
    with state.edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "ship", [], state.stamp()) | {"questions": [state.new_question("/asks/j1-helper.md", HELPER_CARD.format("12.121.70"), state.stamp())]}
    restarts = ["capt-hook helper install", "brew upgrade yasyf/tap/captain-hook", "brew reinstall --cask captain-hook", "brew upgrade"]
    for command, step in zip(restarts, ["`capt-hook helper install`", "`brew upgrade` of captain-hook", "`brew reinstall` of captain-hook", "`brew upgrade` of captain-hook"], strict=True):
        reason = bash_guard.deny_reason(bash(command, session="ordinary"))
        assert reason.startswith(f"refused: {step} installs captain-hook 12.121.70, the newest release, and restarts the hook helper")
        assert "no answer of his picks Install now on his card /asks/j1-helper.md" in reason
    assert bash_guard.deny_reason(bash("brew upgrade yasyf/tap/ccx", session="ordinary")) is None
    answers.append({"id": "a2", "title": HELPER_CARD.format("12.121.70"), "body": f"Install now\n{HELPER_OPTIONS}", "tags": []})
    assert [bash_guard.deny_reason(bash(command, session="ordinary")) for command in restarts] == [None] * len(restarts)
