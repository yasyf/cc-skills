import json
from collections.abc import Callable
from pathlib import Path

import landing_guard
import pytest
from conftest import SESSION
from drive import state, view

Run = Callable[..., tuple[int, str]]
PR = "https://github.com/Forge-AI/monorepo/pull/42"
SIBLINGS = ("https://github.com/Forge-AI/monorepo/pull/41", "https://github.com/Forge-AI/monorepo/pull/43")
CARD = "demo:j1-661229"
LIVE = "https://github.com/yasyf/cc-skills/blob/3d8111f/pr-shots/live-hover.png?raw=true"
DEMO = "https://github.com/yasyf/cc-skills/blob/3d8111f/pr-shots/demo-hover/x.png?raw=true"


@pytest.fixture
def drive_pr(run: Run, demo: str) -> str:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    run("pr", "add", PR, "--job", "j1", "--tap")
    return state.pr_key(PR)


@pytest.fixture
def no_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def record(args: list[str], cwd: str) -> str:
        raise AssertionError(f"unexpected call: {args}")

    monkeypatch.setattr(landing_guard, "run", record)


def bash(command: str, cwd: str = "/work", **fields: object) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd, "session_id": SESSION, **fields}


def approve(key: str) -> None:
    with state.edit("demo") as data:
        data["prs"][key]["tap"] = {"label": "Approve", "at": state.stamp(), "head": "abc", "source": "slack"}


@pytest.mark.parametrize(
    ("command", "step"),
    [
        ('ccx vcs ship -m "Fix: x"', "ccx vcs ship"),
        ("for j in j1 j2; do drive job end $j; done", "for"),
        ("cd /work && orca terminal close --terminal term_9ef3a5e3", "orca terminal close"),
        ("bash -c 'go build ./...'", "go build ./..."),
    ],
)
def test_the_coordinators_own_ship_loop_orca_or_build_call_is_refused_naming_a_job(demo: str, no_calls: None, command: str, step: str) -> None:
    reason = landing_guard.deny_reason(bash(command, session_id=SESSION))
    assert reason == landing_guard.COORDINATOR_STEP.format(step=step)
    assert "Start a job with this step" in reason
    assert "drive job add" in reason


@pytest.mark.parametrize(
    "command",
    ["drive status o288", "orca orchestration worker-read --dispatch d1 --limit 50 --full --json", "cci show 661229 --json"],
)
def test_the_coordinators_reads_pass(demo: str, no_calls: None, command: str) -> None:
    assert landing_guard.deny_reason(bash(command, session_id=SESSION)) is None


def test_a_coordinators_agent_and_another_session_may_ship(demo: str, no_calls: None) -> None:
    assert landing_guard.deny_reason(bash('ccx vcs ship -m "Fix: x"', session_id=SESSION, agent_id="a1")) is None
    assert landing_guard.deny_reason(bash('ccx vcs ship -m "Fix: x"', session_id="elsewhere")) is None


def test_landing_while_his_card_on_it_is_open_is_refused_naming_the_card_to_wait_on(drive_pr: str, no_calls: None) -> None:
    approve(drive_pr)
    with state.edit("demo") as data:
        data["prs"][drive_pr]["waits"] = [CARD]
    reason = landing_guard.deny_reason(bash("stack-enqueue 42"))
    assert reason == landing_guard.CARD_REASON.format(key=drive_pr, cards=CARD)
    assert f"return `WAITING {CARD}`" in reason
    with state.edit("demo") as data:
        data["prs"][drive_pr]["waits"] = []
        data["jobs"]["j1"]["waits"] = [CARD]
    assert landing_guard.deny_reason(bash("stack-enqueue 42")) == reason


