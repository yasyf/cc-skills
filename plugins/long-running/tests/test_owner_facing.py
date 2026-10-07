from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from cc_transcript.query import Session
from captain_hook.events import PostToolUseEvent, PreToolUseEvent, StopEvent
from captain_hook import CONFIRMED, UNCONFIRMED
from captain_hook.testing.helpers import StubbedContext, build_context

from fire import fire
from hooks import nudges, owner_facing
from hooks.compaction_handoff import CompactionState

MISFIRES = {
    "artifact-retry-repush": (
        "Lane artifact-retry-repush (release-v3). Effort xhigh. Authority: stack writes on branch "
        "yasyf/v3-incident-api-1n80-sandsql-fetch-fix (PR #29016, SandSQL fetch retry, incident fix ahead of parity) only."
    ),
    "incident-runner": (
        "Lane incident-runner (release-v3 tooling track). Effort xhigh. Owner go: implement brief 1 of the astra deep "
        "dive, a durable incident executor in the long-running plugin."
    ),
    "desk-runner": (
        "Lane desk-runner (release-v3 tooling track). Effort xhigh. Owner go: implement brief 3 of the astra deep dive. "
        "Scope: incident relay plus one landing workflow."
    ),
}
ROLES = {"artifact-retry-repush": "ship", "incident-runner": "tooling", "desk-runner": "tooling"}


def entry(kind: str, text: str, *, sidechain: bool = False) -> dict:
    message = {"role": kind, "content": [{"type": "text", "text": text}]}
    if kind == "assistant":
        usage = {"input_tokens": 2, "output_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        message |= {"model": "claude-opus-5-5", "usage": usage}
    return {
        "type": kind,
        "isSidechain": sidechain,
        "timestamp": "2026-10-01T12:00:00.000Z",
        "uuid": str(uuid.uuid4()),
        "parentUuid": None,
        "sessionId": "0123456789abcdef",
        "message": message,
    }


class Root:
    def __init__(self, home: Path, *, active: bool = True) -> None:
        self.transcript = home / "root.jsonl"
        self.session_dir = home / "state"
        seed = PostToolUseEvent(
            _raw={"session_id": "0123456789abcdef", "tool_name": "Bash"}, ctx=build_context(session_dir=self.session_dir)
        )
        CompactionState(active=active).save(seed)

    def event(self, cls, **raw):
        payload = {"session_id": "0123456789abcdef", "transcript_path": str(self.transcript), "cwd": str(self.transcript.parent)}
        transcript = Session.from_path(self.transcript)
        return cls(_raw=payload | raw, ctx=build_context(transcript=transcript, session_dir=self.session_dir))

    def spawn(self, name: str, prompt: str, *, block: bool = True) -> str | None:
        raw = {"session_id": "0123456789abcdef", "cwd": str(self.transcript.parent)}
        ctx = StubbedContext.wrapping(
            build_context(session_dir=self.session_dir), decisions=CONFIRMED if block else UNCONFIRMED
        )
        tool_input = {"name": name, "prompt": prompt, "subagent_type": "long-running:lane-ship"}
        evt = PreToolUseEvent(_raw=raw | {"tool_name": "Agent", "tool_input": tool_input}, ctx=ctx)
        results = fire(owner_facing, evt)
        return results[0].message if results else None

    def reply(self, *entries: dict) -> list[str]:
        self.transcript.write_text("".join(json.dumps(item) + "\n" for item in entries))
        evt = self.event(StopEvent)
        owner_facing.nudge_utc_in_owner_replies(evt)
        with nudges.NudgeState.mutate(evt) as state:
            pending, state.pending = state.pending, []
        return pending


@pytest.fixture
def root(tmp_path: Path) -> Root:
    return Root(tmp_path)


@pytest.mark.parametrize(
    "text",
    ["Fix lane spawned at 17:35Z; mechanism by 17:45Z.", "Owner asks landed 17:3xZ.", "Red since 16:52 UTC."],
)
def test_utc_times_in_the_last_reply_nudge(root: Root, text: str) -> None:
    assert root.reply(entry("user", "status?"), entry("assistant", text)) == [owner_facing.UTC_IN_REPLY]


@pytest.mark.parametrize("text", ["Fix lane spawned at 10:35am; mechanism by 10:45.", "Build 72755 is red."])
def test_pacific_or_timeless_replies_are_quiet(root: Root, text: str) -> None:
    assert root.reply(entry("assistant", text)) == []


def test_only_the_root_reply_counts(root: Root) -> None:
    assert root.reply(entry("assistant", "Pacific 10:35am."), entry("assistant", "lane at 17:35Z", sidechain=True)) == []


def test_outside_a_drive_is_quiet(tmp_path: Path) -> None:
    assert Root(tmp_path, active=False).reply(entry("assistant", "at 17:35Z")) == []


@pytest.mark.parametrize("name", MISFIRES)
def test_logged_misfires_pass_once_their_role_is_declared(root: Root, name: str) -> None:
    header = f"ccx: role={ROLES[name]}" + (f" tooling-lane={name}" if ROLES[name] == "tooling" else "")

    assert root.spawn(name, f"{header}\n{MISFIRES[name]}") is None


@pytest.mark.parametrize("name", MISFIRES)
def test_logged_misfires_without_a_role_go_to_the_model(root: Root, name: str) -> None:
    assert root.spawn(name, MISFIRES[name], block=False) is None


@pytest.mark.parametrize("name", MISFIRES)
def test_logged_misfires_without_a_role_block_on_a_confident_match(root: Root, name: str) -> None:
    assert root.spawn(name, MISFIRES[name]) == owner_facing.INCIDENT_ROUTE


def test_a_fix_lane_with_incident_turn_authority_blocks_without_the_model(root: Root) -> None:
    prompt = "ccx: role=fix\nYou are api-1n80-fix, fixing the outage behind API-1N80.\nAuthority: Incident Turn."

    assert root.spawn("api-1n80-fix", prompt, block=False) == owner_facing.INCIDENT_ROUTE


def test_a_fix_lane_naming_an_outage_without_authority_goes_to_the_model(root: Root) -> None:
    prompt = "ccx: role=fix\nYou are api-1n80-fix, fixing the outage behind API-1N80."

    assert root.spawn("api-1n80-fix", prompt, block=False) is None


@pytest.mark.parametrize(
    ("name", "prompt"),
    [
        ("incident-api-1n80-evidence", "ccx: role=evidence\nRead telemetry for the incident."),
        ("ledger-fix", "ccx: role=fix\nFix the ledger's quota probe."),
        ("incident-api-1n80-watch", "ccx: role=watch\nWatch the incident thread."),
    ],
)
def test_declared_support_and_non_incident_fix_lanes_pass(root: Root, name: str, prompt: str) -> None:
    assert root.spawn(name, prompt) is None
