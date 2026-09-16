from __future__ import annotations

from datetime import datetime, timedelta, timezone

import desk
from conftest import FakeShell, LEDGER

DESK = "d" * 40
PR = "21221"
HEAD = "3f3acff97aa11bb22cc33dd44ee55ff667788990"
OLD_HEAD = "0" * 40
SQUASH = "9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b"
REPO = "Forge-AI/monorepo"
LANE = "lightning-eh"


def stamp(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(shell, *argv) -> int:
    return desk.main(list(argv), shell)


def desk_shell(**pull) -> FakeShell:
    shell = FakeShell()
    shell.stores[DESK] = {"id": DESK, "title": "desk", "columns": [], "rows": []}
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
    return run(shell, "report", "--desk", DESK, "--ledger", LEDGER, "--pr", PR, "--head", head, "--lane", lane, "--verdict", verdict)


def label(shell, *extra) -> int:
    return run(shell, "label", "--desk", DESK, "--ledger", LEDGER, "--repo", REPO, "--pr", PR, *extra)


def desk_keys(shell) -> list[str]:
    return shell.keys(DESK)


def test_report_enqueues_once_and_opens_the_pr_row():
    shell = desk_shell()
    assert report(shell) == 0
    assert report(shell) == 0

    assert desk_keys(shell) == ["msg/000001"]
    row = shell.fields(PR)
    assert row["lane"] == LANE
    assert row["reported_head"] == HEAD
    assert row["reported_verdict"] == "clean"


def test_a_new_head_from_the_same_lane_is_a_new_message():
    shell = desk_shell()
    report(shell)
    report(shell, verdict="red", head=OLD_HEAD)

    assert desk_keys(shell) == ["msg/000001", "msg/000002"]


def test_inbox_orders_p0_before_rulings_before_reports_before_idles(capsys):
    shell = desk_shell()
    run(shell, "enqueue", "--desk", DESK, "--kind", "idle", "--pr", PR, "--head", HEAD, "--lane", LANE, "--text", "still here")
    report(shell)
    run(shell, "ruling", "--desk", DESK, "--lane", LANE, "--pr", PR, "--text", "land or close", "--options", "A land|B close")
    run(shell, "enqueue", "--desk", DESK, "--kind", "p0", "--pr", PR, "--head", HEAD, "--lane", LANE, "--text", "dev is red")
    capsys.readouterr()

    run(shell, "inbox", "--desk", DESK, "--take")
    lines = capsys.readouterr().out.splitlines()

    assert [line.split()[1] if line.startswith("msg/") else "RULING" for line in lines] == ["p0", "RULING", "report", "idle"]
    assert lines[1] == "RULING NEEDED: land or close; options: A land / B close"
    assert all(shell.fields(key, DESK)["state"] == "acked" for key in desk_keys(shell))


def test_duplicate_idle_notice_is_recorded_once_and_answered_never(capsys):
    shell = desk_shell()
    argv = ["enqueue", "--desk", DESK, "--kind", "idle", "--pr", PR, "--head", HEAD, "--lane", LANE, "--text", "done"]
    run(shell, *argv)
    run(shell, *argv)

    assert "duplicate of msg/000001; nothing recorded, nothing to answer" in capsys.readouterr().out
    assert desk_keys(shell) == ["msg/000001"]


def test_hold_carries_reason_and_expiry_into_both_ledgers(capsys):
    shell = desk_shell()
    run(shell, "hold", "--desk", DESK, "--ledger", LEDGER, "--pr", PR, "--reason", "owner applies first", "--until", "2020-01-01T00:00:00Z")

    hold = shell.fields(f"hold/{PR}", DESK)
    assert hold["state"] == "active"
    assert shell.fields(PR)["hold_reason"] == "owner applies first"
    assert shell.fields(PR)["hold_since"] == hold["since"]
    capsys.readouterr()

    run(shell, "holds", "--desk", DESK)
    assert capsys.readouterr().out.strip() == f"held #{PR}: owner applies first until 2020-01-01T00:00:00Z EXPIRED"

    run(shell, "lift", "--desk", DESK, "--ledger", LEDGER, "--pr", PR)
    assert shell.fields(f"hold/{PR}", DESK)["state"] == "lifted"
    assert shell.fields(PR)["hold_reason"] == ""
    assert shell.fields(PR)["hold_since"] == ""


def test_route_prints_the_lane_message_once_per_head_and_job(capsys):
    shell = desk_shell()
    report(shell)
    capsys.readouterr()

    run(shell, "route", "--desk", DESK, "--ledger", LEDGER, "--pr", PR, "--head", HEAD, "--job", "buildkite/test: Test infra")
    first = capsys.readouterr().out
    run(shell, "route", "--desk", DESK, "--ledger", LEDGER, "--pr", PR, "--head", HEAD, "--job", "buildkite/test: Test infra")
    second = capsys.readouterr().out

    assert first.startswith(f"to {LANE}:\nDESK #{PR} {HEAD[:9]}: buildkite/test: Test infra\n")
    assert "3-line report" in first
    assert second.startswith("already routed to lightning-eh at ")
    assert [key for key in desk_keys(shell) if key.startswith("route/")] == [f"route/{PR}/{HEAD}/buildkite-test-test-infra"]


def test_route_takes_head_and_lane_from_the_pr_row():
    shell = desk_shell()
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": "p2-edge-rows"}})

    run(shell, "route", "--desk", DESK, "--ledger", LEDGER, "--pr", PR, "--job", "rebase onto dev")

    assert shell.fields(f"route/{PR}/{HEAD}/rebase-onto-dev", DESK)["lane"] == "p2-edge-rows"


def test_route_falls_back_to_the_reported_head_before_the_first_refresh():
    shell = desk_shell()
    report(shell, verdict="red")

    run(shell, "route", "--desk", DESK, "--ledger", LEDGER, "--pr", PR, "--job", "buildkite/test: Test infra")

    assert f"route/{PR}/{HEAD}/buildkite-test-test-infra" in desk_keys(shell)


def test_label_re_reads_the_head_and_posts_the_label_once():
    shell = desk_shell()
    assert label(shell) == 0

    assert shell.labelled == [f"{PR}:merge"]
    assert f"repos/{REPO}/pulls/{PR}" in shell.endpoints()
    assert f"repos/{REPO}/commits/{HEAD}/status" in shell.endpoints()
    record = shell.fields(f"label/{PR}/{HEAD}", DESK)
    assert record["base"] == "dev"
    assert shell.fields(PR)["last_label_head"] == HEAD

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
    run(shell, "hold", "--desk", DESK, "--ledger", LEDGER, "--pr", PR, "--reason", "grader pending", "--hours", "2")

    assert label(shell) == 1
    assert "REFUSED #21221 is held: grader pending until" in capsys.readouterr().out


def test_label_refuses_a_red_status_or_failed_check(capsys):
    shell = desk_shell()
    shell.routes[f"status:{HEAD}"] = "status-failure.json"
    assert label(shell) == 1
    assert "REFUSED commit status failure" in capsys.readouterr().out

    shell = desk_shell()
    shell.routes[f"checks:{HEAD}"] = "check-runs.json"
    assert label(shell) == 1
    assert "REFUSED failed check runs on 3f3acff97: buildkite/test/gate-sand-build-timing" in capsys.readouterr().out


def test_label_refuses_a_closed_pr_because_landing_is_read_from_the_base_log(capsys):
    shell = desk_shell(state="closed")

    assert label(shell) == 1
    assert "REFUSED #21221 is closed; a landing is read from the dev log" in capsys.readouterr().out


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
    assert desk_keys(shell) == []


def test_unlabel_records_the_pull_and_blocks_a_relabel_of_the_same_head(capsys):
    shell = desk_shell()
    label(shell)
    run(shell, "unlabel", "--desk", DESK, "--repo", REPO, "--pr", PR, "--reason", "owner reversed the ruling")

    assert shell.unlabelled == [f"{PR}:merge"]
    record = shell.fields(f"label/{PR}/{HEAD}", DESK)
    assert record["pull_reason"] == "owner reversed the ruling"
    assert "a pull is not a stop" in capsys.readouterr().out

    assert label(shell) == 1
    assert "the same head is never re-queued" in capsys.readouterr().out


def test_landed_is_read_from_the_base_log_never_from_merged(capsys, tmp_path):
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})
    shell.base_log = [("f" * 40, "infra: later change (#21230)"), (SQUASH, f"lightning: bake policy (#{PR})")]
    shell.commit_dates[SQUASH] = "2026-09-16T08:00:00+00:00"

    assert run(shell, "landed", "--desk", DESK, "--ledger", LEDGER, "--repo", REPO, "--checkout", str(tmp_path)) == 0

    row = shell.fields(PR)
    assert row["state"] == "landed"
    assert row["landed_sha"] == SQUASH
    assert row["landed_at"] == "2026-09-16T08:00:00Z"
    assert row["lane"] == LANE
    assert desk_keys(shell) == []
    assert f"landed #{PR} as {SQUASH[:9]} on dev" in capsys.readouterr().out
    assert not [argv for argv in shell.calls if "graphql" in " ".join(argv)]


