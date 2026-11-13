import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import Checked, Jev, Thread
from drive import (
    agents,
    cli,
    dashboard,
    picture,
    reads,
    refs,
    render,
    settled,
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
CORRECTIONS = agents.corrections
DRAW = talk.draw
SSQL = "are we doing the ssql release foe it"
FOOTERS = "why didnt our release footers whatever catch this and bring ssql along with API"


@pytest.fixture
def posts(demo: str, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    sent: list[tuple[str, ...]] = []
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "https://acme.slack.com/", "questions": [question_card(CARD_URL, QUESTION, WAITS)], "pr_cards": [], "closed": []})

    def cc_slack(*argv: str) -> dict:
        if argv[0] == "dm-status" and not {"--", "--card", "--file"} & set(argv):
            raise slack.SlackError("dm-status needs a card, text, or files")
        sent.append(argv)
        return {"permalink": POSTED, "thread_ts": "1794300000.000900"}

    monkeypatch.setattr(slack, "cc_slack", cc_slack)
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


def test_a_card_named_in_its_own_thread_reads_this_card_with_no_link(demo: str, posts: list[tuple[str, ...]]) -> None:
    talk.say(demo, {"text": "The design job behind {card:demo:j1-7} has your question", "thread_ts": "1794300000.000100"})
    talk.say(demo, {"text": "{card:demo:j1-7|The lock question} has your question", "thread_ts": "1794300000.000100"})
    talk.say(demo, {"text": "Why {card:demo:j1-7} asks: one lock per chat", "about": WAITS})
    assert posts == [
        ("dm-status", "--agent", "claude", "--thread", "1794300000.000100", "--", "The design job behind this card has your question"),
        ("dm-status", "--agent", "claude", "--thread", "1794300000.000100", "--", "The lock question has your question"),
        ("dm-status", "--agent", "claude", "--about", WAITS, "--", "Why this card asks: one lock per chat"),
    ]
    assert said(demo)[0]["text"] == f"The design job behind [{QUESTION}](#/queue/question/1794300000.000100) has your question"


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
    assert posts[0][-1] == f"<{RELEASE}|Release 14765>, carrying <{PR_URL}|the fix>, is failing; Slack draws `<{RELEASE}|its <link>>` broken."


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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "a verb for owners", "--thread", "1794200000.000100")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: one owner per order")
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
        "661194": {"kind": "report", "text": "sandsql-server ran out of memory at its 32 GiB limit, 3 times since 9:40 PM", "refs": {"url": notebook}},
        "661195": {"kind": "report", "text": "the write lock is held across the whole call", "refs": {}},
        "661196": {"kind": "report", "text": timeouts, "refs": {"url": notebook}},
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
    commit = "1:30 commit-sha '1a2b3c4': link the commit"
    assert refs.answer_ids(["1:12 commit-sha 'f03a0aa': link the commit", commit], state.load(demo)) == [commit]


def test_a_jobs_start_and_a_records_time_are_filled_from_the_drives_stamps(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    today = state.now().replace(microsecond=0)
    with state.edit(demo) as data:
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
    with state.edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "design the store", [], state.stamp()) | {"waits": [CARD_URL, other]}
        data["jobs"]["j1"]["questions"].append(state.new_question(None, QUESTION, state.stamp(), card=CARD_URL) | {"waits": WAITS})
        data["jobs"]["j2"] = state.new_job("o1", "builder", "ship the store", [], state.stamp()) | {"waits": [CARD_URL]}
        data["prs"]["yasyf/cc-skills#7"]["waits"] = [other, CARD_URL]
    talk.say(demo, {"card": {"title": QUESTION, "waits": WAITS}})
    data = state.load(demo)
    jobs, prs = data["jobs"], data["prs"]
    assert jobs["j1"]["questions"][0]["card"] == POSTED
    assert (jobs["j1"]["waits"], jobs["j2"]["waits"], prs["yasyf/cc-skills#7"]["waits"]) == ([WAITS, other], [WAITS], [other, WAITS])
    talk.say(demo, {"card": {"title": QUESTION, "waits": WAITS}})
    assert (state.load(demo)["jobs"], state.load(demo)["prs"]) == (jobs, prs)


def test_a_question_card_returns_and_records_its_own_permalink(demo: str, posts: list[tuple[str, ...]]) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "design the store", [], state.stamp())
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "Keep the old store?", state.stamp()) | {"waits": "demo:j1-8"})
    assert talk.say(demo, {"card": {"title": "Keep the old store?", "waits": "demo:j1-8"}, "thread_ts": "1794200000.000100"}) == f"POSTED {POSTED} CARD {POSTED}"
    assert state.load(demo)["jobs"]["j1"]["questions"][0]["card"] == POSTED
    assert said(demo)[0]["card"] == POSTED


def test_a_card_no_question_keeps_moves_each_wait_on_it_to_its_waits_id_when_asked_again_with_new_words(demo: str, posts: list[tuple[str, ...]]) -> None:
    with state.edit(demo) as data:
        data["jobs"]["j2"] = state.new_job("o1", "builder", "ship the store", [], state.stamp()) | {"waits": [CARD_URL]}
        data["prs"]["yasyf/cc-skills#7"]["waits"] = [CARD_URL]
    talk.say(demo, {"card": {"title": QUESTION, "waits": WAITS}})
    data = state.load(demo)
    assert (data["jobs"]["j2"]["waits"], data["prs"]["yasyf/cc-skills#7"]["waits"]) == ([WAITS], [WAITS])


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
    assert [entry["visuals"] for entry in view.history_page(board.model, None, "said", 1)["moments"]] == [kept["visuals"]]


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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "show a run's history")
    building(demo)
    with pytest.raises(DriveError, match=r"tap_prs names yasyf/cc-skills#8, which the drive holds no PR for"):
        talk.say(demo, {"text": "land these", "tap_prs": [PR_URL, "https://github.com/yasyf/cc-skills/pull/8"]})
    assert (posts, said(demo)) == ([], [])
    talk.say(demo, {"text": "land these", "tap_prs": ["yasyf/cc-skills#7"]})
    assert posts[0] == ("dm-status", "--agent", "claude", "--tap-pr", PR_URL, "--", "land these")
    assert said(demo)[0]["prs"] == ["yasyf/cc-skills#7"]


