from datetime import datetime

from lane import landing, rows, store

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
        "premerge": False,
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


def test_his_approve_stands_on_a_moved_head_only_once_a_model_judged_the_change_compatible() -> None:
    unjudged = row(3, head="new") | {"approved_head": "old"}
    carried = row(3, head="new") | {"approved_head": "old", "carried": {"head": "new", "compatible": True}}
    stale = row(3, head="newer") | {"approved_head": "old", "carried": {"head": "new", "compatible": True}}
    assert landing.verdicts(lane(unjudged))[0].why == "Forge-AI/monorepo#3 waits: its head moved since his Approve, and a model has not yet judged the change compatible with what he approved"
    assert landing.verdicts(lane(carried))[0].lands is True
    assert landing.verdicts(lane(stale))[0].lands is False


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
    current = row(1) | {"dangerous": {"head": "h", "classes": {}, "rule": "new"}, "guarded": {"head": "h", "classes": {}, "rule": "new"}}
    older = row(2) | {"dangerous": {"head": "h", "classes": {}, "rule": "old"}, "guarded": {"head": "h", "classes": {}, "rule": "old"}}
    read = {landing.key(pr): {"head": "h", "checks": "pass", "under": None} for pr in (current, older)}
    seen = rows.seen(lane(current, older), read, "new", [])
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
    assert stager.want("durable-agents", "run-page-v3") == stager.AFTER_MERGE
    assert not (tmp_path / stager.WANTS / "run-page-v3.json").exists()
    assert stager.want("durable-agents", "sandsql-ha-release-3").startswith("sandsql-ha-release-3-")


def test_writing_a_staging_target_that_exists_nowhere_is_refused_and_one_that_exists_is_kept(monkeypatch, tmp_path) -> None:
    import pytest

    from drive import providers
    from lane import stager, store

    monkeypatch.setattr(store, "drive_dir", lambda drive: tmp_path)
    monkeypatch.setattr(store, "config", lambda drive: {"checkout": str(tmp_path), "staging_env_file": "infra/envs/{env}.yaml"})
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


def test_53810s_conflict_queues_a_rebase_item_and_tells_run_page_v3_once_with_no_blocker(monkeypatch, tmp_path) -> None:
    from lane import cli, store, verbs

    conflicting = row(53810) | {"mergeable": "CONFLICTING"}
    assert landing.verdicts(lane(conflicting))[0].why == f"Forge-AI/monorepo#53810 waits: {landing.CONFLICTS}"
    woken, noted = [], []
    monkeypatch.setattr(store, "drive_dir", lambda drive: tmp_path)
    monkeypatch.setattr(verbs, "blocked", lambda drive, lane_id, what, by: woken.append(lane_id))
    monkeypatch.setattr(verbs, "handover", lambda drive, lane_id, text, by: noted.append((lane_id, text)))
    items = []
    monkeypatch.setattr(cli.page, "view", lambda drive: {"prs": {"Forge-AI/monorepo#53810": {"head": "h", "branch": "j8342", "base": "dev"}}})
    monkeypatch.setattr(cli.work, "add", lambda drive, kind, lane_id, subject, words, head=None, touches=None, by="owner": items.append((kind, lane_id, subject, head, touches, by)))
    v3 = lane(conflicting) | {"id": "run-page-v3"}
    said = "in go/sand/next/internal/runtime-api/runs/stepnames_test.go"
    cli.conflicted("durable-agents", v3, ["Forge-AI/monorepo#53810"], lambda: said)
    cli.conflicted("durable-agents", v3, ["Forge-AI/monorepo#53810"], lambda: said)
    assert woken == [] and len(noted) == 1 and noted[0][0] == "run-page-v3"
    assert "stepnames_test.go" in noted[0][1] and "a rebase item is queued" in noted[0][1] and "Approve" not in noted[0][1]
    assert items[0] == ("rebase", "run-page-v3", "Forge-AI/monorepo#53810", "h", ["Forge-AI/monorepo"], "lane-land")


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


def test_a_stager_is_told_the_staging_alerts_channel_to_read_into_each_stacks_result(monkeypatch, tmp_path) -> None:
    import subprocess

    from lane import session, stager, store

    monkeypatch.setattr(stager, "lanes_dir", lambda drive: tmp_path)
    monkeypatch.setattr(stager, "live", lambda drive: True)
    monkeypatch.setattr(store, "config", lambda drive: store.DEFAULTS | {"checkout": str(tmp_path), "staging_targets": ["plat-usw2-staging sandsql-nodes-3"], "staging_alerts": "#alerts-staging"})
    stager.written(tmp_path / stager.WANTS / "sandsql-ha-release-3.json", {"lane": "sandsql-ha-release-3", "cut": "ha-1", "at": "2026-11-16T23:28:50Z"})
    started = []
    monkeypatch.setattr(session, "argv", lambda config, role, checkout, own, head, tail: ["claude", *head, *tail])
    monkeypatch.setattr(subprocess, "run", lambda argv, **named: started.append(argv) or subprocess.CompletedProcess(argv, 0, "bg", ""))
    stager.start("durable-agents")
    assert "Staging alerts post in #alerts-staging" in str(started)


