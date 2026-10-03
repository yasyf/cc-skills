"""desk-runner.py against a fake Orca Run, ledger, stack-enqueue, and judge, on a fake clock: every side effect is recorded on ``shell.calls``."""

from __future__ import annotations

import importlib.util
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


class FakeShell(runner_module.Shell):
    def __init__(self, root: Path):
        self.root = root
        self.clock = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
        self.calls: list[list[str]] = []
        self.dispatches: dict[str, dict] = {}
        self.mailbox: list[dict] = []
        self.inbox_error = None
        self.inboxes: dict[str, list[dict]] = {}
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
        self.sequence = 0

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += timedelta(seconds=seconds)

    def load(self):
        return self.cpu_load

    def cores(self):
        return 8

    def spawn(self, argv, out, env):
        self.calls.append(list(argv))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.launch_line)
        return Process()

    def next_id(self, prefix: str) -> str:
        self.sequence += 1
        return f"{prefix}_{self.sequence:04d}"

    def run(self, argv, stdin=None, env=None):
        self.calls.append(list(argv))
        name = Path(argv[0]).name
        if argv[0] == "orca":
            return self.orca(argv[1:-1])
        if argv[:5] == ["ccn", "-R", str(self.root / "checkout"), "attachment", "path"] and argv[5] == "briefs1":
            path = self.attachments.get(argv[6])
            return runner_module.Done(0, f"{path}\n", "") if path else runner_module.Done(1, "", f"no attachment {argv[6]}")
        if name == "orca-check.sh":
            assert argv[1:] == ["--stale"]
            return runner_module.Done(0, self.stale_out, "")
        if name == "stack-enqueue":
            return self.stack_enqueue(argv[1:])
        if name == "claude":
            return runner_module.Done(0, json.dumps([{"type": "system"}, {"type": "result", "structured_output": self.verdict}]), "")
        if len(argv) > 1 and Path(argv[1]).name == "ledger.py":
            verb = argv[4]
            return runner_module.Done(0, json.dumps(self.rows) if verb == "list" else f"{verb} ok\n", "")
        if len(argv) > 1 and Path(argv[1]).name == "bus.py":
            return runner_module.Done(0, "#7\n", "")
        raise AssertionError(f"unexpected call {argv}")

    def ok(self, result: dict) -> runner_module.Done:
        return runner_module.Done(0, json.dumps({"ok": True, "result": result}), "")

    def orca(self, argv: list[str]) -> runner_module.Done:
        verb = argv[:2]
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
            assert argv[2] == "--terminal" and not argv[3].startswith("run:")
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
                "escalations": str(tmp_path / "inbox/root-runner.md"),
                "view": str(tmp_path / "desk-runner.md"),
                "orca": {"run": "run_1", "receipts": str(tmp_path / "receipts"), "briefs": {"repo": str(tmp_path / "checkout"), "log": "briefs1"}},
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


def escalations(tmp_path: Path) -> list[str]:
    path = tmp_path / "inbox/root-runner.md"
    return path.read_text().splitlines() if path.is_file() else []


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
    assert escalations(tmp_path) == []


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
    lines = escalations(tmp_path)
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
    lines = escalations(tmp_path)
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
    assert escalations(tmp_path) == []
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
    assert escalations(tmp_path) == []


def test_a_question_the_brief_does_not_settle_escalates_with_options(shell, config, tmp_path):
    orca_pass(shell, config)
    shell.launch(LANE, "ctx_a")
    shell.verdict = {"verdict": "escalate", "text": "apply to prod? A) wait B) apply now"}
    shell.receive({"id": "msg_q2", "type": "escalation", "subject": "apply?", "body": "", "thread_id": None, "payload": json.dumps({"dispatchId": "ctx_a"}), "from_handle": "term_ctx_a", "lane": LANE})
    orca_pass(shell, config)
    lines = escalations(tmp_path)
    assert len(lines) == 1 and "DECIDE msg_q2" in lines[0] and "A) wait B) apply now" in lines[0]
    assert not [call for call in shell.calls if call[:3] == ["orca", "orchestration", "reply"]]


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


@pytest.mark.parametrize(("model", "flags"), [("sol", ()), ("opus", ("--owner-directed",))])
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
    assert launches(shell) == [] and escalations(tmp_path) == []
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "accepted"
    for _ in range(3):
        shell.sleep(60)
        orca_pass(shell, config)
    assert launches(shell) == []
    action = incident(tmp_path, f"desk-lane-{LANE}").actions["R638"]
    assert action.status == "failed" and "load 40 above 8 cores" in action.reason
    [line] = escalations(tmp_path)
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
    lines = escalations(tmp_path)
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


