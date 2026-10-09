from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/long-running/scripts"
BIN = Path(__file__).resolve().parents[1] / "bin"

SENT = {"rc": 0, "out": {"ok": True, "result": {}}}
BOUND = {"rc": 0, "out": {"ok": True, "result": {"run": {"id": "run_1", "coordinator_handle": "term_coordinator"}}}}
TIMED_OUT = {"rc": 1, "out": {"ok": False, "error": {"code": "runtime_error", "message": "Timed out waiting for terminal handle after creation"}}}
IDLE = {"rc": 0, "out": {"ok": True, "result": {"wait": {"handle": "term_a", "condition": "tui-idle", "satisfied": True, "status": "running", "exitCode": None}}}}
MISSING = {"rc": 1, "out": ""}
NOT_IDLE = {"rc": 1, "out": {"ok": False, "error": {"code": "timeout", "message": "timeout"}}}
DESK_CONTRACT = " Standing desk: loop until rotation; setup and quiet cycles are not done. Send worker_done only at rotation, naming the handoff doc."

ORCA = """#!/usr/bin/env python3
import json, os, subprocess, sys
state = os.environ["FAKE_STATE"]
with open(os.path.join(state, "calls"), "a") as calls:
    calls.write(json.dumps(sys.argv[1:]) + "\\n")
key = "_".join(sys.argv[1:3])
queue = os.path.join(state, key)
if key == "worktree_show" and not os.path.exists(queue):
    path = sys.argv[sys.argv.index("--worktree") + 1].removeprefix("path:")
    found = os.path.isdir(path)
    sys.stdout.write(json.dumps({"ok": True, "result": {"worktree": {"path": path}}}) if found else "")
    sys.exit(0 if found else 1)
replies = json.load(open(queue)) if os.path.exists(queue) else [{"rc": 1, "out": ""}]
reply = replies.pop(0) if len(replies) > 1 else replies[0]
json.dump(replies, open(queue, "w"))
if reply.get("mkdir"):
    os.makedirs(reply["mkdir"], exist_ok=True)
    if not os.path.exists(os.path.join(reply["mkdir"], ".git")):
        subprocess.run(["git", "init", "-q", reply["mkdir"]], check=True)
        subprocess.run(["git", "-C", reply["mkdir"], "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base"], check=True)
sys.stdout.write(reply["out"] if isinstance(reply["out"], str) else json.dumps(reply["out"]))
sys.exit(reply["rc"])
"""

SLEEP = """#!/bin/sh
echo "$1" >> "$FAKE_STATE/sleeps"
[ ! -f "$FAKE_STATE/on_sleep" ] || { sh "$FAKE_STATE/on_sleep" && rm "$FAKE_STATE/on_sleep"; }
"""


class Orca:
    def __init__(self, root: Path):
        self.root = root
        self.state = root / "state"
        self.state.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for tool, body in {"orca": ORCA, "sleep": SLEEP}.items():
            (bin_dir / tool).write_text(body)
            (bin_dir / tool).chmod(0o755)
        self.receipts = root / "receipts"
        self.parent = root / "workspaces" / "coordinator"
        self.parent.mkdir(parents=True)
        self.brief = root / "specs" / "lane-a.full.md"
        self.brief.parent.mkdir()
        self.brief.write_text("# brief\n")
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_STATE": str(self.state),
            "ORCA_LAUNCH_RUN": "run_1",
            "ORCA_LAUNCH_REPO": "repo-1",
            "ORCA_LAUNCH_PARENT": str(self.parent),
            "ORCA_LAUNCH_PREFIX": "v3-",
            "ORCA_LAUNCH_BASE": "origin/dev",
            "ORCA_LAUNCH_STATE": str(self.receipts),
            "ORCA_LAUNCH_CLAUDE_ARGS": "--channels plugin:cc-review@cc-review",
            "ORCA_CHECK_STATE": str(self.receipts),
            "ORCA_TERMINAL_HANDLE": "term_root",
            "HOME": str(root / "home"),
            "CODEX_HOME": str(root / "codex"),
        }
        self.env.pop("CLAUDE_LONG_RUNNING_DRIVE", None)

    @property
    def worktree(self) -> Path:
        return self.parent.parent / "v3-lane-a-base"

    def reply(self, command: str, *replies: dict) -> None:
        (self.state / command.replace(" ", "_")).write_text(json.dumps(list(replies)))

    def calls(self, command: str | None = None) -> list[list[str]]:
        path = self.state / "calls"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [row for row in rows if command is None or " ".join(row[:2]) == command]

    def sleeps(self) -> list[str]:
        path = self.state / "sleeps"
        return path.read_text().split() if path.exists() else []

    def run(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(SCRIPTS / script), *args], env=self.env, capture_output=True, text=True)

    def healthy(self, state: str = "ready", screen: str = "⏵⏵ bypass permissions on (shift+tab to cycle)", agent: str = "claude") -> None:
        self.reply("orchestration run-current", BOUND)
        self.reply("worktree create", {"rc": 0, "out": {"ok": True, "result": {"worktree": {"path": str(self.worktree)}}}, "mkdir": str(self.worktree)})
        self.reply("terminal create", {"rc": 0, "out": {"ok": True, "result": {"terminal": {"handle": "term_a"}}}})
        self.reply("orchestration worker-start", {"rc": 0, "out": {"ok": True, "result": {"state": state, "taskId": "task_a", "dispatchId": "ctx_a"}}})
        self.reply("terminal read", {"rc": 0, "out": {"ok": True, "result": {"terminal": {"tail": ["❯", screen]}}}})
        self.reply("terminal list", listing(agent))
        self.reply("terminal wait", IDLE if agent else NOT_IDLE)
        self.reply("terminal close", SENT)
        self.reply("worktree rm", SENT)

    def check_out(self, index: str = "full") -> None:
        git = ["git", "-C", str(self.worktree), "-c", "user.name=t", "-c", "user.email=t@t"]
        self.worktree.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", str(self.worktree)], check=True)
        (self.worktree / "file").write_text("x\n")
        subprocess.run([*git, "add", "file"], check=True)
        subprocess.run([*git, "commit", "-qm", "base"], check=True)
        if index == "empty":
            subprocess.run([*git, "read-tree", "--empty"], check=True)

    def launch(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.run("orca-launch.sh", *(args or ("lane-a", "opus", "high", str(self.brief))))


def registered(orca: Orca) -> dict:
    return {"rc": 0, "out": {"ok": True, "result": {"worktree": {"path": str(orca.worktree)}}}}


def listing(agent: str | None, *handles: str) -> dict:
    terminals = [{"handle": "term_other", "agentIdentity": "claude"}, *({"handle": handle, "agentIdentity": agent} for handle in handles or ("term_a",))]
    return {"rc": 0, "out": {"ok": True, "result": {"terminals": terminals}}}


def tasks(*ids: str) -> dict:
    return {"rc": 0, "out": {"ok": True, "result": {"tasks": [{"id": task} for task in ids]}}}


def bare() -> dict:
    return {"rc": 0, "out": {"ok": True, "result": {"terminals": [{"handle": "term_other", "agentIdentity": "claude"}]}}}


@pytest.fixture
def orca() -> Iterator[Orca]:
    with tempfile.TemporaryDirectory(dir="/tmp") as root:
        yield Orca(Path(root))


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_a_worker_launched_inside_a_drive_carries_the_drive_to_claude(orca):
    orca.healthy()
    orca.env["CLAUDE_LONG_RUNNING_DRIVE"] = "900424b6"
    assert orca.launch().returncode == 0
    [terminal] = orca.calls("terminal create")
    assert flag(terminal, "--command").startswith("env CLAUDE_LONG_RUNNING_LANE=lane-a CLAUDE_LONG_RUNNING_DRIVE=900424b6 claude ")


def test_a_worker_launched_by_a_sessionless_runner_carries_the_drive_of_its_orca_run(orca):
    orca.healthy()
    drives = orca.root / "home" / ".claude" / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / "900424b6.json").write_text(json.dumps({"drive": "900424b6", "sessions": [], "orca_run": "run_1"}))
    orca.env.pop("CLAUDE_CODE_SESSION_ID", None)
    assert orca.launch().returncode == 0
    [terminal] = orca.calls("terminal create")
    assert flag(terminal, "--command").startswith("env CLAUDE_LONG_RUNNING_LANE=lane-a CLAUDE_LONG_RUNNING_DRIVE=900424b6 claude ")


