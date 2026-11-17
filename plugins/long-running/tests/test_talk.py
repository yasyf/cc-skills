import json
import os
import re
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import HELD_RELEASES, STANDING_FREEZE, Answers, Checked, Jev, Thread, edit
from drive import (
    agents,
    brief,
    cli,
    dashboard,
    picture,
    providers,
    reads,
    refs,
    render,
    settled,
    slack,
    state,
    talk,
    tools,
    view,
    watch,
)
from drive.state import DriveError

WAITS = "demo:j1-7"
CARD_URL = "https://acme.slack.com/archives/D123/p1794300000000100"
PR_URL = "https://github.com/yasyf/cc-skills/pull/7"
POSTED = "https://acme.slack.com/archives/D123/p1794300000000900"
QUESTION = "Lock each chat's database, or the whole table?"
ASKED = "2026-11-10T20:59:00-08:00"
AWAY = "2026-11-10T21:30:00-08:00"
AMENDMENTS = agents.amendments
DRAW = talk.draw
SSQL = "are we doing the ssql release foe it"
FOOTERS = "why didnt our release footers whatever catch this and bring ssql along with API"
PACIFIC = timezone(timedelta(hours=-8))


@pytest.fixture
def posts(communicator: str, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    sent: list[tuple[str, ...]] = []
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "https://acme.slack.com/", "questions": [question_card(CARD_URL, QUESTION, WAITS)], "pr_cards": [], "closed": []})

    def cc_slack(*argv: str) -> dict:
        if argv[0] == "dm-status" and not {"--", "--card", "--file", "--tap-pr"} & set(argv):
            raise slack.SlackError("dm-status needs a card, text, or files")
        sent.append(argv)
        reply = {"permalink": POSTED, "thread_ts": "1794300000.000900"}
        return reply | {"card_permalink": POSTED} if argv[0] == "dm-status" and not {"--", "--card", "--file"} & set(argv) else reply

    monkeypatch.setattr(slack, "cc_slack", cc_slack)
    with edit(communicator) as data:
        data["prs"]["yasyf/cc-skills#7"] = state.new_pr(PR_URL, "j1", "o1") | {"title": "New: \U0001f680 the run page shows its history"}
    return sent


def question_card(url: str, question: str, waits: str) -> dict:
    card = {"title": question, "body": "Each chat's database or the whole table.", "options": ["Each chat"], "recommended": "Each chat", "waits": waits}
    return {"question": question, "options": card["options"], "recommended": card["recommended"], "asked": ASKED, "url": url, "card": card}


def said(name: str) -> list[dict]:
    return [event for event, _ in state.read_events(name) if event["kind"] == "said"]


@pytest.mark.parametrize(
    ("post", "fields", "rows"),
    [
        ({"text": "An update"}, [], []),
        ({"edit": POSTED, "text": "An updated line"}, [], []),
        ({"channel_id": "U123", "job": "j1", "text": "A question"}, [], [("jobs", "j1")]),
        ({"tap_prs": [PR_URL], "text": "A PR card"}, [], [("prs", "yasyf/cc-skills#7")]),
        ({"channel_id": "C1", "card": {"title": "A question"}, "text": "A card"}, [("slack",)], []),
    ],
)
def test_post_validation_reads_only_addressed_rows(communicator: str, posts: list[tuple[str, ...]], post: dict, fields: list[tuple[str, ...]], rows: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = []
    fetched = []
    stored = state.stored
    saved_row = state.saved_row

    def selected(name: str, fields: tuple[str, ...] | None = None) -> dict:
        loaded.append(fields)
        return stored(name, fields)

    def selected_row(name: str, collection: str, key: str) -> dict | None:
        fetched.append((collection, key))
        return saved_row(name, collection, key)

    monkeypatch.setattr(state, "load", lambda *args: pytest.fail("post validation must not shape the drive"))
    monkeypatch.setattr(state, "stored", selected)
    monkeypatch.setattr(state, "saved_row", selected_row)
    monkeypatch.setattr(slack, "operators", lambda: {"D123", "C1"})
    if post.get("job"):
        with pytest.raises(DriveError, match="job j1 is no job of this drive"):
            talk.check(communicator, post)
    else:
        talk.check(communicator, post)
    assert loaded == fields
    assert fetched == rows


def test_one_post_links_each_reference_the_way_slack_and_the_dashboard_each_show_it(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = []
    load = state.load

    def selected(name: str, fields: tuple[str, ...] | None = None) -> dict:
        loaded.append(fields)
        return load(name, fields)

    monkeypatch.setattr(state, "load", selected)
    assert talk.say(demo, {"text": "{pr:7} waits on {card:demo:j1-7}", "thread_ts": "1794200000.000100"}) == f"POSTED {POSTED}"
    assert posts == [
        ("dm-status", "--agent", "claude", "--drive", "demo", "--as-holder", "--thread", "1794200000.000100", "--", f"<{PR_URL}|#7> waits on <{CARD_URL}|{QUESTION}>")
    ]
    assert ("name", "roles", "subcoordinators", "communicators") in loaded
    [kept] = said(demo)
    assert kept["text"] == f"[the run page shows its history]({PR_URL}) waits on [{QUESTION}](#/queue/question/1794300000.000100)"
    assert (kept["url"], kept["thread"]) == (POSTED, "1794300000.000900")


def test_an_answer_on_a_project_order_goes_in_his_dm_messages_thread_and_keeps_the_messages_it_answers(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack, "operator_channels", lambda: [{"id": "C777", "name": "him-drive-demo-iris"}])
    with edit(demo) as data:
        data["slack"]["channels"] = {"Iris": {"id": "C777", "name": "him-drive-demo-iris"}}
        data["orders"]["o1"] = state.new_order("ship the run page", "D123", None, {}, state.stamp()) | {"project": "Iris"}
    talk.posted(demo, {"text": "The fix is up.", "asked": json.dumps(["1794200000.000300"])}, "o1")
    talk.posted(demo, {"text": "The fix is in."})
    assert posts == [
        ("dm-status", "--agent", "claude", "--drive", "demo", "--as-holder", "--thread", "1794200000.000300", "--", "The fix is up."),
        ("dm-status", "--agent", "claude", "--drive", "demo", "--as-holder", "--", "The fix is in."),
    ]
    assert [(event["channel"], event["answers"]) for event in said(demo)] == [(None, ["1794200000.000300"]), (None, [])]


def test_a_card_of_no_project_asks_at_the_root_of_the_drives_own_channel_while_a_status_line_of_none_stays_in_his_dm(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack, "operator_channels", lambda: [{"id": "C777", "name": "him-drive-demo-iris"}, {"id": "C888", "name": "him-drive-demo-tooling"}])
    with edit(demo) as data:
        data["slack"]["channels"] = {"Iris": {"id": "C777", "name": "him-drive-demo-iris"}, "Drive tooling": {"id": "C888", "name": "him-drive-demo-tooling"}}
        data["orders"]["o1"] = state.new_order("design the store", "D123", None, {}, state.stamp()) | {"jobs": ["j1"]}
        data["jobs"]["j1"] = state.new_job("o1", "builder", "design the store", [], state.stamp())
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "Keep the old store?", state.stamp()) | {"waits": "demo:j1-8"})
    talk.say(demo, {"card": {"title": "Keep the old store?", "waits": "demo:j1-8"}})
    talk.say(demo, {"text": "The fix is in."})
    assert [argv[argv.index("--channel") + 1] if "--channel" in argv else None for argv in posts] == ["C888", None]


def test_once_the_drive_has_its_own_channel_a_post_of_a_project_with_none_makes_its_channel_an_incident_line_goes_there_too_and_a_status_line_of_no_project_goes_to_the_drives(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    made = {"him-drive-demo-iris": {"id": "C777", "name": "him-drive-demo-iris"}}
    sent = slack.cc_slack
    monkeypatch.setattr(slack, "cc_slack", lambda *argv, **_: made[argv[1]] if argv[0] == "operator-channel" else sent(*argv))
    with edit(demo) as data:
        data["slack"]["channels"] = {"": {"id": "C666", "name": "him-drive-demo"}}
        data["orders"]["o1"] = state.new_order("ship the run page", "D123", None, {}, state.stamp()) | {"project": "Iris"}
        data["orders"]["o2"] = state.new_order("fix the outage", "D123", None, {}, state.stamp()) | {"project": "Iris", "incident": True}
    talk.posted(demo, {"text": "The fix is up."}, "o1")
    talk.posted(demo, {"text": "The fix is in."})
    talk.posted(demo, {"text": "The outage is fixed."}, "o2")
    assert [argv[argv.index("--channel") + 1] if "--channel" in argv else None for argv in posts] == ["C777", "C666", "C777"]
    assert state.load(demo)["slack"]["channels"]["Iris"] == made["him-drive-demo-iris"]


def test_a_priority_orders_post_makes_its_bound_seats_own_channel_while_its_projects_other_orders_stay_in_the_projects(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    made = {"him-drive-demo-iris": {"id": "C777", "name": "him-drive-demo-iris"}, "him-drive-demo-prio-fix-the-lock": {"id": "C790", "name": "him-drive-demo-prio-fix-the-lock"}}
    sent = slack.cc_slack
    monkeypatch.setattr(slack, "cc_slack", lambda *argv, **_: made[argv[1]] if argv[0] == "operator-channel" else sent(*argv))
    with edit(demo) as data:
        data["slack"]["channels"] = {"": {"id": "C666", "name": "him-drive-demo"}}
        data["orders"]["o1"] = state.new_order("ship the run page", "D123", None, {}, state.stamp()) | {"project": "Iris"}
        data["orders"]["o2"] = state.new_order("fix the lock", "D123", None, {}, state.stamp()) | {"project": "Iris", "priority": state.priority_seat("o2")}
        data["subcoordinators"][state.priority_seat("o2")] = {"session": "seat-1", "cwd": "/w/demo", "from": 0}
    talk.posted(demo, {"text": "The page is up."}, "o1")
    talk.posted(demo, {"text": "The lock is fixed."}, "o2")
    assert [argv[argv.index("--channel") + 1] for argv in posts] == ["C777", "C790"]
    assert state.load(demo)["slack"]["channels"][state.priority_seat("o2")] == made["him-drive-demo-prio-fix-the-lock"]


def test_an_order_filed_under_a_priority_orders_project_and_workstream_posts_in_that_seats_channel(demo: str, posts: list[tuple[str, ...]]) -> None:
    with edit(demo) as data:
        data["slack"]["channels"] = {"": {"id": "C666", "name": "him-drive-demo"}, "Platform": {"id": "C700", "name": "him-drive-demo-platform"}, state.priority_seat("o1"): {"id": "C790", "name": "him-drive-demo-prio-releases"}}
        data["orders"]["o1"] = state.new_order("cut the release", "D123", None, {}, state.stamp()) | {"project": "Platform", "workstream": "Releases", "priority": state.priority_seat("o1")}
        data["orders"]["o2"] = state.new_order("release everything but SandSQL", "D123", None, {}, state.stamp()) | {"project": "Platform", "workstream": "Releases"}
        data["orders"]["o3"] = state.new_order("fix the router", "D123", None, {}, state.stamp()) | {"project": "Platform", "workstream": "Routing"}
        data["subcoordinators"][state.priority_seat("o1")] = {"session": "seat-1", "cwd": "/w/demo", "from": 0}
    for order_id in ("o2", "o3"):
        talk.posted(demo, {"text": "Moving."}, order_id)
    assert [argv[argv.index("--channel") + 1] for argv in posts] == ["C790", "C700"]


def test_the_drives_own_channel_posts_one_line_per_project_channel_linking_it_and_its_queue(demo: str, unposted_waits: Callable[..., dict[str, str]]) -> None:
    with edit(demo) as data:
        data["slack"]["channels"] = {"": {"id": "C666", "name": "him-drive-demo"}, "Iris": {"id": "C777", "name": "him-drive-demo-iris"}}
    board = dashboard.link(demo)
    assert unposted_waits(demo, state.load(demo), view.NO_CARDS, state.now())["C666"] == f"*<{board}#/|demo>* projects:\n<#C777> · <{board}#/queue/all?project=Iris|nothing waiting>"


def test_drive_channels_moves_each_open_card_of_his_dm_to_its_projects_channel_one_of_no_project_to_the_drives_and_follows_it_there(run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    order_id, job_id = "o1", "j1"
    with edit(demo) as data:
        data["orders"][order_id] = state.new_order("ship the run page", "D123", None, {}, state.stamp()) | {"project": "Iris", "jobs": [job_id]}
        data["jobs"][job_id] = state.new_job(order_id, "builder", "ship the run page", [], state.stamp()) | {"waits": [CARD_URL]}
        data["prs"]["yasyf/cc-skills#7"] = state.new_pr(PR_URL, job_id, order_id)
    iris, drive, moved = {"id": "C777", "name": "him-drive-demo-iris"}, {"id": "C888", "name": "him-drive-demo-tooling"}, "https://acme.slack.com/archives/C777/p1794300000000950"
    made = {"him-drive-demo": {"id": "C666", "name": "him-drive-demo"}, iris["name"]: iris, drive["name"]: drive}
    unfiled = question_card("https://acme.slack.com/archives/D123/p1794300000000300", "Which drive?", "demo:j99-7")
    pr_card = {"prs": [PR_URL], "asked": ASKED, "url": "https://acme.slack.com/archives/D123/p1794300000000200", "card": {"title": "Merge #7?", "waits": "merge:yasyf/cc-skills#7"}}
    asked = question_card(CARD_URL, QUESTION, f"demo:{job_id}-7")
    sent: list[tuple[str, ...]] = []
    monkeypatch.setattr(slack, "operator_channels", lambda: [made["him-drive-demo"], iris, drive])
    monkeypatch.setattr(slack, "cc_slack", lambda *argv, **_: sent.append(argv) or (made[argv[1]] if argv[0] == "operator-channel" else {"permalink": moved}))
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "https://acme.slack.com/", "questions": [asked, unfiled], "pr_cards": [pr_card], "closed": []})

    assert run("channels")[1].splitlines() == ["no channel yet for the drive: --make makes it", "no channel yet for Drive tooling: --make makes it"]
    assert sent == []
    assert run("channels", "--make", "--move-cards")[1].splitlines()[-1] == "3 open card(s) of his DM moved to their project's channel"
    assert [argv for argv in sent if argv[0] == "move-card"] == [
        ("move-card", "--url", CARD_URL, "--channel", "C777"),
        ("move-card", "--url", unfiled["url"], "--channel", "C888"),
        ("move-card", "--url", pr_card["url"], "--channel", "C777"),
    ]
    data = state.load(demo)
    assert data["jobs"][job_id]["waits"] == [asked["card"]["waits"]]
    assert ["C777", "1794300000.000950"] in [where[:2] for where in data["orders"][order_id]["threads"]]


def test_replaying_o2068_a_post_in_an_orders_thread_recorded_on_no_order_is_still_its_last_line(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    reply = "https://acme.slack.com/archives/D123/p1794300000000900?thread_ts=1794200000.000100"
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: posts.append(argv) or {"permalink": reply, "thread_ts": "1794200000.000100"})
    with edit(demo) as data:
        data["orders"]["o1"] = state.new_order("ship the run page", "D123", ("D123", "1794200000.000100"), {}, state.stamp())
        data["orders"]["o2"] = state.new_order("fix the lock", "D123", ("D123", "1794200000.000200"), {}, state.stamp())
    talk.say(demo, {"text": "On it.", "thread_ts": "1794200000.000100"})
    orders = state.load(demo)["orders"]
    assert (orders["o1"]["last_line"], orders["o2"]["last_line"]) == (state.stamp(slack.sent_at(slack.posted(reply, ""))), None)


def test_a_post_answering_his_words_names_the_message_its_permalink_names_and_an_edit_names_none(demo: str, posts: list[tuple[str, ...]]) -> None:
    talk.posted(demo, {"text": "It is up.", "asked": ["is it up?", "https://acme.slack.com/archives/D123/p1794200000000500"]})
    talk.posted(demo, {"edit": POSTED, "text": "It is in.", "asked": ["is it up?"]})
    assert [event["answers"] for event in said(demo)] == [["1794200000.000500"], []]


def test_a_card_named_in_its_own_thread_reads_this_card_with_no_link(demo: str, posts: list[tuple[str, ...]]) -> None:
    talk.say(demo, {"text": "The design job behind {card:demo:j1-7} has your question", "thread_ts": "1794300000.000100"})
    talk.say(demo, {"text": "{card:demo:j1-7|The lock question} has your question", "thread_ts": "1794300000.000100"})
    talk.say(demo, {"text": "Why {card:demo:j1-7} asks: one lock per chat", "about": WAITS})
    assert posts == [
        ("dm-status", "--agent", "claude", "--drive", "demo", "--as-holder", "--thread", "1794300000.000100", "--", "The design job behind this card has your question"),
        ("dm-status", "--agent", "claude", "--drive", "demo", "--as-holder", "--thread", "1794300000.000100", "--", "The lock question has your question"),
        ("dm-status", "--agent", "claude", "--drive", "demo", "--as-holder", "--about", WAITS, "--", "Why this card asks: one lock per chat"),
    ]
    assert said(demo)[0]["text"] == f"The design job behind [{QUESTION}](#/queue/question/1794300000.000100) has your question"


def test_an_edit_links_each_reference_as_a_post_does_and_the_dashboard_shows_its_new_words_in_place(demo: str, posts: list[tuple[str, ...]]) -> None:
    talk.say(demo, {"text": "{pr:7} is up", "thread_ts": "1794200000.000100"})
    edit = {"edit": POSTED, "text": "{pr:7} waits on {card:demo:j1-7}"}
    sent = f"<{PR_URL}|#7> waits on <{CARD_URL}|{QUESTION}>"
    assert talk.say(demo, edit | {"dry_run": True}) == f"DRY edit of {POSTED}, 0 pictures: {sent}"
    assert talk.say(demo, edit) == f"EDITED {POSTED}"
    assert posts[1] == ("edit", "--url", POSTED, "--text", sent)
    assert [event["edited"] for event in said(demo)] == [False, True]
    history = view.history(state.load(demo), (event for event, _ in state.read_events(demo)), "https://acme.slack.com/")
    [shown] = [entry for entry in history if entry["kind"] == "said"]
    assert shown["text"] == f"[the run page shows its history]({PR_URL}) waits on [{QUESTION}](#/queue/question/1794300000.000100)"


@pytest.mark.parametrize(
    ("extra", "refused"),
    [
        ({"card": {"title": QUESTION, "waits": WAITS}}, "also passes card. Redraw a card with drive job card"),
        ({"thread_ts": "1794200000.000100"}, "also passes thread_ts"),
        ({"text": ""}, "an edit needs text"),
        ({"text": "Who takes it:\n```mermaid\nflowchart TD\n  chat --> lock\n```"}, "an edit of a Slack message cannot add"),
    ],
)
def test_an_edit_carrying_what_an_edit_of_a_slack_message_cannot_change_edits_nothing(
    demo: str, posts: list[tuple[str, ...]], extra: dict, refused: str
) -> None:
    with pytest.raises(DriveError, match=re.escape(refused)):
        talk.say(demo, {"edit": POSTED, "text": "Reworded."} | extra)
    assert (posts, said(demo)) == ([], [])


def test_a_reference_to_nothing_the_drive_holds_refuses_the_post_and_sends_nothing(demo: str, posts: list[tuple[str, ...]]) -> None:
    with pytest.raises(DriveError, match=r"\{card:demo:j9-1\} names no card"):
        talk.say(demo, {"text": "see {card:demo:j9-1}"})
    with pytest.raises(DriveError, match=r"\{pr:8\}: the drive tracks no PR numbered 8"):
        talk.say(demo, {"text": "see {pr:8}"})
    with edit(demo) as data:
        data["prs"]["Forge-AI/monorepo#7"] = state.new_pr("https://github.com/Forge-AI/monorepo/pull/7", "j1", "o1")
    with pytest.raises(DriveError, match=r"\{pr:7\}: 7 is the number of yasyf/cc-skills#7, Forge-AI/monorepo#7; name one as owner/repo#7"):
        talk.say(demo, {"text": "{pr:7}"})
    assert (posts, said(demo)) == ([], [])
    talk.say(demo, {"text": "{pr:Forge-AI/monorepo#7}"})
    assert posts[0][-1] == "<https://github.com/Forge-AI/monorepo/pull/7|#7>"


RELEASE = "https://buildkite.com/acme/release/builds/14765"


def test_a_reference_or_link_inside_a_raw_links_words_refuses_the_post_naming_the_nesting_and_sends_nothing(demo: str, posts: list[tuple[str, ...]]) -> None:
    line = {"text": f"<{RELEASE}|The release carrying {{pr:7|the fix that makes the view route return the read's real error}}> is failing on its `runtime-api-next` image build.", "thread_ts": "1794200000.000100"}
    refused = f"{{pr:7|the fix that makes the view route return the read's real error}} sits inside the words of the link to {RELEASE}"
    for post in (line, line | {"dry_run": True}):
        with pytest.raises(DriveError, match=re.escape(refused)):
            talk.say(demo, post)
    with pytest.raises(DriveError, match=re.escape(f"<{PR_URL}|the fix> sits inside the words of the link to {RELEASE}")):
        talk.say(demo, {"text": f"<{RELEASE}|The release carrying <{PR_URL}|the fix>> is failing."})
    assert (posts, said(demo)) == ([], [])
    talk.say(demo, {"text": f"<{RELEASE}|Release 14765>, carrying {{pr:7|the fix}}, is failing; Slack draws `<{RELEASE}|its <link>>` broken."})
    assert posts[0][-1] == f"<{RELEASE}|Release 14765>, carrying <{PR_URL}|#7 the fix>, is failing; Slack draws `<{RELEASE}|its <link>>` broken."


def test_a_card_he_answered_or_the_drive_withdrew_still_links_by_its_waits_id(demo: str, posts: list[tuple[str, ...]]) -> None:
    settled = "https://acme.slack.com/archives/D123/p1794300000000500"
    with edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "design the store", [], state.stamp())
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "Keep the old store?", state.stamp(), card=settled) | {"waits": "demo:j1-6"})
    talk.say(demo, {"text": "I withdrew {card:demo:j1-6}: the redesign settles it."})
    assert posts[0][-1] == f"I withdrew <{settled}|Keep the old store?>: the redesign settles it."
    assert said(demo)[0]["text"] == "I withdrew [Keep the old store?](#/feature/o1): the redesign settles it."
    with pytest.raises(DriveError, match=r"\{card:demo:j1-5\} names no card the drive drew"):
        talk.say(demo, {"text": "see {card:demo:j1-5}"})


