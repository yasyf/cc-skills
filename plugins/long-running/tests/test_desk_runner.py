"""desk-runner.py against a fake Orca Run, ledger, and stack-enqueue, on a fake clock: every side effect is recorded on ``shell.calls``."""

from __future__ import annotations

import contextlib
import hashlib
import html
import importlib.util
import itertools
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from long_running import actions
import mq as mq_module
import pytest
from conftest import gateway

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/desk-runner.py"
spec = importlib.util.spec_from_file_location("desk_runner", SCRIPT)
runner_module = importlib.util.module_from_spec(spec)
sys.modules["desk_runner"] = runner_module
spec.loader.exec_module(runner_module)

LANE = "api-fix"
QUOTA_RESET = "2026-10-01T22:00:00Z"
FORBIDDEN = {"worker-release", "kill", "pkill", "close", "stop", "terminate", "worker-stop"}
IDLE_PROMPT = ["⏺ Done.", "─" * 80, "❯", "─" * 80, "  ⏵⏵ bypass permissions on (shift+tab to cycle)"]
PICKER = ["─" * 80, " ☐ Rebase", "❯ 1. Rebase now", "  2. Wait", "─" * 80, "Enter to select · ↑/↓ to navigate · Esc to cancel"]
RULING = "Requirement: " + "the release holds until the canary reads back green. " * 12 + "Then land the fix."
PARAGRAPHS = "Ruling, in three parts:\n\n- hold the release; relay the canary result to walker\n- " + "rebuild each failed head once, " * 15 + "then stop\n\nReport on this thread."
FAST_SQUASH = "5e11a9d0c2b47f6e8a1d3c5b7f9e0a2c4d6b8e1f"
FAST_BODY = "Resume uploads a pre-session-rows thread instead of refusing.\n\nRelease-With: sand\n"


class Reexeced(Exception):
    pass


class Process:
    pid = 4242

    def poll(self):
        return 0


class Running:
    code = None

    def poll(self):
        return self.code


def cci_names(record: dict, pr: str) -> bool:
    refs = record.get("refs", {}).get("prs", [])
    if "#" in pr:
        return pr in refs or int(pr.rpartition("#")[2]) in refs
    return int(pr) in refs


def identity(kind: str, **limits: int) -> dict:
    pools = [{"identity": kind, "resource": resource, "limit": limit, "remaining": limit, "resetAt": QUOTA_RESET, "readAt": "2026-10-01T17:59:00Z", "callers": []} for resource, limit in limits.items()]
    return {"kind": kind, "name": kind, "pools": [*pools, {"identity": kind, "resource": "search", "limit": 30, "remaining": 2, "resetAt": QUOTA_RESET, "readAt": "2026-10-01T17:59:00Z", "callers": []}]}


def pool(shell: FakeShell, side: str, resource: str) -> dict:
    return next(entry for entry in shell.auth[side]["pools"] if entry["resource"] == resource)


class FakeShell(runner_module.Shell):
    def __init__(self, root: Path):
        self.root = root
        self.receipts = root / ".claude/scratch/orca-launch/run_1"
        self.clock = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
        self.calls: list[list[str]] = []
        self.dispatches: dict[str, dict] = {}
        self.mailbox: list[dict] = []
        self.delivery: list[dict] = []
        self.arriving: list[dict] = []
        self.inbox_error = None
        self.orca_down = None
        self.inboxes: dict[str, list[dict]] = {}
        self.questions: list[dict] = []
        self.lose_sends = 0
        self.lose_replies = 0
        self.answered: dict[str, str] = {}
        self.reject_sends = 0
        self.mailbox_down = False
        self.garble_sends = 0
        self.requests: dict[str, str] = {}
        self.stale_out = ""
        self.prompt_watch: dict = {}
        self.screens: dict[str, list[str]] = {}
        self.stop_answers: dict[str, tuple[str, int | None]] = {}
        self.gates: dict[str, list[str]] = {}
        self.enqueue_out: dict[str, tuple[int, str]] = {}
        self.mergeable_reads: list[str] = []
        self.aborts: list[tuple[list[str], str | None]] = []
        self.ship_failure: runner_module.Done | None = None
        self.statuses = ""
        self.combined_statuses: dict[str, list[dict]] = {}
        self.review_runs: dict[str, list[str]] = {}
        self.status_read_error = ""
        self.advance_error = ""
        self.mirror: str | None = "1a2b3c4"
        self.indexes = 0
        self.diffs: dict[str, str] = {}
        self.compare_errors: dict[str, str] = {}
        self.pr_commits: dict[str, int] = {}
        self.files: dict[str, list[str]] = {}
        self.files_error = ""
        self.auth = {"reads": identity("app", core=15000, graphql=10000), "writes": identity("user", core=5000, graphql=5000)}
        self.raw: list[list[str]] = []
        self.quota_error = ""
        self.status_error = ""
        self.pr_labels: dict[str, list[str]] = {}
        self.head_refs: dict[str, str] = {}
        self.pr_label_error = ""
        self.dequeue_code = 0
        self.queue: dict[str, str] = {}
        self.pr_status: dict[str, dict] = {}
        self.bodies: dict[str, str] = {}
        self.comments: dict[str, list[str]] = {}
        self.rows: list[dict] = []
        self.ledger_code = 0
        self.launch_line = ""
        self.cpu_load = 1.0
        self.attachments: dict[str, Path] = {}
        self.entries: list[str] = []
        self.attach_error = ""
        self.attach_code = 1
        self.logs: dict[str, str] = {"briefs1": "briefs: d1"}
        self.sequence = 0
        self.environ = {"ORCA_TERMINAL_HANDLE": "term_root", "ORCA_PANE_KEY": "pane_root"}
        self.coordinator = "term_root"
        self.dead: set[str] = set()
        self.gone: set[str] = set()
        self.seen: dict[str, str] = {}
        self.seen_as: dict[str, tuple[str, str]] = {}
        self.announced: dict[str, str] = {}
        self.sender = ""
        self.generation = 3
        self.run_use_error: dict | None = None
        self.posts: list[dict] = []
        self.states: list[dict] = []
        self.alive: list[str] = []
        self.beats: list[dict] = []
        self.records: list[dict] = []
        self.trunk: list[dict] = []
        self.trunk_error = ""
        self.tail_error = ""
        self.tail_failures = 0
        self.post_error = ""
        self.post_out = ""
        self.post_ids: dict[str, int] = {}
        self.sprite_line = ""
        self.sprite_receipts: dict[str, str] = {}
        self.spawned_env: dict[str, dict] = {}
        self.successors: list[tuple[list[str], Path, bool]] = []
        self.finder_jobs: list[Path] = []
        self.exited: set[int] = set()
        self.fidelity_jobs: list[Path] = []
        self.reexeced: list[list[str]] = []
        self.reviews: dict[str, dict] = {}
        self.queue_drafts: list[dict] | None = None
        self.squashed: set[str] = set()
        self.merged_heads: list[str] = []
        self.replay_conflicts: set[str] = set()
        self.patch_changes: set[str] = set()
        self.replayed: list[tuple[str, str, str]] = []
        self.patches: dict[str, str] = {}
        self.pushes: list[list[str]] = []
        self.approval_after_push = "APPROVED"
        self.review_commit = ""
        self.behind: dict[str, int] = {}
        self.stack_code = 0
        self.checks: dict[str, list[dict]] = {}
        self.stack_merge_out = ""
        self.on_sleep: list = []
        self.link_out = ""
        self.pr_states: dict[str, str] = {}
        self.bases: dict[str, str] = {}
        self.stack_members: set[str] = set()
        self.async_pending: set[str] = set()
        self.diff_names: dict[str, list[str]] = {}
        self.merge_commits: dict[str, str] = {}
        self.current_heads: dict[str, str] = {}
        self.live: dict[str, dict] = {}
        self.running: list[dict] = []
        self.ancestors: set[tuple[str, str]] = set()
        self.worktrees: dict[str, dict[str, str]] = {}
        self.listed: list[tuple[str, str]] = []
        self.kept: dict[str, str] = {}
        self.removed: list[str] = []
        self.ships: list[tuple[list[str], str | None]] = []
        self.ship_envs: list[dict | None] = []
        self.drives: list[str] = ["d1"]
        self.claims: list[list[str]] = []
        self.claim_error = ""
        self.registered: list[list[str]] = []
        self.root_drives: list[str] = ["d1"]
        self.builds: dict[str, dict] = {}
        self.annotations: dict[str, list[dict]] = {}
        self.go_imports: set[tuple[str, str, str]] = set()
        self.merge_bases: dict[str, str] = {}
        self.local_heads: set[str] = set()
        self.commit_statuses: dict[str, list[dict]] = {}
        self.grade_refs: dict[str, str] = {}
        self.grade_starts: list[dict] = []
        self.grade_error = ""
        self.ledger_outs: dict[str, tuple[int, str]] = {"show": (0, json.dumps({"rows": []}))}
        self.viewer = "desk-owner"
        self.cards: list[dict] = []
        self.card_error = ""
        self.check_pr: dict[str, tuple[int, str]] = {}
        self.refreshes: list[list[str]] = []
        self.refresh_error = ""
        self.pr_cards: list[dict] = []
        self.pr_card_error = ""
        self.board_seq = 900000
        self.no_snapshot = False
        self.snapshot_drops: set[str] = set()
        self.stall_pass = Running()
        self.board: dict[str, dict[str, dict]] = {}
        self.board_rows: list[tuple[str, str]] = []
        self.unknown_blocks: set[str] = set()
        self.carries: list[dict[str, str]] = []
        self.register: dict[str, dict] = {}
        self.board_error = ""
        self.pr_heads: dict[str, tuple[str, str]] = {}
        self.missing_prs: set[str] = set()

    def env(self, name):
        return self.environ.get(name, "")

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += timedelta(seconds=seconds)
        while self.on_sleep:
            self.on_sleep.pop(0)()

    def load(self):
        return self.cpu_load

    def cores(self):
        return 8

    def adopt(self, values):
        self.environ.update(values)

    def reexec(self, argv):
        self.reexeced.append(list(argv))
        raise Reexeced

    def pid_alive(self, pid):
        return pid not in self.exited

    def spawn(self, argv, out, env, append=False):
        self.calls.append(list(argv))
        self.spawned_env[Path(argv[0]).name] = env
        out.parent.mkdir(parents=True, exist_ok=True)
        if Path(argv[0]).name == "desk-runner.py":
            self.successors.append((list(argv), out, append))
            return Process()
        if argv[1:2] == [str(runner_module.STALL_WATCH)]:
            return self.stall_pass
        if argv[1:2] == [str(runner_module.SCRIPTS / "finder.py")]:
            assert argv == [sys.executable, str(runner_module.SCRIPTS / "finder.py"), str(out.parent)] and not env
            self.finder_jobs.append(out.parent)
            process = Process()
            process.pid = 5000 + len(self.finder_jobs)
            return process
        if argv[1:2] == [str(runner_module.SCRIPTS / "fidelity.py")]:
            assert argv == [sys.executable, str(runner_module.SCRIPTS / "fidelity.py"), str(out.parent)] and not env
            self.fidelity_jobs.append(out.parent)
            return Process()
        if Path(argv[0]).name == "worker-launch.sh":
            out.write_text(self.sprite_line)
            attempt = Path(argv[4]).with_suffix(".worker")
            for name, text in self.sprite_receipts.items():
                attempt.mkdir(exist_ok=True)
                (attempt / name).write_text(text)
            return Process()
        if Path(argv[0]).name == "orca-launch.sh" and (ready := runner_module.LAUNCHED.search(self.launch_line)):
            state = Path(env.get("ORCA_LAUNCH_STATE") or self.root / ".claude/scratch/orca-launch" / env.get("ORCA_LAUNCH_RUN", "run_1"))
            state.mkdir(parents=True, exist_ok=True)
            (state / f"{argv[1]}.json").write_text(json.dumps({"result": {"taskId": "task_1", "dispatchId": ready["dispatch"]}}))
            (state / f"{argv[1]}.terminal").write_text(f"{ready['terminal']}\n")
            self.dispatches[ready["dispatch"]] = {"status": "dispatched", "terminal": ready["terminal"]}
        out.write_text(self.launch_line)
        return Process()

    def stat(self, path):
        return path.read_text() if path.is_file() else None

    def next_id(self, prefix: str) -> str:
        self.sequence += 1
        return f"{prefix}_{self.sequence:04d}"

    def run(self, argv, stdin=None, env=None, cwd=None):
        self.raw.append(list(argv))
        argv = gateway(argv)
        self.calls.append(argv)
        name = Path(argv[0]).name
        if argv[:2] == ["codex-ask", "--cancel"]:
            return runner_module.Done(0, "", "")
        if argv[:3] == ["gh", "pr", "view"] and argv[-1] == runner_module.MERGE_CARD_FIELDS:
            return runner_module.Done(0, json.dumps({"title": "Record each run's owner", "author": {"login": "dana"}, "additions": 12, "deletions": 3, "reviewDecision": "APPROVED"}), "")
        if argv[:3] == ["cc-slack", "dm-status", "--root-only"]:
            self.cards.append(json.loads(argv[argv.index("--card") + 1]))
            return runner_module.Done(1 if self.card_error else 0, "", self.card_error)
        if argv[0] == "orca":
            self.sender = (env or {}).get("ORCA_TERMINAL_HANDLE") or self.environ.get("ORCA_TERMINAL_HANDLE", "")
            if self.orca_down and argv[2] not in ("run-current", "run-show"):
                return self.orca_down
            return self.orca(argv[1:-1])
        if argv[:3] == ["ccn", "-R", str(self.root / "checkout")]:
            return self.ccn(argv[3:])
        if name == "orca-check.sh":
            assert argv[1:] == ["--stale"]
            return runner_module.Done(0, self.stale_out, "")
        if argv[:2] == [sys.executable, str(runner_module.SCRIPTS / "prompt_watch.py")]:
            assert argv[2:] == ["show", "--cci-drive", "d1", "--json"]
            return runner_module.Done(0, json.dumps(self.prompt_watch), "")
        if name == "stack-enqueue":
            return self.stack_enqueue(argv[1:])
        if argv[:4] == ["git", "-C", str(self.root / "checkout"), "replay"]:
            assert argv[4] == "--onto" and argv[6] == "--ref" and argv[8] == "--ref-action=print"
            head = argv[9].split("..")[1]
            if head in self.replay_conflicts:
                return runner_module.Done(1, "", f"error: could not apply {head[:7]}")
            new = f"{head[:4]}r{len(self.replayed)}".ljust(10, "0")
            self.replayed.append((argv[5], head, new))
            self.patches[new] = self.patches.get(head, f"patch {head}") if head not in self.patch_changes else "changed"
            return runner_module.Done(0, f"update {argv[7]} {new} 0000000000\n", "")
        if argv[:5] == ["git", "-C", str(self.root / "checkout"), "diff", "--name-only"]:
            return runner_module.Done(0, "".join(f"{name}\n" for name in self.diff_names.get(argv[6], [])), "")
        if argv[:4] == ["git", "-C", str(self.root / "checkout"), "diff"]:
            return runner_module.Done(0, self.patches.get(argv[5], f"patch {argv[5]}"), "")
        if argv[:4] == ["git", "-C", str(self.root / "checkout"), "push"]:
            self.pushes.append(argv[4:])
            branches = {row["branch"]: row["pr"] for row in self.rows}
            for spec in argv[argv.index("origin") + 1 :]:
                new, ref = spec.split(":")
                self.current_heads[branches[ref.removeprefix("refs/heads/")]] = new
            return runner_module.Done(0, "", "")
        if argv[:4] == ["git", "-C", str(self.root / "checkout"), "merge-base"] and argv[4] not in ("HEAD", "--is-ancestor"):
            return runner_module.Done(0, "bbbb000000\n", "")
        if argv[:5] == ["git", "-C", str(self.root / "checkout"), "merge-base", "HEAD"]:
            return runner_module.Done(0, f"{self.merge_bases[argv[5]]}\n", "") if argv[5] in self.merge_bases else runner_module.Done(1, "", "")
        if argv[:5] == ["git", "-C", str(self.root / "checkout"), "merge-base", "--is-ancestor"]:
            return runner_module.Done(0 if tuple(argv[5:]) in self.ancestors else 1, "", "")
        if argv[:2] == ["git", "-C"] and argv[2] in self.worktrees:
            assert argv[3:] in (["status", "--short", "--branch"], ["log", "-1", "--format=%ct %h %s"], ["rev-list", "--count", "origin/dev..HEAD"], ["rev-parse", "--path-format=absolute", "--git-common-dir", "HEAD"]) or argv[3] == "ls-remote"
            found = self.worktrees[argv[2]].get(argv[3])
            return runner_module.Done(0, found, "") if found is not None else runner_module.Done(128, "", "fatal")
        if argv == ["git", "-C", str(self.root / "checkout"), "worktree", "list", "--porcelain"]:
            mirror = [] if self.mirror is None else [f"worktree {self.root / 'checkout'}\nHEAD {self.mirror}\nbranch refs/heads/runner/drive\n"]
            return runner_module.Done(0, "\n".join(mirror + [f"worktree {path}\nHEAD 1a2b3c4\n" + (f"branch refs/heads/{branch}" if branch else "detached") + "\n" for path, branch in self.listed]), "")
        if argv[:5] == ["ccx", "vcs", "worktree", "rm", "--path"]:
            self.removed.append(argv[5])
            if argv[5] in self.kept:
                return runner_module.Done(1, "", self.kept[argv[5]])
            self.listed = [(path, branch) for path, branch in self.listed if path != argv[5]]
            return runner_module.Done(0, "", "")
        if argv[:3] == ["gh", "pr", "list"] and "--author" in argv:
            assert argv[3:] == ["--repo", "Forge-AI/monorepo", "--author", "app/graphite-app", "--state", "all", "--limit", "2", "--json", "createdAt,closedAt"]
            return runner_module.Done(1, "", "HTTP 502") if self.queue_drafts is None else runner_module.Done(0, json.dumps(self.queue_drafts), "")
        if argv[:2] == ["gh", "stack"]:
            assert env == {"GH_REPO": "Forge-AI/monorepo"} and cwd == str(self.root / "checkout")
            if argv[2] == "link" and self.link_out:
                return runner_module.Done(1, "", self.link_out)
            code = self.stack_code if argv[2] == "merge" else 0
            return runner_module.Done(code, self.stack_merge_out if code else "", "a pull request in the stack is not mergeable" if code and not self.stack_merge_out else "")
        if argv[:4] == ["gh", "api", "-X", "PUT"] and argv[4].endswith("/merge-async"):
            if argv[4].split("/")[4] in self.async_pending:
                return runner_module.Done(0, json.dumps({"status": "pending"}), "")
            self.pr_states[argv[4].split("/")[4]] = "MERGED"
            return runner_module.Done(0, json.dumps({"status": "queued"}), "")
        if argv[:3] == ["gh", "api", "graphql"] and "pullRequests(headRefName: " in argv[4]:
            return runner_module.Done(0, json.dumps({"data": {"repository": {}}}), "")
        if argv[:3] == ["git", "-C", str(self.root / "checkout")] and argv[3:] == ["ls-remote", "origin", "refs/heads/dev"]:
            if self.trunk_error:
                return runner_module.Done(128, "", self.trunk_error)
            return runner_module.Done(0, f"{self.trunk[0]['oid']}\trefs/heads/dev\n" if self.trunk else "", "")
        if argv[:5] == ["git", "-C", str(self.root / "checkout"), "cat-file", "-e"]:
            return runner_module.Done(0 if argv[5].removesuffix("^{commit}") in self.local_heads else 128, "", "")
        if argv[:6] == ["git", "-C", str(self.root / "checkout"), "fetch", "-q", "--no-tags"] and argv[6] == "origin" and argv[7:] != ["refs/heads/dev"]:
            self.local_heads.update(argv[7:])
            return runner_module.Done(0, "", "")
        if argv[:6] == ["git", "-C", str(self.root / "checkout"), "grep", "-q", "-F"]:
            sha, _, pathspec = argv[-3:]
            directory = pathspec.removeprefix(":(glob)").removesuffix("/*.go")
            imported = [argv[i + 1].strip('"') for i in range(6, len(argv) - 3, 2)]
            return runner_module.Done(0 if any((sha, directory, path) in self.go_imports for path in imported) else 1, "", "")
        if argv[:3] == ["git", "-C", str(self.root / "checkout")] and argv[3:5] in (["rev-parse", "--verify"], ["symbolic-ref", "-q"], ["rev-parse", "--path-format=absolute"]):
            self.indexes += 1
            return runner_module.Done(0, {"--verify": "cccc333\n", "-q": "runner/drive\n"}.get(argv[4], f"{self.root}/admin-{self.indexes}/index\n"), "")
        if argv[:3] == ["git", "-C", str(self.root / "checkout")] and argv[3] in ("worktree", "reset") or argv[0] == "cp":
            self.mirror = "cccc333"
            return runner_module.Done(0, "", "")
        if argv[:3] == ["git", "-C", str(self.root / "checkout")]:
            assert argv[3:] in (["fetch", "-q", "--no-tags", "--refmap=", "origin", "refs/heads/dev"], ["merge", "-q", "--ff-only", "FETCH_HEAD"])
            return runner_module.Done(128, "", self.advance_error) if self.advance_error and argv[3] == "merge" else runner_module.Done(0, "", "")
        if argv[:7] == ["ccx", "vcs", "pr", "status", "-R", "Forge-AI/monorepo", "--json"]:
            if self.status_error:
                return runner_module.Done(runner_module.TIMED_OUT, "", self.status_error)
            prs = argv[9:] if argv[7:9] == ["--max-age", "300s"] else argv[7:]
            return runner_module.Done(0, json.dumps([{"number": int(pr), "queue": self.queue.get(pr, "queued"), "verdict": "landed" if self.queue.get(pr) == "landed" else "landable"} | self.pr_status.get(pr, {}) for pr in prs]), "")
        if argv[:4] == ["ccx", "vcs", "auth", "status"]:
            assert argv[4:] == ["Forge-AI/monorepo", "--json"]
            return runner_module.Done(1, "", self.quota_error) if self.quota_error else runner_module.Done(0, json.dumps([{"repo": "forge-ai/monorepo", **self.auth}]), "")
        if argv[:3] == ["gh", "api", "graphql"]:
            assert argv[3] == "-f" and len(argv) == 5 and env == {"GH_REPO": "Forge-AI/monorepo"}
            return self.graphql(argv[4].removeprefix("query="))
        if argv[:4] == ["gh", "api", "-X", "PATCH"] and argv[4].startswith("repos/Forge-AI/monorepo/git/refs/heads/"):
            branch = argv[4].removeprefix("repos/Forge-AI/monorepo/git/refs/heads/")
            if branch not in self.grade_refs:
                return runner_module.Done(1, "", "gh: Reference does not exist (HTTP 422)")
            self.grade_refs[branch] = argv[6].removeprefix("sha=")
            return runner_module.Done(0, "{}", "")
        if argv[:5] == ["gh", "api", "-X", "POST", "repos/Forge-AI/monorepo/git/refs"]:
            self.grade_refs[argv[6].removeprefix("ref=refs/heads/")] = argv[8].removeprefix("sha=")
            return runner_module.Done(0, "{}", "")
        if argv[:2] == ["gh", "api"] and argv[2].startswith("repos/Forge-AI/monorepo/commits/") and argv[2].endswith("/status"):
            return runner_module.Done(0, json.dumps({"statuses": self.commit_statuses.get(argv[2].split("/")[4], [])}), "")
        if argv[:2] == ["gh", "api"] and "/compare/" in argv[2] and argv[3:] == ["--jq", ".behind_by"]:
            base, head = argv[2].rsplit("/", 1)[1].split("...")
            moved = sum(1 for merged in self.merged_heads if merged != head) if base == "dev" else 0
            return runner_module.Done(0, f"{self.behind.get(head, 0) + moved}\n", "")
        if argv[:2] == ["gh", "api"] and "/compare/" in argv[2] and argv[3:] == ["--jq", ".status"]:
            return runner_module.Done(0, "ahead\n", "")
        if argv[:2] == ["gh", "api"] and "/compare/" in argv[2]:
            assert argv[2].startswith("repos/Forge-AI/monorepo/compare/") and argv[3:5] == ["-H", "Accept: application/vnd.github.diff"] and argv[5:] in ([], ["--allow-escape-sequences"])
            head = argv[2].rsplit("...", 1)[1]
            if head in self.compare_errors:
                return runner_module.Done(1, "", self.compare_errors[head])
            diff = self.diffs.get(head, f"diff --git a/{head} b/{head}\n")
            if "\x1b" in diff and "--allow-escape-sequences" not in argv:
                return runner_module.Done(1, "", "the response contains terminal escape sequences; pass --allow-escape-sequences to output it anyway")
            return runner_module.Done(0, diff, "")
        if argv == ["gh", "api", "user", "--jq", ".login"]:
            return runner_module.Done(0, f"{self.viewer}\n", "")
        if argv[:2] == ["gh", "api"] and argv[2].startswith("repos/Forge-AI/monorepo/pulls/") and argv[3:] == ["--jq", ".commits"]:
            return runner_module.Done(0, f"{self.pr_commits.get(argv[2].rsplit('/', 1)[1], 1)}\n", "")
        if argv[:3] == ["gh", "api", "--paginate"]:
            assert argv[3].startswith("repos/Forge-AI/monorepo/pulls/") and argv[3].endswith("/files") and argv[4:] == ["--jq", ".[].filename"]
            if self.files_error:
                return runner_module.Done(1, "", self.files_error)
            return runner_module.Done(0, "".join(f"{name}\n" for name in self.files[argv[3].split("/")[4]]), "")
        if argv == ["git", "patch-id", "--stable"]:
            return runner_module.Done(0, f"{hashlib.sha1(stdin.encode()).hexdigest()} {'0' * 40}\n" if stdin else "", "")
        if argv[:3] == ["gh", "pr", "view"] and argv[-1] == "reviewDecision,headRefOid,state":
            assert argv[4:6] == ["--repo", "Forge-AI/monorepo"]
            return runner_module.Done(0, json.dumps(self.reviews[argv[3]]), "")
        if argv[:3] == ["gh", "pr", "view"] and argv[-1] == "reviewDecision,reviews":
            reviewed = self.review_commit or self.current_heads.get(argv[3], "")
            return runner_module.Done(0, json.dumps({"reviewDecision": self.approval_after_push, "reviews": [{"state": "APPROVED", "commit": {"oid": reviewed}}]}), "")
        if argv[:3] == ["gh", "pr", "view"] and argv[-1] == runner_module.ASYNC_STATE:
            return runner_module.Done(0, f"{self.pr_states.get(argv[3], 'OPEN')} {self.current_heads.get(argv[3], '')}\n", "")
        if argv[:3] == ["gh", "pr", "view"] and argv[-1] == ".baseRefName":
            return runner_module.Done(0, f"{self.bases.get(argv[3], 'dev')}\n", "")
        if argv[:3] == ["gh", "pr", "view"] and argv[-1] == ".state":
            return runner_module.Done(0, f"{self.pr_states.get(argv[3], 'OPEN')}\n", "")
        if argv[:3] == ["gh", "pr", "view"] and argv[-1] == ".headRefOid":
            return runner_module.Done(0, f"{self.current_heads[argv[3]]}\n", "") if argv[3] in self.current_heads else runner_module.Done(1, "", "no pull requests found")
        if argv[:3] == ["gh", "pr", "view"] and argv[-1] == "mergeCommit":
            return runner_module.Done(0, json.dumps({"mergeCommit": {"oid": self.merge_commits[argv[3]]} if argv[3] in self.merge_commits else None}), "")
        if argv[:3] == ["gh", "pr", "checks"]:
            assert argv[4:6] == ["--repo", "Forge-AI/monorepo"] and argv[-2:] == ["--json", "name,bucket"]
            checks = self.checks.get(argv[3], [{"name": "buildkite/test", "bucket": "pass"}])
            return runner_module.Done(8 if any(check["bucket"] == "pending" for check in checks) else 0, json.dumps(checks), "")
        if argv[:3] == ["gh", "pr", "merge"]:
            assert argv[4:8] == ["--repo", "Forge-AI/monorepo", "--squash", "--match-head-commit"]
            if argv[3] in self.stack_members:
                return runner_module.Done(1, "", "GraphQL: This pull request is part of a stack and must be merged using the asynchronous merge REST API. (mergePullRequest)")
            self.reviews.setdefault(argv[3], {})["state"] = "MERGED"
            self.merged_heads.append(argv[8])
            return runner_module.Done(0, "", "")
        if argv == [str(self.root / "checkout/tools/ci"), "release", "live", "--json"]:
            return runner_module.Done(0, json.dumps(self.running), "")
        if argv[:3] == [str(self.root / "checkout/tools/ci"), "release", "live"]:
            assert argv[4:] == ["--json"]
            return runner_module.Done(0, json.dumps(self.live.get(argv[3], {})), "")
        if argv[:3] == ["gh", "pr", "view"] and argv[-1] == "labels,headRefName":
            assert argv[4:] == ["--repo", "Forge-AI/monorepo", "--json", "labels,headRefName"]
            return runner_module.Done(0, json.dumps({"labels": [{"name": name} for name in self.pr_labels.get(argv[3], [])], "headRefName": self.head_refs.get(argv[3], f"lane-{argv[3]}")}), "")
        if argv[:3] == ["gh", "pr", "view"] and argv[-1] == "labels":
            assert argv[4:] == ["--repo", "Forge-AI/monorepo", "--json", "labels"]
            if self.pr_label_error:
                return runner_module.Done(1, "", self.pr_label_error)
            return runner_module.Done(0, json.dumps({"labels": [{"name": name} for name in self.pr_labels.get(argv[3], [])]}), "")
        if argv[:3] == ["gh", "pr", "view"] and argv[-1] == "body":
            assert argv[4:] == ["--repo", "Forge-AI/monorepo", "--json", "body"]
            return runner_module.Done(0, json.dumps({"body": self.bodies.get(argv[3], "")}), "")
        if argv[:3] == ["gh", "pr", "view"] and argv[-1] == "comments":
            assert argv[4:] == ["--repo", "Forge-AI/monorepo", "--json", "comments"]
            return runner_module.Done(0, json.dumps({"comments": [{"body": body} for body in self.comments.get(argv[3], [])]}), "")
        if len(argv) > 2 and Path(argv[1]).name == "rulings.py":
            return self.rulings(argv[2:])
        if len(argv) > 1 and Path(argv[1]).name == "ledger.py":
            verb = argv[4]
            assert env == {"CLAUDE_LONG_RUNNING_DRIVE": "d1"}
            if (out := self.ledger_outs.get(verb + (" --dry-run" if "--dry-run" in argv else ""))) is not None:
                return runner_module.Done(*out, "")
            if verb == "landed":
                for row in self.rows:
                    if row["pr"] in self.squashed and f"--pr={row['pr']}" in argv:
                        row["state"] = "landed"
            if verb != "list":
                return runner_module.Done(0, f"{verb} ok\n", "")
            return runner_module.Done(self.ledger_code, json.dumps([{"ours": row.get("state", "open") == "open", **row} for row in self.rows]), "")
        if argv[:2] == ["cci", "tail"]:
            return self.cci_tail(argv)
        if argv == ["cci", "drive", "ls", "--json"]:
            return runner_module.Done(0, json.dumps([{"drive": drive} for drive in self.drives]), "")
        if argv[:2] == ["cci", "claim"]:
            self.claims.append(argv[2:])
            return runner_module.Done(1, "", self.claim_error) if self.claim_error else runner_module.Done(0, f"started build:{argv[argv.index('--build') + 1]}\n", "")
        if argv[:3] == [sys.executable, str(runner_module.SCRIPTS / "drive.py"), "release"]:
            self.registered.append(argv[3:])
            return runner_module.Done(0, "recorded\n", "")
        if argv == ["cci", "who", "--role", "root", "--json"]:
            return runner_module.Done(0, "".join(json.dumps({"lane": "root", "drive": drive}) + "\n" for drive in self.root_drives), "")
        if argv[:5] == [str(self.root / "checkout/tools/ci"), "bk", "api", "-X", "POST"]:
            body, number = json.loads(argv[6]), str(78400 + len(self.grade_starts))
            self.grade_starts.append(body | {"path": argv[7]})
            if self.grade_error:
                return runner_module.Done(1, "", self.grade_error)
            self.builds[number] = {"state": "running", "commit": body["commit"], "jobs": []}
            return runner_module.Done(0, json.dumps({"web_url": f"https://buildkite.com/forge/{argv[7].split('/')[2]}/builds/{number}"}), "")
        if argv[:3] == [str(self.root / "checkout/tools/ci"), "bk", "api"] and argv[3].endswith("/annotations"):
            return runner_module.Done(0, json.dumps(self.annotations.get(argv[3].split("/")[4], [])), "")
        if argv[:3] == [str(self.root / "checkout/tools/ci"), "bk", "api"]:
            build = self.builds[argv[3].rsplit("/", 1)[1]]
            return runner_module.Done(0, build if isinstance(build, str) else json.dumps(build), "")
        if argv == ["cci", "who", "--drive", "d1", "--json"]:
            return runner_module.Done(0, "".join(json.dumps({"lane": lane, "agent_name": name, "live": True, "seen_at": at} | ({"announced_at": self.announced[name]} if name in self.announced else {})) + "\n" for name, (lane, at) in self.seen_as.items()) + "".join(json.dumps({"lane": lane, "live": True, "seen_at": at, "announced_at": at}) + "\n" for lane, at in self.seen.items() if lane not in self.gone), "")
        if argv[:2] == ["cci", "who"]:
            assert argv[2:4] == ["--drive", "d1"] and argv[4] == "--lane" and argv[6:] == ["--json"]
            return runner_module.Done(0, "" if argv[5] in self.gone else json.dumps({"lane": argv[5], "live": True} | ({"seen_at": self.seen[argv[5]]} if argv[5] in self.seen else {})) + "\n", "")
        if argv[:2] == ["cci", "post"]:
            flags = dict(zip(argv[2::2], argv[3::2], strict=True))
            assert flags["--drive"] in self.drives and flags["--lane"] in ("desk-runner", "root") and len(flags["--text"]) <= 400
            if self.post_error:
                return runner_module.Done(1, "", self.post_error)
            if (flags["--kind"], flags["--to"]) == ("state", f"{runner_module.WATCHDOG}@{flags['--drive']}"):
                self.alive.append(flags["--text"])
                attrs = dict(value.split("=", 1) for flag, value in zip(argv[2::2], argv[3::2], strict=True) if flag == "--attr")
                self.beats.append({"seq": len(self.beats) + 1, "drive": flags["--drive"], "lane": "desk-runner", "kind": "state", "topic": flags["--topic"], "to": [flags["--to"]], "at": actions.stamp(self.clock), "text": flags["--text"], "source": "post", "fields": {"attrs": attrs}})
                return runner_module.Done(0, "#0\n", "")
            if (flags["--kind"], flags.get("--topic")) == ("state", "landing-state"):
                self.states.append({key.lstrip("-"): value for key, value in flags.items()})
                return runner_module.Done(0, "#0\n", "")
            if flags["--kind"] == "landed" and "--pr" not in flags:
                return runner_module.Done(1, "", "cci: kind landed requires --pr")
            if flags.get("--topic") == runner_module.board_rows.WRITE_TOPIC:
                return self.board_write(dict(value.split("=", 1) for flag, value in zip(argv[2::2], argv[3::2], strict=True) if flag == "--attr"))
            if stored := self.post_ids.get(flags.get("--id", "")):
                return runner_module.Done(0, f"#{stored} (duplicate)\n", "")
            self.posts.append({key.lstrip("-"): value for key, value in flags.items()})
            if "--id" in flags:
                self.post_ids[flags["--id"]] = len(self.posts)
            return runner_module.Done(0, self.post_out or f"#{len(self.posts)}\n", "")
        if argv[:2] == ["gh", "api"] and argv[2].startswith("repos/Forge-AI/monorepo/pulls/") and argv[3:] == ["--jq", ".mergeable"]:
            self.mergeable_reads.append(argv[2].rsplit("/", 1)[1])
            return runner_module.Done(0, "MERGEABLE\n", "")
        if argv[:4] == ["ccx", "vcs", "stack", "abort"]:
            self.aborts.append((argv[4:], cwd))
            return runner_module.Done(0, "dropped the stopped stack rebase\n", "")
        if argv[:3] == ["ccx", "vcs", "ship"]:
            self.ships.append((argv[3:], cwd))
            self.ship_envs.append(env)
            if self.ship_failure:
                return self.ship_failure
            return runner_module.Done(0, f"submitted {argv[4]} → PR #1 https://example.com/1\n", "")
        raise AssertionError(f"unexpected call {argv}")

    def rulings(self, argv: list[str]) -> runner_module.Done:
        verb, flags = argv[0], argv[1:]
        assert flags[:4] == ["--repo", str(self.root / "checkout"), "--drive", "d1"]
        if verb == "slice" and flags[4] == "--refresh":
            self.refreshes.append(argv)
            return runner_module.Done(1 if self.refresh_error else 0, "", self.refresh_error)
        if verb == "rows":
            return runner_module.Done(0, json.dumps({"rows": self.register}), "")
        repo, _, pr = flags[flags.index("--pr") + 1].rpartition("#")
        assert repo == "Forge-AI/monorepo"
        assert verb == "check-pr", argv
        return runner_module.Done(*self.check_pr.get(pr + (" --incident" if "--incident" in flags else ""), (0, "")), "")

    def graphql(self, query: str) -> runner_module.Done:
        assert query.startswith('query { repository(owner: "Forge-AI", name: "monorepo") { ')
        if self.status_read_error:
            return runner_module.Done(1, "", self.status_read_error)
        if pulls := re.findall(r"p(\d+): pullRequest\(number: \1\) \{ state headRefOid \}", query):
            missing = [pr for pr in pulls if pr in self.missing_prs]
            errors = [{"type": "NOT_FOUND", "path": ["repository", f"p{pr}"], "message": f"Could not resolve to a PullRequest with the number of {pr}."} for pr in missing]
            data = {"data": {"repository": {f"p{pr}": dict(zip(("state", "headRefOid"), self.pr_heads[pr])) if pr in self.pr_heads else None for pr in pulls}}}
            return runner_module.Done(int(bool(missing)), json.dumps(data | ({"errors": errors} if errors else {})), "".join(f"gh: {error['message']}\n" for error in errors))
        if self.trunk and f'object(expression: "{self.trunk[0]["oid"]}") {{ ... on Commit {{ history(' in query:
            return runner_module.Done(0, json.dumps({"data": {"repository": {"object": {"history": {"nodes": self.trunk}}}}}), "")
        heads = re.findall(r'(h\d+): object\(expression: "([0-9a-f]+)"\) \{ \.\.\. on Commit \{ status \{ contexts \{ context state description targetUrl \} \} checkSuites\(last: 10, filterBy: \{checkName: "ai-review"\}\) \{ nodes \{ checkRuns\(last: 1, filterBy: \{checkName: "ai-review"\}\) \{ nodes \{ title startedAt \} \} \} \} \} \}', query)
        commits = {
            alias: {
                "status": {"contexts": [{"description": None, "targetUrl": status.get("target_url")} | {key: value for key, value in status.items() if key != "target_url"} | {"state": status["state"].upper()} for status in statuses]} if (statuses := self.combined_statuses.get(sha, self.commit_statuses.get(sha))) is not None else None,
                "checkSuites": {"nodes": [{"checkRuns": {"nodes": [{"title": title, "startedAt": f"2026-10-27T19:0{index}:00Z"}]}} for index, title in enumerate(self.review_runs.get(sha, []))]},
            }
            for alias, sha in heads
        }
        return runner_module.Done(0, json.dumps({"data": {"repository": commits}}), "")

    def board_write(self, write: dict[str, str]) -> runner_module.Done:
        if write["op"] == "ask":
            self.pr_cards.append({"card": json.loads(write["card"]), "session": write["session"]})
            if self.pr_card_error:
                return runner_module.Done(1, "", self.pr_card_error)
            return runner_module.Done(0, f"#{self.board_seq + len(self.pr_cards)}\n", "")
        carry = {"--session": write["session"], "--block": write["block"], "--row": write["row"], "--head": write["head"], "--note": write["note"]}
        if carry not in self.carries:
            self.carries.append(carry)
            entry = self.board[write["block"]][write["row"]]
            self.board[write["block"]][write["row"]] = entry | {"head": write["head"], "carriedFrom": write["from"]}
        return runner_module.Done(0, "#1\n", "")

    def board_records(self) -> list[dict]:
        blocks = sorted({*(block for block in self.board if block not in self.unknown_blocks), *(["merge-prs"] if self.board_rows else [])})
        snapshot = {key: value for key, value in {"blocks": blocks, "rows": [[pr, head, ""] for pr, head in self.board_rows], "merges": [], "cards": []}.items() if key not in self.snapshot_drops}
        records = [] if self.no_snapshot else [{"seq": self.board_seq, "drive": "d1", "lane": "board-watch", "kind": "note", "topic": "board-rows", "to": ["desk-runner"], "refs": {}, "at": "2026-10-01T17:00:00Z", "text": "board", "fields": {"attrs": {"session": "s-root", "board": json.dumps(snapshot)}}}]
        for block, rows in self.board.items():
            for row, entry in rows.items():
                if entry.get("verdict"):
                    fields = {"session": "s-root", "block": block, "row": row, "verdict": entry["verdict"], "head": entry.get("head") or "", "at": entry.get("at") or "", "carried_from": entry.get("carriedFrom") or ""}
                    records.append({"seq": self.board_seq + len(records), "drive": "d1", "lane": "board-watch", "kind": "owner", "topic": block, "to": ["all"], "refs": {}, "at": "2026-10-01T17:00:00Z", "text": f"{row} {entry['verdict']}", "fields": {"attrs": {name: value for name, value in fields.items() if value}}})
        return records

    def cci_tail(self, argv: list[str]) -> runner_module.Done:
        if "--lane" in argv and argv[argv.index("--lane") + 1] == "board-watch":
            if self.board_error:
                return runner_module.Done(1, "", self.board_error)
            since = argv[argv.index("--since") + 1]
            return runner_module.Done(0, "".join(f"{json.dumps(record)}\n" for record in self.board_records() if not since.isdigit() or record["seq"] > int(since)), "")
        if self.tail_failures:
            self.tail_failures -= 1
            return runner_module.Done(1, "", "cci: daemon restarting")
        if self.tail_error:
            return runner_module.Done(1, "", self.tail_error)
        drive = argv[argv.index("--drive") + 1]
        assert drive in self.drives and "--json" in argv and "--cursor" not in argv
        if "--to" in argv and argv[argv.index("--to") + 1] == f"{runner_module.WATCHDOG}@{drive}":
            topic = next(arg.partition("=")[2] for arg in argv if arg.startswith("--topic="))
            return runner_module.Done(0, "".join(f"{json.dumps(beat)}\n" for beat in [beat for beat in self.beats if beat["drive"] == drive and beat["topic"] == topic][-1:]), "")
        since = argv[argv.index("--since") + 1]
        kinds = [arg.partition("=")[2] for arg in argv if arg.startswith("--kind=")]
        topics = [arg.partition("=")[2] for arg in argv if arg.startswith("--topic=")]
        lanes = [argv[index + 1] for index, arg in enumerate(argv) if arg == "--lane"]
        found = [
            record
            for record in self.records
            if (record["seq"] > int(since) if since.isdigit() else "at" in record and actions.parse_stamp(record["at"]) >= actions.parse_stamp(since))
            and ("--to" not in argv or argv[argv.index("--to") + 1] in record["to"])
            and (not lanes or record.get("lane") in lanes)
            and ("--pr" not in argv or cci_names(record, argv[argv.index("--pr") + 1]))
            and (not kinds or record.get("kind") in kinds)
            and (not topics or record.get("topic") in topics)
            and (drive == "d1" or record.get("drive") == drive)
        ]
        if "--limit" in argv:
            found = found[-int(argv[argv.index("--limit") + 1]) :]
        return runner_module.Done(0, "".join(f"{json.dumps(record)}\n" for record in found), "")

    def ccn(self, argv: list[str]) -> runner_module.Done:
        if argv[:2] == ["attachment", "path"] and argv[2] in self.logs:
            path = self.attachments.get(argv[3])
            return runner_module.Done(0, f"{path}\n", "") if path else runner_module.Done(1, "", f"no attachment {argv[3]}")
        if argv[:2] == ["log", "append"] and argv[2] in self.logs:
            return self.append(argv[3:])
        if argv[:2] == ["log", "show"]:
            return runner_module.Done(0, f"id: {argv[2]}\n", "") if argv[2] in self.logs else runner_module.Done(1, "", f'not-found: entity not found: no log matches "{argv[2]}"')
        if argv == ["log", "list", "--json"]:
            return runner_module.Done(0, json.dumps([{"id": log, "title": title} for log, title in self.logs.items()]), "")
        if argv[:2] == ["log", "add"]:
            assert argv[3:] == ["--label", "program:d1", "--json"]
            log = f"log{len(self.logs) + 1}"
            self.logs[log] = argv[2]
            return runner_module.Done(0, json.dumps({"id": log, "title": argv[2]}), "")
        raise AssertionError(f"unexpected ccn call {argv}")

    def append(self, flags: list[str]) -> runner_module.Done:
        assert flags[0] == "--entry" and flags[2] == "--attach" and flags[4:] == ["--replace"]
        if self.attach_error or self.attach_code != 1:
            return runner_module.Done(self.attach_code, "", self.attach_error)
        source = Path(flags[3])
        stored = self.root / "lfs" / hashlib.sha256(source.read_bytes()).hexdigest()
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_bytes(source.read_bytes())
        self.attachments[source.name] = stored
        self.entries.append(flags[1])
        return runner_module.Done(0, "briefs1\n", "")

    def ok(self, result: dict) -> runner_module.Done:
        return runner_module.Done(0, json.dumps({"ok": True, "result": result}), "")

    def bound_run(self) -> dict:
        return {"id": "run_1", "coordinator_handle": self.coordinator, "consumer_generation": self.generation}

    def orca(self, argv: list[str]) -> runner_module.Done:
        verb = argv[:2]
        if verb == ["orchestration", "run-current"]:
            assert self.sender
            if self.sender in self.dead:
                return runner_module.Done(1, json.dumps({"ok": False, "error": {"code": "terminal_handle_stale", "message": f"{self.sender} is gone"}}), "")
            return self.ok({"run": self.bound_run() if self.coordinator == self.sender else None})
        if verb == ["orchestration", "run-show"]:
            assert argv[2:] == ["--id", "run_1"]
            return self.ok({"run": self.bound_run()})
        if verb == ["orchestration", "run-use"]:
            assert argv[2:] == ["--id", "run_1"]
            if self.run_use_error:
                return runner_module.Done(1, json.dumps({"ok": False, "error": self.run_use_error}), "")
            self.coordinator = self.sender
            self.generation += 1
            return self.ok({"run": self.bound_run()})
        if verb == ["orchestration", "worker-show"]:
            if argv[3] not in self.dispatches:
                return runner_module.Done(1, json.dumps({"ok": False, "error": {"code": "dispatch_not_found", "message": f"no dispatch {argv[3]}"}}), "")
            dispatch = self.dispatches[argv[3]]
            if "settles_in" in dispatch:
                dispatch["settles_in"] -= 1
                if not dispatch["settles_in"]:
                    dispatch["status"] = "failed"
            fence = {"capabilityRevokedAt": "2026-10-01T18:00:00Z"} if dispatch.get("fenced") else {}
            if dispatch.get("missing"):
                return self.ok({"dispatch": {"status": dispatch["status"], **fence}, "observation": {"status": "missing", "exactWorker": False}, "terminal": None})
            return self.ok({"dispatch": {"status": dispatch["status"], **fence}, "observation": {"status": "live", "agentWait": None}, "terminal": {"handle": dispatch["terminal"]}})
        if verb == ["orchestration", "worker-list"]:
            assert argv[2:] == ["--run", "run_1", "--include-remote", "--limit", "100"]
            return self.ok({"workers": [{"dispatchId": dispatch, "dispatchStatus": entry["status"], "agentTerminalHandle": entry["terminal"]} for dispatch, entry in self.dispatches.items()], "page": {"nextCursor": None}})
        if verb in (["orchestration", "worker-stop"], ["orchestration", "worker-abandon"]):
            assert argv[2] == "--dispatch"
            dispatch = self.dispatches[argv[3]]
            if verb[1] == "worker-stop" and dispatch.get("fenced"):
                return runner_module.Done(1, json.dumps({"ok": False, "error": {"code": "dispatch_inactive"}}), "")
            if argv[3] in self.stop_answers:
                answer, settles_in = self.stop_answers[argv[3]]
                dispatch["fenced"] = True
                if settles_in is not None:
                    dispatch["settles_in"] = settles_in
                return runner_module.Done(1, json.dumps({"ok": True, "result": {"state": answer, "processAction": "none", "lastError": "The worker terminal is unproven; no terminal was closed."}}), "")
            dispatch |= {"status": "failed", "fenced": True}
            return self.ok({"state": "stopped" if verb[1] == "worker-stop" else "abandoned", "processAction": "terminated"})
        if verb == ["orchestration", "send"]:
            if self.reject_sends:
                self.reject_sends -= 1
                return runner_module.Done(1, json.dumps({"ok": False, "error": {"code": "invalid_argument", "message": "refused"}}), "")
            if self.garble_sends:
                self.garble_sends -= 1
                return runner_module.Done(1, "", "socket hang up")
            if self.lose_sends:
                self.lose_sends -= 1
                request = f"00000000-0000-0000-0000-{self.sequence:012d}"
                self.sequence += 1
                return runner_module.Done(1, json.dumps({"ok": False, "error": {"code": "runtime_unavailable", "data": {"orchestrationRequestId": request}}}), "")
            if argv[argv.index("--to") + 1].startswith("run:"):
                return self.ok({"message": {"id": self.next_id("msg")}})
            to = argv[argv.index("--to") + 1].removeprefix("dispatch:")
            message = {"id": self.next_id("msg"), "thread_id": argv[argv.index("--thread-id") + 1], "created_at": "2026-10-01T18:00:00Z"}
            self.inboxes.setdefault(self.dispatches[to]["terminal"], []).append(message)
            return self.ok({"message": message})
        if verb == ["orchestration", "reply"]:
            if self.lose_replies:
                self.lose_replies -= 1
                return runner_module.Done(1, "", "socket hang up")
            question, body = argv[argv.index("--id") + 1], argv[argv.index("--body") + 1]
            if self.answered.get(question, body) != body:
                request = f"00000000-0000-0000-0000-{self.sequence:012d}"
                self.sequence += 1
                return runner_module.Done(1, json.dumps({"ok": False, "error": {"code": "answer_conflict", "message": f"Question {question} already has a different answer.", "data": {"orchestrationRequestId": request}}}), "")
            return self.ok({"message": {"id": self.next_id("msg")}})
        if verb == ["orchestration", "request-show"]:
            return self.ok({"state": self.requests.get(argv[3], "absent")})
        if verb == ["orchestration", "check"] and argv[2] == "--run":
            return self.deliver(argv[3:])
        if verb == ["orchestration", "check"]:
            assert argv[2] == "--terminal"
            if argv[3].startswith("run:"):
                assert argv[3:] == ["run:run_1", "--all", "--types", "question"]
                return self.ok({"messages": self.questions})
            if self.mailbox_down:
                return runner_module.Done(1, json.dumps({"ok": False, "error": {"code": "runtime_unavailable"}}), "")
            return self.ok({"messages": self.inboxes.get(argv[3], [])})
        if verb == ["orchestration", "inbox"]:
            assert argv[2:4] == ["--terminal", "run:run_1"]
            if self.inbox_error:
                return self.inbox_error
            messages = self.run_mail()[::-1][:int(argv[5])]
            return self.ok({"messages": messages, "count": len(messages)})
        if verb == ["terminal", "read"]:
            return self.ok({"terminal": {"source": "screen", "tail": self.screens.get(argv[3], IDLE_PROMPT)}})
        if verb == ["terminal", "send"]:
            return self.ok({})
        raise AssertionError(f"unexpected orca call {argv}")

    def run_mail(self) -> list[dict]:
        return sorted((message for message in self.mailbox if message["to_handle"] == "run:run_1"), key=lambda message: message["sequence"])

    def deliver(self, argv: list[str]) -> runner_module.Done:
        assert argv[0] == "run_1"
        if self.coordinator != self.sender:
            return runner_module.Done(1, json.dumps({"ok": False, "error": {"code": "consumer_fenced", "message": "This coordinator terminal is no longer bound to Run run_1."}}), "")
        if argv[1:2] == ["--ack"]:
            assert argv[2:] == [f"delivery_{self.delivery[0]['id']}", "--peek"]
            for message in self.delivery:
                message["read"] = True
            self.delivery = []
            return self.ok({"runId": "run_1", "messages": [], "count": 0, "acknowledged": argv[2]})
        assert argv[1:] == []
        self.delivery = self.delivery or [message for message in self.run_mail() if not message["read"]][:50]
        while self.arriving:
            self.receive(self.arriving.pop(0))
        exposed = [{key: value for key, value in message.items() if key not in ("sequence", "read")} for message in self.delivery]
        return self.ok({"runId": "run_1", "deliveryId": f"delivery_{self.delivery[0]['id']}" if self.delivery else None, "messages": exposed, "count": len(exposed)})

    def receive(self, message: dict) -> dict:
        message = {
            "id": self.next_id("msg"),
            "sequence": self.sequence,
            "type": "status",
            "subject": "checkpoint",
            "body": "",
            "thread_id": None,
            "payload": "{}",
            "from_handle": "term_ctx_a",
            "to_handle": "run:run_1",
            "created_at": self.now().isoformat(),
            "read": False,
            **message,
        }
        self.mailbox.append(message)
        return message

    def stack_enqueue(self, argv: list[str]) -> runner_module.Done:
        if "--status" in argv:
            return runner_module.Done(0, self.statuses, "")
        if "--dequeue" in argv:
            return runner_module.Done(self.dequeue_code, "", "queue still holds the PR" if self.dequeue_code else "")
        tip = " ".join(itertools.takewhile(str.isdigit, argv))
        if "--check" in argv:
            outs = self.gates[tip]
            out = outs.pop(0) if len(outs) > 1 else outs[0]
            return runner_module.Done(*out) if isinstance(out, tuple) else runner_module.Done(0, out, "")
        return runner_module.Done(*self.enqueue_out[tip], "")

    def sends(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["orca", "orchestration", "send"]]

    def enqueues(self) -> list[list[str]]:
        return [call for call in self.calls if Path(call[0]).name == "stack-enqueue" and not {"--check", "--status", "--dequeue"} & set(call)]

    def launch(self, lane: str, dispatch: str, status: str = "dispatched") -> None:
        self.receipts.mkdir(parents=True, exist_ok=True)
        terminal = f"term_{dispatch}"
        (self.receipts / f"{lane}.json").write_text(json.dumps({"result": {"taskId": "task_1", "dispatchId": dispatch}}))
        (self.receipts / f"{lane}.terminal").write_text(terminal + "\n")
        self.dispatches[dispatch] = {"status": status, "terminal": terminal}

    def ack(self, thread: str, verb: str, dispatch: str) -> None:
        key = thread.split("/", 1)[1]
        self.receive(
            {
                "type": "status",
                "subject": f"{verb} {key}",
                "body": "ok",
                "thread_id": thread,
                "payload": json.dumps({"dispatchId": dispatch}),
                "from_handle": self.dispatches[dispatch]["terminal"],
            }
        )


@pytest.fixture
def shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeShell:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return FakeShell(tmp_path)


@pytest.fixture
def config(tmp_path: Path, shell: FakeShell) -> Path:
    briefs = tmp_path / "lfs"
    briefs.mkdir()
    (briefs / "f85c00ba").write_text("Lane brief: rebase onto dev when asked; never touch production.\n")
    shell.attachments[f"{LANE}.full.md"] = briefs / "f85c00ba"
    registry = tmp_path / ".claude/long-running/drives/d1.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"drive": "durable", "cci_drive": "d1", "sessions": ["s-old-root", "s-root"]}))
    path = tmp_path / "runner.json"
    path.write_text(
        json.dumps(
            {
                "store": str(tmp_path / "store"),
                "drive": "d1",
                "view": str(tmp_path / "desk-runner.md"),
                "orca": {"run": "run_1", "desk_inbox": str(tmp_path / "inbox/orca-desk.md"), "briefs": {"repo": str(tmp_path / "checkout"), "log": "briefs1"}},
                "landing": {
                    "repo": "Forge-AI/monorepo",
                    "ledger": "abc",
                    "checkout": str(tmp_path / "checkout"),
                    "fidelity_lane": "fidelity-desk",
                    "merge_mode": "auto",
                    "adapter": {"kind": "graphite-stack-enqueue", "script": ".agents/skills/submit-pr/scripts/stack-enqueue"},
                    "release_owner": "peer:sole-51",
                    "release_cli": "tools/ci release",
                    "policy": {"rule": "prefix", "revision": "a1b2c3d4e5", "source": "Forge-AI/monorepo#28601"},
                },
            }
        )
    )
    return path


def cli(shell: FakeShell, config: Path, *argv: str) -> int:
    return runner_module.main([argv[0], "--config", str(config), *argv[1:]], shell)


def orca_pass(shell: FakeShell, config: Path) -> None:
    assert cli(shell, config, "run", "--desk", "orca", "--once") == 0


def landing_pass(shell: FakeShell, config: Path) -> None:
    assert cli(shell, config, "run", "--desk", "landing", "--once") == 0


def said(shell: FakeShell) -> list[str]:
    return [(Path(post["path"]).read_text() if "path" in post else post["text"]).splitlines()[0] for post in shell.posts]


def escalations(shell: FakeShell) -> list[str]:
    return [Path(post["path"]).read_text().splitlines()[0] if "path" in post else post["text"] for post in shell.posts if post["to"] == "root"]


def incident(tmp_path: Path, name: str) -> actions.Incident:
    return actions.Store(tmp_path / "store").load(name)


@pytest.mark.parametrize("existing_landing", [False, True])
def test_render_decodes_each_incident_once(shell, config, tmp_path, monkeypatch, existing_landing):
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    with store.owned(runner_module.RUNNER, 0) as current:
        current.facts[runner_module.BINDING] = {"bound": True, "at": actions.stamp(shell.now()), "terminal": "term_root", "run": "run_1", "generation": 3, "via": "config"}
        current.facts[runner_module.DEFERRED] = {"incident": "api-fix", "ids": ["first", "second"]}
        for key in ("first", "second"):
            current.accept(key, "escalation", f"held {key}", key, shell.now())
    if existing_landing:
        runner_module.seed_policy(runner)
        runner.accept_policy("new-policy", "whole", "new-head", "owner ruling", "a1b2c3d4e5")
    lane = runner_module.LANE_PREFIX + LANE
    for name, kind in ((lane, "relay"), (runner_module.FINDER, "finder"), (runner_module.FIDELITY, "fidelity")):
        current = actions.Incident(name, name, 0, actions.stamp(shell.now()), {})
        current.accept("job", kind, json.dumps({"pr": 42, "head": "abcdef1234567890"}), "authority", shell.now())
        store.create(current)
    decoded = []
    load = actions.Incident.load

    def counted(record):
        decoded.append(record["incident_id"])
        return load(record)

    monkeypatch.setattr(actions.Incident, "load", staticmethod(counted))
    view = runner.render()
    at = runner_module.pacific(shell.now())
    expected = [f"# desk-runner {at}", f"binding: terminal term_root coordinates run_1 at generation 3, via config; checked {at}", "deferred during api-fix: 2", "- held first", "- held second"]
    if existing_landing:
        expected.append("landing policy: whole at new-head (owner ruling)")
    expected += ["finder #42 abcdef123456: queued", "fidelity #42 abcdef123456: queued", f"## {lane} owner {lane} generation 0", f"- job relay [accepted] accepted {at}", "## desk-landing owner desk-landing generation 0"]
    assert view == "\n".join(expected) + "\n"
    assert sorted(decoded) == sorted([runner_module.RUNNER, lane, runner_module.FINDER, runner_module.FIDELITY, *([runner_module.LANDING] if existing_landing else [])])
    assert store.path(runner_module.LANDING).is_file()


def test_render_reloads_incidents_on_every_call(shell, config, tmp_path):
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    runner_module.seed_policy(runner)
    before = runner.render()
    runner.accept_policy("new-policy", "whole", "new-head", "owner ruling", "a1b2c3d4e5")
    shell.sleep(60)
    after = runner.render()
    assert "landing policy: prefix at a1b2c3d4e5" in before
    assert "landing policy: whole at new-head" in after
    assert f"# desk-runner {runner_module.pacific(shell.now())}\n" in after


def thread(key: str) -> str:
    return f"desk-lane-{LANE}/{key}"


def test_a_relay_delivered_twice_and_a_restart_send_one_command(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert len(shell.sends()) == 1
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R625"].status == "accepted"
    shell.ack(thread("R625"), "started", "ctx_a")
    shell.ack(thread("R625"), "started", "ctx_a")
    orca_pass(shell, config)
    shell.ack(thread("R625"), "done", "ctx_a")
    orca_pass(shell, config)
    action = incident(tmp_path, f"desk-lane-{LANE}").actions["R625"]
    assert (action.status, action.response["text"]) == ("completed", "ok")
    assert len(shell.sends()) == 1
    assert escalations(shell) == []


def test_a_moved_generation_has_one_owner_and_the_stale_dispatch_is_stood_down(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    shell.dispatches["ctx_a"]["status"] = "completed"
    shell.launch(LANE, "ctx_b")
    orca_pass(shell, config)
    container = incident(tmp_path, f"desk-lane-{LANE}")
    assert (container.owner, container.pending_owner, container.owner_generation) == ("ctx_a", "ctx_b", 0)
    assert [call[call.index("--to") + 1] for call in shell.sends()] == ["dispatch:ctx_a", "dispatch:ctx_b"]
    shell.ack(thread("R625"), "started", "ctx_b")
    orca_pass(shell, config)
    container = incident(tmp_path, f"desk-lane-{LANE}")
    assert (container.owner, container.pending_owner, container.owner_generation) == ("ctx_b", None, 1)
    assert container.actions["R625"].status == "started"
    shell.dispatches["ctx_a"]["status"] = "dispatched"
    shell.ack(thread("R625"), "done", "ctx_a")
    orca_pass(shell, config)
    container = incident(tmp_path, f"desk-lane-{LANE}")
    assert container.actions["R625"].status == "started"
    stood = [call for call in shell.calls if call[:3] == ["orca", "orchestration", "reply"]]
    assert len(stood) == 1 and "Do not execute R625" in stood[0][stood[0].index("--body") + 1]


def send_statuses(tmp_path: Path) -> list[str]:
    return [action.status for action in incident(tmp_path, f"desk-lane-{LANE}").actions.values() if action.kind == "send"]


def later_pass(shell: FakeShell, config: Path) -> None:
    shell.clock += runner_module.RESEND_AFTER
    orca_pass(shell, config)
    orca_pass(shell, config)


def test_a_lost_send_waits_for_proof_before_it_is_resent(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.lose_sends = 1
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    for _ in range(3):
        orca_pass(shell, config)
    assert len(shell.sends()) == 1
    assert send_statuses(tmp_path) == ["unverifiable"]
    assert escalations(shell) == []


def test_a_lost_send_found_on_the_lanes_thread_is_settled_and_never_resent(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.lose_sends = 1
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    shell.inboxes["term_ctx_a"] = [{"id": "msg_late", "thread_id": thread("R625"), "created_at": "2026-10-01T18:00:05Z"}]
    later_pass(shell, config)
    later_pass(shell, config)
    assert len(shell.sends()) == 1
    [send] = [action for action in incident(tmp_path, f"desk-lane-{LANE}").actions.values() if action.kind == "send"]
    assert (send.status, send.response) == ("completed", {"message": "msg_late", "at": "2026-10-01T18:00:05Z", "reconciled": True})
    assert escalations(shell) == []


@pytest.mark.parametrize("fault", ["lose_sends", "garble_sends"])
def test_a_lost_send_with_no_receipt_is_resent_on_its_thread_and_its_delivery_tells_the_root_nothing(shell, config, tmp_path, fault):
    shell.launch(LANE, "ctx_a")
    setattr(shell, fault, 1)
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    later_pass(shell, config)
    later_pass(shell, config)
    first, second = shell.sends()
    assert first == second and "--retry-request" not in second
    assert second[second.index("--thread-id") + 1] == thread("R625")
    assert send_statuses(tmp_path) == ["failed", "completed"]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R625"].status == "accepted"
    assert shell.posts == []


def test_a_send_lost_again_and_again_is_resent_once_per_window_until_one_is_delivered(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.lose_sends = 2
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    for sent in range(1, 3):
        orca_pass(shell, config)
        assert len(shell.sends()) == sent
        later_pass(shell, config)
        assert len(shell.sends()) == sent + 1
    for _ in range(3):
        later_pass(shell, config)
    sends = shell.sends()
    assert len(sends) == 3 and all(send == sends[0] for send in sends)
    assert send_statuses(tmp_path) == ["failed"] * 2 + ["completed"]
    assert [action.action_id for action in incident(tmp_path, f"desk-lane-{LANE}").actions.values() if action.kind == "send"] == [
        "send:R625:ctx_a#1",
        "send:R625:ctx_a#1:resend1",
        "send:R625:ctx_a#1:resend2",
    ]
    assert shell.posts == []


def test_a_send_lost_through_every_resend_reports_unacked_once_with_the_count_and_the_relay_text(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.lose_sends = runner_module.RESEND_LIMIT + 5
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    for _ in range(runner_module.RESEND_LIMIT):
        later_pass(shell, config)
    assert len(shell.sends()) == runner_module.RESEND_LIMIT + 1 and shell.posts == []
    for _ in range(3):
        later_pass(shell, config)
    assert len(shell.sends()) == runner_module.RESEND_LIMIT + 1
    assert send_statuses(tmp_path) == ["failed"] * runner_module.RESEND_LIMIT + ["unverifiable"]
    [post] = shell.posts
    assert (post["kind"], post["to"], post["topic"]) == ("blocker", "root", "R625")
    assert post["text"] == (
        f"UNACKED R625 {LANE}: the send to ctx_a and 2 resends lost their response, and the lane has not named R625 on its thread or the Run; "
        "Orca holds no receipt and no message on its thread; nothing more is sent; "
        "check the lane's Run messages before delivering the relay text at this record's path by hand"
    )
    assert Path(post["path"]).read_text().split("\n\n")[1] == "rebase onto dev\n"


@pytest.mark.parametrize(("key", "subject", "status"), [("R625", "done R625: rebased", "completed"), ("inbox@cci#65519", "started cci#65519", "started")])
def test_a_run_status_naming_a_lost_relay_acknowledges_it_and_ends_the_resends(shell, config, tmp_path, key, subject, status):
    shell.launch(LANE, "ctx_a")
    shell.lose_sends = runner_module.RESEND_LIMIT + 5
    cli(shell, config, "relay", "--key", key, "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    later_pass(shell, config)
    shell.receive({"subject": subject, "thread_id": "run:run_1", "payload": json.dumps({"dispatchId": "ctx_a"})})
    for _ in range(runner_module.RESEND_LIMIT + 2):
        later_pass(shell, config)
    assert len(shell.sends()) == 2
    assert send_statuses(tmp_path) == ["failed", "completed"]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions[key].status == status
    assert [line for line in escalations(shell) if line.startswith(("UNVERIFIABLE", "UNACKED"))] == []


def test_a_dispatch_that_settles_between_resends_is_unverifiable_and_nothing_more_is_sent(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.lose_sends = 5
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    later_pass(shell, config)
    later_pass(shell, config)
    shell.dispatches["ctx_a"]["status"] = "completed"
    for _ in range(3):
        later_pass(shell, config)
    assert len(shell.sends()) == 3
    assert send_statuses(tmp_path) == ["failed", "failed", "unverifiable"]
    assert [line for line in escalations(shell) if line.startswith(("UNVERIFIABLE", "UNACKED"))] == [
        f"UNVERIFIABLE desk-lane-{LANE}/send:R625:ctx_a#1:resend2 {LANE}: R625 send to ctx_a lost its response; Orca holds no receipt and no message on its thread; it was not resent"
    ]


def test_a_receipt_orca_holds_for_a_resend_replays_it_and_ends_the_resends(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.lose_sends = 2
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    later_pass(shell, config)
    [request] = [action.response["request_id"] for action in incident(tmp_path, f"desk-lane-{LANE}").actions.values() if action.status == "unverifiable"]
    shell.requests[request] = "completed"
    for _ in range(3):
        later_pass(shell, config)
    sends = shell.sends()
    assert len(sends) == 3 and sends[2][sends[2].index("--retry-request") + 1] == request
    assert send_statuses(tmp_path) == ["failed", "completed"]
    assert shell.posts == []


def test_a_message_that_shows_on_the_thread_between_resends_ends_them(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.lose_sends = 5
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    later_pass(shell, config)
    shell.inboxes["term_ctx_a"] = [{"id": "msg_late", "thread_id": thread("R625"), "created_at": "2026-10-01T18:00:35Z"}]
    for _ in range(3):
        later_pass(shell, config)
    assert len(shell.sends()) == 2
    assert send_statuses(tmp_path) == ["failed", "completed"]
    assert shell.posts == []


def test_a_lost_send_to_a_dispatch_no_longer_live_is_unverifiable_and_never_resent(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.lose_sends = 1
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    shell.dispatches["ctx_a"]["status"] = "completed"
    later_pass(shell, config)
    assert len(shell.sends()) == 1
    assert send_statuses(tmp_path) == ["unverifiable"]
    assert [line for line in escalations(shell) if line.startswith("UNVERIFIABLE")] == [
        f"UNVERIFIABLE desk-lane-{LANE}/send:R625:ctx_a#1 {LANE}: R625 send to ctx_a lost its response; Orca holds no receipt and no message on its thread; it was not resent"
    ]


def test_a_lost_reply_found_on_the_questions_thread_completes_and_is_never_resent(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.lose_replies = 1
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "30s", "--reply-to", "msg_q9")
    orca_pass(shell, config)
    shell.inboxes["term_ctx_a"] = [{"id": "msg_late", "thread_id": "msg_q9", "body": "30s\n\n(R625)", "created_at": "2026-10-01T18:00:05Z"}]
    later_pass(shell, config)
    assert len(replies(shell)) == 1
    assert send_statuses(tmp_path) == ["completed"]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R625"].status == "completed"
    assert escalations(shell) == []


def test_a_lost_send_is_not_resent_while_the_mailbox_cannot_be_read(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.lose_sends = 1
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    shell.mailbox_down = True
    later_pass(shell, config)
    later_pass(shell, config)
    assert len(shell.sends()) == 1
    assert send_statuses(tmp_path) == ["unverifiable"]
    assert escalations(shell) == []
    shell.mailbox_down = False
    later_pass(shell, config)
    assert len(shell.sends()) == 2


def test_a_resend_orca_rejects_is_never_followed_by_a_third_send(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.lose_sends = 1
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    shell.reject_sends = 1
    for _ in range(3):
        later_pass(shell, config)
    assert len(shell.sends()) == 2
    assert send_statuses(tmp_path) == ["failed", "failed"]
    [line] = escalations(shell)
    assert line.startswith(f"SEND-FAILED desk-lane-{LANE}/send:R625:ctx_a {LANE}: R625 to ctx_a failed 2 times: ")


def test_a_reply_orca_refuses_as_already_answered_goes_to_the_dispatch_once_as_a_message(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.answered["msg_q9"] = "300s\n\n(R600)"
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "30s", "--reply-to", "msg_q9")
    orca_pass(shell, config)
    for _ in range(3):
        later_pass(shell, config)
    assert len(replies(shell)) == 1
    [send] = shell.sends()
    assert send[send.index("--to") + 1] == "dispatch:ctx_a"
    assert send[send.index("--thread-id") + 1] == thread("R625")
    assert send[send.index("--body") + 1] == "30s\n\n(R625)"
    assert send_statuses(tmp_path) == ["failed", "completed"]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R625"].status == "completed"
    assert not [call for call in shell.calls if call[:3] == ["orca", "orchestration", "request-show"]]
    assert escalations(shell) == []


def test_a_lost_reply_with_no_receipt_is_resent_past_an_older_answer_on_its_thread(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.inboxes["term_ctx_a"] = [{"id": "msg_old", "thread_id": "msg_q9", "body": "300s\n\n(R600)", "created_at": "2026-10-01T17:55:00Z"}]
    shell.lose_replies = 1
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "30s", "--reply-to", "msg_q9")
    orca_pass(shell, config)
    later_pass(shell, config)
    later_pass(shell, config)
    first, second = replies(shell)
    assert first == second
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R625"].status == "completed"
    assert shell.posts == []


def test_a_lost_root_reply_keyed_by_its_cci_record_is_resent_once_per_window_until_delivered(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.questions = [{"id": "msg_q9", "from_handle": "dispatch:ctx_a", "created_at": "2026-10-01T17:50:00Z"}]
    orca_pass(shell, config)
    decide = cci_record(shell, lane="desk-runner", kind="decide", to=["root"], topic="msg_q9", text=f"DECIDE msg_q9 {LANE}: Question: 300s or 30s? | options")
    cci_record(shell, kind="answer", to=[LANE], re=decide, text="30s NLB delay, 35s preStop.")
    shell.lose_replies = 2
    orca_pass(shell, config)
    for sent in range(1, 3):
        orca_pass(shell, config)
        assert len(replies(shell)) == sent
        later_pass(shell, config)
        assert len(replies(shell)) == sent + 1
    later_pass(shell, config)
    assert len(replies(shell)) == 3
    assert send_statuses(tmp_path) == ["failed"] * 2 + ["completed"]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["cci#34767"].status == "completed"
    assert escalations(shell) == [f"RELAYED cci#34767 {LANE}: reply to question msg_q9 of dispatch ctx_a"]


def test_a_lost_send_orca_recorded_replays_its_receipt_once(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.lose_sends = 1
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    request = next(iter(action.response["request_id"] for action in incident(tmp_path, f"desk-lane-{LANE}").actions.values() if action.kind == "send"))
    shell.requests[request] = "completed"
    orca_pass(shell, config)
    later_pass(shell, config)
    sends = shell.sends()
    assert len(sends) == 2 and sends[1][sends[1].index("--retry-request") + 1] == request
    assert send_statuses(tmp_path) == ["completed"]
    assert escalations(shell) == []


def test_a_missed_start_deadline_escalates_once_and_never_relaunches(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    for _ in range(25):
        orca_pass(shell, config)
        shell.sleep(60)
    lines = escalations(shell)
    assert len(lines) == 1 and "DEADLINE" in lines[0] and "delivered to ctx_a, no started reply" in lines[0]
    assert [call for call in shell.calls if FORBIDDEN & {Path(token).name for token in call}] == []
    assert not [call for call in shell.calls if Path(call[0]).name == "orca-launch.sh"]


def test_a_lane_that_posts_a_head_for_the_relayed_pr_draws_no_deadline(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    cli(shell, config, "relay", "--key", "blocker:29020:cccc333333", "--lane", LANE, "--text", "#29020 cccc333333: rebase onto dev")
    shell.sleep(60)
    shell.records.append({"seq": 34900, "drive": "d1", "lane": LANE, "kind": "head", "to": [], "refs": {"prs": ["acme/service#29020"]}, "text": "api-fix dddd444444", "at": actions.stamp(shell.clock)})
    for _ in range(25):
        orca_pass(shell, config)
        shell.sleep(60)
    assert [line for line in escalations(shell) if "DEADLINE" in line] == []


def test_a_relay_on_a_pr_its_lane_contracted_as_red_by_design_draws_no_deadline(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    cli(shell, config, "relay", "--key", "blocker:29020:cccc333333", "--lane", LANE, "--text", "#29020 cccc333333: graphite FAILING_REQUIRED")
    shell.records.append({"seq": 34900, "drive": "d1", "lane": LANE, "kind": "contract", "to": ["root"], "refs": {"prs": ["acme/service#29020"]}, "text": "C01 update: #29020 fails in run 78325 until step 9 lands, on #29031.", "at": actions.stamp(shell.clock)})
    for _ in range(25):
        orca_pass(shell, config)
        shell.sleep(60)
    assert [line for line in escalations(shell) if "DEADLINE" in line] == []


def test_a_lane_that_heartbeats_after_a_relay_draws_no_deadline_while_it_finishes_its_turn(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    cli(shell, config, "relay", "--key", "blocker:29020:cccc333333", "--lane", LANE, "--text", "#29020 cccc333333: graphite FAILING_REQUIRED")
    for minute in range(45):
        if minute == 5:
            shell.receive({"type": "heartbeat", "subject": f"{LANE} heartbeat"})
        orca_pass(shell, config)
        shell.sleep(60)
    assert [line for line in escalations(shell) if "DEADLINE" in line] == []


def test_a_lane_that_heartbeat_just_before_a_relay_gets_the_grace_and_a_silent_one_still_draws_the_deadline(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    shell.receive({"type": "heartbeat", "subject": f"{LANE} heartbeat", "created_at": (shell.clock - timedelta(minutes=2)).isoformat()})
    for _ in range(20):
        orca_pass(shell, config)
        shell.sleep(60)
    assert [line for line in escalations(shell) if "DEADLINE" in line] == []
    for _ in range(15):
        orca_pass(shell, config)
        shell.sleep(60)
    lines = [line for line in escalations(shell) if "DEADLINE" in line]
    assert len(lines) == 1 and "delivered to ctx_a, no started reply" in lines[0]


def test_a_relay_at_a_newer_head_folds_into_the_one_its_lane_has_not_answered(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    cli(shell, config, "relay", "--key", "blocker:29020:cccc333333", "--lane", LANE, "--text", "#29020 cccc333333: graphite FAILING_REQUIRED")
    orca_pass(shell, config)
    cli(shell, config, "relay", "--key", "blocker:29020:dddd444444", "--lane", LANE, "--text", "#29020 dddd444444: graphite FAILING_REQUIRED")
    orca_pass(shell, config)
    assert len(shell.sends()) == 1
    folded = incident(tmp_path, f"desk-lane-{LANE}").actions["blocker:29020:dddd444444"]
    assert folded.status == "completed" and "blocker:29020:cccc333333" in folded.response["text"]


def wakes(shell: FakeShell) -> list[str]:
    return [call[call.index("--text") + 1] for call in shell.calls if call[:3] == ["orca", "terminal", "send"]]


def test_the_sweep_names_a_prompt_watch_that_stopped_polling_and_leaves_prompts_to_it(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    waiting = {"name": LANE, "role": "worker", "state": "approval", "detail": "Do you want to proceed?", "line": f"WAITING-ON-PROMPT {LANE} worker approval 9m terminal=term_ctx_a: Do you want to proceed?"}
    down = {"name": "900424b6", "role": "watch", "state": "down", "detail": "last poll 9m ago, so the states below are old; start it with `prompt_watch.py start --drive 900424b6`", "line": ""}
    shell.prompt_watch = {"drive": "900424b6", "header": "", "rows": [down, waiting]}
    orca_pass(shell, config)
    orca_pass(shell, config)
    [line] = escalations(shell)
    assert line.startswith("PROMPT-WATCH-DOWN prompt-watch:2026-10-01T18 runner: last poll 9m ago, so the states below are old; start it with")
    assert [call for call in shell.calls if FORBIDDEN & {Path(token).name for token in call}] == []


def test_a_polling_prompt_watch_keeps_the_sweep_quiet_about_prompts(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.prompt_watch = {"drive": "900424b6", "header": "", "rows": [{"name": LANE, "role": "worker", "state": "approval", "detail": "Do you want to proceed?", "line": ""}]}
    orca_pass(shell, config)
    assert escalations(shell) == []


def test_stale_mail_wakes_a_lane_once_and_only_at_its_idle_prompt(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.stale_out = f"STALE {LANE} 12m unread msg_7\n"
    shell.screens["term_ctx_a"] = PICKER
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert wakes(shell) == []
    shell.screens["term_ctx_a"] = IDLE_PROMPT
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert wakes(shell) == ["unread Orca message msg_7; read it now"]


def test_a_relay_types_no_wake_into_a_picker(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.screens["term_ctx_a"] = PICKER
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    assert len(shell.sends()) == 1
    assert wakes(shell) == []


def test_a_quiet_ten_minutes_writes_nothing_and_wakes_no_one(shell, config, tmp_path):
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_a")
    shell.receive({"id": "msg_hb", "type": "status", "subject": "checkpoint", "body": "working", "thread_id": None, "payload": json.dumps({"dispatchId": "ctx_a"}), "from_handle": "term_ctx_a", "lane": LANE})
    for _ in range(10):
        orca_pass(shell, config)
        shell.sleep(60)
    assert escalations(shell) == []
    assert not [call for call in shell.calls if Path(call[0]).name == "claude"]


def test_a_worker_question_escalates_once_to_root_and_the_runner_never_answers_it(shell, config, tmp_path):
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_a")
    question = {"id": "msg_q1", "type": "question", "subject": "rebase?", "body": "may I rebase onto dev", "thread_id": None, "payload": json.dumps({"dispatchId": "ctx_a"}), "from_handle": "term_ctx_a", "lane": LANE}
    shell.receive(question)
    orca_pass(shell, config)
    shell.receive(question)
    orca_pass(shell, config)
    assert escalations(shell) == [f"DECIDE msg_q1 {LANE}: rebase?: may I rebase onto dev"]
    assert not [call for call in shell.calls if call[:3] == ["orca", "orchestration", "reply"]]
    assert not [call for call in shell.calls if Path(call[0]).name == "claude"]


def test_an_ask_from_a_dispatch_handle_escalates_under_its_lane(shell, config, tmp_path):
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_a")
    shell.receive({"id": "msg_q3", "type": "question", "subject": "rebase?", "body": "may I rebase onto dev", "from_handle": "dispatch:ctx_a"})
    orca_pass(shell, config)
    assert escalations(shell) == [f"DECIDE msg_q3 {LANE}: rebase?: may I rebase onto dev"]
    assert not [call for call in shell.calls if call[:3] == ["orca", "orchestration", "reply"]]


def test_a_worker_escalation_escalates_to_root_as_a_decide(shell, config, tmp_path):
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_a")
    shell.receive({"id": "msg_q2", "type": "escalation", "subject": "apply?", "body": "A) wait B) apply now", "thread_id": None, "payload": json.dumps({"dispatchId": "ctx_a"}), "from_handle": "term_ctx_a", "lane": LANE})
    orca_pass(shell, config)
    assert escalations(shell) == [f"DECIDE msg_q2 {LANE}: apply?: A) wait B) apply now"]
    assert not [call for call in shell.calls if call[:3] == ["orca", "orchestration", "reply"]]


def test_a_relayed_question_carries_its_whole_body_by_path(shell, config, tmp_path):
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_a")
    body = "The scope surprise is " + "x" * 2_955 + " A) take it B) leave it"
    shell.receive({"id": "msg_q9", "type": "question", "subject": "scope?", "body": body, "thread_id": None, "payload": json.dumps({"dispatchId": "ctx_a"}), "from_handle": "term_ctx_a", "lane": LANE})
    orca_pass(shell, config)
    [post] = [post for post in shell.posts if post["to"] == "root"]
    receipt = Path(post["path"]).read_text()
    assert len(body) == 3_000 and len(post["text"]) <= runner_module.CCI_TEXT
    assert post["text"].startswith("DECIDE msg_q9") and receipt.startswith("DECIDE msg_q9")
    assert f"scope?\n\n{body}\n" in receipt


def test_a_worker_done_report_carries_its_body_by_path(shell, config, tmp_path):
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_a")
    shell.receive({"id": "msg_d1", "type": "worker_done", "subject": "shipped", "body": "PR #12 open; " + "y" * 1_000, "payload": json.dumps({"dispatchId": "ctx_a", "outcome": "succeeded"})})
    orca_pass(shell, config)
    [post] = [post for post in shell.posts if post["to"] == "root"]
    assert post["text"] == f"OUTCOME msg_d1 {LANE}: worker_done succeeded dispatch=ctx_a: shipped"
    assert Path(post["path"]).read_text().endswith("shipped\n\nPR #12 open; " + "y" * 1_000 + "\n")


def test_a_launch_runs_detached_once_and_its_relays_wait_for_the_dispatch(shell, config, tmp_path):
    brief = tmp_path / "fix.md"
    brief.write_text("ccx: place=local\nbrief")
    shell.launch_line = f"{LANE} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "sol", "--effort", "xhigh", "--brief", str(brief))
    cli(shell, config, "relay", "--key", "R640", "--lane", LANE, "--text", "second 429")
    shell.launch(LANE, "ctx_n")
    orca_pass(shell, config)
    orca_pass(shell, config)
    launches = [call for call in shell.calls if Path(call[0]).name == "orca-launch.sh"]
    assert launches == [[str(runner_module.LAUNCHER), LANE, "sol", "xhigh", str(brief)]]
    container = incident(tmp_path, f"desk-lane-{LANE}")
    assert container.actions["R638"].status == "verified"
    assert [call[call.index("--to") + 1] for call in shell.sends()] == ["dispatch:ctx_n"]


def test_the_orca_runner_starts_one_stall_pass_at_a_time_on_the_cadence_runner_json_sets(shell, config, tmp_path):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))

    def passes() -> int:
        return sum(call[1:2] == [str(runner_module.STALL_WATCH)] for call in shell.calls)

    runner.stall_watch()
    assert passes() == 0
    config.write_text(json.dumps(json.loads(config.read_text()) | {"stall": {"every_minutes": 5}}))
    runner.stall_watch()
    runner.stall_watch()
    assert passes() == 1
    shell.clock += timedelta(minutes=6)
    runner.stall_watch()
    assert passes() == 1
    shell.stall_pass.code = 0
    runner.stall_watch()
    assert passes() == 2
    shell.clock += timedelta(minutes=3)
    runner.stall_watch()
    assert passes() == 2
    assert [call for call in shell.calls if call[1:2] == [str(runner_module.STALL_WATCH)]][-1] == [sys.executable, str(runner_module.STALL_WATCH), "--config", str(config.resolve())]


def launches(shell: FakeShell) -> list[list[str]]:
    return [call for call in shell.calls if Path(call[0]).name == "orca-launch.sh"]


def test_an_incident_launch_on_load_starts_anyway_while_a_normal_launch_beside_it_is_held(shell, config, tmp_path):
    """drive-watchdog-2, cci #157631: the load gate refused incident fix lanes R84845 and R84846 at load 45 and 71."""
    brief = tmp_path / "fix.md"
    brief.write_text("ccx: place=local\nbrief")
    shell.cpu_load = 140
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "incident", "--effort", "xhigh", "--brief", str(brief))
    cli(shell, config, "launch", "--key", "R639", "--lane", "other-lane", "--model", "opus", "--effort", "xhigh", "--brief", str(brief))
    shell.launch(LANE, "ctx_n")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, "incident", "xhigh", str(brief)]]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "verified"
    assert incident(tmp_path, "desk-lane-other-lane").actions["R639"].status == "accepted"
    assert [line.split()[0] for line in escalations(shell)] == ["LOAD-HIGH", "LOAD-INCIDENT", "LAUNCHED"]
    assert escalations(shell)[1] == f"LOAD-INCIDENT desk-lane-{LANE}/R638:load {LANE}: R638 launches on load 140 above 8 cores: an incident fix lane is never held for load"


def test_an_owner_directed_launch_on_load_fails_at_once_and_tells_the_root_to_move_the_load(shell, config, tmp_path):
    brief = tmp_path / "fix.md"
    brief.write_text("ccx: place=local\nbrief")
    shell.cpu_load = 140
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "opus", "--effort", "xhigh", "--brief", str(brief), "--owner-directed")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert launches(shell) == []
    action = incident(tmp_path, f"desk-lane-{LANE}").actions["R638"]
    assert action.status == "failed" and action.reason == "R638 refused on load 140 above 8 cores; nothing was launched, and the root launches it in-process or once the load is moved"
    assert escalations(shell) == [
        "LOAD-HIGH load:2026-10-01T18:0 runner: load 140 above 8 cores: Orca launches are refused while it lasts, because a worktree checkout times out under it. root: move the load off this Mac now; a pass, backfill, dry run, or bulk rewrite runs on in-region compute",
        f"LAUNCH-FAILED desk-lane-{LANE}/R638 {LANE}: {action.reason}",
    ]
    assert [post["kind"] for post in shell.posts] == ["blocker", "defect"]


def test_a_launch_held_by_load_starts_nothing_then_fails_loudly_at_the_hold_deadline(shell, config, tmp_path):
    brief = tmp_path / "fix.md"
    brief.write_text("ccx: place=local\nbrief")
    shell.cpu_load = 40
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "opus", "--effort", "xhigh", "--brief", str(brief))
    for _ in range(4):
        orca_pass(shell, config)
        shell.sleep(60)
    assert launches(shell) == [] and [line.split()[0] for line in escalations(shell)] == ["LOAD-HIGH", "LAUNCH-STALLED"]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "accepted"
    for _ in range(3):
        shell.sleep(60)
        orca_pass(shell, config)
    assert launches(shell) == []
    action = incident(tmp_path, f"desk-lane-{LANE}").actions["R638"]
    assert action.status == "failed" and "load 40 above 8 cores" in action.reason
    [load, _, line] = escalations(shell)
    assert load.startswith("LOAD-HIGH load:") and "LAUNCH-HELD" in line and "R638 launch failed after 5m held" in line
    [mail] = [call for call in shell.sends() if call[call.index("--to") + 1] == "run:run_1"]
    assert mail[mail.index("--subject") + 1] == f"LAUNCH-HELD {LANE}"


def test_show_marks_a_load_held_launch_held_with_its_reason_and_age(shell, config, tmp_path, capsys):
    brief = tmp_path / "fix.md"
    brief.write_text("brief")
    shell.cpu_load = 40
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "opus", "--effort", "xhigh", "--brief", str(brief))
    shell.sleep(180)
    capsys.readouterr()
    cli(shell, config, "show")
    assert "- R638 launch [HELD load 40 above 8 cores for 3m] accepted 11:00" in capsys.readouterr().out
    shell.cpu_load = 1.0
    cli(shell, config, "show")
    assert "- R638 launch [accepted] accepted 11:00" in capsys.readouterr().out


def stalled(shell: FakeShell) -> list[str]:
    return [line for line in escalations(shell) if line.startswith("LAUNCH-STALLED ")]


def test_an_accepted_launch_with_no_dispatch_a_minute_on_logs_one_launch_stalled_through_a_live_clock(shell, config, tmp_path, monkeypatch):
    """Papercut 3dfdc2f: launches R10190 and R10191 sat accepted with no dispatch at load 11 of 32 cores, and nothing told the root."""
    live_clock(tmp_path, shell)
    shell.cpu_load = 11
    monkeypatch.setattr(shell, "cores", lambda: 32)
    brief = tmp_path / "fix.md"
    brief.write_text("brief")
    lanes = {"R10190": "inc-a-evidence", "R10191": "inc-a-fix"}
    for key, lane in lanes.items():
        cli(shell, config, "launch", "--key", key, "--lane", lane, "--model", "incident", "--effort", "xhigh", "--brief", str(brief))
    shell.orca_down = UNREACHABLE_FAILURES[0]
    orca_pass(shell, config)
    shell.sleep(59)
    orca_pass(shell, config)
    assert stalled(shell) == []
    shell.sleep(1)
    for _ in range(3):
        orca_pass(shell, config)
        shell.sleep(60)
    assert sorted(stalled(shell)) == [
        f"LAUNCH-STALLED stalled:desk-lane-{lane}/{key} {lane}: {key} accepted at 11:00 has no dispatch 60 s on, with the 1-minute load 11 of 32 cores;"
        f" withdraw it with `orca-desk: withdraw {key}`, then launch it by hand: `orca-launch.sh {lane} incident xhigh {brief}`"
        for key, lane in lanes.items()
    ]
    assert [post["kind"] for post in shell.posts if post["text"].startswith("LAUNCH-STALLED")] == ["blocker", "blocker"]
    assert launches(shell) == [] and {incident(tmp_path, f"desk-lane-{lane}").actions[key].status for key, lane in lanes.items()} == {"accepted"}


def test_a_launch_the_runner_started_logs_no_launch_stalled(shell, config, tmp_path):
    brief = tmp_path / "fix.md"
    brief.write_text("ccx: place=local\nbrief")
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "opus", "--effort", "xhigh", "--brief", str(brief))
    orca_pass(shell, config)
    shell.sleep(5 * 60)
    orca_pass(shell, config)
    assert len(launches(shell)) == 1 and stalled(shell) == []


def gate(*lines: str, would: str = "") -> str:
    return "\n".join(lines) + (f"\nwould enqueue {would} in one call\n" if would else "\nenqueued nothing: the bottom PR is BLOCKED\n")


def grade(shell: FakeShell, pr: str, text: str, lane: str = "fidelity-desk", finder: bool = True) -> int:
    seq = 34766 + len(shell.records)
    shell.records.append({"seq": seq, "drive": "d1", "lane": lane, "kind": "review", "to": ["root"], "refs": {"prs": [int(pr)]}, "text": f"fidelity: {text}", "at": actions.stamp(shell.clock)})
    if finder and text.startswith("ok") and (head := runner_module.graded_head(text)):
        finder_verdict(shell, pr, f"ok {head} no findings")
    return seq


def finder_verdict(shell: FakeShell, pr: str, text: str, lane: str = "finder-desk") -> int:
    seq = 34766 + len(shell.records)
    shell.records.append({"seq": seq, "drive": "d1", "lane": lane, "kind": "review", "to": ["root"], "refs": {"prs": [f"Forge-AI/monorepo#{pr}"]}, "text": f"finder: {text}"})
    return seq


def test_a_finder_review_that_names_the_pr_only_in_its_text_lets_the_head_enqueue(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.", finder=False)
    shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": "finder-desk", "kind": "review", "to": ["root"], "refs": {}, "text": "finder: ok cccc333 on [29020], no findings"})
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]
    assert not [call for call in shell.calls if call[:2] == ["cci", "tail"] and "--kind=head" in call and "--pr" in call]


def test_a_finder_lgtm_reads_as_ok(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.", finder=False)
    finder_verdict(shell, "29020", "lgtm cccc333 (findings schema verdict)")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


def grade_gated_heads_ok(shell: FakeShell) -> None:
    for outs in shell.gates.values():
        for out in outs:
            for verdict in runner_module.merge_queue.VERDICT_LINE.finditer(out[1] if isinstance(out, tuple) else out):
                grade(shell, verdict["pr"], f"ok F307 at {verdict['sha'][:7]}")


def stack_rows(**states: str) -> list[dict]:
    return [
        {"pr": "28997", "lane": "artifact-retry", "branch": "a/1", "base": "dev", "head": "aaaa111111", "state": states.get("28997", "open"), "reported_head": "aaaa111111", "landed_sha": "f00d000001"},
        {"pr": "29016", "lane": "artifact-retry", "branch": "a/2", "base": "a/1", "head": "bbbb222222", "state": states.get("29016", "open"), "reported_head": "bbbb222222", "landed_sha": "f00d000002"},
        {"pr": "29020", "lane": LANE, "branch": "a/3", "base": "a/2", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
    ]


@pytest.mark.parametrize("pending", [{"pr": "38417", "ours": True}, {"pr": "38417", "ours": True, "branch": None, "base": None}, {"pr": "38417", "ours": True, "branch": "", "base": ""}])
@pytest.mark.parametrize(("held", "covered"), [([], set()), (["38417"], {"38417"}), (["29016"], {"29016", "29020"}), (["28997"], {"28997", "29016", "29020"})])
def test_pending_pr_metadata_creates_no_hold_ancestry_edge(pending, held, covered):
    rows = {row["pr"]: row for row in [*stack_rows(), pending]}
    assert runner_module.under_hold(rows, held) == covered
    assert rows["38417"] == pending


def test_stack_trees_do_not_walk_an_unknown_base_into_a_pending_pr(shell, config, tmp_path):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    landing = runner_module.Landing(runner, runner.config.landing)
    rows = {row["pr"]: dict(row, ours=True) for row in stack_rows()}
    rows["38416"] = {"pr": "38416", "branch": "unknown-base", "ours": True}
    rows["38417"] = {"pr": "38417", "ours": True}
    assert landing.trees(rows) == [["29020"], ["38416"]]
    assert rows["38417"] == {"pr": "38417", "ours": True}


def test_the_accepted_prefix_policy_beats_a_stale_whole_stack_ruling(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 BLOCKED cccc333333 ai-review pending", would="#28997 #29016")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29016"] = (0, "enqueue #28997 #29016: {}\n#28997 QUEUED aaaa111111 graphite QUEUED_TO_MERGE\n")
    shell.gates["29016"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", would="#28997 #29016")]
    landing_pass(shell, config)
    assert cli(shell, config, "policy", "--key", "L260", "--landing", "whole", "--revision", "e802ae614e", "--source", "sole checkout") == 0
    lines = escalations(shell)
    assert len(lines) == 1 and "STALE-POLICY L260" in lines[0] and "prefix at a1b2c3d4e5" in lines[0]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29016", "--partial"]]
    assert all("--whole" not in call for call in shell.calls if Path(call[0]).name == "stack-enqueue")
    shell.rows = stack_rows(**{"28997": "landed", "29016": "landed"})
    landing_pass(shell, config)
    landing_pass(shell, config)
    enqueue = next(action for action in incident(tmp_path, "desk-landing").actions.values() if action.kind == "enqueue")
    assert enqueue.status == "verified" and enqueue.verification_receipt["landed"] == {"28997": "f00d000001", "29016": "f00d000002"}
    restacks = [call for call in shell.sends() if "restack" in call[call.index("--body") + 1]]
    assert len(restacks) == 1 and restacks[0][restacks[0].index("--to") + 1] == "dispatch:ctx_a"
    assert "#29020" in restacks[0][restacks[0].index("--body") + 1]


def test_a_prefix_holding_a_pr_no_drive_lane_opened_is_never_enqueued(shell, config, tmp_path):
    shell.rows = [dict(row, ours=row["pr"] != "28997") for row in stack_rows()]
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == []
    lines = [line for line in said(shell) if line.startswith("FOREIGN")]
    assert len(lines) == 1 and "#28997" in lines[0] and "nothing was enqueued" in lines[0]


def test_a_labelled_row_the_gate_admits_is_not_foreign_to_the_prefix_check_in_the_same_pass(shell, config, tmp_path):
    shell.rows = [dict(row, ours=row["pr"] != "28997") for row in stack_rows()]
    shell.pr_labels["28997"] = ["drive:d1"]
    shell.head_refs["28997"] = "a/1"
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020")]
    landing_pass(shell, config)
    assert len(registrations(shell)) == 1 and shell.enqueues() == [] and escalations(shell) == []
    assert len([call for call in shell.calls if call[-1] == "labels,headRefName"]) == 1


def registrations(shell: FakeShell) -> list[list[str]]:
    return [call[4:] for call in shell.calls if len(call) > 4 and Path(call[1]).name == "ledger.py" and call[4] == "register"]


def test_a_rowless_prefix_pr_carrying_the_drive_label_is_registered_under_its_branch_and_enqueued(shell, config, tmp_path):
    shell.rows = stack_rows()[1:]
    shell.pr_labels["28997"] = ["drive:d1"]
    shell.head_refs["28997"] = "a/1"
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #28997 #29016 #29020: {}\n#28997 QUEUED aaaa111111 graphite QUEUED_TO_MERGE\n")
    landing_pass(shell, config)
    assert registrations(shell) == [["register", "--ledger", "abc", "--repo", "Forge-AI/monorepo", "--lane", "a/1", "--pr", "28997"]]
    assert shell.enqueues() == [] and not [line for line in escalations(shell) if "FOREIGN" in line]
    shell.rows = stack_rows()
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]
    assert not [line for line in escalations(shell) if "FOREIGN" in line]


def test_an_opened_or_head_record_admits_no_pr(shell, config, tmp_path):
    """#41141: a lane's opened record named a PR another engineer opened, and the runner admitted it from that text; record text never admits a PR now."""
    shell.rows = [*stack_rows(), {"pr": "41141", "branch": "other/fix-mount", "base": "dev", "head": "4114141141", "state": "open", "ours": False}]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 held")]
    shell.records += [
        {"seq": 104650, "drive": "d1", "lane": "inc-mount-fix", "kind": "opened", "to": [], "refs": {"prs": [41141]}, "text": "opened #41141", "source": "post", "at": "2026-10-21T09:00:00Z"},
        {"seq": 104651, "drive": "d1", "lane": "inc-mount-fix", "kind": "head", "to": [], "refs": {"prs": ["Forge-AI/monorepo#41141"]}, "text": "#41141 4114141141", "source": "post", "at": "2026-10-21T09:00:01Z"},
    ]
    landing_pass(shell, config)
    assert registrations(shell) == []
    [line] = [line for line in escalations(shell) if line.startswith("NOT-OURS")]
    assert "#41141" in line and "the drive did not admit it" in line


def test_an_unadmitted_row_carrying_the_drive_label_is_registered_under_its_branch_reading_github_once_per_head(shell, config, tmp_path):
    """#40902 and #40941 on Oct 20: a refresh rowed each before any lane registered it, and the desk skipped both as NOT-OURS for want of a record; the PR's own label admits it now."""
    shell.rows = [*stack_rows(), {"pr": "40902", "branch": "durable-step-6a-detach-route", "base": "dev", "head": "6b42b36b990", "state": "open", "ours": False}, {"pr": "40941", "branch": "step-6-fork-detach", "base": "dev", "head": "e615fb42c6f", "state": "open", "ours": False}]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 held")]
    shell.pr_labels["40902"] = ["drive:d1"]
    shell.head_refs.update({"40902": "durable-step-6a-detach-route", "40941": "step-6-fork-detach"})
    landing_pass(shell, config)
    assert registrations(shell) == [["register", "--ledger", "abc", "--repo", "Forge-AI/monorepo", "--lane", "durable-step-6a-detach-route", "--pr", "40902"]]
    [line] = [line for line in escalations(shell) if line.startswith("NOT-OURS")]
    assert "#40941" in line
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    landing = runner_module.Landing(runner, runner.config.landing)
    assert landing.admit({"40941": "e615fb42c6f"}) == set() and landing.admit({"40941": "e615fb42c6f"}) == set()
    assert len([call for call in shell.calls if call[-1] == "labels,headRefName" and call[3] == "40941"]) == 2


def test_a_row_ledger_unadmit_withdrew_is_never_read_for_admission(shell, config, tmp_path):
    shell.rows = [*stack_rows(), {"pr": "41141", "branch": "other/fix-mount", "base": "dev", "head": "4114141141", "state": "open", "ours": False, "unadmitted": "2026-10-21T09:05:00Z"}]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 held")]
    shell.pr_labels["41141"] = ["drive:d1"]
    landing_pass(shell, config)
    assert registrations(shell) == [] and not [call for call in shell.calls if call[-1] == "labels,headRefName"]


def test_a_rowless_rider_carrying_the_drive_label_is_registered_instead_of_escalated(shell, config, tmp_path):
    shell.rows = fork_rows()[:3]
    shell.pr_labels["36530"] = ["drive:d1"]
    shell.head_refs["36530"] = "ro/rider"
    rider = "rides the batch through #36463 but is neither named nor below #{}; name it too or pass --partial to leave it out"
    shell.gates["36487"] = [gate("#36463 GREEN aaaa111111 graphite READY", "#36487 GREEN bbbb222222 graphite READY", "#36501 BLOCKED cccc333333 " + rider.format(36487), "#36530 BLOCKED 9999aaaaaa " + rider.format(36487))]
    shell.gates["36487 36501"] = [gate("#36463 GREEN aaaa111111 graphite READY", "#36487 GREEN bbbb222222 graphite READY", "#36501 GREEN cccc333333 graphite READY", "#36530 BLOCKED 9999aaaaaa " + rider.format("36487 #36501"))]
    landing_pass(shell, config)
    assert ["register", "--ledger", "abc", "--repo", "Forge-AI/monorepo", "--lane", "ro/rider", "--pr", "36530"] in registrations(shell)
    assert not [line for line in escalations(shell) if "FOREIGN" in line] and shell.enqueues() == []


def test_a_tip_no_drive_lane_opened_never_reaches_stack_enqueue(shell, config, tmp_path):
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333", "ours": False}]
    landing_pass(shell, config)
    assert not [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]


def test_an_open_pr_skipped_as_not_ours_escalates_once_per_head(shell, config, tmp_path):
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333", "ours": False}]
    landing_pass(shell, config)
    landing_pass(shell, config)
    lines = escalations(shell)
    assert len(lines) == 1 and "NOT-OURS" in lines[0] and "#29020" in lines[0] and "ledger.py register --lane <lane> --branch-prefix <its branch> or --pr 29020" in lines[0]
    shell.rows[0] |= {"head": "dddd444444", "reported_head": "dddd444444"}
    landing_pass(shell, config)
    assert len(escalations(shell)) == 2


def test_a_superseding_policy_naming_the_accepted_revision_applies(shell, config, tmp_path):
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #28997 #29016 #29020: {}\n")
    landing_pass(shell, config)
    cli(shell, config, "policy", "--key", "L300", "--landing", "whole", "--revision", "f9e8d7c6b5", "--supersedes", "a1b2c3d4e5", "--source", "owner ruling")
    assert escalations(shell) == []
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111112 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020")]
    grade_gated_heads_ok(shell)
    landing_pass(shell, config)
    assert "--whole" in shell.enqueues()[-1]


def test_an_approval_that_arrived_before_the_send_is_never_asked_for(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 changes requested by poetic-svc on bbbb222222"), gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    landing_pass(shell, config)
    assert not [call for call in shell.sends() if "changes requested" in call[call.index("--body") + 1]]
    assert escalations(shell) == []


def test_a_blocker_still_present_at_send_time_goes_to_the_lane_once(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 changes requested by poetic-svc on cccc333333")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    asked = [call for call in shell.sends() if "changes requested" in call[call.index("--body") + 1]]
    assert len(asked) == 1


def relayed(shell: FakeShell) -> list[str]:
    return [call[call.index("--body") + 1].split("\n\n")[0] for call in shell.sends()]


ONE_ROW = {"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}


@pytest.mark.parametrize(
    "detail",
    [
        "graphite FAILING_REQUIRED",
        "fidelity failure: cci #61407 fidelity: missing F307 at cccc333",
        "graphite MERGE_CONFLICTS; run ccx vcs stack submit from the branch's worktree: cd /w && ccx vcs stack submit",
        "conflicts with origin/dev in go/a.go; run ccx vcs stack submit from the branch's worktree: cd /w && ccx vcs stack submit",
        "GitHub reads CONFLICTING (DIRTY) against dev; run ccx vcs stack submit from the branch's worktree: cd /w && ccx vcs stack submit",
        "changes requested by forge-pr-reviewer[bot] on cccc333333",
        "ejected from the merge queue Oct 13, 1:06 AM UTC because Failed CI (test-unit), and no head was pushed since; read the failure in its Merge activity comment and push the fix as a new head before enqueueing again",
    ],
)
def test_red_ci_a_conflict_a_review_ask_or_an_ejection_goes_to_the_lane(shell, config, tmp_path, detail):
    shell.launch(LANE, "ctx_a")
    shell.rows = [ONE_ROW]
    shell.gates["29020"] = [gate(f"#29020 BLOCKED cccc333333 {detail}")]
    landing_pass(shell, config)
    assert relayed(shell) == [f"#29020 cccc333333: {detail}"]


@pytest.mark.parametrize(
    ("posted", "gone", "to"),
    [
        ([], set(), "chat-move-r4"),
        ([("chat-move-r4", "opened"), ("chat-move-land-2", "head"), ("one-session-open-restack", "ready")], set(), "one-session-open-restack"),
        ([("chat-move-r4", "opened"), ("chat-move-land-2", "claim")], {"chat-move-land-2"}, "chat-move-land-2,root"),
        ([("a1f2e3d4a5b6c7d8e", "opened"), ("p3-census", "report", "clean READY #29020 green")], set(), "p3-census"),
        ([("chat-move-land-2", "head"), ("root", "report", "CI is still red on #29020")], set(), "chat-move-land-2"),
    ],
)
def test_a_blocker_goes_to_the_newest_head_opened_ready_or_claim_poster_and_the_root_when_that_lane_is_gone(shell, config, posted, gone, to):
    shell.rows = [ONE_ROW | {"lane": "chat-move-r4"}]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 graphite FAILING_REQUIRED")]
    shell.records = [
        {"seq": 34766 + index, "drive": "d1", "lane": lane, "kind": kind, "to": ["root"], "refs": {"prs": [29020]}, "text": text[0] if text else f"#29020 {kind}", "source": "post", "at": actions.stamp(shell.clock)}
        for index, (lane, kind, *text) in enumerate(posted)
    ]
    shell.gone = gone
    landing_pass(shell, config)
    assert [post["to"] for post in shell.posts if post["kind"] == "blocker"] == [to]


@pytest.mark.parametrize(
    ("claimer", "refs", "text", "to"),
    [
        ("p8a-step6b-creds-3", [], "takes #29016 #29020 and the forest input", "p8a-step6b-creds-3"),
        ("p8a-step6b-creds-3", [], "takes Forge-AI/monorepo#29020 and yasyf/cc-skills#704", "p8a-step6b-creds-3"),
        ("p8a-step6b-creds-3", [], "takes #29016; it waits on #29020", "p8a-step6b-creds-2"),
        ("p8a-step6b-creds-3", [], "takes yasyf/cc-skills#29020", "p8a-step6b-creds-2"),
        ("p8a-step6b-creds-3", ["yasyf/cc-skills#29020"], "takes its tooling PR", "p8a-step6b-creds-2"),
        ("root", [], "p8a-step6b-creds-3 owns #29020", "p8a-step6b-creds-2"),
        ("root", ["Forge-AI/monorepo#29020"], "p8a-step6b-creds-3 owns #29020", "p8a-step6b-creds-2"),
        ("fidelity-desk-3", [], "fidelity-desk-3 claim: grading #29020 cccc333", "p8a-step6b-creds-2"),
        ("fidelity-desk", ["Forge-AI/monorepo#29020"], "grading #29020 cccc333", "p8a-step6b-creds-2"),
    ],
    ids=["text-only", "repo-qualified", "mentioned-after-the-clause", "another-repo-in-text", "another-repo-in-refs", "root-text", "root-refs", "fidelity-successor-text", "fidelity-refs"],
)
def test_a_lanes_claim_takes_the_blocker_for_the_prs_it_lists_and_roots_and_desks_do_not(shell, config, claimer, refs, text, to):
    shell.rows = [ONE_ROW | {"lane": "chat-move-r4"}]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 graphite FAILING_REQUIRED")]
    shell.records = [
        {"seq": 34766, "drive": "d1", "lane": "p8a-step6b-creds-2", "kind": "head", "to": ["root"], "refs": {"prs": [29020]}, "text": "#29020 head", "source": "post", "at": actions.stamp(shell.clock)},
        {"seq": 34767, "drive": "d1", "lane": claimer, "kind": "claim", "to": ["root"], "refs": {"prs": refs}, "text": text, "source": "post", "at": actions.stamp(shell.clock)},
    ]
    landing_pass(shell, config)
    assert [post["to"] for post in blockers(shell)] == [to]


def post_body(post: dict) -> str:
    return Path(post["path"]).read_text() if "path" in post else post["text"]


@pytest.mark.parametrize(("minutes", "to"), [(59, "p8e-desktop-ship-3,root"), (61, "root")])
def test_a_blocker_whose_lane_has_no_session_and_no_record_in_an_hour_goes_to_root_alone_as_owner_lane_dead(shell, config, minutes, to):
    shell.rows = [ONE_ROW | {"lane": "chat-move-r4"}]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 graphite FAILING_REQUIRED")]
    shell.records = [{"seq": 34766, "drive": "d1", "lane": "p8e-desktop-ship-3", "kind": "head", "to": ["root"], "refs": {"prs": [29020]}, "text": "#29020 head", "source": "post", "at": actions.stamp(shell.clock - timedelta(minutes=minutes))}]
    shell.gone = {"p8e-desktop-ship-3"}
    landing_pass(shell, config)
    [blocker] = blockers(shell)
    dead = post_body(blocker).startswith("owner lane dead: p8e-desktop-ship-3 has no live session and no record in 60 min, so this comes to root; #29020 cccc333333: ")
    assert (blocker["to"], dead) == (to, to == "root")


def test_a_dead_owner_lanes_blocker_goes_to_the_restack_lane_the_drive_names_not_root(shell, config):
    raw = json.loads(config.read_text())
    raw["landing"]["restack_lane"] = "restack-worker"
    config.write_text(json.dumps(raw))
    shell.rows = [ONE_ROW | {"lane": "chat-move-r4"}]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 graphite FAILING_REQUIRED")]
    shell.records = [{"seq": 34766, "drive": "d1", "lane": "p8e-desktop-ship-3", "kind": "head", "to": ["root"], "refs": {"prs": [29020]}, "text": "#29020 head", "source": "post", "at": actions.stamp(shell.clock - timedelta(minutes=61))}]
    shell.gone = {"p8e-desktop-ship-3"}
    landing_pass(shell, config)
    [blocker] = blockers(shell)
    assert blocker["to"] == "restack-worker"
    assert post_body(blocker).startswith("owner lane dead: p8e-desktop-ship-3 has no live session and no record in 60 min, so this comes to restack-worker; #29020 cccc333333: ")


def test_a_dead_owner_lanes_blocker_goes_to_root_when_the_restack_lane_is_dead_too(shell, config):
    raw = json.loads(config.read_text())
    raw["landing"]["restack_lane"] = "restack-worker"
    config.write_text(json.dumps(raw))
    shell.rows = [ONE_ROW | {"lane": "chat-move-r4"}]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 graphite FAILING_REQUIRED")]
    shell.records = [{"seq": 34766, "drive": "d1", "lane": "p8e-desktop-ship-3", "kind": "head", "to": ["root"], "refs": {"prs": [29020]}, "text": "#29020 head", "source": "post", "at": actions.stamp(shell.clock - timedelta(minutes=61))}]
    shell.gone = {"p8e-desktop-ship-3", "restack-worker"}
    landing_pass(shell, config)
    [blocker] = blockers(shell)
    assert blocker["to"] == "root"


CONFLICT = "GitHub reads CONFLICTING (DIRTY) against dev; run ccx vcs stack submit from the branch's worktree: cd /w && ccx vcs stack submit"


def parked(shell: FakeShell) -> list[str]:
    return [line for line in escalations(shell) if line.startswith("PARKED")]


def posted_lately(shell: FakeShell, lane: str) -> None:
    shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": lane, "kind": "note", "to": ["root"], "refs": {}, "text": "still working", "source": "post", "at": actions.stamp(shell.clock)})


@pytest.mark.parametrize("dispatch", [None, "completed"], ids=["no-dispatch", "settled-dispatch"])
def test_a_conflict_for_a_lane_with_no_live_dispatch_or_session_is_parked_once(shell, config, tmp_path, dispatch):
    if dispatch:
        shell.launch(LANE, "ctx_a", status=dispatch)
    shell.gone = {LANE}
    posted_lately(shell, LANE)
    shell.rows = [ONE_ROW]
    shell.gates["29020"] = [gate(f"#29020 BLOCKED cccc333333 {CONFLICT}")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    shell.rows = [ONE_ROW | {"head": "dddd444444", "reported_head": "dddd444444"}]
    shell.gates["29020"] = [gate(f"#29020 BLOCKED dddd444444 {CONFLICT}")]
    landing_pass(shell, config)
    assert parked(shell) == [f"PARKED parked:29020 #29020: cccc333333: {LANE} has no live dispatch or cci session, so its conflict routes to no one until the lane is back: {CONFLICT}"]
    assert blockers(shell) == [] and shell.sends() == []


def test_a_parked_conflict_routes_once_its_lane_is_back(shell, config, tmp_path):
    shell.gone = {LANE}
    posted_lately(shell, LANE)
    shell.rows = [ONE_ROW]
    shell.gates["29020"] = [gate(f"#29020 BLOCKED cccc333333 {CONFLICT}")]
    landing_pass(shell, config)
    shell.gone = set()
    landing_pass(shell, config)
    assert len(parked(shell)) == 1 and [post["text"] for post in blockers(shell)] == [f"#29020 cccc333333: {CONFLICT}"]


def test_a_landed_pr_with_an_unpruned_branch_gets_no_blocker(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.rows = [ONE_ROW]
    shell.queue["29020"] = "landed"
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 carries #29016 already squashed on dev, so the queue ejects it for conflicts; run ccx vcs stack submit")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.sends() == [] and shell.posts == [] and escalations(shell) == []


@pytest.mark.parametrize(
    "detail",
    [
        "CI running (graphite RUNNING)",
        "graphite WAITING_ON_DOWNSTACK",
        "graphite NEEDS_CODEOWNER_APPROVALS; ai-review in progress",
        "graphite REBASING; retry when restacked",
        "rules-review in progress on head",
        "not approved by poetic-svc",
    ],
)
def test_a_pending_check_or_a_missing_approval_is_never_routed(shell, config, tmp_path, detail):
    shell.launch(LANE, "ctx_a")
    shell.rows = [ONE_ROW]
    shell.gates["29020"] = [gate(f"#29020 BLOCKED cccc333333 {detail}")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.sends() == [] and shell.posts == [] and escalations(shell) == []


def test_a_conflict_beside_a_running_check_routes_once_per_head_without_the_check(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.rows = [ONE_ROW]
    running = gate("#29020 BLOCKED cccc333333 CI running (graphite RUNNING); conflicts with origin/dev in go/a.go")
    red = gate("#29020 BLOCKED cccc333333 graphite FAILING_REQUIRED; conflicts with origin/dev in go/a.go")
    shell.gates["29020"] = [running, running, gate("#29020 BLOCKED cccc333333 conflicts with origin/dev in go/a.go"), red, red]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert relayed(shell) == ["#29020 cccc333333: conflicts with origin/dev in go/a.go"]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert relayed(shell) == ["#29020 cccc333333: conflicts with origin/dev in go/a.go"]
    shell.rows = [ONE_ROW | {"head": "dddd444444", "reported_head": "dddd444444"}]
    shell.gates["29020"] = [gate("#29020 BLOCKED dddd444444 graphite FAILING_REQUIRED")]
    landing_pass(shell, config)
    assert relayed(shell) == ["#29020 cccc333333: conflicts with origin/dev in go/a.go", "#29020 dddd444444: graphite FAILING_REQUIRED"]


def test_a_stack_above_a_held_pr_gets_no_blocker_until_the_hold_lifts(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    seq = root_hold(shell, 37102, text="hold #37102 until reworked into replan B1")
    shell.rows = [
        {"pr": "37102", "lane": LANE, "branch": "r/1", "base": "dev", "head": "aaaa111111", "state": "open", "reported_head": "aaaa111111"},
        {"pr": "37103", "lane": LANE, "branch": "r/2", "base": "r/1", "head": "bbbb222222", "state": "open", "reported_head": "bbbb222222"},
        {"pr": "37156", "lane": LANE, "branch": "r/3", "base": "r/2", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
    ]
    shell.gates["37156"] = [
        gate(
            "#37102 BLOCKED aaaa111111 held; graphite FAILING_REQUIRED",
            "#37103 BLOCKED bbbb222222 conflicts with origin/dev in go/a.go",
            "#37156 BLOCKED cccc333333 CI running (graphite RUNNING); sits above held #37102",
        )
    ]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.sends() == [] and shell.posts == []
    lift(shell, seq)
    shell.gates["37156"] = [
        gate(
            "#37102 BLOCKED aaaa111111 graphite FAILING_REQUIRED",
            "#37103 BLOCKED bbbb222222 conflicts with origin/dev in go/a.go",
            "#37156 BLOCKED cccc333333 CI running (graphite RUNNING)",
        )
    ]
    landing_pass(shell, config)
    assert sorted(relayed(shell)) == [
        "#37102 aaaa111111: graphite FAILING_REQUIRED",
        "#37103 bbbb222222: conflicts with origin/dev in go/a.go",
    ]


def test_a_pr_carrying_the_hold_label_and_the_stack_above_it_get_no_blocker(shell, config, tmp_path):
    shell.rows = [
        {"pr": "39007", "lane": LANE, "branch": "j/8", "base": "dev", "head": "aaaa111111", "state": "open", "reported_head": "aaaa111111", "labels": "hold"},
        {"pr": "39050", "lane": LANE, "branch": "j/11", "base": "j/8", "head": "bbbb222222", "state": "open", "reported_head": "bbbb222222"},
    ]
    shell.gates["39050"] = [gate("#39007 BLOCKED aaaa111111 graphite FAILING_REQUIRED", "#39050 BLOCKED bbbb222222 graphite FAILING_REQUIRED")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert blockers(shell) == [] and escalations(shell) == []


@pytest.mark.parametrize(
    "statuses",
    [
        [],
        [{"context": "release-pr-check", "state": "failure"}, {"context": "buildkite/test", "state": "success"}],
        [{"context": "emergency-head", "state": "error"}, {"context": "buildkite/test", "state": "pending"}],
    ],
)
def test_a_pr_on_another_open_prs_branch_gets_no_failing_required_blocker_while_only_gates_or_its_base_are_red(shell, config, tmp_path, statuses):
    shell.combined_statuses["bbbb222222"] = statuses
    shell.rows = [
        {"pr": "39007", "lane": LANE, "branch": "j/8", "base": "dev", "head": "aaaa111111", "state": "open", "reported_head": "aaaa111111"},
        {"pr": "39050", "lane": "hj-rows-fixture", "branch": "j/11", "base": "j/8", "head": "bbbb222222", "state": "open", "reported_head": "bbbb222222", "labels": "merge"},
    ]
    shell.gates["39050"] = [gate("#39007 BLOCKED aaaa111111 graphite FAILING_REQUIRED", "#39050 BLOCKED bbbb222222 graphite FAILING_REQUIRED")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [(post["topic"], post["text"]) for post in blockers(shell)] == [("39007", "#39007 aaaa111111: graphite FAILING_REQUIRED")]


def test_a_pr_on_another_open_prs_branch_with_its_own_red_test_gets_one_blocker_per_head(shell, config, tmp_path):
    shell.combined_statuses["bbbb222222"] = [{"context": "release-pr-check", "state": "failure"}, {"context": "buildkite/test", "state": "failure"}]
    shell.rows = [
        {"pr": "39007", "lane": LANE, "branch": "j/8", "base": "dev", "head": "aaaa111111", "state": "open", "reported_head": "aaaa111111"},
        {"pr": "39050", "lane": "hj-rows-fixture", "branch": "j/11", "base": "j/8", "head": "bbbb222222", "state": "open", "reported_head": "bbbb222222"},
    ]
    shell.gates["39050"] = [gate("#39007 GREEN aaaa111111 graphite READY", "#39050 BLOCKED bbbb222222 graphite FAILING_REQUIRED")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [(post["to"], post["text"]) for post in blockers(shell)] == [("hj-rows-fixture", "#39050 bbbb222222: graphite FAILING_REQUIRED")]


def test_a_failing_required_check_posts_one_blocker_per_job_and_cause_and_a_line_per_later_head(shell, config, tmp_path):
    ssm_miss = {"context": "buildkite/sand-mac-harness-release", "state": "failure", "description": "Build #8812 failed (3 minutes)"}
    row = {"pr": "39050", "lane": "hj-rows-fixture", "branch": "j/11", "base": "dev", "state": "open"}

    def head(sha: str, *statuses: dict) -> None:
        shell.combined_statuses[sha] = list(statuses)
        shell.rows = [row | {"head": sha, "reported_head": sha}]
        shell.gates["39050"] = [gate(f"#39050 BLOCKED {sha} graphite FAILING_REQUIRED")]
        landing_pass(shell, config)
        landing_pass(shell, config)

    head("aaaa111111", ssm_miss)
    head("bbbb222222", ssm_miss | {"description": "Build #8840 failed (4 minutes)"})
    head("cccc333333", ssm_miss | {"description": "Build #8851 canceled"})
    head("dddd444444", ssm_miss | {"description": "Build #8851 canceled"}, {"context": "buildkite/test", "state": "failure", "description": "Build #8852 failed"})

    first = next(seq for seq, post in enumerate(shell.posts, 1) if post["kind"] == "blocker")
    assert [(post["kind"], post.get("re"), post["text"]) for post in shell.posts if post["kind"] in ("blocker", "note")] == [
        ("blocker", None, "#39050 aaaa111111: graphite FAILING_REQUIRED"),
        ("note", str(first), "#39050 bbbb222222: the same failure again: buildkite/sand-mac-harness-release: Build ## failed (# minutes)"),
        ("blocker", None, "#39050 cccc333333: graphite FAILING_REQUIRED"),
        ("blocker", None, "#39050 dddd444444: graphite FAILING_REQUIRED"),
    ]


def test_a_red_by_design_pr_gets_no_failing_required_blocker(shell, config, tmp_path):
    shell.combined_statuses["bbbb222222"] = [{"context": "buildkite/test", "state": "failure"}]
    shell.bodies["39050"] = "**Red by design, per owner tap b61a053.** The rows fail until the fix lands.\n\n## Context\n"
    shell.rows = [
        {"pr": "39007", "lane": LANE, "branch": "j/8", "base": "dev", "head": "aaaa111111", "state": "open", "reported_head": "aaaa111111"},
        {"pr": "39050", "lane": "hj-rows-fixture", "branch": "j/11", "base": "j/8", "head": "bbbb222222", "state": "open", "reported_head": "bbbb222222"},
    ]
    shell.gates["39050"] = [gate("#39007 GREEN aaaa111111 graphite READY", "#39050 BLOCKED bbbb222222 graphite FAILING_REQUIRED")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert blockers(shell) == []


def test_a_pr_its_lane_contracted_as_red_by_design_gets_no_failing_required_blocker(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.combined_statuses["bbbb222222"] = [{"context": "buildkite/test", "state": "failure"}]
    shell.rows = [
        {"pr": "39007", "lane": "hj-base", "branch": "j/8", "base": "dev", "head": "aaaa111111", "state": "open", "reported_head": "aaaa111111"},
        {"pr": "39050", "lane": LANE, "branch": "j/11", "base": "j/8", "head": "bbbb222222", "state": "open", "reported_head": "bbbb222222"},
    ]
    shell.records.append({"seq": 34900, "drive": "d1", "lane": LANE, "kind": "contract", "to": ["root"], "refs": {"prs": ["Forge-AI/monorepo#39050"]}, "text": "#39050 red serve rows on #39007. Failing run 78325 https://buildkite.com/forge/test/builds/78325.", "at": actions.stamp(shell.clock)})
    shell.gates["39050"] = [gate("#39007 GREEN aaaa111111 graphite READY", "#39050 BLOCKED bbbb222222 graphite FAILING_REQUIRED")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert blockers(shell) == [] and not [call for call in shell.sends() if "FAILING_REQUIRED" in call[call.index("--body") + 1]]


def test_a_held_pr_is_never_named_in_a_stack_enqueue_call_or_routed(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    root_hold(shell, 29020, text="hold #29020 owner hold until the parity waves land")
    root_hold(shell, 28349, text="hold #28349 d4 ships whole")
    shell.rows = [
        {"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "28349", "lane": "d4", "branch": "d/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
    ]
    landing_pass(shell, config)
    assert stack_enqueue_calls(shell) == []
    assert shell.sends() == [] and escalations(shell) == []


def held_on_the_bus(shell: FakeShell, tmp_path: Path) -> str:
    seq = 34766 + len(shell.records)
    shell.records.append({"seq": seq, "drive": "d1", "lane": "root", "kind": "hold", "status": "open", "to": ["root"], "refs": {"prs": [29020]}, "text": "hold #29020 until the parity waves land"})
    return f"cci hold #{seq}"


def test_a_held_pr_above_a_green_chain_is_cut_out_and_only_the_chain_below_it_is_named_with_where_its_hold_stands(shell, config, tmp_path):
    where = held_on_the_bus(shell, tmp_path)
    shell.rows = stack_rows()
    shell.gates["29016"] = [
        gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020"),
        gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", would="#28997 #29016"),
    ]
    shell.enqueue_out["29016"] = (0, "enqueue #28997 #29016: {}\n")
    grade_gated_heads_ok(shell)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29016", "--partial"]]
    assert not [call for call in stack_enqueue_calls(shell) if "29020" in call]
    [line] = [line for line in escalations(shell) if line.startswith("HELD-OUT ")]
    assert line.endswith(f"#29016: a hold covers #29020 ({where}), so #29020 stay out of the batch and the enqueue names only #28997 #29016, with `--partial`")


def test_a_hold_record_holds_the_pr_it_leads_with_and_not_the_prs_it_waits_on(shell, config, tmp_path):
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": "root", "kind": "hold", "status": "open", "to": ["root"], "refs": {"prs": [28349, 29020]}, "text": "hold #28349 until #29020 is on dev"})
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and not [line for line in escalations(shell) if line.startswith("REFUSED ")]


def test_a_hold_record_holds_only_its_leading_pr(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    root_hold(shell, 36525, 35413, text="hold #36525 lands only after the Switch batch (#35413 deletes the SandSQL sandbox exclusion) and without its infra/rows/datadog/sandsql.ts hunk")
    shell.rows = [
        {"pr": "35413", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "36525", "lane": "d4", "branch": "d/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
    ]
    shell.gates["35413"] = [gate("#35413 GREEN cccc333333 graphite READY", would="#35413")]
    shell.gates["36525"] = [gate("#36525 BLOCKED dddd444444 held")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["35413"] = (0, "enqueue #35413\n")
    landing_pass(shell, config)
    assert [call[1] for call in shell.enqueues()] == ["35413"]


def test_a_holds_file_line_holds_nothing_since_holds_are_cci_records_alone(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    raw["landing"]["holds"] = str(tmp_path / "holds.md")
    config.write_text(json.dumps(raw))
    (tmp_path / "holds.md").write_text("#29020 owner hold\n")
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1 and escalations(shell) == []


def test_mq_hold_posts_a_root_hold_record_and_mq_lift_resolves_every_open_record_holding_the_pr(shell, config, capsys):
    assert mq(shell, config, "hold", "29020", "--reason", "owner review") == 0
    [held] = shell.posts
    assert (held["lane"], held["kind"], held["pr"], held["text"]) == ("root", "hold", "Forge-AI/monorepo#29020", "hold #29020 owner review")
    first = root_hold(shell, 29020, text="hold #29020 owner review")
    second = root_hold(shell, 28349, 29020, text="hold the parity wave")
    root_hold(shell, 28400, text="hold #28400 unrelated")
    capsys.readouterr()
    assert mq(shell, config, "lift", "29020") == 0
    lifted = shell.posts[-1]
    assert (lifted["lane"], lifted["kind"], lifted["resolves"], lifted["text"]) == ("root", "lift", f"{first},{second}", "lift #29020")
    assert capsys.readouterr().out.strip().endswith("the same records held #28349, which lift too")


def test_an_open_cci_hold_holds_its_pr_and_a_closed_one_holds_nothing(shell, config, tmp_path):
    shell.records = [
        {"seq": 7, "drive": "d1", "lane": "root", "kind": "hold", "status": "open", "to": ["root"], "refs": {"prs": [29020]}, "text": "HOLD #29020, do not enqueue"},
        {"seq": 8, "drive": "d1", "lane": "root", "kind": "hold", "status": "closed", "to": ["root"], "refs": {"prs": [28349]}, "text": "HOLD #28349", "at": "2026-10-01T09:00:00Z"},
        {"seq": 9, "drive": "d1", "lane": "root", "kind": "hold", "status": "open", "to": ["root"], "refs": {"targets": ["sandsql"]}, "text": "hold sandsql"},
    ]
    shell.rows = [
        {"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "28349", "lane": "d4", "branch": "d/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
    ]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 held")]
    shell.gates["28349"] = [gate("#28349 GREEN dddd444444 graphite READY", would="#28349")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["28349"] = (0, "enqueue #28349\n")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["28349", "--partial"]]
    assert shell.sends() == []


def fork_rows() -> list[dict]:
    return [
        {"pr": "36463", "lane": "release-order", "branch": "ro/monitors", "base": "dev", "head": "aaaa111111", "state": "open", "reported_head": "aaaa111111"},
        {"pr": "36487", "lane": "release-order", "branch": "ro/verify", "base": "ro/monitors", "head": "bbbb222222", "state": "open", "reported_head": "bbbb222222"},
        {"pr": "36501", "lane": "release-order", "branch": "ro/readers", "base": "ro/monitors", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "36510", "lane": "release-order", "branch": "ro/held", "base": "ro/monitors", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
        {"pr": "36520", "lane": "release-order", "branch": "ro/red", "base": "ro/monitors", "head": "eeee555555", "state": "open", "reported_head": "eeee555555"},
        {"pr": "36600", "lane": LANE, "branch": "b/1", "base": "dev", "head": "ffff666666", "state": "open", "reported_head": "ffff666666"},
    ]


def test_each_green_chain_of_a_fork_enqueues_in_its_own_call_in_one_pass(shell, config, tmp_path):
    shell.launch("release-order", "ctx_r")
    root_hold(shell, 36510, text="hold #36510 owner hold until the monitors soak")
    shell.rows = fork_rows()
    verdicts = (
        "#36463 GREEN aaaa111111 graphite READY",
        "#36487 GREEN bbbb222222 graphite READY",
        "#36501 GREEN cccc333333 graphite READY",
        "#36510 BLOCKED dddd444444 held",
        "#36520 BLOCKED eeee555555 graphite FAILING_REQUIRED",
    )
    shell.gates["36487"] = [gate(*verdicts)]
    shell.gates["36487 36501 36520"] = [gate(*verdicts, would="#36463 #36487 #36501")]
    shell.gates["36487 36501"] = [gate(*verdicts, would="#36463 #36487 #36501")]
    shell.gates["36600"] = [gate("#36600 BLOCKED ffff666666 not approved by poetic-svc")]
    shell.combined_statuses["eeee555555"] = [{"context": "buildkite/test", "state": "failure"}]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["36487"] = (0, "enqueue #36463 #36487: {}\n")
    shell.enqueue_out["36501"] = (0, "enqueue #36501: {}\n")
    landing_pass(shell, config)
    landing_pass(shell, config)
    checks = [call for call in shell.calls if Path(call[0]).name == "stack-enqueue" and "--check" in call]
    assert {tuple(itertools.takewhile(str.isdigit, call[1:])) for call in checks} == {("36487",), ("36487", "36501", "36520"), ("36487", "36501"), ("36600",)}
    assert [call[1:] for call in shell.enqueues()] == [["36487", "--partial"], ["36501", "--partial"]]
    assert not [line for line in escalations(shell) if line.startswith("FORK-DEFERRED ")]
    blockers = [call[call.index("--body") + 1] for call in shell.sends()]
    assert [body for body in blockers if "#36520" in body] and not [body for body in blockers if "#36510" in body]


def test_a_green_pr_no_drive_lane_opened_riding_a_fork_escalates_as_foreign(shell, config, tmp_path):
    shell.rows = fork_rows()[:3]
    shell.gates["36487"] = [
        gate(
            "#36463 GREEN aaaa111111 graphite READY",
            "#36487 GREEN bbbb222222 graphite READY",
            "#36501 BLOCKED cccc333333 rides the batch through #36463 but is neither named nor below #36487; name it too or pass --partial to leave it out",
            "#36530 BLOCKED 9999aaaaaa rides the batch through #36463 but is neither named nor below #36487; name it too or pass --partial to leave it out",
        )
    ]
    shell.gates["36487 36501"] = [
        gate(
            "#36463 GREEN aaaa111111 graphite READY",
            "#36487 GREEN bbbb222222 graphite READY",
            "#36501 GREEN cccc333333 graphite READY",
            "#36530 BLOCKED 9999aaaaaa rides the batch through #36463 but is neither named nor below #36487 #36501; name it too or pass --partial to leave it out",
        )
    ]
    landing_pass(shell, config)
    landing_pass(shell, config)
    lines = [line for line in said(shell) if line.startswith("FOREIGN")]
    assert len(lines) == 1 and "#36530 would ride the batch" in lines[0]
    assert shell.enqueues() == []


def test_a_whole_policy_gates_each_tip_of_a_fork_alone(shell, config, tmp_path):
    shell.rows = fork_rows()[:3]
    assert cli(shell, config, "policy", "--key", "L300", "--landing", "whole", "--revision", "f9e8d7c6b5", "--supersedes", "a1b2c3d4e5", "--source", "owner ruling") == 0
    shell.gates["36487"] = [gate("#36463 GREEN aaaa111111 graphite READY", "#36487 GREEN bbbb222222 graphite READY", "#36501 BLOCKED cccc333333 is stacked beside the top")]
    shell.gates["36501"] = [gate("#36463 GREEN aaaa111111 graphite READY", "#36501 GREEN cccc333333 graphite READY", "#36487 BLOCKED bbbb222222 is stacked beside the top")]
    landing_pass(shell, config)
    checks = [call for call in shell.calls if Path(call[0]).name == "stack-enqueue" and "--check" in call]
    assert checks and all(len(list(itertools.takewhile(str.isdigit, call[1:]))) == 1 and "--whole" in call for call in checks)


def test_tips_whose_ledger_bases_share_a_root_share_a_check_only_when_stack_enqueue_counts_them_one_stack(shell, config, tmp_path):
    shell.rows = [
        {"pr": "37145", "lane": "recon", "branch": "recon/compat", "base": "dev", "head": "aaaa111111", "state": "open", "reported_head": "aaaa111111"},
        {"pr": "37153", "lane": "recon", "branch": "recon/size", "base": "recon/compat", "head": "bbbb222222", "state": "open", "reported_head": "bbbb222222"},
        {"pr": "37947", "lane": "track1", "branch": "t1/identity", "base": "recon/compat", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "37990", "lane": "track1", "branch": "t1/ltx", "base": "t1/identity", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
    ]
    shell.gates["37153"] = [gate("#37145 GREEN aaaa111111 graphite READY", "#37153 GREEN bbbb222222 graphite READY", would="#37145 #37153")]
    shell.gates["37990"] = [
        gate(
            "#37947 BLOCKED cccc333333 base graphite-base/37947 while its parent #37145 is still open; restack with ccx vcs stack submit",
            "#37990 WAITING dddd444444 graphite READY; sits above BLOCKED #37947",
        )
    ]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["37153"] = (0, "enqueue #37145 #37153\n")
    landing_pass(shell, config)
    checks = [tuple(itertools.takewhile(str.isdigit, call[1:])) for call in shell.calls if Path(call[0]).name == "stack-enqueue" and "--check" in call]
    assert checks and set(checks) == {("37153",), ("37990",)}
    assert [call[1] for call in shell.enqueues()] == ["37153"]
    assert not [line for line in escalations(shell) if "GATE-FAILED" in line]


def test_fork_tips_a_joined_check_refuses_without_a_verdict_gate_alone_on_the_same_pass(shell, config, tmp_path):
    shell.rows = fork_rows()[:3]
    rider = "BLOCKED 9999aaaaaa rides the batch through #36463 but is neither named nor below #{}; name it too or pass --partial to leave it out"
    shell.gates["36487"] = [gate("#36463 GREEN aaaa111111 graphite READY", "#36487 GREEN bbbb222222 graphite READY", "#36501 GREEN cccc333333 graphite READY", "#36530 " + rider.format(36487))]
    shell.gates["36487 36501"] = [(1, "enqueued nothing: #36501 is not in #36487's stack\n", "")]
    shell.gates["36501"] = [gate("#36463 GREEN aaaa111111 graphite READY", "#36501 GREEN cccc333333 graphite READY", "#36531 " + rider.format(36501))]
    landing_pass(shell, config)
    checks = [tuple(itertools.takewhile(str.isdigit, call[1:])) for call in shell.calls if Path(call[0]).name == "stack-enqueue" and "--check" in call]
    assert checks == [("36487",), ("36487", "36501"), ("36501",)]
    lines = escalations(shell)
    assert not [line for line in lines if "GATE-FAILED" in line]
    assert sorted(line.split(" would ride")[0][-6:] for line in said(shell) if line.startswith("FOREIGN")) == ["#36530", "#36531"]


def test_a_fork_its_last_check_named_one_stack_is_gated_by_one_joined_check(shell, config, tmp_path):
    shell.rows = fork_rows()[:3]
    verdicts = ("#36463 GREEN aaaa111111 graphite READY", "#36487 GREEN bbbb222222 graphite READY", "#36501 BLOCKED cccc333333 ai-review pending")
    shell.gates["36487"] = [gate(*verdicts)]
    shell.gates["36487 36501"] = [gate(*verdicts)]
    landing = landing_of(shell, config, tmp_path)
    landing.run()
    shell.calls.clear()
    landing.run()
    checks = [tuple(itertools.takewhile(str.isdigit, call[1:])) for call in shell.calls if Path(call[0]).name == "stack-enqueue" and "--check" in call]
    assert checks == [("36487", "36501")]


def test_a_fork_that_split_since_the_last_pass_falls_back_to_solo_checks(shell, config, tmp_path):
    shell.rows = fork_rows()[:3]
    rider = "BLOCKED 9999aaaaaa rides the batch through #36463 but is neither named nor below #{}; name it too or pass --partial to leave it out"
    both = gate("#36463 GREEN aaaa111111 graphite READY", "#36487 GREEN bbbb222222 graphite READY", "#36501 BLOCKED cccc333333 ai-review pending")
    shell.gates["36487"] = [both]
    shell.gates["36487 36501"] = [both]
    landing = landing_of(shell, config, tmp_path)
    landing.run()
    shell.calls.clear()
    shell.gates["36487 36501"] = [(1, "enqueued nothing: #36501 is not in #36487's stack\n", "")]
    shell.gates["36487"] = [gate("#36463 GREEN aaaa111111 graphite READY", "#36487 BLOCKED bbbb222222 ai-review pending", "#36530 " + rider.format(36487))]
    shell.gates["36501"] = [gate("#36463 GREEN aaaa111111 graphite READY", "#36501 BLOCKED cccc333333 ai-review pending")]
    landing.run()
    checks = [tuple(itertools.takewhile(str.isdigit, call[1:])) for call in shell.calls if Path(call[0]).name == "stack-enqueue" and "--check" in call]
    assert checks == [("36487", "36501"), ("36487",), ("36501",)]
    assert not [line for line in escalations(shell) if "GATE-FAILED" in line]


def test_a_tip_checked_alone_after_a_refused_join_escalates_when_it_has_no_verdict_either(shell, config, tmp_path):
    shell.rows = fork_rows()[:3]
    shell.gates["36487"] = [gate("#36463 GREEN aaaa111111 graphite READY", "#36487 GREEN bbbb222222 graphite READY", "#36501 GREEN cccc333333 graphite READY")]
    shell.gates["36487 36501"] = [(1, "enqueued nothing: #36501 is not in #36487's stack\n", "")]
    shell.gates["36501"] = [(5, "", "stack-enqueue failed: <urlopen error timed out>\n")]
    landing_pass(shell, config)
    lines = [line for line in escalations(shell) if "GATE-FAILED" in line]
    assert len(lines) == 1 and "#36501" in lines[0] and "#36487" not in lines[0]


def test_a_check_that_exits_with_no_verdict_escalates_once_instead_of_passing_silently(shell, config, tmp_path):
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333", "ours": True}]
    shell.gates["29020"] = [(2, "", "usage: stack-enqueue PR [PR ...]\nstack-enqueue: error: unrecognized arguments: 28349\n")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    lines = escalations(shell)
    assert len(lines) == 1 and "GATE-FAILED" in lines[0] and "#29020" in lines[0] and "unrecognized arguments: 28349" in lines[0]
    assert shell.enqueues() == []


def test_a_check_with_no_verdict_on_a_pr_github_reads_merged_escalates_nothing(shell, config, tmp_path):
    """Root #200922: #44828 merged directly, its branch went, and the gate's fetch of that branch read as a GATE-FAILED defect."""
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333", "ours": True}]
    shell.gates["29020"] = [(5, "", "stack-enqueue failed: git fetch failed with exit 128: fatal: couldn't find remote ref refs/heads/a/3\n")]
    shell.pr_states["29020"] = "MERGED"
    landing_pass(shell, config)
    assert not [line for line in escalations(shell) if "GATE-FAILED" in line]


HANDSHAKE = "stack-enqueue failed: _ssl.c:1064: The handshake operation timed out\n"


def single_row() -> list[dict]:
    return [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333", "ours": True}]


def checks(shell: FakeShell) -> list[list[str]]:
    return [call for call in shell.calls if Path(call[0]).name == "stack-enqueue" and "--check" in call]


def test_a_check_that_hits_a_transient_network_failure_is_retried_and_reads_the_verdict(shell, config, tmp_path):
    shell.rows = single_row()
    shell.gates["29020"] = [(5, HANDSHAKE, ""), gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    started = shell.clock
    landing_pass(shell, config)
    assert escalations(shell) == []
    assert len(checks(shell)) >= 2 and len(shell.enqueues()) == 1
    assert shell.clock - started >= timedelta(seconds=runner_module.merge_queue.TRANSIENT_PAUSE_SECONDS)


def test_a_check_killed_at_its_sixty_second_cap_logs_nothing_and_the_next_pass_enqueues_on_its_verdict(shell, config, tmp_path):
    assert runner_module.GATE_CHECK_SECONDS == 60
    shell.rows = single_row()
    killed = f"stack-enqueue 29020 --check timed out after {runner_module.GATE_CHECK_SECONDS}s and was killed"
    shell.gates["29020"] = [(runner_module.TIMED_OUT, "", killed), gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    landing_pass(shell, config)
    assert escalations(shell) == [] and shell.enqueues() == []
    landing_pass(shell, config)
    assert escalations(shell) == [] and len(shell.enqueues()) == 1


def test_a_check_that_keeps_failing_transiently_escalates_once_after_three_attempts(shell, config, tmp_path):
    shell.rows = single_row()
    shell.gates["29020"] = [(5, HANDSHAKE, "")]
    landing_pass(shell, config)
    assert len(checks(shell)) == runner_module.merge_queue.TRANSIENT_ATTEMPTS
    landing_pass(shell, config)
    lines = escalations(shell)
    assert len(lines) == 1 and "GATE-FAILED" in lines[0] and "#29020" in lines[0] and "handshake operation timed out" in lines[0]


@pytest.mark.parametrize("failure", [(1, "", "stack-enqueue: error: could not resolve the head ref of #29020: the branch a/3 is gone\n"), (5, HANDSHAKE, "")], ids=["queue-deleted-branch", "network-timeout"])
def test_a_check_with_no_verdict_on_a_tip_that_landed_since_the_pass_read_the_ledger_escalates_nothing(shell, config, tmp_path, failure):
    """#98460: the queue landed #40945 and deleted its branch, and the next check on it posted GATE-FAILED."""
    shell.rows = single_row()
    shell.gates["29020"] = [failure]
    real = shell.run

    def run(argv, stdin=None, env=None):
        done = real(argv, stdin, env)
        if len(argv) > 4 and Path(argv[1]).name == "ledger.py" and argv[4] == "landed":
            assert "--pr=29020" in argv
            shell.rows[0] |= {"state": "landed", "landed_sha": "f00d000003"}
        return done

    shell.run = run
    landing_pass(shell, config)
    assert escalations(shell) == []


@pytest.mark.parametrize("failure", [(2, "", "usage: stack-enqueue PR [PR ...]\n"), (5, "stack-enqueue failed: Graphite pull-request-info failed: status error, message nope\n", "")])
def test_a_check_failing_for_any_other_reason_is_never_retried(shell, config, tmp_path, failure):
    shell.rows = single_row()
    shell.gates["29020"] = [failure]
    landing_pass(shell, config)
    assert len(checks(shell)) == 1
    assert len(escalations(shell)) == 1


@pytest.mark.parametrize(
    "error",
    [
        "fatal: unable to access 'https://github.com/Forge-AI/monorepo.git/': Recv failure: Operation timed out\n",
        "fatal: unable to access 'https://github.com/Forge-AI/monorepo.git/': Failed to connect to github.com port 443 after 75006 ms: Couldn't connect to server\n",
        "fatal: unable to access 'https://github.com/Forge-AI/monorepo.git/': Could not resolve host: github.com\n",
        "fatal: unable to access 'https://github.com/Forge-AI/monorepo.git/': Recv failure: Connection was aborted\n",
    ],
)
def test_a_git_network_failure_reads_as_transient(error):
    assert runner_module.merge_queue.TRANSIENT_FAILURE.search(error)


def test_a_git_failure_that_is_not_the_network_reads_as_permanent():
    assert not runner_module.merge_queue.TRANSIENT_FAILURE.search("fatal: couldn't find remote ref refs/heads/a/3\n")


def test_an_enqueue_that_hits_a_transient_network_failure_is_retried(shell, config, tmp_path):
    shell.rows = single_row()
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    real, attempts = shell.stack_enqueue, []

    def flaky(argv):
        if "--check" in argv or "--status" in argv:
            return real(argv)
        attempts.append(argv)
        return runner_module.Done(5, "stack-enqueue failed: HTTP Error 502: Bad Gateway\n", "") if len(attempts) == 1 else real(argv)

    shell.stack_enqueue = flaky
    landing_pass(shell, config)
    assert len(attempts) == 2
    assert escalations(shell) == []


def test_a_gate_pass_never_runs_more_checks_at_once_than_gate_workers(shell, config, tmp_path):
    prs = [str(29100 + n) for n in range(runner_module.GATE_WORKERS * 3)]
    shell.rows = [{"pr": pr, "lane": LANE, "branch": f"g/{pr}", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"} for pr in prs]
    shell.gates = {pr: [gate(f"#{pr} GREEN cccc333333 graphite READY")] for pr in prs}
    check, lock, running, peak = shell.stack_enqueue, threading.Lock(), [0], [0]

    def overlapping(argv):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.02)
        with lock:
            running[0] -= 1
        return check(argv)

    shell.stack_enqueue = overlapping
    landing_pass(shell, config)
    checked = {call[1] for call in shell.calls if Path(call[0]).name == "stack-enqueue" and "--check" in call}
    assert checked == set(prs)
    assert peak[0] <= runner_module.GATE_WORKERS


def test_an_accepted_tree_enqueues_while_a_later_tree_is_still_being_checked(shell, config, tmp_path):
    shell.rows = [
        {"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "29100", "lane": LANE, "branch": "b/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
    ]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    shell.gates["29100"] = [gate("#29100 BLOCKED dddd444444 CI running (graphite RUNNING)")]
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    grade(shell, "29020", "ok F307 at cccc333.")
    check, queued_first = shell.stack_enqueue, []

    def slow(argv):
        if argv[0] == "29100" and "--check" in argv:
            deadline = time.monotonic() + 5
            while not shell.enqueues() and time.monotonic() < deadline:
                time.sleep(0.01)
            queued_first.append(bool(shell.enqueues()))
        return check(argv)

    shell.stack_enqueue = slow
    landing_pass(shell, config)
    assert queued_first and all(queued_first)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


def stack_enqueue_calls(shell: FakeShell) -> list[list[str]]:
    return [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]


def root_hold(shell: FakeShell, *prs: int, text: str = "") -> int:
    seq = 34766 + len(shell.records)
    shell.records.append({"seq": seq, "drive": "d1", "lane": "root", "kind": "hold", "status": "open", "to": ["root"], "refs": {"prs": list(prs)}, "text": text or f"hold #{prs[0]}", "at": actions.stamp(shell.clock)})
    return seq


def lift(shell: FakeShell, seq: int) -> None:
    next(record for record in shell.records if record["seq"] == seq)["status"] = "closed"


def hold_record(shell: FakeShell, seq: int, pr: int, status: str, at: str) -> None:
    if not any(record["seq"] == seq for record in shell.records):
        shell.records.append({"seq": seq, "drive": "d1", "lane": "root", "kind": "hold", "status": status, "to": ["root"], "refs": {"prs": [pr]}, "text": f"HOLD #{pr}", "at": at})


def ready_record(shell: FakeShell, seq: int, pr: int, at: str) -> None:
    shell.records.append({"seq": seq, "drive": "d1", "lane": LANE, "kind": "ready", "status": "open", "to": ["root"], "refs": {"prs": [pr]}, "text": f"READY #{pr}", "at": at})


def after_call(shell: FakeShell, when, change) -> None:
    inner = shell.stack_enqueue

    def call(argv: list[str]) -> runner_module.Done:
        done = inner(argv)
        if when(argv):
            change()
        return done

    shell.stack_enqueue = call


def is_check(argv: list[str]) -> bool:
    return "--check" in argv


def is_enqueue(argv: list[str]) -> bool:
    return not {"--check", "--status", "--dequeue"} & set(argv)


@pytest.mark.parametrize(
    ("source", "named"),
    [("label", "the `hold` label on GitHub"), ("record", "open cci hold #41")],
)
def test_a_hold_that_appears_after_the_gate_refuses_the_enqueue_with_one_line_naming_it(shell, config, tmp_path, source, named):
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    appears = {
        "label": lambda: shell.pr_labels.update({"29020": ["merge", "hold"]}),
        "record": lambda: hold_record(shell, 41, 29020, "open", "2026-10-01T17:59:00Z"),
    }[source]
    after_call(shell, is_check, appears)
    landing_pass(shell, config)
    [line] = escalations(shell)
    assert line.startswith("REFUSED refused:") and f"#29020 is held by {named}" in line
    [action] = enqueues_of(tmp_path).values()
    assert (action.status, action.response["outcome"]) == ("completed", "refused")
    assert shell.enqueues() == []


def test_an_open_incident_naming_the_pr_refuses_its_enqueue_naming_the_incident(shell, config, tmp_path):
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    incident = {"seq": 42, "drive": "d1", "lane": "incident-api-1", "kind": "incident", "status": "open", "to": ["root"], "refs": {}, "text": "INCIDENT api 5xx; the fix is #29020", "source": "post", "at": "2026-10-01T17:59:00Z"}
    after_call(shell, is_check, lambda: incident in shell.records or {**incident, "status": "closed"} in shell.records or shell.records.append(incident))
    landing_pass(shell, config)
    refused = [line for line in escalations(shell) if line.startswith("REFUSED refused:")]
    assert len(refused) == 1 and "#29020 is held by open INCIDENT #42, whose lane squash-merges it directly" in refused[0]
    assert shell.enqueues() == []
    shell.records[shell.records.index(incident)] = {**incident, "status": "closed"}
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1


def test_a_label_read_github_refuses_holds_the_enqueue(shell, config, tmp_path):
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    shell.pr_label_error = "HTTP 502"
    landing_pass(shell, config)
    [line] = escalations(shell)
    assert "#29020 is held by GitHub refused the label read: HTTP 502" in line and shell.enqueues() == []
    shell.pr_label_error = ""
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1


def test_a_ready_record_older_than_a_later_hold_never_enqueues_until_a_newer_ready(shell, config, tmp_path):
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    ready_record(shell, 10, 29020, "2026-10-01T16:00:00Z")
    hold_record(shell, 11, 29020, "closed", "2026-10-01T17:00:00Z")
    landing_pass(shell, config)
    [line] = escalations(shell)
    assert "cci ready #10 is older than cci hold #11" in line and shell.enqueues() == []
    landing_pass(shell, config)
    assert shell.enqueues() == [] and len(escalations(shell)) == 1
    ready_record(shell, 12, 29020, "2026-10-01T17:30:00Z")
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1 and len(escalations(shell)) == 1


def test_a_lifted_hold_posted_after_the_enqueue_was_accepted_still_refuses_it(shell, config, tmp_path):
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    after_call(shell, is_check, lambda: hold_record(shell, 20, 29020, "closed", "2026-10-01T18:00:05Z"))
    landing_pass(shell, config)
    [line] = escalations(shell)
    assert "cci hold #20 was posted after this enqueue was accepted" in line and shell.enqueues() == []


def test_a_lifted_hold_older_than_the_enqueue_and_any_ready_lets_it_through(shell, config, tmp_path):
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    hold_record(shell, 20, 29020, "closed", "2026-10-01T17:00:00Z")
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1 and escalations(shell) == []


def stack_of_three(shell: FakeShell) -> None:
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #28997 #29016 #29020: {}\n")


def dequeues(shell: FakeShell) -> list[list[str]]:
    return [call[1:] for call in stack_enqueue_calls(shell) if "--dequeue" in call]


def test_a_hold_that_appears_while_stack_enqueue_runs_takes_the_held_pr_and_those_above_it_back_out_top_first(shell, config, tmp_path):
    stack_of_three(shell)
    after_call(shell, is_enqueue, lambda: hold_record(shell, 41, 29016, "open", "2026-10-01T17:59:59Z"))
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1 and dequeues(shell) == [["29020", "--dequeue"], ["29016", "--dequeue"]]
    [action] = enqueues_of(tmp_path).values()
    assert (action.status, action.response["outcome"]) == ("completed", "dequeued")
    [line] = escalations(shell)
    assert line.startswith("DEQUEUED ") and "#29016 is held by open cci hold #41" in line
    shell.records[-1]["status"] = "closed"
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 2 and len(dequeues(shell)) == 2
    assert sorted(action.response["outcome"] for action in enqueues_of(tmp_path).values()) == ["dequeued", "enqueued"]


def test_a_dequeue_the_queue_refuses_is_a_defect_and_the_prefix_is_not_enqueued_again(shell, config, tmp_path):
    stack_of_three(shell)
    shell.dequeue_code = 4
    after_call(shell, is_enqueue, lambda: hold_record(shell, 41, 29020, "open", "2026-10-01T17:59:59Z"))
    landing_pass(shell, config)
    [action] = enqueues_of(tmp_path).values()
    assert action.response["outcome"] == "held-queued"
    [line] = escalations(shell)
    assert line.startswith("DEQUEUE-FAILED ") and "#29020: queue still holds the PR" in line
    shell.records[-1]["status"] = "closed"
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1


def test_a_stopped_pass_still_settles_a_lost_enqueue_from_graphite_status_and_makes_no_other_stack_enqueue_call(shell, config, tmp_path):
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", would="#28997")]
    grade_gated_heads_ok(shell)
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    runner_module.seed_policy(runner)
    landing = runner_module.Landing(runner, runner.config.landing)
    landing.accept("29020", ["28997"], {"28997": {"sha": "aaaa111111"}}, {"28997": {"ours": True}}, [])
    key = next(iter(store.load("desk-landing").actions.keys() - {"policy:a1b2c3d4e5"}))
    with store.owned("desk-landing", 0) as live:
        live.start(key, shell.now())
    shell.sleep(16 * 60)
    shell.statuses = "#28997 QUEUED_TO_MERGE aaaa111111 open review APPROVED on dev\n"
    raw = json.loads(config.read_text())
    raw["landing"]["checks_wait_for_go"] = 34700
    config.write_text(json.dumps(raw))
    landing_pass(shell, config)
    action = store.load("desk-landing").actions[key]
    assert (action.status, action.response["outcome"]) == ("completed", "enqueued")
    assert [call[-1] for call in stack_enqueue_calls(shell)] == ["--status"]
    assert [line.split(" ", 1)[0] for line in escalations(shell)] == ["GATE-PAUSED"]


def lone_green_pr(shell: FakeShell) -> None:
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")


def blockers(shell: FakeShell) -> list[dict]:
    return [post for post in shell.posts if post["kind"] == "blocker"]


def test_a_check_that_finds_mergeability_uncomputed_asks_github_over_rest_and_runs_again_in_the_same_pass(shell, config, tmp_path):
    """Owner 6:09 PM Oct 29 on #45291's 5:52-5:57 refusal: "thats false it never takes that long so something is wrong with your tooling, fix it". GitHub computes mergeability only once something reads the pull over REST (root #192866)."""
    lone_green_pr(shell)
    shell.gates["29020"] = [(1, gate("#29020 BLOCKED cccc333333 GitHub still computing mergeability; rerun on the next pass"), ""), (1, gate("#29020 BLOCKED cccc333333 GitHub still computing mergeability; rerun on the next pass"), ""), gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    grade(shell, "29020", "ok F307 at cccc333.")
    started = shell.clock
    landing_pass(shell, config)
    assert shell.mergeable_reads == ["29020"]
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]
    assert blockers(shell) == [] and shell.clock - started >= timedelta(seconds=20)


def test_a_head_the_fidelity_lane_graded_ok_is_enqueued(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307,F321 at cccc333. The restack onto dev has a range-diff of '='.")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]
    assert blockers(shell) == [] and escalations(shell) == []


def test_the_enqueue_names_the_prefix_top_with_no_head_pin_or_hold(shell, config, tmp_path):
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #28997 #29016 #29020: {}\n")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]
    assert not [call for call in stack_enqueue_calls(shell) if "--head" in call or "--hold" in call]


def test_a_fidelity_ok_posted_after_the_pass_scanned_reviews_counts_before_the_pass_refuses(shell, config, tmp_path):
    """Oct 29 #190787: fidelity-pipeline's ok on #44900 landed at 4:55 while the 4:54 pass ran, and the pass refused the head at 4:56 for having none."""
    lone_green_pr(shell)
    finder_verdict(shell, "29020", "ok cccc333 no findings")
    scan = shell.cci_tail
    late = {"seq": 99999, "drive": "d1", "lane": "fidelity-desk", "kind": "review", "to": ["root"], "refs": {"prs": ["Forge-AI/monorepo#29020"]}, "text": "fidelity: ok cccc333333 carried, patch-identical."}

    def ok_lands_after_the_scan(argv: list[str]) -> runner_module.Done:
        done = scan(argv)
        if "--kind=review" in argv and "--kind=head" in argv and late not in shell.records:
            shell.records.append(late)
        return done

    shell.cci_tail = ok_lands_after_the_scan
    landing_pass(shell, config)
    assert blockers(shell) == []
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


def test_a_failed_cci_read_stops_every_refusal_until_a_pass_reads_cleanly(shell, config, tmp_path):
    """Oct 29 4:43 PM: while cci reads timed out, stack workers kept refusing heads on what the failed reads left out."""
    landing = landing_of(shell, config, tmp_path)
    shell.tail_error = "cci: store locked"
    with pytest.raises(runner_module.CciTailFailed):
        landing.runner.cci_read(["cci", "tail", "--drive", "d1", "--since", "0", "--json"])
    shell.tail_error = ""
    with pytest.raises(runner_module.CciTailFailed):
        landing.post_blocker("blocker:29020:cccc333333", LANE, "#29020 cccc333333: no review", "29020")
    assert blockers(shell) == []


def test_a_head_graded_missing_is_blocked_with_the_grade(shell, config, tmp_path):
    lone_green_pr(shell)
    seq = grade(shell, "29020", "missing F307 at cccc333 (View fix). It carries no test.")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == [] and enqueues_of(tmp_path) == {}
    [blocker] = blockers(shell)
    assert blocker["to"] == f"{LANE},fidelity-desk" and blocker["topic"] == "29020"
    assert blocker["text"] == f"#29020 cccc333333: nothing in #29020 was enqueued; fidelity-desk graded this head on cci #{seq}: fidelity: missing F307 at cccc333 (View fix). It carries no test."


@pytest.mark.parametrize("grounds", ["owner-words. root #131110 keeps this missing", "design/s9 the plan's design row", "#131668 root's tap-carry rule"])
def test_a_missing_that_names_no_plan_line_or_owner_row_goes_to_the_owning_lane_and_the_graders_not_root(shell, config, tmp_path, grounds):
    """Root #198356: the lane that owns the PR owes the answer, and root hears only when no live lane owns it."""
    lone_green_pr(shell)
    seq = grade(shell, "29020", f"missing cccc333 {grounds}")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    [blocker] = blockers(shell)
    assert blocker["to"] == f"{LANE},fidelity-desk"
    key = grounds.split()[0].rstrip(".")
    assert blocker["text"].endswith(f"cci #{seq}: fidelity: missing cccc333 {grounds} | it rests on `{key}`, no plan line and no owner row: the lane answers with a fix or a refutation, the grader re-posts it from `rulings.py grade`, or root puts the gap on the owner's board")


def test_an_ok_at_an_older_head_only_is_blocked(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at bbbb222.")
    grade(shell, "29020", "ok, same rows as head bbbb222. This head has an identical diff (a restack).")
    grade(shell, "29020", "ok, unchanged: measurement only.")
    grade(shell, "28349", "ok F307 at cccc333.")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == []
    [blocker] = blockers(shell)
    assert blocker["text"] == "#29020 cccc333333: nothing in #29020 was enqueued; no review from fidelity-desk starting `fidelity: ok` names this head"


def test_a_missing_after_an_ok_at_the_same_head_blocks_until_a_newer_ok(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    seq = grade(shell, "29020", "missing F335 at cccc333: the sealed read is not folded in.")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    [blocker] = blockers(shell)
    assert f"cci #{seq}: fidelity: missing F335" in blocker["text"]
    grade(shell, "29020", "ok F307,F335 at cccc333333.")
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1 and len(blockers(shell)) == 1


@pytest.mark.parametrize(("lane", "lands"), [("root", True), ("p8a-step7", False)])
def test_a_missing_a_root_record_resolves_is_accepted_and_the_head_enqueues(shell, config, tmp_path, lane, lands):
    lone_green_pr(shell)
    finder_verdict(shell, "29020", "ok cccc333 no findings")
    seq = grade(shell, "29020", "missing cccc333 plan names no enum `UNLABELLED`")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    cci_record(shell, lane=lane, kind="decide", resolves=[seq], refs={"prs": [29020]}, at=actions.stamp(shell.now()), text="Accepted at cccc333: a review-tool label, no product path")
    landing_pass(shell, config)
    assert len(shell.enqueues()) == int(lands)


def test_an_ungraded_pr_cuts_the_prefix_and_the_graded_pr_below_it_enqueues_alone(shell, config, tmp_path):
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 BLOCKED cccc333333 ai-review pending", would="#28997 #29016")]
    shell.gates["28997"] = [gate("#28997 GREEN aaaa111111 graphite READY", would="#28997")]
    shell.enqueue_out["28997"] = (0, "enqueue #28997: {}\n")
    grade(shell, "28997", "ok F307 at aaaa111.")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["28997", "--partial"]]
    [blocker] = [post for post in blockers(shell) if post["topic"] == "29016"]
    assert blocker["to"] == "artifact-retry,fidelity-desk" and not [post for post in blockers(shell) if post["topic"] == "28997"]
    assert blocker["text"].startswith("#29016 bbbb222222: the prefix is cut at #29016, and only #28997 may enqueue, without #29016; no review from fidelity-desk")


def four_pr_stack(shell: FakeShell) -> None:
    heads = {"42590": "aaaa111111", "42615": "bbbb222222", "42634": "dddd444444", "42620": "eeee555555"}
    shell.rows = [
        {"pr": pr, "lane": LANE, "branch": f"s/{index}", "base": f"s/{index - 1}" if index else "dev", "head": head, "state": "open", "reported_head": head}
        for index, (pr, head) in enumerate(heads.items())
    ]
    shell.gates["42620"] = [gate(*(f"#{pr} GREEN {head} graphite READY" for pr, head in heads.items()), would="#42590 #42615 #42634 #42620")]
    shell.gates["42615"] = [gate("#42590 GREEN aaaa111111 graphite READY", "#42615 GREEN bbbb222222 graphite READY", would="#42590 #42615")]
    shell.enqueue_out["42615"] = (0, "enqueue #42590 #42615: {}\n")


def test_a_missing_grade_on_the_third_pr_of_four_enqueues_the_bottom_two_and_names_the_cut(shell, config, tmp_path):
    four_pr_stack(shell)
    grade(shell, "42590", "ok F307 at aaaa111.")
    grade(shell, "42615", "ok F307 at bbbb222.")
    seq = grade(shell, "42634", "missing F335 at dddd444: the sealed read is not folded in.")
    grade(shell, "42620", "ok F307 at eeee555.")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["42615", "--partial"]]
    assert list(enqueues_of(tmp_path)) == ["enqueue:42590@aaaa111111,42615@bbbb222222#1"]
    assert all("--partial" in call for call in checks(shell) if call[1] == "42615")
    [blocker] = blockers(shell)
    assert blocker["topic"] == "42634" and blocker["to"] == f"{LANE},fidelity-desk"
    assert blocker["text"] == f"#42634 dddd444444: the prefix is cut at #42634, and only #42590 #42615 may enqueue, without #42634 #42620; fidelity-desk graded this head on cci #{seq}: fidelity: missing F335 at dddd444: the sealed read is not folded in."


def test_the_fidelity_lane_is_the_one_runner_json_names(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    raw["landing"]["fidelity_lane"] = "parity-desk"
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    assert shell.enqueues() == [] and blockers(shell)[0]["to"] == f"{LANE},parity-desk"
    grade(shell, "29020", "ok F307 at cccc333.", lane="parity-desk")
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1


def test_the_landing_desk_refuses_to_start_without_a_fidelity_lane(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    del raw["landing"]["fidelity_lane"]
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)
    assert cli(shell, config, "run", "--desk", "landing", "--once") == 3
    assert shell.enqueues() == []
    [line] = escalations(shell)
    assert line.startswith("FIDELITY-LANE-UNSET ") and "names no landing.fidelity_lane" in line


HIGH_RISK_PATHS = ["*migrations/*", "*.sql", "api/src/db/*", "infra/rows/*"]


def merge_mode(config: Path, mode: str) -> None:
    raw = json.loads(config.read_text())
    raw["landing"]["merge_mode"] = mode
    if mode == "high-risk":
        raw["landing"]["high_risk_paths"] = HIGH_RISK_PATHS
    config.write_text(json.dumps(raw))


def merge_go(shell: FakeShell, pr: str, text: str, lane: str = "root") -> None:
    shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": lane, "kind": "go", "to": ["desk-runner"], "refs": {"prs": [int(pr)]}, "text": text, "at": actions.stamp(shell.clock)})


@pytest.mark.parametrize("identical", [True, False], ids=["patch-identical", "changed"])
def test_a_go_at_an_older_head_carries_to_a_fidelity_ok_head_with_the_same_own_patch(shell, config, tmp_path, identical):
    merge_mode(config, "every")
    lone_green_pr(shell)
    merge_go(shell, "29020", "go port #29020 at aaaa111")
    grade(shell, "29020", "ok cccc333 pure port, no interaction; restacked onto dev")
    shell.files["29020"] = ["x.go"]
    shell.diffs["aaaa111"] = "diff --git a/x.go b/x.go\n+port\n"
    shell.diffs["cccc333333"] = shell.diffs["aaaa111"] if identical else "diff --git a/x.go b/x.go\n+changed\n"
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == ([["29020", "--partial"]] if identical else [])


def test_a_green_head_gets_its_merge_card_before_its_fidelity_grade_and_enqueues_only_once_graded(shell, config, tmp_path):
    merge_mode(config, "every")
    lone_green_pr(shell)
    landing_pass(shell, config)
    assert [card["card"]["id"] for card in shell.pr_cards] == ["merge:29020:cccc333"] and shell.enqueues() == []
    merge_go(shell, "29020", "merge #29020 at cccc333")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


def test_a_root_go_whatever_its_leading_words_answers_the_merge_ask_and_show_names_the_key_until_then(shell, config, tmp_path, capsys):
    merge_mode(config, "every")
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    capsys.readouterr()
    cli(shell, config, "show")
    assert "- #29020 waits on `merge:29020:cccc333`: a root go naming #29020 at cccc333\n" in capsys.readouterr().out
    for text in ("withdrawn: merge #29020 at cccc333", "hold: do not go #29020 at cccc333", 'he said "go #29020 at cccc333" yesterday'):
        merge_go(shell, "29020", text)
    landing_pass(shell, config)
    assert shell.enqueues() == []
    merge_go(shell, "29020", "Owner tapped Merge on board row merge-prs: go #29020 at cccc333 (fidelity ok, finder ok).")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]
    cli(shell, config, "show")
    assert "waits on `merge:29020" not in capsys.readouterr().out


@pytest.mark.parametrize(("graded", "warns"), [("ok F307 at cccc333. title: matches", False), ("ok F307 at cccc333. title: stale, it names the old fix", True)])
def test_a_stale_title_in_the_newest_grade_warns_root_and_the_lane_and_still_asks(shell, config, tmp_path, graded, warns):
    merge_mode(config, "every")
    lone_green_pr(shell)
    seq = grade(shell, "29020", graded)
    landing_pass(shell, config)
    [line] = escalations(shell)
    stale = f"; no go until the lane's title fix: cci #{seq} says `title: stale, it names the old fix`"
    assert line.startswith("MERGE-ASK ") and (stale in line) == warns and len(shell.pr_cards) == 1 and shell.enqueues() == []
    assert [LANE in post["to"].split(",") for post in blockers(shell) if "title: stale" in post_body(post)] == ([True] if warns else [])


def test_merge_mode_every_asks_the_owner_once_per_head_and_enqueues_on_the_merge_answer(shell, config, tmp_path):
    merge_mode(config, "every")
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == [] and enqueues_of(tmp_path) == {}
    [line] = escalations(shell)
    assert line == "MERGE-ASK merge-ask:29020 #29020: #29020 cccc333 waits on the owner's tap on `merge:29020:cccc333` on merge card cci #900001 (merge mode every)"
    [card] = shell.pr_cards
    assert card == {
        "card": {
            "id": "merge:29020:cccc333",
            "type": "card",
            "title": "Merge this PR at cccc333?",
            "children": [
                {"id": "merge:29020:cccc333-context", "type": "markdown", "md": "Every PR waits for your answer.\n\nRecord each run's owner, by dana, +12 \u22123 lines, review: approved."},
                {"id": "merge:29020:cccc333-pr", "type": "pr", "repo": "Forge-AI/monorepo", "number": 29020},
                {"id": "merge:29020:cccc333-answer", "type": "choice", "options": [{"id": "merge", "label": "Merge", "recommended": True}, {"id": "hold", "label": "Hold"}]},
            ],
        },
        "session": "s-root",
    }
    assert not [call for call in shell.calls if call[:2] == ["cc-slack", "dm-status"] and "--root-only" not in call]
    merge_go(shell, "29020", "merge #29020 at bbbb222")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    merge_go(shell, "29020", "merge #29020 at cccc333")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


@pytest.mark.parametrize("row", ["", "29020", "merge-row-29020-cccc333"])
def test_a_go_on_a_head_a_tap_rule_names_enqueues_only_on_the_owners_board_tap_at_that_head(shell, config, row):
    merge_mode(config, "every")
    raw = json.loads(config.read_text())
    raw["landing"]["tap_rules"] = {"rule/82317b0": ["go/sand/next/*"]}
    config.write_text(json.dumps(raw))
    shell.register = {"rule/82317b0": {"kind": "order", "status": "in-force"}}
    lone_green_pr(shell)
    shell.files["29020"] = ["go/sand/next/vm/run.go"]
    grade(shell, "29020", "ok F307 at cccc333.")
    shell.board["merge-rows"] = {row or "29020": {"verdict": "approved", "head": "cccc333333" if row else "bbbb222222", "at": "2026-10-01T17:40:00Z"}}
    merge_go(shell, "29020", "merge #29020 at cccc333")
    landing_pass(shell, config)
    landing_pass(shell, config)
    refusals = [post for post in blockers(shell) if "TAP-REQUIRED" in post_body(post)]
    if row:
        assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and refusals == []
        return
    assert shell.enqueues() == []
    [blocker] = refusals
    assert "root" in blocker["to"].split(",") and post_body(blocker).endswith("; TAP-REQUIRED rule/82317b0: it lands only on the owner's tap at this head, and a root go is no tap; no approval on the root's board names cccc333333")


@pytest.mark.parametrize(
    ("graded", "go", "admitted"),
    [
        ("ok cccc333 pure port, no interaction: it only ports old-tree #43020.", "go port #29020 at cccc333", True),
        ("ok cccc333 pure port, no interaction: it only ports old-tree #43020.", "go #29020 at cccc333, a pure port", True),
        ("ok cccc333 port touches rebuild code: it edits the pod writer.", "go port #29020 at cccc333", False),
        ("ok cccc333 pure port, no interaction: it only ports old-tree #43020.", "go #29020 at cccc333", True),
    ],
    ids=["go-port", "go-saying-pure-port", "grade-touching-rebuild-code", "plain-go-on-a-port-grade"],
)
def test_a_pure_port_lands_on_a_root_go_with_no_board_tap_only_when_grade_and_go_both_say_so(shell, config, graded, go, admitted):
    merge_mode(config, "every")
    raw = json.loads(config.read_text())
    raw["landing"]["tap_rules"] = {"rule/82317b0": ["go/sand/next/*"]}
    config.write_text(json.dumps(raw))
    shell.register = {"rule/82317b0": {"kind": "order", "status": "in-force"}}
    lone_green_pr(shell)
    shell.files["29020"] = ["go/sand/next/vm/run.go"]
    seq = grade(shell, "29020", graded)
    merge_go(shell, "29020", go)
    landing_pass(shell, config)
    landing_pass(shell, config)
    refusals = [post for post in blockers(shell) if "TAP-REQUIRED" in post_body(post)]
    admissions = [post_body(post) for post in notes(shell) if "EXEMPT-ADMITTED" in post_body(post)]
    if not admitted:
        assert shell.enqueues() == [] and len(refusals) == 1 and admissions == []
        return
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and refusals == []
    assert admissions == [f'EXEMPT-ADMITTED #29020 at cccc333333: fidelity grade cci #{seq} says "pure port, no interaction", so it goes straight to the merge queue with no go or board tap (root #194387)']


@pytest.mark.parametrize(("lane", "admitted"), [("go-sweep-5", True), ("go-sweep-6", True), ("p8a-step7", False)], ids=["configured-go-lane", "its-successor", "other-lane"])
def test_a_go_from_a_configured_go_lane_lands_like_a_root_go(shell, config, lane, admitted):
    merge_mode(config, "every")
    raw = json.loads(config.read_text())
    raw["landing"] |= {"go_lanes": ["go-sweep-5"]}
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    merge_go(shell, "29020", "go #29020 at cccc333", lane)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == ([["29020", "--partial"]] if admitted else [])


@pytest.mark.parametrize("pipeline", ["missing cccc333 plan:112 the plan names no gallery field", "ok cccc333 consistent with the plan"], ids=["pipeline-missing", "pipeline-plain-ok"])
def test_a_desk_pure_port_grade_admits_the_go_over_a_newer_pipeline_grade_without_the_port_words(shell, config, pipeline):
    merge_mode(config, "every")
    raw = json.loads(config.read_text())
    raw["landing"] |= {"tap_rules": {"rule/82317b0": ["go/sand/next/*"]}, "fidelity_lane": ["fidelity-desk", "fidelity-pipeline"]}
    config.write_text(json.dumps(raw))
    shell.register = {"rule/82317b0": {"kind": "order", "status": "in-force"}}
    lone_green_pr(shell)
    shell.files["29020"] = ["go/sand/next/vm/run.go"]
    grade(shell, "29020", "ok cccc333 pure port, no interaction: it only ports old-tree #43020.")
    grade(shell, "29020", pipeline, lane="fidelity-pipeline")
    merge_go(shell, "29020", "go port #29020 at cccc333")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


@pytest.mark.parametrize(("carry", "admitted"), [("ok cccc333 carried from bbbb222, patch-identical.", True), ("ok cccc333 carried from bbbb222; it adds new behavior in the reader.", False)], ids=["patch-identical-carry", "carry-adding-behavior"])
def test_a_port_grade_reaches_a_restacked_head_through_its_carry_chain(shell, config, carry, admitted):
    merge_mode(config, "every")
    raw = json.loads(config.read_text())
    raw["landing"]["tap_rules"] = {"rule/82317b0": ["go/sand/next/*"]}
    config.write_text(json.dumps(raw))
    shell.register = {"rule/82317b0": {"kind": "order", "status": "in-force"}}
    lone_green_pr(shell)
    shell.files["29020"] = ["go/sand/next/vm/run.go"]
    grade(shell, "29020", "ok bbbb222 pure port, no interaction: it only ports old-tree #43020.")
    grade(shell, "29020", carry)
    merge_go(shell, "29020", "go #29020 at cccc333")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == ([["29020", "--partial"]] if admitted else [])


TEST_ONLY_GRADE = "ok cccc333 test-only or CI-only, no non-test code: it adds cases and a pipeline step."


def tap_ruled_pr(shell, config, files, **landing):
    merge_mode(config, "every")
    raw = json.loads(config.read_text())
    raw["landing"]["tap_rules"] = {"rule/82317b0": ["go/sand/next/*"]}
    raw["landing"] |= landing
    config.write_text(json.dumps(raw))
    shell.register = {"rule/82317b0": {"kind": "order", "status": "in-force"}}
    lone_green_pr(shell)
    shell.files["29020"] = files


@pytest.mark.parametrize(
    "go",
    ["go test-only #29020 at cccc333", "go ci-only #29020 at cccc333", "go #29020 at cccc333, test-only", "go #29020 at cccc333, CI-only", "go port #29020 at cccc333"],
    ids=["go-test-only", "go-ci-only", "plain-go-test-only", "plain-go-ci-only", "go-port"],
)
def test_a_test_only_pr_lands_on_a_root_go_with_no_board_tap_and_one_exempt_admitted_note(shell, config, go):
    tap_ruled_pr(shell, config, ["go/sand/next/vm/run_test.go", "go/sand/next/testdata/a.json", ".buildkite/pipeline.yml"])
    seq = grade(shell, "29020", TEST_ONLY_GRADE)
    merge_go(shell, "29020", go)
    landing_pass(shell, config)
    landing_pass(shell, config)
    refusals = [post for post in blockers(shell) if "TAP-REQUIRED" in post_body(post)]
    admissions = [post_body(post) for post in notes(shell) if "EXEMPT-ADMITTED" in post_body(post)]
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and refusals == []
    assert admissions == [f'EXEMPT-ADMITTED #29020 at cccc333333: fidelity grade cci #{seq} says "test-only or CI-only, no non-test code", so it goes straight to the merge queue with no go or board tap (root #194387)']


@pytest.mark.parametrize("said", ["test-only, no non-test code", "CI-only, no non-test code"])
def test_a_grade_saying_test_only_or_ci_only_alone_lands_on_a_root_go_with_no_board_tap(shell, config, said):
    tap_ruled_pr(shell, config, ["go/sand/next/vm/run_test.go", ".buildkite/pipeline.yml"])
    seq = grade(shell, "29020", f"ok cccc333 {said}: it adds cases and a pipeline step.")
    merge_go(shell, "29020", "go test-only #29020 at cccc333")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and not [post for post in blockers(shell) if "TAP-REQUIRED" in post_body(post)]
    assert [post_body(post) for post in notes(shell) if "EXEMPT-ADMITTED" in post_body(post)] == [f'EXEMPT-ADMITTED #29020 at cccc333333: fidelity grade cci #{seq} says "{said}", so it goes straight to the merge queue with no go or board tap (root #194387)']


@pytest.mark.parametrize("go", ["go test-only #29020 at cccc333", "go port #29020 at cccc333"], ids=["go-test-only", "go-port"])
def test_a_test_only_claim_over_a_pr_that_changes_non_test_code_is_refused_naming_the_first_non_test_file(shell, config, go):
    tap_ruled_pr(shell, config, ["go/sand/next/vm/run_test.go", "go/sand/next/vm/run.go", "go/sand/next/vm/other.go"])
    grade(shell, "29020", TEST_ONLY_GRADE)
    merge_go(shell, "29020", go)
    landing_pass(shell, config)
    landing_pass(shell, config)
    refusals = [post_body(post) for post in blockers(shell) if "TEST-ONLY-MISMATCH" in post_body(post)]
    assert shell.enqueues() == [] and [note for note in notes(shell) if "EXEMPT-ADMITTED" in post_body(note)] == []
    assert len(refusals) == 1 and "TEST-ONLY-MISMATCH go/sand/next/vm/run.go is neither a test nor CI configuration" in refusals[0] and "other.go" not in refusals[0]


@pytest.mark.parametrize(
    ("comments", "admitted"),
    [
        ([], False),
        (["## Rulings\n\nThe api change only forwards the ported call; no interaction. # owner: 4c20204"], True),
        (["## Rulings\n\nno interaction, by my read"], False),
        (["The api change has no interaction. # owner: 4c20204"], False),
    ],
    ids=["no-comment", "owner-cleared", "uncited", "no-rulings-heading"],
)
def test_a_port_claim_over_a_pr_that_changes_a_file_outside_the_port_tree_waits_for_the_tap_unless_a_rulings_comment_clears_it(shell, config, comments, admitted):
    tap_ruled_pr(shell, config, ["go/sand/next/vm/run.go", "api/src/routes/run.ts"])
    shell.comments["29020"] = comments
    grade(shell, "29020", "ok cccc333 pure port, no interaction: it moves run.go and its route.")
    merge_go(shell, "29020", "go port #29020 at cccc333")
    landing_pass(shell, config)
    landing_pass(shell, config)
    refusals = [post_body(post) for post in blockers(shell) if "TAP-REQUIRED" in post_body(post)]
    assert bool(shell.enqueues()) is admitted and bool([note for note in notes(shell) if "EXEMPT-ADMITTED" in post_body(note)]) is admitted
    assert refusals == ([] if admitted else [refusals[0]]) and all("PORT-MISMATCH api/src/routes/run.ts is outside go/sand/next/" in refusal for refusal in refusals)


def test_the_legacy_port_phrase_key_still_sets_the_port_grade_phrase(shell, config):
    tap_ruled_pr(shell, config, ["go/sand/next/vm/run.go"], port_phrase="straight port")
    grade(shell, "29020", "ok cccc333 straight port of old-tree #43020.")
    merge_go(shell, "29020", "go port #29020 at cccc333")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


@pytest.mark.parametrize(
    ("name", "exempt"),
    [
        ("go/sand/internal/vm/run_test.go", True),
        ("dashboard/src/a.test.ts", True),
        ("dashboard/src/a.test.tsx", True),
        ("api/src/__tests__/x.ts", True),
        ("go/sand/internal/testdata/golden.json", True),
        ("dashboard/fixtures/a.json", True),
        (".buildkite/pipeline.yml", True),
        (".github/workflows/ci.yml", True),
        ("infra/pipelines/dev.buildkite.yaml", True),
        ("tools/ci/main.go", True),
        ("infra/ci/agents.ts", True),
        (".agents/skills/submit-pr/scripts/ship.py", True),
        (".agents/skills/submit-pr/tests/test_ship.py", True),
        ("docs/slack.md", True),
        ("go/sand/next/vm/fakes/clock.go", True),
        ("dashboard/e2e/realServe.ts", True),
        ("go/sand/internal/iris/iriseval/evals/platform-tasks/unlink-the-resolved-blocker/eval.yaml", True),
        ("go/sand/internal/iris/iriseval/evalset.go", False),
        ("dashboard/src/a.spec.ts", True),
        ("tools/rulings_test.py", True),
        ("go/sand/next/tests/helpers.go", True),
        ("go/sand/internal/vm/run.go", False),
        ("dashboard/src/a.ts", False),
        ("api/src/testing.ts", False),
        ("infra/pipelines/dev.yml", False),
        ("go/sand/mytestdata_test_helper.go", False),
    ],
)
def test_only_test_files_and_ci_configuration_count_as_exempt(name, exempt):
    assert runner_module.is_test_or_ci_file(name) is exempt


@pytest.mark.parametrize(
    ("graded", "files", "straight"),
    [
        (TEST_ONLY_GRADE, ["go/sand/next/vm/run_test.go", ".buildkite/pipeline.yml"], True),
        ("ok cccc333 CI-only, no non-test code: it adds a pipeline step.", [".buildkite/pipeline.yml"], True),
        ("ok cccc333 pure port, no interaction: it only ports old-tree #43020.", ["go/sand/next/vm/run.go"], True),
        (TEST_ONLY_GRADE, ["go/sand/next/vm/run_test.go", "go/sand/next/vm/run.go"], False),
        ("ok F307 at cccc333.", ["go/sand/next/vm/run_test.go"], False),
    ],
    ids=["test-only", "ci-only", "pure-port", "test-only-claim-over-code", "plain-ok"],
)
def test_an_exempt_change_goes_straight_to_the_queue_in_the_same_pass_with_no_go_or_tap(shell, config, graded, files, straight):
    """Owner 7:00 PM Oct 29 on card land-faster, 'all 3' (root #194387): test-only, CI-only and pure-port changes go straight to Graphite's queue by default, not through the drive queue's gate."""
    board_surface(config)
    lone_green_pr(shell)
    shell.files["29020"] = files
    grade(shell, "29020", graded)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == ([["29020", "--partial"]] if straight else [])


def test_an_exempt_change_a_hold_names_stays_out_of_the_queue(shell, config, tmp_path):
    board_surface(config)
    lone_green_pr(shell)
    shell.files["29020"] = ["go/sand/next/vm/run_test.go"]
    grade(shell, "29020", TEST_ONLY_GRADE)
    root_hold(shell, 29020, text="hold #29020 owner hold until the parity waves land")
    landing_pass(shell, config)
    assert shell.enqueues() == []


def test_a_kept_tap_waits_for_the_root_go_before_the_queue(shell, config, tmp_path):
    """Root #195224: TAP-KEPT misread four grades on Oct 29, so a kept tap is never exempt; the head waits for root's go naming the fix."""
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    grade(shell, "29020", KEPT_GRADE)
    landing_pass(shell, config)
    assert [note["text"].split(":")[0] for note in tap_notes(shell)] == ["TAP-KEPT #29020"]
    assert shell.enqueues() == []
    assert not [post for post in shell.posts if "EXEMPT-ADMITTED" in post_body(post)]


def test_a_kept_tap_holds_when_a_later_grade_at_the_same_head_omits_the_phrase(shell, config, tmp_path):
    """Root #205918: #45393 at 389e77a drew TAP-KEPT, then TAP-NOT-CARRIED nine minutes later because a newer grade at the same head lacked the phrase."""
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    grade(shell, "29020", KEPT_GRADE)
    landing_pass(shell, config)
    grade(shell, "29020", "ok cccc333333 a review fix: the poll gains a nil check.")
    landing_pass(shell, config)
    assert [note["text"].split(":")[0] for note in tap_notes(shell)] == ["TAP-KEPT #29020"]


def test_a_port_exemption_carries_to_a_head_a_head_record_calls_patch_identical(shell, config):
    """Root #205918: #45529's runner rebase to 17ac4c6 was patch-identical by patch-id, and the head still drew TAP-REQUIRED (#205554)."""
    merge_mode(config, "every")
    raw = json.loads(config.read_text())
    raw["landing"]["tap_rules"] = {"rule/82317b0": ["go/sand/next/*"]}
    config.write_text(json.dumps(raw))
    shell.register = {"rule/82317b0": {"kind": "order", "status": "in-force"}}
    lone_green_pr(shell)
    shell.files["29020"] = ["go/sand/next/vm/run.go"]
    seq = grade(shell, "29020", "ok aaaa111111 pure port, no interaction: it only ports old-tree #43020.")
    lane_head_record(shell, "29020", "HEAD #29020 cccc333333 (was aaaa111111): the landing runner rebased it onto dev for its direct merge, patch-identical to aaaa111111 by patch-id")
    grade(shell, "29020", "ok cccc333333 F307 at cccc333.")
    merge_go(shell, "29020", "go port #29020 at cccc333")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert not [post for post in blockers(shell) if "TAP-REQUIRED" in post_body(post)]
    assert [post_body(post) for post in notes(shell) if "EXEMPT-ADMITTED" in post_body(post)] == [f'EXEMPT-ADMITTED #29020 at cccc333333: fidelity grade cci #{seq} says "pure port, no interaction", so it goes straight to the merge queue with no go or board tap (root #194387)']


def test_a_long_branching_chain_of_patch_identical_heads_is_walked_once_per_head(shell, config):
    """Root #207477: 0.7.639 read the exemption back along every path of a PR's patch-identical head moves, recomputing them at each step, and the landing runner's first pass never finished."""
    merge_mode(config, "every")
    raw = json.loads(config.read_text())
    raw["landing"]["tap_rules"] = {"rule/82317b0": ["go/sand/next/*"]}
    config.write_text(json.dumps(raw))
    shell.register = {"rule/82317b0": {"kind": "order", "status": "in-force"}}
    lone_green_pr(shell)
    shell.files["29020"] = ["go/sand/next/vm/run.go"]
    heads = [hashlib.sha1(str(n).encode()).hexdigest()[:10] for n in range(40)] + ["cccc333333"]
    for n in range(1, len(heads)):
        for back in (1, 2):
            if n >= back:
                lane_head_record(shell, "29020", f"HEAD #29020 {heads[n]} (was {heads[n - back]}): the landing runner rebased it, patch-identical by patch-id")
    grade(shell, "29020", "ok cccc333333 F307 at cccc333.")
    merge_go(shell, "29020", "go #29020 at cccc333")
    landing_pass(shell, config)
    assert [post for post in blockers(shell) if "TAP-REQUIRED" in post_body(post)]


@pytest.mark.parametrize("state", ["board unread", "row absent", "row superseded"])
def test_a_tap_rule_never_guesses_a_merge_on_an_unread_board_and_gates_only_while_its_row_is_in_force(shell, config, state):
    merge_mode(config, "every")
    raw = json.loads(config.read_text())
    raw["landing"]["tap_rules"] = {"rule/82317b0": ["go/sand/next/*"]}
    config.write_text(json.dumps(raw))
    shell.register = {} if state == "row absent" else {"rule/82317b0": {"kind": "order", "status": "superseded" if state == "row superseded" else "in-force"}}
    shell.board_error = "cc-present: board offline" if state == "board unread" else ""
    lone_green_pr(shell)
    shell.files["29020"] = ["go/sand/next/vm/run.go"]
    grade(shell, "29020", "ok F307 at cccc333.")
    merge_go(shell, "29020", "merge #29020 at cccc333")
    landing_pass(shell, config)
    landing_pass(shell, config)
    warnings = [post_body(post) for post in notes(shell) if "TAP-UNREAD" in post_body(post)]
    assert not [post for post in blockers(shell) if "TAP-REQUIRED" in post_body(post)]
    if state != "board unread":
        assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and warnings == []
        return
    assert shell.enqueues() == []
    [warning] = warnings
    assert warning.endswith("WARNING TAP-UNREAD #29020: the root board did not read, so the runner leaves it unqueued for the landing desk")


def test_the_prs_a_root_go_names_enqueue_before_any_status_read_of_the_rest_of_the_ledger(shell, config, tmp_path):
    merge_mode(config, "every")
    heads = {str(pr): f"{pr:x}aaaaaa" for pr in range(30000, 30040)}
    gone = ["30005", "30017", "30031"]
    shell.rows = [{"pr": pr, "lane": LANE, "branch": f"b/{pr}", "base": "dev", "head": head, "state": "open", "reported_head": head} for pr, head in heads.items()]
    for pr, head in heads.items():
        shell.gates[pr] = [gate(f"#{pr} GREEN {head} graphite READY", would=f"#{pr}") if pr in gone else gate(f"#{pr} BLOCKED {head} CI running (graphite RUNNING)")]
    for pr in gone:
        shell.enqueue_out[pr] = (0, f"enqueue #{pr}: {{}}\n")
        grade(shell, pr, f"ok F307 at {heads[pr][:7]}.")
        merge_go(shell, pr, f"merge #{pr} at {heads[pr][:7]}")
    landing_pass(shell, config)
    assert sorted(call[1:] for call in shell.enqueues()) == [[pr, "--partial"] for pr in gone]
    last = max(shell.calls.index(call) for call in shell.enqueues())
    rest = {pr: head for pr, head in heads.items() if pr not in gone}
    assert all(shell.calls.index(call) > last for call in checks(shell) if call[1] in rest)
    assert all(shell.calls.index(call) > last for call in graphql_reads(shell) if any(head in call[4] for head in rest.values()))
    assert {call[1] for call in checks(shell)} == set(heads)


def five_pr_fork(shell: FakeShell) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    heads = {"31001": "aaaa111111", "31002": "bbbb222222", "31003": "cccc333333", "31004": "dddd444444", "31005": "eeee555555"}
    bases = {"31001": "dev", "31002": "f/1", "31003": "f/2", "31004": "f/2", "31005": "f/4"}
    shell.rows = [{"pr": pr, "lane": LANE, "branch": f"f/{pr[-1]}", "base": bases[pr], "head": head, "state": "open", "reported_head": head, "landed_sha": f"f00d00{pr}"} for pr, head in heads.items()]
    green = {pr: f"#{pr} GREEN {head} graphite READY" for pr, head in heads.items()}
    queued = {pr: f"#{pr} QUEUED {heads[pr]} graphite QUEUED_TO_MERGE" for pr in ("31001", "31002", "31003")}
    shell.gates["31003 31005"] = [gate(*green.values(), would="#31001 #31002 #31003 #31004 #31005")]
    shell.enqueue_out["31003"] = (0, "enqueue #31001 #31002 #31003: {}\n")
    shell.enqueue_out["31005"] = (0, "enqueue #31004 #31005: {}\n")
    for pr, head in heads.items():
        merge_go(shell, pr, f"merge #{pr} at {head[:7]}")
    return heads, green, queued


def test_a_forked_stack_of_five_with_go_on_all_enqueues_each_chain_as_its_own_batch_in_one_pass(shell, config, tmp_path):
    merge_mode(config, "every")
    heads, green, _ = five_pr_fork(shell)
    shell.gates["31003"] = [gate(*green.values())]
    for pr, head in heads.items():
        grade(shell, pr, f"ok F307 at {head[:7]}.")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["31003", "--partial"], ["31005", "--partial"]]
    assert not [line for line in escalations(shell) if line.startswith("FORK-DEFERRED ")]
    assert list(enqueues_of(tmp_path)) == ["enqueue:31001@aaaa111111,31002@bbbb222222,31003@cccc333333,31004@dddd444444,31005@eeee555555#1"]


def bound_chain(shell: FakeShell, bound: str) -> dict[str, str]:
    heads = {"32001": "aaaa111111", "32002": "bbbb222222", "32003": "cccc333333"}
    bases = {"32001": "dev", "32002": "c/1", "32003": "c/2"}
    shell.rows = [{"pr": pr, "lane": LANE, "branch": f"c/{pr[-1]}", "base": bases[pr], "head": head, "state": "open", "reported_head": head} for pr, head in heads.items()]
    for pr, head in heads.items():
        grade(shell, pr, f"ok F307 at {head[:7]}.")
    merge_go(shell, "32001", f"go #32001 at aaaa111 and #32002 at bbbb222 with {bound}")
    merge_go(shell, "32003", "go #32003 at cccc333")
    shell.enqueue_out["32003"] = (0, "enqueue #32001 #32002 #32003: {}\n")
    return {pr: f"#{pr} GREEN {head} graphite READY" for pr, head in heads.items()}


def test_a_go_with_a_batch_enqueues_nothing_until_every_pr_it_binds_enqueues_in_one_call(shell, config, tmp_path):
    merge_mode(config, "every")
    green = bound_chain(shell, "#32003")
    shell.gates["32003"] = [gate(green["32001"], green["32002"], "#32003 BLOCKED cccc333333 CI running (graphite RUNNING)", would="#32001 #32002")]
    landing_pass(shell, config)
    assert shell.enqueues() == []
    [waits] = [line for line in escalations(shell) if line.startswith("BATCH-WAITS ")]
    assert "binds #32001 #32002 into a batch" in waits
    shell.gates["32003"] = [gate(*green.values(), would="#32001 #32002 #32003")]
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["32003", "--partial"]]


def test_a_go_bound_to_a_pr_with_no_ledger_row_enqueues_nothing(shell, config, tmp_path):
    merge_mode(config, "every")
    green = bound_chain(shell, "#32003 #32009")
    shell.gates["32003"] = [gate(*green.values(), would="#32001 #32002 #32003")]
    landing_pass(shell, config)
    assert shell.enqueues() == []


def test_a_chain_stacked_on_an_open_fork_waits_for_the_fork_to_land(shell, config, tmp_path):
    merge_mode(config, "every")
    heads, green, queued = five_pr_fork(shell)
    shell.gates["31003"] = [gate(*green.values()), gate(green["31001"], green["31002"], green["31003"], would="#31001 #31002 #31003")]
    for pr in ("31001", "31002", "31003"):
        grade(shell, pr, f"ok F307 at {heads[pr][:7]}.")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["31003", "--partial"]]
    for pr in ("31004", "31005"):
        grade(shell, pr, f"ok F307 at {heads[pr][:7]}.")
    shell.gates["31003"] = [gate(*queued.values(), green["31004"], green["31005"])]
    shell.gates["31003 31005"] = [gate(*queued.values(), green["31004"], green["31005"], would="#31004 #31005")]
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1
    [deferred] = [line for line in escalations(shell) if line.startswith("FORK-DEFERRED ")]
    assert "#31004 #31005 fork off #31002, which has not landed" in deferred
    shell.rows = [dict(row, state="landed") if row["pr"] in queued else dict(row, base="dev") if row["pr"] == "31004" else row for row in shell.rows]
    shell.gates["31005"] = [gate(green["31004"], green["31005"], would="#31004 #31005")]
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["31003", "--partial"], ["31005", "--partial"]]


def test_a_pass_reads_the_queue_once_for_its_requeue_and_its_landed_checks(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.rows = [
        {"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "29100", "lane": LANE, "branch": "b/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
    ]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    shell.gates["29100"] = [gate(f"#29100 BLOCKED dddd444444 {CONFLICT}")]
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]
    shell.calls.clear()
    shell.rows[1] = dict(shell.rows[1], head="eeee555555", reported_head="eeee555555")
    shell.gates["29100"] = [gate(f"#29100 BLOCKED eeee555555 {CONFLICT}")]
    landing_pass(shell, config)
    reads = [call for call in shell.calls if call[:4] == ["ccx", "vcs", "pr", "status"]]
    assert [call[7:] for call in reads] == [["--max-age", "300s", "29020", "29100"]]
    assert runner_module.time_bound(reads[0]) == runner_module.QUEUE_STATUS_SECONDS


def test_a_pass_reads_pr_status_in_pages_of_twenty_five(shell, config, tmp_path):
    board_surface(config)
    shell.rows = [{"pr": str(pr), "lane": LANE, "branch": f"b/{pr}", "base": "dev", "head": f"{pr:x}aaaaaa", "state": "open", "reported_head": f"{pr:x}aaaaaa"} for pr in range(30000, 30030)]
    shell.queue |= {row["pr"]: "not queued" for row in shell.rows}
    landing_pass(shell, config)
    reads = [call[9:] for call in shell.calls if call[:4] == ["ccx", "vcs", "pr", "status"]]
    assert reads == [[str(pr) for pr in range(30000, 30025)], [str(pr) for pr in range(30025, 30030)]]


def test_a_status_read_that_fails_is_one_visible_escalation_per_cause_and_hour(shell, config, tmp_path):
    board_surface(config)
    lone_green_pr(shell)
    shell.status_error = "ccx vcs pr status -R Forge-AI/monorepo --json 29020 timed out after 300s and was killed"
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [line for line in escalations(shell) if line.startswith("STATUS-READ-FAILED")] == [
        f"STATUS-READ-FAILED pr-status:{shell.now():%Y%m%d%H}:{hashlib.sha1(shell.status_error.encode()).hexdigest()[:8]} landing: `ccx vcs pr status` over 1 open rows failed: {shell.status_error};"
        " this pass requeues nothing, settles no landing from the queue, and routes no PR without a go, and the next pass reads again"
    ]


def ruling_card(cards: Path, key: str, title: str) -> Path:
    card = cards / f"{key}.json"
    card.write_text(json.dumps({"status": "needs_you", "title": title, "body": "This also means:\n1. Nothing copies it to the laptop.", "options": ["Right", "Some wrong", "Open board"], "waits": key}))
    return card


def test_the_landing_pass_logs_one_ruling_ask_per_round_however_many_ruling_cards_queue(shell, config, tmp_path):
    cards = tmp_path / "cards"
    cards.mkdir()
    for index in range(3):
        ruling_card(cards, f"rule:5c0e9a{index}", f"\u201cruling {index}\u201d")
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    for index in range(3, 30):
        ruling_card(cards, f"rule:5c0e9a{index}", f"\u201cruling {index}\u201d")
    landing_pass(shell, config)
    [line] = [line for line in escalations(shell) if line.startswith("RULING-ASK")]
    assert line.startswith("RULING-ASK ruling-ask:round-1 round-1: 3 ruling confirmations queue | when the owner can take a round, run `rulings.py round --drive d1`")
    assert "on the live cc-present board as round 1, foundational rows first, never as an AskUserQuestion or a Slack card" in line
    assert "`rulings.py confirm <key> --tick all --capture <digest>`" in line
    assert not [call for call in shell.calls if call[:2] == ["cc-slack", "dm-status"] and "--root-only" not in call]
    rounds = cards / "rounds"
    rounds.mkdir()
    (rounds / "round-1.json").write_text("{}")
    landing_pass(shell, config)
    skipped = rounds / "skipped"
    skipped.mkdir()
    for card in cards.glob("*.json"):
        card.replace(skipped / card.name)
    ruling_card(cards, "rule:5c0e9b0", "\u201cruling after a drained queue\u201d")
    landing_pass(shell, config)
    assert [line.split(" ", 2)[1] for line in escalations(shell) if line.startswith("RULING-ASK")] == ["ruling-ask:round-1", "ruling-ask:round-2"]


def test_the_landing_pass_logs_no_ruling_ask_while_no_ruling_card_queues(shell, config, tmp_path):
    (tmp_path / "cards" / "rounds").mkdir(parents=True)
    (tmp_path / "cards" / "round-1.md").write_text("# Round 1")
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    assert [line for line in escalations(shell) if line.startswith("RULING-ASK")] == []


def test_merge_mode_high_risk_enqueues_a_pr_off_the_risk_list_without_asking(shell, config, tmp_path):
    merge_mode(config, "high-risk")
    lone_green_pr(shell)
    shell.files["29020"] = ["dashboard/src/iris/Thread.tsx", "docs/object-hierarchy.md"]
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and escalations(shell) == []


def test_merge_mode_high_risk_asks_for_a_pr_that_touches_a_migration(shell, config, tmp_path):
    merge_mode(config, "high-risk")
    lone_green_pr(shell)
    shell.files["29020"] = ["api/src/routes/runs.ts", "api/migrations/0399_run_owner.sql"]
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    [line] = escalations(shell)
    assert line.startswith("MERGE-ASK ") and "merge mode high-risk" in line
    assert shell.pr_cards[0]["card"]["children"][0]["md"].startswith("It changes api/migrations/0399_run_owner.sql.\n\n")
    merge_go(shell, "29020", "merge #29020 at cccc333")
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1


def test_merge_mode_high_risk_enqueues_nothing_while_the_changed_files_do_not_read(shell, config, tmp_path):
    merge_mode(config, "high-risk")
    lone_green_pr(shell)
    shell.files_error = "gh: Server Error (HTTP 502)"
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == []
    [line] = escalations(shell)
    assert line.startswith("FILES-READ-FAILED files:29020:cccc333333 #29020: GitHub refused the changed files of #29020: gh: Server Error (HTTP 502)")
    shell.files_error = ""
    shell.files["29020"] = ["README.md"]
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1


RESERVED = "RESERVED-NO-ANSWER reserved/user-data\tUser data stays where it is.\n"


def test_checks_wait_for_go_runs_no_gate_until_a_root_go_comes_after_its_sequence(shell, config, tmp_path):
    merge_mode(config, "every")
    raw = json.loads(config.read_text())
    raw["landing"]["checks_wait_for_go"] = 34700
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    landing_pass(shell, config)
    checks = lambda: [call for call in shell.calls if Path(call[0]).name == "stack-enqueue" and "--check" in call]
    assert checks() == []
    [line] = escalations(shell)
    assert line.startswith("GATE-PAUSED gate-paused:34700 landing: landing.checks_wait_for_go is 34700")
    merge_go(shell, "29020", "merge #29020 at cccc333")
    landing_pass(shell, config)
    assert checks() and len(shell.enqueues()) == 1


def board_surface(config: Path) -> None:
    merge_mode(config, "every")
    raw = json.loads(config.read_text())
    raw["landing"]["merge_ask_surface"] = "board"
    config.write_text(json.dumps(raw))


def stack_named(shell: FakeShell) -> set[str]:
    return {pr for call in shell.calls if Path(call[0]).name == "stack-enqueue" for pr in itertools.takewhile(str.isdigit, call[1:])}


def board_lane(config: Path) -> None:
    raw = json.loads(config.read_text())
    raw["landing"]["board_lane"] = "board-refresher"
    config.write_text(json.dumps(raw))


def tap(shell: FakeShell, pr: str, head: str) -> None:
    shell.board.setdefault("merge-rows", {})[pr] = {"verdict": "approved", "head": head, "at": "2026-10-01T17:40:00Z"}


def lane_head_record(shell: FakeShell, pr: str, text: str) -> int:
    seq = 34766 + len(shell.records)
    shell.records.append({"seq": seq, "drive": "d1", "lane": LANE, "kind": "head", "to": ["root"], "refs": {"prs": [f"Forge-AI/monorepo#{pr}"]}, "text": text, "at": actions.stamp(shell.clock)})
    return seq


KEPT_GRADE = "ok cccc333333 a review fix: no new behavior since the tap at aaaa111111."


def tap_notes(shell: FakeShell) -> list[dict]:
    return [post for post in notes(shell) if post["text"].startswith("TAP-")]


def test_a_snapshot_from_an_older_board_watch_with_no_blocks_carries_no_tap_and_the_pass_goes_on(shell, config, tmp_path):
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    shell.snapshot_drops = {"blocks"}
    landing_pass(shell, config)
    shell.diffs["aaaa111111"] = shell.diffs["cccc333333"] = "diff --git a/x.go b/x.go\n+same\n"
    lane_head_record(shell, "29020", "HEAD #29020 cccc333333 (was aaaa111111): restack onto dev, range-diff patch-identical (3 commits)")
    landing_pass(shell, config)
    assert shell.carries == [] and tap_notes(shell) == []


@pytest.mark.parametrize("restack", ["head", "fidelity", "no new behavior", "chain", "carried: patch-identical to", "tap carries"])
def test_a_board_tap_carries_itself_to_a_patch_identical_restack_once_a_record_says_so(shell, config, tmp_path, restack):
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    landing_pass(shell, config)
    assert shell.carries == [] and tap_notes(shell) == []
    shell.diffs["aaaa111111"] = shell.diffs["cccc333333"] = "diff --git a/x.go b/x.go\n+same\n"
    if restack == "head":
        seqs = [lane_head_record(shell, "29020", "HEAD #29020 cccc333333 (was aaaa111111): restack onto dev, range-diff patch-identical (3 commits)")]
    elif restack == "fidelity":
        seqs = [grade(shell, "29020", "ok cccc333333 patch-identical to, with no root go at, aaaa111111 (#34700), patch-identical (patch-id 5ebf1cae3e6e).", lane="fidelity-desk")]
    elif restack == "no new behavior":
        seqs = [grade(shell, "29020", "ok cccc333333 carried from aaaa111111, patch-identical replay with no new behavior.")]
    elif restack == "carried: patch-identical to":
        seqs = [grade(shell, "29020", "ok cccc333333 carried: patch-identical to aaaa111 (ok #165915), on dev after the batch landed. A pure replay, so his 6:03 PM tap on aaaa111 carries under 399b5fa.")]
    elif restack == "tap carries":
        seqs = [grade(shell, "29020", "ok cccc333, tap carries (399b5fa): patch-identical replay of aaaa111 onto the restacked #42810. old-app: unchanged vs c57cad26 (release 10435).")]
    else:
        older = lane_head_record(shell, "29020", "HEAD #29020 bbbb222222 (was aaaa111111): restack, range-diff patch-identical")
        seqs = [grade(shell, "29020", "ok cccc333333 carried from bbbb222222, patch-identical replay onto dev."), older]
    landing_pass(shell, config)
    landing_pass(shell, config)
    cited = ", ".join(f"cci #{seq}" for seq in seqs)
    assert shell.carries == [{"--session": "s-root", "--block": "merge-rows", "--row": "29020", "--head": "cccc333333", "--note": f"patch-identical restack, per {cited}"}]
    assert shell.board["merge-rows"]["29020"]["head"] == "cccc333333" and shell.board["merge-rows"]["29020"]["carriedFrom"] == "aaaa111111"
    [note] = tap_notes(shell)
    assert note["to"] == "root,board-refresher"
    [go] = [post for post in shell.posts if post["kind"] == "go"]
    assert go["text"].startswith("go #29020 at cccc333 TAP-CARRIED: the owner's tap at aaaa111111 carries to cccc333333, a patch-identical restack; the runner posts this go itself")
    assert note["text"] == f"TAP-CARRIED #29020: the owner's tap at aaaa111111 on board block merge-rows now stands at cccc333333, a patch-identical restack, per {cited}, recorded as a board-write record the board's watch applies; the row asks no new tap"
    assert [line for line in escalations(shell) if line.startswith("TAP-")] == []


def test_a_record_calling_a_head_patch_identical_carries_no_tap_when_the_head_adds_a_commit(shell, config, tmp_path):
    """p8a-step7 #199440: #45309's head record said "adds one test-only commit on top of the patch-identical e86a899", and the runner carried the tap to d6f1bca on those words."""
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    shell.diffs["aaaa111111"] = "diff --git a/x.go b/x.go\n+same\n"
    shell.diffs["cccc333333"] = "diff --git a/x.go b/x.go\n+same\ndiff --git a/x_test.go b/x_test.go\n+added\n"
    lane_head_record(shell, "29020", "HEAD #29020 cccc333333 (was aaaa111111): adds one test-only commit on top of the patch-identical aaaa111111.")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.carries == [] and [post for post in shell.posts if post["kind"] == "go"] == []
    assert not [note for note in tap_notes(shell) if note["text"].startswith("TAP-CARRIED")]


def said_on(shell: FakeShell, said: list[tuple[str, str]]) -> int:
    seq = 0
    for kind, text in said:
        if kind == "head":
            seq = lane_head_record(shell, "29020", text)
        elif kind == "withdraw":
            seq = 34766 + len(shell.records)
            shell.records.append({"seq": seq, "drive": "d1", "lane": LANE, "kind": "withdraw", "to": [], "re": shell.records[-1]["seq"], "refs": {"prs": ["Forge-AI/monorepo#29020"]}, "text": text, "at": actions.stamp(shell.clock)})
        else:
            seq = grade(shell, "29020", text)
    return seq


@pytest.mark.parametrize(
    ("said", "quote", "heads"),
    [
        ([("head", "HEAD #29020 cccc333333 (was aaaa111111): patch-identical except one new behavior: retries the upload")], "HEAD #29020 cccc333333 (was aaaa111111): patch-identical except one new behavior: retries the upload", "cccc333333"),
        ([("head", "HEAD #29020 cccc333333 (was bbbb222222): adds upload retries; bbbb222222 (was aaaa111111): restack, range-diff patch-identical"), ("fidelity", "ok cccc333333 a fixed version of bbbb222222 that adds new behavior: the upload retries.")], "fidelity: ok cccc333333 a fixed version of bbbb222222 that adds new behavior: the upload retries.", "bbbb222222 -> cccc333333"),
        ([("fidelity", "ok cccc333333 carried from aaaa111111, patch-identical except one new behavior: retries the upload.")], "fidelity: ok cccc333333 carried from aaaa111111, patch-identical except one new behavior: retries the upload.", "cccc333333"),
        ([("fidelity", "ok cccc333333 a fixed version of aaaa111111."), ("head", "HEAD #29020 cccc333333: adds new behavior: upload retries")], "HEAD #29020 cccc333333: adds new behavior: upload retries", "cccc333333"),
    ],
)
def test_a_board_tap_never_carries_to_a_head_that_adds_new_behavior_and_root_and_the_board_lane_hear_once_it_needs_a_new_row(shell, config, tmp_path, said, quote, heads):
    """Owner 6:59 PM Oct 29 (root #194315): a re-asked row says when he tapped, what changed since, and why it is shown again, so the note carries his tap's Pacific time, the heads since it, and the record naming the new behavior."""
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    seq = said_on(shell, said)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.carries == [] and shell.board["merge-rows"]["29020"]["head"] == "aaaa111111"
    [note] = tap_notes(shell)
    assert note["to"] == "root,board-refresher"
    assert post_body(note).strip() == f"TAP-NOT-CARRIED #29020: the owner's tap at aaaa111111 (10:40 AM Oct 1 Pacific) on board block merge-rows does not carry to cccc333333, since cci #{seq} says that head adds new behavior: \"{quote}\". Heads since his tap: {heads}. The board needs a new row for #29020 at cccc333333 opening 'You approved this at 10:40 AM Oct 1. Since then: <what changed>. Shown again because: <the new behavior>.'"


@pytest.mark.parametrize("identical", [True, False], ids=["patch-identical", "changed"])
def test_a_tap_carries_itself_to_a_head_whose_own_commits_have_the_tapped_heads_patch(shell, config, tmp_path, identical):
    """Root #197523, p8a-step7 #197578: the merger's rebases of #45367 were patch-identical to his tap at 61cd21b, yet the runner asked for a new row because no grade used the keep phrase."""
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    shell.diffs["aaaa111111"] = "diff --git a/x.go b/x.go\n+tapped\n"
    shell.diffs["cccc333333"] = shell.diffs["aaaa111111"] if identical else "diff --git a/x.go b/x.go\n+changed\n"
    landing_pass(shell, config)
    carried = [note["text"] for note in tap_notes(shell) if note["text"].startswith("TAP-CARRIED-BY-PATCH")]
    assert carried == (["TAP-CARRIED-BY-PATCH #29020: the owner's tap at aaaa111111 on board block merge-rows now stands at cccc333333, a patch-identical restack by git patch-id over its 1 own commits, recorded as a board-write record the board's watch applies; the row asks no new tap"] if identical else [])
    assert [carry["--head"] for carry in shell.carries] == (["cccc333333"] if identical else [])


def test_a_successor_fidelity_desk_grade_counts_with_no_config_edit(shell, config, tmp_path):
    """Root #197631: fidelity-desk-13 succeeded fidelity-desk-12, and its ok on #45427 read as ungraded because the configured list stopped at -12."""
    merge_mode(config, "every")
    raw = json.loads(config.read_text())
    raw["landing"]["fidelity_lane"] = ["fidelity-desk", "fidelity-desk-12"]
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)
    merge_go(shell, "29020", "go #29020 at cccc333")
    grade(shell, "29020", "ok cccc333 resubscribes from the fetched position.", lane="fidelity-desk-13")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


@pytest.mark.parametrize(
    "graded",
    [
        "ok cccc333333 new-tree fix, stays a tap row: carries bbbb222.",
        "ok cccc333333 carried from my ok at bbbb222222, a review fix.",
        "ok cccc333333 a fixed version of aaaa111111: a review fix without new behavior.",
        "ok cccc333333 no new behavior since the tap at bbbb222222.",
    ],
)
def test_a_tap_keeps_only_on_a_grade_saying_no_new_behavior_since_that_tapped_head(shell, config, tmp_path, graded):
    """Root #195066: the runner read 'carried from my ok' and 'stays a tap row' as a review fix four times on Oct 29 and admitted new behavior with no tap."""
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    grade(shell, "29020", graded)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == [] and shell.carries == []
    assert not [note for note in tap_notes(shell) if note["text"].startswith("TAP-KEPT")]


@pytest.mark.parametrize(
    "said",
    [
        [("head", "HEAD #29020 cccc333333 (was aaaa111111): answers the finder's major with a nil check"), ("fidelity", "ok cccc333333 a nil check answers the finder's major; no new behavior since the tap at aaaa111.")],
        [("fidelity", KEPT_GRADE)],
    ],
)
def test_a_board_tap_keeps_over_a_graded_review_fix_and_root_hears_once_to_post_its_go(shell, config, tmp_path, said):
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    seq = said_on(shell, said)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.carries == []
    [note] = tap_notes(shell)
    assert note["to"] == "root,board-refresher"
    assert note["text"] == f"TAP-KEPT #29020: the owner's tap at aaaa111111 on board block merge-rows keeps at cccc333333, a review fix with no new behavior per fidelity grade cci #{seq} (rule 399b5fa), so it goes straight to the merge queue with no go (root #194387), and the row lists the fix"


@pytest.mark.parametrize(("graded", "row"), [(True, False), (False, True)], ids=["no-row", "ungraded"])
def test_a_merge_ask_names_only_a_graded_head_with_a_board_row(shell, config, tmp_path, graded, row):
    """Owner 5:22 PM Oct 29, third time: "tjhers nothing to tap on the baord stop using tht as an excuse"; root #191653 binds a MERGE-ASK to a fidelity grade and a board row at the head."""
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    shell.queue["29020"] = "not queued"
    shell.pr_status["29020"] = {"verdict": "landable"}
    if graded:
        grade(shell, "29020", "ok F307 at cccc333.")
    if row:
        shell.board_rows = [("29020", "cccc333333")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [line for line in escalations(shell) if line.startswith("MERGE-ASK")] == []
    missing = [note for note in notes(shell) if post_body(note).startswith("BOARD-ROW-MISSING")]
    if graded:
        [note] = missing
        assert note["to"] == "board-refresher" and post_body(note).startswith("BOARD-ROW-MISSING #29020 at cccc333333: graded ok")
    else:
        assert missing == []


def test_a_pass_reads_the_board_from_records_alone_for_its_tap_carries_and_its_rows(shell, config, tmp_path):
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    shell.queue["29020"] = "not queued"
    shell.pr_status["29020"] = {"verdict": "landable"}
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [post_body(note).split(":")[0] for note in notes(shell) if post_body(note).startswith("BOARD-ROW-MISSING")] == ["BOARD-ROW-MISSING #29020 at cccc333333"]
    assert [call for call in shell.calls if call[0] == "cc-present"] == []


def test_a_tap_keeps_only_once_the_finder_is_clear_at_the_fixed_head(shell, config, tmp_path):
    """p8a-step7 #192190: the runner noted TAP-KEPT for #44103 at 78aad32 while finder #191854's major on the head before it stood unanswered, and root's go on it had to be withdrawn."""
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    finder_verdict(shell, "29020", "major aaaa111 1 open: go/a.go:12 the retry drops the sealed read")
    grade(shell, "29020", KEPT_GRADE, finder=False)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert tap_notes(shell) == []
    finder_verdict(shell, "29020", "ok cccc333 no findings")
    landing_pass(shell, config)
    assert [note["text"].split(":")[0] for note in tap_notes(shell)] == ["TAP-KEPT #29020"]


def test_a_tap_keeps_over_a_pipeline_missing_a_desk_ok_overrides_at_the_same_head(shell, config, tmp_path):
    """p8a-step7 #193454: fidelity-desk-3's ok and root's TAP-KEPT go stood at #45286 c8a6014, but fidelity-pipeline's newer missing at that head kept the runner from noting TAP-KEPT, so it refused the head TAP-REQUIRED."""
    board_surface(config)
    board_lane(config)
    raw = json.loads(config.read_text())
    raw["landing"]["fidelity_lane"] = ["fidelity-desk", "fidelity-pipeline"]
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    grade(shell, "29020", KEPT_GRADE)
    grade(shell, "29020", "missing cccc333333 plan:266 lists every local chat before grouping teams.", lane="fidelity-pipeline")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [note["text"].split(":")[0] for note in tap_notes(shell)] == ["TAP-KEPT #29020"]


def test_a_root_go_on_a_head_whose_tap_does_not_keep_draws_one_tap_carry_failed_naming_why(shell, config, tmp_path):
    """p8a-step7 #193454: root's TAP-KEPT go named #45286 c8a6014, the runner did not carry the tap there, said nothing, and refused the head TAP-REQUIRED."""
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    seq = grade(shell, "29020", "ok cccc333333 new-tree fix, stays a tap row, carried from aaaa111.")
    merge_go(shell, "29020", "go #29020 at cccc333333 TAP-KEPT: owner tap at aaaa111 keeps")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.carries == []
    [line] = [line for line in said(shell) if line.startswith("TAP-CARRY-FAILED")]
    assert line.endswith(f"#29020: the owner's tap on #29020 at aaaa111111 on board block merge-rows does not carry to cccc333333, which a root go names: fidelity grade cci #{seq} does not say cccc333333 adds no new behavior since the tap at aaaa111111; until it carries, #29020 waits on the tap at this head")


def test_a_kept_tap_under_a_root_go_lands_the_tap_rule_head_when_the_board_refuses_the_carry(shell, config, tmp_path):
    """Root #192094: the runner noted TAP-KEPT for #44704, root posted the go, and the runner still refused it TAP-REQUIRED because the board named only the old head."""
    board_surface(config)
    board_lane(config)
    raw = json.loads(config.read_text())
    raw["landing"]["tap_rules"] = {"rule/82317b0": ["go/sand/next/*"]}
    config.write_text(json.dumps(raw))
    shell.register = {"rule/82317b0": {"kind": "order", "status": "in-force"}}
    lone_green_pr(shell)
    shell.files["29020"] = ["go/sand/next/vm/run.go"]
    tap(shell, "29020", "aaaa111111")
    shell.unknown_blocks = {"merge-rows"}
    grade(shell, "29020", KEPT_GRADE)
    landing_pass(shell, config)
    merge_go(shell, "29020", "go #29020 at cccc333333: carried from his tap on aaaa111 (go at that head), no new behavior (rule 399b5fa).")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [post for post in blockers(shell) if "TAP-REQUIRED" in post_body(post)] == []
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


@pytest.mark.parametrize(
    "said",
    [
        [("head", "HEAD #29020 cccc333333 (was aaaa111111): answers the finder's major with a nil check")],
        [("head", "HEAD #29020 cccc333333 (was aaaa111111): restack, range-diff not patch-identical, one commit answers the finder's major")],
        [("head", "HEAD #29020 cccc333333 (was aaaa111111): restack, range-diff patch-identical"), ("withdraw", "wrong: one commit changed"), ("head", "HEAD #29020 cccc333333 (was aaaa111111): one commit changed in the restack")],
    ],
)
def test_a_board_tap_at_an_ungraded_fixed_head_asks_for_nothing_until_its_grade_says_fix_or_new_behavior(shell, config, tmp_path, said):
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    said_on(shell, said)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.carries == [] and tap_notes(shell) == []


def test_a_kept_tap_carries_to_the_review_fix_once_root_posts_its_go_and_the_tap_rule_admits_the_head(shell, config, tmp_path):
    board_surface(config)
    board_lane(config)
    raw = json.loads(config.read_text())
    raw["landing"]["tap_rules"] = {"rule/82317b0": ["go/sand/next/*"]}
    config.write_text(json.dumps(raw))
    shell.register = {"rule/82317b0": {"kind": "order", "status": "in-force"}}
    lone_green_pr(shell)
    shell.files["29020"] = ["go/sand/next/vm/run.go"]
    tap(shell, "29020", "aaaa111111")
    graded = grade(shell, "29020", "ok cccc333333 restacked from aaaa111 (ok #166024) onto #44069 21c80e5. Its one commit changes only context lines. The added and removed lines are the same as at aaaa111. No new behavior since the tap at aaaa111.")
    landing_pass(shell, config)
    assert shell.carries == [] and [note["text"].split(":")[0] for note in tap_notes(shell)] == ["TAP-KEPT #29020"]
    merge_go(shell, "29020", "go #29020 at cccc333333: carried from his tap on aaaa111 (go at that head), no new behavior (rule 399b5fa).")
    go = shell.records[-1]["seq"]
    landing_pass(shell, config)
    landing_pass(shell, config)
    why = f"review fix with no new behavior, per fidelity grade cci #{graded} and root go cci #{go} (rule 399b5fa)"
    assert shell.carries == [{"--session": "s-root", "--block": "merge-rows", "--row": "29020", "--head": "cccc333333", "--note": why}]
    assert [note["text"] for note in tap_notes(shell)][1:] == [f"TAP-CARRIED #29020: the owner's tap at aaaa111111 on board block merge-rows now stands at cccc333333, a {why}, recorded as a board-write record the board's watch applies; the row asks no new tap"]
    assert not [post for post in blockers(shell) if "TAP-REQUIRED" in post_body(post)]
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


def test_a_fixed_head_a_root_go_names_asks_the_board_for_no_new_row(shell, config, tmp_path):
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    lane_head_record(shell, "29020", "HEAD #29020 cccc333333 (was aaaa111111): answers the finder's major with a nil check")
    root_go(shell, "go #29020 at cccc333 (fixed version of aaaa111, his tap)", shell.now())
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.carries == [] and tap_notes(shell) == []


@pytest.mark.parametrize(
    ("said", "board", "holds"),
    [
        ("go #29020 at cccc333333: owner tapped cccc333333 on the board (watchdog #172407).", None, "no verdict for #29020"),
        ("go #29020 at cccc333333: TAP-CARRIED from the board.", "aaaa111111", "approved at aaaa111111 on block merge-rows"),
        ("go #29020 at cccc333333: tapped, the watchdog says.", "aaaa111111", ""),
        ("go #29020 at cccc333333: owner tapped cccc333 on merge-rows.", "cccc333333", ""),
        ("go #29020 at cccc333333: TAP-KEPT from his tap at aaaa111 (#178242).", "aaaa111111", ""),
        ("go port #29020 at cccc333333: pure port, no tap needed.", None, ""),
        ("go #29020 at cccc333333: finder ok, fidelity ok, green.", None, ""),
        ("go #29020 at cccc333333: lands without his approval, a CI fix.", None, ""),
        ("go #29020 at cccc333333 (tap row; lands on the owner's tap)", None, ""),
        ("go #29020 at cccc333333: tap row, go on grades per root #203509; fidelity #203204 ok, finder ok #203234. Lands only on his board tap.", None, ""),
    ],
    ids=["no-verdict", "verdict-at-another-head", "bare-tapped-claims-nothing", "verdict-at-head", "kept-tap-names-its-head", "pure-port", "no-tap-claimed", "negated-approval", "root-tap-row-204186", "sweep-tap-row-203548"],
)
def test_a_root_go_claiming_a_tap_the_board_does_not_hold_draws_one_go_tap_missing_naming_what_it_holds(shell, config, tmp_path, said, board, holds):
    board_surface(config)
    lone_green_pr(shell)
    if board:
        tap(shell, "29020", board)
    merge_go(shell, "29020", said)
    seq = shell.records[-1]["seq"]
    landing_pass(shell, config)
    landing_pass(shell, config)
    missing = [post_body(post).splitlines()[0] for post in shell.posts if post_body(post).startswith("GO-TAP-MISSING")]
    expected = f"GO-TAP-MISSING go-tap-missing:{seq}:29020 #29020: root go cci #{seq} says the owner tapped #29020 at cccc333333, but no approval on the root's board names cccc333333 or a head the go names; the board holds {holds}. Withdraw the go, or post it again citing the board verdict at this head or the carry to it"
    assert missing == ([expected] if holds else [])


def test_go_tap_missing_goes_to_the_go_lanes_the_desk_and_the_owning_lane_not_root(shell, config, tmp_path):
    board_surface(config)
    desk_lane(config)
    raw = json.loads(config.read_text())
    raw["landing"]["go_lanes"] = ["go-sweep"]
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)
    merge_go(shell, "29020", "go #29020 at cccc333333: owner tapped cccc333333 on the board.")
    landing_pass(shell, config)
    assert heard(shell, "GO-TAP-MISSING") == [f"go-sweep,{DESK},{LANE}"]


@pytest.mark.parametrize("settled", ["go", "fidelity carry", "none"])
def test_a_carry_onto_a_merge_block_the_board_dropped_is_nothing_to_carry_settled_or_not(shell, config, tmp_path, settled):
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    shell.unknown_blocks = {"merge-rows"}
    lane_head_record(shell, "29020", "HEAD #29020 cccc333333 (was aaaa111111): restack onto dev, range-diff patch-identical (3 commits)")
    if settled == "go":
        root_go(shell, "go #29020 at cccc333 (patch-identical restack of his tap)", shell.now())
    elif settled == "fidelity carry":
        grade(shell, "29020", "ok cccc333333 carried from aaaa111111, patch-identical replay onto dev.")
    landing_pass(shell, config)
    landing_pass(shell, config)
    failed = [line for line in escalations(shell) if line.startswith("TAP-CARRY-FAILED")]
    assert (failed, tap_notes(shell)) == ([], [])


def test_a_root_go_posted_while_the_pass_runs_stops_its_merge_ask(shell, config, tmp_path, monkeypatch):
    board_surface(config)
    lone_green_pr(shell)
    shell.queue["29020"] = "not queued"
    shell.pr_status["29020"] = {"verdict": "landable"}
    grade(shell, "29020", "ok F307 at cccc333.")
    run = shell.run

    def tapped_mid_pass(argv, stdin=None, env=None):
        if argv[:4] == ["ccx", "vcs", "pr", "status"] and not any(record["kind"] == "go" for record in shell.records):
            merge_go(shell, "29020", "merge #29020 at cccc333")
        return run(argv, stdin, env)

    monkeypatch.setattr(shell, "run", tapped_mid_pass)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [line for line in escalations(shell) if line.startswith("MERGE-ASK")] == []


def test_a_board_surface_asks_from_the_status_read_with_no_check_until_a_root_go_names_the_head(shell, config, tmp_path):
    board_surface(config)
    lone_green_pr(shell)
    shell.board_rows = [("29020", "cccc333333")]
    shell.queue["29020"] = "not queued"
    shell.pr_status["29020"] = {"verdict": "landable"}
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert checks(shell) == [] and shell.pr_cards == []
    [line] = escalations(shell)
    assert line == "MERGE-ASK merge-ask:29020 #29020: #29020 cccc333 waits on the owner's tap on `merge:29020:cccc333` on the root's board (merge mode every)"
    merge_go(shell, "29020", "merge #29020 at cccc333")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and len(escalations(shell)) == 1


def test_on_a_board_surface_a_pr_without_a_go_gets_its_blockers_from_the_status_read_once_per_head(shell, config, tmp_path):
    board_surface(config)
    shell.rows = [
        {"pr": "29020", "lane": LANE, "branch": "a/1", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "29100", "lane": LANE, "branch": "b/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
    ]
    shell.queue |= {"29020": "not queued", "29100": "not queued"}
    shell.pr_status["29020"] = {"verdict": "blocked:conflict,ci-pending"}
    shell.pr_status["29100"] = {"verdict": "blocked:ci-red,changes-requested", "ci": {"state": "red", "failing": ["buildkite/test"]}, "approval": {"state": "changes-requested", "changes_requested_by": ["poetic-svc"]}}
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert checks(shell) == []
    assert [(post["to"], post_body(post)) for post in blockers(shell) if post["to"] != "root"] == [
        (LANE, "#29020 cccc333333: GitHub reads it as conflicting against its base; run ccx vcs stack submit from the branch's worktree"),
        (LANE, "#29100 dddd444444: CI is red: buildkite/test; changes requested by poetic-svc"),
    ]


def test_a_red_head_draws_one_blocker_whether_the_status_read_or_the_gate_names_it_first(shell, config, tmp_path):
    board_surface(config)
    head = "c" * 40
    shell.rows = [dict(ONE_ROW, head=head, reported_head=head)]
    shell.queue["29020"] = "not queued"
    shell.pr_status["29020"] = {"verdict": "blocked:ci-red", "ci": {"state": "red", "failing": ["buildkite/test"]}}
    shell.gates["29020"] = [gate("#29020 BLOCKED cccccccccc graphite FAILING_REQUIRED")]
    landing_pass(shell, config)
    merge_go(shell, "29020", "merge #29020 at ccccccc")
    landing_pass(shell, config)
    assert [post_body(post) for post in blockers(shell) if post["to"] != "root"] == [f"#29020 {head}: CI is red: buildkite/test"]


def test_on_a_board_surface_a_conflict_alone_is_one_blocker_per_base_whatever_the_head(shell, config, tmp_path):
    board_surface(config)
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/1", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.queue["29020"] = "not queued"
    shell.pr_status["29020"] = {"verdict": "blocked:conflict,ci-pending"}
    landing_pass(shell, config)
    shell.rows[0] = dict(shell.rows[0], head="dddd444444", reported_head="dddd444444")
    landing_pass(shell, config)
    assert [post_body(post) for post in blockers(shell) if post["to"] != "root"] == [f"#29020 cccc333333: {runner_module.CONFLICT_BLOCKER}"]
    shell.rows[0] = dict(shell.rows[0], base="a/0")
    landing_pass(shell, config)
    assert [post_body(post) for post in blockers(shell) if post["to"] != "root"][1:] == [f"#29020 dddd444444: {runner_module.CONFLICT_BLOCKER}"]


def test_on_a_board_surface_no_check_or_enqueue_names_a_pr_without_a_root_go_at_its_head(shell, config, tmp_path):
    board_surface(config)
    shell.rows = [
        {"pr": "29020", "lane": LANE, "branch": "a/1", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "29031", "lane": LANE, "branch": "a/2", "base": "a/1", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
        {"pr": "29100", "lane": LANE, "branch": "b/1", "base": "dev", "head": "eeee555555", "state": "open", "reported_head": "eeee555555"},
        {"pr": "29101", "lane": LANE, "branch": "b/2", "base": "b/1", "head": "ffff666666", "state": "open", "reported_head": "ffff666666"},
    ]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", "#29031 GREEN dddd444444 graphite READY", would="#29020 #29031"), gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    for pr, head in (("29020", "cccc333"), ("29101", "ffff666")):
        grade(shell, pr, f"ok F307 at {head}.")
        merge_go(shell, pr, f"merge #{pr} at {head}")
    merge_go(shell, "29100", "merge #29100 at bbbb222")
    shell.pr_status |= {"29031": {"verdict": "blocked:ci-pending"}, "29100": {"verdict": "blocked:ci-pending"}}
    landing_pass(shell, config)
    assert stack_named(shell) == {"29020"}
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]
    assert escalations(shell) == [ungone_parents("29100", "#29101 at ffff666666", "#29100 at eeee555555")]


def ungone_parents(bottom: str, heads: str, parents: str) -> str:
    return f"BELOW-BLOCKED above #{bottom}: root gos name {heads}, stacked on {parents} with no go at that head, so none of them is enqueued, and the stack below #{bottom} lands without them; the owner taps each parent's merge row and root posts `go #<parent> at <head>`"


def notes(shell: FakeShell) -> list[dict]:
    return [post for post in shell.posts if post["kind"] == "note"]


def two_pr_stack(shell: FakeShell) -> None:
    shell.rows = [
        {"pr": "29020", "lane": LANE, "branch": "a/1", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "29031", "lane": LANE, "branch": "a/2", "base": "a/1", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
    ]
    shell.gates["29031"] = [gate("#29020 GREEN cccc333333 graphite READY", "#29031 GREEN dddd444444 graphite READY", would="#29020 #29031")]
    shell.enqueue_out["29031"] = (0, "enqueue #29020 #29031: {}\n")
    for pr, head in (("29020", "cccc333"), ("29031", "dddd444")):
        grade(shell, pr, f"ok F307 at {head}.")


@pytest.mark.parametrize(
    "passing",
    [
        "p5-pass-2 owns #29020 #29031: post your own claim now. Finders for #29020 cccc333 and #29031 dddd444 are running",
        "merge #29031 at dddd444: it sits above #29020 cccc333, which waits for his fresh tap",
        "withdrawn: go for #29020 at cccc333 (#131549)",
    ],
)
def test_on_a_board_surface_a_root_go_naming_a_parent_in_passing_never_sweeps_it_into_the_batch(shell, config, tmp_path, passing):
    board_surface(config)
    two_pr_stack(shell)
    merge_go(shell, "29031", "merge #29031 at dddd444")
    root_go(shell, passing, shell.now())
    landing_pass(shell, config)
    assert stack_named(shell) == set() and shell.enqueues() == []
    assert ungone_parents("29020", "#29031 at dddd444444", "#29020 at cccc333333") in [post_body(post) for post in notes(shell) if post["to"] == "root"]
    merge_go(shell, "29020", "GO #29020 AT CCCC333 and #29031 at dddd444 (owner taps 3:10 PM)")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29031", "--partial"]]


def test_the_parent_tap_blocker_posts_once_per_set_of_parents_without_a_go_whatever_their_heads(shell, config, tmp_path):
    board_surface(config)
    two_pr_stack(shell)
    shell.board_rows = [("29020", "cccc333333"), ("29020", "eeee555555"), ("29031", "dddd444444")]
    merge_go(shell, "29031", "merge #29031 at dddd444")
    landing_pass(shell, config)
    shell.rows[0] = dict(shell.rows[0], head="eeee555555", reported_head="eeee555555")
    shell.gates["29031"] = [gate("#29020 GREEN eeee555555 graphite READY", "#29031 GREEN dddd444444 graphite READY", would="#29020 #29031")]
    landing_pass(shell, config)
    assert [post_body(post) for post in notes(shell) if post["to"] == "root"] == [ungone_parents("29020", "#29031 at dddd444444", "#29020 at cccc333333")]


def test_gos_on_several_rows_of_one_stack_above_untapped_parents_post_one_note_for_the_stack(shell, config, tmp_path):
    board_surface(config)
    shell.rows = [
        {"pr": "29020", "lane": LANE, "branch": "a/1", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "29031", "lane": LANE, "branch": "a/2", "base": "a/1", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
        {"pr": "29042", "lane": LANE, "branch": "a/3", "base": "a/2", "head": "eeee555555", "state": "open", "reported_head": "eeee555555"},
        {"pr": "29053", "lane": LANE, "branch": "a/4", "base": "a/3", "head": "ffff666666", "state": "open", "reported_head": "ffff666666"},
    ]
    for pr, head in (("29042", "eeee555"), ("29053", "ffff666")):
        merge_go(shell, pr, f"merge #{pr} at {head}")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [post_body(post) for post in notes(shell) if post["to"] == "root"] == [ungone_parents("29020", "#29042 at eeee555555 and #29053 at ffff666666", "#29020 at cccc333333 and #29031 at dddd444444")]
    assert not [post for post in blockers(shell) if post["to"] == "root" and "root gos name" in post_body(post)]


@pytest.mark.parametrize(("first", "lands"), [("carried", True), ("plain", False), ("withdrawn", False), ("behavior", False), ("aside", False), ("root-withdraw", False)])
def test_a_root_go_carries_along_the_fidelity_desks_carried_grades_to_the_replayed_head(shell, config, tmp_path, first, lands):
    merge_mode(config, "every")
    gone, replayed, head = "aaaa111111", "bbbb222222", "cccc333333"
    shell.rows = [{"pr": "33001", "lane": LANE, "branch": "c/1", "base": "dev", "head": head, "state": "open", "reported_head": head}]
    merge_go(shell, "33001", f"go #33001 at {gone[:7]}")
    seq = grade(shell, "33001", {"plain": f"ok {replayed[:7]} at the replayed head", "behavior": f"ok {replayed[:7]} carried from {gone[:7]} plus two commits, graded alone above", "aside": f"ok {replayed[:7]} carried from {gone[:7]} plus one commit, new behavior. Its sibling PR is test-only."}.get(first, f"ok {replayed[:7]} carried from {gone[:7]} (patch-identical replay)"))
    if first == "withdrawn":
        shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": "fidelity-desk", "kind": "withdraw", "re": seq, "to": ["root"], "refs": {"prs": [33001]}, "text": "withdrawn: not patch-identical", "at": actions.stamp(shell.clock)})
    if first == "root-withdraw":
        shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": "root", "kind": "withdraw", "re": 1, "to": ["desk-runner"], "refs": {"prs": [33001]}, "text": "withdraw any go on #33001: the range adds behavior", "at": actions.stamp(shell.clock)})
    grade(shell, "33001", f"ok {head} carried from {replayed[:7]} plus one test-only commit")
    shell.gates["33001"] = [gate(f"#33001 GREEN {head} graphite READY", would="#33001")]
    shell.enqueue_out["33001"] = (0, "enqueue #33001: {}\n")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == ([["33001", "--partial"]] if lands else [])


@pytest.mark.parametrize(
    "words",
    [
        "ok cccc333 carried from aaaa111 (patch-identical replay)",
        "ok cccc333 patch-identical to, with no root go at, aaaa111 (#143075), patch-identical (patch-id 6424ab2b7f99). Carried under root #131054.",
        "ok cccc333 carried: patch-identical to aaaa111 (ok #165915), on dev after the batch landed. A pure replay, so his 6:03 PM tap on aaaa111 carries under 399b5fa.",
        "ok cccc333, tap carries (399b5fa): patch-identical replay of aaaa111 onto the restacked #42810. old-app: unchanged vs c57cad26 (release 10435).",
    ],
    ids=["carried-from", "patch-identical-to", "carried-patch-identical", "tap-carries"],
)
@pytest.mark.parametrize(("old_app", "lands"), [(None, False), ("old-app: unchanged cccc333 go/sand/next only", True), ("old-app: CHANGES cccc333 the bundled server writes a new journal format", False)], ids=["ungraded", "unchanged", "changes"])
def test_with_the_old_app_check_a_root_go_carries_on_either_desks_carry_words_once_the_head_grades_old_app_unchanged(shell, config, tmp_path, words, old_app, lands):
    merge_mode(config, "every")
    old_app_checked(config)
    shell.rows = [{"pr": "33001", "lane": LANE, "branch": "c/1", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    merge_go(shell, "33001", "go #33001 at aaaa111")
    grade(shell, "33001", words)
    if old_app:
        shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": "fidelity-desk", "kind": "review", "to": ["root"], "refs": {"prs": [33001]}, "text": old_app, "at": actions.stamp(shell.clock)})
    shell.gates["33001"] = [gate("#33001 GREEN cccc333333 graphite READY", would="#33001")]
    shell.enqueue_out["33001"] = (0, "enqueue #33001: {}\n")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == ([["33001", "--partial"]] if lands else [])


def test_a_newer_old_app_change_a_head_record_binds_to_the_carried_head_stops_the_carry(shell, config, tmp_path):
    merge_mode(config, "every")
    old_app_checked(config)
    shell.rows = [{"pr": "33001", "lane": LANE, "branch": "c/1", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    merge_go(shell, "33001", "go #33001 at aaaa111")
    grade(shell, "33001", "ok cccc333 carried from aaaa111 (patch-identical replay)")
    for lane, kind, text in (("fidelity-desk", "review", "old-app: unchanged cccc333"), (LANE, "head", "#33001 cccc333333"), ("fidelity-desk", "review", "old-app: CHANGES the retry button moves")):
        shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": lane, "kind": kind, "to": ["root"], "refs": {"prs": [33001]}, "text": text, "at": actions.stamp(shell.clock)})
    shell.gates["33001"] = [gate("#33001 GREEN cccc333333 graphite READY", would="#33001")]
    shell.enqueue_out["33001"] = (0, "enqueue #33001: {}\n")
    landing_pass(shell, config)
    assert shell.enqueues() == []


def test_a_withdrawn_root_go_lets_nothing_merge(shell, config, tmp_path):
    board_surface(config)
    two_pr_stack(shell)
    merge_go(shell, "29031", "merge #29031 at dddd444")
    merge_go(shell, "29020", "merge #29020 at cccc333")
    seq = shell.records[-1]["seq"]
    cci_record(shell, lane="root", kind="withdraw", re=seq, text=f"withdrawn: #{seq}, the owner never tapped this head", to=["desk-runner"], at=actions.stamp(shell.clock))
    landing_pass(shell, config)
    assert shell.enqueues() == []


@pytest.mark.parametrize(
    "void",
    [
        lambda seq: {"kind": "decide", "text": "WITHDRAWN: the go for #29020 at cccc333 named a rebase, not a review fix"},
        lambda seq: {"kind": "decide", "resolves": seq, "text": "that go was a rebase, not a review fix"},
        lambda seq: {"kind": "withdraw", "resolves": [seq - 1, seq], "text": "both gos named a rebase"},
    ],
)
def test_a_go_a_later_root_record_resolves_or_opens_withdrawn_on_lets_nothing_merge(shell, config, tmp_path, void):
    board_surface(config)
    two_pr_stack(shell)
    merge_go(shell, "29031", "merge #29031 at dddd444")
    merge_go(shell, "29020", "merge #29020 at cccc333")
    cci_record(shell, lane="root", to=["desk-runner"], at=actions.stamp(shell.clock), **void(shell.records[-1]["seq"]))
    landing_pass(shell, config)
    assert shell.enqueues() == []


@pytest.mark.parametrize("mode", ["every", "auto"])
def test_a_reserved_touch_puts_the_row_on_the_merge_card_and_waits_past_the_go_for_the_authorized_tap(shell, config, tmp_path, mode):
    merge_mode(config, mode)
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333")
    shell.check_pr["29020"] = (1, RESERVED)
    landing_pass(shell, config)
    assert shell.enqueues() == [] and blockers(shell) == []
    [line] = escalations(shell)
    assert line.startswith("MERGE-ASK merge-ask:29020 #29020: #29020 cccc333 waits on the owner's tap on `merge:29020:cccc333` on merge card cci #")
    assert f"(merge mode {mode}); only the tap authorizes reserved/user-data" in line
    assert ' It touches your rule "User data stays where it is." Merging also allows it.\n\n' in shell.pr_cards[0]["card"]["children"][0]["md"]
    assert line.endswith("; only the tap authorizes reserved/user-data: `rulings.py authorize --drive d1 --row reserved/user-data --action <tap ts>`")
    shell.clock += timedelta(minutes=1)
    merge_go(shell, "29020", "merge #29020 at cccc333")
    landing_pass(shell, config)
    assert shell.enqueues() == [] and len(escalations(shell)) == 1
    shell.check_pr["29020"] = (0, "")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and len(escalations(shell)) == 1


def test_a_go_that_predates_a_reserved_touch_still_sends_the_card_naming_the_row_and_a_later_go_authorizes_nothing(shell, config, tmp_path):
    merge_mode(config, "every")
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333")
    merge_go(shell, "29020", "merge #29020 at cccc333")
    shell.clock += timedelta(minutes=1)
    shell.check_pr["29020"] = (1, RESERVED)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == []
    [line] = escalations(shell)
    assert line.startswith("MERGE-ASK merge-ask:29020 #29020:") and len(shell.pr_cards) == 1
    assert 'It touches your rule "User data stays where it is."' in shell.pr_cards[0]["card"]["children"][0]["md"]
    shell.clock += timedelta(minutes=1)
    merge_go(shell, "29020", "merge #29020 at cccc333")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    shell.check_pr["29020"] = (0, "")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


def test_a_go_naming_the_reserved_key_authorizes_nothing(shell, config, tmp_path):
    merge_mode(config, "every")
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333")
    shell.check_pr["29020"] = (1, RESERVED)
    shell.pr_card_error = "ci slack: SSM GetParameter: RequestID: dea32ce6-c6ad-4b7d-91b7-6bc348555b24"
    merge_go(shell, "29020", "merge #29020 at cccc333")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    merge_go(shell, "29020", "merge #29020 at cccc333, reserved/user-data allowed")
    landing_pass(shell, config)
    assert shell.enqueues() == []


@pytest.mark.parametrize("mode", ["every", "high-risk"])
def test_a_repository_the_runner_account_owns_enqueues_green_with_no_card_unless_it_touches_a_reserved_row(shell, config, tmp_path, mode):
    merge_mode(config, mode)
    shell.viewer = "forge-ai"
    lone_green_pr(shell)
    shell.files["29020"] = ["api/migrations/0399_run_owner.sql"]
    grade(shell, "29020", "ok cccc333")
    shell.check_pr["29020"] = (1, RESERVED)
    landing_pass(shell, config)
    assert shell.enqueues() == [] and [line.split(" ", 1)[0] for line in escalations(shell)] == ["MERGE-ASK"]
    shell.check_pr["29020"] = (0, "")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and len(escalations(shell)) == 1


def test_a_merge_card_is_edited_in_place_when_its_question_changes_and_never_posted_twice(shell, config, tmp_path):
    merge_mode(config, "every")
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert len(shell.pr_cards) == 1
    shell.check_pr["29020"] = (1, RESERVED)
    config.with_name(f"{config.stem}.check-pr.json").unlink()
    landing_pass(shell, config)
    landing_pass(shell, config)
    first, second = shell.pr_cards
    assert second["card"]["id"] == first["card"]["id"] == "merge:29020:cccc333" and "User data stays where it is." in second["card"]["children"][0]["md"]
    assert [line for line in escalations(shell) if line.startswith("MERGE-ASK")][-1].count("on merge card cci #900002") == 1


def test_a_merge_card_that_does_not_post_warns_and_posts_on_the_next_pass(shell, config, tmp_path):
    merge_mode(config, "every")
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333")
    shell.pr_card_error = "slack: channel_not_found, RequestID: dea32ce6-c6ad-4b7d-91b7-6bc348555b24"
    landing_pass(shell, config)
    shell.pr_card_error = "slack: channel_not_found, RequestID: 69b50a14-6da9-4805-9189-2f39cc06d691"
    landing_pass(shell, config)
    assert shell.enqueues() == []
    [line] = escalations(shell)
    assert line.startswith("MERGE-CARD-FAILED merge-card:29020:cccc333:") and "the merge card for #29020 at cccc333 did not post: slack: channel_not_found" in line
    shell.pr_card_error = ""
    landing_pass(shell, config)
    assert [line.split(" ", 1)[0] for line in escalations(shell)] == ["MERGE-CARD-FAILED", "MERGE-ASK"]


def test_a_board_with_no_snapshot_record_posts_no_merge_card_and_says_what_to_open(shell, config, tmp_path):
    merge_mode(config, "every")
    shell.no_snapshot = True
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333")
    landing_pass(shell, config)
    assert shell.enqueues() == [] and shell.pr_cards == []
    [line] = escalations(shell)
    assert line.startswith("MERGE-CARD-FAILED merge-card:unset landing:") and "open the root board" in line


def test_the_runner_carries_a_tap_and_asks_a_card_with_board_write_records_and_runs_no_cc_present(shell, config, tmp_path):
    board_surface(config)
    board_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    shell.diffs["aaaa111111"] = shell.diffs["cccc333333"] = "diff --git a/x.go b/x.go\n+same\n"
    lane_head_record(shell, "29020", "HEAD #29020 cccc333333 (was aaaa111111): restack onto dev, range-diff patch-identical (3 commits)")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [(carry["--session"], carry["--block"], carry["--row"], carry["--head"]) for carry in shell.carries] == [("s-root", "merge-rows", "29020", "cccc333333")]
    assert shell.board["merge-rows"]["29020"]["carriedFrom"] == "aaaa111111"
    assert [call for call in shell.calls if call[0] == "cc-present"] == []


SKIP_BUILD = {"jobs": [{"name": ":pipeline: Render and upload the pipeline"}, {"name": ":graphite: Redundant within its stack"}]}
FULL_BUILD = {"jobs": [{"name": ":pipeline: Render and upload the pipeline"}, {"name": ":go: Run sand tests"}]}


def built(shell: FakeShell, sha: str, number: str, build: dict) -> None:
    shell.builds[number] = build | {"state": "passed", "commit": sha, "finished_at": "2026-10-01T18:00:00Z"}
    shell.commit_statuses[sha] = [{"context": "buildkite/test", "state": "success", "target_url": f"https://buildkite.com/forge/test/builds/{number}"}]


GRADE = """tools/ci bk api -X POST -d '{"commit":"{commit}","branch":"{branch}","ignore_pipeline_branch_filters":true}' /pipelines/{pipeline}/builds"""


def with_ci_build(config: Path, grade: str = "") -> None:
    raw = json.loads(config.read_text())
    raw["landing"]["ci_build"] = "tools/ci bk api /pipelines/{pipeline}/builds/{number}"
    if grade:
        raw["landing"]["ci_grade"] = grade
    config.write_text(json.dumps(raw))


def blocker_bodies(shell: FakeShell) -> list[str]:
    return [call[call.index("--body") + 1] for call in shell.sends()] + [post_body(post) for post in blockers(shell)]


def test_a_green_head_whose_build_is_a_redundant_skip_is_unproven_until_a_full_build_on_the_stacks_tip_contains_it(shell, config, tmp_path):
    with_ci_build(config)
    shell.launch(LANE, "ctx_a")
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #28997 #29016 #29020: {}\n")
    for sha, number in (("aaaa111111", "78197"), ("bbbb222222", "78200"), ("cccc333333", "78214")):
        built(shell, sha, number, SKIP_BUILD)
    landing_pass(shell, config)
    assert shell.enqueues() == []
    bodies = [call[call.index("--body") + 1] for call in shell.sends()]
    assert any("#29020 cccc333333: unproven: redundant skip, no child in the ledger, so no full test build proves cccc333333" in body for body in bodies)
    built(shell, "cccc333333", "78286", FULL_BUILD)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


def status(pipeline: str, number: str, state: str = "success") -> dict:
    return {"context": f"buildkite/{pipeline}", "state": state, "target_url": f"https://buildkite.com/forge/{pipeline}/builds/{number}"}


def finished(build: dict, commit: str, state: str = "passed") -> dict:
    return build | {"state": state, "commit": commit, "finished_at": "2026-10-01T18:00:00Z"}


def test_a_head_whose_own_build_failed_is_unproven_whatever_build_covers_it(shell, config, tmp_path):
    with_ci_build(config)
    shell.launch(LANE, "ctx_a")
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    shell.commit_statuses["cccc333333"] = [status("test", "78299", "failure")]
    shell.builds["78299"] = finished(FULL_BUILD, "cccc333333", "failed")
    shell.builds["78301"] = finished(FULL_BUILD, "ffff666666")
    cci_record(shell, lane=LANE, kind="note", to=["root"], at=actions.stamp(shell.now()), text="full run green 78301 (ffff666, 84 jobs): #29020.")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    bodies = [call[call.index("--body") + 1] for call in shell.sends()]
    assert any("#29020 cccc333333: unproven: its own build https://buildkite.com/forge/test/builds/78299 failed at cccc333333" in body for body in bodies)


def test_only_a_full_build_of_the_skipped_pipeline_on_the_stacks_tip_covers_the_heads_below_it(shell, config, tmp_path):
    with_ci_build(config)
    shell.launch(LANE, "ctx_a")
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #28997 #29016 #29020: {}\n")
    for sha, number in (("aaaa111111", "78197"), ("bbbb222222", "78200")):
        built(shell, sha, number, SKIP_BUILD)
    shell.commit_statuses["cccc333333"] = [status("test", "78214"), status("release-pr-check", "29750")]
    shell.builds["78214"] = finished(SKIP_BUILD, "cccc333333")
    shell.builds["29750"] = finished(FULL_BUILD, "cccc333333")
    shell.builds["78301"] = finished(FULL_BUILD, "cccc333333")
    cci_record(shell, lane=LANE, kind="note", to=["root"], at=actions.stamp(shell.now()), text="full run green 78301 (cccc333, 84 jobs): #29020.")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    bodies = [call[call.index("--body") + 1] for call in shell.sends()]
    assert not any("#29020 cccc333333: unproven" in body for body in bodies)


def test_a_redundant_skip_head_is_proven_by_a_full_run_its_lane_reports_green_on_a_commit_containing_it(shell, config, tmp_path):
    with_ci_build(config)
    shell.launch(LANE, "ctx_a")
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    built(shell, "cccc333333", "78197", SKIP_BUILD)
    landing_pass(shell, config)
    assert shell.enqueues() == []
    cci_record(shell, lane=LANE, kind="note", to=["root"], at=actions.stamp(shell.now()), text="full run green 78301 (ffff666, 84 jobs): #28997 #29020. #29031 moved to a new head.")
    shell.builds["78301"] = FULL_BUILD | {"state": "passed", "commit": "ffff666666", "finished_at": "2026-10-01T18:00:00Z"}
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


def test_a_redundant_skip_head_no_tip_build_covers_gets_one_full_build_on_a_grade_branch_and_is_green_once_it_passes(shell, config, tmp_path):
    with_ci_build(config, GRADE)
    shell.launch(LANE, "ctx_a")
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    built(shell, "cccc333333", "78197", SKIP_BUILD)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == []
    assert shell.grade_refs == {"ci-grade/29020": "cccc333333"}
    assert shell.grade_starts == [{"commit": "cccc333333", "branch": "ci-grade/29020", "ignore_pipeline_branch_filters": True, "path": "/pipelines/test/builds"}]
    assert any("#29020 cccc333333: unproven: redundant skip, no child in the ledger, so full build https://buildkite.com/forge/test/builds/78400 grades cccc333333" in body for body in blocker_bodies(shell))
    shell.builds["78400"] = finished(FULL_BUILD, "cccc333333", "failed")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    shell.builds["78400"] = finished(FULL_BUILD, "cccc333333")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


def test_a_head_ccx_reads_unproven_gets_its_full_build_and_blocker_without_a_check_and_is_checked_once_proven(shell, config, tmp_path):
    with_ci_build(config, GRADE)
    shell.launch(LANE, "ctx_a")
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    built(shell, "cccc333333", "78197", SKIP_BUILD)
    shell.pr_status["29020"] = {"queue": "not queued", "verdict": "blocked:ci-unproven"}
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert checks(shell) == [] and shell.grade_refs == {"ci-grade/29020": "cccc333333"}
    assert any("#29020 cccc333333: unproven: redundant skip, no child in the ledger, so full build https://buildkite.com/forge/test/builds/78400 grades cccc333333" in body for body in blocker_bodies(shell))
    shell.builds["78400"] = finished(FULL_BUILD, "cccc333333")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


@pytest.mark.parametrize(
    ("status", "body", "said"),
    [
        ({"verdict": "blocked:ci-red", "ci": {"state": "red", "rollup": "ERROR"}}, "", "CI is red: ERROR"),
        ({"verdict": "blocked:ci-red,changes-requested", "ci": {"state": "red", "failing": ["buildkite/test"]}, "approval": {"state": "changes-requested", "changes_requested_by": ["poetic-svc"]}}, "**Red by design, per owner tap b61a053.** The rows fail until the fix lands.", "changes requested by poetic-svc"),
    ],
)
def test_a_red_pr_left_unchecked_hears_what_its_status_entry_asks(shell, config, tmp_path, status, body, said):
    shell.rows = single_row()
    shell.bodies["29020"] = body
    shell.pr_status["29020"] = {"queue": "not queued"} | status
    landing_pass(shell, config)
    assert checks(shell) == []
    assert [post_body(post) for post in blockers(shell) if post["to"] != "root"] == [f"#29020 cccc333333: {said}"]


def build_reads(shell: FakeShell, number: str) -> list[list[str]]:
    return [call for call in shell.calls if call[:3] == [str(shell.root / "checkout/tools/ci"), "bk", "api"] and call[3].endswith(f"/builds/{number}")]


def test_a_passed_build_is_read_once_and_a_new_runner_reads_it_from_the_kept_copy(shell, config, tmp_path):
    """Oct 29: the 0.7.580 landing pass waited 247 s on 192 `ci bk api` reads, every finished build read again by the runner each release re-executes."""
    with_ci_build(config)
    shell.launch(LANE, "ctx_a")
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    built(shell, "cccc333333", "78197", SKIP_BUILD)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert len(build_reads(shell, "78197")) == 1 and shell.enqueues() == []


def test_a_failed_build_is_never_kept_so_a_retried_job_that_passes_reads_green(shell, config, tmp_path):
    with_ci_build(config)
    shell.launch(LANE, "ctx_a")
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    shell.commit_statuses["cccc333333"] = [status("test", "78299", "failure")]
    shell.builds["78299"] = finished(FULL_BUILD, "cccc333333", "failed")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    shell.commit_statuses["cccc333333"] = [status("test", "78299")]
    shell.builds["78299"] = finished(FULL_BUILD, "cccc333333")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


def test_an_open_heads_builds_come_from_the_passs_one_status_query_with_no_rest_status_read(shell, config, tmp_path):
    """Oct 29: the 0.7.584 landing pass waited 125 s on 87 `gh api` reads, one commit status read per head its single GraphQL query had already read."""
    with_ci_build(config)
    shell.launch(LANE, "ctx_a")
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    built(shell, "cccc333333", "78197", SKIP_BUILD)
    landing_pass(shell, config)
    assert len(build_reads(shell, "78197")) == 1
    assert not [call for call in shell.calls if call[:2] == ["gh", "api"] and "/commits/" in call[2]]


def test_whether_a_commit_contains_another_is_read_once_per_pair(shell, config, tmp_path):
    landing = landing_of(shell, config, tmp_path)
    assert landing.contains("ffff666666", "cccc333333") and landing.contains("ffff666666", "cccc333333")
    assert len([call for call in shell.calls if call[:2] == ["gh", "api"] and call[2].endswith("/compare/cccc333333...ffff666666")]) == 1


def test_no_full_build_starts_below_a_tip_whose_own_build_is_still_running(shell, config, tmp_path):
    with_ci_build(config, GRADE)
    shell.launch(LANE, "ctx_a")
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020")]
    grade_gated_heads_ok(shell)
    for sha, number in (("aaaa111111", "78197"), ("bbbb222222", "78200")):
        built(shell, sha, number, SKIP_BUILD)
    shell.commit_statuses["cccc333333"] = [status("test", "78214", "pending")]
    shell.builds["78214"] = {"state": "running", "commit": "cccc333333", "jobs": FULL_BUILD["jobs"]}
    landing_pass(shell, config)
    assert shell.grade_starts == [] and shell.enqueues() == []
    assert any("#28997 aaaa111111: unproven: redundant skip, child #29020 open, so no full test build proves aaaa111111" in body for body in blocker_bodies(shell))


def test_a_full_build_that_does_not_start_escalates_once_per_head_and_is_tried_again(shell, config, tmp_path):
    with_ci_build(config, GRADE)
    shell.launch(LANE, "ctx_a")
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    built(shell, "cccc333333", "78197", SKIP_BUILD)
    shell.grade_error = "buildkite: 403 Forbidden"
    landing_pass(shell, config)
    landing_pass(shell, config)
    [failed] = [line for line in escalations(shell) if line.startswith("CI-GRADE-FAILED ci-grade:29020@cccc333333")]
    assert "could not start a full test build at cccc333333 on ci-grade/29020: buildkite: 403 Forbidden" in failed
    assert len(shell.grade_starts) == 2
    shell.grade_error = ""
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert len(shell.grade_starts) == 3


def test_the_landing_desk_refuses_to_start_without_a_merge_mode(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    del raw["landing"]["merge_mode"]
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)
    assert cli(shell, config, "run", "--desk", "landing", "--once") == 3
    assert shell.enqueues() == []
    [line] = escalations(shell)
    assert line.startswith("MERGE-MODE-UNSET ") and "names no landing.merge_mode of every, high-risk, auto" in line


def test_a_config_without_a_release_cli_is_refused_with_every_missing_member_in_one_line(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    del raw["landing"]["release_cli"]
    del raw["landing"]["adapter"]
    config.write_text(json.dumps(raw))
    cli_path = tmp_path / "checkout" / "tools" / "ci"
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("")
    lone_green_pr(shell)
    assert cli(shell, config, "run", "--desk", "landing", "--once") == 3
    assert shell.enqueues() == []
    [line] = escalations(shell)
    assert line.startswith("MQ-CONFIG-INVALID ") and "names no landing.adapter.kind" in line and "names no landing.release_cli" in line
    assert line.endswith(': "adapter": {"kind": "label"}, "release_cli": "tools/ci release"')


def test_the_release_cli_resolves_its_first_word_under_the_landing_checkout(tmp_path):
    assert runner_module.release_cli(tmp_path / "checkout", {"release_cli": "bin/deploy --profile 'prod east'"}) == [str(tmp_path / "checkout/bin/deploy"), "--profile", "prod east"]


def test_a_fidelity_lane_added_to_runner_json_counts_without_a_restart(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.", lane="parity-desk")
    landing = landing_of(shell, config, tmp_path)
    landing.run()
    assert shell.enqueues() == []
    raw = json.loads(config.read_text())
    raw["landing"]["fidelity_lane"] = ["fidelity-desk", "parity-desk"]
    config.write_text(json.dumps(raw))
    landing.run()
    assert len(shell.enqueues()) == 1


def test_every_lane_a_fidelity_list_names_can_grade_a_head(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    raw["landing"]["fidelity_lane"] = ["fidelity-desk", "fidelity-desk-7"]
    config.write_text(json.dumps(raw))
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020")]
    shell.enqueue_out["29020"] = (0, "enqueue #28997 #29016 #29020: {}\n")
    shell.gates["29016"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", would="#28997 #29016")]
    shell.enqueue_out["29016"] = (0, "enqueue #28997 #29016: {}\n")
    grade(shell, "28997", "ok F307 at aaaa111.")
    grade(shell, "29016", "ok F307 at bbbb222.", lane="fidelity-desk-7")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29016", "--partial"]]
    [blocker] = blockers(shell)
    assert blocker["to"].endswith(",fidelity-desk,fidelity-desk-7")
    assert "no review from fidelity-desk or fidelity-desk-7" in blocker["text"]
    grade(shell, "29020", "ok F307 at cccc333.", lane="fidelity-desk-7")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29016", "--partial"], ["29020", "--partial"]]


def test_the_newest_grade_across_a_fidelity_list_decides_the_head(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    raw["landing"]["fidelity_lane"] = ["fidelity-desk", "fidelity-desk-7"]
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    seq = grade(shell, "29020", "missing F335 at cccc333.", lane="fidelity-desk-7")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    [blocker] = blockers(shell)
    assert blocker["text"] == f"#29020 cccc333333: nothing in #29020 was enqueued; fidelity-desk-7 graded this head on cci #{seq}: fidelity: missing F335 at cccc333."


def test_a_status_read_github_refuses_blocks_the_head_and_is_escalated_once(shell, config, tmp_path):
    lone_green_pr(shell)
    shell.status_read_error = "gh: API rate limit exceeded (HTTP 403)"
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == []
    assert [line.split(" ", 1)[0] for line in escalations(shell)] == ["STATUS-READ-FAILED"]


def test_one_github_blip_across_many_heads_logs_one_status_read_record_with_the_count(shell, config, tmp_path):
    prs = [str(29100 + n) for n in range(5)]
    root_hold(shell, *map(int, prs), text="every PR waits for owner review")
    shell.rows = [{"pr": pr, "lane": LANE, "branch": f"h/{pr}", "base": "dev", "head": f"{pr}abcd", "state": "open", "reported_head": f"{pr}abcd"} for pr in prs]
    shell.gates = {pr: [gate(f"#{pr} BLOCKED {pr}abcd held")] for pr in prs}
    shell.status_read_error = 'Post "https://api.github.com/graphql": net/http: TLS handshake timeout'
    landing_pass(shell, config)
    landing_pass(shell, config)
    [line] = escalations(shell)
    assert line.startswith("STATUS-READ-FAILED status-read:") and "TLS handshake timeout, for 5 head(s): " + " ".join(f"#{pr} {pr}abcd" for pr in prs) in line

def graphql_reads(shell: FakeShell) -> list[list[str]]:
    return [call for call in shell.calls if call[:3] == ["gh", "api", "graphql"]]


def github_calls(shell: FakeShell) -> list[list[str]]:
    return [call for call in shell.calls if call[0] == "gh" or call[:4] == ["ccx", "vcs", "pr", "status"] or Path(call[0]).name == "stack-enqueue" or (len(call) > 1 and Path(call[1]).name == "ledger.py" and call[4] != "list")]


def landing_of(shell: FakeShell, config: Path, tmp_path: Path) -> runner_module.Landing:
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    runner_module.seed_policy(runner)
    return runner_module.Landing(runner, runner.config.landing)


def test_a_pass_fetches_the_trunk_while_the_ledger_refreshes(shell, config):
    lone_green_pr(shell)
    refreshing, overlapped = threading.Event(), []
    run = shell.run

    def watched(argv, stdin=None, env=None):
        if len(argv) > 4 and Path(argv[1]).name == "ledger.py" and argv[4] == "refresh":
            refreshing.set()
        if argv[:2] == ["git", "-C"] and argv[3:4] == ["fetch"]:
            overlapped.append(refreshing.wait(5))
        return run(argv, stdin, env)

    shell.run = watched
    landing_pass(shell, config)
    assert overlapped == [True]


def test_a_checkout_that_cannot_fast_forward_gates_nothing_until_it_can(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    shell.advance_error = "fatal: Not possible to fast-forward, aborting."
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == [] and not [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]
    assert [line.split(" ", 1)[0] for line in escalations(shell)] == ["CHECKOUT-STALE"]
    shell.advance_error = ""
    shell.calls.clear()
    landing_pass(shell, config)
    names = [call[3] if call[0] == "git" else Path(call[0]).name for call in shell.calls if call[0] == "git" or Path(call[0]).name == "stack-enqueue"]
    assert names[:3] == ["fetch", "worktree", "merge"] and "stack-enqueue" in names[3:]


@pytest.mark.parametrize(("mirror", "found"), [(None, "was not a registered worktree of its repository"), ("0" * 40, "was on a branch with no commit")])
def test_a_checkout_git_lost_or_left_unborn_is_rebuilt_at_the_trunk_head_and_the_pass_goes_on(shell, config, mirror, found):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    shell.mirror = mirror
    landing_pass(shell, config)
    checkout = str(shell.root / "checkout")
    rebuilt = [call[3:] if call[:3] == ["git", "-C", checkout] else call[:1] for call in shell.calls if call[:3] == ["git", "-C", checkout] or call[0] == "cp"]
    repair = [["rev-parse", "--verify", "-q", "FETCH_HEAD^{commit}"]]
    if mirror is None:
        staged = rebuilt[5][-2]
        assert Path(staged).name == "checkout" and Path(staged).parent.parent == shell.root
        index = ["rev-parse", "--path-format=absolute", "--git-path", "index"]
        repair += [["symbolic-ref", "-q", "--short", "HEAD"], index, ["worktree", "add", "-q", "--no-checkout", "-B", "runner/drive", staged, "cccc333"], ["cp"], ["worktree", "repair", checkout], index, ["cp"]]
        assert [call for call in shell.calls if call[0] == "cp"][1] == ["cp", f"{shell.root}/admin-3/index", f"{shell.root}/admin-4/index"]
    assert rebuilt[:len(repair) + 3] == [["fetch", "-q", "--no-tags", "--refmap=", "origin", "refs/heads/dev"], ["worktree", "list", "--porcelain"], *repair, ["reset", "-q", "--hard", "cccc333"]]
    assert [line.split(" ", 1)[0] for line in escalations(shell)] == ["MIRROR-REBUILT"] and found in escalations(shell)[0]
    assert [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]
    assert len(shell.enqueues()) == 1


def test_a_pass_reads_every_tracked_head_in_one_graphql_query(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    [read] = graphql_reads(shell)
    assert "cccc333333" in read[4]
    assert not [call for call in shell.calls if call[:2] == ["gh", "api"] and "/commits/" in call[2]]
    assert len(shell.enqueues()) == 1


def test_a_head_is_read_again_only_after_a_minute(shell, config, tmp_path):
    landing = landing_of(shell, config, tmp_path)
    green, red = {"context": "ci", "state": "success", "description": ""}, {"context": "ci", "state": "failure", "description": "Build #2 failed"}
    shell.combined_statuses["cccc333333"] = [green]
    assert landing.head_statuses("29020", "cccc333333") == [green]
    shell.clock += timedelta(seconds=59)
    assert landing.head_statuses("29020", "cccc333") == [green]
    assert len(graphql_reads(shell)) == 1
    shell.clock += timedelta(seconds=1)
    shell.combined_statuses["cccc333333"] = [red]
    assert landing.head_statuses("29020", "cccc333333") == [red]
    assert len(graphql_reads(shell)) == 2


@pytest.mark.parametrize("bucket", ["core", "graphql"])
def test_a_read_quota_under_the_gateway_floor_reads_nothing_until_its_reset_and_keeps_the_accepted_enqueue(shell, config, tmp_path, bucket):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_of(shell, config, tmp_path).accept(["29020"], ["29020"], {"29020": {"sha": "cccc333333"}}, {"29020": {"ours": True, "lane": LANE}}, [])
    shell.calls.clear()
    pool(shell, "reads", bucket)["remaining"] = runner_module.QUOTA_FLOOR - 1
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert github_calls(shell) == []
    [action] = enqueues_of(tmp_path).values()
    assert action.status == "accepted"
    assert [line for line in escalations(shell) if line.startswith("GITHUB-BACKOFF")] == [
        f"GITHUB-BACKOFF github-backoff:app-{bucket}:1790892000 landing: GitHub's app {bucket} quota has 999 of {pool(shell, 'reads', bucket)['limit']} calls left, under ccx's 1000-call floor; the desk reads nothing from GitHub until it resets at 3:00 PM, and accepted enqueues and root go records run after the reset"
    ]
    pool(shell, "reads", bucket)["remaining"] = pool(shell, "reads", bucket)["limit"]
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


def test_a_write_quota_under_the_floor_backs_nothing_off(shell, config):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    pool(shell, "writes", "graphql")["remaining"] = runner_module.QUOTA_FLOOR - 1
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]
    assert not [line for line in escalations(shell) if line.startswith("GITHUB-BACKOFF")]


def quota_alerts(shell: FakeShell) -> list[tuple[str, str]]:
    return [(post["kind"], post["text"]) for post in shell.posts if post["to"] == "root" and post["text"].startswith("GITHUB-QUOTA-LOW")]


def test_each_quota_under_1500_posts_one_urgent_alert_per_reset_naming_its_five_heaviest_callers(shell, config):
    lone_green_pr(shell)
    graphql = pool(shell, "writes", "graphql")
    graphql["remaining"] = 1499
    graphql["callers"] = [{"caller": name, "requests": requests} for name, requests in (("cc-review", 900), ("desk-runner", 300), ("board_rows", 120), ("label-watch", 60), ("ledger", 40), ("rulings", 20))]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert quota_alerts(shell) == [
        ("urgent", "GITHUB-QUOTA-LOW github-quota:user-graphql:1790892000 github: the user graphql quota has 1499 of 5000 calls left and resets at 3:00 PM; top callers: cc-review 900, desk-runner 300, board_rows 120, label-watch 60, ledger 40"),
    ]
    graphql["resetAt"] = "2026-10-01T23:00:00Z"
    landing_pass(shell, config)
    assert len(quota_alerts(shell)) == 2 and "resets at 4:00 PM" in quota_alerts(shell)[1][1]


def test_a_quota_read_that_fails_is_reported_once_an_hour_and_the_pass_runs(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    shell.quota_error = "ccx: GitHub unreachable"
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]
    assert [line.split(" ", 1)[0] for line in escalations(shell)] == ["QUOTA-READ-FAILED"]


def test_a_reserved_touch_found_after_the_enqueue_was_accepted_enqueues_nothing(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333")
    landing_of(shell, config, tmp_path).accept(["29020"], ["29020"], {"29020": {"sha": "cccc333333"}}, {"29020": {"ours": True, "lane": LANE}}, [])
    shell.check_pr["29020"] = (1, RESERVED)
    config.with_name(f"{config.stem}.check-pr.json").unlink()
    landing = landing_of(shell, config, tmp_path)
    landing.enqueue()
    landing.drain()
    assert shell.enqueues() == []
    [action] = enqueues_of(tmp_path).values()
    assert action.response["outcome"] == "blocked" and action.response["out"].startswith("the merge-mode check refuses #29020: merge mode auto waits for the owner's Merge answer")


def test_a_head_regraded_missing_after_its_enqueue_was_accepted_enqueues_nothing(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    runner_module.seed_policy(runner)
    landing = runner_module.Landing(runner, runner.config.landing)
    landing.accept(["29020"], ["29020"], {"29020": {"sha": "cccc333333"}}, {"29020": {"ours": True, "lane": LANE}}, [])
    seq = grade(shell, "29020", "missing F307 at cccc333.")
    landing.enqueue()
    landing.drain()
    assert shell.enqueues() == []
    [blocker] = blockers(shell)
    assert f"cci #{seq}: fidelity: missing F307" in blocker["text"]
    [action] = enqueues_of(tmp_path).values()
    assert action.response["outcome"] == "blocked" and action.response["out"].startswith("the fidelity check refuses #29020: fidelity-desk graded this head on cci #")


def compare_calls(shell: FakeShell) -> list[str]:
    return [call[2].rsplit("/", 1)[1] for call in shell.calls if call[:2] == ["gh", "api"] and "/compare/" in call[2]]


def test_a_restack_with_the_graded_heads_patch_carries_its_ok_and_cites_that_head(shell, config, tmp_path):
    lone_green_pr(shell)
    seq = grade(shell, "29020", "ok F307 at bbbb222.")
    shell.diffs["bbbb222"] = shell.diffs["cccc333333"] = "diff --git a/sealed.rs b/sealed.rs\n+vfs_lazy::open_read_only\n"
    finder_verdict(shell, "29020", "ok cccc333 no findings")
    carried = landing_of(shell, config, tmp_path).fidelity("29020", "cccc333333")
    assert (carried.ok, carried.record["seq"], carried.why) == (True, seq, "fidelity: ok at cccc333333, patch-identical to bbbb222")
    shell.calls.clear()
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and blockers(shell) == [] and escalations(shell) == []
    assert compare_calls(shell) == ["cccc333333~1...cccc333333", "bbbb222~1...bbbb222"]


def test_a_restacked_head_with_its_own_commits_patch_identical_carries_both_fidelity_and_finder(shell, config, tmp_path):
    lone_green_pr(shell)
    shell.pr_commits["29020"] = 3
    seq = grade(shell, "29020", "ok F307 at bbbb222.")
    shell.diffs["bbbb222"] = shell.diffs["cccc333333"] = "diff --git a/sealed.rs b/sealed.rs\n+vfs_lazy::open_read_only\n"
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and blockers(shell) == []
    assert set(compare_calls(shell)) == {"cccc333333~3...cccc333333", "bbbb222~3...bbbb222"}
    [action] = enqueues_of(tmp_path).values()
    assert action.action_id.startswith("enqueue:29020@cccc333333")
    assert landing_of(shell, config, tmp_path).fidelity("29020", "cccc333333").record["seq"] == seq


def test_a_restack_whose_patch_differs_waits_for_its_own_grade(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at bbbb222.")
    shell.diffs["bbbb222"] = "diff --git a/sealed.rs b/sealed.rs\n+vfs_lazy::open_read_only\n"
    shell.diffs["cccc333333"] = "diff --git a/sealed.rs b/sealed.rs\n+Connection::open_with_flags\n"
    landing_pass(shell, config)
    assert shell.enqueues() == []
    [blocker] = blockers(shell)
    assert "no review from fidelity-desk starting `fidelity: ok` names this head" in blocker["text"]


def test_a_grade_naming_the_new_head_beats_a_carried_ok(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at bbbb222.")
    seq = grade(shell, "29020", "missing F335 at cccc333: the restack dropped the fixture.")
    shell.diffs["bbbb222"] = shell.diffs["cccc333333"] = "diff --git a/x b/x\n+same\n"
    landing_pass(shell, config)
    assert shell.enqueues() == [] and compare_calls(shell) == []
    [blocker] = blockers(shell)
    assert f"cci #{seq}: fidelity: missing F335" in blocker["text"]


def test_no_ok_carries_from_a_head_whose_newest_grade_is_missing(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at bbbb222.")
    grade(shell, "29020", "missing F335 at bbbb222.")
    shell.diffs["bbbb222"] = shell.diffs["cccc333333"] = "diff --git a/x b/x\n+same\n"
    landing_pass(shell, config)
    assert shell.enqueues() == [] and compare_calls(shell) == []


def test_a_short_sha_that_is_no_commit_carries_nothing_and_escalates_nothing(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok 9057dda: the restack onto dev has a range-diff of '='.")
    shell.compare_errors["9057dda"] = "gh: Not Found (HTTP 404)"
    shell.diffs["cccc333333"] = "diff --git a/x b/x\n+same\n"
    landing_pass(shell, config)
    assert shell.enqueues() == [] and escalations(shell) == []
    assert compare_calls(shell) == ["cccc333333~1...cccc333333", "9057dda~1...9057dda"]


def test_a_register_key_a_grade_lists_after_its_head_is_never_probed_as_a_head(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok bbbb222 checked rule/d968e1d,rule/1068167 names grade window|hourly pass")
    finder_verdict(shell, "29020", "ok cccc333 no findings")
    shell.diffs["bbbb222"] = shell.diffs["cccc333333"] = "diff --git a/x b/x\n+same\n"
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1 and escalations(shell) == []
    assert compare_calls(shell) == ["cccc333333~1...cccc333333", "bbbb222~1...bbbb222"]


def test_the_whole_stack_policy_never_cuts_the_prefix_at_a_missing_grade(shell, config, tmp_path):
    four_pr_stack(shell)
    assert cli(shell, config, "policy", "--key", "L300", "--landing", "whole", "--revision", "f9e8d7c6b5", "--supersedes", "a1b2c3d4e5", "--source", "owner ruling") == 0
    grade(shell, "42590", "ok F307 at aaaa111.")
    grade(shell, "42615", "ok F307 at bbbb222.")
    grade(shell, "42634", "missing F335 at dddd444: the sealed read is not folded in.")
    grade(shell, "42620", "ok F307 at eeee555.")
    landing_pass(shell, config)
    assert shell.enqueues() == [] and enqueues_of(tmp_path) == {}
    [blocker] = blockers(shell)
    assert blocker["text"].startswith("#42634 dddd444444: nothing in #42590 #42615 #42634 #42620 was enqueued; ")


def test_a_diff_carrying_terminal_escape_sequences_reads_and_carries_the_ok(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at bbbb222.")
    shell.diffs["bbbb222"] = shell.diffs["cccc333333"] = 'diff --git a/log.go b/log.go\n+const reset = "\x1b[0m"\n'
    finder_verdict(shell, "29020", "ok cccc333 no findings")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]
    assert [line for line in escalations(shell) if "PATCH-READ-FAILED" in line] == [] and blockers(shell) == []


def test_an_unreadable_head_diff_carries_nothing_and_escalates_once(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at bbbb222.")
    shell.compare_errors["cccc333333"] = "gh: API rate limit exceeded (HTTP 403)"
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == []
    assert [line for line in escalations(shell) if "PATCH-READ-FAILED" in line] == [
        "PATCH-READ-FAILED patch:29020:cccc333333 #29020: GitHub refused the diff of cccc333333~1...cccc333333: gh: API rate limit exceeded (HTTP 403); no fidelity ok carries to this head until it reads, and the runner reads it again on the next pass"
    ]


def test_a_head_with_no_finder_record_is_refused_finder_pending_once(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333", finder=False)
    finder_verdict(shell, "29020", "ok bbbb222 no findings")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == []
    [blocker] = blockers(shell)
    assert blocker["to"] == f"{LANE},finder-desk" and blocker["topic"] == "29020"
    assert blocker["text"] == f"#29020 cccc333333: nothing in #29020 was enqueued; FINDER-PENDING #29020: no `finder:` record from finder-desk names this head, and a lane's own finder record never grades it"


def test_a_finder_pending_head_with_no_pass_waiting_queues_one_and_again_once_a_pass_skipped_it_while_closed(shell, config, tmp_path):
    finder_config(config)
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333", finder=False)

    def passes() -> list[tuple[str, str]]:
        return [(action.action_id, action.status) for action in incident(tmp_path, "desk-finder").actions.values()]

    landing_pass(shell, config)
    landing_pass(shell, config)
    assert passes() == [("finder:29020:cccc333333", "accepted")]
    orca_pass(shell, config)
    [job] = shell.finder_jobs
    assert json.loads((job / "job.json").read_text())["head"] == "cccc333333"
    (job / "result.json").write_text(json.dumps({"skipped": "Forge-AI/monorepo#29020 is closed"}))
    orca_pass(shell, config)
    landing_pass(shell, config)
    assert passes() == [("finder:29020:cccc333333", "verified"), ("finder:29020:cccc333333#2", "accepted")]


@pytest.mark.parametrize(("opened", "lane"), [("port-memory-3", "port-memory-3"), ("", LANE)], ids=["opened-lane", "no-record"])
def test_a_finder_pending_pass_is_addressed_to_the_lane_that_opened_the_pr_not_its_worktree(shell, config, tmp_path, opened, lane):
    finder_config(config)
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333", finder=False)
    if opened:
        head_record(shell, "29020", "cccc333333", lane=opened, kind="opened", text="OPENED #29020 head cccc333")
    landing_pass(shell, config)
    orca_pass(shell, config)
    [job] = shell.finder_jobs
    assert json.loads((job / "job.json").read_text())["lane"] == lane


PRECONDITION = "This lands only after the seed's read-back shows the pointer."


def said_in_body(shell: FakeShell) -> str:
    shell.bodies["29020"] = f"Reads the release pointer.\n\n{PRECONDITION} Readers ship first."
    return "the PR body"


def said_on_the_bus(shell: FakeShell) -> str:
    seq = 34766 + len(shell.records)
    shell.records.append({"seq": seq, "drive": "d1", "lane": LANE, "kind": "report", "to": ["root"], "refs": {"prs": [29020]}, "text": PRECONDITION})
    return f"cci #{seq}"


@pytest.mark.parametrize(
    ("text", "sentence"),
    [
        ("Reads the pointer. Waits for the card's answer before it lands.", "Waits for the card's answer before it lands."),
        ("Interim pin, not for landing as is.\nRe-pins later.", "Interim pin, not for landing as is."),
        ("Do not merge until the seed reads back! Then ship.", "Do not merge until the seed reads back!"),
        ('This lands only after "the seed. It reads back" holds. Then ship.', 'This lands only after "the seed. It reads back" holds.'),
        ('Ready. "worker" lands only after the seed.', '"worker" lands only after the seed.'),
    ],
)
def test_each_explicit_landing_order_phrase_reads_as_a_precondition(text, sentence):
    assert runner_module.precondition_sentences(text) == [sentence]


@pytest.mark.parametrize(
    "text",
    [
        'The phrase list matches the landing side\'s: "lands only after", "merge only after", "requires ... before", "do not land until". Each is checked.',
        "Rulings:\n- “it lands only after the seed. ok” (abc1234)",
        "Scans for `before it lands` and `not for landing`.",
        "```\nThis lands only after the seed.\n```",
        "> Do not merge until the seed reads back.\nQuoted from the plan.",
        "Scans for ``do not land until`` too.\n```\nDo not merge until the seed reads back.",
    ],
)
def test_a_text_that_cites_the_phrases_holds_no_landing(text):
    assert runner_module.precondition_sentences(text) == []


def precondition_notes(shell: FakeShell) -> list[dict]:
    return [post for post in shell.posts if post["kind"] == "note" and "WARNING landing precondition" in post["text"]]


@pytest.mark.parametrize("say", [said_in_body, said_on_the_bus])
def test_a_landing_precondition_warns_the_owning_lane_not_root_once_per_head_and_the_head_still_enqueues(shell, config, tmp_path, say):
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333")
    where = say(shell)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and blockers(shell) == []
    [note] = precondition_notes(shell)
    assert note["to"] == LANE
    assert note["text"].startswith(f'#29020 cccc333333: WARNING landing precondition: {where} says "{PRECONDITION}".')


@pytest.mark.parametrize(
    "body",
    [
        "Lands the release pointer reader. Readers come before the writer.",
        "A store the readers-first gate would refuse is past the chain head, and no shipped build has one.",
        "This lands after its parent in the stack.",
    ],
)
def test_a_head_with_no_landing_precondition_enqueues_with_no_warning(shell, config, tmp_path, body):
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333")
    shell.bodies["29020"] = body
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and blockers(shell) == [] and precondition_notes(shell) == []


def test_a_precondition_a_root_record_resolves_no_longer_warns(shell, config, tmp_path):
    """Root #194669: #42785 kept warning on #147074, #152592 and a sentence naming hold #134863 after root #193831 resolved all three."""
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333")
    stated = said_on_the_bus(shell)
    seq = 34766 + len(shell.records)
    shell.records.append({"seq": seq, "drive": "d1", "lane": LANE, "kind": "report", "to": ["root"], "refs": {"prs": [29020]}, "text": "Hold #134863 stands: the conversion lands only after the reader release is live."})
    shell.records.append({"seq": seq + 1, "drive": "d1", "lane": "root", "kind": "decide", "to": [LANE], "refs": {"prs": [29020]}, "resolves": [int(stated.removeprefix("cci #")), 134863], "text": "Stale preconditions on #29020 cleared."})
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and precondition_notes(shell) == []


def test_a_restack_with_the_finder_ok_heads_patch_carries_the_finder_ok(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333", finder=False)
    finder_verdict(shell, "29020", "ok bbbb222 no findings")
    shell.diffs["bbbb222"] = shell.diffs["cccc333333"] = "diff --git a/sealed.rs b/sealed.rs\n+vfs_lazy::open_read_only\n"
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and blockers(shell) == []


@pytest.mark.parametrize(
    ("carry", "enqueued"),
    [
        ("ok cccc333 carried from bbbb222 (#137368). range-diff: commit 1 differs only in one context line", True),
        ("ok cccc333 carried from bbbb222. The fix adds new behavior: the retry now reads the sealed copy", False),
    ],
)
def test_a_fidelity_carry_carries_the_earlier_heads_finder_ok(shell, config, tmp_path, carry, enqueued):
    lone_green_pr(shell)
    grade(shell, "29020", carry, finder=False)
    finder_verdict(shell, "29020", "ok bbbb222 no findings")
    shell.diffs["bbbb222"] = "diff --git a/sealed.rs b/sealed.rs\n+vfs_lazy::open\n"
    shell.diffs["cccc333333"] = "diff --git a/sealed.rs b/sealed.rs\n+vfs_lazy::open_read_only\n"
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == ([["29020", "--partial"]] if enqueued else [])
    assert any("FINDER-PENDING #29020" in blocker["text"] for blocker in blockers(shell)) is not enqueued


def test_a_withdrawn_fidelity_carry_carries_no_finder_ok(shell, config, tmp_path):
    lone_green_pr(shell)
    carry = grade(shell, "29020", "ok cccc333 carried from bbbb222 (#137368).", finder=False)
    shell.records.append({"seq": carry + 1, "drive": "d1", "lane": "fidelity-desk", "kind": "withdraw", "to": ["root"], "refs": {"prs": [29020]}, "re": carry, "text": "withdraw: the carry misread the range-diff", "at": actions.stamp(shell.clock)})
    finder_verdict(shell, "29020", "ok bbbb222 no findings")
    shell.diffs["bbbb222"] = "diff --git a/sealed.rs b/sealed.rs\n+vfs_lazy::open\n"
    shell.diffs["cccc333333"] = "diff --git a/sealed.rs b/sealed.rs\n+vfs_lazy::open_read_only\n"
    landing_pass(shell, config)
    assert shell.enqueues() == []


def test_an_open_finder_major_holds_the_head_until_a_finder_pass_answers_it(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333", finder=False)
    seq = finder_verdict(shell, "29020", "major cccc333 1 open: go/a.go:12 the retry drops the sealed read")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    [blocker] = blockers(shell)
    assert f"FINDER-PENDING #29020: finder-desk posted on cci #{seq}: finder: major cccc333 1 open: go/a.go:12" in blocker["text"]
    finder_verdict(shell, "29020", "ok cccc333 answered: the PR rebuts it with the sealed-read test")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and len(blockers(shell)) == 1


@pytest.mark.parametrize("settled", ["decision", "go", "resolves", "also-resolves"])
def test_a_root_decision_answering_a_finder_major_or_a_later_root_go_lets_the_head_enqueue(shell, config, tmp_path, settled):
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333", finder=False)
    seq = finder_verdict(shell, "29020", "major cccc333 1 open: go/a.go:12 the migration test seeds write version 1")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    if settled == "decision":
        shell.records.append({"seq": seq + 1, "drive": "d1", "lane": LANE, "kind": "ask", "to": ["root"], "re": seq, "refs": {"prs": [29020]}, "text": "a test gap, not a defect"})
        shell.records.append({"seq": seq + 2, "drive": "d1", "lane": "root", "kind": "decision", "to": [LANE], "re": seq + 1, "refs": {}, "text": "land it; the test gap is a follow-up PR"})
    elif settled == "resolves":
        shell.records.append({"seq": seq + 1, "drive": "d1", "lane": "root", "kind": "decide", "to": [LANE], "refs": {}, "resolves": [seq], "text": "the migration seed is a test gap; land it"})
    elif settled == "also-resolves":
        shell.records.append({"seq": seq + 1, "drive": "d1", "lane": "root", "kind": "decide", "to": [LANE], "refs": {}, "resolves": seq - 1, "also_resolves": [seq], "text": "Settled, root #201709: the migration seed is a test gap; land it"})
    else:
        merge_go(shell, "29020", "merge #29020 at cccc333")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


def test_a_finder_major_after_a_missing_finder_record_posts_its_own_blocker(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333", finder=False)
    landing_pass(shell, config)
    seq = finder_verdict(shell, "29020", "major cccc333 1 open: go/a.go:12 the retry drops the sealed read")
    landing_pass(shell, config)
    first, second = blockers(shell)
    assert "no `finder:` record from finder-desk names this head" in first["text"] and f"posted on cci #{seq}: finder: major cccc333" in second["text"]


@pytest.mark.parametrize(
    ("lane", "text", "enqueued"),
    [
        (LANE, "api-fix REVIEW reply to finder 202370 and fidelity 202341 on #29020 bbbb222; new HEAD cccc333.", False),
        (LANE, "sol finder on #29020: no majors; 1 minor (3 rustfmt layouts) fixed in cccc333", False),
        (LANE, "finder: ok cccc333 no majors", False),
        ("finder-desk", "sol finder over whole cccc333: no majors", False),
        ("finder-desk", "finder: ok cccc333 sol finder over whole: no majors", True),
        ("finder-desk-2", "finder: ok cccc333 sol finder over whole: no majors", True),
    ],
    ids=["lane-reply-naming-the-finder", "lane-plain-words", "lane-finder-line", "desk-plain-words", "desk-finder-line", "desk-successor"],
)
def test_only_a_finder_desk_finder_line_grades_a_head_and_a_lane_reply_naming_the_finder_draws_no_note(shell, config, tmp_path, lane, text, enqueued):
    """Oct 30 1:03 AM, root #202594: the runner read port-memory-3's REVIEW reply #202493, which names finder record 202370, as a finder record and asked the lane for a verdict (#202508)."""
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333", finder=False)
    shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": LANE, "kind": "head", "to": [], "refs": {"prs": ["Forge-AI/monorepo#29020"]}, "text": "a/3 cccc333333"})
    shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": lane, "kind": "review", "to": ["finder-desk", "desk-runner"], "refs": {"prs": ["Forge-AI/monorepo#29020"]}, "text": text})
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert bool(shell.enqueues()) is enqueued
    assert not [post for post in shell.posts if post["kind"] == "note" and "names the finder" in post["text"]]


def test_a_check_pr_killed_at_its_bound_warns_once_that_the_rulings_are_unproven_and_holds_nothing(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333")
    shell.check_pr["29020"] = (runner_module.TIMED_OUT, "")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and blockers(shell) == []
    [note] = [post for post in shell.posts if post["kind"] == "note"]
    assert note["text"].endswith(f"CHECK-PR-TIMEOUT it ran past {runner_module.CHECK_PR_SECONDS}s and was killed, so the rulings at cccc333333 are unproven this pass")

@pytest.mark.parametrize(
    "out",
    [
        "RULINGS-UNCITED rule/abc1234\nTEST-REMOVED TestAnOpenOldChatReadsItsRows\nCONTRACT-MISSING lane-a: the grade #9 covers slice-hash 0; the lane's slice is now 1\n",
        "TESTS-CORPUS plan/B-tests.md sha256 differs from corpus/b-tests\n",
    ],
)
def test_a_head_check_pr_names_findings_for_is_enqueued_with_one_warning_naming_them(shell, config, tmp_path, out):
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333")
    shell.check_pr["29020"] = (1, out)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and blockers(shell) == [] and escalations(shell) == []
    [note] = [post for post in shell.posts if post["kind"] == "note"]
    assert note["to"] == LANE
    assert note["text"].endswith(f"WARNING `rulings.py check-pr` warns at cccc333333, and the head is not held for it: {'; '.join(out.splitlines())}")


@pytest.mark.parametrize(
    ("code", "out"),
    [
        (2, "usage: rulings.py: error: argument command: invalid choice: 'check-pr'\n"),
        (1, "SOMETHING-NEW rule/x\n"),
        (0, "BLOCKER gh pr view 29020 failed: HTTP 502\n"),
        (0, "RULINGS-UNCITED rule/abc1234 the body cites no answer\n"),
    ],
)
def test_a_check_pr_run_the_runner_cannot_read_passes_the_head_and_posts_nothing(shell, config, tmp_path, code, out):
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333")
    shell.check_pr["29020"] = (code, out)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1 and blockers(shell) == []
    assert escalations(shell) == []


def test_a_heads_rulings_check_runs_once_across_passes(shell, config, tmp_path):
    two_pr_stack(shell)
    shell.check_pr["29031"] = (1, "NAME-HIT rule/394dfd6 go/x.go:5 sync\n")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    runner_module.seed_policy(runner)
    landing = runner_module.Landing(runner, runner.config.landing)
    landing.run()
    landing.run()
    assert sorted(call[call.index("--pr") + 1] for call in shell.calls if "check-pr" in call) == ["Forge-AI/monorepo#29020", "Forge-AI/monorepo#29031"]


def test_a_heads_final_rulings_check_survives_a_runner_restart_and_an_unread_one_does_not(shell, config, tmp_path):
    two_pr_stack(shell)
    shell.check_pr["29020"] = (1, "NAME-HIT rule/394dfd6 go/x.go:5 sync\n")
    shell.check_pr["29031"] = (0, "BLOCKER the diff of acme/app#29031: git fetch exited 128\n")
    for _ in range(2):
        runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
        landing = runner_module.Landing(runner, runner.config.landing)
        assert landing.rulings_report("29020", "cccc333333") == ["NAME-HIT rule/394dfd6 go/x.go:5 sync"]
        landing.rulings_report("29031", "dddd444444")
    assert sorted(call[call.index("--pr") + 1] for call in shell.calls if "check-pr" in call) == ["Forge-AI/monorepo#29020", "Forge-AI/monorepo#29031", "Forge-AI/monorepo#29031"]


def test_kept_rulings_checks_outlive_a_release_that_changes_only_the_plumbing_rulings_reads_through(shell, config, tmp_path, monkeypatch):
    two_pr_stack(shell)
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    runner_module.Landing(runner, runner.config.landing).rulings_report("29020", "cccc333333")
    plumbing = tmp_path / "scripts"
    plumbing.mkdir()
    for path in runner_module.SCRIPTS.glob("*.py"):
        (plumbing / path.name).write_bytes(path.read_bytes() + (b"\n" if path.stem in ("ledger", "grades", "cci", "drive", "ghread") else b""))
    monkeypatch.setattr(runner_module, "SCRIPTS", plumbing)
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    runner_module.Landing(runner, runner.config.landing).rulings_report("29020", "cccc333333")
    assert len([call for call in shell.calls if "check-pr" in call]) == 1


def test_a_pass_reads_a_repeated_cci_query_once_until_the_runner_posts(shell, config, tmp_path):
    two_pr_stack(shell)
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    landing = runner_module.Landing(runner, runner.config.landing)
    runner.cci_tail.remember()

    def withdraw_reads() -> int:
        return len([call for call in shell.calls if call[:2] == ["cci", "tail"] and "--kind=withdraw" in call])

    for pr, head in (("29020", "cccc333333"), ("29031", "dddd444444")):
        landing.carried_by_fidelity(pr, head, lambda sha: False)
    assert withdraw_reads() == len(landing.fidelity_lanes)
    runner.cci_post("note", "a note", "root", "29020", "note:29020")
    landing.carried_by_fidelity("29020", "cccc333333", lambda sha: False)
    assert withdraw_reads() == 2 * len(landing.fidelity_lanes)


def test_old_app_grades_are_read_once_per_pr_whatever_heads_a_carried_go_tries(shell, config, tmp_path, monkeypatch):
    raw = json.loads(config.read_text())
    raw["landing"]["checks"] = ["old-app"]
    config.write_text(json.dumps(raw))
    two_pr_stack(shell)
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    landing = runner_module.Landing(runner, runner.config.landing)
    read: list[str] = []
    graded = runner_module.old_app_grades_in
    monkeypatch.setattr(runner_module, "old_app_grades_in", lambda found, drive, lanes, repo, pr: read.append(pr) or graded(found, drive, lanes, repo, pr))
    lands = landing.old_app_lands()
    for pr, head in (("29020", "cccc333333"), ("29020", "aaaa111111"), ("29031", "dddd444444"), ("29031", "bbbb222222")):
        assert not lands(pr, head)
    assert read == ["29020", "29031"]


def test_the_accept_checks_of_heads_no_go_names_run_in_parallel(shell, config, tmp_path, monkeypatch):
    two_pr_stack(shell)
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    landing = runner_module.Landing(runner, runner.config.landing)
    rows = landing.rows()
    landing.no_go = {"29020", "29031"}
    both = threading.Barrier(2, timeout=5)
    monkeypatch.setattr(runner_module.Landing, "failing", lambda self, stage, graded: both.wait() and None)
    landing.tell_unpassed(rows, [])
    assert not both.broken


def test_independent_stacks_enqueue_concurrently_with_one_queued_report_each(shell, config, tmp_path, monkeypatch):
    lone_green_pr(shell)
    shell.rows.append({"pr": "29040", "lane": LANE, "branch": "b/1", "base": "dev", "head": "eeee555555", "state": "open", "reported_head": "eeee555555"})
    shell.gates["29040"] = [gate("#29040 GREEN eeee555555 graphite READY", would="#29040")]
    shell.enqueue_out["29040"] = (0, "enqueue #29040: {}\n")
    for pr, sha in (("29020", "cccc333"), ("29040", "eeee555")):
        grade(shell, pr, f"ok {sha}")
    both = threading.Barrier(2, timeout=5)
    chains = runner_module.Landing.enqueue_chains

    def meets(self, tops, heads, whole):
        both.wait()
        return chains(self, tops, heads, whole)

    monkeypatch.setattr(runner_module.Landing, "enqueue_chains", meets)
    landing_pass(shell, config)
    assert not both.broken
    assert sorted(call[1:] for call in shell.enqueues()) == [["29020", "--partial"], ["29040", "--partial"]]
    assert sorted(post["text"].split(" at ")[0] for post in shell.posts if post["kind"] == "report" and post["text"].startswith("QUEUED ")) == ["QUEUED #29020", "QUEUED #29040"]


def test_a_slow_stack_check_holds_no_other_stack_out_of_the_queue(shell, config, tmp_path, monkeypatch):
    lone_green_pr(shell)
    shell.rows.append({"pr": "29040", "lane": LANE, "branch": "b/1", "base": "dev", "head": "eeee555555", "state": "open", "reported_head": "eeee555555"})
    shell.gates["29040"] = [gate("#29040 GREEN eeee555555 graphite READY", would="#29040")]
    shell.enqueue_out["29040"] = (0, "enqueue #29040: {}\n")
    for pr, sha in (("29020", "cccc333"), ("29040", "eeee555")):
        grade(shell, pr, f"ok {sha}")
    other_queued = threading.Event()
    waited: list[bool] = []
    stacks, chains = runner_module.Landing.proven_stacks, runner_module.Landing.enqueue_chains

    def slow(self, tips):
        if tips == ["29020"]:
            waited.append(other_queued.wait(5))
        return stacks(self, tips)

    def chained(self, tops, heads, whole):
        done = chains(self, tops, heads, whole)
        if tops == ["29040"]:
            other_queued.set()
        return done

    monkeypatch.setattr(runner_module.Landing, "proven_stacks", slow)
    monkeypatch.setattr(runner_module.Landing, "enqueue_chains", chained)
    landing_pass(shell, config)
    assert waited == [True]
    assert [call[1] for call in shell.enqueues()] == ["29040", "29020"]


def test_a_landable_stack_the_gate_never_settles_goes_straight_to_the_queue_five_minutes_on(shell, config, tmp_path):
    merge_mode(config, "every")
    gone_green(shell, "#33001 GREEN aaaa111111 graphite READY")
    shell.queue["33001"] = "not queued"
    shell.gates["33001"] = [(runner_module.TIMED_OUT, "", "stack-enqueue --check timed out after 60s and was killed")]
    overflow = landing_of(shell, config, tmp_path)
    landing_pass(shell, config)
    overflow.overflow()
    assert shell.enqueues() == []
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    overflow.overflow()
    landing_pass(shell, config)
    overflow.overflow()
    assert [call[1:] for call in shell.enqueues()] == [["33001", "--partial"]]
    queued = [post["text"] for post in shell.posts if post["kind"] == "report" and post["text"].startswith("QUEUED #33001 at aaaa111111")]
    assert len(queued) == 1 and "overflow put it straight in the merge queue" in queued[0]
    [action] = [action for action in enqueues_of(tmp_path).values() if f"#{runner_module.OVERFLOW}-" in action.action_id]
    assert action.deadline


def test_the_overflow_leaves_a_landable_stack_the_runner_refused_at_its_head(shell, config, tmp_path):
    tap_ruled_pr(shell, config, ["go/sand/next/vm/run_test.go", "go/sand/next/vm/run.go"])
    shell.rows = [dict(row, test_state="success") for row in shell.rows]
    shell.queue["29020"] = "not queued"
    grade(shell, "29020", TEST_ONLY_GRADE)
    merge_go(shell, "29020", "go test-only #29020 at cccc333")
    overflow = landing_of(shell, config, tmp_path)
    landing_pass(shell, config)
    overflow.overflow()
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    overflow.overflow()
    assert [post for post in blockers(shell) if "TEST-ONLY-MISMATCH" in post_body(post)] and shell.enqueues() == []


def test_the_overflow_never_enqueues_a_head_whose_go_was_withdrawn(shell, config, tmp_path):
    merge_mode(config, "every")
    gone_green(shell, "#33001 GREEN aaaa111111 graphite READY")
    shell.queue["33001"] = "not queued"
    shell.gates["33001"] = [(runner_module.TIMED_OUT, "", "stack-enqueue --check timed out after 60s and was killed")]
    go = shell.records[-1]["seq"]
    shell.records.append({"seq": go + 1, "drive": "d1", "lane": "root", "kind": "withdraw", "re": go, "to": ["desk-runner"], "refs": {"prs": [33001]}, "text": f"withdrawn: go #{go} for #33001", "at": actions.stamp(shell.clock)})
    overflow = landing_of(shell, config, tmp_path)
    overflow.overflow()
    shell.sleep(6 * 60)
    overflow.overflow()
    assert shell.enqueues() == []


def test_the_overflow_never_enqueues_a_stack_holding_a_pr_below_with_no_go(shell, config, tmp_path):
    merge_mode(config, "every")
    two_pr_stack(shell)
    shell.rows = [dict(row, test_state="success") for row in shell.rows]
    shell.queue["29020"] = shell.queue["29031"] = "not queued"
    shell.gates["29031"] = [(runner_module.TIMED_OUT, "", "stack-enqueue --check timed out after 60s and was killed")]
    merge_go(shell, "29031", "merge #29031 at dddd444")
    overflow = landing_of(shell, config, tmp_path)
    overflow.overflow()
    shell.sleep(6 * 60)
    overflow.overflow()
    assert shell.enqueues() == []


def test_the_overflow_puts_a_go_named_stack_in_whole_when_only_its_bottom_has_waited_five_minutes(shell, config, tmp_path):
    merge_mode(config, "every")
    two_pr_stack(shell)
    shell.rows = [dict(row, test_state="success") for row in shell.rows]
    shell.queue["29020"] = shell.queue["29031"] = "not queued"
    merge_go(shell, "29020", "merge #29020 at cccc333")
    overflow = landing_of(shell, config, tmp_path)
    overflow.overflow()
    shell.sleep(6 * 60)
    merge_go(shell, "29031", "merge #29031 at dddd444")
    overflow.overflow()
    assert [call[1:] for call in shell.enqueues()] == [["29031", "--partial"]]


def test_the_overflow_tries_a_blocked_enqueue_again_five_minutes_later(shell, config, tmp_path):
    merge_mode(config, "every")
    gone_green(shell, "#33001 GREEN aaaa111111 graphite READY")
    shell.queue["33001"] = "not queued"
    shell.enqueue_out["33001"] = (1, "#33001 BLOCKED aaaa111111 CI running (graphite RUNNING)\nenqueued nothing: the bottom PR #33001 is BLOCKED\n")
    overflow = landing_of(shell, config, tmp_path)
    overflow.overflow()
    shell.sleep(6 * 60)
    overflow.overflow()
    overflow.overflow()
    assert len(shell.enqueues()) == 1
    shell.enqueue_out["33001"] = (0, "enqueue #33001: {}\n")
    shell.sleep(6 * 60)
    overflow.overflow()
    shell.sleep(6 * 60)
    overflow.overflow()
    assert len(shell.enqueues()) == 2


def test_a_stack_worker_past_the_gate_budget_runs_on_after_the_pass_while_the_other_stack_lands(shell, config, tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "GATE_BUDGET", timedelta(seconds=1))
    lone_green_pr(shell)
    shell.rows.append({"pr": "29040", "lane": LANE, "branch": "b/1", "base": "dev", "head": "eeee555555", "state": "open", "reported_head": "eeee555555"})
    shell.gates["29040"] = [gate("#29040 GREEN eeee555555 graphite READY", would="#29040")]
    shell.enqueue_out["29040"] = (0, "enqueue #29040: {}\n")
    for pr, sha in (("29020", "cccc333"), ("29040", "eeee555")):
        grade(shell, pr, f"ok {sha}")
    release = threading.Event()
    stacks = runner_module.Landing.proven_stacks

    def stuck(self, tips):
        if tips == ["29020"]:
            release.wait(10)
        return stacks(self, tips)

    monkeypatch.setattr(runner_module.Landing, "proven_stacks", stuck)
    landing = landing_of(shell, config, tmp_path)
    landing.run()
    running = [future for _, future in landing.inflight.values() if not future.done()]
    enqueued = [call[1] for call in shell.enqueues()]
    steps = json.loads(config.with_name(f"{config.stem}.landing-stacks.json").read_text())["stacks"]
    release.set()
    landing.drain()
    assert len(running) == 1 and enqueued == ["29040"]
    assert [(entry["root"], entry["step"], entry["budget"]) for entry in steps.values()] == [("29020", "check", runner_module.STEP_BUDGETS["check"])]
    assert [call[1] for call in shell.enqueues()] == ["29040", "29020"]


def test_lanes_take_turns_so_one_lanes_many_stacks_never_go_ahead_of_every_other_lane():
    open_rows = {pr: {"lane": lane, "branch": f"b/{pr}", "base": "dev"} for pr, lane in (("1", "a"), ("2", "a"), ("3", "a"), ("4", "b"))}
    assert runner_module.lane_turns([["1"], ["2"], ["3"], ["4"]], open_rows) == [["1"], ["4"], ["2"], ["3"]]


def test_every_landing_pass_posts_one_state_line_to_root(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    landing_pass(shell, config)
    states = shell.states
    assert len(states) == 2 and all(post["to"] == "root" for post in states)
    assert re.fullmatch(r"LANDING queued \d+, waiting \d+, held 0, refused 0, in flight 0, overflowed 0 this hour; pass \d+s", states[-1]["text"])


def test_an_overflow_thread_that_finished_no_run_in_five_minutes_logs_overflow_stalled(shell, config, tmp_path):
    landing = landing_of(shell, config, tmp_path)
    landing.overflower = runner_module.Landing(landing.runner, landing.runner.config.landing)
    landing.overflower.overflowed_at -= 6 * 60
    landing.post_state(1.0)
    landing.runner.flush()
    [line] = [line for line in escalations(shell) if line.startswith("OVERFLOW-STALLED")]
    assert "the overflow thread has finished no run in 6 minutes and is dead" in line


@pytest.mark.parametrize("count", [0, 1, 20])
def test_landing_state_reads_one_incident_snapshot(shell, config, tmp_path, monkeypatch, count):
    landing = landing_of(shell, config, tmp_path)
    landing.ledger_rows = {str(index): {"state": "open", "head": f"{index:040x}", "test_state": "failure"} for index in range(1, count + 1)}
    landing.statuses = {"3": {"queue": runner_module.QUEUED_STATE}} if count >= 3 else {}
    landing.held_now = ["4"] if count >= 4 else []
    with landing.book.store.owned(runner_module.LANDING, 0) as current:
        for index in range(1, min(count, 2) + 1):
            current.accept(f"blocker:{index}:{index:040x}", "bus", json.dumps({"kind": "blocker", "pr": str(index)}), "owner", shell.now())
        current.accept("sample#overflow-1", "enqueue", "1", "owner", shell.now())
    decoded, opened = [], []
    load, read = actions.Incident.load, Path.read_text

    def counted(record):
        decoded.append(record["incident_id"])
        return load(record)

    def watched(path, *args, **kwargs):
        if path == landing.book.store.path(runner_module.LANDING):
            opened.append(path)
        return read(path, *args, **kwargs)

    monkeypatch.setattr(actions.Incident, "load", staticmethod(counted))
    monkeypatch.setattr(Path, "read_text", watched)
    landing.post_state(1.0)
    assert shell.states[-1]["text"] == f"LANDING queued {int(count >= 3)}, waiting 0, held {int(count >= 4)}, refused {min(count, 2)}, in flight 0, overflowed 1 this hour; pass 1s"
    assert decoded == [runner_module.LANDING]
    assert len(opened) == 1


@pytest.mark.parametrize("count", [0, 1, 2])
def test_landing_state_preserves_refusal_target_errors_and_short_circuit(shell, config, tmp_path, count):
    landing = landing_of(shell, config, tmp_path)
    landing.ledger_rows = {str(index): {"state": "open", "head": f"{index:040x}"} for index in range(1, count + 1)}
    with landing.book.store.owned(runner_module.LANDING, 0) as current:
        current.accept(f"blocker:1:{1:040x}", "bus", json.dumps({"kind": "blocker", "pr": "1"}), "owner", shell.now())
        current.accept("invalid-target", "bus", "{", "owner", shell.now())
    if count == 2:
        with pytest.raises(json.JSONDecodeError):
            landing.post_state(1.0)
    else:
        landing.post_state(1.0)
        assert f"refused {count}," in shell.states[-1]["text"]


def test_landing_refusal_gate_keeps_fresh_reads_and_empty_member_errors(shell, config, tmp_path):
    landing = landing_of(shell, config, tmp_path)
    rows = {"1": {"head": f"{1:040x}"}}
    assert not landing.refused_at_head({"1"}, rows)
    with landing.book.store.owned(runner_module.LANDING, 0) as current:
        current.accept(f"blocker:1:{1:040x}", "bus", json.dumps({"kind": "blocker", "pr": "1"}), "owner", shell.now())
        current.accept("invalid-target", "bus", "{", "owner", shell.now())
    assert landing.refused_at_head({"1"}, rows)
    with pytest.raises(json.JSONDecodeError):
        landing.refused_at_head(set(), rows)


def test_landing_state_reloads_actions_and_clock_on_each_report(shell, config, tmp_path):
    landing = landing_of(shell, config, tmp_path)
    landing.ledger_rows = {"1": {"state": "open", "head": f"{1:040x}"}}
    with landing.book.store.owned(runner_module.LANDING, 0) as current:
        current.accept("sample#overflow-1", "enqueue", "1", "owner", shell.now())
    landing.post_state(1.0)
    assert "refused 0, in flight 0, overflowed 1 this hour" in shell.states[-1]["text"]
    with landing.book.store.owned(runner_module.LANDING, 0) as current:
        current.accept(f"blocker:1:{1:040x}", "bus", json.dumps({"kind": "blocker", "pr": "1"}), "owner", shell.now())
    shell.sleep(3601)
    landing.post_state(2.0)
    assert "refused 1, in flight 0, overflowed 0 this hour; pass 2s" in shell.states[-1]["text"]


def test_a_read_that_fails_on_a_transient_network_error_runs_once_more_and_a_write_never_does(tmp_path, monkeypatch):
    bin_dir, tries = tmp_path / "bin", tmp_path / "tries"
    bin_dir.mkdir()
    cci = bin_dir / "cci"
    cci.write_text(f"#!/bin/sh\necho x >> {tries}\nif [ $(wc -l < {tries}) -lt 2 ]; then echo 'Connection reset by peer' >&2; exit 1; fi\necho ok\n")
    cci.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    read = runner_module.Shell().run(["cci", "tail", "--drive", "d1"])
    assert (read.code, read.out, tries.read_text().count("x")) == (0, "ok\n", 2)
    tries.unlink()
    write = runner_module.Shell().run(["cci", "post", "--drive", "d1", "--text", "hi"])
    assert (write.code, tries.read_text().count("x")) == (1, 1)


def test_heads_no_go_names_are_told_new_heads_first_within_the_pass_budget(shell, config, tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "GATE_WORKERS", 1)
    two_pr_stack(shell)
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    landing = runner_module.Landing(runner, runner.config.landing)
    rows = landing.rows()
    landing.no_go = {"29020", "29031"}
    told: list[str] = []

    def failing(self, stage, graded):
        told.extend(graded.heads)
        shell.clock += timedelta(seconds=61)

    monkeypatch.setattr(runner_module.Landing, "failing", failing)
    landing.tell_unpassed(rows, [])
    landing.tell_unpassed(rows, [])
    assert told == ["29020", "29031"]


def test_a_pass_reads_the_ledger_the_background_refresh_keeps_and_waits_on_no_refresh(shell, config, tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "REFRESH_EVERY", timedelta(hours=1))
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    runner_module.seed_policy(runner)
    landing = runner_module.Landing(runner, runner.config.landing)

    def refreshes() -> int:
        return len([call for call in list(shell.calls) if "refresh" in call])

    landing.keep_fresh()
    deadline = time.monotonic() + 5
    while not refreshes() and time.monotonic() < deadline:
        time.sleep(0.01)
    landing.run()
    assert refreshes() == 1 and [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


@pytest.mark.parametrize("page", [1, 25])
def test_the_queue_status_read_covers_every_open_row_in_pages(shell, config, tmp_path, monkeypatch, page):
    monkeypatch.setattr(runner_module, "STATUS_PAGE", page)
    two_pr_stack(shell)
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    landing = runner_module.Landing(runner, runner.config.landing)
    landing.rows()
    assert set(landing.pr_statuses()) == {"29020", "29031"}
    assert len([call for call in shell.calls if call[:4] == ["ccx", "vcs", "pr", "status"]]) == 2 // page + (2 % page > 0)


def test_a_check_pr_failure_whose_text_differs_only_in_numbers_posts_nothing(shell, config, tmp_path):
    """Oct 23: a fetch failure naming each run's pid in its temporary ref posted 30 identical RULINGS-UNREAD records to root in ten minutes."""
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333")
    for pid in (4101, 4102, 4103):
        shell.queue["29020"] = "not queued"
        shell.check_pr["29020"] = (0, f"BLOCKER the diff of acme/app#29020: git fetch origin +dev:refs/rulings/cccc333333-{pid} exited 128\n")
        landing_pass(shell, config)
    assert sum("check-pr" in call for call in shell.calls) >= 2
    assert escalations(shell) == []


def test_a_grade_posted_with_the_owner_repo_reference_counts_and_one_for_another_repo_does_not(shell, config, tmp_path):
    lone_green_pr(shell)
    finder_verdict(shell, "29020", "ok cccc333 no findings")
    shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": "fidelity-desk", "kind": "review", "to": ["root"], "refs": {"prs": ["yasyf/cc-skills#29020"]}, "text": "fidelity: ok cccc333"})
    landing_pass(shell, config)
    assert shell.enqueues() == []
    shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": "fidelity-desk", "kind": "review", "to": ["root"], "refs": {"prs": ["Forge-AI/monorepo#29020"]}, "text": "fidelity: ok cccc333"})
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]
    assert not [call for call in shell.calls if call[:2] == ["cci", "tail"] and "--kind=review" in call and "--pr" in call]


def test_a_fidelity_desk_ok_stands_over_a_pipeline_missing_at_the_same_head(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    raw["landing"]["fidelity_lane"] = ["fidelity-desk", "fidelity-pipeline"]
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)
    grade(shell, "29020", "missing cccc333 plan:112 the plan names no gallery field", lane="fidelity-pipeline")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    grade(shell, "29020", "ok cccc333 a line-for-line port of the old-tree commit")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


def test_a_missing_from_one_fidelity_lane_is_never_cleared_by_a_later_ok_from_another(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    raw["landing"]["fidelity_lane"] = ["fidelity-desk", "fidelity-desk-7"]
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)
    seq = grade(shell, "29020", "missing cccc333 scope/progress a second progress state is shown", lane="fidelity-desk-7")
    grade(shell, "29020", "ok cccc333 checked reserved/user-data,scope/progress names user data|progress bar")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    [blocker] = blockers(shell)
    assert f"fidelity-desk-7 graded this head on cci #{seq}: fidelity: missing cccc333 scope/progress" in blocker["text"]
    grade(shell, "29020", "ok cccc333", lane="fidelity-desk-7")
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1


def test_an_ok_counts_whatever_rows_its_checked_set_lists_and_the_register_gains_after_it(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok cccc333 checked reserved/user-data names user data")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and blockers(shell) == []


def test_a_cci_outage_ends_the_landing_pass_without_killing_the_runner(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    shell.tail_error = "cci: daemon unavailable"
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert stack_enqueue_calls(shell) == []
    assert escalations(shell) == ["CCI-TAIL-FAILED cci-tail:2026-10-01T18:0 runner: cci tail exited 1: cci: daemon unavailable"]
    shell.tail_error = ""
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1


def test_an_enqueue_whose_response_was_lost_settles_from_graphite_status(shell, config, tmp_path):
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", would="#28997")]
    grade_gated_heads_ok(shell)
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    runner_module.seed_policy(runner)
    landing = runner_module.Landing(runner, runner.config.landing)
    landing.accept("29020", ["28997"], {"28997": {"sha": "aaaa111111"}}, {"28997": {"ours": True}}, [])
    key = next(iter(store.load("desk-landing").actions.keys() - {"policy:a1b2c3d4e5"}))
    with store.owned("desk-landing", 0) as live:
        live.start(key, shell.now())
    shell.sleep(16 * 60)
    shell.statuses = "#28997 QUEUED_TO_MERGE aaaa111111 open review APPROVED on dev\n"
    landing_pass(shell, config)
    action = store.load("desk-landing").actions[key]
    assert (action.status, action.response["outcome"]) == ("completed", "enqueued")
    assert shell.enqueues() == []


def out_of_queue(shell: FakeShell, *prs: str, state: str = "not queued") -> None:
    shell.sleep(runner_module.STATUS_MAX_AGE.total_seconds() + 1)
    for pr in prs:
        shell.queue[pr] = state


def enqueues_of(tmp_path: Path) -> dict[str, actions.Action]:
    return {key: action for key, action in incident(tmp_path, "desk-landing").actions.items() if action.kind == "enqueue"}


def test_a_prefix_the_queue_dropped_at_its_admitted_head_is_enqueued_again(shell, config, tmp_path):
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1
    out_of_queue(shell, "29020")
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 2
    shell.queue["29020"] = "queued"
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 2
    first, second = (action for _, action in sorted(enqueues_of(tmp_path).items()))
    assert (first.response["outcome"], second.response["outcome"]) == ("dropped", "enqueued")
    assert first.response["drop"] == {"29020": {"kind": "", "detail": "graphite READY"}}
    assert escalations(shell) == [] and [post for post in shell.posts if post["kind"] == "blocker"] == []


def test_a_prefix_the_queue_dropped_for_a_conflict_goes_to_its_lane_as_a_blocker(shell, config, tmp_path):
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    landing_pass(shell, config)
    out_of_queue(shell, "29020", state="evicted")
    shell.gates["29020"] = [gate(CONFLICT_DROP)]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1
    blockers = [post for post in shell.posts if post["kind"] == "blocker"]
    assert len(blockers) == 1 and blockers[0]["to"] == LANE and blockers[0]["topic"] == "29020"
    assert "merge conflicts" in blockers[0]["text"] and "cccc333333" in blockers[0]["text"]
    assert [action.response["outcome"] for action in enqueues_of(tmp_path).values()] == ["ejected"]


CONFLICT_DROP = (
    "#29020 BLOCKED cccc333333 ejected from the merge queue Oct 13, 1:06 AM UTC because it had merge conflicts, and no head was pushed since; "
    "run ccx vcs stack submit from the branch's worktree: cd /w && ccx vcs stack submit"
)
OWN_RED = (
    "#29016 BLOCKED bbbb222222 ejected from the merge queue Oct 13, 1:06 AM UTC because Failed CI (test-unit), and no head was pushed since; "
    "test-unit reads failure on this head, so the red check is its own; "
    "read the failure on draft #39356 and push the fix as a new head before enqueueing again"
)


def test_an_enqueue_a_root_go_makes_is_judged_against_the_queue_only_once_a_status_read_postdates_it(shell, config, tmp_path):
    board_surface(config)
    two_pr_stack(shell)
    merge_go(shell, "29020", "merge #29020 at cccc333")
    merge_go(shell, "29031", "merge #29031 at dddd444")
    shell.queue["29020"] = shell.queue["29031"] = "not queued"
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [action.response["outcome"] for action in enqueues_of(tmp_path).values()] == ["enqueued"]
    out_of_queue(shell, "29020", "29031")
    landing_pass(shell, config)
    assert sorted(enqueues_of(tmp_path).items())[0][1].response["outcome"] == "dropped"


def test_a_red_check_drop_its_gate_blocks_waits_for_a_new_head(shell, config, tmp_path):
    shell.launch("artifact-retry", "ctx_a")
    shell.rows = [row for row in stack_rows() if row["pr"] != "29020"]
    shell.gates["29016"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", would="#28997 #29016")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29016"] = (0, "enqueue #28997 #29016: {}\n")
    landing_pass(shell, config)
    out_of_queue(shell, "29016")
    shell.gates["29016"] = [gate("#28997 QUEUED aaaa111111 graphite QUEUED_TO_MERGE", OWN_RED)]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1 and blockers(shell) == []
    assert [action.response["outcome"] for action in enqueues_of(tmp_path).values()] == ["dropped"]
    assert relayed(shell) == [f"#29016 bbbb222222: {OWN_RED.removeprefix('#29016 BLOCKED bbbb222222 ')}"]
    shell.rows[1] |= {"head": "bbbb333333", "reported_head": "bbbb333333"}
    shell.gates["29016"] = [gate("#28997 QUEUED aaaa111111 graphite QUEUED_TO_MERGE", "#29016 GREEN bbbb333333 graphite READY", would="#29016")]
    grade_gated_heads_ok(shell)
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 2


FOREIGN_RETRY = (
    "#29020 GREEN cccc333333 graphite READY; retries the same head after its ejection Oct 16, 8:04 PM UTC because downstack failures on PR "
    "#[39016](https://app.graphite.com/github/pr/Forge-AI/monorepo/39016), with no queue draft"
)


def root_go(shell: FakeShell, text: str, at: datetime) -> None:
    shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": "root", "kind": "go", "to": ["landing-desk"], "refs": {}, "text": text, "at": actions.stamp(at)})


def test_a_drop_blaming_a_pr_outside_the_stack_re_enqueues_the_same_head_on_the_next_root_go(shell, config, tmp_path):
    shell.rows = [ONE_ROW]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    root_go(shell, "GO #29020 at cccc3333 standard", shell.now() - timedelta(minutes=5))
    landing_pass(shell, config)
    out_of_queue(shell, "29020")
    shell.gates["29020"] = [gate(FOREIGN_RETRY, would="#29020")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1 and blockers(shell) == []
    [line] = escalations(shell)
    assert line.startswith("AWAITS-GO awaits-go:29020:cccc333333 #29020: cccc333333: the merge queue dropped it for downstack failures on #39016, outside its stack")
    [dropped] = enqueues_of(tmp_path).values()
    assert (dropped.response["outcome"], dropped.response["ejected"], dropped.response["awaiting_go"]) == ("dropped", {}, {"29020": "cccc333333"})
    shell.sleep(60)
    root_go(shell, "GO #29021 at eeee5555 standard", shell.now())
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1
    root_go(shell, "GO #29020 at cccc3333 standard; a head change is not required", shell.now())
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 2 and blockers(shell) == [] and len(escalations(shell)) == 1


def test_a_gate_ejection_blaming_a_pr_outside_the_stack_is_never_routed(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.rows = [ONE_ROW]
    shell.gates["29020"] = [
        gate(
            "#29020 BLOCKED cccc333333 ejected from the merge queue Oct 16, 8:04 PM UTC because downstack failures on PR "
            "#[39016](https://app.graphite.com/github/pr/Forge-AI/monorepo/39016), and no head was pushed since; "
            "run ccx vcs stack submit from the branch's worktree: cd /w && ccx vcs stack submit"
        )
    ]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.sends() == [] and shell.posts == []


def test_a_drop_blaming_a_pr_inside_the_stack_is_gated_again_and_re_enqueues_the_same_heads(shell, config, tmp_path):
    shell.rows = [row for row in stack_rows() if row["pr"] != "29020"]
    shell.gates["29016"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", would="#28997 #29016")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29016"] = (0, "enqueue #28997 #29016: {}\n")
    landing_pass(shell, config)
    out_of_queue(shell, "28997", "29016")
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 2 and blockers(shell) == [] and escalations(shell) == []
    assert sorted(action.response["outcome"] for action in enqueues_of(tmp_path).values()) == ["dropped", "enqueued"]


def test_a_red_check_drop_its_gate_reads_as_a_batch_mates_failure_re_enqueues_the_same_head(shell, config, tmp_path):
    shell.rows = [ONE_ROW]
    retry = (
        "#29020 GREEN cccc333333 graphite READY; retries the same head after its ejection Oct 16, 9:47 PM UTC because it was not satisfying all "
        "requirements (Failed CI (buildkite/test)), with queue draft #39356: draft #39356 also held #39302 outside this stack and "
        "buildkite/test reads success on this head"
    )
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    landing_pass(shell, config)
    out_of_queue(shell, "29020")
    shell.gates["29020"] = [gate(retry, would="#29020")]
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 2 and blockers(shell) == [] and escalations(shell) == []


def test_a_believed_queued_pr_whose_row_left_the_ledger_is_skipped(shell, config, tmp_path):
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    landing_pass(shell, config)
    shell.rows = [{"pr": "28349", "lane": "d4", "branch": "d/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"}]
    shell.gates["28349"] = [gate("#28349 BLOCKED dddd444444 held")]
    before = len(shell.calls)
    landing_pass(shell, config)
    assert not [call for call in shell.calls[before:] if call[:4] == ["ccx", "vcs", "pr", "status"] and "29020" in call]


def mq(shell: FakeShell, config: Path, *argv: str) -> int:
    return mq_module.main(["--config", str(config), *argv], shell)


QUIET_WHY = ["hold pass", "precondition pass", "rules-review pass", "rulings pass", "fidelity pass", "finder pass", "merge-mode pass", "approvals pass", "required-ci pass"]


@pytest.mark.parametrize(
    ("blocked", "line"),
    [
        ("hold", "hold fail open cci hold #{seq}"),
        ("fidelity", "fidelity fail no review from fidelity-desk starting `fidelity: ok` names this head"),
        ("finder", f"finder fail FINDER-PENDING #29020: no `finder:` record from finder-desk names this head, and a lane's own finder record never grades it"),
        ("rules-review", "rules-review fail rules-review holds cccc333333: its review is pending or carries a finding no `rules-override` waived"),
    ],
)
def test_a_held_an_ungraded_or_a_rules_blocked_head_is_refused_and_mq_why_names_the_check(shell, config, tmp_path, capsys, blocked, line):
    lone_green_pr(shell)
    if blocked == "fidelity":
        finder_verdict(shell, "29020", "ok cccc333 no findings")
    elif blocked == "finder":
        grade(shell, "29020", "ok cccc333", finder=False)
    else:
        grade_gated_heads_ok(shell)
    if blocked == "hold":
        line = line.format(seq=root_hold(shell, 29020, text="hold #29020 owner review"))
    if blocked == "rules-review":
        shell.rows[0]["rules_blocked"] = True
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == []
    capsys.readouterr()
    posts = len(shell.posts)
    assert mq(shell, config, "why", "29020") == 0
    name = line.split(" ", 1)[0]
    assert capsys.readouterr().out.splitlines() == ["#29020 cccc333333 graphite-stack-enqueue: GREEN graphite READY", *(line if quiet.startswith(f"{name} ") else quiet for quiet in QUIET_WHY)]
    assert len(shell.posts) == posts


def test_a_rulings_warning_posts_once_per_head_whether_the_pass_names_it_by_full_or_short_sha(shell, config, tmp_path):
    lone_green_pr(shell)
    shell.check_pr["29020"] = (1, "NAME-HIT rule/394dfd6 go/x.go:5 sync\n")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    landing = runner_module.Landing(runner, runner.config.landing)
    rows = landing.rows()
    for head in ("cccc333333" + "d" * 30, "cccc333333"):
        landing.check_rulings(runner_module.Graded({"29020": head}, rows, shell.now(), act=True))
    assert len([post for post in shell.posts if post["kind"] == "note" and "warns at cccc333333" in post["text"]]) == 1


@pytest.mark.parametrize(
    ("first", "second", "answered", "warnings"),
    [
        ("NAME-HIT rule/8aeaaff go/x.go:5 writer\nCONTRACT-MISSING lane-a: no fidelity desk has graded a read-back\n", "NAME-HIT rule/8aeaaff go/x.go:5 writer\n", False, 1),
        ("NAME-HIT rule/8aeaaff go/x.go:5 writer\n", "NAME-HIT rule/8aeaaff go/x.go:5 writer\nRULINGS-PLAIN a code symbol\n", True, 1),
        ("NAME-HIT rule/8aeaaff go/x.go:5 writer\n", "NAME-HIT rule/8aeaaff go/x.go:5 writer\nRULINGS-PLAIN a code symbol\n", False, 2),
    ],
)
def test_a_rulings_warning_posts_again_at_its_head_only_for_a_new_cause_before_the_lane_answers(shell, config, tmp_path, first, second, answered, warnings):
    lone_green_pr(shell)
    for out in (first, second):
        shell.check_pr["29020"] = (1, out)
        runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
        landing = runner_module.Landing(runner, runner.config.landing)
        landing.reports_file.unlink(missing_ok=True)
        landing.check_rulings(runner_module.Graded({"29020": "cccc333333"}, landing.rows(), shell.now(), act=True))
        if answered:
            shell.records.append({"seq": 900, "drive": "d1", "lane": LANE, "kind": "answer", "to": ["desk-runner"], "resolves": 1, "text": "both hits are unrelated words; head unchanged"})
    assert len([post for post in shell.posts if post["kind"] == "note" and "warns at cccc333333" in post["text"]]) == warnings


def test_a_rulings_warning_for_a_new_cause_at_an_unchanged_head_names_that_cause_alone(shell, config, tmp_path):
    lone_green_pr(shell)
    for out in ("NAME-HIT rule/8aeaaff go/x.go:5 writer\n", "NAME-HIT rule/8aeaaff go/x.go:5 writer\nCONTRACT-MISSING lane-a: no fidelity desk has graded a read-back\n"):
        shell.check_pr["29020"] = (1, out)
        runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
        landing = runner_module.Landing(runner, runner.config.landing)
        landing.reports_file.unlink(missing_ok=True)
        landing.check_rulings(runner_module.Graded({"29020": "cccc333333"}, landing.rows(), shell.now(), act=True))
    first, second = [post["text"] for post in shell.posts if post["kind"] == "note" and "warns at cccc333333" in post["text"]]
    assert first.endswith("the head is not held for it: NAME-HIT rule/8aeaaff go/x.go:5 writer")
    assert second.endswith("the head is not held for it: CONTRACT-MISSING lane-a: no fidelity desk has graded a read-back")


def test_mq_why_names_the_adapters_approval_and_ci_verdicts(shell, config, tmp_path, capsys):
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 graphite FAILING_REQUIRED; not approved by forge-pr-reviewer[bot]")]
    assert mq(shell, config, "why", "29020") == 0
    lines = capsys.readouterr().out.splitlines()
    assert "approvals pending not approved by forge-pr-reviewer[bot]" in lines and "required-ci fail graphite FAILING_REQUIRED" in lines


def test_mq_gos_lists_each_standing_go_the_queue_does_not_hold_from_one_status_read(shell, config, capsys):
    two_pr_stack(shell)
    shell.rows.append({"pr": "29042", "lane": LANE, "branch": "b/1", "base": "dev", "head": "eeee555555", "state": "open", "labels": "hold"})
    shell.rows.append({"pr": "29050", "lane": LANE, "branch": "c/1", "base": "dev", "head": "ffff666666", "state": "open"})
    root_go(shell, "go #29020 at cccc333, #29031 at dddd444 and #29042 at abcd1234", shell.now())
    seq = shell.records[-1]["seq"]
    shell.queue = {"29020": "queued", "29031": "not queued", "29042": "evicted", "29050": "not queued"}
    shell.pr_status = {"29031": {"verdict": "landable"}, "29042": {"verdict": "blocked:ci-red"}}
    assert mq(shell, config, "gos") == 0
    assert capsys.readouterr().out.splitlines() == [
        f"#29031 go #{seq} at its head dddd444444; not queued, landable",
        f"#29042 go #{seq} at abcd1234, but its head is eeee555555; evicted, blocked:ci-red; held",
    ]
    assert len([call for call in shell.calls if call[:4] == ["ccx", "vcs", "pr", "status"]]) == 1
    assert not [call for call in shell.calls if call[:3] == ["gh", "pr", "view"]]


def pushed_while_enqueued(shell: FakeShell, monkeypatch: pytest.MonkeyPatch, pr: str, head: str) -> None:
    run = shell.run

    def pushing(argv, stdin=None, env=None):
        if Path(argv[0]).name == "stack-enqueue" and not {"--check", "--status"} & set(argv):
            shell.current_heads[pr] = head
        return run(argv, stdin, env)

    monkeypatch.setattr(shell, "run", pushing)


def queued_at_new_head(shell: FakeShell) -> list[tuple[str, str]]:
    return [(post["to"], post_body(post)) for post in notes(shell) if post_body(post).startswith("QUEUED-AT-NEW-HEAD")]


def test_mq_enqueue_runs_the_gate_enqueues_and_names_a_head_pushed_while_it_ran(shell, config, capsys, monkeypatch):
    two_pr_stack(shell)
    pushed_while_enqueued(shell, monkeypatch, "29031", "ffff777777")
    assert mq(shell, config, "enqueue", "29031") == 0
    assert capsys.readouterr().out.startswith("enqueued #29020 #29031 at #29020 cccc333333 #29031 dddd444444\n")
    assert [call[1:] for call in shell.enqueues()] == [["29031", "--whole"]]
    assert queued_at_new_head(shell) == [("root,fidelity-desk", "QUEUED-AT-NEW-HEAD #29031: the gate graded dddd444444 and the queue took ffff777777, pushed while the enqueue ran; grade ffff777777")]


def test_mq_enqueue_refuses_a_head_the_gate_does_not_pass_and_enqueues_nothing(shell, config, capsys):
    two_pr_stack(shell)
    shell.current_heads["29020"] = "eeee555555"
    assert mq(shell, config, "enqueue", "29031") == 1
    assert capsys.readouterr().out.splitlines()[-1] == "REFUSED: a check above does not pass a head this enqueue puts in the queue"
    assert shell.enqueues() == []


def test_mq_enqueue_refuses_a_pr_an_open_hold_record_names_as_held_though_landing_checks_omits_hold_until_the_root_resolves_it(shell, config, capsys):
    raw = json.loads(config.read_text())
    raw["landing"]["checks"] = [name for name in runner_module.merge_queue.CHECKS if name != "hold"]
    config.write_text(json.dumps(raw))
    two_pr_stack(shell)
    hold_record(shell, 41, 29020, "open", "2026-09-01T17:59:00Z")
    assert mq(shell, config, "enqueue", "29031") == 1
    assert capsys.readouterr().out.splitlines() == ["#29020 cccc333333 hold fail open cci hold #41", "HELD: #29020 held as named above; the hand enqueue puts nothing in the queue until each hold clears, an open cci hold record by the root's `--resolves`"]
    assert shell.enqueues() == []
    next(record for record in shell.records if record["seq"] == 41)["status"] = "closed"
    assert mq(shell, config, "enqueue", "29031") == 0
    assert [call[1:] for call in shell.enqueues()] == [["29031", "--whole"]]


def test_mq_enqueue_refuses_a_go_named_head_the_owner_never_tapped_with_the_runners_tap_reason(shell, config, capsys):
    merge_mode(config, "every")
    raw = json.loads(config.read_text())
    raw["landing"]["tap_rules"] = {"rule/82317b0": ["go/sand/next/*"]}
    config.write_text(json.dumps(raw))
    shell.register = {"rule/82317b0": {"kind": "order", "status": "in-force"}}
    lone_green_pr(shell)
    shell.files["29020"] = ["go/sand/next/vm/run.go"]
    grade(shell, "29020", "ok F307 at cccc333.")
    merge_go(shell, "29020", "go #29020 at cccc333: his tap on the board (watchdog #172407)")
    assert mq(shell, config, "enqueue", "29020", "--partial") == 1
    out = capsys.readouterr().out.splitlines()
    assert "#29020 cccc333333 merge-mode pending TAP-REQUIRED rule/82317b0: it lands only on the owner's tap at this head, and a root go is no tap; no approval on the root's board names cccc333333" in out
    assert out[-1] == "REFUSED: a check above does not pass a head this enqueue puts in the queue" and shell.enqueues() == []
    tap(shell, "29020", "cccc333333")
    assert mq(shell, config, "enqueue", "29020", "--partial") == 0
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


def test_a_head_pushed_while_the_runner_enqueued_it_draws_one_note_naming_the_head_the_queue_took(shell, config, monkeypatch):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    pushed_while_enqueued(shell, monkeypatch, "29020", "eeee555555")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert queued_at_new_head(shell) == [("root,fidelity-desk", "QUEUED-AT-NEW-HEAD #29020: the gate graded cccc333333 and the queue took eeee555555, pushed while the enqueue ran; grade eeee555555")]


def viewed_heads(shell: FakeShell) -> list[str]:
    return [call[3] for call in shell.calls if call[:3] == ["gh", "pr", "view"] and call[-1] == ".headRefOid"]


@pytest.mark.parametrize(("partial", "graded"), [((), ["29020", "29031"]), (("--partial",), ["29020"])])
def test_mq_gate_holds_a_hand_enqueue_to_the_runners_checks_at_githubs_head_of_every_pr_it_queues(shell, config, capsys, partial, graded):
    two_pr_stack(shell)
    assert mq(shell, config, "gate", "29031") == 0
    assert capsys.readouterr().out == ""
    shell.current_heads["29020"] = "eeee555555"
    grade(shell, "29020", "ok F307 at eeee555.", finder=False)
    posts = len(shell.posts)
    assert mq(shell, config, "gate", "29020", *partial) == 1
    assert capsys.readouterr().out.splitlines() == [
        f"#29020 eeee555555 finder fail FINDER-PENDING #29020: no `finder:` record from finder-desk names this head, and a lane's own finder record never grades it"
    ]
    assert viewed_heads(shell)[-len(graded):] == graded and len(shell.posts) == posts


def other_drive_opened(shell: FakeShell, pr: str) -> None:
    shell.drives = ["d1", "d2"]
    shell.records.append({"seq": 9100 + len(shell.records), "drive": "d2", "lane": "billing-fix", "kind": "opened", "to": ["root"], "refs": {"prs": [f"Forge-AI/monorepo#{pr}"]}, "text": f"opened #{pr}", "at": "2026-10-01T17:00:00Z"})


def stopped(shell: FakeShell) -> list[tuple[str, str]]:
    return [(post["drive"], post["to"]) for post in shell.posts if post["kind"] == "stopped"]


def dequeue_lines(shell: FakeShell) -> list[str]:
    return [line for line in escalations(shell) if line.startswith("DEQUEUE")]


def root_order(shell: FakeShell, seq: int, text: str) -> None:
    shell.records.append({"seq": seq, "drive": "d1", "lane": "root", "kind": "decide", "to": ["landing-desk"], "refs": {}, "text": text, "at": actions.stamp(shell.clock)})


def test_a_root_dequeue_order_takes_the_pr_out_and_says_dequeued_once_the_status_read_shows_it_out(shell, config, tmp_path):
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    landing_pass(shell, config)
    root_order(shell, 40000, "Adopted. landing-desk: dequeue #29020 cccc333 now; it re-enters after his pick.")
    landing_pass(shell, config)
    assert dequeues(shell) == [["29020", "--dequeue"]] and dequeue_lines(shell) == [] and stopped(shell) == []
    shell.clock += runner_module.STATUS_MAX_AGE + timedelta(seconds=1)
    shell.queue["29020"] = "not queued"
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert dequeues(shell) == [["29020", "--dequeue"]] and stopped(shell) == [("d1", LANE)]
    assert dequeue_lines(shell) == ["DEQUEUED dequeue:40000:29020 #29020: #29020 at cccc333333, root #40000 ordered it out: the status read after the dequeue finds it not queued; its head stays out until mq.py requeue"]


def test_a_root_text_that_only_mentions_a_dequeue_takes_nothing_out(shell, config, tmp_path):
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    landing_pass(shell, config)
    root_order(shell, 40000, "Do not dequeue #29020 yet; the lane ran mq dequeue #29020 by hand and said `dequeue #29020` was wrong.")
    root_order(shell, 40001, "Previously requested: dequeue #29020.")
    root_order(shell, 40002, "```\ndequeue #29020\n```")
    landing_pass(shell, config)
    assert dequeues(shell) == [] and dequeue_lines(shell) == []


def test_an_order_for_a_pr_already_out_of_the_queue_latches_its_head_out_and_one_still_queued_reads_failed(shell, config, tmp_path):
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    landing_pass(shell, config)
    shell.queue["29020"] = "evicted"
    root_order(shell, 40000, "dequeue #29020 until the pick.")
    landing_pass(shell, config)
    shell.clock += runner_module.STATUS_MAX_AGE + timedelta(seconds=1)
    landing_pass(shell, config)
    assert dequeues(shell) == [] and len(shell.enqueues()) == 1
    assert dequeue_lines(shell) == ["DEQUEUED dequeue:40000:29020 #29020: #29020 at cccc333333, root #40000 ordered it out: the status read finds it evicted, so nothing was taken out; its head stays out until mq.py requeue"]
    shell.queue["29020"] = "queued"
    root_order(shell, 40001, "Second ask. landing-desk: dequeue #29020.")
    landing_pass(shell, config)
    shell.clock += runner_module.STATUS_MAX_AGE + timedelta(seconds=1)
    landing_pass(shell, config)
    assert dequeue_lines(shell)[1:] == ["DEQUEUE-FAILED dequeue:40001:29020 #29020: #29020 at cccc333333, root #40001 ordered it out: the status read after the dequeue still finds it queued"]
    assert mq(shell, config, "requeue", "29020") == 0
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 2


@pytest.mark.parametrize("closing", ["resolves", "re"])
def test_a_root_withdraw_of_a_go_the_runner_already_enqueued_takes_the_pr_out_and_never_enqueues_it_again(shell, config, closing):
    merge_mode(config, "every")
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    merge_go(shell, "29020", "merge #29020 at cccc333")
    go = shell.records[-1]["seq"]
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]
    shell.records.append({"seq": 40000, "drive": "d1", "lane": "root", "kind": "withdraw", "to": ["landing-desk"], "refs": {}, "text": "go on #29020 withdrawn: new-tree fix, his tap first", "at": actions.stamp(shell.clock), closing: [go] if closing == "resolves" else go})
    landing_pass(shell, config)
    assert dequeues(shell) == [["29020", "--dequeue"]]
    shell.clock += runner_module.STATUS_MAX_AGE + timedelta(seconds=1)
    shell.queue["29020"] = "not queued"
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1 and stopped(shell) == [("d1", LANE)]
    assert dequeue_lines(shell) == [f"DEQUEUED dequeue:40000:29020 #29020: #29020 at cccc333333, root #40000 withdrew go #{go}: the status read after the dequeue finds it not queued; its head stays out until mq.py requeue"]


def test_mq_dequeue_refuses_another_drives_pr_and_names_its_root(shell, config, tmp_path, capsys):
    shell.rows = [dict(ONE_ROW, ours=False)]
    other_drive_opened(shell, "29020")
    assert mq(shell, config, "dequeue", "29020") == 1
    err = capsys.readouterr().err
    assert "#29020 is not this drive's" in err and "ask root@d2 to dequeue it" in err
    assert dequeues(shell) == [] and stopped(shell) == []


def test_mq_dequeue_of_our_pr_tells_every_naming_drive_and_keeps_the_head_out_until_requeue(shell, config, tmp_path, capsys):
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1
    other_drive_opened(shell, "29020")
    assert mq(shell, config, "dequeue", "29020", "--reason", "owner pulled it") == 0
    assert dequeues(shell) == [["29020", "--dequeue"]]
    assert stopped(shell) == [("d1", LANE), ("d2", "root")]
    assert all(post["text"].startswith("DEQUEUED #29020 by d1: owner pulled it") for post in shell.posts if post["kind"] == "stopped")
    [action] = enqueues_of(tmp_path).values()
    assert action.response["outcome"] == "pulled"
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1
    assert mq(shell, config, "requeue", "29020") == 0
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 2


FLAKY = (
    "#29020 BLOCKED cccc333333 ejected from the merge queue Oct 16, 9:47 PM UTC because Failed CI (buildkite/test), and no head was pushed since; "
    "draft #39356 held only this stack, so the red check is its own; read the failure on draft #39356 and push the fix as a new head before enqueueing again"
)


def test_a_flaky_check_ejection_goes_to_the_restack_lane_once_naming_the_job_and_its_head_stays_out(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    raw["landing"]["restack_lane"] = "restack-worker"
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    landing_pass(shell, config)
    out_of_queue(shell, "29020")
    shell.gates["29020"] = [gate(FLAKY)]
    landing_pass(shell, config)
    landing_pass(shell, config)
    [blocker] = blockers(shell)
    assert blocker["to"] == "restack-worker" and blocker["topic"] == "29020"
    assert "cccc333333" in post_body(blocker) and "Failed CI (buildkite/test)" in post_body(blocker)
    assert len(shell.enqueues()) == 1 and escalations(shell) == []
    assert [action.response["outcome"] for action in enqueues_of(tmp_path).values()] == ["ejected"]


def test_a_pr_left_on_a_graphite_base_is_resubmitted_once_per_head_one_a_pass_with_a_note_to_the_desk_lane(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    raw["landing"]["desk_lane"] = "landing-desk-3"
    config.write_text(json.dumps(raw))
    shell.rows = [
        {"pr": "29020", "lane": LANE, "branch": "a/3", "base": "graphite-base/29020", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "29031", "lane": LANE, "branch": "a/4", "base": "graphite-base/29031", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
    ]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 GitHub reads CONFLICTING")]
    shell.gates["29031"] = [gate("#29031 BLOCKED dddd444444 GitHub reads CONFLICTING")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    landing_pass(shell, config)
    checkout = str(Path(json.loads(config.read_text())["landing"]["checkout"]).expanduser())
    assert shell.ships == [(["--branch", "a/3", "--tip-only", "--no-commit", "--no-watch"], checkout), (["--branch", "a/4", "--tip-only", "--no-commit", "--no-watch"], checkout)]
    notes = [post for post in shell.posts if post["kind"] == "note" and post["to"] == "landing-desk-3"]
    assert [post["topic"] for post in notes] == ["29020", "29031"]
    assert notes[0]["text"].startswith("#29020 cccc333333 sat on graphite-base/29020 after its parent landed; resubmitted with ccx vcs ship --branch a/3 --tip-only --no-commit --no-watch")


def test_a_runner_started_from_a_lane_posts_a_resubmits_head_as_the_prs_owner_not_that_lane(shell, config, tmp_path):
    """Oct 29: tooling-mq-redesign-2 ran `desk-runner.py handoff`, its CCI_AGENT_ID reached the runner, and ccx posted the resubmits' head records for #45379 and #45383 as tooling-mq-redesign-2, so their blockers went to it."""
    shell.environ |= {"CCI_AGENT_ID": "atooling-mq-redesign-2-477d5e57b81838a8", "CLAUDE_CODE_SESSION_ID": "d50e80e8", "CCI_LANE": "tooling-mq-redesign-2"}
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "graphite-base/29020", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 GitHub reads CONFLICTING")]
    landing_pass(shell, config)
    assert [shell.environ[name] for name in runner_module.LANE_IDENTITY] == ["", "", "", ""]
    assert shell.ship_envs == [{"CCI_DRIVE": json.loads(config.read_text())["drive"], "CCI_LANE": LANE}]


@pytest.mark.parametrize(
    ("failure", "aborted"),
    [
        (runner_module.Done(runner_module.TIMED_OUT, "", "ccx vcs ship --branch a/3 --tip-only --no-commit --no-watch timed out after 300s and was killed"), True),
        (runner_module.Done(1, "", "stack rebase: a/3 stopped on a conflict in ~/.claude/worktrees/monorepo/conflict-a-3"), True),
        (runner_module.Done(1, "", "stack rebase: a stack rebase of a/3 is already in progress (stopped on a/3 in ~/.claude/worktrees/monorepo/conflict-a-3, pid 85358) — ccx vcs stack continue, or ccx vcs stack abort, from one of its branches"), False),
    ],
)
def test_a_failed_resubmit_drops_its_stopped_stack_rebase_and_names_the_abort_in_its_note(shell, config, tmp_path, failure, aborted):
    """p8a-step7 #192681, #193574, #193729: a graphite-base resubmit killed at its bound left a stopped stack rebase that blocked the lane's next `ccx vcs stack submit`, and the bare abort that followed named no run, so it dropped nothing or another lane's."""
    raw = json.loads(config.read_text())
    raw["landing"]["desk_lane"] = "landing-desk-3"
    config.write_text(json.dumps(raw))
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "graphite-base/29020", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 GitHub reads CONFLICTING")]
    shell.ship_failure = failure
    landing_pass(shell, config)
    checkout = str(Path(json.loads(config.read_text())["landing"]["checkout"]).expanduser())
    assert shell.aborts == ([(["--stack", "a/3"], checkout)] if aborted else [])
    [note] = [post for post in shell.posts if post["kind"] == "note" and post["to"] == "landing-desk-3"]
    assert note["text"].endswith("; ccx vcs stack abort --stack a/3 exited 0: dropped the stopped stack rebase") == aborted
    assert runner_module.time_bound(["ccx", "vcs", "ship", "--branch", "a/3", "--tip-only"]) == runner_module.ENQUEUE_SECONDS


def test_a_lower_priority_value_gates_and_enqueues_first(shell, config, tmp_path, capsys):
    shell.rows = [ONE_ROW, {"pr": "29100", "lane": LANE, "branch": "p/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444", "priority": 0}]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    shell.gates["29100"] = [gate("#29100 GREEN dddd444444 graphite READY", would="#29100")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    shell.enqueue_out["29100"] = (0, "enqueue #29100: {}\n")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29100", "--partial"], ["29020", "--partial"]]
    assert mq(shell, config, "status") == 0
    trees = [line for line in capsys.readouterr().out.splitlines() if line.startswith("tree ")]
    assert trees == ["tree #29100 priority 0: #29100", "tree #29020 priority 100: #29020"]
    assert mq(shell, config, "priority", "29020", "5") == 0
    assert [call[4:] for call in shell.calls if len(call) > 4 and Path(call[1]).name == "ledger.py" and call[4] == "priority"] == [["priority", "--ledger", "abc", "--repo", "Forge-AI/monorepo", "--pr", "29020", "--priority", "5"]]


def label_adapter(shell: FakeShell, config: Path) -> None:
    raw = json.loads(config.read_text())
    raw["landing"]["adapter"] = {"kind": "label"}
    config.write_text(json.dumps(raw))
    shell.rows = [ONE_ROW]
    grade(shell, "29020", "ok F307 at cccc333.")
    shell.ledger_outs["label --dry-run"] = (0, "would label #29020 cccc333333\n")
    shell.ledger_outs["label"] = (0, "labelled #29020 cccc333333 at 2026-10-01T18:00:00Z; the queue takes #29020 as one entry\n")


def ledger_calls(shell: FakeShell, verb: str) -> list[list[str]]:
    return [call[4:] for call in shell.calls if len(call) > 4 and Path(call[1]).name == "ledger.py" and call[4] == verb and "--dry-run" not in call]


def test_a_label_adapter_gates_and_enqueues_through_ledger_label(shell, config, tmp_path):
    label_adapter(shell, config)
    landing_pass(shell, config)
    assert ledger_calls(shell, "label")[-1] == ["label", "--ledger", "abc", "--repo", "Forge-AI/monorepo", "--pr", "29020", "--expect-head", "cccc333333", "--checkout", str(tmp_path / "checkout")]
    [action] = enqueues_of(tmp_path).values()
    assert action.response["outcome"] == "enqueued" and stack_enqueue_calls(shell) == []


def test_mq_requeue_under_the_label_adapter_clears_the_ledger_latch_before_the_pulled_head_goes_back(shell, config, tmp_path, capsys):
    label_adapter(shell, config)
    landing_pass(shell, config)
    assert mq(shell, config, "dequeue", "29020", "--reason", "owner pulled it") == 0
    assert len(ledger_calls(shell, "unlabel")) == 1
    shell.ledger_outs["requeue"] = (1, "ledger unreachable\n")
    capsys.readouterr()
    assert mq(shell, config, "requeue", "29020") == 1
    assert "the label queue still refuses this head, so nothing was cleared: ledger unreachable" in capsys.readouterr().err
    [action] = enqueues_of(tmp_path).values()
    assert action.response["outcome"] == "pulled"
    shell.ledger_outs["requeue"] = (0, "cleared the label latch on #29020 cccc33333 at 2026-10-01T18:00:00Z; label may queue the same head once more\n")
    assert mq(shell, config, "requeue", "29020") == 0
    assert ledger_calls(shell, "requeue")[-1] == ["requeue", "--ledger", "abc", "--repo", "Forge-AI/monorepo", "--pr", "29020", "--expect-head", "cccc333333"]
    landing_pass(shell, config)
    assert len(ledger_calls(shell, "label")) == 2


def test_a_label_refusal_its_lane_must_fix_routes_as_the_adapters_blocker_and_a_missing_approval_routes_nothing(shell, config, tmp_path):
    label_adapter(shell, config)
    whole = "REFUSED the stack #29020 enqueues as one entry, so #29020 refuses all of it; nothing was labelled\n"
    shell.ledger_outs["label --dry-run"] = (1, "REFUSED #29020 has no approval in force: a reviewer's latest decision must be APPROVED (#29020)\n" + whole)
    landing_pass(shell, config)
    assert blockers(shell) == [] and ledger_calls(shell, "label") == []
    shell.ledger_outs["label --dry-run"] = (1, "REFUSED failed check runs on cccc33333: buildkite/test (#29020)\n" + whole)
    landing_pass(shell, config)
    [blocker] = blockers(shell)
    assert "#29020 cccc333333: failed check runs on cccc33333: buildkite/test" in blocker["text"]
    assert ledger_calls(shell, "label") == [] and ledger_calls(shell, "route") == []


@pytest.mark.parametrize(
    ("change", "named", "member"),
    [
        (lambda landing: landing.pop("adapter"), "names no landing.adapter.kind", '"adapter": {"kind": "label"}'),
        (lambda landing: landing.update(adapter={"kind": "graphite-stack-enqueue"}), "names no landing.adapter.script", '"adapter": {"kind": "graphite-stack-enqueue", "script": ".agents/skills/submit-pr/scripts/stack-enqueue"}'),
        (lambda landing: landing.update(checks=["hold", "lint"]), "lists lint under landing.checks", '"checks": ["hold"]'),
        (lambda landing: landing.update(merge_mode="high-risk"), "runs merge mode high-risk with no landing.high_risk_paths globs", '"high_risk_paths": ["*migrations/*", "*.sql", "*backfill*", "*secret*", "*credential*", "*creds*"]'),
        (lambda landing: landing.pop("release_cli"), "names no landing.release_cli", '"release_cli": "<release command line>"'),
    ],
)
def test_the_landing_desk_refuses_to_start_on_a_merge_queue_config_it_cannot_run_and_names_the_members_that_fix_it(shell, config, tmp_path, change, named, member):
    raw = json.loads(config.read_text())
    change(raw["landing"])
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)
    assert cli(shell, config, "run", "--desk", "landing", "--once") == 3
    [line] = escalations(shell)
    assert line.startswith("MQ-CONFIG-INVALID ") and named in line and shell.enqueues() == []
    assert line.endswith(f'add to the "landing" object of runner.json, replacing any member of the same name: {member}')
    fixed = json.loads("{" + member + "}")
    raw["landing"].update(fixed)
    if "high_risk_paths" in fixed:
        raw["landing"]["merge_mode"] = "high-risk"
    assert runner_module.mq_config_fault(raw["landing"]) == ""


def test_a_config_with_no_adapter_on_a_checkout_carrying_stack_enqueue_is_told_to_name_it_and_every_fault_at_once(shell, config, tmp_path, monkeypatch):
    raw = json.loads(config.read_text())
    del raw["landing"]["adapter"]
    raw["landing"]["merge_mode"] = "high-risk"
    script = tmp_path / "checkout" / ".agents/skills/submit-pr/scripts/stack-enqueue"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("")
    monkeypatch.setattr(runner_module.merge_queue.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""))
    fault = runner_module.mq_config_fault(raw["landing"])
    assert "names no landing.adapter.kind" in fault and "no landing.high_risk_paths globs" in fault
    assert fault.endswith(': "adapter": {"kind": "graphite-stack-enqueue", "script": ".agents/skills/submit-pr/scripts/stack-enqueue"}, "high_risk_paths": ["*migrations/*", "*.sql", "*backfill*", "*secret*", "*credential*", "*creds*"]')


def test_a_checks_list_without_fidelity_enqueues_an_ungraded_head(shell, config, tmp_path, capsys):
    raw = json.loads(config.read_text())
    raw["landing"]["checks"] = ["hold", "rules-review", "merge-mode", "approvals", "required-ci"]
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1 and blockers(shell) == []
    assert mq(shell, config, "checks") == 0
    assert capsys.readouterr().out.splitlines() == ["hold enqueue", "rules-review accept, enqueue", "merge-mode accept, enqueue", "approvals adapter check", "required-ci adapter check"]


def old_app_checked(config: Path) -> None:
    raw = json.loads(config.read_text())
    raw["landing"]["checks"] = [*runner_module.merge_queue.CHECKS, "old-app"]
    config.write_text(json.dumps(raw))


@pytest.mark.parametrize(
    ("lane", "text", "warned"),
    [
        (None, None, True),
        ("fidelity-desk", "old-app: unchanged cccc333 (touches only the new tree)", False),
        ("fidelity-desk", "old-app: bug fix cccc333", False),
        ("fidelity-desk", "old-app: CHANGES cccc333: a user without the flag loses the retry button", True),
        ("fidelity-desk", "old-app: unchanged bbbb222", True),
        (LANE, "old-app: unchanged cccc333", True),
        ("fidelity-desk", "old-app: unchanged (touches only the new tree)", True),
    ],
)
def test_a_drive_naming_the_old_app_check_enqueues_every_head_and_warns_once_on_one_no_fidelity_lane_graded_unchanged_or_bug_fix(shell, config, tmp_path, lane, text, warned):
    old_app_checked(config)
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    if text:
        shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": lane, "kind": "review", "to": ["root"], "refs": {"prs": ["Forge-AI/monorepo#29020"]}, "text": text})
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() and not blockers(shell)
    pending = [note for note in notes(shell) if "OLD-APP-PENDING #29020" in note["text"]]
    assert len(pending) == int(warned)
    assert all(note["to"] == f"{LANE},fidelity-desk" for note in pending)


def test_an_old_app_grade_posted_after_the_pass_scanned_its_reviews_draws_no_warning(shell, config, tmp_path):
    """Oct 27 #156087: a fidelity desk's old-app line landed while a long landing pass ran, after its one review scan, and the pass still warned OLD-APP-PENDING on that head."""
    old_app_checked(config)
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    scan = shell.cci_tail
    late = {"seq": 99999, "drive": "d1", "lane": "fidelity-desk", "kind": "review", "to": ["root"], "refs": {"prs": ["Forge-AI/monorepo#29020"]}, "text": "old-app: unchanged cccc333"}

    def grade_lands_after_the_scan(argv: list[str]) -> runner_module.Done:
        done = scan(argv)
        if "--kind=review" in argv and "--kind=head" in argv and late not in shell.records:
            shell.records.append(late)
        return done

    shell.cci_tail = grade_lands_after_the_scan
    landing_pass(shell, config)
    assert not [note for note in notes(shell) if "OLD-APP-PENDING #29020" in post_body(note)]


@pytest.mark.parametrize(("session", "posted", "to"), [(True, False, f"{LANE},fidelity-desk"), (False, True, f"{LANE},fidelity-desk"), (False, False, "fidelity-desk")], ids=["live", "no-session", "dead"])
def test_an_old_app_warning_reaches_its_living_lane_and_the_fidelity_lanes_and_never_the_root(shell, config, tmp_path, session, posted, to):
    old_app_checked(config)
    lone_green_pr(shell)
    shell.gone = set() if session else {LANE}
    if posted:
        posted_lately(shell, LANE)
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    [pending] = [note for note in notes(shell) if "OLD-APP-PENDING #29020" in post_body(note)]
    assert (pending["to"], post_body(pending).startswith("#29020 cccc333333: WARNING")) == (to, True)


def test_an_old_app_warning_for_a_rotated_lane_reaches_its_live_successor_and_the_fidelity_lanes(shell, config, tmp_path):
    old_app_checked(config)
    lone_green_pr(shell)
    shell.gone = {LANE}
    shell.seen[f"{LANE}-2"] = actions.stamp(shell.clock)
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    [pending] = [note for note in notes(shell) if "OLD-APP-PENDING #29020" in post_body(note)]
    assert pending["to"] == f"{LANE}-2,fidelity-desk"


def fast_row(shell: FakeShell, labels: str = "merge-fast,externally-merged", lane: str = LANE) -> dict:
    return {"pr": "36642", "lane": lane, "branch": "f/1", "base": "dev", "head": "eeee555555", "state": "landed", "reported_head": "eeee555555", "labels": labels, "landed_sha": FAST_SQUASH, "landed_at": actions.stamp(shell.now())}


def land_fast(shell: FakeShell, config: Path, body: str = FAST_BODY, labels: str = "merge-fast,externally-merged", lane: str = LANE) -> None:
    shell.bodies["36642"] = body
    shell.rows = [fast_row(shell, labels, lane) | {"state": "open", "base": "f/0"}]
    shell.gates["36642"] = [gate("#36642 QUEUED eeee555555 graphite QUEUED_TO_MERGE")]
    landing_pass(shell, config)
    shell.sleep(180)
    shell.rows = [fast_row(shell, labels, lane)]
    shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": lane, "kind": "landed", "to": ["root"], "refs": {"prs": ["Forge-AI/monorepo#36642"]}, "text": "#36642 landed", "at": actions.stamp(shell.now())})


def body_reads(shell: FakeShell) -> list[list[str]]:
    return [call for call in shell.calls if call[:3] == ["gh", "pr", "view"] and call[-1] == "body"]


def release_action(tmp_path: Path) -> actions.Action:
    return incident(tmp_path, "desk-landing").actions["release:36642"]


INCIDENT_LANE = "incident-sand-fix"
INCIDENT_HEAD = "ffff666666aaaabbbbccccddddeeee0000111122"
INCIDENT_SQUASH = "9a8b7c6d5e4f30211203f4e5d6c7b8a9f0e1d2c3"
WALKER = "release-walker-4"


def incident_pr(shell: FakeShell, buckets: tuple[str, ...] = ("pass", "pass")) -> None:
    shell.rows = [{"pr": "36650", "lane": INCIDENT_LANE, "branch": "i/1", "base": "dev", "head": INCIDENT_HEAD, "state": "open", "reported_head": INCIDENT_HEAD}]
    shell.reviews["36650"] = {"reviewDecision": "APPROVED", "headRefOid": INCIDENT_HEAD, "state": "OPEN"}
    shell.checks["36650"] = [{"name": name, "bucket": bucket} for name, bucket in zip(("test", "build"), buckets, strict=True)]
    shell.merge_commits["36650"] = INCIDENT_SQUASH


def merges(shell: FakeShell) -> list[list[str]]:
    return [call for call in shell.calls if call[:3] == ["gh", "pr", "merge"]]


def test_an_approved_green_incident_fix_is_merged_by_plain_squash_once_and_never_enqueued(shell, config, tmp_path):
    incident_pr(shell)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert merges(shell) == [["gh", "pr", "merge", "36650", "--repo", "Forge-AI/monorepo", "--squash", "--match-head-commit", INCIDENT_HEAD]]
    assert shell.enqueues() == [] and not [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]
    squash_reads = [call for call in shell.raw if call[:3] == ["ccx", "vcs", "gh"] and call[-1] in ("reviewDecision,headRefOid,state", "name,bucket", "mergeCommit")]
    assert {call[-1] for call in squash_reads} == {"reviewDecision,headRefOid,state", "name,bucket", "mergeCommit"} and {tuple(call[3:5]) for call in squash_reads} == {("--max-age", "0s")}
    [landed] = [post for post in shell.posts if post["kind"] == "landed"]
    assert (landed["to"], landed["pr"], landed["id"]) == (f"{INCIDENT_LANE},root", "36650", f"land:36650@{INCIDENT_HEAD}")
    assert landed["text"].startswith("#36650 landed by plain squash as 9a8b7c6d5e: an incident fix, approved and green at ffff666666")
    squash = incident(tmp_path, "desk-landing").actions[f"land:36650@{INCIDENT_HEAD}"]
    assert (squash.status, squash.response["sha"]) == ("completed", INCIDENT_SQUASH)

def test_a_root_decide_taking_an_incident_fix_off_the_incident_path_stops_its_squash_and_logs_once(shell, config, tmp_path):
    incident_pr(shell)
    shell.records.append({"seq": 9500, "drive": "d1", "lane": "root", "kind": "decide", "to": ["desk-runner"], "refs": {"prs": ["Forge-AI/monorepo#36650"]}, "text": "#36650 is NOT an incident fix; take it off the incident path; it lands only on his tap", "at": actions.stamp(shell.clock)})
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert merges(shell) == [] and shell.enqueues() == []
    assert [line.split(" ", 1)[0] for line in escalations(shell) if "INCIDENT-TAG-CLEARED" in line] == ["INCIDENT-TAG-CLEARED"]


@pytest.mark.parametrize("kind", ["go", "hold", "lift"])
def test_a_root_go_hold_or_lift_saying_not_an_incident_fix_stops_its_squash(shell, config, tmp_path, kind):
    incident_pr(shell)
    shell.records.append({"seq": 9501, "drive": "d1", "lane": "root", "kind": kind, "status": "open", "to": ["desk-runner"], "refs": {"prs": ["Forge-AI/monorepo#36650"]}, "text": f"{kind} #36650 at {INCIDENT_HEAD[:7]}: normal queue landing at that head only, not an incident fix, off the incident path: no squash", "at": actions.stamp(shell.clock)})
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert merges(shell) == []
    assert [line.split(" ", 1)[0] for line in escalations(shell) if "INCIDENT-TAG-CLEARED" in line] == ["INCIDENT-TAG-CLEARED"]


def test_a_drive_whose_checks_wait_for_a_go_squashes_an_approved_green_incident_fix_only_once_its_ruling_checks_pass(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    raw["landing"]["checks_wait_for_go"] = 34700
    config.write_text(json.dumps(raw))
    incident_pr(shell)
    shell.check_pr["36650 --incident"] = (1, "TEST-REMOVED tests/test_user_data.py\n")
    landing_pass(shell, config)
    assert merges(shell) == []
    del shell.check_pr["36650 --incident"]
    landing_pass(shell, config)
    assert merges(shell) == [["gh", "pr", "merge", "36650", "--repo", "Forge-AI/monorepo", "--squash", "--match-head-commit", INCIDENT_HEAD]]
    assert not [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]


def test_a_fix_the_runner_squashed_owes_its_release_after_the_executor_incident_naming_it_recovers(shell, config, tmp_path):
    """#41063: the executor record naming the fix settled before the pass that read its landing, so the runner owed no release for a fix it had squashed (8075b15 section 4 change 4)."""
    incident_pr(shell)
    shell.rows[0]["lane"] = LANE
    shell.bodies["36650"] = "Fixes the sand migrate check.\n"
    store = actions.Store()
    store.create(actions.Incident("api-1001", "executor", 1, actions.stamp(shell.clock), {"adopted": {"fix": LANE}, "repair_pr": 36650}))
    landing_pass(shell, config)
    assert len(merges(shell)) == 1
    path = store.path("api-1001")
    path.write_text(json.dumps(json.loads(path.read_text()) | {"status": "recovered"}))
    shell.sleep(60)
    shell.rows = [shell.rows[0] | {"state": "landed", "landed_sha": INCIDENT_SQUASH, "landed_at": actions.stamp(shell.now())}]
    landing_pass(shell, config)
    [release] = [post for post in shell.posts if post["kind"] == "release"]
    assert release["text"].startswith("#36650 landed as 9a8b7c6d5e as an incident fix. Start the release its squash needs now")
    assert json.loads(incident(tmp_path, "desk-landing").actions["release:36650"].target)["incident"]


@pytest.mark.parametrize(("buckets", "decision"), [(("pass", "pending"), "APPROVED"), (("pass", "fail"), "APPROVED"), (("pass", "pass"), "REVIEW_REQUIRED")], ids=["a-pending-check", "a-red-check", "no-approval"])
def test_an_incident_fix_short_of_approved_and_green_is_neither_merged_nor_enqueued(shell, config, tmp_path, buckets, decision):
    incident_pr(shell, buckets)
    shell.reviews["36650"]["reviewDecision"] = decision
    landing_pass(shell, config)
    assert merges(shell) == [] and shell.enqueues() == [] and shell.posts == []


def test_a_held_incident_fix_is_not_merged(shell, config, tmp_path):
    incident_pr(shell)
    root_hold(shell, 36650, text="hold #36650 root holds the sand fix")
    landing_pass(shell, config)
    assert merges(shell) == []


def test_an_incident_fix_check_pr_names_a_ruling_finding_for_is_refused_once_and_merges_when_it_clears(shell, config, tmp_path):
    incident_pr(shell)
    shell.check_pr["36650 --incident"] = (1, "RESERVED-NO-ANSWER reserved/user-data\tUser data stays where it is.\n")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert merges(shell) == []
    [blocker] = [post for post in shell.posts if post["kind"] == "blocker" and "RULINGS-REFUSED" in post["text"]]
    assert blocker["to"].split(",")[-1] == "root"
    assert blocker["text"] == f"#36650 {INCIDENT_HEAD[:10]}: RULINGS-REFUSED `rulings.py check-pr --incident` names these on the approved, green incident fix, and the runner does not merge it until they clear (45200f2): RESERVED-NO-ANSWER reserved/user-data\tUser data stays where it is."
    assert f"land:36650@{INCIDENT_HEAD}" not in incident(tmp_path, "desk-landing").actions
    del shell.check_pr["36650 --incident"]
    landing_pass(shell, config)
    assert len(merges(shell)) == 1
    assert incident(tmp_path, "desk-landing").actions[f"land:36650@{INCIDENT_HEAD}"].status == "completed"


def test_an_incident_fix_merges_past_every_cause_but_the_three_it_must_meet_before_merge(shell, config, tmp_path):
    incident_pr(shell)
    shell.check_pr["36650 --incident"] = (0, "BLOCKER the finder record is read after the merge\n")
    shell.check_pr["36650"] = (1, "FINDER-MAJOR ffff666\n")
    landing_pass(shell, config)
    assert len(merges(shell)) == 1
    [check] = [call for call in shell.calls if len(call) > 2 and Path(call[1]).name == "rulings.py"]
    assert check[2] == "check-pr" and "--incident" in check


def test_a_hold_posted_after_the_pass_read_its_holds_stops_the_incident_squash(shell, config, tmp_path):
    incident_pr(shell)
    inner = shell.rulings

    def rulings(argv: list[str]) -> runner_module.Done:
        if not any(record["kind"] == "hold" for record in shell.records):
            root_hold(shell, 36650, text="hold #36650 the owner stopped every merge")
        return inner(argv)

    shell.rulings = rulings
    landing_pass(shell, config)
    assert merges(shell) == []


@pytest.mark.parametrize("late", [2, 66])
def test_an_incident_fix_still_ungraded_thirty_minutes_after_its_squash_escalates_once(shell, config, tmp_path, late):
    incident_pr(shell)
    landing_pass(shell, config)
    assert len(merges(shell)) == 1
    finder_verdict(shell, "36650", f"ok {INCIDENT_HEAD[:7]} no findings")
    shell.sleep(29 * 60)
    landing_pass(shell, config)
    assert not [line for line in escalations(shell) if line.startswith("UNGRADED-30")]
    shell.sleep(late * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [line for line in escalations(shell) if line.startswith("UNGRADED-30")] == [
        f"UNGRADED-30 ungraded-30:36650@{INCIDENT_HEAD} #36650: #36650 merged as an incident fix at ffff666666 {29 + late} minutes ago and has no fidelity ok at that head; both grades are owed within 30 minutes of the merge (d968e1d)"
    ]


def gone_green(shell: FakeShell, gate_line: str) -> None:
    shell.rows = [{"pr": "33001", "lane": LANE, "branch": "g/1", "base": "dev", "head": "aaaa111111", "state": "open", "reported_head": "aaaa111111", "test_state": "success"}]
    shell.gates["33001"] = [gate(gate_line, would="#33001" if "GREEN" in gate_line else "")]
    shell.enqueue_out["33001"] = (0, "enqueue #33001: {}\n")
    grade(shell, "33001", "ok F307 at aaaa111.")
    merge_go(shell, "33001", "merge #33001 at aaaa111")


def gone_failing(shell: FakeShell) -> None:
    gone_green(shell, "#33001 GREEN aaaa111111 graphite READY")
    shell.enqueue_out["33001"] = (5, "graphite refused the enqueue: the API answered 422\n")


def unqueued(shell: FakeShell) -> list[str]:
    return [line for line in said(shell) if line.startswith("GO-UNQUEUED")]


def test_a_green_head_a_go_names_whose_enqueue_failed_escalates_go_unqueued_once_five_minutes_on(shell, config, tmp_path):
    merge_mode(config, "every")
    gone_failing(shell)
    shell.queue["33001"] = "not queued"
    shell.sleep(4 * 60)
    landing_pass(shell, config)
    assert unqueued(shell) == []
    shell.sleep(2 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    [line] = unqueued(shell)
    assert line.startswith("GO-UNQUEUED go-unqueued:33001 #33001: #33001 is green at aaaa111111 and root go #")
    assert "yet it is not in the queue after 6 minutes; its newest enqueue since the go ended failed" in line


@pytest.mark.parametrize("status", ["WAITING_TO_MERGE", "QUEUED_TO_MERGE", "FAILURE_HANDLING"])
def test_a_go_named_head_graphite_reads_queued_draws_no_go_unqueued_though_the_status_read_says_not_queued(shell, config, tmp_path, status):
    merge_mode(config, "every")
    gone_failing(shell)
    shell.queue["33001"] = "not queued"
    shell.statuses = f"#33001 {status} aaaa111111 base graphite-base/33001\n"
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert unqueued(shell) == []
    assert ["--status"] in [call[-1:] for call in shell.calls if Path(call[0]).name == "stack-enqueue"]


def test_a_green_head_a_go_names_that_the_runner_has_not_enqueued_yet_is_backlog_and_draws_no_go_unqueued(shell, config, tmp_path):
    merge_mode(config, "every")
    gone_green(shell, "#33001 BLOCKED aaaa111111 merge conflict (graphite CONFLICT)")
    shell.queue["33001"] = "not queued"
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert unqueued(shell) == []


def test_a_go_named_head_the_landing_desk_blocked_draws_no_go_unqueued(shell, config, tmp_path):
    merge_mode(config, "every")
    desk_lane(config)
    gone_green(shell, "#33001 BLOCKED aaaa111111 merge conflict (graphite CONFLICT)")
    shell.queue["33001"] = "not queued"
    shell.sleep(60)
    blocker = f"{LANE}: #33001 aaaa111 sits on closed #33000's untracked branch; run ccx vcs stack submit from #33001's worktree, post HEAD"
    shell.records.append({"seq": 34900, "drive": "d1", "lane": DESK, "kind": "blocker", "to": [LANE, "root"], "refs": {}, "text": blocker, "at": actions.stamp(shell.clock), "status": "open"})
    shell.sleep(5 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert unqueued(shell) == []


HELD_TWO = "🛑 Held — 2 blocking open; security: high; confidentiality: high"


def test_a_go_named_green_head_the_review_bot_holds_tells_its_lane_once_and_draws_no_go_unqueued(shell, config, tmp_path):
    merge_mode(config, "every")
    gone_green(shell, "#33001 BLOCKED aaaa111111 merge conflict (graphite CONFLICT)")
    shell.queue["33001"] = "not queued"
    shell.review_runs["aaaa111111"] = ["ℹ️ Waiting for restack — push or comment @forge-pr-reviewer review", HELD_TWO]
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    shell.gates["33001"] = [gate("#33001 GREEN aaaa111111 graphite READY", would="#33001")]
    shell.enqueue_out["33001"] = (5, "graphite refused the enqueue: the API answered 422\n")
    landing_pass(shell, config)
    assert unqueued(shell) == []
    assert [post_body(post) for post in blockers(shell) if "review bot" in post_body(post)] == [f"#33001 aaaa111111: the review bot holds it on 2 blocking findings; {LANE} fixes or accepts them"]


def test_a_go_named_green_head_the_review_bot_waits_to_see_restacked_says_so_in_go_unqueued_and_tells_no_lane(shell, config, tmp_path):
    merge_mode(config, "every")
    gone_failing(shell)
    shell.queue["33001"] = "not queued"
    shell.review_runs["aaaa111111"] = [HELD_TWO, "ℹ️ Waiting for restack — push or comment @forge-pr-reviewer review"]
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    [line] = unqueued(shell)
    assert "minutes; the review bot waits for a restack push; owner lane " in line
    assert [post for post in blockers(shell) if "review bot" in post_body(post)] == []


def test_a_go_named_head_that_waits_on_a_reserved_row_tap_draws_no_go_unqueued(shell, config, tmp_path):
    merge_mode(config, "every")
    gone_green(shell, "#33001 GREEN aaaa111111 graphite READY")
    shell.check_pr["33001"] = (1, RESERVED)
    shell.queue["33001"] = "not queued"
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert unqueued(shell) == []


def test_a_go_named_green_head_above_a_parent_no_go_names_draws_one_below_blocked_note_and_no_go_unqueued(shell, config, tmp_path):
    board_surface(config)
    two_pr_stack(shell)
    shell.board_rows = [("29020", "cccc333333"), ("29031", "dddd444444")]
    shell.rows[1] = dict(shell.rows[1], test_state="success")
    shell.queue["29031"] = "not queued"
    merge_go(shell, "29031", "merge #29031 at dddd444")
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert unqueued(shell) == []
    assert [post_body(post) for post in notes(shell) if post["to"] == "root"] == [ungone_parents("29020", "#29031 at dddd444444", "#29020 at cccc333333")]


@pytest.mark.parametrize("broken", [{"test_state": "failure"}, {"test_state": "success", "mergeable_state": "dirty"}])
def test_a_go_named_green_head_above_a_red_or_conflicting_parent_with_its_own_go_draws_one_below_blocked_note_and_no_go_unqueued(shell, config, tmp_path, broken):
    board_surface(config)
    two_pr_stack(shell)
    shell.rows[0] = dict(shell.rows[0], **broken)
    shell.rows[1] = dict(shell.rows[1], test_state="success")
    shell.queue["29031"] = "not queued"
    merge_go(shell, "29020", "merge #29020 at cccc333")
    merge_go(shell, "29031", "merge #29031 at dddd444")
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert unqueued(shell) == []
    assert [post_body(post) for post in notes(shell) if post["to"] == "root"] == [
        "BELOW-BLOCKED above #29020: root gos name #29031 at dddd444444, stacked on #29020 at cccc333333, red on CI or conflicting with its base, so none of them is enqueued, and the stack below #29020 lands without them; each red or conflicting parent's lane makes it green and mergeable"
    ]


@pytest.mark.parametrize("parent_go", [True, False], ids=["parent-go", "no-parent-go"])
def test_a_go_named_green_head_above_a_held_parent_draws_one_below_held_note_and_no_go_unqueued(shell, config, tmp_path, parent_go):
    board_surface(config)
    two_pr_stack(shell)
    root_hold(shell, 29020, text="hold #29020 owner gate on the old app")
    shell.rows = [dict(row, test_state="success") for row in shell.rows]
    shell.queue["29031"] = "not queued"
    if parent_go:
        merge_go(shell, "29020", "merge #29020 at cccc333")
    merge_go(shell, "29031", "merge #29031 at dddd444")
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert unqueued(shell) == []
    assert [post_body(post) for post in notes(shell) if post["to"] == "root"] == [
        "BELOW-HELD above #29020: root gos name #29031 at dddd444444, stacked on #29020 at cccc333333, which a hold covers, so none of them is enqueued, and the stack below #29020 lands without them; the stack waits for each hold to lift"
    ]


def test_a_head_the_runner_refused_draws_no_go_unqueued(shell, config, tmp_path):
    tap_ruled_pr(shell, config, ["go/sand/next/vm/run_test.go", "go/sand/next/vm/run.go"])
    shell.rows = [dict(row, test_state="success") for row in shell.rows]
    shell.queue["29020"] = "not queued"
    grade(shell, "29020", TEST_ONLY_GRADE)
    merge_go(shell, "29020", "go test-only #29020 at cccc333")
    landing_pass(shell, config)
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [post for post in blockers(shell) if "TEST-ONLY-MISMATCH" in post_body(post)] and unqueued(shell) == []


def test_a_go_named_head_the_queue_evicted_after_its_enqueue_went_in_draws_go_unqueued_naming_the_eviction(shell, config, tmp_path):
    merge_mode(config, "every")
    gone_green(shell, "#33001 GREEN aaaa111111 graphite READY")
    shell.queue["33001"] = "not queued"
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1
    shell.queue["33001"] = "evicted"
    shell.pr_status["33001"] = {"evicted": "the batch it was included in timed out", "evicted_at": "Oct 30, 1:58 AM UTC"}
    shell.statuses = "#33001 WAITING_TO_MERGE aaaa111111\n"
    shell.gates["33001"] = [gate("#33001 QUEUED aaaa111111 graphite WAITING_TO_MERGE", would="")]
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    [line] = unqueued(shell)
    assert "the queue let it go at Oct 30, 1:58 AM UTC after its newest enqueue since the go (" in line and "): the batch it was included in timed out" in line


def test_a_go_named_head_whose_queue_draft_closed_is_dequeued_once_and_never_recovered(shell, config, tmp_path):
    merge_mode(config, "every")
    gone_green(shell, "#33001 BLOCKED aaaa111111 queue head orphaned: its queue draft #45279 closed unmerged 201 minutes ago and the PR is still queued; rerun stack-enqueue 33001 --recover")
    shell.queue["33001"] = "not queued"
    landing_pass(shell, config)
    landing_pass(shell, config)
    calls = [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]
    assert [call[1:] for call in calls if "--dequeue" in call] == [["33001", "--dequeue"]]
    assert not [call for call in calls if "--recover" in call]
    assert [post for post in shell.posts if post["text"].startswith("ORPHAN-RELEASED #33001 at aaaa111111")]
    assert not [post for post in blockers(shell) if "orphaned" in post_body(post)]


EVICTED_GATE = "#33001 BLOCKED aaaa111111 graphite EVICTED; run ccx vcs stack submit from the branch's worktree: cd /w/g1 && ccx vcs stack submit"


def test_a_go_named_head_the_queue_evicted_before_any_enqueue_gets_one_blocker_to_root_and_its_lane_naming_the_eviction(shell, config, tmp_path):
    """Root #208430: #45286's gate read `graphite EVICTED` every pass from 6:42 PM, no rule routed that verdict, and nothing said why it sat out for 2 h 21 m."""
    merge_mode(config, "every")
    gone_green(shell, EVICTED_GATE)
    shell.queue["33001"] = "evicted"
    shell.pr_status["33001"] = {"evicted": "the batch it was included in timed out", "evicted_at": "Oct 30, 1:42 AM UTC"}
    landing_pass(shell, config)
    landing_pass(shell, config)
    [post] = blockers(shell)
    assert "root" in post["to"].split(",") and LANE in post["to"].split(",")
    assert post_body(post) == "#33001 aaaa111111: graphite EVICTED; run ccx vcs stack submit from the branch's worktree: cd /w/g1 && ccx vcs stack submit; the queue dropped it at Oct 30, 1:42 AM UTC: the batch it was included in timed out"
    assert shell.enqueues() == []


def skipped(shell: FakeShell) -> list[str]:
    return [line for line in said(shell) if line.startswith("ADMITTED-SKIPPED")]


def test_an_admitted_head_no_record_accounts_for_draws_one_admitted_skipped_naming_the_branch_ten_minutes_on(shell, config, tmp_path):
    """Owner 5:16 AM Oct 30 on #45286, admitted and unqueued for two hours with no record: "make a durable fix for this not jsut a done-off" (root #208560). The gate reads a verdict no rule routes, so the pass leaves it out silently."""
    merge_mode(config, "every")
    gone_green(shell, "#33001 BLOCKED aaaa111111 merge conflict (graphite CONFLICT)")
    shell.queue["33001"] = "not queued"
    shell.sleep(9 * 60)
    landing_pass(shell, config)
    assert skipped(shell) == []
    shell.sleep(2 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    [line] = skipped(shell)
    assert line.startswith("ADMITTED-SKIPPED admitted-skipped:33001 #33001: #33001 is green at aaaa111111 and root go #")
    assert "yet no record says it is queued, merging or refused: the gate read aaaa111111 BLOCKED: merge conflict (graphite CONFLICT); owner lane api-fix" in line
    assert "root" in heard(shell, "ADMITTED-SKIPPED")[0].split(",")


def test_an_admitted_head_its_lane_was_told_about_draws_no_admitted_skipped(shell, config, tmp_path):
    merge_mode(config, "every")
    gone_green(shell, EVICTED_GATE)
    shell.queue["33001"] = "evicted"
    shell.sleep(11 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert len(blockers(shell)) == 1 and skipped(shell) == []


def test_a_go_whose_newest_enqueue_shows_it_queued_draws_no_go_unqueued(shell, config, tmp_path):
    merge_mode(config, "every")
    gone_green(shell, "#33001 GREEN aaaa111111 graphite READY")
    shell.queue["33001"] = "not queued"
    shell.gates["33001"] = [gate("#33001 GREEN aaaa111111 graphite READY", would="#33001"), gate("#33001 QUEUED aaaa111111 graphite QUEUED_TO_MERGE nothing to enqueue: the queue already holds the whole stack", would="")]
    landing_pass(shell, config)
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert unqueued(shell) == []


def test_go_named_green_heads_out_of_the_queue_in_one_stack_draw_one_go_unqueued_naming_the_bottom(shell, config, tmp_path):
    merge_mode(config, "every")
    two_pr_stack(shell)
    shell.rows = [dict(row, test_state="success") for row in shell.rows]
    shell.enqueue_out["29031"] = (5, "graphite refused the enqueue: the API answered 422\n")
    shell.queue["29020"] = shell.queue["29031"] = "not queued"
    merge_go(shell, "29020", "merge #29020 at cccc333")
    merge_go(shell, "29031", "merge #29031 at dddd444")
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    [line] = unqueued(shell)
    assert line.startswith("GO-UNQUEUED go-unqueued:29020 #29020: #29020 is green at cccc333333 and root go #")
    assert "; #29031 above it in the stack from #29020 wait on it too; owner lane " in line


def test_a_go_a_fidelity_carry_moved_to_a_new_head_counts_its_five_minutes_from_the_carry_grade(shell, config, tmp_path):
    merge_mode(config, "every")
    shell.rows = [{"pr": "33001", "lane": LANE, "branch": "g/1", "base": "dev", "head": "aaaa111111", "state": "open", "reported_head": "aaaa111111", "test_state": "success"}]
    shell.gates["33001"] = [gate("#33001 GREEN aaaa111111 graphite READY", would="#33001")]
    shell.enqueue_out["33001"] = (5, "graphite refused the enqueue: the API answered 422\n")
    shell.queue["33001"] = "not queued"
    merge_go(shell, "33001", "merge #33001 at 9999999")
    shell.sleep(10 * 60)
    grade(shell, "33001", "ok aaaa111111 carried from 9999999, patch-identical replay onto dev.")
    shell.sleep(3 * 60)
    landing_pass(shell, config)
    assert unqueued(shell) == []
    shell.sleep(3 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    [line] = unqueued(shell)
    assert "yet it is not in the queue after 6 minutes" in line


def test_go_unqueued_never_counts_an_enqueue_attempt_from_before_the_go(shell, config, tmp_path):
    merge_mode(config, "every")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    book, key = runner_module.Landing(runner, runner.config.landing).book, "enqueue:33001@aaaa111111#1"
    book.accept(runner_module.LANDING, key, "enqueue", "33001", "", shell.now() + timedelta(minutes=15))
    book.attempt(runner_module.LANDING, lambda incident: incident.start(key, shell.now()))
    book.attempt(runner_module.LANDING, lambda incident: incident.complete(key, {"outcome": "blocked", "out": "the merge-mode check refuses #33001", "at": book.stamp()}))
    shell.sleep(60)
    gone_green(shell, "#33001 BLOCKED aaaa111111 merge conflict (graphite CONFLICT)")
    shell.queue["33001"] = "not queued"
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    assert unqueued(shell) == []


@pytest.mark.parametrize(("version", "minutes", "retried"), [(None, 0, True), (runner_module.RUNNING_VERSION, 0, False), (runner_module.RUNNING_VERSION, 10, True)], ids=["older-runner", "same-runner-fresh", "same-runner-10-min"])
def test_a_failed_enqueue_at_unchanged_heads_retries_under_a_new_runner_or_ten_minutes_on(shell, config, tmp_path, version, minutes, retried):
    merge_mode(config, "every")
    gone_green(shell, "#33001 GREEN aaaa111111 graphite READY")
    shell.queue["33001"] = "not queued"
    shell.sleep(6 * 60)
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    book, key = runner_module.Landing(runner, runner.config.landing).book, "enqueue:33001@aaaa111111#1"
    book.accept(runner_module.LANDING, key, "enqueue", "33001", "", shell.now() + timedelta(minutes=15))
    book.attempt(runner_module.LANDING, lambda incident: incident.start(key, shell.now()))
    book.attempt(runner_module.LANDING, lambda incident: incident.complete(key, {"outcome": "failed", "out": "GraphQL: This pull request is part of a stack", "at": book.stamp()} | ({"version": version} if version else {})))
    shell.sleep(minutes * 60)
    landing_pass(shell, config)
    assert any(action.action_id == "enqueue:33001@aaaa111111#2" for action in book.actions(runner_module.LANDING, kind="enqueue")) == retried


def test_a_go_named_head_the_queue_already_holds_from_a_hand_enqueue_escalates_nothing(shell, config, tmp_path):
    merge_mode(config, "every")
    gone_green(shell, "#33001 BLOCKED aaaa111111 graphite QUEUED_TO_MERGE")
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == [] and unqueued(shell) == []


@pytest.mark.parametrize("withdrawal", [lambda go: {"text": f"go #{go} on #33001 aaaa111 is withdrawn: both grades it cited were withdrawn"}, lambda go: {"re": go, "text": "Withdrawn: the finder found a blocker at aaaa111"}])
def test_a_go_the_root_withdraws_by_naming_it_neither_enqueues_nor_escalates(shell, config, tmp_path, withdrawal):
    merge_mode(config, "every")
    gone_green(shell, "#33001 GREEN aaaa111111 graphite READY")
    shell.queue["33001"] = "not queued"
    go = shell.records[-1]["seq"]
    shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": "root", "kind": "decide", "to": ["landing-desk"], "refs": {}, "at": actions.stamp(shell.clock)} | withdrawal(go))
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == [] and unqueued(shell) == []


@pytest.mark.parametrize("text", ["go #{go} is not withdrawn; the grade it cited stands", "Answering go #{go}: the fidelity grade #140953 it cited was withdrawn. The go stands."])
def test_a_root_record_that_mentions_a_go_beside_a_withdrawal_leaves_the_go_standing(shell, config, tmp_path, text):
    merge_mode(config, "every")
    gone_green(shell, "#33001 GREEN aaaa111111 graphite READY")
    go = shell.records[-1]["seq"]
    shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": "root", "kind": "decide", "to": ["landing-desk"], "refs": {}, "re": go, "text": text.format(go=go), "at": actions.stamp(shell.clock)})
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["33001", "--partial"]]


def test_a_green_head_a_go_names_that_enqueues_escalates_nothing(shell, config, tmp_path):
    merge_mode(config, "every")
    gone_green(shell, "#33001 GREEN aaaa111111 graphite READY")
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["33001", "--partial"]]
    assert unqueued(shell) == []


def test_a_pr_landed_with_no_enqueue_of_the_runners_own_posts_landed_to_its_lane_once(shell, config, tmp_path):
    shell.rows = single_row()
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 graphite CONFLICTING")]
    shell.pr_status["29020"] = {"queue": "not queued", "verdict": "blocked:ci-pending"}
    landing_pass(shell, config)
    shell.rows[0] |= {"state": "landed", "landed_sha": "dddd444444"}
    landing_pass(shell, config)
    landing_pass(shell, config)
    [landed] = [post for post in shell.posts if post["text"].startswith("LANDED")]
    assert (landed["kind"], landed["to"], landed["id"]) == ("landed", f"root,{LANE}", "landed:29020")
    assert landed["text"].startswith("LANDED #29020 as dddd444444: merged into dev with no enqueue of the landing runner's own")


def test_a_landing_removes_each_worktree_on_the_landed_branch_once_and_logs_one_it_refuses_once(shell, config, tmp_path):
    shell.rows = single_row()
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 graphite CONFLICTING")]
    shell.pr_status["29020"] = {"queue": "not queued", "verdict": "blocked:ci-pending"}
    clean, dirty = str(tmp_path / "worktrees" / "a-3"), str(tmp_path / "worktrees" / "a-3-copy")
    shell.listed = [(str(tmp_path / "checkout"), "dev"), (clean, "a/3"), (dirty, "a/3"), (str(tmp_path / "worktrees" / "other"), "b/1"), (str(tmp_path / "worktrees" / "detached"), "")]
    shell.kept = {dirty: f"ccx: worktree rm: {dirty}: uncommitted changes: notes.md"}
    landing_pass(shell, config)
    assert shell.removed == []
    shell.rows[0] |= {"state": "landed", "landed_sha": "dddd444444"}
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.removed == [clean, dirty]
    assert [line for line in escalations(shell) if line.startswith("WORKTREE-KEPT")] == [
        f"WORKTREE-KEPT worktree-kept:{dirty} #29020: {dirty} stays on landed #29020's branch: ccx: worktree rm: {dirty}: uncommitted changes: notes.md"
    ]


def test_a_handoff_stops_worktree_removal_between_worktrees(shell, config, tmp_path):
    """Oct 29 6:00 PM: a 0.7.560 landing runner kept removing landed worktrees one by one for over ten minutes after `handoff` asked it to stop."""
    shell.rows = [single_row()[0] | {"state": "landed", "landed_sha": "dddd444444"}]
    first, second = str(tmp_path / "worktrees" / "a-3"), str(tmp_path / "worktrees" / "a-3-copy")
    shell.listed = [(first, "a/3"), (second, "a/3")]
    landing = landing_of(shell, config, tmp_path)
    run = shell.run

    def handed_off(argv, *args, **kwargs):
        done = run(argv, *args, **kwargs)
        if argv[:4] == ["ccx", "vcs", "worktree", "rm"]:
            landing.runner.stop_marker().write_text(str(os.getpid()))
        return done

    shell.run = handed_off
    with pytest.raises(runner_module.StopRequested):
        landing.remove_landed_worktrees({row["pr"]: row for row in shell.rows})
    assert shell.removed == [first]


def test_a_slow_worktree_removal_holds_back_none_of_the_side_work(shell, config, tmp_path, monkeypatch):
    """Oct 29: the 0.7.584 landing runner spent 481 s in 24 `ccx vcs worktree rm` calls on the side worker, so the releases and restack checks queued behind them waited minutes."""
    shell.rows = [single_row()[0] | {"state": "landed", "landed_sha": "dddd444444"}]
    shell.listed = [(str(tmp_path / "worktrees" / "a-3"), "a/3")]
    checked = threading.Event()
    monkeypatch.setattr(runner_module.Landing, "verify_restacks", lambda self, rows: checked.set())
    run = shell.run
    waited = []

    def slow(argv, *args, **kwargs):
        if argv[:4] == ["ccx", "vcs", "worktree", "rm"]:
            waited.append(checked.wait(5))
        return run(argv, *args, **kwargs)

    shell.run = slow
    landing_pass(shell, config)
    assert waited == [True] and shell.removed == [str(tmp_path / "worktrees" / "a-3")]


def test_a_runner_enqueue_posts_queued_and_then_landed_naming_the_go(shell, config, tmp_path):
    merge_mode(config, "every")
    gone_green(shell, "#33001 GREEN aaaa111111 graphite READY")
    go = shell.records[-1]["seq"]
    landing_pass(shell, config)
    [queued] = [post for post in shell.posts if post["text"].startswith("QUEUED")]
    assert queued["kind"] == "report" and queued["to"].startswith("root") and queued["text"].startswith(f"QUEUED #33001 at aaaa111111 (go #{go}): the landing runner put it in the merge queue at ")
    shell.rows[0] |= {"state": "landed", "landed_sha": "dddd444444"}
    landing_pass(shell, config)
    landing_pass(shell, config)
    [landed] = [post for post in shell.posts if post["text"].startswith("LANDED")]
    assert landed["kind"] == "landed" and landed["text"].startswith("LANDED #33001 as dddd444444: through the merge queue, which the landing runner entered at ")


def test_a_pr_the_runner_enqueued_three_times_posts_landed_once_for_its_one_squash_naming_the_newest_entry(shell, config, tmp_path):
    """Oct 30 12:56 AM, p8a-step7 #202436: #45368 landed once as d75a03ed and the runner posted LANDED three times (#202421, #202424, #202435), one per queue entry it had recorded at 20:17, 21:07 and 00:46."""
    merge_mode(config, "every")
    gone_green(shell, "#33001 GREEN aaaa111111 graphite READY")
    go = shell.records[-1]["seq"]
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    book = runner_module.Landing(runner, runner.config.landing).book
    for attempt, head in enumerate(["ffff000000", "aaaa111111", "aaaa111111"], 1):
        key = f"enqueue:33001@{head}#{attempt}"
        book.accept(runner_module.LANDING, key, "enqueue", "33001", "", shell.now() + timedelta(minutes=15))
        book.attempt(runner_module.LANDING, lambda incident, key=key: incident.start(key, shell.now()))
        book.attempt(runner_module.LANDING, lambda incident, key=key: incident.complete(key, {"outcome": "enqueued", "out": "enqueue #33001: {}", "at": book.stamp()}))
        shell.sleep(60)
    shell.rows[0] |= {"state": "landed", "landed_sha": "dddd444444"}
    landing_pass(shell, config)
    landing_pass(shell, config)
    [landed] = [post for post in shell.posts if post["text"].startswith("LANDED")]
    assert landed["text"].startswith("LANDED #33001 as dddd444444: through the merge queue") and landed["text"].endswith(f"with #33001 at aaaa111111 (go #{go})")
    assert {action.status for action in book.actions(runner_module.LANDING, kind="enqueue")} == {"verified"}


def test_an_incident_fix_graded_by_both_within_thirty_minutes_escalates_nothing(shell, config, tmp_path):
    incident_pr(shell)
    landing_pass(shell, config)
    grade(shell, "36650", f"ok {INCIDENT_HEAD[:7]}")
    shell.sleep(31 * 60)
    landing_pass(shell, config)
    assert not [line for line in escalations(shell) if line.startswith("UNGRADED-30")]


def live_clock(tmp_path: Path, shell: FakeShell, lane: str = "sand-fix") -> None:
    clock = {"key": "R1", "launched": actions.stamp(shell.clock), "reached": {}, "ended": ""}
    actions.Store(tmp_path / "store").create(actions.Incident(f"desk-lane-{lane}", "ctx_sand", 0, actions.stamp(shell.clock), {"incident-clock": clock}))


def land_incident(shell: FakeShell, config: Path, body: str = "Fixes the sand migrate check.\n") -> None:
    land_fast(shell, config, body=body, labels="externally-merged", lane=INCIDENT_LANE)


def test_an_incident_fix_landing_owes_its_release_with_no_label_or_release_with_line_and_presses_every_three_minutes_while_a_clock_runs(shell, config, tmp_path):
    live_clock(tmp_path, shell)
    land_incident(shell, config)
    landing_pass(shell, config)
    [release] = shell.posts
    assert (release["kind"], release["to"], release["id"]) == ("release", "peer:sole-51,root", "release:36642:0")
    assert release["text"].startswith("#36642 landed as 5e11a9d0c2 as an incident fix. Start the release its squash needs now, then run `cci post --drive d1 ")
    assert "--target <component> --build <url> --text <build>" in release["text"]
    shell.sleep(3 * 60)
    landing_pass(shell, config)
    [late] = blockers(shell)
    assert late["text"].startswith("#36642 5e11a9d0c2: landed 3 minutes ago as an incident fix, and no cci `release` record since targets a component or names #36642. Start the release its squash needs now and post `release --build <url> --target <component>`") and "repeats every 3 minutes" in late["text"]


def clearing_record(shell: FakeShell, pr: str, kind: str, seq: int) -> None:
    text = "no release is owed for it" if kind == "release" else "not an incident fix, off the incident path: no squash"
    shell.records.append({"seq": seq, "drive": "d1", "lane": "root", "kind": kind, "status": "open", "to": ["desk-runner"], "refs": {"prs": [f"Forge-AI/monorepo#{pr}"]}, "text": f"#{pr}: {text}", "at": actions.stamp(shell.clock)})


@pytest.mark.parametrize("kind", ["go", "decide", "release"])
def test_a_squashed_fix_whose_incident_tag_is_cleared_before_its_landing_is_read_owes_no_release(shell, config, tmp_path, kind):
    """#44193: the runner squashed a fix, a root decide then took it off the incident path, and the runner still posted the release its squash owed and pressed it every 10 minutes."""
    incident_pr(shell)
    shell.bodies["36650"] = "Fixes the sand migrate check.\n"
    landing_pass(shell, config)
    assert len(merges(shell)) == 1
    clearing_record(shell, "36650", kind, 9502)
    shell.sleep(60)
    shell.rows = [shell.rows[0] | {"state": "landed", "landed_sha": INCIDENT_SQUASH, "landed_at": actions.stamp(shell.now())}]
    landing_pass(shell, config)
    shell.sleep(15 * 60)
    landing_pass(shell, config)
    assert [post for post in shell.posts if post["kind"] in ("release", "blocker")] == []


@pytest.mark.parametrize("kind", ["go", "decide"])
def test_an_incident_landing_whose_tag_is_cleared_after_its_release_record_stops_its_blockers(shell, config, tmp_path, kind):
    live_clock(tmp_path, shell)
    land_incident(shell, config)
    landing_pass(shell, config)
    assert [post["kind"] for post in shell.posts] == ["release"]
    clearing_record(shell, "36642", kind, 9503)
    shell.sleep(3 * 60)
    landing_pass(shell, config)
    assert blockers(shell) == []
    assert incident(tmp_path, "desk-landing").actions["release:36642"].verification_receipt["cleared"]


def test_an_incident_fix_landing_settles_only_on_a_release_record_with_a_build(shell, config, tmp_path):
    live_clock(tmp_path, shell)
    land_incident(shell, config, FAST_BODY)
    landing_pass(shell, config)
    cci_record(shell, kind="answer", re=1, at=actions.stamp(shell.now()), text="the root starts the sand release")
    cci_record(shell, lane=WALKER, kind="release", refs={"targets": ["sand"]}, at=actions.stamp(shell.now()), text="sand release starting")
    shell.sleep(3 * 60)
    landing_pass(shell, config)
    assert len(blockers(shell)) == 1 and release_action(tmp_path).status == "completed"
    seq = cci_record(shell, lane=WALKER, kind="release", refs={"targets": ["sand"], "builds": ["https://buildkite.com/forge/release/builds/8748"]}, at=actions.stamp(shell.now()), text="CUT build 8748 (releases/2026-10-20/33)")
    shell.sleep(3 * 60)
    landing_pass(shell, config)
    released = release_action(tmp_path)
    assert len(blockers(shell)) == 1 and (released.status, released.verification_receipt["cci"], released.verification_receipt["release"]) == ("verified", seq, "2026-10-20/33")


@pytest.mark.parametrize(
    ("prs", "text", "settles"),
    [
        (["Forge-AI/monorepo#36642"], "release: rides release 2's sand cut", True),
        (["Forge-AI/monorepo#36650"], "release: rides release 2's sand cut", False),
        (["Forge-AI/monorepo#36650"], "release: #36650 and #36642 ride release 2's sand cut", True),
    ],
    ids=["its-pr", "another-pr", "named-in-text"],
)
def test_an_incident_fix_landing_settles_on_a_root_release_record_naming_its_pr_and_the_release_that_carries_it(shell, config, tmp_path, prs, text, settles):
    live_clock(tmp_path, shell)
    land_incident(shell, config, FAST_BODY)
    landing_pass(shell, config)
    seq = cci_record(shell, kind="release", refs={"prs": prs, "targets": ["sand"]}, at=actions.stamp(shell.now()), text=text)
    shell.sleep(3 * 60)
    landing_pass(shell, config)
    released = release_action(tmp_path)
    assert (len(blockers(shell)), released.status, (released.verification_receipt or {}).get("cci")) == ((0, "verified", seq) if settles else (1, "completed", None))


def test_a_root_hold_on_a_landed_incident_fix_is_answered_once_and_the_cadence_holds(shell, config, tmp_path):
    live_clock(tmp_path, shell)
    land_incident(shell, config, FAST_BODY)
    landing_pass(shell, config)
    seq = cci_record(shell, kind="hold", status="open", refs={"prs": [36642]}, at=actions.stamp(shell.now()), text="hold #36642: one cut with #41071 when that lands")
    for _ in range(2):
        shell.sleep(3 * 60)
        landing_pass(shell, config)
    held = [line for line in escalations(shell) if line.startswith("RELEASE-HELD-AFTER-LANDING")]
    assert held == [f"RELEASE-HELD-AFTER-LANDING #36642: landed 5e11a9d0c2 at 11:03; its cut is owed now, the other PR gets its own cut when it lands (root #{seq})"]
    assert [post["text"].split(": ", 1)[1].split(" as ")[0] for post in blockers(shell) if post["text"].startswith("#36642 ")] == ["landed 3 minutes ago", "landed 6 minutes ago"]


@pytest.mark.parametrize(
    ("record", "held"),
    [
        ({"kind": "hold", "status": "open", "to": ["sole-51"], "refs": {}, "text": "apply nothing more on plat until root lifts this"}, True),
        ({"kind": "hold", "status": "closed", "refs": {"prs": [36642]}, "text": "hold #36642: one cut with #41071"}, False),
        ({"kind": "decide", "status": "open", "refs": {"prs": [36642]}, "text": "no holds: cut #36642 the moment it lands"}, False),
        ({"kind": "go", "to": ["sole-51"], "text": "wait for #41071, then cut both"}, False),
    ],
    ids=["an-open-hold-to-the-release-owner", "a-resolved-hold", "a-decide-naming-the-pr", "a-go-telling-the-owner-to-wait"],
)
def test_only_an_open_root_hold_record_holds_a_landed_incident_fix(shell, config, tmp_path, record, held):
    live_clock(tmp_path, shell)
    land_incident(shell, config, FAST_BODY)
    landing_pass(shell, config)
    cci_record(shell, at=actions.stamp(shell.now()), **record)
    shell.sleep(3 * 60)
    landing_pass(shell, config)
    assert len([line for line in escalations(shell) if line.startswith("RELEASE-HELD-AFTER-LANDING")]) == held


@pytest.mark.parametrize(("text", "release"), [("CUT build 8743 (releases/2026-10-20/31)", "2026-10-20/31"), ("CUT build 8743", "8743")], ids=["named-release", "build-number-only"])
def test_a_released_build_posts_one_released_line_to_the_root_alone_once_every_component_is_live(shell, config, tmp_path, text, release):
    raw = json.loads(config.read_text())
    raw["landing"]["comms_lane"] = "comms-desk"
    config.write_text(json.dumps(raw))
    land_incident(shell, config, FAST_BODY)
    landing_pass(shell, config)
    build = "https://buildkite.com/forge/release/builds/8743"
    cci_record(shell, lane=WALKER, kind="release", refs={"targets": ["sand"], "builds": [build]}, at=actions.stamp(shell.now()), text=text)
    shell.live[release] = {"sand": {"live": "not live"}}
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [post for post in shell.posts if post["kind"] == "fix-live"] == []
    shell.live[release] = {"sand": {"live": "0.162.1437", "since": "2026-10-20T18:02:18Z"}}
    landing_pass(shell, config)
    landing_pass(shell, config)
    [line] = [post for post in shell.posts if post["kind"] == "fix-live"]
    assert (line["to"], line["text"], line["target"], line["build"], line["id"]) == ("root", f"RELEASED sand 0.162.1437 ({build})", "sand", build, "release:36642:live")


def test_a_landed_merge_fast_pr_with_release_with_tells_the_release_owner_and_root_once(shell, config, tmp_path):
    land_fast(shell, config)
    landing_pass(shell, config)
    shell.sleep(180)
    landing_pass(shell, config)
    [release] = shell.posts
    assert (release["kind"], release["to"], release["topic"], release["pr"], release["id"]) == ("release", "peer:sole-51,root", "36642", "36642", "release:36642:0")
    assert release["text"].startswith("#36642 landed as 5e11a9d0c2 with merge-fast and Release-With: sand. Start the sand release now, then run `cci post --drive d1 ")
    assert len(body_reads(shell)) == 1 and release_action(tmp_path).status == "completed"


@pytest.mark.parametrize(
    ("labels", "body", "reads"),
    [("merge,externally-merged", FAST_BODY, 0), ("merge-fast", "Resume uploads the thread.\n\nRelease-Order: api before sand\n", 1)],
    ids=["no-merge-fast-label", "no-release-with-line"],
)
def test_a_landing_without_the_merge_fast_label_or_a_release_with_line_posts_nothing(shell, config, tmp_path, labels, body, reads):
    land_fast(shell, config, body=body, labels=labels)
    for _ in range(3):
        landing_pass(shell, config)
        shell.sleep(11 * 60)
    assert shell.posts == [] and len(body_reads(shell)) == reads


def test_a_merge_fast_landing_older_than_the_runners_first_pass_is_history(shell, config, tmp_path):
    shell.bodies["36642"] = FAST_BODY
    shell.rows = [fast_row(shell)]
    shell.sleep(60)
    for _ in range(3):
        landing_pass(shell, config)
        shell.sleep(11 * 60)
    assert shell.posts == [] and body_reads(shell) == []


def test_an_incident_landing_older_than_the_runners_first_pass_is_history(shell, config, tmp_path):
    live_clock(tmp_path, shell)
    shell.bodies["36642"] = FAST_BODY
    shell.rows = [fast_row(shell, "externally-merged", INCIDENT_LANE)]
    shell.sleep(60)
    for _ in range(3):
        landing_pass(shell, config)
        shell.sleep(3 * 60)
    assert shell.posts == [] and body_reads(shell) == []


def test_a_landing_a_pass_read_is_never_judged_again_once_it_reads_as_an_incident_fix(shell, config, tmp_path):
    land_fast(shell, config, labels="externally-merged")
    landing_pass(shell, config)
    shell.rows[0]["lane"] = INCIDENT_LANE
    for _ in range(2):
        shell.sleep(3 * 60)
        landing_pass(shell, config)
    assert shell.posts == [] and body_reads(shell) == []


def test_a_landed_merge_fast_row_with_no_landing_time_is_history(shell, config, tmp_path):
    land_fast(shell, config)
    del shell.rows[0]["landed_at"]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.posts == [] and body_reads(shell) == []


def test_every_release_with_line_names_its_components_once_and_any_of_them_counts(shell, config, tmp_path):
    land_fast(shell, config, body="Moves the wire.\r\n\r\nRelease-With: api sand\r\nRelease-Order: api before sand\r\nRelease-With: sand restate\r\n")
    landing_pass(shell, config)
    assert "Release-With: api sand restate. Start the api sand restate release now" in shell.posts[0]["text"]
    cci_record(shell, lane="sole", kind="release", refs={"targets": ["restate"]}, at=actions.stamp(shell.now()), text="restate release started")
    shell.sleep(10 * 60)
    landing_pass(shell, config)
    assert blockers(shell) == [] and release_action(tmp_path).status == "verified"


def test_ten_minutes_with_no_release_record_posts_a_blocker_and_twenty_a_second(shell, config, tmp_path):
    land_fast(shell, config)
    landing_pass(shell, config)
    shell.sleep(9 * 60)
    landing_pass(shell, config)
    assert blockers(shell) == []
    shell.sleep(60)
    landing_pass(shell, config)
    shell.sleep(180)
    landing_pass(shell, config)
    [late] = blockers(shell)
    assert (late["to"], late["topic"], late["pr"]) == ("peer:sole-51,root", "36642", "36642")
    assert late["text"].startswith("#36642 5e11a9d0c2: landed 10 minutes ago with merge-fast and Release-With: sand, and no cci `release` record since targets sand or names #36642. Start the sand release now")
    shell.sleep(7 * 60)
    landing_pass(shell, config)
    assert [post["text"].split(": ", 1)[1].split(" with ")[0] for post in blockers(shell)] == ["landed 10 minutes ago", "landed 20 minutes ago"]
    assert escalations(shell) == []


@pytest.mark.parametrize("refs", [{"targets": ["sand"]}, {"prs": [36642]}, {"prs": ["Forge-AI/monorepo#36642"]}], ids=["target", "pr", "qualified-pr"])
def test_a_release_record_naming_the_component_or_the_pr_stops_the_blockers(shell, config, tmp_path, refs):
    land_fast(shell, config)
    landing_pass(shell, config)
    seq = cci_record(shell, lane="sole", kind="release", refs=refs, at=actions.stamp(shell.now()), text="sand release started in build 6101")
    shell.sleep(21 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    released = release_action(tmp_path)
    assert blockers(shell) == [] and (released.status, released.verification_receipt["cci"]) == ("verified", seq)


@pytest.mark.parametrize(
    ("record", "minutes"),
    [
        ({"lane": "desk-runner", "refs": {"prs": [36642]}}, 0),
        ({"lane": "sole", "refs": {"targets": ["sand"]}}, -5),
        ({"lane": "sole", "refs": {"targets": ["api"], "prs": [36641, "Forge-AI/design-docs#36642"]}}, 0),
        ({"lane": "sole", "drive": "release-v3", "refs": {"targets": ["sand"], "prs": [36642]}}, 0),
    ],
    ids=["the-runners-own-record", "before-the-landing", "another-release", "on-another-drive"],
)
def test_a_release_record_that_does_not_name_this_landing_stops_nothing(shell, config, tmp_path, record, minutes):
    land_fast(shell, config)
    cci_record(shell, kind="release", at=actions.stamp(shell.now() + timedelta(minutes=minutes)), text="release started", **record)
    landing_pass(shell, config)
    shell.sleep(10 * 60)
    landing_pass(shell, config)
    assert len(blockers(shell)) == 1 and release_action(tmp_path).status == "completed"


@pytest.mark.parametrize(
    ("reply", "stops"),
    [({"re": 1}, True), ({"resolves": 2}, True), ({"re": 6}, False), ({"re": 1, "drive": "release-v3"}, False)],
    ids=["re-the-release-record", "resolves-the-blocker", "re-another-record", "another-drives-root"],
)
def test_a_root_reply_to_the_release_record_or_its_blocker_stops_the_blockers(shell, config, tmp_path, reply, stops):
    land_fast(shell, config)
    landing_pass(shell, config)
    shell.sleep(10 * 60)
    landing_pass(shell, config)
    assert [post["kind"] for post in shell.posts] == ["release", "blocker"]
    seq = cci_record(shell, kind="answer", text="sole-51 is mid-apply; the root starts the sand release", **reply)
    shell.sleep(10 * 60)
    landing_pass(shell, config)
    assert len(blockers(shell)) == (1 if stops else 2)
    assert (release_action(tmp_path).verification_receipt or {}).get("cci") == (seq if stops else None)


def test_a_landing_first_read_late_posts_the_release_record_then_a_blocker_at_the_next_step(shell, config, tmp_path):
    land_fast(shell, config)
    shell.sleep(25 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [post["kind"] for post in shell.posts] == ["release"]
    shell.sleep(5 * 60)
    landing_pass(shell, config)
    assert [post["kind"] for post in shell.posts] == ["release", "blocker"] and ": landed 30 minutes ago with " in shell.posts[1]["text"]


LIVE_COMMIT = "7d9d9602f9d1c0ffee0000000000000000000000"


@pytest.mark.parametrize(
    ("running", "ancestor", "owed"),
    [
        ([{"target": "sand", "env": "plat-usw2-prod", "release": "releases/2026-10-20/51", "commit": LIVE_COMMIT}], True, False),
        ([{"target": "sand", "env": "plat-usw2-prod", "release": "releases/2026-10-20/49", "commit": LIVE_COMMIT}], False, True),
        ([{"target": "api", "env": "plat-usw2-prod", "release": "releases/2026-10-20/51", "commit": LIVE_COMMIT}], True, True),
        ([], False, True),
    ],
    ids=["released-since", "live-before-the-squash", "another-component-released", "nothing-live"],
)
def test_a_landing_from_before_a_restart_is_owed_only_while_no_live_deploy_carries_its_squash(shell, config, tmp_path, running, ancestor, owed):
    land_fast(shell, config)
    shell.sleep(6 * 60 * 60)
    shell.running = running
    if ancestor:
        shell.ancestors.add((FAST_SQUASH, LIVE_COMMIT))
    landing_pass(shell, config)
    shell.sleep(20 * 60)
    landing_pass(shell, config)
    released = release_action(tmp_path)
    if owed:
        assert [post["kind"] for post in shell.posts] == ["release", "blocker"] and released.status == "completed"
    else:
        assert shell.posts == [] and released.status == "verified"
        assert released.verification_receipt["shipped"] == "sand on plat-usw2-prod runs 7d9d9602f9 (releases/2026-10-20/51)"


def test_a_restarted_runner_never_replays_a_settled_incident_release_or_its_held_blocker(shell, config, tmp_path):
    """#104815 to #104821 on Oct 20: a restarted runner posted seven RELEASE-HELD-AFTER-LANDING blockers for #41074 and #41089, released hours earlier (backlog dcf28ec item 1)."""
    live_clock(tmp_path, shell)
    land_incident(shell, config, FAST_BODY)
    landing_pass(shell, config)
    for text in ("hold #36642: one cut with #41071 when that lands", "hold #36642 until #41071 lands, then one cut"):
        cci_record(shell, kind="hold", status="open", refs={"prs": [36642]}, at=actions.stamp(shell.now()), text=text)
    shell.sleep(3 * 60)
    landing_pass(shell, config)
    cci_record(shell, lane=WALKER, kind="release", refs={"targets": ["sand"], "builds": ["https://buildkite.com/forge/release/builds/8748"]}, at=actions.stamp(shell.now()), text="CUT build 8748 (releases/2026-10-20/33)")
    shell.sleep(3 * 60)
    landing_pass(shell, config)
    assert release_action(tmp_path).status == "verified"
    settled = [post["text"] for post in shell.posts if post["kind"] in ("release", "blocker")]
    assert [text for text in settled if text.startswith("RELEASE-HELD-AFTER-LANDING")]
    shell.sleep(6 * 60 * 60)
    for _ in range(3):
        landing_pass(shell, config)
        shell.sleep(3 * 60)
    assert [post["text"] for post in shell.posts if post["kind"] in ("release", "blocker")] == settled


def test_a_landing_read_in_the_pass_it_lands_never_reads_what_is_live(shell, config, tmp_path):
    land_fast(shell, config)
    landing_pass(shell, config)
    assert [post["kind"] for post in shell.posts] == ["release"]
    assert not [call for call in shell.calls if call[1:] == ["release", "live", "--json"]]


@pytest.mark.parametrize(("link", "stops"), [("resolves", True), ("re", False)])
def test_a_root_resolves_on_an_incident_landings_record_settles_it_and_a_re_does_not(shell, config, tmp_path, link, stops):
    live_clock(tmp_path, shell)
    land_incident(shell, config, FAST_BODY)
    landing_pass(shell, config)
    cci_record(shell, kind="note", at=actions.stamp(shell.now()), text="answered by the sand cut already running", **{link: 1})
    shell.sleep(3 * 60)
    landing_pass(shell, config)
    assert (blockers(shell) == []) is stops and (release_action(tmp_path).status == "verified") is stops


def test_a_root_resolves_on_a_release_held_line_settles_the_incident_landing(shell, config, tmp_path):
    live_clock(tmp_path, shell)
    land_incident(shell, config, FAST_BODY)
    landing_pass(shell, config)
    cci_record(shell, kind="hold", status="open", refs={"prs": [36642]}, at=actions.stamp(shell.now()), text="hold #36642: one cut with #41071 when that lands")
    shell.sleep(3 * 60)
    landing_pass(shell, config)
    [held] = [post for post in shell.posts if post["text"].startswith("RELEASE-HELD-AFTER-LANDING")]
    cci_record(shell, kind="note", resolves=shell.posts.index(held) + 1, at=actions.stamp(shell.now()), text="answered by GO CUT 104939")
    shell.sleep(3 * 60)
    landing_pass(shell, config)
    shell.sleep(3 * 60)
    landing_pass(shell, config)
    assert [post["to"] for post in blockers(shell)] == ["peer:sole-51,root", "root"] and release_action(tmp_path).status == "verified"


def test_a_fix_live_record_from_another_lane_settles_a_landing(shell, config, tmp_path):
    live_clock(tmp_path, shell)
    land_incident(shell, config, FAST_BODY)
    landing_pass(shell, config)
    seq = cci_record(shell, lane="alerts-desk", kind="fix-live", refs={"prs": [36642], "targets": ["sand"], "builds": ["https://buildkite.com/forge/release/builds/8815"]}, at=actions.stamp(shell.now()), text="RELEASED sand 0.162.1467")
    shell.sleep(3 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    released = release_action(tmp_path)
    assert blockers(shell) == [] and (released.status, released.verification_receipt["live"]) == ("verified", seq)
    assert not [post for post in shell.posts if post["kind"] == "fix-live"]


def test_a_root_resolves_on_the_release_record_closes_the_landings_cut_clock(shell, config, tmp_path):
    land_fast(shell, config)
    orca_pass(shell, config)
    landing_pass(shell, config)
    orca_pass(shell, config)
    cci_record(shell, kind="note", resolves=1, at=actions.stamp(shell.now()), text="#36642 was released by another drive's 8668; no release is owed")
    shell.sleep(60)
    landing_pass(shell, config)
    shell.sleep(5 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert cut_silent(shell) == [] and incident(tmp_path, "desk-runner").facts["cut-clocks"]["release:36642"]["ended"] == "#36642 settled"


class Killed(BaseException):
    pass


def test_a_release_record_posted_just_before_a_crash_is_never_posted_twice(shell, config, tmp_path, monkeypatch):
    land_fast(shell, config)
    save = runner_module.release_posted

    def killed(*args):
        raise Killed("killed after the post")

    monkeypatch.setattr(runner_module, "release_posted", killed)
    with pytest.raises(Killed, match="killed after the post"):
        cli(shell, config, "run", "--desk", "landing", "--once")
    assert len(shell.posts) == 1 and release_action(tmp_path).status == "accepted"
    monkeypatch.setattr(runner_module, "release_posted", save)
    landing_pass(shell, config)
    shell.sleep(10 * 60)
    landing_pass(shell, config)
    assert [(post["kind"], post["id"]) for post in shell.posts] == [("release", "release:36642:0"), ("blocker", "release:36642:1")]
    assert release_action(tmp_path).response["posted"] == {"0": 1, "1": 2}


def test_a_post_that_prints_no_sequence_is_escalated_and_settled_by_the_repost(shell, config, tmp_path):
    land_fast(shell, config)
    shell.post_out = "cci: stored\n"
    landing_pass(shell, config)
    assert release_action(tmp_path).status == "accepted"
    shell.post_out = ""
    landing_pass(shell, config)
    assert [post["kind"] for post in shell.posts] == ["release", "defect"] and release_action(tmp_path).response["posted"] == {"0": 1}
    [line] = escalations(shell)
    assert line.startswith("RELEASE-FAILED release:36642:0 #36642: cci printed no sequence for the release record to peer:sole-51,root: cci: stored; #36642 landed as 5e11a9d0c2 ")


def test_a_release_record_cci_refuses_is_escalated_to_root_each_step_and_posted_once_cci_takes_it(shell, config, tmp_path):
    land_fast(shell, config)
    shell.post_error = "cci: peer:sole-51 matches no session"
    landing_pass(shell, config)
    landing_pass(shell, config)
    shell.sleep(10 * 60)
    landing_pass(shell, config)
    assert shell.posts == [] and release_action(tmp_path).status == "accepted"
    shell.post_error = ""
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [post["kind"] for post in shell.posts] == ["release", "defect", "defect"]
    lines = escalations(shell)
    assert [line.split(": ", 1)[0] for line in lines] == ["RELEASE-FAILED release:36642:0 #36642", "RELEASE-FAILED release:36642:1 #36642"]
    assert lines[0].startswith("RELEASE-FAILED release:36642:0 #36642: cci refused the release record to peer:sole-51,root: cci: peer:sole-51 matches no session; #36642 landed as 5e11a9d0c2 ")


def test_the_release_owner_and_the_start_deadline_are_the_ones_runner_json_names(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    raw["landing"]["release_owner"] = "release-desk"
    raw["deadlines"] = {"release_start_minutes": 4}
    config.write_text(json.dumps(raw))
    land_fast(shell, config)
    landing_pass(shell, config)
    shell.sleep(4 * 60)
    landing_pass(shell, config)
    assert [(post["kind"], post["to"]) for post in shell.posts] == [("release", "release-desk,root"), ("blocker", "release-desk,root")]
    assert "landed 4 minutes ago" in shell.posts[1]["text"] and "repeats every 4 minutes" in shell.posts[1]["text"]


def test_a_landing_config_that_names_no_release_owner_does_not_start(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    del raw["landing"]["release_owner"]
    config.write_text(json.dumps(raw))
    with pytest.raises(KeyError, match="release_owner"):
        cli(shell, config, "run", "--desk", "landing", "--once")


def test_the_view_renders_pacific_times_from_receipts(shell, config, tmp_path, capsys):
    shell.launch(LANE, "ctx_a")
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    capsys.readouterr()
    assert cli(shell, config, "show") == 0
    view = capsys.readouterr().out
    assert "R625 relay [accepted] accepted 11:00" in view
    assert "Z" not in view.split("\n", 1)[1].replace("desk-lane", "")


def test_a_send_with_no_parseable_reply_is_unverifiable_not_failed(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.garble_sends = 1
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    for _ in range(3):
        orca_pass(shell, config)
    assert len(shell.sends()) == 1
    assert send_statuses(tmp_path) == ["unverifiable"]


def test_an_accepted_relay_with_no_dispatch_to_reach_fails_at_once_not_at_its_deadline(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a", status="completed")
    cli(shell, config, "relay", "--key", "R633", "--lane", LANE, "--text", "use evidence.md", "--reply-to", "msg_q9")
    cli(shell, config, "relay", "--key", "R634", "--lane", "ghost", "--text", "rebase onto dev")
    for _ in range(12):
        orca_pass(shell, config)
        shell.sleep(60)
    assert sorted(line for line in escalations(shell) if not line.startswith("RECLAIM")) == [
        "RELAY-FAILED R633 api-fix: no live dispatch (ctx_a is completed); nothing was relayed",
        "RELAY-FAILED R634 ghost: no live dispatch; nothing was relayed",
    ]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R633"].status == "failed"
    assert shell.sends() == [] and replies(shell) == []


def test_a_dispatch_orca_answers_is_gone_fails_its_relay(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    del shell.dispatches["ctx_a"]
    cli(shell, config, "relay", "--key", "R635", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    assert escalations(shell) == [f"RELAY-FAILED R635 {LANE}: no live dispatch; nothing was relayed"]


def test_a_delivered_relay_is_never_failed_or_reported_again_when_its_dispatch_ends(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    cli(shell, config, "relay", "--key", "R437", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    shell.dispatches["ctx_a"]["status"] = "completed"
    cli(shell, config, "relay", "--key", "R440", "--lane", LANE, "--text", "post the status")
    for _ in range(3):
        orca_pass(shell, config)
    container = incident(tmp_path, f"desk-lane-{LANE}")
    assert (container.actions["R437"].status, container.actions["R440"].status) == ("accepted", "failed")
    assert len(shell.sends()) == 1
    assert [line for line in escalations(shell) if not line.startswith(("RECLAIM", "LANE-GONE"))] == [f"RELAY-FAILED R440 {LANE}: no live dispatch (ctx_a is completed); nothing was relayed"]


UNREACHABLE_FAILURES = [
    runner_module.Done(1, json.dumps({"ok": False, "error": {"code": "runtime_unavailable", "message": "Could not connect to the running Orca app. Restart Orca."}}), ""),
    runner_module.Done(1, json.dumps({"ok": False, "error": {"code": "runtime_timeout", "message": "Timed out waiting for the Orca runtime to respond."}}), ""),
    runner_module.Done(1, "", "connect ECONNREFUSED"),
]


@pytest.mark.parametrize("failure", UNREACHABLE_FAILURES)
def test_an_orca_blip_changes_no_relay_and_logs_one_unreachable_and_one_back(shell, config, tmp_path, failure):
    shell.launch(LANE, "ctx_a")
    cli(shell, config, "relay", "--key", "R437", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    shell.orca_down = failure
    cli(shell, config, "relay", "--key", "R440", "--lane", LANE, "--text", "post the status")
    for _ in range(3):
        orca_pass(shell, config)
        shell.sleep(60)
    container = incident(tmp_path, f"desk-lane-{LANE}")
    assert (container.actions["R437"].status, container.actions["R440"].status) == ("accepted", "accepted")
    [down] = escalations(shell)
    assert down.startswith("ORCA-UNREACHABLE orca:") and "reads no dispatch as gone and fails no relay" in down
    shell.orca_down = None
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert [call[call.index("--thread-id") + 1] for call in shell.sends()] == [thread("R437"), thread("R440")]
    lines = escalations(shell)
    assert len(lines) == 2 and lines[1].startswith("ORCA-BACK orca-back:") and "for 3 min" in lines[1]
    assert not any(line.startswith("RELAY-FAILED") for line in lines)


def test_a_second_orca_outage_logs_its_own_streak(shell, config, tmp_path):
    shell.orca_down = UNREACHABLE_FAILURES[0]
    orca_pass(shell, config)
    shell.orca_down = None
    orca_pass(shell, config)
    shell.sleep(60)
    shell.orca_down = UNREACHABLE_FAILURES[0]
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert [line.split()[0] for line in escalations(shell)] == ["ORCA-UNREACHABLE", "ORCA-BACK", "ORCA-UNREACHABLE"]
    assert runner_module.UNREACHABLE_FACT in incident(tmp_path, "desk-runner").facts


def test_an_enqueue_whose_heads_moved_before_it_ran_enqueues_nothing(shell, config, tmp_path):
    shell.rows = stack_rows()
    shell.gates["29020"] = [
        gate("#28997 GREEN aaaa111111 graphite READY", would="#28997"),
        gate("#28997 GREEN abab111111 graphite READY", would="#28997"),
    ]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["28997"] = (0, "enqueue #28997: {}\n")
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    runner_module.seed_policy(runner)
    landing = runner_module.Landing(runner, runner.config.landing)
    landing.accept("29020", ["28997"], {"28997": {"sha": "aaaa111111"}}, {"28997": {"ours": True}}, [])
    shell.gates["28997"] = [gate("#28997 GREEN abab111111 graphite READY", would="#28997")]
    landing.enqueue()
    landing.drain()
    assert shell.enqueues() == []
    [action] = [action for action in store.load("desk-landing").actions.values() if action.kind == "enqueue"]
    assert action.response["outcome"] == "superseded"


def test_a_lost_enqueue_graphite_does_not_hold_stays_unverifiable(shell, config, tmp_path):
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", would="#28997")]
    grade_gated_heads_ok(shell)
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    runner_module.seed_policy(runner)
    runner_module.Landing(runner, runner.config.landing).accept("29020", ["28997"], {"28997": {"sha": "aaaa111111"}}, {"28997": {"ours": True}}, [])
    key = next(key for key in store.load("desk-landing").actions if key.startswith("enqueue:"))
    with store.owned("desk-landing", 0) as live:
        live.start(key, shell.now())
    shell.sleep(16 * 60)
    shell.statuses = "#28997 READY_TO_MERGE aaaa111111 open review APPROVED on dev\n"
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert store.load("desk-landing").actions[key].status == "unverifiable"
    assert shell.enqueues() == []
    lines = escalations(shell)
    assert len(lines) == 1 and "holds none of #28997" in lines[0]


def test_an_unrelated_push_does_not_settle_a_restack_still_on_the_landed_branch(shell, config, tmp_path):
    shell.rows = stack_rows(**{"28997": "landed", "29016": "landed"})
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    runner.accept_relay("restack:29020:29016", LANE, "#29016 landed; #29020 at cccc333333 sits on its deleted branch.", "", 10)
    landing = runner_module.Landing(runner, runner.config.landing)
    shell.rows[2]["head"] = "dddd444444"
    landing.verify_restacks({row["pr"]: row for row in shell.rows})
    assert store.load(f"desk-lane-{LANE}").actions["restack:29020:29016"].status == "accepted"
    shell.rows[2]["base"] = "dev"
    landing.verify_restacks({row["pr"]: row for row in shell.rows})
    assert store.load(f"desk-lane-{LANE}").actions["restack:29020:29016"].status == "verified"


def test_an_empty_receipt_or_a_null_payload_does_not_stop_the_runner(shell, config, tmp_path):
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_a")
    (shell.receipts / "half-written.json").write_text("")
    (shell.receipts / "half-written.terminal").write_text("term_x\n")
    cli(shell, config, "relay", "--key", "R700", "--lane", "half-written", "--text", "hello")
    shell.receive({"id": "msg_n", "type": "status", "subject": "note", "body": "", "thread_id": None, "payload": "null", "from_handle": "term_ctx_a", "lane": LANE})
    orca_pass(shell, config)
    assert shell.sends() == []


def test_read_messages_still_transfer_ownership_complete_relays_and_report_outcomes(shell, config, tmp_path):
    orca_pass(shell, config)
    cli(shell, config, "relay", "--key", "R800", "--lane", LANE, "--text", "apply the fix")
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    assert incident(tmp_path, f"desk-lane-{LANE}").pending_owner == "ctx_a"
    shell.ack(thread("R800"), "started", "ctx_a")
    shell.ack(thread("R800"), "done", "ctx_a")
    outcome = shell.receive({"type": "worker_done", "subject": "fixed", "payload": json.dumps({"dispatchId": "ctx_a", "outcome": "success"})})
    for message in shell.mailbox:
        message["read"] = True
    orca_pass(shell, config)
    container = incident(tmp_path, f"desk-lane-{LANE}")
    assert (container.owner, container.pending_owner, container.owner_generation) == ("ctx_a", None, 1)
    assert container.actions["R800"].status == "completed"
    assert container.actions["R800"].response["message"] == shell.mailbox[1]["id"]
    assert escalations(shell) == [f"OUTCOME {outcome['id']} {LANE}: worker_done success dispatch=ctx_a: fixed"]
    assert all(message["read"] for message in shell.mailbox)
    assert not any("--wait" in call or "--ack" in call for call in shell.calls)


def test_run_mail_is_acknowledged_once_relayed_and_never_ahead_of_the_cursor(shell, config, tmp_path):
    orca_pass(shell, config)
    done = shell.receive({"type": "worker_done", "subject": "fixed", "payload": json.dumps({"dispatchId": "ctx_a", "outcome": "success"})})
    beat = shell.receive({"type": "heartbeat"})
    shell.inbox_error = runner_module.Done(1, "", "socket closed")
    orca_pass(shell, config)
    assert not (done["read"] or beat["read"])
    shell.inbox_error = None
    shell.arriving.append({"type": "worker_done", "subject": "late"})
    orca_pass(shell, config)
    late = shell.mailbox[-1]
    assert (done["read"], beat["read"], late["read"]) == (True, True, False)
    assert [line.split()[1] for line in escalations(shell) if line.startswith("OUTCOME")] == [done["id"], late["id"]]
    orca_pass(shell, config)
    assert late["read"]
    assert not any("--wait" in call for call in shell.calls)


def test_mail_that_reaches_the_run_behind_the_cursor_is_relayed_before_its_delivery_is_acknowledged(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    stray = shell.receive({"type": "worker_done", "subject": "sent to the terminal", "to_handle": "term_root"})
    note = shell.receive({"subject": "direct note", "to_handle": "term_root"})
    fixed = shell.receive({"type": "worker_done", "subject": "fixed"})
    orca_pass(shell, config)
    assert incident(tmp_path, "desk-runner").facts["inbox:run_1"] == fixed["sequence"]
    stray["to_handle"] = note["to_handle"] = "run:run_1"
    shell.inbox_error = runner_module.Done(1, "", "socket closed")
    orca_pass(shell, config)
    assert not (stray["read"] or note["read"])
    shell.inbox_error = None
    orca_pass(shell, config)
    assert stray["read"] and note["read"]
    assert incident(tmp_path, "desk-runner").facts["inbox:run_1"] == fixed["sequence"]
    assert [line for line in escalations(shell) if not line.startswith("ORCA-")] == [
        f"OUTCOME {fixed['id']} {LANE}: worker_done ? dispatch=ctx_a: fixed",
        f"OUTCOME {stray['id']} {LANE}: worker_done ? dispatch=ctx_a: sent to the terminal",
        f"UNRELAYED {note['id']} {LANE}: status from term_ctx_a at sequence {note['sequence']}, behind cursor {fixed['sequence']}, has no relay record and its type has no relay: direct note",
    ]
    orca_pass(shell, config)
    assert len(escalations(shell)) == 5


def test_nothing_dispatches_before_the_first_read_sets_the_cursor(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    cli(shell, config, "relay", "--key", "R900", "--lane", LANE, "--text", "apply the fix")
    shell.inbox_error = runner_module.Done(1, "", "socket closed")
    orca_pass(shell, config)
    assert shell.sends() == []
    assert "inbox:run_1" not in incident(tmp_path, "desk-runner").facts
    shell.inbox_error = None
    orca_pass(shell, config)
    assert len(shell.sends()) == 1
    shell.ack(thread("R900"), "started", "ctx_a")
    orca_pass(shell, config)
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R900"].status == "started"


def test_the_cursor_survives_restart_and_pages_back_without_replay_or_skips(shell, config, tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "INBOX_PAGE_SIZE", 2)
    orca_pass(shell, config)
    before = shell.receive({"type": "worker_done", "subject": "before restart"})
    orca_pass(shell, config)
    messages = [shell.receive({"type": "worker_done", "subject": f"after restart {index}"}) for index in range(7)]
    shell.calls.clear()
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    processed = []
    handle = runner.message

    def record(message):
        processed.append(message["id"])
        handle(message)

    monkeypatch.setattr(runner, "message", record)
    runner_module.run_orca(runner, True)
    assert processed == [message["id"] for message in messages]
    assert [call[call.index("--limit") + 1] for call in shell.calls if call[:3] == ["orca", "orchestration", "inbox"]] == ["2", "4", "8"]
    assert incident(tmp_path, "desk-runner").facts["inbox:run_1"] == messages[-1]["sequence"]
    runner.check()
    assert processed == [message["id"] for message in messages]
    assert [line.split()[1] for line in escalations(shell)] == [before["id"], *processed]


def test_a_cursor_checkpoint_survives_a_failure_mid_batch(shell, config, tmp_path, monkeypatch):
    orca_pass(shell, config)
    messages = [shell.receive({"type": "worker_done", "subject": str(index)}) for index in range(3)]
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    handle = runner.message

    def fail_second(message):
        if message["id"] == messages[1]["id"]:
            raise RuntimeError("interrupted")
        handle(message)

    monkeypatch.setattr(runner, "message", fail_second)
    with pytest.raises(RuntimeError, match="interrupted"):
        runner.check()
    assert incident(tmp_path, "desk-runner").facts["inbox:run_1"] == messages[0]["sequence"]
    restarted = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    processed = []
    handle = restarted.message

    def record(message):
        processed.append(message["id"])
        handle(message)

    monkeypatch.setattr(restarted, "message", record)
    restarted.check()
    restarted.flush()
    assert processed == [message["id"] for message in messages[1:]]
    assert [line.split()[1] for line in escalations(shell)] == [message["id"] for message in messages]


@pytest.mark.parametrize(
    "subject,kind,sender,lane",
    [("fix-live: 12:48 PM PT release abc", "FIX-LIVE", "term_ctx_a", LANE), ("MeChAnIsM: wrong selector", "MECHANISM", "term_unknown", "term_unknown"), ("ready: #12 green at 9c0b891", "READY", "term_ctx_a", LANE)],
)
def test_milestone_status_escalates_once_per_message_id(shell, config, tmp_path, subject, kind, sender, lane):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    body = "release\n\t" + "a " * 200
    message = shell.receive({"subject": subject, "body": body, "from_handle": sender})
    orca_pass(shell, config)
    shell.receive({"id": message["id"], "subject": subject, "body": body, "from_handle": sender})
    orca_pass(shell, config)
    assert escalations(shell) == [f"{kind} {message['id']} {lane}: {subject}: {' '.join(body.split())[:300].rstrip()}"]


def test_only_exact_status_milestone_prefixes_escalate_and_heartbeats_advance_the_cursor(shell, config, tmp_path):
    orca_pass(shell, config)
    for subject in (None, "", "checkpoint", "fix-live soon", "mechanisms: known", "prefix fix-live: now"):
        shell.receive({"subject": subject})
    heartbeat = shell.receive({"type": "heartbeat", "subject": "fix-live: ignored", "payload": "not json"})
    orca_pass(shell, config)
    assert escalations(shell) == []
    assert incident(tmp_path, "desk-runner").facts["inbox:run_1"] == heartbeat["sequence"]


def test_first_read_starts_at_the_newest_sequence_without_replaying_history(shell, config, tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "INBOX_PAGE_SIZE", 2)
    for index in range(5):
        newest = shell.receive({"type": "worker_done", "subject": f"history {index}", "read": True})
    orca_pass(shell, config)
    assert incident(tmp_path, "desk-runner").facts["inbox:run_1"] == newest["sequence"]
    assert escalations(shell) == []
    assert len([call for call in shell.calls if call[:3] == ["orca", "orchestration", "inbox"]]) == 1
    fresh = shell.receive({"type": "worker_done", "subject": "fresh"})
    orca_pass(shell, config)
    assert [line.split()[1] for line in escalations(shell)] == [fresh["id"]]


def test_inbox_errors_escalate_once_per_bucket_without_advancing_the_cursor(shell, config, tmp_path):
    orca_pass(shell, config)
    fresh = shell.receive({"type": "worker_done", "subject": "fresh"})
    shell.inbox_error = runner_module.Done(1, json.dumps({"ok": False, "error": {"code": "invalid_argument", "message": "socket closed"}}), "")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert incident(tmp_path, "desk-runner").facts["inbox:run_1"] == 0
    [line] = escalations(shell)
    assert "ORCA-INBOX orca-inbox:" in line and "socket closed" in line
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    assert len(escalations(shell)) == 2
    shell.inbox_error = None
    orca_pass(shell, config)
    assert incident(tmp_path, "desk-runner").facts["inbox:run_1"] == fresh["sequence"]
    assert f"OUTCOME {fresh['id']}" in escalations(shell)[-1]


def test_the_orca_loop_waits_ten_seconds_between_quiet_passes(shell, config, tmp_path, monkeypatch):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    sleeps = []
    passes = []

    def beat(desk):
        passes.append(shell.now())
        if len(passes) == 3:
            raise RuntimeError("end loop")

    def sleep(seconds):
        sleeps.append(seconds)
        shell.clock += timedelta(seconds=seconds)

    monkeypatch.setattr(runner, "beat", beat)
    monkeypatch.setattr(shell, "sleep", sleep)
    with pytest.raises(RuntimeError, match="end loop"):
        runner_module.run_orca(runner, False)
    assert set(sleeps) == {runner_module.WAKE_SECONDS}
    assert [later - earlier for earlier, later in itertools.pairwise(passes)] == [timedelta(seconds=runner_module.PASS_SECONDS)] * 2


def test_the_orca_loop_backs_off_while_orca_gives_no_answer_and_resets_when_it_answers(shell, config, tmp_path, monkeypatch):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    passes = []

    def beat(desk):
        passes.append(shell.now())
        if len(passes) == 5:
            shell.orca_down = None
        if len(passes) == 8:
            raise RuntimeError("end loop")

    def sleep(seconds):
        shell.clock += timedelta(seconds=seconds)

    shell.orca_down = UNREACHABLE_FAILURES[0]
    monkeypatch.setattr(runner, "beat", beat)
    monkeypatch.setattr(shell, "sleep", sleep)
    with pytest.raises(RuntimeError, match="end loop"):
        runner_module.run_orca(runner, False)
    assert [(later - earlier).total_seconds() for earlier, later in itertools.pairwise(passes)] == [20, 40, 80, 120, 120, 10, 10]


def test_a_finished_launch_ends_the_wait_at_once(shell, config, tmp_path, monkeypatch):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))

    launch = Running()
    runner.launching["desk-lane-a/R1"] = launch
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        launch.code = 0

    monkeypatch.setattr(shell, "sleep", sleep)
    runner.idle(runner_module.PASS_SECONDS)
    assert sleeps == [runner_module.WAKE_SECONDS]


def with_gc(config: Path) -> None:
    raw = json.loads(config.read_text())
    raw["orca"]["gc"] = ".agents/skills/orca/scripts/orca-gc"
    config.write_text(json.dumps(raw))


def test_a_settled_dispatch_is_named_once_for_the_roots_gc_and_never_closed(shell, config, tmp_path):
    with_gc(config)
    shell.launch(LANE, "ctx_a", status="completed")
    shell.launch("live-lane", "ctx_b")
    orca_pass(shell, config)
    orca_pass(shell, config)
    reclaims = [line for line in escalations(shell) if line.startswith("RECLAIM ")]
    assert len(reclaims) == 1
    assert len([call for call in shell.calls if call[:5] == ["orca", "orchestration", "worker-show", "--dispatch", "ctx_a"]]) == 1
    assert reclaims[0].endswith(f"1 settled dispatch(es) still hold their terminal: {LANE}=ctx_a:term_ctx_a; run {runner_module.SCRIPTS / 'desk-runner.py'} gc --config {config.resolve()}, then .agents/skills/orca/scripts/orca-gc --run run_1 --dispatch ctx_a")
    assert [call for call in shell.calls if FORBIDDEN & {Path(token).name for token in call}] == []


def test_a_large_settled_set_names_the_whole_run(shell, config, tmp_path):
    with_gc(config)
    for index in range(runner_module.RECLAIM_NAMED + 2):
        shell.launch(f"lane-{index:02d}", f"ctx_{index:02d}", status="failed")
    orca_pass(shell, config)
    [reclaim] = [line for line in escalations(shell) if line.startswith("RECLAIM ")]
    assert f" and 2 more; run {runner_module.SCRIPTS / 'desk-runner.py'} gc --config {config.resolve()}, then .agents/skills/orca/scripts/orca-gc --run run_1" in reclaim
    assert reclaim.endswith("--run run_1")


def test_without_a_gc_the_line_names_the_r195_bar(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a", status="completed")
    orca_pass(shell, config)
    [reclaim] = [line for line in escalations(shell) if line.startswith("RECLAIM ")]
    assert reclaim.endswith(f"run {runner_module.SCRIPTS / 'desk-runner.py'} gc --config {config.resolve()}, then close each other idle terminal and remove each finished worktree under R195")


@pytest.mark.parametrize(("flag", "value"), [("--model", "gemini"), ("--effort", "extreme")])
def test_a_launch_the_script_cannot_start_is_refused_when_submitted(shell, config, tmp_path, flag, value):
    argv = {"--model": "astra", "--effort": "xhigh"} | {flag: value}
    with pytest.raises(SystemExit):
        cli(shell, config, "launch", "--key", "R1", "--lane", LANE, "--model", argv["--model"], "--effort", argv["--effort"], "--brief", str(tmp_path / "brief.md"))
    assert not (tmp_path / "store").exists()


@pytest.mark.parametrize("model", ["astra", "codex", "sol", "incident", "opus", "claude-opus-5-5", "gpt-6.1-sol"])
def test_every_model_the_script_starts_is_accepted(model):
    assert runner_module.launch_model(model) == model


def orca_calls(shell: FakeShell, verb: str) -> list[list[str]]:
    return [call for call in shell.calls if call[:3] == ["orca", "orchestration", verb]]


def accept_launch(shell: FakeShell, config: Path, tmp_path: Path) -> Path:
    brief = tmp_path / "fix.md"
    brief.write_text("ccx: place=local\nbrief")
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "sol", "--effort", "xhigh", "--brief", str(brief))
    return brief


@pytest.mark.parametrize(
    ("coordinator", "why"),
    [
        ("term_dead", "orca orchestration run-current as coordinator term_dead failed: terminal_handle_stale: term_dead is gone"),
        ("", "Orca binds run_1 to no terminal"),
    ],
)
def test_a_runner_outside_orca_with_no_live_coordinator_refuses_to_start_and_prints_the_rebind(shell, config, tmp_path, capsys, coordinator, why):
    shell.environ = {}
    shell.coordinator = coordinator
    shell.dead = {"term_dead"}
    accept_launch(shell, config, tmp_path)
    assert cli(shell, config, "run", "--desk", "orca", "--once") == 3
    expected = f"desk-runner cannot start workers on run_1: {why}. From the coordinator's Orca terminal run `desk-runner.py rebind --config {config.resolve()}`; the runner follows the new coordinator on its next pass, so it needs no restart"
    assert capsys.readouterr().err == expected + "\n"
    assert launches(shell) == [] and orca_calls(shell, "inbox") == []
    assert len(orca_calls(shell, "run-current")) == (1 if coordinator else 0)
    [line] = escalations(shell)
    assert f"UNBOUND unbound:{coordinator}:3 runner: {expected}; the runner refused to start" in line
    binding = incident(tmp_path, "desk-runner").facts["binding"]
    assert binding["bound"] is False and binding["why"] == why
    assert cli(shell, config, "run", "--desk", "orca", "--once") == 3
    assert len(escalations(shell)) == 1


@pytest.mark.parametrize("coordinator", ["term_dead", ""])
def test_a_runner_restarted_inside_orca_with_no_live_coordinator_rebinds_the_run_to_its_own_terminal(shell, config, tmp_path, capsys, coordinator):
    shell.coordinator = coordinator
    shell.dead = {"term_dead"}
    brief = accept_launch(shell, config, tmp_path)
    assert cli(shell, config, "run", "--desk", "orca", "--once") == 0
    assert orca_calls(shell, "run-use") == [["orca", "orchestration", "run-use", "--id", "run_1", "--json"]]
    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, "sol", "xhigh", str(brief)]]
    assert shell.spawned_env["orca-launch.sh"]["ORCA_TERMINAL_HANDLE"] == "term_root"
    assert escalations(shell) == []
    binding = incident(tmp_path, "desk-runner").facts["binding"]
    assert (binding["terminal"], binding["generation"], binding["bound"]) == ("term_root", 4, True)
    assert binding["via"] == f"orca orchestration run-use by desk-runner rebind at 11:00, replacing {coordinator or 'no terminal'} at generation 3"


def test_a_runner_whose_own_terminal_is_gone_acts_as_the_coordinator_the_run_names(shell, config, tmp_path):
    shell.environ = {"ORCA_TERMINAL_HANDLE": "term_closed", "ORCA_PANE_KEY": "pane_closed"}
    shell.dead = {"term_closed"}
    shell.coordinator = "term_new"
    shell.generation = 4
    brief = accept_launch(shell, config, tmp_path)
    orca_pass(shell, config)
    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, "sol", "xhigh", str(brief)]]
    env = shell.spawned_env["orca-launch.sh"]
    assert (env["ORCA_TERMINAL_HANDLE"], env["ORCA_PANE_KEY"]) == ("term_new", "")
    assert escalations(shell) == []
    binding = incident(tmp_path, "desk-runner").facts["binding"]
    assert (binding["terminal"], binding["generation"], binding["bound"]) == ("term_new", 4, True)


def test_a_running_runner_follows_the_coordinator_to_a_new_terminal(shell, config, tmp_path):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    assert runner.binding()["bound"]
    brief = accept_launch(shell, config, tmp_path)
    shell.coordinator = "term_new"
    shell.generation = 4
    runner.deliver()
    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, "sol", "xhigh", str(brief)]]
    assert shell.spawned_env["orca-launch.sh"]["ORCA_TERMINAL_HANDLE"] == "term_new"
    assert escalations(shell) == []


@pytest.mark.parametrize(("model", "flags"), [("sol", ()), ("incident", ()), ("opus", ("--owner-directed",))])
def test_a_coordinator_that_dies_mid_run_fails_each_launch_at_once_and_escalates_unbound_once(shell, config, tmp_path, model, flags):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    assert runner.binding()["bound"]
    shell.coordinator = "term_dead"
    shell.dead = {"term_dead"}
    shell.generation = 4
    brief = tmp_path / "fix.md"
    brief.write_text("ccx: place=local\nbrief")
    for key in ("R638", "R639"):
        cli(shell, config, "launch", "--key", key, "--lane", LANE, "--model", model, "--effort", "xhigh", "--brief", str(brief), *flags)
        runner.deliver()
        runner.flush()
    assert launches(shell) == []
    action = incident(tmp_path, f"desk-lane-{LANE}").actions["R638"]
    assert action.status == "failed"
    assert action.reason.startswith("R638 refused on an unbound runner: orca orchestration run-current as coordinator term_dead failed")
    assert action.reason.endswith(f"nothing was launched, and the root launches it by hand with orca-launch.sh, or runs `desk-runner.py rebind --config {config}` from its own terminal and submits it again under a new key")
    lines = escalations(shell)
    assert [line.split()[0] for line in lines] == ["UNBOUND", "LAUNCH-FAILED", "LAUNCH-FAILED"]
    assert lines[0].endswith("; each launch fails with LAUNCH-FAILED until it is bound")
    assert [post["kind"] for post in shell.posts] == ["blocker", "defect", "defect"]
    assert cli(shell, config, "rebind") == 0
    cli(shell, config, "launch", "--key", "R640", "--lane", LANE, "--model", model, "--effort", "xhigh", "--brief", str(brief), *flags)
    runner.deliver()
    assert [call[1] for call in launches(shell)] == [LANE]


def test_an_unbound_launch_fails_on_the_binding_before_the_load_is_read(shell, config, tmp_path):
    brief = tmp_path / "fix.md"
    brief.write_text("brief")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "opus", "--effort", "xhigh", "--brief", str(brief))
    shell.cpu_load = 40
    shell.coordinator = "term_dead"
    shell.dead = {"term_dead"}
    runner.deliver()
    runner.flush()
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].reason.startswith("R638 refused on an unbound runner:")
    assert [line.split()[0] for line in escalations(shell)] == ["UNBOUND", "LAUNCH-FAILED"]


def test_rebind_moves_the_run_to_this_terminal_and_records_how(shell, config, tmp_path, capsys):
    shell.coordinator = "term_dead"
    assert cli(shell, config, "rebind") == 0
    assert orca_calls(shell, "run-use") == [["orca", "orchestration", "run-use", "--id", "run_1", "--json"]]
    via = "orca orchestration run-use by desk-runner rebind at 11:00, replacing term_dead at generation 3"
    line = f"binding: terminal term_root coordinates run_1 at generation 4, via {via}; checked 11:00"
    assert capsys.readouterr().out == line + "\n"
    orca_pass(shell, config)
    assert incident(tmp_path, "desk-runner").facts["binding"]["via"] == via
    cli(shell, config, "show")
    assert capsys.readouterr().out.splitlines()[1] == line


def test_a_refused_rebind_names_orcas_error_and_records_nothing(shell, config, tmp_path, capsys):
    shell.coordinator = "term_dead"
    shell.run_use_error = {"code": "terminal_handle_stale", "message": "term_root is gone"}
    assert cli(shell, config, "rebind") == 1
    assert capsys.readouterr().err == "desk-runner rebind: orca orchestration run-use failed: terminal_handle_stale: term_root is gone\n"
    assert "binding" not in incident(tmp_path, "desk-runner").facts


def test_a_runner_records_the_coordinator_it_read_from_orca(shell, config, tmp_path, capsys):
    orca_pass(shell, config)
    binding = incident(tmp_path, "desk-runner").facts["binding"]
    assert (binding["terminal"], binding["generation"], binding["bound"]) == ("term_root", 3, True)
    assert binding["via"] == "coordinator read from Orca at generation 3"
    cli(shell, config, "show")
    assert capsys.readouterr().out.splitlines()[1].startswith("binding: terminal term_root coordinates run_1 at generation 3")


@pytest.mark.parametrize(
    ("printed", "reason"),
    [
        (
            f"{LANE} failed worker-start terminal=term_b: consumer_fenced: worker-start requires the coordinator terminal currently bound to the Task Run.; rolled back terminal=term_b worktree=/w\n",
            f"{LANE} failed worker-start terminal=term_b: consumer_fenced: worker-start requires the coordinator terminal currently bound to the Task Run.; rolled back terminal=term_b worktree=/w",
        ),
        ("orca-launch.sh: unknown model astra\nusage: orca-launch.sh <lane> <model> <effort> <brief-file>\n\n  ORCA_LAUNCH_WORKTREE_SECONDS  ceiling\n", "orca-launch.sh: unknown model astra usage: orca-launch.sh <lane> <model> <effort> <brief-file> ORCA_LAUNCH_WORKTREE_SECONDS ceiling"),
        (f"{LANE} failed worktree create: runtime_unavailable: Start the Orca app first.\n}}\n", f"{LANE} failed worktree create: runtime_unavailable: Start the Orca app first."),
    ],
)
def test_launch_failed_carries_the_launchs_own_failure_line(shell, config, tmp_path, printed, reason):
    shell.launch_line = printed
    accept_launch(shell, config, tmp_path)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].reason == reason
    [line] = escalations(shell)
    assert line == f"LAUNCH-FAILED desk-lane-{LANE}/R638 {LANE}: {reason}"


def desk_orders(shell: FakeShell, *lines: str) -> int:
    for line in lines:
        cci_go(shell, line)
    return shell.records[-1]["seq"]


def replies(shell: FakeShell) -> list[list[str]]:
    return [call for call in shell.calls if call[:3] == ["orca", "orchestration", "reply"]]


@pytest.mark.parametrize(
    ("line", "key", "lanes", "text"),
    [
        ("R1054 (3:0x PM PT) orca-desk: relay to merge-walker-r2 and d-land-r2: G331 (chain resumes)", "R1054", ["merge-walker-r2", "d-land-r2"], "G331 (chain resumes)"),
        ("- orca-desk: relay to a, b, and c: `walker: router GO`", None, ["a", "b", "c"], "`walker: router GO`"),
        ("R7 orca-desk: relay to lane-a: see deploy-go.md: G12", "R7", ["lane-a"], "see deploy-go.md: G12"),
        ("R8 orca-desk: relay to lane-a and lane-b: Ruling:\n- hold the release\n- rebuild once", "R8", ["lane-a", "lane-b"], "Ruling:\n- hold the release\n- rebuild once"),
        ("R9 (8:20 AM) orca-desk: relay to lane-a:\nRuling:\n\n- hold; relay nothing yet", "R9", ["lane-a"], "Ruling:\n\n- hold; relay nothing yet"),
    ],
)
def test_the_relay_grammar_names_the_key_lanes_and_text(line, key, lanes, text):
    directive = runner_module.INBOX_DIRECTIVE.match(line)
    assert directive["verb"] == "relay"
    parsed = runner_module.RELAY_TO.match(directive["rest"])
    assert (directive["key"], runner_module.LANE_LIST.split(parsed["lanes"]), parsed["text"]) == (key, lanes, text)


def test_an_inbox_relay_line_reaches_every_named_lane_once(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.launch("walker", "ctx_w")
    desk_orders(shell, f"R1 (9:0x AM PT) orca-desk: relay to {LANE}: history is never replayed")
    orca_pass(shell, config)
    desk_orders(shell, f"R2 (3:0x PM PT) orca-desk: relay to {LANE} and walker: G331 resumes the chain")
    orca_pass(shell, config)
    orca_pass(shell, config)
    sends = shell.sends()
    assert [(call[call.index("--to") + 1], call[call.index("--subject") + 1]) for call in sends] == [("dispatch:ctx_a", "R2: act R2"), ("dispatch:ctx_w", "R2: act R2")]
    assert all(call[call.index("--body") + 1].startswith("G331 resumes the chain\n") for call in sends)
    assert [line for line in escalations(shell)] == [
        f"RELAYED R2 {LANE}: relay to dispatch ctx_a",
        "RELAYED R2 walker: relay to dispatch ctx_w",
    ]


def test_a_refused_cci_post_keeps_the_desk_relaying_and_posts_on_a_later_pass(shell, config, tmp_path, capsys):
    shell.launch(LANE, "ctx_a")
    desk_orders(shell, "R1 orca-desk: relay to nobody: history")
    orca_pass(shell, config)
    shell.post_error = "cci: store schema 9 is newer than this cci 0.9.3 (schema 6)"
    desk_orders(shell, f"R2 orca-desk: relay to {LANE}: rebase onto dev")
    orca_pass(shell, config)
    assert len(shell.sends()) == 1 and escalations(shell) == []
    assert "1 escalation(s) left for the next pass; cci post exited 1: cci: store schema 9" in capsys.readouterr().err
    assert cli(shell, config, "relay", "--key", "R3", "--lane", LANE, "--text", "rebase again") == 1
    shell.post_error = ""
    orca_pass(shell, config)
    assert escalations(shell) == [f"RELAYED R2 {LANE}: relay to dispatch ctx_a"]


def heartbeat(shell: FakeShell, desk: str) -> dict | None:
    return runner_module.drive.heartbeat(lambda argv: shell.run(argv).out, "d1", desk)


def test_the_orca_heartbeat_is_a_cci_alive_record_naming_the_runner_and_writes_no_file(shell, config, tmp_path):
    orca_pass(shell, config)
    assert heartbeat(shell, "orca") == {
        "pid": str(os.getpid()),
        "at": actions.stamp(shell.clock),
        "config": str(config.resolve()),
        "terminal": "term_root",
        "restart": f"nohup desk-runner.py run --config {config.resolve()} --desk orca >> {tmp_path / 'orca-runner.log'} 2>&1 < /dev/null &",
        "version": runner_module.RUNNING_VERSION,
    }
    assert heartbeat(shell, "landing") is None
    assert list(tmp_path.glob("inbox/*.heartbeat.json")) == [] and not (tmp_path / "desk-runner.md").exists()


def installed(tmp_path: Path, *versions: str) -> None:
    plugins = tmp_path / ".claude" / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {"long-running@skills": [{"scope": "user", "version": version} for version in versions], "other@skills": [{"scope": "user", "version": "999.0.0"}]}}))


def newer(version: str, step: int = 1) -> str:
    major, _, rest = version.partition(".")
    return f"{int(major) + step}.{rest}"


def test_a_runner_behind_the_installed_plugin_posts_nothing_about_it(shell, config, tmp_path):
    """#535 shipped at 5:07 PM on Oct 20 as 0.7.186 and the live runner stayed on 0.7.185 until 9:26 PM, so the evening's phantom cutters ran on a fixed bug (#107539)."""
    running = runner_module.RUNNING_VERSION
    installed(tmp_path, running)
    orca_pass(shell, config)
    landing_pass(shell, config)
    assert [line for line in escalations(shell) if line.startswith("RUNNER-STALE")] == []
    installed(tmp_path, running, newer(running))
    for _ in range(2):
        orca_pass(shell, config)
        landing_pass(shell, config)
    installed(tmp_path, newer(running, 2))
    orca_pass(shell, config)
    landing_pass(shell, config)
    assert [line for line in escalations(shell) if line.startswith("RUNNER-STALE")] == []
    assert not [post for post in shell.posts if post["text"].startswith("RUNNER-STALE")]
    assert shell.reexeced == []


@pytest.mark.parametrize("desk", ["orca", "landing"])
def test_a_runner_behind_the_installed_plugin_re_executes_into_it_at_the_end_of_its_pass(shell, config, tmp_path, desk):
    installed(tmp_path, newer(runner_module.RUNNING_VERSION))
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    with pytest.raises(Reexeced):
        (runner_module.run_orca if desk == "orca" else runner_module.run_landing)(runner, False)
    assert shell.reexeced == [[str(runner_module.RUNNER_BIN), "run", "--config", str(config.resolve()), "--desk", desk]]
    assert heartbeat(shell, desk)["pid"] == str(os.getpid())


def test_the_orca_loop_exits_on_its_stop_marker_at_the_first_step(shell, config, tmp_path):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    runner.stop_marker().write_text(f"{os.getpid()}\n")
    assert runner_module.run_orca(runner, False) == 0
    assert heartbeat(shell, "orca") is not None
    assert not runner.stop_marker().exists()


def test_a_stop_marker_written_during_an_orca_step_ends_the_pass_before_the_next_step(shell, config, tmp_path, monkeypatch):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    run = shell.run

    def stopping(argv, stdin=None, env=None):
        if argv[:3] == ["orca", "orchestration", "inbox"]:
            runner.stop_marker().write_text(f"{os.getpid()}\n")
        return run(argv, stdin, env)

    monkeypatch.setattr(shell, "run", stopping)
    assert runner_module.run_orca(runner, False) == 0
    assert not runner.stop_marker().exists()
    after = shell.calls[next(index for index, call in enumerate(shell.calls) if call[:3] == ["orca", "orchestration", "inbox"]) + 1 :]
    assert [call for call in after if call[:2] == ["cci", "tail"] or call[:3] == ["orca", "orchestration", "worker-list"]] == []


def test_a_stop_marker_written_during_a_stack_check_ends_the_landing_pass_before_the_next_stack(shell, config, tmp_path):
    prs = [str(29100 + n) for n in range(runner_module.GATE_WORKERS * 3)]
    shell.rows = [{"pr": pr, "lane": LANE, "branch": f"g/{pr}", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"} for pr in prs]
    shell.gates = {pr: [gate(f"#{pr} GREEN cccc333333 graphite READY")] for pr in prs}
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    check, lock = shell.stack_enqueue, threading.Lock()

    def stopping(argv):
        with lock:
            if "--check" in argv and not runner.stop_marker().exists():
                runner.stop_marker().write_text(f"{os.getpid()}\n")
        return check(argv)

    shell.stack_enqueue = stopping
    assert runner_module.run_landing(runner, False) == 0
    assert not runner.stop_marker().exists()
    checked = {call[1] for call in shell.calls if Path(call[0]).name == "stack-enqueue" and "--check" in call}
    assert 1 <= len(checked) <= runner_module.GATE_WORKERS < len(prs)


def test_a_stop_marker_naming_another_process_stops_nothing(shell, config, tmp_path):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    runner.stop_marker(os.getpid() + 1).write_text(f"{os.getpid() + 1}\n")
    assert not runner.stop_requested()


def test_one_runner_leaving_on_its_stop_keeps_the_other_runners_stop(shell, config, tmp_path):
    """Oct 29 8:24 PM: the orca runner, stopped by a handoff, removed the one shared marker, so the landing runner stopped by an earlier handoff crashed removing it."""
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    runner.stop_marker().write_text(f"{os.getpid()}\n")
    runner.stop_marker(os.getpid() + 1).write_text(f"{os.getpid() + 1}\n")
    runner.stop_marker(os.getpid() + 1).unlink()
    assert runner.stop_requested()


def successor(config: Path, tmp_path: Path, desk: str) -> tuple[list[str], Path, bool]:
    return [str(runner_module.RUNNER_BIN), "run", "--config", str(config.resolve()), "--desk", desk], tmp_path / f"{desk}-runner.log", True


def live_runner(shell: FakeShell, config: Path, tmp_path: Path, desk: str) -> tuple[runner_module.Runner, contextlib.ExitStack]:
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    held = contextlib.ExitStack()
    assert held.enter_context(runner.desk_lock(desk)) is None
    return runner, held


def test_handoff_waits_for_the_runner_holding_the_desk_to_exit_and_then_starts_its_successor(shell, config, tmp_path, monkeypatch):
    runner, held = live_runner(shell, config, tmp_path, "orca")
    started = []

    def exits(seconds):
        started.append(list(shell.successors))
        assert runner.stop_marker().read_text() == f"{os.getpid()}\n"
        runner.stop_marker().unlink()
        held.close()

    monkeypatch.setattr(shell, "sleep", exits)
    assert cli(shell, config, "handoff", "--desk", "orca") == 0
    assert not runner.stop_marker().exists()
    assert started == [[]]
    assert shell.successors == [successor(config, tmp_path, "orca")]
    assert runner_module.RUNNER_BIN.parent.name == "bin" and (runner_module.RUNNER_BIN.parent.parent / ".claude-plugin").is_dir()


def test_a_desk_env_in_runner_json_reaches_the_runner_its_restart_line_and_its_handoff_successor(shell, config, tmp_path, monkeypatch):
    """release-cards #112958 on Oct 23: the landing runner's `ci slack pr-card` needs AWS_PROFILE=release-operator, set only on the nohup line, so a RUNNER-STALE restart or a hand restart dropped it."""
    raw = json.loads(config.read_text())
    raw["landing"]["env"] = {"AWS_PROFILE": "release-operator"}
    config.write_text(json.dumps(raw))
    installed(tmp_path, newer(runner_module.RUNNING_VERSION))
    landing_pass(shell, config)
    restart = f"nohup env AWS_PROFILE=release-operator desk-runner.py run --config {config.resolve()} --desk landing >> {tmp_path / 'landing-runner.log'} 2>&1 < /dev/null &"
    assert shell.environ["AWS_PROFILE"] == "release-operator"
    assert heartbeat(shell, "landing")["restart"] == restart
    _, held = live_runner(shell, config, tmp_path, "landing")
    monkeypatch.setattr(shell, "sleep", lambda seconds: held.close())
    assert cli(shell, config, "handoff", "--desk", "landing") == 0
    assert shell.successors == [successor(config, tmp_path, "landing")]
    assert shell.spawned_env["desk-runner.py"] == {"AWS_PROFILE": "release-operator"}
    del shell.environ["AWS_PROFILE"]
    orca_pass(shell, config)
    assert "AWS_PROFILE" not in shell.environ


def test_a_handoff_whose_runner_still_runs_after_twenty_minutes_starts_no_successor_and_tells_root_its_pid(shell, config, tmp_path):
    _, held = live_runner(shell, config, tmp_path, "orca")
    started = shell.now()
    with held:
        assert cli(shell, config, "handoff", "--desk", "orca") == 1
    assert shell.successors == [] and shell.now() - started >= timedelta(minutes=20)
    [line] = [line for line in escalations(shell) if line.startswith("HANDOFF-STALLED ")]
    assert f"runner {os.getpid()} still runs the orca desk after 0:20:00, so no successor started" in line and "handoff --desk orca` starts the successor" in line


@pytest.mark.parametrize(("version", "in_place"), [(runner_module.RUNNING_VERSION, True), ("0.0.1", False)], ids=["re-executed", "old-version"])
def test_a_handoff_whose_runner_re_executes_into_this_version_under_its_pid_is_done_with_no_successor(shell, config, tmp_path, monkeypatch, version, in_place):
    runner, held = live_runner(shell, config, tmp_path, "orca")
    sleep = shell.sleep

    def beats(seconds):
        sleep(seconds)
        with monkeypatch.context() as patched:
            patched.setattr(runner_module, "RUNNING_VERSION", version)
            runner.alive_posted.clear()
            runner.post_alive("orca")

    monkeypatch.setattr(shell, "sleep", beats)
    with held:
        assert cli(shell, config, "handoff", "--desk", "orca") == (0 if in_place else 1)
    stalled = [line for line in escalations(shell) if line.startswith("HANDOFF-STALLED ")]
    assert shell.successors == [] and bool(stalled) is not in_place and runner.stop_marker().exists() is not in_place


@pytest.mark.parametrize("desk", ["orca", "landing"])
def test_a_second_runner_on_the_same_config_and_desk_refuses_to_start_and_names_the_first(shell, config, tmp_path, capsys, desk):
    _, held = live_runner(shell, config, tmp_path, desk)
    with held:
        assert cli(shell, config, "run", "--desk", desk, "--once") == 3
    assert f"runner {os.getpid()} already runs the {desk} desk" in capsys.readouterr().err
    assert heartbeat(shell, desk) is None


@pytest.mark.parametrize("desk", ["orca", "landing"])
def test_a_runner_started_while_its_handed_off_predecessor_still_holds_the_lock_waits_for_it_to_exit_then_runs(shell, config, tmp_path, monkeypatch, desk):
    runner, held = live_runner(shell, config, tmp_path, desk)
    predecessor = contextlib.ExitStack()
    predecessor.enter_context(held)
    marker = runner.stop_marker()
    marker.write_text(f"{os.getpid()}\n")
    sleep = shell.sleep

    def exits(seconds):
        sleep(seconds)
        marker.unlink(missing_ok=True)
        predecessor.close()

    monkeypatch.setattr(shell, "sleep", exits)
    assert cli(shell, config, "run", "--desk", desk, "--once") != 3
    assert heartbeat(shell, desk) is not None


def test_the_landing_loop_exits_on_its_stop_marker_within_one_interval_and_its_successor_resumes_from_the_same_book(shell, config, tmp_path):
    """Tasks 36d2185 and f04ea6b: landing runner 76826 stayed on 0.7.183 through the 2:48 PM rotation on Oct 20, because its loop never read the stop marker."""
    land_fast(shell, config)
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    runner.stop_marker().write_text(f"{os.getpid()}\n")
    before = shell.now()
    assert runner_module.run_landing(runner, False) == 0
    assert not runner.stop_marker().exists() and shell.now() - before < timedelta(seconds=180)
    assert shell.posts == []
    landing_pass(shell, config)
    assert [post["kind"] for post in shell.posts] == ["release"]
    landing_pass(shell, config)
    assert [post["kind"] for post in shell.posts] == ["release"]
    shell.sleep(20 * 60)
    landing_pass(shell, config)
    assert [post["kind"] for post in shell.posts] == ["release", "blocker"]


def test_handoff_to_an_install_missing_a_brief_stops_no_runner_and_starts_no_successor(shell, config, tmp_path, monkeypatch):
    runner, held = live_runner(shell, config, tmp_path, "orca")
    monkeypatch.setattr(runner_module, "BRIEF_FILES", (tmp_path / "install" / "reference" / "finder-desk-brief.md",))
    assert cli(shell, config, "handoff", "--desk", "orca") == 1
    assert not runner.stop_marker().exists()
    assert shell.successors == []
    held.close()


def test_handoff_to_an_install_missing_a_file_its_marketplace_copy_holds_stops_no_runner(shell, config, tmp_path, monkeypatch, capsys):
    install, source = tmp_path / "cache/long-running/0.7.503/skill", tmp_path / "marketplaces/skills/skill"
    for root, names in ((install, ["reference/finder-desk-brief.md", "scripts/desk-runner.py"]), (source, ["reference/finder-desk-brief.md", "reference/orca-lane-brief.md", "scripts/desk-runner.py", "scripts/lanes.py", "scripts/__pycache__/lanes.cpython-313.pyc"])):
        for name in names:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text("")
    monkeypatch.setattr(runner_module, "SCRIPTS", install / "scripts")
    monkeypatch.setattr(runner_module.drive, "marketplace_copy", lambda path: source)
    runner, held = live_runner(shell, config, tmp_path, "orca")
    assert cli(shell, config, "handoff", "--desk", "orca") == 1
    assert "this install lacks reference/orca-lane-brief.md, scripts/lanes.py, so no runner was stopped" in capsys.readouterr().err
    assert not runner.stop_marker().exists()
    assert shell.successors == []
    held.close()


@pytest.mark.parametrize("desk", ["orca", "landing"])
def test_handoff_with_no_heartbeat_has_no_runner_to_stop_and_starts_one(shell, config, tmp_path, desk):
    assert cli(shell, config, "handoff", "--desk", desk) == 0
    assert not runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store")).stop_marker().exists()
    assert shell.successors == [successor(config, tmp_path, desk)]


def test_a_desk_posts_its_alive_record_when_it_starts_before_its_first_pass_ends(shell, config, tmp_path):
    shell.beats.append({"seq": 0, "drive": "d1", "lane": "desk-runner", "kind": "state", "topic": "landing", "at": "2026-10-27T21:58:00Z", "text": "landing alive 2:58 PM pass 9", "fields": {"attrs": {"pid": "1"}}})
    runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store")).keep_alive("landing").set()
    assert heartbeat(shell, "landing")["pid"] == str(os.getpid())
    assert shell.alive == [f"landing alive {shell.clock.astimezone(runner_module.PACIFIC):%-I:%M %p} pass 0"]


def test_each_landing_pass_posts_its_own_alive_record(shell, config, tmp_path):
    started = shell.clock
    landing_pass(shell, config)
    assert shell.alive[0] == f"landing alive {started.astimezone(runner_module.PACIFIC):%-I:%M %p} pass 0"
    beat = heartbeat(shell, "landing")
    assert beat["pid"] == str(os.getpid())
    assert beat["restart"] == f"nohup desk-runner.py run --config {config.resolve()} --desk landing >> {tmp_path / 'landing-runner.log'} 2>&1 < /dev/null &"
    assert heartbeat(shell, "orca") is None


def test_the_alive_record_names_the_command_in_flight_and_how_long_it_has_run(shell, config, tmp_path, monkeypatch):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    monkeypatch.setitem(runner_module.Shell.in_progress, 1, ("stack-enqueue 42627 42676 --check", time.monotonic() - 125))

    runner.post_alive("landing")

    assert shell.alive == [f"landing alive {shell.clock.astimezone(runner_module.PACIFIC):%-I:%M %p} pass 1 running `stack-enqueue 42627 42676 --check` for 2m05s"]


def test_a_pass_that_raises_posts_a_blocker_and_the_runner_goes_on_until_the_same_raise_twice_in_a_row(shell, config, tmp_path, monkeypatch):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    raises = iter([UnicodeDecodeError("utf-8", b"\xb5", 0, 1, "invalid start byte"), None, KeyError("base"), KeyError("base")])

    def run(landing):
        if error := next(raises):
            raise error

    monkeypatch.setattr(runner_module.Landing, "run", run)
    monkeypatch.setattr(runner, "rest", lambda seconds: False)

    assert runner_module.run_landing(runner, False) == 1
    assert [(post["to"], post["text"]) for post in blockers(shell)] == [
        ("root", "landing runner pass raised UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb5 in position 0: invalid start byte; the runner goes on with its next pass"),
        ("root", "landing runner pass raised KeyError: 'base'; the runner goes on with its next pass"),
        ("root", "landing runner pass raised KeyError: 'base'; it raised the same on the pass before, so the runner exited; restart it with the restart attr of its newest alive record"),
    ]


def test_the_landing_loop_exits_on_its_stop_marker_at_the_first_step(shell, config, tmp_path):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    runner.stop_marker().write_text(f"{os.getpid()}\n")
    assert runner_module.run_landing(runner, False) == 0
    assert heartbeat(shell, "landing") is not None
    assert not runner.stop_marker().exists()


def test_handoff_of_the_landing_desk_waits_for_the_landing_runner_and_not_the_orca_runner(shell, config, tmp_path, monkeypatch):
    _, orca = live_runner(shell, config, tmp_path, "orca")
    runner, landing = live_runner(shell, config, tmp_path, "landing")
    named = []

    def exits(seconds):
        named.append(runner.stop_marker().read_text().strip())
        runner.stop_marker().unlink()
        landing.close()

    monkeypatch.setattr(shell, "sleep", exits)
    with orca:
        assert cli(shell, config, "handoff", "--desk", "landing") == 0
    assert named == [str(os.getpid())]
    assert shell.successors == [successor(config, tmp_path, "landing")]


def test_an_inbox_relay_replies_to_the_lanes_latest_open_question(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.questions = [
        {"id": "msg_other", "from_handle": "dispatch:ctx_z", "created_at": "2026-10-01T17:59:00Z"},
        {"id": "msg_q9", "from_handle": "dispatch:ctx_a", "created_at": "2026-10-01T17:50:00Z"},
        {"id": "msg_q8", "from_handle": "term_ctx_a", "created_at": "2026-10-01T17:55:00Z"},
        {"id": "msg_q7", "from_handle": "dispatch:ctx_a", "created_at": "2026-10-01T17:40:00Z"},
    ]
    shell.inboxes["term_ctx_a"] = [{"id": "msg_r8", "thread_id": "msg_q8"}]
    orca_pass(shell, config)
    seq = desk_orders(shell, f"- orca-desk: relay to {LANE}: walker: router GO")
    orca_pass(shell, config)
    [reply] = replies(shell)
    assert reply[reply.index("--id") + 1] == "msg_q9"
    assert reply[reply.index("--body") + 1].startswith("walker: router GO\n")
    assert shell.sends() == []
    [line] = escalations(shell)
    assert line == f"RELAYED inbox@cci#{seq} {LANE}: reply to question msg_q9 of dispatch ctx_a"


def test_an_inbox_relay_to_a_lane_without_a_live_dispatch_fails_visibly(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a", status="completed")
    orca_pass(shell, config)
    desk_orders(shell, f"R3 orca-desk: relay to {LANE} and ghost: G332")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert shell.sends() == [] and replies(shell) == []
    assert [line for line in escalations(shell) if line.startswith("RELAY")] == [
        f"RELAY-FAILED R3 {LANE}: no live dispatch (ctx_a is completed)",
        "RELAY-FAILED R3 ghost: no live dispatch",
    ]


def test_a_relay_line_outside_the_grammar_fails_visibly_and_relays_nothing(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    desk_orders(
        shell,
        f"R1050 (12:0x AM PT) orca-desk: relay G328 (AIG applies held again) to {LANE}.",
        f"R1048 (10:5x PM PT) orca-desk: relay to {LANE}: `walker: router GO`; relay to walker: `d-land: sandsql GO`",
    )
    orca_pass(shell, config)
    assert shell.sends() == []
    lines = [line for line in escalations(shell)]
    assert [line.split(":", 1)[0] for line in lines] == ["RELAY-FAILED R1050 inbox", "RELAY-FAILED R1048 inbox"]
    assert all(runner_module.RELAY_GRAMMAR in line for line in lines)


def test_a_line_appended_to_the_desk_inbox_file_relays_and_launches_nothing(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    path = tmp_path / "inbox/orca-desk.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"R4 orca-desk: relay to {LANE}: rebase onto dev\nR5 orca-desk: launch {LANE}-2 sol xhigh brief={launch_brief(tmp_path)}\n")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert shell.sends() == [] and launches(shell) == [] and escalations(shell) == []


def test_an_inbox_relay_under_a_key_holding_another_relay_fails_visibly(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    cli(shell, config, "relay", "--key", "R5", "--lane", LANE, "--text", "rebase onto dev")
    desk_orders(shell, f"R5 orca-desk: relay to {LANE}: deploy now")
    orca_pass(shell, config)
    assert [call[call.index("--body") + 1].split("\n", 1)[0] for call in shell.sends()] == ["rebase onto dev"]
    [line] = escalations(shell)
    assert line == f"RELAY-FAILED R5 {LANE}: R5 already holds a different relay to {LANE} (accepted)"


def launch_brief(tmp_path: Path) -> Path:
    brief = tmp_path / "fix-brief.md"
    brief.write_text(f"ccx: place=local\nbrief\nScale: no N\n\nRegister slice: lane={LANE} step=1.9\n")
    return brief


def refreshed(tmp_path: Path, brief: Path) -> list[str]:
    return ["slice", "--repo", str(tmp_path / "checkout"), "--drive", "d1", "--refresh", str(brief)]


def test_a_launch_line_whose_brief_has_no_slice_line_is_refused_unless_it_is_an_incident_fix(shell, config, tmp_path):
    brief = tmp_path / "fix-brief.md"
    brief.write_text("brief")
    orca_pass(shell, config)
    desk_orders(shell, f"R40 orca-desk: launch {LANE} sol xhigh brief={brief}")
    orca_pass(shell, config)

    assert launches(shell) == [] and shell.refreshes == []
    assert escalations(shell) == [
        f"LAUNCH-FAILED R40 {LANE}: brief {brief} has no `Register slice: lane=<name> step=<n>` line; add one and run `rulings.py slice --refresh {brief}`; nothing was launched"
    ]

    shell.launch_line = f"{LANE} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    desk_orders(shell, f"R41 orca-desk: launch {LANE} NOW incident xhigh brief={brief}")
    orca_pass(shell, config)
    orca_pass(shell, config)

    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, "incident", "xhigh", str(brief)]]
    assert brief.read_text() == f"brief\n\nRegister slice: lane={LANE} step=incident\n"
    assert shell.refreshes == [refreshed(tmp_path, brief)]
    assert not [line for line in escalations(shell) if "R41" in line and "LAUNCH-FAILED" in line]


def test_a_launch_line_whose_brief_has_no_scale_line_is_refused_unless_it_is_an_incident_fix(shell, config, tmp_path):
    brief = tmp_path / "fix-brief.md"
    brief.write_text(f"brief\n\nRegister slice: lane={LANE} step=1.9\n")
    orca_pass(shell, config)
    desk_orders(shell, f"R43 orca-desk: launch {LANE} sol xhigh brief={brief}")
    orca_pass(shell, config)

    assert launches(shell) == [] and shell.refreshes == []
    assert escalations(shell) == [f"LAUNCH-FAILED R43 {LANE}: brief {brief} has no `Scale:` line; add the one the lane brief template gives: the N each path runs at, or `no N`; nothing was launched"]

    shell.launch_line = f"{LANE} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    desk_orders(shell, f"R44 orca-desk: launch {LANE} NOW incident xhigh brief={brief}")
    orca_pass(shell, config)
    orca_pass(shell, config)

    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, "incident", "xhigh", str(brief)]]


def test_a_launch_refreshes_its_slice_first_and_a_slice_that_does_not_read_blocks_nothing(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    shell.refresh_error = "no register for d1"
    shell.launch_line = f"{LANE} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    orca_pass(shell, config)
    desk_orders(shell, f"R42 orca-desk: launch {LANE} sol xhigh brief={brief}")
    orca_pass(shell, config)
    orca_pass(shell, config)

    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, "sol", "xhigh", str(brief)]]
    assert shell.refreshes == [refreshed(tmp_path, brief)]
    assert f"SLICE-UNREAD slice:R42 {LANE}: `rulings.py slice --refresh` exited 1: no register for d1; {LANE} launches on its brief without a fresh register slice" in escalations(shell)
    assert ("blocker", "slice:R42") in [(post["kind"], post["topic"]) for post in shell.posts]


@pytest.mark.parametrize(
    ("line", "key", "lane", "now", "model", "effort", "role", "brief"),
    [
        ("R1907 (7:0x PM PT) orca-desk: launch alerts-api-1n91-fix NOW incident xhigh brief=/d/fix-brief.md", "R1907", "alerts-api-1n91-fix", " NOW", "incident", "xhigh", None, "/d/fix-brief.md"),
        ("- orca-desk: launch docs-lane astra high brief=/d/b.md", None, "docs-lane", None, "astra", "high", None, "/d/b.md"),
        ("R2 orca-desk: launch inc-evidence NOW incident xhigh role=evidence brief=/d/ev.md", "R2", "inc-evidence", " NOW", "incident", "xhigh", "evidence", "/d/ev.md"),
    ],
)
def test_the_launch_grammar_names_the_key_lane_route_role_and_brief(line, key, lane, now, model, effort, role, brief):
    directive = runner_module.INBOX_DIRECTIVE.match(line)
    spec = runner_module.LAUNCH_SPEC.match(directive["rest"])
    assert (directive["verb"], directive["key"], spec["lane"], spec["now"], spec["model"], spec["effort"], spec["role"], spec["brief"]) == ("launch", key, lane, now, model, effort, role, brief)


DIRECTIVE_SPECS = {
    "relay": runner_module.RELAY_TO,
    "launch": runner_module.LAUNCH_SPEC,
    "alert": runner_module.ALERT_SPEC,
    "incident": runner_module.INCIDENT_SPEC,
    "hold": runner_module.HOLD_SPEC,
    "unhold": runner_module.UNHOLD_SPEC,
    "withdraw": runner_module.WITHDRAW_SPEC,
}


@pytest.mark.parametrize(
    ("line", "verb"),
    [
        ("R12 (9:46 PM) orca-desk: relay to lane-a: rebase onto dev", "relay"),
        ("R12 orca-desk: relay to lane-a, lane-b and lane-c: G331 resumes the chain", "relay"),
        ("R13 (9:46 PM) orca-desk: launch lane-a NOW opus xhigh brief=/d/brief.md", "launch"),
        ("R13 orca-desk: launch lane-a sonnet low brief=/d/brief.md", "launch"),
        ("R14 (7:05 PM) orca-desk: launch lane-a NOW claude-opus-5-5 high brief=/d/brief.md", "launch"),
        ("R1907 (7:05 PM) orca-desk: launch alerts-api-1933-fix NOW incident xhigh brief=/Users/me/.claude/scratch/release-v3/briefs/alerts-api-1933-fix.md", "launch"),
        ("orca-desk: launch alerts-api-1933-fix NOW incident xhigh brief=/Users/me/.claude/scratch/release-v3/briefs/alerts-api-1933-fix.md", "launch"),
        ("orca-desk: launch alerts-api-1933-fix-case2 NOW incident xhigh brief=/d/case2.md", "launch"),
        ("R16 (9:46 PM) orca-desk: withdraw R13", "withdraw"),
        ("R16 (9:46 PM) orca-desk: withdraw lane-a", "withdraw"),
        ("orca-desk: withdraw unfixed:msg_fc99fa64dc83:2", "withdraw"),
        ("orca-desk: alert dd-333042395 https://app.datadoghq.com/monitors/333042395 :: Datadog OK -> Alert at 2026-10-16T01:41:35+00:00: Runner refusing runs", "alert"),
        ("orca-desk: incident dd-333042395 https://app.datadoghq.com/monitors/333042395 reported=18:41 :: Datadog OK -> Alert Runner refusing runs", "incident"),
        ("orca-desk: incident alerts-api-1841 https://in-the-forge.slack.com/archives/C0822AHFY3G/p1791941123606789 reported=18:41 :: Datadog Warn mount failing", "incident"),
        ("orca-desk: incident eng_alerts-1841 https://in-the-forge.slack.com/archives/C0822AHFY3G/p1791941123606789 reported=18:41 :: Sentry [api] fetch failed", "incident"),
        ("orca-desk: incident runner-refusing-1841 https://app.datadoghq.com/monitors/333042395 reported=6:41 PM :: Datadog 333042395 Runner refusing runs", "incident"),
        ("orca-desk: hold release-api owner=lane-a :: api deploys, until #123 lands", "hold"),
        ("orca-desk: unhold release-api", "unhold"),
    ],
)
def test_every_documented_inbox_form_parses(line, verb):
    directive = runner_module.INBOX_DIRECTIVE.match(line)
    assert directive and directive["verb"] == verb
    assert DIRECTIVE_SPECS[verb].match(directive["rest"])


def test_an_inbox_launch_line_launches_once_and_now_marks_it_urgent(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    shell.launch_line = f"{LANE} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    orca_pass(shell, config)
    desk_orders(shell, f"R1907 (7:0x PM PT) orca-desk: launch {LANE} NOW opus xhigh brief={brief}")
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_n")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, "opus", "xhigh", str(brief)]]
    action = incident(tmp_path, f"desk-lane-{LANE}").actions["R1907"]
    assert (action.status, json.loads(action.target)["urgent"]) == ("verified", True)
    assert [line for line in escalations(shell)] == [f"LAUNCHED R1907 {LANE}: dispatch ctx_n terminal term_ctx_n"]
    assert [(post["kind"], post["topic"]) for post in shell.posts] == [("report", "R1907")]


@pytest.mark.parametrize("via", ["inbox", "cli"])
def test_a_verified_launch_records_the_lanes_dispatch_and_terminal_and_relays_reach_it(shell, config, tmp_path, capsys, via):
    brief = launch_brief(tmp_path)
    shell.launch_line = f"{LANE} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    orca_pass(shell, config)
    if via == "inbox":
        desk_orders(shell, f"R9001 orca-desk: launch {LANE} opus high brief={brief}")
    else:
        cli(shell, config, "launch", "--key", "R9001", "--lane", LANE, "--model", "opus", "--effort", "high", "--brief", str(brief))
    orca_pass(shell, config)
    orca_pass(shell, config)
    desk_orders(shell, f"R9004 orca-desk: relay to {LANE}: rebase onto dev")
    orca_pass(shell, config)
    assert (shell.receipts / f"{LANE}.terminal").read_text() == "term_ctx_n\n"
    assert [call[call.index("--to") + 1] for call in shell.sends()] == ["dispatch:ctx_n"]
    assert [line for line in escalations(shell) if line.startswith(("LAUNCHED", "RELAY"))] == [
        f"LAUNCHED R9001 {LANE}: dispatch ctx_n terminal term_ctx_n",
        f"RELAYED R9004 {LANE}: relay to dispatch ctx_n",
    ]
    capsys.readouterr()
    assert cli(shell, config, "show") == 0
    assert f"## desk-lane-{LANE} owner unlaunched generation 0 pending ctx_n" in capsys.readouterr().out


def test_a_lane_launched_by_hand_with_orca_launch_defaults_receives_relays(shell, config, tmp_path):
    state = Path.home() / ".claude/scratch/orca-launch/run_1"
    state.mkdir(parents=True)
    (state / f"{LANE}.json").write_text(json.dumps({"result": {"taskId": "task_h", "dispatchId": "ctx_h"}}))
    (state / f"{LANE}.terminal").write_text("term_h\n")
    shell.dispatches["ctx_h"] = {"status": "dispatched", "terminal": "term_h"}
    orca_pass(shell, config)
    desk_orders(shell, f"R9010 orca-desk: relay to {LANE}: rebase onto dev")
    cli(shell, config, "relay", "--key", "R9011", "--lane", LANE, "--text", "then rerun CI")
    orca_pass(shell, config)
    assert [call[call.index("--to") + 1] for call in shell.sends()] == ["dispatch:ctx_h", "dispatch:ctx_h"]
    assert [line for line in escalations(shell) if line.startswith("RELAY")] == [f"RELAYED R9010 {LANE}: relay to dispatch ctx_h"]


def test_an_inbox_launch_under_a_key_already_held_launches_nothing_twice(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    orca_pass(shell, config)
    desk_orders(shell, f"R9 orca-desk: launch {LANE} sol xhigh brief={brief}", f"R9 orca-desk: launch {LANE} sol xhigh brief={brief}")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert len(launches(shell)) == 1
    assert [line for line in escalations(shell) if "LAUNCH-FAILED" in line] == [
        f"LAUNCH-FAILED R9 {LANE}: R9 already holds a launch for {LANE} (accepted); nothing was launched"
    ]


def test_an_inbox_launch_outside_the_grammar_or_route_fails_visibly_and_launches_nothing(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    orca_pass(shell, config)
    desk_orders(
        shell,
        f"R1908 (7:1x PM PT) orca-desk: launch {LANE} NOW — sol xhigh fast (incident route), brief {brief}; relay to walker: go",
        f"R1909 orca-desk: launch {LANE} NOW",
        f"R1910 orca-desk: launch {LANE} gemini xhigh brief={brief}",
        f"R1911 orca-desk: launch {LANE} sol extreme brief={brief}",
        f"R1912 orca-desk: launch {LANE} sol xhigh brief={tmp_path}/missing.md",
    )
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert launches(shell) == []
    lines = [line for line in escalations(shell)]
    assert [line.split(":", 1)[0] for line in lines] == ["LAUNCH-FAILED R1908 inbox", "LAUNCH-FAILED R1909 inbox", f"LAUNCH-FAILED R1910 {LANE}", f"LAUNCH-FAILED R1911 {LANE}", f"LAUNCH-FAILED R1912 {LANE}"]
    assert all(runner_module.LAUNCH_GRAMMAR in line for line in lines[:2])
    assert "gemini is not a model orca-launch.sh starts" in lines[2] and "extreme is not an effort" in lines[3] and "missing.md is not a file" in lines[4]


def test_an_inbox_launch_for_a_lane_with_a_live_dispatch_fails_visibly(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    desk_orders(shell, f"R10 orca-desk: launch {LANE} NOW sol xhigh brief={brief}")
    orca_pass(shell, config)
    assert launches(shell) == []
    [line] = escalations(shell)
    assert line == f"LAUNCH-FAILED R10 {LANE}: dispatch ctx_a is dispatched; relay to it instead; nothing was launched"


def holding(monkeypatch, tmp_path: Path, holder: str = LANE, pr: str = "#1201", ahead: int = 2, pushed: str = "pushed") -> None:
    worktree = runner_module.lanes.Worktree(path=tmp_path / holder, branch=holder, ahead=ahead, pushed=pushed, pr=pr)
    monkeypatch.setattr(runner_module.Runner, "lane_worktrees", lambda self, lane: [worktree] if lane == holder else [])


@pytest.mark.parametrize(
    ("now", "ahead", "pushed", "duplicate"),
    [("", 2, "pushed", True), ("NOW ", 2, "pushed", False), ("", 0, "not pushed", False), ("", 0, "no origin branch", False), ("", 0, "pushed", True)],
)
def test_an_inbox_launch_naming_a_pr_a_live_lane_holds_is_refused_as_a_duplicate(shell, config, tmp_path, monkeypatch, now, ahead, pushed, duplicate):
    clocked_lane(shell, config, tmp_path)
    holding(monkeypatch, tmp_path, ahead=ahead, pushed=pushed)
    brief = tmp_path / "cache.md"
    brief.write_text("ccx: place=local\nFinish the cache half of #1201.\nScale: no N\n\nRegister slice: lane=api-cache step=1.9\n")
    desk_orders(shell, f"R11 orca-desk: launch api-cache {now}sol xhigh brief={brief}")
    orca_pass(shell, config)
    orca_pass(shell, config)
    refused = [line for line in escalations(shell) if line.startswith("LAUNCH-DUPLICATE")]
    assert bool(refused) is duplicate
    if duplicate:
        assert refused == [runner_module.duplicate_line("R11", "api-cache", f"{LANE}: {runner_module.lanes.summary(LANE, runner_module.Runner.lane_worktrees(None, LANE))}")]
    assert len(launches(shell)) == (1 if duplicate else 2)


def test_a_cli_launch_on_a_live_lanes_incident_is_refused_unless_owner_directed(shell, config, tmp_path, monkeypatch, capsys):
    clocked_lane(shell, config, tmp_path, lane="inc-sand-fix")
    holding(monkeypatch, tmp_path, holder="inc-sand-fix", pr="")
    brief = tmp_path / "second.md"
    brief.write_text("Fix the reader.\n")
    assert cli(shell, config, "launch", "--key", "launch-two", "--lane", "inc-sand-fix-2", "--model", "opus", "--effort", "xhigh", "--brief", str(brief)) == 1
    assert "LAUNCH-DUPLICATE launch-two inc-sand-fix-2: inc-sand-fix:" in capsys.readouterr().err
    assert cli(shell, config, "launch", "--key", "launch-three", "--lane", "inc-sand-fix-2", "--model", "opus", "--effort", "xhigh", "--brief", str(brief), "--owner-directed") == 0


@pytest.mark.parametrize(
    ("kind", "text", "go", "minutes", "unowned"),
    [
        ("report", "NEW CLASS SAND-5A uploads fail on retry", "", 11, True),
        ("state", "CLASS SAND-5A uploads fail on retry", "", 11, True),
        ("state", "PR2 is green at the head", "", 11, False),
        ("report", "NEW CLASS SAND-5A uploads fail on retry", "launch inc-sand-fix for SAND-5A", 11, False),
        ("report", "NEW CLASS SAND-5A uploads fail on retry", "launch inc-sand-evidence to collect SAND-5A logs", 11, True),
        ("report", "NEW CLASS SAND-5A uploads fail on retry", "", 5, False),
        ("report", "SAND-5A uploads fail on retry", "", 11, False),
        ("incident", "[SAND-5A] uploads fail on retry", "", 11, True),
        ("incident", "STEP1+2: sand/latest.json is 5273 bytes, SHA256 3588023a49a1516e, and SAND-5A logs match", "", 11, False),
    ],
)
def test_a_class_record_with_no_fix_lane_go_in_ten_minutes_raises_one_blocker(shell, config, tmp_path, kind, text, go, minutes, unowned):
    orca_pass(shell, config)
    seq = cci_record(shell, lane="inc-sand-evidence", kind=kind, text=text, at=actions.stamp(shell.clock))
    if go:
        lane_record(shell, "root", "go", go)
    shell.sleep(minutes * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    lines = [line for line in escalations(shell) if line.startswith("CLASS-UNOWNED")]
    assert len(lines) == unowned
    if unowned:
        assert lines[0].startswith(f"CLASS-UNOWNED tag:SAND5A inc-sand-evidence: #{seq} at ")
        assert "no fix-lane GO naming tag:SAND5A" in lines[0]


def cci_go(shell: FakeShell, text: str, source: str = "post") -> None:
    shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": "root", "kind": "go", "text": text, "to": ["orca-desk"], "source": source})


def test_a_launch_posted_to_the_orca_desk_on_cci_launches_once_and_history_never_replays(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    shell.launch_line = f"{LANE} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    cci_go(shell, f"R1345 (5:06 PM) orca-desk: launch {LANE} NOW incident xhigh brief={brief}")
    orca_pass(shell, config)
    cci_go(shell, f"R1346 (5:16 PM) orca-desk: launch {LANE} NOW incident xhigh brief={brief}")
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_n")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, "incident", "xhigh", str(brief)]]
    container = incident(tmp_path, f"desk-lane-{LANE}")
    assert container.actions["R1346"].status == "verified" and "R1345" not in container.actions
    assert escalations(shell) == [f"LAUNCHED R1346 {LANE}: dispatch ctx_n terminal term_ctx_n"]


def test_a_record_cci_imported_from_the_old_desk_inbox_file_relays_once(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    cci_go(shell, f"- orca-desk: relay to {LANE}: rebase onto dev", source=f"import:{(tmp_path / 'inbox/orca-desk.md').resolve()}")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert len(shell.sends()) == 1
    assert escalations(shell) == [f"RELAYED inbox@cci#{shell.records[-1]['seq']} {LANE}: relay to dispatch ctx_a"]


def cci_record(shell: FakeShell, **fields) -> int:
    seq = 34766 + len(shell.records)
    shell.records.append({"seq": seq, "drive": "d1", "lane": "root", "kind": "go", "to": [], "source": "post", **fields})
    return seq


LANE_GONE_ADVICE = "a roster or cci miss is not absence: SendMessage it and spawn a successor only when it has not answered in 5 minutes"


def dispatch_gone_lines(shell: FakeShell) -> list[str]:
    return [line for line in escalations(shell) if line.startswith("LANE-GONE ")]


def test_an_orca_lane_whose_dispatch_failed_in_an_outage_is_lane_gone_on_the_first_pass_orca_answers(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    seq = cci_record(shell, lane=LANE, kind="report", text=f"REPORT {LANE}: rebased", at=actions.stamp(shell.clock))
    orca_pass(shell, config)
    shell.orca_down = UNREACHABLE_FAILURES[0]
    shell.sleep(60)
    orca_pass(shell, config)
    shell.dispatches["ctx_a"]["status"] = "failed"
    shell.orca_down = None
    shell.sleep(60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert dispatch_gone_lines(shell) == [
        f"LANE-GONE lane-gone:{LANE} {LANE}: Orca shows its dispatch ctx_a failed; its last record is #{seq} report at 11:00, 2 min ago;"
        " Orca gave no answer from 11:01 until this pass (runtime_unavailable: Could not connect to the running Orca app. Restart Orca.); relaunch it from its brief and handoff"
    ]
    assert [line.split()[0] for line in escalations(shell) if line.startswith("ORCA-")] == ["ORCA-UNREACHABLE", "ORCA-BACK"]


def test_an_orca_lane_whose_terminal_is_missing_is_lane_gone_on_that_pass(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    shell.dispatches["ctx_a"]["missing"] = True
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert dispatch_gone_lines(shell) == [f"LANE-GONE lane-gone:{LANE} {LANE}: Orca shows its dispatch ctx_a dispatched with its terminal term_ctx_a missing; it has posted no record; relaunch it from its brief and handoff"]


@pytest.mark.parametrize("ended", ["done", "worker_done"])
def test_an_orca_lane_that_ended_its_work_is_never_lane_gone_when_its_dispatch_settles(shell, config, tmp_path, ended):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    if ended == "done":
        lane_record(shell, LANE, "done")
    else:
        shell.receive({"type": "worker_done", "subject": "shipped", "payload": json.dumps({"dispatchId": "ctx_a", "outcome": "succeeded"})})
    shell.dispatches["ctx_a"]["status"] = "completed"
    orca_pass(shell, config)
    assert dispatch_gone_lines(shell) == []


@pytest.mark.parametrize(("seen_minutes_ago", "gone"), [(20, True), (5, False), (None, True)])
def test_a_lane_silent_past_a_root_order_logs_one_lane_gone(shell, config, tmp_path, seen_minutes_ago, gone):
    last = cci_record(shell, lane="p8-lane", kind="head", text="HEAD #29020 cccc333333", at=actions.stamp(shell.clock - timedelta(minutes=40)))
    order = cci_record(shell, kind="decide", to=["p8-lane", "drive-watchdog"], text="p8-lane: rebase onto the new seed", at=actions.stamp(shell.clock - timedelta(minutes=30)))
    cci_record(shell, lane="drive-watchdog", kind="note", text="ack", at=actions.stamp(shell.clock - timedelta(minutes=1)))
    if seen_minutes_ago is None:
        shell.gone.add("p8-lane")
    else:
        shell.seen["p8-lane"] = actions.stamp(shell.clock - timedelta(minutes=seen_minutes_ago))
    orca_pass(shell, config)
    orca_pass(shell, config)
    lines = [line for line in escalations(shell) if line.startswith("LANE-GONE")]
    assert len(lines) == gone
    if gone:
        assert lines[0].startswith(f"LANE-GONE lane-gone:p8-lane p8-lane: root #{order} to p8-lane unanswered; its last record is #{last} head at ")
        assert lines[0].endswith(("cci last saw it 20 min ago; " + LANE_GONE_ADVICE, "cci lists no session for it; " + LANE_GONE_ADVICE))


def test_with_a_desk_lane_a_repeat_lane_gone_still_reaches_root(shell, config, tmp_path):
    desk_lane(config)
    cci_record(shell, lane="p8-lane", kind="head", text="HEAD #29020 cccc333333", at=actions.stamp(shell.clock - timedelta(minutes=40)))
    cci_record(shell, kind="decide", to=["p8-lane"], text="p8-lane: rebase onto the new seed", at=actions.stamp(shell.clock - timedelta(minutes=30)))
    shell.gone.add("p8-lane")
    orca_pass(shell, config)
    orca_pass(shell, config)
    cci_record(shell, kind="decide", to=["p8-lane"], text="p8-lane: and drop the old shim", at=actions.stamp(shell.clock - timedelta(minutes=1)))
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert heard(shell, "LANE-GONE") == ["root", f"root,{DESK},p8-lane"]


@pytest.mark.parametrize("answer", [{"lane": "p8-lane-2", "kind": "note", "text": "taking over"}, {"kind": "stand-down", "to": ["p8-lane"], "text": "p8-lane: stand down"}])
def test_a_successor_record_or_a_root_stand_down_answers_a_lane_order(shell, config, tmp_path, answer):
    cci_record(shell, lane="p8-lane", kind="head", text="HEAD #29020 cccc333333", at=actions.stamp(shell.clock - timedelta(minutes=40)))
    cci_record(shell, kind="decide", to=["p8-lane"], text="p8-lane: rebase onto the new seed", at=actions.stamp(shell.clock - timedelta(minutes=30)))
    cci_record(shell, **answer, at=actions.stamp(shell.clock - timedelta(minutes=20)))
    shell.gone |= {"p8-lane", "p8-lane-2"}
    orca_pass(shell, config)
    assert not [line for line in escalations(shell) if line.startswith("LANE-GONE")]


@pytest.mark.parametrize("why", ["fresh order", "fresh spawn", "resolved report", "answered report", "live dispatch"])
def test_a_fresh_order_a_fresh_spawn_or_a_report_the_root_settled_logs_no_lane_gone(shell, config, tmp_path, why):
    cci_record(shell, lane="p8-lane", kind="head", text="HEAD #29020 cccc333333", at=actions.stamp(shell.clock - timedelta(minutes=50)))
    if why.endswith("report"):
        report = cci_record(shell, lane="desk-runner", kind="report", text="LANE-GONE lane-gone:p8-lane:34700 p8-lane: root #34700 to p8-lane unanswered", at=actions.stamp(shell.clock - timedelta(minutes=45)))
        cci_record(shell, kind="note", to=["desk-runner"], text="p8-lane is alive", at=actions.stamp(shell.clock - timedelta(minutes=44)), **({"resolves": [report]} if why == "resolved report" else {"re": report}))
    cci_record(shell, kind="decide", to=["p8-lane"], text="p8-lane: rebase onto the new seed", at=actions.stamp(shell.clock - timedelta(minutes=5 if why == "fresh order" else 30)))
    if why == "fresh spawn":
        shell.seen_as["p8-lane"] = ("root", actions.stamp(shell.clock - timedelta(minutes=40)))
        shell.announced["p8-lane"] = actions.stamp(shell.clock - timedelta(minutes=4))
    if why == "live dispatch":
        shell.launch("p8-lane", "ctx_p8")
    orca_pass(shell, config)
    assert not [line for line in escalations(shell) if line.startswith("LANE-GONE")]


@pytest.mark.parametrize(
    ("kind", "text", "gone"),
    [
        ("decide", "Owners for the reds: team 10 -> drive-watchdog; #29020 stays with p8-lane, READY", False),
        ("note", "p8-lane: FYI the seed moved", False),
        ("ask", "which seed did #29020 run on?", True),
        ("correction", "p8-lane: rebase onto the new seed", True),
    ],
    ids=["decide-listing-the-lane", "note", "ask", "correction-ordering-the-lane"],
)
def test_a_lane_with_no_session_is_gone_only_when_a_root_record_it_owes_goes_unanswered(shell, config, tmp_path, kind, text, gone):
    cci_record(shell, lane="p8-lane", kind="head", text="HEAD #29020 cccc333333", at=actions.stamp(shell.clock - timedelta(minutes=40)))
    cci_record(shell, kind=kind, to=["p8-lane", "drive-watchdog"], text=text, at=actions.stamp(shell.clock - timedelta(minutes=30)))
    shell.gone.add("p8-lane")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert len([line for line in escalations(shell) if line.startswith("LANE-GONE lane-gone:p8-lane ")]) == gone


@pytest.mark.parametrize("lane", ["stall-watch", "prompt-watch"])
def test_a_root_order_to_a_runner_child_pass_logs_no_lane_gone(shell, config, tmp_path, lane):
    cci_record(shell, lane=lane, kind="urgent", to=["root"], text="STALE #29020 at cccc333, 130 min: held", at=actions.stamp(shell.clock - timedelta(minutes=40)))
    cci_record(shell, kind="decide", to=[lane], text=f"{lane}: close the stale alarms on #29020", at=actions.stamp(shell.clock - timedelta(minutes=30)))
    shell.gone.add(lane)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert not [line for line in escalations(shell) if line.startswith("LANE-GONE")]


def test_a_lane_cci_lists_under_another_lane_by_its_agent_name_is_not_gone(shell, config, tmp_path):
    cci_record(shell, lane="p8-lane", kind="head", text="HEAD #29020 cccc333333", at=actions.stamp(shell.clock - timedelta(minutes=40)))
    cci_record(shell, kind="decide", to=["p8-lane"], text="p8-lane: rebase onto the new seed", at=actions.stamp(shell.clock - timedelta(minutes=30)))
    shell.seen_as["p8-lane"] = ("root", actions.stamp(shell.clock - timedelta(minutes=2)))
    orca_pass(shell, config)
    assert not [line for line in escalations(shell) if line.startswith("LANE-GONE")]


@pytest.mark.parametrize(("lane", "kind"), [("p8-lane", "done"), ("p8-lane", "handoff"), ("orca-desk", "head"), ("finder-desk", "review"), ("dashboard-dev", "defect")])
def test_a_lane_that_ended_its_work_a_desk_pass_or_a_script_posting_only_defects_never_reads_as_gone(shell, config, tmp_path, lane, kind):
    cci_record(shell, lane=lane, kind=kind, text="delivered", at=actions.stamp(shell.clock - timedelta(minutes=40)))
    cci_record(shell, kind="decide", to=[lane], text=f"{lane}: one more edit", at=actions.stamp(shell.clock - timedelta(minutes=30)))
    shell.gone.add(lane)
    orca_pass(shell, config)
    assert not [line for line in escalations(shell) if line.startswith("LANE-GONE")]


@pytest.mark.parametrize(("name", "lane", "expected"), [("p8-lane-2", "p8-lane", True), ("p8-lane-3", "p8-lane-2", True), ("p8-lane-2", "p8-lane-2", False), ("p8-lane-2", "p8-lane-3", False), ("p8-lane-x", "p8-lane", False), ("p8-lanes-2", "p8-lane", False)])
def test_succeeds_reads_a_numbered_successor_only(name, lane, expected):
    assert runner_module.succeeds(name, lane) is expected


def test_a_withdraw_line_stops_a_held_launch_before_it_starts(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    shell.cpu_load = 140
    orca_pass(shell, config)
    desk_orders(shell, f"R20 orca-desk: launch {LANE} sol xhigh brief={brief}")
    orca_pass(shell, config)
    desk_orders(shell, "R21 (11:52 PM) orca-desk: withdraw R20")
    orca_pass(shell, config)
    shell.cpu_load = 1.0
    orca_pass(shell, config)
    assert launches(shell) == []
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R20"].status == "failed"
    assert [line for line in escalations(shell) if not line.startswith("LOAD-HIGH ")] == [f"WITHDRAWN R20 {LANE}: by R21; nothing was launched"]


def stops(shell: FakeShell) -> list[list[str]]:
    return [call[2:5] for call in shell.calls if call[:2] == ["orca", "orchestration"] and call[2] in ("worker-stop", "worker-abandon")]


def withdrawals(shell: FakeShell) -> list[str]:
    return [line for line in escalations(shell) if line.startswith("WITHDRAW")]


def test_a_withdraw_of_a_launched_key_stops_its_dispatch_and_an_unknown_target_fails_visibly(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    shell.launch_line = f"{LANE} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    orca_pass(shell, config)
    desk_orders(shell, f"R20 orca-desk: launch {LANE} NOW incident xhigh brief={brief}")
    orca_pass(shell, config)
    desk_orders(shell, "R21 orca-desk: withdraw R20", "R22 orca-desk: withdraw R19", "R23 orca-desk: withdraw the duplicates", "R24 orca-desk: withdraw nobody-fix")
    orca_pass(shell, config)
    assert len(launches(shell)) == 1 and stops(shell) == [["worker-stop", "--dispatch", "ctx_n"]]
    assert withdrawals(shell) == [
        f"WITHDRAWN R20 {LANE}: by R21; dispatch ctx_n stopped (stopped), and no report of it launches a case lane",
        "WITHDRAW-FAILED R22 R19: the runner holds no launch or lane under R19; nothing was withdrawn",
        f"WITHDRAW-FAILED R23 inbox: one withdrawal per line, in the form `{runner_module.WITHDRAW_GRAMMAR}`; nothing was withdrawn",
        "WITHDRAW-FAILED R24 nobody-fix: the runner holds no launch or lane under nobody-fix; nothing was withdrawn",
    ]


WORKING = ["✳ Cogitating… (6m 2s · esc to interrupt)", "─" * 80, "❯", "─" * 80, "  ⏵⏵ bypass permissions on (shift+tab to cycle)"]


@pytest.mark.parametrize(
    ("answer", "shows"),
    [
        ("stop_unknown", "dispatch ctx_n still dispatched on terminal term_ctx_n"),
        ("stopped", "dispatch ctx_n failed, but terminal term_ctx_n still at work"),
    ],
)
def test_a_withdraw_orca_cannot_confirm_stopped_is_unconfirmed_not_withdrawn(shell, config, tmp_path, answer, shows):
    brief = launch_brief(tmp_path)
    shell.launch_line = f"{LANE} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    orca_pass(shell, config)
    desk_orders(shell, f"R20 orca-desk: launch {LANE} NOW incident xhigh brief={brief}")
    orca_pass(shell, config)
    if answer == "stop_unknown":
        shell.stop_answers["ctx_n"] = (answer, None)
    shell.screens["term_ctx_n"] = WORKING
    started = shell.clock
    desk_orders(shell, "R21 orca-desk: withdraw R20")
    orca_pass(shell, config)
    assert stops(shell) == [["worker-stop", "--dispatch", "ctx_n"]]
    assert withdrawals(shell) == [
        f"WITHDRAW-UNCONFIRMED R21 {LANE}: worker-stop of dispatch ctx_n answered {answer}, but after 30s Orca shows {shows}; it is marked withdrawn, so no report of it launches a case lane; stop it with orca-gc --done"
    ]
    assert shell.clock - started >= runner_module.STOP_CONFIRM
    assert not [call for call in shell.calls if call[:3] == ["orca", "terminal", "close"]]
    assert incident(tmp_path, f"desk-lane-{LANE}").facts["withdrawn"] == {"dispatch": "ctx_n", "by": "R21"}


def test_a_withdraw_whose_stop_orca_settles_within_the_wait_is_withdrawn(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    shell.launch_line = f"{LANE} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    orca_pass(shell, config)
    desk_orders(shell, f"R20 orca-desk: launch {LANE} NOW incident xhigh brief={brief}")
    orca_pass(shell, config)
    shell.stop_answers["ctx_n"] = ("stop_unknown", 4)
    desk_orders(shell, "R21 orca-desk: withdraw R20")
    orca_pass(shell, config)
    assert withdrawals(shell) == [f"WITHDRAWN R20 {LANE}: by R21; dispatch ctx_n stopped (stop_unknown), and no report of it launches a case lane"]


def test_a_root_cci_withdraw_of_a_launch_record_withdraws_it_and_is_no_directive(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    shell.cpu_load = 140
    orca_pass(shell, config)
    launch = cci_record(shell, to=["orca-desk"], text=f"R30 (11:49 PM) orca-desk: launch {LANE} sol xhigh brief={brief}")
    orca_pass(shell, config)
    cci_record(shell, kind="withdraw", to=["orca-desk"], re=launch, text="withdraw R30; relaunching under R32")
    cci_record(shell, to=["orca-desk"], text=f"R32 (11:51 PM) orca-desk: launch {LANE} NOW incident xhigh brief={brief}")
    shell.cpu_load = 1.0
    orca_pass(shell, config)
    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, "incident", "xhigh", str(brief)]]
    assert [line for line in escalations(shell) if not line.startswith("LOAD-HIGH ")][0] == f"WITHDRAWN R30 {LANE}: by cci#34767; nothing was launched"


def test_a_root_reply_to_a_decide_answers_that_worker_question(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.questions = [{"id": "msg_q9", "from_handle": "dispatch:ctx_a", "created_at": "2026-10-01T17:50:00Z"}]
    orca_pass(shell, config)
    decide = cci_record(shell, lane="desk-runner", kind="decide", to=["root"], topic="msg_q9", text=f"DECIDE msg_q9 {LANE}: Question: 300s or 30s? | options")
    cci_record(shell, kind="answer", to=[LANE], re=decide, text="30s NLB delay, 35s preStop.")
    orca_pass(shell, config)
    orca_pass(shell, config)
    [reply] = replies(shell)
    assert reply[reply.index("--id") + 1] == "msg_q9"
    assert reply[reply.index("--body") + 1].startswith("30s NLB delay, 35s preStop.\n")
    assert escalations(shell) == [f"RELAYED cci#34767 {LANE}: reply to question msg_q9 of dispatch ctx_a"]


def test_a_peer_drives_root_reply_to_a_decide_relays_nothing(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.questions = [{"id": "msg_q9", "from_handle": "dispatch:ctx_a", "created_at": "2026-10-01T17:50:00Z"}]
    orca_pass(shell, config)
    decide = cci_record(shell, lane="desk-runner", kind="decide", to=["root"], topic="msg_q9", text=f"DECIDE msg_q9 {LANE}: Question: 300s or 30s? | options")
    cci_record(shell, drive="other-drive", kind="answer", to=["root"], re=decide, text="take option 1")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert replies(shell) == [] and shell.sends() == []
    assert escalations(shell) == []


def test_a_root_reply_to_a_decide_whose_question_an_inbox_relay_answered_is_a_plain_relay(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.questions = [{"id": "msg_q9", "from_handle": "dispatch:ctx_a", "created_at": "2026-10-01T17:50:00Z"}]
    orca_pass(shell, config)
    decide = cci_record(shell, lane="desk-runner", kind="decide", to=["root"], topic="msg_q9", text=f"DECIDE msg_q9 {LANE}: Question: 300s or 30s? | options")
    desk_orders(shell, f"R390 orca-desk: relay to {LANE}: 30s NLB delay")
    orca_pass(shell, config)
    answer = cci_record(shell, kind="answer", to=[LANE], re=decide, text="30s NLB delay, 35s preStop.")
    orca_pass(shell, config)
    orca_pass(shell, config)
    [reply] = replies(shell)
    assert reply[reply.index("--id") + 1] == "msg_q9"
    assert reply[reply.index("--body") + 1].startswith("30s NLB delay\n")
    [send] = shell.sends()
    assert send[send.index("--body") + 1].startswith("30s NLB delay, 35s preStop.\n")
    assert escalations(shell) == [f"RELAYED R390 {LANE}: reply to question msg_q9 of dispatch ctx_a", f"RELAYED cci#{answer} {LANE}: relay to dispatch ctx_a"]


def test_a_root_reply_to_a_lanes_cci_ask_closes_its_open_orca_question(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.questions = [{"id": "msg_q4", "from_handle": "dispatch:ctx_a", "created_at": "2026-10-01T17:50:00Z"}]
    orca_pass(shell, config)
    ask = cci_record(shell, lane=LANE, kind="ask", to=["root"], text="Need Opus PR prose")
    cci_record(shell, kind="answer", to=[LANE], re=ask, text="Prose ready at /tmp/pr-body.md")
    cci_record(shell, kind="answer", to=["other-lane"], text="no re, not a reply")
    orca_pass(shell, config)
    [reply] = replies(shell)
    assert reply[reply.index("--id") + 1] == "msg_q4"
    assert reply[reply.index("--body") + 1].startswith("The root answered on cci #34767 (re #34766), which supersedes this question: Prose ready at /tmp/pr-body.md\n")


def test_two_root_replies_to_a_lane_with_one_open_question_answer_it_once_and_relay_the_other(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.questions = [{"id": "msg_q4", "from_handle": "dispatch:ctx_a", "created_at": "2026-10-01T17:50:00Z"}]
    orca_pass(shell, config)
    override = cci_record(shell, lane=LANE, kind="ask", to=["root"], text="Override #37923?")
    image = cci_record(shell, lane=LANE, kind="ask", to=["root"], text="busybox: A or B?")
    cci_record(shell, kind="answer", to=[LANE], re=override, text="Override recorded")
    cci_record(shell, kind="answer", to=[LANE], re=image, text="Option A")
    orca_pass(shell, config)
    [reply] = replies(shell)
    assert reply[reply.index("--id") + 1] == "msg_q4"
    assert reply[reply.index("--body") + 1].startswith("The root answered on cci #34768 (re #34766), which supersedes this question: Override recorded\n")
    [send] = shell.sends()
    assert send[send.index("--body") + 1].startswith("The root answered on cci #34769 (re #34767), which supersedes this question: Option A\n")
    assert escalations(shell) == [f"RELAYED cci#34768 {LANE}: reply to question msg_q4 of dispatch ctx_a", f"RELAYED cci#34769 {LANE}: relay to dispatch ctx_a"]


def test_a_root_reply_to_a_lane_with_no_open_question_relays_nothing(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    ask = cci_record(shell, lane=LANE, kind="ask", to=["root"], text="Need Opus PR prose")
    cci_record(shell, kind="answer", to=[LANE], re=ask, text="Prose ready")
    orca_pass(shell, config)
    assert replies(shell) == [] and shell.sends() == [] and escalations(shell) == []


def clipped_text(text: str) -> str:
    return (text + "x" * runner_module.CCI_TEXT)[: runner_module.CCI_TEXT - 1] + "…"


def spilled(tmp_path: Path, text: str) -> dict:
    blob = tmp_path / "blobs" / f"{hashlib.sha256(text.encode()).hexdigest()[:16]}.txt"
    blob.parent.mkdir(exist_ok=True)
    blob.write_text(f"{text}\n")
    first = next(line.strip() for line in text.split("\n") if line.strip())
    return {"text": first if len(first) <= runner_module.CCI_TEXT else first[: runner_module.CCI_TEXT - 1] + "…", "refs": {"path": str(blob)}}


def test_a_relay_record_over_the_text_limit_is_relayed_whole_from_the_file_cci_wrote(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    record = spilled(tmp_path, f"R20 (8:20 AM) orca-desk: relay to {LANE}: {RULING}")
    assert len(record["text"]) == runner_module.CCI_TEXT and record["text"].endswith("…")
    cci_record(shell, to=["orca-desk"], **record)
    orca_pass(shell, config)
    orca_pass(shell, config)
    [send] = shell.sends()
    assert send[send.index("--body") + 1].startswith(f"{RULING}\n\nReply on thread ")
    assert escalations(shell) == [f"RELAYED R20 {LANE}: relay to dispatch ctx_a"]


def test_a_relay_record_cci_clipped_is_relayed_from_its_file_whatever_the_files_first_line_reads(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    record = spilled(tmp_path, f"R20 (8:20 AM) orca-desk: relay to {LANE}: {RULING}")
    cci_record(shell, to=["orca-desk"], text=record["text"].replace("8:20 AM", "8:21 AM"), refs=record["refs"])
    orca_pass(shell, config)
    orca_pass(shell, config)
    [send] = shell.sends()
    assert send[send.index("--body") + 1].startswith(f"{RULING}\n\nReply on thread ")


@pytest.mark.parametrize("over_the_limit", [True, False])
def test_a_relay_record_spanning_lines_is_relayed_with_every_line_after_its_directive(shell, config, tmp_path, over_the_limit):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    text = PARAGRAPHS if over_the_limit else PARAGRAPHS.replace("rebuild each failed head once, " * 15, "rebuild each failed head once, ")
    whole = f"R21 orca-desk: relay to {LANE}:\n{text}"
    record = spilled(tmp_path, whole) if over_the_limit else {"text": whole}
    assert (len(whole) > runner_module.CCI_TEXT) == over_the_limit and "…" not in record["text"]
    cci_record(shell, to=["orca-desk"], **record)
    orca_pass(shell, config)
    orca_pass(shell, config)
    [send] = shell.sends()
    assert send[send.index("--body") + 1].startswith(f"{text}\n\nReply on thread ")
    assert escalations(shell) == [f"RELAYED R21 {LANE}: relay to dispatch ctx_a"]


def test_a_relay_record_naming_a_file_that_is_not_its_text_relays_the_records_own_text(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    evidence = tmp_path / "evidence.md"
    evidence.write_text("# Evidence\n\nthe canary read back red at 14:02\n")
    cci_record(shell, to=["orca-desk"], text=f"R22 orca-desk: relay to {LANE}: read the evidence at {evidence}", refs={"path": str(evidence)})
    orca_pass(shell, config)
    orca_pass(shell, config)
    [send] = shell.sends()
    assert send[send.index("--body") + 1].startswith(f"read the evidence at {evidence}\n\nReply on thread ")


def test_a_relay_record_whose_file_cannot_be_read_here_is_refused_naming_the_path(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    cci_record(shell, to=["orca-desk"], text=clipped_text(f"R20 (8:20 AM) orca-desk: relay to {LANE}: Requirement: "), refs={"path": "/blobs/f727f5487a4aee3f.txt"})
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert shell.sends() == []
    assert escalations(shell) == [
        "RELAY-FAILED R20 inbox: the record's full text at /blobs/f727f5487a4aee3f.txt is not readable here; nothing was relayed; post it again on the machine this runner runs on"
    ]
    assert [(post["kind"], post["topic"]) for post in shell.posts] == [("defect", "R20")]


def test_a_relay_record_exactly_at_the_text_limit_is_relayed_whole(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    text = clipped_text(f"R20 orca-desk: relay to {LANE}: Requirement: ")
    cci_record(shell, to=["orca-desk"], text=text, refs={})
    orca_pass(shell, config)
    orca_pass(shell, config)
    [send] = shell.sends()
    assert send[send.index("--body") + 1].startswith(text.split(": ", 2)[2] + "\n")


def test_a_root_reply_to_a_decide_over_the_text_limit_answers_with_its_whole_text(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.questions = [{"id": "msg_q9", "from_handle": "dispatch:ctx_a", "created_at": "2026-10-01T17:50:00Z"}]
    orca_pass(shell, config)
    decide = cci_record(shell, lane="desk-runner", kind="decide", to=["root"], topic="msg_q9", text=f"DECIDE msg_q9 {LANE}: Question: 300s or 30s? | options")
    answer = "30s NLB delay, because " + "the drain must outlast the preStop hook. " * 12 + "\n\nThen set preStop to 35s."
    record = spilled(tmp_path, answer)
    assert len(record["text"]) == runner_module.CCI_TEXT and record["text"].endswith("…")
    cci_record(shell, kind="answer", to=[LANE], re=decide, **record)
    orca_pass(shell, config)
    orca_pass(shell, config)
    [reply] = replies(shell)
    assert reply[reply.index("--id") + 1] == "msg_q9"
    assert reply[reply.index("--body") + 1] == f"{answer}\n\n(cci#34767)"
    assert escalations(shell) == [f"RELAYED cci#34767 {LANE}: reply to question msg_q9 of dispatch ctx_a"]


def test_a_root_reply_to_a_lanes_cci_ask_over_the_text_limit_closes_its_question_with_the_whole_text(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.questions = [{"id": "msg_q4", "from_handle": "dispatch:ctx_a", "created_at": "2026-10-01T17:50:00Z"}]
    orca_pass(shell, config)
    ask = cci_record(shell, lane=LANE, kind="ask", to=["root"], text="Which heads do I rebuild?")
    cci_record(shell, kind="answer", to=[LANE], re=ask, **spilled(tmp_path, PARAGRAPHS))
    orca_pass(shell, config)
    orca_pass(shell, config)
    [reply] = replies(shell)
    assert reply[reply.index("--id") + 1] == "msg_q4"
    assert reply[reply.index("--body") + 1] == f"The root answered on cci #34767 (re #34766), which supersedes this question: {PARAGRAPHS}\n\n(cci#34767)"


def test_a_root_reply_whose_file_cannot_be_read_here_is_refused_and_answers_nothing(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.questions = [{"id": "msg_q9", "from_handle": "dispatch:ctx_a", "created_at": "2026-10-01T17:50:00Z"}]
    orca_pass(shell, config)
    decide = cci_record(shell, lane="desk-runner", kind="decide", to=["root"], topic="msg_q9", text=f"DECIDE msg_q9 {LANE}: Question: 300s or 30s? | options")
    cci_record(shell, kind="answer", to=[LANE], re=decide, text=clipped_text("30s NLB delay, because "), refs={"path": "/blobs/a16e1fea5c0bf21e.txt"})
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert replies(shell) == []
    assert escalations(shell) == [
        f"RELAY-FAILED cci#34767 {LANE}: the record's full text at /blobs/a16e1fea5c0bf21e.txt is not readable here; nothing was relayed; post it again on the machine this runner runs on"
    ]


def test_a_record_to_the_orca_desk_outside_the_grammar_fails_visibly(shell, config, tmp_path):
    orca_pass(shell, config)
    cci_go(shell, "launch incident-timeout-fix on the incident route now")
    orca_pass(shell, config)
    orca_pass(shell, config)
    [line] = escalations(shell)
    assert line.startswith("DIRECTIVE-FAILED cci#34766 inbox: a record addressed to orca-desk is one `orca-desk: ")
    assert [(post["kind"], post["topic"]) for post in shell.posts] == [("defect", "cci#34766")]


def test_a_failed_cci_read_escalates_and_rereads_from_the_same_sequence(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    cci_go(shell, f"R5 orca-desk: relay to {LANE}: rebase onto dev")
    shell.tail_error = "cci: daemon unavailable"
    orca_pass(shell, config)
    shell.tail_error = ""
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert len(shell.sends()) == 1
    assert [line.split(":", 1)[0] for line in escalations(shell)] == ["CCI-TAIL-FAILED cci-tail", f"RELAYED R5 {LANE}"]


def test_the_alert_grammar_names_the_slug_link_and_what_fired():
    directive = runner_module.INBOX_DIRECTIVE.match("- orca-desk: alert dd-312516332 https://app.datadoghq.com/monitors/312516332 :: Datadog OK -> Alert Run assignment starved")
    spec = runner_module.ALERT_SPEC.match(directive["rest"])
    assert (directive["verb"], spec["slug"], spec["link"], spec["what"]) == (
        "alert",
        "dd-312516332",
        "https://app.datadoghq.com/monitors/312516332",
        "Datadog OK -> Alert Run assignment starved",
    )


def test_a_monitor_alert_line_launches_nothing_and_records_the_transition(shell, config, tmp_path):
    orca_pass(shell, config)
    desk_orders(shell, "- orca-desk: alert dd-312516332 https://app.datadoghq.com/monitors/312516332 :: Datadog OK -> Alert Run assignment starved")
    orca_pass(shell, config)
    assert launches(shell) == []
    assert shell.entries == []
    [line] = escalations(shell)
    assert line.startswith("ALERT dd-312516332 ") and "no lane launched; the alerts desk writes `orca-desk: incident" in line


def test_a_briefs_log_that_stops_resolving_mid_drive_repoints_to_the_drives_briefs_log_and_the_incident_still_launches(shell, config, tmp_path):
    orca_pass(shell, config)
    del shell.logs["briefs1"]
    shell.logs["b2"] = "briefs: d1"
    shell.launch_line = "dd-7-fix ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    desk_orders(shell, "- orca-desk: incident dd-7 https://app.datadoghq.com/monitors/7 reported=11:10 :: Datadog OK -> Alert api 5xx")
    orca_pass(shell, config)
    shell.launch("dd-7-fix", "ctx_n")
    orca_pass(shell, config)
    brief = written(tmp_path, "dd-7-fix")
    assert launches(shell) == [[str(runner_module.LAUNCHER), "dd-7-fix", "incident", "xhigh", str(brief)]]
    assert f"ccn -R {tmp_path / 'checkout'} attachment path b2 dd-7-evidence.md" in brief.read_text()
    lines = escalations(shell)
    assert lines[0].startswith("BRIEFS-LOG briefs-log:briefs1 runner: briefs log briefs1 does not resolve") and "briefs now attach to b2 (briefs: d1)" in lines[0]
    assert lines[1].startswith("INCIDENT dd-7 ") and "fix lane dd-7-fix launching on incident xhigh" in lines[1]


def test_a_drive_with_no_briefs_log_creates_its_briefs_log_at_start(shell, config):
    raw = json.loads(config.read_text())
    raw["orca"]["briefs"] = {"repo": raw["orca"]["briefs"]["repo"]}
    config.write_text(json.dumps(raw))
    shell.logs.clear()
    orca_pass(shell, config)
    assert shell.logs == {"log1": "briefs: d1"}
    assert [line for line in escalations(shell) if line.startswith("BRIEFS-LOG ")] == [f"BRIEFS-LOG briefs-log:unset runner: briefs log (unset) does not resolve on {raw['orca']['briefs']['repo']}; briefs now attach to log1 (briefs: d1), so set orca.briefs.log to it"]


def test_an_incident_line_attaches_the_brief_to_the_briefs_log_and_launches_the_incident_fix_lane_once(shell, config, tmp_path):
    facts = tmp_path / "alert-facts.md"
    facts.write_text("apply authority: 0 deletes and 0 replaces while the alert is active\n")
    raw = json.loads(config.read_text())
    config.write_text(json.dumps({**raw, "alert": {"facts": str(facts)}}))
    shell.launch_line = "dd-312516332-fix ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    orca_pass(shell, config)
    seq = desk_orders(shell, "- orca-desk: incident dd-312516332 https://app.datadoghq.com/monitors/312516332 reported=23:48 :: Datadog OK -> Alert Run assignment starved")
    orca_pass(shell, config)
    shell.launch("dd-312516332-fix", "ctx_n")
    orca_pass(shell, config)
    brief = written(tmp_path, "dd-312516332-fix")
    assert brief.parent == tmp_path / "briefs" and brief.read_text() == shell.attachments["dd-312516332-fix.full.md"].read_text()
    assert launches(shell) == [[str(runner_module.LAUNCHER), "dd-312516332-fix", "incident", "xhigh", str(brief)]]
    assert shell.entries == ["INCIDENT dd-312516332: fix brief for dd-312516332-fix: Datadog OK -> Alert Run assignment starved https://app.datadoghq.com/monitors/312516332"]
    text = brief.read_text()
    assert "Datadog OK -> Alert Run assignment starved" in text and "apply authority: 0 deletes and 0 replaces" in text
    assert "  0. NO ROLLBACK; find the mechanism and fix forward;" in text and "Rollback first" not in text and "{" not in text
    assert "  - Reported 23:48 PT. The bar is fixed and live within 20 minutes of the\n    report, by 00:08 PT;" in text
    assert f"ccn -R {tmp_path / 'checkout'} attachment path briefs1 dd-312516332-evidence.md" in text
    assert not (tmp_path / "incidents").exists()
    lines = [line for line in escalations(shell)]
    assert lines[0].startswith("INCIDENT dd-312516332 ") and "fix lane dd-312516332-fix launching on incident xhigh" in lines[0]
    assert lines[1] == f"LAUNCHED inbox@cci#{seq} dd-312516332-fix: dispatch ctx_n terminal term_ctx_n"


ROOT_ALERT = "orca-desk: incident alerts-api-0305 https://in-the-forge.slack.com/archives/C0822AHFY3G/p1791108227539019 reported=03:03 :: SandDB admission turning bulk queries away on 0ddq7rb (monitor 327967001, Warn 3:03 AM PT)"


def root_brief(tmp_path: Path, monitor: str) -> Path:
    brief = tmp_path / "incidents/alerts-api-0303/fix-brief.md"
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text(f"# alerts-api-0303-fix\n\nccx: incident={monitor} role=fix\n")
    return brief


@pytest.mark.parametrize(("monitor", "skipped"), [("327967001", True), ("328006761", False)])
def test_an_alert_defers_to_a_root_launch_line_for_the_same_monitor(shell, config, tmp_path, monitor, skipped):
    brief = root_brief(tmp_path, monitor)
    orca_pass(shell, config)
    desk_orders(shell, ROOT_ALERT, f"R1068 (3:05 AM PT) orca-desk: launch alerts-api-0303-fix NOW incident xhigh brief={brief}")
    alert = shell.records[-2]["seq"]
    orca_pass(shell, config)
    launched = [call[1] for call in launches(shell)]
    assert ("alerts-api-0305-fix" not in launched) is skipped and "alerts-api-0303-fix" in launched
    assert any(f"LAUNCH-SKIPPED inbox@cci#{alert} alerts-api-0305-fix: duplicate of alerts-api-0303-fix" in line for line in escalations(shell)) is skipped


def test_an_alert_defers_to_a_live_lane_on_the_same_monitor(shell, config, tmp_path):
    brief = root_brief(tmp_path, "327967001")
    orca_pass(shell, config)
    desk_orders(shell, f"R1068 orca-desk: launch alerts-api-0303-fix NOW incident xhigh brief={brief}")
    orca_pass(shell, config)
    shell.launch("alerts-api-0303-fix", "ctx_root")
    orca_pass(shell, config)
    shell.sleep(3600)
    desk_orders(shell, ROOT_ALERT)
    orca_pass(shell, config)
    assert [call[1] for call in launches(shell)] == ["alerts-api-0303-fix"]
    assert any("LAUNCH-SKIPPED" in line and "duplicate of alerts-api-0303-fix" in line for line in escalations(shell))


@pytest.mark.parametrize("reused", [False, True], ids=["finished", "reused-for-another-monitor"])
def test_an_alert_launches_when_the_lane_on_its_monitor_is_no_longer_on_it(shell, config, tmp_path, reused):
    brief = root_brief(tmp_path, "327967001")
    orca_pass(shell, config)
    desk_orders(shell, f"R1068 orca-desk: launch alerts-api-0303-fix NOW incident xhigh brief={brief}")
    orca_pass(shell, config)
    shell.launch("alerts-api-0303-fix", "ctx_root")
    orca_pass(shell, config)
    shell.dispatches["ctx_root"]["status"] = "completed"
    if reused:
        other = tmp_path / "incidents/alerts-api-0400/fix-brief.md"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text("# alerts-api-0400-fix\n\nccx: incident=328006761 role=fix\n")
        desk_orders(shell, f"R1070 orca-desk: launch alerts-api-0303-fix NOW incident xhigh brief={other}")
        orca_pass(shell, config)
        shell.launch("alerts-api-0303-fix", "ctx_reused")
        orca_pass(shell, config)
    desk_orders(shell, ROOT_ALERT)
    orca_pass(shell, config)
    assert "alerts-api-0305-fix" in [call[1] for call in launches(shell)]
    assert not any("LAUNCH-SKIPPED" in line for line in escalations(shell))


def test_an_alert_brief_names_its_monitor_for_later_dedupe(shell, config, tmp_path):
    orca_pass(shell, config)
    desk_orders(shell, ROOT_ALERT)
    orca_pass(shell, config)
    assert "ccx: lane=alerts-api-0305-fix role=incident effort=xhigh incident=327967001\n" in written(tmp_path, "alerts-api-0305-fix").read_text()


def test_a_repeat_alert_relays_to_the_live_fix_lane_and_keeps_its_brief(shell, config, tmp_path):
    brief = tmp_path / "lfs/root-edited"
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text("root-edited brief")
    shell.attachments["alerts-runs-1704-fix.full.md"] = brief
    shell.launch("alerts-runs-1704-fix", "ctx_a")
    orca_pass(shell, config)
    seq = desk_orders(shell, "orca-desk: alert alerts-runs-1704 https://in-the-forge.slack.com/archives/C098XDNJJR3/p1791072242613549 :: Page #11507 SoFi Disputes stuck-case monitor stopped reporting")
    orca_pass(shell, config)
    assert launches(shell) == []
    [send] = shell.sends()
    assert "The alert fired again at" in send[send.index("--body") + 1]
    assert brief.read_text() == "root-edited brief" and shell.attachments["alerts-runs-1704-fix.full.md"] == brief and shell.entries == []
    [line] = escalations(shell)
    assert line == f"RELAYED inbox@cci#{seq}:again alerts-runs-1704-fix: relay to dispatch ctx_a"


def test_an_alert_outside_the_grammar_fails_visibly_and_launches_nothing(shell, config, tmp_path):
    orca_pass(shell, config)
    desk_orders(shell, "R12 orca-desk: alert Run assignment starved, please look")
    orca_pass(shell, config)
    assert launches(shell) == []
    [line] = escalations(shell)
    assert line.startswith("ALERT-FAILED R12 inbox: one alert per line") and runner_module.ALERT_GRAMMAR in line


def test_an_incident_after_the_last_fix_lane_finished_launches_on_a_fresh_brief(shell, config, tmp_path):
    last = tmp_path / "lfs/last-episode"
    last.parent.mkdir(parents=True, exist_ok=True)
    last.write_text("last episode")
    shell.attachments["dd-7-fix.full.md"] = last
    shell.launch("dd-7-fix", "ctx_old", status="completed")
    orca_pass(shell, config)
    desk_orders(shell, "R40 orca-desk: incident dd-7 https://app.datadoghq.com/monitors/7 reported=11:02 :: Datadog OK -> Alert again")
    orca_pass(shell, config)
    brief = written(tmp_path, "dd-7-fix")
    assert [call[1:] for call in launches(shell)] == [["dd-7-fix", "incident", "xhigh", str(brief)]]
    assert brief != last and "Datadog OK -> Alert again" in brief.read_text()


def test_an_incident_whose_brief_fails_to_attach_launches_nothing(shell, config, tmp_path):
    shell.attach_error = "error: cc-notes: ref lock held"
    orca_pass(shell, config)
    desk_orders(shell, "R41 orca-desk: incident dd-8 https://app.datadoghq.com/monitors/8 reported=11:02 :: Datadog OK -> Alert")
    orca_pass(shell, config)
    assert launches(shell) == [] and "dd-8-fix.full.md" not in shell.attachments
    [line] = escalations(shell)
    assert line.startswith("INCIDENT dd-8 ") and line.endswith("| dd-8-fix not launched: the brief did not attach: error: cc-notes: ref lock held")


def test_an_incident_whose_attach_dies_silently_launches_nothing(shell, config, tmp_path):
    shell.attach_code = -9
    orca_pass(shell, config)
    desk_orders(shell, "R42 orca-desk: incident dd-9 https://app.datadoghq.com/monitors/9 reported=11:02 :: Datadog OK -> Alert")
    orca_pass(shell, config)
    assert launches(shell) == []
    [line] = escalations(shell)
    assert line.endswith("| dd-9-fix not launched: the brief did not attach: ccn exited -9")


def written(tmp_path: Path, lane: str) -> Path:
    return tmp_path / "briefs" / f"{lane}.full.md"


def clocked_lane(shell: FakeShell, config: Path, tmp_path: Path, lane: str = LANE, model: str = "incident", header: str = "") -> Path:
    brief = tmp_path / f"{lane}.brief.md"
    brief.write_text(f"{header}Fix the alert on api.\n")
    orca_pass(shell, config)
    cli(shell, config, "launch", "--key", f"launch-{lane}", "--lane", lane, "--model", model, "--effort", "xhigh", "--brief", str(brief))
    orca_pass(shell, config)
    shell.launch(lane, f"ctx_{lane}")
    orca_pass(shell, config)
    return brief


def lane_record(shell: FakeShell, lane: str, kind: str, text: str = "", at: str = "", **refs) -> None:
    cci_record(shell, lane=lane, kind=kind, text=text or f"{kind.upper()} {lane}", at=at or actions.stamp(shell.clock), refs=refs)


def clock_lines(shell: FakeShell, lane: str = LANE) -> list[str]:
    return [line for line in escalations(shell) if line.startswith(f"INCIDENT-CLOCK {lane} ")]


def executor_backup(shell: FakeShell, status: str, fix: str = LANE) -> str:
    backup = "incident-api-1001-backup"
    record = actions.Incident("api-1001", "executor", 1, actions.stamp(shell.clock), {"adopted": {"fix": fix}})
    record.accept("dispatch:backup", "dispatch", backup, "R16", shell.clock)
    record.start("dispatch:backup", shell.clock)
    if status == "failed":
        record.fail("dispatch:backup", "orca-launch.sh failed worker-start")
    if status == "verified":
        receipt = {"lane": backup, "dispatch": "ctx_backup"}
        record.complete("dispatch:backup", receipt, dispatch_id="ctx_backup")
        record.verify("dispatch:backup", receipt)
    actions.Store().create(record)
    return backup


def test_a_silent_incident_lane_gets_one_opus_fallback_and_one_blocker_per_threshold(shell, config, tmp_path):
    brief = clocked_lane(shell, config, tmp_path)
    shell.sleep(9 * 60)
    orca_pass(shell, config)
    assert clock_lines(shell) == [] and len(launches(shell)) == 1
    shell.sleep(60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    backup = f"{LANE}-opus"
    fallback = written(tmp_path, f"{backup}")
    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, "incident", "xhigh", str(brief)], [str(runner_module.LAUNCHER), backup, "opus", "xhigh", str(fallback)]]
    assert shell.spawned_env["orca-launch.sh"]["ORCA_LAUNCH_CLAUDE_ARGS"] == runner_module.CLAUDE_FAST_MODE
    launch = json.loads(incident(tmp_path, f"desk-lane-{backup}").actions[f"incident-clock:ctx_{LANE}"].target)
    assert (launch["urgent"], launch["fast"]) == (True, True)
    text = fallback.read_text()
    assert text.startswith(f"ccx: lane={backup} role=incident effort=xhigh\n") and text.endswith(f"{brief.read_text().rstrip()}\n\nRegister slice: lane={backup} step=incident\n")
    assert f"You are the Opus fallback for `{LANE}`" in text and f"10 minutes after `{LANE}` launched" in text
    assert "had posted no mechanism" in text and "the first PR wins" in text
    assert f"Post on cci as `{backup}`, and address `{LANE}` and `root` on every record." in text
    assert shell.entries == [f"INCIDENT-CLOCK {LANE}: Opus fallback brief for {backup}, no mechanism at +10 min"]
    shell.launch(backup, "ctx_opus")
    orca_pass(shell, config)
    for _ in range(2):
        shell.sleep(5 * 60)
        orca_pass(shell, config)
        orca_pass(shell, config)
    assert clock_lines(shell) == [
        f"INCIDENT-CLOCK {LANE} mechanism: no mechanism 10 min after its launch at 11:00, limit 10 min; launch to now 10 min | the Opus fallback {backup} is launching on opus xhigh in fast mode, brief {fallback}",
        f"INCIDENT-CLOCK {LANE} pr: no PR 15 min after its launch at 11:00, limit 15 min; launch to now 15 min | the Opus fallback {backup} is already launched",
        f"INCIDENT-CLOCK {LANE} ready: not ready 20 min after its launch at 11:00, limit 20 min; missing mechanism, PR, ready; launch to now 20 min",
    ]
    assert [(post["kind"], post["topic"]) for post in shell.posts if post["text"].startswith(f"INCIDENT-CLOCK {LANE} ")] == [("blocker", LANE)] * 3
    assert len(launches(shell)) == 2 and len(shell.entries) == 1


@pytest.mark.parametrize("source", ["status", "cci", "base-name", "any-kind-text"])
def test_a_mechanism_from_the_run_or_from_cci_stops_the_mechanism_threshold(shell, config, tmp_path, capsys, source):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(4 * 60)
    if source == "status":
        shell.receive({"subject": "mechanism: wrong selector", "from_handle": f"term_ctx_{LANE}"})
    elif source == "base-name":
        lane_record(shell, LANE.removesuffix("-fix"), "incident", text="MECHANISM d121062c confirmed")
    elif source == "any-kind-text":
        lane_record(shell, LANE, "state", text="Found the mechanism: the release smoke reads a missing next.json")
    else:
        lane_record(shell, LANE, "mechanism")
    orca_pass(shell, config)
    shell.sleep(7 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert clock_lines(shell) == [] and len(launches(shell)) == 1
    capsys.readouterr()
    cli(shell, config, "show")
    assert "- incident clock [11 min of 20] launched 11:00; mechanism +4 min\n" in capsys.readouterr().out


@pytest.mark.parametrize("addressed", [True, False])
def test_a_root_decide_naming_the_lane_stops_its_clock(shell, config, tmp_path, addressed):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(4 * 60)
    if addressed:
        cci_record(shell, lane="root", kind="decide", to=[LANE], text="hold the forward edit until the bucket owner answers", at=actions.stamp(shell.clock))
    else:
        cci_record(shell, lane="root", kind="decide", to=["landing-desk"], text=f"{LANE} owns the release incident; no fallback", at=actions.stamp(shell.clock))
    orca_pass(shell, config)
    shell.sleep(16 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert clock_lines(shell) == [] and len(launches(shell)) == 1


@pytest.mark.parametrize(
    ("kind", "to", "text"),
    [("decision", [LANE, "landing-desk"], "Register classify, 1 rows"), ("decide", ["landing-desk"], f"{LANE}.v2 owns the release incident")],
)
def test_a_root_decision_broadcast_or_a_decide_naming_a_dotted_sibling_leaves_the_clock_running(shell, config, tmp_path, kind, to, text):
    clocked_lane(shell, config, tmp_path)
    cci_record(shell, lane="root", kind=kind, to=to, text=text, at=actions.stamp(shell.clock))
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert [line.split(":")[0] for line in clock_lines(shell)] == [f"INCIDENT-CLOCK {LANE} mechanism"]


def test_a_mechanism_with_no_pr_launches_the_fallback_at_the_pr_threshold(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(4 * 60)
    lane_record(shell, LANE, "mechanism")
    shell.sleep(11 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    fallback = written(tmp_path, f"{LANE}-opus")
    assert clock_lines(shell) == [
        f"INCIDENT-CLOCK {LANE} pr: no PR 15 min after its launch at 11:00, limit 15 min; launch to mechanism 4 min, mechanism to now 11 min | the Opus fallback {LANE}-opus is launching on opus xhigh in fast mode, brief {fallback}"
    ]
    assert [call[1] for call in launches(shell)] == [LANE, f"{LANE}-opus"] and "had posted no PR" in fallback.read_text()


def test_a_mechanism_a_case_sibling_posted_stops_the_mechanism_fallback_but_not_the_pr_threshold(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    clocked_lane(shell, config, tmp_path, lane=f"{LANE}-case1")
    shell.sleep(8 * 60)
    lane_record(shell, f"{LANE}-case1", "mechanism")
    shell.sleep(4 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert clock_lines(shell) == [] and f"{LANE}-opus" not in [call[1] for call in launches(shell)]
    shell.sleep(3 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    [line] = clock_lines(shell)
    assert line.startswith(f"INCIDENT-CLOCK {LANE} pr: no PR 15 min after its launch at 11:00, limit 15 min; launch to mechanism 8 min, mechanism to now 7 min")
    assert f"{LANE}-opus" in [call[1] for call in launches(shell)]


def test_a_mechanism_another_fix_lane_on_the_incident_posted_stops_the_mechanism_fallback(shell, config, tmp_path):
    for lane in ("inc-disk-fix", "inc-disk-cred", "inc-dusk-fix"):
        clocked_lane(shell, config, tmp_path, lane=lane)
    shell.sleep(6 * 60)
    lane_record(shell, "inc-disk-fix", "mechanism")
    shell.sleep(5 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert clock_lines(shell, "inc-disk-cred") == [] and clock_lines(shell, "inc-disk-fix") == []
    assert [line.split(":", 1)[0] for line in clock_lines(shell, "inc-dusk-fix")] == ["INCIDENT-CLOCK inc-dusk-fix mechanism"]
    assert [call[1] for call in launches(shell)][3:] == ["inc-dusk-fix-opus"]


def test_a_lane_named_for_evidence_on_the_incident_alias_is_never_clocked(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path, lane="inc-disk-evidence")
    shell.sleep(30 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert clock_lines(shell, "inc-disk-evidence") == [] and len(launches(shell)) == 1


def test_a_withdrawn_lane_clock_ends_as_withdrawn_and_posts_nothing(shell, config, tmp_path, capsys):
    clocked_lane(shell, config, tmp_path)
    desk_orders(shell, f"R30 orca-desk: withdraw {LANE}")
    orca_pass(shell, config)
    shell.sleep(21 * 60)
    orca_pass(shell, config)
    assert clock_lines(shell) == [] and len(launches(shell)) == 1
    assert incident(tmp_path, f"desk-lane-{LANE}").facts["incident-clock"]["ended"] == "withdrawn by R30"


def test_a_root_stand_down_ends_the_clock_as_withdrawn(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(60)
    cci_record(shell, kind="stand-down", to=[LANE], at=actions.stamp(shell.now()), text="stand down; the opus lane has it")
    orca_pass(shell, config)
    shell.sleep(20 * 60)
    orca_pass(shell, config)
    assert clock_lines(shell) == [] and incident(tmp_path, f"desk-lane-{LANE}").facts["incident-clock"]["ended"].startswith("withdrawn by #")


def test_escalations_outside_the_incident_wait_for_the_last_clock_and_reach_root_as_one_deferred_record(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    shell.launch("docs-lane", "ctx_docs")
    cli(shell, config, "relay", "--key", "R700", "--lane", "docs-lane", "--text", "rebase onto dev")
    orca_pass(shell, config)
    shell.sleep(11 * 60)
    lane_record(shell, LANE, "mechanism")
    lane_record(shell, LANE, "opened", prs=[4242])
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert [line for line in escalations(shell) if line.startswith(("DEADLINE", "DEFERRED"))] == []
    view = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store")).render()
    assert "deferred during api-fix: 1\n- DEADLINE deadline:desk-lane-docs-lane/R700:accepted docs-lane: " in view
    lane_record(shell, LANE, "ready")
    orca_pass(shell, config)
    orca_pass(shell, config)
    held = [line for line in escalations(shell) if line.startswith(("DEADLINE", "DEFERRED"))]
    assert len(held) == 1 and held[0].startswith("DEFERRED 1 escalations held during api-fix: DEADLINE deadline:desk-lane-docs-lane/R700:accepted docs-lane: R700 relay: delivered to ")


def test_an_escalation_already_held_for_the_incident_leaves_the_store_unwritten_on_later_passes(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    shell.launch("docs-lane", "ctx_docs")
    cli(shell, config, "relay", "--key", "R700", "--lane", "docs-lane", "--text", "rebase onto dev")
    orca_pass(shell, config)
    shell.sleep(11 * 60)
    lane_record(shell, LANE, "mechanism")
    lane_record(shell, LANE, "opened", prs=[4242])
    orca_pass(shell, config)
    orca_pass(shell, config)
    held = incident(tmp_path, runner_module.RUNNER).facts[runner_module.DEFERRED]
    assert held["ids"] == ["escalation:deadline:desk-lane-docs-lane/R700:accepted"]
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    before = store.path(runner_module.RUNNER).stat()
    assert runner.flush() and runner.flush()
    after = store.path(runner_module.RUNNER).stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert incident(tmp_path, runner_module.RUNNER).facts[runner_module.DEFERRED] == held


def go_to_walker(shell: FakeShell, config: Path, text: str = "cut sand now at 94d61b91951", refs: dict | None = None) -> int:
    orca_pass(shell, config)
    return cci_record(shell, kind="go", to=[WALKER], at=actions.stamp(shell.now()), text=text, refs={"targets": ["sand"]} if refs is None else refs)


def cut_silent(shell: FakeShell) -> list[str]:
    return [line for line in escalations(shell) if line.startswith("CUT-SILENT")]


@pytest.mark.parametrize(
    ("kind", "text", "refs"),
    [
        ("go", "GO CUT (01:47 PM): sand-only --emergency cut NOW at f05ddf0dbbc (carries 41176 and 41178)", {"prs": [41176]}),
        ("go", "release start after the head lands, at 7d9d9602f9d", {}),
        ("decision", "Pre-ruling: a LANDED revert PR is forward-only and cuts under the standing foreign-cut rule (#98906)", {"path": "/blobs/82ca5ea90a3c62f3.txt"}),
        ("go", "GO #41287 (open_elsewhere reopen): --fast enqueue on green; sand-only --emergency cut at its squash by manual-release-at", {"prs": [41287]}),
    ],
    ids=["go-cut-with-prs", "release-start-no-refs", "prose-decision", "enqueue-go-mentioning-a-cut"],
)
def test_a_root_record_that_mentions_a_cut_without_a_target_ref_opens_no_cut_clock(shell, config, tmp_path, kind, text, refs):
    seq = go_to_walker(shell, config, text=text, refs=refs)
    shell.records[-1]["kind"] = kind
    orca_pass(shell, config)
    shell.sleep(5 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert f"go:{seq}" not in incident(tmp_path, "desk-runner").facts.get("cut-clocks", {})
    assert cut_silent(shell) == [] and [call for call in launches(shell) if call[1].startswith("manual-release-")] == []


@pytest.mark.parametrize(
    ("to", "text", "refs"),
    [
        (
            ["inc-a-fix", "landing-desk-42", "fidelity-desk-14", WALKER],
            "GO 01:26 PM: #41176 plain enqueue on green + approval + fidelity ok; fidelity-desk-14 grades --pr 41176. It rides the next sand cut after the #41163 one (walker-7 keys that cut on #41176 + #41170 landed).",
            {},
        ),
        (
            ["inc-b-fix", "landing-desk-42", "manual-release-at", "alerts-desk"],
            "GO option A now: build with codex sol xhigh under your contract, one PR off dev, direct merge, sand cut at the squash; HEAD on every push",
            {"path": "/blobs/00a2e945f92936ee.txt"},
        ),
    ],
    ids=["104822-future-tense-go", "107029-order-naming-no-component"],
)
def test_the_orders_behind_the_phantom_cutters_open_no_cut_clock_and_launch_no_manual_release_lane(shell, config, tmp_path, to, text, refs):
    """CUT-SILENT #104822 launched manual-release-after from the future-tense GO #104776, and #107029 fired on GO #106999, which named no component; both orders are reshaped here without their content (backlog dcf28ec item 2)."""
    orca_pass(shell, config)
    seq = cci_record(shell, to=to, at=actions.stamp(shell.now()), text=text, refs=refs)
    orca_pass(shell, config)
    shell.sleep(5 * 60)
    orca_pass(shell, config)
    shell.sleep(60)
    orca_pass(shell, config)
    assert f"go:{seq}" not in incident(tmp_path, "desk-runner").facts.get("cut-clocks", {})
    assert cut_silent(shell) == [] and [call for call in launches(shell) if call[1].startswith("manual-release-")] == []


def test_a_landing_release_that_names_no_release_with_component_opens_no_cut_clock(shell, config, tmp_path):
    land_incident(shell, config)
    orca_pass(shell, config)
    landing_pass(shell, config)
    orca_pass(shell, config)
    shell.sleep(5 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert "release:36642" not in incident(tmp_path, "desk-runner").facts.get("cut-clocks", {}) and cut_silent(shell) == []


@pytest.mark.parametrize(
    ("kind", "refs", "closed"),
    [
        ("release", {"targets": ["sand"], "builds": ["https://buildkite.com/forge/release/builds/8864"]}, "release"),
        ("refuse", {"targets": ["sand"]}, "refuse"),
        ("release", {"targets": ["sand"]}, None),
    ],
    ids=["keeps-the-lanes-build", "keeps-the-lanes-refusal", "resolves-a-release-with-no-build"],
)
def test_an_order_that_resolves_the_release_lanes_answer_is_answered_by_it(shell, config, tmp_path, kind, refs, closed):
    orca_pass(shell, config)
    answer = cci_record(shell, lane=WALKER, kind=kind, refs=refs, at=actions.stamp(shell.now()), text="CUT ALREADY STARTED: build 8864")
    shell.sleep(60)
    seq = cci_record(shell, kind="go", to=[WALKER], resolves=answer, at=actions.stamp(shell.now()), text="KEEP 8864, the lane's sand cut at 94d61b91951", refs={"targets": ["sand"]})
    orca_pass(shell, config)
    shell.sleep(5 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    ended = incident(tmp_path, "desk-runner").facts["cut-clocks"][f"go:{seq}"]["ended"]
    if closed:
        assert cut_silent(shell) == [] and ended == f"#{answer} {closed}"
    else:
        assert len(cut_silent(shell)) == 1 and ended == "silent"


@pytest.mark.parametrize(
    ("record", "withdrawn"),
    [
        ({"kind": "answer", "resolves": "silent", "text": "WITHDRAWN: not a cut-silent; build 8865 is the cut"}, "resolves CUT-SILENT"),
        ({"lane": WALKER, "kind": "release", "refs": {"targets": ["sand"], "builds": ["https://buildkite.com/forge/release/builds/8865"]}, "text": "CUT build 8865"}, "release"),
        ({"kind": "note", "resolves": "order", "text": "the cut already runs"}, "resolves"),
        ({"kind": "note", "re": "silent", "text": "noted"}, None),
        (None, None),
    ],
    ids=["root-resolves-the-cut-silent", "lane-cuts-in-the-grace-minute", "root-resolves-the-order", "root-re-only", "nothing"],
)
def test_a_withdrawal_inside_the_grace_minute_fails_the_manual_release_launch_before_it_dispatches(shell, config, tmp_path, record, withdrawn):
    seq = go_to_walker(shell, config)
    orca_pass(shell, config)
    shell.sleep(4 * 60)
    orca_pass(shell, config)
    [silent] = [index + 1 for index, post in enumerate(shell.posts) if post["text"].startswith("CUT-SILENT")]
    orca_pass(shell, config)
    assert [call for call in launches(shell) if call[1] == "manual-release-sand"] == []
    if record:
        links = {link: {"silent": silent, "order": seq}[record[link]] for link in ("resolves", "re") if link in record}
        cci_record(shell, at=actions.stamp(shell.now()), **(record | links))
    shell.sleep(60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    launched = [call for call in launches(shell) if call[1] == "manual-release-sand"]
    if withdrawn:
        [line] = [line for line in escalations(shell) if line.startswith("WITHDRAWN")]
        assert line.startswith(f"WITHDRAWN cut:go:{seq} manual-release-sand: #") and withdrawn in line and line.endswith("; nothing was launched")
        assert launched == [] and incident(tmp_path, "desk-runner").facts["cut-clocks"][f"go:{seq}"]["ended"].startswith("withdrawn: #")
        assert incident(tmp_path, "desk-lane-manual-release-sand").actions[f"cut:go:{seq}"].status == "failed"
    else:
        assert len(launched) == 1 and not [line for line in escalations(shell) if line.startswith("WITHDRAWN")]


@pytest.mark.parametrize(
    ("record", "closed"),
    [
        ({"kind": "note", "resolves": "order", "text": "RESOLVED: answered by the cut already running"}, "resolves"),
        ({"kind": "note", "re": "order", "text": "noted; the walker cuts"}, None),
        ({"lane": "manual-release-at", "kind": "release", "refs": {"prs": [41176]}, "text": "CUT for #41176 started"}, "release"),
        ({"lane": "alerts-desk", "kind": "fix-live", "refs": {"prs": [41176]}, "text": "RELEASED sand 0.162.1467"}, "fix-live"),
        ({"lane": "alerts-desk", "kind": "fix-live", "refs": {"prs": ["Forge-AI/monorepo#41176"]}, "text": "RELEASED sand 0.162.1467"}, "fix-live"),
        ({"lane": "manual-release-at", "kind": "release", "refs": {"prs": [41177]}, "text": "CUT for #41177 started"}, None),
    ],
    ids=["root-resolves", "root-re", "release-naming-its-pr", "fix-live-naming-its-pr", "fix-live-naming-its-qualified-pr", "release-for-another-pr"],
)
def test_a_root_resolution_or_a_release_naming_its_pr_closes_the_cut_clock(shell, config, tmp_path, record, closed):
    seq = go_to_walker(shell, config, refs={"targets": ["sand"], "prs": [41176]})
    orca_pass(shell, config)
    shell.sleep(60)
    links = {link: seq for link in ("resolves", "re") if record.get(link) == "order"}
    cci_record(shell, at=actions.stamp(shell.now()), **(record | links))
    shell.sleep(5 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    ended = incident(tmp_path, "desk-runner").facts["cut-clocks"][f"go:{seq}"]["ended"]
    if closed:
        assert cut_silent(shell) == [] and ended.endswith(closed) and not [call for call in launches(shell) if call[1] == "manual-release-sand"]
    else:
        assert len(cut_silent(shell)) == 1 and ended == "silent"


def test_a_cut_order_with_no_cut_for_four_minutes_escalates_once_and_launches_the_manual_release_lane(shell, config, tmp_path):
    seq = go_to_walker(shell, config)
    orca_pass(shell, config)
    shell.sleep(3 * 60)
    orca_pass(shell, config)
    assert [line for line in escalations(shell) if line.startswith("CUT-SILENT")] == []
    shell.sleep(60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    [line] = [line for line in escalations(shell) if line.startswith("CUT-SILENT")]
    until = actions.stamp(shell.now() + timedelta(hours=2))
    hand = f"tools/ci release claim sand/<env> --purpose incident --reason 'GO #{seq} cut by hand' --until {until} && tools/ci release start --manual sand --env <env> --commit 94d61b91951 --emergency"
    assert line.startswith(f"CUT-SILENT {WALKER} sand: no CUT or refusal 4 min after GO #{seq}; hand path: {hand} | manual-release-sand launching on opus xhigh, brief ")
    shell.sleep(60)
    orca_pass(shell, config)
    [launch] = [call for call in launches(shell) if call[1] == "manual-release-sand"]
    assert launch[2:4] == ["opus", "xhigh"]
    brief = Path(launch[4]).read_text()
    assert brief.startswith("ccx: lane=manual-release-sand role=release place=local incident=none effort=xhigh\n") and hand in brief and f"after GO #{seq}" in brief


def test_a_manual_release_launch_stalls_sixty_seconds_after_its_acceptance_with_no_grace_added(shell, config, tmp_path):
    seq = go_to_walker(shell, config)
    orca_pass(shell, config)
    shell.sleep(4 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert len(cut_silent(shell)) == 1 and stalled(shell) == []
    accepted = incident(tmp_path, "desk-lane-manual-release-sand").actions[f"cut:go:{seq}"]
    shell.orca_down = UNREACHABLE_FAILURES[0]
    shell.clock = actions.parse_stamp(accepted.accepted_at) + timedelta(seconds=60)
    orca_pass(shell, config)
    [line] = stalled(shell)
    assert line.startswith(f"LAUNCH-STALLED stalled:desk-lane-manual-release-sand/cut:go:{seq} manual-release-sand: cut:go:{seq} accepted at ") and "has no dispatch 60 s on" in line


@pytest.mark.parametrize(("kind", "refs"), [("release", {"targets": ["sand"], "builds": ["https://buildkite.com/forge/release/builds/8748"]}), ("refuse", {"targets": ["sand"]})], ids=["cut", "refused"])
def test_a_cut_or_a_refusal_from_the_release_lane_stops_the_cut_clock(shell, config, tmp_path, kind, refs):
    go_to_walker(shell, config)
    orca_pass(shell, config)
    shell.sleep(2 * 60)
    cci_record(shell, lane=WALKER, kind=kind, refs=refs, at=actions.stamp(shell.now()), text="CUT build 8748" if kind == "release" else "tools/ci refused the start: InWatch")
    orca_pass(shell, config)
    shell.sleep(5 * 60)
    orca_pass(shell, config)
    assert [line for line in escalations(shell) if line.startswith("CUT-SILENT")] == [] and not [call for call in launches(shell) if call[1] == "manual-release-sand"]


def test_a_refusal_that_says_a_release_already_ships_the_commit_reaches_the_manual_release_lane(shell, config, tmp_path):
    go_to_walker(shell, config)
    orca_pass(shell, config)
    shell.sleep(4 * 60)
    orca_pass(shell, config)
    shell.sleep(60)
    orca_pass(shell, config)
    seq = cci_record(shell, lane=WALKER, kind="refuse", refs={"targets": ["sand"]}, at=actions.stamp(shell.now()), text="start refused: release 2026-10-20/33 already ships sand at `6de9540` or later")
    orca_pass(shell, config)
    relay = incident(tmp_path, "desk-lane-manual-release-sand").actions[f"cut-refuse:{seq}"]
    assert json.loads(relay.target)["text"] == f"{WALKER} posted on cci #{seq}: start refused: release 2026-10-20/33 already ships sand at `6de9540` or later"


@pytest.mark.parametrize("mechanism", [True, False], ids=["after-a-mechanism", "with-no-mechanism-record"])
def test_an_opened_pr_stops_the_pr_threshold_and_counts_for_the_mechanism(shell, config, tmp_path, mechanism):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(4 * 60)
    if mechanism:
        lane_record(shell, LANE, "mechanism")
    shell.sleep(4 * 60)
    lane_record(shell, LANE, "opened", prs=[4242])
    shell.sleep(8 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert clock_lines(shell) == [] and len(launches(shell)) == 1


@pytest.mark.parametrize(("kind", "text"), [("ready", "READY #4242 green at 9c0b891"), ("report", "READY #4242 green at 9c0b891"), ("incident", "READY --fast GREEN #4242")])
def test_a_ready_record_stops_the_clock(shell, config, tmp_path, capsys, kind, text):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(4 * 60)
    lane_record(shell, LANE, "mechanism")
    lane_record(shell, LANE, "report", text="checkpoint: READY is close")
    shell.sleep(8 * 60)
    lane_record(shell, LANE, "opened", prs=[4242])
    shell.sleep(6 * 60)
    lane_record(shell, LANE, kind, text=text, prs=[4242])
    orca_pass(shell, config)
    shell.sleep(30 * 60)
    orca_pass(shell, config)
    assert clock_lines(shell) == [] and len(launches(shell)) == 1
    capsys.readouterr()
    cli(shell, config, "show")
    assert "- incident clock [stopped: ready] launched 11:00; mechanism +4 min, PR +12 min, ready +18 min\n" in capsys.readouterr().out


def test_the_ready_threshold_names_the_missing_milestone_and_each_phases_minutes(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(4 * 60)
    lane_record(shell, LANE, "mechanism")
    shell.sleep(8 * 60)
    lane_record(shell, LANE, "opened", prs=[4242])
    shell.sleep(8 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert clock_lines(shell) == [
        f"INCIDENT-CLOCK {LANE} ready: not ready 20 min after its launch at 11:00, limit 20 min; missing ready; launch to mechanism 4 min, mechanism to PR 8 min, PR to now 8 min"
    ]
    assert len(launches(shell)) == 1


def test_an_opus_fallback_lane_is_clocked_and_gets_no_fallback_of_its_own(shell, config, tmp_path):
    backup = f"{LANE}-opus"
    clocked_lane(shell, config, tmp_path, lane=backup, model="opus", header="ccx: role=incident place=local\n")
    shell.sleep(15 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert [line.split(" | ")[1] for line in clock_lines(shell, backup)] == [f"{backup} is itself an Opus fallback, so none is launched for it"] * 2
    assert len(launches(shell)) == 1 and shell.entries == []
    assert not (tmp_path / "store" / f"desk-lane-{backup}-opus.json").exists()


def test_a_lane_whose_opus_sibling_is_already_live_gets_no_second_fallback(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    shell.launch(f"{LANE}-opus", "ctx_hand")
    shell.sleep(15 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert [line.split(" | ")[1] for line in clock_lines(shell)] == [f"no fallback launched for {LANE}-opus: dispatch ctx_hand is dispatched; relay to it instead"] * 2
    assert len(launches(shell)) == 1 and shell.entries == []


@pytest.mark.parametrize(
    ("status", "dispatch", "fix", "stands_down"),
    [("started", "", LANE, True), ("verified", "dispatched", LANE, True), ("verified", "completed", LANE, False), ("failed", "", LANE, False), ("started", "", "another-fix", False)],
)
def test_a_lane_whose_executor_backup_is_launching_or_live_gets_no_fallback_from_the_clock(shell, config, tmp_path, status, dispatch, fix, stands_down):
    clocked_lane(shell, config, tmp_path)
    backup = executor_backup(shell, status, fix)
    shell.dispatches["ctx_backup"] = {"status": dispatch, "terminal": "term_ctx_backup"}
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    [line] = clock_lines(shell)
    stood_down = f"no fallback launched for {LANE}-opus: the incident executor's Opus backup {backup} is launching or live, and a fix lane gets one Opus fallback"
    assert (line.split(" | ")[1] == stood_down) is stands_down
    assert len(launches(shell)) == (1 if stands_down else 2)
    assert [call[4] for call in shell.calls if call[:3] == ["orca", "orchestration", "worker-show"] and call[4] == "ctx_backup"] == ["ctx_backup"] * (status == "verified")


@pytest.mark.parametrize("end", ["completed", "failed", "fix-live", "not-ours", "stopped", "stand-down", "fix-live status"])
def test_a_settled_dispatch_or_a_closing_record_stops_the_clock(shell, config, tmp_path, capsys, end):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(5 * 60)
    if end in ("completed", "failed"):
        shell.dispatches[f"ctx_{LANE}"]["status"] = end
    elif end == "fix-live status":
        shell.receive({"subject": "fix-live: 11:05 AM PT alert recovered", "from_handle": f"term_ctx_{LANE}"})
    else:
        lane_record(shell, LANE, end)
    orca_pass(shell, config)
    shell.sleep(20 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert clock_lines(shell) == [] and len(launches(shell)) == 1
    capsys.readouterr()
    cli(shell, config, "show")
    stopped = f"dispatch {end}" if end in ("completed", "failed") else end.removesuffix(" status")
    assert f"- incident clock [stopped: {stopped}] launched 11:00; " in capsys.readouterr().out


def test_a_record_from_before_the_launch_does_not_stop_the_clock(shell, config, tmp_path):
    lane_record(shell, LANE, "fix-live", at="2026-10-01T17:00:00Z")
    lane_record(shell, LANE, "mechanism", at="2026-10-01T17:30:00Z")
    clocked_lane(shell, config, tmp_path)
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    assert [line.split(":", 1)[0] for line in clock_lines(shell)] == [f"INCIDENT-CLOCK {LANE} mechanism"]


@pytest.mark.parametrize(("model", "header", "clocked"), [
    ("opus", "ccx: place=local\n", False),
    ("opus", "ccx: incident=none role=fix place=local\n", False),
    ("incident", "ccx: role=evidence\n", False),
    ("opus", "ccx: incident=327967001 role=retro place=local\n", False),
    ("opus", "ccx: role=incident place=local\n", True),
    ("sol", "ccx: incident=327967001 role=fix place=local\n", True),
    ("incident", "ccx: role=fix\n", True),
])
def test_only_an_incident_fix_lane_is_clocked(shell, config, tmp_path, capsys, model, header, clocked):
    clocked_lane(shell, config, tmp_path, model=model, header=header)
    shell.sleep(30 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert [line.split(":", 1)[0] for line in clock_lines(shell)] == [f"INCIDENT-CLOCK {LANE} {about}" for about in ("mechanism", "pr", "ready")] * clocked
    assert len(launches(shell)) == 1 + clocked
    capsys.readouterr()
    cli(shell, config, "show")
    assert ("- incident clock [" in capsys.readouterr().out) is clocked


@pytest.mark.parametrize(("minutes", "threshold"), [(10, "mechanism"), (15, "pr"), (20, "ready")])
def test_an_evidence_lane_launched_with_its_role_passes_each_threshold_unclocked(shell, config, tmp_path, minutes, threshold):
    lanes = {"inc-fix": "fix", "inc-evidence": "evidence"}
    orca_pass(shell, config)
    for n, (lane, role) in enumerate(lanes.items(), 1):
        brief = tmp_path / f"{lane}.brief.md"
        brief.write_text("Work the alert on api.\n")
        cci_go(shell, f"R{n} orca-desk: launch {lane} NOW incident xhigh role={role} brief={brief}")
    orca_pass(shell, config)
    for lane in lanes:
        shell.launch(lane, f"ctx_{lane}")
    orca_pass(shell, config)
    shell.sleep(minutes * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert [line.split(":", 1)[0] for line in clock_lines(shell, "inc-fix")][-1] == f"INCIDENT-CLOCK inc-fix {threshold}"
    assert clock_lines(shell, "inc-evidence") == []
    assert sorted(argv[1] for argv in launches(shell)) == ["inc-evidence", "inc-fix", "inc-fix-opus"]


def test_the_clock_thresholds_come_from_the_configs_deadlines(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    config.write_text(json.dumps({**raw, "deadlines": {"incident_mechanism_minutes": 3, "incident_pr_minutes": 4, "incident_ready_minutes": 5}}))
    clocked_lane(shell, config, tmp_path)
    shell.sleep(3 * 60)
    orca_pass(shell, config)
    assert [line.split(";", 1)[0] for line in clock_lines(shell)] == [f"INCIDENT-CLOCK {LANE} mechanism: no mechanism 3 min after its launch at 11:00, limit 3 min"]
    shell.sleep(2 * 60)
    orca_pass(shell, config)
    assert [line.split(";", 1)[0] for line in clock_lines(shell)][1:] == [
        f"INCIDENT-CLOCK {LANE} pr: no PR 5 min after its launch at 11:00, limit 4 min",
        f"INCIDENT-CLOCK {LANE} ready: not ready 5 min after its launch at 11:00, limit 5 min",
    ]


def test_the_clock_logs_nothing_while_cci_cannot_be_read(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(10 * 60)
    shell.tail_error = "cci: store locked"
    orca_pass(shell, config)
    assert clock_lines(shell) == [] and [line.split()[0] for line in escalations(shell)].count("CCI-TAIL-FAILED") == 1
    shell.tail_error = ""
    orca_pass(shell, config)
    assert len(clock_lines(shell)) == 1


def test_an_opus_fallback_whose_orca_launch_fails_once_is_launched_once_more(shell, config, tmp_path):
    brief = clocked_lane(shell, config, tmp_path)
    backup = f"{LANE}-opus"
    shell.launch_line = f"{backup} failed worktree checkout: index still differs from HEAD after 180s\n"
    shell.sleep(10 * 60)
    for _ in range(3):
        orca_pass(shell, config)
    shell.launch_line = f"{backup} ready task=task_1 dispatch=ctx_opus terminal=term_ctx_opus worktree=/w\n"
    for _ in range(2):
        orca_pass(shell, config)
    fallback = written(tmp_path, f"{backup}")
    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, "incident", "xhigh", str(brief)], *[[str(runner_module.LAUNCHER), backup, "opus", "xhigh", str(fallback)]] * 2]
    tries = incident(tmp_path, f"desk-lane-{backup}").actions
    assert [(key, action.status) for key, action in tries.items()] == [(f"incident-clock:ctx_{LANE}", "failed"), (f"incident-clock:ctx_{LANE}:retry", "verified")]
    assert [line for line in clock_lines(shell) if " in-process: " in line] == []


def test_an_opus_fallback_refused_on_load_twice_goes_to_the_root_in_process(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(10 * 60)
    shell.cpu_load = 40
    for _ in range(4):
        orca_pass(shell, config)
    backup = f"{LANE}-opus"
    fallback = written(tmp_path, f"{backup}")
    assert len(launches(shell)) == 1
    tries = incident(tmp_path, f"desk-lane-{backup}").actions
    assert [(key, action.status) for key, action in tries.items()] == [(f"incident-clock:ctx_{LANE}", "failed"), (f"incident-clock:ctx_{LANE}:retry", "failed")]
    refused = "refused on load 40 above 8 cores; nothing was launched, and the root launches it in-process or once the load is moved"
    assert [line for line in clock_lines(shell) if " in-process: " in line] == [
        f"INCIDENT-CLOCK {LANE} in-process: the Orca launch of the Opus fallback {backup} failed twice: incident-clock:ctx_{LANE} {refused} | incident-clock:ctx_{LANE}:retry {refused} | root: start {backup} in-process now, an Agent of type long-running:lane-ship named {backup} on opus in fast mode with the brief {fallback}"
    ]
    assert [line.split()[0] for line in escalations(shell)].count("LOAD-HIGH") == 1
    for _ in range(2):
        orca_pass(shell, config)
    assert len([line for line in clock_lines(shell) if " in-process: " in line]) == 1


def test_no_retry_or_in_process_fallback_while_the_executors_backup_is_live(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(10 * 60)
    shell.cpu_load = 40
    orca_pass(shell, config)
    executor_backup(shell, "verified")
    shell.dispatches["ctx_backup"] = {"status": "dispatched", "terminal": "term_backup"}
    for _ in range(3):
        orca_pass(shell, config)
    assert [line for line in clock_lines(shell) if " in-process: " in line] == []
    assert list(incident(tmp_path, f"desk-lane-{LANE}-opus").actions) == [f"incident-clock:ctx_{LANE}"]


def test_no_in_process_fallback_once_the_lanes_clock_has_stopped(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(10 * 60)
    shell.cpu_load = 40
    orca_pass(shell, config)
    lane_record(shell, LANE, "ready")
    for _ in range(3):
        orca_pass(shell, config)
    assert [line for line in clock_lines(shell) if " in-process: " in line] == []
    assert list(incident(tmp_path, f"desk-lane-{LANE}-opus").actions) == [f"incident-clock:ctx_{LANE}"]


def test_a_fallback_for_a_high_cpu_incident_brief_stays_on_the_mac(shell, config, tmp_path):
    with_sprites(config)
    clocked_lane(shell, config, tmp_path, header="ccx: role=fix cpu=high\n")
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert sprite_launches(shell) == [] and [call[1] for call in launches(shell)] == [LANE, f"{LANE}-opus"]


CASES = ("resume of a thread with attachments still fails", "resume after an interrupted import loses the draft")


def unfixed_report(shell: FakeShell, lane: str, kind: str, *cases: str, **fields) -> dict:
    body = "\n".join([f"PR #4242 is green on {lane}.", *(f"UNFIXED {lane}: {case}" for case in cases)])
    message = {"subject": f"{kind}: READY {lane}: #4242 green", "body": body, "from_handle": f"term_ctx_{lane}"}
    if kind == "worker_done":
        message |= {"type": "worker_done", "subject": "shipped", "payload": json.dumps({"dispatchId": f"ctx_{lane}", "outcome": "succeeded"})}
    return shell.receive(message | fields)


def incident_lines(shell: FakeShell, lane: str = LANE) -> list[str]:
    return [line for line in escalations(shell) if line.startswith(f"INCIDENT {lane} ")]


@pytest.mark.parametrize("kind", ["ready", "fix-live"])
def test_each_case_an_incident_lane_reports_unfixed_launches_an_incident_lane_in_the_same_pass(shell, config, tmp_path, kind):
    brief = clocked_lane(shell, config, tmp_path)
    shell.sleep(60)
    lane_record(shell, LANE, "opened", prs=[4250])
    lane_record(shell, LANE, "opened", prs=[4242, "yasyf/cc-skills#77", "4250"])
    unfixed_report(shell, LANE, kind, *CASES)
    orca_pass(shell, config)
    lanes = [f"{LANE}-case1", f"{LANE}-case2"]
    briefs = [written(tmp_path, f"{lane}") for lane in lanes]
    assert launches(shell)[1:] == [[str(runner_module.LAUNCHER), lane, "incident", "xhigh", str(path)] for lane, path in zip(lanes, briefs, strict=True)]
    for lane, path, case in zip(lanes, briefs, CASES, strict=True):
        text = path.read_text()
        assert text.startswith(f"ccx: lane={lane} role=incident effort=xhigh\n") and "{" not in text
        assert f"The case, in the words of `{LANE}`:\n{case}\n" in text
        assert f"`{LANE}` works from the brief at {brief}." in text and "Its pull requests so far: #4242 #4250 yasyf/cc-skills#77." in text
        assert "Reproduce the case above exactly as written" in text and f"`UNFIXED {lane}: none`" in text
    assert shell.entries == [f"INCIDENT {LANE}: unfixed case brief for {lane}: {case}" for lane, case in zip(lanes, CASES, strict=True)]
    assert incident_lines(shell) == [
        f"INCIDENT {LANE} 11:01: its fix does not cover: {case} | case lane {lane} launching on incident xhigh, brief {path}; root: ratify, and keep the reporter told that this case is still open"
        for lane, path, case in zip(lanes, briefs, CASES, strict=True)
    ]
    assert [post["kind"] for post in shell.posts if post["text"].startswith(f"INCIDENT {LANE} ")] == ["incident", "incident"]


def test_an_unfixed_none_line_launches_nothing(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    unfixed_report(shell, LANE, "ready", "none")
    unfixed_report(shell, LANE, "fix-live", "None.")
    orca_pass(shell, config)
    assert len(launches(shell)) == 1 and shell.entries == [] and incident_lines(shell) == []


def test_a_case_reported_again_launches_nothing_new(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    message = unfixed_report(shell, LANE, "ready", CASES[0])
    orca_pass(shell, config)
    shell.launch(f"{LANE}-case1", "ctx_case1")
    orca_pass(shell, config)
    unfixed_report(shell, LANE, "ready", CASES[0], id=message["id"])
    orca_pass(shell, config)
    shell.dispatches["ctx_case1"]["status"] = "completed"
    unfixed_report(shell, LANE, "fix-live", *CASES)
    orca_pass(shell, config)
    assert [call[1] for call in launches(shell)] == [LANE, f"{LANE}-case1", f"{LANE}-case2"]
    assert incident(tmp_path, f"desk-lane-{LANE}").facts["unfixed-cases"] == list(CASES)
    assert len(incident_lines(shell)) == 2


@pytest.mark.parametrize(("model", "header"), [("opus", "ccx: place=local\n"), ("incident", "ccx: role=evidence\n")])
def test_an_unfixed_line_from_a_lane_that_is_no_incident_fix_lane_launches_nothing(shell, config, tmp_path, model, header):
    clocked_lane(shell, config, tmp_path, model=model, header=header)
    shell.launch("by-hand", "ctx_by-hand")
    unfixed_report(shell, LANE, "ready", *CASES)
    unfixed_report(shell, "by-hand", "ready", *CASES)
    orca_pass(shell, config)
    assert len(launches(shell)) == 1 and shell.entries == [] and incident_lines(shell) == []


def test_a_case_lanes_new_case_reaches_the_root_with_its_launch_line_and_its_own_case_launches_nothing(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    unfixed_report(shell, LANE, "ready", CASES[0])
    orca_pass(shell, config)
    case = f"{LANE}-case1"
    shell.launch(case, f"ctx_{case}")
    orca_pass(shell, config)
    message = unfixed_report(shell, case, "ready", *CASES)
    orca_pass(shell, config)
    assert [call[1] for call in launches(shell)] == [LANE, case]
    brief = written(tmp_path, f"{LANE}-case2")
    line = f"orca-desk: launch {LANE}-case2 NOW incident xhigh brief={brief}"
    assert [line for line in escalations(shell) if line.startswith(("DECIDE ", "UNFIXED "))] == [
        f"DECIDE unfixed:{message['id']} {case}: a case lane reports new cases, past the one-generation cap: {CASES[1]} | to launch its lane, post `{line}`"
    ]
    assert f"`{case}` works from the brief at {written(tmp_path, f'{case}')}." in brief.read_text()
    desk_orders(shell, f"R40 {line}")
    orca_pass(shell, config)
    assert [call[1] for call in launches(shell)] == [LANE, case, f"{LANE}-case2"]
    assert incident(tmp_path, f"desk-lane-{LANE}").facts["unfixed-cases"] == [CASES[0]]


def two_case_lanes(shell: FakeShell, config: Path, tmp_path: Path) -> dict:
    clocked_lane(shell, config, tmp_path)
    message = unfixed_report(shell, LANE, "ready", *CASES)
    orca_pass(shell, config)
    for n in (1, 2):
        shell.launch(f"{LANE}-case{n}", f"ctx_case{n}")
    orca_pass(shell, config)
    return message


def test_a_withdraw_names_a_case_lane_or_its_launch_id_and_stops_its_dispatch(shell, config, tmp_path):
    message = two_case_lanes(shell, config, tmp_path)
    desk_orders(shell, f"R21 orca-desk: withdraw {LANE}-case1", f"R22 orca-desk: withdraw unfixed:{message['id']}:2")
    orca_pass(shell, config)
    assert stops(shell) == [["worker-stop", "--dispatch", "ctx_case1"], ["worker-stop", "--dispatch", "ctx_case2"]]
    assert withdrawals(shell) == [
        f"WITHDRAWN {LANE}-case1 {LANE}-case1: by R21; dispatch ctx_case1 stopped (stopped), and no report of it launches a case lane",
        f"WITHDRAWN unfixed:{message['id']}:2 {LANE}-case2: by R22; dispatch ctx_case2 stopped (stopped), and no report of it launches a case lane",
    ]


def test_a_withdrawn_lanes_stop_report_launches_no_case_lane_and_its_redispatch_offer_asks_no_one(shell, config, tmp_path):
    two_case_lanes(shell, config, tmp_path)
    desk_orders(shell, f"R21 orca-desk: withdraw {LANE}")
    orca_pass(shell, config)
    unfixed_report(shell, LANE, "ready", "SoFi mount timeouts x16, alerts desk owns; root withdrew this fix lane")
    offer = shell.receive({"type": "escalation", "subject": "Agent exited unexpectedly (Agent process stop was requested but never confirmed)", "body": "The task is ready to be dispatched again.", "from_handle": f"term_ctx_{LANE}"})
    orca_pass(shell, config)
    assert [call[1] for call in launches(shell)] == [LANE, f"{LANE}-case1", f"{LANE}-case2"]
    assert not [call for call in shell.calls if call[0] == "claude"] and not [line for line in escalations(shell) if line.startswith("DECIDE ")]
    assert [line for line in escalations(shell) if line.startswith(("UNFIXED ", "WITHDRAWN "))] == [
        f"WITHDRAWN {LANE} {LANE}: by R21; dispatch ctx_{LANE} stopped (stopped), and no report of it launches a case lane",
        f"UNFIXED {LANE} 11:00: SoFi mount timeouts x16, alerts desk owns; root withdrew this fix lane | no case lane launched: the root withdrew {LANE} (R21)",
        f"WITHDRAWN {offer['id']} {LANE}: withdrawn by R21, so it is not dispatched again and its question needs no answer: {offer['subject']}",
    ]


def test_a_case_lane_the_root_stopped_by_hand_is_withdrawn_and_never_offered_again(shell, config, tmp_path):
    two_case_lanes(shell, config, tmp_path)
    shell.dispatches["ctx_case1"] |= {"status": "failed", "fenced": True}
    offer = shell.receive({"type": "escalation", "subject": "Agent exited unexpectedly (Agent process stop was requested but never confirmed)", "body": "The task is ready to be dispatched again.", "from_handle": "term_ctx_case1"})
    orca_pass(shell, config)
    unfixed_report(shell, f"{LANE}-case1", "ready", "a third case", from_handle="term_ctx_case1")
    orca_pass(shell, config)
    assert not [call for call in shell.calls if call[0] == "claude"] and not [line for line in escalations(shell) if line.startswith("DECIDE ")]
    assert f"WITHDRAWN {offer['id']} {LANE}-case1: withdrawn by a stop of dispatch ctx_case1, so it is not dispatched again and its question needs no answer: {offer['subject']}" in escalations(shell)
    assert incident(tmp_path, f"desk-lane-{LANE}-case1").facts["withdrawn"] == {"dispatch": "ctx_case1", "by": "a stop of dispatch ctx_case1"}
    assert [line.split(" | ")[1] for line in escalations(shell) if line.startswith(("UNFIXED ", "DECIDE "))] == [f"no case lane launched: the root withdrew {LANE}-case1 (a stop of dispatch ctx_case1)"]
    assert len(launches(shell)) == 3


def test_a_case_the_opus_fallback_reports_too_launches_one_case_lane(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    backup = f"{LANE}-opus"
    shell.launch(backup, f"ctx_{backup}")
    orca_pass(shell, config)
    unfixed_report(shell, LANE, "ready", CASES[0])
    unfixed_report(shell, backup, "ready", *CASES)
    orca_pass(shell, config)
    assert [call[1] for call in launches(shell)] == [LANE, backup, f"{LANE}-case1", f"{LANE}-case2"]
    assert incident(tmp_path, f"desk-lane-{LANE}").facts["unfixed-cases"] == list(CASES)


@pytest.mark.parametrize(("lane", "first"), [("a-fix", "a-fix"), ("a-fix-opus", "a-fix"), ("a-fix-case2", "a-fix"), ("a-fix-case2-opus-case11", "a-fix"), ("a-fix-cases", "a-fix-cases"), ("opus-case", "opus-case")])
def test_a_fallback_or_case_lane_descends_from_the_lane_without_those_endings(lane, first):
    assert runner_module.first_lane(lane) == first


def test_an_unfixed_line_that_names_another_lane_launches_nothing(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    shell.receive({"subject": f"ready: READY {LANE}: #4242 green", "body": f"UNFIXED another-fix: {CASES[0]}", "from_handle": f"term_ctx_{LANE}"})
    orca_pass(shell, config)
    assert len(launches(shell)) == 1 and shell.entries == [] and incident_lines(shell) == []


def test_a_case_lane_whose_brief_fails_to_attach_is_named_and_launches_on_the_next_report(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    shell.attach_error = "error: cc-notes: ref lock held"
    unfixed_report(shell, LANE, "ready", CASES[0])
    orca_pass(shell, config)
    assert len(launches(shell)) == 1 and "unfixed-cases" not in incident(tmp_path, f"desk-lane-{LANE}").facts
    assert incident_lines(shell) == [f"INCIDENT {LANE} 11:00: its fix does not cover: {CASES[0]} | case lane {LANE}-case1 not launched: the brief did not attach: error: cc-notes: ref lock held"]
    shell.attach_error = ""
    unfixed_report(shell, LANE, "fix-live", CASES[0])
    orca_pass(shell, config)
    assert [call[1] for call in launches(shell)] == [LANE, f"{LANE}-case1"]
    assert incident(tmp_path, f"desk-lane-{LANE}").facts["unfixed-cases"] == [CASES[0]]


def test_a_worker_done_that_lists_unfixed_cases_launches_no_case_lane(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    unfixed_report(shell, LANE, "worker_done", *CASES)
    orca_pass(shell, config)
    assert len(launches(shell)) == 1 and shell.entries == [] and incident_lines(shell) == []
    assert not [line for line in escalations(shell) if line.startswith(("DECIDE ", "UNFIXED "))]


@pytest.mark.parametrize(
    ("lane", "kind", "to"),
    [(LANE, "not-ours", []), (LANE, "stand-down", []), (f"{LANE}-opus", "not-ours", []), ("root", "stand-down", [LANE]), ("root", "stopped", [LANE]), (LANE, "stopped", [])],
)
def test_a_report_after_its_incident_ended_launches_no_case_lane_and_tells_the_root_once(shell, config, tmp_path, lane, kind, to):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(60)
    seq = cci_record(shell, lane=lane, kind=kind, text=f"{kind.upper()} {LANE}", to=to, at=actions.stamp(shell.clock))
    shell.sleep(60)
    unfixed_report(shell, LANE, "ready", *CASES)
    orca_pass(shell, config)
    assert len(launches(shell)) == 1 and shell.entries == [] and incident_lines(shell) == []
    assert [line for line in escalations(shell) if line.startswith(("DECIDE ", "UNFIXED "))] == [
        f"UNFIXED {LANE} 11:02: {CASES[0]}; {CASES[1]} | no case lane launched: the incident ended with #{seq} {kind.upper()} {lane}"
    ]


@pytest.mark.parametrize(
    "report",
    [
        {"subject": "ready: NOT-OURS: release-v3 owns this incident"},
        {"subject": "fix-live: stood down, root gave the incident to another drive"},
        {"body": "Stopped by the root.\n" + "\n".join(f"UNFIXED {LANE}: {case}" for case in CASES)},
    ],
)
def test_a_report_that_hands_the_incident_back_launches_no_case_lane_and_files_no_decide(shell, config, tmp_path, report):
    clocked_lane(shell, config, tmp_path)
    unfixed_report(shell, LANE, "ready", *CASES, **report)
    orca_pass(shell, config)
    assert len(launches(shell)) == 1 and shell.entries == [] and incident_lines(shell) == []
    assert not [line for line in escalations(shell) if line.startswith(("DECIDE ", "UNFIXED "))]


def test_a_case_lane_that_hands_the_incident_back_files_no_decide(shell, config, tmp_path):
    two_case_lanes(shell, config, tmp_path)
    unfixed_report(shell, f"{LANE}-case2", "ready", "monitoring stays with release-v3", subject="ready: NOT-OURS: duplicate monitoring repair")
    orca_pass(shell, config)
    assert len(launches(shell)) == 3
    assert not [line for line in escalations(shell) if line.startswith(("DECIDE ", "UNFIXED "))]


def test_a_stopped_posted_after_the_report_leaves_its_case_lanes_launching(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    unfixed_report(shell, LANE, "ready", *CASES)
    shell.sleep(60)
    lane_record(shell, LANE, "stopped")
    orca_pass(shell, config)
    assert [call[1] for call in launches(shell)] == [LANE, f"{LANE}-case1", f"{LANE}-case2"]


def test_a_case_launch_still_pending_when_the_root_stands_the_incident_down_never_starts(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    message = unfixed_report(shell, LANE, "ready", CASES[0])
    runner.check()
    assert incident(tmp_path, f"desk-lane-{LANE}-case1").actions[f"unfixed:{message['id']}:1"].status == "accepted"
    seq = cci_record(shell, lane="root", kind="stand-down", text=f"stand-down {LANE}-case1", to=[f"{LANE}-case1"], at=actions.stamp(shell.clock))
    runner.deliver()
    runner.flush()
    assert len(launches(shell)) == 1
    assert incident(tmp_path, f"desk-lane-{LANE}-case1").actions[f"unfixed:{message['id']}:1"].status == "failed"
    assert [line for line in escalations(shell) if line.startswith("WITHDRAWN ")] == [
        f"WITHDRAWN unfixed:{message['id']}:1 {LANE}-case1: the incident ended with #{seq} STAND-DOWN root; nothing was launched"
    ]


def test_an_incident_launches_two_case_lanes_and_puts_the_rest_to_the_root_in_one_decide(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    cases = [f"RayID a4b65fb{n} POST /api/dd/metrics" for n in range(5)]
    message = unfixed_report(shell, LANE, "ready", *cases)
    orca_pass(shell, config)
    assert [call[1] for call in launches(shell)] == [LANE, f"{LANE}-case1", f"{LANE}-case2"]
    assert incident(tmp_path, f"desk-lane-{LANE}").facts["unfixed-cases"] == cases[:2]
    offers = [f"{case} | to launch its lane, post `orca-desk: launch {LANE}-case{n} NOW incident xhigh brief={written(tmp_path, f'{LANE}-case{n}')}`" for n, case in zip((3, 4, 5), cases[2:], strict=True)]
    assert [line for line in escalations(shell) if line.startswith("DECIDE ")] == [f"DECIDE unfixed:{message['id']} {LANE}: past the cap of 2 case lanes per incident: {'; '.join(offers)}"]


@pytest.mark.parametrize("where", ["report", "mechanism"])
def test_a_lane_that_says_its_cause_is_unknown_launches_no_case_lane_without_the_root(shell, config, tmp_path, where):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(60)
    if where == "mechanism":
        lane_record(shell, LANE, "mechanism", f"MECHANISM {LANE}: UNKNOWN transport cause; ten 305 s 524s")
    message = unfixed_report(shell, LANE, "ready", CASES[0], *(["RayID a4b65fe5 GET /api/desktop-releases; original 524 transport cause UNKNOWN"] if where == "report" else []))
    orca_pass(shell, config)
    assert len(launches(shell)) == 1 and incident_lines(shell) == []
    source = f"its report {message['id']}" if where == "report" else f"its mechanism #{shell.records[-1]['seq']}"
    [line] = [line for line in escalations(shell) if line.startswith("DECIDE ")]
    assert line.startswith(f"DECIDE unfixed:{message['id']} {LANE}: {source} says the cause is UNKNOWN, so no case lane launches until the root ratifies it: {CASES[0]} | to launch its lane, post `orca-desk: launch {LANE}-case1 NOW incident xhigh brief=")


def test_a_ready_status_from_the_run_escalates_and_stops_the_clock(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(5 * 60)
    message = unfixed_report(shell, LANE, "ready", "none")
    orca_pass(shell, config)
    shell.sleep(30 * 60)
    orca_pass(shell, config)
    assert f"READY {message['id']} {LANE}: ready: READY {LANE}: #4242 green: PR #4242 is green on {LANE}. UNFIXED {LANE}: none" in escalations(shell)
    assert clock_lines(shell) == [] and len(launches(shell)) == 1


def test_the_unfixed_case_brief_renders_with_every_field_filled():
    fields = {"lane": "dd-7-fix-case1", "parent": "dd-7-fix", "case": "resume of thread T123 still fails", "parent_brief": "/lfs/parent.md", "prs": "#4242", "onset": "11:20", "reported": "11:20", "due": "11:40", "log": "briefs1", "facts": "apply authority: 0 deletes"}
    text = runner_module.UNFIXED_TEMPLATE.read_text().format(**fields)
    assert "{" not in text and all(value in text for value in fields.values())
    assert "fix proven only on a synthetic or neighbouring case is not ready." in text
    assert "`UNFIXED dd-7-fix-case1: none`" in text and "`READY dd-7-fix-case1: " in text


def test_an_incident_line_with_a_twelve_hour_report_time_sets_the_bar_from_it(shell, config, tmp_path):
    orca_pass(shell, config)
    shell.launch_line = "runner-refusing-1841-fix ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    desk_orders(shell, "orca-desk: incident runner-refusing-1841 https://app.datadoghq.com/monitors/333042395 reported=6:41 PM :: Datadog OK -> Alert Runner refusing runs")
    orca_pass(shell, config)
    shell.launch("runner-refusing-1841-fix", "ctx_n")
    orca_pass(shell, config)
    brief = written(tmp_path, "runner-refusing-1841-fix")
    assert launches(shell) == [[str(runner_module.LAUNCHER), "runner-refusing-1841-fix", "incident", "xhigh", str(brief)]]
    assert "  - Reported 18:41 PT. The bar is fixed and live within 20 minutes of the\n    report, by 19:01 PT;" in brief.read_text()


def test_an_incident_line_without_the_report_time_launches_nothing(shell, config, tmp_path):
    orca_pass(shell, config)
    desk_orders(shell, "R44 orca-desk: incident dd-11 https://app.datadoghq.com/monitors/11 :: Datadog OK -> Alert")
    orca_pass(shell, config)
    assert launches(shell) == []
    [line] = escalations(shell)
    assert line == f"INCIDENT-FAILED R44 inbox: one incident per line, in the form `{runner_module.INCIDENT_GRAMMAR}`; nothing was launched"


OWNED_LINE = "- orca-desk: incident dd-7 https://app.datadoghq.com/monitors/7 reported=11:10 :: Datadog OK -> Alert api 5xx"


def owner_claim(shell: FakeShell, claim: str) -> str:
    if claim == "done":
        named = cci_record(shell, lane="alerts-desk", kind="incident", text="[dd-7] api 5xx since 11:02", to=["root"])
        return f"root@d1 done #{cci_record(shell, kind='done', text='Handed to release-v3', resolves=named)}"
    if claim == "stopped":
        return f"root@d1 stopped #{cci_record(shell, kind='stopped', text='STAND DOWN dd-7-fix', to=['dd-7-fix'])}"
    return f"root@release-v3 answer #{cci_record(shell, drive='release-v3', kind='answer', text='release-v3 owns incident dd-7', to=['root@d1'])}"


@pytest.mark.parametrize("claim", ["done", "stopped", "answer"])
def test_an_incident_another_owner_holds_launches_no_lane_and_posts_one_state_line(shell, config, tmp_path, claim):
    orca_pass(shell, config)
    owner = owner_claim(shell, claim)
    seq = desk_orders(shell, OWNED_LINE)
    orca_pass(shell, config)
    assert launches(shell) == [] and shell.entries == [] and "dd-7-fix.full.md" not in shell.attachments
    assert escalations(shell) == [f"INCIDENT-OWNED inbox@cci#{seq} dd-7-fix: incident dd-7 owned by {owner}; no launch"]
    assert [(post["kind"], post["topic"]) for post in shell.posts] == [("state", f"inbox@cci#{seq}")]


@pytest.mark.parametrize(
    "record",
    [
        {"kind": "done", "text": "Handed off", "resolves": 1},
        {"kind": "stopped", "text": "STAND DOWN other-fix", "to": ["other-fix"]},
        {"kind": "stopped", "lane": "dd-7-fix", "text": "stopped", "to": ["root"], "at": "2026-01-01T00:00:00Z"},
        {"kind": "answer", "drive": "release-v3", "text": "release-v3 owns incident dd-8", "to": ["root@d1"]},
        {"kind": "answer", "drive": "release-v3", "text": "release-v3 owns incident dd-7", "to": ["root@other"]},
        {"kind": "answer", "text": "d1 owns incident dd-7", "to": ["root@d1"]},
    ],
)
def test_a_record_that_does_not_give_the_incident_away_leaves_its_launch_alone(shell, config, tmp_path, record):
    shell.launch_line = "dd-7-fix ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    orca_pass(shell, config)
    cci_record(shell, lane="alerts-desk", kind="incident", text="[dd-7] api 5xx since 11:02", to=["root"])
    cci_record(shell, **record)
    desk_orders(shell, OWNED_LINE)
    orca_pass(shell, config)
    shell.launch("dd-7-fix", "ctx_n")
    orca_pass(shell, config)
    brief = written(tmp_path, "dd-7-fix")
    assert launches(shell) == [[str(runner_module.LAUNCHER), "dd-7-fix", "incident", "xhigh", str(brief)]]
    assert not [line for line in escalations(shell) if line.startswith("INCIDENT-OWNED ")]


def test_a_launch_pending_when_another_owner_takes_the_incident_fails_and_never_starts(shell, config, tmp_path):
    orca_pass(shell, config)
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    key = f"inbox@cci#{desk_orders(shell, OWNED_LINE)}"
    runner.read_cci()
    assert incident(tmp_path, "desk-lane-dd-7-fix").actions[key].status == "accepted"
    owner = owner_claim(shell, "done")
    runner.deliver()
    runner.flush()
    assert launches(shell) == []
    assert incident(tmp_path, "desk-lane-dd-7-fix").actions[key].status == "failed"
    assert escalations(shell)[-1] == f"INCIDENT-OWNED {key} dd-7-fix: incident dd-7 owned by {owner}; no launch"


def test_a_cci_read_that_fails_holds_a_pending_incident_launch_until_it_can_be_read(shell, config, tmp_path):
    orca_pass(shell, config)
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    key = f"inbox@cci#{desk_orders(shell, OWNED_LINE)}"
    runner.read_cci()
    shell.tail_error = "cci: daemon unavailable"
    runner.deliver()
    assert launches(shell) == [] and incident(tmp_path, "desk-lane-dd-7-fix").actions[key].status == "accepted"
    shell.tail_error = ""
    orca_pass(shell, config)
    assert len(launches(shell)) == 1


STANDING_ORDERS = """Standing orders, fixed in this template:
  - Reported {} PT. The bar is fixed and live within 20 minutes of the
    report, by {} PT; the desk's limits below run from your launch, inside it.
  - "stop asking people for things you can collect yourself": never ask a reporter
    or anyone else for evidence; read telemetry and Sentry yourself (owner answer 8ff2abf).
  - Verification: no local test runs, builds, typechecks or lint; CI grades; a red CI step is read from its log, fixed, and pushed.
  - Any repair pass or backfill runs on in-region EC2 (cc-notes e192288), never on
    this Mac.
  - A tooling failure gets a same-turn tooling lane plus the hand path; it never
    stops the fix.
"""


@pytest.mark.parametrize("template", [runner_module.ALERT_TEMPLATE, runner_module.UNFIXED_TEMPLATE])
def test_every_desk_incident_brief_template_carries_the_standing_orders_no_rollback_and_no_local_build(template):
    text = template.read_text()
    assert STANDING_ORDERS.format("{reported}", "{due}") in text
    assert "go build" not in text and "go vet" not in text and "typecheck otherwise" not in text
    assert "  0. NO ROLLBACK; find the mechanism and fix forward; the fix ships as a new release\n" in text and "Rollback first" not in text
    assert "No brief, lane, or exception grants a rollback or a revert. One runs only when\n     the owner's own word names that act, after your diagnosis has found the issue" in text
    assert "2f6cdcf" not in text and "roll the target back" not in text


@pytest.mark.parametrize("template", [runner_module.ALERT_TEMPLATE, runner_module.UNFIXED_TEMPLATE])
def test_every_desk_incident_brief_template_names_the_component_that_served_the_failing_request(template):
    text = " ".join(template.read_text().split())
    assert "which component served the failing request: a cloud run is served by the cloud, never by their machine" in text


@pytest.mark.parametrize("template", [runner_module.ALERT_TEMPLATE, runner_module.UNFIXED_TEMPLATE])
def test_every_desk_incident_brief_template_leaves_enqueue_to_the_landing_desk(template):
    text = " ".join(template.read_text().split())
    assert "post READY with the exact head; the landing desk owns enqueue. Never run stack-enqueue, rebase, or push a queued branch." in text
    assert "enqueue it `--fast`" not in text


def test_an_alert_fix_brief_is_sliced_for_the_incident_before_it_attaches(shell, config, tmp_path):
    orca_pass(shell, config)
    desk_orders(shell, ROOT_ALERT)
    orca_pass(shell, config)
    text = written(tmp_path, "alerts-api-0305-fix").read_text()
    assert text.endswith("\nRegister slice: lane=alerts-api-0305-fix step=incident\n")
    assert [[call[0], call[5]] for call in shell.refreshes] == [["slice", "--refresh"]]


def test_the_alert_fix_brief_demands_the_reporters_own_case_and_the_unfixed_field(shell, config, tmp_path):
    orca_pass(shell, config)
    desk_orders(shell, ROOT_ALERT)
    orca_pass(shell, config)
    text = written(tmp_path, "alerts-api-0305-fix").read_text()
    assert "Before READY, reproduce the reporter's own case" in text
    assert "A fix proven only on a synthetic or neighbouring case is not" in text
    assert "`UNFIXED alerts-api-0305-fix: none`" in text and "{" not in text


def test_the_unfixed_grammar_reads_bullets_and_code_ticks_and_names_each_of_the_lanes_cases_once_as_written():
    body = "done.\nUNFIXED a-fix: one\n- UNFIXED a-fix:   two  words \n> `UNFIXED a-fix: three`\nUNFIXED a-fix: one\nUNFIXED a-fix: two words\nUNFIXED a-fix: none\nsee UNFIXED a-fix: four\nUNFIXED b-fix: five\n"
    assert runner_module.unfixed_cases(body, "a-fix") == {"one": "one", "two words": "two  words", "three": "three"}


def test_an_urgent_hold_older_than_fifteen_minutes_escalates_one_decide_line(shell, config, tmp_path):
    orca_pass(shell, config)
    desk_orders(shell, "R50 (3:50 PM PT) orca-desk: hold api-catchup owner=merge-walker-r2 :: api, runtime-v2 and restate-worker wait on the G327 move; executor already shipped ahead of them")
    orca_pass(shell, config)
    shell.sleep(14 * 60)
    orca_pass(shell, config)
    assert escalations(shell) == []
    shell.sleep(2 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    [line] = escalations(shell)
    assert line == (
        "DECIDE hold:api-catchup merge-walker-r2: held 16 min: api, runtime-v2 and restate-worker wait on the G327 move; executor already shipped ahead of them"
        " | the root decides it now with merge-walker-r2, ahead of any open owner question on another subject"
    )


def test_a_lifted_hold_never_escalates_and_a_new_hold_restarts_the_clock(shell, config, tmp_path):
    orca_pass(shell, config)
    desk_orders(shell, "orca-desk: hold api-catchup owner=walker :: api waits on the move", "orca-desk: hold api-catchup owner=walker :: a repeat keeps the first clock")
    orca_pass(shell, config)
    shell.sleep(10 * 60)
    desk_orders(shell, "orca-desk: unhold api-catchup")
    orca_pass(shell, config)
    shell.sleep(10 * 60)
    desk_orders(shell, "orca-desk: hold api-catchup owner=walker :: held again")
    orca_pass(shell, config)
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    assert escalations(shell) == []
    shell.sleep(6 * 60)
    orca_pass(shell, config)
    [line] = escalations(shell)
    assert "DECIDE hold:api-catchup walker: held 16 min: held again" in line


def test_a_hold_outside_the_grammar_fails_visibly(shell, config, tmp_path):
    orca_pass(shell, config)
    desk_orders(shell, "R51 orca-desk: hold the api chain until the move lands", "R52 orca-desk: unhold the api chain")
    orca_pass(shell, config)
    lines = [line for line in escalations(shell)]
    assert [line.split(":", 1)[0] for line in lines] == ["HOLD-FAILED R51 inbox", "HOLD-FAILED R52 inbox"]
    assert runner_module.HOLD_GRAMMAR in lines[0] and runner_module.UNHOLD_GRAMMAR in lines[1]


LAUNCHER = "/repo/.agents/skills/orca/scripts/worker-launch.sh"


def with_sprites(config: Path) -> None:
    raw = json.loads(config.read_text())
    raw["orca"]["launch_env"] = {"ORCA_LAUNCH_RUN": "run_1"}
    raw["orca"]["sprite"] = {"launcher": LAUNCHER, "env": {"WORKER_CC_REMOTE": "/repo/tools/cc-remote/bin/cc-remote", "WORKER_REF": "dev"}}
    config.write_text(json.dumps(raw))


def sprite_launches(shell: FakeShell) -> list[list[str]]:
    return [call for call in shell.calls if call[0] == LAUNCHER]


def lane_brief(tmp_path: Path, header: str) -> Path:
    brief = tmp_path / "build.brief.md"
    brief.write_text(f"# build\n\n{header}\nDo: build it.\n")
    return brief


SPRITE_READY = json.dumps({"state": "ready", "task": "task_s", "dispatch": "ctx_s", "terminal": "term_s", "worktree": "/home/sprite/repo", "lane": LANE, "workspace": LANE, "allocation": "created"}) + "\n"
SPRITE_STARTED = json.dumps({"ok": True, "result": {"state": "ready", "taskId": "task_s", "dispatchId": "ctx_s"}})
SPRITE_UNKNOWN = json.dumps({"ok": True, "result": {"state": "outcome_unknown", "stage": "turn_start_unobserved", "taskId": "task_s", "dispatchId": "ctx_s"}})
SPRITE_KEPT = "kept terminal term_s, worktree /home/sprite/repo, and the receipt in /x.worker"
PREPARED, STARTED, START_FAILED = {"prepare.status": "0\n"}, {"worker-start.status": "0\n"}, {"worker-start.status": "1\n"}


@pytest.mark.parametrize(
    "header",
    [
        "ccx: role=build",
        "ccx: role=build cpu=high",
        "ccx: role=research place=remote",
        "ccx: place=remote role=build cpu=high",
        "ccx: role=build cpu=highest",
        "ccx: role=build place=remotely",
        "ccx: role=build place=locally",
        "ccx: role=build\nplace=local",
    ],
)
def test_an_ordinary_lane_launches_on_a_sprite_whatever_the_mac_load(shell, config, tmp_path, header):
    with_sprites(config)
    brief = lane_brief(tmp_path, header)
    shell.cpu_load = 140
    shell.sprite_line = SPRITE_READY
    shell.sprite_receipts = {**PREPARED, **STARTED, "worker-start.json": SPRITE_STARTED}
    shell.dispatches["ctx_s"] = {"status": "dispatched", "terminal": "term_s"}
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "opus", "--effort", "xhigh", "--brief", str(brief))
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert sprite_launches(shell) == [[LAUNCHER, LANE, "claude-opus-5-5", "xhigh", str(brief)]]
    assert launches(shell) == []
    assert not [call for call in shell.calls if call[0] == "sprite"]
    env = shell.spawned_env["worker-launch.sh"]
    assert (env["WORKER_RUN"], env["WORKER_REF"]) == ("run_1", "dev")
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "verified"
    assert json.loads((shell.receipts / f"{LANE}.json").read_text())["result"]["dispatchId"] == "ctx_s"
    assert (shell.receipts / f"{LANE}.terminal").read_text() == "term_s\n"


def sprite_passes(shell: FakeShell, config: Path, tmp_path: Path, restarted: bool) -> None:
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    for _ in range(3):
        if restarted:
            orca_pass(shell, config)
        else:
            assert runner_module.run_orca(runner, True) == 0


NOTHING_PREPARED = ("no cc-remote command at /repo/tools/cc-remote/bin/cc-remote; set WORKER_CC_REMOTE to cc-remote 0.26.0 or newer; nothing was prepared", {})
PREPARE_UNRECORDED = ("jq: error: cannot write request.json", {"request.json": "{}"})
PREPARE_FAILED = ("cc-remote orca prepare exited 1; /x.worker keeps its output, and nothing was attached or retried", {"prepare.status": "1\n"})
PROVIDER_REFUSED = (
    PREPARE_FAILED[0],
    {
        "prepare.status": "1\n",
        "prepare.err": f"Error: sprite {LANE}: whether the create allocated it is unknown: max sprites per org exceeded; {LANE} was allocated as {LANE} but not provisioned, so its record is kept: run destroy {LANE} once the provider answers\n",
    },
)
OUTCOME_UNKNOWN = (
    f"orca orchestration worker-start exited 1; {SPRITE_KEPT}",
    {**PREPARED, **START_FAILED, "worker-start.json": SPRITE_UNKNOWN},
)


@pytest.mark.parametrize(("name", "expected"), [("build.brief.md", "build.brief.worker"), ("brief", "brief.worker"), (".brief", ".worker")])
def test_a_sprite_attempt_is_the_directory_worker_launch_names(tmp_path, name, expected):
    assert runner_module.sprite_attempt(tmp_path / name) == tmp_path / expected


@pytest.mark.parametrize(
    ("printed", "receipts", "restarted"),
    [
        (*NOTHING_PREPARED, False),
        (*PREPARE_UNRECORDED, False),
        (*PREPARE_FAILED, False),
        (*PREPARE_FAILED, True),
        (*PROVIDER_REFUSED, False),
        (*PROVIDER_REFUSED, True),
    ],
    ids=["nothing-prepared", "prepare-unrecorded", "prepare-failed", "prepare-failed-restarted", "provider-refused", "provider-refused-restarted"],
)
def test_a_sprite_launch_that_stopped_before_worker_start_fails_and_never_launches_locally(shell, config, tmp_path, printed, receipts, restarted):
    with_sprites(config)
    brief = lane_brief(tmp_path, "ccx: role=build")
    attempt = tmp_path / "build.brief.worker"
    shell.sprite_line = f"{printed}\n"
    shell.sprite_receipts = receipts
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "sol", "--effort", "xhigh", "--brief", str(brief))
    sprite_passes(shell, config, tmp_path, restarted)
    assert sprite_launches(shell) == [[LAUNCHER, LANE, "gpt-6.1-sol", "xhigh", str(brief)]]
    assert launches(shell) == []
    assert escalations(shell) == [
        f"LAUNCH-FAILED desk-lane-{LANE}/R638 {LANE}: R638 refused on a Sprite that never started a worker: {printed}; "
        f"nothing was launched, and its receipts are in {attempt}; the root submits it again under a new key"
    ]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "failed"


@pytest.mark.parametrize(
    ("printed", "receipts", "named", "restarted"),
    [
        (*OUTCOME_UNKNOWN, " as task=task_s dispatch=ctx_s", False),
        (*OUTCOME_UNKNOWN, " as task=task_s dispatch=ctx_s", True),
        (f"orca orchestration worker-start exited 143; {SPRITE_KEPT}", {**PREPARED, "worker-start.status": "143\n", "worker-start.json": '{"ok": true, "result": {"taskId": "task_s"'}, "", False),
        (
            f"orca orchestration worker-start exited 1: invalid_argument: sk-synthetic-credential-0000; {SPRITE_KEPT}",
            {**PREPARED, **START_FAILED, "worker-start.json": json.dumps({"ok": False, "error": {"code": "invalid_argument", "message": "sk-synthetic-credential-0000"}})},
            "",
            False,
        ),
        (
            f"orca orchestration worker-start exited 0 without a ready receipt for Run run_1; {SPRITE_KEPT}",
            {**PREPARED, **STARTED, "worker-start.json": json.dumps({"ok": True, "result": {"state": "ready", "taskId": "task s\nUNOWNED", "dispatchId": "ctx_s"}})},
            " as dispatch=ctx_s",
            False,
        ),
        (json.dumps({"state": "ready", "task": "task_s", "dispatch": "ctx_s"}), {**PREPARED, **STARTED, "worker-start.json": SPRITE_STARTED}, " as task=task_s dispatch=ctx_s", False),
        ("/x.worker/allocation.json holds prepared is not true, so the prepared worker stays idle and unattached", PREPARED, "", False),
        (*NOTHING_PREPARED, "", True),
        (*PREPARE_UNRECORDED, "", True),
    ],
    ids=[
        "outcome-unknown",
        "outcome-unknown-restarted",
        "lost-response",
        "error-envelope",
        "unprintable-task",
        "malformed-ready-line",
        "prepared-unattached",
        "nothing-prepared-restarted",
        "prepare-unrecorded-restarted",
    ],
)
def test_a_sprite_launch_that_may_have_reached_worker_start_is_unverifiable_and_never_launches_locally(shell, config, tmp_path, printed, receipts, named, restarted):
    with_sprites(config)
    brief = lane_brief(tmp_path, "ccx: role=build")
    attempt = tmp_path / "build.brief.worker"
    shell.sprite_line = f"{printed}\n"
    shell.sprite_receipts = receipts
    shell.launch_line = f"{LANE} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "sol", "--effort", "xhigh", "--brief", str(brief))
    sprite_passes(shell, config, tmp_path, restarted)
    assert sprite_launches(shell) == [[LAUNCHER, LANE, "gpt-6.1-sol", "xhigh", str(brief)]]
    assert launches(shell) == []
    assert escalations(shell) == [
        f"SPRITE-UNVERIFIABLE desk-lane-{LANE}/R638:sprite {LANE}: R638 does not launch locally: its Sprite launch is unverified and a remote worker may be running{named}; "
        f"receipts in {attempt}, nothing retried or cleaned up: {printed}"
    ]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "unverifiable"
    assert {path.name: path.read_text() for path in attempt.iterdir()} == receipts if receipts else not attempt.exists()
    assert not (shell.receipts / f"{LANE}.json").exists()
    assert not FORBIDDEN & {word for call in shell.calls for word in call}


def test_a_sprite_launch_that_prints_nothing_is_unverifiable_and_never_relaunched(shell, config, tmp_path):
    with_sprites(config)
    brief = lane_brief(tmp_path, "ccx: role=build")
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "sol", "--effort", "xhigh", "--brief", str(brief))
    for _ in range(3):
        orca_pass(shell, config)
    assert sprite_launches(shell) == [[LAUNCHER, LANE, "gpt-6.1-sol", "xhigh", str(brief)]]
    assert launches(shell) == []
    assert escalations(shell) == [f"UNVERIFIABLE desk-lane-{LANE}/R638 {LANE}: launch outcome unknown: no launch line and no new receipt; it was not relaunched"]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "unverifiable"


@pytest.mark.parametrize("header", ["ccx: role=build", "ccx: role=research place=remote"])
def test_a_lane_name_a_sprite_refuses_fails_and_never_launches_locally(shell, config, tmp_path, header):
    with_sprites(config)
    brief = lane_brief(tmp_path, header)
    lane = "api.build"
    cli(shell, config, "launch", "--key", "R638", "--lane", lane, "--model", "opus", "--effort", "xhigh", "--brief", str(brief))
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert sprite_launches(shell) == [] and launches(shell) == []
    assert escalations(shell) == [
        f"LAUNCH-FAILED desk-lane-{lane}/R638 {lane}: R638 refused on lane {lane}, not a Sprite name of up to 55 lowercase letters, digits, and dashes; "
        "nothing was launched, and the root submits it again under a new key once that is fixed"
    ]
    assert incident(tmp_path, f"desk-lane-{lane}").actions["R638"].status == "failed"


@pytest.mark.parametrize("flags", [(), ("--owner-directed",)])
@pytest.mark.parametrize("header", ["ccx: role=build", "ccx: role=build cpu=high", "ccx: role=research place=remote"])
def test_an_ordinary_lane_with_no_sprite_config_fails_at_once_and_never_launches_locally(shell, config, tmp_path, header, flags):
    brief = lane_brief(tmp_path, header)
    shell.cpu_load = 140
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "opus", "--effort", "xhigh", "--brief", str(brief), *flags)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert sprite_launches(shell) == [] and launches(shell) == []
    assert [line for line in escalations(shell) if not line.startswith("LOAD-HIGH ")] == [
        f"LAUNCH-FAILED desk-lane-{LANE}/R638 {LANE}: R638 refused on a runner config with no orca.sprite; nothing was launched, and the root submits it again under a new key once that is fixed"
    ]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "failed"


@pytest.mark.parametrize("sprites", [True, False])
@pytest.mark.parametrize(
    ("header", "model"),
    [
        ("ccx: role=build place=local", "sol"),
        ("ccx: role=build cpu=high place=local", "opus"),
        ("ccx: role=build cpu=high", "fable"),
        ("ccx: role=build place=remote", "fable"),
        ("ccx: role=build", "claude-fable-5-1"),
        ("ccx: role=build place=remote", "claude-fable-5-1"),
        ("ccx: role=fix cpu=high", "incident"),
        ("ccx: role=fix place=remote", "incident"),
        ("ccx: role=desk place=remote", "opus"),
        ("ccx: role=watch cpu=high place=remote", "sonnet"),
    ],
)
def test_a_lane_placed_or_forced_local_never_leaves_the_mac(shell, config, tmp_path, header, model, sprites):
    if sprites:
        with_sprites(config)
    brief = lane_brief(tmp_path, header)
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", model, "--effort", "xhigh", "--brief", str(brief), "--owner-directed")
    orca_pass(shell, config)
    assert sprite_launches(shell) == []
    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, model, "xhigh", str(brief)]]
    assert not [call for call in shell.calls if call[0] == "sprite"]


@pytest.mark.parametrize("header", ["ccx: role=build place=local", "ccx: role=build cpu=high place=local"])
def test_a_local_launch_beside_a_sprite_config_still_waits_on_load(shell, config, tmp_path, header):
    with_sprites(config)
    brief = lane_brief(tmp_path, header)
    shell.cpu_load = 40
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "opus", "--effort", "xhigh", "--brief", str(brief))
    orca_pass(shell, config)
    assert launches(shell) == [] and sprite_launches(shell) == []
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "accepted"


SILENT_LANE = "inc-liam-load-opus"


def silent_lines(shell: FakeShell, lane: str = SILENT_LANE) -> list[str]:
    return [line for line in escalations(shell) if line.startswith(f"PR-SILENT {lane} ")]


def lane_worktree(shell: FakeShell, path: Path, status: str = "## HEAD (no branch)\n", committed: timedelta = timedelta(hours=2), listed: bool = True, ahead: int = 0) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    shell.worktrees[str(path)] = {"status": status, "log": f"{int((shell.clock - committed).timestamp())} 1a2b3c4 dev: bump the sidecar\n", "rev-list": f"{ahead}\n", "rev-parse": "/repo/.git\n1a2b3c4d\n", "ls-remote": ""}
    if listed:
        branch = status.splitlines()[0].removeprefix("## ").split("...")[0]
        shell.listed.append((str(path), "" if branch.startswith("HEAD ") else branch))
    return path


def test_an_incident_lanes_mechanism_with_no_pr_or_blocker_escalates_pr_silent_with_its_worktree_every_five_minutes(shell, config, tmp_path):
    orca_pass(shell, config)
    worktree = lane_worktree(shell, tmp_path / ".claude/worktrees/monorepo" / SILENT_LANE)
    mechanism = cci_record(shell, lane=SILENT_LANE, kind="mechanism", text="liam-load NOT retention: SAND-59 is a fork", at=actions.stamp(shell.clock), to=["root"])
    shell.sleep(9 * 60)
    orca_pass(shell, config)
    assert silent_lines(shell) == []
    shell.sleep(60)
    orca_pass(shell, config)
    assert silent_lines(shell) == [
        f"PR-SILENT {SILENT_LANE} #{mechanism}: MECHANISM at 11:00, 10 min with no OPENED or BLOCKER record; monorepo: {worktree} (git worktree list): HEAD (no branch), 0 dirty files, 0 ahead of trunk, last commit 130 min ago 1a2b3c4 dev: bump the sidecar, not pushed, no PR | {SILENT_LANE}: commit and push the fix, open its PR with the submit skill and post OPENED, or post BLOCKER"
    ]
    assert [(post["kind"], post["topic"]) for post in shell.posts if post["text"].startswith("PR-SILENT ")] == [("blocker", SILENT_LANE)]
    shell.sleep(4 * 60)
    orca_pass(shell, config)
    assert len(silent_lines(shell)) == 1
    shell.sleep(60)
    orca_pass(shell, config)
    assert [line.split(";", 1)[0] for line in silent_lines(shell)][1] == f"PR-SILENT {SILENT_LANE} #{mechanism}: MECHANISM at 11:00, 15 min with no OPENED or BLOCKER record"
    lane_record(shell, SILENT_LANE, "opened", pr=["Forge-AI/monorepo#41461"])
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    assert len(silent_lines(shell)) == 2


def test_show_lists_each_lane_whose_mechanism_waits_with_its_lane_status_command(shell, config, tmp_path, capsys):
    orca_pass(shell, config)
    mechanism = cci_record(shell, lane=SILENT_LANE, kind="mechanism", text="the fork reads the parent", at=actions.stamp(shell.clock))
    shell.sleep(4 * 60)
    orca_pass(shell, config)
    capsys.readouterr()
    cli(shell, config, "show")
    assert f"pr-silent {SILENT_LANE}: MECHANISM #{mechanism} 4 min ago, no OPENED or BLOCKER; code state: `drive.py lane-status {SILENT_LANE}`\n" in capsys.readouterr().out


def test_a_dirty_worktree_names_its_branch_and_files_and_a_missing_one_says_so(shell, config, tmp_path):
    orca_pass(shell, config)
    worktree = lane_worktree(shell, tmp_path / ".claude/worktrees/monorepo" / SILENT_LANE, status="## inc-liam-fork-parent-grant\n M rust/crates/sandsql-server/src/chain.rs\n?? rust/crates/sandsql-server/src/grant.rs\n", committed=timedelta(minutes=3), listed=False, ahead=1)
    cci_record(shell, lane=SILENT_LANE, kind="state", text=f"WORKTREE {SILENT_LANE} {worktree} inc-liam-fork-parent-grant", at=actions.stamp(shell.clock), fields={"attrs": {"worktree": str(worktree)}})
    cci_record(shell, lane=SILENT_LANE, kind="report", text="CANDIDATE chain.rs copies through the parent grant", at=actions.stamp(shell.clock))
    cci_record(shell, lane="inc-ghost-fix", kind="mechanism", text="the ghost fails", at=actions.stamp(shell.clock))
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    [line] = silent_lines(shell)
    assert f"; monorepo: {worktree} (cci record): inc-liam-fork-parent-grant, 2 dirty files: rust/crates/sandsql-server/src/chain.rs, rust/crates/sandsql-server/src/grant.rs, 1 ahead of trunk, last commit 13 min ago 1a2b3c4 dev: bump the sidecar, no origin branch, no PR |" in line
    assert line.startswith(f"PR-SILENT {SILENT_LANE} #") and ": CANDIDATE at 11:00, 10 min" in line
    [ghost] = silent_lines(shell, "inc-ghost-fix")
    assert "; no worktree found for lane inc-ghost-fix |" in ghost


@pytest.mark.parametrize(
    "ending",
    ["blocker", "blocked", "opened-by-sibling", "ready", "not-ours", "root-stand-down", "root-stopped", "text-blocker"],
)
def test_a_blocker_a_sibling_pr_or_an_end_of_the_lane_stops_the_pr_silent_clock(shell, config, tmp_path, ending):
    orca_pass(shell, config)
    cci_record(shell, lane=SILENT_LANE, kind="mechanism", text="the fork reads the parent", at=actions.stamp(shell.clock))
    shell.sleep(4 * 60)
    if ending == "opened-by-sibling":
        lane_record(shell, "inc-liam-load-fix", "opened")
    elif ending.startswith("root-"):
        cci_record(shell, lane="root", kind=ending.removeprefix("root-"), text="stand down", at=actions.stamp(shell.clock), to=[SILENT_LANE])
    elif ending == "text-blocker":
        lane_record(shell, SILENT_LANE, "state", text="BLOCKER the grant needs an owner ruling")
    else:
        lane_record(shell, SILENT_LANE, ending)
    shell.sleep(20 * 60)
    orca_pass(shell, config)
    assert silent_lines(shell) == []


def test_a_root_resolve_of_a_pr_silent_ends_its_repeats(shell, config, tmp_path):
    orca_pass(shell, config)
    cci_record(shell, lane=SILENT_LANE, kind="mechanism", text="the fork reads the parent", at=actions.stamp(shell.clock))
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    [post] = [post for post in shell.posts if post["text"].startswith("PR-SILENT ")]
    cci_record(shell, lane="root", kind="answer", text="known; it is writing", at=actions.stamp(shell.clock), resolves=shell.posts.index(post) + 1)
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    assert len(silent_lines(shell)) == 1


@pytest.mark.parametrize("lane", ["enum-local-client", "diag-env-not-found", "inc-fleet-evidence", "incident-closeout"])
def test_a_mechanism_from_a_lane_that_is_no_incident_fix_lane_starts_no_pr_silent_clock(shell, config, tmp_path, lane):
    orca_pass(shell, config)
    cci_record(shell, lane=lane, kind="mechanism", text="the set is proven", at=actions.stamp(shell.clock))
    shell.sleep(20 * 60)
    orca_pass(shell, config)
    assert [line for line in escalations(shell) if line.startswith("PR-SILENT ")] == []


def test_a_clocked_orca_fix_lane_reports_the_worktree_its_launch_receipt_names_and_is_never_deferred(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    worktree = lane_worktree(shell, tmp_path / "orca" / f"v3-{LANE}", listed=False)
    (shell.receipts / f"{LANE}.worktree").write_text(f"{worktree}\n")
    shell.sleep(2 * 60)
    lane_record(shell, LANE, "mechanism")
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    [line] = silent_lines(shell, LANE)
    assert f"; orca: {worktree} (launch receipt): HEAD (no branch), 0 dirty files," in line
    assert not [post for post in shell.posts if post["text"].startswith("DEFERRED ")]


def test_a_mechanism_older_than_the_runners_first_read_window_starts_no_clock(shell, config, tmp_path):
    cci_record(shell, lane=SILENT_LANE, kind="mechanism", text="the fork reads the parent", at=actions.stamp(shell.clock - timedelta(hours=3)))
    orca_pass(shell, config)
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    assert silent_lines(shell) == []


def test_an_incident_lane_named_for_no_fix_starts_its_clock_once_it_owns_a_worktree(shell, config, tmp_path):
    orca_pass(shell, config)
    cci_record(shell, lane="inc-mac-cpu-spin", kind="mechanism", text="the sidecar spins on a closed socket", at=actions.stamp(shell.clock))
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    assert silent_lines(shell, "inc-mac-cpu-spin") == []
    worktree = lane_worktree(shell, tmp_path / ".claude/worktrees/monorepo/inc-mac-cpu-spin")
    shell.sleep(5 * 60)
    orca_pass(shell, config)
    [line] = silent_lines(shell, "inc-mac-cpu-spin")
    assert f"; monorepo: {worktree} (git worktree list): HEAD (no branch)," in line


def silent_posts(shell: FakeShell) -> list[str]:
    return [line for line in escalations(shell) if line.startswith("PR-SILENT ")]


@pytest.mark.parametrize(
    "answer",
    [
        {"lane": "inc-sand3k-fix", "kind": "opened", "text": "SAND-3K fix PR #41471 at 40cc7d1d277: blank-dir check moved below the local restore"},
        {"lane": "inc-malformed-row-5a", "kind": "opened", "text": "OPENED pr#41468 [SAND-3K]: prepareLocalThread passes ErrMalformed", "topic": "SAND-3K"},
        {"lane": "inc-other-fix", "kind": "head", "text": "HEAD pr#41471 at 40cc7d1", "re": "MECHANISM"},
        {"lane": "inc-other-fix", "kind": "report", "text": "OPENED pr#41471 sand3k: the attach path keeps the typed refusal", "topic": "SAND3K"},
        {"lane": "root", "kind": "go", "text": "GO inc-sand3k-fix: SAND-3K fix ships from inc-sand3k-fix", "to": ["inc-sand3k-fix"]},
    ],
)
def test_a_pr_another_lane_opens_for_the_same_incident_settles_the_mechanism(shell, config, tmp_path, answer):
    orca_pass(shell, config)
    lane_worktree(shell, tmp_path / ".claude/worktrees/monorepo/inc-chats-load-fix-2")
    mechanism = cci_record(shell, lane="inc-chats-load-fix-2", kind="mechanism", text="SAND-3K partial mechanism: live 500 open loop on 1546", at=actions.stamp(shell.clock))
    shell.sleep(3 * 60)
    cci_record(shell, at=actions.stamp(shell.clock), **{**answer, **({"re": mechanism} if answer.get("re") == "MECHANISM" else {})})
    shell.sleep(20 * 60)
    orca_pass(shell, config)
    assert silent_posts(shell) == []


def test_a_pr_opened_for_the_incident_before_the_mechanism_settles_it_and_one_for_another_incident_does_not(shell, config, tmp_path):
    orca_pass(shell, config)
    lane_worktree(shell, tmp_path / ".claude/worktrees/monorepo/inc-malformed-open-fix")
    lane_worktree(shell, tmp_path / ".claude/worktrees/monorepo/inc-sync-fix")
    cci_record(shell, lane="inc-malformed-row-5a", kind="opened", text="OPENED pr#41468 head 85937a4 [SAND-5A]: prepareLocalThread", topic="SAND-5A", at=actions.stamp(shell.clock))
    shell.sleep(60)
    cci_record(shell, lane="inc-malformed-open-fix", kind="mechanism", text="SAND5A exact 50566ea9 at 21:25: malformed_row", at=actions.stamp(shell.clock))
    cci_record(shell, lane="inc-sync-fix", kind="mechanism", text="SAND-3Y the sync toast reads a stale cursor", at=actions.stamp(shell.clock))
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    assert [line.split(" #", 1)[0] for line in silent_posts(shell)] == ["PR-SILENT inc-sync-fix"]


@pytest.mark.parametrize("brief", ["briefs/{lane}.md", "{lane}/brief.md"])
@pytest.mark.parametrize("role", ["ccx: lane={lane} role=proof\n\n# {lane}\n", "# {lane} (codex sol xhigh, read-only audit): prove every incident class is done\n"])
def test_a_lane_whose_brief_is_evidence_proof_closeout_desk_or_audit_is_never_pr_silent(shell, config, tmp_path, brief, role):
    lane = "inc-chats-load-fix-2"
    path = tmp_path / brief.format(lane=lane)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(role.format(lane=lane) + "Prove the fix on the live version.\n")
    orca_pass(shell, config)
    lane_worktree(shell, tmp_path / ".claude/worktrees/monorepo" / lane)
    cci_record(shell, lane=lane, kind="mechanism", text="SAND-3K partial mechanism", at=actions.stamp(shell.clock))
    shell.sleep(20 * 60)
    orca_pass(shell, config)
    assert silent_posts(shell) == []


@pytest.mark.parametrize("lane", ["inc-sand3k-proof", "inc-mount-fault-closeout", "inc-alerts-desk-2"])
def test_a_lane_named_for_evidence_proof_closeout_or_desk_is_never_pr_silent(shell, config, tmp_path, lane):
    orca_pass(shell, config)
    lane_worktree(shell, tmp_path / ".claude/worktrees/monorepo" / lane)
    cci_record(shell, lane=lane, kind="mechanism", text="SAND-3K partial mechanism", at=actions.stamp(shell.clock))
    shell.sleep(20 * 60)
    orca_pass(shell, config)
    assert silent_posts(shell) == []


def test_a_fix_brief_whose_title_mentions_a_desk_still_clocks_its_lane(shell, config, tmp_path):
    lane = "inc-landing-crash-fix"
    (tmp_path / "briefs").mkdir(exist_ok=True)
    (tmp_path / "briefs" / f"{lane}.md").write_text(f"# {lane}: fix the landing desk crash on a held PR\n")
    orca_pass(shell, config)
    lane_worktree(shell, tmp_path / ".claude/worktrees/monorepo" / lane)
    cci_record(shell, lane=lane, kind="mechanism", text="the landing desk reads a stale hold", at=actions.stamp(shell.clock))
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    assert [line.split(" #", 1)[0] for line in silent_posts(shell)] == [f"PR-SILENT {lane}"]


@pytest.mark.parametrize(
    "ruling",
    [
        {"lane": "root", "kind": "decide", "text": "CLEAR 333417098, NOT counted: a 13 s burst of OpenAI 499 client-disconnects", "to": ["inc-llmgw-fix", "alerts-desk"], "re": "MECHANISM"},
        {"lane": "root", "kind": "decision", "text": "CLEAR, no code for this alert", "to": ["inc-llmgw-fix"]},
        {"lane": "inc-llmgw-fix", "kind": "done", "text": "CLEAR / NOT counted per the root's ruling; git diff clean"},
        {"lane": "inc-llmgw-fix", "kind": "state", "text": "STOPPED: root ruled no code"},
    ],
)
def test_a_root_ruling_to_the_lane_or_its_own_done_answers_its_mechanism(shell, config, tmp_path, ruling):
    orca_pass(shell, config)
    mechanism = cci_record(shell, lane="inc-llmgw-fix", kind="mechanism", text="333417098 fired on 14 OpenAI 499 client-disconnects", at=actions.stamp(shell.clock))
    shell.sleep(3 * 60)
    cci_record(shell, at=actions.stamp(shell.clock), **{**ruling, **({"re": mechanism} if ruling.get("re") == "MECHANISM" else {})})
    shell.sleep(20 * 60)
    orca_pass(shell, config)
    assert silent_posts(shell) == []


def test_a_root_go_ordering_the_lane_to_work_leaves_its_mechanism_owed(shell, config, tmp_path):
    orca_pass(shell, config)
    cci_record(shell, lane="inc-llmgw-fix", kind="mechanism", text="333417098 fired on a gateway 5xx run", at=actions.stamp(shell.clock))
    shell.sleep(3 * 60)
    cci_record(shell, lane="root", kind="go", text="GO open the PR now", to=["inc-llmgw-fix"], at=actions.stamp(shell.clock))
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    assert [line.split(" #", 1)[0] for line in silent_posts(shell)] == ["PR-SILENT inc-llmgw-fix"]


def test_the_landing_desk_redraws_the_drive_root_card_from_the_ledger_only_when_it_changes(shell, config, tmp_path):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    landing = runner_module.Landing(runner, runner.config.landing)
    rows = {row["pr"]: row for row in stack_rows(**{"28997": "landed"})}
    rows["29020"]["hold_reason"] = "waiting on the owner"
    landing.draw_card(rows)
    landing.draw_card(rows)
    assert len(shell.cards) == 1
    card = shell.cards[0]
    assert card["drive"] == "d1" and card["refs"] == []
    assert card["progress"] == {"step": 1, "total": 3, "label": "PRs landed"}
    assert card["rollup"]["prs"] == [
        {"url": "https://github.com/Forge-AI/monorepo/pull/29016", "label": "#29016 artifact-retry", "status": "open"},
        {"url": "https://github.com/Forge-AI/monorepo/pull/29020", "label": f"#29020 {LANE}", "status": "held"},
    ]
    assert {lane["name"]: (lane["status"], lane.get("detail")) for lane in card["rollup"]["lanes"]} == {"artifact-retry": ("1 open PR", "#29016"), LANE: ("1 open PR", "#29020")}
    assert {fact["label"]: fact["value"] for fact in card["facts"]} == {"Open PRs": "2", "Queued": "0", "Held": "1", "Landed": "1"}
    rows["29016"] = dict(rows["29016"], state="landed")
    shell.card_error = "cc-slack: dm-status root_only needs this session's status thread; post a card first"
    landing.draw_card(rows)
    landing.draw_card(rows)
    runner.flush()
    assert [card["progress"]["step"] for card in shell.cards] == [1, 2, 2]
    failed = [line for line in escalations(shell) if "CARD-FAILED" in line]
    assert len(failed) == 1 and "post a card first" in failed[0]


def test_the_drive_root_card_stays_inside_cc_slacks_limits_on_a_large_ledger(shell, config, tmp_path):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    landing = runner_module.Landing(runner, runner.config.landing)
    rows = {str(10_000_000 + n): {"pr": str(10_000_000 + n), "lane": "bulk", "head": f"{n:010x}", "state": "open" if n < 60 else "landed"} for n in range(1500)}
    card = landing.card(rows)
    assert card["progress"] == {"step": 960, "total": 1000, "label": "PRs landed"}
    assert len(card["rollup"]["prs"]) == 50
    detail = card["rollup"]["lanes"][0]["detail"]
    assert len(detail) <= 200 and detail.endswith("more")


BUILD = "https://buildkite.com/forge/release/builds/8748"


def with_applying(config: Path) -> None:
    raw = json.loads(config.read_text())
    raw["landing"]["applying"] = {"build": "tools/ci bk api /pipelines/{pipeline}/builds/{number}", "step": r"apply-(?P<component>.+?)--(?P<env>.+)", "components": ["sandsql", "api", "executor", "rsw-*"]}
    config.write_text(json.dumps(raw))


def release_started(shell: FakeShell, lane: str = WALKER) -> int:
    return cci_record(shell, lane=lane, kind="release", refs={"builds": [BUILD], "targets": ["api"]}, at=actions.stamp(shell.now()), text="CUT releases/2026-10-01/3 build 8748")


def apply_job(job: str, key: str, state: str) -> dict:
    return {"id": job, "step_key": key, "state": state}


def applying_posts(shell: FakeShell) -> list[dict]:
    return [post for post in shell.posts if post["kind"] == "release" and post["lane"] == "desk-runner"]


def test_a_lanes_release_build_is_claimed_for_the_lane_and_recorded_in_the_registry(shell, config, tmp_path):
    orca_pass(shell, config)
    release_started(shell)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert shell.claims == [["--for", f"{WALKER}@d1", "--build", BUILD, "--rel", "started"]]
    assert shell.registered == [["--cci-drive", "d1", "--owner", f"{WALKER}@d1", "--build", BUILD, "--release", "releases/2026-10-01/3"]]
    assert [line for line in escalations(shell) if "CLAIM" in line] == []


def test_a_release_record_from_before_the_runner_started_is_never_claimed(shell, config, tmp_path):
    release_started(shell)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert shell.claims == []


def test_a_refused_claim_escalates_once(shell, config, tmp_path):
    shell.claim_error = f"cci: --for {WALKER}@d1: no live session on this machine is bound as lane {WALKER} in drive d1"
    orca_pass(shell, config)
    release_started(shell)
    orca_pass(shell, config)
    orca_pass(shell, config)
    [line] = [line for line in escalations(shell) if line.startswith("RELEASE-CLAIM-FAILED ")]
    assert f"build {BUILD} started by {WALKER}@d1: `cci claim --for {WALKER}@d1 --build {BUILD} --rel started` exited 1: cci: --for" in line


def test_a_running_apply_is_announced_once_to_every_live_drive_root(shell, config, tmp_path):
    with_applying(config)
    shell.root_drives = ["d1", "d2", "d3"]
    shell.builds["8748"] = {"commit": "94d61b91951aa", "jobs": [apply_job("job_plan", "plan-api--prod", "running"), apply_job("job_web", "apply-web--prod", "running"), apply_job("job_api", "apply-api--prod", "scheduled")], "finished_at": None}
    orca_pass(shell, config)
    release_started(shell)
    orca_pass(shell, config)
    assert applying_posts(shell) == []
    shell.builds["8748"]["jobs"][2]["state"] = "running"
    orca_pass(shell, config)
    orca_pass(shell, config)
    [post] = applying_posts(shell)
    assert post["text"] == "applying api/prod 94d61b91 (releases/2026-10-01/3)"
    assert post["to"] == "root,root@d2,root@d3" and post["id"] == "applying:job_api" and post["stack"] == "api/prod" and post["build"] == BUILD


def test_a_build_reader_that_prints_no_json_escalates_and_the_desk_keeps_running(shell, config, tmp_path):
    with_applying(config)
    shell.builds["8748"] = "not json"
    orca_pass(shell, config)
    release_started(shell)
    orca_pass(shell, config)
    orca_pass(shell, config)
    [line] = [line for line in escalations(shell) if line.startswith("APPLYING-UNREAD ")]
    assert "printed no JSON" in line


def test_a_finished_build_is_read_no_more(shell, config, tmp_path):
    with_applying(config)
    shell.builds["8748"] = {"commit": "94d61b91951aa", "jobs": [], "finished_at": "2026-10-01T18:30:00Z"}
    orca_pass(shell, config)
    release_started(shell)
    orca_pass(shell, config)
    orca_pass(shell, config)
    reads = [call for call in shell.calls if call[1:3] == ["bk", "api"]]
    assert len(reads) == 1


def test_an_applying_config_without_named_groups_refuses_to_start(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    raw["landing"]["applying"] = {"build": "tools/ci bk api /builds/{number}", "step": "apply-.+", "components": ["api"]}
    config.write_text(json.dumps(raw))
    assert cli(shell, config, "run", "--desk", "orca", "--once") == 3
    [line] = escalations(shell)
    assert line.startswith("APPLYING-CONFIG-INVALID ") and "no `component` and `env` named groups" in line


def frozen(shell: FakeShell, until: datetime, lane: str = WALKER) -> int:
    return cci_record(shell, lane=lane, kind="hold", status="open", refs={"targets": ["api"]}, at=actions.stamp(shell.now()), text=f"froze api/prod until {actions.stamp(until)} as freeze frz_77: canary reads back")


def lift_asks(shell: FakeShell) -> list[tuple[str, str]]:
    return [(post["to"], post["text"]) for post in shell.posts if post["kind"] == "ask"]


def test_a_freeze_past_its_until_gets_one_lift_ask_to_its_lane(shell, config, tmp_path):
    orca_pass(shell, config)
    frozen(shell, shell.clock + timedelta(minutes=30))
    orca_pass(shell, config)
    assert lift_asks(shell) == []
    shell.clock += timedelta(minutes=31)
    orca_pass(shell, config)
    shell.clock += timedelta(minutes=1)
    orca_pass(shell, config)
    [(to, text)] = lift_asks(shell)
    assert to == WALKER and text.startswith("lift freeze frz_77 on api/prod: it ran to its until")


def test_a_freeze_whose_lane_is_gone_gets_one_lift_ask_to_root(shell, config, tmp_path):
    orca_pass(shell, config)
    frozen(shell, shell.clock + timedelta(hours=4))
    shell.gone.add(WALKER)
    orca_pass(shell, config)
    shell.clock += timedelta(minutes=1)
    orca_pass(shell, config)
    [(to, text)] = lift_asks(shell)
    assert to == "root" and "lane " + WALKER + ", its owner, has no live session; run `tools/ci release unfreeze frz_77`" in text


def test_a_lifted_freeze_is_asked_about_no_more(shell, config, tmp_path):
    orca_pass(shell, config)
    seq = frozen(shell, shell.clock + timedelta(minutes=5))
    orca_pass(shell, config)
    next(record for record in shell.records if record["seq"] == seq)["status"] = "resolved"
    shell.clock += timedelta(minutes=10)
    orca_pass(shell, config)
    assert lift_asks(shell) == []
    assert runner_module.FREEZES not in incident(tmp_path, "desk-runner").facts or incident(tmp_path, "desk-runner").facts[runner_module.FREEZES] == {}


TRUNK_CHECK = "buildkite/test"
OPEN_ROWS = {"29020": {"state": "open", "head": "cccc333333"}, "29021": {"state": "open", "head": "dddd444444"}}


def trunk_commit(sha: str, state: str | None, pr: str, landed: str) -> dict:
    contexts = [{"context": TRUNK_CHECK, "state": state, "targetUrl": f"https://buildkite.com/forge/test/builds/{pr}"}] if state else []
    return {"oid": sha * 10, "committedDate": landed, "message": f"api: 🐛 a change (#{pr})\n\nbody (#1)", "status": {"contexts": contexts}}


def trunk_watch(shell: FakeShell, config: Path, tmp_path: Path) -> runner_module.Landing:
    loaded = json.loads(config.read_text())
    loaded["landing"]["trunk_check"] = TRUNK_CHECK
    config.write_text(json.dumps(loaded))
    return landing_of(shell, config, tmp_path)


def trunk_posts(shell: FakeShell) -> list[tuple[str, str, str]]:
    return [(post["kind"], post["to"], post["text"]) for post in shell.posts if post["topic"] == "dev-red"]


def bus_trunk(shell: FakeShell, lane: str, kind: str, text: str, pr: str = "") -> None:
    refs = {"prs": [f"Forge-AI/monorepo#{pr}"]} if pr else {}
    shell.records.append({"seq": 9000 + len(shell.records), "drive": "d1", "lane": lane, "kind": kind, "topic": "dev-red", "to": ["root"], "refs": refs, "text": text, "at": actions.stamp(shell.clock)})


@pytest.mark.parametrize(
    ("history", "since", "pr"),
    [
        ([("b", "FAILURE", "29019"), ("a", "SUCCESS", "29018")], "b", "29019"),
        ([("d", None, "29023"), ("c", "PENDING", "29022"), ("b", "ERROR", "29019"), ("e", "FAILURE", "29017"), ("a", "SUCCESS", "29016")], "e", "29017"),
        ([("b", "PENDING", "29019"), ("a", "SUCCESS", "29018"), ("e", "FAILURE", "29017")], None, None),
    ],
)
def test_the_trunk_red_run_starts_at_its_oldest_failed_commit_and_skips_unfinished_ones(history, since, pr):
    red = runner_module.trunk_red([trunk_commit(sha, state, number, "2026-10-01T17:50:00Z") for sha, state, number in history], TRUNK_CHECK)
    assert (red.since[0], red.pr) == (since, pr) if since else red is None


def test_a_trunk_whose_check_never_finished_reads_as_unknown_not_green():
    assert runner_module.trunk_red([trunk_commit("b", "PENDING", "29019", "2026-10-01T17:50:00Z"), trunk_commit("a", None, "29018", "2026-10-01T17:40:00Z")], TRUNK_CHECK) is False


def test_a_red_trunk_posts_dev_red_every_half_hour_naming_its_fix_once_named(shell, config, tmp_path):
    landing = trunk_watch(shell, config, tmp_path)
    shell.trunk = [trunk_commit("b", "FAILURE", "29019", "2026-10-01T17:50:00Z"), trunk_commit("a", "SUCCESS", "29018", "2026-10-01T17:40:00Z")]

    landing.watch_trunk(OPEN_ROWS)
    landing.watch_trunk(OPEN_ROWS)
    shell.clock += timedelta(minutes=30)
    bus_trunk(shell, "desk-runner", "incident", trunk_posts(shell)[0][2])
    bus_trunk(shell, "fix-dev", "opened", "opened #29021", pr="29021")
    landing.watch_trunk(OPEN_ROWS)

    red = "DEV-RED since 10:50 AM: dev's buildkite/test is red on https://buildkite.com/forge/test/builds/29019, first red at bbbbbbbbbb, landed by #29019. No PR can land until this is fixed; "
    assert trunk_posts(shell) == [
        ("incident", "root,drive-watchdog@d1", red + "nothing lands until a fix lands; name it with `cci post --topic dev-red --pr <n>`."),
        ("incident", "root,drive-watchdog@d1", red + "nothing lands until #29021 fixes it."),
    ]


def test_a_settled_trunk_is_read_from_github_again_only_once_ls_remote_shows_it_moved(shell, config, tmp_path):
    landing = trunk_watch(shell, config, tmp_path)
    shell.trunk = [trunk_commit("b", "SUCCESS", "29019", "2026-10-01T17:50:00Z")]
    landing.watch_trunk(OPEN_ROWS)
    landing.watch_trunk(OPEN_ROWS)
    assert len(graphql_reads(shell)) == 1
    shell.trunk = [trunk_commit("c", "PENDING", "29020", "2026-10-01T17:55:00Z"), *shell.trunk]
    landing.watch_trunk(OPEN_ROWS)
    landing.watch_trunk(OPEN_ROWS)
    assert len(graphql_reads(shell)) == 2
    shell.clock += runner_module.TRUNK_REREAD
    landing.watch_trunk(OPEN_ROWS)
    assert len(graphql_reads(shell)) == 3


def test_a_trunk_that_does_not_read_gates_nothing_and_posts_once_per_streak(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    trunk_watch(shell, config, tmp_path)
    shell.trunk = [trunk_commit("b", "SUCCESS", "29019", "2026-10-01T17:50:00Z")]
    shell.trunk_error = "fatal: expected flush after ref listing"
    landing_pass(shell, config)
    shell.clock += timedelta(hours=2)
    landing_pass(shell, config)
    assert shell.enqueues() == [] and not [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]
    assert [line.split(" ", 1)[0] for line in escalations(shell)] == ["TRUNK-READ-FAILED"]
    shell.trunk_error = ""
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1
    assert [line.split(" ", 1)[0] for line in escalations(shell)] == ["TRUNK-READ-FAILED", "TRUNK-READ-RECOVERED"]


def test_a_pr_body_is_read_once_per_five_minutes_across_passes(shell, config, tmp_path):
    landing = landing_of(shell, config, tmp_path)
    shell.bodies["29020"] = "first"
    assert [landing.body("29020"), landing.body("29020")] == ["first", "first"]
    shell.bodies["29020"] = "second"
    shell.clock += runner_module.BODY_MAX_AGE
    assert landing.body("29020") == "second"
    assert len([call for call in shell.calls if call[:3] == ["gh", "pr", "view"] and call[-1] == "body"]) == 2


def test_a_tree_waiting_on_github_alone_is_not_checked_and_its_lane_hears_what_its_status_asks(shell, config, tmp_path):
    shell.rows = single_row()
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 graphite CONFLICTING")]
    shell.pr_status["29020"] = {"queue": "not queued", "verdict": "blocked:ci-pending"}
    landing_pass(shell, config)
    assert checks(shell) == [] and blockers(shell) == []
    shell.pr_status["29020"] = {"queue": "not queued", "verdict": "blocked:conflict,ci-pending"}
    landing_pass(shell, config)
    assert checks(shell) == []
    assert [(post["to"], post_body(post)) for post in blockers(shell) if post["to"] != "root"] == [(LANE, f"#29020 cccc333333: {runner_module.CONFLICT_BLOCKER}")]
    shell.pr_status["29020"] = {"queue": "not queued", "verdict": "landable"}
    landing_pass(shell, config)
    assert len(checks(shell)) == 1


def test_a_pr_graphite_asks_to_restack_hears_it_from_the_status_read_without_a_check(shell, config, tmp_path):
    shell.rows = single_row()
    shell.pr_status["29020"] = {"queue": "not queued", "verdict": "blocked:needs-restack (parent landed)"}
    landing_pass(shell, config)
    assert checks(shell) == []
    assert [post_body(post) for post in blockers(shell) if post["to"] != "root"] == ["#29020 cccc333333: The stack reads as needs-restack (parent landed); run ccx vcs stack submit from the branch's worktree"]


@pytest.mark.parametrize(
    ("parent", "child", "checked"),
    [
        ("queued", "landable", True),
        ("landed", "landable", True),
        ("blocked:ci-pending", "landable", False),
        ("queued", "queued", False),
        ("landable", "blocked:waiting-on-downstack #29020,ci-pending", True),
        ("queued", "blocked:needs-restack (parent landed)", False),
        ("queued", "blocked:queue-unknown", True),
        ("queued", "blocked:ci-red", False),
        ("queued", "blocked:unapproved", False),
        ("queued", "blocked:ci-unproven", True),
    ],
)
def test_a_tree_is_checked_only_when_the_pass_is_about_to_enqueue_from_it(shell, config, tmp_path, parent, child, checked):
    two_pr_stack(shell)
    shell.pr_status = {"29020": {"verdict": parent}, "29031": {"verdict": child}}
    landing = landing_of(shell, config, tmp_path)
    rows = {pr: dict(row, ours=True) for pr, row in landing.rows().items()}
    assert landing.landable_trees(rows, [["29031"]]) == ([["29031"]] if checked else [])
    assert landing.unchecked == (set() if checked else {"29020", "29031"})


def test_a_trunk_green_again_posts_dev_green_once(shell, config, tmp_path):
    landing = trunk_watch(shell, config, tmp_path)
    bus_trunk(shell, "desk-runner", "incident", "DEV-RED since 10:50 AM: dev's buildkite/test is red. No PR can land until this is fixed.")
    shell.trunk = [trunk_commit("c", "SUCCESS", "29020", "2026-10-01T17:55:00Z"), trunk_commit("b", "FAILURE", "29019", "2026-10-01T17:50:00Z")]

    landing.watch_trunk(OPEN_ROWS)
    bus_trunk(shell, "desk-runner", "report", trunk_posts(shell)[0][2])
    landing.watch_trunk(OPEN_ROWS)

    assert trunk_posts(shell) == [("report", "root,drive-watchdog@d1", "DEV-GREEN dev's buildkite/test passes again; it was red since 10:50 AM.")]


@pytest.mark.parametrize(
    ("argv", "family"),
    [
        ([sys.executable, "/plugin/scripts/ledger.py", "-C", "/drive/checkout", "refresh", "--repo", "Forge-AI/monorepo"], "ledger.py refresh"),
        (["/drive/checkout/.agents/skills/submit-pr/scripts/stack-enqueue", "43052", "--check", "--partial"], "stack-enqueue --check"),
        (["ccx", "vcs", "pr", "status", "-R", "Forge-AI/monorepo", "--json", "42580", "42587"], "ccx vcs pr status"),
        (["gh", "api", "repos/Forge-AI/monorepo/compare/a~14...a", "-H", "Accept: application/vnd.github.diff"], "gh api"),
        (["ccx", "vcs", "gh", "--max-age", "5m", "--", "pr", "view", "42580", "--json", "body"], "gh pr view"),
        (["git", "-C", "/drive/checkout", "merge", "-q", "--ff-only", "FETCH_HEAD"], "git merge"),
    ],
)
def test_a_pass_cost_groups_each_command_by_its_program_and_verbs(argv, family):
    assert runner_module.command_family(argv) == family


def test_a_pass_cost_lists_what_the_pass_itself_waited_on_then_every_thread_and_starts_over():
    shell = runner_module.Shell()
    shell.spent = runner_module.Counter({"ledger.py refresh": 55.2, "stack-enqueue --check": 120.4, "cci tail": 31.0})
    shell.called = runner_module.Counter({"ledger.py refresh": 1, "stack-enqueue --check": 14, "cci tail": 90})
    shell.waited = runner_module.Counter({"cci tail": 30.2})
    assert shell.pass_cost() == "the pass waited on cci tail 30s; all threads: stack-enqueue --check 120s in 14, ledger.py refresh 55s in 1, cci tail 31s in 90"
    assert shell.pass_cost() == "the pass waited on nothing; all threads: "


def test_a_command_the_pass_thread_runs_counts_as_waited_and_a_workers_does_not():
    shell = runner_module.Shell()
    shell.spent, shell.called, shell.waited = runner_module.Counter(), runner_module.Counter(), runner_module.Counter()
    shell.run(["true"])
    worker = threading.Thread(target=lambda: shell.run(["true"]))
    worker.start()
    worker.join()
    assert (shell.called["true"], set(shell.waited)) == (2, {"true"})


def test_the_alive_record_names_the_passs_own_command_over_a_longer_one_a_worker_runs(shell, config, tmp_path, monkeypatch):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    monkeypatch.setitem(runner_module.Shell.in_progress, 1, ("ledger.py refresh --repo Forge-AI/monorepo", time.monotonic() - 600))
    monkeypatch.setitem(runner_module.Shell.in_progress, threading.main_thread().ident, ("cci tail --drive d1", time.monotonic() - 65))

    runner.post_alive("landing")

    assert shell.alive == [f"landing alive {shell.clock.astimezone(runner_module.PACIFIC):%-I:%M %p} pass 1 running `cci tail --drive d1` for 1m05s"]


def test_a_pr_query_reads_only_the_records_after_the_newest_it_kept_and_drops_one_that_expired():
    posted = [{"seq": 4, "text": "a"}, {"seq": 8, "text": "c"}]
    calls: list[str] = []

    def run(argv: list[str]) -> str:
        since = int(argv[argv.index("--since") + 1])
        calls.append(str(since))
        return "".join(json.dumps(record) + "\n" for record in posted if record["seq"] > since)

    tail = runner_module.cci.Tail(run)
    tail.remember()
    query = ("d1", "--pr", "Forge-AI/monorepo#29020")
    assert [record["seq"] for record in runner_module.cci.records(tail, *query)] == [4, 8]
    tail.forget()
    posted.append({"seq": 11, "text": "d"})
    assert [record["seq"] for record in runner_module.cci.records(tail, *query)] == [4, 8, 11]
    tail.forget()
    tail.grown[(query[0], query[1:])][0]["expires_at"] = "2000-01-01T00:00:00Z"
    assert [record["seq"] for record in runner_module.cci.records(tail, *query)] == [8, 11]
    assert calls == ["0", "8", "11"]


def test_a_cci_page_under_half_the_budget_is_the_last_one_read(monkeypatch):
    monkeypatch.setattr(runner_module.cci, "PAGE_BYTES", 400)
    pages = [[{"seq": 1, "text": "x" * 120}, {"seq": 2, "text": "x" * 120}], [{"seq": 3, "text": "y"}], []]
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(argv)
        return "".join(json.dumps(record) + "\n" for record in pages[len(calls) - 1])

    assert [record["seq"] for record in runner_module.cci.records(run, "d1", "--kind=go")] == [1, 2, 3]
    assert [call[call.index("--since") + 1] for call in calls] == ["0", "2"]


def test_a_full_page_of_cci_rows_is_never_the_last_one_read_however_small(monkeypatch):
    monkeypatch.setattr(runner_module.cci, "PAGE_ROWS", 2)
    pages = [[{"seq": 1, "text": "a"}, {"seq": 2, "text": "b"}], [{"seq": 3, "text": "c"}]]
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(argv)
        return "".join(json.dumps(record) + "\n" for record in pages[len(calls) - 1])

    assert [record["seq"] for record in runner_module.cci.records(run, "d1", "--kind=go")] == [1, 2, 3]
    assert [call[call.index("--since") + 1] for call in calls] == ["0", "2"]


def script_build(*steps: str, failed: str = "") -> dict:
    return {"jobs": [{"type": "script", "step_key": step, "name": step, "state": "failed" if step == failed else "passed"} for step in steps]}


def test_a_trunk_job_any_open_rows_build_runs_is_a_landing_red_even_when_the_newest_build_left_it_out(shell, config, tmp_path):
    with_ci_build(config)
    landing = trunk_watch(shell, config, tmp_path)
    built(shell, "cccc333333", "78214", script_build("sanddb-http", "sand-tests"))
    built(shell, "dddd444444", "78286", script_build("sand-tests"))
    built(shell, "bbbb", "29019", script_build("sanddb-http", "sand-tests", failed="sanddb-http"))
    built(shell, "aaaa", "29018", script_build("sanddb-http", "sand-tests"))
    shell.trunk = [trunk_commit("b", "FAILURE", "29019", "2026-10-01T17:50:00Z"), trunk_commit("a", "SUCCESS", "29018", "2026-10-01T17:40:00Z")]

    landing.watch_trunk(OPEN_ROWS)

    [(kind, _, text)] = trunk_posts(shell)
    assert kind == "incident" and text.startswith("DEV-RED since 10:50 AM: dev's buildkite/test fails sanddb-http")


def test_a_trunk_build_green_only_because_a_job_soft_fails_posts_post_merge_red_once_naming_the_prs_landed_since_the_last_build(shell, config, tmp_path):
    with_ci_build(config)
    desk_lane(config)
    landing = trunk_watch(shell, config, tmp_path)
    built(shell, "aaaa", "29018", script_build("sand-tests", "new-tree"))
    built(shell, "cccc", "29020", {"jobs": [{"type": "script", "name": "sand-tests", "state": "passed"}, {"type": "script", "name": "new-tree", "state": "failed", "soft_failed": True}]})
    shell.trunk = [trunk_commit("c", "SUCCESS", "29020", "2026-10-01T17:55:00Z"), trunk_commit("b", None, "29019", "2026-10-01T17:50:00Z"), trunk_commit("a", "SUCCESS", "29018", "2026-10-01T17:40:00Z")]
    rows = {"29019": {"state": "landed", "lane": "lane-b"}, "29020": {"state": "landed", "lane": "lane-c"}}

    landing.watch_trunk(rows)
    landing.watch_trunk(rows)

    [red] = [post for post in shell.posts if post["text"].startswith("POST-MERGE-RED")]
    assert red["to"] == f"root,{DESK},lane-c,lane-b"
    assert "dev's build https://buildkite.com/forge/test/builds/29020 has jobs failing that https://buildkite.com/forge/test/builds/29018 did not: new-tree soft-failed. Landed between the two: #29020, #29019." in red["text"]
    assert trunk_posts(shell) == []
    assert len([call for call in shell.calls if call[1:3] == ["bk", "api"]]) == 2


FINDER_HEAD = "7414944e30bfec89dee45328414dbc1016e35afa"
FINDER_OLD = "a269784e49009c5aa92b2341e08d6945f7ff8e95"


def finder_config(config: Path, **spec) -> None:
    raw = json.loads(config.read_text())
    config.write_text(json.dumps(raw | {"finder": spec}))


def head_record(shell: FakeShell, pr: str, head: str, lane: str = "p8a-step9", **changes) -> int:
    seq = 149000 + len(shell.records)
    shell.records.append({"seq": seq, "drive": "d1", "lane": lane, "kind": "head", "to": [], "refs": {"prs": [f"Forge-AI/monorepo#{pr}"]}, "fields": {"commit": head}, "text": f"HEAD #{pr} {head}", "source": "post", "at": actions.stamp(shell.clock)} | changes)
    return seq


def finder_review(shell: FakeShell, pr: str, text: str, lane: str = "p8a-step6b-creds-6") -> int:
    seq = 149000 + len(shell.records)
    shell.records.append({"seq": seq, "drive": "d1", "lane": lane, "kind": "review", "to": ["root"], "refs": {"prs": [f"Forge-AI/monorepo#{pr}"]}, "text": text, "source": "post", "at": actions.stamp(shell.clock)})
    return seq


def finder_jobs(shell: FakeShell) -> list[dict]:
    return [json.loads((directory / "job.json").read_text()) for directory in shell.finder_jobs]


def finder_failures(shell: FakeShell) -> list[str]:
    return [line for line in escalations(shell) if line.startswith("FINDER-FAILED")]


def test_each_head_starts_one_finder_at_once_however_many_records_name_it(shell, config):
    finder_config(config)
    orca_pass(shell, config)
    head_record(shell, "43439", FINDER_HEAD)
    head_record(shell, "43439", FINDER_HEAD, lane="p8a-step6b-creds-6", fields={}, text=f"#43439 HEAD {FINDER_HEAD} (was {FINDER_OLD}) on #42786")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert shell.finder_jobs == [config.resolve().parent / "finder-desk" / f"43439-{FINDER_HEAD[:12]}"]
    assert finder_jobs(shell) == [
        {"drive": "d1", "repo": "Forge-AI/monorepo", "pr": "43439", "head": FINDER_HEAD, "since": "", "prior": None, "previous": "", "history": [], "settled": {}, "lane": "p8a-step9", "to": ["desk-runner"], "checkout": str(config.parent / "checkout"), "model": "sol", "fast": True, "full": False}
    ]


def test_a_finder_job_also_addresses_its_prs_ledger_lane_when_a_worker_named_the_head(shell, config):
    finder_config(config)
    orca_pass(shell, config)
    head_record(shell, "45309", FINDER_HEAD, lane="p8a7-send-latency")
    shell.rows = [{"pr": "45309", "lane": "p8a-step7"}]
    orca_pass(shell, config)
    assert [(job["lane"], job["to"]) for job in finder_jobs(shell)] == [("p8a7-send-latency", ["desk-runner", "p8a-step7"])]

def test_an_opened_record_naming_a_full_head_starts_its_finder(shell, config):
    finder_config(config)
    orca_pass(shell, config)
    head_record(shell, "44099", FINDER_HEAD, kind="opened", fields={}, source="claim", text="opened #Forge-AI/monorepo#44099")
    head_record(shell, "44099", FINDER_HEAD, kind="opened", fields={}, text=f"OPENED #44099 at {FINDER_HEAD}, on dev")
    orca_pass(shell, config)
    assert [(job["pr"], job["head"]) for job in finder_jobs(shell)] == [("44099", FINDER_HEAD)]


@pytest.mark.parametrize(("lane", "passes"), [("finder-desk", 0), ("p8a-step6b-creds-6", 1)], ids=["desk-graded", "lane-graded"])
def test_only_a_head_the_finder_desk_already_graded_starts_no_second_pass(shell, config, lane, passes):
    finder_config(config)
    orca_pass(shell, config)
    head_record(shell, "43439", FINDER_HEAD)
    finder_review(shell, "43439", f"finder: ok {FINDER_HEAD[:7]} lgtm, no findings", lane=lane)
    orca_pass(shell, config)
    assert len(shell.finder_jobs) == passes


def test_a_full_pass_request_at_a_head_a_delta_record_graded_starts_a_whole_pass_ahead_of_queued_heads(shell, config):
    finder_config(config, concurrent=1)
    orca_pass(shell, config)
    head_record(shell, "44114", FINDER_HEAD)
    finder_review(shell, "44114", f"finder: major {FINDER_HEAD} 21 open over delta {FINDER_OLD[:7]}..{FINDER_HEAD[:7]}: go/a.go:1 drops a write", lane="finder-desk")
    head_record(shell, "43299", FINDER_OLD)
    seq = 149000 + len(shell.records)
    shell.records.append({"seq": seq, "drive": "d1", "lane": "root", "kind": "ask", "to": ["finder-desk", "p8b-step10"], "refs": {"prs": ["Forge-AI/monorepo#44114"]}, "text": f"finder-desk: finder: whole on #44114 over its own commits at {FINDER_HEAD[:7]}", "source": "post", "at": actions.stamp(shell.clock)})
    orca_pass(shell, config)
    assert shell.finder_jobs == [config.resolve().parent / "finder-desk" / f"44114-whole-{seq}"]
    [whole] = finder_jobs(shell)
    assert (whole["pr"], whole["head"], whole["since"], whole["prior"], whole["full"]) == ("44114", "", "", None, True)
    finder_review(shell, "44114", f"finder: ok {FINDER_HEAD} sol finder over whole {FINDER_OLD[:7]}^..{FINDER_HEAD[:7]}: no majors", lane="finder-desk")
    (shell.finder_jobs[0] / "result.json").write_text(json.dumps({"verdict": "ok", "record": "finder: ok", "posted": "#150003"}))
    shell.records.append({"seq": seq + 10, "drive": "d1", "lane": "root", "kind": "decide", "to": ["finder-desk"], "refs": {"prs": ["Forge-AI/monorepo#44114"]}, "text": "finder-desk: the whole-diff pass on #44114 is still owed", "source": "post", "at": actions.stamp(shell.clock)})
    orca_pass(shell, config)
    assert [job["pr"] for job in finder_jobs(shell)] == ["44114", "43299"]


@pytest.mark.parametrize(
    ("lane", "kind", "text"),
    [
        ("root", "decide", "re #182756: the carried major on #44114 at {head} is settled"),
        ("root", "decide", "finder: whole #44114, one whole-diff pass at {head}"),
        ("p8b-step10", "ask", "finder: whole #44114 at {head}"),
    ],
    ids=["root-decide", "root-decide-saying-whole", "lane-ask"],
)
def test_a_root_decide_or_a_lane_ask_on_a_pr_with_a_finder_record_starts_no_whole_pass(shell, config, lane, kind, text):
    finder_config(config, concurrent=1)
    orca_pass(shell, config)
    head_record(shell, "44114", FINDER_HEAD)
    finder_review(shell, "44114", f"finder: major {FINDER_HEAD} 1 open over delta {FINDER_OLD[:7]}..{FINDER_HEAD[:7]}: go/a.go:1 drops a write", lane="finder-desk")
    orca_pass(shell, config)
    started = list(shell.finder_jobs)
    shell.records.append({"seq": 149000 + len(shell.records), "drive": "d1", "lane": lane, "kind": kind, "to": ["finder-desk"], "refs": {"prs": ["Forge-AI/monorepo#44114"]}, "text": text.format(head=FINDER_HEAD[:7]), "source": "post", "at": actions.stamp(shell.clock)})
    orca_pass(shell, config)
    assert shell.finder_jobs == started and not [job for job in shell.finder_jobs if "-whole-" in job.name]


def test_a_finder_job_carries_each_finder_record_a_root_decide_settled(shell, config):
    finder_config(config)
    orca_pass(shell, config)
    first = finder_review(shell, "44114", f"finder: major {FINDER_OLD} 1 open over whole {FINDER_OLD[:7]}^..{FINDER_OLD[:7]}: go/a.go:1 drops a write", lane="finder-desk")
    carried = finder_review(shell, "44114", f"finder: major {FINDER_OLD[:7]} carried from #{first}", lane="finder-desk")
    for re_seq, text in ((first, "re: the write is dropped on purpose"), (carried + 1000, "#44114: the carried major is settled")):
        shell.records.append({"seq": 149000 + len(shell.records), "drive": "d1", "lane": "root", "kind": "decide", "to": ["finder-desk"], "re": re_seq, "resolves": re_seq, "refs": {"prs": ["Forge-AI/monorepo#44114"]}, "text": text, "at": actions.stamp(shell.clock)})
    decides = [record["seq"] for record in shell.records if record["kind"] == "decide"]
    shell.records.append({"seq": 149000 + len(shell.records), "drive": "d1", "lane": "p8b-step10", "kind": "decide", "to": ["finder-desk"], "resolves": carried, "refs": {"prs": ["Forge-AI/monorepo#44114"]}, "text": "settled, I think", "at": actions.stamp(shell.clock)})
    later = finder_review(shell, "44114", f"finder: major {FINDER_OLD[:7]} 1 open over delta: go/b.go:2 a new finding", lane="finder-desk")
    head_record(shell, "44114", FINDER_HEAD)
    orca_pass(shell, config)
    [job] = finder_jobs(shell)
    assert job["settled"] == {str(first): decides[0], str(carried): decides[1]} and str(later) not in job["settled"]


def test_finders_past_the_cap_wait_queued_and_start_as_running_ones_finish(shell, config, capsys):
    finder_config(config, concurrent=2, model="astra", to=["fidelity-desk"])
    orca_pass(shell, config)
    heads = {pr: hashlib.sha1(pr.encode()).hexdigest() for pr in ("43439", "43299", "42784")}
    for pr, head in heads.items():
        head_record(shell, pr, head)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert [job["pr"] for job in finder_jobs(shell)] == ["43439", "43299"]
    assert {(job["model"], tuple(job["to"])) for job in finder_jobs(shell)} == {("astra", ("desk-runner", "fidelity-desk"))}
    cli(shell, config, "show")
    shown = capsys.readouterr().out
    assert f"finder #42784 {heads['42784'][:12]}: queued" in shown and f"finder #43299 {heads['43299'][:12]}: running since" in shown
    (shell.finder_jobs[0] / "result.json").write_text(json.dumps({"verdict": "ok", "record": f"finder: ok {heads['43439']}", "posted": "#150001"}))
    orca_pass(shell, config)
    assert [job["pr"] for job in finder_jobs(shell)] == ["43439", "43299", "42784"]
    assert not finder_failures(shell)


def test_a_queued_head_whose_pr_a_root_go_names_starts_before_older_heads(shell, config):
    finder_config(config, concurrent=1)
    orca_pass(shell, config)
    heads = {pr: hashlib.sha1(pr.encode()).hexdigest() for pr in ("43439", "43299", "43601")}
    for pr, head in heads.items():
        head_record(shell, pr, head)
    shell.records.append({"seq": 149900, "drive": "d1", "lane": "root", "kind": "go", "to": ["landing-desk"], "refs": {}, "text": f"go #43601 at {heads['43601'][:10]}", "at": actions.stamp(shell.clock)})
    orca_pass(shell, config)
    assert [job["pr"] for job in finder_jobs(shell)] == ["43601"]


def test_a_queued_head_the_ledger_ranks_first_starts_before_a_root_go_and_older_heads(shell, config):
    finder_config(config, concurrent=1)
    orca_pass(shell, config)
    heads = {pr: hashlib.sha1(pr.encode()).hexdigest() for pr in ("43439", "43299", "43601", "45244")}
    for pr, head in heads.items():
        head_record(shell, pr, head)
    shell.records.append({"seq": 149900, "drive": "d1", "lane": "root", "kind": "go", "to": ["landing-desk"], "refs": {}, "text": f"go #43601 at {heads['43601'][:10]}", "at": actions.stamp(shell.clock)})
    shell.rows = [{"pr": "43601", "priority": 100}, {"pr": "45244", "priority": 0}]
    orca_pass(shell, config)
    assert [job["pr"] for job in finder_jobs(shell)] == ["45244"]


def test_a_green_ledger_head_no_record_named_starts_one_finder_pass_and_others_start_none(shell, config):
    finder_config(config)
    orca_pass(shell, config)
    heads = {pr: hashlib.sha1(pr.encode()).hexdigest() for pr in ("44614", "44599", "44713", "45001")}
    shell.rows = [
        {"pr": "44614", "head": heads["44614"], "test_state": "success", "lane": "p8c-conversion-ship-6"},
        {"pr": "44599", "head": heads["44599"], "test_state": "pending", "lane": "p8c-conversion-ship-6"},
        {"pr": "44713", "head": heads["44713"], "test_state": "success", "lane": "p8c-conversion-ship-6", "state": "closed"},
        {"pr": "45001", "head": heads["45001"][:12], "test_state": "success", "lane": "p8c-conversion-ship-6"},
    ]
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert [(job["pr"], job["head"], job["lane"]) for job in finder_jobs(shell)] == [("44614", heads["44614"], "p8c-conversion-ship-6")]


def test_a_ledger_head_trailing_a_queued_head_record_never_supersedes_it(shell, config):
    finder_config(config, concurrent=1)
    orca_pass(shell, config)
    head_record(shell, "43439", hashlib.sha1(b"43439").hexdigest())
    head_record(shell, "44614", FINDER_HEAD)
    shell.rows = [{"pr": "44614", "head": FINDER_OLD, "test_state": "success"}]
    orca_pass(shell, config)
    (shell.finder_jobs[0] / "result.json").write_text(json.dumps({"verdict": "ok", "record": "finder: ok", "posted": "#150001"}))
    orca_pass(shell, config)
    assert [(job["pr"], job["head"]) for job in finder_jobs(shell)][1:] == [("44614", FINDER_HEAD)]


def test_a_finder_job_asks_at_the_fast_tier_unless_runner_json_turns_it_off(shell, config):
    finder_config(config, fast=False)
    orca_pass(shell, config)
    head_record(shell, "43439", FINDER_HEAD)
    orca_pass(shell, config)
    assert [job["fast"] for job in finder_jobs(shell)] == [False]


def test_a_queued_head_whose_pr_an_owner_tapped_starts_before_older_heads(shell, config):
    finder_config(config, concurrent=1)
    orca_pass(shell, config)
    heads = {pr: hashlib.sha1(pr.encode()).hexdigest() for pr in ("43439", "43299", "43601")}
    for pr, head in heads.items():
        head_record(shell, pr, head)
    shell.board = {"pr-groups": {"43601": {"verdict": "approved", "head": heads["43601"]}, "43439": {"verdict": None}}}
    orca_pass(shell, config)
    assert [job["pr"] for job in finder_jobs(shell)] == ["43601"]


def test_a_head_of_a_pr_a_finder_pass_graded_starts_before_older_ungraded_heads(shell, config):
    finder_config(config, concurrent=1)
    orca_pass(shell, config)
    head_record(shell, "43299", FINDER_OLD)
    orca_pass(shell, config)
    head_record(shell, "43439", hashlib.sha1(b"43439").hexdigest())
    head_record(shell, "43299", FINDER_HEAD)
    (shell.finder_jobs[0] / "result.json").write_text(json.dumps({"verdict": "ok", "record": f"finder: ok {FINDER_OLD}", "posted": "#150001"}))
    orca_pass(shell, config)
    assert [(job["pr"], job["head"]) for job in finder_jobs(shell)] == [("43299", FINDER_OLD), ("43299", FINDER_HEAD)]


def test_a_pr_whose_head_moves_while_it_waits_keeps_its_place_ahead_of_prs_queued_after_its_first_head(shell, config):
    finder_config(config, concurrent=1)
    orca_pass(shell, config)
    head_record(shell, "43601", hashlib.sha1(b"43601").hexdigest())
    orca_pass(shell, config)
    head_record(shell, "44802", FINDER_OLD)
    head_record(shell, "43439", hashlib.sha1(b"43439").hexdigest())
    orca_pass(shell, config)
    shell.clock += timedelta(minutes=5)
    head_record(shell, "44802", FINDER_HEAD)
    orca_pass(shell, config)
    (shell.finder_jobs[0] / "result.json").write_text(json.dumps({"verdict": "ok", "record": "finder: ok", "posted": "#150001"}))
    orca_pass(shell, config)
    assert [(job["pr"], job["head"]) for job in finder_jobs(shell)][1] == ("44802", FINDER_HEAD)


def test_a_running_pass_whose_pr_queues_a_newer_head_is_cancelled_and_frees_its_slot(shell, config):
    finder_config(config, concurrent=1)
    orca_pass(shell, config)
    head_record(shell, "43299", FINDER_OLD)
    orca_pass(shell, config)
    [running] = shell.finder_jobs
    (running / "codex-1").mkdir()
    head_record(shell, "43299", FINDER_HEAD)
    orca_pass(shell, config)
    assert [job["head"] for job in finder_jobs(shell)] == [FINDER_OLD, FINDER_HEAD]
    assert (running / runner_module.finder.CANCELLED).read_text() == f"{FINDER_HEAD}\n"
    assert ["codex-ask", "--cancel", str(running / "codex-1")] in shell.calls
    assert incident(config.parent, "desk-finder").actions[f"finder:43299:{FINDER_OLD}"].response["superseded"] == FINDER_HEAD
    assert not finder_failures(shell)


def test_a_queued_head_whose_pr_closed_or_moved_settles_skipped_and_starts_no_pass(shell, config):
    finder_config(config, concurrent=1)
    orca_pass(shell, config)
    heads = {pr: hashlib.sha1(pr.encode()).hexdigest() for pr in ("43439", "43299", "42784")}
    for pr, head in heads.items():
        head_record(shell, pr, head)
    shell.pr_heads = {"43439": ("CLOSED", heads["43439"]), "43299": ("OPEN", FINDER_HEAD), "42784": ("MERGED", heads["42784"])}
    orca_pass(shell, config)
    assert [job["pr"] for job in finder_jobs(shell)] == ["42784"]
    finder = incident(config.parent, "desk-finder").actions
    assert finder[f"finder:43439:{heads['43439']}"].response["skipped"] == "#43439 is closed"
    assert finder[f"finder:43299:{heads['43299']}"].response["skipped"] == f"#43299 moved to {FINDER_HEAD[:12]}"


def test_a_head_bound_to_a_cci_seq_as_its_pr_settles_skipped_and_the_real_prs_live_read_still_holds(shell, config):
    finder_config(config)
    orca_pass(shell, config)
    head_record(shell, "177834", FINDER_HEAD, lane="newtree-deploy-targets-1", refs={"prs": [177834]}, fields={}, text=f"newtree-deploy-targets-1 {FINDER_HEAD}: gateway URL route (root #177834)")
    head_record(shell, "43439", FINDER_OLD)
    head_record(shell, "43299", FINDER_OLD)
    shell.missing_prs = {"177834"}
    shell.pr_heads = {"43439": ("OPEN", FINDER_OLD), "43299": ("CLOSED", FINDER_OLD)}
    orca_pass(shell, config)
    assert [job["pr"] for job in finder_jobs(shell)] == ["43439"]
    finder = incident(config.parent, "desk-finder").actions
    assert finder[f"finder:177834:{FINDER_HEAD}"].response["skipped"] == "#177834 is no pull request in Forge-AI/monorepo"
    assert finder[f"finder:43299:{FINDER_OLD}"].response["skipped"] == "#43299 is closed"
    assert not finder_failures(shell)


def test_the_finder_thread_refills_a_freed_slot_before_the_next_orca_pass(shell, config, tmp_path, monkeypatch):
    finder_config(config, concurrent=1)
    orca_pass(shell, config)
    head_record(shell, "43439", FINDER_HEAD)
    head_record(shell, "43299", FINDER_OLD)
    orca_pass(shell, config)
    (shell.finder_jobs[0] / "result.json").write_text(json.dumps({"verdict": "ok", "record": f"finder: ok {FINDER_HEAD}", "posted": "#150001"}))
    monkeypatch.setattr(runner_module, "FINDER_EVERY", timedelta(milliseconds=10))
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    stop = runner.keep_finding()
    deadline = time.monotonic() + 5
    while len(shell.finder_jobs) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    assert [job["pr"] for job in finder_jobs(shell)] == ["43439", "43299"]


def test_a_newer_head_supersedes_the_queued_one_and_reads_since_the_head_the_last_finder_read(shell, config):
    finder_config(config, concurrent=1)
    orca_pass(shell, config)
    head_record(shell, "43299", FINDER_OLD)
    orca_pass(shell, config)
    review = finder_review(shell, "43299", f"finder: major {FINDER_OLD[:7]} 1 open: walk.go:9 skips the volume link", lane="finder-desk")
    head_record(shell, "43299", hashlib.sha1(b"between").hexdigest())
    head_record(shell, "43299", FINDER_HEAD)
    (shell.finder_jobs[0] / "result.json").write_text(json.dumps({"verdict": "major", "record": "finder: major", "posted": "#150002"}))
    orca_pass(shell, config)
    first, second = finder_jobs(shell)
    assert (first["head"], first["since"]) == (FINDER_OLD, "")
    assert (second["head"], second["since"]) == (FINDER_HEAD, FINDER_OLD)
    assert second["prior"] == {"seq": review, "lane": "finder-desk", "head": FINDER_OLD[:7], "text": f"finder: major {FINDER_OLD[:7]} 1 open: walk.go:9 skips the volume link"}


def test_a_head_after_a_finder_review_carries_the_head_its_head_record_replaced(shell, config):
    finder_config(config, concurrent=1)
    orca_pass(shell, config)
    finder_review(shell, "43299", "p8b-step8 finder (gpt-6.1-sol, one pass, never re-run) on #43299 S8.3: major 1; each major fixed or rebutted below")
    head_record(shell, "43299", FINDER_HEAD, text=f"HEAD #43299 {FINDER_HEAD} (was {FINDER_OLD}): restack onto #42751", fields={})
    head_record(shell, "43439", FINDER_OLD, text=f"HEAD #43439 {FINDER_OLD} (was {FINDER_HEAD})", fields={})
    orca_pass(shell, config)
    (shell.finder_jobs[0] / "result.json").write_text(json.dumps({"skipped": "replay"}))
    orca_pass(shell, config)
    assert [(job["pr"], job["previous"]) for job in finder_jobs(shell)] == [("43299", FINDER_OLD), ("43439", "")]
    [named] = finder_jobs(shell)[0]["history"]
    assert named["lane"] == "p8a-step6b-creds-6" and named["text"].startswith("p8b-step8 finder (gpt-6.1-sol, one pass")


def test_a_finder_that_fails_or_writes_nothing_by_its_deadline_runs_again_and_escalates_once_on_its_third_failure(shell, config):
    finder_config(config, timeout_minutes=1)
    orca_pass(shell, config)
    head_record(shell, "43439", FINDER_HEAD)
    head_record(shell, "43299", FINDER_OLD)
    orca_pass(shell, config)
    for attempt in range(3):
        failed, silent = shell.finder_jobs[-2:]
        (failed / "result.json").write_text(json.dumps({"error": "codex-ask -m sol --schema findings exited 1: quota exhausted"}))
        shell.clock += timedelta(minutes=2)
        orca_pass(shell, config)
        assert len(finder_failures(shell)) == (2 if attempt == 2 else 0)
    orca_pass(shell, config)
    first, second = shell.finder_jobs[:2]
    assert shell.finder_jobs == [first, second, first.with_name(f"{first.name}-2"), second.with_name(f"{second.name}-2"), first.with_name(f"{first.name}-3"), second.with_name(f"{second.name}-3")]
    assert finder_failures(shell) == [
        f"FINDER-FAILED finder:43439:{FINDER_HEAD} p8a-step9: #43439 {FINDER_HEAD[:12]}: the finder desk's pass failed on the last of its 3 attempts: codex-ask -m sol --schema findings exited 1: quota exhausted; its log is {failed / 'job.log'}; root asks finder-desk `finder: whole` on #43439 to run it again, since only its records grade a head",
        f"FINDER-FAILED finder:43299:{FINDER_OLD} p8a-step9: #43299 {FINDER_OLD[:12]}: the finder desk's pass failed on the last of its 3 attempts: no result 2 min after it started; its log is {silent / 'job.log'}; root asks finder-desk `finder: whole` on #43299 to run it again, since only its records grade a head",
    ]


def test_a_pass_whose_process_exited_with_no_result_fails_that_pass_and_its_slot_starts_the_retry(shell, config, tmp_path):
    finder_config(config, concurrent=1)
    orca_pass(shell, config)
    head_record(shell, "43439", FINDER_HEAD)
    head_record(shell, "43299", FINDER_OLD)
    orca_pass(shell, config)
    [crashed] = shell.finder_jobs
    shell.exited.add(int((crashed / "pid").read_text()))
    orca_pass(shell, config)
    assert shell.finder_jobs == [crashed, crashed.with_name(f"{crashed.name}-2")]
    passes = incident(tmp_path, "desk-finder").actions
    assert passes[f"finder:43439:{FINDER_HEAD}"].reason == "finder.py exited 0 min after it started and wrote no result"
    assert passes[f"finder:43439:{FINDER_HEAD}#2"].status == "started" and passes[f"finder:43299:{FINDER_OLD}"].status == "accepted"
    assert not finder_failures(shell)


def finder_desk_stalls(shell: FakeShell) -> list[str]:
    return [line for line in escalations(shell) if line.startswith("FINDER-DESK-STALLED")]


def test_passes_queued_while_none_settles_for_fifteen_minutes_post_one_desk_stall_per_streak(shell, config):
    finder_config(config, concurrent=1)
    orca_pass(shell, config)
    for pr in ("43439", "43299", "42784"):
        head_record(shell, pr, hashlib.sha1(pr.encode()).hexdigest())
    orca_pass(shell, config)
    shell.clock += timedelta(minutes=14)
    orca_pass(shell, config)
    assert finder_desk_stalls(shell) == []
    shell.clock += timedelta(minutes=2)
    orca_pass(shell, config)
    orca_pass(shell, config)
    running = hashlib.sha1(b"43439").hexdigest()[:12]
    assert finder_desk_stalls(shell) == [
        f"FINDER-DESK-STALLED finder-desk:2026-10-01T18:00:00Z finder-desk: 2 queued, and no finder pass has settled since 11:00; running (at most 1 at once): #43439 {running} for 16 min; each pass logs to job.log in its directory under {config.resolve().parent / 'finder-desk'}"
    ]
    (shell.finder_jobs[0] / "result.json").write_text(json.dumps({"verdict": "ok", "record": "finder: ok", "posted": "#150001"}))
    orca_pass(shell, config)
    shell.clock += timedelta(minutes=16)
    orca_pass(shell, config)
    assert [line.split()[1] for line in finder_desk_stalls(shell)] == ["finder-desk:2026-10-01T18:00:00Z", "finder-desk:2026-10-01T18:16:00Z"]


def test_a_runner_with_no_finder_config_starts_no_finder(shell, config, tmp_path):
    orca_pass(shell, config)
    head_record(shell, "43439", FINDER_HEAD)
    orca_pass(shell, config)
    assert not shell.finder_jobs and not (tmp_path / "store" / "desk-finder.json").exists()


BASELINE = "c57cad26 (release 10435, fleet sand 0.162.1876)"


def fidelity_config(config: Path, **spec) -> None:
    raw = json.loads(config.read_text())
    raw["landing"]["fidelity_lane"] = ["fidelity-desk-4", "fidelity-pipeline"]
    config.write_text(json.dumps(raw | {"fidelity": spec}))


def fidelity_jobs(shell: FakeShell) -> list[dict]:
    return [json.loads((directory / "job.json").read_text()) for directory in shell.fidelity_jobs]


def fidelity_failures(shell: FakeShell) -> list[tuple[str, str]]:
    return [(post["to"], Path(post["path"]).read_text().splitlines()[0] if "path" in post else post["text"]) for post in shell.posts if post.get("text", "").startswith("FIDELITY-FAILED")]


def test_each_head_starts_one_fidelity_pass_carrying_the_prs_grades_old_app_lines_and_the_lanes_contract(shell, config):
    fidelity_config(config, to=["board-refresher-2"], baseline=BASELINE)
    orca_pass(shell, config)
    graded = finder_review(shell, "43439", f"fidelity: ok {FINDER_OLD} pure port, no interaction: ports old-tree #41725", lane="fidelity-desk-4")
    finder_review(shell, "43439", f"old-app: unchanged {FINDER_OLD[:7]} baseline {BASELINE}; go/sand/next only", lane="fidelity-desk-4")
    finder_review(shell, "43439", f"fidelity: ok {FINDER_OLD} a grade from a lane that is no fidelity lane", lane="p8a-step9")
    shell.records.append({"seq": 149900, "drive": "d1", "lane": "p8a-step9", "kind": "contract", "to": ["fidelity-desk-4"], "refs": {"prs": ["Forge-AI/monorepo#43439"]}, "text": "contract: builds the resubscribe; deletes nothing", "at": actions.stamp(shell.clock)})
    for seq, lane, text in ((149901, "root", f"re #{graded}: class wrong, not a pure port."), (149902, "root", f"re #{graded}: go after the restack."), (149903, "p8a-step9", f"re #{graded}: class wrong, I think.")):
        shell.records.append({"seq": seq, "drive": "d1", "lane": lane, "kind": "decide", "to": ["desk-runner"], "re": graded, "refs": {"prs": ["Forge-AI/monorepo#43439"]}, "text": text, "at": actions.stamp(shell.clock)})
    head_record(shell, "43439", FINDER_HEAD)
    second = head_record(shell, "43439", FINDER_HEAD, fields={}, text=f"#43439 HEAD {FINDER_HEAD} (was {FINDER_OLD}): restack, range-diff patch-identical")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert shell.fidelity_jobs == [config.resolve().parent / "fidelity-pipeline" / f"43439-{FINDER_HEAD[:12]}-{second}"]
    [job] = fidelity_jobs(shell)
    assert job == {
        "drive": "d1",
        "repo": "Forge-AI/monorepo",
        "pr": "43439",
        "head": FINDER_HEAD,
        "lane": "p8a-step9",
        "to": ["desk-runner", "board-refresher-2"],
        "readers": ["fidelity-desk-4"],
        "lanes": ["fidelity-desk-4", "fidelity-pipeline"],
        "grades": [{"seq": graded, "lane": "fidelity-desk-4", "text": f"fidelity: ok {FINDER_OLD} pure port, no interaction: ports old-tree #41725"}],
        "old_apps": [{"seq": graded + 1, "lane": "fidelity-desk-4", "head": FINDER_OLD[:7], "line": f"old-app: unchanged {FINDER_OLD[:7]} baseline {BASELINE}; go/sand/next only"}],
        "corrected": [graded],
        "settled": {str(graded): 149901},
        "contract": "contract: builds the resubscribe; deletes nothing",
        "exempt": {"port": runner_module.PORT_PHRASE, "test-only": runner_module.TEST_ONLY_PHRASE},
        "exempt_grades": [runner_module.PORT_PHRASE, runner_module.TEST_ONLY_PHRASE],
        "baseline": BASELINE,
        "checkout": str(config.parent / "checkout"),
        "model": "sol",
        "fast": True,
    }


@pytest.mark.parametrize(
    ("lane", "text"),
    [
        ("fidelity-desk-4", f"fidelity: ok {FINDER_HEAD[:7]} checked rule/949e6be names poll"),
        ("fidelity-desk-4", f"fidelity: missing {FINDER_HEAD} rule/949e6be polls the signal"),
        ("fidelity-pipeline", f"fidelity: reader {FINDER_HEAD} deletes a reserved path"),
    ],
    ids=["desk-ok", "desk-missing", "sent-to-a-reader"],
)
def test_a_head_a_fidelity_lane_already_graded_or_sent_to_a_reader_starts_no_pass(shell, config, lane, text):
    fidelity_config(config)
    orca_pass(shell, config)
    head_record(shell, "43439", FINDER_HEAD)
    finder_review(shell, "43439", text, lane=lane)
    orca_pass(shell, config)
    assert not shell.fidelity_jobs


def test_the_finder_thread_starts_a_new_heads_fidelity_pass_before_the_next_orca_pass(shell, config, tmp_path, monkeypatch):
    fidelity_config(config)
    orca_pass(shell, config)
    head_record(shell, "43439", FINDER_HEAD)
    monkeypatch.setattr(runner_module, "FINDER_EVERY", timedelta(milliseconds=10))
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    stop = runner.keep_finding()
    deadline = time.monotonic() + 5
    while not shell.fidelity_jobs and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    assert [(job["pr"], job["head"]) for job in fidelity_jobs(shell)] == [("43439", FINDER_HEAD)]

def test_fidelity_passes_past_eight_wait_queued_and_show_lists_queued_and_running_ones(shell, config, capsys):
    fidelity_config(config)
    orca_pass(shell, config)
    heads = {str(pr): hashlib.sha1(str(pr).encode()).hexdigest() for pr in range(43430, 43439)}
    for pr, head in heads.items():
        head_record(shell, pr, head)
    orca_pass(shell, config)
    assert [job["pr"] for job in fidelity_jobs(shell)] == list(heads)[:8]
    cli(shell, config, "show")
    shown = capsys.readouterr().out
    assert f"fidelity #43438 {heads['43438'][:12]}: queued" in shown and f"fidelity #43430 {heads['43430'][:12]}: running since" in shown
    (shell.fidelity_jobs[0] / "result.json").write_text(json.dumps({"verdict": "carried", "posted": ["#150001"]}))
    orca_pass(shell, config)
    assert [job["pr"] for job in fidelity_jobs(shell)] == list(heads)


def test_a_fidelity_head_the_ledger_ranks_first_starts_before_older_heads_and_asks_at_the_fast_tier(shell, config):
    fidelity_config(config, concurrent=1)
    orca_pass(shell, config)
    for pr in ("43439", "43299", "45244"):
        head_record(shell, pr, hashlib.sha1(pr.encode()).hexdigest())
    shell.rows = [{"pr": "45244", "priority": 0}]
    orca_pass(shell, config)
    assert [(job["pr"], job["fast"]) for job in fidelity_jobs(shell)] == [("45244", True)]


def test_a_green_ledger_head_no_record_named_starts_one_fidelity_pass(shell, config):
    fidelity_config(config)
    orca_pass(shell, config)
    shell.rows = [{"pr": "44614", "head": FINDER_HEAD, "test_state": "success", "lane": "p8c-conversion-ship-6"}, {"pr": "44599", "head": FINDER_OLD, "test_state": "failure"}]
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert [(job["pr"], job["head"], job["lane"]) for job in fidelity_jobs(shell)] == [("44614", FINDER_HEAD, "p8c-conversion-ship-6")]


def test_a_failed_fidelity_pass_fails_at_once_to_the_human_desks_and_the_next_head_record_queues_it_again(shell, config):
    fidelity_config(config, timeout_minutes=1)
    orca_pass(shell, config)
    first = head_record(shell, "43439", FINDER_HEAD)
    silent = head_record(shell, "43299", FINDER_OLD)
    orca_pass(shell, config)
    failed, quiet = shell.fidelity_jobs
    (failed / "result.json").write_text(json.dumps({"error": "codex-ask -m sol --schema fidelity.schema.json exited 1: quota exhausted"}))
    orca_pass(shell, config)
    shell.clock += timedelta(minutes=2)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert fidelity_failures(shell) == [
        ("fidelity-desk-4", f"FIDELITY-FAILED fidelity:43439:{FINDER_HEAD}:{first} p8a-step9: #43439 {FINDER_HEAD[:12]}: the fidelity pass failed: codex-ask -m sol --schema fidelity.schema.json exited 1: quota exhausted; its log is {failed / 'job.log'}; a fidelity desk grades it by hand, and the next head record for #43439 queues the pass again"),
        ("fidelity-desk-4", f"FIDELITY-FAILED fidelity:43299:{FINDER_OLD}:{silent} p8a-step9: #43299 {FINDER_OLD[:12]}: the fidelity pass failed: no result 2 min after it started; its log is {quiet / 'job.log'}; a fidelity desk grades it by hand, and the next head record for #43299 queues the pass again"),
    ]
    again = head_record(shell, "43439", FINDER_HEAD)
    orca_pass(shell, config)
    assert shell.fidelity_jobs[-1] == config.resolve().parent / "fidelity-pipeline" / f"43439-{FINDER_HEAD[:12]}-{again}"


def test_a_failed_pass_whose_notice_does_not_post_stays_started_and_tells_the_desks_on_the_next_pass(shell, config):
    fidelity_config(config)
    orca_pass(shell, config)
    head_record(shell, "43439", FINDER_HEAD)
    orca_pass(shell, config)
    [failed] = shell.fidelity_jobs
    (failed / "result.json").write_text(json.dumps({"error": "codex-ask exited 1: quota exhausted"}))
    shell.post_error = "cci: daemon restarting"
    orca_pass(shell, config)
    assert not fidelity_failures(shell) and [action.status for action in incident(config.parent, "desk-fidelity").actions.values()] == ["started"]
    shell.post_error = ""
    orca_pass(shell, config)
    assert len(fidelity_failures(shell)) == 1 and [action.status for action in incident(config.parent, "desk-fidelity").actions.values()] == ["failed"]


def test_a_fidelity_head_bound_to_a_cci_seq_or_a_closed_or_moved_pr_settles_skipped_and_fails_nothing(shell, config):
    fidelity_config(config)
    orca_pass(shell, config)
    bound = head_record(shell, "177834", FINDER_HEAD, lane="newtree-deploy-targets-1", refs={"prs": [177834]}, fields={}, text=f"newtree-deploy-targets-1 {FINDER_HEAD}: gateway URL route (root #177834)")
    live, closed, moved = (head_record(shell, pr, FINDER_OLD) for pr in ("43439", "43299", "42784"))
    shell.missing_prs = {"177834"}
    shell.pr_heads = {"43439": ("OPEN", FINDER_OLD), "43299": ("CLOSED", FINDER_OLD), "42784": ("OPEN", FINDER_HEAD)}
    orca_pass(shell, config)
    assert [job["pr"] for job in fidelity_jobs(shell)] == ["43439"]
    passes = incident(config.parent, "desk-fidelity").actions
    assert passes[f"fidelity:177834:{FINDER_HEAD}:{bound}"].response["skipped"] == "#177834 is no pull request in Forge-AI/monorepo"
    assert passes[f"fidelity:43299:{FINDER_OLD}:{closed}"].response["skipped"] == "#43299 is closed"
    assert passes[f"fidelity:42784:{FINDER_OLD}:{moved}"].response["skipped"] == f"#42784 moved to {FINDER_HEAD[:12]}"
    assert f"fidelity:43439:{FINDER_OLD}:{live}" in passes and not fidelity_failures(shell)


def test_a_runner_with_no_fidelity_config_starts_no_fidelity_pass(shell, config, tmp_path):
    orca_pass(shell, config)
    head_record(shell, "43439", FINDER_HEAD)
    orca_pass(shell, config)
    assert not shell.fidelity_jobs and not (tmp_path / "store" / "desk-fidelity.json").exists()


def build_failed_checked(config: Path) -> None:
    raw = json.loads(config.read_text())
    raw["landing"]["checks"] = [*runner_module.merge_queue.CHECKS, "build-failed"]
    raw["landing"]["ci_build"] = "tools/ci bk api /pipelines/{pipeline}/builds/{number}"
    raw["landing"]["build_failures"] = {"tree": "go/sand/next/", "context": "go-build-failed-sand-next-tests", "annotations": "tools/ci bk api /pipelines/{pipeline}/builds/{number}/annotations"}
    config.write_text(json.dumps(raw))


LOCALRUN_ERROR = "go/sand/next/internal/localrun/localrun.go:49:21: undefined: vmrun.ReadMeta"


def build_failed_annotation(*lines: tuple[str, str], context: str = "go-build-failed-sand-next-tests") -> dict:
    items = "".join(f"<li><code>{package}</code> <code>{html.escape(error)}</code></li>\n" for package, error in lines)
    return {"context": context, "style": "error", "body_html": f"<ul>\n{items}</ul>\n"}


def soft_failed_build(shell: FakeShell, sha: str, number: str, *lines: tuple[str, str]) -> None:
    built(shell, sha, number, FULL_BUILD)
    shell.annotations[number] = [build_failed_annotation(*lines)] if lines else []


def test_build_failures_in_reads_each_package_and_its_first_error_from_the_annotation_html():
    body = build_failed_annotation(("sand/next/internal/localrun", LOCALRUN_ERROR), ("sand/next/cmd/runtime-api", 'go/sand/next/cmd/runtime-api/main.go:31:2: package "x" & <y> is not in std'))["body_html"]
    assert runner_module.build_failures_in(body) == {"sand/next/internal/localrun": LOCALRUN_ERROR, "sand/next/cmd/runtime-api": 'go/sand/next/cmd/runtime-api/main.go:31:2: package "x" & <y> is not in std'}


@pytest.mark.parametrize(
    ("package", "directory", "other", "path"),
    [
        ("sand/next/internal/localrun", "go/sand/next/internal/localrun", "go/sand/next/internal/vm_run", "sand/next/internal/vm_run"),
        ("example.com/svc/api", "services/svc/api", "services/svc/store", "example.com/svc/store"),
        ("example.com/svc/api", "services/svc/api", "services/svc", "example.com/svc"),
        ("example.com/svc/api", "services/svc/api", "tools/other", None),
        ("sand/next/internal/runtime-api/server", "go/sand/next/internal/runtime-api/sessions", "go/sand/next/internal/runtime-api/sessions/store", None),
    ],
)
def test_import_path_maps_a_directory_to_its_import_path_by_the_failing_packages_module(package, directory, other, path):
    assert runner_module.import_path(package, directory, other) == path


def test_a_head_whose_build_cannot_compile_a_package_it_changes_is_held_until_a_new_head_builds_it(shell, config, tmp_path):
    build_failed_checked(config)
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    shell.files["29020"] = ["go/sand/next/internal/localrun/localrun.go"]
    soft_failed_build(shell, "cccc333333", "85356", ("sand/next/internal/localrun", LOCALRUN_ERROR))
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == []
    [blocker] = blockers(shell)
    assert blocker["to"] == LANE and blocker["topic"] == "29020"
    assert blocker["text"] == f"#29020 cccc333333: nothing in #29020 was enqueued; BUILD-FAILED #29020: sand/next/internal/localrun fails to build at cccc333 on https://buildkite.com/forge/test/builds/85356: {LOCALRUN_ERROR}"
    shell.rows[0] |= {"head": "dddd444444", "reported_head": "dddd444444"}
    shell.gates["29020"] = [gate("#29020 GREEN dddd444444 graphite READY", would="#29020")]
    grade(shell, "29020", "ok F307 at dddd444.")
    shell.files["29020"] = ["go/sand/next/internal/localrun/localrun.go"]
    soft_failed_build(shell, "dddd444444", "85400")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]]


@pytest.mark.parametrize(("imports", "held"), [(True, True), (False, False)], ids=["importer", "unrelated"])
def test_a_package_that_fails_to_build_holds_the_head_only_when_it_imports_a_package_the_pr_changes(shell, config, tmp_path, imports, held):
    build_failed_checked(config)
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    shell.files["29020"] = ["go/sand/next/internal/vm_run/reader.go", "go/sand/next/internal/vm_run/reader_test.go"]
    if imports:
        shell.go_imports.add(("cccc333333", "go/sand/next/internal/localrun", "sand/next/internal/vm_run"))
    soft_failed_build(shell, "cccc333333", "85356", ("sand/next/internal/localrun", LOCALRUN_ERROR))
    landing_pass(shell, config)
    assert (shell.enqueues() == []) == held
    assert [blocker["text"].split("; ", 1)[1].split(" fails")[0] for blocker in blockers(shell)] == (["BUILD-FAILED #29020: sand/next/internal/localrun"] if held else [])
    assert "cccc333333" in shell.local_heads


@pytest.mark.parametrize(
    ("base_error", "held"),
    [(LOCALRUN_ERROR, False), ("go/sand/next/internal/localrun/localrun.go:12:2: undefined: vmrun.Open", True)],
    ids=["inherited", "introduced"],
)
def test_a_build_failure_its_merge_base_fails_with_the_same_error_is_inherited_and_holds_nothing(shell, config, tmp_path, base_error, held):
    build_failed_checked(config)
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    shell.files["29020"] = ["go/sand/next/internal/vm_run/reader.go"]
    shell.go_imports.add(("cccc333333", "go/sand/next/internal/localrun", "sand/next/internal/vm_run"))
    shell.merge_bases["cccc333333"] = "bbbb222222"
    soft_failed_build(shell, "bbbb222222", "85300", ("sand/next/internal/localrun", base_error))
    soft_failed_build(shell, "cccc333333", "85356", ("sand/next/internal/localrun", LOCALRUN_ERROR))
    landing_pass(shell, config)
    inherited = [post for post in notes(shell) if "INHERITED-BUILD-FAILURE" in post["text"]]
    if held:
        assert shell.enqueues() == [] and not inherited
        assert [blocker["text"].split("; ", 1)[1].split(" fails")[0] for blocker in blockers(shell)] == ["BUILD-FAILED #29020: sand/next/internal/localrun"]
        return
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and blockers(shell) == []
    [note] = inherited
    assert note["to"] == LANE
    assert note["text"].endswith(f"WARNING INHERITED-BUILD-FAILURE #29020, and the head is not held for it: sand/next/internal/localrun fails to build at cccc333 on https://buildkite.com/forge/test/builds/85356: {LOCALRUN_ERROR}, as it does at its base bbbb222 on https://buildkite.com/forge/test/builds/85300")


def test_a_head_that_changes_nothing_in_the_tree_is_never_held_for_its_build_failures(shell, config, tmp_path):
    build_failed_checked(config)
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    shell.files["29020"] = ["go/sand/internal/localrun/localrun.go", "api/src/index.ts"]
    soft_failed_build(shell, "cccc333333", "85356", ("sand/next/internal/localrun", LOCALRUN_ERROR))
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and blockers(shell) == []
    assert not [call for call in shell.calls if call[-1].endswith("/annotations")]


def test_the_build_failed_check_without_its_config_refuses_to_start(shell, config, tmp_path):
    raw = json.loads(config.read_text())
    raw["landing"]["checks"] = [*runner_module.merge_queue.CHECKS, "build-failed"]
    assert "runs the build-failed check with no landing.build_failures or no landing.ci_build" in runner_module.mq_config_fault(raw["landing"])


DESK = "landing-desk-3"


def desk_lane(config: Path) -> None:
    raw = json.loads(config.read_text())
    raw["landing"]["desk_lane"] = DESK
    config.write_text(json.dumps(raw))


def heard(shell: FakeShell, label: str) -> list[str]:
    return [post["to"] for post in shell.posts if post_body(post).startswith(label)]


def test_with_a_desk_lane_queued_and_landed_go_to_the_desk_and_the_owning_lane_and_never_to_root(shell, config, tmp_path):
    merge_mode(config, "every")
    desk_lane(config)
    gone_green(shell, "#33001 GREEN aaaa111111 graphite READY")
    landing_pass(shell, config)
    shell.rows[0] |= {"state": "landed", "landed_sha": "dddd444444"}
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert (heard(shell, "QUEUED"), heard(shell, "LANDED")) == ([f"{DESK},{LANE}"], [f"{DESK},{LANE}"])


def test_with_a_desk_lane_a_merge_ask_skips_root(shell, config, tmp_path):
    board_surface(config)
    desk_lane(config)
    lone_green_pr(shell)
    shell.board_rows = [("29020", "cccc333333")]
    shell.queue["29020"] = "not queued"
    shell.pr_status["29020"] = {"verdict": "landable"}
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert heard(shell, "MERGE-ASK") == [f"{DESK},{LANE}"] and escalations(shell) == []


def test_with_a_desk_lane_a_below_blocked_note_skips_root(shell, config, tmp_path):
    board_surface(config)
    desk_lane(config)
    two_pr_stack(shell)
    merge_go(shell, "29031", "merge #29031 at dddd444")
    landing_pass(shell, config)
    assert heard(shell, "BELOW-BLOCKED") == [f"{DESK},{LANE}"]


def test_with_a_desk_lane_a_carried_tap_tells_the_desk_the_board_and_the_owning_lane(shell, config, tmp_path):
    board_surface(config)
    board_lane(config)
    desk_lane(config)
    lone_green_pr(shell)
    tap(shell, "29020", "aaaa111111")
    shell.diffs["aaaa111111"] = shell.diffs["cccc333333"] = "diff --git a/x.go b/x.go\n+same\n"
    head_record(shell, "29020", "HEAD #29020 cccc333333 (was aaaa111111): restack onto dev, range-diff patch-identical (3 commits)")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert heard(shell, "TAP-CARRIED") == [f"{DESK},board-refresher,{LANE}"]


def test_with_a_desk_lane_every_go_unqueued_goes_to_the_desk_and_the_owning_lane_never_root(shell, config, tmp_path):
    merge_mode(config, "every")
    desk_lane(config)
    gone_failing(shell)
    shell.queue["33001"] = "not queued"
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    shell.rows[0] |= {"head": "bbbb222222", "reported_head": "bbbb222222"}
    shell.gates["33001"] = [gate("#33001 GREEN bbbb222222 graphite READY", would="#33001")]
    grade(shell, "33001", "ok F307 at bbbb222.")
    merge_go(shell, "33001", "merge #33001 at bbbb222")
    shell.sleep(6 * 60)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert heard(shell, "GO-UNQUEUED") == [f"{DESK},{LANE}", f"{DESK},{LANE}"]


def test_with_a_desk_lane_a_fork_deferred_batch_skips_root(shell, config, tmp_path):
    merge_mode(config, "every")
    desk_lane(config)
    heads, green, queued = five_pr_fork(shell)
    shell.gates["31003"] = [gate(*green.values()), gate(green["31001"], green["31002"], green["31003"], would="#31001 #31002 #31003")]
    for pr in ("31001", "31002", "31003"):
        grade(shell, pr, f"ok F307 at {heads[pr][:7]}.")
    landing_pass(shell, config)
    for pr in ("31004", "31005"):
        grade(shell, pr, f"ok F307 at {heads[pr][:7]}.")
    shell.gates["31003"] = [gate(*queued.values(), green["31004"], green["31005"])]
    shell.gates["31003 31005"] = [gate(*queued.values(), green["31004"], green["31005"], would="#31004 #31005")]
    landing_pass(shell, config)
    [to] = heard(shell, "FORK-DEFERRED")
    assert to.split(",")[0] == DESK and "root" not in to.split(",")


def test_book_edit_decodes_current_incident_once(shell, tmp_path, monkeypatch):
    store = actions.Store(tmp_path / "store")
    store.create(actions.Incident("book", "owner", 3, actions.stamp(shell.now()), {"cursor": 7}))
    book = runner_module.Book(store, shell)
    decoded = []
    load = actions.Incident.load

    def counted(record):
        decoded.append(record["incident_id"])
        return load(record)

    monkeypatch.setattr(actions.Incident, "load", staticmethod(counted))
    assert book.edit("book", lambda incident: incident.facts.pop("cursor")) == 7
    assert decoded == ["book"]
    assert store.load("book").facts == {}


@pytest.mark.parametrize("edit", ["pop", "setdefault", "update", "nested"])
def test_book_noop_preserves_incident_file(shell, tmp_path, monkeypatch, edit):
    store = actions.Store(tmp_path / "store")
    store.create(actions.Incident("book", "owner", 3, actions.stamp(shell.now()), {"cursor": 7, "nested": {"x": [1]}}))
    path = store.path("book")
    before, info = path.read_bytes(), path.stat()
    writes = []
    write = Path.write_text

    def counted(target, text, *args, **kwargs):
        if target.parent == path.parent:
            writes.append(target.name)
        return write(target, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", counted)
    changes = {
        "pop": lambda incident: incident.facts.pop("missing", None),
        "setdefault": lambda incident: incident.facts.setdefault("cursor", 9),
        "update": lambda incident: incident.facts.update({"cursor": 7}),
        "nested": lambda incident: (incident.facts["nested"]["x"].append(2), incident.facts["nested"]["x"].pop()),
    }
    runner_module.Book(store, shell).edit("book", changes[edit])
    assert writes == []
    assert path.read_bytes() == before
    assert (path.stat().st_ino, path.stat().st_mtime_ns) == (info.st_ino, info.st_mtime_ns)
    assert store.load("book").owner_generation == 3


def test_book_edit_retries_callback_stale_generation_without_persisting_partial_change(shell, tmp_path):
    store = actions.Store(tmp_path / "store")
    store.create(actions.Incident("book", "owner", 3, actions.stamp(shell.now()), {"cursor": 7}))
    attempts = []

    def change(incident):
        attempts.append(incident.facts["cursor"])
        incident.facts["cursor"] = 8
        if len(attempts) == 1:
            raise actions.StaleGeneration("callback stale")
        return "done"

    assert runner_module.Book(store, shell).edit("book", change) == "done"
    assert attempts == [7, 7]
    assert store.load("book").facts == {"cursor": 8}


def test_book_edit_failure_preserves_bytes_and_missing_diagnostic(shell, tmp_path):
    store = actions.Store(tmp_path / "store")
    store.create(actions.Incident("book", "owner", 3, actions.stamp(shell.now()), {"cursor": 7}))
    path = store.path("book")
    before, info = path.read_bytes(), path.stat()
    book = runner_module.Book(store, shell)

    def change(incident):
        incident.facts["cursor"] = 8
        raise ValueError("refused")

    with pytest.raises(ValueError, match="refused"):
        book.edit("book", change)
    assert path.read_bytes() == before
    assert (path.stat().st_ino, path.stat().st_mtime_ns) == (info.st_ino, info.st_mtime_ns)
    with pytest.raises(SystemExit, match=f"no incident missing in {store.root}"):
        book.edit("missing", change)


def test_book_edit_uses_current_owner_while_explicit_generation_still_refuses(shell, tmp_path):
    store = actions.Store(tmp_path / "store")
    store.create(actions.Incident("book", "old", 3, actions.stamp(shell.now()), {}))
    store.transfer("book", 3, "new")
    store.ack("book", "new")
    book = runner_module.Book(store, shell)
    assert book.edit("book", lambda incident: (incident.owner, incident.owner_generation)) == ("new", 4)
    with pytest.raises(actions.StaleGeneration):
        with store.owned("book", 3):
            pytest.fail("stale owner entered")
    assert not book.as_owner("book", "old", lambda incident: incident.facts.update({"old": True}))
    assert store.load("book").facts == {}


def test_book_edit_holds_the_incident_lock_through_the_callback(shell, tmp_path):
    store = actions.Store(tmp_path / "store")
    store.create(actions.Incident("book", "owner", 3, actions.stamp(shell.now()), {}))

    def change(incident):
        with open(store.root / ".book.lock", "w") as lock:
            with pytest.raises(BlockingIOError):
                actions.fcntl.flock(lock, actions.fcntl.LOCK_EX | actions.fcntl.LOCK_NB)
        incident.facts["locked"] = True

    runner_module.Book(store, shell).edit("book", change)
    assert store.load("book").facts == {"locked": True}


def test_owned_noop_preserves_bytes_and_nested_mutation_is_persisted(shell, tmp_path):
    store = actions.Store(tmp_path / "store")
    store.create(actions.Incident("book", "owner", 3, actions.stamp(shell.now()), {"nested": {"x": [1]}}))
    path = store.path("book")
    before, info = path.read_bytes(), path.stat()
    with store.owned("book", 3) as incident:
        incident.facts["nested"]["x"] = [1]
    assert path.read_bytes() == before
    assert (path.stat().st_ino, path.stat().st_mtime_ns) == (info.st_ino, info.st_mtime_ns)
    with store.owned("book", 3) as incident:
        incident.facts["nested"]["x"].append(2)
    assert store.load("book").facts == {"nested": {"x": [1, 2]}}
    assert path.stat().st_ino != info.st_ino


def test_book_noop_canonicalizes_legacy_format_once(shell, tmp_path):
    store = actions.Store(tmp_path / "store")
    store.create(actions.Incident("book", "owner", 3, actions.stamp(shell.now()), {}))
    path = store.path("book")
    record = json.loads(path.read_text())
    path.write_text(json.dumps(record))
    book = runner_module.Book(store, shell)
    book.edit("book", lambda incident: None)
    expected = json.dumps(record, indent=2) + "\n"
    assert path.read_text() == expected
    info = path.stat()
    book.edit("book", lambda incident: None)
    assert path.read_text() == expected
    assert (path.stat().st_ino, path.stat().st_mtime_ns) == (info.st_ino, info.st_mtime_ns)


def test_inputs_noop_still_advances_input_revision(shell, tmp_path):
    store = actions.Store(tmp_path / "store")
    store.create(actions.Incident("book", "owner", 3, actions.stamp(shell.now()), {}))
    with store.inputs("book"):
        pass
    assert store.load("book").input_revision == 2


def queue_drafts(shell: FakeShell, *drafts: tuple[int, bool]) -> None:
    """Graphite's queue drafts, newest first, each as how many minutes ago it opened and whether it is still open."""
    shell.queue_drafts = [{"createdAt": actions.stamp(shell.clock - timedelta(minutes=age)), "closedAt": None if still_open else actions.stamp(shell.clock)} for age, still_open in drafts]


def graphite_posts(shell: FakeShell) -> list[str]:
    return [post["text"].split(":")[0].split(" since")[0] for post in shell.posts if post["kind"] == "state" and post["text"].startswith("GRAPHITE-")]


def direct_merges(shell: FakeShell) -> list[list[str]]:
    return [call for call in shell.calls if call[:3] == ["gh", "pr", "merge"] or call[:2] == ["gh", "stack"] or call[:4] == ["gh", "api", "-X", "PUT"] and call[4].endswith("/merge-async")]


def merged_records(shell: FakeShell) -> list[str]:
    return [post["text"] for post in shell.posts if post["kind"] == "state" and post["text"].startswith("MERGED ")]


@pytest.mark.parametrize(
    ("drafts", "queued", "down"),
    [
        ([(40, True), (50, True)], "not queued", True),
        ([(20, False), (60, False)], "queued", True),
        ([(20, False), (60, False)], "not queued", False),
        ([(5, True), (50, True)], "queued", False),
        ([(40, True), (50, False)], "not queued", False),
        (None, "queued", False),
    ],
    ids=["two-batches-timed-out", "no-batch-while-queued", "no-batch-nothing-queued", "fresh-batch", "one-timeout", "drafts-unread"],
)
def test_graphite_reads_down_when_its_two_newest_batches_time_out_or_none_starts_while_prs_wait(shell, config, tmp_path, drafts, queued, down):
    """Owner 7:50 PM Oct 29 (b530f5d): Graphite timed out every batch after 6:50 PM, so the runner merges directly while it is down."""
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    shell.queue["29020"] = queued
    if drafts is not None:
        queue_drafts(shell, *drafts)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert graphite_posts(shell) == (["GRAPHITE-DOWN"] if down else [])
    assert bool(direct_merges(shell)) == down and bool(shell.enqueues()) != down


def test_graphite_reads_up_once_a_batch_starts_after_it_went_down_and_the_runner_enqueues_again(shell, config, tmp_path):
    lone_green_pr(shell)
    queue_drafts(shell, (40, True), (50, True))
    landing_pass(shell, config)
    assert graphite_posts(shell) == ["GRAPHITE-DOWN"]
    queue_drafts(shell, (1, True), (40, True))
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    assert graphite_posts(shell) == ["GRAPHITE-DOWN", "GRAPHITE-UP"]
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--partial"]] and direct_merges(shell) == []


def test_while_graphite_is_down_an_admitted_pr_squash_merges_at_its_head_instead_of_enqueueing(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    queue_drafts(shell, (40, True), (50, True))
    shell.merge_commits["29020"] = "9999aaaa0000"
    landing_pass(shell, config)
    assert shell.enqueues() == []
    assert direct_merges(shell) == [["gh", "pr", "merge", "29020", "--repo", "Forge-AI/monorepo", "--squash", "--match-head-commit", "cccc333333"]]
    assert merged_records(shell) == ["MERGED #29020 at cccc333333 direct as 9999aaaa00, Graphite down, b530f5d: gh pr merge --squash"]
    assert [post["pr"] for post in shell.posts if post["text"].startswith("MERGED ")] == ["29020"]
    assert [action.response["outcome"] for action in enqueues_of(tmp_path).values()] == ["merged"]


def test_while_graphite_is_down_a_pr_its_dead_queue_still_holds_merges_directly(shell, config, tmp_path):
    """Root #202044: #45016, #45367 and #45377 passed every check but read QUEUED in Graphite's dead queue, so the direct merger never took them."""
    lone_green_pr(shell)
    shell.gates["29020"] = [gate("#29020 QUEUED cccc333333 graphite WAITING_TO_MERGE")]
    shell.pr_status["29020"] = {"verdict": "queued"}
    grade(shell, "29020", "ok F307 at cccc333.")
    queue_drafts(shell, (40, True), (50, True))
    shell.merge_commits["29020"] = "9999aaaa0000"
    landing_pass(shell, config)
    assert direct_merges(shell) == [["gh", "pr", "merge", "29020", "--repo", "Forge-AI/monorepo", "--squash", "--match-head-commit", "cccc333333"]]
    assert merged_records(shell) == ["MERGED #29020 at cccc333333 direct as 9999aaaa00, Graphite down, b530f5d: gh pr merge --squash"]


def test_while_graphite_is_down_a_pr_the_queue_evicted_merges_directly_and_tells_no_lane(shell, config, tmp_path):
    """Root #208430: the queue evicted #45286 at 6:42 PM, 8 minutes before the runner read Graphite down, and the direct merger offered only the would-enqueue and QUEUED PRs, so it sat green with a go for 2 h 21 m."""
    lone_green_pr(shell)
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 graphite EVICTED; run ccx vcs stack submit from the branch's worktree: ccx vcs stack submit from a checkout of a/3")]
    shell.queue["29020"] = "evicted"
    grade(shell, "29020", "ok F307 at cccc333.")
    queue_drafts(shell, (40, True), (50, True))
    shell.merge_commits["29020"] = "9999aaaa0000"
    landing_pass(shell, config)
    assert direct_merges(shell) == [["gh", "pr", "merge", "29020", "--repo", "Forge-AI/monorepo", "--squash", "--match-head-commit", "cccc333333"]]
    assert blockers(shell) == []


@pytest.mark.parametrize("excluded", ["hold", "no-go", "red", "draft"])
def test_while_graphite_is_down_a_held_ungone_red_or_draft_pr_never_merges(shell, config, tmp_path, excluded):
    lone_green_pr(shell)
    queue_drafts(shell, (40, True), (50, True))
    if excluded == "no-go":
        merge_mode(config, "every")
    if excluded == "hold":
        root_hold(shell, 29020, text="hold #29020 (reader release): no enqueue until it is live")
    verdict = {"red": "BLOCKED cccc333333 graphite FAILING", "draft": "BLOCKED cccc333333 draft"}.get(excluded)
    if verdict:
        shell.gates["29020"] = [gate(f"#29020 {verdict}")]
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert direct_merges(shell) == [] and shell.enqueues() == [] and merged_records(shell) == []


def test_while_graphite_is_down_a_stack_rebased_on_dev_merges_as_one_gh_stack_bottom_first(shell, config, tmp_path):
    """Owner 8:15 PM Oct 29 (e7170fa): for direct merges always use stacks and always rebase first."""
    two_pr_stack(shell)
    queue_drafts(shell, (40, True), (50, True))
    shell.current_heads |= {"29020": "cccc333333", "29031": "dddd444444"}
    shell.merge_commits |= {"29020": "1111aaaa0000", "29031": "2222bbbb0000"}
    landing_pass(shell, config)
    assert direct_merges(shell) == [["gh", "stack", "link", "29020", "29031"], ["gh", "stack", "merge", "29031", "--yes", "--squash"]]
    assert merged_records(shell) == [f"MERGED #{pr} at {head} direct as {squash}, Graphite down, b530f5d: gh stack merge of #29020 #29031" for pr, head, squash in (("29020", "cccc333333", "1111aaaa00"), ("29031", "dddd444444", "2222bbbb00"))]
    compared = [call[2].rsplit("/", 1)[1] for call in shell.calls if call[:2] == ["gh", "api"] and call[3:] == ["--jq", ".behind_by"]]
    assert compared == ["dev...cccc333333", "cccc333333...dddd444444"]


def test_while_graphite_is_down_a_stack_waits_for_its_required_checks_before_the_merge_and_merges_once_they_pass(shell, config, tmp_path):
    """Root #198761, #198874: the runner merged right after its own rebase push, and GitHub refused every stack with 'Required status check "buildkite/test" is pending'."""
    two_pr_stack(shell)
    queue_drafts(shell, (40, True), (50, True))
    shell.current_heads |= {"29020": "cccc333333", "29031": "dddd444444"}
    shell.merge_commits |= {"29020": "1111aaaa0000", "29031": "2222bbbb0000"}
    shell.checks |= {pr: [{"name": "buildkite/test", "bucket": "pending"}] for pr in ("29020", "29031")}
    landing_pass(shell, config)
    assert direct_merges(shell) == [] and merged_records(shell) == []
    shell.checks |= {pr: [{"name": "buildkite/test", "bucket": "pass"}] for pr in ("29020", "29031")}
    landing_pass(shell, config)
    assert direct_merges(shell) == [["gh", "stack", "link", "29020", "29031"], ["gh", "stack", "merge", "29031", "--yes", "--squash"]]


def test_while_graphite_is_down_the_runner_posts_the_queue_of_admitted_chains_once_per_change(shell, config, tmp_path):
    """Root #200367: lanes asked root where their stack stood in the one serial queue."""
    two_pr_stack(shell)
    queue_drafts(shell, (40, True), (50, True))
    shell.current_heads |= {"29020": "cccc333333", "29031": "dddd444444"}
    shell.checks |= {pr: [{"name": "buildkite/test", "bucket": "pending"}] for pr in ("29020", "29031")}

    def queues():
        return [post["text"] for post in shell.posts if post["kind"] == "state" and post["text"].startswith("QUEUE ")]

    landing_pass(shell, config)
    landing_pass(shell, config)
    [queue] = queues()
    assert "Graphite is down, so the landing runner merges one chain at a time, in this order:\n1. #29020 #29031 at cccc333 dddd444: CI pending: " in queue
    shell.checks |= {pr: [{"name": "buildkite/test", "bucket": "fail"}] for pr in ("29020", "29031")}
    landing_pass(shell, config)
    assert len(queues()) == 2 and "CI red" in queues()[-1]


def test_a_handoff_ends_a_direct_merge_wait_on_checks_so_the_runner_exits_at_once(shell, config, tmp_path):
    """Oct 30 1:02 AM: a 0.7.624 landing runner held its handoff past ten minutes while three chains waited their turn on CI, and its attempts read DEADLINE at 15 minutes."""
    two_pr_stack(shell)
    queue_drafts(shell, (40, True), (50, True))
    shell.current_heads |= {"29020": "cccc333333", "29031": "dddd444444"}
    shell.checks |= {pr: [{"name": "buildkite/test", "bucket": "pending"}] for pr in ("29020", "29031")}
    landing = landing_of(shell, config, tmp_path)
    run = shell.run

    def handed_off(argv, *args, **kwargs):
        done = run(argv, *args, **kwargs)
        if "checks" in argv and "--required" not in argv:
            landing.runner.stop_marker().write_text(str(os.getpid()))
        return done

    shell.run = handed_off
    with contextlib.suppress(runner_module.StopRequested):
        landing.run()
    [action] = enqueues_of(tmp_path).values()
    assert direct_merges(shell) == [] and action.response["outcome"] == "blocked" and runner_module.HANDING_OFF in action.response["why"]
    assert actions.parse_stamp(action.deadline) - actions.parse_stamp(action.accepted_at) == runner_module.DIRECT_DEADLINE


def test_a_go_withdrawn_while_a_stack_waits_for_its_checks_keeps_the_stack_unmerged(shell, config, tmp_path):
    """Root #199069: the direct merge waits for checks for minutes, so a go a root record withdraws meanwhile must still stop the merge."""
    board_surface(config)
    merge_mode(config, "every")
    two_pr_stack(shell)
    queue_drafts(shell, (40, True), (50, True))
    shell.current_heads |= {"29020": "cccc333333", "29031": "dddd444444"}
    merge_go(shell, "29020", "go #29020 at cccc333")
    merge_go(shell, "29031", "go #29031 at dddd444")
    shell.checks |= {pr: [{"name": "buildkite/test", "bucket": "pending"}] for pr in ("29020", "29031")}

    def withdraw():
        shell.records.append({"seq": 34766 + len(shell.records), "drive": "d1", "lane": "root", "kind": "withdraw", "re": shell.records[-1]["seq"], "to": ["desk-runner"], "refs": {"prs": [29031]}, "text": "withdraw the go on #29031: new behavior since the tap", "at": actions.stamp(shell.clock)})
        shell.checks |= {pr: [{"name": "buildkite/test", "bucket": "pass"}] for pr in ("29020", "29031")}

    shell.on_sleep.append(withdraw)
    landing_pass(shell, config)
    assert [call for call in direct_merges(shell) if call[:3] == ["gh", "stack", "merge"]] == []


def partial_prefix(shell: FakeShell) -> None:
    """Root #199186: gh stack link refused 'Include all existing PRs in the command to update the stack' for 4 of #44828's 7 PRs."""
    two_pr_stack(shell)
    shell.rows.append({"pr": "29042", "lane": LANE, "branch": "a/3", "base": "a/2", "head": "eeee555555", "state": "open", "reported_head": "eeee555555"})
    shell.gates["29042"] = [gate("#29020 GREEN cccc333333 graphite READY", "#29031 GREEN dddd444444 graphite READY", "#29042 BLOCKED eeee555555 graphite RUNNING", would="#29020 #29031")]
    queue_drafts(shell, (40, True), (50, True))
    shell.current_heads |= {"29020": "cccc333333", "29031": "dddd444444", "29042": "eeee555555"}
    shell.merge_commits |= {"29020": "1111aaaa0000", "29031": "2222bbbb0000"}
    shell.link_out = "Include all existing PRs in the command to update the stack"
    shell.bases["29031"] = "a/1"


def test_while_graphite_is_down_a_prefix_inside_a_longer_stack_merges_bottom_up_each_pr_once_github_retargets_it_onto_dev(shell, config, tmp_path):
    """Root #200822: the asynchronous merge of #44923, the prefix's top, squashed it into its parent's branch instead of dev."""
    partial_prefix(shell)
    shell.stack_members = {"29031"}
    shell.on_sleep.append(lambda: shell.bases.pop("29031", None))
    landing_pass(shell, config)
    assert [call for call in direct_merges(shell) if call[:2] != ["gh", "stack"]] == [
        ["gh", "pr", "merge", "29020", "--repo", "Forge-AI/monorepo", "--squash", "--match-head-commit", "cccc333333"],
        ["gh", "pr", "merge", "29031", "--repo", "Forge-AI/monorepo", "--squash", "--match-head-commit", "dddd444444"],
        ["gh", "api", "-X", "PUT", "repos/Forge-AI/monorepo/pulls/29031/merge-async", "-f", "merge_method=squash", "-f", "merge_action=direct_merge", "-f", "sha=dddd444444"],
    ]
    assert [record.split(" direct")[0] for record in merged_records(shell)] == ["MERGED #29020 at cccc333333", "MERGED #29031 at dddd444444"]


@pytest.mark.parametrize("moved", [False, True], ids=["still-open", "head-moved"])
def test_an_asynchronous_merge_that_does_not_land_fails_its_attempt_naming_why(shell, config, tmp_path, moved):
    """Root #202936, #203042: GitHub accepted #45010's asynchronous merge at 541fbe47 after its head had moved to 7e5a512, and only the PR still reading open told the runner."""
    partial_prefix(shell)
    shell.bases.pop("29031")
    shell.stack_members = {"29031"}
    shell.async_pending = {"29031"}
    if moved:
        shell.on_sleep.append(lambda: shell.current_heads.update({"29031": "ffff888888"}))
    landing_pass(shell, config)
    [action] = enqueues_of(tmp_path).values()
    assert [record.split(" direct")[0] for record in merged_records(shell)] == ["MERGED #29020 at cccc333333"]
    if moved:
        assert action.response["outcome"] == "failed" and "#29031's head moved to ffff888888 after the asynchronous merge was asked at dddd444444" in action.response["why"]
    else:
        assert action.response["outcome"] == "failed" and "GitHub accepted the asynchronous merge of #29031 at dddd444444, but it has not merged after 10 minutes" in action.response["why"]


def test_a_pr_whose_head_moved_before_its_direct_merge_is_refused_unmerged(shell, config, tmp_path):
    partial_prefix(shell)
    shell.bases.pop("29031")
    run = shell.run

    def pushed(argv, *args, **kwargs):
        if argv[:4] == ["gh", "pr", "merge", "29020"]:
            shell.current_heads["29031"] = "ffff888888"
        return run(argv, *args, **kwargs)

    shell.run = pushed
    landing_pass(shell, config)
    assert [call[3] for call in direct_merges(shell) if call[:3] == ["gh", "pr", "merge"]] == ["29020"]
    [action] = enqueues_of(tmp_path).values()
    assert "refused: #29031's head moved to ffff888888 from the admitted dddd444444" in action.response["why"]


def test_while_graphite_is_down_a_pr_github_never_retargets_onto_dev_is_refused_and_never_merged_into_its_parent(shell, config, tmp_path):
    partial_prefix(shell)
    landing_pass(shell, config)
    assert [call[3] for call in direct_merges(shell) if call[:3] == ["gh", "pr", "merge"]] == ["29020"]
    assert not [call for call in direct_merges(shell) if call[:4] == ["gh", "api", "-X", "PUT"]]
    assert [record.split(" direct")[0] for record in merged_records(shell)] == ["MERGED #29020 at cccc333333"]
    [action] = enqueues_of(tmp_path).values()
    assert action.response["outcome"] == "failed" and "refused: #29031 still reads base a/1, not dev" in action.response["out"]


@pytest.mark.parametrize("overlap", [False, True], ids=["dev-moved-elsewhere", "dev-touched-its-files"])
def test_while_graphite_is_down_a_chain_the_runner_rebased_goes_onto_dev_again_only_when_dev_touches_its_files(shell, config, tmp_path, overlap):
    """Root #199733: dev moved three times in 20 minutes and the merger rebased the #44828 chain onto each, restarting its 40-minute CI every time."""
    two_pr_stack(shell)
    queue_drafts(shell, (40, True), (50, True))
    shell.trunk = [{"oid": "9999dddd00"}]
    shell.behind["cccc333333"] = 2
    shell.checks |= {pr: [{"name": "buildkite/test", "bucket": "pending"}] for pr in ("29020", "29031")}
    landing_pass(shell, config)
    rebased = {"29020": shell.replayed[0][2], "29031": shell.replayed[1][2]}
    for row in shell.rows:
        row["head"] = row["reported_head"] = rebased[row["pr"]]
    shell.gates["29031"] = [gate(f"#29020 GREEN {rebased['29020']} graphite READY", f"#29031 GREEN {rebased['29031']} graphite READY", would="#29020 #29031")]
    for pr, head in rebased.items():
        grade(shell, pr, f"ok F307 at {head[:7]}.")
    shell.trunk = [{"oid": "8888eeee00"}]
    shell.behind[rebased["29020"]] = 1
    shell.diff_names |= {"8888eeee00": ["go/sand/next/vm/run.go" if overlap else "dashboard/src/app.tsx"], rebased["29031"]: ["go/sand/next/vm/run.go"]}
    shell.checks |= {pr: [{"name": "buildkite/test", "bucket": "pass"}] for pr in ("29020", "29031")}
    landing_pass(shell, config)
    assert len(shell.replayed) == (4 if overlap else 2)


def test_a_direct_merge_rebase_moves_a_pr_stacked_above_the_admitted_prefix_with_it(shell, config, tmp_path):
    """stack-prep-3 #199394, root #199395: the rebase moved #44922 and left #45010 on its old head, so GitHub's diff of #45010 carried #44922's code and a test-only PR read TEST-ONLY-MISMATCH."""
    two_pr_stack(shell)
    shell.rows.append({"pr": "29042", "lane": LANE, "branch": "a/3", "base": "a/2", "head": "eeee555555", "state": "open", "reported_head": "eeee555555"})
    shell.gates["29042"] = [gate("#29020 GREEN cccc333333 graphite READY", "#29031 GREEN dddd444444 graphite READY", "#29042 BLOCKED eeee555555 graphite RUNNING", would="#29020 #29031")]
    queue_drafts(shell, (40, True), (50, True))
    shell.trunk = [{"oid": "9999dddd00"}]
    shell.behind["cccc333333"] = 2
    landing_pass(shell, config)
    assert [(onto, head) for onto, head, _ in shell.replayed] == [("9999dddd00", "cccc333333"), (shell.replayed[0][2], "dddd444444"), (shell.replayed[1][2], "eeee555555")]
    assert shell.pushes[1] == ["--no-verify", "--force-with-lease=refs/heads/a/3:eeee555555", "origin", f"{shell.replayed[2][2]}:refs/heads/a/3"]
    assert [post["text"].split(" (was")[0] for post in shell.posts if post["kind"] == "head" and "29042" in post["text"]] == [f"HEAD #29042 {shell.replayed[2][2]}"]


def test_a_failed_direct_merge_names_githubs_whole_refusal_not_its_last_line(shell, config, tmp_path):
    two_pr_stack(shell)
    queue_drafts(shell, (40, True), (50, True))
    shell.current_heads |= {"29020": "cccc333333", "29031": "dddd444444"}
    shell.stack_code = 1
    shell.stack_merge_out = ('Merging #29020, #29031 into dev via squash...\n✗ merge failed: Required status check "buildkite/test" is pending.\nStack merges are atomic, so nothing was merged.\n')
    landing_pass(shell, config)
    failed = [post_body(post) for post in shell.posts if "DIRECT-MERGE-FAILED" in post_body(post)]
    assert failed and 'Required status check "buildkite/test" is pending.' in failed[0]


@pytest.mark.parametrize("behind", ["cccc333333", "dddd444444"], ids=["bottom-behind-dev", "child-behind-parent"])
def test_while_graphite_is_down_a_stack_behind_its_base_is_rebased_onto_the_trunk_tip_pushed_and_merged_by_the_runner(shell, config, tmp_path, behind):
    """Owner 8:15 PM Oct 29 (e7170fa), root #197237: the merger rebases onto the latest dev itself, since dev moves faster than lanes restack."""
    two_pr_stack(shell)
    queue_drafts(shell, (40, True), (50, True))
    shell.trunk = [{"oid": "9999dddd00"}]
    shell.behind[behind] = 2
    landing_pass(shell, config)
    assert [(onto, head) for onto, head, _ in shell.replayed] == [("9999dddd00", "cccc333333"), (shell.replayed[0][2], "dddd444444")]
    rebased = {"29020": shell.replayed[0][2], "29031": shell.replayed[1][2]}
    assert shell.pushes == [["--atomic", "--no-verify", "--force-with-lease=refs/heads/a/1:cccc333333", "--force-with-lease=refs/heads/a/2:dddd444444", "origin", f"{rebased['29020']}:refs/heads/a/1", f"{rebased['29031']}:refs/heads/a/2"]]
    assert direct_merges(shell) == [["gh", "stack", "link", "29020", "29031"], ["gh", "stack", "merge", "29031", "--yes", "--squash"]]
    assert [record.split(" direct")[0] for record in merged_records(shell)] == [f"MERGED #29020 at cccc333333, rebased to {rebased['29020']},", f"MERGED #29031 at dddd444444, rebased to {rebased['29031']},"]


@pytest.mark.parametrize("refusal", ["conflict", "patch-changes"])
def test_while_graphite_is_down_a_stack_whose_rebase_conflicts_or_changes_its_patch_pushes_nothing_and_goes_to_its_lane_once(shell, config, tmp_path, refusal):
    two_pr_stack(shell)
    queue_drafts(shell, (40, True), (50, True))
    shell.trunk = [{"oid": "9999dddd00"}]
    shell.behind["cccc333333"] = 2
    (shell.replay_conflicts if refusal == "conflict" else shell.patch_changes).add("dddd444444")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.pushes == [] and direct_merges(shell) == [] and merged_records(shell) == []
    asked = [post_body(post) for post in blockers(shell) if "rebases each admitted stack" in post_body(post)]
    assert len(asked) == 1 and asked[0].startswith("#29031 dddd444444: Graphite is down")


def test_while_graphite_is_down_a_stack_merge_github_refuses_escalates_and_posts_no_merged_record(shell, config, tmp_path):
    two_pr_stack(shell)
    queue_drafts(shell, (40, True), (50, True))
    shell.current_heads |= {"29020": "cccc333333", "29031": "dddd444444"}
    shell.stack_code = 4
    landing_pass(shell, config)
    assert merged_records(shell) == []
    failed = [line for line in escalations(shell) if line.startswith("DIRECT-MERGE-FAILED")]
    assert len(failed) == 1 and failed[0].endswith("#29031: a pull request in the stack is not mergeable")


def test_an_enqueue_of_a_pr_another_lane_already_merged_settles_it_landed_and_enqueues_nothing(shell, config, tmp_path):
    """Root #196021: the runner tried to enqueue #45374 after landing-desk-3 merged it directly, and stack-enqueue failed on its deleted branch."""
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    shell.squashed = {"29020"}
    landing_pass(shell, config)
    assert shell.enqueues() == [] and direct_merges(shell) == []
    assert [action.response["outcome"] for action in enqueues_of(tmp_path).values()] == ["landed"]


def test_while_graphite_is_down_two_admitted_prs_merge_one_at_a_time_and_the_second_rebases_onto_the_first_squash(shell, config, tmp_path):
    """Owner 9:00 PM Oct 29: when you are bypassing graphite MQ, your MQ needs to do things serially, one at a time, and always rebased on latest first."""
    shell.rows = [
        {"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "29100", "lane": LANE, "branch": "b/1", "base": "dev", "head": "eeee555555", "state": "open", "reported_head": "eeee555555"},
    ]
    for pr, head in (("29020", "cccc333333"), ("29100", "eeee555555")):
        shell.gates[pr] = [gate(f"#{pr} GREEN {head} graphite READY", would=f"#{pr}")]
        shell.enqueue_out[pr] = (0, f"enqueue #{pr}: {{}}\n")
        shell.merge_commits[pr] = f"{pr}aaaa0000"
        grade(shell, pr, f"ok F307 at {head[:7]}.")
    queue_drafts(shell, (40, True), (50, True))
    shell.trunk = [{"oid": "9999dddd00"}]
    landing_pass(shell, config)
    merged = direct_merges(shell)
    assert [call[3] for call in merged] == ["29020", "29100"] or [call[3] for call in merged] == ["29100", "29020"]
    second = merged[1]
    assert [head for _, head, _ in shell.replayed] == [{"29020": "cccc333333", "29100": "eeee555555"}[second[3]]] and second[-1] == shell.replayed[0][2]
    first_squash = next(index for index, call in enumerate(shell.calls) if call[-1] == "mergeCommit")
    assert shell.calls.index(second) > first_squash


@pytest.mark.parametrize("conflicts", [False, True], ids=["rebases", "conflicts"])
def test_an_approved_green_incident_fix_behind_dev_merges_rebased_onto_the_tip_or_asks_its_lane_once(shell, config, tmp_path, conflicts):
    """#45425, the 8:35 PM Oct 29 revert, merged ten commits behind dev and broke it at 8:48; the owner's 9:00 PM rule covers every merge outside the queue."""
    incident_pr(shell)
    shell.trunk = [{"oid": "9999dddd00"}]
    shell.behind[INCIDENT_HEAD] = 3
    if conflicts:
        shell.replay_conflicts.add(INCIDENT_HEAD)
    landing_pass(shell, config)
    landing_pass(shell, config)
    asked = [post_body(post).split(":")[0] for post in blockers(shell) if "lands only rebased" in post_body(post)]
    if conflicts:
        assert merges(shell) == [] and asked == ["#36650 ffff666666"]
    else:
        assert merges(shell) == [["gh", "pr", "merge", "36650", "--repo", "Forge-AI/monorepo", "--squash", "--match-head-commit", shell.replayed[0][2]]] and asked == []


@pytest.mark.parametrize("approval", ["dismissed", "old-head"])
def test_while_graphite_is_down_a_rebased_pr_whose_approval_does_not_come_back_in_ten_minutes_never_merges_and_names_the_bot(shell, config, tmp_path, approval):
    """Root #197434 and #198367: a rebase push dismisses the approving review, and #45365 merged-failed at 10:19 PM Oct 29 on an approval GitHub still read from the old head, so the runner waits for an approval on the new head itself, never --admin or --auto."""
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    queue_drafts(shell, (40, True), (50, True))
    shell.trunk = [{"oid": "9999dddd00"}]
    shell.behind["cccc333333"] = 1
    if approval == "dismissed":
        shell.approval_after_push = "REVIEW_REQUIRED"
    else:
        shell.review_commit = "cccc333333"
    started = shell.clock
    landing_pass(shell, config)
    assert len(shell.pushes) == 1 and direct_merges(shell) == [] and shell.clock - started >= timedelta(minutes=9)
    [asked] = [post_body(post) for post in blockers(shell) if "rebases each admitted stack" in post_body(post)]
    assert "forge-pr-reviewer[bot] has not approved #29020 again" in asked


def test_while_graphite_is_down_a_child_of_a_squashed_pr_is_replayed_onto_the_squash(shell, config, tmp_path):
    """#45428, Oct 30: once #45377 squash-merged, #45428's branch still carried #45377's commits, and stack-enqueue read it conflicted with dev."""
    two_pr_stack(shell)
    shell.gates["29031"] = [gate("#29020 GREEN cccc333333 graphite READY", "#29031 BLOCKED dddd444444 graphite RUNNING", would="#29020")]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    queue_drafts(shell, (40, True), (50, True))
    shell.current_heads |= {"29020": "cccc333333", "29031": "dddd444444"}
    shell.merge_commits["29020"] = "1111aaaa0000"
    landing_pass(shell, config)
    assert merged_records(shell) == ["MERGED #29020 at cccc333333 direct as 1111aaaa00, Graphite down, b530f5d: gh pr merge --squash"]
    assert [(onto, head) for onto, head, _ in shell.replayed] == [("1111aaaa0000", "dddd444444")]
    assert shell.pushes[-1] == ["--no-verify", "--force-with-lease=refs/heads/a/2:dddd444444", "origin", f"{shell.replayed[0][2]}:refs/heads/a/2"]


def test_while_graphite_is_down_a_child_whose_parent_has_not_landed_never_merges_into_the_parent_branch(shell, config, tmp_path):
    """Finder on cc-skills#1110: the gate can admit a child alone while its parent sits in Graphite's queue, and gh pr merge would squash it into the parent's branch."""
    two_pr_stack(shell)
    shell.gates["29031"] = [gate("#29020 QUEUED cccc333333 graphite WAITING_TO_MERGE", "#29031 GREEN dddd444444 graphite READY", would="#29031")]
    queue_drafts(shell, (40, True), (50, True))
    landing_pass(shell, config)
    assert direct_merges(shell) == [] and merged_records(shell) == []


def test_while_graphite_is_down_a_stack_whose_head_moved_during_the_wait_never_merges(shell, config, tmp_path):
    """Finder on cc-skills#1110: gh stack merge pins no head, so the runner reads each head again just before it."""
    two_pr_stack(shell)
    queue_drafts(shell, (40, True), (50, True))
    shell.current_heads |= {"29020": "cccc333333", "29031": "ffff777777"}
    landing_pass(shell, config)
    assert direct_merges(shell) == [] and merged_records(shell) == []
    [asked] = [post_body(post) for post in blockers(shell) if "rebases each admitted stack" in post_body(post)]
    assert "#29031's head reads ffff777777 on GitHub, not the checked dddd444444" in asked


def test_native_adapter_never_uses_a_saved_graphite_outage_to_merge_directly():
    landing = object.__new__(runner_module.Landing)
    landing.adapter = SimpleNamespace(kind="trunk-stack-enqueue")
    landing.book = SimpleNamespace(load=lambda _: pytest.fail("native queue must not inspect Graphite outage facts"))
    assert landing.down_since() is None


def test_native_adapter_does_not_repair_graphite_caps():
    landing = object.__new__(runner_module.Landing)
    landing.adapter = SimpleNamespace(kind="trunk-stack-enqueue")
    landing.resubmit_graphite_base({"20": {"state": "open", "ours": True, "base": "graphite-base/10", "head": "abcd"}})


@pytest.mark.parametrize("history", [32, 10_000])
@pytest.mark.parametrize("heartbeats", [32, 100])
def test_heartbeat_page_writes_the_full_runner_history_only_once(shell, config, tmp_path, monkeypatch, history, heartbeats):
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    assert runner.check()
    with store.owned(runner_module.RUNNER, 0) as current:
        for index in range(history):
            key = f"history:{index}"
            current.accept(key, "history", "old", None, shell.now())
            current.verify(key, {"done": True})
    for _ in range(heartbeats):
        shell.receive({"type": "heartbeat"})
    written, write = [], Path.write_text

    def counted(path, text, *args, **kwargs):
        result = write(path, text, *args, **kwargs)
        if path.parent == store.root and path.name.startswith(f".{runner_module.RUNNER}.json."):
            written.append(len(text.encode()))
        return result

    monkeypatch.setattr(Path, "write_text", counted)
    assert runner.check()
    assert store.load(runner_module.RUNNER).facts["inbox:run_1"] == heartbeats
    print(json.dumps({"history": history, "heartbeats": heartbeats, "runner_writes": len(written), "runner_payload_bytes_written": sum(written)}))
    assert len(written) == 1
    assert sum(written) == store.path(runner_module.RUNNER).stat().st_size


def test_pending_heartbeat_cursor_is_durable_before_a_nonheartbeat_effect(shell, config, tmp_path, monkeypatch):
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    assert runner.check()
    beat = shell.receive({"type": "heartbeat"})
    shell.receive({"type": "status"})

    def interrupted(message):
        assert store.load(runner_module.RUNNER).facts["inbox:run_1"] == beat["sequence"]
        raise RuntimeError("interrupted effect")

    monkeypatch.setattr(runner, "message", interrupted)
    with pytest.raises(RuntimeError, match="interrupted effect"):
        runner.check()
    assert store.load(runner_module.RUNNER).facts["inbox:run_1"] == beat["sequence"]


def test_interrupted_heartbeat_replay_keeps_the_original_lease_timestamp(shell, config, tmp_path, monkeypatch):
    shell.launch(LANE, "ctx_a")
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    container = runner_module.lane_container(LANE)
    runner.book.ensure(container, runner_module.ORCA_DESK)
    assert runner.check()
    beat = shell.receive({"type": "heartbeat"})
    shell.receive({"type": "heartbeat"})
    received = []
    beat_lane = runner.beat_lane

    def interrupted(message):
        beat_lane(message)
        received.append(message["id"])
        if len(received) == 2:
            raise RuntimeError("interrupted heartbeat page")

    with monkeypatch.context() as scoped:
        scoped.setattr(runner, "beat_lane", interrupted)
        with pytest.raises(RuntimeError, match="interrupted heartbeat page"):
            runner.check()
    assert store.load(container).facts[runner_module.LANE_HEARTBEAT] == beat["created_at"]
    shell.sleep(600)
    assert runner.check()
    assert store.load(container).facts[runner_module.LANE_HEARTBEAT] == beat["created_at"]
    assert store.load(runner_module.RUNNER).facts["inbox:run_1"] == 2
