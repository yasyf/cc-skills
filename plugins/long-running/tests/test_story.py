import os
import time

import pytest

from drive import served, story

ASKED = {"title": "Raise the limit?", "body": "Writes hit the bucket's cap.", "options": ["Raise it", "Shard it"], "recommended": "Raise it"}
TOLD = {
    "format": "cockpit",
    "headline": "Raise the limit?",
    "beats": [{"label": "What happened", "text": "Writes hit the bucket's cap."}],
    "ask": "",
    "options": [{"title": "Raise it", "consequence": "One setting changes.", "priority": "", "metrics": []}, {"title": "Shard it", "consequence": "Each journal writes under 16 prefixes.", "priority": "", "metrics": []}],
}

UNSTAGED = "Not on staging: its staging test has not started"


@pytest.fixture
def writer(demo: str) -> story.Writer:
    return story.Writer(demo)


def asking(*cards: dict) -> dict:
    return {"queue": [{"kind": "question", "card": card} for card in cards], "review": []}


def test_a_question_cards_story_is_written_once_kept_for_a_restarted_dashboard_and_written_anew_when_the_card_changes(writer: story.Writer, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = []
    monkeypatch.setattr(served, "structured", lambda name, rules, prompt, schema, seconds, doing: prompts.append(prompt) or TOLD)
    first, again = asking(ASKED), asking(ASKED)
    writer.tell(first)
    writer.tell(again)
    assert (first["queue"][0]["told"], writer.writing.qsize()) == ({"writing": True}, 1)
    writer.write(*writer.writing.get())
    restarted = asking(ASKED, ASKED | {"options": [*ASKED["options"], "Wait a week"]})
    story.Writer("demo").tell(restarted)
    labelled = TOLD | {"options": [option | {"label": label} for option, label in zip(TOLD["options"], ASKED["options"], strict=True)]}
    assert [item["told"] for item in restarted["queue"]] == [{"story": labelled}, {"writing": True}]
    assert len(prompts) == 1
    assert '"Shard it"' in prompts[0]


def test_a_story_that_tells_another_count_of_options_shows_why_and_is_written_again_only_after_the_retry_wait(writer: story.Writer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(served, "structured", lambda *args: TOLD | {"options": TOLD["options"][:1]})
    shown = asking(ASKED)
    writer.tell(shown)
    writer.write(*writer.writing.get())
    writer.tell(shown)
    assert shown["queue"][0]["told"] == {"error": "the story written for his card drew 1 options for its 2"}
    failed = next(writer.folder.glob("*.failed"))
    os.utime(failed, (time.time() - story.RETRY_SECONDS - 1,) * 2)
    writer.tell(shown)
    assert shown["queue"][0]["told"] == {"writing": True}


def test_only_a_pr_waiting_on_his_tap_gets_a_story_and_a_new_head_gets_its_own(writer: story.Writer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(story, "change", lambda name, entry: {"headline": entry["head"], "beats": []})
    waiting = {"key": "demo-org/demo-app#41", "head": "h1", "lead": None, "staging": UNSTAGED, "group": "you"}
    shown = {"queue": [], "review": [waiting, {"key": "demo-org/demo-app#42", "head": "h2", "lead": None, "staging": UNSTAGED, "group": "not ready"}]}
    writer.tell(shown)
    writer.write(*writer.writing.get())
    writer.tell(shown)
    moved = {"queue": [], "review": [waiting | {"head": "h3"}]}
    writer.tell(moved)
    assert [entry.get("told") for entry in shown["review"]] == [{"story": {"headline": "h1", "beats": []}}, None]
    assert moved["review"][0]["told"] == {"writing": True}


def test_a_prs_story_is_written_anew_once_its_staging_line_moves(writer: story.Writer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(story, "change", lambda name, entry: {"headline": entry["staging"], "beats": []})
    waiting = {"key": "demo-org/demo-app#41", "head": "h1", "lead": None, "staging": UNSTAGED, "group": "you"}
    writer.tell({"queue": [], "review": [waiting]})
    writer.write(*writer.writing.get())
    tested = {"queue": [], "review": [waiting | {"staging": "Tested on staging: plat-usw2-staging at f3a6f23, passed, its proof a file on the Mac"}]}
    writer.tell(tested)
    assert tested["review"][0]["told"] == {"writing": True}