def test_a_pr_card_he_tapped_links_by_its_waits_id_from_cc_slacks_closed_cards_in_a_post_and_its_edit(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    tapped = "https://acme.slack.com/archives/D123/p1794300000000300"
    closed = {"waits": "merge:yasyf:cc-skills:7", "title": "New: approve the run page history?", "prs": [PR_URL], "asked": ASKED, "url": tapped, "answer": {"label": "Approve", "at": AWAY}, "tapped": "", "replaced_by": None}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "https://acme.slack.com/", "questions": [], "pr_cards": [], "closed": [closed]})
    talk.say(demo, {"text": "You approved {card:merge:yasyf:cc-skills:7}.", "thread_ts": "1794200000.000100"})
    talk.say(demo, {"edit": POSTED, "text": "You approved {card:merge:yasyf:cc-skills:7}; it lands next."})
    linked = f"<{tapped}|New: approve the run page history?>"
    assert [argv[-1] for argv in posts] == [f"You approved {linked}.", f"You approved {linked}; it lands next."]
    assert said(demo)[0]["text"] == f"You approved [New: approve the run page history?]({tapped})."


def test_an_order_links_its_thread_in_slack_or_else_its_dashboard_page_under_its_title(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]]
) -> None:
    shared = "Shared from Sam in #C0BUGS1: [November 13th, 2026 8:13 AM] sam: Screenshot 2026-11-13 at 11.11.51"
    run("order", "add", "--title", "Share links open the session", "--project", "The drive", "--workstream", "Releases", shared, "--thread", "1794200000.000100", "--owner", "coordinator")
    run("order", "add", "--title", "One owner per order", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: one owner per order")
    view.refresh_cards(demo)
    talk.say(demo, {"text": "{order:o1} is live; {order:o2} is next."})
    assert posts[0][-1] == (
        "<https://acme.slack.com/archives/D123/p1794200000000100|Share links open the session> is live;"
        f" <{dashboard.link(demo)}#/feature/o2|One owner per order> is next."
    )
    assert said(demo)[0]["text"] == "[Share links open the session](#/feature/o1) is live; [One owner per order](#/feature/o2) is next."
    with pytest.raises(DriveError, match=r"\{order:o9\} names no order"):
        talk.say(demo, {"text": "{order:o9} is next."})


def test_a_record_links_what_it_measured_under_the_words_after_its_bar_or_draws_those_words_alone(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    notebook = "https://app.datadoghq.com/notebook/1"
    shown = {
        "661194": {"kind": "report", "lane": "root", "text": "sandsql-server ran out of memory at its 32 GiB limit, 3 times since 9:40 PM", "refs": {"url": notebook}},
        "661195": {"kind": "report", "lane": "root", "text": "the write lock is held across the whole call", "refs": {}},
    }
    asked: list[list[str]] = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout: asked.append(argv) or json.dumps(shown[argv[2]]))
    talk.say(demo, {"text": "The pod restarts because {record:661194|it ran out of memory}. Stalls: {record:661195|the lock is held}."})
    assert asked == [["cci", "show", seq, "--json", "--drive", demo] for seq in ("661194", "661195")]
    assert posts[0][-1] == f"The pod restarts because <{notebook}|it ran out of memory>. Stalls: the lock is held."
    assert said(demo)[0]["text"].startswith(f"The pod restarts because [it ran out of memory]({notebook}).")


def test_712711_a_bare_record_is_refused_naming_the_words_of_its_own_it_would_have_drawn(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    merged = {"kind": "report", "lane": "root", "text": "PR #55865 merged at 4:10 AM, its checks green", "refs": {}}
    monkeypatch.setattr(reads, "run", lambda argv, checkout: json.dumps(merged))
    with pytest.raises(DriveError) as refused:
        talk.say(demo, {"text": "Its fix is in, {record:712709}."})
    assert str(refused.value) == (
        '{record:712709} would draw that record\'s own words, "PR #55865 merged at 4:10 AM", which you never wrote: say what that read'
        " measured after a |, as {record:712709|<what that read measured>}"
    )
    assert (posts, said(demo)) == ([], [])
    talk.say(demo, {"text": "Its fix is in, {record:712709|the merge}."})
    assert posts[0][-1] == "Its fix is in, the merge."


def test_his_ruling_draws_as_his_words_and_when_he_said_them_linked_to_their_ack_and_its_id_is_no_commit(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    ack = "https://acme.slack.com/archives/D123/p1794478560975959"
    ruling = {"body": f"you need to fix the hookw henver it wrongly refuses yu\nNotes: typed at 2:15 AM. Ack: {ack}", "created_at": "2026-11-12T10:16:31Z"}

    def shown(answer_id: str, cwd: str) -> dict:
        if answer_id != "f03a0aa":
            raise DriveError(f"could not read his answer {answer_id}")
        return ruling

    monkeypatch.setattr(settled, "shown", shown)
    talk.say(demo, {"text": "Fixed under {answer:f03a0aa}."})
    when = render.when(state.parse_time(ruling["created_at"]).astimezone().isoformat())
    assert posts[0][-1] == f'Fixed under <{ack}|his {when} ruling "you need to fix the hookw henver it wrongly refuses…">.'


def test_a_jobs_start_and_a_records_time_are_filled_from_the_drives_stamps(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    today = state.now().replace(microsecond=0)
    with edit(demo) as data:
        data["jobs"]["j1860"] = state.new_job("o1", "builder", "fix the replication sync", [], state.stamp(today.replace(hour=4, minute=50, second=21)))
    went_out = today.replace(hour=4, minute=47, second=9).astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    monkeypatch.setattr(reads, "run", lambda argv, checkout: json.dumps({"at": went_out, "text": "POST Yes to both", "refs": {}}))
    talk.say(demo, {"text": "A second fix job started at {started:j1860}, after the report of {at:673251}."})
    assert posts[0][-1] == "A second fix job started at 4:50 AM (job start), after the report of 4:47 AM."
    build = "<https://buildkite.com/forge/release/builds/15020|The SandSQL release build>"
    talk.say(demo, {"text": f"{build} started `11:30 AM`, and its builder at {{started:j1860}}."})
    assert posts[1][-1] == f"{build} started `11:30 AM`, and its builder at 4:50 AM (job start)."
    with pytest.raises(DriveError, match=r"\{started:j9\} names no job of the drive's"):
        talk.say(demo, {"text": "It started at {started:j9}."})
    with pytest.raises(DriveError, match=r"\{started:j1860\|the release's start\} would draw its words in place of the job's start"):
        talk.say(demo, {"text": "SandSQL is releasing; it started {started:j1860|the release's start}."})
    assert len(posts) == 2


def test_a_card_asked_again_with_new_words_moves_its_question_to_the_new_card_and_each_wait_on_it_to_its_waits_id(demo: str, posts: list[tuple[str, ...]]) -> None:
    other = "https://acme.slack.com/archives/D123/p1794300000000400"
    with edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "design the store", [], state.stamp()) | {"waits": [CARD_URL, other]}
        data["jobs"]["j1"]["questions"].append(state.new_question(None, QUESTION, state.stamp(), card=CARD_URL) | {"waits": WAITS})
        data["jobs"]["j2"] = state.new_job("o1", "builder", "ship the store", [], state.stamp()) | {"waits": [CARD_URL]}
    talk.say(demo, {"card": {"title": QUESTION, "waits": WAITS}})
    data = state.load(demo)
    jobs, prs = data["jobs"], data["prs"]
    assert jobs["j1"]["questions"][0]["card"] == POSTED
    assert (jobs["j1"]["waits"], jobs["j2"]["waits"]) == ([WAITS, other], [WAITS])
    talk.say(demo, {"card": {"title": QUESTION, "waits": WAITS}})
    assert (state.load(demo)["jobs"], state.load(demo)["prs"]) == (jobs, prs)


def test_a_question_card_returns_and_records_its_own_permalink(demo: str, posts: list[tuple[str, ...]]) -> None:
    with edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "design the store", [], state.stamp())
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "Keep the old store?", state.stamp()) | {"waits": "demo:j1-8"})
    assert talk.say(demo, {"card": {"title": "Keep the old store?", "waits": "demo:j1-8"}, "thread_ts": "1794200000.000100"}) == f"POSTED {POSTED} CARD {POSTED}"
    assert state.load(demo)["jobs"]["j1"]["questions"][0]["card"] == POSTED
    assert said(demo)[0]["card"] == POSTED


def test_a_card_no_question_keeps_moves_each_wait_on_it_to_its_waits_id_when_asked_again_with_new_words(demo: str, posts: list[tuple[str, ...]]) -> None:
    with edit(demo) as data:
        data["jobs"]["j2"] = state.new_job("o1", "builder", "ship the store", [], state.stamp()) | {"waits": [CARD_URL]}
    talk.say(demo, {"card": {"title": QUESTION, "waits": WAITS}})
    assert state.load(demo)["jobs"]["j2"]["waits"] == [WAITS]


def test_say_posts_no_word_that_a_release_may_start_unless_it_names_each_freeze_and_hold_standing_now(
    demo: str, posts: list[tuple[str, ...]], freezes: list[dict], recorded: Answers, monkeypatch: pytest.MonkeyPatch
) -> None:
    freezes.append(STANDING_FREEZE)
    recorded.listed = [HELD_RELEASES]
    stands = f'{STANDING_FREEZE["text"]}; his hold 15c4de1 on every release reaching past the rehearsal envs of the release provider stands: "no releases until I say we can again"'
    for claim in ("Platy shows the release freeze lifted at 11:06 AM, so the api cut can start now.", "Releases can go again."):
        with pytest.raises(DriveError) as refused:
            talk.say(demo, {"text": claim, "dry_run": True})
        assert str(refused.value) == f"nothing posted: these words say a release may start, and these stand now: {stands}. Name each by its id and what it holds, or leave the claim out"
    named = "Freeze 45ab54019c and his hold 15c4de1 still stand, so no release can start until each lifts."
    talk.say(demo, {"text": named})

    def unread(name: str, data: dict) -> list[dict]:
        raise reads.ReadError("tools/ci: exit 1")

    monkeypatch.setattr(brief, "freezes", unread)
    with pytest.raises(DriveError, match=r"these stand now: the release ledger could not be read \(tools/ci: exit 1\)"):
        talk.say(demo, {"text": named})
    assert [argv[-1] for argv in posts] == [named]