def bind_drive(orca: Orca, run: str) -> None:
    drives = orca.root / "home" / ".claude" / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / "900424b6.json").write_text(json.dumps({"drive": "900424b6", "sessions": ["session-root"], "orca_run": run}))
    orca.env["CLAUDE_CODE_SESSION_ID"] = "session-root"


def test_a_launch_without_a_run_id_launches_into_its_drives_orca_run(orca):
    orca.healthy()
    bind_drive(orca, "run_1")
    del orca.env["ORCA_LAUNCH_RUN"]
    assert orca.launch().returncode == 0
    [start] = orca.calls("orchestration worker-start")
    assert flag(start, "--run") == "run_1"
    assert (orca.receipts / "lane-a.json").exists()


def test_an_explicit_run_id_wins_over_the_drives_orca_run(orca):
    orca.healthy()
    bind_drive(orca, "run_other")
    assert orca.launch().returncode == 0
    [start] = orca.calls("orchestration worker-start")
    assert flag(start, "--run") == "run_1"


def test_a_launch_outside_any_drive_without_a_run_id_fails_before_touching_orca(orca):
    orca.healthy()
    orca.env.pop("CLAUDE_CODE_SESSION_ID", None)
    del orca.env["ORCA_LAUNCH_RUN"]
    result = orca.launch()
    assert result.returncode == 1
    assert result.stdout.startswith("lane-a failed run: this session's drive binds no Orca run;")
    assert orca.calls() == []


def main_checkout(orca: Orca) -> Path:
    main = orca.root / "main"
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    subprocess.run(["git", "-C", str(main), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    orca.parent.rmdir()
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(orca.parent)], check=True)
    return main.resolve()


def repos(*paths: Path) -> dict:
    return {"rc": 0, "out": {"ok": True, "result": {"repos": [{"id": f"repo-{index}", "path": str(path)} for index, path in enumerate(paths)]}}}


def test_a_launch_without_a_repo_id_uses_the_orca_repo_at_the_parents_main_checkout(orca):
    orca.healthy()
    main = main_checkout(orca)
    orca.reply("repo list", repos(main.parent / "elsewhere", main))
    del orca.env["ORCA_LAUNCH_REPO"]
    assert orca.launch().returncode == 0
    [create] = orca.calls("worktree create")
    assert flag(create, "--repo") == "id:repo-1"


def test_an_explicit_repo_id_never_lists_orca_repos(orca):
    orca.healthy()
    assert orca.launch().returncode == 0
    assert orca.calls("repo list") == []
    [create] = orca.calls("worktree create")
    assert flag(create, "--repo") == "id:repo-1"


def test_a_main_checkout_orca_does_not_know_fails_naming_its_path(orca):
    orca.healthy()
    main = main_checkout(orca)
    orca.reply("repo list", repos(main.parent / "elsewhere"))
    del orca.env["ORCA_LAUNCH_REPO"]
    result = orca.launch()
    assert result.returncode == 1
    assert result.stdout.strip() == f"lane-a failed repo: orca repo list names no repo at {main}; set ORCA_LAUNCH_REPO or run orca repo add --path {main}"
    assert orca.calls("worktree create") == []


def test_launch_creates_a_child_worktree_and_a_bypass_terminal(orca):
    orca.healthy()
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"lane-a ready task=task_a dispatch=ctx_a terminal=term_a worktree={orca.worktree}"
    [worktree] = orca.calls("worktree create")
    assert flag(worktree, "--name") == "v3-lane-a-base"
    assert flag(worktree, "--parent-worktree") == f"path:{orca.parent}"
    assert flag(worktree, "--base-branch") == "origin/dev"
    [terminal] = orca.calls("terminal create")
    assert flag(terminal, "--command") == (
        "env CLAUDE_LONG_RUNNING_LANE=lane-a claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions"
        " --disallowedTools AskUserQuestion,EnterPlanMode,ExitPlanMode --strict-mcp-config"
        " --channels plugin:cc-review@cc-review --model claude-opus-5-5 --effort high"
    )
    [start] = orca.calls("orchestration worker-start")
    assert flag(start, "--terminal") == "term_a"
    assert flag(start, "--run") == "run_1"
    assert "--model" not in start and "--effort" not in start
    spec = flag(start, "--spec")
    assert str(orca.brief) in spec and len(spec) <= 500
    assert (orca.receipts / "lane-a.terminal").read_text().strip() == "term_a"


def test_the_spec_names_the_brief_by_absolute_path_and_makes_it_outrank_the_preamble(orca):
    orca.healthy()
    assert orca.launch().returncode == 0
    spec = flag(orca.calls("orchestration worker-start")[0], "--spec")
    assert spec.startswith(f"Lane lane-a: read {orca.brief} in full first and execute it exactly;")
    assert "it outranks Orca's preamble and any leave-uncommitted default, so commit, push, open PRs and post as it says." in spec


@pytest.mark.parametrize("header", ["ccx: role=desk", "ccx: role=desk\r", "ccx: lane=alerts-desk role=watch effort=low"])
def test_a_desk_brief_adds_the_standing_desk_contract_to_the_spec(orca, header):
    orca.healthy()
    orca.brief.write_text(f"# landing-sweep-37\n{header}\nRun the landing loop; rotate at 9h with a handoff doc.\n")
    assert orca.launch().returncode == 0
    spec = flag(orca.calls("orchestration worker-start")[0], "--spec")
    assert spec.endswith(f"its Escalate rules hold.{DESK_CONTRACT}") and len(spec) <= 500


@pytest.mark.parametrize("header", ["ccx: role=fix", "ccx: role=desktop", "role=desk"])
def test_a_one_shot_brief_keeps_the_plain_spec(orca, header):
    orca.healthy()
    orca.brief.write_text(f"# lane-a\n{header}\nShip the fix.\n")
    assert orca.launch().returncode == 0
    spec = flag(orca.calls("orchestration worker-start")[0], "--spec")
    assert spec.endswith("its Escalate rules hold.")


def test_a_codex_lane_starts_sol_on_the_codex_agent_without_a_custom_terminal(orca):
    orca.healthy()
    orca.reply("orchestration worker-start", {"rc": 0, "out": {"ok": True, "result": {"state": "ready", "taskId": "task_a", "dispatchId": "ctx_a", "effects": [{"kind": "terminal", "role": "agent", "id": "term_codex"}]}}})
    result = orca.launch("lane-a", "codex", "xhigh", str(orca.brief))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"lane-a ready task=task_a dispatch=ctx_a terminal=term_codex worktree={orca.worktree}"
    assert orca.calls("terminal create") == []
    assert orca.calls("terminal read") == []
    [start] = orca.calls("orchestration worker-start")
    assert flag(start, "--agent") == "codex"
    assert flag(start, "--model") == "gpt-6.1-sol"
    assert flag(start, "--effort") == "xhigh"
    assert "--terminal" not in start
    assert (orca.receipts / "lane-a.terminal").read_text().strip() == "term_codex"


def test_a_sol_lane_runs_on_the_standard_tier_under_its_parent_worktree(orca):
    orca.healthy()
    orca.reply("orchestration worker-start", {"rc": 0, "out": {"ok": True, "result": {"state": "ready", "taskId": "task_a", "dispatchId": "ctx_a", "effects": [{"kind": "terminal", "role": "agent", "id": "term_codex"}]}}})
    assert orca.launch("lane-a", "sol", "xhigh", str(orca.brief)).returncode == 0
    [worktree] = orca.calls("worktree create")
    assert "--parent-worktree" in worktree and "--no-parent" not in worktree
    assert orca.calls("terminal create") == []
    [start] = orca.calls("orchestration worker-start")
    assert (flag(start, "--agent"), flag(start, "--model")) == ("codex", "gpt-6.1-sol")
    assert not any("service_tier" in arg for arg in start)


def test_an_incident_lane_runs_codex_on_the_fast_tier_in_its_own_terminal_in_a_top_level_worktree(orca):
    orca.healthy(agent="codex")
    result = orca.launch("lane-a", "incident", "xhigh", str(orca.brief))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"lane-a ready task=task_a dispatch=ctx_a terminal=term_a worktree={orca.worktree}"
    [worktree] = orca.calls("worktree create")
    assert "--no-parent" in worktree and "--parent-worktree" not in worktree
    [terminal] = orca.calls("terminal create")
    assert flag(terminal, "--command") == (
        f"sh -c 'PATH={BIN}:$PATH exec codex --dangerously-bypass-approvals-and-sandbox -c model=gpt-6.1-sol -c service_tier=fast -c model_reasoning_effort=xhigh -c check_for_update_on_startup=false -c mcp_servers={{}}'"
    )
    [start] = orca.calls("orchestration worker-start")
    assert flag(start, "--terminal") == "term_a"
    assert "--agent" not in start and "--model" not in start and "--effort" not in start
    assert orca.calls("terminal read") == []
    assert (orca.receipts / "lane-a.terminal").read_text().strip() == "term_a"


