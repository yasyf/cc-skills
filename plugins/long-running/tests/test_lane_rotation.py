from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cc_transcript import Session, parse
from captain_hook.events import StopEvent
from captain_hook.testing.helpers import build_context
from fire import fire
from hooks import lane_rotation, nudges, session_tree, turns
from hooks.compaction_handoff import CompactionState

ROOT_AT = datetime(2026, 9, 25, 21, 35, tzinfo=UTC)
TEAM = "session-rot"
DESK = "alanding-desk-1a1a1a1a1a1a1a1a"
GOLDEN = Path(__file__).resolve().parents[1] / "capt-hook" / "hooks" / "tests" / "fixtures" / "rotation" / "golden"


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
    ) -> dict:
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

    def roster(self, team: str) -> None:
        members = [{"agentId": f"{member}@{team}", "name": member} for member in self.members[team]]
        (self.claude / "teams" / team).mkdir(parents=True, exist_ok=True)
        (self.claude / "teams" / team / "config.json").write_text(json.dumps({"name": team, "members": members}))

    def read(self, *names: str) -> None:
        for name in names:
            transcript = max((self.root.with_suffix("") / "subagents").glob(f"agent-a{name}-*.jsonl"), key=lambda path: path.stat().st_mtime)
            with transcript.open("a") as lane:
                lane.write(json.dumps(user(ROOT_AT, "ROTATE", sidechain=True)) + "\n")

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
    built = Tree(tmp_path)
    (built.claude / "settings.json").write_text(json.dumps({"autoCompactWindow": 600_000}))
    return built


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [ROOT_AT.timestamp()]
    monkeypatch.setattr(lane_rotation.time, "time", lambda: now[0])
    return now


@dataclass
class Stop:
    tree: Tree
    tasks: list[dict]

    def evt(self) -> StopEvent:
        raw = {"session_id": "0123456789abcdef", "transcript_path": str(self.tree.root), "cwd": str(self.tree.claude.parent)}
        ctx = build_context(transcript=Session.from_path(self.tree.root), session_dir=self.tree.claude / "session")
        return StopEvent(_raw=raw | {"background_tasks": self.tasks}, ctx=ctx)


def stop(tree: Tree, tasks: list[dict]) -> Stop:
    handle = Stop(tree, tasks)
    CompactionState(active=True).save(handle.evt())
    return handle


def rotate_lanes(handle: Stop) -> None:
    assert fire(lane_rotation, handle.evt()) == []


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
        f"ROOT-ACTION `alerts-watch`: Rotate it by hand: spawn a handoff subagent from `{lane_rotation.HANDOFF_BRIEF.name}`, "
        "spawn its successor from the lane brief plus that handoff, then `TaskStop` `alerts-watch@session-rot`."
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


def test_escalation_repeats_every_firing_without_piling_up(tree: Tree, clock: list[float]) -> None:
    evt = stop(tree, [tree.lane("desk-3", 450_000)])
    rotate_lanes(evt)
    tree.read("desk-3")
    clock[0] += lane_rotation.ACK_WINDOW_SECONDS
    for _ in range(3):
        rotate_lanes(evt)
        clock[0] += 60

    [line] = pending(evt)
    assert line.startswith("ROOT-ACTION `desk-3`: Rotate it by hand")
    assert [(entry["event"], entry.get("count"), entry.get("last")) for entry in timeline(evt)] == [
        ("ask", None, None),
        ("escalate", 3, "2026-09-25T21:47:00Z"),
    ]


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


def test_lanes_sharing_a_description_each_name_their_own_stop_id(tree: Tree, clock: list[float]) -> None:
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
    assert lines["ccx-guard-eperm"].endswith("then `TaskStop` `ccx-guard-eperm@session-rot`.")
    assert lines["landing-desk-2"].endswith("then `TaskStop` `landing-desk-2@session-rot`.")


def test_stopped_lane_sharing_a_description_is_gone_not_matched_to_a_live_task(tree: Tree, clock: list[float]) -> None:
    shared = "Lane brief (long-running drive release-v3, root = s"
    tree.lane("ccx-guard-eperm", 513_462, description=shared, behind=timedelta(minutes=1))
    live = tree.lane("landing-desk-2", 440_000, description=shared, behind=timedelta(minutes=2))
    evt = stop(tree, [live, live])
    rotate_lanes(evt)
    tree.read("landing-desk-2")

    tree.kill("ccx-guard-eperm")
    evt = stop(tree, [live])
    assert [(lane.name, lane.stop_id) for lane in lane_rotation.live_lanes(evt.evt())] == [("landing-desk-2", "landing-desk-2@session-rot")]

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
    "body",
    ["still working on #25751", "Flushed: b037decf", "flushed abc"],
)
def test_reply_parsing(body: str) -> None:
    replies = lane_rotation.flushed_replies(f'<teammate-message teammate_id="desk">\n{body}\n</teammate-message>')
    assert replies == ([("desk", ["b037decf"])] if body.startswith("Flushed") else [])


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
