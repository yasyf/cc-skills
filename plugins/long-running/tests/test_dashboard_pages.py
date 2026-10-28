import datetime as dt

import pytest
from brief import Curator
from pages import PAGES, REVIEW_SCHEMA, open_question, plan_slot, review_digest, review_slot, stamp, waiting_since
from server import bot_words, unanswered

NOW = dt.datetime(2026, 10, 27, 23, 0, tzinfo=dt.timezone.utc)
ROW = {
    "number": 43210,
    "head": "886920e",
    "title": "Editing a message forks the chat",
    "change": "Today every edit is refused; after this, the edit starts a new chat.",
    "plan": "Matches the plan.",
    "risk": "Low: forks into another team are refused first.",
    "review": "Codex found nothing; the reviewer bot approved.",
    "blocker": "Your approval.",
}
COUNTED = {**ROW, "number": 43211, "review": "Codex found three problems in an earlier version."}
CARD = {
    "id": "card-release-plan",
    "title": "Release plan: ten releases until everyone is on the new build",
    "summary": "Old sand 0.162.1852 rode the api release.",
    "recommended": "Release now",
    "options": [{"label": "Release now"}, {"label": "Wait for the browser pair"}],
    "parts": [
        {"kind": "record", "facts": []},
        {"kind": "choice", "prompt": "Release the SandSQL replica chain now?", "answer": None},
    ],
}


def drive(*rows, cards=(), groups=()):
    board = {"rows": [dict(row) for row in rows], "cards": [dict(card) for card in cards]}
    stamp(board)
    needs = [{"kind": "card", "card": card} for card in board["cards"]]
    needs += [{"kind": "prs", "key": key, "group": "Tooling PRs whose workers died, re-owned", "relation": "Three small tooling fixes.", "prs": board["rows"]} for key in groups]
    return {"board": board, "needs": needs, "records": [], "lanes": [], "workers": [], "prs": {}, "drive": {"prs": []}, "fetch": lambda kinds, since: []}


def rewrite_of(model, row, **fields):
    keyed = next(r for r in model["board"]["rows"] if r["number"] == row["number"])
    return {"key": keyed["key"], **{k: keyed[k] for k in ("title", "change", "plan", "risk", "review", "blocker")}, **fields}


def test_rows_that_pass_the_gate_need_no_run():
    assert review_digest(drive(ROW), {}, None, NOW) is None


def test_only_a_failing_row_goes_to_the_model_with_its_failing_fields():
    facts = review_digest(drive(ROW, COUNTED), {}, None, NOW)
    assert [list(row["problems"]) for row in facts["rows"]] == [["review"]]


def test_a_row_rewritten_from_the_same_words_needs_no_new_run():
    model = drive(COUNTED)
    previous = {"rows": [rewrite_of(model, COUNTED, review="Codex found the edit lost its parent, now kept.")], "groups": [], "cards": []}
    assert review_digest(model, {}, previous, NOW) is None


def test_a_new_head_is_rewritten_again():
    previous = {"rows": [rewrite_of(drive(COUNTED), COUNTED, review="Codex found the edit lost its parent, now kept.")], "groups": [], "cards": []}
    assert len(review_digest(drive({**COUNTED, "head": "1111111"}), {}, previous, NOW)["rows"]) == 1


def test_a_board_correction_at_the_same_head_is_rewritten_again():
    previous = {"rows": [rewrite_of(drive(COUNTED), COUNTED, review="Codex found the edit lost its parent, now kept.")], "groups": [], "cards": []}
    corrected = {**COUNTED, "blocker": "Conflicts with its base, so it cannot land until its worker updates it."}
    facts = review_digest(drive(corrected), {}, previous, NOW)
    assert facts["rows"][0]["previous_rewrite"] is None


def test_a_question_goes_to_the_model_with_its_open_step():
    facts = review_digest(drive(cards=[CARD]), {}, None, NOW)
    assert [(card["open_question"], card["recommended"]) for card in facts["cards"]] == [("Release the SandSQL replica chain now?", "Release now")]


def test_an_answered_question_has_no_open_step():
    answered = {**CARD, "parts": [{"kind": "choice", "prompt": "Release now?", "answer": {"optionIds": ["now"]}}]}
    assert open_question(answered) is None


def test_a_group_with_a_rewrite_at_its_key_needs_no_new_run():
    model = drive(ROW, groups=["g1"])
    assert review_digest(model, {}, None, NOW)["groups"][0]["title"] == "Tooling PRs whose workers died, re-owned"
    previous = {"rows": [], "groups": [{"key": "g1", "title": "Dashboard test fixes", "relation": "The test naming fix lands first."}], "cards": []}
    assert review_digest(model, {}, previous, NOW) is None


