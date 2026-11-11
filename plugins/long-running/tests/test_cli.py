import argparse
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import types
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import drive
import pytest
from conftest import RESEARCH, Answers, Drawing, Thread, WatchProcess, Withdrawals
from drive import card, cli, github, limits, orca, picture, render, slack, state, view

Run = Callable[..., tuple[int, str]]
UNLISTED = {"-h", "--help", "--drive", "--json"}
FLAG = re.compile(r"(?<![\w-])--[a-z][\w-]*")
PLACEHOLDER = re.compile(r"<[^<>`]*>|\"[^\"]*\"|…|\[|\]|\.\.\.")
PR = "https://github.com/Forge-AI/monorepo/pull/42"
DESIGN_RESEARCH = RESEARCH.replace("DONE researched the store copy", "DESIGN where the copy runs") + (
    "- on the primary, beside the queries it serves (recommended)\n- on a separate worker that reads the bucket\n"
)
CARD_BODY = """## Releasable alone
A release at its merge commit lands it on every target, in any order, and nothing runs after it.

## What you'll hate most
It lands without a second look.

## His words, step by step
- "land it" beside `land.py:1`
"""
SHORT_ASK = """ASK How should a finished run's files be laid out so its page opens without guessing?
A finished run is about 180 small files, each with its page map at the end, so the browser guesses where each map starts.
Recommended: one snapshot file per finished run, so any finished run opens in 3 requests.
Meanwhile nothing is built on either layout.
Show me:
1 · One snapshot file per finished run, the requests its page makes:
```http
GET snapshot.ltx      map at a fixed place
GET 2 page ranges
```

2 · A map file beside each journal file, the requests its page makes:
```http
GET journal/0001.map  the map, no guessing
GET journal/0001.ltx  one page range
... 180 files, two requests each
```
- One snapshot file per finished run, written when it ends, old runs backfilled: opens in 3 requests (recommended)
- A map file beside each journal file: no guessing, but one request per file and twice the objects
- Map at the start of each file: changes the shared file format and still needs a length read first

Findings:
Direct fix: none: a design choice, with no defect behind it
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


def test_a_coordinators_start_starts_the_watch_and_says_so(run: Run, watch_process: WatchProcess, monkeypatch: pytest.MonkeyPatch) -> None:
    run("start", "demo", "--role", "communicator", "--channel", "D123")
    assert watch_process.ensured == []
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "coordinator-session")
    code, out = run("start", "demo", "--role", "coordinator")
    assert (code, watch_process.ensured) == (0, ["demo"])
    assert "\nthe watch runs for demo\ncoordinator: " in out


def test_start_needs_a_role(run: Run) -> None:
    with pytest.raises(SystemExit):
        run("start", "demo")


def test_each_role_binds_its_own_session_and_a_restart_rebinds_one(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "keep me")
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
    assert run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "from the communicator") == (0, "o2")


def test_each_order_has_one_owner_and_the_other_session_adds_no_job_to_it(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "fix the watch")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a verb for owners")
    assert cli.main(["order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: another", "--owner", "coordinator"]) == 2
    assert "a \"Drive tooling:\" order is the communicator's" in capsys.readouterr().err
    assert [order["owner"] for order in state.load("demo")["orders"].values()] == ["coordinator", "communicator"]
    assert cli.main(["job", "add", "builder", "--order", "o2", "--goal", "build it"]) == 2
    assert "refused, no job added: o2 is the communicator's order, session not started: only the communicator adds its jobs" in capsys.readouterr().err
    assert run("job", "add", "builder", "--order", "o1", "--goal", "fix it")[1] == "j1"
    with state.edit("demo") as data:
        data["orders"]["o3"] = data["orders"]["o1"] | {"text": "an order from before owners", "owner": None, "jobs": []}
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "communicator-session")
    run("start", "demo", "--role", "communicator")
    assert cli.main(["job", "add", "reader", "--order", "o1", "--goal", "read it"]) == 2
    assert "o1 is the coordinator's order, session session-under-test" in capsys.readouterr().err
    assert run("job", "add", "reader", "--order", "o3", "--goal", "read it")[0] == 0
    assert run("job", "add", "builder", "--order", "o2", "--goal", "build it")[0] == 0
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-under-test")
    assert cli.main(["job", "add", "builder", "--order", "o3", "--goal", "build it"]) == 2
    assert "o3 is the communicator's order, session communicator-session" in capsys.readouterr().err


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
    assert 'Hold with cc-slack subscribe --kind alerts,releases,posts --channel <id>: C0B75AL4XEW' in out
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
        f'coordinator: session {session}, in no Orca terminal; Hold with cc-slack subscribe --kind alerts,releases,posts --channel <id>: C0B75AL4XEW, C0822AHFY3G',
    ]
    code, out = run("status", "--json")
    assert json.loads(out)["slack"] == {"channel": "D123", "watch": ["C0B75AL4XEW", "C0822AHFY3G"]}
    code, out = run("context")
    assert 'Hold with cc-slack subscribe --kind alerts,releases,posts --channel <id>: C0B75AL4XEW, C0822AHFY3G' in out.splitlines()
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
    assert run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "first  words", "--from", "slack:D123/1.2", "--thread", "1.2") == (0, "o1")
    assert run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "second", "--due", "+30m")[0] == 2
    assert run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "second", "--due", "+30m", "--thread", "1.3") == (0, "o2")
    orders = state.load("demo")["orders"]
    assert orders["o1"]["text"] == "first  words"
    assert orders["o1"]["from"] == "slack:D123/1.2"
    assert orders["o1"]["thread"] == "1.2"
    assert orders["o1"]["state"] == "open"
    assert orders["o1"]["due"] is None
    assert orders["o2"]["from"] == "terminal"
    assert state.parse_time(orders["o2"]["due"]) > state.now()


def test_an_orders_thread_is_refused_unless_slack_reads_it_as_a_thread_where_its_lines_go(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    threads = {
        "1794200000.000100": [{"ts": "1794200060.000300", "thread_ts": "1794200000.000100", "from_claude": True}],
        "1794200050.000200": [{"ts": "1794200050.000200", "thread_ts": "1794200000.000100", "from_user": True}],
    }
    reads: list[tuple[str, ...]] = []

    def thread(*where: str) -> list[dict]:
        reads.append(where)
        if where[3] not in threads:
            raise slack.SlackError("cc-slack can't read this conversation (thread_not_found)")
        return threads[where[3]]

    monkeypatch.setattr(slack, "thread", thread)
    words = ["order", "add", "--project", "The drive", "--workstream", "Drive tooling", "tell the stopped people", "--from", "slack:D123/1794200050.000200"]
    assert cli.main([*words, "--thread", "1794200090.000700"]) == 2
    assert "--thread 1794200090.000700: refused, it is no thread in D123, where the order's lines go: cc-slack can't read this conversation (thread_not_found)" in capsys.readouterr().err
    assert cli.main([*words, "--thread", "1794200050.000200"]) == 2
    assert "--thread 1794200050.000200: refused, it is a reply in thread 1794200000.000100; pass --thread 1794200000.000100" in capsys.readouterr().err
    assert state.load("demo")["orders"] == {}
    assert run(*words, "--thread", "1794200000.000100") == (0, "o1")
    assert cli.main(["order", "set", "o1", "--thread", "1794200090.000700"]) == 2
    assert "--thread 1794200090.000700: refused, it is no thread in D123" in capsys.readouterr().err
    assert state.load("demo")["orders"]["o1"]["thread"] == "1794200000.000100"
    assert reads[0] == ("--channel", "D123", "--thread", "1794200090.000700", "--limit", "1")


def test_order_set_due_on_an_order_with_no_thread_stores_the_drives_own_time_which_the_next_replaces_and_a_close_never_reads(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "wait on the release")
    code, out = run("order", "set", "o1", "--due", "+30m")
    first = state.load("demo")["orders"]["o1"]["due"]
    said = (
        f"stored {render.when(first)} as the drive's own time: o1 has no thread, so no line to him keeps it, and the watch"
        " says DUE once then; a time promised to him needs --thread <ts>"
    )
    assert (code, out.splitlines()[-1]) == (0, said)
    assert run("order", "set", "o1", "--due", "+60m")[0] == 0
    order = state.load("demo")["orders"]["o1"]
    assert (state.parse_time(order["due"]) > state.parse_time(first), state.owed_at(order), order["kept"]) == (True, order["due"], None)
    monkeypatch.setattr(slack, "order_posts", lambda data, order: pytest.fail("closing an order with no thread reads none"))
    assert run("order", "set", "o1", "--state", "dropped")[0] == 0


def test_job_set_waits_on_the_release_of_a_pr_the_drive_tracks(run: Run, demo: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    wait = "release:api:Forge-AI/monorepo#7"
    assert cli.main(["job", "set", "j1", "--waits", wait]) == 2
    assert f"--waits {wait}: the drive tracks no PR Forge-AI/monorepo#7; drive pr add it to its job first" in capsys.readouterr().err
    assert run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/7", "--job", "j1")[0] == 0
    assert run("job", "set", "j1", "--waits", wait)[0] == 0
    assert state.load("demo")["jobs"]["j1"]["waits"] == [wait]


def test_order_set_due_says_it_stored_the_time_and_what_it_waits_on_or_why_it_stored_none(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "send the numbers")
    past = "2026-01-01T09:00:00-08:00"
    assert cli.main(["order", "set", "o1", "--due", past, "--thread", "1.2"]) == 2
    assert f"o1: refused, no time stored: {render.when(past)} is already past" in capsys.readouterr().err
    assert state.load("demo")["orders"]["o1"]["due"] is None
    first = run("order", "set", "o1", "--due", "+30m", "--thread", "1.2")[1].splitlines()[-1]
    due = render.when(state.load("demo")["orders"]["o1"]["due"])
    assert first == (
        f"stored {due}: it waits on the next drive post in D123 thread 1.2, which tells him the time; from that post he is owed a line by {due}"
    )
    later = run("order", "set", "o1", "--due", "+2h")[1].splitlines()
    order = state.load("demo")["orders"]["o1"]
    assert render.when(order["due"]) in later[0]
    assert later[-1] == (
        f"stored {render.when(order['next_due'])} behind the line already owed for {due}: that time keeps firing DUE until the next"
        f" drive post in D123 thread 1.2 lands, which tells him {render.when(order['next_due'])} and makes it the time owed"
    )


def test_an_incident_order_records_a_job_on_each_part_and_names_every_open_incident_with_its_record(run: Run, demo: str) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL fork copies fail on plat", "--incident", "--parts", "fix", "first check")
    run("order", "set", "o1", "--note", "record 3fa2c1")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "the dashboard is slow")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "an old outage", "--incident", "--parts", "rollback", "fix", "cause")
    run("order", "set", "o3", "--state", "done")
    assert run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "sandsql-api times out on plat", "--incident", "--parts", "fix", "first check") == (
        0,
        (
            "o4\n"
            'j6 on "fix": start its agent now, its brief ending Drive: demo, order o4, job j6\n'
            'j7 on "first check": start its agent now, its brief ending Drive: demo, order o4, job j7\n'
            "o1 open incident: SandSQL fork copies fail on plat; record 3fa2c1"
        ),
    )
    data = state.load("demo")
    assert data["orders"]["o4"]["jobs"] == ["j6", "j7"]
    assert [(job["kind"], job["parts"], job["agent"], job["state"]) for job in (data["jobs"]["j6"], data["jobs"]["j7"])] == [
        ("codex", ["fix"], None, "running"),
        ("codex", ["first check"], None, "running"),
    ]
    assert run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "why is the dashboard slow?") == (0, "o5")


def test_an_incident_order_with_no_fix_part_is_refused_unless_it_joins_an_open_incident(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["order", "add", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL release 60 failed", "--incident", "--parts", "first check"]) == 2
    assert 'an incident order starts its fix job at the alert, beside diagnosis: name "fix" among --parts' in capsys.readouterr().err
    assert state.load("demo")["orders"] == {}
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL release 60 failed", "--incident", "--parts", "fix", "first check")
    run("order", "set", "o1", "--note", "record 3fa2c1")
    assert run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL mint endpoint failing", "--joins", "o1") == (
        0,
        "o2\njoins o1: record 3fa2c1; its owner job carries this order with --carries o2",
    )
    joined = state.load("demo")["orders"]["o2"]
    assert (joined["incident"], joined["note"], joined["jobs"]) == (True, "record 3fa2c1", [])
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "the dashboard is slow")
    assert cli.main(["order", "add", "--project", "The drive", "--workstream", "Drive tooling", "the dashboard is slower", "--joins", "o3"]) == 2
    assert "--joins o3: name an open incident order; o3 is open, not an incident" in capsys.readouterr().err


def test_an_order_becomes_an_incident_only_once_a_job_owns_its_fix(run: Run, demo: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "runs fail for one team", "--parts", "first check")
    assert cli.main(["order", "set", "o1", "--incident"]) == 2
    assert 'o1: refused, an incident order starts its fix job at the alert, beside diagnosis: give it a "fix" part' in capsys.readouterr().err
    assert state.load("demo")["orders"]["o1"]["incident"] is False
    run("order", "set", "o1", "--parts", "first check", "fix")
    run("job", "add", "codex", "--order", "o1", "--goal", "fix: runs fail", "--part", "fix", "--agent", "a-1")
    assert run("order", "set", "o1", "--incident")[0] == 0
    assert state.load("demo")["orders"]["o1"]["incident"] is True


RECORD = "sandsql on plat: release 60 cannot restore databases, since 9:16 PM"
FILED = ("--project", "Platform", "--workstream", "SandSQL")


def recorded(calls: list[list[str]]) -> Callable[[list[str], Path], str]:
    def read(argv: list[str], checkout: Path) -> str:
        calls.append(argv)
        return json.dumps({"id": "5f6b2b5c8a8d"} if argv[0] == "ccn" else {"freeze": {"id": "fvr7477syf"}, "by": {}})

    return read


def test_incident_open_files_the_order_a_job_on_each_part_and_its_evidence_record_in_one_call(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.reads, "run", recorded(calls))
    words = "SandSQL release 60 failed on plat"
    assert run("incident", "open", words, "--release", "--record", RECORD, *FILED) == (
        0,
        (
            "o1 record 5f6b2b5\n"
            'j1 on "fix": start its agent now, its brief ending Drive: demo, order o1, job j1\n'
            'j2 on "first check": start its agent now, its brief ending Drive: demo, order o1, job j2'
        ),
    )
    assert calls == [["ccn", "investigation", "open", RECORD, "--body", f"{words}\n\nFrom terminal", "--label", "drive:demo", "--label", "incident", "--json"]]
    run("incident", "open", "runs fail for one team", "--record", RECORD, *FILED)
    run("incident", "open", "table moves rerun on every write", "--no-rollback", "--record", RECORD, *FILED)
    orders = state.load("demo")["orders"]
    assert [(order["incident"], order["kind"], list(order["parts"]), order["note"]) for order in orders.values()] == [
        (True, "build", ["fix", "first check"], "record 5f6b2b5"),
        (True, "build", ["fix", "rollback", "cause"], "record 5f6b2b5"),
        (True, "build", ["fix", "stop", "cause"], "record 5f6b2b5"),
    ]


def test_incident_open_names_the_jobs_to_start_when_its_record_does_not_open(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refused(argv: list[str], checkout: Path) -> str:
        raise cli.reads.ReadError("ccn investigation open exited 1: no repository")

    monkeypatch.setattr(cli.reads, "run", refused)
    assert cli.main(["incident", "open", "SandSQL release 60 failed on plat", "--release", "--record", RECORD, *FILED]) == 2
    err = capsys.readouterr().err
    assert "o1 and its jobs are added, but its evidence record did not open: ccn investigation open exited 1: no repository" in err
    assert 'j1 on "fix": start its agent now, its brief ending Drive: demo, order o1, job j1' in err
    assert state.load("demo")["orders"]["o1"]["note"] is None


def test_incident_stop_gives_an_open_incident_one_running_stop_job(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.reads, "run", recorded([]))
    run("incident", "open", "table moves rerun on every write", "--record", RECORD, *FILED)
    assert run("incident", "stop", "o1") == (0, 'j4 on "stop": start its agent now, its brief ending Drive: demo, order o1, job j4')
    data = state.load("demo")
    assert (list(data["orders"]["o1"]["parts"]), data["jobs"]["j4"]["kind"]) == (["fix", "rollback", "cause", "stop"], "codex")
    assert cli.main(["incident", "stop", "o1"]) == 2
    assert "o1: refused, its stop runs as j4; resume that job instead" in capsys.readouterr().err
    run("order", "add", "the dashboard is slow", *FILED)
    assert cli.main(["incident", "stop", "o2"]) == 2
    assert "o2: refused, it is no open incident order" in capsys.readouterr().err


def test_incident_freeze_freezes_the_service_for_twelve_hours_and_notes_the_freeze(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.reads, "run", recorded(calls))
    run("incident", "open", "SandSQL release 60 failed on plat", "--release", "--record", RECORD, *FILED)
    code, said = run("incident", "freeze", "o1", "sandsql")
    freeze = calls[-1]
    assert (code, Path(freeze[0]).parts[-2:], freeze[1:6]) == (
        0,
        ("tools", "ci"),
        ["release", "freeze", "sandsql", "--reason", "o1, SandSQL release 60 failed on plat: held until its root fix releases"],
    )
    assert timedelta(hours=11, minutes=59) < state.parse_time(freeze[7]) - state.now() <= timedelta(hours=12)
    assert state.load("demo")["orders"]["o1"]["note"] == "record 5f6b2b5; freeze fvr7477syf"
    assert said.endswith("the job that merges the root fix lifts it with tools/ci release unfreeze fvr7477syf before the fix's build starts")


def handed(name: str, line: str) -> dict:
    out = io.StringIO()
    card.main(io.StringIO(json.dumps({"release": {"name": name, "line": line, "link": f"https://x.slack.com/archives/C1/{name}"}})), out)
    return json.loads(out.getvalue())


def test_a_named_release_failing_a_step_reopens_its_incidents_stop_once_with_a_job_or_the_running_stop_job(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.reads, "run", recorded([]))
    run("incident", "open", "SandSQL release 60 failed on plat", "--release", "--record", RECORD, *FILED)
    run("incident", "open", "table moves rerun on every write", "--no-rollback", "--record", RECORD, *FILED)
    assert cli.main(["incident", "release", "o1", "release 60"]) == 2
    assert "release 60: refused, name the release as its post in the releases channel does, such as stark-maclean" in capsys.readouterr().err
    assert run("incident", "release", "o1", "sanguine-neruda") == (0, "o1: sanguine-neruda named; the turn it fails a step, o1's stop reopens")
    run("incident", "release", "o2", "stark-maclean")
    assert handed("sanguine-neruda", "sanguine-neruda is running · sandsql") == {"reopened": []}
    assert handed("quiet-otter", "quiet-otter failed · sandsql") == {"reopened": []}
    assert handed("sanguine-neruda", "sanguine-neruda is running · sandsql · sandsql on plat failed") == {"reopened": ["o1"]}
    assert handed("sanguine-neruda", "sanguine-neruda failed · sandsql") == {"reopened": []}
    assert handed("stark-maclean", "stark-maclean partly released · sandsql") == {"reopened": ["o2"]}
    data = state.load("demo")
    assert (list(data["orders"]["o1"]["parts"]), data["orders"]["o1"]["jobs"], data["jobs"]["j6"]["parts"]) == (
        ["fix", "first check", "stop"],
        ["j1", "j2", "j6"],
        ["stop"],
    )
    assert data["orders"]["o2"]["jobs"] == ["j3", "j4", "j5"]
    stops = [(event["order"], event["release"], event.get("job"), event.get("resume")) for event, _ in state.read_events("demo") if event["kind"] == "stop"]
    assert stops == [("o1", "sanguine-neruda", "j6", None), ("o2", "stark-maclean", None, "j4")]


def test_no_stop_ends_the_last_running_job_on_an_open_incidents_fix(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.reads, "run", recorded([]))
    run("incident", "open", "SandSQL mint endpoint failing", "--release", "--record", RECORD, *FILED)
    assert cli.main(["job", "set", "j1", "--state", "stopped"]) == 2
    assert "j1 is the last running job on incident o1's fix, and nothing stops an open incident's fix" in capsys.readouterr().err
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    assert run("job", "set", "j2", "--state", "stopped")[0] == 0
    run("job", "add", "builder", "--order", "o1", "--part", "fix", "--goal", "release the default branch")
    assert run("job", "set", "j1", "--state", "stopped")[0] == 0


def test_an_incident_job_waits_on_his_card_only_once_the_incident_thread_says_so(
    run: Run, demo: str, slack_thread: Thread, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL mint endpoint failing", "--thread", "1794200000.000100", "--incident", "--parts", "fix", "first check")
    assert cli.main(["job", "set", "j1", "--card", DESIGN_CARD]) == 2
    refusal = capsys.readouterr().err
    assert "j1 works on incident o1: a card that blocks it is said in the incident's thread at once" in refusal
    assert "Link each card where the line names it, as <its permalink|your card on what it decides>, never as its bare permalink" in refusal
    moment = state.now()
    elsewhere = slack_thread.post(moment, from_claude=True, thread_ts="1794100000.000100", text=f"<{DESIGN_CARD}|the store card> blocks the fix")
    assert cli.main(["job", "set", "j1", "--card", DESIGN_CARD, "--told", elsewhere]) == 2
    assert "name the drive's own post in the incident's thread, 1794200000.000100" in capsys.readouterr().err
    unlinked = slack_thread.post(moment + timedelta(seconds=1), from_claude=True, text="the store card blocks the fix")
    assert cli.main(["job", "set", "j1", "--card", DESIGN_CARD, "--told", unlinked]) == 2
    refusal = capsys.readouterr().err
    assert f"does not link {DESIGN_CARD}" in refusal
    assert "as <its permalink|your card on what it decides>, never as its bare permalink" in refusal
    said = slack_thread.post(
        moment + timedelta(seconds=2),
        from_claude=True,
        text=f"<{DESIGN_CARD}|Your card on where the store lives> blocks the fix's release; it builds on the recommended shared store meanwhile.",
    )
    assert run("job", "set", "j1", "--card", DESIGN_CARD, "--told", said)[0] == 0
    assert [question["card"] for question in state.load("demo")["jobs"]["j1"]["questions"]] == [DESIGN_CARD]
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "the dashboard is slow")
    run("job", "add", "builder", "--order", "o2", "--goal", "speed it up")
    assert run("job", "set", "j3", "--card", DESIGN_CARD)[0] == 0


def test_order_add_json(run: Run, demo: str) -> None:
    code, out = run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "words", "--json")
    assert code == 0
    assert json.loads(out)["id"] == "o1"


def test_order_set_updates_given_fields_and_stamps_lined(run: Run, demo: str) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "words")
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


def test_an_order_is_filed_when_it_is_added_and_unfiled_lists_the_older_ones_still_missing_a_filing(run: Run, demo: str) -> None:
    with pytest.raises(SystemExit):
        cli.main(["order", "add", "fix the composer", "--project", "Iris"])
    assert state.load("demo")["orders"] == {}
    run("order", "add", "redo the dashboard", "--project", "The drive", "--workstream", "Dashboard")
    with state.edit("demo") as data:
        for order_id, words, project in (("o2", "move the tables", "Platform"), ("o3", "fix the composer", None), ("o4", "old work", None)):
            data["orders"][order_id] = data["orders"]["o1"] | {"text": words, "project": project, "workstream": None}
        data["orders"]["o4"]["state"] = "done"
    assert run("order", "unfiled") == (
        0,
        "o2 needs workstream: move the tables\no3 needs project and workstream: fix the composer\nin use: The drive / Dashboard",
    )
    run("order", "set", "o2", "--workstream", "Tables")
    run("order", "set", "o3", "--project", "Iris", "--workstream", "Chats")
    run("order", "set", "o1", "--workstream", "")
    orders = state.load("demo")["orders"]
    assert [(orders[key]["project"], orders[key]["workstream"]) for key in ("o1", "o2", "o3")] == [
        ("The drive", None),
        ("Platform", "Tables"),
        ("Iris", "Chats"),
    ]
    assert run("order", "unfiled")[1].splitlines()[0] == "o1 needs workstream: redo the dashboard"


def test_filing_an_order_keeps_the_time_it_last_moved_and_any_other_change_moves_it(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "ship the fix", "--project", "Platform", "--workstream", "SandSQL")
    run("order", "set", "o1", "--state", "done")
    went_live = state.load("demo")["orders"]["o1"]["updated"]
    monkeypatch.setattr(state, "now", lambda: state.parse_time(went_live) + timedelta(days=2))
    run("order", "set", "o1", "--project", "Iris", "--workstream", "Chats and agents")
    order = state.load("demo")["orders"]["o1"]
    assert (order["project"], order["workstream"], order["updated"]) == ("Iris", "Chats and agents", went_live)
    run("order", "set", "o1", "--project", "Platform", "--note", "moved back")
    assert state.load("demo")["orders"]["o1"]["updated"] != went_live


def test_a_drive_stored_with_milestones_loads_without_them(run: Run, demo: str) -> None:
    run("order", "add", "ship the fix", "--project", "Platform", "--workstream", "SandSQL")
    stored = json.loads(state.state_path("demo").read_text())
    stored["orders"]["o1"]["milestone"] = "Dashboard parity"
    state.state_path("demo").write_text(json.dumps(stored))
    assert "milestone" not in state.load("demo")["orders"]["o1"]


def test_order_set_unknown_order_fails(run: Run, demo: str) -> None:
    assert run("order", "set", "o9", "--state", "done")[0] == 2


def test_order_set_rejects_unknown_state(run: Run, demo: str) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "words")
    with pytest.raises(SystemExit):
        run("order", "set", "o1", "--state", "finished")


def test_job_add_links_the_order(run: Run, demo: str) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "words")
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


def test_job_add_takes_designer_its_agent_types_name_as_a_design_job(run: Run, demo: str) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "pick the layout")
    assert run("job", "add", "designer", "--order", "o1", "--goal", "cards: the layout") == (0, "j1")
    assert state.load("demo")["jobs"]["j1"]["kind"] == "design"


def test_job_set_appends_prs_once(run: Run, demo: str) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "words")
    run("job", "add", "codex", "--order", "o1", "--goal", "review")
    run("job", "set", "j1", "--agent", "a-1", "--said", "PR up", "--pr", PR)
    run("job", "set", "j1", "--pr", PR)
    job = state.load("demo")["jobs"]["j1"]
    assert job["agent"] == "a-1"
    assert job["said"] == "PR up"
    assert job["prs"] == [PR]


def test_job_set_stores_each_pr_as_its_canonical_url(run: Run, demo: str) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "words")
    run("job", "add", "builder", "--order", "o1", "--goal", "build")
    run("job", "set", "j1", "--pr", PR + "/files", "--pr", PR)
    assert state.load("demo")["jobs"]["j1"]["prs"] == [PR]
    assert run("job", "set", "j1", "--pr", "https://example.com/not-a-pr")[0] == 2


def test_job_set_waits_on_his_pr_card_by_its_slack_link_or_merge_waits_id_until_he_taps_it(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    link, merge = "https://acme.slack.com/archives/D123/p1794300000000400", "merge:acme:app:42"
    tap = {"asked": state.stamp(), "url": link, "prs": ["https://github.com/acme/app/pull/42"], "card": {"title": "New: land it?", "waits": merge}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [tap]})
    view.refresh_cards("demo")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    assert run("job", "set", "j1", "--waits", link, "--waits", merge)[0] == 0
    data = state.load("demo")
    assert data["jobs"]["j1"]["waits"] == [link, merge]
    assert view.holding(data, [link, merge], view.live_cards("demo")) == [link, merge]
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS)
    view.refresh_cards("demo")
    assert view.holding(data, [link, merge], view.live_cards("demo")) == []
    assert cli.main(["job", "set", "j1", "--waits", merge]) == 2
    assert (
        f"--waits {merge}: name a job of this drive, the Slack link or waits id of a card of his still open, a question"
        " card or a PR card still waiting on his tap, whose waits id is merge:<owner>:<repo>:<n>, a PR of this drive as"
    ) in capsys.readouterr().err
    assert cli.main(["job", "set", "j1", "--waits", "acme/app#9"]) == 2
    assert "--waits acme/app#9: the drive tracks no PR acme/app#9; drive pr add it to its job first" in capsys.readouterr().err


def test_job_set_repro_keeps_a_copy_in_the_drive_and_status_shows_it(
    run: Run, demo: str, tmp_path: Path, state_root: Path
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "reproduce the bug")
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
    assert run("job", "set", "j1", "--repro", str(kept))[0] == 0
    assert kept.read_text() == "1. run it\n"


@pytest.fixture
def asking(run: Run, demo: str) -> str:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "design the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    return "j1"


def test_job_ask_keeps_an_ask_in_the_shape_he_reads_and_sets_the_job_waiting(
    run: Run, asking: str, tmp_path: Path, state_root: Path
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    code, out = run("job", "ask", asking, str(block))
    kept = state_root / "demo" / "asks" / f"j1-{hashlib.sha256(SHORT_ASK.encode()).hexdigest()[:8]}.md"
    assert (code, out) == (0, str(kept))
    assert kept.read_text() == SHORT_ASK
    job = state.load("demo")["jobs"]["j1"]
    assert [question["file"] for question in job["questions"]] == [str(kept)]
    assert job["state"] == "waiting"
    assert job["said"] == "How should a finished run's files be laid out so its page opens without guessing?"
    shown = json.loads(run("job", "ask", asking, str(block), "--json")[1])
    assert shown["recommended"].startswith("One snapshot file per finished run")
    assert len(shown["options"]) == 3
    assert len(shown["summary"]) == 3
    assert shown["show_me"][0] == "1 · One snapshot file per finished run, the requests its page makes:"
    assert shown["show_me"][-1] == "```"


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


def test_job_ask_check_says_ready_or_every_fix_with_no_drive_and_keeps_nothing(
    state_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    assert cli.main(["job", "ask", "--check", str(block)]) == 0
    assert capsys.readouterr().out == "ready\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(FINDINGS_FIRST))
    assert cli.main(["job", "ask", "--check", "-"]) == 2
    refusal = capsys.readouterr().err
    assert "the summary is 0 lines" in refusal
    assert "0 options; an ASK has 2 to 4" in refusal
    assert not state_root.exists()
    assert cli.main(["job", "ask"]) == 2
    assert "drive job ask <j> <file>; drive job ask --check <file> only checks its shape" in capsys.readouterr().err


def test_job_ask_and_card_refuse_a_file_that_is_not_there_by_its_path(
    run: Run, asking: str, tmp_path: Path, state_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "ask.md"
    for argv in (["job", "ask", "--check", str(missing)], ["job", "ask", asking, str(missing)], ["job", "card", str(missing), "--waits", "demo:j1-7"]):
        assert cli.main(argv) == 2
        assert capsys.readouterr().err == f"drive: no ASK block file at {missing}\n"
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["questions"]) == ("running", [])
    assert not (state_root / "demo" / "asks").exists()


def test_job_ask_keeps_an_option_his_no_sweepers_ruling_refuses_only_when_unsettled_names_that_ruling(
    state_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(
        "ASK How do leases stop leaking?\nA lease outlives the run that took it.\n"
        "- reclaim orphaned leases every 10 minutes (recommended)\n- one owner holds the lease until it ends\n"
        "Findings:\nDirect fix: none: a design pick\nTwo runs held one lease.\n"
    )
    assert cli.main(["job", "ask", "--check", str(block)]) == 2
    assert 'cleans up a bad state with "reclaim orphaned"' in capsys.readouterr().err
    assert cli.main(["job", "ask", "--check", str(block), "--unsettled", "b89209a: the lease lives in a store no owner can hold"]) == 0
    assert capsys.readouterr().out == "ready\n"


def test_job_ask_names_every_way_an_ask_is_out_of_shape(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(
        "ASK Which layout?\n"
        "The guess in reader.go calls posix_fadvise and byte_copy() from /Users/me/repos/sand while j702 waits; #53288 and #53300 both touch it.\n"
        f"- {' '.join(['word'] * 26)} (recommended)\n"
        "- Keep it (recommended)\n"
        "- Same as 1 but slower\n"
    )
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    refusal = capsys.readouterr().err
    assert '"Same as 1" points at an option by number or position' in refusal
    assert 'option "' in refusal and "is 26 words; each is one line of at most 25" in refusal
    assert "2 options end with (recommended); exactly one does" in refusal
    assert '"j702" is a drive job or order id, which he cannot open' in refusal
    assert not any(term in refusal for term in ("reader.go", "posix_fadvise", "byte_copy", "/Users/me/repos/sand"))
    assert '"#53288" is a number he cannot open; link it by what it is' in refusal
    assert "Show me" not in refusal
    assert "no `Findings:` line with the evidence under it" in refusal


def test_job_ask_refuses_a_question_over_150_words(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    line = " ".join(["word"] * 50)
    block.write_text(f"ASK Which?\n{line}\n{line}\n{line}\n- Yes (recommended)\n- No\nFindings:\nmeasured\n")
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert "the question, summary and options are 153 words; at most 150 in all" in capsys.readouterr().err


def test_job_ask_refuses_a_card_cc_slack_would_not_draw_in_its_own_words(
    run: Run, asking: str, tmp_path: Path, state_root: Path, capsys: pytest.CaptureFixture[str], drawing: Drawing
) -> None:
    row = "no event and no versions      unknowable     the second option, so a next loss names its actor"
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK.replace("... 180 files, two requests each", row))
    drawing.refusal = (
        'card body points at an option by number or position with "second option": the card draws the recommended option'
        " first and renumbers the rest, so name the option in its own words"
    )
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert f"cc-slack would not draw it: {drawing.refusal}" in capsys.readouterr().err
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    assert not (state_root / "demo" / "asks").exists()
    [card] = drawing.cards
    assert card["waits"] == "demo:j1-0"
    assert card["recommended"] == card["options"][0]
    assert row in card["body"].split("\n*Show me*\n")[1].splitlines()


def test_job_ask_counts_the_show_me_outside_its_150_words(run: Run, asking: str, tmp_path: Path) -> None:
    block = tmp_path / "ask.md"
    code = "\n".join(f"GET journal/{index:04}.ltx  last 64 KiB, guessing where the map starts" for index in range(40))
    block.write_text(SHORT_ASK.replace("... 180 files, two requests each", code))
    assert run("job", "ask", asking, str(block))[0] == 0


TEAMMATE_FIX = """ASK A teammate's PR for the missing update button landed first: keep it, add our download status to it, or replace it?
A teammate's PR landed at 2:12 PM today: every locked chat shows an Update sand button that downloads and installs in one click, even mid-download. Ours shows the button once the download finishes and says downloading until then. They conflict. Ours waits unlanded meanwhile.
- Keep theirs, close ours: the button is always there and nothing needs a terminal, but the card never says it is downloading. (recommended)
- Add our status line beside their always-on button: the card says checking, downloading or downloaded; ours is rebuilt and reviewed again.
- Replace theirs with ours: the button appears once downloaded, terminal steps when a download fails; it undoes a teammate's landed PR.
Findings:
Direct fix: Keep theirs, close ours
- Theirs merged 2:12 PM Nov 10; ours now conflicts with dev.
"""
TEAMMATE_FIX_SHOWN = TEAMMATE_FIX.replace(
    "A teammate's PR landed at 2:12 PM today:",
    "<https://github.com/Forge-AI/monorepo/pull/53618|A teammate's PR for the update button> landed at 2:12 PM today:",
).replace(
    "Ours waits unlanded meanwhile.\n",
    "Ours waits unlanded meanwhile.\nShow me:\nWhat a locked chat shows mid-download, theirs first, then ours:\n"
    "| Version | The card mid-download |\n| --- | --- |\n| Theirs | Sand 0.142 is ready [ Update sand ] |\n"
    "| Ours | Downloading Sand 0.142…, no button yet |\n",
)


def test_job_ask_refuses_a_pr_named_with_no_link_and_keeps_the_linked_copy(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(TEAMMATE_FIX)
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    refusal = capsys.readouterr().err
    assert (
        '"teammate\'s PR" names something he could open with no link there, in "A teammate\'s PR landed at 2:12 PM today"; put those'
        " words in its link"
    ) in refusal
    assert '"teammate\'s landed PR" in the question or an option names something he could open, and those draw no links' in refusal
    block.write_text(TEAMMATE_FIX_SHOWN)
    assert run("job", "ask", asking, str(block))[0] == 0
    block.write_text(TEAMMATE_FIX_SHOWN.replace("theirs first, then ours", "with their PR, then ours"))
    assert run("job", "ask", asking, str(block))[0] == 0
    block.write_text(TEAMMATE_FIX_SHOWN.replace("A teammate's PR for the update button>", "A teammate's change>").replace("theirs first, then ours", "with their PR, then ours"))
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert '"their PR" names something he could open with no link there, in "with their PR"' in capsys.readouterr().err


def test_job_ask_offers_the_direct_fix_and_recommends_it_unless_the_summary_says_why_not(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    other = TEAMMATE_FIX_SHOWN.replace("Direct fix: Keep theirs, close ours", "Direct fix: Add our status line beside their always-on button")
    block.write_text(other)
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert 'the direct fix "Add our status line beside their always-on button" is an option but not the recommended one' in capsys.readouterr().err
    block.write_text(other.replace("Ours waits unlanded meanwhile.", "Ours waits; add our status line beside their always-on button only if the download state beats a review."))
    assert run("job", "ask", asking, str(block))[0] == 0


def test_job_ask_refuses_a_question_that_hides_the_terms_its_findings_name(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK.replace("ASK How should a finished run's files be laid out", "ASK How should the change be laid out"))
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert 'the question names its object only as "the change", while its summary and findings name SQLite, LTX' in capsys.readouterr().err
    block.write_text(SHORT_ASK.replace("ASK How should a finished run's files be laid out", "ASK How should a finished run's LTX files be laid out"))
    assert run("job", "ask", asking, str(block))[0] == 0


def test_job_ask_refuses_a_show_me_out_of_shape(run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    block = tmp_path / "ask.md"
    caption = " ".join(["word"] * 26)
    block.write_text(
        SHORT_ASK.replace("```http\nGET snapshot", f"{caption}\n```sql\nGET snapshot", 1).replace("two requests each\n```\n", "two requests each\n")
    )
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    refusal = capsys.readouterr().err
    assert f'Show me line "{caption}" is 26 words; outside a code fence each is a caption of at most 25' in refusal
    assert "a ``` fence in the Show me is never closed" in refusal
    head, shown = SHORT_ASK.split("Show me:\n")
    example, options = shown.split("- One snapshot", 1)
    block.write_text(f"{head}- One snapshot{options.split('Findings:')[0]}Show me:\n{example}Findings:\nmeasured\n")
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert "the options come after the Show me, never above it" in capsys.readouterr().err


PLANNED_TABLE = """| Database size | 0 change files | 100 change files |
| --- | --- | --- |
| 100 MiB | {cell} | time, bytes, disk |
| 1 GiB | time, bytes, disk | time, bytes, disk |
"""


@pytest.mark.parametrize(("cell", "refused"), [("time, bytes, disk", True), ("0.4 s, 2 MiB, 0 (measured)", False)])
def test_job_ask_refuses_a_show_me_table_whose_every_cell_says_the_same(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], cell: str, refused: bool
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK.replace("Show me:\n", "Show me:\n" + PLANNED_TABLE.format(cell=cell) + "\n", 1))
    assert cli.main(["job", "ask", asking, str(block)]) == (2 if refused else 0)
    placeholder = (
        'the Show me table headed "Database size | 0 change files | 100 change files" holds "time, bytes, disk" in every cell:'
        " a Show me shows real contents in every row and cell"
    )
    assert (placeholder in capsys.readouterr().err) == refused


def test_job_ask_refuses_an_option_that_lands_first_and_fixes_up_after(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    landing = "- Land the snapshot writer now, then backfill old runs from a pass box: opens in 3 requests"
    block.write_text(SHORT_ASK.replace("- A map file beside each journal file", f"{landing}\n- A map file beside each journal file"))
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert f'option "{landing[2:]}" lands first and leaves "then backfill" for after: dev must always be releasable' in capsys.readouterr().err
    block.write_text(SHORT_ASK.replace("- A map file beside each journal file", "- Land the snapshot writer with no backfill: old runs keep guessing"))
    assert run("job", "ask", asking, str(block))[0] == 0


LANDING_ASK = """ASK Land your run history change so the run page change you approved can land?
<https://github.com/acme/app/pull/12|The run page change> is built on <https://github.com/acme/app/pull/11|your run history change>, which is green and approved but has no tap of yours.
I recommend landing both together, run history first; meanwhile both stay open.
- Land both now, run history first (recommended)
- Move the run page change onto dev without run history, and leave that one with you

