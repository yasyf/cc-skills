from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import uuid
import weakref
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from cc_transcript import Session, parse
from cc_transcript.models import TranscriptEvent
from captain_hook.app import _state
from captain_hook.events import PostToolUseEvent, PreToolUseEvent, StopEvent
from captain_hook.testing.helpers import build_context
from captain_hook.transcripts import lift_session
from captain_hook.util import reqenv
from captain_hook.util.caching import StampedCache
from fire import fire
from hooks import lane_rotation, nudges, session_tree, task_list, turns
from hooks.compaction_handoff import TURN_WINDOW, CompactionState

ROOT_AT = datetime(2026, 9, 25, 21, 35, tzinfo=UTC)
TEAM = "session-rot"
TASK_LIST = "rot-list"
DESK = "alanding-desk-1a1a1a1a1a1a1a1a"
GOLDEN = Path(__file__).resolve().parents[1] / "capt-hook" / "hooks" / "tests" / "fixtures" / "rotation" / "golden"


class Events(list):
    pass


class Indexed(Sequence):
    def __init__(self, events: Sequence[TranscriptEvent]) -> None:
        self.events = events
        self.read: list[int] = []

    def __len__(self) -> int:
        return len(self.events)

    def __getitem__(self, index: int) -> TranscriptEvent:
        self.read.append(index)
        return self.events[index]

    def __iter__(self) -> Iterator[TranscriptEvent]:
        raise AssertionError("turn_of walked the transcript from the front")

    def __reversed__(self) -> Iterator[TranscriptEvent]:
        raise AssertionError("turn_of materialized the transcript to reverse it")


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


def retokened(transcript: str, tokens: int, replacement: int) -> str:
    return transcript.replace(f'"cache_read_input_tokens": {tokens - 2}', f'"cache_read_input_tokens": {replacement - 2}')


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

    def delete_task(self, name: str) -> None:
        (self.tasks / f"{name}.json").unlink()

    def transcript(self, name: str) -> Path:
        return max((self.root.with_suffix("") / "subagents").glob(f"agent-a{name}-*.jsonl"), key=lambda path: path.stat().st_mtime)

    def grow(self, name: str, tokens: int) -> None:
        with self.transcript(name).open("a") as lane:
            lane.write(json.dumps(assistant(ROOT_AT, tokens, sidechain=True)) + "\n")

    def roster(self, team: str) -> None:
        members = [{"agentId": f"{member}@{team}", "name": member} for member in self.members[team]]
        (self.claude / "teams" / team).mkdir(parents=True, exist_ok=True)
        (self.claude / "teams" / team / "config.json").write_text(json.dumps({"name": team, "members": members}))

    def read(self, *names: str) -> None:
        for name in names:
            with self.transcript(name).open("a") as lane:
                lane.write(json.dumps(user(ROOT_AT, "ROTATE", sidechain=True)) + "\n")

    def hear(self, name: str, content: str) -> None:
        with self.transcript(name).open("a") as lane:
            lane.write(json.dumps(user(ROOT_AT, content, sidechain=True)) + "\n")
        (self.claude / "teams" / TEAM / "inboxes" / f"{name}.json").write_text("[]")

    def kill(self, name: str, team: str = TEAM) -> None:
        self.members[team].remove(name)
        self.roster(team)

    def inbox(self, name: str) -> list[dict]:
        path = self.claude / "teams" / TEAM / "inboxes" / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else []


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


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [ROOT_AT.timestamp()]
    monkeypatch.setattr(lane_rotation.time, "time", lambda: now[0])
    return now


@pytest.fixture
def parsed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    names: list[str] = []
    parse_transcript = session_tree.parse

    def recording(path: Path):
        names.append(path.stem.removeprefix("agent-a").rsplit("-", 1)[0])
        return parse_transcript(path)

    monkeypatch.setattr(session_tree, "parse", recording)
    return names


@dataclass
class Stop:
    tree: Tree
    tasks: list[dict]
    window: int | None = None

    def evt(self) -> StopEvent:
        raw = {"session_id": "0123456789abcdef", "transcript_path": str(self.tree.root), "cwd": str(self.tree.claude.parent)}
        transcript = Session.from_path(self.tree.root)
        if self.window:
            transcript = lift_session(transcript.events[-self.window :], path=self.tree.root)
        ctx = build_context(transcript=transcript, session_dir=self.tree.claude / "session")
        return StopEvent(_raw=raw | {"background_tasks": self.tasks}, ctx=ctx)


def stop(tree: Tree, tasks: list[dict]) -> Stop:
    handle = Stop(tree, tasks)
    CompactionState(active=True).save(handle.evt())
    return handle


def rotate_lanes(handle: Stop) -> None:
    assert fire(lane_rotation, handle.evt()) == []


def request(tree: Tree) -> reqenv.RequestOverrides:
    return reqenv.RequestOverrides(env=dict(os.environ), cwd=str(tree.claude.parent), client_ppid=os.getppid(), session_id="0123456789abcdef")


def deliver(tree: Tree, text: str, at: datetime) -> None:
    with tree.root.open("a") as transcript:
        transcript.write(json.dumps(user(at, text)) + "\n")
        transcript.write(json.dumps(assistant(at + timedelta(seconds=5), 300_000, sidechain=False)) + "\n")


def pending(handle: Stop) -> list[str]:
    return nudges.NudgeState.load(handle.evt()).pending


def timeline(handle: Stop) -> list[dict]:
    return lane_rotation.RotationState.load(handle.evt()).timeline


def test_45_lanes_over_the_line_get_three_inbox_asks_highest_first(tree: Tree, clock: list[float]) -> None:
    tasks = [tree.lane(f"lane-{i:02}", 400_000 + i * 1_000) for i in range(45)]
    evt = stop(tree, tasks)

    rotate_lanes(evt)
    asked = sorted(f"lane-{i:02}" for i in range(45) if tree.inbox(f"lane-{i:02}"))
    assert asked == ["lane-42", "lane-43", "lane-44"]
    [message] = tree.inbox("lane-44")
    assert list(message) == ["from", "text", "summary", "timestamp", "msgV", "msg_id", "type", "read"]
    assert (message["from"], message["text"], message["summary"], message["msgV"], message["type"], message["read"]) == (
        "long-running",
        'ROTATE: record anything not yet in the ledger or cc-notes, reply "flushed <ids>" to team-lead, then keep working.',
        "ROTATE: flush and keep working",
        1,
        "message",
        False,
    )
    assert pending(evt) == []

    clock[0] += 60
    rotate_lanes(evt)
    assert sum(bool(tree.inbox(f"lane-{i:02}")) for i in range(45)) == 3

    clock[0] += lane_rotation.PACE_SECONDS
    rotate_lanes(evt)
    assert sorted(f"lane-{i:02}" for i in range(45) if tree.inbox(f"lane-{i:02}"))[:3] == ["lane-39", "lane-40", "lane-41"]


def test_team_config_only_lane_is_never_touched(tree: Tree, clock: list[float]) -> None:
    tree.lane("stopped", 550_000)
    (tree.claude / "teams" / TEAM / "inboxes" / "stopped.json").write_text("[]")

    rotate_lanes(stop(tree, []))

    assert tree.inbox("stopped") == []


@pytest.mark.parametrize(
    "prompt",
    [
        "You are lane `alerts-watch` for the release v3 drive (respawned after all sessions were killed).",
        "You are lane `alerts-watch`.",
    ],
)
def test_teammate_labelled_by_its_prompt_is_live(tree: Tree, clock: list[float], prompt: str) -> None:
    task = tree.lane("alerts-watch", 550_000, description="Alerts watch after restart", prompt=prompt)

    rotate_lanes(stop(tree, [task]))

    assert len(tree.inbox("alerts-watch")) == 1


def test_dormant_lane_is_skipped(tree: Tree, clock: list[float]) -> None:
    tasks = [tree.lane("sleepy", 550_000, behind=timedelta(hours=1, minutes=1)), tree.lane("awake", 550_000)]

    rotate_lanes(stop(tree, tasks))

    assert (tree.inbox("sleepy"), len(tree.inbox("awake"))) == ([], 1)


def test_unacked_lane_is_asked_again_every_thirty_minutes(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("desk", 450_000)])
    counts = []
    for minutes in (0, 20, 11, 60):
        clock[0] += minutes * 60
        rotate_lanes(evt)
        tree.read("desk")
        counts.append(len(tree.inbox("desk")))
    assert counts == [1, 1, 2, 3]


def test_subagent_lane_without_an_inbox_is_asked_through_the_root(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("reviewer", 420_000, team=None, description="Reviewer")])

    rotate_lanes(evt)
    rotate_lanes(evt)

    assert pending(evt) == [f"ROOT-ACTION `reviewer`: `SendMessage` it now, since it has no teammate inbox: `{lane_rotation.ROTATE}`"]
    assert [entry["via"] for entry in timeline(evt)] == ["root"]


def task_stop(tree: Tree, task_id: str) -> str | None:
    raw = {"session_id": "0123456789abcdef", "transcript_path": str(tree.root), "cwd": str(tree.claude.parent)}
    ctx = build_context(transcript=Session.from_path(tree.root), session_dir=tree.claude / "session")
    evt = PostToolUseEvent(_raw=raw | {"tool_name": "TaskStop", "tool_input": {"task_id": task_id}}, ctx=ctx)
    return "\n".join(result.message for result in fire(nudges, evt)) or None