def test_a_hold_cc_notes_cannot_read_stands_under_no_id_so_a_brief_still_says_what_stands_and_no_release_claim_passes(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unread(label: str, cwd: str) -> list[dict]:
        raise DriveError(f"could not read his answers labelled {label}: error: open git repository at {cwd}")

    monkeypatch.setattr(settled, "listed", unread)
    cwd = state.load(demo)["roles"]["coordinator"]["cwd"]
    assert brief.releases(demo, state.load(demo)) == (
        f"his hold on every release could not be read (could not read his answers labelled release-hold: error: open git repository at {cwd}), "
        "so read `ccn answer list --label release-hold` before saying a release may start"
    )
    with pytest.raises(DriveError, match=r"these stand now: his hold on every release could not be read"):
        talk.say(demo, {"text": "Releases can go again."})
    assert posts == []


@pytest.mark.parametrize(
    "ended",
    [
        "Done with staging: our freeze is lifted, and the soak passed.",
        "The freeze on staging's SandSQL is lifted, so it is free for the next test.",
        "The hold on staging's SandSQL is lifted.",
        "Staging is unfrozen and back on dev's code.",
        "The release freeze lifted at 11:06 AM.",
    ],
    ids=["record 718927's line", "a staging freeze ended", "a staging hold ended", "unfrozen", "a release freeze ended"],
)
def test_say_posts_words_that_a_freeze_or_hold_ended_while_others_and_his_hold_stand_since_an_end_starts_no_release(
    demo: str, posts: list[tuple[str, ...]], freezes: list[dict], recorded: Answers, ended: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    freezes.append(STANDING_FREEZE)
    recorded.listed = [HELD_RELEASES]
    talk.say(demo, {"text": ended})

    def unread(name: str, data: dict) -> list[dict]:
        raise reads.ReadError("ci release: ci release runs no freezes")

    monkeypatch.setattr(brief, "freezes", unread)
    (state.drive_dir(demo) / talk.SAID_FILE).unlink()
    talk.say(demo, {"text": ended})
    assert [argv[-1] for argv in posts] == [ended, ended]


def test_say_words_a_bare_link_itself_since_another_hooks_updated_input_can_drop_the_say_hooks_fix(demo: str, posts: list[tuple[str, ...]], checked: Checked) -> None:
    freeze = "https://acme.slack.com/archives/C0B75AL4XEW/p1794828199124889"
    talk.say(demo, {"text": f"The new freeze holds until 9:00 AM tomorrow ({freeze}). See the freeze (<{freeze}>)."})
    assert ([argv[-1] for argv in posts], checked.texts) == ([f"The new freeze holds until 9:00 AM tomorrow (<{freeze}|the message>). <{freeze}|See the freeze>."], [])


def test_say_posts_no_reference_token_or_stub_line_its_text_or_blocks_still_hold_as_typed(demo: str, posts: list[tuple[str, ...]]) -> None:
    for post in ({"text": "The fix {pr: 56973} is up."}, {"text": "The fix is up.\n(its facts)"}, {"text": "The fix is up.", "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "*Order* {order: o1339}"}}]}):
        with pytest.raises(DriveError, match="nothing posted: its words would show"):
            talk.say(demo, post)
    assert posts == []


def test_a_block_post_slack_shows_as_text_alone_or_drops_a_block_of_is_said_to_its_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    link = "https://acme.slack.com/archives/C1/p1794829439040349?thread_ts=1794827701.310319"
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "*Cases*"}}, {"type": "table", "rows": []}]
    for shown, said in (([{"type": "rich_text"}], "shows this post as its text alone"), ([{"type": "section"}], "dropped its table blocks"), ([{"type": "section"}, {"type": "table"}], None)):
        monkeypatch.setattr(slack, "cc_slack", lambda *argv, shown=shown: {"messages": [{"ts": "1794829439.040349", "raw": {"blocks": shown}}]})
        assert (said in found) if said and (found := talk.drawn_as(link, blocks)) else talk.drawn_as(link, blocks) is None, shown


def test_say_with_done_marks_each_asked_message_done_once_it_posts_and_a_status_marks_none(demo: str, posts: list[tuple[str, ...]]) -> None:
    asked = "https://acme.slack.com/archives/C0CHZM7A478/p1794828096208109?thread_ts=1794827701.310319&cid=C0CHZM7A478"
    talk.say(demo, {"text": "Checking the three builds.", "asked": asked})
    assert [argv for argv in posts if argv[0] == "pickup"] == []
    assert talk.say(demo, {"text": "All three builds passed.", "asked": asked, "done": True}).endswith("; marked done 1794828096.208109")
    assert [argv for argv in posts if argv[0] == "pickup"] == [("pickup", "--channel", "C0CHZM7A478", "--ts", "1794828096.208109", "--thread", "1794827701.310319", "--done")]


def test_a_say_an_old_mcp_server_hands_no_ticket_takes_the_sessions_ticket_the_hook_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    kept = tmp_path / "cc-slack" / "tickets"
    kept.mkdir(parents=True)
    (kept / "toolu_other.json").write_text(json.dumps({"ticket_id": "x", "ticket_tree": "s2", "session": "s2"}))
    (kept / "toolu_mine.json").write_text(json.dumps({"ticket_id": "t1", "ticket_tree": "s1", "ticket_call": "toolu_mine", "session": "s1"}))
    assert talk.with_ticket({"text": "hi"}) == {"text": "hi", "ticket_id": "t1", "ticket_tree": "s1", "ticket_call": "toolu_mine"}
    assert sorted(path.name for path in kept.iterdir()) == ["toolu_other.json"]
    assert talk.with_ticket({"text": "hi", "ticket_id": "given"}) == {"text": "hi", "ticket_id": "given"}


def test_a_report_the_drive_only_took_to_post_counts_as_unposted_and_one_with_a_permalink_as_posted(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agents, "answered", lambda seq: {"attrs": {}, "thread": "1794200000.000100"})
    monkeypatch.setattr(talk, "report_line", lambda name, seq: "POSTED https://acme.slack.com/archives/C1/p1")
    with edit(demo) as data:
        data["carried"]["700"] = agents.DRIVE_CLAIM
    assert talk.report_post(demo, "700") == "report 700 posted by the drive: POSTED https://acme.slack.com/archives/C1/p1"
    with edit(demo) as data:
        data["carried"]["700"] = "https://acme.slack.com/archives/C1/p2"
    assert talk.report_post(demo, "700") == "report 700 already posted at https://acme.slack.com/archives/C1/p2: nothing posted again"


def test_a_post_report_the_drive_took_and_never_posted_is_taken_again_then_owed_on_its_order_past_due(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[list[dict]] = []
    taken: list[list[str]] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: sent.append(records) or ["1"])
    monkeypatch.setattr(state, "detach", lambda name, args, log: taken.append(args) or 1)
    monkeypatch.setattr(agents, "answered", lambda seq: {"attrs": {"order": "o1"} if seq == "700" else {}, "thread": None})
    took = state.now() - agents.UNPOSTED_AFTER - timedelta(minutes=1)
    with edit(demo) as data:
        data["orders"]["o1"] = state.new_order("ship release 3", "terminal", None, {}, ASKED)
        data["carried"] |= {"700": agents.DRIVE_CLAIM, "701": "https://acme.slack.com/archives/C1/p1", "702": agents.DRIVE_CLAIM}
        data["posts"]["claimed"] = {"700": state.stamp(took), "701": state.stamp(took), "702": state.stamp(took)}
    agents.unposted(demo, state.now())
    assert taken == [["report-post", "700"], ["report-post", "702"]] and not sent
    assert "report 700 taken again" in (state.drive_dir(demo) / "posts.log").read_text()
    assert sorted(state.load(demo, ("posts",))["posts"]["claimed"]) == ["700", "702"]
    agents.unposted(demo, state.now())
    assert len(taken) == 2
    agents.unposted(demo, state.now() + agents.UNPOSTED_AFTER + timedelta(minutes=1))
    assert len(taken) == 2
    assert [record["text"].split(";")[0] for records in sent for record in records] == ["POST reports 702 have no post after two tries of report-post, and name no order to owe it on"]
    data = state.load(demo)
    assert data["posts"]["claimed"] == {} and data["posts"]["retaken"] == []
    assert [item["what"] for item in data["orders"]["o1"]["owed"]] == ["the post of report 700, which report-post tried twice"]


def test_a_report_slack_still_rate_limits_stays_taken_and_the_watchs_next_pass_posts_it(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with edit(demo) as data:
        data["carried"]["735976"] = agents.DRIVE_CLAIM
        data["posts"]["claimed"] = {"735976": state.stamp()}
    monkeypatch.setattr(agents, "answered", lambda seq: {"attrs": {}, "report": {"kind": "report", "lane": "root", "text": "POST x", "seq": seq}, "thread": "1794200000.000100"})
    limited = "cc-slack: can't read thread 1794687682.137749 now (ratelimited): Slack limits the read for 10s more"
    monkeypatch.setattr(talk, "report_line", lambda name, seq: (_ for _ in ()).throw(slack.SlackError(limited)))
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: pytest.fail(f"sent the report back over a rate limit: {records}"))
    assert talk.report_post(demo, "735976").startswith("report 735976 deferred: Slack still rate-limits its read")
    data = state.load(demo)
    assert (data["carried"]["735976"], list(data["posts"]["claimed"]), data["posts"]["deferred"]) == (agents.DRIVE_CLAIM, ["735976"], ["735976"])
    taken: list[list[str]] = []
    monkeypatch.setattr(state, "detach", lambda name, args, log: taken.append(args) or 1)
    agents.retake_deferred(demo)
    agents.retake_deferred(demo)
    assert taken == [["report-post", "735976"]]
    assert "report 735976 taken again after a Slack rate limit" in (state.drive_dir(demo) / "posts.log").read_text()


def test_a_report_a_live_run_holds_is_never_posted_beside_it_by_a_retry(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agents, "answered", lambda seq: {"attrs": {}, "report": {"kind": "report", "lane": "root", "text": "POST x", "seq": seq}, "thread": "1794200000.000100"})
    monkeypatch.setattr(talk, "report_line", lambda name, seq: pytest.fail("posted beside a live run"))
    taken: list[list[str]] = []
    monkeypatch.setattr(state, "detach", lambda name, args, log: taken.append(args) or 1)
    with talk.report_lock(demo, "736162") as held:
        assert held
        assert talk.report_post(demo, "736162") == "report 736162 is being posted by a live run already, which holds its lock: nothing started"
        agents.take_post(demo, "736162", "taken again, with no post after its first try,")
        assert taken == []
    assert not talk.report_running(demo, "736162")
    agents.take_post(demo, "736162", "taken again, with no post after its first try,")
    assert taken == [["report-post", "736162"]]


def test_a_report_posted_meanwhile_is_refused_before_the_slack_write(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    link = "https://acme.slack.com/archives/C0CHV3PTX4K/p1794859034825359"
    with edit(demo) as data:
        data["carried"]["736162"] = link
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: pytest.fail(f"wrote to Slack after a parallel run posted: {argv}"))
    with pytest.raises(DriveError, match=f"report 736162 went out meanwhile at {re.escape(link)}"):
        talk.posted(demo, {"text": "The fix is live.", "record": "736162"})


def test_a_report_post_tries_again_while_cc_slack_times_out_and_the_report_has_no_post(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    tries: list[str] = []

    def timing_out(name: str, seq: str) -> str:
        tries.append(seq)
        if len(tries) < talk.REPORT_POST_TRIES:
            raise slack.SlackError("cc-slack thread: context deadline exceeded")
        return "POSTED https://acme.slack.com/archives/C1/p1"

    monkeypatch.setattr(agents, "answered", lambda seq: {"attrs": {}, "thread": "1794200000.000100"})
    monkeypatch.setattr(talk, "report_line", timing_out)
    assert talk.report_post(demo, "735671") == "report 735671 posted by the drive: POSTED https://acme.slack.com/archives/C1/p1"
    assert len(tries) == talk.REPORT_POST_TRIES


def test_a_seat_communicators_say_marks_its_seat_active_so_the_watch_never_retires_it_as_idle(communicator: str) -> None:
    started = state.stamp(state.now() - watch.COMMUNICATOR_IDLE - timedelta(minutes=5))
    with edit(communicator) as data:
        data["communicators"]["Priority o9"] = {"session": os.environ["CLAUDE_CODE_SESSION_ID"], "started": started}
    talk.caller(communicator)
    heard = state.load(communicator, ("communicators",))["communicators"]["Priority o9"]["heard"]
    assert state.now() - state.parse_time(heard) < timedelta(minutes=1)


def test_a_report_post_edit_named_by_its_edit_attr_posts_as_that_report_so_no_has_no_post_defect_follows(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from drive import answer

    attrs = {"channel": "C1", "edit": "1794847894.692869", "thread": "1794847681.824529", "progress": "true"}
    monkeypatch.setattr(agents, "answered", lambda seq: {"attrs": attrs, "report": {"kind": "report", "text": "POST edit: the cause is the pool", "seq": seq}, "thread": None})
    monkeypatch.setattr(answer, "ask", lambda facts, prompt, rules, tag: (json.dumps({"text": "The cause is the pool.", "place": "edit"}), None))
    monkeypatch.setattr(slack, "whoami", lambda: {"team_url": "https://acme.slack.com/"})
    monkeypatch.setattr(slack, "homes", lambda data: {"C1"})
    monkeypatch.setattr(slack, "post_findings", lambda text, draws_card, asked="": [])
    edited = "https://acme.slack.com/archives/C1/p1794847894692869?thread_ts=1794847681.824529&cid=C1"
    spoken: list[dict] = []

    def posting(name: str, post: dict, known: dict) -> str:
        spoken.append(post)
        with edit(name) as data:
            data["carried"][post["record"]] = post["edit"]
        return f"EDITED {post['edit']}"

    monkeypatch.setattr(talk, "spoken", posting)
    assert talk.report_line(demo, "734749") == f"EDITED {edited}"
    assert [(post["edit"], post["record"]) for post in spoken] == [(edited, "734749")]
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: pytest.fail(f"flagged a posted edit: {records}"))
    with edit(demo) as data:
        data["posts"]["claimed"] = {"734749": state.stamp(state.now() - agents.UNPOSTED_AFTER - timedelta(minutes=1))}
    agents.unposted(demo, state.now())


def test_a_report_folds_in_the_unposted_reports_sent_before_it_to_its_thread_and_drops_a_passed_due_telling_its_writer(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from drive import answer

    thread = "1794830198.687629"
    reports = {
        "735764": {"attrs": {"channel": "C1", "thread": thread}, "report": {"kind": "report", "lane": "priority-o1339", "text": "POST staging's writer stopped four times", "seq": "735764"}, "thread": None},
        "735790": {"attrs": {"channel": "C1", "thread": "1794800000.000001"}, "report": {"kind": "report", "lane": "root", "text": "POST another thread", "seq": "735790"}, "thread": None},
        "735816": {"attrs": {"channel": "C1", "thread": thread, "due": "2020-01-01T00:00:00-08:00", "owes": "the next leg"}, "report": {"kind": "correction", "lane": "priority-o1339", "re": "735764", "text": "POST four stops, not three", "seq": "735816"}, "thread": None},
    }
    with edit(demo) as data:
        data["carried"] |= dict.fromkeys(reports, agents.DRIVE_CLAIM)
        data["posts"]["claimed"] = dict.fromkeys(reports, state.stamp())
    monkeypatch.setattr(agents, "answered", lambda seq: reports[seq])
    asked: list[str] = []
    monkeypatch.setattr(answer, "ask", lambda facts, prompt, rules, tag: asked.append(facts) or (json.dumps({"text": "Staging's writer stopped four times.", "place": "thread"}), None))
    monkeypatch.setattr(slack, "homes", lambda data: {"C1"})
    monkeypatch.setattr(slack, "post_findings", lambda text, draws_card, asked="": [])
    link = f"https://acme.slack.com/archives/C1/p1794858000000100?thread_ts={thread}&cid=C1"
    spoken: list[dict] = []

    def posting(name: str, post: dict, known: dict, recorded_first: bool = False, beside: bool = False) -> str:
        spoken.append(post)
        with edit(name) as data:
            data["carried"][post["record"]] = link
        return f"POSTED {link}"

    sent: list[list[dict]] = []
    monkeypatch.setattr(talk, "spoken", posting)
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: sent.append(records) or ["1"])
    talk.report_line(demo, "735816")
    assert "- Report 735764: staging's writer stopped four times" in asked[0] and "735790" not in asked[0]
    assert "passed before it could post" in asked[0]
    assert "He is away" not in asked[0]
    assert "due" not in spoken[0] and "owes" not in spoken[0]
    data = state.load(demo)
    assert data["carried"]["735764"] == link and "735764" not in data["posts"]["claimed"]
    assert data["carried"]["735790"] == agents.DRIVE_CLAIM
    assert [(record["to"], record["re"]) for records in sent for record in records] == [([f"priority-o1339@{demo}"], ["735816"])]


def test_a_reference_its_writer_wrapped_in_angle_brackets_draws_one_link_with_no_literal_brackets() -> None:
    found = {"{pr:7|the monitor fix}": {"kind": "pr", "title": "the monitor fix", "url": "https://github.com/acme/app/pull/7"}}
    assert refs.slack("You merged <{pr:7|the monitor fix}>, and &lt;<https://x.example/a|this>&gt; too.", found, None) == "You merged <https://github.com/acme/app/pull/7|the monitor fix>, and <https://x.example/a|this> too."


def test_say_drops_the_exact_words_it_already_sent_to_the_same_place(communicator: str, posts: list[tuple[str, ...]]) -> None:
    first = talk.say(communicator, {"text": "The recommended answer to this question is built and paused."})
    count = len(posts)
    again = talk.say(communicator, {"text": "The recommended answer to this question is built and paused."})
    assert again.startswith("DROPPED these exact words already went to this place at ") and first.split()[1] in again
    assert len(posts) == count
    talk.say(communicator, {"text": "The recommended answer is live."})
    assert len(posts) == count + 1


@pytest.mark.parametrize("writer", ["root", "priority-o2296", "watch"])
def test_a_report_about_an_order_from_any_session_but_its_communicator_goes_to_the_communicator_so_its_thread_hears_one_voice(demo: str, writer: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with edit(demo) as data:
        data["orders"]["o1"] = state.new_order("why did the run fail?", "terminal", None, {}, ASKED) | {"owner": "coordinator"}
        data["carried"]["736200"] = agents.DRIVE_CLAIM
        data["posts"]["claimed"] = {"736200": state.stamp()}
    monkeypatch.setattr(agents, "answered", lambda seq: {"attrs": {"order": "o1", "thread": "1794201440.150389"}, "report": {"kind": "report", "lane": writer, "text": "POST the run failed on a stale lock", "seq": seq}, "thread": None})
    monkeypatch.setattr(talk, "report_line", lambda name, seq: pytest.fail("posted a report its thread's voice should carry"))
    handed: list[tuple[str, str]] = []
    monkeypatch.setattr(agents, "handle", lambda name, brief, lane="communicator": handed.append((lane, brief)) or (lane, True))
    assert talk.report_post(demo, "736200") == "report 736200 handed to communicator, the one voice of o1's thread"
    assert handed[0][0] == "communicator" and "say naming record 736200" in handed[0][1]
    data = state.load(demo)
    assert data["posts"]["handed"]["736200"]["order"] == "o1" and "736200" in data["posts"]["claimed"]


def test_a_correction_of_a_posted_report_rewords_that_post_itself_rather_than_bouncing_to_a_writer_that_cannot_post(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from drive import answer

    posted = "https://acme.slack.com/archives/C1/p1794847894692869?thread_ts=1794847681.824529&cid=C1"
    with edit(demo) as data:
        data["carried"]["735590"] = posted
    monkeypatch.setattr(agents, "answered", lambda seq: {"attrs": {"channel": "C1"}, "report": {"kind": "correction", "re": "735590", "text": "POST the cause is the pool", "seq": seq}, "thread": None})
    monkeypatch.setattr(answer, "ask", lambda facts, prompt, rules, tag: (json.dumps({"text": "The cause is the pool.", "place": "thread"}), None))
    monkeypatch.setattr(slack, "homes", lambda data: {"C1"})
    monkeypatch.setattr(slack, "post_findings", lambda text, draws_card, asked="": [])
    spoken: list[dict] = []
    monkeypatch.setattr(talk, "spoken", lambda name, post, known, recorded_first=False: spoken.append(post) or f"EDITED {post['edit']}")
    assert talk.report_line(demo, "735599") == f"EDITED {posted}"
    assert spoken[0]["edit"] == posted


@pytest.mark.parametrize("given", ["1794853324.557129", "https://acme.slack.com/archives/C0CELUMJ6MU/p1794853324557129?thread_ts=1794847111.398069&cid=C0CELUMJ6MU"])
def test_a_reports_edit_attr_names_its_post_by_ts_or_by_its_permalink_as_given(given: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack, "whoami", lambda: {"team_url": "https://acme.slack.com/"})
    assert talk.edited("C0CELUMJ6MU", given, "1794847111.398069") == "https://acme.slack.com/archives/C0CELUMJ6MU/p1794853324557129?thread_ts=1794847111.398069&cid=C0CELUMJ6MU"


def test_a_report_handed_to_the_communicator_for_another_channel_counts_as_posted_and_its_reporter_told_once_it_posts_there(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    channel, thread = "C0CELUMJ6MU", "1794847111.398069"
    link = f"https://acme.slack.com/archives/{channel}/p1794853324557129?thread_ts={thread}&cid={channel}"
    with edit(demo) as data:
        data["orders"]["o1"] = state.new_order("fix the 401s", "terminal", None, {}, ASKED)
        data["orders"]["o1"]["reporters"] = [{"channel": channel, "thread": thread, "link": f"https://acme.slack.com/archives/{channel}/p{thread.replace('.', '')}", "told": None}]
        data["carried"]["735525"] = agents.DRIVE_CLAIM
        data["posts"]["claimed"] = {"735525": state.stamp()}
    monkeypatch.setattr(agents, "answered", lambda seq: {"attrs": {"channel": channel, "thread": thread, "order": "o1"}, "report": {"kind": "report", "text": "POST fix is live", "seq": seq}, "thread": None})
    monkeypatch.setattr(slack, "homes", lambda data: {"C1"})
    monkeypatch.setattr(agents, "handle", lambda name, brief, lane="communicator": (lane, True))
    assert "handed to the communicator" in talk.report_post(demo, "735525")

    def posting_there(data: dict) -> None:
        for handed in talk.handed_posts(data, None, (channel, thread)):
            talk.carry_handed(data, handed, link)

    state.write(demo, posting_there)
    data = state.load(demo)
    assert data["carried"]["735525"] == link
    assert data["posts"]["claimed"] == {} and data["posts"]["handed"] == {}
    assert data["orders"]["o1"]["reporters"][0]["told"] == link


def test_a_channel_thread_post_naming_a_pr_by_its_github_link_links_its_open_card(communicator: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    card = "https://acme.slack.com/archives/D123/p1794300000000777"
    monkeypatch.setattr(view, "live_cards", lambda name: {"pr_cards": []})
    monkeypatch.setattr(view, "head_card", lambda pr, cards: {"url": card})
    text = f"Both are green: {PR_URL} and #7."
    loaded = []
    load = state.load

    def selected(name: str, fields: tuple[str, ...] | None = None) -> dict:
        loaded.append(fields)
        return load(name, fields)

    with monkeypatch.context() as reads:
        reads.setattr(state, "load", selected)
        assert refs.carded(communicator, text) == "Both are green: {pr:yasyf/cc-skills#7} and {pr:yasyf/cc-skills#7}."
    assert loaded == [("prs",)]
    found = refs.resolved(communicator, refs.carded(communicator, text))
    assert refs.slack(refs.carded(communicator, text), found, "1794830614.862999").count(f"<{card}|") == 2
    monkeypatch.setattr(view, "head_card", lambda pr, cards: None)
    assert refs.carded(communicator, text) == text


def test_a_report_sent_back_to_its_writer_counts_as_handled_so_no_has_no_post_defect_follows(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[list[dict]] = []
    monkeypatch.setattr(agents, "answered", lambda seq: {"attrs": {}, "thread": "1794200000.000100"})
    monkeypatch.setattr(agents, "shown", lambda seq: {"lane": "root"})
    monkeypatch.setattr(talk, "report_line", lambda name, seq: (_ for _ in ()).throw(DriveError("{release:18149} names no release post")))
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: sent.append(records) or ["1"])
    with edit(demo) as data:
        data["carried"]["734916"] = agents.DRIVE_CLAIM
        data["posts"]["claimed"] = {"734916": state.stamp(state.now() - agents.UNPOSTED_AFTER - timedelta(minutes=1))}
    assert talk.report_post(demo, "734916").startswith("report 734916 sent back to root@demo")
    assert state.load(demo, ("carried",))["carried"]["734916"] == "sent back to root@demo"
    agents.unposted(demo, state.now())
    assert [record["kind"] for records in sent for record in records] == ["refused"]


def test_a_progress_report_stays_in_records_and_one_naming_no_thread_goes_back_to_its_writer_never_to_a_channels_root(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[list[dict]] = []
    reports = {
        "736397": {"attrs": {"order": "o2046", "progress": "true"}, "report": {"kind": "report", "lane": "priority-o2046", "text": "POST progress", "seq": "736397"}, "thread": None},
        "736476": {"attrs": {"order": "o2046"}, "report": {"kind": "report", "lane": "priority-o2046", "text": "POST no thread", "seq": "736476"}, "thread": None},
    }
    monkeypatch.setattr(agents, "answered", lambda seq: reports[seq])
    monkeypatch.setattr(agents, "shown", lambda seq: {"lane": "priority-o2046"})
    monkeypatch.setattr(talk, "report_line", lambda name, seq: pytest.fail(f"posted report {seq}"))
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: sent.append(records) or ["1"])
    with edit(demo) as data:
        data["carried"] |= dict.fromkeys(reports, agents.DRIVE_CLAIM)
        data["posts"]["claimed"] = dict.fromkeys(reports, state.stamp())
    assert talk.report_post(demo, "736397") == "report 736397 kept in records, not posted: it is a progress report"
    assert talk.report_post(demo, "736476").startswith("report 736476 sent back to priority-o2046@demo: it names no thread")
    data = state.load(demo)
    assert (data["carried"]["736397"], data["carried"]["736476"], data["posts"]["claimed"]) == ("kept in records: a progress report is never posted to him", "sent back to priority-o2046@demo", {})
    assert [(record["kind"], record["re"]) for records in sent for record in records] == [("refused", ["736476"])]


def test_a_release_reference_keeps_a_found_post_looks_a_miss_up_again_after_a_while_and_goes_out_unlinked_with_a_warning_while_none_is_up(communicator: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    post = "https://acme.slack.com/archives/C9/p1794851300000100"
    looks: list[str] = []
    up: dict[str, str] = {}

    def looked(release: str) -> str | None:
        looks.append(release)
        return up.get(release)

    monkeypatch.setattr(providers, "of", lambda data: SimpleNamespace(release=SimpleNamespace(post=looked), transformed=lambda text: text))
    said = talk.say(communicator, {"text": "Release 10 is building: {release:18220|release 10}."})
    assert posts[-1][-1] == "Release 10 is building: release 10."
    assert "WARNING {release:18220} went out as its words, unlinked" in said and "where the drive itself started or announced it" in said
    talk.say(communicator, {"text": "Still building: {release:18220|release 10}."})
    assert looks == ["18220"]
    up["18220"] = post
    monkeypatch.setattr(refs, "LOOKED", {})
    talk.say(communicator, {"text": "Release 10 is on staging: {release:18220|release 10}."})
    monkeypatch.setattr(refs, "LOOKED", {})
    talk.say(communicator, {"text": "Release 10 is live: {release:18220|release 10}."})
    assert posts[-1][-1] == f"Release 10 is live: <{post}|release 10>."
    assert looks == ["18220", "18220"]
    talk.say(communicator, {"text": "Release 11 is building: {release:18221|release 11}."})
    monkeypatch.setattr(refs, "LOOKED", {(str(state.drive_dir(communicator) / refs.RELEASE_POSTS_FILE), "18221"): (refs.time.monotonic() - refs.RELEASE_RELOOK_SECONDS, None)})
    up["18221"] = post
    talk.say(communicator, {"text": "Release 11 is live: {release:18221|release 11}."})
    assert posts[-1][-1] == f"Release 11 is live: <{post}|release 11>."
    assert looks == ["18220", "18220", "18221", "18221"]


def test_a_report_post_may_link_what_the_reports_references_draw_but_no_other_link(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from drive import answer

    pull = "https://github.com/yasyf/cc-skills/pull/6064"
    words = "POST the retro: {pr:yasyf/cc-skills#6064|required staging cases} now gates every cut"
    monkeypatch.setattr(agents, "answered", lambda seq: {"attrs": {"channel": "C1"}, "report": {"kind": "report", "text": words, "seq": seq}, "thread": None})
    monkeypatch.setattr(slack, "homes", lambda data: {"C1"})
    monkeypatch.setattr(slack, "post_findings", lambda text, draws_card, asked="": [])
    monkeypatch.setattr(view, "live_cards", lambda name: None)
    monkeypatch.setattr(talk, "spoken", lambda name, post, known, recorded_first=False, beside=False: f"POSTED {post['text']}")
    for link, refused in ((pull, False), ("https://github.com/yasyf/cc-skills/pull/9999", True)):
        monkeypatch.setattr(answer, "ask", lambda facts, prompt, rules, tag, link=link: (json.dumps({"text": f"<{link}|Required staging cases> now gate every cut.", "place": "root"}), None))
        if refused:
            with pytest.raises(DriveError, match="which the report does not print"):
                talk.report_line(demo, "735031")
        else:
            assert talk.report_line(demo, "735031").startswith("POSTED")


def test_a_repositorys_posting_rule_reaches_the_communicators_rules_and_its_transform_rewrites_a_post(communicator: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    rule = "Link a release by its Slack post, never its build page."
    post = "https://acme.slack.com/archives/C9/p1794851300000100"
    posting = {"rules": [rule], "transforms": [{"match": r"<?https://ci\.example/release/builds/(\d+)(?:\|[^>]*)?>?", "to": "{release:$1}"}]}
    monkeypatch.setattr(state, "repo_config", lambda data: {"posting": posting})
    monkeypatch.setattr(providers, "of", lambda data: SimpleNamespace(release=SimpleNamespace(post={"18219": post}.get)))
    assert f"- {rule}" in render.rules(state.load(communicator), "communicator")
    assert f"- {rule}" not in render.rules(state.load(communicator), "coordinator")
    talk.say(communicator, {"text": "Release 9 is out: https://ci.example/release/builds/18219"})
    assert posts[-1][-1] == f"Release 9 is out: <{post}|18219>"


def test_the_communicator_speaks_in_its_order_threads_and_a_seat_communicators_say_there_goes_to_it(communicator: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    thread = "1794201440.150389"
    with edit(communicator) as data:
        data["orders"]["o1"] = state.new_order("fix the run page", "terminal", None, {}, ASKED) | {"owner": "coordinator", "thread": [slack.dm(), thread]}
    handed: list[tuple[str, str]] = []
    monkeypatch.setattr(agents, "handle", lambda name, brief, lane="communicator": handed.append((lane, brief)) or (lane, True))
    count = len(posts)
    assert talk.say(communicator, {"text": "The run page fix is in review.", "thread_ts": thread}).startswith("POSTED")
    assert len(posts) == count + 1 and not handed
    monkeypatch.setattr(talk, "own_lane", lambda data: "priority-o9-communicator")
    said = talk.say(communicator, {"text": "The run page fix merged.", "thread_ts": thread})
    assert said == f"HANDED {slack.dm()}/{thread} to communicator, the one voice of o1's thread"
    assert len(posts) == count + 1
    assert handed[0][0] == "communicator" and "The run page fix merged." in handed[0][1]


def test_say_posts_the_words_its_hook_judged_and_checks_none_of_them_again(demo: str, posts: list[tuple[str, ...]], checked: Checked) -> None:
    checked.findings = ["1:5 drive-term 'fence': say what happens to the reader's work instead"]
    with edit(demo) as data:
        data["stand_ins"]["backup"] = "S3 replica"
    text = "The fence held, the backup copy is current, and the runs failed because the pool was full."
    assert talk.say(demo, {"text": text, "dry_run": True}) == f"DRY his DM root, 0 pictures: {text}"
    talk.say(demo, {"text": text})
    assert ([argv[-1] for argv in posts], checked.texts) == ([text], [])


def test_each_visual_goes_to_slack_as_a_picture_and_to_the_dashboard_as_its_source(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    drawn = tmp_path / "one chat.png"
    drawn.write_bytes(b"one chat")
    asked: list[tuple[str, str, str]] = []
    monkeypatch.setattr(picture, "cached", lambda folder, name, kind, source: asked.append((name, kind, source)) or drawn)
    talk.say(demo, {"text": "The lock", "visuals": [{"kind": "code", "language": "go", "source": "mu.Lock()", "caption": "one chat"}]})
    assert asked == [("one chat", "code:go", "mu.Lock()")]
    assert posts[0][-4:] == ("--file", str(drawn), "--", "The lock")
    assert said(demo)[0]["visuals"] == [{"kind": "code:go", "source": "mu.Lock()", "caption": "one chat", "picture": str(drawn)}]


def test_a_chart_goes_to_slack_as_its_fence_for_slack_to_draw_and_the_dashboard_draws_it_from_its_source(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    own_pr(demo)
    monkeypatch.setattr(picture, "cached", lambda *drawn: pytest.fail(f"a chart was drawn as a picture: {drawn}"))
    encoding = {"y": {"field": "what", "type": "nominal"}, "x": {"field": "count", "type": "quantitative"}}
    spec = json.dumps({"data": {"values": [{"what": "Slack posts", "count": 384}, {"what": "distinct alerts", "count": 41}]}, "mark": "bar", "encoding": encoding})
    talk.say(demo, {"text": "Alerts, not posts.", "visuals": [{"kind": "chart", "source": spec, "caption": "in 24 hours"}]})
    assert posts[0][6:] == ("--", f"Alerts, not posts.\n\nin 24 hours\n```chart\n{spec}\n```")
    [kept] = said(demo)
    assert (kept["text"], kept["visuals"]) == ("Alerts, not posts.", [{"kind": "chart", "source": spec, "caption": "in 24 hours"}])
    monkeypatch.setattr(dashboard, "home_repo", lambda checkout: None)
    board = dashboard.Board(demo, [])
    board.rebuild()
    assert [entry["visuals"] for entry in view.history_page(board.model, None, "said", 1)["moments"]] == [kept["visuals"]]


def test_a_code_or_diagram_fence_in_the_text_goes_to_slack_as_its_picture_while_words_raw_data_and_a_chart_stay_text(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    asked: list[tuple[str, str, str]] = []

    def cached(folder: Path, name: str, kind: str, source: str) -> Path:
        asked.append((name, kind, source))
        (drawn := tmp_path / f"{name}.png").write_bytes(source.encode())
        return drawn

    monkeypatch.setattr(picture, "cached", cached)
    stays = 'The reply:\n```json\n{"locked": true}\n```\nThe steps:\n```\nlock, write, unlock\n```\nPer day:\n```chart\n{"mark": "bar"}\n```'
    text = f"The lock covers each chat:\n```go\nmu.Lock()\ndefer mu.Unlock()\n```\nWho takes it:\n```mermaid\nflowchart TD\n  chat --> lock\n```\n{stays}"
    talk.say(demo, {"text": text})
    assert asked == [("go code", "code:go", "mu.Lock()\ndefer mu.Unlock()\n"), ("Diagram", "mermaid", "flowchart TD\n  chat --> lock\n")]
    sent = f"The lock covers each chat:\n\nWho takes it:\n\n{stays}"
    assert posts[0][6:] == ("--file", str(tmp_path / "go code.png"), "--file", str(tmp_path / "Diagram.png"), "--", sent)
    [kept] = said(demo)
    assert (kept["text"], kept["visuals"]) == (text, [])
    assert talk.say(demo, {"text": text, "dry_run": True}) == f"DRY his DM root, 2 pictures: {sent}"


def building(demo: str) -> None:
    with edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "show a run's history", list(data["orders"]["o1"]["parts"]), state.stamp())
        data["orders"]["o1"]["jobs"].append("j1")


def test_a_stack_card_names_each_pr_by_its_url_and_the_dashboard_keeps_the_stack(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "show a run's history", "--owner", "coordinator")
    building(demo)
    with pytest.raises(DriveError, match=r"tap_prs names yasyf/cc-skills#8, which the drive holds no PR for"):
        talk.say(demo, {"text": "land these", "tap_prs": [PR_URL, "https://github.com/yasyf/cc-skills/pull/8"]})
    assert (posts, said(demo)) == ([], [])
    talk.say(demo, {"text": "land these", "tap_prs": ["yasyf/cc-skills#7"]})
    assert posts[0] == ("dm-status", "--agent", "claude", "--drive", "demo", "--as-holder", "--tap-pr", PR_URL, "--", "land these")
    assert said(demo)[0]["prs"] == ["yasyf/cc-skills#7"]


def test_the_drives_own_draw_of_his_card_goes_in_its_orders_thread_with_no_line_over_it(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, checked: Checked
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "show a run's history", "--thread", "1794200000.000100", "--owner", "coordinator")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: the watch draws the PR card")
    building(demo)
    tooling = "https://github.com/yasyf/cc-skills/pull/8"
    with edit(demo) as data:
        data["prs"]["yasyf/cc-skills#8"] = state.new_pr(tooling, "j1", "o2") | {"title": "Fix: \U0001f41b the watch draws the PR card"}
    view.refresh_cards(demo)
    rows = [{"title": "the run page shows its history"}, {"title": "the watch draws the PR card"}]
    monkeypatch.setattr("drive.card.cards", lambda asked: {"cards": [{"repo": "yasyf/cc-skills", "number": 7, "card": {"title": "New: approve 2 PRs?", "prs": rows}}], "skipped": []})
    checked.texts.clear()
    assert DRAW(demo, ["yasyf/cc-skills#7", "yasyf/cc-skills#8"]) == POSTED
    assert posts == [("dm-status", "--agent", "claude", "--drive", "demo", "--as-holder", "--thread", "1794200000.000100", "--tap-pr", PR_URL, "--tap-pr", tooling)]
    assert (said(demo)[0]["text"], checked.texts) == ("the run page shows its history; the watch draws the PR card", [])


def test_a_draw_of_his_card_under_the_waits_id_of_one_he_answered_posts_nothing_until_a_pr_on_it_changed_since(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "show a run's history")
    building(demo)
    tapped = "https://acme.slack.com/archives/D123/p1794300000000300"
    closed = {"waits": "merge:yasyf:cc-skills:7", "title": "New: approve the run page history?", "prs": [PR_URL], "asked": ASKED, "url": tapped, "answer": {"label": "Approve", "at": AWAY}, "tapped": "", "replaced_by": None}
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS | {"closed": [closed]})
    view.refresh_cards(demo)
    rows = [{"title": "the run page shows its history", "changed": ""}]
    monkeypatch.setattr("drive.card.cards", lambda asked: {"cards": [{"repo": "yasyf/cc-skills", "number": 7, "card": {"title": "New: approve the run page history?", "waits": "merge:yasyf:cc-skills:7", "prs": rows}}], "skipped": []})
    with pytest.raises(DriveError, match=f"nothing was posted: his card under merge:yasyf:cc-skills:7 is {tapped}, which he answered"):
        DRAW(demo, ["yasyf/cc-skills#7"])
    assert (posts, said(demo)) == ([], [])
    rows[0]["changed"] = 'Reworked to your words "Own settings key per value": one indexed row per read'
    assert DRAW(demo, ["yasyf/cc-skills#7"]) == POSTED


def test_a_draw_cc_slack_names_no_card_under_is_kept_as_said_so_the_watch_posts_it_not_again(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "show a run's history", "--owner", "coordinator")
    building(demo)
    monkeypatch.setattr("drive.card.cards", lambda asked: {"cards": [{"repo": "yasyf/cc-skills", "number": 7, "card": {"title": "Fix: approve the run page?", "prs": [{"title": "the run page shows its history"}]}}], "skipped": []})
    sent = slack.cc_slack
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {key: value for key, value in sent(*argv).items() if key != "card_permalink"} | {"warning": "the card command did not finish"})
    with pytest.raises(DriveError, match=f"posted {POSTED}, so post it not again, but cc-slack named no card of yasyf/cc-skills#7 under it: the card command did not finish"):
        DRAW(demo, ["yasyf/cc-skills#7"])
    assert [(event["text"], event["url"], event["card"]) for event in said(demo)] == [("the run page shows its history", POSTED, None)]


def test_a_pr_card_goes_under_its_first_line_in_his_dm_and_each_other_open_orders_thread_links_it_so_one_card_carries_the_buttons(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "show a run's history", "--thread", "1794200000.000100", "--owner", "coordinator")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "There's no approval button", "--thread", "1794300000.000100", "--owner", "coordinator")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "why is the page slow?", "--thread", "1794400000.000100", "--owner", "coordinator")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "one more check", "--from", "slack:C9TEAM/1794500000.000100", "--owner", "coordinator")
    building(demo)
    with edit(demo) as data:
        for order_id in ("o2", "o3", "o4"):
            state.carry(data, "j1", order_id, list(data["orders"][order_id]["parts"]))
        data["orders"]["o3"]["state"] = "done"
        data["orders"]["o4"]["incident"] = True
    team = {"text": "land these", "tap_prs": [PR_URL], "channel_id": "C9TEAM", "thread_ts": "1794500000.000100"}
    assert talk.say(demo, team | {"dry_run": True}) == (
        "DRY thread 1794200000.000100, thread 1794300000.000100, thread 1794500000.000100 of channel C9TEAM, 0 pictures, PR card for 1 PRs: land these"
    )
    talk.say(demo, team | TICKET)
    assert posts == [
        ("dm-status", "--agent", "claude", "--drive", "demo", "--as-holder", "--thread", "1794200000.000100", "--tap-pr", PR_URL, "--", "land these"),
        ("dm-status", "--agent", "claude", "--drive", "demo", "--as-holder", "--thread", "1794300000.000100", "--", f"land these\n<{POSTED}|#7>"),
        (
            "reply", "--agent", "claude", "--channel", "C9TEAM", "--thread", "1794500000.000100", "--text", f"land these\n<{POSTED}|#7>",
            "--ticket", "t1", "--ticket-tree", "tree1", "--ticket-agent", "agent1", "--ticket-call", "call1", "--ticket-fingerprint", "print1",
        ),
    ]
    assert [(event.get("channel"), event["prs"]) for event in said(demo)] == [(None, ["yasyf/cc-skills#7"])] * 2 + [("C9TEAM", ["yasyf/cc-skills#7"])]


def test_a_stack_cards_thread_is_a_thread_of_each_order_its_prs_carry_out_not_only_the_order_that_opened_them(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "show a run's history", "--owner", "coordinator")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "the list fix", "--owner", "coordinator")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "an unrelated page", "--thread", "1794450000.000100", "--owner", "coordinator")
    building(demo)
    with edit(demo) as data:
        state.carry(data, "j1", "o2", list(data["orders"]["o2"]["parts"]))
    stack = "1794493484.938019"
    sent = slack.cc_slack
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: sent(*argv) | {"card_permalink": f"https://acme.slack.com/archives/D123/p{stack.replace('.', '')}"})
    talk.say(demo, {"text": "land these", "tap_prs": [PR_URL]})
    data = state.load(demo)
    assert [("D123", stack) in slack.order_threads(data, order, data["orders"][order]) for order in ("o1", "o2", "o3")] == [True, True, False]
    assert state.thread_order(data, stack) == "o2"


def test_a_pr_cards_line_that_fails_in_a_later_thread_says_the_card_went_out_so_none_is_posted_again(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "show a run's history", "--thread", "1794200000.000100", "--owner", "coordinator")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "There's no approval button", "--thread", "1794300000.000100", "--owner", "coordinator")
    building(demo)
    with edit(demo) as data:
        state.carry(data, "j1", "o2", list(data["orders"]["o2"]["parts"]))

    def slack_post(*argv: str) -> dict:
        if "1794300000.000100" in argv:
            raise slack.SlackError("ratelimited")
        return {"permalink": POSTED, "thread_ts": "1794200000.000100"}

    monkeypatch.setattr(slack, "cc_slack", slack_post)
    with pytest.raises(slack.SlackError, match=f"posted {POSTED} with its card, but its line in thread 1794300000.000100 did not go out, so post neither again: ratelimited"):
        talk.say(demo, {"text": "land these", "tap_prs": [PR_URL]})
    assert [event["url"] for event in said(demo)] == [POSTED]


@pytest.mark.parametrize(
    ("caption", "title"),
    [("p95 / p99 [ms]: before and after", "p95 p99 ms before and after"), ("", "show-me"), ("  ", "show-me")],
)
def test_a_local_image_goes_to_slack_as_a_copy_named_for_the_title_slack_gives_it(
    demo: str, posts: list[tuple[str, ...]], tmp_path: Path, caption: str, title: str
) -> None:
    shot = tmp_path / "Screenshot 2026-11-10 at 9.41.12 PM.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")
    talk.say(demo, {"text": "The page", "visuals": [{"kind": "image", "source": str(shot), "caption": caption}]})
    sent = Path(posts[0][posts[0].index("--file") + 1])
    assert (sent.name, sent.read_bytes()) == (f"{title}.png", shot.read_bytes())
    assert said(demo)[0]["visuals"][0]["picture"] == str(sent)

REFUSAL = "nothing posted: only the communicator posts to him: send these words as a report record to communicator@demo, which posts and records them"


def test_705235_a_post_from_a_session_not_the_communicator_goes_nowhere_with_or_without_a_record(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, posts: list[tuple[str, ...]], unrecorded: list[str]
) -> None:
    thread = "1794261099.160079"
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "SandSQL", "why app actions fail", "--thread", thread, "--owner", "coordinator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = "communicator-session"
    report(monkeypatch, {"order": "o1", "stall": "true", "thread": thread}, lane="watch")
    line = {"text": "The copy still stalls on the lock.", "thread_ts": thread}
    for each in (line | {"record": "705228", "dry_run": True}, line | {"record": "705228"}, line | {"dry_run": True}, line):
        with pytest.raises(DriveError, match=f"^{re.escape(REFUSAL)}$"):
            talk.say(demo, each)
    assert (posts, said(demo), unrecorded, state.load(demo)["orders"]["o1"]["last_line"]) == ([], [], [], None)


@pytest.mark.parametrize(
    ("attrs", "lane", "text", "recorded"),
    [
        (
            {"job": "j1", "order": "o1", "progress": "true", "thread": "1794200000.000100"},
            "platform",
            "*One team's reads held the customer primary at its 12-core limit 3:00–3:45 PM:* 55 runs read 655 million rows.",
            "o1 progress line, no part delivered: {answer}",
        ),
        (
            {"job": "j1", "order": "o1", "part": "o1:fix", "thread": "1794200000.000100"},
            "root",
            '*App actions no longer fail with "is not materialized on this node":* a read or write of a moved table waits for its move.',
            'o1 "fix": posted {answer}',
        ),
    ],
)
def test_a_post_in_its_orders_thread_is_recorded_on_that_order_though_the_record_runs_as_no_session(
    demo: str,
    run: Callable[..., tuple[int, str]],
    monkeypatch: pytest.MonkeyPatch,
    slack_thread: Thread,
    unrecorded: list[str],
    attrs: dict[str, str],
    lane: str,
    text: str,
    recorded: str,
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "SandSQL", "why app actions fail", "--kind", "answer", "--thread", thread, "--parts", "fix")
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    answer = slack_thread.post(state.now(), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": answer, "thread_ts": thread})
    report(monkeypatch, attrs, lane=lane)
    assert talk.say(demo, {"text": text, "thread_ts": thread, "record": "683585"}) == f"POSTED {answer}; {recorded.format(answer=answer)}"
    assert unrecorded == []
    assert state.load(demo)["orders"]["o1"]["last_line"] is not None


def test_a_post_on_an_order_goes_out_in_any_thread_and_a_thread_the_order_holds_not_yet_joins_its_threads(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL errors on plat", "--thread", "1794200000.000100", "--incident", "--parts", "fix", "first check")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL error log count", "--from", "slack:D123/1794300000.000100", "--thread", "1794300000.000100", "--joins", "o1")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why the copy stalls", "--thread", "1794400000.000100")
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"

    def say(thread: str | None, order: str) -> str:
        posted = slack_thread.post(state.now() + timedelta(minutes=len(slack_thread.messages) + 1), from_claude=True, thread_ts=thread).split("?")[0]
        line = f"{posted}?thread_ts={thread}" if thread else posted
        monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": line, "thread_ts": thread})
        report(monkeypatch, {"order": order})
        return talk.say(demo, {"text": f"The copy stalls on the lock for {order}.", "thread_ts": thread, "record": str(683594 + len(slack_thread.messages))}).removeprefix(f"POSTED {line}; {order} progress line, no part delivered: {line}")

    assert [say(thread, order) for thread, order in [("1794300000.000100", "o1"), ("1794200000.000100", "o2"), (None, "o3")]] == ["", "", ""]
    assert say("1794400000.000100", "o1") == ", in thread 1794400000.000100, now one of its threads"
    assert say("1794400000.000100", "o2") == ""
    assert say("1794200000.000100", "o3") == ", in thread 1794200000.000100, now one of its threads"
    data = state.load(demo)
    assert [[thread for _, thread, _, _ in data["orders"][order]["threads"]] for order in ("o1", "o2", "o3")] == [["1794400000.000100"], [], ["1794200000.000100"]]
    assert (state.thread_order(data, "1794400000.000100"), state.thread_order(data, "1794200000.000100")) == ("o1", "o3")


def ended(minutes: int) -> str:
    """An end that many minutes from now, as a writer names a claim's until: RFC 3339 in Pacific's offset."""
    return (state.now() + timedelta(minutes=minutes)).astimezone(PACIFIC).isoformat(timespec="minutes")


def report(monkeypatch: pytest.MonkeyPatch, attrs: dict[str, str], lane: str = "root", kind: str = "report") -> None:
    record = {"kind": kind, "lane": lane, "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": attrs}}
    monkeypatch.setattr(cli.reads, "run", lambda argv, checkout: json.dumps({"seq": argv[2], **record}))


def answering_his(name: str, order_id: str, message: str | None = None) -> None:
    with edit(name) as data:
        channel, thread = data["orders"][order_id]["thread"]
        his = message or thread
        data["orders"][order_id]["from"] = f"slack:{channel}/{his}"
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]


def test_a_reply_goes_on_the_order_of_the_record_of_his_words_it_carries_delivering_no_part_and_one_with_no_record_on_none_each_the_last_line_of_every_order_in_its_thread(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread, unrecorded: list[str]
) -> None:
    thread, his = "1794200000.000100", "1794201723.677000"
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Releases", "ship the SandSQL fix", "--thread", thread, "--parts", "leave it frozen")
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Releases", "make staging fleets large", "--thread", thread)
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = "communicator-session"
        data["roles"]["coordinator"]["session"] = "elsewhere"
        data["orders"]["o2"]["created"] = state.stamp(state.now() + timedelta(minutes=1))
    answering_his(demo, "o2", his)
    assert state.thread_order(state.load(demo), thread) == "o2"
    answer = slack_thread.post(state.now(), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": answer, "thread_ts": thread})
    report(monkeypatch, {"order": "o1", "part": "o1:leave it frozen", "thread": thread}, lane="communicator", kind="owner")
    post = {"text": "Your ask to lift the freeze is withdrawn; the release stays frozen.", "thread_ts": thread}
    assert talk.say(demo, post | {"record": "698123", "dry_run": True}).endswith("; o1 progress line, no part delivered")
    assert talk.say(demo, post | {"record": "698123"}) == f"POSTED {answer}; o1 progress line, no part delivered: {answer}"
    data = state.load(demo)
    lined = state.stamp(slack.sent_at(slack.posted(answer, "")))
    assert (data["orders"]["o1"]["last_line"] is not None, data["orders"]["o2"]["last_line"], data["orders"]["o1"]["parts"]["leave it frozen"]["posted"]) == (True, lined, None)
    assert said(demo)[-1]["order"] == "o1"
    assert talk.say(demo, post | {"text": "The release stays frozen."}) == f"POSTED {answer}"
    assert (said(demo)[-1]["order"], state.load(demo)["orders"]["o2"]["last_line"], unrecorded) == (None, lined, [])


def test_a_post_delivers_the_part_its_report_names_and_a_progress_report_naming_only_a_job_delivers_none(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why it fails, and the options", "--kind", "answer", "--thread", thread, "--parts", "cause", "options as a card")
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
        data["jobs"]["j1"] = state.new_job("o1", "codex", "find the cause", ["cause"], state.stamp()) | {"state": "done", "said": "DONE the cause"}
        data["orders"]["o1"]["jobs"].append("j1")
    answer = slack_thread.post(state.now(), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": answer, "thread_ts": thread})
    report(monkeypatch, {"job": "j1", "thread": thread})
    progress = talk.say(demo, {"text": "Still reading the lock.", "thread_ts": thread, "record": "661193"})
    assert progress == f"POSTED {answer}; o1 progress line, no part delivered: {answer}"
    assert [part["posted"] for part in state.load(demo)["orders"]["o1"]["parts"].values()] == [None, None]
    report(monkeypatch, {"job": "j1", "thread": thread, "part": "cause"})
    assert talk.say(demo, {"text": "The lock is held across the whole call.", "thread_ts": thread, "record": "661194"}) == f'POSTED {answer}; o1 "cause": posted {answer}'
    parts = state.load(demo)["orders"]["o1"]["parts"]
    assert (parts["cause"]["posted"], parts["options as a card"]["posted"]) == (answer, None)
    later = state.now() + timedelta(minutes=30)
    gaps = watch.part_gaps(watch.Watcher(demo, offset=0), state.load(demo), later)
    assert gaps == {("o1", "cause"): None, ("o1", "options as a card"): "no job"}


def test_a_report_naming_two_parts_of_its_order_in_its_one_part_attr_delivers_both(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Releases", f"1) {SSQL}, and 2) {FOOTERS}?", "--kind", "answer", "--thread", thread, "--parts", SSQL, FOOTERS)
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    answer = slack_thread.post(state.now(), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": answer, "thread_ts": thread})
    report(monkeypatch, {"order": "o1", "thread": thread, "part": f"o1:{SSQL};o1:{FOOTERS}"})
    said = "Yes, the SandSQL release is live. The footers missed it because that release named only api."
    assert talk.say(demo, {"text": said, "thread_ts": thread, "record": "677031"}) == f'POSTED {answer}; o1 "{SSQL}", "{FOOTERS}": posted {answer}'
    assert {part: kept["posted"] for part, kept in state.load(demo)["orders"]["o1"]["parts"].items()} == {SSQL: answer, FOOTERS: answer}


CAUSE_REPORT = (
    "Cause of the QUEUE_FULL refusals from 23:01Z to 23:45Z: load on the shared pod, not the handover stall and not a restore. Its"
    " primary ran at 5.1 to 6.1 cores of its 6.1-core limit for those minutes, so the 32-deep write queues filled. No open PR stops"
    " one team's queries from filling another team's write queue."
)


def test_a_report_naming_an_incidents_fix_and_cause_delivers_the_cause_its_stopped_diagnosis_job_found_never_the_fix_and_asks_no_judge(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread, jev: Jev
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Incidents", "QUEUE_FULL refusals", "--thread", thread, "--incident", "--parts", "first check", "fix", "cause")
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
        data["jobs"]["j3"].update(state="stopped", said="HANDOFF the shared pod's load filled the write queues")
    later = state.now() + timedelta(minutes=30)
    assert watch.part_gaps(watch.Watcher(demo, offset=0), state.load(demo), later)[("o1", "cause")] == "j3 stopped"
    answer = slack_thread.post(state.now(), from_claude=True, text=CAUSE_REPORT)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": answer, "thread_ts": thread})
    report(monkeypatch, {"incident": "true", "order": "o1", "thread": thread, "part": "o1:fix;o1:cause"})
    dry = talk.say(demo, {"text": CAUSE_REPORT, "thread_ts": thread, "record": "682974", "dry_run": True})
    assert dry.endswith('; o1 "cause": would be delivered')
    line = talk.say(demo, {"text": CAUSE_REPORT, "thread_ts": thread, "record": "682974"})
    assert line.startswith(f'POSTED {answer}; o1 "cause": posted {answer}')
    assert {part: kept["posted"] for part, kept in state.load(demo)["orders"]["o1"]["parts"].items()} == {"first check": None, "fix": None, "cause": answer}
    assert watch.part_gaps(watch.Watcher(demo, offset=0), state.load(demo), later)[("o1", "cause")] is None
    assert jev.asked == []


@pytest.mark.parametrize(
    ("text", "due"),
    [
        ("Next line by 9:45.", "Nov 11 21:45"),
        ("Next line by 9:45 PM.", "Nov 11 21:45"),
        ("Next update at 10:00", "Nov 11 22:00"),
        ("Next post before 12:15 a.m.", "Nov 12 00:15"),
        ("Next line by 21:45", "Nov 11 21:45"),
        ("It was green by 9:22, so next line by 9:45", "Nov 11 21:45"),
        ("The fix is due by 9:50, and the next line by 9:40.", "Nov 11 21:40"),
        ("The last failure was at 9:22:53 PM, and both jobs started at 9:27.", None),
        ("Its folder was empty by 7:30.", None),
        ("You get it before 12:15 a.m.", "Nov 12 00:15"),
        ("Its count of cards comes by 9:58 PM.", "Nov 11 21:58"),
        ("The fix lands before 10:30, and the next line by 10:00.", "Nov 11 22:00"),
        ("Next line by 9:45 AM.", None),
        ("Next line by 10:00 AM tomorrow.", "Nov 12 10:00"),
        ("Next line by 9:00 PT tomorrow", "Nov 12 09:00"),
        ("Next update at 8:00 on Nov 13", "Nov 13 08:00"),
    ],
)
def test_a_post_promises_the_earliest_clock_time_after_by_or_before_in_any_words_read_as_the_time_of_day_nearest_it_when_that_is_ahead(text: str, due: str | None) -> None:
    found = cli.promised_due(text, datetime(2026, 11, 11, 21, 29, 43, 954739).astimezone())
    assert (found and f"{found:%b %d %H:%M}") == due


def test_a_post_promising_a_fix_by_2_37_and_the_next_line_by_2_32_owes_the_earlier_2_32() -> None:
    found = cli.promised_due("The fix is due by 2:37, and you get the next line by 2:32.", datetime(2026, 11, 12, 2, 23, 28, 890659).astimezone())
    assert (found and f"{found:%b %d %H:%M}") == "Nov 12 02:32"


def test_a_post_on_an_incident_records_its_line_and_keeps_no_time_its_words_promise(
    demo: str, run: Callable[..., tuple[int, str]], slack_thread: Thread
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Alerts", "Iris chat turns fail on sand 0.162.3983", "--thread", thread, "--incident", "--parts", "fix", "first check")
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    at = datetime(2026, 11, 11, 21, 29, 43, 954739).astimezone()
    line = slack_thread.post(at, from_claude=True, text="The fix job is changing the turn's retry. Next line by 9:45.")
    code, said = run("order", "posted", "o1", line)
    assert (code, said.startswith(f"o1 progress line, no part delivered: {line}"), "promise" in said) == (0, True, False)
    told = state.load(demo)["orders"]["o1"]
    assert told["last_line"] == state.stamp(slack.sent_at(slack_thread.messages[-1]))


def test_a_say_of_a_record_retried_after_cc_slack_posted_and_failed_hands_cc_slack_the_records_key_again_and_keeps_the_one_post_slack_holds(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why the copy stalls", "--thread", thread)
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "https://acme.slack.com/", "questions": [], "pr_cards": [], "closed": []})
    keys: list[str] = []
    held: dict[str, str] = {}

    def cc_slack(*argv: str) -> dict:
        keys.append(argv[argv.index("--request-key") + 1])
        if keys[-1] not in held:
            held[keys[-1]] = slack_thread.post(state.now(), from_claude=True, text=argv[-1])
            raise slack.SlackError("context deadline exceeded")
        return {"permalink": held[keys[-1]], "thread_ts": thread}

    monkeypatch.setattr(slack, "cc_slack", cc_slack)
    answering_his(demo, "o1")
    report(monkeypatch, {"order": "o1", "thread": thread}, lane="communicator", kind="owner")
    post = {"text": "The copy stalls on the lock.", "thread_ts": thread, "record": "690001"}
    with pytest.raises(slack.SlackError, match="context deadline exceeded"):
        talk.say(demo, post)
    line = talk.say(demo, post)
    [link] = held.values()
    assert keys[0] == keys[1] and keys[0].startswith("1794388096896-demo.690001.")
    assert line == f"POSTED {link}; o1 progress line, no part delivered: {link}"
    assert [(kept["url"], kept["order"]) for kept in said(demo)] == [(link, "o1")]


def test_a_records_post_keys_on_its_words_so_a_retry_after_a_timeout_reposts_its_line_under_one_key_and_a_carried_record_posts_nothing_again(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why the copy stalls", "--thread", thread)
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    report(monkeypatch, {"order": "o1", "thread": thread}, lane="communicator", kind="owner")
    sending, timeouts = slack.cc_slack, [True, True]

    def timing_out(*argv: str) -> dict:
        reply = sending(*argv)
        if timeouts and timeouts.pop():
            raise slack.SlackError("deadline exceeded")
        return reply

    monkeypatch.setattr(slack, "cc_slack", timing_out)
    for text in ("The copy stalls on the lock.", "The lock is released."):
        with pytest.raises(slack.SlackError):
            talk.say(demo, {"text": text, "thread_ts": thread, "record": "690001"})
    talk.say(demo, {"text": "The copy stalls on the lock.", "thread_ts": thread, "record": "690001"})
    with pytest.raises(state.DriveError, match="report 690001 went out meanwhile"):
        talk.say(demo, {"text": "The lock is released.", "thread_ts": thread, "record": "690001"})
    keys = [argv[argv.index("--request-key") + 1] for argv in posts]
    assert keys[0] == keys[2] != keys[1] and len(keys) == 3
    assert all(key.startswith("1794388096896-demo.690001.") for key in keys)


def test_a_report_delivers_on_an_order_only_from_the_lane_of_its_holder_and_a_refusal_names_the_report_the_handler_passes(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Questions", "which page shows a run's history?", "--thread", thread, "--owner", "communicator")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "show a run's history", "--thread", thread, "--owner", "coordinator")
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    sent: list[tuple[str, ...]] = []
    answer = slack_thread.post(state.now(), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: sent.append(argv) or {"permalink": answer, "thread_ts": thread})
    report(monkeypatch, {"close": "o1"})
    with pytest.raises(
        DriveError,
        match=(
            r"nothing posted: refused, the report is the coordinator's and o1 is the communicator's order, so it delivers nothing there:"
            r" post your own report on the cci post tool with kind report, lane communicator, attrs \{close: o1\} and no to"
        ),
    ):
        talk.say(demo, {"text": "The run page shows it.", "thread_ts": thread, "record": "664348"})
    assert (sent, said(demo)) == ([], [])
    report(monkeypatch, {"close": "o1"}, lane="communicator")
    assert talk.say(demo, {"text": "The run page shows it.", "thread_ts": thread, "record": "664350"}) == f'POSTED {answer}; o1 closing line, no part delivered: {answer}'
    report(monkeypatch, {"close": "o2"}, lane="communicator")
    with pytest.raises(DriveError, match=r"refused, the report is the communicator's and o2 is the coordinator's order, so it delivers nothing there: pass the coordinator's own report on o2"):
        talk.say(demo, {"text": "The run page is live.", "thread_ts": thread, "record": "664351"})
    report(monkeypatch, {"close": "o2"})
    assert talk.say(demo, {"text": "The run page is live.", "thread_ts": thread, "record": "664352"}) == f'POSTED {answer}; o2 closing line, no part delivered: {answer}'
    assert len(sent) == 2


def test_a_report_naming_no_order_goes_on_the_newest_open_order_in_its_thread_as_a_progress_line_whoever_wrote_it_and_one_naming_a_part_only_from_its_holder(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "show a run's history", "--thread", thread, "--owner", "coordinator")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix the post tool", "--thread", thread, "--owner", "meta")
    communicating(run, demo)
    with edit(demo) as data:
        data["orders"]["o2"]["state"] = "done"
    answer = slack_thread.post(state.now(), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": answer, "thread_ts": thread})
    report(monkeypatch, {"thread": thread}, lane="meta")
    assert talk.say(demo, {"text": "The fix is building.", "thread_ts": thread, "record": "720574"}) == f"POSTED {answer}; o1 progress line, no part delivered: {answer}"
    report(monkeypatch, {"thread": thread, "part": "Short title"}, lane="meta")
    with pytest.raises(DriveError, match=r"nothing posted: refused, the report is the meta session's and o1 is the coordinator's order, so it delivers nothing there"):
        talk.say(demo, {"text": "The run page shows it.", "thread_ts": thread, "record": "720580"})


def test_say_records_its_post_from_when_cc_slack_returns_it_waiting_out_a_database_write_held_past_the_wait_before_any_write_of_its_own(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread, unrecorded: list[str]
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Questions", "which page shows a run's history?", "--thread", thread, "--owner", "communicator")
    communicating(run, demo)
    monkeypatch.setattr(state, "WRITE_BUDGET_SECONDS", 0.2)
    answer = slack_thread.post(state.now(), from_claude=True)

    def held_once_posted(*argv: str) -> dict:
        stuck = sqlite3.connect(state.database_path(demo), isolation_level=None, check_same_thread=False)
        stuck.execute("BEGIN IMMEDIATE")
        threading.Timer(4 * state.WRITE_BUDGET_SECONDS, stuck.close).start()
        return {"permalink": answer, "thread_ts": thread}

    monkeypatch.setattr(slack, "cc_slack", held_once_posted)
    report(monkeypatch, {"order": "o1"}, lane="communicator")
    assert talk.say(demo, {"text": "The run page shows it.", "thread_ts": thread, "record": "720560"}) == f"POSTED {answer}; o1 progress line, no part delivered: {answer}"
    assert (state.load(demo)["carried"], unrecorded) == ({"720560": answer}, [])


def test_a_second_report_closing_one_order_posts_nothing_whoever_wrote_it_and_a_failed_post_gives_its_close_back(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread, capsys: pytest.CaptureFixture[str]
) -> None:
    thread = "1794200000.000100"
    for words in ("one fix-live post per order", "one fix-live post per order, again"):
        run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", words, "--thread", thread, "--owner", "meta")
    communicating(run, demo)
    sent: list[tuple[str, ...]] = []
    answer = slack_thread.post(state.now(), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: sent.append(argv) or {"permalink": answer, "thread_ts": thread})
    report(monkeypatch, {"close": "o1"}, lane="watch")
    assert talk.say(demo, {"text": "The fix is live.", "thread_ts": thread, "record": "684712"}).startswith(f"POSTED {answer}")
    report(monkeypatch, {"close": "o1"}, lane="meta")
    refused = "refused, report 684712 already closes o1, so this second close of it posts nothing: send no close of o1"
    assert "WARNING report 684712 already closes o1; the post went out without its close of o1" in talk.say(demo, {"text": "The fix is live on prod.", "thread_ts": thread, "record": "684713"})
    assert "WARNING" not in talk.say(demo, {"text": "The fix is live again.", "thread_ts": thread, "record": "684714"})
    assert len(sent) == 3
    capsys.readouterr()
    assert cli.main(["order", "set", "o1", "--state", "done", "--record", "684713"]) == 2
    assert capsys.readouterr().err.strip() == f"drive: o1: {refused}"

    def failing(*argv: str) -> dict:
        raise slack.SlackError("context deadline exceeded")

    monkeypatch.setattr(slack, "cc_slack", failing)
    report(monkeypatch, {"close": "o2"}, lane="watch")
    with pytest.raises(slack.SlackError):
        talk.say(demo, {"text": "The second fix is live.", "thread_ts": thread, "record": "686065"})
    assert state.load(demo)["orders"]["o2"]["closed_on"] is None
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: sent.append(argv) or {"permalink": answer, "thread_ts": thread})
    report(monkeypatch, {"close": "o2"}, lane="meta")
    assert talk.say(demo, {"text": "The second fix is live now.", "thread_ts": thread, "record": "686070"}).startswith(f"POSTED {answer}")
    report(monkeypatch, {"close": "o2"}, lane="watch")
    assert "WARNING report 686070 already closes o2" in talk.say(demo, {"text": "The second fix is live.", "thread_ts": thread, "record": "686065"})
    assert len(sent) == 5


def own_pr(name: str) -> None:
    with edit(name) as data:
        data["orders"]["o1"] = state.new_order("show the run page's history", "terminal", None, {}, ASKED)
        data["jobs"]["j1"] = state.new_job("o1", "builder", "show the run page's history", [], ASKED)


def test_amendments_reads_each_correction_and_withdrawal_re_the_reports_in_one_cci_grep_oldest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    newest_first = [
        {"seq": 669995, "kind": "withdraw", "re": 669902},
        {"seq": 669990, "kind": "correction", "re": 669950},
        {"seq": 669970, "kind": "correction", "re": 669960},
        {"seq": 669960, "kind": "correction"},
        {"seq": 669952, "kind": "correction", "re": 669950},
    ]
    calls: list[list[str]] = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout: calls.append(argv) or "".join(json.dumps(record) + "\n" for record in newest_first))
    assert AMENDMENTS("demo", ["669950", "669902"]) == {"correction": {"669950": ["669952", "669990"]}, "withdraw": {"669902": ["669995"]}}
    assert AMENDMENTS("demo", []) == {"correction": {}, "withdraw": {}}
    assert calls == [
        ["cci", "grep", ".", "--drive", "demo", "--kind", "correction", "--kind", "withdraw", "--since", "669902", "--budget", str(agents.AMENDMENT_BYTES), "--json"]
    ]


def test_a_report_post_names_each_correction_cci_holds_re_it(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch, cci_amendments: dict[str, dict[str, list[str]]]) -> None:
    report(monkeypatch, {"orders": "o1"})
    cci_amendments["correction"]["669950"] = ["669952"]
    assert talk.say(demo, {"text": "Each request reads 64 MiB.", "record": "669950"}) == (
        f"POSTED {POSTED}; cci holds corrections of report 669950, 669952: unless this post carries each, reword it with say(edit={POSTED}, text=…)"
    )


def test_a_post_citing_its_record_as_cci_prints_it_reads_its_corrections_by_the_seq_cci_keeps(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch, cci_amendments: dict[str, dict[str, list[str]]]
) -> None:
    shown = {"seq": 669950, "num": "#669950", "kind": "report", "lane": "root", "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": {"orders": "o1"}}}
    monkeypatch.setattr(cli.reads, "run", lambda argv, checkout: json.dumps(shown))
    cci_amendments["correction"]["669950"] = ["669952"]
    assert talk.say(demo, {"text": "Each request reads 64 MiB.", "record": "#669950"}) == (
        f"POSTED {POSTED}; cci holds corrections of report 669950, 669952: unless this post carries each, reword it with say(edit={POSTED}, text=…)"
    )


def test_a_correction_of_a_report_already_posted_edits_the_post_it_went_out_in(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch, cci_amendments: dict[str, dict[str, list[str]]]
) -> None:
    records = {"669950": {"kind": "report", "attrs": {"orders": "o1"}}, "669980": {"kind": "correction", "re": 669950}}

    def shown(argv: list[str], checkout: Path) -> str:
        record = records[argv[2]]
        return json.dumps({"seq": argv[2], "lane": "root", "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": record.get("attrs", {})}} | record)

    monkeypatch.setattr(cli.reads, "run", shown)
    talk.say(demo, {"text": "Each request reads 64 MiB.", "record": "669950"})
    with edit(demo) as data:
        data["carried"]["669980"] = agents.DRIVE_CLAIM
        data["posts"]["claimed"] = {"669980": state.stamp(state.now() - agents.UNPOSTED_AFTER - timedelta(minutes=1))}
    assert talk.say(demo, {"text": "Each request reads 16 MiB.", "record": "669980"}).startswith(f"EDITED {POSTED}")
    assert state.load(demo, ("carried",))["carried"]["669980"] == POSTED
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: pytest.fail(f"flagged a posted correction: {records}"))
    agents.unposted(demo, state.now())
    assert state.load(demo, ("posts",))["posts"]["claimed"] == {}


def test_a_post_goes_out_in_exactly_the_words_it_was_given_while_built_work_waits_on_his_card(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    view.refresh_cards(demo)
    thread = "1794200000.000100"
    with edit(demo) as data:
        data["orders"]["o1"] = state.new_order("lock it", "terminal", ("D123", thread), {"lock it": state.new_part()}, ASKED) | {"state": "working", "jobs": ["j1"]}
        data["jobs"]["j1"] = state.new_job("o1", "builder", "lock it", ["lock it"], ASKED) | {"state": "built", "branch": "lane/lock", "waits": [WAITS]}
    talk.say(demo, {"text": "Shipped.", "thread_ts": thread})
    talk.say(demo, {"text": "Catch-up"})
    report(monkeypatch, {"orders": "o1"})
    talk.say(demo, {"text": "The fix is live.", "record": "660299"})
    assert [argv[-1] for argv in posts] == ["Shipped.", "Catch-up", "The fix is live."]


def test_a_pr_is_linked_as_its_number_and_the_words_the_post_gives_it_whether_the_drive_holds_it_or_not(demo: str, posts: list[tuple[str, ...]]) -> None:
    talk.say(demo, {"text": "{pr:7|the run page fix} and {pr:yasyf/cc-skills#4250|the card that draws charts} are live."})
    assert posts[0][-1] == f"<{PR_URL}|#7 the run page fix> and <https://github.com/yasyf/cc-skills/pull/4250|#4250 the card that draws charts> are live."
    assert said(demo)[0]["text"] == f"[#7 the run page fix]({PR_URL}) and [#4250 the card that draws charts](https://github.com/yasyf/cc-skills/pull/4250) are live."
    with pytest.raises(DriveError, match=r"\{pr:yasyf/cc-skills#4250\} names no PR of the drive's"):
        talk.say(demo, {"text": "{pr:yasyf/cc-skills#4250} is live."})


def test_a_pr_in_a_table_cell_is_linked_under_its_words_whether_the_bar_before_them_is_escaped_for_the_table_or_not(
    demo: str, posts: list[tuple[str, ...]]
) -> None:
    rows = "| PR | what |\n|---|---|\n| {first} | lands |\n| {second} | lands |"
    talk.say(demo, {"text": rows.format(first="{pr:7\\|the run page fix}", second="{pr:yasyf/cc-skills#4250|the card that draws charts}")})
    assert posts[0][-1] == rows.format(first=f"<{PR_URL}|#7 the run page fix>", second="<https://github.com/yasyf/cc-skills/pull/4250|#4250 the card that draws charts>")
    assert said(demo)[0]["text"] == rows.format(first=f"[#7 the run page fix]({PR_URL})", second="[#4250 the card that draws charts](https://github.com/yasyf/cc-skills/pull/4250)")


ALERT = "1794436805.002349"
TICKET = {"ticket_id": "t1", "ticket_tree": "tree1", "ticket_agent": "agent1", "ticket_call": "call1", "ticket_fingerprint": "print1"}


def alert_order(run: Callable[..., tuple[int, str]], slack_thread: Thread) -> None:
    slack_thread.messages.append({"ts": ALERT, "from_user": False})
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the release failed", "--from", f"slack:C0ALERT1/{ALERT}", "--incident", "--parts", "fix", "first check", "--owner", "coordinator")


def communicating(run: Callable[..., tuple[int, str]], name: str) -> None:
    run("start", name, "--role", "communicator")
    with edit(name) as data:
        data["roles"]["coordinator"]["session"] = "elsewhere"


def test_a_line_in_an_alerts_thread_goes_out_through_cc_slack_reply_with_the_ticket_its_hook_named(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    alert_order(run, slack_thread)
    report(monkeypatch, {"order": "o1"})
    line = {"text": "The release failed on the migration.", "channel_id": "C0ALERT1", "thread_ts": ALERT, "record": "683597"}
    assert talk.say(demo, line | {"dry_run": True}) == f"DRY thread {ALERT} of channel C0ALERT1, 0 pictures: The release failed on the migration.; o1 progress line, no part delivered"
    assert talk.say(demo, line | TICKET).startswith(f"POSTED {POSTED}")
    assert posts == [
        (
            "reply", "--agent", "claude", "--channel", "C0ALERT1", "--thread", ALERT, "--text", "The release failed on the migration.",
            "--request-key", "1794388096896-demo.683597.ed6ee70d9e1d9efd", "--ticket", "t1", "--ticket-tree", "tree1", "--ticket-agent", "agent1", "--ticket-call", "call1", "--ticket-fingerprint", "print1",
        )
    ]
    assert said(demo)[0]["channel"] == "C0ALERT1"
    assert talk.say(demo, {"text": "The release failed on the migration.", "thread_ts": ALERT, "dry_run": True}).startswith(f"DRY thread {ALERT} of channel C0ALERT1, ")
    assert len(posts) == 1


def test_a_reports_post_naming_no_place_goes_in_the_thread_and_channel_its_record_names(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    alert_order(run, slack_thread)
    report(monkeypatch, {"order": "o1", "thread": ALERT})
    line = {"text": "The release failed on the migration.", "record": "683597"}
    assert talk.say(demo, line | {"dry_run": True}).startswith(f"DRY thread {ALERT} of channel C0ALERT1, ")
    card = "1794439546.145729"
    report(monkeypatch, {"order": "o1", "thread": card})
    assert talk.say(demo, line | {"dry_run": True}).startswith(f"DRY thread {card}, ")
    assert posts == []


def test_an_edit_of_a_line_in_an_alerts_thread_goes_out_with_the_ticket_its_hook_named_and_its_claims_posting_none_of_them_and_the_dashboard_shows_its_new_words(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], slack_thread: Thread
) -> None:
    alert_order(run, slack_thread)
    talk.say(demo, {"text": "The release failed on the migration.", "channel_id": "C0ALERT1", "thread_ts": ALERT} | TICKET)
    claims = [{"claim": "The release failed on the migration", "read": "tools/ci release status"}]
    assert talk.say(demo, {"edit": POSTED, "text": "The release failed on the migration {pr:7} fixes.", "claims": claims} | TICKET) == f"EDITED {POSTED}"
    assert posts[1] == (
        "edit", "--url", POSTED, "--text", f"The release failed on the migration <{PR_URL}|#7> fixes.",
        "--ticket", "t1", "--ticket-tree", "tree1", "--ticket-agent", "agent1", "--ticket-call", "call1", "--ticket-fingerprint", "print1",
    )
    history = view.history(state.load(demo), (event for event, _ in state.read_events(demo)), "https://acme.slack.com/")
    [shown] = [entry for entry in history if entry["kind"] == "said"]
    assert shown["text"] == f"The release failed on the migration [the run page shows its history]({PR_URL}) fixes."


@pytest.mark.parametrize(
    ("post", "why"),
    [
        ({"channel_id": "D123", "thread_ts": ALERT}, "channel_id D123 is his DM; leave channel_id out"),
        ({"channel_id": "C0ALERT1", "thread_ts": ALERT, "about": WAITS}, "about names the card a reply in his DM is about"),
        ({"channel_id": "U0REPORTER", "card": {"title": QUESTION}}, "a card goes up in his DM and its line in a thread outside it, so with channel_id U0REPORTER it takes"),
    ],
)
def test_a_post_outside_his_dm_names_no_dm_of_his_and_takes_nothing_only_his_dm_draws(demo: str, posts: list[tuple[str, ...]], post: dict, why: str) -> None:
    with pytest.raises(DriveError, match=f"nothing posted: {re.escape(why)}"):
        talk.say(demo, {"text": "The release failed on the migration."} | post)
    assert (posts, said(demo)) == ([], [])


def test_a_card_on_an_order_in_an_alerts_thread_goes_up_in_his_dm_and_its_line_goes_in_the_alerts_thread(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], slack_thread: Thread
) -> None:
    alert_order(run, slack_thread)
    bare = {"text": "Your pick holds the fix.", "card": {"title": QUESTION}, "thread_ts": ALERT}
    assert talk.say(demo, bare | {"dry_run": True}) == f"DRY his DM root, thread {ALERT} of channel C0ALERT1, 0 pictures, card {QUESTION!r}: Your pick holds the fix."
    assert posts == []
    card = {"text": "Your pick holds the fix.", "card": {"title": QUESTION}, "channel_id": "C0ALERT1", "thread_ts": ALERT}
    assert talk.say(demo, card | {"dry_run": True}) == f"DRY his DM root, thread {ALERT} of channel C0ALERT1, 0 pictures, card {QUESTION!r}: Your pick holds the fix."
    assert talk.say(demo, card | TICKET) == f"POSTED {POSTED} CARD {POSTED}"
    talk.say(demo, {"text": "Ready to land.", "tap_prs": [PR_URL], "channel_id": "C0ALERT1", "thread_ts": ALERT} | TICKET)
    tickets = ("--ticket", "t1", "--ticket-tree", "tree1", "--ticket-agent", "agent1", "--ticket-call", "call1", "--ticket-fingerprint", "print1")
    assert posts == [
        ("dm-status", "--agent", "claude", "--drive", "demo", "--as-holder", "--card", json.dumps({"title": QUESTION}), "--", "Your pick holds the fix."),
        ("reply", "--agent", "claude", "--channel", "C0ALERT1", "--thread", ALERT, "--text", f"Your pick holds the fix.\n<{POSTED}|{QUESTION}>", *tickets),
        ("dm-status", "--agent", "claude", "--drive", "demo", "--as-holder", "--tap-pr", PR_URL, "--", "Ready to land."),
        ("reply", "--agent", "claude", "--channel", "C0ALERT1", "--thread", ALERT, "--text", f"Ready to land.\n<{POSTED}|#7>", *tickets),
    ]
    assert [(event["channel"], event["card"]) for event in said(demo)] == [(None, POSTED), ("C0ALERT1", POSTED), (None, None), ("C0ALERT1", POSTED)]


def test_a_post_with_no_thread_naming_a_person_goes_out_through_cc_slack_send_in_their_group_dm_with_him_and_the_bot(demo: str, posts: list[tuple[str, ...]]) -> None:
    channel = "U0REPORTER"
    note = {"text": "The run page shows its history again, since 6:40 PM.", "channel_id": channel}
    assert talk.say(demo, note | {"dry_run": True}) == f"DRY the group DM of {channel}, him and the bot, 0 pictures: {note['text']}"
    assert talk.say(demo, note | TICKET).startswith(f"POSTED {POSTED}")
    assert posts == [
        (
            "send", "--agent", "claude", "--channel", channel, "--text", note["text"],
            "--ticket", "t1", "--ticket-tree", "tree1", "--ticket-agent", "agent1", "--ticket-call", "call1", "--ticket-fingerprint", "print1",
        )
    ]
    assert said(demo)[0]["channel"] == channel


@pytest.mark.parametrize(
    ("post", "why"),
    [
        ({"job": "j2"}, "job j2 is done, so nothing would wait on the reply"),
        ({"job": "j9"}, "job j9 is no job of this drive, so nothing would wait on the reply"),
        ({"job": "j1", "channel_id": None}, "job names the job a question to a teammate is for, so it takes channel_id"),
    ],
)
def test_a_question_naming_a_job_that_cannot_wait_on_the_reply_posts_nothing(demo: str, posts: list[tuple[str, ...]], post: dict, why: str) -> None:
    with edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "reader", "ask Dana about the migration", [], state.stamp())
        data["jobs"]["j2"] = state.new_job("o1", "reader", "ask Dana about the migration", [], state.stamp()) | {"state": "done"}
    with pytest.raises(DriveError, match=f"^nothing posted: {re.escape(why)}"):
        talk.say(demo, {"text": "Can we run the migration tonight?", "channel_id": "U0REPORTER"} | post | TICKET)
    assert (posts, said(demo)) == ([], [])


def test_a_question_to_a_teammate_for_a_job_keeps_that_job_waiting_on_their_reply(demo: str, posts: list[tuple[str, ...]]) -> None:
    with edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "reader", "ask Dana about the migration", [], state.stamp())
    question = {"text": "Can we run the migration tonight?", "channel_id": "U0REPORTER", "job": "j1"}
    assert talk.say(demo, question | TICKET).startswith(f"POSTED {POSTED}; j1 waits on the reply to it")
    job = state.load(demo)["jobs"]["j1"]
    assert (job["waits"], job["state"], job["said"]) == ([f"reply:{POSTED}"], "waiting", state.NOT_STARTED)
    assert posts[0][:5] == ("send", "--agent", "claude", "--channel", "U0REPORTER")


def test_a_report_closing_its_order_in_another_orders_thread_his_message_started_records_there_and_joins_it(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    his, own = "1794200000.000100", "1794100000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the incident badge counts only live incidents", "--thread", own)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "how is the dashboard looking", "--from", f"slack:D123/{his}", "--thread", his)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the zoomed-out view", "--thread", "1794150000.000100")
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    slack_thread.messages.append({"ts": his, "thread_ts": his, "from_user": True, "text": "how is the dashboard looking"})
    report(monkeypatch, {"close": "o1"})
    answer = slack_thread.post(state.now() + timedelta(minutes=1), from_claude=True, text="The incident badge counts only live incidents: 3 right now.")
    assert run("order", "posted", "o1", answer, "--record", "677110") == (
        0, f'o1 closing line, no part delivered: {answer}, in thread {his}, now one of its threads'
    )
    data = state.load(demo)
    assert [where[:2] for where in data["orders"]["o1"]["threads"]] == [["D123", his]] and state.thread_order(data, his) == "o1"


def test_a_report_about_its_order_goes_out_through_say_in_a_thread_his_message_started_and_joins_it(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    his, alert = "1794200000.000100", "1794100000.000100"
    run("order", "add", "--title", "Short title", "--project", "SandSQL", "--workstream", "Alerts", "--from", f"slack:C0822AHFY3G/{alert}", "unknown column case_v2.audit_status", "--thread", alert)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the run page", "--thread", "1794150000.000100")
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
        data["orders"]["o1"]["thread"] = ["C0822AHFY3G", alert]
    slack_thread.messages.append({"ts": his, "thread_ts": his, "from_user": True, "text": "is the audit_status fix out yet?"})
    report(monkeypatch, {"progress": "true", "order": "o1", "thread": his})
    answer = slack_thread.post(state.now() + timedelta(minutes=1), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": answer, "thread_ts": his})
    assert talk.say(demo, {"text": "The fix is building.", "thread_ts": his, "record": "677158"}) == (
        f"POSTED {answer}; o1 progress line, no part delivered: {answer}, in thread {his}, now one of its threads"
    )
    assert [where[:2] for where in state.load(demo)["orders"]["o1"]["threads"]] == [["D123", his]]


def test_a_report_say_posts_at_the_root_of_another_channel_joins_its_thread_as_members_whose_replies_keep_no_line_to_him(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    own, claim = "1794100000.000100", "1794687785.402399"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "test the batch writer on staging", "--thread", own)
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    until = ended(30)
    report(monkeypatch, {"progress": "true", "order": "o1", "until": until})
    root = f"https://acme.slack.com/archives/C0STAGING1/p{claim.replace('.', '')}"
    reply = f"https://acme.slack.com/archives/C0STAGING1/p1794689000000100?thread_ts={claim}&cid=C0STAGING1"
    away = "so it is no line to him"
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": root})
    text = f"Claiming staging until {render.clock(state.parse_time(until))} to test the batch writer."
    assert talk.say(demo, {"text": text, "channel_id": "C0STAGING1", "record": "677158"} | TICKET) == (
        f"POSTED {root}; o1 progress line, no part delivered: {root}, at the root of another channel, {away}, in thread {claim}, now one of its threads,"
        f" and its thread owes its closing line from {render.when(until)}"
    )
    report(monkeypatch, {"progress": "true", "order": "o1"})
    before = state.load(demo)
    assert [[where[0], where[1], where[3]] for where in before["orders"]["o1"]["threads"]] == [["C0STAGING1", claim, "members"]]
    assert not talk.to_him(before, "C0STAGING1", claim) and talk.to_him(before, "C0STAGING1", "1794600000.000100") and talk.to_him(before, "D123", own)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": reply, "thread_ts": claim})
    assert talk.say(demo, {"text": "Staging is free again: the batch writer test passed.", "channel_id": "C0STAGING1", "thread_ts": claim, "record": "677159"} | TICKET) == (
        f"POSTED {reply}; o1 progress line, no part delivered: {reply}, in a thread of another channel its members read, {away}"
    )
    after = state.load(demo)["orders"]["o1"]
    assert [after[field] for field in ("last_line", "threads")] == [before["orders"]["o1"][field] for field in ("last_line", "threads")]


def test_a_line_at_his_dm_root_on_an_order_whose_own_thread_holds_no_line_to_him_is_the_thread_its_next_lines_go_in(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    alert, root = "1794100000.000100", "1794200100.000500"
    run("order", "add", "--title", "Short title", "--project", "SandSQL", "--workstream", "Alerts", "--from", f"slack:C0822AHFY3G/{alert}", "unknown column case_v2.audit_status", "--thread", alert)
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
        data["orders"]["o1"]["thread"] = ["C0822AHFY3G", alert]
    link = f"https://acme.slack.com/archives/D123/p{root.replace('.', '')}"
    slack_thread.messages.append({"ts": root, "from_claude": True, "text": "The fix is building."})
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": link})
    report(monkeypatch, {"progress": "true", "order": "o1"})
    assert talk.say(demo, {"text": "The fix is building.", "record": "677158"}).startswith(f"POSTED {link}; o1 progress line")
    assert state.thread_attr(state.load(demo), state.load(demo)["orders"]["o1"]) == {"thread": root}


def test_a_claim_at_a_channels_root_keeps_its_end_on_its_order_and_a_line_in_its_thread_moves_it(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    root, moved = "1794200100.000500", "1794203700.000600"
    until, later = ended(30), ended(90)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "test the batch writer on staging")
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    claim = f"Claiming staging until {render.clock(state.parse_time(until))} to test the batch writer, as the repository's rule for claiming staging asks. Does anyone object?"
    moving = f"Still testing; staging stays claimed until {render.clock(state.parse_time(later))}."
    link = f"https://acme.slack.com/archives/C0STAGING1/p{root.replace('.', '')}"
    slack_thread.messages.append({"ts": root, "from_claude": True, "text": claim})
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": link})
    report(monkeypatch, {"progress": "true", "order": "o1", "until": until})
    with pytest.raises(DriveError, match="nothing posted: report 677158's until attr is the end of a post at the root of a channel outside his DM"):
        talk.say(demo, {"text": claim, "record": "677158"})
    unowed = "so it is no line to him"
    assert talk.say(demo, {"text": claim, "channel_id": "C0STAGING1", "record": "677158"}) == (
        f"POSTED {link}; o1 progress line, no part delivered: {link}, at the root of another channel, {unowed}, in thread {root}, now one of its threads, and its thread owes its closing line from {render.when(until)}"
    )
    data = state.load(demo)
    assert [where[::3] for where in data["orders"]["o1"]["threads"]] == [["C0STAGING1", "members"]]
    assert data["orders"]["o1"]["claims"] == {root: {"channel": "C0STAGING1", "link": link, "until": until, "since": root, "said": None, "replies_read": None}}
    line = f"https://acme.slack.com/archives/C0STAGING1/p{moved.replace('.', '')}?thread_ts={root}"
    slack_thread.messages.append({"ts": moved, "thread_ts": root, "from_claude": True, "text": moving})
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": line, "thread_ts": root})
    report(monkeypatch, {"progress": "true", "order": "o1", "until": later})
    assert talk.say(demo, {"text": moving, "channel_id": "C0STAGING1", "thread_ts": root, "record": "677159"}) == (
        f"POSTED {line}; o1 progress line, no part delivered: {line}, in a thread of another channel its members read, {unowed}, and its thread owes its closing line from {render.when(later)}"
    )
    assert state.load(demo)["orders"]["o1"]["claims"] == {root: {"channel": "C0STAGING1", "link": link, "until": later, "since": moved, "said": None, "replies_read": None}}


def test_a_claim_at_a_channels_root_naming_no_end_or_a_past_one_posts_nothing_and_goes_back_to_its_writer_and_one_whose_words_miss_its_end_posts_nothing(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "test the release on staging")
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    sent: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, posts: sent.append((lane, posts)) or [str(50 + len(sent))])
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: pytest.fail("a refused claim reached Slack"))
    stale, later = ended(-5), ended(30)
    unended = "Testing the release on staging, as the repository's rule for claiming staging asks. Does anyone object?"
    back = "refused record 52 sent it back to root@demo, which wrote report 677158: post nothing and return NOTHING sent back to root@demo"
    with pytest.raises(DriveError, match=re.escape("nothing posted: this post names no until attr") + ".*a claim goes out only on a report naming its end"):
        talk.say(demo, {"text": unended, "channel_id": "C0STAGING1"})
    assert sent == []
    report(monkeypatch, {"progress": "true", "order": "o1"})
    with pytest.raises(DriveError, match=re.escape("nothing posted: report 677158 names no until attr, the end of what a post at the root of a channel outside his DM")):
        talk.say(demo, {"text": unended, "channel_id": "C0STAGING1", "record": "677158"})
    report(monkeypatch, {"progress": "true", "order": "o1", "until": stale})
    with pytest.raises(DriveError, match=re.escape(f"nothing posted: report 677158's until attr ends this claim at {render.when(stale)}, which passed before it went out at")) as refused:
        talk.say(demo, {"text": f"Claiming staging until {render.clock(state.parse_time(stale))} to test the release.", "channel_id": "C0STAGING1", "record": "677158"})
    assert str(refused.value).endswith(back)
    assert [[(lane, {key: record[key] for key in ("kind", "re", "to")}) for record in posts] for lane, posts in sent] == [
        [("communicator", {"kind": "refused", "re": ["677158"], "to": ["root@demo"]})]
    ] * 2
    assert f"ends this claim at {render.when(stale)}, which passed" in sent[1][1][0]["text"] + sent[1][1][0].get("body", "")
    report(monkeypatch, {"progress": "true", "order": "o1", "until": later})
    with pytest.raises(DriveError, match=re.escape(f'its words name neither that time nor how long it holds: say "until {render.clock(state.parse_time(later))}" or "for ')):
        talk.say(demo, {"text": unended, "channel_id": "C0STAGING1", "record": "677158"})
    assert len(sent) == 2 and state.load(demo)["orders"]["o1"]["claims"] == {}


@pytest.mark.parametrize(
    ("words", "named"),
    [("for 30", True), ("for 28 mins", True), ("for about half an hour", True), ("until {end}", True), ("for an hour", False), ("for 2 hours", False), ("for 3 PRs", False)],
)
def test_a_claims_words_name_its_end_as_its_clock_time_or_as_how_long_it_holds_within_five_minutes(demo: str, words: str, named: bool) -> None:
    until = ended(30)
    text = f"Testing the release on staging {words.format(end=render.clock(state.parse_time(until)))}, as the repository's rule for claiming staging asks; lmk if anyone objects"
    refused = talk.unended(state.load(demo), {"text": text, "channel_id": "C0STAGING1"}, {"seq": "677158", "writer": "root", "until": until})
    assert (refused is None) is named


def test_an_incident_added_from_the_terminal_takes_its_first_line_at_his_dm_root_as_its_own_thread_so_its_next_line_replies_under_it(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    root = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "SandSQL", "--workstream", "Alerts", "memory recall returns nothing")
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
        data["orders"]["o1"]["incident"] = True
    first = f"https://acme.slack.com/archives/D123/p{root.replace('.', '')}"
    slack_thread.messages.append({"ts": root, "from_claude": True, "text": "Memory recall returns nothing; the fix is building."})
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": first})
    report(monkeypatch, {"progress": "true", "order": "o1"})
    assert talk.say(demo, {"text": "Memory recall returns nothing; the fix is building.", "record": "677158"}) == (
        f"POSTED {first}; o1 progress line, no part delivered: {first}, in thread {root}, now its own thread"
    )
    assert state.thread_attr(state.load(demo), state.load(demo)["orders"]["o1"]) == {"thread": root}
    second = slack_thread.post(state.now() + timedelta(minutes=1), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": second, "thread_ts": root})
    report(monkeypatch, {"progress": "true", "order": "o1", "thread": root})
    assert talk.say(demo, {"text": "The fix is live.", "thread_ts": root, "record": "677159"}) == f"POSTED {second}; o1 progress line, no part delivered: {second}"
    order = state.load(demo)["orders"]["o1"]
    assert (order["thread"], order["threads"]) == (["D123", root], [])


def test_his_later_words_in_a_thread_go_to_the_order_that_came_into_it_last(demo: str, run: Callable[..., tuple[int, str]]) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the 50 dashboard improvements")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "how many incidents are open", "--thread", thread)
    with edit(demo) as data:
        created = state.parse_time(data["orders"]["o2"]["created"])
        del data["orders"]["o1"]["threads"]
        data["orders"]["o1"]["asked_in"] = [["D123", thread]]
        data["orders"]["o1"]["last_line"] = state.stamp(created + timedelta(minutes=3))
    assert state.load(demo)["orders"]["o1"]["threads"] == [["D123", thread, state.stamp(created + timedelta(minutes=3)), "posted"]]
    assert state.thread_order(state.load(demo), thread) == "o1"
    with edit(demo) as data:
        data["orders"]["o2"]["created"] = state.stamp(created + timedelta(minutes=5))
    assert state.thread_order(state.load(demo), thread) == "o2"
    assert state.thread_order(state.load(demo), "1794100000.000100") is None


def test_a_pr_card_drawn_at_his_dm_root_is_a_thread_of_the_order_its_pr_serves_so_a_line_under_it_records_there_and_his_words_there_go_to_that_order(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    question, card = "1794520890.022349", "1794526059.841239"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "give the sprite what it needs", "--owner", "coordinator")
    building(demo)
    with edit(demo) as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(None, QUESTION, state.stamp()) | {"waits": WAITS})
    cli.follow_card(demo, WAITS, set(), f"https://acme.slack.com/archives/D123/p{question.replace('.', '')}")
    sent = slack.cc_slack
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: sent(*argv) | {"card_permalink": f"https://acme.slack.com/archives/D123/p{card.replace('.', '')}"})
    talk.say(demo, {"text": "land these", "tap_prs": [PR_URL]})
    assert posts == [("dm-status", "--agent", "claude", "--drive", "demo", "--as-holder", "--tap-pr", PR_URL, "--", "land these")]
    report(monkeypatch, {"progress": "true", "order": "o1"})
    line = f"https://acme.slack.com/archives/D123/p1794532866928349?thread_ts={card}"
    slack_thread.messages.append({"ts": "1794532866.928349", "thread_ts": card, "from_claude": True})
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": line, "thread_ts": card})
    assert talk.say(demo, {"text": "It merged.", "thread_ts": card, "record": "681990"}) == f"POSTED {line}; o1 progress line, no part delivered: {line}"
    data = state.load(demo)
    assert slack.order_threads(data, "o1", data["orders"]["o1"]) == [("D123", question), ("D123", card)]
    assert state.thread_order(data, card) == "o1"


def test_say_keeps_a_post_in_a_thread_as_the_line_that_thread_holds_about_each_card_of_his_it_links(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]]
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL mint endpoint failing")
    building(demo)
    with edit(demo) as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(None, QUESTION, state.stamp(), card=CARD_URL) | {"waits": WAITS})
    talk.posted(demo, {"text": f"{{card:{WAITS}|Your card on the lock}} blocks the fix; it builds on one lock per chat meanwhile.", "thread_ts": thread})
    line = view.told_line(state.load(demo), thread, WAITS)
    assert (line["link"], CARD_URL in line["words"]) == (POSTED, True)
    talk.posted(demo, {"text": "The fix is live.", "thread_ts": thread})
    assert view.told_line(state.load(demo), thread, CARD_URL) == line


