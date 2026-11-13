import json
import subprocess
from collections import Counter
from datetime import timedelta

import pytest
from drive import land, page, stage, state, view, watch
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
        "thread": ["D123", "1794200000.000100"],
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
        "reporter": None,
        "kind": "build",
        "proof": None,
        "note": None,
        "created": at(-120),
        "updated": at(-60),
        "last_line": None,
        "project": None,
        "workstream": None,
        "title": None,
        "kind": "build",
        "options_only": False,
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
    entry |= {"checks": "pass", "review": "approved"} | overrides
    asks = entry["state"] == "open" and stage.carded(entry) and entry["checks"] == "pass" and not (entry["tap"] or entry["hold_label"] or entry["waits"])
    drawn = {"stage": {"name": "card", "owner": stage.HIM, "why": "green, waits on his Approve", "since": at(-number), "woke": None}, "card": entry["card"] or f"{TEAM}archives/D123/p{number}"}
    kept = drawn if asks and "stage" not in overrides else {}
    return f"yasyf/cc-skills#{number}", entry | kept


def drive_of(orders: dict, jobs: dict, prs: list[tuple[str, dict]]) -> dict:
    data = state.new_drive("demo", "coordinator", "s1", "/work", "D123")
    data.update(orders=orders, jobs=jobs, prs=dict(prs))
    return data


def found(questions: list[dict] = (), pr_cards: list[dict] = (), closed: list[dict] = ()) -> dict:
    return {"team_url": TEAM, "questions": list(questions), "pr_cards": list(pr_cards), "closed": list(closed)}


def question(title: str, minutes: int, waits: str | None = None) -> dict:
    drawn = {"title": title, "body": "Why it matters", "options": ["Yes", "No"], "recommended": "Yes", "waits": waits}
    return {"question": title, "options": ["Yes", "No"], "recommended": "Yes", "asked": at(minutes), "url": f"{TEAM}archives/D123/p{minutes}", "card": drawn}


def test_board_reuses_unchanged_text_and_redraws_changed_words(monkeypatch: pytest.MonkeyPatch) -> None:
    data = drive_of(
        {f"o{n}": order(f"Ship feature {n} (cci {123456 + n}). Keep its words.", [f"j{n}"], proof=f"Delivered feature {n} (j{n}).", state="done") for n in range(64)},
        {f"j{n}": job(f"o{n}", "failed", said=f"Failed feature {n} (cci {123456 + n}). Retry it.") for n in range(64)},
        [],
    )
    plain, said = view.plain, view.said
    plain.cache_clear()
    said.cache_clear()

    def uncached() -> dict:
        with monkeypatch.context() as patch:
            patch.setattr(view, "plain", plain.__wrapped__)
            patch.setattr(view, "said", said.__wrapped__)
            return view.board(data, found(), "live", [], T0, set(), {})

    try:
        first = view.board(data, found(), "live", [], T0, set(), {})
        assert first == uncached()
        misses = (plain.cache_info().misses, said.cache_info().misses)
        assert all(misses)
        assert view.board(data, found(), "live", [], T0, set(), {}) == first
        assert (plain.cache_info().misses, said.cache_info().misses) == misses
        data["orders"]["o0"]["text"] = "Changed title (cci 999999)."
        data["jobs"]["j0"]["said"] = "Recovered the changed job (cci 999999)."
        changed = view.board(data, found(), "live", [], T0, set(), {})
        assert changed != first
        assert changed == uncached()
        assert plain.cache_info().misses == misses[0] + 1
        assert said.cache_info().misses == misses[1] + 1
    finally:
        plain.cache_clear()
        said.cache_clear()


def test_bulk_stages_build_the_stack_graph_once(monkeypatch: pytest.MonkeyPatch) -> None:
    data = drive_of(
        {f"o{n}": order(f"Ship feature {n}", [f"j{n}"]) for n in range(1, 65)},
        {f"j{n}": job(f"o{n}", "waiting") for n in range(1, 65)},
        [pr(n, f"o{n}", job=f"j{n}", branch=f"change/{n}", base=f"change/{n-1}", checks="pending") for n in range(1, 65)],
    )
    keys = list(data["prs"])
    expected = {key: stage.of(data, key, found()) for key in keys}
    unders = land.unders
    scans = 0

    def counted(data: dict) -> dict[str, str | None]:
        nonlocal scans
        scans += 1
        return unders(data)

    monkeypatch.setattr(land, "unders", counted)
    stage.shown(data, found(), keys)
    assert scans == 1
    assert {key: (value["stage"]["name"], value["stage"]["owner"], value["stage"]["why"]) for key, value in data["prs"].items()} == {key: (value.name, value.owner, value.why) for key, value in expected.items()}
    data["prs"][keys[1]]["base"] = "main"
    stage.shown(data, found(), keys)
    assert scans == 2


def test_pr_job_index_preserves_the_complete_board() -> None:
    data = drive_of(
        {f"o{n}": order(f"Ship feature {n}", [f"j{n}"]) for n in range(1, 65)},
        {f"j{n}": job(f"o{n}", "waiting", prs=[f"https://github.com/yasyf/cc-skills/pull/{n}"]) for n in range(1, 65)},
        [pr(n, f"o{n}", job=f"j{n}") for n in range(1, 65)],
    )
    expected = view.board(data, found(), "live", [], T0, set(), {})
    with state.indexed_pr_jobs(data):
        assert view.board(data, found(), "live", [], T0, set(), {}) == expected
    data["jobs"]["j2"]["prs"].append(data["prs"]["yasyf/cc-skills#1"]["url"])
    expected = view.board(data, found(), "live", [], T0, set(), {})
    with state.indexed_pr_jobs(data):
        assert view.board(data, found(), "live", [], T0, set(), {}) == expected