@pytest.mark.parametrize("task_id", ["reviewer@session-rot", "reviewer", None])
def test_stopping_a_lane_drops_its_queued_root_action(tree: Tree, clock: list[float], task_id: str | None) -> None:
    reviewer = tree.lane("reviewer", 420_000, team=None, description="Reviewer")
    evt = stop(tree, [reviewer, tree.lane("auditor", 420_000, team=None, description="Auditor")])
    rotate_lanes(evt)

    delivered = task_stop(tree, task_id or reviewer["id"])

    assert delivered is not None
    assert "ROOT-ACTION `auditor`" in delivered
    assert "ROOT-ACTION `reviewer`" not in delivered


def test_ask_is_recorded_in_the_timeline(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("desk", 450_000)])

    rotate_lanes(evt)

    [entry] = timeline(evt)
    assert entry == {
        "at": "2026-09-25T21:35:00Z",
        "lane": "desk",
        "agent_id": entry["agent_id"],
        "event": "ask",
        "via": "inbox",
        "tokens": 450_000,
        "line": 396_900,
    }


def test_unacked_lane_escalates_to_the_root_after_the_ack_window(tree: Tree, clock: list[float]) -> None:
    task = tree.lane("alerts-watch", 512_340, spawned=ROOT_AT - timedelta(hours=5, minutes=12))
    evt = stop(tree, [task])
    rotate_lanes(evt)
    tree.read("alerts-watch")

    clock[0] += lane_rotation.ACK_WINDOW_SECONDS - 1
    rotate_lanes(evt)
    assert pending(evt) == []

    clock[0] += 1
    rotate_lanes(evt)
    assert pending(evt) == [
        f"ROOT-ACTION `alerts-watch`: Rotate it by hand: `TaskStop` `alerts-watch`, spawn a handoff subagent from "
        f"`{lane_rotation.HANDOFF_BRIEF.name}`, then its successor from the lane brief plus that handoff. "
        "Never `SendMessage` `alerts-watch`: it resumes on its stale brief."
    ]
    assert len(pending(evt)[0]) <= 300
    assert lane_rotation.HANDOFF_BRIEF.is_file()
    assert timeline(evt)[-1] | {"agent_id": None} == {
        "at": "2026-09-25T21:45:00Z",
        "lane": "alerts-watch",
        "agent_id": None,
        "event": "escalate",
        "last": "2026-09-25T21:45:00Z",
        "count": 1,
        "tokens": 512_340,
    }


def test_escalation_repeats_at_most_every_two_hours_without_piling_up(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("desk-3", 450_000)])
    rotate_lanes(evt)
    clock[0] += lane_rotation.ACK_WINDOW_SECONDS
    issued = []
    for _ in range(25):
        tree.read("desk-3")
        rotate_lanes(evt)
        issued.append(len(pending(evt)))
        nudges.NudgeState(pending=[]).save(evt.evt())
        clock[0] += 5 * 60

    assert issued == [1] + [0] * 23 + [1]
    assert [(entry["event"], entry.get("count"), entry.get("last")) for entry in timeline(evt)] == [
        ("ask", None, None),
        ("escalate", 1, "2026-09-25T21:45:00Z"),
        *[("ask", None, None)] * 4,
        ("escalate", 1, "2026-09-25T23:45:00Z"),
    ]


def test_a_new_rotate_ask_within_two_hours_does_not_reissue_the_escalation(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("desk-3", 450_000)])
    rotate_lanes(evt)
    tree.read("desk-3")
    clock[0] += lane_rotation.ACK_WINDOW_SECONDS
    rotate_lanes(evt)
    tree.read("desk-3")
    nudges.NudgeState(pending=[]).save(evt.evt())

    clock[0] += lane_rotation.ASK_GAP_SECONDS
    rotate_lanes(evt)

    assert len(tree.inbox("desk-3")) == 2
    assert pending(evt) == []


def test_flushed_reply_ends_the_escalation(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("landing-desk", 450_000)])
    rotate_lanes(evt)
    tree.read("landing-desk")
    clock[0] += lane_rotation.ACK_WINDOW_SECONDS
    rotate_lanes(evt)
    nudges.NudgeState(pending=[]).save(evt.evt())

    deliver(tree, '<teammate-message teammate_id="landing-desk">\nflushed 74de6071\n</teammate-message>', ROOT_AT + timedelta(minutes=11))
    clock[0] += lane_rotation.ACK_WINDOW_SECONDS
    rotate_lanes(evt)

    assert pending(evt) == ["Lane `landing-desk` flushed its context. Leave it running; do not `TaskStop` it."]
    assert [entry["event"] for entry in timeline(evt)] == ["ask", "escalate", "flushed"]
    assert timeline(evt)[-1]["ids"] == ["74de6071"]


def test_stopped_lane_is_recorded_gone_and_never_escalated(tree: Tree, clock: list[float]) -> None:
    task = tree.lane("alerts-watch", 450_000)
    rotate_lanes(stop(tree, [task]))

    clock[0] += lane_rotation.ACK_WINDOW_SECONDS
    evt = stop(tree, [])
    rotate_lanes(evt)

    assert pending(evt) == []
    assert [entry["event"] for entry in timeline(evt)] == ["ask", "gone"]
    assert lane_rotation.RotationState.load(evt.evt()).asks == {}


def test_compacted_lane_starts_a_fresh_cycle(tree: Tree, clock: list[float]) -> None:
    task = tree.lane("desk", 450_000)
    rotate_lanes(stop(tree, [task]))
    [transcript] = (tree.root.with_suffix("") / "subagents").glob("agent-adesk-*.jsonl")
    with transcript.open("a") as lane:
        lane.write(json.dumps(assistant(ROOT_AT, 30_000, sidechain=True)) + "\n")

    clock[0] += lane_rotation.ACK_WINDOW_SECONDS
    evt = stop(tree, [task])
    rotate_lanes(evt)

    assert pending(evt) == []
    assert [(entry["event"], entry.get("tokens")) for entry in timeline(evt)] == [("ask", 450_000), ("compacted", 30_000)]
    assert lane_rotation.RotationState.load(evt.evt()).asks == {}


def test_lanes_sharing_a_description_each_get_their_own_escalation(tree: Tree, clock: list[float]) -> None:
    shared = "Lane brief (long-running drive release-v3, root = s"
    tasks = [
        tree.lane("ccx-guard-eperm", 480_000, description=shared, behind=timedelta(minutes=1)),
        tree.lane("landing-desk-2", 440_000, description=shared, behind=timedelta(minutes=2)),
    ]
    evt = stop(tree, [tasks[1], tasks[1]])
    rotate_lanes(evt)
    tree.read("ccx-guard-eperm", "landing-desk-2")
    clock[0] += lane_rotation.ACK_WINDOW_SECONDS
    rotate_lanes(evt)

    lines = {line.split("`")[1]: line for line in pending(evt)}
    assert lines["ccx-guard-eperm"].endswith("Never `SendMessage` `ccx-guard-eperm`: it resumes on its stale brief.")
    assert lines["landing-desk-2"].endswith("Never `SendMessage` `landing-desk-2`: it resumes on its stale brief.")


def test_stopped_lane_sharing_a_description_is_gone_not_matched_to_a_live_task(tree: Tree, clock: list[float]) -> None:
    shared = "Lane brief (long-running drive release-v3, root = s"
    tree.lane("ccx-guard-eperm", 513_462, description=shared, behind=timedelta(minutes=1))
    live = tree.lane("landing-desk-2", 440_000, description=shared, behind=timedelta(minutes=2))
    evt = stop(tree, [live, live])
    rotate_lanes(evt)
    tree.read("landing-desk-2")

    tree.kill("ccx-guard-eperm")
    evt = stop(tree, [live])
    assert [lane.name for lane in lane_rotation.live_lanes(evt.evt())] == ["landing-desk-2"]

    clock[0] += lane_rotation.ACK_WINDOW_SECONDS
    rotate_lanes(evt)
    assert [line.split("`")[1] for line in pending(evt)] == ["landing-desk-2"]
    assert ("ccx-guard-eperm", "gone") in [(entry["lane"], entry["event"]) for entry in timeline(evt)]


def test_lane_that_never_reads_its_ask_is_gone_until_its_transcript_moves(tree: Tree, clock: list[float]) -> None:
    shared = "Lane brief (long-running drive release-v3, root = s"
    stopped = tree.lane("ccx-guard-eperm", 513_462, description=shared, behind=timedelta(minutes=1))
    evt = stop(tree, [stopped])
    rotate_lanes(evt)

    clock[0] += lane_rotation.ACK_WINDOW_SECONDS
    rotate_lanes(evt)
    assert pending(evt) == []
    assert timeline(evt)[-1] | {"agent_id": None} == {
        "at": "2026-09-25T21:45:00Z",
        "lane": "ccx-guard-eperm",
        "agent_id": None,
        "event": "gone",
        "reason": "transcript unchanged since the ROTATE ask",
    }

    clock[0] += lane_rotation.ASK_GAP_SECONDS
    rotate_lanes(evt)
    assert (len(tree.inbox("ccx-guard-eperm")), pending(evt)) == (1, [])

    tree.read("ccx-guard-eperm")
    rotate_lanes(evt)
    assert len(tree.inbox("ccx-guard-eperm")) == 2


