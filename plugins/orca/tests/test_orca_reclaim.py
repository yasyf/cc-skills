from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "bin/orca-reclaim.py"
loader = SourceFileLoader("orca_gc", str(SCRIPT))
orca_gc = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
sys.modules[loader.name] = orca_gc
loader.exec_module(orca_gc)

NOW = 10_000_000_000
WT = "/w/repo/v3-lane-base"
WT2 = "/w/repo/v3-lane-next"
REPOSITORY = "/w/repo/.git"
REMOTE = orca_gc.Remote(heads=frozenset({"a" * 40}), landed=frozenset())
UNPAUSED = orca_gc.Pause(SCRIPT)
BORDER = "─" * 80
CLAUDE_DONE = [
    "  The build 2210 JSON I pulled is at /tmp/b2210-evidence.json.",
    "✻ Baked for 8m 57s · done 1:17 PM",
    BORDER,
    "❯",
    BORDER,
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
    "                                                       Now using usage credits",
    "                                                                 168428 tokens",
    "     current: 2.1.287 · latest: 2.1.288 ✔ Update installed · Restart to update",
    "                                        new task? /clear to save 168.4k tokens",
]


def row(dispatch: str, status: str = "completed", terminal_state: str = "retained", ownership: str | None = "external", reason: str | None = "external_terminal", terminal: str | None = None, worktree: str = WT) -> dict:
    resource = None if ownership is None else {"ownershipState": ownership, "retainedReason": reason, "worktreeId": f"repo::{worktree}"}
    return {
        "dispatchId": dispatch,
        "dispatchStatus": status,
        "terminalState": terminal_state,
        "agentTerminalHandle": terminal if terminal is not None else f"term_{dispatch}",
        "resource": resource,
        "projection": {"workspace": {"id": f"repo::{worktree}"}},
    }


def shell(handle: str, worktree: str = WT, quiet_ms: int = 3_600_000, title: str | None = None) -> orca_gc.Terminal:
    return orca_gc.Terminal(handle, None, title if title is not None else f"~/.o/w/m/m/{Path(worktree).name}", NOW - quiet_ms)


def checkout(path: str = WT, **overrides) -> orca_gc.Checkout:
    return orca_gc.Checkout(**{"path": path, "dirty": False, "branch": "v3/lane", "head": "a" * 40, "on_trunk": False, "repository": REPOSITORY, **overrides})


class FakeOrca(orca_gc.Orca):
    def __init__(self, rows: list[dict], idle: dict[str, bool | None] | None = None, terminals: dict[str, list] | None = None, refusal: str | None = None, stops: dict[str, tuple[str | None, str, dict]] | None = None, abandons: dict[str, tuple[str | None, str, dict]] | None = None):
        self.rows = rows
        self.stops = stops or {}
        self.abandons = abandons or {}
        self.refusal = refusal
        self.idle_by = {handle: list(reads) if isinstance(reads, list) else [reads] for handle, reads in (idle or {}).items()}
        self.terminals = terminals or {}
        self.holders: dict[str, str] = {}
        self.panes: dict[str, frozenset[str]] = {}
        self.stale: set[str] = set()
        self.disconnected: set[str] = set()
        self.known: set[str] = set()
        self.calls: list[tuple[str, ...]] = []
        self.paired: dict[str, orca_gc.Checkout | str] = {}

    def workers(self, run: str) -> list:
        return [orca_gc.Worker.of(r) for r in self.rows]

    def idle(self, terminal: str, environment: str | None = None) -> bool | None:
        reads = self.idle_by.get(terminal, [True])
        return reads.pop(0) if len(reads) > 1 else reads[0]

    def terminals_in(self, worktree: str, environment: str | None = None) -> list:
        return self.terminals.get(worktree, [])

    def presence(self, terminal: str, environment: str | None = None) -> str:
        return orca_gc.STALE if terminal in self.stale else orca_gc.DISCONNECTED if terminal in self.disconnected else orca_gc.CONNECTED

    def stop(self, dispatch: str) -> tuple[str | None, str]:
        self.calls.append(("stop", dispatch))
        return self.fenced(self.stops[dispatch], dispatch)

    def abandon(self, dispatch: str) -> tuple[str | None, str]:
        self.calls.append(("abandon", dispatch))
        return self.fenced(self.abandons[dispatch], dispatch)

    def fenced(self, outcome: tuple[str | None, str, dict], dispatch: str) -> tuple[str | None, str]:
        state, detail, after = outcome
        self.rows = [{**r, **after} if r["dispatchId"] == dispatch else r for r in self.rows]
        return state, detail

    def release(self, dispatch: str) -> tuple[str, str]:
        self.calls.append(("release", dispatch))
        return "released", ""

    def close(self, terminal: str, environment: str | None = None) -> str | None:
        self.calls.append(("close", terminal, *filter(None, [environment])))
        self.terminals = {path: [t for t in listed if t.handle != terminal] for path, listed in self.terminals.items()}
        return None

    def remove(self, worktree: str, environment: str | None = None) -> str | None:
        self.calls.append(("remove", worktree, *filter(None, [environment])))
        return self.refusal

    def checkout(self, worktree: str, environment: str, git: orca_gc.Git) -> orca_gc.Checkout | str | None:
        return self.paired.get(worktree)

    def forget(self, environment: str) -> str | None:
        self.calls.append(("forget", environment))
        return None

    def registered(self, worktree: str) -> bool:
        return worktree in self.known

    def now_ms(self) -> int:
        return NOW

    def holder(self, worktree: str, panes: frozenset[str]) -> str | None:
        self.panes[worktree] = panes
        return self.holders.get(worktree)


class FakeGit(orca_gc.Git):
    def __init__(self, checkouts: dict[str, orca_gc.Checkout], remote: orca_gc.Remote = REMOTE, remotes: dict[str, orca_gc.Remote] | None = None, refusal: str | None = None):
        self.checkouts = checkouts
        self.remotes = {REPOSITORY: remote, **(remotes or {})}
        self.refusal = refusal
        self.removed: list[str] = []

    def on_trunk(self, head: str, repository: str) -> bool:
        return False

    def checkout(self, path: str):
        return self.checkouts.get(path)

    def repository(self) -> str:
        return REPOSITORY

    def remote(self, repository: str) -> orca_gc.Remote:
        return self.remotes[repository]

    def remove(self, worktree: str) -> str | None:
        self.removed.append(worktree)
        return self.refusal


SPRITE = orca_gc.Sprite("pool-abc", "pool-abc", "/s/ssh/pool-abc.ssh", "/home/sprite/repo", "env_abc")
SPRITE_WT = "repo_abc::/home/sprite/repo"


class FakeCcRemote(orca_gc.CcRemote):
    def __init__(self, hosted: dict[tuple[str, str], orca_gc.Sprite] | None = None, checkouts: dict[str, orca_gc.Checkout | str] | None = None, refusal: str | None = None):
        self.hosted = hosted or {}
        self.checkouts = checkouts or {}
        self.refusal = refusal
        self.destroyed: list[str] = []

    def sprites(self) -> dict[tuple[str, str], orca_gc.Sprite]:
        return self.hosted

    def checkout(self, sprite: orca_gc.Sprite) -> orca_gc.Checkout | str:
        return self.checkouts.get(sprite.workspace, checkout(sprite.root))

    def remote(self, slug: str) -> orca_gc.Remote:
        return REMOTE

    def destroy(self, sprite: orca_gc.Sprite) -> str | None:
        self.destroyed.append(sprite.workspace)
        return self.refusal


def sprite_row(dispatch: str, status: str = "completed") -> dict:
    return {
        "dispatchId": dispatch,
        "dispatchStatus": status,
        "terminalState": "retained",
        "agentTerminalHandle": "term_sprite",
        "resource": None,
        "projection": {"workspace": {"id": SPRITE_WT}},
    }


class RecordingOrca(orca_gc.Orca):
    def __init__(self, reply: dict, tabs: dict[str, str | None] | None = None):
        self.reply = reply
        self.tabs = tabs or {"term_a": "tab_1"}
        self.argv: list[tuple[str, ...]] = []

    def call(self, *args: str) -> dict:
        self.argv.append(args)
        if args[:2] == ("terminal", "show"):
            return {"ok": True, "result": {"terminal": {"tabId": self.tabs[args[3]], "worktreePath": WT}}}
        if args[:2] == ("terminal", "list"):
            return {"ok": True, "result": {"terminals": [{"handle": handle, "tabId": tab, "connected": True} for handle, tab in self.tabs.items()]}}
        return self.reply


def run_gc(orca, git, dry_run: bool = False, dispatches: set[str] | None = None, done: frozenset[str] = frozenset(), cc_remote=None, pause: orca_gc.Pause = UNPAUSED) -> tuple[dict, str]:
    out = io.StringIO()
    tally = orca_gc.gc("run_x", dispatches or set(), dry_run, orca, git, cc_remote or FakeCcRemote(), pause, out, ending=done)
    return dict(tally), out.getvalue()


class TerminalActionTest(unittest.TestCase):
    def action(self, data: dict, live: set[str] | None = None, idle: bool | None = True, ended: dict[str, str] | None = None):
        worker = orca_gc.Worker.of(data)
        return orca_gc.terminal_action(worker, live or set(), FakeOrca([], {worker.terminal: idle}), ended or {})

    def test_a_done_lanes_busy_terminal_is_ended(self):
        self.assertEqual(self.action(row("ctx_a", status="dispatched"), idle=False, ended={"ctx_a": "none"}), orca_gc.Action("end", "term_ctx_a", "dispatch ctx_a is done"))

    def test_a_terminal_worker_stop_closed_needs_nothing(self):
        self.assertIsNone(self.action(row("ctx_a", status="failed", ownership="owned", reason=None), ended={"ctx_a": "closed_agent_terminal"}))

    def test_a_done_lanes_terminal_orca_retained_on_abandon_is_ended(self):
        self.assertEqual(self.action(row("ctx_a", status="failed", terminal_state="active", ownership="owned", reason="user_requested"), ended={"ctx_a": "none"}).verb, "end")

    def test_owned_terminal_is_released_through_orca(self):
        self.assertEqual(self.action(row("ctx_a", terminal_state="reclaimable", ownership="owned", reason=None)), orca_gc.Action("release", "ctx_a"))

    def test_external_idle_terminal_is_closed(self):
        self.assertEqual(self.action(row("ctx_a")).verb, "close")

    def test_busy_external_terminal_is_kept(self):
        self.assertEqual(self.action(row("ctx_a"), idle=False), orca_gc.Action("keep", "term_ctx_a", "not idle: busy, prompt typed or asking, or output in the last 10 minutes"))

    def test_stale_handle_is_already_gone(self):
        self.assertIsNone(self.action(row("ctx_a"), idle=None))

    def test_terminal_reused_by_a_live_dispatch_is_kept(self):
        self.assertEqual(self.action(row("ctx_a"), live={"term_ctx_a"}).reason, "serves an unsettled dispatch")

    def test_user_retained_terminal_is_kept(self):
        self.assertEqual(self.action(row("ctx_a", ownership="owned", reason="user_requested")).reason, "retained: user_requested")

    def test_released_terminal_needs_nothing(self):
        self.assertIsNone(self.action(row("ctx_a", terminal_state="released", ownership="released", reason=None)))


