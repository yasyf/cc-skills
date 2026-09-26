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


def test_report_refuses_a_second_ask_on_a_pr_still_in_pr():
    shell = desk_shell()
    ask(shell)
    ask(shell, text="a second ask")

    report(shell)

    with pytest.raises(SystemExit, match="already carries ask/000001, still IN-PR"):
        report(shell, ask_id="ask/000002")
    assert shell.fields("ask/000002").get("prs", "") == ""


def test_report_may_add_a_second_ask_once_the_first_has_landed():
    shell = desk_shell()
    ask(shell)
    ask(shell, text="a second ask")
    report(shell)
    shell.fields(PR).update(state="landed", landed_at=stamp(timedelta(minutes=-5)))

    assert report(shell, ask_id="ask/000002") == 0

    assert shell.fields("ask/000002")["prs"] == PR


def test_report_refuses_an_ask_the_ledger_does_not_hold():
    shell = desk_shell()

    with pytest.raises(SystemExit):
        report(shell, ask_id="ask/000009")

    assert shell.keys() == []


def test_summary_names_every_ask_with_no_pr_after_thirty_minutes_as_lost(capsys):
    shell = desk_shell()
    for text in ("lost", "fresh", "linked", "answered"):
        ask(shell, text=text)
    for key in ("ask/000001", "ask/000003", "ask/000004"):
        aged(shell, key, 45)
    report(shell, ask_id="ask/000003")
    run(shell, "answer", "--ledger", LEDGER, "--ask", "ask/000004", "--text", "answered in chat")

    lines = summarize(shell, capsys)

    assert lines[0].endswith("| lost 1 | landed-not-live 0")
    assert lines[1] == f"LOST ask/000001 {LANE}: lost"
    assert not [line for line in lines[2:] if "ask/" in line]


def test_summary_names_every_delivered_ask_as_landed_not_live_until_the_pipeline_runs(capsys):
    shell = desk_shell()
    ask(shell)
    ask(shell, text="half landed")
    report(shell)
    report(shell, pr="21222", ask_id="ask/000002")
    report(shell, pr="21223", ask_id="ask/000002")
    landed_at = stamp(timedelta(minutes=-5))
    for pr in (PR, "21222"):
        shell.fields(pr).update(state="landed", landed_at=landed_at)

    lines = summarize(shell, capsys)

    assert lines[0].endswith("| lost 0 | landed-not-live 1")
    assert lines[1] == f"LANDED-NOT-LIVE ask/000001 {LANE}: {ASK}"

    run(shell, "live", "--ledger", LEDGER, "--at", stamp(timedelta(minutes=-1)))

    assert summarize(shell, capsys)[0].endswith("| lost 0 | landed-not-live 0")
    capsys.readouterr()
    run(shell, "show", "--ledger", LEDGER, "--asks")
    assert f"ask/000001 {LANE}: {ASK} [LIVE]" in capsys.readouterr().out.splitlines()


def test_summary_escalates_an_in_pr_ask_past_the_hour_mark(capsys):
    shell = desk_shell()
    ask(shell)
    report(shell)
    aged(shell, "ask/000001", 61)

    lines = summarize(shell, capsys)

    assert lines[1] == f"IN-PR 61m ask/000001 {LANE}: {ASK}: #{PR} never graded: run label --all-clean"


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

    assert lines[1:13] == [f"LOST ask/{index + 1:06d} {LANE}: ask {index}" for index in range(12)]
    assert len(lines) == 22
    assert lines[-1].endswith("more lines in ledger show")


def test_drop_refuses_an_ask_the_ledger_does_not_hold():
    with pytest.raises(SystemExit):
        run(desk_shell(), "drop", "--ledger", LEDGER, "--ask", "ask/000001", "--reason", "x")


def test_answer_refuses_an_ask_the_ledger_does_not_hold():
    with pytest.raises(SystemExit):
        run(desk_shell(), "answer", "--ledger", LEDGER, "--ask", "ask/000001", "--text", "x")


def test_drop_is_terminal_regardless_of_age(capsys):
    shell = desk_shell()
    ask(shell)
    aged(shell, "ask/000001", 45)

    run(shell, "drop", "--ledger", LEDGER, "--ask", "ask/000001", "--reason", "owner withdrew it")

    lines = summarize(shell, capsys)
    assert lines[0].endswith("| lost 0 | landed-not-live 0")
    assert not [line for line in lines[1:] if "ask/" in line]


def test_show_asks_lists_each_ask_with_its_computed_state(capsys):
    shell = desk_shell()
    ask(shell)
    ask(shell, text="unstarted")
    ask(shell, text="a question")
    report(shell)
    run(shell, "answer", "--ledger", LEDGER, "--ask", "ask/000003", "--text", "done")
    capsys.readouterr()

    run(shell, "show", "--ledger", LEDGER, "--asks")

    assert capsys.readouterr().out.splitlines() == [
        f"ask/000001 {LANE}: {ASK} [IN-PR]",
        f"ask/000002 {LANE}: unstarted [pending]",
        f"ask/000003 {LANE}: a question [answered]",
    ]
