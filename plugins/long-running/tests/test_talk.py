import json
import os
import re
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import Checked, Thread
from drive import (
    cli,
    dashboard,
    picture,
    reads,
    slack,
    state,
    talk,
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
OPEN_CARDS = f"\n\n*Your open cards: 1 question*\n<{CARD_URL}|{QUESTION}>"


@pytest.fixture
def posts(demo: str, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    sent: list[tuple[str, ...]] = []
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "https://acme.slack.com/", "questions": [question_card(CARD_URL, QUESTION, WAITS)], "pr_cards": []})
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: sent.append(argv) or {"permalink": POSTED, "thread_ts": "1794300000.000900"})
    with state.edit(demo) as data:
        data["prs"]["yasyf/cc-skills#7"] = state.new_pr(PR_URL, "j1", "o1") | {"title": "New: \U0001f680 the run page shows its history"}
    return sent


def question_card(url: str, question: str, waits: str) -> dict:
    card = {"title": question, "body": "Each chat's database or the whole table.", "options": ["Each chat"], "recommended": "Each chat", "waits": waits}
    return {"question": question, "options": card["options"], "recommended": card["recommended"], "asked": ASKED, "url": url, "card": card}


def said(name: str) -> list[dict]:
    return [event for event, _ in state.read_events(name) if event["kind"] == "said"]


def test_one_post_links_each_reference_the_way_slack_and_the_dashboard_each_show_it(demo: str, posts: list[tuple[str, ...]]) -> None:
    assert talk.say(demo, {"text": "{pr:7} waits on {card:demo:j1-7}", "thread_ts": "1794200000.000100"}) == f"POSTED {POSTED}"
    assert posts == [
        ("dm-status", "--agent", "claude", "--thread", "1794200000.000100", "--", f"<{PR_URL}|the run page shows its history> waits on <{CARD_URL}|{QUESTION}>")
    ]
    [kept] = said(demo)
    assert kept["text"] == f"[the run page shows its history]({PR_URL}) waits on [{QUESTION}](#/queue/question/1794300000.000100)"
    assert (kept["url"], kept["thread"]) == (POSTED, "1794300000.000900")


def test_an_edit_links_each_reference_as_a_post_does_and_the_dashboard_shows_its_new_words_in_place(demo: str, posts: list[tuple[str, ...]]) -> None:
    talk.say(demo, {"text": "{pr:7} is up", "thread_ts": "1794200000.000100"})
    edit = {"edit": POSTED, "text": "{pr:7} waits on {card:demo:j1-7}"}
    sent = f"<{PR_URL}|the run page shows its history> waits on <{CARD_URL}|{QUESTION}>"
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
    with pytest.raises(DriveError, match=r"\{pr:8\} names no PR"):
        talk.say(demo, {"text": "see {pr:8}"})
    with state.edit(demo) as data:
        data["prs"]["Forge-AI/monorepo#7"] = state.new_pr("https://github.com/Forge-AI/monorepo/pull/7", "j1", "o1")
    with pytest.raises(DriveError, match=r"could be yasyf/cc-skills#7 or Forge-AI/monorepo#7"):
        talk.say(demo, {"text": "{pr:7}"})
    assert (posts, said(demo)) == ([], [])
    talk.say(demo, {"text": "{pr:Forge-AI/monorepo#7}"})
    assert posts[0][-1] == "<https://github.com/Forge-AI/monorepo/pull/7|Forge-AI/monorepo#7>"


def test_a_card_he_answered_or_the_drive_withdrew_still_links_by_its_waits_id(demo: str, posts: list[tuple[str, ...]]) -> None:
    settled = "https://acme.slack.com/archives/D123/p1794300000000500"
    with state.edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "design the store", [], state.stamp())
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "Keep the old store?", state.stamp(), card=settled) | {"waits": "demo:j1-6"})
    talk.say(demo, {"text": "I withdrew {card:demo:j1-6}: the redesign settles it."})
    assert posts[0][-1] == f"I withdrew <{settled}|Keep the old store?>: the redesign settles it."
    assert said(demo)[0]["text"] == "I withdrew [Keep the old store?](#/feature/o1): the redesign settles it."
    with pytest.raises(DriveError, match=r"\{card:demo:j1-5\} names no card the drive drew"):
        talk.say(demo, {"text": "see {card:demo:j1-5}"})


