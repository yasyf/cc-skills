import fcntl
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from drive import reads, settled, stage, state
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
    assert state.load("demo")["jobs"]["j1"]["questions"][0]["file"] == str(kept)
    assert kept.read_text() == (
        "ASK Should row counts be exact?\nCounting reads every row.\nRecommended: exact.\nShow me:\nOne query per table:\n```sql\n"
        "select count(*) from alerts\n```\n![dash](/nowhere/dash.png)\n- Exact: one query per table (recommended)\n"
        "- Estimate: the planner's numbers\nFindings:\nRoot cause: none: rebuilt from the posted card\nHolds: none: rebuilt from the posted card\n"
    )
    kept.unlink()
    assert state.load("demo")["jobs"]["j1"]["questions"][0]["file"] is None
    assert not kept.exists()


def test_load_labels_his_old_taps_approving_a_pr_in_the_coordinators_checkout_once_and_never_for_a_drive_state_create_built(state_root: Path, backfilled: list[str]) -> None:
    make("demo")
    state.load("demo")
    assert backfilled == []
    (state.drive_dir("demo") / state.APPROVALS_LABELLED).unlink()
    state.load("demo")
    state.load("demo")
    assert backfilled == ["/work"]
    state.create(state.new_drive("talk", "communicator", None, "/talk"))
    state.load("talk")
    assert backfilled == ["/work"]


def test_a_copy_of_a_drive_runs_only_the_once_only_passes_a_load_of_the_drive_would(
    state_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backfilled: list[str]
) -> None:
    make("demo")
    state.clone("demo", tmp_path / "labelled")
    (state.drive_dir("demo") / state.APPROVALS_LABELLED).unlink()
    state.clone("demo", tmp_path / "unlabelled")
    monkeypatch.setenv("LONG_RUNNING_STATE", str(tmp_path / "labelled"))
    state.load("demo")
    assert backfilled == []
    monkeypatch.setenv("LONG_RUNNING_STATE", str(tmp_path / "unlabelled"))
    state.load("demo")
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
    checker = {"head": "abc", "pid": 1, "at": taken, "outcome": "NOT READY", "against": "", "objected": "abc"}
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
    assert kept == [(None, None), (read_typed, checker), (read_tap, checker)]
    with state.edit("demo"):
        pass
    state.load("demo")
    assert len(shown) == 2


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
        order_id: {"text": text, "jobs": [], "parts": {}, "promised": None, "project": project}
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



def test_only_the_coordinator_session_hearing_a_jobs_order_reaches_its_agent_by_sendmessage_so_a_moved_job_takes_a_resume(state_root: Path) -> None:
    make("demo", "top", "2026-11-11T01:00:00-08:00")
    with state.edit("demo") as data:
        data["subcoordinators"]["Iris"] = {"session": "iris", "terminal": None, "cwd": "/work", "started": "2026-11-11T02:00:00-08:00"}
        data["orders"]["o1"] = {"text": "ship the run page", "jobs": [], "parts": {}, "promised": None, "project": "Iris"}
        data["orders"]["o2"] = {"text": "fix the watch", "jobs": [], "parts": {}, "promised": None, "project": "The drive"}
        ran = {"moved": ("o1", "local", "top", "agent-1"), "iris": ("o1", "local", "iris", "agent-2"), "top": ("o2", "local", "top", "agent-3"), "sprite": ("o1", "remote", "iris", None), "ended": ("o2", "local", "gone", "agent-4")}
        for job_id, (order_id, where, session, agent) in ran.items():
            data["jobs"][job_id] = state.new_job(order_id, "builder", "build it", [], "2026-11-11T03:00:00-08:00") | {"where": where, "session": session, "agent": agent}
    data = state.load("demo")
    assert {job_id: state.agent_lane(data, job) for job_id, job in data["jobs"].items()} == {"moved": None, "iris": "iris", "top": "root", "sprite": None, "ended": None}


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
    assert kept[f"{MONOREPO}#4289"]["rework"] == {"by": "a1", "words": "bind it as @parent", "at": state.stamp(REFERENCE)}
    assert (kept[f"{MONOREPO}#4279"]["rework"], kept[f"{MONOREPO}#4284"]["rework"], any("verdict" in pr for pr in kept.values())) == (None, None, False)


