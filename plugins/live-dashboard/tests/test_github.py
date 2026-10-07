from __future__ import annotations

import pytest
from conftest import LEDGER, PR_STATUS, fake, fixture
from livedash.components import github


def queue(tmp_path, **params):
    ctx = fake(tmp_path, replies={LEDGER: fixture("ledger-rows.json"), PR_STATUS: fixture("pr-status.json")}, graphql=fixture("graphql-prs.json"))
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


def test_an_agent_posted_review_never_counts_as_the_owner(tmp_path):
    _, table = queue(tmp_path, owner_login="yasyf")
    assert {row["key"]: row["owner"] for row in table.rows} == {"101": "waiting", "102": "waiting", "103": "reviewed"}
    _, table = queue(tmp_path, owner_login="owner-person")
    assert {row["key"]: row["owner"] for row in table.rows}["103"] == "reviewed"


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