def test_respawned_lane_is_read_from_its_newest_transcript(tree: Tree, clock: list[float]) -> None:
    tree.lane("desk", 550_000, description="Landing desk", spawned=ROOT_AT - timedelta(hours=5))
    task = tree.lane("desk", 150_000, description="Landing desk", spawned=ROOT_AT - timedelta(minutes=30))

    rotate_lanes(stop(tree, [task]))

    assert tree.inbox("desk") == []


def test_lanes_sharing_a_description_each_need_their_own_task(tree: Tree, clock: list[float]) -> None:
    task = tree.lane("desk-a", 450_000, description="lane", spawned=ROOT_AT - timedelta(hours=2))
    tree.lane("desk-b", 460_000, description="lane", spawned=ROOT_AT - timedelta(hours=1), behind=timedelta(minutes=50))

    assert [lane.name for lane in lane_rotation.live_lanes(stop(tree, [task]).evt())] == ["desk-a"]
    assert sorted(lane.name for lane in lane_rotation.live_lanes(stop(tree, [task, task]).evt())) == ["desk-a", "desk-b"]


def test_each_team_roster_is_read_once_per_scan(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    tasks = [tree.lane("desk-a", 450_000), tree.lane("desk-b", 450_000), tree.lane("scout", 450_000, team="scout-team")]
    read: list[str] = []
    team_members = session_tree.team_members

    def recording(evt: StopEvent, team: str) -> dict[str, str]:
        read.append(team)
        return team_members(evt, team)

    monkeypatch.setattr(session_tree, "team_members", recording)

    lanes = lane_rotation.live_lanes(stop(tree, tasks).evt())

    assert (sorted(lane.name for lane in lanes), sorted(read)) == (["desk-a", "desk-b", "scout"], ["scout-team", TEAM])


def test_lanes_off_their_roster_are_never_parsed(tree: Tree, parsed: list[str]) -> None:
    task = tree.lane("desk", 450_000)
    for hours in (9, 6):
        tree.lane("retired", 550_000, spawned=ROOT_AT - timedelta(hours=hours))
        tree.kill("retired")

    lanes = lane_rotation.live_lanes(stop(tree, [task]).evt())

    assert ([lane.name for lane in lanes], parsed) == (["desk"], ["desk"])


def test_teammates_are_never_parsed_while_no_teammate_task_runs(tree: Tree, parsed: list[str]) -> None:
    tree.lane("stopped", 550_000)
    reviewer = tree.lane("reviewer", 420_000, team=None, description="Reviewer")

    lanes = lane_rotation.live_lanes(stop(tree, [reviewer]).evt())

    assert ([lane.name for lane in lanes], parsed) == (["reviewer"], ["reviewer"])


@pytest.mark.parametrize(
    "background",
    [[], [{"id": "build", "type": "local_bash", "status": "running", "description": "build"}]],
    ids=["empty", "non-lane-task"],
)
def test_no_live_lanes_skip_all_historical_metadata(
    tree: Tree, parsed: list[str], monkeypatch: pytest.MonkeyPatch, background: list[dict]
) -> None:
    for index in range(32):
        tree.lane(f"retired-team-{index}", 450_000, status=None)
        tree.lane(f"retired-subagent-{index}", 450_000, team=None, status=None)
    event = stop(tree, background).evt()
    counts = {"enumerations": 0, "metadata_stats": 0, "metadata_reads": 0}
    subagents = session_tree.subagents
    stat = Path.stat
    read_text = Path.read_text

    def enumerated(evt: StopEvent) -> list[session_tree.Subagent]:
        counts["enumerations"] += 1
        return subagents(evt)

    def stated(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        counts["metadata_stats"] += path.name.endswith(".meta.json")
        return stat(path, follow_symlinks=follow_symlinks)

    def read(path: Path, *args, **kwargs) -> str:
        counts["metadata_reads"] += path.name.endswith(".meta.json")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(session_tree, "subagents", enumerated)
    monkeypatch.setattr(Path, "stat", stated)
    monkeypatch.setattr(Path, "read_text", read)

    assert lane_rotation.scan_lanes(event) == ()
    assert parsed == []
    assert counts == {"enumerations": 0, "metadata_stats": 0, "metadata_reads": 0}


def test_newer_lane_off_its_roster_still_hides_an_older_namesake_on_another_roster(tree: Tree, parsed: list[str]) -> None:
    task = tree.lane("desk", 450_000, spawned=ROOT_AT - timedelta(hours=5))
    tree.lane("desk", 450_000, team="next-team", spawned=ROOT_AT - timedelta(hours=1))
    tree.kill("desk", "next-team")

    assert lane_rotation.live_lanes(stop(tree, [task]).evt()) == []
    assert parsed == ["desk", "desk"]


def test_newer_teammate_without_a_task_still_hides_a_running_subagent_namesake(tree: Tree) -> None:
    reviewer = tree.lane("reviewer", 420_000, team=None, spawned=ROOT_AT - timedelta(hours=5))
    tree.lane("reviewer", 450_000, spawned=ROOT_AT - timedelta(hours=1))

    assert lane_rotation.live_lanes(stop(tree, [reviewer]).evt()) == []


def test_prompt_label_claims_a_task_only_once_the_description_has_none_left(tree: Tree, parsed: list[str]) -> None:
    shared = "Lane brief (long-running drive release-v3, root = s"
    tasks = [
        tree.lane("desk-a", 450_000, description=shared, behind=timedelta(minutes=1)),
        tree.lane("desk-b", 450_000, description=shared, prompt="You are lane `desk-b`.", behind=timedelta(minutes=2)),
    ]

    lanes = lane_rotation.live_lanes(stop(tree, tasks).evt())

    assert ([lane.name for lane in lanes], sorted(parsed)) == (["desk-a", "desk-b"], ["desk-a", "desk-b"])


def test_a_scan_lets_go_of_each_transcript_before_reading_the_next(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    for hours in (9, 6):
        tree.lane("desk", 450_000, spawned=ROOT_AT - timedelta(hours=hours))
    tasks = [tree.lane("desk", 450_000), tree.lane("scout", 450_000)]
    held: list[weakref.ref[Events]] = []
    live_at_parse: list[int] = []
    parse_transcript = session_tree.parse

    def tracked(path: Path) -> SimpleNamespace:
        live_at_parse.append(sum(ref() is not None for ref in held))
        held.append(weakref.ref(events := Events(parse_transcript(path).events)))
        return SimpleNamespace(events=events)

    monkeypatch.setattr(session_tree, "parse", tracked)

    lanes = lane_rotation.live_lanes(stop(tree, tasks).evt())

    assert (sorted(lane.name for lane in lanes), live_at_parse, [ref() for ref in held]) == (
        ["desk", "scout"],
        [0, 0, 0, 0],
        [None] * 4,
    )


def test_a_transcript_unchanged_since_the_last_scan_is_not_parsed_again(tree: Tree, parsed: list[str]) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000), tree.lane("scout", 450_000)])

    before = {lane.name: lane.turn.tokens for lane in lane_rotation.live_lanes(handle.evt())}
    tree.grow("desk", 460_000)
    after = {lane.name: lane.turn.tokens for lane in lane_rotation.live_lanes(handle.evt())}

    assert (before, after, sorted(parsed)) == (
        {"desk": 450_000, "scout": 450_000},
        {"desk": 460_000, "scout": 450_000},
        ["desk", "desk", "scout"],
    )


def test_a_transcript_replaced_at_the_same_size_and_mtime_is_read_again(tree: Tree, parsed: list[str]) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000)])
    transcript = tree.transcript("desk")
    before = lane_rotation.live_lanes(handle.evt())[0].turn.tokens
    original = transcript.stat()
    replacement = transcript.with_name(f"{transcript.name}.tmp")
    replacement.write_text(retokened(transcript.read_text(), 450_000, 460_000))
    os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
    replacement.replace(transcript)
    replaced = transcript.stat()
    after = lane_rotation.live_lanes(handle.evt())[0].turn.tokens

    assert (before, after, parsed, (replaced.st_size, replaced.st_mtime_ns), replaced.st_ino != original.st_ino) == (
        450_000,
        460_000,
        ["desk", "desk"],
        (original.st_size, original.st_mtime_ns),
        True,
    )


def test_a_transcript_rewritten_in_place_at_the_same_size_is_read_again(tree: Tree, parsed: list[str]) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000)])
    transcript = tree.transcript("desk")
    before = lane_rotation.live_lanes(handle.evt())[0].turn.tokens
    original = transcript.stat()
    transcript.write_text(retokened(transcript.read_text(), 450_000, 460_000))
    os.utime(transcript, ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000_000))
    rewritten = transcript.stat()
    after = lane_rotation.live_lanes(handle.evt())[0].turn.tokens

    assert (before, after, parsed, (rewritten.st_ino, rewritten.st_size)) == (
        450_000,
        460_000,
        ["desk", "desk"],
        (original.st_ino, original.st_size),
    )


