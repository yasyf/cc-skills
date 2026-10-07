from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from conftest import fake
from livedash import view, yamlish
from livedash.components.release import cards, overview, platy
from livedash.context import RATE_LIMIT_BACKOFF, RateLimited

MOMENT = datetime(2026, 10, 5, 7, 0, tzinfo=UTC)
OVERVIEW_MOMENT = datetime(2026, 10, 6, 6, 55, tzinfo=UTC)


CENSUS_REPORT = """\
# IaC drift census

## Non-zero and unplanned stacks

| stack | cause | c/u/d/r | first resources | applied at | commits behind | source |
|---|---|---|---|---|---|---|
| infra/core-usw2-auto | code | 0/2/0/0 | aws:iam:Role | 1a2b3c | 4 | 1251 |
| broken row |

## Stacks at 0/0

| stack | applied at | commits behind |
|---|---|---|
| dashboard/plat | 9f8e7d | 0 |
| receiver/plat | 9f8e7d | 0 |
| escape-hatch/plat | 9f8e7d | 0 |
"""


def build(number: int, message: str, state: str = "passed", branch: str = "main", thread: bool = False, at: str = "2026-10-05T05:00:00Z") -> dict:
    env = {"RELEASE_START": json.dumps({"thread": {"channel": "C0B75AL4XEW", "ts": "1759.1"}})} if thread else {}
    return {"number": number, "state": state, "branch": branch, "commit": "abcdef1234567890", "created_at": at, "finished_at": at, "message": message, "web_url": f"https://bk/{number}", "env": env}


PIPELINE = {"sha": "c0ffee0123456789", "at": "2026-10-04T12:00:00Z", "subject": "release: move checks into builds"}


AFTER_CHANGE = {"abcdef123456"}


def contains(ancestor: str, commit: str) -> bool:
    assert ancestor == PIPELINE["sha"]
    return commit in AFTER_CHANGE


def rows_of(census, targets, builds, lines, pipeline_contains=contains, blockers=()):
    return platy.stack_rows(census, targets, builds, lines, list(blockers), PIPELINE, pipeline_contains)


def record(seq: int, text: str, at: str, lane: str = "sweep", to: tuple[str, ...] = (), stacks: tuple[str, ...] = (), targets: tuple[str, ...] = ()) -> dict:
    return {"seq": seq, "kind": "defect", "lane": lane, "at": at, "text": text, "to": list(to), "refs": {"stacks": list(stacks), "targets": list(targets)}}


def report_of(text: str) -> SimpleNamespace:
    return SimpleNamespace(read_text=lambda: text)


def test_yamlish_reads_the_dashboard_subset():
    text = """\
# drive views
owner: []
platy: {census: 'done-board/*.md', overrides: {}}
views:
  - id: census
    type: stat
    where: {verb: '^STATE$'}
    match: 'N (?P<n>\\d+)'
    columns: [{field: at, time: true}, n]
    note: |-
      first line
      second line
    limit: 40
    hide: false
targets:
  infra:
    components:
      - infra
      - "receiver"
"""
    assert yamlish.loads(text) == {
        "owner": [],
        "platy": {"census": "done-board/*.md", "overrides": {}},
        "views": [
            {
                "id": "census",
                "type": "stat",
                "where": {"verb": "^STATE$"},
                "match": "N (?P<n>\\d+)",
                "columns": [{"field": "at", "time": True}, "n"],
                "note": "first line\nsecond line",
                "limit": 40,
                "hide": False,
            }
        ],
        "targets": {"infra": {"components": ["infra", "receiver"]}},
    }


def test_yamlish_respects_escaped_quotes_and_keeps_literal_headings():
    text = 'a: "x, \\"y\\" # z"\nb: ["p, q", \'r\']\nnote: |\n  # Heading\n  body\n  # Closing\n# a real comment\nc: 1\n'
    assert yamlish.loads(text) == {"a": 'x, "y" # z', "b": ["p, q", "r"], "note": "# Heading\nbody\n# Closing\n", "c": 1}


