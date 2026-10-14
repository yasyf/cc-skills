"""desk-runner.py against a fake Orca Run, ledger, stack-enqueue, and judge, on a fake clock: every side effect is recorded on ``shell.calls``."""

from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import actions
import holds
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/desk-runner.py"
spec = importlib.util.spec_from_file_location("desk_runner", SCRIPT)
runner_module = importlib.util.module_from_spec(spec)
sys.modules["desk_runner"] = runner_module
spec.loader.exec_module(runner_module)

LANE = "incident-fix"
FORBIDDEN = {"worker-release", "kill", "pkill", "close", "stop", "terminate", "worker-stop"}
IDLE_PROMPT = ["⏺ Done.", "─" * 80, "❯", "─" * 80, "  ⏵⏵ bypass permissions on (shift+tab to cycle)"]
PICKER = ["─" * 80, " ☐ Rebase", "❯ 1. Rebase now", "  2. Wait", "─" * 80, "Enter to select · ↑/↓ to navigate · Esc to cancel"]
RULING = "Requirement: " + "the release holds until the canary reads back green. " * 12 + "Then land the fix."
PARAGRAPHS = "Ruling, in three parts:\n\n- hold the release; relay the canary result to walker\n- " + "rebuild each failed head once, " * 15 + "then stop\n\nReport on this thread."
FAST_SQUASH = "5e11a9d0c2b47f6e8a1d3c5b7f9e0a2c4d6b8e1f"
FAST_BODY = "Resume uploads a pre-session-rows thread instead of refusing.\n\nRelease-With: sand\n"
PUBLISH_LANE = "phase-b-replace-publish-command"
SIZING_LANE = "phase-b-executor-sidecar-sizing"
PUBLISH_HOLD = f"{PUBLISH_LANE} HOLD (L34 re-land, revert of #36397): no enqueue until root posts 'L32 pass done' (F189)"
SIZING_HOLD = f"{SIZING_LANE} HOLD (L35 drop sessions-backfill/upload): no enqueue until root posts 'L32 pass done'"
UNREAD_HOLD = f"{PUBLISH_LANE}: no enqueue until root posts 'L32 pass done' (F189)"


class Process:
    def poll(self):
        return 0


class Running:
    code = None

    def poll(self):
        return self.code


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
        self.inboxes: dict[str, list[dict]] = {}
        self.questions: list[dict] = []
        self.lose_sends = 0
        self.lose_replies = 0
        self.reject_sends = 0
        self.mailbox_down = False
        self.garble_sends = 0
        self.requests: dict[str, str] = {}
        self.stale_out = ""
        self.prompt_watch: dict = {}
        self.screens: dict[str, list[str]] = {}
        self.gates: dict[str, list[str]] = {}
        self.enqueue_out: dict[str, tuple[int, str]] = {}
        self.statuses = ""
        self.head_statuses: list[dict] = []
        self.status_error = ""
        self.queue: dict[str, str] = {}
        self.activity: dict[str, str] = {}
        self.bodies: dict[str, str] = {}
        self.rows: list[dict] = []
        self.ledger_code = 0
        self.verdict = {"verdict": "answer", "text": "the brief says yes"}
        self.launch_line = ""
        self.cpu_load = 1.0
        self.attachments: dict[str, Path] = {}
        self.entries: list[str] = []
        self.attach_error = ""
        self.attach_code = 1
        self.unresolved: set[str] = set()
        self.sequence = 0
        self.environ = {"ORCA_TERMINAL_HANDLE": "term_root", "ORCA_PANE_KEY": "pane_root"}
        self.coordinator = "term_root"
        self.generation = 3
        self.run_use_error: dict | None = None
        self.posts: list[dict] = []
        self.records: list[dict] = []
        self.tail_error = ""
        self.tail_failures = 0
        self.post_error = ""
        self.post_out = ""
        self.post_ids: dict[str, int] = {}
        self.judge_process: Running | None = None
        self.sprite_line = ""
        self.sprite_receipts: dict[str, str] = {}
        self.spawned_env: dict[str, dict] = {}

    def env(self, name):
        return self.environ.get(name, "")

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += timedelta(seconds=seconds)

    def load(self):
        return self.cpu_load

    def cores(self):
        return 8

    def spawn(self, argv, out, env, stdin=None, err=None):
        self.calls.append(list(argv))
        self.spawned_env[Path(argv[0]).name] = env
        out.parent.mkdir(parents=True, exist_ok=True)
        if Path(argv[0]).name == "worker-launch.sh":
            out.write_text(self.sprite_line)
            attempt = Path(argv[4]).with_suffix(".worker")
            for name, text in self.sprite_receipts.items():
                attempt.mkdir(exist_ok=True)
                (attempt / name).write_text(text)
            return Process()
        if argv[0] == "claude" and self.judge_process:
            out.write_text("")
            return self.judge_process
        if Path(argv[0]).name == "orca-launch.sh" and (ready := runner_module.LAUNCHED.search(self.launch_line)):
            state = Path(env.get("ORCA_LAUNCH_STATE") or self.root / ".claude/scratch/orca-launch" / env.get("ORCA_LAUNCH_RUN", "run_1"))
            state.mkdir(parents=True, exist_ok=True)
            (state / f"{argv[1]}.json").write_text(json.dumps({"result": {"taskId": "task_1", "dispatchId": ready["dispatch"]}}))
            (state / f"{argv[1]}.terminal").write_text(f"{ready['terminal']}\n")
            self.dispatches[ready["dispatch"]] = {"status": "dispatched", "terminal": ready["terminal"]}
        out.write_text(json.dumps([{"type": "system"}, {"type": "result", "structured_output": self.verdict}]) if argv[0] == "claude" else self.launch_line)
        return Process()

    def stat(self, path):
        return path.read_text() if path.is_file() else None

    def next_id(self, prefix: str) -> str:
        self.sequence += 1
        return f"{prefix}_{self.sequence:04d}"

    def run(self, argv, stdin=None, env=None):
        self.calls.append(list(argv))
        name = Path(argv[0]).name
        if argv[0] == "orca":
            return self.orca(argv[1:-1])
        if argv[:5] == ["ccn", "-R", str(self.root / "checkout"), "attachment", "path"] and argv[5] == "briefs1":
            path = None if argv[6] in self.unresolved else self.attachments.get(argv[6])
            return runner_module.Done(0, f"{path}\n", "") if path else runner_module.Done(1, "", f"no attachment {argv[6]}")
        if argv[:6] == ["ccn", "-R", str(self.root / "checkout"), "log", "append", "briefs1"]:
            return self.append(argv[6:])
        if name == "orca-check.sh":
            assert argv[1:] == ["--stale"]
            return runner_module.Done(0, self.stale_out, "")
        if argv[:2] == [sys.executable, str(runner_module.SCRIPTS / "prompt_watch.py")]:
            assert argv[2:] == ["show", "--cci-drive", "d1", "--json"]
            return runner_module.Done(0, json.dumps(self.prompt_watch), "")
        if name == "stack-enqueue":
            return self.stack_enqueue(argv[1:])
        if argv[:7] == ["ccx", "vcs", "pr", "status", "-R", "Forge-AI/monorepo", "--json"]:
            return runner_module.Done(0, json.dumps([{"number": int(pr), "queue": self.queue.get(pr, "queued")} for pr in argv[7:]]), "")
        if argv[:2] == ["gh", "api"]:
            assert argv[2].startswith("repos/Forge-AI/monorepo/statuses/") and argv[3:] == ["--method", "POST", "--input", "-"]
            if self.status_error:
                return runner_module.Done(1, "", self.status_error)
            self.head_statuses.append({"sha": argv[2].rsplit("/", 1)[1], **json.loads(stdin)})
            return runner_module.Done(0, "{}\n", "")
        if argv[:3] == ["gh", "pr", "view"] and argv[-1] == "body":
            assert argv[4:] == ["--repo", "Forge-AI/monorepo", "--json", "body"]
            return runner_module.Done(0, json.dumps({"body": self.bodies[argv[3]]}), "")
        if argv[:3] == ["gh", "pr", "view"]:
            assert argv[4:] == ["--repo", "Forge-AI/monorepo", "--json", "comments"]
            return runner_module.Done(0, json.dumps({"comments": [{"body": self.activity[argv[3]]}] if argv[3] in self.activity else []}), "")
        if len(argv) > 1 and Path(argv[1]).name == "ledger.py":
            verb = argv[4]
            assert env == {"CLAUDE_LONG_RUNNING_DRIVE": "d1"}
            if verb != "list":
                return runner_module.Done(0, f"{verb} ok\n", "")
            return runner_module.Done(self.ledger_code, json.dumps([{"ours": row.get("state", "open") == "open", **row} for row in self.rows]), "")
        if argv[:2] == ["cci", "tail"]:
            return self.cci_tail(argv)
        if argv[:2] == ["cci", "post"]:
            flags = dict(zip(argv[2::2], argv[3::2], strict=True))
            assert flags["--drive"] == "d1" and flags["--lane"] == "desk-runner" and len(flags["--text"]) <= 400
            if self.post_error:
                return runner_module.Done(1, "", self.post_error)
            if stored := self.post_ids.get(flags.get("--id", "")):
                return runner_module.Done(0, f"#{stored} (duplicate)\n", "")
            self.posts.append({key.lstrip("-"): value for key, value in flags.items()})
            if "--id" in flags:
                self.post_ids[flags["--id"]] = len(self.posts)
            return runner_module.Done(0, self.post_out or f"#{len(self.posts)}\n", "")
        raise AssertionError(f"unexpected call {argv}")

    def cci_tail(self, argv: list[str]) -> runner_module.Done:
        if self.tail_failures:
            self.tail_failures -= 1
            return runner_module.Done(1, "", "cci: daemon restarting")
        if self.tail_error:
            return runner_module.Done(1, "", self.tail_error)
        assert argv[argv.index("--drive") + 1] == "d1" and "--json" in argv and "--cursor" not in argv
        since = int(argv[argv.index("--since") + 1])
        kinds = [arg.partition("=")[2] for arg in argv if arg.startswith("--kind=")]
        lanes = [argv[index + 1] for index, arg in enumerate(argv) if arg == "--lane"]
        found = [
            record
            for record in self.records
            if record["seq"] > since
            and ("--to" not in argv or argv[argv.index("--to") + 1] in record["to"])
            and (not lanes or record.get("lane") in lanes)
            and ("--pr" not in argv or int(argv[argv.index("--pr") + 1]) in record.get("refs", {}).get("prs", []))
            and (not kinds or record.get("kind") in kinds)
        ]
        if "--limit" in argv:
            found = found[-int(argv[argv.index("--limit") + 1]) :]
        return runner_module.Done(0, "".join(f"{json.dumps(record)}\n" for record in found), "")

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
            assert self.env("ORCA_TERMINAL_HANDLE")
            return self.ok({"run": self.bound_run() if self.coordinator == self.env("ORCA_TERMINAL_HANDLE") else None})
        if verb == ["orchestration", "run-show"]:
            assert argv[2:] == ["--id", "run_1"]
            return self.ok({"run": self.bound_run()})
        if verb == ["orchestration", "run-use"]:
            assert argv[2:] == ["--id", "run_1"]
            if self.run_use_error:
                return runner_module.Done(1, json.dumps({"ok": False, "error": self.run_use_error}), "")
            self.coordinator = self.env("ORCA_TERMINAL_HANDLE")
            self.generation += 1
            return self.ok({"run": self.bound_run()})
        if verb == ["orchestration", "worker-show"]:
            dispatch = self.dispatches[argv[3]]
            return self.ok({"dispatch": {"status": dispatch["status"]}, "observation": {"status": "live", "agentWait": None}, "terminal": {"handle": dispatch["terminal"]}})
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
        if self.coordinator != self.env("ORCA_TERMINAL_HANDLE"):
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
        tip = " ".join(itertools.takewhile(str.isdigit, argv))
        if "--check" in argv:
            outs = self.gates[tip]
            out = outs.pop(0) if len(outs) > 1 else outs[0]
            return runner_module.Done(*out) if isinstance(out, tuple) else runner_module.Done(0, out, "")
        return runner_module.Done(*self.enqueue_out[tip], "")

    def sends(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["orca", "orchestration", "send"]]

    def enqueues(self) -> list[list[str]]:
        return [call for call in self.calls if Path(call[0]).name == "stack-enqueue" and "--check" not in call and "--status" not in call]

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
    return FakeShell(tmp_path)


@pytest.fixture
def config(tmp_path: Path, shell: FakeShell) -> Path:
    briefs = tmp_path / "lfs"
    briefs.mkdir()
    (briefs / "f85c00ba").write_text("Lane brief: rebase onto dev when asked; never touch production.\n")
    shell.attachments[f"{LANE}.full.md"] = briefs / "f85c00ba"
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
                    "holds": str(tmp_path / "holds.md"),
                    "release_owner": "peer:sole-51",
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


def escalations(shell: FakeShell) -> list[str]:
    return [Path(post["path"]).read_text().splitlines()[0] if "path" in post else post["text"] for post in shell.posts if post["to"] == "root"]


