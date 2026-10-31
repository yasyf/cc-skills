from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cc_transcript import Session
from captain_hook.events import PostToolUseEvent, PreToolUseEvent, StopEvent
from captain_hook.testing.helpers import build_context
from fire import fire
from hooks import lane_rotation

ROOT_AT = datetime(2026, 9, 25, 21, 35, tzinfo=UTC)
TEAM = "session-rot"
TASK_LIST = "rot-list"


def stamp(at: datetime) -> str:
    return at.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def envelope(kind: str, at: datetime, *, sidechain: bool) -> dict:
    return {"type": kind, "isSidechain": sidechain, "timestamp": stamp(at), "uuid": str(uuid.uuid4()), "parentUuid": None, "sessionId": "0123456789abcdef"}


def user(at: datetime, content: str, *, sidechain: bool = False) -> dict:
    return envelope("user", at, sidechain=sidechain) | {"message": {"role": "user", "content": content}}


def assistant(at: datetime, tokens: int, *, sidechain: bool, model: str = "claude-opus-5-5") -> dict:
    usage = {"input_tokens": 2, "output_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": tokens - 2}
    message = {"role": "assistant", "model": model, "usage": usage, "content": [{"type": "text", "text": "ok"}]}
    return envelope("assistant", at, sidechain=sidechain) | {"message": message}


class Tree:
    def __init__(self, home: Path) -> None:
        self.claude = home / ".claude"
        self.root = self.claude / "projects" / "p" / "root.jsonl"
        write_jsonl(self.root, [assistant(ROOT_AT, 300_000, sidechain=False)])
        (self.claude / "teams" / TEAM / "inboxes").mkdir(parents=True)
        self.tasks = self.claude / "tasks" / TASK_LIST
        self.tasks.mkdir(parents=True)
        self.members: dict[str, list[str]] = {}

    def lane(
        self,
        name: str,
        tokens: int,
        *,
        behind: timedelta = timedelta(minutes=5),
        team: str | None = TEAM,
        model: str = "claude-opus-5-5",
        hint: str | None = None,
        description: str | None = None,
        prompt: str | None = None,
        spawned: datetime = ROOT_AT - timedelta(hours=3),
        status: str | None = "in_progress",
    ) -> dict:
        if status:
            self.task(name, status)
        agent_id = f"a{name}-{hashlib.sha1(f'{name}{spawned}'.encode()).hexdigest()[:16]}"
        subagents = self.root.with_suffix("") / "subagents"
        meta = {"agentType": "general-purpose", "description": description or f"{name} lane", "name": name}
        meta |= {"model": hint or (f"{model}[1m]" if model.startswith("claude-opus") else model)}
        if team:
            meta |= {"taskKind": "in_process_teammate", "teamName": team}
        if team:
            self.members.setdefault(team, []).append(name)
            self.roster(team)
        subagents.mkdir(parents=True, exist_ok=True)
        (subagents / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta))
        content = f'<teammate-message teammate_id="team-lead" summary="{meta["description"]}">\n{prompt}\n</teammate-message>' if prompt else "go"
        first = user(spawned, content, sidechain=True)
        write_jsonl(subagents / f"agent-{agent_id}.jsonl", [first, assistant(ROOT_AT - behind, tokens, sidechain=True, model=model)])
        label = (prompt[:50] + "..." if len(prompt) > 50 else prompt) if prompt else meta["description"]
        return {"id": f"t-{name}" if team else agent_id, "type": "teammate" if team else "subagent", "status": "running", "description": label}

    def task(self, name: str, status: str) -> None:
        raw = {"id": name, "subject": f"Drive the {name} work", "status": status, "owner": name}
        (self.tasks / f"{name}.json").write_text(json.dumps(raw))

    def roster(self, team: str) -> None:
        members = [{"agentId": f"{member}@{team}", "name": member} for member in self.members[team]]
        (self.claude / "teams" / team).mkdir(parents=True, exist_ok=True)
        (self.claude / "teams" / team / "config.json").write_text(json.dumps({"name": team, "members": members}))