def test_a_worker_starts_only_the_mcp_servers_its_launch_names(orca):
    orca.healthy()
    orca.env["ORCA_LAUNCH_MCP_CONFIG"] = "/briefs/slack-mcp.json"
    assert orca.launch().returncode == 0
    command = flag(orca.calls("terminal create")[0], "--command")
    assert " --strict-mcp-config --mcp-config /briefs/slack-mcp.json " in command


def test_an_incident_worker_takes_its_mcp_servers_from_the_launch(orca):
    orca.healthy(agent="codex")
    orca.env["ORCA_LAUNCH_CODEX_MCP"] = '{datadog={url="https://mcp.datadoghq.com"}}'
    assert orca.launch("lane-a", "incident", "xhigh", str(orca.brief)).returncode == 0
    command = flag(orca.calls("terminal create")[0], "--command")
    assert command.endswith(""" -c mcp_servers={datadog={url="https://mcp.datadoghq.com"}}'""")


def test_an_incident_worker_disables_every_config_server_its_launch_does_not_name(orca):
    orca.healthy(agent="codex")
    (orca.root / "codex").mkdir()
    (orca.root / "codex" / "config.toml").write_text(
        'model = "gpt-6-luna"\n[mcp_servers.node_repl]\ncommand = "node_repl"\n[mcp_servers.slack]\ncommand = "npx"\n'
        '[mcp_servers.datadog]\nurl = "https://mcp.datadoghq.com"\n[plugins."computer-history@openai-bundled"]\nenabled = true\n'
    )
    orca.env["ORCA_LAUNCH_CODEX_MCP"] = '{datadog={url="https://mcp.datadoghq.com"}}'
    assert orca.launch("lane-a", "incident", "xhigh", str(orca.brief)).returncode == 0
    command = flag(orca.calls("terminal create")[0], "--command")
    assert command.endswith(
        """ -c mcp_servers={datadog={url="https://mcp.datadoghq.com"}} -c mcp_servers.node_repl.enabled=false -c mcp_servers.slack.enabled=false'"""
    )


def test_a_claude_lane_never_reads_the_codex_config(orca):
    orca.healthy()
    (orca.root / "codex").mkdir()
    (orca.root / "codex" / "config.toml").write_text("not toml [")
    assert orca.launch().returncode == 0


STARTUP = {"rc": 0, "out": {"ok": True, "result": {"terminals": [{"handle": "term_shell", "agentIdentity": None}, {"handle": "term_other", "agentIdentity": "claude"}]}}}


def test_a_created_worktrees_startup_shell_is_closed_before_the_agent_terminal_opens(orca):
    orca.healthy()
    orca.reply("terminal list", STARTUP, listing("claude"))
    orca.reply("terminal close", SENT)
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    assert orca.calls("terminal close") == [["terminal", "close", "--terminal", "term_shell", "--tab", "--json"]]
    calls = [" ".join(call[:2]) for call in orca.calls()]
    assert calls.index("terminal close") < calls.index("terminal create")


def test_a_codex_lanes_created_worktree_has_its_startup_shell_closed(orca):
    orca.healthy()
    orca.reply("terminal list", STARTUP)
    orca.reply("terminal close", SENT)
    orca.reply("orchestration worker-start", {"rc": 0, "out": {"ok": True, "result": {"state": "ready", "taskId": "task_a", "dispatchId": "ctx_a", "effects": []}}})
    assert orca.launch("lane-a", "codex", "xhigh", str(orca.brief)).returncode == 0
    assert [flag(call, "--terminal") for call in orca.calls("terminal close")] == ["term_shell"]


def test_a_failed_startup_shell_close_still_launches(orca):
    orca.healthy()
    orca.reply("terminal list", STARTUP, listing("claude"))
    orca.reply("terminal close", {"rc": 1, "out": {"ok": False, "error": {"code": "not_found"}}})
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not_found" in (orca.receipts / "lane-a.startup.json").read_text()


def test_a_worktree_with_a_setup_terminal_keeps_every_terminal(orca):
    orca.healthy()
    created = {"worktree": {"path": str(orca.worktree)}, "setupReceipt": {"state": "running", "terminalHandle": "term_shell"}}
    orca.reply("worktree create", {"rc": 0, "out": {"ok": True, "result": created}, "mkdir": str(orca.worktree)})
    orca.reply("terminal list", STARTUP, listing("claude"))
    assert orca.launch().returncode == 0
    assert orca.calls("terminal close") == []


def test_a_relaunch_into_an_existing_worktree_closes_nothing(orca):
    orca.healthy()
    orca.check_out()
    orca.reply("terminal list", STARTUP, listing("claude", "term_shell", "term_a"))
    assert orca.launch().returncode == 0
    assert orca.calls("worktree create") == []
    assert orca.calls("terminal close") == []


def test_a_create_whose_response_was_lost_closes_nothing(orca):
    orca.healthy()
    orca.check_out()
    orca.reply("worktree show", MISSING, registered(orca))
    orca.reply("worktree create", {"rc": 1, "out": "Error: socket hang up"})
    orca.reply("terminal list", STARTUP, listing("claude"))
    assert orca.launch().returncode == 0
    assert orca.calls("terminal close") == []


def test_no_parent_puts_a_claude_lane_in_a_top_level_worktree(orca):
    orca.healthy()
    orca.env["ORCA_LAUNCH_NO_PARENT"] = "1"
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    [worktree] = orca.calls("worktree create")
    assert "--no-parent" in worktree and "--parent-worktree" not in worktree


@pytest.mark.parametrize("model", ["incident", "opus"])
def test_a_terminal_command_never_inlines_the_callers_path(orca, model):
    orca.healthy(agent="codex" if model == "incident" else "claude")
    orca.env["PATH"] = f"{orca.env['PATH']}:/{'p' * 6000}"
    assert orca.launch("lane-a", model, "xhigh", str(orca.brief)).returncode == 0
    command = flag(orca.calls("terminal create")[0], "--command")
    assert "p" * 100 not in command and len(command) < 600


def test_an_incident_terminal_runs_codex_with_the_plugin_bin_ahead_of_its_own_path(orca):
    orca.healthy(agent="codex")
    assert orca.launch("lane-a", "incident", "xhigh", str(orca.brief)).returncode == 0
    command = flag(orca.calls("terminal create")[0], "--command")
    codex = orca.root / "bin" / "codex"
    codex.write_text('#!/bin/sh\necho "$PATH"\necho "$@"\n')
    codex.chmod(0o755)
    shell = subprocess.run(["sh", "-c", command], env={"PATH": f"{orca.root / 'bin'}:/usr/bin:/bin"}, capture_output=True, text=True)
    assert shell.returncode == 0, shell.stderr
    assert shell.stdout.splitlines() == [
        f"{BIN}:{orca.root / 'bin'}:/usr/bin:/bin",
        "--dangerously-bypass-approvals-and-sandbox -c model=gpt-6.1-sol -c service_tier=fast -c model_reasoning_effort=xhigh -c check_for_update_on_startup=false -c mcp_servers={}",
    ]


def test_a_codex_readiness_timeout_sends_the_spec_and_runs_unsupervised(orca):
    orca.healthy()
    orca.reply("orchestration worker-start", {"rc": 1, "out": {"ok": True, "result": {"state": "failed", "failedStage": "agent_readiness", "taskId": "task_a", "dispatchId": "ctx_a", "effects": [{"kind": "terminal", "role": "agent", "id": "term_codex"}]}}})
    orca.reply("terminal send", {"rc": 0, "out": {"ok": True}})
    result = orca.launch("lane-a", "codex", "xhigh", str(orca.brief))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"lane-a unsupervised task=task_a dispatch=ctx_a terminal=term_codex worktree={orca.worktree}"
    [send] = orca.calls("terminal send")
    assert flag(send, "--terminal") == "term_codex"
    assert str(orca.brief) in flag(send, "--text") and "--enter" in send


def test_an_incident_readiness_timeout_sends_the_spec_to_its_own_terminal(orca):
    orca.healthy(agent="codex")
    orca.reply("orchestration worker-start", {"rc": 1, "out": {"ok": True, "result": {"state": "failed", "failedStage": "agent_readiness", "taskId": "task_a", "dispatchId": "ctx_a"}}})
    orca.reply("terminal send", {"rc": 0, "out": {"ok": True}})
    result = orca.launch("lane-a", "incident", "xhigh", str(orca.brief))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"lane-a unsupervised task=task_a dispatch=ctx_a terminal=term_a worktree={orca.worktree}"
    [send] = orca.calls("terminal send")
    assert flag(send, "--terminal") == "term_a"


