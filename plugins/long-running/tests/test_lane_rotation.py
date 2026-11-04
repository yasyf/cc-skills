from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cc_transcript import Session
from captain_hook.events import PreToolUseEvent
from captain_hook.testing.helpers import build_context
from fire import fire
from hooks import lane_rotation
from hooks.compaction_handoff import CompactionState

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


def tool_use(tree: Tree, tool: str, tool_input: dict, agent_id: str | None = None) -> list:
    raw = {"session_id": "0123456789abcdef", "transcript_path": str(tree.root), "cwd": str(tree.claude.parent)}
    ctx = build_context(transcript=Session.from_path(tree.root), session_dir=tree.claude / "session")
    raw |= {"tool_name": tool, "tool_input": tool_input} | ({"agent_id": agent_id} if agent_id else {})
    evt = PreToolUseEvent(_raw=raw, ctx=ctx)
    CompactionState(active=True, coordinator=True).save(evt)
    return fire(lane_rotation, evt)


def registry(monkeypatch: pytest.MonkeyPatch, routes: dict[str, str]) -> list[str]:
    asked: list[str] = []

    def routed(evt, lane: str) -> dict:
        asked.append(lane)
        return {"lane": lane, "route": routes.get(lane, lane), "state": "retired" if lane in routes else "desired"}

    monkeypatch.setattr(lane_rotation, "routed", routed)
    return asked


def send(tree: Tree, to: str, message: str = "status?", sender: str | None = "aoncall-dedupe-fix-2"):
    [result] = tool_use(tree, "SendMessage", {"to": to, "message": message}, sender) or [None]
    return result


def test_a_send_to_a_lane_the_registry_routes_on_goes_to_the_successor_its_row_names_whatever_its_name(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    """cc-skills#1311 and #1335: a message to a stood-down lane reaches the lane that took its work, read from the supervisor's registry row, never from this session's spawns or a numbered name."""
    registry(monkeypatch, {"oncall-connect": "restack-desk"})

    result = send(tree, "oncall-connect", sender=None)

    assert (result.action, result.updated_input["to"]) == ("rewrite", "restack-desk")
    assert result.note == "`oncall-connect` is stood down in the lane supervisor's registry, so this message went to its successor `restack-desk`, which owns its work. `TaskStop` `oncall-connect` if it still runs."


def test_a_lanes_send_to_a_lane_retired_with_no_successor_goes_to_main(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    """cc-skills#1311: a worker's report to a lane stood down with no successor goes to main instead of resuming it on its stale brief."""
    registry(monkeypatch, {"desk": "root"})

    result = send(tree, "desk", "done: #12 merged")

    assert (result.action, result.updated_input["to"]) == ("rewrite", "main")


@pytest.mark.parametrize("message", ["rulings-hygiene-2 reported; stand down and send nothing further.", "STAND-DOWN: rulings-hygiene-2 owns it."])
def test_a_stand_down_to_a_retired_lane_is_warned_and_never_forwarded_to_its_successor(tree: Tree, monkeypatch: pytest.MonkeyPatch, message: str) -> None:
    registry(monkeypatch, {"rulings-hygiene": "rulings-hygiene-2"})

    assert send(tree, "rulings-hygiene", message, sender=None).action == "warn"


def post(tree: Tree, to: str, kind: str = "decide") -> list:
    return tool_use(tree, "Bash", {"command": f"cci post --lane root --kind {kind} --to {to} --text x"})


def test_an_alias_a_live_lane_holds_gets_no_warning_on_an_urgent_post(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    registry(monkeypatch, {"desks-move": "desks-move-1"})

    assert post(tree, "desks-move") == []


@pytest.mark.parametrize("kind", ["decide", "report"])
def test_a_numbered_post_warns_once_naming_the_alias(tree: Tree, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    registry(monkeypatch, {"desks-move": "desks-move-2", "desks-move-1": "desks-move-2"})

    [warned] = post(tree, "desks-move-1", kind)

    assert warned.action == "warn" and "`--to desks-move`" in warned.message and "desks-move-2" not in warned.message
    assert len(warned.message) < 300


def test_a_stood_down_lanes_warning_names_its_successors_alias(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    registry(monkeypatch, {"s3-errors": "restack-desk-4"})

    [warned] = post(tree, "s3-errors")

    assert "Post it to its successor `restack-desk`" in warned.message and "restack-desk-4" not in warned.message


def test_a_numbered_name_no_live_lane_answers_for_passes(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    registry(monkeypatch, {})

    assert post(tree, "phase-2", "report") == []


def test_a_failed_route_read_passes_silently(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(*args, **kwargs):
        raise subprocess.TimeoutExpired("lr", 10)

    monkeypatch.setattr(lane_rotation, "drive_entry", lambda evt: {"state_dir": str(tree.claude)})
    monkeypatch.setattr(lane_rotation.subprocess, "run", failing)

    assert post(tree, "desks-move-1") == []


def test_a_numbered_spawn_reroutes_nothing_the_registry_does_not_link(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    """The name-matching successor lookup is gone: `desk-4` existing says nothing about where `desk-3`'s mail goes."""
    asked = registry(monkeypatch, {})
    tree.lane("desk-3", 450_000)
    tree.lane("desk-4", 80_000)

    assert send(tree, "desk-3", sender=None) is None and asked == ["desk-3"]
