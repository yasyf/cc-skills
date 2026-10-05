from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ledger
import pytest
from conftest import FIXTURES, LEDGER, FakeShell

PR = "21221"
HEAD = "3f3acff97aa11bb22cc33dd44ee55ff667788990"
OLD_HEAD = "0" * 40
SQUASH = "9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b"
REPO = "Forge-AI/monorepo"
LANE = "lightning-eh"


@pytest.mark.parametrize("path", ["SKILL.md", "reference/landing-desk-brief.md", "reference/priority-desk-brief.md"])
def test_desk_briefs_wait_in_process_and_monitor_top_level_sessions(path):
    skill = Path(__file__).resolve().parents[1] / "skills/long-running"
    text = (skill / path).read_text()
    assert "desk-wait.sh" in text
    assert "timeout: 60000" in text
    assert "cci watch" in text
    assert "tail -n 0 -F" not in text


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
    shell.pull_heads[PR] = HEAD
    shell.routes[f"checks:{HEAD}"] = "check-runs-green.json"
    shell.reviews[PR] = [review("APPROVED", HEAD)]
    return shell


def review(state: str, commit: str, login: str = "yasyf") -> dict:
    return {"state": state, "commit_id": commit, "user": {"login": login}}


def report(shell, verdict="clean", head=HEAD, lane=LANE) -> int:
    return run(shell, "report", "--ledger", LEDGER, "--pr", PR, "--head", head, "--lane", lane, "--verdict", verdict)


def label(shell, *extra) -> int:
    return run(shell, "label", "--repo", REPO, "--ledger", LEDGER, "--pr", PR, *extra)


def hold(shell, pr=PR, *extra) -> int:
    return run(shell, "hold", "--ledger", LEDGER, "--pr", pr, "--reason", "owner applies first", *extra)


def summarize(shell, capsys, *extra) -> list[str]:
    """Run summary, which always reconciles first, and return its lines from the counts line on."""
    for row in shell.store["rows"]:
        if row["key"].isdigit() and row["fields"].get("state") not in ledger.TERMINAL_STATES:
            shell.pulls.setdefault(row["key"], {"number": int(row["key"]), "state": "open", "head": {"sha": HEAD, "ref": f"b/{row['key']}"}, "base": {"ref": "dev"}})
    capsys.readouterr()
    assert run(shell, "summary", "--repo", REPO, "--ledger", LEDGER, "--checkout", "/nonexistent", *extra) == 0
    lines = capsys.readouterr().out.splitlines()
    return lines[next(index for index, line in enumerate(lines) if line.startswith("desk ")) :]


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


def test_a_new_head_supersedes_the_pending_report_on_the_old_head(capsys):
    shell = desk_shell()
    report(shell, head=OLD_HEAD)
    report(shell)
    capsys.readouterr()

    assert shell.fields("msg/000001")["superseded_by"] == "msg/000002"
    run(shell, "inbox", "--ledger", LEDGER)
    assert capsys.readouterr().out.splitlines() == [f"msg/000002 report #{PR} {HEAD[:9]} {LANE}: clean"]


def test_inbox_takes_reports_on_landed_prs_and_old_heads_without_listing_them(capsys):
    shell = desk_shell()
    rows = shell.stores[LEDGER]["rows"]
    rows.append({"key": "28001", "fields": {"lane": LANE, "state": "landed"}})
    for key, pr, head in (("msg/000001", "28001", HEAD), ("msg/000002", PR, OLD_HEAD), ("msg/000003", PR, HEAD)):
        rows.append({"key": key, "fields": {"kind": "report", "pr": pr, "head": head, "lane": LANE, "text": "clean READY", "state": "pending"}})

    run(shell, "inbox", "--ledger", LEDGER, "--take")

    assert capsys.readouterr().out.splitlines() == [f"msg/000003 report #{PR} {HEAD[:9]} {LANE}: clean READY"]
    assert all(shell.fields(key)["state"] == "acked" for key in messages(shell))
    assert shell.fields("msg/000001")["moot"] == shell.fields("msg/000002")["moot"] == "true"


def test_a_hand_keyed_message_row_does_not_break_the_next_key():
    shell = desk_shell()
    stray = {"kind": "report", "pr": PR, "head": HEAD, "lane": "bg-pulumi", "state": "ready", "text": "READY"}
    shell.stores[LEDGER]["rows"].append({"key": "msg/bg-pulumi-28510-open", "fields": stray, "position": "z"})

    assert run(shell, "enqueue", "--ledger", LEDGER, "--kind", "idle", "--pr", "0", "--head", "-", "--lane", LANE, "--text", "still here") == 0

    assert messages(shell) == ["msg/bg-pulumi-28510-open", "msg/000001"]


def test_duplicate_idle_notice_is_recorded_once_and_answered_never(capsys):
    shell = desk_shell()
    argv = ["enqueue", "--ledger", LEDGER, "--kind", "idle", "--pr", PR, "--head", HEAD, "--lane", LANE, "--text", "done"]
    run(shell, *argv)
    run(shell, *argv)

    assert "duplicate of msg/000001; nothing recorded, nothing to answer" in capsys.readouterr().out
    assert messages(shell) == ["msg/000001"]


def test_a_new_verdict_at_the_same_head_supersedes_the_pending_report(capsys):
    shell = desk_shell()
    argv = ["report", "--ledger", LEDGER, "--pr", PR, "--head", HEAD, "--lane", LANE, "--text", "ci"]
    run(shell, *argv, "--verdict", "red")
    run(shell, *argv, "--verdict", "clean")
    run(shell, *argv, "--verdict", "clean")
    capsys.readouterr()

    assert messages(shell) == ["msg/000001", "msg/000002"]
    assert shell.fields("msg/000001")["state"] == "acked"
    assert shell.fields("msg/000001")["superseded_by"] == "msg/000002"
    assert shell.fields(PR)["reported_verdict"] == "clean"
    run(shell, "inbox", "--ledger", LEDGER)
    assert capsys.readouterr().out.splitlines() == [f"msg/000002 report #{PR} {HEAD[:9]} {LANE}: clean ci"]


def test_a_verdict_reverted_at_the_same_head_is_recorded_again(capsys):
    shell = desk_shell()
    argv = ["report", "--ledger", LEDGER, "--pr", PR, "--head", HEAD, "--lane", LANE, "--text", "ci"]
    for verdict in ("red", "clean", "red"):
        run(shell, *argv, "--verdict", verdict)
    capsys.readouterr()

    run(shell, "inbox", "--ledger", LEDGER)
    assert capsys.readouterr().out.splitlines() == [f"msg/000003 report #{PR} {HEAD[:9]} {LANE}: red ci"]
    assert shell.fields(PR)["reported_verdict"] == "red"


def test_label_with_a_checkout_grades_named_refs_so_concurrent_desks_never_share_fetch_head(tmp_path):
    shell = desk_shell()

    label(shell, "--checkout", str(tmp_path))

    git = [argv for argv in shell.calls if argv[0] == "git"]
    assert not any("FETCH_HEAD" in argv for argv in git)
    assert [argv[-1] for argv in git if argv[3] == "merge-tree"] == [HEAD]
    assert [argv[-2] for argv in git if argv[3] == "merge-tree"] == ["refs/desk/base/dev"]


def test_hold_carries_reason_and_expiry_on_the_row_and_lift_clears_them(capsys):
    shell = desk_shell()
    hold(shell, PR, "--until", "2020-01-01T00:00:00Z")

    row = shell.fields(PR)
    assert row["hold_reason"] == "owner applies first"
    assert row["hold_until"] == "2020-01-01T00:00:00Z"
    assert row["hold_since"]
    capsys.readouterr()

    assert f"held #{PR}: owner applies first until 2020-01-01T00:00:00Z EXPIRED" in summarize(shell, capsys)

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
    assert not [argv for argv in shell.calls if argv[-1] == "--format=%x00%H %cI%n%B"], "the base log is never searched by number"


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
    shell.base_log = [f"{SQUASH} 2026-09-17T03:04:05+02:00\nci: 🐛 retry buildkite reads (#{PR})"]

    run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path))

    assert shell.fields(PR)["state"] == "landed"
    assert shell.fields(PR)["landed_sha"] == SQUASH
    assert shell.fields(PR)["landed_at"] == "2026-09-17T01:04:05Z"
    assert "which has moved on its files since" in capsys.readouterr().out


def test_a_stacked_row_whose_base_the_queue_deleted_settles_by_its_squash_on_the_trunk(capsys, tmp_path):
    parent = "yasyf/eh-v2/dns-into-box"
    shell = desk_shell(state="closed", base={"ref": parent})
    shell.deleted_refs.add(f"refs/heads/{parent}")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE, "base": parent}})
    shell.pr_files[PR] = ["infra/rows/escape-hatch/dns.ts"]
    shell.base_log = [f"{SQUASH} 2026-09-30T05:10:00+00:00\nescape-hatch: ✨ dns into the box (#{PR})"]

    assert run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path)) == 0

    assert shell.fields(PR)["state"] == "landed"
    assert shell.fields(PR)["landed_sha"] == SQUASH
    assert shell.fields(PR)["base"] == "dev"
    assert not [argv for argv in shell.calls if argv[0] == "git" and any(parent in arg for arg in argv)]
    assert f"landed #{PR} as {SQUASH[:9]} on dev" in capsys.readouterr().out


