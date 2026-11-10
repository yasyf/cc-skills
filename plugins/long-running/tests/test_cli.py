import json
import os
import subprocess
import sys
import types
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import drive
import pytest
from drive import cli, github, orca, slack, state

Run = Callable[..., tuple[int, str]]
PR = "https://github.com/Forge-AI/monorepo/pull/42"
SHORT_ASK = """ASK How should a finished run's files be laid out so its page opens without guessing?
A finished run is about 180 small files, each with its page map at the end, so the browser guesses where each map starts.
Recommended: one snapshot file per finished run, so any finished run opens in 3 requests.
Meanwhile nothing is built on either layout.
- One snapshot file per finished run, written when it ends, old runs backfilled: opens in 3 requests (recommended)
- A map file beside each journal file: no guessing, but one request per file and twice the objects
- Map at the start of each file: changes the shared file format and still needs a length read first

Findings:
(1) What a journal file is: a run's journal is a SQLite database, copied to the team's bucket as LTX files.
(2) Each LTX file ends with a page map; its length sits in the last 24 bytes.
"""
FINDINGS_FIRST = """ASK Two design picks for the new run page: how a run's files are laid out, and what the runner keeps.

Findings:

(1) The steps log. It is a table inside every run's journal.

The two questions:

Q1. How should a run's files be laid out so the browser never guesses at a page map?
- Snapshot each finished run when it lands (recommended)
- A map file beside every journal file
"""


def test_start_creates_the_drive_and_prints_the_roles_context(run: Run, tmp_path: Path) -> None:
    code, out = run("start", "demo", "--role", "communicator", "--channel", "D123")
    assert code == 0
    assert out.startswith("You are the communicator of drive demo: you talk to him. The coordinator: not started.")
    data = state.load("demo")
    assert data["roles"] == {
        "communicator": {
            "session": os.environ["CLAUDE_CODE_SESSION_ID"],
            "terminal": None,
            "cwd": str(tmp_path),
            "started": data["roles"]["communicator"]["started"],
        }
    }
    assert data["slack"] == {"channel": "D123", "page": None, "watch": []}


def test_start_needs_a_role(run: Run) -> None:
    with pytest.raises(SystemExit):
        run("start", "demo")


def test_each_role_binds_its_own_session_and_a_restart_rebinds_one(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "keep me")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "communicator-session")
    assert run("start", "demo", "--role", "communicator")[0] == 0
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "after-restart")
    code, _ = run("start", "demo", "--role", "coordinator")
    data = state.load("demo")
    assert code == 0
    assert data["roles"]["communicator"]["session"] == "communicator-session"
    assert data["roles"]["coordinator"]["session"] == "after-restart"
    assert data["slack"]["channel"] == "D123"
    assert data["orders"]["o1"]["text"] == "keep me"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "communicator-session")
    assert run("order", "add", "from the communicator") == (0, "o2")


def test_a_drive_saved_with_one_driver_reads_as_its_coordinator(run: Run, demo: str) -> None:
    with state.locked("demo"):
        saved = json.loads(state.state_path("demo").read_text())
        saved["driver"] = saved.pop("roles")["coordinator"]
        state.state_path("demo").write_text(json.dumps(saved))
    data = state.load("demo")
    assert "driver" not in data
    assert data["roles"]["coordinator"]["session"] == os.environ["CLAUDE_CODE_SESSION_ID"]
    assert run("start", "demo", "--role", "communicator")[0] == 0
    saved = json.loads(state.state_path("demo").read_text())
    assert "driver" not in saved
    assert set(saved["roles"]) == {"communicator", "coordinator"}


def test_start_stores_watched_channels_and_a_later_watch_adds(run: Run) -> None:
    code, out = run(
        "start", "demo", "--role", "coordinator", "--channel", "D123", "--watch", "C0B75AL4XEW", "--watch", "C0B75AL4XEW"
    )
    assert code == 0
    assert "Hold with slack_watch: C0B75AL4XEW" in out
    assert state.load("demo")["slack"]["watch"] == ["C0B75AL4XEW"]
    run("start", "demo", "--role", "coordinator")
    assert state.load("demo")["slack"]["watch"] == ["C0B75AL4XEW"]
    run("start", "demo", "--role", "coordinator", "--watch", "C0822AHFY3G", "--watch", "C0B75AL4XEW")
    data = state.load("demo")
    assert data["slack"]["channel"] == "D123"
    assert data["slack"]["watch"] == ["C0B75AL4XEW", "C0822AHFY3G"]


