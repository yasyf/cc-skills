from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from lrdash import platy, views, yamlish
from test_dashboard import dashboard

MOMENT = datetime(2026, 10, 5, 7, 0, tzinfo=UTC)
VIEWS_FILE = Path(__file__).resolve().parents[1] / "skills" / "long-running" / "reference" / "dashboard-views.yaml"

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


def test_the_default_views_parse_and_name_known_types():
    declared = yamlish.loads(VIEWS_FILE.read_text())["views"]
    assert len({view["id"] for view in declared}) == len(declared)
    assert {view["type"] for view in declared} <= set(views.TYPES)


def test_markdown_tables_skip_rows_with_the_wrong_cell_count():
    tables = views.markdown_tables(CENSUS_REPORT)
    assert [row["stack"] for row in views.select_table(CENSUS_REPORT, "Non-zero")] == ["infra/core-usw2-auto"]
    assert len(tables) == 2


def test_pipeline_filters_matches_sorts_and_keeps_the_latest():
    rows = [
        {"at": "2026-10-05T06:00:00Z", "verb": "STATE", "text": "STATE census (cycle 2): 232/293 0/0 at abc"},
        {"at": "2026-10-05T05:00:00Z", "verb": "STATE", "text": "STATE census (cycle 1): 230/293 0/0 at abd 3 drift"},
        {"at": "2026-10-03T05:00:00Z", "verb": "STATE", "text": "STATE census (cycle 0): 200/293 0/0 at abe"},
        {"at": "2026-10-05T06:30:00Z", "verb": "NOTE", "text": "STATE census (cycle 3): 1/293 0/0 at abf"},
    ]
    spec = {"where": {"verb": "^STATE$"}, "since": "48h", "match": r"(?P<n>\d+)/(?P<total>\d+) 0/0 at (?P<head>\w+)(?:.*?(?P<drift>\d+) drift)?"}
    kept = views.pipeline(rows, spec, MOMENT)
    assert [(row["n"], row.get("drift")) for row in kept] == [("232", None), ("230", "3")]
    assert views.pipeline(rows, {"where": {"verb": "!^STATE$"}}, MOMENT)[0]["verb"] == "NOTE"
    assert [row["verb"] for row in views.pipeline(rows, {"latest_by": "verb"}, MOMENT)] == ["NOTE", "STATE"]


def test_stat_reports_value_delta_and_spark():
    rows = [{"n": "232", "total": "293", "at": "b"}, {"n": "230", "total": "293", "at": "a"}]
    data = views.shape("stat", rows, {"value": "n", "of": "total", "detail": ["at"]}, MOMENT)
    assert (data["value"], data["of"], data["delta"], data["spark"], data["detail"]) == (232, 293, 2, [230, 232], {"at": "b"})


def test_series_buckets_by_hour_and_group():
    rows = [
        {"at": "2026-10-05T06:10:00Z", "state": "passed"},
        {"at": "2026-10-05T06:50:00Z", "state": "passed"},
        {"at": "2026-10-05T05:10:00Z", "state": "failed"},
    ]
    assert views.shape("series", rows, {"bucket": "hour", "group": "state"}, MOMENT)["series"] == [
        {"name": "failed", "points": [["2026-10-05T05:00:00Z", 1]]},
        {"name": "passed", "points": [["2026-10-05T06:00:00Z", 2]]},
    ]


def test_matrix_keeps_the_first_row_per_cell():
    rows = [
        {"component": "infra", "env": "plat", "cell": "no", "text": "newest"},
        {"component": "infra", "env": "plat", "cell": "yes", "text": "older"},
        {"component": "dashboard", "env": "sofi", "cell": "yes", "text": "x"},
    ]
    data = views.shape("matrix", rows, {"rows": "component", "cols": "env", "value": "cell"}, MOMENT)
    assert data["rows"] == ["dashboard", "infra"]
    assert data["cols"] == ["plat", "sofi"]
    assert data["cells"]["infra"]["plat"]["value"] == "no"


def test_progress_counts_done_and_excluded():
    rows = [{"state": "LANDED #1"}, {"state": "NO WORK"}, {"state": "OPEN"}]
    assert views.shape("progress", rows, {"done": {"state": "^LANDED"}, "exclude": {"state": "^NO WORK"}}, MOMENT) == {"done": 1, "total": 2, "excluded": 1}


def test_render_rejects_unknown_types_and_sources():
    with pytest.raises(views.SpecError, match="unknown view type"):
        views.render({"type": "pie", "source": "inbox"}, {"inbox": []}, None, MOMENT)
    with pytest.raises(views.SpecError, match="unknown source"):
        views.render({"type": "table", "source": "nope"}, {"inbox": []}, None, MOMENT)


