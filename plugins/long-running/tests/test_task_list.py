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

    def task(
        self, task_id: str, subject: str, status: str = "in_progress", owner: str | None = None, description: str = ""
    ) -> None:
        raw = {"id": task_id, "subject": subject, "description": description, "status": status, "blocks": [], "blockedBy": []}
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


def test_a_second_owner_ask_within_the_hour_is_not_flagged_again(drive: Drive) -> None:
    drive.queue("also sweep every monitor so only outages page")
    assert drive.stop() == [ASK_LINE]

    drive.queue("and fix the ledger hook")
    assert drive.stop() == []


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

    assert gate(drive, "5").startswith("Warning, nothing was blocked: Task #5 is the root's record of lane ledger-fix")


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
        "Warning, nothing was blocked: Task #62 is the root's record of lane ledger-auto-register; the root completes it once it has consumed "
        "your deliverable. SendMessage team-lead the deliverable instead and leave the task open."
    )
    assert gate(drive, "62", status="in_progress") is None
    assert gate(drive, "90") is None


def test_owner_ask_soon_after_a_task_create_is_not_flagged(drive: Drive) -> None:
    drive.tool("TaskCreate", {"subject": "Earlier ask", "description": "x"})
    drive.say("fix the ledger hook")

    assert [drive.bash(), drive.bash(), drive.bash()] == [None, None, None]
    drive.say("also sweep every monitor")
    assert drive.stop() == []

    with task_list.AskState.mutate(drive.event(StopEvent)) as state:
        state.created_at -= task_list.ASK_GRACE_SECONDS
    drive.say("fix the ledger hook again")
    assert [drive.bash(), drive.bash(), drive.bash()] == [None, None, ASK_LINE]


def test_task_completion_reads_only_selected_native_record(drive: Drive, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(64):
        drive.task(str(index), f"Task {index}", owner="selected-lane" if index == 17 else None)
    original = Path.read_text
    reads: list[Path] = []

    def read_text(path: Path, *args, **kwargs) -> str:
        if path.parent == drive.tasks:
            reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    assert "lane selected-lane" in gate(drive, "17")
    assert reads == [drive.tasks / "17.json"]
    reads.clear()
    drive.task("17", "Task 17")
    assert gate(drive, "17") is None
    assert reads == [drive.tasks / "17.json"]
    reads.clear()
    (drive.tasks / "17.json").unlink()
    assert gate(drive, "17") is None
    assert reads == [drive.tasks / "17.json"]