def test_a_transcript_truncated_back_to_an_earlier_version_is_read_again(tree: Tree, parsed: list[str]) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000)])
    transcript = tree.transcript("desk")
    earlier = transcript.read_text()
    tokens = [lane_rotation.live_lanes(handle.evt())[0].turn.tokens]
    tree.grow("desk", 460_000)
    tokens.append(lane_rotation.live_lanes(handle.evt())[0].turn.tokens)
    grown = transcript.stat()
    transcript.write_text(earlier)
    os.utime(transcript, ns=(grown.st_atime_ns, grown.st_mtime_ns))
    truncated = transcript.stat()
    tokens.append(lane_rotation.live_lanes(handle.evt())[0].turn.tokens)

    assert (tokens, parsed, (truncated.st_ino, truncated.st_mtime_ns), truncated.st_size < grown.st_size) == (
        [450_000, 460_000, 450_000],
        ["desk", "desk", "desk"],
        (grown.st_ino, grown.st_mtime_ns),
        True,
    )


def test_an_append_that_lands_after_the_read_is_read_on_the_next_scan(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000)])
    reads: list[str] = []
    parse_transcript = session_tree.parse

    def appended_behind_the_read(path: Path) -> SimpleNamespace:
        reads.append(path.name)
        events = Events(parse_transcript(path).events)
        if len(reads) == 1:
            tree.grow("desk", 460_000)
        return SimpleNamespace(events=events)

    monkeypatch.setattr(session_tree, "parse", appended_behind_the_read)

    tokens = [lane_rotation.live_lanes(handle.evt())[0].turn.tokens for _ in range(3)]

    assert (tokens, len(reads)) == ([450_000, 460_000, 460_000], 2)


def test_a_deleted_transcript_drops_its_lane_without_a_read(tree: Tree, parsed: list[str]) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000), tree.lane("scout", 450_000)])
    before = sorted(lane.name for lane in lane_rotation.live_lanes(handle.evt()))
    tree.transcript("desk").unlink()
    after = [lane.name for lane in lane_rotation.live_lanes(handle.evt())]

    assert (before, after, sorted(parsed)) == (["desk", "scout"], ["scout"], ["desk", "scout"])


def test_a_transcript_deleted_after_its_listing_raises_instead_of_serving_its_last_scan(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000)])
    [lane] = lane_rotation.live_lanes(handle.evt())
    listed = session_tree.subagents(handle.evt())
    monkeypatch.setattr(session_tree, "subagents", lambda evt: listed)
    tree.transcript("desk").unlink()

    with pytest.raises(FileNotFoundError):
        lane_rotation.live_lanes(handle.evt())

    assert lane.turn.tokens == 450_000


def disk_error(bound: reqenv.RequestOverrides) -> None:
    raise OSError("disk")


def caller_gone(bound: reqenv.RequestOverrides) -> None:
    bound.abandon.set()


@pytest.mark.parametrize(
    ("fail", "raised"),
    [pytest.param(disk_error, OSError, id="read-error"), pytest.param(caller_gone, reqenv.Abandoned, id="abandoned")],
)
def test_a_scan_that_fails_its_read_is_not_kept_and_the_next_scan_reads_again(
    tree: Tree, monkeypatch: pytest.MonkeyPatch, fail, raised: type[BaseException]
) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000)])
    first = request(tree)
    reads: list[str] = []
    parse_transcript = session_tree.parse

    def failing_once(path: Path):
        reads.append(path.name)
        if len(reads) == 1:
            fail(first)
        return parse_transcript(path)

    monkeypatch.setattr(session_tree, "parse", failing_once)

    with reqenv.use_request(first), pytest.raises(raised):
        lane_rotation.live_lanes(handle.evt())
    tokens = [lane_rotation.live_lanes(handle.evt())[0].turn.tokens for _ in range(2)]

    assert (tokens, len(reads)) == ([450_000, 450_000], 2)


@pytest.mark.parametrize(
    ("update", "after"),
    [
        pytest.param({"model": "sonnet"}, [("desk", 116_900)], id="model-hint"),
        pytest.param({"description": "renamed lane"}, [], id="description"),
    ],
)
def test_a_reused_scan_is_paired_with_the_lanes_current_metadata(
    tree: Tree, parsed: list[str], update: dict, after: list[tuple[str, int]]
) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000, model="claude-sonnet-4-6", hint="sonnet[1m]")])
    before = [(lane.name, lane.line) for lane in lane_rotation.live_lanes(handle.evt())]
    meta = tree.transcript("desk").with_suffix(".meta.json")
    meta.write_text(json.dumps(json.loads(meta.read_text()) | update))

    assert (before, [(lane.name, lane.line) for lane in lane_rotation.live_lanes(handle.evt())], parsed) == (
        [("desk", 396_900)],
        after,
        ["desk"],
    )


def test_the_scan_cache_holds_its_capacity_and_reads_the_oldest_transcript_again() -> None:
    computed: list[int] = []

    def scan_once(index: int) -> None:
        lane_rotation.SCANS.get(Path(f"/evicted/agent-{index}.jsonl"), (index,), math.inf, lambda: computed.append(index))

    for index in range(lane_rotation.SCAN_CACHE_SIZE + 1):
        scan_once(index)
    scan_once(0)
    scan_once(lane_rotation.SCAN_CACHE_SIZE)

    assert computed == [*range(lane_rotation.SCAN_CACHE_SIZE + 1), 0]


def test_a_start_tie_keeps_the_namesake_listed_first(tree: Tree) -> None:
    task = tree.lane("desk", 450_000)
    subagents = tree.root.with_suffix("") / "subagents"
    [listed_first] = subagents.glob("agent-adesk-*.meta.json")
    (subagents / "agent-adesk-zzzzzzzzzzzzzzzz.meta.json").write_text(listed_first.read_text())
    write_jsonl(
        subagents / "agent-adesk-zzzzzzzzzzzzzzzz.jsonl",
        [user(ROOT_AT - timedelta(hours=3), "go", sidechain=True), assistant(ROOT_AT, 460_000, sidechain=True)],
    )

    [lane] = lane_rotation.live_lanes(stop(tree, [task]).evt())

    assert lane.turn.tokens == 450_000


@pytest.mark.parametrize("case", ["finished-empty", "untracked-empty", "untracked-stale"])
def test_unselected_lanes_do_not_resolve_the_task_list(
    tree: Tree, clock: list[float], monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    payload = [tree.lane("stale", 450_000, behind=timedelta(seconds=task_list.IDLE_SECONDS + 1))] if case == "untracked-stale" else []
    subagents = tree.root.with_suffix("") / "subagents"
    subagents.mkdir(parents=True, exist_ok=True)
    for index in range(64):
        (subagents / f"agent-history-{index}.meta.json").write_text(json.dumps({"agentType": "general-purpose"}))
    monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID")
    evt = stop(tree, payload).evt()
    task_list.UntrackedState(turns=task_list.RECONCILE_TURNS - 1).save(evt)
    calls = {"identity": 0, "store": 0, "metadata_reads": 0, "metadata_enumerations": 0}
    resolving = False
    read_text = Path.read_text
    glob = Path.glob
    list_id = task_list.Tasks.list_id
    for_session = task_list.Tasks.for_session

    def identity(cls, session_id, transcript_path):
        nonlocal resolving
        calls["identity"] += 1
        resolving = True
        try:
            return list_id(session_id, transcript_path)
        finally:
            resolving = False

    def read_metadata(path, *args, **kwargs):
        if resolving and path.name.endswith(".meta.json"):
            calls["metadata_reads"] += 1
        return read_text(path, *args, **kwargs)

    def enumerate_metadata(path, pattern, **kwargs):
        if resolving and pattern == "agent-*.meta.json":
            calls["metadata_enumerations"] += 1
        return glob(path, pattern, **kwargs)

    def store(cls, session_id, **kwargs):
        calls["store"] += 1
        return for_session(session_id, **kwargs)

    monkeypatch.setattr(task_list.Tasks, "list_id", classmethod(identity))
    monkeypatch.setattr(task_list.Tasks, "for_session", classmethod(store))
    monkeypatch.setattr(Path, "read_text", read_metadata)
    monkeypatch.setattr(Path, "glob", enumerate_metadata)

    if case == "finished-empty":
        assert lane_rotation.finished(evt, []) == set()
    else:
        assert task_list.flag_untracked_lanes(evt) is None
        assert pending(stop(tree, payload)) == []

    assert calls == {"identity": 0, "store": 0, "metadata_reads": 0, "metadata_enumerations": 0}


def test_selected_lanes_read_task_changes_on_each_new_event(tree: Tree, clock: list[float]) -> None:
    payload = [tree.lane("desk", 450_000)]
    handle = stop(tree, payload)
    evt = handle.evt()
    lanes = lane_rotation.live_lanes(evt)
    assert lane_rotation.finished(evt, lanes) == set()

    tree.delete_task("desk")
    evt = handle.evt()
    assert lane_rotation.finished(evt, lanes) == {lanes[0].agent_id}
    task_list.UntrackedState(turns=task_list.RECONCILE_TURNS - 1).save(evt)
    assert task_list.flag_untracked_lanes(evt) is None
    assert pending(handle) == [task_list.untracked_line(["desk"])]

    tree.task("desk", "in_progress")
    evt = handle.evt()
    assert lane_rotation.finished(evt, lanes) == set()
    task_list.UntrackedState(turns=task_list.RECONCILE_TURNS - 1).save(evt)
    assert task_list.flag_untracked_lanes(evt) is None
    assert pending(handle) == [task_list.untracked_line(["desk"])]


def test_the_four_stop_callers_of_one_request_share_one_scan(tree: Tree, clock: list[float], parsed: list[str]) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000)])

    with reqenv.use_request(request(tree)):
        lane_rotation.ask_lanes_to_rotate(handle.evt())
        lane_rotation.escalate_unrotated_lanes(handle.evt())
        stale = task_list.drifted(handle.evt(), handle.evt().tasks, clock[0])
        loose = task_list.untracked(handle.evt(), clock[0])

    assert (parsed, len(tree.inbox("desk")), stale, loose) == (["desk"], 1, {}, [])