class IdleScreenTest(unittest.TestCase):
    def test_finished_claude_at_an_empty_prompt_is_idle(self):
        self.assertTrue(orca_gc.idle_screen(CLAUDE_DONE))

    def test_codex_at_its_placeholder_prompt_is_idle(self):
        self.assertTrue(orca_gc.idle_screen(["Worked for 16m 40s • 1:24 PM", "› Ask Codex to do anything", "  GPT-6.1-Sol xhigh · ~/w"]))

    def test_a_running_turn_is_busy(self):
        self.assertFalse(orca_gc.idle_screen(["✶ Baking… (esc to interrupt)", *CLAUDE_DONE[2:]]))

    def test_typed_prompt_text_is_not_idle(self):
        self.assertFalse(orca_gc.idle_screen([line.replace("❯", "❯ rebase onto dev") for line in CLAUDE_DONE]))

    def test_an_unanswered_question_is_not_idle(self):
        self.assertFalse(orca_gc.idle_screen(["  Should I rebase onto dev?", *CLAUDE_DONE[1:]]))

    def test_a_screen_with_no_prompt_is_not_idle(self):
        self.assertFalse(orca_gc.idle_screen(["$ tail -f build.log", "line 1"]))


class WorktreeActionTest(unittest.TestCase):
    def test_clean_pushed_tree_with_an_idle_shell_is_removed(self):
        self.assertEqual(orca_gc.worktree_action(checkout(), [shell("term_sh")], {}, REMOTE, NOW), orca_gc.Action("remove", WT, shells=frozenset({"term_sh"})))

    def test_agent_terminal_without_a_dispatch_keeps_the_tree(self):
        orphan = orca_gc.Terminal("term_cc", "claude", "✳ Claude Code", 0)
        self.assertEqual(orca_gc.worktree_action(checkout(), [orphan], {}, REMOTE, NOW).verb, "keep")

    def test_shell_with_recent_output_keeps_the_tree(self):
        self.assertEqual(orca_gc.worktree_action(checkout(), [shell("term_sh", quiet_ms=1000)], {}, REMOTE, NOW).verb, "keep")

    def test_orcas_untitled_startup_shell_is_idle(self):
        self.assertEqual(orca_gc.worktree_action(checkout(), [shell("term_sh", title="")], {}, REMOTE, NOW).verb, "remove")

    def test_an_untitled_shell_with_recent_output_keeps_the_tree(self):
        self.assertEqual(orca_gc.worktree_action(checkout(), [shell("term_sh", title="", quiet_ms=1000)], {}, REMOTE, NOW).verb, "keep")

    def test_shell_running_a_command_keeps_the_tree(self):
        self.assertEqual(orca_gc.worktree_action(checkout(), [shell("term_sh", title="tail -f log")], {}, REMOTE, NOW).verb, "keep")

    def test_dirty_tree_is_kept(self):
        self.assertEqual(orca_gc.worktree_action(checkout(dirty=True), [], {}, REMOTE, NOW).reason, "uncommitted changes")

    def test_clean_pushed_tree_is_removed_while_its_pr_is_open(self):
        self.assertEqual(orca_gc.worktree_action(checkout(), [], {}, REMOTE, NOW).verb, "remove")

    def test_unpushed_head_keeps_the_tree(self):
        self.assertEqual(orca_gc.worktree_action(checkout(head="b" * 40), [], {}, REMOTE, NOW).reason, "unpushed commits on v3/lane")

    def test_landed_branch_whose_remote_head_is_deleted_is_removed(self):
        remote = orca_gc.Remote(frozenset(), frozenset({"b" * 40}))
        self.assertEqual(orca_gc.worktree_action(checkout(head="b" * 40), [], {}, remote, NOW).verb, "remove")

    def test_commits_after_a_landed_head_on_a_reused_branch_keep_the_tree(self):
        remote = orca_gc.Remote(frozenset(), frozenset({"b" * 40}))
        self.assertEqual(orca_gc.worktree_action(checkout(head="d" * 40), [], {}, remote, NOW).reason, "unpushed commits on v3/lane")

    def test_lane_that_never_committed_is_removed(self):
        self.assertEqual(orca_gc.worktree_action(checkout(head="c" * 40, on_trunk=True), [], {}, orca_gc.Remote(frozenset(), frozenset()), NOW).verb, "remove")


