from conftest import REAL_ANSWER, edit
from drive import answer, state

ROWS = "- job: staging test of memory running; its last line: c7 fails, build https://buildkite.com/forge/release/builds/17166\n  - PR https://github.com/Forge-AI/monorepo/pull/57072: open, checks pass"


def test_answer_from_rows_posts():
    text, why = answer.reply_of(f"{answer.ANSWER}\nRed: <https://buildkite.com/forge/release/builds/17166|the memory test build> fails one case.", ROWS)
    assert text and why == "answered from the drive's rows"


def test_escalate_posts_nothing():
    assert answer.reply_of(f"{answer.ESCALATE} he gives an order", ROWS) == (None, "he gives an order")


def test_a_link_the_rows_do_not_print_posts_with_a_warning():
    text, why = answer.reply_of(f"{answer.ANSWER}\nSee <https://github.com/Forge-AI/monorepo/pull/99999|the fix>.", ROWS)
    assert text and "warning" in why and "99999" in why


def test_a_number_the_rows_do_not_print_posts_with_a_warning():
    text, why = answer.reply_of(f"{answer.ANSWER}\nBuild 17999 is red.", ROWS)
    assert text and "warning" in why and "17999" in why


def test_a_long_answer_posts_with_a_warning():
    text, why = answer.reply_of(f"{answer.ANSWER}\n" + "word " * 90, ROWS)
    assert text and "warning" in why and "words" in why


def test_a_claimed_message_is_answered_once(monkeypatch, tmp_path):
    runs = []
    monkeypatch.setattr(answer, "claim_path", lambda name, ts: tmp_path / f"{ts}.json")
    monkeypatch.setattr(answer, "found_answer", lambda *args: runs.append(args) or {"answered": "https://slack/p1", "why": "answered from the drive's rows"})
    link = "https://x.slack.com/archives/D1/p1794806180473129?thread_ts=1794774184.502879&cid=D1"
    first = REAL_ANSWER("d", link, "status?")
    assert REAL_ANSWER("d", link, "status?") == {"answered": first["answered"], "why": first["why"]}
    assert len(runs) == 1


def test_a_link_with_no_words_naming_it_posts_with_a_warning():
    text, why = answer.reply_of(f"{answer.ANSWER}\nMerged: <https://github.com/Forge-AI/monorepo/pull/57072|#57072>.", ROWS)
    assert text and "no words" in why


def test_a_bare_link_posts_with_a_warning():
    text, why = answer.reply_of(f"{answer.ANSWER}\nMerged: https://github.com/Forge-AI/monorepo/pull/57072", ROWS)
    assert text and "bare" in why


def test_drive_ids_and_drive_words_post_with_a_warning():
    assert "j6639" in answer.reply_of(f"{answer.ANSWER}\nThe memory test j6639 fails one case.", ROWS + " j6639")[1]
    assert "handoff" in answer.reply_of(f"{answer.ANSWER}\nThe memory test waits on a handoff.", ROWS)[1]


def test_his_thread_question_with_no_card_drawn_yet_reads_so_in_the_rows(demo, monkeypatch):
    with edit(demo) as data:
        data["orders"]["o1"] = state.new_order("ship the run page", "terminal", ["D1", "1794774184.502879"], {}, state.stamp()) | {"jobs": ["j1"]}
        data["jobs"]["j1"] = state.new_job("o1", "builder", "ship the run page", [], state.stamp()) | {"state": "waiting", "questions": [state.new_question(None, "Ring or knock?", state.stamp())]}
    asked = []
    monkeypatch.setattr(answer, "ask", lambda rows, words: asked.append(rows) or (f"{answer.ESCALATE} he asks", {}))
    answer.found_answer(demo, "https://x.slack.com/archives/D1/p1794806180473129", "status?", "D1", "1794774184.502879", True)
    assert "question, no card drawn yet, asking him: Ring or knock?" in asked[0]