def test_a_request_keeps_its_first_scan_and_the_next_request_scans_again(tree: Tree, parsed: list[str]) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000)])
    tokens: list[int] = []

    for grown in (460_000, 470_000):
        with reqenv.use_request(request(tree)):
            tokens.append(lane_rotation.live_lanes(handle.evt())[0].turn.tokens)
            tree.grow("desk", grown)
            tokens.append(lane_rotation.live_lanes(handle.evt())[0].turn.tokens)

    assert (tokens, parsed) == ([450_000, 450_000, 460_000, 460_000], ["desk", "desk"])


def test_outside_a_request_every_call_scans(tree: Tree, parsed: list[str]) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000)])

    before = lane_rotation.live_lanes(handle.evt())[0].turn.tokens
    tree.grow("desk", 460_000)
    after = lane_rotation.live_lanes(handle.evt())[0].turn.tokens

    assert (before, after, parsed) == (450_000, 460_000, ["desk", "desk"])


def test_each_caller_gets_its_own_list_of_the_shared_lanes(tree: Tree) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000)])

    with reqenv.use_request(request(tree)):
        first = lane_rotation.live_lanes(handle.evt())
        kept = first[0]
        first.clear()
        second = lane_rotation.live_lanes(handle.evt())

    assert (first, [lane.name for lane in second], second[0] is kept) == ([], ["desk"], True)


def test_concurrent_hooks_of_one_request_scan_once(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000)])
    entered, release = threading.Event(), threading.Event()
    scans: list[str] = []
    parse_transcript, scan_lanes = session_tree.parse, lane_rotation.scan_lanes

    def slow(path: Path):
        entered.set()
        assert release.wait(10)
        return parse_transcript(path)

    def counted(evt: StopEvent) -> tuple[lane_rotation.Lane, ...]:
        scans.append("scan")
        return scan_lanes(evt)

    monkeypatch.setattr(session_tree, "parse", slow)
    monkeypatch.setattr(lane_rotation, "scan_lanes", counted)

    with reqenv.use_request(request(tree)), ThreadPoolExecutor(max_workers=4) as pool:
        hooks = [pool.submit(copy_context().run, lane_rotation.live_lanes, handle.evt()) for _ in range(4)]
        assert entered.wait(10)
        release.set()
        seen = [[lane.name for lane in hook.result(timeout=10)] for hook in hooks]

    assert (seen, len(scans)) == ([["desk"]] * 4, 1)


def test_a_failed_scan_is_not_kept_for_the_request(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000)])
    team_members = session_tree.team_members
    failures = [OSError("disk")]

    def flaky(evt: StopEvent, team: str) -> dict[str, str]:
        if failures:
            raise failures.pop()
        return team_members(evt, team)

    monkeypatch.setattr(session_tree, "team_members", flaky)

    with reqenv.use_request(request(tree)):
        with pytest.raises(OSError, match="disk"):
            lane_rotation.live_lanes(handle.evt())
        lanes = lane_rotation.live_lanes(handle.evt())

    assert [lane.name for lane in lanes] == ["desk"]


