from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "bin/orca-worker-launch.sh"
RUN = "run_1"
LANE = "lane-a"
LANE_ENV = "DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING=1 CHOKIDAR_USEPOLLING=1 CHOKIDAR_INTERVAL=300000 CLAUDE_CODE_DISABLE_MOUSE=1 CLAUDE_CODE_SYNTAX_HIGHLIGHT=false CLAUDE_CODE_DISABLE_AGENT_PROGRESS_SUMMARIES=1 CLAUDE_CODE_ENABLE_AWAY_SUMMARY=false CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1 DISABLE_COST_WARNINGS=1 DISABLE_INSTALLATION_CHECKS=1 CLAUDE_CODE_AUTO_CONNECT_IDE=false CLAUDE_CODE_IDE_SKIP_AUTO_INSTALL=1 ENABLE_CLAUDEAI_MCP_SERVERS=false DISABLE_TELEMETRY=1 DISABLE_ERROR_REPORTING=1 CLAUDE_CODE_GB_DISK_CACHE_WHEN_TELEMETRY_OFF=1 CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1 CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1 CLAUDE_CODE_PLUGIN_DIR_WATCH=false CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL=1"
LANE_SETTINGS = '{"spinnerTipsEnabled":false,"prefersReducedMotion":true,"verbose":false,"showThinkingSummaries":false,"feedbackDrafts":"off","awaySummaryEnabled":false,"promptSuggestionEnabled":false,"tui":"fullscreen","disableDeepLinkRegistration":"disable","statusLine":{"type":"command","command":"exit 0"},"terminalProgressBarEnabled":false}'
LANE_DENIED_TOOLS = "AskUserQuestion,EnterPlanMode,ExitPlanMode,PushNotification,DesignSync"
SPRITE_SETTINGS = json.dumps({**json.loads(LANE_SETTINGS), "permissions": {"deny": LANE_DENIED_TOOLS.split(",")}}, separators=(",", ":"))
WORKSPACE = "pool-spare"
SESSION = "5d576546-4c87-4a78-94b4-945eb9e49c86"
REPO = "repo-local"
BASE_BRANCH = "origin/main"
CONE = ("src", "docs")
TERMINAL = "term_remote_1"
ROOT = "/home/sprite/repo"
BASE = "1" * 40
BRIEF = "# Lane A\n\nWrite the patch and the report beside this brief.\n"
SHA = hashlib.sha256(BRIEF.encode()).hexdigest()
BYTES = len(BRIEF.encode())
LOCAL_NAME = f"{LANE}-{hashlib.sha256(RUN.encode()).hexdigest()[:8]}"
LOCAL_WORKTREE = f"/w/{LOCAL_NAME}"
LOCAL_TERMINAL = "term_local_1"
MAIN = "/repos/main"
LIST = ["orca", "worktree", "list", "--repo", f"id:{REPO}", "--limit", "10000", "--json"]
REPO_SHOW = ["orca", "repo", "show", "--repo", f"id:{REPO}", "--json"]
GIT_LIST = ["git", "-C", MAIN, "worktree", "list", "--porcelain"]
UNLISTED = {"out": {"ok": True, "result": {"worktrees": []}}}
SHOWN = {"out": {"ok": True, "result": {"repo": {"id": REPO, "path": MAIN}}}}
DROPPED = {"rc": 1, "out": {"ok": False, "error": {"code": "runtime_timeout", "message": "Timed out waiting for the Orca runtime to respond."}}}
WIDEN = f"git -C {LOCAL_WORKTREE} sparse-checkout add <dir>"
SPARSE = f"{LOCAL_WORKTREE} is sparse: widen with {WIDEN}"
TOOLS = ("orca", "cc-remote", "git", "gh", "ccx", "cc-notes", "curl", "ssh")
PIN = "9.8.7"

VERSION = ["cc-remote", "version"]
CURRENT = ["orca", "orchestration", "run-current", "--full", "--json"]
DESTROY = ["cc-remote", "destroy", WORKSPACE]
STOP = ["cc-remote", "orca", "stop", LANE, "--provider", "sprites", "--profile", "agents"]
BOUND = {"out": {"ok": True, "result": {"run": {"id": RUN, "coordinator_handle": "term_coordinator"}}}}
AGENT = {"kind": "claude", "model": "claude-opus-5-5", "effort": "xhigh"}
PREPARED = {
    "schemaVersion": 1,
    "workspace": WORKSPACE,
    "provider": "sprites",
    "profile": "agents",
    "machine": WORKSPACE,
    "projectRoot": ROOT,
    "environment": WORKSPACE,
    "environmentId": "env-1",
    "runtimeId": "rt-1",
    "managedServer": "0.1.0+15ec2904eb00",
    "repoId": "repo-1",
    "worktreeId": f"repo-1::{ROOT}",
    "agent": AGENT,
    "terminal": TERMINAL,
    "forward": {"host": WORKSPACE, "config": f"/state/ssh/{WORKSPACE}.ssh"},
    "bootstrap": ["trust"],
    "prepared": True,
    "brief": {"path": f"/home/sprite/.cc-remote/orca/tasks/{WORKSPACE}/brief.md", "sha256": SHA, "bytes": BYTES},
    "baseCommit": BASE,
}
RELEASES = (("ccx", "yasyf/cc-context", "v0.95.43", "a" * 64, "", "checksums.txt", "v0.95.43\n"), ("cc-notes", "yasyf/cc-notes", "v0.76.7", "b" * 64, "ccn", "SHA256SUMS.txt", "v0.76.7 (0123abc)\n"))
REWRITTEN = BRIEF + "\nA later line.\n"
INSTALLED = {"out": "ccx v0.95.43\ncc-notes v0.76.7\nccn v0.76.7\n"}
UNREAD = {"rc": 22, "err": "curl: (22) The requested URL returned error: 503\n"}
FLOOR = "ccx: GitHub rate limit floor: user core has 680 of 5000 left, under the floor of 1000, until 6:52PM (2026-11-12T18:52:01-08:00); top callers: zsh 440, drive watch 347, drive card 156, drive pr check 154, cc-slack 142\n"
RECEIPT = {"ok": True, "result": {"runId": RUN, "taskId": "task_a", "dispatchId": "ctx_a", "state": "ready", "stage": "remote_input_accepted"}}
ALLOCATION_KEPT = ["allocation.json", "prepare.err", "prepare.status", "request.json"]
PREPARE_KEPT = sorted([*ALLOCATION_KEPT, "prepared.json"])
START_KEPT = ["worker-start.err", "worker-start.json", "worker-start.status"]
TOOLS_KEPT = sorted([*PREPARE_KEPT, "tools.err", "tools.out"])
SPRITE_KEPT = sorted([*TOOLS_KEPT, *START_KEPT])
LOCAL_KEPT = sorted(["request.json", "worktree", *START_KEPT])
UNREADY = "orca orchestration worker-start exited 0 without a ready receipt for Run {run}; kept terminal {terminal}, worktree {worktree}, and the receipt in {attempt}"
REQUEST = "3c349608-67b7-49a0-8c8d-1b16b27d0e13"
UNREACHED = {
    "rc": 1,
    "out": {
        "ok": False,
        "error": {
            "code": "runtime_error",
            "message": f"Timed out waiting for the remote Orca runtime. If the server is on another network, connect both devices to Tailscale and pair using its Tailscale address (100.x or a *.ts.net name). See https://tailscale.com/download. Orchestration mutation request ID: {REQUEST}.",
            "data": {"orchestrationRequestId": REQUEST},
        },
    },
}
REQUEST_SHOW = ["orca", "orchestration", "request-show", "--request", REQUEST, "--json"]
ENV_SHOW = ["orca", "environment", "show", "--environment", PREPARED["environmentId"], "--json"]
UNKNOWN_ENVIRONMENT = {"rc": 1, "out": {"id": "local", "ok": False, "error": {"code": "invalid_argument", "message": f"Unknown environment: {PREPARED['environmentId']}"}}}


def request_state(state: str) -> dict:
    return {"out": {"ok": True, "result": {"requestId": REQUEST, "state": state}}}

FAKE = """#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_STATE"]
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
with open(os.path.join(state, "calls"), "a") as calls:
    calls.write(json.dumps({"argv": [name, *args], "real": os.path.realpath(sys.argv[0]), "env": {"CC_REMOTE_CONFIG": os.environ.get("CC_REMOTE_CONFIG")}, "stdin": sys.stdin.read() if name == "ssh" else None}) + "\\n")
words = [arg for arg in args[:2] if not arg.startswith("-") and "/" not in arg]
queue = os.path.join(state, "_".join([name, *words]))
replies = json.load(open(queue)) if os.path.exists(queue) else []
reply = replies.pop(0) if replies else {"rc": 1}
json.dump(replies, open(queue, "w"))
out = reply.get("out", "")
sys.stdout.write(out if isinstance(out, str) else json.dumps(out))
sys.stderr.write(reply.get("err", ""))
sys.exit(reply.get("rc", 0))
"""

BIND_REFUSAL = "drive: orca orchestration run-use: run_not_found: no such Run"
BIND_CRASH = "NameError: name 'unsent' is not defined"
TASK_LIST = ["orca", "orchestration", "task-list", "--run", RUN, "--json"]


def first_bind(code: int, said: str) -> str:
    """A WORKER_BIND whose first run exits code saying said, before it runs worker-start, and whose later runs run it."""
    return f"""#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_STATE"]
with open(os.path.join(state, "calls"), "a") as calls:
    calls.write(json.dumps({{"argv": ["bound", *sys.argv[1:]]}}) + "\\n")
refused = os.path.join(state, "bound_refused")
if not os.path.exists(refused):
    open(refused, "w").close()
    sys.stderr.write({said + chr(10)!r})
    sys.exit({code})
os.execvp(sys.argv[2], sys.argv[2:])
"""


def tasks(*titles: str) -> dict:
    return {"out": {"ok": True, "result": {"tasks": [{"id": f"task_{n}", "title": title} for n, title in enumerate(titles)]}}}


def download(repo: str, tag: str, name: str) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


def listed(bin: str, sha: str) -> dict:
    return {"out": f"{'c' * 64}  {bin}_darwin_arm64\n{sha}  {bin}_linux_amd64\n{'d' * 64}  {bin}_linux_arm64\n"}


def fake(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FAKE)
    path.chmod(0o755)
    return path


def allocation(prepared: dict = PREPARED, **fields) -> dict:
    return {
        "schemaVersion": 1,
        "lane": LANE,
        "workspace": WORKSPACE,
        "allocation": "warm",
        "ref": "dev",
        "head": prepared.get("baseCommit"),
        "replenish": {"target": 1, "pid": 123},
        "task": prepared,
        **fields,
    }


