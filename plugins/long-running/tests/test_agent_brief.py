import io
import json
import os
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import agent_brief
import pytest
from conftest import REAL_MACHINE, RESEARCH, kept_brief
from drive import agents, brief, cli, orca, place, state, view, watch
from drive.render import when

Run = Callable[..., tuple[int, str]]


def started(subagent_type: str, prompt: str, model: str | None = None) -> dict:
    tool_input = {"subagent_type": subagent_type, "prompt": prompt, "description": "d"} | ({"model": model} if model else {})
    return {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "tool_name": "Agent", "tool_input": tool_input}


def briefed(subagent_type: str, text: str, model: str | None = None) -> dict:
    return started(subagent_type, kept_brief(text), model)


def test_a_design_job_starts_as_the_designer(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Restores", "restores copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    monkeypatch.setattr("sys.stdin", io.StringIO(RESEARCH))
    run("job", "report", "j1", "-")
    run("job", "add", "design", "--order", "o1", "--goal", "design the copy")
    prompt = "Researched: j1\nDrive: demo, order o1, job j3"
    for call in (started("codex:codex-wrapper", prompt), started("long-running:builder", prompt, "fable")):
        assert agents.shaped(call) == call["tool_input"] | {"subagent_type": "long-running:designer"}
    assert agent_brief.deny_reason(briefed("long-running:designer", prompt, "fable")) is None


def test_a_builders_serves_line_names_one_of_the_three(demo: str) -> None:
    order = "Order (his words): fix the watch\n"
    refused = agent_brief.deny_reason(briefed("long-running:builder", f"{order}Serves: cleanup I noticed\nGoal: tidy the watch"))
    assert refused.startswith("refused: `Serves: cleanup I noticed` names none of `port of #N`, `drive tooling` or `his order`")
    for serves in ("his order o284", "port of #4203", '"fix the watch"'):
        assert agent_brief.deny_reason(briefed("long-running:builder", f"{order}Serves: {serves}\nGoal: fix it")) is None


def test_a_builders_brief_file_is_read_from_whichever_line_of_its_prompt_names_it_in_the_drives_jobs_folder(demo: str, tmp_path: Path) -> None:
    kept = state.drive_dir(demo) / "jobs" / "j3.brief.md"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("Order (his words): fix the watch\nServes: his order\nGoal: fix it\nDrive: demo, order o1, job j3\n")
    for prompt in (
        f"Read your brief first: {kept}",
        f"Read your brief first: {kept}; his words from o1, rulings: 0, earlier jobs: 0",
        f"Brief: {kept} (read it whole; it is your brief)\nDrive: demo, order o1, job j3",
        f"Read the whole brief at `{kept}`, then follow it.",
    ):
        assert agent_brief.deny_reason(started("long-running:builder", prompt)) is None
    elsewhere = tmp_path / "scratch" / "coordinator" / "j3.brief.md"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text(kept.read_text())
    assert agent_brief.deny_reason(started("long-running:builder", f"Brief: {elsewhere}")) == (
        f"refused: a long-running:builder starts only on a brief the drive wrote; {elsewhere} is not in the drive's jobs folder:"
        " `drive job add` it, then `drive job brief <j>`, and start it on the line that prints"
    )
    kept.write_text("Order (his words): fix the watch\nGoal: fix it\nDrive: demo, order o1, job j3\n")
    assert agent_brief.deny_reason(started("long-running:builder", f"Brief: {kept}")).startswith(
        "refused: a builder's brief, in its prompt or the brief file the prompt names, carries a `Serves:` line"
    )


def test_a_builder_reads_no_brief_of_another_job_its_prompt_names_beside_its_own_drive_line(demo: str) -> None:
    other = state.drive_dir(demo) / "jobs" / "j2.brief.md"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("Order (his words): fix the watch\nServes: his order\nDrive: demo, order o1, job j2\n")
    prompt = f"Order (his words): fix the watch\nGoal: find why the builder on {other} stopped\nDrive: demo, order o1, job j3"
    assert brief.carried(prompt) == prompt
    assert agent_brief.deny_reason(started("long-running:builder", prompt)).startswith(
        "refused: a long-running:builder starts only on a brief the drive wrote: `drive job add` it"
    )


def test_a_drive_agent_starts_only_on_a_brief_the_drive_wrote_never_on_a_typed_prompt_or_a_brief_path_nothing_wrote(demo: str, run: Run) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Restores", "restores copy one store", "--kind", "build", "--from", "cci:702923")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    never = state.drive_dir(demo) / "jobs" / "j1.brief.md"
    refused = agent_brief.deny_reason(started("long-running:reader", f"Read your brief first, whole: {never} (if that path is missing, run `drive status j1`)"))
    assert refused == (
        f"refused: a long-running:reader starts only on a brief the drive wrote; {never} does not exist:"
        " `drive job brief j1`, then start it on the line that prints"
    )
    assert agents.ruled(refused) == {"judged": False}
    typed = "Order: Drive tooling: restores copy one store\nResearch: the Researched: block this build needs\nDrive: demo, order o1, job j1\nScratch: /s"
    for kind in agent_brief.BRIEFED:
        assert agent_brief.deny_reason(started(kind, typed)).startswith(f"refused: a {kind} starts only on a brief the drive wrote: `drive job brief j1`")
    assert agent_brief.deny_reason(started("Explore", typed)) is None
    line = run("job", "brief", "j1")[1].splitlines()[0]
    assert line == f"Read your brief first: {never}"
    assert agent_brief.deny_reason(started("long-running:reader", line)) is None
    assert agent_brief.deny_reason(started("long-running:reader", typed)) == (
        f"refused: a long-running:reader starts only on a brief the drive wrote: start it on `{line}`"
    )
    assert never.read_text().startswith("Order (his words): restores copy one store\nFrom: cci:702923\n")


def test_a_builder_passing_over_a_teams_data_names_what_it_runs_as(demo: str) -> None:
    order = "Order (his words): plat's runs are missing\nServes: his order\n"
    brief = f"{order}Goal: backfill run rows for team plat"
    refused = agent_brief.deny_reason(briefed("long-running:builder", brief))
    assert refused.startswith("refused: the Goal reads as a pass over a team's data (`backfill`) with no `Runs as:` line")
    assert refused.endswith('--runs-as "<service account, how it starts>"`, or `--runs-as "none: <why>"` when it writes no team\'s data')
    for runs_as in ("svc-plat-backfill in the plat cluster, a cronjob", "none: it edits PR bodies"):
        assert agent_brief.deny_reason(briefed("long-running:builder", f"{brief}\nRuns as: {runs_as}")) is None
    for goal, word in (
        ("repair the two Cargill databases whose bucket prefixes are empty", "repair"),
        ("queued: migrate old-runner runs as each cluster's service account", "queued: migrate"),
        ("run the merged cleanup delete_old_generations on plat", "run the merged cleanup"),
        ("plat's run rows are backfilled", "are backfilled"),
    ):
        assert f"(`{word}`)" in agent_brief.deny_reason(briefed("long-running:builder", f"{order}Goal: {goal}"))
    for goal in (
        "write a migration adding a column",
        "the Show me of three PRs is redrawn as one compare each so the backfill stack's tap card draws",
        "rerun the backfill stack's red check",
        "api: ✨ a service account's API key runs admin backfills through its own permission",
        "migrate tests to pytest",
    ):
        assert agent_brief.deny_reason(briefed("long-running:builder", f"{order}Goal: {goal}")) is None


def test_a_ninth_builder_here_starts_on_a_sprite_at_once_and_only_one_whose_brief_runs_on_this_mac_waits_its_turn_while_old_held_work_neither_counts_nor_queues(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrote = state.now() - timedelta(minutes=3)
    monkeypatch.setattr(watch, "last_write", lambda job, data: (wrote, None))
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "tidy the docs", "--parts", *(f"part {number}" for number in range(1, 9)), "docs")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix the card", "--due", "+2h", "--thread", "1.2")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix the watch", "--due", "+1h", "--thread", "1.3", "--parts", "here", "on a Sprite")
    run("job", "add", "builder", "--order", "o3", "--part", "here", "--goal", "fix the watch")
    for number in range(1, 9):
        run("job", "add", "builder", "--order", "o1", "--part", f"part {number}", "--goal", f"build part {number}", "--agent", f"a{number}")
    run("job", "add", "builder", "--order", "o1", "--part", "docs", "--goal", "tidy the docs")
    run("job", "add", "builder", "--order", "o2", "--goal", "fix the card")
    run("job", "add", "builder", "--order", "o3", "--part", "on a Sprite", "--goal", "fix the watch on a Sprite", "--remote")
    with state.edit(demo) as data:
        asked = state.new_question(None, "Which store?", state.stamp(), card="https://acme.slack.com/archives/D123/p1794200000000100")
        data["jobs"]["j13"] = state.new_job("o1", "builder", "cut over agent_hosts", [], state.stamp()) | {"state": "waiting", "questions": [asked]}
        data["orders"]["o1"]["jobs"].append("j13")
    sprite = "Order (his words): fix the watch\nServes: his order\nGoal: fix it\nDrive: demo, "
    mac = "Order (his words): fix the watch\nServes: his order\nGoal: fix it\nMac: replay it on a test Mac\nDrive: demo, "
    counted = "; ".join(f"j{number + 1} on o1, its agent a{number} last wrote at {when(state.stamp(wrote), seconds=True)}" for number in range(1, 9))
    assert agent_brief.deny_reason(briefed("long-running:builder", f"{sprite}order o3, job j1")) == (
        f"refused: 8 builders already run on this Mac, its cap of 8 on builders that compile here, where a staging tester, a merged PR's prover or another builder marked --builds-nothing holds no slot ({counted}), so j1 starts on a Sprite at once, since its brief declares no"
        f" step only this Mac runs on {place.MAC_LINES}: write this brief to a file and run drive job launch j1 <file>"
    )
    assert agents.ruled(agent_brief.deny_reason(briefed("long-running:builder", f"{sprite}order o3, job j1"))) == {"judged": False}
    assert state.load(demo)["jobs"]["j1"]["waits"] == []
    for order_id, job_id in (("o1", "j10"), ("o2", "j11"), ("o3", "j1")):
        refused = agent_brief.deny_reason(briefed("long-running:builder", f"{mac}order {order_id}, job {job_id}"))
    assert refused.startswith(
        f"refused: 8 builders already run on this Mac, its cap of 8 on builders that compile here, where a staging tester, a merged PR's prover or another builder marked --builds-nothing holds no slot ({counted}), so j1 waits its turn for a builder slot on this Mac, 1 of 3 in line."
        " The watch's FREE line names each job waiting its turn as a slot opens, earliest due first: j1 (due "
    )
    assert refused.index('"fix the watch"; j11 (due ') < refused.index('"fix the card"; j10 (no time promised) "tidy the docs".')
    assert "j12" not in refused
    assert "j13" not in refused
    assert refused.endswith(
        f"Only a builder whose brief declares a step only this Mac runs, on {place.MAC_LINES}, waits here;"
        " start any other on a Sprite at once with drive job launch <j> <brief>"
    )
    assert agents.ruled(refused) == {"judged": False}
    held = state.load(demo)["jobs"]["j1"]
    assert (held["state"], held["waits"], held["said"], held["where"]) == ("waiting", [state.SLOT_WAIT], "waits its turn for a builder slot on this Mac", "local")
    run("job", "set", "j2", "--state", "stopped")
    for job_id, spot in (("j1", "1 of 3"), ("j11", "2 of 3"), ("j10", "3 of 3")):
        assert agent_brief.deny_reason(briefed("long-running:builder", f"{mac}order o3, job {job_id}")).startswith(
            f"refused: {job_id} waits its turn for a builder slot on this Mac, {spot} in line. The watch's FREE line names"
        )
    assert agent_brief.deny_reason(briefed("long-running:builder", f"{sprite}order o1, job j10")).startswith(
        "refused: j10 waits its turn for a builder slot on this Mac, 3 of 3 in line, so j10 starts on a Sprite at once,"
    )
    assert agent_brief.deny_reason(briefed("long-running:builder", "Order (his words): fix the watch\nServes: his order\nGoal: fix it")).startswith(
        "refused: builders already wait their turn for a slot on this Mac ahead of it, earliest due first (j1, j11, j10), so this one waits:"
        " start it once a slot opens here with no builder waiting its turn. The watch's FREE line"
    )
    assert agent_brief.deny_reason(briefed("long-running:builder", f"{mac}order o1, job j13")).startswith(
        "refused: builders already wait their turn for a slot on this Mac ahead of it, earliest due first (j1, j11, j10), so this one waits:"
    )
    assert state.load(demo)["jobs"]["j13"]["waits"] == []
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(waits=[], slot=state.stamp())
    assert agent_brief.deny_reason(briefed("long-running:builder", f"{sprite}order o3, job j1")) is None
    assert agent_brief.deny_reason(briefed("long-running:builder", f"{mac}order o2, job j11")).startswith("refused: j11 waits its turn for a builder slot on this Mac, 1 of 2 in line.")


