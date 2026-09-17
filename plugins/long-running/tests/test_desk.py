from __future__ import annotations

from datetime import datetime, timedelta, timezone

import ledger
from conftest import FakeShell, LEDGER

PR = "21221"
HEAD = "3f3acff97aa11bb22cc33dd44ee55ff667788990"
OLD_HEAD = "0" * 40
SQUASH = "9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b"
REPO = "Forge-AI/monorepo"
LANE = "lightning-eh"


def stamp(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(shell, *argv) -> int:
    return ledger.main(list(argv), shell)


def desk_shell(**pull) -> FakeShell:
    shell = FakeShell()
    shell.pulls[PR] = {
        "number": int(PR),
        "state": "open",
        "head": {"sha": HEAD, "ref": "lightning/bake-policy"},
        "base": {"ref": "dev"},
        "mergeable_state": "clean",
        "merged": False,
        "merged_at": None,
        **pull,
    }
    shell.commit_dates[HEAD] = stamp(timedelta(minutes=-5))
    shell.pull_heads[PR] = HEAD
    shell.routes[f"checks:{HEAD}"] = "check-runs-no-ai-review.json"
    return shell


def report(shell, verdict="clean", head=HEAD, lane=LANE) -> int:
    return run(shell, "report", "--ledger", LEDGER, "--pr", PR, "--head", head, "--lane", lane, "--verdict", verdict)


def label(shell, *extra) -> int:
    return run(shell, "label", "--repo", REPO, "--ledger", LEDGER, "--pr", PR, *extra)


def hold(shell, pr=PR, *extra) -> int:
    return run(shell, "hold", "--ledger", LEDGER, "--pr", pr, "--reason", "owner applies first", *extra)


def messages(shell) -> list[str]:
    return [key for key in shell.keys() if key.startswith("msg/")]


def test_report_enqueues_once_and_opens_the_pr_row():
    shell = desk_shell()
    assert report(shell) == 0
    assert report(shell) == 0

    assert messages(shell) == ["msg/000001"]
    row = shell.fields(PR)
    assert row["lane"] == LANE
    assert row["reported_head"] == HEAD
    assert row["reported_verdict"] == "clean"


def test_a_new_head_from_the_same_lane_is_a_new_message():
    shell = desk_shell()
    report(shell)
    report(shell, verdict="red", head=OLD_HEAD)

    assert messages(shell) == ["msg/000001", "msg/000002"]


def test_inbox_orders_p0_before_rulings_before_reports_before_idles(capsys):
    shell = desk_shell()
    run(shell, "enqueue", "--ledger", LEDGER, "--kind", "idle", "--pr", PR, "--head", HEAD, "--lane", LANE, "--text", "still here")
    report(shell)
    run(shell, "ruling", "--ledger", LEDGER, "--lane", LANE, "--pr", PR, "--text", "land or close", "--options", "A land|B close")
    run(shell, "enqueue", "--ledger", LEDGER, "--kind", "p0", "--pr", PR, "--head", HEAD, "--lane", LANE, "--text", "dev is red")
    capsys.readouterr()

    run(shell, "inbox", "--ledger", LEDGER, "--take")
    lines = capsys.readouterr().out.splitlines()

    assert [line.split()[1] if line.startswith("msg/") else "RULING" for line in lines] == ["p0", "RULING", "report", "idle"]
    assert lines[1] == "RULING NEEDED: land or close; options: A land / B close"
    assert all(shell.fields(key)["state"] == "acked" for key in messages(shell))

    run(shell, "inbox", "--ledger", LEDGER)
    assert capsys.readouterr().out == ""
    run(shell, "inbox", "--ledger", LEDGER, "--all")
    assert len(capsys.readouterr().out.splitlines()) == 4


def test_duplicate_idle_notice_is_recorded_once_and_answered_never(capsys):
    shell = desk_shell()
    argv = ["enqueue", "--ledger", LEDGER, "--kind", "idle", "--pr", PR, "--head", HEAD, "--lane", LANE, "--text", "done"]
    run(shell, *argv)
    run(shell, *argv)

    assert "duplicate of msg/000001; nothing recorded, nothing to answer" in capsys.readouterr().out
    assert messages(shell) == ["msg/000001"]


def test_hold_carries_reason_and_expiry_on_the_row_and_lift_clears_them(capsys):
    shell = desk_shell()
    hold(shell, PR, "--until", "2020-01-01T00:00:00Z")

    row = shell.fields(PR)
    assert row["hold_reason"] == "owner applies first"
    assert row["hold_until"] == "2020-01-01T00:00:00Z"
    assert row["hold_since"]
    capsys.readouterr()

    run(shell, "summary", "--ledger", LEDGER)
    assert f"held #{PR}: owner applies first until 2020-01-01T00:00:00Z EXPIRED" in capsys.readouterr().out

    run(shell, "lift", "--ledger", LEDGER, "--pr", PR)
    assert shell.fields(PR)["hold_reason"] == ""
    assert shell.fields(PR)["hold_since"] == ""
    assert shell.fields(PR)["hold_until"] == ""


def test_label_re_reads_the_head_and_posts_the_label_once():
    shell = desk_shell()
    assert label(shell) == 0

    assert shell.labelled == [f"{PR}:merge"]
    assert f"repos/{REPO}/pulls/{PR}" in shell.endpoints()
    assert f"repos/{REPO}/commits/{HEAD}/status" in shell.endpoints()
    row = shell.fields(PR)
    assert row["label_head"] == HEAD
    assert row["labelled_at"]
    assert row["base"] == "dev"

    assert label(shell) == 1
    assert shell.labelled == [f"{PR}:merge"]


def test_label_refuses_a_head_younger_than_a_minute(capsys):
    shell = desk_shell()
    shell.commit_dates[HEAD] = stamp(timedelta(seconds=-20))

    assert label(shell) == 1
    assert "REFUSED head is 20s old" in capsys.readouterr().out
    assert shell.labelled == []


def test_label_refuses_a_conflicting_or_uncomputed_head(capsys):
    for state in ("dirty", "unknown", "blocked", "unstable"):
        shell = desk_shell(mergeable_state=state)
        assert label(shell) == 1
        assert f"REFUSED mergeable_state {state}" in capsys.readouterr().out
        assert shell.labelled == []


def test_label_refuses_when_the_forge_head_moved(capsys):
    shell = desk_shell()

    assert label(shell, "--expect-head", OLD_HEAD) == 1
    assert "REFUSED head moved" in capsys.readouterr().out


def test_label_refuses_a_held_pr(capsys):
    shell = desk_shell()
    hold(shell, PR, "--hours", "2")

    assert label(shell) == 1
    assert "REFUSED #21221 is held: owner applies first until" in capsys.readouterr().out


def test_label_refuses_a_red_status_or_failed_check(capsys):
    shell = desk_shell()
    shell.routes[f"status:{HEAD}"] = "status-failure.json"
    assert label(shell) == 1
    assert "REFUSED commit status failure" in capsys.readouterr().out

    shell = desk_shell()
    shell.routes[f"checks:{HEAD}"] = "check-runs.json"
    assert label(shell) == 1
    assert "REFUSED failed check runs on 3f3acff97: buildkite/test/gate-sand-build-timing" in capsys.readouterr().out


def test_label_refuses_a_closed_pr_because_landing_is_read_from_the_base_tree(capsys):
    shell = desk_shell(state="closed")

    assert label(shell) == 1
    assert "REFUSED #21221 is closed; a landing is read from the dev tree" in capsys.readouterr().out


def test_label_with_a_checkout_refuses_a_head_that_conflicts_with_the_base(capsys, tmp_path):
    shell = desk_shell()
    shell.conflicts[HEAD] = ["infra/rows/k8s/api.ts"]

    assert label(shell, "--checkout", str(tmp_path)) == 1
    assert "REFUSED 3f3acff97 conflicts with dev on infra/rows/k8s/api.ts" in capsys.readouterr().out
    assert shell.labelled == []


def test_label_with_a_checkout_refuses_when_the_pull_ref_disagrees_with_the_api(capsys, tmp_path):
    shell = desk_shell()
    shell.pull_heads[PR] = OLD_HEAD

    assert label(shell, "--checkout", str(tmp_path)) == 1
    assert f"REFUSED refs/pull/{PR}/head is 000000000 on the forge" in capsys.readouterr().out


def test_label_dry_run_runs_every_guard_and_writes_nothing(capsys, tmp_path):
    shell = desk_shell()

    assert label(shell, "--checkout", str(tmp_path), "--dry-run") == 0
    assert capsys.readouterr().out.strip() == f"would label #{PR} {HEAD}"
    assert shell.labelled == []
    assert shell.keys() == []


def test_unlabel_records_the_pull_and_blocks_a_relabel_of_the_same_head(capsys):
    shell = desk_shell()
    label(shell)
    run(shell, "unlabel", "--repo", REPO, "--ledger", LEDGER, "--pr", PR, "--reason", "owner reversed the ruling")

    assert shell.unlabelled == [f"{PR}:merge"]
    row = shell.fields(PR)
    assert row["label_pull_reason"] == "owner reversed the ruling"
    assert row["label_pulled_at"]
    assert "a pull is not a stop" in capsys.readouterr().out

    assert label(shell) == 1
    assert "the same head is never re-queued" in capsys.readouterr().out


def test_a_new_head_after_a_pull_may_be_labelled(capsys):
    shell = desk_shell()
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"label_head": OLD_HEAD, "labelled_at": "x", "label_pulled_at": "y", "label_pull_reason": "z"}})

    assert label(shell) == 0
    assert shell.labelled == [f"{PR}:merge"]
    assert shell.fields(PR)["label_pulled_at"] == ""