@pytest.mark.parametrize("model", ["codex", "incident"])
@pytest.mark.parametrize("prompt", ["Update available!", "Skip until next version"])
def test_a_readiness_timeout_at_an_update_prompt_never_sends_the_spec(orca, model, prompt):
    orca.healthy(agent="codex", screen=prompt)
    orca.reply("orchestration worker-start", {"rc": 1, "out": {"ok": True, "result": {"state": "failed", "failedStage": "agent_readiness", "taskId": "task_a", "dispatchId": "ctx_a", "effects": [{"kind": "terminal", "role": "agent", "id": "term_codex"}]}}})
    orca.reply("terminal send", SENT)
    result = orca.launch("lane-a", model, "xhigh", str(orca.brief))
    terminal = "term_codex" if model == "codex" else "term_a"
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.splitlines() == [f"lane-a failed agent_readiness terminal={terminal} update prompt: '[\"❯\",\"{prompt}\"]'"]
    [read] = orca.calls("terminal read")
    assert read == ["terminal", "read", "--terminal", terminal, "--screen", "--json"]
    assert orca.calls("terminal send") == []


def test_an_update_prompt_failure_quotes_one_line_of_at_most_300_screen_characters(orca):
    orca.healthy(agent="codex")
    prompt = "\nUpdate available!\t\tSkip until next version\r\nRelease notes: https://github.com/openai/codex/releases/latest\n" + "x" * 320
    orca.reply("terminal read", {"rc": 0, "out": {"ok": True, "result": {"terminal": {"tail": prompt}}}})
    orca.reply("orchestration worker-start", {"rc": 1, "out": {"ok": True, "result": {"state": "failed", "failedStage": "agent_readiness", "taskId": "task_a", "dispatchId": "ctx_a"}}})
    orca.reply("terminal send", SENT)
    result = orca.launch("lane-a", "incident", "xhigh", str(orca.brief))
    assert result.returncode == 1, result.stdout + result.stderr
    [line] = result.stdout.splitlines()
    prefix = "lane-a failed agent_readiness terminal=term_a update prompt: '"
    assert line.startswith(prefix)
    assert line.endswith("'")
    quoted = line.removeprefix(prefix).removesuffix("'")
    assert len(quoted) == 300
    assert quoted.startswith(" Update available! Skip until next version Release notes: https://github.com/openai/codex/releases/latest ")
    assert orca.calls("terminal send") == []


def test_relaunch_retries_the_recorded_dispatch_in_the_existing_worktree(orca):
    orca.healthy()
    orca.check_out()
    orca.receipts.mkdir()
    (orca.receipts / "lane-a.json").write_text(json.dumps({"result": {"taskId": "task_old", "dispatchId": "ctx_old"}}))
    orca.reply("orchestration task-list", tasks("task_other"), tasks("task_old"))
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    assert orca.calls("worktree create") == []
    assert [flag(call, "--status") for call in orca.calls("orchestration task-list")] == ["failed", "blocked"]
    [start] = orca.calls("orchestration worker-start")
    assert flag(start, "--task") == "task_old"
    assert flag(start, "--retry-of") == "ctx_old"
    assert "--spec" not in start


def test_relaunch_after_the_recorded_task_completed_starts_a_fresh_task(orca):
    orca.healthy()
    orca.check_out()
    orca.receipts.mkdir()
    (orca.receipts / "lane-a.json").write_text(json.dumps({"result": {"taskId": "task_old", "dispatchId": "ctx_old"}}))
    orca.reply("orchestration task-list", tasks("task_other"))
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"lane-a ready task=task_a dispatch=ctx_a terminal=term_a worktree={orca.worktree}"
    [start] = orca.calls("orchestration worker-start")
    assert "--task" not in start
    assert "--retry-of" not in start
    assert flag(start, "--task-title") == "v3-lane-a"
    assert json.loads((orca.receipts / "lane-a.json").read_text())["result"]["taskId"] == "task_a"


def test_a_dropped_worktree_create_that_never_registers_is_created_again_after_the_wait(orca):
    orca.healthy()
    orca.env["ORCA_LAUNCH_WORKTREE_SECONDS"] = "8"
    orca.reply(
        "worktree create",
        {"rc": 1, "out": "connection lost"},
        {"rc": 0, "out": {"ok": True, "result": {"worktree": {"path": str(orca.worktree)}}}, "mkdir": str(orca.worktree)},
    )
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(orca.calls("worktree create")) == 2
    assert orca.sleeps()[:2] == ["4", "4"]


@pytest.mark.parametrize(
    "lost",
    [
        {"rc": 1, "out": {"ok": False, "error": {"code": "runtime_unavailable", "message": "The Orca runtime closed the connection before responding."}}},
        {"rc": 0, "out": "Error: socket hang up"},
    ],
)
def test_a_worktree_create_that_lost_its_response_is_awaited_not_repeated(orca, lost):
    orca.healthy()
    orca.check_out()
    miss = {"rc": 1, "out": ""}
    orca.reply("worktree create", {**lost, "mkdir": str(orca.worktree)})
    orca.reply("worktree show", miss, miss, miss, {"rc": 0, "out": {"ok": True, "result": {"worktree": {"path": str(orca.worktree)}}}})
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(orca.calls("worktree create")) == 1
    assert orca.sleeps()[:2] == ["4", "4"]
    assert result.stdout.strip().endswith(f"worktree={orca.worktree}")


def test_a_worktree_whose_create_lost_its_response_waits_for_orca_to_fill_its_index(orca):
    orca.healthy()
    orca.check_out(index="empty")
    orca.reply("worktree show", MISSING, registered(orca))
    orca.reply("worktree create", {"rc": 1, "out": {"ok": False, "error": {"code": "runtime_unavailable", "message": "closed"}}})
    (orca.state / "on_sleep").write_text(f"git -C {orca.worktree} read-tree HEAD\n")
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    assert orca.sleeps()[:1] == ["4"]
    assert len(orca.calls("terminal create")) == 1


def test_a_worktree_whose_index_never_fills_fails_before_any_terminal_opens(orca):
    orca.healthy()
    orca.env["ORCA_LAUNCH_WORKTREE_SECONDS"] = "4"
    orca.check_out(index="empty")
    orca.reply("worktree show", MISSING, registered(orca))
    orca.reply("worktree create", {"rc": 1, "out": {"ok": False, "error": {"code": "runtime_unavailable", "message": "closed"}}})
    result = orca.launch()
    assert result.returncode == 1
    assert result.stdout.strip() == f"lane-a failed worktree checkout: {orca.worktree} registered, but its index still differs from HEAD after 4s; rolled back worktree={orca.worktree}"
    assert orca.calls("terminal create") == []


def test_a_listed_worktree_that_is_no_checkout_fails_at_once_and_drops_its_receipt(orca):
    orca.healthy()
    orca.worktree.mkdir()
    orca.receipts.mkdir()
    (orca.receipts / "lane-a.worktree").write_text(f"{orca.worktree}\n")
    result = orca.launch()
    assert result.returncode == 1
    assert result.stdout.strip() == f"lane-a failed worktree {orca.worktree}: Orca lists it, but it is not a git checkout; remove the directory before relaunching"
    assert orca.calls("worktree create") == []
    assert orca.calls("terminal create") == []
    assert orca.worktree.is_dir()
    assert not (orca.receipts / "lane-a.worktree").exists()


def test_a_lost_create_whose_git_add_was_undone_removes_the_directory_it_left(orca):
    orca.healthy()
    orca.env["ORCA_LAUNCH_WORKTREE_SECONDS"] = "4"
    orca.check_out(index="empty")
    orca.reply("worktree show", MISSING, registered(orca), registered(orca), MISSING)
    orca.reply("worktree create", {"rc": 1, "out": {"ok": False, "error": {"code": "runtime_unavailable", "message": "closed"}}})
    (orca.state / "on_sleep").write_text(f"rm -rf {orca.worktree}/.git\n")
    result = orca.launch()
    assert result.returncode == 1
    assert result.stdout.strip().endswith(f"; rolled back worktree={orca.worktree}")
    assert orca.calls("worktree rm") == []
    assert not orca.worktree.exists()
    assert not (orca.receipts / "lane-a.worktree").exists()


