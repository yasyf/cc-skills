from conftest import REAL_ANSWER
from drive import answer

ROWS = "- job: staging test of memory running; its last line: c7 fails, build https://buildkite.com/forge/release/builds/17166\n  - PR https://github.com/Forge-AI/monorepo/pull/57072: open, checks pass"


def test_answer_from_rows_posts():
    text, why = answer.reply_of(f"{answer.ANSWER}\nRed: <https://buildkite.com/forge/release/builds/17166|the memory test build> fails one case.", ROWS)
    assert text and why == "answered from the drive's rows"


def test_escalate_posts_nothing():
    assert answer.reply_of(f"{answer.ESCALATE} he gives an order", ROWS) == (None, "he gives an order")


def test_link_the_rows_do_not_print_escalates():
    text, why = answer.reply_of(f"{answer.ANSWER}\nSee <https://github.com/Forge-AI/monorepo/pull/99999|the fix>.", ROWS)
    assert text is None and "99999" in why


def test_number_the_rows_do_not_print_escalates():
    text, why = answer.reply_of(f"{answer.ANSWER}\nBuild 17999 is red.", ROWS)
    assert text is None and "17999" in why


def test_long_answer_escalates():
    text, why = answer.reply_of(f"{answer.ANSWER}\n" + "word " * 90, ROWS)
    assert text is None and "words" in why


def test_a_claimed_message_is_answered_once(monkeypatch, tmp_path):
    runs = []
    monkeypatch.setattr(answer, "claim_path", lambda name, ts: tmp_path / f"{ts}.json")
    monkeypatch.setattr(answer, "found_answer", lambda *args: runs.append(args) or {"answered": "https://slack/p1", "why": "answered from the drive's rows"})
    link = "https://x.slack.com/archives/D1/p1794806180473129?thread_ts=1794774184.502879&cid=D1"
    first = REAL_ANSWER("d", link, "status?")
    assert REAL_ANSWER("d", link, "status?") == {"answered": first["answered"], "why": first["why"]}
    assert len(runs) == 1


def test_a_link_with_no_words_naming_it_is_refused():
    text, why = answer.reply_of(f"{answer.ANSWER}\nMerged: <https://github.com/Forge-AI/monorepo/pull/57072|#57072>.", ROWS)
    assert text is None and "no words" in why


def test_a_bare_link_is_refused():
    text, why = answer.reply_of(f"{answer.ANSWER}\nMerged: https://github.com/Forge-AI/monorepo/pull/57072", ROWS)
    assert text is None and "bare" in why


def test_drive_ids_and_drive_words_are_refused():
    assert "j6639" in answer.reply_of(f"{answer.ANSWER}\nThe memory test j6639 fails one case.", ROWS + " j6639")[1]
    assert "handoff" in answer.reply_of(f"{answer.ANSWER}\nThe memory test waits on a handoff.", ROWS)[1]


def test_a_report_with_nothing_new_posts_nothing():
    assert answer.report_of(f"{answer.NOTHING} no job moved", ROWS) == (None, answer.NOTHING, "no job moved")


def test_a_report_line_is_checked_as_an_answer_is():
    assert answer.report_of(f"{answer.POST}\nThe memory test fails one case on <https://buildkite.com/forge/release/builds/17166|its staging build>.", ROWS)[1] == answer.POST
    assert answer.report_of(f"{answer.POST}\nBuild 17999 is red.", ROWS)[1] is None


def test_a_verb_written_with_a_colon_reads_as_that_verb():
    assert answer.report_of(f"{answer.NOTHING}: no job moved", ROWS) == (None, answer.NOTHING, "no job moved")