class GcTest(unittest.TestCase):
    def test_local_removals_run_one_at_a_time(self):
        class SlowOrca(FakeOrca):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.running, self.most = 0, 0
                self.counting = threading.Lock()

            def remove(self, worktree: str, environment: str | None = None) -> str | None:
                with self.counting:
                    self.running += 1
                    self.most = max(self.most, self.running)
                threading.Event().wait(0.05)
                with self.counting:
                    self.running -= 1
                return super().remove(worktree, environment)

        trees = [f"{WT}-{n}" for n in range(3)]
        terminals = {tree: [orca_gc.Terminal(f"term_ctx_{n}", "claude", "✳ lane", 0)] for n, tree in enumerate(trees)}
        orca = SlowOrca([row(f"ctx_{n}", worktree=tree) for n, tree in enumerate(trees)], terminals=terminals)
        with tempfile.TemporaryDirectory() as state, mock.patch.object(orca_gc, "REMOVING", Path(state) / "remove.lock"):
            tally, _ = run_gc(orca, FakeGit({tree: checkout(tree) for tree in trees}))
        self.assertEqual((tally, orca.most), ({"removed": 3}, 1))

    def test_finished_lanes_removal_stops_its_idle_terminal(self):
        orca = FakeOrca([row("ctx_a")], terminals={WT: [orca_gc.Terminal("term_ctx_a", "claude", "✳ lane", 0), shell("term_sh")]})
        tally, out = run_gc(orca, FakeGit({WT: checkout()}))
        self.assertEqual((tally, orca.calls), ({"removed": 1}, [("remove", WT)]))
        self.assertIn(f"removed {WT} stopping term_ctx_a", out)

    def test_a_tab_close_timeout_no_longer_strands_a_removable_tree(self):
        class Wedged(FakeOrca):
            def close(self, terminal: str, environment: str | None = None) -> str | None:
                self.calls.append(("close", terminal))
                return "terminal_tab_close_timeout"

        orca = Wedged([row("ctx_a", status="failed")], terminals={WT: [orca_gc.Terminal("term_ctx_a", "claude", "✳ lane", 0)]})
        tally, _ = run_gc(orca, FakeGit({WT: checkout()}))
        self.assertEqual((tally, orca.calls), ({"removed": 1}, [("remove", WT)]))

    def test_a_kept_trees_terminal_is_still_closed(self):
        orca = FakeOrca([row("ctx_a")], terminals={WT: [orca_gc.Terminal("term_ctx_a", "claude", "✳ lane", 0)]})
        tally, out = run_gc(orca, FakeGit({WT: checkout(dirty=True)}))
        self.assertEqual((tally, orca.calls), ({"keep": 1, "closed": 1}, [("close", "term_ctx_a")]))
        self.assertIn(f"keep {WT} uncommitted changes", out)

    def test_a_refused_removal_falls_back_to_closing_the_terminal(self):
        with tempfile.TemporaryDirectory() as tree:
            orca = FakeOrca([row("ctx_a", worktree=tree)], terminals={tree: [orca_gc.Terminal("term_ctx_a", "claude", "✳ lane", 0)]}, refusal="worktree_busy")
            tally, _ = run_gc(orca, FakeGit({tree: checkout(tree)}))
        self.assertEqual((tally, orca.calls), ({"keep": 1, "closed": 1}, [("remove", tree), ("close", "term_ctx_a")]))

    def test_a_removal_whose_reply_orca_lost_counts_as_removed_and_names_the_error(self):
        orca = FakeOrca([row("ctx_a")], terminals={WT: [shell("term_sh")]}, refusal="runtime_unavailable: The Orca runtime closed the connection before responding")
        tally, out = run_gc(orca, FakeGit({WT: checkout()}))
        self.assertEqual(tally, {"closed": 1, "removed": 1})
        self.assertIn(f"removed {WT}\nerror {WT} orca worktree rm answered runtime_unavailable: The Orca runtime closed the connection before responding, but its directory is gone\n", out)

    def test_a_terminal_that_wakes_before_the_removal_keeps_the_tree(self):
        orca = FakeOrca([row("ctx_a")], idle={"term_ctx_a": [True, False]}, terminals={WT: [orca_gc.Terminal("term_ctx_a", "claude", "✳ lane", 0)]})
        tally, out = run_gc(orca, FakeGit({WT: checkout()}))
        self.assertEqual((tally, orca.calls), ({"keep": 2}, []))
        self.assertIn(f"keep {WT} changed since the first read", out)

    def test_a_busy_settled_terminal_keeps_the_tree(self):
        orca = FakeOrca([row("ctx_a")], idle={"term_ctx_a": False}, terminals={WT: [orca_gc.Terminal("term_ctx_a", "claude", "✳ lane", NOW)]})
        tally, out = run_gc(orca, FakeGit({WT: checkout()}))
        self.assertEqual((tally, orca.calls), ({"keep": 2}, []))
        self.assertIn(f"keep {WT} 1 agent terminal(s) no settled dispatch owns", out)

    def test_dry_run_changes_nothing(self):
        orca = FakeOrca([row("ctx_a")], terminals={WT: [orca_gc.Terminal("term_ctx_a", "claude", "✳ lane", 0)]})
        git = FakeGit({WT: checkout()})
        tally, out = run_gc(orca, git, dry_run=True)
        self.assertEqual(tally, {"remove": 1})
        self.assertEqual(orca.calls, [])
        self.assertIn(f"would remove {WT} stopping term_ctx_a", out)

    def test_worktree_with_an_unsettled_dispatch_is_never_touched(self):
        orca = FakeOrca([row("ctx_a"), row("ctx_b", status="dispatched")])
        git = FakeGit({WT: checkout()})
        tally, _ = run_gc(orca, git)
        self.assertEqual((tally, orca.calls), ({"closed": 1}, [("close", "term_ctx_a")]))

    def test_dispatch_filter_limits_the_sweep(self):
        other = "/w/repo/v3-other-base"
        orca = FakeOrca([row("ctx_a"), row("ctx_b", worktree=other)])
        git = FakeGit({WT: checkout(), other: checkout(other)})
        run_gc(orca, git, dispatches={"ctx_a"})
        self.assertEqual(orca.calls, [("remove", WT), ("close", "term_ctx_a")])

    def test_done_lanes_busy_terminal_is_closed_before_its_tree_is_removed(self):
        orca = FakeOrca(
            [row("ctx_a", status="dispatched")],
            idle={"term_ctx_a": False},
            terminals={WT: [orca_gc.Terminal("term_ctx_a", "claude", "✳ lane", NOW), shell("term_sh")]},
            stops={"ctx_a": ("stop_unknown", "none", {})},
        )
        tally, out = run_gc(orca, FakeGit({WT: checkout()}), done=frozenset({"ctx_a"}))
        self.assertEqual(tally, {"stopped": 1, "closed": 1, "removed": 1})
        self.assertEqual(orca.calls, [("stop", "ctx_a"), ("close", "term_ctx_a"), ("remove", WT)])
        self.assertIn("closed term_ctx_a dispatch ctx_a is done", out)

    def test_done_lane_orca_already_fenced_is_abandoned_closed_and_removed(self):
        orca = FakeOrca(
            [row("ctx_a", status="dispatched", terminal_state="active", ownership="owned", reason=None)],
            idle={"term_ctx_a": False},
            terminals={WT: [orca_gc.Terminal("term_ctx_a", "codex", "", NOW)]},
            stops={"ctx_a": (None, "dispatch_inactive", {})},
            abandons={"ctx_a": ("abandoned", "none", {"dispatchStatus": "failed", "resource": {"ownershipState": "owned", "retainedReason": "user_requested", "worktreeId": f"repo::{WT}"}})},
        )
        tally, out = run_gc(orca, FakeGit({WT: checkout()}), done=frozenset({"ctx_a"}))
        self.assertEqual(tally, {"stopped": 1, "closed": 1, "removed": 1})
        self.assertEqual(orca.calls, [("stop", "ctx_a"), ("abandon", "ctx_a"), ("close", "term_ctx_a"), ("remove", WT)])
        self.assertIn("stopped ctx_a abandoned", out)

    def test_done_lane_on_an_unreachable_host_is_abandoned_and_says_so(self):
        orca = FakeOrca(
            [row("ctx_a", status="dispatched")],
            stops={"ctx_a": (None, "remote_runtime_unavailable", {})},
            abandons={"ctx_a": ("abandoned", "none", {"dispatchStatus": "failed"})},
        )
        orca.presence = lambda terminal, environment=None: orca_gc.RUNTIME_DOWN
        tally, out = run_gc(orca, FakeGit({WT: checkout(head="d" * 40)}), done=frozenset({"ctx_a"}))
        self.assertEqual((tally, orca.calls), ({"abandoned": 1, "keep": 2}, [("stop", "ctx_a"), ("abandon", "ctx_a")]))
        self.assertIn("abandoned ctx_a its host is unavailable, so its worker could keep running if the host comes back", out)
        self.assertIn("keep term_ctx_a its host is unavailable", out)
        self.assertIn("keep /w/repo/v3-lane-base unpushed commits on v3/lane", out)

    def test_refused_abandon_on_an_unreachable_host_keeps_the_lane(self):
        orca = FakeOrca(
            [row("ctx_a", status="dispatched")],
            stops={"ctx_a": (None, "remote_runtime_unavailable", {})},
            abandons={"ctx_a": (None, "worker_identity_changed", {})},
        )
        tally, out = run_gc(orca, FakeGit({WT: checkout()}), done=frozenset({"ctx_a"}))
        self.assertEqual((tally, orca.calls), ({"keep": 1}, [("stop", "ctx_a"), ("abandon", "ctx_a")]))
        self.assertIn("keep ctx_a worker-abandon worker_identity_changed", out)

    def test_done_dry_run_predicts_the_abandon_for_an_unavailable_host(self):
        gone = row("ctx_a", status="dispatched")
        gone["projection"] = {**gone["projection"], "liveness": {"verdict": "unverifiable", "reason": "host_unavailable"}}
        orca = FakeOrca([gone, row("ctx_b", status="dispatched", worktree="/w/repo/v3-other-base")])
        tally, out = run_gc(orca, FakeGit({}), dry_run=True, done=frozenset({"ctx_a", "ctx_b"}))
        self.assertEqual(orca.calls, [])
        self.assertEqual((tally["abandon"], tally["stop"]), (1, 1))
        self.assertIn("would abandon ctx_a its host is unavailable, so its worker could keep running if the host comes back", out)
        self.assertIn("would stop ctx_b", out)

    def test_done_lane_with_unpushed_work_closes_its_busy_terminal(self):
        orca = FakeOrca(
            [row("ctx_a", status="dispatched")],
            idle={"term_ctx_a": False},
            terminals={WT: [orca_gc.Terminal("term_ctx_a", "claude", "✳ lane", NOW)]},
            stops={"ctx_a": ("stop_unknown", "none", {})},
        )
        tally, out = run_gc(orca, FakeGit({WT: checkout(head="d" * 40)}), done=frozenset({"ctx_a"}))
        self.assertEqual((tally, orca.calls), ({"stopped": 1, "keep": 1, "closed": 1}, [("stop", "ctx_a"), ("close", "term_ctx_a")]))
        self.assertIn("closed term_ctx_a dispatch ctx_a is done", out)

    def test_done_lane_whose_terminal_worker_stop_closed_is_not_closed_again(self):
        orca = FakeOrca(
            [row("ctx_a", status="dispatched", ownership="owned", reason=None)],
            stops={"ctx_a": ("stopped", "closed_agent_terminal", {"dispatchStatus": "failed"})},
        )
        tally, _ = run_gc(orca, FakeGit({WT: checkout()}), done=frozenset({"ctx_a"}))
        self.assertEqual((tally, orca.calls), ({"stopped": 1, "removed": 1}, [("stop", "ctx_a"), ("remove", WT)]))

    def test_done_lane_that_already_settled_is_released(self):
        orca = FakeOrca([row("ctx_a", ownership="owned", reason=None)], stops={"ctx_a": ("succeeded", "none", {})})
        tally, _ = run_gc(orca, FakeGit({}), done=frozenset({"ctx_a"}))
        self.assertEqual((tally, orca.calls), ({"stopped": 1, "released": 1}, [("stop", "ctx_a"), ("release", "ctx_a")]))

    def test_refused_stop_leaves_the_live_lane_alone(self):
        orca = FakeOrca([row("ctx_a", status="dispatched")], stops={"ctx_a": (None, "worker_identity_changed", {})})
        tally, out = run_gc(orca, FakeGit({WT: checkout()}), done=frozenset({"ctx_a"}))
        self.assertEqual((tally, orca.calls), ({"keep": 1}, [("stop", "ctx_a")]))
        self.assertIn("keep ctx_a worker-stop worker_identity_changed", out)

    def test_refused_abandon_leaves_the_stopping_lane_alone(self):
        orca = FakeOrca([row("ctx_a", status="dispatched")], stops={"ctx_a": (None, "dispatch_inactive", {})}, abandons={"ctx_a": (None, "dispatch_inactive", {})})
        tally, out = run_gc(orca, FakeGit({WT: checkout()}), done=frozenset({"ctx_a"}))
        self.assertEqual((tally, orca.calls), ({"keep": 1}, [("stop", "ctx_a"), ("abandon", "ctx_a")]))
        self.assertIn("keep ctx_a worker-abandon dispatch_inactive", out)

    def test_done_lane_with_unpushed_work_keeps_its_tree(self):
        orca = FakeOrca([row("ctx_a", status="dispatched")], stops={"ctx_a": ("stop_unknown", "none", {})})
        tally, _ = run_gc(orca, FakeGit({WT: checkout(head="d" * 40)}), done=frozenset({"ctx_a"}))
        self.assertEqual((tally, orca.calls), ({"stopped": 1, "closed": 1, "keep": 1}, [("stop", "ctx_a"), ("close", "term_ctx_a")]))

    def test_done_dry_run_changes_nothing(self):
        orca = FakeOrca([row("ctx_a", status="dispatched")], idle={"term_ctx_a": False})
        tally, out = run_gc(orca, FakeGit({WT: checkout()}), dry_run=True, done=frozenset({"ctx_a"}))
        self.assertEqual((tally, orca.calls), ({"stop": 1, "end": 1, "remove": 1}, []))
        self.assertIn("would stop ctx_a", out)
        self.assertIn("would end term_ctx_a", out)

    def test_done_scopes_the_sweep_to_its_lane(self):
        other = "/w/repo/v3-other-base"
        orca = FakeOrca([row("ctx_a", status="dispatched"), row("ctx_b", worktree=other)], stops={"ctx_a": ("stop_unknown", "none", {})})
        run_gc(orca, FakeGit({WT: checkout(), other: checkout(other)}), done=frozenset({"ctx_a"}))
        self.assertEqual(orca.calls, [("stop", "ctx_a"), ("close", "term_ctx_a"), ("remove", WT)])

    def test_terminal_that_wakes_before_the_close_is_kept(self):
        orca = FakeOrca([row("ctx_a")], idle={"term_ctx_a": [True, False]})
        tally, out = run_gc(orca, FakeGit({}))
        self.assertEqual((tally, orca.calls), ({"keep": 1}, []))
        self.assertIn("changed since the first read", out)

    def test_orca_refusal_keeps_the_tree_and_says_why(self):
        with tempfile.TemporaryDirectory() as tree:
            orca = FakeOrca([row("ctx_a", worktree=tree)], refusal="worktree_dirty: uncommitted changes")
            tally, out = run_gc(orca, FakeGit({tree: checkout(tree)}))
        self.assertEqual(tally, {"closed": 1, "keep": 1})
        self.assertIn(f"keep {tree} worktree_dirty", out)

    def test_a_terminal_that_opens_before_the_removal_keeps_the_tree(self):
        class Opening(FakeOrca):
            def remove(self, worktree: str, environment: str | None = None) -> str | None:
                raise AssertionError("removed a tree with a new terminal")

        orca = Opening([row("ctx_a")], terminals={WT: [shell("term_sh")]})
        listed = iter([[shell("term_sh")], [shell("term_sh"), orca_gc.Terminal("term_new", "claude", "✳ new", NOW)]])
        orca.terminals_in = lambda worktree, environment=None: next(listed)
        tally, out = run_gc(orca, FakeGit({WT: checkout()}))
        self.assertEqual(tally, {"closed": 1, "keep": 1})
        self.assertIn(f"keep {WT} changed since the first read", out)

    def test_a_disconnected_handle_is_already_closed(self):
        orca = FakeOrca([row("ctx_a", terminal="term_old")], terminals={WT: [shell("term_sh")]})
        orca.disconnected.add("term_old")
        tally, out = run_gc(orca, FakeGit({WT: checkout()}))
        self.assertEqual((tally, orca.calls), ({"removed": 1}, [("remove", WT)]))
        self.assertNotIn("term_old", out)

    def test_done_lanes_disconnected_handle_gets_no_close(self):
        orca = FakeOrca([row("ctx_a", status="dispatched", terminal="term_old")], stops={"ctx_a": ("stop_unknown", "none", {})})
        orca.disconnected.add("term_old")
        tally, out = run_gc(orca, FakeGit({WT: checkout()}), done=frozenset({"ctx_a"}))
        self.assertEqual((tally, orca.calls), ({"stopped": 1, "removed": 1}, [("stop", "ctx_a"), ("remove", WT)]))
        self.assertNotIn("term_old", out)

    def test_a_worktree_with_one_unrecorded_connected_terminal_stays_kept(self):
        orca = FakeOrca([row("ctx_a", terminal="term_old")], terminals={WT: [orca_gc.Terminal("term_x", "claude", "✳ x", 0, "tab_2", "leaf_2")]})
        orca.stale.add("term_old")
        tally, out = run_gc(orca, FakeGit({WT: checkout()}))
        self.assertEqual((tally, orca.calls), ({"keep": 2}, []))
        self.assertIn(f"keep term_old {orca_gc.STALE_HANDLE}", out)
        self.assertIn(f"keep {WT} 1 agent terminal(s) no settled dispatch owns", out)

    def test_a_tree_a_live_process_holds_is_kept(self):
        orca = FakeOrca([row("ctx_a")])
        orca.holders[WT] = "live process script /s/runner.zsh names it"
        tally, out = run_gc(orca, FakeGit({WT: checkout()}))
        self.assertNotIn(("remove", WT), orca.calls)
        self.assertIn(f"keep {WT} live process script /s/runner.zsh names it", out)

    def test_a_live_runner_script_naming_the_tree_holds_it(self):
        with tempfile.TemporaryDirectory() as root:
            tree, runner = Path(root) / "stack-enqueue-preflight-speed", Path(root) / "desk"
            tree.mkdir()
            runner.mkdir()
            (runner / "runner.zsh").write_text(f"cd {tree.name} && stack-enqueue\n")
            orca = orca_gc.Orca()
            lsof = {("lsof", "-t", "+D", str(tree)): "", ("lsof", "-a", "-d", "cwd", "-Fn"): f"p1\nfcwd\nn{runner}\n"}

            def run(argv, **_):
                return type("Done", (), {"stdout": lsof.get(tuple(argv), "zsh ./runner.zsh\n")})()

            original = orca_gc.subprocess.run
            orca_gc.subprocess.run = run
            try:
                self.assertEqual(orca.holder(str(tree), frozenset()), f"live process script {runner / 'runner.zsh'} names it")
            finally:
                orca_gc.subprocess.run = original

    def test_a_cwd_under_library_is_never_opened(self):
        with tempfile.TemporaryDirectory() as root:
            tree, scanned = Path(root) / "v3-idle-base", Path(root) / "desk"
            protected = Path.home() / "Library" / "Containers" / "com.apple.mail" / "Data"
            lsof = {("lsof", "-t", "+D", str(tree)): "", ("lsof", "-a", "-d", "cwd", "-Fn"): f"p1\nfcwd\nn{protected}\np2\nfcwd\nn{scanned}\n"}

            def run(argv, **_):
                return type("Done", (), {"stdout": lsof.get(tuple(argv), "")})()

            with mock.patch.object(orca_gc.subprocess, "run", run), mock.patch.object(orca_gc.Path, "glob", autospec=True, return_value=()) as glob:
                self.assertIsNone(orca_gc.Orca().holder(str(tree), frozenset()))
            self.assertEqual({call.args[0] for call in glob.call_args_list}, {scanned})

    def test_a_cwd_under_the_per_user_temp_tree_is_never_opened(self):
        with tempfile.TemporaryDirectory() as root:
            tree, scanned = Path(root) / "v3-idle-base", Path(root) / "desk"
            protected = Path("/private/var/folders/ab/cd/T/session")
            lsof = {("lsof", "-t", "+D", str(tree)): "", ("lsof", "-a", "-d", "cwd", "-Fn"): f"p1\nfcwd\nn{protected}\np2\nfcwd\nn{scanned}\n"}

            def run(argv, **_):
                return type("Done", (), {"stdout": lsof.get(tuple(argv), "")})()

            with mock.patch.object(orca_gc.subprocess, "run", run), mock.patch.object(orca_gc.Path, "glob", autospec=True, return_value=()) as glob:
                self.assertIsNone(orca_gc.Orca().holder(str(tree), frozenset()))
            self.assertEqual({call.args[0] for call in glob.call_args_list}, {scanned})

    def test_a_removal_orca_drops_the_connection_on_keeps_a_tree_still_on_disk_and_names_it(self):
        with tempfile.TemporaryDirectory() as tree:
            orca = FakeOrca([row("ctx_a", worktree=tree)], refusal="runtime_unavailable: The Orca runtime closed the connection before responding")
            tally, out = run_gc(orca, FakeGit({tree: checkout(tree)}))
        self.assertEqual(tally, {"closed": 1, "keep": 1})
        self.assertIn(f"keep {tree} runtime_unavailable: The Orca runtime closed the connection before responding\n", out)
        self.assertEqual(orca.calls.count(("remove", tree)), 1)

    def test_a_tree_orca_no_longer_tracks_is_removed_with_git(self):
        with tempfile.TemporaryDirectory() as tree:
            orca = FakeOrca([row("ctx_a", worktree=tree)], refusal="selector_not_found: selector_not_found")
            git = FakeGit({tree: checkout(tree)})
            tally, out = run_gc(orca, git)
        self.assertEqual((tally, git.removed), ({"closed": 1, "removed": 1}, [tree]))
        self.assertIn(f"removed {tree} Orca no longer tracks it, so git worktree remove removed it\n", out)

    def test_a_tree_orca_no_longer_tracks_is_kept_when_git_refuses(self):
        with tempfile.TemporaryDirectory() as tree:
            orca = FakeOrca([row("ctx_a", worktree=tree)], refusal="selector_not_found: selector_not_found")
            git = FakeGit({tree: checkout(tree)}, refusal="fatal: cannot remove a locked working tree")
            tally, out = run_gc(orca, git)
        self.assertEqual((tally, git.removed), ({"closed": 1, "keep": 1}, [tree]))
        self.assertIn(f"keep {tree} Orca no longer tracks it and git worktree remove refused: fatal: cannot remove a locked working tree\n", out)

    def test_an_untracked_tree_that_a_live_process_holds_is_never_handed_to_git(self):
        with tempfile.TemporaryDirectory() as tree:
            orca = FakeOrca([row("ctx_a", worktree=tree)], refusal="selector_not_found: selector_not_found")
            orca.holders[tree] = "a live process has a file or its cwd open inside"
            git = FakeGit({tree: checkout(tree)})
            tally, _ = run_gc(orca, git)
        self.assertEqual((tally, git.removed, orca.calls.count(("remove", tree))), ({"closed": 1, "keep": 1}, [], 0))

    def test_a_dispatch_that_failed_before_its_worktree_resolved_is_kept_and_gc_continues(self):
        unplaced = {
            "dispatchId": "ctx_a",
            "dispatchStatus": "failed",
            "terminalState": None,
            "agentTerminalHandle": None,
            "resource": None,
            "projection": {"host": {"kind": "remote", "id": "env_box"}, "workspace": None},
        }
        orca = FakeOrca([unplaced, row("ctx_b")], terminals={WT: [orca_gc.Terminal("term_ctx_b", "claude", "✳ lane", 0), shell("term_sh")]})
        tally, out = run_gc(orca, FakeGit({WT: checkout()}))
        self.assertEqual((tally, orca.calls), ({"keep": 1, "removed": 1}, [("remove", WT)]))
        self.assertIn("keep ctx_a Orca records no workspace for it", out)

    def test_missing_worktree_is_skipped(self):
        tally, _ = run_gc(FakeOrca([row("ctx_a")]), FakeGit({}))
        self.assertEqual(tally, {"closed": 1})

    def test_a_missing_worktree_orca_still_registers_is_dropped(self):
        orca = FakeOrca([row("ctx_a")])
        orca.known.add(WT)
        tally, out = run_gc(orca, FakeGit({}))
        self.assertEqual((tally, orca.calls), ({"closed": 1, "dropped": 1}, [("remove", WT), ("close", "term_ctx_a")]))
        self.assertIn(f"dropped {WT} its directory is gone", out)

    def test_a_drop_orca_already_forgot_reads_as_dropped(self):
        orca = FakeOrca([row("ctx_a")], refusal="selector_not_found: selector_not_found")
        orca.known.add(WT)
        tally, _ = run_gc(orca, FakeGit({}))
        self.assertEqual(tally, {"closed": 1, "dropped": 1})

    def test_dry_run_names_the_drop(self):
        orca = FakeOrca([row("ctx_a")])
        orca.known.add(WT)
        tally, out = run_gc(orca, FakeGit({}), dry_run=True)
        self.assertEqual((tally, orca.calls), ({"close": 1, "drop": 1}, []))
        self.assertIn(f"would drop {WT} its directory is gone", out)

    def test_a_tree_git_cannot_read_is_kept_and_gc_continues(self):
        other = "/w/repo/v3-other-base"
        orca = FakeOrca([row("ctx_a"), row("ctx_b", worktree=other)])
        git = FakeGit({WT: "git cannot read it: fatal: gitfile does not point to a valid repository", other: checkout(other)})
        tally, out = run_gc(orca, git)
        self.assertEqual(tally, {"closed": 2, "keep": 1, "removed": 1})
        self.assertIn(f"keep {WT} git cannot read it: fatal: gitfile does not point to a valid repository", out)

    def test_a_worktree_whose_terminals_orca_cannot_list_is_kept(self):
        orca = FakeOrca([row("ctx_a")])
        orca.terminals_in = lambda worktree, environment=None: "orca terminal list selector_not_found"
        tally, out = run_gc(orca, FakeGit({WT: checkout()}))
        self.assertEqual(tally, {"closed": 1, "keep": 1})
        self.assertNotIn(("remove", WT), orca.calls)
        self.assertIn(f"keep {WT} orca terminal list selector_not_found", out)

    def test_a_terminal_list_refusal_before_the_removal_keeps_the_tree(self):
        orca = FakeOrca([row("ctx_a")], terminals={WT: [shell("term_sh")]})
        listed = iter([[shell("term_sh")], "orca terminal list selector_not_found"])
        orca.terminals_in = lambda worktree, environment=None: next(listed)
        tally, out = run_gc(orca, FakeGit({WT: checkout()}))
        self.assertEqual(tally, {"closed": 1, "keep": 1})
        self.assertIn(f"keep {WT} orca terminal list selector_not_found", out)