def test_landed_is_read_from_the_base_tree_never_from_merged(capsys, tmp_path):
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})
    shell.pr_files[PR] = ["infra/rows/lightning.ts"]
    shell.delivered[HEAD] = (SQUASH, "2026-09-16T08:00:00+00:00")

    assert run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path)) == 0

    row = shell.fields(PR)
    assert row["state"] == "landed"
    assert row["landed_sha"] == SQUASH
    assert row["landed_at"] == "2026-09-16T08:00:00Z"
    assert row["lane"] == LANE
    assert f"landed #{PR}, payload delivered by {SQUASH[:9]} on dev" in capsys.readouterr().out
    assert not [argv for argv in shell.calls if "graphql" in " ".join(argv)]


def test_a_stacked_child_lands_the_parents_payload_under_another_number(capsys, tmp_path):
    """The parent merges as a no-op, so no commit on the base ever carries its number."""
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})
    shell.pr_files[PR] = ["infra/rows/storage/vpc-flow-logs-bucket.ts"]
    shell.delivered[HEAD] = (SQUASH, "2026-09-17T01:55:07+00:00")

    run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path))

    assert shell.fields(PR)["state"] == "landed"
    assert not [argv for argv in shell.calls if "--grep" in argv], "the base log is never searched by number"


def test_a_diff_against_the_base_is_not_a_landing(tmp_path):
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})
    shell.pr_files[PR] = ["infra/rows/lightning.ts"]

    run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path))

    assert shell.fields(PR)["state"] == "closed-without-squash"
    assert "landed_sha" not in shell.fields(PR)


