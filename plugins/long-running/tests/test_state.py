import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from drive import state
from drive.state import DriveError

REFERENCE = datetime(2026, 11, 9, 14, 0).astimezone()


def test_root_prefers_explicit_then_xdg_then_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LONG_RUNNING_STATE", str(tmp_path / "explicit"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert state.root() == tmp_path / "explicit"
    monkeypatch.delenv("LONG_RUNNING_STATE")
    assert state.root() == tmp_path / "xdg" / "long-running"
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert state.root() == tmp_path / ".local" / "state" / "long-running"


def make(name: str, session: str | None = None, started: str | None = None) -> None:
    data = state.new_drive(name, "coordinator", session, "/work", None)
    if started:
        data["roles"]["coordinator"]["started"] = started
    state.create(data)


def test_edit_saves_atomically_and_leaves_no_temp(state_root: Path) -> None:
    make("demo")
    with state.edit("demo") as data:
        data["orders"]["o1"] = {"text": "ship it", "jobs": []}
    assert state.load("demo")["orders"]["o1"]["text"] == "ship it"
    assert not (state_root / "demo" / "drive.json.tmp").exists()
    assert (state_root / "demo" / ".lock").exists()


def test_load_gives_each_picture_drawn_before_pictures_kept_their_source_its_kept_question_or_pr_fence_once(state_root: Path) -> None:
    make("demo")
    drive = state_root / "demo"
    (drive / "asks").mkdir()
    (drive / "asks" / "j1.md").write_text("ASK Which entry point?\nShow me:\n```rust\nfn main() {}\n```\n- Keep main\n")
    pr_card = {"card": {"prs": [{"description": "## Show me\n\n```sand\nbrowser::click(@page, @b) -> @r\n```\n"}]}}
    (drive / "cards.json").write_text(json.dumps({"pr_cards": [pr_card], "questions": [], "source": "live", "team_url": ""}))
    asked, landed = drive / "pictures" / "e133bc15ea5e3141", drive / "pictures" / "c704dc0a0eebbf8f"
    for home in (asked, landed):
        home.mkdir(parents=True)
        (home / "show-me.png").write_bytes(b"\x89PNG\r\n")
    state.load("demo")
    assert json.loads((asked / "source.json").read_text()) == {"kind": "code:rust", "source": "fn main() {}\n"}
    assert json.loads((landed / "source.json").read_text()) == {"kind": "code:sand", "source": "browser::click(@page, @b) -> @r\n"}
    (asked / "source.json").unlink()
    state.load("demo")
    assert not (asked / "source.json").exists()


def test_load_rebuilds_from_his_stored_card_the_ask_file_a_question_never_kept_word_for_word_once(state_root: Path) -> None:
    data = state.new_drive("demo", "coordinator", None, "/work", None)
    data["jobs"]["j1"] = state.new_job("o1", "design", "row counts", [], "2026-11-10T21:00:00-08:00")
    data["jobs"]["j1"]["questions"] = [state.new_question(None, "Should row counts be exact?", "2026-11-10T21:00:00-08:00") | {"waits": "demo:j1-7"}]
    state.create(data)
    drive = state_root / "demo"
    shown = drive / "pictures" / "0123456789abcdef"
    shown.mkdir(parents=True)
    (shown / "show-me.png").write_bytes(b"\x89PNG\r\n")
    (shown / "source.json").write_text(json.dumps({"kind": "code:sql", "source": "select count(*) from alerts\n"}))
    body = f"*This question is being redone:* new numbers\nCounting reads every row.\nRecommended: exact.\n*Show me*\nOne query per table:\n![Show me]({shown / 'show-me.png'})\n![dash](/nowhere/dash.png)"
    card = {"title": "Should row counts be exact?", "body": body, "options": ["Exact: one query per table", "Estimate: the planner's numbers"], "recommended": "Exact: one query per table", "waits": "demo:j1-7"}
    (drive / "cards.json").write_text(json.dumps({"pr_cards": [], "questions": [{"card": card}], "source": "live", "team_url": ""}))
    kept = drive / "asks" / "j1-7.md"
    assert state.load("demo")["jobs"]["j1"]["questions"][0]["file"] == str(kept)
    assert kept.read_text() == (
        "ASK Should row counts be exact?\nCounting reads every row.\nRecommended: exact.\nShow me:\nOne query per table:\n```sql\n"
        "select count(*) from alerts\n```\n![dash](/nowhere/dash.png)\n- Exact: one query per table (recommended)\n"
        "- Estimate: the planner's numbers\nFindings:\nDirect fix: none: rebuilt from the posted card\n"
    )
    kept.unlink()
    assert state.load("demo")["jobs"]["j1"]["questions"][0]["file"] is None
    assert not kept.exists()