def test_markdown_tables_skip_rows_with_the_wrong_cell_count():
    tables = view.markdown_tables(CENSUS_REPORT)
    assert [row["stack"] for row in view.select_table(CENSUS_REPORT, "Non-zero")] == ["infra/core-usw2-auto"]
    assert len(tables) == 2


def test_pipeline_filters_matches_sorts_and_keeps_the_latest():
    rows = [
        {"at": "2026-10-05T06:00:00Z", "verb": "STATE", "text": "STATE census (cycle 2): 232/293 0/0 at abc"},
        {"at": "2026-10-05T05:00:00Z", "verb": "STATE", "text": "STATE census (cycle 1): 230/293 0/0 at abd 3 drift"},
        {"at": "2026-10-03T05:00:00Z", "verb": "STATE", "text": "STATE census (cycle 0): 200/293 0/0 at abe"},
        {"at": "2026-10-05T06:30:00Z", "verb": "NOTE", "text": "STATE census (cycle 3): 1/293 0/0 at abf"},
    ]
    spec = {"where": {"verb": "^STATE$"}, "since": "48h", "match": r"(?P<n>\d+)/(?P<total>\d+) 0/0 at (?P<head>\w+)(?:.*?(?P<drift>\d+) drift)?"}
    kept = view.pipeline(rows, spec, MOMENT)
    assert [(row["n"], row.get("drift")) for row in kept] == [("232", None), ("230", "3")]
    assert view.pipeline(rows, {"where": {"verb": "!^STATE$"}}, MOMENT)[0]["verb"] == "NOTE"
    assert [row["verb"] for row in view.pipeline(rows, {"latest_by": "verb"}, MOMENT)] == ["NOTE", "STATE"]


def test_build_row_tells_a_platy_start_from_a_cli_start_and_a_deploy():
    platy_start = platy.build_row(build(1251, "release infra, platform, started by ym@poetic.com", branch="releases/2026-10-04/1", thread=True))
    assert (platy_start["kind"], platy_start["targets"], platy_start["platy"], platy_start["applies"]) == ("release", ["infra", "platform"], True, True)
    cli = platy.build_row(build(1252, "release infra, started by ym@poetic.com"))
    assert (cli["platy"], cli["applies"]) == (False, False)
    deploy = platy.build_row(build(900, "deploy receiver to plat at 9f8e7d1", branch="releases/deploy-receiver"))
    assert (deploy["kind"], deploy["stacks"], deploy["applies"]) == ("deploy", ["receiver/plat"], True)
    assert platy.build_row(build(901, "release check"))["kind"] == "check"


def test_build_row_reads_plans_rollbacks_skips_and_the_running_step():
    plan = platy.build_row(build(902, "plan the deploy of escape-hatch-base-ami, reco to plat-use1-prod at 182f47f1ec0b"))
    assert (plan["kind"], plan["stacks"], plan["applies"]) == ("plan", ["escape-hatch-base-ami/plat-use1-prod", "reco/plat-use1-prod"], False)
    rollback = platy.build_row(build(903, "roll back api/plat-usw2-prod, router/rt-usw2-fwd at cad15aa81eca", branch="releases/deploy/13-stacks/2026-10-05/1966"))
    assert (rollback["kind"], rollback["stacks"], rollback["applies"]) == ("rollback", ["api/plat-usw2-prod", "router/rt-usw2-fwd"], True)
    raw = build(904, "deploy storage/core-usw2-artifacts at de561dc2d100, allowing deletes and replaces, without plat-use1-prod, tnt-usw2-0ddq7rb", state="running")
    raw["jobs"] = [
        {"type": "script", "state": "passed", "name": ":mag: preview storage on core-usw2-artifacts"},
        {"type": "script", "state": "running", "name": ":pulumi: deploy storage on core-usw2-artifacts"},
        {"type": "script", "state": "failed", "name": ":broom: delete what this deploy outlived"},
        {"type": "waiter", "state": "passed"},
    ]
    deploy = platy.build_row(raw)
    assert (deploy["without"], deploy["destructive"], deploy["steps"], deploy["now"], deploy["failed_steps"]) == (["plat-use1-prod", "tnt-usw2-0ddq7rb"], True, [1, 3], ["deploy storage on core-usw2-artifacts"], ["delete what this deploy outlived"])
    assert platy.build_row(build(905, "release infra, started by ym@poetic.com", branch="releases/x", thread=True))["thread"] == {"channel": "C0B75AL4XEW", "ts": "1759.1"}


