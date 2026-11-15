import fcntl
import json
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import REAL_DOWN_MAC_BOXES, carrying
from drive import github, install, reads, render, served, settled, stage, state
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
    data = state.new_drive(name, "coordinator", session, "/work")
    if started:
        data["roles"]["coordinator"]["started"] = started
    state.create(data)


def test_a_save_takes_down_the_mac_boxes_of_each_job_it_moves_into_done_failed_or_stopped_once(state_root: Path, mac_boxes_downed: list[str]) -> None:
    make("demo")
    with state.edit("demo") as data:
        data["jobs"] |= {job_id: state.new_job("o1", "builder", "use a Mac box", [], state.stamp()) for job_id in ("j1", "j2", "j3", "j4")}
    with state.edit("demo") as data:
        data["jobs"]["j1"]["state"] = "done"
        data["jobs"]["j2"]["state"] = "waiting"
        data["jobs"]["j3"]["state"] = "built"
        data["jobs"]["j4"]["state"] = "failed"
    assert mac_boxes_downed == ["demo/j1", "demo/j4"]
    with state.edit("demo") as data:
        data["jobs"]["j1"]["state"] = "stopped"
        data["jobs"]["j2"]["state"] = "stopped"
        data["jobs"]["j3"]["goal"] = "use another Mac box"
        data["jobs"]["j4"]["goal"] = "use another Mac box"
    assert mac_boxes_downed == ["demo/j1", "demo/j4", "demo/j2"]
    with state.edit("demo") as data:
        data["jobs"]["j1"]["state"] = "running"
    with state.edit("demo") as data:
        data["jobs"]["j1"]["state"] = "done"
    assert mac_boxes_downed == ["demo/j1", "demo/j4", "demo/j2", "demo/j1"]


def test_a_save_hands_a_finished_jobs_mac_boxes_to_a_process_of_its_own_that_takes_them_down_from_the_drives_checkout(
    state_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: Callable[..., tuple[int, str]]
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "tools").mkdir(parents=True)
    (checkout / "tools" / "ci").write_text(f'#!/bin/sh\necho "$(pwd -P) $*" >> {tmp_path / "downs"}\n')
    (checkout / "tools" / "ci").chmod(0o755)
    state.create(state.new_drive("demo", "coordinator", None, str(checkout)))
    detached: list[tuple[str, list[str], str]] = []
    monkeypatch.setattr(state, "detach", lambda name, args, log: detached.append((name, args, log)) or 0)
    REAL_DOWN_MAC_BOXES("demo", state.load("demo"), ["j1", "j2"])
    REAL_DOWN_MAC_BOXES("demo", state.new_drive("demo", "coordinator", None, str(tmp_path / "elsewhere")), ["j3"])
    assert detached == [("demo", ["mac-boxes-down", "j1", "j2"], state.MAC_BOXES_LOG)]
    assert run("mac-boxes-down", "j1", "j2", "--drive", "demo")[0] == 0
    assert (tmp_path / "downs").read_text().splitlines() == [f"{checkout.resolve()} mac-box down --holder demo/j1", f"{checkout.resolve()} mac-box down --holder demo/j2"]


def test_edit_saves_atomically_and_leaves_no_temp(state_root: Path) -> None:
    make("demo")
    with state.edit("demo") as data:
        data["orders"]["o1"] = {"text": "ship it", "jobs": []}
    assert state.load("demo")["orders"]["o1"]["text"] == "ship it"
    assert [path.name for path in (state_root / "demo").iterdir() if path.name.endswith(".tmp")] == []
    assert (state_root / "demo" / ".lock").exists()


def test_a_drive_a_newer_install_saved_is_refused_before_any_of_it_is_read_while_its_bindings_still_read(state_root: Path) -> None:
    make("demo", session="session-1")
    assert state.saved_by("demo") == state.VERSION
    major, minor, patch = state.VERSION.split(".")
    state.store("demo", state.stored("demo") | {"saved_by": f"{major}.{minor}.{int(patch) - 1}"})
    assert state.load("demo")["roles"]["coordinator"]["session"] == "session-1"
    newer = f"{major}.{minor}.{int(patch) + 1}"
    state.store("demo", state.stored("demo") | {"saved_by": newer, "orders": {"o1": {"parts": "a shape no install reads yet"}}})
    with pytest.raises(state.NewerDrive, match=f"saved by long-running {re.escape(newer)}, newer than this {re.escape(state.VERSION)}"):
        state.load("demo")
    assert state.saved("demo")["roles"]["coordinator"]["session"] == "session-1"


