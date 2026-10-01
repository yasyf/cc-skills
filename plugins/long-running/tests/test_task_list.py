from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cc_transcript import Session
from captain_hook.events import PostToolUseEvent, PreToolUseEvent, StopEvent
from captain_hook.testing.helpers import build_context

from fire import fire
from hooks import nudges, task_list
from hooks.compaction_handoff import CompactionState

SESSION = "0123456789abcdef"
ASK_LINE = "Every owner ask gets a task. Run `TaskCreate` for it."
TEAM = "session-root"
NOW = datetime.now(UTC)


def stamp(at: datetime) -> str:
    return at.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def envelope(kind: str, at: datetime | None = None, *, sidechain: bool = False) -> dict:
    return {
        "type": kind,
        "isSidechain": sidechain,
        "timestamp": stamp(at or datetime.now(UTC)),
        "uuid": str(uuid.uuid4()),
        "parentUuid": None,
        "sessionId": SESSION,
    }


def user_row(content: object, at: datetime | None = None, *, sidechain: bool = False) -> dict:
    return envelope("user", at, sidechain=sidechain) | {"message": {"role": "user", "content": content}}




class Drive:
    def __init__(self, home: Path) -> None:
        self.claude = home / ".claude"
        self.root = self.claude / "projects" / "p" / "root.jsonl"
        self.root.parent.mkdir(parents=True)
        self.root.write_text(json.dumps(user_row("/long-running go")) + "\n")
        self.tasks = self.claude / "tasks" / TEAM
        self.tasks.mkdir(parents=True)
        self.session_dir = home / "state"
        self.lanes: dict[str, dict] = {}
        self.lane("seed", busy=False)
        self.activate()

    def activate(self) -> None:
        CompactionState(active=True).save(self.event(PostToolUseEvent, tool_name="Bash", tool_input={"command": "ls"}))

    def lane(self, name: str, *, behind: timedelta = timedelta(minutes=2), busy: bool = True) -> None:
        subagents = self.root.with_suffix("") / "subagents"
        subagents.mkdir(parents=True, exist_ok=True)
        meta = {"name": name, "description": f"{name} lane", "teamName": TEAM, "taskKind": "in_process_teammate"}
        (subagents / f"agent-a{name}.meta.json").write_text(json.dumps(meta))
        at = NOW - behind
        usage = {"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1}
        reply = {"role": "assistant", "model": "claude-opus-5-5", "usage": usage, "content": [{"type": "text", "text": "ok"}]}
        rows = [
            user_row("go", at - timedelta(hours=1), sidechain=True),
            envelope("assistant", at, sidechain=True) | {"message": reply},
        ]
        (subagents / f"agent-a{name}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        roster = self.claude / "teams" / TEAM / "config.json"
        roster.parent.mkdir(parents=True, exist_ok=True)
        members = json.loads(roster.read_text())["members"] if roster.exists() else []
        roster.write_text(json.dumps({"name": TEAM, "members": [*members, {"agentId": f"{name}@{TEAM}", "name": name}]}))
        if busy:
            self.lanes[name] = {"id": f"t-{name}", "type": "teammate", "status": "running", "description": meta["description"]}

    def task(self, task_id: str, subject: str, status: str = "in_progress", owner: str | None = None) -> None:
        raw = {"id": task_id, "subject": subject, "description": "", "status": status, "blocks": [], "blockedBy": []}
        (self.tasks / f"{task_id}.json").write_text(json.dumps(raw | ({"owner": owner} if owner else {})))

    def append(self, *entries: dict) -> None:
        with self.root.open("a") as transcript:
            transcript.writelines(json.dumps(entry) + "\n" for entry in entries)

    def say(self, text: str) -> None:
        self.append(user_row(text))

    def queue(self, text: str) -> None:
        self.append(envelope("attachment") | {"attachment": {"type": "queued_command", "prompt": text}})

    def event(self, cls, **raw):
        payload = {"session_id": SESSION, "transcript_path": str(self.root), "cwd": str(self.claude.parent)} | raw
        ctx = build_context(transcript=Session.from_path(self.root), session_dir=self.session_dir)
        return cls(_raw=payload, ctx=ctx)

    def tool(self, name: str, tool_input: dict, **raw) -> str | None:
        evt = self.event(PostToolUseEvent, tool_name=name, tool_input=tool_input, **raw)
        messages = [result.message for result in fire(task_list, evt)]
        return "\n".join(messages) or None

    def bash(self, command: str = "ls") -> str | None:
        return self.tool("Bash", {"command": command})

    def stop(self) -> list[str]:
        evt = self.event(StopEvent, background_tasks=list(self.lanes.values()))
        assert fire(task_list, evt) == []
        with nudges.NudgeState.mutate(evt) as state:
            pending, state.pending = state.pending, []
        return pending


@pytest.fixture
def drive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Drive:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CAPTAIN_HOOK_TASKS_DIR", str(tmp_path / ".claude" / "tasks"))
    built = Drive(tmp_path)
    built.bash()
    return built


def test_spawn_without_a_task_is_named_at_turn_end(drive: Drive) -> None:
    drive.tool("Agent", {"name": "ledger-fix", "description": "fix ledger", "prompt": "go", "team_name": TEAM})

    assert drive.stop() == ["Lane `ledger-fix` has no task. Run `TaskCreate` with `owner=ledger-fix`."]
    assert drive.stop() == []


def test_spawn_with_a_task_in_the_same_turn_is_quiet(drive: Drive) -> None:
    drive.tool("Agent", {"name": "ledger-fix", "description": "fix ledger", "prompt": "go", "team_name": TEAM})
    drive.task("7", "Fix the ledger", owner="ledger-fix")
    drive.tool("TaskCreate", {"subject": "Fix the ledger", "description": "x"})

    assert drive.stop() == []


def test_task_created_before_the_spawn_covers_it(drive: Drive) -> None:
    drive.task("7", "Owner: fix the ledger — lane ledger-fix", status="pending")
    drive.tool("Agent", {"name": "ledger-fix", "description": "fix ledger", "prompt": "go", "team_name": TEAM})

    assert drive.stop() == []


@pytest.mark.parametrize(
    "command",
    [
        "scripts/orca-launch.sh alert-fix sol xhigh brief.md",
        "orca orchestration worker-start --spec x --display-name alert-fix --agent claude",
        "orca orchestration worker-start --spec x --name=alert-fix --agent claude",
    ],
)
def test_orca_launch_counts_as_a_spawn(drive: Drive, command: str) -> None:
    drive.bash(command)

    assert drive.stop() == ["Lane `alert-fix` has no task. Run `TaskCreate` with `owner=alert-fix`."]


def test_unnamed_subagent_needs_no_task(drive: Drive) -> None:
    drive.tool("Agent", {"description": "look around", "prompt": "go", "subagent_type": "Explore"})

    assert drive.stop() == []


def test_done_message_from_the_owning_lane_flags_its_task_once(drive: Drive) -> None:
    drive.task("12", "Land the stack", owner="stack-lander")
    drive.task("13", "Other work", owner="other-lane")
    drive.say('<teammate-message teammate_id="stack-lander" summary="landed">\n#28398 landed on dev\n</teammate-message>')

    assert drive.bash() == "Lane `stack-lander` reported its task done. Consume its deliverable, then run `TaskUpdate`."
    drive.say('<teammate-message teammate_id="stack-lander" summary="done">\nall done\n</teammate-message>')
    assert drive.bash() is None


def test_lane_named_in_the_subject_owns_the_task(drive: Drive) -> None:
    drive.task("62", "Owner: auto-register PRs — lane ledger-auto-register")
    drive.queue(
        'Another Claude session sent a message:\n<teammate-message teammate_id="ledger-auto-register">\n'
        "PR #41 GREEN, READY\n</teammate-message>"
    )

    assert drive.bash().startswith("Lane `ledger-auto-register` reported its task done")


def test_desk_relay_flags_the_lane_it_names_not_the_desk(drive: Drive) -> None:
    drive.task("34", "Dev-head sweep", owner="orca-desk")
    drive.task("68", "Apply the handoff fix", owner="incident-sandsql-handoff")
    drive.say('<teammate-message teammate_id="landing-desk-2">\nR384 is done: incident-sandsql-handoff applied all three, orca-desk relayed\n</teammate-message>')

    [line] = drive.bash().splitlines()
    assert line.startswith("Lane `incident-sandsql-handoff` reported its task done")
    drive.say('<teammate-message teammate_id="orca-desk">\nR385 done, #28562 LANDED\n</teammate-message>')
    assert drive.bash() is None


def test_lane_with_several_tasks_flags_only_the_one_its_report_names(drive: Drive) -> None:
    drive.task("29", "Deploy experience umbrella", owner="deploy-experience")
    drive.task("60", "G37: force a TenantSmoke re-run", owner="deploy-experience")
    drive.say('<teammate-message teammate_id="deploy-experience">\nREADY #28608 — G37 TenantSmoke re-run\n</teammate-message>')

    assert drive.bash().startswith("Lane `deploy-experience` reported its task done")
    drive.say('<teammate-message teammate_id="deploy-experience">\nGREEN #28599 — G38 handoff read\n</teammate-message>')
    assert drive.bash() is None


def test_progress_and_idle_messages_do_not_flag(drive: Drive) -> None:
    drive.task("12", "Land the stack", owner="landing-desk")
    drive.say('<teammate-message teammate_id="landing-desk">\nhead moved to abc after red CI\n</teammate-message>')
    drive.say('<teammate-message teammate_id="landing-desk">\n{"type":"idle_notification","result":"done"}\n</teammate-message>')

    assert drive.bash() is None


def test_worker_done_notification_flags_the_named_lane(drive: Drive) -> None:
    drive.task("68", "apply plat → SoFi → AIG", owner="incident-sandsql-handoff")
    drive.queue(
        '<task-notification>\n<summary>Monitor event</summary>\n<event>{"kind": "worker_done", '
        '"worker": "incident-sandsql-handoff"}</event>\n</task-notification>'
    )

    assert drive.bash().startswith("Lane `incident-sandsql-handoff` reported its task done")


def test_owner_ask_without_a_task_is_flagged_on_the_third_call(drive: Drive) -> None:
    drive.say("fix the long running ledger so you're not guessing https://github.com/x/y/pull/1")

    assert [drive.bash(), drive.bash(), drive.bash()] == [
        None,
        None,
        ASK_LINE,
    ]


def test_owner_ask_answered_with_a_task_is_quiet(drive: Drive) -> None:
    drive.say("make sure every PR lands in the ledger")
    drive.tool("TaskCreate", {"subject": "Ledger auto-register", "description": "x"})

    assert [drive.bash(), drive.bash(), drive.bash()] == [None, None, None]
    assert drive.stop() == []


def test_mid_turn_owner_ask_is_flagged_at_turn_end(drive: Drive) -> None:
    drive.queue("also sweep every monitor so only outages page")

    assert drive.stop() == [ASK_LINE]


@pytest.mark.parametrize(
    "text",
    [
        "overall status update?",
        "can you give me a status update?",
        "so whats left and how are we doing",
        '<teammate-message teammate_id="x">\nplease fix y\n</teammate-message>',
        "<task-notification>\n<summary>fix landed</summary>\n</task-notification>",
        "/long-running continue",
    ],
)
def test_status_questions_and_relayed_messages_are_not_asks(drive: Drive, text: str) -> None:
    drive.say(text)

    assert [drive.bash(), drive.bash(), drive.bash()] == [None, None, None]


def test_reconciliation_lists_stale_tasks_and_untracked_lanes(drive: Drive) -> None:
    drive.lane("busy-lane")
    drive.lane("quiet-lane", behind=timedelta(hours=2))
    drive.lane("loose-lane")
    drive.lane("gone-lane", busy=False)
    drive.task("1", "Busy work", owner="busy-lane")
    drive.task("2", "Quiet work", owner="quiet-lane")
    drive.task("3", "Gone work — lane gone-lane")
    drive.task("4", "Root-held decision")

    turns = [drive.stop() for _ in range(task_list.RECONCILE_TURNS)]

    assert turns[:-1] == [[]] * (task_list.RECONCILE_TURNS - 1)
    assert turns[-1] == [
        "The task list has drifted from the running lanes. Run `TaskUpdate` to complete, re-own, or delete the stale tasks."
    ]


def test_completing_an_unrelated_task_leaves_the_ask_pending(drive: Drive) -> None:
    drive.say("fix the ledger hook")
    drive.tool("TaskUpdate", {"taskId": "3", "status": "deleted"})

    assert drive.bash() is None
    assert drive.bash() == ASK_LINE


def test_owner_ask_with_an_image_is_read_from_its_text_block(drive: Drive) -> None:
    drive.append(user_row([{"type": "image"}, {"type": "text", "text": "fix this"}]))

    assert drive.stop() == [ASK_LINE]


def test_explicit_task_list_id_wins(drive: Drive, monkeypatch: pytest.MonkeyPatch) -> None:
    shared = drive.claude / "tasks" / "shared-list"
    shared.mkdir()
    (shared / "5.json").write_text(json.dumps({"id": "5", "subject": "x", "status": "in_progress", "owner": "ledger-fix"}))
    monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", "shared-list")

    assert gate(drive, "5").startswith("Task #5 is the root's record of lane ledger-fix")


def test_named_task_and_negative_status(drive: Drive) -> None:
    drive.task("29", "Umbrella", owner="deploy-experience")
    drive.task("60", "Smoke re-run", owner="deploy-experience")
    drive.say('<teammate-message teammate_id="deploy-experience">\n#28608 NOT-READY: smoke red\n</teammate-message>')
    assert drive.bash() is None
    drive.say('<teammate-message teammate_id="deploy-experience">\ntask #60 done\n</teammate-message>')
    assert drive.bash().startswith("Lane `deploy-experience` reported its task done")


def test_orca_lane_is_never_called_missing(drive: Drive) -> None:
    drive.task("68", "Apply the fix", owner="incident-orca-worker")

    assert [drive.stop() for _ in range(task_list.RECONCILE_TURNS)][-1] == []


def test_inactive_drive_is_ignored(drive: Drive) -> None:
    CompactionState(active=False).save(drive.event(PostToolUseEvent, tool_name="Bash", tool_input={"command": "ls"}))
    drive.tool("Agent", {"name": "ledger-fix", "prompt": "go", "team_name": TEAM})
    drive.say("fix everything")

    assert [drive.bash(), drive.bash(), drive.bash()] == [None, None, None]
    assert drive.stop() == []


def gate(drive: Drive, task_id: str, status: str = "completed") -> str | None:
    evt = drive.event(PreToolUseEvent, tool_name="TaskUpdate", tool_input={"taskId": task_id, "status": status}, agent_id="aledger")
    result = task_list.lanes_leave_root_tasks_open(evt)
    return result.message if result else None


def test_lane_cannot_complete_the_roots_task_for_it(drive: Drive) -> None:
    drive.task("62", "Auto-register PRs", owner="ledger-auto-register")
    drive.task("90", "My own subtask")

    assert gate(drive, "62") == (
        "Task #62 is the root's record of lane ledger-auto-register; the root completes it once it has consumed "
        "your deliverable. SendMessage team-lead the deliverable instead and leave the task open."
    )
    assert gate(drive, "62", status="in_progress") is None
    assert gate(drive, "90") is None