def test_a_cli_deploy_covers_every_named_stack():
    row = platy.build_row(build(990, "deploy api, router/rt-usw2-fwd to plat,sofi at 9f8e7d1", branch="releases/deploy-x"))
    assert row["stacks"] == ["api/plat", "api/sofi", "router/rt-usw2-fwd"]


def test_builds_backfill_once_then_ask_for_live_builds_and_sweep_finished_ones(tmp_path):
    queries = []

    def fetch(query):
        queries.append(query)
        return [build(query["page"] * 1000 + n, "x") for n in range(platy.PER_PAGE)] if set(query) == {"page"} and query["page"] < 3 else []

    cache = tmp_path / "builds.json"
    assert len(platy.Builds(cache, fetch).refresh(MOMENT)) == 2 * platy.PER_PAGE
    assert queries == [{"page": 1}, {"page": 2}, {"page": 3}]
    queries.clear()
    builds = platy.Builds(cache, fetch)
    assert (builds.fetched_at, builds.swept_at) == (MOMENT, MOMENT - platy.RESWEEP)
    builds.swept_at = MOMENT
    builds.refresh(MOMENT + timedelta(minutes=1))
    assert queries == [{"state[]": list(platy.LIVE), "page": 1}]
    queries.clear()
    builds.refresh(MOMENT + platy.RESWEEP)
    assert queries == [{"state[]": list(platy.LIVE), "page": 1}, {"finished_from": "2026-10-05T06:58:00Z", "page": 1}]


def test_a_failed_refresh_keeps_the_known_builds(tmp_path):
    cache = tmp_path / "builds.json"
    platy.Builds(cache, lambda query: [build(7, "release infra, started by ym@poetic.com")] if query["page"] == 1 else []).refresh(MOMENT)

    def down(query):
        raise OSError("bk is down")

    builds = platy.Builds(cache, down)
    with pytest.raises(OSError):
        builds.refresh(MOMENT)
    assert [row["number"] for row in builds.known()] == [7]


def test_a_build_leaving_the_live_set_is_swept_at_once_and_a_retry_rejoins_it(tmp_path):
    cache = tmp_path / "builds.json"
    running = build(41, "x", state="running")
    platy.Builds(cache, lambda query: [running, build(40, "x", state="failed")]).refresh(MOMENT)
    answers = {"state[]": [build(40, "x", state="running")], "finished_from": [running | {"state": "passed"}]}
    queries = []

    def fetch(query):
        queries.append(query)
        return next(answer for key, answer in answers.items() if key in query)

    builds = platy.Builds(cache, fetch)
    builds.swept_at = MOMENT
    rows = {row["number"]: row for row in builds.refresh(MOMENT + timedelta(minutes=1))}
    assert [next(iter(query)) for query in queries] == ["state[]", "finished_from"]
    assert (rows[41]["state"], rows[40]["state"], builds.swept_at) == ("passed", "running", MOMENT + timedelta(minutes=1))


def test_a_backfill_cut_short_resumes_at_its_next_page(tmp_path):
    full = [build(n, "x") for n in range(platy.PER_PAGE)]
    answers = iter([full, OSError("429"), [build(500, "x")]])

    def fetch(query):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    builds = platy.Builds(tmp_path / "builds.json", fetch)
    with pytest.raises(OSError):
        builds.refresh(MOMENT)
    assert (builds.fetched_at, builds.backfill_page) == (None, 2)
    builds.swept_at = None
    assert len(builds.refresh(MOMENT + timedelta(minutes=1))) == platy.PER_PAGE + 1
    assert (builds.fetched_at, builds.backfill_page) == (MOMENT + timedelta(minutes=1), 0)