def test_an_open_incidents_fix_builder_and_an_immediate_fix_start_here_at_once_while_eight_hold_the_slots_and_others_start_on_a_sprite(demo: str, run: Run) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "tidy the docs", "--parts", *(f"part {number}" for number in range(1, 9)), "docs")
    for number in range(1, 9):
        run("job", "add", "builder", "--order", "o1", "--part", f"part {number}", "--goal", f"build part {number}", "--agent", f"a{number}")
    run("job", "add", "builder", "--order", "o1", "--part", "docs", "--goal", "tidy the docs")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix this asap", "--incident", "--parts", "fix", "first check")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the card is broken", "--parts", "the card", "immediate fix")
    order = "Order (his words): fix it\nServes: his order\nGoal: fix it\nDrive: demo, "
    for order_id, part in (("o2", "fix"), ("o3", "immediate fix")):
        job_id = run("job", "add", "builder", "--order", order_id, "--part", part, "--goal", "fix it")[1].splitlines()[0]
        assert agent_brief.deny_reason(briefed("long-running:builder", f"{order}order {order_id}, job {job_id}")) is None
    assert "so j9 starts on a Sprite at once" in agent_brief.deny_reason(briefed("long-running:builder", f"{order}order o1, job j9"))


def test_a_priority_orders_builder_starts_here_at_once_past_a_full_cap_the_line_and_the_load_gate_and_one_already_in_line_takes_its_turn_first(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "tidy the docs", "--parts", *(f"part {number}" for number in range(1, 9)), "docs")
    for number in range(1, 9):
        run("job", "add", "builder", "--order", "o1", "--part", f"part {number}", "--goal", f"build part {number}", "--agent", f"a{number}")
    run("job", "add", "builder", "--order", "o1", "--part", "docs", "--goal", "tidy the docs")
    run("order", "add", "--title", "Short title", "--project", "Releases", "--workstream", "Hotfix", "ship his fix alone first", "--parts", "the hotfix", "the cut")
    run("job", "add", "builder", "--order", "o2", "--part", "the hotfix", "--goal", "build the hotfix")
    with state.edit(demo) as data:
        for job_id in ("j9", "j10"):
            place.queue(data["jobs"][job_id], state.now())
    data = state.load(demo)
    assert (place.waiting_builders(data), place.turns(data, state.now())) == (["j9", "j10"], ([], 8))
    assert run("order", "set", "o2", "--priority")[0] == 0
    data = state.load(demo)
    assert (data["orders"]["o2"]["priority"], data["orders"]["o1"]["priority"]) == ("Priority o2", None)
    assert (place.waiting_builders(data), place.turns(data, state.now()), place.crowded(data, "j10", state.now())) == (["j10", "j9"], (["j10"], 8), None)
    assert run("job", "add", "builder", "--order", "o2", "--part", "the cut", "--goal", "cut it", "--repo", "Forge-AI/monorepo") == (0, "j11")
    assert state.load(demo)["jobs"]["j11"]["where"] == "local"
    monkeypatch.setattr(place, "busy", lambda: 0.95)
    monkeypatch.setattr(place, "limit", lambda: 0.5)
    assert agent_brief.deny_reason(briefed("long-running:builder", "Order (his words): ship his fix alone first\nServes: his order\nGoal: cut it\nDrive: demo, order o2, job j11")) is None
    assert run("order", "set", "o2", "--no-priority")[0] == 0
    assert place.crowded(state.load(demo), "j10", state.now()).startswith("9 builders already run on this Mac")