def test_one_row_the_forge_cannot_answer_is_recorded_and_the_pass_settles_the_rest(capsys, tmp_path):
    broken = "21220"
    shell = desk_shell(state="closed")
    shell.pulls[broken] = {**shell.pulls[PR], "number": int(broken)}
    shell.pull_heads[broken] = HEAD
    shell.deleted_refs.add(f"refs/pull/{broken}/head")
    shell.stores[LEDGER]["rows"] += [
        {"key": broken, "fields": {"head": HEAD, "lane": LANE}},
        {"key": PR, "fields": {"head": HEAD, "lane": LANE}},
    ]
    shell.pr_files[broken] = shell.pr_files[PR] = ["infra/rows/lightning.ts"]
    shell.delivered[HEAD] = (SQUASH, "2026-09-16T08:00:00+00:00")

    assert run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path)) == 0

    assert shell.fields(broken).get("state") is None
    assert "couldn't find remote ref" in shell.fields(broken)["settle_error"]
    assert shell.fields(broken)["settle_failed_at"]
    assert shell.fields(PR)["state"] == "landed"
    assert f"#{broken} NOT SETTLED" in capsys.readouterr().out


def test_a_settled_row_clears_its_earlier_failure(tmp_path):
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE, "settle_error": "x", "settle_failed_at": "y"}})
    shell.pr_files[PR] = ["infra/rows/lightning.ts"]
    shell.delivered[HEAD] = (SQUASH, "2026-09-16T08:00:00+00:00")

    run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path))

    assert shell.fields(PR)["state"] == "landed"
    assert shell.fields(PR)["settle_error"] == ""
    assert shell.fields(PR)["settle_failed_at"] == ""


def test_the_queues_bot_closing_a_stacked_child_is_not_a_landing(tmp_path):
    """Deleting a parent's branch closes its child through the same bot, landing nothing."""
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})
    shell.pr_files[PR] = ["infra/rows/ci/refresh-cluster-lock.sh"]
    shell.closed_by[PR] = "graphite-app[bot]"

    run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path))

    assert shell.fields(PR)["state"] == "closed-without-squash"


def test_an_externally_merged_label_settles_nothing(tmp_path):
    """The queue applies it to open pull requests whose content never reached the trunk."""
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})
    shell.pr_files[PR] = ["infra/ci/src/buildkite-api.ts"]
    shell.pr_labels[PR] = ["externally-merged"]

    run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path))

    assert shell.fields(PR)["state"] == "closed-without-squash"


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

    lines = summarize(shell, capsys)

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

    lines = summarize(shell, capsys)

    assert "| open 2 | merged/h 0 | labelled 0 | held 0 |" in lines[0]
    assert lines[1:] == [f"waiting: ungraded #{PR}"]


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
    assert "reconciled 1 non-terminal rows: 0 landed by squash, 1 of 1 closed settled, 0 open" in capsys.readouterr().out


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


def test_summary_refuses_to_print_without_settling_landings_first():
    with pytest.raises(SystemExit):
        run(FakeShell(), "summary", "--ledger", LEDGER)
    with pytest.raises(SystemExit):
        run(FakeShell(), "summary", "--ledger", LEDGER, "--repo", REPO)


def test_label_refuses_a_neutral_ai_review_because_it_is_a_held_blocking_finding(capsys):
    """Neutral is not a failure and is absent from the combined status, so nothing else sees it."""
    shell = desk_shell()
    shell.routes[f"checks:{HEAD}"] = "check-runs-neutral-ai-review.json"

    assert label(shell) == 1
    assert "REFUSED ai-review is neutral" in capsys.readouterr().out


def test_label_refuses_an_absent_ai_review(capsys):
    shell = desk_shell()
    shell.routes[f"checks:{HEAD}"] = "check-runs-no-ai-review.json"

    assert label(shell) == 1
    assert "REFUSED ai-review is absent" in capsys.readouterr().out


def test_label_refuses_a_pr_with_no_reviews(capsys):
    shell = desk_shell()
    shell.reviews[PR] = []

    assert label(shell) == 1
    assert "REFUSED #21221 has no approval in force" in capsys.readouterr().out
    assert shell.labelled == []
    assert shell.keys() == []


def test_label_accepts_an_approval_of_an_earlier_head(capsys):
    shell = desk_shell()
    shell.reviews[PR] = [review("APPROVED", OLD_HEAD)]

    assert label(shell, "--expect-head", HEAD) == 0
    assert shell.labelled == [f"{PR}:merge"]
    assert shell.fields(PR)["approved_by"] == "yasyf"


def test_label_refuses_a_dismissed_approval_of_the_head(capsys):
    shell = desk_shell()
    shell.reviews[PR] = [review("DISMISSED", HEAD)]

    assert label(shell) == 1
    assert "REFUSED #21221 has no approval in force" in capsys.readouterr().out
    assert shell.labelled == []


def test_label_refuses_an_approval_its_reviewer_later_withdrew(capsys):
    shell = desk_shell()
    shell.reviews[PR] = [review("APPROVED", HEAD), review("CHANGES_REQUESTED", HEAD)]

    assert label(shell) == 1
    assert "REFUSED #21221 has no approval in force" in capsys.readouterr().out
    assert shell.labelled == []


def test_label_refuses_while_the_latest_ai_review_run_is_still_reviewing(capsys):
    shell = desk_shell()
    shell.routes[f"checks:{HEAD}"] = "check-runs-ai-review-in-progress.json"

    assert label(shell) == 1
    assert "REFUSED ai-review still reviewing 3f3acff97" in capsys.readouterr().out
    assert shell.labelled == []


def test_label_records_the_approvers_of_the_head_from_every_review_page(capsys):
    shell = desk_shell()
    shell.reviews[PR] = [review("COMMENTED", OLD_HEAD, "bot")] * 100 + [review("APPROVED", HEAD, "yasyf"), review("APPROVED", HEAD, "octocat")]

    assert label(shell) == 0
    assert shell.labelled == [f"{PR}:merge"]
    assert shell.fields(PR)["approved_by"] == "octocat,yasyf"
    assert "approved by octocat,yasyf" in capsys.readouterr().out


def test_label_refuses_a_parent_whose_branch_is_still_a_base(capsys):
    """The forge closes the child when the parent's branch is deleted, and reopen is refused."""
    shell = desk_shell()
    shell.children["lightning/bake-policy"] = [{"number": 21720}]

    assert label(shell) == 1
    out = capsys.readouterr().out
    assert "is the base of #21720" in out
    assert "BEFORE labelling" in out


def test_reconcile_refuses_to_grade_from_a_shallow_clone(capsys, tmp_path):
    """Trunk traversal truncates at a depth that moves with every fetch."""
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})
    shell.pr_files[PR] = ["infra/rows/lightning.ts"]
    shell.shallow = True

    assert run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path)) == 1
    assert "shallow clone" in capsys.readouterr().err
    assert shell.fields(PR).get("state") is None


def test_an_unusable_checkout_grades_nothing_rather_than_failing_every_row(capsys, tmp_path):
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})
    shell.pr_files[PR] = ["infra/rows/lightning.ts"]
    shell.broken_checkout = True

    assert run(shell, "reconcile", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path)) == 1
    assert "not a usable git checkout" in capsys.readouterr().err
    assert "settle_error" not in shell.fields(PR)


def test_a_failed_fetch_grades_nothing_rather_than_grading_the_previous_state(capsys, tmp_path):
    """A concurrent fetch in another worktree loses the ref lock."""
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"head": HEAD, "lane": LANE}})
    shell.pr_files[PR] = ["infra/rows/lightning.ts"]
    shell.fetch_fails = "cannot lock ref 'refs/remotes/origin/dev'"

    assert run(shell, "landed", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path)) == 1
    assert "cannot lock ref" in capsys.readouterr().err
    assert shell.fields(PR).get("state") is None


def reconcile_shell() -> FakeShell:
    """A drive ledger whose rows drifted: two squashed on dev but read open or blank, one closed unsquashed, one still open."""
    shell = FakeShell(rows=json.loads((FIXTURES / "ledger-reconcile.json").read_text()))
    shell.trunk_log = [
        f"{SQUASH} 2026-10-01T05:10:00+00:00 b2: 🚚 move the data stacks (#28100)",
        "0c9e7654c0000000000000000000000000000000 2026-10-01T04:00:00+00:00 escape-hatch: ✨ orca vm bootstrap (#28230)",
        "1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a 2026-10-01T03:00:00+00:00 Revert \"c2: 🔥 cleanup (#28302)\" (#28999)",
        "2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b 2026-10-01T02:00:00+00:00 docs: mention #28349 in passing",
    ]
    for pr, state in (("28302", "closed"), ("28349", "open")):
        shell.pulls[pr] = {"number": int(pr), "state": state, "head": {"sha": f"{pr[-1]}" * 40, "ref": f"b/{pr}"}, "base": {"ref": "dev"}}
        shell.pull_heads[pr] = f"{pr[-1]}" * 40
    shell.pr_files["28302"] = ["infra/rows/c2.ts"]
    return shell


def reconcile(shell, *extra) -> int:
    return run(shell, "reconcile", "--repo", REPO, "--ledger", LEDGER, "--checkout", "/checkout", *extra)


def test_reconcile_lands_every_row_whose_squash_is_on_the_trunk_in_one_batch(capsys):
    shell = reconcile_shell()

    assert reconcile(shell) == 0

    assert {"state": "landed", "landed_sha": SQUASH, "landed_at": "2026-10-01T05:10:00Z", "base": "dev"}.items() <= shell.fields("28100").items()
    assert shell.fields("28230")["state"] == "landed"
    assert shell.fields("28230")["landed_sha"].startswith("0c9e7654c")
    assert shell.fields("28302")["state"] == "closed-without-squash", "a revert naming the PR inside its subject is not its squash"
    assert shell.fields("28349")["state"] == "open"
    assert "landed_at" not in shell.fields("28349"), "a number mentioned mid-subject is not a squash"
    out = capsys.readouterr().out
    assert "reconciled 4 non-terminal rows: 2 landed by squash, 1 of 1 closed settled, 1 open" in out