def test_each_refresh_reads_a_bounded_number_of_backfill_pages_and_a_restart_resumes_there(tmp_path):
    queries = []

    def fetch(query):
        queries.append(query)
        return [build(query["page"] * 1000 + n, "x") for n in range(platy.PER_PAGE)] if set(query) == {"page"} else []

    cache = tmp_path / "builds.json"
    platy.Builds(cache, fetch).refresh(MOMENT)
    assert queries == [{"page": page} for page in range(1, platy.BACKFILL_PAGES + 1)]
    queries.clear()
    builds = platy.Builds(cache, fetch)
    assert (len(builds.known()), builds.backfill_page) == (platy.BACKFILL_PAGES * platy.PER_PAGE, platy.BACKFILL_PAGES + 1)
    builds.refresh(MOMENT + timedelta(minutes=1))
    assert queries[0] == {"state[]": list(platy.LIVE), "page": 1}
    assert [query["page"] for query in queries if set(query) == {"page"}] == list(range(platy.BACKFILL_PAGES + 1, 2 * platy.BACKFILL_PAGES + 1))


def test_mentions_matches_whole_names_only():
    assert platy.mentions("infra").search("release infra, sandsql")
    assert not platy.mentions("infra").search("ci-infra failed")
    assert not platy.mentions("sand").search("sandsql row 4")


def test_components_count_only_in_stack_form():
    named = platy.naming("infra", ["data", "network"])
    assert any(pattern.search("plan data/tnt-usw2-26qmqm1 is clean") for pattern in named)
    assert any(pattern.search("release infra, ci-infra") for pattern in named)
    assert not any(pattern.search("the data shows network churn") for pattern in named)


def test_census_rows_reads_both_tables():
    rows = platy.census_rows(report_of(CENSUS_REPORT))
    assert [(row["stack"], row["zero"]) for row in rows] == [("infra/core-usw2-auto", "drift"), ("dashboard/plat", "0/0"), ("receiver/plat", "0/0"), ("escape-hatch/plat", "0/0")]


def test_an_unplanned_stack_is_not_called_drift():
    report = "## Non-zero and unplanned stacks\n\n| stack | cause | c/u/d/r |\n|---|---|---|\n| accounts/ai-gbl-0001 | not planned | - |\n| api/plat | unreleased merge | 0/1/0/0 |\n"
    assert [row["zero"] for row in platy.census_rows(report_of(report))] == ["unplanned", "drift"]


def test_stack_rows_answer_deployable_reason_and_work():
    census = platy.census_rows(report_of(CENSUS_REPORT))
    targets = {"infra": "infra", "dashboard": "dashboard", "receiver": "executor"}
    builds = [
        platy.build_row(build(1251, "release infra, started by ym@poetic.com", state="failed", branch="releases/x", thread=True, at="2026-10-05T05:00:00Z")),
        platy.build_row(build(574, "release dashboard, started by ym@poetic.com", branch="releases/y", thread=True, at="2026-10-04T05:00:00Z")),
        platy.build_row(build(990, "deploy receiver to plat at 9f8e7d1", branch="releases/deploy-r", at="2026-10-04T06:00:00Z")),
    ]
    lines = [{"at": "2026-10-05T06:30:00Z", "lane": "platy-fix", "verb": "OPENED", "text": "OPENED platy-fix #30481 retry approval", "url": "/i?2", "to": None}]
    defect = record(25100, "infra approval step crashed", "2026-10-05T05:30:00.120Z", to=("platy-fix",), targets=("infra",))
    rows = {row["stack"]: row for row in rows_of(census, targets, builds, lines, blockers=[defect])}
    infra = rows["infra/core-usw2-auto"]
    assert (infra["deployable"], infra["platy_build"], infra["platy_state"], infra["zero"]) == ("blocked", 1251, "failed", "drift")
    assert (infra["blocked_by"], infra["blocked_seq"], infra["doing_lane"]) == ("infra approval step crashed", 25100, "platy-fix")
    assert infra["cell"] == "blocked · drift"
    dashboard = rows["dashboard/plat"]
    assert (dashboard["deployable"], dashboard["proven_at"], dashboard["reason"], dashboard["doing"]) == ("proven", "abcdef123456", None, None)
    receiver = rows["receiver/plat"]
    assert (receiver["deployable"], receiver["reason"], receiver["cli_build"]) == ("unproven", "no Platy release has converged receiver/plat", 990)
    assert rows["escape-hatch/plat"]["target"] == platy.UNTARGETED