def test_queue_ranks_late_promises_then_what_lands_most_then_the_oldest_wait() -> None:
    data = drive_of(
        {
            "o1": order("ship the page", ["j1"], project="Drive", workstream="Dashboard"),
            "o2": order("promised earlier", ["j2"], due=at(-90)),
            "o3": order("promised later", ["j3"], due=at(-10)),
            "o4": order("not due yet", ["j4"], due=at(30)),
            "o5": order("done late", ["j5"], state="done", due=at(-90)),
        },
        {f"j{n}": job(f"o{n}", "waiting") for n in range(1, 6)},
        [
            pr(1, "o1", wants_tap=True),
            pr(2, "o1", wants_tap=True, checks="fail"),
            pr(3, "o1", wants_tap=True),
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
        ("late", "promised earlier"),
        ("late", "promised later"),
        ("tap", "change 3"),
        ("tap", "change 7"),
        ("tap", "change 1"),
        ("question", "Release now?"),
    ]
    assert [item["priority"]["rank"] for item in queue] == list(range(1, 7))
    assert [item["priority"]["overdue"] for item in queue] == [True, True, False, False, False, False]
    assert [item["priority"]["weight"] for item in queue] == [0, 0, 2, 2, 2, 0]
    assert all(not item["priority"]["incident"] for item in queue)
    assert queue[5]["detail"] == "Recommended: Yes"
    assert (queue[5]["card"]["body"], queue[5]["answer"]) == ("Why it matters", f"{TEAM}archives/D123/p-20")
    assert queue[2]["url"] == card["url"]
    assert (queue[2]["card"], queue[2]["answer"]) == (drawn, card["url"])
    assert "answer" not in queue[4]
    assert queue[2]["links"] == [{"label": "Pull request", "url": "https://github.com/yasyf/cc-skills/pull/3"}]
    assert (queue[4]["url"], queue[4]["since"], queue[4]["links"]) == ("https://github.com/yasyf/cc-skills/pull/1", None, [])
    assert queue[3]["detail"] == "green, approved; changed since your tap: now deletes the old page; card, owed by him: green, waits on his Approve; no Slack card yet"
    assert queue[0]["url"] == f"{TEAM}archives/D123/p1794200000000100"
    assert [item["rank_why"] for item in queue] == [
        f"past due since {when(at(-90))}",
        f"past due since {when(at(-10))}",
        f"lands 1 PR; waiting since {when(at(-50))}",
        f"lands 1 PR; waiting since {when(at(-7))}",
        f"lands 1 PR; waiting since {when(at(-1))}",
        f"waiting since {when(at(-20))}",
    ]
    assert [(item["tasks"], item["project"], item["workstream"]) for item in queue[1:3]] == [(["o3"], None, None), (["o1"], "Drive", "Dashboard")]


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
    assert "## 🟣 Built, waiting on your pick (1)" in markdown
    assert f"| 🟣 [Which layout?]({asked['url']}) | 1 finished; rank the cards |" in markdown
    assert f"BUILT lane/built<br>🟣 built on `lane/built`, no PR yet<br>❓ waits on [Which layout?]({asked['url']})" in markdown


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
    assert f"❓ waits on [Which layout?]({layout['url']})<br>❓ waits on [Rename it?]({rename['url']})" in markdown
    assert "finished; rank the cards; ship the page |" in markdown


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
    model = view.board(data, found([layout, rename]), "live", [], T0, set(), {})
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
    assert "| 🟣 [Busy one?](https://acme.slack.com/archives/D123/p-10) | 3 finished; rank the cards |" in markdown
    assert "| [Idle one?](https://acme.slack.com/archives/D123/p-20) | — |" in markdown
    assert markdown.index("[Busy one?]") < markdown.index("[Old one?]") < markdown.index("[Idle one?]")


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
    assert "| 🟣 [Built one?](https://acme.slack.com/archives/D123/p-30) | 1 finished; rank the cards |" in markdown
    assert "| [Busy one?](https://acme.slack.com/archives/D123/p-10) | 2 finished; rank the cards |" in markdown
    assert markdown.index("[Built one?]") < markdown.index("[Busy one?]")


def test_a_small_drive_draws_its_counts_then_a_table_for_each_part_that_has_rows() -> None:
    data = drive_of(
        {
            "o1": order("ship the page", ["j1"]),
            "o2": order("wait for CI", ["j2"]),
            "o3": order("shipped", [], state="done", proof="installed 1.0.12", updated=at(-5)),
        },
        {"j1": job("o1", "running", "drawing it"), "j2": job("o2", "waiting", "CI runs")},
        [pr(1, "o1", wants_tap=True), pr(2, "o2", state="merged"), pr(3, "o2", checks="fail", wants_tap=False)],
    )
    model = view.build(data, found([question("Release now?", -20)]), T0)
    markdown = page.render(model, DASHBOARD)
    thread = "https://acme.slack.com/archives/D123/p1794200000000100"
    first = "[#1](https://github.com/yasyf/cc-skills/pull/1)"
    assert markdown.startswith(f"Updated {when(model['at'])} · [open the dashboard]({DASHBOARD})\n\n| 🙋 Needs you | 🔴 Blocked | 🔵 Running |")
    assert "| **2** | **1** | **1** | **0** | **0** | **0** | **0** | **1** |" in markdown
    assert [line for line in markdown.splitlines() if line.startswith("## ")] == [
        "## ❓ Your questions (1)",
        "## 👆 PRs waiting on your tap (1)",
        "## 📊 Every project at a glance",
        "## 🔴 Blocked (1)",
        "## 🔵 Running (1)",
        "## ✅ Finished (1)",
    ]
    assert f"| [Release now?](https://acme.slack.com/archives/D123/p-20) | — | {when(at(-20))} | Yes |" in markdown
    assert f"| [change 1](https://github.com/yasyf/cc-skills/pull/1) · {first} | 🟢 green, approved; card, owed by him: green, waits on his Approve; no Slack card yet | — |" in markdown
    assert "| **No project** | 1 | 1 | · | · | · | · | 1 |" in markdown
    assert f"| [wait for CI]({thread}) | — | CI runs | 🔴 [#3](https://github.com/yasyf/cc-skills/pull/3) 1 merged |" in markdown
    assert f"| [ship the page]({thread}) | — | drawing it | 🟢 {first} |" in markdown
    assert f"| [shipped]({thread}) | ✅ live | installed 1.0.12 | {when(at(-5))} |" in markdown
    assert "more on the" not in markdown


def test_an_empty_drive_draws_only_its_counts() -> None:
    model = view.build(drive_of({}, {}, []), found(), T0)
    assert page.render(model, DASHBOARD).splitlines()[1:] == [
        "",
        "| 🙋 Needs you | 🔴 Blocked | 🔵 Running | 🟣 Built, waiting on your pick | ⏳ Waiting | ⚪ Not started | 🟢 Merged, not live yet | ✅ Live in the last day |",
        "|---|---|---|---|---|---|---|---|",
        "| **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** |",
    ]


@pytest.mark.parametrize(
    ("said", "drawn"),
    [
        (
            "READY https://github.com/yasyf/cc-skills/pull/12 https://github.com/yasyf/cc-skills/pull/13",
            "READY [#12](https://github.com/yasyf/cc-skills/pull/12) [#13](https://github.com/yasyf/cc-skills/pull/13)",
        ),
        ("its build https://buildkite.com/forge/release/builds/9.", "its build [buildkite](https://buildkite.com/forge/release/builds/9)."),
        ("cut at https://buildkite.com/fo…", "cut at …"),
        ("kept [#5](https://github.com/yasyf/cc-skills/pull/5) as is", "kept [#5](https://github.com/yasyf/cc-skills/pull/5) as is"),
        ("one | two\nthree", "one / two three"),
    ],
)
def test_a_cell_links_each_bare_url_by_a_short_name_and_keeps_to_one_line(said: str, drawn: str) -> None:
    assert page.cell(said) == drawn


def test_a_feature_names_its_first_four_open_prs_and_counts_the_rest() -> None:
    prs = [{"url": f"https://github.com/yasyf/cc-skills/pull/{n}", "state": "open", "checks": "pass", "tap": None} for n in range(1, 7)]
    prs[1] |= {"checks": "fail", "tap": "Hold"}
    prs.append({"url": "https://github.com/yasyf/cc-skills/pull/7", "state": "merged", "checks": "pass", "tap": None})
    assert page.pr_marks(prs) == (
        "🟢 [#1](https://github.com/yasyf/cc-skills/pull/1) 🔴 [#2](https://github.com/yasyf/cc-skills/pull/2) held by you"
        " 🟢 [#3](https://github.com/yasyf/cc-skills/pull/3) 🟢 [#4](https://github.com/yasyf/cc-skills/pull/4) 2 more open 1 merged"
    )


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
    prs = [pr(n, f"o{n % orders}", wants_tap=n <= taps, state="open" if n <= taps or n % 3 else "merged", title=f"New: {words} {n}") for n in range(1, orders)]
    asked = [
        question(f"{words}, now or after the release cut tonight? {n}", -n) | {"url": f"{TEAM}archives/D0C4HQWPLCC/p17942{n:08d}?thread_ts=1794224280.936909&cid=D0C4HQWPLCC"}
        for n in range(questions)
    ]
    return drive_of(drive_orders, drive_jobs, prs), found(asked)


def test_a_big_drive_draws_every_section_at_most_eight_rows_and_counts_the_rest_on_the_dashboard() -> None:
    data, cards = big_drive(orders=500, jobs=1000, questions=400, taps=30, late=15)
    model = view.build(data, cards, T0)
    tables = page.layout(model, DASHBOARD)
    markdown = page.render(model, DASHBOARD)
    counts, kinds = model["counts"], Counter(item["kind"] for item in model["queue"])
    assert kinds == Counter(question=400, tap=30, late=11)
    assert [table.heading for table in tables] == [
        "",
        "❓ Your questions (400)",
        "👆 PRs waiting on your tap (30)",
        "⏰ Promised times that passed (11)",
        "📊 Every project at a glance",
        *(f"{page.STATE_MARKS[name]} {page.STATE_TITLES[name]} ({counts[name]})" for name in page.OPEN_STATES if counts[name]),
        f"✅ Finished ({counts['merged'] + counts['live']})",
    ]
    assert all(len(table.rows) == min(page.ROWS, table.total) for table in tables)
    assert all((len(table.rows) + 1) * len(table.columns) <= 300 for table in tables)
    assert all(f"*{table.total - page.ROWS} more on the [dashboard]({table.rest})*" in markdown for table in tables if table.total > page.ROWS)
    questions = [item for item in model["queue"] if item["kind"] == "question"]
    assert all(page.link(item["title"], item["url"]) in markdown for item in questions[: page.ROWS])
    assert page.link(questions[page.ROWS]["title"], questions[page.ROWS]["url"]) not in markdown


def test_watch_redraws_the_page_only_when_the_model_changes(demo: str, drawn: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch.github, "fetch", lambda keys: ({}, None))
    watching = watch.Watcher(demo, offset=state.events_end(demo))
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
    watching = watch.Watcher(demo, offset=state.events_end(demo))
    assert watch.run_pass(watching, T0) == [f"WATCH status page stale since {when(state.stamp(T0))}: Slack refused the redraw (canvas_too_large)"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=10)) == []
    assert watch.run_pass(watch.Watcher(demo, offset=state.events_end(demo)), T0 + timedelta(minutes=40)) == []
    monkeypatch.setattr(page, "draw", accept)
    redrawn = f"WATCH status page redrawn: Slack takes its redraws again, after refusing them since {when(state.stamp(T0))}"
    assert watch.run_pass(watching, T0 + timedelta(minutes=50)) == [redrawn]
    assert watch.run_pass(watch.Watcher(demo, offset=state.events_end(demo)), T0 + timedelta(minutes=60)) == []
    assert len(drawn) == 2