Findings:
Direct fix: Land both now, run history first
The run page change's base branch is the run history change's branch, so the merge queue cannot take it alone.
"""


def test_job_ask_sends_an_ask_to_land_prs_to_their_stack_card_and_still_redraws_one_it_kept(
    asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(LANDING_ASK)
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert "it asks him to land PRs, which is his tap on their stack card and never a question card: return READY" in capsys.readouterr().err
    assert limits.question(LANDING_ASK, kept=True).recommended == "Land both now, run history first"
    block.write_text(LANDING_ASK.replace("ASK Land your", "ASK Keep your"))
    assert cli.main(["job", "ask", asking, str(block)]) == 0


def test_job_card_draws_the_show_me_code_as_pictures_on_lines_of_their_own(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawn = []

    def visual_png(kind: str, source: str, out: Path) -> None:
        drawn.append(kind)
        out.write_bytes(b"\x89PNG\r\n")

    monkeypatch.setattr(picture, "visual_png", visual_png)
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    code, printed = run("job", "card", str(block), "--waits", "demo:j1-7")
    assert code == 0
    card = json.loads(printed)
    shown = card["body"].split("*Show me*\n", 1)[1].splitlines()
    pictures = [line.removeprefix("![").removesuffix(")").split("](") for line in shown if line.startswith("![")]
    assert [caption for caption, _ in pictures] == [
        "1 · One snapshot file per finished run, the requests its page makes",
        "2 · A map file beside each journal file, the requests its page makes",
    ]
    assert all(Path(path).is_file() for _, path in pictures)
    assert "```" not in card["body"]
    assert drawn == ["code:http", "code:http"]
    assert (card["waits"], card["recommended"]) == ("demo:j1-7", "One snapshot file per finished run, written when it ends, old runs backfilled: opens in 3 requests")
    assert run("job", "card", str(block), "--waits", "demo:j1-7") == (0, printed)
    assert drawn == ["code:http", "code:http"]


KEPT_TEXT_FENCE_ASK = """ASK Should the newest server warn the open window at launch when the api refuses it?
The fix blocks the window when a chat fails to open with the api's refusal. Recommend keeping it as built.
Show me:
What the newest sand sends the open window on its chat-list connection, and the code that sends it.
```text
before: {"sessions":[…],"removed":[…]}   or   {"live":[…]}
after:  the same, plus {"type":"update_required"} once per window, after the api first refuses this sand
```
```go
case <-refused:
\trefused = nil
\tevent = wireEvent{"type": platformapi.CodeUpdateRequired}
```
- Warn at launch; the block lasts until sand restarts or updates, even if the api later lowers its floor. (recommended)
- Warn at launch; lift the block once sand's next call to the api succeeds, so a lowered floor frees the window without a restart.
- No launch warning: block only when a chat fails to open; a smaller change, but the window looks usable until then.
Findings:
Direct fix: none: a design choice, with no defect behind it
"""


def test_job_card_redraws_a_kept_text_fence_as_a_code_block_where_it_sat(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    block = tmp_path / "ask.md"
    block.write_text(KEPT_TEXT_FENCE_ASK)
    code, printed = run("job", "card", str(block), "--waits", "demo:j1-7")
    assert code == 0
    shown = json.loads(printed)["body"].split("*Show me*\n", 1)[1].splitlines()
    assert shown[:5] == [
        "What the newest sand sends the open window on its chat-list connection, and the code that sends it.",
        "```text",
        'before: {"sessions":[…],"removed":[…]}   or   {"live":[…]}',
        'after:  the same, plus {"type":"update_required"} once per window, after the api first refuses this sand',
        "```",
    ]
    assert len(shown) == 6
    assert shown[5].startswith("![go code](")


def test_job_card_draws_a_card_no_job_asked_and_gives_no_job_its_waits(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    jobs = state.load("demo")["jobs"]
    code, printed = run("job", "card", str(block), "--waits", "demo:hier-2")
    assert (code, json.loads(printed)["waits"]) == (0, "demo:hier-2")
    assert state.load("demo")["jobs"] == jobs


def test_job_card_after_a_job_draws_its_newest_kept_ask_or_the_one_named_under_the_waits_id_its_card_has(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    first = run("job", "ask", asking, str(block))[1]
    assert cli.main(["job", "card", asking]) == 2
    assert f"j1's ASK in {first} has no card yet, so no waits id: pass --waits demo:j1-<its ask record's seq>" in capsys.readouterr().err
    code, printed = run("job", "card", first, "--waits", "demo:j1-7")
    assert code == 0
    assert run("job", "card", asking) == (0, printed)
    block.write_text(SHORT_ASK.replace("ASK How should", "ASK Where should"))
    newest = run("job", "ask", asking, str(block))[1]
    assert cli.main(["job", "card", asking]) == 2
    assert f"j1's ASK in {newest} has no card yet" in capsys.readouterr().err
    assert run("job", "card", asking, first) == (0, printed)


def test_job_card_reworking_says_atop_the_body_that_the_question_is_being_redone_and_adds_no_counted_words(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    plain = json.loads(run("job", "card", str(block), "--waits", "demo:j1-7")[1])
    why = "the layouts are being measured again; back within the hour"
    redone = json.loads(run("job", "card", str(block), "--waits", "demo:j1-7", "--reworking", why)[1])
    assert redone["body"] == f"*This question is being redone:* {why}\n{plain['body']}"
    assert (redone["title"], redone["options"], redone["recommended"]) == (plain["title"], plain["options"], plain["recommended"])
    assert limits.card_words(redone) == limits.card_words(plain)


def open_card(n: int, card: dict) -> dict:
    return {
        "question": card["title"],
        "options": card["options"],
        "recommended": card["recommended"],
        "asked": state.stamp(),
        "url": f"https://acme.slack.com/archives/D123/p179430000000010{n}",
        "card": card,
    }


def test_cards_redraw_prints_each_open_cards_card_in_one_pass_and_names_each_it_could_not_redraw(
    run: Run, asking: str, state_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    kept = []
    for text in (SHORT_ASK, SHORT_ASK.replace("ASK How should", "ASK Where should")):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        kept.append(run("job", "ask", asking, "-")[1])
    printed = [run("job", "card", path, "--waits", f"demo:j1-{n}")[1] for n, path in enumerate(kept)]
    unasked = json.loads(printed[0]) | {"waits": "demo:hier-1"}
    unanswerable = {"title": "Which way?", "body": "One line.", "options": [], "recommended": "", "waits": "demo:hier-2", "drive": "demo"}
    shown = [open_card(n, card) for n, card in enumerate([*map(json.loads, printed), unasked, unanswerable])]
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": shown, "pr_cards": []})
    jobs = state.load("demo")["jobs"]
    assert cli.main(["cards", "redraw"]) == 2
    out, err = capsys.readouterr()
    assert out.splitlines() == [*printed, json.dumps(unasked)]
    assert "demo:hier-2: the ASK is out of shape" in err and "0 options; an ASK has" in err
    assert (state_root / "demo" / "asks" / "hier-1.md").is_file()
    assert state.load("demo")["jobs"] == jobs
    assert run("cards", "redraw", "--waits", "demo:j1-1") == (0, printed[1])
    assert cli.main(["cards", "redraw", "--waits", "demo:j1-9"]) == 2
    assert "demo:j1-9: not one of his open cards" in capsys.readouterr().err


def test_cards_redraw_draws_a_card_from_the_ask_a_question_linked_to_it_keeps_when_it_asks_the_cards_question(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    path = run("job", "ask", asking, "-")[1]
    printed = run("job", "card", path, "--waits", "demo:j1-7")[1]
    shown = open_card(0, json.loads(printed))
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0] |= {"waits": None, "card": shown["url"]}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": []})
    assert run("cards", "redraw") == (0, printed)
    assert [(question["file"], question["waits"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [(path, "demo:j1-7")]


def test_cards_redraw_rebuilds_a_card_whose_linked_question_asks_something_else_and_keeps_it_in_that_questions_place(
    run: Run, asking: str, state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    kept = []
    for text in (SHORT_ASK, SHORT_ASK.replace("ASK How should", "ASK Where should")):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        kept.append(run("job", "ask", asking, "-")[1])
    printed = run("job", "card", kept[1], "--waits", "demo:j1-8")[1]
    shown = open_card(0, json.loads(printed))
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"] = [data["jobs"]["j1"]["questions"][0] | {"waits": None, "card": shown["url"]}]
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": []})
    assert run("cards", "redraw", "--waits", "demo:j1-8") == (0, printed)
    rebuilt = state_root / "demo" / "asks" / "j1-8.md"
    assert limits.redrawn(rebuilt.read_text()).title == json.loads(printed)["title"]
    assert [(question["file"], question["waits"], question["card"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [
        (str(rebuilt), "demo:j1-8", shown["url"])
    ]
    assert run("cards", "redraw", "--waits", "demo:j1-8") == (0, printed)


def test_job_card_from_a_relative_path_gives_the_question_kept_there_its_waits_id(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    kept = Path(run("job", "ask", asking, str(block))[1])
    monkeypatch.chdir(kept.parent)
    assert run("job", "card", kept.name, "--waits", "demo:j1-7")[0] == 0
    assert [question["waits"] for question in state.load("demo")["jobs"]["j1"]["questions"]] == ["demo:j1-7"]


def test_job_ask_reads_a_block_from_stdin_and_keeps_each_block_in_its_own_file(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = []
    for text in (SHORT_ASK, SHORT_ASK.replace("ASK How should", "ASK Where should")):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        code, path = run("job", "ask", asking, "-")
        assert code == 0
        kept.append(Path(path))
    assert kept[0] != kept[1]
    assert [path.read_text().split("\n")[0] for path in kept] == [
        "ASK How should a finished run's files be laid out so its page opens without guessing?",
        "ASK Where should a finished run's files be laid out so its page opens without guessing?",
    ]
    assert [question["file"] for question in state.load("demo")["jobs"]["j1"]["questions"]] == [str(path) for path in kept]


def test_a_job_keeps_every_question_it_asks_each_open_until_his_answer_to_its_own_card(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    titles = ["How should", "Where should", "When should"]
    kept = []
    for title in titles:
        monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("ASK How should", f"ASK {title}")))
        kept.append(run("job", "ask", asking, "-")[1])
    links = [f"https://acme.slack.com/archives/D123/p179430000000010{n}" for n in range(3)]
    shown = [
        {"question": title, "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": title, "waits": f"demo:j1-{n}"}}
        for n, (title, link) in enumerate(zip(titles, links, strict=True))
    ]
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": list(shown), "pr_cards": []})
    for n, (path, link) in enumerate(zip(kept[:2], links[:2], strict=True)):
        assert run("job", "card", path, "--waits", f"demo:j1-{n}")[0] == 0
        assert run("job", "set", asking, "--card", link)[0] == 0
    assert run("job", "set", asking, "--card", links[2])[0] == 0
    questions = state.load("demo")["jobs"]["j1"]["questions"]
    assert [(question["file"], question["waits"], question["card"]) for question in questions] == [
        (kept[0], "demo:j1-0", links[0]),
        (kept[1], "demo:j1-1", links[1]),
        (kept[2], None, None),
        (None, "demo:j1-2", links[2]),
    ]
    shown.pop(0)
    view.refresh_cards("demo")
    status = run("status")[1]
    assert links[0] not in status
    assert f"asks him: Where should a finished run's files be laid out so its page opens without guess…; card {links[1]} (demo:j1-1)" in status
    assert f"no card drawn yet, kept in {kept[2]}" in status
    assert f"asks him: When should; card {links[2]} (demo:j1-2)" in status


def test_a_redrawn_card_moves_to_the_question_that_redoes_it_and_a_card_never_posted_holds_nothing(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": []})
    kept = []
    for text in (SHORT_ASK, SHORT_ASK.replace("opens in 3 requests", "opens in 3 calls"), SHORT_ASK.replace("ASK How should", "ASK Where should")):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        kept.append(run("job", "ask", asking, "-")[1])
    run("job", "card", kept[0], "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    run("job", "card", kept[1], "--waits", "demo:j1-7")
    run("job", "card", kept[2], "--waits", "demo:j1-8")
    questions = state.load("demo")["jobs"]["j1"]["questions"]
    assert [(question["file"], question["waits"], question["card"]) for question in questions] == [(kept[1], "demo:j1-7", link), (kept[2], "demo:j1-8", None)]
    status = run("status")[1]
    assert f"card {link} (demo:j1-7)" in status
    assert "asks him: Where should" not in status


def test_his_words_under_a_card_answer_it_so_its_job_never_redraws_it_with_a_new_question(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": []})
    view.refresh_cards("demo")
    kept = []
    for text in (SHORT_ASK, SHORT_ASK.replace("ASK How should", "ASK Where should")):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        kept.append(run("job", "ask", asking, "-")[1])
    run("job", "card", kept[0], "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    reply = "https://acme.slack.com/archives/D123/p1794300000000400?thread_ts=1794300000.000100"
    assert cli.main(["answer", "a1", "--words", "its one snapshot file, just wait for the copy", "--link", reply]) == 2
    assert "  j1 waits on o1 and has not heard a1: SendMessage it his words verbatim, or mark it untouched" in capsys.readouterr().err
    assert run("answer", "a1", "--sent", "j1") == (
        0,
        "his answer a1 recorded: 0 standing, 0 superseded, 0 reopened by it; 1 waiting job sent his words, 0 untouched",
    )
    assert cli.main(["job", "card", kept[1], "--waits", "demo:j1-7"]) == 2
    refusal = capsys.readouterr().err
    assert 'demo:j1-7: his words under that card answer it' in refusal
    assert '"its one snapshot file, just wait for the copy"' in refusal
    assert [question["file"] for question in state.load("demo")["jobs"]["j1"]["questions"] if question["waits"] == "demo:j1-7"] == [kept[0]]
    assert cli.main(["answer", "a2", "--words", "One snapshot file", "--link", link]) == 2
    assert "  j1 waits on o1 and has not heard a2: SendMessage it his words verbatim, or mark it untouched" in capsys.readouterr().err
    assert run("answer", "a2", "--sent", "j1")[0] == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("ASK How should", "ASK When should")))
    assert cli.main(["job", "ask", asking, "-"]) == 2
    refusal = capsys.readouterr().err
    assert f"- a1 his words under this job's card {link}: its one snapshot file, just wait for the copy" in refusal
    assert f"- a2 his pick on this job's card {link}: One snapshot file" in refusal
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("ASK How should", "ASK When should")))
    assert run("job", "ask", asking, "-", "--unsettled", "a1, a2: he picked the layout, not when it is written")[0] == 0


IDLE_RULING = {
    "id": "ac6134849ff8a392c9f26294d4303635d1c27a58",
    "title": "Owner Nov 9: close every completed or idle Claude or Codex session and its terminal, without asking\nsecond line",
}
MERGE_RULING = {"id": "33eaad946c1f36dc438a3a04d443eb7d71573815", "title": "What should the revert of the merge rule cover?"}


def test_job_ask_refuses_a_question_his_recorded_answer_may_settle_until_a_reason_names_it(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], recorded: Answers
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    recorded.found = [IDLE_RULING]
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    refusal = capsys.readouterr().err
    assert "- ac61348 Owner Nov 9: close every completed or idle Claude or Codex session and its terminal, without asking" in refusal
    assert "second line" not in refusal
    assert recorded.asked[0].startswith("How should a finished run's files be laid out")
    assert "SQLite" not in recorded.asked[0]
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    assert cli.main(["job", "ask", asking, str(block), "--unsettled", "it closes sessions, not a run's files"]) == 2
    why = "ac61348: it closes sessions; this asks how a run's files are laid out"
    assert run("job", "ask", asking, str(block), "--unsettled", why)[0] == 0
    assert state.load("demo")["jobs"]["j1"]["questions"][-1]["unsettled"] == why


def test_job_ask_lists_every_candidate_in_the_same_order_whatever_the_reason_names(
    asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], recorded: Answers
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    recorded.found = [IDLE_RULING, MERGE_RULING]
    refusals = []
    for why in ("33eaad9: a merge-rule revert, not a run's files", "ac61348: it closes sessions, not a run's files"):
        assert cli.main(["job", "ask", asking, str(block), "--unsettled", why]) == 2
        refusals.append(capsys.readouterr().err)
    listed = [[line for line in refusal.splitlines() if line.startswith("- ")] for refusal in refusals]
    assert listed[0] == listed[1] == [
        "- ac61348 Owner Nov 9: close every completed or idle Claude or Codex session and its terminal, without asking",
        "- 33eaad9 What should the revert of the merge rule cover?",
    ]
    form = '--unsettled one string that names every id below, such as --unsettled "ac61348: <why it does not settle this>; 33eaad9: <why it does not settle this>".'
    assert all(form in refusal for refusal in refusals)
    assert "The reason given leaves out ac61348." in refusals[0]
    assert "The reason given leaves out 33eaad9." in refusals[1]


def test_job_ask_checks_a_reworded_ask_against_the_answers_its_refusal_listed_until_its_title_changes(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], recorded: Answers
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    recorded.found = [IDLE_RULING]
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert "- ac61348 " in capsys.readouterr().err
    recorded.found = [MERGE_RULING]
    where = SHORT_ASK.replace("ASK How should", "ASK Where should")
    block.write_text(where)
    assert cli.main(["job", "ask", asking, str(block), "--unsettled", "ac61348: it closes sessions; this asks where a run's files go"]) == 2
    assert "The reason given leaves out 33eaad9." in capsys.readouterr().err
    recorded.found = [IDLE_RULING]
    block.write_text(where.replace("about 180 small files", "180 files"))
    why = "33eaad9: a merge-rule revert, not where a run's files go"
    assert run("job", "ask", asking, str(block), "--unsettled", why)[0] == 0
    assert len(recorded.asked) == 2
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["questions"][-1]["unsettled"], job["may_settle"]) == ("waiting", why, None)


def test_job_ask_checks_a_link_by_its_label_never_the_words_of_its_url(run: Run, asking: str, tmp_path: Path, recorded: Answers) -> None:
    block = tmp_path / "ask.md"
    linked = "Meanwhile <https://github.com/Forge-AI/monorepo/pull/53849|the layout fix> builds on neither layout."
    block.write_text(SHORT_ASK.replace("Meanwhile nothing is built on either layout.", linked))
    assert run("job", "ask", asking, str(block))[0] == 0
    assert "the layout fix" in recorded.asked[0]
    assert "github" not in recorded.asked[0]
    assert "monorepo" not in recorded.asked[0]


def test_job_ask_checks_his_answers_in_the_coordinators_checkout_whatever_directory_it_runs_in(
    run: Run, asking: str, tmp_path: Path, recorded: Answers, monkeypatch: pytest.MonkeyPatch
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert run("job", "ask", asking, str(block))[0] == 0
    checkout = state.load("demo")["roles"]["coordinator"]["cwd"]
    assert checkout != os.getcwd()
    assert recorded.cwds == [checkout]


def test_job_ask_reads_a_remote_lanes_report_when_given_no_file(run: Run, asking: str, state_root: Path) -> None:
    assert run("job", "ask", asking)[0] == 2
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(where="remote", report=SHORT_ASK)
    assert run("job", "ask", asking) == (0, str(state_root / "demo" / "asks" / f"j1-{hashlib.sha256(SHORT_ASK.encode()).hexdigest()[:8]}.md"))


def test_job_report_keeps_a_jobs_whole_return_from_stdin_and_prints_it_back(
    run: Run, demo: str, state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    assert run("job", "report", "j1")[0] == 2
    monkeypatch.setattr("sys.stdin", io.StringIO("READY https://github.com/yasyf/cc-skills/pull/1\nSERVES: his order\nDESIGN: none\n"))
    assert run("job", "report", "j1", "-") == (0, str(state_root / "demo" / "reports" / "j1.md"))
    assert run("job", "report", "j1") == (0, "READY https://github.com/yasyf/cc-skills/pull/1\nSERVES: his order\nDESIGN: none")


def test_a_builders_done_names_its_proof_and_what_he_sees_is_proven_on_that_surface(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    fetched = tmp_path / "view.json"
    fetched.write_text("{}")
    recording = tmp_path / "queue-click.mov"
    recording.write_bytes(b"\0")
    assert keep(monkeypatch, capsys, "j1", "DONE the queue shows one card\nCHECK: it looked right\n")[0] == 2
    assert keep(monkeypatch, capsys, "j1", "DONE the queue shows one card\nCHECK: /gone/view.json\n")[1].startswith(
        "drive: j1: refused, nothing kept: a DONE names its proof on a CHECK: line"
    )
    seen = "DONE the queue shows one card\nNATIVE: the dashboard's queue view\nCHECK: {}\n"
    code, refused = keep(monkeypatch, capsys, "j1", seen.format(fetched))
    assert code == 2
    assert "this builds something he sees, by its NATIVE: line, so its proof is from that surface" in refused
    assert keep(monkeypatch, capsys, "j1", seen.format(recording))[0] == 0
    assert keep(monkeypatch, capsys, "j1", seen.format("https://poetic-technology.slack.com/archives/D1/p1794300000000100"))[0] == 0
    unseen = "DONE the watch stops on a dead lane\nNATIVE: none: he never sees it\nCHECK: {}\n"
    assert keep(monkeypatch, capsys, "j1", unseen.format(fetched))[0] == 0
    assert state.load("demo")["jobs"]["j1"]["state"] == "done"


def test_job_report_refuses_an_empty_stdin(run: Run, demo: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert cli.main(["job", "report", "j1", "-"]) == 2
    assert "stdin held no report for j1" in capsys.readouterr().err
    assert run("job", "report", "j1")[0] == 2


def test_job_report_prints_the_one_line_result_of_a_job_that_kept_no_report_and_refuses_one_that_returned_nothing(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    assert cli.main(["job", "report", "j1"]) == 2
    assert "j1 kept no report and has returned nothing yet" in capsys.readouterr().err
    run("job", "set", "j1", "--said", "DONE installed 1.0.76")
    assert run("job", "report", "j1") == (0, "DONE installed 1.0.76\n(j1 kept no report; this one-line result is all it returned)")
    assert json.loads(run("job", "report", "j1", "--json")[1]) == {"id": "j1", "report": None, "said": "DONE installed 1.0.76"}


def keep(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], job_id: str, text: str) -> tuple[int, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    code = cli.main(["job", "report", job_id, "-"])
    return code, capsys.readouterr().err


def test_an_answer_order_is_one_readers_and_a_build_orders_builder_waits_on_its_readers_research(
    run: Run, communicator: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "why did the copy stall?", "--kind", "answer", "--complex")
    assert [state.load("demo")["orders"]["o1"][field] for field in ("kind", "complex")] == ["answer", True]
    assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", "fix the copy"]) == 2
    assert "refused, no job added: o1 is an answer order: one reader researches it and answers him" in capsys.readouterr().err
    assert run("job", "add", "reader", "--order", "o1", "--goal", "answer him")[0] == 0
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    assert cli.main(["job", "add", "builder", "--order", "o2", "--goal", "build it"]) == 2
    assert "o2 is a build order, so its builder starts only once an Opus reader has kept a Researched: line" in capsys.readouterr().err
    run("job", "add", "reader", "--order", "o2", "--goal", "research: the copy")
    keep(monkeypatch, capsys, "j2", "DONE read the copy path\n")
    assert cli.main(["job", "add", "builder", "--order", "o2", "--goal", "build it"]) == 2
    capsys.readouterr()
    assert keep(monkeypatch, capsys, "j2", RESEARCH) == (0, "")
    assert run("job", "add", "builder", "--order", "o2", "--goal", "build it")[1].splitlines()[0] == "j3"
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "an order from an older skill")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL copies fail on plat", "--incident", "--parts", "fix", "first check")
    assert state.load("demo")["orders"]["o4"]["kind"] == "build"
    for order_id, part in (("o3", []), ("o4", ["--part", "fix"])):
        assert run("job", "add", "builder", "--order", order_id, "--goal", "fix it", *part)[0] == 0


def test_a_build_orders_design_job_waits_on_its_readers_research(
    run: Run, communicator: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    assert cli.main(["job", "add", "design", "--order", "o1", "--goal", "cards: where the copy runs"]) == 2
    assert "o1 is a build order, so its design starts only once an Opus reader has kept a Researched: line" in capsys.readouterr().err
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    keep(monkeypatch, capsys, "j1", RESEARCH)
    assert run("job", "add", "design", "--order", "o1", "--goal", "cards: where the copy runs")[0] == 0


def test_a_jobs_clock_is_a_time_to_come(run: Run, communicator: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store")
    run("job", "add", "codex", "--order", "o1", "--goal", "first check")
    assert cli.main(["job", "set", "j1", "--due", "2026-01-01T00:00:00+00:00"]) == 2
    assert "j1: refused, no clock set:" in capsys.readouterr().err
    assert state.load("demo")["jobs"]["j1"]["due"] is None
    assert run("job", "set", "j1", "--due", "+10m")[0] == 0
    assert timedelta(minutes=9) < state.parse_time(state.load("demo")["jobs"]["j1"]["due"]) - state.now() <= timedelta(minutes=10)


def test_a_readers_design_return_carries_his_options_and_holds_the_builder_until_a_design_job_starts(
    run: Run, communicator: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    one_option = DESIGN_RESEARCH.replace("- on a separate worker that reads the bucket\n", "")
    for refused in (one_option, DESIGN_RESEARCH.replace("(recommended)", "")):
        code, err = keep(monkeypatch, capsys, "j1", refused)
        assert (code, "j1: refused, nothing kept: a reader's DESIGN return is a choice for him" in err) == (2, True)
    assert run("job", "report", "j1")[0] == 2
    assert keep(monkeypatch, capsys, "j1", DESIGN_RESEARCH) == (0, "")
    assert state.load("demo")["jobs"]["j1"]["state"] == "done"
    assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", "build it"]) == 2
    assert "j1 returned DESIGN: the choice its research found goes to him as cards before any builder" in capsys.readouterr().err
    assert run("job", "add", "design", "--order", "o1", "--goal", "cards: where the copy runs")[0] == 0
    assert run("job", "add", "builder", "--order", "o1", "--goal", "build it")[0] == 0


def test_a_builders_ready_says_how_it_reuses_what_its_research_found(
    run: Run, communicator: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    keep(monkeypatch, capsys, "j1", RESEARCH)
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    ready = "READY https://github.com/yasyf/cc-skills/pull/1\nSERVES: drive tooling\n"
    code, err = keep(monkeypatch, capsys, "j2", ready)
    assert code == 2
    assert "j2: refused, nothing kept: j1's research found what already does part of this: supervisor/copies.rs store_copy_at copies one store." in err
    assert keep(monkeypatch, capsys, "j2", ready + "REUSES: calls store_copy_at for each store\n") == (0, "")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a new verb", "--kind", "build")
    run("job", "add", "reader", "--order", "o2", "--goal", "research: the verb")
    keep(monkeypatch, capsys, "j3", RESEARCH.replace("exists: supervisor/copies.rs store_copy_at copies one store", "exists: none"))
    run("job", "add", "builder", "--order", "o2", "--goal", "build it")
    assert keep(monkeypatch, capsys, "j4", ready) == (0, "")


def test_order_set_sorts_an_order_again(run: Run, demo: str) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "why did the copy stall?", "--kind", "answer")
    assert run("order", "set", "o1", "--kind", "build", "--complex")[0] == 0
    assert [state.load("demo")["orders"]["o1"][field] for field in ("kind", "complex")] == ["build", True]
    run("order", "set", "o1", "--no-complex")
    assert state.load("demo")["orders"]["o1"]["complex"] is False


@pytest.mark.parametrize(
    ("kind", "first", "taken"),
    [
        ("builder", "DONE installed 1.0.76", "done"),
        ("builder", "READY https://github.com/yasyf/cc-skills/pull/1", "waiting"),
        ("builder", "WAITING: CI on #7", "waiting"),
        ("design", "DESIGN the table move", "waiting"),
        ("builder", "BLOCKED no order of his names this", "failed"),
        ("reader", "The flag exists since 1.0.40", "done"),
        ("builder", "BUILT lr-move, waits on demo:j1-3", "built"),
        ("builder", "I pushed the fix", None),
    ],
)
def test_a_kept_report_sets_a_running_job_from_its_first_line(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, kind: str, first: str, taken: str | None
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", kind, "--order", "o1", "--goal", "ship")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship again")
    run("job", "set", "j2", "--state", "stopped")
    for job_id in ("j1", "j2"):
        monkeypatch.setattr("sys.stdin", io.StringIO(f"{first}\nCHECK: https://github.com/yasyf/cc-skills/actions/runs/12\n"))
        run("job", "report", job_id, "-")
    jobs = state.load("demo")["jobs"]
    assert (jobs["j1"]["state"], jobs["j1"]["said"]) == ((taken, first) if taken else ("running", None))
    assert (jobs["j2"]["state"], jobs["j2"]["said"]) == ("stopped", None)


def test_a_built_return_sets_its_job_built_on_its_branch_waiting_on_each_card_it_names(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    card = "https://acme.slack.com/archives/D123/p1794200000000100"
    monkeypatch.setattr("sys.stdin", io.StringIO(f"BUILT lr-move, waits on demo:j1-3, {card}\nCHECK: build 12\n"))
    run("job", "report", "j1", "-")
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["branch"], job["waits"]) == ("built", "lr-move", ["demo:j1-3", card])


def test_job_set_takes_state_stopped_alone_and_says_the_line_a_job_returns_sets_every_other(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    for taken in ("waiting", "built", "done", "failed"):
        assert cli.main(["job", "set", "j1", "--state", taken]) == 2
        refusal = capsys.readouterr().err
        assert "j1 is running, and --state sets only stopped, for a job that ends unfinished: every other state comes from the line the job returns" in refusal
        assert (
            "which the watch reads or drive job report j1 - takes: DONE sets done, READY sets waiting, WAITING sets waiting, DESIGN sets waiting,"
            " ASK sets waiting, BLOCKED sets failed, and BUILT <branch>, waits on <cards> sets built"
        ) in refusal
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    assert run("job", "set", "j1", "--state", "stopped")[0] == 0
    assert state.load("demo")["jobs"]["j1"]["state"] == "stopped"


def test_job_set_naming_the_state_a_job_already_holds_changes_nothing_and_says_so(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "reader", "--order", "o1", "--goal", "read where it ships")
    monkeypatch.setattr("sys.stdin", io.StringIO("DONE it ships from the release branch\n"))
    assert run("job", "report", "j1", "-")[0] == 0
    code, out = run("job", "set", "j1", "--state", "done", "--said", "read")
    assert (code, out.splitlines()[0]) == (0, "j1 is already done")
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("done", "read")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship the rest")
    assert run("job", "set", "j2", "--state", "running")[1].splitlines()[0] == "j2 is already running"


def test_job_report_prints_a_remote_lanes_latest_report(run: Run, demo: str) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship", "--remote")
    with state.edit("demo") as data:
        data["jobs"]["j1"]["report"] = "DONE installed 1.0.76\nCHECK: drive --version"
    assert run("job", "report", "j1") == (0, "DONE installed 1.0.76\nCHECK: drive --version")


@pytest.fixture(autouse=True)
def on_github(monkeypatch: pytest.MonkeyPatch) -> dict:
    pr = {
        "title": "Fix: land it",
        "body": CARD_BODY,
        "comments": [],
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": "abc",
        "baseRefName": "main",
        "headRefName": "land-it",
        "statusCheckRollup": [],
        "reviewDecision": "REVIEW_REQUIRED",
        "url": PR,
    }
    monkeypatch.setattr(card, "run", lambda cmd: json.dumps(pr))
    return pr


@pytest.fixture
def with_pr(run: Run, demo: str) -> str:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "land it")
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


def test_pr_add_on_a_public_repo_refuses_a_live_picture_and_a_private_pr(
    run: Run, demo: str, on_github: dict, visibilities: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    visibilities.update({"Forge-AI/monorepo": "public", "Forge-AI/litestream": "private", "owner/repo": None})
    live = "https://github.com/yasyf/cc-skills/blob/3d8111f/pr-shots/plan.png?raw=true"
    on_github["body"] = (
        f"## Show me\n![Plan]({live})\n"
        "![Hover](https://github.com/yasyf/cc-skills/blob/73f9efa/pr-shots/demo-hover.png?raw=true)\n"
        "Opened on Forge-AI/litestream#7, like owner/repo#3."
    )
    assert cli.main(["pr", "add", PR, "--job", "j1"]) == 2
    error = capsys.readouterr().err
    assert error.startswith(
        f"drive: Forge-AI/monorepo#42: Forge-AI/monorepo is public, and its body shows pictures outside a demo or public"
        f" path ({live}) and PRs of the private Forge-AI/litestream. "
    )
    assert "Forge-AI/monorepo#42" not in state.load("demo")["prs"]


def test_pr_add_on_a_public_repo_takes_demo_pictures(run: Run, demo: str, on_github: dict, visibilities: dict) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    visibilities.update({"Forge-AI/monorepo": "public", "yasyf/cc-skills": "public"})
    on_github["body"] = (
        "## Show me\n<img src=\"https://raw.githubusercontent.com/yasyf/cc-skills/73f9efa/public/board.png\">\n"
        "![Hover](https://github.com/yasyf/cc-skills/blob/73f9efa/pr-shots/demo-hover.png?raw=true)\n"
        "Follows yasyf/cc-skills#4146."
    )
    assert run("pr", "add", PR, "--job", "j1") == (0, "Forge-AI/monorepo#42")


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


def test_pr_set_tapped_records_only_a_tap_his_permalink_shows(run: Run, with_pr: str) -> None:
    assert run("pr", "set", PR, "--tapped", "Hold")[0] == 2
    for unlinked in ("recorded", "migrated hold: breaks:a client on an older build cannot read it", "drive applying his rule 18082c8"):
        with pytest.raises(SystemExit):
            run("pr", "set", PR, "--tapped", "Hold", "--tap-source", unlinked)
    assert state.load("demo")["prs"][with_pr]["tap"] is None
    link = "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794173216327589"
    assert run("pr", "set", PR, "--tapped", "Approve", "--tap-source", link)[0] == 0
    assert state.load("demo")["prs"][with_pr]["tap"]["source"] == link


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


def test_pr_set_refuses_a_revert_the_incident_fix_exemption_and_names_the_tap_card_it_waits_on(
    run: Run, with_pr: str, on_github: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "set", "o1", "--note", "record 3fa2c1")
    reverts = [
        ('Revert "Fix: land it"', "Reverts Forge-AI/monorepo#41"),
        ("store: ⏪️ Fix: no table move starts", CARD_BODY),
        ("Fix: no table move starts", "## Summary\nThis reverts #41 whole."),
    ]
    for title, body in reverts:
        on_github.update(title=title, body=body)
        assert cli.main(["pr", "set", PR, "--exempt", "incident fix"]) == 2
        refusal = capsys.readouterr().err
        assert f'{with_pr}: refused, it reverts a change, and a revert never lands as an "incident fix": no incident order approves it, only his tap does.' in refusal
        assert f"Draw his tap card with drive pr set {PR} --tap --serves o1 --design none" in refusal
        assert "its body stating the bug in the change it takes out, with that change's PR and evidence, from o1's record (record 3fa2c1)" in refusal
    assert state.load("demo")["prs"][with_pr]["exempt"] is None
    assert run("pr", "tapped", PR)[0] == 1
    on_github.update(title="Fix: the mint endpoint answers again", body=CARD_BODY)
    assert run("pr", "set", PR, "--exempt", "incident fix")[0] == 0
    assert run("pr", "tapped", PR) == (0, f"{with_pr}: exempt: incident fix")


def test_pr_set_refuses_the_incident_fix_exemption_on_an_incidents_stop(
    run: Run, demo: str, on_github: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.reads, "run", recorded([]))
    run("incident", "open", "table moves rerun on every write", "--no-rollback", "--record", RECORD, *FILED)
    run("job", "add", "builder", "--order", "o1", "--part", "stop", "--goal", "ship the stop")
    run("pr", "add", PR, "--job", "j4")
    assert cli.main(["pr", "set", PR, "--exempt", "incident fix"]) == 2
    refusal = capsys.readouterr().err
    assert 'Forge-AI/monorepo#42: refused, it is o1\'s stop, which turns the failing path off, and a stop never lands as an "incident fix"' in refusal
    assert f"Draw his tap card with drive pr set {PR} --tap --serves o1 --design none, its body quoting the bug from o1's record (record 5f6b2b5)" in refusal
    assert state.load("demo")["prs"]["Forge-AI/monorepo#42"]["exempt"] is None


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


def test_pr_set_tap_refuses_a_body_his_card_would_refuse_naming_every_missing_section(
    run: Run, with_pr: str, on_github: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    run("pr", "set", PR, "--no-tap", "--why", "not his order")
    on_github["body"] = CARD_BODY.replace("## What you'll hate most", "## Biggest surprise")
    assert cli.main(["pr", "set", PR, "--tap", "--serves", "o1", "--design", "none"]) == 2
    reason = f"its tap card needs a \"What you'll hate most\" section in the body of {PR}"
    assert capsys.readouterr().err.strip() == f"drive: {with_pr}: {reason}"
    pr = state.load("demo")["prs"][with_pr]
    assert (pr["wants_tap"], pr.get("serves")) == (False, None)
    on_github["body"] = CARD_BODY.split("## His words")[0].replace("## What you'll hate most", "## Biggest surprise")
    assert cli.main(["pr", "set", PR, "--tap", "--serves", "o1", "--design", "none"]) == 2
    assert capsys.readouterr().err.startswith(
        f"drive: {with_pr}: its tap card needs a \"What you'll hate most\" section and a \"His words, step by step\""
        f" list naming his order in the body of {PR}. No order of his names {PR}: "
    )


@pytest.mark.parametrize(
    ("alone", "lines", "refused"),
    [
        ("", "", 'a "Releasable alone" section saying why a release cut at its merge commit is safe by itself'),
        ("None.", "", 'a "Releasable alone" section saying why'),
        ("The new column ships empty and readers skip it.", "", ""),
        ("The new column ships empty; then run the fill from a pass box.", "", 'but it says "then run" in'),
        ("Every target reads both shapes, so any order is safe.", "- **Install:** merge, then backfill after the deploy\n", 'but it says "then backfill" in "- **Install:** merge'),
        ("Every target reads both shapes, so any order is safe.", "## Risk\nIt needs no backfill and no pass box.\n", ""),
        (
            "Every target reads both shapes, and nothing has to run once it ships.",
            "## Risk\nOnly the `backfill-runner` account, holding `backfills:run`, starts one from a pass box, and each run shows on the admin backfills page.\n",
            "",
        ),
        ("Every target reads both shapes.", "## Risk\nRun the backfill once it ships.\n", 'but it says "Run the backfill once it ships" in'),
        ("Every target reads both shapes, so any order is safe.", "PROVE: after release, open the run page and see its steps\n", ""),
        ("Every target reads both shapes.", "```\nINSTALL: drive install x\nthen run fill\n```\n", ""),
    ],
)
def test_pr_set_tap_refuses_a_body_that_is_not_releasable_alone(
    run: Run, with_pr: str, on_github: dict, capsys: pytest.CaptureFixture[str], alone: str, lines: str, refused: str
) -> None:
    run("pr", "set", PR, "--no-tap", "--why", "not his order")
    section = f"## Releasable alone\n{alone}\n\n" if alone else ""
    on_github["body"] = section + CARD_BODY.split("\n\n", 1)[1] + lines
    code = cli.main(["pr", "set", PR, "--tap", "--serves", "o1", "--design", "none"])
    error = capsys.readouterr().err
    assert (code, refused in error) == ((2, True) if refused else (0, True))
    assert ("Dev must always be releasable" in error) == bool(refused)


def test_pr_set_serves_on_a_pr_added_with_tap_refuses_a_body_his_card_would_refuse(
    run: Run, with_pr: str, on_github: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    on_github["body"] = "## Summary\nLands it."
    assert cli.main(["pr", "set", PR, "--serves", "o1", "--design", "none"]) == 2
    assert capsys.readouterr().err.startswith(
        f"drive: {with_pr}: its tap card needs a \"What you'll hate most\" section and a \"His words, step by step\""
    )
    assert state.load("demo")["prs"][with_pr].get("serves") is None
    run("pr", "set", PR, "--no-tap", "--why", "not his order")
    assert run("pr", "set", PR, "--serves", "o1")[0] == 0


def test_pr_set_tap_serving_a_drive_tooling_order_needs_no_words_of_his(run: Run, with_pr: str, on_github: dict) -> None:
    run("pr", "set", PR, "--no-tap", "--why", "not his order")
    on_github["body"] = CARD_BODY.split("## His words")[0] + "## His words, step by step\nNone: drive tooling.\n"
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")[0] == 2
    assert run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a tooling PR lands on his Approve") == (0, "o2")
    code, out = run("pr", "set", PR, "--tap", "--serves", "o2", "--design", "none")
    assert code == 0
    assert out.splitlines()[-1] == "serves o2, his words: Drive tooling: a tooling PR lands on his Approve"
    pr = state.load("demo")["prs"][with_pr]
    assert (pr["wants_tap"], pr["serves"]) == (True, "o2")


def test_pr_set_tap_accepts_a_body_with_every_section_his_card_needs(run: Run, with_pr: str) -> None:
    run("pr", "set", PR, "--no-tap", "--why", "not his order")
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")[0] == 0
    assert state.load("demo")["prs"][with_pr]["wants_tap"] is True


def test_pr_set_tap_on_an_exempt_pr_draws_no_card_so_checks_none(run: Run, with_pr: str, on_github: dict) -> None:
    run("pr", "set", PR, "--exempt", "test-only")
    on_github["body"] = "## Summary\nTests only."
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")[0] == 0


def test_pr_set_port_of_reads_no_body(run: Run, with_pr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def unread(cmd: list[str]) -> str:
        raise AssertionError("a port draws no card, so its body is never read")

    monkeypatch.setattr(card, "run", unread)
    assert run("pr", "set", PR, "--port-of", "51234")[0] == 0
    assert state.load("demo")["prs"][with_pr]["exempt"] == "pure port"


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


def test_pr_set_on_a_pr_the_drive_does_not_track_names_drive_pr_add(run: Run, demo: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["pr", "set", PR, "--no-tap", "--why", "not his order"]) == 2
    assert f"no drive PR Forge-AI/monorepo#42: add it first with drive pr add {PR} --job <j>, then set it" in capsys.readouterr().err


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
    "checks": [{"name": "test", "conclusion": "SUCCESS"}],
    "reviewDecision": "APPROVED",
    "mergeable": "MERGEABLE",
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
    assert 'Forge-AI/monorepo#42 "Fix: the live title" open, checks pass, review approved' in out
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
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "something else")
    for key in ("o1", "j1", with_pr, PR):
        code, out = run("status", key)
        assert code == 0
        assert out.splitlines()[0].startswith("demo: 1 open of 1 orders")
        assert "land it" in out
        assert "something else" not in out
    assert run("status", "o2")[1].splitlines()[0].startswith("demo: 1 open of 1 orders")
    assert run("status", "j9")[0] != 0


def test_status_reads_a_tracked_pr_by_its_bare_number_and_names_the_forms_an_id_takes(
    run: Run, with_pr: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(github, "fetch", lambda keys: {})
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "something else")
    code, out = run("status", "42")
    assert (code, "land it" in out, "something else" in out) == (0, True, False)
    assert cli.main(["status", "53615"]) == 2
    assert "no order, job or PR 53615; name an order o<n>, a job j<n>, or a PR as owner/repo#N, its URL or its bare number" in capsys.readouterr().err
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/sand#42"] = data["prs"][with_pr] | {"url": "https://github.com/Forge-AI/sand/pull/42"}
    assert cli.main(["status", "42"]) == 2
    assert "42 is the number of Forge-AI/monorepo#42, Forge-AI/sand#42; name one as owner/repo#42" in capsys.readouterr().err


def test_order_show_and_job_show_print_what_status_prints(run: Run, with_pr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "fetch", lambda keys: {})
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "something else")
    for verb, key in (("order", "o1"), ("job", "j1"), ("job", "42")):
        shown = run(verb, "show", key)
        assert (shown[0], "land it" in shown[1]) == (0, True)
        assert shown == run("status", key)


def test_status_prints_each_order_its_ids_name_whole_and_closed_ones_too(run: Run, demo: str) -> None:
    words = "move the replica's restore " + "onto the primary's one restore code path, " * 4
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", words)
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "something else")
    run("job", "add", "builder", "--order", "o1", "--goal", "one restore code path " * 8)
    said = "DONE " + "merged and installed, the replica now calls the primary's restore code, " * 4
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(state="done", said=said)
    run("order", "set", "o1", "--state", "done")
    lines = run("status", "j1")[1].splitlines()
    assert lines == [
        "demo: 0 open of 1 orders; PR fields live",
        f"- o1 done: {' '.join(words.split())}",
        f"  parts: {state.load('demo')['orders']['o1']['parts'].popitem()[0]}",
        f"  - j1 builder done: {' '.join(('one restore code path ' * 8).split())}",
        f"    said: {' '.join(said.split())}",
    ]


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
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "for other", "--drive", "other")
    assert state.load("other")["orders"]["o1"]["text"] == "for other"
    assert state.load("demo")["orders"] == {}


def test_unresolvable_drive_exits_two(run: Run, state_root: Path) -> None:
    assert run("status")[0] == 2


def fake_module(monkeypatch: pytest.MonkeyPatch, name: str, main: Callable[..., int]) -> None:
    module = types.ModuleType(f"drive.{name}")
    module.main = main
    monkeypatch.setitem(sys.modules, f"drive.{name}", module)
    monkeypatch.setattr(drive, name, module, raising=False)


def test_watch_loop_runs_the_watch_with_the_drive_flag(run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(cli.watch, "main", lambda name: calls.append(name) or 0)
    assert run("watch", "--loop", "--drive", "demo")[0] == 0
    assert calls == ["demo"]


def test_card_dispatches_stdin_and_stdout(run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    fake_module(monkeypatch, "card", lambda stdin, stdout: calls.append((stdin, stdout)) or 3)
    assert run("card")[0] == 3
    assert calls == [(sys.stdin, sys.stdout)]


def test_card_with_no_request_on_stdin_names_card_command_and_what_stdin_held(
    state_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for stdin, held in (("", "nothing"), ("\n", "nothing"), ("tap it", "no JSON")):
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        assert cli.main(["card"]) == 2
        assert capsys.readouterr().err == (
            f"drive: drive card answers cc-slack's card_command: pipe it one request JSON on stdin, such as {{\"prs\": [...]}}; stdin held {held}\n"
        )
    with pytest.raises(SystemExit):
        cli.main(["-h"])
    assert "cc-slack's card_command" in capsys.readouterr().out


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
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "build the files design", "--parts", "stack 1", "stack 2", "stack 3")
    assert run("job", "add", "builder", "--order", "o1", "--goal", "all of it", "--agent", "a")[0] == 2
    assert run("job", "add", "builder", "--order", "o1", "--goal", "x", "--part", "stack 9")[0] == 2
    assert run("job", "add", "builder", "--order", "o1", "--goal", "stacks 2-3", "--part", "stack 2", "--part", "stack 3")[1] == "j1"
    run("job", "set", "j1", "--part", "stack 1", "--card", "https://acme.slack.com/archives/D1/p1")
    data = state.load("demo")
    assert list(data["orders"]["o1"]["parts"]) == ["stack 1", "stack 2", "stack 3"]
    assert data["jobs"]["j1"]["parts"] == ["stack 2", "stack 3", "stack 1"]
    assert [question["card"] for question in data["jobs"]["j1"]["questions"]] == ["https://acme.slack.com/archives/D1/p1"]


def test_job_add_prints_each_live_job_already_carrying_its_part_under_its_id(run: Run, demo: str) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "three spikes", "--parts", "spike 1", "spike 2", "spike 3")
    run("job", "add", "builder", "--order", "o1", "--goal", "spike 1: restore cost at three database sizes, one operation at a time", "--part", "spike 1")
    run("job", "add", "reader", "--order", "o1", "--goal", "spike 2", "--part", "spike 2")
    with state.edit("demo") as data:
        data["jobs"]["j2"]["state"] = "done"
    assert run("job", "add", "builder", "--order", "o1", "--goal", "spike 2 again", "--part", "spike 2") == (0, "j3")
    code, out = run("job", "add", "builder", "--order", "o1", "--goal", "spikes", "--part", "spike 1", "--part", "spike 2", "--part", "spike 3")
    assert (code, out.splitlines()) == (
        0,
        [
            "j4",
            'j1 builder running on "spike 1": spike 1: restore cost at three database sizes, one operation',
            'j3 builder running on "spike 2": spike 2 again',
        ],
    )


def test_an_order_of_one_deliverable_is_one_part_its_jobs_take_by_default(run: Run, demo: str) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "fix   the\nwatch")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix", "--agent", "a")
    data = state.load("demo")
    assert list(data["orders"]["o1"]["parts"]) == ["fix the watch"]
    assert data["jobs"]["j1"]["parts"] == ["fix the watch"]
    assert data["jobs"]["j1"]["session"] == os.environ["CLAUDE_CODE_SESSION_ID"]


def test_setting_parts_keeps_what_a_kept_part_recorded(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: "Thread"
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "two answers", "--parts", "why", "how")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    answer = slack_thread.post(state.now(), from_claude=True)
    assert run("order", "posted", "o1", answer, "--part", "why")[0] == 0
    run("order", "set", "o1", "--parts", "why", "what")
    parts = state.load("demo")["orders"]["o1"]["parts"]
    assert parts == {"why": {"posted": answer, "orphan_said": None}, "what": state.new_part()}


def test_a_post_naming_no_part_of_an_order_of_several_is_a_progress_line_that_keeps_its_promised_time(
    run: Run, demo: str, slack_thread: "Thread", capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "stop the harm, roll back, find the cause", "--thread", "1794200000.000100", "--due", "+10m", "--parts", "stop the harm", "rollback", "cause")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    run("order", "set", "o1", "--due", "+40m")
    promised = state.load("demo")["orders"]["o1"]
    assert promised["due"] != promised["next_due"]
    line = slack_thread.post(state.now() + timedelta(seconds=1), from_claude=True)
    assert run("order", "posted", "o1", line) == (0, f"o1 progress line, no part delivered: {line}")
    order = state.load("demo")["orders"]["o1"]
    assert (order["due"], order["kept"]) == (promised["next_due"], None)
    assert order["last_line"] == state.stamp(slack.sent_at(slack_thread.messages[-1]))
    assert [part["posted"] for part in order["parts"].values()] == [None, None, None]
    assert run("order", "posted", "o1", line, "--part", "rollback")[0] == 0
    marks = {part: kept["posted"] for part, kept in state.load("demo")["orders"]["o1"]["parts"].items()}
    assert marks == {"stop the harm": None, "rollback": line, "cause": None}
    assert cli.main(["order", "posted", "o1", line, "--part", "fix"]) == 2
    assert 'o1 has no part fix; its parts are "stop the harm", "rollback", "cause"' in capsys.readouterr().err


def test_the_communicator_takes_back_a_parts_delivered_mark(run: Run, demo: str, slack_thread: "Thread") -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "stop the harm, roll back", "--thread", "1794200000.000100", "--parts", "stop the harm", "rollback")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    line = slack_thread.post(state.now(), from_claude=True)
    run("order", "posted", "o1", line, "--part", "stop the harm")
    run("order", "posted", "o1", line, "--part", "rollback")
    assert run("order", "posted", "o1", "--undo")[0] == 2
    assert run("order", "posted", "o1", "--part", "stop the harm", "--undo") == (0, 'o1 "stop the harm": not delivered')
    parts = state.load("demo")["orders"]["o1"]["parts"]
    assert (parts["stop the harm"]["posted"], parts["rollback"]["posted"]) == (None, line)


def test_an_answer_counts_as_posted_in_the_thread_of_one_of_the_orders_cards(
    run: Run, demo: str, slack_thread: "Thread"
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "how fast can restore go?", "--thread", "1794100000.000100")
    run("job", "add", "design", "--order", "o1", "--goal", "faster restore")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    answer = slack_thread.post(state.now(), from_claude=True)
    assert run("order", "posted", "o1", answer)[0] == 2
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(None, None, state.stamp(), card="https://acme.slack.com/archives/D123/p1794200000000100"))
    assert run("order", "posted", "o1", answer)[0] == 0
    assert state.load("demo")["orders"]["o1"]["parts"]["how fast can restore go?"]["posted"] == answer


def test_a_line_in_the_thread_of_the_card_of_a_pr_on_the_order_is_in_the_orders_thread(
    run: Run, demo: str, slack_thread: "Thread"
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship web search", "--thread", "1794100000.000100", "--due", "+30m")
    run("job", "add", "builder", "--order", "o1", "--goal", "web search")
    run("pr", "add", PR, "--job", "j1", "--tap")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    root = {"ts": "1794200000.000100", "thread_ts": "1794200000.000100", "from_claude": True}
    slack_thread.messages.append(root | {"text": "New: approve web search? — ✓ *Approve* · <https://github.com/Forge-AI/monorepo/pull/41|PR 41>"})
    line = slack_thread.post(state.now(), from_claude=True)
    assert run("order", "posted", "o1", line)[0] == 2
    slack_thread.messages[0] = root | {"text": f"New: approve web search? — ✓ *Approve* · answered 7:10pm · <{PR}|monorepo PR 42>"}
    assert run("order", "posted", "o1", line) == (0, f'o1 "ship web search": posted {line}')
    order = state.load("demo")["orders"]["o1"]
    assert (order["parts"]["ship web search"]["posted"], order["last_line"]) == (line, state.stamp(slack.sent_at(slack_thread.messages[-1])))


def test_a_part_delivered_in_another_channel_is_recorded_where_it_was_made_and_keeps_no_promised_time(
    run: Run, demo: str, slack_thread: "Thread"
) -> None:
    parts = ["answer why it fails silently", "tell the two stopped people now"]
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "tell the stopped people", "--thread", "1794100000.000100", "--due", "+30m", "--parts", *parts)
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    told = slack_thread.post(state.now(), from_claude=True).replace("/D123/", "/C0SHARE01/")
    before = state.load("demo")["orders"]["o1"]
    assert run("order", "posted", "o1", told)[0] == 2
    assert run("order", "posted", "o1", told, "--part", parts[1]) == (
        0,
        f'o1 "{parts[1]}": posted {told}, outside his threads, so it is no line to him and keeps no promised time',
    )
    after = state.load("demo")["orders"]["o1"]
    assert after == before | {"parts": {parts[0]: state.new_part(), parts[1]: {**state.new_part(), "posted": told}}}


def test_a_post_for_a_report_delivers_the_part_its_record_names_in_any_thread(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: "Thread"
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL errors on plat", "--thread", "1794100000.000100", "--incident", "--parts", "fix", "first check")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    monkeypatch.setattr(cli.reads, "run", lambda argv, checkout: json.dumps({"seq": argv[2], "lane": "root", "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": {"part": "first check", "incident": "true"}}}))
    line = slack_thread.post(state.now(), from_claude=True)
    assert run("order", "posted", "o1", line, "--record", "660611") == (
        0,
        f'o1 "first check": posted {line}, outside his threads, so it is no line to him and keeps no promised time',
    )
    parts = state.load("demo")["orders"]["o1"]["parts"]
    assert (parts["fix"]["posted"], parts["first check"]["posted"]) == (None, line)


def test_a_line_in_the_thread_of_another_alert_on_an_incident_is_refused_naming_the_order_that_joins_it(
    run: Run, demo: str, slack_thread: "Thread", capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL errors on plat", "--thread", "1794100000.000100", "--incident", "--parts", "fix", "first check")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    slack_thread.messages.append({"ts": "1794200000.000100", "thread_ts": "1794200000.000100", "from_user": True})
    line = slack_thread.post(state.now(), from_claude=True)
    assert cli.main(["order", "posted", "o1", line]) == 2
    assert (
        "when his message at the root of that thread is another alert on this incident, that alert is its own order, which the coordinator"
        ' records with drive order add "<his alert>" --from slack:D123/1794200000.000100 --thread 1794200000.000100 --joins o1'
        ' --project "The drive" --workstream "Drive tooling", and this line goes on that order'
    ) in capsys.readouterr().err
    alert = "SandSQL replica lanes unproven on staging"
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", alert, "--from", "slack:D123/1794200000.000100", "--thread", "1794200000.000100", "--joins", "o1")
    assert run("order", "posted", "o2", line) == (0, f'o2 "{state.whole_order(alert)}": posted {line}')


def test_a_line_in_the_thread_of_the_incident_an_alert_joined_keeps_the_alerts_promised_time(
    run: Run, demo: str, slack_thread: "Thread"
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL errors on plat", "--thread", "1794200000.000100", "--incident", "--parts", "fix", "first check")
    alert = "SandSQL replica lanes unproven on staging"
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", alert, "--from", "slack:C0ALERTS1/1794300000.000100", "--thread", "1794300000.000100", "--joins", "o1", "--due", "+30m")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    line = slack_thread.post(state.now() + timedelta(seconds=1), from_claude=True)
    assert run("order", "posted", "o2", line)[0] == 2
    with state.edit("demo") as data:
        state.carry(data, "j1", "o2", list(data["orders"]["o2"]["parts"]))
    assert run("order", "posted", "o2", line) == (0, f'o2 "{state.whole_order(alert)}": posted {line}')
    assert state.load("demo")["orders"]["o2"]["kept"] == slack.sent_at(slack_thread.messages[-1]).isoformat()


def test_only_his_own_message_withdraws_a_promised_line(run: Run, demo: str, slack_thread: "Thread") -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "send the numbers", "--thread", "1794200000.000100", "--due", "+30m")
    ours = slack_thread.post(state.now() - timedelta(minutes=60), from_claude=True)
    his = slack_thread.post(state.now() - timedelta(minutes=59), from_user=True)
    assert run("order", "set", "o1", "--state", "dropped")[0] == 2
    assert run("order", "set", "o1", "--state", "dropped", "--withdrawn", ours)[0] == 2
    assert run("order", "set", "o1", "--state", "dropped", "--withdrawn", his)[0] == 0
    order = state.load("demo")["orders"]["o1"]
    assert (order["state"], order["withdrawn"]) == ("dropped", his)


def test_the_line_a_closing_report_posts_keeps_the_time_owed_even_when_it_was_the_line_that_told_him_that_time(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: "Thread", capsys: pytest.CaptureFixture[str]
) -> None:
    moment = state.now()
    reports = {"21": ("o1", moment - timedelta(seconds=35)), "22": ("o1", moment + timedelta(minutes=2)), "23": ("o2", moment + timedelta(seconds=30))}
    monkeypatch.setattr(
        cli.reads,
        "run",
        lambda argv, checkout: json.dumps({"seq": argv[2], "lane": "root", "at": reports[argv[2]][1].isoformat(), "fields": {"attrs": {"close": reports[argv[2]][0]}}}),
    )
    fix = "ship the background-thread fix"
    for text in ("unfreeze and release", fix):
        run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", text, "--thread", "1794200000.000100")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    run("order", "set", "o1", "--due", "+28m")
    run("order", "set", "o2", "--due", "+28m")
    done = slack_thread.post(moment + timedelta(minutes=1), from_claude=True)
    run("order", "posted", "o1", done)
    told = state.load("demo")["orders"]["o1"]
    assert (told["promised"], state.owed(told)) == (slack.sent_at(slack_thread.messages[-1]).isoformat(), True)
    assert cli.main(["order", "set", "o1", "--state", "done", "--record", "22"]) == 2
    assert (
        f"o1 owes him the line promised for {render.when(told['due'])}, and no drive post is in its thread since"
        f" {render.when(told['promised'], seconds=True)}: post it there first with say(order=o1, record=22)"
    ) in capsys.readouterr().err
    assert run("order", "set", "o1", "--state", "done", "--record", "21")[0] == 0
    closed = state.load("demo")["orders"]["o1"]
    assert (closed["state"], closed["kept"], closed["due"]) == ("done", told["promised"], told["due"])
    due = state.load("demo")["orders"]["o2"]["due"]
    assert run("order", "posted", "o2", done, "--record", "23") == (
        0,
        f'o2 "{state.whole_order(fix)}": posted {done}, its close keeps the line promised for {render.when(due)}',
    )
    kept = state.load("demo")["orders"]["o2"]
    assert (kept["kept"], kept["next_due"], state.owed(kept)) == (told["promised"], None, False)


def test_a_handler_closes_only_an_order_its_report_record_closes(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    attrs = {"10": {"orders": "o1", "thread": "1794200000.000100"}, "11": {"orders": "o1", "close": "o2"}, "12": {"close": "o2 o1"}}
    monkeypatch.setattr(cli.reads, "run", lambda argv, checkout: json.dumps({"seq": argv[2], "lane": "root", "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": attrs[argv[2]]}}))
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "fix the watch")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "fix the handler")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    assert run("order", "set", "o1", "--state", "done")[0] == 2
    assert run("order", "set", "o1", "--state", "done", "--record", "10")[0] == 2
    assert run("order", "set", "o1", "--state", "done", "--record", "11")[0] == 2
    assert state.load("demo")["orders"]["o1"]["state"] == "open"
    assert run("order", "set", "o1", "--state", "done", "--record", "12")[0] == 0
    assert state.load("demo")["orders"]["o1"]["state"] == "done"


def test_the_communicator_drops_an_order_no_record_names_with_a_note_and_closes_one_done_only_by_its_record(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "fix the watch")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    for closing in (["dropped"], ["done", "--note", "he called it off"]):
        assert cli.main(["order", "set", "o1", "--state", *closing]) == 2
        assert "pass --record <that record's seq>, or drop an order no record names with --state dropped --note <why>" in capsys.readouterr().err
    assert run("order", "set", "o1", "--state", "dropped", "--note", "he called it off")[0] == 0
    order = state.load("demo")["orders"]["o1"]
    assert (order["state"], order["note"]) == ("dropped", "he called it off")


def test_only_the_communicator_marks_an_answer_posted(run: Run, demo: str) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "why?")
    assert run("order", "posted", "o1", "https://acme.slack.com/archives/D1/p2")[0] == 2
    assert state.load("demo")["orders"]["o1"]["parts"]["why?"]["posted"] is None


def test_version_names_the_plugin_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    plugin = json.loads((Path(drive.__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json").read_text())
    assert capsys.readouterr().out.strip() == f"drive {plugin['version']}"


def test_the_skill_body_stays_a_short_map_of_pages_drive_manual_prints(run: Run) -> None:
    skill = (cli.PAGES.parent / "SKILL.md").read_text()
    named = {line.split("`")[1] for line in skill.splitlines() if line.startswith("| `")}
    assert named == {path.stem for path in cli.PAGES.glob("*.md")}
    assert len(skill) < 8_000
    code, out = run("manual", "jobs")
    assert (code, out) == (0, (cli.PAGES / "jobs.md").read_text().strip())


def leaf_parsers(parser: argparse.ArgumentParser, words: tuple[str, ...] = ()) -> Iterator[tuple[str, argparse.ArgumentParser]]:
    actions = [action for action in parser._actions if isinstance(action, argparse._SubParsersAction)]
    if not actions:
        yield " ".join(words), parser
        return
    for name, child in actions[0].choices.items():
        yield from leaf_parsers(child, (*words, name))


def flags(parser: argparse.ArgumentParser) -> set[str]:
    return {flag for action in parser._actions for flag in action.option_strings} - UNLISTED


def test_the_verbs_page_names_every_verb_and_flag_exactly_as_the_parser_takes_them(run: Run) -> None:
    leaves, rows = dict(leaf_parsers(cli.parser())), {}
    for line in (cli.PAGES / "verbs.md").read_text().splitlines():
        if line.startswith("| `drive "):
            words = shlex.split(line.split("|")[1].strip().strip("`"))[1:]
            verb = next(" ".join(words[:n]) for n in (2, 1) if " ".join(words[:n]) in leaves)
            rows[verb] = (len(words) - len(verb.split()), line)
    assert set(rows) == set(leaves)
    for verb, (positionals, line) in rows.items():
        parser = leaves[verb]
        assert positionals == sum(not action.option_strings for action in parser._actions), verb
        assert set(FLAG.findall(line)) == flags(parser), verb
        assert [choice for action in parser._actions for choice in map(str, action.choices or ()) if choice not in line] == []
    assert run("manual", "verbs", "job set") == (0, rows["job set"][1])


def refusal(command: str, leaves: dict[str, argparse.ArgumentParser]) -> str | None:
    tokens = command.split()
    verb = next((" ".join(tokens[1:n]) for n in (3, 2) if " ".join(tokens[1:n]) in leaves), None)
    if verb is None:
        named = tokens[1] in {name.split()[0] for name in leaves} and tokens[2:3] in ([], ["X"])
        return None if named else f"`{command}` names no verb drive takes"
    parser, rest = leaves[verb], tokens[len(verb.split()) + 1 :]
    if unknown := set(FLAG.findall(command)) - flags(parser) - UNLISTED:
        return f"`{command}` names {sorted(unknown)}, which drive {verb} does not take"
    takes = {flag: action for action in parser._actions for flag in action.option_strings}
    first = next((action for action in parser._actions if not action.option_strings), None)
    for before, word in zip([None, *rest], rest, strict=False):
        action = takes.get(before) if before else first
        if action and action.choices and word != "X" and not word.startswith("-") and word not in map(str, action.choices):
            return f"`{command}` gives {action.dest} {word!r}, not one of {list(action.choices)}"
    if not any(word.startswith("--") for word in rest) or first and not names_positional(rest, takes):
        return None
    if missing := sorted(action.option_strings[0] for action in parser._actions if action.required and action.option_strings and not set(action.option_strings) & set(rest)):
        return f"`{command}` leaves out {missing}, which drive {verb} requires"
    source = rest[rest.index("--from") + 1] if "--from" in rest[:-1] else ""
    if verb == "order add" and "--due" in rest and "--thread" not in rest and source != "X" and not source.startswith("slack:"):
        return f"`{command}` gives --due with no --thread, and drive order add refuses a promised time with no thread its line goes in"
    return None


def names_positional(rest: list[str], takes: dict[str, argparse.Action]) -> bool:
    flag = None
    for word in rest:
        if word.startswith("--"):
            flag = takes.get(word)
        elif flag is None or flag.nargs == 0:
            return True
        elif flag.nargs not in ("+", "*"):
            flag = None
    return False


def drive_commands(text: str, prose: bool) -> Iterator[str]:
    for span in re.findall(r"\bdrive [a-z].*" if prose else r"`(drive [a-z][^`]*)`", text):
        while inner := re.search(r"\$\((drive [^()]*)\)", span):
            yield from drive_commands(inner.group(1), prose)
            span = f"{span[: inner.start()]}X{span[inner.end() :]}"
        span = PLACEHOLDER.sub(" X ", span)
        for command in re.split(r"(?=\bdrive [a-z])", span) if prose else [span]:
            if command.startswith("drive "):
                yield re.split(r"[,;)]|\. |: ", command)[0] if prose else command


def test_every_drive_command_the_skill_names_parses_with_the_cli() -> None:
    leaves, skill = dict(leaf_parsers(cli.parser())), cli.PAGES.parent
    verbs = {name.split()[0] for name in leaves}
    pages = [skill / "SKILL.md", *cli.PAGES.glob("*.md"), *(skill.parents[1] / "agents").glob("*.md")]
    commands = [command for page in pages for command in drive_commands(page.read_text(), prose=False)]
    rules = " ".join(rule for rules in render.RULES.values() for rule in rules)
    commands += [command for command in drive_commands(rules, prose=True) if command.split()[1] in verbs]
    assert len(commands) > 150
    assert [why for command in commands if (why := refusal(command, leaves))] == []
    for guess in (
        "drive job list j1",
        "drive order set o1 --part x",
        "drive job set j1 --state live",
        "drive job add reader --order X",
        "drive job add --order X design",
        "drive order add X --from migrated: X --due X --project X --workstream X",
    ):
        assert refusal(guess, leaves)
    for mention in ("drive order add X --from slack: X / X --due X --project X --workstream X", "drive order add --due", "drive job add --order X"):
        assert refusal(mention, leaves) is None


def test_drive_manual_prints_only_the_rows_a_word_names(run: Run) -> None:
    code, out = run("manual", "coordinator", "tap")
    assert code == 0
    assert [line.split("|")[1].strip() for line in out.splitlines()] == ["`TAP Approve …`", "`TAP Hold …`"]
    assert len(out) < len((cli.PAGES / "coordinator.md").read_text()) / 10
    assert run("manual", "coordinator", "nothing-names-this")[0] == 2


def test_drive_manual_with_no_page_lists_the_pages(run: Run) -> None:
    pages = sorted(path.stem for path in cli.PAGES.glob("*.md"))
    assert run("manual") == (0, "\n".join(["drive manual <page> prints one of these pages:", *(f"- {page}" for page in pages)]))


DESIGN_CARD = "https://acme.slack.com/archives/D123/p1794300000000100"
TAP_CARD = "https://acme.slack.com/archives/D123/p1794300000000200"
PICK = "https://acme.slack.com/archives/D123/p1794300000000300?thread_ts=1794300000.000100"
WORDS = "Iris proposes with one new tool, guided by each topology's use-when line"
STACKED = "https://github.com/Forge-AI/monorepo/pull/43"


def cards_asking(monkeypatch: pytest.MonkeyPatch, *prs: str) -> list[tuple[str, ...]]:
    question = {"question": "Which topology?", "options": ["Tree", "Ring"], "recommended": "Tree", "asked": state.stamp(), "url": DESIGN_CARD, "card": {"title": "Which topology?", "waits": "demo:j2-7"}}
    tap = {"asked": state.stamp(), "url": TAP_CARD, "prs": list(prs), "card": {"title": "New: land it?", "waits": "merge:Forge-AI:monorepo:42"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [question], "pr_cards": [tap]})
    view.refresh_cards("demo")
    settled: list[tuple[str, ...]] = []
    monkeypatch.setattr(slack, "cc_slack", lambda *args: settled.append(args) or {"answered": True, "label": args[4], "at": "now"})
    return settled


@pytest.fixture
def designing(run: Run, with_pr: str) -> str:
    run("job", "add", "design", "--order", "o1", "--goal", "pick a topology")
    run("job", "set", "j2", "--card", DESIGN_CARD)
    return with_pr


def test_an_answer_stays_unfinished_until_each_card_asking_him_is_marked_and_a_superseded_one_is_settled_in_place(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], closed: list[str]
) -> None:
    settled = cards_asking(monkeypatch, PR)
    assert cli.main(["answer", "a1", "--stands", "demo:j2-7"]) == 2
    assert "run it with --words and --link alone first" in capsys.readouterr().err
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    listed = capsys.readouterr().err
    assert "demo:j2-7" not in listed
    assert f"  {designing}  tap  j1 o1  {PR}  {TAP_CARD}" in listed
    assert cli.main(["answer", "a1", "--stands", "demo:j2-7"]) == 2
    refusal = capsys.readouterr().err
    assert "demo:j2-7: not a card or PR that a1 has not marked" in refusal
    assert refusal.split("no mark:")[1].split("\n")[1].startswith(f"  {designing}  tap")
    code, out = run("answer", "a1", "--superseded", f"{designing}=stop")
    assert out.splitlines() == [
        "his answer a1 recorded: 0 standing, 1 superseded, 0 reopened by it",
        f"{designing}: its card {TAP_CARD} now shows his words and a link to them, with no buttons",
        f"{designing} no longer waits on his tap",
        f"{designing} closed on GitHub",
        "j1 stopped: end its agent, or its lane with drive job end j1",
    ]
    assert (code, settled, closed) == (0, [("card-answer", "--card", TAP_CARD, "--label", WORDS, "--withdrawn", "--via", PICK)], [PR])
    data = state.load("demo")
    assert (data["prs"][designing]["wants_tap"], data["jobs"]["j1"]["state"], data["jobs"]["j2"]["state"]) == (False, "stopped", "running")
    assert data["jobs"]["j1"]["said"] == "superseded by his answer a1"


def test_an_answer_with_no_id_records_his_words_in_cc_notes_first_and_goes_on_by_its_short_id(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cards_asking(monkeypatch, PR)
    added: list[tuple[list[str], str]] = []

    def ccn(argv: list[str], input: str, **_: object) -> subprocess.CompletedProcess[str]:
        added.append((argv, input))
        return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "beb09e5f00d"}), "")

    monkeypatch.setattr("drive.settled.subprocess", types.SimpleNamespace(run=ccn, TimeoutExpired=subprocess.TimeoutExpired))
    assert cli.main(["answer", "--words", WORDS, "--link", PICK]) == 2
    assert "--title" in capsys.readouterr().err
    assert cli.main(["answer", "--title", "Which topology?", "--words", WORDS, "--link", PICK, "--label", "header:topology"]) == 2
    assert "his answer beb09e5 " in capsys.readouterr().err
    labels = ["--label=scope:durable", "--label=drive:demo", "--label=header:topology"]
    assert added == [(["ccn", "answer", "add", "--title=Which topology?", "--body-file", "-", *labels, "--json"], WORDS)]
    assert run("answer", "beb09e5", "--others-stand")[0] == 0


def test_an_answer_with_no_id_records_his_words_in_the_coordinators_checkout_whatever_directory_it_runs_in(
    designing: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cards_asking(monkeypatch, PR)
    cwds: list[str] = []

    def ccn(argv: list[str], input: str, cwd: str, **_: object) -> subprocess.CompletedProcess[str]:
        cwds.append(cwd)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "beb09e5f00d"}), "")

    monkeypatch.setattr("drive.settled.subprocess", types.SimpleNamespace(run=ccn, TimeoutExpired=subprocess.TimeoutExpired))
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    cli.main(["answer", "--title", "Which topology?", "--words", WORDS, "--link", PICK])
    checkout = state.load("demo")["roles"]["coordinator"]["cwd"]
    assert checkout != os.getcwd()
    assert cwds == [checkout]


def test_an_answer_lists_every_pr_of_its_order_not_yet_live_and_a_reopened_one_goes_to_the_live_job_that_reworks_it(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settled = cards_asking(monkeypatch, PR)
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/44", "--job", "j1")
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/45", "--job", "j2")
    run("job", "add", "builder", "--order", "o1", "--goal", "shipped")
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/46", "--job", "j3")
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#45"]["state"] = data["prs"]["Forge-AI/monorepo#46"]["state"] = "merged"
        data["jobs"]["j3"]["state"] = "done"
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    listed = capsys.readouterr().err
    assert "; 3 PRs have no mark:" in listed
    assert "  Forge-AI/monorepo#44  open  j1 o1  " in listed
    assert "  Forge-AI/monorepo#45  merged  j2 o1  " in listed
    assert "#46" not in listed
    assert run("answer", "a1", "--reopened", "demo:j2-7=j1")[0] == 2
    assert run("answer", "a1", "--reopened", "Forge-AI/monorepo#45=j3")[0] == 2
    code, out = run("answer", "a1", "--reopened", f"{designing}=j1", "Forge-AI/monorepo#45=j1", "--others-stand")
    assert out.splitlines() == [
        "his answer a1 recorded: 1 standing, 0 superseded, 2 reopened by it",
        f"{designing}: its card {TAP_CARD} now shows his words and a link to them, with no buttons",
        f"{designing} no longer waits on his tap until j1 reworks it",
        f"{designing} reopened by his answer a1: j1 reworks it to his words, so brief j1 with them",
        "Forge-AI/monorepo#45 reopened by his answer a1: j1 reworks it to his words, so brief j1 with them",
    ]
    assert (code, settled) == (0, [("card-answer", "--card", TAP_CARD, "--label", WORDS, "--withdrawn", "--via", PICK)])
    data = state.load("demo")
    assert (data["prs"][designing]["wants_tap"], data["jobs"]["j1"]["state"]) == (False, "running")
    assert data["answers"]["a1"]["cards"]["Forge-AI/monorepo#45"] == {"mark": "reopened", "job": "j1", "card": None}


def test_an_answer_under_a_card_lists_only_its_job_and_orders_cards_and_prs_and_those_its_words_name(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cards_asking(monkeypatch, PR)
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "the new tool")
    run("job", "add", "builder", "--order", "o2", "--goal", "build the tool")
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/44", "--job", "j3")
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/45", "--job", "j3")
    words = f"{WORDS}; hold https://github.com/Forge-AI/monorepo/pull/45 until it lands"
    assert cli.main(["answer", "a1", "--words", words, "--link", PICK]) == 2
    listed = capsys.readouterr().err
    assert "; 2 PRs have no mark:" in listed
    assert f"  {designing}  tap  j1 o1  {PR}  {TAP_CARD}" in listed
    assert "  Forge-AI/monorepo#45  open  j3 o2  " in listed
    assert "#44" not in listed
    marked = run("answer", "a1", "--stands", designing, "Forge-AI/monorepo#45", "Forge-AI/monorepo#44")
    assert marked == (0, "his answer a1 recorded: 3 standing, 0 superseded, 0 reopened by it")
    assert state.load("demo")["answers"]["a1"]["cards"]["Forge-AI/monorepo#44"] == {"mark": "stands"}


def test_an_answer_stays_unfinished_until_each_job_waiting_on_an_order_it_touches_is_sent_his_words_or_marked_untouched(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cards_asking(monkeypatch, PR)
    run("job", "add", "builder", "--order", "o1", "--goal", "build the store")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "the new tool")
    run("job", "add", "builder", "--order", "o2", "--goal", "build the tool")
    with state.edit("demo") as data:
        data["jobs"]["j3"]["state"] = data["jobs"]["j4"]["state"] = "waiting"
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    listed = capsys.readouterr().err
    assert "  j3 waits on o1 and has not heard a1: SendMessage it his words verbatim, or mark it untouched" in listed
    assert "j4" not in listed
    assert cli.main(["answer", "a1", "--stands", designing]) == 2
    refusal = capsys.readouterr().err
    assert "stays unfinished until each job waiting on an order it touches has heard it:" in refusal
    assert f"  {designing}  tap" not in refusal
    assert "mark each: drive answer a1 --sent <job>... once SendMessage gave it his words verbatim" in refusal
    assert cli.main(["answer", "a1", "--sent", "j4"]) == 2
    assert "j4: not a job waiting on an order a1 touches that is unmarked" in capsys.readouterr().err
    assert run("answer", "a1", "--sent", "j3") == (
        0,
        "his answer a1 recorded: 1 standing, 0 superseded, 0 reopened by it; 1 waiting job sent his words, 0 untouched",
    )
    assert state.load("demo")["answers"]["a1"]["jobs"] == {"j3": {"mark": "sent"}}


def test_an_answer_under_a_card_no_longer_open_still_waits_on_each_job_waiting_on_that_cards_order(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": []})
    view.refresh_cards("demo")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the store")
    with state.edit("demo") as data:
        data["jobs"]["j3"]["state"] = "waiting"
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    listed = capsys.readouterr().err
    assert f"  {designing}  open  j1 o1  " in listed
    assert "  j3 waits on o1 and has not heard a1: SendMessage it his words verbatim, or mark it untouched" in listed
    assert run("answer", "a1", "--stands", designing, "--sent", "j3") == (
        0,
        "his answer a1 recorded: 1 standing, 0 superseded, 0 reopened by it; 1 waiting job sent his words, 0 untouched",
    )


def test_others_stand_refuses_to_mark_more_than_a_few_unread_cards_and_prs_standing_so_each_is_marked_by_its_key(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cards_asking(monkeypatch, PR)
    for number in (44, 45, 46):
        run("pr", "add", f"https://github.com/Forge-AI/monorepo/pull/{number}", "--job", "j1")
    assert run("answer", "a1", "--words", WORDS, "--link", PICK)[0] == 2
    assert cli.main(["answer", "a1", "--others-stand"]) == 2
    refusal = capsys.readouterr().err
    assert "--others-stand would mark 4 PRs standing at once, unread; it marks at most 3." in refusal
    assert "mark each by its key: --stands <key>..." in refusal
    assert "  Forge-AI/monorepo#46  open  j1 o1  " in refusal
    assert set(state.load("demo")["answers"]["a1"]["cards"].values()) == {None}
    marked = run("answer", "a1", "--stands", "Forge-AI/monorepo#44", "Forge-AI/monorepo#45", "--others-stand")
    assert marked == (0, "his answer a1 recorded: 4 standing, 0 superseded, 0 reopened by it")


def test_superseding_an_open_pr_outright_closes_it_on_github_and_leaves_a_merged_one_as_it_is(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, closed: list[str]
) -> None:
    cards_asking(monkeypatch, PR)
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/44", "--job", "j1")
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#44"]["state"] = "merged"
    assert run("answer", "a1", "--words", WORDS, "--link", PICK)[0] == 2
    code, out = run("answer", "a1", "--superseded", f"{designing}=stop", "Forge-AI/monorepo#44=stop")
    assert code == 0
    assert f"{designing} closed on GitHub" in out.splitlines()
    assert "Forge-AI/monorepo#44 closed" not in out
    assert closed == [PR]
    assert [pr["state"] for pr in state.load("demo")["prs"].values()] == ["closed", "merged"]


def test_a_superseded_card_folds_its_job_into_a_live_one_and_the_card_he_tapped_needs_no_mark(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cards_asking(monkeypatch, PR)
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "the new tool")
    run("job", "add", "builder", "--order", "o2", "--goal", "build the tool")
    assert run("answer", "a1", "--words", WORDS, "--link", TAP_CARD)[0] == 2
    assert list(state.load("demo")["answers"]["a1"]["cards"]) == ["demo:j2-7"]
    assert run("answer", "a1", "--superseded", "demo:j2-7=j2")[0] == 2
    assert run("answer", "a1", "--superseded", "demo:j2-7=j9")[0] == 2
    assert state.load("demo")["jobs"]["j2"]["state"] == "running"
    assert run("answer", "a1", "--superseded", "demo:j2-7=j3")[0] == 0
    data = state.load("demo")
    assert (data["jobs"]["j2"]["state"], data["jobs"]["j3"]["carries"]) == ("stopped", {"o1": ["land it"]})
    assert "j3" in data["orders"]["o1"]["jobs"]


def test_superseding_a_card_he_already_answered_refuses_and_stops_no_job(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cards_asking(monkeypatch, PR)
    monkeypatch.setattr(slack, "cc_slack", lambda *args: {"answered": False, "already_answered": True, "label": "Tree", "at": "now"})
    assert run("answer", "a1", "--words", WORDS, "--link", TAP_CARD)[0] == 2
    assert cli.main(["answer", "a1", "--superseded", "demo:j2-7=stop"]) == 2
    assert f"{DESIGN_CARD} already shows his answer 'Tree'" in capsys.readouterr().err
    data = state.load("demo")
    assert (data["answers"]["a1"]["cards"]["demo:j2-7"], data["jobs"]["j2"]["state"]) == (None, "running")


def test_superseding_one_pr_of_a_stack_card_refuses_until_the_whole_stack_is_marked_and_settles_it_once(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("pr", "add", STACKED, "--job", "j1", "--tap")
    settled = cards_asking(monkeypatch, PR, STACKED)
    assert run("answer", "a1", "--words", WORDS, "--link", PICK)[0] == 2
    assert run("answer", "a1", "--superseded", f"{designing}=stop")[0] == 2
    assert settled == []
    assert run("answer", "a1", "--superseded", f"{designing}=stop", "Forge-AI/monorepo#43=stop", "--others-stand")[0] == 0
    assert len(settled) == 1
    assert [pr["wants_tap"] for pr in state.load("demo")["prs"].values()] == [False, False]