def test_a_record_naming_one_stack_blocks_only_that_stack():
    census = platy.census_rows(report_of(CENSUS_REPORT))
    release = build(574, "release infra, started by ym@poetic.com", branch="releases/y", thread=True, at="2026-10-04T05:00:00Z")
    hold = record(25200, "receiver/plat waits on the owner", "2026-10-05T01:00:00Z", stacks=("receiver/plat",))
    rows = {row["stack"]: row for row in rows_of(census, {"dashboard": "infra", "receiver": "infra"}, [platy.build_row(release)], [], blockers=[hold])}
    assert (rows["dashboard/plat"]["deployable"], rows["receiver/plat"]["deployable"], rows["receiver/plat"]["blocked_seq"]) == ("proven", "blocked", 25200)


def test_a_release_that_predates_a_pipeline_change_is_unproven_since_that_change():
    census = platy.census_rows(report_of(CENSUS_REPORT))
    builds = [platy.build_row(build(574, "release dashboard, started by ym@poetic.com", branch="releases/y", thread=True, at="2026-10-04T05:00:00Z"))]
    lines = [{"at": "2026-10-04T13:00:00Z", "lane": "sweep-7", "verb": "GO", "text": "GO root: sweep-7 re-runs the dashboard release", "url": "/i?3", "to": None}]
    [row] = [row for row in rows_of(census, {"dashboard": "dashboard"}, builds, lines, lambda ancestor, commit: False) if row["stack"] == "dashboard/plat"]
    assert (row["deployable"], row["unproven_since"], row["last_pass_commit"], row["doing_lane"]) == ("unproven", "c0ffee012345", "abcdef123456", "sweep-7")
    assert row["reason"].endswith("before the release pipeline changed at c0ffee0123: release: move checks into builds")


def test_a_deselected_stack_is_not_released_by_the_start():
    census = platy.census_rows(report_of(CENSUS_REPORT))
    start = build(423, "release infra, started by ym@poetic.com", branch="releases/z", at="2026-10-04T02:00:00Z")
    start["env"] = {"RELEASE_START": json.dumps({"thread": "1759.1", "deselected": [{"component": "infra", "env": "core-usw2-auto"}]})}
    rows = rows_of(census, {"infra": "infra", "dashboard": "infra"}, [platy.build_row(start)], [])
    by_stack = {row["stack"]: row for row in rows}
    assert (by_stack["infra/core-usw2-auto"]["deployable"], by_stack["infra/core-usw2-auto"]["platy_build"]) == ("unproven", None)
    assert (by_stack["dashboard/plat"]["deployable"], by_stack["dashboard/plat"]["platy_build"]) == ("proven", 423)
    [target] = [row for row in platy.target_rows(rows) if row["target"] == "infra"]
    assert (target["stacks"], target["proven"], target["unproven"], target["blocked"], target["deployable"], target["platy_build"], target["at_zero"]) == (2, 1, 1, 0, "unproven", 423, 1)


