from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

import pytest
from lrdash import overview, platy
from test_dashboard import dashboard

MOMENT = datetime(2026, 10, 6, 6, 55, tzinfo=UTC)


def row(number: int, message: str, state: str = "passed", branch: str = "dev", at: str = "2026-10-06T06:00:00Z", finished: str | None = "2026-10-06T06:10:00Z", env: dict | None = None) -> dict:
    return platy.build_row({"number": number, "state": state, "branch": branch, "commit": f"{number:012d}", "created_at": at, "started_at": at, "finished_at": finished, "message": message, "web_url": f"https://bk/{number}", "env": env or {}})


def record(seq: int, kind: str, lane: str, text: str, at: str = "2026-10-06T06:00:00Z", topic: str | None = None, **links: int) -> dict:
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
    rows = overview.release_rows(sorted(builds, key=lambda b: -b["number"]), subjects, "Forge-AI/monorepo", "https://in-the-forge.slack.com", {2193: "https://in-the-forge.slack.com/archives/C1/p1"}, MOMENT)
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
        record(1, "incident", "alerts-watch-12", "NEW Alert 324971208 Escape-hatch site unavailable (delos), 10:42 PM PT https://dd"),
        record(2, "incident", "alerts-watch-12", "NEW Alert 324971203 Escape-hatch site unavailable (apollo-demo), 10:44 PM PT"),
        record(3, "mechanism", "eh-delos-2242-fix", "Delos ready again", topic="eh-delos-2242"),
        record(4, "evidence", "eh-delos-evidence", "eh-delos-2242: delos down 05:39-05:48Z"),
        record(5, "fix-live", "eh-delos-2242-fix", "FIX-LIVE delos", topic="eh-delos-2242"),
        record(6, "recovered", "alerts-watch-12", "RECOVERED escape-hatch site unavailable: apollo-demo 324971203"),
        record(7, "incident", "incident-slack-watch-37", "11:22 PM #alerts-api Sentry [api] Error: Workflow not found"),
        record(8, "mechanism", "wf-not-found-2322-fix", "MECHANISM pinned"),
    ]
    groups = {group["key"]: group for group in overview.incident_groups(records, {1, 2, 7}, [{"slug": "wf-not-found-2322", "path": "/x", "text": "brief.md"}], MOMENT, set(), set())}
    assert sorted(groups) == ["eh-delos-2242", "seq:2", "wf-not-found-2322"]
    assert ([r["seq"] for r in groups["eh-delos-2242"]["records"]], groups["eh-delos-2242"]["status"], groups["eh-delos-2242"]["active"]) == ([5, 4, 3, 1], "fix live", False)
    assert (groups["seq:2"]["status"], groups["seq:2"]["title"], groups["seq:2"]["active"]) == ("recovered", "Escape-hatch site unavailable (apollo-demo), 10:44 PM PT", False)
    assert (groups["wf-not-found-2322"]["status"], groups["wf-not-found-2322"]["active"], groups["wf-not-found-2322"]["files"]) == ("mechanism found", True, "brief.md")


def test_an_unanswered_report_older_than_a_day_is_quiet_not_open():
    group = overview.incident_groups([record(1, "incident", "root", "INCIDENT 1:36 PM cert cap", at="2026-10-04T20:00:00Z")], {1}, [], MOMENT, set(), set())[0]
    assert (group["status"], group["quiet"], group["active"]) == ("open", True, False)


def test_landings_count_each_pr_once_in_its_pacific_hour():
    records = [
        {"seq": 1, "lane": "landing-sweep-23", "at": "2026-10-06T06:42:00Z", "text": "LANDED #30863", "refs": {"prs": [30863]}},
        {"seq": 2, "lane": "fix", "at": "2026-10-06T06:51:00Z", "text": "verified #30863 landed", "refs": {"prs": [30863]}},
        {"seq": 3, "lane": "landing-sweep-23", "at": "2026-10-06T05:10:00Z", "text": "LANDED #30875", "refs": {"prs": [30875]}},
    ]
    landed = overview.landings(records, {30863: "infra: grants"}, "Forge-AI/monorepo")
    assert [(r["pr"], r["title"]) for r in landed] == [(30863, "infra: grants"), (30875, "LANDED #30875")]
    hours = overview.landed_hours(landed, MOMENT)
    assert (len(hours), hours[-1]["count"], hours[-2]["count"]) == (overview.LANDED_HOURS, 1, 1)


