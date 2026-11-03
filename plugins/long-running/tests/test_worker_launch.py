from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/worker-launch.sh"
CCI_URL = "https://example.test/cci_linux_amd64.tar.gz"
CCI_SHA = "c" * 64
RUN = "run_1"
LANE = "lane-a"
WORKSPACE = "pool-spare"
REPO = "71ae0557-90f0-462f-9c2d-32ed95fe5334"
TERMINAL = "term_remote_1"
ROOT = "/home/sprite/monorepo"
BASE = "1" * 40
BRIEF = "# Lane A\n\nWrite the patch and the report beside this brief.\n"
SHA = hashlib.sha256(BRIEF.encode()).hexdigest()
BYTES = len(BRIEF.encode())
LOCAL_WORKTREE = "/w/lane-a"
LOCAL_TERMINAL = "term_local_1"
TOOLS = ("orca", "cc-remote", "sprite", "cci")
SPONSOR = "sponsor-1"
RELAY_URL = "https://relay.example.test/"
OWNER_KEY = "BOwner=="
KEY_PATH = "/home/sprite/.cc-inbox/node/key.pem"
PIN = "9.8.7"

VERSION = ["cc-remote", "version"]
CURRENT = ["orca", "orchestration", "run-current", "--full", "--json"]
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
    "bootstrap": ["trust"],
    "prepared": True,
    "brief": {"path": f"/home/sprite/.cc-remote/orca/tasks/{WORKSPACE}/brief.md", "sha256": SHA, "bytes": BYTES},
    "baseCommit": BASE,
}
RECEIPT = {"ok": True, "result": {"runId": RUN, "taskId": "task_a", "dispatchId": "ctx_a", "state": "ready", "stage": "remote_input_accepted"}}
ALLOCATION_KEPT = ["allocation.json", "brief.md", "prepare.err", "prepare.status", "request.json"]
PREPARE_KEPT = sorted([*ALLOCATION_KEPT, "prepared.json"])
START_KEPT = ["worker-start.err", "worker-start.json", "worker-start.status"]
SPRITE_KEPT = sorted([*PREPARE_KEPT, "cci-install.log", *START_KEPT])
LOCAL_KEPT = sorted(["request.json", *START_KEPT])
UNREADY = "orca orchestration worker-start exited 0 without a ready receipt for Run {run}; kept terminal {terminal}, worktree {worktree}, and the receipt in {attempt}"

FAKE = """#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_STATE"]
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
with open(os.path.join(state, "calls"), "a") as calls:
    calls.write(json.dumps({"argv": [name, *args], "real": os.path.realpath(sys.argv[0]), "env": {"CC_REMOTE_CONFIG": os.environ.get("CC_REMOTE_CONFIG")}}) + "\\n")
words = [arg for arg in args[:2] if not arg.startswith("-")]
queue = os.path.join(state, "_".join([name, *words]))
replies = json.load(open(queue)) if os.path.exists(queue) else []
reply = replies.pop(0) if replies else {"rc": 1}
json.dump(replies, open(queue, "w"))
out = reply.get("out", "")
sys.stdout.write(out if isinstance(out, str) else json.dumps(out))
sys.stderr.write(reply.get("err", ""))
sys.exit(reply.get("rc", 0))
"""

CARRY_NOTHING = """#!/bin/sh
exec cat "$2"
"""

CARRY = """#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
into, box = args[args.index("--into") + 1], args[args.index("--box") + 1]
for rel, text in (("Users/me/.claude/scratch/plan.md", "the plan\\n"), ("ccn/doc-c3230a2.md", "register rows\\n")):
    os.makedirs(os.path.dirname(os.path.join(into, rel)), exist_ok=True)
    open(os.path.join(into, rel), "w").write(text)
sys.stdout.write(f"Read {box}/Users/me/.claude/scratch/plan.md, then {box}/ccn/doc-c3230a2.md.\\n")
"""


