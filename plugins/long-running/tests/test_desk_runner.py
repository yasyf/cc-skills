"""desk-runner.py against a fake Orca Run, ledger, stack-enqueue, and judge, on a fake clock: every side effect is recorded on ``shell.calls``."""

from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import actions
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/desk-runner.py"
spec = importlib.util.spec_from_file_location("desk_runner", SCRIPT)
runner_module = importlib.util.module_from_spec(spec)
sys.modules["desk_runner"] = runner_module
spec.loader.exec_module(runner_module)

LANE = "incident-fix"
FORBIDDEN = {"worker-release", "kill", "pkill", "close", "stop", "terminate", "worker-stop"}


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
        self.clock = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
        self.calls: list[list[str]] = []
        self.dispatches: dict[str, dict] = {}
        self.mailbox: list[dict] = []
        self.inbox_error = None
        self.inboxes: dict[str, list[dict]] = {}
        self.questions: list[dict] = []
        self.lose_sends = 0
        self.garble_sends = 0
        self.requests: dict[str, str] = {}
        self.stale_out = ""
        self.gates: dict[str, list[str]] = {}
        self.enqueue_out: dict[str, tuple[int, str]] = {}
        self.statuses = ""
        self.rows: list[dict] = []
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
        self.judge_process: Running | None = None

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
        out.parent.mkdir(parents=True, exist_ok=True)
        if argv[0] == "claude" and self.judge_process:
            out.write_text("")
            return self.judge_process
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
        if name == "stack-enqueue":
            return self.stack_enqueue(argv[1:])
        if len(argv) > 1 and Path(argv[1]).name == "ledger.py":
            verb = argv[4]
            assert env == {"CLAUDE_LONG_RUNNING_DRIVE": "d1"}
            return runner_module.Done(0, json.dumps([{"ours": row.get("state", "open") == "open", **row} for row in self.rows]) if verb == "list" else f"{verb} ok\n", "")
        if argv[:2] == ["cci", "tail"]:
            return self.cci_tail(argv)
        if argv[:2] == ["cci", "post"]:
            flags = dict(zip(argv[2::2], argv[3::2]))
            assert flags["--drive"] == "d1" and flags["--lane"] == "desk-runner" and len(flags["--text"]) <= 400
            self.posts.append({key.lstrip("-"): value for key, value in flags.items()})
            return runner_module.Done(0, f"#{len(self.posts)}\n", "")
        raise AssertionError(f"unexpected call {argv}")

    def cci_tail(self, argv: list[str]) -> runner_module.Done:
        if self.tail_error:
            return runner_module.Done(1, "", self.tail_error)
        assert argv[argv.index("--drive") + 1] == "d1" and "--json" in argv and "--cursor" not in argv
        since = int(argv[argv.index("--since") + 1])
        found = [record for record in self.records if record["seq"] > since and ("--to" not in argv or argv[argv.index("--to") + 1] in record["to"])]
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
            return self.ok({"message": {"id": self.next_id("msg")}})
        if verb == ["orchestration", "request-show"]:
            return self.ok({"state": self.requests.get(argv[3], "absent")})
        if verb == ["orchestration", "check"]:
            assert argv[2] == "--terminal"
            if argv[3].startswith("run:"):
                assert argv[3:] == ["run:run_1", "--all", "--types", "question"]
                return self.ok({"messages": self.questions})
            return self.ok({"messages": self.inboxes.get(argv[3], [])})
        if verb == ["orchestration", "inbox"]:
            assert argv[2:4] == ["--terminal", "run:run_1"]
            if self.inbox_error:
                return self.inbox_error
            messages = sorted(self.mailbox, key=lambda message: message["sequence"], reverse=True)[:int(argv[5])]
            return self.ok({"messages": messages, "count": len(messages)})
        if verb == ["terminal", "send"]:
            return self.ok({})
        raise AssertionError(f"unexpected orca call {argv}")

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
        tip = argv[0]
        if "--check" in argv:
            outs = self.gates[tip]
            return runner_module.Done(0, outs.pop(0) if len(outs) > 1 else outs[0], "")
        return runner_module.Done(*self.enqueue_out[tip], "")

    def sends(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["orca", "orchestration", "send"]]

    def enqueues(self) -> list[list[str]]:
        return [call for call in self.calls if Path(call[0]).name == "stack-enqueue" and "--check" not in call and "--status" not in call]

    def launch(self, lane: str, dispatch: str, status: str = "dispatched") -> None:
        receipts = self.root / "receipts"
        receipts.mkdir(exist_ok=True)
        terminal = f"term_{dispatch}"
        (receipts / f"{lane}.json").write_text(json.dumps({"result": {"taskId": "task_1", "dispatchId": dispatch}}))
        (receipts / f"{lane}.terminal").write_text(terminal + "\n")
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
def shell(tmp_path: Path) -> FakeShell:
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
                "orca": {"run": "run_1", "receipts": str(tmp_path / "receipts"), "desk_inbox": str(tmp_path / "inbox/orca-desk.md"), "briefs": {"repo": str(tmp_path / "checkout"), "log": "briefs1"}},
                "landing": {
                    "repo": "Forge-AI/monorepo",
                    "ledger": "abc",
                    "checkout": str(tmp_path / "checkout"),
                    "holds": str(tmp_path / "holds.md"),
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


def test_a_lost_send_with_no_receipt_is_unverifiable_and_never_resent(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.lose_sends = 1
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    for _ in range(3):
        orca_pass(shell, config)
    assert len(shell.sends()) == 1
    sends = [action for action in incident(tmp_path, f"desk-lane-{LANE}").actions.values() if action.kind == "send"]
    assert [action.status for action in sends] == ["unverifiable"]
    lines = escalations(shell)
    assert len(lines) == 1 and "UNVERIFIABLE" in lines[0] and "not resent" in lines[0]


def test_a_lost_send_orca_recorded_replays_its_receipt_once(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.lose_sends = 1
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    request = next(iter(action.response["request_id"] for action in incident(tmp_path, f"desk-lane-{LANE}").actions.values() if action.kind == "send"))
    shell.requests[request] = "completed"
    orca_pass(shell, config)
    orca_pass(shell, config)
    sends = shell.sends()
    assert len(sends) == 2 and sends[1][sends[1].index("--retry-request") + 1] == request
    assert [action.status for action in incident(tmp_path, f"desk-lane-{LANE}").actions.values() if action.kind == "send"] == ["completed"]


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
    assert launches == [[str(runner_module.SCRIPTS / "orca-launch.sh"), LANE, "sol", "xhigh", str(brief)]]
    container = incident(tmp_path, f"desk-lane-{LANE}")
    assert container.actions["R638"].status == "verified"
    assert [call[call.index("--to") + 1] for call in shell.sends()] == ["dispatch:ctx_n"]


def launches(shell: FakeShell) -> list[list[str]]:
    return [call for call in shell.calls if Path(call[0]).name == "orca-launch.sh"]


@pytest.mark.parametrize(("model", "flags"), [("incident", ()), ("opus", ("--owner-directed",))])
def test_an_incident_or_owner_directed_launch_never_waits_on_load(shell, config, tmp_path, model, flags):
    brief = tmp_path / "fix.md"
    brief.write_text("brief")
    shell.cpu_load = 140
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", model, "--effort", "xhigh", "--brief", str(brief), *flags)
    orca_pass(shell, config)
    assert launches(shell) == [[str(runner_module.SCRIPTS / "orca-launch.sh"), LANE, model, "xhigh", str(brief)]]


def test_a_launch_held_by_load_starts_nothing_then_fails_loudly_at_the_hold_deadline(shell, config, tmp_path):
    brief = tmp_path / "fix.md"
    brief.write_text("brief")
    shell.cpu_load = 40
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "opus", "--effort", "xhigh", "--brief", str(brief))
    for _ in range(4):
        orca_pass(shell, config)
        shell.sleep(60)
    assert launches(shell) == [] and escalations(shell) == []
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "accepted"
    for _ in range(3):
        shell.sleep(60)
        orca_pass(shell, config)
    assert launches(shell) == []
    action = incident(tmp_path, f"desk-lane-{LANE}").actions["R638"]
    assert action.status == "failed" and "load 40 above 8 cores" in action.reason
    [line] = escalations(shell)
    assert "LAUNCH-HELD" in line and "R638 launch failed after 5m held" in line
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
    shell.enqueue_out["29016"] = (0, "enqueue #28997 #29016: {}\n#28997 QUEUED aaaa111111 graphite QUEUED_TO_MERGE\n")
    shell.gates["29016"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", would="#28997 #29016")]
    landing_pass(shell, config)
    assert cli(shell, config, "policy", "--key", "L260", "--landing", "whole", "--revision", "e802ae614e", "--source", "sole checkout") == 0
    lines = escalations(shell)
    assert len(lines) == 1 and "STALE-POLICY L260" in lines[0] and "prefix at a1b2c3d4e5" in lines[0]
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert [call[1:] for call in shell.enqueues()] == [["29016"]]
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


def test_a_superseding_policy_naming_the_accepted_revision_applies(shell, config, tmp_path):
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020")]
    shell.enqueue_out["29020"] = (0, "enqueue #28997 #29016 #29020: {}\n")
    landing_pass(shell, config)
    cli(shell, config, "policy", "--key", "L300", "--landing", "whole", "--revision", "f9e8d7c6b5", "--supersedes", "a1b2c3d4e5", "--source", "owner ruling")
    assert escalations(shell) == []
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111112 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020")]
    landing_pass(shell, config)
    assert "--whole" in shell.enqueues()[-1]



def test_an_approval_that_arrived_before_the_send_is_never_asked_for(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 not approved by poetic-svc"), gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
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
    assert all(call[call.index("--hold") + 1 :] == ["28349", "29020"] for call in checks)
    assert shell.sends() == [] and escalations(shell) == []


def test_a_rules_blocked_pr_reaches_stack_enqueue_as_a_hold(shell, config, tmp_path):
    shell.rows = [
        {"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333", "rules_blocked": True},
        {"pr": "28349", "lane": "d4", "branch": "d/1", "base": "dev", "head": "dddd444444", "state": "open", "reported_head": "dddd444444", "rules_blocked": False},
    ]
    shell.gates["29020"] = [gate("#29020 BLOCKED cccc333333 held")]
    shell.gates["28349"] = [gate("#28349 GREEN dddd444444 graphite READY", would="#28349")]
    shell.enqueue_out["28349"] = (0, "enqueue #28349\n")
    landing_pass(shell, config)
    calls = [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]
    assert shell.enqueues() and all(call[call.index("--hold") + 1 :] == ["29020"] for call in calls)


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
    shell.enqueue_out["29020"] = (0, "enqueue #29020\n")
    shell.enqueue_out["28400"] = (0, "enqueue #28400\n")
    landing_pass(shell, config)
    calls = [call for call in shell.calls if Path(call[0]).name == "stack-enqueue"]
    assert calls and all(call[call.index("--hold") + 1 :] == ["28349"] for call in calls)
    assert sorted(call[1] for call in shell.enqueues()) == ["28400", "29020"]
    assert shell.sends() == [] and escalations(shell) == []


def test_a_release_line_without_hold_all_holds_nothing(shell, config, tmp_path):
    (tmp_path / "holds.md").write_text("release #29020 owner reviewed\n")
    shell.rows = [{"pr": "29020", "lane": LANE, "branch": "a/3", "base": "dev", "head": "cccc333333", "state": "open", "reported_head": "cccc333333"}]
    shell.gates["29020"] = [gate("#29020 GREEN cccc333333 graphite READY", would="#29020")]
    shell.enqueue_out["29020"] = (0, "enqueue #29020\n")
    landing_pass(shell, config)
    assert all("--hold" not in call for call in shell.calls if Path(call[0]).name == "stack-enqueue")
    assert len(shell.enqueues()) == 1


def test_an_enqueue_whose_response_was_lost_settles_from_graphite_status(shell, config, tmp_path):
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", would="#28997")]
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


def test_the_view_renders_pacific_times_from_receipts(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    orca_pass(shell, config)
    view = (tmp_path / "desk-runner.md").read_text()
    assert "R625 relay [accepted] accepted 11:00" in view
    assert "Z" not in view.split("\n", 1)[1].replace("desk-lane", "")


def test_a_send_with_no_parseable_reply_is_unverifiable_not_retried(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.garble_sends = 1
    cli(shell, config, "relay", "--key", "R625", "--lane", LANE, "--text", "rebase onto dev")
    for _ in range(3):
        orca_pass(shell, config)
    assert len(shell.sends()) == 1
    assert "UNVERIFIABLE" in escalations(shell)[0]


def test_an_undeliverable_reply_reaches_its_deadline(shell, config, tmp_path):
    cli(shell, config, "relay", "--key", "R633", "--lane", LANE, "--text", "use evidence.md", "--reply-to", "msg_q9")
    for _ in range(12):
        orca_pass(shell, config)
        shell.sleep(60)
    lines = escalations(shell)
    assert len(lines) == 1 and "DEADLINE" in lines[0] and "never delivered" in lines[0]


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
    (tmp_path / "receipts" / "half-written.json").write_text("")
    (tmp_path / "receipts" / "half-written.terminal").write_text("term_x\n")
    cli(shell, config, "relay", "--key", "R700", "--lane", "half-written", "--text", "hello")
    shell.receive({"id": "msg_n", "type": "status", "subject": "note", "body": "", "thread_id": None, "payload": "null", "from_handle": "term_ctx_a", "lane": LANE})
    orca_pass(shell, config)
    assert shell.sends() == []


def test_read_messages_still_transfer_ownership_complete_relays_and_report_outcomes(shell, config, tmp_path):
    cli(shell, config, "relay", "--key", "R800", "--lane", LANE, "--text", "apply the fix")
    orca_pass(shell, config)
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


@pytest.mark.parametrize("subject,kind,sender,lane", [("fix-live: 12:48 PM PT release abc", "FIX-LIVE", "term_ctx_a", LANE), ("MeChAnIsM: wrong selector", "MECHANISM", "term_unknown", "term_unknown")])
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
    assert launches(shell) == [[str(runner_module.SCRIPTS / "orca-launch.sh"), LANE, "sol", "xhigh", str(brief)]]


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


def test_an_inbox_launch_line_launches_once_and_now_skips_the_load_hold(shell, config, tmp_path):
    brief = launch_brief(tmp_path)
    shell.cpu_load = 140
    shell.launch_line = f"{LANE} ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    orca_pass(shell, config)
    desk_inbox(tmp_path, f"R1907 (7:0x PM PT) orca-desk: launch {LANE} NOW opus xhigh brief={brief}")
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_n")
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert launches(shell) == [[str(runner_module.SCRIPTS / "orca-launch.sh"), LANE, "opus", "xhigh", str(brief)]]
    action = incident(tmp_path, f"desk-lane-{LANE}").actions["R1907"]
    assert (action.status, json.loads(action.target)["urgent"]) == ("verified", True)
    assert [line for line in escalations(shell)] == [f"LAUNCHED R1907 {LANE}: dispatch ctx_n terminal term_ctx_n"]
    assert [(post["kind"], post["topic"]) for post in shell.posts] == [("report", "R1907")]


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
    assert launches(shell) == [[str(runner_module.SCRIPTS / "orca-launch.sh"), LANE, "incident", "xhigh", str(brief)]]
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
    shell.cpu_load = 140
    shell.launch_line = "dd-312516332-fix ready task=task_1 dispatch=ctx_n terminal=term_ctx_n worktree=/w\n"
    orca_pass(shell, config)
    desk_inbox(tmp_path, "- orca-desk: incident dd-312516332 https://app.datadoghq.com/monitors/312516332 :: Datadog OK -> Alert Run assignment starved")
    orca_pass(shell, config)
    shell.launch("dd-312516332-fix", "ctx_n")
    orca_pass(shell, config)
    brief = shell.attachments["dd-312516332-fix.full.md"]
    assert brief.parent == tmp_path / "lfs"
    assert launches(shell) == [[str(runner_module.SCRIPTS / "orca-launch.sh"), "dd-312516332-fix", "incident", "xhigh", str(brief)]]
    assert shell.entries == ["INCIDENT dd-312516332: fix brief for dd-312516332-fix: Datadog OK -> Alert Run assignment starved https://app.datadoghq.com/monitors/312516332"]
    text = brief.read_text()
    assert "Datadog OK -> Alert Run assignment starved" in text and "apply authority: 0 deletes and 0 replaces" in text
    assert "Rollback first" in text and "{" not in text
    assert f"ccn -R {tmp_path / 'checkout'} attachment path briefs1 dd-312516332-evidence.md" in text
    assert not (tmp_path / "incidents").exists()
    lines = [line for line in escalations(shell)]
    assert lines[0].startswith("INCIDENT dd-312516332 ") and "fix lane dd-312516332-fix launching on incident xhigh" in lines[0]
    assert lines[1] == "LAUNCHED inbox@0 dd-312516332-fix: dispatch ctx_n terminal term_ctx_n"


ROOT_ALERT = "orca-desk: incident alerts-api-0305 https://in-the-forge.slack.com/archives/C0822AHFY3G/p1791108227539019 :: SandDB admission turning bulk queries away on 0ddq7rb (monitor 327967001, Warn 3:03 AM PT)"


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
    desk_inbox(tmp_path, "R40 orca-desk: incident dd-7 https://app.datadoghq.com/monitors/7 :: Datadog OK -> Alert again")
    orca_pass(shell, config)
    brief = shell.attachments["dd-7-fix.full.md"]
    assert [call[1:] for call in launches(shell)] == [["dd-7-fix", "incident", "xhigh", str(brief)]]
    assert brief != last and "Datadog OK -> Alert again" in brief.read_text()


def test_an_incident_whose_brief_fails_to_attach_launches_nothing(shell, config, tmp_path):
    shell.attach_error = "error: cc-notes: ref lock held"
    orca_pass(shell, config)
    desk_inbox(tmp_path, "R41 orca-desk: incident dd-8 https://app.datadoghq.com/monitors/8 :: Datadog OK -> Alert")
    orca_pass(shell, config)
    assert launches(shell) == [] and "dd-8-fix.full.md" not in shell.attachments
    [line] = escalations(shell)
    assert line.startswith("INCIDENT dd-8 ") and line.endswith("| dd-8-fix not launched: the brief did not attach: error: cc-notes: ref lock held")


def test_an_incident_whose_attach_dies_silently_launches_nothing(shell, config, tmp_path):
    shell.attach_code = -9
    orca_pass(shell, config)
    desk_inbox(tmp_path, "R42 orca-desk: incident dd-9 https://app.datadoghq.com/monitors/9 :: Datadog OK -> Alert")
    orca_pass(shell, config)
    assert launches(shell) == []
    [line] = escalations(shell)
    assert line.endswith("| dd-9-fix not launched: the brief did not attach: ccn exited -9")


def test_an_incident_whose_attached_brief_has_no_path_launches_nothing(shell, config, tmp_path):
    shell.unresolved.add("dd-10-fix.full.md")
    orca_pass(shell, config)
    desk_inbox(tmp_path, "R43 orca-desk: incident dd-10 https://app.datadoghq.com/monitors/10 :: Datadog OK -> Alert")
    orca_pass(shell, config)
    assert launches(shell) == []
    [line] = escalations(shell)
    assert line.endswith("| dd-10-fix not launched: the attached brief has no path")


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