def test_a_create_that_prints_no_handle_polls_for_its_terminal_every_four_seconds(orca):
    orca.healthy()
    orca.reply("terminal create", TIMED_OUT, {"rc": 0, "out": {"ok": True, "result": {"terminal": {"handle": "term_dup"}}}})
    orca.reply("terminal list", bare(), bare(), bare(), listing("claude"))
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(orca.calls("terminal create")) == 1
    assert orca.sleeps()[:3] == ["4", "4", "4"]
    assert flag(orca.calls("orchestration worker-start")[0], "--terminal") == "term_a"


def test_a_worktree_that_never_registers_fails_the_launch_after_four_creates(orca):
    orca.healthy()
    orca.env["ORCA_LAUNCH_WORKTREE_SECONDS"] = "4"
    orca.reply("worktree create", {"rc": 1, "out": "runtime_unavailable"})
    result = orca.launch()
    assert result.returncode == 1
    assert result.stdout.strip() == "lane-a failed worktree create: runtime_unavailable"
    assert len(orca.calls("worktree create")) == 4
    assert orca.calls("terminal create") == []


def test_launch_links_a_brief_whose_pointer_passes_500_characters(orca):
    orca.healthy()
    brief = orca.root / ("x" * 250) / "lane-a.full.md"
    brief.parent.mkdir()
    brief.write_text("# brief\n")
    result = orca.launch("lane-a", "opus", "high", str(brief))
    assert result.returncode == 0, result.stdout + result.stderr
    spec = flag(orca.calls("orchestration worker-start")[0], "--spec")
    link = Path(spec.split("read ")[1].split(" in full")[0])
    assert len(spec) <= 500
    assert link.parent == Path(orca.env["HOME"]) / ".claude"
    assert len(link.name) == 8
    assert link.is_symlink()
    assert link.resolve() == brief.resolve()


def test_launch_leaves_a_short_pointer_unlinked(orca):
    orca.healthy()
    assert orca.launch().returncode == 0
    assert not (Path(orca.env["HOME"]) / ".claude").exists()


def test_launch_refuses_a_worktree_path_that_alone_passes_500_characters(orca):
    orca.healthy()
    orca.env["ORCA_LAUNCH_ROOT"] = str(orca.root / ("x" * 500))
    result = orca.launch()
    assert result.returncode == 1
    assert "over 500" in result.stdout
    assert orca.calls() == []


def test_launch_fails_when_the_receipt_is_not_ready(orca):
    orca.healthy(state="failed")
    result = orca.launch()
    assert result.returncode == 1
    assert result.stdout.startswith("lane-a failed worker-start state=failed")


UNOBSERVED = {"rc": 1, "out": {"ok": True, "result": {"state": "outcome_unknown", "stage": "turn_start_unobserved", "taskId": "task_a", "dispatchId": "ctx_a"}}}


def shown(activity: str, state: str = "start_unknown") -> dict:
    return {"rc": 0, "out": {"ok": True, "result": {"worker": {"state": state}, "projection": {"stage": {"activity": activity}}}}}


@pytest.mark.parametrize("started", [shown("working"), shown("done", state="succeeded")])
def test_an_unobserved_turn_start_launches_once_worker_show_proves_the_worker_started(orca, started):
    orca.healthy()
    orca.reply("orchestration worker-start", UNOBSERVED)
    orca.reply("orchestration worker-show", shown("unknown"), started)
    result = orca.launch()
    assert result.returncode == 0, result.stdout
    assert result.stdout.startswith("lane-a ready task=task_a dispatch=ctx_a terminal=term_a ")
    assert [flag(call, "--dispatch") for call in orca.calls("orchestration worker-show")] == ["ctx_a", "ctx_a"]
    assert (orca.receipts / "lane-a.terminal").read_text().strip() == "term_a"


def test_an_unobserved_turn_start_that_never_starts_fails_naming_the_dispatch_and_keeps_it(orca):
    orca.healthy()
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "8"
    orca.reply("orchestration worker-start", UNOBSERVED)
    orca.reply("orchestration worker-show", shown("unknown"))
    result = orca.launch()
    assert result.returncode == 1
    assert result.stdout.startswith("lane-a failed worker-start outcome_unknown dispatch=ctx_a terminal=term_a ")
    assert "worker-abandon --dispatch ctx_a" in result.stdout
    assert "rolled back" not in result.stdout
    assert len(orca.calls("orchestration worker-show")) == 3
    assert orca.calls("terminal close") == []
    assert json.loads((orca.receipts / "lane-a.json").read_text())["result"]["dispatchId"] == "ctx_a"


UNCONFIGURED = {"rc": 1, "out": {"ok": False, "error": {"code": "agent_unconfigured", "message": "Terminal term_a is not running a recognized agent."}}}


def test_an_idle_agent_orca_does_not_recognize_yet_is_started_again(orca):
    orca.healthy()
    orca.reply("orchestration worker-start", UNCONFIGURED, UNCONFIGURED, {"rc": 0, "out": {"ok": True, "result": {"state": "ready", "taskId": "task_a", "dispatchId": "ctx_a"}}})
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(orca.calls("orchestration worker-start")) == 3
    assert orca.sleeps() == ["4", "4"]


def test_an_agent_orca_never_recognizes_rolls_back_at_the_boot_ceiling(orca):
    orca.healthy()
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "8"
    orca.reply("orchestration worker-start", UNCONFIGURED)
    result = orca.launch()
    assert result.returncode == 1
    assert len(orca.calls("orchestration worker-start")) == 3
    assert result.stdout.strip().endswith(f"; rolled back terminal=term_a worktree={orca.worktree}")


def test_launch_fails_when_the_terminal_is_not_in_bypass_mode(orca):
    orca.healthy(screen="⏸ plan mode on (shift+tab to cycle)")
    result = orca.launch()
    assert result.returncode == 1
    assert "shift-tab" in result.stdout
    assert len(orca.calls("terminal read")) == 10


def test_launch_starts_the_worker_once_orca_sees_its_agent_idle(orca):
    orca.healthy()
    orca.reply("terminal wait", NOT_IDLE, IDLE)
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    waits = orca.calls("terminal wait")
    assert [(flag(call, "--terminal"), flag(call, "--for"), flag(call, "--timeout-ms")) for call in waits] == [
        ("term_a", "tui-idle", "60000"),
        ("term_a", "tui-idle", "120000"),
    ]
    assert orca.sleeps() == []
    assert [" ".join(call[:2]) for call in orca.calls()][-3:] == ["terminal wait", "orchestration worker-start", "terminal read"]


def test_an_agent_identity_without_an_idle_agent_never_reaches_worker_start(orca):
    orca.healthy()
    orca.reply("terminal wait", NOT_IDLE)
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "10"
    result = orca.launch()
    assert result.returncode == 1
    assert result.stdout.strip() == (
        f"lane-a failed boot terminal=term_a: orca terminal wait --for tui-idle reads timeout: timeout after 10s; rolled back terminal=term_a worktree={orca.worktree}"
    )
    assert [flag(call, "--timeout-ms") for call in orca.calls("terminal wait")] == ["3333", "6667"]
    assert orca.calls("orchestration worker-start") == []
    assert orca.calls("terminal send") == []


def test_an_agent_blocked_on_a_startup_prompt_fails_naming_the_prompt(orca):
    orca.healthy()
    blocked = {"rc": 1, "out": {"ok": True, "result": {"wait": {"handle": "term_a", "condition": "tui-idle", "satisfied": False, "status": "running", "exitCode": None, "blockedReason": "agent-trust-workspace"}}}}
    orca.reply("terminal wait", blocked)
    orca.reply("terminal read", screen("yasyf@mac ~/v3-lane-a-base> env CLAUDE_LONG_RUNNING_LANE=lane-a claude", "Do you trust the files in this folder?"))
    result = orca.launch()
    assert result.returncode == 1
    assert "boot terminal=term_a: orca terminal wait --for tui-idle reads agent-trust-workspace after 180s;" in result.stdout
    assert orca.calls("terminal send") == []
    assert orca.calls("orchestration worker-start") == []


def test_an_agent_that_exited_is_not_idle(orca):
    orca.healthy()
    exited = {"rc": 0, "out": {"ok": True, "result": {"wait": {"handle": "term_a", "condition": "tui-idle", "satisfied": True, "status": "exited", "exitCode": 1}}}}
    orca.reply("terminal wait", exited)
    orca.reply("terminal read", screen("yasyf@mac ~/v3-lane-a-base> env CLAUDE_LONG_RUNNING_LANE=lane-a claude", "yasyf@mac ~/v3-lane-a-base>"))
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "12"
    result = orca.launch()
    assert result.returncode == 1
    assert "reads status=exited after 12s" in result.stdout
    assert orca.calls("orchestration worker-start") == []