def test_reconcile_reads_the_trunk_once_and_the_forge_only_for_closed_unsquashed_rows():
    shell = reconcile_shell()

    reconcile(shell)

    logs = [argv for argv in shell.calls if argv[:4] == ["git", "-C", "/checkout", "log"] and argv[-1] == "--format=%H %cI %s"]
    assert len(logs) == 1
    assert [argv[4:] for argv in shell.state_calls()] == [["--repo", REPO, "28302", "28349"]]
    pulled = {argv[2].split("/")[4] for argv in shell.calls if argv[:2] == ["gh", "api"] and "/pulls/" in argv[2]}
    assert pulled == {"28302"}


def closed_board(count: int) -> FakeShell:
    shell = FakeShell(rows=[{"key": str(28400 + index), "fields": {"lane": LANE, "state": "open"}} for index in range(count)])
    for index in range(count):
        pr, head = str(28400 + index), f"{index:040x}"
        shell.pulls[pr] = {"number": int(pr), "state": "closed", "head": {"sha": head, "ref": f"b/{pr}"}, "base": {"ref": "dev"}}
        shell.pull_heads[pr] = head
        shell.pr_files[pr] = [f"infra/rows/{pr}.ts"]
    return shell


def test_reconcile_settles_a_closed_board_in_one_message_read_one_object_check_and_one_write(capsys):
    shell = closed_board(40)
    shell.objects = {pull["head"]["sha"] for pull in shell.pulls.values()}
    shell.delivered["0" * 40] = (SQUASH, "2026-10-01T05:10:00+00:00")
    shell.base_log = [f"{SQUASH} 2026-10-01T05:10:00+00:00\nb2: 🚚 move (#28401)"]

    assert reconcile(shell) == 0

    assert shell.fields("28400")["state"] == "landed"
    assert shell.fields("28401")["state"] == "landed"
    assert {shell.fields(str(pr))["state"] for pr in range(28402, 28440)} == {"closed-without-squash"}
    assert "40 of 40 closed settled" in capsys.readouterr().out
    assert len([argv for argv in shell.calls if argv[-1] == "--format=%x00%H %cI%n%B"]) == 1
    assert len([argv for argv in shell.calls if argv[3:4] == ["cat-file"]]) == 1
    assert not [argv for argv in shell.calls if argv[3:4] == ["fetch"] and "refs/pull/" in argv[-1]]
    assert not [argv for argv in shell.calls if argv[:3] == ["ccn", "ledger", "row"]]
    assert len([argv for argv in shell.calls if argv[:3] == ["ccn", "ledger", "sync"]]) == 1
    assert not [endpoint for endpoint in shell.endpoints() if endpoint.split("?")[0].count("/") == 4 and "/pulls/" in endpoint]


def test_settle_fetches_only_the_heads_the_clone_lacks():
    shell = closed_board(3)
    shell.objects = {shell.pulls["28400"]["head"]["sha"], shell.pulls["28402"]["head"]["sha"]}

    assert reconcile(shell) == 0

    assert [argv[-1] for argv in shell.calls if argv[3:4] == ["fetch"] and "refs/pull/" in argv[-1]] == ["+refs/pull/28401/head:refs/desk/pr28401"]


def test_reconcile_dry_run_writes_nothing(capsys):
    shell = reconcile_shell()
    before = json.dumps(shell.store, sort_keys=True)

    assert reconcile(shell, "--dry-run") == 0

    assert json.dumps(shell.store, sort_keys=True) == before
    assert not [argv for argv in shell.calls if argv[:3] == ["ccn", "ledger", "row"]]
    out = capsys.readouterr().out
    assert f"would land #28100 as {SQUASH[:9]} on dev at 2026-10-01T05:10:00Z" in out
    assert "would settle closed #28302 with no squash on dev" in out


def rate_limited_shell() -> FakeShell:
    shell = reconcile_shell()
    shell.quota_resets_at = 1790000000
    shell.fields("28349")["last_refresh"] = stamp(-timedelta(minutes=12))
    return shell


def test_reconcile_under_an_exhausted_quota_lands_the_squashed_rows_and_names_the_cache(capsys):
    shell = rate_limited_shell()

    assert reconcile(shell) == 0

    assert shell.fields("28100")["state"] == "landed"
    assert shell.fields("28302")["state"] == "open", "a closed row the cache cannot read stays unsettled"
    out = capsys.readouterr().out
    assert "pr states: cached 12m (GraphQL quota exhausted, resets 2026-09-21T14:13:20Z)" in out
    assert "reconciled 4 non-terminal rows: 2 landed by squash, 0 of 0 closed settled, 2 unread" in out
    assert len(shell.state_calls()) == 1, "a rate-limited read is never retried"


def test_summary_under_an_exhausted_quota_renders_from_the_cached_rows(capsys):
    shell = rate_limited_shell()

    assert run(shell, "summary", "--ledger", LEDGER, "--repo", REPO, "--checkout", "/checkout") == 0

    out = capsys.readouterr().out
    assert out.index("pr states: cached 12m (GraphQL quota exhausted") < out.index("desk 2")
    assert "open 2 |" in out


def test_reconcile_under_an_exhausted_quota_with_no_recorded_refresh_says_so(capsys):
    shell = reconcile_shell()
    shell.quota_resets_at = 1790000000

    assert reconcile(shell) == 0

    assert "pr states: cached age unknown (GraphQL quota exhausted" in capsys.readouterr().out


def test_a_pr_state_failure_that_is_not_the_quota_still_refuses(capsys):
    shell = reconcile_shell()
    shell.pr_state_error = "ccx: no such repo"

    assert reconcile(shell) == 1

    assert "forge unreachable, wrote nothing: ccx vcs pr state: ccx: no such repo" in capsys.readouterr().err


def test_reconcile_with_nothing_open_touches_neither_git_nor_the_forge(capsys):
    shell = FakeShell(rows=[{"key": "27887", "fields": {"state": "landed"}}])

    assert reconcile(shell) == 0

    assert [argv[0] for argv in shell.calls] == ["ccn"]
    assert "reconciled 0 non-terminal rows" in capsys.readouterr().out


def test_every_verb_runs_from_any_directory_against_the_named_one(tmp_path, monkeypatch):
    monkeypatch.chdir(Path.home())
    shell = reconcile_shell()

    assert run(shell, "-C", str(tmp_path), "list", "--ledger", LEDGER, "--open") == 0

    assert Path.cwd() == tmp_path.resolve()


def test_list_open_names_only_rows_still_open(capsys):
    shell = reconcile_shell()

    run(shell, "list", "--ledger", LEDGER, "--open")

    assert [line.split()[0] for line in capsys.readouterr().out.splitlines()] == ["#28100", "#28230", "#28302", "#28349"]


def test_report_refuses_a_pr_that_is_not_a_bare_number(capsys):
    shell = desk_shell()

    with pytest.raises(SystemExit):
        run(shell, "report", "--ledger", LEDGER, "--pr", "28096 publish-kinds restacked", "--head", HEAD, "--lane", LANE, "--verdict", "held")

    assert "a bare PR number" in capsys.readouterr().err
    assert shell.keys() == []


def test_report_refuses_a_branch_prefix_as_the_head(capsys):
    shell = desk_shell()

    with pytest.raises(SystemExit):
        run(shell, "report", "--ledger", LEDGER, "--pr", PR, "--head", "yasyf/v3-b6-publish/", "--lane", LANE, "--verdict", "held")

    assert "hex prefix of the head sha" in capsys.readouterr().err


def test_every_reader_skips_malformed_keys_with_one_warning(capsys):
    stray = [
        {"key": "28096 publish-kinds restacked", "fields": {"lane": "b6-publish", "reported_head": "yasyf/v3-b6-publish/"}},
        {"key": "msg/bg-pulumi-28510-open", "fields": {"kind": "report", "pr": "28510", "head": "1d192f7834a4", "lane": "bg-pulumi", "state": "pending", "text": "READY"}},
    ]
    shell = reconcile_shell()
    shell.store["rows"].extend(stray)

    assert run(shell, "report", "--ledger", LEDGER, "--pr", "28349", "--head", "4" * 40, "--lane", "d-cutover", "--verdict", "clean") == 0
    assert run(shell, "list", "--ledger", LEDGER) == 0
    assert run(shell, "inbox", "--ledger", LEDGER) == 0
    assert reconcile(shell) == 0

    captured = capsys.readouterr()
    warnings = [line for line in captured.err.splitlines() if "skipping malformed keys" in line]
    assert len(warnings) == 4, "one line per command, never one per read"
    assert "'28096 publish-kinds restacked', 'msg/bg-pulumi-28510-open'" in warnings[0]
    assert "bg-pulumi" not in captured.out
    assert "publish-kinds" not in captured.out


STACK = ("24001", "24002", "24003")


def stack_shell(tracked=STACK[:2]) -> FakeShell:
    """dev <- 24001 <- 24002 <- 24003, each approved and green, the lower two reported by their lane."""
    shell = FakeShell()
    base = "dev"
    for index, pr in enumerate(STACK):
        head = f"{index + 1}" * 40
        shell.pulls[pr] = {
            "number": int(pr),
            "state": "open",
            "head": {"sha": head, "ref": f"stack/{pr}", "repo": {"full_name": REPO}},
            "base": {"ref": base},
            "mergeable_state": "clean",
        }
        base = f"stack/{pr}"
        shell.pull_heads[pr] = head
        shell.routes[f"checks:{head}"] = "check-runs-green.json"
        shell.reviews[pr] = [review("APPROVED", head)]
        if pr in tracked:
            shell.stores[LEDGER]["rows"].append({"key": pr, "fields": {"lane": LANE, "reported_head": head, "reported_verdict": "clean"}})
    return shell


