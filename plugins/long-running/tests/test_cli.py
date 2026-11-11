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
from conftest import CARD_BODY, RESEARCH, SHORT_ASK, Answers, Drawing, Thread, WatchProcess, Withdrawals
from drive import (
    card,
    cli,
    github,
    limits,
    orca,
    picture,
    place,
    render,
    settled,
    slack,
    state,
    tools,
    view,
)

Run = Callable[..., tuple[int, str]]
UNLISTED = {"-h", "--help", "--drive", "--json"}
FLAG = re.compile(r"(?<![\w-])--[a-z][\w-]*")
PLACEHOLDER = re.compile(r"<[^<>`]*>|\"[^\"]*\"|…|\[|\]|\.\.\.")
PR = "https://github.com/Forge-AI/monorepo/pull/42"
REPORTED = "https://acme.slack.com/archives/C0REPORTS01/p1794400100000200"
ROOT_CAUSE = "Root cause: none: where a copy runs is a pick between two designs, with no defect behind it\n"
DESIGN_RESEARCH = RESEARCH.replace("DONE researched the store copy", "DESIGN where the copy runs") + (
    "- on the primary, beside the queries it serves (recommended)\n- on a separate worker that reads the bucket\n" + ROOT_CAUSE
)
DESIGNED = """DESIGN the store copy
Design: one copy per store, in the order the stores list
Settled: "copy one store"
Native: none: he never sees it
Fork: none: one layout fits
Facts: lib/drive/state.py:18
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


def test_a_sub_coordinator_binds_beside_the_top_for_a_project_an_order_is_filed_under_and_a_new_session_replaces_it(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess
) -> None:
    run("order", "add", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    watch_process.ensured.clear()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    code, out = run("start", "demo", "--role", "coordinator", "--project", "Iris")
    lines = out.splitlines()
    assert code == 0
    assert lines[0].startswith("You are the sub-coordinator for project Iris of drive demo: you run his orders filed under Iris,")
    assert "The top coordinator: session session-under-test. The communicator: not started." in lines[0]
    assert lines[1:4] == [
        "Your cci lane is iris: post with --lane iris; the watch for Iris and the communicator's records reach you at iris@demo.",
        "Hold no Slack channel: the top coordinator holds the drive's.",
        f"Subscribe: cci drive use demo, then cci subscribe --reader iris --cursor coordinator-iris {render.COORDINATOR_KINDS}",
    ]
    assert lines[-2:] == ["the watch runs for demo", "sub-coordinator for Iris: session iris-1, in no Orca terminal"]
    assert watch_process.ensured == ["demo --project Iris"]
    assert run("context")[1].splitlines()[:4] == lines[:4]
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-2")
    assert run("start", "demo", "--role", "coordinator", "--project", "Iris")[0] == 0
    data = state.load("demo")
    assert data["roles"]["coordinator"]["session"] == "session-under-test"
    assert {project: held["session"] for project, held in data["subcoordinators"].items()} == {"Iris": "iris-2"}
    assert state.session_project(data, "iris-1") is None


def test_start_refuses_a_project_to_the_communicator_and_one_no_order_is_filed_under(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    assert cli.main(["start", "demo", "--role", "communicator", "--project", "Iris"]) == 2
    assert capsys.readouterr().err.strip() == "drive: --project binds a sub-coordinator, which holds the coordinator role; the communicator holds no project"
    assert cli.main(["start", "demo", "--role", "coordinator", "--project", "Platform"]) == 2
    assert capsys.readouterr().err.strip() == "drive: --project Platform: no order is filed under it; the projects in use: Iris"
    data = state.load("demo")
    assert (data["subcoordinators"], "communicator" in data["roles"]) == ({}, False)


TAKEN = "file its orders under a project whose lane no one else takes"


def test_start_refuses_a_project_whose_lane_another_sub_coordinator_the_drive_its_records_or_a_numbered_name_takes(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], posted_lanes: set[str]
) -> None:
    for project in ("A/B", "A B", "Root", "Iris 2", "Handler", "Memory"):
        run("order", "add", "--project", project, "--workstream", "W", f"work for {project}")
    posted_lanes.add("memory")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "ab-1")
    assert run("start", "demo", "--role", "coordinator", "--project", "A/B")[0] == 0
    refusals = {}
    for number, project in enumerate(("A B", "Root", "Iris 2", "Handler", "Memory")):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", f"other-{number}")
        assert cli.main(["start", "demo", "--role", "coordinator", "--project", project]) == 2
        refusals[project] = capsys.readouterr().err.strip()
    assert refusals == {
        "A B": f"drive: --project A B: its lane a-b is the lane of the sub-coordinator for A/B; {TAKEN}",
        "Root": f"drive: --project Root: its lane root is the drive's own root lane; {TAKEN}",
        "Iris 2": f"drive: --project Iris 2: its lane iris-2 ends in -<digits>, which cci reads as a numbered copy of lane iris; {TAKEN}",
        "Handler": f"drive: --project Handler: its lane handler is the drive's own handler lane; {TAKEN}",
        "Memory": f"drive: --project Memory: its lane memory is a lane cci records of drive demo were posted as; {TAKEN}",
    }
    first = state.load("demo")["subcoordinators"]["A/B"]["from"]
    run("order", "add", "--project", "A/B", "--workstream", "W", "more work")
    posted_lanes.add("a-b")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "ab-2")
    assert run("start", "demo", "--role", "coordinator", "--project", "A/B")[0] == 0
    bound = state.load("demo")["subcoordinators"]
    assert ({project: held["session"] for project, held in bound.items()}, bound["A/B"]["from"]) == ({"A/B": "ab-2"}, first)


def test_a_session_holding_a_role_binds_no_sub_coordinator_and_a_sub_coordinators_session_takes_no_role(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("order", "add", "--project", "Platform", "--workstream", "Alerts", "fix the alert")
    assert cli.main(["start", "demo", "--role", "coordinator", "--project", "Iris"]) == 2
    assert capsys.readouterr().err.strip() == (
        "drive: --project Iris: session session-under-test holds the top coordinator role of drive demo; a sub-coordinator is a session"
        " of its own, so bind it from another session"
    )
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    assert run("start", "demo", "--role", "coordinator", "--project", "Iris")[0] == 0
    assert cli.main(["start", "demo", "--role", "coordinator", "--project", "Platform"]) == 2
    assert capsys.readouterr().err.strip() == "drive: --project Platform: session iris-1 is the sub-coordinator for Iris of drive demo; bind Platform's from another session"
    assert cli.main(["start", "demo", "--role", "communicator"]) == 2
    assert capsys.readouterr().err.strip() == (
        "drive: --role communicator: session iris-1 is the sub-coordinator for Iris in drive demo; the top coordinator, the communicator and"
        " the meta session are sessions of their own, so start this role from another session"
    )
    data = state.load("demo")
    assert (list(data["subcoordinators"]), data["roles"]["coordinator"]["session"], "communicator" in data["roles"]) == (["Iris"], "session-under-test", False)


def test_heard_returns_one_word_whatever_sub_coordinator_his_thread_is_filed_under(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", "demo", "--role", "coordinator", "--project", "Iris")
    assert run("heard") == (0, "heard")
    assert tools.run("heard", "demo", {}) == "heard"
    assert tools.run("heard", "demo", {"away": True}) == "away"
    with pytest.raises(state.DriveError, match="drive heard takes no thread"):
        tools.run("heard", "demo", {"thread": "1794200000.000100"})


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


def test_the_meta_session_takes_the_communicators_tooling_orders_and_holds_each_new_one_but_no_product_order(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a verb for owners")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "communicator-session")
    run("start", "demo", "--role", "communicator")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "--kind", "answer", "what does the watch hold")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "meta-session")
    assert cli.main(["start", "demo", "--role", "meta", "--project", "The drive"]) == 2
    assert "the meta session holds no project" in capsys.readouterr().err
    code, out = run("start", "demo", "--role", "meta")
    assert code == 0
    assert out.startswith("You are the meta session of drive demo: you hold drive tooling")
    assert "the meta session holds o1 now, the drive tooling orders the communicator held\nmeta: session meta-session" in out
    assert [order["owner"] for order in state.load("demo")["orders"].values()] == ["meta", "communicator"]
    assert cli.main(["order", "add", "--project", "Iris", "--workstream", "Run page", "ship the run page"]) == 2
    assert 'the meta session adds only "Drive tooling:" orders and never touches his product orders' in capsys.readouterr().err
    assert run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: cards say why") == (0, "o3")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-under-test")
    assert run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: the watch says why") == (0, "o4")
    assert cli.main(["order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: another", "--owner", "communicator"]) == 2
    assert "a \"Drive tooling:\" order is the meta session's" in capsys.readouterr().err
    assert [order["owner"] for order in state.load("demo")["orders"].values()] == ["meta", "communicator", "meta", "meta"]
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "communicator-session")
    assert cli.main(["job", "add", "reader", "--order", "o1", "--goal", "read it"]) == 2
    assert "o1 is the meta session's order, session meta-session" in capsys.readouterr().err
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "meta-session")
    assert run("job", "add", "reader", "--order", "o1", "--goal", "research: a verb for owners")[0] == 0


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


def test_status_and_context_print_who_holds_each_channel(run: Run) -> None:
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


def test_status_lists_each_sub_coordinator_with_its_lane_session_place_and_whether_its_watch_runs(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "fix the watch")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", "demo", "--role", "coordinator", "--project", "Iris")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "drive-1")
    run("start", "demo", "--role", "coordinator", "--project", "The drive")
    state.pid_path("demo", "Iris").write_text(f"{os.getpid()}\n")
    assert run("status")[1].splitlines()[3:6] == [
        "meta: not started; Hold no Slack channel.",
        f"sub-coordinator Iris: lane iris, session iris-1, in no Orca terminal; its watch runs as pid {os.getpid()}",
        "sub-coordinator The drive: lane the-drive, session drive-1, in no Orca terminal; its watch is not running; drive watch --project 'The drive' from its session starts it",
    ]
    listed = json.loads(run("status", "--json")[1])["subcoordinators"]
    assert {project: (held["lane"], held["session"], held["watch"]) for project, held in listed.items()} == {
        "Iris": ("iris", "iris-1", os.getpid()),
        "The drive": ("the-drive", "drive-1", None),
    }


def test_an_orphaned_terminal_gets_the_repository_setting_and_a_shown_one_the_switch_command(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.delenv("TMUX")
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
    assert reads[:2] == [
        ("--channel", "D123", "--thread", "1794200050.000200", "--limit", "1"),
        ("--channel", "D123", "--thread", "1794200090.000700", "--limit", "1"),
    ]


def test_an_order_from_his_reply_goes_in_the_thread_his_reply_is_in_in_the_channel_that_thread_is_in(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, reply = "1794388517.958349", "1794388585.312939"
    bug_root, bug_reply = "1794341529.595149", "1794341600.000200"
    messages = {
        ("D123", reply): [{"ts": reply, "thread_ts": root, "from_user": True}],
        ("C0BUGS1", bug_reply): [{"ts": bug_reply, "thread_ts": bug_root, "from_user": True}],
    }
    reads: list[tuple[str, ...]] = []

    def thread(*where: str) -> list[dict]:
        reads.append(where)
        if (where[1], where[3]) not in messages:
            raise slack.SlackError("cc-slack can't read this conversation (channel_not_found)")
        return messages[where[1], where[3]]

    monkeypatch.setattr(slack, "thread", thread)
    filed = ["order", "add", "--project", "The drive", "--workstream", "Drive tooling"]
    assert run(*filed, "What the hell is this message?", "--from", f"slack:D123/{reply}") == (0, "o1")
    assert run(*filed, "the card still shows the old state", "--from", f"slack:C0BUGS1/{bug_reply}") == (0, "o2")
    assert run(*filed, "and the dashboard too", "--from", f"slack:C0BUGS1/{bug_reply}", "--thread", bug_root) == (0, "o3")
    assert reads == [
        ("--channel", "D123", "--thread", reply, "--limit", "1"),
        ("--channel", "C0BUGS1", "--thread", bug_reply, "--limit", "1"),
        ("--channel", "C0BUGS1", "--thread", bug_reply, "--limit", "1"),
    ]
    data = state.load("demo")
    assert {order_id: (order["thread"], order["from_thread"], state.promise_thread(data, order)) for order_id, order in data["orders"].items()} == {
        "o1": (root, root, ("D123", root)),
        "o2": (bug_root, bug_root, ("C0BUGS1", bug_root)),
        "o3": (bug_root, bug_root, ("C0BUGS1", bug_root)),
    }
    assert cli.thread_refusal(data, "o1", root) is None
    assert cli.main([*filed, "look at this", "--from", "slack:C0GONE1/1794341700.000300"]) == 2
    assert (
        "--from slack:C0GONE1/1794341700.000300: refused, Slack cannot read the message it names: cc-slack can't read this conversation (channel_not_found)"
        in capsys.readouterr().err
    )
    assert set(state.load("demo")["orders"]) == {"o1", "o2", "o3"}


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
    monkeypatch.setattr(slack, "order_posts", lambda data, order_id, order: pytest.fail("closing an order with no thread reads none"))
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


def test_job_ask_check_says_ready_or_every_fix_its_words_and_card_need_with_no_drive_and_keeps_nothing(
    state_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], drawing: Drawing
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    assert cli.main(["job", "ask", "--check", str(block)]) == 0
    assert capsys.readouterr().out == "ready\n"
    drawing.refusal = 'card body points at an option by number or position with "second option"'
    assert cli.main(["job", "ask", "--check", str(block)]) == 2
    assert f"cc-slack would not draw it: {drawing.refusal}" in capsys.readouterr().err
    assert [card["waits"] for card in drawing.cards] == ["check:ask-0", "check:ask-0"]
    monkeypatch.setattr("sys.stdin", io.StringIO(FINDINGS_FIRST))
    assert cli.main(["job", "ask", "--check", "-"]) == 2
    refusal = capsys.readouterr().err
    assert "the summary is 0 lines" in refusal
    assert "0 options; an ASK has 2 to 4" in refusal
    assert not state_root.exists()
    assert cli.main(["job", "ask"]) == 2
    assert "drive job ask <j> <file>; drive job ask --check <file> only checks its shape" in capsys.readouterr().err


def test_job_ask_check_refuses_a_summary_answering_none_of_his_next_questions_unless_its_findings_say_none_follows(
    state_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    bare = "ASK Which way should this go?\nTwo ways to copy.\n- server-side copy (recommended)\n- copy in batches\nFindings:\nRoot cause: none: a design pick\nKept on disk.\n"
    block.write_text(bare)
    assert cli.main(["job", "ask", "--check", str(block)]) == 2
    assert (
        "the summary answers none of his next questions: why, did he already decide this, is it a port, what comes after, or why"
        ' it is held; add the one he would ask, as "held until you pick, so nothing ships meanwhile" does, or write'
        " `Next question: none: <why>` under `Findings:`"
    ) in capsys.readouterr().err
    block.write_text(bare.replace("Two ways to copy.", "Two ways to copy; the copy is held until you pick one."))
    assert cli.main(["job", "ask", "--check", str(block)]) == 0
    block.write_text(bare.replace("Two ways to copy.", "Two ways to copy; copying is paused pending your approval."))
    assert cli.main(["job", "ask", "--check", str(block)]) == 0
    block.write_text(bare.replace("Findings:\n", "Findings:\nNext question: none: a pick with nothing held on it\n"))
    assert cli.main(["job", "ask", "--check", str(block)]) == 0
    assert limits.question(bare, kept=True).summary == ["Two ways to copy."]


def test_term_keeps_a_word_he_corrected_beside_its_proper_term_and_a_drive_saved_before_it_loads_with_none(run: Run, demo: str) -> None:
    assert state.load(demo)["stand_ins"] == {}
    assert run("term", "backup", "S3 replica") == (0, 'say\'s hook finds "backup" from now on, naming "S3 replica", for Jev to judge against his words')
    assert state.load(demo)["stand_ins"] == {"backup": "S3 replica"}
    saved = json.loads(state.state_path(demo).read_text())
    del saved["stand_ins"]
    state.state_path(demo).write_text(json.dumps(saved))
    assert state.load(demo)["stand_ins"] == {}


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
        "ASK How do leases stop leaking?\nA lease outlives the run that took it, so the next run waits on it.\n"
        "- reclaim orphaned leases every 10 minutes (recommended)\n- one owner holds the lease until it ends\n"
        "Findings:\nRoot cause: none: a design pick\nTwo runs held one lease.\n"
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
Root cause: none: two landed designs of one button, a pick between them with no defect behind it
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


def test_job_ask_starts_from_the_root_cause_and_recommends_the_option_that_removes_it(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    picked = "Root cause: none: two landed designs of one button, a pick between them with no defect behind it"
    caused = TEAMMATE_FIX_SHOWN.replace(picked, "Root cause: the button and our status line read two download states\nFirst principles: Add our status line beside their always-on button")
    block.write_text(caused)
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert '"Add our status line beside their always-on button" removes the root cause and is not the recommended option' in capsys.readouterr().err
    block.write_text(TEAMMATE_FIX_SHOWN.replace(picked, "Root cause: the button reads no download state\nFirst principles: Keep theirs, close ours"))
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


def test_job_ask_keeps_an_option_that_lands_first_and_backfills_after(
    run: Run, asking: str, tmp_path: Path
) -> None:
    block = tmp_path / "ask.md"
    landing = "- Land the snapshot writer now, then backfill old runs from a pass box: opens in 3 requests"
    block.write_text(SHORT_ASK.replace("- A map file beside each journal file", f"{landing}\n- A map file beside each journal file"))
    assert run("job", "ask", asking, str(block))[0] == 0


LANDING_ASK = """ASK Land your run history change so the run page change you approved can land?
<https://github.com/acme/app/pull/12|The run page change> is built on <https://github.com/acme/app/pull/11|your run history change>, which is green and approved but has no tap of yours.
I recommend landing both together, run history first; meanwhile both stay open.
- Land both now, run history first (recommended)
- Move the run page change onto dev without run history, and leave that one with you