class Launch:
    def __init__(self, root: Path):
        self.fake = root / "fake"
        self.fake.mkdir()
        self.bin = root / "bin"
        for tool in TOOLS:
            fake(self.bin / tool)
        self.root = root
        self.checkout = root / "checkout"
        self.script = SCRIPT
        self.cli = self.bin / "cc-remote"
        self.config = self.checkout / "cc-remote" / "config.yaml"
        self.config.parent.mkdir(parents=True)
        self.config.write_text("provider: sprites\n")
        self.payload = self.config.with_name("agents-payload.json")
        self.payload.write_text(json.dumps({"bootstrapHelper": {"version": PIN}}))
        self.briefs = root / "briefs"
        self.briefs.mkdir()
        self.brief = self.briefs / f"{LANE}.md"
        self.brief.write_text(BRIEF)
        self.attempt = self.briefs / f"{LANE}.worker"
        self.home = root / "home"
        self.home.mkdir()
        self.path = [str(self.bin), *(path for path in os.environ["PATH"].split(os.pathsep) if path)]
        self.env = {
            **{key: value for key, value in os.environ.items() if not key.startswith(("WORKER_", "ORCA_", "CC_REMOTE_"))},
            "PATH": os.pathsep.join(self.path),
            "FAKE_STATE": str(self.fake),
            "HOME": str(self.home),
            "PYTHONPYCACHEPREFIX": str(root / "python-cache"),
            "WORKER_RUN": RUN,
            "CC_REMOTE_CONFIG": str(self.config),
            "ORCA_TERMINAL_HANDLE": "term_coordinator",
        }

    def reply(self, command: str, *replies: dict) -> None:
        (self.fake / command.replace(" ", "_")).write_text(json.dumps(list(replies)))

    def sprite(self, prepared: dict | str = PREPARED, version: dict | None = None, started: dict | None = None, allocated: dict | str | None = None, copied: dict | None = None) -> None:
        self.reply("cc-remote version", version or {"err": f"{PIN}\n"})
        self.reply("orca orchestration run-current", BOUND)
        response = allocated if allocated is not None else prepared if isinstance(prepared, str) else allocation(prepared)
        self.reply("cc-remote orca prepare", {"out": response if isinstance(response, str) else json.dumps(response), "err": "prepared on sprites\n"})
        self.reply(f"cc-remote destroy {WORKSPACE}", {})
        self.reply("cc-remote orca stop", {"out": json.dumps({"lane": LANE, "workspace": WORKSPACE})})
        for bin, *_, version in RELEASES:
            self.reply(bin, {"out": version})
        self.reply("curl", *(listed(bin, sha) for bin, _, _, sha, *_ in RELEASES))
        self.reply("ssh", *(() if copied is None else (copied,)), INSTALLED)
        self.reply("orca orchestration worker-start", started or {"out": RECEIPT})

    def local(self, wait: dict | None = None, started: dict | None = None) -> None:
        self.reply("orca orchestration run-current", BOUND)
        self.reply("orca worktree list", {"out": {"ok": True, "result": {"worktrees": []}}})
        self.reply("orca worktree create", {"out": {"ok": True, "result": {"worktree": {"path": LOCAL_WORKTREE}}}})
        self.reply("git", {"out": ""})
        self.reply("orca terminal list", {"out": {"ok": True, "result": {"terminals": [{"handle": LOCAL_TERMINAL, "agentIdentity": None}]}}})
        self.reply("orca terminal send", {"out": {"ok": True}})
        self.reply("orca terminal show", {"out": {"ok": True, "result": {"terminal": {"agentIdentity": "claude"}}}})
        self.reply("orca terminal wait", wait or {"out": {"ok": True}})
        self.reply("orca orchestration worker-start", started or {"out": {"ok": True, "result": {"runId": RUN, "state": "ready", "taskId": "task_l", "dispatchId": "ctx_l"}}})

    def records(self) -> list[dict]:
        path = self.fake / "calls"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def calls(self) -> list[list[str]]:
        return [record["argv"] for record in self.records()]

    def kept(self) -> list[str]:
        return sorted(path.name for path in self.attempt.iterdir()) if self.attempt.is_dir() else []

    def receipt(self, name: str) -> str:
        return (self.attempt / name).read_text()

    def run(self, model: str = "opus", effort: str = "xhigh", lane: str = LANE, cwd: Path | None = None, **env: str) -> subprocess.CompletedProcess[str]:
        local = {"WORKER_REPO": REPO, "WORKER_BASE_BRANCH": BASE_BRANCH, "WORKER_SPARSE": " ".join(CONE)} if env.get("WORKER_LOCAL") == "1" else {}
        return subprocess.run([str(self.script), lane, model, effort, str(self.brief)], env={**self.env, **local, **env}, cwd=cwd, capture_output=True, text=True, timeout=60)

    def cc_remote(self) -> set[tuple[str, str]]:
        return {(record["real"], record["env"]["CC_REMOTE_CONFIG"]) for record in self.records() if record["argv"][0] == "cc-remote"}

    def prepare(self, *extra: str, model: str = "claude-opus-5-5", kind: str = "claude", effort: str = "xhigh", lane: str = LANE, continued: str | None = None) -> list[str]:
        opening = [continued, "--existing"] if continued else [lane, "--warm"]
        lane_env = [*(word for pair in LANE_ENV.split() for word in ("--env", pair)), "--settings", SPRITE_SETTINGS] if kind == "claude" else []
        return ["cc-remote", "orca", "prepare", *opening, "--provider", "sprites", "--profile", "agents", "--agent", kind, "--model", model, "--effort", effort, "--brief-file", str(self.brief), *lane_env, *extra]

    def tools(self, placed: str = "0") -> list[list[str]]:
        read = [call for bin, repo, tag, _, _, sums, _ in RELEASES for call in ([bin, "--version"], ["curl", "-fsSL", "--retry", "3", download(repo, tag, sums)])]
        words = " ".join(f"{bin} {tag} {download(repo, tag, f'{bin}_linux_amd64')} {sha} {alias or "''"}" for bin, repo, tag, sha, alias, *_ in RELEASES)
        return [*read, ["ssh", "-F", PREPARED["forward"]["config"], "-o", "BatchMode=yes", "-o", "ConnectTimeout=30", WORKSPACE, f"bash -s -- {placed} {words}"]]

    def copy(self) -> list[str]:
        return ["ssh", "-F", PREPARED["forward"]["config"], "-o", "BatchMode=yes", "-o", "ConnectTimeout=30", WORKSPACE, f"cat >{PREPARED['brief']['path']}"]

    def start(self, prepared: dict = PREPARED, retry: str | None = None, brief: str = BRIEF) -> list[str]:
        spec = f"Lane {LANE}: read the whole brief at {prepared['brief']['path']}, verify its SHA-256 is {hashlib.sha256(brief.encode()).hexdigest()} and its size is {len(brief.encode())} bytes, then follow it exactly."
        placement = ["--on", prepared["environment"], "--worktree", f"id:{prepared['worktreeId']}", "--terminal", prepared["terminal"]]
        retried = ["--retry-request", retry] if retry else []
        return ["orca", "orchestration", "worker-start", "--run", RUN, "--spec", spec, "--task-title", LANE, *placement, *retried, "--timeout-ms", "600000", "--full", "--json"]

    def local_calls(self, model: str = "claude-fable-5-2", effort: str = "xhigh", repo: str = REPO, cone: tuple[str, ...] = CONE, lane_env: str = LANE_ENV) -> list[list[str]]:
        claude = f"env {lane_env} claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --disallowedTools {LANE_DENIED_TOOLS} --strict-mcp-config --no-chrome --model {model} --effort {effort} --settings '{LANE_SETTINGS}'"
        pointer = f"Lane '{LANE}'. Your full brief is the file {self.brief}: read it in full with the Read tool before anything else and execute it exactly (Orca truncates pasted specs). Your Orca worktree is {LOCAL_WORKTREE}.{f" This lane is sparse: widen with `{WIDEN}` before reading outside it." if cone else ""} You run in bypass-permissions mode; the brief's Escalate rules still hold."
        return [
            CURRENT,
            ["orca", "worktree", "list", "--repo", f"id:{repo}", "--limit", "10000", "--json"],
            ["orca", "worktree", "create", "--name", LOCAL_NAME, "--repo", f"id:{repo}", "--base-branch", BASE_BRANCH, "--setup", "run", "--json"],
            *([["git", "-C", LOCAL_WORKTREE, "sparse-checkout", "set", "--cone", *cone]] if cone else []),
            ["orca", "terminal", "list", "--worktree", f"path:{LOCAL_WORKTREE}", "--json"],
            ["orca", "terminal", "send", "--terminal", LOCAL_TERMINAL, "--enter", "--text", claude, "--json"],
            ["orca", "terminal", "show", "--terminal", LOCAL_TERMINAL, "--json"],
            ["orca", "terminal", "wait", "--terminal", LOCAL_TERMINAL, "--for", "tui-idle", "--timeout-ms", "120000", "--json"],
            ["orca", "orchestration", "worker-start", "--run", RUN, "--spec", pointer, "--task-title", LANE, "--worktree", f"path:{LOCAL_WORKTREE}", "--terminal", LOCAL_TERMINAL, "--timeout-ms", "600000", "--full", "--json"],
        ]


class LaunchTest(unittest.TestCase):
    def fresh(self) -> Launch:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Launch(Path(tmp.name).resolve())

    def setUp(self):
        self.launch = self.fresh()

    def assert_failed(self, launch: Launch, result: subprocess.CompletedProcess[str], line: str, calls: list[list[str]], code: int = 1) -> None:
        self.assertEqual((result.returncode, result.stdout, result.stderr), (code, "", f"{line}\n"))
        self.assertEqual(launch.calls(), calls)

    def assert_refused(self, launch: Launch, result: subprocess.CompletedProcess[str], line: str, calls: list[list[str]], code: int = 1) -> None:
        self.assert_failed(launch, result, line, calls, code)
        self.assertFalse(launch.attempt.exists() or launch.attempt.is_symlink())