def test_a_builder_whose_wait_still_holds_starts_here_only_on_its_free_line_by_the_refusal_drive_job_launch_gives(demo: str, run: Run) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy one store", "--parts", "the interface", "the reads")
    run("job", "add", "builder", "--order", "o1", "--part", "the interface", "--goal", "build the interface")
    run("job", "add", "builder", "--order", "o1", "--part", "the reads", "--goal", "build the reads", "--waits", "j1")
    brief = "Order (his words): copy one store\nServes: his order\nGoal: build it\nDrive: demo, order o1, job "
    refused = "j2 waits on j1: start nothing for it now: once the last wait ends the watch starts it, or its FREE line names who does"
    assert view.start_refusal(state.load(demo), "j2", True) == refused
    assert agent_brief.deny_reason(briefed("long-running:builder", f"{brief}j2")) == f"refused: {refused}"
    assert agent_brief.deny_reason(briefed("long-running:builder", f"{brief}j1")) is None
    with state.edit(demo) as data:
        data["jobs"]["j1"]["state"] = "done"
    assert agent_brief.deny_reason(briefed("long-running:builder", f"{brief}j2")) is None


def test_a_builder_added_to_build_nothing_such_as_an_agentinbox_live_proof_starts_here_at_once_past_a_full_cap_and_its_line_and_holds_no_slot(demo: str, run: Run) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "tidy the docs", "--parts", *(f"part {number}" for number in range(1, 9)), "docs")
    for number in range(1, 9):
        run("job", "add", "builder", "--order", "o1", "--part", f"part {number}", "--goal", f"build part {number}", "--agent", f"a{number}")
    run("job", "add", "builder", "--order", "o1", "--part", "docs", "--goal", "tidy the docs")
    with state.edit(demo) as data:
        place.queue(data["jobs"]["j9"], state.now())
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix the AgentInbox", "--due", "+1h", "--thread", "1.3")
    assert run("job", "add", "builder", "--order", "o2", "--goal", "prove the AgentInbox fix live: a Datadog APM read", "--builds-nothing") == (0, "j10")
    proof = state.load(demo)["jobs"]["j10"]
    assert (proof["state"], proof["waits"], proof["where"], proof["builds_nothing"]) == ("waiting", [], "local", True)
    brief = "Order (his words): fix the AgentInbox\nServes: his order\nGoal: prove it live with a Datadog APM read\nDrive: demo, order o2, job j10"
    assert agent_brief.deny_reason(briefed("long-running:builder", brief)) is None
    run("job", "set", "j10", "--agent", "a10")
    data = state.load(demo)
    assert list(place.local_builders(data, state.now())) == [f"j{number}" for number in range(1, 9)]
    assert place.turns(data, state.now()) == ([], place.LOCAL_BUILDERS)
    assert place.waiting_builders(data) == ["j9"]
    assert run("job", "add", "reader", "--order", "o2", "--goal", "read the APM traces", "--builds-nothing")[0] == 2
    run("job", "set", "j9", "--builds-nothing")
    assert (state.load(demo)["jobs"]["j9"]["waits"], place.waiting_builders(state.load(demo))) == ([], [])
    dry_run = "Order (his words): tidy the docs\nServes: his order\nGoal: dry-run it\nMac: tools/ci cluster-job reads this Mac's Buildkite token\nDrive: demo, order o1, job j9"
    assert agent_brief.deny_reason(briefed("long-running:builder", dry_run)) is None
    run("job", "add", "reader", "--order", "o2", "--goal", "read the APM traces")
    assert run("job", "set", "j11", "--builds-nothing")[0] == 2