def test_the_base_moving_on_a_file_after_the_squash_is_still_a_landing(capsys, tmp_path):
    """A squash onto a moved base equals neither side, so content cannot see it."""
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})
    shell.pr_files[PR] = ["infra/ci/src/buildkite-api.ts"]
    shell.closed_by[PR] = "graphite-app[bot]"

    run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path))

    assert shell.fields(PR)["state"] == "landed"
    assert "the queue closed it" in capsys.readouterr().out


def test_an_externally_merged_label_settles_it_too(tmp_path):
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})
    shell.pr_files[PR] = ["infra/ci/src/buildkite-api.ts"]
    shell.pr_labels[PR] = ["externally-merged"]

    run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path))

    assert shell.fields(PR)["state"] == "landed"


def test_a_person_closing_it_is_not_a_landing(tmp_path):
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})
    shell.pr_files[PR] = ["infra/rows/lightning.ts"]
    shell.closed_by[PR] = "yasyf"

    run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path))

    assert shell.fields(PR)["state"] == "closed-without-squash"


def test_a_pr_with_no_files_never_reads_as_landed(tmp_path):
    """An empty file list makes every diff empty, which would land every empty row."""
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})

    run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path))

    assert shell.fields(PR)["state"] == "closed-without-squash"


def test_landed_leaves_open_prs_alone_and_never_rereads_a_landed_row(tmp_path):
    shell = desk_shell()
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})
    shell.stores[LEDGER]["rows"].append({"key": "21137", "fields": {"state": "landed", "landed_sha": SQUASH, "landed_at": "2026-09-16T04:45:34Z"}})
    shell.stores[LEDGER]["rows"].append({"key": "msg/000001", "fields": {"kind": "idle", "pr": PR, "head": HEAD, "lane": LANE, "text": "x", "state": "pending"}})

    run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path))

    assert shell.fields(PR).get("state") is None
    assert not [argv for argv in shell.calls if argv[0] == "git"]
    assert f"repos/{REPO}/pulls/21137" not in shell.endpoints()
    assert not [endpoint for endpoint in shell.endpoints() if "msg" in endpoint]