class PreAllocationTest(LaunchTest):
    def test_usage_takes_exactly_four_arguments(self):
        result = subprocess.run([str(self.launch.script), LANE, "opus", "xhigh"], env=self.launch.env, capture_output=True, text=True, timeout=60)
        self.assert_refused(self.launch, result, f"usage: WORKER_RUN=<run_id> {self.launch.script} <lane> <model> <effort> <brief-file>", [], code=2)

    def test_a_missing_brief_refuses(self):
        self.launch.sprite()
        self.launch.brief.unlink()
        self.assert_refused(self.launch, self.launch.run(), f"no brief at {self.launch.brief}", [])

    def test_a_model_neither_claude_nor_gpt_refuses(self):
        for model in ("haiku", "o3", "gemini-3-pro"):
            with self.subTest(model=model):
                launch = self.fresh()
                launch.sprite()
                self.assert_refused(launch, launch.run(model=model), f"model {model} is neither a claude- nor a gpt- model, so neither claude nor codex runs it; nothing launched", [])

    def test_a_service_tier_on_a_claude_model_refuses(self):
        self.launch.sprite()
        self.assert_refused(self.launch, self.launch.run(WORKER_SERVICE_TIER="fast"), "WORKER_SERVICE_TIER is a codex setting, and claude runs claude-opus-5-5; nothing launched", [])

    def test_a_fast_service_tier_outside_an_incident_refuses(self):
        for incident in ("", "0", "true"):
            with self.subTest(incident=incident):
                launch = self.fresh()
                launch.sprite()
                self.assert_refused(launch, launch.run(model="gpt-6.1-sol", WORKER_SERVICE_TIER="fast", WORKER_INCIDENT=incident), "WORKER_SERVICE_TIER=fast is for incident lanes only; set WORKER_INCIDENT=1 for an incident lane; nothing launched", [])

    def test_worker_local_takes_only_1(self):
        for value in ("true", "0", "yes"):
            with self.subTest(value=value):
                launch = self.fresh()
                launch.sprite()
                self.assert_refused(launch, launch.run(WORKER_LOCAL=value), "WORKER_LOCAL is 1 for the explicit local route or unset for a Sprite; nothing launched", [])

    def test_a_lane_cc_remote_cannot_name_refuses_before_any_call(self):
        for lane in ("Lane-a", "lane_a", "lane.a", "a" * 56, "-lane", "lane a", "lane/a", ""):
            with self.subTest(lane=lane):
                launch = self.fresh()
                launch.sprite()
                self.assert_refused(launch, launch.run(lane=lane), "a Sprite lane takes up to 55 lowercase letters, digits, and dashes, starting with a letter or digit; nothing was prepared", [])

    def test_a_lane_cc_remote_can_name_reaches_prepare(self):
        for lane in ("a" * 55, "0-lane-9"):
            with self.subTest(lane=lane):
                launch = self.fresh()
                launch.sprite()
                launch.reply("cc-remote orca prepare", {"rc": 1})
                self.assert_failed(launch, launch.run(lane=lane), f"cc-remote orca prepare exited 1; {launch.attempt} keeps its output, nothing was attached, and launching this brief again prepares afresh", [VERSION, CURRENT, launch.prepare(lane=lane)])

    def test_a_local_repository_refuses_the_sprite_route(self):
        self.launch.sprite()
        self.assert_refused(self.launch, self.launch.run(WORKER_REPO="repo-1"), "WORKER_REPO picks a local Orca repository; a Sprite checks out the repository its cc-remote config names, chosen with CC_REMOTE_CONFIG; nothing was prepared", [])

    def test_fable_refuses_the_sprite_route_before_any_call(self):
        for model, model_id in (("fable", "claude-fable-5-2"), ("claude-fable-5-2", "claude-fable-5-2"), ("claude-Fable-6", "claude-Fable-6")):
            with self.subTest(model=model):
                launch = self.fresh()
                launch.sprite()
                self.assert_refused(launch, launch.run(model=model), f"{model_id} is Fable, which runs only on the explicit local route, WORKER_LOCAL=1; nothing was prepared, and nothing launched locally instead", [])

    def test_a_design_lanes_brief_refuses_the_sprite_route(self):
        self.launch.brief.write_text(BRIEF + "\nDraw: every visual deliverable, such as a page layout, is drawn by gpt-6.1-sol through codex.\n")
        self.launch.sprite()
        self.assert_refused(self.launch, self.launch.run(), f"{self.launch.brief} is a design lane's brief, and its codex design pass needs the ChatGPT login only this Mac has, since a Sprite carries an API key that mounts no image tools; launch it with WORKER_LOCAL=1; nothing was prepared", [])

    def test_a_local_base_branch_refuses_the_sprite_route(self):
        self.launch.sprite()
        self.assert_refused(self.launch, self.launch.run(WORKER_BASE_BRANCH="origin/dev"), "WORKER_BASE_BRANCH picks a local worktree's base; a Sprite checks out WORKER_REF, a branch or tag, or the cc-remote config's ref; nothing was prepared", [])

    def test_an_expected_base_must_be_a_full_lowercase_commit(self):
        for value in ("abc1234", "A" * 40, "1" * 39, "1" * 41, "main"):
            with self.subTest(value=value):
                launch = self.fresh()
                launch.sprite()
                self.assert_refused(launch, launch.run(WORKER_EXPECT_BASE=value), "WORKER_EXPECT_BASE is a full 40- or 64-character lowercase commit; nothing was prepared", [])

    def test_a_missing_cc_remote_refuses(self):
        missing = self.launch.home / "absent" / "cc-remote"
        self.launch.sprite()
        self.assert_refused(self.launch, self.launch.run(WORKER_CC_REMOTE=str(missing)), f"WORKER_CC_REMOTE names no cc-remote command at {missing}; nothing was prepared", [])

    def test_a_cc_remote_other_than_the_helper_pin_refuses_before_allocation(self):
        cases = (
            ({"err": "9.8.6\n"}, "9.8.6"),
            ({"err": "9.8.8\n"}, "9.8.8"),
            ({"err": "10.0.0\n"}, "10.0.0"),
            ({"out": f"v{PIN}\n"}, f"v{PIN}"),
            ({"err": f"cc-remote {PIN}\n"}, f"cc-remote {PIN}"),
            ({"err": "dev\n"}, "dev"),
            ({"out": ""}, "nothing"),
        )
        for version, printed in cases:
            with self.subTest(version=version):
                launch = self.fresh()
                launch.sprite(version=version)
                self.assert_refused(launch, launch.run(), f"cc-remote version printed {printed}, and {launch.config} pins bootstrap helper {PIN}; a Sprite needs the CLI at exactly its helper's release; nothing was prepared", [VERSION])

    def test_a_cc_remote_that_cannot_print_its_version_refuses_with_its_own_message(self):
        untrusted = "mise ERROR Config files in ~/checkout are not trusted."
        self.launch.sprite(version={"rc": 1, "err": f"{untrusted}\n"})
        self.assert_refused(self.launch, self.launch.run(), f"cc-remote version failed: {untrusted}; nothing was prepared", [VERSION])

    def test_a_checkout_without_a_helper_pin_refuses_before_any_call(self):
        for payload in ({}, {"bootstrapHelper": {}}, {"bootstrapHelper": {"version": None}}):
            with self.subTest(payload=payload):
                launch = self.fresh()
                launch.sprite()
                launch.payload.write_text(json.dumps(payload))
                self.assert_refused(launch, launch.run(), f"{launch.payload} pins no bootstrap helper version; nothing was prepared", [])

    def test_the_pinned_cc_remote_prepares(self):
        for version in ({"err": f"{PIN}\n"}, {"out": f"{PIN}\n"}, {"err": f"mise cc-remote@{PIN} download\nmise cc-remote@{PIN} installed\n{PIN}\n"}):
            with self.subTest(version=version):
                launch = self.fresh()
                launch.sprite(version=version)
                result = launch.run()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(launch.calls(), [VERSION, CURRENT, launch.prepare(), *launch.tools(), launch.start()])

    def test_a_missing_or_different_run_owner_refuses_before_prepare(self):
        cases = (
            ({"rc": 1, "out": ""}, "no Run"),
            ({"rc": 1, "out": BOUND["out"]}, "no Run"),
            ({"out": {"ok": False, "error": {"code": "consumer_fenced", "message": "fenced"}}}, "no Run"),
            ({"out": {"ok": True, "result": {"run": {"id": "run_2"}}}}, "run_2"),
            ({"out": {"ok": True, "result": {}}}, "no Run"),
            ({"out": {**BOUND["out"], "error": {"code": "stale"}}}, "no Run"),
            ({"out": json.dumps(BOUND["out"]) * 2}, "no Run"),
            ({"out": "not json"}, "no Run"),
        )
        for bound, owner in cases:
            with self.subTest(bound=bound):
                launch = self.fresh()
                launch.sprite()
                launch.reply("orca orchestration run-current", bound)
                self.assert_refused(launch, launch.run(), f"terminal term_coordinator coordinates {owner}, not {RUN}, so nothing launched; launch from the coordinator that already owns Run {RUN}", [VERSION, CURRENT])


class AttemptTest(LaunchTest):
    def test_an_earlier_attempt_refuses_replay_before_prepare(self):
        for shape in ("prepared", "started", "link"):
            with self.subTest(shape=shape):
                launch = self.fresh()
                launch.sprite()
                if shape == "link":
                    launch.attempt.symlink_to(launch.briefs / "gone")
                else:
                    launch.attempt.mkdir()
                kept = {"prepared": {"prepare.status": "0\n", "prepared.json": json.dumps(PREPARED)}, "started": {"prepare.status": "1\n", "worker-start.status": "1\n"}}.get(shape, {})
                for receipt, text in kept.items():
                    (launch.attempt / receipt).write_text(text)
                shown = [ENV_SHOW] if shape == "prepared" else []
                self.assert_failed(launch, launch.run(), f"{launch.attempt} holds an earlier attempt of this brief, and a brief gets one attempt; inspect it instead", [VERSION, CURRENT, *shown])
                self.assertEqual(launch.kept(), sorted(kept))

    def test_a_legacy_dispatch_receipt_refuses_instead_of_retrying(self):
        for local in (False, True):
            for shape in ("file", "dangling link"):
                with self.subTest(local=local, shape=shape):
                    launch = self.fresh()
                    if local:
                        launch.local()
                    else:
                        launch.sprite()
                    legacy = launch.briefs / f"{LANE}.dispatch.json"
                    gone = launch.briefs / "gone.dispatch.json"
                    if shape == "file":
                        legacy.write_text(json.dumps({"ok": True, "result": {"taskId": "task_old", "dispatchId": "ctx_old"}}))
                    else:
                        legacy.symlink_to(gone)
                    result = launch.run(model="fable", WORKER_LOCAL="1") if local else launch.run()
                    self.assert_refused(launch, result, f"{legacy} holds an earlier launch of this brief, and a brief gets one attempt; inspect it instead", [CURRENT] if local else [VERSION, CURRENT])
                    if shape == "file":
                        self.assertTrue(legacy.is_file())
                    else:
                        self.assertEqual(os.readlink(legacy), str(gone))

    def test_a_launch_after_failed_prepares_keeps_each_and_prepares_again(self):
        failures = []
        for _ in range(2):
            self.launch.sprite()
            self.launch.reply("cc-remote orca prepare", {"rc": 1, "out": ""})
            failures.append(self.launch.run())
        self.launch.sprite()
        third = self.launch.run()
        self.assertEqual([failure.returncode for failure in failures], [1, 1])
        retired = [self.launch.attempt.with_name(f"{self.launch.attempt.name}.{n}") for n in (1, 2)]
        self.assertIn(f"{retired[0]} keeps the earlier attempt, whose prepare exited 1 before any worker started; preparing again", failures[1].stderr)
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertIn(f"{retired[1]} keeps the earlier attempt, whose prepare exited 1 before any worker started; preparing again", third.stderr)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *[VERSION, CURRENT, STOP, self.launch.prepare()] * 2, *self.launch.tools(), self.launch.start()])
        self.assertEqual([sorted(path.name for path in attempt.iterdir()) for attempt in retired], [ALLOCATION_KEPT, ALLOCATION_KEPT])
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)

    def test_a_launch_after_a_prepare_that_never_finished_keeps_it_and_prepares_again(self):
        self.launch.attempt.mkdir()
        (self.launch.attempt / "prepare.err").write_text("claiming pool-spare\n")
        (self.launch.attempt / "allocation.json").write_text("")
        self.launch.sprite()
        result = self.launch.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        retired = self.launch.attempt.with_name(f"{self.launch.attempt.name}.1")
        self.assertIn(f"{retired} keeps the earlier attempt, whose prepare never finished before any worker started; preparing again", result.stderr)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, STOP, self.launch.prepare(), *self.launch.tools(), self.launch.start()])
        self.assertEqual(sorted(path.name for path in retired.iterdir()), ["allocation.json", "prepare.err"])
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)

    def test_a_launch_after_a_prepare_that_exited_0_with_no_sprite_to_attach_keeps_it_and_prepares_again(self):
        held = f"cc-remote: another cc-remote command holds the Orca task for {LANE}, so this one started and changed nothing"
        self.launch.sprite()
        self.launch.reply("cc-remote orca prepare", {"out": "", "err": f"{held}\n"})
        first = [VERSION, CURRENT, self.launch.prepare()]
        self.assert_failed(self.launch, self.launch.run(), f"{self.launch.attempt}/allocation.json holds not one JSON object, so the prepared worker stays idle and unattached", first)
        self.launch.sprite()
        result = self.launch.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        retired = self.launch.attempt.with_name(f"{self.launch.attempt.name}.1")
        self.assertEqual(result.stderr, f"{retired} keeps the earlier attempt, whose prepare exited 0 with no Sprite to attach: {held}; preparing again\n")
        self.assertEqual(self.launch.calls(), [*first, VERSION, CURRENT, STOP, self.launch.prepare(), *self.launch.tools(), self.launch.start()])
        self.assertEqual(sorted(path.name for path in retired.iterdir()), ALLOCATION_KEPT)
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)

    def test_a_relaunch_whose_stop_of_the_failed_claim_fails_keeps_the_attempt_and_prepares_nothing(self):
        self.launch.attempt.mkdir()
        (self.launch.attempt / "prepare.err").write_text("cc-remote: guest-bootstrap: exit 255\n")
        (self.launch.attempt / "prepare.status").write_text("1\n")
        self.launch.sprite()
        self.launch.reply("cc-remote orca stop", {"rc": 1, "err": "cc-remote: suspend pool-spare: 503\n"})
        self.assert_failed(self.launch, self.launch.run(), f"cc-remote orca stop {LANE} failed: cc-remote: suspend pool-spare: 503, so the Sprite the earlier prepare claimed still holds the lane; {self.launch.attempt} keeps that attempt, and nothing was prepared", [VERSION, CURRENT, STOP])
        self.assertEqual(self.launch.kept(), ["prepare.err", "prepare.status"])

    def test_a_local_launch_after_a_sprite_attempt_that_started_no_worker_keeps_it_and_launches_here(self):
        for shape, kept, why in (
            ("failed", {"prepare.err": "cc-remote: guest-bootstrap: exit 255\n", "prepare.status": "1\n"}, "whose prepare exited 1 before any worker started"),
            ("unstarted", {"prepare.err": "", "prepare.status": "0\n", "prepared.json": json.dumps(PREPARED), "tools.err": "ccx: GitHub rate limit floor\n"}, "whose Sprite was prepared but started no worker"),
        ):
            with self.subTest(shape=shape):
                launch = self.fresh()
                launch.local()
                launch.attempt.mkdir()
                for receipt, text in kept.items():
                    (launch.attempt / receipt).write_text(text)
                result = launch.run(model="fable", WORKER_LOCAL="1")
                self.assertEqual(result.returncode, 0, result.stderr)
                retired = launch.attempt.with_name(f"{launch.attempt.name}.1")
                self.assertIn(f"{retired} keeps the earlier attempt, {why}; preparing again", result.stderr)
                self.assertEqual(launch.calls(), launch.local_calls())
                self.assertEqual(sorted(path.name for path in retired.iterdir()), sorted(kept))
                self.assertEqual(launch.kept(), LOCAL_KEPT)

    def test_an_earlier_local_attempt_refuses_instead_of_retrying(self):
        self.launch.local()
        self.launch.attempt.mkdir()
        (self.launch.attempt / "request.json").write_text(json.dumps({"lane": LANE, "run": RUN, "route": "local", "worktree": LOCAL_NAME}))
        (self.launch.attempt / "worker-start.json").write_text(json.dumps({"ok": True, "result": {"taskId": "task_l", "dispatchId": "ctx_l"}}))
        self.assert_failed(self.launch, self.launch.run(model="fable", WORKER_LOCAL="1"), f"{self.launch.attempt} holds an earlier attempt of this brief, and a brief gets one attempt; inspect it instead", [CURRENT])
        self.assertEqual(self.launch.kept(), ["request.json", "worker-start.json"])

    def test_a_local_attempt_that_never_ran_worker_start_launches_again_under_the_worktree_name_it_kept(self):
        earlier = "run_0"
        kept = f"{LANE}-{hashlib.sha256(earlier.encode()).hexdigest()[:8]}"
        path = f"/w/{kept}"
        for listed in (True, False):
            with self.subTest(listed=listed):
                launch = self.fresh()
                launch.local()
                launch.attempt.mkdir()
                (launch.attempt / "request.json").write_text(json.dumps({"lane": LANE, "run": earlier, "route": "local", "worktree": kept}))
                if listed:
                    launch.reply("orca worktree list", {"out": {"ok": True, "result": {"worktrees": [{"displayName": kept, "path": path}]}}})
                    launch.reply("orca terminal create", {"out": {"ok": True, "result": {"terminal": {"handle": LOCAL_TERMINAL}}}})
                result = launch.run(model="fable", WORKER_LOCAL="1")
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = launch.calls()
                created = [call for call in calls if call[:3] == ["orca", "worktree", "create"]]
                self.assertEqual(created, [] if listed else [["orca", "worktree", "create", "--name", kept, "--repo", f"id:{REPO}", "--base-branch", BASE_BRANCH, "--setup", "run", "--json"]])
                if listed:
                    self.assertIn(["orca", "terminal", "create", "--worktree", f"path:{path}", "--title", LANE, "--json"], calls)
                self.assertEqual(json.loads(result.stdout)["worktree"], path if listed else LOCAL_WORKTREE)
                self.assertEqual({key: json.loads(launch.receipt("request.json"))[key] for key in ("run", "worktree")}, {"run": RUN, "worktree": kept})
                self.assertEqual(launch.kept(), LOCAL_KEPT)