def test_a_failed_target_release_blocks_stacks_it_deselected():
    census = platy.census_rows(report_of(CENSUS_REPORT))
    passed = build(423, "release infra, started by ym@poetic.com", branch="releases/a", thread=True, at="2026-10-04T02:00:00Z")
    failed = build(1251, "release infra, started by ym@poetic.com", state="failed", branch="releases/b", at="2026-10-05T05:00:00Z")
    failed["env"] = {"RELEASE_START": json.dumps({"thread": "1759.2", "deselected": [{"component": "dashboard", "env": "plat"}]})}
    rows = {row["stack"]: row for row in rows_of(census, {"dashboard": "infra"}, [platy.build_row(failed), platy.build_row(passed)], [])}
    assert (rows["dashboard/plat"]["deployable"], rows["dashboard/plat"]["platy_build"], rows["dashboard/plat"]["reason"]) == ("unproven", 423, "the last Platy release of infra, #1251, failed")


def test_a_target_reports_the_work_of_its_unproven_stacks():
    census = platy.census_rows(report_of(CENSUS_REPORT))
    release = build(574, "release infra, started by ym@poetic.com", branch="releases/y", thread=True, at="2026-10-04T05:00:00Z")
    lines = [{"at": "2026-10-04T13:00:00Z", "lane": "sweep-7", "verb": "GO", "text": "GO root: sweep-7 re-runs receiver/plat", "url": "/i?4", "to": None}]
    deselecting = dict(release, env={"RELEASE_START": json.dumps({"thread": "1759.1", "deselected": [{"component": "receiver", "env": "plat"}]})})
    rows = rows_of(census, {"dashboard": "infra", "receiver": "infra"}, [platy.build_row(deselecting)], lines)
    assert [(row["stack"], row["deployable"]) for row in rows if row["target"] == "infra"] == [("dashboard/plat", "proven"), ("receiver/plat", "unproven")]
    [target] = [row for row in platy.target_rows(rows) if row["target"] == "infra"]
    assert (target["deployable"], target["doing_lane"], target["doing"]) == ("unproven", "sweep-7", lines[0]["text"])


def row(number: int, message: str, state: str = "passed", branch: str = "dev", at: str = "2026-10-06T06:00:00Z", finished: str | None = "2026-10-06T06:10:00Z", env: dict | None = None) -> dict:
    return platy.build_row({"number": number, "state": state, "branch": branch, "commit": f"{number:012d}", "created_at": at, "started_at": at, "finished_at": finished, "message": message, "web_url": f"https://bk/{number}", "env": env or {}})


def cci_record(seq: int, kind: str, lane: str, text: str, at: str = "2026-10-06T06:00:00Z", topic: str | None = None, **links: int) -> dict:
    return {"seq": seq, "kind": kind, "lane": lane, "text": text, "at": at, "topic": topic, "refs": {}, **links}


@pytest.mark.parametrize(
    ("message", "title"),
    [
        ("deploy receiver/core-usw2-auto at 0a185fff46e5, without plat-usw2-prod", "Deploy receiver to core-usw2-auto"),
        ("plan the deploy of escape-hatch-base-ami, reco to plat-use1-prod at 182f47f1ec0b", "Plan escape-hatch-base-ami and reco to plat-use1-prod"),
        ("deploy forge-dns/tnt-a, forge-dns/tnt-b at 0a185fff46e5", "Deploy forge-dns to 2 environments"),
        ("deploy a/x, b/x, c/x, d/x at 0a185fff46e5", "Deploy 4 stacks across 4 components"),
        ("release platform, executor, started by andrew.benton@withforge.com", "Release platform and executor"),
        ("dry-run infra, started by ym@poetic.com", "Dry run of infra"),
        ("release check of dev at e5847b05a47c", "Release check"),
    ],
)
def test_titles_name_targets_instead_of_the_raw_message(message, title):
    assert overview.title_of(row(1, message)) == title