def fake(path: Path, script: str = FAKE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script)
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
        fake(self.bin / "lr", CARRY_NOTHING)
        self.root = root
        self.checkout = root / "checkout"
        self.script = SCRIPT
        self.cli = fake(self.checkout / "tools" / "cc-remote" / "bin" / "cc-remote")
        self.config = self.cli.parent.parent / "config.yaml"
        self.config.write_text("provider: sprites\n")
        self.payload = self.cli.parent.parent / "agents-payload.json"
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
            "WORKER_CHECKOUT": str(self.checkout),
            "WORKER_CCI_URL": CCI_URL,
            "WORKER_CCI_SHA256": CCI_SHA,
            "ORCA_TERMINAL_HANDLE": "term_coordinator",
        }

    def reply(self, command: str, *replies: dict) -> None:
        (self.fake / command.replace(" ", "_")).write_text(json.dumps(list(replies)))

    def sprite(self, prepared: dict | str = PREPARED, version: dict | None = None, started: dict | None = None, allocated: dict | str | None = None) -> None:
        self.reply("cc-remote version", version or {"err": f"{PIN}\n"})
        self.reply("orca orchestration run-current", BOUND)
        response = allocated if allocated is not None else prepared if isinstance(prepared, str) else allocation(prepared)
        self.reply("cc-remote orca prepare", {"out": response if isinstance(response, str) else json.dumps(response), "err": "prepared on sprites\n"})
        self.reply("orca orchestration worker-start", started or {"out": RECEIPT})
        self.reply("sprite exec", {"rc": 0}, {"rc": 0}, {"rc": 0})

    def relay(self) -> None:
        self.reply("cci relay status", {"out": [{"mesh": "other", "url": "https://elsewhere.test/", "routes": ["x"]}, {"mesh": "default", "url": RELAY_URL, "routes": ["drive-a", "drive-b"]}]})
        self.reply("cci node", {"out": {"id": "n1", "public_key": OWNER_KEY}})
        self.reply("sprite exec", {"rc": 0}, {"rc": 0}, {"rc": 0})

    def local(self, wait: dict | None = None, started: dict | None = None) -> None:
        self.reply("orca orchestration run-current", BOUND)
        self.reply("orca worktree list", {"out": {"ok": True, "result": {"worktrees": []}}})
        self.reply("orca worktree create", {"out": {"ok": True, "result": {"worktree": {"path": LOCAL_WORKTREE}}}})
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
        return subprocess.run([str(self.script), lane, model, effort, str(self.brief)], env={**self.env, **env}, cwd=cwd, capture_output=True, text=True, timeout=60)

    def cc_remote(self) -> set[tuple[str, str]]:
        return {(record["real"], record["env"]["CC_REMOTE_CONFIG"]) for record in self.records() if record["argv"][0] == "cc-remote"}

    def prepare(self, *extra: str, model: str = "claude-opus-5-5", kind: str = "claude", effort: str = "xhigh", lane: str = LANE) -> list[str]:
        return ["cc-remote", "orca", "prepare", lane, "--warm", "--provider", "sprites", "--profile", "agents", "--agent", kind, "--model", model, "--effort", effort, "--brief-file", str(self.attempt / "brief.md"), *extra]

    def install(self, workspace: str = WORKSPACE) -> list[str]:
        script = 'curl -fsSL "$1" -o /tmp/cci.tgz && echo "$2  /tmp/cci.tgz" | sha256sum -c - && mkdir -p ~/.local/bin && tar -xzf /tmp/cci.tgz -C ~/.local/bin cci'
        return ["sprite", "exec", "-s", workspace, "--no-stdin", "--", "sh", "-c", script, "sh", CCI_URL, CCI_SHA]

    def join(self) -> list[list[str]]:
        script = 'CCI_SERVICE_KEY=$(cat); export CCI_SERVICE_KEY; ~/.local/bin/cci relay join --instance --drive "$1" --url "$2" --ttl 24h --owner "$3" && sprite-env services create cci --cmd "$HOME/.local/bin/cci" --args supervise --no-stream'
        return [
            ["cci", "relay", "status", "--json"],
            ["cci", "node", "--json"],
            ["sprite", "exec", "-s", SPONSOR, "--no-stdin", "--", "cat", KEY_PATH],
            ["sprite", "exec", "-s", WORKSPACE, "--", "sh", "-c", script, "sh", "drive-a,drive-b", RELAY_URL, OWNER_KEY],
        ]

    def start(self, prepared: dict = PREPARED) -> list[list[str]]:
        spec = f"Lane {LANE}: read the whole brief at {prepared['brief']['path']}, verify its SHA-256 is {SHA} and its size is {BYTES} bytes, then follow it exactly."
        placement = ["--on", prepared["environment"], "--worktree", f"id:{prepared['worktreeId']}", "--terminal", prepared["terminal"]]
        return [self.install(prepared["environment"]), ["orca", "orchestration", "worker-start", "--run", RUN, "--spec", spec, "--task-title", LANE, *placement, "--timeout-ms", "600000", "--full", "--json"]]

    def local_calls(self, model: str = "claude-fable-5-1", effort: str = "xhigh", repo: str = REPO) -> list[list[str]]:
        claude = f"claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --disallowedTools AskUserQuestion,EnterPlanMode,ExitPlanMode --strict-mcp-config --model {model} --effort {effort}"
        pointer = f"Lane '{LANE}'. Your full brief is the file {self.brief}: read it in full with the Read tool before anything else and execute it exactly (Orca truncates pasted specs). Your Orca worktree is {LOCAL_WORKTREE}. You run in bypass-permissions mode; the brief's Escalate rules still hold."
        return [
            CURRENT,
            ["orca", "worktree", "list", "--repo", f"id:{repo}", "--limit", "10000", "--json"],
            ["orca", "worktree", "create", "--name", LANE, "--repo", f"id:{repo}", "--base-branch", "origin/dev", "--setup", "run", "--json"],
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
                self.assert_failed(launch, launch.run(lane=lane), f"cc-remote orca prepare exited 1; {launch.attempt} keeps its output, and nothing was attached or retried", [VERSION, CURRENT, launch.prepare(lane=lane)])

    def test_a_local_repository_refuses_the_sprite_route(self):
        self.launch.sprite()
        self.assert_refused(self.launch, self.launch.run(WORKER_REPO="repo-1"), "WORKER_REPO picks a local Orca repository; a Sprite checks out the repository its cc-remote config names, chosen with CC_REMOTE_CONFIG; nothing was prepared", [])

    def test_fable_refuses_the_sprite_route_before_any_call(self):
        for model, model_id in (("fable", "claude-fable-5-1"), ("claude-fable-5-1", "claude-fable-5-1"), ("claude-Fable-6", "claude-Fable-6")):
            with self.subTest(model=model):
                launch = self.fresh()
                launch.sprite()
                self.assert_refused(launch, launch.run(model=model), f"{model_id} is Fable, which runs only on the explicit local route, WORKER_LOCAL=1; nothing was prepared, and nothing launched locally instead", [])

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
        self.assert_refused(self.launch, self.launch.run(WORKER_CC_REMOTE=str(missing)), f"WORKER_CC_REMOTE names no cc-remote command at {missing}; unset it to run this checkout's pinned one; nothing was prepared", [])

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
                self.assert_refused(launch, launch.run(), f"{launch.cli} version printed {printed}, and {launch.config} pins bootstrap helper {PIN}; a Sprite needs the CLI at exactly its helper's release; nothing was prepared", [VERSION])

    def test_a_cc_remote_that_cannot_print_its_version_refuses_with_its_own_message(self):
        untrusted = "mise ERROR Config files in ~/checkout are not trusted."
        self.launch.sprite(version={"rc": 1, "err": f"{untrusted}\n"})
        self.assert_refused(self.launch, self.launch.run(), f"{self.launch.cli} version failed: {untrusted}; nothing was prepared", [VERSION])

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
                self.assertEqual(launch.calls(), [VERSION, CURRENT, launch.prepare(), *launch.start()])

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
    def test_an_attempt_that_never_started_a_worker_leaves_the_brief_free_and_keeps_its_receipts(self):
        for shape in ("empty", "failed", "link"):
            with self.subTest(shape=shape):
                launch = self.fresh()
                launch.sprite()
                if shape == "link":
                    launch.attempt.symlink_to(launch.briefs / "gone")
                else:
                    launch.attempt.mkdir()
                if shape == "failed":
                    (launch.attempt / "prepare.status").write_text("1\n")
                result = launch.run()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(launch.calls(), [VERSION, CURRENT, launch.prepare(), *launch.start()])
                self.assertEqual(launch.kept(), SPRITE_KEPT)
                [moved] = launch.briefs.glob(f"{LANE}.worker.*")
                if shape == "failed":
                    self.assertEqual((moved / "prepare.status").read_text(), "1\n")

    def test_an_attempt_that_reached_worker_start_refuses_replay_before_prepare(self):
        self.launch.sprite()
        self.launch.attempt.mkdir()
        (self.launch.attempt / "worker-start.json").write_text("{}")
        self.assert_failed(self.launch, self.launch.run(), f"{self.launch.attempt} holds an earlier attempt of this brief, and a brief gets one attempt; inspect it instead", [VERSION, CURRENT])
        self.assertEqual(self.launch.kept(), ["worker-start.json"])

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

    def test_a_launch_refused_before_any_worker_started_relaunches_on_the_same_brief(self):
        """Nov 2: coordinator-desks-1's prepare timed out under R84964, and R84978 was refused because a brief gets one attempt, though no worker had started."""
        self.launch.sprite()
        self.launch.reply("cc-remote orca prepare", {"rc": 1, "out": ""})
        first = self.launch.run()
        self.launch.sprite()
        second = self.launch.run()
        self.assertEqual(first.returncode, 1)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), VERSION, CURRENT, self.launch.prepare(), *self.launch.start()])
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)

    def test_an_earlier_local_attempt_refuses_instead_of_retrying(self):
        self.launch.local()
        self.launch.attempt.mkdir()
        (self.launch.attempt / "worker-start.json").write_text(json.dumps({"ok": True, "result": {"taskId": "task_l", "dispatchId": "ctx_l"}}))
        self.assert_failed(self.launch, self.launch.run(model="fable", WORKER_LOCAL="1"), f"{self.launch.attempt} holds an earlier attempt of this brief, and a brief gets one attempt; inspect it instead", [CURRENT])
        self.assertEqual(self.launch.kept(), ["worker-start.json"])