def test_a_page_slack_calls_full_moves_to_a_new_tab_and_the_watch_says_where(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch.github, "fetch", lambda keys: ({}, None))
    monkeypatch.setattr(page, "draw", DRAW)
    answers = iter(["F1", "F1", "F2"])

    def canvas(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        canvas_id = next(answers)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"canvas_id": canvas_id, "url": f"https://slack.example/docs/T1/{canvas_id}"}), "")

    monkeypatch.setattr(page.subprocess, "run", canvas)
    watching = watch.Watcher(demo, offset=state.events_end(demo))
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
    model = view.board(data, found(), "live", [], T0, set(), {})
    assert model["progress"] == {"prs": 2, "merged": 1, "open": 1, "red": 1, "running": 2}
    assert [(entry["id"], entry["state"]) for entry in model["work"]] == [("j1", "running"), ("j2", "waiting")]
    entry = next(entry for group in model["status"] for entry in group["features"])
    assert (entry["id"], entry["text"], entry["project"], entry["workstream"]) == ("o1", "ship the page\nwith its tests", "The drive", "Dashboard")
    assert [job_entry["id"] for job_entry in entry["jobs"]] == ["j1", "j2"]


def test_a_project_or_workstream_named_in_two_cases_is_one_under_the_newest_orders_spelling() -> None:
    data = drive_of(
        {
            "o1": order("draw the canvas", [], project="Drive tooling", workstream="jobs", updated=at(-10)),
            "o2": order("fold the names", [], project="drive tooling", workstream="Jobs", updated=at(-90)),
            "o3": order("watch the lanes", [], project="Drive tooling", workstream="Watch", updated=at(-50)),
        },
        {},
        [],
    )
    model = view.board(data, found(), "live", [], T0, set(), {})
    filed = {entry["id"]: (entry["project"], entry["workstream"]) for group in model["status"] for entry in group["features"]}
    assert filed == {"o1": ("Drive tooling", "jobs"), "o2": ("Drive tooling", "jobs"), "o3": ("Drive tooling", "Watch")}