def test_a_sandsql_lanes_pr_waits_on_a_staging_proof_whatever_the_judge_scored_and_another_lanes_lands(monkeypatch) -> None:
    pr = row(56892) | {"dangerous": {"head": "h", "classes": {}, "rule": "r"}, "guarded": {"head": "h", "classes": {}, "rule": "r"}}
    read = {landing.key(pr): {"head": "h", "checks": "pass", "under": None}}
    sandsql = rows.seen({"id": "sandsql-engine", "prs": [pr]}, read, "r", ["sandsql-engine"])
    other = rows.seen({"id": "run-page", "prs": [pr]}, read, "r", ["sandsql-engine"])
    assert landing.verdicts(sandsql)[0] == landing.Verdict((), False, "Forge-AI/monorepo#56892 waits on a staging proof at h, the stack's top head")
    assert landing.verdicts(other)[0].lands is True


def test_a_pass_takes_a_queued_pr_that_waits_on_a_staging_proof_out_of_the_merge_queue(monkeypatch, tmp_path) -> None:
    import subprocess

    from lane import cli

    ran: list[list[str]] = []
    monkeypatch.setattr(cli, "queue_repo", lambda checkout: "Forge-AI/monorepo")
    monkeypatch.setattr(cli, "queue_cli", lambda drive: ["ci", "trunk"])
    monkeypatch.setattr(cli, "queued", lambda drive, checkout, numbers: {"57423": {"isCurrentlySubmittedToQueue": True}, "57931": {"isCurrentlySubmittedToQueue": False}})
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: ran.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    said = cli.dequeued("durable-agents", tmp_path, ["Forge-AI/monorepo#57423", "Forge-AI/monorepo#57931", "yasyf/litestream#46"])
    assert ran == [["ci", "trunk", "dequeue", "57423"]]
    assert said == ["dequeued Forge-AI/monorepo#57423: it waits on a staging proof"]


def test_a_stack_a_cut_cannot_carry_goes_back_in_line_unclaimed_so_the_next_stager_takes_it_with_no_ask_again(monkeypatch, tmp_path) -> None:
    from lane import stager

    monkeypatch.setattr(stager, "lanes_dir", lambda drive: tmp_path)
    monkeypatch.setattr(stager, "refuse_in_shadow", lambda drive, what: None)
    stager.written(tmp_path / stager.WANTS / "sandsql-engine.json", {"lane": "sandsql-engine", "cut": "engine-1", "at": "2026-11-17T00:12:21Z", "claimed": store.stamp()})
    assert "sandsql-engine" not in stager.unserved("durable-agents")
    stager.defer("durable-agents", "sandsql-engine", "it shares sandsql with the HA cut on nodes-3")
    assert "sandsql-engine" in stager.unserved("durable-agents")
    assert stager.starts("durable-agents", "engine-1") == "next"
    assert not (tmp_path / stager.CUTS / "engine-1.json").exists()


def test_each_pass_files_an_ask_for_a_lane_owing_a_staging_test_closes_one_owing_none_and_waits_out_a_fresh_block(monkeypatch, tmp_path) -> None:
    import json

    from lane import stager

    monkeypatch.setattr(stager, "lanes_dir", lambda drive: tmp_path)
    monkeypatch.setattr(stager, "live", lambda drive: True)
    stager.written(tmp_path / stager.WANTS / "run-page-v3.json", {"lane": "run-page-v3", "cut": "v3-1", "at": "2026-11-17T01:25:03Z", "claimed": "2026-11-17T01:25:03Z"})
    stager.written(tmp_path / stager.CUTS / "blobstore-1.json", {"lane": "blobstore", "cut": "blobstore-1", "result": "blocked", "stacks": {}, "why": "nodes-4 exists nowhere"})
    stager.reconcile("durable-agents", {"sandsql-engine", "blobstore"})
    assert set(stager.wants("durable-agents")) == {"sandsql-engine"}
    assert json.loads((tmp_path / stager.CUTS / "v3-1.json").read_text())["result"] == "after_merge"
    assert stager.starts("durable-agents", stager.wants("durable-agents")["sandsql-engine"]["cut"]) == "next"


def test_a_landing_lock_lives_only_while_held_and_a_second_taker_is_refused_meanwhile(tmp_path) -> None:
    from lane import cli

    path = tmp_path / "landing" / "Forge-AI_monorepo-46.lock"
    with cli.exclusive(path) as mine:
        assert mine and path.exists()
        with cli.exclusive(path) as again:
            assert not again
    assert not path.exists()


def test_a_verdict_under_a_replaced_ruling_is_dropped_from_merged_and_headless_rows_too() -> None:
    merged = row(57314, merged=True) | {"dangerous": {"head": "h", "classes": {"by his 14:33 and 14:55 rulings": ["migration"]}, "rule": "old"}, "guarded": {"head": "h", "classes": {}, "rule": "new"}}
    headless = row(55948, head=None) | {"dangerous": {"head": "x", "classes": {"dangerous_paths": ["infra"]}, "rule": "old"}, "guarded": None}
    lanes = {"releases": {"id": "releases", "prs": [merged, headless]}}
    assert rows.cleared(lanes, "new") == 2
    assert [(pr["dangerous"], pr["guarded"]) for pr in lanes["releases"]["prs"]] == [(None, {"head": "h", "classes": {}, "rule": "new"}), (None, None)]


