from datetime import timedelta
from pathlib import Path

from drive import cli, render, state

HOOK_CHARS = 10_000


def order(
    text: str,
    order_state: str = "working",
    jobs: list[str] | None = None,
    created: str = "2026-11-09T08:00:00-08:00",
) -> dict:
    return {
        "text": text,
        "from": "terminal",
        "thread": None,
        "state": order_state,
        "jobs": jobs or [],
        "parts": {},
        "proof": None,
        "reporters": [],
        "note": None,
        "created": created,
        "updated": created,
        "last_line": None,
        "project": None,
        "workstream": None,
        "title": None,
        "incident": False,
        "owner": None,
        "priority": None,
    }


def sample() -> dict:
    drive = state.new_drive("demo", "coordinator", "s1", "/work")
    drive["orders"] = {
        "o1": order("Ship the drive rewrite and install it", jobs=["j1"]),
        "o2": order("Already shipped", order_state="done"),
        "o3": order("Answer whether the cache is warm " + "x" * 200, order_state="open"),
    }
    drive["jobs"] = {
        "j1": {
            "order": "o1",
            "kind": "builder",
            "goal": "rewrite long-running",
            "agent": "agent-7",
            "session": "s1",
            "where": "local",
            "repo": "yasyf/cc-skills",
            "branch": "lr-rewrite",
            "worktree": None,
            "prs": ["https://github.com/yasyf/cc-skills/pull/9"],
            "questions": [],
            "state": "running",
            "said": None,
            "undelivered": [],
            "queued": [],
            "created": "2026-11-09T08:00:00-08:00",
            "updated": "2026-11-09T08:00:00-08:00",
        }
    }
    drive["prs"] = {
        "yasyf/cc-skills#9": state.new_pr("https://github.com/yasyf/cc-skills/pull/9", "j1", "o1")
        | {"title": "New: drive", "card_state": None, "head": "abc1234", "checks": "pass", "updated": "2026-11-09T08:00:00-08:00"}
    }
    return drive


def test_coordinator_context_gives_rules_reads_and_monitor_but_no_order_list() -> None:
    text = render.context(sample(), "coordinator")
    assert text.startswith("You are the coordinator of drive demo: ")
    assert "The communicator: not started." in text.splitlines()[0]
    assert "cci: run cci drive use demo, and no cci subscribe: your cci alias demo-coordinator owns your subscription." in text
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
    assert "cci: run cci drive use demo, and no cci subscribe: your cci alias demo-communicator owns your subscription." in text
    assert "o1" not in text
    for rule in render.COMMUNICATOR_RULES:
        assert rule in text
    for row in ("+1 react", "slack_dm_status", "background subagent", "2 to 4 options", "merge:", "defect record", "Sent."):
        assert row in text
    assert render.FIRST_CALL["coordinator"] not in text
    assert text.endswith(render.FIRST_CALL["communicator"])


def test_the_meta_session_and_communicator_read_one_definition_of_drive_tooling() -> None:
    drive = sample()
    text = render.context(drive, "meta")
    assert text.startswith(f"You are the meta session of drive demo: you hold drive tooling, {state.DRIVE_TOOLING}.")
    assert "The communicator: not started. The coordinator: session s1." in text.splitlines()[0]
    assert text.splitlines()[1] == "Hold no Slack channel."
    assert "cci: run cci drive use demo, and no cci subscribe: your cci alias demo-meta owns your subscription." in text
    for rule in render.META_RULES:
        assert rule in text
    assert text.endswith(render.FIRST_CALL["meta"])
    assert sum(state.DRIVE_TOOLING in rule for rule in render.META_RULES) == 1
    assert sum(state.DRIVE_TOOLING in rule for rule in render.COMMUNICATOR_RULES) == 1
    drive["roles"]["meta"] = {"session": "s3", "cwd": "/work", "started": "2026-11-11T12:40:00-08:00"}
    assert "The meta session: session s3." in render.context(drive, "coordinator").splitlines()[0]