def test_landing_once_he_answered_the_card_passes(drive_pr: str, no_calls: None) -> None:
    approve(drive_pr)
    with state.edit("demo") as data:
        data["prs"][drive_pr]["waits"] = [CARD]
    state.cards_path("demo").write_text(json.dumps({"source": "live", **view.NO_CARDS}))
    assert landing_guard.deny_reason(bash("stack-enqueue 42")) is None


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 42 -R Forge-AI/monorepo --squash",
        "gh pr edit 42 -R Forge-AI/monorepo --add-label merge-fast",
        "gh pr edit 42 -R Forge-AI/monorepo --add-label merge",
        "gh api -X PUT repos/Forge-AI/monorepo/pulls/42/merge -f merge_method=squash",
    ],
)
def test_a_tapped_monorepo_pr_landed_around_the_queue_is_refused_naming_stack_enqueue(drive_pr: str, no_calls: None, command: str) -> None:
    approve(drive_pr)
    reason = landing_guard.deny_reason(bash(command))
    assert reason == landing_guard.QUEUE_REASON.format(key=drive_pr)
    assert "stack-enqueue" in reason
    assert landing_guard.deny_reason(bash(command, session_id="ordinary")) is None


def test_a_tapped_monorepo_pr_enqueued_or_an_incident_fix_merged_passes(drive_pr: str, no_calls: None) -> None:
    approve(drive_pr)
    assert landing_guard.deny_reason(bash(".agents/skills/submit-pr/scripts/stack-enqueue 42")) is None
    with state.edit("demo") as data:
        data["prs"][drive_pr]["exempt"] = "incident fix"
    assert landing_guard.deny_reason(bash("gh pr merge 42 -R Forge-AI/monorepo --squash")) is None


@pytest.fixture
def stored_stack(drive_pr: str, run: Run) -> list[str]:
    for url in SIBLINGS:
        run("pr", "add", url, "--job", "j1", "--tap")
    keys = [state.pr_key(SIBLINGS[0]), drive_pr, state.pr_key(SIBLINGS[1])]
    for key in keys:
        approve(key)
    with state.edit("demo") as data:
        for key in keys:
            data["prs"][key]["stack"] = keys
    return keys


def test_enqueueing_the_bottom_of_a_stored_stack_alone_is_refused_naming_every_pr(stored_stack: list[str], no_calls: None) -> None:
    reason = landing_guard.deny_reason(bash("stack-enqueue 41"))
    assert reason == landing_guard.STACK_REASON.format(named=stored_stack[0], left=", ".join(stored_stack[1:]), every="41 42 43")
    assert reason.endswith("`stack-enqueue 41 42 43`")


def test_enqueueing_every_open_pr_of_a_stored_stack_passes(stored_stack: list[str], no_calls: None) -> None:
    assert landing_guard.deny_reason(bash("stack-enqueue 41 42 43")) is None
    with state.edit("demo") as data:
        data["prs"][stored_stack[0]]["state"] = "merged"
    assert landing_guard.deny_reason(bash("stack-enqueue 42 43")) is None


def test_his_typed_approval_recorded_with_its_source_lets_the_pr_land(drive_pr: str, run: Run, no_calls: None) -> None:
    assert landing_guard.deny_reason(bash("stack-enqueue 42")).startswith(f"{drive_pr}: no tap from him yet")
    run("pr", "set", PR, "--tapped", "Approve", "--tap-source", "slack:https://acme.slack.com/archives/D123/p1794200000000100")
    assert landing_guard.deny_reason(bash("stack-enqueue 42")) is None


def body_post(tmp_path: Path, command: str, picture: str, **fields: object) -> dict:
    (tmp_path / "b.md").write_text(f"## Show me\n![Hover]({picture})\n")
    return bash(command, cwd=str(tmp_path), session_id=SESSION, agent_id="a1") | fields


