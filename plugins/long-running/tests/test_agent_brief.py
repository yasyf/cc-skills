import io
import os
from collections.abc import Callable

import agent_brief
import pytest
from conftest import RESEARCH

Run = Callable[..., tuple[int, str]]


def started(subagent_type: str, prompt: str, model: str | None = None) -> dict:
    tool_input = {"subagent_type": subagent_type, "prompt": prompt, "description": "d"} | ({"model": model} if model else {})
    return {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "tool_name": "Agent", "tool_input": tool_input}


def test_a_design_job_starts_only_as_the_designer(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Restores", "restores copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    monkeypatch.setattr("sys.stdin", io.StringIO(RESEARCH))
    run("job", "report", "j1", "-")
    run("job", "add", "design", "--order", "o1", "--goal", "design the copy")
    prompt = "Researched: j1\nDrive: demo, order o1, job j2"
    assert agent_brief.deny_reason(started("codex:codex-wrapper", prompt)) == (
        "refused: j2 is a design job, which runs only as long-running:designer, since only that definition keeps it read"
        " only and its forks in his cards' shape; start it again with subagent_type long-running:designer"
    )
    assert agent_brief.deny_reason(started("long-running:designer", prompt, "fable")) is None


def test_a_codex_lane_on_the_immediate_fix_starts_with_no_research(demo: str, run: Run) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Restores", "restores copy one store", "--kind", "build", "--parts", "the copy", "immediate fix")
    run("job", "add", "codex", "--order", "o1", "--part", "immediate fix", "--goal", "stop the stuck restores")
    prompt = "Order (his words): restores copy one store\nDrive: demo, order o1, job j1"
    assert agent_brief.deny_reason(started("codex:codex-wrapper", prompt)) is None


def test_a_codex_lane_on_a_build_order_waits_on_its_readers_research(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Restores", "restores copy one store", "--kind", "build")
    run("job", "add", "codex", "--order", "o1", "--goal", "build the copy")
    prompt = "Order (his words): restores copy one store\nDrive: demo, order o1, job j1"
    refused = agent_brief.deny_reason(started("codex:codex-wrapper", prompt))
    assert refused.startswith("refused: j1 is a codex lane on o1, a build order, which starts only once an Opus reader has kept")
    assert 'drive job add reader --order o1 --goal "research: <what>"' in refused
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    monkeypatch.setattr("sys.stdin", io.StringIO(RESEARCH))
    run("job", "report", "j2", "-")
    assert agent_brief.deny_reason(started("codex:codex-wrapper", prompt)) is None


def test_a_builders_serves_line_names_one_of_the_three(demo: str) -> None:
    order = "Order (his words): fix the watch\n"
    refused = agent_brief.deny_reason(started("long-running:builder", f"{order}Serves: cleanup I noticed\nGoal: tidy the watch"))
    assert refused.startswith("refused: `Serves: cleanup I noticed` names none of `port of #N`, `drive tooling` or `his order`")
    for serves in ("his order o284", "port of #4203", '"fix the watch"'):
        assert agent_brief.deny_reason(started("long-running:builder", f"{order}Serves: {serves}\nGoal: fix it")) is None


def test_a_builder_passing_over_a_teams_data_names_what_it_runs_as(demo: str) -> None:
    order = "Order (his words): plat's runs are missing\nServes: his order\n"
    brief = f"{order}Goal: backfill run rows for team plat"
    refused = agent_brief.deny_reason(started("long-running:builder", brief))
    assert refused.startswith("refused: this builder's Goal reads as a pass over a team's data (`backfill`) and its brief has no `Runs as:` line")
    assert refused.endswith('--runs-as "<service account, how it starts>"`, or `--runs-as "none: <why>"` when the job runs no pass')
    for runs_as in ("svc-plat-backfill in the plat cluster, a cronjob", "none: it edits PR bodies"):
        assert agent_brief.deny_reason(started("long-running:builder", f"{brief}\nRuns as: {runs_as}")) is None
    for goal, word in (
        ("repair the two Cargill databases whose bucket prefixes are empty", "repair"),
        ("queued: migrate old-runner runs as each cluster's service account", "queued: migrate"),
        ("run the merged cleanup delete_old_generations on plat", "run the merged cleanup"),
        ("plat's run rows are backfilled", "are backfilled"),
    ):
        assert f"(`{word}`)" in agent_brief.deny_reason(started("long-running:builder", f"{order}Goal: {goal}"))
    for goal in (
        "write a migration adding a column",
        "the Show me of three PRs is redrawn as one compare each so the backfill stack's tap card draws",
        "rerun the backfill stack's red check",
        "api: ✨ a service account's API key runs admin backfills through its own permission",
        "migrate tests to pytest",
    ):
        assert agent_brief.deny_reason(started("long-running:builder", f"{order}Goal: {goal}")) is None


def test_a_ninth_builder_here_waits_and_its_refusal_names_the_jobs_waiting_oldest_due_first(demo: str, run: Run) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "tidy the docs")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "fix the card", "--due", "+2h", "--thread", "1.2")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "fix the watch", "--due", "+1h", "--thread", "1.3")
    run("job", "add", "builder", "--order", "o1", "--goal", "tidy the docs")
    run("job", "add", "builder", "--order", "o2", "--goal", "fix the card")
    run("job", "add", "builder", "--order", "o3", "--goal", "fix the watch")
    for number in range(1, 9):
        run("job", "add", "builder", "--order", "o1", "--goal", f"build part {number}", "--agent", f"a{number}")
    run("job", "add", "builder", "--order", "o3", "--goal", "fix the watch on a Sprite", "--remote")
    brief = "Order (his words): fix the watch\nServes: his order\nGoal: fix it\nDrive: demo, order o3, job j3"
    refused = agent_brief.deny_reason(started("long-running:builder", brief))
    assert refused.startswith(
        "refused: 8 builders already run on this Mac, its cap of 8, so this one waits: start no builder here until one"
        " finishes, then start the first job waiting, oldest due first: j3 (due "
    )
    assert refused.index('"fix the watch"; j2 (due ') < refused.index('"fix the card"; j1 (no time promised) "tidy the docs".')
    assert "j12" not in refused
    assert refused.endswith("A Forge-AI/monorepo builder need not wait: start it on a Sprite with drive job launch <j> <brief>")
    run("job", "set", "j4", "--state", "stopped")
    assert agent_brief.deny_reason(started("long-running:builder", brief)) is None
