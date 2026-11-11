import io
import os
from collections.abc import Callable

import agent_brief
import pytest
from conftest import RESEARCH

Run = Callable[..., tuple[int, str]]
HIS_BRIEF = (
    "Message (his words): why is this card asking again?\n"
    "Link: https://poetic-technology.slack.com/archives/D123/p1794380463481959\n"
    "Thread: D123 1794380463.481959\n"
    "Drive: demo\n"
    "Scratch: /scratch/h1\n"
    "Deliverable: his answer, in his thread"
)


def started(subagent_type: str, prompt: str, model: str | None = None) -> dict:
    tool_input = {"subagent_type": subagent_type, "prompt": prompt, "description": "d"} | ({"model": model} if model else {})
    return {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "tool_name": "Agent", "tool_input": tool_input}


def test_a_handlers_prompt_is_its_brief_alone(communicator: str) -> None:
    retyped = agent_brief.deny_reason(started("long-running:handler", f"{HIS_BRIEF}\nDo not answer the card; he picked nothing."))
    assert retyped.startswith("refused: a handler's prompt is its brief alone")
    assert "`Do not answer the card; he picked nothing.` is more" in retyped
    assert retyped.endswith("drop the line")
    assert agent_brief.deny_reason(started("long-running:handler", "Record: 661229 Drive: durable-agents Scratch: /scratch/h1")) is None
    context = "\nContext: he replies to the card drawn at 11:21 PM\nits question: the bucket copy\nits options: skip or wait"
    his_lines = HIS_BRIEF.replace("again?", "again?\nand again?")
    assert agent_brief.deny_reason(started("long-running:handler", f"{his_lines}{context}")) is None
    assert "`and the drive's own rules` is more" in agent_brief.deny_reason(
        started("long-running:handler", f"{HIS_BRIEF}{context}\nand the drive's own rules")
    )


def test_a_handler_on_sonnet_takes_only_a_ready_record_or_a_post_report(communicator: str, records: dict[str, str]) -> None:
    assert agent_brief.deny_reason(started("long-running:handler", HIS_BRIEF, "sonnet")) == (
        "refused: a handler runs on Opus for his words, an ask, a card or any judgment, and Sonnet or Haiku only for a `ready`"
        " record or a `POST` report briefed as its one `Record:` line; pass no model"
    )
    assert agent_brief.deny_reason(started("long-running:handler", HIS_BRIEF)) is None
    records["661300"] = "@Nov 10 11:34 PM\n661300 ready root>communicator [job=j1 order=o1] + j1 READY https://github.com/yasyf/cc-skills/pull/1"
    records["661301"] = "@Nov 10 11:35 PM\n661301 report root>communicator [thread=1.2] + POST j1 is installed"
    records["661302"] = "@Nov 10 11:36 PM\n661302 report root>communicator [thread=1.2] + Step 3 of 3: his pick is live"
    for seq in ("661300", "661301"):
        assert agent_brief.deny_reason(started("long-running:handler", f"Record: {seq} Drive: demo Scratch: /scratch/h1", "sonnet")) is None
    assert agent_brief.deny_reason(started("long-running:handler", "Record: 661302 Drive: demo Scratch: /scratch/h1", "haiku")).endswith(
        "pass no model"
    )


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
    assert refused.endswith('--runs-as "<service account, how it starts>", or --runs-as "none: <why>" when the job runs no pass')
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