def test_a_builder_whose_goal_proves_merged_prs_live_builds_nothing_unmarked_so_it_starts_here_past_a_full_cap_and_holds_no_slot(demo: str, run: Run) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "tidy the docs", "--parts", *(f"part {number}" for number in range(1, 9)), "docs")
    for number in range(1, 9):
        run("job", "add", "builder", "--order", "o1", "--part", f"part {number}", "--goal", f"build part {number}", "--agent", f"a{number}")
    code, out = run("job", "add", "builder", "--order", "o1", "--part", "docs", "--goal", "prove Forge-AI/monorepo#42 live")
    proof = state.load(demo)["jobs"]["j9"]
    assert (code, out.splitlines()[0], proof["state"], proof["waits"], proof["where"], bool(proof["builds_nothing"]), state.builds_nothing(proof)) == (0, "j9", "waiting", [], "local", False, True)
    run("job", "set", "j9", "--agent", "a9")
    assert list(place.local_builders(state.load(demo), state.now())) == [f"j{number}" for number in range(1, 9)]


def test_a_builder_on_staging_test_parts_alone_builds_nothing_unmarked_so_it_starts_here_past_a_full_cap_and_holds_no_slot_as_j6160_needed(demo: str, run: Run) -> None:
    staging = "staging test of #42 before it lands"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "tidy the docs", "--parts", *(f"part {number}" for number in range(1, 9)), staging, "docs")
    for number in range(1, 9):
        run("job", "add", "builder", "--order", "o1", "--part", f"part {number}", "--goal", f"build part {number}", "--agent", f"a{number}")
    code, out = run("job", "add", "builder", "--order", "o1", "--part", staging, "--goal", "soak #42 on staging")
    tester = state.load(demo)["jobs"]["j9"]
    assert (code, out.splitlines()[0], tester["where"], bool(tester["builds_nothing"]), state.builds_nothing(tester)) == (0, "j9", "local", False, True)
    run("job", "set", "j9", "--agent", "a9")
    assert list(place.local_builders(state.load(demo), state.now())) == [f"j{number}" for number in range(1, 9)]
    assert not state.builds_nothing(tester | {"parts": [staging, "docs"]})