def test_56842_failed_in_the_merge_queue_at_its_head_is_not_resubmitted_and_its_owner_is_told_once(monkeypatch, tmp_path) -> None:
    import subprocess

    from lane import cli, verbs

    woken, ran = [], []
    monkeypatch.setattr(store, "drive_dir", lambda drive: tmp_path)
    monkeypatch.setattr(cli, "queue_repo", lambda checkout: "Forge-AI/monorepo")
    monkeypatch.setattr(verbs, "blocked", lambda drive, lane_id, what, by: woken.append(what))
    monkeypatch.setattr(verbs, "handover", lambda drive, lane_id, text, by: None)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: ran.append(argv))
    pr = row(56842, head="7659341")
    failed = {"56842": {"sha": "7659341", "isCurrentlySubmittedToQueue": False, "queueEntry": {"state": "failed"}, "htmlUrl": "https://app.trunk.io/poetic/pull/56842"}}
    for _ in range(2):
        said = cli.merged("durable-agents", lane(pr), ["Forge-AI/monorepo#56842"], tmp_path, failed, False)
    assert said == "run-page: the merge queue failed Forge-AI/monorepo#56842 at 7659341; it waits on a new head"
    assert ran == [] and len(woken) == 1 and "https://app.trunk.io/poetic/pull/56842" in woken[0]


def test_a_passed_staging_result_lands_its_stack_in_the_same_call_and_a_failed_one_does_not(monkeypatch, tmp_path) -> None:
    import json

    from lane import cli, stager

    landed = []
    monkeypatch.setattr(stager, "lanes_dir", lambda drive: tmp_path)
    monkeypatch.setattr(stager, "refuse_in_shadow", lambda drive, what: None)
    monkeypatch.setattr(rows, "prove", lambda drive, cut, stacks: None)
    monkeypatch.setattr(stager, "served", lambda drive, stacks: None)
    monkeypatch.setattr(cli, "land_stack", lambda drive, lane_id, key: landed.append((lane_id, key)) or f"{lane_id}: queued {key}")
    stacks = {"Forge-AI/monorepo#57891": {"lane": "sandsql-ha-release-3", "stack": ["Forge-AI/monorepo#57891"], "head": "h", "target": "t", "result": "pass"}, "Forge-AI/monorepo#57944": {"lane": "sandsql-engine", "stack": ["Forge-AI/monorepo#57944"], "head": "h", "target": "t", "result": "fail"}}
    written = json.loads(stager.result("durable-agents", "cut-1", stacks).read_text())
    assert landed == [("sandsql-ha-release-3", "Forge-AI/monorepo#57891")]
    assert written["landed"] == {"Forge-AI/monorepo#57891": "sandsql-ha-release-3: queued Forge-AI/monorepo#57891"} and written["result"] == "fail"


def test_land_stack_submits_the_one_stack_holding_the_pr_and_reads_only_its_lane(monkeypatch, tmp_path) -> None:
    from lane import cli, stager

    asked, submitted = [], []
    pr = row(57908)
    monkeypatch.setattr(store, "load", lambda drive, lane_id: lane(pr))
    monkeypatch.setattr(store, "config", lambda drive: {"premerge_lanes": [], "checkout": str(tmp_path)})
    monkeypatch.setattr(store, "drive_dir", lambda drive: tmp_path)
    monkeypatch.setattr(rows, "view", lambda drive, lane_ids=None: asked.append(lane_ids) or {landing.key(pr): {"head": "h", "checks": "pass", "under": None}})
    monkeypatch.setattr(rows, "rule_key", lambda drive: "r")
    monkeypatch.setattr(rows, "seen", lambda lane, read, rule, premerge: lane)
    monkeypatch.setattr(cli, "queue_repo", lambda checkout: "Forge-AI/monorepo")
    monkeypatch.setattr(cli, "queued", lambda drive, checkout, numbers: {})
    monkeypatch.setattr(cli.fixes, "held_keys", lambda drive, lane: set())
    monkeypatch.setattr(stager, "live", lambda drive: True)
    monkeypatch.setattr(cli, "merged", lambda drive, lane, keys, checkout, held, dry_run: submitted.append((keys, dry_run)) or "queued")
    assert cli.land_stack("durable-agents", "run-page", "Forge-AI/monorepo#57908") == "queued"
    assert asked == [["run-page"]] and submitted == [(["Forge-AI/monorepo#57908"], False)]


def test_an_approved_pr_github_did_not_answer_holds_instead_of_crashing_the_pass_on_a_missing_carry_verdict() -> None:
    unread = row(58054, head=None) | {"approved_head": "ae4c617c7"}
    assert landing.verdicts(lane(unread))[0].lands is False