def test_an_abandoned_scan_stops_at_the_next_transcript_and_is_not_kept(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    handle = stop(tree, [tree.lane(name, 450_000) for name in ("desk-a", "desk-b", "desk-c")])
    bound = request(tree)
    parsed: list[str] = []
    parse_transcript = session_tree.parse

    def abandoning(path: Path):
        parsed.append(path.name)
        bound.abandon.set()
        return parse_transcript(path)

    monkeypatch.setattr(session_tree, "parse", abandoning)

    with reqenv.use_request(bound):
        with pytest.raises(reqenv.Abandoned):
            lane_rotation.live_lanes(handle.evt())
        stopped = len(parsed)
        bound.abandon.clear()
        monkeypatch.setattr(session_tree, "parse", parse_transcript)
        lanes = lane_rotation.live_lanes(handle.evt())

    assert (stopped, sorted(lane.name for lane in lanes)) == (1, ["desk-a", "desk-b", "desk-c"])


def test_an_abandoned_scan_reads_no_turn_once_its_caller_is_gone(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    handle = stop(tree, [tree.lane(name, 450_000) for name in ("desk-a", "desk-b")])
    bound = request(tree)
    parsed: list[str] = []
    turns: list[str] = []
    parse_transcript, turn_of = session_tree.parse, lane_rotation.turn_of

    def last_parse_abandons(path: Path):
        parsed.append(path.name)
        if len(parsed) == 2:
            bound.abandon.set()
        return parse_transcript(path)

    def recording(events, *, sidechain: bool = False):
        turns.append("turn")
        return turn_of(events, sidechain=sidechain)

    monkeypatch.setattr(session_tree, "parse", last_parse_abandons)
    monkeypatch.setattr(lane_rotation, "turn_of", recording)

    with reqenv.use_request(bound), pytest.raises(reqenv.Abandoned):
        lane_rotation.live_lanes(handle.evt())

    assert (len(parsed), turns) == (2, ["turn"])


def test_an_abandoned_scan_builds_no_lane_once_its_caller_is_gone(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    handle = stop(tree, [tree.lane("desk", 450_000)])
    bound = request(tree)
    lines: list[str] = []
    turn_of, rotation_line = lane_rotation.turn_of, lane_rotation.rotation_line

    def last_turn_abandons(events, *, sidechain: bool = False):
        bound.abandon.set()
        return turn_of(events, sidechain=sidechain)

    def recording(model: str, hint: str | None, project: Path | None) -> int:
        lines.append(model)
        return rotation_line(model, hint, project)

    monkeypatch.setattr(lane_rotation, "turn_of", last_turn_abandons)
    monkeypatch.setattr(lane_rotation, "rotation_line", recording)

    with reqenv.use_request(bound), pytest.raises(reqenv.Abandoned):
        lane_rotation.live_lanes(handle.evt())

    assert lines == []


def test_a_hook_abandoned_while_another_hook_fills_the_scan_cache_reads_no_transcript(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    filling = stop(tree, [tree.lane("desk", 450_000, team=None)]).evt()
    waiting = stop(tree, [tree.lane("scout", 450_000, team=None)]).evt()
    filler, waiter = request(tree), request(tree)
    desk, scout = tree.transcript("desk").name, tree.transcript("scout").name
    reading, release, arrived = threading.Event(), threading.Event(), threading.Event()
    reads: list[str] = []
    parse_transcript = session_tree.parse

    class Arrivals(StampedCache):
        def get(self, key: Path, *args, **kwargs):
            if key.name == scout:
                arrived.set()
            return super().get(key, *args, **kwargs)

    def held_read(path: Path):
        reads.append(path.name)
        if path.name == desk:
            reading.set()
            assert release.wait(10)
        return parse_transcript(path)

    def hook(bound: reqenv.RequestOverrides, evt: StopEvent) -> list[str]:
        with reqenv.use_request(bound):
            return [lane.name for lane in lane_rotation.live_lanes(evt)]

    monkeypatch.setattr(lane_rotation, "SCANS", Arrivals(lane_rotation.SCAN_CACHE_SIZE))
    monkeypatch.setattr(session_tree, "parse", held_read)

    with ThreadPoolExecutor(max_workers=2) as pool:
        filled = pool.submit(hook, filler, filling)
        assert reading.wait(10)
        abandoned = pool.submit(hook, waiter, waiting)
        assert arrived.wait(10)
        waiter.abandon.set()
        release.set()
        lanes = filled.result(timeout=10)
        with pytest.raises(reqenv.Abandoned):
            abandoned.result(timeout=10)

    assert (lanes, reads) == (["desk"], [desk])


def test_flushed_reply_queues_one_nudge(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("landing-desk", 450_000)])
    rotate_lanes(evt)
    reply = (
        "Another Claude session sent a message:\n"
        '<teammate-message teammate_id="landing-desk" color="blue" summary="flushed b037decf c2b3a6c">\n'
        "flushed b037decf c2b3a6c. The worktree is clean.\n</teammate-message>\n\n"
        '<teammate-message teammate_id="landing-desk" color="blue">\n'
        '{"type":"idle_notification","from":"landing-desk","timestamp":"2026-09-25T21:36:11.572Z",'
        '"idleReason":"available","result":"flushed b037decf"}\n</teammate-message>\n\n'
        '<teammate-message teammate_id="poller" color="red">\nflushed 74de6071\n</teammate-message>'
    )
    deliver(tree, reply, ROOT_AT + timedelta(minutes=2))
    deliver(tree, reply, ROOT_AT + timedelta(minutes=3))

    for _ in range(2):
        clock[0] += 60
        rotate_lanes(evt)

    assert pending(evt) == ["Lane `landing-desk` flushed its context. Leave it running; do not `TaskStop` it."]
    clock[0] += 2 * lane_rotation.ASK_GAP_SECONDS
    rotate_lanes(evt)
    assert len(tree.inbox("landing-desk")) == 1


def test_flushed_reply_behind_a_later_stamped_entry_is_found(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("landing-desk", 450_000)])
    rotate_lanes(evt)
    with tree.root.open("a") as transcript:
        transcript.write(json.dumps({"type": "pr-link", "timestamp": stamp(ROOT_AT + timedelta(minutes=5))}) + "\n")
    deliver(tree, '<teammate-message teammate_id="landing-desk">\nflushed 74de6071\n</teammate-message>', ROOT_AT + timedelta(minutes=4))

    rotate_lanes(evt)

    assert len(pending(evt)) == 1


def test_half_written_reply_is_read_on_the_next_stop(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("landing-desk", 450_000)])
    rotate_lanes(evt)
    line = json.dumps(user(ROOT_AT, '<teammate-message teammate_id="landing-desk">\nflushed 74de6071\n</teammate-message>')) + "\n"
    with tree.root.open("a") as transcript:
        transcript.write(line[:40])

    rotate_lanes(evt)
    assert pending(evt) == []

    with tree.root.open("a") as transcript:
        transcript.write(line[40:])
    rotate_lanes(evt)
    assert len(pending(evt)) == 1


def test_flushed_reply_before_the_ask_is_ignored(tree: Tree, clock: list[float]) -> None:
    deliver(tree, '<teammate-message teammate_id="landing-desk">\nflushed 74de6071\n</teammate-message>', ROOT_AT - timedelta(minutes=10))
    evt = stop(tree, [tree.lane("landing-desk", 450_000)])

    rotate_lanes(evt)
    clock[0] += 60
    rotate_lanes(evt)

    assert pending(evt) == []


@pytest.mark.parametrize(
    ("body", "ids"),
    [
        ("still working on #25751", None),
        ("Flushed: b037decf", ["b037decf"]),
        ("flushed abc", []),
        (
            "flushed ledger msg/000038 msg/000043; bus #83 #112; ccn note 7803814 — nothing new since the last flush.",
            ["msg/000038", "msg/000043", "#83", "#112", "7803814"],
        ),
        ("flushed bus#137 msg/000042.\nOption 1 was already applied on #29838", ["bus#137", "msg/000042"]),
    ],
)
def test_reply_parsing(body: str, ids: list[str] | None) -> None:
    replies = lane_rotation.flushed_replies(f'<teammate-message teammate_id="desk">\n{body}\n</teammate-message>')
    assert replies == ([] if ids is None else [("desk", ids)])


def test_a_failed_append_keeps_the_asks_already_made(
    tree: Tree, clock: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = [tree.lane("desk", 460_000), tree.lane("broken", 450_000)]
    evt = stop(tree, tasks)
    append = session_tree.append_inbox

    def flaky(inbox: Path, message: dict) -> bool:
        if inbox.stem == "broken":
            raise OSError("disk")
        return append(inbox, message)

    monkeypatch.setattr(session_tree, "append_inbox", flaky)
    for _ in range(2):
        with pytest.raises(OSError):
            lane_rotation.ask_lanes_to_rotate(evt.evt())
        clock[0] += 60

    assert len(tree.inbox("desk")) == 1


def test_a_missing_team_dir_is_created(tree: Tree, clock: list[float]) -> None:
    rotate_lanes(stop(tree, [tree.lane("orphan", 450_000, team="new-team")]))

    assert len(json.loads((tree.claude / "teams" / "new-team" / "inboxes" / "orphan.json").read_text())) == 1


def test_empty_inbox_reads_as_no_messages(tmp_path: Path) -> None:
    inbox = tmp_path / "inboxes" / "desk.json"
    inbox.parent.mkdir()
    inbox.write_text("")

    assert session_tree.append_inbox(inbox, {"text": "ROTATE"})

    assert json.loads(inbox.read_text()) == [{"text": "ROTATE"}]


def test_fork_transcript_without_a_leading_timestamp_is_live(tree: Tree, clock: list[float]) -> None:
    task = tree.lane("fork", 450_000)
    [transcript] = (tree.root.with_suffix("") / "subagents").glob("agent-afork-*.jsonl")
    transcript.write_text(json.dumps({"type": "fork-context-ref"}) + "\n" + transcript.read_text())

    assert [lane.name for lane in lane_rotation.live_lanes(stop(tree, [task]).evt())] == ["fork"]


@pytest.mark.parametrize(
    ("model", "hint", "env", "line"),
    [
        ("claude-opus-5-5", None, None, 396_900),
        ("claude-opus-5-5", "inherit", None, 396_900),
        ("claude-haiku-4-5-20251001", None, None, 116_900),
        ("claude-sonnet-4-6", "sonnet[1m]", None, 396_900),
        ("claude-sonnet-4-6", "sonnet", None, 116_900),
        ("claude-opus-5-5", None, "250000", 250_000),
    ],
)
def test_rotation_line_follows_the_lanes_compaction_threshold(
    tree: Tree, monkeypatch: pytest.MonkeyPatch, model: str, hint: str | None, env: str | None, line: int
) -> None:
    if env:
        monkeypatch.setenv("LONG_RUNNING_LANE_ROTATE_TOKENS", env)
    evt = stop(tree, [tree.lane("desk", 450_000, model=model, hint=hint)])

    [lane] = lane_rotation.live_lanes(evt.evt())

    assert lane.line == line


def test_compact_boundary_after_the_last_turn_reports_post_compact_tokens(tmp_path: Path) -> None:
    transcript = tmp_path / "lane.jsonl"
    boundary = {
        **envelope("system", ROOT_AT, sidechain=True),
        "subtype": "compact_boundary",
        "compactMetadata": {"trigger": "auto", "preTokens": 570_709, "postTokens": 18_816},
    }
    write_jsonl(transcript, [assistant(ROOT_AT - timedelta(minutes=1), 568_591, sidechain=True), boundary])

    assert turns.turn_of(parse(transcript).events, sidechain=True) == turns.Turn("claude-opus-5-5", 18_816, ROOT_AT)

    write_jsonl(transcript, [boundary, assistant(ROOT_AT + timedelta(minutes=1), 24_000, sidechain=True)])
    assert turns.turn_of(parse(transcript).events, sidechain=True).tokens == 24_000


def test_turn_of_reads_back_only_as_far_as_the_last_turn(tmp_path: Path) -> None:
    transcript = tmp_path / "lane.jsonl"
    turns_taken = [assistant(ROOT_AT - timedelta(minutes=minutes), 10_000 * minutes, sidechain=True) for minutes in (3, 2, 1)]
    write_jsonl(transcript, [user(ROOT_AT - timedelta(hours=1), "go", sidechain=True), *turns_taken, user(ROOT_AT, "tail", sidechain=True)])
    events = Indexed(parse(transcript).events)

    assert (turns.turn_of(events, sidechain=True).tokens, events.read) == (10_000, [len(events) - 1, len(events) - 2])


def test_inbox_append_matches_claude_code_format(tmp_path: Path) -> None:
    golden = (GOLDEN / "inbox.json").read_text()
    inbox = tmp_path / "inboxes" / "desk.json"
    inbox.parent.mkdir()
    inbox.write_text(golden)

    assert session_tree.append_inbox(inbox, {"from": "long-running", "text": "ROTATE"})

    assert inbox.read_text() == golden.removesuffix("\n]") + ',\n  {\n    "from": "long-running",\n    "text": "ROTATE"\n  }\n]'
    assert sorted(path.name for path in inbox.parent.iterdir()) == ["desk.json"]


def test_inbox_append_creates_a_missing_inbox(tmp_path: Path) -> None:
    inbox = tmp_path / "inboxes" / "desk.json"

    assert session_tree.append_inbox(inbox, {"text": "ROTATE"})

    assert json.loads(inbox.read_text()) == [{"text": "ROTATE"}]


def test_held_lock_skips_the_append(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_tree, "LOCK_MAX_DELAY", 0.001)
    inbox = tmp_path / "inboxes" / "desk.json"
    inbox.parent.mkdir()
    inbox.write_text("[]")
    (tmp_path / "inboxes" / "desk.json.lock").mkdir()

    assert not session_tree.append_inbox(inbox, {"text": "ROTATE"})

    assert (inbox.read_text(), (tmp_path / "inboxes" / "desk.json.lock").is_dir()) == ("[]", True)


def test_stale_lock_is_broken(tmp_path: Path) -> None:
    inbox = tmp_path / "inboxes" / "desk.json"
    inbox.parent.mkdir()
    inbox.write_text("[]")
    lock = tmp_path / "inboxes" / "desk.json.lock"
    lock.mkdir()
    old = session_tree.time.time() - session_tree.LOCK_STALE_SECONDS - 1
    os.utime(lock, (old, old))

    assert session_tree.append_inbox(inbox, {"text": "ROTATE"})

    assert (json.loads(inbox.read_text()), lock.exists()) == ([{"text": "ROTATE"}], False)


def test_concurrent_appends_keep_every_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_tree, "LOCK_RETRIES", 1000)
    inbox = tmp_path / "inboxes" / "desk.json"
    inbox.parent.mkdir()
    inbox.write_text("[]")
    writers = [threading.Thread(target=session_tree.append_inbox, args=(inbox, {"n": n})) for n in range(20)]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join()

    assert sorted(message["n"] for message in json.loads(inbox.read_text())) == list(range(20))


def test_hooks_sharing_rotation_state_cannot_lose_an_ask(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("desk", 460_000)])
    lane_rotation.ask_lanes_to_rotate(evt.evt())
    lane_rotation.note_flushed_lanes(evt.evt())
    lane_rotation.escalate_unrotated_lanes(evt.evt())
    assert len(lane_rotation.RotationState.load(evt.evt()).asks) == 1
    assert len(tree.inbox("desk")) == 1


