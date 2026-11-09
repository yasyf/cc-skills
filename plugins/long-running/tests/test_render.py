from datetime import timedelta

from drive import render, state


def order(
    text: str,
    order_state: str = "working",
    due: str | None = None,
    jobs: list[str] | None = None,
    created: str = "2026-11-09T08:00:00-08:00",
) -> dict:
    return {
        "text": text,
        "from": "terminal",
        "thread": None,
        "state": order_state,
        "due": due,
        "due_said": None,
        "jobs": jobs or [],
        "proof": None,
        "note": None,
        "created": created,
        "updated": created,
        "last_line": None,
    }


def sample() -> dict:
    drive = state.new_drive("demo", "s1", "/work", "D123")
    drive["orders"] = {
        "o1": order(
            "Ship the drive rewrite and install it", due=state.stamp(state.now() + timedelta(hours=1)), jobs=["j1"]
        ),
        "o2": order("Already shipped", order_state="done"),
        "o3": order("Answer whether the cache is warm " + "x" * 200, order_state="open"),
    }
    drive["jobs"] = {
        "j1": {
            "order": "o1",
            "kind": "builder",
            "goal": "rewrite long-running",
            "agent": "agent-7",
            "repo": "yasyf/cc-skills",
            "branch": "lr-rewrite",
            "worktree": None,
            "prs": ["https://github.com/yasyf/cc-skills/pull/9"],
            "state": "running",
            "said": None,
            "created": "2026-11-09T08:00:00-08:00",
            "updated": "2026-11-09T08:00:00-08:00",
        }
    }
    drive["prs"] = {
        "yasyf/cc-skills#9": {
            "url": "https://github.com/yasyf/cc-skills/pull/9",
            "job": "j1",
            "order": "o1",
            "wants_tap": True,
            "tap": None,
            "exempt": None,
            "fundamental": None,
            "state": "open",
            "title": "New: drive",
            "head": "abc1234",
            "checks": "pass",
            "review": "none",
            "draft": False,
            "merged_sha": None,
            "updated": "2026-11-09T08:00:00-08:00",
        }
    }
    return drive


def test_context_lists_open_orders_jobs_prs_rules_and_monitor() -> None:
    text = render.context(sample())
    assert "drive demo" in text
    assert "o1 working due " in text
    assert "o3 open" in text
    assert "o2" not in text
    assert "j1 builder running agent agent-7: rewrite long-running" in text
    assert "yasyf/cc-skills#9 open, checks pass, review none, wants tap" in text
    for rule in render.DRIVER_RULES:
        assert rule in text
    assert text.endswith(render.MONITOR_LINE)


def test_context_cuts_words_to_120() -> None:
    line = next(line for line in render.context(sample()).splitlines() if line.startswith("- o3"))
    assert len(line.split(": ", 1)[1]) == 120
    assert line.endswith("…")


def test_context_with_no_open_orders_says_none() -> None:
    drive = state.new_drive("empty", None, "/work", None)
    assert "Open orders (0):\n- none" in render.context(drive)


def test_status_text_counts_open_orders() -> None:
    text = render.status_text(sample())
    assert text.splitlines()[0] == "demo: 2 open of 3 orders"
    assert "o1 working" in text
    assert "o2" not in text


def test_status_data_keeps_only_open_work() -> None:
    data = render.status_data(sample())
    assert set(data["orders"]) == {"o1", "o3"}
    assert set(data["jobs"]) == {"j1"}
    assert set(data["prs"]) == {"yasyf/cc-skills#9"}


def test_rollup_card_orders_by_due_then_created() -> None:
    card = render.rollup_card(sample())
    assert card["title"] == "2 open: demo"
    assert [fact["label"] for fact in card["facts"]] == [
        "Ship the drive rewrite and install it",
        render.cut(sample()["orders"]["o3"]["text"], 60),
    ]
    assert card["facts"][0]["value"].startswith("working · due ")
    assert card["facts"][0]["value"].endswith("https://github.com/yasyf/cc-skills/pull/9")
    assert card["facts"][1]["value"] == "open"
    assert all(len(fact["label"]) <= 60 for fact in card["facts"])


def test_rollup_card_caps_facts_at_ten() -> None:
    drive = state.new_drive("busy", None, "/work", None)
    drive["orders"] = {f"o{n}": order(f"order {n}", created=f"2026-11-09T08:{n:02d}:00-08:00") for n in range(1, 15)}
    card = render.rollup_card(drive)
    assert card["title"] == "14 open: busy"
    assert [fact["label"] for fact in card["facts"]] == [f"order {n}" for n in range(1, 11)]


def test_when_names_the_day_for_other_days() -> None:
    tomorrow = state.now() + timedelta(days=1)
    assert render.when(state.stamp(tomorrow)).startswith(tomorrow.strftime("%b %-d"))
