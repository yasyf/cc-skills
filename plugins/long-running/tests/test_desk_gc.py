"""desk-runner.py gc against a fake Orca Run on a fake clock: only a settled launch's created terminal is ever closed."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from long_running import migration

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/desk-runner.py"
if "desk_runner" not in sys.modules:
    spec = importlib.util.spec_from_file_location("desk_runner", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["desk_runner"] = module
    spec.loader.exec_module(module)
runner_module = sys.modules["desk_runner"]

IDLE = ["⏺ Done.", "─" * 80, "❯", "─" * 80, "  ⏵⏵ bypass permissions on (shift+tab to cycle)"]
ASKING = ["⏺ Should I open the PR now?", "─" * 80, "❯", "─" * 80, "  ⏵⏵ bypass permissions on (shift+tab to cycle)"]
BUSY = ["⏺ Running the build… (12s · esc to interrupt)", "─" * 80, "❯", "─" * 80, "  ⏵⏵ bypass permissions on (shift+tab to cycle)"]


class FakeShell(runner_module.Shell):
    def __init__(self, root: Path):
        self.root = root
        self.receipts = root / ".claude/scratch/orca-launch/run_1"
        self.receipts.mkdir(parents=True)
        self.clock = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
        self.calls: list[list[str]] = []
        self.workers: list[dict] = []
        self.terminals: dict[str, dict] = {}
        self.screens: dict[str, list[str]] = {}
        self.stale: set[str] = set()
        self.closed: list[tuple[str, bool]] = []
        self.mtimes: dict[Path, datetime] = {}

    def now(self):
        return self.clock

    def stat(self, path):
        return (0, int(self.mtimes[path].timestamp() * 1e9)) if path in self.mtimes else None

    def run(self, argv, stdin=None, env=None):
        self.calls.append(argv)
        assert argv[0] == "orca" and argv[-1] == "--json"
        return runner_module.Done(0, json.dumps(self.orca(argv[1:-1])), "")

    def orca(self, argv: list[str]) -> dict:
        if argv[:2] == ["orchestration", "worker-list"]:
            assert argv[2:4] == ["--run", "run_1"]
            return {"ok": True, "result": {"workers": self.workers, "page": {}}}
        handle = argv[argv.index("--terminal") + 1] if "--terminal" in argv else ""
        if handle in self.stale:
            return {"ok": False, "error": {"code": "terminal_handle_stale", "message": "stale"}}
        if argv[:2] == ["terminal", "show"]:
            return {"ok": True, "result": {"terminal": self.terminals[handle]}}
        if argv[:2] == ["terminal", "wait"]:
            return {"ok": True, "result": {"wait": {"satisfied": True, "status": "running"}}}
        if argv[:2] == ["terminal", "read"]:
            return {"ok": True, "result": {"terminal": {"source": "screen", "tail": self.screens[handle]}}}
        if argv[:2] == ["terminal", "list"]:
            worktree = argv[argv.index("--worktree") + 1].removeprefix("path:")
            return {"ok": True, "result": {"terminals": [{"handle": name, **row} for name, row in self.terminals.items() if row["worktreePath"] == worktree]}}
        if argv[:2] == ["terminal", "close"]:
            self.closed.append((handle, "--tab" in argv))
            self.terminals[handle]["connected"] = False
            return {"ok": True, "result": {}}
        raise AssertionError(f"unexpected orca {argv}")

    def terminal(self, handle: str, worktree: str, screen: list[str], quiet_minutes: int = 30, tab: str = "", leaf: str = "leaf_1") -> None:
        output = self.clock - timedelta(minutes=quiet_minutes)
        self.terminals[handle] = {"connected": True, "worktreePath": worktree, "lastOutputAt": int(output.timestamp() * 1000), "tabId": tab or f"tab_{handle}", "leafId": leaf}
        self.screens[handle] = screen

    def launched(self, lane: str, handle: str, worktree: str, pane: str = "", dispatch: str = "") -> None:
        terminal = {"handle": handle, **({"paneKey": pane} if pane else {})}
        (self.receipts / f"{lane}.terminal.json").write_text(json.dumps({"ok": True, "result": {"terminal": terminal}}))
        (self.receipts / f"{lane}.worktree").write_text(f"{worktree}\n")
        if dispatch:
            (self.receipts / f"{lane}.json").write_text(json.dumps({"result": {"taskId": "task_1", "dispatchId": dispatch}}))
            (self.receipts / f"{lane}.terminal").write_text(f"{handle}\n")

    def dispatch(self, dispatch: str, status: str, handle: str, worktree: str = "/wt/elsewhere") -> None:
        self.workers.append({"dispatchId": dispatch, "dispatchStatus": status, "agentTerminalHandle": handle, "resource": {"worktreeId": f"repo_1::{worktree}"}})


@pytest.fixture
def shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeShell:
    monkeypatch.setenv("HOME", str(tmp_path))
    return FakeShell(tmp_path)


@pytest.fixture
def config(tmp_path: Path, shell: FakeShell) -> Path:
    migration.initialize(tmp_path / "store")
    path = tmp_path / "runner.json"
    path.write_text(
        json.dumps(
            {
                "store": str(tmp_path / "store"),
                "drive": "d1",
                "view": str(tmp_path / "desk-runner.md"),
                "orca": {"run": "run_1", "desk_inbox": str(tmp_path / "inbox/orca-desk.md"), "briefs": {"repo": str(tmp_path / "checkout")}},
            }
        )
    )
    return path


def gc(shell: FakeShell, config: Path, capsys: pytest.CaptureFixture, *flags: str) -> tuple[int, list[str]]:
    code = runner_module.main(["gc", "--config", str(config), *flags], shell)
    return code, capsys.readouterr().out.splitlines()


def launch(tmp_path: Path, shell: FakeShell, config: Path, lane: str, status: str, written: timedelta = timedelta(minutes=1)) -> None:
    runner = runner_module.Runner(shell, runner_module.Config.load(config), runner_module.actions.Store(tmp_path / "store"))
    container = f"{runner_module.LANE_PREFIX}{lane}"
    runner.book.ensure(container, runner_module.ORCA_DESK)
    runner.book.accept(container, "R7", "launch", json.dumps({"prior": ""}), "root", None)
    if status in ("started", "failed"):
        runner.book.attempt(container, lambda incident: incident.start("R7", shell.clock))
    if status == "failed":
        runner.book.attempt(container, lambda incident: incident.fail("R7", "worker-start: consumer_fenced"))
        shell.mtimes[shell.receipts / f"{lane}.terminal.json"] = shell.clock + written
        shell.mtimes[runner.launch_log(container, "R7")] = shell.clock + timedelta(minutes=2)


def test_a_settled_dispatchs_idle_terminal_is_closed_with_its_tab_and_its_receipt_dropped(shell, config, capsys):
    shell.launched("api-fix", "term_a", "/wt/api-fix", dispatch="ctx_a")
    shell.dispatch("ctx_a", "completed", "term_a")
    shell.terminal("term_a", "/wt/api-fix", IDLE)
    code, lines = gc(shell, config, capsys)
    assert code == 0
    assert lines == ["close term_a dispatch ctx_a is completed", "close=1"]
    assert shell.closed == [("term_a", True)]
    assert not (shell.receipts / "api-fix.terminal.json").exists()
    assert (shell.receipts / "api-fix.json").exists()


def test_a_live_dispatchs_terminal_is_never_probed_or_closed(shell, config, capsys):
    shell.launched("api-fix", "term_a", "/wt/api-fix", dispatch="ctx_a")
    shell.dispatch("ctx_a", "running", "term_a")
    shell.terminal("term_a", "/wt/api-fix", IDLE)
    _, lines = gc(shell, config, capsys)
    assert lines == ["keep term_a dispatch ctx_a is running", "keep=1"]
    assert shell.closed == []
    assert [call for call in shell.calls if call[1] == "terminal"] == []


def test_a_dispatch_missing_from_the_run_is_kept(shell, config, capsys):
    shell.launched("api-fix", "term_a", "/wt/api-fix", dispatch="ctx_a")
    shell.terminal("term_a", "/wt/api-fix", IDLE)
    _, lines = gc(shell, config, capsys)
    assert lines[0] == "keep term_a dispatch ctx_a is not in the Run"
    assert shell.closed == []


@pytest.mark.parametrize(("screen", "quiet"), [(BUSY, 30), (ASKING, 30), (IDLE, 9)])
def test_a_settled_terminal_that_is_busy_asking_or_recently_active_is_kept(shell, config, capsys, screen, quiet):
    shell.launched("api-fix", "term_a", "/wt/api-fix", dispatch="ctx_a")
    shell.dispatch("ctx_a", "failed", "term_a")
    shell.terminal("term_a", "/wt/api-fix", screen, quiet_minutes=quiet)
    _, lines = gc(shell, config, capsys)
    assert lines == [f"keep term_a {runner_module.NOT_IDLE}", "keep=1"]
    assert shell.closed == []
    assert (shell.receipts / "api-fix.terminal.json").exists()


def test_quiet_counts_from_the_fake_clock(shell, config, capsys):
    shell.launched("api-fix", "term_a", "/wt/api-fix", dispatch="ctx_a")
    shell.dispatch("ctx_a", "completed", "term_a")
    shell.terminal("term_a", "/wt/api-fix", IDLE, quiet_minutes=9)
    assert gc(shell, config, capsys)[1][0] == f"keep term_a {runner_module.NOT_IDLE}"
    shell.clock += timedelta(minutes=2)
    assert gc(shell, config, capsys)[1][0] == "close term_a dispatch ctx_a is completed"


def test_a_failed_launchs_orphan_is_closed_once_the_runner_settled_the_launch(tmp_path, shell, config, capsys):
    shell.launched("sol-fix", "term_o", "/wt/sol-fix")
    shell.terminal("term_o", "/wt/sol-fix", IDLE)
    launch(tmp_path, shell, config, "sol-fix", "failed")
    _, lines = gc(shell, config, capsys)
    assert lines == ["close term_o launch sol-fix has no dispatch", "close=1"]
    assert shell.closed == [("term_o", True)]


@pytest.mark.parametrize("status", ["accepted", "started"])
def test_an_orphan_of_a_launch_in_flight_is_kept(tmp_path, shell, config, capsys, status):
    shell.launched("sol-fix", "term_o", "/wt/sol-fix")
    shell.terminal("term_o", "/wt/sol-fix", IDLE)
    launch(tmp_path, shell, config, "sol-fix", status)
    _, lines = gc(shell, config, capsys)
    assert lines == [f"keep term_o launch R7 of sol-fix is {status}", "keep=1"]
    assert shell.closed == []


def test_an_orphan_no_runner_launch_settled_is_kept(shell, config, capsys):
    shell.launched("hand-lane", "term_h", "/wt/hand-lane")
    shell.terminal("term_h", "/wt/hand-lane", IDLE)
    _, lines = gc(shell, config, capsys)
    assert lines == ["keep term_h launch hand-lane has no dispatch, and no settled launch of it by this runner wrote its receipt", "keep=1"]
    assert shell.closed == []


def test_an_orphan_a_dispatch_references_is_kept(tmp_path, shell, config, capsys):
    shell.launched("sol-fix", "term_o", "/wt/sol-fix")
    shell.terminal("term_o", "/wt/sol-fix", IDLE)
    shell.dispatch("ctx_z", "running", "term_o")
    launch(tmp_path, shell, config, "sol-fix", "failed")
    _, lines = gc(shell, config, capsys)
    assert lines[0] == "keep term_o dispatch ctx_z references it"
    assert shell.closed == []


def test_a_stale_handle_closes_the_connected_terminal_carrying_its_launch_pane(shell, config, capsys):
    shell.launched("api-fix", "term_old", "/wt/api-fix", pane="tab_9:leaf_2", dispatch="ctx_a")
    shell.dispatch("ctx_a", "completed", "term_old")
    shell.stale.add("term_old")
    shell.terminal("term_new", "/wt/api-fix", IDLE, tab="tab_9", leaf="leaf_2")
    shell.terminal("term_other", "/wt/api-fix", IDLE, tab="tab_3", leaf="leaf_1")
    _, lines = gc(shell, config, capsys)
    assert lines == ["close term_new dispatch ctx_a is completed, replacing stale term_old", "close=1"]
    assert shell.closed == [("term_new", True)]


def test_a_stale_handle_never_rebinds_in_a_worktree_an_unsettled_dispatch_works_in(shell, config, capsys):
    shell.launched("api-fix", "term_old", "/wt/api-fix", pane="tab_9:leaf_2", dispatch="ctx_a")
    shell.dispatch("ctx_a", "completed", "term_old")
    shell.dispatch("ctx_b", "running", "term_stale_b", worktree="/wt/api-fix")
    shell.stale.add("term_old")
    shell.terminal("term_new", "/wt/api-fix", IDLE, tab="tab_9", leaf="leaf_2")
    _, lines = gc(shell, config, capsys)
    assert lines[0] == "keep term_old stale handle, and an unsettled dispatch works in its worktree"
    assert shell.closed == []


def test_a_terminal_a_newer_unsettled_dispatch_also_names_is_kept(shell, config, capsys):
    shell.launched("api-fix", "term_a", "/wt/api-fix", dispatch="ctx_old")
    shell.dispatch("ctx_new", "running", "term_a")
    shell.dispatch("ctx_old", "completed", "term_a")
    shell.terminal("term_a", "/wt/api-fix", IDLE)
    _, lines = gc(shell, config, capsys)
    assert lines[0] == "keep term_a serves unsettled dispatch ctx_new, ctx_old"
    assert shell.closed == []


def test_an_orphan_receipt_written_after_the_runners_launch_ended_is_kept(tmp_path, shell, config, capsys):
    shell.launched("sol-fix", "term_o", "/wt/sol-fix")
    shell.terminal("term_o", "/wt/sol-fix", IDLE)
    launch(tmp_path, shell, config, "sol-fix", "failed", written=timedelta(minutes=5))
    _, lines = gc(shell, config, capsys)
    assert lines[0] == "keep term_o launch sol-fix has no dispatch, and no settled launch of it by this runner wrote its receipt"
    assert shell.closed == []


def test_a_terminal_with_no_known_output_time_is_kept(shell, config, capsys):
    shell.launched("api-fix", "term_a", "/wt/api-fix", dispatch="ctx_a")
    shell.dispatch("ctx_a", "completed", "term_a")
    shell.terminal("term_a", "/wt/api-fix", IDLE)
    shell.terminals["term_a"]["lastOutputAt"] = None
    _, lines = gc(shell, config, capsys)
    assert lines[0] == f"keep term_a {runner_module.NOT_IDLE}"


def test_a_stale_handle_with_no_matching_pane_is_kept(shell, config, capsys):
    shell.launched("api-fix", "term_old", "/wt/api-fix", pane="tab_9:leaf_2", dispatch="ctx_a")
    shell.dispatch("ctx_a", "completed", "term_old")
    shell.stale.add("term_old")
    shell.terminal("term_new", "/wt/api-fix", IDLE, tab="tab_3", leaf="leaf_1")
    _, lines = gc(shell, config, capsys)
    assert lines[0] == "keep term_old stale handle, and no connected terminal carries its launch pane"
    assert shell.closed == []


def test_a_pane_sharing_its_tab_closes_without_the_tab(shell, config, capsys):
    shell.launched("api-fix", "term_a", "/wt/api-fix", dispatch="ctx_a")
    shell.dispatch("ctx_a", "completed", "term_a")
    shell.terminal("term_a", "/wt/api-fix", IDLE, tab="tab_1", leaf="leaf_1")
    shell.terminal("term_b", "/wt/api-fix", BUSY, tab="tab_1", leaf="leaf_2")
    gc(shell, config, capsys)
    assert shell.closed == [("term_a", False)]


def test_an_already_closed_terminal_only_drops_its_receipt(shell, config, capsys):
    shell.launched("api-fix", "term_a", "/wt/api-fix", dispatch="ctx_a")
    shell.dispatch("ctx_a", "completed", "term_a")
    shell.terminal("term_a", "/wt/api-fix", IDLE)
    shell.terminals["term_a"]["connected"] = False
    _, lines = gc(shell, config, capsys)
    assert lines == ["drop term_a dispatch ctx_a is completed, and its terminal is already closed", "drop=1"]
    assert shell.closed == []
    assert not (shell.receipts / "api-fix.terminal.json").exists()


def test_a_dry_run_closes_and_drops_nothing(shell, config, capsys):
    shell.launched("api-fix", "term_a", "/wt/api-fix", dispatch="ctx_a")
    shell.dispatch("ctx_a", "completed", "term_a")
    shell.terminal("term_a", "/wt/api-fix", IDLE)
    _, lines = gc(shell, config, capsys, "--dry-run")
    assert lines == ["close term_a dispatch ctx_a is completed", "close=1"]
    assert shell.closed == []
    assert (shell.receipts / "api-fix.terminal.json").exists()


def test_no_receipts_reclaims_nothing(shell, config, capsys):
    assert gc(shell, config, capsys) == (0, ["nothing to reclaim"])