class PauseTest(unittest.TestCase):
    def setUp(self):
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        self.resumed = Path(state.name) / "resumed"
        self.resumed.touch()
        self.pause = orca_gc.Pause(self.resumed)

    def skipped(self, target: str, verb: str) -> str:
        return self.pause.skip(target, verb).line()

    def test_a_pause_set_before_the_run_changes_nothing(self):
        self.resumed.unlink()
        orca = FakeOrca([row("ctx_a", status="dispatched")], terminals={WT: [shell("term_sh")]}, stops={"ctx_a": ("stop_unknown", "none", {})})
        tally, out = run_gc(orca, FakeGit({WT: checkout()}), done=frozenset({"ctx_a"}), pause=self.pause)
        self.assertEqual((tally, orca.calls), ({"skip": 1}, []))
        self.assertEqual(out, self.skipped("run_x", "gc") + "\n")

    def test_a_dry_run_reads_through_a_pause(self):
        self.resumed.unlink()
        orca = FakeOrca([row("ctx_a", terminal_state="released")], terminals={WT: [shell("term_sh")]})
        tally, out = run_gc(orca, FakeGit({WT: checkout()}), dry_run=True, pause=self.pause)
        self.assertEqual((tally, orca.calls), ({"remove": 1}, []))
        self.assertIn(f"would remove {WT}", out)

    def test_a_pause_set_between_candidates_skips_the_rest(self):
        resumed, removed = self.resumed, threading.Event()

        class Pausing(FakeOrca):
            def terminals_in(self, worktree: str, environment: str | None = None) -> list:
                if worktree == WT2:
                    removed.wait(5)
                return super().terminals_in(worktree)

            def remove(self, worktree: str, environment: str | None = None) -> str | None:
                resumed.unlink()
                refusal = super().remove(worktree)
                removed.set()
                return refusal

        orca = Pausing([row("ctx_a", terminal_state="released"), row("ctx_b", terminal_state="released", worktree=WT2)])
        tally, out = run_gc(orca, FakeGit({WT: checkout(), WT2: checkout(WT2)}), pause=self.pause)
        self.assertEqual((tally, orca.calls), ({"removed": 1, "skip": 1}, [("remove", WT)]))
        self.assertIn(self.skipped(WT2, "remove"), out)

    def test_a_pause_set_after_a_candidate_is_chosen_skips_its_removal(self):
        resumed = self.resumed

        class Pausing(FakeOrca):
            def holder(self, worktree: str, panes: frozenset[str]) -> str | None:
                resumed.unlink()
                return super().holder(worktree, panes)

        orca = Pausing([row("ctx_a", terminal_state="released")], terminals={WT: [shell("term_sh")]})
        tally, out = run_gc(orca, FakeGit({WT: checkout()}), pause=self.pause)
        self.assertEqual((tally, orca.calls), ({"skip": 1}, []))
        self.assertEqual(orca.panes, {WT: frozenset()})
        self.assertIn(self.skipped(WT, "remove"), out)

    def test_a_pause_set_during_a_stop_lets_it_finish_and_skips_the_rest(self):
        resumed = self.resumed

        class Pausing(FakeOrca):
            def stop(self, dispatch: str) -> tuple[str | None, str]:
                resumed.unlink()
                return super().stop(dispatch)

        stops = {"ctx_a": ("stop_unknown", "none", {}), "ctx_b": ("stop_unknown", "none", {})}
        orca = Pausing([row("ctx_a", status="dispatched"), row("ctx_b", status="dispatched", worktree=WT2)], stops=stops)
        tally, out = run_gc(orca, FakeGit({WT: checkout(), WT2: checkout(WT2)}), done=frozenset({"ctx_a", "ctx_b"}), pause=self.pause)
        self.assertEqual((tally, orca.calls), ({"stopped": 1, "skip": 3}, [("stop", "ctx_a")]))
        self.assertIn(self.skipped("ctx_b", "stop"), out)
        self.assertIn(self.skipped("term_ctx_a", "end"), out)
        self.assertIn(self.skipped(WT, "remove"), out)