UNPACKING_SPRITE = """#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.path.join(os.environ["FAKE_STATE"], "calls"), "a") as calls:
    calls.write(json.dumps({"argv": ["sprite", *args], "real": os.path.realpath(sys.argv[0]), "env": {"CC_REMOTE_CONFIG": None}}) + "\\n")
if "--no-stdin" not in args:
    script, root = args[args.index("-c") + 1], args[-1].replace("/home/sprite", os.environ["FAKE_BOX"])
    sys.exit(subprocess.run(["sh", "-c", script, "sh", root], stdin=sys.stdin).returncode)
"""


class CarryTest(LaunchTest):
    SENT = "Read /home/sprite/drive/carried/Users/me/.claude/scratch/plan.md, then /home/sprite/drive/carried/ccn/doc-c3230a2.md.\n"

    def carrying(self, sprite: str = UNPACKING_SPRITE) -> subprocess.CompletedProcess[str]:
        fake(self.launch.bin / "lr", CARRY)
        self.launch.sprite({**PREPARED, "brief": {**PREPARED["brief"], "sha256": hashlib.sha256(self.SENT.encode()).hexdigest(), "bytes": len(self.SENT.encode())}})
        (self.launch.bin / "sprite").write_text(sprite)
        return self.launch.run(FAKE_BOX=str(self.launch.root / "box"))

    def test_a_mac_file_and_a_ccn_doc_the_brief_names_are_on_the_box_at_the_paths_the_worker_reads_before_it_starts(self):
        result = self.carrying()
        self.assertEqual(result.returncode, 0, result.stderr)
        for named, text in (("Users/me/.claude/scratch/plan.md", "the plan\n"), ("ccn/doc-c3230a2.md", "register rows\n")):
            self.assertIn(f"/home/sprite/drive/carried/{named}", self.SENT)
            self.assertEqual((self.launch.root / "box" / "drive" / "carried" / named).read_text(), text)
        self.assertEqual((self.launch.attempt / "brief.md").read_text(), self.SENT)
        calls = self.launch.calls()
        copied = next(index for index, call in enumerate(calls) if call[0] == "sprite" and "--no-stdin" not in call)
        started = next(index for index, call in enumerate(calls) if call[:3] == ["orca", "orchestration", "worker-start"])
        self.assertLess(calls.index(self.launch.prepare()), copied)
        self.assertLess(copied, started)
        self.assertIn(hashlib.sha256(self.SENT.encode()).hexdigest(), calls[started][calls[started].index("--spec") + 1])

    def test_a_brief_naming_what_no_sprite_can_receive_is_refused_naming_it_and_prepares_nothing(self):
        fake(self.launch.bin / "lr", f"#!/bin/sh\necho 'lr: brief {self.launch.brief} names what no Sprite can receive: /Users/me/gone.md does not exist on this Mac' >&2\nexit 1\n")
        self.launch.sprite()
        result = self.launch.run()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"brief {self.launch.brief} names what no Sprite can receive: /Users/me/gone.md does not exist on this Mac; nothing was prepared", result.stderr)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT])

    def test_a_failed_copy_leaves_the_worker_idle(self):
        result = self.carrying("#!/bin/sh\ncat >/dev/null\nexit 1\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("copying the files the brief names", result.stderr)
        self.assertFalse((self.launch.attempt / "worker-start.json").exists())


class SpriteTest(LaunchTest):
    def test_the_default_prepares_one_sprite_and_starts_it_once(self):
        self.launch.sprite()
        result = self.launch.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(json.loads(result.stdout), {"state": "ready", "task": "task_a", "dispatch": "ctx_a", "terminal": TERMINAL, "worktree": ROOT, "lane": LANE, "workspace": WORKSPACE, "allocation": "warm", "relay": None})
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *self.launch.start()])
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)

    def test_a_standing_desk_brief_carries_the_desk_contract_into_the_sprite_spec(self):
        text = "# Lane A\nccx: role=desk place=remote\nRun the loop until rotation.\n"
        self.launch.brief.write_text(text)
        self.launch.sprite({**PREPARED, "brief": {**PREPARED["brief"], "sha256": hashlib.sha256(text.encode()).hexdigest(), "bytes": len(text.encode())}})
        result = self.launch.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        start = next(call for call in self.launch.calls() if call[:3] == ["orca", "orchestration", "worker-start"])
        spec = start[start.index("--spec") + 1]
        self.assertTrue(spec.endswith("then follow it exactly. Standing desk: loop until rotation; setup and quiet cycles are not done. Send worker_done only at rotation, naming the handoff doc."), spec)

    def test_a_relay_sponsor_joins_the_sprite_to_the_macs_default_mesh_before_the_worker_starts(self):
        self.launch.sprite()
        self.launch.relay()
        result = self.launch.run(WORKER_RELAY_SPONSOR=SPONSOR)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(json.loads(result.stdout)["relay"], "joined")
        install, start = self.launch.start()
        status, node, *pipeline = self.launch.join()
        calls = self.launch.calls()
        self.assertEqual(calls[:6], [VERSION, CURRENT, self.launch.prepare(), install, status, node])
        self.assertCountEqual(calls[6:8], pipeline)
        self.assertEqual(calls[8:], [start])
        self.assertEqual(self.launch.kept(), sorted([*SPRITE_KEPT, "relay-join.log"]))

    def test_a_failed_relay_join_warns_and_still_starts_the_worker(self):
        self.launch.sprite()
        self.launch.relay()
        self.launch.reply("cci node", {"rc": 1})
        result = self.launch.run(WORKER_RELAY_SPONSOR=SPONSOR)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, f"Sprite {WORKSPACE} did not join the cci relay with {SPONSOR}'s service key, so its records stay on the Sprite; {self.launch.attempt}/relay-join.log keeps the output, and the worker starts anyway\n")
        self.assertEqual(json.loads(result.stdout)["relay"], "failed")
        install, start = self.launch.start()
        status, node, *_ = self.launch.join()
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), install, status, node, start])

    def test_a_failed_cci_install_leaves_the_prepared_worker_unattached(self):
        self.launch.sprite()
        self.launch.reply("sprite exec", {"rc": 1})
        install, _ = self.launch.start()
        line = f"installing cci from {CCI_URL} on Sprite {WORKSPACE} failed; {self.launch.attempt}/cci-install.log keeps its output, and the prepared worker stays idle and unattached"
        self.assert_failed(self.launch, self.launch.run(), line, [VERSION, CURRENT, self.launch.prepare(), install])
        self.assertEqual(self.launch.kept(), sorted([*PREPARE_KEPT, "cci-install.log"]))

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
                "agent": {**AGENT, "serviceTier": None},
                "ref": None,
                "expectBase": None,
                "brief": {"path": str(self.launch.attempt / "brief.md"), "sha256": SHA, "bytes": BYTES},
            },
        )

    def test_receipts_stay_in_the_attempt(self):
        self.launch.sprite()
        self.assertEqual(self.launch.run().returncode, 0)
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)
        self.assertEqual(list(self.launch.home.iterdir()), [])

    def test_an_unset_launch_runs_the_checkouts_own_cli_and_config(self):
        self.launch.sprite()
        self.assertEqual(self.launch.run().returncode, 0)
        self.assertEqual(self.launch.cc_remote(), {(str(self.launch.cli), str(self.launch.config))})
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *self.launch.start()])
        self.assertFalse(any("--config" in argv for argv in self.launch.calls()))

    def test_the_checkout_is_the_one_worker_checkout_names_and_not_the_callers(self):
        elsewhere = self.launch.root / "elsewhere"
        fake(elsewhere / "tools" / "cc-remote" / "bin" / "cc-remote")
        self.launch.sprite()
        result = self.launch.run(cwd=elsewhere)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.cc_remote(), {(str(self.launch.cli), str(self.launch.config))})

    def test_an_explicit_cli_runs_against_the_checkouts_config_at_the_helper_pin(self):
        selected = fake(self.launch.root / "selected" / "cc-remote")
        self.launch.sprite()
        result = self.launch.run(WORKER_CC_REMOTE=str(selected))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.cc_remote(), {(str(selected), str(self.launch.config))})
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *self.launch.start()])

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
        relative = "tools/cc-remote/config.yaml"
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
                self.assertEqual(launch.calls(), [*([VERSION] if own else []), CURRENT, launch.prepare(), *launch.start()])

    def test_an_explicit_config_of_another_checkout_leaves_the_version_to_cc_remote(self):
        other = self.launch.root / "other" / "config.yaml"
        other.parent.mkdir()
        other.write_text("provider: sprites\n")
        self.launch.sprite(version={"err": "9.8.9\n"})
        result = self.launch.run(CC_REMOTE_CONFIG=str(other))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.cc_remote(), {(str(self.launch.cli), str(other))})
        self.assertEqual(self.launch.calls(), [CURRENT, self.launch.prepare(), *self.launch.start()])

    def test_aliases_and_full_claude_ids_reach_prepare_unchanged(self):
        for model, model_id in (("opus", "claude-opus-5-5"), ("sonnet", "claude-sonnet-5-5"), ("claude-opus-5-5", "claude-opus-5-5"), ("claude-haiku-4-5-20251001", "claude-haiku-4-5-20251001")):
            with self.subTest(model=model):
                launch = self.fresh()
                launch.sprite(prepared={**PREPARED, "agent": {**AGENT, "model": model_id, "effort": "max"}})
                result = launch.run(model=model, effort="max")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(launch.calls(), [VERSION, CURRENT, launch.prepare(model=model_id, effort="max"), *launch.start()])

    def test_a_codex_model_keeps_its_effort_tier_ref_and_mcp(self):
        agent = {"kind": "codex", "model": "gpt-6.1-sol", "effort": "xhigh", "serviceTier": "fast", "mcp": ["{}"]}
        self.launch.sprite(prepared={**PREPARED, "agent": agent}, allocated=allocation({**PREPARED, "agent": agent}, ref="release/2026.10"))
        result = self.launch.run(model="gpt-6.1-sol", WORKER_SERVICE_TIER="fast", WORKER_INCIDENT="1", WORKER_REF="release/2026.10", WORKER_MCP_CONFIG="{}")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = self.launch.prepare("--ref", "release/2026.10", "--service-tier", "fast", "--mcp-config", "{}", model="gpt-6.1-sol", kind="codex")
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, expected, *self.launch.start()])
        request = json.loads(self.launch.receipt("request.json"))
        self.assertEqual((request["agent"], request["ref"]), ({"kind": "codex", "model": "gpt-6.1-sol", "effort": "xhigh", "serviceTier": "fast"}, "release/2026.10"))

    def test_a_hex_spelled_branch_passes_unchanged_as_a_ref(self):
        self.launch.sprite(allocated=allocation(ref="deadbeef"))
        result = self.launch.run(WORKER_REF="deadbeef")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare("--ref", "deadbeef"), *self.launch.start()])

    def test_a_moved_branch_attaches_on_its_actual_base(self):
        moved = "2" * 40
        self.launch.sprite(prepared={**PREPARED, "baseCommit": moved})
        result = self.launch.run(WORKER_REF="dev")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare("--ref", "dev"), *self.launch.start()])
        self.assertEqual(json.loads(self.launch.receipt("prepared.json"))["baseCommit"], moved)

    def test_an_expected_base_that_matches_attaches(self):
        self.launch.sprite()
        result = self.launch.run(WORKER_EXPECT_BASE=BASE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *self.launch.start()])
        self.assertEqual(json.loads(self.launch.receipt("request.json"))["expectBase"], BASE)

    def test_an_expected_base_mismatch_leaves_the_prepared_worker_idle(self):
        expected = "3" * 64
        self.launch.sprite()
        result = self.launch.run(WORKER_EXPECT_BASE=expected)
        self.assert_failed(self.launch, result, f"the Sprite checked out {BASE}, not WORKER_EXPECT_BASE {expected}, so the prepared worker stays idle and unattached, and nothing is prepared again", [VERSION, CURRENT, self.launch.prepare()])
        self.assertEqual(self.launch.kept(), PREPARE_KEPT)

    def test_a_failed_prepare_keeps_its_raw_output_and_attaches_nothing(self):
        partial = json.dumps(allocation({**PREPARED, "prepared": False}))
        self.launch.sprite()
        self.launch.reply("cc-remote orca prepare", {"rc": 1, "out": partial, "err": "cc-remote: provider timed out\n"})
        result = self.launch.run()
        self.assert_failed(self.launch, result, f"cc-remote orca prepare exited 1; {self.launch.attempt} keeps its output, and nothing was attached or retried", [VERSION, CURRENT, self.launch.prepare()])
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
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *self.launch.start(prepared)])

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
                self.assert_failed(launch, result, f"{launch.attempt}/allocation.json holds {reason}, so the prepared worker stays idle and unattached", [VERSION, CURRENT, launch.prepare("--ref", "dev")])
                self.assertEqual(launch.kept(), ALLOCATION_KEPT)

    def test_replenishment_failure_does_not_replay_an_attached_worker(self):
        refill = {"target": 1, "failure": {"exit": None, "httpStatus": None, "bytes": 4, "sha256": "0" * 64}}
        self.launch.sprite(allocated=allocation(replenish=refill))
        result = self.launch.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.launch.receipt("allocation.json"))["replenish"], refill)
        self.assertEqual(self.launch.calls(), [VERSION, CURRENT, self.launch.prepare(), *self.launch.start()])

    def test_a_failed_sprite_start_keeps_its_prepared_worker_and_receipts(self):
        refused = {"rc": 1, "out": {"ok": False, "error": {"code": "consumer_fenced", "message": "fenced"}, "result": {"taskId": "task_a", "dispatchId": "ctx_a"}}}
        self.launch.sprite(started=refused)
        result = self.launch.run()
        self.assert_failed(self.launch, result, f"orca orchestration worker-start exited 1: consumer_fenced: fenced; kept terminal {TERMINAL}, worktree {ROOT}, and the receipt in {self.launch.attempt}", [VERSION, CURRENT, self.launch.prepare(), *self.launch.start()])
        self.assertEqual(self.launch.receipt("worker-start.status"), "1\n")
        self.assertEqual(self.launch.kept(), SPRITE_KEPT)

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
                self.assert_failed(launch, launch.run(), line, [VERSION, CURRENT, launch.prepare(), *launch.start()])
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
        self.assert_failed(self.launch, result, f"orca orchestration worker-start exited 1: consumer_fenced: fenced; kept terminal {LOCAL_TERMINAL}, worktree {LOCAL_WORKTREE}, and the receipt in {self.launch.attempt}", self.launch.local_calls())
        self.assertEqual(self.launch.kept(), LOCAL_KEPT)
        self.assertEqual(self.launch.receipt("worker-start.json"), json.dumps(refused["out"]))
        self.assertEqual(self.launch.receipt("worker-start.status"), "1\n")

    def test_a_local_start_without_a_ready_receipt_prints_no_success(self):
        self.launch.local(started={"out": {"ok": True, "result": {"state": "ready", "taskId": "task_l", "dispatchId": "ctx_l"}}})
        result = self.launch.run(model="fable", WORKER_LOCAL="1")
        self.assert_failed(self.launch, result, UNREADY.format(run=RUN, terminal=LOCAL_TERMINAL, worktree=LOCAL_WORKTREE, attempt=self.launch.attempt), self.launch.local_calls())
        self.assertEqual(self.launch.kept(), LOCAL_KEPT)

    def test_a_local_idle_timeout_keeps_its_terminal_and_worktree(self):
        self.launch.local(wait={"rc": 1, "out": "timed out"})
        result = self.launch.run(model="fable", WORKER_LOCAL="1")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertEqual(result.stderr, f"orca terminal wait failed: timed out\nclaude in {LOCAL_TERMINAL} never went idle; kept it and worktree {LOCAL_WORKTREE}\n")
        self.assertEqual(self.launch.calls(), self.launch.local_calls()[:-1])
        self.assertEqual(self.launch.kept(), ["request.json"])

    def test_the_local_route_starts_only_claude(self):
        self.launch.local()
        self.assert_refused(self.launch, self.launch.run(model="gpt-6.1-sol", WORKER_LOCAL="1"), "the local route starts only claude, and codex runs gpt-6.1-sol; nothing launched", [])

    def test_the_local_route_refuses_sprite_checkout_settings(self):
        for env in ({"WORKER_REF": "dev"}, {"WORKER_EXPECT_BASE": BASE}):
            with self.subTest(env=env):
                launch = self.fresh()
                launch.local()
                self.assert_refused(launch, launch.run(model="fable", WORKER_LOCAL="1", **env), "WORKER_REF and WORKER_EXPECT_BASE pick a Sprite's checkout; the local route takes WORKER_BASE_BRANCH; nothing launched", [])

    def test_the_local_route_checks_the_run_owner_before_any_resource(self):
        self.launch.local()
        self.launch.reply("orca orchestration run-current", {"out": {"ok": True, "result": {"run": {"id": "run_2"}}}})
        self.assert_refused(self.launch, self.launch.run(model="fable", WORKER_LOCAL="1"), f"terminal term_coordinator coordinates run_2, not {RUN}, so nothing launched; launch from the coordinator that already owns Run {RUN}", [CURRENT])


if __name__ == "__main__":
    unittest.main()
