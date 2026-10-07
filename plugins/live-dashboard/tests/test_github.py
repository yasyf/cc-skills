from __future__ import annotations

import json
import re

import pytest
from conftest import LEDGER, PR_STATUS, fake, fixture
from livedash.components import github

ALIAS = re.compile(r"(pr\d+): pullRequest")
OPEN_LIST = "pullRequests(states: OPEN"


def github_api(prs: dict, open_numbers: list[int] | None = None, page: int = 100):
    listed = sorted(open_numbers if open_numbers is not None else [node["number"] for node in prs["repository"].values()])

    def answer(query: str) -> dict:
        if OPEN_LIST in query:
            start = int(query.split('after: "', 1)[1].split('"', 1)[0]) if "after:" in query else 0
            chunk = listed[start : start + page]
            return {"repository": {"pullRequests": {"nodes": [{"number": number} for number in chunk], "pageInfo": {"hasNextPage": start + page < len(listed), "endCursor": str(start + page)}}}}
        return {"repository": {alias: prs["repository"][alias] for alias in ALIAS.findall(query)}}

    return answer


def queue(tmp_path, **params):
    ctx = fake(tmp_path, replies={LEDGER: fixture("ledger-rows.json"), PR_STATUS: fixture("pr-status.json")}, graphql=github_api(fixture("graphql-prs.json")))
    return ctx, github.review_queue(ctx, **{"repo": "o/r", "ledger": "L1"} | params)


def test_the_queue_walks_each_stack_from_its_trunk(tmp_path):
    _, table = queue(tmp_path)
    assert [row["key"] for row in table.rows] == ["103", "101", "102"]
    _, table = queue(tmp_path, parents={"103": "101"})
    assert [row["key"] for row in table.rows] == ["101", "102", "103"]
    _, table = queue(tmp_path, order="age")
    assert [row["key"] for row in table.rows] == ["103", "101", "102"]


def test_each_row_carries_size_checks_reviews_and_holds(tmp_path):
    _, table = queue(tmp_path)
    rows = {row["key"]: row for row in table.rows}
    assert rows["101"].items() >= {"ci": "green", "bot": "green", "rules": "error", "threads": 0, "verdict": "landable", "owner": "waiting", "tone": "warn", "held": "2026-10-06T20:21:59Z"}.items()
    assert rows["102"]["rules"] == "clean" and rows["102"]["threads"] == 1 and rows["102"]["verdict"] == "blocked:ci-pending"
    assert rows["103"].items() >= {"ci": "red", "rules": "none", "owner": "reviewed", "tone": "bad", "held": None, "size": "+2,472/-0"}.items()
    assert table.footer == {"pr": "3 PRs", "size": "+2,515/-7,515", "threads": 2}


def test_the_rules_verdict_reads_the_head_github_reports(tmp_path):
    prs = fixture("graphql-prs.json")
    text = json.dumps(prs).replace("a" * 40, "f" * 40)
    ctx = fake(tmp_path, replies={LEDGER: fixture("ledger-rows.json"), PR_STATUS: fixture("pr-status.json")}, graphql=github_api(json.loads(text)))
    rows = {row["key"]: row for row in github.review_queue(ctx, repo="o/r", ledger="L1").rows}
    assert rows["101"]["rules"] == "none"


def owner_review(state: str, body: str) -> dict:
    return {"author": {"login": "yasyf"}, "state": state, "body": body, "submittedAt": "2026-10-06T19:00:00Z", "commit": {"oid": "a" * 40}}


def test_only_an_owner_approval_or_change_request_counts_as_review(tmp_path):
    prs = fixture("graphql-prs.json")
    prs["repository"]["pr101"]["reviews"]["nodes"] += [owner_review("COMMENTED", "Rules review of `aaaaaaaaa`. Rulings violated: 1."), owner_review("COMMENTED", "")]
    prs["repository"]["pr102"]["reviews"]["nodes"].append(owner_review("CHANGES_REQUESTED", "split the config change out"))
    ctx = fake(tmp_path, replies={LEDGER: fixture("ledger-rows.json"), PR_STATUS: fixture("pr-status.json")}, graphql=github_api(prs))
    table = github.review_queue(ctx, repo="o/r", ledger="L1", owner_login="yasyf")
    assert {row["key"]: row["owner"] for row in table.rows} == {"101": "waiting", "102": "reviewed", "103": "reviewed"}


def test_named_prs_skip_the_ledger(tmp_path):
    ctx, table = queue(tmp_path, prs=[101, 102], ledger=None)
    assert [row["key"] for row in table.rows] == ["101", "102"]
    assert not any(call[:1] == ["ccn"] for call in ctx.calls)
    assert table.rows[0]["lane"] == "yasyf"


def test_an_unknown_order_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="order must be stack or age"):
        queue(tmp_path, order="size")


def test_the_reviewed_action_posts_an_owner_record_to_main(tmp_path):
    ctx = fake(tmp_path, replies={("cci", "post"): {"seq": 77}})
    message = github.record_review(ctx, {"key": "101", "head": "a" * 40}, "looks right")
    assert message == "cci #77: main records owner_reviewed_at on #101"
    assert ctx.calls[-1] == ["cci", "post", "--drive", "test-drive", "--lane", "owner", "--kind", "owner", "--to", "main", "--topic", "101", "--text", f"Reviewed #101 at {'a' * 12}: looks right", "--json"]


def test_quota_reads_bad_under_the_floor(tmp_path):
    tiles = github.quota(fake(tmp_path, replies={("gh", "api", "rate_limit"): fixture("rate-limit.json")}))
    assert [(tile.label, tile.value, tile.tone) for tile in tiles.tiles] == [("GitHub core", 4990, "ok"), ("GitHub graphql", 420, "bad")]


def test_ledger_rows_github_no_longer_lists_as_open_are_dropped_unread(tmp_path):
    ctx = fake(tmp_path, replies={LEDGER: fixture("ledger-rows.json"), PR_STATUS: fixture("pr-status.json")}, graphql=github_api(fixture("graphql-prs.json"), open_numbers=[101, 103, 900]))
    table = github.review_queue(ctx, repo="o/r", ledger="L1")
    assert sorted(row["key"] for row in table.rows) == ["101", "103"]
    assert not any("pr102:" in call[1] for call in ctx.calls if call[0] == "graphql")


def test_open_prs_page_through_github_and_details_come_in_bounded_batches(tmp_path, monkeypatch):
    template = fixture("graphql-prs.json")["repository"]["pr101"]
    many = {"repository": {f"pr{number}": template | {"number": number} for number in range(1, 61)}}
    monkeypatch.setattr(github, "DETAIL_BATCH", 25)
    ctx = fake(tmp_path, replies={PR_STATUS: []}, graphql=github_api(many, page=40))
    table = github.review_queue(ctx, repo="o/r", prs=list(range(1, 61)), order="age")
    queries = [call[1] for call in ctx.calls if call[0] == "graphql"]
    assert [OPEN_LIST in query for query in queries] == [True, True, False, False, False]
    assert [len(ALIAS.findall(query)) for query in queries[2:]] == [25, 25, 10]
    assert len(table.rows) == 60