def test_a_monorepo_builder_over_this_macs_load_limit_starts_here_only_when_its_brief_names_a_step_only_this_mac_runs(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(place, "busy", lambda: 0.63)
    monkeypatch.setattr(place, "limit", lambda: 0.5)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "prove the fixes", "--parts", "prove", "tidy")
    assert run("job", "add", "builder", "--order", "o1", "--part", "prove", "--goal", "prove", "--repo", "Forge-AI/monorepo") == (0, "j1")
    run("job", "add", "builder", "--order", "o1", "--part", "tidy", "--goal", "tidy", "--repo", "yasyf/cc-skills")

    def start(job_id: str, lines: str) -> str | None:
        return agent_brief.deny_reason(briefed("long-running:builder", f"Order (his words): prove the fixes\nServes: his order\n{lines}\nDrive: demo, order o1, job {job_id}"))

    assert start("j1", "Goal: fix the run page") == (
        "refused: this Mac is 63% busy, at or over its 50% limit, so j1 starts on a Sprite: drive job launch j1 <brief>. A brief declaring a step"
        f" only this Mac runs, on {place.MAC_LINES}, starts here at any load"
    )
    assert agents.ruled(start("j1", "Goal: fix the run page")) == {"judged": False}
    for lines in (
        "Goal: land the desktop fix\nHandoff: merged\nMac: tools/ci mac-box up; tools/ci mac-box prove --commit f3163cd03c48; tools/ci mac-box record",
        'Goal: prove the session rows\nHandoff: merged\nMac: tools/ci db-read "select count(*) from sessions where kind = \'personal\'"',
        "Goal: fix the endpoint\nMac: read the 500s in Datadog APM traces",
        "Goal: release the fix\nRelease: tools/ci release start api --env prod --order 680721",
        "Goal: free the stuck rows\nMac: drive break-glass j1 --stack sandsql/plat-usw2-prod --reason stuck -- aws s3 ls",
    ):
        assert start("j1", lines) is None
    for lines in (
        "Goal: land the desktop fix\nHandoff: merged\nPROVE: tools/ci mac-box prove --commit f3163cd03c48",
        "Goal: read the 500s in Datadog APM traces and fix the endpoint",
        "Goal: fix the release start hook\nPlan: his words, typed on the drive's dashboard under the card",
    ):
        assert start("j1", lines).startswith("refused: this Mac is 63% busy, at or over its 50% limit, so j1 starts on a Sprite")
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
    assert agent_brief.deny_reason(briefed("long-running:builder", shared_alert)).startswith(
        "refused: this Mac is 63% busy, at or over its 50% limit, so j1 starts on a Sprite: drive job launch j1 <brief>."
    )
    assert start("j2", "Goal: fix the run page") is None
    monkeypatch.setattr(place, "busy", lambda: 0.4)
    assert start("j1", "Goal: fix the run page") is None