def screen(*lines: str, source: str = "screen") -> dict:
    return {"rc": 0, "out": {"ok": True, "result": {"terminal": {"source": source, "tail": list(lines)}}}}


@pytest.mark.parametrize(("model", "agent"), [("opus", "claude"), ("incident", "codex")])
def test_a_startup_command_orca_dropped_is_typed_into_the_terminal_once(orca, model, agent):
    orca.healthy(agent=agent)
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "40"
    orca.reply("terminal create", TIMED_OUT)
    orca.reply("terminal list", bare(), listing(None))
    orca.reply("terminal wait", NOT_IDLE, IDLE)
    orca.reply("terminal read", screen("Welcome to fish", "yasyf@mac ~/v3-lane-a-base>"), screen("❯", "⏵⏵ bypass permissions on (shift+tab to cycle)"))
    orca.reply("terminal send", SENT)
    result = orca.launch("lane-a", model, "high", str(orca.brief))
    assert result.returncode == 0, result.stdout + result.stderr
    [create] = orca.calls("terminal create")
    [send] = orca.calls("terminal send")
    assert flag(send, "--terminal") == "term_a"
    assert flag(send, "--text") == flag(create, "--command")
    assert "--enter" in send
    steps = [" ".join(call[:2]) for call in orca.calls()]
    assert steps.index("terminal send") < steps.index("orchestration worker-start")


def test_a_typed_command_that_starts_no_agent_fails_the_launch_before_worker_start(orca):
    orca.healthy(agent=None)
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "12"
    orca.reply("terminal read", screen("yasyf@mac ~/v3-lane-a-base>"))
    orca.reply("terminal send", SENT)
    result = orca.launch()
    assert result.returncode == 1
    assert result.stdout.strip() == (
        "lane-a failed boot terminal=term_a: orca terminal wait --for tui-idle reads timeout: timeout after 12s; the command was typed into the terminal once;"
        f" rolled back terminal=term_a worktree={orca.worktree}"
    )
    assert len(orca.calls("terminal send")) == 1
    assert orca.calls("orchestration worker-start") == []


def test_a_failed_command_send_fails_the_launch_before_worker_start(orca):
    orca.healthy(agent=None)
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "12"
    orca.reply("terminal read", screen("yasyf@mac ~/v3-lane-a-base>"))
    orca.reply("terminal send", {"rc": 1, "out": "runtime_unavailable"})
    result = orca.launch()
    assert result.returncode == 1
    assert result.stdout.strip() == f"lane-a failed command send terminal=term_a after Orca dropped the startup command; rolled back terminal=term_a worktree={orca.worktree}"
    assert orca.calls("orchestration worker-start") == []


def test_a_command_line_wrapped_across_screen_rows_is_not_typed_again(orca):
    orca.healthy(agent=None)
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "12"
    orca.reply("terminal read", screen("yasyf@mac ~/v3-lane-a-base> env CLAUDE_LONG_RUNN", "ING_LANE=lane-a claude --model claude-opus-5-5"))
    result = orca.launch()
    assert result.returncode == 1
    assert result.stdout.strip().endswith(f"after 12s; rolled back terminal=term_a worktree={orca.worktree}")
    assert orca.calls("terminal send") == []


@pytest.mark.parametrize("read", [{"rc": 1, "out": ""}, screen("yasyf@mac ~/v3-lane-a-base>", source="screen-unavailable")])
def test_no_command_is_typed_when_the_screen_cannot_be_read(orca, read):
    orca.healthy(agent=None)
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "12"
    orca.reply("terminal read", read)
    result = orca.launch()
    assert result.returncode == 1
    assert orca.calls("terminal send") == []


def test_no_command_is_typed_when_the_agent_idles_within_a_third_of_the_boot_ceiling(orca):
    orca.healthy()
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "180"
    assert orca.launch().returncode == 0
    assert [flag(call, "--timeout-ms") for call in orca.calls("terminal wait")] == ["60000"]
    assert len(orca.calls("terminal read")) == 1
    assert orca.calls("terminal send") == []


def test_every_terminal_list_is_scoped_to_the_lanes_worktree(orca):
    orca.healthy()
    assert orca.launch().returncode == 0
    lists = orca.calls("terminal list")
    assert len(lists) == 1
    assert all(flag(call, "--worktree") == f"path:{orca.worktree}" for call in lists)


def test_a_create_that_prints_no_handle_adopts_the_terminal_it_created(orca):
    orca.healthy()
    orca.reply("terminal create", {"rc": 0, "out": "Error: socket hang up"}, {"rc": 0, "out": {"ok": True, "result": {"terminal": {"handle": "term_dup"}}}})
    orca.reply("terminal list", bare(), listing(None), listing("claude"))
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"lane-a ready task=task_a dispatch=ctx_a terminal=term_a worktree={orca.worktree}"
    assert len(orca.calls("terminal create")) == 1
    assert flag(orca.calls("orchestration worker-start")[0], "--terminal") == "term_a"
    assert (orca.receipts / "lane-a.terminal.json").read_text() == "Error: socket hang up"


def test_a_relaunch_never_adopts_a_terminal_that_predates_the_create(orca):
    orca.healthy()
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
    orca.reply("terminal create", {"rc": 0, "out": ""}, {"rc": 0, "out": {"ok": True, "result": {"terminal": {"handle": "term_b"}}}})
    orca.reply("terminal list", listing("claude", "term_a"), listing("claude", "term_a"), listing("claude", "term_a", "term_b"))
    result = orca.launch()
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(orca.calls("terminal create")) == 2
    assert flag(orca.calls("orchestration worker-start")[0], "--terminal") == "term_b"


def test_a_failed_create_reports_its_output_after_three_attempts(orca):
    orca.healthy()
    orca.reply("terminal create", {"rc": 1, "out": "runtime_unavailable"})
    orca.reply("terminal list", bare())
    result = orca.launch()
    assert result.returncode == 1
    assert result.stdout.strip() == f"lane-a failed terminal create: runtime_unavailable; rolled back worktree={orca.worktree}"
    assert len(orca.calls("terminal create")) == 3


def test_an_incident_lane_waits_for_its_codex_to_idle(orca):
    orca.healthy(agent=None)
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
    result = orca.launch("lane-a", "incident", "xhigh", str(orca.brief))
    assert result.returncode == 1
    assert "boot terminal=term_a: orca terminal wait --for tui-idle reads timeout" in result.stdout
    assert orca.calls("orchestration worker-start") == []


def test_a_failed_start_keeps_its_dispatch_for_the_relaunch(orca):
    orca.healthy(state="failed")
    assert orca.launch().returncode == 1
    orca.healthy()
    orca.reply("orchestration task-list", tasks("task_a"))
    assert orca.launch().returncode == 0
    first, second = orca.calls("orchestration worker-start")
    assert "--spec" in first
    assert flag(second, "--task") == "task_a"
    assert flag(second, "--retry-of") == "ctx_a"


def test_relaunch_reuses_the_worktree_orca_created(orca):
    elsewhere = orca.root / "elsewhere" / "lane-a"
    orca.healthy()
    orca.reply("worktree create", {"rc": 0, "out": {"ok": True, "result": {"worktree": {"path": str(elsewhere)}}}, "mkdir": str(elsewhere)})
    first = orca.launch()
    assert first.stdout.strip().endswith(f"worktree={elsewhere}")
    assert f"Worktree {elsewhere}," in flag(orca.calls("orchestration worker-start")[0], "--spec")
    orca.healthy()
    assert orca.launch().returncode == 0
    assert len(orca.calls("worktree create")) == 1
    assert flag(orca.calls("terminal create")[1], "--worktree") == f"path:{elsewhere}"


