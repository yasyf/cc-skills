import json
import subprocess
from datetime import timedelta

import pytest
from drive import page, state, view, watch
from drive.render import when

T0 = state.parse_time("2026-11-09T10:00:00-08:00")
TEAM = "https://acme.slack.com/"
DRAW = page.draw
DASHBOARD = "http://mac:41234/"


def at(minutes: int) -> str:
    return state.stamp(T0 + timedelta(minutes=minutes))


def order(text: str, jobs: list[str], **overrides: object) -> dict:
    base = {
        "text": text,
        "from": "terminal",
        "from_thread": None,
        "thread": "1794200000.000100",
        "state": "working",
        "due": None,
        "due_said": None,
        "next_due": None,
        "next_set": None,
        "kept": None,
        "withdrawn": None,
        "jobs": jobs,
        "incident": False,
        "recovered": None,
        "proof": None,
        "note": None,
        "created": at(-120),
        "updated": at(-60),
        "last_line": None,
        "project": None,
        "workstream": None,
        "parts": {text: state.new_part()},
    }
    return base | overrides


def job(order_id: str, job_state: str, said: str | None = None, updated: int = -30, **overrides: object) -> dict:
    base = {"order": order_id, "kind": "builder", "goal": "g", "prs": [], "state": job_state, "said": said, "updated": at(updated)}
    return base | {"branch": None, "waits": [], "questions": [], "parts": [], "carries": {}} | overrides


def asked_on(*cards: str) -> list[dict]:
    return [state.new_question(None, None, at(-30), card=card) for card in cards]


def pr(number: int, order_id: str, **overrides: object) -> tuple[str, dict]:
    url = f"https://github.com/yasyf/cc-skills/pull/{number}"
    entry = state.new_pr(url, "j1", order_id) | {"wants_tap": False, "title": f"New: \U0001f680 change {number}", "updated": at(-number)}
    return f"yasyf/cc-skills#{number}", entry | {"checks": "pass", "review": "approved"} | overrides


def drive_of(orders: dict, jobs: dict, prs: list[tuple[str, dict]]) -> dict:
    data = state.new_drive("demo", "coordinator", "s1", "/work", "D123")
    data.update(orders=orders, jobs=jobs, prs=dict(prs))
    return data


def found(questions: list[dict] = (), pr_cards: list[dict] = ()) -> dict:
    return {"team_url": TEAM, "questions": list(questions), "pr_cards": list(pr_cards)}


def question(title: str, minutes: int, waits: str | None = None) -> dict:
    drawn = {"title": title, "body": "Why it matters", "options": ["Yes", "No"], "recommended": "Yes", "waits": waits}
    return {"question": title, "options": ["Yes", "No"], "recommended": "Yes", "asked": at(minutes), "url": f"{TEAM}archives/D123/p{minutes}", "card": drawn}


def test_queue_puts_questions_then_ready_taps_then_late_promises() -> None:
    data = drive_of(
        {
            "o1": order("ship the page", ["j1"]),
            "o2": order("promised earlier", ["j2"], due=at(-90)),
            "o3": order("promised later", ["j3"], due=at(-10)),
            "o4": order("not due yet", ["j4"], due=at(30)),
            "o5": order("done late", ["j5"], state="done", due=at(-90)),
        },
        {f"j{n}": job(f"o{n}", "waiting") for n in range(1, 6)},
        [
            pr(1, "o1", wants_tap=True),
            pr(2, "o1", wants_tap=True, checks="fail"),
            pr(3, "o1", wants_tap=True, checks="pending"),
            pr(4, "o1", wants_tap=True, tap={"label": "Hold", "at": at(-5), "head": "", "source": "pressed"}),
            pr(5, "o1", wants_tap=True, exempt="test-only"),
            pr(6, "o1", wants_tap=False),
            pr(7, "o1", wants_tap=True, fundamental="now deletes the old page"),
            pr(8, "o1", wants_tap=True, hold_label=True),
        ],
    )
    drawn = {"title": "Fix: approve change 3?", "options": ["Approve", "Hold"], "prs": [{"url": "https://github.com/yasyf/cc-skills/pull/3", "refresher": "One key"}]}
    card = {"prs": ["https://github.com/yasyf/cc-skills/pull/3"], "asked": at(-50), "url": f"{TEAM}archives/D123/p3", "card": drawn}
    queue = view.build(data, found([question("Release now?", -20)], [card]), T0)["queue"]
    assert [(item["kind"], item["title"]) for item in queue] == [
        ("question", "Release now?"),
        ("tap", "change 3"),
        ("tap", "change 1"),
        ("tap", "change 7"),
        ("late", "promised earlier"),
        ("late", "promised later"),
    ]
    assert queue[0]["detail"] == "Recommended: Yes"
    assert (queue[0]["card"]["body"], queue[0]["answer"]) == ("Why it matters", f"{TEAM}archives/D123/p-20")
    assert queue[1]["url"] == card["url"]
    assert (queue[1]["card"], queue[1]["answer"]) == (drawn, card["url"])
    assert "answer" not in queue[2]
    assert queue[1]["links"] == [{"label": "Pull request", "url": "https://github.com/yasyf/cc-skills/pull/3"}]
    assert (queue[2]["url"], queue[2]["since"], queue[2]["links"]) == ("https://github.com/yasyf/cc-skills/pull/1", None, [])
    assert queue[3]["detail"] == "green, approved; changed since your tap: now deletes the old page; no Slack card yet"
    assert queue[4]["url"] == f"{TEAM}archives/D123/p1794200000000100"