Findings:
Root cause: none: landing two PRs, with no defect behind it
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
Root cause: none: a design choice, with no defect behind it
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


def test_job_card_refuses_an_ask_saying_a_pr_waits_on_his_tap_while_he_has_nothing_to_tap(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    url = "https://github.com/acme/app/pull/42"
    with state.edit("demo") as data:
        data["prs"]["acme/app#42"] = state.new_pr(url, "j1", "o1") | {"head": "abc123", "tap": {"label": "Land it", "at": state.stamp(), "head": "abc123", "source": "slack"}}
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK.replace("Meanwhile nothing is built on either layout.", f"Meanwhile <{url}|the layout change> waits on your tap."))
    assert cli.main(["job", "card", str(block), "--waits", "demo:j1-7"]) == 2
    assert "acme/app#42: he tapped Land it at" in capsys.readouterr().err
    with state.edit("demo") as data:
        data["prs"]["acme/app#42"]["tap"] = None
    assert cli.main(["job", "card", str(block), "--waits", "demo:j1-7"]) == 2
    assert "acme/app#42: no open tap card of his stands at its head" in capsys.readouterr().err
    link = "https://acme.slack.com/archives/D123/p1794300000000400"
    tap = {"asked": state.stamp(), "url": link, "prs": [url], "card": {"title": "New: land it?", "prs": [{"url": url, "head": "abc123"}]}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [tap]})
    view.refresh_cards("demo")
    assert cli.main(["job", "card", str(block), "--waits", "demo:j1-7"]) == 2
    assert f"acme/app#42: the words do not link its open tap card {link}" in capsys.readouterr().err
    block.write_text(SHORT_ASK.replace("Meanwhile nothing is built on either layout.", f"Meanwhile <{url}|the layout change> waits on your tap: <{link}|its tap card>."))
    assert run("job", "card", str(block), "--waits", "demo:j1-7")[0] == 0


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