@pytest.mark.parametrize(
    "handler",
    [
        lane_rotation.note_flushed_lanes,
        lane_rotation.ask_lanes_to_rotate,
        lane_rotation.escalate_unrotated_lanes,
        task_list.require_task_for_owner_ask,
        task_list.flag_lane_done_reports,
    ],
)
def test_root_transcript_hooks_read_a_tail_window(handler) -> None:
    entry = next(hook for hook in _state.hooks if hook.handler is handler)
    assert entry.spec.transcript_events == TURN_WINDOW


def test_flushed_reply_is_found_after_the_cursor_leaves_the_window(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("landing-desk", 450_000)])
    evt.window = TURN_WINDOW
    rotate_lanes(evt)
    with tree.root.open("a") as transcript:
        for second in range(TURN_WINDOW):
            transcript.write(json.dumps(user(ROOT_AT + timedelta(seconds=second), "status")) + "\n")
    deliver(tree, '<teammate-message teammate_id="landing-desk">\nflushed 74de6071\n</teammate-message>', ROOT_AT + timedelta(minutes=5))

    clock[0] += 60
    rotate_lanes(evt)

    assert pending(evt) == ["Lane `landing-desk` flushed its context. Leave it running; do not `TaskStop` it."]


def test_unflushed_lane_escalates_from_a_tail_window(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("landing-desk", 450_000)])
    evt.window = TURN_WINDOW
    rotate_lanes(evt)
    tree.read("landing-desk")

    clock[0] += lane_rotation.ACK_WINDOW_SECONDS
    rotate_lanes(evt)

    assert [line.split(":")[0] for line in pending(evt)] == ["ROOT-ACTION `landing-desk`"]


@pytest.mark.parametrize("status", ["completed", "deleted"])
def test_finished_lane_is_never_asked(tree: Tree, clock: list[float], status: str) -> None:
    task = tree.lane("b-pipeline", 550_000)
    if status == "deleted":
        tree.delete_task("b-pipeline")
    else:
        tree.task("b-pipeline", status)
    evt = stop(tree, [task])

    for _ in range(3):
        rotate_lanes(evt)
        clock[0] += lane_rotation.ASK_GAP_SECONDS

    assert (tree.inbox("b-pipeline"), pending(evt), timeline(evt)) == ([], [], [])


def test_lane_that_finishes_after_its_ask_is_dropped_without_escalation(tree: Tree, clock: list[float]) -> None:
    task = tree.lane("b-queue-roles", 450_000)
    evt = stop(tree, [task])
    rotate_lanes(evt)
    tree.read("b-queue-roles")

    tree.task("b-queue-roles", "completed")
    for _ in range(4):
        clock[0] += lane_rotation.ASK_GAP_SECONDS
        rotate_lanes(evt)

    assert (len(tree.inbox("b-queue-roles")), pending(evt)) == (1, [])
    assert [entry["event"] for entry in timeline(evt)] == ["ask", "finished"]
    assert lane_rotation.RotationState.load(evt.evt()).asks == {}


def stand_down(tree: Tree, name: str, at: datetime = ROOT_AT) -> None:
    message = {"from": "team-lead", "text": "STAND-DOWN, root: your successor owns this.", "timestamp": stamp(at), "msg_id": str(uuid.uuid4())}
    assert session_tree.append_inbox(tree.claude / "teams" / TEAM / "inboxes" / f"{name}.json", message)


def test_flushed_lane_is_never_asked_again(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("b-platy-page-route", 450_000)])
    rotate_lanes(evt)
    tree.read("b-platy-page-route")
    deliver(
        tree,
        '<teammate-message teammate_id="b-platy-page-route">\nflushed msg/000032 (ledger READY #29838 at 281d2ad1), bus #33\n</teammate-message>',
        ROOT_AT + timedelta(minutes=2),
    )
    clock[0] += 120
    rotate_lanes(evt)
    nudges.NudgeState(pending=[]).save(evt.evt())

    tree.grow("b-platy-page-route", 900_000)
    for _ in range(3):
        clock[0] += lane_rotation.ASK_GAP_SECONDS
        rotate_lanes(evt)
    assert (len(tree.inbox("b-platy-page-route")), pending(evt)) == (1, [])
    assert lane_rotation.RotationState.load(evt.evt()).flushed == ["b-platy-page-route"]
    assert [entry["event"] for entry in timeline(evt)] == ["ask", "flushed"]


def test_a_flush_cancels_the_lanes_queued_root_action(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("landing-desk", 450_000)])
    rotate_lanes(evt)
    tree.read("landing-desk")
    clock[0] += lane_rotation.ACK_WINDOW_SECONDS
    rotate_lanes(evt)
    assert [line.split(":")[0] for line in pending(evt)] == ["ROOT-ACTION `landing-desk`"]

    deliver(tree, '<teammate-message teammate_id="landing-desk">\nflushed 74de6071\n</teammate-message>', ROOT_AT + timedelta(minutes=12))
    clock[0] += 60
    rotate_lanes(evt)

    assert pending(evt) == ["Lane `landing-desk` flushed its context. Leave it running; do not `TaskStop` it."]


def test_flushed_lane_that_compacts_is_still_never_asked_again(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("desk", 450_000)])
    rotate_lanes(evt)
    tree.read("desk")
    deliver(tree, '<teammate-message teammate_id="desk">\nflushed 74de6071\n</teammate-message>', ROOT_AT + timedelta(minutes=2))
    clock[0] += 120
    rotate_lanes(evt)

    tree.grow("desk", 30_000)
    rotate_lanes(evt)
    tree.grow("desk", 400_000)
    clock[0] += lane_rotation.PACE_SECONDS
    rotate_lanes(evt)
    assert len(tree.inbox("desk")) == 1


def test_a_flush_reply_to_an_ask_the_state_forgot_still_ends_the_asks(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("hook-diet", 450_000)])
    rotate_lanes(evt)
    forgotten = lane_rotation.RotationState.load(evt.evt())
    forgotten.names, forgotten.asks = {}, {}
    forgotten.save(evt.evt())
    deliver(tree, '<teammate-message teammate_id="hook-diet">\nflushed 74de6071\n</teammate-message>', ROOT_AT + timedelta(minutes=2))

    for _ in range(3):
        clock[0] += lane_rotation.ASK_GAP_SECONDS
        rotate_lanes(evt)

    assert len(tree.inbox("hook-diet")) == 1


def test_lane_with_a_stand_down_on_disk_is_never_asked(tree: Tree, clock: list[float]) -> None:
    task = tree.lane("lr-dashboard", 550_000)
    stand_down(tree, "lr-dashboard")
    evt = stop(tree, [task])

    for _ in range(3):
        rotate_lanes(evt)
        clock[0] += lane_rotation.ASK_GAP_SECONDS

    assert ([m["text"] for m in tree.inbox("lr-dashboard")], pending(evt), timeline(evt)) == (["STAND-DOWN, root: your successor owns this."], [], [])


def test_lane_stood_down_after_its_ask_is_neither_asked_again_nor_escalated(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("lr-dashboard-2", 450_000)])
    rotate_lanes(evt)
    tree.read("lr-dashboard-2")
    stand_down(tree, "lr-dashboard-2")

    for _ in range(4):
        clock[0] += lane_rotation.ASK_GAP_SECONDS
        rotate_lanes(evt)

    assert ([m["from"] for m in tree.inbox("lr-dashboard-2")], pending(evt)) == (["long-running", "team-lead"], [])


STEP_8_STAND_DOWN = "STAND-DOWN: mem-08-go-client-2 has taken over Step 8 (#31058) at 790dafc3. Stop all work now: no pushes, posts, or enqueues. Send nothing further."


def test_lane_that_already_read_its_stand_down_is_never_asked(tree: Tree, clock: list[float]) -> None:
    task = tree.lane("mem-08-go-client", 550_000)
    stored = {
        "from": "team-lead",
        "text": STEP_8_STAND_DOWN,
        "summary": "STAND-DOWN: successor owns Step 8",
        "timestamp": stamp(ROOT_AT),
        "msgV": 1,
        "msg_id": str(uuid.uuid4()),
        "type": "message",
        "read": False,
    }
    assert session_tree.append_inbox(tree.claude / "teams" / TEAM / "inboxes" / "mem-08-go-client.json", stored)
    tree.hear(
        "mem-08-go-client",
        f'<teammate-message teammate_id="long-running" summary="ROTATE: flush and keep working">\n{lane_rotation.ROTATE}\n</teammate-message>\n\n'
        f'<teammate-message teammate_id="team-lead" summary="STAND-DOWN: successor owns Step 8">\n{STEP_8_STAND_DOWN}\n</teammate-message>',
    )
    evt = stop(tree, [task])

    for _ in range(4):
        rotate_lanes(evt)
        clock[0] += lane_rotation.ASK_GAP_SECONDS

    assert (tree.inbox("mem-08-go-client"), pending(evt), timeline(evt)) == ([], [], [])