def test_the_overview_counts_only_builds_that_apply():
    builds = [
        row(3, "deploy a/x at 0a185fff46e5", state="failed", branch="releases/deploy/a/3"),
        row(2, "deploy a/x at 0a185fff46e5"),
        row(1, "plan the deploy of a/x at 0a185fff46e5", state="failed"),
    ]
    releases = overview.release_rows(builds, {}, "o/r", None, {}, MOMENT)
    summary = overview.overview(releases, {"total": 2, "at_zero": 1}, [{"zero": "0/0"}, {"zero": "drift"}], 4, [], [], [], [], [], MOMENT)
    assert summary["tiles"]["releases_today"] == {"passed": 0, "failed": 1, "canceled": 0, "running": 0}
    assert (summary["last_release"]["number"], [d["number"] for d in summary["charts"]["durations"]]) == (3, [3])
    assert summary["charts"]["states"] == [{"state": "0/0", "count": 1}, {"state": "drift", "count": 1}, {"state": "unplanned", "count": 0}]


def test_an_owner_action_posts_one_owner_record_to_main(tmp_path, monkeypatch):
    calls = []

    def run(argv, cwd=None):
        calls.append(argv)
        return json.dumps({"seq": 41, "at": "2026-10-06T07:00:00Z"})

    monkeypatch.setattr(dashboard, "run", run)
    collector = dashboard.Collector({"drive": "d", "state_dir": str(tmp_path / "release-v3")})
    (tmp_path / "release-v3").mkdir()
    stored = collector.act({"cite": "task:7", "text": "grant actions:write"}, "question", "still needed?")
    assert calls == [[str(dashboard.CCI_BIN), "post", "--drive", "release-v3", "--lane", "owner", "--kind", "owner", "--to", "main", "--text", "Question: grant actions:write [task:7] — still needed?", "--json"]]
    assert collector.actions() == [stored] == [{"cite": "task:7", "action": "question", "text": "still needed?", "at": "2026-10-06T07:00:00Z", "seq": 41}]
    rows = dashboard.with_actions([{"cite": "task:7"}], collector.actions(), [{"seq": 42, "re": 41, "text": "yes"}])
    assert rows[0]["actions"][0]["reply"]["text"] == "yes"


def test_a_long_item_is_clipped_so_the_owner_text_fits(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "run", lambda argv, cwd=None: (sent.append(argv[11]), json.dumps({"seq": 1, "at": "x"}))[1])
    sent: list[str] = []
    (tmp_path / "p").mkdir()
    dashboard.Collector({"drive": "d", "state_dir": str(tmp_path / "p")}).act({"cite": "ask:a", "text": "x" * 600}, "complete", "")
    assert len(sent[0]) == dashboard.CCI_TEXT and sent[0].startswith("Mark complete: xxx") and sent[0].endswith("… [ask:a]")


@pytest.mark.parametrize(("headers", "status"), [({}, 403), ({"X-Dashboard-Action": "wrong"}, 403), ({"X-Dashboard-Action": "{token}", "Origin": "http://evil.example"}, 403), ({"X-Dashboard-Action": "{token}"}, 200)])
def test_owner_actions_need_the_page_token_and_the_same_origin(tmp_path, monkeypatch, headers, status):
    monkeypatch.setattr(dashboard, "run", lambda argv, cwd=None: json.dumps({"seq": 9, "at": "2026-10-06T07:00:00Z"}))
    server = dashboard.bind("127.0.0.1", 0, dashboard.Collector({"drive": "d", "state_dir": str(tmp_path)}), 60)
    server.sources = {"owner": [{"cite": "task:7", "text": "approve"}]}
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sent = {key: value.format(token=server.action_token) for key, value in headers.items()}
    request = urllib.request.Request(f"http://127.0.0.1:{port}/owner/act", data=json.dumps({"cite": "task:7", "action": "reply", "text": "done"}).encode(), method="POST", headers={"Content-Type": "application/json", **sent})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            assert (response.status, json.loads(response.read())["seq"]) == (status, 9)
    except urllib.error.HTTPError as failure:
        assert failure.code == status
    finally:
        server.shutdown()


