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
            return self.check(argv)
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
            to = argv[argv.index("--to") + 1].removeprefix("dispatch:")
            message = {"id": self.next_id("msg"), "thread_id": argv[argv.index("--thread-id") + 1], "created_at": "2026-10-01T18:00:00Z"}
            self.inboxes.setdefault(self.dispatches[to]["terminal"], []).append(message)
            return self.ok({"message": message})
        if verb == ["orchestration", "reply"]:
            return self.ok({"message": {"id": self.next_id("msg")}})
        if verb == ["orchestration", "request-show"]:
            return self.ok({"state": self.requests.get(argv[3], "absent")})
        if verb == ["orchestration", "check"]:
            return self.ok({"messages": self.inboxes.get(argv[3], [])})
        if verb == ["terminal", "send"]:
            return self.ok({})
        raise AssertionError(f"unexpected orca call {argv}")

    def check(self, argv: list[str]) -> runner_module.Done:
        if "--stale" in argv:
            return runner_module.Done(0, self.stale_out, "")
        if not self.mailbox:
            return runner_module.Done(0, "timeout\n", "")
        batch, self.mailbox = self.mailbox, []
        lines = [json.dumps(message) for message in batch] + [f"delivery {self.next_id('dlv')} heartbeats=0"]
        return runner_module.Done(0, "\n".join(lines) + "\n", "")

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

    def ack(self, thread: str, verb: str, dispatch: str, lane: str = LANE) -> None:
        key = thread.split("/", 1)[1]
        self.mailbox.append(
            {
                "id": self.next_id("msg"),
                "type": "status",
                "subject": f"{verb} {key}",
                "body": "ok",
                "thread_id": thread,
                "payload": json.dumps({"dispatchId": dispatch}),
                "from_handle": self.dispatches[dispatch]["terminal"],
                "lane": lane,
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
    shell.launch(LANE, "ctx_a")
    shell.mailbox.append({"id": "msg_hb", "type": "status", "subject": "checkpoint", "body": "working", "thread_id": None, "payload": json.dumps({"dispatchId": "ctx_a"}), "from_handle": "term_ctx_a", "lane": LANE})
    for _ in range(10):
        orca_pass(shell, config)
        shell.sleep(60)
    assert escalations(tmp_path) == []
    assert not [call for call in shell.calls if Path(call[0]).name == "claude"]


def test_a_routine_question_the_brief_settles_is_answered_without_escalating(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    question = {"id": "msg_q1", "type": "question", "subject": "rebase?", "body": "may I rebase onto dev", "thread_id": None, "payload": json.dumps({"dispatchId": "ctx_a"}), "from_handle": "term_ctx_a", "lane": LANE}
    shell.mailbox.append(question)
    orca_pass(shell, config)
    shell.mailbox.append(question)
    orca_pass(shell, config)
    replies = [call for call in shell.calls if call[:3] == ["orca", "orchestration", "reply"]]
    assert len(replies) == 1 and replies[0][replies[0].index("--id") + 1] == "msg_q1"
    assert len([call for call in shell.calls if Path(call[0]).name == "claude"]) == 1
    assert escalations(tmp_path) == []


def test_a_question_the_brief_does_not_settle_escalates_with_options(shell, config, tmp_path):
    shell.launch(LANE, "ctx_a")
    shell.verdict = {"verdict": "escalate", "text": "apply to prod? A) wait B) apply now"}
    shell.mailbox.append({"id": "msg_q2", "type": "escalation", "subject": "apply?", "body": "", "thread_id": None, "payload": json.dumps({"dispatchId": "ctx_a"}), "from_handle": "term_ctx_a", "lane": LANE})
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


def test_a_launch_held_by_load_starts_nothing(shell, config, tmp_path):
    brief = tmp_path / "fix.md"
    brief.write_text("brief")
    shell.cpu_load = 40
    cli(shell, config, "launch", "--key", "R638", "--lane", LANE, "--model", "sol", "--effort", "xhigh", "--brief", str(brief))
    orca_pass(shell, config)
    assert not [call for call in shell.calls if Path(call[0]).name == "orca-launch.sh"]
    assert incident(tmp_path, f"desk-lane-{LANE}").actions["R638"].status == "accepted"


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
    shell.launch(LANE, "ctx_a")
    (tmp_path / "receipts" / "half-written.json").write_text("")
    (tmp_path / "receipts" / "half-written.terminal").write_text("term_x\n")
    cli(shell, config, "relay", "--key", "R700", "--lane", "half-written", "--text", "hello")
    shell.mailbox.append({"id": "msg_n", "type": "status", "subject": "note", "body": "", "thread_id": None, "payload": "null", "from_handle": "term_ctx_a", "lane": LANE})
    orca_pass(shell, config)
    assert shell.sends() == []
