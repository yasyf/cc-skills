import io
import os
from collections.abc import Callable
from datetime import timedelta

import agent_brief
import pytest
from conftest import RESEARCH
from drive import agents, place, state, watch
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


def test_a_ninth_builder_here_starts_on_a_sprite_at_once_and_only_one_whose_brief_runs_on_this_mac_waits_its_turn_while_old_held_work_neither_counts_nor_queues(
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
    with state.edit(demo) as data:
        asked = state.new_question(None, "Which store?", state.stamp(), card="https://acme.slack.com/archives/D123/p1794200000000100")
        data["jobs"]["j13"] = state.new_job("o1", "builder", "cut over agent_hosts", [], state.stamp()) | {"state": "waiting", "questions": [asked]}
        data["orders"]["o1"]["jobs"].append("j13")
    sprite = "Order (his words): fix the watch\nServes: his order\nGoal: fix it\nDrive: demo, "
    mac = "Order (his words): fix the watch\nServes: his order\nGoal: fix it and replay it on a test Mac\nDrive: demo, "
    counted = "; ".join(f"j{number + 1} on o1, its agent a{number} last wrote at {when(state.stamp(wrote), seconds=True)}" for number in range(1, 9))
    assert agent_brief.deny_reason(started("long-running:builder", f"{sprite}order o3, job j1")) == (
        f"refused: 8 builders already run on this Mac, its cap of 8 ({counted}), so j1 starts on a Sprite at once, since its brief names no"
        f" step only this Mac runs, {place.MAC_ONLY_STEPS}: write this brief to a file and run drive job launch j1 <file>"
    )
    assert state.load(demo)["jobs"]["j1"]["waits"] == []
    for order_id, job_id in (("o1", "j10"), ("o2", "j11"), ("o3", "j1")):
        refused = agent_brief.deny_reason(started("long-running:builder", f"{mac}order {order_id}, job {job_id}"))
    assert refused.startswith(
        f"refused: 8 builders already run on this Mac, its cap of 8 ({counted}), so j1 waits its turn for a builder slot on this Mac, 1 of 3 in line."
        " The watch's FREE line names each job waiting its turn as a slot opens, earliest due first: j1 (due "
    )
    assert refused.index('"fix the watch"; j11 (due ') < refused.index('"fix the card"; j10 (no time promised) "tidy the docs".')
    assert "j12" not in refused
    assert "j13" not in refused
    assert refused.endswith(
        f"Only a builder whose brief names a step only this Mac runs, {place.MAC_ONLY_STEPS}, waits here;"
        " start any other on a Sprite at once with drive job launch <j> <brief>"
    )
    held = state.load(demo)["jobs"]["j1"]
    assert (held["state"], held["waits"], held["said"], held["where"]) == ("waiting", [state.SLOT_WAIT], "waits its turn for a builder slot on this Mac", "local")
    run("job", "set", "j2", "--state", "stopped")
    for job_id, spot in (("j1", "1 of 3"), ("j11", "2 of 3"), ("j10", "3 of 3")):
        assert agent_brief.deny_reason(started("long-running:builder", f"{mac}order o3, job {job_id}")).startswith(
            f"refused: {job_id} waits its turn for a builder slot on this Mac, {spot} in line. The watch's FREE line names"
        )
    assert agent_brief.deny_reason(started("long-running:builder", f"{sprite}order o1, job j10")).startswith(
        "refused: j10 waits its turn for a builder slot on this Mac, 3 of 3 in line, so j10 starts on a Sprite at once,"
    )
    assert agent_brief.deny_reason(started("long-running:builder", "Order (his words): fix the watch\nServes: his order\nGoal: fix it")).startswith(
        "refused: builders already wait their turn for a slot on this Mac ahead of it, earliest due first (j1, j11, j10), so this one waits:"
        " start it once a slot opens here with no builder waiting its turn. The watch's FREE line"
    )
    assert agent_brief.deny_reason(started("long-running:builder", f"{mac}order o1, job j13")).startswith(
        "refused: builders already wait their turn for a slot on this Mac ahead of it, earliest due first (j1, j11, j10), so this one waits:"
    )
    assert state.load(demo)["jobs"]["j13"]["waits"] == []
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(waits=[], slot=state.stamp())
    assert agent_brief.deny_reason(started("long-running:builder", f"{sprite}order o3, job j1")) is None
    assert agent_brief.deny_reason(started("long-running:builder", f"{mac}order o2, job j11")).startswith("refused: j11 waits its turn for a builder slot on this Mac, 1 of 2 in line.")


def test_an_open_incidents_fix_builder_and_an_immediate_fix_start_here_at_once_while_eight_hold_the_slots_and_others_start_on_a_sprite(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watch, "last_write", lambda job, data: (state.now(), None))
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "tidy the docs")
    for number in range(1, 9):
        run("job", "add", "builder", "--order", "o1", "--goal", f"build part {number}", "--agent", f"a{number}")
    run("job", "add", "builder", "--order", "o1", "--goal", "tidy the docs")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix this asap", "--incident", "--parts", "fix", "first check")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the card is broken", "--parts", "the card", "immediate fix")
    order = "Order (his words): fix it\nServes: his order\nGoal: fix it\nDrive: demo, "
    for order_id, part in (("o2", "fix"), ("o3", "immediate fix")):
        job_id = run("job", "add", "builder", "--order", order_id, "--part", part, "--goal", "fix it")[1].splitlines()[0]
        assert agent_brief.deny_reason(started("long-running:builder", f"{order}order {order_id}, job {job_id}")) is None
    assert "so j9 starts on a Sprite at once" in agent_brief.deny_reason(started("long-running:builder", f"{order}order o1, job j9"))