class SpriteTest(LaunchTest):
    def test_the_default_prepares_one_sprite_and_starts_it_once(self):
        self.launch.sprite()
        result = self.launch.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(json.loads(result.stdout), {"state": "ready", "task": "task_a", "dispatch": "ctx_a", "terminal": TERMINAL, "worktree": ROOT, "lane": LANE, "workspace": WORKSPACE, "allocation": "warm"})
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *self.launch.tools(), self.launch.start()])
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)

    def test_the_attempt_keeps_every_raw_output_and_the_request(self):
        self.launch.sprite()
        self.assertEqual(self.launch.run().returncode, 0)
        self.assertEqual(self.launch.receipt("allocation.json"), json.dumps(allocation()))
        self.assertEqual(json.loads(self.launch.receipt("prepared.json")), PREPARED)
        self.assertEqual(self.launch.receipt("prepare.err"), "prepared on sprites\n")
        self.assertEqual(self.launch.receipt("prepare.status"), "0\n")
        self.assertEqual(self.launch.receipt("worker-start.json"), json.dumps(RECEIPT))
        self.assertEqual(self.launch.receipt("worker-start.status"), "0\n")
        self.assertEqual(
            json.loads(self.launch.receipt("request.json")),
            {
                "lane": LANE,
                "run": RUN,
                "route": "sprite",
                "worktree": None,
                "agent": {**AGENT, "serviceTier": None},
                "ref": None,
                "expectBase": None,
                "brief": {"path": str(self.launch.brief), "sha256": SHA, "bytes": BYTES},
            },
        )

    def test_receipts_stay_in_the_attempt(self):
        self.launch.sprite()
        self.assertEqual(self.launch.run().returncode, 0)
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)
        self.assertEqual(list(self.launch.home.iterdir()), [])

    def test_an_unset_cli_runs_cc_remote_from_path_against_the_given_config(self):
        self.launch.sprite()
        self.assertEqual(self.launch.run().returncode, 0)
        self.assertEqual(self.launch.cc_remote(), {(str(self.launch.cli), str(self.launch.config))})
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *self.launch.tools(), self.launch.start()])
        self.assertFalse(any("--config" in argv for argv in self.launch.calls()))

    def test_a_sprite_launch_without_a_config_refuses_before_any_call(self):
        self.launch.sprite()
        self.assert_refused(self.launch, self.launch.run(CC_REMOTE_CONFIG=""), "set CC_REMOTE_CONFIG, or ccRemoteConfig in .orca.json, to the cc-remote config that names the repository a Sprite checks out; nothing was prepared", [])

    def test_an_explicit_cli_runs_against_the_checkouts_config_at_the_helper_pin(self):
        selected = fake(self.launch.root / "selected" / "cc-remote")
        self.launch.sprite()
        result = self.launch.run(WORKER_CC_REMOTE=str(selected))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.cc_remote(), {(str(selected), str(self.launch.config))})
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *self.launch.tools(), self.launch.start()])

    def test_an_explicit_cli_off_the_helper_pin_refuses_before_allocation(self):
        for route in ("unset", "path", "link"):
            with self.subTest(route=route):
                launch = self.fresh()
                selected = fake(launch.root / "selected" / "cc-remote")
                link = launch.root / "linked.yaml"
                link.symlink_to(launch.config)
                config = link if route == "link" else launch.config
                env = {} if route == "unset" else {"CC_REMOTE_CONFIG": str(config)}
                launch.sprite(version={"err": "9.8.9\n"})
                self.assert_refused(launch, launch.run(WORKER_CC_REMOTE=str(selected), **env), f"{selected} version printed 9.8.9, and {config} pins bootstrap helper {PIN}; a Sprite needs the CLI at exactly its helper's release; nothing was prepared", [VERSION])

    def test_a_relative_config_is_the_callers_file_for_the_check_and_for_cc_remote(self):
        relative = "cc-remote/config.yaml"
        for own in (True, False):
            with self.subTest(own=own):
                launch = self.fresh()
                cwd = launch.checkout if own else launch.root / "other"
                config = cwd / relative
                config.parent.mkdir(parents=True, exist_ok=True)
                config.write_text("provider: sprites\n")
                launch.sprite()
                result = launch.run(cwd=cwd, CC_REMOTE_CONFIG=relative)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(launch.cc_remote(), {(str(launch.cli), str(config))})
                self.assertEqual(launch.calls(), [*([VERSION] if own else []), CURRENT, launch.prepare(), *launch.tools(), launch.start()])

    def test_an_explicit_config_of_another_checkout_leaves_the_version_to_cc_remote(self):
        other = self.launch.root / "other" / "config.yaml"
        other.parent.mkdir()
        other.write_text("provider: sprites\n")
        self.launch.sprite(version={"err": "9.8.9\n"})
        result = self.launch.run(CC_REMOTE_CONFIG=str(other))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.cc_remote(), {(str(self.launch.cli), str(other))})
        self.assertEqual(self.launch.calls(), [CURRENT, self.launch.prepare(), *self.launch.tools(), self.launch.start()])

    def test_aliases_and_full_claude_ids_reach_prepare_unchanged(self):
        for model, model_id in (("opus", "claude-opus-5-5"), ("sonnet", "claude-sonnet-5-5"), ("claude-opus-5-5", "claude-opus-5-5"), ("claude-haiku-4-5-20251001", "claude-haiku-4-5-20251001")):
            with self.subTest(model=model):
                launch = self.fresh()
                launch.sprite(prepared={**PREPARED, "agent": {**AGENT, "model": model_id, "effort": "max"}})
                result = launch.run(model=model, effort="max")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(launch.calls(), [VERSION, CURRENT, launch.prepare(model=model_id, effort="max"), *launch.tools(), launch.start()])

    def test_a_codex_model_keeps_its_effort_tier_ref_and_mcp(self):
        agent = {"kind": "codex", "model": "gpt-6.1-sol", "effort": "xhigh", "serviceTier": "fast", "mcp": ["{}"]}
        self.launch.sprite(prepared={**PREPARED, "agent": agent}, allocated=allocation({**PREPARED, "agent": agent}, ref="release/2026.10"))
        result = self.launch.run(model="gpt-6.1-sol", WORKER_SERVICE_TIER="fast", WORKER_INCIDENT="1", WORKER_REF="release/2026.10", WORKER_MCP_CONFIG="{}")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = self.launch.prepare("--ref", "release/2026.10", "--service-tier", "fast", "--mcp-config", "{}", model="gpt-6.1-sol", kind="codex")
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, expected, *self.launch.tools(), self.launch.start()])
        request = json.loads(self.launch.receipt("request.json"))
        self.assertEqual((request["agent"], request["ref"]), ({"kind": "codex", "model": "gpt-6.1-sol", "effort": "xhigh", "serviceTier": "fast"}, "release/2026.10"))

    def test_worker_env_reaches_prepare_as_one_env_flag_per_word(self):
        self.launch.sprite()
        result = self.launch.run(WORKER_ENV="CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000 ENABLE_PROMPT_CACHING_1H=1")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = self.launch.prepare("--env", "CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000", "--env", "ENABLE_PROMPT_CACHING_1H=1")
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, expected, *self.launch.tools(), self.launch.start()])

    def test_a_worker_env_word_that_is_not_name_value_refuses_before_any_call(self):
        self.assert_refused(self.launch, self.launch.run(WORKER_ENV="ENABLE_PROMPT_CACHING_1H=1 stray"), "WORKER_ENV holds stray, and each of its words is NAME=VALUE with no spaces; nothing launched", [])

    def test_a_hex_spelled_branch_passes_unchanged_as_a_ref(self):
        self.launch.sprite(allocated=allocation(ref="deadbeef"))
        result = self.launch.run(WORKER_REF="deadbeef")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare("--ref", "deadbeef"), *self.launch.tools(), self.launch.start()])

    def test_a_moved_branch_attaches_on_its_actual_base(self):
        moved = "2" * 40
        self.launch.sprite(prepared={**PREPARED, "baseCommit": moved})
        result = self.launch.run(WORKER_REF="dev")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare("--ref", "dev"), *self.launch.tools(), self.launch.start()])
        self.assertEqual(json.loads(self.launch.receipt("prepared.json"))["baseCommit"], moved)

    def test_an_expected_base_that_matches_attaches(self):
        self.launch.sprite()
        result = self.launch.run(WORKER_EXPECT_BASE=BASE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *self.launch.tools(), self.launch.start()])
        self.assertEqual(json.loads(self.launch.receipt("request.json"))["expectBase"], BASE)

    def test_an_expected_base_mismatch_destroys_the_prepared_sprite(self):
        expected = "3" * 64
        self.launch.sprite()
        result = self.launch.run(WORKER_EXPECT_BASE=expected)
        self.assert_failed(self.launch, result, f"the Sprite checked out {BASE}, not WORKER_EXPECT_BASE {expected}, so nothing was attached, its Sprite {WORKSPACE} is destroyed, and nothing is prepared again", [VERSION, CURRENT, self.launch.prepare(), DESTROY])
        self.assertEqual(self.launch.kept(), PREPARE_KEPT)

    def test_a_failed_prepare_keeps_its_raw_output_and_attaches_nothing(self):
        partial = json.dumps(allocation({**PREPARED, "prepared": False}))
        self.launch.sprite()
        self.launch.reply("cc-remote orca prepare", {"rc": 1, "out": partial, "err": "cc-remote: provider timed out\n"})
        result = self.launch.run()
        self.assert_failed(self.launch, result, f"cc-remote orca prepare exited 1; {self.launch.attempt} keeps its output, nothing was attached, and launching this brief again prepares afresh", [VERSION, CURRENT, self.launch.prepare()])
        self.assertEqual(self.launch.kept(), ALLOCATION_KEPT)
        self.assertEqual(self.launch.receipt("allocation.json"), partial)
        self.assertEqual(self.launch.receipt("prepare.err"), "cc-remote: provider timed out\n")
        self.assertEqual(self.launch.receipt("prepare.status"), "1\n")

    def test_a_prepared_record_that_differs_from_the_request_attaches_nothing(self):
        workspace = "a task other than the allocated Sprite agents workspace"
        agent = "an agent other than claude claude-opus-5-5 at xhigh effort"
        brief = f"a brief other than {BYTES} bytes with SHA-256 {SHA}"
        electron = "an Orca runtime serving on Electron, not its managed server"
        placement = "a task without its environment, terminal, repoId::projectRoot worktree, and brief path"
        cases = (
            ("", "not one JSON object"),
            (json.dumps(PREPARED) * 2, "not one JSON object"),
            (json.dumps([PREPARED]), "not one JSON object"),
            ("prepared", "not readable as JSON"),
            ({**PREPARED, "prepared": False}, "prepared is not true"),
            ({key: value for key, value in PREPARED.items() if key != "managedServer"}, electron),
            ({**PREPARED, "managedServer": ""}, electron),
            ({**PREPARED, "workspace": "lane-b"}, workspace),
            ({**PREPARED, "provider": "namespace"}, workspace),
            ({**PREPARED, "profile": "tests"}, workspace),
            ({**PREPARED, "agent": {**AGENT, "kind": "codex"}}, agent),
            ({**PREPARED, "agent": {**AGENT, "model": "claude-sonnet-5-5"}}, agent),
            ({**PREPARED, "agent": {**AGENT, "effort": "high"}}, agent),
            ({**PREPARED, "agent": {**AGENT, "serviceTier": "fast"}}, agent),
            ({**PREPARED, "brief": {**PREPARED["brief"], "sha256": "0" * 64}}, brief),
            ({**PREPARED, "brief": {**PREPARED["brief"], "bytes": BYTES + 1}}, brief),
            ({key: value for key, value in PREPARED.items() if key != "environment"}, placement),
            ({**PREPARED, "terminal": ""}, placement),
            ({**PREPARED, "worktreeId": "repo-1::/home/sprite/other"}, placement),
            ({**PREPARED, "brief": {**PREPARED["brief"], "path": None}}, placement),
        )
        for index, (prepared, reason) in enumerate(cases):
            with self.subTest(case=index, reason=reason):
                launch = self.fresh()
                launch.sprite(prepared=prepared)
                if isinstance(prepared, dict) and prepared["workspace"] == WORKSPACE:
                    self.assert_failed(launch, launch.run(), f"{launch.attempt}/allocation.json holds {reason}, so nothing was attached, and its Sprite {WORKSPACE} is destroyed", [VERSION, CURRENT, launch.prepare(), DESTROY])
                else:
                    self.assert_failed(launch, launch.run(), f"{launch.attempt}/allocation.json holds {reason}, so the prepared worker stays idle and unattached", [VERSION, CURRENT, launch.prepare()])
                self.assertEqual(launch.kept(), ALLOCATION_KEPT)

    def test_an_empty_pool_creates_the_requested_lane_once(self):
        prepared = {**PREPARED, "workspace": LANE, "machine": LANE, "environment": LANE}
        self.launch.sprite(allocated=allocation(prepared, workspace=LANE, allocation="created"))
        result = self.launch.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["workspace"], LANE)
        self.assertEqual(json.loads(result.stdout)["allocation"], "created")
        self.assertEqual(json.loads(self.launch.receipt("prepared.json")), prepared)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *self.launch.tools(), self.launch.start(prepared)])

    def test_an_allocation_must_bind_the_requested_lane_and_actual_task(self):
        cases = (
            (allocation(schemaVersion=2), "an allocation other than schema 1 for lane lane-a"),
            (allocation(lane="lane-b"), "an allocation other than schema 1 for lane lane-a"),
            (allocation(workspace="../pool"), "an invalid allocated workspace"),
            (allocation(allocation="existing"), "an allocation kind inconsistent with its workspace"),
            (allocation(allocation="created"), "an allocation kind inconsistent with its workspace"),
            (allocation(workspace=LANE), "an allocation kind inconsistent with its workspace"),
            (allocation(ref="release"), "a ref other than the requested branch or tag"),
            (allocation(task=None), "a task other than the allocated Sprite agents workspace"),
            (allocation(head="2" * 40), "an allocation HEAD inconsistent with its task"),
            (PREPARED, "an allocation other than schema 1 for lane lane-a"),
        )
        for response, reason in cases:
            with self.subTest(response=response):
                launch = self.fresh()
                launch.sprite(allocated=response)
                result = launch.run(WORKER_REF="dev")
                owned = isinstance(response, dict) and response.get("lane") == LANE and response.get("allocation") in ("warm", "created") and response["workspace"] == WORKSPACE and (response["task"] or {}).get("workspace") == WORKSPACE
                if owned:
                    self.assert_failed(launch, result, f"{launch.attempt}/allocation.json holds {reason}, so nothing was attached, and its Sprite {WORKSPACE} is destroyed", [VERSION, CURRENT, launch.prepare("--ref", "dev"), DESTROY])
                else:
                    self.assert_failed(launch, result, f"{launch.attempt}/allocation.json holds {reason}, so the prepared worker stays idle and unattached", [VERSION, CURRENT, launch.prepare("--ref", "dev")])
                self.assertEqual(launch.kept(), ALLOCATION_KEPT)

    def test_replenishment_failure_does_not_replay_an_attached_worker(self):
        refill = {"target": 1, "failure": {"exit": None, "httpStatus": None, "bytes": 4, "sha256": "0" * 64}}
        self.launch.sprite(allocated=allocation(replenish=refill))
        result = self.launch.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.launch.receipt("allocation.json"))["replenish"], refill)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *self.launch.tools(), self.launch.start()])

    def test_a_failed_sprite_start_keeps_its_prepared_worker_and_receipts(self):
        refused = {"rc": 1, "out": {"ok": False, "error": {"code": "consumer_fenced", "message": "fenced"}, "result": {"taskId": "task_a", "dispatchId": "ctx_a"}}}
        self.launch.sprite(started=refused)
        result = self.launch.run()
        self.assert_failed(self.launch, result, f"orca orchestration worker-start exited 1: consumer_fenced: fenced; kept terminal {TERMINAL}, worktree {ROOT}, and the receipt in {self.launch.attempt}", [VERSION, CURRENT, self.launch.prepare(), *self.launch.tools(), self.launch.start()])
        self.assertEqual(self.launch.receipt("worker-start.status"), "1\n")
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)

    def test_a_worker_start_orca_never_recorded_runs_once_more_under_its_request_id(self):
        self.launch.sprite()
        self.launch.reply("orca orchestration worker-start", UNREACHED, {"out": RECEIPT})
        self.launch.reply("orca orchestration request-show", request_state("absent"))
        result = self.launch.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["dispatch"], "ctx_a")
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *self.launch.tools(), self.launch.start(), REQUEST_SHOW, self.launch.start(retry=REQUEST)])
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)

    def test_a_worker_start_orca_recorded_or_could_not_look_up_is_never_run_again(self):
        message = UNREACHED["out"]["error"]["message"]
        for shown in (request_state("completed"), request_state("pending"), {"rc": 1}):
            with self.subTest(shown=shown):
                launch = self.fresh()
                launch.sprite(started=UNREACHED)
                launch.reply("orca orchestration request-show", shown)
                line = f"orca orchestration worker-start exited 1: runtime_error: {message}; kept terminal {TERMINAL}, worktree {ROOT}, and the receipt in {launch.attempt}"
                self.assert_failed(launch, launch.run(), line, [VERSION, CURRENT, launch.prepare(), *launch.tools(), launch.start(), REQUEST_SHOW])
                self.assertEqual(launch.kept(), SPRITE_KEPT)

    def test_a_worker_start_unrecorded_twice_fails_in_one_line_and_a_relaunch_retries_it_on_the_same_sprite(self):
        self.launch.sprite()
        self.launch.reply("orca orchestration worker-start", UNREACHED, UNREACHED)
        self.launch.reply("orca orchestration request-show", request_state("absent"), request_state("absent"))
        line = f"no worker started on Sprite {WORKSPACE}: orca orchestration worker-start failed twice before Orca recorded it, with runtime_error: Timed out waiting for the remote Orca runtime; its terminal {TERMINAL} waits idle, and launching this brief again retries worker-start in it"
        first = [VERSION, CURRENT, self.launch.prepare(), *self.launch.tools(), self.launch.start(), REQUEST_SHOW, self.launch.start(retry=REQUEST), REQUEST_SHOW]
        self.assert_failed(self.launch, self.launch.run(), line, first)
        self.assertEqual(self.launch.kept(), sorted([*SPRITE_KEPT, "worker-start.unsent"]))
        self.assertEqual(self.launch.receipt("worker-start.unsent"), f"{REQUEST}\n")
        self.launch.sprite(copied={})
        relaunched = self.launch.run()
        self.assertEqual(relaunched.returncode, 0, relaunched.stderr)
        self.assertEqual(json.loads(relaunched.stdout)["dispatch"], "ctx_a")
        self.assertEqual(self.launch.calls(), [*first, VERSION, CURRENT, ENV_SHOW, self.launch.copy(), *self.launch.tools(), self.launch.start(retry=REQUEST)])
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)

    def bound(self, launch: Launch, code: int, said: str) -> None:
        bound = launch.bin / "bound"
        bound.write_text(first_bind(code, said))
        bound.chmod(0o755)

    def test_a_bind_that_refuses_before_worker_start_fails_in_one_line_and_a_relaunch_retries_it_on_the_same_sprite(self):
        self.bound(self.launch, 75, BIND_REFUSAL)
        self.launch.sprite()
        line = f"no worker started on Sprite {WORKSPACE}: WORKER_BIND refused before worker-start ran: {BIND_REFUSAL}; its terminal {TERMINAL} waits idle, and launching this brief again retries worker-start in it"
        first = [VERSION, self.launch.prepare(), *self.launch.tools(), ["bound", "--", *self.launch.start()]]
        self.assert_failed(self.launch, self.launch.run(WORKER_BIND="bound --"), line, first)
        self.assertEqual(self.launch.receipt("worker-start.status"), "75\n")
        self.assertEqual(self.launch.receipt("worker-start.unsent"), "\n")
        self.launch.sprite(copied={})
        relaunched = self.launch.run(WORKER_BIND="bound --")
        self.assertEqual(relaunched.returncode, 0, relaunched.stderr)
        self.assertEqual(json.loads(relaunched.stdout)["dispatch"], "ctx_a")
        self.assertEqual(self.launch.calls(), [*first, VERSION, ENV_SHOW, self.launch.copy(), *self.launch.tools(), ["bound", "--", *self.launch.start()], self.launch.start()])
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)

    def test_a_bind_that_crashes_before_orca_records_a_task_fails_in_one_line_and_a_relaunch_retries_it_on_the_same_sprite(self):
        self.bound(self.launch, 1, BIND_CRASH)
        self.launch.sprite()
        self.launch.reply("orca orchestration task-list", tasks("lane-other"))
        line = f"no worker started on Sprite {WORKSPACE}: worker-start exited 1 before Orca answered: {BIND_CRASH}, and Run {RUN} holds no task for {LANE}; its terminal {TERMINAL} waits idle, and launching this brief again retries worker-start in it"
        first = [VERSION, self.launch.prepare(), *self.launch.tools(), ["bound", "--", *self.launch.start()], TASK_LIST]
        self.assert_failed(self.launch, self.launch.run(WORKER_BIND="bound --"), line, first)
        self.assertEqual((self.launch.receipt("worker-start.status"), self.launch.receipt("worker-start.json")), ("1\n", ""))
        self.assertEqual(self.launch.receipt("worker-start.unsent"), "\n")
        self.launch.sprite(copied={})
        relaunched = self.launch.run(WORKER_BIND="bound --")
        self.assertEqual(relaunched.returncode, 0, relaunched.stderr)
        self.assertEqual(json.loads(relaunched.stdout)["dispatch"], "ctx_a")
        self.assertEqual(self.launch.calls(), [*first, VERSION, ENV_SHOW, self.launch.copy(), *self.launch.tools(), ["bound", "--", *self.launch.start()], self.launch.start()])
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)

    def test_an_earlier_attempt_whose_worker_start_never_answered_is_reused_only_while_its_run_holds_no_task_for_the_lane(self):
        for shown, reused in ((tasks(), True), (tasks(LANE), False)):
            with self.subTest(reused=reused):
                launch = self.fresh()
                self.bound(launch, 1, BIND_CRASH)
                launch.sprite()
                kept = f"orca orchestration worker-start exited 1; kept terminal {TERMINAL}, worktree {ROOT}, and the receipt in {launch.attempt}"
                first = [VERSION, launch.prepare(), *launch.tools(), ["bound", "--", *launch.start()], TASK_LIST]
                self.assert_failed(launch, launch.run(WORKER_BIND="bound --"), kept, first)
                self.assertFalse((launch.attempt / "worker-start.unsent").exists())
                launch.sprite(copied={})
                launch.reply("orca orchestration task-list", shown)
                relaunched = launch.run(WORKER_BIND="bound --")
                if reused:
                    self.assertEqual(relaunched.returncode, 0, relaunched.stderr)
                    self.assertEqual(json.loads(relaunched.stdout)["dispatch"], "ctx_a")
                    self.assertEqual(launch.calls(), [*first, VERSION, ENV_SHOW, TASK_LIST, launch.copy(), *launch.tools(), ["bound", "--", *launch.start()], launch.start()])
                else:
                    self.assert_failed(launch, relaunched, f"{launch.attempt} holds an earlier attempt of this brief, and a brief gets one attempt; inspect it instead", [*first, VERSION, ENV_SHOW, TASK_LIST])

    def test_a_relaunch_copies_a_rewritten_brief_to_the_prepared_sprite_and_starts_its_worker_on_it(self):
        rewritten = {**PREPARED, "brief": {**PREPARED["brief"], "sha256": hashlib.sha256(REWRITTEN.encode()).hexdigest(), "bytes": len(REWRITTEN.encode())}}
        for failed in ("tools", "worker-start"):
            with self.subTest(failed=failed):
                launch = self.fresh()
                launch.sprite()
                if failed == "tools":
                    launch.reply("curl", UNREAD)
                else:
                    launch.reply("orca orchestration worker-start", UNREACHED, UNREACHED)
                    launch.reply("orca orchestration request-show", request_state("absent"), request_state("absent"))
                self.assertEqual(launch.run().returncode, 1)
                first = launch.calls()
                launch.brief.write_text(REWRITTEN)
                launch.sprite(copied={})
                relaunched = launch.run()
                self.assertEqual(relaunched.returncode, 0, relaunched.stderr)
                self.assertEqual(json.loads(relaunched.stdout)["dispatch"], "ctx_a")
                retry = REQUEST if failed == "worker-start" else None
                self.assertEqual(launch.calls(), [*first, VERSION, CURRENT, ENV_SHOW, launch.copy(), *launch.tools(), launch.start(retry=retry, brief=REWRITTEN)])
                self.assertEqual(launch.records()[len(first) + 3]["stdin"], REWRITTEN)
                self.assertEqual(json.loads(launch.receipt("prepared.json")), rewritten)
                self.assertEqual(json.loads(launch.receipt("allocation.json"))["task"], rewritten)
                self.assertEqual(launch.kept(), SPRITE_KEPT)

    def test_a_relaunch_whose_prepared_sprite_answers_no_ssh_retires_the_attempt_and_prepares_afresh(self):
        rewritten = {**PREPARED, "brief": {**PREPARED["brief"], "sha256": hashlib.sha256(REWRITTEN.encode()).hexdigest(), "bytes": len(REWRITTEN.encode())}}
        gone = "ssh: connect to host pool-spare port 22: Connection refused"
        self.launch.sprite()
        self.launch.reply("curl", UNREAD)
        self.assertEqual(self.launch.run().returncode, 1)
        first = self.launch.calls()
        self.launch.brief.write_text(REWRITTEN)
        self.launch.sprite(prepared=rewritten, copied={"rc": 255, "err": f"{gone}\n"})
        relaunched = self.launch.run()
        self.assertEqual(relaunched.returncode, 0, relaunched.stderr)
        retired = self.launch.attempt.with_name(f"{self.launch.attempt.name}.1")
        self.assertEqual(relaunched.stderr, f"{retired} keeps the earlier attempt, whose Sprite {WORKSPACE} answered no ssh connection before any worker started: {gone}, and its Sprite {WORKSPACE} is destroyed; preparing again\n")
        self.assertEqual(self.launch.calls(), [*first, VERSION, CURRENT, ENV_SHOW, self.launch.copy(), DESTROY, self.launch.prepare(), *self.launch.tools(), self.launch.start(brief=REWRITTEN)])
        self.assertEqual(sorted(path.name for path in retired.iterdir()), TOOLS_KEPT)
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)

    def test_a_relaunch_whose_prepared_sprite_orca_answers_unknown_environment_for_retires_the_attempt_and_prepares_afresh(self):
        unanswered = {"rc": 1, "out": {"ok": True, "result": {"taskId": "task_a", "dispatchId": "ctx_a", "state": "outcome_unknown", "stage": "remote_attach", "failedStage": "remote_attach", "effects": []}}}
        kept = f"orca orchestration worker-start exited 1; kept terminal {TERMINAL}, worktree {ROOT}, and the receipt in {{attempt}}"
        timed_out = {"rc": 1, "out": {"ok": False, "error": {"code": "runtime_error", "message": "Timed out waiting for the local Orca runtime"}}}
        for started, failed in (({"out": RECEIPT}, None), (unanswered, kept)):
            for shown, retired in ((UNKNOWN_ENVIRONMENT, True), (timed_out, False), ({"out": {"ok": True, "result": {"environment": {"id": PREPARED["environmentId"]}}}}, False)):
                with self.subTest(started=started["out"]["result"]["state"], retired=retired, shown=shown["out"]["ok"]):
                    launch = self.fresh()
                    launch.sprite(started=started)
                    first = [VERSION, CURRENT, launch.prepare(), *launch.tools(), launch.start()]
                    if failed:
                        self.assert_failed(launch, launch.run(), failed.format(attempt=launch.attempt), first)
                    else:
                        self.assertEqual(launch.run().returncode, 0)
                    launch.sprite()
                    launch.reply("orca environment show", shown)
                    relaunched = launch.run()
                    if not retired:
                        self.assert_failed(launch, relaunched, f"{launch.attempt} holds an earlier attempt of this brief, and a brief gets one attempt; inspect it instead", [*first, VERSION, CURRENT, ENV_SHOW])
                        continue
                    self.assertEqual(relaunched.returncode, 0, relaunched.stderr)
                    moved = launch.attempt.with_name(f"{launch.attempt.name}.1")
                    self.assertEqual(relaunched.stderr, f"{moved} keeps the earlier attempt, whose Sprite {WORKSPACE} Orca answers Unknown environment for, so nothing it started can run, and its Sprite {WORKSPACE} is destroyed; preparing again\n")
                    self.assertEqual(launch.calls(), [*first, VERSION, CURRENT, ENV_SHOW, DESTROY, launch.prepare(), *launch.tools(), launch.start()])
                    self.assertEqual(sorted(path.name for path in moved.iterdir()), SPRITE_KEPT)
                    self.assertEqual(launch.kept(), SPRITE_KEPT)

    def test_a_prepared_sprite_that_refuses_the_brief_keeps_the_attempt_for_another_launch(self):
        refused = f"bash: line 1: {PREPARED['brief']['path']}: No such file or directory"
        self.launch.sprite()
        self.launch.reply("curl", UNREAD)
        self.assertEqual(self.launch.run().returncode, 1)
        first = self.launch.calls()
        self.launch.sprite(copied={"rc": 1, "err": f"{refused}\n"})
        line = f"the Sprite {WORKSPACE} did not take the brief at {PREPARED['brief']['path']}: {refused}; the prepared worker stays idle and unattached, and launching this brief again copies it again"
        self.assert_failed(self.launch, self.launch.run(), line, [*first, VERSION, CURRENT, ENV_SHOW, self.launch.copy()])
        self.assertEqual(self.launch.kept(), TOOLS_KEPT)

    def test_a_continued_workspace_prepares_its_own_sprite_with_existing_and_starts_there(self):
        self.launch.sprite(allocated=PREPARED)
        result = self.launch.run(WORKER_WORKSPACE=WORKSPACE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(continued=WORKSPACE), *self.launch.tools(), self.launch.start()])
        self.assertEqual(json.loads(result.stdout), {"state": "ready", "task": "task_a", "dispatch": "ctx_a", "terminal": TERMINAL, "worktree": ROOT, "lane": LANE, "workspace": WORKSPACE, "allocation": "existing"})
        self.assertEqual(json.loads(self.launch.receipt("prepared.json")), PREPARED)

    def test_a_continued_workspace_resumes_the_claude_session_it_names(self):
        self.launch.sprite(allocated=PREPARED)
        result = self.launch.run(WORKER_WORKSPACE=WORKSPACE, WORKER_SESSION=SESSION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare("--resume", SESSION, continued=WORKSPACE), *self.launch.tools(), self.launch.start()])

    def test_a_continued_workspace_takes_no_ref(self):
        self.launch.sprite(allocated=PREPARED)
        self.assert_refused(self.launch, self.launch.run(WORKER_WORKSPACE=WORKSPACE, WORKER_REF="dev"), "WORKER_WORKSPACE continues that Sprite's checkout as it is, so it takes no WORKER_REF; nothing was prepared", [])

    def test_a_prepare_that_continued_another_workspace_attaches_nothing(self):
        self.launch.sprite(allocated={**PREPARED, "workspace": "pool-other"})
        result = self.launch.run(WORKER_WORKSPACE=WORKSPACE)
        self.assert_failed(self.launch, result, f"{self.launch.attempt}/allocation.json holds an allocation kind inconsistent with its workspace, so the prepared worker stays idle and unattached", [VERSION, CURRENT, self.launch.prepare(continued=WORKSPACE)])

    def test_continuing_a_workspace_keeps_an_unstarted_attempt_on_another_sprite_and_prepares_its_own(self):
        self.launch.sprite()
        self.launch.reply("orca orchestration worker-start", UNREACHED, UNREACHED)
        self.launch.reply("orca orchestration request-show", request_state("absent"), request_state("absent"))
        self.assertEqual(self.launch.run().returncode, 1)
        held = {**PREPARED, "workspace": "pool-held"}
        self.launch.sprite(allocated=held)
        calls = self.launch.calls()
        result = self.launch.run(WORKER_WORKSPACE="pool-held")
        self.assertEqual(result.returncode, 0, result.stderr)
        retired = self.launch.attempt.with_name(f"{self.launch.attempt.name}.1")
        self.assertEqual(result.stderr, f"{retired} keeps the earlier attempt, which prepared Sprite {WORKSPACE} while this launch continues on Sprite pool-held, and its Sprite {WORKSPACE} is destroyed; preparing again\n")
        self.assertEqual(self.launch.calls(), [*calls, VERSION, CURRENT, DESTROY, self.launch.prepare(continued="pool-held"), *self.launch.tools(), self.launch.start()])
        self.assertEqual(json.loads(result.stdout)["workspace"], "pool-held")
        self.assertTrue((retired / "worker-start.unsent").is_file())

    def test_a_sprite_whose_tool_releases_cannot_be_read_stays_idle_and_a_relaunch_reruns_them_on_it(self):
        self.launch.sprite()
        self.launch.reply("curl", UNREAD)
        reason = f"curl could not read {download('yasyf/cc-context', 'v0.95.43', 'checksums.txt')}, so no tool was installed"
        line = f"the Sprite did not take the released ccx and cc-notes, the tools WORKER_TOOLS names, or the wasm toolchain: {reason}; the prepared worker stays idle and unattached, {self.launch.attempt} keeps tools.err, and launching this brief again reruns the tool check on that Sprite"
        first = [VERSION, CURRENT, self.launch.prepare(), *self.launch.tools()[:2]]
        self.assert_failed(self.launch, self.launch.run(), line, first)
        self.assertEqual(self.launch.kept(), TOOLS_KEPT)
        self.launch.sprite(copied={})
        relaunched = self.launch.run()
        self.assertEqual(relaunched.returncode, 0, relaunched.stderr)
        self.assertEqual(json.loads(relaunched.stdout)["dispatch"], "ctx_a")
        self.assertEqual(self.launch.calls(), [*first, VERSION, CURRENT, ENV_SHOW, self.launch.copy(), *self.launch.tools(), self.launch.start()])
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)

    def test_a_launch_while_ccx_refuses_github_api_reads_under_its_floor_installs_the_releases_this_mac_runs(self):
        self.launch.sprite()
        self.launch.reply("gh api", {"rc": 1, "err": FLOOR}, {"rc": 1, "err": FLOOR})
        result = self.launch.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *self.launch.tools(), self.launch.start()])

    def test_a_release_this_mac_cannot_name_or_whose_checksums_omit_its_asset_starts_no_worker(self):
        checksums = download("yasyf/cc-context", "v0.95.43", "checksums.txt")
        for answered, reply, reason, read in (
            ("ccx", {"out": "dev\n"}, "this Mac's ccx --version printed 'dev', not a release tag, so no tool was installed", 1),
            ("curl", listed("cc-notes", "a" * 64), f"{checksums} names no sha256 for ccx_linux_amd64, so no tool was installed", 2),
        ):
            with self.subTest(answered=answered):
                launch = self.fresh()
                launch.sprite()
                launch.reply(answered, reply)
                line = f"the Sprite did not take the released ccx and cc-notes, the tools WORKER_TOOLS names, or the wasm toolchain: {reason}; the prepared worker stays idle and unattached, {launch.attempt} keeps tools.err, and launching this brief again reruns the tool check on that Sprite"
                self.assert_failed(launch, launch.run(), line, [VERSION, CURRENT, launch.prepare(), *launch.tools()[:read]])
                self.assertEqual(launch.kept(), TOOLS_KEPT)

    def test_a_sprite_whose_own_install_refuses_the_tools_is_destroyed_and_a_relaunch_prepares_a_fresh_one(self):
        for reason in (
            "a login shell runs ccx from /usr/local/bin/ccx, printing 'v0.89.18', not v0.95.43 from /home/sprite/.local/bin/ccx",
            "install: error writing '/home/sprite/.local/bin/ccx': No space left on device",
            "error: could not create temp file /.sprite/languages/rust/rustup/tmp/0f3a_file: Permission denied (os error 13)",
        ):
            with self.subTest(reason=reason):
                launch = self.fresh()
                launch.sprite()
                launch.reply("ssh", {"rc": 1, "err": f"{reason}\n"})
                retired = launch.attempt.with_name(f"{launch.attempt.name}.1")
                line = f"{retired} keeps the earlier attempt, whose Sprite {WORKSPACE} refused the tools a worker needs: {reason}, and its Sprite {WORKSPACE} is destroyed; launching this brief again prepares a fresh Sprite"
                first = [VERSION, CURRENT, launch.prepare(), *launch.tools(), DESTROY]
                self.assert_failed(launch, launch.run(), line, first, code=76)
                self.assertEqual(sorted(path.name for path in retired.iterdir()), TOOLS_KEPT)
                launch.sprite()
                relaunched = launch.run()
                self.assertEqual(relaunched.returncode, 0, relaunched.stderr)
                self.assertEqual(launch.calls(), [*first, VERSION, CURRENT, launch.prepare(), *launch.tools(), launch.start()])
                self.assertEqual(launch.kept(), SPRITE_KEPT)

    def test_a_continued_sprite_that_refuses_the_tools_is_kept_and_the_attempt_retired(self):
        reason = "install: error writing '/home/sprite/.local/bin/ccx': No space left on device"
        self.launch.sprite(allocated=PREPARED)
        self.launch.reply("ssh", {"rc": 1, "err": f"{reason}\n"})
        retired = self.launch.attempt.with_name(f"{self.launch.attempt.name}.1")
        line = f"{retired} keeps the earlier attempt, whose Sprite {WORKSPACE} refused the tools a worker needs: {reason}, and its Sprite {WORKSPACE} is kept, since this launch continues the lane on it; launching this brief again prepares a fresh Sprite once WORKER_WORKSPACE is unset"
        self.assert_failed(self.launch, self.launch.run(WORKER_WORKSPACE=WORKSPACE), line, [VERSION, CURRENT, self.launch.prepare(continued=WORKSPACE), *self.launch.tools()], code=76)
        self.assertEqual(self.launch.kept(), [])

    def test_each_tool_worker_tools_names_goes_to_the_sprite_whole_beside_the_released_ones(self):
        launch = self.fresh()
        launch.sprite()
        tool = launch.root / "drive"
        tool.write_text("#!/usr/bin/env python3\nprint('on the Mac')\n")
        result = launch.run(WORKER_TOOLS=f"drive={tool}")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = base64.b64encode(tool.read_bytes()).decode()
        self.assertEqual(launch.calls(), [VERSION, CURRENT, launch.prepare(), *launch.tools(f"1 drive {text}"), launch.start()])

    def test_a_worker_tools_word_naming_no_file_starts_no_worker(self):
        launch = self.fresh()
        launch.sprite()
        result = launch.run(WORKER_TOOLS="drive=/nowhere/drive")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("WORKER_TOOLS holds drive=/nowhere/drive, and each of its words is NAME=FILE naming a file on this Mac, so no tool was installed", result.stderr)
        self.assertNotIn(launch.start(), launch.calls())

    def test_an_unproven_ready_never_prints_success_or_retries(self):
        result = RECEIPT["result"]
        cases = (
            "",
            "not json",
            json.dumps(RECEIPT) * 2,
            {**RECEIPT, "ok": False},
            {**RECEIPT, "error": {"code": "stale"}},
            {"ok": True, "result": {**result, "state": "outcome_unknown"}},
            {"ok": True, "result": {**result, "runId": "run_2"}},
            {"ok": True, "result": {key: value for key, value in result.items() if key != "taskId"}},
            {"ok": True, "result": {**result, "dispatchId": "ctx a"}},
        )
        for index, receipt in enumerate(cases):
            with self.subTest(case=index):
                launch = self.fresh()
                launch.sprite(started={"out": receipt})
                line = UNREADY.format(run=RUN, terminal=TERMINAL, worktree=ROOT, attempt=launch.attempt)
                self.assert_failed(launch, launch.run(), line, [VERSION, CURRENT, launch.prepare(), *launch.tools(), launch.start()])
                self.assertEqual(launch.kept(), SPRITE_KEPT)


