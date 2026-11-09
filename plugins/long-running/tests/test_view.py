from datetime import timedelta

import pytest
from drive import cli, page, state, view, watch

T0 = state.parse_time("2026-11-09T10:00:00-08:00")
TEAM = "https://acme.slack.com/"


def at(minutes: int) -> str:
    return state.stamp(T0 + timedelta(minutes=minutes))


def order(text: str, jobs: list[str], **overrides: object) -> dict:
    base = {
        "text": text,
        "from": "terminal",
        "thread": "1794200000.000100",
        "state": "working",
        "due": None,
        "due_said": None,
        "jobs": jobs,
        "proof": None,
        "note": None,
        "created": at(-120),
        "updated": at(-60),
        "last_line": None,
    }
    return base | overrides


def job(order_id: str, job_state: str, said: str | None = None, updated: int = -30) -> dict:
    return {"order": order_id, "kind": "builder", "goal": "g", "prs": [], "state": job_state, "said": said, "updated": at(updated)}


def pr(number: int, order_id: str, **overrides: object) -> tuple[str, dict]:
    url = f"https://github.com/yasyf/cc-skills/pull/{number}"
    entry = cli.new_pr(url, "j1", order_id) | {"title": f"New: \U0001f680 change {number}", "updated": at(-number)}
    return f"yasyf/cc-skills#{number}", entry | {"checks": "pass", "review": "approved"} | overrides


def drive_of(orders: dict, jobs: dict, prs: list[tuple[str, dict]]) -> dict:
    data = state.new_drive("demo", "coordinator", "s1", "/work", "D123")
    data.update(orders=orders, jobs=jobs, prs=dict(prs))
    return data


def found(questions: list[dict] = (), pr_cards: list[dict] = ()) -> dict:
    return {"team_url": TEAM, "questions": list(questions), "pr_cards": list(pr_cards)}


def question(title: str, minutes: int) -> dict:
    return {"question": title, "options": ["Yes", "No"], "recommended": "Yes", "asked": at(minutes), "url": f"{TEAM}archives/D123/p{minutes}"}


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
        ],
    )
    card = {"prs": ["https://github.com/yasyf/cc-skills/pull/3"], "asked": at(-50), "url": f"{TEAM}archives/D123/p3"}
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
    assert queue[1]["url"] == card["url"]
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
    assert model["counts"] == {"queue": 1, "blocked": 0, "running": 1, "waiting": 0, "merged": 0, "live": 1, "not started": 0}


def test_page_leads_with_his_queue_and_tables_each_state() -> None:
    data = drive_of({"o1": order("ship the page", ["j1"])}, {"j1": job("o1", "running", "drawing it")}, [pr(1, "o1", wants_tap=True)])
    markdown = page.render(view.build(data, found([question("Release now?", -20)]), T0))
    head, _, rest = markdown.partition("## Running (1)")
    assert "## Waiting on you" in head
    assert "1. **Question:** [Release now?]" in head
    assert "2. **Your tap:** [change 1](https://github.com/yasyf/cc-skills/pull/1)" in head
    assert "| Feature | Now | Pull requests |" in rest
    assert ":large_green_circle: [change 1](https://github.com/yasyf/cc-skills/pull/1)" in rest


def test_page_says_when_nothing_waits_on_him() -> None:
    data = drive_of({}, {}, [])
    assert "Nothing waits on you." in page.render(view.build(data, found(), T0))


def test_watch_redraws_the_page_only_when_the_model_changes(demo: str, drawn: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch.github, "fetch", lambda keys: {})
    watching = watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0)
    watch.run_pass(watching, T0)
    watch.run_pass(watching, T0 + timedelta(seconds=60))
    assert len(drawn) == 1
    with state.edit(demo) as data:
        data["orders"]["o1"] = order("a new order", [])
    watch.run_pass(watching, T0 + timedelta(seconds=120))
    assert len(drawn) == 2
    assert "a new order" in drawn[-1]


def test_a_failed_draw_is_said_at_most_every_ten_minutes_and_retried(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch.github, "fetch", lambda keys: {})

    def refused(name: str, markdown: str) -> str:
        raise page.PageError("missing_scope")

    monkeypatch.setattr(page, "draw", refused)
    watching = watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0)
    assert watch.run_pass(watching, T0) == ["WATCH status page draw failed: missing_scope"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=10)) == ["WATCH status page draw failed: missing_scope"]
