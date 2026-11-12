from datetime import timedelta

import re

from drive import cli, render, state, tools

HOOK_CHARS = 10_000


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
        "reporter": None,
        "note": None,
        "created": created,
        "updated": created,
        "last_line": None,
        "project": None,
        "workstream": None,
        "title": None,
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
            "card_state": None,
            "head": "abc1234",
            "checks": "pass",
            "review": "none",
            "hold_label": False,
            "rework": None,
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


def test_the_meta_session_and_communicator_read_one_definition_of_drive_tooling() -> None:
    drive = sample()
    text = render.context(drive, "meta")
    assert text.startswith(f"You are the meta session of drive demo: you hold drive tooling, {state.DRIVE_TOOLING}.")
    assert "The communicator: not started. The coordinator: session s1." in text.splitlines()[0]
    assert text.splitlines()[1] == "Hold no Slack channel."
    assert "cci subscribe --reader meta --cursor meta" in text
    for rule in render.META_RULES:
        assert rule in text
    assert text.endswith(render.FIRST_CALL["meta"])
    assert sum(state.DRIVE_TOOLING in rule for rule in render.META_RULES) == 1
    assert sum(state.DRIVE_TOOLING in rule for rule in render.COMMUNICATOR_RULES) == 1
    drive["roles"]["meta"] = {"session": "s3", "cwd": "/work", "started": "2026-11-11T12:40:00-08:00"}
    assert "The meta session: session s3." in render.context(drive, "coordinator").splitlines()[0]


def test_every_role_reads_a_question_proposing_a_way_as_his_order_and_meta_reads_his_ruling_to_keep_simplifying() -> None:
    contexts = {role: render.context(sample(), role) for role in state.ROLES}
    assert '"why not just X?"' in contexts["communicator"]
    assert [role for role in state.ROLES if '"why not just X?"' not in (cli.PAGES / f"{role}.md").read_text()] == []
    assert [role for role, text in contexts.items() if "in geernal keeps implifying all this tuff" in text] == ["meta"]


def test_each_page_naming_drive_tooling_carries_its_one_definition() -> None:
    pages = [cli.PAGES / "communicator.md", cli.PAGES / "meta.md", cli.PAGES / "concepts.md", cli.PAGES.parents[2] / "agents" / "handler.md"]
    assert [page.name for page in pages if state.DRIVE_TOOLING not in page.read_text()] == []


def live_sized() -> dict:
    drive = sample()
    passed = state.stamp(state.now() - timedelta(minutes=30))
    for n in range(785):
        order_id, job_id, still_open = f"o{n + 10}", f"j{n + 10}", n < 388
        filing = {"project": f"Project {n % 74 % 15}", "workstream": f"Workstream {n % 74}"}
        drive["orders"][order_id] = order(f"order {n} " + "x" * 200, "open" if still_open else "done", passed if still_open else None, [job_id]) | filing
        drive["jobs"][job_id] = drive["jobs"]["j1"] | {"order": order_id, "goal": f"goal {n} " + "y" * 200, "prs": []}
    drive["name"] = "durable-agents"
    drive["slack"] |= {"channel": "D0C4HQWPLCC", "watch": [f"C0B75AL{n:04d}" for n in range(8)]}
    drive["roles"] = {role: drive["roles"]["coordinator"] | {"session": f"{n:08x}-ce81-4ce1-b1dc-8bbdf3e90a5e"} for n, role in enumerate(state.ROLES)}
    return drive


def test_every_session_start_block_fits_claude_codes_hook_limit_on_a_drive_as_large_as_the_live_one() -> None:
    small, large = sample(), live_sized()
    blocks = {role: render.context(large, role) for role in state.ROLES} | {"sub-coordinator": render.context(large, "coordinator", "Project 1")}
    assert {seat: len(block) for seat, block in blocks.items() if len(block) >= HOOK_CHARS} == {}
    for role in state.ROLES:
        assert render.context(large, role).splitlines()[3:] == render.context(small, role).splitlines()[3:]
    assert not any(line.startswith("- o") for block in blocks.values() for line in block.splitlines())


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
        '    - yasyf/cc-skills#9 "New: drive" open, checks pass, review none, wants tap, his card never drawn',
        '    - yasyf/cc-skills#11 "New: drive" open, checks pass, review none, wants tap, his card never drawn',
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
    drive["held"] = {"700": {"order": "o1", "at": at, "told": "701", "corrections": ["704", "706"]}, "702": {"order": None, "at": at, "told": "703", "corrections": []}}
    lines = render.status_text(drive, {}).splitlines()
    start = lines.index("Held for his catch-up, since he is away; the catch-up he gets when he is back carries each:")
    assert lines[start + 1 : start + 3] == [
        f"- report 700 (o1), held {render.when(at)}, its writer told in record 701, corrected by records 704, 706",
        f"- report 702 (no order), held {render.when(at)}, its writer told in record 703",
    ]
    assert render.status_data(drive, {})["held"] == drive["held"]
    assert not any(line.startswith("Held for his catch-up") for line in render.status_text(sample(), {}).splitlines())


def test_when_names_the_day_for_other_days() -> None:
    tomorrow = state.now() + timedelta(days=1)
    assert render.when(state.stamp(tomorrow)).startswith(tomorrow.strftime("%b %-d"))


def test_status_text_names_the_thread_each_orders_lines_go_in() -> None:
    drive = sample()
    drive["orders"]["o1"]["thread"] = ["D123", "1794307360.614569"]
    lines = render.status_text(drive, {}).splitlines()
    due = render.when(drive["orders"]["o1"]["due"])
    assert next(line for line in lines if line.startswith("- o1 ")).startswith(
        f"- o1 working due {due}, its lines go in D123 thread 1794307360.614569: "
    )
    assert next(line for line in lines if line.startswith("- o3 ")).startswith("- o3 open: ")


def test_every_order_a_sessions_rules_or_rows_tell_it_to_add_carries_each_field_order_add_requires() -> None:
    required = set(tools.schema(tools.leaf("order", "add"))["required"])
    texts = [*(rule for rules in render.RULES.values() for rule in rules), *((cli.PAGES / f"{role}.md").read_text() for role in state.ROLES)]
    templates = [re.findall(r"(\w+):", found) for text in texts for found in re.findall(r"order: \{([^}]*)\}", text)]
    assert templates
    for keys in templates:
        assert required <= set(keys), keys


def test_every_block_sends_a_new_order_to_drive_order_unfiled_for_the_projects_and_workstreams_in_use() -> None:
    drive = sample()
    drive["orders"]["o1"] |= {"project": "Platform", "workstream": "SandSQL"}
    for role in state.ROLES:
        text = render.context(drive, role)
        assert render.FILED in text
        assert "SandSQL" not in text