class LocalTest(LaunchTest):
    def test_explicit_local_fable_uses_only_the_existing_local_primitives(self):
        self.launch.local()
        result = self.launch.run(model="fable", WORKER_LOCAL="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"state": "ready", "task": "task_l", "dispatch": "ctx_l", "terminal": LOCAL_TERMINAL, "worktree": LOCAL_WORKTREE})
        self.assertEqual(self.launch.calls(), self.launch.local_calls())
        self.assertEqual(self.launch.kept(), LOCAL_KEPT)
        self.assertEqual(self.launch.receipt("worker-start.status"), "0\n")
        self.assertEqual(json.loads(self.launch.receipt("request.json"))["route"], "local")

    def test_explicit_local_exports_worker_env_to_claude(self):
        self.launch.local()
        result = self.launch.run(model="fable", WORKER_LOCAL="1", WORKER_ENV="CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.calls(), self.launch.local_calls(lane_env=f"{LANE_ENV} CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000"))

    def test_explicit_local_passes_the_requested_repository(self):
        self.launch.local()
        result = self.launch.run(model="fable", WORKER_LOCAL="1", WORKER_REPO="repo-other")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.calls(), self.launch.local_calls(repo="repo-other"))

    def test_explicit_local_keeps_the_exact_model_and_effort(self):
        self.launch.local()
        result = self.launch.run(model="claude-opus-5-5", effort="max", WORKER_LOCAL="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.calls(), self.launch.local_calls(model="claude-opus-5-5", effort="max"))

    def test_a_failed_local_start_keeps_its_terminal_and_worktree(self):
        refused = {"rc": 1, "out": {"ok": False, "error": {"code": "consumer_fenced", "message": "fenced"}}}
        self.launch.local(started=refused)
        result = self.launch.run(model="fable", WORKER_LOCAL="1")
        self.assert_failed(self.launch, result, f"{SPARSE}\norca orchestration worker-start exited 1: consumer_fenced: fenced; kept terminal {LOCAL_TERMINAL}, worktree {LOCAL_WORKTREE}, and the receipt in {self.launch.attempt}", self.launch.local_calls())
        self.assertEqual(self.launch.kept(), LOCAL_KEPT)
        self.assertEqual(self.launch.receipt("worker-start.json"), json.dumps(refused["out"]))
        self.assertEqual(self.launch.receipt("worker-start.status"), "1\n")

    def test_a_local_start_without_a_ready_receipt_prints_no_success(self):
        self.launch.local(started={"out": {"ok": True, "result": {"state": "ready", "taskId": "task_l", "dispatchId": "ctx_l"}}})
        result = self.launch.run(model="fable", WORKER_LOCAL="1")
        self.assert_failed(self.launch, result, f"{SPARSE}\n" + UNREADY.format(run=RUN, terminal=LOCAL_TERMINAL, worktree=LOCAL_WORKTREE, attempt=self.launch.attempt), self.launch.local_calls())
        self.assertEqual(self.launch.kept(), LOCAL_KEPT)

    def test_a_local_idle_timeout_keeps_its_terminal_and_worktree(self):
        self.launch.local(wait={"rc": 1, "out": "timed out"})
        result = self.launch.run(model="fable", WORKER_LOCAL="1")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertEqual(result.stderr, f"{SPARSE}\norca terminal wait failed: timed out\nclaude in {LOCAL_TERMINAL} never went idle; kept it and worktree {LOCAL_WORKTREE}\n")
        self.assertEqual(self.launch.calls(), self.launch.local_calls()[:-1])
        self.assertEqual(self.launch.kept(), ["request.json", "worktree"])

    def test_a_new_local_worktree_is_sparse_with_the_requested_cone(self):
        self.launch.local()
        result = self.launch.run(model="fable", WORKER_LOCAL="1", WORKER_SPARSE="desktop common")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.calls(), self.launch.local_calls(cone=("desktop", "common")))
        self.assertEqual(result.stderr, f"{SPARSE}\n")

    def test_a_local_worktree_without_a_cone_is_a_full_checkout(self):
        self.launch.local()
        result = self.launch.run(model="fable", WORKER_LOCAL="1", WORKER_SPARSE="")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.launch.calls(), self.launch.local_calls(cone=()))

    def test_the_local_route_needs_its_repository_and_base(self):
        for env, line in (
            ({"WORKER_REPO": ""}, "set WORKER_REPO, or repo in .orca.json, to the Orca repository the local route adds its worktree to; nothing launched"),
            ({"WORKER_BASE_BRANCH": ""}, "set WORKER_BASE_BRANCH, or baseBranch in .orca.json, to the branch a local worktree starts from; nothing launched"),
        ):
            with self.subTest(env=env):
                launch = self.fresh()
                launch.local()
                self.assert_refused(launch, launch.run(model="fable", WORKER_LOCAL="1", **env), line, [])

    def test_the_local_route_starts_only_claude(self):
        self.launch.local()
        self.assert_refused(self.launch, self.launch.run(model="gpt-6.1-sol", WORKER_LOCAL="1"), "the local route starts only claude, and codex runs gpt-6.1-sol; nothing launched", [])

    def test_the_local_route_refuses_sprite_checkout_settings(self):
        for env in ({"WORKER_REF": "dev"}, {"WORKER_EXPECT_BASE": BASE}, {"WORKER_WORKSPACE": WORKSPACE}, {"WORKER_SESSION": SESSION}):
            with self.subTest(env=env):
                launch = self.fresh()
                launch.local()
                self.assert_refused(launch, launch.run(model="fable", WORKER_LOCAL="1", **env), "WORKER_REF, WORKER_EXPECT_BASE, WORKER_WORKSPACE and WORKER_SESSION pick a Sprite's checkout or session; the local route takes WORKER_BASE_BRANCH; nothing launched", [])

    def test_the_local_worktree_takes_a_name_scoped_to_the_run(self):
        self.launch.local()
        result = self.launch.run(model="fable", WORKER_LOCAL="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(["orca", "worktree", "create", "--name", LOCAL_NAME, "--repo", f"id:{REPO}", "--base-branch", BASE_BRANCH, "--setup", "run", "--json"], self.launch.calls())
        self.assertEqual(json.loads(self.launch.receipt("request.json"))["worktree"], LOCAL_NAME)

    def test_a_relaunch_into_the_run_s_live_worktree_resumes_it(self):
        self.launch.local()
        self.launch.reply("orca worktree list", {"out": {"ok": True, "result": {"worktrees": [{"displayName": LANE, "path": "/w/other"}, {"displayName": LOCAL_NAME, "path": LOCAL_WORKTREE}]}}})
        self.launch.reply("orca terminal create", {"out": {"ok": True, "result": {"terminal": {"handle": LOCAL_TERMINAL}}}})
        result = self.launch.run(model="fable", WORKER_LOCAL="1")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        calls = self.launch.calls()
        self.assertFalse([call for call in calls if call[:3] == ["orca", "worktree", "create"] or call[0] == "git"])
        self.assertIn(["orca", "terminal", "create", "--worktree", f"path:{LOCAL_WORKTREE}", "--title", LANE, "--json"], calls)
        self.assertEqual(json.loads(result.stdout)["worktree"], LOCAL_WORKTREE)

    def test_a_name_an_earlier_attempt_cut_is_never_cut_again(self):
        earlier = self.launch.briefs / "earlier.worker"
        earlier.mkdir()
        (earlier / "worktree").write_text(f"{LOCAL_NAME}\n")
        self.launch.local()
        result = self.launch.run(model="fable", WORKER_LOCAL="1")
        line = f"worktree {LOCAL_NAME} was cut for {earlier} and is gone, and a name is never cut twice; launch under a new lane name; nothing launched"
        self.assert_failed(self.launch, result, line, self.launch.local_calls()[:2])
        self.assertEqual(self.launch.kept(), ["request.json"])

    def test_a_create_whose_reply_is_lost_waits_while_git_holds_the_worktree_until_orca_lists_it(self):
        self.launch.local()
        self.launch.reply("orca worktree create", DROPPED)
        self.launch.reply("orca worktree list", UNLISTED, UNLISTED, {"out": {"ok": True, "result": {"worktrees": [{"displayName": LOCAL_NAME, "path": LOCAL_WORKTREE}]}}})
        self.launch.reply("orca repo show", SHOWN)
        self.launch.reply("git", {"out": f"worktree {MAIN}\nHEAD {BASE}\nbranch refs/heads/main\n\nworktree {LOCAL_WORKTREE}\nHEAD {BASE}\nlocked initializing\n"}, {"out": ""})
        result = self.launch.run(model="fable", WORKER_LOCAL="1")
        self.assertEqual((result.returncode, result.stderr), (0, f"{SPARSE}\n"))
        self.assertEqual(json.loads(result.stdout)["worktree"], LOCAL_WORKTREE)
        calls = self.launch.local_calls()
        self.assertEqual(self.launch.calls(), [*calls[:3], LIST, REPO_SHOW, GIT_LIST, LIST, *calls[3:]])
        self.assertEqual(self.launch.kept(), LOCAL_KEPT)

    def test_a_create_that_leaves_git_no_worktree_of_its_name_dies_quoting_its_own_error(self):
        line = f"orca worktree create made no worktree named {LOCAL_NAME} and answered runtime_timeout: Timed out waiting for the Orca runtime to respond, and git lists none under {MAIN}; launching this brief again creates it under that name"
        other = f"worktree {MAIN}\nHEAD {BASE}\nbranch refs/heads/main\n\nworktree /w/{LOCAL_NAME}-old\nHEAD {BASE}\n"
        held = f"{other}\nworktree {LOCAL_WORKTREE}\nHEAD {BASE}\nlocked initializing\n"
        for name, gits, lists in (("never cut", [other], []), ("rolled back", [held, other], [LIST])):
            with self.subTest(shape=name):
                launch = self.fresh()
                launch.local()
                launch.reply("orca worktree create", DROPPED)
                launch.reply("orca worktree list", UNLISTED, UNLISTED, UNLISTED)
                launch.reply("orca repo show", SHOWN)
                launch.reply("git", *({"out": out} for out in gits))
                waited = [call for listed in lists for call in (listed, GIT_LIST)]
                self.assert_failed(launch, launch.run(model="fable", WORKER_LOCAL="1"), line, [*launch.local_calls()[:3], LIST, REPO_SHOW, GIT_LIST, *waited])
                self.assertEqual(launch.kept(), ["request.json"])

    def test_a_name_an_earlier_attempt_only_planned_is_still_free(self):
        earlier = self.launch.briefs / "earlier.worker"
        earlier.mkdir()
        (earlier / "request.json").write_text(json.dumps({"lane": LANE, "route": "local", "worktree": LOCAL_NAME}))
        self.launch.local()
        result = self.launch.run(model="fable", WORKER_LOCAL="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.receipt("worktree"), f"{LOCAL_NAME}\n")

    def test_the_local_route_checks_the_run_owner_before_any_resource(self):
        self.launch.local()
        self.launch.reply("orca orchestration run-current", {"out": {"ok": True, "result": {"run": {"id": "run_2"}}}})
        self.assert_refused(self.launch, self.launch.run(model="fable", WORKER_LOCAL="1"), f"terminal term_coordinator coordinates run_2, not {RUN}, so nothing launched; launch from the coordinator that already owns Run {RUN}", [CURRENT])


    def test_a_checkouts_orca_json_names_the_cc_remote_pair_the_environment_leaves_unset(self):
        selected = fake(self.launch.checkout / "tools" / "cc-remote")
        (self.launch.checkout / ".orca.json").write_text(json.dumps({"ccRemote": "tools/cc-remote", "ccRemoteConfig": "cc-remote/config.yaml"}))
        self.launch.sprite()
        result = self.launch.run(cwd=self.launch.checkout, CC_REMOTE_CONFIG="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.cc_remote(), {(str(selected), str(self.launch.config))})
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *self.launch.tools(), self.launch.start()])

    def test_the_environment_outranks_a_checkouts_orca_json(self):
        fake(self.launch.checkout / "tools" / "cc-remote")
        (self.launch.checkout / ".orca.json").write_text(json.dumps({"ccRemote": "tools/cc-remote", "ccRemoteConfig": "missing.yaml"}))
        self.launch.sprite()
        result = self.launch.run(cwd=self.launch.checkout, WORKER_CC_REMOTE=str(self.launch.cli))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.cc_remote(), {(str(self.launch.cli), str(self.launch.config))})

    def test_a_checkouts_orca_json_names_the_local_repository_and_base_and_leads_the_cone(self):
        (self.launch.checkout / ".orca.json").write_text(json.dumps({"repo": REPO, "baseBranch": BASE_BRANCH, "sparse": ["src"]}))
        self.launch.local()
        result = self.launch.run(model="fable", cwd=self.launch.checkout, WORKER_LOCAL="1", WORKER_REPO="", WORKER_BASE_BRANCH="", WORKER_SPARSE="docs")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.calls(), self.launch.local_calls(cone=CONE))


def test_a_lane_keeps_the_terminal_title_orca_reads_for_its_mail():
    assert "CLAUDE_CODE_DISABLE_TERMINAL_TITLE" not in LANE_ENV


if __name__ == "__main__":
    unittest.main()