def label_stack(shell, pr=STACK[-1], *extra) -> int:
    return run(shell, "label", "--repo", REPO, "--ledger", LEDGER, "--pr", pr, *extra)


def test_a_clean_stack_is_enqueued_by_one_label_on_its_tip(capsys):
    shell = stack_shell()

    assert label_stack(shell) == 0

    assert shell.labelled == ["24003:merge"]
    assert "the queue takes #24001 <- #24002 <- #24003 as one entry" in capsys.readouterr().out
    for index, pr in enumerate(STACK):
        row = shell.fields(pr)
        assert row["label_head"] == f"{index + 1}" * 40
        assert row["label_stack"] == "24001,24002,24003"
        assert row["approved_by"] == "yasyf"


STACK_GATE_PENDING = "check-runs-stack-mergeability-in-progress.json"


def settling_stack(**routes) -> FakeShell:
    shell = stack_shell()
    for index, pr in enumerate(STACK[1:], 1):
        shell.pulls[pr]["mergeable_state"] = "unstable"
        shell.routes[f"checks:{str(index + 1) * 40}"] = STACK_GATE_PENDING
    shell.routes.update(routes)
    return shell


def test_a_stack_whose_upper_prs_wait_only_on_graphites_mergeability_check_is_labelled(capsys):
    shell = settling_stack()

    assert label_stack(shell) == 0

    assert shell.labelled == ["24003:merge"]
    assert "the queue takes #24001 <- #24002 <- #24003 as one entry" in capsys.readouterr().out


def test_the_bottom_pr_reading_unstable_is_still_refused(capsys):
    shell = settling_stack()
    shell.pulls["24001"]["mergeable_state"] = "unstable"
    shell.routes[f"checks:{'1' * 40}"] = STACK_GATE_PENDING

    assert label_stack(shell) == 1

    assert "REFUSED mergeable_state unstable" in capsys.readouterr().out
    assert shell.labelled == []


def test_a_stack_pr_reading_unstable_on_a_red_status_is_refused_for_the_status(capsys):
    shell = settling_stack(**{f"status:{'2' * 40}": "status-failure.json"})

    assert label_stack(shell) == 1

    assert f"REFUSED commit status failure on {'2' * 9}; only success is labelled (#24002)" in capsys.readouterr().out
    assert shell.labelled == []


@pytest.mark.parametrize(
    ("mutate", "pending"),
    [
        (lambda runs: runs.append({"name": "buildkite/test", "status": "in_progress", "conclusion": None}), False),
        (lambda runs: runs.append({"name": "buildkite/test", "status": "completed", "conclusion": "skipped"}), True),
        (lambda runs: [run.update(status="in_progress", conclusion=None) for run in runs if run["name"] == "ai-review"], True),
        (lambda runs: [run.update(status="completed", conclusion="success") for run in runs if run["name"] == ledger.STACK_MERGEABILITY_CHECK], False),
    ],
)
def test_only_an_unfinished_mergeability_check_settles_a_non_bottom_unstable_pr(mutate, pending):
    checks = json.loads((FIXTURES / STACK_GATE_PENDING).read_text())
    mutate(checks["check_runs"])

    assert ledger.stack_gate_pending({"mergeable_state": "unstable", "base": {"ref": "stack/1"}}, "dev", checks) is pending


@pytest.mark.parametrize("prefix", ["3", "3f3acf", "3F3ACFF97", "not-a-sha"])
def test_expect_head_rejects_a_prefix_too_short_or_not_hex_to_pin_a_head(prefix):
    with pytest.raises(SystemExit):
        label(desk_shell(), "--expect-head", prefix)


def test_label_accepts_a_short_expect_head_and_refuses_a_short_head_that_moved(capsys):
    shell = desk_shell()
    assert label(shell, "--expect-head", HEAD[:9]) == 0

    shell = desk_shell()
    assert label(shell, "--expect-head", OLD_HEAD[:9]) == 1
    assert f"REFUSED head moved: expected {OLD_HEAD[:9]}, the forge has {HEAD[:9]}" in capsys.readouterr().out


def checkout_with_stack_enqueue(tmp_path):
    script = tmp_path / ledger.STACK_ENQUEUE
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env python3\n")
    return str(tmp_path)


def test_a_checkout_carrying_stack_enqueue_enqueues_the_tip_through_it_and_adds_no_label(capsys, tmp_path):
    shell = stack_shell()
    shell.stack_enqueue_out = "#24001 QUEUED 1111111111 graphite READY\n#24002 QUEUED 2222222222 graphite READY\n#24003 QUEUED 3333333333 graphite READY\n"

    assert label_stack(shell, STACK[-1], "--checkout", checkout_with_stack_enqueue(tmp_path)) == 0

    assert shell.stack_enqueues == [["24003"]]
    assert shell.labelled == []
    out = capsys.readouterr().out
    assert "#24003 QUEUED 3333333333 graphite READY" in out
    assert f"enqueued #24003 {'3' * 40}" in out
    assert "the queue takes #24001 <- #24002 <- #24003 as one entry" in out
    for index, pr in enumerate(STACK):
        row = shell.fields(pr)
        assert row["label_head"] == f"{index + 1}" * 40
        assert row["label_stack"] == "24001,24002,24003"


def test_stack_enqueue_naming_blockers_refuses_the_stack_and_records_them(capsys, tmp_path):
    shell = stack_shell()
    shell.stack_enqueue_exit = 1
    shell.stack_enqueue_out = "#24001 GREEN 1111111111\n#24002 BLOCKED 2222222222 awaiting approval on head from poetic-svc\nenqueued nothing: every PR in the downstack must be GREEN\n"

    assert label_stack(shell, STACK[-1], "--checkout", checkout_with_stack_enqueue(tmp_path)) == 1

    out = capsys.readouterr().out
    assert "REFUSED stack-enqueue refused #24002 (stack tip #24003): awaiting approval on head from poetic-svc (#24002)" in out
    assert "so #24002 refuses all of it" in out
    assert shell.labelled == []
    assert "awaiting approval on head from poetic-svc" in shell.fields("24002")["label_refused"]
    assert "so #24002 refuses all of it" in shell.fields("24001")["label_refused"]
    assert all("label_head" not in shell.fields(pr) for pr in STACK[:2])


def test_a_stack_enqueue_blocker_is_returned_against_the_blocked_prs_own_head(tmp_path):
    shell = stack_shell()
    shell.stack_enqueue_exit = 1
    shell.stack_enqueue_out = "#24002 BLOCKED 2222222222 graphite CONFLICTING\n"
    gh, notes = ledger.Github(shell, REPO), ledger.Notes(shell, LEDGER)

    refused = ledger.label_stack(shell, gh, notes, notes.pr_rows(), "dev", gh.api(f"pulls/{STACK[-1]}"), None, Path(checkout_with_stack_enqueue(tmp_path)), False)

    assert list(refused) == ["24002"]
    assert refused["24002"][0] == "2" * 40


def test_a_stack_enqueue_crash_without_blocker_lines_refuses_the_tip(capsys, tmp_path):
    shell = stack_shell()
    shell.stack_enqueue_exit = 1

    assert label_stack(shell, STACK[-1], "--checkout", checkout_with_stack_enqueue(tmp_path)) == 1

    assert "stack-enqueue refused #24003 (stack tip #24003): exit 1" in capsys.readouterr().out


def test_a_partly_dropped_enqueue_still_counts_as_enqueued_so_the_heads_are_not_queued_twice(capsys, tmp_path):
    shell = stack_shell()
    shell.stack_enqueue_exit = 2
    shell.stack_enqueue_out = "#24003 DROPPED 3333333333 graphite CONFLICT\nthe queue dropped part of the stack; read each PR's Merge activity comment\n"

    assert label_stack(shell, STACK[-1], "--checkout", checkout_with_stack_enqueue(tmp_path)) == 0

    out = capsys.readouterr().out
    assert "the queue dropped part of the stack" in out
    assert f"enqueued #24003 {'3' * 40}" in out
    assert all(shell.fields(pr)["label_head"] for pr in STACK[:2])
    assert ledger.is_label_candidate(shell.fields("24001")) is False


def test_a_refused_guard_never_reaches_stack_enqueue(tmp_path):
    shell = stack_shell()
    shell.routes[f"status:{'2' * 40}"] = "status-failure.json"

    assert label_stack(shell, STACK[-1], "--checkout", checkout_with_stack_enqueue(tmp_path)) == 1

    assert shell.stack_enqueues == []


def test_a_stack_dry_run_gates_through_stack_enqueue_check_and_records_nothing(capsys, tmp_path):
    shell = stack_shell()
    shell.stack_enqueue_out = "would enqueue #24001 #24002 #24003\n"

    assert label_stack(shell, STACK[-1], "--dry-run", "--checkout", checkout_with_stack_enqueue(tmp_path)) == 0

    assert shell.stack_enqueues == [["24003", "--check"]]
    assert capsys.readouterr().out.strip() == "would enqueue #24001 #24002 #24003"
    assert all("label_head" not in shell.fields(pr) for pr in STACK[:2])


def test_a_checkout_without_stack_enqueue_still_labels_the_tip(tmp_path):
    shell = stack_shell()

    assert label_stack(shell, STACK[-1], "--checkout", str(tmp_path)) == 0

    assert shell.labelled == ["24003:merge"]
    assert shell.stack_enqueues == []