def test_every_role_block_carries_the_rules_its_repositorys_long_running_toml_sets(tmp_path: Path) -> None:
    drive = state.new_drive("demo", "coordinator", "s1", str(tmp_path))
    bare = render.rules_digests(drive, "coordinator")
    assert render.REPO_RULES_HEADING not in render.context(drive, "coordinator")
    (tmp_path / ".claude").mkdir()
    (tmp_path / state.REPO_RULES).write_text('[[rule]]\nid = "staging-claim"\ntext = "Post in the staging channel before you freeze it."\n')
    drawn = f"{render.REPO_RULES_HEADING}\n- staging-claim: Post in the staging channel before you freeze it."
    assert [role for role in state.ROLES if drawn not in render.context(drive, role)] == []
    assert render.rules_digests(drive, "coordinator") != bare


def test_every_role_reads_a_question_proposing_a_way_as_his_order_and_meta_reads_his_ruling_to_keep_simplifying() -> None:
    contexts = {role: render.context(sample(), role) for role in state.ROLES}
    assert '"why not just X?"' in contexts["communicator"]
    assert [role for role in state.ROLES if '"why not just X?"' not in (cli.PAGES / f"{role}.md").read_text()] == []
    assert [role for role, text in contexts.items() if "in geernal keeps implifying all this tuff" in text] == ["meta"]


def test_every_agent_opens_on_the_skills_one_rule_that_his_words_newest_first_outrank_every_default() -> None:
    skill = (cli.PAGES.parent / "SKILL.md").read_text()
    rule = next(line.removeprefix("- ") for line in skill.splitlines() if line.startswith("- His words are the spec:"))
    agents = sorted((cli.PAGES.parents[2] / "agents").glob("*.md"))
    assert agents
    assert [agent.name for agent in agents if not " ".join(agent.read_text().partition("\n---\n")[2].split()).startswith(rule)] == []


def test_each_page_naming_drive_tooling_carries_its_one_definition() -> None:
    pages = [cli.PAGES / "communicator.md", cli.PAGES / "meta.md", cli.PAGES / "concepts.md"]
    assert [page.name for page in pages if state.DRIVE_TOOLING not in page.read_text()] == []


def live_sized() -> dict:
    drive = sample()
    for n in range(785):
        order_id, job_id, still_open = f"o{n + 10}", f"j{n + 10}", n < 388
        filing = {"project": f"Project {n % 74 % 15}", "workstream": f"Workstream {n % 74}"}
        drive["orders"][order_id] = order(f"order {n} " + "x" * 200, "open" if still_open else "done", jobs=[job_id]) | filing
        drive["jobs"][job_id] = drive["jobs"]["j1"] | {"order": order_id, "goal": f"goal {n} " + "y" * 200, "prs": []}
    drive["name"] = "durable-agents"
    drive["slack"]["watch"] = [f"C0B75AL{n:04d}" for n in range(8)]
    drive["roles"] = {role: drive["roles"]["coordinator"] | {"session": f"{n:08x}-ce81-4ce1-b1dc-8bbdf3e90a5e"} for n, role in enumerate(state.ROLES)}
    return drive


def test_every_session_start_block_fits_claude_codes_hook_limit_on_a_drive_as_large_as_the_live_one() -> None:
    small, large = sample(), live_sized()
    blocks = {role: render.context(large, role) for role in state.ROLES} | {"sub-coordinator": render.context(large, "coordinator", "Project 1")}
    assert {seat: len(block) for seat, block in blocks.items() if len(block) >= HOOK_CHARS} == {}
    for role in state.ROLES:
        renamed = render.context(large, role).replace(str(state.drive_dir(large["name"])), str(state.drive_dir(small["name"])))
        assert [line for line in renamed.splitlines()[3:] if not line.startswith("Your brief: ")] == [line for line in render.context(small, role).splitlines()[3:] if not line.startswith("Your brief: ")]
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


def test_an_alert_channel_held_by_a_seat_is_that_sub_coordinators_and_not_the_top_coordinators() -> None:
    drive = sample()
    drive["slack"]["watch"] = ["C0B75AL4XEW", "C0CAC1HT17W"]
    drive["slack"]["held_by"] = {"C0CAC1HT17W": {"seat": "Platform", "kinds": "alerts"}}
    assert render.hold_line(drive, "coordinator") == "Hold with cc-slack subscribe --kind alerts,releases,posts --channel <id>: C0B75AL4XEW"
    assert render.hold_line(drive, "coordinator", "Platform") == "Hold with cc-slack subscribe --kind alerts --channel <id>: C0CAC1HT17W"
    assert render.hold_line(drive, "coordinator", "Iris") == "Hold no Slack channel: the top coordinator holds the drive's."