class HolderTest(unittest.TestCase):
    TREE = "/w/repo/v3-idle-base"

    def holder(self, ps: str, hooks: str = "", env: str = "", panes: frozenset[str] = frozenset()) -> str | None:
        replies = {
            ("lsof", "-t", "+D", self.TREE): "84516\n",
            ("ps", "-axo", "pid=,ppid=,pgid=,tpgid=,comm="): ps,
            ("ps", "-axo", "pid=,ppid=,command="): hooks,
            ("ps", "-E", "-ww", "-o", "pid=,tty=,command=", "-p", "84516"): env,
            ("lsof", "-a", "-d", "cwd", "-Fn"): f"p84516\nfcwd\nn{self.TREE}\n",
        }

        def run(argv, **_):
            return type("Done", (), {"stdout": replies.get(tuple(argv), "-/opt/homebrew/bin/fish\n")})()

        original = orca_gc.subprocess.run
        orca_gc.subprocess.run = run
        try:
            return orca_gc.Orca().holder(self.TREE, panes)
        finally:
            orca_gc.subprocess.run = original

    def test_an_idle_login_shell_at_its_prompt_does_not_hold_the_tree(self):
        self.assertIsNone(self.holder("    1     0     1     0 launchd\n84516 84510 84516 84516 -/opt/homebrew/bin/fish\n"))

    def test_a_login_shell_running_an_agent_holds_the_tree(self):
        ps = "84516 84510 84516 84516 -/opt/homebrew/bin/fish\n84861 84516 84861 84516 claude\n"
        self.assertEqual(self.holder(ps), "a live process has a file or its cwd open inside")

    def test_a_login_shell_running_a_foreground_command_holds_the_tree(self):
        self.assertEqual(self.holder("84516 84510 84516 90000 -/bin/zsh\n"), "a live process has a file or its cwd open inside")

    def test_a_shell_that_is_not_a_login_shell_holds_the_tree(self):
        self.assertEqual(self.holder("84516 84510 84516 84516 zsh\n"), "a live process has a file or its cwd open inside")

    def test_a_capt_hookd_worker_parked_in_the_tree_does_not_hold_it(self):
        hooks = "  500     1 /Applications/Captain Hook.app/Contents/Helpers/capt-hookd serve\n84516   500 /h/capt-hook/bin/python -P -m captain_hook.worker\n"
        self.assertIsNone(self.holder("84516   500 84516     0 /h/capt-hook/bin/python\n", hooks=hooks))

    def test_a_python_worker_no_capt_hookd_spawned_holds_the_tree(self):
        hooks = "84516   777 /h/capt-hook/bin/python -P -m captain_hook.worker\n"
        self.assertEqual(self.holder("84516   777 84516     0 /h/capt-hook/bin/python\n", hooks=hooks), "a live process has a file or its cwd open inside")

    def test_the_agent_of_a_terminal_the_removal_stops_does_not_hold_the_tree(self):
        env = "84516 ttys064  claude ORCA_TERMINAL_HANDLE=term_new ORCA_PANE_KEY=tab_1:leaf_1 HOME=/u\n"
        self.assertIsNone(self.holder("84516 84510 84516 84516 claude\n", env=env, panes=frozenset({"tab_1:leaf_1"})))

    def test_a_process_detached_from_a_stopped_terminal_holds_the_tree(self):
        env = "84516 ??       node server.js ORCA_PANE_KEY=tab_1:leaf_1\n"
        self.assertEqual(self.holder("84516     1 84516     0 node\n", env=env, panes=frozenset({"tab_1:leaf_1"})), "a live process has a file or its cwd open inside")

    def test_a_process_of_a_terminal_the_removal_keeps_holds_the_tree(self):
        env = "84516 ttys070  claude ORCA_PANE_KEY=tab_2:leaf_2\n"
        self.assertEqual(self.holder("84516 84510 84516 84516 claude\n", env=env, panes=frozenset({"tab_1:leaf_1"})), "a live process has a file or its cwd open inside")


BOX = "env_box"
BOX_WT = "/home/agent/orca/workspaces/repo/box-lane"


def box_row(dispatch: str, status: str = "completed", worktree: str = BOX_WT) -> dict:
    return {
        "dispatchId": dispatch,
        "dispatchStatus": status,
        "terminalState": "retained",
        "agentTerminalHandle": "term_box",
        "resource": None,
        "projection": {"host": {"kind": "remote", "id": BOX}, "workspace": {"id": f"repo::{worktree}"}},
    }


