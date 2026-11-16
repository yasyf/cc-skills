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