def test_hold_line_reads_a_drive_saved_before_watch_existed() -> None:
    drive = sample()
    del drive["slack"]["watch"]
    assert render.hold_line(drive, "coordinator") == "Hold no Slack channel."


def test_the_communicators_hold_line_names_his_dm_as_cc_slack_whoami_names_it_on_a_drive_started_with_no_dm_channel(his_dm: str) -> None:
    drive = state.new_drive("empty", "coordinator", None, "/work")
    assert render.hold_line(drive, "communicator") == f"Hold with slack_watch: his DM {his_dm}"


def test_status_text_lists_each_jobs_pr_urls_and_registered_prs_once() -> None:
    drive = sample()
    drive["jobs"]["j1"]["prs"] = [
        "https://github.com/yasyf/cc-skills/pull/10",
        "https://github.com/yasyf/cc-skills/pull/9",
    ]
    drive["prs"]["yasyf/cc-skills#11"] = drive["prs"]["yasyf/cc-skills#9"] | {
        "url": "https://github.com/yasyf/cc-skills/pull/11"
    }
    lines = render.status_text(drive, {}, drive).splitlines()
    pr_lines = [line for line in lines if line.startswith("    - ")]
    assert pr_lines == [
        "    - https://github.com/yasyf/cc-skills/pull/10",
        '    - yasyf/cc-skills#9 "New: drive" open, checks pass, review none, wants tap, his card never drawn; Not on staging: yasyf/cc-skills has no staging env',
        '    - yasyf/cc-skills#11 "New: drive" open, checks pass, review none, wants tap, his card never drawn; Not on staging: yasyf/cc-skills has no staging env',
    ]


def test_a_pr_line_names_the_stage_its_row_keeps_only_while_the_pr_is_open() -> None:
    unlanded = {"name": "unlanded", "owner": "the session holding its order", "why": "its lander's try at 987c0fe ended not landed", "since": state.stamp(), "woke": None}
    pr = state.new_pr("https://github.com/yasyf/cc-skills/pull/4998", "j1", "o1") | {"stage": unlanded}
    lines = [render.pr_line({"prs": {"yasyf/cc-skills#4998": pr | {"state": now}}}, "yasyf/cc-skills#4998", None) for now in ("open", "merged", "closed")]
    assert ["owed by" in line for line in lines] == [True, False, False]
    assert lines[1].startswith("yasyf/cc-skills#4998 merged, checks pending")


def test_a_merged_prs_red_line_names_each_check_its_lander_merged_past_as_the_default_branchs_where_5709_read_only_merged_checks_fail() -> None:
    outcome = "merged at aa1b2918f68f past test red at 9834316, which main failed on the same 4 tests at 11b003c; long-running@skills 1.0.1238 installed"
    pr = state.new_pr("https://github.com/yasyf/cc-skills/pull/5709", "j1", "o1") | {"lander": {"outcome": outcome}}
    lines = {(now, checks): render.pr_line({"prs": {"yasyf/cc-skills#5709": pr | {"state": now, "checks": checks}}}, "yasyf/cc-skills#5709", None) for now in ("open", "merged") for checks in ("fail", "pass")}
    assert lines["merged", "fail"].startswith(
        "yasyf/cc-skills#5709 merged, checks fail (the lander merged at aa1b2918f68f past test red at 9834316, which main failed on the same 4 tests at 11b003c), review none"
    )
    assert [key for key, line in lines.items() if "the lander" in line] == [("merged", "fail")]


def test_a_monorepo_prs_status_line_says_its_staging_test_and_result_as_his_card_does(named_repo: None) -> None:
    drive = sample()
    proof = "https://buildkite.com/forge/staging/builds/9"
    drive["orders"]["o1"]["parts"][state.STAGING_TEST.format(number=42)] = state.new_part() | {"soak": "staging:sandsql/plat-usw2-staging@f3a6f23059917549d64ca2fdbdb5034f34289019", "posted": proof}
    drive["prs"] = {"Forge-AI/monorepo#42": drive["prs"]["yasyf/cc-skills#9"] | {"url": "https://github.com/Forge-AI/monorepo/pull/42"}}
    assert render.pr_line(drive, "Forge-AI/monorepo#42", None).endswith(f"his card never drawn; Tested on staging: plat-usw2-staging at f3a6f23, passed, [proof]({proof})")


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
    assert data["orders"]["o1"].startswith("o1 working")
    assert "jobs" not in data and "prs" not in data