class PairedServerTest(unittest.TestCase):
    def test_a_settled_box_lanes_clean_pushed_tree_is_removed_through_its_server(self):
        orca = FakeOrca([box_row("ctx_a")], terminals={BOX_WT: [orca_gc.Terminal("term_box", "claude", "✳ lane", 0)]})
        orca.paired = {BOX_WT: checkout(BOX_WT, environment=BOX)}
        orca.holders = {BOX_WT: "a Mac process scan must not apply"}
        tally, out = run_gc(orca, FakeGit({}))
        self.assertEqual((tally, orca.calls), ({"removed": 1}, [("remove", BOX_WT, BOX)]))
        self.assertIn(f"removed {BOX_WT} stopping term_box", out)

    def test_a_box_tree_with_unpushed_commits_is_kept_and_its_idle_terminal_closed(self):
        orca = FakeOrca([box_row("ctx_a")], terminals={BOX_WT: [orca_gc.Terminal("term_box", "claude", "✳ lane", 0)]})
        orca.paired = {BOX_WT: checkout(BOX_WT, head="d" * 40, environment=BOX)}
        tally, out = run_gc(orca, FakeGit({}))
        self.assertEqual((tally, orca.calls), ({"keep": 1, "closed": 1}, [("close", "term_box", BOX)]))
        self.assertIn(f"keep {BOX_WT} unpushed commits on v3/lane", out)

    def test_a_box_terminal_reused_by_a_live_dispatch_keeps_both(self):
        orca = FakeOrca([box_row("ctx_a"), box_row("ctx_b", status="dispatched")])
        orca.paired = {BOX_WT: checkout(BOX_WT, environment=BOX)}
        tally, out = run_gc(orca, FakeGit({}))
        self.assertEqual((tally, orca.calls), ({"keep": 1}, []))
        self.assertIn("keep term_box serves an unsettled dispatch", out)

    def test_a_box_path_never_matches_a_live_mac_tree_of_the_same_path(self):
        orca = FakeOrca([box_row("ctx_a", worktree=WT), row("ctx_b", status="dispatched", terminal="term_mac")], terminals={WT: [orca_gc.Terminal("term_box", "claude", "✳ lane", 0)]})
        orca.paired = {WT: checkout(WT, environment=BOX)}
        tally, _ = run_gc(orca, FakeGit({WT: checkout()}))
        self.assertEqual((tally, orca.calls), ({"removed": 1}, [("remove", WT, BOX)]))

    def test_orca_reads_a_box_trees_head_and_branch_from_its_server(self):
        tree = {"head": "b" * 40, "branch": "refs/heads/yasyf/box-lane"}
        orca = RecordingOrca({"ok": True, "result": {"worktree": tree}})
        self.assertEqual(orca.checkout(BOX_WT, BOX, FakeGit({})), checkout(BOX_WT, head="b" * 40, branch="yasyf/box-lane", environment=BOX))
        self.assertEqual(orca.argv, [("worktree", "show", "--worktree", f"path:{BOX_WT}", "--environment", BOX)])


class SpriteTest(unittest.TestCase):
    hosted = {(SPRITE_WT, "term_sprite"): SPRITE}

    def test_a_settled_lanes_clean_landed_sprite_is_destroyed_with_its_environment(self):
        orca, cc_remote = FakeOrca([sprite_row("ctx_s")]), FakeCcRemote(self.hosted)
        tally, out = run_gc(orca, FakeGit({}), cc_remote=cc_remote)
        self.assertEqual((tally, cc_remote.destroyed, orca.calls), ({"destroyed": 1}, ["pool-abc"], [("forget", "env_abc")]))
        self.assertIn("destroyed pool-abc dispatch ctx_s", out)

    def test_a_sprite_no_cc_remote_task_ties_to_the_dispatch_is_never_touched(self):
        cc_remote = FakeCcRemote({(SPRITE_WT, "term_other"): SPRITE})
        tally, _ = run_gc(FakeOrca([sprite_row("ctx_s")]), FakeGit({}), cc_remote=cc_remote)
        self.assertEqual(cc_remote.destroyed, [])
        self.assertNotIn("destroyed", tally)

    def test_an_earlier_dispatch_on_a_live_sprite_never_reaches_worktree_rm_on_its_checkout(self):
        remote_row = {**sprite_row("ctx_s"), "projection": {"host": {"kind": "remote", "id": SPRITE.environment}, "workspace": {"id": SPRITE_WT}}}
        orca, cc_remote = FakeOrca([remote_row]), FakeCcRemote({(SPRITE_WT, "term_other"): SPRITE})
        orca.paired[SPRITE.root] = checkout(SPRITE.root, environment=SPRITE.environment)
        tally, out = run_gc(orca, FakeGit({}), cc_remote=cc_remote)
        self.assertEqual((orca.calls, cc_remote.destroyed), ([("close", "term_sprite", SPRITE.environment)], []))
        self.assertNotIn(SPRITE.root, out)

    def test_a_sprite_task_saved_before_its_worker_terminal_is_skipped(self):
        saved = {"schemaVersion": 1, "provider": "sprites", "workspace": "pool-new", "projectRoot": SPRITE.root, "environmentId": "env_new", "forward": {"host": "pool-new", "config": "/s/ssh/pool-new.ssh"}}
        attached = {**saved, "workspace": "pool-abc", "environmentId": "env_abc", "forward": {"host": "pool-abc", "config": "/s/ssh/pool-abc.ssh"}, "worktreeId": SPRITE_WT, "terminal": "term_sprite"}
        with tempfile.TemporaryDirectory() as state:
            for task in (saved, attached):
                for kind in ("orca", "workspaces"):
                    path = Path(state) / kind / f"{task['workspace']}.json"
                    path.parent.mkdir(exist_ok=True)
                    path.write_text(json.dumps(task))
            self.assertEqual(orca_gc.CcRemote(Path(state)).sprites(), self.hosted)

    def test_uncommitted_work_keeps_the_sprite(self):
        cc_remote = FakeCcRemote(self.hosted, {"pool-abc": checkout(SPRITE.root, dirty=True)})
        _, out = run_gc(FakeOrca([sprite_row("ctx_s")]), FakeGit({}), cc_remote=cc_remote)
        self.assertEqual(cc_remote.destroyed, [])
        self.assertIn("keep pool-abc uncommitted changes", out)

    def test_an_unpushed_head_keeps_the_sprite(self):
        cc_remote = FakeCcRemote(self.hosted, {"pool-abc": checkout(SPRITE.root, head="b" * 40)})
        _, out = run_gc(FakeOrca([sprite_row("ctx_s")]), FakeGit({}), cc_remote=cc_remote)
        self.assertEqual(cc_remote.destroyed, [])
        self.assertIn("keep pool-abc unpushed commits on v3/lane", out)

    def test_an_unreadable_checkout_keeps_the_sprite(self):
        cc_remote = FakeCcRemote(self.hosted, {"pool-abc": "ssh to pool-abc could not read /home/sprite/repo: timeout"})
        _, out = run_gc(FakeOrca([sprite_row("ctx_s")]), FakeGit({}), cc_remote=cc_remote)
        self.assertEqual(cc_remote.destroyed, [])
        self.assertIn("keep pool-abc ssh to pool-abc could not read", out)

    def test_a_busy_terminal_keeps_the_sprite(self):
        cc_remote = FakeCcRemote(self.hosted)
        _, out = run_gc(FakeOrca([sprite_row("ctx_s")], idle={"term_sprite": False}), FakeGit({}), cc_remote=cc_remote)
        self.assertEqual(cc_remote.destroyed, [])
        self.assertIn(f"keep pool-abc terminal term_sprite is {orca_gc.NOT_IDLE}", out)

    def test_an_unreadable_terminal_keeps_the_sprite(self):
        cc_remote = FakeCcRemote(self.hosted)
        _, out = run_gc(FakeOrca([sprite_row("ctx_s")], idle={"term_sprite": None}), FakeGit({}), cc_remote=cc_remote)
        self.assertEqual(cc_remote.destroyed, [])
        self.assertIn("keep pool-abc terminal term_sprite is unreadable or disconnected, so its idleness is unknown", out)

    def test_done_destroys_the_sprite_without_reading_its_terminal(self):
        orca = FakeOrca([sprite_row("ctx_s", status="dispatched")], idle={"term_sprite": None}, stops={"ctx_s": ("stopped", "none", {"dispatchStatus": "failed"})})
        cc_remote = FakeCcRemote(self.hosted)
        tally, _ = run_gc(orca, FakeGit({}), done=frozenset({"ctx_s"}), cc_remote=cc_remote)
        self.assertEqual((cc_remote.destroyed, tally.get("destroyed")), (["pool-abc"], 1))

    def test_a_terminal_that_wakes_before_the_destroy_keeps_the_sprite(self):
        cc_remote = FakeCcRemote(self.hosted)
        _, out = run_gc(FakeOrca([sprite_row("ctx_s")], idle={"term_sprite": [True, False]}), FakeGit({}), cc_remote=cc_remote)
        self.assertEqual(cc_remote.destroyed, [])
        self.assertIn("keep pool-abc changed since the first read", out)

    def test_an_unsettled_dispatch_on_the_same_sprite_keeps_it(self):
        cc_remote = FakeCcRemote(self.hosted)
        _, out = run_gc(FakeOrca([sprite_row("ctx_s"), sprite_row("ctx_t", status="dispatched")]), FakeGit({}), cc_remote=cc_remote)
        self.assertEqual(cc_remote.destroyed, [])
        self.assertIn("keep pool-abc serves an unsettled dispatch", out)

    def test_a_refused_destroy_keeps_the_environment(self):
        orca, cc_remote = FakeOrca([sprite_row("ctx_s")]), FakeCcRemote(self.hosted, refusal="machine lacks the workspace label")
        _, out = run_gc(orca, FakeGit({}), cc_remote=cc_remote)
        self.assertEqual(orca.calls, [])
        self.assertIn("keep pool-abc machine lacks the workspace label", out)

    def test_sprite_dry_run_destroys_nothing(self):
        orca, cc_remote = FakeOrca([sprite_row("ctx_s")]), FakeCcRemote(self.hosted)
        _, out = run_gc(orca, FakeGit({}), dry_run=True, cc_remote=cc_remote)
        self.assertEqual((cc_remote.destroyed, orca.calls), ([], []))
        self.assertIn("would destroy pool-abc dispatch ctx_s", out)

    def test_task_records_tie_a_recorded_sprite_workspace_to_its_orca_worktree_and_terminal(self):
        with tempfile.TemporaryDirectory() as root:
            state = Path(root)
            (state / "orca").mkdir()
            (state / "workspaces").mkdir()

            def task(name: str, **overrides) -> None:
                record = {"schemaVersion": 1, "workspace": name, "provider": "sprites", "projectRoot": "/home/sprite/repo", "environmentId": f"env_{name}",
                          "worktreeId": f"repo_{name}::/home/sprite/repo", "terminal": f"term_{name}",
                          "forward": {"host": name, "config": f"/s/ssh/{name}.ssh"}, **overrides}
                (state / "orca" / f"{name}.json").write_text(json.dumps(record))

            task("live")
            (state / "workspaces" / "live.json").write_text("{}")
            task("destroyed")
            task("namespace", provider="namespace")
            (state / "workspaces" / "namespace.json").write_text("{}")
            (state / "orca" / "live.json.lock").write_text("")
            sprites = orca_gc.CcRemote(state).sprites()
        self.assertEqual(sprites, {("repo_live::/home/sprite/repo", "term_live"): orca_gc.Sprite("live", "live", "/s/ssh/live.ssh", "/home/sprite/repo", "env_live")})


