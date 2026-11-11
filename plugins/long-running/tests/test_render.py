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
        "promised": None,
        "next_due": None,
        "kept": None,
        "withdrawn": None,
        "jobs": jobs or [],
        "proof": None,
        "note": None,
        "created": created,
        "updated": created,
        "last_line": None,
    }


def sample() -> dict:
    drive = state.new_drive("demo", "coordinator", "s1", "/work", "D123")
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
            "questions": [],
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


def test_coordinator_context_gives_rules_reads_and_monitor_but_no_order_list() -> None:
    text = render.context(sample(), "coordinator")
    assert text.startswith("You are the coordinator of drive demo: ")
    assert "The communicator: not started." in text.splitlines()[0]
    assert "cci subscribe --reader root --cursor coordinator" in text
    for rule in render.COORDINATOR_RULES:
        assert rule in text
    assert "Ship the drive rewrite" not in text
    assert "j1" not in text
    assert render.READS["coordinator"] in text
    assert "drive manual coordinator" in text and "drive manual jobs" in text
    assert text.endswith(render.MONITOR_LINE)


def test_communicator_context_gives_rules_and_tools_but_no_order_list() -> None:
    drive = sample()
    drive["roles"]["communicator"] = {"session": "s2", "cwd": "/work", "started": "2026-11-09T08:00:00-08:00"}
    text = render.context(drive, "communicator")
    assert text.startswith("You are the communicator of drive demo: you talk to him. The coordinator: session s1.")
    assert "cci subscribe --reader communicator --cursor communicator" in text
    assert "o1" not in text
    for rule in render.COMMUNICATOR_RULES:
        assert rule in text
    for row in ("+1 react", "--away", "drive job add reader", "Explain more", "merge:", "MISSING <path>", "defect record", "Sent."):
        assert row in text
    assert render.MONITOR_LINE not in text
    assert text.endswith(render.SLACK_TOOLS_LINE)


def test_coordinator_context_names_each_order_whose_promised_line_is_owed() -> None:
    drive = sample()
    passed = state.now() - timedelta(minutes=30)
    drive["orders"]["o3"]["due"] = state.stamp(passed)
    lines = render.context(drive, "coordinator").splitlines()
    assert f"- o3 line owed since {render.clock(passed)}" in lines
    assert not any(line.startswith("- o1") for line in lines)
    assert "- o3" not in render.context(drive, "communicator")


def test_context_size_does_not_grow_with_open_orders() -> None:
    few, many = sample(), sample()
    many["orders"] |= {f"o{n}": order(f"order {n} " + "x" * 200, order_state="open") for n in range(10, 110)}
    for role in ("communicator", "coordinator"):
        assert len(render.context(many, role)) == len(render.context(few, role)) < 10_000


def test_compact_instructions_keep_only_what_the_disk_does_not_hold() -> None:
    drive = sample()
    for role in ("communicator", "coordinator"):
        text = render.compact_instructions(drive, role)
        assert text.startswith("These instructions replace the default summary sections.")
        assert f"is at most {render.KEEP_LIMIT[role]} characters" in text
        for item in render.KEEP[role]:
            assert item in text
        assert "his earlier messages" in text


def test_his_dm_is_the_communicators_and_watched_channels_the_coordinators() -> None:
    drive = sample()
    drive["slack"]["watch"] = ["C0B75AL4XEW", "C0822AHFY3G"]
    assert render.context(drive, "communicator").splitlines()[1] == "Hold with slack_watch: his DM D123"
    assert render.context(drive, "coordinator").splitlines()[1] == "Hold with slack_watch: C0B75AL4XEW, C0822AHFY3G"


def test_hold_line_reads_a_drive_saved_before_watch_existed() -> None:
    drive = sample()
    del drive["slack"]["watch"]
    assert render.hold_line(drive, "coordinator") == "Hold no Slack channel."


def test_hold_line_says_when_no_dm_is_set() -> None:
    drive = state.new_drive("empty", "coordinator", None, "/work", None)
    assert render.hold_line(drive, "communicator") == "Hold with slack_watch: no DM set"


def test_status_text_lists_each_jobs_pr_urls_and_registered_prs_once() -> None:
    drive = sample()
    drive["jobs"]["j1"]["prs"] = [
        "https://github.com/yasyf/cc-skills/pull/10",
        "https://github.com/yasyf/cc-skills/pull/9",
    ]
    drive["prs"]["yasyf/cc-skills#11"] = drive["prs"]["yasyf/cc-skills#9"] | {
        "url": "https://github.com/yasyf/cc-skills/pull/11"
    }
    lines = render.status_text(drive, "live", {}).splitlines()
    pr_lines = [line for line in lines if line.startswith("    - ")]
    assert pr_lines == [
        "    - https://github.com/yasyf/cc-skills/pull/10",
        "    - yasyf/cc-skills#9 open, checks pass, review none, wants tap",
        "    - yasyf/cc-skills#11 open, checks pass, review none, wants tap",
    ]


def test_status_text_counts_open_orders() -> None:
    text = render.status_text(sample(), "live", {"coordinator": "in no Orca terminal"})
    assert text.splitlines()[:3] == [
        "demo: 2 open of 3 orders; PR fields live",
        "communicator: not started; Hold with slack_watch: his DM D123",
        "coordinator: session s1, in no Orca terminal; Hold no Slack channel.",
    ]
    assert "o1 working" in text
    assert "o2" not in text


def test_status_data_keeps_only_open_work() -> None:
    data = render.status_data(sample(), "stored", {"coordinator": "in no Orca terminal"})
    assert data["pr_fields"] == "stored"
    assert set(data["roles"]) == {"coordinator"}
    assert set(data["orders"]) == {"o1", "o3"}
    assert set(data["jobs"]) == {"j1"}
    assert set(data["prs"]) == {"yasyf/cc-skills#9"}


def test_when_names_the_day_for_other_days() -> None:
    tomorrow = state.now() + timedelta(days=1)
    assert render.when(state.stamp(tomorrow)).startswith(tomorrow.strftime("%b %-d"))


def test_status_text_names_the_thread_each_orders_lines_go_in() -> None:
    drive = sample()
    drive["orders"]["o1"]["thread"] = "1794307360.614569"
    lines = render.status_text(drive, "live", {}).splitlines()
    due = render.when(drive["orders"]["o1"]["due"])
    assert next(line for line in lines if line.startswith("- o1 ")).startswith(
        f"- o1 working due {due}, its lines go in D123 thread 1794307360.614569: "
    )
    assert next(line for line in lines if line.startswith("- o3 ")).startswith("- o3 open: ")