def test_status_prints_the_moment_he_reads_away_from_the_one_every_hold_reads() -> None:
    drive = sample()
    heard = state.now() - timedelta(minutes=10)
    drive["owner"] = {"heard": state.stamp(heard), "away": state.stamp(heard + state.AWAY_AFTER), "absence": None}
    here = f"He is here until {render.when(drive['owner']['away'])}: last heard {render.when(drive['owner']['heard'])}, and an hour with no word or tap reads him away"
    assert here in render.status_text(drive, {}).splitlines()
    drive["owner"]["away"] = drive["owner"]["heard"]
    assert f"He is away since he said so at {render.when(drive['owner']['heard'])}" in render.status_text(drive, {}).splitlines()
    drive["owner"] |= {"heard": state.stamp(heard - timedelta(hours=2)), "away": state.stamp(heard - timedelta(hours=1))}
    away = f"He is away since {render.when(drive['owner']['away'])}: last heard {render.when(drive['owner']['heard'])}, an hour with no word or tap"
    assert away in render.status_text(drive, {}).splitlines()
    assert render.status_data(drive, {})["owner"] == drive["owner"]
    assert not any(line.startswith("He is") for line in render.status_text(sample(), {}).splitlines())


def test_when_names_the_day_for_other_days() -> None:
    tomorrow = state.now() + timedelta(days=1)
    assert render.when(state.stamp(tomorrow)).startswith(tomorrow.strftime("%b %-d"))


def test_an_incident_orders_line_says_it_is_one() -> None:
    drive = sample()
    drive["orders"]["o3"]["incident"] = True
    assert render.order_line(drive, "o3", drive["orders"]["o3"], 40) == "o3 open incident: Answer whether the cache is warm…"


def test_status_text_names_the_thread_each_orders_lines_go_in() -> None:
    drive = sample()
    drive["orders"]["o1"]["thread"] = ["D123", "1794307360.614569"]
    lines = render.status_text(drive, {}).splitlines()
    assert next(line for line in lines if line.startswith("- o1 ")).startswith(
        "- o1 working, its lines go in D123 thread 1794307360.614569: "
    )
    assert next(line for line in lines if line.startswith("- o3 ")).startswith("- o3 open: ")


def test_every_block_sends_a_new_order_to_drive_order_unfiled_for_the_projects_and_workstreams_in_use() -> None:
    drive = sample()
    drive["orders"]["o1"] |= {"project": "Platform", "workstream": "SandSQL"}
    for role in state.ROLES:
        text = render.context(drive, role)
        assert render.FILED in text
        assert "SandSQL" not in text


def test_a_priority_seats_sub_coordinator_is_told_to_send_its_first_line_within_two_minutes_linking_its_channel() -> None:
    drive = sample()
    drive["orders"]["o1"]["priority"] = state.priority_seat("o1")
    drive["slack"]["channels"][state.priority_seat("o1")] = {"id": "C0PRIO", "name": "him-drive-demo-prio-end-to-end"}
    lines = render.opening(drive, "coordinator", state.priority_seat("o1"))
    assert lines[-1].startswith("Within 2 minutes of binding, before any research, send him your first line on o1: a POST report record to your communicator, posted in the thread his order came from, linking #him-drive-demo-prio-end-to-end C0PRIO,")
    assert "drive order set o1 --thread <that post's permalink>" in lines[-1]
    assert not any(line.startswith("Within 2 minutes") for line in render.opening(drive, "coordinator", "Iris"))


def test_an_incidents_page_mark_shows_the_users_record_who_marked_it_and_when_and_by_whom_he_was_paged() -> None:
    paged = "2026-11-16T08:42:00-08:00"
    marked = {"users": 734700, "users_by": "root", "paged": paged, "paged_by": "the watch"}
    assert render.page_line(marked) == f"real users failing per record 734700, marked by root; paged him at {render.when(paged)} by the watch"
    assert render.page_line({"users": None, "users_by": None, "paged": None, "paged_by": None}) == "not marked as hitting real users; he was not paged"