def test_the_watchs_paused_reply_under_his_question_card_goes_in_the_cards_thread_in_his_dm_on_a_drive_started_with_no_dm_channel(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], his_dm: str
) -> None:
    question = "1794579637.319519"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "one communicator for every drive", "--owner", "coordinator")
    building(demo)
    with edit(demo) as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(None, QUESTION, state.stamp()) | {"waits": WAITS})
    cli.follow_card(demo, WAITS, set(), f"https://acme.slack.com/archives/{his_dm}/p{question.replace('.', '')}")
    data = state.load(demo)
    assert ("channel" not in data["slack"], data["orders"]["o1"]["threads"][0][:2]) == (True, [his_dm, question])
    assert talk.posted(demo, {"text": watch.PAUSED, "thread_ts": question})["permalink"] == POSTED
    assert posts == [("dm-status", "--agent", "claude", "--drive", demo, "--as-holder", "--thread", question, "--", watch.PAUSED)]


def test_every_line_naming_a_carded_pr_links_its_card_whether_or_not_it_asks_his_tap() -> None:
    pr = {"url": "https://github.com/yasyf/cc-skills/pull/7", "head": "abc1234", "title": "Fix: the run page shows its history"}
    card = {"url": "https://acme.slack.com/archives/D1/p1794300000000200", "prs": [pr["url"]], "card": {"prs": [{"url": pr["url"], "head": "abc1234"}]}}
    prs = {"yasyf/cc-skills#7": pr}
    found = {"{pr:7}": refs.pr(prs, "7", "", [card])}
    assert refs.slack("Tap Approve on {pr:7}\nAlso merged: {pr:7}", found, None) == f"Tap Approve on <{card['url']}|#7>\nAlso merged: <{card['url']}|#7>"
    assert refs.pr(prs, "7", "", [card | {"card": {"prs": [{"url": pr["url"], "head": "old0000"}]}}])["card"] is None