def test_merge_hides_extends_and_appends():
    defaults = [{"id": "a", "title": "A", "type": "table"}, {"id": "b", "title": "B"}]
    declared = [{"id": "b", "hide": True}, {"id": "a", "extend": True, "limit": 5}, {"id": "c", "title": "C"}]
    assert views.merge(defaults, declared) == [{"id": "a", "title": "A", "type": "table", "extend": True, "limit": 5}, {"id": "c", "title": "C"}]


def build(number: int, message: str, state: str = "passed", branch: str = "main", thread: bool = False, at: str = "2026-10-05T05:00:00Z") -> dict:
    env = {"RELEASE_START": json.dumps({"thread": "1759.1"})} if thread else {}
    return {"number": number, "state": state, "branch": branch, "commit": "abcdef1234567890", "created_at": at, "finished_at": at, "message": message, "web_url": f"https://bk/{number}", "env": env}


PIPELINE = {"sha": "c0ffee0123456789", "at": "2026-10-04T12:00:00Z", "subject": "release: move checks into builds"}
AFTER_CHANGE = {"abcdef123456"}


def contains(ancestor: str, commit: str) -> bool:
    assert ancestor == PIPELINE["sha"]
    return commit in AFTER_CHANGE


def rows_of(census, targets, builds, lines, overrides, pipeline_contains=contains):
    return platy.stack_rows(census, targets, builds, lines, overrides, PIPELINE, pipeline_contains)


def test_build_row_tells_a_platy_start_from_a_cli_start_and_a_deploy():
    platy_start = platy.build_row(build(1251, "release infra, platform, started by ym@poetic.com", branch="releases/2026-10-04/1", thread=True))
    assert (platy_start["kind"], platy_start["targets"], platy_start["platy"], platy_start["applies"]) == ("release", ["infra", "platform"], True, True)
    cli = platy.build_row(build(1252, "release infra, started by ym@poetic.com"))
    assert (cli["platy"], cli["applies"]) == (False, False)
    deploy = platy.build_row(build(900, "deploy receiver to plat at 9f8e7d1", branch="releases/deploy-receiver"))
    assert (deploy["kind"], deploy["stacks"], deploy["applies"]) == ("deploy", ["receiver/plat"], True)
    assert platy.build_row(build(901, "release check"))["kind"] == "check"


def test_builds_backfill_once_then_read_a_few_pages(tmp_path):
    pages = []

    def fetch(page):
        pages.append(page)
        return [build(page * 1000 + n, "x") for n in range(platy.PER_PAGE)] if page < 3 else []

    cache = tmp_path / "builds.json"
    assert len(platy.Builds(cache, fetch).refresh()) == 2 * platy.PER_PAGE
    assert pages == [1, 2, 3]
    pages.clear()
    platy.Builds(cache, fetch).refresh()
    assert pages == [1]


def test_mentions_matches_whole_names_only():
    assert platy.mentions("infra").search("release infra, sandsql")
    assert not platy.mentions("infra").search("ci-infra failed")
    assert not platy.mentions("sand").search("sandsql row 4")


def test_census_rows_reads_both_tables():
    rows = platy.census_rows(report_of(CENSUS_REPORT))
    assert [(row["stack"], row["zero"]) for row in rows] == [("infra/core-usw2-auto", "drift"), ("dashboard/plat", "0/0"), ("receiver/plat", "0/0"), ("escape-hatch/plat", "0/0")]


def report_of(text: str) -> SimpleNamespace:
    return SimpleNamespace(read_text=lambda: text)