@pytest.mark.parametrize(
    ("order_state", "jobs", "prs", "expected"),
    [
        ("working", {"j1": "running"}, {}, "running"),
        ("working", {"j1": "running"}, {"checks": "fail"}, "running"),
        ("waiting", {"j1": "waiting"}, {"checks": "fail"}, "blocked"),
        ("working", {"j1": "failed", "j2": "running"}, {}, "blocked"),
        ("waiting", {"j1": "waiting"}, {"state": "merged"}, "merged"),
        ("waiting", {"j1": "built", "j2": "waiting"}, {}, "built"),
        ("waiting", {"j1": "waiting"}, {}, "waiting"),
        ("open", {}, {}, "not started"),
        ("done", {"j1": "done"}, {"state": "merged"}, "live"),
    ],
)
def test_each_feature_lands_in_one_state(order_state: str, jobs: dict, prs: dict, expected: str) -> None:
    data = drive_of(
        {"o1": order("the feature", list(jobs), state=order_state)},
        {job_id: job("o1", job_state) for job_id, job_state in jobs.items()},
        [pr(1, "o1", **prs)] if prs else [],
    )
    groups = {group["state"]: [entry["title"] for entry in group["features"]] for group in view.build(data, found(), T0)["status"]}
    assert groups == {name: ["the feature"] if name == expected else [] for name in view.STATES}


def test_status_drops_dropped_orders_and_old_done_ones_and_says_the_latest_line_without_ids() -> None:
    data = drive_of(
        {
            "o1": order("<https://x.test|the page> lands today\nmore words", ["j1", "j2"], due=at(-5)),
            "o2": order("old news", [], state="done", updated=state.stamp(T0 - timedelta(days=2))),
            "o3": order("never mind", [], state="dropped"),
            "o4": order("shipped", [], state="done", proof="installed 1.0.12; the page shows the drive"),
        },
        {"j1": job("o1", "running", "older line", updated=-50), "j2": job("o1", "waiting", "CI runs (m2129). Lands next.", updated=-5)},
        [],
    )
    model = view.build(data, found(), T0)
    features = {entry["title"]: entry for group in model["status"] for entry in group["features"]}
    assert set(features) == {"the page lands today", "shipped"}
    assert features["the page lands today"]["now"] == "CI runs. Lands next."
    assert features["the page lands today"]["late"] is True
    assert features["shipped"]["now"] == "installed 1.0.12; the page shows the drive"
    assert model["counts"] == {"queue": 1, "blocked": 0, "running": 1, "built": 0, "waiting": 0, "merged": 0, "live": 1, "not started": 0}


def test_a_built_job_shows_its_branch_and_the_cards_it_waits_on_in_status_and_on_the_page() -> None:
    data = drive_of(
        {"o1": order("rank the cards", ["j1"])},
        {"j1": job("o1", "built", "BUILT lane/built", branch="lane/built", waits=["demo:j1-7-a"])},
        [],
    )
    asked = question("Which layout?", -20, "demo:j1-7-a")
    model = view.build(data, found([asked]), T0)
    [entry] = next(group["features"] for group in model["status"] if group["state"] == "built")
    assert entry["built"] == [{"job": "j1", "branch": "lane/built", "waits": [{"title": "Which layout?", "url": asked["url"]}]}]
    markdown = page.render(model, DASHBOARD)
    assert "## Built, waiting on your design answers (1)" in markdown
    assert f"built on `lane/built`, no PR yet · waits on your answer to [Which layout?]({asked['url']}), asked {when(asked['asked'])}" in markdown


def test_each_feature_names_the_open_cards_of_his_that_block_it_and_each_card_the_features_it_blocks() -> None:
    data = drive_of(
        {"o1": order("rank the cards", ["j1", "j2"]), "o2": order("ship the page", ["j3"]), "o3": order("free to go", ["j4"])},
        {
            "j1": job("o1", "waiting", questions=asked_on(f"{TEAM}archives/D123/p-40")),
            "j2": job("o1", "built", branch="lane/built", waits=["demo:j2-7-a", "j1"]),
            "j3": job("o2", "waiting"),
            "j4": job("o3", "waiting", questions=asked_on(f"{TEAM}archives/D123/p-90")),
        },
        [pr(1, "o2", waits=["demo:j2-7-a"]), pr(2, "o3", waits=["demo:j9-1-a"], state="merged")],
    )
    layout, rename, other = question("Which layout?", -20, "demo:j2-7-a"), question("Rename it?", -40), question("Release now?", -10)
    model = view.build(data, found([layout, rename, other]), T0)
    features = {entry["title"]: entry for group in model["status"] for entry in group["features"]}
    assert features["rank the cards"]["cards"] == [
        {"title": "Which layout?", "url": layout["url"], "asked": layout["asked"]},
        {"title": "Rename it?", "url": rename["url"], "asked": rename["asked"]},
    ]
    assert features["ship the page"]["cards"] == [{"title": "Which layout?", "url": layout["url"], "asked": layout["asked"]}]
    assert features["free to go"]["cards"] == []
    assert {item["title"]: item["blocks"] for item in model["queue"]} == {
        "Which layout?": [{"id": "o1", "title": "rank the cards"}, {"id": "o2", "title": "ship the page"}],
        "Rename it?": [{"id": "o1", "title": "rank the cards"}],
        "Release now?": [],
    }
    markdown = page.render(model, DASHBOARD)
    assert f"waits on your answer to [Which layout?]({layout['url']}), asked {when(layout['asked'])}; [Rename it?]({rename['url']})" in markdown
    assert "· blocks rank the cards; ship the page ·" in markdown


