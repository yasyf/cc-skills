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


def test_a_staging_ask_from_a_lane_with_no_premerge_pr_returns_after_merge_and_creates_no_wait(monkeypatch, tmp_path) -> None:
    from lane import stager, store

    journal = row(53430) | {"dangerous": {"head": "h", "classes": {}, "rule": "r"}, "guarded": {"head": "h", "classes": {}, "rule": "r"}}
    sandsql = row(56597) | {"dangerous": {"head": "h", "classes": {"before merge": ["rust/crates/sandsql-storage/src/lib.rs"]}, "rule": "r"}, "guarded": {"head": "h", "classes": {}, "rule": "r"}}
    lanes = {"run-page-v3": lane(journal) | {"id": "run-page-v3"}, "sandsql-ha-release-3": lane(sandsql) | {"id": "sandsql-ha-release-3"}}
    monkeypatch.setattr(stager, "lanes_dir", lambda drive: tmp_path)
    monkeypatch.setattr(store, "load", lambda drive, lane_id: lanes[lane_id])
    monkeypatch.setattr(rows, "view", lambda drive: {landing.key(pr): {"head": "h", "checks": "pass", "under": None} for pr in (journal, sandsql)})
    monkeypatch.setattr(rows, "rule_key", lambda drive: "r")
    monkeypatch.setattr(rows, "ours", lambda: "lane-bot")
    assert stager.want("durable-agents", "run-page-v3") == stager.AFTER_MERGE
    assert not (tmp_path / stager.WANTS / "run-page-v3.json").exists()
    assert stager.want("durable-agents", "sandsql-ha-release-3").startswith("sandsql-ha-release-3-")


def test_writing_a_staging_target_that_exists_nowhere_is_refused_and_one_that_exists_is_kept(monkeypatch, tmp_path) -> None:
    import pytest

    from drive import providers
    from lane import stager, store

    monkeypatch.setattr(store, "drive_dir", lambda drive: tmp_path)
    monkeypatch.setattr(store, "config", lambda drive: {"checkout": str(tmp_path)})
    monkeypatch.setattr(providers, "at", lambda checkout: type("Found", (), {"sha": "dev"})())
    monkeypatch.setattr(providers, "blob", lambda checkout, sha, path: "sandsql:\n  primaryGeneration: sandsql-nodes-3\n" if path == "infra/envs/plat-usw2-staging.yaml" else None)
    with pytest.raises(store.Refused, match="names no sandsql-nodes-4"):
        stager.targets("durable-agents", ["plat-usw2-staging sandsql-nodes-4"])
    assert stager.targets("durable-agents", ["plat-usw2-staging sandsql-nodes-3"]) == ["plat-usw2-staging sandsql-nodes-3"]
    assert stager.missing("durable-agents", "plat-usw2-staging-2") == "plat-usw2-staging-2 has no infra/envs/plat-usw2-staging-2.yaml on the default branch"


def test_a_head_that_moved_since_its_verdict_under_the_same_danger_page_is_judged_again() -> None:
    moved = row(1, head="old") | {"dangerous": {"head": "old", "classes": {}, "rule": "r"}, "guarded": {"head": "old", "classes": {}, "rule": "r"}}
    still = row(2) | {"dangerous": {"head": "h", "classes": {}, "rule": "r"}, "guarded": {"head": "h", "classes": {}, "rule": "r"}}
    lanes = {"run-page": lane(moved, still)}
    read = {landing.key(pr): {"head": "h"} for pr in (moved, still)}
    verdict = {"head": "h", "classes": {}, "rule": "r"}
    judged = {gate: {landing.key(pr): verdict for pr in (moved, still)} for gate in ("dangerous", "guarded")}
    assert rows.written(lanes, read, judged, "r") == 2
    assert moved["dangerous"]["head"] == moved["guarded"]["head"] == "h"


def test_53810s_conflict_holds_it_and_wakes_run_page_v3_once_naming_stepnames_test(monkeypatch, tmp_path) -> None:
    from lane import cli, store, verbs

    conflicting = row(53810) | {"mergeable": "CONFLICTING"}
    assert landing.verdicts(lane(conflicting))[0].why == f"Forge-AI/monorepo#53810 waits: {landing.CONFLICTS}"
    woken, noted = [], []
    monkeypatch.setattr(store, "drive_dir", lambda drive: tmp_path)
    monkeypatch.setattr(verbs, "blocked", lambda drive, lane_id, what, by: woken.append((lane_id, what, by)))
    monkeypatch.setattr(verbs, "handover", lambda drive, lane_id, text, by: noted.append(lane_id))
    v3 = lane(conflicting) | {"id": "run-page-v3"}
    said = "in go/sand/next/internal/runtime-api/runs/stepnames_test.go"
    cli.conflicted("durable-agents", v3, ["Forge-AI/monorepo#53810"], lambda: said)
    cli.conflicted("durable-agents", v3, ["Forge-AI/monorepo#53810"], lambda: said)
    assert [(lane_id, by) for lane_id, _, by in woken] == [("run-page-v3", "lane-land")]
    assert "stepnames_test.go" in woken[0][1] and noted == ["run-page-v3"]


def test_a_staging_cut_wait_names_when_its_cut_starts_and_one_no_ask_names_never_starts(monkeypatch, tmp_path) -> None:
    from lane import stager

    monkeypatch.setattr(stager, "lanes_dir", lambda drive: tmp_path)
    stager.written(tmp_path / stager.WANTS / "sandsql-ha-release-3.json", {"lane": "sandsql-ha-release-3", "cut": "ha-1", "at": "2026-11-16T23:28:50Z"})
    stager.written(tmp_path / stager.WANTS / "blobstore.json", {"lane": "blobstore", "cut": "blob-1", "at": "2026-11-16T23:24:41Z", "claimed": "2026-11-17T01:05:13Z"})
    stager.written(tmp_path / stager.CUTS / "done-1.json", {"lane": "run-page-v3", "result": "after_merge"})
    assert stager.starts("durable-agents", "ha-1") == "next"
    assert stager.starts("durable-agents", "blob-1") == "2026-11-17T01:05:13Z"
    assert stager.starts("durable-agents", "done-1") == "written"
    assert stager.starts("durable-agents", "sandsql-ha-release-3-20261116232850") is None