def test_identical_launchers_fold_into_the_build_whose_branch_names_them():
    message = "deploy a/x at 0a185fff46e5"
    builds = [row(10, message), row(11, message), row(13, message, branch="releases/deploy/a/x/2026-10-05/11"), row(14, message, branch="releases/deploy/a/x/2026-10-05/10")]
    assert overview.launchers(builds) == {11: 13, 10: 14}


def test_a_deploy_to_several_environments_names_each_stack():
    deploy = platy.build_row({"number": 1, "state": "passed", "branch": "dev", "message": "plan the deploy of api to plat-usw2-prod, tnt-usw2-0ddq7rb at abcdef123456", "web_url": "u"})
    assert deploy["stacks"] == ["api/plat-usw2-prod", "api/tnt-usw2-0ddq7rb"]


def test_an_alert_stays_open_while_any_report_in_it_is_open():
    records = [record(1, "incident", "alerts-watch-12", "NEW Alert 324971203 site down"), record(2, "evidence", "alerts-watch-12", "still down 324971203")]
    group = overview.incident_groups(records, {1}, [], MOMENT, set(), set())[0]
    assert (group["status"], group["active"]) == ("investigating", True)


def groups_of(records: list[dict], open_seqs: set[int] = frozenset(), closed: set[str] = frozenset(), resolved: set[int] = frozenset()) -> dict[str, dict]:
    return {group["key"]: group for group in overview.incident_groups(records, set(open_seqs), [], MOMENT, set(closed), set(resolved))}


def test_one_fix_live_naming_two_incidents_settles_both():
    records = [
        record(1, "mechanism", "node-id-prefix-2036-fix", "MECHANISM raw UUID", topic="node-id-prefix-2036"),
        record(2, "mechanism", "env-name-len-2125-fix", "MECHANISM >20 chars", topic="env-name-len-2125"),
        record(3, "fix-live", "root", "FIX-LIVE node-id-prefix-2036 + env-name-len-2125: #30822 + #30823 live"),
    ]
    groups = groups_of(records)
    assert {key: (group["status"], group["active"]) for key, group in groups.items()} == {"node-id-prefix-2036": ("fix live", False), "env-name-len-2125": ("fix live", False)}


def test_not_ours_and_duplicate_settle_the_incident_they_name():
    groups = groups_of([record(1, "mechanism", "chime-disputes-1415-fix", "MECHANISM", topic="chime-disputes-1415"), record(2, "not-ours", "chime-disputes-1415-fix", "NOT-OURS customer side", topic="chime-disputes-1415")])
    assert (groups["chime-disputes-1415"]["status"], groups["chime-disputes-1415"]["active"]) == ("not ours", False)


def test_a_sighting_with_a_clock_range_joins_the_incident_named_for_its_start():
    groups = groups_of([record(1, "incident", "incident-slack-watch-35", "INCIDENT 4:09-4:19 PM #alerts-api GraphQLError functionType"), record(2, "mechanism", "function-type-1609-fix", "MECHANISM caller errors")], {1})
    assert sorted(groups) == ["function-type-1609"]


def test_done_settles_only_the_incident_it_points_at():
    opener = record(1, "mechanism", "cf-cert-limit-1336-fix", "MECHANISM 1445", topic="cf-cert-limit-1336")
    evidence_done = record(2, "done", "cf-cert-limit-1336-evidence", "Read-only evidence complete", topic="cf-cert-limit-1336")
    assert groups_of([opener, evidence_done])["cf-cert-limit-1336"]["status"] == "mechanism found"
    assert groups_of([opener, record(3, "done", "root", "closed", re=1)])["cf-cert-limit-1336"]["status"] == "done"