def test_load_labels_his_old_taps_approving_a_pr_in_the_coordinators_checkout_once(state_root: Path, backfilled: list[str]) -> None:
    make("demo")
    state.load("demo")
    state.load("demo")
    assert backfilled == ["/work"]
    state.create(state.new_drive("talk", "communicator", None, "/talk", None))
    state.load("talk")
    assert backfilled == ["/work"]


def test_load_drops_the_answers_a_refused_ask_kept_before_the_settled_check_skipped_his_taps_approving_a_pr(state_root: Path) -> None:
    data = state.new_drive("demo", "coordinator", None, "/work", None)
    tapped = {"title": "Which store holds a run's files?", "answers": [{"id": "a1" * 20, "title": "Tap: #50203, a run's files move to the store"}]}
    data["jobs"]["j1"] = state.new_job("o1", "design", "run files", [], "2026-11-10T21:00:00-08:00") | {"may_settle": tapped}
    state.create(data)
    assert state.load("demo")["jobs"]["j1"]["may_settle"] is None
    assert state.load("demo")["jobs"]["j1"]["may_settle"] is None
    kept = {"title": "Which store holds a run's files?", "answers": [{"id": "b2" * 20, "title": "Which store holds the blobs?"}]}
    with state.edit("demo") as data:
        data["jobs"]["j1"]["may_settle"] = kept
    assert state.load("demo")["jobs"]["j1"]["may_settle"] == kept


