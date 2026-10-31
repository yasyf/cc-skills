from __future__ import annotations

import json

from long_running import ledger
from conftest import DIRTY_HEAD, DRIVE, LEDGER, MOVED_HEAD, FakeShell

HOME = "Forge-AI/monorepo"
OTHER = "yasyf/cc-skills"
HEAD = "e718a7434fa01ee9835f992a9a647a825350732c"


def run(shell: FakeShell, *argv: str) -> None:
    assert ledger.main(list(argv), shell) == 0


def report(shell: FakeShell, pr: str, lane: str, *extra: str) -> None:
    run(shell, "report", "--ledger", LEDGER, "--drive", DRIVE, "--pr", pr, "--head", HEAD, "--lane", lane, "--verdict", "clean", *extra)


def state_call(repo: str, *rest: str) -> list[str]:
    return ["ccx", "vcs", "pr", "state", "--repo", repo, "--max-age", "2m", *rest]


def test_scope_maps_pr_keys_both_ways():
    other = ledger.Scope(OTHER, HOME)

    assert other.store("12") == f"{OTHER}#12"
    assert other.store("ask/000001") == "ask/000001"
    stored = {"12": {"lane": "home"}, f"{OTHER}#12": {"lane": "other"}, "ask/000001": {}}
    assert other.view(stored) == {"12": {"lane": "other"}, "ask/000001": {}}
    assert ledger.Scope(HOME, HOME).view(stored) == {"12": {"lane": "home"}, "ask/000001": {}}
    assert ledger.Scope("forge-ai/MONOREPO", HOME).store("12") == "12"


def test_the_same_number_in_two_repos_is_two_rows():
    shell = FakeShell(rows=[{"key": "21052", "fields": {"lane": "monorepo-lane", "reported_head": "a" * 40}}])

    report(shell, "21052", "skills-lane", "--repo", OTHER)

    assert shell.fields("21052") == {"lane": "monorepo-lane", "reported_head": "a" * 40}
    assert shell.fields(f"{OTHER}#21052")["lane"] == "skills-lane"
    assert shell.messages()[-1]["topic"] == f"{OTHER}#21052"


def test_a_report_without_repo_lands_on_the_checkouts_own_bare_key():
    shell = FakeShell()

    report(shell, "21052", "monorepo-lane")

    assert shell.keys() == ["21052"]
    assert shell.messages()[-1]["topic"] == "21052"


def test_refresh_reads_each_repo_once_and_writes_each_row_under_its_own_key(lock):
    shell = FakeShell(rows=[{"key": "20961", "fields": {"head": "stale"}}, {"key": f"{OTHER}#21052", "fields": {"head": "stale", "lane": "skills-lane"}}])

    run(shell, "refresh", "--ledger", LEDGER, "--lock", str(lock), "--pr", "20970")

    assert shell.state_calls() == [state_call(HOME, "20961", "20970"), state_call(OTHER, "21052")]
    assert shell.fields("20961")["head"] == DIRTY_HEAD
    assert shell.fields(f"{OTHER}#21052")["head"] == MOVED_HEAD
    assert shell.fields(f"{OTHER}#21052")["lane"] == "skills-lane"
    assert sorted(shell.keys()) == ["20961", "20970", f"{OTHER}#21052"]


def test_refresh_admits_a_named_pr_into_the_repo_it_names(lock):
    shell = FakeShell()

    run(shell, "refresh", "--repo", OTHER, "--ledger", LEDGER, "--lock", str(lock), "--pr", "20970", "--lane", "20970=skills-lane")

    assert shell.state_calls() == [state_call(OTHER, "20970")]
    assert shell.keys() == [f"{OTHER}#20970"]
    assert shell.fields(f"{OTHER}#20970")["lane"] == "skills-lane"


def test_a_lane_prefix_is_read_only_against_the_repo_it_was_registered_in(lock):
    lanes = [
        {"key": "lane/skills-lane", "fields": {"lane": "skills-lane", "branch_prefix": "skills/", "repo": OTHER}},
        {"key": "lane/monorepo-lane", "fields": {"lane": "monorepo-lane", "branch_prefix": "mono/"}},
    ]
    shell = FakeShell(rows=lanes)

    run(shell, "refresh", "--ledger", LEDGER, "--lock", str(lock))

    assert shell.state_calls() == [state_call(HOME, "--lane-prefix", "mono/"), state_call(OTHER, "--lane-prefix", "skills/")]


def test_register_records_the_repo_its_prefix_and_prs_live_in():
    shell = FakeShell()

    run(shell, "register", "--ledger", LEDGER, "--lane", "skills-lane", "--repo", OTHER, "--branch-prefix", "skills/tool-x", "--pr", "123")

    assert shell.fields("lane/skills-lane")["repo"] == OTHER
    assert shell.fields(f"{OTHER}#123")["registered"] == "skills-lane"
    assert shell.state_calls() == [state_call(OTHER, "--lane-prefix", "skills/tool-x")]


