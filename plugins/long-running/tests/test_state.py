import fcntl
import itertools
import json
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from drive import stage, state
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


def test_a_drive_a_newer_install_saved_is_refused_before_any_of_it_is_read_while_its_bindings_still_read(state_root: Path) -> None:
    make("demo", session="session-1")
    path = state_root / "demo" / "drive.json"
    assert json.loads(path.read_text())["saved_by"] == state.VERSION
    major, minor, patch = state.VERSION.split(".")
    path.write_text(json.dumps(json.loads(path.read_text()) | {"saved_by": f"{major}.{minor}.{int(patch) - 1}"}))
    assert state.load("demo")["roles"]["coordinator"]["session"] == "session-1"
    newer = f"{major}.{minor}.{int(patch) + 1}"
    path.write_text(json.dumps(json.loads(path.read_text()) | {"saved_by": newer, "orders": {"o1": {"parts": "a shape no install reads yet"}}}))
    with pytest.raises(state.NewerDrive, match=f"saved by long-running {re.escape(newer)}, newer than this {re.escape(state.VERSION)}"):
        state.load("demo")
    assert state.saved("demo")["roles"]["coordinator"]["session"] == "session-1"


NEWER_RELEASE_SAVE = """
import sys
sys.path.insert(0, sys.argv[1])
from drive import state
with state.edit("demo") as data:
    data["slack"]["page_stale"] = "the newer release's save"
"""


def test_a_newer_release_saving_mid_verb_waits_for_the_older_releases_verb_so_the_verb_and_the_save_both_land(state_root: Path, tmp_path: Path) -> None:
    make("demo")
    release = tmp_path / "9.9.9"
    shutil.copytree(Path(state.__file__).resolve().parent, release / "lib" / "drive")
    (release / ".claude-plugin").mkdir()
    (release / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "9.9.9"}))
    state.hold_install()
    saving = subprocess.Popen([sys.executable, "-c", NEWER_RELEASE_SAVE, str(release / "lib")])
    with pytest.raises(subprocess.TimeoutExpired):
        saving.wait(timeout=2)
    with state.edit("demo") as data:
        data["run"] = "the older verb's receipt"
    state.let_go_install()
    assert saving.wait(timeout=30) == 0
    saved = json.loads(state.state_path("demo").read_text())
    assert (saved["saved_by"], saved["run"], saved["slack"]["page_stale"]) == ("9.9.9", "the older verb's receipt", "the newer release's save")


def test_a_first_save_waits_only_for_verbs_of_the_installs_from_the_one_that_saved_the_drive_up_to_its_own(state_root: Path) -> None:
    make("demo")
    path = state.state_path("demo")
    path.write_text(json.dumps(json.loads(path.read_text()) | {"saved_by": "0.0.2"}))
    state.hold_install()
    verbs = {version: open(state.install_lock(version), "a") for version in ("0.0.1", "0.0.2", "9.9.9")}
    for handle in verbs.values():
        fcntl.flock(handle, fcntl.LOCK_SH)

    def save() -> None:
        with state.edit("demo") as data:
            data["run"] = "run-1"

    saving = threading.Thread(target=save, daemon=True)
    saving.start()
    saving.join(1)
    assert saving.is_alive()
    verbs.pop("0.0.2").close()
    saving.join(10)
    assert (saving.is_alive(), state.load("demo")["run"]) == (False, "run-1")
    for handle in verbs.values():
        handle.close()