@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Tree:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_AUTO_COMPACT_WINDOW", raising=False)
    monkeypatch.delenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", raising=False)
    monkeypatch.delenv("LONG_RUNNING_LANE_ROTATE_TOKENS", raising=False)
    monkeypatch.setenv("CAPTAIN_HOOK_TASKS_DIR", str(tmp_path / ".claude" / "tasks"))
    monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", TASK_LIST)
    built = Tree(tmp_path)
    (built.claude / "settings.json").write_text(json.dumps({"autoCompactWindow": 600_000}))
    return built


@dataclass
class Stop:
    tree: Tree
    tasks: list[dict]

    def evt(self) -> StopEvent:
        raw = {"session_id": "0123456789abcdef", "transcript_path": str(self.tree.root), "cwd": str(self.tree.claude.parent)}
        ctx = build_context(transcript=Session.from_path(self.tree.root), session_dir=self.tree.claude / "session")
        return StopEvent(_raw=raw | {"background_tasks": self.tasks}, ctx=ctx)


def tool_use(tree: Tree, event: type[PreToolUseEvent | PostToolUseEvent], tool: str, tool_input: dict, agent_id: str | None = None) -> list:
    raw = {"session_id": "0123456789abcdef", "transcript_path": str(tree.root), "cwd": str(tree.claude.parent)}
    ctx = build_context(transcript=Session.from_path(tree.root), session_dir=tree.claude / "session")
    raw |= {"tool_name": tool, "tool_input": tool_input} | ({"agent_id": agent_id} if agent_id else {})
    return fire(lane_rotation, event(_raw=raw, ctx=ctx))


def spawn(tree: Tree, name: str) -> None:
    tree.lane(name, 80_000)
    assert tool_use(tree, PostToolUseEvent, "Agent", {"name": name, "prompt": "go"}) == []


def send(tree: Tree, to: str, message: str = "status?", sender: str | None = "aoncall-dedupe-fix-2") -> tuple[str, str] | None:
    [result] = tool_use(tree, PreToolUseEvent, "SendMessage", {"to": to, "message": message}, sender) or [None]
    return result and (result.action, result.message)


def verdict(tree: Tree, to: str, message: str = "status?", sender: str | None = "aoncall-dedupe-fix-2") -> str | None:
    return (sent := send(tree, to, message, sender)) and sent[0]


def rotate(tree: Tree, old: str, successor: str) -> None:
    tree.lane(old, 450_000)
    tree.lane(f"{old}-handoff", 80_000, team=None, status=None)
    assert tool_use(tree, PostToolUseEvent, "TaskStop", {"task_id": old}) == []
    spawn(tree, successor)


def redirected(tree: Tree, to: str, sender: str | None) -> tuple[dict, str]:
    [result] = tool_use(tree, PreToolUseEvent, "SendMessage", {"to": to, "message": "status?"}, sender)
    assert result.action == "rewrite"
    return result.updated_input, result.note


def test_a_roots_send_to_a_rotated_lane_goes_to_its_successor_and_names_task_stop(tree: Tree) -> None:
    rotate(tree, "oncall-connect", "oncall-connect-2")

    assert redirected(tree, "oncall-connect", None) == (
        {"to": "oncall-connect-2", "message": "status?"},
        "`oncall-connect` is stood down, so this message went to its successor `oncall-connect-2`, which owns its work. "
        "`TaskStop` `oncall-connect` if it still runs.",
    )
    assert send(tree, "oncall-connect-2", sender=None) is None


def test_a_lanes_send_to_a_rotated_lane_goes_to_its_successor_without_naming_task_stop(tree: Tree) -> None:
    rotate(tree, "oncall-connect", "oncall-connect-2")

    updated, note = redirected(tree, "oncall-connect", "aoncall-dedupe-fix-2")

    assert (updated["to"], "TaskStop" in note) == ("oncall-connect-2", False)


@pytest.mark.parametrize("message", ["rulings-hygiene-2 reported; stand down and send nothing further.", "STAND-DOWN: rulings-hygiene-2 owns it."])
def test_a_stand_down_to_a_rotated_lane_is_warned_and_never_forwarded_to_its_successor(tree: Tree, message: str) -> None:
    rotate(tree, "rulings-hygiene", "rulings-hygiene-2")

    assert verdict(tree, "rulings-hygiene", message, sender=None) == "warn"


