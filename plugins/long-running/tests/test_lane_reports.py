from __future__ import annotations

from pathlib import Path

import pytest
from captain_hook.events import PostToolUseEvent, StopEvent, SubagentStopEvent
from captain_hook.testing.helpers import build_context

from fire import fire
from hooks import lane_reports
from hooks.compaction_handoff import CompactionState

SESSION = "0123456789abcdef"
REPORT = "Landed #30441 at 06:40.\n" + "Verified the row 4 start on dev, the bake, and the release plan in full. " * 6
ONE_LINER = "done: #30441 landed; report sent to team-lead"


def drive(home: Path, *, active: bool = True) -> Path:
    state = home / "state"
    seed = PostToolUseEvent(_raw={"session_id": SESSION, "tool_name": "Bash"}, ctx=build_context(session_dir=state))
    CompactionState(active=active).save(seed)
    return state


def stop(state: Path, cls=StopEvent, **raw) -> list[str]:
    evt = cls(_raw={"session_id": SESSION, "stop_hook_active": False} | raw, ctx=build_context(session_dir=state))
    return [result.message for result in fire(lane_reports, evt)]


@pytest.mark.parametrize(
    "who",
    [
        {"agent_id": "arow4-without-panic-fix-5cfd0bd6c6932b9c", "agent_type": "row4-without-panic-fix"},
        {"agent_id": "a44fd2b6534c7cc71", "agent_type": "long-running:lane-ship"},
        {"agent_id": "a44fd2b6534c7cc71", "agent_type": "long-running:lane"},
    ],
)
def test_a_lane_final_text_over_the_cap_is_refused(tmp_path: Path, who: dict) -> None:
    assert stop(drive(tmp_path), last_assistant_message=REPORT, **who) == [lane_reports.ONE_LINE]


def test_a_lane_subagent_stop_over_the_cap_is_refused(tmp_path: Path) -> None:
    raw = {"agent_id": "a44fd2b6534c7cc71", "agent_type": "long-running:lane", "last_assistant_message": REPORT}

    assert stop(drive(tmp_path), SubagentStopEvent, **raw) == [lane_reports.ONE_LINE]


@pytest.mark.parametrize("text", [ONE_LINER, "x" * lane_reports.FINAL_TEXT_CAP, "", f"  {ONE_LINER}  \n"])
def test_a_lane_one_liner_passes(tmp_path: Path, text: str) -> None:
    raw = {"agent_id": "arow4-without-panic-fix-5cfd0bd6c6932b9c", "agent_type": "row4-without-panic-fix"}

    assert stop(drive(tmp_path), last_assistant_message=text, **raw) == []


@pytest.mark.parametrize(
    "who",
    [
        {"agent_id": "a44fd2b6534c7cc71", "agent_type": "general-purpose"},
        {"agent_id": "a44fd2b6534c7cc71", "agent_type": "codex:codex-wrapper"},
        {},
    ],
)
def test_non_lane_agents_and_the_root_are_untouched(tmp_path: Path, who: dict) -> None:
    assert stop(drive(tmp_path), last_assistant_message=REPORT, **who) == []


def test_outside_a_drive_is_quiet(tmp_path: Path) -> None:
    raw = {"agent_id": "arow4-without-panic-fix-5cfd0bd6c6932b9c", "agent_type": "row4-without-panic-fix"}

    assert stop(drive(tmp_path, active=False), last_assistant_message=REPORT, **raw) == []
