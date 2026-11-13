import io
import json
import plistlib
import subprocess
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

def test_a_sprite_lanes_ship_asks_the_coordinators_mac_whether_his_open_cards_hold_its_pr(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = []
    answer = {"code": 0, "out": json.dumps({"id": "j7", "cards": ["demo:j2-1-a"]}), "err": ""}

    def drive(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        asked.append(argv)
        return subprocess.CompletedProcess(argv, answer["code"], stdout=answer["out"], stderr=answer["err"])

    monkeypatch.setattr(land.subprocess, "run", drive)
    opening = "ccx vcs ship --pr-title-file /s/t --pr-body-file /s/b"
    assert bash_guard.deny_reason(bash(opening, session="sprite")) is None
    monkeypatch.setenv(state.REMOTE_LANE_ENV, "1")
    assert bash_guard.deny_reason(bash(opening, session="sprite")).startswith("refused: j7's work waits on his answer to demo:j2-1-a, and this ship would open its PR")
    assert asked == [["drive", "job", "held", "--json"]]
    assert bash_guard.deny_reason(bash("ccx vcs ship --no-pr", session="sprite")) is None
    assert bash_guard.deny_reason(bash("git status", session="sprite")) is None
    assert len(asked) == 1
    answer.update(code=1, out="", err="drive: orca orchestration ask exited 1: no run")
    assert bash_guard.deny_reason(bash(opening, session="sprite")) == (
        "refused: `drive job held` could not say whether his open cards hold this lane's PR: drive: orca orchestration ask exited 1: no run; ship again once it answers"
    )
    answer.update(code=0, out=json.dumps({"id": "j7", "cards": []}))
    assert bash_guard.deny_reason(bash(opening, session="sprite")) is None

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


def test_claude_codes_permission_prompt_in_an_orca_lane_is_refused(demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """j2871's read-only sqlite3 loop, which Claude Code's dangerous-rm check prompted on in bypass permissions mode because its parser cannot read zsh's `${(s:,:)cols}`."""
    loop = (
        "for db in ~/.cc-slack/dm-threads.db ~/.cc-slack/threads.db ~/.cc-slack/posts.db ~/.cc-slack/cards.db; do"
        " for t in $(sqlite3 -readonly $db \"select name from sqlite_master where type='table'\"); do"
        " cols=$(sqlite3 -readonly $db \"select group_concat(name) from pragma_table_info('$t')\");"
        " for col in ${(s:,:)cols}; do sqlite3 -readonly $db \"select '$db', '$t', '$col' from $t where $col like '%thread_ts%' limit 1\"; done;"
        " done; done"
    )
    prompt = bash(loop, session="lane", hook_event_name="PermissionRequest")
    assert bash_guard.deny_reason(prompt) is None
    monkeypatch.setenv(state.REMOTE_LANE_ENV, "1")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(prompt)))
    bash_guard.main()
    decided = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert decided == {"hookEventName": "PermissionRequest", "decision": {"behavior": "deny", "message": bash_guard.UNSEEN_PROMPT}}
    assert bash_guard.deny_reason(bash(loop, session="lane", hook_event_name="PreToolUse")) is None


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
    typed = f"cci session type {demo}-coordinator /exit"
    assert bash_guard.deny_reason(bash(typed)).startswith("refused: o1 is an open incident order, and ending the coordinator now stops it mid-incident")
    assert bash_guard.deny_reason(bash(f"cci session type {demo}-coordinator /compact")) is None
    with state.edit(demo) as data:
        data["orders"]["o1"]["state"] = "done"
    assert bash_guard.deny_reason(bash(send)) is None
    assert bash_guard.deny_reason(bash(typed)) is None


def test_a_drive_call_retrying_past_a_failing_check_is_refused_naming_the_log_read(demo: str) -> None:
    for command, step in (
        ("cd /work && .agents/skills/submit-pr/scripts/stack-enqueue 4244 --flake", "stack-enqueue --flake"),
        ("tools/ci bk rerun 0190a1b2-job", "ci bk rerun"),
        ("gh run rerun 123 --failed -R yasyf/cc-skills", "gh run rerun"),
    ):
        reason = bash_guard.deny_reason(bash(command))
        assert reason.startswith(f"refused: `{step}` retries past a failing check, and a check that fails is fixed at its cause")
        assert "`tools/ci bk log '<job URL>'`" in reason
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


R2 = "https://4287159177eecb5f0a7e87cfe96ccde5.r2.cloudflarestorage.com"
BLOBS = "s3://artifacts-prod/iris/teams/team_01demo/runs/blobs"


def scratch(tmp_path: Path, folder: str) -> Path:
    """j2386's scratch folder as its break-glass reads ran it: bucket-read5.sh fans its digests out 32 at a time to fetch-one.sh, which copies one blob from the artifacts bucket on R2."""
    root = tmp_path / "j2386"
    root.mkdir()
    (root / "bucket-read5.sh").write_text(f"#!/bin/zsh\nset -euo pipefail\nS={folder}\nmkdir -p $S/blobs\nxargs -P 32 -n 1 zsh $S/fetch-one.sh < $S/digests-left.txt\n")
    (root / "fetch-one.sh").write_text(f'#!/bin/zsh\nenv -u AWS_PROFILE AWS_ACCESS_KEY_ID="$K" aws --endpoint-url {R2} s3 cp "{BLOBS}/$1" "$S/blobs/$1" --only-show-errors\n')
    return root


@pytest.mark.parametrize("folder", ["/unused/elsewhere", "${0:A:h}"])
def test_a_drive_job_reads_no_teams_blobs_where_it_runs_even_through_break_glass_and_xargs(demo: str, tmp_path: Path, folder: str) -> None:
    root = scratch(tmp_path, str(tmp_path / "j2386") if folder.startswith("/") else folder)
    builder = {"agent_id": "ad4fdaae757fd3b72", "agent_type": "long-running:builder"}
    replay = 'drive break-glass j2386 --stack sandsql/plat-usw2-prod --reason "read-only: fetch the OOM run\'s blobs" -- zsh ./bucket-read5.sh'
    reason = bash_guard.deny_reason(bash(replay, cwd=str(root), **builder))
    assert reason.startswith("refused: this reads `artifacts-prod/iris/teams/team_01demo/runs/blobs/$1` where the job runs")
    assert '"it shouldnt be reading iris blobs at all locally only with a service account on a pass box"' in reason
    assert "`AWS_PROFILE=plat-prod tools/ci pass-box launch --purpose <job>" in reason
    assert '`eval "$(./ci runs-reader grant --team team_01demo)"`' in reason
    assert bash_guard.deny_reason(bash(f"cd {root} && {replay}", cwd="/work", **builder)) == reason
    assert bash_guard.deny_reason(bash(replay, cwd=str(root), session="ordinary")) is None


def test_a_bucket_read_is_refused_by_what_it_reads_and_a_forge_bucket_or_a_pass_box_call_is_not(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    refused = {
        "aws s3 ls s3://artifacts-prod/iris/teams/team_01demo/sessions/": "artifacts-prod/iris/teams/team_01demo/sessions/",
        f"aws --endpoint-url {R2} s3 cp --recursive --exclude '*' --include '*.ltx' s3://artifacts-prod/iris/teams/t/runs/wal/ ./wal": "artifacts-prod/iris/teams/t/runs/wal/",
        "AWS_PROFILE=plat-prod aws s3 sync s3://frg-plat-usw2-prod-sandsql-replication/wal/team_1/ .": "frg-plat-usw2-prod-sandsql-replication/wal/team_1/",
        "aws s3api get-object --bucket forge-acme-com --key iris/teams/t/runs/blobs/x out": "forge-acme-com",
        "aws s3 cp s3://forge-globex-artifact-bucket/iris/teams/t/x -": "forge-globex-artifact-bucket/iris/teams/t/x",
        'B=artifacts-prod; for d in a b; do aws s3 cp "s3://$B/iris/teams/t/runs/blobs/$d" .; done': "artifacts-prod/iris/teams/t/runs/blobs/$d",
        "rclone copy r2:artifacts-prod/iris/teams/t/runs/ ./runs --transfers 32": "artifacts-prod/iris/teams/t/runs/",
        "gsutil -m cp gs://customer-bucket/iris/teams/t/x .": "customer-bucket/iris/teams/t/x",
        "azcopy copy 'https://acct.blob.core.windows.net/iris/teams/t/x' ./x": "acct/iris/teams/t/x",
        "sh -c 'aws s3 ls s3://$BUCKET/iris/'": "$BUCKET/iris/",
        "aws s3 presign s3://frg-plat-usw2-prod-forge-app-bucket/iris/teams/t/runs/blobs/x --expires-in 1800": "frg-plat-usw2-prod-forge-app-bucket/iris/teams/t/runs/blobs/x",
        "AWS_PROFILE=plat-staging aws s3 cp s3://frg-plat-usw2-staging-microvm-artifacts/projects/proj_01demo/code.zip -": "frg-plat-usw2-staging-microvm-artifacts/projects/proj_01demo/code.zip",
        "aws s3 ls s3://frg-plat-usw2-staging-microvm-artifacts/": "frg-plat-usw2-staging-microvm-artifacts/",
    }
    builder = {"agent_id": "a-1", "agent_type": "long-running:builder"}
    for command, location in refused.items():
        assert bash_guard.deny_reason(bash(command, **builder)).startswith(f"refused: this reads `{location}` where the job runs"), command
    passed = [
        "aws s3 cp s3://frg-core-usw2-artifacts-internal-release-artifacts/sand/latest.json -",
        "aws s3 ls s3://frg-core-usw2-root-pulumi-state/pulumi/.pulumi/stacks/api/",
        "AWS_PROFILE=plat-staging aws s3 presign s3://frg-plat-usw2-staging-microvm-artifacts/pass-box/searchbench/searchbench.gz --expires-in 1800 --region us-west-2",
        "AWS_PROFILE=plat-prod aws s3 cp s3://frg-plat-usw2-prod-microvm-artifacts/pass-box/j2386-oom/ci-linux.gz -",
        "aws s3 cp ./ci-linux s3://artifacts-prod/iris/teams/t/upload",
        "aws s3 rm s3://artifacts-prod/iris/teams/t/runs/stuck.json",
        "aws s3 ls",
        "aws s3api get-bucket-notification-configuration --bucket $B",
        "AWS_PROFILE=plat-prod tools/ci pass-box launch --purpose j2386-oom --type r7i.4xlarge --delete-after 2026-11-14",
        "AWS_PROFILE=plat-prod aws ssm send-command --region us-west-2 --instance-ids i-0abc --document-name AWS-RunShellScript"
        ' --parameters \'commands=["eval \\"$(./ci runs-reader grant --team t)\\"","aws s3 cp s3://artifacts-prod/iris/teams/t/runs/blobs/x ."]\'',
        "cat > /s/box.sh <<'EOF'\naws s3 cp s3://artifacts-prod/iris/teams/t/runs/blobs/x .\nEOF",
        "grep -rn artifacts-prod /work/docs",
    ]
    assert [bash_guard.deny_reason(bash(command, **builder)) for command in passed] == [None] * len(passed)
    assert bash_guard.deny_reason(bash("aws s3 ls s3://artifacts-prod/iris/teams/t/")).startswith("refused: this reads")
    assert bash_guard.deny_reason(bash("aws s3 ls s3://artifacts-prod/iris/teams/t/", session="ordinary")) is None
    monkeypatch.setenv(state.REMOTE_LANE_ENV, "1")
    assert bash_guard.deny_reason(bash("aws s3 ls s3://artifacts-prod/iris/teams/t/", session="sprite")).startswith("refused: this reads")


def test_a_refused_team_read_names_the_runs_reader_for_a_teams_runs_and_blocked_for_every_other_prefix(demo: str) -> None:
    builder = {"agent_id": "a-1", "agent_type": "long-running:builder"}
    runs = bash_guard.deny_reason(bash("aws s3 cp s3://artifacts-prod/iris/teams/team_01demo/runs/blobs/x -", **builder))
    assert "The one such read is `iris/teams/<team>/runs/` of a team the plat-prod cluster serves" in runs
    assert '`eval "$(./ci runs-reader grant --team team_01demo)"`' in runs
    assert "A team another cluster serves, a staging read, or a `./ci runs-reader grant` that refuses" in runs
    assert "Forge-AI/monorepo#55460 and #55533 are live, has no route: return `BLOCKED`" in runs
    blocked = {
        "aws s3 ls s3://artifacts-prod/iris/teams/team_01demo/sessions/": "iris/teams/team_01demo/sessions/",
        "aws s3 cp s3://artifacts-prod/iris/teams/team_01demo/blobs/team_01demo/x -": "iris/teams/team_01demo/blobs/",
        "aws s3api get-object --bucket forge-acme-com --key iris/teams/t/runs/blobs/x out": "forge-acme-com",
        "AWS_PROFILE=plat-prod aws s3 sync s3://frg-plat-usw2-prod-sandsql-replication/wal/team_1/ .": "frg-plat-usw2-prod-sandsql-replication/wal/team_1/",
        "sh -c 'aws s3 ls s3://$BUCKET/iris/'": "$BUCKET/iris/",
    }
    for command, prefix in blocked.items():
        reason = bash_guard.deny_reason(bash(command, **builder))
        assert f"No service account reads `{prefix}`" in reason, command
        assert "return `BLOCKED` naming the prefix and the team" in reason, command
        assert "never a person's grant such as `tools/ci pass-box-creds`" in reason, command
        assert "runs-reader grant" not in reason and "pass-box launch" not in reason, command


HANDLER_SESSION = "5e48f8c7-ad54-49d0-bf4e-bbc509e11035"
MONITOR_READ = (
    "pup monitors get 337843756 2>&1 | python3 -I -c \"import json,sys; d=json.load(sys.stdin); d=d.get('data',d);"
    " print({k:d.get(k) for k in ['name','overall_state','overallState']}); print(json.dumps(d.get('state',{}))[:600])\""
)
LOGS_READ = (
    "pup logs search --query 'service:restate-worker @log_kind:executor_oom_run_crashed -forge_cluster:plat-sandbox"
    " -forge_cluster:plat-staging' --from 5h --limit 50 2>&1 | python3 -I -c \"\nimport json,sys\nd=json.load(sys.stdin);"
    " rows=d.get('data',d) if isinstance(d,dict) else d\nprint(len(rows))\nfor r in rows[:20]:\n  a=r.get('attributes',r);"
    " print(a.get('timestamp'), (a.get('attributes') or {}).get('forge_cluster'))\n\""
)


def test_handler_9d78cc4es_pup_reads_are_refused_naming_the_datadog_mcp(communicator: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    with state.edit(communicator) as data:
        data["handlers"]["9d78cc4e"] = {"session": HANDLER_SESSION, "started": state.stamp(), "holds": None, "handed": []}
    own = {"agent_type": "long-running:handler", "transcript_path": str(tmp_path / "handler.jsonl")}
    for command in (MONITOR_READ, LOGS_READ):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bash(command, session=HANDLER_SESSION, **own))))
        bash_guard.main()
        output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
        assert (output["permissionDecision"], output["permissionDecisionReason"]) == ("deny", bash_guard.DATADOG_READ), command
    assert bash_guard.DATADOG_READ.startswith("refused: a drive session reads Datadog only through the Datadog MCP's tools, whose `execute_code`")
    assert "`select:mcp__plugin_datadog_mcp__execute_code," in bash_guard.DATADOG_READ