def test_a_successor_brief_naming_its_predecessor_rotates_it_without_a_handoff(tree: Tree) -> None:
    tree.lane("creds-5", 450_000)
    tree.lane("creds-6", 80_000)
    prompt = "Replaces: creds-5\nRun `drive.py lane-status creds-5` before any write."
    assert tool_use(tree, PostToolUseEvent, "Agent", {"name": "creds-6", "prompt": prompt}) == []

    assert lane_rotation.RotationState.load(Stop(tree, []).evt()).retired == {"creds-5": "creds-6"}
    assert redirected(tree, "creds-5", None)[0]["to"] == "creds-6"


def test_a_rotation_records_the_successor_in_cci_so_every_senders_records_reach_it(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive watchdog #235092: 62 records from desks and scripts went to board-refresher-2 after it rotated, because only this session's SendMessage calls followed the rotation."""
    posted: list[list[str]] = []

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess:
        posted.extend([argv] if argv[:2] == ["cci", "post"] else [])
        return subprocess.CompletedProcess(argv, 0, "#9\n", "")

    monkeypatch.setattr(lane_rotation, "drive_entry", lambda evt: {"drive": "d1", "state_dir": "/state/d1"})
    monkeypatch.setattr(lane_rotation.subprocess, "run", run)
    tree.lane("creds-5", 450_000)
    tree.lane("creds-6", 80_000)

    tool_use(tree, PostToolUseEvent, "Agent", {"name": "creds-6", "prompt": "Replaces: creds-5\ngo"})

    assert [argv[argv.index("--to") + 1 : argv.index("--successor") + 2] for argv in posted] == [["creds-5", "--successor", "creds-6"]]
    assert all(argv[argv.index("--kind") + 1] == "stand-down" and argv[argv.index("--drive") + 1] == "d1" for argv in posted)


def test_a_respawn_under_a_rotated_name_clears_its_redirect(tree: Tree) -> None:
    rotate(tree, "desk-3", "desk-4")
    assert verdict(tree, "desk-3", sender=None) == "rewrite"
    spawn(tree, "desk-3")
    assert send(tree, "desk-3", sender=None) is None

    spawn(tree, "desk-4")

    assert verdict(tree, "desk-3", sender=None) == "rewrite"


def test_second_rotation_redirects_to_the_newest_successor(tree: Tree) -> None:
    tree.lane("desk-3", 450_000)
    tree.lane("desk-3-handoff", 80_000, team=None, status=None)
    spawn(tree, "desk-4")
    tree.lane("desk-4-handoff", 80_000, team=None, status=None)
    spawn(tree, "desk-5")

    assert redirected(tree, "desk-3", "aoncall-dedupe-fix-2")[0]["to"] == "desk-5"


def test_a_workers_report_to_a_lane_stopped_with_no_successor_goes_to_main_and_the_roots_resume_passes(tree: Tree) -> None:
    reviewer = tree.lane("reviewer", 120_000, team=None)
    assert tool_use(tree, PostToolUseEvent, "TaskStop", {"task_id": reviewer["id"]}) == []

    assert redirected(tree, "reviewer", "aoncall-dedupe-fix-2") == (
        {"to": "main", "message": "status?"},
        "`reviewer` is stood down with no successor, so this message went to `main` instead of resuming it on its stale brief.",
    )
    assert send(tree, "reviewer", sender=None) is None


def test_stand_down_without_a_handoff_retires_the_lane(tree: Tree) -> None:
    tree.lane("alerts-watch", 450_000)
    assert tool_use(tree, PostToolUseEvent, "SendMessage", {"to": "alerts-watch", "message": "STAND-DOWN: done."}) == []

    assert redirected(tree, "alerts-watch", "aoncall-dedupe-fix-2")[0]["to"] == "main"


def test_numbered_sibling_without_a_handoff_retires_nothing(tree: Tree) -> None:
    tree.lane("memdoc-disposition-1", 450_000)
    spawn(tree, "memdoc-disposition-2")

    assert send(tree, "memdoc-disposition-1") is None


def test_respawn_under_a_retired_name_clears_it(tree: Tree) -> None:
    tree.lane("oncall-watch", 450_000)
    assert tool_use(tree, PostToolUseEvent, "TaskStop", {"task_id": "oncall-watch@session-rot"}) == []
    spawn(tree, "oncall-watch")

    assert send(tree, "oncall-watch") is None