def test_the_drives_own_draw_of_his_card_goes_at_his_dm_root_under_a_line_linking_each_order_its_prs_carry_out(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "show a run's history", "--thread", "1794200000.000100")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: the watch draws the PR card")
    building(demo)
    tooling = "https://github.com/yasyf/cc-skills/pull/8"
    with state.edit(demo) as data:
        data["prs"]["yasyf/cc-skills#8"] = state.new_pr(tooling, "j1", "o2") | {"title": "Fix: \U0001f41b the watch draws the PR card"}
    view.refresh_cards(demo)
    monkeypatch.setattr("drive.card.cards", lambda asked: {"cards": [{"repo": "yasyf/cc-skills", "number": 7, "card": {"title": "New: approve the run page shows its history?"}}], "skipped": []})
    assert DRAW(demo, ["yasyf/cc-skills#7", "yasyf/cc-skills#8"]) == POSTED
    assert posts == [
        (
            "dm-status", "--agent", "claude", "--tap-pr", PR_URL, "--tap-pr", tooling, "--",
            'Carries out your "<https://acme.slack.com/archives/D123/p1794200000000100|show a run\'s history>"'
            f"\n<{dashboard.link(demo)}#/feature/o2|Drive tooling: the watch draws the PR card>",
        )
    ]
    assert said(demo)[0]["text"] == 'Carries out your "[show a run\'s history](#/feature/o1)"\n[Drive tooling: the watch draws the PR card](#/feature/o2)'
    assert [state.load(demo)["prs"][key]["card"] for key in ("yasyf/cc-skills#7", "yasyf/cc-skills#8")] == [POSTED, POSTED]


def test_a_pr_card_goes_in_the_thread_of_each_open_order_its_pr_carries_out_so_the_one_he_reads_carries_its_link(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "show a run's history", "--thread", "1794200000.000100")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "There's no approval button", "--thread", "1794300000.000100")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "why is the page slow?", "--thread", "1794400000.000100")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "one more check", "--from", "slack:C9TEAM/1794500000.000100")
    building(demo)
    with state.edit(demo) as data:
        for order_id in ("o2", "o3", "o4"):
            state.carry(data, "j1", order_id, list(data["orders"][order_id]["parts"]))
        data["orders"]["o3"]["state"] = "done"
    with pytest.raises(DriveError, match="pass channel_id C9TEAM and thread_ts 1794500000.000100"):
        talk.say(demo, {"text": "land these", "tap_prs": [PR_URL]})
    team = {"text": "land these", "tap_prs": [PR_URL], "channel_id": "C9TEAM", "thread_ts": "1794500000.000100"}
    assert talk.say(demo, team | {"dry_run": True}) == (
        "DRY thread 1794200000.000100, thread 1794300000.000100, thread 1794500000.000100 of channel C9TEAM, 0 pictures, PR card for 1 PRs: land these"
    )
    talk.say(demo, team | TICKET)
    assert posts == [
        *(("dm-status", "--agent", "claude", "--thread", thread, "--tap-pr", PR_URL, "--", "land these") for thread in ("1794200000.000100", "1794300000.000100")),
        (
            "reply", "--agent", "claude", "--channel", "C9TEAM", "--thread", "1794500000.000100", "--text", f"land these\n<{POSTED}|the run page shows its history>",
            "--ticket", "t1", "--ticket-tree", "tree1", "--ticket-agent", "agent1", "--ticket-call", "call1", "--ticket-fingerprint", "print1",
        ),
    ]
    assert [(event.get("channel"), event["prs"]) for event in said(demo)] == [(None, ["yasyf/cc-skills#7"])] * 2 + [("C9TEAM", ["yasyf/cc-skills#7"])]


def test_a_pr_cards_line_that_fails_in_a_later_thread_says_the_card_went_out_so_none_is_posted_again(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "show a run's history", "--thread", "1794200000.000100")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "There's no approval button", "--thread", "1794300000.000100")
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