def test_an_order_links_its_thread_in_slack_or_else_its_dashboard_page_under_his_words(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "a verb for owners", "--thread", "1794200000.000100")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: one owner per order")
    view.refresh_cards(demo)
    talk.say(demo, {"text": "{order:o1} is live; {order:o2} is next."})
    assert posts[0][-1] == (
        "<https://acme.slack.com/archives/D123/p1794200000000100|a verb for owners> is live;"
        f" <{dashboard.link(demo)}#/feature/o2|Drive tooling: one owner per order> is next."
    )
    assert said(demo)[0]["text"] == "[a verb for owners](#/feature/o1) is live; [Drive tooling: one owner per order](#/feature/o2) is next."
    with pytest.raises(DriveError, match=r"\{order:o9\} names no order"):
        talk.say(demo, {"text": "{order:o9} is next."})


def test_a_record_links_what_it_links_under_its_first_clause_or_the_words_after_a_bar(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    notebook = "https://app.datadoghq.com/notebook/1"
    timeouts = (
        "The 37 API failures at 4:14 AM were registry timeouts while the primary switched builds, per #663477 and"
        " the replica logs. Retries drained them by 4:16 AM."
    )
    shown = {
        "661194": {"text": "sandsql-server ran out of memory at its 32 GiB limit, 3 times since 9:40 PM", "refs": {"url": notebook}},
        "661195": {"text": "the write lock is held across the whole call", "refs": {}},
        "661196": {"text": timeouts, "refs": {"url": notebook}},
    }
    asked: list[list[str]] = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout: asked.append(argv) or json.dumps(shown[argv[2]]))
    talk.say(demo, {"text": "The pod restarts because {record:661194}. Stalls: {record:661195}. Failures: {record:661196}, {record:661196|the timeouts}."})
    assert asked == [["cci", "show", seq, "--json", "--drive", demo] for seq in ("661194", "661195", "661196", "661196")]
    assert posts[0][-1] == (
        f"The pod restarts because <{notebook}|sandsql-server ran out of memory at its 32 GiB limit>. Stalls: the write lock is"
        f" held across the whole call. Failures: <{notebook}|The 37 API failures at 4:14 AM were registry timeouts…>,"
        f" <{notebook}|the timeouts>."
    )
    assert said(demo)[0]["text"].startswith(f"The pod restarts because [sandsql-server ran out of memory at its 32 GiB limit]({notebook}).")


def test_a_card_asked_again_with_new_words_moves_its_question_and_each_wait_on_it_to_the_new_card(demo: str, posts: list[tuple[str, ...]]) -> None:
    other = "https://acme.slack.com/archives/D123/p1794300000000400"
    with state.edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "design the store", [], state.stamp()) | {"waits": [CARD_URL, other]}
        data["jobs"]["j1"]["questions"].append(state.new_question(None, QUESTION, state.stamp(), card=CARD_URL) | {"waits": WAITS})
        data["jobs"]["j2"] = state.new_job("o1", "builder", "ship the store", [], state.stamp()) | {"waits": [CARD_URL]}
        data["prs"]["yasyf/cc-skills#7"]["waits"] = [other, CARD_URL]
    talk.say(demo, {"card": {"title": QUESTION, "waits": WAITS}})
    data = state.load(demo)
    jobs, prs = data["jobs"], data["prs"]
    assert jobs["j1"]["questions"][0]["card"] == POSTED
    assert (jobs["j1"]["waits"], jobs["j2"]["waits"], prs["yasyf/cc-skills#7"]["waits"]) == ([POSTED, other], [POSTED], [other, POSTED])
    talk.say(demo, {"card": {"title": QUESTION, "waits": WAITS}})
    assert (state.load(demo)["jobs"], state.load(demo)["prs"]) == (jobs, prs)


def test_a_question_card_returns_and_records_its_own_permalink(demo: str, posts: list[tuple[str, ...]]) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "design the store", [], state.stamp())
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "Keep the old store?", state.stamp()) | {"waits": "demo:j1-8"})
    assert talk.say(demo, {"card": {"title": "Keep the old store?", "waits": "demo:j1-8"}, "thread_ts": "1794200000.000100"}) == f"POSTED {POSTED} CARD {POSTED}"
    assert state.load(demo)["jobs"]["j1"]["questions"][0]["card"] == POSTED
    assert said(demo)[0]["card"] == POSTED


def test_a_card_no_question_keeps_moves_each_wait_on_it_to_the_new_card_when_asked_again_with_new_words(demo: str, posts: list[tuple[str, ...]]) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j2"] = state.new_job("o1", "builder", "ship the store", [], state.stamp()) | {"waits": [CARD_URL]}
        data["prs"]["yasyf/cc-skills#7"]["waits"] = [CARD_URL]
    talk.say(demo, {"card": {"title": QUESTION, "waits": WAITS}})
    data = state.load(demo)
    assert (data["jobs"]["j2"]["waits"], data["prs"]["yasyf/cc-skills#7"]["waits"]) == ([POSTED], [POSTED])


