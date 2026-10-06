from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime

import pytest
from lrdash import overview, platy
from test_dashboard import dashboard

MOMENT = datetime(2026, 10, 6, 6, 55, tzinfo=UTC)


def row(number: int, message: str, state: str = "passed", branch: str = "dev", at: str = "2026-10-06T06:00:00Z", finished: str | None = "2026-10-06T06:10:00Z", env: dict | None = None) -> dict:
    return platy.build_row({"number": number, "state": state, "branch": branch, "commit": f"{number:012d}", "created_at": at, "started_at": at, "finished_at": finished, "message": message, "web_url": f"https://bk/{number}", "env": env or {}})


def record(seq: int, kind: str, lane: str, text: str, at: str = "2026-10-06T06:00:00Z", topic: str | None = None) -> dict:
    return {"seq": seq, "kind": kind, "lane": lane, "text": text, "at": at, "topic": topic, "refs": {}}


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
    groups = {group["key"]: group for group in overview.incident_groups(records, {1, 2, 7}, [{"slug": "wf-not-found-2322", "path": "/x", "text": "brief.md"}], MOMENT)}
    assert sorted(groups) == ["eh-delos-2242", "seq:2", "wf-not-found-2322"]
    assert ([r["seq"] for r in groups["eh-delos-2242"]["records"]], groups["eh-delos-2242"]["status"], groups["eh-delos-2242"]["active"]) == ([5, 4, 3, 1], "fix live", False)
    assert (groups["seq:2"]["status"], groups["seq:2"]["title"], groups["seq:2"]["active"]) == ("recovered", "Escape-hatch site unavailable (apollo-demo), 10:44 PM PT", False)
    assert (groups["wf-not-found-2322"]["status"], groups["wf-not-found-2322"]["active"], groups["wf-not-found-2322"]["files"]) == ("mechanism found", True, "brief.md")


def test_an_unanswered_report_older_than_a_day_is_quiet_not_open():
    group = overview.incident_groups([record(1, "incident", "root", "INCIDENT 1:36 PM cert cap", at="2026-10-04T20:00:00Z")], {1}, [], MOMENT)[0]
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
    group = overview.incident_groups(records, {1}, [], MOMENT)[0]
    assert (group["status"], group["active"]) == ("investigating", True)
