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
        ("mcp__slack__slack_send_message", {"channel_id": "C1", "text": "Shipping now"}, None),
        ("mcp__plugin_cc-slack_cc-slack__slack_reply", {"url": "C1/p1", "text": "Shipping now"}, None),
        ("Bash", {"command": "cc-slack send --channel C1 --text 'Shipping now'"}, None),
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


class Judged:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_name: str, tool_input: dict) -> None:
        self.searches: list[tuple[str, ...]] = []
        self.model_calls: list[str] = []
        self.evt = pre(tmp_path, tool_name, tool_input)
        with judge.JudgedAnswers.mutate(self.evt) as state:
            state.injected.append("main:1984bf6")
        monkeypatch.setattr(judge, "drive_args", lambda evt: ["--program", "brook"])
        monkeypatch.setattr(judge, "rulings", self.search)
        monkeypatch.setattr(PreToolUseEvent, "llm", lambda evt, prompt, *args, **kwargs: self.model_calls.append(prompt))

    def search(self, cwd: str, *args: str, stdin: str = "") -> str:
        self.searches.append(args)
        return "" if ("--exclude", "1984bf6") in zip(args, args[1:]) else judge.CANDIDATE


def test_a_slack_send_carrying_an_injected_ruling_makes_no_model_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    judged = Judged(tmp_path, monkeypatch, "mcp__plugin_cc-slack_cc-slack__slack_send", {"channel": "C1", "text": "Shipping the skip flag"})

    assert judge.judge_key_moment(judged.evt) is None
    assert judged.searches == []
    assert judged.model_calls == []


def test_an_injected_ruling_is_excluded_from_the_search_and_skips_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    judged = Judged(tmp_path, monkeypatch, "Agent", {"prompt": "Add a --skip-tenant flag."})

    assert judge.judge_key_moment(judged.evt) is None
    assert judged.searches == [("match", "--program", "brook", "-k", "5", "--budget", "6000", "--exclude", "1984bf6")]
    assert judged.model_calls == []