def test_a_closer_naming_a_longer_slug_leaves_the_shorter_incident_open():
    groups = groups_of([record(1, "mechanism", "api-down-1609-fix", "MECHANISM", topic="api-down-1609"), record(2, "not-ours", "root", "NOT-OURS api-down-1609-extra is customer-side")])
    assert groups["api-down-1609"]["status"] == "mechanism found"


def test_a_resolves_record_or_a_curator_record_resolves_the_group():
    opener = record(1, "mechanism", "gha-runner-pickup", "MECHANISM: GitHub incident, not ours", topic="gha-runner-pickup")
    assert groups_of([opener], resolved={1})["gha-runner-pickup"]["status"] == "resolved"
    group = groups_of([opener], closed={"incident:gha-runner-pickup"})["gha-runner-pickup"]
    assert (group["status"], group["active"]) == ("resolved", False)


def test_curated_reads_resolved_topics():
    assert overview.curated([record(1, "done", "dashboard-curator", "x", topic="resolved:task:657"), record(2, "done", "a", "x", topic="x")]) == {"task:657"}


def test_a_hold_closes_when_a_lift_names_it_or_root_lifts_its_incident():
    hold = record(10, "hold", "root", "FENCE api (incident wf-not-found-2322): exclude api applies")
    by_seq = record(11, "lift", "landing-desk", "LIFT #10: the plan read 0 deletes")
    by_slug = record(12, "go", "root", "ROOT 11:34 PM: LIFT api fence (wf-not-found-2322): resume api applies")
    assert overview.standing_holds([hold], [by_seq]) == []
    assert overview.standing_holds([hold], [by_slug]) == []


@pytest.mark.parametrize(
    "later",
    [
        record(13, "lift", "some-lane", "LIFT unrelated hold, see wf-not-found-2322"),
        record(14, "go", "root", "wf-not-found-2322 still fenced; LIFT #10 after the fix lands"),
        record(15, "decision", "root", "Do not LIFT #10 until health checks pass"),
        record(16, "lift", "root", "walker: HOLD (#10) lifts with that apply"),
        record(9, "lift", "root", "LIFT #10 before it existed"),
    ],
)
def test_a_hold_stays_on_a_conditional_negated_or_unrelated_lift(later):
    hold = record(10, "hold", "root", "FENCE api (incident wf-not-found-2322): exclude api applies")
    assert overview.standing_holds([hold], [later]) == [hold]


def board(slug: str, at: str, asks: int = 1, submitted: str = "not submitted") -> dict:
    return {"title": slug, "slug": slug, "status": "open", "at": at, "url": f"/p/{slug}", "cite": f"board:{slug}", "submitted": submitted, "answered": 0, "asks": asks, "closed": False}


def test_only_boards_from_this_drive_that_ask_something_wait_on_the_owner(tmp_path):
    collector = dashboard.Collector({"drive": "d", "state_dir": str(tmp_path), "started_at": "2026-10-01T05:05:38Z"})
    boards = [board("design-round-1", "2026-09-01T00:32:38-07:00"), board("explainer", "2026-10-03T22:44:15-07:00", asks=0), board("pick-a-fix", "2026-10-05T22:00:00-07:00"), board("done", "2026-10-05T22:00:00-07:00", submitted="submitted")]
    assert [row["cite"] for row in collector.owner([], [], [], boards, {}, MOMENT)] == ["board:pick-a-fix"]


def test_an_owner_item_closes_on_a_curator_record_or_the_owners_mark_complete():
    rows = [{"cite": "task:657", "actions": []}, {"cite": "task:161", "actions": [{"action": "complete"}]}, {"cite": "task:656", "actions": [{"action": "question"}]}]
    assert [row["cite"] for row in dashboard.still_open(rows, {"task:657"})] == ["task:656"]