@pytest.mark.parametrize(
    "command",
    ["gh pr create -R yasyf/cc-skills --title 'New: hover' --body-file b.md", "gh pr edit 7 --repo yasyf/cc-skills -F b.md"],
)
def test_a_drive_agents_public_pr_body_with_a_live_picture_is_refused_before_it_goes_up(
    demo: str, no_calls: None, visibilities: dict, tmp_path: Path, command: str
) -> None:
    visibilities["yasyf/cc-skills"] = "public"
    reason = landing_guard.deny_reason(body_post(tmp_path, command, LIVE))
    assert reason.startswith(
        f"Refused on purpose, before it goes up: yasyf/cc-skills is public, and its body shows pictures outside a demo or public path ({LIVE})"
    )
    assert reason.endswith("Fix the body, then run this again")
    assert landing_guard.deny_reason(body_post(tmp_path, command, DEMO)) is None
    assert landing_guard.deny_reason(body_post(tmp_path, command, LIVE, session_id="elsewhere")) is None


def test_a_drive_agents_public_pr_body_naming_a_teammate_is_refused_before_it_goes_up(
    demo: str, no_calls: None, visibilities: dict, tmp_path: Path
) -> None:
    visibilities["yasyf/cc-skills"] = "public"
    (tmp_path / "b.md").write_text("## Refresher\nRowan's report started this.\n")
    create = bash("gh pr create -R yasyf/cc-skills --title X --body-file b.md", cwd=str(tmp_path), session_id=SESSION, agent_id="a1")
    assert landing_guard.deny_reason(create).startswith(
        "Refused on purpose, before it goes up: yasyf/cc-skills is public, and its body names Rowan (Rowan Vale). "
    )
    (tmp_path / "b.md").write_text("## Refresher\nThe reporter's report started this.\n")
    assert landing_guard.deny_reason(create) is None


def test_a_pr_body_is_judged_by_the_repository_of_the_pr_it_names(demo: str, monkeypatch: pytest.MonkeyPatch, visibilities: dict, tmp_path: Path) -> None:
    monkeypatch.setattr(landing_guard, "run", lambda args, cwd: "https://github.com/yasyf/cc-skills.git")
    visibilities["yasyf/cc-skills"] = "public"
    assert landing_guard.deny_reason(body_post(tmp_path, f"gh pr edit {PR} -F b.md", LIVE)) is None
    public = "gh pr edit https://github.com/yasyf/cc-skills/pull/7 -F b.md"
    assert "yasyf/cc-skills is public" in landing_guard.deny_reason(body_post(tmp_path, public, LIVE))


def test_a_body_file_written_in_the_same_call_is_refused_until_it_exists(demo: str, no_calls: None, visibilities: dict, tmp_path: Path) -> None:
    visibilities["yasyf/cc-skills"] = "public"
    create = f"gh pr create -R yasyf/cc-skills --title X --body-file {tmp_path}/new.md"
    written = f"cat > {tmp_path}/new.md <<'EOF'\n## What you'll hate most\n![Live]({LIVE})\nEOF\n{create}"
    reason = landing_guard.deny_reason(bash(written, agent_id="a1"))
    assert reason.startswith(f"Refused on purpose, before it goes up: the body file {tmp_path}/new.md does not exist yet")
    assert reason.endswith("Write the file in a call of its own, then run this one")
    (tmp_path / "new.md").write_text(f"## Show me\n![Hover]({DEMO})\n")
    assert landing_guard.deny_reason(bash(create, agent_id="a1")) is None


def test_a_ship_with_a_public_pr_body_reads_its_repo_from_the_checkout(
    demo: str, monkeypatch: pytest.MonkeyPatch, visibilities: dict, tmp_path: Path
) -> None:
    monkeypatch.setattr(landing_guard, "run", lambda args, cwd: "https://github.com/yasyf/cc-skills.git")
    visibilities["yasyf/cc-skills"] = "public"
    command = "ccx vcs ship -m 'New: hover' --pr-body-file hover=b.md"
    assert "yasyf/cc-skills is public" in landing_guard.deny_reason(body_post(tmp_path, command, LIVE))
    assert landing_guard.deny_reason(body_post(tmp_path, command, DEMO)) is None
    visibilities["yasyf/cc-skills"] = "private"
    assert landing_guard.deny_reason(body_post(tmp_path, command, LIVE)) is None