def test_history_lists_every_day_newest_first_with_his_taps_answers_and_undos_each_line_sent_him_and_the_swears_in_his_own_words() -> None:
    card = "https://acme.slack.com/archives/D123/p1794200000000300"
    data = drive_of(
        {
            "o1": order("ship the page", ["j1"], created=at(-30), state="done", updated=at(-10), proof="installed 1.0.21"),
            "o2": order("WTF, fix the damn thing. Hello, shell", [], created=state.stamp(T0 - timedelta(days=2)), state="waiting", **{"from": "slack:D123/1794200000.000100"}, reporter=None),
            "o3": order("Drive tooling: the shit the lanes say", [], created=at(-40)),
            "o4": order("damn, the build is down", [], created=at(-50), **{"from": "slack:C9/1794200000.000200"}, reporter=state.reporter("https://acme.slack.com/archives/C9/p1794200000000200")),
        },
        {"j1": job("o1", "failed", "CI red (m12)", updated=-20)},
        [pr(1, "o1", state="merged", updated=at(-15))],
    )
    data["answers"] = {"a1": {"words": "keep both, damn it", "link": f"{card}?thread_ts=1794200000.000300", "at": at(-6), "said": at(-6), "cards": {}, "jobs": {}}}
    events = [
        {"kind": "tap", "at": at(-5), "repo": "yasyf/cc-skills", "number": 1, "label": "Approve"},
        {"kind": "shown", "at": at(-4), "repo": "yasyf/cc-skills", "number": 1, "waits": ""},
        {"kind": "answered", "at": at(-4), "url": card, "label": "Keep both", "question": "Ring or knock?"},
        {"kind": "answered", "at": at(-4), "url": "https://acme.slack.com/archives/D123/p1794200000000400", "label": "Shard it", "question": "Raise the limit?"},
        {"kind": "undone", "at": at(-3), "url": "https://acme.slack.com/archives/D123/p1794200000000400", "label": "Shard it", "question": None},
        {"kind": "refused", "at": at(-3), "repo": "yasyf/cc-skills", "number": 9, "reason": "its body lacks a section"},
        {"kind": "said", "at": at(-2), "text": "[change 1](https://github.com/yasyf/cc-skills/pull/1) is live", "url": "https://acme.slack.com/archives/D123/p1", "order": "o1", "visuals": []},
    ]
    history = view.board(data, found(), "live", events, T0, set(), {})["history"]
    assert [(entry["kind"], entry["text"], entry["detail"]) for entry in history] == [
        ("said", "[change 1](https://github.com/yasyf/cc-skills/pull/1) is live", ""),
        ("undone", "a card", "Shard it"),
        ("refused", "https://github.com/yasyf/cc-skills/pull/9", "its body lacks a section"),
        ("answered", "Raise the limit?", "Shard it"),
        ("tap", "change 1", "Approve"),
        ("answer", "keep both, damn it", ""),
        ("live", "ship the page", "installed 1.0.21"),
        ("merged", "change 1", ""),
        ("failed", "g", "CI red"),
        ("order", "ship the page", ""),
        ("order", "Drive tooling: the shit the lanes say", ""),
        ("order", "damn, the build is down", ""),
        ("order", "WTF, fix the damn thing. Hello, shell", ""),
    ]
    assert [entry["swears"] for entry in history if entry["kind"] in ("order", "answer")] == [1, 0, 0, 0, 2]


def test_a_dashboard_answer_offers_its_undo_in_history_until_an_undo_of_its_card_follows() -> None:
    data = drive_of({"o1": order("ship the page", [], created=at(-30))}, {}, [])
    taken, kept = "https://acme.slack.com/archives/D123/p1", "https://acme.slack.com/archives/D123/p2"
    events = [
        {"kind": "answered", "at": at(-5), "url": taken, "label": "Raise it", "question": "Raise the limit?", "undo_until": at(5)},
        {"kind": "answered", "at": at(-4), "url": kept, "label": "Keep it", "question": "Keep the cache?", "undo_until": at(6)},
        {"kind": "undone", "at": at(-3), "url": taken, "label": "Raise it", "question": "Raise the limit?"},
    ]
    history = view.board(data, found(), "live", events, T0, set(), {})["history"]
    assert [(entry["kind"], entry["url"], entry["undo_until"]) for entry in history if entry["kind"] in ("answered", "undone")] == [
        ("undone", taken, None),
        ("answered", kept, at(6)),
        ("answered", taken, None),
    ]


def test_a_stack_card_sent_to_him_shows_each_pr_as_it_stands_now() -> None:
    data = drive_of({"o1": order("ship the page", ["j1"])}, {"j1": job("o1", "waiting", "READY")}, [pr(1, "o1", wants_tap=True), pr(2, "o1", state="merged")])
    events = [
        {"kind": "said", "at": at(-2), "text": "land the page", "url": "https://acme.slack.com/archives/D123/p1", "order": "o1", "visuals": [], "prs": ["yasyf/cc-skills#1", "yasyf/cc-skills#2"]},
        {"kind": "said", "at": at(-1), "text": "an older line", "url": "https://acme.slack.com/archives/D123/p2", "order": None, "visuals": []},
    ]
    stacks = [entry["prs"] for entry in view.board(data, found(), "live", events, T0, set(), {})["history"] if entry["kind"] == "said"]
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
    stack = next(entry["prs"] for entry in view.board(data, found(), "live", [sent], T0, set(), {})["history"] if entry["kind"] == "said")
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