def test_a_launcher_folds_into_the_build_it_started_and_keeps_its_slack_link():
    builds = [
        row(2195, "deploy receiver/core-usw2-auto at 0a185fff46e5", state="failed", branch="releases/deploy/receiver/core-usw2-auto/2026-10-05/2193"),
        row(2193, "deploy receiver/core-usw2-auto at 0a185fff46e5", finished="2026-10-06T06:00:12Z"),
        row(2200, "plan the deploy of reco to plat-use1-prod at 182f47f1ec0b", state="failed"),
        row(2085, "release executor, started by andrew.benton@withforge.com", branch="releases/2026-10-05/14", env={"RELEASE_START": json.dumps({"thread": {"channel": "C0B75AL4XEW", "ts": "1791251534.097049"}})}),
        row(2083, "Platy asks for a release launch"),
    ]
    subjects = {f"{2195:012d}": "cc-remote: pin 0.22.1 (#30856)"}
    rows = overview.release_rows(sorted(builds, key=lambda b: -b["number"]), subjects, "Forge-AI/monorepo", "https://in-the-forge.slack.com", {2193: "https://in-the-forge.slack.com/archives/C1/p1"}, OVERVIEW_MOMENT)
    assert [r["number"] for r in rows] == [2200, 2195, 2085]
    failed = rows[1]
    assert (failed["launcher"], failed["slack"], failed["pr"], failed["pr_title"], failed["minutes"]) == (2193, "https://in-the-forge.slack.com/archives/C1/p1", 30856, "cc-remote: pin 0.22.1", 10.0)
    assert (rows[2]["slack"], rows[2]["by"]) == ("https://in-the-forge.slack.com/archives/C0B75AL4XEW/p1791251534097049", "Andrew Benton")


def test_slack_links_pair_one_release_build_with_its_thread():
    lines = [
        {"text": "RELEASE dashboard PASSED https://buildkite.com/forge/release/builds/276 thread https://in-the-forge.slack.com/archives/C0B/p17910"},
        {"text": "CI https://buildkite.com/forge/monorepo/builds/277 https://in-the-forge.slack.com/archives/C0B/p17911"},
        {"text": "two builds https://buildkite.com/forge/release/builds/278 https://buildkite.com/forge/release/builds/279 https://in-the-forge.slack.com/archives/C0B/p17912"},
    ]
    assert overview.slack_links(lines, "release") == {276: "https://in-the-forge.slack.com/archives/C0B/p17910"}


def test_incidents_group_fix_lanes_sightings_and_recoveries():
    records = [
        cci_record(1, "incident", "alerts-watch-12", "NEW Alert 324971208 Escape-hatch site unavailable (delos), 10:42 PM PT https://dd"),
        cci_record(2, "incident", "alerts-watch-12", "NEW Alert 324971203 Escape-hatch site unavailable (apollo-demo), 10:44 PM PT"),
        cci_record(3, "mechanism", "eh-delos-2242-fix", "Delos ready again", topic="eh-delos-2242"),
        cci_record(4, "evidence", "eh-delos-evidence", "eh-delos-2242: delos down 05:39-05:48Z"),
        cci_record(5, "fix-live", "eh-delos-2242-fix", "FIX-LIVE delos", topic="eh-delos-2242"),
        cci_record(6, "recovered", "alerts-watch-12", "RECOVERED escape-hatch site unavailable: apollo-demo 324971203"),
        cci_record(7, "incident", "incident-slack-watch-37", "11:22 PM #alerts-api Sentry [api] Error: Workflow not found"),
        cci_record(8, "mechanism", "wf-not-found-2322-fix", "MECHANISM pinned"),
    ]
    groups = {group["key"]: group for group in overview.incident_groups(records, {1, 2, 7}, [{"slug": "wf-not-found-2322", "path": "/x", "text": "brief.md"}], OVERVIEW_MOMENT, set(), set())}
    assert sorted(groups) == ["eh-delos-2242", "seq:2", "wf-not-found-2322"]
    assert ([r["seq"] for r in groups["eh-delos-2242"]["records"]], groups["eh-delos-2242"]["status"], groups["eh-delos-2242"]["active"]) == ([5, 4, 3, 1], "fix live", False)
    assert (groups["seq:2"]["status"], groups["seq:2"]["title"], groups["seq:2"]["active"]) == ("recovered", "Escape-hatch site unavailable (apollo-demo), 10:44 PM PT", False)
    assert (groups["wf-not-found-2322"]["status"], groups["wf-not-found-2322"]["active"], groups["wf-not-found-2322"]["files"]) == ("mechanism found", True, "brief.md")