def incident(tmp_path: Path, name: str) -> actions.Incident:
    return actions.Store(tmp_path / "store").load(name)


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
    shell.lose_sends = 4
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    for sent in range(1, 5):
        orca_pass(shell, config)
        assert len(shell.sends()) == sent
        later_pass(shell, config)
        assert len(shell.sends()) == sent + 1
    for _ in range(3):
        later_pass(shell, config)
    sends = shell.sends()
    assert len(sends) == 5 and all(send == sends[0] for send in sends)
    assert send_statuses(tmp_path) == ["failed"] * 4 + ["completed"]
    assert [action.action_id for action in incident(tmp_path, f"desk-lane-{LANE}").actions.values() if action.kind == "send"] == [
        "send:R625:ctx_a#1",
        "send:R625:ctx_a#1:resend1",
        "send:R625:ctx_a#1:resend2",
        "send:R625:ctx_a#1:resend3",
        "send:R625:ctx_a#1:resend4",
    ]
    assert shell.posts == []


def test_a_send_lost_through_every_resend_reports_lost_once_with_the_count_and_the_relay_text(shell, config, tmp_path):
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
        f"LOST R625 {LANE}: the send to ctx_a and all 10 resends lost their response; Orca holds no receipt and no message on its thread; "
        "nothing more is sent; this record's path holds the relay text for the root to deliver by hand"
    )
    assert Path(post["path"]).read_text().split("\n\n")[1] == "rebase onto dev\n"


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
    assert [line for line in escalations(shell) if line.startswith(("UNVERIFIABLE", "LOST"))] == [
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
    shell.lose_replies = 3
    orca_pass(shell, config)
    for sent in range(1, 4):
        orca_pass(shell, config)
        assert len(replies(shell)) == sent
        later_pass(shell, config)
        assert len(replies(shell)) == sent + 1
    later_pass(shell, config)
    assert len(replies(shell)) == 4
    assert send_statuses(tmp_path) == ["failed"] * 3 + ["completed"]
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


def test_a_routine_question_the_brief_settles_is_answered_without_escalating(shell, config, tmp_path):
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_a")
    question = {"id": "msg_q1", "type": "question", "subject": "rebase?", "body": "may I rebase onto dev", "thread_id": None, "payload": json.dumps({"dispatchId": "ctx_a"}), "from_handle": "term_ctx_a", "lane": LANE}
    shell.receive(question)
    orca_pass(shell, config)
    shell.receive(question)
    orca_pass(shell, config)
    replies = [call for call in shell.calls if call[:3] == ["orca", "orchestration", "reply"]]
    assert len(replies) == 1 and replies[0][replies[0].index("--id") + 1] == "msg_q1"
    assert len([call for call in shell.calls if Path(call[0]).name == "claude"]) == 1
    assert escalations(shell) == []


def test_an_ask_from_a_dispatch_handle_is_judged_against_its_lanes_brief(shell, config, tmp_path):
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_a")
    shell.receive({"id": "msg_q3", "type": "question", "subject": "rebase?", "body": "may I rebase onto dev", "from_handle": "dispatch:ctx_a"})
    orca_pass(shell, config)
    replies = [call for call in shell.calls if call[:3] == ["orca", "orchestration", "reply"]]
    assert len(replies) == 1 and replies[0][replies[0].index("--id") + 1] == "msg_q3"
    assert len([call for call in shell.calls if Path(call[0]).name == "claude"]) == 1
    assert escalations(shell) == []


def test_a_question_the_brief_does_not_settle_escalates_with_options(shell, config, tmp_path):
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_a")
    shell.verdict = {"verdict": "escalate", "text": "apply to prod? A) wait B) apply now"}
    shell.receive({"id": "msg_q2", "type": "escalation", "subject": "apply?", "body": "", "thread_id": None, "payload": json.dumps({"dispatchId": "ctx_a"}), "from_handle": "term_ctx_a", "lane": LANE})
    orca_pass(shell, config)
    lines = escalations(shell)
    assert len(lines) == 1 and "DECIDE msg_q2" in lines[0] and "A) wait B) apply now" in lines[0]
    assert not [call for call in shell.calls if call[:3] == ["orca", "orchestration", "reply"]]


def test_a_relayed_question_carries_its_whole_body_by_path(shell, config, tmp_path):
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_a")
    shell.verdict = {"verdict": "escalate", "text": "scope? A) widen B) hold"}
    body = "The scope surprise is " + "x" * 2_955 + " A) take it B) leave it"
    shell.receive({"id": "msg_q9", "type": "question", "subject": "scope?", "body": body, "thread_id": None, "payload": json.dumps({"dispatchId": "ctx_a"}), "from_handle": "term_ctx_a", "lane": LANE})
    orca_pass(shell, config)
    [post] = [post for post in shell.posts if post["to"] == "root"]
    receipt = Path(post["path"]).read_text()
    assert len(body) == 3_000 and len(post["text"]) <= runner_module.CCI_TEXT
    assert post["text"].startswith("DECIDE msg_q9") and receipt.startswith("DECIDE msg_q9") and "A) widen B) hold" in receipt
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
    brief.write_text("brief")
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


def launches(shell: FakeShell) -> list[list[str]]:
    return [call for call in shell.calls if Path(call[0]).name == "orca-launch.sh"]


@pytest.mark.parametrize(("model", "flags"), [("incident", ()), ("opus", ("--owner-directed",))])
def test_an_incident_or_owner_directed_launch_on_load_fails_at_once_and_tells_the_root_to_move_the_load(shell, config, tmp_path, model, flags):
    brief = tmp_path / "fix.md"
    brief.write_text("brief")
    shell.cpu_load = 140
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", model, "--effort", "xhigh", "--brief", str(brief), *flags)
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
    brief.write_text("brief")
    shell.cpu_load = 40
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "opus", "--effort", "xhigh", "--brief", str(brief))
    for _ in range(4):
        orca_pass(shell, config)
        shell.sleep(60)
    assert launches(shell) == [] and [line.split()[0] for line in escalations(shell)] == ["LOAD-HIGH"]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "accepted"
    for _ in range(3):
        shell.sleep(60)
        orca_pass(shell, config)
    assert launches(shell) == []
    action = incident(tmp_path, f"desk-lane-{LANE}").actions["R638"]
    assert action.status == "failed" and "load 40 above 8 cores" in action.reason
    [load, line] = escalations(shell)
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


def gate(*lines: str, would: str = "") -> str:
    return "\n".join(lines) + (f"\nwould enqueue {would} in one call\n" if would else "\nenqueued nothing: the bottom PR is BLOCKED\n")


def grade(shell: FakeShell, pr: str, text: str, lane: str = "fidelity-desk") -> int:
    seq = 34766 + len(shell.records)
    shell.records.append({"seq": seq, "drive": "d1", "lane": lane, "kind": "review", "to": ["root"], "refs": {"prs": [int(pr)]}, "text": f"fidelity: {text}"})
    return seq


def grade_gated_heads_ok(shell: FakeShell) -> None:
    for outs in shell.gates.values():
        for out in outs:
            for verdict in runner_module.VERDICT_LINE.finditer(out[1] if isinstance(out, tuple) else out):
                grade(shell, verdict["pr"], f"ok F307 at {verdict['sha'][:7]}")


def held_in(call: list[str]) -> list[str]:
    return [held for flag, held in itertools.pairwise(call) if flag == "--hold"]