def test_a_body_mention_of_the_number_is_not_a_landing(tmp_path):
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})
    shell.base_log = [("f" * 40, f"infra: mentions (#{PR}) in passing (#21230)")]

    run(shell, "landed", "--desk", DESK, "--ledger", LEDGER, "--repo", REPO, "--checkout", str(tmp_path))

    assert shell.fields(PR)["state"] == "closed-without-squash"
    assert "landed_sha" not in shell.fields(PR)


def test_landed_leaves_open_prs_alone_and_never_rereads_a_landed_row(tmp_path):
    shell = desk_shell()
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})
    shell.stores[LEDGER]["rows"].append({"key": "21137", "fields": {"state": "landed", "landed_sha": SQUASH, "landed_at": "2026-09-16T04:45:34Z"}})

    run(shell, "landed", "--desk", DESK, "--ledger", LEDGER, "--repo", REPO, "--checkout", str(tmp_path))

    assert shell.fields(PR).get("state") is None
    assert not [argv for argv in shell.calls if argv[0] == "git"]
    assert f"repos/{REPO}/pulls/21137" not in shell.endpoints()


def test_summary_counts_landings_in_the_window_and_fits_ten_lines(capsys):
    shell = desk_shell()
    for pr in range(21200, 21230):
        shell.stores[LEDGER]["rows"].append({"key": str(pr), "fields": {"head": HEAD, "labels": "merge" if pr % 2 else ""}})
        shell.stores[DESK]["rows"].append({"key": f"hold/{pr}", "fields": {"reason": f"reason {pr}", "until": stamp(timedelta(hours=1)), "state": "active"}})
    shell.stores[LEDGER]["rows"] += [
        {"key": "21100", "fields": {"state": "landed", "labels": "merge", "landed_at": stamp(timedelta(minutes=-30))}},
        {"key": "21101", "fields": {"state": "landed", "landed_at": stamp(timedelta(hours=-3))}},
        {"key": "21102", "fields": {"state": "closed", "labels": "merge"}},
    ]
    shell.stores[DESK]["rows"] += [
        {"key": "msg/000001", "fields": {"kind": "ruling", "pr": "21201", "head": "-", "lane": LANE, "text": "land or close", "options": "A|B", "state": "pending"}},
        {"key": "msg/000002", "fields": {"kind": "p0", "pr": "21202", "head": HEAD, "lane": LANE, "text": "dev red", "state": "pending"}},
        {"key": "msg/000003", "fields": {"kind": "ruling", "pr": "21203", "head": "-", "lane": LANE, "text": "old", "options": "A", "state": "acked"}},
        {"key": f"route/21204/{HEAD}/x", "fields": {"lane": LANE, "job": "x", "sent_at": ""}},
    ]

    run(shell, "summary", "--desk", DESK, "--ledger", LEDGER)
    lines = capsys.readouterr().out.splitlines()

    assert len(lines) == 10
    assert lines[0].startswith("desk ")
    assert "| open 30 | merged/h 1 | labelled 15 | held 30 | rulings 1 | p0 1 | routed 1" in lines[0]
    assert lines[1] == "merged: #21100"
    assert lines[2].startswith("labelled: #21201 #21203")
    assert lines[3] == f"P0 #21202 {LANE}: dev red"
    assert lines[4] == "RULING NEEDED: land or close; options: A / B"
    assert lines[5].startswith("held #21200: reason 21200 until ")
    assert lines[9].endswith("more lines in desk show")


def test_summary_never_counts_an_unlabelled_row_as_labelled(capsys):
    shell = desk_shell()
    report(shell)
    run(shell, "hold", "--desk", DESK, "--ledger", LEDGER, "--pr", "20284", "--reason", "waits on #20314", "--hours", "4")
    capsys.readouterr()

    run(shell, "summary", "--desk", DESK, "--ledger", LEDGER)
    lines = capsys.readouterr().out.splitlines()

    assert "| open 2 | merged/h 0 | labelled 0 | held 1 |" in lines[0]
    assert not any(line.startswith("labelled:") for line in lines)


def test_init_creates_the_desk_ledger_and_prints_its_id(capsys):
    shell = FakeShell()

    assert run(shell, "init", "--title", "desk: civ2") == 0

    ledger = capsys.readouterr().out.strip()
    assert shell.stores[ledger]["title"] == "desk: civ2"