def test_one_red_pr_refuses_the_whole_stack(capsys):
    shell = stack_shell()
    shell.routes[f"status:{'2' * 40}"] = "status-failure.json"

    assert label_stack(shell) == 1

    out = capsys.readouterr().out
    assert "REFUSED commit status failure on 222222222; only success is labelled (#24002)" in out
    assert "the stack #24001 <- #24002 <- #24003 enqueues as one entry, so #24002 refuses all of it" in out
    assert shell.labelled == []
    assert all("label_head" not in shell.fields(pr) for pr in STACK[:2])


def test_a_stack_dry_run_names_every_pr_it_would_enqueue(capsys):
    shell = stack_shell()

    assert label_stack(shell, STACK[-1], "--dry-run") == 0
    assert capsys.readouterr().out.strip() == f"would label #24003 {'3' * 40}, enqueuing #24001 <- #24002 <- #24003"
    assert shell.labelled == []


def test_a_downstack_pr_no_lane_reported_refuses_the_stack(capsys):
    shell = stack_shell(tracked=STACK[1:2])

    assert label_stack(shell) == 1
    assert "#24001 is below #24003 in the stack and no lane reported it" in capsys.readouterr().out
    assert shell.labelled == []


def test_a_downstack_pr_whose_head_moved_since_its_report_refuses_the_stack(capsys):
    shell = stack_shell()
    shell.fields("24001")["reported_head"] = OLD_HEAD

    assert label_stack(shell) == 1
    assert "REFUSED head moved: expected 000000000, the forge has 111111111; grade the new head before labelling (#24001)" in capsys.readouterr().out
    assert shell.labelled == []


def test_labelling_mid_stack_refuses_when_the_pr_above_is_untracked(capsys):
    shell = stack_shell()

    assert label_stack(shell, "24002") == 1
    out = capsys.readouterr().out
    assert "#24002's branch stack/24002 is the base of #24003, which no lane tracks in Graphite's stack record" in out
    assert shell.labelled == []


def test_a_green_bottom_prefix_is_labelled_under_a_tracked_graphite_child(capsys):
    shell = stack_shell(tracked=STACK)

    assert label_stack(shell, "24002") == 0

    assert shell.labelled == ["24002:merge"]
    assert "the queue takes #24001 <- #24002 as one entry" in capsys.readouterr().out


def test_a_tracked_child_outside_graphites_stack_record_refuses_the_prefix(capsys):
    shell = stack_shell(tracked=STACK)
    shell.routes[f"checks:{'3' * 40}"] = "check-runs-no-graphite.json"

    assert label_stack(shell, "24002") == 1

    assert "is the base of #24003, which no lane tracks in Graphite's stack record" in capsys.readouterr().out
    assert shell.labelled == []


def test_a_stack_whose_parent_closed_without_landing_is_refused(capsys):
    shell = stack_shell()
    shell.pulls["24001"]["state"] = "closed"

    assert label_stack(shell) == 1
    assert "#24002 is based on stack/24001, which is neither dev nor exactly one open pull request's branch (found none)" in capsys.readouterr().out
    assert shell.labelled == []


def test_a_downstack_row_with_no_reported_head_is_untracked(capsys):
    shell = stack_shell()
    del shell.fields("24001")["reported_head"]

    assert label_stack(shell) == 1
    assert "#24001 is below #24003 in the stack and no lane reported it" in capsys.readouterr().out
    assert shell.labelled == []


def test_two_open_prs_on_the_parent_branch_refuse_the_stack(capsys):
    shell = stack_shell()
    shell.pulls["24004"] = dict(shell.pulls["24001"], number=24004, base={"ref": "dev"})

    assert label_stack(shell) == 1
    assert "found #24001, #24004" in capsys.readouterr().out
    assert shell.labelled == []


def test_a_base_cycle_refuses_instead_of_walking_forever(capsys):
    shell = stack_shell()
    shell.pulls["24001"]["base"] = {"ref": "stack/24003"}

    assert label_stack(shell) == 1
    assert "#24003 is its own ancestor" in capsys.readouterr().out
    assert shell.labelled == []


def test_a_refused_stack_records_each_rows_blocker_and_a_label_clears_it(capsys):
    shell = stack_shell(tracked=STACK)
    shell.routes[f"status:{'2' * 40}"] = "status-failure.json"

    assert label_stack(shell) == 1

    assert shell.fields("24002")["label_refused"] == "commit status failure on 222222222; only success is labelled"
    assert shell.fields("24002")["label_refused_head"] == "2" * 40
    assert shell.fields("24003")["label_refused"].startswith("the stack #24001 <- #24002 <- #24003 enqueues as one entry, so #24002")
    del shell.routes[f"status:{'2' * 40}"]

    assert label_stack(shell) == 0
    assert all(shell.fields(pr)["label_refused"] == "" for pr in STACK)


def test_a_refused_label_opens_no_row_for_an_unreported_pr(capsys):
    shell = desk_shell(mergeable_state="dirty")

    assert label(shell) == 1
    assert PR not in shell.keys()


def test_a_dry_run_refusal_records_nothing(capsys):
    shell = stack_shell(tracked=STACK)
    shell.routes[f"status:{'2' * 40}"] = "status-failure.json"

    assert label_stack(shell, STACK[-1], "--dry-run") == 1
    assert all("label_refused" not in shell.fields(pr) for pr in STACK)


def all_clean(shell, *extra) -> int:
    return run(shell, "label", "--repo", REPO, "--ledger", LEDGER, "--all-clean", *extra)


def lone_pr(shell: FakeShell, pr: str, head: str, lane: str = LANE, **fields) -> None:
    shell.pulls[pr] = {
        "number": int(pr),
        "state": "open",
        "head": {"sha": head, "ref": f"lone/{pr}", "repo": {"full_name": REPO}},
        "base": {"ref": "dev"},
        "mergeable_state": "clean",
    }
    shell.routes[f"checks:{head}"] = "check-runs-green.json"
    shell.reviews[pr] = [review("APPROVED", head)]
    shell.stores[LEDGER]["rows"].append({"key": pr, "fields": {"lane": lane, "reported_head": head, "reported_verdict": "clean", **fields}})


def test_all_clean_labels_every_clean_stack_tip_in_one_pass(capsys):
    shell = stack_shell(tracked=STACK)
    lone_pr(shell, "24010", "a" * 40)
    lone_pr(shell, "24011", "b" * 40)

    assert all_clean(shell) == 0

    assert sorted(shell.labelled) == ["24003:merge", "24010:merge", "24011:merge"]
    assert capsys.readouterr().out.splitlines()[-1] == "batch: labelled 3 of 3 stacks #24003 #24010 #24011"
    assert shell.fields("24001")["label_stack"] == "24001,24002,24003"


def test_all_clean_labels_the_rest_when_one_stack_refuses(capsys):
    shell = stack_shell(tracked=STACK)
    shell.routes[f"status:{'2' * 40}"] = "status-failure.json"
    lone_pr(shell, "24010", "a" * 40)

    assert all_clean(shell) == 0

    assert shell.labelled == ["24010:merge"]
    assert capsys.readouterr().out.splitlines()[-1] == "batch: labelled 1 of 2 stacks #24010 | refused #24003"
    assert shell.fields("24002")["label_refused_head"] == "2" * 40


def test_all_clean_skips_held_landed_and_already_labelled_rows(capsys):
    shell = FakeShell()
    lone_pr(shell, "24010", "a" * 40, hold_reason="owner applies first", hold_since="x", hold_until=stamp(timedelta(hours=1)))
    lone_pr(shell, "24011", "b" * 40, reported_verdict="held")
    lone_pr(shell, "24012", "c" * 40, state="landed")
    lone_pr(shell, "24013", "d" * 40, label_head="d" * 40, labelled_at=stamp(timedelta(minutes=-3)))
    lone_pr(shell, "24014", "e" * 40, label_head="e" * 40, label_pulled_at=stamp(timedelta(minutes=-3)), label_pull_reason="x")

    assert all_clean(shell) == 0

    assert shell.labelled == []
    assert capsys.readouterr().out.strip() == "batch: labelled 0 of 0 stacks"
    assert not any(endpoint.startswith(f"repos/{REPO}/pulls/2401") for endpoint in shell.endpoints())


def test_a_lanes_red_report_gates_nothing_when_the_forge_reads_green(capsys):
    shell = FakeShell()
    lone_pr(shell, "24015", "f" * 40, reported_verdict="red")

    assert all_clean(shell) == 0
    assert shell.labelled == ["24015:merge"]


def test_all_clean_labels_nothing_in_a_stack_whose_tip_is_red_on_the_forge(capsys):
    shell = stack_shell(tracked=STACK)
    shell.routes[f"status:{'3' * 40}"] = "status-failure.json"

    assert all_clean(shell) == 0

    assert shell.labelled == []
    assert "commit status failure on 333333333" in capsys.readouterr().out


def test_all_clean_dry_run_names_each_stack_and_writes_nothing(capsys):
    shell = stack_shell(tracked=STACK)
    lone_pr(shell, "24010", "a" * 40)

    assert all_clean(shell, "--dry-run") == 0

    out = capsys.readouterr().out
    assert f"would label #24003 {'3' * 40}, enqueuing #24001 <- #24002 <- #24003" in out
    assert out.splitlines()[-1] == "batch: would label 2 of 2 stacks #24003 #24010"
    assert shell.labelled == []


def test_all_clean_with_a_shard_labels_only_that_shards_lanes(capsys):
    shell = FakeShell()
    lone_pr(shell, "24010", "a" * 40, lane="lane-a")
    lone_pr(shell, "24011", "b" * 40, lane="lane-b")
    lone_pr(shell, "24012", "c" * 40, lane="lane-c")

    assert all_clean(shell, "--shard", "lane-a,lane-c") == 0

    assert sorted(shell.labelled) == ["24010:merge", "24012:merge"]