def test_a_question_cards_rail_names_the_job_that_asked_it_what_it_holds_up_and_his_answers_matched_to_it_newest_first() -> None:
    layout, rename = question("Which layout?", -40, "demo:j2-7-a"), question("Rename it?", -20, "demo:j3-2-a")
    data = drive_of(
        {"o1": order("rank the cards", ["j1", "j2"]), "o2": order("ship the page", ["j3"])},
        {
            "j1": job("o1", "waiting", goal="read the ranking", questions=asked_on(layout["url"]), brief="/tmp/brief.md"),
            "j2": job("o1", "built", goal="rank by age", branch="lane/age", waits=["demo:j2-7-a"]),
            "j3": job("o2", "running", goal="rank by size"),
        },
        [pr(1, "o2", waits=["demo:j2-7-a"]), pr(2, "o2", waits=["demo:j2-7-a"], checks="fail")],
    )
    said = {"link": f"{TEAM}archives/D123/p1", "said": at(-90), "jobs": {}}
    data["answers"] = {
        "a1": said | {"words": "older words", "at": at(-90), "cards": {"demo:j2-7-a": None}},
        "a2": said | {"words": "newer words", "at": at(-60), "cards": {"demo:j2-7-a": {"mark": "stands"}}},
        "a3": said | {"words": "other words", "at": at(-50), "cards": {"demo:j3-2-a": None}},
    }
    model = view.board(data, found([layout, rename]), "live", [], T0, set())
    items = {item["title"]: item for item in model["queue"]}
    assert {title: (item["job"], item["holds"]) for title, item in items.items()} == {"Which layout?": ("j1", 2), "Rename it?": ("j3", 0)}
    rail = items["Which layout?"]["rail"]
    assert rail["held"] == [
        {"kind": "pr", "feature": "o2", "title": view.pr_title(data["prs"]["yasyf/cc-skills#1"]), "url": "https://github.com/yasyf/cc-skills/pull/1"},
        {"kind": "job", "feature": "o1", "title": "rank by age", "branch": "lane/age"},
    ]
    assert (rail["asker"]["id"], rail["asker"]["goal"], rail["asker"]["state"], rail["asker"]["files"]) == ("j1", "read the ranking", "waiting", ["brief"])
    assert rail["answers"] == [{"words": "newer words", "at": at(-60), "mark": "stands"}, {"words": "older words", "at": at(-90), "mark": None}]
    assert items["Rename it?"]["rail"]["answers"] == [{"words": "other words", "at": at(-50), "mark": None}]
    assert all("rail" not in item for item in view.summary(model)["queue"])


def test_his_question_cards_come_most_finished_work_held_up_first_and_a_pr_waiting_on_one_asks_no_tap() -> None:
    data = drive_of(
        {"o1": order("rank the cards", ["j1", "j2"])},
        {"j1": job("o1", "waiting"), "j2": job("o1", "built", branch="lane/built", waits=["demo:j2-7-b"])},
        [
            pr(1, "o1", waits=["demo:j2-7-b", "demo:j2-7-a"], wants_tap=True),
            pr(2, "o1", waits=[f"{TEAM}archives/D123/p-10"]),
            pr(3, "o1", waits=["demo:j2-7-b"], checks="fail"),
        ],
    )
    questions = [question("Old one?", -30, "demo:j2-7-a"), question("Busy one?", -10, "demo:j2-7-b"), question("Idle one?", -20)]
    model = view.build(data, found(questions), T0)
    assert [(item["title"], item["holds"]) for item in model["queue"]] == [("Busy one?", 3), ("Old one?", 1), ("Idle one?", 0)]
    markdown = page.render(model, DASHBOARD)
    assert (
        "1. **Built, paused on your pick:** [Busy one?](https://acme.slack.com/archives/D123/p-10)"
        " · holds up 3 finished · blocks rank the cards · since"
    ) in markdown
    assert "3. **Question:** [Idle one?](https://acme.slack.com/archives/D123/p-20) · since" in markdown