@pytest.mark.parametrize(("old", "successor"), [("mem-08-go-client", "mem-08-go-client-2"), ("desk-3", "desk-4")])
def test_handed_off_lane_with_a_live_successor_is_never_asked(tree: Tree, clock: list[float], old: str, successor: str) -> None:
    tree.lane(f"{old}-handoff", 80_000, team=None, status=None)
    evt = stop(tree, [tree.lane(old, 550_000), tree.lane(successor, 450_000)])

    for _ in range(4):
        rotate_lanes(evt)
        clock[0] += lane_rotation.ASK_GAP_SECONDS

    assert (tree.inbox(old), {m["from"] for m in tree.inbox(successor)}) == ([], {"long-running"})


def test_numbered_sibling_without_a_handoff_is_still_asked(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("memdoc-disposition-1", 550_000), tree.lane("memdoc-disposition-2", 450_000)])

    rotate_lanes(evt)

    assert [len(tree.inbox(name)) for name in ("memdoc-disposition-1", "memdoc-disposition-2")] == [1, 1]


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


def test_the_first_send_to_a_rotated_lane_is_blocked_and_names_task_stop(tree: Tree) -> None:
    rotate(tree, "oncall-connect", "oncall-connect-2")

    assert send(tree, "oncall-connect", sender=None) == (
        "block",
        "`oncall-connect` rotated to `oncall-connect-2`, which owns its work now, so this `SendMessage` is blocked: any message to "
        "`oncall-connect`, a stand-down included, resumes it on its stale brief. Stand it down with `TaskStop` `oncall-connect`, "
        "and send its work to `oncall-connect-2`.",
    )
    assert send(tree, "oncall-connect", sender=None) == (
        "warn",
        "Warning, nothing was blocked: `oncall-connect` rotated to `oncall-connect-2`, which owns its work now, and this message "
        "resumes the old lane on its stale brief. Stand it down with `TaskStop` `oncall-connect`, and send the next one to `oncall-connect-2`.",
    )
    assert send(tree, "oncall-connect-2") is None


def test_a_lanes_send_to_a_rotated_lane_is_blocked_without_naming_task_stop(tree: Tree) -> None:
    rotate(tree, "oncall-connect", "oncall-connect-2")

    action, message = send(tree, "oncall-connect") or ("", "")

    assert (action, "TaskStop" in message, message.endswith("Send its work to `oncall-connect-2`.")) == ("block", False, True)
    assert verdict(tree, "oncall-connect") == "warn"


@pytest.mark.parametrize("message", ["rulings-hygiene-2 reported; stand down and send nothing further.", "STAND-DOWN: rulings-hygiene-2 owns it."])
def test_a_stand_down_to_a_rotated_lane_is_blocked_like_any_send(tree: Tree, message: str) -> None:
    rotate(tree, "rulings-hygiene", "rulings-hygiene-2")

    assert verdict(tree, "rulings-hygiene", message, sender=None) == "block"


def test_a_successor_brief_naming_its_predecessor_rotates_it_without_a_handoff(tree: Tree) -> None:
    tree.lane("creds-5", 450_000)
    tree.lane("creds-6", 80_000)
    prompt = "Replaces: creds-5\nRun `drive.py lane-status creds-5` before any write."
    assert tool_use(tree, PostToolUseEvent, "Agent", {"name": "creds-6", "prompt": prompt}) == []

    assert lane_rotation.RotationState.load(Stop(tree, []).evt()).retired == {"creds-5": "creds-6"}
    assert verdict(tree, "creds-5", sender=None) == "block"


def test_a_respawn_under_a_rotated_name_clears_its_block(tree: Tree) -> None:
    rotate(tree, "desk-3", "desk-4")
    assert verdict(tree, "desk-3", sender=None) == "block"
    spawn(tree, "desk-3")
    assert send(tree, "desk-3", sender=None) is None

    spawn(tree, "desk-4")

    assert verdict(tree, "desk-3", sender=None) == "block"


def test_second_rotation_redirects_to_the_newest_successor(tree: Tree) -> None:
    tree.lane("desk-3", 450_000)
    tree.lane("desk-3-handoff", 80_000, team=None, status=None)
    spawn(tree, "desk-4")
    tree.lane("desk-4-handoff", 80_000, team=None, status=None)
    spawn(tree, "desk-5")

    assert (send(tree, "desk-3") or ("", ""))[1].endswith("Send its work to `desk-5`.")


def test_stopped_lane_without_a_successor_redirects_lanes_to_main(tree: Tree) -> None:
    reviewer = tree.lane("reviewer", 120_000, team=None)
    assert tool_use(tree, PostToolUseEvent, "TaskStop", {"task_id": reviewer["id"]}) == []

    assert "Send the next one to `main`." in (send(tree, "reviewer") or ("", ""))[1]
    assert send(tree, "reviewer", sender=None) is None


def test_stand_down_without_a_handoff_retires_the_lane(tree: Tree) -> None:
    tree.lane("alerts-watch", 450_000)
    assert tool_use(tree, PostToolUseEvent, "SendMessage", {"to": "alerts-watch", "message": "STAND-DOWN: done."}) == []

    assert "Send the next one to `main`." in (send(tree, "alerts-watch") or ("", ""))[1]


def test_numbered_sibling_without_a_handoff_retires_nothing(tree: Tree) -> None:
    tree.lane("memdoc-disposition-1", 450_000)
    spawn(tree, "memdoc-disposition-2")

    assert send(tree, "memdoc-disposition-1") is None


def test_respawn_under_a_retired_name_clears_it(tree: Tree) -> None:
    tree.lane("oncall-watch", 450_000)
    assert tool_use(tree, PostToolUseEvent, "TaskStop", {"task_id": "oncall-watch@session-rot"}) == []
    spawn(tree, "oncall-watch")

    assert send(tree, "oncall-watch") is None


ROOT_STAND_DOWN = "Root: stand down. Your handoff doc d652fe1 is the record and p8c-ship-3 has been spawned from your brief plus that doc."


def test_a_lane_the_root_stood_down_loses_its_root_action_and_is_never_asked_again(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("p8c-ship-2", 450_000)])
    rotate_lanes(evt)
    tree.read("p8c-ship-2")
    clock[0] += lane_rotation.ACK_WINDOW_SECONDS
    rotate_lanes(evt)
    assert [line.split(":")[0] for line in pending(evt)] == ["ROOT-ACTION `p8c-ship-2`"]

    assert tool_use(tree, PostToolUseEvent, "SendMessage", {"to": "p8c-ship-2", "message": ROOT_STAND_DOWN}) == []
    for _ in range(6):
        clock[0] += lane_rotation.ROOT_ACTION_GAP_SECONDS
        rotate_lanes(evt)

    assert (len(tree.inbox("p8c-ship-2")), pending(evt)) == (1, [])


def test_a_lane_that_heard_a_stand_down_in_the_roots_own_words_is_never_asked(tree: Tree, clock: list[float]) -> None:
    task = tree.lane("p8c-ship-2", 550_000)
    tree.hear("p8c-ship-2", f'<teammate-message teammate_id="team-lead" summary="stand down">\n{ROOT_STAND_DOWN}\n</teammate-message>')
    evt = stop(tree, [task])

    for _ in range(4):
        rotate_lanes(evt)
        clock[0] += lane_rotation.ASK_GAP_SECONDS

    assert (tree.inbox("p8c-ship-2"), pending(evt), timeline(evt)) == ([], [], [])


STAND_DOWN_MENTIONS = ["do not stand down", "On STAND-DOWN: stop", "p8c-ship-2 is stood down; stand-down sent to it"]


@pytest.mark.parametrize(("text", "asked"), [("Stand down, p8c-ship-2.", 0), *((mention, 1) for mention in STAND_DOWN_MENTIONS)])
def test_only_a_stand_down_order_heard_by_the_lane_ends_its_asks(tree: Tree, clock: list[float], text: str, asked: int) -> None:
    task = tree.lane("p8c-ship-2", 550_000)
    tree.hear("p8c-ship-2", f'<teammate-message teammate_id="team-lead" summary="root">\n{text}\n</teammate-message>')
    evt = stop(tree, [task])

    rotate_lanes(evt)

    assert len(tree.inbox("p8c-ship-2")) == asked


@pytest.mark.parametrize("text", STAND_DOWN_MENTIONS)
def test_a_root_message_that_only_mentions_a_stand_down_keeps_the_lane_asked(tree: Tree, clock: list[float], text: str) -> None:
    evt = stop(tree, [tree.lane("p8c-ship-2", 550_000)])

    assert tool_use(tree, PostToolUseEvent, "SendMessage", {"to": "p8c-ship-2", "message": text}) == []
    rotate_lanes(evt)

    assert ("p8c-ship-2" in lane_rotation.RotationState.load(evt.evt()).retired, len(tree.inbox("p8c-ship-2"))) == (False, 1)