def test_label_requires_a_pr_or_all_clean():
    with pytest.raises(SystemExit):
        run(FakeShell(), "label", "--repo", REPO, "--ledger", LEDGER)
    with pytest.raises(SystemExit):
        run(FakeShell(), "label", "--repo", REPO, "--ledger", LEDGER, "--pr", PR, "--all-clean")


def stale_row(pr: str, minutes: int, lane: str = LANE, head: str = HEAD, **fields) -> dict:
    return {"key": pr, "fields": {"state": "open", "head": head, "lane": lane, "reported_head": HEAD, "reported_verdict": "clean", "reported_at": stamp(timedelta(minutes=-minutes)), **fields}}


def test_stale_names_every_clean_row_past_the_threshold_with_its_blocker_oldest_first(capsys):
    shell = FakeShell(
        rows=[
            stale_row("24020", 45, hold_reason="waits on #20314", hold_since="x", hold_until="2099-01-01T00:00:00Z"),
            stale_row("24021", 50, labels="merge", label_head=HEAD, labelled_at="2026-09-24T10:00:00Z"),
            stale_row("24022", 55, routed_head=HEAD, routed_job="fix: tsc"),
            stale_row("24023", 60, label_refused="ai-review is neutral", label_refused_head=HEAD),
            stale_row("24024", 65, head=OLD_HEAD),
            stale_row("24025", 70),
            stale_row("24026", 75, labels="merge"),
            stale_row("24027", 10),
            stale_row("24028", 90, reported_verdict="red"),
            stale_row("24029", 90, state="landed"),
        ]
    )

    run(shell, "stale", "--ledger", LEDGER)

    assert capsys.readouterr().out.splitlines() == [
        f"stale #24026 75m {LANE}: in the queue, labelled outside the desk",
        f"stale #24025 70m {LANE}: never graded: run label --all-clean",
        f"stale #24024 65m {LANE}: head moved since the report",
        f"stale #24023 60m {LANE}: label refused: ai-review is neutral",
        f"stale #24022 55m {LANE}: routed: fix: tsc",
        f"stale #24021 50m {LANE}: in the queue since 2026-09-24T10:00:00Z",
        f"stale #24020 45m {LANE}: held: waits on #20314 until 2099-01-01T00:00:00Z",
    ]


def test_stale_honours_minutes_and_shard(capsys):
    shell = FakeShell(rows=[stale_row("24020", 12, lane="lane-a"), stale_row("24021", 12, lane="lane-b")])

    run(shell, "stale", "--ledger", LEDGER, "--minutes", "10", "--shard", "lane-b")
    assert capsys.readouterr().out.splitlines() == ["stale #24021 12m lane-b: never graded: run label --all-clean"]

    run(shell, "stale", "--ledger", LEDGER)
    assert capsys.readouterr().out.strip() == "no clean row older than 30m and no PR older than 60h"


def test_summary_carries_stale_rows_under_the_counts_and_the_report_to_landed_median(capsys):
    shell = FakeShell(
        rows=[
            stale_row("24030", 40),
            {"key": "24031", "fields": {"state": "landed", "reported_at": stamp(timedelta(minutes=-50)), "landed_at": stamp(timedelta(minutes=-40))}},
            {"key": "24032", "fields": {"state": "landed", "reported_at": stamp(timedelta(minutes=-50)), "landed_at": stamp(timedelta(minutes=-20))}},
            {"key": "24033", "fields": {"state": "landed", "reported_at": stamp(timedelta(minutes=-55)), "landed_at": stamp(timedelta(minutes=-5))}},
        ]
    )

    lines = summarize(shell, capsys)

    assert lines[0].endswith("| merged/h 3 | labelled 0 | held 0 | rulings 0 | p0 0 | routed 0 | stale 1 | p50 report→landed 30m | lost 0 | landed-not-live 0")
    assert lines[1] == f"stale #24030 40m {LANE}: never graded: run label --all-clean"
    assert lines[2] == "waiting: ungraded #24030"
    assert lines[3] == "merged: #24031 #24032 #24033"


def test_summary_with_no_landing_prints_no_median(capsys):
    shell = FakeShell(rows=[stale_row("24030", 5)])

    assert summarize(shell, capsys, "--stale-minutes", "60")[0].endswith("| stale 0 | p50 report→landed - | lost 0 | landed-not-live 0")


def test_a_shard_sees_only_its_lanes_rows_and_messages(capsys):
    shell = FakeShell(
        rows=[
            stale_row("24040", 40, lane="lane-a"),
            stale_row("24041", 40, lane="lane-b"),
            {"key": "msg/000001", "fields": {"kind": "p0", "pr": "24040", "head": HEAD, "lane": "lane-a", "text": "dev red", "state": "pending"}},
            {"key": "msg/000002", "fields": {"kind": "p0", "pr": "24041", "head": HEAD, "lane": "lane-b", "text": "dev red", "state": "pending"}},
        ]
    )

    lines = summarize(shell, capsys, "--shard", "lane-a")
    assert "| open 1 |" in lines[0] and "| p0 1 |" in lines[0]
    assert lines[1] == "stale #24040 40m lane-a: never graded: run label --all-clean"

    run(shell, "inbox", "--ledger", LEDGER, "--shard", "lane-b")
    assert capsys.readouterr().out.strip() == f"msg/000002 p0 #24041 {HEAD[:9]} lane-b: dev red"


def test_a_sharded_refresh_regrades_only_its_lanes_rows(lock):
    shell = desk_shell(user={"login": "yasyf"}, title="t", changed_files=1, created_at="2026-09-24T08:00:00Z")
    shell.stores[LEDGER]["rows"] += [
        {"key": PR, "fields": {"lane": "lane-a", "reported_head": HEAD}},
        {"key": "24050", "fields": {"lane": "lane-b", "reported_head": HEAD}},
    ]

    run(shell, "refresh", "--repo", REPO, "--ledger", LEDGER, "--lock", str(lock), "--shard", "lane-a")

    [state] = shell.state_calls()
    assert PR in state
    assert "24050" not in state


class MovingShell(FakeShell):
    """Moves one PR's head the moment another PR is labelled, as a lane pushing mid-batch would."""

    def __init__(self, moves: str, to: str, after: str):
        super().__init__()
        self.moves, self.to, self.after = moves, to, after

    def run(self, argv, stdin=None):
        out = super().run(argv, stdin)
        if argv[0] == "gh" and "POST" in argv and f"issues/{self.after}/labels" in argv[2]:
            self.pulls[self.moves]["head"]["sha"] = self.to
        return out


def test_all_clean_rereads_each_tip_so_a_head_pushed_mid_batch_is_refused(capsys):
    shell = MovingShell(moves="24011", to="c" * 40, after="24010")
    lone_pr(shell, "24010", "a" * 40)
    lone_pr(shell, "24011", "b" * 40)

    assert all_clean(shell) == 0

    assert shell.labelled == ["24010:merge"]
    assert "REFUSED head moved: expected bbbbbbbbb, the forge has ccccccccc" in capsys.readouterr().out
    assert shell.fields("24011")["label_refused_head"] == "c" * 40


def test_all_clean_ignores_a_closed_child_so_its_parent_is_the_tip(capsys):
    shell = stack_shell(tracked=STACK)
    shell.pulls["24003"]["state"] = "closed"

    assert all_clean(shell) == 0

    assert shell.labelled == ["24002:merge"]


def test_a_refusal_on_a_moved_head_reads_as_refused_in_stale(capsys):
    shell = FakeShell()
    lone_pr(shell, "24060", "a" * 40, reported_head=HEAD, reported_at=stamp(timedelta(minutes=-40)))

    assert run(shell, "label", "--repo", REPO, "--ledger", LEDGER, "--pr", "24060", "--expect-head", HEAD) == 1
    capsys.readouterr()
    run(shell, "stale", "--ledger", LEDGER)

    assert capsys.readouterr().out.strip() == (
        f"stale #24060 40m {LANE}: label refused: head moved: expected {HEAD[:9]}, the forge has aaaaaaaaa; grade the new head before labelling"
    )


def lane_pull(shell: FakeShell, pr: str, head: str, branch: str, base: str = "dev") -> None:
    shell.pulls[pr] = {
        "number": int(pr),
        "state": "open",
        "head": {"sha": head, "ref": branch, "repo": {"full_name": REPO}},
        "base": {"ref": base},
        "mergeable_state": "clean",
        "user": {"login": "yasyf"},
        "title": f"pr {pr}",
        "changed_files": 1,
        "created_at": "2026-09-24T08:00:00Z",
    }
    shell.routes[f"checks:{head}"] = "check-runs-green.json"
    shell.reviews[pr] = [review("APPROVED", head)]


def refresh(shell, lock) -> int:
    return run(shell, "refresh", "--repo", REPO, "--ledger", LEDGER, "--lock", str(lock))


def test_register_records_the_lane_and_marks_its_prs_tracked(capsys):
    shell = FakeShell()

    run(shell, "register", "--ledger", LEDGER, "--lane", LANE, "--branch-prefix", "lightning/", "--pr", "24070")

    assert shell.fields(f"lane/{LANE}")["branch_prefix"] == "lightning/"
    assert shell.fields("24070") == {"lane": LANE, "registered": LANE}
    assert capsys.readouterr().out.strip() == f"registered {LANE} on lightning/* #24070"


