from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from captain_hook.events import StopEvent, UserPromptSubmitEvent
from captain_hook.testing.helpers import build_context

from hooks import lane_rotation, nudges, turns
from hooks.compaction_handoff import CompactionState

ROOT_AT = datetime(2026, 9, 25, 21, 35, tzinfo=UTC)
TEAM = "session-rot"
DESK = "alanding-desk-1a1a1a1a1a1a1a1a"


def stamp(at: datetime) -> str:
    return at.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def assistant(at: datetime, tokens: int, *, sidechain: bool, model: str = "claude-opus-5-5") -> dict:
    usage = {"input_tokens": 2, "cache_creation_input_tokens": 0, "cache_read_input_tokens": tokens - 2}
    return {"type": "assistant", "isSidechain": sidechain, "timestamp": stamp(at), "message": {"model": model, "usage": usage}}


class Tree:
    def __init__(self, home: Path) -> None:
        self.claude = home / ".claude"
        self.root = self.claude / "projects" / "p" / "root.jsonl"
        write_jsonl(self.root, [assistant(ROOT_AT, 300_000, sidechain=False)])
        (self.claude / "teams" / TEAM / "inboxes").mkdir(parents=True)
        self.members: list[str] = []

    def lane(
        self,
        name: str,
        tokens: int,
        *,
        behind: timedelta = timedelta(minutes=5),
        team: str | None = TEAM,
        model: str = "claude-opus-5-5",
        description: str | None = None,
        spawned: datetime = ROOT_AT - timedelta(hours=3),
    ) -> dict:
        agent_id = f"a{name}-{hashlib.sha1(f'{name}{spawned}'.encode()).hexdigest()[:16]}"
        subagents = self.root.with_suffix("") / "subagents"
        meta = {"agentType": "general-purpose", "description": description or f"{name} lane", "name": name}
        meta |= {"model": f"{model}[1m]"} if model.startswith("claude-opus") else {"model": model}
        if team:
            meta |= {"taskKind": "in_process_teammate", "teamName": team}
            self.members.append(name)
            (self.claude / "teams" / team / "config.json").write_text(
                json.dumps({"name": team, "members": [{"name": member} for member in self.members]})
            )
        subagents.mkdir(parents=True, exist_ok=True)
        (subagents / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta))
        first = {"type": "user", "isSidechain": True, "timestamp": stamp(spawned), "message": {"content": "go"}}
        write_jsonl(subagents / f"agent-{agent_id}.jsonl", [first, assistant(ROOT_AT - behind, tokens, sidechain=True, model=model)])
        return {"id": f"t-{name}", "type": "teammate" if team else "subagent", "status": "running", "description": meta["description"]}

    def inbox(self, name: str) -> list[dict]:
        path = self.claude / "teams" / TEAM / "inboxes" / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else []


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Tree:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_AUTO_COMPACT_WINDOW", raising=False)
    monkeypatch.delenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", raising=False)
    monkeypatch.delenv("LONG_RUNNING_LANE_ROTATE_TOKENS", raising=False)
    built = Tree(tmp_path)
    (built.claude / "settings.json").write_text(json.dumps({"autoCompactWindow": 600_000}))
    return built


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [ROOT_AT.timestamp()]
    monkeypatch.setattr(lane_rotation.time, "time", lambda: now[0])
    return now


def stop(tree: Tree, tasks: list[dict]) -> StopEvent:
    raw = {"session_id": "0123456789abcdef", "transcript_path": str(tree.root), "cwd": str(tree.claude.parent)}
    evt = StopEvent(_raw=raw | {"background_tasks": tasks}, ctx=build_context(session_dir=tree.claude / "session"))
    CompactionState(active=True).save(evt)
    return evt


def prompt(tree: Tree, text: str) -> UserPromptSubmitEvent:
    raw = {"session_id": "0123456789abcdef", "prompt": text}
    return UserPromptSubmitEvent(_raw=raw, ctx=build_context(session_dir=tree.claude / "session"))


def pending(evt) -> list[str]:
    return nudges.NudgeState.load(evt).pending