def test_a_save_that_changes_a_row_or_a_command_that_does_more_than_read_is_kept_as_written_so_its_verb_never_runs_twice(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make("demo")
    with state.edit("demo"):
        pass
    monkeypatch.setattr(state, "WROTE", set())
    with state.edit("demo"):
        pass
    subprocess.run(["git", "--version"], check=True, capture_output=True)
    assert state.WROTE == set()
    with state.edit("demo") as data:
        data["terminal"] = "a changed row"
    subprocess.run([sys.executable, "-c", ""], check=True)
    assert state.WROTE == {"drive", Path(sys.executable).name}


@pytest.mark.parametrize(
    ("argv", "reading"),
    [
        (["ccx", "vcs", "gh", "--max-age", "30s", "--", "pr", "view", "https://github.com/o/r/pull/1"], True),
        (["ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "repos/o/r/pulls/1/files", "--jq", ".[].filename"], True),
        (["ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "-X", "POST", "repos/o/r/issues/1/comments"], False),
        (["ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "graphql", "-f", 'query=query { r0: repository(owner: "o", name: "r") { p1: pullRequest(number: 1) { state } } }'], True),
        (["ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "graphql", "-f", "query=mutation { x }"], False),
        (["ccx", "vcs", "gh", "--max-age", "0s", "--", "pr", "merge", "--squash", "1"], False),
        (["/plugins/cc-slack/1.0/bin/cc-slack", "thread", "--channel", "D1"], True),
        (["/plugins/cc-slack/1.0/bin/cc-slack", "dm-status", "--text", "a post"], False),
        (["/plugins/cc-slack/1.0/bin/cc-slack", "card", "render", "--card", "/dev/stdin"], True),
        (["orca", "orchestration", "task-list", "--run", "run_1", "--json"], True),
        (["orca", "orchestration", "send", "--to", "t1", "--json"], False),
        (["cci", "show", "2065"], True),
        (["cci", "post", "--batch", "-"], False),
        (["tmux", "show-environment", "ORCA_TERMINAL_HANDLE"], True),
        (["git", "-C", "/clone", "rev-parse", "HEAD"], True),
        (["git", "-C", "/clone", "push", "origin", "lane"], False),
        (["/plugins/long-running/1.0/bin/drive", "job", "read", "j1", "m1"], False),
        (["tailscale", "status", "--self", "--peers=false", "--json"], True),
        (["tailscale", "whois", "--json", "100.64.0.1"], True),
        (["tailscale", "funnel", "--bg", "--yes", "--https=8443"], False),
        (["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--headless=new", "--incognito", "--screenshot=/tmp/p.png", "file:///tmp/p.html"], True),
    ],
)
def test_only_a_command_that_reads_leaves_a_verb_free_to_run_again(argv: list[str], reading: bool) -> None:
    assert state.reads_only(argv) is reading


NEWER_RELEASE_SAVE = """
import sys
sys.path.insert(0, sys.argv[1])
from drive import state
with state.edit("demo") as data:
    data["slack"]["page_stale"] = "the newer release's save"
"""


def test_an_older_installs_save_after_a_newer_installs_is_refused_leaving_the_newer_save_whole(state_root: Path, tmp_path: Path) -> None:
    make("demo")
    release = tmp_path / "9.9.9"
    shutil.copytree(Path(state.__file__).resolve().parent, release / "lib" / "drive")
    (release / ".claude-plugin").mkdir()
    (release / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "9.9.9"}))
    read = state.load("demo")
    subprocess.run([sys.executable, "-c", NEWER_RELEASE_SAVE, str(release / "lib")], check=True, timeout=30)
    with pytest.raises(state.NewerDrive, match="saved by long-running 9.9.9 since this"):
        state.save("demo", read | {"terminal": "the older verb's receipt"})
    with pytest.raises(state.NewerDrive, match="saved by long-running 9.9.9, newer than this"), state.edit("demo") as data:
        data["terminal"] = "the older verb's receipt"
    saved = state.stored("demo")
    assert (saved["saved_by"], saved.get("terminal"), saved["slack"]["page_stale"]) == ("9.9.9", None, "the newer release's save")


def test_a_write_waiting_past_the_drive_lock_wait_changes_nothing_and_names_the_lock_held(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make("demo")
    monkeypatch.setattr(state, "DRIVE_LOCK_SECONDS", 0.3)
    with open(state.drive_dir("demo") / ".lock", "a") as stuck:
        fcntl.flock(stuck, fcntl.LOCK_EX)
        with pytest.raises(DriveError, match=r"demo's drive lock is still held after 0\.3s, so this changed nothing: "):
            with state.edit("demo") as data:
                data["terminal"] = "term-1"
    assert state.load("demo")["terminal"] is None
    with state.edit("demo") as data:
        data["terminal"] = "term-1"
    assert state.load("demo")["terminal"] == "term-1"


def test_migrate_gives_each_picture_drawn_before_pictures_kept_their_source_its_kept_question_or_pr_fence_once_and_load_never_does(state_root: Path) -> None:
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
    assert not (asked / "source.json").exists()
    state.migrate("demo")
    assert json.loads((asked / "source.json").read_text()) == {"kind": "code:rust", "source": "fn main() {}\n"}
    assert json.loads((landed / "source.json").read_text()) == {"kind": "code:sand", "source": "browser::click(@page, @b) -> @r\n"}
    (asked / "source.json").unlink()
    state.migrate("demo")
    assert not (asked / "source.json").exists()


def test_migrate_rebuilds_from_his_stored_card_the_ask_file_a_question_never_kept_word_for_word_once_and_load_never_does(state_root: Path) -> None:
    data = state.new_drive("demo", "coordinator", None, "/work")
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
    assert state.load("demo")["jobs"]["j1"]["questions"][0]["file"] is None
    state.migrate("demo")
    assert state.load("demo")["jobs"]["j1"]["questions"][0]["file"] == str(kept)
    assert kept.read_text() == (
        "ASK Should row counts be exact?\nCounting reads every row.\nRecommended: exact.\nShow me:\nOne query per table:\n```sql\n"
        "select count(*) from alerts\n```\n![dash](/nowhere/dash.png)\n- Exact: one query per table (recommended)\n"
        "- Estimate: the planner's numbers\nFindings:\nRoot cause: none: rebuilt from the posted card\nHolds: none: rebuilt from the posted card\n"
    )
    kept.unlink()
    state.migrate("demo")
    assert state.load("demo")["jobs"]["j1"]["questions"][0]["file"] is None
    assert not kept.exists()


def test_migrate_rebuilds_no_ask_file_for_his_stored_card_holding_a_picture_the_drive_keeps_no_file_of(state_root: Path) -> None:
    data = state.new_drive("demo", "coordinator", None, "/work")
    data["jobs"]["j1"] = state.new_job("o1", "design", "row counts", [], "2026-11-10T21:00:00-08:00")
    data["jobs"]["j1"]["questions"] = [state.new_question(None, "Should row counts be exact?", "2026-11-10T21:00:00-08:00") | {"waits": "demo:j1-7"}]
    state.create(data)
    image = {"key": f"cc-slack/pictures/T1/{'0' * 64}.png", "title": "option-a.png"}
    card = {"title": "Should row counts be exact?", "body": "Counting reads every row.", "options": ["Exact", "Estimate"], "recommended": "Exact", "waits": "demo:j1-7", "images": [image]}
    (state_root / "demo" / "cards.json").write_text(json.dumps({"closed": [], "pr_cards": [], "questions": [{"card": card}], "source": "live", "team_url": ""}))
    state.migrate("demo")
    assert state.load("demo")["jobs"]["j1"]["questions"][0]["file"] is None
    assert not (state_root / "demo" / "asks" / "j1-7.md").exists()


def test_migrate_labels_his_old_taps_approving_a_pr_in_the_coordinators_checkout_once_and_never_for_a_drive_state_create_built_while_load_never_does(
    state_root: Path, backfilled: list[str]
) -> None:
    make("demo")
    state.migrate("demo")
    assert backfilled == []
    (state.drive_dir("demo") / state.APPROVALS_LABELLED).unlink()
    state.load("demo")
    assert backfilled == []
    state.migrate("demo")
    state.migrate("demo")
    assert backfilled == ["/work"]
    state.create(state.new_drive("talk", "communicator", None, "/talk"))
    state.migrate("talk")
    assert backfilled == ["/work"]


def test_migrate_records_each_job_an_older_install_left_running_or_waiting_with_no_worker_ever_started_as_not_started_once(state_root: Path) -> None:
    make("demo")
    with state.edit("demo") as data:
        data["jobs"] |= {job_id: state.new_job("o1", "builder", "ship it", [], state.stamp()) for job_id in ("j1", "j2", "j3", "j4", "j5")}
        data["jobs"]["j1"].update(state="running", said=None)
        data["jobs"]["j2"].update(state="waiting", said="waits on j1", waits=["j1"])
        data["jobs"]["j3"].update(state="running", said=None, codex_run="/runs/j3")
        data["jobs"]["j4"].update(state="running", said=None, agent="a4")
        data["jobs"]["j5"].update(state="waiting", said="which store holds the blobs?")
    state.migrate("demo")
    jobs = state.load("demo")["jobs"]
    assert [(jobs[job_id]["state"], jobs[job_id]["said"]) for job_id in jobs] == [
        ("waiting", state.NOT_STARTED),
        ("waiting", state.NOT_STARTED),
        ("running", None),
        ("running", None),
        ("waiting", "which store holds the blobs?"),
    ]
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(state="running", said=None)
    state.migrate("demo")
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"


def test_migrations_run_at_once_take_turns_so_only_the_first_makes_the_change(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make("demo")
    (state.drive_dir("demo") / state.APPROVALS_LABELLED).unlink()
    labelled: list[str] = []

    def slow_backfill(cwd: str) -> None:
        labelled.append(cwd)
        time.sleep(0.05)

    monkeypatch.setattr(settled, "backfill", slow_backfill)
    with ThreadPoolExecutor() as pool:
        list(pool.map(lambda _: state.migrate("demo"), range(4)))
    assert labelled == ["/work"]


def test_a_copy_of_a_drive_migrates_only_the_once_only_passes_a_migrate_of_the_drive_would(
    state_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backfilled: list[str]
) -> None:
    make("demo")
    state.clone("demo", tmp_path / "labelled")
    (state.drive_dir("demo") / state.APPROVALS_LABELLED).unlink()
    state.clone("demo", tmp_path / "unlabelled")
    monkeypatch.setenv("LONG_RUNNING_STATE", str(tmp_path / "labelled"))
    state.migrate("demo")
    assert backfilled == []
    monkeypatch.setenv("LONG_RUNNING_STATE", str(tmp_path / "unlabelled"))
    state.migrate("demo")
    assert backfilled == ["/work"]


def test_load_drops_the_readings_of_his_answers_drive_job_ask_once_kept_beside_a_job(state_root: Path) -> None:
    data = state.new_drive("demo", "coordinator", None, "/work")
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


def test_a_drive_saved_holding_his_answer_on_jobs_not_owed_it_drops_each_copy_on_a_job_made_after_he_gave_it_or_marked_heard(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["holds_owed"]
    tapped, took = "2026-11-13T12:06:14-08:00", "2026-11-13T14:15:38-08:00"
    tap_card, typed_card = "https://acme.slack.com/archives/D1/p1794600340632399", "https://acme.slack.com/archives/D1/p1794600900000100"
    tap = state.new_question("/asks/j1-b.md", "Which forms?", "2026-11-13T11:30:00-08:00", card=tap_card, closed=state.new_close("answered", "Optional NONE", tap_card, seq="699068", at=tapped))
    typed = state.new_question("/asks/j1-c.md", "Which reports?", "2026-11-13T11:40:00-08:00", card=typed_card, closed=state.new_close("answered", "the reports", typed_card, seq="699111", at="2026-11-13T13:00:00-08:00"))

    def held(seq: str, waits: str, words: str) -> dict:
        return {"seq": seq, "words": f'His answer on card {waits}, verbatim: "{words}"', "at": took, "said": None, "request": f"req-{seq}"}

    both = [held("699068", "demo:j1-2", "Optional NONE"), held("699111", "demo:j1-3", "the reports")]
    data["jobs"] = {
        "j1": state.new_job("o1", "builder", "build it", [], "2026-11-13T10:00:00-08:00") | {"questions": [tap | {"waits": "demo:j1-2"}, typed | {"waits": "demo:j1-3"}], "undelivered": both},
        "j2": state.new_job("o1", "builder", "build the reports", [], "2026-11-13T12:11:36-08:00") | {"undelivered": both},
        "j3": state.new_job("o1", "builder", "build the checker", [], "2026-11-13T09:00:00-08:00") | {"undelivered": both},
    }
    data["answers"] = {"e614876": {"words": "Optional NONE", "link": tap_card, "at": took, "said": took, "cards": {}, "jobs": {"j1": None, "j3": {"mark": "untouched"}}}}
    state.create(data)
    with state.edit("demo") as drive:
        assert {job_id: [kept["seq"] for kept in job["undelivered"]] for job_id, job in drive["jobs"].items()} == {"j1": ["699068", "699111"], "j2": ["699111"], "j3": ["699111"]}
        assert drive["holds_owed"] is True


def test_a_drive_saved_before_say_kept_lines_keeps_on_each_carded_question_the_newest_reply_of_each_thread_linking_its_card(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["lines_kept"]
    card = "https://acme.slack.com/archives/D1/p1794600340632399"
    data["jobs"] = {"j1": state.new_job("o1", "builder", "build it", [], "2026-11-13T10:00:00-08:00") | {"questions": [state.new_question("/asks/j1-b.md", "Which forms?", "2026-11-13T11:30:00-08:00", card=card) | {"waits": "demo:j1-2"}]}}
    alert = "https://acme.slack.com/archives/C0ALERT1/p{ts}?thread_ts=1794200000.000100"
    said = [
        ("https://acme.slack.com/archives/D1/p1794600400000100", "[Your card on the forms](http://board#/queue/question/1794600340.632399) went up"),
        (alert.format(ts="1794600500000100"), "[Your card on the forms](http://board#/queue/question/1794600340.632399) blocks the fix"),
        (alert.format(ts="1794600600000100"), f"Your card on the forms ({card}) blocks the fix and its release"),
        (alert.format(ts="1794600700000100"), "The fix is live"),
    ]
    for url, text in said:
        state.append_event("demo", "said", text=text, url=url)
    state.create(data)
    with state.edit("demo") as drive:
        assert (drive["jobs"]["j1"]["questions"][0]["told"], drive["lines_kept"]) == ({"1794200000.000100": {"link": said[2][0], "words": said[2][1]}}, True)


def test_a_question_kept_before_its_record_held_the_hash_of_its_words_reads_the_hash_its_file_is_named_for(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    files = (state.drive_dir("demo") / "asks" / "j1-0badf00d.md", state.rebuilt_ask("demo", "demo:j1-2"))
    unhashed = [{field: value for field, value in state.new_question(str(file), "Which forms?", "2026-11-13T11:30:00-08:00").items() if field != "hash"} for file in files]
    data["jobs"] = {"j1": state.new_job("o1", "builder", "build it", [], "2026-11-13T10:00:00-08:00") | {"questions": unhashed}}
    state.create(data)
    assert [question["hash"] for question in state.load("demo")["jobs"]["j1"]["questions"]] == ["0badf00d", None]


def test_j3668_a_drive_saved_with_two_questions_in_one_ask_file_keeps_the_one_answered_on_his_card_and_drops_its_refusal(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["one_question_per_file"]
    moment = "2026-11-12T09:00:00-08:00"
    link = "https://acme.slack.com/archives/D1/p1794396500236719"
    answered = state.new_question("/asks/j1-a.md", "Which host runs the rules?", moment, card=link, refused="refused: it ended answered", closed=state.new_close("answered", "Rules in the shared wasm", link, at=moment))
    undrawn = state.new_question("/asks/j1-a.md", "Which host runs the rules?", moment)
    other = state.new_question("/asks/j1-b.md", "Who builds it?", moment)
    data["jobs"] = {"j1": state.new_job("o1", "builder", "build it", [], moment) | {"state": "waiting", "questions": [answered, undrawn, other]}}
    state.create(data)
    assert state.load("demo")["jobs"]["j1"]["questions"] == [answered | {"refused": None}, other]


def test_j3771_a_drive_saved_with_his_cards_on_an_unstarted_design_builders_waits_hands_each_card_to_the_watch_with_why_it_ended_and_sets_no_state(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["change_builders_unwaited"]
    moment = "2026-11-13T09:00:00-08:00"
    cards = ["demo:j1-660010", "demo:j1-660681"]
    data["jobs"] = {
        "j1": state.new_job("o1", "design", "cards: the copy", [], moment) | {"state": "done"},
        "j2": state.new_job("o1", "builder", "build j1's change: the interface", ["the interface"], moment) | {"state": "waiting", "waits": [*cards, "j3"], "said": "waits on them"},
        "j3": state.new_job("o1", "builder", "build j1's change: the reads", ["the reads"], moment) | {"state": "waiting", "waits": cards, "said": f"waits on {', '.join(cards)}"},
        "j4": state.new_job("o1", "builder", "build j1's change: the writes", ["the writes"], moment) | {"state": "built", "waits": cards, "agent": "a4", "said": None},
        "j5": state.new_job("o1", "builder", "build the writes", ["the writes"], moment) | {"state": "waiting", "waits": cards},
    }
    state.create(data)
    drive = state.load("demo")
    assert {job_id: (job["state"], job["waits"], job["said"]) for job_id, job in drive["jobs"].items() if job_id != "j1"} == {
        "j2": ("waiting", [*cards, "j3"], "waits on them"),
        "j3": ("waiting", cards, state.NOT_STARTED),
        "j4": ("built", cards, None),
        "j5": ("waiting", cards, state.NOT_STARTED),
    }
    ended = {card: f"{card} holds only the PR its Holds: line names, never a change builder's start" for card in cards}
    assert (drive["dropped_waits"], drive["change_builders_unwaited"]) == ({"j2": ended, "j3": ended}, True)


def test_j3986_a_drive_saved_keying_his_rulings_as_typed_keys_each_by_its_full_id_and_folds_one_kept_twice(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make("demo")
    data = state.load("demo")
    del data["answers_keyed"]
    ruling, other = "596e9ccb7937b69da452c43c49fc284a31301704", "76872536b257acbecf0a8cba730ce7ef1ccbc651"
    early, late = "2026-11-13T03:02:22-08:00", "2026-11-13T03:03:34-08:00"
    kept = {"words": "Tree", "link": "https://acme.slack.com/archives/D1/p1"}
    dropped = {"mark": "dropped", "job": None, "card": None}
    data["answers"] = {
        ruling: kept | {"at": early, "said": early, "cards": {"demo:j1-1": None, "demo:j1-2": {"mark": "stands"}}, "jobs": {"j1": None}},
        "596e9cc": kept | {"at": late, "said": late, "cards": {"demo:j1-1": dropped}, "jobs": {"j1": {"mark": "sent"}}},
        "7687253": kept | {"at": late, "said": late, "cards": {}, "jobs": {}},
    }
    closed = state.new_close("answered", None, None, seq="671985", ruling="7687253", at=late)
    question = state.new_question(None, "Which host runs the rules?", early, closed=closed)
    data["jobs"] = {"j1": state.new_job("o1", "builder", "build it", [], early) | {"questions": [question], "brief_flags": {"ruling": ["596e9cc", ruling, "deadbee"]}}}
    state.create(data)
    listed: list[list[str]] = []
    monkeypatch.setattr(settled, "ccn", lambda argv, text, cwd, failure: listed.append(argv) or json.dumps([{"id": ruling}, {"id": other}]))
    drive = state.load("demo")
    assert listed == [["ccn", "answer", "list", "--limit", "0", "--include-superseded", "--json"]]
    assert drive["answers"] == {
        ruling: kept | {"at": early, "said": late, "cards": {"demo:j1-1": dropped, "demo:j1-2": {"mark": "stands"}}, "jobs": {"j1": {"mark": "sent"}}},
        other: kept | {"at": late, "said": late, "cards": {}, "jobs": {}},
    }
    assert (drive["jobs"]["j1"]["questions"][0]["closed"]["ruling"], drive["jobs"]["j1"]["brief_flags"]["ruling"]) == (other, [ruling, "deadbee"])
    with state.edit("demo"):
        pass
    assert (state.load("demo")["answers"] == drive["answers"], len(listed)) == (True, 2)


def test_a_drive_saved_dating_his_late_card_taps_by_when_the_drive_took_them_dates_each_by_its_tap_once_and_rechecks_each_open_pr_that_read_one(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make("demo")
    data = state.load("demo")
    del data["taps_dated"]
    taken, other = "2026-11-13T14:15:38-08:00", "2026-11-13T12:13:00-08:00"
    tapped = state.stamp(datetime.fromtimestamp(1794600374.250448).astimezone())
    tap = state.new_close("answered", "Optional NONE on all three raw_sql forms", "https://acme.slack.com/archives/D1/p1794600340632399", seq="699068", at=taken)
    typed = state.new_close("answered", "Ring", "https://acme.slack.com/archives/D1/p1794600900000100", seq="699080", at=other)
    questions = [("Which raw_sql forms?", tap), ("Which forms take it?", tap | {"how": "superseded", "by": "demo:j1-1"}), ("Ring or knock?", typed)]
    data["jobs"] = {"j1": state.new_job("o1", "builder", "build it", [], taken) | {"questions": [state.new_question(None, title, taken, closed=closed) for title, closed in questions]}}
    checker = {"head": "abc", "pid": 1, "at": taken, "orders": None, "answers": None, "outcome": "NOT READY", "against": "", "carried": None}
    read_tap = [f'demo:j1-1794600337558, {state.parse_time(taken).astimezone().isoformat(timespec="seconds")}, asked "Which raw_sql forms?": "Optional NONE"']
    read_typed = [f'demo:j1-1794600900000, {state.parse_time(other).astimezone().isoformat(timespec="seconds")}, asked "Ring or knock?": "Ring"']
    data["prs"] = {
        f"yasyf/cc-skills#{number}": state.new_pr(f"https://github.com/yasyf/cc-skills/pull/{number}", "j1", "o1") | {"state": status, "answers": answers, "checker": checker}
        for number, status, answers in ((1, "open", read_tap), (2, "open", read_typed), (3, "merged", read_tap))
    }
    state.create(data)
    records = {"699068": {"seq": 699068, "id": "tap:demo:j1-1794600337558:1794600374.250448"}, "699080": {"seq": 699080}}
    shown: list[list[str]] = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout: shown.append(argv) or "".join(json.dumps(records[seq]) + "\n" for seq in argv[3:]))
    drive = state.load("demo")
    dated = [question["closed"]["at"] for question in drive["jobs"]["j1"]["questions"]]
    kept = [(pr["answers"], pr["checker"]) for pr in drive["prs"].values()]
    assert (shown, dated) == ([["cci", "show", "--json", "699068", "699080"]], [tapped, tapped, other])
    assert kept == [(None, None), (read_typed, checker | {"faulted": []}), (read_tap, checker | {"faulted": []})]
    with state.edit("demo"):
        pass
    state.load("demo")
    assert len(shown) == 2


def test_a_drive_saved_keeping_the_answers_cc_notes_ranked_near_a_card_beside_those_a_pr_cites_drops_each_open_prs_kept_answers_and_objection_once_and_keeps_only_the_cited_ones_on_the_rest(
    state_root: Path,
) -> None:
    make("demo")
    data = state.load("demo")
    del data["near_answers_apart"]
    asked = "2026-11-13T11:07:37-08:00"
    cited = f'11f8cf2, {asked}, asked "Keep the handoff metric?": "Land both"'
    options = 'the options his card "Keep the handoff metric?" offered, numbered as it drew them: Option 1 (recommended): Land both'
    near = f'0df9263, {asked}{state.SUBJECT_LABEL}Keep the handoff metric?", asked "Which chart for the dashboard?": "bar charts only"'
    passed = {"head": "abc", "pid": 1, "at": asked, "orders": ["o1"], "answers": [], "outcome": "CHECKED against the words of o1 at abc", "against": "", "carried": None}
    objected = passed | {"outcome": f'NOT READY: yasyf/cc-skills#4 go against his words: 0df9263 "bar charts only": draws a line chart. His answers the check read, each whole: {near}', "against": '0df9263 "bar charts only": draws a line chart'}
    data["jobs"] = {"j1": state.new_job("o1", "builder", "build it", [], asked)}
    data["prs"] = {
        f"yasyf/cc-skills#{number}": state.new_pr(f"https://github.com/yasyf/cc-skills/pull/{number}", "j1", "o1") | {"state": status, "answers": answers, "checker": checker}
        for number, status, answers, checker in (
            (1, "open", [cited, near, options], passed),
            (2, "merged", [cited, near, options], objected),
            (3, "open", None, passed),
            (4, "open", None, objected),
        )
    }
    state.create(data)
    drive = state.load("demo")
    assert [(pr["answers"], pr["checker"]) for pr in drive["prs"].values()] == [(None, passed | {"faulted": []}), ({"cited": [cited, options], "near": []}, objected | {"faulted": []}), (None, passed | {"faulted": []}), (None, None)]
    with state.edit("demo") as data:
        data["prs"]["yasyf/cc-skills#1"]["answers"] = {"cited": [cited], "near": [near]}
    assert (state.load("demo")["prs"]["yasyf/cc-skills#1"]["answers"], state.load("demo")["near_answers_apart"]) == ({"cited": [cited], "near": [near]}, True)


def test_a_drive_saved_with_a_stack_refused_for_a_ruling_write_its_own_pr_makes_drops_each_open_prs_refusal_once_so_the_stack_is_checked_again_with_no_push(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["stack_writes_read"]
    at = "2026-11-15T00:40:00-08:00"
    owes = f'c0de123 "Key the cache on the run id": Forge-AI/monorepo#1 changes go/sand/cache/key.go, under go/sand/cache, and no AGENTS.md at or above go/sand/cache holds these words of his at the tip of its base branch, at the head of another open PR or in its diff; add them verbatim with their date to the AGENTS.md nearest go/sand/cache, by his ruling {served.WRITES_RULING}, "{served.WRITES_WORDS}"'
    refused = {"head": "abc", "pid": 1, "at": at, "orders": ["o1"], "answers": [], "outcome": f"NOT READY: Forge-AI/monorepo#1, Forge-AI/monorepo#2 go against his words: {owes}", "against": owes, "carried": None}
    judged = refused | {"outcome": 'NOT READY: Forge-AI/monorepo#3 go against his words: o1 "one store": adds a second store', "against": 'o1 "one store": adds a second store'}
    data["jobs"] = {"j1": state.new_job("o1", "builder", "build it", [], at)}
    data["prs"] = {
        f"Forge-AI/monorepo#{number}": state.new_pr(f"https://github.com/Forge-AI/monorepo/pull/{number}", "j1", "o1") | {"state": status, "checker": checker}
        for number, status, checker in ((1, "open", refused), (2, "open", refused), (3, "open", judged), (4, "merged", refused))
    }
    state.create(data)
    drive = state.load("demo")
    assert ([pr["checker"] for pr in drive["prs"].values()], drive["stack_writes_read"]) == ([None, None, judged | {"faulted": []}, refused | {"faulted": []}], True)
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#1"]["checker"] = refused
    assert state.load("demo")["prs"]["Forge-AI/monorepo#1"]["checker"] == refused | {"faulted": []}


def test_a_drive_saved_with_builders_whose_goal_is_their_readers_question_gives_each_untaken_one_its_parts_goal_on_its_brief_too(state_root: Path, tmp_path: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["builder_goals_named"]
    copied = "build what j1's research finds: read-only, at once: why the copy stalls"
    kept = tmp_path / "j2.brief.md"
    kept.write_text(f"Order (his words): copy one store\nGoal: {copied}\nResearched: j1\n")
    parts = ["the copy", "the proof"]
    data["jobs"] = {
        "j2": state.new_job("o1", "builder", copied, parts, state.stamp()) | {"state": "waiting", "brief": str(kept)},
        "j3": state.new_job("o1", "builder", copied, parts, state.stamp()) | {"agent": "a1"},
    }
    state.create(data)
    jobs = state.load("demo")["jobs"]
    assert ([jobs[job_id]["goal"] for job_id in ("j2", "j3")], kept.read_text()) == (
        ["build the copy; the proof", copied],
        "Order (his words): copy one store\nGoal: build the copy; the proof\nResearched: j1\n",
    )


def test_a_drive_saved_before_proofs_proven_keeps_work_delivered_only_where_drive_job_prove_proved_it_and_puts_back_a_superseded_job_its_proof_failed(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["proofs_proven"]
    data["proofs_posted"] = True
    proven, merged, ended, staging, answered, moved = "the proven fix", "the merged fix", "the ended fix", "staging test of #7 before it lands", "why it broke", "Bulk-apply spike"
    link = "https://github.com/yasyf/cc-skills/pull/7"
    post = "https://acme.slack.com/archives/D123/p1794200040000500"
    build = "https://buildkite.com/forge/release/builds/16112"
    proof = f"{link} live: repro/j1.sh exits 0 on long-running@skills 1.0.3"
    parts = {proven: state.new_part(), merged: state.new_part() | {"posted": link}, ended: state.new_part(), staging: state.new_part() | {"posted": build}, moved: state.new_part()}
    data["orders"] = {
        "o1": state.new_order("fix it", "slack", None, parts, state.stamp()) | {"kind": "build", "jobs": ["j1", "j2", "j3", "j4", "j5"]},
        "o2": state.new_order(answered, "slack", None, {answered: state.new_part() | {"posted": post}}, state.stamp()) | {"kind": "answer", "jobs": []},
    }
    first, later = "2026-11-15T04:19:00-08:00", "2026-11-15T05:19:00-08:00"
    pinned = "DONE duckdb-spike-bulk-apply at d9bf4dd ran every case"
    data["jobs"] = {
        "j1": state.new_job("o1", "builder", "prove it", [proven], first) | {"state": "done", "said": f"DONE {proof}", "prs": [link]},
        "j2": state.new_job("o1", "builder", "merge it", [merged, staging], first) | {"state": "done", "said": f"DONE {link} merged: yasyf/cc-skills#7 at abc123"},
        "j3": state.new_job("o1", "codex", "end it", [ended], first) | {"state": "done", "said": "DONE checked by hand"},
        "j4": state.new_job("o1", "builder", "spike it", [moved], first) | {"state": "failed", "said": "BLOCKED j4: repro/j4.sh exits 1", "proof": {"pid": 1, "at": first, "said": pinned, "version": "1.0.1168"}},
        "j5": state.new_job("o1", "builder", "move it on", [moved], later) | {"state": "failed", "said": "BLOCKED j5: repro/j5.sh exits 1", "proof": {"pid": 1, "at": later, "said": "DONE at f05ceac", "version": "1.0.1168"}},
    }
    data["prs"] = {"yasyf/cc-skills#7": state.new_pr(link, "j1", "o1") | {"state": "merged", "merged_sha": "abc123", "head": "abc123", "checker": carrying("abc123", {})}}
    state.create(data)
    loaded = state.load("demo")
    assert {part: loaded["orders"]["o1"]["parts"][part]["posted"] for part in parts} == {proven: proof, merged: None, ended: None, staging: build, moved: None}
    assert [(loaded["jobs"][job_id]["state"], loaded["jobs"][job_id]["said"]) for job_id in ("j4", "j5")] == [("done", pinned), ("failed", "BLOCKED j5: repro/j5.sh exits 1")]
    assert (loaded["orders"]["o2"]["parts"][answered]["posted"], loaded["proofs_proven"], "proofs_posted" in loaded) == (post, True, False)


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
    assert [state.own_thread(order) for order in orders.values()][3:] == [("C0822AHFY3G", "1794493064.110609")] * 2 + [None, ("C0822AHFY3G", "1794493064.110609")]


def test_an_orders_lines_to_him_go_in_a_thread_of_his_dm_and_in_a_channel_thread_only_for_an_incident() -> None:
    at = "2026-11-13T10:00:00-08:00"
    release = ["C0B75AL4XEW", "1794436805.002349"]
    order = state.new_order("ship the release", "slack:C0B75AL4XEW/1794436805.002349", tuple(release), {}, at)
    kept = [["C0B75AL4XEW", "1794436900.000100", at, "posted"], ["D123", "1794437000.000200", at, "question"]]
    assert [
        state.promise_thread(order),
        state.promise_thread(order | {"threads": kept}),
        state.promise_thread(order | {"threads": kept, "incident": True}),
        state.promise_thread(order | {"thread": ["D123", "1794437100.000300"], "threads": kept}),
        state.promise_thread(order | {"thread": None, "threads": kept}),
    ] == [None, ("D123", "1794437000.000200"), tuple(release), ("D123", "1794437100.000300"), None]


def test_an_order_an_older_install_saved_with_one_reporter_reads_as_a_list_of_that_reporter_and_one_with_none_as_no_reporters(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    told = state.reporter("slack:C0REPORTS01/1794400100.000200") | {"told": "https://acme.slack.com/archives/C0REPORTS01/p1794418000000100"}
    older = {field: value for field, value in state.new_order("fix it", "terminal", None, {"fix it": state.new_part()}, state.stamp()).items() if field != "reporters"}
    data["orders"] = {"o1": older | {"reporter": told}, "o2": older | {"reporter": None}}
    state.create(data)
    orders = state.load("demo")["orders"]
    assert [(orders[order_id]["reporters"], "reporter" in orders[order_id]) for order_id in ("o1", "o2")] == [([told], False), ([], False)]


@pytest.mark.parametrize(("heard", "away", "moment"), [("2026-11-13T15:20:49-08:00", True, "2026-11-13T15:20:49-08:00"), ("2026-11-13T15:20:49-08:00", False, "2026-11-13T16:20:49-08:00"), (None, False, None)])
def test_an_owner_row_an_older_install_saved_with_an_away_flag_reads_as_the_moment_he_reads_away_from(state_root: Path, heard: str | None, away: bool, moment: str | None) -> None:
    make("demo")
    data = state.load("demo")
    data["owner"] = {"heard": heard, "away": away}
    state.create(data)
    assert state.load("demo")["owner"] == {"heard": heard, "away": moment, "absence": None}


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
    monkeypatch.setenv(state.DRIVE_ENV, "beta")
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
    saved = state.stored("demo")
    del saved["subcoordinators"]
    saved["orders"] = {
        order_id: {"text": text, "jobs": [], "parts": {}, "promised": None, "project": project, "state": "open"}
        for order_id, project, text in (("o1", "Iris", "ship the run page"), ("o2", "Platform", "fix the alert"), ("o3", None, "a new order"))
    }
    state.store("demo", saved)
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


def test_a_jobs_agent_stays_with_the_coordinator_that_started_it_whatever_coordinator_holds_its_order_and_a_meta_held_job_takes_a_resume(state_root: Path) -> None:
    make("demo", "top", "2026-11-11T01:00:00-08:00")
    with state.edit("demo") as data:
        data["subcoordinators"]["Iris"] = {"session": "iris", "terminal": None, "cwd": "/work", "started": "2026-11-11T02:00:00-08:00"}
        data["orders"]["o1"] = {"text": "ship the run page", "jobs": [], "parts": {}, "promised": None, "project": "Iris"}
        data["orders"]["o2"] = {"text": "fix the watch", "jobs": [], "parts": {}, "promised": None, "project": "The drive"}
        data["orders"]["o3"] = {"text": "Drive tooling: fix the wake", "jobs": [], "parts": {}, "promised": None, "project": None}
        data["roles"]["meta"] = {"session": "meta-1", "terminal": None, "cwd": "/meta", "started": "2026-11-11T02:00:00-08:00"}
        ran = {
            "moved": ("o1", "local", "top", "agent-1"),
            "iris": ("o1", "local", "iris", "agent-2"),
            "top": ("o2", "local", "top", "agent-3"),
            "sprite": ("o1", "remote", "iris", None),
            "ended": ("o2", "local", "gone", "agent-4"),
            "tooling": ("o3", "local", "top", "agent-5"),
            "meta": ("o3", "local", "meta-1", "agent-6"),
        }
        for job_id, (order_id, where, session, agent) in ran.items():
            data["jobs"][job_id] = state.new_job(order_id, "builder", "build it", [], "2026-11-11T03:00:00-08:00") | {"where": where, "session": session, "agent": agent}
    data = state.load("demo")
    assert {job_id: state.agent_lane(data, job) for job_id, job in data["jobs"].items()} == {"moved": "root", "iris": "iris", "top": "root", "sprite": None, "ended": None, "tooling": None, "meta": "meta"}
    assert {job_id: state.job_lane(data, job) for job_id, job in data["jobs"].items()} == {"moved": "root", "iris": "iris", "top": "root", "sprite": "iris", "ended": "root", "tooling": "meta", "meta": "meta"}


def test_each_save_tells_each_lane_that_lost_open_orders_once_naming_them_and_their_holders_now(state_root: Path) -> None:
    created = "2026-11-11T01:00:00-08:00"
    make("demo", "top", created)
    with state.edit("demo") as data:
        for order_id, project in (("o1", "Iris"), ("o2", "Iris"), ("o3", None), ("o4", "Iris")):
            data["orders"][order_id] = state.new_order(f"order {order_id}", "terminal", None, {f"order {order_id}": state.new_part()}, created) | {"project": project}
        data["orders"]["o4"]["state"] = "done"
    start = state.events_end("demo")
    with state.edit("demo") as data:
        data["orders"]["o3"]["text"] = "a changed order"
    assert list(state.read_events("demo", start)) == []
    with state.edit("demo") as data:
        data["subcoordinators"]["Iris"] = {"session": "iris", "terminal": None, "cwd": "/work", "started": created}
        data["orders"]["o5"] = state.new_order("order o5", "terminal", None, {"order o5": state.new_part()}, created) | {"project": "Iris"}
    with state.batch("demo"):
        with state.edit("demo") as data:
            data["orders"]["o3"]["owner"] = "meta"
        with state.edit("demo") as data:
            data["orders"]["o1"]["project"] = None
    assert [{field: event[field] for field in ("kind", "lane", "orders", "to")} for event, _ in state.read_events("demo", start)] == [
        {"kind": state.ORDERS_MOVED, "lane": "root", "orders": ["o1", "o2"], "to": ["iris"]},
        {"kind": state.ORDERS_MOVED, "lane": "iris", "orders": ["o1"], "to": ["root"]},
        {"kind": state.ORDERS_MOVED, "lane": "root", "orders": ["o3"], "to": ["meta"]},
    ]


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
    data = state.new_drive("demo", "coordinator", None, "/work")
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
    assert kept[f"{MONOREPO}#4289"]["rework"] == {"by": "a1", "words": "bind it as @parent", "at": state.stamp(REFERENCE), "link": ""}
    assert (kept[f"{MONOREPO}#4279"]["rework"], kept[f"{MONOREPO}#4284"]["rework"], any("verdict" in pr for pr in kept.values())) == (None, None, False)


def test_a_waiting_line_puts_no_card_it_names_on_any_pr_since_a_card_holds_a_pr_only_by_its_holds_line(state_root: Path) -> None:
    data = kept_prs()
    data["jobs"]["j9"] = state.new_job("o226", "builder", "fix", [], state.stamp(REFERENCE))
    card = "https://acme.slack.com/archives/D123/p1794300000000200"
    state.take_result(data, "j9", f"WAITING #4289 and #4279 waits on {card} and demo:j9-3")
    assert [pr["waits"] for pr in data["prs"].values()] == [[]] * len(data["prs"])
    data["prs"][f"{MONOREPO}#4289"]["waits"] = [card, "demo:j9-3", f"{MONOREPO}#4302"]
    state.create(data)
    assert state.load("demo")["prs"][f"{MONOREPO}#4289"]["waits"] == [f"{MONOREPO}#4302"]


def test_a_waiting_naming_a_buildkite_build_waits_on_it_as_no_card_and_a_release_jobs_waiting_waits_on_the_build_its_release_started() -> None:
    data = kept_prs()
    build, started = "https://buildkite.com/forge/release/builds/14769", "https://buildkite.com/forge/release/builds/15020"
    data["jobs"]["j9"] = state.new_job("o226", "builder", "fix", [], state.stamp(REFERENCE))
    data["jobs"]["j10"] = state.new_job("o226", "builder", "release", [], state.stamp(REFERENCE)) | {"release_build": started}
    data["jobs"]["j11"] = state.new_job("o226", "builder", "fix", [], state.stamp(REFERENCE))
    line = f"WAITING {build} stops at its approve step"
    assert [state.result_refusal(data, job_id, said, said) is None for job_id, said in (("j9", line), ("j10", "WAITING release"), ("j11", "WAITING release"))] == [True, True, False]
    state.take_result(data, "j9", line, line)
    state.take_result(data, "j10", "WAITING release", "WAITING release")
    assert (data["jobs"]["j9"]["waits"], data["jobs"]["j10"]["waits"], state.card_wait(data, build), state.held_builds(data)) == ([build], [started], False, {build, started})
    data["jobs"]["j9"]["state"] = "done"
    assert state.held_builds(data) == {started}


def test_a_waiting_report_naming_his_open_card_only_below_its_first_line_waits_on_no_card() -> None:
    data = kept_prs()
    asked = state.new_question(None, "Which option?", state.stamp(REFERENCE), card="https://acme.slack.com/archives/D123/p1794300000000200") | {"waits": "demo:j8-1"}
    data["jobs"]["j8"] = state.new_job("o226", "reader", "research", [], state.stamp(REFERENCE)) | {"questions": [asked]}
    data["jobs"]["j9"] = state.new_job("o226", "builder", "fix", [], state.stamp(REFERENCE)) | {"prs": [state.pr_url(f"{MONOREPO}#4289")]}
    data["prs"][f"{MONOREPO}#4289"] |= {"checks": "fail", "failed_checks": ["test"]}
    line = "WAITING #4279, the fix for the test #4289 inherits"
    state.take_result(data, "j9", line, f"{line}\nDESIGN: none: every option on demo:j8-1 includes this change\n")
    assert (data["jobs"]["j9"]["waited"]["waits"], data["prs"][f"{MONOREPO}#4289"]["waits"]) == ([f"{MONOREPO}#4279"], [f"{MONOREPO}#4279"])


def test_a_builders_ask_keeps_the_step_each_of_its_prs_owes_beside_its_open_cards_and_its_next_other_result_drops_it() -> None:
    data = kept_prs()
    asked = state.new_question("/asks/j9.md", "Which pool runs the staging test?", state.stamp(REFERENCE), card="https://acme.slack.com/archives/D123/p1794300000000200") | {"waits": "demo:j9-1"}
    data["jobs"]["j9"] = state.new_job("o226", "builder", "fix", [], state.stamp(REFERENCE)) | {"prs": [state.pr_url(f"{MONOREPO}#4289")], "questions": [asked]}
    state.rework(data["prs"][f"{MONOREPO}#4289"], "o227", "Test it on staging before it merges", "https://acme.slack.com/archives/D123/p1794300000000600")
    state.take_result(data, "j9", "ASK /asks/j9.md")
    assert data["jobs"]["j9"]["waited"] == {"owed": {f"{MONOREPO}#4289": 'being reworked to his words "Test it on staging before it merges"'}, "waits": ["demo:j9-1"]}
    state.take_result(data, "j9", "PUSHED #4289")
    assert data["jobs"]["j9"]["waited"] is None


def test_load_keeps_the_red_an_older_install_kept_for_a_builders_waiting_as_the_step_it_owed_and_drops_one_kept_past_another_result(state_root: Path) -> None:
    data = kept_prs()
    red = {f"{MONOREPO}#4289": "its checks fail at abc: test"}
    for job_id, said in (("j9", "WAITING #4289 waits on demo:j9-3"), ("j10", "PUSHED #4289")):
        data["jobs"][job_id] = state.new_job("o226", "builder", "fix", [], state.stamp(REFERENCE)) | {"said": said, "waited": {"red": red, "waits": ["demo:j9-3"]}}
    state.create(data)
    jobs = state.load("demo")["jobs"]
    assert (jobs["j9"]["waited"], jobs["j10"]["waited"]) == ({"owed": red, "waits": ["demo:j9-3"]}, None)


def test_a_pr_reworked_to_his_words_owes_no_card_until_its_builders_ready_says_how_they_are_met_which_drops_his_approve_so_a_fresh_card_asks_it() -> None:
    data = kept_prs()
    data["jobs"] |= {job_id: state.new_job("o226", "builder", "fix", [], state.stamp(REFERENCE)) for job_id in ("j9", "j622")}
    pr = data["prs"][f"{MONOREPO}#4289"]
    pr["tap"] = {"label": "Approve", "at": state.stamp(REFERENCE), "head": "abc", "source": "slack", "picks": {}}
    state.rework(pr, "o227", "Topology should be local metadata", "https://acme.slack.com/archives/D123/p1794300000000600")
    assert (bool(pr["rework"]), state.asks_tap(pr), pr["tap"]["label"]) == (True, False, "Approve")
    state.take_result(data, "j9", "READY #4289", "READY #4289\nPR: #4289\nCHANGE: topology moves to the session's meta table")
    assert (stage.carded(pr), pr["rework"], pr["tap"], state.asks_tap(pr)) == (True, None, None, True)
    assert pr["fundamental"] == 'Reworked to your words "Topology should be local metadata": topology moves to the session\'s meta table'


def test_load_drops_a_tap_standing_beside_a_fundamental_change_so_his_fresh_card_asks_it_and_keeps_every_other_tap(state_root: Path) -> None:
    data = kept_prs()
    for number in (4289, 4279):
        data["prs"][f"{MONOREPO}#{number}"]["tap"] = state.new_tap("Approve", "abc", "slack", at=state.stamp(REFERENCE))
    data["prs"][f"{MONOREPO}#4289"]["fundamental"] = 'Reworked to your words "Own settings key per value": one indexed row per read'
    state.create(data)
    kept = state.load("demo")["prs"]
    assert (kept[f"{MONOREPO}#4289"]["tap"], kept[f"{MONOREPO}#4279"]["tap"]["label"], state.asks_tap(kept[f"{MONOREPO}#4289"])) == (None, "Approve", True)


def test_a_readers_ready_on_a_pr_reworked_to_his_words_leaves_it_reworking() -> None:
    data = kept_prs()
    state.rework(data["prs"][f"{MONOREPO}#4289"], "o227", "Topology should be local metadata", "https://acme.slack.com/archives/D123/p1794300000000600")
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


def test_two_questions_are_the_same_whatever_their_case_spacing_and_punctuation_and_an_untitled_one_matches_none() -> None:
    assert state.same_question("What runs a watch's  Sand function?", "what runs a watch's Sand function?")
    assert state.same_question("Who settles a tooling PR's conflict before the lander's squash merge", "Who settles a tooling PR’s conflict before the lander’s squash merge?")
    assert not state.same_question("Who settles a tooling PR's conflict?", "Who settles a tooling PRs conflict?")
    assert not state.same_question("What runs a watch's Sand function?", "What runs a watch's Sand function each interval?")
    assert not state.same_question(None, None)


def test_a_drive_saved_with_the_changes_each_part_waited_on_loads_its_parts_without_them(state_root: Path) -> None:
    make("demo")
    saved = state.stored("demo")
    parts = {
        "the interface": {"posted": None, "design": "j2", "after": None},
        "the reads": {"posted": None, "design": "j2", "after": ["the interface"]},
        "the order": {"posted": None},
    }
    saved["orders"] = {"o1": {"text": "move the tables", "jobs": [], "parts": parts, "promised": None}}
    state.store("demo", saved)
    loaded = state.load("demo")["orders"]["o1"]["parts"]
    assert {part: (held["design"], "after" in held) for part, held in loaded.items()} == {"the interface": ("j2", False), "the reads": ("j2", False), "the order": (None, False)}


def test_an_order_an_older_install_saved_keeps_the_threads_posts_on_it_went_in_its_jobs_cards_and_its_pr_cards_at_his_dm_root_once(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    created = "2026-11-10T18:46:23-08:00"
    data["orders"] = {
        "o1": state.new_order("give the sprite what it needs", "terminal", None, {"give the sprite what it needs": state.new_part()}, created) | {"jobs": ["j1"]},
        "o2": state.new_order("the run page", "slack:D123/1794200000.000100", ("D123", "1794200000.000100"), {"the run page": state.new_part()}, created),
    }
    for order in data["orders"].values():
        del order["threads"]
    data["orders"]["o2"]["asked_in"] = [["D123", "1794300000.000100"]]
    card = state.new_question(None, "Which sprite?", created, card="https://acme.slack.com/archives/D123/p1794520890022349")
    data["jobs"] = {"j1": state.new_job("o1", "builder", "give it", ["give the sprite what it needs"], created) | {"questions": [card]}}
    data["prs"] = {"yasyf/cc-skills#7": state.new_pr("https://github.com/yasyf/cc-skills/pull/7", "j1", "o1")}
    state.create(data)
    for link in ("https://acme.slack.com/archives/D123/p1794526059841239", "https://acme.slack.com/archives/D123/p1794200000000900?thread_ts=1794200000.000100"):
        state.append_event("demo", "said", text="land these", url=link, card=link, prs=["yasyf/cc-skills#7", "yasyf/cc-skills#8"])
    orders = state.load("demo")["orders"]
    assert [[*where[:2], where[3]] for where in orders["o1"]["threads"]] == [["D123", "1794520890.022349", "question"], ["D123", "1794526059.841239", "pr"]]
    assert (orders["o2"]["threads"], "asked_in" in orders["o2"]) == ([["D123", "1794300000.000100", created, "posted"]], False)


def test_a_drive_saved_keeping_a_staging_claims_thread_as_posted_or_not_at_all_keeps_it_as_members_once_while_his_dm_a_group_dm_an_edit_and_a_reply_join_nothing(
    state_root: Path,
) -> None:
    make("demo")
    data = state.load("demo")
    del data["members_kept"]
    created = "2026-11-14T10:00:00-08:00"
    staging, first, second = "C0BQSADC7SS", "1794687785.402399", "1794692852.753919"
    data["orders"] = {"o1": state.new_order("test the batch writer on staging", "slack:D123/1794600000.000100", ("D123", "1794600000.000100"), {"test it": state.new_part()}, created)}
    data["orders"]["o1"]["threads"] = [[staging, second, created, "posted"]]
    state.create(data)

    def said(channel: str | None, link: str, order: str = "o1", edited: bool = False) -> None:
        state.append_event("demo", "said", text="Claiming staging.", channel=channel, thread=None, url=link, card=None, order=order, visuals=[], prs=[], edited=edited)

    said(staging, f"https://acme.slack.com/archives/{staging}/p{first.replace('.', '')}")
    said(staging, f"https://acme.slack.com/archives/{staging}/p{second.replace('.', '')}")
    said(staging, f"https://acme.slack.com/archives/{staging}/p1794693000000100?thread_ts={first}&cid={staging}")
    said("U0REPORTER", "https://acme.slack.com/archives/C0MPIM0001/p1794690000000100")
    said("C0MPIM0001", "https://acme.slack.com/archives/C0MPIM0001/p1794690000000100", edited=True)
    said(None, "https://acme.slack.com/archives/D123/p1794691000000100")
    said(staging, f"https://acme.slack.com/archives/{staging}/p1794694000000100", order="o9")
    kept = [[staging, second, "members"], [staging, first, "members"]]
    assert [[*where[:2], where[3]] for where in state.load("demo")["orders"]["o1"]["threads"]] == kept
    with state.edit("demo") as drive:
        assert drive["members_kept"] is True
    said(staging, f"https://acme.slack.com/archives/{staging}/p1794695000000100")
    assert [[*where[:2], where[3]] for where in state.load("demo")["orders"]["o1"]["threads"]] == kept


def test_a_drive_saved_with_a_builders_done_on_its_own_open_pr_puts_it_back_waiting_on_that_pr_once_its_staging_test_delivered_by_its_first_check_proof_as_j1308s_was_not(
    state_root: Path,
) -> None:
    make("demo")
    data = state.load("demo")
    del data["open_dones_held"]
    created = "2026-11-14T10:00:00-08:00"
    change, staging, read_part = "ship the writer", "staging test of #42 before it lands", "the reader"
    proof, open_url, merged_url = "https://buildkite.com/forge/release/builds/16112", "https://github.com/Forge-AI/monorepo/pull/42", "https://github.com/Forge-AI/monorepo/pull/43"
    done = f"DONE {proof} refused and https://buildkite.com/forge/release/builds/16121 passed the staging test of #42"
    data["orders"] = {"o1": state.new_order("ship the writer", "slack:D123/1794600000.000100", ("D123", "1794600000.000100"), {part: state.new_part() for part in (change, staging, read_part)}, created)}
    data["orders"]["o1"] |= {"kind": "build", "jobs": ["j1", "j2"]}
    data["jobs"] = {
        "j1": state.new_job("o1", "builder", "ship it", [change, staging], created) | {"state": "done", "said": done, "prs": [open_url]},
        "j2": state.new_job("o1", "builder", "read it", [read_part], created) | {"state": "done", "said": "DONE the reader ships", "prs": [merged_url]},
    }
    data["prs"] = {
        "Forge-AI/monorepo#42": state.new_pr(open_url, "j1", "o1"),
        "Forge-AI/monorepo#43": state.new_pr(merged_url, "j2", "o1") | {"state": "merged", "merged_sha": "fedcba9876543210"},
    }
    state.create(data)
    state.keep_report("demo", "j1", done, f"{done}\nCHECK: {proof} https://buildkite.com/forge/release/builds/16121\nCASE a rollback refused: pass {proof}\n")
    data = state.load("demo")
    held, kept = data["jobs"]["j1"], data["jobs"]["j2"]
    assert (held["state"], held["said"], held["waits"], kept["state"], kept["said"]) == ("waiting", f"WAITING {open_url} to merge: {done}", ["Forge-AI/monorepo#42"], "done", "DONE the reader ships")
    posted = data["orders"]["o1"]["parts"][staging]
    assert ([state.delivered(data, "o1", part) for part in (change, staging, read_part)], posted["posted"], posted["posted_by"]) == ([False, True, False], proof, "j1")
    with state.edit("demo") as drive:
        assert drive["open_dones_held"] is True


STAGING, PROOF = "staging test of #42 before it lands", "https://buildkite.com/forge/release/builds/16121"


def staging_drive(*testers: str) -> dict:
    make("demo")
    data = state.load("demo")
    created = "2026-11-14T10:00:00-08:00"
    data["orders"] = {"o1": state.new_order("soak the writer", "slack:D123/1794600000.000100", ("D123", "1794600000.000100"), {STAGING: state.new_part()}, created) | {"kind": "build", "jobs": list(testers)}}
    data["jobs"] = {job_id: state.new_job("o1", "builder", "run the staging test of #42", [STAGING], created) for job_id in testers}
    return data


def staging_return(data: dict, job_id: str, line: str, cases: str, proof: str = PROOF) -> None:
    state.take_result(data, job_id, line, f"{line}\nCHECK: {proof} https://buildkite.com/forge/release/builds/16062\n{cases}")


def staging_reads(data: dict) -> tuple[bool, str | None, str | None]:
    held = data["orders"]["o1"]["parts"][STAGING]
    return state.delivered(data, "o1", STAGING), held["posted"], held["posted_by"]


@pytest.mark.parametrize(
    ("cases", "passed"),
    [
        ("CASE a rollback refused: pass\nCASE a write lands: PASS on the cut\n", True),
        ("CASE a rollback refused: pass\nCASE a write lands: fail: the writer kept the old row\n", False),
        ("CASE a rollback refused: pass\nCASE a write lands: not exercised\n", False),
        ("CASE a write lands: skipped\n", False),
        ("Case 5 a write lands: fail: the writer kept the old row\n", False),
    ],
    ids=["every-case-passes", "a-case-fails", "a-case-not-exercised", "a-case-skipped", "no-case-line"],
)
def test_a_staging_test_part_is_delivered_by_its_testers_done_only_when_every_case_line_reads_pass_as_j4832s_and_j4850s_were_not(
    state_root: Path, cases: str, passed: bool
) -> None:
    data = staging_drive("j1")
    staging_return(data, "j1", "DONE ran the staging test of #42 on cut 1", cases)
    assert (data["jobs"]["j1"]["state"], staging_reads(data)) == ("done", (True, PROOF, "j1") if passed else (False, None, None))


def test_a_staging_test_parts_full_pass_holds_only_while_its_tester_is_the_newest_not_stopped_and_still_holds_that_done(state_root: Path) -> None:
    data = staging_drive("j1")
    staging_return(data, "j1", "DONE every case of #42 passed", "CASE a rollback refused: pass\n")
    passed = staging_reads(data)
    data["jobs"]["j2"] = state.new_job("o1", "builder", "rerun the staging test of #42 at its new head", [STAGING], state.stamp())
    data["orders"]["o1"]["jobs"].append("j2")
    rerun = staging_reads(data)
    data["jobs"]["j2"]["state"] = "stopped"
    stopped = staging_reads(data)
    state.take_result(data, "j1", "BLOCKED staging is frozen for an incident")
    blocked = staging_reads(data)
    data["jobs"]["j2"]["state"] = "running"
    newer = "https://buildkite.com/forge/release/builds/16200"
    staging_return(data, "j2", "DONE every case of #42 passed at its new head", "CASE a rollback refused: pass\n", newer)
    replaced = staging_reads(data)
    staging_return(data, "j2", "DONE #42 failed a case on its rerun", "CASE a rollback refused: fail: the rollback went through\n", newer)
    assert [passed, rerun, stopped, blocked, replaced, staging_reads(data)] == [
        (True, PROOF, "j1"),
        (False, PROOF, "j1"),
        (True, PROOF, "j1"),
        (False, None, None),
        (True, newer, "j2"),
        (False, None, None),
    ]


def git_head(clone: Path, *args: str) -> str:
    subprocess.run(["git", "-C", str(clone), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)
    return subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def test_a_staging_tests_done_frees_the_prs_held_on_it_only_by_a_check_build_that_deployed_a_commit_carrying_their_heads_to_staging_as_j4507s_soak_build_did_not(
    state_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = tmp_path / "monorepo"
    (clone / "infra" / "envs").mkdir(parents=True)
    (clone / "infra" / "envs" / "plat-usw2-staging.yaml").write_text("name: plat-usw2-staging\nstage: staging\n")
    (clone / "infra" / "envs" / "plat-usw2-prod.yaml").write_text("name: plat-usw2-prod\nstage: prod\n")
    subprocess.run(["git", "init", "-q", "-b", "dev", str(clone)], check=True)
    git_head(clone, "commit", "-q", "--allow-empty", "-m", "base")
    git_head(clone, "switch", "-q", "-c", "parts")
    heads = []
    for path, subject in (("layout.rs", "a table layout is key ranges"), ("reach.rs", "routed by Layout::reach")):
        (clone / path).write_text(f"{subject}\n")
        git_head(clone, "add", path)
        heads.append(git_head(clone, "commit", "-q", "-m", subject))
    bottom, tip = heads
    git_head(clone, "switch", "-q", "dev")
    soak = git_head(clone, "commit", "-q", "--allow-empty", "-m", "soak scenarios")
    git_head(clone, "switch", "-q", "-c", "half")
    half = git_head(clone, "cherry-pick", bottom)
    git_head(clone, "switch", "-q", "-c", "cut", "dev")
    cut = git_head(clone, "cherry-pick", bottom, tip)
    proof = tmp_path / "proof.sh"
    proof.write_text("#!/bin/sh\nexit 0\n")
    proof.chmod(0o755)
    data = state.new_drive("demo", "coordinator", None, str(clone))
    state.create(data)
    data = state.load("demo")
    created, part = "2026-11-14T10:00:00-08:00", "a table layout is key ranges over files"
    data["orders"] = {"o1": state.new_order("ship table layouts", "slack:D123/1794600000.000100", ("D123", "1794600000.000100"), {part: state.new_part()}, created) | {"kind": "build", "jobs": ["j1", "j2"]}}
    data["jobs"] = {
        "j1": state.new_job("o1", "builder", "ship table layouts", [part], created) | {"state": "waiting", "repro": str(proof)},
        "j2": state.new_job("o1", "builder", "the stack deployed to staging, frozen there and thoroughly tested", [part], created) | {"state": "running", "builds_nothing": True, "repro": str(proof)},
    }
    keys = [f"{MONOREPO}#4201", f"{MONOREPO}#4202"]
    data["prs"] = {key: state.new_pr(f"https://github.com/{MONOREPO}/pull/{key.rsplit('#', 1)[1]}", "j1", "o1") | {"head": head, "waits": ["j2"]} for key, head in zip(keys, (bottom, tip))}
    apply = {"state": "passed", "step_key": "apply-sandsql--plat-usw2-staging"}
    builds = {
        "16203": {"commit": soak, "jobs": [{"state": "passed", "step_key": "soak-staging"}]},
        "16085": {"commit": half, "jobs": [apply]},
        "16090": {"commit": cut, "jobs": [{"state": "passed", "step_key": "apply-sandsql--plat-usw2-prod"}, {"state": "failed", "step_key": "apply-sandsql--plat-usw2-staging"}]},
        "16086": {"commit": cut, "jobs": [apply]},
    }
    monkeypatch.setattr(github, "build", lambda pipeline, number: builds[number])

    def done(job_id: str, *numbers: str) -> str | None:
        report = "DONE the stack ran its tests on staging\n" + "".join(f"CHECK: https://buildkite.com/forge/release/builds/{number}\n" for number in numbers)
        return state.result_refusal(data, job_id, report.splitlines()[0], report)

    soaked, halved, prod, none = done("j2", "16203"), done("j2", "16085"), done("j2", "16090"), done("j2")
    assert "j2's DONE frees Forge-AI/monorepo#4201, Forge-AI/monorepo#4202 from waiting on its staging test" in soaked
    assert "https://buildkite.com/forge/release/builds/16203 deployed nothing to staging" in soaked
    assert halved.startswith("j2's DONE frees Forge-AI/monorepo#4202 from waiting")
    assert f"git cherry finds Forge-AI/monorepo#4202's {tip[:12]} missing from {half[:12]}, which https://buildkite.com/forge/release/builds/16085 deployed to plat-usw2-staging" in halved
    assert "16090 deployed nothing to staging" in prod
    assert none.endswith("they name no Buildkite build. Name on a CHECK: line the release build that deployed a commit carrying each one's head to staging, or return WAITING on what still holds them, such as staging:<target>/<env>@<commit> while staging does not run it yet")
    assert [done("j2", "16086"), done("j2", "16203", "16086"), done("j1", "16203")] == [None, None, None]
    data["jobs"]["j2"]["goal"] = "read the layout's key ranges"
    assert done("j2", "16203") is None


def test_a_full_pass_by_the_builder_of_the_pr_it_tests_delivers_its_staging_part_while_its_done_waits_on_that_pr_and_after_the_watch_takes_its_merge(state_root: Path) -> None:
    data = staging_drive("j1")
    url = "https://github.com/Forge-AI/monorepo/pull/42"
    data["jobs"]["j1"]["prs"] = [url]
    data["prs"] = {"Forge-AI/monorepo#42": state.new_pr(url, "j1", "o1")}
    done = "DONE every case of #42 passed"
    staging_return(data, "j1", done, "CASE a rollback refused: pass\n")
    held = (data["jobs"]["j1"]["state"], staging_reads(data))
    data["prs"]["Forge-AI/monorepo#42"] |= {"state": "merged", "merged_sha": "fedcba9876543210"}
    state.take_result(data, "j1", f"DONE {url} merged: Forge-AI/monorepo#42 at fedcba987654")
    assert [held, (data["jobs"]["j1"]["state"], staging_reads(data))] == [("waiting", (True, PROOF, "j1")), ("done", (True, PROOF, "j1"))]


APPROVE = {"label": "Approve", "at": "2026-11-14T10:30:00-08:00", "head": "aaaaaaa111", "source": "slack", "picks": {}}


def test_a_staging_tests_full_pass_takes_back_his_approve_given_before_it_on_each_open_pr_its_part_holds_so_only_an_approve_after_it_lands_the_pr(state_root: Path) -> None:
    data = staging_drive("j1")
    data["jobs"] |= {job_id: state.new_job("o1", "builder", "ship the writer", [], "2026-11-14T10:00:00-08:00") for job_id in ("j2", "j3")}
    data["orders"]["o1"]["jobs"] += ["j2", "j3"]
    tested, other = f"{MONOREPO}#42", f"{MONOREPO}#43"
    data["prs"] = {
        tested: state.new_pr(state.pr_url(tested), "j2", "o1") | {"tap": dict(APPROVE), "waits": [f"o1:{STAGING}"]},
        other: state.new_pr(state.pr_url(other), "j3", "o1") | {"tap": dict(APPROVE)},
    }
    staging_return(data, "j1", "DONE #42 failed a case", "CASE a rollback refused: fail: the rollback went through\n")
    failed = [data["prs"][key]["tap"] for key in (tested, other)]
    staging_return(data, "j1", "DONE every case of #42 passed", "CASE a rollback refused: pass\n")
    taken = (data["prs"][tested]["tap"], data["prs"][tested]["fundamental"], data["prs"][other]["tap"])
    data["prs"][tested].update(tap=dict(APPROVE), fundamental=None)
    staging_return(data, "j1", "DONE every case of #42 passed", "CASE a rollback refused: pass\n")
    assert (failed, taken, data["prs"][tested]["tap"]) == (
        [APPROVE, APPROVE],
        (None, f"its staging test passed after your Approve, so only an Approve you give now lands it: {PROOF}", APPROVE),
        APPROVE,
    )


def test_a_drive_saved_delivering_staging_test_parts_by_any_done_keeps_each_only_on_the_newest_tester_whose_full_pass_named_its_proof_and_pr_once_as_j4683s_and_j4832s_were_not(
    state_root: Path,
) -> None:
    data = staging_drive()
    del data["staging_passes_named"]
    data["staging_passes_owned"] = True
    created = "2026-11-14T10:00:00-08:00"
    owned, rerun, running, unposted, compared, unnamed = (state.STAGING_TEST.format(number=number) for number in (42, 43, 44, 45, 46, 47))
    data["orders"]["o1"]["parts"] = {part: state.new_part() | {"posted": PROOF if part != unposted else None} for part in (owned, rerun, running, unposted, compared, unnamed)}
    for part in (owned, rerun, running, unposted):
        del data["orders"]["o1"]["parts"][part]["posted_by"]
    data["orders"]["o1"]["parts"][compared]["posted_by"] = "j6"
    data["orders"]["o1"]["parts"][unnamed]["posted_by"] = "j7"
    testers = {"j1": owned, "j2": rerun, "j3": rerun, "j4": running, "j5": unposted, "j6": compared, "j7": unnamed}
    data["orders"]["o1"]["jobs"] = list(testers)
    done = f"DONE {PROOF} ran the staging test"
    data["jobs"] = {job_id: state.new_job("o1", "builder", "run the staging test", [part], created) | {"state": "done", "said": done} for job_id, part in testers.items()}
    data["jobs"]["j3"] |= {"state": "waiting", "said": "WAITING on a staging slot"}
    data["jobs"]["j4"] |= {"state": "running", "said": None}
    state.create(data)
    for job_id in ("j1", "j2", "j4", "j5"):
        state.keep_report("demo", job_id, done, f"{done} of #{state.STAGING_PART.fullmatch(testers[job_id])['number']}\nCHECK: {PROOF}\nCASE a rollback refused: pass\n")
    state.keep_report("demo", "j6", done, f"{done} of #46\nCHECK: {PROOF}\n")
    state.keep_report("demo", "j7", done, f"{done} of #42\nCHECK: {PROOF}\nCASE a rollback refused: pass\n")
    data = state.load("demo")
    parts = data["orders"]["o1"]["parts"]
    assert [(state.delivered(data, "o1", part), parts[part]["posted"], parts[part]["posted_by"]) for part in (owned, rerun, running, unposted, compared, unnamed)] == [
        (True, PROOF, "j1"),
        (False, PROOF, "j2"),
        (False, PROOF, "j4"),
        (False, None, None),
        (False, None, None),
        (False, None, None),
    ]
    with state.edit("demo") as drive:
        assert (drive["staging_passes_named"], "staging_passes_owned" in drive) == (True, False)


def test_a_part_an_older_install_saved_with_spaces_around_its_name_loads_trimmed_wherever_a_job_or_pr_names_it(state_root: Path) -> None:
    make("demo")
    saved = state.stored("demo")
    cut = "both PRs he approved this morning and the stack he tapped an hour later sat for "
    parts = {cut: {"posted": None, "design": None}, " fix": {"posted": None, "design": None}}
    saved["orders"] = {"o1": {"text": "the stack sat", "jobs": ["j1", "j2"], "parts": parts, "promised": None}, "o2": {"text": "why", "jobs": [], "parts": {"cause ": {"posted": None}}, "promised": None}}
    saved["jobs"] = {"j1": state.new_job("o1", "reader", "find why", [cut], state.stamp()), "j2": state.new_job("o2", "builder", "fix it", ["cause "], state.stamp()) | {"carries": {"o1": [" fix"]}}}
    saved["prs"] = {"yasyf/cc-skills#7": state.new_pr("https://github.com/yasyf/cc-skills/pull/7", "j2", "o1") | {"parts": [" fix"]}}
    state.store("demo", saved)
    loaded = state.load("demo")
    whole = cut.rstrip()
    assert list(loaded["orders"]["o1"]["parts"]) == [whole, "fix"]
    assert list(loaded["orders"]["o2"]["parts"]) == ["cause"]
    assert (loaded["jobs"]["j1"]["parts"], loaded["jobs"]["j2"]["parts"], loaded["jobs"]["j2"]["carries"]) == ([whole], ["cause"], {"o1": ["fix"]})
    assert loaded["prs"]["yasyf/cc-skills#7"]["parts"] == ["fix"]
    assert state.whole_order(f"{cut}two hours") == whole


def test_each_seq_an_older_install_saved_with_the_hash_cci_prints_loads_as_its_digits(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    quiet = {"record": "#697593", "at": None, "handler": None, "heard": None, "news": {}}
    watched = state.new_watch("slack:C0822AHFY3G/1794493064.110609", state.now()) | {"record": "#697594"}
    order = state.new_order("fix it", "terminal", None, {"fix it": state.new_part()}, state.stamp())
    data["orders"] = {"o1": order | {"jobs": ["j1"], "closed_on": "#697590", "quiet": quiet, "watch": watched}}
    question = state.new_question(None, "Which ring?", state.stamp(), closed=state.new_close("answered", "Ring", None, seq="#697591"))
    undelivered = {"seq": "#697592", "words": "Ring", "at": state.stamp(), "said": None, "request": "req-ring"}
    data["jobs"] = {"j1": state.new_job("o1", "builder", "fix it", ["fix it"], state.stamp()) | {"questions": [question], "undelivered": [undelivered]}}
    data["catch_up"] = state.new_catch_up(state.stamp(), ["o1"]) | {"record": "#697595"}
    state.create(data)
    loaded = state.load("demo")
    order, job = loaded["orders"]["o1"], loaded["jobs"]["j1"]
    assert (order["closed_on"], order["quiet"]["record"], order["watch"]["record"], job["questions"][0]["closed"]["seq"], job["undelivered"][0]["seq"], loaded["catch_up"]["record"]) == (
        "697590",
        "697593",
        "697594",
        "697591",
        "697592",
        "697595",
    )


WAITS = ["release:api:Forge-AI/monorepo#42", "release:eks:Forge-AI/monorepo#42"]


def test_the_news_an_older_install_kept_with_each_jobs_waits_loads_as_the_facts_the_watch_compares_now(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    news = {"j1": ["done", [], [], "DONE it"], "j2": ["waiting", WAITS, [], None], "j3": ["waiting", WAITS, [], "WAITING on the api release"]}
    quiet = {"record": None, "at": None, "handler": None, "heard": None, "news": news}
    order = state.new_order("fix it", "terminal", None, {"fix it": state.new_part()}, state.stamp())
    data["orders"] = {"o1": order | {"jobs": ["j1", "j2", "j3"], "quiet": quiet}}
    data["jobs"] = {job_id: state.new_job("o1", "builder", "fix it", ["fix it"], state.stamp()) for job_id in ("j1", "j2", "j3")}
    state.create(data)
    assert state.load("demo")["orders"]["o1"]["quiet"]["news"] == {
        "j1": ["done", False, [], "DONE it"],
        "j2": ["waiting", True, [], None],
        "j3": ["waiting", True, [], "WAITING on the api release"],
    }


def test_the_news_an_older_install_kept_with_each_prs_head_or_card_loads_as_the_facts_the_watch_compares_now(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    news = {"yasyf/cc-skills#7": ["open", "2809a4c", "fail", False, None], "yasyf/cc-skills#8": ["open", "2809a4c", "fail", False, "card", "Approve"], "j1": ["done", [], [], "DONE it"]}
    quiet = {"record": None, "at": None, "handler": None, "heard": None, "news": news}
    order = state.new_order("fix it", "terminal", None, {"fix it": state.new_part()}, state.stamp())
    data["orders"] = {"o1": order | {"jobs": ["j1"], "quiet": quiet}}
    data["jobs"] = {"j1": state.new_job("o1", "builder", "fix it", ["fix it"], state.stamp())}
    data["prs"] = {f"yasyf/cc-skills#{n}": state.new_pr(f"https://github.com/yasyf/cc-skills/pull/{n}", "j1", "o1") for n in (7, 8)}
    state.create(data)
    assert state.load("demo")["orders"]["o1"]["quiet"]["news"] == {
        "yasyf/cc-skills#7": ["open", "fail", False, None],
        "yasyf/cc-skills#8": ["open", "fail", False, "Approve"],
        "j1": ["done", False, [], "DONE it"],
    }


def test_a_part_an_older_install_saved_with_a_kind_loads_without_it(state_root: Path) -> None:
    make("demo")
    saved = state.stored("demo")
    saved["orders"] = {"o1": {"text": "why, then fix it", "jobs": [], "parts": {"why": {"posted": None, "orphan_said": None, "kind": "answer"}, "fix": {"posted": None, "orphan_said": None, "kind": "build"}}, "promised": None}}
    state.store("demo", saved)
    assert state.load("demo")["orders"]["o1"]["parts"] == {"why": state.new_part() | {"answer": True}, "fix": state.new_part() | {"answer": True}}


def test_a_detached_verb_names_its_drive_its_log_and_no_session_in_its_environment(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make("demo")
    started = []

    class Started:
        pid = 4242

        def __init__(self, argv: list[str], **options: object) -> None:
            started.append((argv, options))

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    monkeypatch.setattr(state.subprocess, "Popen", Started)
    monkeypatch.setattr(install, "plugin_copies_first", lambda: "/cache/cc-context/0.96.10/bin:/opt/homebrew/bin")
    assert state.detach("demo", ["pr", "land", "https://github.com/yasyf/cc-skills/pull/7"], "landings.log") == 4242
    [(argv, options)] = started
    assert argv[1:] == ["pr", "land", "https://github.com/yasyf/cc-skills/pull/7", "--drive", "demo"]
    assert options["env"][state.DRIVE_ENV] == "demo" and options["env"][state.DETACHED_ENV] == "landings.log" and "CLAUDE_CODE_SESSION_ID" not in options["env"]
    assert options["env"]["PATH"] == "/cache/cc-context/0.96.10/bin:/opt/homebrew/bin"


def test_projection_indexes_pr_jobs_once_preserving_order_and_scope() -> None:
    class Jobs(dict):
        scans = 0

        def items(self):
            self.scans += 1
            return super().items()

    jobs = Jobs({f"j{n}": {"prs": [f"pr{n}", "shared", "shared"]} for n in range(64)})
    data = {"jobs": jobs}
    prs = [{"job": f"j{n}", "url": f"pr{n}"} for n in range(64)] + [{"job": "outside", "url": "shared"}, {"job": "outside", "url": "unknown"}]
    expected = [state.pr_jobs(data, pr) for pr in prs]
    assert jobs.scans == len(prs)
    jobs.scans = 0
    other = {"jobs": {"other": {"prs": ["shared"]}}}
    with state.indexed_pr_jobs(data):
        assert [state.pr_jobs(data, pr) for pr in prs] == expected
        assert jobs.scans == 1
        assert state.pr_jobs(other, prs[-2]) == ["outside", "other"]
        with state.indexed_pr_jobs(other):
            assert state.pr_jobs(other, prs[-2]) == ["outside", "other"]
        assert state.pr_jobs(data, prs[-2]) == expected[-2]
        assert jobs.scans == 1
    jobs["new"] = {"prs": ["shared"]}
    assert state.pr_jobs(data, prs[-2]) == [*expected[-2], "new"]
    assert jobs.scans == 2
    with pytest.raises(RuntimeError), state.indexed_pr_jobs(data):
        raise RuntimeError("projection failed")
    assert state.PR_JOB_INDEX.get() is None


def test_the_demo_drive_a_release_saves_for_the_next_release_to_try_reads_with_its_own_drive_status(
    tmp_path: Path, run, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = tmp_path / "saved"
    subprocess.run([sys.executable, "-I", str(Path(__file__).resolve().parents[1] / "tools" / "demo-drive"), str(saved)], check=True)
    monkeypatch.setenv("LONG_RUNNING_STATE", str(saved))

    code, out = run("status", "--drive", "demo")

    assert code == 0
    assert len(state.load("demo")["jobs"]) == len(state.JOB_KINDS) * len(state.JOB_STATES)


def test_j4060_a_drive_saved_with_failed_builders_another_builder_took_over_stops_each_replaced_by_the_newest_carrying_all_its_parts(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["replaced_ended"]
    moment = state.stamp()
    fix, post = "fix the proof", "post the summary"
    key = f"{MONOREPO}#1"
    data["orders"] = {
        "o1": state.new_order("fix the proof and post it", "terminal", None, {fix: state.new_part(), post: state.new_part()}, moment) | {"jobs": ["j1", "j2", "j3", "j4", "j5"]},
        "o2": state.new_order("thread the post", "terminal", None, {"thread it": state.new_part()}, moment) | {"jobs": ["j1"]},
    }
    data["jobs"] = {
        "j1": state.new_job("o1", "builder", "fix it", [fix], moment) | {"state": "failed", "carries": {"o2": ["thread it"]}, "prs": [state.pr_url(key)]},
        "j2": state.new_job("o1", "builder", "fix it again", [fix], moment) | {"state": "failed"},
        "j3": state.new_job("o1", "builder", "fix and post it", [fix, post], moment) | {"state": "failed", "said": None},
        "j4": state.new_job("o1", "builder", "fix it a third way", [fix], moment) | {"state": "waiting", "said": None},
        "j5": state.new_job("o1", "builder", "called off", [fix, post], moment) | {"state": "stopped", "said": None},
    }
    data["prs"] = {key: state.new_pr(state.pr_url(key), "j1", "o1")}
    state.create(data)
    data = state.load("demo")
    jobs = data["jobs"]
    assert {job_id: (job["state"], job["said"]) for job_id, job in jobs.items()} == {
        "j1": ("stopped", "replaced by j4"),
        "j2": ("stopped", "replaced by j4"),
        "j3": ("failed", None),
        "j4": ("waiting", None),
        "j5": ("stopped", None),
    }
    assert (data["prs"][key]["job"], jobs["j4"]["prs"], jobs["j4"]["carries"], data["replaced_ended"]) == ("j4", [state.pr_url(key)], {"o2": ["thread it"]}, True)


def test_a_job_saved_done_or_stopped_drops_the_texts_it_holds_one_folded_into_another_hands_them_over_and_a_failed_one_keeps_them_for_its_relaunch(state_root: Path) -> None:
    make("demo")
    moment = state.stamp()
    wake = "Your lane went quiet: carry on from your last step"
    data = state.load("demo")
    data["orders"] = {"o1": state.new_order("spike it", "terminal", None, {"spike": state.new_part()}, moment) | {"jobs": ["j1", "j2", "j3", "j4", "j5"]}}
    data["jobs"] = {job_id: state.new_job("o1", "builder", "spike it", ["spike"], moment) for job_id in ("j1", "j2", "j3", "j4", "j5")}
    state.create(data)
    with state.edit("demo") as data:
        for job in data["jobs"].values():
            state.hold_text(job, wake, None)
        state.hold_text(data["jobs"]["j4"], "rebase on main first", "coordinator")
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(state="done", said="DONE the spike ran")
        data["jobs"]["j2"].update(state="stopped", said="replaced by one job per part")
        data["jobs"]["j3"].update(state="failed")
        state.fold(data, "j4", "j5")
        data["jobs"]["j4"].update(state="stopped", said="replaced by j5")
    data = state.load("demo")
    assert {job_id: [kept["words"] for kept in job["undelivered"]] for job_id, job in data["jobs"].items()} == {
        "j1": [],
        "j2": [],
        "j3": [wake],
        "j4": [],
        "j5": [wake, "rebase on main first"],
    }
    assert ["holds to deliver" in render.job_line(data, job_id, data["jobs"][job_id]) for job_id in ("j1", "j2", "j4")] == [False, False, False]
    with state.edit("demo") as data:
        state.hold_text(data["jobs"]["j1"], "one more round", "coordinator")
    assert [kept["words"] for kept in state.load("demo")["jobs"]["j1"]["undelivered"]] == ["one more round"]


def test_a_drive_saved_while_its_done_and_stopped_jobs_hold_texts_drops_them_once_but_a_standing_parts_last_pass_keeps_its_for_the_next_pass(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["finished_texts_dropped"]
    moment = state.stamp()
    box = "codex test box"
    data["orders"] = {
        "o1": state.new_order("spike it", "terminal", None, {"spike": state.new_part(), box: state.new_part() | {"standing": "test runs on the devbox, round after round"}}, moment)
        | {"jobs": ["j7", "j10", "j20", "j30", "j40"]}
    }
    data["jobs"] = {
        job_id: state.new_job("o1", kind, "spike it", [part], moment)
        | {"state": job_state, "undelivered": [{"seq": None, "words": "Your lane went quiet: carry on", "sender": None, "at": moment, "said": None, "request": f"req-{job_id}"}]}
        for job_id, kind, part, job_state in (
            ("j7", "builder", "spike", "stopped"),
            ("j10", "builder", "spike", "done"),
            ("j20", "builder", "spike", "failed"),
            ("j30", "builder", "spike", "waiting"),
            ("j40", "codex", box, "stopped"),
        )
    }
    state.create(data)
    data = state.load("demo")
    assert ({job_id: len(job["undelivered"]) for job_id, job in data["jobs"].items()}, data["finished_texts_dropped"]) == ({"j7": 0, "j10": 0, "j20": 1, "j30": 1, "j40": 1}, True)


def test_j4060_a_builder_failing_after_the_builder_replacing_it_was_added_stops_at_its_result(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    moment = state.stamp()
    data["orders"] = {"o1": state.new_order("fix the proof", "terminal", None, {"fix the proof": state.new_part()}, moment) | {"jobs": ["j1", "j2"]}}
    data["jobs"] = {
        "j1": state.new_job("o1", "builder", "fix it", ["fix the proof"], moment) | {"state": "waiting", "said": "PUSHED https://github.com/yasyf/cc-slack/pull/406"},
        "j2": state.new_job("o1", "builder", "take its next event", ["fix the proof"], moment),
    }
    state.create(data)
    with state.edit("demo") as data:
        state.take_result(data, "j1", "BLOCKED repro/j1.py exits 1")
    jobs = state.load("demo")["jobs"]
    assert {job_id: (job["state"], job["said"]) for job_id, job in jobs.items()} == {"j1": ("stopped", "replaced by j2"), "j2": ("waiting", state.NOT_STARTED)}


def test_j5802_a_failed_builder_stays_failed_beside_a_later_builder_that_already_ended(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    moment = state.stamp()
    data["orders"] = {"o1": state.new_order("fix the proof", "terminal", None, {"fix the proof": state.new_part()}, moment) | {"jobs": ["j1", "j2"]}}
    data["jobs"] = {
        "j1": state.new_job("o1", "builder", "fix it", ["fix the proof"], moment) | {"state": "waiting", "said": "PUSHED https://github.com/yasyf/cc-slack/pull/406"},
        "j2": state.new_job("o1", "builder", "land it", ["fix the proof"], moment) | {"state": "done", "said": "DONE https://github.com/yasyf/cc-slack/pull/406 merged; landed"},
    }
    state.create(data)
    with state.edit("demo") as data:
        state.take_result(data, "j1", "BLOCKED repro/j1.py exits 1")
    jobs = state.load("demo")["jobs"]
    assert {job_id: (job["state"], job["said"]) for job_id, job in jobs.items()} == {
        "j1": ("failed", "BLOCKED repro/j1.py exits 1"),
        "j2": ("done", "DONE https://github.com/yasyf/cc-slack/pull/406 merged; landed"),
    }


def test_j5802_a_drive_whose_failed_job_an_older_install_stopped_for_a_job_already_finished_reads_it_failed_on_its_line_and_takes_the_carries_it_folded_back(
    state_root: Path,
) -> None:
    make("demo")
    data = state.load("demo")
    del data["finished_replacers_undone"]
    now = state.now()
    fix, thread = "fix the proof", "thread it"
    data["orders"] = {
        "o1": state.new_order("fix the proof", "terminal", None, {fix: state.new_part()}, state.stamp(now)) | {"jobs": ["j1", "j2", "j3", "j4"]},
        "o2": state.new_order("thread the post", "terminal", None, {thread: state.new_part()}, state.stamp(now)) | {"jobs": ["j1", "j2"]},
    }
    data["jobs"] = {
        "j1": state.new_job("o1", "builder", "prove it", [fix], state.stamp(now)) | {"state": "stopped", "said": "replaced by j2", "carries": {"o2": [thread]}},
        "j2": state.new_job("o1", "builder", "land it", [fix], state.stamp(now)) | {"state": "done", "said": "DONE landed", "carries": {"o2": [thread]}, "updated": state.stamp(now - timedelta(hours=1))},
        "j3": state.new_job("o1", "builder", "fix it", [fix], state.stamp(now)) | {"state": "stopped", "said": "replaced by j4", "updated": state.stamp(now - timedelta(hours=2))},
        "j4": state.new_job("o1", "builder", "fix it again", [fix], state.stamp(now)) | {"state": "done", "said": "DONE fixed", "updated": state.stamp(now - timedelta(hours=1))},
    }
    state.create(data)
    state.keep_report("demo", "j1", "BLOCKED repro/j1.sh exits 1", "BLOCKED repro/j1.sh exits 1\nCHECK: repro/j1.sh\n")
    data = state.load("demo")
    jobs = data["jobs"]
    assert {job_id: (job["state"], job["said"]) for job_id, job in jobs.items()} == {
        "j1": ("failed", "BLOCKED repro/j1.sh exits 1"),
        "j2": ("done", "DONE landed"),
        "j3": ("stopped", "replaced by j4"),
        "j4": ("done", "DONE fixed"),
    }
    assert (jobs["j1"]["carries"], jobs["j2"]["carries"], data["finished_replacers_undone"]) == ({"o2": [thread]}, {}, True)


def test_j5802_a_done_design_carries_no_part_a_builder_of_another_change_it_listed_holds(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    moment = state.stamp()
    follow_up, pages, pin, replica = "a page map follow-up", "write a page map", "pin the fork", "open the replica"
    parts = {follow_up: state.new_part()} | {part: state.new_part() | {"design": "j1"} for part in (pages, pin, replica)}
    data["orders"] = {"o1": state.new_order("page maps", "terminal", None, parts, moment) | {"jobs": ["j1", "j2", "j3"]}}
    data["jobs"] = {
        "j1": state.new_job("o1", "design", "design it", [follow_up, pages, pin, replica], moment) | {"state": "done"},
        "j2": state.new_job("o1", "builder", "build page maps", [pages, pin], moment) | {"state": "failed"},
        "j3": state.new_job("o1", "builder", "build the replica", [replica], moment) | {"state": "running"},
    }
    assert state.design_builder(data, "j1") == "j3"
    assert [[job_id for job_id, _ in state.carriers(data, "o1", part)] for part in (follow_up, pages, pin, replica)] == [["j1"], ["j2"], ["j2"], ["j3"]]