def in_place(printed: str) -> str:
    return json.dumps(json.loads(printed) | {"in_place": True})


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
    assert out.splitlines() == [*map(in_place, printed), in_place(json.dumps(unasked))]
    assert "demo:hier-2: the ASK is out of shape" in err and "0 options; an ASK has" in err
    assert (state_root / "demo" / "asks" / "hier-1.md").is_file()
    assert state.load("demo")["jobs"] == jobs
    assert run("cards", "redraw", "--waits", "demo:j1-1") == (0, in_place(printed[1]))
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
    assert run("cards", "redraw") == (0, in_place(printed))
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
    assert run("cards", "redraw", "--waits", "demo:j1-8") == (0, in_place(printed))
    rebuilt = state_root / "demo" / "asks" / "j1-8.md"
    assert limits.redrawn(rebuilt.read_text()).title == json.loads(printed)["title"]
    assert [(question["file"], question["waits"], question["card"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [
        (str(rebuilt), "demo:j1-8", shown["url"])
    ]
    assert run("cards", "redraw", "--waits", "demo:j1-8") == (0, in_place(printed))


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


def test_a_question_asked_again_in_the_same_words_redraws_its_one_card_under_the_waits_id_of_its_first_ask(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": []})
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1]
    run("job", "card", first, "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    asked = state.load("demo")["jobs"]["j1"]["questions"][0]["asked"]
    for seq, requests in ((9, "3 calls"), (11, "2 calls")):
        monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("opens in 3 requests", f"opens in {requests}")))
        again = run("job", "ask", asking, "-", "--json")[1]
        assert json.loads(again)["waits"] == "demo:j1-7"
        card = json.loads(run("job", "card", json.loads(again)["question"], "--waits", f"demo:j1-{seq}")[1])
        assert card["waits"] == "demo:j1-7"
        assert f"opens in {requests}" in card["body"]
    questions = state.load("demo")["jobs"]["j1"]["questions"]
    assert [(question["file"], question["waits"], question["card"], question["asked"]) for question in questions] == [
        (json.loads(again)["question"], "demo:j1-7", link, asked)
    ]


def test_a_question_asked_twice_before_its_card_is_drawn_draws_its_newest_block_on_one_card(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    kept = []
    for text in (SHORT_ASK, SHORT_ASK.replace("opens in 3 requests", "opens in 3 calls")):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        kept.append(run("job", "ask", asking, "-")[1])
    first = json.loads(run("job", "card", kept[0], "--waits", "demo:j1-7")[1])
    second = json.loads(run("job", "card", kept[1], "--waits", "demo:j1-8")[1])
    assert (first["waits"], second["waits"]) == ("demo:j1-7", "demo:j1-7")
    assert first == second and "opens in 3 calls" in first["body"]
    assert [(question["file"], question["waits"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [(kept[1], "demo:j1-7")]


def test_a_question_asked_again_after_he_answered_its_card_goes_on_a_card_of_its_own(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    listed = [shown]
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": list(listed), "pr_cards": []})
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1]
    run("job", "card", first, "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    listed.clear()
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("opens in 3 requests", "opens in 3 calls")))
    again = run("job", "ask", asking, "-")[1]
    assert json.loads(run("job", "card", again, "--waits", "demo:j1-9")[1])["waits"] == "demo:j1-9"
    assert [(question["file"], question["waits"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [
        (first, "demo:j1-7"),
        (again, "demo:j1-9"),
    ]


def test_job_ask_drop_throws_away_a_near_copy_kept_with_no_card_so_nothing_counts_or_sends_it(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": []})
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1]
    run("job", "card", first, "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    near_copy = "ASK How should a finished run's files be laid out so its page opens with no guessing?"
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace(SHORT_ASK.split("\n")[0], near_copy)))
    copy = run("job", "ask", asking, "-")[1]
    assert f"no card drawn yet, kept in {copy}" in run("status")[1]
    assert cli.main(["job", "ask", asking, "--drop", first]) == 2
    assert f"kept in {first} is on his card {link}, which only his answer closes" in capsys.readouterr().err
    code, out = run("job", "ask", asking, "--drop", copy)
    assert code == 0 and out.startswith('j1 no longer asks "How should') and f"kept in {copy} is dropped and the file deleted" in out
    job = state.load("demo")["jobs"]["j1"]
    assert [(question["file"], question["waits"], question["card"]) for question in job["questions"]] == [(first, "demo:j1-7", link)]
    assert job["said"] == SHORT_ASK.split("\n")[0].removeprefix("ASK ")
    assert not Path(copy).exists()
    status = run("status")[1]
    assert copy not in status and f"card {link} (demo:j1-7)" in status
    assert settled.ask_refusal(["demo"], {"ask": copy, "job": "j1"}, near_copy).startswith(f"refused: {copy} keeps no question of j1")
    assert json.loads(run("job", "card", asking)[1])["title"] == job["said"]


def test_job_ask_refuses_a_question_another_jobs_open_card_asks_naming_the_wait_on_that_card(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": []})
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1]
    run("job", "card", first, "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    run("job", "add", "builder", "--order", "o1", "--goal", "build the run page")
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("opens in 3 requests", "opens in 3 calls")))
    assert cli.main(["job", "ask", "j2", "-"]) == 2
    refusal = capsys.readouterr().err
    assert f"j2: refused, nothing kept: j1 already asks him \"How should a finished run's files be laid out so its page opens without guessing?\" on its open card {link} (demo:j1-7)" in refusal
    assert "drive job set j2 --waits demo:j1-7, which frees j2 at his answer" in refusal
    assert state.load("demo")["jobs"]["j2"]["questions"] == []


def test_his_words_under_a_card_answer_it_so_its_job_never_redraws_it_with_a_new_question(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": []})
    view.refresh_cards("demo")
    monkeypatch.setattr(slack, "cc_slack", lambda *args: {"answered": True})
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
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS)
    view.refresh_cards("demo")
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("ASK How should", "ASK When should")))
    assert run("job", "ask", asking, "-", "--unsettled", "a1, a2: he picked the layout, not when it is written")[0] == 0


def test_job_ask_refuses_unsettled_naming_his_words_under_its_own_card_until_drive_answer_settles_that_card(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    reply = "https://acme.slack.com/archives/D123/p1794300000000400?thread_ts=1794300000.000100"
    words = "keep one snapshot file, and copy it once"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    asking_him = [shown]
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": list(asking_him), "pr_cards": []})
    view.refresh_cards("demo")
    answered: list[tuple[str, ...]] = []
    monkeypatch.setattr(slack, "cc_slack", lambda *args: answered.append(args) or asking_him.clear() or {"answered": True})
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "How should?", state.stamp(), None, link) | {"waits": "demo:j1-7"})
        data["answers"]["6251dff"] = {"words": words, "link": reply, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {}}
    block = SHORT_ASK.replace("ASK How should", "ASK When should")
    why = "6251dff: not about copy"
    monkeypatch.setattr("sys.stdin", io.StringIO(block))
    assert cli.main(["job", "ask", asking, "-", "--unsettled", why]) == 2
    refusal = capsys.readouterr().err
    assert "--unsettled names his words under this job's own card, and that card still asks him" in refusal
    assert "settle the card with `drive answer 6251dff`, which shows his words on it" in refusal
    assert f'- 6251dff under {link}: "{words}" ({reply})' in refusal
    assert run("answer", "6251dff")[1].splitlines() == [
        "his answer 6251dff recorded: 0 standing, 0 superseded, 0 reopened by it",
        f"demo:j1-7: its card {link} now shows his words and a link to them, so it no longer asks him",
    ]
    assert answered == [("card-answer", "--card", link, "--label", words, "--settled", "--other", "--via", reply)]
    monkeypatch.setattr("sys.stdin", io.StringIO(block))
    assert run("job", "ask", asking, "-", "--unsettled", why)[0] == 0
    assert state.load("demo")["jobs"]["j1"]["questions"][-1]["unsettled"] == why


def test_the_open_card_refusal_gives_a_drive_answer_command_for_each_answer_of_his_it_names() -> None:
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    said = [
        {"id": "6251dff", "card": link, "words": "keep one snapshot file", "link": f"{link}?thread_ts=1794300000.000100"},
        {"id": "729fb9a", "card": link, "words": "and copy it once", "link": f"{link}?thread_ts=1794300000.000100"},
    ]
    refusal = settled.open_card_refusal(said, "6251dff, 729fb9a: not about copy")
    assert "settle the card with `drive answer 6251dff`, then `drive answer 729fb9a`, which shows his words on it" in refusal


def test_job_ask_refuses_a_question_his_words_in_its_orders_thread_may_settle_until_unsettled_names_each_by_permalink(
    run: Run, demo: str, slack_thread: Thread, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS | {"team_url": "https://acme.slack.com/"})
    view.refresh_cards("demo")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "design the run page", "--thread", "1794200000.000100")
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    moment = state.now()
    settles = slack_thread.post(moment, from_user=True, text="keep one snapshot file per finished run, no guessing")
    slack_thread.post(moment + timedelta(seconds=1), from_user=True, text="ok go ahead")
    slack_thread.post(moment + timedelta(seconds=2), from_claude=True, text="building one snapshot file per finished run")
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    assert cli.main(["job", "ask", "j1", str(block)]) == 2
    refusal = capsys.readouterr().err
    assert "his words in this order's thread may already settle this question" in refusal
    listed = [line for line in refusal.splitlines() if line.startswith("- ")]
    assert listed == [f'- {settles} "keep one snapshot file per finished run, no guessing" (shares finished, guessing, snapshot)']
    assert f'such as --unsettled "{settles}: <why it does not settle this>"' in refusal
    assert ("--channel", "D123", "--thread", "1794200000.000100", "--limit", "1000") in slack_thread.reads
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    why = f"{settles}: he picked the file layout, not where its map sits"
    assert run("job", "ask", "j1", str(block), "--unsettled", why)[0] == 0


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
    picture = tmp_path / "queue.png"
    picture.write_bytes(b"\0")
    assert keep(monkeypatch, capsys, "j1", seen.format(f"{picture}: the queue with one card"))[0] == 0
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
    assert "j1 returned DESIGN: the choice its research found goes to him as cards, and the recommended option's builder starts beside them" in capsys.readouterr().err
    assert run("job", "add", "design", "--order", "o1", "--goal", "cards: where the copy runs")[0] == 0
    assert run("job", "add", "builder", "--order", "o1", "--goal", "build it")[0] == 0


def test_the_immediate_fix_starts_at_once_past_the_research_the_design_and_options_only(
    run: Run, communicator: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        "order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build",
        "--parts", "the copy", "immediate fix", "--options-only",
    )
    assert cli.main(["job", "add", "builder", "--order", "o1", "--part", "the copy", "--goal", "build it"]) == 2
    assert "o1 is limited to options: he picks one before the proper fix is built" in capsys.readouterr().err
    assert run("job", "add", "builder", "--order", "o1", "--part", "immediate fix", "--goal", "stop the harm") == (0, "j1")
    run("order", "set", "o1", "--no-options-only")
    assert cli.main(["job", "add", "builder", "--order", "o1", "--part", "the copy", "--goal", "build it"]) == 2
    assert 'starts at once on its own "immediate fix" part, added with drive order set o1 --parts "the copy" "immediate fix"' in capsys.readouterr().err
    run("job", "add", "reader", "--order", "o1", "--part", "the copy", "--goal", "research: the copy")
    assert keep(monkeypatch, capsys, "j2", DESIGN_RESEARCH) == (0, "")
    assert run("job", "add", "codex", "--order", "o1", "--part", "immediate fix", "--goal", "stop it another way")[0] == 0
    assert cli.main(["job", "add", "builder", "--order", "o1", "--part", "the copy", "--part", "immediate fix", "--goal", "both"]) == 2
    assert 'the "immediate fix" part is a job of its own: its PR lands at once' in capsys.readouterr().err
    assert cli.main(["job", "set", "j1", "--part", "the copy"]) == 2
    assert 'the "immediate fix" part is a job of its own' in capsys.readouterr().err
    assert state.load("demo")["jobs"]["j1"]["parts"] == ["immediate fix"]


def test_a_readers_design_on_a_reported_bug_names_the_immediate_fix_shipping_while_he_picks(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        "order", "add", "--project", "p", "--workstream", "w", "fix the stuck restores", "--kind", "build",
        "--thread", "1794200000.000100", "--reporter", REPORTED, "--parts", "the copy", "immediate fix",
    )
    assert run("job", "add", "builder", "--order", "o1", "--part", "immediate fix", "--goal", "repair the stuck restores") == (0, "j1")
    run("job", "add", "reader", "--order", "o1", "--part", "the copy", "--goal", "research: the copy")
    code, err = keep(monkeypatch, capsys, "j2", DESIGN_RESEARCH)
    assert (code, "a reader's DESIGN on a bug hurting someone now names its immediate fix" in err) == (2, True)
    assert keep(monkeypatch, capsys, "j2", DESIGN_RESEARCH + "Interim fix: j1 repairs the stuck restores\n") == (0, "")


def test_a_builder_landing_prs_he_approved_starts_with_no_research(run: Run, with_pr: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "set", "o1", "--kind", "build")
    assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", "land it"]) == 2
    assert "o1 is a build order, so its builder starts only once an Opus reader has kept" in capsys.readouterr().err
    assert cli.main(["job", "add", "codex", "--order", "o1", "--lands", PR, "--goal", "land it"]) == 2
    assert "--lands names the PRs a builder lands" in capsys.readouterr().err
    assert cli.main(["job", "add", "builder", "--order", "o1", "--lands", PR, "--goal", "land it"]) == 2
    assert f"--lands {with_pr}: it may not land yet, no tap from him yet" in capsys.readouterr().err
    assert cli.main(["job", "add", "builder", "--order", "o1", "--lands", "https://github.com/Forge-AI/monorepo/pull/43", "--goal", "land it"]) == 2
    assert "--lands Forge-AI/monorepo#43: no drive PR" in capsys.readouterr().err
    run("pr", "set", PR, "--tapped", "Approve", "--tap-source", "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794173216327589")
    assert run("job", "add", "builder", "--order", "o1", "--lands", PR, "--goal", "land it")[0] == 0


def test_a_readers_kept_design_return_is_the_ask_its_card_draws(
    run: Run, communicator: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    assert cli.asked_report("demo", "j1", state.load("demo")["jobs"]["j1"]) is None
    assert keep(monkeypatch, capsys, "j1", DESIGN_RESEARCH) == (0, "")
    assert cli.asked_report("demo", "j1", state.load("demo")["jobs"]["j1"]).startswith("ASK where the copy runs\n")


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


def test_a_builders_report_opens_with_a_result_word_and_a_handoff_stops_its_job(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    code, err = keep(monkeypatch, capsys, "j1", "Summary: built the strip\nCHECK: https://github.com/yasyf/cc-skills/actions/runs/12\n")
    assert code == 2
    assert (
        'j1: refused, nothing kept: its first line opens "Summary: built the strip", and a builder\'s report opens with DONE,'
        " READY, BUILT, WAITING, ASK, BLOCKED, DESIGN or HANDOFF"
    ) in err
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    assert keep(monkeypatch, capsys, "j1", "READY https://github.com/yasyf/cc-skills/pull/4244\n") == (0, "")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship again")
    assert keep(monkeypatch, capsys, "j2", "HANDOFF /scratch/j2/left.md\nBRANCH: lr-strip\n") == (0, "")
    assert state.load("demo")["jobs"]["j2"]["state"] == "stopped"


def test_a_report_naming_a_repro_file_keeps_it_on_the_job_first(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "why does the copy stall?")
    run("job", "add", "reader", "--order", "o1", "--goal", "reproduce the stall")
    script = tmp_path / "r.sh"
    script.write_text("echo stall\n")
    report = f"The copy stalls on the second store\nREPRO: {script}\n"
    code, err = keep(monkeypatch, capsys, "j1", report)
    assert code == 2
    assert "j1: refused, nothing kept: it names a REPRO: file, but j1 keeps none in the drive: run drive job set j1 --repro <path> first" in err
    run("job", "set", "j1", "--repro", str(script))
    assert keep(monkeypatch, capsys, "j1", report) == (0, "")


def test_a_design_lanes_report_is_the_design_in_its_shape_unless_it_stops_short(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "copy one store")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store copy")
    code, err = keep(monkeypatch, capsys, "j1", "DONE wrote the forks\n")
    assert code == 2
    assert (
        "j1: refused, nothing kept: a design lane's report is the design in its shape, which the coordinator reads for its"
        " forks, and this one lacks a first line DESIGN <the part>, a Settled: line, a Native: line, a Fork: line, a Facts: line"
    ) in err
    assert "this one lacks a Fork: line: DESIGN <the part" in keep(monkeypatch, capsys, "j1", DESIGNED.replace("Fork: none: one layout fits\n", ""))[1]
    assert keep(monkeypatch, capsys, "j1", DESIGNED) == (0, "")
    assert state.load("demo")["jobs"]["j1"]["state"] == "waiting"
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store copy again")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store list")
    assert keep(monkeypatch, capsys, "j2", "HANDOFF /scratch/j2/left.md\n") == (0, "")
    assert keep(monkeypatch, capsys, "j3", "BLOCKED the store list is unreadable\n") == (0, "")
    jobs = state.load("demo")["jobs"]
    assert (jobs["j2"]["state"], jobs["j3"]["state"]) == ("stopped", "failed")


def test_a_research_readers_report_carries_its_filled_researched_block(
    run: Run, communicator: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    code, err = keep(monkeypatch, capsys, "j1", "DONE found two callers\n")
    assert code == 2
    assert (
        "j1: refused, nothing kept: j1 researches a build order, so its report carries a Researched: line and under it"
        " rulings:, exists: and open: lines, each filled or none, which its builder's brief names; it lacks rulings:, exists:, open:"
    ) in err
    assert keep(monkeypatch, capsys, "j1", RESEARCH.replace("open: none\n", "open:\n"))[1].endswith("it lacks open:\n")
    assert keep(monkeypatch, capsys, "j1", RESEARCH) == (0, "")


def test_a_readers_design_starts_from_the_root_cause_and_counts_only_its_options(
    run: Run, communicator: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    code, err = keep(monkeypatch, capsys, "j1", DESIGN_RESEARCH.replace(ROOT_CAUSE, ""))
    assert code == 2
    assert "j1: refused, nothing kept: a reader's DESIGN starts from the root cause and recommends the option that removes it: no `Root cause:` line" in err
    bulleted = DESIGN_RESEARCH.replace("\nrulings:", "\n- rulings:").replace("\nexists:", "\n- exists:").replace("\nopen:", "\n- open:")
    assert keep(monkeypatch, capsys, "j1", bulleted) == (0, "")


def test_a_done_or_ready_leaves_no_step_for_someone_else(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "backfill the copies")
    run("job", "add", "builder", "--order", "o1", "--goal", "backfill")
    done = "DONE the copies page lists every store\nCHECK: https://github.com/yasyf/cc-skills/actions/runs/12\n"
    for left in ("NEXT: coordinator runs the backfill once it ships", "UNPROVEN: the old stores, until the coordinator runs the backfill"):
        code, err = keep(monkeypatch, capsys, "j1", f"{done}{left}\n")
        assert code == 2
        assert f'j1: refused, nothing kept: a DONE leaves a step for someone else: "{left}". Do that step yourself before you return, or return ASK' in err
    assert keep(monkeypatch, capsys, "j1", f"{done}NEXT: no coordinator runs a backfill; the release applies it automatically\n") == (0, "")
    assert keep(monkeypatch, capsys, "j1", f"{done}PROVE: after release, open the run page\n") == (0, "")


def test_a_limit_left_standing_is_his_choice_and_returns_ask_or_design(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "copy the bucket")
    run("job", "add", "builder", "--order", "o1", "--goal", "copy it")
    check = "CHECK: https://github.com/yasyf/cc-skills/actions/runs/12\n"
    for finding in (
        "the bucket caps at 5k objects/s, left as is",
        "the copy hit the 5k objects/s rate limit",
        "the copy did not lose data and hit the 5k objects/s rate limit",
    ):
        code, err = keep(monkeypatch, capsys, "j1", f"DONE {finding}\n{check}")
        assert code == 2
        assert f'j1: refused, nothing kept: it reports a limit as a finding left standing: "DONE {finding}". A limit or capacity finding is his choice' in err
        assert "return ASK or DESIGN with two to four options for it, one recommended" in err
    refusal = "GraphQL: API rate limit already exceeded for user ID 709645."
    for own_words in (
        "UNPROVEN: the queue state of the stack; the read stopped when it hit GitHub's 5,000 calls an hour rate limit",
        f'UNPROVEN: the queue state of the stack; it hit the 5,000 calls an hour rate limit, "{refusal}"',
        f'UNPROVEN: the queue state of the stack, read for each of its four PRs before the merge, after the restack and at the enqueue; every read was refused with "{refusal}"',
    ):
        code, err = keep(monkeypatch, capsys, "j1", f"DONE the copy finished\n{check}{own_words}\n")
        assert code == 2
        assert f'it reports a limit as a finding left standing: "{own_words.rpartition("; ")[2]}". A limit or capacity finding is his choice' in err
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    design = "DESIGN the bucket caps at 5k objects/s, left as is\n- shard the copy across buckets (recommended)\n- copy at 5k objects/s\n"
    assert keep(monkeypatch, capsys, "j1", design) == (0, "")
    run("job", "add", "builder", "--order", "o1", "--goal", "queue the cards")
    assert keep(monkeypatch, capsys, "j2", f"DONE the queue caps at 4 cards, as his order asked; the card title is unchanged\n{check}") == (0, "")
    assert keep(monkeypatch, capsys, "j2", f"DONE the 5k objects/s cap was never hit\n{check}") == (0, "")
    for evidence in (
        f'DEFECT: ccx vcs gh refuses every read on this Sprite: "{refusal}"',
        f'Measured: stack-enqueue read the queue under its "GitHub rate limit floor" of 500 calls, then its 1:20 PM read was refused: "{refusal}"',
        f'Measured: gh pr view was refused 3 times between 1:10 PM and 1:24 PM, each "{refusal}"',
    ):
        assert keep(monkeypatch, capsys, "j2", f"DONE the copy finished\n{check}{evidence}\n") == (0, "")


def test_a_builders_ready_names_prs_whose_tap_card_draws(
    run: Run, with_pr: str, on_github: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    on_github["body"] = CARD_BODY.replace("## What you'll hate most", "## Biggest surprise")
    code, err = keep(monkeypatch, capsys, "j1", f"READY {PR}\n")
    assert code == 2
    assert f"j1: refused, nothing kept: the PRs it names would draw no tap card: its tap card needs a \"What you'll hate most\" section in the body of {PR}" in err
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    on_github["body"] = CARD_BODY
    assert keep(monkeypatch, capsys, "j1", f"READY {PR}\n") == (0, "")


def test_a_builders_ready_for_a_change_he_sees_needs_a_before_and_an_after_screenshot_on_its_card_kept_where_the_drive_keeps_pictures(
    run: Run, with_pr: str, on_github: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = f"READY {PR}\nNATIVE: the Iris app's run page\n"
    pictures = state.root() / "demo" / "pictures"
    before, after = f"![1 · Before, dev]({pictures}/j779-run-page-before-de31bf9.png)\n", f"![2 · After, this PR]({pictures}/j779-run-page-after-e3aefbd.png)\n"
    for show_me, alone in (("", False), (f"## Show me\n{before}", True)):
        on_github["body"] = CARD_BODY + show_me
        code, err = keep(monkeypatch, capsys, "j1", seen)
        said = " ".join(err.split())
        assert (code, card.SCREENSHOTS in said, 'the Show me picture "1 · Before, dev" is a file drawn by hand' in said) == (2, True, alone)
    on_github["body"] = CARD_BODY + f"## Show me\n{before}{after}"
    assert keep(monkeypatch, capsys, "j1", seen) == (0, "")
    on_github["body"] = CARD_BODY
    assert keep(monkeypatch, capsys, "j1", f"READY {PR}\nNATIVE: none: he never sees it\n") == (0, "")


def test_a_builders_ready_checks_each_stack_its_prs_form_on_its_own(
    run: Run, with_pr: str, on_github: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    other = PR.removesuffix("42") + "43"
    run("pr", "add", other, "--job", "j1")
    branches = {"42": ("main", "land-it"), "43": ("land-it", "land-more")}

    def gh(cmd: list[str], **_: str) -> str:
        if cmd[1] == "api":
            return "[]"
        base, head = branches[cmd[3]]
        return json.dumps(on_github | {"url": PR.removesuffix("42") + cmd[3], "baseRefName": base, "headRefName": head})

    monkeypatch.setattr(card, "run", gh)
    code, err = keep(monkeypatch, capsys, "j1", f"READY {PR} {other}\n")
    assert code == 2
    assert (
        "j1: refused, nothing kept: the PRs it names would draw no tap card: its tap card needs a \"The stack\" section with the"
        f" stack's one-line title then its summary in the body of {PR}"
    ) in err
    branches["43"] = ("main", "land-more")
    assert keep(monkeypatch, capsys, "j1", f"READY {PR} {other}\n") == (0, "")


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
        ("builder", "HANDOFF /scratch/j1/left.md", "stopped"),
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
    rest = DESIGNED.split("\n", 1)[1] if kind == "design" else ""
    for job_id in ("j1", "j2"):
        monkeypatch.setattr("sys.stdin", io.StringIO(f"{first}\n{rest}CHECK: https://github.com/yasyf/cc-skills/actions/runs/12\n"))
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


def test_job_set_keeps_a_jobs_state_for_any_state_but_stopped_and_still_sets_the_rest(run: Run, demo: str) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    kept = (
        "j1 stays running: --state sets only stopped, for a job that ends unfinished, and done for a design job a builder carries;"
        " every other state comes from the line the job returns"
    )
    for taken in ("waiting", "built", "done", "failed"):
        code, out = run("job", "set", "j1", "--state", taken, "--said", f"asked {taken}")
        assert (code, out.splitlines()[0]) == (0, kept)
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("running", "asked failed")
    assert run("job", "set", "j1", "--state", "stopped")[0] == 0
    assert state.load("demo")["jobs"]["j1"]["state"] == "stopped"


def test_a_design_job_closes_once_a_builder_carries_it(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "copy one store")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store copy")
    assert keep(monkeypatch, capsys, "j1", DESIGNED) == (0, "")
    assert run("job", "set", "j1", "--state", "done")[1].startswith("j1 stays waiting: --state sets only stopped")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the copy")
    assert run("job", "set", "j1", "--state", "done")[1].splitlines()[0] == "j1's design is carried by builder j2"
    assert state.load("demo")["jobs"]["j1"]["state"] == "done"
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store list")
    assert keep(monkeypatch, capsys, "j3", DESIGNED) == (0, "")
    assert keep(monkeypatch, capsys, "j2", "DONE installed 1.0.76\nCHECK: https://github.com/yasyf/cc-skills/actions/runs/12\n") == (0, "")
    design = state.load("demo")["jobs"]["j3"]
    assert (design["state"], design["said"]) == ("done", "built by j2: DONE installed 1.0.76")


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


@pytest.fixture
def with_pr(run: Run, demo: str) -> str:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    run("pr", "add", PR + "/files", "--job", "j1")
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


def test_pr_add_waits_on_his_tap_unless_it_is_a_port_exempt_drive_tooling_or_untapped_for_a_reason(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    key = "Forge-AI/monorepo#42"
    assert cli.main(["pr", "add", PR, "--job", "j1", "--no-tap"]) == 2
    assert "--no-tap needs --why <reason>" in capsys.readouterr().err
    for flags, stored in (
        (["--no-tap", "--why", "a teammate lands it"], (False, None, None)),
        (["--exempt", "test-only"], (True, "test-only", "landing")),
    ):
        with state.edit("demo") as data:
            data["prs"].pop(key, None)
        assert run("pr", "add", PR, "--job", "j1", *flags)[0] == 0
        pr = state.load("demo")["prs"][key]
        assert (pr["wants_tap"], pr["exempt"], state.owed_step(pr)) == stored
    with state.edit("demo") as data:
        data["prs"].pop(key)
        data["orders"]["o1"]["text"] = "Drive tooling: the watch draws his card"
    assert run("pr", "add", PR, "--job", "j1")[0] == 0
    assert state.load("demo")["prs"][key]["wants_tap"] is False


def test_pr_add_of_a_port_takes_the_pure_port_exemption_and_no_tap(run: Run, demo: str, on_github: dict) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    on_github["title"] = "Port: land it"
    assert run("pr", "add", PR, "--job", "j1", "--port-of", "51234")[0] == 0
    pr = state.load("demo")["prs"]["Forge-AI/monorepo#42"]
    assert (pr["port_of"], pr["exempt"], pr["wants_tap"], state.owed_step(pr)) == (51234, "pure port", False, "landing")


def test_job_add_reader_checks_stamps_the_prs_head_on_the_reader_and_its_card_check(run: Run, with_pr: str, on_github: dict) -> None:
    on_github["headRefOid"] = "def5678"
    code, out = run("job", "add", "reader", "--order", "o1", "--goal", "his words", "--checks", PR)
    job_id = out.splitlines()[0]
    data = state.load("demo")
    assert (code, data["jobs"][job_id]["checks"]) == (0, {with_pr: "def5678"})
    assert data["prs"][with_pr]["check"] == {"job": job_id, "head": "def5678", "at": data["jobs"][job_id]["created"]}
    assert run("job", "add", "builder", "--order", "o1", "--goal", "land", "--checks", PR)[0] == 2


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


def test_pr_set_no_tapped_drops_a_hold_the_drive_gave_itself_with_its_why(run: Run, with_pr: str) -> None:
    source = "migrated hold: breaks:a client on an older build cannot read it"
    with state.edit("demo") as data:
        data["prs"][with_pr]["tap"] = {"label": "Hold", "at": state.stamp(), "head": "abc", "source": source}
    assert run("pr", "set", PR, "--no-tapped")[0] == 2
    assert state.load("demo")["prs"][with_pr]["tap"] is not None
    code, out = run("pr", "set", PR, "--no-tapped", "--why", "the release carrying its reader is live")
    assert code == 0
    assert out.endswith(f"\ndropped the Hold the drive gave itself ({source}): the release carrying its reader is live")
    pr = state.load("demo")["prs"][with_pr]
    assert (pr["tap"], pr["fundamental"]) == (None, None)
    assert run("pr", "tapped", PR) == (1, f"{with_pr}: no tap from him yet")
    [event] = [event for event, _ in state.read_events("demo") if event["kind"] == "untapped"]
    assert (event["number"], event["label"], event["source"], event["why"]) == (42, "Hold", source, "the release carrying its reader is live")
    assert run("pr", "set", PR, "--no-tapped", "--why", "again")[0] == 2


def test_pr_set_no_tapped_writes_its_event_for_the_watch_hearing_the_prs_order(run: Run, with_pr: str) -> None:
    with state.edit("demo") as data:
        data["prs"][with_pr]["tap"] = {"label": "Hold", "at": state.stamp(), "head": "abc", "source": "migrated hold: its reader ships first"}
        cli.bind_subcoordinator(data, "The drive", {"session": "drive-1", "terminal": None, "cwd": "/work", "started": state.stamp()})
    assert run("pr", "set", PR, "--no-tapped", "--why", "the release carrying its reader is live")[0] == 0
    [event] = [event for event, _ in state.read_events("demo") if event["kind"] == "untapped"]
    assert event["lane"] == "the-drive"


@pytest.mark.parametrize(
    ("label", "source", "pressed"),
    [
        ("Hold", "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1793895991193029?thread_ts=1793895914.909779", False),
        ("Hold", "pressed the button", True),
        ("Approve", "migrated: old drive ledger tap", False),
    ],
)
def test_pr_set_no_tapped_refuses_a_tap_of_his(run: Run, with_pr: str, label: str, source: str, pressed: bool) -> None:
    with state.edit("demo") as data:
        data["prs"][with_pr]["tap"] = {"label": label, "at": state.stamp(), "head": "abc", "source": source}
    if pressed:
        state.append_event("demo", "tap", repo="Forge-AI/monorepo", number=42, label=label, head="abc", source=source, action_ts="1.2")
    assert run("pr", "set", PR, "--no-tapped", "--why", "the drive's hold moved to --waits")[0] == 2
    assert state.load("demo")["prs"][with_pr]["tap"]["source"] == source


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


def test_pr_tapped_refuses_a_pr_the_drive_holds_with_the_hold_label_even_when_exempt(run: Run, with_pr: str) -> None:
    run("pr", "set", PR, "--exempt", "test-only")
    with state.edit("demo") as data:
        data["prs"][with_pr]["hold_label"] = True
    assert run("pr", "tapped", PR) == (1, f"{with_pr}: held by the drive with the hold label")


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


def test_an_incidents_stop_is_its_immediate_fix_and_lands_at_once_as_an_incident_fix(
    run: Run, demo: str, on_github: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli.reads, "run", recorded([]))
    run("incident", "open", "table moves rerun on every write", "--no-rollback", "--record", RECORD, *FILED)
    run("job", "add", "builder", "--order", "o1", "--part", "stop", "--goal", "ship the stop")
    run("pr", "add", PR, "--job", "j4")
    on_github.update(title="Fix: table moves stop rerunning on every write", body=CARD_BODY)
    assert run("pr", "set", PR, "--exempt", "incident fix")[0] == 0
    assert state.load("demo")["prs"]["Forge-AI/monorepo#42"]["exempt"] == "incident fix"


def test_pr_set_toggles_wants_tap(run: Run, with_pr: str) -> None:
    run("pr", "set", PR, "--no-tap", "--why", "not his order")
    assert state.load("demo")["prs"][with_pr]["wants_tap"] is False
    run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")
    assert state.load("demo")["prs"][with_pr]["wants_tap"] is True


def test_pr_set_serves_names_the_order_it_serves_and_prints_his_words(run: Run, with_pr: str) -> None:
    run("pr", "set", PR, "--no-tap", "--why", "not his order")
    assert run("pr", "set", PR, "--tap", "--serves", "o9", "--design", "none")[0] == 2
    assert state.load("demo")["prs"][with_pr]["wants_tap"] is False
    code, out = run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")
    assert code == 0
    assert out.splitlines()[-1] == "serves o1, his words: land it"
    assert state.load("demo")["prs"][with_pr]["serves"] == "o1"


def test_pr_set_tap_takes_a_pr_on_an_order_with_merged_prs_on_other_symptoms(run: Run, with_pr: str) -> None:
    with state.edit("demo") as data:
        for key in ("acme/widgets#4153", "acme/widgets#4190", "acme/widgets#4201"):
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"state": "merged", "serves": "o1"}
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")[0] == 0


def test_pr_set_serves_needs_the_design_answer_he_gave_or_none(run: Run, with_pr: str) -> None:
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


def test_pr_set_tap_draws_a_body_with_no_releasable_section_and_a_step_after_the_release(
    run: Run, with_pr: str, on_github: dict
) -> None:
    run("pr", "set", PR, "--no-tap", "--why", "not his order")
    on_github["body"] = CARD_BODY + "## Risk\nThen run the fill from a pass box once it ships.\n"
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")[0] == 0


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


def test_pr_set_tap_serving_a_drive_tooling_order_needs_no_words_of_his_and_only_the_communicator_sets_it(
    run: Run, communicator: str, on_github: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a tooling PR lands on his Approve")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    run("pr", "add", PR, "--job", "j1")
    on_github["body"] = CARD_BODY.split("## His words")[0] + "## His words, step by step\nNone: drive tooling.\n"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "elsewhere")
    assert cli.main(["pr", "set", PR, "--tap", "--serves", "o1", "--design", "none"]) == 2
    assert capsys.readouterr().err.strip() == (
        'drive: Forge-AI/monorepo#42 --serves o1: refused, o1 is a "Drive tooling:" order, which only the communicator acts on:'
        " send a defect record to communicator@demo naming what it needs, and the communicator's builder takes it over"
    )
    assert state.load("demo")["prs"]["Forge-AI/monorepo#42"]["wants_tap"] is False
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-under-test")
    code, out = run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")
    assert code == 0
    assert out.splitlines()[-1] == "serves o1, his words: Drive tooling: a tooling PR lands on his Approve"
    pr = state.load("demo")["prs"]["Forge-AI/monorepo#42"]
    assert (pr["wants_tap"], pr["serves"]) == (True, "o1")


def test_a_tooling_orders_job_or_pr_never_carries_or_serves_his_feature_order_nor_the_reverse(
    run: Run, communicator: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: the ask card redraws")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: fix ask")
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "show the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "redraw it")
    run("pr", "add", PR, "--job", "j1")
    crossed = "o1 is a drive tooling order and o3 is an order of his feature work, and one job or PR never carries both: start a job on o3 with drive job add <kind> --order o3"
    assert cli.main(["job", "set", "j1", "--carries", "o3"]) == 2
    assert capsys.readouterr().err.strip() == f"drive: j1 --carries o3: refused, {crossed}"
    assert cli.main(["pr", "set", PR, "--serves", "o3", "--no-tap", "--why", "drive tooling"]) == 2
    assert capsys.readouterr().err.strip() == f"drive: Forge-AI/monorepo#42 --serves o3: refused, {crossed}"
    assert state.load("demo")["jobs"]["j1"]["carries"] == {}
    assert run("job", "set", "j1", "--carries", "o2")[0] == 0
    assert state.load("demo")["jobs"]["j1"]["carries"] == {"o2": ["Drive tooling: fix ask"]}
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "elsewhere")
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "land the page")
    run("job", "add", "builder", "--order", "o4", "--goal", "land it")
    assert cli.main(["job", "set", "j2", "--carries", "o2"]) == 2
    assert "j2 --carries o2: refused, o2 is a \"Drive tooling:\" order, which only the communicator acts on: send a defect record to communicator@demo" in capsys.readouterr().err


def test_pr_set_tap_accepts_a_body_with_every_section_his_card_needs(run: Run, with_pr: str) -> None:
    run("pr", "set", PR, "--no-tap", "--why", "not his order")
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")[0] == 0
    assert state.load("demo")["prs"][with_pr]["wants_tap"] is True


def test_pr_set_tap_on_an_exempt_pr_draws_no_card_so_checks_none(run: Run, with_pr: str, on_github: dict) -> None:
    run("pr", "set", PR, "--exempt", "test-only")
    on_github["body"] = "## Summary\nTests only."
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")[0] == 0


def test_pr_set_port_of_refuses_a_title_that_does_not_open_port(
    run: Run, with_pr: str, on_github: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    on_github["title"] = "move strip"
    assert cli.main(["pr", "set", PR, "--port-of", "4100"]) == 2
    assert capsys.readouterr().err.strip() == (
        f'drive: {with_pr}: refused, its title "move strip" does not open Port:, since it is a port of #4100, the kind of change his card'
        ' names it by: retitle it with gh pr edit 42 -R Forge-AI/monorepo --title "Port: move strip", then run this again'
    )
    on_github["title"] = "Fix: move strip"
    assert cli.main(["pr", "set", PR, "--port-of", "4100"]) == 2
    assert 'does not open Port:, since it is a port of #4100' in capsys.readouterr().err
    assert state.load("demo")["prs"][with_pr]["exempt"] is None
    on_github["title"] = "Port: move strip"
    assert run("pr", "set", PR, "--port-of", "4100")[0] == 0
    assert state.load("demo")["prs"][with_pr]["exempt"] == "pure port"


def test_pr_set_port_of_exempts_it_with_no_tap(run: Run, with_pr: str, on_github: dict, withdrawals: "Withdrawals") -> None:
    on_github["title"] = "Port: land it"
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


def test_pr_add_refuses_a_title_his_card_cannot_name_the_kind_of(run: Run, demo: str, on_github: dict, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "move the strip")
    run("job", "add", "builder", "--order", "o1", "--goal", "move it")
    on_github["title"] = "move strip"
    assert cli.main(["pr", "add", PR, "--job", "j1"]) == 2
    assert capsys.readouterr().err.strip() == (
        'drive: Forge-AI/monorepo#42: refused, its title "move strip" does not open Fix:, New:, Cleanup: or Port:, the kind of change'
        ' his card names it by: retitle it with gh pr edit 42 -R Forge-AI/monorepo --title "<Fix|New|Cleanup|Port>: move strip", then run this again'
    )
    assert state.load("demo")["prs"] == {}
    for title in ("Port: move strip", "store: 🐛 move strip", "fix(store): move strip"):
        on_github["title"] = title
        assert run("pr", "add", PR, "--job", "j1")[0] == 0


def test_pr_set_tap_refuses_a_title_retitled_since_drive_pr_add_read_it(
    run: Run, with_pr: str, on_github: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    on_github["title"] = "move strip"
    assert cli.main(["pr", "set", PR, "--tap", "--serves", "o1", "--design", "none"]) == 2
    assert capsys.readouterr().err.strip() == (
        f'drive: {with_pr}: refused, its title "move strip" does not open Fix:, New:, Cleanup: or Port:, the kind of change'
        ' his card names it by: retitle it with gh pr edit 42 -R Forge-AI/monorepo --title "<Fix|New|Cleanup|Port>: move strip", then run this again'
    )
    assert state.load("demo")["prs"][with_pr].get("serves") is None
    on_github["title"] = "Fix: move strip"
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")[0] == 0
    assert state.load("demo")["prs"][with_pr]["serves"] == "o1"


def test_pr_add_on_an_incident_order_refuses_a_revert_unless_it_waits_on_his_tap(
    run: Run, demo: str, on_github: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "the mint endpoint fails", "--incident", "--parts", "fix", "first check")
    on_github.update(title="Fix: the mint endpoint answers again", body="Reverts Forge-AI/monorepo#41")
    for untapped in (["--exempt", "incident fix"], ["--no-tap", "--why", "the incident lands it"]):
        assert cli.main(["pr", "add", PR, "--job", "j1", *untapped]) == 2
        refusal = capsys.readouterr().err
        assert 'refused, it reverts a change, and a revert never lands as an "incident fix"' in refusal
        assert f"Add it waiting on his tap with drive pr add {PR} --job j1. Draw his tap card with drive pr set {PR} --tap --serves o1 --design none" in refusal
    assert state.load("demo")["prs"] == {}
    assert run("pr", "add", PR, "--job", "j1")[0] == 0
    assert state.load("demo")["prs"]["Forge-AI/monorepo#42"]["wants_tap"] is True
    on_github["body"] = CARD_BODY
    assert run("pr", "add", PR, "--job", "j1")[0] == 0


def test_a_tap_card_refuses_a_body_naming_record_ids(run: Run, with_pr: str, on_github: dict, capsys: pytest.CaptureFixture[str]) -> None:
    run("pr", "set", PR, "--no-tap", "--why", "not his order")
    on_github["body"] = CARD_BODY.replace("It lands without a second look.", "It lands without a second look. It follows cci 661229.")
    assert cli.main(["pr", "set", PR, "--tap", "--serves", "o1", "--design", "none"]) == 2
    assert capsys.readouterr().err.strip() == (
        f"drive: {with_pr}: its tap card needs plain words for what each of cci 661229 is, in place of the record id, which says"
        f" nothing to him in the body of {PR}"
    )
    on_github["body"] = CARD_BODY.replace("It lands without a second look.", "It lands without a second look, per his 11:23 PM card answer.")
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")[0] == 0


def test_job_add_refuses_a_local_builder_past_the_macs_cap_or_a_monorepo_one_while_drive_place_says_remote(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(place, "busy", lambda: 0.95)
    monkeypatch.setattr(place, "limit", lambda: 0.8)
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "land the strip")
    assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", "land", "--repo", "Forge-AI/monorepo"]) == 2
    assert capsys.readouterr().err.strip() == (
        "drive: refused, no job added: this Mac is 95% busy, at or over its 80% limit, so this builder starts remote: add --remote,"
        " or wait until drive place says local"
    )
    assert run("job", "add", "builder", "--order", "o1", "--goal", "land", "--repo", "Forge-AI/monorepo", "--remote")[0] == 0
    for _ in range(place.LOCAL_BUILDERS - 1):
        assert run("job", "add", "builder", "--order", "o1", "--goal", "land", "--repo", "yasyf/cc-skills", "--agent", "a")[0] == 0
    assert run("job", "add", "builder", "--order", "o1", "--goal", "land", "--repo", "yasyf/cc-skills")[0] == 0
    with state.edit("demo") as data:
        data["jobs"]["j9"].update(state.local_lane("dispatch-9"))
    assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", "land", "--repo", "yasyf/cc-skills"]) == 2
    assert "refused, no job added: 8 builders already run on this Mac, its cap of 8, so this builder starts remote" in capsys.readouterr().err
    assert run("job", "add", "reader", "--order", "o1", "--goal", "read")[0] == 0
    with state.edit("demo") as data:
        state.lane_died(data["jobs"]["j9"], "dispatch-9", "its worker finished", state.now())
    assert run("job", "add", "builder", "--order", "o1", "--goal", "land", "--repo", "yasyf/cc-skills")[0] == 0


def test_job_brief_writes_native_interactions_would_he_ask_and_a_readers_research_and_keeps_each_flag_left_out(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "show the run page", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the run page")
    code, out = run("job", "brief", "j1", "--serves", "his order", "--done-when", "researched", "--where", "yasyf/cc-skills main")
    assert code == 0
    assert "\nResearch: the Researched: block this build needs\n" in Path(out.splitlines()[0].removeprefix("Read your brief first: ")).read_text()
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "show the run's steps")
    run("job", "add", "builder", "--order", "o2", "--goal", "draw the steps")
    assert cli.main(["job", "brief", "j2"]) == 2
    assert capsys.readouterr().err.strip() == "drive: j2 has no earlier brief to keep --serves, --done-when, --where from: name each"
    first = ["job", "brief", "j2", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main", "--native", "the dashboard's run page"]
    assert cli.main(first) == 2
    assert capsys.readouterr().err.strip() == (
        "drive: j2's result is something he uses, by --native: name each click or step he takes on it with --interactions,"
        " which its builder walks through and records as its proof"
    )
    code, out = run(*first, "--interactions", "open the run page, click a step, read its log", "--would-he-ask", "a link to the run from his DM")
    written = Path(out.splitlines()[0].removeprefix("Read your brief first: "))
    assert code == 0
    assert (
        "Native: the dashboard's run page\nInteractions: open the run page, click a step, read its log\nWould he ask: a link to the run from his DM\n"
        in written.read_text()
    )
    code, out = run("job", "brief", "j2", "--plan", "the steps section")
    assert code == 0
    assert out.splitlines()[-1] == "kept from its earlier brief: --serves, --done-when, --where, --native, --interactions, --would-he-ask"
    assert "Serves: his order\n" in written.read_text()
    assert "Native: the dashboard's run page\n" in written.read_text()
    assert "Plan: the steps section\n" in written.read_text()


def test_order_set_proves_done_on_his_surface_with_a_recording_or_the_live_post(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    view, walk, log, log_walk = (tmp_path / saved for saved in ("view.json", "walk.mov", "log.json", "log.mov"))
    for saved in (view, walk, log, log_walk):
        saved.write_text("saved")
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "show the run's steps")
    run("job", "add", "builder", "--order", "o1", "--goal", "draw the steps")
    run(
        "job", "brief", "j1", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main",
        "--native", "the dashboard's run page", "--interactions", "open the run page, click a step",
    )
    assert cli.main(["order", "set", "o1", "--state", "done", "--proof", str(view)]) == 2
    assert capsys.readouterr().err.startswith(
        f"drive: --proof {view}: refused, o1 builds something he uses (the dashboard's run page), so it is proven done on that surface:"
    )
    assert state.load("demo")["orders"]["o1"]["state"] == "open"
    assert run("order", "set", "o1", "--state", "done", "--proof", str(walk))[0] == 0
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "count the runs")
    run("job", "add", "builder", "--order", "o2", "--goal", "count them")
    run("job", "brief", "j2", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main", "--native", "none: he never sees it")
    assert run("order", "set", "o2", "--state", "done", "--proof", str(view))[0] == 0
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "show the run's log")
    run("job", "add", "builder", "--order", "o3", "--goal", "draw the log")
    assert run("order", "set", "o3", "--proof", str(log))[0] == 0
    run(
        "job", "brief", "j3", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main",
        "--native", "the dashboard's log page", "--interactions", "open the log page",
    )
    assert cli.main(["order", "set", "o3", "--state", "done"]) == 2
    refusal = capsys.readouterr().err
    assert refusal.startswith(f"drive: its proof {log}: refused, o3 builds something he uses (the dashboard's log page)")
    assert refusal.strip().endswith("; close it with --proof <that recording or link>")
    assert state.load("demo")["orders"]["o3"]["state"] == "open"
    assert run("order", "set", "o3", "--state", "done", "--proof", str(log_walk))[0] == 0


def test_job_brief_refuses_an_adopted_prs_waiting_job_until_an_event_hands_it_off(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "land the strip")
    run("job", "add", "builder", "--order", "o1", "--goal", "Fix: land it", "--repo", "Forge-AI/monorepo", "--remote")
    monkeypatch.setattr("sys.stdin", io.StringIO(f"WAITING {PR}\n"))
    run("job", "report", "j1", "-")
    run("pr", "add", PR, "--job", "j1")
    brief = ["job", "brief", "j1", "--serves", "his order", "--done-when", "merged", "--where", "Forge-AI/monorepo main"]
    assert cli.main(brief) == 2
    assert capsys.readouterr().err.strip() == (
        f"drive: j1 is an adopted PR's job, waiting with no agent on {PR}: its builder starts only on an event that needs action,"
        " failed checks, requested changes, a new head or his tap; when the watch reports one, brief it with --handoff <the PR,"
        " its head and the event line verbatim>"
    )
    assert run(*brief, "--handoff", f"{PR} head abc: CHECK test failed")[0] == 0


def test_job_ask_on_a_port_names_the_pr_it_ports_in_its_summary(
    run: Run, asking: str, on_github: dict, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    on_github["title"] = "Port: move strip"
    run("pr", "add", PR, "--job", asking)
    run("pr", "set", PR, "--port-of", "4100")
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert capsys.readouterr().err.strip() == (
        'drive: j1\'s PR is a port of #4100: its ASK\'s summary says "port of #4100", as the text of that PR\'s link, and what in the'
        " target tree forces a choice #4100 did not make, such as what that tree lacks"
    )
    ported = "This is a <https://github.com/Forge-AI/monorepo/pull/4100|port of #4100>; the target tree lacks the snapshot writer. A finished run is about"
    block.write_text(SHORT_ASK.replace("A finished run is about", ported))
    assert run("job", "ask", asking, str(block))[0] == 0


def test_pr_tapped_unknown_pr_may_land(run: Run, demo: str) -> None:
    assert run("pr", "tapped", PR) == (0, "Forge-AI/monorepo#42: not a drive PR")


def test_pr_tapped_json(run: Run, with_pr: str) -> None:
    code, out = run("pr", "tapped", PR, "--json")
    assert code == 1
    assert json.loads(out) == {"key": with_pr, "may_land": False, "why": "no tap from him yet"}


def test_status_reads_the_prs_from_the_drive_and_never_from_github(run: Run, with_pr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(*_: object, **__: object) -> str:
        raise AssertionError("drive status read GitHub")

    monkeypatch.setattr(github, "run", refused)
    with state.edit("demo") as data:
        data["prs"][with_pr].update(title="Fix: the watched title", checks="pass", review="approved")
    code, out = run("status")
    assert code == 0
    assert out.splitlines()[0] == "demo: 1 open of 1 orders"
    assert 'Forge-AI/monorepo#42 "Fix: the watched title" open, checks pass, review approved' in out
    assert json.loads(run("status", "--json")[1])["prs"][with_pr]["title"] == "Fix: the watched title"


def test_status_reads_only_the_orders_its_ids_name(run: Run, with_pr: str) -> None:
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
    run: Run, with_pr: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "something else")
    code, out = run("status", "42")
    assert (code, "land it" in out, "something else" in out) == (0, True, False)
    assert cli.main(["status", "53615"]) == 2
    assert "no order, job or PR 53615; name an order o<n>, a job j<n>, or a PR as owner/repo#N, its URL or its bare number" in capsys.readouterr().err
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/sand#42"] = data["prs"][with_pr] | {"url": "https://github.com/Forge-AI/sand/pull/42"}
    assert cli.main(["status", "42"]) == 2
    assert "42 is the number of Forge-AI/monorepo#42, Forge-AI/sand#42; name one as owner/repo#42" in capsys.readouterr().err


def test_order_show_and_job_show_print_what_status_prints(run: Run, with_pr: str) -> None:
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
        "demo: 0 open of 1 orders",
        f"- o1 done: {' '.join(words.split())}",
        f"  parts: {state.load('demo')['orders']['o1']['parts'].popitem()[0]}",
        f"  - j1 builder done: {' '.join(('one restore code path ' * 8).split())}",
        f"    said: {' '.join(said.split())}",
    ]


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
    monkeypatch.setattr(cli.watch, "main", lambda name, project: calls.append((name, project)) or 0)
    assert run("watch", "--loop", "--drive", "demo")[0] == 0
    assert calls == [("demo", None)]


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
    report_records(monkeypatch, {"661300": {"part": "why"}})
    assert run("order", "posted", "o1", answer, "--record", "661300")[0] == 0
    run("order", "set", "o1", "--parts", "why", "what")
    parts = state.load("demo")["orders"]["o1"]["parts"]
    assert parts == {"why": {"posted": answer, "orphan_said": None}, "what": state.new_part()}


def test_a_post_naming_no_part_of_an_order_of_several_is_a_progress_line_that_keeps_its_promised_time(
    run: Run, demo: str, slack_thread: "Thread", capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
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
    report_records(monkeypatch, {"661300": {"part": "rollback"}, "661301": {"part": "fix"}})
    assert run("order", "posted", "o1", line, "--record", "661300")[0] == 0
    marks = {part: kept["posted"] for part, kept in state.load("demo")["orders"]["o1"]["parts"].items()}
    assert marks == {"stop the harm": None, "rollback": line, "cause": None}
    assert cli.main(["order", "posted", "o1", line, "--record", "661301"]) == 2
    assert 'o1 has no part fix; its parts are "stop the harm", "rollback", "cause"' in capsys.readouterr().err


def test_the_communicator_takes_back_a_parts_delivered_mark(run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "stop the harm, roll back", "--thread", "1794200000.000100", "--parts", "stop the harm", "rollback")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    line = slack_thread.post(state.now(), from_claude=True)
    report_records(monkeypatch, {"661300": {"part": "stop the harm"}, "661301": {"part": "rollback"}})
    run("order", "posted", "o1", line, "--record", "661300")
    run("order", "posted", "o1", line, "--record", "661301")
    assert run("order", "posted", "o1", "--undo")[0] == 2
    assert run("order", "posted", "o1", "--part", "stop the harm", "--undo") == (0, 'o1 "stop the harm": not delivered')
    parts = state.load("demo")["orders"]["o1"]["parts"]
    assert (parts["stop the harm"]["posted"], parts["rollback"]["posted"]) == (None, line)


def test_an_answer_counts_as_posted_in_the_thread_of_one_of_the_orders_cards(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch
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
    report_records(monkeypatch, {"661300": {"job": "j1", "part": "how fast can restore go?"}})
    assert run("order", "posted", "o1", answer, "--record", "661300") == (0, f'o1 "how fast can restore go?": posted {answer}')
    assert state.load("demo")["orders"]["o1"]["parts"]["how fast can restore go?"]["posted"] == answer


def test_a_line_in_the_thread_of_the_card_of_a_pr_on_the_order_is_in_the_orders_thread(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship web search", "--thread", "1794100000.000100", "--due", "+30m")
    run("job", "add", "builder", "--order", "o1", "--goal", "web search")
    run("pr", "add", PR, "--job", "j1")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    root = {"ts": "1794200000.000100", "thread_ts": "1794200000.000100", "from_claude": True}
    slack_thread.messages.append(root | {"text": "New: approve web search? — ✓ *Approve* · <https://github.com/Forge-AI/monorepo/pull/41|PR 41>"})
    line = slack_thread.post(state.now(), from_claude=True)
    assert run("order", "posted", "o1", line)[0] == 2
    slack_thread.messages[0] = root | {"text": f"New: approve web search? — ✓ *Approve* · answered 7:10pm · <{PR}|monorepo PR 42>"}
    report_records(monkeypatch, {"661300": {"job": "j1", "part": "ship web search"}})
    assert run("order", "posted", "o1", line, "--record", "661300") == (0, f'o1 "ship web search": posted {line}')
    order = state.load("demo")["orders"]["o1"]
    assert (order["parts"]["ship web search"]["posted"], order["last_line"]) == (line, state.stamp(slack.sent_at(slack_thread.messages[-1])))


def test_a_bare_link_to_a_line_under_the_card_of_a_pr_on_the_order_reads_the_card_its_thread_hangs_from(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship web search", "--thread", "1794100000.000100")
    run("job", "add", "builder", "--order", "o1", "--goal", "web search")
    run("pr", "add", PR, "--job", "j1")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    card = {"ts": "1794200000.000100", "thread_ts": "1794200000.000100", "from_claude": True, "text": f"New: approve web search? — ✓ *Approve* · <{PR}|monorepo PR 42>"}
    slack_thread.messages.append(card)
    line = slack_thread.post(state.now(), from_claude=True).split("?")[0]
    reply = slack_thread.messages[-1]
    monkeypatch.setattr(slack, "thread", lambda *where: slack_thread.reads.append(where) or ([reply] if where[0] == "--url" else list(slack_thread.messages)))
    assert run("order", "posted", "o1", line) == (0, f"o1 progress line, no part delivered: {line}")
    assert ("--channel", "D123", "--thread", card["ts"], "--limit", "1000") in slack_thread.reads


def test_a_part_delivered_in_another_channel_is_recorded_where_it_was_made_and_keeps_no_promised_time(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch
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
    report_records(monkeypatch, {"661300": {"part": parts[1]}})
    assert run("order", "posted", "o1", told, "--record", "661300") == (
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


def test_a_line_in_the_thread_of_another_alert_on_an_incident_is_refused_until_the_alert_joins_it_then_counts_on_either_order(
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
    assert run("order", "posted", "o2", line) == (0, f"o2 progress line, no part delivered: {line}")
    assert run("order", "posted", "o1", line) == (0, f"o1 progress line, no part delivered: {line}")


def test_a_line_in_the_thread_of_an_order_outside_the_incident_is_refused_naming_that_order_and_no_join(
    run: Run, demo: str, slack_thread: "Thread", capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL errors on plat", "--thread", "1794100000.000100", "--incident", "--parts", "fix", "first check")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "why the copy stalls", "--thread", "1794200000.000100")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    slack_thread.messages.append({"ts": "1794200000.000100", "thread_ts": "1794200000.000100", "from_user": True})
    line = slack_thread.post(state.now(), from_claude=True)
    assert cli.main(["order", "posted", "o1", line]) == 2
    refused = capsys.readouterr().err
    assert f"; that thread is o2's, so a line about o2 is recorded on it with drive order posted o2 {line}" in refused
    assert "--joins" not in refused


def test_a_line_in_the_thread_of_the_incident_an_alert_joined_keeps_the_alerts_promised_time_once_no_job_carries_it(
    run: Run, demo: str, slack_thread: "Thread"
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL errors on plat", "--thread", "1794200000.000100", "--incident", "--parts", "fix", "first check")
    alert = "SandSQL replica lanes unproven on staging"
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", alert, "--from", "slack:C0ALERTS1/1794300000.000100", "--thread", "1794300000.000100", "--joins", "o1", "--due", "+30m")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
        state.carry(data, "j1", "o2", list(data["orders"]["o2"]["parts"]))
        state.drop_carries(data, "j1")
    line = slack_thread.post(state.now() + timedelta(seconds=1), from_claude=True)
    assert run("order", "posted", "o2", line) == (0, f"o2 progress line, no part delivered: {line}")
    assert state.load("demo")["orders"]["o2"]["kept"] == slack.sent_at(slack_thread.messages[-1]).isoformat()


def test_an_alert_saved_before_joins_was_kept_loads_joined_to_the_incident_its_owner_job_carries_it_for(run: Run, demo: str) -> None:
    run("order", "add", "--project", "p", "--workstream", "w", "SandSQL errors on plat", "--incident", "--parts", "fix", "first check")
    run("order", "add", "--project", "p", "--workstream", "w", "SandSQL error log count", "--joins", "o1")
    run("order", "add", "--project", "p", "--workstream", "w", "SandSQL restore runs retrying", "--joins", "o2")
    with state.locked("demo"):
        saved = json.loads(state.state_path("demo").read_text())
        assert [saved["orders"][order_id]["joins"] for order_id in ("o1", "o2", "o3")] == [None, "o1", "o1"]
        for order in saved["orders"].values():
            del order["joins"]
        saved["jobs"]["j1"]["carries"]["o2"] = ["SandSQL error log count"]
        saved["orders"]["o2"]["jobs"].append("j1")
        state.state_path("demo").write_text(json.dumps(saved))
    data = state.load("demo")
    assert [data["orders"][order_id]["joins"] for order_id in ("o1", "o2", "o3")] == [None, "o1", None]
    assert state.incident_orders(data, "o2") == ["o1", "o2"]


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
    fix = "ship the background-thread fix"
    reports = {
        "21": ({"close": "o1"}, moment - timedelta(seconds=35)),
        "22": ({"close": "o1"}, moment + timedelta(minutes=2)),
        "23": ({"close": "o2", "part": state.whole_order(fix)}, moment + timedelta(seconds=30)),
    }
    monkeypatch.setattr(
        cli.reads,
        "run",
        lambda argv, checkout: json.dumps({"seq": argv[2], "lane": "root", "at": reports[argv[2]][1].isoformat(), "fields": {"attrs": reports[argv[2]][0]}}),
    )
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
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    attrs = {
        "10": {"orders": "o1", "thread": "1794200000.000100"},
        "11": {"orders": "o1", "close": "o2"},
        "12": {"close": "o2 o1"},
        "13": {"close": "o2,o3, o4"},
    }
    monkeypatch.setattr(cli.reads, "run", lambda argv, checkout: json.dumps({"seq": argv[2], "lane": "root", "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": attrs[argv[2]]}}))
    for text in ("fix the watch", "fix the handler", "fix the say tool", "fix the card"):
        run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", text)
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    assert run("order", "set", "o1", "--state", "done")[0] == 2
    assert cli.main(["order", "set", "o1", "--state", "done", "--record", "10"]) == 2
    assert "record 10 has no close attr, so it closes no order: a report names each order its post finishes in its close attr" in capsys.readouterr().err
    assert run("order", "set", "o1", "--state", "done", "--record", "11")[0] == 2
    assert state.load("demo")["orders"]["o1"]["state"] == "open"
    assert run("order", "set", "o1", "--state", "done", "--record", "12")[0] == 0
    assert state.load("demo")["orders"]["o1"]["state"] == "done"
    for order_id in ("o2", "o3", "o4"):
        assert run("order", "set", order_id, "--state", "done", "--record", "13")[0] == 0
    assert [state.load("demo")["orders"][order_id]["state"] for order_id in ("o2", "o3", "o4")] == ["done", "done", "done"]


def note_in(slack_thread: "Thread", ts: str, thread: str = "1794400100.000200", channel: str = "C0REPORTS01") -> str:
    slack_thread.messages.append({"ts": ts, "thread_ts": thread, "from_claude": True})
    return f"https://acme.slack.com/archives/{channel}/p{ts.replace('.', '')}?thread_ts={thread}"


def test_an_order_with_a_reporter_closes_done_only_once_its_one_fix_live_note_is_recorded_and_one_without_closes_as_before(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "p", "--workstream", "w", "fix the stuck staging deploy", "--thread", "1794200000.000100", "--reporter", REPORTED)
    run("order", "add", "--project", "p", "--workstream", "w", "fix the watch", "--thread", "1794200000.000100")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    report_records(monkeypatch, {"30": {"close": "o1,o2"}})
    assert cli.main(["order", "set", "o1", "--state", "done", "--record", "30"]) == 2
    assert f"o1: refused, its bug was reported in {REPORTED}, and no fix-live note there is recorded." in capsys.readouterr().err
    assert run("order", "set", "o2", "--state", "done", "--record", "30")[0] == 0
    note = note_in(slack_thread, "1794418000.000100")
    assert cli.main(["order", "told", "o1", note]) == 2
    assert "o1: refused, it keeps no proof that its fix is live, so a note now would come before the fix" in capsys.readouterr().err
    run("order", "set", "o1", "--proof", "https://buildkite.com/forge/release/builds/14185")
    assert cli.main(["order", "told", "o1", slack_thread.post(state.now(), from_claude=True)]) == 2
    assert f"is not the drive's post in {REPORTED}, the thread the bug was reported in" in capsys.readouterr().err
    assert run("order", "told", "o1", note) == (0, f"o1: told in the reporter's thread, {note}")
    assert cli.main(["order", "told", "o1", note_in(slack_thread, "1794418060.000100")]) == 2
    assert f"o1: refused, the thread its bug was reported in heard it is fixed once, in {note}, and hears it only once" in capsys.readouterr().err
    assert run("order", "set", "o1", "--state", "done", "--record", "30")[0] == 0
    assert [state.load("demo")["orders"][order_id]["state"] for order_id in ("o1", "o2")] == ["done", "done"]


def test_a_reporter_the_bot_cannot_reach_is_told_through_him_and_only_his_words_take_a_reporter_off(
    run: Run, demo: str, slack_thread: "Thread", capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["order", "add", "--project", "p", "--workstream", "w", "fix it", "--reporter", "C0REPORTS01/1794400100.000200"]) == 2
    assert "refused, name the bug report by its Slack permalink or as slack:<channel>/<its thread's ts>" in capsys.readouterr().err
    for words in ("fix the deploy", "fix the cache"):
        run("order", "add", "--project", "p", "--workstream", "w", words, "--thread", "1794200000.000100", "--reporter", "slack:C0REPORTS01/1794400100.000200")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    run("order", "set", "o1", "--proof", "https://buildkite.com/forge/release/builds/14185")
    instead = slack_thread.post(state.now(), from_claude=True)
    assert run("order", "told", "o1", instead, "--unreachable") == (0, f"o1: said to him in place of the reporter's thread, {instead}")
    assert "out of the bot's reach, so he was told in" in run("order", "show", "o1")[1]
    ours = slack_thread.post(state.now() - timedelta(minutes=2), from_claude=True)
    his = slack_thread.post(state.now() - timedelta(minutes=1), from_user=True)
    assert run("order", "set", "o2", "--no-reporter", ours)[0] == 2
    assert run("order", "set", "o2", "--no-reporter", his)[0] == 0
    assert state.load("demo")["orders"]["o2"]["reporter"] is None


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


def test_only_the_communicator_marks_an_answer_posted_and_the_coordinator_is_told_to_send_a_report_record(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "why?")
    assert cli.main(["order", "posted", "o1", "https://acme.slack.com/archives/D1/p2"]) == 2
    assert (
        "only the communicator records a post in an order's thread or takes back a part's mark: send the post's facts as a report record"
        " to communicator@demo, its part or close attr naming what it delivers, and the communicator's handler posts it and records it"
    ) in capsys.readouterr().err
    assert state.load("demo")["orders"]["o1"]["parts"]["why?"]["posted"] is None


def report_records(monkeypatch: pytest.MonkeyPatch, attrs: dict[str, dict], lane: str = "root") -> None:
    monkeypatch.setattr(
        cli.reads, "run", lambda argv, checkout: json.dumps({"seq": argv[2], "lane": lane, "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": attrs[argv[2]]}})
    )


def test_a_part_is_delivered_only_by_the_report_record_that_names_it_and_a_post_with_none_is_a_progress_line(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "p", "--workstream", "w", "fix the unfurl", "--thread", "1794200000.000100", "--parts", "fix")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    report_records(monkeypatch, {"661300": {"part": "fix"}, "661301": {}})
    line = slack_thread.post(state.now(), from_claude=True)
    assert cli.main(["order", "posted", "o1", line, "--part", "fix"]) == 2
    assert (
        '--part "fix": refused, no report record names it; a post delivers a part only through the report record whose part attr names it:'
        " pass --record <that record's seq>"
    ) in capsys.readouterr().err
    assert cli.main(["order", "posted", "o1", line, "--part", "fix", "--record", "661301"]) == 2
    assert "record 661301's part attr names none" in capsys.readouterr().err
    assert run("order", "posted", "o1", line) == (0, f"o1 progress line, no part delivered: {line}")
    assert state.load("demo")["orders"]["o1"]["parts"]["fix"]["posted"] is None
    assert run("order", "posted", "o1", line, "--record", "661300") == (0, f'o1 "fix": posted {line}')
    assert state.load("demo")["orders"]["o1"]["parts"]["fix"]["posted"] == line


def test_order_posted_on_the_communicators_own_order_takes_the_report_its_handler_wrote_from_the_communicators_lane(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "p", "--workstream", "w", "which page shows a run's history?", "--thread", "1794200000.000100", "--owner", "communicator")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    line = slack_thread.post(state.now(), from_claude=True)
    report_records(monkeypatch, {"664348": {"close": "o1"}})
    assert cli.main(["order", "posted", "o1", line, "--record", "664348"]) == 2
    assert (
        "refused, the report is the coordinator's and o1 is the communicator's order, so it delivers nothing there: post your own report from the"
        ' communicator\'s lane, cci post --kind report --lane communicator --attr close=o1 "<its first words>" with no --to, carrying this report\'s'
        " facts, and pass that seq, or post with no record, a progress line"
    ) in capsys.readouterr().err
    assert state.load("demo")["orders"]["o1"]["parts"]["which page shows a run's history?"]["posted"] is None
    report_records(monkeypatch, {"664350": {"close": "o1"}}, lane="communicator")
    assert run("order", "posted", "o1", line, "--record", "664350") == (0, f'o1 "which page shows a run\'s history?": posted {line}')


def test_start_outside_an_orca_terminal_is_refused_naming_the_command_that_starts_it_in_one(
    run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.delenv("ORCA_TERMINAL_HANDLE")
    monkeypatch.delenv("TMUX")
    assert cli.main(["start", "demo", "--role", "coordinator"]) == 2
    refused = capsys.readouterr().err
    assert "drive start demo --role coordinator: refused, this session runs in no Orca terminal, so he cannot open it from Orca" in refused
    assert f"orca terminal create --worktree path:{tmp_path} --title demo-coordinator" in refused
    assert not state.exists("demo")
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "term_outer")
    monkeypatch.setenv("TMUX", "/private/tmp/tmux-501/default,1,0")
    assert run("start", "demo", "--role", "coordinator")[0] == 0


def test_a_drive_tooling_order_leaves_out_its_thread_since_its_posts_go_at_his_dm_root(run: Run, demo: str) -> None:
    tooling = ["order", "add", "--project", "p", "--workstream", "w", "Drive tooling: fix ask"]
    rootless = '--thread 1794378983.440769 left out: a "Drive tooling:" order\'s posts go at his DM root, in no thread'
    assert run(*tooling, "--thread", "1794378983.440769") == (0, f"o1\n{rootless}")
    code, out = run("order", "set", "o1", "--thread", "1794378983.440769")
    assert (code, out.splitlines()[0]) == (0, rootless)
    assert state.load("demo")["orders"]["o1"]["thread"] is None


def test_a_drive_tooling_order_his_words_already_hold_adds_none(run: Run, demo: str) -> None:
    tooling = ["order", "add", "--project", "p", "--workstream", "w", "Drive tooling: fix ask"]
    assert run(*tooling) == (0, "o1")
    assert run(*tooling) == (0, "o1\no1 already holds these words, so no order was added")
    assert list(state.load("demo")["orders"]) == ["o1"]


def drive_terms(text: str, asked: list[str]) -> list[str]:
    return [f"1:{found.start() + 1} drive-term {found[0]!r}: say what happens to the reader's work instead" for found in re.finditer(r"(?<![\w./:-])[oj]\d{2,}\b", text)]


@pytest.mark.parametrize(
    ("flagged", "reworded"),
    [
        (
            "Drive tooling: the thread reader cannot open a teammate's session (defect 661904, j844): its script takes only a local path",
            "Drive tooling: the thread reader cannot open a teammate's session: its script takes only a local path",
        ),
        (
            "Drive tooling: say refuses the PR card of o341, whose words check-post refuses",
            "Drive tooling: say refuses a PR card whose tooling order's words check-post refuses",
        ),
    ],
)
def test_a_drive_tooling_orders_words_pass_check_post_since_his_pr_card_quotes_them(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, flagged: str, reworded: str
) -> None:
    monkeypatch.setattr(slack, "check_post", drive_terms)
    filed = ["order", "add", "--project", "p", "--workstream", "w"]
    assert cli.main([*filed, flagged]) == 2
    refused = capsys.readouterr().err
    assert 'a "Drive tooling:" order\'s words reach him verbatim, quoted on the line above its PR card' in refused
    assert refused.endswith("\n".join(drive_terms(flagged, [])) + "\n")
    assert run(*filed, reworded) == (0, "o1")
    assert run(*filed, "send j844's numbers to o341's thread") == (0, "o2")
    with state.edit("demo") as data:
        data["orders"]["o1"]["text"] = flagged
    assert cli.main(["order", "set", "o1", "--words", flagged]) == 2
    assert capsys.readouterr().err == refused
    assert cli.main(["order", "set", "o1", "--words", "the thread reader opens a teammate's session"]) == 2
    assert cli.main(["order", "set", "o2", "--words", "Drive tooling: send the numbers"]) == 2
    assert capsys.readouterr().err.count('--words: refused, only a "Drive tooling:" order\'s words change') == 2
    assert run("order", "set", "o1", "--words", reworded)[0] == 0
    assert [order["text"] for order in state.load("demo")["orders"].values()] == [reworded, "send j844's numbers to o341's thread"]


def test_a_proof_is_the_checks_link_file_or_output_never_a_claim(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    run("order", "add", "--project", "p", "--workstream", "w", "unquarantine the flaky test")
    assert cli.main(["order", "set", "o1", "--state", "done", "--proof", "unquarantined"]) == 2
    assert '--proof "unquarantined": refused, a proof is the check itself' in (refused := capsys.readouterr().err)
    assert "rerun the check and paste its output, or link its run" in refused
    assert state.load("demo")["orders"]["o1"]["state"] == "open"
    saved = tmp_path / "check.txt"
    saved.write_text("12 passed")
    for proof in ("https://github.com/acme/app/actions/runs/123", f"saved at {saved}", "$ pytest tests/test_flaky.py\n12 passed in 3.1s"):
        assert run("order", "set", "o1", "--proof", proof)[0] == 0
        assert state.load("demo")["orders"]["o1"]["proof"] == proof


def test_an_order_limited_to_options_starts_no_builder_or_codex_job_until_he_picks(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "p", "--workstream", "w", "give me options for the cache layout", "--options-only")
    for kind in ("builder", "codex"):
        assert cli.main(["job", "add", kind, "--order", "o1", "--goal", "build it"]) == 2
        refused = capsys.readouterr().err
        assert f"o1 is limited to options: he picks one before the proper fix is built, so no {kind} starts on it" in refused
        assert "drive order set o1 --no-options-only" in refused
    assert run("job", "add", "reader", "--order", "o1", "--goal", "research the options") == (0, "j1")
    assert run("job", "add", "designer", "--order", "o1", "--goal", "draw his options")[0] == 0
    run("order", "set", "o1", "--no-options-only")
    assert run("job", "add", "builder", "--order", "o1", "--goal", "build his pick")[0] == 0


def test_an_incident_order_is_never_limited_to_options_since_its_codex_jobs_start_at_the_alert(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    for joined in ([], ["--joins", "o1"]):
        assert cli.main(["order", "add", "--project", "p", "--workstream", "w", "give me options only", "--incident", "--parts", "fix", "--options-only", *joined]) == 2
        assert "--options-only: refused, an incident order is never limited to options, since its codex jobs start at the alert" in capsys.readouterr().err
    assert (state.load("demo")["orders"], state.load("demo")["jobs"]) == ({}, {})
    run("order", "add", "--project", "p", "--workstream", "w", "errors on the api", "--incident", "--parts", "fix")
    run("order", "add", "--project", "p", "--workstream", "w", "give me options for the api fix", "--parts", "fix", "--options-only")
    run("job", "add", "reader", "--order", "o2", "--goal", "research the options", "--part", "fix")
    for changed in (["o1", "--options-only"], ["o2", "--incident"]):
        assert cli.main(["order", "set", *changed]) == 2
        assert f"{changed[0]}: refused, an incident order is never limited to options" in capsys.readouterr().err
    orders = state.load("demo")["orders"]
    assert [(order["incident"], order["options_only"]) for order in orders.values()] == [(True, False), (False, True)]
    assert run("order", "set", "o2", "--incident", "--no-options-only")[0] == 0


def test_an_order_saved_before_options_only_and_once_loads_as_neither(run: Run, demo: str) -> None:
    run("order", "add", "--project", "p", "--workstream", "w", "fix the watch")
    with state.locked("demo"):
        saved = json.loads(state.state_path("demo").read_text())
        for field in ("options_only", "once"):
            del saved["orders"]["o1"][field]
        state.state_path("demo").write_text(json.dumps(saved))
    order = state.load("demo")["orders"]["o1"]
    assert (order["options_only"], order["once"]) == (False, False)


def test_a_repair_he_asked_to_run_once_puts_up_no_pr(run: Run, demo: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--project", "p", "--workstream", "w", "rerun the backfill once", "--once")
    run("job", "add", "builder", "--order", "o1", "--goal", "run the backfill")
    assert cli.main(["pr", "add", PR, "--job", "j1"]) == 2
    assert "refused, o1 is a repair he asked to run once: run it, and put up no PR for it" in capsys.readouterr().err
    assert state.load("demo")["prs"] == {}
    run("order", "set", "o1", "--no-once")
    assert run("pr", "add", PR, "--job", "j1") == (0, "Forge-AI/monorepo#42")


def test_status_names_the_open_incidents_and_each_running_job_whose_session_ended(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "p", "--workstream", "w", "errors on the api", "--incident", "--parts", "fix", "first check")
    run("order", "set", "o1", "--note", "record 3fa2c1")
    run("order", "add", "--project", "p", "--workstream", "w", "fix the watch")
    run("job", "add", "builder", "--order", "o2", "--goal", "fix it")
    lines = run("status")[1].splitlines()
    assert lines[4:6] == [
        "Open incident orders; an alert on the same failing service joins one with drive order add --joins <o>:",
        "o1 open incident: errors on the api; record 3fa2c1",
    ]
    assert not [line for line in lines if "no live agent" in line]
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "after-restart")
    run("start", "demo", "--role", "coordinator")
    gone = [f"{job_id} running with no live agent, its session gone: start a fresh one with drive job brief {job_id}" for job_id in ("j1", "j2", "j3")]
    assert run("status")[1].splitlines()[6:9] == gone
    assert run("status", "o2")[1].splitlines()[1] == gone[2]


def test_heard_prints_one_word_with_orders_in_use(run: Run, demo: str) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "fix the watch")
    run("order", "add", "--project", "Billing", "--workstream", "Invoices", "why is the invoice late?")
    assert run("heard") == (0, "heard")
    assert run("heard", "--away") == (0, "away")
    assert "in_use" not in json.loads(run("heard", "--json")[1])


def test_heard_reads_nothing_from_github_so_a_refusal_there_never_leaves_him_unheard(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "fix the watch")
    monkeypatch.setattr(github, "run", lambda *args, **kwargs: pytest.fail("drive heard read GitHub"))
    monkeypatch.setattr(card, "run", lambda *args, **kwargs: pytest.fail("drive heard read GitHub"))
    assert run("heard") == (0, "heard")
    assert state.load(demo)["owner"]["heard"] is not None


def test_heard_refuses_his_words_repeating_a_part_no_live_job_carries(run: Run, demo: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "ship the unfurl fix and its check", "--parts", "fix", "check")
    assert run("heard")[0] == 0
    owner = state.load("demo")["owner"]
    assert cli.main(["heard", "--repeats", "o1", "--part", "fix"]) == 2
    refusal = capsys.readouterr().err
    assert 'his words repeat o1 "ship the unfurl fix and its check", part "fix", and no running, waiting or built job or open card of his carries it' in refusal
    assert 'start its job with drive job add <kind> --order o1 --part <part> --goal "<its deliverable>", file a defect with this refusal verbatim' in refusal
    assert state.load("demo")["owner"] == owner
    assert cli.main(["heard", "--part", "fix"]) == 2
    assert "pass --repeats <o> with it" in capsys.readouterr().err
    assert cli.main(["heard", "--repeats", "o1"]) == 2
    assert 'o1 has parts "fix", "check"; name the one this covers with --part' in capsys.readouterr().err
    run("job", "add", "builder", "--order", "o1", "--part", "fix", "--goal", "ship the fix")
    assert run("heard", "--repeats", "o1", "--part", "fix")[1].splitlines()[0] == "heard"
    with state.edit("demo") as data:
        data["jobs"]["j1"]["state"] = "waiting"
    assert run("heard", "--repeats", "o1", "--part", "fix")[0] == 0
    with state.edit("demo") as data:
        data["jobs"]["j1"]["state"] = "done"
    assert cli.main(["heard", "--repeats", "o1", "--part", "fix"]) == 2


def test_heard_takes_a_part_a_done_jobs_card_still_open_waits_on(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Releases", "ship the unfurl fix and its check", "--parts", "fix", "check")
    run("job", "add", "design", "--order", "o1", "--part", "fix", "--goal", "pick how the card unfurls")
    run("job", "set", "j1", "--card", DESIGN_CARD)
    with state.edit("demo") as data:
        data["jobs"]["j1"]["state"] = "done"
    question = {"question": "Which unfurl?", "options": ["Bot", "Link"], "recommended": "Bot", "asked": state.stamp(), "url": DESIGN_CARD, "card": {"title": "Which unfurl?"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [question], "pr_cards": []})
    view.refresh_cards("demo")
    assert run("heard", "--repeats", "o1", "--part", "fix")[1].splitlines()[0] == "heard"
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS)
    view.refresh_cards("demo")
    assert run("heard", "--repeats", "o1", "--part", "fix")[0] == 2


def test_drive_move_takes_his_word_records_it_on_the_role_and_prints_steps_two_to_four(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(orca, "claude_pids", lambda session: [4242])
    assert cli.main(["move", "coordinator"]) == 2
    assert "refused, ending a session is his decision: pass --his-word <the permalink of his message naming the move>" in capsys.readouterr().err
    ours = slack_thread.post(state.now() - timedelta(minutes=2), from_claude=True)
    assert cli.main(["move", "coordinator", "--his-word", ours]) == 2
    assert f"{ours} is not his message; only his own words move a session" in capsys.readouterr().err
    his = slack_thread.post(state.now() - timedelta(minutes=1), from_user=True)
    run("order", "add", "--project", "Billing", "--workstream", "Invoices", "the invoice run fails")
    with state.edit("demo") as data:
        data["orders"]["o1"]["incident"] = True
        data["roles"]["coordinator"]["terminal"] = "term_coordinator"
    assert cli.main(["move", "coordinator", "--his-word", his]) == 2
    assert "refused, incident o1 is open and a move ends the coordinator's session: move it once the fix is live" in capsys.readouterr().err
    assert "move_word" not in state.load("demo")["roles"]["coordinator"]
    with state.edit("demo") as data:
        data["orders"]["o1"]["incident"] = False
    code, out = run("move", "coordinator", "--his-word", his)
    held = state.load("demo")["roles"]["coordinator"]
    assert (code, held["move_word"]) == (0, his)
    assert out.splitlines() == [
        f"1. his word {his} recorded on the coordinator",
        (
            '2. cci post --lane communicator --kind ask --to root@demo --text "Moving you into an Orca terminal on his word: run cc-slack'
            " unfollow for each of your Slack watches, cci unsubscribe, stop your drive watch Monitor if you run one, reply released, and"
            ' take no more work." and wait for its reply'
        ),
        "3. orca terminal send --terminal term_coordinator --text /exit --enter",
        "4. wait until ps -p 4242 lists nothing",
        f"then steps 5 to 7 of drive manual migration, Into an Orca terminal: create the terminal resuming {held['session']} in {held['cwd']}",
    ]


def test_drive_move_reads_a_role_recorded_before_roles_kept_a_terminal_as_in_none(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orca, "claude_pids", lambda session: [])
    his = slack_thread.post(state.now() - timedelta(minutes=1), from_user=True)
    with state.edit("demo") as data:
        del data["roles"]["coordinator"]["terminal"]
    code, out = run("move", "coordinator", "--his-word", his)
    session = state.load("demo")["roles"]["coordinator"]["session"]
    assert code == 0
    assert out.splitlines()[2:4] == [
        f"3. tmux send-keys -t <pane> /exit Enter under tmux, its pane the one tmux list-panes -a lists with its claude line, or claude stop {session} for a claude --bg session",
        f"4. wait none: no ~/.claude/sessions/<pid>.json names {session}, so its process has exited",
    ]


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
ANSWERED = ("card-answer", "--card", DESIGN_CARD, "--label", WORDS, "--settled", "--other", "--via", PICK)
MATCHED, BACKFILL = settled.matched, settled.backfill


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
    assert "  demo:j2-7  " not in listed
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
    assert (code, settled, closed) == (0, [ANSWERED, ("card-answer", "--card", TAP_CARD, "--label", WORDS, "--withdrawn", "--via", PICK)], [PR])
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


def test_an_answer_tapped_on_a_pr_card_is_recorded_as_an_approval_and_his_words_are_not(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cards_asking(monkeypatch, PR)
    added: list[list[str]] = []

    def ccn(argv: list[str], input: str, **_: object) -> subprocess.CompletedProcess[str]:
        added.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"id": f"{len(added)}eb09e5f00d"}), "")

    monkeypatch.setattr("drive.settled.subprocess", types.SimpleNamespace(run=ccn, TimeoutExpired=subprocess.TimeoutExpired))
    run("answer", "--title", "New: land it?", "--words", "Approve", "--link", TAP_CARD)
    run("answer", "--title", "Which topology?", "--words", WORDS, "--link", PICK)
    labels = [[arg for arg in argv if arg.startswith("--label=")] for argv in added]
    assert labels == [
        ["--label=scope:durable", "--label=drive:demo", "--label=kind:approval"],
        ["--label=scope:durable", "--label=drive:demo"],
    ]


def test_the_settled_check_skips_his_taps_approving_a_pr_and_still_lists_three_of_his_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[list[str]] = []
    found = [
        {"id": "a1" * 20, "title": "Tap: #50203, canary diagnostics on the search", "tags": ["scope:durable", "kind:approval"]},
        {"id": "b2" * 20, "title": "Which store holds a run's files?", "tags": ["scope:durable"]},
        {"id": "c3" * 20, "title": "Where does a run's map sit?", "tags": ["scope:durable", "drive:demo"]},
        {"id": "d4" * 20, "title": "Approve #21378 to land?", "tags": ["scope:durable", "kind:approval"]},
        {"id": "e5" * 20, "title": "How many files does a run keep?", "tags": ["scope:durable"]},
        {"id": "f6" * 20, "title": "When is a run's snapshot written?", "tags": ["scope:durable"]},
    ]

    def ccn(argv: list[str], input: str, **_: object) -> subprocess.CompletedProcess[str]:
        asked.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(found), "")

    monkeypatch.setattr("drive.settled.subprocess", types.SimpleNamespace(run=ccn, TimeoutExpired=subprocess.TimeoutExpired))
    assert [answer["id"][:2] for answer in MATCHED("where do a run's files go", "/work")] == ["b2", "c3", "e5"]
    assert asked == [["ccn", "answer", "match", "-", "--label", "scope:durable", "--limit", "-1", "--json"]]


def test_the_settled_check_finds_his_answer_ranked_past_twenty_of_his_taps_approving_a_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    taps = [{"id": f"{tap:02}" * 20, "title": f"Tap: #5020{tap}, a run's files move to the store", "tags": ["scope:durable", "kind:approval"]} for tap in range(20)]
    ruling = {"id": "b2" * 20, "title": "Which store holds a run's files?", "tags": ["scope:durable"]}

    def ccn(argv: list[str], input: str, **_: object) -> subprocess.CompletedProcess[str]:
        limit = int(argv[argv.index("--limit") + 1])
        ranked = [*taps, ruling]
        return subprocess.CompletedProcess(argv, 0, json.dumps(ranked if limit < 0 else ranked[:limit]), "")

    monkeypatch.setattr("drive.settled.subprocess", types.SimpleNamespace(run=ccn, TimeoutExpired=subprocess.TimeoutExpired))
    assert [answer["id"][:2] for answer in MATCHED("where do a run's files go", "/work")] == ["b2"]


def test_backfill_labels_as_approvals_only_his_taps_approving_a_pr_recorded_before_drive_answer_labelled_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    stored = [
        {"id": "a1" * 20, "title": "Tap: #50203, canary diagnostics on the search", "body": "Approve at 4aca314 (1:03 PM)", "tags": ["scope:durable"]},
        {"id": "b2" * 20, "title": "Approve #21378 to land? https://github.com/acme/app/pull/21378", "body": "Approve, label merge (Recommended)", "tags": ["scope:durable"]},
        {"id": "c3" * 20, "title": "Tap: #50134, runs resume from their snapshot", "body": "Approve at 704de36", "tags": ["scope:durable", "kind:approval"]},
        {"id": "d4" * 20, "title": "Tap: #48654. Does a converted chat need its own records?", "body": "Not approved as built. \"convert it like any other\"", "tags": ["scope:durable"]},
        {"id": "e5" * 20, "title": "Which store holds the blobs?", "body": "Approve the blob store as drawn", "tags": ["scope:durable"]},
        {"id": "f6" * 20, "title": "For #123, which service owns snapshots?", "body": "Approve the design: snapshot-index owns all snapshots; never store them in the gateway.", "tags": ["scope:durable"]},
        {"id": "a7" * 20, "title": "PR approvals tapped 10:12 AM Nov 5: #50861, #50795", "body": "Approve on #50861 and #50795.", "tags": ["scope:durable"]},
    ]

    def ccn(argv: list[str], input: str, cwd: str, **_: object) -> subprocess.CompletedProcess[str]:
        calls.append([cwd, *argv])
        return subprocess.CompletedProcess(argv, 0, json.dumps(stored) if argv[2] == "list" else "{}", "")

    monkeypatch.setattr("drive.settled.subprocess", types.SimpleNamespace(run=ccn, TimeoutExpired=subprocess.TimeoutExpired))
    BACKFILL("/work")
    assert calls == [
        ["/work", "ccn", "answer", "list", "--label", "scope:durable", "--limit", "0", "--full", "--json"],
        ["/work", "ccn", "answer", "edit", "a1" * 20, "--add-label", "kind:approval"],
        ["/work", "ccn", "answer", "edit", "b2" * 20, "--add-label", "kind:approval"],
        ["/work", "ccn", "answer", "edit", "a7" * 20, "--add-label", "kind:approval"],
    ]


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
        "Forge-AI/monorepo#45 no longer waits on his tap until j1 reworks it",
        "Forge-AI/monorepo#45 reopened by his answer a1: j1 reworks it to his words, so brief j1 with them",
    ]
    assert (code, settled) == (0, [ANSWERED, ("card-answer", "--card", TAP_CARD, "--label", WORDS, "--withdrawn", "--via", PICK)])
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
    run("pr", "add", STACKED, "--job", "j1")
    settled = cards_asking(monkeypatch, PR, STACKED)
    assert run("answer", "a1", "--words", WORDS, "--link", PICK)[0] == 2
    assert run("answer", "a1", "--superseded", f"{designing}=stop")[0] == 2
    assert settled == [ANSWERED]
    assert run("answer", "a1", "--superseded", f"{designing}=stop", "Forge-AI/monorepo#43=stop", "--others-stand")[0] == 0
    assert len(settled) == 2
    assert [pr["wants_tap"] for pr in state.load("demo")["prs"].values()] == [False, False]


def test_cards_withdraw_settles_his_open_card_with_his_words_and_refuses_a_card_not_open(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    shown = open_card(7, {"title": "Which cache disk?", "options": ["Lazy", "Bigger"], "recommended": "Lazy", "waits": "demo:j1-7"})
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": []})
    calls: list[tuple] = []
    monkeypatch.setattr(slack, "cc_slack", lambda *args: calls.append(args) or {"answered": True})
    words, via = "We're already addressing this with the lazy + restate object", "https://acme.slack.com/archives/D123/p1794300000000999"
    code, line = run("cards", "withdraw", "demo:j1-7", "--words", words, "--via", via)
    assert (code, calls) == (0, [("card-answer", "--card", shown["url"], "--label", words, "--withdrawn", "--via", via)])
    assert line == f"withdrew his card {shown['url']} (demo:j1-7): it shows his words and a link to them, with no buttons; j1 asks again on a new card once its work is back"
    assert cli.main(["cards", "withdraw", "demo:j1-8", "--words", words, "--via", via]) == 2
    assert "demo:j1-8: not one of his open question cards" in capsys.readouterr().err


def test_a_ruling_records_only_with_the_tooling_order_rewriting_the_skill_to_it(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": []})
    view.refresh_cards(demo)
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "design the run page")
    ruling = ["answer", "--title", "How should cards read?", "--words", "Cards say why", "--link", "https://acme.slack.com/archives/D123/p1", "--label", "ruling"]
    for extra in ([], ["--rewrite", "o1"]):
        assert cli.main([*ruling, *extra]) == 2
        assert "refused, nothing recorded: a ruling's skill rewrite is owed the moment the ruling is recorded" in capsys.readouterr().err


def test_a_pr_carrying_a_code_ruling_is_refused_until_it_writes_his_words_into_the_nearest_agents_md(
    recorded: Answers, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded.shown["23ff059"] = {"id": "23ff059", "title": "How should the cache key?", "body": '"Key the cache on the run id"\nNotes: cci owner 1', "tags": ["ruling", "code:go/sand/cache"]}
    data = {"roles": {"coordinator": {"cwd": "/repo"}}, "prs": {"Forge-AI/monorepo#7": {"job": "j1"}}, "jobs": {"j1": {"brief_flags": {"ruling": ["23ff059"]}}}}
    files = [[{"filename": "go/sand/AGENTS.md", "patch": "@@ -1 +1,2 @@\n # Sand\n+- Key the cache on the run id (Nov 11)"}]]
    monkeypatch.setattr(github, "read", lambda what, cmd: json.dumps(files))
    cli.refuse_an_unwritten_rule(data, "Forge-AI/monorepo#7")
    files[0][0]["filename"] = "go/sand/other/AGENTS.md"
    with pytest.raises(state.DriveError, match='his ruling 23ff059 on how go/sand/cache must work, "Key the cache on the run id", is not written'):
        cli.refuse_an_unwritten_rule(data, "Forge-AI/monorepo#7")