def test_45_lanes_over_the_line_get_three_inbox_asks_highest_first(tree: Tree, clock: list[float]) -> None:
    tasks = [tree.lane(f"lane-{i:02}", 400_000 + i * 1_000) for i in range(45)]
    evt = stop(tree, tasks)

    assert lane_rotation.rotate_lanes(evt) is None
    asked = sorted(f"lane-{i:02}" for i in range(45) if tree.inbox(f"lane-{i:02}"))
    assert asked == ["lane-42", "lane-43", "lane-44"]
    [message] = tree.inbox("lane-44")
    assert list(message) == ["from", "text", "summary", "timestamp", "msgV", "msg_id", "type", "read"]
    assert (message["from"], message["text"], message["msgV"], message["type"], message["read"]) == (
        "long-running",
        lane_rotation.ROTATE,
        1,
        "message",
        False,
    )
    assert pending(evt) == []

    clock[0] += 60
    lane_rotation.rotate_lanes(evt)
    assert sum(bool(tree.inbox(f"lane-{i:02}")) for i in range(45)) == 3

    clock[0] += lane_rotation.PACE_SECONDS
    lane_rotation.rotate_lanes(evt)
    assert sorted(f"lane-{i:02}" for i in range(45) if tree.inbox(f"lane-{i:02}"))[:3] == ["lane-39", "lane-40", "lane-41"]


def test_team_config_only_lane_is_never_touched(tree: Tree, clock: list[float]) -> None:
    tree.lane("stopped", 550_000)
    (tree.claude / "teams" / TEAM / "inboxes" / "stopped.json").write_text("[]")

    lane_rotation.rotate_lanes(stop(tree, []))

    assert tree.inbox("stopped") == []


def test_dormant_lane_is_skipped(tree: Tree, clock: list[float]) -> None:
    tasks = [tree.lane("sleepy", 550_000, behind=timedelta(hours=1, minutes=1)), tree.lane("awake", 550_000)]

    lane_rotation.rotate_lanes(stop(tree, tasks))

    assert (tree.inbox("sleepy"), len(tree.inbox("awake"))) == ([], 1)


def test_lane_is_asked_twice_at_most_thirty_minutes_apart(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("desk", 450_000)])
    counts = []
    for minutes in (0, 20, 11, 60):
        clock[0] += minutes * 60
        lane_rotation.rotate_lanes(evt)
        counts.append(len(tree.inbox("desk")))
    assert counts == [1, 1, 2, 2]


def test_subagent_lane_without_an_inbox_gets_one_root_nudge(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("reviewer", 420_000, team=None, description="Reviewer")])

    lane_rotation.rotate_lanes(evt)
    lane_rotation.rotate_lanes(evt)

    assert pending(evt) == [
        "Lanes over their rotation line with no teammate inbox: `reviewer` (420,000). "
        f"SendMessage each `{lane_rotation.ROTATE}` when convenient."
    ]


def test_respawned_lane_is_read_from_its_newest_transcript(tree: Tree, clock: list[float]) -> None:
    tree.lane("desk", 550_000, description="Landing desk", spawned=ROOT_AT - timedelta(hours=5))
    task = tree.lane("desk", 150_000, description="Landing desk", spawned=ROOT_AT - timedelta(minutes=30))

    lane_rotation.rotate_lanes(stop(tree, [task]))

    assert tree.inbox("desk") == []


def test_flushed_reply_queues_one_nudge(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("landing-desk", 450_000)])
    lane_rotation.rotate_lanes(evt)
    reply = (
        "Another Claude session sent a message:\n"
        '<teammate-message teammate_id="landing-desk" color="blue" summary="flushed b037decf c2b3a6c">\n'
        "flushed b037decf c2b3a6c. The worktree is clean.\n</teammate-message>\n\n"
        '<teammate-message teammate_id="landing-desk" color="blue">\n'
        '{"type":"idle_notification","from":"landing-desk","timestamp":"2026-09-25T21:36:11.572Z",'
        '"idleReason":"available","result":"flushed b037decf"}\n</teammate-message>\n\n'
        '<teammate-message teammate_id="poller" color="red">\nflushed 74de6071\n</teammate-message>'
    )

    for _ in range(2):
        assert lane_rotation.nudge_flushed(prompt(tree, reply)) is None

    assert pending(evt) == [
        "lane landing-desk flushed (b037decf, c2b3a6c): TaskStop it and respawn it from its handoff note "
        "at a natural pause"
    ]
    clock[0] += 2 * lane_rotation.ASK_GAP_SECONDS
    lane_rotation.rotate_lanes(evt)
    assert len(tree.inbox("landing-desk")) == 1


def test_unflushed_reply_queues_nothing(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("landing-desk", 450_000)])
    lane_rotation.rotate_lanes(evt)

    lane_rotation.nudge_flushed(
        prompt(tree, '<teammate-message teammate_id="landing-desk">\nstill working on #25751\n</teammate-message>')
    )

    assert pending(evt) == []