def test_stack_rows_answer_deployable_reason_and_work():
    census = platy.census_rows(report_of(CENSUS_REPORT))
    targets = {"infra": "infra", "dashboard": "dashboard", "receiver": "executor"}
    builds = [
        platy.build_row(build(1251, "release infra, started by ym@poetic.com", state="failed", branch="releases/x", thread=True, at="2026-10-05T05:00:00Z")),
        platy.build_row(build(574, "release dashboard, started by ym@poetic.com", branch="releases/y", thread=True, at="2026-10-04T05:00:00Z")),
        platy.build_row(build(990, "deploy receiver to plat at 9f8e7d1", branch="releases/deploy-r", at="2026-10-04T06:00:00Z")),
    ]
    lines = [
        {"at": "2026-10-05T06:30:00Z", "lane": "platy-fix", "verb": "OPENED", "text": "OPENED platy-fix #30481 retry infra approval", "url": "/i?2", "to": None},
        {"at": "2026-10-05T05:30:00Z", "lane": "sweep", "verb": "DEFECT", "text": "DEFECT sweep -> platy-fix: infra approval step crashed", "url": "/i?1", "to": "platy-fix"},
    ]
    rows = {row["stack"]: row for row in rows_of(census, targets, builds, lines, {"dashboard": {"doing": "nothing needed"}})}
    infra = rows["infra/core-usw2-auto"]
    assert (infra["deployable"], infra["platy_build"], infra["platy_state"], infra["zero"]) == ("blocked", 1251, "failed", "drift")
    assert infra["blocked_by"].startswith("DEFECT sweep") and infra["doing"].startswith("OPENED platy-fix") and infra["doing_lane"] == "platy-fix"
    assert infra["cell"] == "blocked · drift"
    dashboard = rows["dashboard/plat"]
    assert (dashboard["deployable"], dashboard["proven_at"], dashboard["reason"], dashboard["doing"]) == ("proven", "abcdef123456", None, "nothing needed")
    receiver = rows["receiver/plat"]
    assert (receiver["deployable"], receiver["reason"], receiver["cli_build"]) == ("unproven", "no Platy release has converged receiver/plat", 990)
    assert rows["escape-hatch/plat"]["target"] == platy.UNTARGETED


def test_a_release_that_predates_a_pipeline_change_is_unproven_since_that_change():
    census = platy.census_rows(report_of(CENSUS_REPORT))
    builds = [platy.build_row(build(574, "release dashboard, started by ym@poetic.com", branch="releases/y", thread=True, at="2026-10-04T05:00:00Z"))]
    lines = [{"at": "2026-10-04T13:00:00Z", "lane": "sweep-7", "verb": "GO", "text": "GO root: sweep-7 re-runs the dashboard release", "url": "/i?3", "to": None}]
    [row] = [row for row in rows_of(census, {"dashboard": "dashboard"}, builds, lines, {}, lambda ancestor, commit: False) if row["stack"] == "dashboard/plat"]
    assert (row["deployable"], row["unproven_since"], row["last_pass_commit"], row["doing_lane"]) == ("unproven", "c0ffee012345", "abcdef123456", "sweep-7")
    assert row["reason"].endswith("before the release pipeline changed at c0ffee0123: release: move checks into builds")


def test_a_deselected_stack_is_not_released_by_the_start():
    census = platy.census_rows(report_of(CENSUS_REPORT))
    start = build(423, "release infra, started by ym@poetic.com", branch="releases/z", at="2026-10-04T02:00:00Z")
    start["env"] = {"RELEASE_START": json.dumps({"thread": "1759.1", "deselected": [{"component": "infra", "env": "core-usw2-auto"}]})}
    rows = rows_of(census, {"infra": "infra", "dashboard": "infra"}, [platy.build_row(start)], [], {})
    by_stack = {row["stack"]: row for row in rows}
    assert (by_stack["infra/core-usw2-auto"]["deployable"], by_stack["infra/core-usw2-auto"]["platy_build"]) == ("unproven", None)
    assert (by_stack["dashboard/plat"]["deployable"], by_stack["dashboard/plat"]["platy_build"]) == ("proven", 423)
    [target] = [row for row in platy.target_rows(rows) if row["target"] == "infra"]
    assert (target["stacks"], target["proven"], target["unproven"], target["blocked"], target["deployable"], target["platy_build"], target["at_zero"]) == (2, 1, 1, 0, "unproven", 423, 1)


def test_an_unplanned_stack_is_not_called_drift():
    report = "## Non-zero and unplanned stacks\n\n| stack | cause | c/u/d/r |\n|---|---|---|\n| accounts/ai-gbl-0001 | not planned | - |\n| api/plat | unreleased merge | 0/1/0/0 |\n"
    assert [row["zero"] for row in platy.census_rows(report_of(report))] == ["unplanned", "drift"]


def test_a_failed_refresh_keeps_the_known_builds(tmp_path):
    cache = tmp_path / "builds.json"
    platy.Builds(cache, lambda page: [build(7, "release infra, started by ym@poetic.com")] if page == 1 else []).refresh()

    def down(page):
        raise OSError("bk is down")

    builds = platy.Builds(cache, down)
    with pytest.raises(OSError):
        builds.refresh()
    assert [row["number"] for row in builds.known()] == [7]