def test_a_card_holding_a_built_job_leads_the_queue_however_much_else_another_card_holds() -> None:
    data = drive_of(
        {"o1": order("rank the cards", ["j1", "j2"])},
        {"j1": job("o1", "built", branch="lane/built", waits=["demo:j1-7-a"]), "j2": job("o1", "waiting")},
        [pr(1, "o1", waits=["demo:j2-7-b"]), pr(2, "o1", waits=["demo:j2-7-b"])],
    )
    questions = [question("Busy one?", -10, "demo:j2-7-b"), question("Built one?", -30, "demo:j1-7-a")]
    model = view.build(data, found(questions), T0)
    assert [(item["title"], item["holds"], item["built"]) for item in model["queue"]] == [
        ("Built one?", 1, [{"job": "j1", "branch": "lane/built"}]),
        ("Busy one?", 2, []),
    ]
    assert [(entry["wait"], entry["job"], entry["order"]) for entry in view.waiting_on_him(data, found(questions))] == [("demo:j1-7-a", "j1", "o1")]
    markdown = page.render(model, DASHBOARD)
    assert "1. **Built, paused on your pick:** [Built one?](https://acme.slack.com/archives/D123/p-30) · holds up 1 finished" in markdown
    assert "2. **Question:** [Busy one?](https://acme.slack.com/archives/D123/p-10) · holds up 2 finished" in markdown


def test_a_small_drive_draws_whole_queue_first_then_one_line_per_feature_and_the_dashboard_last() -> None:
    data = drive_of(
        {
            "o1": order("ship the page", ["j1"]),
            "o2": order("wait for CI", ["j2"]),
            "o3": order("shipped", [], state="done", proof="installed 1.0.12", updated=at(-5)),
        },
        {"j1": job("o1", "running", "drawing it"), "j2": job("o2", "waiting", "CI runs")},
        [pr(1, "o1", wants_tap=True), pr(2, "o2", state="merged"), pr(3, "o2", checks="fail", wants_tap=False)],
    )
    markdown = page.render(view.build(data, found([question("Release now?", -20)]), T0), DASHBOARD)
    head, _, rest = markdown.partition("## Running (1)")
    assert "## Waiting on you" in head
    assert "1. **Question:** [Release now?]" in head
    assert "2. **Your tap:** [change 1](https://github.com/yasyf/cc-skills/pull/1)" in head
    assert "- [ship the page](https://acme.slack.com/archives/D123/p1794200000000100) · drawing it · :large_green_circle: [#1](https://github.com/yasyf/cc-skills/pull/1)" in rest
    assert "## Blocked (1)" in head
    assert "· CI runs · :red_circle: [#3](https://github.com/yasyf/cc-skills/pull/3) 1 merged" in head
    assert "## Finished (1 live in the last day)" in rest
    assert "- [shipped](https://acme.slack.com/archives/D123/p1794200000000100) · live · installed 1.0.12" in rest
    assert "Left out" not in markdown
    assert markdown.endswith(f"The [dashboard]({DASHBOARD}) has everything else.\n")


def test_page_says_when_nothing_waits_on_him() -> None:
    data = drive_of({}, {}, [])
    assert "Nothing waits on you." in page.render(view.build(data, found(), T0), DASHBOARD)


def big_drive(orders: int, jobs: int, questions: int, taps: int, late: int) -> tuple[dict, dict]:
    states = ("working", "waiting", "done", "open")
    job_states = ("running", "waiting", "done", "failed")
    words = "Clean up the stale worktree records that keep the release lane far too slow to use every morning"
    drive_orders = {
        f"o{n}": order(
            f"{n}: {words}",
            [f"j{n}", f"j{n + orders}"],
            state=states[n % 4],
            due=at(-30) if n < late else None,
            note="Waiting on CI for the second PR, then the install on this Mac and a live check of the page " * 2,
            updated=at(-n),
        )
        for n in range(orders)
    }
    drive_jobs = {
        f"j{n}": job(f"o{n % orders}", job_states[n % 4], "PR open, CI running on the head, the review bots read it next; lands after his tap " * 2)
        for n in range(jobs)
    }
    prs = [pr(n, f"o{n % orders}", wants_tap=n < taps, state="open" if n < taps or n % 3 else "merged", title=f"New: {words} {n}") for n in range(1, orders)]
    asked = [
        question(f"{words}, now or after the release cut tonight? {n}", -n) | {"url": f"{TEAM}archives/D0C4HQWPLCC/p17942{n:08d}?thread_ts=1794224280.936909&cid=D0C4HQWPLCC"}
        for n in range(questions)
    ]
    return drive_of(drive_orders, drive_jobs, prs), found(asked)


def test_a_drive_of_500_orders_and_1000_jobs_fits_and_still_shows_everything_that_waits_on_him() -> None:
    data, cards = big_drive(orders=500, jobs=1000, questions=60, taps=30, late=15)
    model = view.build(data, cards, T0)
    markdown = page.render(model, DASHBOARD)
    assert page.size(markdown) <= page.PAGE_BYTES
    assert len(model["queue"]) == 100
    assert all(page.queue_line(number, item) in markdown for number, item in enumerate(model["queue"], 1))
    assert "more waiting on you" not in markdown
    assert "Left out to fit Slack's canvas limit: " in markdown
    assert markdown.endswith(f"The [dashboard]({DASHBOARD}) has everything else.\n")