@pytest.mark.parametrize(
    ("model", "env", "line"),
    [
        ("claude-opus-5-5", None, 396_900),
        ("claude-haiku-4-5-20251001", None, 116_900),
        ("claude-opus-5-5", "250000", 250_000),
    ],
)
def test_rotation_line_follows_the_lanes_compaction_threshold(
    tree: Tree, monkeypatch: pytest.MonkeyPatch, model: str, env: str | None, line: int
) -> None:
    if env:
        monkeypatch.setenv("LONG_RUNNING_LANE_ROTATE_TOKENS", env)
    evt = stop(tree, [tree.lane("desk", 450_000, model=model)])

    [lane] = lane_rotation.live_lanes(evt)

    assert lane.line == line


def test_compact_boundary_after_the_last_turn_reports_post_compact_tokens(tmp_path: Path) -> None:
    transcript = tmp_path / "lane.jsonl"
    boundary = {
        "type": "system",
        "subtype": "compact_boundary",
        "isSidechain": True,
        "timestamp": stamp(ROOT_AT),
        "compactMetadata": {"trigger": "auto", "preTokens": 570_709, "postTokens": 18_816},
    }
    write_jsonl(transcript, [assistant(ROOT_AT - timedelta(minutes=1), 568_591, sidechain=True), boundary])

    assert turns.latest_turn(transcript, sidechain=True) == turns.Turn("claude-opus-5-5", 18_816, ROOT_AT)

    write_jsonl(transcript, [boundary, assistant(ROOT_AT + timedelta(minutes=1), 24_000, sidechain=True)])
    assert turns.latest_turn(transcript, sidechain=True).tokens == 24_000


def test_inbox_append_matches_claude_code_format(tmp_path: Path) -> None:
    inbox = tmp_path / "inboxes" / "desk.json"
    inbox.parent.mkdir()
    inbox.write_text(json.dumps([{"from": "team-lead", "text": "héllo", "timestamp": "t", "type": "message", "read": False}], indent=2))

    assert lane_rotation.append_inbox(inbox, {"from": "long-running", "text": "ROTATE"})

    assert inbox.read_text() == json.dumps(
        [{"from": "team-lead", "text": "héllo", "timestamp": "t", "type": "message", "read": False}, {"from": "long-running", "text": "ROTATE"}],
        indent=2,
        ensure_ascii=False,
    )
    assert sorted(path.name for path in inbox.parent.iterdir()) == ["desk.json"]


def test_inbox_append_creates_a_missing_inbox(tmp_path: Path) -> None:
    inbox = tmp_path / "inboxes" / "desk.json"

    assert lane_rotation.append_inbox(inbox, {"text": "ROTATE"})

    assert json.loads(inbox.read_text()) == [{"text": "ROTATE"}]


def test_held_lock_skips_the_append(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lane_rotation, "LOCK_MAX_DELAY", 0.001)
    inbox = tmp_path / "inboxes" / "desk.json"
    inbox.parent.mkdir()
    inbox.write_text("[]")
    (tmp_path / "inboxes" / "desk.json.lock").mkdir()

    assert not lane_rotation.append_inbox(inbox, {"text": "ROTATE"})

    assert (inbox.read_text(), (tmp_path / "inboxes" / "desk.json.lock").is_dir()) == ("[]", True)


def test_stale_lock_is_broken(tmp_path: Path) -> None:
    inbox = tmp_path / "inboxes" / "desk.json"
    inbox.parent.mkdir()
    inbox.write_text("[]")
    lock = tmp_path / "inboxes" / "desk.json.lock"
    lock.mkdir()
    old = lane_rotation.time.time() - lane_rotation.LOCK_STALE_SECONDS - 1
    os.utime(lock, (old, old))

    assert lane_rotation.append_inbox(inbox, {"text": "ROTATE"})

    assert (json.loads(inbox.read_text()), lock.exists()) == ([{"text": "ROTATE"}], False)


def test_concurrent_appends_keep_every_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lane_rotation, "LOCK_RETRIES", 1000)
    inbox = tmp_path / "inboxes" / "desk.json"
    inbox.parent.mkdir()
    inbox.write_text("[]")
    writers = [threading.Thread(target=lane_rotation.append_inbox, args=(inbox, {"n": n})) for n in range(20)]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join()

    assert sorted(message["n"] for message in json.loads(inbox.read_text())) == list(range(20))