def test_a_first_save_waits_for_an_older_installs_verb_however_long_it_runs_never_saving_past_it(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make("demo")
    path = state.state_path("demo")
    path.write_text(json.dumps(json.loads(path.read_text()) | {"saved_by": "0.0.1"}))
    hours = itertools.count(step=3600)
    monkeypatch.setattr(state.time, "monotonic", lambda: next(hours))
    state.install_lock("0.0.1").parent.mkdir(parents=True)
    launch = open(state.install_lock("0.0.1"), "a")
    fcntl.flock(launch, fcntl.LOCK_SH)

    def save() -> None:
        with state.edit("demo") as data:
            data["slack"]["page_stale"] = "the newer install's save"

    saving = threading.Thread(target=save, daemon=True)
    saving.start()
    saving.join(2)
    assert (saving.is_alive(), json.loads(path.read_text())["saved_by"]) == (True, "0.0.1")
    launch.close()
    saving.join(10)
    saved = json.loads(path.read_text())
    assert (saving.is_alive(), saved["saved_by"], saved["slack"]["page_stale"]) == (False, state.VERSION, "the newer install's save")


def test_a_write_waiting_past_the_drive_lock_wait_changes_nothing_and_names_the_lock_held(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make("demo")
    monkeypatch.setattr(state, "DRIVE_LOCK_SECONDS", 0.3)
    with open(state.drive_dir("demo") / ".lock", "a") as stuck:
        fcntl.flock(stuck, fcntl.LOCK_EX)
        with pytest.raises(DriveError, match=r"demo's drive lock is still held after 0\.3s, so this changed nothing: "):
            with state.edit("demo") as data:
                data["run"] = "run-1"
    assert "run" not in state.load("demo")
    with state.edit("demo") as data:
        data["run"] = "run-1"
    assert state.load("demo")["run"] == "run-1"


def test_load_gives_each_picture_drawn_before_pictures_kept_their_source_its_kept_question_or_pr_fence_once(state_root: Path) -> None:
    make("demo")
    drive = state_root / "demo"
    (drive / "asks").mkdir()
    (drive / "asks" / "j1.md").write_text("ASK Which entry point?\nShow me:\n```rust\nfn main() {}\n```\n- Keep main\n")
    pr_card = {"card": {"prs": [{"description": "## Show me\n\n```sand\nbrowser::click(@page, @b) -> @r\n```\n"}]}}
    (drive / "cards.json").write_text(json.dumps({"closed": [], "pr_cards": [pr_card], "questions": [], "source": "live", "team_url": ""}))
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
    (drive / "cards.json").write_text(json.dumps({"closed": [], "pr_cards": [], "questions": [{"card": card}], "source": "live", "team_url": ""}))
    kept = drive / "asks" / "j1-7.md"
    assert state.load("demo")["jobs"]["j1"]["questions"][0]["file"] == str(kept)
    assert kept.read_text() == (
        "ASK Should row counts be exact?\nCounting reads every row.\nRecommended: exact.\nShow me:\nOne query per table:\n```sql\n"
        "select count(*) from alerts\n```\n![dash](/nowhere/dash.png)\n- Exact: one query per table (recommended)\n"
        "- Estimate: the planner's numbers\nFindings:\nRoot cause: none: rebuilt from the posted card\nHolds: none: rebuilt from the posted card\n"
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


def test_load_drops_the_readings_of_his_answers_drive_job_ask_once_kept_beside_a_job(state_root: Path) -> None:
    data = state.new_drive("demo", "coordinator", None, "/work", None)
    by_words = {"title": "Which store holds a run's files?", "answer": {"id": "b2b2b2b", "asked": "Which store holds the blobs?", "said": "The blob store"}}
    data["jobs"]["j1"] = state.new_job("o1", "design", "run files", [], "2026-11-10T21:00:00-08:00") | {"may_settle": by_words}
    data["may_settle_by_subject"] = data["may_settle_per_answer"] = True
    state.create(data)
    loaded = state.load("demo")
    assert ("may_settle" in loaded["jobs"]["j1"], "may_settle_by_subject" in loaded, "may_settle_per_answer" in loaded) == (False, False, False)


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


def test_a_drive_saved_while_the_card_store_held_whether_a_question_was_open_closes_each_question_its_card_closed_and_finishes_each_asked_reader_once(
    state_root: Path,
) -> None:
    make("demo")
    data = state.load("demo")
    del data["closed_from_cards"]
    moment = "2026-11-09T14:00:00-08:00"
    shown, answered, withdrawn = (f"https://acme.slack.com/archives/D1/p17942000000001{n}0" for n in range(3))

    def saved(file: str, title: str, card: str | None = None, settled: dict | None = None) -> dict:
        return {key: value for key, value in state.new_question(file, title, moment, card=card).items() if key != "closed"} | {"settled": settled}

    data["jobs"] = {
        "j1": state.new_job("o1", "reader", "research: the copy", [], moment) | {"state": "waiting", "questions": [saved("/asks/j1-a.md", "Where?", shown)]},
        "j2": state.new_job("o1", "builder", "build it", [], moment)
        | {
            "state": "waiting",
            "questions": [
                saved("/asks/j2-a.md", "Which?", answered),
                saved("/asks/j2-b.md", "Who?", withdrawn),
                saved("/asks/j2-c.md", "When?", settled={"seq": "671985", "ruling": "32164d3", "at": moment}),
            ],
        },
    }
    state.create(data)
    state.keep_report("demo", "j1", "DESIGN where the copy runs", "DESIGN where the copy runs\n")
    store = {"source": "live", "team_url": "", "pr_cards": [], "questions": [{"url": shown, "card": {"title": "Where?"}}]}
    closed = [
        {"waits": None, "url": answered, "answer": {"label": "Team", "at": moment}},
        {"waits": None, "url": withdrawn, "answer": {"label": "his order replaced it", "at": moment, "withdrawn": True}},
    ]
    state.cards_path("demo").write_text(json.dumps(store | {"closed": closed}))
    jobs = state.load("demo")["jobs"]
    assert (jobs["j1"]["state"], jobs["j1"]["questions"][0]["closed"], jobs["j2"]["state"]) == ("done", None, "waiting")
    assert [question["closed"] for question in jobs["j2"]["questions"]] == [
        state.new_close("answered", "Team", None, at=moment),
        state.new_close("withdrawn", "his order replaced it", None, at=moment),
        state.new_close("answered", None, None, seq="671985", ruling="32164d3", at=moment),
    ]
    with state.edit("demo") as data:
        assert data["closed_from_cards"] is True
    state.cards_path("demo").write_text(json.dumps(store | {"questions": [], "closed": [{"waits": None, "url": shown, "answer": {"label": "Here", "at": moment}}, *closed]}))
    assert state.load("demo")["jobs"]["j1"]["questions"][0]["closed"] is None


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


def test_a_job_saved_before_jobs_recorded_where_they_run_reads_with_every_field_a_new_job_has_and_no_session_of_the_loader(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make("demo")
    data = state.load("demo")
    old = {"order": "o1", "kind": "builder", "goal": "fix", "parts": [], "agent": "a-1", "said": None, "state": "running", "created": "2026-11-09T02:56:00-08:00", "updated": "2026-11-09T02:56:00-08:00"}
    data["jobs"] = {"j1": old, "j2": old | {"agent": None, "dispatch": "dispatch_1"}}
    state.create(data)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "coordinator-session")
    jobs = state.load("demo")["jobs"]
    assert [(job["where"], job["session"]) for job in jobs.values()] == [("local", None), ("remote", None)]
    assert jobs["j1"].keys() >= state.new_job("o1", "builder", "fix", [], old["created"]).keys()


def test_an_order_an_older_install_saved_with_a_bare_thread_reads_as_the_channel_and_thread_that_install_read_it_in(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    data["slack"].update(channel="D123", watch=["C0B75AL4XEW"])
    in_dm = {"posted": "https://acme.slack.com/archives/D123/p1794200040000500?thread_ts=1794200000.000100&cid=D123", "orphan_said": None}
    shared = {"posted": "https://acme.slack.com/archives/C0SHARED1/p1794200090000700", "orphan_said": None}
    watched = {"posted": "https://acme.slack.com/archives/C0B75AL4XEW/p1794200095000800", "orphan_said": None}
    base = {"text": "tell the stopped people", "from": "slack:D123/1794200030.000400", "jobs": [], "promised": None, "updated": "2026-11-10T18:46:23-08:00"}
    alert = {"from": "slack:C0822AHFY3G/1794493064.110609", "parts": {"answer": in_dm}}
    data["orders"] = {
        "o1": base | {"thread": "1794200090.000700", "parts": {"answer": in_dm, "tell them": shared}},
        "o2": base | {"thread": "1794200095.000800", "parts": {"answer": in_dm, "tell them": watched}},
        "o3": base | {"thread": "1794200000.000100", "parts": {"answer": in_dm, "tell them": shared}},
        "o4": base | alert | {"from": "slack:C0822AHFY3G/1794495071.669329", "from_thread": "1794493064.110609", "thread": "1794493064.110609"},
        "o5": base | alert | {"from_thread": None, "thread": None},
        "o6": base | {"from": "terminal", "from_thread": None, "thread": None, "parts": {"answer": in_dm}},
        "o7": base | {"from": "slack:D123/1794495097.793329", "thread": ["C0822AHFY3G", "1794493064.110609"], "parts": {"answer": in_dm}},
    }
    state.create(data)
    orders = state.load("demo")["orders"]
    assert {order_id: order["thread"] for order_id, order in orders.items()} == {
        "o1": ["D123", "1794200000.000100"],
        "o2": ["D123", "1794200095.000800"],
        "o3": ["D123", "1794200000.000100"],
        "o4": ["C0822AHFY3G", "1794493064.110609"],
        "o5": ["C0822AHFY3G", "1794493064.110609"],
        "o6": None,
        "o7": ["C0822AHFY3G", "1794493064.110609"],
    }
    assert not any("from_thread" in order for order in orders.values())
    assert [state.promise_thread(order) for order in orders.values()][3:] == [("C0822AHFY3G", "1794493064.110609")] * 2 + [None, ("C0822AHFY3G", "1794493064.110609")]


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("slack:D123/1794495097.793329", ("D123", "1794495097.793329", None)),
        ("slack:C0822AHFY3G/1794493064.110609/1794495071.669329", ("C0822AHFY3G", "1794495071.669329", "1794493064.110609")),
        (
            "https://acme.slack.com/archives/C0822AHFY3G/p1794495071669329?thread_ts=1794493064.110609&cid=C0822AHFY3G",
            ("C0822AHFY3G", "1794495071.669329", "1794493064.110609"),
        ),
        ("https://acme.slack.com/archives/D123/p1794495097793329", ("D123", "1794495097.793329", "1794495097.793329")),
        ("terminal", None),
    ],
)
def test_a_slack_message_is_read_from_each_form_of_its_link_with_no_slack_call(source: str, message: tuple[str, str, str | None] | None) -> None:
    assert state.slack_message(source) == message


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
    order = {"due": state.stamp(at(0)), "promised": state.stamp(at(-60)), "kept": None, "withdrawn": None, "next_due": None, "next_set": None, "incident": False}
    state.promise(order, state.stamp(at(120)), at(30))
    state.keep(order, [at(-10), at(10)])
    assert (order["due"], order["promised"], order["kept"]) == (state.stamp(at(120)), state.stamp(at(30)), None)
    state.keep(order, [at(-10), at(10), at(40)])
    assert (order["promised"], order["kept"]) == (at(40).isoformat(), None)
    state.keep(order, [at(-10), at(10), at(40), at(50)])
    assert order["kept"] == at(50).isoformat()


def test_the_line_of_a_report_closing_the_order_keeps_the_time_it_told_him_and_a_line_before_the_report_does_not() -> None:
    at = lambda minutes: REFERENCE + timedelta(minutes=minutes)
    order = {"due": None, "promised": None, "kept": None, "withdrawn": None, "next_due": None, "next_set": None, "due_said": None, "incident": False}
    state.promise(order, state.stamp(at(28)), at(0))
    state.keep(order, [at(16)])
    state.keep(order, [at(16)], announced=False)
    assert (order["due"], order["promised"], state.owed(order)) == (state.stamp(at(28)), at(16).isoformat(), True)
    state.keep_closing(order, [at(16)], at(17))
    assert state.owed(order)
    state.keep_closing(order, [at(16)], at(15))
    assert (order["kept"], order["next_due"], state.owed(order)) == (at(16).isoformat(), None, False)


def test_an_incident_owes_no_promised_time_since_the_watch_owes_its_line_five_quiet_minutes_after_its_last() -> None:
    order = {"due": state.stamp(REFERENCE), "promised": state.stamp(REFERENCE - timedelta(minutes=5)), "kept": None, "withdrawn": None, "incident": False}
    assert (state.owed(order), state.owed(order | {"incident": True})) == (True, False)


MONOREPO = "Forge-AI/monorepo"


def test_a_new_pr_waits_on_his_tap() -> None:
    pr = state.new_pr(state.pr_url(f"{MONOREPO}#1"), "j1", "o1")
    assert (pr["wants_tap"], stage.carded(pr)) == (True, True)


def kept_prs() -> dict:
    data = state.new_drive("demo", "coordinator", None, "/work", None)
    data["orders"] |= {order_id: state.new_order("fix it", "terminal", None, {}, state.stamp(REFERENCE)) for order_id in ("o226", "o359")}
    for number, order_id in ((4289, "o226"), (4279, "o226"), (4284, "o226"), (4302, "o359")):
        key = f"{MONOREPO}#{number}"
        data["prs"][key] = state.new_pr(state.pr_url(key), "j622", order_id) | {"head": "abc"}
    data["prs"]["yasyf/cc-skills#4289"] = state.new_pr(state.pr_url("yasyf/cc-skills#4289"), "j9", "o359")
    return data


def test_load_keeps_his_answers_rework_on_the_head_it_reopened_as_a_rework_to_his_words_and_drops_one_on_an_older_head(state_root: Path) -> None:
    data = kept_prs()
    marked = {"job": "j9", "head": "abc", "ready": False, "why": "bind it as @parent", "at": state.stamp(REFERENCE), "said": True, "answer": "a1"}
    data["prs"][f"{MONOREPO}#4289"] |= {"verdict": marked}
    data["prs"][f"{MONOREPO}#4279"] |= {"verdict": marked | {"answer": None}}
    for pr in data["prs"].values():
        del pr["rework"]
    data["prs"][f"{MONOREPO}#4284"]["rework"] = {field: marked[field] for field in ("answer", "job", "why", "at")} | {"head": "older"}
    state.create(data)
    kept = state.load("demo")["prs"]
    assert kept[f"{MONOREPO}#4289"]["rework"] == {"by": "a1", "words": "bind it as @parent", "at": state.stamp(REFERENCE)}
    assert (kept[f"{MONOREPO}#4279"]["rework"], kept[f"{MONOREPO}#4284"]["rework"], any("verdict" in pr for pr in kept.values())) == (None, None, False)


def test_a_waiting_line_puts_each_card_it_names_on_each_pr_of_its_order_it_names() -> None:
    data = kept_prs()
    data["jobs"]["j9"] = state.new_job("o226", "builder", "fix", [], state.stamp(REFERENCE))
    card = "https://acme.slack.com/archives/D123/p1794300000000200"
    state.take_result(data, "j9", f"WAITING #4289 and #4279 waits on {card} and demo:j9-3")
    assert [data["prs"][f"{MONOREPO}#{n}"]["waits"] for n in (4289, 4279, 4284)] == [[card, "demo:j9-3"], [card, "demo:j9-3"], []]
    assert data["prs"]["yasyf/cc-skills#4289"]["waits"] == []


def test_a_pr_reworked_to_his_words_owes_no_card_until_its_builders_ready_says_how_they_are_met() -> None:
    data = kept_prs()
    data["jobs"]["j9"] = state.new_job("o226", "builder", "fix", [], state.stamp(REFERENCE))
    pr = data["prs"][f"{MONOREPO}#4289"]
    pr["tap"] = {"label": "Approve", "at": state.stamp(REFERENCE), "head": "abc", "source": "slack"}
    state.rework(pr, "o227", "Topology should be local metadata")
    assert (bool(pr["rework"]), state.asks_tap(pr), pr["tap"]["label"]) == (True, False, "Approve")
    state.take_result(data, "j9", "READY #4289", "READY #4289\nPR: #4289\nCHANGE: topology moves to the session's meta table")
    assert (stage.carded(pr), pr["rework"]) == (True, None)
    assert pr["fundamental"] == 'Reworked to your words "Topology should be local metadata": topology moves to the session\'s meta table'


def test_a_readers_ready_on_a_pr_reworked_to_his_words_leaves_it_reworking() -> None:
    data = kept_prs()
    state.rework(data["prs"][f"{MONOREPO}#4289"], "o227", "Topology should be local metadata")
    data["jobs"]["j740"] = state.new_job("o226", "reader", "READY-row check", [], state.stamp(REFERENCE))
    state.take_result(data, "j740", "READY #4289")
    assert data["prs"][f"{MONOREPO}#4289"]["rework"]["by"] == "o227"


def test_a_pushed_line_settles_only_on_a_read_after_it_was_taken_and_holds_on_a_required_check_still_running_or_red() -> None:
    data = kept_prs()
    data["jobs"]["j9"] = state.new_job("o226", "builder", "fix", [], state.stamp(REFERENCE)) | {"said": "PUSHED #4289 #4279"}
    one, two = data["prs"][f"{MONOREPO}#4289"], data["prs"][f"{MONOREPO}#4279"]
    after = state.stamp(REFERENCE + timedelta(minutes=1))
    build = "https://buildkite.com/forge/monorepo/builds/7"
    one["required"] = {"checks": "pass", "failed": {}, "read": after}
    two["required"] = {"checks": "pass", "failed": {}, "read": state.stamp(REFERENCE)}
    assert state.pushed_verdict(data, "j9") == ("pending", [])
    two["required"] = {"checks": "pending", "failed": {}, "read": after}
    assert state.pushed_verdict(data, "j9") == ("pending", [(f"{MONOREPO}#4279", "required checks still run at abc")])
    two["required"] = {"checks": "fail", "failed": {"buildkite/test": build}, "read": after}
    assert state.pushed_verdict(data, "j9") == ("fail", [(f"{MONOREPO}#4279", f"required buildkite/test failed at abc ({build})")])
    two["state"] = "merged"
    assert state.pushed_verdict(data, "j9") == ("pass", [])
    data["jobs"]["j9"]["said"] = "PUSHED the fix"
    assert state.pushed_verdict(data, "j9") == ("pass", [])


def test_a_bare_number_two_repositories_share_names_the_pr_on_the_jobs_order() -> None:
    assert state.named_prs(kept_prs(), "o226", "#4289") == [f"{MONOREPO}#4289"]
    assert state.named_prs(kept_prs(), "o359", "#4289 and Forge-AI/monorepo#4302") == ["yasyf/cc-skills#4289", f"{MONOREPO}#4302"]


def test_two_questions_are_the_same_whatever_their_case_and_spacing_and_an_untitled_one_matches_none() -> None:
    assert state.same_question("What runs a watch's  Sand function?", "what runs a watch's Sand function?")
    assert not state.same_question("What runs a watch's Sand function?", "What runs a watch's Sand function each interval?")
    assert not state.same_question(None, None)


def test_a_drive_saved_with_one_change_a_part_waited_on_loads_it_as_the_list_of_changes_it_waits_on(state_root: Path) -> None:
    make("demo")
    saved = json.loads(state.state_path("demo").read_text())
    parts = {
        "the interface": {"posted": None, "design": "j2", "after": None},
        "the reads": {"posted": None, "design": "j2", "after": "the interface"},
        "the order": {"posted": None},
    }
    saved["orders"] = {"o1": {"text": "move the tables", "jobs": [], "parts": parts, "promised": None}}
    state.state_path("demo").write_text(json.dumps(saved))
    loaded = state.load("demo")["orders"]["o1"]["parts"]
    assert {part: held["after"] for part, held in loaded.items()} == {"the interface": [], "the reads": ["the interface"], "the order": []}


def test_a_part_an_older_install_saved_with_spaces_around_its_name_loads_trimmed_wherever_a_job_or_pr_names_it(state_root: Path) -> None:
    make("demo")
    saved = json.loads(state.state_path("demo").read_text())
    cut = "both PRs he approved this morning and the stack he tapped an hour later sat for "
    parts = {cut: {"posted": None, "design": None, "after": []}, " fix": {"posted": None, "design": None, "after": [cut]}}
    saved["orders"] = {"o1": {"text": "the stack sat", "jobs": ["j1", "j2"], "parts": parts, "promised": None}, "o2": {"text": "why", "jobs": [], "parts": {"cause ": {"posted": None}}, "promised": None}}
    saved["jobs"] = {"j1": state.new_job("o1", "reader", "find why", [cut], state.stamp()), "j2": state.new_job("o2", "builder", "fix it", ["cause "], state.stamp()) | {"carries": {"o1": [" fix"]}}}
    saved["prs"] = {"yasyf/cc-skills#7": state.new_pr("https://github.com/yasyf/cc-skills/pull/7", "j2", "o1") | {"parts": [" fix"]}}
    state.state_path("demo").write_text(json.dumps(saved))
    loaded = state.load("demo")
    whole = cut.rstrip()
    assert {part: held["after"] for part, held in loaded["orders"]["o1"]["parts"].items()} == {whole: [], "fix": [whole]}
    assert list(loaded["orders"]["o2"]["parts"]) == ["cause"]
    assert (loaded["jobs"]["j1"]["parts"], loaded["jobs"]["j2"]["parts"], loaded["jobs"]["j2"]["carries"]) == ([whole], ["cause"], {"o1": ["fix"]})
    assert loaded["prs"]["yasyf/cc-skills#7"]["parts"] == ["fix"]
    assert state.whole_order(f"{cut}two hours") == whole


def test_a_part_an_older_install_saved_with_no_kind_loads_as_work_but_on_an_answer_order_as_words_he_reads(state_root: Path) -> None:
    make("demo")
    saved = json.loads(state.state_path("demo").read_text())
    saved["orders"] = {
        order_id: {"text": "why, then fix it", "jobs": [], "parts": {"why": {"posted": None}, "fix": {"posted": None}}, "promised": None} | kind
        for order_id, kind in (("o1", {"kind": "answer"}), ("o2", {"kind": "build"}), ("o3", {}))
    }
    state.state_path("demo").write_text(json.dumps(saved))
    orders = state.load("demo")["orders"]
    assert {order_id: [held["kind"] for held in order["parts"].values()] for order_id, order in orders.items()} == {
        "o1": ["answer", "answer"],
        "o2": ["build", "build"],
        "o3": ["build", "build"],
    }


def test_a_detached_verb_names_its_drive_and_no_session_in_its_environment(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make("demo")
    started = []

    class Started:
        pid = 4242

        def __init__(self, argv: list[str], **options: object) -> None:
            started.append((argv, options))

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    monkeypatch.setattr(state.subprocess, "Popen", Started)
    assert state.detach("demo", ["pr", "land", "https://github.com/yasyf/cc-skills/pull/7"], "landings.log") == 4242
    [(argv, options)] = started
    assert argv[1:] == ["pr", "land", "https://github.com/yasyf/cc-skills/pull/7", "--drive", "demo"]
    assert options["env"][state.DRIVE_ENV] == "demo" and "CLAUDE_CODE_SESSION_ID" not in options["env"]
