from datetime import datetime

from lane import landing, rows

NOW = datetime(2026, 11, 16, 15, 0).astimezone()
SAFE: dict = {}
MIGRATION = {"stored data": ["api/migrations/1700_add_runs.sql"]}


def row(number: int, head: str = "h", classes: dict | None = SAFE, under: int | None = None, guarded: dict | None = None, **changes: object) -> dict:
    return {
        "repo": "Forge-AI/monorepo",
        "number": number,
        "head": head,
        "approved_head": head,
        "held": None,
        "checks": "pass",
        "dangerous": None if classes is None else {"head": head, "classes": classes},
        "guarded": None if classes is None else {"head": head, "classes": classes if guarded is None else guarded},
        "under": f"Forge-AI/monorepo#{under}" if under else None,
    } | changes


def lane(*rows: dict) -> dict:
    return {"id": "run-page", "prs": list(rows)}


def test_his_approve_lands_a_green_safe_pr_at_its_head_with_no_staging() -> None:
    assert landing.verdicts(lane(row(1))) == [landing.Verdict(("Forge-AI/monorepo#1",), True, "every open PR of the stack lands")]


def test_an_unread_head_a_head_read_before_a_push_and_no_approve_never_land() -> None:
    unread = row(1, classes=None)
    stale = row(2, head="new") | {"dangerous": {"head": "old", "classes": {}}}
    unapproved = row(3) | {"approved_head": None}
    assert [verdict.lands for stack in (unread, stale, unapproved) for verdict in landing.verdicts(lane(stack))] == [False, False, False]


def test_his_approve_stands_on_a_head_only_our_own_commits_moved_and_a_teammates_commit_needs_his_tap_again() -> None:
    ours = row(3, head="new") | {"approved_head": "old", "foreign": []}
    theirs = row(3, head="new") | {"approved_head": "old", "foreign": ["new by a-teammate"]}
    lost = row(3, head="new") | {"approved_head": "old", "foreign": None}
    assert landing.verdicts(lane(ours))[0].lands is True
    assert landing.verdicts(lane(theirs))[0].why == "Forge-AI/monorepo#3 waits: commits since his Approve need his tap again: new by a-teammate"
    assert landing.verdicts(lane(lost))[0].lands is False


def test_foreign_lists_the_commits_after_his_approved_head_that_another_account_authored() -> None:
    commits = [("a" * 40, "lane-bot"), ("b" * 40, "lane-bot"), ("c" * 40, "teammate"), ("d" * 40, "lane-bot")]
    assert rows.foreign(commits, "a" * 40, "lane-bot", False) == [f"{'c' * 12} by teammate"]
    assert rows.foreign(commits, "c" * 40, "lane-bot", False) == []
    assert rows.foreign(commits, "e" * 40, "lane-bot", False) is None
    assert rows.foreign(commits, "e" * 40, "lane-bot", True) == [f"{'c' * 12} by teammate"]
    assert rows.foreign([("f" * 40, "lane-bot")], "e" * 40, "lane-bot", True) == []


def test_a_dangerous_stack_lands_whole_only_on_a_pass_at_its_top_head() -> None:
    bottom, top = row(1, classes=MIGRATION), row(2, head="t", under=1)
    unproven = landing.verdicts(lane(bottom, top))
    proven = lane(bottom, top | {"staging": {"head": "t", "result": "pass", "cut": "c1", "target": "plat-usw2-staging"}})
    stale_proof = lane(bottom, top | {"staging": {"head": "older", "result": "pass", "cut": "c1", "target": "plat-usw2-staging"}})
    assert [verdict.lands for verdict in unproven] == [False]
    assert landing.verdicts(proven)[0].keys == ("Forge-AI/monorepo#1", "Forge-AI/monorepo#2")
    assert landing.verdicts(stale_proof)[0].lands is False


def test_a_safe_pr_above_an_unproven_dangerous_one_waits_since_the_queue_lands_a_stack_from_its_bottom() -> None:
    [verdict] = landing.verdicts(lane(row(2, under=1), row(1, classes=MIGRATION)))
    assert (verdict.lands, verdict.why.startswith("Forge-AI/monorepo#1 waits on a staging proof")) == (False, True)


def test_a_hold_and_a_red_check_keep_a_pr_and_everything_above_it() -> None:
    [verdict] = landing.verdicts(lane(row(1), row(2, under=1, held="wait for the rework"), row(3, under=2)))
    assert (verdict.keys, verdict.why) == (("Forge-AI/monorepo#1",), "Forge-AI/monorepo#2 waits: he holds it: wait for the rework")
    assert landing.verdicts(lane(row(1, checks="fail")))[0].lands is False


def test_a_pr_sitting_on_a_pr_outside_its_lane_never_lands_since_the_queue_would_land_that_one_too() -> None:
    [verdict] = landing.verdicts(lane(row(57164, under=56866)))
    assert (verdict.lands, verdict.why) == (False, "Forge-AI/monorepo#57164 sits on Forge-AI/monorepo#56866, outside this lane, which the merge queue would land with it")


def test_a_pr_whose_stack_link_is_unread_never_lands() -> None:
    unlinked = {field: value for field, value in row(1).items() if field != "under"}
    assert landing.verdicts(lane(unlinked))[0].lands is False


def test_a_runtime_api_change_merges_on_his_approve_with_no_staging_pass_whether_or_not_he_is_around() -> None:
    api = row(57676, guarded={"runtime-api": ["go/runtime-api/handler.go"]})
    assert landing.verdicts(lane(api))[0].lands is True


def test_a_head_the_presence_classes_have_not_read_never_lands() -> None:
    assert landing.verdicts(lane(row(1) | {"guarded": None}))[0].lands is False


def test_a_verdict_judged_under_an_older_danger_page_is_dropped_so_the_head_holds_until_judged_again(monkeypatch) -> None:
    monkeypatch.setattr(rows, "ours", lambda: "lane-bot")
    current = row(1) | {"dangerous": {"head": "h", "classes": {}, "rule": "new"}, "guarded": {"head": "h", "classes": {}, "rule": "new"}}
    older = row(2) | {"dangerous": {"head": "h", "classes": {}, "rule": "old"}, "guarded": {"head": "h", "classes": {}, "rule": "old"}}
    read = {landing.key(pr): {"head": "h", "checks": "pass", "under": None} for pr in (current, older)}
    seen = rows.seen(lane(current, older), read, "new")
    assert [verdict.lands for verdict in landing.verdicts(seen)] == [True, False]
    assert landing.verdicts(seen)[1].why == "Forge-AI/monorepo#2 waits: the dangerous reader has not judged its head"


def test_a_tooling_pr_lands_on_green_with_no_approve_of_his_and_a_red_one_waits() -> None:
    tool = row(57908, approved_head=None) | {"guarded": {"head": "h", "classes": {}, "tooling": True}}
    assert landing.verdicts(lane(tool))[0].lands is True
    assert landing.verdicts(lane(tool | {"checks": "fail"}))[0].why == "Forge-AI/monorepo#57908 waits: checks fail"
    assert landing.verdicts(lane(row(1, approved_head=None)))[0].why == "Forge-AI/monorepo#1 waits: no Approve of his"