def test_a_waiting_line_puts_each_card_it_names_on_each_pr_of_its_order_it_names() -> None:
    data = kept_prs()
    data["jobs"]["j9"] = state.new_job("o226", "builder", "fix", [], state.stamp(REFERENCE))
    card = "https://acme.slack.com/archives/D123/p1794300000000200"
    state.take_result(data, "j9", f"WAITING #4289 and #4279 waits on {card} and demo:j9-3")
    assert [data["prs"][f"{MONOREPO}#{n}"]["waits"] for n in (4289, 4279, 4284)] == [[card, "demo:j9-3"], [card, "demo:j9-3"], []]
    assert data["prs"]["yasyf/cc-skills#4289"]["waits"] == []


def test_a_waiting_report_naming_his_open_card_only_below_its_first_line_waits_on_no_card() -> None:
    data = kept_prs()
    asked = state.new_question(None, "Which option?", state.stamp(REFERENCE), card="https://acme.slack.com/archives/D123/p1794300000000200") | {"waits": "demo:j8-1"}
    data["jobs"]["j8"] = state.new_job("o226", "reader", "research", [], state.stamp(REFERENCE)) | {"questions": [asked]}
    data["jobs"]["j9"] = state.new_job("o226", "builder", "fix", [], state.stamp(REFERENCE)) | {"prs": [state.pr_url(f"{MONOREPO}#4289")]}
    data["prs"][f"{MONOREPO}#4289"] |= {"checks": "fail", "failed_checks": ["test"]}
    line = "WAITING #4279, the fix for the test #4289 inherits"
    state.take_result(data, "j9", line, f"{line}\nDESIGN: none: every option on demo:j8-1 includes this change\n")
    assert (data["jobs"]["j9"]["waited"]["waits"], data["prs"][f"{MONOREPO}#4289"]["waits"]) == ([f"{MONOREPO}#4279"], [f"{MONOREPO}#4279"])


def test_a_pr_reworked_to_his_words_owes_no_card_until_its_builders_ready_says_how_they_are_met_which_drops_his_approve_so_a_fresh_card_asks_it() -> None:
    data = kept_prs()
    data["jobs"]["j9"] = state.new_job("o226", "builder", "fix", [], state.stamp(REFERENCE))
    pr = data["prs"][f"{MONOREPO}#4289"]
    pr["tap"] = {"label": "Approve", "at": state.stamp(REFERENCE), "head": "abc", "source": "slack"}
    state.rework(pr, "o227", "Topology should be local metadata")
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
    news = {"j1": ["done", [], [], "DONE it"], "j2": ["waiting", WAITS, [], state.waits_line(WAITS)], "j3": ["waiting", WAITS, [], "WAITING on the api release"]}
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


def test_a_part_an_older_install_saved_with_a_kind_loads_without_it(state_root: Path) -> None:
    make("demo")
    saved = state.stored("demo")
    saved["orders"] = {"o1": {"text": "why, then fix it", "jobs": [], "parts": {"why": {"posted": None, "orphan_said": None, "kind": "answer"}, "fix": {"posted": None, "orphan_said": None, "kind": "build"}}, "promised": None}}
    state.store("demo", saved)
    assert state.load("demo")["orders"]["o1"]["parts"] == {"why": state.new_part(), "fix": state.new_part()}


def test_a_detached_verb_names_its_drive_its_log_and_no_session_in_its_environment(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert options["env"][state.DRIVE_ENV] == "demo" and options["env"][state.DETACHED_ENV] == "landings.log" and "CLAUDE_CODE_SESSION_ID" not in options["env"]


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