def test_launch_points_at_the_absolute_brief(orca):
    orca.healthy()
    result = subprocess.run(
        [str(SCRIPTS / "orca-launch.sh"), "lane-a", "opus", "high", "specs/lane-a.full.md"],
        env=orca.env, capture_output=True, text=True, cwd=orca.root,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"read {orca.brief.resolve()} in full" in flag(orca.calls("orchestration worker-start")[0], "--spec")


def test_launch_rejects_an_unknown_effort_in_one_line(orca):
    result = orca.launch("lane-a", "opus", "extreme", str(orca.brief))
    assert result.returncode == 1
    assert result.stdout.strip() == "lane-a failed effort extreme unknown: use low, medium, high, xhigh, or max"
    assert orca.calls() == []


def test_launch_rejects_an_unknown_model_in_one_line(orca):
    result = orca.launch("lane-a", "gemini", "xhigh", str(orca.brief))
    assert result.returncode == 1
    assert result.stdout.strip() == "lane-a failed model gemini unknown: use opus, sonnet, fable, claude-*, sol, codex, incident, astra, or gpt-*"
    assert orca.calls() == []


def test_an_astra_lane_starts_gpt_6_astra_on_the_codex_agent(orca):
    orca.healthy()
    orca.reply("orchestration worker-start", {"rc": 0, "out": {"ok": True, "result": {"state": "ready", "taskId": "task_a", "dispatchId": "ctx_a", "effects": [{"kind": "terminal", "role": "agent", "id": "term_codex"}]}}})
    result = orca.launch("lane-a", "astra", "xhigh", str(orca.brief))
    assert result.returncode == 0, result.stdout + result.stderr
    [start] = orca.calls("orchestration worker-start")
    assert (flag(start, "--agent"), flag(start, "--model")) == ("codex", "gpt-6-astra")


def message(msg_id: str, kind: str, sender: str, subject: str, body: str) -> dict:
    return {"id": msg_id, "type": kind, "from_handle": sender, "subject": subject, "body": body}


def test_check_prints_each_non_heartbeat_message_and_the_delivery(orca):
    orca.receipts.mkdir()
    (orca.receipts / "lane-a.terminal").write_text("term_a\n")
    orca.reply(
        "orchestration check",
        {
            "rc": 0,
            "out": {
                "ok": True,
                "result": {
                    "runId": "run_1",
                    "deliveryId": "delivery_2",
                    "messages": [
                        message("msg_1", "heartbeat", "term_a", "alive", ""),
                        message("msg_2", "worker_done", "term_a", "done", "Opened #12.\nGreen."),
                        message("msg_3", "question", "term_b", "scope", "A or B?"),
                    ],
                    "timedOut": False,
                    "connectionLost": False,
                },
            },
        },
    )
    result = orca.run("orca-check.sh", "--", "--terminal", "term_root", "--ack", "delivery_1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "msg_2 worker_done lane-a done: Opened #12. Green.",
        "msg_3 question term_b scope: A or B?",
        "delivery delivery_2 heartbeats=1",
    ]
    [check] = orca.calls("orchestration check")
    assert flag(check, "--ack") == "delivery_1"
    assert flag(check, "--types") == "worker_done,escalation,question"
    assert flag(check, "--timeout-ms") == "60000"
    assert "--wait" in check


@pytest.mark.parametrize("args", [("--json",), ("--ack", "delivery_1")])
def test_check_rejects_removed_runner_modes(orca, args):
    result = orca.run("orca-check.sh", *args)
    assert result.returncode == 2
    assert result.stderr.startswith("usage: orca-check.sh [--peek] [-- <orca check args>...]")
    assert orca.calls() == []


def test_check_prints_timeout_for_an_empty_wait(orca):
    orca.reply("orchestration check", {"rc": 0, "out": {"ok": True, "result": {"runId": "run_1", "messages": [], "timedOut": True}}})
    result = orca.run("orca-check.sh")
    assert result.stdout.strip() == "timeout"


def test_check_retries_a_lost_connection_once(orca):
    orca.reply(
        "orchestration check",
        {"rc": 0, "out": {"ok": True, "result": {"runId": "run_1", "messages": [], "connectionLost": True}}},
        {"rc": 0, "out": {"ok": True, "result": {"runId": "run_1", "messages": [], "timedOut": True}}},
    )
    result = orca.run("orca-check.sh")
    assert result.stdout.strip() == "timeout"
    assert orca.sleeps() == ["30"]


def test_check_retries_an_unavailable_runtime(orca):
    orca.reply(
        "orchestration check",
        {"rc": 1, "out": {"ok": False, "error": {"code": "runtime_unavailable", "message": "socket closed"}}},
        {"rc": 0, "out": {"ok": True, "result": {"runId": "run_1", "messages": [], "timedOut": True}}},
    )
    result = orca.run("orca-check.sh")
    assert result.stdout.strip() == "timeout"
    assert orca.sleeps() == ["30"]


def test_check_returns_to_the_caller_after_one_retry(orca):
    orca.reply("orchestration check", {"rc": 1, "out": ""})
    result = orca.run("orca-check.sh")
    assert result.returncode == 1
    assert result.stdout.strip() == "connection-lost"
    assert len(orca.calls("orchestration check")) == 2


def test_check_reports_an_orca_error_without_retrying(orca):
    orca.reply("orchestration check", {"rc": 1, "out": {"ok": False, "error": {"code": "consumer_fenced", "message": "not bound"}}})
    result = orca.run("orca-check.sh")
    assert result.returncode == 1
    assert result.stdout.strip() == "error consumer_fenced: not bound"
    assert orca.sleeps() == []


def test_check_peek_reads_without_waiting(orca):
    orca.reply("orchestration check", {"rc": 0, "out": {"ok": True, "result": {"runId": "run_1", "messages": [message("msg_9", "dispatch", "term_root", "note", "hi")]}}})
    result = orca.run("orca-check.sh", "--peek", "--", "--terminal", "term_me")
    assert result.stdout.strip() == "msg_9 dispatch term_root note: hi"
    [check] = orca.calls("orchestration check")
    assert check[2:] == ["--peek", "--terminal", "term_me", "--json"]


def minutes_ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def stale_lane(orca: Orca, status: str, completed_at: str | None = None) -> None:
    orca.receipts.mkdir(exist_ok=True)
    (orca.receipts / "lane-a.terminal").write_text("term_a\n")
    (orca.receipts / "lane-a.json").write_text(json.dumps({"result": {"taskId": "task_a", "dispatchId": "ctx_a"}}))
    (orca.receipts / "lane-a.worktree.json").write_text(json.dumps({"ok": True}))
    orca.reply("orchestration worker-show", {"rc": 0, "out": {"ok": True, "result": {"dispatch": {"id": "ctx_a", "status": status, "completedAt": completed_at}}}})


def unread(msg_id: str, minutes: int) -> dict:
    return {**message(msg_id, "dispatch", "term_desk", "R1", "restack"), "created_at": minutes_ago(minutes), "read": 0}


def test_stale_flags_an_in_progress_dispatch_with_an_old_unread_message(orca):
    stale_lane(orca, "dispatched")
    orca.reply("orchestration check", {"rc": 0, "out": {"ok": True, "result": {"messages": [unread("msg_old", 30), unread("msg_new", 2)]}}})
    result = orca.run("orca-check.sh", "--stale")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["STALE lane-a 30m unread msg_old"]
    [show] = orca.calls("orchestration worker-show")
    assert flag(show, "--dispatch") == "ctx_a"
    [check] = orca.calls("orchestration check")
    assert check[2:] == ["--terminal", "term_a", "--peek", "--json"]


def test_stale_flags_every_message_and_inbox_route_to_a_completed_dispatch(orca):
    stale_lane(orca, "completed", minutes_ago(90).replace("Z", ".645Z"))
    orca.reply("orchestration check", {"rc": 0, "out": {"ok": True, "result": {"messages": [unread("msg_new", 2)]}}})
    inbox = orca.root / "orca-desk.md"
    inbox.write_text("R1 msg_1 lane-a: done before\nR2 msg_2 lane-b: other lane\nR3 prompt lane-a: restack onto dev\n")
    (orca.root / "orca-desk.md.cursor").write_text("1\n")
    result = orca.run("orca-check.sh", "--stale", "--inbox", str(inbox))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["STALE lane-a 2m completed msg_new", "STALE lane-a 90m completed R3"]


def test_stale_skips_a_terminal_receipt_without_its_json_and_sweeps_the_rest(orca):
    stale_lane(orca, "dispatched")
    (orca.receipts / "codex-smoke.terminal").write_text("term_smoke\n")
    (orca.receipts / "codex-smoke.json.foreign").write_text("{}")
    orca.reply("orchestration check", {"rc": 0, "out": {"ok": True, "result": {"messages": [unread("msg_old", 30)]}}})
    result = orca.run("orca-check.sh", "--stale")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["skip codex-smoke: no receipt json", "STALE lane-a 30m unread msg_old"]


def test_stale_reports_an_orca_error(orca):
    stale_lane(orca, "dispatched")
    orca.reply("orchestration worker-show", {"rc": 1, "out": {"ok": False, "error": {"code": "not_found", "message": "no dispatch"}}})
    result = orca.run("orca-check.sh", "--stale")
    assert result.returncode == 1
    assert result.stdout.strip() == "error not_found: no dispatch"


def test_inbox_needs_stale(orca):
    assert orca.run("orca-check.sh", "--inbox", "x").returncode == 2



def test_the_plugin_bin_launches_every_script_by_name():
    assert sorted(path.name for path in BIN.iterdir()) == sorted(path.name for path in SCRIPTS.iterdir() if path.suffix in (".py", ".sh") and (path.read_text().startswith("#!") or "__main__" in path.read_text()))
    for launcher in BIN.iterdir():
        assert os.access(launcher, os.X_OK)
        assert launcher.read_text().endswith(f'"$(dirname "$0")/../skills/long-running/scripts/{launcher.name}" "$@"\n')


@pytest.mark.parametrize("script", ["ledger.py", "bus.py", "drive.py"])
def test_a_cli_runs_by_name_from_any_directory_with_the_plugin_bin_on_path(script, tmp_path):
    env = {**os.environ, "PATH": f"{BIN}:{os.environ['PATH']}"}
    result = subprocess.run([script, "--help"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"usage: {script}")


def test_a_launch_that_fails_before_worker_start_closes_its_tab_and_removes_the_worktree_it_created(orca):
    orca.healthy(agent=None)
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
    assert orca.launch().returncode == 1
    assert orca.calls("terminal close")[-1] == ["terminal", "close", "--terminal", "term_a", "--tab", "--json"]
    [remove] = orca.calls("worktree rm")
    assert remove == ["worktree", "rm", "--worktree", f"path:{orca.worktree}", "--force", "--json"]
    assert not (orca.receipts / "lane-a.worktree").exists()
    assert [" ".join(call[:2]) for call in orca.calls()][-2:] == ["terminal close", "worktree rm"]


def test_a_relaunch_that_fails_keeps_the_worktree_it_found(orca):
    orca.healthy(agent=None)
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
    orca.check_out()
    result = orca.launch()
    assert result.stdout.strip().endswith("; rolled back terminal=term_a")
    assert orca.calls("worktree create") == []
    assert orca.calls("worktree rm") == []


def test_a_rollback_orca_refuses_is_named_in_the_failure(orca):
    orca.healthy(agent=None)
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
    orca.reply("worktree rm", {"rc": 1, "out": {"ok": False, "error": {"code": "worktree_locked"}}})
    result = orca.launch()
    assert result.stdout.strip().endswith(f"; rolled back terminal=term_a; rollback left worktree={orca.worktree}")
    assert (orca.receipts / "lane-a.worktree").read_text().strip() == str(orca.worktree)


@pytest.mark.parametrize(("state", "screen"), [("failed", "❯"), ("ready", "plan mode on")])
def test_nothing_is_rolled_back_once_worker_start_has_run(orca, state, screen):
    orca.healthy(state=state, screen=screen)
    assert orca.launch().returncode == 1
    assert orca.calls("terminal close") == []
    assert orca.calls("worktree rm") == []


def test_a_worktree_that_was_only_briefly_unregistered_is_never_removed(orca):
    orca.healthy(agent=None)
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
    orca.env["ORCA_LAUNCH_WORKTREE_SECONDS"] = "8"
    orca.check_out()
    found = {"rc": 0, "out": {"ok": True, "result": {"worktree": {"path": str(orca.worktree)}}}}
    orca.reply("worktree show", {"rc": 1, "out": ""}, found)
    orca.reply("worktree create", {"rc": 1, "out": {"ok": False, "error": {"code": "worktree_exists"}}})
    result = orca.launch()
    assert result.stdout.strip().endswith("; rolled back terminal=term_a")
    assert orca.calls("worktree rm") == []


def test_an_adopted_terminal_is_named_not_closed(orca):
    orca.healthy(agent=None)
    orca.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
    orca.reply("terminal create", TIMED_OUT)
    orca.reply("terminal list", bare(), listing(None))
    result = orca.launch()
    assert result.stdout.strip().endswith(f"; rolled back worktree={orca.worktree}; rollback left terminal=term_a")
    assert ["terminal", "close", "--terminal", "term_a", "--tab", "--json"] not in orca.calls("terminal close")


FENCED = {
    "rc": 1,
    "out": {
        "ok": False,
        "error": {
            "code": "consumer_fenced",
            "message": "worker-start requires the coordinator terminal currently bound to the Task Run. Orchestration mutation request ID: req-1.",
            "data": {"orchestrationRequestId": "req-1"},
        },
    },
}


@pytest.mark.parametrize("current", [{"run": None}, {"run": {"id": "run_other"}}])
def test_a_terminal_that_does_not_coordinate_the_run_creates_nothing_and_names_the_rebind(orca, current):
    orca.healthy()
    orca.reply("orchestration run-current", {"rc": 0, "out": {"ok": True, "result": current}})
    result = orca.launch()
    assert result.returncode == 1
    bound = (current["run"] or {}).get("id", "no Run")
    assert result.stdout == f"lane-a failed coordinator binding: terminal term_root coordinates {bound}, not run_1 (" + json.dumps({"ok": True, "result": current}) + "); from the coordinator's Orca terminal run: orca orchestration run-use --id run_1\n"
    assert [" ".join(call[:2]) for call in orca.calls()] == ["orchestration run-current"]


def test_a_fenced_worker_start_rolls_back_its_terminal_and_worktree_and_quotes_orcas_error(orca):
    orca.healthy()
    orca.reply("orchestration worker-start", FENCED)
    result = orca.launch()
    assert result.returncode == 1
    assert result.stdout == (
        "lane-a failed worker-start terminal=term_a: consumer_fenced: worker-start requires the coordinator terminal currently bound to the Task Run. "
        "Orchestration mutation request ID: req-1.; terminal term_root is not the Run's coordinator: from the coordinator's Orca terminal run "
        f"orca orchestration run-use --id run_1; rolled back terminal=term_a worktree={orca.worktree}\n"
    )
    assert orca.calls("terminal close")[-1] == ["terminal", "close", "--terminal", "term_a", "--tab", "--json"]
    assert orca.calls("worktree rm") == [["worktree", "rm", "--worktree", f"path:{orca.worktree}", "--force", "--json"]]
    assert not (orca.receipts / "lane-a.json").exists()
    assert not (orca.receipts / "lane-a.terminal").exists()


def test_a_fenced_relaunch_closes_its_terminal_and_keeps_the_worktree_and_receipt(orca):
    orca.healthy(state="failed")
    assert orca.launch().returncode == 1
    orca.healthy()
    orca.reply("orchestration worker-start", FENCED)
    result = orca.launch()
    assert result.stdout.strip().endswith("; rolled back terminal=term_a")
    assert orca.calls("worktree rm") == []
    assert json.loads((orca.receipts / "lane-a.json").read_text())["result"]["dispatchId"] == "ctx_a"


def test_a_worker_start_that_may_have_dispatched_names_what_it_left(orca):
    orca.healthy()
    orca.reply("orchestration worker-start", TIMED_OUT)
    result = orca.launch()
    assert result.stdout == f"lane-a failed worker-start terminal=term_a: runtime_error: Timed out waiting for terminal handle after creation; rollback left terminal=term_a worktree={orca.worktree}\n"
    assert orca.calls("worktree rm") == []
    assert ["terminal", "close", "--terminal", "term_a", "--tab", "--json"] not in orca.calls("terminal close")


def test_an_orca_error_printed_across_lines_fails_the_launch_on_one_line(orca):
    orca.healthy()
    unavailable = {"ok": False, "error": {"code": "runtime_unavailable", "message": "Could not read Orca runtime metadata. Start the Orca app first."}}
    orca.reply("worktree create", {"rc": 1, "out": json.dumps(unavailable, indent=2)})
    orca.env["ORCA_LAUNCH_WORKTREE_SECONDS"] = "0"
    result = orca.launch()
    assert result.stdout == "lane-a failed worktree create: runtime_unavailable: Could not read Orca runtime metadata. Start the Orca app first.\n"


def test_a_worker_start_that_printed_a_dispatch_beside_a_refusal_rolls_nothing_back(orca):
    orca.healthy()
    dispatched = json.dumps({"ok": True, "result": {"state": "starting"}})
    orca.reply("orchestration worker-start", {"rc": 1, "out": dispatched + "\n" + json.dumps(FENCED["out"])})
    result = orca.launch()
    assert result.stdout.strip().endswith(f"; rollback left terminal=term_a worktree={orca.worktree}")
    assert orca.calls("worktree rm") == []