def test_start_adds_watch_to_a_drive_saved_before_watch_existed(run: Run, demo: str) -> None:
    with state.edit("demo") as data:
        del data["slack"]["watch"]
    assert run("start", "demo", "--role", "coordinator", "--watch", "C0822AHFY3G")[0] == 0
    assert state.load("demo")["slack"]["watch"] == ["C0822AHFY3G"]


def test_status_and_context_print_who_holds_each_channel(run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "fetch", lambda keys: {})
    run("start", "demo", "--role", "coordinator", "--channel", "D123", "--watch", "C0B75AL4XEW", "--watch", "C0822AHFY3G")
    session = os.environ["CLAUDE_CODE_SESSION_ID"]
    code, out = run("status")
    assert code == 0
    assert out.splitlines()[1:3] == [
        "communicator: not started; Hold with slack_watch: his DM D123",
        f"coordinator: session {session}, in no Orca terminal; Hold with slack_watch: C0B75AL4XEW, C0822AHFY3G",
    ]
    code, out = run("status", "--json")
    assert json.loads(out)["slack"] == {"channel": "D123", "watch": ["C0B75AL4XEW", "C0822AHFY3G"]}
    code, out = run("context")
    assert "Hold with slack_watch: C0B75AL4XEW, C0822AHFY3G" in out.splitlines()
    code, out = run("context", "--role", "communicator")
    assert "Hold with slack_watch: his DM D123" in out.splitlines()


def test_an_orphaned_terminal_gets_the_repository_setting_and_a_shown_one_the_switch_command(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(github, "fetch", lambda keys: {})
    session = os.environ["CLAUDE_CODE_SESSION_ID"]
    shown = {"orphaned": True, "holds": True}
    settings: list[tuple[str, ...]] = []

    def orca_calls(*args: str) -> dict:
        if args[:2] == ("repo", "set"):
            settings.append(args)
            return {}
        return {"terminal": {"ptyId": "pty-1", "orphaned": shown["orphaned"], "worktreePath": "/repos/acme/.worktrees/demo"}}

    monkeypatch.setattr(orca, "request", orca_calls)
    monkeypatch.setattr(orca, "main_checkout", lambda worktree: Path("/repos/acme"))
    monkeypatch.setattr(orca, "holds_session", lambda terminal, held: shown["holds"] and held == session)
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "term_abc")
    setting = "orca repo set --repo path:/repos/acme --external-worktree-visibility show"
    hidden = "not visible in Orca, which hides the worktrees it did not make for this repository"

    started = run("start", "demo", "--role", "coordinator")[1].splitlines()[-1]
    assert state.load("demo")["roles"]["coordinator"]["terminal"] == "term_abc"
    assert started.startswith(f"coordinator: session {session}, {hidden}, so drive start ran {setting} ")
    assert settings == [("repo", "set", "--repo", "path:/repos/acme", "--external-worktree-visibility", "show")]
    assert run("status")[1].splitlines()[2] == (
        f"coordinator: session {session}, {hidden}; show them, with no restart of the session, with: {setting}; "
        "Hold no Slack channel."
    )
    assert len(settings) == 1

    shown["orphaned"] = False
    switch = f"coordinator: session {session}, open it with: orca terminal switch --terminal term_abc"
    assert run("start", "demo", "--role", "coordinator")[1].splitlines()[-1] == switch
    assert run("status")[1].splitlines()[2] == f"{switch}; Hold no Slack channel."
    shown["holds"] = False
    assert run("status")[1].splitlines()[2] == (
        f"coordinator: session {session}, not visible in Orca until restarted into a terminal; Hold no Slack channel."
    )
    assert len(settings) == 1

    monkeypatch.setenv("TMUX", "/private/tmp/tmux-501/default,1,0")
    run("start", "demo", "--role", "coordinator")
    assert state.load("demo")["roles"]["coordinator"]["terminal"] is None
    assert run("status")[1].splitlines()[2] == f"coordinator: session {session}, in no Orca terminal; Hold no Slack channel."