def test_while_the_drive_keeps_channels_an_orders_line_naming_no_place_starts_its_thread_at_its_seats_channel_root_and_a_follow_up_replies_there(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    his, root = "1794200000.000100", "1794200100.000200"
    run("order", "add", "--title", "Short title", "--project", "SandSQL", "--workstream", "Alerts", "memory recall returns nothing", "--thread", his)
    run("start", "demo", "--role", "communicator")
    with edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
        data["slack"]["channels"] = {"": {"id": "C100", "name": "him-drive-demo"}, "SandSQL": {"id": "C200", "name": "him-drive-demo-sandsql"}}
    assert state.load(demo)["orders"]["o1"]["thread"] == ["D123", his]
    first = f"https://acme.slack.com/archives/C200/p{root.replace('.', '')}"
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: calls.append(argv) or {"permalink": first})
    report(monkeypatch, {"progress": "true", "order": "o1"})
    assert talk.say(demo, {"text": "The fix is building.", "record": "677158"}) == (
        f"POSTED {first}; o1 progress line, no part delivered: {first}, in thread {root}, now one of its threads"
    )
    assert calls[-1][calls[-1].index("--channel") + 1] == "C200" and "--thread" not in calls[-1]
    order = state.load(demo)["orders"]["o1"]
    assert (order["thread"], [where[:2] for where in order["threads"]]) == (["D123", his], [["C200", root]])
    assert state.thread_attr(state.load(demo), order) == {"thread": root, "channel": "C200"}
    second = f"https://acme.slack.com/archives/C200/p1794200200000300?thread_ts={root}&cid=C200"
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: calls.append(argv) or {"permalink": second, "thread_ts": root})
    report(monkeypatch, {"progress": "true", "order": "o1", "thread": root, "channel": "C200"})
    assert talk.say(demo, {"text": "The fix is live.", "thread_ts": root, "record": "677159"}).startswith(f"POSTED {second}; o1 progress line")
    assert (calls[-1][calls[-1].index("--channel") + 1], calls[-1][calls[-1].index("--thread") + 1]) == ("C200", root)