def test_a_queue_too_long_for_the_page_names_how_many_more_wait_on_him() -> None:
    data, cards = big_drive(orders=500, jobs=1000, questions=400, taps=30, late=15)
    model = view.build(data, cards, T0)
    markdown = page.render(model, DASHBOARD)
    lines = [page.queue_line(number, item) for number, item in enumerate(model["queue"], 1)]
    shown = sum(line in markdown for line in lines)
    assert page.size(markdown) <= page.PAGE_BYTES
    assert f"Left out to fit Slack's canvas limit: {len(lines) - shown} more waiting on you, " in markdown
    assert all(line in markdown for line in lines[:shown])
    assert "## Blocked" not in markdown


def test_a_page_over_the_limit_leaves_out_its_lowest_priority_section_first(monkeypatch: pytest.MonkeyPatch) -> None:
    data, cards = big_drive(orders=20, jobs=40, questions=2, taps=1, late=1)
    model = view.build(data, cards, T0)
    whole = page.render(model, DASHBOARD)
    assert "## Finished (" in whole
    monkeypatch.setattr(page, "PAGE_BYTES", page.size(whole) - 1)
    markdown = page.render(model, DASHBOARD)
    finished = model["counts"]["merged"] + model["counts"]["live"]
    assert "## Finished (" not in markdown
    assert all(f"## {page.GROUP_TITLES[name]} (" in markdown for name in page.OPEN_STATES if model["counts"][name])
    assert f"Left out to fit Slack's canvas limit: {finished} finished." in markdown


def test_watch_redraws_the_page_only_when_the_model_changes(demo: str, drawn: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch.github, "fetch", lambda keys: ({}, None))
    watching = watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0)
    watch.run_pass(watching, T0)
    watch.run_pass(watching, T0 + timedelta(seconds=60))
    assert len(drawn) == 1
    with state.edit(demo) as data:
        data["orders"]["o1"] = order("a new order", [])
    watch.run_pass(watching, T0 + timedelta(seconds=120))
    assert len(drawn) == 2
    assert "a new order" in drawn[-1]


def test_a_refused_draw_is_said_once_across_restarts_and_once_when_slack_takes_a_redraw_again(
    demo: str, drawn: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watch.github, "fetch", lambda keys: ({}, None))
    accept = page.draw

    def refused(name: str, markdown: str) -> page.Drawn:
        raise page.PageError("Slack refused the redraw (canvas_too_large)")

    monkeypatch.setattr(page, "draw", refused)
    watching = watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0)
    assert watch.run_pass(watching, T0) == [f"WATCH status page stale since {when(state.stamp(T0))}: Slack refused the redraw (canvas_too_large)"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=10)) == []
    assert watch.run_pass(watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0), T0 + timedelta(minutes=40)) == []
    monkeypatch.setattr(page, "draw", accept)
    redrawn = f"WATCH status page redrawn: Slack takes its redraws again, after refusing them since {when(state.stamp(T0))}"
    assert watch.run_pass(watching, T0 + timedelta(minutes=50)) == [redrawn]
    assert watch.run_pass(watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0), T0 + timedelta(minutes=60)) == []
    assert len(drawn) == 2


def test_a_page_slack_calls_full_moves_to_a_new_tab_and_the_watch_says_where(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch.github, "fetch", lambda keys: ({}, None))
    monkeypatch.setattr(page, "draw", DRAW)
    answers = iter(["F1", "F1", "F2"])

    def canvas(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        canvas_id = next(answers)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"canvas_id": canvas_id, "url": f"https://slack.example/docs/T1/{canvas_id}"}), "")

    monkeypatch.setattr(page.subprocess, "run", canvas)
    watching = watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0)
    assert watch.run_pass(watching, T0) == []
    for text, minutes in (("a new order", 10), ("another order", 20)):
        with state.edit(demo) as data:
            data["orders"][f"o{minutes}"] = order(text, [])
        said = watch.run_pass(watching, T0 + timedelta(minutes=minutes))
    assert [line for line in said if line.startswith("WATCH status page")] == ["WATCH status page redrawn on a new tab: https://slack.example/docs/T1/F2"]
    assert state.load(demo)["slack"]["page"] == "F2"


def test_board_adds_progress_work_and_each_features_order_and_jobs() -> None:
    data = drive_of(
        {
            "o1": order("ship the page\nwith its tests", ["j1", "j2"], project="The drive", workstream="Dashboard"),
            "o2": order("old news", ["j3"], state="done", updated=state.stamp(T0 - timedelta(days=2))),
        },
        {"j1": job("o1", "running", "drawing it", updated=-50), "j2": job("o1", "waiting", updated=-5), "j3": job("o2", "running")},
        [pr(1, "o1", checks="fail"), pr(2, "o1", state="merged")],
    )
    model = view.board(data, found(), "live", [], T0, set())
    assert model["progress"] == {"prs": 2, "merged": 1, "open": 1, "red": 1, "running": 2}
    assert [(entry["id"], entry["state"]) for entry in model["work"]] == [("j1", "running"), ("j2", "waiting")]
    entry = next(entry for group in model["status"] for entry in group["features"])
    assert (entry["id"], entry["text"], entry["project"], entry["workstream"]) == ("o1", "ship the page\nwith its tests", "The drive", "Dashboard")
    assert [job_entry["id"] for job_entry in entry["jobs"]] == ["j1", "j2"]


