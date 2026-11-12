import io
import os
from collections.abc import Callable
from datetime import timedelta

import agent_brief
import pytest
from conftest import RESEARCH
from drive import agents, state, watch
from drive.render import when

Run = Callable[..., tuple[int, str]]


def started(subagent_type: str, prompt: str, model: str | None = None) -> dict:
    tool_input = {"subagent_type": subagent_type, "prompt": prompt, "description": "d"} | ({"model": model} if model else {})
    return {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "tool_name": "Agent", "tool_input": tool_input}


def test_a_design_job_starts_as_the_designer(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Restores", "restores copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    monkeypatch.setattr("sys.stdin", io.StringIO(RESEARCH))
    run("job", "report", "j1", "-")
    run("job", "add", "design", "--order", "o1", "--goal", "design the copy")
    prompt = "Researched: j1\nDrive: demo, order o1, job j2"
    for call in (started("codex:codex-wrapper", prompt), started("long-running:builder", prompt, "fable")):
        assert agents.shaped(call) == call["tool_input"] | {"subagent_type": "long-running:designer"}
    assert agent_brief.deny_reason(started("long-running:designer", prompt, "fable")) is None


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


def test_a_ninth_builder_here_is_held_waiting_its_turn_and_only_the_one_the_watch_frees_starts_while_others_wait(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrote = state.now() - timedelta(minutes=3)
    monkeypatch.setattr(watch, "last_write", lambda job, data: (wrote, None))
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "tidy the docs")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix the card", "--due", "+2h", "--thread", "1.2")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix the watch", "--due", "+1h", "--thread", "1.3")
    run("job", "add", "builder", "--order", "o3", "--goal", "fix the watch")
    for number in range(1, 9):
        run("job", "add", "builder", "--order", "o1", "--goal", f"build part {number}", "--agent", f"a{number}")
    run("job", "add", "builder", "--order", "o1", "--goal", "tidy the docs")
    run("job", "add", "builder", "--order", "o2", "--goal", "fix the card")
    run("job", "add", "builder", "--order", "o3", "--goal", "fix the watch on a Sprite", "--remote")
    order = "Order (his words): fix the watch\nServes: his order\nGoal: fix it\nDrive: demo, "
    refused = agent_brief.deny_reason(started("long-running:builder", f"{order}order o3, job j1"))
    counted = "; ".join(f"j{number + 1} on o1, its agent a{number} last wrote at {when(state.stamp(wrote), seconds=True)}" for number in range(1, 9))
    assert refused.startswith(
        f"refused: 8 builders already run on this Mac, its cap of 8 ({counted}), so j1 waits its turn for a builder slot on this Mac, 1 of 3 in line."
        " The watch's FREE line names each job waiting its turn as a slot opens, earliest due first: j1 (due "
    )
    assert refused.index('"fix the watch"; j11 (due ') < refused.index('"fix the card"; j10 (no time promised) "tidy the docs".')
    assert "j12" not in refused
    assert refused.endswith("A Forge-AI/monorepo builder need not wait: start it on a Sprite with drive job launch <j> <brief>")
    held = state.load(demo)["jobs"]["j1"]
    assert (held["state"], held["waits"], held["said"]) == ("waiting", [state.SLOT_WAIT], "waits its turn for a builder slot on this Mac")
    run("job", "set", "j2", "--state", "stopped")
    for job_id, place in (("j1", "1 of 3"), ("j11", "2 of 3"), ("j10", "3 of 3")):
        assert agent_brief.deny_reason(started("long-running:builder", f"{order}order o3, job {job_id}")).startswith(
            f"refused: {job_id} waits its turn for a builder slot on this Mac, {place} in line. The watch's FREE line names"
        )
    assert agent_brief.deny_reason(started("long-running:builder", "Order (his words): fix the watch\nServes: his order\nGoal: fix it")).startswith(
        "refused: builders already wait their turn for a slot on this Mac ahead of it, earliest due first (j1, j11, j10), so this one waits:"
        " start it once a slot opens here with no builder waiting its turn. The watch's FREE line"
    )
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="running", waits=[])
    assert agent_brief.deny_reason(started("long-running:builder", f"{order}order o3, job j1")) is None
    assert agent_brief.deny_reason(started("long-running:builder", f"{order}order o2, job j11")).startswith("refused: j11 waits its turn for a builder slot on this Mac, 1 of 2 in line.")