def test_an_unanswered_report_older_than_a_day_is_quiet_not_open():
    group = overview.incident_groups([cci_record(1, "incident", "root", "INCIDENT 1:36 PM cert cap", at="2026-10-04T20:00:00Z")], {1}, [], OVERVIEW_MOMENT, set(), set())[0]
    assert (group["status"], group["quiet"], group["active"]) == ("open", True, False)


def test_landings_count_each_pr_once_in_its_pacific_hour():
    records = [
        {"seq": 1, "lane": "landing-sweep-23", "at": "2026-10-06T06:42:00Z", "text": "LANDED #30863", "refs": {"prs": [30863]}},
        {"seq": 2, "lane": "fix", "at": "2026-10-06T06:51:00Z", "text": "verified #30863 landed", "refs": {"prs": [30863]}},
        {"seq": 3, "lane": "landing-sweep-23", "at": "2026-10-06T05:10:00Z", "text": "LANDED #30875", "refs": {"prs": [30875]}},
    ]
    landed = overview.landings(records, {30863: "infra: grants"}, "Forge-AI/monorepo")
    assert [(r["pr"], r["title"]) for r in landed] == [(30863, "infra: grants"), (30875, "LANDED #30875")]
    hours = overview.landed_hours(landed, OVERVIEW_MOMENT)
    assert (len(hours), hours[-1]["count"], hours[-2]["count"]) == (overview.LANDED_HOURS, 1, 1)


def test_the_overview_counts_only_builds_that_apply():
    builds = [
        row(3, "deploy a/x at 0a185fff46e5", state="failed", branch="releases/deploy/a/3"),
        row(2, "deploy a/x at 0a185fff46e5"),
        row(1, "plan the deploy of a/x at 0a185fff46e5", state="failed"),
    ]
    releases = overview.release_rows(builds, {}, "o/r", None, {}, OVERVIEW_MOMENT)
    summary = overview.overview(releases, {"total": 2, "at_zero": 1}, [{"zero": "0/0"}, {"zero": "drift"}], 4, [], [], [], [], [], OVERVIEW_MOMENT)
    assert summary["tiles"]["releases_today"] == {"passed": 0, "failed": 1, "canceled": 0, "running": 0}
    assert (summary["last_release"]["number"], [d["number"] for d in summary["charts"]["durations"]]) == (3, [3])
    assert summary["charts"]["states"] == [{"state": "0/0", "count": 1}, {"state": "drift", "count": 1}, {"state": "unplanned", "count": 0}]


def test_a_rate_limited_build_read_holds_the_shared_cache_for_every_card(tmp_path):
    reads = []

    def json_reply(argv, cwd=None):
        reads.append(argv)
        raise RateLimited("exit 1: HTTP request failed: 429 429 Too Many Requests")

    def ctx(moment):
        return SimpleNamespace(dir=tmp_path, now=moment, json=json_reply)

    with pytest.raises(RateLimited):
        cards.known_builds(ctx(MOMENT), "release", "/checkout")
    assert cards.known_builds(ctx(MOMENT + cards.BUILDS_INTERVAL), "release", "/checkout") == []
    assert cards.known_builds(ctx(MOMENT + timedelta(seconds=RATE_LIMIT_BACKOFF) - timedelta(seconds=1)), "release", "/checkout") == []
    assert len(reads) == 1
    with pytest.raises(RateLimited):
        cards.known_builds(ctx(MOMENT + timedelta(seconds=RATE_LIMIT_BACKOFF)), "release", "/checkout")
    assert len(reads) == 2


def test_work_lines_ask_cci_only_for_kinds_it_knows(tmp_path):
    ctx = fake(tmp_path, cci_replies={"records": [{"seq": 9, "at": "2026-10-06T01:00:00Z", "lane": "walker", "kind": "release", "text": "receiver live", "refs": {}}]})
    assert [(entry.text, entry.tone) for entry in cards.lines(ctx).entries] == [("RELEASE receiver live", "ok")]