def test_a_post_its_order_will_not_take_still_says_it_went_out_so_nobody_sends_it_twice(
    demo: str, run: Callable[..., tuple[int, str]], posts: list[tuple[str, ...]], unrecorded: list[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "SandSQL", "why app actions fail")
    line = talk.say(demo, {"text": "done", "order": "o1"})
    assert line.startswith(f"POSTED {POSTED}; not recorded on o1: only the communicator records a post")
    assert len(posts) == 1
    [defect] = unrecorded
    assert defect.startswith(f"say posted {POSTED} but it was not recorded on o1")


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
def test_a_handlers_post_in_its_orders_thread_is_recorded_on_that_order_though_the_record_runs_as_no_session(
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
    with state.edit(demo) as data:
        data["roles"]["communicator"]["session"] = "communicator-session"
        data["roles"]["coordinator"]["session"] = "elsewhere"
        data["handlers"]["a74493d1"] = {"session": os.environ["CLAUDE_CODE_SESSION_ID"], "transcript": None, "started": state.stamp(), "holds": {}, "handed": [], "posted": []}
    answer = slack_thread.post(state.now(), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": answer, "thread_ts": thread})
    report(monkeypatch, attrs, lane=lane)
    assert talk.say(demo, {"text": text, "thread_ts": thread, "order": "o1", "record": "683585"}) == f"POSTED {answer}; {recorded.format(answer=answer)}"
    assert unrecorded == []
    assert state.load(demo)["orders"]["o1"]["last_line"] is not None


def test_a_post_on_an_order_goes_out_in_any_thread_and_a_thread_the_order_holds_not_yet_joins_its_threads(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL errors on plat", "--thread", "1794200000.000100", "--incident", "--parts", "fix", "first check")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL error log count", "--from", "slack:D123/1794300000.000100", "--thread", "1794300000.000100", "--joins", "o1")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why the copy stalls", "--thread", "1794400000.000100")
    run("start", "demo", "--role", "communicator")
    with state.edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"

    def say(thread: str | None, order: str) -> str:
        posted = slack_thread.post(state.now() + timedelta(minutes=len(slack_thread.messages) + 1), from_claude=True, thread_ts=thread).split("?")[0]
        line = f"{posted}?thread_ts={thread}" if thread else posted
        monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": line, "thread_ts": thread})
        return talk.say(demo, {"text": "The copy stalls on the lock.", "thread_ts": thread, "order": order}).removeprefix(f"POSTED {line}; {order} progress line, no part delivered: {line}")

    assert [say(thread, order) for thread, order in [("1794300000.000100", "o1"), ("1794200000.000100", "o2"), (None, "o3")]] == ["", "", ""]
    assert say("1794400000.000100", "o1") == ", in thread 1794400000.000100, now one of its threads"
    assert say("1794400000.000100", "o2") == ""
    assert say("1794200000.000100", "o3") == ", in thread 1794200000.000100, now one of its threads"
    data = state.load(demo)
    assert [[thread for _, thread, _ in data["orders"][order]["asked_in"]] for order in ("o1", "o2", "o3")] == [["1794400000.000100"], [], ["1794200000.000100"]]
    assert (state.thread_order(data, "1794400000.000100"), state.thread_order(data, "1794200000.000100")) == ("o1", "o3")


def report(monkeypatch: pytest.MonkeyPatch, attrs: dict[str, str], lane: str = "root") -> None:
    record = {"kind": "report", "lane": lane, "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": attrs}}
    monkeypatch.setattr(cli.reads, "run", lambda argv, checkout: json.dumps({"seq": argv[2], **record}))


def test_a_post_delivers_the_part_its_report_names_and_a_progress_report_naming_only_a_job_delivers_none(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why it fails, and the options", "--kind", "answer", "--thread", thread, "--parts", "cause", "options as a card")
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


def test_a_report_naming_two_parts_of_its_order_in_its_one_part_attr_delivers_both(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Releases", f"1) {SSQL}, and 2) {FOOTERS}?", "--kind", "answer", "--thread", thread, "--parts", SSQL, FOOTERS)
    run("start", "demo", "--role", "communicator")
    with state.edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    answer = slack_thread.post(state.now(), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": answer, "thread_ts": thread})
    report(monkeypatch, {"order": "o1", "thread": thread, "part": f"o1:{SSQL};o1:{FOOTERS}"})
    said = "Yes, the SandSQL release is live. The footers missed it because that release named only api."
    assert talk.say(demo, {"text": said, "thread_ts": thread, "order": "o1", "record": "677031"}) == f'POSTED {answer}; o1 "{SSQL}", "{FOOTERS}": posted {answer}'
    assert {part: kept["posted"] for part, kept in state.load(demo)["orders"]["o1"]["parts"].items()} == {SSQL: answer, FOOTERS: answer}


CAUSE_REPORT = (
    "Cause of the QUEUE_FULL refusals from 23:01Z to 23:45Z: load on the shared pod, not the handover stall and not a restore. Its"
    " primary ran at 5.1 to 6.1 cores of its 6.1-core limit for those minutes, so the 32-deep write queues filled. No open PR stops"
    " one team's queries from filling another team's write queue."
)


def test_a_report_naming_an_incidents_parts_delivers_none_since_each_is_work_he_ordered_and_asks_no_judge(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread, jev: Jev
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Incidents", "QUEUE_FULL refusals", "--thread", thread, "--incident", "--parts", "first check", "fix", "cause")
    run("start", "demo", "--role", "communicator")
    with state.edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    answer = slack_thread.post(state.now(), from_claude=True, text=CAUSE_REPORT)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": answer, "thread_ts": thread})
    report(monkeypatch, {"incident": "true", "order": "o1", "thread": thread, "part": "o1:fix;o1:cause"})
    dry = talk.say(demo, {"text": CAUSE_REPORT, "thread_ts": thread, "order": "o1", "record": "682974", "dry_run": True})
    assert dry.endswith("; o1 progress line, no part delivered")
    line = talk.say(demo, {"text": CAUSE_REPORT, "thread_ts": thread, "order": "o1", "record": "682974"})
    assert line.startswith(f"POSTED {answer}; o1 progress line, no part delivered: {answer}")
    assert [part["posted"] for part in state.load(demo)["orders"]["o1"]["parts"].values()] == [None, None, None]
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
        ("You get it before 12:15 a.m.", None),
        ("Next line by 9:45 AM.", None),
    ],
)
def test_a_post_promises_the_clock_time_after_next_line_read_as_the_time_of_day_nearest_it_when_that_is_ahead(text: str, due: str | None) -> None:
    found = cli.promised_due(text, datetime(2026, 11, 11, 21, 29, 43, 954739).astimezone())
    assert (found and f"{found:%b %d %H:%M}") == due


def test_a_post_promising_the_next_line_by_2_32_owes_2_32_not_the_2_37_it_says_a_fix_is_due_by() -> None:
    found = cli.promised_due("The fix is due by 2:37, and you get the next line by 2:32.", datetime(2026, 11, 12, 2, 23, 28, 890659).astimezone())
    assert (found and f"{found:%b %d %H:%M}") == "Nov 12 02:32"


def test_a_time_a_post_promises_becomes_its_orders_due_once_slack_has_the_post_and_the_next_post_keeps_it(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Iris chat turns fail on sand 0.162.3983", "--thread", thread)
    run("start", "demo", "--role", "communicator")
    at = datetime(2026, 11, 11, 21, 29, 43, 954739).astimezone()
    with state.edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
        data["orders"]["o1"].update(due=state.stamp(at.replace(minute=32, second=0, microsecond=0)), promised=state.stamp(at.replace(minute=27, second=0, microsecond=0)))
    text = "Turns have run without failing since 9:22 PM. Next line by 9:45."
    line = slack_thread.post(at, from_claude=True, text=text)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": line, "thread_ts": thread})
    before = state.load(demo)["orders"]["o1"]
    talk.say(demo, {"text": text, "thread_ts": thread, "order": "o1", "dry_run": True})
    assert state.load(demo)["orders"]["o1"] == before
    due = state.stamp(at.replace(minute=45, second=0, microsecond=0))
    assert talk.say(demo, {"text": text, "thread_ts": thread, "order": "o1"}) == (
        f"POSTED {line}; o1 progress line, no part delivered: {line}, its words promise him the next line by {render.when(due)}, now owed"
    )
    told = state.load(demo)["orders"]["o1"]
    assert (told["due"], told["promised"], told["kept"], state.owed(told)) == (due, slack.sent_at(slack_thread.messages[-1]).isoformat(), None, True)
    later = slack_thread.post(at + timedelta(minutes=10), from_claude=True, text="The fix is in review. Next line by 10:00.")
    assert run("order", "posted", "o1", later) == (0, f"o1 progress line, no part delivered: {later}, its words promise him the next line by {render.when(state.stamp(at.replace(hour=22, minute=0, second=0, microsecond=0)))}, now owed")
    last = slack_thread.post(at + timedelta(minutes=20), from_claude=True, text="The fix merged.")
    run("order", "posted", "o1", last)
    kept = state.load(demo)["orders"]["o1"]
    assert (kept["kept"], state.owed(kept)) == (slack.sent_at(slack_thread.messages[-1]).isoformat(), False)


def test_a_post_on_an_incident_records_its_line_and_keeps_no_time_its_words_promise_since_the_watch_owes_the_next_five_quiet_minutes_on(
    demo: str, run: Callable[..., tuple[int, str]], slack_thread: Thread
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Alerts", "Iris chat turns fail on sand 0.162.3983", "--thread", thread, "--incident", "--parts", "fix", "first check")
    run("start", "demo", "--role", "communicator")
    with state.edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    at = datetime(2026, 11, 11, 21, 29, 43, 954739).astimezone()
    line = slack_thread.post(at, from_claude=True, text="The fix job is changing the turn's retry. Next line by 9:45.")
    code, said = run("order", "posted", "o1", line)
    assert (code, said.startswith(f"o1 progress line, no part delivered: {line}"), "promise" in said) == (0, True, False)
    told = state.load(demo)["orders"]["o1"]
    assert (told["due"], told["last_line"]) == (None, state.stamp(slack.sent_at(slack_thread.messages[-1])))


def test_a_post_cc_slack_reports_failing_after_it_went_out_is_kept_and_recorded_on_its_order_and_one_that_never_went_out_raises(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why the copy stalls", "--thread", thread)
    run("start", "demo", "--role", "communicator")
    with state.edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "https://acme.slack.com/", "questions": [], "pr_cards": [], "closed": []})
    monkeypatch.setattr(view, "stored_cards", lambda name: ({"team_url": "https://acme.slack.com/", "questions": [], "pr_cards": [], "closed": []}, "live"))
    reaches = {"post": True}

    def cc_slack(*argv: str) -> dict:
        if argv[0] == "thread":
            since = float(argv[argv.index("--since-ts") + 1])
            return {"channel_id": "D123", "thread_ts": thread, "messages": [message for message in slack_thread.messages if float(message["ts"]) >= since]}
        if reaches["post"]:
            slack_thread.post(state.now(), from_claude=True, text=argv[-1].replace("&", "&amp;"))
        raise slack.SlackError("context deadline exceeded")

    monkeypatch.setattr(slack, "cc_slack", cc_slack)
    line = talk.say(demo, {"text": "The copy stalls on the lock & its waiter.", "thread_ts": thread, "order": "o1"})
    [kept] = said(demo)
    ts = slack_thread.messages[-1]["ts"]
    assert kept["url"] == f"https://acme.slack.com/archives/D123/p{ts.replace('.', '')}?thread_ts={thread}&cid=D123"
    assert (kept["thread"], kept["order"]) == (thread, "o1")
    assert line == f"POSTED {kept['url']} WARNING cc-slack said context deadline exceeded, but the post is in Slack; o1 progress line, no part delivered: {kept['url']}"
    assert state.load(demo)["orders"]["o1"]["last_line"] == state.stamp(slack.sent_at(slack_thread.messages[-1]))
    reaches["post"] = False
    with pytest.raises(slack.SlackError, match="context deadline exceeded"):
        talk.say(demo, {"text": "The lock is released.", "thread_ts": thread, "order": "o1"})
    assert len(said(demo)) == 1


def test_a_report_delivers_on_an_order_only_from_the_lane_of_its_holder_and_a_refusal_names_the_report_the_handler_passes(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Questions", "which page shows a run's history?", "--thread", thread, "--owner", "communicator")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Run page", "show a run's history", "--thread", thread, "--owner", "coordinator")
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



def test_a_second_report_closing_one_order_posts_nothing_whoever_wrote_it_and_a_failed_post_gives_its_close_back(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread, capsys: pytest.CaptureFixture[str]
) -> None:
    thread = "1794200000.000100"
    for words in ("one fix-live post per order", "one fix-live post per order, again"):
        run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", words, "--thread", thread, "--owner", "meta")
    run("start", "demo", "--role", "communicator")
    with state.edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
    sent: list[tuple[str, ...]] = []
    answer = slack_thread.post(state.now(), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: sent.append(argv) or {"permalink": answer, "thread_ts": thread})
    report(monkeypatch, {"close": "o1"}, lane="watch")
    assert talk.say(demo, {"text": "The fix is live.", "thread_ts": thread, "order": "o1", "record": "684712"}).startswith(f"POSTED {answer}")
    report(monkeypatch, {"close": "o1"}, lane="meta")
    refused = "refused, report 684712 already closes o1, so this second close of it posts nothing: send no close of o1, and a handler passed this one returns NOTHING o1 closes on report 684712"
    with pytest.raises(DriveError, match=re.escape(f"nothing posted: {refused}")):
        talk.say(demo, {"text": "The fix is live.", "thread_ts": thread, "order": "o1", "record": "684713"})
    assert len(sent) == 1
    capsys.readouterr()
    assert cli.main(["order", "set", "o1", "--state", "done", "--record", "684713"]) == 2
    assert capsys.readouterr().err.strip() == f"drive: o1: {refused}"

    def failing(*argv: str) -> dict:
        raise slack.SlackError("context deadline exceeded")

    monkeypatch.setattr(slack, "cc_slack", failing)
    report(monkeypatch, {"close": "o2"}, lane="watch")
    with pytest.raises(slack.SlackError):
        talk.say(demo, {"text": "The fix is live.", "thread_ts": thread, "order": "o2", "record": "686065"})
    assert state.load(demo)["orders"]["o2"]["closed_on"] is None
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: sent.append(argv) or {"permalink": answer, "thread_ts": thread})
    report(monkeypatch, {"close": "o2"}, lane="meta")
    assert talk.say(demo, {"text": "The fix is live.", "thread_ts": thread, "order": "o2", "record": "686070"}).startswith(f"POSTED {answer}")
    report(monkeypatch, {"close": "o2"}, lane="watch")
    with pytest.raises(DriveError, match="refused, report 686070 already closes o2"):
        talk.say(demo, {"text": "The fix is live.", "thread_ts": thread, "order": "o2", "record": "686065"})
    assert len(sent) == 2


def test_a_report_the_away_check_held_posts_nothing_whatever_its_judge_read(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(demo) as data:
        data["held"] = {"684842": {"order": "o1", "at": state.stamp(), "told": "684843"}}
    report(monkeypatch, {"close": "o1"}, lane="watch")
    with pytest.raises(DriveError, match="nothing posted: report 684842 is held for his catch-up, which carries it: return NOTHING held for his catch-up"):
        talk.say(demo, {"text": "The journal fix merged.", "record": "684842"})
    assert (posts, said(demo)) == ([], [])

def owe(name: str, *orders: str) -> str:
    with state.edit(name) as data:
        data["catch_up"] = state.new_catch_up(ASKED, list(orders))
    return f"*Catch-up since {render.when(ASKED)}*\n"


def test_a_catch_up_opens_with_its_title_from_the_time_he_was_last_heard_whatever_title_it_came_with_and_posts_once_for_each_one_owed(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    report(monkeypatch, {"catch_up": "true"})
    owe(demo)
    talk.say(demo, {"text": "The run page fix is live.", "record": "660299"})
    titled = owe(demo) + "The run page fix is live."
    talk.say(demo, {"text": "*Catch-up since 4:00 PM*\nThe run page fix is live.", "record": "660301"})
    assert [argv[-1] for argv in posts] == [titled + OPEN_CARDS] * 2
    assert [kept["text"] for kept in said(demo)] == [f"{titled}\n\n*Your open cards: 1 question*\n[{QUESTION}](#/queue/question/1794300000.000100)"] * 2
    with pytest.raises(DriveError, match="nothing posted: report 660301 is a catch-up, and the drive owes him none, since its last catch-up already posted"):
        talk.say(demo, {"text": "The run page fix is live.", "record": "660301"})
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
    owe(demo)
    assert talk.say(demo, {"text": "The run page fix is live.", "record": "660299"}).endswith("; it carries the reports held for it, no longer held: 700, 702")
    assert state.load(demo)["held"] == {}


def test_corrections_reads_each_correction_re_the_reports_in_one_cci_grep_oldest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    newest_first = [{"seq": 669990, "re": 669950}, {"seq": 669970, "re": 669960}, {"seq": 669960}, {"seq": 669952, "re": 669950}]
    calls: list[list[str]] = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout: calls.append(argv) or "".join(json.dumps(record | {"kind": "correction"}) + "\n" for record in newest_first))
    assert CORRECTIONS("demo", ["669950", "669902"]) == {"669950": ["669952", "669990"]}
    assert CORRECTIONS("demo", []) == {}
    assert calls == [["cci", "grep", ".", "--drive", "demo", "--kind", "correction", "--since", "669902", "--budget", str(agents.CORRECTION_BYTES), "--json"]]


def test_holding_a_report_tells_its_writer_each_correction_cci_already_holds_re_it(
    demo: str, monkeypatch: pytest.MonkeyPatch, cci_corrections: dict[str, list[str]]
) -> None:
    cci_corrections["669950"] = ["669952"]
    told: list[list[str]] = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout: told.append(argv) or json.dumps({"seq": 669957}))
    assert talk.hold(demo, "669950", {"writer": "root"}, "o1") == "669957"
    assert told[0][-1] == talk.HELD.format(seq="669950") + "; the catch-up carries it, corrected by record 669952"


def test_a_report_post_names_each_correction_cci_holds_re_it(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch, cci_corrections: dict[str, list[str]]) -> None:
    report(monkeypatch, {"orders": "o1"})
    cci_corrections["669950"] = ["669952"]
    assert talk.say(demo, {"text": "Each request reads 64 MiB.", "record": "669950"}) == (
        f"POSTED {POSTED}; cci holds corrections of report 669950, 669952: unless this post carries each, reword it with say(edit={POSTED}, text=…)"
    )


def test_a_correction_of_a_held_report_rides_with_it_and_one_after_his_catch_up_names_the_post_to_reword(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch, cci_corrections: dict[str, list[str]]
) -> None:
    records = {"669952": {"kind": "correction", "re": 669950}, "669980": {"kind": "correction", "re": 669950}, "669999": {"kind": "report", "attrs": {"catch_up": "true"}}}

    def shown(argv: list[str], checkout: Path) -> str:
        record = records[argv[2]]
        return json.dumps({"seq": argv[2], "lane": "root", "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": record.get("attrs", {})}} | record)

    monkeypatch.setattr(cli.reads, "run", shown)
    with state.edit(demo) as data:
        data["held"] = {"669950": {"order": "o1", "at": state.stamp(), "told": "669957"}}
    with pytest.raises(DriveError, match="nothing posted: record 669952 corrects report 669950, held for his catch-up, which carries this correction with it"):
        talk.say(demo, {"text": "It reads 16 MiB a request, not 64.", "record": "669952"})
    owe(demo)
    cci_corrections["669950"] = ["669952", "669980"]
    assert talk.say(demo, {"text": "Each request reads 16 MiB.", "record": "669999"}).endswith(
        f"; it carries the reports held for it, no longer held: 669950; cci holds corrections 669952, 669980 of them: unless this post carries each, reword it with say(edit={POSTED}, text=…)"
    )
    assert (state.load(demo)["held"], state.load(demo)["carried"]) == ({}, {"669950": POSTED})
    with pytest.raises(DriveError, match=f"nothing posted: record 669980 corrects report 669950, which his catch-up {re.escape(POSTED)} carried: reword that post"):
        talk.say(demo, {"text": "It reads 12 MiB a request.", "record": "669980"})


def test_his_catch_up_closes_with_every_open_card_waiting_on_him_five_a_message_none_drawn_twice(
    demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = [question_card(f"https://acme.slack.com/archives/D123/p179430000000020{n}", f"Question {n}?", f"demo:j{n}-1") for n in range(1, 8)]
    tap = {"prs": [PR_URL], "asked": ASKED, "url": "https://acme.slack.com/archives/D123/p1794300000000300", "card": {"title": "New: approve the run page history?", "waits": "merge:yasyf:cc-skills:7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "https://acme.slack.com/", "questions": questions, "pr_cards": [tap], "closed": []})
    with state.edit(demo) as data:
        data["prs"]["yasyf/cc-skills#7"].update(checks="pass", stage={"name": "card", "owner": "him", "why": "green, waits on his Approve", "since": ASKED, "woke": None})
    report(monkeypatch, {"catch_up": "true"})
    title = owe(demo)
    text = "*The run page*\nIts fix is live, and {card:demo:j3-1} picks its lock."
    drawn = [f"<{tap['url']}|{tap['card']['title']}>"] + [f"<{card['url']}|{card['question']}>" for card in [*questions[:2], *questions[3:]]]
    sent = (
        f"{title}*The run page*\nIts fix is live, and <{questions[2]['url']}|Question 3?> picks its lock."
        "\n\n*Your open cards: 6 questions, 1 PR card*\n" + "\n".join(drawn[:5]) + "\n\n" + "\n".join(drawn[5:])
    )
    assert talk.say(demo, {"text": text, "record": "660299", "dry_run": True}) == f"DRY his DM root, 0 pictures: {sent}"
    talk.say(demo, {"text": text, "record": "660299"})
    assert posts[-1][-1] == sent
    shown = said(demo)[-1]["text"]
    assert "\n[New: approve the run page history?](#/queue/tap/1794300000.000300)\n" in shown and shown.endswith("\n[Question 7?](#/queue/question/1794300000.000207)")


def test_his_catch_up_posts_nothing_while_cc_slack_cannot_list_the_cards_it_closes_with(demo: str, posts: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    def unlisted(name: str) -> dict:
        raise view.CardsError("cc-slack exited 1")

    monkeypatch.setattr(view, "cards", unlisted)
    report(monkeypatch, {"catch_up": "true"})
    owe(demo)
    with pytest.raises(DriveError, match="nothing posted: his catch-up draws every open card of his, which cc-slack could not list: cc-slack exited 1"):
        talk.say(demo, {"text": "The run page fix is live.", "record": "660299"})
    assert (posts, said(demo)) == ([], [])


def test_a_held_report_is_told_to_the_coordinator_session_that_wrote_it_else_the_one_hearing_its_order(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a verb for owners")
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
    title = owe(demo, "o1")
    talk.say(demo, {"text": "The fix is live.", "record": "660299"})
    assert [argv[-1] for argv in posts] == ["Shipped.", "Catch-up", f"{title}The fix is live." + OPEN_CARDS]
    assert state.load(demo)["catch_up"] is None
    assert state.load(demo)["orders"]["o1"]["last_line"]


def test_a_pr_is_linked_under_the_words_the_post_gives_it_whether_the_drive_holds_it_or_not(demo: str, posts: list[tuple[str, ...]]) -> None:
    talk.say(demo, {"text": "{pr:7|the run page fix} and {pr:yasyf/cc-skills#4250|the card that draws charts} are live."})
    assert posts[0][-1] == f"<{PR_URL}|the run page fix> and <https://github.com/yasyf/cc-skills/pull/4250|the card that draws charts> are live."
    assert said(demo)[0]["text"] == f"[the run page fix]({PR_URL}) and [the card that draws charts](https://github.com/yasyf/cc-skills/pull/4250) are live."
    with pytest.raises(DriveError, match=r"\{pr:yasyf/cc-skills#4250\} names no PR of the drive's"):
        talk.say(demo, {"text": "{pr:yasyf/cc-skills#4250} is live."})


def test_a_pr_in_a_table_cell_is_linked_under_its_words_whether_the_bar_before_them_is_escaped_for_the_table_or_not(
    demo: str, posts: list[tuple[str, ...]]
) -> None:
    rows = "| PR | what |\n|---|---|\n| {first} | lands |\n| {second} | lands |"
    talk.say(demo, {"text": rows.format(first="{pr:7\\|the run page fix}", second="{pr:yasyf/cc-skills#4250|the card that draws charts}")})
    assert posts[0][-1] == rows.format(first=f"<{PR_URL}|the run page fix>", second="<https://github.com/yasyf/cc-skills/pull/4250|the card that draws charts>")
    assert said(demo)[0]["text"] == rows.format(first=f"[the run page fix]({PR_URL})", second="[the card that draws charts](https://github.com/yasyf/cc-skills/pull/4250)")


ALERT = "1794436805.002349"
TICKET = {"ticket_id": "t1", "ticket_tree": "tree1", "ticket_agent": "agent1", "ticket_call": "call1", "ticket_fingerprint": "print1"}


def alert_order(run: Callable[..., tuple[int, str]], slack_thread: Thread) -> None:
    slack_thread.messages.append({"ts": ALERT, "from_user": False})
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the release failed", "--from", f"slack:C0ALERT1/{ALERT}", "--incident", "--parts", "fix", "first check")


def test_a_line_in_an_alerts_thread_goes_out_through_cc_slack_reply_with_the_ticket_its_hook_named(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], slack_thread: Thread
) -> None:
    alert_order(run, slack_thread)
    line = {"text": "The release failed on the migration.", "channel_id": "C0ALERT1", "thread_ts": ALERT, "order": "o1"}
    assert talk.say(demo, line | {"dry_run": True}) == f"DRY thread {ALERT} of channel C0ALERT1, 0 pictures: The release failed on the migration.; o1 progress line, no part delivered"
    assert talk.say(demo, line | TICKET).startswith(f"POSTED {POSTED}")
    assert posts == [
        (
            "reply", "--agent", "claude", "--channel", "C0ALERT1", "--thread", ALERT, "--text", "The release failed on the migration.",
            "--ticket", "t1", "--ticket-tree", "tree1", "--ticket-agent", "agent1", "--ticket-call", "call1", "--ticket-fingerprint", "print1",
        )
    ]
    assert said(demo)[0]["channel"] == "C0ALERT1"
    with pytest.raises(DriveError, match=f"^nothing posted: thread {ALERT} is in channel C0ALERT1, not his DM, so pass channel_id C0ALERT1 with it$"):
        talk.say(demo, {"text": "The release failed on the migration.", "thread_ts": ALERT, "order": "o1"})
    assert len(posts) == 1


def test_an_edit_of_a_line_in_an_alerts_thread_goes_out_with_the_ticket_its_hook_named_and_its_claims_posting_none_of_them_and_the_dashboard_shows_its_new_words(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], slack_thread: Thread
) -> None:
    alert_order(run, slack_thread)
    talk.say(demo, {"text": "The release failed on the migration.", "channel_id": "C0ALERT1", "thread_ts": ALERT, "order": "o1"} | TICKET)
    claims = [{"claim": "The release failed on the migration", "read": "tools/ci release status"}]
    assert talk.say(demo, {"edit": POSTED, "text": "The release failed on the migration {pr:7} fixes.", "claims": claims} | TICKET) == f"EDITED {POSTED}"
    assert posts[1] == (
        "edit", "--url", POSTED, "--text", f"The release failed on the migration <{PR_URL}|the run page shows its history> fixes.",
        "--ticket", "t1", "--ticket-tree", "tree1", "--ticket-agent", "agent1", "--ticket-call", "call1", "--ticket-fingerprint", "print1",
    )
    history = view.history(state.load(demo), (event for event, _ in state.read_events(demo)), "https://acme.slack.com/")
    [shown] = [entry for entry in history if entry["kind"] == "said"]
    assert shown["text"] == f"The release failed on the migration [the run page shows its history]({PR_URL}) fixes."


@pytest.mark.parametrize(
    ("post", "why"),
    [
        ({"channel_id": "D123", "thread_ts": ALERT}, "channel_id D123 is his DM; leave channel_id out"),
        ({"channel_id": "C0ALERT1"}, "channel_id C0ALERT1 names the channel of a thread outside his DM, so it takes that thread's thread_ts"),
        ({"channel_id": "C0ALERT1", "thread_ts": ALERT, "about": WAITS}, "about names the card a reply in his DM is about"),
        ({"channel_id": "U0REPORTER", "card": {"title": QUESTION}}, "a card goes up in his DM and its line in a thread outside it, so with channel_id U0REPORTER it takes"),
    ],
)
def test_a_post_outside_his_dm_takes_a_thread_and_nothing_only_his_dm_draws(demo: str, posts: list[tuple[str, ...]], post: dict, why: str) -> None:
    with pytest.raises(DriveError, match=f"nothing posted: {re.escape(why)}"):
        talk.say(demo, {"text": "The release failed on the migration."} | post)
    assert (posts, said(demo)) == ([], [])


def test_a_card_on_an_order_in_an_alerts_thread_goes_up_in_his_dm_and_its_line_goes_in_the_alerts_thread(
    demo: str, posts: list[tuple[str, ...]], run: Callable[..., tuple[int, str]], slack_thread: Thread
) -> None:
    alert_order(run, slack_thread)
    with pytest.raises(DriveError, match=f"^nothing posted: thread {ALERT} is in channel C0ALERT1, not his DM, so pass channel_id C0ALERT1 with it$"):
        talk.say(demo, {"text": "Your pick holds the fix.", "card": {"title": QUESTION}, "thread_ts": ALERT})
    with pytest.raises(DriveError, match=f"pass channel_id C0ALERT1 and thread_ts {ALERT}"):
        talk.say(demo, {"text": "Ready to land.", "tap_prs": [PR_URL]})
    assert posts == []
    card = {"text": "Your pick holds the fix.", "card": {"title": QUESTION}, "channel_id": "C0ALERT1", "thread_ts": ALERT}
    assert talk.say(demo, card | {"dry_run": True}) == f"DRY his DM root, thread {ALERT} of channel C0ALERT1, 0 pictures, card {QUESTION!r}: Your pick holds the fix."
    assert talk.say(demo, card | TICKET) == f"POSTED {POSTED} CARD {POSTED}"
    talk.say(demo, {"text": "Ready to land.", "tap_prs": [PR_URL], "channel_id": "C0ALERT1", "thread_ts": ALERT} | TICKET)
    tickets = ("--ticket", "t1", "--ticket-tree", "tree1", "--ticket-agent", "agent1", "--ticket-call", "call1", "--ticket-fingerprint", "print1")
    assert posts == [
        ("dm-status", "--agent", "claude", "--card", json.dumps({"title": QUESTION}), "--", "Your pick holds the fix."),
        ("reply", "--agent", "claude", "--channel", "C0ALERT1", "--thread", ALERT, "--text", f"Your pick holds the fix.\n<{POSTED}|{QUESTION}>", *tickets),
        ("dm-status", "--agent", "claude", "--tap-pr", PR_URL, "--", "Ready to land."),
        ("reply", "--agent", "claude", "--channel", "C0ALERT1", "--thread", ALERT, "--text", f"Ready to land.\n<{POSTED}|the run page shows its history>", *tickets),
    ]
    assert [(event["channel"], event["card"]) for event in said(demo)] == [(None, POSTED), ("C0ALERT1", POSTED), (None, None), ("C0ALERT1", POSTED)]


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


def test_a_report_closing_its_order_in_another_orders_thread_his_message_started_records_there_and_joins_it(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    his, own = "1794200000.000100", "1794100000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the incident badge counts only live incidents", "--thread", own)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "how is the dashboard looking", "--from", f"slack:D123/{his}", "--thread", his)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the zoomed-out view", "--thread", "1794150000.000100")
    run("start", "demo", "--role", "communicator")
    with state.edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    slack_thread.messages.append({"ts": his, "thread_ts": his, "from_user": True, "text": "how is the dashboard looking"})
    report(monkeypatch, {"close": "o1"})
    answer = slack_thread.post(state.now() + timedelta(minutes=1), from_claude=True, text="The incident badge counts only live incidents: 3 right now.")
    assert run("order", "posted", "o1", answer, "--record", "677110") == (
        0, f'o1 "the incident badge counts only live incidents": posted {answer}, in thread {his}, now one of its threads'
    )
    data = state.load(demo)
    assert [where[:2] for where in data["orders"]["o1"]["asked_in"]] == [["D123", his]] and state.thread_order(data, his) == "o1"


def test_a_report_about_its_order_goes_out_through_say_in_a_thread_his_message_started_and_joins_it(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    his, alert = "1794200000.000100", "1794100000.000100"
    run("order", "add", "--title", "Short title", "--project", "SandSQL", "--workstream", "Alerts", "--from", f"slack:C0822AHFY3G/{alert}", "unknown column case_v2.audit_status", "--thread", alert)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the run page", "--thread", "1794150000.000100")
    run("start", "demo", "--role", "communicator")
    with state.edit(demo) as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
        data["orders"]["o1"]["thread"] = ["C0822AHFY3G", alert]
    slack_thread.messages.append({"ts": his, "thread_ts": his, "from_user": True, "text": "is the audit_status fix out yet?"})
    report(monkeypatch, {"progress": "true", "order": "o1", "thread": his})
    answer = slack_thread.post(state.now() + timedelta(minutes=1), from_claude=True)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": answer, "thread_ts": his})
    assert talk.say(demo, {"text": "The fix is building.", "thread_ts": his, "order": "o1", "record": "677158"}) == (
        f"POSTED {answer}; o1 progress line, no part delivered: {answer}, in thread {his}, now one of its threads"
    )
    assert [where[:2] for where in state.load(demo)["orders"]["o1"]["asked_in"]] == [["D123", his]]


def test_his_later_words_in_a_thread_go_to_the_order_that_came_into_it_last(demo: str, run: Callable[..., tuple[int, str]]) -> None:
    thread = "1794200000.000100"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the 50 dashboard improvements")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "how many incidents are open", "--thread", thread)
    with state.edit(demo) as data:
        created = state.parse_time(data["orders"]["o2"]["created"])
        data["orders"]["o1"]["asked_in"] = [["D123", thread]]
        data["orders"]["o1"]["last_line"] = state.stamp(created + timedelta(minutes=3))
    assert state.load(demo)["orders"]["o1"]["asked_in"] == [["D123", thread, state.stamp(created + timedelta(minutes=3))]]
    assert state.thread_order(state.load(demo), thread) == "o1"
    with state.edit(demo) as data:
        data["orders"]["o2"]["created"] = state.stamp(created + timedelta(minutes=5))
    assert state.thread_order(state.load(demo), thread) == "o2"
    assert state.thread_order(state.load(demo), "1794100000.000100") is None
