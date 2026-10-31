from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from cc_transcript import Session
from captain_hook.events import PreToolUseEvent, SessionStartEvent
from captain_hook.testing.helpers import build_context

from hooks import compaction_handoff as handoff
from hooks import ruling_judge as judge

FIXTURES = Path(handoff.__file__).parent / "tests" / "fixtures"
TRANSCRIPT = str(FIXTURES / "usage-460k.jsonl")


def pre(tmp_path: Path, tool_name: str, tool_input: dict, **raw) -> PreToolUseEvent:
    raw = {"session_id": "s1", "transcript_path": TRANSCRIPT, "cwd": str(tmp_path), "tool_name": tool_name, "tool_input": tool_input} | raw
    return PreToolUseEvent(_raw=raw, ctx=build_context(transcript=Session.from_path(Path(TRANSCRIPT)), session_dir=tmp_path / "s"))


def session_start(tmp_path: Path, source: str) -> SessionStartEvent:
    raw = {"session_id": "s1", "transcript_path": TRANSCRIPT, "cwd": str(tmp_path), "source": source}
    return SessionStartEvent(_raw=raw, ctx=build_context(transcript=Session.from_path(Path(TRANSCRIPT)), session_dir=tmp_path / "s"))


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
    JUDGE_SECONDS = 90

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_name: str, tool_input: dict) -> None:
        self.searches: list[tuple[str, ...]] = []
        self.model_calls: list[tuple[str, str]] = []
        self.evt = pre(tmp_path, tool_name, tool_input)
        with judge.JudgedAnswers.mutate(self.evt) as state:
            state.injected.append("main:1984bf6")
        monkeypatch.setattr(judge, "drive_args", lambda evt: ["--program", "brook"])
        monkeypatch.setattr(judge, "rulings", self.search)
        monkeypatch.setattr(judge, "script_module", lambda name: self)

    def backend_from(self, env: dict) -> str:
        return "sonnet"

    def judge(self, kind: str, evidence: str, artifact: str, **kwargs) -> None:
        self.model_calls.append((kind, evidence))

    def search(self, cwd: str, *args: str, evt, stdin: str = "", timeout: int) -> str:
        assert evt is self.evt
        assert timeout == judge.MATCH_TIMEOUT_SECONDS
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
    assert judged.searches == [("match", "--program", "brook", "-k", "2", "--budget", "6000", "--exclude", "1984bf6")]
    assert judged.model_calls == []


def test_a_match_that_times_out_fails_open_without_a_model_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    judged = Judged(tmp_path, monkeypatch, "Agent", {"prompt": "Add a --skip-tenant flag."})

    def slow(cwd: str, *args: str, evt, stdin: str = "", timeout: int) -> str:
        raise subprocess.TimeoutExpired(["rulings.py", *args], timeout)

    monkeypatch.setattr(judge, "rulings", slow)

    assert judge.judge_key_moment(judged.evt) is None
    assert judged.model_calls == []


@pytest.mark.parametrize(
    ("source", "kept"),
    [
        ("compact", ["a1b2c3:1984bf6", "a1b2c3:2e7c0aa"]),
        ("resume", ["main:1984bf6", "a1b2c3:1984bf6", "main:2e7c0aa", "a1b2c3:2e7c0aa"]),
        ("startup", ["main:1984bf6", "a1b2c3:1984bf6", "main:2e7c0aa", "a1b2c3:2e7c0aa"]),
    ],
)
def test_a_compaction_forgets_the_roots_judged_answers_and_keeps_each_lanes(tmp_path: Path, source: str, kept: list[str]) -> None:
    started = session_start(tmp_path, source)
    with judge.JudgedAnswers.mutate(started) as state:
        state.injected = ["main:1984bf6", "a1b2c3:1984bf6", "main:2e7c0aa", "a1b2c3:2e7c0aa"]

    assert judge.forget_root_answers_after_compaction(started) is None
    assert judge.JudgedAnswers.load(started).injected == kept


def test_after_a_compaction_the_root_is_judged_again_on_an_answer_it_already_saw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    judged = Judged(tmp_path, monkeypatch, "Agent", {"prompt": "Add a --skip-tenant flag."})
    lane = pre(tmp_path, "Agent", {"prompt": "Add a --skip-tenant flag."}, agent_id="a1b2c3")
    with judge.JudgedAnswers.mutate(lane) as state:
        state.injected.append("a1b2c3:1984bf6")

    judge.forget_root_answers_after_compaction(session_start(tmp_path, "compact"))
    judge.judge_key_moment(judged.evt)
    judge.judge_key_moment(lane)

    assert judged.searches == [
        ("match", "--program", "brook", "-k", "2", "--budget", "6000"),
        ("match", "--program", "brook", "-k", "2", "--budget", "6000", "--exclude", "1984bf6"),
    ]
    assert len(judged.model_calls) == 1