def test_summary_counts_landings_in_the_window_and_fits_ten_lines(capsys):
    shell = desk_shell()
    for pr in range(21200, 21230):
        shell.stores[LEDGER]["rows"].append(
            {"key": str(pr), "fields": {"head": HEAD, "labels": "merge" if pr % 2 else "", "hold_reason": f"reason {pr}", "hold_since": "x", "hold_until": stamp(timedelta(hours=1))}}
        )
    shell.stores[LEDGER]["rows"] += [
        {"key": "21100", "fields": {"state": "landed", "labels": "merge", "landed_at": stamp(timedelta(minutes=-30))}},
        {"key": "21101", "fields": {"state": "landed", "landed_at": stamp(timedelta(hours=-3))}},
        {"key": "21102", "fields": {"state": "closed-without-squash", "labels": "merge", "hold_until": "2020-01-01T00:00:00Z", "hold_reason": "x"}},
        {"key": "21103", "fields": {"head": HEAD, "routed_head": HEAD, "routed_job": "x"}},
        {"key": "21104", "fields": {"head": HEAD, "routed_head": OLD_HEAD, "routed_job": "x"}},
        {"key": "msg/000001", "fields": {"kind": "ruling", "pr": "21201", "head": "-", "lane": LANE, "text": "land or close", "options": "A|B", "state": "pending"}},
        {"key": "msg/000002", "fields": {"kind": "p0", "pr": "21202", "head": HEAD, "lane": LANE, "text": "dev red", "state": "pending"}},
        {"key": "msg/000003", "fields": {"kind": "ruling", "pr": "21203", "head": "-", "lane": LANE, "text": "old", "options": "A", "state": "acked"}},
    ]

    run(shell, "summary", "--ledger", LEDGER)
    lines = capsys.readouterr().out.splitlines()

    assert len(lines) == 10
    assert lines[0].startswith("desk ")
    assert "| open 32 | merged/h 1 | labelled 15 | held 30 | rulings 1 | p0 1 | routed 1" in lines[0]
    assert lines[1] == "merged: #21100"
    assert lines[2].startswith("labelled: #21201 #21203")
    assert lines[3] == f"P0 #21202 {LANE}: dev red"
    assert lines[4] == "RULING NEEDED: land or close; options: A / B"
    assert lines[5].startswith("held #21200: reason 21200 until ")
    assert lines[9].endswith("more lines in ledger show")


def test_summary_never_counts_an_unlabelled_or_lifted_row_as_labelled_or_held(capsys):
    shell = desk_shell()
    report(shell)
    hold(shell, "20284", "--hours", "4")
    run(shell, "lift", "--ledger", LEDGER, "--pr", "20284")
    capsys.readouterr()

    run(shell, "summary", "--ledger", LEDGER)
    lines = capsys.readouterr().out.splitlines()

    assert "| open 2 | merged/h 0 | labelled 0 | held 0 |" in lines[0]
    assert len(lines) == 1


def test_show_renders_pr_rows_only(capsys):
    shell = desk_shell()
    report(shell)
    capsys.readouterr()

    run(shell, "show", "--ledger", LEDGER)
    printed = capsys.readouterr().out

    assert f"#{PR}" in printed
    assert "msg/" not in printed
    assert HEAD[:9] in printed


def test_init_creates_the_ledger_and_prints_its_id(capsys):
    shell = FakeShell()

    assert run(shell, "init", "--title", "desk: civ2") == 0

    ledger_id = capsys.readouterr().out.strip()
    assert shell.stores[ledger_id]["title"] == "desk: civ2"


def test_reconcile_settles_a_row_nobody_touched(capsys, tmp_path):
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE, "state": "labelled"}})
    shell.pr_files[PR] = ["infra/rows/lightning.ts"]
    shell.delivered[HEAD] = (SQUASH, "2026-09-16T08:00:00+00:00")

    assert run(shell, "reconcile", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path)) == 0

    assert shell.fields(PR)["state"] == "landed"
    assert shell.fields(PR)["landed_sha"] == SQUASH
    assert "reconciled 1 non-terminal rows, 1 moved" in capsys.readouterr().out


def test_reconcile_rereads_no_terminal_row(tmp_path):
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE, "state": "landed"}})

    run(shell, "reconcile", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path))

    assert not [argv for argv in shell.calls if argv[:2] == ["gh", "api"] and f"pulls/{PR}" in " ".join(argv)]


def test_summary_reconciles_first_when_given_a_checkout(capsys, tmp_path):
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE, "state": "labelled"}})
    shell.pr_files[PR] = ["infra/rows/lightning.ts"]
    shell.delivered[HEAD] = (SQUASH, "2026-09-16T08:00:00+00:00")

    assert run(shell, "summary", "--ledger", LEDGER, "--repo", REPO, "--checkout", str(tmp_path)) == 0

    assert shell.fields(PR)["state"] == "landed"
    out = capsys.readouterr().out
    assert out.index(f"landed #{PR},") < out.index("desk 2"), "reconcile must run before the report prints"


def test_summary_without_a_checkout_prints_without_reconciling(tmp_path):
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE, "state": "labelled"}})

    assert run(shell, "summary", "--ledger", LEDGER) == 0

    assert shell.fields(PR)["state"] == "labelled"
