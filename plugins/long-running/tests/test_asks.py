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


@pytest.mark.parametrize("ask_id", ["ask/000001", "000001", "1"])
def test_every_ask_verb_finds_the_ask_by_its_printed_key_or_its_bare_number(capsys, ask_id):
    shell = desk_shell()
    ask(shell)
    ask(shell, text="a question")
    ask(shell, text="withdrawn")

    report(shell, ask_id=ask_id)
    run(shell, "answer", "--ledger", LEDGER, "--ask", ask_id.replace("1", "2"), "--text", "done")
    run(shell, "drop", "--ledger", LEDGER, "--ask", ask_id.replace("1", "3"), "--reason", "owner withdrew it")
    mark(shell, "backlog", ask_id=ask_id, note="parked")

    assert shell.fields("ask/000001")["prs"] == PR
    assert shell.fields("ask/000001")["state"] == "backlog"
    assert shell.fields("ask/000002")["answer"] == "done"
    assert shell.fields("ask/000003")["dropped_reason"] == "owner withdrew it"
    assert not [key for key in shell.keys() if key.startswith("ask/") and key not in ("ask/000001", "ask/000002", "ask/000003")]


@pytest.mark.parametrize("ask_id", ["ask/", "ask/x1", "msg/000001", "000001a", ""])
def test_an_ask_id_that_is_neither_a_key_nor_a_number_is_a_usage_error(ask_id):
    with pytest.raises(SystemExit) as refused:
        run(desk_shell(), "answer", "--ledger", LEDGER, "--ask", ask_id, "--text", "x")
    assert refused.value.code == 2


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


def mark(shell, state, ask_id="ask/000001", note=None) -> int:
    return run(shell, "ask", "--ledger", LEDGER, "--state", state, "--ask", ask_id, *(["--note", note] if note else []))


def test_ask_state_backlog_renders_under_its_own_heading_instead_of_lost(capsys):
    shell = desk_shell()
    ask(shell)
    ask(shell, text="a second ask")
    aged(shell, "ask/000001", 45)
    aged(shell, "ask/000002", 45)

    assert mark(shell, "backlog", note="cc-notes eb2707ec") == 0

    assert capsys.readouterr().out.splitlines()[-1] == "ask/000001 backlog: cc-notes eb2707ec"
    lines = summarize(shell, capsys)
    assert lines[0].endswith("| lost 1 | landed-not-live 0")
    assert lines[1:3] == [f"LOST ask/000002 {LANE}: a second ask", "BACKLOG ask/000001 cc-notes eb2707ec"]
    run(shell, "show", "--ledger", LEDGER, "--asks")
    assert f"ask/000001 {LANE}: {ASK} [BACKLOG]" in capsys.readouterr().out.splitlines()


@pytest.mark.parametrize(
    ("state", "shown"),
    [("backlog", "BACKLOG"), ("live", "LIVE"), ("lost", "LOST")],
)
def test_ask_state_overrides_the_computed_state_of_a_fresh_ask(state, shown, capsys):
    shell = desk_shell()
    ask(shell)
    mark(shell, state)
    capsys.readouterr()

    run(shell, "show", "--ledger", LEDGER, "--asks")

    assert capsys.readouterr().out.splitlines() == [f"ask/000001 {LANE}: {ASK} [{shown}]"]


def test_ask_state_live_outranks_a_delivered_ask_the_pipeline_has_not_reached(capsys):
    shell = desk_shell()
    ask(shell)
    report(shell)
    shell.fields(PR).update(state="landed", landed_at=stamp(timedelta(minutes=-5)))
    mark(shell, "live")

    lines = summarize(shell, capsys)

    assert lines[0].endswith("| lost 0 | landed-not-live 0")


def test_the_ledger_wide_live_marker_does_not_clobber_a_per_ask_state(capsys):
    shell = desk_shell()
    ask(shell)
    aged(shell, "ask/000001", 45)
    mark(shell, "backlog", note="cc-notes eb2707ec")

    run(shell, "live", "--ledger", LEDGER, "--at", stamp(timedelta(minutes=-1)))

    lines = summarize(shell, capsys)
    assert lines[0].endswith("| lost 0 | landed-not-live 0")
    assert lines[1] == "BACKLOG ask/000001 cc-notes eb2707ec"


def test_ask_state_open_restores_the_computed_state(capsys):
    shell = desk_shell()
    ask(shell)
    aged(shell, "ask/000001", 45)
    mark(shell, "backlog", note="cc-notes eb2707ec")

    mark(shell, "open")

    lines = summarize(shell, capsys)
    assert lines[0].endswith("| lost 1 | landed-not-live 0")
    assert lines[1] == f"LOST ask/000001 {LANE}: {ASK}"


def test_ask_state_leaves_a_dropped_ask_dropped(capsys):
    shell = desk_shell()
    ask(shell)
    run(shell, "drop", "--ledger", LEDGER, "--ask", "ask/000001", "--reason", "owner withdrew it")
    mark(shell, "backlog", note="cc-notes eb2707ec")

    assert not [line for line in summarize(shell, capsys)[1:] if "ask/" in line]


def test_ask_state_refuses_an_ask_the_ledger_does_not_hold():
    with pytest.raises(SystemExit, match="no ask ask/000009"):
        mark(desk_shell(), "backlog", ask_id="ask/000009")


def test_ask_state_needs_an_ask_id():
    with pytest.raises(SystemExit, match="--ask ID"):
        run(desk_shell(), "ask", "--ledger", LEDGER, "--state", "backlog")


def test_ask_without_a_state_still_needs_text_lane_and_accept():
    with pytest.raises(SystemExit, match="--text, --lane and --accept"):
        run(desk_shell(), "ask", "--ledger", LEDGER, "--text", ASK)
