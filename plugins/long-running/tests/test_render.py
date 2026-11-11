from datetime import timedelta

import re

from drive import render, state, tools


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
        "from_thread": None,
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
        "reporter": None,
        "note": None,
        "created": created,
        "updated": created,
        "last_line": None,
        "project": None,
        "workstream": None,
        "incident": False,
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
            "hold_label": False,
            "verdict": None,
            "check": None,
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
    assert text.endswith(render.FIRST_CALL["coordinator"])


def test_communicator_context_gives_rules_and_tools_but_no_order_list() -> None:
    drive = sample()
    drive["roles"]["communicator"] = {"session": "s2", "cwd": "/work", "started": "2026-11-09T08:00:00-08:00"}
    text = render.context(drive, "communicator")
    assert text.startswith("You are the communicator of drive demo: you talk to him. The coordinator: session s1.")
    assert "cci subscribe --reader communicator --cursor communicator" in text
    assert "o1" not in text
    for rule in render.COMMUNICATOR_RULES:
        assert rule in text
    for row in ("+1 react", "away: true", 'kind: "reader"', "Explain more", "merge:", "MISSING <path>", "defect record", "post feedback", "Sent."):
        assert row in text
    assert render.FIRST_CALL["coordinator"] not in text
    assert text.endswith(render.FIRST_CALL["communicator"])


def test_the_meta_sessions_context_gives_its_rules_and_drive_tooling_leaves_the_communicators_once_it_binds() -> None:
    drive = sample()
    text = render.context(drive, "meta")
    assert text.startswith("You are the meta session of drive demo: you hold drive tooling: every tool fix, every skill rewrite")
    assert "The communicator: not started. The coordinator: session s1." in text.splitlines()[0]
    assert text.splitlines()[1] == "Hold no Slack channel."
    assert "cci subscribe --reader meta --cursor meta" in text
    for rule in render.META_RULES:
        assert rule in text
    assert text.endswith(render.FIRST_CALL["meta"])
    assert render.NO_META.format(name="demo") in render.context(drive, "communicator")
    drive["roles"]["meta"] = {"session": "s3", "cwd": "/work", "started": "2026-11-11T12:40:00-08:00"}
    assert render.NO_META.format(name="demo") not in render.context(drive, "communicator")
    assert "The meta session: session s3." in render.context(drive, "coordinator").splitlines()[0]


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
    for role in state.ROLES:
        assert len(render.context(many, role)) == len(render.context(few, role)) < 10_000


def test_compact_instructions_keep_only_what_the_disk_does_not_hold() -> None:
    drive = sample()
    for role in state.ROLES:
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
    assert render.context(drive, "coordinator").splitlines()[1] == 'Hold with cc-slack subscribe --kind alerts,releases,posts --channel <id>: C0B75AL4XEW, C0822AHFY3G'


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
    lines = render.status_text(drive, {}).splitlines()
    pr_lines = [line for line in lines if line.startswith("    - ")]
    assert pr_lines == [
        "    - https://github.com/yasyf/cc-skills/pull/10",
        '    - yasyf/cc-skills#9 "New: drive" open, checks pass, review none, wants tap',
        '    - yasyf/cc-skills#11 "New: drive" open, checks pass, review none, wants tap',
    ]


def test_status_text_counts_open_orders() -> None:
    text = render.status_text(sample(), {"coordinator": "in no Orca terminal"})
    assert text.splitlines()[:3] == [
        "demo: 2 open of 3 orders",
        "communicator: not started; Hold with slack_watch: his DM D123",
        "coordinator: session s1, in no Orca terminal; Hold no Slack channel.",
    ]
    assert "o1 working" in text
    assert "o2" not in text


def test_status_data_keeps_only_open_work() -> None:
    data = render.status_data(sample(), {"coordinator": "in no Orca terminal"})
    assert set(data["roles"]) == {"coordinator"}
    assert set(data["orders"]) == {"o1", "o3"}
    assert set(data["jobs"]) == {"j1"}
    assert set(data["prs"]) == {"yasyf/cc-skills#9"}


def test_status_lists_each_report_held_for_his_catch_up() -> None:
    drive = sample()
    at = state.stamp(state.now())
    drive["held"] = {"700": {"order": "o1", "at": at, "told": "701"}, "702": {"order": None, "at": at, "told": "703"}}
    lines = render.status_text(drive, {}).splitlines()
    start = lines.index("Held for his catch-up, since he is away; the catch-up he gets when he is back carries each:")
    assert lines[start + 1 : start + 3] == [
        f"- report 700 (o1), held {render.when(at)}, its writer told in record 701",
        f"- report 702 (no order), held {render.when(at)}, its writer told in record 703",
    ]
    assert render.status_data(drive, {})["held"] == drive["held"]
    assert not any(line.startswith("Held for his catch-up") for line in render.status_text(sample(), {}).splitlines())


def test_when_names_the_day_for_other_days() -> None:
    tomorrow = state.now() + timedelta(days=1)
    assert render.when(state.stamp(tomorrow)).startswith(tomorrow.strftime("%b %-d"))


def test_status_text_names_the_thread_each_orders_lines_go_in() -> None:
    drive = sample()
    drive["orders"]["o1"]["thread"] = "1794307360.614569"
    lines = render.status_text(drive, {}).splitlines()
    due = render.when(drive["orders"]["o1"]["due"])
    assert next(line for line in lines if line.startswith("- o1 ")).startswith(
        f"- o1 working due {due}, its lines go in D123 thread 1794307360.614569: "
    )
    assert next(line for line in lines if line.startswith("- o3 ")).startswith("- o3 open: ")


def test_every_order_a_sessions_rules_tell_it_to_add_carries_each_field_order_add_requires() -> None:
    required = set(tools.schema(tools.leaf("order", "add"))["required"])
    templates = [
        re.findall(r"(\w+):", found)
        for rules in render.RULES.values()
        for rule in rules
        for found in re.findall(r"order: \{([^}]*)\}", rule)
    ]
    assert templates
    for keys in templates:
        assert required <= set(keys), keys


def test_the_context_names_the_projects_and_workstreams_in_use_for_a_new_order_to_reuse() -> None:
    drive = sample()
    drive["orders"]["o1"] |= {"project": "Platform", "workstream": "SandSQL"}
    drive["orders"]["o2"] |= {"project": "The drive", "workstream": "Drive tooling"}
    drive["orders"]["o3"] |= {"project": "Platform", "workstream": "Releases and CI"}
    for role in ("communicator", "coordinator"):
        assert (
            "Projects in use, each with its workstreams: Platform: SandSQL, Releases and CI; The drive: Drive tooling"
            in render.context(drive, role)
        )