def test_a_failed_target_release_blocks_stacks_it_deselected():
    census = platy.census_rows(report_of(CENSUS_REPORT))
    passed = build(423, "release infra, started by ym@poetic.com", branch="releases/a", thread=True, at="2026-10-04T02:00:00Z")
    failed = build(1251, "release infra, started by ym@poetic.com", state="failed", branch="releases/b", at="2026-10-05T05:00:00Z")
    failed["env"] = {"RELEASE_START": json.dumps({"thread": "1759.2", "deselected": [{"component": "dashboard", "env": "plat"}]})}
    rows = {row["stack"]: row for row in rows_of(census, {"dashboard": "infra"}, [platy.build_row(failed), platy.build_row(passed)], [], {})}
    assert (rows["dashboard/plat"]["deployable"], rows["dashboard/plat"]["platy_build"], rows["dashboard/plat"]["reason"]) == ("unproven", 423, "the last Platy release of infra, #1251, failed")


def test_components_count_only_in_stack_form():
    named = platy.naming("infra", ["data", "network"])
    assert any(pattern.search("plan data/tnt-usw2-26qmqm1 is clean") for pattern in named)
    assert any(pattern.search("release infra, ci-infra") for pattern in named)
    assert not any(pattern.search("the data shows network churn") for pattern in named)


def test_an_override_can_point_at_inbox_lines():
    census = platy.census_rows(report_of(CENSUS_REPORT))
    lines = [{"at": "2026-10-05T07:47:00Z", "lane": "sweep-8", "verb": "EVIDENCE", "text": "EVIDENCE sweep-8 -> platy-ux: the start got no reply", "url": "/inbox/deploy-go.md?line=4641", "cite": "inbox:deploy-go.md:4641", "to": "platy-ux"}]
    overrides = {"infra": {"doing_cite": "inbox:deploy-go.md:4641", "reason": "cycle fix landed; start hangs"}}
    [row] = [row for row in rows_of(census, {"infra": "infra"}, [], lines, overrides) if row["stack"] == "infra/core-usw2-auto"]
    assert (row["doing"], row["doing_url"], row["reason"], row["reason_url"]) == (lines[0]["text"], lines[0]["url"], "cycle fix landed; start hangs", None)
    with pytest.raises(KeyError, match="names no inbox line"):
        rows_of(census, {"infra": "infra"}, [], [], overrides)


def test_yamlish_respects_escaped_quotes_and_keeps_literal_headings():
    text = 'a: "x, \\"y\\" # z"\nb: ["p, q", \'r\']\nnote: |\n  # Heading\n  body\n  # Closing\n# a real comment\nc: 1\n'
    assert yamlish.loads(text) == {"a": 'x, "y" # z', "b": ["p, q", "r"], "note": "# Heading\nbody\n# Closing\n", "c": 1}


def test_a_refresh_pages_back_to_an_unsettled_build(tmp_path):
    cache = tmp_path / "builds.json"
    pages = {page: [build((10 - page) * 1000 - n, "x") for n in range(platy.PER_PAGE)] for page in range(1, 6)}
    pages[4][0]["state"] = "running"
    platy.Builds(cache, lambda page: pages.get(page, [])).refresh()
    pages[4][0]["state"] = "passed"
    fetched = []

    def fetch(page):
        fetched.append(page)
        return pages.get(page, [])

    rows = {row["number"]: row for row in platy.Builds(cache, fetch).refresh()}
    assert fetched == [1, 2, 3, 4]
    assert rows[pages[4][0]["number"]]["state"] == "passed"


def test_a_stat_reads_every_field_from_the_row_with_a_value():
    rows = [{"n": None, "total": "20", "at": "new"}, {"n": "9", "total": "19", "at": "old"}]
    data = views.shape("stat", rows, {"value": "n", "of": "total"}, MOMENT)
    assert (data["value"], data["of"], data["at"]) == (9, 19, "old")


def test_a_cli_deploy_covers_every_named_stack():
    row = platy.build_row(build(990, "deploy api, router/rt-usw2-fwd to plat,sofi at 9f8e7d1", branch="releases/deploy-x"))
    assert row["stacks"] == ["api/plat", "api/sofi", "router/rt-usw2-fwd"]


def test_every_source_reads_as_json_over_http(tmp_path):
    server = dashboard.bind("127.0.0.1", 0, dashboard.Collector({"drive": "d", "state_dir": str(tmp_path)}), 60)
    server.sources = {"platy": [{"stack": "infra/core-usw2-auto", "deployable": "unproven"}]}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/sources"
    try:
        with urllib.request.urlopen(f"{base}/platy.json", timeout=5) as response:
            assert json.loads(response.read()) == server.sources["platy"]
        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(f"{base}/nope.json", timeout=5)
        assert missing.value.code == 404
    finally:
        server.shutdown()