def test_every_drive_session_and_drive_agent_reads_datadog_through_its_mcp_never_pup(communicator: str, tmp_path: Path) -> None:
    script = tmp_path / "read.sh"
    script.write_text("#!/bin/zsh\nset -euo pipefail\n/opt/homebrew/bin/pup logs search --query 'service:api' --from 1h\n")
    reader = {"agent_id": "a-1", "agent_type": "long-running:reader"}
    refused = [
        bash(MONITOR_READ),
        bash(f"zsh {script}"),
        bash('drive break-glass j1 --stack sandsql/plat-usw2-prod --reason "read the monitor" -- pup monitors get 337843756'),
        bash("env PUP_SITE=datadoghq.com pup metrics query --query 'avg:system.load.1{*}' --from 1h", session="ordinary", **reader),
    ]
    for payload in refused:
        assert bash_guard.deny_reason(payload) == bash_guard.DATADOG_READ, payload["tool_input"]["command"]
    assert bash_guard.deny_reason(bash("which pup dog datadog")) is None
    assert bash_guard.deny_reason(bash(f"cat > {tmp_path / 'later.sh'} <<'EOF'\npup monitors get 1\nEOF")) is None
    assert bash_guard.deny_reason(bash(MONITOR_READ, session="ordinary")) is None