@pytest.mark.parametrize(("path", "slot"), [(("rows", 0, "review"), "headline"), (("groups", 1, "relation"), "latest"), (("cards", 0, "question"), "latest"), (("cards", 0, "answers", 1), "title"), (("cards", 0, "key"), None)])
def test_review_slots_cover_rows_groups_and_cards(path, slot):
    assert review_slot(path) == slot


@pytest.mark.parametrize("kind", ["done", "stopped", "stand-down"])
def test_a_finished_worker_wakes_the_work_page(kind):
    curator = Curator({}, None, None, next(page for page in PAGES if page.name == "work"))
    curator.poke(kind)
    assert curator.dirty.is_set()


def test_a_question_whose_open_step_changes_is_rewritten_again():
    before = drive(cards=[CARD])
    previous = {"rows": [], "groups": [], "cards": [{"key": before["board"]["cards"][0]["key"], "question": "Release the SandSQL replica chain now?", "means": "Starts it now."}]}
    moved = {**CARD, "parts": [CARD["parts"][0], {"kind": "choice", "prompt": "Release the browser live-update pair now?", "answer": None}]}
    assert review_digest(before, {}, previous, NOW) is None
    assert review_digest(drive(cards=[moved]), {}, previous, NOW)["cards"][0]["previous_rewrite"] is None


def test_a_landed_change_name_is_gated_as_a_title():
    assert plan_slot(("parts", 2, "landed", 0)) == "title"


def test_a_question_leaves_waiting_once_his_answer_names_it():
    cards = [{"id": "card-r2-failed-runs"}, {"id": "card-r2-retro"}, {"id": "card-browser-signal-token"}]
    words = [
        {"title": "Owner board write-in 10:39 AM Oct 27 on card-r2-failed-runs: re-trigger all the runs"},
        {"title": "Board retry-fork-point-choice (card-r2-retro), 7:25 PM Oct 26: where does a failed run retry"},
        {"title": "Owner 3:47 PM Oct 27: a real UI pass"},
    ]
    assert [card["id"] for card in unanswered(cards, words)] == ["card-browser-signal-token"]


def test_each_answer_label_is_its_own_string():
    card = REVIEW_SCHEMA["properties"]["cards"]["items"]["properties"]
    assert card["answers"] == {"type": "array", "items": {"type": "string"}}


def test_work_waits_from_the_change_s_last_push_like_review():
    records = [
        {"kind": "head", "at": "2026-10-28T00:10:00Z", "refs": {"prs": [43686]}},
        {"kind": "review", "at": "2026-10-28T00:15:00Z", "refs": {"prs": [43686]}},
        {"kind": "head", "at": "2026-10-28T01:17:00Z", "refs": {"prs": [43686]}},
        {"kind": "head", "at": "2026-10-28T01:30:00Z", "refs": {"prs": [43700]}},
        {"kind": "state", "at": "2026-10-28T01:40:00Z", "refs": None},
    ]
    assert waiting_since(records, 43686) == "6:17 PM"
    assert waiting_since(records, 49001) == ""


COMPANION = "<!-- pr-reviewer bot companion -->\n\n### ELI5\n\nEditing a message now starts a new chat instead of failing.\n\n### Read in this order\n\n| File | What it settles |\n\n<sub>`606342b`</sub>"
SUMMARY = "<!-- pr-reviewer bot summary -->\n\n> [!TIP]\n> **No blocking findings**\n\n**Since [`606342b`](x):** `startHeadless` arms its timers.\n\nA CLI that fails to start now fails the test.\n\n<details><summary>All findings</summary>\n\n| Finding |\n\n</details>"


def test_the_review_page_quotes_the_bot_s_eli5_section_alone():
    assert bot_words([SUMMARY, COMPANION])["eli5"] == "Editing a message now starts a new chat instead of failing."


def test_without_an_eli5_the_bot_s_plain_summary_stands_in():
    assert bot_words([SUMMARY]) == {"eli5": None, "summary": "A CLI that fails to start now fails the test."}


def test_without_an_eli5_the_companion_s_title_says_what_it_does_and_an_incremental_summary_is_dropped():
    companion = "<!-- pr-reviewer bot companion -->\n### GitHub merge state gates untracked dev PRs\n\n```mermaid\nflowchart LR\n```"
    summary = "<!-- pr-reviewer bot summary -->\n\n> [!TIP]\n> **No blocking findings**\n\n**Since [`7bed615`](x):** Adds a fallback.\n\nNo new findings at this head. The existing thread still applies.\n\n<details></details>"
    assert bot_words([summary, companion]) == {"eli5": "GitHub merge state gates untracked dev PRs", "summary": None}


def test_a_companion_section_that_is_not_a_diagram_title_is_not_taken_for_what_the_change_does():
    companion = "<!-- pr-reviewer bot companion -->\n<!-- ships-with -->\n### Ships with\n- `api`, `dashboard` ship in the same release\n<!-- /ships-with -->"
    assert bot_words([companion])["eli5"] is None

