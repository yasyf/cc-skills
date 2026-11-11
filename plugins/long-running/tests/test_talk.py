import json
import os
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
    refs,
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


def test_a_record_draws_in_its_own_words_linked_to_what_it_links(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    oom = "sandsql-server ran out of memory at its 32 GiB limit, 3 times since 9:40 PM"
    shown = {"661194": {"text": oom, "refs": {"url": "https://app.datadoghq.com/notebook/1"}}, "661195": {"text": "the write lock is held across the whole call", "refs": {}}}
    asked: list[list[str]] = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout: asked.append(argv) or json.dumps(shown[argv[2]]))
    talk.say(demo, {"text": "The pod restarts because {record:661194}. Stalls: {record:661195}."})
    assert asked == [["cci", "show", seq, "--json", "--drive", demo] for seq in ("661194", "661195")]
    assert posts[0][-1] == f"The pod restarts because <https://app.datadoghq.com/notebook/1|{oom}>. Stalls: the write lock is held across the whole call."
    assert said(demo)[0]["text"] == f"The pod restarts because [{oom}](https://app.datadoghq.com/notebook/1). Stalls: the write lock is held across the whole call."


def test_a_card_asked_again_with_new_words_moves_its_question_and_each_wait_on_it_to_the_new_card(demo: str, posts: list[tuple[str, ...]]) -> None:
    other = "https://acme.slack.com/archives/D123/p1794300000000400"
    with state.edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "design the store", [], state.stamp()) | {"waits": [CARD_URL, other]}
        data["jobs"]["j1"]["questions"].append(state.new_question(None, QUESTION, state.stamp(), card=CARD_URL) | {"waits": WAITS})
        data["jobs"]["j2"] = state.new_job("o1", "builder", "ship the store", [], state.stamp()) | {"waits": [CARD_URL]}
    talk.say(demo, {"card": {"title": QUESTION, "waits": WAITS}})
    jobs = state.load(demo)["jobs"]
    assert jobs["j1"]["questions"][0]["card"] == POSTED
    assert (jobs["j1"]["waits"], jobs["j2"]["waits"]) == ([POSTED, other], [POSTED])
    talk.say(demo, {"card": {"title": QUESTION, "waits": WAITS}})
    assert state.load(demo)["jobs"] == jobs


def test_a_line_naming_a_cause_with_no_record_posts_nothing(demo: str, posts: list[tuple[str, ...]]) -> None:
    with pytest.raises(DriveError, match=r"nothing posted: a line naming a cause carries \{record:<seq>\} .*: The runs failed because the pool was full$"):
        talk.say(demo, {"text": "Merged.\nThe runs failed because the pool was full", "dry_run": True})
    assert (posts, said(demo)) == ([], [])
    talk.say(demo, {"text": '> did it fail because of the pool?\nYou asked "is it because of the pool"; the runs are back.'})
    assert len(posts) == 1
    assert refs.unbacked("```text\nThe cause is the pool\n```\nDue to a full pool, runs wait.") == ["Due to a full pool, runs wait."]


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


def test_a_pr_card_serving_drive_tooling_posts_only_under_a_line_quoting_that_order(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]]
) -> None:
    tooling = "Drive tooling: a card named in a post shows under the line"
    run("order", "add", "--project", "The drive", "--workstream", "Cards", "cards show under the line")
    with state.edit(demo) as data:
        data["orders"]["o1"]["text"] = tooling
        data["prs"]["yasyf/cc-skills#7"]["serves"] = "o1"
    for dry_run in (True, False):
        with pytest.raises(DriveError, match=rf'yasyf/cc-skills#7 serves a drive tooling order, .*nothing was posted: put "{tooling}" in text'):
            talk.say(demo, {"text": "land these", "tap_prs": [PR_URL], "dry_run": dry_run})
    assert (posts, said(demo)) == ([], [])
    talk.say(demo, {"text": f"_{tooling}_", "tap_prs": [PR_URL]})
    assert posts[0][-1] == f"_{tooling}_"


def test_a_tooling_order_whose_words_check_post_refuses_draws_its_pr_card_once_reworded(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    flagged = "Drive tooling: the thread reader cannot open a teammate's session (defect 661904, j844): its script takes only a local path"
    reworded = "Drive tooling: the thread reader cannot open a teammate's session: its script takes only a local path"
    monkeypatch.setattr(slack, "check_post", lambda text: ["1:97 drive-term 'j844': say what happens to the reader's work instead"] if "j844" in text else [])
    run("order", "add", "--project", "The drive", "--workstream", "Cards", "cards show under the line")
    with state.edit(demo) as data:
        data["orders"]["o1"]["text"] = flagged
        data["prs"]["yasyf/cc-skills#7"]["serves"] = "o1"
    for dry_run in (True, False):
        with pytest.raises(DriveError, match=r"nothing posted: `cc-slack check-post` finds this .*\n1:97 drive-term 'j844'"):
            talk.say(demo, {"text": flagged, "tap_prs": [PR_URL], "dry_run": dry_run})
    assert (posts, said(demo)) == ([], [])
    assert run("order", "set", "o1", "--words", reworded)[0] == 0
    assert talk.say(demo, {"text": reworded, "tap_prs": [PR_URL], "dry_run": True}) == f"DRY his DM root, 0 pictures, PR card for 1 PRs: {reworded}"
    talk.say(demo, {"text": reworded, "tap_prs": [PR_URL]})
    assert posts == [("dm-status", "--agent", "claude", "--tap-pr", PR_URL, "--", reworded)]


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


def report(monkeypatch: pytest.MonkeyPatch, attrs: dict[str, str]) -> None:
    monkeypatch.setattr(cli.reads, "run", lambda argv, checkout: json.dumps({"seq": argv[2], "lane": "root", "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": attrs}}))


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