def test_a_terminal_holds_a_session_only_when_its_process_tree_does_with_or_without_a_pane_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = tmp_path / "config" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "7807.json").write_text(json.dumps({"pid": 7807, "sessionId": "s-comm"}))
    (sessions / "9102.json").write_text(json.dumps({"pid": 9102, "sessionId": "s-coord"}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(orca, "parents", lambda: {7807: 7333, 7333: 7330, 7330: 1, 9102: 99962, 99962: 1})
    pane = {"sessionId": "pty-1", "pid": 7330, "paneKey": "tab:leaf"}
    monkeypatch.setattr(orca, "request", lambda *args: {"worktrees": [{"sessions": [pane]}]})
    assert orca.holds_session({"ptyId": "pty-1"}, "s-comm")
    assert not orca.holds_session({"ptyId": "pty-1"}, "s-coord")
    assert not orca.holds_session({"ptyId": "pty-2"}, "s-comm")
    pane["paneKey"] = None
    assert orca.holds_session({"ptyId": "pty-1"}, "s-comm")


def test_context_refuses_a_session_with_no_role(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "a-stranger")
    monkeypatch.setenv("LONG_RUNNING_DRIVE", "demo")
    assert run("context")[0] == 2


def test_start_holds_the_lock_across_its_existence_check(run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    unpatched = state.locked

    def rival_starts_while_this_one_waits(name: str):
        monkeypatch.setattr(state, "locked", unpatched)
        rival = state.new_drive(name, "communicator", "rival-session", "/rival", "D999")
        rival["orders"]["o1"] = {"text": "the rival's order", "state": "open", "due": None, "jobs": []}
        state.create(rival)
        return unpatched(name)

    monkeypatch.setattr(state, "locked", rival_starts_while_this_one_waits)
    assert run("start", "demo", "--role", "coordinator")[0] == 0
    data = state.load("demo")
    assert data["orders"]["o1"]["text"] == "the rival's order"
    assert data["roles"]["communicator"]["session"] == "rival-session"
    assert data["roles"]["coordinator"]["session"] == os.environ["CLAUDE_CODE_SESSION_ID"]
    assert data["slack"]["channel"] == "D999"


def test_start_rejects_a_name_that_is_not_a_directory_name(run: Run) -> None:
    code, _ = run("start", "../escape", "--role", "coordinator")
    assert code == 2


def test_order_add_allocates_ids_and_stores_words_verbatim(run: Run, demo: str) -> None:
    assert run("order", "add", "first  words", "--from", "slack:D123/1.2", "--thread", "1.2") == (0, "o1")
    assert run("order", "add", "second", "--due", "+30m")[0] == 2
    assert run("order", "add", "second", "--due", "+30m", "--thread", "1.3") == (0, "o2")
    orders = state.load("demo")["orders"]
    assert orders["o1"]["text"] == "first  words"
    assert orders["o1"]["from"] == "slack:D123/1.2"
    assert orders["o1"]["thread"] == "1.2"
    assert orders["o1"]["state"] == "open"
    assert orders["o1"]["due"] is None
    assert orders["o2"]["from"] == "terminal"
    assert state.parse_time(orders["o2"]["due"]) > state.now()


def test_order_add_json(run: Run, demo: str) -> None:
    code, out = run("order", "add", "words", "--json")
    assert code == 0
    assert json.loads(out)["id"] == "o1"


def test_order_set_updates_given_fields_and_stamps_lined(run: Run, demo: str) -> None:
    run("order", "add", "words")
    code, out = run("order", "set", "o1", "--state", "working", "--note", "on it", "--lined")
    order = state.load("demo")["orders"]["o1"]
    assert code == 0
    assert out == "o1 working: words"
    assert order["note"] == "on it"
    assert order["last_line"] == order["updated"]
    assert order["proof"] is None
    run("order", "set", "o1", "--proof", "https://example.com/proof")
    order = state.load("demo")["orders"]["o1"]
    assert order["state"] == "working"
    assert order["proof"] == "https://example.com/proof"


def test_order_set_unknown_order_fails(run: Run, demo: str) -> None:
    assert run("order", "set", "o9", "--state", "done")[0] == 2


def test_order_set_rejects_unknown_state(run: Run, demo: str) -> None:
    run("order", "add", "words")
    with pytest.raises(SystemExit):
        run("order", "set", "o1", "--state", "finished")


def test_job_add_links_the_order(run: Run, demo: str) -> None:
    run("order", "add", "words")
    code, out = run(
        "job", "add", "builder", "--order", "o1", "--goal", "do it", "--repo", "yasyf/cc-skills", "--branch", "b"
    )
    data = state.load("demo")
    assert (code, out) == (0, "j1")
    assert data["orders"]["o1"]["jobs"] == ["j1"]
    assert data["jobs"]["j1"]["kind"] == "builder"
    assert data["jobs"]["j1"]["state"] == "running"
    assert data["jobs"]["j1"]["repo"] == "yasyf/cc-skills"


def test_job_add_needs_a_known_order(run: Run, demo: str) -> None:
    assert run("job", "add", "reader", "--order", "o3", "--goal", "look")[0] == 2


def test_job_set_appends_prs_once(run: Run, demo: str) -> None:
    run("order", "add", "words")
    run("job", "add", "codex", "--order", "o1", "--goal", "review")
    run("job", "set", "j1", "--agent", "a-1", "--state", "waiting", "--said", "PR up", "--pr", PR)
    run("job", "set", "j1", "--pr", PR)
    job = state.load("demo")["jobs"]["j1"]
    assert job["agent"] == "a-1"
    assert job["state"] == "waiting"
    assert job["said"] == "PR up"
    assert job["prs"] == [PR]


def test_job_set_stores_each_pr_as_its_canonical_url(run: Run, demo: str) -> None:
    run("order", "add", "words")
    run("job", "add", "builder", "--order", "o1", "--goal", "build")
    run("job", "set", "j1", "--pr", PR + "/files", "--pr", PR)
    assert state.load("demo")["jobs"]["j1"]["prs"] == [PR]
    assert run("job", "set", "j1", "--pr", "https://example.com/not-a-pr")[0] == 2


def test_job_set_repro_keeps_a_copy_in_the_drive_and_status_shows_it(
    run: Run, demo: str, tmp_path: Path, state_root: Path
) -> None:
    run("order", "add", "reproduce the bug")
    run("job", "add", "builder", "--order", "o1", "--goal", "reproduce")
    run("order", "set", "o1", "--state", "done")
    steps = tmp_path / "steps.md"
    steps.write_text("1. run it\n")
    assert run("job", "set", "j1", "--repro", str(steps))[0] == 0
    steps.unlink()
    kept = state_root / "demo" / "repro" / "j1.md"
    assert state.load("demo")["jobs"]["j1"]["repro"] == str(kept)
    assert kept.read_text() == "1. run it\n"
    assert f"- j1 (o1): {kept}" in run("status")[1]
    assert run("job", "set", "j1", "--repro", str(steps))[0] == 2


@pytest.fixture
def asking(run: Run, demo: str) -> str:
    run("order", "add", "design the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    return "j1"


def test_job_ask_keeps_an_ask_in_the_shape_he_reads_and_sets_the_job_waiting(
    run: Run, asking: str, tmp_path: Path, state_root: Path
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    code, out = run("job", "ask", asking, str(block))
    kept = state_root / "demo" / "asks" / "j1.md"
    assert (code, out) == (0, str(kept))
    assert kept.read_text() == SHORT_ASK
    job = state.load("demo")["jobs"]["j1"]
    assert job["question"] == str(kept)
    assert job["state"] == "waiting"
    assert job["said"] == "How should a finished run's files be laid out so its page opens without guessing?"
    shown = json.loads(run("job", "ask", asking, str(block), "--json")[1])
    assert shown["recommended"].startswith("One snapshot file per finished run")
    assert len(shown["options"]) == 3
    assert len(shown["summary"]) == 3


def test_job_ask_refuses_findings_before_the_question(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(FINDINGS_FIRST)
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    refusal = capsys.readouterr().err
    assert "the ASK is out of shape" in refusal
    assert "the summary is 0 lines" in refusal
    assert "0 options; an ASK has 2 to 4" in refusal
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"


def test_job_ask_names_every_way_an_ask_is_out_of_shape(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(
        "ASK Which layout?\n"
        "The guess lives in /Users/me/repos/sand/reader.go and progress_nodes holds the rest; #53288 and #53300 both touch it.\n"
        f"- {' '.join(['word'] * 26)} (recommended)\n"
        "- Keep it (recommended)\n"
        "- Same as 1 but slower\n"
    )
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    refusal = capsys.readouterr().err
    assert '"Same as 1" points at an option by number or position' in refusal
    assert 'option "' in refusal and "is 26 words; each is one line of at most 25" in refusal
    assert "2 options end with (recommended); exactly one does" in refusal
    assert 'plain words only: "/Users/me/repos/sand/reader.go" is a file path' in refusal
    assert 'plain words only: "progress_nodes" is a code name' in refusal
    assert "it names 2 pull requests; name only the one being decided" in refusal
    assert "no `Findings:` line with the evidence under it" in refusal


def test_job_ask_refuses_a_question_over_150_words(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    line = " ".join(["word"] * 50)
    block.write_text(f"ASK Which?\n{line}\n{line}\n{line}\n- Yes (recommended)\n- No\nFindings:\nmeasured\n")
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert "the question, summary and options are 153 words; at most 150 in all" in capsys.readouterr().err


def test_job_ask_reads_a_remote_lanes_report_when_given_no_file(run: Run, asking: str, state_root: Path) -> None:
    assert run("job", "ask", asking)[0] == 2
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(where="remote", report=SHORT_ASK)
    assert run("job", "ask", asking) == (0, str(state_root / "demo" / "asks" / "j1.md"))


@pytest.fixture
def with_pr(run: Run, demo: str) -> str:
    run("order", "add", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    run("pr", "add", PR + "/files", "--job", "j1", "--tap")
    return "Forge-AI/monorepo#42"


def test_pr_add_registers_under_the_key(run: Run, with_pr: str) -> None:
    data = state.load("demo")
    pr = data["prs"][with_pr]
    assert pr["url"] == PR
    assert pr["job"] == "j1"
    assert pr["order"] == "o1"
    assert pr["wants_tap"] is True
    assert pr["tap"] is None
    assert pr["state"] == "open"
    assert data["jobs"]["j1"]["prs"] == [PR]


def test_pr_add_again_keeps_stored_state(run: Run, with_pr: str) -> None:
    with state.edit("demo") as data:
        data["prs"][with_pr]["checks"] = "pass"
    run("pr", "add", PR, "--job", "j1")
    pr = state.load("demo")["prs"][with_pr]
    assert pr["checks"] == "pass"
    assert pr["wants_tap"] is True


def test_pr_tapped_without_a_tap_exits_one(run: Run, with_pr: str) -> None:
    assert run("pr", "tapped", PR) == (1, f"{with_pr}: no tap from him yet")


def test_pr_tapped_with_approve_exits_zero(run: Run, with_pr: str) -> None:
    with state.edit("demo") as data:
        data["prs"][with_pr]["tap"] = {"label": "Approve", "at": state.stamp(), "head": "abc", "source": "slack"}
    code, out = run("pr", "tapped", PR)
    assert code == 0
    assert out.startswith(f"{with_pr}: approved by him at ")


def test_pr_tapped_with_hold_exits_one(run: Run, with_pr: str) -> None:
    with state.edit("demo") as data:
        data["prs"][with_pr]["tap"] = {"label": "Hold", "at": state.stamp(), "head": "abc", "source": "slack"}
    code, out = run("pr", "tapped", PR)
    assert code == 1
    assert "held by him" in out


def test_pr_set_fundamental_clears_the_tap(run: Run, with_pr: str) -> None:
    with state.edit("demo") as data:
        data["prs"][with_pr]["tap"] = {"label": "Approve", "at": state.stamp(), "head": "abc", "source": "slack"}
    run("pr", "set", PR, "--fundamental", "now deletes the old table")
    assert state.load("demo")["prs"][with_pr]["tap"] is None
    assert run("pr", "tapped", PR) == (1, f"{with_pr}: no tap since it changed: now deletes the old table")


def test_pr_set_fundamental_clears_an_exemption(run: Run, with_pr: str) -> None:
    run("pr", "set", PR, "--exempt", "test-only")
    run("pr", "set", PR, "--fundamental", "now migrates the users table")
    assert state.load("demo")["prs"][with_pr]["exempt"] is None
    assert run("pr", "tapped", PR) == (1, f"{with_pr}: no tap since it changed: now migrates the users table")


def test_pr_set_no_exempt_clears_the_exemption(run: Run, with_pr: str) -> None:
    run("pr", "set", PR, "--exempt", "pure port")
    assert run("pr", "set", PR, "--no-exempt")[0] == 0
    assert state.load("demo")["prs"][with_pr]["exempt"] is None
    assert run("pr", "tapped", PR)[0] == 1
    with pytest.raises(SystemExit):
        run("pr", "set", PR, "--exempt", "pure port", "--no-exempt")


def test_pr_set_exempt_lets_it_land(run: Run, with_pr: str) -> None:
    run("pr", "set", PR, "--exempt", "test-only")
    assert run("pr", "tapped", PR) == (0, f"{with_pr}: exempt: test-only")


def test_pr_set_toggles_wants_tap(run: Run, with_pr: str) -> None:
    run("pr", "set", PR, "--no-tap", "--why", "not his order")
    assert state.load("demo")["prs"][with_pr]["wants_tap"] is False
    run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")
    assert state.load("demo")["prs"][with_pr]["wants_tap"] is True


def test_pr_set_tap_names_the_order_it_serves_and_prints_his_words(run: Run, with_pr: str) -> None:
    run("pr", "set", PR, "--no-tap", "--why", "not his order")
    assert run("pr", "set", PR, "--tap", "--design", "none")[0] == 2
    assert state.load("demo")["prs"][with_pr]["wants_tap"] is False
    assert run("pr", "set", PR, "--tap", "--serves", "o9", "--design", "none")[0] == 2
    code, out = run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")
    assert code == 0
    assert out.splitlines()[-1] == "serves o1, his words: land it"
    assert state.load("demo")["prs"][with_pr]["serves"] == "o1"


def test_pr_set_tap_needs_the_design_answer_he_gave_or_none(run: Run, with_pr: str) -> None:
    run("pr", "set", PR, "--no-tap", "--why", "not his order")
    assert run("pr", "set", PR, "--tap", "--serves", "o1")[0] == 2
    assert state.load("demo")["prs"][with_pr]["wants_tap"] is False
    for unsaid in ("maybe", "asked:", "asked: "):
        with pytest.raises(SystemExit):
            run("pr", "set", PR, "--tap", "--serves", "o1", "--design", unsaid)
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "asked:045b08d")[0] == 0
    pr = state.load("demo")["prs"][with_pr]
    assert (pr["wants_tap"], pr["design"]) == (True, "asked:045b08d")
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")[0] == 0
    assert state.load("demo")["prs"][with_pr]["design"] == "none"


def test_pr_set_port_of_exempts_it_with_no_tap(run: Run, with_pr: str, withdrawals: "Withdrawals") -> None:
    code, out = run("pr", "set", PR, "--port-of", "51234")
    pr = state.load("demo")["prs"][with_pr]
    assert code == 0
    assert (pr["port_of"], pr["exempt"], pr["wants_tap"]) == (51234, "pure port", False)
    assert out.endswith("no tap needed, exempt: pure port of #51234")
    assert withdrawals.asked == [(PR, "port of #51234, which lands with no tap")]
    assert run("pr", "tapped", PR) == (0, f"{with_pr}: exempt: pure port")
    assert run("pr", "set", PR, "--port-of", "51234", "--tap", "--serves", "o1", "--design", "none")[0] == 2
    with pytest.raises(SystemExit):
        run("pr", "set", PR, "--port-of", "51234", "--exempt", "test-only")


def test_pr_set_no_tap_withdraws_his_open_card_showing_why(
    run: Run, with_pr: str, withdrawals: "Withdrawals", monkeypatch: pytest.MonkeyPatch
) -> None:
    card = "https://acme.slack.com/archives/D123/p1794286637698859"
    assert run("pr", "set", PR, "--no-tap")[0] == 2
    assert (state.load("demo")["prs"][with_pr]["wants_tap"], withdrawals.asked) == (True, [])
    withdrawals.cards = [card]
    code, out = run("pr", "set", PR, "--no-tap", "--why", "not this drive's order")
    assert (code, withdrawals.asked) == (0, [(PR, "not this drive's order")])
    assert out.splitlines()[-1] == f"withdrew his card {card}: not this drive's order"
    assert state.load("demo")["prs"][with_pr]["wants_tap"] is False

    def refuse(url: str, reason: str) -> list[str]:
        raise slack.SlackError("cc-slack exited 1")

    monkeypatch.setattr(slack, "withdraw_pr_card", refuse)
    assert run("pr", "set", PR, "--no-tap", "--why", "not this drive's order")[0] == 2


def test_pr_set_unknown_pr_fails(run: Run, demo: str) -> None:
    assert run("pr", "set", PR, "--no-tap", "--why", "not his order")[0] == 2


def test_pr_tapped_unknown_pr_may_land(run: Run, demo: str) -> None:
    assert run("pr", "tapped", PR) == (0, "Forge-AI/monorepo#42: not a drive PR")


def test_pr_tapped_json(run: Run, with_pr: str) -> None:
    code, out = run("pr", "tapped", PR, "--json")
    assert code == 1
    assert json.loads(out) == {"key": with_pr, "may_land": False, "why": "no tap from him yet"}


LIVE = {
    "state": "OPEN",
    "isDraft": False,
    "headRefOid": "fff0000",
    "mergeCommit": None,
    "checks": "SUCCESS",
    "reviewDecision": "APPROVED",
    "title": "Fix: the live title",
}


def test_status_overlays_live_pr_fields_without_saving_them(
    run: Run, with_pr: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(github, "fetch", lambda keys: calls.append(keys) or {with_pr: LIVE})
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#7"] = data["prs"][with_pr] | {"state": "merged"}
    stored = state.state_path("demo").read_text()
    code, out = run("status")
    assert code == 0
    assert out.splitlines()[0] == "demo: 1 open of 1 orders; PR fields live"
    assert "Forge-AI/monorepo#42 open, checks pass, review approved" in out
    code, out = run("status", "--json")
    payload = json.loads(out)
    assert payload["pr_fields"] == "live"
    assert (payload["prs"][with_pr]["head"], payload["prs"][with_pr]["title"]) == ("fff0000", "Fix: the live title")
    assert calls == [[with_pr], [with_pr]]
    assert state.state_path("demo").read_text() == stored


def test_status_names_the_prs_github_did_not_return(run: Run, with_pr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "fetch", lambda keys: {})
    code, out = run("status")
    assert code == 0
    first = "demo: 1 open of 1 orders; PR fields live; stored for Forge-AI/monorepo#42, which GitHub did not return"
    assert out.splitlines()[0] == first


def test_status_reads_only_the_orders_its_ids_name(run: Run, with_pr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "fetch", lambda keys: {})
    run("order", "add", "something else")
    for key in ("o1", "j1", with_pr, PR):
        code, out = run("status", key)
        assert code == 0
        assert out.splitlines()[0].startswith("demo: 1 open of 1 orders")
        assert "land it" in out
        assert "something else" not in out
    assert run("status", "o2")[1].splitlines()[0].startswith("demo: 1 open of 1 orders")
    assert run("status", "j9")[0] != 0


def test_status_renders_stored_fields_when_github_fails(
    run: Run, with_pr: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(keys: list[str]) -> dict:
        raise github.GithubError("HTTP 502")

    monkeypatch.setattr(github, "fetch", broken)
    code, out = run("status")
    assert code == 0
    assert out.splitlines()[0] == "demo: 1 open of 1 orders; PR fields stored (GitHub read failed: HTTP 502)"
    assert "Forge-AI/monorepo#42 open, checks pending, review none" in out


def test_context_prints_the_role_block_without_open_orders(run: Run, with_pr: str) -> None:
    code, out = run("context")
    assert code == 0
    assert out.startswith("You are the coordinator of drive demo: ")
    assert "land it" not in out


def test_view_prints_the_model_and_its_page(run: Run, with_pr: str) -> None:
    code, out = run("view", "--json")
    model = json.loads(out)
    assert code == 0
    assert (model["drive"], model["cards"]) == ("demo", "live")
    assert [group["features"][0]["title"] for group in model["status"] if group["features"]] == ["land it"]
    code, out = run("view")
    assert "## Waiting on you" in out
    assert "land it" in out


def test_drive_flag_picks_among_several(run: Run, demo: str) -> None:
    run("start", "other", "--role", "coordinator")
    run("order", "add", "for other", "--drive", "other")
    assert state.load("other")["orders"]["o1"]["text"] == "for other"
    assert state.load("demo")["orders"] == {}


def test_unresolvable_drive_exits_two(run: Run, state_root: Path) -> None:
    assert run("status")[0] == 2


def fake_module(monkeypatch: pytest.MonkeyPatch, name: str, main: Callable[..., int]) -> None:
    module = types.ModuleType(f"drive.{name}")
    module.main = main
    monkeypatch.setitem(sys.modules, f"drive.{name}", module)
    monkeypatch.setattr(drive, name, module, raising=False)


def test_watch_dispatches_with_the_drive_flag(run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    fake_module(monkeypatch, "watch", lambda name: calls.append(name) or 0)
    assert run("watch", "--drive", "demo")[0] == 0
    assert calls == ["demo"]


def test_card_dispatches_stdin_and_stdout(run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    fake_module(monkeypatch, "card", lambda stdin, stdout: calls.append((stdin, stdout)) or 3)
    assert run("card")[0] == 3
    assert calls == [(sys.stdin, sys.stdout)]


def test_install_dispatches_its_arguments(run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    fake_module(monkeypatch, "install", lambda *args: calls.append(args) or 0)
    assert run("install", "long-running@skills", "--sha", "abc", "--repo", "yasyf/cc-skills")[0] == 0
    assert run("install", "long-running@skills", "--timeout", "60")[0] == 0
    assert calls == [("long-running@skills", "abc", "yasyf/cc-skills", 900), ("long-running@skills", None, None, 60)]


def installed(config: Path, entries: dict[str, list[dict]]) -> None:
    plugins = config / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": entries}))


def fake_plugin(path: Path, marker: str) -> str:
    (path / "bin").mkdir(parents=True)
    target = path / "bin" / "drive"
    target.write_text(f'#!/bin/sh\necho {marker} "$@"\n')
    target.chmod(0o755)
    return str(path)


def test_setup_shim_execs_the_newest_installed_plugin(run: Run, tmp_path: Path) -> None:
    config = tmp_path / "claude"
    installed(
        config,
        {
            "long-running@skills": [
                {"installPath": fake_plugin(tmp_path / "old", "old"), "lastUpdated": "2026-11-01T00:00:00Z"}
            ],
            "long-running@other": [
                {"installPath": fake_plugin(tmp_path / "new", "new"), "lastUpdated": "2026-11-09T00:00:00Z"}
            ],
            "codex@skills": [
                {"installPath": fake_plugin(tmp_path / "codex", "codex"), "lastUpdated": "2026-12-01T00:00:00Z"}
            ],
        },
    )
    bin_dir = tmp_path / "bin"
    code, out = run("setup", "--bin-dir", str(bin_dir))
    shim = bin_dir / "drive"
    assert (code, out) == (0, f"wrote {shim}")
    assert os.access(shim, os.X_OK)
    result = subprocess.run(
        [str(shim), "card", "x"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(config)},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "new card x"


def test_setup_shim_fails_when_not_installed(run: Run, tmp_path: Path) -> None:
    config = tmp_path / "claude"
    installed(config, {})
    run("setup", "--bin-dir", str(tmp_path / "bin"))
    result = subprocess.run(
        [str(tmp_path / "bin" / "drive")],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(config)},
    )
    assert result.returncode == 1
    assert "not installed" in result.stderr


def test_setup_defaults_to_xdg_bin_home(run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_BIN_HOME", str(tmp_path / "xdg-bin"))
    run("setup")
    assert (tmp_path / "xdg-bin" / "drive").exists()


def test_desk_wait_forwards_to_lr_wait(tmp_path: Path) -> None:
    stub = tmp_path / "lr"
    stub.write_text('#!/bin/sh\necho lr "$@"\n')
    stub.chmod(0o755)
    plugin = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    for script in (plugin / "bin" / "desk-wait.sh", plugin / "skills" / "long-running" / "scripts" / "desk-wait.sh"):
        out = subprocess.run([str(script), "30", "mailbox:x=c"], env=env, capture_output=True, text=True, check=True)
        assert out.stdout == "lr wait 30 mailbox:x=c\n"


def test_an_order_lists_its_parts_and_each_job_names_the_ones_it_delivers(run: Run, demo: str) -> None:
    run("order", "add", "build the files design", "--parts", "stack 1", "stack 2", "stack 3")
    assert run("job", "add", "builder", "--order", "o1", "--goal", "all of it", "--agent", "a")[0] == 2
    assert run("job", "add", "builder", "--order", "o1", "--goal", "x", "--part", "stack 9")[0] == 2
    assert run("job", "add", "builder", "--order", "o1", "--goal", "stacks 2-3", "--part", "stack 2", "--part", "stack 3")[1] == "j1"
    run("job", "set", "j1", "--part", "stack 1", "--card", "https://acme.slack.com/archives/D1/p1")
    data = state.load("demo")
    assert list(data["orders"]["o1"]["parts"]) == ["stack 1", "stack 2", "stack 3"]
    assert data["jobs"]["j1"]["parts"] == ["stack 2", "stack 3", "stack 1"]
    assert data["jobs"]["j1"]["card"] == "https://acme.slack.com/archives/D1/p1"


def test_an_order_of_one_deliverable_is_one_part_its_jobs_take_by_default(run: Run, demo: str) -> None:
    run("order", "add", "fix   the\nwatch")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix", "--agent", "a")
    data = state.load("demo")
    assert list(data["orders"]["o1"]["parts"]) == ["fix the watch"]
    assert data["jobs"]["j1"]["parts"] == ["fix the watch"]
    assert data["jobs"]["j1"]["session"] == os.environ["CLAUDE_CODE_SESSION_ID"]


def test_setting_parts_keeps_what_a_kept_part_recorded(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: "Thread"
) -> None:
    run("order", "add", "two answers", "--parts", "why", "how")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    answer = slack_thread.post(state.now(), from_claude=True)
    assert run("order", "posted", "o1", answer)[0] == 2
    assert run("order", "posted", "o1", answer, "--part", "why")[0] == 0
    run("order", "set", "o1", "--parts", "why", "what")
    parts = state.load("demo")["orders"]["o1"]["parts"]
    assert parts == {"why": {"posted": answer, "orphan_said": None}, "what": state.new_part()}


def test_only_his_own_message_withdraws_a_promised_line(run: Run, demo: str, slack_thread: "Thread") -> None:
    run("order", "add", "send the numbers", "--thread", "1794200000.000100", "--due", "+30m")
    ours = slack_thread.post(state.now() - timedelta(minutes=60), from_claude=True)
    his = slack_thread.post(state.now() - timedelta(minutes=59), from_user=True)
    assert run("order", "set", "o1", "--state", "dropped")[0] == 2
    assert run("order", "set", "o1", "--state", "dropped", "--withdrawn", ours)[0] == 2
    assert run("order", "set", "o1", "--state", "dropped", "--withdrawn", his)[0] == 0
    order = state.load("demo")["orders"]["o1"]
    assert (order["state"], order["withdrawn"]) == ("dropped", his)


def test_only_the_communicator_marks_an_answer_posted(run: Run, demo: str) -> None:
    run("order", "add", "why?")
    assert run("order", "posted", "o1", "https://acme.slack.com/archives/D1/p2")[0] == 2
    assert state.load("demo")["orders"]["o1"]["parts"]["why?"]["posted"] is None


def test_version_names_the_plugin_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    plugin = json.loads((Path(drive.__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json").read_text())
    assert capsys.readouterr().out.strip() == f"drive {plugin['version']}"