def test_a_superseding_policy_naming_the_accepted_revision_applies(shell, config, tmp_path):
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", "#29016 GREEN bbbb222222 graphite READY", "#29020 GREEN cccc333333 graphite READY", would="#28997 #29016 #29020")]
    shell.enqueue_out["29020"] = (0, "enqueue #28997 #29016 #29020: {}\n")
    landing_pass(shell, config)
    cli(shell, config, "policy", "--key", "L300", "--landing", "whole", "--revision", "f9e8d7c6b5", "--supersedes", "a1b2c3d4e5", "--source", "owner ruling")
    assert escalations(tmp_path) == []
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
    assert escalations(tmp_path) == []


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
    assert shell.sends() == [] and escalations(tmp_path) == []


def test_an_enqueue_whose_response_was_lost_settles_from_graphite_status(shell, config, tmp_path):
    shell.rows = stack_rows()
    shell.gates["29020"] = [gate("#28997 GREEN aaaa111111 graphite READY", would="#28997")]
    store = actions.Store(tmp_path / "store")
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    runner_module.seed_policy(runner)
    landing = runner_module.Landing(runner, runner.config.landing)
    landing.accept("29020", ["28997"], {"28997": {"sha": "aaaa111111"}})
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
    assert "UNVERIFIABLE" in escalations(tmp_path)[0]


def test_an_undeliverable_reply_reaches_its_deadline(shell, config, tmp_path):
    cli(shell, config, "relay", "--key", "R633", "--lane", LANE, "--text", "use evidence.md", "--reply-to", "msg_q9")
    for _ in range(12):
        orca_pass(shell, config)
        shell.sleep(60)
    lines = escalations(tmp_path)
    assert len(lines) == 1 and "DEADLINE" in lines[0] and "never delivered" in lines[0]


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
    lines = escalations(tmp_path)
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
    landing.accept("29020", ["28997"], {"28997": {"sha": "aaaa111111"}})
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
    runner_module.Landing(runner, runner.config.landing).accept("29020", ["28997"], {"28997": {"sha": "aaaa111111"}})
    key = next(key for key in store.load("desk-landing").actions if key.startswith("enqueue:"))
    with store.owned("desk-landing", 0) as live:
        live.start(key, shell.now())
    shell.sleep(16 * 60)
    shell.statuses = "#28997 READY_TO_MERGE aaaa111111 open review APPROVED on dev\n"
    landing_pass(shell, config)
    landing_pass(shell, config)
    assert store.load("desk-landing").actions[key].status == "unverifiable"
    assert shell.enqueues() == []
    lines = escalations(tmp_path)
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
    assert escalations(tmp_path) == [f"11:00 OUTCOME {outcome['id']} {LANE}: worker_done success dispatch=ctx_a: fixed"]
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
    assert [line.split()[2] for line in escalations(tmp_path)] == [before["id"], *processed]


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
    assert [line.split()[2] for line in escalations(tmp_path)] == [message["id"] for message in messages]


@pytest.mark.parametrize("subject,kind,sender,lane", [("fix-live: 12:48 PM PT release abc", "FIX-LIVE", "term_ctx_a", LANE), ("MeChAnIsM: wrong selector", "MECHANISM", "term_unknown", "term_unknown")])
def test_milestone_status_escalates_once_per_message_id(shell, config, tmp_path, subject, kind, sender, lane):
    shell.launch(LANE, "ctx_a")
    orca_pass(shell, config)
    body = "release\n\t" + "a " * 200
    message = shell.receive({"subject": subject, "body": body, "from_handle": sender})
    orca_pass(shell, config)
    shell.receive({"id": message["id"], "subject": subject, "body": body, "from_handle": sender})
    orca_pass(shell, config)
    assert escalations(tmp_path) == [f"11:00 {kind} {message['id']} {lane}: {subject}: {' '.join(body.split())[:300].rstrip()}"]


def test_only_exact_status_milestone_prefixes_escalate_and_heartbeats_advance_the_cursor(shell, config, tmp_path):
    orca_pass(shell, config)
    for subject in (None, "", "checkpoint", "fix-live soon", "mechanisms: known", "prefix fix-live: now"):
        shell.receive({"subject": subject})
    heartbeat = shell.receive({"type": "heartbeat", "subject": "fix-live: ignored", "payload": "not json"})
    orca_pass(shell, config)
    assert escalations(tmp_path) == []
    assert incident(tmp_path, "desk-runner").facts["inbox:run_1"] == heartbeat["sequence"]