def test_hold_and_lift_touch_only_the_named_repos_row():
    shell = FakeShell(rows=[{"key": "20961", "fields": {"lane": "monorepo-lane"}}, {"key": f"{OTHER}#20961", "fields": {"lane": "skills-lane"}}])

    run(shell, "hold", "--ledger", LEDGER, "--repo", OTHER, "--pr", "20961", "--reason", "waits on the release", "--hours", "2")

    assert "hold_reason" not in shell.fields("20961")
    assert shell.fields(f"{OTHER}#20961")["hold_reason"] == "waits on the release"

    run(shell, "lift", "--ledger", LEDGER, "--repo", OTHER, "--pr", "20961")

    assert shell.fields(f"{OTHER}#20961")["hold_until"] == ""
    assert "hold_until" not in shell.fields("20961")


def test_list_reads_one_repo_and_names_it(capsys):
    rows = [{"key": "20961", "fields": {"lane": "monorepo-lane", "state": "landed"}}, {"key": f"{OTHER}#20961", "fields": {"lane": "skills-lane", "state": "landed"}}]
    shell = FakeShell(rows=rows)

    run(shell, "list", "--ledger", LEDGER, "--repo", OTHER, "--json")
    run(shell, "list", "--ledger", LEDGER, "--json")

    other, home = (json.loads(line) for line in capsys.readouterr().out.splitlines())
    assert [(row["pr"], row["repo"], row["lane"]) for row in other] == [("20961", OTHER, "skills-lane")]
    assert [(row["pr"], row["repo"], row["lane"]) for row in home] == [("20961", HOME, "monorepo-lane")]


def test_an_ask_links_and_grades_the_pr_in_the_repo_its_report_named(capsys):
    ask = {"key": "ask/000001", "fields": {"text": "ship it", "lane": "skills-lane", "accept": "merged", "asked_at": "2026-01-01T00:00:00Z"}}
    landed = {"key": "21052", "fields": {"state": "landed", "landed_at": "2026-01-01T01:00:00Z"}}
    shell = FakeShell(rows=[ask, landed])

    report(shell, "21052", "skills-lane", "--repo", OTHER, "--ask", "ask/000001")
    run(shell, "show", "--ledger", LEDGER, "--asks")

    assert shell.fields("ask/000001")["prs"] == f"{OTHER}#21052"
    assert capsys.readouterr().out.strip().endswith("[IN-PR]")


def test_unregister_drops_the_rows_its_prefix_pulled_in_from_another_repo(capsys):
    lane = {"key": "lane/skills-lane", "fields": {"lane": "skills-lane", "branch_prefix": "skills/", "repo": OTHER}}
    discovered = {"key": f"{OTHER}#7", "fields": {"lane": "skills-lane", "registered": "skills-lane", "branch": "skills/x"}}
    shell = FakeShell(rows=[lane, discovered])

    run(shell, "unregister", "--ledger", LEDGER, "--lane", "skills-lane")

    assert shell.keys() == []
    assert capsys.readouterr().out.strip().endswith(f"dropped {OTHER}#7")


def test_unregister_leaves_the_same_prefix_alone_in_another_repo():
    lane = {"key": "lane/skills-lane", "fields": {"lane": "skills-lane", "branch_prefix": "skills/", "repo": OTHER}}
    home = {"key": "7", "fields": {"lane": "skills-lane", "registered": "skills-lane", "branch": "skills/x"}}
    shell = FakeShell(rows=[lane, home])

    run(shell, "unregister", "--ledger", LEDGER, "--lane", "skills-lane")

    assert shell.keys() == ["7"]


def test_a_repo_spelled_in_another_case_writes_the_same_row():
    shell = FakeShell(rows=[{"key": f"{OTHER}#20961", "fields": {"lane": "skills-lane"}}])

    run(shell, "lift", "--ledger", LEDGER, "--repo", "Yasyf/CC-Skills", "--pr", "20961")

    assert shell.keys() == [f"{OTHER}#20961"]


def test_keys_that_look_like_prefixes_or_start_with_a_digit_are_pr_keys():
    stored = {"123org/project#7": {"lane": "a"}, "review/project#8": {"lane": "b"}, "lane/project#9": {"lane": "c"}}

    assert not any(ledger.is_malformed(key) for key in stored)
    assert ledger.Scope("review/project", HOME).view(stored) == {"8": {"lane": "b"}}
    assert ledger.Scope(HOME, HOME).lanes(stored) == {}