def test_a_monorepo_builder_over_this_macs_load_limit_starts_here_only_when_its_brief_names_a_step_only_this_mac_runs(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(place, "busy", lambda: 0.63)
    monkeypatch.setattr(place, "limit", lambda: 0.5)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "prove the fixes")
    assert run("job", "add", "builder", "--order", "o1", "--goal", "prove", "--repo", "Forge-AI/monorepo") == (0, "j1")
    run("job", "add", "builder", "--order", "o1", "--goal", "tidy", "--repo", "yasyf/cc-skills")

    def start(job_id: str, lines: str) -> str | None:
        return agent_brief.deny_reason(started("long-running:builder", f"Order (his words): prove the fixes\nServes: his order\n{lines}\nDrive: demo, order o1, job {job_id}"))

    assert start("j1", "Goal: fix the run page") == (
        "refused: this Mac is 63% busy, at or over its 50% limit, so j1 starts on a Sprite: drive job launch j1 <brief>. A brief naming a step"
        f" only this Mac runs, {place.MAC_ONLY_STEPS}, starts here at any load"
    )
    for lines in (
        "Goal: land the desktop fix\nHandoff: merged\nPROVE: tools/ci mac-box up; tools/ci mac-box prove --commit f3163cd03c48; tools/ci mac-box record",
        'Goal: prove the session rows\nHandoff: merged\nPROVE: tools/ci db-read "select count(*) from sessions where kind = \'personal\'"',
        "Goal: read the 500s in Datadog APM traces and fix the endpoint",
        "Goal: release the fix\nINSTALL: tools/ci release start --order 680721",
        "Goal: free the stuck rows\nRepair: drive break-glass j1 --stack sandsql/plat-usw2-prod --reason stuck -- aws s3 ls",
    ):
        assert start("j1", lines) is None
    shared_alert = (
        "Order (his words): I thoguth we fixed OOMs? Shared from Datadog in <#C0822AHFY3G>: Executor OOM Run Crash Count (16Gi)\n"
        "Serves: his order\n"
        "Goal: a retry of a run forks the newest attempt in its chain whose journal is in the team's bucket: change 1 of design j2810\n"
        "Where: Forge-AI/monorepo, one new branch off dev; go/sand/next/internal/vm_run/fork.go\n"
        "Plan: Design j2810 (drive job report j2810, read it whole first)\n"
        "Incident: Every release a drive job starts starts the regular way, `tools/ci release start <target> --from <sha>`\n"
        "Transcripts:\n- j2333 builder: read the 500s in Datadog APM, then tools/ci mac-box prove\n"
        "Drive: demo, order o1, job j1"
    )
    assert agent_brief.deny_reason(started("long-running:builder", shared_alert)).startswith(
        "refused: this Mac is 63% busy, at or over its 50% limit, so j1 starts on a Sprite: drive job launch j1 <brief>."
    )
    assert start("j2", "Goal: fix the run page") is None
    monkeypatch.setattr(place, "busy", lambda: 0.4)
    assert start("j1", "Goal: fix the run page") is None
