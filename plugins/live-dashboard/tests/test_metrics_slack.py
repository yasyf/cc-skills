from __future__ import annotations

import pytest
from conftest import FIXTURES, fake, fixture
from livedash.components import metrics, slack

THREAD = "https://example.slack.com/archives/C0TEST/p1791305688632409"


def test_file_percentiles_group_samples_and_count_failures_apart(tmp_path):
    result = metrics.latency_percentiles(fake(tmp_path), files=str(FIXTURES / "memorybench-*.json"), rows_key="samples", group="scenario", failure="failed", groups=["cached", "warm", "cold", "hybrid"], budgets={"cold": "250"}, budget_ms=150)
    dists = {dist.label: dist for dist in result.dists}
    assert [dist.label for dist in result.dists] == ["cached", "warm", "cold", "hybrid"]
    assert (dists["cold"].n, dists["cold"].failures, dists["cold"].budget) == (19, 1, 250.0)
    assert (dists["warm"].n, dists["warm"].failures, dists["warm"].budget) == (20, None, 150)
    assert dists["hybrid"].n == 0 and dists["hybrid"].p95 is None
    assert dists["cold"].p50 <= dists["cold"].p95 <= dists["cold"].p99 <= dists["cold"].max
    assert result.history.thresholds == {"budget": 150}


def test_a_negated_failure_flag_counts_rows_that_did_not_succeed(tmp_path):
    result = metrics.latency_percentiles(fake(tmp_path), files=str(FIXTURES / "spikes.jsonl"), group="mode", failure="!success")
    assert [(dist.label, dist.n, dist.failures, dist.max) for dist in result.dists] == [("low", 2, 1, 290.1), ("medium", 2, None, 254.9)]


def test_timeline_deltas_measure_the_bot_from_the_page_to_each_stage(tmp_path):
    result = metrics.latency_percentiles(fake(tmp_path), files=str(FIXTURES / "timelines" / "*.timeline.json"), deltas_from="origin", deltas_to=["thread", "triage", "incident-channel", "postmortem"])
    dists = {dist.label: dist for dist in result.dists}
    assert dists["thread"].n == 2 and dists["thread"].p50 == pytest.approx(30000) and dists["thread"].max == pytest.approx(42751)
    assert dists["incident-channel"].n == 1 and dists["incident-channel"].p95 == pytest.approx(51000)
    assert dists["postmortem"].n == 0
    assert result.note is None


def test_nothing_to_read_says_not_run(tmp_path):
    result = metrics.latency_percentiles(fake(tmp_path), files="runs/*.json", rows_key="samples", groups=["cold"])
    assert result.note == "Not run yet: nothing matches runs/*.json"
    assert [(dist.label, dist.n) for dist in result.dists] == [("cold", 0)]


def test_a_datadog_query_becomes_a_distribution_per_series(tmp_path):
    reply = {"status": "ok", "series": [{"metric": "bot.latency", "scope": "stage:triage", "pointlist": [[1791305688000, 900.0], [1791305748000, None], [1791305808000, 1300.0]]}]}
    ctx = fake(tmp_path, replies={("pup", "metrics", "query"): reply})
    result = metrics.latency_percentiles(ctx, query="p95:bot.latency{*} by {stage}")
    assert [(dist.label, dist.n, dist.max) for dist in result.dists] == [("stage:triage", 2, 1300.0)]
    assert ctx.calls[-1][-1] == "--no-agent"


def test_monitors_become_gates_and_expected_monitors_show_as_not_created(tmp_path):
    ctx = fake(tmp_path, replies={("pup", "monitors", "search"): fixture("monitors.json")})
    gates = metrics.datadog_monitors(ctx, query="tag:step:13", expect=["memory lookup failures", "memory recall drift"])
    assert [(gate.title, gate.status) for gate in gates.items] == [("memory lookup failures", "pass"), ("memory lookup p95", "fail"), ("memory recall drift", "not-created")]


def test_the_slack_feed_times_each_reply_from_the_thread_opener(tmp_path):
    ctx = fake(tmp_path, replies={("cc-slack", "thread"): fixture("slack-thread.json")})
    feed = slack.slack_feed(ctx, threads=[THREAD], bot="iris")
    assert [(entry.actor, entry.latency_ms) for entry in feed.entries] == [("Iris (claude)", 19796), ("Iris (claude)", 2997)]
    assert feed.entries[0].link == "https://example.slack.com/archives/C0TEST/p1791305708428659?thread_ts=1791305688.632409&cid=C0TEST"
    assert ctx.calls[-1] == ["cc-slack", "thread", "--url", THREAD, "--limit", "50"]


def test_the_slack_feed_without_threads_says_so(tmp_path):
    assert slack.slack_feed(fake(tmp_path)).note.startswith("Not run: no test thread is named yet")
