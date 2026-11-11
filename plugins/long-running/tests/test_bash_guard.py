import io
import json
from collections.abc import Callable
from pathlib import Path

import bash_guard
import pytest
from conftest import SESSION
from drive import orca, state
from drive.orca import OrcaError

Run = Callable[..., tuple[int, str]]
RESUMED = "d8677ab9-1e8c-4adb-a3bb-3f661dc45b77"
CREATE = f'orca terminal create --worktree path:/work --title demo-coordinator --command "claude --resume {RESUMED} -n demo-coordinator" --json'
CACHED = "~/.claude/plugins/cache/x/long-running/1.0.1/skills/long-running/reference"


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


def test_a_builder_opens_its_pr_the_repositorys_way(demo: str) -> None:
    builder = {"agent_id": "a-1", "agent_type": "long-running:builder"}
    reason = bash_guard.deny_reason(bash("gh pr create --base main --head fix --title X --body-file /s/body.md", **builder))
    assert reason.startswith("refused: a builder opens its PR the repository's way, never a bare `gh pr create`")
    assert "`ccx vcs ship --pr-title-file <title> --pr-body-file <body>`" in reason
    assert bash_guard.deny_reason(bash("ccx vcs ship --no-watch --pr-title-file /s/t --pr-body-file /s/b", **builder)) is None
    assert bash_guard.deny_reason(bash("gh pr create --title X", session="ordinary")) is None


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


def test_a_drive_role_runs_a_verb_its_mcp_tool_serves_through_the_tool(demo: str) -> None:
    reason = bash_guard.deny_reason(bash('drive job set j3 --said "x"'))
    assert reason.startswith("refused: `drive job set` has a tool on the drive MCP server")
    assert "`job(ops=[{set: {" in reason
    assert "`drive tool job`" in reason
    assert bash_guard.deny_reason(bash("drive status o1")).startswith("refused: `drive status` has a tool")
    assert bash_guard.deny_reason(bash("drive job report j3 -")) is None
    assert bash_guard.deny_reason(bash("drive status --json")) is None
    assert bash_guard.deny_reason(bash('drive job set j3 --said "x"', agent_id="a-1", agent_type="long-running:builder")) is None


def test_a_session_runs_a_drive_tool_its_session_does_not_list_through_drive_tool(communicator: str) -> None:
    assert "pipe that JSON to `drive tool heard`" in bash_guard.deny_reason(bash("drive heard --ts 1.2"))
    assert bash_guard.deny_reason(bash("echo '{\"ts\": \"1.2\"}' | drive tool heard")) is None


def test_a_session_outside_any_drive_and_its_agents_are_not_checked(demo: str) -> None:
    command = 'orca terminal create --worktree path:/work --command "claude; exec zsh -l"'
    assert bash_guard.deny_reason(bash(command, session="ordinary")) is None
    assert bash_guard.deny_reason(bash('drive job resume j3 "go"', session="ordinary", call={"run_in_background": True})) is None
    builder = {"agent_id": "a-1", "agent_type": "long-running:builder"}
    assert bash_guard.deny_reason(bash(command, session="ordinary", **builder)).startswith("refused: the terminal's --command runs `zsh` after claude")


def test_drive_job_resume_runs_in_the_foreground(demo: str) -> None:
    reason = bash_guard.deny_reason(bash('drive job resume j3 "go"', call={"run_in_background": True}))
    assert reason.startswith("refused: `drive job resume` runs in the foreground with `timeout: 20000`")
    assert bash_guard.deny_reason(bash('drive job resume j3 "go"', call={"timeout": 20000})) is None


def test_a_handler_keeps_each_bash_call_one_plain_pipeline(demo: str) -> None:
    handler = {"agent_id": "a-1", "agent_type": "long-running:handler"}
    reason = bash_guard.deny_reason(bash("a=$(cci show 1); for s in 1 2; do cci show $s; done", **handler))
    assert reason.startswith("refused: a handler keeps each Bash call one plain pipeline, and this one chains steps with `(`")
    assert "`Scratch:` folder" in reason
    assert "`&&`" in bash_guard.deny_reason(bash("cd /work && cci show 1", **handler))
    assert bash_guard.deny_reason(bash("cci show 661229 --json", **handler)) is None
    assert bash_guard.deny_reason(bash("cci show 661229 --json | jq -r .text", **handler)) is None
    assert bash_guard.deny_reason(bash("drive job report j3 - <<'EOF'\nDONE it ran; (twice)\nEOF", **handler)) is None
    assert bash_guard.deny_reason(bash("a=$(cci show 1); cci show $a")) is None


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


def test_a_terminal_command_ends_with_the_claude_command(demo: str) -> None:
    reason = bash_guard.deny_reason(bash('orca terminal create --worktree path:/work --command "claude --resume x; exec zsh -l"'))
    assert reason.startswith("refused: the terminal's --command runs `zsh` after claude")
    assert bash_guard.deny_reason(bash('orca terminal create --worktree path:/work --command "claude --resume x"')) is None


def test_no_session_is_moved_while_an_incident_order_is_open(demo: str, run: Run) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Incidents", "the API is down")
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