def test_register_a_lone_pr_records_its_head_and_keeps_the_lane_that_claimed_it_first(capsys):
    shell = FakeShell()

    run(shell, "register", "--ledger", LEDGER, "--lane", LANE, "--pr", "24070", "--head", HEAD)
    run(shell, "register", "--ledger", LEDGER, "--lane", "ship-pr", "--pr", "24070", "--head", OLD_HEAD)

    assert [row["key"] for row in shell.stores[LEDGER]["rows"]] == ["24070"]
    assert shell.fields("24070") == {"lane": LANE, "registered": LANE, "registered_head": OLD_HEAD}
    assert capsys.readouterr().out.splitlines() == [f"registered {LANE} #24070", f"registered ship-pr #24070 (lane {LANE})"]


@pytest.mark.parametrize(
    "argv",
    [["--lane", LANE], ["--lane", LANE, "--pr", "1", "--pr", "2", "--head", HEAD]],
    ids=["nothing-to-register", "head-for-two-prs"],
)
def test_register_refuses_an_ambiguous_call(argv):
    with pytest.raises(SystemExit):
        run(FakeShell(), "register", "--ledger", LEDGER, *argv)


def test_list_reads_back_pr_rows_oldest_first_filtered_by_lane_and_state(capsys):
    shell = FakeShell(
        rows=[
            {"key": "24071", "fields": {"lane": LANE, "state": "open", "branch": "lightning/b", "head": HEAD}},
            {"key": "24070", "fields": {"lane": LANE, "registered": LANE, "registered_head": OLD_HEAD}},
            {"key": "24072", "fields": {"lane": "other", "state": "landed"}},
            {"key": "msg/000001", "fields": {"kind": "report"}},
            {"key": f"review/24071@{HEAD}", "fields": {"pr": "24071", "head": HEAD, "verdict": "findings"}},
        ]
    )

    run(shell, "list", "--ledger", LEDGER, "--lane", LANE, "--json")
    assert [(row["pr"], row["rules_blocked"]) for row in json.loads(capsys.readouterr().out)] == [("24070", False), ("24071", True)]

    run(shell, "list", "--ledger", LEDGER, "--open")
    assert capsys.readouterr().out.splitlines() == [
        f"#24070 {LANE} open - -",
        f"#24071 {LANE} open lightning/b {HEAD[:12]} rules-blocked",
    ]


@pytest.mark.parametrize(
    ("review", "blocked"),
    [
        ({"verdict": "findings"}, True),
        ({"verdict": "findings", "override": "R12"}, False),
        ({"verdict": "pending"}, False),
        ({"verdict": "error"}, False),
        ({"verdict": "clean"}, False),
    ],
    ids=["findings", "overridden", "pending", "error", "clean"],
)
def test_list_blocks_a_head_only_on_an_unwaived_finding(review, blocked, capsys):
    old = {"key": f"review/24071@{OLD_HEAD}", "fields": {"pr": "24071", "head": OLD_HEAD, "verdict": "findings"}}
    current = {"key": f"review/24071@{HEAD}", "fields": {"pr": "24071", "head": HEAD, **review}}
    shell = FakeShell(rows=[{"key": "24071", "fields": {"lane": LANE, "state": "open", "head": HEAD}}, old, current])

    run(shell, "list", "--ledger", LEDGER, "--json")
    assert json.loads(capsys.readouterr().out)[0]["rules_blocked"] is blocked


def test_refresh_admits_every_open_pr_on_a_registered_prefix_and_nothing_else(lock):
    shell = FakeShell()
    lane_pull(shell, "24071", "a" * 40, "lightning/one")
    lane_pull(shell, "24072", "b" * 40, "lightning/two")
    lane_pull(shell, "24073", "c" * 40, "someone-else/three")
    run(shell, "register", "--ledger", LEDGER, "--lane", LANE, "--branch-prefix", "lightning/")

    assert refresh(shell, lock) == 0

    assert sorted(shell.pr_keys()) == ["24071", "24072"]
    assert shell.fields("24071")["registered"] == LANE
    assert shell.fields("24071")["head"] == "a" * 40
    assert shell.state_calls() == [["ccx", "vcs", "pr", "state", "--repo", REPO, "--lane-prefix", "lightning/"]]


def test_an_unreported_registered_head_is_labelled_once_its_gates_pass(capsys, lock):
    shell = FakeShell()
    lane_pull(shell, "24071", "a" * 40, "lightning/one")
    run(shell, "register", "--ledger", LEDGER, "--lane", LANE, "--branch-prefix", "lightning/")
    refresh(shell, lock)

    assert all_clean(shell) == 0

    assert shell.labelled == ["24071:merge"]


def test_a_head_moved_since_the_report_is_regraded_and_labelled_without_a_re_report(capsys, lock):
    shell = FakeShell()
    lane_pull(shell, "24071", "b" * 40, "lightning/one")
    shell.stores[LEDGER]["rows"].append({"key": "24071", "fields": {"lane": LANE, "reported_head": "a" * 40, "reported_verdict": "red", "reported_at": stamp(timedelta(hours=-1))}})
    refresh(shell, lock)

    assert all_clean(shell) == 0

    assert shell.labelled == ["24071:merge"]
    assert shell.fields("24071")["label_head"] == "b" * 40


def test_a_moved_head_a_gate_refuses_routes_one_new_head_line_once(capsys, lock):
    shell = FakeShell()
    lane_pull(shell, "24071", "b" * 40, "lightning/one")
    shell.reviews["24071"] = []
    shell.stores[LEDGER]["rows"].append({"key": "24071", "fields": {"lane": LANE, "reported_head": "a" * 40, "reported_verdict": "clean", "reported_at": stamp(timedelta(hours=-1))}})
    refresh(shell, lock)
    capsys.readouterr()

    all_clean(shell)
    first = capsys.readouterr().out
    all_clean(shell)
    second = capsys.readouterr().out

    assert shell.labelled == []
    assert f"to {LANE}:\nDESK #24071 bbbbbbbbb: new head bbbbbbbbb: #24071 has no approval in force" in first
    assert "already routed" in second
    assert shell.fields("24071")["routed_head"] == "b" * 40


def test_a_refusal_on_a_gone_lanes_head_is_never_sent_to_it(capsys, lock):
    shell = FakeShell()
    lane_pull(shell, "24071", "b" * 40, "lightning/one")
    shell.reviews["24071"] = []
    shell.stores[LEDGER]["rows"].append({"key": "24071", "fields": {"lane": LANE, "reported_head": "a" * 40, "reported_verdict": "clean", "reported_at": stamp(timedelta(hours=-1))}})
    refresh(shell, lock)
    run(shell, "gone", "--ledger", LEDGER, "--lane", LANE)
    capsys.readouterr()

    all_clean(shell)

    printed = capsys.readouterr().out
    assert f"#24071 {LANE} is gone; its refusal goes to the root, not the lane" in printed
    assert f"to {LANE}:" not in printed
    assert "routed_head" not in shell.fields("24071")


def test_a_moved_downstack_head_is_graded_and_labelled_without_a_re_report(capsys, lock):
    shell = FakeShell()
    lane_pull(shell, "24081", "c" * 40, "lightning/base")
    lane_pull(shell, "24082", "d" * 40, "lightning/tip", base="lightning/base")
    for pr, head in (("24081", "1" * 40), ("24082", "d" * 40)):
        shell.stores[LEDGER]["rows"].append({"key": pr, "fields": {"lane": LANE, "reported_head": head, "reported_verdict": "clean", "reported_at": stamp(timedelta(hours=-1))}})
    refresh(shell, lock)

    assert all_clean(shell) == 0
    assert shell.labelled == ["24082:merge"]
    assert shell.fields("24081")["label_head"] == "c" * 40


def test_a_head_with_an_unwaived_rules_finding_is_never_labelled(capsys):
    shell = desk_shell()
    review = {"key": f"review/{PR}@{HEAD}", "fields": {"pr": PR, "head": HEAD, "verdict": "findings"}}
    shell.store["rows"].append(review)

    assert label(shell, "--expect-head", HEAD) == 1
    assert shell.labelled == []
    assert f"#{PR} {HEAD[:9]} has rules-review findings" in capsys.readouterr().out

    review["fields"]["override"] = "R901"
    assert label(shell, "--expect-head", HEAD) == 0
    assert shell.labelled == [f"{PR}:merge"]


def test_a_head_no_review_has_reached_is_labelled(capsys):
    shell = desk_shell()

    assert label(shell, "--expect-head", HEAD) == 0
    assert shell.labelled == [f"{PR}:merge"]


def test_a_head_is_labelled_as_soon_as_its_gates_pass(capsys):
    shell = desk_shell()

    assert label(shell, "--expect-head", HEAD) == 0
    assert shell.labelled == [f"{PR}:merge"]
    assert f"repos/{REPO}/commits/{HEAD}" not in shell.endpoints()


def test_summary_lists_every_tracked_pr_without_a_labelable_head_by_reason(capsys):
    reported = {"state": "open", "lane": LANE, "head": HEAD, "reported_head": HEAD, "reported_at": stamp(timedelta(minutes=-1))}
    shell = FakeShell(
        rows=[
            {"key": "24090", "fields": {"state": "open", "lane": LANE, "registered": LANE, "head": HEAD}},
            {"key": "24091", "fields": dict(reported, head=OLD_HEAD, reported_verdict="clean")},
            {"key": "24092", "fields": dict(reported, reported_verdict="clean", label_refused="ai-review is neutral", label_refused_head=HEAD)},
            {"key": "24093", "fields": dict(reported, reported_verdict="clean", label_refused="x", label_refused_head=OLD_HEAD)},
            {"key": "24094", "fields": dict(reported, reported_verdict="clean", test_state="failure")},
            {"key": "24095", "fields": dict(reported, reported_verdict="conflicting", mergeable_state="dirty")},
            {"key": "24096", "fields": dict(reported, reported_verdict="held")},
            {"key": "24097", "fields": dict(reported, reported_verdict="clean", hold_reason="x", hold_since="x", hold_until="2099-01-01T00:00:00Z")},
            {"key": "24098", "fields": dict(reported, reported_verdict="clean", labels="merge")},
            {"key": "24099", "fields": {"state": "open", "head": HEAD}},
        ]
    )

    lines = summarize(shell, capsys)

    assert "waiting: ungraded #24090 #24091 #24093 | refused #24092 | red #24094 #24095 | held #24096 #24097" in lines