def test_a_usage_limit_stop_is_kept_on_the_job_whose_lane_runs_in_the_sessions_worktree_and_in_the_shared_log(demo: str, run: Run, tmp_path: Path) -> None:
    run("order", "add", "--title", "Short title", "--project", "Releases", "--workstream", "Hotfix", "ship his fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the hotfix")
    lane = str(tmp_path / "lane")
    with state.edit(demo) as data:
        data["jobs"]["j1"]["worktree"] = lane
    stop = state.parse_time("2026-11-15T17:33:31-08:00")
    hit = state.usage_limit_hit("You've hit your weekly limit · resets Nov 17 at 11pm (America/Los_Angeles)", stop)
    assert hit == {"limit": "weekly", "until": "2026-11-17T23:00:00-08:00", "notice": "You've hit your weekly limit · resets Nov 17 at 11pm (America/Los_Angeles)", "at": "2026-11-15T17:33:31-08:00"}
    assert state.keep_limit_hit([demo], "s-lane", lane, hit | {"dismissed": True}) == "j1"
    assert state.keep_limit_hit([demo], "s-root", str(tmp_path), hit | {"dismissed": False}) is None
    job = state.load(demo)["jobs"]["j1"]
    assert (job["state"], job["limit_hits"]) == ("waiting", [hit | {"dismissed": True}])
    logged = [json.loads(line) for line in (state.root() / state.USAGE_LIMIT_LOG).read_text().splitlines()]
    assert [(line["session"], line["job"], line["dismissed"]) for line in logged] == [("s-lane", "j1", True), ("s-root", None, False)]


def test_a_limit_dialog_and_the_login_screen_after_it_get_escape_alone_until_a_reread_shows_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "term_lane")
    monkeypatch.setattr(orca.time, "sleep", lambda _: None)
    screens = iter(
        [
            ["working"],
            ["What do you want to do?", "3. Add funds to continue with usage credits"],
            ["Select login method:", "1. Claude account with subscription"],
            ["> "],
        ]
    )
    calls = []

    def answered(*args: str, **_: object) -> dict:
        calls.append(args)
        return {"terminal": {"tail": next(screens)}} if args[1] == "read" else {}

    monkeypatch.setattr(orca, "request", answered)
    assert orca.dismiss_limit_dialog() is True
    assert [call for call in calls if call[1] == "send"] == [("terminal", "send", "--terminal", "term_lane", "--text", "\x1b")] * 2
    assert calls[-1][1] == "read"
    monkeypatch.delenv("ORCA_TERMINAL_HANDLE")
    assert orca.dismiss_limit_dialog() is False


def test_a_usage_limit_resets_at_the_next_moment_its_words_name() -> None:
    zone = state.ZoneInfo("America/Los_Angeles")
    moment = state.parse_time("2026-12-31T22:10:00-08:00")
    assert [state.stamp(state.limit_reset(words, zone, moment)) for words in ("3:30am", "11pm", "Jan 2 at 9am", "Dec 30 at 9am")] == [
        "2027-01-01T03:30:00-08:00",
        "2026-12-31T23:00:00-08:00",
        "2027-01-02T09:00:00-08:00",
        "2027-12-30T09:00:00-08:00",
    ]
    assert state.usage_limit_hit("API Error: 500 overloaded", moment) is None


def test_a_loaded_mac_starts_no_builder_here_and_gives_no_slot_but_a_priority_orders_builder_starts_past_it(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "Releases", "--workstream", "Hotfix", "ship his fix", "--parts", "the hotfix", "the cut")
    run("job", "add", "builder", "--order", "o1", "--part", "the hotfix", "--goal", "build the hotfix")
    run("job", "add", "builder", "--order", "o1", "--part", "the cut", "--goal", "cut it")
    with state.edit(demo) as data:
        place.queue(data["jobs"]["j2"], state.now())
    loaded = "this Mac's 1-minute load is 160, over its 32 cores"
    monkeypatch.setattr(place, "strained", lambda: loaded)
    moment = state.now()
    data = state.load(demo)
    assert (place.crowded(data, "j1", moment), place.turns(data, moment)) == (loaded, ([], 0))
    refused = agent_brief.deny_reason(briefed("long-running:builder", "Order (his words): ship his fix\nServes: his order\nGoal: build the hotfix\nDrive: demo, order o1, job j1"))
    assert refused.startswith(f"refused: {loaded}, so j1 starts on a Sprite at once")
    assert run("order", "set", "o1", "--priority")[0] == 0
    data = state.load(demo)
    assert (place.crowded(data, "j1", moment), place.turns(data, moment)[0]) == (None, ["j2"])