def test_history_lists_every_day_newest_first_with_his_taps_by_title_each_line_sent_him_and_the_swears_in_his_orders() -> None:
    data = drive_of(
        {
            "o1": order("ship the page", ["j1"], created=at(-30), state="done", updated=at(-10), proof="installed 1.0.21"),
            "o2": order("WTF, fix the damn thing. Hello, shell", [], created=state.stamp(T0 - timedelta(days=2)), state="waiting"),
        },
        {"j1": job("o1", "failed", "CI red (m12)", updated=-20)},
        [pr(1, "o1", state="merged", updated=at(-15))],
    )
    events = [
        {"kind": "tap", "at": at(-5), "repo": "yasyf/cc-skills", "number": 1, "label": "Approve"},
        {"kind": "shown", "at": at(-4), "repo": "yasyf/cc-skills", "number": 1, "waits": ""},
        {"kind": "refused", "at": at(-3), "repo": "yasyf/cc-skills", "number": 9, "reason": "its body lacks a section"},
        {"kind": "said", "at": at(-2), "text": "[change 1](https://github.com/yasyf/cc-skills/pull/1) is live", "url": "https://acme.slack.com/archives/D123/p1", "order": "o1", "visuals": []},
    ]
    history = view.board(data, found(), "live", events, T0, set())["history"]
    assert [(entry["kind"], entry["text"], entry["detail"]) for entry in history] == [
        ("said", "[change 1](https://github.com/yasyf/cc-skills/pull/1) is live", ""),
        ("refused", "https://github.com/yasyf/cc-skills/pull/9", "its body lacks a section"),
        ("tap", "change 1", "Approve"),
        ("live", "ship the page", "installed 1.0.21"),
        ("merged", "change 1", ""),
        ("failed", "g", "CI red"),
        ("order", "ship the page", ""),
        ("order", "WTF, fix the damn thing. Hello, shell", ""),
    ]
    assert [entry["swears"] for entry in history if entry["kind"] == "order"] == [0, 2]


def test_a_stack_card_sent_to_him_shows_each_pr_as_it_stands_now() -> None:
    data = drive_of({"o1": order("ship the page", ["j1"])}, {"j1": job("o1", "waiting", "READY")}, [pr(1, "o1", wants_tap=True), pr(2, "o1", state="merged")])
    events = [
        {"kind": "said", "at": at(-2), "text": "land the page", "url": "https://acme.slack.com/archives/D123/p1", "order": "o1", "visuals": [], "prs": ["yasyf/cc-skills#1", "yasyf/cc-skills#2"]},
        {"kind": "said", "at": at(-1), "text": "an older line", "url": "https://acme.slack.com/archives/D123/p2", "order": None, "visuals": []},
    ]
    stacks = [entry["prs"] for entry in view.board(data, found(), "live", events, T0, set())["history"] if entry["kind"] == "said"]
    assert stacks == [[], [view.pr_entry(data["prs"]["yasyf/cc-skills#1"]), view.pr_entry(data["prs"]["yasyf/cc-skills#2"])]]
    assert [(each["state"], each["tap"]) for each in stacks[1]] == [("open", "wanted"), ("merged", None)]


def test_a_stack_card_whose_prs_the_watch_dropped_since_draws_them_by_their_links_instead_of_ending_the_dashboard() -> None:
    data = drive_of({"o1": order("ship the page", ["j1"])}, {"j1": job("o1", "waiting", "READY")}, [])
    sent = {
        "kind": "said",
        "at": "2026-11-11T17:19:24-08:00",
        "text": "Approve is up on the stack card below",
        "thread": "1794440505.483109",
        "url": "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794446363517879?thread_ts=1794440505.483109&cid=D0C4HQWPLCC",
        "card": "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794446363698169",
        "order": None,
        "visuals": [],
        "prs": ["Forge-AI/monorepo#53080", "Forge-AI/monorepo#53096"],
        "edited": False,
    }
    stack = next(entry["prs"] for entry in view.board(data, found(), "live", [sent], T0, set())["history"] if entry["kind"] == "said")
    assert stack == [
        {"title": f"https://github.com/Forge-AI/monorepo/pull/{number}", "url": f"https://github.com/Forge-AI/monorepo/pull/{number}", "state": "dropped", "checks": None, "review": "none", "tap": None}
        for number in (53080, 53096)
    ]