def test_summary_never_reports_a_landed_row_as_waiting(capsys):
    shell = desk_shell(state="closed")
    shell.stores[LEDGER]["rows"].append({"key": PR, "fields": {"lane": LANE, "registered": LANE, "head": HEAD, "state": "open"}})
    shell.pr_files[PR] = ["infra/rows/lightning.ts"]
    shell.delivered[HEAD] = (SQUASH, stamp(timedelta(minutes=-5)))

    lines = summarize(shell, capsys)

    assert shell.fields(PR)["state"] == "landed"
    assert "merged: #21221" in lines
    assert not [line for line in lines if line.startswith("waiting")]


def orca_worker(dispatch: str, outcome: str = "in_progress") -> dict:
    return {"dispatchId": dispatch, "projection": {"outcome": outcome}}


def minutes_ago(minutes: int) -> float:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).timestamp() * 1000


def shown(branch: str, wait: dict | None) -> dict:
    return {"observation": {"agentWait": wait}, "terminal": {"branch": f"refs/heads/{branch}"}}


def test_summary_inside_orca_names_every_worker_parked_on_a_prompt_for_five_minutes(capsys, monkeypatch):
    monkeypatch.setenv(ledger.ORCA_TERMINAL, "term_root")
    shell = FakeShell(rows=[{"key": f"lane/{LANE}", "fields": {"lane": LANE, "branch_prefix": "yasyf/lightning/"}}])
    shell.orca = {
        ("orchestration", "worker-list"): {"workers": [orca_worker("ctx_old"), orca_worker("ctx_new"), orca_worker("ctx_done", "succeeded")], "page": {"hasMore": True, "nextCursor": "c2"}},
        ("orchestration", "worker-list", "--cursor", "c2"): {"workers": [orca_worker("ctx_busy"), orca_worker("ctx_title")], "page": {"hasMore": False}},
        ("orchestration", "worker-show", "--dispatch", "ctx_old"): shown("yasyf/lightning/bake", {"source": "hook", "since": minutes_ago(7)}),
        ("orchestration", "worker-show", "--dispatch", "ctx_new"): shown("yasyf/lightning/bake", {"source": "hook", "since": minutes_ago(2)}),
        ("orchestration", "worker-show", "--dispatch", "ctx_busy"): shown("yasyf/lightning/bake", None),
        ("orchestration", "worker-show", "--dispatch", "ctx_title"): shown("yasyf/v3-other", {"source": "prompt-text", "reason": "plan approval"}),
    }

    lines = summarize(shell, capsys)

    assert lines[1:3] == [
        f"WAITING-ON-PROMPT {LANE} 7m dispatch=ctx_old via hook: interactive prompt",
        "WAITING-ON-PROMPT yasyf/v3-other ?m dispatch=ctx_title via prompt-text: plan approval",
    ]
    assert ["orca", "orchestration", "worker-show", "--dispatch", "ctx_done", "--json"] not in shell.calls


def test_summary_outside_orca_reads_no_orca_state(capsys):
    shell = FakeShell()

    summarize(shell, capsys)

    assert not [call for call in shell.calls if call[0] == "orca"]


def test_register_refuses_a_prefix_that_is_not_a_whole_branch_namespace():
    for prefix in ("lightning", "", "/"):
        with pytest.raises(SystemExit):
            run(FakeShell(), "register", "--ledger", LEDGER, "--lane", LANE, "--branch-prefix", prefix)


def test_a_registered_row_before_its_first_refresh_is_neither_routed_nor_crashes(capsys):
    shell = FakeShell()
    run(shell, "register", "--ledger", LEDGER, "--lane", LANE, "--branch-prefix", "lightning/", "--pr", "24071")

    assert run(shell, "route", "--repo", REPO, "--ledger", LEDGER) == 0
    assert run(shell, "show", "--ledger", LEDGER, "--red") == 0
    assert "routed 0 rows" in capsys.readouterr().out


def test_a_parent_shared_by_two_tips_is_routed_once_per_batch(capsys, lock):
    shell = FakeShell()
    lane_pull(shell, "24101", "e" * 40, "lightning/base")
    lane_pull(shell, "24102", "f" * 40, "lightning/left", base="lightning/base")
    lane_pull(shell, "24103", "9" * 40, "lightning/right", base="lightning/base")
    shell.reviews["24101"] = []
    run(shell, "register", "--ledger", LEDGER, "--lane", LANE, "--branch-prefix", "lightning/")
    refresh(shell, lock)
    capsys.readouterr()

    all_clean(shell)

    assert capsys.readouterr().out.count("DESK #24101") == 1
    assert shell.labelled == []


def test_a_red_head_the_route_sweep_owns_is_not_routed_again_by_the_batch(capsys, lock):
    shell = FakeShell()
    lane_pull(shell, "24071", "b" * 40, "lightning/one")
    shell.routes[f"status:{'b' * 40}"] = "status-failure.json"
    run(shell, "register", "--ledger", LEDGER, "--lane", LANE, "--branch-prefix", "lightning/")
    refresh(shell, lock)
    capsys.readouterr()

    all_clean(shell)

    assert "DESK #24071" not in capsys.readouterr().out
    assert shell.fields("24071")["test_state"] == "failure"


def test_a_head_pushed_during_the_conflict_fetch_is_not_routed(capsys, lock, tmp_path):
    shell = FakeShell()
    lane_pull(shell, "24071", "b" * 40, "lightning/one")
    shell.pull_heads["24071"] = "c" * 40
    run(shell, "register", "--ledger", LEDGER, "--lane", LANE, "--branch-prefix", "lightning/")
    refresh(shell, lock)
    capsys.readouterr()

    all_clean(shell, "--checkout", str(tmp_path))

    out = capsys.readouterr().out
    assert "refs/pull/24071/head is ccccccccc on the forge" in out
    assert "DESK #24071" not in out


def test_a_clean_report_a_gate_refused_is_waiting_as_refused(capsys):
    shell = FakeShell(
        rows=[
            {"key": "24110", "fields": {"state": "open", "lane": LANE, "head": HEAD, "reported_head": HEAD, "reported_verdict": "clean", "reported_at": stamp(timedelta(minutes=-1)), "label_refused": "ai-review is neutral", "label_refused_head": HEAD}},
        ]
    )

    assert summarize(shell, capsys)[1] == "waiting: refused #24110"

def stacked(pr: str, base: str, test_state: str = "success") -> dict:
    return {"key": pr, "fields": {"state": "open", "branch": f"s/{pr}", "base": base, "test_state": test_state, "lane": LANE}}


def test_hold_refuses_a_pr_with_green_prs_stacked_on_it():
    shell = FakeShell(rows=[stacked("24100", "dev"), stacked("24101", "s/24100", "pending"), stacked("24102", "s/24101")])

    with pytest.raises(SystemExit, match=r"#24100 has green PRs stacked on it \(#24102\).*--parent <child>=<trunk>"):
        hold(shell, "24100", "--hours", "4")

    assert "hold_until" not in shell.fields("24100")


def test_hold_stack_holds_every_pr_stacked_above_with_the_same_reason_and_expiry(capsys):
    shell = FakeShell(rows=[stacked("24100", "dev"), stacked("24101", "s/24100"), stacked("24102", "s/24101"), stacked("24103", "dev")])

    assert hold(shell, "24100", "--hours", "4", "--stack") == 0

    held = [line.split()[1] for line in capsys.readouterr().out.splitlines()]
    assert held == ["#24100", "#24101", "#24102"]
    assert len({shell.fields(pr)["hold_until"] for pr in ("24100", "24101", "24102")}) == 1
    assert shell.fields("24102")["hold_reason"] == "owner applies first"
    assert "hold_until" not in shell.fields("24103")


def test_hold_on_the_top_of_a_stack_or_under_red_work_needs_no_stack_flag():
    shell = FakeShell(rows=[stacked("24100", "dev"), stacked("24101", "s/24100", "failure"), {"key": "24102", "fields": dict(stacked("24102", "s/24100")["fields"], state="landed")}])

    assert hold(shell, "24101", "--hours", "4") == 0
    assert hold(shell, "24100", "--hours", "4") == 0


def aged_row(pr: str, hours: int, **fields) -> dict:
    return {"key": pr, "fields": {"state": "open", "head": HEAD, "lane": LANE, "registered": LANE, "created_at": stamp(timedelta(hours=-hours)), **fields}}


def test_stale_names_every_pr_opened_60_hours_ago_oldest_first(capsys):
    shell = FakeShell(
        rows=[
            aged_row("24200", 61),
            aged_row("24201", 90, test_state="failure"),
            aged_row("24202", 70, labels="merge"),
            aged_row("24203", 59),
            aged_row("24204", 100, state="landed"),
        ]
    )

    run(shell, "stale", "--ledger", LEDGER)

    assert capsys.readouterr().out.splitlines() == [
        f"aged #24201 90h {LANE}: red",
        f"aged #24202 70h {LANE}: in the queue",
        f"aged #24200 61h {LANE}: ungraded",
    ]

    run(shell, "stale", "--ledger", LEDGER, "--hours", "80")
    assert capsys.readouterr().out.splitlines() == [f"aged #24201 90h {LANE}: red"]