def test_a_message_wakes_a_builder_marked_builds_nothing_whose_turn_ended_on_a_loaded_mac_at_its_local_cap_as_the_refusal_says(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "Releases", "--workstream", "Hotfix", "ship his fix", "--parts", "the hotfix", "the soak")
    run("job", "add", "builder", "--order", "o1", "--part", "the hotfix", "--goal", "build the hotfix")
    assert run("job", "add", "builder", "--order", "o1", "--part", "the soak", "--goal", "soak the hotfix with tools/ci staging-smoke", "--builds-nothing")[0] == 0
    with state.edit(demo) as data:
        for job_id in ("j1", "j2"):
            data["jobs"][job_id].update(where="local", agent=f"a-{job_id}")
    loaded = "this Mac's 1-minute load is 160, over its 32 cores"
    monkeypatch.setattr(place, "strained", lambda: loaded)
    monkeypatch.setattr(place, "local_builders", lambda data, moment: {})
    (state.root() / place.CAPS_FILE).write_text(json.dumps({"local": 0}))
    data, moment = state.load(demo), state.now()
    assert [place.woken(data, job_id, moment) for job_id in ("j1", "j2")] == [f"its turn ended and {loaded}", None]


def test_the_local_cap_counts_the_lanes_here_of_every_drive_and_the_owners_caps_file_sets_it(demo: str, run: Run) -> None:
    (state.root() / place.CAPS_FILE).write_text(json.dumps({"local": 1}))
    assert place.caps() == {"local": 1, "sprite": 30, "handlers": 8, "handler_pool": 4, "load_per_core": 1, "free_memory": 15}
    run("order", "add", "--title", "Short title", "--project", "Releases", "--workstream", "Hotfix", "ship his fix", "--parts", "the hotfix", "the cut")
    run("job", "add", "builder", "--order", "o1", "--part", "the hotfix", "--goal", "build the hotfix")
    assert run("job", "add", "reader", "--order", "o1", "--part", "the cut", "--goal", "research: the cut")[0] == 0
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(where="local", launching=state.stamp())
    place.counted_again()
    data = state.load(demo)
    assert list(place.lanes(state.now())["local"]) == [f"{demo}/j1"]
    assert place.crowded(data, "j1", state.now()) is None
    refused = agent_brief.deny_reason(briefed("long-running:reader", "Goal: research the cut\nDrive: demo, order o1, job j2"))
    assert refused.startswith(f"refused: 1 local Claude lanes work across this Mac's drives, at its cap of 1 set by {state.root() / place.CAPS_FILE} (j1 builder on o1, its agent launching since")
    assert refused.endswith("so j2 starts on a Sprite: write this brief to a file and run drive job launch j2 <file>")


def test_a_first_lane_at_the_sprite_cap_waits_its_turn_on_sprite_slot_until_the_watch_frees_it_and_a_takeover_never_waits(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "Releases", "--workstream", "Hotfix", "ship his fix", "--parts", "the hotfix")
    run("job", "add", "builder", "--order", "o1", "--part", "the hotfix", "--goal", "build the hotfix")
    full = {"local": {}, "sprite": {f"other/x{number}": f"x{number} builder on o9, its Sprite lane at work" for number in range(30)}}
    monkeypatch.setattr(place, "lanes", lambda moment: full)
    data = state.load(demo)
    assert cli.admission_refusal(demo, data, "j1", False, None, True) is None
    refused = cli.admission_refusal(demo, data, "j1", False, None, False)
    assert refused.startswith(f"30 Sprite lanes run across this Mac's drives, at its cap of 30 set by {state.root() / place.CAPS_FILE} (x0 builder on o9")
    assert refused.endswith("so j1 waits its turn for a Sprite lane, 1 of 1 in line, and the watch launches it as one frees")
    data = state.load(demo)
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["waits"]) == ("waiting", [state.SPRITE_WAIT])
    assert view.start_refusal(data, "j1", False) == "j1 waits on sprite-slot: start nothing for it now: once the last wait ends the watch starts it, or its FREE line names who does"
    assert place.sprite_turns(data, state.now()) == ([], 30)
    monkeypatch.setattr(place, "lanes", lambda moment: {"local": {}, "sprite": {}})
    assert place.sprite_turns(data, state.now()) == (["j1"], 0)