def test_a_ledger_pr_closes_on_a_trunk_squash_or_a_curator_record(tmp_path, monkeypatch):
    rows = [{"key": str(pr), "fields": {"state": "open", "title": f"pr {pr}", "head": "abc"}} for pr in (30001, 30002, 30003)]
    monkeypatch.setattr(dashboard, "run", lambda argv, cwd=None: json.dumps({"rows": rows}) if "ledger" in argv else "api: fix (#30001)\ninfra: other (#29999)\n")
    collector = dashboard.Collector({"drive": "d", "state_dir": str(tmp_path), "checkout": str(tmp_path), "ledger": "L", "repo": "o/r"})
    assert collector.squashed() == {30001, 29999}
    assert [row["pr"] for row in collector.ledger(MOMENT, collector.squashed(), {"pr:30002"})["prs"]] == [30003]


def test_a_rate_limited_builds_fetch_keeps_the_last_rows_and_backs_off(tmp_path, monkeypatch):
    calls = []

    def run(argv, cwd=None):
        calls.append(argv)
        if len(calls) > 1:
            raise subprocess.CalledProcessError(1, argv, "", "Error: error making request: HTTP request failed: 429 429 Too Many Requests (https://api.buildkite.com/...)")
        return json.dumps([{"number": 7, "state": "passed", "created_at": "2026-10-06T06:00:00Z", "message": "release infra, started by ym@poetic.com"}])

    monkeypatch.setattr(dashboard, "run", run)
    collector = dashboard.Collector({"drive": "d", "state_dir": str(tmp_path), "checkout": str(tmp_path)})
    assert [row["number"] for row in collector.release_builds({}, MOMENT)] == [7]
    assert calls[0][:2] == ["bk", "api"] and calls[0][2] == "/pipelines/release/builds?per_page=100&page=1"
    assert [row["number"] for row in collector.release_builds({}, MOMENT + timedelta(seconds=15))] == [7]
    assert len(calls) == 1
    assert [row["number"] for row in collector.release_builds({}, MOMENT + timedelta(minutes=1))] == [7]
    assert (len(calls), collector.builds_due) == (2, MOMENT + timedelta(minutes=1) + dashboard.RATE_LIMIT_BACKOFF)
    assert collector.builds.fetched_at == MOMENT


def test_any_other_builds_failure_still_surfaces(tmp_path, monkeypatch):
    def run(argv, cwd=None):
        raise subprocess.CalledProcessError(1, argv, "", "Error: 401 Unauthorized")

    monkeypatch.setattr(dashboard, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        dashboard.Collector({"drive": "d", "state_dir": str(tmp_path), "checkout": str(tmp_path)}).release_builds({}, MOMENT)


def test_open_items_print_one_line_per_open_item_with_its_cite():
    sources = {
        "owner": [{"cite": "task:657", "kind": "task", "at": "2026-10-05T05:29:42Z", "title": "Owner item: grant\nactions:write"}],
        "incident_groups": [{"cite": "incident:gha-runner-pickup", "status": "mechanism found", "at": "2026-10-05T20:49:34Z", "title": "gha-runner-pickup", "active": True, "records": [{"seq": 26837}]}, {"cite": "incident:old", "active": False}],
        "holds": [{"seq": 27893, "lane": "root", "at": "2026-10-06T06:25:39Z", "text": "FENCE api"}],
        "prs": [{"cite": "pr:30871", "lane": "eh-unbake-app", "at": None, "title": "release: unbake"}],
    }
    assert dashboard.open_items(sources) == [
        "task:657\ttask\t2026-10-05T05:29:42Z\tOwner item: grant actions:write",
        "incident:gha-runner-pickup\tincident mechanism found\t2026-10-05T20:49:34Z\tgha-runner-pickup (cci 26837)",
        "cci:27893\thold by root\t2026-10-06T06:25:39Z\tFENCE api",
        "pr:30871\tpr of eh-unbake-app\t-\trelease: unbake",
    ]


def test_a_lane_named_for_an_incident_still_names_it():
    groups = groups_of([record(1, "incident", "root", "INCIDENT 7:07 PM api/plat apply failed. Fix lane api-plan-constraint-1907-fix (R1144 sol)"), record(2, "mechanism", "api-plan-constraint-1907-fix", "MECHANISM stale saved plan")], {1})
    assert sorted(groups) == ["api-plan-constraint-1907"]