def test_first_read_starts_at_the_newest_sequence_without_replaying_history(shell, config, tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "INBOX_PAGE_SIZE", 2)
    for index in range(5):
        newest = shell.receive({"type": "worker_done", "subject": f"history {index}", "read": True})
    orca_pass(shell, config)
    assert incident(tmp_path, "desk-runner").facts["inbox:run_1"] == newest["sequence"]
    assert escalations(tmp_path) == []
    assert len([call for call in shell.calls if call[:3] == ["orca", "orchestration", "inbox"]]) == 1
    fresh = shell.receive({"type": "worker_done", "subject": "fresh"})
    orca_pass(shell, config)
    assert [line.split()[2] for line in escalations(tmp_path)] == [fresh["id"]]


@pytest.mark.parametrize("failure", [runner_module.Done(1, json.dumps({"ok": False, "error": {"code": "runtime_unavailable", "message": "socket closed"}}), ""), runner_module.Done(1, "", "socket closed")])
def test_inbox_errors_escalate_once_per_bucket_without_advancing_the_cursor(shell, config, tmp_path, failure):
    orca_pass(shell, config)
    fresh = shell.receive({"type": "worker_done", "subject": "fresh"})
    shell.inbox_error = failure
    orca_pass(shell, config)
    orca_pass(shell, config)
    assert incident(tmp_path, "desk-runner").facts["inbox:run_1"] == 0
    [line] = escalations(tmp_path)
    assert "ORCA-INBOX orca-inbox:" in line and "socket closed" in line
    shell.sleep(10 * 60)
    orca_pass(shell, config)
    assert len(escalations(tmp_path)) == 2
    shell.inbox_error = None
    orca_pass(shell, config)
    assert incident(tmp_path, "desk-runner").facts["inbox:run_1"] == fresh["sequence"]
    assert f"OUTCOME {fresh['id']}" in escalations(tmp_path)[-1]


def test_the_orca_loop_sleeps_ten_seconds_between_passes(shell, config, tmp_path, monkeypatch):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        raise RuntimeError("end loop")

    monkeypatch.setattr(shell, "sleep", sleep)
    with pytest.raises(RuntimeError, match="end loop"):
        runner_module.run_orca(runner, False)
    assert sleeps == [10]


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
    reclaims = [line for line in escalations(tmp_path) if " RECLAIM " in line]
    assert len(reclaims) == 1
    assert reclaims[0].endswith(f"1 settled dispatch(es) still hold their terminal: {LANE}=ctx_a:term_ctx_a; run .agents/skills/orca/scripts/orca-gc --run run_1 --dispatch ctx_a")
    assert [call for call in shell.calls if FORBIDDEN & {Path(token).name for token in call}] == []


def test_a_large_settled_set_names_the_whole_run(shell, config, tmp_path):
    with_gc(config)
    for index in range(runner_module.RECLAIM_NAMED + 2):
        shell.launch(f"lane-{index:02d}", f"ctx_{index:02d}", status="failed")
    orca_pass(shell, config)
    [reclaim] = [line for line in escalations(tmp_path) if " RECLAIM " in line]
    assert " and 2 more; run .agents/skills/orca/scripts/orca-gc --run run_1" in reclaim
    assert reclaim.endswith("--run run_1")


def test_without_a_gc_the_line_names_the_r195_bar(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a", status="completed")
    orca_pass(shell, config)
    [reclaim] = [line for line in escalations(tmp_path) if " RECLAIM " in line]
    assert reclaim.endswith("close each idle terminal and remove each finished worktree under R195")


@pytest.mark.parametrize(("flag", "value"), [("--model", "gemini"), ("--effort", "extreme")])
def test_a_launch_the_script_cannot_start_is_refused_when_submitted(shell, config, tmp_path, flag, value):
    argv = {"--model": "astra", "--effort": "xhigh"} | {flag: value}
    with pytest.raises(SystemExit):
        cli(shell, config, "launch", "--key", "R1", "--lane", LANE, "--model", argv["--model"], "--effort", argv["--effort"], "--brief", str(tmp_path / "brief.md"))
    assert not (tmp_path / "store").exists()


@pytest.mark.parametrize("model", ["astra", "codex", "sol", "opus", "claude-opus-5-5", "gpt-6.1-sol"])
def test_every_model_the_script_starts_is_accepted(model):
    assert runner_module.launch_model(model) == model