def test_a_relaunch_or_takeover_waits_its_turn_in_its_own_process_until_the_sprite_cap_has_room(demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Short title", "--project", "Releases", "--workstream", "Hotfix", "ship his fix", "--parts", "the hotfix")
    run("job", "add", "builder", "--order", "o1", "--part", "the hotfix", "--goal", "build the hotfix")
    full = {"local": {}, "sprite": {f"other/x{number}": f"x{number} builder on o9, its Sprite lane at work" for number in range(30)}}
    reads = iter([full, full, {"local": {}, "sprite": {}}])
    monkeypatch.setattr(place, "lanes", lambda moment: next(reads))
    monkeypatch.setattr(place, "TURN_SECONDS", 0)
    place.await_admission(demo, "j1", "sprite")
    assert capsys.readouterr().err.startswith("j1: waits its turn for a Sprite lane: 30 Sprite lanes run across this Mac's drives, at its cap of 30")
    assert next(reads, None) is None


def test_drive_job_start_starts_a_briefed_reader_as_its_own_session_bound_as_the_agent_of_its_orders_session_and_starts_no_second_beside_it(
    demo: str, run: Run, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Restores", "restores copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    kept = state.drive_dir(demo) / "jobs" / "j1.brief.md"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("Order (his words): restores copy one store\nGoal: research the copy\nDrive: demo, order o1, job j1\n")
    with state.edit(demo) as data:
        data["jobs"]["j1"]["brief"] = str(kept)
    launches: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(agents, "start_job", lambda name, job_id, agent, call, held, cwd: launches.append((job_id, agent, call)) or "bg-session")
    monkeypatch.setattr(orca, "checkout", lambda name, cwd: tmp_path)
    monkeypatch.setattr(place, "admission", lambda *args, **kwargs: None)
    assert run("job", "start", "j1")[0] == 0
    [(job_id, agent, call)] = launches
    assert (job_id, call["subagent_type"], call["prompt"]) == ("j1", "long-running:reader", f"Read your brief first: {kept}")
    assert state.load(demo)["jobs"]["j1"]["bg"] | {"started": None} == {"agent": agent, "session": "bg-session", "started": None, "cwd": str(tmp_path), "pointed": [], "pool": False}
    monkeypatch.setattr(agents, "gone", lambda entry: False)
    assert run("job", "start", "j1")[0] == 2
    assert len(launches) == 1
    assert agent_brief.deny_reason(started("long-running:reader", f"Read your brief first: {kept}")).startswith("refused: the watch started j1 as its own claude --bg session bg-session")
    monkeypatch.setenv(state.JOB_ENV, f"demo/j1/{agent}/{os.environ['CLAUDE_CODE_SESSION_ID']}")
    bound = agents.seen({"session_id": "bg-session", "transcript_path": "/bg-session.jsonl", "agent_type": "long-running:reader"})
    assert (bound["session_id"], bound["agent_id"], bound["agent_transcript_path"]) == (os.environ["CLAUDE_CODE_SESSION_ID"], agent, "/bg-session.jsonl")
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(agent=agent, where="local", session=os.environ["CLAUDE_CODE_SESSION_ID"], state="running")
    assert state.agent_address(state.load(demo)["jobs"]["j1"]) == "peer:bg-session"
    assert watch.carrier("j1", state.load(demo)["jobs"]["j1"], state.load(demo), None) == ["job", "resume", "j1"]
    with state.edit(demo) as data:
        data["jobs"]["j1"]["session"] = "earlier-session"
    data = state.load(demo)
    assert (watch.session_of(data["jobs"]["j1"], data)["session"], watch.agent_death(data["jobs"]["j1"], data)) == (data["roles"]["coordinator"]["session"], None)


def test_the_load_gate_reads_the_machine_caps_json_injects_and_closes_on_load_or_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(place, "machine", REAL_MACHINE)
    path = state.root() / place.CAPS_FILE
    path.write_text(json.dumps({"machine": {"load": 9, "cores": 4, "free_memory": 50}}))
    assert place.strained() == f"this Mac's 1-minute load is 9, over its 4 cores times 1, set by {path}"
    path.write_text(json.dumps({"machine": {"load": 1, "cores": 4, "free_memory": 9}}))
    assert place.strained() == f"9% of this Mac's memory is free, under 15%, set by {path}"
    path.write_text(json.dumps({"machine": {"load": 1, "cores": 4, "free_memory": 50}}))
    assert place.strained() is None


def test_handlers_hold_a_pool_of_their_own_that_a_full_local_cap_never_closes_and_the_load_gate_does(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    full = {"local": {f"other/x{number}": f"x{number} builder on o9, its agent at work" for number in range(12)}, "sprite": {}, "handlers": {}}
    monkeypatch.setattr(place, "lanes", lambda moment: full)
    data = state.load(demo)
    assert place.admission(data, None, "local", state.now()).startswith("12 local Claude lanes work across this Mac's drives, at its cap of 12")
    assert place.admission(data, None, "handlers", state.now()) is None
    full["handlers"] = {f"other/handler h{number}": f"handler h{number}, its session s{number}" for number in range(8)}
    assert place.admission(data, None, "handlers", state.now()).startswith("8 handler sessions work across this Mac's drives, at its cap of 8")
    full["handlers"] = {}
    monkeypatch.setattr(place, "strained", lambda: "this Mac's 1-minute load is 160, over its 32 cores times 1")
    assert place.admission(data, None, "handlers", state.now()) == "this Mac's 1-minute load is 160, over its 32 cores times 1"