class GitCheckoutTest(unittest.TestCase):
    def test_a_missing_directory_is_already_removed(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(orca_gc.Git().checkout(str(Path(root) / "gone")))

    def test_a_directory_whose_repository_is_gone_names_why_git_cannot_read_it(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / ".git").write_text(f"gitdir: {root}/reaped/.git/worktrees/lane\n")
            self.assertRegex(orca_gc.Git().checkout(root), r"^git cannot read it: fatal: ")

    def test_gc_reclaims_the_rest_of_the_run_past_a_checkout_git_cannot_read(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / ".git").write_text(f"gitdir: {root}/reaped/.git/worktrees/lane\n")

            class Partial(FakeGit):
                def checkout(self, path: str):
                    return orca_gc.Git.checkout(self, path) if path == root else super().checkout(path)

            orca = FakeOrca([row("ctx_gone", worktree=root), row("ctx_a")], terminals={WT: [shell("term_s")]})
            removed, out = run_gc(orca, Partial({WT: checkout()}))
        self.assertEqual(removed["removed"], 1)
        self.assertIn(f"keep {root} git cannot read it: fatal: ", out)


class GitRemoveTest(unittest.TestCase):
    def test_git_removes_a_clean_worktree_and_names_why_it_keeps_a_dirty_one(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root).resolve()

            def git(*args: str, cwd: Path = base) -> str:
                config = ("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null")
                return subprocess.run(["git", *config, *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()

            git("init", "--quiet", "-b", "main", "clone")
            clone = base / "clone"
            git("commit", "--allow-empty", "-m", "base", cwd=clone)
            git("worktree", "add", "--quiet", "--detach", str(base / "clean"), cwd=clone)
            git("worktree", "add", "--quiet", "--detach", str(base / "dirty"), cwd=clone)
            (base / "dirty" / "scratch.txt").write_text("unsaved")

            removed, refused = orca_gc.Git().remove(str(base / "clean")), orca_gc.Git().remove(str(base / "dirty"))
            self.assertIsNone(removed)
            self.assertFalse((base / "clean").exists())
            self.assertRegex(refused, r"^fatal: ")
            self.assertTrue((base / "dirty" / "scratch.txt").is_file())


class OwnRemoteTest(unittest.TestCase):
    def test_a_worktree_of_another_repository_is_checked_against_that_repositorys_origin(self):
        other, pushed = "/w/ccx/fs-churn", "e" * 40
        orca = FakeOrca([row("ctx_a"), row("ctx_b", worktree=other)], terminals={WT: [shell("term_s")], other: [shell("term_o", other)]})
        git = FakeGit({WT: checkout(), other: checkout(other, head=pushed, repository="/w/ccx/.git")}, remotes={"/w/ccx/.git": orca_gc.Remote(frozenset({pushed}), frozenset())})
        tally, out = run_gc(orca, git)
        self.assertEqual(tally["removed"], 2, out)

    def test_git_reads_the_trunk_and_heads_of_the_checkouts_own_origin(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root).resolve()

            def git(*args: str, cwd: Path = base) -> str:
                config = ("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null")
                return subprocess.run(["git", *config, *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()

            git("init", "--bare", "-b", "main", "origin.git")
            git("clone", "--quiet", str(base / "origin.git"), "clone")
            clone = base / "clone"
            git("commit", "--allow-empty", "-m", "base", cwd=clone)
            git("push", "--quiet", "origin", "main", cwd=clone)
            git("worktree", "add", "--quiet", "-b", "lane", str(base / "lane"), cwd=clone)
            git("commit", "--allow-empty", "-m", "lane", cwd=base / "lane")
            git("push", "--quiet", "origin", "lane", cwd=base / "lane")
            git("worktree", "add", "--quiet", "--detach", str(base / "trunk"), "main", cwd=clone)

            lane, trunk = orca_gc.Git().checkout(str(base / "lane")), orca_gc.Git().checkout(str(base / "trunk"))
            remote = orca_gc.Git().remote(lane.repository)
        self.assertEqual((lane.repository, lane.branch, lane.on_trunk), (str(clone / ".git"), "lane", False))
        self.assertTrue(trunk.on_trunk)
        self.assertIn(lane.head, remote.heads)
        self.assertEqual(remote.landed.origin, str(base / "origin.git"))


class OrcaArgvTest(unittest.TestCase):
    def test_close_takes_the_whole_tab_when_the_pane_is_alone_in_it(self):
        orca = RecordingOrca({"ok": True}, {"term_a": "tab_1", "term_b": "tab_2"})
        self.assertIsNone(orca.close("term_a"))
        self.assertEqual(orca.argv[-1], ("terminal", "close", "--terminal", "term_a", "--tab"))

    def test_close_leaves_a_tab_another_pane_shares(self):
        orca = RecordingOrca({"ok": True}, {"term_a": "tab_1", "term_b": "tab_1"})
        self.assertIsNone(orca.close("term_a"))
        self.assertEqual(orca.argv[-1], ("terminal", "close", "--terminal", "term_a"))

    def test_close_of_a_terminal_already_gone_reads_as_closed(self):
        class Gone(RecordingOrca):
            def call(self, *args: str) -> dict:
                if args[:2] == ("terminal", "show"):
                    self.argv.append(args)
                    return {"ok": True, "result": {"terminal": {"tabId": "tab_1", "worktreePath": WT, "connected": False}}}
                return super().call(*args)

        self.assertIsNone(Gone({"ok": False, "error": {"code": "terminal_handle_stale"}}).close("term_a"))

    def test_close_refused_as_stale_while_the_pane_is_live_names_the_code(self):
        class Live(RecordingOrca):
            def call(self, *args: str) -> dict:
                if args[:2] == ("terminal", "show"):
                    self.argv.append(args)
                    return {"ok": True, "result": {"terminal": {"tabId": "tab_1", "worktreePath": WT, "connected": True}}}
                return super().call(*args)

        self.assertEqual(Live({"ok": False, "error": {"code": "terminal_handle_stale"}}).close("term_a"), "terminal_handle_stale")

    def test_a_handle_orca_calls_stale_reads_as_stale(self):
        orca = RecordingOrca({"ok": False, "error": {"code": "terminal_handle_stale"}})
        orca.call = lambda *args: orca.argv.append(args) or orca.reply
        self.assertEqual(orca.presence("term_a"), orca_gc.STALE)
        self.assertEqual(orca.argv, [("terminal", "show", "--terminal", "term_a")])

    def test_a_handle_orca_shows_unconnected_reads_as_disconnected(self):
        orca = RecordingOrca({"ok": True, "result": {"terminal": {"connected": False}}})
        orca.call = lambda *args: orca.argv.append(args) or orca.reply
        self.assertEqual(orca.presence("term_a"), orca_gc.DISCONNECTED)

    def test_stop_fences_the_dispatch_through_orca(self):
        orca = RecordingOrca({"ok": True, "result": {"state": "stopped", "processAction": "closed_agent_terminal"}})
        self.assertEqual(orca.stop("ctx_a"), ("stopped", "closed_agent_terminal"))
        self.assertEqual(orca.argv, [("orchestration", "worker-stop", "--dispatch", "ctx_a", "--full")])

    def test_workers_ask_orca_for_the_full_record(self):
        orca = RecordingOrca({"ok": True, "result": {"workers": [row("ctx_a")], "page": {"nextCursor": None}}})
        self.assertEqual([worker.dispatch for worker in orca.workers("run_x")], ["ctx_a"])
        self.assertEqual(orca.argv, [("orchestration", "worker-list", "--run", "run_x", "--include-remote", "--limit", "100", "--full")])

    def test_stop_refusal_names_the_code(self):
        self.assertEqual(RecordingOrca({"ok": False, "error": {"code": "dispatch_not_found"}}).stop("ctx_a"), (None, "dispatch_not_found"))

    def test_a_terminal_whose_process_exited_is_already_gone(self):
        class Exited(RecordingOrca):
            def call(self, *args: str) -> dict:
                self.argv.append(args)
                return {"ok": True, "result": {"terminal": {"connected": False, "lastOutputAt": 0}}}

        orca = Exited({})
        self.assertIsNone(orca.idle("term_a"))
        self.assertEqual(orca.argv, [("terminal", "show", "--terminal", "term_a")])

    def test_a_disconnected_terminal_no_longer_holds_its_worktree(self):
        class Listed(RecordingOrca):
            def call(self, *args: str) -> dict:
                self.argv.append(args)
                rows = [{"handle": "term_dead", "agentIdentity": "codex", "connected": False}, {"handle": "term_sh", "connected": True}]
                return {"ok": True, "result": {"terminals": rows}}

        self.assertEqual([terminal.handle for terminal in Listed({}).terminals_in(WT)], ["term_sh"])

    def test_output_during_the_idle_wait_is_not_idle(self):
        class Chatty(RecordingOrca):
            def call(self, *args: str) -> dict:
                self.argv.append(args)
                if args[:2] == ("terminal", "wait"):
                    return {"ok": True, "result": {"wait": {"satisfied": True}}}
                shows = sum(1 for argv in self.argv if argv[:2] == ("terminal", "show"))
                return {"ok": True, "result": {"terminal": {"connected": True, "lastOutputAt": 0 if shows == 1 else NOW}}}

            def now_ms(self) -> int:
                return NOW

        self.assertFalse(Chatty({}).idle("term_a"))

    def test_idle_reads_a_sprite_terminal_through_its_environment(self):
        orca = RecordingOrca({"ok": False, "error": {"code": "terminal_handle_stale"}})
        orca.call = lambda *args: orca.argv.append(args) or orca.reply
        self.assertIsNone(orca.idle("term_a", "env_abc"))
        self.assertEqual(orca.argv, [("terminal", "show", "--terminal", "term_a", "--environment", "env_abc")])

    def test_forget_removes_the_orca_environment(self):
        orca = RecordingOrca({"ok": True})
        self.assertIsNone(orca.forget("env_abc"))
        self.assertEqual(orca.argv, [("environment", "rm", "--environment", "env_abc")])

    def test_an_orchestration_call_asks_for_the_full_record_and_refuses_the_concise_view(self):
        original = orca_gc.subprocess.run
        argv: list[list[str]] = []
        replies = [{"ok": True, "result": {"workers": []}, "_meta": {"runtimeId": "rt_1"}}, {"ok": True, "result": {"workers": []}}]

        def fake(command, **_):
            argv.append(command)
            return orca_gc.subprocess.CompletedProcess(command, 0, json.dumps(replies.pop(0)), "")

        orca_gc.subprocess.run = fake
        try:
            self.assertEqual(orca_gc.Orca().call("orchestration", "worker-list", "--run", "run_1")["result"], {"workers": []})
            with self.assertRaisesRegex(RuntimeError, "orca orchestration worker-list printed its concise view"):
                orca_gc.Orca().call("orchestration", "worker-list", "--run", "run_1")
        finally:
            orca_gc.subprocess.run = original
        self.assertEqual(argv[0], ["orca", "orchestration", "worker-list", "--run", "run_1", "--full", "--json"])

    def test_remote_checkout_reads_porcelain_branch_headers_over_ssh(self):
        original = orca_gc.subprocess.run
        argv: list[list[str]] = []

        def fake(command, **_):
            argv.append(command)
            if command[0] == "ssh":
                return orca_gc.subprocess.CompletedProcess(command, 0, "https://token@github.com/acme/repo.git\n# branch.oid " + "a" * 40 + "\n# branch.head (detached)\n? scratch.txt\n", "")
            return orca_gc.subprocess.CompletedProcess(command, 0, '{"default_branch": "dev", "status": "behind"}', "")

        orca_gc.github_trunk.cache_clear()
        orca_gc.subprocess.run = fake
        try:
            found = orca_gc.CcRemote(Path("/nonexistent")).checkout(SPRITE)
        finally:
            orca_gc.subprocess.run = original
        self.assertEqual(found, orca_gc.Checkout(SPRITE.root, True, None, "a" * 40, True, "acme/repo"))
        self.assertEqual(argv[0][:6], ["ssh", "-F", "/s/ssh/pool-abc.ssh", "-o", "BatchMode=yes", "pool-abc"])
        self.assertEqual(argv[1:], [["gh", "api", "repos/acme/repo"], ["gh", "api", f"repos/acme/repo/compare/dev...{'a' * 40}?per_page=1"]])

    def test_a_sprite_without_a_github_origin_is_kept_by_name(self):
        original = orca_gc.subprocess.run
        orca_gc.subprocess.run = lambda command, **_: orca_gc.subprocess.CompletedProcess(command, 0, "https://token@gitlab.example/acme/repo\n# branch.oid x\n# branch.head dev\n", "")
        try:
            found = orca_gc.CcRemote(Path("/nonexistent")).checkout(SPRITE)
        finally:
            orca_gc.subprocess.run = original
        self.assertEqual(found, "/home/sprite/repo has no GitHub origin to check its HEAD against")

    def replying(self, replies: dict[str, tuple[int, str, str]]) -> None:
        original = orca_gc.subprocess.run
        self.addCleanup(setattr, orca_gc.subprocess, "run", original)
        orca_gc.github_trunk.cache_clear()
        orca_gc.subprocess.run = lambda command, **_: orca_gc.subprocess.CompletedProcess(command, *replies[command[-1]])

    def test_a_head_github_does_not_know_is_not_on_its_trunk(self):
        self.replying({"repos/acme/repo": (0, '{"default_branch": "dev"}', ""), "repos/acme/repo/compare/dev...dead?per_page=1": (1, "{}", "gh: Not Found (HTTP 404)")})
        self.assertFalse(orca_gc.on_github_trunk("dead", "acme/repo"))

    def test_a_head_that_diverged_from_trunk_is_not_on_it(self):
        self.replying({"repos/acme/repo": (0, '{"default_branch": "dev"}', ""), "repos/acme/repo/compare/dev...b?per_page=1": (0, '{"status": "diverged"}', "")})
        self.assertFalse(orca_gc.on_github_trunk("b", "acme/repo"))

    def test_any_other_github_failure_raises_a_refusal(self):
        self.replying({"repos/acme/repo": (1, "", "gh: API rate limit exceeded (HTTP 403)")})
        with self.assertRaisesRegex(orca_gc.GitHubRefusal, "API rate limit exceeded"):
            orca_gc.on_github_trunk("b", "acme/repo")

    def test_a_rate_limit_keeps_the_sprite_it_was_reading(self):
        original = orca_gc.subprocess.run
        self.addCleanup(setattr, orca_gc.subprocess, "run", original)
        orca_gc.github_trunk.cache_clear()

        def fake(command, **_):
            if command[0] == "ssh":
                return orca_gc.subprocess.CompletedProcess(command, 0, "https://github.com/acme/repo\n# branch.oid " + "a" * 40 + "\n# branch.head dev\n", "")
            return orca_gc.subprocess.CompletedProcess(command, 1, "", "gh: API rate limit exceeded (HTTP 403)")

        orca_gc.subprocess.run = fake
        found = orca_gc.CcRemote(Path("/nonexistent")).checkout(SPRITE)
        self.assertEqual(found, "GitHub could not answer: gh: API rate limit exceeded (HTTP 403)")

    def test_a_rate_limit_on_the_landed_check_reads_as_unsaved_not_a_crash(self):
        class Limited:
            def __contains__(self, head: str) -> bool:
                raise orca_gc.GitHubRefusal("GitHub could not answer: rate limit")

        held = orca_gc.unsaved(checkout(head="b" * 40), orca_gc.Remote(frozenset(), Limited()))
        self.assertEqual(held, "GitHub could not answer: rate limit")

    def test_github_tips_hold_a_head_only_a_branch_points_at(self):
        self.replying({"repos/acme/repo/commits/a/branches-where-head": (0, '[{"name": "lane"}]', ""), "repos/acme/repo/commits/b/branches-where-head": (0, "[]", ""), "repos/acme/repo/commits/c/branches-where-head": (1, "{}", "gh: No commit found for SHA: c (HTTP 422)")})
        tips = orca_gc.GitHubTips("acme/repo")
        self.assertEqual(("a" in tips, "b" in tips, "c" in tips), (True, False, False))

    def test_github_landed_needs_this_head_merged_into_trunk(self):
        pull = {"merged_at": "2026-11-03T00:00:00Z", "head": {"sha": "a"}, "base": {"ref": "dev"}}
        cases = {"a": pull, "b": {**pull, "merged_at": None, "head": {"sha": "b"}}, "c": {**pull, "head": {"sha": "other"}}, "d": {**pull, "head": {"sha": "d"}, "base": {"ref": "parent"}}}
        self.replying({"repos/acme/repo": (0, '{"default_branch": "dev"}', ""), **{f"repos/acme/repo/commits/{head}/pulls": (0, json.dumps([found]), "") for head, found in cases.items()}})
        landed = orca_gc.GitHubLanded("acme/repo")
        self.assertEqual([head in landed for head in cases], [True, False, False, False])

    def test_landed_searches_closed_prs_for_the_one_head_it_is_asked_about(self):
        original = orca_gc.subprocess.run
        argv: list[list[str]] = []
        pulls = [{"number": 7, "headRefOid": "b" * 40}, {"number": 8, "headRefOid": "c" * 40}]

        def fake(command, **_):
            argv.append(command)
            return orca_gc.subprocess.CompletedProcess(command, 0, json.dumps([pull for pull in pulls if pull["headRefOid"] == command[8]]), "")

        orca_gc.subprocess.run = fake
        try:
            landed = orca_gc.Landed("https://github.com/acme/repo", frozenset({7}))
            found = ("b" * 40 in landed, "c" * 40 in landed, "d" * 40 in landed)
        finally:
            orca_gc.subprocess.run = original
        self.assertEqual(found, (True, False, False))
        self.assertEqual(argv[0], ["gh", "pr", "list", "--repo", "https://github.com/acme/repo", "--state", "closed", "--search", "b" * 40, "--json", "number,headRefOid"])

    def test_remove_deregisters_the_worktree_from_orca(self):
        orca = RecordingOrca({"ok": True})
        self.assertIsNone(orca.remove(WT))
        self.assertEqual(orca.argv, [("worktree", "rm", "--worktree", f"path:{WT}")])

    def test_a_worktree_orca_no_longer_tracks_has_no_terminals(self):
        orca = RecordingOrca({"ok": False, "error": {"code": "selector_not_found", "message": "selector_not_found"}})
        orca.call = lambda *args: orca.argv.append(args) or orca.reply
        self.assertIsNone(orca.terminals_in(WT))
        self.assertEqual(orca.argv, [("terminal", "list", "--worktree", f"path:{WT}", "--limit", "1000")])

    def test_close_leaves_the_tab_when_orca_no_longer_tracks_the_worktree(self):
        class Untracked(RecordingOrca):
            def call(self, *args: str) -> dict:
                if args[:2] == ("terminal", "list"):
                    self.argv.append(args)
                    return {"ok": False, "error": {"code": "selector_not_found", "message": "selector_not_found"}}
                return super().call(*args)

        orca = Untracked({"ok": True})
        self.assertIsNone(orca.close("term_a"))
        self.assertEqual(orca.argv[-1], ("terminal", "close", "--terminal", "term_a"))

    def test_remove_refusal_names_the_code_and_message(self):
        orca = RecordingOrca({"ok": False, "error": {"code": "worktree_dirty", "message": "uncommitted changes"}})
        self.assertEqual(orca.remove(WT), "worktree_dirty: uncommitted changes")



PROBE = """
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("orca_gc", sys.argv[1])
gc = sys.modules["orca_gc"] = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gc)
print(json.dumps([gc.CC_REMOTE, os.environ.get("CC_REMOTE_CONFIG")]))
"""


class RepoValuesTest(unittest.TestCase):
    def cc_remote(self, cwd: Path, **env: str) -> list:
        inherited = {key: value for key, value in os.environ.items() if key not in ("WORKER_CC_REMOTE", "CC_REMOTE_CONFIG")}
        done = subprocess.run([sys.executable, "-c", PROBE, str(SCRIPT)], cwd=cwd, env={**inherited, **env}, capture_output=True, text=True, check=True)
        return json.loads(done.stdout)

    def test_a_checkouts_orca_json_names_the_cc_remote_pair_the_environment_leaves_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp).resolve()
            (checkout / ".orca.json").write_text(json.dumps({"ccRemote": "tools/cc-remote", "ccRemoteConfig": "tools/config.yaml"}))
            self.assertEqual(self.cc_remote(checkout), ["tools/cc-remote", str(checkout / "tools/config.yaml")])
            self.assertEqual(self.cc_remote(checkout, WORKER_CC_REMOTE="/opt/cc-remote", CC_REMOTE_CONFIG="/etc/cc.yaml"), ["/opt/cc-remote", "/etc/cc.yaml"])

    def test_a_checkout_without_an_orca_json_runs_cc_remote_from_path_under_the_inherited_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.cc_remote(Path(tmp)), ["cc-remote", None])


if __name__ == "__main__":
    unittest.main()
