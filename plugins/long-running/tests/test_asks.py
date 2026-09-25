from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import LEDGER
from test_desk import HEAD, LANE, PR, desk_shell, run, stamp, summarize

ASK = "one post and one approval per release"
CHECK = "the next release posts once and holds once"


def ask(shell, text=ASK, lane=LANE) -> int:
    return run(shell, "ask", "--ledger", LEDGER, "--text", text, "--lane", lane, "--accept", CHECK)


def report(shell, pr=PR, ask_id="ask/000001") -> int:
    return run(shell, "report", "--ledger", LEDGER, "--pr", pr, "--head", HEAD, "--lane", LANE, "--verdict", "clean", "--ask", ask_id)


def aged(shell, key: str, minutes: int) -> None:
    shell.fields(key)["asked_at"] = stamp(timedelta(minutes=-minutes))


def test_ask_records_the_owner_text_verbatim_under_its_own_key(capsys):
    shell = desk_shell()

    assert ask(shell) == 0
    assert ask(shell, text="a second ask", lane="other") == 0

    assert capsys.readouterr().out.splitlines() == [f"ask/000001 {LANE}: {ASK}", "ask/000002 other: a second ask"]
    row = shell.fields("ask/000001")
    assert (row["text"], row["lane"], row["accept"]) == (ASK, LANE, CHECK)
    assert shell.pr_keys() == []


def test_report_links_each_pr_to_its_ask_once():
    shell = desk_shell()
    ask(shell)

    report(shell)
    report(shell)
    report(shell, pr="21222")

    assert shell.fields("ask/000001")["prs"] == f"{PR},21222"
    assert shell.fields(PR)["reported_head"] == HEAD


def test_one_pr_can_deliver_two_asks():
    shell = desk_shell()
    ask(shell)
    ask(shell, text="a second ask")

    report(shell)
    report(shell, ask_id="ask/000002")

    assert shell.fields("ask/000001")["prs"] == PR
    assert shell.fields("ask/000002")["prs"] == PR


def test_report_refuses_an_ask_the_ledger_does_not_hold():
    shell = desk_shell()

    with pytest.raises(SystemExit):
        report(shell, ask_id="ask/000009")

    assert shell.keys() == []


def test_summary_names_every_ask_with_no_pr_after_thirty_minutes_as_dropped(capsys):
    shell = desk_shell()
    for text in ("dropped", "fresh", "linked", "verified"):
        ask(shell, text=text)
    for key in ("ask/000001", "ask/000003", "ask/000004"):
        aged(shell, key, 45)
    report(shell, ask_id="ask/000003")
    run(shell, "verify", "--ledger", LEDGER, "--ask", "ask/000004", "--text", "answered in chat")

    lines = summarize(shell, capsys)

    assert lines[0].endswith("| dropped 1 | unverified 0")
    assert lines[1] == f"DROPPED ask/000001 {LANE}: dropped"
    assert not [line for line in lines[2:] if "ask/" in line]


def test_summary_names_every_landed_ask_whose_check_is_unmarked_until_verify(capsys):
    shell = desk_shell()
    ask(shell)
    ask(shell, text="half landed")
    report(shell)
    report(shell, pr="21222", ask_id="ask/000002")
    report(shell, pr="21223", ask_id="ask/000002")
    for pr in (PR, "21222"):
        shell.fields(pr).update(state="landed", landed_at=stamp(timedelta(minutes=-5)))

    lines = summarize(shell, capsys)

    assert lines[0].endswith("| dropped 0 | unverified 1")
    assert lines[1] == f"UNVERIFIED ask/000001 {LANE}: {ASK}; check: {CHECK}"

    run(shell, "verify", "--ledger", LEDGER, "--ask", "ask/000001", "--text", "release 37 posted once")

    assert shell.fields("ask/000001")["verified"] == "release 37 posted once"
    assert summarize(shell, capsys)[0].endswith("| dropped 0 | unverified 0")


def test_summary_never_truncates_an_ask_line_under_the_ten_line_cap(capsys):
    shell = desk_shell()
    for index in range(12):
        ask(shell, text=f"ask {index}")
        aged(shell, f"ask/{index + 1:06d}", 45)
    for pr in range(21300, 21312):
        shell.stores[LEDGER]["rows"].append(
            {"key": str(pr), "fields": {"head": HEAD, "hold_reason": f"reason {pr}", "hold_since": "x", "hold_until": stamp(timedelta(hours=1))}}
        )

    lines = summarize(shell, capsys)

    assert lines[1:13] == [f"DROPPED ask/{index + 1:06d} {LANE}: ask {index}" for index in range(12)]
    assert len(lines) == 22
    assert lines[-1].endswith("more lines in ledger show")


def test_verify_refuses_an_ask_the_ledger_does_not_hold():
    with pytest.raises(SystemExit):
        run(desk_shell(), "verify", "--ledger", LEDGER, "--ask", "ask/000001", "--text", "x")


def test_show_asks_lists_each_ask_with_its_prs_or_its_verification(capsys):
    shell = desk_shell()
    ask(shell)
    ask(shell, text="unstarted")
    ask(shell, text="answered")
    report(shell)
    run(shell, "verify", "--ledger", LEDGER, "--ask", "ask/000003", "--text", "done")
    capsys.readouterr()

    run(shell, "show", "--ledger", LEDGER, "--asks")

    assert capsys.readouterr().out.splitlines() == [
        f"ask/000001 {LANE}: {ASK} [#{PR}]",
        f"ask/000002 {LANE}: unstarted [no PR]",
        f"ask/000003 {LANE}: answered [verified]",
    ]
