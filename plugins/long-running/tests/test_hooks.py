import io
import json
import os
from collections.abc import Callable

import landing_guard
import pytest
import session_start
from drive import state

Run = Callable[..., tuple[int, str]]
PR = "https://github.com/Forge-AI/monorepo/pull/42"
ORIGIN = "git@github.com:Forge-AI/monorepo.git"


@pytest.fixture
def drive_pr(run: Run, demo: str) -> str:
    run("order", "add", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    run("pr", "add", PR, "--job", "j1", "--tap")
    return state.pr_key(PR)


@pytest.fixture
def no_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def record(args: list[str], cwd: str) -> str:
        calls.append(args)
        raise AssertionError(f"unexpected call: {args}")

    monkeypatch.setattr(landing_guard, "run", record)
    return calls


def origin_is(monkeypatch: pytest.MonkeyPatch, remote: str) -> None:
    def answer(args: list[str], cwd: str) -> str:
        assert args == ["git", "remote", "get-url", "origin"]
        return remote

    monkeypatch.setattr(landing_guard, "run", answer)


def bash(command: str, cwd: str = "/work") -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd}


def approve(key: str) -> None:
    with state.edit("demo") as data:
        data["prs"][key]["tap"] = {"label": "Approve", "at": state.stamp(), "head": "abc", "source": "slack"}


def hook_output(module: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stdin: str) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    module.main()
    return capsys.readouterr().out


def test_session_start_gives_the_driver_its_context(demo: str, run: Run) -> None:
    run("order", "add", "ship the fix")
    output = session_start.respond({"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "source": "resume"})
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "You are the driver of drive demo." in context
    assert "ship the fix" in context


def test_session_start_prints_nothing_for_another_session(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert session_start.respond({"session_id": "someone-else"}) is None
    assert hook_output(session_start, monkeypatch, capsys, json.dumps({"session_id": "someone-else"})) == ""


def test_session_start_main_prints_hook_json(
    demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = json.loads(hook_output(session_start, monkeypatch, capsys, json.dumps({"session_id": os.environ["CLAUDE_CODE_SESSION_ID"]})))
    assert "drive demo" in output["hookSpecificOutput"]["additionalContext"]


def test_guard_denies_an_untapped_drive_pr(
    drive_pr: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    origin_is(monkeypatch, ORIGIN)
    output = json.loads(hook_output(landing_guard, monkeypatch, capsys, json.dumps(bash("gh pr merge 42 --squash"))))
    decision = output["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"
    assert decision["permissionDecisionReason"].startswith("Forge-AI/monorepo#42: no tap from him yet")
    assert "slack_dm_status tap_prs" in decision["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    [
        f"gh pr merge {PR} --squash",
        "cd /work/monorepo && gh pr merge 42 -R Forge-AI/monorepo --auto",
        "gh pr edit 42 --repo Forge-AI/monorepo --add-label merge",
        "GH_TOKEN=x ccx vcs merge 42 --repo Forge-AI/monorepo",
    ],
)
def test_guard_denies_every_landing_form(drive_pr: str, no_calls: list[list[str]], command: str) -> None:
    assert landing_guard.deny_reason(bash(command)) is not None


def test_guard_allows_a_tapped_drive_pr(drive_pr: str, no_calls: list[list[str]]) -> None:
    approve(drive_pr)
    assert landing_guard.deny_reason(bash(f"gh pr merge {PR} --squash")) is None


def test_guard_denies_a_held_drive_pr(drive_pr: str, no_calls: list[list[str]]) -> None:
    with state.edit("demo") as data:
        data["prs"][drive_pr]["tap"] = {"label": "Hold", "at": state.stamp(), "head": "abc", "source": "slack"}
    assert "held by him" in landing_guard.deny_reason(bash(f"gh pr merge {PR} --squash"))


def test_guard_allows_an_exempt_drive_pr(drive_pr: str, run: Run, no_calls: list[list[str]]) -> None:
    run("pr", "set", PR, "--exempt", "test-only")
    assert landing_guard.deny_reason(bash(f"gh pr merge {PR} --squash")) is None


def test_guard_allows_a_pr_the_drive_does_not_know(drive_pr: str, no_calls: list[list[str]]) -> None:
    assert landing_guard.deny_reason(bash("gh pr merge 999 -R Forge-AI/monorepo --squash")) is None


def test_guard_denies_admin_on_forge_even_when_tapped(drive_pr: str, no_calls: list[list[str]]) -> None:
    approve(drive_pr)
    reason = landing_guard.deny_reason(bash(f"gh pr merge {PR} --squash --admin"))
    assert reason == landing_guard.ADMIN_REASON


def test_guard_allows_a_repo_outside_forge(drive_pr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    origin_is(monkeypatch, "https://github.com/yasyf/cc-skills.git")
    assert landing_guard.deny_reason(bash("gh pr merge 42 --squash --admin")) is None


def test_guard_ignores_commands_that_do_not_land(drive_pr: str, no_calls: list[list[str]]) -> None:
    assert landing_guard.deny_reason(bash("gh pr view 42 -R Forge-AI/monorepo && echo 'gh pr merge 42'")) is None


def test_guard_resolves_a_branch_through_gh(drive_pr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def answer(args: list[str], cwd: str) -> str:
        assert args[:3] == ["gh", "pr", "view"]
        return PR

    monkeypatch.setattr(landing_guard, "run", answer)
    assert landing_guard.deny_reason(bash("gh pr merge my-branch -R Forge-AI/monorepo")) is not None


@pytest.mark.parametrize("stdin", ["not json", "{}", json.dumps({"tool_input": {"command": "gh pr merge 'unclosed"}})])
def test_guard_allows_malformed_input(
    drive_pr: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stdin: str
) -> None:
    assert hook_output(landing_guard, monkeypatch, capsys, stdin) == ""