def test_cards_an_older_cc_slack_lists_with_no_closed_field_read_as_none_closed(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    gone = f"{TEAM}archives/D123/p1794200000000300"
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": TEAM, "questions": [], "pr_cards": []})
    view.refresh_cards(demo)
    listed, source = view.stored_cards(demo)
    assert (listed, source) == (found(), "live")
    assert view.card_words(listed, source, gone) == f"card {gone}, which cc-slack no longer keeps"
    assert view.card_words(listed, source, "demo:j1-1") == "card demo:j1-1 never drawn"
    assert view.pr_card_words(listed, source, "https://github.com/acme/app/pull/43") is None


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
    entries = view.board(data, found(pr_cards=[card]), "live", [], T0, set(), {})["review"]
    assert [(entry["key"], entry["group"], entry["feature_title"]) for entry in entries] == [
        ("yasyf/cc-skills#1", "you", "ship the page"),
        ("yasyf/cc-skills#2", "not ready", "ship the page"),
        ("yasyf/cc-skills#3", "decided", "ship the page"),
    ]
    assert (entries[0]["card"], entries[0]["answer"], entries[0]["since"], entries[0]["thread"]) == (drawn, card["url"], card["asked"], "1794200000.000100")
    assert all(entry["card"] is None and entry["thread"] is None for entry in entries[1:])


def test_review_says_his_approve_queues_a_green_monorepo_pr_with_no_tap_at_once() -> None:
    _, entry = pr(5, "o1", wants_tap=True)
    key = "Forge-AI/monorepo#5"
    data = drive_of({"o1": order("ship the page", ["j1"])}, {"j1": job("o1", "running")}, [(key, entry | {"url": "https://github.com/Forge-AI/monorepo/pull/5"})])
    [review] = view.board(data, found(), "live", [], T0, set(), {})["review"]
    assert review["approve_waits"] == ""


def test_a_pr_card_carrying_a_stack_is_one_queue_item_and_each_card_less_pr_is_its_own() -> None:
    data = drive_of({"o1": order("ship the page", ["j1"])}, {"j1": job("o1", "running")}, [pr(n, "o1", wants_tap=True) for n in (1, 2, 3)])
    urls = [f"https://github.com/yasyf/cc-skills/pull/{n}" for n in (1, 2)]
    card = {"prs": urls, "asked": at(-50), "url": f"{TEAM}archives/D123/p1794200000000100", "card": {"title": "Fix: approve the stack?", "options": ["Approve", "Hold"], "prs": [{"url": url} for url in urls]}}
    queue = view.build(data, found(pr_cards=[card]), T0)["queue"]
    assert [(item["kind"], item.get("answer"), item["url"]) for item in queue] == [
        ("tap", card["url"], card["url"]),
        ("tap", None, "https://github.com/yasyf/cc-skills/pull/3"),
    ]


def test_a_stack_card_in_his_queue_carries_each_of_its_prs_as_it_stands_now_in_the_cards_order() -> None:
    data = drive_of({"o1": order("ship the page", ["j1"])}, {"j1": job("o1", "running")}, [pr(1, "o1", state="merged"), pr(2, "o1", wants_tap=True)])
    urls = [f"https://github.com/yasyf/cc-skills/pull/{n}" for n in (1, 2)]
    card = {"prs": urls, "asked": at(-50), "url": f"{TEAM}archives/D123/p1794200000000100", "card": {"title": "Fix: approve the stack?", "options": ["Approve", "Hold"], "prs": [{"url": url} for url in urls]}}
    [item] = view.build(data, found(pr_cards=[card]), T0)["queue"]
    assert item["prs"] == [view.pr_entry(data["prs"]["yasyf/cc-skills#1"]), view.pr_entry(data["prs"]["yasyf/cc-skills#2"])]
    assert [(each["state"], each["tap"]) for each in item["prs"]] == [("merged", None), ("open", "wanted")]


REPLAYED_TAPS = {
    "Forge-AI/monorepo#55451": ("ced18ce6af0fb56617cf2c1a40ed78ca08441a77", "2026-11-13T00:08:20-08:00", "p1794539745884569"),
    "Forge-AI/monorepo#55464": ("996c1360a9fa0eb843b87dece9cc4f8d2e8b1a7d", "2026-11-13T00:08:42-08:00", "p1794539760095039"),
}


def test_a_pr_he_approved_on_the_dashboard_leaves_every_queue_at_once_though_the_watch_still_keeps_it_waiting_on_his_card() -> None:
    rows = []
    for key, (head, tapped, card) in REPLAYED_TAPS.items():
        _, entry = pr(1, "o1", wants_tap=True)
        tap = {"label": "Approve", "at": tapped, "head": head, "source": "answered on the dashboard"}
        rows.append((key, entry | {"url": state.pr_url(key), "head": head, "card": f"https://poetic-technology.slack.com/archives/D0C4HQWPLCC/{card}", "tap": tap}))
    data = drive_of({"o1": order("land the journal fixes", ["j1"])}, {"j1": job("o1", "waiting")}, rows)
    assert {key: entry["stage"]["name"] for key, entry in data["prs"].items()} == dict.fromkeys(REPLAYED_TAPS, "card")
    moment = state.parse_time("2026-11-13T00:09:00-08:00")
    assert view.build(data, found(), moment)["queue"] == []
    model = view.board(data, found(), "live", [], moment, set(), {})
    assert model["queue"] == []
    assert {entry["key"]: (entry["group"], entry["tap"], entry["stage"]["name"]) for entry in model["review"]} == dict.fromkeys(REPLAYED_TAPS, ("decided", "Approve", "ready"))


TEAM_FILES_CARD = "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794531567561719"


def test_a_card_he_asked_about_on_the_dashboard_sinks_below_the_rest_until_the_drive_posts_in_its_thread() -> None:
    held = question("Should team files get their own SandSQL primary?", 0, "durable-agents:j2423-681737") | {"url": TEAM_FILES_CARD, "asked": "2026-11-12T16:59:27-08:00"}
    newer = question("Release now?", 0) | {"url": "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794552000000100", "asked": "2026-11-12T23:20:00-08:00"}
    asked = {
        "kind": "asked",
        "at": "2026-11-13T00:21:13-08:00",
        "card": TEAM_FILES_CARD,
        "url": "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794558073608119?thread_ts=1794531567.561719&cid=D0C4HQWPLCC",
    }
    answered = {
        "kind": "said",
        "at": "2026-11-13T00:23:58-08:00",
        "text": "*Your system-database audit went to the coordinator.*",
        "channel": None,
        "thread": "1794531567.561719",
        "url": "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794558238705429?thread_ts=1794531567.561719&cid=D0C4HQWPLCC",
        "card": None,
        "order": None,
        "visuals": [],
        "prs": [],
        "edited": False,
    }
    elsewhere = answered | {"thread": "1794526897.162909", "url": "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794558100000100"}
    moment = state.parse_time("2026-11-13T00:30:00-08:00")

    def ranked(events: list[dict]) -> list[tuple[str, str]]:
        return [(item["title"], item["rank_why"]) for item in view.board(drive_of({}, {}, []), found([held, newer]), "live", events, moment, set(), {})["queue"]]

    waited = [(held["question"], f"waiting since {when(held['asked'])}"), (newer["question"], f"waiting since {when(newer['asked'])}")]
    assert ranked([]) == waited
    sunk = [waited[1], (held["question"], f"sent down while the drive answers your words of {when(asked['at'])}; waiting since {when(held['asked'])}")]
    assert ranked([asked]) == ranked([asked, elsewhere]) == ranked([answered, asked]) == sunk
    assert ranked([asked, answered]) == ranked([asked, answered | {"edited": True}, answered]) == waited
    assert ranked([asked, answered | {"edited": True}]) == sunk
    assert [item["priority"] for item in view.board(drive_of({}, {}, []), found([held, newer]), "live", [asked], moment, set(), {})["queue"]] == [
        {"rank": 1, "answering": False, "incident": False, "overdue": False, "weight": 0},
        {"rank": 2, "answering": True, "incident": False, "overdue": False, "weight": 0},
    ]


def test_a_card_posted_inside_a_thread_reads_that_thread_on_the_queue_and_in_review() -> None:
    data = drive_of({"o1": order("ship the page", ["j1"])}, {"j1": job("o1", "running")}, [pr(1, "o1", wants_tap=True)])
    drawn = {"title": "Fix: approve change 1?", "options": ["Approve", "Hold"], "prs": [{"url": "https://github.com/yasyf/cc-skills/pull/1"}]}
    tap = {"prs": ["https://github.com/yasyf/cc-skills/pull/1"], "asked": at(-50), "url": f"{TEAM}archives/D123/p1794200000000100?thread_ts=1794100000.000200&cid=D123", "card": drawn}
    asked = {**question("Release now?", -20), "url": f"{TEAM}archives/D123/p1794300000000300"}
    model = view.board(data, found([asked], [tap]), "live", [], T0, set(), {})
    assert [(item["kind"], item["thread"]) for item in model["queue"]] == [("tap", "1794100000.000200"), ("question", "1794300000.000300")]
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
    assert [(entry["id"], entry["title"]) for entry in view.board(data, found(), "live", [], T0, set(), {})["incidents"]] == [
        ("o4", "Sand usage events set aside"),
        ("o1", "runs fail for one team"),
    ]


def test_bug_reports_list_each_shown_reported_order_oldest_first_at_the_stage_it_reached_until_its_reporter_is_told_and_no_shared_question() -> None:
    def reported(told: str | None = None) -> dict:
        return state.reporter("https://acme.slack.com/archives/C1/p1794200000000100") | {"told": told}

    note = "https://acme.slack.com/archives/C1/p1794200000000900"
    data = drive_of(
        {
            "o1": order("runs page errors", [], reporter=reported(), created=at(-50)),
            "o2": order("questions stopped showing", ["j2"], reporter=reported(), created=at(-40)),
            "o3": order("GET /api/runs fails", ["j3"], reporter=reported(), created=at(-30)),
            "o4": order("recording the run fails", ["j4"], reporter=reported(), proof="runs record on plat", created=at(-20)),
            "o5": order("ids read wrong", ["j5"], reporter=reported(note), proof="ids read right on plat", state="done", created=at(-10), updated=at(-5)),
            "o6": order("ship the dashboard", ["j6"], created=at(-60)),
            "o7": order("an old bug", [], reporter=reported(note), proof="fixed", state="done", created=at(-3000), updated=at(-2000)),
            "o8": order("a dropped bug", [], reporter=reported(), state="dropped", created=at(-70)),
            "o9": order("what is left for cloud Iris", [], reporter=reported(), kind="answer", created=at(-80)),
        },
        {f"j{n}": job(f"o{n}", "done") for n in (2, 3, 4, 5, 6)},
        [pr(1, "o2", job="j2"), pr(2, "o3", state="merged"), pr(3, "o3", state="closed"), pr(4, "o4", state="merged"), pr(5, "o5", state="merged")],
    )
    assert [(entry["id"], entry["stage"]) for entry in view.board(data, found(), "live", [], T0, set(), {})["bug_reports"]] == [
        ("o1", "reported"),
        ("o2", "fixing"),
        ("o3", "merged"),
        ("o4", "live"),
        ("o5", "told"),
    ]


def test_the_dashboard_model_carries_no_plan() -> None:
    data = drive_of({"o1": order("ship the plan", [])}, {}, [])
    assert not {"milestones", "unplanned"} & set(view.board(data, found(), "live", [], T0, set(), {}))
    assert "milestone" not in view.board(data, found(), "live", [], T0, set(), {})["status"][-1]["features"][0]


def test_the_summary_every_page_is_sent_leaves_out_history_lanes_and_each_features_words_and_jobs_and_shows_each_card_at_a_glance() -> None:
    data = drive_of({"o1": order("ship the page\nwith its tests", ["j1", "j2"])}, {"j1": job("o1", "running"), "j2": job("o1", "done")}, [])
    shown = view.summary(view.board(data, found([question("Raise the limit?", -30)]), "live", [], T0, set(), {}))
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
    assert view.history_page(model, "2026-11-09", "said", 2) | {"days": [], "hours": []} == {"days": [], "hours": [], "day": "2026-11-09", "moments": said[80:], "page": 2, "pages": 2}
    assert view.history_page(model, "2026-11-12", "prs", 1)["moments"] == [merged]


def test_a_zoomed_day_counts_each_pacific_hour_of_it_and_his_words_answers_and_orders() -> None:
    history = [
        {"at": "2026-11-09T23:10:00-08:00", "kind": "merged"},
        {"at": "2026-11-09T09:40:00-08:00", "kind": "answer", "swears": 1},
        {"at": "2026-11-09T09:05:00-08:00", "kind": "merged"},
        {"at": "2026-11-09T09:00:00-08:00", "kind": "order", "swears": 2},
        {"at": "2026-11-08T09:00:00-08:00", "kind": "merged"},
    ]
    model = {"at": "2026-11-11T10:00:00-08:00", "history": history}
    hours = view.history_page(model, "2026-11-09", "all", 1)["hours"]
    assert len(hours) == 24
    assert [(each["hour"], each["merged"], each["swears"], each["asked"], each["moments"]) for each in hours if each["moments"]] == [(9, 1, 3, 1, 3), (23, 1, 0, 0, 1)]
    assert view.history_page(model, None, "all", 1)["hours"] == []
    assert [each["swears"] for each in view.history_page(model, None, "all", 1)["days"]] == [0, 3, 0, 0]


def test_a_features_story_is_its_newest_six_moments_of_every_kind_and_record_and_the_drives_is_its_orders_launches_merges_and_taps() -> None:
    kinds = [("said", "o1"), ("merged", "o2"), ("failed", "o1"), *[("tap", "o1")] * 7]
    history = [{"at": at(-n), "kind": kind, "feature": feature} for n, (kind, feature) in enumerate(kinds, 1)]
    record = {"seq": 5, "at": at(-2), "lane": "j1", "kind": "report", "text": "READY on #7"}
    model = {"history": history, "status": [{"state": "running", "features": [{"id": "o1", "heard": [record]}, {"id": "o2", "heard": []}]}]}
    told = view.story(model, {"o1"})
    assert [entry["kind"] for entry in told] == ["said", "heard", "failed", "tap", "tap", "tap"]
    assert told[1] == {"at": at(-2), "kind": "heard", "text": "READY on #7", "detail": "j1", "url": None, "feature": "o1"}
    assert view.story(model, None)[0] == history[1]


def test_a_running_task_says_its_jobs_newest_record_with_its_time_and_lists_each_record_naming_it_once() -> None:
    data = drive_of({"o1": order("ship it", ["j1"])}, {"j1": job("o1", "running", "PUSHED #7 with its test", updated=-30, goal="retry once")}, [])
    line = lambda seq, minutes, text: {"seq": seq, "at": at(minutes), "lane": "j1", "kind": "report", "text": text}
    heard = {"j1": [line(3, -5, "READY refused: buildkite/test failed at 1bd7e1e"), line(1, -40, "started")], "o1": [line(3, -5, "READY refused: buildkite/test failed at 1bd7e1e"), line(2, -20, "ORPHAN o1 has no job")]}
    model = view.board(data, found(), "live", [], T0, set(), heard)
    [entry] = [entry for group in model["status"] for entry in group["features"]]
    assert {key: entry["holdup"][key] for key in ("text", "at")} == {"text": "builder j1: READY refused: buildkite/test failed at 1bd7e1e", "at": at(-5)}
    assert entry["jobs"][0]["word"] == {"text": "READY refused: buildkite/test failed at 1bd7e1e", "at": at(-5)}
    assert [each["seq"] for each in entry["heard"]] == [3, 2, 1]
    assert not any("heard" in each for group in view.summary(model)["status"] for each in group["features"])
    quiet = view.board(data, found(), "live", [], T0, set(), {})
    assert [entry["jobs"][0]["word"] for group in quiet["status"] for entry in group["features"]] == [{"text": "PUSHED #7 with its test", "at": at(-30)}]


def test_a_task_lists_the_prs_of_each_job_carrying_it_and_each_pr_serving_it() -> None:
    data = drive_of(
        {"o1": order("the umbrella", ["j1"]), "o2": order("carried", []), "o3": order("served", [])},
        {"j1": job("o1", "running", prs=["https://github.com/yasyf/cc-skills/pull/1"], carries={"o2": ["whole"]})},
        [pr(1, "o1"), pr(2, "o1", serves="o3")],
    )
    model = view.board(data, found(), "live", [], T0, set(), {})
    prs = {entry["id"]: sorted(pr["url"].rsplit("/", 1)[1] for pr in entry["prs"]) for group in model["status"] for entry in group["features"]}
    assert prs == {"o1": ["1", "2"], "o2": ["1"], "o3": ["2"]}


def test_the_nav_counts_running_jobs_lanes_at_work_and_prs_merged_today() -> None:
    data = drive_of(
        {"o1": order("ship it", ["j1", "j2", "j3"])},
        {"j1": job("o1", "running"), "j2": job("o1", "waiting"), "j3": job("o1", "done")},
        [pr(1, "o1", state="merged", updated=at(-30)), pr(2, "o1", state="merged", updated=at(-60 * 30))],
    )
    counts = view.board(data, found(), "live", [], T0, set(), {})["counts"]
    assert (counts["jobs"], counts["lanes"], counts["merged_today"]) == (1, 2, 1)


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
    assert [entry["title"] for entry in view.board(data, found(), "live", [], T0, set(), {})["incidents"]] == [
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
    data = {"jobs": {"j3": {"goal": "prove the real run"}}, "prs": {"Forge-AI/monorepo#7": state.new_pr("https://github.com/Forge-AI/monorepo/pull/7", "j3", "o1") | {"title": "Fix: retry [flaky] uploads"}}}
    listed = {"team_url": "", "pr_cards": [], "closed": [], "questions": [{"question": "Pick a store?", "url": f"{QUESTION}?thread_ts=1.2", "card": {"title": "Pick a store?", "waits": "demo:j1-7-a"}}]}
    assert view.wait_words(data, wait, listed) == words


def closed(url: str, minutes: int, waits: str = "", prs: list[str] = (), answer: dict | None = None, replaced_by: str = "", tapped: str = "") -> dict:
    return {"waits": waits, "title": "", "prs": list(prs), "asked": at(minutes), "url": url, "answer": answer, "tapped": tapped, "replaced_by": replaced_by}


def test_card_words_name_each_question_card_as_cc_slacks_store_holds_it_open_answered_withdrawn_or_redrawn() -> None:
    first, again, other = (f"{TEAM}archives/D123/p17943808{n}" for n in (45, 50, 55))
    ledger = found(
        [question("Pick a store?", -5, "demo:j9-3")],
        closed=[
            closed(first, -60, "demo:j7-1", answer={"label": "his order replaced it", "at": at(-40), "withdrawn": True}),
            closed(again, -30, "demo:j7-2", answer={"label": "Yes", "at": at(-20)}),
            closed(other, -50, "demo:j8-1", answer={"label": "his order changed it", "at": at(-45), "withdrawn": True}, replaced_by=again),
        ],
    )
    assert view.card_words(ledger, "live", "demo:j9-3") == f"card {TEAM}archives/D123/p-5 open since {when(at(-5))}"
    assert view.card_words(ledger, "live", first) == f"card {first} withdrawn at {when(at(-40))}: his order replaced it"
    assert view.card_words(ledger, "live", "demo:j7-2") == f'card {again} answered "Yes" at {when(at(-20))}'
    assert view.card_words(ledger, "live", "demo:j8-1") == f'card {other} withdrawn at {when(at(-45))}: his order changed it, redrawn as card {again} answered "Yes" at {when(at(-20))}'
    assert view.card_words(ledger, "live", None) == "never drawn"
    assert view.card_words(ledger, "live", "demo:j7-9") == "card demo:j7-9 never drawn"
    assert view.card_words(None, "unavailable: not signed in", first) == "his cards could not be read (unavailable: not signed in)"


def test_a_question_card_cc_slacks_store_forgot_reads_as_the_drives_question_stands() -> None:
    forgotten = f"{TEAM}archives/D123/p1794200000000300"
    asked = state.new_question(None, "Merge #41 now?", at(-2000), card=forgotten)
    asked["waits"] = "demo:j3083-689347"
    assert view.question_card_words(found(), "live", asked) == f"card {forgotten} open since {when(at(-2000))}"
    asked["closed"] = state.new_close("answered", "Merge", None, at=at(-5))
    assert view.question_card_words(found(), "live", asked) == f'card {forgotten} answered "Merge" at {when(at(-5))}'
    asked["closed"] = state.new_close("withdrawn", None, None, at=at(-3))
    assert view.question_card_words(found(), "live", asked) == f"card {forgotten} withdrawn at {when(at(-3))}"
    assert view.question_card_words(None, "unavailable: not signed in", asked) == "his cards could not be read (unavailable: not signed in)"


def test_pr_card_words_name_his_open_tap_card_a_tap_on_a_redrawn_one_or_none() -> None:
    pr, other = "https://github.com/acme/app/pull/41", "https://github.com/acme/app/pull/42"
    first, again = f"{TEAM}archives/D123/p1794448525433989", f"{TEAM}archives/D123/p1794479611192239"
    tapped = closed(again, -10, "merge:acme/app:41", [pr], answer={"label": "Approve", "at": at(-5)}, tapped="✓ Approve")
    withdrawn = closed(first, -60, "merge:acme/app:41", [pr], answer={"label": "a new head", "at": at(-59), "withdrawn": True}, replaced_by=again)
    ledger = found(pr_cards=[{"prs": [other], "asked": at(-30), "url": f"{TEAM}archives/D123/p1794454407770969", "card": {"waits": "merge:acme/app:42"}}], closed=[withdrawn, tapped])
    assert view.pr_card_words(ledger, "live", other) == f"card {TEAM}archives/D123/p1794454407770969 open since {when(at(-30))}"
    assert view.pr_card_words(ledger, "live", pr) == f'card {again} answered "Approve" at {when(at(-5))}'
    assert view.pr_card_words(found(closed=[withdrawn]), "live", pr) == f"card {first} withdrawn at {when(at(-59))}: a new head, redrawn as card {again}, which cc-slack no longer keeps"
    assert view.pr_card_words(ledger, "live", "https://github.com/acme/app/pull/43") is None


def test_a_task_is_named_by_its_short_title_with_his_words_whole_and_an_untitled_order_by_its_first_words() -> None:
    words = "the chat list should keep its scroll when a new chat lands, see https://example.com/report for the recording"
    data = drive_of({"o1": order(words, [], title="Keep chat scroll"), "o2": order("ship the page\nwith its tests", [])}, {}, [])
    model = view.board(data, found(), "live", [], T0, set(), {})
    assert {entry["id"]: (entry["title"], entry["text"]) for group in model["status"] for entry in group["features"]} == {
        "o1": ("Keep chat scroll", words),
        "o2": ("ship the page", "ship the page\nwith its tests"),
    }


def test_a_line_too_long_for_its_row_ends_on_a_whole_word() -> None:
    line = view.said("PUSHED " + "https://github.com/yasyf/cc-skills/pull/12 " * 3 + "retries once " * 20)
    assert len(line) <= view.NOW_CHARS + 1
    assert line.endswith("once…") or line.endswith("retries…")
    assert set(line.removesuffix("…").split()) == {"PUSHED", "https://github.com/yasyf/cc-skills/pull/12", "retries", "once"}


@pytest.mark.parametrize(
    ("order_state", "jobs", "prs", "state_shown", "waits_on"),
    [
        ("working", {"j1": ("failed", "refund fee line"), "j2": ("running", "round like the ledger")}, {}, "blocked", {"text": "builder j1 failed: refund fee line"}),
        ("waiting", {"j1": ("waiting", "g")}, {"checks": "fail"}, "blocked", {"text": "red checks on #7", "pr": "yasyf/cc-skills#7"}),
        ("working", {"j1": ("running", "retry once")}, {"checks": "fail"}, "running", {"text": "builder j1 is on it: retry once"}),
        ("waiting", {"j1": ("waiting", "g")}, {"wants_tap": True}, "waiting", {"text": "waits on your tap on #7", "pr": "yasyf/cc-skills#7"}),
        ("waiting", {"j1": ("waiting", "g")}, {"checks": "pending"}, "waiting", {"text": "waits on #7's checks", "pr": "yasyf/cc-skills#7"}),
        ("waiting", {"j1": ("waiting", "cart sync")}, None, "waiting", {"text": "waits on builder j1: cart sync"}),
        ("waiting", {"j1": ("done", "g")}, {"state": "merged"}, "merged", {"text": "waits on proof it works where you use it"}),
        ("open", {}, None, "not started", {"text": "no job has started on it yet"}),
    ],
)
def test_a_task_header_names_what_it_waits_on_and_agrees_with_the_state_it_shows(order_state: str, jobs: dict, prs: dict | None, state_shown: str, waits_on: dict) -> None:
    made = {job_id: job("o1", job_state, goal=goal, updated=-10 * n) for n, (job_id, (job_state, goal)) in enumerate(jobs.items(), 1)}
    data = drive_of({"o1": order("ship it", list(jobs), state=order_state)}, made, [] if prs is None else [pr(7, "o1", **prs)])
    [entry] = [entry for group in view.board(data, found(), "live", [], T0, set(), {})["status"] for entry in group["features"]]
    assert entry["state"] == state_shown
    assert {key: value for key, value in entry["holdup"].items() if value and key != "url"} == waits_on


def test_a_task_waiting_on_his_card_names_that_card_and_a_live_one_waits_on_nothing() -> None:
    asked = question("Merge carts on sign-in?", -30)
    data = drive_of(
        {"o1": order("sync the cart", ["j1"]), "o2": order("stack promo codes", ["j2"], state="done", proof="live on #401")},
        {"j1": job("o1", "waiting", waits=[asked["url"]]), "j2": job("o2", "done")},
        [],
    )
    entries = {entry["id"]: entry for group in view.board(data, found([asked]), "live", [], T0, set(), {})["status"] for entry in group["features"]}
    assert entries["o1"]["holdup"] == {"text": "waits on your answer to Merge carts on sign-in?", "card": asked["url"], "pr": None, "url": None, "at": None}
    assert entries["o2"]["holdup"] is None


def test_a_task_lists_each_part_with_the_newest_job_carrying_it_and_where_it_is_reads_that_jobs_whole_line() -> None:
    line = "BLOCKED buildkite/test is red on https://github.com/yasyf/cc-skills/pull/7: the refund total rounds 0.005 down while the ledger rounds it up " * 2
    data = drive_of(
        {"o1": order("retry and receipt", ["j1", "j2"], parts={"retry": state.new_part(), "receipt": state.new_part(), "docs": state.new_part()})},
        {"j1": job("o1", "done", "DONE retry", updated=-50, parts=["retry"]), "j2": job("o1", "failed", line, updated=-5, parts=["receipt"])},
        [],
    )
    [entry] = [entry for group in view.board(data, found(), "live", [], T0, set(), {})["status"] for entry in group["features"]]
    assert [(part["name"], part["whole"], part["job"], part["state"]) for part in entry["parts"]] == [
        ("retry", False, "j1", "done"),
        ("receipt", False, "j2", "failed"),
        ("docs", False, None, None),
    ]
    assert entry["where"] == {"text": line.strip(), "job": "j2", "state": "failed", "at": at(-5)}
    assert not {"parts", "where", "holdup"} & set(view.outline(entry))


def test_where_it_is_takes_out_sentences_naming_record_ids_and_reads_a_line_whose_every_sentence_names_one_as_written() -> None:
    data = drive_of(
        {
            "o1": order("retry", ["j1"]),
            "o2": order("receipt", ["j2"]),
            "o3": order("docs", [], state="done", proof="DONE yasyf/cc-notes#278 merged as 9219fa636f"),
        },
        {
            "j1": job("o1", "waiting", "WAITING on #53778's checks at head 9c276243. The fix is pushed."),
            "j2": job("o2", "done", "DONE reader  j2027 done, 1 of 3 parts delivered"),
        },
        [],
    )
    assert [view.where_it_is(data, data["orders"][key])["text"] for key in ("o1", "o2", "o3")] == [
        "The fix is pushed.",
        "DONE reader j2027 done, 1 of 3 parts delivered",
        "DONE yasyf/cc-notes#278 merged as 9219fa636f",
    ]