def stack_rows(**states: str) -> list[dict]:
    return [
        {"pr": "28997", "lane": "artifact-retry", "branch": "a/1", "base": "dev", "head": "aaaa111111", "state": states.get("28997", "open"), "reported_head": "aaaa111111", "landed_sha": "f00d000001"},
        {"pr": "29016", "lane": "artifact-retry", "branch": "a/2", "base": "a/1", "head": "bbbb222222", "state": states.get("29016", "open"), "reported_head": "bbbb222222", "landed_sha": "f00d000002"},
        {"pr": "29020", "lane": LANE, "branch": "a/3", "base": "a/2", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
    ]


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
    assert [call[1:] for call in shell.enqueues()] == [["29016", "--head", "28997=aaaa111111", "--head", "29016=bbbb222222"]]
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
    lines = escalations(shell)
    assert len(lines) == 1 and "FOREIGN" in lines[0] and "#28997" in lines[0] and "nothing was enqueued" in lines[0]


def test_a_tip_no_drive_lane_opened_never_reaches_stack_enqueue(shell, config, tmp_path):
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333", "ours": False}]
    landing_pass(shell, config)
    assert not [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]


def test_an_open_pr_skipped_as_not_ours_escalates_once_per_head(shell, config, tmp_path):
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333", "ours": False}]
    landing_pass(shell, config)
    landing_pass(shell, config)
    lines = escalations(shell)
    assert len(lines) == 1 and "NOT-OURS" in lines[0] and "#29020" in lines[0] and "ledger.py register --lane <lane> --pr 29020" in lines[0]
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
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 not approved by poetic-svc"), gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    landing_pass(shell, config)
    assert not [call for call in shell.sends() if "approved" in call[call.index("--body") + 1]]
    assert escalations(shell) == []


def test_a_blocker_still_present_at_send_time_goes_to_the_lane_once(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 not approved by poetic-svc")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    asked = [call for call in shell.sends() if "not approved" in call[call.index("--body") + 1]]
    assert len(asked) == 1


def test_a_held_prefix_is_never_routed_and_holds_reach_stack_enqueue(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    (tmp_path / "holds.md").write_text("#29020 owner hold until the parity waves land\nlane:d4 ships whole\n")
    shell.rows = [
        {"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "28349", "lane": "d4", "branch": "d/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
    ]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 held")]
    shell.gates["28349"] = [gate("#28349 BLOCKED dddd444444 not approved by poetic-svc; held")]
    landing_pass(shell, config)
    checks = [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]
    assert all(call[call.index("--hold") :] == ["--hold", "28349", "--hold", "29020"] for call in checks)
    assert shell.sends() == [] and escalations(shell) == []


def test_a_hold_line_holds_only_its_leading_pr(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    (tmp_path / "holds.md").write_text("#36525 lands only after the Switch batch (#35413 deletes the SandSQL sandbox exclusion) and without its infra/rows/datadog/sandsql.ts hunk\n")
    shell.rows = [
        {"pr": "35413", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "36525", "lane": "d4", "branch": "d/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
    ]
    shell.gates["35413"] = [gate("#35413 GREEN cccc333333 graphite READY", would="#35413")]
    shell.gates["36525"] = [gate("#36525 BLOCKED dddd444444 held")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["35413"] = (0, "enqueue #35413\n")
    landing_pass(shell, config)
    calls = [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]
    assert calls and all(held_in(call) == ["36525"] for call in calls)
    assert [call[1] for call in shell.enqueues()] == ["35413"]


def test_holds_py_entries_reach_stack_enqueue(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    holds_file = str(tmp_path / "holds.md")
    assert holds.main(["--file", holds_file, "add", "--pr", "29020", "--reason", "owner hold until the parity waves land"]) == 0
    assert holds.main(["--file", holds_file, "add", "--lane", "d4", "--reason", "ships whole"]) == 0
    shell.rows = [
        {"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "28349", "lane": "d4", "branch": "d/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
    ]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 held")]
    shell.gates["28349"] = [gate("#28349 BLOCKED dddd444444 held")]
    landing_pass(shell, config)
    checks = [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]
    assert checks and all(call[call.index("--hold") :] == ["--hold", "28349", "--hold", "29020"] for call in checks)


def test_an_open_cci_hold_reaches_stack_enqueue_until_a_lift_closes_it(shell, config, tmp_path):
    shell.records = [
        {"seq": 7, "drive": "d1", "kind": "hold", "status": "open", "to": ["root"], "refs": {"prs": [29020]}, "text": "HOLD #29020, do not enqueue"},
        {"seq": 8, "drive": "d1", "kind": "hold", "status": "closed", "to": ["root"], "refs": {"prs": [28349]}, "text": "HOLD #28349"},
        {"seq": 9, "drive": "d1", "kind": "hold", "status": "open", "to": ["root"], "refs": {"targets": ["sandsql"]}, "text": "hold sandsql"},
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
    calls = [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]
    assert shell.enqueues() and all(held_in(call) == ["29020"] for call in calls)


def test_a_rules_blocked_pr_reaches_stack_enqueue_as_a_hold(shell, config, tmp_path):
    shell.rows = [
        {"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333", "rules_blocked": True},
        {"pr": "28349", "lane": "d4", "branch": "d/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444", "rules_blocked": False},
    ]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 held")]
    shell.gates["28349"] = [gate("#28349 GREEN dddd444444 graphite READY", would="#28349")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["28349"] = (0, "enqueue #28349\n")
    landing_pass(shell, config)
    calls = [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]
    assert shell.enqueues() and all(held_in(call) == ["29020"] for call in calls)


def test_hold_all_holds_every_open_row_except_the_released_prs_and_lanes(shell, config, tmp_path):
    (tmp_path / "holds.md").write_text("hold:all every PR waits for owner review\nrelease #29020 owner reviewed\nrelease lane:d5\n")
    shell.rows = [
        {"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "28349", "lane": "d4", "branch": "d/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
        {"pr": "28400", "lane": "d5", "branch": "e/1", "base": "dev", "head": "eeee555555", "state": "open", "reported_head": "eeee555555"},
        {"pr": "28300", "lane": "d6", "branch": "f/1", "base": "dev", "head": "ffff666666", "state": "landed", "reported_head": "ffff666666"},
    ]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    shell.gates["28349"] = [gate("#28349 BLOCKED dddd444444 held")]
    shell.gates["28400"] = [gate("#28400 GREEN eeee555555 graphite READY", would="#28400")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #29020\n")
    shell.enqueue_out["28400"] = (0, "enqueue #28400\n")
    landing_pass(shell, config)
    calls = [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]
    assert calls and all(held_in(call) == ["28349"] for call in calls)
    assert sorted(call[1] for call in shell.enqueues()) == ["28400", "29020"]
    assert shell.sends() == [] and escalations(shell) == []


def fork_rows() -> list[dict]:
    return [
        {"pr": "36463", "lane": "release-order", "branch": "ro/monitors", "base": "dev", "head": "aaaa111111", "state": "open", "reported_head": "aaaa111111"},
        {"pr": "36487", "lane": "release-order", "branch": "ro/verify", "base": "ro/monitors", "head": "bbbb222222", "state": "open", "reported_head": "bbbb222222"},
        {"pr": "36501", "lane": "release-order", "branch": "ro/readers", "base": "ro/monitors", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
        {"pr": "36510", "lane": "release-order", "branch": "ro/held", "base": "ro/monitors", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
        {"pr": "36520", "lane": "release-order", "branch": "ro/red", "base": "ro/monitors", "head": "eeee555555", "state": "open", "reported_head": "eeee555555"},
        {"pr": "36600", "lane": LANE, "branch": "b/1", "base": "dev", "head": "ffff666666", "state": "open", "reported_head": "ffff666666"},
    ]


def test_every_green_tip_of_a_fork_enqueues_in_one_call_naming_each_tip(shell, config, tmp_path):
    shell.launch("release-order", "ctx_r")
    (tmp_path / "holds.md").write_text("#36510 owner hold until the monitors soak\n")
    shell.rows = fork_rows()
    verdicts = (
        "#36463 GREEN aaaa111111 graphite READY",
        "#36487 GREEN bbbb222222 graphite READY",
        "#36501 GREEN cccc333333 graphite READY",
        "#36510 BLOCKED dddd444444 held",
        "#36520 BLOCKED eeee555555 failed check runs: test",
    )
    shell.gates["36487 36501 36510 36520"] = [gate(*verdicts, would="#36463 #36487 #36501")]
    shell.gates["36487 36501"] = [gate(*verdicts, would="#36463 #36487 #36501")]
    shell.gates["36600"] = [gate("#36600 BLOCKED ffff666666 not approved by poetic-svc")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["36487 36501"] = (0, "enqueue #36463 #36487 #36501: {}\n")
    landing_pass(shell, config)
    landing_pass(shell, config)
    checks = [call for call in shell.calls if Path(call[0]).name == "stack-enqueue" and "--check" in call]
    assert {tuple(itertools.takewhile(str.isdigit, call[1:])) for call in checks} == {("36487", "36501", "36510", "36520"), ("36487", "36501"), ("36600",)}
    assert all(call[call.index("--hold") :] == ["--hold", "36510"] for call in checks)
    assert [call[1:] for call in shell.enqueues()] == [["36487", "36501", "--hold", "36510", "--head", "36463=aaaa111111", "--head", "36487=bbbb222222", "--head", "36501=cccc333333"]]
    blockers = [call[call.index("--body") + 1] for call in shell.sends()]
    assert [body for body in blockers if "#36520" in body] and not [body for body in blockers if "#36510" in body]


def test_a_green_pr_no_drive_lane_opened_riding_a_fork_escalates_as_foreign(shell, config, tmp_path):
    shell.rows = fork_rows()[:3]
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
    lines = escalations(shell)
    assert len(lines) == 1 and "FOREIGN" in lines[0] and "#36530 would ride the batch" in lines[0]
    assert shell.enqueues() == []


def test_a_whole_policy_gates_each_tip_of_a_fork_alone(shell, config, tmp_path):
    shell.rows = fork_rows()[:3]
    assert cli(shell, config, "policy", "--key", "L300", "--landing", "whole", "--revision", "f9e8d7c6b5", "--supersedes", "a1b2c3d4e5", "--source", "owner ruling") == 0
    shell.gates["36487"] = [gate("#36463 GREEN aaaa111111 graphite READY", "#36487 GREEN bbbb222222 graphite READY", "#36501 BLOCKED cccc333333 is stacked beside the top")]
    shell.gates["36501"] = [gate("#36463 GREEN aaaa111111 graphite READY", "#36501 GREEN cccc333333 graphite READY", "#36487 BLOCKED bbbb222222 is stacked beside the top")]
    landing_pass(shell, config)
    checks = [call for call in shell.calls if Path(call[0]).name == "stack-enqueue" and "--check" in call]
    assert checks and all(len(list(itertools.takewhile(str.isdigit, call[1:]))) == 1 and "--whole" in call for call in checks)


def test_a_check_that_exits_with_no_verdict_escalates_once_instead_of_passing_silently(shell, config, tmp_path):
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333", "ours": True}]
    shell.gates["29020"] = [(2, "", "usage: stack-enqueue PR [PR ...]\nstack-enqueue: error: unrecognized arguments: 28349\n")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    lines = escalations(shell)
    assert len(lines) == 1 and "GATE-FAILED" in lines[0] and "#29020" in lines[0] and "unrecognized arguments: 28349" in lines[0]
    assert shell.enqueues() == []


def test_a_release_line_without_hold_all_holds_nothing(shell, config, tmp_path):
    (tmp_path / "holds.md").write_text("release #29020 owner reviewed\n")
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #29020\n")
    landing_pass(shell, config)
    assert all("--hold" not in call for call in shell.calls if Path(call[0]).name == "stack-enqueue")
    assert len(shell.enqueues()) == 1


def phase_b_rows() -> list[dict]:
    return [
        {"pr": "36397", "lane": PUBLISH_LANE, "branch": "p/0", "base": "dev", "head": "bbbb222222", "state": "landed", "reported_head": "bbbb222222"},
        {"pr": "36934", "lane": SIZING_LANE, "branch": "s/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"},
        {"pr": "36935", "lane": PUBLISH_LANE, "branch": "p/1", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"},
    ]


def stack_enqueue_calls(shell: FakeShell) -> list[list[str]]:
    return [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]


@pytest.mark.parametrize(
    "line",
    [
        PUBLISH_HOLD,
        f"HOLD {PUBLISH_LANE} (L34 re-land, revert of #36397): no enqueue until root posts 'L32 pass done' (F189)",
        f"lane:{PUBLISH_LANE} no enqueue until root posts 'L32 pass done' (F189)",
    ],
)
def test_each_lane_form_holds_the_open_rows_of_the_lane_it_names(shell, config, tmp_path, line):
    (tmp_path / "holds.md").write_text(f"{line}\n")
    shell.rows = phase_b_rows()
    shell.gates["36934"] = [gate("#36934 GREEN dddd444444 graphite READY", would="#36934")]
    shell.gates["36935"] = [gate("#36935 BLOCKED cccc333333 held")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["36934"] = (0, "enqueue #36934\n")
    landing_pass(shell, config)
    assert stack_enqueue_calls(shell) and all(held_in(call) == ["36935"] for call in stack_enqueue_calls(shell))
    assert [call[1] for call in shell.enqueues()] == ["36934"]
    assert shell.sends() == [] and escalations(shell) == []


def test_the_two_lane_lines_the_reader_dropped_hold_both_lanes(shell, config, tmp_path):
    (tmp_path / "holds.md").write_text(f"{PUBLISH_HOLD}\n{SIZING_HOLD}\n")
    shell.rows = phase_b_rows()
    shell.gates["36934"] = [gate("#36934 BLOCKED dddd444444 held")]
    shell.gates["36935"] = [gate("#36935 BLOCKED cccc333333 held")]
    landing_pass(shell, config)
    assert stack_enqueue_calls(shell) and all(held_in(call) == ["36934", "36935"] for call in stack_enqueue_calls(shell))
    assert shell.enqueues() == [] and escalations(shell) == []


@pytest.mark.parametrize("line", [PUBLISH_HOLD, f"HOLD {PUBLISH_LANE} (L34 re-land)", f"lane:{PUBLISH_LANE} waits for the L32 pass"])
def test_a_lane_line_naming_a_lane_no_row_carries_stops_every_enqueue_and_escalates_once(shell, config, tmp_path, line):
    holds_file = tmp_path / "holds.md"
    holds_file.write_text(f"{line}\n")
    shell.rows = [{"pr": "36935", "lane": "ship-l34-reland", "branch": "p/1", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["36935"] = [gate("#36935 GREEN cccc333333 graphite READY", would="#36935")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["36935"] = (0, "enqueue #36935\n")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert stack_enqueue_calls(shell) == [] and enqueues_of(tmp_path) == {}
    assert escalations(shell) == [
        f"HOLDS-UNREAD holds:{hashlib.sha1(line.encode()).hexdigest()[:8]} landing: the holds-file line `{line}` names lane {PUBLISH_LANE}, which no ledger row carries, so it holds nothing and the desk enqueues nothing until it is fixed"
        f" | in {holds_file} hold each PR as `#<pr> <why>`, or delete the line"
    ]
    assert [post["kind"] for post in shell.posts if post["to"] == "root"] == ["blocker"]
    holds_file.write_text("#36935 HOLD (L34 re-land): no enqueue until root posts 'L32 pass done'\n")
    shell.gates["36935"] = [gate("#36935 BLOCKED cccc333333 held")]
    landing_pass(shell, config)
    assert stack_enqueue_calls(shell) and all(held_in(call) == ["36935"] for call in stack_enqueue_calls(shell))
    assert shell.enqueues() == [] and len(escalations(shell)) == 1


def test_a_lane_line_whose_rows_have_all_landed_holds_nothing_and_stops_nothing(shell, config, tmp_path):
    (tmp_path / "holds.md").write_text(f"{PUBLISH_HOLD}\n")
    lone_green_pr(shell)
    shell.rows.append({"pr": "36397", "lane": PUBLISH_LANE, "branch": "p/0", "base": "dev", "head": "bbbb222222", "state": "landed", "reported_head": "bbbb222222"})
    grade_gated_heads_ok(shell)
    landing_pass(shell, config)
    assert all("--hold" not in call for call in stack_enqueue_calls(shell))
    assert len(shell.enqueues()) == 1 and escalations(shell) == []


def test_a_holds_line_no_form_matches_stops_every_enqueue_and_escalates_once_per_line(shell, config, tmp_path):
    holds_file = tmp_path / "holds.md"
    holds_file.write_text(f"#28349 owner review\n{UNREAD_HOLD}\n")
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert stack_enqueue_calls(shell) == [] and enqueues_of(tmp_path) == {}
    assert escalations(shell) == [
        f"HOLDS-UNREAD holds:{hashlib.sha1(UNREAD_HOLD.encode()).hexdigest()[:8]} landing: no form matches the holds-file line `{UNREAD_HOLD}`, so the desk enqueues nothing until it is fixed"
        f" | in {holds_file} write `#<pr> <why>`, `lane:<name> <why>`, `<lane> HOLD <why>`, `HOLD <lane> <why>`, `hold:all <why>`, or `release #<pr>`"
    ]
    assert [post["kind"] for post in shell.posts if post["to"] == "root"] == ["blocker"]
    holds_file.write_text(f"{UNREAD_HOLD}\n36935 waits for the L32 pass\n")
    landing_pass(shell, config)
    assert stack_enqueue_calls(shell) == []
    assert [line.split("`")[1] for line in escalations(shell)] == [UNREAD_HOLD, "36935 waits for the L32 pass"]
    holds_file.write_text("#28349 owner review\n")
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1 and len(escalations(shell)) == 2


def test_a_bad_holds_line_added_after_the_gate_stops_the_enqueue_it_was_about_to_make(shell, config, tmp_path):
    holds_file = tmp_path / "holds.md"
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    gated = shell.stack_enqueue

    def gate_then_add(argv: list[str]) -> runner_module.Done:
        holds_file.write_text(f"{UNREAD_HOLD}\n")
        return gated(argv)

    shell.stack_enqueue = gate_then_add
    landing_pass(shell, config)
    assert shell.enqueues() == [] and len(stack_enqueue_calls(shell)) == 1
    [action] = enqueues_of(tmp_path).values()
    assert (action.status, action.response["outcome"]) == ("completed", "blocked")
    assert [line.split("`")[1] for line in escalations(shell)] == [UNREAD_HOLD]
    shell.stack_enqueue = gated
    holds_file.write_text("")
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1 and len(escalations(shell)) == 1


def test_a_lane_line_on_an_empty_ledger_posts_its_blocker_and_an_unlistable_ledger_posts_none(shell, config, tmp_path):
    (tmp_path / "holds.md").write_text(f"{PUBLISH_HOLD}\n")
    shell.ledger_code = 1
    landing_pass(shell, config)
    assert escalations(shell) == []
    shell.ledger_code = 0
    landing_pass(shell, config)
    assert [line.split("`")[1] for line in escalations(shell)] == [PUBLISH_HOLD] and stack_enqueue_calls(shell) == []


def test_a_stopped_pass_still_settles_a_lost_enqueue_from_graphite_status_and_makes_no_other_stack_enqueue_call(shell, config, tmp_path):
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", would="#28997")]
    grade_gated_heads_ok(shell)
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    runner_module.seed_policy(runner)
    landing = runner_module.Landing(runner, runner.config.landing)
    landing.accept("29020", ["28997"], {"28997": {"sha": "aaaa111111"}}, {"28997": {"ours": True}})
    key = next(iter(store.load("desk-landing").actions.keys() - {"policy:a1b2c3d4e5"}))
    with store.owned("desk-landing", 0) as live:
        live.start(key, shell.now())
    shell.sleep(16 * 60)
    shell.statuses = "#28997 QUEUED_TO_MERGE aaaa111111 open review APPROVED on dev\n"
    (tmp_path / "holds.md").write_text(f"{UNREAD_HOLD}\n")
    landing_pass(shell, config)
    action = store.load("desk-landing").actions[key]
    assert (action.status, action.response["outcome"]) == ("completed", "enqueued")
    assert [call[-1] for call in stack_enqueue_calls(shell)] == ["--status"]
    assert [line.split("`")[1] for line in escalations(shell) if line.startswith("HOLDS-UNREAD")] == [UNREAD_HOLD]


def test_a_blank_holds_line_is_no_error(shell, config, tmp_path):
    (tmp_path / "holds.md").write_text("\n#28349 owner review\n   \n\n")
    lone_green_pr(shell)
    grade_gated_heads_ok(shell)
    landing_pass(shell, config)
    assert stack_enqueue_calls(shell) and all(held_in(call) == ["28349"] for call in stack_enqueue_calls(shell))
    assert len(shell.enqueues()) == 1 and escalations(shell) == []


def lone_green_pr(shell: FakeShell) -> None:
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")


def blockers(shell: FakeShell) -> list[dict]:
    return [post for post in shell.posts if post["kind"] == "blocker"]


def test_a_head_the_fidelity_lane_graded_ok_is_enqueued(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307,F321 at cccc333. The restack onto dev has a range-diff of '='.")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--head", "29020=cccc333333"]]
    assert blockers(shell) == [] and escalations(shell) == []


def test_the_enqueue_pins_every_pr_of_the_prefix_to_the_head_fidelity_graded(shell, config, tmp_path):
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #28997 #29016 #29020: {}\n")
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29020", "--head", "28997=aaaa111111", "--head", "29016=bbbb222222", "--head", "29020=cccc333333"]]
    assert all("--head" not in call for call in shell.calls if "--check" in call)


def test_a_head_graded_missing_is_blocked_with_the_grade(shell, config, tmp_path):
    lone_green_pr(shell)
    seq = grade(shell, "29020", "missing F307 at cccc333 (View fix). It carries no test.")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.enqueues() == [] and enqueues_of(tmp_path) == {}
    [blocker] = blockers(shell)
    assert blocker["to"] == f"{LANE},fidelity-desk" and blocker["topic"] == "29020"
    assert blocker["text"] == f"#29020 cccc333333: nothing in #29020 was enqueued; fidelity-desk graded this head on cci #{seq}: fidelity: missing F307 at cccc333 (View fix). It carries no test."


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


def test_one_ungraded_pr_keeps_its_whole_prefix_out_of_the_queue(shell, config, tmp_path):
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 BLOCKED cccc333333 ai-review pending", would="#28997 #29016")]
    grade(shell, "28997", "ok F307 at aaaa111.")
    landing_pass(shell, config)
    assert shell.enqueues() == []
    [blocker] = [post for post in blockers(shell) if post["topic"] == "29016"]
    assert blocker["to"] == "artifact-retry,fidelity-desk" and not [post for post in blockers(shell) if post["topic"] == "28997"]
    assert blocker["text"].startswith("#29016 bbbb222222: nothing in #28997 #29016 was enqueued; no review from fidelity-desk")


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


def test_a_head_regraded_missing_after_its_enqueue_was_accepted_enqueues_nothing(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    runner_module.seed_policy(runner)
    landing = runner_module.Landing(runner, runner.config.landing)
    landing.accept(["29020"], ["29020"], {"29020": {"sha": "cccc333333"}}, {"29020": {"ours": True, "lane": LANE}})
    seq = grade(shell, "29020", "missing F307 at cccc333.")
    landing.enqueue()
    assert shell.enqueues() == []
    [blocker] = blockers(shell)
    assert f"cci #{seq}: fidelity: missing F307" in blocker["text"]
    [action] = enqueues_of(tmp_path).values()
    assert action.response["outcome"] == "blocked" and "no fidelity: ok from fidelity-desk at the head of #29020" in action.response["out"]


def held_pr(shell: FakeShell, tmp_path: Path, head: str = "cccc333333") -> None:
    (tmp_path / "holds.md").write_text("#29020 owner review\n")
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": head, "state": "open", "reported_head": head}]
    shell.gates["29020"] = [gate(f"#29020 BLOCKED {head} held")]


def fidelity_statuses(shell: FakeShell) -> list[tuple[str, str]]:
    return [(status["sha"], status["state"]) for status in shell.head_statuses]


@pytest.mark.parametrize(("text", "state"), [("ok F307 at cccc333.", "success"), ("missing F307 at cccc333 (View fix).", "failure")])
def test_a_fidelity_grade_sets_the_fidelity_commit_status_on_the_graded_head(shell, config, tmp_path, text, state):
    held_pr(shell, tmp_path)
    seq = grade(shell, "29020", text)
    landing_pass(shell, config)
    assert shell.head_statuses == [{"sha": "cccc333333", "state": state, "context": "fidelity", "description": f"cci #{seq} fidelity: {text}"}]
    assert escalations(shell) == []


def test_the_status_describes_the_grade_by_its_first_line_clipped_to_the_github_limit(shell, config, tmp_path):
    held_pr(shell, tmp_path)
    first_line = "missing F328 at cccc333 (#29020). " + "The sandbox build of this head is still owed. " * 4
    seq = grade(shell, "29020", f"{first_line}\nNote for root: a second line.")
    landing_pass(shell, config)
    [status] = shell.head_statuses
    assert (status["sha"], status["state"]) == ("cccc333333", "failure")
    assert len(status["description"]) == 140 and status["description"] == f"cci #{seq} fidelity: {first_line}"[:139] + "…"


def test_the_status_is_on_the_head_before_the_gate_reads_it(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    calls = [call for call in shell.calls if call[:2] == ["gh", "api"] or Path(call[0]).name == "stack-enqueue"]
    assert calls[0] == ["gh", "api", "repos/Forge-AI/monorepo/statuses/cccc333333", "--method", "POST", "--input", "-"]
    assert fidelity_statuses(shell) == [("cccc333333", "success")] and len(shell.enqueues()) == 1


def test_a_new_head_gets_a_status_only_once_the_fidelity_lane_grades_it(shell, config, tmp_path):
    held_pr(shell, tmp_path)
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    held_pr(shell, tmp_path, head="dddd444444")
    landing_pass(shell, config)
    assert fidelity_statuses(shell) == [("cccc333333", "success")]
    grade(shell, "29020", "missing F307 at dddd444 (View fix).")
    landing_pass(shell, config)
    assert fidelity_statuses(shell) == [("cccc333333", "success"), ("dddd444444", "failure")]


def test_a_regrade_of_the_same_head_flips_its_status(shell, config, tmp_path):
    held_pr(shell, tmp_path)
    grade(shell, "29020", "missing F307 at cccc333 (View fix).")
    landing_pass(shell, config)
    grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert fidelity_statuses(shell) == [("cccc333333", "failure"), ("cccc333333", "success")]


def test_a_restarted_runner_does_not_post_a_status_again(shell, config, tmp_path):
    held_pr(shell, tmp_path)
    seq = grade(shell, "29020", "ok F307 at cccc333.")
    landing_pass(shell, config)
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert fidelity_statuses(shell) == [("cccc333333", "success")]
    action = incident(tmp_path, "desk-landing").actions[f"status:29020:cccc333333:{seq}"]
    assert (action.kind, action.status, action.response["state"]) == ("status", "completed", "success")


def test_a_head_with_no_grade_gets_no_status(shell, config, tmp_path):
    held_pr(shell, tmp_path)
    shell.rows.append({"pr": "28300", "lane": "d6", "branch": "f/1", "base": "dev", "head": "ffff666666", "state": "landed", "reported_head": "ffff666666"})
    grade(shell, "29020", "ok F307 at bbbb222.")
    grade(shell, "28349", "ok F307 at cccc333.")
    grade(shell, "28300", "ok F307 at ffff666.")
    grade(shell, "29020", "ok F307 at cccc333.", lane="rules-desk")
    landing_pass(shell, config)
    assert shell.head_statuses == [] and escalations(shell) == []
    assert [action for action in incident(tmp_path, "desk-landing").actions.values() if action.kind == "status"] == []


def test_one_failed_cci_read_skips_the_mirror_for_that_pass_and_the_gate_still_runs(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    shell.tail_failures = 1
    landing_pass(shell, config)
    assert shell.head_statuses == [] and len(shell.enqueues()) == 1
    assert [line.split(":", 1)[0] for line in escalations(shell)] == ["CCI-TAIL-FAILED cci-tail"]
    landing_pass(shell, config)
    assert fidelity_statuses(shell) == [("cccc333333", "success")]


def test_a_cci_outage_ends_the_landing_pass_without_killing_the_runner(shell, config, tmp_path):
    lone_green_pr(shell)
    grade(shell, "29020", "ok F307 at cccc333.")
    shell.tail_error = "cci: daemon unavailable"
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.head_statuses == [] and stack_enqueue_calls(shell) == []
    assert escalations(shell) == ["CCI-TAIL-FAILED cci-tail:2026-10-01T18:0 runner: cci tail exited 1: cci: daemon unavailable"]
    shell.tail_error = ""
    landing_pass(shell, config)
    assert fidelity_statuses(shell) == [("cccc333333", "success")] and len(shell.enqueues()) == 1


def test_a_status_github_refuses_is_escalated_once_and_posted_on_a_later_pass(shell, config, tmp_path):
    held_pr(shell, tmp_path)
    seq = grade(shell, "29020", "ok F307 at cccc333.")
    shell.status_error = "gh: API rate limit exceeded (HTTP 403)"
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert shell.head_statuses == []
    assert escalations(shell) == [
        f"STATUS-FAILED status:29020:cccc333333:{seq} #29020: GitHub refused the `fidelity` commit status on cccc333333: gh: API rate limit exceeded (HTTP 403); the runner posts it again on the next pass"
    ]
    assert [post["kind"] for post in shell.posts if post["to"] == "root"] == ["defect"]
    shell.status_error = ""
    landing_pass(shell, config)
    assert fidelity_statuses(shell) == [("cccc333333", "success")] and len(escalations(shell)) == 1


def test_an_enqueue_whose_response_was_lost_settles_from_graphite_status(shell, config, tmp_path):
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", would="#28997")]
    grade_gated_heads_ok(shell)
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    runner_module.seed_policy(runner)
    landing = runner_module.Landing(runner, runner.config.landing)
    landing.accept("29020", ["28997"], {"28997": {"sha": "aaaa111111"}}, {"28997": {"ours": True}})
    key = next(iter(store.load("desk-landing").actions.keys() - {"policy:a1b2c3d4e5"}))
    with store.owned("desk-landing", 0) as live:
        live.start(key, shell.now())
    shell.sleep(16 * 60)
    shell.statuses = "#28997 QUEUED_TO_MERGE aaaa111111 open review APPROVED on dev\n"
    landing_pass(shell, config)
    action = store.load("desk-landing").actions[key]
    assert (action.status, action.response["outcome"]) == ("completed", "enqueued")
    assert shell.enqueues() == []


QUEUE_ADDED = "### Merge activity\n\n* **Oct 13, 12:45 AM UTC**: `yasyf` added this pull request to the [Graphite merge queue](https://app.graphite.com/merges).\n"


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
    shell.queue["29020"] = "not queued"
    shell.activity["29020"] = QUEUE_ADDED
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 2
    shell.queue["29020"] = "queued"
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 2
    first, second = (action for _, action in sorted(enqueues_of(tmp_path).items()))
    assert (first.response["outcome"], second.response["outcome"]) == ("dropped", "enqueued")
    assert first.response["drop"] == {"29020": QUEUE_ADDED.strip().splitlines()[-1]}
    assert escalations(shell) == [] and [post for post in shell.posts if post["kind"] == "blocker"] == []


def test_a_prefix_the_queue_dropped_for_a_conflict_goes_to_its_lane_as_a_blocker(shell, config, tmp_path):
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    landing_pass(shell, config)
    shell.queue["29020"] = "evicted"
    shell.activity["29020"] = QUEUE_ADDED + "* **Oct 13, 1:06 AM UTC**: The Graphite merge queue removed this pull request because it had merge conflicts.\n"
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1
    blockers = [post for post in shell.posts if post["kind"] == "blocker"]
    assert len(blockers) == 1 and blockers[0]["to"] == LANE and blockers[0]["topic"] == "29020"
    assert "merge conflicts" in blockers[0]["text"] and "cccc333333" in blockers[0]["text"]
    assert [action.response["outcome"] for action in enqueues_of(tmp_path).values()] == ["ejected"]


def test_an_ejected_pr_is_never_enqueued_again_at_its_head_in_a_smaller_prefix(shell, config, tmp_path):
    shell.rows = [row for row in stack_rows() if row["pr"] != "29020"]
    shell.gates["29016"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", would="#28997 #29016")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29016"] = (0, "enqueue #28997 #29016: {}\n")
    landing_pass(shell, config)
    shell.queue["29016"] = "not queued"
    shell.activity["29016"] = QUEUE_ADDED + "* **Oct 13, 1:06 AM UTC**: The Graphite merge queue removed this pull request due to Failed CI (test-unit).\n"
    shell.gates["29016"] = [gate("#28997 QUEUED aaaa111111 graphite QUEUED_TO_MERGE", "#29016 GREEN bbbb222222 graphite READY", would="#29016")]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 1
    assert len([post for post in shell.posts if post["kind"] == "blocker"]) == 1
    shell.rows[1] |= {"head": "bbbb333333", "reported_head": "bbbb333333"}
    shell.gates["29016"] = [gate("#28997 QUEUED aaaa111111 graphite QUEUED_TO_MERGE", "#29016 GREEN bbbb333333 graphite READY", would="#29016")]
    grade_gated_heads_ok(shell)
    landing_pass(shell, config)
    assert len(shell.enqueues()) == 2


def test_a_believed_queued_pr_whose_row_left_the_ledger_is_skipped(shell, config, tmp_path):
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    grade_gated_heads_ok(shell)
    shell.enqueue_out["29020"] = (0, "enqueue #29020: {}\n")
    landing_pass(shell, config)
    shell.rows = [{"pr": "28349", "lane": "d4", "branch": "d/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444"}]
    shell.gates["28349"] = [gate("#28349 BLOCKED dddd444444 held")]
    landing_pass(shell, config)
    assert not [call for call in shell.calls if call[:2] == ["ccx", "vcs"]]


def fast_row(shell: FakeShell, labels: str = "merge-fast,externally-merged") -> dict:
    return {"pr": "36642", "lane": LANE, "branch": "f/1", "base": "dev", "head": "eeee555555", "state": "landed", "reported_head": "eeee555555", "labels": labels, "landed_sha": FAST_SQUASH, "landed_at": actions.stamp(shell.now())}


def land_fast(shell: FakeShell, config: Path, body: str = FAST_BODY, labels: str = "merge-fast,externally-merged") -> None:
    shell.bodies["36642"] = body
    shell.rows = [fast_row(shell, labels) | {"state": "open"}]
    shell.gates["36642"] = [gate("#36642 QUEUED eeee555555 graphite QUEUED_TO_MERGE")]
    landing_pass(shell, config)
    shell.sleep(180)
    shell.rows = [fast_row(shell, labels)]


def body_reads(shell: FakeShell) -> list[list[str]]:
    return [call for call in shell.calls if call[:3] == ["gh", "pr", "view"] and call[-1] == "body"]


def release_action(tmp_path: Path) -> actions.Action:
    return incident(tmp_path, "desk-landing").actions["release:36642"]


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


def test_a_release_record_posted_just_before_a_crash_is_never_posted_twice(shell, config, tmp_path, monkeypatch):
    land_fast(shell, config)
    save = runner_module.release_posted

    def killed(*args):
        raise RuntimeError("killed after the post")

    monkeypatch.setattr(runner_module, "release_posted", killed)
    with pytest.raises(RuntimeError, match="killed after the post"):
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


def test_the_view_renders_pacific_times_from_receipts(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    view = (tmp_path / "desk-runner.md").read_text()
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
        "RELAY-FAILED R633 incident-fix: no live dispatch (ctx_a is completed); nothing was relayed",
        "RELAY-FAILED R634 ghost: no live dispatch; nothing was relayed",
    ]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R633"].status == "failed"
    assert shell.sends() == [] and replies(shell) == []


def test_a_judge_runs_beside_the_pass_and_answers_once_it_exits(shell, config, tmp_path):
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_a")
    shell.judge_process = Running()
    shell.receive({"id": "msg_q5", "type": "question", "subject": "rebase?", "body": "may I rebase onto dev", "thread_id": None, "payload": json.dumps({"dispatchId": "ctx_a"}), "from_handle": "term_ctx_a", "lane": LANE})
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    runner.check()
    runner.resume_judges()

    def replies():
        return [call for call in shell.calls if call[:3] == ["orca", "orchestration", "reply"]]

    assert replies() == [] and incident(tmp_path, "desk-runner").actions["judge:msg_q5"].status == "started"
    runner.judge_file("msg_q5", "out").write_text(json.dumps([{"type": "result", "structured_output": shell.verdict}]))
    shell.judge_process.code = 0
    runner.resume_judges()
    runner.resume_judges()
    assert len(replies()) == 1 and incident(tmp_path, "desk-runner").actions["judge:msg_q5"].status == "verified"


def test_a_judged_answer_survives_a_restart_before_its_reply(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    runner.book.accept("desk-runner", "judge:msg_q3", "judge", json.dumps({"msg": "msg_q3", "question": "rebase?"}), LANE, None)
    with store.owned("desk-runner", 0) as live:
        live.start("judge:msg_q3", shell.now())
        live.complete("judge:msg_q3", {"verdict": "answer", "text": "yes, per the brief", "at": "2026-10-01T18:00:00Z"})
    orca_pass(shell, config)
    orca_pass(shell, config)
    replies = [call for call in shell.calls if call[:3] == ["orca", "orchestration", "reply"]]
    assert len(replies) == 1 and replies[0][replies[0].index("--id") + 1] == "msg_q3"


def test_a_judge_that_never_returned_escalates(shell, config, tmp_path):
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    runner.book.accept("desk-runner", "judge:msg_q4", "judge", json.dumps({"msg": "msg_q4", "question": "scope?"}), LANE, None)
    with store.owned("desk-runner", 0) as live:
        live.start("judge:msg_q4", shell.now())
    shell.sleep(6 * 60)
    orca_pass(shell, config)
    lines = escalations(shell)
    assert len(lines) == 1 and "DECIDE msg_q4" in lines[0] and "never returned" in lines[0]


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
    landing.accept("29020", ["28997"], {"28997": {"sha": "aaaa111111"}}, {"28997": {"ours": True}})
    shell.gates["28997"] = [gate("#28997 GREEN abab111111 graphite READY", would="#28997")]
    landing.enqueue()
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
    runner_module.Landing(runner, runner.config.landing).accept("29020", ["28997"], {"28997": {"sha": "aaaa111111"}}, {"28997": {"ours": True}})
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
    assert [line for line in escalations(shell) if "ORCA-INBOX" not in line] == [
        f"OUTCOME {fixed['id']} {LANE}: worker_done ? dispatch=ctx_a: fixed",
        f"OUTCOME {stray['id']} {LANE}: worker_done ? dispatch=ctx_a: sent to the terminal",
        f"UNRELAYED {note['id']} {LANE}: status from term_ctx_a at sequence {note['sequence']}, behind cursor {fixed['sequence']}, has no relay record and its type has no relay: direct note",
    ]
    orca_pass(shell, config)
    assert len(escalations(shell)) == 4


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


@pytest.mark.parametrize("failure", [runner_module.Done(1, json.dumps({"ok": False, "error": {"code": "runtime_unavailable", "message": "socket closed"}}), ""), runner_module.Done(1, "", "socket closed")])
def test_inbox_errors_escalate_once_per_bucket_without_advancing_the_cursor(shell, config, tmp_path, failure):
    orca_pass(shell, config)
    fresh = shell.receive({"type": "worker_done", "subject": "fresh"})
    shell.inbox_error = failure
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

    def write_view():
        passes.append(shell.now())
        if len(passes) == 3:
            raise RuntimeError("end loop")

    def sleep(seconds):
        sleeps.append(seconds)
        shell.clock += timedelta(seconds=seconds)

    monkeypatch.setattr(runner, "write_view", write_view)
    monkeypatch.setattr(shell, "sleep", sleep)
    with pytest.raises(RuntimeError, match="end loop"):
        runner_module.run_orca(runner, False)
    assert set(sleeps) == {runner_module.WAKE_SECONDS}
    assert [later - earlier for earlier, later in itertools.pairwise(passes)] == [timedelta(seconds=runner_module.PASS_SECONDS)] * 2


def test_a_desk_inbox_append_ends_the_wait_at_once(shell, config, tmp_path, monkeypatch):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    inbox = runner.config.desk_inbox
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_text("")
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            inbox.write_text("R1 (7:13 AM) orca-desk: launch lane-a NOW incident xhigh brief=/tmp/brief.md\n")

    monkeypatch.setattr(shell, "sleep", sleep)
    runner.idle(runner_module.PASS_SECONDS)
    assert sleeps == [runner_module.WAKE_SECONDS] * 2


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
    assert reclaims[0].endswith(f"1 settled dispatch(es) still hold their terminal: {LANE}=ctx_a:term_ctx_a; run .agents/skills/orca/scripts/orca-gc --run run_1 --dispatch ctx_a")
    assert [call for call in shell.calls if FORBIDDEN & {Path(token).name for token in call}] == []


def test_a_large_settled_set_names_the_whole_run(shell, config, tmp_path):
    with_gc(config)
    for index in range(runner_module.RECLAIM_NAMED + 2):
        shell.launch(f"lane-{index:02d}", f"ctx_{index:02d}", status="failed")
    orca_pass(shell, config)
    [reclaim] = [line for line in escalations(shell) if line.startswith("RECLAIM ")]
    assert " and 2 more; run .agents/skills/orca/scripts/orca-gc --run run_1" in reclaim
    assert reclaim.endswith("--run run_1")


def test_without_a_gc_the_line_names_the_r195_bar(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a", status="completed")
    orca_pass(shell, config)
    [reclaim] = [line for line in escalations(shell) if line.startswith("RECLAIM ")]
    assert reclaim.endswith("close each idle terminal and remove each finished worktree under R195")


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
    brief.write_text("brief")
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "sol", "--effort", "xhigh", "--brief", str(brief))
    return brief


@pytest.mark.parametrize(
    ("environ", "why"),
    [
        ({"ORCA_TERMINAL_HANDLE": "term_root", "ORCA_PANE_KEY": "pane_root"}, "terminal term_root coordinates no Run; Orca binds run_1 to term_dead at generation 3"),
        ({}, "no ORCA_TERMINAL_HANDLE: the runner was not started from an Orca terminal"),
    ],
)
def test_a_runner_whose_terminal_is_not_the_coordinator_refuses_to_start_and_prints_the_rebind(shell, config, tmp_path, capsys, environ, why):
    shell.environ = environ
    shell.coordinator = "term_dead"
    accept_launch(shell, config, tmp_path)
    assert cli(shell, config, "run", "--desk", "orca", "--once") == 3
    expected = f"desk-runner cannot start workers on run_1: {why}. From the coordinator's Orca terminal run `desk-runner.py rebind --config {config.resolve()}`, and start the runner from that terminal"
    assert capsys.readouterr().err == expected + "\n"
    assert launches(shell) == [] and orca_calls(shell, "inbox") == []
    assert len(orca_calls(shell, "run-current")) == (1 if environ else 0)
    [line] = escalations(shell)
    assert f"UNBOUND unbound:{environ.get('ORCA_TERMINAL_HANDLE', '')}:term_dead:3 runner: {expected}; the runner refused to start" in line
    binding = incident(tmp_path, "desk-runner").facts["binding"]
    assert binding["bound"] is False and binding["why"] == why
    assert cli(shell, config, "run", "--desk", "orca", "--once") == 3
    assert len(escalations(shell)) == 1


def test_a_binding_lost_mid_run_holds_launches_and_escalates_once_until_rebind(shell, config, tmp_path):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    assert runner.binding()["bound"]
    brief = accept_launch(shell, config, tmp_path)
    shell.coordinator = "term_elsewhere"
    shell.generation = 4
    for _ in range(3):
        runner.deliver()
        runner.flush()
    assert launches(shell) == []
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "accepted"
    [line] = escalations(shell)
    assert "UNBOUND unbound:term_root:term_elsewhere:4 runner: desk-runner cannot start workers on run_1: terminal term_root coordinates no Run" in line
    assert line.endswith("; launches wait until it is bound")
    assert cli(shell, config, "rebind") == 0
    runner.deliver()
    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, "sol", "xhigh", str(brief)]]


def test_rebind_moves_the_run_to_this_terminal_and_records_how(shell, config, tmp_path, capsys):
    shell.coordinator = "term_dead"
    assert cli(shell, config, "rebind") == 0
    assert orca_calls(shell, "run-use") == [["orca", "orchestration", "run-use", "--id", "run_1", "--json"]]
    via = "orca orchestration run-use by desk-runner rebind at 11:00, replacing term_dead at generation 3"
    line = f"binding: terminal term_root pane pane_root coordinates run_1 at generation 4, via {via}; checked 11:00"
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


def test_a_runner_bound_at_start_records_the_inherited_terminal(shell, config, tmp_path, capsys):
    orca_pass(shell, config)
    binding = incident(tmp_path, "desk-runner").facts["binding"]
    assert (binding["terminal"], binding["pane"], binding["coordinator"], binding["generation"], binding["bound"]) == ("term_root", "pane_root", "term_root", 3, True)
    assert binding["via"] == "ORCA_TERMINAL_HANDLE inherited from the shell that started the runner, already bound at generation 3"
    cli(shell, config, "show")
    assert capsys.readouterr().out.splitlines()[1].startswith("binding: terminal term_root pane pane_root coordinates run_1 at generation 3")


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


def test_an_unbound_launch_held_by_load_stays_accepted_past_the_load_deadline(shell, config, tmp_path):
    brief = tmp_path / "fix.md"
    brief.write_text("brief")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "opus", "--effort", "xhigh", "--brief", str(brief))
    shell.cpu_load = 40
    shell.coordinator = "term_elsewhere"
    for _ in range(8):
        runner.deliver()
        runner.flush()
        shell.sleep(60)
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "accepted"
    assert [line.split()[0] for line in escalations(shell)] == ["UNBOUND"] and [post["kind"] for post in shell.posts] == ["blocker"]


def desk_inbox(tmp_path: Path, *lines: str) -> None:
    path = tmp_path / "inbox/orca-desk.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as inbox:
        inbox.write("".join(f"{line}\n" for line in lines))


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
    desk_inbox(tmp_path, f"R1 (9:0x AM PT) orca-desk: relay to {LANE}: history is never replayed")
    orca_pass(shell, config)
    desk_inbox(tmp_path, f"R2 (3:0x PM PT) orca-desk: relay to {LANE} and walker: G331 resumes the chain", "- walker (3:1x PM PT) ASK root: say 'orca-desk: relay to walker: go'")
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
    desk_inbox(tmp_path, "R1 orca-desk: relay to nobody: history")
    orca_pass(shell, config)
    shell.post_error = "cci: store schema 9 is newer than this cci 0.9.3 (schema 6)"
    desk_inbox(tmp_path, f"R2 orca-desk: relay to {LANE}: rebase onto dev")
    orca_pass(shell, config)
    assert len(shell.sends()) == 1 and escalations(shell) == []
    assert "1 escalation(s) left for the next pass; cci post exited 1: cci: store schema 9" in capsys.readouterr().err
    assert cli(shell, config, "relay", "--key", "R3", "--lane", LANE, "--text", "rebase again") == 1
    shell.post_error = ""
    orca_pass(shell, config)
    assert escalations(shell) == [f"RELAYED R2 {LANE}: relay to dispatch ctx_a"]


def test_each_orca_pass_writes_a_heartbeat_beside_the_desk_inbox(shell, config, tmp_path):
    orca_pass(shell, config)
    beat = json.loads((tmp_path / "inbox" / runner_module.HEARTBEAT).read_text())
    assert beat == {
        "pid": os.getpid(),
        "at": shell.clock.isoformat(),
        "config": str(config.resolve()),
        "terminal": "term_root",
        "restart": f"nohup desk-runner.py run --config {config.resolve()} --desk orca > {tmp_path / 'orca-runner.log'} 2>&1 < /dev/null &",
    }


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
    desk_inbox(tmp_path, f"- orca-desk: relay to {LANE}: walker: router GO")
    orca_pass(shell, config)
    [reply] = replies(shell)
    assert reply[reply.index("--id") + 1] == "msg_q9"
    assert reply[reply.index("--body") + 1].startswith("walker: router GO\n")
    assert shell.sends() == []
    [line] = escalations(shell)
    assert line == f"RELAYED inbox@0 {LANE}: reply to question msg_q9 of dispatch ctx_a"


def test_an_inbox_relay_to_a_lane_without_a_live_dispatch_fails_visibly(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a", status="completed")
    orca_pass(shell, config)
    desk_inbox(tmp_path, f"R3 orca-desk: relay to {LANE} and ghost: G332")
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
    desk_inbox(
        tmp_path,
        f"R1050 (12:0x AM PT) orca-desk: relay G328 (AIG applies held again) to {LANE}.",
        f"R1048 (10:5x PM PT) orca-desk: relay to {LANE}: `walker: router GO`; relay to walker: `d-land: sandsql GO`",
    )
    orca_pass(shell, config)
    assert shell.sends() == []
    lines = [line for line in escalations(shell)]
    assert [line.split(":", 1)[0] for line in lines] == ["RELAY-FAILED R1050 inbox", "RELAY-FAILED R1048 inbox"]
    assert all(runner_module.RELAY_GRAMMAR in line for line in lines)


def test_an_inbox_relay_waits_for_its_newline_and_a_reread_relays_nothing_twice(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    path = tmp_path / "inbox/orca-desk.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"R4 orca-desk: relay to {LANE}: rebase onto dev")
    orca_pass(shell, config)
    assert shell.sends() == []
    path.write_text(path.read_text() + "\n")
    orca_pass(shell, config)
    store = actions.Store(tmp_path / "store")
    with store.owned("desk-runner", store.load("desk-runner").owner_generation) as runner:
        runner.facts[f"desk-inbox:{path}"] = 0
    orca_pass(shell, config)
    assert len(shell.sends()) == 1
    assert len(escalations(shell)) == 1


def test_an_hourly_rotation_archives_read_lines_and_the_cursor_still_names_the_next_line(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    desk_inbox(tmp_path, f"R1 orca-desk: relay to {LANE}: first")
    orca_pass(shell, config)
    shell.clock += timedelta(hours=7)
    desk_inbox(tmp_path, f"R2 orca-desk: relay to {LANE}: second")
    orca_pass(shell, config)
    path = tmp_path / "inbox/orca-desk.md"
    assert path.read_text() == f"R2 orca-desk: relay to {LANE}: second\n"
    assert (tmp_path / "inbox/orca-desk.md.archive").is_dir()
    orca_pass(shell, config)
    assert [call[call.index("--subject") + 1] for call in shell.sends()] == ["R1: act R1", "R2: act R2"]


def test_an_inbox_relay_under_a_key_holding_another_relay_fails_visibly(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    cli(shell, config, "relay", "--key", "R5", "--lane", LANE, "--text", "rebase onto dev")
    desk_inbox(tmp_path, f"R5 orca-desk: relay to {LANE}: deploy now")
    orca_pass(shell, config)
    assert [call[call.index("--body") + 1].split("\n", 1)[0] for call in shell.sends()] == ["rebase onto dev"]
    [line] = escalations(shell)
    assert line == f"RELAY-FAILED R5 {LANE}: R5 already holds a different relay to {LANE} (accepted)"


def launch_brief(tmp_path: Path) -> Path:
    brief = tmp_path / "fix-brief.md"
    brief.write_text("brief")
    return brief


@pytest.mark.parametrize(
    ("line", "key", "lane", "now", "model", "effort", "brief"),
    [
        ("R1907 (7:0x PM PT) orca-desk: launch alerts-api-1n91-fix NOW incident xhigh brief=/d/fix-brief.md", "R1907", "alerts-api-1n91-fix", " NOW", "incident", "xhigh", "/d/fix-brief.md"),
        ("- orca-desk: launch docs-lane astra high brief=/d/b.md", None, "docs-lane", None, "astra", "high", "/d/b.md"),
    ],
)
def test_the_launch_grammar_names_the_key_lane_route_and_brief(line, key, lane, now, model, effort, brief):
    directive = runner_module.INBOX_DIRECTIVE.match(line)
    spec = runner_module.LAUNCH_SPEC.match(directive["rest"])
    assert (directive["verb"], directive["key"], spec["lane"], spec["now"], spec["model"], spec["effort"], spec["brief"]) == ("launch", key, lane, now, model, effort, brief)


def test_an_inbox_launch_line_launches_once_and_now_marks_it_urgent(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    shell.launch_line = f"{LANE} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    orca_pass(shell, config)
    desk_inbox(tmp_path, f"R1907 (7:0x PM PT) orca-desk: launch {LANE} NOW opus xhigh brief={brief}")
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
        desk_inbox(tmp_path, f"R9001 orca-desk: launch {LANE} opus high brief={brief}")
    else:
        cli(shell, config, "launch", "--key", "R9001", "--lane", LANE, "--model", "opus", "--effort", "high", "--brief", str(brief))
    orca_pass(shell, config)
    orca_pass(shell, config)
    desk_inbox(tmp_path, f"R9004 orca-desk: relay to {LANE}: rebase onto dev")
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
    desk_inbox(tmp_path, f"R9010 orca-desk: relay to {LANE}: rebase onto dev")
    cli(shell, config, "relay", "--key", "R9011", "--lane", LANE, "--text", "then rerun CI")
    orca_pass(shell, config)
    assert [call[call.index("--to") + 1] for call in shell.sends()] == ["dispatch:ctx_h", "dispatch:ctx_h"]
    assert [line for line in escalations(shell) if line.startswith("RELAY")] == [f"RELAYED R9010 {LANE}: relay to dispatch ctx_h"]


def test_an_inbox_launch_under_a_key_already_held_launches_nothing_twice(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    orca_pass(shell, config)
    desk_inbox(tmp_path, f"R9 orca-desk: launch {LANE} sol xhigh brief={brief}", f"R9 orca-desk: launch {LANE} sol xhigh brief={brief}")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert len(launches(shell)) == 1
    assert [line for line in escalations(shell) if "LAUNCH-FAILED" in line] == [
        f"LAUNCH-FAILED R9 {LANE}: R9 already holds a launch for {LANE} (accepted); nothing was launched"
    ]


def test_an_inbox_launch_outside_the_grammar_or_route_fails_visibly_and_launches_nothing(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    orca_pass(shell, config)
    desk_inbox(
        tmp_path,
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
    desk_inbox(tmp_path, f"R10 orca-desk: launch {LANE} NOW sol xhigh brief={brief}")
    orca_pass(shell, config)
    assert launches(shell) == []
    [line] = escalations(shell)
    assert line == f"LAUNCH-FAILED R10 {LANE}: dispatch ctx_a is dispatched; relay to it instead; nothing was launched"


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


def test_a_desk_inbox_line_cci_imported_acts_once_from_the_file(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    line = f"- orca-desk: relay to {LANE}: rebase onto dev"
    desk_inbox(tmp_path, line)
    cci_go(shell, line, source=f"import:{(tmp_path / 'inbox/orca-desk.md').resolve()}")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert len(shell.sends()) == 1
    assert escalations(shell) == [f"RELAYED inbox@0 {LANE}: relay to dispatch ctx_a"]


def cci_record(shell: FakeShell, **fields) -> int:
    seq = 34766 + len(shell.records)
    shell.records.append({"seq": seq, "drive": "d1", "lane": "root", "kind": "go", "to": [], "source": "post", **fields})
    return seq


def test_a_withdraw_line_stops_a_held_launch_before_it_starts(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    shell.cpu_load = 140
    orca_pass(shell, config)
    desk_inbox(tmp_path, f"R20 orca-desk: launch {LANE} sol xhigh brief={brief}")
    orca_pass(shell, config)
    desk_inbox(tmp_path, "R21 (11:52 PM) orca-desk: withdraw R20")
    orca_pass(shell, config)
    shell.cpu_load = 1.0
    orca_pass(shell, config)
    assert launches(shell) == []
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R20"].status == "failed"
    assert [line for line in escalations(shell) if not line.startswith("LOAD-HIGH ")] == [f"WITHDRAWN R20 {LANE}: by R21; nothing was launched"]


def test_a_withdraw_of_a_started_launch_or_an_unknown_key_fails_visibly(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    shell.launch_line = f"{LANE} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    orca_pass(shell, config)
    desk_inbox(tmp_path, f"R20 orca-desk: launch {LANE} NOW incident xhigh brief={brief}")
    orca_pass(shell, config)
    desk_inbox(tmp_path, "R21 orca-desk: withdraw R20", "R22 orca-desk: withdraw R19", "R23 orca-desk: withdraw the duplicates")
    orca_pass(shell, config)
    assert len(launches(shell)) == 1
    assert [line for line in escalations(shell) if line.startswith("WITHDRAW")] == [
        f"WITHDRAW-FAILED R21 {LANE}: launch R20 is verified; nothing was withdrawn",
        "WITHDRAW-FAILED R22 R19: the runner holds no launch under R19; nothing was withdrawn",
        f"WITHDRAW-FAILED R23 inbox: one withdrawal per line, in the form `{runner_module.WITHDRAW_GRAMMAR}`; nothing was withdrawn",
    ]


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
    desk_inbox(tmp_path, "- orca-desk: alert dd-312516332 https://app.datadoghq.com/monitors/312516332 :: Datadog OK -> Alert Run assignment starved")
    orca_pass(shell, config)
    assert launches(shell) == []
    assert shell.entries == []
    [line] = escalations(shell)
    assert line.startswith("ALERT dd-312516332 ") and "no lane launched; the alerts desk writes `orca-desk: incident" in line


def test_an_incident_line_attaches_the_brief_to_the_briefs_log_and_launches_the_incident_fix_lane_once(shell, config, tmp_path):
    facts = tmp_path / "alert-facts.md"
    facts.write_text("apply authority: 0 deletes and 0 replaces while the alert is active\n")
    raw = json.loads(config.read_text())
    config.write_text(json.dumps({**raw, "alert": {"facts": str(facts)}}))
    shell.launch_line = "dd-312516332-fix ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    orca_pass(shell, config)
    desk_inbox(tmp_path, "- orca-desk: incident dd-312516332 https://app.datadoghq.com/monitors/312516332 reported=23:48 :: Datadog OK -> Alert Run assignment starved")
    orca_pass(shell, config)
    shell.launch("dd-312516332-fix", "ctx_n")
    orca_pass(shell, config)
    brief = shell.attachments["dd-312516332-fix.full.md"]
    assert brief.parent == tmp_path / "lfs"
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
    assert lines[1] == "LAUNCHED inbox@0 dd-312516332-fix: dispatch ctx_n terminal term_ctx_n"


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
    desk_inbox(tmp_path, ROOT_ALERT, f"R1068 (3:05 AM PT) orca-desk: launch alerts-api-0303-fix NOW incident xhigh brief={brief}")
    orca_pass(shell, config)
    launched = [call[1] for call in launches(shell)]
    assert ("alerts-api-0305-fix" not in launched) is skipped and "alerts-api-0303-fix" in launched
    assert any("LAUNCH-SKIPPED inbox@0 alerts-api-0305-fix: duplicate of alerts-api-0303-fix" in line for line in escalations(shell)) is skipped


def test_an_alert_defers_to_a_live_lane_on_the_same_monitor(shell, config, tmp_path):
    brief = root_brief(tmp_path, "327967001")
    orca_pass(shell, config)
    desk_inbox(tmp_path, f"R1068 orca-desk: launch alerts-api-0303-fix NOW incident xhigh brief={brief}")
    orca_pass(shell, config)
    shell.launch("alerts-api-0303-fix", "ctx_root")
    orca_pass(shell, config)
    shell.sleep(3600)
    desk_inbox(tmp_path, ROOT_ALERT)
    orca_pass(shell, config)
    assert [call[1] for call in launches(shell)] == ["alerts-api-0303-fix"]
    assert any("LAUNCH-SKIPPED" in line and "duplicate of alerts-api-0303-fix" in line for line in escalations(shell))


@pytest.mark.parametrize("reused", [False, True], ids=["finished", "reused-for-another-monitor"])
def test_an_alert_launches_when_the_lane_on_its_monitor_is_no_longer_on_it(shell, config, tmp_path, reused):
    brief = root_brief(tmp_path, "327967001")
    orca_pass(shell, config)
    desk_inbox(tmp_path, f"R1068 orca-desk: launch alerts-api-0303-fix NOW incident xhigh brief={brief}")
    orca_pass(shell, config)
    shell.launch("alerts-api-0303-fix", "ctx_root")
    orca_pass(shell, config)
    shell.dispatches["ctx_root"]["status"] = "completed"
    if reused:
        other = tmp_path / "incidents/alerts-api-0400/fix-brief.md"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text("# alerts-api-0400-fix\n\nccx: incident=328006761 role=fix\n")
        desk_inbox(tmp_path, f"R1070 orca-desk: launch alerts-api-0303-fix NOW incident xhigh brief={other}")
        orca_pass(shell, config)
        shell.launch("alerts-api-0303-fix", "ctx_reused")
        orca_pass(shell, config)
    desk_inbox(tmp_path, ROOT_ALERT)
    orca_pass(shell, config)
    assert "alerts-api-0305-fix" in [call[1] for call in launches(shell)]
    assert not any("LAUNCH-SKIPPED" in line for line in escalations(shell))


def test_an_alert_brief_names_its_monitor_for_later_dedupe(shell, config, tmp_path):
    orca_pass(shell, config)
    desk_inbox(tmp_path, ROOT_ALERT)
    orca_pass(shell, config)
    assert "ccx: lane=alerts-api-0305-fix role=incident effort=xhigh incident=327967001\n" in shell.attachments["alerts-api-0305-fix.full.md"].read_text()


def test_a_repeat_alert_relays_to_the_live_fix_lane_and_keeps_its_brief(shell, config, tmp_path):
    brief = tmp_path / "lfs/root-edited"
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text("root-edited brief")
    shell.attachments["alerts-runs-1704-fix.full.md"] = brief
    shell.launch("alerts-runs-1704-fix", "ctx_a")
    orca_pass(shell, config)
    desk_inbox(tmp_path, "orca-desk: alert alerts-runs-1704 https://in-the-forge.slack.com/archives/C098XDNJJR3/p1791072242613549 :: Page #11507 SoFi Disputes stuck-case monitor stopped reporting")
    orca_pass(shell, config)
    assert launches(shell) == []
    [send] = shell.sends()
    assert "The alert fired again at" in send[send.index("--body") + 1]
    assert brief.read_text() == "root-edited brief" and shell.attachments["alerts-runs-1704-fix.full.md"] == brief and shell.entries == []
    [line] = escalations(shell)
    assert line == "RELAYED inbox@0:again alerts-runs-1704-fix: relay to dispatch ctx_a"


def test_an_alert_outside_the_grammar_fails_visibly_and_launches_nothing(shell, config, tmp_path):
    orca_pass(shell, config)
    desk_inbox(tmp_path, "R12 orca-desk: alert Run assignment starved, please look")
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
    desk_inbox(tmp_path, "R40 orca-desk: incident dd-7 https://app.datadoghq.com/monitors/7 reported=11:02 :: Datadog OK -> Alert again")
    orca_pass(shell, config)
    brief = shell.attachments["dd-7-fix.full.md"]
    assert [call[1:] for call in launches(shell)] == [["dd-7-fix", "incident", "xhigh", str(brief)]]
    assert brief != last and "Datadog OK -> Alert again" in brief.read_text()


def test_an_incident_whose_brief_fails_to_attach_launches_nothing(shell, config, tmp_path):
    shell.attach_error = "error: cc-notes: ref lock held"
    orca_pass(shell, config)
    desk_inbox(tmp_path, "R41 orca-desk: incident dd-8 https://app.datadoghq.com/monitors/8 reported=11:02 :: Datadog OK -> Alert")
    orca_pass(shell, config)
    assert launches(shell) == [] and "dd-8-fix.full.md" not in shell.attachments
    [line] = escalations(shell)
    assert line.startswith("INCIDENT dd-8 ") and line.endswith("| dd-8-fix not launched: the brief did not attach: error: cc-notes: ref lock held")


def test_an_incident_whose_attach_dies_silently_launches_nothing(shell, config, tmp_path):
    shell.attach_code = -9
    orca_pass(shell, config)
    desk_inbox(tmp_path, "R42 orca-desk: incident dd-9 https://app.datadoghq.com/monitors/9 reported=11:02 :: Datadog OK -> Alert")
    orca_pass(shell, config)
    assert launches(shell) == []
    [line] = escalations(shell)
    assert line.endswith("| dd-9-fix not launched: the brief did not attach: ccn exited -9")


def test_an_incident_whose_attached_brief_has_no_path_launches_nothing(shell, config, tmp_path):
    shell.unresolved.add("dd-10-fix.full.md")
    orca_pass(shell, config)
    desk_inbox(tmp_path, "R43 orca-desk: incident dd-10 https://app.datadoghq.com/monitors/10 reported=11:02 :: Datadog OK -> Alert")
    orca_pass(shell, config)
    assert launches(shell) == []
    [line] = escalations(shell)
    assert line.endswith("| dd-10-fix not launched: the attached brief has no path")


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
    fallback = shell.attachments[f"{backup}.full.md"]
    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, "incident", "xhigh", str(brief)], [str(runner_module.LAUNCHER), backup, "opus", "xhigh", str(fallback)]]
    assert shell.spawned_env["orca-launch.sh"]["ORCA_LAUNCH_CLAUDE_ARGS"] == runner_module.CLAUDE_FAST_MODE
    launch = json.loads(incident(tmp_path, f"desk-lane-{backup}").actions[f"incident-clock:ctx_{LANE}"].target)
    assert (launch["urgent"], launch["fast"]) == (True, True)
    text = fallback.read_text()
    assert text.startswith(f"ccx: lane={backup} role=incident effort=xhigh\n") and text.endswith(brief.read_text())
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


@pytest.mark.parametrize("source", ["status", "cci", "base-name"])
def test_a_mechanism_from_the_run_or_from_cci_stops_the_mechanism_threshold(shell, config, tmp_path, capsys, source):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(4 * 60)
    if source == "status":
        shell.receive({"subject": "mechanism: wrong selector", "from_handle": f"term_ctx_{LANE}"})
    elif source == "base-name":
        lane_record(shell, LANE.removesuffix("-fix"), "incident", text="MECHANISM d121062c confirmed")
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


def test_a_mechanism_with_no_pr_launches_the_fallback_at_the_pr_threshold(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    shell.sleep(4 * 60)
    lane_record(shell, LANE, "mechanism")
    shell.sleep(11 * 60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    fallback = shell.attachments[f"{LANE}-opus.full.md"]
    assert clock_lines(shell) == [
        f"INCIDENT-CLOCK {LANE} pr: no PR 15 min after its launch at 11:00, limit 15 min; launch to mechanism 4 min, mechanism to now 11 min | the Opus fallback {LANE}-opus is launching on opus xhigh in fast mode, brief {fallback}"
    ]
    assert [call[1] for call in launches(shell)] == [LANE, f"{LANE}-opus"] and "had posted no PR" in fallback.read_text()


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
    clocked_lane(shell, config, tmp_path, lane=backup, model="opus", header="ccx: role=incident\n")
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
    ("opus", "", False),
    ("opus", "ccx: incident=none role=fix\n", False),
    ("incident", "ccx: role=evidence\n", False),
    ("opus", "ccx: incident=327967001 role=retro\n", False),
    ("opus", "ccx: role=incident\n", True),
    ("sol", "ccx: incident=327967001 role=fix\n", True),
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
    fallback = shell.attachments[f"{backup}.full.md"]
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
    fallback = shell.attachments[f"{backup}.full.md"]
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


@pytest.mark.parametrize("kind", ["ready", "fix-live", "worker_done"])
def test_each_case_an_incident_lane_reports_unfixed_launches_an_incident_lane_in_the_same_pass(shell, config, tmp_path, kind):
    brief = clocked_lane(shell, config, tmp_path)
    shell.sleep(60)
    lane_record(shell, LANE, "opened", prs=[4250])
    lane_record(shell, LANE, "opened", prs=[4242, "yasyf/cc-skills#77", "4250"])
    unfixed_report(shell, LANE, kind, *CASES)
    orca_pass(shell, config)
    lanes = [f"{LANE}-case1", f"{LANE}-case2"]
    briefs = [shell.attachments[f"{lane}.full.md"] for lane in lanes]
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
    unfixed_report(shell, LANE, "worker_done", "None.")
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
    unfixed_report(shell, LANE, "worker_done", *CASES)
    orca_pass(shell, config)
    assert [call[1] for call in launches(shell)] == [LANE, f"{LANE}-case1", f"{LANE}-case2"]
    assert incident(tmp_path, f"desk-lane-{LANE}").facts["unfixed-cases"] == list(CASES)
    assert len(incident_lines(shell)) == 2


@pytest.mark.parametrize(("model", "header"), [("opus", ""), ("incident", "ccx: role=evidence\n")])
def test_an_unfixed_line_from_a_lane_that_is_no_incident_fix_lane_launches_nothing(shell, config, tmp_path, model, header):
    clocked_lane(shell, config, tmp_path, model=model, header=header)
    shell.launch("by-hand", "ctx_by-hand")
    unfixed_report(shell, LANE, "worker_done", *CASES)
    unfixed_report(shell, "by-hand", "worker_done", *CASES)
    orca_pass(shell, config)
    assert len(launches(shell)) == 1 and shell.entries == [] and incident_lines(shell) == []


def test_a_case_lanes_own_unfixed_cases_launch_beside_it_and_only_the_new_one(shell, config, tmp_path):
    clocked_lane(shell, config, tmp_path)
    unfixed_report(shell, LANE, "ready", CASES[0])
    orca_pass(shell, config)
    case = f"{LANE}-case1"
    shell.launch(case, f"ctx_{case}")
    orca_pass(shell, config)
    unfixed_report(shell, case, "worker_done", *CASES)
    orca_pass(shell, config)
    assert [call[1] for call in launches(shell)] == [LANE, case, f"{LANE}-case2"]
    assert f"`{case}` works from the brief at {shell.attachments[f'{case}.full.md']}." in shell.attachments[f"{LANE}-case2.full.md"].read_text()
    assert [line.split(" | ")[1].split(" launching")[0] for line in incident_lines(shell, case)] == [f"case lane {LANE}-case2"]
    assert incident(tmp_path, f"desk-lane-{LANE}").facts["unfixed-cases"] == list(CASES)


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
    unfixed_report(shell, LANE, "worker_done", CASES[0])
    orca_pass(shell, config)
    assert [call[1] for call in launches(shell)] == [LANE, f"{LANE}-case1"]
    assert incident(tmp_path, f"desk-lane-{LANE}").facts["unfixed-cases"] == [CASES[0]]


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


def test_an_incident_line_without_the_report_time_launches_nothing(shell, config, tmp_path):
    orca_pass(shell, config)
    desk_inbox(tmp_path, "R44 orca-desk: incident dd-11 https://app.datadoghq.com/monitors/11 :: Datadog OK -> Alert")
    orca_pass(shell, config)
    assert launches(shell) == []
    [line] = escalations(shell)
    assert line == f"INCIDENT-FAILED R44 inbox: one incident per line, in the form `{runner_module.INCIDENT_GRAMMAR}`; nothing was launched"


STANDING_ORDERS = """Standing orders, fixed in this template:
  - Reported {} PT. The bar is fixed and live within 20 minutes of the
    report, by {} PT; the desk's limits below run from your launch, inside it.
  - "stop asking people for things you can collect yourself": never ask a reporter
    for a version, id, or log the platform records; read it from telemetry first.
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


@pytest.mark.parametrize("template", [runner_module.ALERT_TEMPLATE, runner_module.UNFIXED_TEMPLATE])
def test_every_desk_incident_brief_template_names_the_component_that_served_the_failing_request(template):
    text = " ".join(template.read_text().split())
    assert "which component served the failing request: a cloud run is served by the cloud, never by their machine" in text


def test_the_alert_fix_brief_demands_the_reporters_own_case_and_the_unfixed_field(shell, config, tmp_path):
    orca_pass(shell, config)
    desk_inbox(tmp_path, ROOT_ALERT)
    orca_pass(shell, config)
    text = shell.attachments["alerts-api-0305-fix.full.md"].read_text()
    assert "Before READY, reproduce the reporter's own case" in text
    assert "A fix proven only on a synthetic or neighbouring case is not" in text
    assert "`UNFIXED alerts-api-0305-fix: none`" in text and "{" not in text


def test_the_unfixed_grammar_reads_bullets_and_code_ticks_and_names_each_of_the_lanes_cases_once_as_written():
    body = "done.\nUNFIXED a-fix: one\n- UNFIXED a-fix:   two  words \n> `UNFIXED a-fix: three`\nUNFIXED a-fix: one\nUNFIXED a-fix: two words\nUNFIXED a-fix: none\nsee UNFIXED a-fix: four\nUNFIXED b-fix: five\n"
    assert runner_module.unfixed_cases(body, "a-fix") == {"one": "one", "two words": "two  words", "three": "three"}


def test_an_urgent_hold_older_than_fifteen_minutes_escalates_one_decide_line(shell, config, tmp_path):
    orca_pass(shell, config)
    desk_inbox(tmp_path, "R50 (3:50 PM PT) orca-desk: hold api-catchup owner=merge-walker-r2 :: api, runtime-v2 and restate-worker wait on the G327 move; executor already shipped ahead of them")
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
    desk_inbox(tmp_path, "orca-desk: hold api-catchup owner=walker :: api waits on the move", "orca-desk: hold api-catchup owner=walker :: a repeat keeps the first clock")
    orca_pass(shell, config)
    shell.sleep(10 * 60)
    desk_inbox(tmp_path, "orca-desk: unhold api-catchup")
    orca_pass(shell, config)
    shell.sleep(10 * 60)
    desk_inbox(tmp_path, "orca-desk: hold api-catchup owner=walker :: held again")
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
    desk_inbox(tmp_path, "R51 orca-desk: hold the api chain until the move lands", "R52 orca-desk: unhold the api chain")
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
SPRITE_KEPT = f"kept {LANE}.json and {LANE}.worker.err, sent nothing more, and cleaned up nothing"
PREPARED, ATTACHED, ATTACH_FAILED = {"prepare.status": "0\n"}, {"attach.status": "0\n"}, {"attach.status": "1\n"}


@pytest.mark.parametrize("header", ["ccx: role=build cpu=high", "ccx: role=research place=remote", "ccx: place=remote role=build cpu=high"])
def test_a_remote_placed_lane_launches_on_a_sprite_whatever_the_mac_load(shell, config, tmp_path, header):
    with_sprites(config)
    brief = lane_brief(tmp_path, header)
    shell.cpu_load = 140
    shell.sprite_line = SPRITE_READY
    shell.sprite_receipts = {**PREPARED, **ATTACHED, f"{LANE}.json": SPRITE_STARTED}
    shell.dispatches["ctx_s"] = {"status": "dispatched", "terminal": "term_s"}
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "opus", "--effort", "xhigh", "--brief", str(brief))
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert sprite_launches(shell) == [[LAUNCHER, LANE, "claude-opus-5-5", "xhigh", str(brief)]]
    assert launches(shell) == []
    assert not [call for call in shell.calls if call[0] == "sprite"]
    env = shell.spawned_env["worker-launch.sh"]
    assert (env["WORKER_RUN"], env["WORKER_REF"], env["WORKER_REMOTE_ATTACH"]) == ("run_1", "dev", str(runner_module.SPRITE_ATTACH))
    assert runner_module.SPRITE_ATTACH.is_file()
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
ATTACH_REFUSED = (
    f"orca-remote-attach.sh exited 1: {LANE} failed status: environment sprite-a exited 1 with an error, not reachable runtime rt-1 with orchestration.contract.v1 and orchestration.federation.v1",
    {**PREPARED, **ATTACH_FAILED},
)
OUTCOME_UNKNOWN = (
    f"orca-remote-attach.sh exited 1: {LANE} failed worker-start exited 1 state=outcome_unknown task=task_s dispatch=ctx_s; {SPRITE_KEPT}",
    {**PREPARED, **ATTACH_FAILED, f"{LANE}.json": SPRITE_UNKNOWN},
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
        (*ATTACH_REFUSED, False),
        (*ATTACH_REFUSED, True),
    ],
    ids=["nothing-prepared", "prepare-unrecorded", "prepare-failed", "prepare-failed-restarted", "provider-refused", "provider-refused-restarted", "attach-refused", "attach-refused-restarted"],
)
def test_a_sprite_launch_that_stopped_before_worker_start_starts_the_same_launch_locally(shell, config, tmp_path, printed, receipts, restarted):
    with_sprites(config)
    brief = lane_brief(tmp_path, "ccx: role=build cpu=high")
    shell.sprite_line = f"{printed}\n"
    shell.sprite_receipts = receipts
    shell.launch_line = f"{LANE} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "sol", "--effort", "xhigh", "--brief", str(brief))
    sprite_passes(shell, config, tmp_path, restarted)
    assert sprite_launches(shell) == [[LAUNCHER, LANE, "gpt-6.1-sol", "xhigh", str(brief)]]
    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, "sol", "xhigh", str(brief)]]
    assert f"SPRITE-FALLBACK desk-lane-{LANE}/R638:sprite {LANE}: R638 launches locally: {printed}" in escalations(shell)
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "verified"


@pytest.mark.parametrize(
    ("printed", "receipts", "named", "restarted"),
    [
        (*OUTCOME_UNKNOWN, " as task=task_s dispatch=ctx_s", False),
        (*OUTCOME_UNKNOWN, " as task=task_s dispatch=ctx_s", True),
        (f"orca-remote-attach.sh exited 1: {LANE} failed worker-start exited 143; {SPRITE_KEPT}", {**PREPARED, **ATTACH_FAILED, f"{LANE}.json": '{"ok": true, "result": {"taskId": "task_s"'}, "", False),
        (
            f"orca-remote-attach.sh exited 1: {LANE} failed worker-start exited 1 code=invalid_argument; {SPRITE_KEPT}",
            {**PREPARED, **ATTACH_FAILED, f"{LANE}.json": json.dumps({"ok": False, "error": {"code": "invalid_argument", "message": "sk-synthetic-credential-0000"}})},
            "",
            False,
        ),
        (
            f"orca-remote-attach.sh exited 1: {LANE} failed worker-start returned no printable task and dispatch IDs, not a matching ready receipt; {SPRITE_KEPT}",
            {**PREPARED, **ATTACH_FAILED, f"{LANE}.json": json.dumps({"ok": True, "result": {"state": "ready", "taskId": "task s\nUNOWNED", "dispatchId": "ctx_s"}})},
            " as dispatch=ctx_s",
            False,
        ),
        (json.dumps({"state": "ready", "task": "task_s", "dispatch": "ctx_s"}), {**PREPARED, **ATTACHED, f"{LANE}.json": SPRITE_STARTED}, " as task=task_s dispatch=ctx_s", False),
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
    brief = lane_brief(tmp_path, "ccx: role=build cpu=high")
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
    brief = lane_brief(tmp_path, "ccx: role=build cpu=high")
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "sol", "--effort", "xhigh", "--brief", str(brief))
    for _ in range(3):
        orca_pass(shell, config)
    assert sprite_launches(shell) == [[LAUNCHER, LANE, "gpt-6.1-sol", "xhigh", str(brief)]]
    assert launches(shell) == []
    assert escalations(shell) == [f"UNVERIFIABLE desk-lane-{LANE}/R638 {LANE}: launch outcome unknown: no launch line and no new receipt; it was not relaunched"]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "unverifiable"


@pytest.mark.parametrize("header", ["ccx: role=build cpu=high", "ccx: role=research place=remote"])
def test_a_lane_name_a_sprite_refuses_launches_locally(shell, config, tmp_path, header):
    with_sprites(config)
    brief = lane_brief(tmp_path, header)
    lane = "api.build"
    shell.launch_line = f"{lane} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    cli(shell, config, "launch", "--key", "R638", "--lane", lane, "--model", "opus", "--effort", "xhigh", "--brief", str(brief))
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert sprite_launches(shell) == []
    assert launches(shell) == [[str(runner_module.LAUNCHER), lane, "opus", "xhigh", str(brief)]]
    assert f"SPRITE-FALLBACK desk-lane-{lane}/R638:sprite {lane}: R638 launches locally: lane {lane} is not a Sprite name of up to 55 lowercase letters, digits, and dashes" in escalations(shell)
    assert incident(tmp_path, f"desk-lane-{lane}").actions["R638"].status == "verified"


@pytest.mark.parametrize(
    ("header", "model"),
    [
        ("ccx: role=build", "opus"),
        ("ccx: role=build cpu=highest", "opus"),
        ("ccx: role=build place=remotely", "opus"),
        ("ccx: role=build\nplace=remote", "opus"),
        ("ccx: role=build cpu=high place=local", "opus"),
        ("ccx: role=build cpu=high", "fable"),
        ("ccx: role=build place=remote", "fable"),
        ("ccx: role=fix cpu=high", "incident"),
        ("ccx: role=fix place=remote", "incident"),
        ("ccx: role=desk place=remote", "opus"),
        ("ccx: role=watch cpu=high place=remote", "sonnet"),
    ],
)
def test_a_lane_placed_or_forced_local_never_leaves_the_mac(shell, config, tmp_path, header, model):
    with_sprites(config)
    brief = lane_brief(tmp_path, header)
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", model, "--effort", "xhigh", "--brief", str(brief), "--owner-directed")
    orca_pass(shell, config)
    assert sprite_launches(shell) == []
    assert launches(shell) == [[str(runner_module.LAUNCHER), LANE, model, "xhigh", str(brief)]]
    assert not [call for call in shell.calls if call[0] == "sprite"]


@pytest.mark.parametrize("header", ["ccx: role=build", "ccx: role=build cpu=high place=local"])
def test_a_local_launch_beside_a_sprite_config_still_waits_on_load(shell, config, tmp_path, header):
    with_sprites(config)
    brief = lane_brief(tmp_path, header)
    shell.cpu_load = 40
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "opus", "--effort", "xhigh", "--brief", str(brief))
    orca_pass(shell, config)
    assert launches(shell) == [] and sprite_launches(shell) == []
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "accepted"


def with_status(config: Path) -> None:
    raw = json.loads(config.read_text())
    config.write_text(json.dumps({**raw, "status": {"channel": "#epd-sprint"}}))


def status_posts(shell: FakeShell) -> list[dict]:
    return [post for post in shell.posts if post["text"].startswith("STATUS-DUE ")]


STATUS_ASK = "comms-desk: post the delta since then now, under the channel's standing grant, with no approval. root: answer it if it asks for facts"


def test_a_drive_with_no_status_channel_is_never_told_a_status_is_due(shell, config, tmp_path):
    orca_pass(shell, config)
    shell.sleep(3 * 60 * 60)
    orca_pass(shell, config)
    assert status_posts(shell) == []


def test_an_hour_with_no_status_post_tells_the_root_and_the_comms_desk_once_per_hour(shell, config, tmp_path):
    with_status(config)
    orca_pass(shell, config)
    shell.sleep(59 * 60)
    orca_pass(shell, config)
    assert status_posts(shell) == []
    shell.sleep(60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    [post] = status_posts(shell)
    assert (post["kind"], post["to"], post["topic"], post["id"]) == ("blocker", "root,comms-desk", "status", "status-due:2026-10-01T18:00:00Z")
    assert post["text"] == f"STATUS-DUE status runner: 60 min with no status post in #epd-sprint; none since the runner started watching at 11:00. {STATUS_ASK}"
    shell.sleep(59 * 60)
    orca_pass(shell, config)
    assert len(status_posts(shell)) == 1
    shell.sleep(60)
    orca_pass(shell, config)
    assert [post["text"].split(";")[0] for post in status_posts(shell)] == [
        "STATUS-DUE status runner: 60 min with no status post in #epd-sprint",
        "STATUS-DUE status runner: 120 min with no status post in #epd-sprint",
    ]


def test_a_status_post_restarts_the_hour_and_the_next_blocker_names_it(shell, config, tmp_path):
    with_status(config)
    orca_pass(shell, config)
    shell.sleep(30 * 60)
    seq = cci_record(shell, lane="comms-desk", kind="posted", topic="status", text="https://forge.slack.com/archives/C1/p1 status at 11:30", at=actions.stamp(shell.clock))
    cci_record(shell, lane="comms-desk", kind="posted", topic="#36883", text="https://forge.slack.com/archives/C1/p2 #36883 landed", at=actions.stamp(shell.clock + timedelta(minutes=5)))
    orca_pass(shell, config)
    shell.sleep(30 * 60)
    orca_pass(shell, config)
    assert status_posts(shell) == []
    shell.sleep(30 * 60)
    orca_pass(shell, config)
    [post] = status_posts(shell)
    assert post["text"] == f"STATUS-DUE status runner: 60 min with no status post in #epd-sprint; the last went out at 11:30, cci #{seq}. {STATUS_ASK}"


def test_a_status_blocker_posted_late_still_opens_the_next_window_at_its_due_time(shell, config, tmp_path):
    with_status(config)
    orca_pass(shell, config)
    shell.sleep(60 * 60)
    shell.post_error = "cci: daemon restarting"
    orca_pass(shell, config)
    shell.post_error = ""
    shell.sleep(50 * 60)
    orca_pass(shell, config)
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    assert [post["id"] for post in status_posts(shell)] == ["status-due:2026-10-01T18:00:00Z", "status-due:2026-10-01T19:00:00Z"]


def test_a_status_blocker_cci_refused_is_sent_again_as_the_same_record(shell, config, tmp_path):
    with_status(config)
    orca_pass(shell, config)
    shell.sleep(60 * 60)
    shell.post_error = "cci: daemon restarting"
    orca_pass(shell, config)
    assert status_posts(shell) == []
    shell.post_error = ""
    shell.sleep(60)
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert [post["id"] for post in status_posts(shell)] == ["status-due:2026-10-01T18:00:00Z"]
