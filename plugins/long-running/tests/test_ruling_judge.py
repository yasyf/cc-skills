from __future__ import annotations

from pathlib import Path

import pytest
from cc_transcript import Session
from captain_hook.events import PreToolUseEvent
from captain_hook.testing.helpers import build_context

from hooks import compaction_handoff as handoff
from hooks import ruling_judge as judge

FIXTURES = Path(handoff.__file__).parent / "tests" / "fixtures"
TRANSCRIPT = str(FIXTURES / "usage-460k.jsonl")


def pre(tmp_path: Path, tool_name: str, tool_input: dict) -> PreToolUseEvent:
    raw = {"session_id": "s1", "transcript_path": TRANSCRIPT, "cwd": str(tmp_path), "tool_name": tool_name, "tool_input": tool_input}
    return PreToolUseEvent(_raw=raw, ctx=build_context(transcript=Session.from_path(Path(TRANSCRIPT)), session_dir=tmp_path / "s"))


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "expected"),
    [
        ("Write", {"file_path": "/Users/u/.claude/plans/brook.md", "content": "# brook\n- step 3: add a flag"}, ("a plan step", "# brook\n- step 3: add a flag")),
        ("Write", {"file_path": "/Users/u/notes/brook.md", "content": "x"}, None),
        ("mcp__slack__slack_send_message", {"channel_id": "C1", "text": "Shipping now"}, ("a Slack write", "Shipping now")),
        ("Bash", {"command": "ccx vcs ship -m 'release: add a skip flag' --pr-title 'release: add a skip flag'"}, ("a pull request", "vcs ship -m release: add a skip flag --pr-title release: add a skip flag")),
        ("Bash", {"command": "git status"}, None),
    ],
)
def test_key_moments_are_recognized_structurally(tmp_path: Path, tool_name: str, tool_input: dict, expected: tuple | None) -> None:
    assert judge.moment(pre(tmp_path, tool_name, tool_input)) == expected


def test_an_orca_launch_reads_its_brief(tmp_path: Path) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text("Lane brief: retire valkey with a migration script.")

    found = judge.moment(pre(tmp_path, "Bash", {"command": f"orca-launch.sh valkey-fold opus xhigh {brief}"}))

    assert found == ("a lane spawn", "Lane brief: retire valkey with a migration script.")