def test_a_job_saved_with_one_question_and_card_reads_as_a_list_of_its_questions_once(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    base = {"order": "o1", "said": "Which store?", "updated": "2026-11-09T14:00:00-08:00"}
    data["jobs"] = {
        "j1": base | {"question": "/asks/j1-a.md", "card": "https://acme.slack.com/archives/D1/p1", "unsettled": "ac61348: another subject"},
        "j2": base | {"question": None, "card": None},
        "j3": base | {"card": "https://acme.slack.com/archives/D1/p3", "questions": []},
    }
    state.create(data)
    jobs = state.load("demo")["jobs"]
    assert jobs["j1"]["questions"] == [state.new_question("/asks/j1-a.md", "Which store?", base["updated"], "ac61348: another subject", "https://acme.slack.com/archives/D1/p1")]
    assert jobs["j2"]["questions"] == []
    assert jobs["j3"]["questions"] == []


def test_an_answer_saved_before_answers_listed_waiting_jobs_reads_with_no_job_to_mark(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    moment = "2026-11-09T14:00:00-08:00"
    data["answers"] = {"a1": {"words": "Tree", "link": "https://acme.slack.com/archives/D1/p1", "at": moment, "said": moment, "cards": {"demo:j2-7": {"mark": "stands"}}}}
    state.create(data)
    assert state.load("demo")["answers"]["a1"]["jobs"] == {}


def test_a_drive_saved_with_one_mail_cursor_per_run_reads_it_as_the_mark_of_each_job_on_that_run(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    base = {"order": "o1", "said": None, "updated": "2026-11-11T08:00:00-08:00", "where": "remote"}
    data["jobs"] = {"j1": base | {"run": "run_a"}, "j2": base | {"run": "run_a"}, "j3": base | {"run": "run_b"}, "j4": base | {"where": "local"}}
    data["lane_mail"] = {"run_a": 41}
    state.create(data)
    loaded = state.load("demo")
    assert {job_id: job["mail_seen"] for job_id, job in loaded["jobs"].items()} == {"j1": 41, "j2": 41, "j3": 0, "j4": 0}
    assert "lane_mail" not in loaded


def test_a_mail_cursor_an_old_watch_writes_again_after_the_migration_raises_each_mark_on_its_run_and_never_lowers_one(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    base = {"order": "o1", "said": None, "updated": "2026-11-11T08:00:00-08:00", "where": "remote"}
    data["jobs"] = {"j1": base | {"run": "run_a"}, "j2": base | {"run": "run_a"}, "j3": base | {"run": "run_b"}}
    data["lane_mail"] = {"run_a": 41}
    state.create(data)
    migrated = state.load("demo")
    migrated["jobs"]["j2"]["mail_seen"] = 45
    migrated["jobs"]["j3"]["mail_seen"] = 12
    migrated["lane_mail"] = {"run_a": 43, "run_b": 9}
    state.save("demo", migrated)
    loaded = state.load("demo")
    assert {job_id: job["mail_seen"] for job_id, job in loaded["jobs"].items()} == {"j1": 43, "j2": 45, "j3": 12}
    assert "lane_mail" not in loaded


def test_a_job_whose_agent_is_an_orca_dispatch_reads_as_a_lane_on_this_mac_read_through_that_dispatch(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    base = {"order": "o1", "said": None, "updated": "2026-11-11T08:00:00-08:00", "where": "local", "session": "s1"}
    data["jobs"] = {"j1": base | {"agent": "ctx_ab821100f80b"}, "j2": base | {"agent": "agent-9"}}
    state.create(data)
    jobs = state.load("demo")["jobs"]
    assert {field: jobs["j1"][field] for field in ("agent", "dispatch", "where", "route", "workspace", "environment")} == {
        "agent": None,
        "dispatch": "ctx_ab821100f80b",
        "where": "remote",
        "route": "local",
        "workspace": None,
        "environment": None,
    }
    assert "run" not in jobs["j1"]
    assert (jobs["j2"]["agent"], jobs["j2"]["where"], jobs["j2"]["route"]) == ("agent-9", "local", None)


def test_an_order_whose_thread_is_its_own_post_outside_his_dm_and_watched_channels_reads_with_its_dm_thread(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    data["slack"].update(channel="D123", watch=["C0B75AL4XEW"])
    in_dm = {"posted": "https://acme.slack.com/archives/D123/p1794200040000500?thread_ts=1794200000.000100&cid=D123", "orphan_said": None}
    shared = {"posted": "https://acme.slack.com/archives/C0SHARED1/p1794200090000700", "orphan_said": None}
    watched = {"posted": "https://acme.slack.com/archives/C0B75AL4XEW/p1794200095000800", "orphan_said": None}
    base = {"text": "tell the stopped people", "from": "slack:D123/1794200030.000400", "jobs": [], "promised": None, "updated": "2026-11-10T18:46:23-08:00"}
    data["orders"] = {
        "o1": base | {"thread": "1794200090.000700", "parts": {"answer": in_dm, "tell them": shared}},
        "o2": base | {"thread": "1794200095.000800", "parts": {"answer": in_dm, "tell them": watched}},
        "o3": base | {"thread": "1794200000.000100", "parts": {"answer": in_dm, "tell them": shared}},
    }
    state.create(data)
    orders = state.load("demo")["orders"]
    assert {order_id: order["thread"] for order_id, order in orders.items()} == {
        "o1": "1794200000.000100",
        "o2": "1794200095.000800",
        "o3": "1794200000.000100",
    }


def test_edit_discards_changes_when_the_body_raises(state_root: Path) -> None:
    make("demo")
    with pytest.raises(RuntimeError), state.edit("demo") as data:
        data["orders"]["o1"] = {"text": "never saved"}
        raise RuntimeError
    assert state.load("demo")["orders"] == {}


def test_load_of_a_missing_drive_names_it(state_root: Path) -> None:
    with pytest.raises(DriveError, match="no drive named ghost"):
        state.load("ghost")


def test_next_id_is_monotonic_past_gaps() -> None:
    assert state.next_id({}, "o") == "o1"
    assert state.next_id({"o1": {}, "o2": {}, "o10": {}}, "o") == "o11"
    assert state.next_id({"j4": {}}, "j") == "j5"


def test_resolve_prefers_flag_then_env(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make("alpha")
    make("beta")
    monkeypatch.setenv("LONG_RUNNING_DRIVE", "beta")
    assert state.resolve("alpha") == "alpha"
    assert state.resolve() == "beta"


def test_resolve_rejects_an_unknown_named_drive(state_root: Path) -> None:
    make("alpha")
    with pytest.raises(DriveError, match="no drive named ghost"):
        state.resolve("ghost")


def test_resolve_finds_the_newest_drive_bound_to_this_session(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make("old", "me", "2026-11-08T10:00:00-08:00")
    make("new", "me", "2026-11-09T10:00:00-08:00")
    make("other", "someone-else")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "me")
    assert state.resolve() == "new"
    with state.edit("other") as data:
        data["roles"]["communicator"] = {"session": "me", "cwd": "/work", "started": "2026-11-09T11:00:00-08:00"}
    assert state.resolve() == "other"
    assert state.session_role(state.load("other"), "me") == "communicator"


def test_resolving_a_sessions_drive_loads_no_drive_it_is_not_bound_to(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make("mine", "me")
    make("other", "someone-else")
    loaded = []
    monkeypatch.setattr(state, "load", lambda name: loaded.append(name) or {})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "me")
    assert (state.resolve(), state.session_drives("someone-else"), loaded) == ("mine", ["other"], [])


def test_a_drive_saved_before_sub_coordinators_loads_with_none_and_a_sub_coordinator_holds_the_coordinator_role_for_its_project_alone(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make("demo", "top", "2026-11-11T01:00:00-08:00")
    saved = json.loads(state.state_path("demo").read_text())
    del saved["subcoordinators"]
    saved["orders"] = {
        order_id: {"text": text, "jobs": [], "parts": {}, "promised": None, "project": project}
        for order_id, project, text in (("o1", "Iris", "ship the run page"), ("o2", "Platform", "fix the alert"), ("o3", None, "a new order"))
    }
    state.state_path("demo").write_text(json.dumps(saved))
    with state.edit("demo") as data:
        assert data["subcoordinators"] == {}
        data["subcoordinators"]["Iris"] = {"session": "iris", "terminal": None, "cwd": "/work", "started": "2026-11-11T02:00:00-08:00"}
    data = state.load("demo")
    assert [state.session_role(data, session) for session in ("top", "iris", "nobody")] == ["coordinator", "coordinator", None]
    assert [state.session_project(data, session) for session in ("top", "iris")] == [None, "Iris"]
    assert (state.session_drives("iris"), state.session_drives("top")) == (["demo"], ["demo"])
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris")
    assert state.resolve() == "demo"
    orders = data["orders"]
    assert [order_id for order_id, order in orders.items() if state.hears(data, order, "Iris")] == ["o1"]
    assert [order_id for order_id, order in orders.items() if state.hears(data, order, None)] == ["o2", "o3"]
    assert [state.order_lane(data, order) for order in orders.values()] == ["iris", "root", "root"]
    assert [state.project_lane(project) for project in ("Iris", "The drive", "SandSQL", "Platform / infra")] == ["iris", "the-drive", "sandsql", "platform-infra"]
    assert list(state.bound_sessions(data)) == ["coordinator", "sub-coordinator Iris"]


def test_resolve_falls_back_to_the_only_drive(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make("solo", "someone-else")
    assert state.resolve() == "solo"


def test_resolve_lists_drives_when_ambiguous(state_root: Path) -> None:
    make("alpha")
    make("beta")
    with pytest.raises(DriveError, match="alpha, beta"):
        state.resolve()


def test_resolve_with_no_drives_says_how_to_start(state_root: Path) -> None:
    with pytest.raises(DriveError, match="drive start"):
        state.resolve()


def test_events_append_and_read_from_an_offset(state_root: Path) -> None:
    make("demo")
    state.append_event("demo", "tap", label="Approve", pr="o/n#1")
    middle = state.events_end("demo")
    state.append_event("demo", "shown", pr="o/n#2")
    everything = list(state.read_events("demo"))
    assert [event["kind"] for event, _ in everything] == ["tap", "shown"]
    assert everything[0][0]["label"] == "Approve"
    assert everything[0][1] == middle
    assert everything[1][1] == state.events_end("demo")
    assert [event["kind"] for event, _ in state.read_events("demo", middle)] == ["shown"]


def test_read_events_stops_before_a_partial_line(state_root: Path) -> None:
    make("demo")
    state.append_event("demo", "tap")
    complete = state.events_end("demo")
    with open(state.events_path("demo"), "a") as handle:
        handle.write('{"kind": "half')
    read = list(state.read_events("demo"))
    assert len(read) == 1
    assert read[0][1] == complete


def test_read_events_without_a_log_yields_nothing(state_root: Path) -> None:
    make("demo")
    assert list(state.read_events("demo")) == []
    assert state.events_end("demo") == 0


def test_parse_due_clock_later_today() -> None:
    assert state.parse_due("16:30", REFERENCE) == REFERENCE.replace(hour=16, minute=30)


def test_parse_due_clock_already_passed_is_tomorrow() -> None:
    assert state.parse_due("9:15", REFERENCE) == REFERENCE.replace(hour=9, minute=15) + timedelta(days=1)


def test_parse_due_reads_the_twelve_hour_clock_the_drive_prints() -> None:
    assert state.parse_due("4:30 PM", REFERENCE) == REFERENCE.replace(hour=16, minute=30)
    assert state.parse_due("12:05 am", REFERENCE) == REFERENCE.replace(hour=0, minute=5) + timedelta(days=1)


def test_parse_due_offsets() -> None:
    assert state.parse_due("+30m", REFERENCE) == REFERENCE + timedelta(minutes=30)
    assert state.parse_due("+2h", REFERENCE) == REFERENCE + timedelta(hours=2)
    assert state.parse_due("+1d", REFERENCE) == REFERENCE + timedelta(days=1)


def test_parse_due_iso_keeps_its_offset() -> None:
    due = state.parse_due("2026-11-10T09:30:00-08:00", REFERENCE)
    assert due.isoformat() == "2026-11-10T09:30:00-08:00"


def test_parse_due_naive_iso_is_local() -> None:
    assert state.parse_due("2026-11-10T09:30", REFERENCE).tzinfo is not None


def test_parse_due_rejects_nonsense() -> None:
    with pytest.raises(DriveError, match="HH:MM"):
        state.parse_due("soon", REFERENCE)


@pytest.mark.parametrize(
    "ref",
    [
        "https://github.com/Forge-AI/monorepo/pull/123",
        "https://github.com/Forge-AI/monorepo/pull/123/files",
        "github.com/Forge-AI/monorepo/pull/123",
        "Forge-AI/monorepo#123",
    ],
)
def test_pr_key(ref: str) -> None:
    assert state.pr_key(ref) == "Forge-AI/monorepo#123"


def test_pr_key_rejects_non_pr_urls() -> None:
    with pytest.raises(DriveError):
        state.pr_key("https://github.com/Forge-AI/monorepo/issues/123")


def test_pr_url_round_trips() -> None:
    assert state.pr_url("yasyf/cc-skills#9") == "https://github.com/yasyf/cc-skills/pull/9"


def test_a_moved_time_is_kept_only_by_lines_after_it_was_set() -> None:
    at = lambda minutes: REFERENCE + timedelta(minutes=minutes)
    order = {"due": state.stamp(at(0)), "promised": state.stamp(at(-60)), "kept": None, "withdrawn": None, "next_due": None, "next_set": None}
    state.promise(order, state.stamp(at(120)), at(30))
    state.keep(order, [at(-10), at(10)])
    assert (order["due"], order["promised"], order["kept"]) == (state.stamp(at(120)), state.stamp(at(30)), None)
    state.keep(order, [at(-10), at(10), at(40)])
    assert (order["promised"], order["kept"]) == (at(40).isoformat(), None)
    state.keep(order, [at(-10), at(10), at(40), at(50)])
    assert order["kept"] == at(50).isoformat()


def test_the_line_of_a_report_closing_the_order_keeps_the_time_it_told_him_and_a_line_before_the_report_does_not() -> None:
    at = lambda minutes: REFERENCE + timedelta(minutes=minutes)
    order = {"due": None, "promised": None, "kept": None, "withdrawn": None, "next_due": None, "next_set": None, "due_said": None}
    state.promise(order, state.stamp(at(28)), at(0))
    state.keep(order, [at(16)])
    state.keep(order, [at(16)], announced=False)
    assert (order["due"], order["promised"], state.owed(order)) == (state.stamp(at(28)), at(16).isoformat(), True)
    state.keep_closing(order, [at(16)], at(17))
    assert state.owed(order)
    state.keep_closing(order, [at(16)], at(15))
    assert (order["kept"], order["next_due"], state.owed(order)) == (at(16).isoformat(), None, False)


MONOREPO = "Forge-AI/monorepo"


def judged(*readers: tuple[str, str]) -> dict:
    data = state.new_drive("demo", "coordinator", None, "/work", None)
    for number, order_id in ((4289, "o226"), (4279, "o226"), (4284, "o226"), (4222, "o226"), (4302, "o359")):
        key = f"{MONOREPO}#{number}"
        data["prs"][key] = state.new_pr(state.pr_url(key), "j622", order_id) | {"head": "abc"}
    data["prs"]["yasyf/cc-skills#4289"] = state.new_pr(state.pr_url("yasyf/cc-skills#4289"), "j9", "o359")
    for job_id, said in readers:
        data["jobs"][job_id] = state.new_job("o226", "reader", "READY-row check", [], state.stamp(REFERENCE))
        state.take_result(data, job_id, said)
    return data


def unready(data: dict) -> list[str]:
    return [key for key, pr in data["prs"].items() if pr["verdict"] and not pr["verdict"]["ready"]]


def test_a_new_pr_waits_on_his_tap() -> None:
    pr = state.new_pr(state.pr_url(f"{MONOREPO}#1"), "j1", "o1")
    assert (pr["wants_tap"], state.owed_step(pr), state.check_status(pr)) == (True, "card", "checking")


def test_a_readers_not_ready_stands_on_the_head_it_checked_of_each_pr_it_names_and_not_on_a_pr_its_reason_cites() -> None:
    data = judged(("j740", "NOT READY #4289: Releasable alone needs #4279's api live first; the store path is hard-coded"))
    verdict = data["prs"][f"{MONOREPO}#4289"]["verdict"]
    assert verdict == {"job": "j740", "head": "abc", "ready": False, "why": "Releasable alone needs #4279's api live first", "at": verdict["at"], "said": False}
    assert (data["jobs"]["j740"]["state"], data["jobs"]["j740"]["checks"]) == ("done", {f"{MONOREPO}#4289": "abc"})
    assert unready(data) == [f"{MONOREPO}#4289"]
    assert state.owed_step(data["prs"][f"{MONOREPO}#4289"]) == "rework"
    assert state.owed_step(data["prs"][f"{MONOREPO}#4279"]) == "card"


def test_a_verdict_judges_the_head_its_reader_was_started_on() -> None:
    data = judged()
    data["jobs"]["j740"] = state.new_job("o226", "reader", "READY-row check", [], state.stamp(REFERENCE)) | {"checks": {f"{MONOREPO}#4289": "old"}}
    state.take_result(data, "j740", "READY-OK #4289")
    pr = data["prs"][f"{MONOREPO}#4289"]
    assert (pr["verdict"]["head"], state.check_status(pr), state.owed_step(pr)) == ("old", "checking", "card")


def test_a_check_status_is_passed_or_rework_on_its_head_and_recheck_once_the_head_moves_under_a_not_ready() -> None:
    data = judged(("j740", "NOT READY #4289: needs #4279 live first"))
    pr = data["prs"][f"{MONOREPO}#4289"]
    assert state.check_status(pr) == "rework"
    pr["head"] = "def"
    assert (state.check_status(pr), state.owed_step(pr)) == ("recheck", "recheck")
    data["jobs"]["j798"] = state.new_job("o226", "reader", "READY-row check", [], state.stamp(REFERENCE))
    state.take_result(data, "j798", "READY-OK #4289")
    assert (state.check_status(pr), state.owed_step(pr)) == ("passed", "card")


def test_only_a_later_readers_ready_naming_the_pr_clears_its_not_ready() -> None:
    data = judged(
        ("j740", "NOT READY #4289: Releasable alone needs #4279's api live first"),
        ("j771", "NOT READY #4279: moves no files into the store"),
        ("j805", "READY-OK #4222; NOT READY #4284: a review finding has no answer"),
    )
    assert unready(data) == [f"{MONOREPO}#{n}" for n in (4289, 4279, 4284)]
    data["jobs"]["j798"] = state.new_job("o226", "reader", "READY-row check", [], state.stamp(REFERENCE))
    state.take_result(data, "j798", "READY-OK #4279 #4289")
    assert unready(data) == [f"{MONOREPO}#4284"]
    assert data["prs"]["yasyf/cc-skills#4289"]["verdict"] is None


def test_a_tap_of_his_owes_no_rework_while_the_verdict_stays_kept() -> None:
    data = judged(("j740", "NOT READY #4289: Releasable alone needs #4279's api live first"))
    pr = data["prs"][f"{MONOREPO}#4289"]
    pr["tap"] = {"label": "Approve", "at": state.stamp(REFERENCE), "head": "abc", "source": "slack"}
    assert (state.owed_step(pr), pr["verdict"]["job"]) == ("card", "j740")


def test_a_builders_ready_and_a_readers_other_answers_judge_nothing() -> None:
    data = judged(("j716", "DONE 0 of 3 pass: #4249 two design decisions unanswered; #4289 quote cut"))
    data["jobs"]["j9"] = state.new_job("o226", "builder", "fix", [], state.stamp(REFERENCE))
    state.take_result(data, "j9", f"READY https://github.com/{MONOREPO}/pull/4289")
    assert not any(pr["verdict"] for pr in data["prs"].values())
    assert data["jobs"]["j9"]["state"] == "waiting"


def test_a_bare_number_two_repositories_share_judges_the_pr_on_the_readers_order() -> None:
    assert state.named_prs(judged(), "o226", "#4289") == [f"{MONOREPO}#4289"]
    assert state.named_prs(judged(), "o359", "#4289 and Forge-AI/monorepo#4302") == ["yasyf/cc-skills#4289", f"{MONOREPO}#4302"]


def test_load_turns_each_kept_not_ready_into_a_verdict_on_no_head_with_no_github_read_and_a_read_dates_it_onto_the_head_unless_it_committed_since(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from drive import github

    data = judged()
    for job_id, said, minutes in (("j740", "NOT READY #4289: needs #4279 live first", 0), ("j798", "READY-OK #4289", 10), ("j803", "NOT READY #4289: a second check is left", 20), ("j804", "NOT READY #4279: no store", 20)):
        data["jobs"][job_id] = state.new_job("o226", "reader", "READY-row check", [], state.stamp(REFERENCE)) | {
            "state": "done",
            "said": said,
            "updated": state.stamp(REFERENCE + timedelta(minutes=minutes)),
        }
    for pr in data["prs"].values():
        for field in ("verdict", "check", "ready_said", "pulled"):
            del pr[field]
        pr["not_ready"] = None
    data["prs"][f"{MONOREPO}#4302"]["state"] = "merged"
    state.create(data)
    asked: list[list[str]] = []
    monkeypatch.setattr(github, "run", lambda cmd, cwd=None, timeout=120: asked.append(cmd) or "{}")
    kept = state.load("demo")
    assert asked == []
    rework, recheck = kept["prs"][f"{MONOREPO}#4289"], kept["prs"][f"{MONOREPO}#4279"]
    assert (rework["verdict"]["why"], rework["verdict"]["head"], rework["verdict"]["said"], state.owed_step(rework)) == ("a second check is left", None, False, "recheck")
    assert (kept["prs"][f"{MONOREPO}#4302"]["verdict"], unready(kept)) == (None, [f"{MONOREPO}#4289", f"{MONOREPO}#4279"])
    state.date_verdict(kept, f"{MONOREPO}#4289", "abc", state.stamp(REFERENCE))
    state.date_verdict(kept, f"{MONOREPO}#4279", "abc", state.stamp(REFERENCE + timedelta(minutes=30)))
    assert (rework["verdict"]["head"], kept["jobs"]["j803"]["checks"], state.owed_step(rework)) == ("abc", {f"{MONOREPO}#4289": "abc"}, "rework")
    assert (recheck["verdict"]["head"], kept["jobs"]["j804"]["checks"], state.owed_step(recheck)) == (None, {f"{MONOREPO}#4279": None}, "recheck")
    assert not any("not_ready" in pr for pr in kept["prs"].values())


def test_resolving_the_sessions_drive_reads_nothing_from_github_while_another_drive_still_needs_its_verdicts_rebuilt(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from drive import github

    old = judged()
    old["name"] = "rs-proof"
    for number in range(1, 146):
        key = f"{MONOREPO}#{number}"
        old["prs"][key] = {field: value for field, value in state.new_pr(state.pr_url(key), "j622", "o226").items() if field not in ("verdict", "check", "ready_said", "pulled")}
    state.create(old)
    make("durable-agents", session="the-session")
    asked: list[list[str]] = []
    monkeypatch.setattr(github, "run", lambda cmd, cwd=None, timeout=120: asked.append(cmd) or "{}")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "the-session")
    monkeypatch.delenv("LONG_RUNNING_DRIVE", raising=False)
    assert (state.resolve(), state.session_drives("the-session"), asked) == ("durable-agents", ["durable-agents"], [])


def test_two_questions_are_the_same_whatever_their_case_and_spacing_and_an_untitled_one_matches_none() -> None:
    assert state.same_question("What runs a watch's  Sand function?", "what runs a watch's Sand function?")
    assert not state.same_question("What runs a watch's Sand function?", "What runs a watch's Sand function each interval?")
    assert not state.same_question(None, None)