def test_cards_are_read_from_the_file_the_watch_rewrites_only_on_change(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert view.stored_cards(demo) == (view.NO_CARDS, view.UNREAD)
    listed = found([question("Release now?", -20)])
    monkeypatch.setattr(view, "cards", lambda name: listed)
    view.refresh_cards(demo)
    written = state.cards_path(demo).stat().st_mtime_ns
    view.refresh_cards(demo)
    assert state.cards_path(demo).stat().st_mtime_ns == written
    assert view.stored_cards(demo) == (listed, "live")

    def refused(name: str) -> dict:
        raise view.CardsError("not signed in")

    monkeypatch.setattr(view, "cards", refused)
    view.refresh_cards(demo)
    assert view.stored_cards(demo) == (view.NO_CARDS, "unavailable: not signed in")


def test_review_lists_every_open_pr_by_whether_it_waits_on_him_and_carries_its_card_and_its_thread() -> None:
    data = drive_of(
        {"o1": order("ship the page", ["j1"])},
        {"j1": job("o1", "running")},
        [
            pr(1, "o1", wants_tap=True),
            pr(2, "o1", wants_tap=True, checks="fail"),
            pr(3, "o1", wants_tap=True, tap={"label": "Hold", "at": at(-5), "head": "", "source": "pressed"}),
            pr(4, "o1", state="merged"),
        ],
    )
    drawn = {"title": "Fix: approve change 1?", "options": ["Approve", "Hold"], "prs": [{"url": "https://github.com/yasyf/cc-skills/pull/1"}]}
    card = {"prs": ["https://github.com/yasyf/cc-skills/pull/1"], "asked": at(-50), "url": f"{TEAM}archives/D123/p1794200000000100", "card": drawn}
    entries = view.board(data, found(pr_cards=[card]), "live", [], T0, set())["review"]
    assert [(entry["key"], entry["group"], entry["feature_title"]) for entry in entries] == [
        ("yasyf/cc-skills#1", "you", "ship the page"),
        ("yasyf/cc-skills#2", "not ready", "ship the page"),
        ("yasyf/cc-skills#3", "decided", "ship the page"),
    ]
    assert (entries[0]["card"], entries[0]["answer"], entries[0]["since"], entries[0]["thread"]) == (drawn, card["url"], card["asked"], "1794200000.000100")
    assert all(entry["card"] is None and entry["thread"] is None for entry in entries[1:])


def test_a_pr_card_carrying_a_stack_is_one_queue_item_and_each_card_less_pr_is_its_own() -> None:
    data = drive_of({"o1": order("ship the page", ["j1"])}, {"j1": job("o1", "running")}, [pr(n, "o1", wants_tap=True) for n in (1, 2, 3)])
    urls = [f"https://github.com/yasyf/cc-skills/pull/{n}" for n in (1, 2)]
    card = {"prs": urls, "asked": at(-50), "url": f"{TEAM}archives/D123/p1794200000000100", "card": {"title": "Fix: approve the stack?", "options": ["Approve", "Hold"], "prs": [{"url": url} for url in urls]}}
    queue = view.build(data, found(pr_cards=[card]), T0)["queue"]
    assert [(item["kind"], item.get("answer"), item["url"]) for item in queue] == [
        ("tap", card["url"], card["url"]),
        ("tap", None, "https://github.com/yasyf/cc-skills/pull/3"),
    ]


def test_a_card_posted_inside_a_thread_reads_that_thread_on_the_queue_and_in_review() -> None:
    data = drive_of({"o1": order("ship the page", ["j1"])}, {"j1": job("o1", "running")}, [pr(1, "o1", wants_tap=True)])
    drawn = {"title": "Fix: approve change 1?", "options": ["Approve", "Hold"], "prs": [{"url": "https://github.com/yasyf/cc-skills/pull/1"}]}
    tap = {"prs": ["https://github.com/yasyf/cc-skills/pull/1"], "asked": at(-50), "url": f"{TEAM}archives/D123/p1794200000000100?thread_ts=1794100000.000200&cid=D123", "card": drawn}
    asked = {**question("Release now?", -20), "url": f"{TEAM}archives/D123/p1794300000000300"}
    model = view.board(data, found([asked], [tap]), "live", [], T0, set())
    assert [(item["kind"], item["thread"]) for item in model["queue"]] == [("question", "1794300000.000300"), ("tap", "1794100000.000200")]
    assert model["review"][0]["thread"] == "1794100000.000200"


def test_incidents_list_his_open_incident_orders_oldest_first_but_one_the_watch_found_over() -> None:
    data = drive_of(
        {
            "o1": order("runs fail for one team", [], incident=True, created=at(-30)),
            "o2": order("ship the dashboard", []),
            "o3": order("the old journal copy broke", [], incident=True, state="done"),
            "o4": order("Sand usage events set aside", [], incident=True, created=at(-90)),
            "o5": order("replication lag on one cluster", [], incident=True, created=at(-60), recovered=at(-1)),
        },
        {},
        [],
    )
    assert [(entry["id"], entry["title"]) for entry in view.board(data, found(), "live", [], T0, set())["incidents"]] == [
        ("o4", "Sand usage events set aside"),
        ("o1", "runs fail for one team"),
    ]


def test_the_dashboard_model_carries_no_plan() -> None:
    data = drive_of({"o1": order("ship the plan", [])}, {}, [])
    assert not {"milestones", "unplanned"} & set(view.board(data, found(), "live", [], T0, set()))
    assert "milestone" not in view.board(data, found(), "live", [], T0, set())["status"][-1]["features"][0]


def test_the_summary_every_page_is_sent_leaves_out_history_lanes_and_each_features_words_and_jobs_and_shows_each_card_at_a_glance() -> None:
    data = drive_of({"o1": order("ship the page\nwith its tests", ["j1", "j2"])}, {"j1": job("o1", "running"), "j2": job("o1", "done")}, [])
    shown = view.summary(view.board(data, found([question("Raise the limit?", -30)]), "live", [], T0, set()))
    [entry] = [entry for group in shown["status"] for entry in group["features"]]
    assert not {"history", "work"} & set(shown)
    assert (entry["title"], "text" in entry, "jobs" in entry, entry["running"]) == ("ship the page", False, False, 1)
    assert shown["queue"][0]["card"] == {"title": "Raise the limit?", "options": ["Yes", "No"], "recommended": "Yes", "prs": []}


def test_history_pages_eighty_moments_newest_first_of_a_pacific_day_or_every_day_kept_to_the_kinds_he_picked() -> None:
    merged = {"at": "2026-11-10T07:30:00Z", "kind": "merged"}
    said = [{"at": state.stamp(state.parse_time("2026-11-09T22:00:00-08:00") - timedelta(minutes=n)), "kind": "said"} for n in range(85)]
    asked = {"at": "2026-11-08T20:00:00-08:00", "kind": "order", "swears": 2}
    model = {"at": "2026-11-11T10:00:00-08:00", "history": [merged, *said, asked]}
    every = view.history_page(model, None, "all", 1)
    assert [(each["day"], each["merged"], each["swears"], each["asked"], each["moments"]) for each in every["days"]] == [
        ("2026-11-08", 0, 2, 1, 1),
        ("2026-11-09", 1, 0, 0, 86),
        ("2026-11-10", 0, 0, 0, 0),
        ("2026-11-11", 0, 0, 0, 0),
    ]
    assert (every["day"], every["moments"], every["pages"]) == (None, [merged, *said[:79]], 2)
    assert view.history_page(model, "2026-11-09", "said", 2) | {"days": []} == {"days": [], "day": "2026-11-09", "moments": said[80:], "page": 2, "pages": 2}
    assert view.history_page(model, "2026-11-12", "prs", 1)["moments"] == [merged]


def test_the_live_story_is_the_newest_six_orders_launches_merges_and_taps_of_the_features_in_view() -> None:
    history = [{"kind": kind, "feature": feature} for kind, feature in [("said", "o1"), ("merged", "o2"), ("failed", "o1"), *[("tap", "o1")] * 7]]
    assert view.story({"history": history}, {"o1"}) == [{"kind": "tap", "feature": "o1"}] * 6
    assert view.story({"history": history}, None)[0] == {"kind": "merged", "feature": "o2"}


def test_an_alert_he_shared_is_titled_by_its_source_and_words_not_its_link() -> None:
    data = drive_of(
        {
            "o1": order("https://x.slack.com/archives/C1/p1794371947314469 Shared from Datadog in <#C0822AHFY3G>: SandSQL Error Log Count — plat", [], incident=True),
            "o2": order("https://x.slack.com/archives/C1/p1794372156644299 fix it (Shared from PlatClaude: mint endpoint failing)", [], incident=True, created=at(10)),
            "o3": order("https://x.slack.com/archives/C1/p1794372156644299", [], incident=True, created=at(20)),
        },
        {},
        [],
    )
    assert [entry["title"] for entry in view.board(data, found(), "live", [], T0, set())["incidents"]] == [
        "Datadog: SandSQL Error Log Count — plat",
        "fix it (Shared from PlatClaude: mint endpoint failing)",
        "https://x.slack.com/archives/C1/p1794372156644299",
    ]


QUESTION = "https://acme.slack.com/archives/D123/p1794200000000100"
UNLISTED = "https://acme.slack.com/archives/D123/p1794200000000900"


@pytest.mark.parametrize(
    ("wait", "words"),
    [
        ("j3", "the build of prove the real run finishes"),
        ("Forge-AI/monorepo#7", "[retry (flaky) uploads](https://github.com/Forge-AI/monorepo/pull/7) lands"),
        ("release:dashboard:Forge-AI/monorepo#7", "the dashboard release carrying [retry (flaky) uploads](https://github.com/Forge-AI/monorepo/pull/7) is out"),
        ("demo:j1-7-a", f"you answer [Pick a store?]({QUESTION}?thread_ts=1.2)"),
        (UNLISTED, f"you answer [your question card]({UNLISTED})"),
        ("demo:j1-9", "you answer your question card"),
    ],
)
def test_each_wait_reads_on_his_card_as_what_it_waits_for_in_linked_words(wait: str, words: str) -> None:
    data = {"jobs": {"j3": {"goal": "prove the real run"}}, "prs": {"Forge-AI/monorepo#7": {"title": "Fix: retry [flaky] uploads", "url": "https://github.com/Forge-AI/monorepo/pull/7"}}}
    listed = {"team_url": "", "pr_cards": [], "questions": [{"question": "Pick a store?", "url": f"{QUESTION}?thread_ts=1.2", "card": {"title": "Pick a store?", "waits": "demo:j1-7-a"}}]}
    assert view.wait_words(data, wait, listed) == words