def test_a_post_goes_out_in_exactly_the_words_it_was_given_while_built_work_waits_on_his_card(
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
    talk.say(demo, {"text": "Shipped.", "thread_ts": thread})
    talk.say(demo, {"text": "Catch-up"})
    report(monkeypatch, {"catch_up": "true"})
    state.append_event(demo, "catch-up", dm="D123", since="5:20 PM", orders="o1")
    talk.say(demo, {"text": "The fix is live.", "record": "660299"})
    assert [argv[-1] for argv in posts] == ["Shipped.", "Catch-up", "*Catch-up since 5:20 PM*\nThe fix is live."]


def test_words_check_post_finds_anything_in_go_nowhere(demo: str, posts: list[tuple[str, ...]], checked: Checked) -> None:
    checked.findings = ["1:5 drive-term 'fence': say what happens to the reader's work instead"]
    with pytest.raises(DriveError, match=r"nothing posted: `cc-slack check-post` finds this in the post's words.*\n1:5 drive-term 'fence'"):
        talk.say(demo, {"text": "{pr:7|a chart fence draws} is live."})
    assert checked.texts == [f"<{PR_URL}|a chart fence draws> is live."]
    assert (posts, said(demo)) == ([], [])


def test_a_pr_is_linked_under_the_words_the_post_gives_it_whether_the_drive_holds_it_or_not(demo: str, posts: list[tuple[str, ...]]) -> None:
    talk.say(demo, {"text": "{pr:7|the run page fix} and {pr:yasyf/cc-skills#4250|the card that draws charts} are live."})
    assert posts[0][-1] == f"<{PR_URL}|the run page fix> and <https://github.com/yasyf/cc-skills/pull/4250|the card that draws charts> are live."
    assert said(demo)[0]["text"] == f"[the run page fix]({PR_URL}) and [the card that draws charts](https://github.com/yasyf/cc-skills/pull/4250) are live."
    with pytest.raises(DriveError, match=r"\{pr:yasyf/cc-skills#4250\} names no PR of the drive's"):
        talk.say(demo, {"text": "{pr:yasyf/cc-skills#4250} is live."})


def test_a_word_he_corrected_is_refused_naming_its_proper_term_and_the_proper_term_or_a_quote_of_him_posts(demo: str, posts: list[tuple[str, ...]]) -> None:
    with state.edit(demo) as data:
        data["stand_ins"]["backup"] = "S3 replica"
    with pytest.raises(DriveError) as refused:
        talk.say(demo, {"text": "The backup copy is current."})
    assert str(refused.value) == (
        "nothing posted: this post calls a thing by a word he corrected, so write its proper term in that word's place:"
        ' "backup" becomes "S3 replica" (his Nov 11 12:36 AM words "use the proper fucking terms for things")'
    )
    with pytest.raises(DriveError, match='"backups" becomes "S3 replica"'):
        talk.say(demo, {"card": {"title": "Restore from which backups?", "body": "One line.", "options": ["Yes", "No"]}})
    assert (posts, said(demo)) == ([], [])
    talk.say(demo, {"text": "The S3 replica is current."})
    talk.say(demo, {"text": 'You asked "is the backup current"; the S3 replica is.'})
    assert len(posts) == 2


def test_a_word_he_corrected_is_refused_in_a_links_words_or_a_visuals_caption_and_posts_in_a_quote_line_of_his(demo: str, posts: list[tuple[str, ...]]) -> None:
    with state.edit(demo) as data:
        data["stand_ins"]["backup"] = "S3 replica"
    with pytest.raises(DriveError, match='"backup" becomes "S3 replica"'):
        talk.say(demo, {"text": "The <https://example.com/replica|backup copy> is current."})
    with pytest.raises(DriveError, match='"backup" becomes "S3 replica"'):
        talk.say(demo, {"text": "Replica status.", "visuals": [{"kind": "chart", "source": "{}", "caption": "The backup copy."}]})
    assert (posts, said(demo)) == ([], [])
    talk.say(demo, {"text": "> Is the backup copy current?\nThe S3 replica is current."})
    assert posts[0][-1] == "> Is the backup copy current?\nThe S3 replica is current."
