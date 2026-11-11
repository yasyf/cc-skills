import json
import os
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import Thread
from drive import cli, dashboard, picture, render, slack, state, talk, view, watch
from drive.state import DriveError

WAITS = "demo:j1-7"
CARD_URL = "https://acme.slack.com/archives/D123/p1794300000000100"
PR_URL = "https://github.com/yasyf/cc-skills/pull/7"
POSTED = "https://acme.slack.com/archives/D123/p1794300000000900"
QUESTION = "Lock each chat's database, or the whole table?"


@pytest.fixture
def posts(demo: str, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    sent: list[tuple[str, ...]] = []
    question = {"url": CARD_URL, "card": {"title": QUESTION, "waits": WAITS}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "https://acme.slack.com/", "questions": [question], "pr_cards": []})
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: sent.append(argv) or {"permalink": POSTED, "thread_ts": "1794300000.000900"})
    with state.edit(demo) as data:
        data["prs"]["yasyf/cc-skills#7"] = state.new_pr(PR_URL, "j1", "o1") | {"title": "New: \U0001f680 the run page shows its history"}
    return sent


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


def test_a_stack_card_names_each_pr_by_its_url_and_the_dashboard_keeps_the_stack(demo: str, posts: list[tuple[str, ...]]) -> None:
    with pytest.raises(DriveError, match=r"tap_prs names yasyf/cc-skills#8, which the drive holds no PR for"):
        talk.say(demo, {"text": "land these", "tap_prs": [PR_URL, "https://github.com/yasyf/cc-skills/pull/8"]})
    assert (posts, said(demo)) == ([], [])
    talk.say(demo, {"text": "land these", "tap_prs": ["yasyf/cc-skills#7"]})
    assert posts[0] == ("dm-status", "--agent", "claude", "--tap-pr", PR_URL, "--", "land these")
    assert said(demo)[0]["prs"] == ["yasyf/cc-skills#7"]


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


def report(monkeypatch: pytest.MonkeyPatch, attrs: dict[str, str]) -> None:
    monkeypatch.setattr(cli.reads, "run", lambda argv, checkout: json.dumps({"seq": argv[2], "lane": "root", "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": attrs}}))


def test_a_post_for_a_report_delivers_the_part_its_job_answered(
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
    assert talk.say(demo, {"text": "The cause is the lock.", "thread_ts": thread, "order": "o1", "record": "661194"}) == f'POSTED {answer}; o1 "cause": posted {answer}'
    parts = state.load(demo)["orders"]["o1"]["parts"]
    assert (parts["cause"]["posted"], parts["options as a card"]["posted"]) == (answer, None)
    later = state.now() + timedelta(minutes=30)
    gaps = watch.part_gaps(watch.Watcher(demo, offset=0, ticked_at=later), state.load(demo), later)
    assert gaps == {("o1", "cause"): None, ("o1", "options as a card"): "no job"}


def test_a_report_from_the_session_that_does_not_hold_its_order_posts_nothing(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch, run: Callable[..., tuple[int, str]]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a verb for owners")
    report(monkeypatch, {"orders": "o1"})
    with pytest.raises(DriveError, match=r"nothing posted: refused, the report is the coordinator's: o1 is the communicator's order"):
        talk.say(demo, {"text": "The verb is live.", "thread_ts": "1794200000.000100", "order": "o1", "record": "661300"})
    assert (posts, said(demo)) == ([], [])


def test_a_catch_up_opens_with_its_title_from_the_time_he_was_last_heard_whatever_title_it_came_with(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    report(monkeypatch, {"catch_up": "true"})
    state.append_event(demo, "catch-up", dm="D123", since="9:00 AM", orders="o1")
    state.append_event(demo, "catch-up", dm="D123", since="5:20 PM", orders="o1")
    talk.say(demo, {"text": "The run page fix is live.", "record": "660299"})
    talk.say(demo, {"text": "*Catch-up since 4:00 PM*\nThe run page fix is live.", "record": "660299"})
    titled = "*Catch-up since 5:20 PM*\nThe run page fix is live."
    assert [argv[-1] for argv in posts] == [titled, titled]
    assert [kept["text"] for kept in said(demo)] == [titled, titled]
    report(monkeypatch, {"orders": "o1"})
    talk.say(demo, {"text": "The run page fix is live.", "record": "660300"})
    assert posts[-1][-1] == "The run page fix is live."


def test_a_post_in_a_thread_or_at_his_dm_root_opens_with_each_card_holding_built_work_there_and_a_card_with_none(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = "2026-11-10T20:59:00-08:00"
    question = {"question": QUESTION, "options": ["Each chat"], "recommended": "Each chat", "asked": asked, "url": CARD_URL, "card": {"title": QUESTION, "waits": WAITS}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "https://acme.slack.com/", "questions": [question], "pr_cards": []})
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
            "created": asked,
            "updated": asked,
            "parts": {"lock it": state.new_part()},
        }
        data["jobs"]["j1"] = state.new_job("o1", "builder", "lock it", ["lock it"], asked) | {"state": "built", "branch": "lane/lock", "waits": [WAITS]}
    line = f"WAITING-ON-HIM <{CARD_URL}|{QUESTION}> blocks j1, built on lane/lock, since {render.when(asked)}"
    talk.say(demo, {"text": "Shipped.", "thread_ts": thread})
    talk.say(demo, {"text": "Elsewhere.", "thread_ts": "1794200000.000555"})
    talk.say(demo, {"text": "Catch-up"})
    report(monkeypatch, {"catch_up": "true"})
    state.append_event(demo, "catch-up", dm="D123", since="5:20 PM", orders="o1")
    talk.say(demo, {"text": "The fix is live.", "record": "660299"})
    talk.say(demo, {"text": "Pick one", "card": {"title": "Pick one"}})
    talk.say(demo, {"text": f"WAITING-ON-HIM {{card:{WAITS}}} blocks j1, built on lane/lock, since {render.when(asked)}\nMore.", "thread_ts": thread})
    assert [argv[-1] for argv in posts] == [
        f"{line}\nShipped.",
        "Elsewhere.",
        f"{line}\nCatch-up",
        f"*Catch-up since 5:20 PM*\n{line}\nThe fix is live.",
        "Pick one",
        f"{line}\nMore.",
    ]