def test_say_posts_the_words_its_hook_judged_and_checks_none_of_them_again(demo: str, posts: list[tuple[str, ...]], checked: Checked) -> None:
    checked.findings = ["1:5 drive-term 'fence': say what happens to the reader's work instead"]
    with state.edit(demo) as data:
        data["stand_ins"]["backup"] = "S3 replica"
    text = "The fence held, the backup copy is current, and the runs failed because the pool was full."
    assert talk.say(demo, {"text": text, "dry_run": True}) == f"DRY his DM root, 0 pictures: {text}"
    talk.say(demo, {"text": text})
    assert ([argv[-1] for argv in posts], checked.texts) == ([text], [])


def test_each_visual_goes_to_slack_as_a_picture_and_to_the_dashboard_as_its_source(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    drawn = tmp_path / "one chat.png"
    asked: list[tuple[str, str, str]] = []
    monkeypatch.setattr(picture, "cached", lambda folder, name, kind, source: asked.append((name, kind, source)) or drawn)
    talk.say(demo, {"text": "The lock", "visuals": [{"kind": "code", "language": "go", "source": "mu.Lock()", "caption": "one chat"}]})
    assert asked == [("one chat", "code:go", "mu.Lock()")]
    assert posts[0][-4:] == ("--file", str(drawn), "--", "The lock")
    assert said(demo)[0]["visuals"] == [{"kind": "code:go", "source": "mu.Lock()", "caption": "one chat", "picture": str(drawn)}]


def test_a_chart_goes_to_slack_as_its_fence_for_slack_to_draw_and_the_dashboard_draws_it_from_its_source(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "cached", lambda *drawn: pytest.fail(f"a chart was drawn as a picture: {drawn}"))
    encoding = {"y": {"field": "what", "type": "nominal"}, "x": {"field": "count", "type": "quantitative"}}
    spec = json.dumps({"data": {"values": [{"what": "Slack posts", "count": 384}, {"what": "distinct alerts", "count": 41}]}, "mark": "bar", "encoding": encoding})
    talk.say(demo, {"text": "Alerts, not posts.", "visuals": [{"kind": "chart", "source": spec, "caption": "in 24 hours"}]})
    assert posts[0][3:] == ("--", f"Alerts, not posts.\n\nin 24 hours\n```chart\n{spec}\n```")
    [kept] = said(demo)
    assert (kept["text"], kept["visuals"]) == ("Alerts, not posts.", [{"kind": "chart", "source": spec, "caption": "in 24 hours"}])
    monkeypatch.setattr(dashboard, "home_repo", lambda checkout: None)
    board = dashboard.Board(demo, [])
    board.rebuild()
    assert [entry["visuals"] for entry in json.loads(board.body)["history"] if entry["kind"] == "said"] == [kept["visuals"]]


def test_a_code_or_diagram_fence_in_the_text_goes_to_slack_as_its_picture_while_words_raw_data_and_a_chart_stay_text(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    asked: list[tuple[str, str, str]] = []
    monkeypatch.setattr(picture, "cached", lambda folder, name, kind, source: asked.append((name, kind, source)) or tmp_path / f"{name}.png")
    stays = 'The reply:\n```json\n{"locked": true}\n```\nThe steps:\n```\nlock, write, unlock\n```\nPer day:\n```chart\n{"mark": "bar"}\n```'
    text = f"The lock covers each chat:\n```go\nmu.Lock()\ndefer mu.Unlock()\n```\nWho takes it:\n```mermaid\nflowchart TD\n  chat --> lock\n```\n{stays}"
    talk.say(demo, {"text": text})
    assert asked == [("go code", "code:go", "mu.Lock()\ndefer mu.Unlock()\n"), ("Diagram", "mermaid", "flowchart TD\n  chat --> lock\n")]
    sent = f"The lock covers each chat:\n\nWho takes it:\n\n{stays}"
    assert posts[0][3:] == ("--file", str(tmp_path / "go code.png"), "--file", str(tmp_path / "Diagram.png"), "--", sent)
    [kept] = said(demo)
    assert (kept["text"], kept["visuals"]) == (text, [])
    assert talk.say(demo, {"text": text, "dry_run": True}) == f"DRY his DM root, 2 pictures: {sent}"


def building(demo: str) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "show a run's history", [state.whole_order(data["orders"]["o1"]["text"])], state.stamp())
        data["orders"]["o1"]["jobs"].append("j1")


def test_a_stack_card_names_each_pr_by_its_url_and_the_dashboard_keeps_the_stack(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Run page", "show a run's history")
    building(demo)
    with pytest.raises(DriveError, match=r"tap_prs names yasyf/cc-skills#8, which the drive holds no PR for"):
        talk.say(demo, {"text": "land these", "tap_prs": [PR_URL, "https://github.com/yasyf/cc-skills/pull/8"]})
    assert (posts, said(demo)) == ([], [])
    talk.say(demo, {"text": "land these", "tap_prs": ["yasyf/cc-skills#7"]})
    assert posts[0] == ("dm-status", "--agent", "claude", "--tap-pr", PR_URL, "--", "land these")
    assert said(demo)[0]["prs"] == ["yasyf/cc-skills#7"]


def test_a_pr_card_goes_in_the_thread_of_each_open_order_its_pr_carries_out_so_the_one_he_reads_carries_its_link(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Run page", "show a run's history", "--thread", "1794200000.000100")
    run("order", "add", "--project", "The drive", "--workstream", "Run page", "There's no approval button", "--thread", "1794300000.000100")
    run("order", "add", "--project", "The drive", "--workstream", "Run page", "why is the page slow?", "--thread", "1794400000.000100")
    run("order", "add", "--project", "The drive", "--workstream", "Run page", "one more check", "--from", "slack:C9TEAM/1794500000.000100")
    building(demo)
    with state.edit(demo) as data:
        for order_id in ("o2", "o3", "o4"):
            state.carry(data, "j1", order_id, list(data["orders"][order_id]["parts"]))
        data["orders"]["o3"]["state"] = "done"
    assert talk.say(demo, {"text": "land these", "tap_prs": [PR_URL], "dry_run": True}) == "DRY thread 1794200000.000100, thread 1794300000.000100, 0 pictures, PR card for 1 PRs: land these"
    talk.say(demo, {"text": "land these", "tap_prs": [PR_URL]})
    assert posts == [
        ("dm-status", "--agent", "claude", "--thread", thread, "--tap-pr", PR_URL, "--", "land these") for thread in ("1794200000.000100", "1794300000.000100")
    ]
    assert [event["prs"] for event in said(demo)] == [["yasyf/cc-skills#7"]] * 2


def test_a_pr_cards_line_that_fails_in_a_later_thread_says_the_card_went_out_so_none_is_posted_again(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Run page", "show a run's history", "--thread", "1794200000.000100")
    run("order", "add", "--project", "The drive", "--workstream", "Run page", "There's no approval button", "--thread", "1794300000.000100")
    building(demo)
    with state.edit(demo) as data:
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


def test_a_post_its_order_will_not_take_still_says_it_went_out_so_nobody_sends_it_twice(demo: str, posts: list[tuple[str, ...]]) -> None:
    line = talk.say(demo, {"text": "done", "order": "o1"})
    assert line.startswith(f"POSTED {POSTED}; not recorded on o1: only the communicator records a post")
    assert len(posts) == 1


def test_a_post_on_an_order_in_none_of_its_threads_posts_nothing_and_says_where_it_goes(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], slack_thread: Thread
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "why the copy stalls", "--thread", "1794200000.000100")
    with pytest.raises(DriveError) as refused:
        talk.say(demo, {"text": "The copy stalls on the lock.", "thread_ts": "1794200090.000700", "order": "o1"})
    assert str(refused.value) == (
        "nothing posted: refused, thread 1794200090.000700 is none of o1's threads, its own, its cards' or its PRs' cards':"
        " 1794200000.000100; post it in one of them, or post it with no order and record the part it delivers with drive order"
        " posted o1 <its permalink> --record <the report's seq>"
    )
    with pytest.raises(DriveError, match="nothing posted: refused, his DM root is none of o1's threads"):
        talk.say(demo, {"text": "The copy stalls on the lock.", "order": "o1"})
    assert (posts, said(demo)) == ([], [])
    assert talk.say(demo, {"text": "The copy stalls on the lock.", "thread_ts": "1794200000.000100", "order": "o1"}).startswith(f"POSTED {POSTED}")
    slack_thread.messages.append({"ts": "1794300000.000500", "from_claude": True, "text": f"<{PR_URL}|the run page shows its history>"})
    assert talk.say(demo, {"text": "It is green.", "thread_ts": "1794300000.000500", "order": "o1"}).startswith(f"POSTED {POSTED}")
    assert len(posts) == 2


def test_a_post_on_an_incident_goes_in_each_joined_alerts_thread_both_ways_and_one_elsewhere_names_whose_thread_it_is(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL errors on plat", "--thread", "1794200000.000100", "--incident", "--parts", "fix", "first check")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL error log count", "--from", "slack:D123/1794300000.000100", "--thread", "1794300000.000100", "--joins", "o1")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "why the copy stalls", "--thread", "1794400000.000100")
    assert talk.say(demo, {"text": "The fix is merged.", "thread_ts": "1794300000.000100", "order": "o1"}).startswith(f"POSTED {POSTED}")
    assert talk.say(demo, {"text": "The fix is merged.", "thread_ts": "1794200000.000100", "order": "o2"}).startswith(f"POSTED {POSTED}")
    with pytest.raises(DriveError) as refused:
        talk.say(demo, {"text": "The fix is merged.", "thread_ts": "1794400000.000100", "order": "o1", "dry_run": True})
    assert str(refused.value) == (
        "nothing posted: refused, thread 1794400000.000100 is o3's thread and none of o1's threads, its own, its cards' or its PRs' cards':"
        " 1794200000.000100, 1794300000.000100; post it in one of them, post it with order o3 when the line is about o3, or post it with no"
        " order and record the part it delivers with drive order posted o1 <its permalink> --record <the report's seq>"
    )
    assert len(posts) == 2


def report(monkeypatch: pytest.MonkeyPatch, attrs: dict[str, str], lane: str = "root") -> None:
    monkeypatch.setattr(cli.reads, "run", lambda argv, checkout: json.dumps({"seq": argv[2], "lane": lane, "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": attrs}}))


def test_a_post_delivers_the_part_its_report_names_and_a_progress_report_naming_only_a_job_delivers_none(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "why it fails, and the options", "--thread", thread, "--parts", "cause", "options as a card")
    run("start", "demo", "--role", "communicator")
    with state.edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
        data["jobs"]["j1"] = state.new_job("o1", "codex", "find the cause", ["cause"], state.stamp()) | {"state": "done", "said": "DONE the cause"}
        data["orders"]["o1"]["jobs"].append("j1")
    answer = slack_thread.post(state.now(), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": answer, "thread_ts": thread})
    report(monkeypatch, {"job": "j1", "thread": thread})
    progress = talk.say(demo, {"text": "Still reading the lock.", "thread_ts": thread, "order": "o1", "record": "661193"})
    assert progress == f"POSTED {answer}; o1 progress line, no part delivered: {answer}"
    assert [part["posted"] for part in state.load(demo)["orders"]["o1"]["parts"].values()] == [None, None]
    report(monkeypatch, {"job": "j1", "thread": thread, "part": "cause"})
    assert talk.say(demo, {"text": "The lock is held across the whole call.", "thread_ts": thread, "order": "o1", "record": "661194"}) == f'POSTED {answer}; o1 "cause": posted {answer}'
    parts = state.load(demo)["orders"]["o1"]["parts"]
    assert (parts["cause"]["posted"], parts["options as a card"]["posted"]) == (answer, None)
    later = state.now() + timedelta(minutes=30)
    gaps = watch.part_gaps(watch.Watcher(demo, offset=0, ticked_at=later), state.load(demo), later)
    assert gaps == {("o1", "cause"): None, ("o1", "options as a card"): "no job"}


def test_a_report_delivers_on_an_order_only_from_the_lane_of_its_holder_and_a_refusal_names_the_report_the_handler_passes(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--project", "The drive", "--workstream", "Questions", "which page shows a run's history?", "--thread", thread, "--owner", "communicator")
    run("order", "add", "--project", "The drive", "--workstream", "Run page", "show a run's history", "--thread", thread, "--owner", "coordinator")
    run("start", "demo", "--role", "communicator")
    with state.edit(demo) as data:
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
        talk.say(demo, {"text": "The run page shows it.", "thread_ts": thread, "order": "o1", "record": "664348"})
    assert (sent, said(demo)) == ([], [])
    report(monkeypatch, {"close": "o1"}, lane="communicator")
    assert talk.say(demo, {"text": "The run page shows it.", "thread_ts": thread, "order": "o1", "record": "664350"}) == f'POSTED {answer}; o1 "which page shows a run\'s history?": posted {answer}'
    report(monkeypatch, {"close": "o2"}, lane="communicator")
    with pytest.raises(DriveError, match=r"refused, the report is the communicator's and o2 is the coordinator's order, so it delivers nothing there: pass the coordinator's own report on o2"):
        talk.say(demo, {"text": "The run page is live.", "thread_ts": thread, "order": "o2", "record": "664351"})
    report(monkeypatch, {"close": "o2"})
    assert talk.say(demo, {"text": "The run page is live.", "thread_ts": thread, "order": "o2", "record": "664352"}) == f'POSTED {answer}; o2 "show a run\'s history": posted {answer}'
    assert len(sent) == 2


def test_a_catch_up_opens_with_its_title_from_the_time_he_was_last_heard_whatever_title_it_came_with(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    report(monkeypatch, {"catch_up": "true"})
    state.append_event(demo, "catch-up", dm="D123", since="9:00 AM", orders="o1")
    state.append_event(demo, "catch-up", dm="D123", since="5:20 PM", orders="o1")
    talk.say(demo, {"text": "The run page fix is live.", "record": "660299"})
    talk.say(demo, {"text": "*Catch-up since 4:00 PM*\nThe run page fix is live.", "record": "660299"})
    titled = "*Catch-up since 5:20 PM*\nThe run page fix is live."
    assert [argv[-1] for argv in posts] == [titled + OPEN_CARDS] * 2
    assert [kept["text"] for kept in said(demo)] == [f"{titled}\n\n*Your open cards: 1 question*\n[{QUESTION}](#/queue/question/1794300000.000100)"] * 2
    report(monkeypatch, {"orders": "o1"})
    talk.say(demo, {"text": "The run page fix is live.", "record": "660300"})
    assert posts[-1][-1] == "The run page fix is live."


def test_his_catch_up_carries_the_reports_held_for_it_and_lets_them_go(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(demo) as data:
        data["held"] = {seq: {"order": "o1", "at": state.stamp(), "told": told} for seq, told in (("700", "701"), ("702", "703"))}
    report(monkeypatch, {"orders": "o1"})
    assert not talk.say(demo, {"text": "The run page fix is live.", "record": "660300"}).endswith("no longer held: 700, 702")
    assert list(state.load(demo)["held"]) == ["700", "702"]
    report(monkeypatch, {"catch_up": "true"})
    state.append_event(demo, "catch-up", dm="D123", since="5:20 PM", orders="o1", held="700 702")
    assert talk.say(demo, {"text": "The run page fix is live.", "record": "660299"}).endswith("; it carries the reports held for it, no longer held: 700, 702")
    assert state.load(demo)["held"] == {}


def test_his_catch_up_closes_with_every_open_card_waiting_on_him_five_a_message_none_drawn_twice(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = [question_card(f"https://acme.slack.com/archives/D123/p179430000000020{n}", f"Question {n}?", f"demo:j{n}-1") for n in range(1, 8)]
    tap = {"prs": [PR_URL], "asked": ASKED, "url": "https://acme.slack.com/archives/D123/p1794300000000300", "card": {"title": "New: approve the run page history?", "waits": "merge:yasyf:cc-skills:7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "https://acme.slack.com/", "questions": questions, "pr_cards": [tap]})
    report(monkeypatch, {"catch_up": "true"})
    state.append_event(demo, "catch-up", dm="D123", since="5:20 PM", orders="o1")
    text = "*The run page*\nIts fix is live, and {card:demo:j3-1} picks its lock."
    drawn = [f"<{card['url']}|{card['question']}>" for card in [*questions[:2], *questions[3:]]] + [f"<{tap['url']}|{tap['card']['title']}>"]
    sent = (
        f"*Catch-up since 5:20 PM*\n*The run page*\nIts fix is live, and <{questions[2]['url']}|Question 3?> picks its lock."
        "\n\n*Your open cards: 6 questions, 1 PR card*\n" + "\n".join(drawn[:5]) + "\n\n" + "\n".join(drawn[5:])
    )
    assert talk.say(demo, {"text": text, "record": "660299", "dry_run": True}) == f"DRY his DM root, 0 pictures: {sent}"
    talk.say(demo, {"text": text, "record": "660299"})
    assert posts[-1][-1] == sent
    assert said(demo)[-1]["text"].endswith("\n[New: approve the run page history?](#/queue/tap/1794300000.000300)")


def test_his_catch_up_posts_nothing_while_cc_slack_cannot_list_the_cards_it_closes_with(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    def unlisted(name: str) -> dict:
        raise view.CardsError("cc-slack exited 1")

    monkeypatch.setattr(view, "cards", unlisted)
    report(monkeypatch, {"catch_up": "true"})
    state.append_event(demo, "catch-up", dm="D123", since="5:20 PM", orders="o1")
    with pytest.raises(DriveError, match="nothing posted: his catch-up draws every open card of his, which cc-slack could not list: cc-slack exited 1"):
        talk.say(demo, {"text": "The run page fix is live.", "record": "660299"})
    assert (posts, said(demo)) == ([], [])


def test_a_held_report_is_told_to_the_coordinator_session_that_wrote_it_else_the_one_hearing_its_order(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a verb for owners")
    with state.edit(demo) as data:
        data["orders"]["o1"]["project"] = "Iris"
        data["subcoordinators"]["Iris"] = {"session": "iris-1", "terminal": None, "cwd": "/work", "started": state.stamp(), "from": 0}
    told = []

    def cci(argv: list[str], checkout: Path) -> str:
        told.append(argv[argv.index("--to") + 1])
        return json.dumps({"seq": 800 + len(told)})

    monkeypatch.setattr(cli.reads, "run", cci)
    assert talk.hold(demo, "700", {"writer": "builder"}, "o1") == "801"
    assert talk.hold(demo, "700", {"writer": "root"}, "o1") == "801"
    assert talk.hold(demo, "702", {"writer": "root"}, "o1") == "802"
    assert talk.hold(demo, "704", {"writer": "builder"}, None) == "803"
    assert told == [f"iris@{demo}", f"root@{demo}", f"root@{demo}"]


def test_a_post_goes_out_in_exactly_the_words_it_was_given_while_built_work_waits_on_his_card(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    view.refresh_cards(demo)
    thread = "1794200000.000100"
    with state.edit(demo) as data:
        data["orders"]["o1"] = {
            "text": "lock it",
            "from": "terminal",
            "thread": thread,
            "state": "working",
            "due": None,
            "jobs": ["j1"],
            "created": ASKED,
            "updated": ASKED,
            "parts": {"lock it": state.new_part()},
        }
        data["jobs"]["j1"] = state.new_job("o1", "builder", "lock it", ["lock it"], ASKED) | {"state": "built", "branch": "lane/lock", "waits": [WAITS]}
    talk.say(demo, {"text": "Shipped.", "thread_ts": thread})
    talk.say(demo, {"text": "Catch-up"})
    report(monkeypatch, {"catch_up": "true"})
    state.append_event(demo, "catch-up", dm="D123", since="5:20 PM", orders="o1")
    talk.say(demo, {"text": "The fix is live.", "record": "660299"})
    assert [argv[-1] for argv in posts] == ["Shipped.", "Catch-up", "*Catch-up since 5:20 PM*\nThe fix is live." + OPEN_CARDS]


def test_a_pr_is_linked_under_the_words_the_post_gives_it_whether_the_drive_holds_it_or_not(demo: str, posts: list[tuple[str, ...]]) -> None:
    talk.say(demo, {"text": "{pr:7|the run page fix} and {pr:yasyf/cc-skills#4250|the card that draws charts} are live."})
    assert posts[0][-1] == f"<{PR_URL}|the run page fix> and <https://github.com/yasyf/cc-skills/pull/4250|the card that draws charts> are live."
    assert said(demo)[0]["text"] == f"[the run page fix]({PR_URL}) and [the card that draws charts](https://github.com/yasyf/cc-skills/pull/4250) are live."
    with pytest.raises(DriveError, match=r"\{pr:yasyf/cc-skills#4250\} names no PR of the drive's"):
        talk.say(demo, {"text": "{pr:yasyf/cc-skills#4250} is live."})


ALERT = "1794436805.002349"
TICKET = {"ticket_id": "t1", "ticket_tree": "tree1", "ticket_agent": "agent1", "ticket_call": "call1", "ticket_fingerprint": "print1"}


def alert_order(run: Callable[..., tuple[int, str]], slack_thread: Thread) -> None:
    slack_thread.messages.append({"ts": ALERT, "from_user": False})
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "the release failed", "--from", f"slack:C0ALERT1/{ALERT}", "--incident", "--parts", "fix", "first check")


def test_a_line_in_an_alerts_thread_goes_out_through_cc_slack_reply_with_the_ticket_its_hook_named(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], slack_thread: Thread, monkeypatch: pytest.MonkeyPatch
) -> None:
    alert_order(run, slack_thread)
    line = {"text": "The release failed on the migration.", "channel_id": "C0ALERT1", "thread_ts": ALERT, "order": "o1"}
    assert talk.say(demo, line | {"dry_run": True}) == f"DRY thread {ALERT} of channel C0ALERT1, 0 pictures: The release failed on the migration."
    assert talk.say(demo, line | TICKET).startswith(f"POSTED {POSTED}")
    assert posts == [
        (
            "reply", "--agent", "claude", "--channel", "C0ALERT1", "--thread", ALERT, "--text", "The release failed on the migration.",
            "--ticket", "t1", "--ticket-tree", "tree1", "--ticket-agent", "agent1", "--ticket-call", "call1", "--ticket-fingerprint", "print1",
        )
    ]
    assert said(demo)[0]["channel"] == "C0ALERT1"

    def unreadable(*where: str) -> list[dict]:
        raise slack.SlackError("cc-slack can't read this thread (thread_not_found)")

    with monkeypatch.context() as unread:
        unread.setattr(slack, "thread", unreadable)
        with pytest.raises(DriveError, match=f"^nothing posted: refused, o1's thread {ALERT} is in channel C0ALERT1, so pass channel_id C0ALERT1$"):
            talk.say(demo, {"text": "The release failed on the migration.", "thread_ts": ALERT, "order": "o1"})
    with pytest.raises(DriveError) as refused:
        talk.say(demo, {"text": "The release failed on the migration.", "thread_ts": "1794200090.000700", "order": "o1"})
    assert str(refused.value).startswith(
        "nothing posted: refused, thread 1794200090.000700 is none of o1's threads, its own, its cards' or its PRs' cards':"
        f" {ALERT} with channel_id C0ALERT1; post it in one of them"
    )
    assert len(posts) == 1


@pytest.mark.parametrize(
    ("post", "why"),
    [
        ({"channel_id": "D123", "thread_ts": ALERT}, "channel_id D123 is his DM; leave channel_id out"),
        ({"channel_id": "C0ALERT1"}, "channel_id C0ALERT1 names the channel of a thread outside his DM, so it takes that thread's thread_ts"),
        ({"channel_id": "C0ALERT1", "thread_ts": ALERT, "tap_prs": [PR_URL]}, "tap_prs draws only in his DM"),
    ],
)
def test_a_post_outside_his_dm_takes_a_thread_and_nothing_only_his_dm_draws(demo: str, posts: list[tuple[str, ...]], post: dict, why: str) -> None:
    with pytest.raises(DriveError, match=f"nothing posted: {re.escape(why)}"):
        talk.say(demo, {"text": "The release failed on the migration."} | post)
    assert (posts, said(demo)) == ([], [])


def test_a_note_to_a_person_with_no_thread_goes_out_through_cc_slack_send_into_their_group_dm_with_him_and_the_bot(demo: str, posts: list[tuple[str, ...]]) -> None:
    note = {"text": "The run page shows its history again, since 6:40 PM.", "channel_id": "U0REPORTER"}
    assert talk.say(demo, note | {"dry_run": True}) == f"DRY the group DM of U0REPORTER, him and the bot, 0 pictures: {note['text']}"
    assert talk.say(demo, note | TICKET).startswith(f"POSTED {POSTED}")
    assert posts == [
        (
            "send", "--agent", "claude", "--channel", "U0REPORTER", "--text", note["text"],
            "--ticket", "t1", "--ticket-tree", "tree1", "--ticket-agent", "agent1", "--ticket-call", "call1", "--ticket-fingerprint", "print1",
        )
    ]
    assert said(demo)[0]["channel"] == "U0REPORTER"


def test_a_post_whose_report_is_about_its_order_in_another_orders_thread_is_told_to_post_in_its_own_never_with_the_other(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "why the copy stalls", "--thread", "1794200000.000100", "--parts", "cause")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "the run page", "--thread", "1794400000.000100")
    listed = "nothing posted: refused, thread 1794400000.000100 is o2's thread and none of o1's threads, its own, its cards' or its PRs' cards': 1794200000.000100;"
    report(monkeypatch, {"close": "o1"})
    with pytest.raises(DriveError) as refused:
        talk.say(demo, {"text": "The copy stalls on the lock.", "thread_ts": "1794400000.000100", "order": "o1", "record": "666938"})
    assert str(refused.value) == f"{listed} its report is about o1, so post it in one of them"
    report(monkeypatch, {"order": "o1", "part": "cause"})
    with pytest.raises(DriveError) as refused:
        talk.say(demo, {"text": "The copy stalls on the lock.", "thread_ts": "1794400000.000100", "order": "o1", "record": "666939"})
    assert str(refused.value) == (
        f'{listed} its report is about o1, so post it in one of them, or post it with no order and record "cause" with drive order posted o1'
        " <its permalink> --record <the report's seq>"
    )
    assert posts == []