def test_a_post_naming_a_place_goes_there_and_one_naming_none_goes_in_his_thread_when_it_answers_him_else_to_its_own_channel(demo: str) -> None:
    his = "1794200000.000100"
    with edit(demo) as data:
        data["slack"]["channels"] = {"": {"id": "C100", "name": "him-drive-demo"}}
    data = state.load(demo)
    assert talk.homed(demo, data, {"text": "x", "thread_ts": his, "asked": [his]}, [], None, ("D123", his)) == ("D123", his)
    assert talk.homed(demo, data, {"text": "x", "thread_ts": his}, [], None, ("D123", his)) == ("D123", his)
    assert talk.homed(demo, data, {"text": "x", "asked": [his]}, [], None, ("D123", None)) == ("D123", his)
    reply = "https://acme.slack.com/archives/D123/p1794200000000300?thread_ts=1794200000.000200&cid=D123"
    assert talk.homed(demo, data, {"text": "x", "asked": reply}, [], None, ("D123", None)) == ("D123", "1794200000.000200")
    assert talk.homed(demo, data, {"text": "x"}, [], None, ("D123", None)) == ("C100", None)
    assert talk.homed(demo, data, {"edit": "https://acme.slack.com/archives/D123/p1794200000000100"}, [], None, ("D123", his)) == ("D123", his)
    with edit(demo) as data:
        data["slack"]["channels"] = {}
    assert talk.homed(demo, state.load(demo), {"text": "x"}, [], None, ("D123", None)) == ("D123", None)


def test_a_picture_already_shown_in_a_thread_or_its_root_is_dropped_from_a_post_there(demo: str, tmp_path: Path) -> None:
    chart, other = tmp_path / "chart.png", tmp_path / "other.png"
    chart.write_bytes(b"stages")
    other.write_bytes(b"other")
    copy = tmp_path / "copy.png"
    copy.write_bytes(b"stages")
    assert talk.unshown(demo, ("C1", None), [str(chart)]) == ([str(chart)], [])
    talk.shown_pictures(demo, ("C1", "1.2"), [str(chart)])
    assert talk.unshown(demo, ("C1", "1.2"), [str(copy), str(other)]) == ([str(other)], ["copy.png"])
    assert talk.unshown(demo, ("C1", "9.9"), [str(copy)]) == ([str(copy)], [])


def test_a_release_reference_links_the_post_its_release_provider_names_and_one_it_names_none_for_warns(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    announced = "https://acme.slack.com/archives/C0REL/p1794826033725399"
    monkeypatch.setattr(providers.ReleaseProvider, "post", lambda self, key: announced if key in ("wavy-mckay", "17672") else None)
    found = refs.resolved(demo, "{release:wavy-mckay} is live, as is {release:17672|the infra release}.")
    assert found["{release:wavy-mckay}"] == {"kind": "release", "title": "wavy-mckay", "url": announced, "board": announced}
    assert found["{release:17672|the infra release}"]["url"] == announced
    assert "unlinked" in refs.resolved(demo, "{release:nope} is live.")["{release:nope}"]["warning"]


def test_a_start_time_drawn_right_after_a_typed_clock_time_is_dropped_so_the_post_gives_one_time() -> None:
    found = {"{started:j1}": {"kind": "started", "title": f"3:20 AM {refs.JOB_START}", "url": None, "board": None}}
    assert refs.slack("The Mac testing started at 3:23 AM ({started:j1})", found, None) == "The Mac testing started at 3:23 AM"
    assert refs.slack("Started at 3:23 AM {started:j1}; done by 4:00 AM", found, None) == "Started at 3:23 AM; done by 4:00 AM"


def test_post_deduplication_and_watch_delivery_clocks_own_separate_files(demo: str) -> None:
    moment = datetime(2026, 11, 16, 20, tzinfo=UTC)
    post = {"channel_id": "D123", "thread_ts": "1794851267.832189", "text": "The update is installed"}
    line = "JOB j1 o1 DONE installed"
    talk.remember_said(demo, post, CARD_URL)
    watch.keep_said(demo, "root", [line], moment)
    watch.keep_posted(demo, "root", moment)
    assert talk.said_before(demo, post) == CARD_URL
    assert watch.unsaid(demo, "root", [line], moment) == []
    talk.remember_said(demo, post | {"text": "The next update is installed"}, PR_URL)
    assert watch.unsaid(demo, "root", [line], moment + watch.SAID_FOR) == [line]
    assert talk.said_before(demo, post) == CARD_URL
