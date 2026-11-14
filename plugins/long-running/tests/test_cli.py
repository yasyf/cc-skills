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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import drive
import pytest
import spawnllm
from conftest import ACME_CUT, CARD_BODY, CUT_PICK, CUT_TAP, LEAD, LOCK_STALL, LOCK_STALL_AGAIN, POST_ASK, RESEARCH, SETTLED_LISTED, SETTLED_MATCHED, SETTLED_NOTE, SHORT_ASK, WRITTEN_AT, answered_cut, cloned, drawn_card, git_in, Answers, explained, Chrome, Draws, Drawing, Heard, Images, Jev, Judge, MetaLaunches, Redraws, Thread, WatchProcess, Withdrawals
from drive import (
    agents,
    brief,
    card,
    cli,
    github,
    install,
    land,
    limits,
    orca,
    picture,
    place,
    reads,
    refs,
    render,
    served,
    settled,
    slack,
    stage,
    state,
    superseded,
    tools,
    view,
    watch,
)

Run = Callable[..., tuple[int, str]]
UNLISTED = {"-h", "--help", "--drive", "--json"}
FLAG = re.compile(r"(?<![\w-])--[a-z][\w-]*")
PLACEHOLDER = re.compile(r"<[^<>`]*>|\"[^\"]*\"|…|\[|\]|\.\.\.")
PR = "https://github.com/Forge-AI/monorepo/pull/42"
FAKE_SELECT = """#!/bin/sh
[ "$1 $2 $3" = "release select --pr" ] || exit 64
cat >"{request}"
cat "{answer}"
"""
REPORTED = "https://acme.slack.com/archives/C0REPORTS01/p1794400100000200"
ROOT_CAUSE = "Root cause: none: where a copy runs is a pick between two designs, with no defect behind it\n"
HOLDS = "Holds: none: no option changes code any job builds\n"
DESIGN_TITLE = "Where should store_copy_at in supervisor/copies.rs run each store copy?"
SECOND_OPTION = (
    "- store_copy_at runs on a separate worker that reads each store from the bucket and copies it there\n"
    "  A new worker in `supervisor/worker.rs` reads each store from the team's bucket and calls store_copy_at there, so the primary"
    " serves only queries; each cluster runs one more process.\n"
)
DESIGN_RESEARCH = (
    f"DESIGN {DESIGN_TITLE}\n"
    "Drive tooling: your pick decides where store_copy_at runs; it copies one store, on the primary or on a separate worker that reads the bucket.\n"
    "Recommended: on the primary; held until you pick, so nothing ships meanwhile.\n"
    "- store_copy_at runs on the primary, beside the queries it serves, copying each store in list order (recommended)\n"
    "  The primary calls store_copy_at in `supervisor/copies.rs` for each store in list order between the queries it serves, writing"
    " each copy to the team's bucket for a restore to read; each query waits behind a running copy.\n"
    f"{SECOND_OPTION}Findings:\n{ROOT_CAUSE}Simplest: store_copy_at runs on the primary, beside the queries it serves, copying each store in list order\n{HOLDS}{RESEARCH.removeprefix('DONE researched the store copy\n')}"
)
DESIGNED = f"""DESIGN the store copy
Design: one copy per store, in the order the stores list
{ROOT_CAUSE}Settled: "copy one store"
Native: none: he never sees it
Exists: none: ccx code search "store copy" and rg store_copy found nothing
Change: copy each store in list order
Facts: lib/drive/state.py:18
"""
DESIGNED_IN_CHANGES = DESIGNED.replace(
    "Change: copy each store in list order\n",
    "Change: the moved-table interface\nChange: the reads through it\nChange: the writes through it (waits on change 2)\n",
)
FINDINGS_FIRST = """ASK Two design picks for the new run page: how a run's files are laid out, and what the runner keeps.

Findings:

(1) The steps log. It is a table inside every run's journal.

The two questions:

Q1. How should a run's files be laid out so the browser never guesses at a page map?
- Snapshot each finished run when it lands (recommended)
- A map file beside every journal file
"""
SPLIT_JOURNAL_ASK = """ASK How should readers reach a split run journal's record database?
Format step 16 and its sibling runner are built and pushed on branch `journal-split` at d62edf6882. A raised journal's page drops `vals`, `call_traces`, `llm_calls` and `nodes` once its record database uploads. No named answer says how the five readers of those tables reach them afterwards. Recommended: the journal engine attaches the record, so no reader's SQL changes. Meanwhile, runners stay at format 15, so nothing splits yet.
Show me:
Where each reader's reads of the four moved tables go, per option
```compare
| Reader | The journal engine attaches the record | Each reader routes by table | Only the run server reads the record |
|---|---|---|---|
| journal-next views, about 20 queries | unchanged; the read attaches `record` | each query picks page or record | unchanged; the read attaches `record` |
| Go runread and runpkg, through the data API | unchanged; the server attaches `record` | two connections; meteredSQL and LoadSeal split in two | unchanged; the server attaches `record` |
| Run server follower | follows the record beside the page | follows the record beside the page | follows the record beside the page |
| Browser, wasm Journal.paged | a second paged file attached as `record` | a second JournalDb; PROGRAM_SQL, TRACE_COLUMNS and LoadSeal rerouted | calls, values and nodes fetched from runtime-api |
| Raw SQL over a run | sees all four tables | sees the page tables only | sees all four, on the server only |
```
- The journal engine attaches the record: every reader's SQL stays as written; it costs one attach path natively and in wasm (recommended)
  Does: a read of a page with no `vals` table attaches `<page key>-record` as schema `record`. SQLite resolves the four table names there. The Go follower follows the record beside a split page, and the browser follows it as a second paged lane attached to the page.
  Data: the four tables in the record database `<page key>-record`, followed lazily: its first segment, then the segments a read touches. No new tables.
  Touches: `lineage.rs` and the views crate in `sandsql-journal-next`, `read_attached` in `sandsql-server/src/data.rs`, `go/sand/next/internal/sandsql/local/followed.go`, the wasm engine's Journal, and dashboard/src/iris/journal. No write path changes.
- Each reader routes by table: no engine change; every reader knows where each table lives and splits statements that read both
  Does: each reader holds two connections and sends a statement to the database that holds its table. A statement reading both, such as `meteredSQL` in runpkg, becomes two reads joined in Go or TypeScript.
  Data: the same two databases; each reader reads them separately.
  Touches: `views/src/lib.rs`, `go/sand/next/internal/vm_run/runread`, `go/sand/next/internal/vm/builtins/runpkg`, `dashboard/src/iris/journal/reader.ts` and `vmframe-wasm`, each carrying its own routing.
- Only the run server reads the record: one attach on the server; the browser stops reading calls, values and nodes from the bucket
  Does: the browser asks runtime-api for every call, value and node; only the server attaches the record.
  Data: the same two databases; the browser reads the page from the bucket and the record through runtime-api.
  Touches: the server code of the first option, and `dashboard/src/iris/journal`, which loses its direct bucket reads for the Calls section, values and the program.
Findings:
Root cause: none: the split is new work; it moves four tables into a second database while every reader today opens one database per run.
Holds: none: no option changes code any job builds
First principles: The journal engine attaches the record
Simplest: The journal engine attaches the record
Interim fix: none: no runner writes format 16 yet, so no reader meets a split journal.
- The card behind answer 0507794 says, in every option: "Readers attach the record only when a step, value or binding opens." No answer says how a reader attaches it.
- Some statements read both databases. `meteredSQL` at `runpkg.go:783` reads `lifecycle_events` from the page and `call_traces` from the record. `LoadSeal` at `runread/nodes.go:45` joins `progress` to `nodes`, and `views/src/lib.rs:2166` reads `call_traces` beside page tables.
- The native server already attaches other databases per read: `inspect` in `lineage.rs` attaches each fork base through `inspect_attached` and `read_attached` in `data.rs`. SQLite resolves an unqualified table name across attached schemas, so the views' SQL stays as written.
- The Go follower already follows extra databases beside a mount: `holdBases` in `followed.go` follows each base.
- The browser opens one `engine.Journal.paged()` per run with no attach, in `dashboard/src/iris/journal/sqlite.ts`. `reader.ts:56` and `:58` read `nodes` and `call_traces`.
- Followed copies restore lazily, per `rust/crates/sandsql-replica/SPEC.md:112` and `:160`: the first segment, then only what reads touch once a database holds more than 16 MiB. So following the record reads it in pieces, as answer 8c0ed43 requires.
- The plan's S4 section, lines 392-396 of `journal-layout-bandwidth.md`, sits on the coordinator's Mac outside the drive folders, so no READ reaches it; if it settles this, its words answer this card.
"""


def test_start_creates_the_drive_and_prints_the_roles_context(run: Run, tmp_path: Path) -> None:
    code, out = run("start", "demo", "--role", "communicator")
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
    assert data["slack"] == {"page": None, "watch": []}


def test_a_coordinators_start_starts_the_watch_and_says_so(run: Run, watch_process: WatchProcess, monkeypatch: pytest.MonkeyPatch) -> None:
    run("start", "demo", "--role", "communicator")
    assert watch_process.ensured == []
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "coordinator-session")
    code, out = run("start", "demo", "--role", "coordinator")
    assert (code, watch_process.ensured) == (0, ["demo"])
    assert "\nthe watch runs for demo\ncoordinator: " in out


def test_drive_start_starts_the_meta_session_beside_the_others_once_until_it_binds(
    run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, meta_launches: MetaLaunches
) -> None:
    out = run("start", "demo", "--role", "communicator")[1]
    registered = "demo-communicator registered: cci session run keeps session session-under-test running in tmux"
    assert f"\n\n{registered}\nstarted the meta session, which holds drive tooling, in Orca terminal term_meta1\ncommunicator: " in out
    assert (meta_launches.started, state.load("demo")["launched"]) == ([("demo", str(tmp_path))], {"meta": "term_meta1"})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "coordinator-session")
    registered = "demo-coordinator registered: cci session run keeps session coordinator-session running in tmux"
    assert f"\n\n{registered}\nthe meta session starts in Orca terminal term_meta1, which an earlier drive start opened\n" in run("start", "demo", "--role", "coordinator")[1]
    meta_launches.open.clear()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "communicator-2")
    assert "in Orca terminal term_meta2\ncommunicator: " in run("start", "demo", "--role", "communicator")[1]
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "meta-session")
    run("start", "demo", "--role", "meta")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "coordinator-session")
    assert "the meta session starts in" not in run("start", "demo", "--role", "coordinator")[1]
    assert len(meta_launches.started) == 2


def test_a_sub_coordinator_binds_beside_the_top_for_a_project_an_order_is_filed_under_and_a_new_session_replaces_it(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, watch_process: WatchProcess
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
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
        "cci: run cci drive use demo, and no cci subscribe: your cci alias demo-iris owns your subscription.",
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
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
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
    for project in ("A.B", "A B", "Root", "Iris 2", "Handler", "Memory"):
        run("order", "add", "--title", "Short title", "--project", project, "--workstream", "W", f"work for {project}")
    posted_lanes.add("memory")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "ab-1")
    assert run("start", "demo", "--role", "coordinator", "--project", "A.B")[0] == 0
    refusals = {}
    for number, project in enumerate(("A B", "Root", "Iris 2", "Handler", "Memory")):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", f"other-{number}")
        assert cli.main(["start", "demo", "--role", "coordinator", "--project", project]) == 2
        refusals[project] = capsys.readouterr().err.strip()
    assert refusals == {
        "A B": f"drive: --project A B: its lane a-b is the lane of the sub-coordinator for A.B; {TAKEN}",
        "Root": f"drive: --project Root: its lane root is the drive's own root lane; {TAKEN}",
        "Iris 2": f"drive: --project Iris 2: its lane iris-2 ends in -<digits>, which cci reads as a numbered copy of lane iris; {TAKEN}",
        "Handler": f"drive: --project Handler: its lane handler is the drive's own handler lane; {TAKEN}",
        "Memory": f"drive: --project Memory: its lane memory is a lane cci records of drive demo were posted as; {TAKEN}",
    }
    first = state.load("demo")["subcoordinators"]["A.B"]["from"]
    run("order", "add", "--title", "Short title", "--project", "A.B", "--workstream", "W", "more work")
    posted_lanes.add("a-b")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "ab-2")
    assert run("start", "demo", "--role", "coordinator", "--project", "A.B")[0] == 0
    bound = state.load("demo")["subcoordinators"]
    assert ({project: held["session"] for project, held in bound.items()}, bound["A.B"]["from"]) == ({"A.B": "ab-2"}, first)


def test_a_session_holding_a_role_binds_no_sub_coordinator_and_a_sub_coordinators_session_takes_no_role(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Alerts", "fix the alert")
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
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "keep me")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "communicator-session")
    assert run("start", "demo", "--role", "communicator")[0] == 0
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "after-restart")
    code, _ = run("start", "demo", "--role", "coordinator")
    data = state.load("demo")
    assert code == 0
    assert data["roles"]["communicator"]["session"] == "communicator-session"
    assert data["roles"]["coordinator"]["session"] == "after-restart"
    assert data["orders"]["o1"]["text"] == "keep me"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "communicator-session")
    assert run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "from the communicator") == (0, "o2")


def test_each_order_has_one_owner_and_the_other_session_adds_no_job_to_it(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "fix the watch")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a verb for owners")
    assert cli.main(["order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: another", "--owner", "coordinator"]) == 2
    assert "a \"Drive tooling:\" order is the meta session's" in capsys.readouterr().err
    assert [order["owner"] for order in state.load("demo")["orders"].values()] == ["coordinator", "meta"]
    assert cli.main(["job", "add", "builder", "--order", "o2", "--goal", "build it"]) == 2
    assert "refused, no job added: o2 is the meta session's order, session not started: only the meta session adds its jobs" in capsys.readouterr().err
    assert run("job", "add", "builder", "--order", "o1", "--goal", "fix it")[1] == "j1"
    with state.edit("demo") as data:
        data["orders"]["o3"] = data["orders"]["o1"] | {"text": "an order from before owners", "owner": None, "jobs": []}
        data["orders"]["o4"] = data["orders"]["o2"] | {"owner": "communicator", "jobs": []}
    assert state.load("demo")["orders"]["o4"]["owner"] == "meta"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "communicator-session")
    run("start", "demo", "--role", "communicator")
    assert cli.main(["job", "add", "reader", "--order", "o1", "--goal", "read it"]) == 2
    assert "o1 is the coordinator's order, session session-under-test" in capsys.readouterr().err
    assert run("job", "add", "reader", "--order", "o3", "--goal", "read it")[0] == 0
    assert cli.main(["job", "add", "builder", "--order", "o2", "--goal", "build it"]) == 2
    assert "o2 is the meta session's order" in capsys.readouterr().err
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-under-test")
    assert cli.main(["job", "add", "builder", "--order", "o3", "--goal", "build it"]) == 2
    assert "o3 is the communicator's order, session communicator-session" in capsys.readouterr().err


def test_the_meta_session_holds_every_tooling_order_but_no_product_order(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a verb for owners")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "communicator-session")
    run("start", "demo", "--role", "communicator")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "--kind", "answer", "what does the watch hold")
    assert [order["owner"] for order in state.load("demo")["orders"].values()] == ["meta", "communicator"]
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "meta-session")
    assert cli.main(["start", "demo", "--role", "meta", "--project", "The drive"]) == 2
    assert "the meta session holds no project" in capsys.readouterr().err
    code, out = run("start", "demo", "--role", "meta")
    assert code == 0
    assert out.startswith("You are the meta session of drive demo: you hold drive tooling")
    assert out.splitlines()[-1] == "meta: session meta-session, in no Orca terminal"
    assert [order["owner"] for order in state.load("demo")["orders"].values()] == ["meta", "communicator"]
    assert cli.main(["order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page"]) == 2
    assert 'the meta session adds only "Drive tooling:" orders and never touches his product orders' in capsys.readouterr().err
    assert run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: cards say why") == (0, "o3")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-under-test")
    assert run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: the watch says why") == (0, "o4")
    assert cli.main(["order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: another", "--owner", "communicator"]) == 2
    assert "a \"Drive tooling:\" order is the meta session's" in capsys.readouterr().err
    assert [order["owner"] for order in state.load("demo")["orders"].values()] == ["meta", "communicator", "meta", "meta"]
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "communicator-session")
    assert cli.main(["job", "add", "reader", "--order", "o1", "--goal", "read it"]) == 2
    assert "o1 is the meta session's order, session meta-session" in capsys.readouterr().err
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "meta-session")
    assert run("job", "add", "reader", "--order", "o1", "--goal", "research: a verb for owners")[0] == 0


def test_a_drive_saved_with_one_driver_reads_as_its_coordinator(run: Run, demo: str) -> None:
    with state.locked("demo"):
        saved = state.stored("demo")
        saved["driver"] = saved.pop("roles")["coordinator"]
        state.store("demo", saved)
    data = state.load("demo")
    assert "driver" not in data
    assert data["roles"]["coordinator"]["session"] == os.environ["CLAUDE_CODE_SESSION_ID"]
    assert run("start", "demo", "--role", "communicator")[0] == 0
    saved = state.stored("demo")
    assert "driver" not in saved
    assert set(saved["roles"]) == {"communicator", "coordinator"}


def test_start_stores_watched_channels_and_a_later_watch_adds(run: Run) -> None:
    code, out = run("start", "demo", "--role", "coordinator", "--watch", "C0B75AL4XEW", "--watch", "C0B75AL4XEW")
    assert code == 0
    assert 'Hold with cc-slack subscribe --kind alerts,releases,posts --channel <id>: C0B75AL4XEW' in out
    assert state.load("demo")["slack"]["watch"] == ["C0B75AL4XEW"]
    run("start", "demo", "--role", "coordinator")
    assert state.load("demo")["slack"]["watch"] == ["C0B75AL4XEW"]
    run("start", "demo", "--role", "coordinator", "--watch", "C0822AHFY3G", "--watch", "C0B75AL4XEW")
    assert state.load("demo")["slack"]["watch"] == ["C0B75AL4XEW", "C0822AHFY3G"]


def test_start_adds_watch_to_a_drive_saved_before_watch_existed(run: Run, demo: str) -> None:
    with state.edit("demo") as data:
        del data["slack"]["watch"]
    assert run("start", "demo", "--role", "coordinator", "--watch", "C0822AHFY3G")[0] == 0
    assert state.load("demo")["slack"]["watch"] == ["C0822AHFY3G"]


def test_status_and_context_print_who_holds_each_channel(run: Run) -> None:
    run("start", "demo", "--role", "coordinator", "--watch", "C0B75AL4XEW", "--watch", "C0822AHFY3G")
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
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "fix the watch")
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


def test_status_with_no_ids_prints_a_coordinator_one_line_per_open_order_it_runs_and_nothing_of_the_others(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    top = os.environ["CLAUDE_CODE_SESSION_ID"]
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("order", "add", "--title", "Short title", "--project", "Billing", "--workstream", "Invoices", "fix the invoice")
    run("order", "add", "--title", "Short title", "--project", "Billing", "--workstream", "Invoices", "fix the watch")
    run("order", "add", "--title", "Short title", "--project", "Billing", "--workstream", "Invoices", "close the books")
    for order in ("o1", "o2", "o4"):
        run("job", "add", "builder", "--order", order, "--goal", f"build {order}")
    run("order", "set", "o4", "--state", "done")
    with state.edit("demo") as data:
        data["orders"]["o3"]["owner"] = "meta"
        for job in data["jobs"].values():
            job.update(state="running", session="gone")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", "demo", "--role", "coordinator", "--project", "Iris")

    def shown() -> tuple[list[str], list[str], str]:
        lines = run("status")[1].splitlines()
        return [line.split()[1] for line in lines if line.startswith("- o")], [line.split()[0] for line in lines if "no live agent" in line], "\n".join(lines)

    orders, agentless, text = shown()
    assert (orders, agentless) == (["o1"], ["j1"])
    assert "Open orders you run: 1, a line each and one under it for each part an ORPHAN named until it heals; drive status <id> prints one whole, with its jobs, PRs and questions:" in text
    assert "build o1" not in text and "fix the invoice" not in text
    assert "fix the invoice" in run("status", "o2")[1]
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", top)
    orders, agentless, text = shown()
    assert (orders, agentless) == (["o2"], ["j2"])
    assert "ship the run page" not in text and "fix the watch" not in text
    assert set(json.loads(run("status", "--json")[1])["orders"]) == {"o2"}
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "bound-to-none")
    assert shown()[:2] == (["o1", "o2", "o3"], ["j1", "j2", "j3"])


def test_an_orphaned_terminal_gets_the_repository_setting_and_a_shown_one_the_switch_command(
    run: Run, monkeypatch: pytest.MonkeyPatch, tmux_shown: dict[str, str]
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
    tmux_shown["show-environment"] = "ORCA_TERMINAL_HANDLE=term_attached\n"
    run("start", "demo", "--role", "coordinator")
    assert state.load("demo")["roles"]["coordinator"]["terminal"] == "term_attached"


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


def test_a_terminal_holds_a_session_when_it_attaches_the_tmux_session_whose_pane_runs_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tmux_shown: dict[str, str]
) -> None:
    sessions = tmp_path / "config" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "8100.json").write_text(json.dumps({"pid": 8100, "sessionId": "s-coord"}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(orca, "parents", lambda: {5001: 5000, 5000: 1, 8100: 8000, 8000: 7999, 7999: 1})
    monkeypatch.setattr(orca, "request", lambda *args: {"worktrees": [{"sessions": [{"sessionId": "pty-1", "pid": 5000}]}]})
    assert not orca.holds_session({"ptyId": "pty-1"}, "s-coord")
    tmux_shown["list-panes"] = "8000 demo-coordinator\n8200 demo-meta\n"
    tmux_shown["list-clients"] = "5001 demo-meta\n"
    assert not orca.holds_session({"ptyId": "pty-1"}, "s-coord")
    tmux_shown["list-clients"] = "5001 demo-coordinator\n"
    assert orca.holds_session({"ptyId": "pty-1"}, "s-coord")


def test_status_places_a_session_while_claude_code_removes_a_session_file_it_listed(
    run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = os.environ["CLAUDE_CODE_SESSION_ID"]
    sessions = tmp_path / "config" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "7807.json").write_text(json.dumps({"pid": 7807, "sessionId": session}))
    removed = sessions / "91343.json"
    removed.write_text(json.dumps({"pid": 91343, "sessionId": "s-exited"}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "term_abc")
    monkeypatch.delenv("TMUX")
    pane = {"sessionId": "pty-1", "pid": 7330}
    monkeypatch.setattr(orca, "request", lambda *args: {"terminal": {"ptyId": "pty-1", "orphaned": False}, "worktrees": [{"sessions": [pane]}]})
    monkeypatch.setattr(orca, "parents", lambda: {7807: 7330, 7330: 1})
    run("start", "demo", "--role", "coordinator")
    listed = Path.glob

    def removed_once_listed(path: Path, pattern: str, **kwargs: bool) -> Iterator[Path]:
        found = list(listed(path, pattern, **kwargs))
        if path == sessions:
            removed.unlink(missing_ok=True)
        return iter(found)

    monkeypatch.setattr(Path, "glob", removed_once_listed)
    assert run("status")[1].splitlines()[2] == (
        f"coordinator: session {session}, open it with: orca terminal switch --terminal term_abc; Hold no Slack channel."
    )
    assert not removed.exists()


def test_context_refuses_a_session_with_no_role(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "a-stranger")
    monkeypatch.setenv(state.DRIVE_ENV, "demo")
    assert run("context")[0] == 2


def test_start_holds_the_lock_across_its_existence_check(run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    unpatched = state.locked

    def rival_starts_while_this_one_waits(name: str):
        monkeypatch.setattr(state, "locked", unpatched)
        rival = state.new_drive(name, "communicator", "rival-session", "/rival")
        rival["orders"]["o1"] = {"text": "the rival's order", "state": "open", "due": None, "jobs": []}
        state.create(rival)
        return unpatched(name)

    monkeypatch.setattr(state, "locked", rival_starts_while_this_one_waits)
    assert run("start", "demo", "--role", "coordinator")[0] == 0
    data = state.load("demo")
    assert data["orders"]["o1"]["text"] == "the rival's order"
    assert data["roles"]["communicator"]["session"] == "rival-session"
    assert data["roles"]["coordinator"]["session"] == os.environ["CLAUDE_CODE_SESSION_ID"]


def test_start_rejects_a_name_that_is_not_a_directory_name(run: Run) -> None:
    code, _ = run("start", "../escape", "--role", "coordinator")
    assert code == 2


def test_order_add_allocates_ids_and_stores_words_verbatim(run: Run, demo: str) -> None:
    assert run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "first  words", "--from", "slack:D123/1.2", "--thread", "1.2") == (0, "o1")
    assert run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "second", "--due", "+30m")[0] == 2
    assert run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "second", "--due", "+30m", "--thread", "1.3") == (0, "o2")
    orders = state.load("demo")["orders"]
    assert orders["o1"]["text"] == "first  words"
    assert orders["o1"]["from"] == "slack:D123/1.2"
    assert orders["o1"]["thread"] == ["D123", "1.2"]
    assert orders["o2"]["thread"] == ["D123", "1.3"]
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
    words = ["order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "tell the stopped people", "--from", "slack:D123/1794200050.000200"]
    assert cli.main([*words, "--thread", "1794200090.000700"]) == 2
    assert "--thread 1794200090.000700: refused, it is no thread in D123, where the order's lines go: cc-slack can't read this conversation (thread_not_found)" in capsys.readouterr().err
    assert cli.main([*words, "--thread", "1794200050.000200"]) == 2
    assert "--thread 1794200050.000200: refused, it is a reply in thread 1794200000.000100; pass --thread 1794200000.000100" in capsys.readouterr().err
    assert state.load("demo")["orders"] == {}
    assert run(*words, "--thread", "1794200000.000100") == (0, "o1")
    assert cli.main(["order", "set", "o1", "--thread", "1794200090.000700"]) == 2
    assert "--thread 1794200090.000700: refused, it is no thread in D123" in capsys.readouterr().err
    assert state.load("demo")["orders"]["o1"]["thread"] == ["D123", "1794200000.000100"]
    assert reads[:2] == [
        ("--channel", "D123", "--thread", "1794200090.000700", "--limit", "1"),
        ("--channel", "D123", "--thread", "1794200050.000200", "--limit", "1"),
    ]


def test_an_order_from_his_reply_goes_in_the_thread_his_reply_is_in_in_the_channel_that_thread_is_in(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, reply = "1794388517.958349", "1794388585.312939"
    bug_root, bug_reply = "1794341529.595149", "1794341600.000200"
    messages = {
        ("D123", reply): [{"ts": reply, "thread_ts": root, "from_user": True}],
        ("C0BUGS1", bug_reply): [{"ts": bug_reply, "thread_ts": bug_root, "from_user": True}],
        ("C0BUGS1", bug_root): [{"ts": bug_root, "from_user": True}],
    }
    reads: list[tuple[str, ...]] = []

    def thread(*where: str) -> list[dict]:
        reads.append(where)
        if (where[1], where[3]) not in messages:
            raise slack.SlackError("cc-slack can't read this conversation (channel_not_found)")
        return messages[where[1], where[3]]

    monkeypatch.setattr(slack, "thread", thread)
    filed = ["order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling"]
    assert run(*filed, "What the hell is this message?", "--from", f"slack:D123/{reply}") == (0, "o1")
    assert run(*filed, "the card still shows the old state", "--from", f"slack:C0BUGS1/{bug_reply}") == (0, "o2")
    assert reads == [
        ("--channel", "D123", "--thread", reply, "--limit", "1"),
        ("--channel", "C0BUGS1", "--thread", bug_reply, "--limit", "1"),
        ("--channel", "C0BUGS1", "--thread", bug_root, "--limit", str(slack.WHOLE_THREAD)),
    ]
    permalink = f"https://acme.slack.com/archives/C0BUGS1/p{bug_reply.replace('.', '')}?thread_ts={bug_root}&cid=C0BUGS1"
    assert run(*filed, "and the dashboard too", "--from", f"slack:C0BUGS1/{bug_root}/{bug_reply}", "--thread", bug_root) == (0, "o3")
    assert run(*filed, "and the card", "--from", permalink) == (0, "o4")
    assert run(*filed, "and the list", "--from", f"slack:C0BUGS1/{bug_root}/{bug_reply}", "--thread", f"slack:C0BUGS1/{bug_root}") == (0, "o5")
    assert len(reads) == 4
    data = state.load("demo")
    assert {order_id: (order["from"], order["thread"], state.promise_thread(order)) for order_id, order in data["orders"].items()} == {
        "o1": (f"slack:D123/{reply}", ["D123", root], ("D123", root)),
        "o2": (f"slack:C0BUGS1/{bug_reply}", ["C0BUGS1", bug_root], None),
        "o3": (f"slack:C0BUGS1/{bug_reply}", ["C0BUGS1", bug_root], None),
        "o4": (f"slack:C0BUGS1/{bug_reply}", ["C0BUGS1", bug_root], None),
        "o5": (f"slack:C0BUGS1/{bug_reply}", ["C0BUGS1", bug_root], None),
    }
    assert state.message_order(data, "C0BUGS1", bug_root, bug_reply) == "o2"
    assert cli.main([*filed, "look at this", "--from", "slack:C0GONE1/1794341700.000300"]) == 2
    assert (
        "--from slack:C0GONE1/1794341700.000300: refused, Slack cannot read the message it names: cc-slack can't read this conversation (channel_not_found)"
        in capsys.readouterr().err
    )
    assert set(state.load("demo")["orders"]) == {"o1", "o2", "o3", "o4", "o5"}


def test_order_set_from_points_an_order_at_his_dm_post_while_its_own_thread_stays_the_alert_thread(run: Run, demo: str, slack_thread: Thread) -> None:
    alert, reply, dm_post = "1794493064.110609", "1794495071.669329", "1794495097.793329"
    slack_thread.messages.append({"ts": alert, "from_bot": True})
    filed = ["order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling"]
    assert run(*filed, "unpause the release", "--from", f"slack:C0822AHFY3G/{alert}/{reply}") == (0, "o1")
    root_read = ("--channel", "C0822AHFY3G", "--thread", alert, "--limit", str(slack.WHOLE_THREAD))
    assert slack_thread.reads == [root_read]
    order = state.load("demo")["orders"]["o1"]
    assert (order["from"], order["thread"]) == (f"slack:C0822AHFY3G/{reply}", ["C0822AHFY3G", alert])
    dm_link, alert_link = (f"https://acme.slack.com/archives/{channel}/p{ts.replace('.', '')}" for channel, ts in (("D123", dm_post), ("C0822AHFY3G", alert)))
    assert run("order", "set", "o1", "--from", dm_link, "--thread", alert_link)[0] == 0
    data = state.load("demo")
    assert (data["orders"]["o1"]["from"], state.own_thread(data["orders"]["o1"])) == (f"slack:D123/{dm_post}", ("C0822AHFY3G", alert))
    assert state.message_order(data, "D123", dm_post, dm_post) == "o1"
    assert run("order", "set", "o1", "--from", f"slack:D123/{dm_post}")[0] == 0
    assert state.load("demo")["orders"]["o1"]["thread"] == ["C0822AHFY3G", alert]
    assert slack_thread.reads == [root_read, ("--channel", "C0822AHFY3G", "--thread", alert, "--limit", "1")]


def test_an_order_he_gives_in_a_thread_a_teammate_started_takes_that_thread_as_its_reporter_and_sends_its_lines_to_his_dm(
    run: Run, demo: str, slack_thread: Thread
) -> None:
    report, reply = "1794345064.592209", "1794522700.000100"
    slack_thread.messages += [{"ts": report, "text": "staging deploys hang at the migrate step"}, {"ts": reply, "thread_ts": report, "from_user": True}]
    filed = ["order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling"]
    assert run(*filed, "fix this", "--from", f"slack:C0BUGS1/{report}/{reply}", "--due", "+2h") == (0, "o1")
    assert state.thread_order(state.load("demo"), report) == "o1"
    assert run(*filed, "reply to him there", "--from", f"slack:C0BUGS1/{report}/{reply}", "--thread", report) == (0, "o2")
    assert run(*filed, "what is left there", "--from", f"slack:C0BUGS1/{report}/{reply}", "--kind", "answer") == (0, "o3")
    data = state.load("demo")
    orders = data["orders"]
    assert orders["o1"]["reporters"] == [state.reporter(f"slack:C0BUGS1/{report}")]
    assert (orders["o1"]["thread"], state.promise_thread(orders["o1"]), state.thread_attr(orders["o1"])) == (["C0BUGS1", report], None, {})
    assert orders["o1"]["due"] and limits.his_words(orders["o1"]) == "fix this"
    assert [(orders[order_id]["reporters"], state.own_thread(orders[order_id]), state.promise_thread(orders[order_id])) for order_id in ("o2", "o3")] == [([], ("C0BUGS1", report), None)] * 2
    slack_thread.messages[0] |= {"from_bot": True}
    assert run(*filed, "unpause the release", "--from", f"slack:C0BUGS1/{report}/{reply}") == (0, "o4")
    assert state.load("demo")["orders"]["o4"]["reporters"] == []


def test_order_set_due_on_an_order_with_no_thread_stores_the_drives_own_time_which_the_next_replaces_and_a_close_never_reads(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "wait on the release")
    code, out = run("order", "set", "o1", "--due", "+30m")
    first = state.load("demo")["orders"]["o1"]["due"]
    said = (
        f"stored {render.when(first)} as the drive's own time: o1 has no thread, so no line to him keeps it, and the watch"
        " says DUE once then; a time promised to him needs --thread <its permalink>"
    )
    assert (code, out.splitlines()[-1]) == (0, said)
    assert run("order", "set", "o1", "--due", "+60m")[0] == 0
    order = state.load("demo")["orders"]["o1"]
    assert (state.parse_time(order["due"]) > state.parse_time(first), state.owed_at(order), order["kept"]) == (True, order["due"], None)
    monkeypatch.setattr(slack, "order_posts", lambda data, order_id, order: pytest.fail("closing an order with no thread reads none"))
    assert run("order", "set", "o1", "--state", "dropped")[0] == 0


def test_job_set_waits_on_the_release_of_a_pr_the_drive_tracks(run: Run, demo: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    wait = "release:api:Forge-AI/monorepo#7"
    assert cli.main(["job", "set", "j1", "--waits", wait]) == 2
    assert f"--waits {wait}: the drive tracks no PR Forge-AI/monorepo#7; drive pr add it to its job first" in capsys.readouterr().err
    assert run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/7", "--job", "j1", *LEAD)[0] == 0
    assert run("job", "set", "j1", "--waits", wait)[0] == 0
    assert state.load("demo")["jobs"]["j1"]["waits"] == [wait]


def test_job_set_waits_on_a_part_of_an_order_named_word_for_word_until_a_builder_on_it_returns_done(run: Run, demo: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("order", "add", "--title", "Memory moves", "--project", "The drive", "--workstream", "Drive tooling", "move every team", "--parts", "move every team")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    run("job", "add", "builder", "--order", "o2", "--goal", "move")
    assert cli.main(["job", "set", "j1", "--waits", "o2:move every tem"]) == 2
    assert "--waits o2:move every tem: name a part of o2 still to be delivered, word for word, as o2:<part>: move every team" in capsys.readouterr().err
    wait = "o2:move every team"
    assert run("job", "set", "j1", "--waits", wait)[0] == 0
    data = state.load("demo")
    assert (data["jobs"]["j1"]["waits"], view.holding(data, [wait], None), state.card_wait(data, wait)) == ([wait], [wait], False)
    with state.edit("demo") as data:
        state.take_result(data, "j2", "DONE https://github.com/Forge-AI/monorepo/pull/7 merged: Forge-AI/monorepo#7 at ccc333ddd444")
    assert view.holding(state.load("demo"), [wait], None) == [wait]
    with state.edit("demo") as data:
        state.take_result(data, "j2", "DONE every team moved, its Job succeeded on each cluster")
    assert view.holding(state.load("demo"), [wait], None) == []
    assert cli.main(["job", "set", "j1", "--waits", wait]) == 2
    assert f"--waits {wait}: name a part of o2 still to be delivered, word for word, as o2:<part>: none is left" in capsys.readouterr().err


def test_a_release_wait_on_a_service_the_prs_paths_never_reach_is_refused_naming_the_components_go_ci_finds_they_reach(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/7", "--job", "j1", *LEAD)
    clone, checkout = tmp_path / "clone", tmp_path / "drive-demo"
    for root in (clone, checkout):
        (root / "tools").mkdir(parents=True)
    with state.edit("demo") as data:
        data["roles"]["coordinator"]["cwd"] = str(clone)
    (clone / "tools" / "ci").write_text("")
    request, answer = tmp_path / "request.json", tmp_path / "answer.json"
    (checkout / "tools" / "ci").write_text(FAKE_SELECT.format(request=request, answer=answer))
    (checkout / "tools" / "ci").chmod(0o755)
    answer.write_text(json.dumps({"components": ["executor", "cloudrunner-next", "sand-next", "k8s"], "touched": ["cloudrunner-next", "sand-next"]}))
    monkeypatch.setattr(github, "checkout_repo", lambda cwd: "Forge-AI/monorepo" if cwd == str(clone) else pytest.fail(cwd))
    monkeypatch.setattr(orca, "checkout", lambda name, cwd: checkout)
    monkeypatch.setattr(github, "files", lambda key: ["go/sand/next/cmd/main.go"] if key == "Forge-AI/monorepo#7" else pytest.fail(key))
    assert cli.main(["job", "set", "j1", "--waits", "release:executor:Forge-AI/monorepo#7"]) == 2
    reach = "`tools/ci release select --pr` lists the paths Forge-AI/monorepo#7 changes reaching cloudrunner-next, sand-next"
    assert (
        f"--waits release:executor:Forge-AI/monorepo#7: executor's release never carries Forge-AI/monorepo#7, since {reach}; wait on the"
        " release of the one that runs the change"
    ) in capsys.readouterr().err
    assert json.loads(request.read_text()) == {"body": "", "paths": ["go/sand/next/cmd/main.go"]}
    assert cli.main(["pr", "set", "https://github.com/Forge-AI/monorepo/pull/7", "--waits", "release:runner-worker:Forge-AI/monorepo#7"]) == 2
    said = "release:runner-worker:Forge-AI/monorepo#7: runner-worker is no component go/ci releases, so no release ever carries Forge-AI/monorepo#7"
    assert f"--waits {said}; {reach}" in capsys.readouterr().err
    code, err = keep(monkeypatch, capsys, "j1", "WAITING release:executor:Forge-AI/monorepo#7 rides the next executor release\n")
    assert (code, "j1: refused, nothing kept: release:executor:Forge-AI/monorepo#7: executor's release never carries" in err) == (2, True)
    assert state.load("demo")["jobs"]["j1"]["waits"] == []
    assert run("job", "set", "j1", "--waits", "release:cloudrunner-next:Forge-AI/monorepo#7")[0] == 0
    answer.write_text(json.dumps({"components": ["executor", "k8s"], "touched": []}))
    assert keep(monkeypatch, capsys, "j1", "WAITING release:k8s:Forge-AI/monorepo#7 rides the next k8s release\n") == (0, "")
    assert state.load("demo")["jobs"]["j1"]["waits"] == ["release:cloudrunner-next:Forge-AI/monorepo#7", "release:k8s:Forge-AI/monorepo#7"]


def test_order_set_due_says_it_stored_the_time_and_what_it_waits_on_or_why_it_stored_none(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "send the numbers")
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
        f"stored {render.when(order['next_due'])} behind the line already owed for {due}: the watch keeps starting a handler on that line until the next"
        f" drive post in D123 thread 1.2 lands, which tells him {render.when(order['next_due'])} and makes it the time owed"
    )


def test_an_incident_order_records_a_job_on_each_part_and_names_every_open_incident_with_its_record(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL fork copies fail on plat", "--incident", "--parts", "fix", "first check")
    run("order", "set", "o1", "--note", "record 3fa2c1")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the dashboard is slow")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "an old outage", "--incident", "--parts", "rollback", "fix", "cause")
    run("order", "set", "o3", "--state", "done")
    assert run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "sandsql-api times out on plat", "--incident", "--parts", "fix", "first check") == (
        0,
        (
            "o4\n"
            'j6 on "fix": start it now with drive job launch j6 -, its brief on stdin ending Drive: demo, order o4, job j6\n'
            'j7 on "first check": start it now with drive job launch j7 -, its brief on stdin ending Drive: demo, order o4, job j7\n'
            f"{state.INCIDENT_BRIEF}\n"
            "o1 open incident: SandSQL fork copies fail on plat; record 3fa2c1"
        ),
    )
    data = state.load("demo")
    assert data["orders"]["o4"]["jobs"] == ["j6", "j7"]
    assert [(job["kind"], job["parts"], job["agent"], job["state"]) for job in (data["jobs"]["j6"], data["jobs"]["j7"])] == [
        ("codex", ["fix"], None, "running"),
        ("codex", ["first check"], None, "running"),
    ]
    assert run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why is the dashboard slow?") == (0, "o5")


def test_an_incident_order_with_no_fix_part_is_refused_unless_it_joins_an_open_incident(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL release 60 failed", "--incident", "--parts", "first check"]) == 2
    assert 'an incident order starts its fix job at the alert, beside diagnosis: name "fix" among --parts' in capsys.readouterr().err
    assert state.load("demo")["orders"] == {}
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL release 60 failed", "--incident", "--parts", "fix", "first check")
    run("order", "set", "o1", "--note", "record 3fa2c1")
    assert run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL mint endpoint failing", "--joins", "o1") == (
        0,
        "o2\njoins o1: record 3fa2c1; its owner job carries this order with --carries o2",
    )
    joined = state.load("demo")["orders"]["o2"]
    assert (joined["incident"], joined["note"], joined["jobs"]) == (True, "record 3fa2c1", [])
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the dashboard is slow")
    assert cli.main(["order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the dashboard is slower", "--incident", "--joins", "o3"]) == 2
    assert "--joins o3: name an open order, an open incident order for --incident; o3 is open, not an incident" in capsys.readouterr().err


def test_a_tooling_order_jev_reads_as_repeating_an_open_one_joins_it_and_rides_on_that_orders_jobs(run: Run, meta: str, jev: Jev) -> None:
    filed = ("order", "add", "--title", "Install lock stall", "--project", "The drive", "--workstream", "Drive tooling")
    run(*filed, LOCK_STALL, "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the install lock")
    jev.picks("o1")
    assert run(*filed, LOCK_STALL_AGAIN) == (0, "o2\njoins o1: no record; carried by j1, j2, so no job starts on it")
    [asked] = [request for request in jev.asked if "repeats" in request["questions"]]
    assert (asked["state"], list(asked["questions"]["repeats"]["label"]["options"])) == ({"order": LOCK_STALL_AGAIN}, ["none", "o1"])
    data = state.load("demo")
    again = data["orders"]["o2"]
    carried = {"o2": ["Install lock stall"]}
    assert (again["joins"], again["incident"], again["kind"], again["jobs"], [job["carries"] for job in data["jobs"].values()], state.hurts_now(data, again)) == (
        "o1", False, "build", ["j1", "j2"], [carried, carried], False
    )
    watching = watch.Watcher("demo", offset=state.events_end("demo"))
    gaps = [watch.part_gap(watching, data, order_id, "Install lock stall", state.now()) for order_id in ("o1", "o2")]
    assert gaps == [watch.SETTLING, watch.SETTLING]
    assert run("job", "add", "reader", "--order", "o1", "--goal", "research: who holds the lock")[0] == 0
    assert [job["carries"] for job in state.load("demo")["jobs"].values()] == [carried, carried, {}]
    jev.picks("o1", 0.6)
    assert run(*filed, "Drive tooling: cards say why") == (0, "o3")
    assert state.load("demo")["orders"]["o3"]["joins"] is None


def test_a_tooling_order_is_read_against_the_open_tooling_orders_sharing_most_words_with_it_however_many_are_open(run: Run, meta: str, jev: Jev) -> None:
    with state.edit("demo") as data:
        for number in range(1, 300):
            data["orders"][f"o{number}"] = state.new_order(f"{state.TOOLING} the footer of card {number} wraps", "terminal", None, {}, state.stamp()) | {"title": f"Footer {number}"}
        data["orders"]["o300"] = state.new_order(LOCK_STALL, "terminal", None, {}, state.stamp()) | {"title": "Install lock stall"}
    jev.picks("o300")
    assert run("order", "add", "--title", "Install lock stall", "--project", "The drive", "--workstream", "Drive tooling", LOCK_STALL_AGAIN)[0] == 0
    [asked] = [request for request in jev.asked if "repeats" in request["questions"]]
    options = list(asked["questions"]["repeats"]["label"]["options"])
    assert (len(options), options[:2]) == (settled.REPEATS_ASKED + 1, ["none", "o300"])
    assert state.load("demo")["orders"]["o301"]["joins"] == "o300"


def test_order_set_incident_turns_an_open_answer_order_into_an_incident_with_its_codex_jobs_and_record_in_one_save(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.reads, "run", recorded(calls))
    words = "runs refused at WriteInputs"
    run("order", "add", "--title", "Runs refused", "--project", "The drive", "--workstream", "Drive tooling", words, "--kind", "answer")
    run("job", "add", "reader", "--order", "o1", "--goal", "say why runs are refused")
    assert run("order", "set", "o1", "--incident") == (
        0,
        (
            f"o1 open incident: {words}\n"
            "o1 record 5f6b2b5\n"
            'j2 on "fix": start it now with drive job launch j2 -, its brief on stdin ending Drive: demo, order o1, job j2\n'
            'j3 on "cause": start it now with drive job launch j3 -, its brief on stdin ending Drive: demo, order o1, job j3\n'
            f"{state.INCIDENT_BRIEF}"
        ),
    )
    assert calls == [["ccn", "investigation", "open", "Runs refused", "--body", f"{words}\n\nFrom terminal", "--label", "drive:demo", "--label", "incident", "--json"]]
    data = state.load("demo")
    order = data["orders"]["o1"]
    assert (order["incident"], order["kind"], {part: held["answer"] for part, held in order["parts"].items()}, order["note"]) == (
        True,
        "build",
        {"Runs refused": True, "fix": False, "cause": False},
        "record 5f6b2b5",
    )
    assert [(data["jobs"][job_id]["kind"], data["jobs"][job_id]["parts"]) for job_id in order["jobs"]] == [("reader", ["Runs refused"]), ("codex", ["fix"]), ("codex", ["cause"])]
    run("order", "set", "o1", "--no-incident")
    assert run("order", "set", "o1", "--incident") == (0, f"o1 open incident: {words}")
    assert (len(calls), list(state.load("demo")["jobs"])) == (1, ["j1", "j2", "j3"])

    def refused(argv: list[str], checkout: Path) -> str:
        raise cli.reads.ReadError("ccn investigation open exited 1")

    monkeypatch.setattr(cli.reads, "run", refused)
    run("order", "add", "--title", "Runs fail", "--project", "The drive", "--workstream", "Drive tooling", "runs fail for one team", "--kind", "answer")
    assert cli.main(["order", "set", "o2", "--incident"]) == 2
    assert "refused, nothing written: its evidence record did not open: ccn investigation open exited 1; fix that and run drive order set o2 --incident again" in capsys.readouterr().err
    order = state.load("demo")["orders"]["o2"]
    assert (order["incident"], order["kind"], list(order["parts"]), order["jobs"]) == (False, "answer", ["Runs fail"], [])


def test_order_set_kind_build_on_one_part_of_an_answer_order_makes_it_a_build_whose_other_parts_stay_answers(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Runs fail", "--project", "The drive", "--workstream", "Drive tooling", "why do runs fail, and fix it", "--kind", "answer", "--parts", "why", "fix")
    assert run("order", "set", "o1", "--kind", "build", "--part", "fix")[0] == 0
    order = state.load("demo")["orders"]["o1"]
    assert (order["kind"], {part: held["answer"] for part, held in order["parts"].items()}) == ("build", {"why": True, "fix": False})


FILED = ("--project", "Platform", "--workstream", "SandSQL", "--title", "SandSQL incident")


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
    assert run("incident", "open", words, "--release", *FILED) == (
        0,
        (
            "o1 record 5f6b2b5\n"
            'j1 on "fix": start it now with drive job launch j1 -, its brief on stdin ending Drive: demo, order o1, job j1\n'
            'j2 on "first check": start it now with drive job launch j2 -, its brief on stdin ending Drive: demo, order o1, job j2\n'
            f"{state.INCIDENT_BRIEF}"
        ),
    )
    assert calls == [["ccn", "investigation", "open", "SandSQL incident", "--body", f"{words}\n\nFrom terminal", "--label", "drive:demo", "--label", "incident", "--json"]]
    run("incident", "open", "runs fail for one team", *FILED)
    run("incident", "open", "table moves rerun on every write", "--no-rollback", *FILED)
    orders = state.load("demo")["orders"]
    assert [(order["incident"], order["kind"], list(order["parts"]), order["note"]) for order in orders.values()] == [
        (True, "build", ["fix", "first check"], "record 5f6b2b5"),
        (True, "build", ["fix", "rollback", "cause"], "record 5f6b2b5"),
        (True, "build", ["fix", "stop", "cause"], "record 5f6b2b5"),
    ]


def test_incident_open_writes_nothing_when_its_record_does_not_open(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    long = "sandsql on plat: release 60 cannot restore databases; " * 5
    assert len(long.encode()) == 270

    refusal = "ccn investigation open exited 2: usage: title is 270 bytes (max 256) — a title is a short handle; put the content in --body (- reads stdin)"

    def refused(argv: list[str], checkout: Path) -> str:
        raise cli.reads.ReadError(refusal)

    monkeypatch.setattr(cli.reads, "run", refused)
    words = "SandSQL release 60 failed on plat"
    with pytest.raises(SystemExit):
        cli.main(["incident", "open", words, "--release", "--record", long, *FILED])
    assert "unrecognized arguments: --record" in capsys.readouterr().err
    assert cli.main(["incident", "open", words, "--release", *FILED[:-1], long]) == 2
    assert f"refused, a title is a few words on one line, at most {cli.TITLE_CHARS} characters" in capsys.readouterr().err
    assert cli.main(["incident", "open", words, "--release", *FILED]) == 2
    assert f"refused, nothing written: its evidence record did not open: {refusal}" in capsys.readouterr().err
    assert (state.load("demo")["orders"], state.load("demo")["jobs"]) == ({}, {})


def test_an_incident_records_the_monitor_or_sentry_issue_by_number_or_short_id_of_each_alert_and_refuses_any_other_link(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.reads, "run", recorded([]))
    monitor, lag, sentry = "https://app.datadoghq.com/monitors/315028134?group=forge_cluster%3Aplat", "https://us5.datadoghq.com/monitors/330850820", "https://forge-ai.sentry.io/issues/6012345678/"
    short = "https://forge-rf.sentry.io/issues/API-1NNF"
    run("incident", "open", "SandSQL error logs on plat", "--alert", monitor, "--alert", short, *FILED)
    run("order", "add", "--title", "Short title", *FILED, "SandSQL replication lag on plat", "--joins", "o1", "--alert", sentry)
    run("order", "set", "o1", "--alert", lag, "--alert", monitor)
    assert cli.main(["order", "set", "o1", "--alert", "https://app.datadoghq.com/dashboard/abc-123"]) == 2
    assert "--alert https://app.datadoghq.com/dashboard/abc-123: refused, name the alert by its Datadog monitor link" in capsys.readouterr().err
    orders = state.load("demo")["orders"]
    assert (orders["o1"]["alerts"], orders["o2"]["alerts"]) == ([monitor, short, lag], [sentry])


def test_incident_stop_gives_an_open_incident_one_running_stop_job(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.reads, "run", recorded([]))
    run("incident", "open", "table moves rerun on every write", *FILED)
    assert run("incident", "stop", "o1") == (0, f'j4 on "stop": start it now with drive job launch j4 -, its brief on stdin ending Drive: demo, order o1, job j4\n{state.INCIDENT_BRIEF}')
    data = state.load("demo")
    assert (list(data["orders"]["o1"]["parts"]), data["jobs"]["j4"]["kind"]) == (["fix", "rollback", "cause", "stop"], "codex")
    assert cli.main(["incident", "stop", "o1"]) == 2
    assert "o1: refused, its stop runs as j4; resume that job instead" in capsys.readouterr().err
    run("order", "add", "--title", "Short title", "the dashboard is slow", *FILED)
    assert cli.main(["incident", "stop", "o2"]) == 2
    assert "o2: refused, it is no open incident order" in capsys.readouterr().err


def test_an_incident_jobs_brief_carries_the_rule_its_kind_follows_on_a_choice_on_its_incident_line_once(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.reads, "run", recorded([]))
    run("incident", "open", "table moves rerun on every write", *FILED)
    run("job", "add", "builder", "--order", "o1", "--part", "fix", "--goal", "ship the fix the codex lane built")
    where = ("--serves", "his order", "--done-when", "fixed", "--where", "Forge-AI/monorepo dev")
    run("job", "brief", "j1", *where, "--incident", "record 5f6b2b5")
    run("job", "brief", "j4", *where)
    jobs = state.load("demo")["jobs"]
    assert f"\nIncident: record 5f6b2b5; {state.INCIDENT_CHOICE['codex']}\n" in Path(jobs["j1"]["brief"]).read_text()
    assert f"\nIncident: {cli.release_rule()}; {state.INCIDENT_CHOICE['builder']}\n" in Path(jobs["j4"]["brief"]).read_text()
    assert cli.release_rule().startswith("Every release a drive job starts, an incident's included, starts the regular way")
    run("job", "brief", "j1", "--incident", f"record 5f6b2b5; {state.INCIDENT_CHOICE['codex']}")
    assert Path(state.load("demo")["jobs"]["j1"]["brief"]).read_text().count(state.INCIDENT_CHOICE["codex"]) == 1


def test_incident_freeze_freezes_the_service_for_twelve_hours_from_the_drives_checkout_and_notes_the_freeze(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.reads, "run", recorded(calls))
    checkout = tmp_path / "drive-demo"
    monkeypatch.setattr(orca, "checkout", lambda name, cwd: checkout if cwd == str(tmp_path) else pytest.fail(cwd))
    run("incident", "open", "SandSQL release 60 failed on plat", "--release", *FILED)
    code, said = run("incident", "freeze", "o1", "sandsql")
    freeze = calls[-1]
    assert (code, freeze[0], freeze[1:6]) == (
        0,
        str(checkout / "tools" / "ci"),
        ["release", "freeze", "sandsql", "--reason", "o1, SandSQL release 60 failed on plat: held until its root fix releases"],
    )
    assert timedelta(hours=11, minutes=59) < state.parse_time(freeze[7]) - state.now() <= timedelta(hours=12)
    assert state.load("demo")["orders"]["o1"]["note"] == "record 5f6b2b5; freeze fvr7477syf"
    assert said.endswith("the job that merges the root fix lifts it with tools/ci release unfreeze fvr7477syf before the fix's build starts")


def test_no_stop_ends_the_last_running_job_on_an_open_incidents_fix(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.reads, "run", recorded([]))
    run("incident", "open", "SandSQL mint endpoint failing", "--release", *FILED)
    assert cli.main(["job", "set", "j1", "--state", "stopped"]) == 2
    assert "j1 is the last running job on incident o1's fix, and nothing stops an open incident's fix" in capsys.readouterr().err
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    assert run("job", "set", "j2", "--state", "stopped")[0] == 0
    run("job", "add", "builder", "--order", "o1", "--part", "fix", "--goal", "release the default branch")
    assert run("job", "set", "j1", "--state", "stopped")[0] == 0


def test_an_incident_job_waits_on_his_card_only_once_the_incident_thread_says_so(
    run: Run, demo: str, slack_thread: Thread, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL mint endpoint failing", "--thread", "1794200000.000100", "--incident", "--parts", "fix", "first check")
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
    assert [(question["card"], question["holds"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [(DESIGN_CARD, None)]
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the dashboard is slow")
    run("job", "add", "builder", "--order", "o2", "--goal", "speed it up")
    assert run("job", "set", "j3", "--card", DESIGN_CARD)[0] == 0


def test_order_add_json(run: Run, demo: str) -> None:
    code, out = run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "words", "--json")
    assert code == 0
    assert json.loads(out)["id"] == "o1"


def test_the_set_verbs_with_dry_run_run_their_checks_refusing_as_they_would_and_keep_nothing(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "words")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    kept = state.load(demo)
    for verb in (["order", "set", "o1", "--note", "on it"], ["order", "set", "o1", "--incident"], ["job", "set", "j1", "--said", "pushed"]):
        code, out = run(*verb, "--dry-run")
        assert (code, out.splitlines()[-1]) == (0, "a dry run: nothing kept")
    assert run("job", "set", "j1", "--waits", "the reporter's reply", "--dry-run")[0] == 2
    assert run("pr", "set", "https://github.com/a/b/pull/1", "--no-tap", "--why", "superseded", "--dry-run")[0] == 2
    assert state.load(demo) == kept


def test_order_set_updates_given_fields_and_stamps_lined(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "words")
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
    assert cli.main(["order", "add", "--title", "Short title", "fix the composer", "--project", "Iris"]) == 2
    assert state.load("demo")["orders"] == {}
    run("order", "add", "--title", "Short title", "redo the dashboard", "--project", "The drive", "--workstream", "Dashboard")
    with state.edit("demo") as data:
        for order_id, words, project in (("o2", "move the tables", "Platform"), ("o3", "fix the composer", None), ("o4", "old work", None)):
            data["orders"][order_id] = data["orders"]["o1"] | {"text": words, "project": project, "workstream": None}
        data["orders"]["o4"]["state"] = "done"
        data["orders"]["o2"]["title"] = None
    assert run("order", "unfiled") == (
        0,
        "o2 needs workstream and title: move the tables\no3 needs project and workstream: fix the composer\nin use: The drive / Dashboard",
    )
    run("order", "set", "o2", "--workstream", "Tables", "--title", "Move the tables")
    run("order", "set", "o3", "--project", "Iris", "--workstream", "Chats")
    run("order", "set", "o1", "--workstream", "")
    orders = state.load("demo")["orders"]
    assert [(orders[key]["project"], orders[key]["workstream"], orders[key]["title"]) for key in ("o1", "o2", "o3")] == [
        ("The drive", None, "Short title"),
        ("Platform", "Tables", "Move the tables"),
        ("Iris", "Chats", "Short title"),
    ]
    assert run("order", "unfiled")[1].splitlines()[0] == "o1 needs workstream: redo the dashboard"


def test_an_order_is_added_only_with_a_short_one_line_title_and_his_words_stay_whole(run: Run, demo: str, capsys: pytest.CaptureFixture[str]) -> None:
    filed = ("--project", "Iris", "--workstream", "Chats")
    words = "the chat list should keep its scroll when a new chat lands, see https://example.com/report for the recording"
    for title in ("", "   ", "x" * 61, "see https://example.com/report", "two\nlines"):
        assert cli.main(["order", "add", words, *filed, "--title", title]) == 2
        assert "refused, a title is a few words on one line, at most 60 characters and no link" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["order", "add", words, *filed])
    assert "--title" in capsys.readouterr().err
    assert state.load("demo")["orders"] == {}
    run("order", "add", words, *filed, "--title", "  Keep chat scroll  ")
    order = state.load("demo")["orders"]["o1"]
    assert (order["title"], order["text"]) == ("Keep chat scroll", words)
    assert cli.main(["order", "set", "o1", "--title", ""]) == 2
    assert state.load("demo")["orders"]["o1"]["title"] == "Keep chat scroll"


def test_filing_an_order_keeps_the_time_it_last_moved_and_any_other_change_moves_it(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "ship the fix", "--project", "Platform", "--workstream", "SandSQL")
    run("order", "set", "o1", "--state", "done")
    went_live = state.load("demo")["orders"]["o1"]["updated"]
    monkeypatch.setattr(state, "now", lambda: state.parse_time(went_live) + timedelta(days=2))
    run("order", "set", "o1", "--project", "Iris", "--workstream", "Chats and agents", "--title", "Ship the composer fix")
    order = state.load("demo")["orders"]["o1"]
    assert (order["project"], order["workstream"], order["title"], order["updated"]) == ("Iris", "Chats and agents", "Ship the composer fix", went_live)
    run("order", "set", "o1", "--project", "Platform", "--note", "moved back")
    assert state.load("demo")["orders"]["o1"]["updated"] != went_live


def test_a_subject_keeps_one_home_refusing_a_name_other_orders_file_only_as_the_other_of_project_and_workstream_or_spell_otherwise(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "partition the tables", "--project", "SandSQL", "--workstream", "Storage")
    run("order", "add", "--title", "Short title", "keep the chat scroll", "--project", "Iris", "--workstream", "Chats")
    for project, workstream, refused, home in (
        ("Platform", "SandSQL", "--workstream SandSQL", "--project SandSQL"),
        ("sandsql", "Row ids", "--project sandsql", "--project SandSQL"),
        ("Platform", "Iris", "--workstream Iris", "--project Iris"),
        ("Iris", "chats", "--workstream chats", "--project Iris --workstream Chats"),
    ):
        assert cli.main(["order", "add", "--title", "Short title", "move the rows", "--project", project, "--workstream", workstream]) == 2
        assert capsys.readouterr().err.startswith(f"drive: {refused}: refused, nothing filed: other orders file this subject as {home}, and a subject keeps one home")
    assert cli.main(["order", "add", "--title", "Short title", "move the rows", "--project", "SandSQL", "--workstream", "Platform/SandSQL"]) == 2
    assert "a project or workstream is one name, never a pair written with / › or >" in capsys.readouterr().err
    assert list(state.load("demo")["orders"]) == ["o1", "o2"]
    assert run("order", "add", "--title", "Short title", "split the row ids", "--project", "SandSQL", "--workstream", "Row ids") == (0, "o3")
    assert run("order", "add", "--title", "Short title", "store the chats", "--project", "Iris", "--workstream", "Storage") == (0, "o4")
    with state.edit("demo") as data:
        data["orders"]["o5"] = data["orders"]["o1"] | {"text": "partition by time", "project": "Platform", "workstream": "SandSQL"}
    run("order", "set", "o5", "--project", "SandSQL", "--workstream", "Auto-partitioning")
    assert cli.main(["order", "set", "o2", "--workstream", "SandSQL"]) == 2
    assert "other orders file this subject as --project SandSQL," in capsys.readouterr().err
    assert (state.load("demo")["orders"]["o2"]["project"], state.load("demo")["orders"]["o2"]["workstream"]) == ("Iris", "Chats")


def test_view_of_a_project_lists_every_order_filed_there_and_refuses_a_name_filed_elsewhere_naming_its_home(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    for n in range(12):
        run("order", "add", "--title", f"Partition table {n}", f"partition table {n}", "--project", "SandSQL", "--workstream", "Row ids" if n % 2 else "Auto-partitioning")
    run("order", "add", "--title", "Release notes", "write the release notes", "--project", "Platform", "--workstream", "Releases")
    run("order", "add", "--title", "Chat scroll", "keep the chat scroll", "--project", "Iris", "--workstream", "Chats")
    code, out = run("view", "--project", "SandSQL")
    assert code == 0
    assert [n for n in range(12) if f"Partition table {n}" not in out] == []
    assert ("Release notes" in out, "Chat scroll" in out, "more on the [dashboard]" in out) == (False, False, False)
    model = json.loads(run("view", "--project", "SandSQL", "--workstream", "Row ids", "--json")[1])
    assert sorted(entry["title"] for group in model["status"] for entry in group["features"]) == sorted(f"Partition table {n}" for n in range(1, 12, 2))
    assert "more on the [dashboard]" in run("view")[1]
    assert cli.main(["view", "--project", "Platform", "--workstream", "SandSQL"]) == 2
    assert capsys.readouterr().err.strip() == "drive: --project Platform --workstream SandSQL: no order is filed there; name --project SandSQL"
    assert cli.main(["view", "--project", "Search"]) == 2
    assert capsys.readouterr().err.strip() == "drive: --project Search: no order is filed there; name a project in use: Iris, Platform, SandSQL"


def test_a_drive_stored_with_milestones_loads_without_them(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "ship the fix", "--project", "Platform", "--workstream", "SandSQL")
    stored = state.stored("demo")
    stored["orders"]["o1"]["milestone"] = "Dashboard parity"
    state.store("demo", stored)
    assert "milestone" not in state.load("demo")["orders"]["o1"]


def test_order_set_unknown_order_fails(run: Run, demo: str) -> None:
    assert run("order", "set", "o9", "--state", "done")[0] == 2


def test_order_set_rejects_unknown_state(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "words")
    with pytest.raises(SystemExit):
        run("order", "set", "o1", "--state", "finished")


def test_job_add_links_the_order(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "words")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "pick the layout")
    assert run("job", "add", "designer", "--order", "o1", "--goal", "cards: the layout") == (0, "j1")
    assert state.load("demo")["jobs"]["j1"]["kind"] == "design"


def test_job_set_appends_prs_once(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "words")
    run("job", "add", "codex", "--order", "o1", "--goal", "review")
    run("job", "set", "j1", "--agent", "a-1", "--said", "PR up", "--pr", PR)
    run("job", "set", "j1", "--pr", PR)
    job = state.load("demo")["jobs"]["j1"]
    assert job["agent"] == "a-1"
    assert job["said"] == "PR up"
    assert job["prs"] == [PR]


def test_job_set_stores_each_pr_as_its_canonical_url(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "words")
    run("job", "add", "builder", "--order", "o1", "--goal", "build")
    run("job", "set", "j1", "--pr", PR + "/files", "--pr", PR)
    assert state.load("demo")["jobs"]["j1"]["prs"] == [PR]
    assert run("job", "set", "j1", "--pr", "https://example.com/not-a-pr")[0] == 2


CARDED = "https://acme.slack.com/archives/D123/p1794300000000400"
TOPOLOGY = "Topology should be local metadata in the sessions ssql server"
REWORKING = f'Being reworked to your words "{TOPOLOGY}". A fresh card asks your tap once its builder says how they are met'


def carded(monkeypatch: pytest.MonkeyPatch, withdrawals: Withdrawals) -> None:
    tap = {"asked": state.stamp(), "url": CARDED, "prs": [PR], "card": {"title": "New: land it?", "waits": "merge:Forge-AI:monorepo:42"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [tap], "closed": []})
    withdrawals.cards = [CARDED]


def left_up(how: str) -> str:
    return f"left up his card {CARDED} on Forge-AI/monorepo#42: read its stack's His words, step by step against o2's words, and run {how} for each PR they leave unmet"


def test_his_order_in_his_pr_cards_thread_names_that_card_and_leaves_it_up_until_rework_names_its_pr(
    run: Run, with_pr: str, monkeypatch: pytest.MonkeyPatch, slack_thread: Thread, withdrawals: Withdrawals
) -> None:
    carded(monkeypatch, withdrawals)
    slack_thread.messages = [{"ts": "1794300000.000500", "thread_ts": "1794300000.000400"}]
    code, out = run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Topology", TOPOLOGY, "--kind", "build", "--from", "slack:D123/1794300000.000500")
    assert (code, out.splitlines(), withdrawals.asked) == (0, ["o2", left_up("drive order set o2 --rework <PR>")], [])
    assert (stage.carded(pr := state.load("demo")["prs"][with_pr]), pr["rework"]) == (True, None)
    code, out = run("order", "set", "o2", "--rework", PR)
    assert (code, out.splitlines()[-2:]) == (0, [f"{with_pr} reworked to o2: its card stays down until its builder's READY says how his words are met", f"withdrew his card {CARDED}: {REWORKING}"])
    assert withdrawals.asked == [(PR, REWORKING)]
    pr = state.load("demo")["prs"][with_pr]
    assert pr["rework"]["by"] == "o2"


def test_his_order_added_with_rework_withdraws_the_card_of_the_pr_it_names_in_the_same_call_with_his_words_as_the_why(
    run: Run, with_pr: str, monkeypatch: pytest.MonkeyPatch, slack_thread: Thread, withdrawals: Withdrawals, capsys: pytest.CaptureFixture[str]
) -> None:
    carded(monkeypatch, withdrawals)
    slack_thread.messages = [{"ts": "1794300000.000500", "thread_ts": "1794300000.000400"}]
    added = ["order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Topology", TOPOLOGY, "--kind", "build", "--from", "slack:D123/1794300000.000500"]
    assert cli.main([*added, "--rework", "Forge-AI/monorepo#99"]) == 2
    assert capsys.readouterr().err.strip() == "drive: --rework Forge-AI/monorepo#99: refused, it names an open PR the drive tracks"
    code, out = run(*added, "--rework", with_pr)
    assert (code, out.splitlines()) == (
        0,
        ["o2", f"{with_pr} reworked to o2: its card stays down until its builder's READY says how his words are met", f"withdrew his card {CARDED}: {REWORKING}"],
    )
    assert withdrawals.asked == [(PR, REWORKING)]


def test_his_question_in_his_pr_cards_thread_leaves_that_card_up_when_it_turns_out_to_need_a_build(
    run: Run, with_pr: str, monkeypatch: pytest.MonkeyPatch, slack_thread: Thread, withdrawals: Withdrawals
) -> None:
    carded(monkeypatch, withdrawals)
    slack_thread.messages = [{"ts": "1794300000.000500", "thread_ts": "1794300000.000400"}]
    asked = "Why is topology going to the api?"
    code, out = run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Topology", asked, "--kind", "answer", "--from", "slack:D123/1794300000.000500")
    assert (code, out.splitlines()) == (0, ["o2", left_up("drive order set o2 --rework <PR>")])
    code, out = run("order", "set", "o2", "--kind", "build")
    assert (code, out.splitlines()[-1], withdrawals.asked) == (0, left_up("drive order set o2 --rework <PR>"), [])
    assert (stage.carded(pr := state.load("demo")["prs"][with_pr]), pr["rework"]) == (True, None)


def test_a_job_carrying_his_order_leaves_his_open_card_on_its_pr_up_until_rework_names_that_pr(
    run: Run, with_pr: str, monkeypatch: pytest.MonkeyPatch, withdrawals: Withdrawals, capsys: pytest.CaptureFixture[str]
) -> None:
    carded(monkeypatch, withdrawals)
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Topology", TOPOLOGY, "--kind", "build")
    code, out = run("job", "set", "j1", "--carries", "o2")
    assert (code, out.splitlines()[-1], withdrawals.asked) == (0, left_up("drive job set j1 --carries o2 --rework <PR>"), [])
    assert cli.main(["job", "set", "j1", "--rework", PR]) == 2
    assert capsys.readouterr().err.strip() == "drive: --rework names the PRs one carried order reworks: name that order with one --carries <o>"
    code, out = run("job", "set", "j1", "--carries", "o2", "--rework", PR)
    assert (code, out.splitlines()[-2:]) == (0, [f"{with_pr} reworked to o2: its card stays down until its builder's READY says how his words are met", f"withdrew his card {CARDED}: {REWORKING}"])
    assert withdrawals.asked == [(PR, REWORKING)]


def test_job_set_waits_on_his_pr_card_by_its_slack_link_or_merge_waits_id_as_one_wait_until_he_taps_it(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    link, merge = "https://acme.slack.com/archives/D123/p1794300000000400", "merge:acme:app:42"
    tap = {"asked": state.stamp(), "url": link, "prs": ["https://github.com/acme/app/pull/42"], "card": {"title": "New: land it?", "waits": merge}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [tap], "closed": []})
    view.refresh_cards("demo")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    assert run("job", "set", "j1", "--waits", link, "--waits", merge)[0] == 0
    data = state.load("demo")
    assert data["jobs"]["j1"]["waits"] == [merge]
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "reproduce the bug")
    run("job", "add", "builder", "--order", "o1", "--goal", "reproduce")
    run("order", "set", "o1", "--state", "done")
    steps = tmp_path / "steps.md"
    steps.write_text("1. run it\n")
    assert run("job", "set", "j1", "--repro", str(steps))[0] == 0
    steps.unlink()
    kept = state_root / "demo" / "repro" / "j1.md"
    assert state.load("demo")["jobs"]["j1"]["repro"] == str(kept)
    script = tmp_path / "repro.sh"
    script.write_text("#!/bin/sh\nexit 1\n")
    script.chmod(0o755)
    assert run("job", "set", "j1", "--repro", str(script))[0] == 0
    assert os.access(state_root / "demo" / "repro" / "j1.sh", os.X_OK)
    assert run("job", "set", "j1", "--repro", str(kept))[0] == 0
    assert kept.read_text() == "1. run it\n"
    assert f"- j1 (o1): {kept}" in run("status")[1]
    assert run("job", "set", "j1", "--repro", str(steps))[0] == 2
    assert run("job", "set", "j1", "--repro", str(kept))[0] == 0
    assert kept.read_text() == "1. run it\n"


@pytest.fixture
def asking(run: Run, demo: str) -> str:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "design the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    return "j1"


def test_job_ask_refuses_a_card_typing_when_past_work_happened_keeping_nothing(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK.replace("Meanwhile nothing is built on either layout.", "Meanwhile nothing is built; the reader finished at 4:55 AM."))
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert '\n- his card draws these words, and "4:55 AM" is a time typed for work already done' in capsys.readouterr().err
    assert state.load("demo")["jobs"]["j1"]["questions"] == []
    block.write_text(SHORT_ASK)
    assert run("job", "ask", asking, str(block))[0] == 0


def test_job_ask_keeps_a_card_naming_when_a_hold_expires_later_today_and_refuses_one_naming_when_it_was_set_earlier_today(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(limits, "now", lambda: WRITTEN_AT.replace(hour=7, minute=10, second=7))
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK.replace("Meanwhile nothing is built on either layout.", "Meanwhile the release hold was set at 6:54 AM."))
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert '"6:54 AM" is a time typed for work already done, and the drive\'s stamps say when it happened: this write is stamped 7:10:07 AM' in capsys.readouterr().err
    block.write_text(SHORT_ASK.replace("Meanwhile nothing is built on either layout.", "Meanwhile the hold expires at 6:54 PM Pacific today."))
    assert run("job", "ask", asking, str(block))[0] == 0


def test_job_ask_keeps_an_ask_in_the_shape_he_reads_keeps_its_builder_running_to_build_the_recommended_option_and_draws_his_card_on_it_under_one_waits_id(
    run: Run, asking: str, tmp_path: Path, state_root: Path, cards_drawn: list[dict]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    code, out = run("job", "ask", asking, str(block))
    kept = state_root / "demo" / "asks" / f"j1-{hashlib.sha256(SHORT_ASK.encode()).hexdigest()[:8]}.md"
    assert (code, out) == (0, f"{kept}\nhis card {drawn_card(1)} asks it under demo:j1-7")
    assert kept.read_text() == SHORT_ASK
    job = state.load("demo")["jobs"]["j1"]
    assert [(question["file"], question["waits"], question["card"]) for question in job["questions"]] == [(str(kept), "demo:j1-7", drawn_card(1))]
    assert (job["state"], job["returned"]) == ("running", None)
    assert job["said"] == "How should a finished run's files be laid out so its page opens without guessing?"
    shown = json.loads(run("job", "ask", asking, str(block), "--json")[1])
    assert [(drawn["file"], drawn["waits"], drawn["title"]) for drawn in cards_drawn] == [(str(kept), "demo:j1-7", job["said"])] * 2
    assert (shown["waits"], shown["card"]) == ("demo:j1-7", drawn_card(2))
    assert shown["recommended"].startswith("One snapshot file per finished run")
    assert len(shown["options"]) == 3
    assert len(shown["summary"]) == 3
    assert shown["show_me"][0] == "1 · One snapshot file per finished run, the requests its page makes:"
    assert shown["show_me"][-1] == "```"


PINNED_CHATS_ASK = explained("""ASK Where should pinned chats draw in the Iris sidebar?
Drive tooling: your pick decides which spot counts as the top. A row's menu gains Pin and Unpin, and the sidebar draws pinned chats and agents ahead of unpinned ones.
You have not decided this. A user asked to keep a Unichat pinned at the top at all times, and a Unichat lists under Agents, below every chat, so I recommend one Pinned group above both.
Show me:
The sidebar from the top down under each option, with a pinned Unichat and one pinned chat
| Sidebar, top down | 1 · Pinned group on top | 2 · Top of each section | 3 · One shared Pinned section |
| --- | --- | --- | --- |
| First | New Chat, Search, New Agent | New Chat, Search, New Agent | New Chat, Search, New Agent |
| Second | Pinned: Unichat, CPQ sync | Chats: CPQ sync with a pin mark, then folders | Pinned: Unichat, CPQ sync, Deal desk app |
| Third | Chats: folders, then the 5 newest chats | Agents: Unichat with a pin mark, then the 5 newest agents | Chats: folders, then the 5 newest chats |
- A new Pinned group above Chats and Agents: pinned chats and agents draw there in the order you drag (recommended)
- Pinned rows rise to the top of their own section: a pinned Unichat tops Agents but stays below every chat and folder
- Chat pins join today's Pinned section of projects and apps, and that section moves from below the chat lists to the top
Findings:
Root cause: none: a new feature a user asked for, not a defect
Simplest: Pinned rows rise to the top of their own section
Holds: none: no option changes code any job builds
The sidebar draws New Chat, Search, New Agent, then the session list, then the Pinned section of projects and apps.
""")


def test_job_ask_refuses_a_look_choice_until_each_option_carries_its_screenshot(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], jev: Jev
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(PINNED_CHATS_ASK)
    jev.looks = 0.99
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    refused = capsys.readouterr().err
    assert "the ASK's options differ in how a screen looks, so he picks by seeing each, and options 1, 2, 3 carry no screenshot" in refused
    assert "his ruling 0d2b7df" in refused
    [looked] = jev.looked
    assert (looked["question"], looked["options"][0]["option"]) == (
        "Where should pinned chats draw in the Iris sidebar?",
        "A new Pinned group above Chats and Agents: pinned chats and agents draw there in the order you drag",
    )
    shots = []
    for number, what in enumerate(("Pinned group on top", "Top of each section", "One shared Pinned section"), start=1):
        shot = tmp_path / "shots" / f"option-{number}.png"
        shot.parent.mkdir(exist_ok=True)
        shot.write_bytes(b"\x89PNG\r\n")
        shots.append(f"![{number} · {what}]({shot})")
    block.write_text(PINNED_CHATS_ASK.replace("Show me:\n", "Show me:\n" + "\n".join(shots[:2]) + "\n"))
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert "and option 3 carries no screenshot" in capsys.readouterr().err
    block.write_text(PINNED_CHATS_ASK.replace("Show me:\n", "Show me:\n" + "\n".join(shots) + "\n"))
    assert run("job", "ask", asking, str(block))[0] == 0
    assert len(jev.looked) == 2


def test_job_ask_keeps_a_choice_he_reads_about_with_no_screenshot(run: Run, asking: str, tmp_path: Path, jev: Jev) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    jev.looks = 0.02
    assert run("job", "ask", asking, str(block))[0] == 0
    assert [looked["question"] for looked in jev.looked] == ["How should a finished run's files be laid out so its page opens without guessing?"]


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


def test_job_ask_refuses_a_compare_fence_holding_a_markdown_table_by_the_fence_its_parse_error_and_the_json_it_wants_and_keeps_nothing(
    run: Run, asking: str, tmp_path: Path, state_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SPLIT_JOURNAL_ASK)
    for argv in (["job", "ask", asking, "--check", str(block)], ["job", "ask", asking, str(block)]):
        assert cli.main(argv) == 2
        refusal = capsys.readouterr().err
        assert refusal.startswith("drive: the ASK is out of shape")
        assert (
            "the Show me ```compare fence is not the JSON it draws from: Expecting value at its line 1, column 1;"
            f" write {limits.COMPARE_SHAPE}, or put a markdown table outside any fence, which Slack draws as its own table"
        ) in refusal
        assert "Traceback" not in refusal
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["questions"]) == ("running", [])
    assert not (state_root / "demo" / "asks").exists()


def test_job_ask_check_says_ready_or_every_fix_its_words_card_and_job_need_at_once_and_keeps_nothing(
    run: Run, asking: str, tmp_path: Path, state_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], drawing: Drawing
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    assert cli.main(["job", "ask", asking, "--check", str(block)]) == 0
    assert capsys.readouterr().out == "ready\n"
    drawing.refusal = 'card body points at an option by number or position with "second option"'
    block.write_text(SHORT_ASK.replace("Meanwhile nothing is built on either layout.", "Meanwhile nothing is built; the reader finished at 4:55 AM."))
    assert cli.main(["job", "ask", asking, "--check", str(block)]) == 2
    refusal = capsys.readouterr().err
    assert f"\n- the ASK's card is out of shape, so cc-slack would not draw it: {drawing.refusal}" in refusal
    assert '\n- his card draws these words, and "4:55 AM" is a time typed for work already done' in refusal
    assert [card["waits"] for card in drawing.cards] == ["demo:j1-0", "demo:j1-0"]
    monkeypatch.setattr("sys.stdin", io.StringIO(FINDINGS_FIRST))
    assert cli.main(["job", "ask", asking, "--check", "-"]) == 2
    refusal = capsys.readouterr().err
    assert "the summary is 0 lines" in refusal
    assert "0 options; an ASK has 2 to 4" in refusal
    assert state.load("demo")["jobs"]["j1"]["questions"] == []
    assert not (state_root / "demo" / "asks").exists()
    assert cli.main(["job", "ask", "--check", str(block)]) == 2
    assert "drive job ask <j> <file>; drive job ask <j> --check <file> runs every check it runs and keeps nothing" in capsys.readouterr().err


def test_job_ask_reads_a_readers_design_return_as_the_ask_its_first_line_asks(run: Run, asking: str, tmp_path: Path) -> None:
    block = tmp_path / "design.md"
    block.write_text(DESIGN_RESEARCH)
    assert run("job", "ask", asking, "--check", str(block)) == (0, "ready")
    code, out = run("job", "ask", asking, str(block))
    assert (code, Path(out.splitlines()[0]).read_text()) == (0, f"ASK {DESIGN_RESEARCH.removeprefix('DESIGN ')}")
    assert state.load("demo")["jobs"]["j1"]["said"] == DESIGN_TITLE


RESTORE_ASK = explained(
    "ASK Who links a moved table when a restore brings back a root database from before the one-off link?\n"
    'Every option runs the one-off break-glass link now; Your "Just do it with break glass now" covers tables moved so far.\n'
    "Held until you pick, so nothing ships meanwhile.\n"
    "- The restore relinks each moved table it brings back (recommended)\n- An operator relinks moved tables by hand after a restore\n"
    "Findings:\nRoot cause: none: a design pick\nSimplest: The restore relinks each moved table it brings back\nHolds: none: no option changes code any job builds\nKept on disk.\n"
)
RESTORE_OPENING = RESTORE_ASK.splitlines()[1]


@pytest.mark.parametrize(
    "opening",
    [
        RESTORE_OPENING,
        "The merge queue ejects the run page change each time dev moves, and your pick decides which way it lands.",
        'For {order:o1}: the restore keeps "your pick" readable once the reader switch ships.',
    ],
)
def test_job_ask_check_refuses_a_summary_whose_first_line_never_says_the_work_it_serves_and_what_his_pick_changes(
    opening: str, run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(RESTORE_ASK.replace(RESTORE_OPENING, opening))
    assert cli.main(["job", "ask", asking, "--check", str(block)]) == 2
    refusal = capsys.readouterr().err
    assert "the summary's first line never says" in refusal
    assert limits.OPENING_RULING in refusal
    assert limits.question(RESTORE_ASK, kept=True).summary[0] == RESTORE_OPENING


@pytest.mark.parametrize(
    "opening",
    [
        "For {order:o1}: your pick decides whether a restore keeps its moved tables readable once the reader switch ships.",
        "For {order:o1|the moved tables order}: you pick who relinks a moved table after a restore.",
        "Drive tooling: your pick decides whether the restore relinks each moved table it brings back.",
        "For the <https://github.com/acme/app/pull/9|port of #9>: your pick decides who relinks a moved table.",
    ],
)
def test_job_ask_check_passes_a_first_line_naming_the_work_it_serves_and_what_his_pick_changes_and_lets_an_order_reference_through(
    opening: str, run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(RESTORE_ASK.replace(RESTORE_OPENING, opening))
    assert cli.main(["job", "ask", asking, "--check", str(block)]) == 0
    block.write_text(RESTORE_ASK.replace(RESTORE_OPENING, f"{opening} It replaces o518's link."))
    assert cli.main(["job", "ask", asking, "--check", str(block)]) == 2
    assert '"o518" is a drive job or order id' in capsys.readouterr().err


def test_job_ask_and_its_check_refuse_a_summary_over_his_length_as_his_card_draws_its_order_references_and_keep_one_whose_references_carry_words(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Store copy", "--project", "The drive", "--workstream", "Drive tooling", "copy every store of the team in the order its list names them, one at a time, with no store skipped or copied twice")
    block = tmp_path / "ask.md"
    repeated = " ".join(["{order:o2}"] * 8)
    block.write_text(RESTORE_ASK.replace(RESTORE_OPENING, f"For {{order:o2}}: your pick decides who relinks a moved table. It serves {repeated}."))
    assert cli.main(["job", "ask", asking, "--check", str(block)]) == 2
    checked = capsys.readouterr().err
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    refusal = capsys.readouterr().err
    assert refusal == checked
    assert "words as his card draws them, each reference in the summary drawn as the words that name it" in refusal
    assert state.load("demo")["jobs"]["j1"]["questions"] == []
    block.write_text(block.read_text().replace("{order:o2}", "{order:o2|the store copy}"))
    assert cli.main(["job", "ask", asking, "--check", str(block)]) == 0
    assert cli.main(["job", "ask", asking, str(block)]) == 0


def test_term_keeps_a_word_he_corrected_beside_its_proper_term_and_a_drive_saved_before_it_loads_with_none(run: Run, demo: str) -> None:
    assert state.load(demo)["stand_ins"] == {}
    assert run("term", "backup", "S3 replica") == (0, 'say\'s hook finds "backup" from now on, naming "S3 replica", for Jev to judge against his words')
    assert state.load(demo)["stand_ins"] == {"backup": "S3 replica"}
    saved = state.stored(demo)
    del saved["stand_ins"]
    state.store(demo, saved)
    assert state.load(demo)["stand_ins"] == {}


def test_job_ask_and_card_refuse_a_file_that_is_not_there_by_its_path(
    run: Run, asking: str, tmp_path: Path, state_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "ask.md"
    for argv in (["job", "ask", asking, "--check", str(missing)], ["job", "ask", asking, str(missing)], ["job", "card", str(missing), "--waits", "demo:j1-7"]):
        assert cli.main(argv) == 2
        assert capsys.readouterr().err == f"drive: no ASK block file at {missing}\n"
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["questions"]) == ("running", [])
    assert not (state_root / "demo" / "asks").exists()


def test_job_ask_keeps_an_option_his_no_sweepers_ruling_refuses_only_when_unsettled_names_that_ruling(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(
        explained(
            "ASK How do leases stop leaking?\nDrive tooling: your pick decides how a lease ends; one outlives the run that took it, so the next run waits on it.\n"
            "- reclaim orphaned leases every 10 minutes (recommended)\n- one owner holds the lease until it ends\n"
            "Findings:\nRoot cause: none: a design pick\nSimplest: one owner holds the lease until it ends\nHolds: none: no option changes code any job builds\nTwo runs held one lease.\n"
        )
    )
    assert cli.main(["job", "ask", asking, "--check", str(block)]) == 2
    assert 'cleans up a bad state with "reclaim orphaned"' in capsys.readouterr().err
    assert cli.main(["job", "ask", asking, "--check", str(block), "--unsettled", "b89209a: the lease lives in a store no owner can hold"]) == 0
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
    assert "the ASK's question, summary and options are 153 words as his card draws them" in capsys.readouterr().err


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


TEAMMATE_FIX = explained("""ASK A teammate's PR for the missing update button landed first: keep it, add our download status to it, or replace it?
Drive tooling: your pick decides which update button ships. A teammate's PR landed today: every locked chat shows an Update sand button that downloads and installs in one click, even mid-download. Ours shows the button once the download finishes and says downloading until then. They conflict. Ours waits unlanded meanwhile.
- Keep theirs, close ours: the button is always there and nothing needs a terminal, but the card never says it is downloading. (recommended)
- Add our status line beside their always-on button: the card says checking, downloading or downloaded; ours is rebuilt and reviewed again.
- Replace theirs with ours: the button appears once downloaded, terminal steps when a download fails; it undoes a teammate's landed PR.
Findings:
Root cause: none: two landed designs of one button, a pick between them with no defect behind it
Simplest: Keep theirs, close ours
Holds: none: no option changes code any job builds
- Theirs merged Nov 10; ours now conflicts with dev.
""")
TEAMMATE_FIX_SHOWN = TEAMMATE_FIX.replace(
    "A teammate's PR landed today:",
    "<https://github.com/Forge-AI/monorepo/pull/53618|A teammate's PR for the update button> landed today:",
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
        '"teammate\'s PR" names something he could open with no link there, in "A teammate\'s PR landed today"; put those'
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


def test_job_ask_starts_from_the_root_cause_and_leaves_which_option_it_recommends_to_jev_reading_his_words(
    run: Run, asking: str, tmp_path: Path, jev: Jev
) -> None:
    block = tmp_path / "ask.md"
    picked = "Root cause: none: two landed designs of one button, a pick between them with no defect behind it"
    block.write_text(TEAMMATE_FIX_SHOWN.replace(picked, "Root cause: the button and our status line read two download states\nFirst principles: Add our status line beside their always-on button"))
    assert run("job", "ask", asking, str(block))[0] == 0
    assert "Option 2: Add our status line beside their always-on button" in jev.asked[-1]["state"]["card"]
    assert "First principles: Add our status line beside their always-on button" in jev.asked[-1]["state"]["card"]


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
    landing = explained("- Land the snapshot writer now, then backfill old runs from a pass box: opens in 3 requests\n")
    block.write_text(SHORT_ASK.replace("- A map file beside each journal file", f"{landing}- A map file beside each journal file"))
    assert run("job", "ask", asking, str(block))[0] == 0


LANDING_ASK = explained("""ASK Land your run history change so the run page change you approved can land?
Drive tooling: your pick decides when both changes land. <https://github.com/acme/app/pull/12|The run page change> is built on <https://github.com/acme/app/pull/11|your run history change>, which is green and approved but has no tap of yours.
I recommend landing both together, run history first; meanwhile both stay open.
- Land both now, run history first (recommended)
- Move the run page change onto dev without run history, and leave that one with you

Findings:
Root cause: none: landing two PRs, with no defect behind it
Simplest: Land both now, run history first
Holds: none: no option changes code any job builds
The run page change's base branch is the run history change's branch, so the merge queue cannot take it alone.
""")


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


def test_job_ask_check_keep_and_card_refuse_a_show_me_picture_that_does_not_draw_with_why_and_keep_it_once_it_draws(
    run: Run, asking: str, tmp_path: Path, state_root: Path, capsys: pytest.CaptureFixture[str], chrome: Chrome
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    chrome.refusal = "Parse error on line 7: Expecting 'SOLID_OPEN_ARROW', got 'NEWLINE'"
    for argv in (["job", "ask", asking, "--check", str(block)], ["job", "ask", asking, str(block)], ["job", "card", str(block), "--waits", "demo:j1-7"]):
        assert cli.main(argv) == 2
        assert capsys.readouterr().err == (
            'drive: the Show me picture "1 · One snapshot file per finished run, the requests its page makes", a ```http fence,'
            " does not draw: Parse error on line 7: Expecting 'SOLID_OPEN_ARROW', got 'NEWLINE'\n"
        )
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["questions"]) == ("running", [])
    assert not list((state_root / "demo").glob("asks/*.md"))
    chrome.refusal = None
    assert run("job", "ask", asking, "--check", str(block)) == (0, "ready")
    code, kept = run("job", "ask", asking, str(block))
    assert code == 0
    assert Path(kept.splitlines()[0]).read_text() == SHORT_ASK
    assert [kind for kind, _ in chrome.drawn] == ["code:http", "code:http", "code:http", "code:http"]
    assert run("job", "card", str(block), "--waits", "demo:j1-7")[0] == 0
    assert len(chrome.drawn) == 4


def test_job_card_keeps_its_refusal_on_the_question_whose_card_did_not_draw_until_job_ask_asks_it_again(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    draw = cli.post_ask

    def locked(name: str, job_id: str, file: Path, card: dict) -> str:
        raise slack.SlackError("dm-status: database is locked")

    monkeypatch.setattr(cli, "post_ask", locked)
    kept = run("job", "ask", asking, str(block))[1].splitlines()[0]
    Path(kept).write_text(SHORT_ASK.replace("Meanwhile nothing is built on either layout.", "Meanwhile the journal keeps its layout."))
    refusal = (
        f"{kept} no longer holds the words drive job ask kept there, so it draws no card: a kept question changes only through"
        " drive job ask, which checks the new words against every rule, so keep them with drive job ask j1 <file> and draw the file it prints"
    )
    assert cli.main(["job", "card", kept, "--waits", "demo:j1-7"]) == 2
    assert capsys.readouterr().err == f"drive: {refusal}\n"
    [question] = state.load("demo")["jobs"]["j1"]["questions"]
    assert (question["waits"], question["card"], question["refused"]) == ("demo:j1-7", None, refusal)
    assert f"refused, so no card draws: {kept}" in run("status", asking)[1]
    Path(kept).write_text(SHORT_ASK)
    assert cli.main(["job", "card", asking]) == 2
    assert f"pending, since `drive job card` refused it, so no card draws from it; act on that refusal and ask again: {refusal}" in capsys.readouterr().err
    monkeypatch.setattr(cli, "post_ask", draw)
    assert run("job", "ask", asking, kept) == (0, f"{kept}\nhis card {drawn_card(1)} asks it under demo:j1-7")
    assert [(question["waits"], question["refused"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [("demo:j1-7", None)]
    assert json.loads(run("job", "card", asking)[1])["waits"] == "demo:j1-7"


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
    block = state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(KEPT_TEXT_FENCE_ASK)}.md"
    block.parent.mkdir(exist_ok=True)
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


def test_job_card_fills_each_reference_in_its_show_me_table_and_descriptions_as_say_fills_a_posts(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    went_out = state.now().replace(hour=4, minute=47, second=9, microsecond=0).astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    monkeypatch.setattr(reads, "run", lambda argv, checkout: json.dumps({"at": went_out, "text": "POST Yes to both", "refs": {}}))
    table = (
        "Show me:\nThe report each layout's numbers come from:\n| Layout | Requests to open a run | Its report went out |\n| --- | --- | --- |\n"
        "| One snapshot file per finished run | 3 | {at:674271} |\n| A map file beside each journal file | 360 | {at:675198} |\n"
    )
    head, rest = SHORT_ASK.split("Show me:\n", 1)
    options = rest[rest.index("- One snapshot file per finished run, written") :]
    described = options.replace("a backfill writes one for each old run.", "a backfill writes one for each old run, as the report of {at:675201} asked.")
    block = tmp_path / "ask.md"
    block.write_text(head + table + described)
    code, printed = run("job", "card", str(block), "--waits", "demo:j1-7")
    assert code == 0
    card = json.loads(printed)
    assert "| One snapshot file per finished run | 3 | 4:47 AM |" in card["body"].splitlines()
    assert "| A map file beside each journal file | 360 | 4:47 AM |" in card["body"].splitlines()
    assert card["hints"][card["recommended"]].endswith("a backfill writes one for each old run, as the report of 4:47 AM asked.")
    assert "{at:" not in printed


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
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [tap], "closed": []})
    view.refresh_cards("demo")
    assert cli.main(["job", "card", str(block), "--waits", "demo:j1-7"]) == 2
    assert f"acme/app#42: the words do not link its open tap card {link}" in capsys.readouterr().err
    block.write_text(SHORT_ASK.replace("Meanwhile nothing is built on either layout.", f"Meanwhile <{url}|the layout change> waits on your tap: <{link}|its tap card>."))
    assert run("job", "card", str(block), "--waits", "demo:j1-7")[0] == 0


def test_job_card_after_a_job_draws_its_newest_kept_ask_or_the_one_named_under_the_waits_id_job_ask_drew_it_under(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    first = run("job", "ask", asking, str(block))[1].splitlines()[0]
    code, printed = run("job", "card", asking)
    assert (code, json.loads(printed)["waits"]) == (0, "demo:j1-7")
    assert run("job", "card", first, "--waits", "demo:j1-7") == (0, printed)
    block.write_text(SHORT_ASK.replace("ASK How should", "ASK Where should"))
    run("job", "ask", asking, str(block))
    assert json.loads(run("job", "card", asking)[1])["waits"] == "demo:j1-8"
    assert run("job", "card", asking, first) == (0, printed)


@pytest.fixture
def slack_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Each cc-slack call drive job ask's own draw makes: dm-status draws a card at the link its words under its waits id hold, edited in place for the same words and posted anew for new words, as cc-slack does."""
    monkeypatch.setattr(cli, "post_ask", POST_ASK)
    calls: list[tuple[str, ...]] = []
    links: dict[str, str] = {}

    def cc_slack(*argv: str) -> dict:
        calls.append(argv)
        if argv[0] != "dm-status":
            return {}
        drawn = argv[argv.index("--card") + 1]
        links.setdefault(drawn, slack_card(len(links) + 1))
        return {"permalink": links[drawn]}

    monkeypatch.setattr(slack, "cc_slack", cc_slack)
    return calls


def slack_card(n: int) -> str:
    return f"https://acme.slack.com/archives/D123/p{1794500000000100 + n}"


def drawn_waits(calls: list[tuple[str, ...]]) -> list[str]:
    return [json.loads(argv[argv.index("--card") + 1])["waits"] for argv in calls if argv[0] == "dm-status"]


def test_a_card_that_does_not_draw_leaves_its_question_undrawn_under_its_waits_id_until_the_watchs_next_read_draws_it(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cards_drawn: list[dict]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    draw = cli.post_ask

    def locked(name: str, job_id: str, file: Path, card: dict) -> str:
        raise slack.SlackError("dm-status: database is locked")

    monkeypatch.setattr(cli, "post_ask", locked)
    kept = state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(SHORT_ASK)}.md"
    assert run("job", "ask", asking, str(block)) == (0, f"{kept}\nj1's card under demo:j1-7 did not draw, so the watch draws it on its next read: dm-status: database is locked")
    assert json.loads(run("job", "card", asking)[1])["waits"] == "demo:j1-7"
    monkeypatch.setattr(cli, "post_ask", draw)
    watcher = watch.Watcher("demo", offset=state.events_end("demo"), cards=view.NO_CARDS)
    watch.draw_questions(watcher, state.now())
    watch.draw_questions(watcher, state.now())
    assert [(question["file"], question["waits"], question["card"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [(str(kept), "demo:j1-7", drawn_card(1))]
    assert [(drawn["file"], drawn["waits"]) for drawn in cards_drawn] == [(str(kept), "demo:j1-7")]


def test_j2801_a_question_kept_under_a_waits_id_with_cci_s_hash_and_no_card_draws_it_under_that_id_without_the_hash_posting_no_record(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, slack_calls: list[tuple[str, ...]]
) -> None:
    kept = state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(SHORT_ASK)}.md"
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(SHORT_ASK)
    with state.edit("demo") as data:
        asked = state.new_question(str(kept), SHORT_ASK.splitlines()[0].removeprefix("ASK "), state.stamp()) | {"waits": "demo:j1-#686784"}
        data["jobs"]["j1"].update(state="waiting", questions=[asked])
    monkeypatch.setattr(tools, "sent", lambda name, lane, posts: pytest.fail(f"a record went out: {posts}"))
    assert run("job", "ask", asking, str(kept)) == (0, f"{kept}\nhis card {slack_card(1)} asks it under demo:j1-686784")
    assert drawn_waits(slack_calls) == ["demo:j1-686784"]
    assert [(question["waits"], question["card"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [("demo:j1-686784", slack_card(1))]


def test_j2645_a_question_asked_again_in_its_words_while_its_card_is_up_redraws_that_card_in_place(
    run: Run, asking: str, tmp_path: Path, slack_calls: list[tuple[str, ...]]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    kept = state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(SHORT_ASK)}.md"
    assert run("job", "ask", asking, str(block)) == (0, f"{kept}\nhis card {slack_card(1)} asks it under demo:j1-7")
    assert run("job", "ask", asking, str(block)) == (0, f"{kept}\nhis card {slack_card(1)} asks it under demo:j1-7")
    assert drawn_waits(slack_calls) == ["demo:j1-7", "demo:j1-7"]
    assert [(question["file"], question["waits"], question["card"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [(str(kept), "demo:j1-7", slack_card(1))]


def test_j2806_a_question_asked_again_in_new_words_redraws_its_card_under_its_one_waits_id_and_each_wait_on_the_old_card_moves_to_that_id(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], slack_calls: list[tuple[str, ...]]
) -> None:
    title = SHORT_ASK.splitlines()[0].removeprefix("ASK ")
    listed = {"question": title, "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": slack_card(1), "card": {"title": title, "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [listed], "pr_cards": [], "closed": []})
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    kept = run("job", "ask", asking, str(block))[1].splitlines()[0]
    with state.edit("demo") as data:
        data["jobs"]["j1"]["waits"] = [slack_card(1)]
    assert cli.main(["job", "ask", asking, "--drop", kept]) == 2
    assert f"is on his card {slack_card(1)}, which only his answer closes" in capsys.readouterr().err
    block.write_text(SHORT_ASK.replace("Meanwhile nothing is built on either layout.", "Meanwhile the reader adds one more finding."))
    again = state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(block.read_text())}.md"
    assert run("job", "ask", asking, str(block)) == (0, f"{again}\nhis card {slack_card(2)} asks it under demo:j1-7")
    job = state.load("demo")["jobs"]["j1"]
    assert ([(question["file"], question["waits"], question["card"]) for question in job["questions"]], job["waits"]) == ([(str(again), "demo:j1-7", slack_card(2))], ["demo:j1-7"])
    assert drawn_waits(slack_calls) == ["demo:j1-7", "demo:j1-7"]


def test_j3997_a_question_asked_again_in_new_words_whose_redraw_fails_stays_on_its_card_so_drop_refuses_it_his_answer_closes_it_and_say_links_it(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], slack_calls: list[tuple[str, ...]]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    run("job", "ask", asking, str(block))

    def answered(name: str, job_id: str, file: Path, card: dict) -> str:
        raise slack.SlackError(f'waiting card "{card["waits"]}" was answered with "Yes" at 12:31pm')

    monkeypatch.setattr(cli, "post_ask", answered)
    block.write_text(SHORT_ASK.replace("Meanwhile nothing is built on either layout.", "Meanwhile the reader adds one more finding."))
    again = state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(block.read_text())}.md"
    assert run("job", "ask", asking, str(block)) == (
        0,
        f"{again}\nj1's card {slack_card(1)} under demo:j1-7 did not redraw, so it shows the words it last drew and his answer there answers this question:"
        ' waiting card "demo:j1-7" was answered with "Yes" at 12:31pm',
    )
    data = state.load("demo")
    assert [(question["file"], question["waits"], question["card"]) for question in data["jobs"]["j1"]["questions"]] == [(str(again), "demo:j1-7", slack_card(1))]
    assert [(job_id, question["file"]) for job_id, question in view.drawn_on(data, slack_card(1))] == [("j1", str(again))]
    assert refs.card({"questions": [], "pr_cards": []}, data, "demo:j1-7")["url"] == slack_card(1)
    assert cli.main(["job", "ask", asking, "--drop", str(again)]) == 2
    assert f"is on his card {slack_card(1)}, which only his answer closes" in capsys.readouterr().err
    assert state.kept_in(state.load("demo")["jobs"]["j1"], again) is not None


@pytest.mark.parametrize("drive", ["demo:", ""])
def test_job_ask_redraws_the_card_redraws_names_and_settles_a_duplicate_card_of_its_question_pointing_at_the_live_one(
    run: Run, asking: str, drive: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], slack_calls: list[tuple[str, ...]]
) -> None:
    first, second = "https://acme.slack.com/archives/D123/p1794543147831659", "https://acme.slack.com/archives/D123/p1794548757330289"
    title = SHORT_ASK.splitlines()[0].removeprefix("ASK ")
    kept = state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(SHORT_ASK)}.md"
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(SHORT_ASK)
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(
            state="waiting",
            waits=[second],
            questions=[
                state.new_question(str(kept), title, state.stamp(), card=first) | {"waits": "demo:j1-685522"},
                state.new_question(str(kept), title, state.stamp(), card=second) | {"waits": "demo:j1-686860"},
            ],
        )
    reworded = SHORT_ASK.replace("Meanwhile nothing is built on either layout.", "Meanwhile the reader adds one more finding.")
    monkeypatch.setattr("sys.stdin", io.StringIO(reworded))
    assert cli.main(["job", "ask", asking, "-", "--redraws", f"{drive}j1-9"]) == 2
    assert "--redraws demo:j1-9: no question of o1 holds that card, so j1 takes over none" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", io.StringIO(reworded))
    again = state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(reworded)}.md"
    assert run("job", "ask", asking, "-", "--redraws", f"{drive}j1-685522") == (
        0,
        f"{again}\nhis card {slack_card(1)} asks it under demo:j1-685522;"
        f" demo:j1-686860: his card {second} now shows his words with no buttons, its question superseded",
    )
    assert drawn_waits(slack_calls) == ["demo:j1-685522"]
    assert [argv for argv in slack_calls if argv[0] == "card-answer"] == [("card-answer", "--card", second, "--label", cli.ASKED_AGAIN, "--withdrawn", "--via", slack_card(1))]
    job = state.load("demo")["jobs"]["j1"]
    assert [(question["waits"], question["card"], question["closed"] and question["closed"]["how"]) for question in job["questions"]] == [
        ("demo:j1-686860", second, "superseded"),
        ("demo:j1-685522", slack_card(1), None),
    ]
    assert (job["waits"], [question["waits"] for question in view.open_questions(job)]) == (["demo:j1-685522"], ["demo:j1-685522"])


def test_j4239_a_waits_id_typed_without_its_drive_names_the_card_its_drive_stores_and_every_other_wait_stays_as_typed(
    asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    others = ["demo:j1-7", asking, "o1", "acme/app#12", "merge:acme:app:12", "release:api:acme/app#12", slack_card(1), state.SLOT_WAIT]
    assert [state.waits_of("demo", wait) for wait in ["j1-7", *others]] == ["demo:j1-7", *others]
    assert cli.folded_waits("demo", [("j1-7", None), ("j1-8", "j2")]) == [("demo:j1-7", None), ("demo:j1-8", "j2")]
    with state.edit("demo") as data:
        data["jobs"][asking]["questions"] = [state.new_question("ask.md", "Which layout?", state.stamp(), card=slack_card(1)) | {"waits": "demo:j1-7"}]
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [], "closed": []})
    assert refs.resolved("demo", "{card:j1-7}")["{card:j1-7}"]["url"] == slack_card(1)


def test_a_job_carrying_on_another_jobs_work_on_its_order_takes_over_that_jobs_open_card_with_redraws_folding_that_jobs_question_into_its_own(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], slack_calls: list[tuple[str, ...]]
) -> None:
    title = SHORT_ASK.splitlines()[0].removeprefix("ASK ")
    kept = state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(SHORT_ASK)}.md"
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(SHORT_ASK)
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(state="stopped", questions=[state.new_question(str(kept), title, state.stamp(), card=slack_card(1)) | {"waits": "demo:j1-685522"}])
    run("job", "add", "design", "--order", "o1", "--goal", "design the run page again")
    run("order", "add", "--title", "Other title", "--project", "The drive", "--workstream", "Drive tooling", "design the status page")
    run("job", "add", "design", "--order", "o2", "--goal", "design the status page")
    reworded = SHORT_ASK.replace("Meanwhile nothing is built on either layout.", "Meanwhile the reader adds one more finding.")
    monkeypatch.setattr("sys.stdin", io.StringIO(reworded))
    assert cli.main(["job", "ask", "j3", "-", "--redraws", "demo:j1-685522"]) == 2
    assert "--redraws demo:j1-685522: no question of o2 holds that card, which asks for o1, so j3 takes over none" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", io.StringIO(reworded))
    again = state.drive_dir("demo") / "asks" / f"j2-{state.ask_hash(reworded)}.md"
    assert run("job", "ask", "j2", "-", "--redraws", "demo:j1-685522") == (0, f"{again}\nhis card {slack_card(1)} asks it under demo:j1-685522")
    assert drawn_waits(slack_calls) == ["demo:j1-685522"]
    data = state.load("demo")
    assert ([(question["file"], question["replaced"]) for question in data["jobs"]["j1"]["questions"]], [(question["file"], question["waits"], question["card"]) for question in data["jobs"]["j2"]["questions"]]) == (
        [(str(kept), str(again))],
        [(str(again), "demo:j1-685522", slack_card(1))],
    )
    assert (state.named_job(data, {"waits": "demo:j1-685522"}), view.asked_by(data, {"questions": [{"url": slack_card(9), "card": {"waits": "demo:j1-685522"}}]})) == (
        "j2",
        {slack_card(9): "j2"},
    )
    with state.edit("demo") as data:
        data["jobs"]["j2"]["questions"][0]["closed"] = state.new_close("answered", "One snapshot file", slack_card(1))
    assert served.answered(state.load("demo"), "demo:j1-685522")["file"] == str(again)
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("ASK How should", "ASK Where should")))
    assert cli.main(["job", "ask", "j2", "-", "--redraws", "demo:j1-685522"]) == 2
    assert "--redraws demo:j1-685522: its question there ended answered" in capsys.readouterr().err


def test_j4258_an_ask_jev_reads_as_asking_again_what_n_open_cards_ask_takes_the_newest_card_and_folds_the_other_n_minus_1_into_it_each_closed_asked_again(
    run: Run, asking: str, tmp_path: Path, jev: Jev, monkeypatch: pytest.MonkeyPatch, slack_calls: list[tuple[str, ...]]
) -> None:
    run("order", "add", "--title", "Other title", "--project", "The drive", "--workstream", "Drive tooling", "design the status page")
    for order in ("o1", "o2", "o1"):
        run("job", "add", "reader", "--order", order, "--goal", "research the run page")
    titles = ["Which layout should a finished run's files take?", "Where should a finished run's files go so its page opens?", "How are a finished run's files laid out for its page?"]
    cards = [f"https://acme.slack.com/archives/D123/p{1794300000000100 + n}" for n in (2, 3, 1)]
    waits = ["demo:j1-1", "demo:j2-2", "demo:j3-3"]
    with state.edit("demo") as data:
        for job_id, title, card, wait in zip(("j1", "j2", "j3"), titles, cards, waits):
            data["jobs"][job_id].update(waits=[wait], questions=[state.new_question(str(tmp_path / f"{job_id}.md"), title, state.stamp(), card=card) | {"waits": wait}])
        data["jobs"]["j1"]["waits"] = [cards[0]]
    jev.again = set(titles)
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    kept = state.drive_dir("demo") / "asks" / f"j4-{state.ask_hash(SHORT_ASK)}.md"
    assert run("job", "ask", "j4", "-") == (
        0,
        f"{kept}\nhis card {slack_card(1)} asks it under demo:j2-2;"
        f" demo:j1-1: his card {cards[0]} now shows his words with no buttons, its question superseded;"
        f" demo:j3-3: his card {cards[2]} now shows his words with no buttons, its question superseded",
    )
    [request] = [request for request in jev.asked if "again1" in request["questions"]]
    assert sorted(key for key in request["questions"] if key.startswith("again")) == ["again1", "again2", "again3"]
    assert drawn_waits(slack_calls) == ["demo:j2-2"]
    assert [argv for argv in slack_calls if argv[0] == "card-answer"] == [
        ("card-answer", "--card", card, "--label", cli.ASKED_AGAIN, "--withdrawn", "--via", slack_card(1)) for card in (cards[0], cards[2])
    ]
    data = state.load("demo")
    assert [(job_id, question["waits"], question["card"], question["replaced"], question["closed"]) for job_id in ("j1", "j2", "j3", "j4") for question in data["jobs"][job_id]["questions"]] == [
        *((job_id, "demo:j2-2", slack_card(1), str(kept), None) for job_id in ("j1", "j2", "j3")),
        ("j4", "demo:j2-2", slack_card(1), None, None),
    ]
    assert [data["jobs"][job_id]["waits"] for job_id in ("j1", "j2", "j3")] == [["demo:j2-2"]] * 3


def test_j4258_a_reader_whose_card_another_jobs_redraws_took_draws_nothing_for_it_on_a_later_design_return_in_its_old_words_or_new_ones(
    run: Run, meta: str, jev: Jev, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], slack_calls: list[tuple[str, ...]]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    assert keep(monkeypatch, capsys, "j1", DESIGN_RESEARCH)[0] == 0
    run("job", "add", "design", "--order", "o1", "--goal", "cards: where the copy runs")
    taken = "Which process should run store_copy_at for each store copy?"
    monkeypatch.setattr("sys.stdin", io.StringIO(DESIGN_RESEARCH.replace(DESIGN_TITLE, taken)))
    took = run("job", "ask", "j2", "-", "--redraws", "demo:j1-7")[1].splitlines()[0]
    assert keep(monkeypatch, capsys, "j1", DESIGN_RESEARCH)[0] == 0
    jev.again = {taken}
    assert keep(monkeypatch, capsys, "j1", DESIGN_RESEARCH.replace(DESIGN_TITLE, "Should store_copy_at copy each store on the primary or on a worker?"))[0] == 0
    assert drawn_waits(slack_calls) == ["demo:j1-7", "demo:j1-7"]
    assert [argv for argv in slack_calls if argv[0] == "card-answer"] == []
    jobs = state.load("demo")["jobs"]
    assert [(question["title"], question["waits"], question["card"], question["replaced"]) for question in jobs["j1"]["questions"]] == [
        (DESIGN_TITLE, "demo:j1-7", slack_card(2), took),
        ("Should store_copy_at copy each store on the primary or on a worker?", "demo:j1-7", slack_card(2), took),
    ]
    assert [(question["file"], question["title"], question["card"], question["replaced"], question["closed"]) for question in jobs["j2"]["questions"]] == [(took, taken, slack_card(2), None, None)]

def test_job_ask_on_an_open_incident_draws_his_card_in_its_thread_and_sends_the_line_saying_what_it_blocks_to_the_communicator(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slack_calls: list[tuple[str, ...]]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL fork copies fail on plat", "--incident", "--thread", "1794200000.000100", "--parts", "fix", "first check")
    sent: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, posts: sent.append((lane, posts)) or ["672400"])
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK + "Interim fix: j2 checks the fork copies first\n")
    kept, said = run("job", "ask", "j1", str(block))[1].split("\n", 1)
    assert said == f"his card {slack_card(1)} asks it under demo:j1-7; its line in thread 1794200000.000100 of channel D123 goes out from the communicator once for each change to it"
    assert [argv[argv.index("--thread") + 1] for argv in slack_calls if argv[0] == "dm-status"] == ["1794200000.000100"]
    [(lane, [report])] = sent
    assert (lane, report["kind"], report["to"], report["attr"]) == ("root", "report", ["communicator@demo"], {"job": "j1", "waits": "demo:j1-7", "thread": "1794200000.000100"})
    title = SHORT_ASK.splitlines()[0].removeprefix("ASK ")
    assert report["text"].startswith(f'POST his card {slack_card(1)} asks him "{title}" for j1, and it blocks incident o1: j1 builds its recommended option meanwhile, One snapshot file per finished run')


@pytest.mark.parametrize(("kept", "card_thread"), [([], None), ([["D123", "1794200050.000200", "2026-11-13T10:00:00-08:00", "question"]], "1794200050.000200")])
def test_job_ask_on_an_order_whose_own_thread_is_outside_his_dm_draws_his_card_in_its_thread_of_his_dm_else_at_his_dm_root_and_sends_no_line_to_that_channel(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slack_calls: list[tuple[str, ...]], kept: list[list[str]], card_thread: str | None
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "design the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    with state.edit("demo") as data:
        data["orders"]["o1"].update(thread=["C0ALERT1", "1794200000.000100"], threads=kept)
    sent: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, posts: sent.append((lane, posts)) or ["672400"])
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    assert run("job", "ask", "j1", str(block))[1].split("\n", 1)[1] == f"his card {slack_card(1)} asks it under demo:j1-7"
    assert [argv[argv.index("--thread") + 1] if "--thread" in argv else None for argv in slack_calls if argv[0] == "dm-status"] == [card_thread]
    assert sent == []


def test_job_ask_sends_his_cards_line_outside_his_dm_to_the_communicator_once_for_each_change_to_it(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit("demo") as data:
        data["orders"]["o1"].update(thread=["C0ALERT1", "1794200000.000100"], incident=True)
    monkeypatch.setattr(cli, "post_ask", POST_ASK)
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: {"permalink": slack_card(1)} if argv[0] == "dm-status" else {})
    kept: dict[str, dict] = {}
    monkeypatch.setattr(tools, "sent", lambda name, lane, posts: [kept.setdefault(post["id"], post) and "672400" for post in posts])
    block = tmp_path / "ask.md"
    retitled = SHORT_ASK.replace("ASK How should", "ASK Where should")
    for text, redraws in (
        (SHORT_ASK, []),
        (SHORT_ASK.replace("Meanwhile nothing is built on either layout.", "Meanwhile the reader adds one more finding."), []),
        (retitled, ["--redraws", "demo:j1-7"]),
        (retitled, ["--redraws", "demo:j1-7"]),
    ):
        block.write_text(text + "Interim fix: j2 checks the fork copies first\n")
        assert run("job", "ask", asking, str(block), *redraws)[0] == 0
    blocks = ", and it blocks incident o1: j1 builds its recommended option meanwhile, One snapshot file per finished run, written when it ends, old runs backfilled: opens in 3 requests"
    assert [report["text"] for report in kept.values()] == [
        f'POST his card {slack_card(1)} asks him "{text.splitlines()[0].removeprefix("ASK ")}" for j1{blocks}' for text in (SHORT_ASK, retitled)
    ]


def test_job_ask_reads_terms_more_than_ten_of_the_drives_questions_use_as_naming_no_one_subject(run: Run, asking: str, tmp_path: Path) -> None:
    refused = state.new_question(str(tmp_path / "j1.md"), "Which drive install command restarts the lanes?", state.stamp(), refused="refused: its Holds line names no change")
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK.replace(SHORT_ASK.split("\n")[0], "ASK Should the drive install command prune old worktrees?"))
    topics = ["rebuild caches", "rotate tokens", "reload hooks", "refresh skills", "repair links", "resume agents", "sync plugins", "trim journals", "pin versions", "clear locks", "renew certs"]
    answered = [
        state.new_question(str(tmp_path / f"{n}.md"), f"Should the drive install command {topic}?", state.stamp(), closed=state.new_close("answered", "Ring", None, answer="a1"))
        for n, topic in enumerate(topics)
    ]
    for others, stays in (([], False), (answered, True)):
        with state.edit("demo") as data:
            data["jobs"]["j1"]["questions"] = [refused, *others]
        assert run("job", "ask", asking, str(block))[0] == 0
        assert (refused["title"] in [question["title"] for question in state.load("demo")["jobs"]["j1"]["questions"]]) is stays


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
        kept.append(run("job", "ask", asking, "-")[1].splitlines()[0])
    printed = [run("job", "card", path, "--waits", f"demo:j1-{n}")[1] for n, path in enumerate(kept, 7)]
    unasked = json.loads(printed[0]) | {"waits": "demo:hier-1"}
    unanswerable = {"title": "Which way?", "body": "One line.", "options": [], "recommended": "", "waits": "demo:hier-2", "drive": "demo"}
    reordered = unasked | {"waits": "demo:hier-3", "options": unasked["options"][::-1]}
    shown = [open_card(n, card) for n, card in enumerate([*map(json.loads, printed), unasked, unanswerable, reordered])]
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": shown, "pr_cards": [], "closed": []})
    jobs = state.load("demo")["jobs"]
    assert cli.main(["cards", "redraw"]) == 2
    out, err = capsys.readouterr()
    assert out.splitlines() == [json.dumps(json.loads(card) | {"replaces": each["url"]}) for card, each in zip([*printed, json.dumps(unasked)], shown)]
    assert "demo:hier-2: the ASK is out of shape" in err and "0 options; an ASK has" in err
    assert "demo:hier-3: the ASK is out of shape" in err and "is not first: put it first" in err
    assert (state_root / "demo" / "asks" / "hier-1.md").is_file()
    assert state.load("demo")["jobs"] == jobs
    assert run("cards", "redraw", "--waits", "demo:j1-8") == (0, json.dumps(json.loads(printed[1]) | {"replaces": shown[1]["url"]}))
    assert cli.main(["cards", "redraw", "--waits", "demo:j1-9"]) == 2
    assert "demo:j1-9: not one of his open cards" in capsys.readouterr().err


def test_cards_redraw_draws_a_card_from_the_ask_a_question_linked_to_it_keeps_when_it_asks_the_cards_question(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    path = run("job", "ask", asking, "-")[1].splitlines()[0]
    printed = run("job", "card", path, "--waits", "demo:j1-7")[1]
    shown = open_card(0, json.loads(printed))
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0] |= {"waits": None, "card": shown["url"]}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
    assert run("cards", "redraw") == (0, json.dumps(json.loads(printed) | {"replaces": shown["url"]}))
    assert [(question["file"], question["waits"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [(path, "demo:j1-7")]


def test_cards_redraw_rebuilds_a_card_whose_linked_question_asks_something_else_and_keeps_it_in_that_questions_place(
    run: Run, asking: str, state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    kept = []
    for text in (SHORT_ASK, SHORT_ASK.replace("ASK How should", "ASK Where should")):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        kept.append(run("job", "ask", asking, "-")[1].splitlines()[0])
    printed = run("job", "card", kept[1], "--waits", "demo:j1-8")[1]
    shown = open_card(0, json.loads(printed))
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"] = [data["jobs"]["j1"]["questions"][0] | {"waits": None, "card": shown["url"]}]
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
    redrawn = json.dumps(json.loads(printed) | {"replaces": shown["url"]})
    assert run("cards", "redraw", "--waits", "demo:j1-8") == (0, redrawn)
    rebuilt = state_root / "demo" / "asks" / "j1-8.md"
    assert limits.redrawn(rebuilt.read_text()).title == json.loads(printed)["title"]
    assert rebuilt.read_text().endswith("\nFindings:\n" + SHORT_ASK.split("Findings:\n", 1)[1])
    assert [(question["file"], question["waits"], question["card"], question["holds"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [
        (str(rebuilt), "demo:j1-8", shown["url"], "")
    ]
    assert run("cards", "redraw", "--waits", "demo:j1-8") == (0, redrawn)


def test_cards_redraw_draws_an_open_question_cc_slack_forgot_over_its_card_where_it_sits_and_no_question_that_ended(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    run("job", "ask", asking, "-")
    redrawn = json.dumps(json.loads(run("job", "card", asking)[1]) | {"replaces": drawn_card(1)})
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [], "closed": []})
    assert run("cards", "redraw") == (0, redrawn)
    assert run("cards", "redraw", "--waits", "demo:j1-7") == (0, redrawn)
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("answered", "Ring", None, answer="a1")
    assert run("cards", "redraw") == (0, "")
    assert cli.main(["cards", "redraw", "--waits", "demo:j1-7"]) == 2
    assert "demo:j1-7: not one of his open cards" in capsys.readouterr().err

def test_job_card_redraws_a_kept_ask_edited_by_hand_from_his_card_as_stored_and_refuses_it_while_no_card_is_stored(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    kept = Path(run("job", "ask", asking, "-")[1].splitlines()[0])
    code, printed = run("job", "card", str(kept), "--waits", "demo:j1-7")
    assert code == 0
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0]["card"] = "https://acme.slack.com/archives/D123/p1794300000000100"
    trimmed = SHORT_ASK.replace("GET 2 page ranges\n", "")
    kept.write_text(trimmed)
    for argv in (["job", "card", asking], ["job", "card", str(kept), "--waits", "demo:j1-7"]):
        assert cli.main(argv) == 2
        assert f"{kept} no longer holds the words drive job ask kept there, so it draws no card" in capsys.readouterr().err
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [open_card(0, json.loads(printed))], "pr_cards": [], "closed": []})
    view.refresh_cards("demo")
    assert run("job", "card", asking) == (0, printed)
    assert run("cards", "redraw") == (0, json.dumps(json.loads(printed) | {"replaces": "https://acme.slack.com/archives/D123/p1794300000000100"}))
    monkeypatch.setattr("sys.stdin", io.StringIO(trimmed))
    rekept = run("job", "ask", asking, "-")[1].splitlines()[0]
    assert Path(rekept).name == f"j1-{state.ask_hash(trimmed)}.md"
    code, redrawn = run("job", "card", asking, "--waits", "demo:j1-7")
    assert code == 0 and redrawn != printed


def posted_question(ts: str, asked: limits.Question) -> dict:
    picks = [
        {
            "type": "section",
            "block_id": f"card-option-{n}",
            "text": {"type": "mrkdwn", "text": f"*{n + 1}. {option}*"},
            "accessory": {"type": "button", **({"style": "primary"} if option == asked.recommended else {}), "value": json.dumps({"id": "demo:j1-7", "label": option, "q": asked.title, "o": asked.options})},
        }
        for n, option in enumerate(asked.options)
    ]
    return {"ts": ts, "raw": {"blocks": [{"type": "header", "text": {"type": "plain_text", "text": asked.title}}, {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join([*asked.summary, "*Show me*"])}}, *picks]}}


def test_job_card_redraws_a_kept_ask_edited_by_hand_whose_card_cc_slack_forgot_while_his_card_in_slack_shows_its_words(
    run: Run, asking: str, slack_thread: Thread, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    kept = Path(run("job", "ask", asking, "-")[1].splitlines()[0])
    assert run("job", "card", str(kept), "--waits", "demo:j1-7")[0] == 0
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0]["card"] = "https://acme.slack.com/archives/D123/p1794300000000100"
    cut = SHORT_ASK.replace(", written when it ends, old runs backfilled", "")
    kept.write_text(cut)
    slack_thread.messages = [posted_question("1794300000.000100", limits.redrawn(cut))]
    code, printed = run("job", "card", asking)
    assert (code, json.loads(printed)["options"][0], json.loads(printed)["waits"]) == (0, "One snapshot file per finished run: opens in 3 requests", "demo:j1-7")
    assert ("--url", "https://acme.slack.com/archives/D123/p1794300000000100", "--raw") in slack_thread.reads
    slack_thread.messages = [posted_question("1794300000.000100", limits.redrawn(SHORT_ASK))]
    assert cli.main(["job", "card", asking]) == 2
    assert f"{kept} no longer holds the words drive job ask kept there, so it draws no card" in capsys.readouterr().err


def test_job_card_redraws_a_question_it_asked_in_words_kept_before_the_new_ask_shape_and_holds_a_file_no_question_keeps_to_that_shape(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = (
        "ASK Which layout should a finished run keep?\n"
        "The run page guesses where each journal file's page map starts.\n"
        "I recommend one snapshot file per finished run.\n"
        "- One snapshot file per finished run, so its page opens in 3 requests (recommended)\n"
        "- A map file beside each journal file, twice the objects\n"
        "Findings:\n"
        "Direct fix: none: rebuilt from the posted card\n"
    )
    rebuilt = state.rebuilt_ask("demo", "demo:j1-8")
    rebuilt.parent.mkdir(exist_ok=True)
    rebuilt.write_text(old)
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(str(rebuilt), "Which layout should a finished run keep?", state.stamp(), card=link) | {"waits": "demo:j1-8"})
    unasked = tmp_path / "ask.md"
    unasked.write_text(old)
    assert cli.main(["job", "card", str(unasked), "--waits", "demo:j1-9"]) == 2
    assert "the ASK is out of shape" in capsys.readouterr().err
    code, printed = run("job", "card", asking, str(rebuilt), "--waits", "demo:j1-8")
    assert (code, json.loads(printed)["waits"], json.loads(printed)["options"]) == (
        0,
        "demo:j1-8",
        ["One snapshot file per finished run, so its page opens in 3 requests", "A map file beside each journal file, twice the objects"],
    )
    assert [(question["file"], question["waits"], question["card"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [(str(rebuilt), "demo:j1-8", link)]


def test_job_card_from_a_relative_path_gives_the_question_kept_there_its_waits_id(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    kept = Path(run("job", "ask", asking, str(block))[1].splitlines()[0])
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
        kept.append(Path(path.splitlines()[0]))
    assert kept[0] != kept[1]
    assert [path.read_text().split("\n")[0] for path in kept] == [
        "ASK How should a finished run's files be laid out so its page opens without guessing?",
        "ASK Where should a finished run's files be laid out so its page opens without guessing?",
    ]
    assert [question["file"] for question in state.load("demo")["jobs"]["j1"]["questions"]] == [str(path) for path in kept]


def undrawn(name: str, job_id: str, file: Path, card: dict) -> str:
    raise slack.SlackError("dm-status timed out")


def test_a_job_keeps_every_question_it_asks_each_open_until_his_answer_to_its_own_card(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    titles = ["How should", "Where should", "When should"]
    kept = []
    for title in titles:
        if title == titles[-1]:
            monkeypatch.setattr(cli, "post_ask", undrawn)
        monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("ASK How should", f"ASK {title}")))
        kept.append(run("job", "ask", asking, "-")[1].splitlines()[0])
    links = [f"https://acme.slack.com/archives/D123/p179430000000010{n}" for n in range(3)]
    shown = [
        {"question": title, "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": title, "waits": waits}}
        for title, link, waits in zip(titles, links, ["demo:j1-7", "demo:j1-8", "demo:j1-2"], strict=True)
    ]
    closed: list[dict] = []
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": list(shown), "pr_cards": [], "closed": list(closed)})
    for n, (path, link) in enumerate(zip(kept[:2], links[:2], strict=True)):
        assert run("job", "card", path, "--waits", f"demo:j1-{7 + n}")[0] == 0
        assert run("job", "set", asking, "--card", link)[0] == 0
    assert run("job", "set", asking, "--card", links[2])[0] == 0
    questions = state.load("demo")["jobs"]["j1"]["questions"]
    assert [(question["file"], question["waits"], question["card"]) for question in questions] == [
        (kept[0], "demo:j1-7", links[0]),
        (kept[1], "demo:j1-8", links[1]),
        (kept[2], "demo:j1-9", None),
        (None, "demo:j1-2", links[2]),
    ]
    answered = shown.pop(0)
    closed.append({"waits": "demo:j1-7", "title": titles[0], "prs": [], "asked": answered["asked"], "url": links[0], "answer": {"label": "Yes", "at": answered["asked"]}, "tapped": "", "replaced_by": ""})
    status = run("status", asking)[1]
    assert f'card {links[0]} answered "Yes" at {render.when(answered["asked"])} (demo:j1-7)' in status
    assert f"asks him: Where should a finished run's files be laid out so its page opens without…; card {links[1]} open since {render.when(shown[0]['asked'])} (demo:j1-8)" in status
    assert f"card demo:j1-9 never drawn, kept in {kept[2]}" in status
    assert f"asks him: When should; card {links[2]} open since {render.when(shown[1]['asked'])} (demo:j1-2)" in status


def test_a_redrawn_card_moves_to_the_question_that_redoes_it_and_a_question_with_no_card_up_yet_shows_its_waits_id(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
    kept = []
    for text in (SHORT_ASK, SHORT_ASK.replace("opens in 3 requests", "opens in 3 calls"), SHORT_ASK.replace("ASK How should", "ASK Where should")):
        if "Where should" in text:
            monkeypatch.setattr(cli, "post_ask", undrawn)
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        kept.append(run("job", "ask", asking, "-")[1].splitlines()[0])
    run("job", "card", kept[0], "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    run("job", "card", kept[1], "--waits", "demo:j1-7")
    questions = state.load("demo")["jobs"]["j1"]["questions"]
    assert [(question["file"], question["waits"], question["card"]) for question in questions] == [(kept[1], "demo:j1-7", link), (kept[2], "demo:j1-8", None)]
    status = run("status", asking)[1]
    assert f"card {link} open since {render.when(shown['asked'])} (demo:j1-7)" in status
    assert f"card demo:j1-8 never drawn, kept in {kept[2]}" in status


def test_a_question_asked_again_in_the_same_words_redraws_its_one_card_under_the_waits_id_of_its_first_ask(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1].splitlines()[0]
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
        (json.loads(again)["question"], "demo:j1-7", drawn_card(3), asked)
    ]


def test_a_question_asked_twice_before_its_card_is_drawn_draws_its_newest_block_on_one_card(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    kept = []
    for text in (SHORT_ASK, SHORT_ASK.replace("opens in 3 requests", "opens in 3 calls")):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        kept.append(run("job", "ask", asking, "-")[1].splitlines()[0])
    first = json.loads(run("job", "card", kept[0], "--waits", "demo:j1-7")[1])
    second = json.loads(run("job", "card", kept[1], "--waits", "demo:j1-8")[1])
    assert (first["waits"], second["waits"]) == ("demo:j1-7", "demo:j1-7")
    assert first == second and "opens in 3 calls" in first["body"]
    assert [(question["file"], question["waits"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [(kept[1], "demo:j1-7")]
    code, out = run("job", "ask", asking, "--drop", kept[0])
    assert (code, out.endswith("a later ask of it replaced its question, so only the file is deleted, so nothing counts it open")) == (0, True)
    assert not Path(kept[0]).exists() and Path(kept[1]).exists()


def test_a_question_asked_again_after_he_answered_its_card_is_kept_settled_in_the_same_words_and_goes_on_a_card_of_its_own_in_other_words(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, settled_notes: list[tuple[str, str]]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    listed = [shown]
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": list(listed), "pr_cards": [], "closed": []})
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1].splitlines()[0]
    run("job", "card", first, "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    listed.clear()
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("answered", "Yes", link)
    reworded = SHORT_ASK.replace("opens in 3 requests", "opens in 3 calls")
    monkeypatch.setattr("sys.stdin", io.StringIO(reworded))
    again = str(state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(reworded)}.md")
    assert run("job", "ask", asking, "-") == (
        0,
        f'{again} keeps j1\'s question settled, no card: he already answered this same question, asked in the same words, at {link}: "Yes". Go ahead on it; j1 builds it now',
    )
    assert settled_notes == [("j1", link)]
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("ASK How should", "ASK When should")))
    other = run("job", "ask", asking, "-")[1].splitlines()[0]
    assert json.loads(run("job", "card", other, "--waits", "demo:j1-8")[1])["waits"] == "demo:j1-8"
    assert [(question["file"], question["waits"], question["closed"] and question["closed"]["words"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [
        (first, "demo:j1-7", "Yes"),
        (again, None, "Yes"),
        (other, "demo:j1-8", None),
    ]


def test_a_withdrawn_card_asked_again_with_redraws_by_its_job_or_another_of_its_order_redraws_under_its_waits_id_leaving_its_own_job_and_folding_into_the_other(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    listed, closed = [shown], []
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": list(listed), "pr_cards": [], "closed": list(closed)})
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1].splitlines()[0]
    run("job", "card", first, "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    listed.clear()
    withdrawn = {"label": "his order replaced it", "at": state.stamp(), "withdrawn": True}
    closed.append({"waits": "demo:j1-7", "title": "How should?", "prs": [], "asked": shown["asked"], "url": link, "answer": withdrawn, "tapped": "", "replaced_by": ""})
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("withdrawn", withdrawn["label"], None)
    assert f"card {link} withdrawn at {render.when(withdrawn['at'])}: his order replaced it (demo:j1-7)" in run("status", asking)[1]
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("opens in 3 requests", "opens in 3 calls")))
    again = json.loads(run("job", "ask", asking, "-", "--json", "--redraws", "demo:j1-7")[1])
    assert again["waits"] == "demo:j1-7"
    assert json.loads(run("job", "card", again["question"], "--waits", "demo:j1-9")[1])["waits"] == "demo:j1-7"
    assert [(question["file"], question["waits"], question["card"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [(again["question"], "demo:j1-7", drawn_card(2))]
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("withdrawn", withdrawn["label"], None)
    run("job", "add", "reader", "--order", "o1", "--goal", "design it again")
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("opens in 3 requests", "opens in 2 calls")))
    assert json.loads(run("job", "ask", "j2", "-", "--json", "--redraws", "demo:j1-7")[1])["waits"] == "demo:j1-7"
    jobs = state.load("demo")["jobs"]
    assert ([(question["waits"], question["card"]) for question in jobs["j2"]["questions"]], [(question["file"], question["replaced"]) for question in jobs["j1"]["questions"]]) == (
        [("demo:j1-7", drawn_card(3))],
        [(again["question"], jobs["j2"]["questions"][0]["file"])],
    )


def test_job_card_refuses_a_question_he_answered_under_a_new_waits_id_and_never_swaps_in_the_answered_one(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    listed = [shown]
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": list(listed), "pr_cards": [], "closed": []})
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1].splitlines()[0]
    run("job", "card", first, "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    listed.clear()
    words = "1 but find a less hacky and more first-principles way"
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("answered", words, link)
    title = SHORT_ASK.split("\n")[0].removeprefix("ASK ")
    for argv in (["job", "card", first, "--waits", "demo:j1-9"], ["job", "card", asking], ["job", "card", asking, first]):
        assert cli.main(argv) == 2
        assert capsys.readouterr().err == f'drive: refused: j1\'s question "{title}" kept in {first} ended answered, so its card never draws again: "{words}" ({link})\n'
    reworded = SHORT_ASK.replace("opens in 3 requests", "opens in 3 calls")
    monkeypatch.setattr("sys.stdin", io.StringIO(reworded))
    again = run("job", "ask", asking, "-")[1].split(" keeps ")[0]
    assert cli.main(["job", "card", again, "--waits", "demo:j1-9"]) == 2
    assert f'refused: j1\'s question "{title}" kept in {again} ended answered, so its card never draws again: "{words}" ({link})' in capsys.readouterr().err
    assert [(question["file"], question["waits"], question["card"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [
        (first, "demo:j1-7", link),
        (again, None, None),
    ]


def test_j3668_an_answered_question_asked_again_settles_on_his_answer_whatever_its_shape_keeping_one_question_in_its_file_the_watch_never_redraws(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cards_drawn: list[dict]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    listed = [shown]
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": list(listed), "pr_cards": [], "closed": []})
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1].splitlines()[0]
    run("job", "set", asking, "--card", link)
    listed.clear()
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("answered", "Rules in the shared wasm", link)
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    assert run("job", "ask", asking, "-")[1].startswith(f"{first} keeps j1's question settled, no card")
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("- Map at the start of each file:", "- Ask the reader to retry the map at the start of each file:")))
    again = run("job", "ask", asking, "-")[1].split(" keeps ")[0]
    questions = state.load("demo")["jobs"]["j1"]["questions"]
    assert [(question["file"], question["waits"], question["card"], question["refused"], question["closed"]["how"]) for question in questions] == [
        (first, "demo:j1-7", link, None, "answered"),
        (again, None, None, None, "answered"),
    ]
    watch.draw_questions(watch.Watcher("demo", offset=state.events_end("demo"), cards=view.NO_CARDS), state.now())
    assert len(cards_drawn) == 1
    assert cli.main(["job", "ask", asking, "--drop", first]) == 2
    assert f"is on his card {link}, ended answered there, so nothing counts it open" in capsys.readouterr().err


def test_j3668_a_card_the_watch_cannot_draw_keeps_one_refusal_under_the_waits_id_its_question_holds(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cards_drawn: list[dict]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    draw = cli.post_ask

    def locked(name: str, job_id: str, file: Path, card: dict) -> str:
        raise slack.SlackError("dm-status: database is locked")

    monkeypatch.setattr(cli, "post_ask", locked)
    kept = run("job", "ask", asking, str(block))[1].splitlines()[0]
    monkeypatch.setattr(cli, "post_ask", draw)
    Path(kept).write_text(SHORT_ASK.replace("Meanwhile nothing is built on either layout.", "Meanwhile the journal keeps its layout."))
    watcher = watch.Watcher("demo", offset=state.events_end("demo"), cards=view.NO_CARDS)
    watch.draw_questions(watcher, state.now())
    watch.draw_questions(watcher, state.now())
    refusal = (
        f"{kept} no longer holds the words drive job ask kept there, so it draws no card: a kept question changes only through"
        " drive job ask, which checks the new words against every rule, so keep them with drive job ask j1 <file> and draw the file it prints"
    )
    assert [(question["waits"], question["card"], question["refused"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [("demo:j1-7", None, refusal)]
    assert cards_drawn == []


def test_a_kept_question_his_answer_to_the_same_question_settles_before_the_watch_draws_it_draws_no_card(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settled_notes: list[tuple[str, str]], cards_drawn: list[dict]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    draw = cli.post_ask

    def locked(name: str, job_id: str, file: Path, card: dict) -> str:
        raise slack.SlackError("dm-status: database is locked")

    monkeypatch.setattr(cli, "post_ask", locked)
    kept = run("job", "ask", asking, str(block))[1].splitlines()[0]
    monkeypatch.setattr(cli, "post_ask", draw)
    card = "https://acme.slack.com/archives/D123/p1794300000000100"
    words = "One snapshot file per finished run"
    run("job", "add", "reader", "--order", "o1", "--goal", "lay out the files")
    with state.edit("demo") as data:
        data["jobs"]["j2"]["questions"] = [state.new_question(None, SHORT_ASK.splitlines()[0].removeprefix("ASK "), state.stamp(), card=card, closed=state.new_close("answered", words, card))]
    watch.draw_questions(watch.Watcher("demo", offset=state.events_end("demo"), cards=view.NO_CARDS), state.now())
    assert [(question["file"], question["card"], question["closed"]["how"], question["closed"]["words"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [
        (kept, None, "answered", words)
    ]
    assert (settled_notes, cards_drawn) == ([("j1", card)], [])


def test_job_ask_drop_throws_away_a_near_copy_whose_card_did_not_draw_so_nothing_counts_it(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1].splitlines()[0]
    run("job", "card", first, "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    near_copy = "ASK How should a finished run's files be laid out so its page opens with no guessing?"
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace(SHORT_ASK.split("\n")[0], near_copy)))
    monkeypatch.setattr(cli, "post_ask", undrawn)
    copy = run("job", "ask", asking, "-")[1].splitlines()[0]
    assert f"card demo:j1-8 never drawn, kept in {copy}" in run("status", asking)[1]
    assert cli.main(["job", "ask", asking, "--drop", first]) == 2
    assert f"kept in {first} is on his card {link}, which only his answer closes" in capsys.readouterr().err
    code, out = run("job", "ask", asking, "--drop", copy)
    assert code == 0 and out.startswith('j1 no longer asks "How should') and f"from {copy}: its question kept there is dropped and the file deleted" in out
    job = state.load("demo")["jobs"]["j1"]
    assert [(question["file"], question["waits"], question["card"]) for question in job["questions"]] == [(first, "demo:j1-7", link)]
    assert job["said"] == SHORT_ASK.split("\n")[0].removeprefix("ASK ")
    assert not Path(copy).exists()
    status = run("status", asking)[1]
    assert copy not in status and f"card {link} open since " in status and "(demo:j1-7)" in status
    assert json.loads(run("job", "card", asking)[1])["title"] == job["said"]


def test_job_ask_refuses_a_question_another_jobs_open_card_asks_naming_the_wait_on_that_card(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1].splitlines()[0]
    run("job", "card", first, "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    run("job", "add", "reader", "--order", "o1", "--goal", "read the run page")
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("opens in 3 requests", "opens in 3 calls")))
    assert cli.main(["job", "ask", "j2", "-"]) == 2
    refusal = capsys.readouterr().err
    assert f"\n- j1 already asks him \"How should a finished run's files be laid out so its page opens without guessing?\" on its open card {link} (demo:j1-7)" in refusal
    assert "drive job set j2 --waits demo:j1-7, which frees j2 at his answer" in refusal
    assert state.load("demo")["jobs"]["j2"]["questions"] == []


def test_the_row_ceiling_questions_built_on_the_open_one_where_rows_are_counted_reach_him_as_one_card_his_one_answer_ends(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cards_drawn: list[dict]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [], "closed": []})
    titles = [
        "Where should SandSQL count each run's rows read from the shared server, and refuse past a ceiling?",
        "What ceiling should SandSQL put on the rows one run reads from the shared server?",
        "Should a person's chats count toward the same hourly row ceiling as a run?",
    ]
    run("job", "add", "design", "--order", "o1", "--goal", "design the row ceiling")
    kept: list[str] = []
    for job_id, title in zip([asking, "j2", "j2"], titles, strict=True):
        text = SHORT_ASK.replace(SHORT_ASK.split("\n")[0], f"ASK {title}")
        monkeypatch.setattr("sys.stdin", io.StringIO(text.replace("(1) ", f"Replaces: {kept[0]}\n(1) ") if kept else text))
        kept.append(run("job", "ask", job_id, "-")[1].splitlines()[0])
    assert [(drawn["file"], drawn["waits"]) for drawn in cards_drawn] == [(kept[0], "demo:j1-7"), (kept[1], "demo:j1-7"), (kept[2], "demo:j1-7")]
    jobs = state.load("demo")["jobs"]
    assert [(question["file"], question["waits"], question["replaced"]) for job_id in ("j1", "j2") for question in jobs[job_id]["questions"]] == [
        (kept[0], "demo:j1-7", kept[1]),
        (kept[1], "demo:j1-7", kept[2]),
        (kept[2], "demo:j1-7", None),
    ]
    assert f"asked as part of the question kept in {kept[1]}" in run("status", asking)[1]
    for file in kept[:2]:
        assert cli.main(["job", "card", file, "--waits", "demo:j1-7"]) == 2
        assert "so it draws no card of its own: draw that file" in capsys.readouterr().err
    assert json.loads(run("job", "card", kept[2], "--waits", "demo:j1-7")[1])["title"] == titles[2]
    closed = state.new_close("answered", "Count at the server, 50,000 rows, chats count too", "https://acme.slack.com/archives/D123/p1794533808759659")
    with state.edit("demo") as data:
        ended = superseded.end(data, "j2", data["jobs"]["j2"]["questions"][-1], closed)
    assert sorted(question["title"] for _, question in ended) == sorted(titles)
    assert [question["closed"]["words"] for job in state.load("demo")["jobs"].values() for question in job["questions"]] == [closed["words"]] * 3


def test_a_replaces_line_naming_a_question_his_answer_closed_or_no_kept_file_is_refused_with_nothing_kept(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1].splitlines()[0]
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("answered", "One snapshot file", "https://acme.slack.com/archives/D123/p1794300000000100")
    run("job", "add", "design", "--order", "o1", "--goal", "design the run page")
    for named, refusal in ((first, "ended answered, so it asks him nothing to replace"), (str(tmp_path / "ask.md"), "names no file a question of this drive is kept in")):
        later = SHORT_ASK.replace("ASK How should", "ASK Where should").replace("(1) ", f"Replaces: {named}\n(1) ")
        monkeypatch.setattr("sys.stdin", io.StringIO(later))
        assert cli.main(["job", "ask", "j2", "-"]) == 2
        assert refusal in capsys.readouterr().err
    assert state.load("demo")["jobs"]["j2"]["questions"] == []


def test_a_question_built_on_a_drawn_card_redraws_that_card_and_the_question_it_folded_in_asked_again_stays_on_it(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cards_drawn: list[dict]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794530848545449"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1].splitlines()[0]
    run("job", "card", first, "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    run("job", "add", "design", "--order", "o1", "--goal", "design the run page")
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("ASK How should", "ASK Where should").replace("(1) ", f"Replaces: {first}\n(1) ")))
    later = json.loads(run("job", "ask", "j2", "-", "--json")[1])
    assert (later["waits"], later["card"], cards_drawn[-1]["waits"]) == ("demo:j1-7", drawn_card(2), "demo:j1-7")
    card = json.loads(run("job", "card", later["question"], "--waits", "demo:j1-7")[1])
    assert (card["waits"], card["title"]) == ("demo:j1-7", later["title"])
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("built on either layout.", "built on either layout yet.")))
    again = json.loads(run("job", "ask", asking, "-", "--json")[1])
    assert (again["waits"], len(cards_drawn)) == ("demo:j1-7", 2)
    assert cli.main(["job", "card", again["question"], "--waits", "demo:j1-7"]) == 2
    assert f"is asked on his card as part of the question kept in {later['question']}" in capsys.readouterr().err
    jobs = state.load("demo")["jobs"]
    assert [(question["file"], question["waits"], question["card"], question["replaced"]) for job_id in ("j1", "j2") for question in jobs[job_id]["questions"]] == [
        (again["question"], "demo:j1-7", drawn_card(2), later["question"]),
        (later["question"], "demo:j1-7", drawn_card(2), None),
    ]


def test_his_words_under_a_card_answer_it_so_its_job_never_redraws_it_with_a_new_question(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev, send_message: Callable[..., None]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
    view.refresh_cards("demo")
    monkeypatch.setattr(slack, "cc_slack", lambda *args: {"answered": True})
    kept = []
    for text in (SHORT_ASK, SHORT_ASK.replace("ASK How should", "ASK Where should")):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        kept.append(run("job", "ask", asking, "-")[1].splitlines()[0])
    run("job", "card", kept[0], "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    with state.edit("demo") as data:
        data["jobs"][asking]["agent"] = "agent-1"
    reply = "https://acme.slack.com/archives/D123/p1794300000000400?thread_ts=1794300000.000100"
    sender = "from root@demo, the session session-under-test that started its agent agent-1"
    assert cli.main(["answer", "a1", "--words", "its one snapshot file, just wait for the copy", "--link", reply]) == 2
    assert f"  j1 waits on o1 and has not heard a1: SendMessage it his words verbatim {sender}, or mark it untouched" in capsys.readouterr().err
    send_message("demo", "j1", "His answer, verbatim: its one snapshot file, just wait for the copy")
    assert run("answer", "a1", "--stands", "demo:j1-8") == (
        0,
        "his answer a1 recorded: 1 standing, 0 superseded, 0 reopened by it; 1 waiting job sent his words, 0 untouched",
    )
    assert cli.main(["job", "card", kept[1], "--waits", "demo:j1-7"]) == 2
    refusal = capsys.readouterr().err
    assert 'demo:j1-7: his answer on that card closed its question' in refusal
    assert '"its one snapshot file, just wait for the copy"' in refusal
    assert [question["file"] for question in state.load("demo")["jobs"]["j1"]["questions"] if question["waits"] == "demo:j1-7"] == [kept[0]]
    assert cli.main(["answer", "a2", "--words", "One snapshot file", "--link", link]) == 2
    assert f"  j1 waits on o1 and has not heard a2: SendMessage it his words verbatim {sender}, or mark it untouched" in capsys.readouterr().err
    send_message("demo", "j1", "One snapshot file", waiting=True)
    assert run("answer", "a2", "--stands", "demo:j1-8")[0] == 0
    when = SHORT_ASK.replace("ASK How should", "ASK When should")
    monkeypatch.setattr("sys.stdin", io.StringIO(when))
    assert run("job", "ask", asking, "-")[1].splitlines()[0] == str(state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(when)}.md")
    assert state.load("demo")["jobs"]["j1"]["questions"][-1]["closed"] is None
    assert not [request for request in jev.asked if "decides" in request["questions"]]
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    assert run("job", "ask", asking, "-") == (
        0,
        f"{kept[0]} keeps j1's question settled, no card: he already answered this same question, asked in the same words, at {link}:"
        ' "its one snapshot file, just wait for the copy". Go ahead on it; j1 builds it now',
    )


def test_job_ask_never_settles_a_question_on_his_message_in_its_orders_thread_so_it_draws_his_card(
    run: Run, demo: str, slack_thread: Thread, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, jev: Jev
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS | {"team_url": "https://acme.slack.com/"})
    view.refresh_cards("demo")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "design the run page", "--thread", "1794200000.000100")
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    slack_thread.post(state.now(), from_user=True, text="keep one snapshot file per finished run, no guessing")
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    assert run("job", "ask", "j1", str(block)) == (0, f"{state.drive_dir('demo') / 'asks' / f'j1-{state.ask_hash(SHORT_ASK)}.md'}\nhis card {drawn_card(1)} asks it under demo:j1-7")
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], [question["closed"] for question in job["questions"]]) == ("running", [None])
    assert ("--channel", "D123", "--thread", "1794200000.000100", "--limit", "1000") in slack_thread.reads
    assert not [request for request in jev.asked if "decides" in request["questions"]]


def test_job_ask_hands_jev_his_order_from_his_slack_message_and_his_thread_words_without_the_lines_he_quoted_so_an_alerts_amounts_are_never_his(
    run: Run, demo: str, slack_thread: Thread, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, jev: Jev
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS | {"team_url": "https://acme.slack.com/"})
    view.refresh_cards("demo")
    alert = "snapshot monitor: a finished snapshot took 15 minutes to copy 64 rows"
    his = "why is it copied twice? keep each finished snapshot 30 days"
    slack_thread.messages.append({"ts": "1794200000.000100", "thread_ts": "1794200000.000100", "from_user": True, "text": f"&gt; {alert}\n{his}"})
    filed = ["order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", f"> {alert} {his}"]
    run(*filed, "--from", "slack:D123/1794200000.000100/1794200000.000100")
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    slack_thread.post(state.now(), from_user=True, text=f"&gt; {alert}")
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    assert run("job", "ask", "j1", str(block))[0] == 0
    assert [(word["id"], word["said"]) for word in jev.asked[0]["state"]["his_words"] if "ruling" not in word["id"]] == [("p1794200000000100", his)]
def test_job_ask_keeps_a_card_whose_option_his_words_rule_out_and_prints_jevs_finding_to_the_agent(
    run: Run, demo: str, slack_thread: Thread, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS | {"team_url": "https://acme.slack.com/"})
    view.refresh_cards("demo")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "design the run page", "--thread", "1794200000.000100")
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    words = "never write a second object per journal file, the bucket is billed per object"
    his = slack_thread.post(state.now(), from_user=True, text=words)
    key = re.search(r"/(p\d+)\?", his)[1]
    jev.breaks("breaks2", key)
    jev.breaks("untapped", key)
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    assert cli.main(["job", "ask", "j1", str(block)]) == 0
    advice = capsys.readouterr().err
    broken = f'as breaking {key}, his message in the order\'s thread, "{words}". This finding refuses no card, by his pick 320b7a7'
    assert f'drive job ask: Jev reads option "A map file beside each journal file: no guessing, but one request per file and twice the objects" {broken}' in advice
    assert f"drive job ask: Jev reads the card {broken}" in advice
    assert "--unsettled" not in advice
    [question] = state.load("demo")["jobs"]["j1"]["questions"]
    assert (question["refused"], question["waits"] is not None, question["unsettled"]) == (None, True, None)


def test_job_ask_keeps_a_card_whose_option_jev_reads_as_never_worked_out_and_prints_jevs_finding_naming_that_option(
    run: Run, demo: str, slack_thread: Thread, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS | {"team_url": "https://acme.slack.com/"})
    view.refresh_cards("demo")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "design the run page", "--thread", "1794200000.000100")
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    unworked = "Map at the start of each file: changes the shared file format and still needs a length read first"
    jev.breaks("worked3", limits.WORKED_WORDS["id"])
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    assert cli.main(["job", "ask", "j1", str(block)]) == 0
    advice = capsys.readouterr().err
    assert f'drive job ask: Jev reads option "{unworked}" as breaking ruling:77b577b, {limits.WORKED_WORDS["asked"]}, "dontg be lazy flash this out' in advice
    assert [line.partition(" as breaking ")[0] for line in advice.splitlines() if line.startswith("drive job ask: Jev reads")] == [f'drive job ask: Jev reads option "{unworked}"']
    [request] = jev.asked
    assert f'The option: "{unworked}".' in request["questions"]["worked3"]["label"]["instructions"]
    assert limits.WORKED_WORDS in request["state"]["his_words"]
    assert state.load("demo")["jobs"]["j1"]["questions"][-1]["refused"] is None


def test_job_ask_hands_jev_the_words_of_his_an_order_the_drive_wrote_quotes(
    run: Run, meta: str, slack_thread: Thread, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, jev: Jev
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS | {"team_url": "https://acme.slack.com/"})
    view.refresh_cards("demo")
    run("order", "add", "--title", "Short title", 'Drive tooling: the finished snapshot page waited 10 minutes for its card, and he ruled "keep each finished snapshot 30 days"')
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    assert run("job", "ask", "j1", str(block))[0] == 0
    assert [(word["id"], word["said"]) for word in jev.asked[0]["state"]["his_words"] if word["id"] == "o1"] == [("o1", '"keep each finished snapshot 30 days"')]


WAIT_RULING = {
    "id": "9992100",
    "asked": "When should the js_func helper process and its memory cap ship?",
    "said": "Wait for the four queue PRs to merge, then build the separate process and the cap on top. No clashes, and nothing ships until then",
}


def test_job_ask_reads_his_recorded_answers_ccn_ranks_nearest_and_a_card_asking_him_to_lift_one_holds_its_incident_pr_until_he_answers(
    run: Run, demo: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], jev: Jev, recorded: Answers
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "chat turns run out of memory on plat", "--incident", "--parts", "fix", "first check")
    run("job", "add", "builder", "--order", "o1", "--part", "fix", "--goal", "move js_func into its own helper process")
    recorded.matched = [WAIT_RULING]
    jev.breaks("breaks1", WAIT_RULING["id"])
    asked = SHORT_ASK.replace("Holds: none: no option changes code any job builds", "Holds: fix") + "Interim fix: j1 restarts the pods that ran out of memory\n"
    block = tmp_path / "ask.md"
    block.write_text(asked)
    assert cli.main(["job", "ask", "j3", str(block)]) == 0
    advice = capsys.readouterr().err
    assert f'as breaking 9992100, {WAIT_RULING["asked"]}, "{WAIT_RULING["said"]}". This finding refuses no card' in advice
    assert f'drive job ask: kept the card unsettled as "9992100: {settled.MARKED}", asking him to lift 9992100, {WAIT_RULING["asked"]}, so it holds its PR' in advice
    assert WAIT_RULING in jev.asked[-1]["state"]["his_words"]
    question = state.load("demo")["jobs"]["j3"]["questions"][-1]
    assert (question["unsettled"], land.ship_held(state.load("demo"), "j3", {})) == (f"9992100: {settled.MARKED}", [question["card"]])
    unsettled = "9992100: this card asks whether the incident overrides his wait"
    assert run("job", "ask", "j3", str(block), "--unsettled", unsettled)[0] == 0
    question = state.load("demo")["jobs"]["j3"]["questions"][-1]
    assert (question["unsettled"], land.ship_held(state.load("demo"), "j3", {})) == (unsettled, [question["card"]])
    url = "https://github.com/Forge-AI/monorepo/pull/43"
    assert run("pr", "add", url, "--job", "j3", *LEAD)[0] == 0
    unlinked = asked.replace("Meanwhile nothing is built on either layout.", "Meanwhile this job builds it on a branch, no PR.")
    block.write_text(unlinked)
    assert cli.main(["job", "ask", "j3", str(block)]) == 2
    assert f"\n- j3 has {url} open, which its ASK's summary does not link" in capsys.readouterr().err
    block.write_text(unlinked.replace("on a branch, no PR.", f"in <{url}|the helper process PR>, held until you answer."))
    assert run("job", "ask", "j3", str(block))[0] == 0
    data = state.load("demo")
    assert land.pr_cards(data, data["prs"]["Forge-AI/monorepo#43"], {}) == [data["jobs"]["j3"]["questions"][-1]["card"]]


NIGHTLY_EXPORT = {
    "id": "7a1c0de" + "0" * 33,
    "title": "Which identity runs each team's nightly export?",
    "body": "A scheduled job in each team's cluster runs the export as that team's service account; each team is one reviewed config line",
    "tags": ["scope:durable"],
    "created_at": "2026-11-11T18:07:00Z",
}
DEPARTS = "7a1c0de: option 1 replaces the per-team line with discovery"


def test_job_ask_keeps_unsettled_a_card_that_says_its_recommended_option_replaces_words_of_an_answer_it_cites_so_its_incident_pr_waits_on_his_answer(
    run: Run, demo: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], jev: Jev, recorded: Answers
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "nightly exports stall", "--incident", "--parts", "fix", "first check")
    run("job", "add", "builder", "--order", "o1", "--part", "fix", "--goal", "run each team's export")
    recorded.shown = {NIGHTLY_EXPORT["id"]: NIGHTLY_EXPORT}
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK.replace("Holds: none: no option changes code any job builds", f"Holds: fix\n{DEPARTS}") + "Interim fix: j1 restarts the stalled exports\n")
    assert cli.main(["job", "ask", "j3", str(block)]) == 0
    assert "kept the card unsettled" not in capsys.readouterr().err
    request = jev.asked[-1]
    cited = {"id": "7a1c0de", "asked": NIGHTLY_EXPORT["title"], "said": NIGHTLY_EXPORT["body"]}
    assert (request["questions"]["says"]["label"]["options"], cited in request["state"]["his_words"]) == ({settled.UNDECIDED: settled.UNSAID, "7a1c0de": None}, True)
    assert DEPARTS in request["state"]["card"].splitlines()
    assert land.ship_held(state.load("demo"), "j3", {}) == []
    jev.breaks("says", "7a1c0de")
    assert cli.main(["job", "ask", "j3", str(block)]) == 0
    assert f'drive job ask: kept the card unsettled as "7a1c0de: {settled.MARKED}", asking him to lift 7a1c0de, {NIGHTLY_EXPORT["title"]}, so it holds its PR from landing on an incident too' in capsys.readouterr().err
    question = state.load("demo")["jobs"]["j3"]["questions"][-1]
    assert (question["unsettled"], land.ship_held(state.load("demo"), "j3", {})) == (f"7a1c0de: {settled.MARKED}", [question["card"]])


def test_job_ask_never_asks_him_to_lift_a_cited_answer_whose_order_the_recommended_option_starts_by_the_release_gates_own_reading(
    run: Run, demo: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], jev: Jev, recorded: Answers
) -> None:
    answered_cut(run, demo, [CUT_PICK, CUT_TAP])
    recorded.shown = {answer["id"][:7]: answer for answer in (CUT_PICK, CUT_TAP)}
    builder = next(job_id for job_id, job in state.load(demo)["jobs"].items() if job["kind"] == "builder")
    release = ACME_CUT.removesuffix(" --order {}")
    described = "  When a run finishes, the uploader in `journal/snapshot.go` writes one snapshot.ltx to the team's bucket with its page map at a fixed place, and the run page's reader in `site/journal.js` reads that map, then 2 page ranges; a backfill writes one for each old run."
    holds = "Holds: none: no option changes code any job builds"
    asked = SHORT_ASK.replace(described, f"  Starts `{release}`, which brings Acme's cluster to the api hotfix the others run.")
    block = tmp_path / "ask.md"
    block.write_text(asked.replace(holds, f"{holds}\n5d17d9c: option 1 carries out his pick"))
    jev.breaks("says", "5d17d9c")
    assert cli.main(["job", "ask", builder, str(block)]) == 0
    advice = capsys.readouterr().err
    assert (settled.ORDERED.format(id="5d17d9c", asked=CUT_PICK["title"], release=release) in advice, "kept the card unsettled" in advice) == (True, False)
    assert state.load(demo)["jobs"][builder]["questions"][-1]["unsettled"] is None
    block.write_text(asked.replace(holds, f"{holds}\n7ab1e20: option 1 leaves out the full release"))
    jev.breaks("says", "7ab1e20")
    assert cli.main(["job", "ask", builder, str(block)]) == 0
    advice = capsys.readouterr().err
    assert ("orders the recommended option's" in advice, f'kept the card unsettled as "7ab1e20: {settled.MARKED}"' in advice) == (False, True)


def test_his_recorded_answers_reach_jev_as_ccn_ranks_them_nearest_to_the_whole_block_his_words_alone_never_a_tap_approving_a_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str, str]] = []
    answers = [
        {"id": "a1" * 20, "title": "Tap Approve on Forge-AI/monorepo#7?", "body": "Approve", "tags": ["scope:durable", "kind:approval"]},
        {"id": f"{WAIT_RULING['id']}3{'0' * 32}", "title": WAIT_RULING["asked"], "body": f"{WAIT_RULING['said']}\nNotes: kept from his DM", "tags": ["scope:durable"]},
        *({"id": f"{number:07d}{'0' * 33}", "title": f"Question {number}?", "body": "Yes", "tags": ["scope:durable"]} for number in range(10)),
    ]
    monkeypatch.setattr(settled, "ccn", lambda argv, text, cwd, failure: calls.append((argv, text, cwd)) or json.dumps(answers))
    block = "Ship <https://github.com/Forge-AI/monorepo/pull/7|the helper process PR> before the four queue PRs?"
    found = SETTLED_MATCHED(block, "/work")
    assert calls == [(["ccn", "answer", "match", "-", "--label", "scope:durable", "--nearest", "--limit", "-1", "--json"], settled.read(block), "/work")]
    assert (found[0], len(found)) == (WAIT_RULING, settled.CANDIDATES)


def test_his_thread_words_reach_only_the_order_they_are_about_never_another_order_sharing_its_thread(run: Run, demo: str, slack_thread: Thread) -> None:
    words = "ok so for 1, lrts mske eveyhting standard on signal or 30 seconds"
    slack_thread.post(state.now() - timedelta(minutes=1), from_user=True, text=words)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "any harm in making it uniform?", "--thread", "1794200000.000100")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "an answer he reads states only what was read in code or measured", "--thread", "1794200000.000100")
    with state.edit("demo") as data:
        data["orders"]["o1"]["created"] = state.stamp(state.now() - timedelta(minutes=2))
    data = state.load("demo")
    assert [(word["said"], word["id"]) for word in settled.order_words(data, "o1")] == [(words, f"p{slack_thread.messages[0]['ts'].replace('.', '')}")]
    assert settled.order_words(data, "o2") == []


def test_job_ask_settles_a_question_only_on_his_answer_to_that_same_question_so_its_job_builds_on_his_words_with_no_card(
    run: Run, asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, jev: Jev, settled_notes: list[tuple[str, str]], cards_drawn: list[dict]
) -> None:
    card = "https://acme.slack.com/archives/D123/p1794300000000100"
    title = SHORT_ASK.splitlines()[0].removeprefix("ASK ")
    words = "One snapshot file per finished run"
    run("job", "add", "reader", "--order", "o1", "--goal", "lay out the files")
    with state.edit("demo") as data:
        data["jobs"]["j2"]["questions"] = [
            state.new_question(None, f"  {title.upper()}", state.stamp(), card=card, closed=state.new_close("answered", words, card)) | {"waits": "demo:j2-1"},
            state.new_question(None, f"{title.removesuffix('?')}, and kept how long?", state.stamp(), card=card, closed=state.new_close("answered", "Thirty days", card)),
        ]
    matched: list[list[str]] = []
    monkeypatch.setattr(settled, "ccn", lambda argv, text, cwd, failure: matched.append(argv) or "[]")
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    kept = state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(SHORT_ASK)}.md"
    assert run("job", "ask", asking, str(block)) == (
        0,
        f"{kept} keeps j1's question settled, no card: he already answered this same question, asked in the same words, at {card}:"
        f' "{words}". Go ahead on it; j1 builds it now',
    )
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], [(question["file"], question["card"], question["closed"]["words"], question["closed"]["link"]) for question in job["questions"]]) == (
        "running",
        [(str(kept), None, words, card)],
    )
    assert settled_notes == [("j1", card)]
    assert (matched, cards_drawn) == ([], [])
    assert not [request for request in jev.asked if "decides" in request["questions"]]
    block.write_text(SHORT_ASK.replace("ASK How should", "ASK When should"))
    assert run("job", "ask", asking, str(block))[0] == 0
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["questions"][-1]["closed"], job["questions"][-1]["waits"], len(cards_drawn)) == ("running", None, "demo:j1-7", 1)


def test_a_settled_question_tells_the_session_holding_its_order_in_one_record_that_wakes_nobody(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: sent.append((lane, records)) or ["9"])
    card = "https://acme.slack.com/archives/D123/p1794300000000100"
    title = "How should a finished run's files be laid out?"
    answered = state.new_question(None, title, state.stamp(), card=card, closed=state.new_close("answered", "One snapshot file", card))
    SETTLED_NOTE("demo", state.load("demo"), asking, title, answered)
    assert sent == [
        (
            "j1",
            [
                {
                    "kind": "evidence",
                    "to": ["root@demo"],
                    "attr": {"job": "j1", "answered": card},
                    "text": f'j1 builds on his answer at {card} to the same question, "{title}", asked in the same words, so no card draws for it',
                }
            ],
        )
    ]


SKIPPED = {
    "j290": (
        "Should a person's memory request be refused when it claims a chat someone else started?",
        "Sign sand in on your Mac so transcript.sh can read a teammate's sand-next chat live, or close without it?",
        "Close without a live read: the order closes on the two merged fixes; a teammate's sand-next transcript stays unproven until someone signs in.",
    ),
    "j2087": (
        "Captain-hook update, your Mac: install 12.121.71 now? It restarts the hook helper once",
        "May the drive end a stuck drive process that jams every command, without asking him first?",
        "next time you dont have to ask me just do it. also hello sttus youre just sitgting there",
    ),
    "j2184": (
        "When may the drive skip your card because an earlier answer or another card already covers that question?",
        "Should the say hook's judge rule on a check's finding by that finding's own rule, or by whether your words cover the post?",
        "1 and then if we kepe getting false rejects move to 3",
    ),
    "j2448": (
        "Where does a promise in a post to you live: as a part of its order the watch chases, or as words the say hook checks?",
        "Should the say hook's judge rule on a check's finding by that finding's own rule, or by whether your words cover the post?",
        "1 and then if we kepe getting false rejects move to 3",
    ),
    "j2192": (
        "Should Iris stop saving a model's call to a tool it doesn't have into the chat, so one bad call can't break every later message?",
        "Oh I meant rewrites like dreaming new synthesized ones, making them more token efficient, not rewriting bad ones bad ones should jsut be deleted",
        "Oh I meant rewrites like dreaming new synthesized ones, making them more token efficient, not rewriting bad ones bad ones should jsut be deleted",
    ),
    "j2155": (
        "When should cluster SandSQL primaries start sending ssql-blobstore their file ref counts, now this release starts journal ref counting?",
        "One SandSQL primary serves every team in a cluster, so one crash stops them all. Split primaries now, and along what line?",
        None,
    ),
}


@pytest.mark.parametrize("skipped", SKIPPED)
def test_each_question_a_card_on_another_question_once_skipped_now_draws_his_card(skipped: str, run: Run, asking: str) -> None:
    asked, other, words = SKIPPED[skipped]
    card = "https://acme.slack.com/archives/D123/p1794300000000100"
    run("job", "add", "reader", "--order", "o1", "--goal", "ask him")
    with state.edit("demo") as data:
        closed = words and state.new_close("answered", words, card)
        data["jobs"]["j1"]["questions"] = [state.new_question(None, other, state.stamp(), card=card, closed=closed) | {"waits": "demo:j1-1"}]
    data = state.load("demo")
    assert (settled.answered(data, asked), cli.elsewhere(data, "j2", asked, None)) == (None, None)
    if words:
        assert settled.answered(data, other) == data["jobs"]["j1"]["questions"][0]
    else:
        assert cli.elsewhere(data, "j2", other, None) == ("j1", data["jobs"]["j1"]["questions"][0])


ADVISORY_ASK = """ASK May Jev's reading of your standing words refuse or skip a question card, or only advise as your advisory-judge pick says?
Every option drops the hard-coded check that refused a card recommending your recorded pick, and stops reading lines you quoted from an alert as your words.
They differ on what Jev's reading of your standing words does: your advisory-judge pick says it only advises, your later pick on <https://github.com/yasyf/cc-skills/pull/4673|the fix that settles only on your answer to that same question> says only that answer skips a card, and <https://github.com/yasyf/cc-skills/pull/4664|the one-judge fix> refuses and skips on it, so its rebase cannot keep all three.
Recommended: Jev refuses a card, never skips it. The one-judge fix stays unmerged until you pick; nobody is hurt meanwhile.
- Jev refuses a card, never skips it (recommended)
  Rebased on main, drive job ask keeps settled.answered as the only skip. One Jev call reads every option against your recorded answers, the order and its thread with quoted lines dropped, and refuses a card offering an option they rule out, an amount with no tap, or a broken ruling. The advisory path in drive_checks.asked is deleted.
- Jev only advises; fix the misreads
  The one-judge fix shrinks to two fixes on main: limits.his_words and settled.thread_words drop lines you quoted with "> ", and limits.root_cause_problems stops refusing a card that recommends your pick. The ask hook keeps printing Jev's finding without refusing, so a card offering an option your standing rule rules out still reaches you.
- Merge the one-judge fix as built
  When Jev is 0.8 sure your recorded answers, order or thread choose one option, drive job ask refuses the card and the builder builds that option, so no card draws; it also refuses on the same findings as the recommended option. It puts back settled.candidates and the may_settle cache your same-question pick deleted.
Findings:
Root cause: nothing that reads a question card against his standing words can stop it: the ask hook's Jev reads only the recommended option and only advises (capt-hook/hooks/drive_checks.py:469 asked returns evt.context in every branch), and limits.root_cause_problems (lib/drive/limits.py:849) hard-codes that the First principles option be recommended over his recorded pick; the per-answer reading that once skipped cards was deleted by yasyf/cc-skills#4673.
First principles: Jev refuses a card, never skips it
Interim fix: none: nobody is hurt now: main's yasyf/cc-skills#4673 already stops skips on answers to other questions, and the agent::start card was answered (cc-notes answer 182b145).
Holds: Drive tooling: a question card drew asking him where a chat's started agent runs
His recorded words, read whole from ccn answer search on the coordinator's Mac: 320b7a7 (2026-11-12, drive:durable-agents, scope:durable) "Advisory judge: drive job ask keeps every well-shaped card and prints Jev's finding to the agent; no card is refused on merits". The pick yasyf/cc-skills#4673 carried out, quoted in its body and in reference/scope.md on main: "Settle only on your answer to that same question, copies only in the same words: no wrong skips; each skip below becomes a card." 86d3c49 (hooks batch): "Some of these are being overzealous and need to be fixed or removed They should all use jev for judgement rather than hard coding thighs".
Never asked: j2377's DESIGN card asking whether 320b7a7 stands was kept twice (asks/j2377-2a5231f8.md, asks/j2377-8290b522.md) and never drawn; j2419 built yasyf/cc-skills#4664 on 86d3c49 alone and named the 320b7a7 conflict only on its UNPROVEN line.
Timeline: yasyf/cc-skills#4664 opened 2026-11-13T01:15:47Z; yasyf/cc-skills#4673 opened 01:50:25Z and merged 01:53:10Z, deleting settled.candidates, decided, reading, refusals, matched and job may_settle; keep_ask on main reads `if not (answer := settled.answered(data, asked.title))`.
Rebase: `git rebase origin/main` of 75126bcf2 conflicts in 14 files (settled.py 4 hunks, cli.py 3, test_cli.py 9, conftest.py 3, state.py, watch.py, drive_checks.py, builder.md, designer.md, coordinator.md, incidents.md, scope.md, verbs.md, test_state.py); every hunk is main's "no answer he gave another question settles it, and no ccn match or Jev reading is asked" against the one-judge fix's "one whose option his words already choose ... is kept settled by those words, draws no card".
Still on main: the advisory hook (capt-hook/hooks/drive_checks.py:227 ASK_JUDGE, :469 asked); the hard-coded check (lib/drive/limits.py:849 "removes the root cause and is not the recommended option: recommend it"); limits.his_words (limits.py:1414-1418) returns a Slack-added order's whole text and settled.thread_words returns each message's whole text, "> " lines included, which refused j2367-2e39047a.md on "15 minute" and "64 row" from the monitor alert he quoted.
Agent::start case: j2377 measured per option p(his words rule it out) one-api-start 0.47, Mac-builds 0.65, pod 0.43 under the old prompt, so under the recommended option that card may still draw, as his same-question pick accepts; the one-judge fix's single call settled it at 0.97-0.98 (j2419's Sprite reconstruction).
Next question: once he picks, j2643 rebuilds yasyf/cc-skills#4664 on main to that option, pushes, and the lander merges it.
"""
TAPPED = "https://acme.slack.com/archives/D123/p1794300000000100"


SNAPSHOT_PICK = "One snapshot file per finished run\nOptions: One snapshot file per finished run | A map file beside each journal file"


def test_already_answered_carries_only_the_answer_its_summary_cites_his_words_above_its_options_line_never_one_its_brief_binds_or_its_findings_cite(
    run: Run, asking: str, recorded: Answers, jev: Jev, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded.shown["631afc4"] = {"id": "631afc4", "title": "Where does a run's page map live?", "body": "At the end of each file\nNotes: cci owner 2", "tags": []}
    recorded.shown["44de4c2"] = {"id": "44de4c2", "title": "Keep old runs?", "body": "Keep them\nNotes: cci owner 3", "tags": []}
    with state.edit("demo") as data:
        data["jobs"]["j1"]["brief_flags"] = {"ruling": ["631afc4"]}
    text = SHORT_ASK.replace(
        "Meanwhile nothing is built on either layout.",
        "Your {answer:c952dbb|Nov 5 answer} asked for snapshots before old runs needed one. Meanwhile nothing is built on either layout.",
    ) + "(3) His 44de4c2 kept old runs.\n"
    drawn, job, posted = tapped_already_answered(run, recorded, monkeypatch, text, "c952dbb", "How are a finished run's files kept?", SNAPSHOT_PICK)
    assert (drawn["options"][-1], drawn["hints"][limits.ALREADY_ANSWERED]) == (limits.ALREADY_ANSWERED, '"One snapshot file per finished run"')
    assert [record["text"] for record in posted] == ["One snapshot file per finished run"]
    assert ((closed := job["questions"][-1]["closed"])["how"], closed["words"]) == ("answered", "One snapshot file per finished run")
    assert jev.asked == []


def test_a_card_citing_a_pick_a_lane_made_provisionally_draws_no_already_answered_since_he_never_said_it(run: Run, asking: str, recorded: Answers) -> None:
    text = SHORT_ASK.replace(
        "Meanwhile nothing is built on either layout.",
        "Your {answer:c952dbb|Oct 19 answer} asked for snapshots before old runs needed one. Meanwhile nothing is built on either layout.",
    )
    kept = state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(text)}.md"
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(text)
    recorded.shown["c952dbb"] = {
        "id": "c952dbb",
        "title": "How are a finished run's files kept?",
        "body": f"PROVISIONAL by the design lane's recommendation (32c72e4)\n{SNAPSHOT_PICK}\nNotes: cci owner 1",
        "tags": ["provisional"],
        "created_at": "2026-10-19T11:33:00Z",
    }
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"] = [state.new_question(str(kept), text.splitlines()[0].removeprefix("ASK "), state.stamp()) | {"waits": "demo:j1-7"}]
    code, printed = run("job", "card", "j1")
    assert code == 0
    assert limits.ALREADY_ANSWERED not in json.loads(printed)["options"]


def test_job_ask_refuses_a_card_whose_sentence_recommending_an_option_cites_his_recorded_answer_naming_the_option_to_build_unless_unsettled_names_it(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], recorded: Answers
) -> None:
    recorded.shown["c952dbb"] = {"id": "c952dbb", "title": "How are a finished run's files kept?", "body": f"{SNAPSHOT_PICK}\nNotes: cci owner 1", "tags": [], "created_at": "2026-11-05T11:33:00Z"}
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK.replace("so any finished run opens in 3 requests.", "as {answer:c952dbb|your Nov 5 pick} chose."))
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert (
        '\n- its summary recommends "One snapshot file per finished run, written when it ends, old runs backfilled: opens in 3 requests" by his recorded'
        ' answer c952dbb, "One snapshot file per finished run", which already chose it, so no card asks him again: build that option with no ASK'
    ) in capsys.readouterr().err
    assert state.load("demo")["jobs"][asking]["questions"] == []
    assert run("job", "ask", asking, str(block), "--unsettled", "c952dbb: this card asks him to lift his Nov 5 pick")[0] == 0


def test_a_card_whose_summary_cites_320b7a7_draws_already_answered_with_his_words_and_the_link_drive_answer_kept_and_his_tap_closes_it_on_them(
    run: Run, asking: str, recorded: Answers, jev: Jev, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794478678050659"
    title = "Should Jev's refusal of a question card stand only when code finds the words it quotes in that card's recommended option?"
    words = "Advisory judge: drive job ask keeps every well-shaped card and prints Jev's finding to the agent; no card is refused on merits"
    with state.edit("demo") as data:
        data["answers"]["320b7a7"] = {"words": words, "link": link, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {}}
    cited = ADVISORY_ASK.replace("your advisory-judge pick says", "your {answer:320b7a7|advisory-judge pick} says")
    drawn, job, posted = tapped_already_answered(run, recorded, monkeypatch, cited, "320b7a7", title, words)
    assert (drawn["options"][-1], drawn["hints"][limits.ALREADY_ANSWERED], len(drawn["options"])) == (limits.ALREADY_ANSWERED, f'"{words}" (<{link}|your answer>)', 4)
    assert [record["text"] for record in posted] == [words]
    assert ((closed := job["questions"][-1]["closed"])["how"], closed["words"], [f'verbatim: "{words}"' in answer["words"] for answer in job["undelivered"]]) == ("answered", words, [True])
    assert jev.asked == []


def tapped_already_answered(run: Run, recorded: Answers, monkeypatch: pytest.MonkeyPatch, text: str, answer_id: str, title: str, words: str) -> tuple[dict, dict, list[dict]]:
    """j1's card drawn from this ASK, kept as the drive kept it and asking him under demo:j1-7, with his answer recorded under its id, and his tap on its Already answered option: the card, j1 after the tap, and the records the tap posted."""
    kept = state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(text)}.md"
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(text)
    recorded.shown[answer_id] = {"id": answer_id, "title": title, "body": f"{words}\nNotes: cci owner 1", "tags": [], "created_at": "2026-11-05T11:33:00Z"}
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"] = [state.new_question(str(kept), text.splitlines()[0].removeprefix("ASK "), state.stamp()) | {"waits": "demo:j1-7"}]
    code, printed = run("job", "card", "j1")
    assert code == 0
    posted: list[dict] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, posts: posted.extend(posts) or ["9"])
    card.question_answered({"waits": "demo:j1-7", "label": limits.ALREADY_ANSWERED, "url": TAPPED})
    return json.loads(printed), state.load("demo")["jobs"]["j1"], posted


def test_job_ask_asked_again_replaces_its_question_once_kept_settled_with_no_card_so_his_card_draws(run: Run, asking: str, tmp_path: Path) -> None:
    title = SHORT_ASK.splitlines()[0].removeprefix("ASK ")
    kept = state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(SHORT_ASK)}.md"
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"] = [state.new_question(str(kept), title, state.stamp(), closed=state.new_close("answered", None, None, answer="ac61348", ruling="ac61348"))]
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    assert run("job", "ask", asking, str(block)) == (0, f"{kept}\nhis card {drawn_card(1)} asks it under demo:j1-7")
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], [(question["file"], question["closed"]) for question in job["questions"]]) == ("running", [(str(kept), None)])


def test_job_ask_reads_a_remote_lanes_report_when_given_no_file(run: Run, asking: str, state_root: Path) -> None:
    assert run("job", "ask", asking)[0] == 2
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(where="remote", report=SHORT_ASK)
    assert run("job", "ask", asking)[1].splitlines()[0] == str(state_root / "demo" / "asks" / f"j1-{hashlib.sha256(SHORT_ASK.encode()).hexdigest()[:8]}.md")


def test_job_report_keeps_a_jobs_whole_return_from_stdin_and_prints_it_back(
    run: Run, demo: str, state_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    kept_proof(tmp_path, "j1")
    run("pr", "add", "https://github.com/yasyf/cc-skills/pull/1", "--job", "j1", *LEAD)
    assert run("job", "report", "j1")[0] == 2
    monkeypatch.setattr("sys.stdin", io.StringIO("READY https://github.com/yasyf/cc-skills/pull/1\nSERVES: his order\nDESIGN: none\n"))
    code, kept = run("job", "report", "j1", "-")
    assert (code, kept.splitlines()[0]) == (0, str(state_root / "demo" / "jobs" / "j1.replies.jsonl"))
    assert run("job", "report", "j1") == (0, "READY https://github.com/yasyf/cc-skills/pull/1\nSERVES: his order\nDESIGN: none")


def test_job_report_prints_every_return_a_job_took_newest_first_whoever_sent_it_and_writes_no_report_file(
    run: Run, demo: str, state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "reader", "--order", "o1", "--goal", "grade", "--agent", "a")
    assert run("job", "replies", "j1") == (0, "j1 has sent nothing yet")
    blocked = "BLOCKED the diffs are not fetched\n"
    agents = "DONE graded three PRs\nCHECK: the three diffs\n"
    restated = "DONE graded three PRs: two ports\nUNPROVEN: none\n"
    for whole in (blocked, agents, restated):
        monkeypatch.setattr("sys.stdin", io.StringIO(whole))
        assert run("job", "report", "j1", "-") == (0, str(state_root / "demo" / "jobs" / "j1.replies.jsonl"))
    kept = json.loads(run("job", "replies", "j1", "--json")[1])
    assert [(reply["type"], reply["body"]) for reply in kept] == [("worker_done", blocked), ("worker_done", agents), ("worker_done", restated)]
    earlier = [state.EARLIER_RETURN.format(at=state.stamp(state.parse_time(reply["created_at"]).astimezone())) for reply in kept]
    assert run("job", "report", "j1", "--all") == (0, f"{restated}\n{earlier[1]}\n{agents}\n{earlier[0]}\n{blocked}".rstrip("\n"))
    at = [state.stamp(state.parse_time(reply["created_at"]).astimezone()) for reply in kept]
    lines = [state.EARLIER_LINE.format(at=at[1], line="DONE graded three PRs"), state.EARLIER_LINE.format(at=at[0], line="BLOCKED the diffs are not fetched")]
    assert run("job", "report", "j1") == (0, "\n".join([restated.rstrip("\n"), *lines]))
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"], list(state_root.rglob("j1.md"))) == ("done", "DONE graded three PRs: two ports", [])


def test_a_return_kept_twice_prints_once_where_it_was_kept_last(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "reader", "--order", "o1", "--goal", "grade")
    graded, listed = "DONE graded three PRs\n", "DONE listed three PRs\n"
    for whole in (graded, listed, graded):
        monkeypatch.setattr("sys.stdin", io.StringIO(whole))
        run("job", "report", "j1", "-")
    assert [reply["body"] for reply in state.returns("demo", "j1")] == [graded, listed]
    assert run("job", "report", "j1")[1].count("DONE graded three PRs") == 1


def test_a_report_an_older_install_kept_beside_a_job_moves_into_its_history_once_and_its_file_goes(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "reader", "--order", "o1", "--goal", "grade")
    run("job", "add", "reader", "--order", "o1", "--goal", "grade again", "--remote")
    monkeypatch.setattr("sys.stdin", io.StringIO("DONE graded three PRs\n"))
    run("job", "report", "j1", "-")
    with state.edit("demo") as data:
        data["jobs"]["j2"]["report"] = "DONE the lane's latest\n"
    folder = state.drive_dir("demo") / "reports"
    folder.mkdir(exist_ok=True)
    older = datetime(2026, 11, 1, 9, tzinfo=UTC)
    for job_id, text in (("j1", "DONE graded three PRs\n"), ("j2", "DONE graded two PRs\nCHECK: the diffs\n"), ("j9", "DONE a job the drive no longer has\n")):
        (folder / f"{job_id}.md").write_text(text)
        os.utime(folder / f"{job_id}.md", (older.timestamp(), older.timestamp()))
    (folder / "j2.proof.txt").write_text("proof output\n")
    for _ in range(2):
        monkeypatch.setattr(state, "MOVED_REPORTS", set())
        with state.edit("demo"):
            pass
    assert (sorted(path.name for path in folder.iterdir()), [reply["body"] for reply in state.returns("demo", "j1")]) == (["j2.proof.txt"], ["DONE graded three PRs\n"])
    assert [reply["body"] for reply in state.returns("demo", "j2")] == ["DONE the lane's latest\n", "DONE graded two PRs\nCHECK: the diffs\n"]
    earlier = state.EARLIER_RETURN.format(at=state.stamp(older.astimezone()))
    assert run("job", "report", "j2", "--all") == (0, f"DONE the lane's latest\n\n{earlier}\nDONE graded two PRs\nCHECK: the diffs")


def test_a_builders_done_names_its_proof_and_what_he_sees_is_proven_on_that_surface(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    fetched = tmp_path / "view.json"
    fetched.write_text("{}")
    recording = tmp_path / "queue-click.mov"
    recording.write_bytes(b"\0")
    assert keep(monkeypatch, capsys, "j1", "DONE the queue shows one card\nCHECK: it looked right\n")[0] == 2
    empty = tmp_path / "j1.proof.txt"
    empty.write_text("")
    for unheld in ("/gone/view.json", empty):
        assert keep(monkeypatch, capsys, "j1", f"DONE the queue shows one card\nCHECK: {unheld}\n")[1].startswith(
            "drive: j1: refused, nothing kept: a DONE names its proof on a CHECK: line"
        )
    seen = "DONE the queue shows one card\nNATIVE: the dashboard's queue view\nCHECK: {}\n"
    code, refused = keep(monkeypatch, capsys, "j1", seen.format(fetched))
    assert code == 2
    assert "this builds something he sees, by its NATIVE: line or its brief's Native, so its proof is from that surface" in refused
    assert keep(monkeypatch, capsys, "j1", seen.format(recording))[0] == 0
    picture = tmp_path / "queue.png"
    picture.write_bytes(b"\0")
    assert keep(monkeypatch, capsys, "j1", seen.format(f"{picture}: the queue with one card"))[0] == 0
    assert keep(monkeypatch, capsys, "j1", seen.format("https://poetic-technology.slack.com/archives/D1/p1794300000000100"))[0] == 0
    unseen = "DONE the watch stops on a dead lane\nNATIVE: none: he never sees it\nCHECK: {}\n"
    assert keep(monkeypatch, capsys, "j1", unseen.format(fetched))[0] == 0
    assert state.load("demo")["jobs"]["j1"]["state"] == "done"


def test_job_report_refuses_an_empty_stdin(run: Run, demo: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert cli.main(["job", "report", "j1", "-"]) == 2
    assert "stdin held no report for j1" in capsys.readouterr().err
    assert run("job", "report", "j1")[0] == 2


def test_job_report_prints_the_one_line_result_of_a_job_that_kept_no_report_and_refuses_one_that_returned_nothing(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    assert cli.main(["job", "report", "j1"]) == 2
    assert "j1 kept no report and has returned nothing yet" in capsys.readouterr().err
    run("job", "set", "j1", "--said", "DONE installed 1.0.76")
    assert run("job", "report", "j1") == (0, "DONE installed 1.0.76\n(j1 kept no report; this one-line result is all it returned)")
    assert json.loads(run("job", "report", "j1", "--json")[1]) == {"id": "j1", "report": None, "said": "DONE installed 1.0.76"}


def test_a_builders_done_on_a_surface_its_brief_names_pictures_each_user_story_against_the_criteria_its_proof_file_kept(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "show the queue")
    run("job", "add", "builder", "--order", "o1", "--goal", "draw the queue")
    told = "press j and the next card opens with its question first; hover a card and its PR's title shows"
    run("job", "brief", "j1", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main", "--native", "the dashboard's queue", "--interactions", told)
    pictures = [tmp_path / name for name in ("press-j.png", "hover-a-card.png")]
    for picture in pictures:
        picture.write_bytes(b"\0")
    one, both = (f"DONE the queue opens each card\nCHECK: {' '.join(map(str, shown))}\n" for shown in (pictures[:1], pictures))
    code, refused = keep(monkeypatch, capsys, "j1", one)
    assert code == 2
    assert "its brief names 2 user stories and its proof shows 1 of them on the installed surface" in refused
    code, refused = keep(monkeypatch, capsys, "j1", both)
    assert code == 2
    assert "j1's brief names user stories and it keeps no proof file holding their acceptance criteria" in refused
    kept_proof(tmp_path, "j1")
    jev.picks("bare")
    code, refused = keep(monkeypatch, capsys, "j1", both)
    assert code == 2
    assert 'its proof stops at the bare minimum for the story "press j and the next card opens with its question first"' in refused
    assert [asked["state"]["proof_file"] for asked in jev.asked if "proof1" in asked["questions"]] == ["#!/bin/sh\nexit 0\n"]
    jev.picks("none")
    assert keep(monkeypatch, capsys, "j1", both)[0] == 0


def test_a_builders_done_on_his_slack_dm_is_proven_by_the_live_posts_link_its_proof_file_reads_back(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "draw the table post")
    run("job", "add", "builder", "--order", "o1", "--goal", "draw the table")
    told = "open the post in his DM, see the table drawn as one table with linked cells, open one link"
    run("job", "brief", "j1", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-slack main", "--native", "his Slack DM", "--interactions", told)
    post = "https://acme.slack.com/archives/D123/p1794559450293519?thread_ts=1794555580.526529&cid=D123"
    done = f"DONE the table post draws as one table\nCHECK: {post}\nNATIVE: Slack's table block\nUNPROVEN: no picture of the post in his Slack window\n"
    code, refused = keep(monkeypatch, capsys, "j1", done)
    assert code == 2
    assert "j1's brief names user stories and it keeps no proof file holding their acceptance criteria" in refused
    kept_proof(tmp_path, "j1")
    assert keep(monkeypatch, capsys, "j1", done)[0] == 0
    [asked] = [asked["questions"]["proof1"]["label"]["instructions"] for asked in jev.asked if "proof1" in asked["questions"]]
    assert "the check names a picture or recording of it, or the live post's link its proof file reads back" in asked


STAND_IN = "https://example.slack.com/archives/D0PROOF/p1794600000000100"
OFF_WORKSPACE = "it is on example.slack.com, not his Slack workspace acme.slack.com"
READ_LINK = brief.unopened.__wrapped__


def test_a_proof_naming_the_demos_placeholder_slack_link_is_refused_on_a_done_and_on_order_set_proof_and_one_naming_what_opens_is_kept(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], unopened_links: dict[str, str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "post one card line")
    run("job", "add", "builder", "--order", "o1", "--goal", "post it")
    unopened_links[STAND_IN] = OFF_WORKSPACE
    output = tmp_path / "j1.proof.txt"
    output.write_text("ok ask 1 posts one card line\n")
    refused = f"a DONE's links each open as he would open them, and {STAND_IN} does not open: {OFF_WORKSPACE}"
    for done in (f"DONE one card line\nCHECK: {output} {STAND_IN}\n", f"DONE one card line at {STAND_IN}\nCHECK: {output}\n"):
        code, said = keep(monkeypatch, capsys, "j1", done)
        assert (code, refused in said) == (2, True)
    assert cli.main(["order", "set", "o1", "--proof", f"{output} {STAND_IN}"]) == 2
    assert f'--proof "{output} {STAND_IN}": refused, a proof\'s links each open as he would open them, and {STAND_IN} does not open: {OFF_WORKSPACE}' in capsys.readouterr().err
    assert state.load("demo")["orders"]["o1"]["proof"] is None
    assert keep(monkeypatch, capsys, "j1", f"DONE one card line\nCHECK: {output} {PR}\n")[0] == 0


def test_a_proof_link_opens_only_as_a_post_in_his_slack_workspace_cc_slack_reads_back_or_a_github_or_buildkite_page_their_cli_returns(
    monkeypatch: pytest.MonkeyPatch, slack_thread: Thread
) -> None:
    ran: list[list[str]] = []

    def answered(cmd: list[str], **_: object) -> str:
        ran.append(cmd)
        if cmd[2].endswith("/404"):
            raise github.GithubError("HTTP 404")
        return ""

    monkeypatch.setattr(slack, "whoami", lambda: {"team_url": "https://acme.slack.com/", "dm_channel_id": "D123"})
    monkeypatch.setattr(github, "run", answered)
    slack_thread.messages.append({"ts": "1794576780.293900", "text": "his card", "from_claude": True})
    post = "https://acme.slack.com/archives/D123/p1794576780293900"
    gone = "https://acme.slack.com/archives/D123/p1794600000000100"
    assert [READ_LINK(link) for link in (post, STAND_IN, gone)] == [None, OFF_WORKSPACE, f"no message 1794600000.000100 in the thread at --url {gone}"]
    pages = {
        "https://github.com/yasyf/cc-skills/pull/5075": "repos/yasyf/cc-skills/pulls/5075",
        "https://github.com/yasyf/cc-skills/commit/b3351b5": "repos/yasyf/cc-skills/commits/b3351b5",
        "https://github.com/yasyf/cc-skills/actions/runs/12/job/34": "repos/yasyf/cc-skills/actions/runs/12",
        "https://github.com/yasyf/cc-skills/releases/tag/v1.0.863": "repos/yasyf/cc-skills/releases/tags/v1.0.863",
    }
    assert [READ_LINK(link) for link in pages] == [None] * 4
    assert [cmd[2] for cmd in ran] == list(pages.values())
    assert READ_LINK("https://github.com/yasyf/cc-skills/pull/404") == "could not read https://github.com/yasyf/cc-skills/pull/404 from GitHub: HTTP 404"
    assert READ_LINK("https://github.com/yasyf/cc-skills/blob/main/README.md") == "it names no pull request, commit, Actions run or release, the GitHub pages the drive reads"
    assert READ_LINK("https://buildkite.com/forge/release/builds/14185") is None
    assert ran[-1] == ["bk", "build", "view", "14185", "-p", "forge/release", "-o", "json"]
    assert READ_LINK("https://app.datadoghq.com/logs?query=service%3Aapi").startswith("the drive reads no app.datadoghq.com link")


def test_a_github_proof_link_refused_under_ccxs_rate_limit_floor_waits_for_the_reset_it_names_then_reads_what_github_answers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    floor = github.GithubError(
        "ccx: GitHub rate limit floor: user core has 749 of 5000 left, under the floor of 1000, until 10:52AM (2026-11-13T10:52:51-08:00); top callers: zsh 188"
    )
    answers = iter([floor, floor, "", floor, github.GithubError("HTTP 404")])
    slept: list[float] = []

    def answered(cmd: list[str], **_: object) -> str:
        if isinstance(answer := next(answers), Exception):
            raise answer
        return answer

    monkeypatch.setattr(github, "run", answered)
    monkeypatch.setattr(github.time, "time", lambda: datetime(2026, 11, 13, 18, 50, 51, tzinfo=UTC).timestamp())
    monkeypatch.setattr(github.time, "sleep", slept.append)
    assert READ_LINK(PR) is None
    assert READ_LINK("https://github.com/yasyf/cc-skills/pull/404") == "could not read https://github.com/yasyf/cc-skills/pull/404 from GitHub: HTTP 404"
    assert slept == [121, 121, 121]
    assert f"waiting 121 s for ccx's GitHub rate-limit floor to lift before reading {PR} again: could not read {PR} from GitHub: {floor}" in capsys.readouterr().err


def keep(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], job_id: str, text: str) -> tuple[int, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    code = cli.main(["job", "report", job_id, "-"])
    return code, capsys.readouterr().err


def kept_proof(tmp_path: Path, *job_ids: str) -> None:
    script = tmp_path / "proof.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    with state.edit("demo") as data:
        for job_id in job_ids:
            data["jobs"][job_id]["repro"] = str(script)


def test_a_builders_ready_naming_a_yasyf_pr_is_refused_until_it_keeps_a_proof_file_it_can_run(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    run("pr", "add", "https://github.com/yasyf/cc-skills/pull/7", "--job", "j1", *LEAD)
    ready = "READY https://github.com/yasyf/cc-skills/pull/7\n"
    code, err = keep(monkeypatch, capsys, "j1", ready)
    assert (code, state.load("demo")["jobs"]["j1"]["state"]) == (2, "running")
    assert "it names yasyf/cc-skills#7, which merges once this is taken and the watch proves by running j1's proof file, and j1 keeps none it can run" in err
    steps = tmp_path / "steps.md"
    steps.write_text("1. run it\n")
    run("job", "set", "j1", "--repro", str(steps))
    assert keep(monkeypatch, capsys, "j1", ready)[0] == 2
    kept_proof(tmp_path, "j1")
    assert keep(monkeypatch, capsys, "j1", ready) == (0, "")


def test_a_builder_proving_a_merged_pr_live_on_a_done_order_is_refused_naming_the_job_that_proved_it(run: Run, communicator: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "show each step", "--kind", "build")
    key = "Forge-AI/monorepo#51852"
    with state.edit("demo") as data:
        data["orders"]["o1"] |= {"state": "done", "jobs": ["j1"]}
        handoff = f"{key} at 485f9f408150 merged: install it and prove it live"
        data["jobs"]["j1"] = state.new_job("o1", "builder", "prove it", list(data["orders"]["o1"]["parts"]), state.stamp()) | {"state": "done", "said": "DONE live on its release", "brief_flags": {"handoff": handoff}}
        data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"state": "merged", "merged_sha": "485f9f408150"}
    assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", f"prove {key} live"]) == 2
    assert f"refused, no job added: o1 is done and j1 proved {key} live, so no builder proves {key} live on it again" in capsys.readouterr().err
    assert list(state.load("demo")["jobs"]) == ["j1"]


def test_an_answer_order_is_one_readers_and_a_build_orders_builder_waits_on_its_readers_research(
    run: Run, communicator: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why did the copy stall?", "--kind", "answer", "--complex")
    assert [state.load("demo")["orders"]["o1"][field] for field in ("kind", "complex")] == ["answer", True]
    assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", "fix the copy"]) == 2
    assert "refused, no job added: o1 is an answer order: one reader researches it and answers him" in capsys.readouterr().err
    assert run("job", "add", "reader", "--order", "o1", "--goal", "answer him")[0] == 0
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy one store", "--kind", "build")
    assert cli.main(["job", "add", "builder", "--order", "o2", "--goal", "build it"]) == 2
    assert "o2 is a build order, so its builder starts only once an Opus reader of its part" in capsys.readouterr().err
    run("job", "add", "reader", "--order", "o2", "--goal", "research: the copy")
    keep(monkeypatch, capsys, "j2", "DONE read the copy path\n")
    assert cli.main(["job", "add", "builder", "--order", "o2", "--goal", "build it"]) == 2
    capsys.readouterr()
    assert keep(monkeypatch, capsys, "j2", RESEARCH) == (0, "")
    assert run("job", "add", "builder", "--order", "o2", "--goal", "build it")[1].splitlines()[0] == "j3"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "an order from an older skill")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL copies fail on plat", "--incident", "--parts", "fix", "first check")
    assert state.load("demo")["orders"]["o4"]["kind"] == "build"
    for order_id, part in (("o3", []), ("o4", ["--part", "fix"])):
        assert run("job", "add", "builder", "--order", order_id, "--goal", "fix it", *part)[0] == 0


def test_a_build_orders_design_job_waits_on_its_readers_research(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    assert cli.main(["job", "add", "design", "--order", "o1", "--goal", "cards: where the copy runs"]) == 2
    assert "o1 is a build order, so its design starts only once an Opus reader of its part" in capsys.readouterr().err
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    keep(monkeypatch, capsys, "j1", RESEARCH)
    assert run("job", "add", "design", "--order", "o1", "--goal", "cards: where the copy runs")[0] == 0


def test_a_reader_carrying_another_orders_part_is_that_parts_reader_so_its_builder_is_added_waiting_on_it(
    run: Run, meta: str, capsys: pytest.CaptureFixture[str]
) -> None:
    filed = ("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "--kind", "build")
    run(*filed, "Drive tooling: copy one store")
    run(*filed, "Drive tooling: copy every store")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    assert run("job", "set", "j1", "--carries", "o2")[0] == 0
    assert cli.main(["job", "add", "builder", "--order", "o2", "--goal", "build it"]) == 2
    assert "while j1 reads, add this builder now waiting on it with --waits j1" in capsys.readouterr().err
    assert run("job", "add", "builder", "--order", "o2", "--goal", "build it", "--waits", "j1")[1].splitlines()[0] == "j3"


def test_a_build_orders_designer_and_builder_are_added_waiting_on_its_reader_still_at_work(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        "order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: topologies", "--kind", "build",
        "--parts", "answer", "control freak", "design options",
    )
    run("job", "add", "reader", "--order", "o1", "--part", "answer", "--part", "control freak", "--part", "design options", "--goal", "research: topologies")
    assert cli.main(["job", "add", "design", "--order", "o1", "--part", "design options", "--goal", "cards"]) == 2
    assert "while j1 reads, add this design now waiting on it with --waits j1" in capsys.readouterr().err
    added = run("job", "add", "design", "--order", "o1", "--part", "design options", "--goal", "cards", "--waits", "j1")
    assert added == (0, "j3\nj3 waits on j1: start no agent for it until the watch's FREE line says its last wait ended")
    assert run("job", "add", "builder", "--order", "o1", "--part", "control freak", "--goal", "build it", "--waits", "j1")[1].splitlines()[:2] == [
        "j2",
        'j2 carried "control freak" and no worker took it, so this add folded into it, a part having one change job: its goal is this add\'s and its waits gained this add\'s',
    ]
    jobs = state.load("demo")["jobs"]
    assert [(jobs[job_id]["state"], jobs[job_id]["waits"], jobs[job_id]["agent"]) for job_id in ("j2", "j3")] == [("waiting", ["j1"], None)] * 2
    assert (jobs["j2"]["goal"], "j4" in jobs) == ("build it", False)
    refusals = {
        "while j1 reads, add this builder now waiting on it with --waits j1": ["builder", "--part", "control freak", "--waits", "j3"],
        "--waits records a job whose agent starts on the watch's FREE line": ["builder", "--part", "control freak", "--waits", "j1", "--agent", "a"],
    }
    for refusal, words in refusals.items():
        assert cli.main(["job", "add", *words, "--order", "o1", "--goal", "x"]) == 2
        assert refusal in capsys.readouterr().err
    assert run("job", "add", "reader", "--order", "o1", "--part", "answer", "--goal", "audit it", "--waits", "j1")[1].splitlines()[0] == "j4"
    keep(monkeypatch, capsys, "j1", RESEARCH)
    assert cli.main(["job", "add", "builder", "--order", "o1", "--part", "control freak", "--goal", "x", "--waits", "j1"]) == 2
    assert "--waits j1: j1 is done, so nothing of it is left to wait on" in capsys.readouterr().err
    assert run("job", "add", "builder", "--order", "o1", "--part", "control freak", "--goal", "x", "--waits", "j4")[1].splitlines()[0] == "j2"
    assert "j4" in state.load("demo")["jobs"]["j2"]["waits"]


def test_a_readers_builder_builds_its_parts_and_each_later_readers_done_rebriefs_it_on_the_newest_research_until_an_agent_takes_it(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build", "--parts", "the copy", "the proof")
    run("job", "add", "reader", "--order", "o1", "--part", "the copy", "--part", "the proof", "--goal", "research: read-only, at once: why the copy stalls")
    assert state.load("demo")["jobs"]["j2"]["goal"] == "build the copy; the proof"
    assert keep(monkeypatch, capsys, "j1", RESEARCH)[0] == 0
    assert state.load("demo")["jobs"]["j2"]["goal"] == "build one store_copy_at call per stalled store"
    run("job", "add", "reader", "--order", "o1", "--part", "the copy", "--goal", "research: the copy again")
    assert keep(monkeypatch, capsys, "j3", RESEARCH.replace("per stalled store", "per stalled store, oldest first"))[0] == 0
    written = Path(state.load("demo")["jobs"]["j2"]["brief"]).read_text()
    assert ("\nGoal: build one store_copy_at call per stalled store, oldest first\n" in written, "\nResearched: j3\n" in written) == (True, True)
    assert run("job", "add", "builder", "--order", "o1", "--part", "the copy", "--goal", "copy it", "--agent", "a1")[1].splitlines()[0] == "j2"
    run("job", "add", "reader", "--order", "o1", "--part", "the copy", "--goal", "research: the copy once more")
    assert keep(monkeypatch, capsys, "j4", RESEARCH.replace("per stalled store", "per stalled store, newest first"))[0] == 0
    job = state.load("demo")["jobs"]["j2"]
    assert (job["goal"], job["agent"], Path(job["brief"]).read_text() == written) == ("copy it", "a1", True)


def test_an_order_added_with_no_parts_is_one_part_named_by_its_title_and_its_readers_build_line_names_its_builders_goal(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    words = "Drive tooling: a builder added on an alerts order gets its part name and its goal cut at eighty characters mid word"
    run("order", "add", "--title", "Builder goal cut mid word", "--project", "The drive", "--workstream", "Drive tooling", words, "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: where the cut comes from")
    builder = state.load("demo")["jobs"]["j2"]
    assert (list(state.load("demo")["orders"]["o1"]["parts"]), builder["parts"], builder["goal"]) == (["Builder goal cut mid word"], ["Builder goal cut mid word"], "build Builder goal cut mid word")
    assert keep(monkeypatch, capsys, "j1", RESEARCH)[0] == 0
    builder = state.load("demo")["jobs"]["j2"]
    assert (builder["goal"], "\nGoal: build one store_copy_at call per stalled store\n" in Path(builder["brief"]).read_text()) == ("build one store_copy_at call per stalled store", True)


def test_a_build_jobs_research_is_a_reader_of_its_own_part_a_designs_change_counting_as_the_designs_part(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        "order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: noisy neighbors", "--kind", "build",
        "--parts", "the breaker", "the proof", "the alert",
    )
    run("job", "add", "reader", "--order", "o1", "--part", "the breaker", "--goal", "research: the breaker")
    keep(monkeypatch, capsys, "j1", RESEARCH)
    run("job", "add", "reader", "--order", "o1", "--part", "the proof", "--goal", "prove the breaker live")
    keep(monkeypatch, capsys, "j3", RESEARCH)
    assert cli.main(["job", "add", "builder", "--order", "o1", "--part", "the alert", "--goal", "alert on trips"]) == 2
    assert 'drive job add reader --order o1 --part "the alert" --goal "research: <what>"' in capsys.readouterr().err
    assert run("job", "add", "design", "--order", "o1", "--part", "the breaker", "--goal", "cards: the breaker")[1].splitlines()[0] == "j5"
    owned = DESIGNED_IN_CHANGES.replace('none: ccx code search "store copy" and rg store_copy found nothing', "supervisor/copies.rs:40 store_copy_at, extended by change 1")
    assert keep(monkeypatch, capsys, "j5", owned) == (0, "")
    assert state.load("demo")["jobs"]["j7"]["parts"] == ["the reads through it"]
    code, out = run("job", "brief", "j7", "--serves", "drive tooling", "--done-when", "merged", "--where", "yasyf/cc-skills main")
    assert (code, "\nResearched: j1\n" in Path(out.splitlines()[0].removeprefix("Read your brief first: ")).read_text()) == (0, True)
    data = state.load("demo")
    assert brief.brief_refusal("demo", data, "j7", "Researched: j1\nDrive: demo, order o1, job j7") is None
    assert brief.brief_refusal("demo", data, "j7", "Researched: j3\nDrive: demo, order o1, job j7") == (
        "Researched: j3 names no reader of j7's part of o1 that kept a Researched: line and under it rulings:, exists: and open: lines in its report"
    )


def test_a_builder_is_added_waiting_on_another_builders_change_which_names_that_builders_open_pr_once_it_opens_one(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    built = "Forge-AI/monorepo#7"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build", "--parts", "the copy", "the read")
    run("job", "add", "reader", "--order", "o1", "--part", "the copy", "--part", "the read", "--goal", "research: the copy")
    keep(monkeypatch, capsys, "j1", RESEARCH)
    assert run("job", "set", "j2", "--parts", "the copy")[0] == 0
    code, out = run("job", "add", "builder", "--order", "o1", "--part", "the read", "--goal", "read the copy", "--waits", "j2")
    assert (code, out.splitlines()[:2]) == (0, ["j3", "j3 waits on j2: start no agent for it until the watch's FREE line says its last wait ended"])
    assert run("pr", "add", state.pr_url(built), "--job", "j2", *LEAD)[0] == 0
    assert state.load("demo")["jobs"]["j3"]["waits"] == [built]
    assert run("job", "add", "builder", "--order", "o1", "--part", "the copy", "--goal", "copy the next store", "--waits", "j2")[1].splitlines()[0] == "j4"
    assert state.load("demo")["jobs"]["j4"]["waits"] == [built]
    assert run("job", "add", "reader", "--order", "o1", "--part", "the read", "--goal", "dropped")[0] == 0
    assert cli.main(["job", "add", "builder", "--order", "o1", "--part", "the read", "--goal", "x", "--waits", "j1"]) == 2
    assert capsys.readouterr().err.strip().endswith("refused, no job added: --waits j1: j1 is done, so nothing of it is left to wait on")
    assert run("job", "add", "design", "--order", "o1", "--part", "the read", "--goal", "x", "--waits", "j5")[1].splitlines()[0] == "j6"
    assert run("job", "add", "design", "--order", "o1", "--part", "the read", "--goal", "y", "--waits", "j2")[1].splitlines()[0] == "j7"
    assert [state.load("demo")["jobs"][job_id]["waits"] for job_id in ("j6", "j7")] == [["j5"], [built]]


def test_a_jobs_clock_is_a_time_to_come(run: Run, meta: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store")
    run("job", "add", "codex", "--order", "o1", "--goal", "first check")
    assert cli.main(["job", "set", "j1", "--due", "2026-01-01T00:00:00+00:00"]) == 2
    assert "j1: refused, no clock set:" in capsys.readouterr().err
    assert state.load("demo")["jobs"]["j1"]["due"] is None
    assert run("job", "set", "j1", "--due", "+10m")[0] == 0
    assert timedelta(minutes=9) < state.parse_time(state.load("demo")["jobs"]["j1"]["due"]) - state.now() <= timedelta(minutes=10)


def test_a_proof_builder_starts_with_no_lands_after_a_readers_design_whatever_question_the_reader_kept(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    design, built, proof = "design: where the copy runs, his calls on its shape", "build: copy each store", "prove the copy live"
    run(
        "order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build",
        "--parts", design, built, proof,
    )
    run("job", "add", "reader", "--order", "o1", "--part", design, "--part", built, "--part", proof, "--goal", "research: the copy")
    assert keep(monkeypatch, capsys, "j1", DESIGN_RESEARCH) == (0, "")
    assert run("job", "add", "design", "--order", "o1", "--part", design, "--part", built, "--goal", "cards: where the copy runs")[0] == 0
    assert run("job", "add", "builder", "--order", "o1", "--part", built, "--goal", "copy each store")[0] == 0
    asked = str(state.drive_dir("demo") / "asks" / "j1-1377896a.md")
    kept = state.new_question(asked, DESIGN_TITLE, state.stamp())
    with state.edit("demo") as data:
        data["jobs"]["j3"]["questions"] = [kept | {"card": "https://acme.slack.com/archives/D123/p1794480049653959", "closed": state.new_close("answered", None, None, seq="671985")}]
        data["jobs"]["j2"]["state"] = "done"
    replays = {
        "o319's j819, kept before drive job report sent asks, or its question dropped": [],
        "kept with no waits id": [kept],
        "refused by drive job ask": [kept | {"refused": "refused: Jev reads his words as ruling out what this card offers him."}],
    }
    for case, questions in replays.items():
        with state.edit("demo") as data:
            data["jobs"]["j1"]["questions"] = questions
        code, out = run("job", "add", "builder", "--order", "o1", "--part", proof, "--goal", "prove the copy live")
        assert (case, code) == (case, 0)
        run("job", "set", out.splitlines()[0], "--state", "stopped")


def test_his_ruling_settling_a_readers_ask_before_its_card_draws_is_kept_on_its_question(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    keep(monkeypatch, capsys, "j1", DESIGN_RESEARCH)
    asked = str(state.drive_dir("demo") / "asks" / "j1-1377896a.md")
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"] = [state.new_question(asked, "Where does the copy run?", state.stamp()) | {"waits": "demo:j1-671975"}]
    posted: list[dict] = []
    real = subprocess.run

    def cci(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["cci", "post"]:
            posted.extend(json.loads(str(kwargs["input"])))
            return subprocess.CompletedProcess(argv, 0, '{"seq": 671985}\n', "")
        return real(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", cci)
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS)
    ruling = {"kind": "answer", "text": "always do the correct first-principels thing not. ahack", "waits": "demo:j1-671975", "ruling": "32164d3"}
    with monkeypatch.context() as communicating:
        communicating.setenv("CLAUDE_CODE_SESSION_ID", "communicator-session")
        run("start", "demo", "--role", "communicator")
        assert tools.run("record", "demo", {"records": [ruling]}).splitlines() == [
            "1 answer 671985",
            '1 j1\'s question "Where does the copy run?" ended answered by 671985',
        ]
        with pytest.raises(state.DriveError, match="1: refused, nothing posted: the question this answer names ended already"):
            tools.run("record", "demo", {"records": [ruling]})
    assert (len(posted), posted[0].get("re"), posted[0].get("resolves"), posted[0]["attr"]) == (1, None, None, {"job": "j1", "waits": "demo:j1-671975", "ruling": "32164d3"})
    job = state.load("demo")["jobs"]["j1"]
    assert (view.open_questions(job), job["questions"][0]["closed"]["ruling"]) == ([], "32164d3")


def test_the_immediate_fix_starts_at_once_past_the_research_the_design_and_options_only(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        "order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build",
        "--parts", "the copy", "immediate fix", "--options-only",
    )
    assert cli.main(["job", "add", "builder", "--order", "o1", "--part", "the copy", "--goal", "build it"]) == 2
    assert "o1 is limited to options: he picks one before the proper fix is built" in capsys.readouterr().err
    assert run("job", "add", "builder", "--order", "o1", "--part", "immediate fix", "--goal", "stop the harm", "--agent", "a1") == (0, "j1")
    run("order", "set", "o1", "--no-options-only")
    assert cli.main(["job", "add", "builder", "--order", "o1", "--part", "the copy", "--goal", "build it"]) == 2
    assert 'starts at once on its own "immediate fix" part, added with drive order set o1 --parts "the copy" "immediate fix"' in capsys.readouterr().err
    run("job", "add", "reader", "--order", "o1", "--part", "the copy", "--goal", "research: the copy")
    assert keep(monkeypatch, capsys, "j2", DESIGN_RESEARCH) == (0, "")
    assert cli.main(["job", "add", "codex", "--order", "o1", "--part", "immediate fix", "--goal", "stop it another way"]) == 2
    assert capsys.readouterr().err.strip().endswith(
        'refused, no job added: j1 builder running carries "immediate fix" already, so a second codex would race it on one change: send it the event with'
        ' drive job resume j1 "<the event>", or stop it with drive job set j1 --state stopped before adding the job that replaces it'
    )
    assert cli.main(["job", "add", "builder", "--order", "o1", "--part", "the copy", "--part", "immediate fix", "--goal", "both"]) == 2
    assert 'the "immediate fix" part is a job of its own: its PR lands at once' in capsys.readouterr().err
    assert cli.main(["job", "set", "j1", "--part", "the copy"]) == 2
    assert 'the "immediate fix" part is a job of its own' in capsys.readouterr().err
    assert state.load("demo")["jobs"]["j1"]["parts"] == ["immediate fix"]


def test_a_follow_up_he_orders_to_a_pr_gets_its_designer_at_once_with_no_reader_or_wait(run: Run, meta: str, capsys: pytest.CaptureFixture[str]) -> None:
    run(
        "order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: land the card fix, then a follow-up that is not hacky",
        "--kind", "build", "--parts", "the card fix", "follow-up",
    )
    assert cli.main(["job", "add", "design", "--order", "o1", "--part", "the card fix", "--goal", "design it"]) == 2
    capsys.readouterr()
    assert run("job", "add", "reader", "--order", "o1", "--part", "the card fix", "--goal", "research: the card fix") == (
        0,
        "j1\nj2 builder on \"the card fix\" waits on j1: start no agent for it until the watch's FREE line says its last wait ended",
    )
    assert cli.main(["job", "add", "design", "--order", "o1", "--part", "follow-up", "--waits", "j1", "--goal", "design the follow-up"]) == 2
    assert 'a "follow-up" design starts now, beside landing the PR it follows' in capsys.readouterr().err
    assert cli.main(["job", "add", "design", "--order", "o1", "--part", "follow-up", "--part", "the card fix", "--goal", "both"]) == 2
    assert 'the "follow-up" part is a job of its own: its design starts at once' in capsys.readouterr().err
    assert run("job", "add", "design", "--order", "o1", "--part", "follow-up", "--goal", "design the follow-up") == (0, "j3")


def test_a_readers_design_on_a_reported_bug_names_the_immediate_fix_shipping_while_he_picks(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        "order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the stuck restores", "--kind", "build",
        "--thread", "1794200000.000100", "--reporter", REPORTED, "--parts", "the copy", "immediate fix",
    )
    assert run("job", "add", "builder", "--order", "o1", "--part", "immediate fix", "--goal", "repair the stuck restores") == (0, "j1")
    run("job", "add", "reader", "--order", "o1", "--part", "the copy", "--goal", "research: the copy")
    code, err = keep(monkeypatch, capsys, "j2", DESIGN_RESEARCH)
    assert (code, "a reader's DESIGN on a bug hurting someone now names its immediate fix" in err) == (2, True)
    code, err = keep(monkeypatch, capsys, "j2", DESIGN_RESEARCH + "Interim fix: j1 repairs the stuck restores\n")
    assert (code, f"j2 traces the cause of o1's live bug, so {state.FIX_RULE}" in err) == (2, True)
    assert keep(monkeypatch, capsys, "j2", DESIGN_RESEARCH + "Interim fix: j1 repairs the stuck restores\nFIX o1\n") == (0, "")


def test_a_cause_report_names_its_fix_whose_order_reads_live_from_it(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.reads, "run", recorded([]))
    run("incident", "open", "SandSQL mint endpoint failing", *FILED)
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the handover", "--kind", "build")
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the restores", "--kind", "build")
    where = ("--serves", "his order", "--done-when", "fixed", "--where", "Forge-AI/monorepo dev")
    run("job", "brief", "j1", *where)
    run("job", "brief", "j3", *where)
    jobs = state.load("demo")["jobs"]
    assert (state.FIX_RULE in Path(jobs["j1"]["brief"]).read_text(), state.FIX_RULE in Path(jobs["j3"]["brief"]).read_text()) == (False, True)
    traced = "DONE the predecessor's handover delays each mint\n"
    code, err = keep(monkeypatch, capsys, "j3", traced)
    assert (code, f"j3: refused, nothing kept: j3 traces the cause of o1's live bug, so {state.FIX_RULE}" in err) == (2, True)
    code, err = keep(monkeypatch, capsys, "j3", traced + "FIX o2, o9\n")
    assert (code, "its FIX line names o9, neither an order of the drive nor a GitHub PR link" in err) == (2, True)
    assert keep(monkeypatch, capsys, "j3", traced + "FIX o2\n") == (0, "")
    orders = state.load("demo")["orders"]
    assert [(orders[order_id]["joins"], state.hurts_now(state.load("demo"), orders[order_id])) for order_id in ("o2", "o3")] == [("o1", True), (None, False)]
    assert state.incident_orders(state.load("demo"), "o1") == ["o1", "o2"]


@pytest.mark.parametrize(
    ("fix", "author", "said", "tracked"),
    [
        ("none", "yasyf", None, None),
        (PR, "yasyf", "FIX kept, Forge-AI/monorepo#42: tracked under o1", ("j3", "o1")),
        (PR, "rowan", "FIX kept, Forge-AI/monorepo#42: refused, nothing added: Forge-AI/monorepo#42 was opened on GitHub by rowan, not yasyf", None),
    ],
)
def test_a_cause_reports_fix_linking_a_pr_the_drive_does_not_track_is_kept_and_registers_it_unless_someone_else_opened_it(
    run: Run,
    demo: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    on_github: dict,
    fix: str,
    author: str,
    said: str | None,
    tracked: tuple[str, str] | None,
) -> None:
    monkeypatch.setattr(cli.reads, "run", recorded([]))
    run("incident", "open", "SandSQL mint endpoint failing", *FILED)
    on_github["author"]["login"] = author
    monkeypatch.setattr("sys.stdin", io.StringIO(f"DONE the predecessor's handover delays each mint\nFIX {fix}\n"))
    assert cli.main(["job", "report", "j3", "-", "--json"]) == 0
    fixes = json.loads(capsys.readouterr().out)["fixes"]
    pr = state.load("demo")["prs"].get("Forge-AI/monorepo#42")
    assert ([line[: len(said)] for line in fixes], pr and (pr["job"], pr["order"])) == ([said] if said else [], tracked)


def test_a_builder_landing_prs_he_approved_starts_with_no_research(run: Run, with_pr: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "set", "o1", "--kind", "build")
    assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", "land it"]) == 2
    assert "o1 is a build order, so its builder starts only once an Opus reader of its part" in capsys.readouterr().err
    assert cli.main(["job", "add", "codex", "--order", "o1", "--lands", PR, "--goal", "land it"]) == 2
    assert "--lands names the PRs a builder lands" in capsys.readouterr().err
    assert cli.main(["job", "add", "builder", "--order", "o1", "--lands", PR, "--goal", "land it"]) == 2
    assert f"--lands {with_pr}: it may not land yet, no tap from him yet" in capsys.readouterr().err
    assert cli.main(["job", "add", "builder", "--order", "o1", "--lands", "https://github.com/Forge-AI/monorepo/pull/43", "--goal", "land it"]) == 2
    assert "--lands Forge-AI/monorepo#43: no drive PR" in capsys.readouterr().err
    run("pr", "set", PR, "--tapped", "Approve", "--tap-source", "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794173216327589")
    assert run("job", "add", "builder", "--order", "o1", "--lands", PR, "--goal", "land it")[0] == 0


def test_a_drive_tooling_yasyf_pr_wanting_no_tap_may_land_untapped_as_the_lander_lands_it(run: Run, with_pr: str, meta: str) -> None:
    run("order", "set", "o1", "--kind", "build")
    run("start", "demo", "--role", "meta")
    with state.edit("demo") as data:
        data["orders"]["o1"]["text"] = "Drive tooling: the watch draws his card"
        data["roles"]["coordinator"]["session"] = "elsewhere"
    tooling = "https://github.com/yasyf/cc-skills/pull/7"
    run("pr", "add", tooling, "--job", "j1")
    assert run("pr", "tapped", tooling) == (0, "yasyf/cc-skills#7: wants no tap: the drive lands a yasyf/* PR with none")
    assert run("pr", "tapped", PR) == (1, f"{with_pr}: no tap from him yet")
    assert run("job", "add", "builder", "--order", "o1", "--lands", tooling, "--goal", "land it")[0] == 0


def test_a_design_report_whose_order_thread_cannot_be_read_fails_as_that_read_and_keeps_nothing(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    thread = "1794532526.296849"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build", "--thread", thread)
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")

    def unread(*where: str) -> list[dict]:
        raise slack.SlackError("daemonkit: business session: context deadline exceeded")

    monkeypatch.setattr(slack, "thread", unread)
    code, err = keep(monkeypatch, capsys, "j1", DESIGN_RESEARCH)
    assert (code, f"could not read the order's thread {thread}" in err, "refused" in err) == (2, True, False)
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["questions"], state.replies_path("demo", "j1").exists()) == ("running", [], False)


def test_a_readers_design_report_keeps_its_ask_and_draws_his_card_on_it_in_the_same_call_or_keeps_nothing(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cards_drawn: list[dict]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    unshaped = DESIGN_RESEARCH.replace("Findings:\n", "")
    code, err = keep(monkeypatch, capsys, "j1", unshaped)
    assert (code, "j1: refused, its report not kept: a reader's DESIGN is his card as it stands, kept as its ASK, and drive job ask refused it:" in err) == (2, True)
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["questions"], state.replies_path("demo", "j1").exists()) == ("running", [], False)
    monkeypatch.setattr("sys.stdin", io.StringIO(DESIGN_RESEARCH))
    code, out = run("job", "report", "j1", "-")
    job = state.load("demo")["jobs"]["j1"]
    [question] = job["questions"]
    assert (code, job["state"], question["title"], question["refused"]) == (0, "done", DESIGN_TITLE, None)
    assert out == (
        f"{state.replies_path('demo', 'j1')}\n{question['file']}\nhis card {drawn_card(1)} asks it under demo:j1-7"
        f"\nj2: Read your brief first: {state.load('demo')['jobs']['j2']['brief']}\nhis words from o1, rulings: 0, earlier jobs: 0"
    )
    assert Path(question["file"]).read_text().startswith(f"ASK {DESIGN_TITLE}\n")
    assert ([(drawn["job"], drawn["file"]) for drawn in cards_drawn], question["waits"], question["card"]) == ([("j1", question["file"])], "demo:j1-7", drawn_card(1))
    assert run("job", "add", "builder", "--order", "o1", "--goal", "build the recommended option")[0] == 0


def test_a_readers_design_report_after_its_own_ask_keeps_that_asks_unsettled_and_redraws_its_one_card(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, cards_drawn: list[dict]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    swept = DESIGN_RESEARCH.replace("from the bucket and copies it there\n", "from the bucket, and a sweeper deletes stale copies\n", 1)
    why = f"{limits.NO_SWEEPERS}: a copy a crashed worker left has no owner to end it"
    monkeypatch.setattr("sys.stdin", io.StringIO(swept))
    assert run("job", "ask", "j1", "-", "--unsettled", why)[0] == 0
    assert state.load("demo")["jobs"]["j1"]["state"] == "waiting"
    monkeypatch.setattr("sys.stdin", io.StringIO(swept))
    assert run("job", "report", "j1", "-")[0] == 0
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], [question["unsettled"] for question in job["questions"]], state.report_text("demo", "j1").strip()) == ("done", [why], swept.strip())
    assert [(drawn["job"], drawn["waits"]) for drawn in cards_drawn] == [("j1", "demo:j1-7")] * 2


def test_a_design_that_kept_a_question_is_kept_with_no_fork_line_and_its_builder_waits_on_that_card(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy one store")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store copy")
    block = tmp_path / "fork.md"
    block.write_text(SHORT_ASK.replace("Holds: none: no option changes code any job builds", "Holds: Short title"))
    run("job", "ask", "j1", str(block))
    assert keep(monkeypatch, capsys, "j1", DESIGNED) == (0, "")
    jobs = state.load("demo")["jobs"]
    assert (jobs["j1"]["said"], jobs["j2"]["state"], jobs["j2"]["waits"]) == ("carried by builder j2", "waiting", [jobs["j1"]["questions"][0]["waits"]])


def test_a_builders_ready_says_how_it_reuses_what_its_research_found(
    run: Run, meta: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    keep(monkeypatch, capsys, "j1", RESEARCH)
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    kept_proof(tmp_path, "j2")
    ready = "READY https://github.com/yasyf/cc-skills/pull/1\nSERVES: drive tooling\n"
    code, err = keep(monkeypatch, capsys, "j2", ready)
    assert code == 2
    assert "j2: refused, nothing kept: j1's research found what already does part of this: supervisor/copies.rs store_copy_at copies one store." in err
    assert keep(monkeypatch, capsys, "j2", ready + "REUSES: calls store_copy_at for each store\n") == (0, "")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a new verb", "--kind", "build")
    run("job", "add", "reader", "--order", "o2", "--goal", "research: the verb")
    keep(monkeypatch, capsys, "j3", RESEARCH.replace("exists: supervisor/copies.rs store_copy_at copies one store", "exists: none"))
    run("job", "add", "builder", "--order", "o2", "--goal", "build it")
    kept_proof(tmp_path, "j4")
    assert keep(monkeypatch, capsys, "j4", ready) == (0, "")


def test_a_builders_report_opens_with_a_result_word_and_a_handoff_stops_its_job(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    code, err = keep(monkeypatch, capsys, "j1", "Summary: built the strip\nCHECK: https://github.com/yasyf/cc-skills/actions/runs/12\n")
    assert code == 2
    assert (
        'j1: refused, nothing kept: its first line opens "Summary: built the strip", and a builder\'s report opens with DONE,'
        " READY, PUSHED, BUILT, WAITING, ASK, BLOCKED, DESIGN or HANDOFF"
    ) in err
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    kept_proof(tmp_path, "j1")
    run("pr", "add", "https://github.com/yasyf/cc-skills/pull/4244", "--job", "j1", *LEAD)
    assert keep(monkeypatch, capsys, "j1", "READY https://github.com/yasyf/cc-skills/pull/4244\n") == (0, "")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship again")
    assert keep(monkeypatch, capsys, "j2", "HANDOFF /scratch/j2/left.md\nBRANCH: lr-strip\n") == (0, "")
    assert state.load("demo")["jobs"]["j2"]["state"] == "stopped"


def test_a_report_naming_a_repro_file_keeps_it_on_the_job_first(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why does the copy stall?")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy one store")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store copy")
    code, err = keep(monkeypatch, capsys, "j1", "DONE wrote the forks\n")
    assert code == 2
    assert (
        "j1: refused, nothing kept: a design lane's report is the design in its shape, which the coordinator reads for its"
        " forks, and this one lacks a first line DESIGN <the part>, a Root cause: line, a Settled: line, a Native: line, an Exists: line,"
        " a Change: line, a Facts: line"
    ) in err
    assert keep(monkeypatch, capsys, "j1", DESIGNED) == (0, "")
    assert (state.load("demo")["jobs"]["j1"]["state"], state.load("demo")["jobs"]["j1"]["said"]) == ("done", "carried by builder j2")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store copy again")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store list")
    assert keep(monkeypatch, capsys, "j3", "HANDOFF /scratch/j3/left.md\n") == (0, "")
    assert keep(monkeypatch, capsys, "j4", "BLOCKED the store list is unreadable\n") == (0, "")
    jobs = state.load("demo")["jobs"]
    assert (jobs["j3"]["state"], jobs["j4"]["state"]) == ("stopped", "failed")


@pytest.mark.parametrize(
    ("exists", "refusal"),
    [
        ("none: nothing like it", 'its Exists: line "none: nothing like it" names no search that found nothing'),
        ("supervisor/copies.rs:40 store_copy_at copies one store", 'its Exists: entry "supervisor/copies.rs:40 store_copy_at copies one store" does not cite'),
        ("store_copy_at copies one store, extended by change 1", 'its Exists: entry "store_copy_at copies one store, extended by change 1" does not cite'),
        ("supervisor/copies.rs:40 store_copy_at, extended by change 2", 'its Exists: entry "supervisor/copies.rs:40 store_copy_at, extended by change 2" does not cite'),
        ("supervisor/copies.rs:40 store_copy_at, extended by change 1; lib/drive/state.py:18 the store list, reused by change 1", None),
        ("lib/sprite/drive:17-41 the Sprite's drive script, extended by change 1; bin/drive:5 the drive wrapper, reused by change 1", None),
        ("cci:683904 the defect, extended by change 1", 'its Exists: entry "cci:683904 the defect, extended by change 1" does not cite'),
    ],
)
def test_a_design_names_what_already_does_what_it_touches_and_the_change_that_owns_it_before_adding_anything(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], exists: str, refusal: str | None
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy one store")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store copy")
    code, err = keep(monkeypatch, capsys, "j1", DESIGNED.replace('none: ccx code search "store copy" and rg store_copy found nothing', exists))
    assert code == (2 if refusal else 0)
    assert (refusal in err and brief.DUPLICATION_RULING in err) if refusal else err == ""


def test_a_design_saying_nothing_exists_is_refused_while_its_orders_reader_found_something(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    keep(monkeypatch, capsys, "j1", RESEARCH)
    run("job", "add", "design", "--order", "o1", "--goal", "cards: where the copy runs")
    code, err = keep(monkeypatch, capsys, "j3", DESIGNED)
    assert code == 2
    assert "its Exists: line says none, but j1's research found what already does part of this: \"supervisor/copies.rs store_copy_at copies one store\"" in err
    owned = DESIGNED.replace('none: ccx code search "store copy" and rg store_copy found nothing', "supervisor/copies.rs:40 store_copy_at, extended by change 1")
    assert keep(monkeypatch, capsys, "j3", owned) == (0, "")


@pytest.mark.parametrize(
    ("pick", "refusal"),
    [
        ("o1", 'Jev reads this design as building less than his words "time, hash by key and spill" (his words the order quotes) name, or later, with no fork asking him'),
        ("first", f"Jev reads this design as building a strategy before the one abstraction its strategies share: design that abstraction first, walk every strategy through it on paper with a worked example of each operation, make it change 1 and each strategy a later Change: line waiting on it, by his words {brief.ABSTRACTION_RULING}"),
        ("none", None),
    ],
)
def test_a_design_building_less_or_later_than_his_words_or_a_strategy_before_their_shared_abstraction_is_refused(
    run: Run, meta: str, jev: Jev, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], pick: str, refusal: str | None
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", 'Drive tooling: partition each table by "time, hash by key and spill"')
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the partitions")
    jev.picks(pick)
    code, err = keep(monkeypatch, capsys, "j1", DESIGNED)
    assert code == (2 if refusal else 0)
    assert (refusal in err) if refusal else err == ""
    [read] = [asked for asked in jev.asked if "narrows" in asked["questions"]]
    assert read["state"]["his_words"] == [{"id": "o1", "asked": "his words the order quotes", "said": '"time, hash by key and spill"'}]
    assert list(read["questions"]["narrows"]["label"]["options"]) == ["none", "o1"]


def test_a_design_of_several_changes_makes_each_a_part_of_its_order_with_a_builder_waiting_on_the_builder_of_each_earlier_change_its_line_names(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy one store", "--parts", "copy one store", "the reads through it")
    run("job", "add", "design", "--order", "o1", "--part", "copy one store", "--goal", "cards: the store copy")
    code, err = keep(monkeypatch, capsys, "j1", DESIGNED_IN_CHANGES.replace("(waits on change 2)", "(waits on change 1 and change 3)"))
    assert code == 2
    assert 'its Change: line "Change: the writes through it (waits on change 1 and change 3)" waits on a change its list does not hold' in err
    monkeypatch.setattr("sys.stdin", io.StringIO(DESIGNED_IN_CHANGES.replace("(waits on change 2)", "(waits on change 1 and change 2 live on every cluster)")))
    assert cli.main(["job", "report", "j1", "-"]) == 0
    assert capsys.readouterr().out.splitlines()[-3:] == [
        'j2 builder on "the moved-table interface": start its agent now',
        'j3 builder on "the reads through it": start its agent now',
        'j4 builder on "the writes through it" waits on j2, j3: start no agent for it until the watch\'s FREE line says its last wait ended',
    ]
    data = state.load("demo")
    assert {part: held["design"] for part, held in data["orders"]["o1"]["parts"].items()} == {
        "copy one store": None,
        "the reads through it": "j1",
        "the moved-table interface": "j1",
        "the writes through it": "j1",
    }
    assert {job_id: (job["parts"], job["state"], job["waits"]) for job_id, job in data["jobs"].items() if job["kind"] == "builder"} == {
        "j2": (["the moved-table interface"], "running", []),
        "j3": (["the reads through it"], "running", []),
        "j4": (["the writes through it"], "waiting", ["j2", "j3"]),
    }


def test_each_builder_a_design_adds_is_briefed_with_that_design_as_its_plan_by_a_brief_naming_no_plan_and_its_sprite_brief_carries_the_designs_report(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy one store", "--parts", "copy one store", "the reads through it")
    run("job", "add", "design", "--order", "o1", "--part", "copy one store", "--goal", "cards: the store copy")
    assert keep(monkeypatch, capsys, "j1", DESIGNED_IN_CHANGES)[0] == 0
    for builder_id, change in (("j2", "the moved-table interface"), ("j3", "the reads through it")):
        cli.write_brief("demo", builder_id, {})
        written = Path(state.load("demo")["jobs"][builder_id]["brief"]).read_text()
        assert f'\nPlan: j1\'s design, change "{change}": drive job report j1\n' in written
        assert f"\n# Report of j1, which your brief names\n\n{DESIGNED_IN_CHANGES}" in orca.vm_brief("demo", builder_id, written, None)
    assert run("job", "brief", "j2", "--plan", "the store copy section")[0] == 0
    assert "\nPlan: the store copy section\n" in Path(state.load("demo")["jobs"]["j2"]["brief"]).read_text()

def test_a_designs_change_waits_on_the_builder_of_the_earlier_change_its_line_names_once_that_builders_pr_closed_unmerged(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    closed = "Forge-AI/monorepo#43"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy one store", "--parts", "copy one store", "the reads through it")
    run("job", "add", "design", "--order", "o1", "--part", "copy one store", "--goal", "cards: the store copy")
    with state.edit("demo") as data:
        data["jobs"]["j2"] = state.new_job("o1", "builder", "build the reads", ["the reads through it"], state.stamp()) | {"state": "built", "prs": [state.pr_url(closed)]}
        data["orders"]["o1"]["jobs"].append("j2")
        data["prs"][closed] = state.new_pr(state.pr_url(closed), "j2", "o1") | {"state": "closed"}
    assert keep(monkeypatch, capsys, "j1", DESIGNED_IN_CHANGES)[0] == 0
    jobs = state.load("demo")["jobs"]
    assert {job["parts"][0]: job["waits"] for job_id, job in jobs.items() if job["kind"] == "builder" and job_id != "j2"} == {
        "the moved-table interface": [],
        "the writes through it": ["j2"],
    }


@pytest.mark.parametrize(
    "change",
    [
        "the reads through it: `data.Client`'s Download and DownloadURL sign through ssql-blobstore",
        'the reads through the "names" route',
        "the reads through $BLOBSTORE_URL",
        "the reads through ssql-blobstore, every Download call and every signed URL on the public SandSQL host",
    ],
)
def test_a_design_names_each_change_in_words_the_coordinator_types_as_its_part(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], change: str
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy one store")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store copy")
    code, err = keep(monkeypatch, capsys, "j1", DESIGNED_IN_CHANGES.replace("Change: the reads through it", f"Change: {change}"))
    assert code == 2
    assert f'its Change: line names its change as "{change}": a change is named in at most 80 characters with no backtick' in err
    assert keep(monkeypatch, capsys, "j1", DESIGNED_IN_CHANGES.replace("Change: the reads through it", "Change: the reads through ssql-blobstore")) == (0, "")
    assert state.load("demo")["jobs"]["j3"]["parts"] == ["the reads through ssql-blobstore"]


def test_order_set_parts_keeps_a_designs_changes_and_refuses_a_list_renaming_one_no_builder_delivered(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy one store")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store copy")
    assert keep(monkeypatch, capsys, "j1", DESIGNED_IN_CHANGES) == (0, "")
    designed = state.load("demo")["orders"]["o1"]["parts"]
    names = list(designed)
    assert cli.main(["order", "set", "o1", "--parts", "Short title", "the interface", "the reads through it", "the writes through it"]) == 2
    assert (
        'o1: refused, its parts stay as they are: --parts adds "the interface" and leaves out "the moved-table interface" of j1\'s design,'
        " which no builder delivered"
    ) in capsys.readouterr().err
    run("order", "set", "o1", "--parts", *names)
    assert state.load("demo")["orders"]["o1"]["parts"] == designed
    run("order", "set", "o1", "--parts", *names[:-1])
    assert list(state.load("demo")["orders"]["o1"]["parts"]) == names[:-1]


def test_a_research_readers_report_carries_its_filled_researched_block(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    code, err = keep(monkeypatch, capsys, "j1", "DONE found two callers\n")
    assert code == 2
    assert (
        "j1: refused, nothing kept: j1 researches a build order, so its report carries a Researched: line and under it"
        " rulings:, exists: and open: lines, each filled or none, which its builder's brief names, and a build: line naming in one line the"
        " change its builder makes, its Goal:; it lacks rulings:, exists:, open:, build:"
    ) in err
    assert keep(monkeypatch, capsys, "j1", RESEARCH.replace("open: none\n", "open:\n"))[1].endswith("it lacks open:\n")
    assert keep(monkeypatch, capsys, "j1", RESEARCH) == (0, "")


def test_a_readers_design_starts_from_the_root_cause_and_counts_only_its_options(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    for refused in (DESIGN_RESEARCH.replace(SECOND_OPTION, ""), DESIGN_RESEARCH.replace("(recommended)", "")):
        code, err = keep(monkeypatch, capsys, "j1", refused)
        assert (code, "j1: refused, nothing kept: a reader's DESIGN return is a choice for him" in err) == (2, True)
    assert run("job", "report", "j1")[0] == 2
    code, err = keep(monkeypatch, capsys, "j1", DESIGN_RESEARCH.replace(ROOT_CAUSE, ""))
    assert code == 2
    assert "j1: refused, nothing kept: a reader's DESIGN names its simplest option, starts from the root cause and names the option that removes it: no `Root cause:` line" in err
    bulleted = DESIGN_RESEARCH.replace("\nrulings:", "\n- rulings:").replace("\nexists:", "\n- exists:").replace("\nopen:", "\n- open:")
    assert keep(monkeypatch, capsys, "j1", bulleted) == (0, "")


def test_a_done_or_ready_leaves_no_step_for_someone_else(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "backfill the copies")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy the bucket")
    run("job", "add", "builder", "--order", "o1", "--goal", "copy it", "--agent", "a1")
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
    assert keep(monkeypatch, capsys, "j2", f'DONE limits.py and cli.py never set "hints" (0 grep hits)\n{check}') == (0, "")
    for evidence in (
        f'DEFECT: ccx vcs gh refuses every read on this Sprite: "{refusal}"',
        f'Measured: stack-enqueue read the queue under its "GitHub rate limit floor" of 500 calls, then its `1:20 PM` read was refused: "{refusal}"',
        f'Measured: gh pr view was refused 3 times between `1:10 PM` and `1:24 PM`, each "{refusal}"',
    ):
        assert keep(monkeypatch, capsys, "j2", f"DONE the copy finished\n{check}{evidence}\n") == (0, "")


def test_a_job_report_quoting_when_past_work_happened_is_kept_since_it_goes_between_sessions(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy the bucket")
    run("job", "add", "builder", "--order", "o1", "--goal", "copy it")
    check = "CHECK: https://github.com/yasyf/cc-skills/actions/runs/12\n"
    assert keep(monkeypatch, capsys, "j1", f"DONE j1 started at 4:55 AM and GitHub went green at 3:47:52 PM\n{check}") == (0, "")


def test_a_builders_ready_is_kept_whatever_its_pr_body_says(
    run: Run, with_pr: str, on_github: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    on_github["body"] = CARD_BODY.replace("## What you'll hate most", "## Biggest surprise")
    assert keep(monkeypatch, capsys, "j1", f"READY {PR}\n") == (0, "")
    assert state.load("demo")["jobs"]["j1"]["state"] == "waiting"


def test_a_builders_ready_counts_only_on_a_green_required_check_and_one_still_running_or_red_returns_pushed(
    run: Run, with_pr: str, on_github: dict, pr_reads: dict[str, dict], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    build = "https://buildkite.com/forge/monorepo/builds/7"
    read = {"state": "OPEN", "isDraft": False, "headRefOid": "abc", "baseRefName": "main", "headRefName": "land-it", "mergeCommit": None, "reviewDecision": None, "approvals": [], "mergeable": "MERGEABLE"}
    read |= {"title": "Fix: land it", "author": "yasyf", "labels": [], "ejected_at": None, "native": [], "trunk": None, "required_checks": ["buildkite/test"], "required_reviews": 1}
    test = {"context": "buildkite/test", "targetUrl": build}
    for posted, held in (
        ([], f"{with_pr}'s required checks still run at abc: return"),
        ([{**test, "state": "PENDING"}], f"{with_pr}'s required checks still run at abc: return"),
        ([{**test, "state": "FAILURE"}], f"{with_pr}'s required buildkite/test failed at abc ({build}): fix it and push, then return"),
    ):
        pr_reads[with_pr] = read | {"checks": posted}
        code, err = keep(monkeypatch, capsys, "j1", f"READY {PR}\n")
        assert code == 2
        assert f"j1: refused, nothing kept: READY counts only on a green required check, and {held} this report with `PUSHED {PR}` as its first line" in err
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    assert keep(monkeypatch, capsys, "j1", f"PUSHED {PR}\n") == (0, "")
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("waiting", f"PUSHED {PR}")


def test_a_builders_ready_and_a_serves_are_kept_unjudged_and_only_the_watchs_check_judges_the_pr_once_at_its_head(
    run: Run, with_pr: str, on_github: dict, judge: Judge, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    judge.against = [{"order": "o1", "words": "land it", "file": "api/run.ts", "change": "api/run.ts keeps the staff flag"}]
    assert keep(monkeypatch, capsys, "j1", f"READY {PR}\n") == (0, "")
    assert run("pr", "set", PR, "--serves", "o1", "--design", "none")[0] == 0
    pr = state.load("demo")["prs"][with_pr]
    assert (judge.asked, pr["serves"], pr["checked"], state.load("demo")["jobs"]["j1"]["state"]) == ([], "o1", None, "waiting")
    assert cli.main(["pr", "check", PR, "--drive", "demo"]) == 0
    refused = f'{served.NOT_READY}: {with_pr} go against his words, judged from each PR\'s title and diff: o1 "land it": api/run.ts keeps the staff flag.'
    assert state.load("demo")["prs"][with_pr]["checker"]["outcome"].startswith(refused)
    judge.against = []
    assert cli.main(["pr", "check", PR, "--drive", "demo"]) == 0
    assert state.load("demo")["prs"][with_pr]["checked"]["orders"] == ["o1"]
    assert cli.main(["pr", "check", PR, "--drive", "demo"]) == 0
    assert len(judge.asked) == 2


DOORS = {
    "id": "953a8c8f00",
    "title": "Should Directory::get stop returning records now, or once Env::file_of reads env_id?",
    "body": "Close every door once Env::file_of reads env_id (recommended)\nOptions: Once Env::file_of reads env_id | Now\nNotes: the coordinator's reading, never his",
    "tags": [],
    "created_at": "2026-11-12T11:33:00Z",
}


def test_a_pr_of_a_two_part_job_naming_one_part_is_judged_alone_against_that_part_and_the_answers_its_design_cites(
    run: Run, demo: str, judge: Judge, recorded: Answers, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "one abstraction everything goes through", "--parts", "moved-table abstraction", "doors close", "partitioning")
    run("job", "add", "builder", "--order", "o1", "--part", "moved-table abstraction", "--part", "doors close", "--goal", "route every caller through Env")
    run("pr", "add", PR, "--job", "j1", *LEAD)
    run("pr", "add", STACKED, "--job", "j1", *LEAD)
    recorded.shown["953a8c8"] = DOORS
    assert cli.main(["pr", "set", PR, "--part", "partitioning"]) == 2
    assert 'its job j1 delivers no part partitioning; its parts are "moved-table abstraction", "doors close"' in capsys.readouterr().err
    code, out = run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "asked:953a8c8", "--part", "moved-table abstraction")
    assert (code, out.rstrip().endswith("delivers moved-table abstraction"), judge.asked) == (0, True, [])
    assert cli.main(["pr", "check", PR, "--drive", demo]) == 0
    said, changes = judge.asked[-1]
    assert list(changes) == ["Forge-AI/monorepo#42"]
    assert "this change delivers: moved-table abstraction\n  other changes deliver: doors close; partitioning\n" in said
    answered = said.rpartition("his answers that bind the PRs, those their brief carries and those they cite for the design they make:\n")[2]
    assert answered.startswith("953a8c8, ") and answered.endswith(f'asked "{DOORS["title"]}": "Close every door once Env::file_of reads env_id (recommended)\nOptions: Once Env::file_of reads env_id | Now"')
    assert "the coordinator's reading" not in said
    pr = state.load(demo)["prs"]["Forge-AI/monorepo#42"]
    assert (pr["parts"], pr["design"], pr["checked"]["orders"]) == (["moved-table abstraction"], "asked:953a8c8", ["o1"])


def test_a_pr_whose_job_carries_one_part_of_its_order_passes_on_that_part_while_another_jobs_pr_carries_the_next(
    run: Run, demo: str, judge: Judge, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, purpose = "The agent API reads and writes the sessions table", "Purpose moves to the chat's meta, set by an inbox message"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Agents", "agents live in the sessions table", "--parts", sessions, purpose)
    run("job", "add", "builder", "--order", "o1", "--part", sessions, "--goal", "move the API")
    run("job", "add", "builder", "--order", "o1", "--part", purpose, "--goal", "move purpose")
    run("pr", "add", PR, "--job", "j1", *LEAD)
    run("pr", "add", STACKED, "--job", "j2", *LEAD)
    refused = {"order": "o1", "words": purpose, "file": "", "change": "Agent.purpose still reads the agents table"}
    monkeypatch.setattr(served, "judged", lambda name, said, changes: judge.asked.append((said, changes)) or ([] if f"other changes deliver: {purpose}" in said else [refused]))
    assert cli.main(["pr", "check", PR, "--drive", demo]) == 0
    said, changes = judge.asked[-1]
    assert (list(changes), f"this change delivers: {sessions}\n" in said) == (["Forge-AI/monorepo#42"], True)
    assert state.load(demo)["prs"]["Forge-AI/monorepo#42"]["checker"]["outcome"].startswith(f"{served.CHECKED} against the words of o1 at ")


def test_a_pr_set_to_serve_an_order_its_job_carries_is_judged_against_that_order_alone_and_its_builders_pushed_is_kept(
    run: Run, with_pr: str, judge: Judge, recorded: Answers, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    grant = "Grant-opened role: each grant opens a role until it expires"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the guards admit a granted session", "--parts", grant)
    with state.edit("demo") as data:
        state.carry(data, "j1", "o2", [grant])
    recorded.shown["3185177"] = DOORS | {"id": "3185177", "title": "How does a grant reach the guards?", "body": f"{grant}; the guards admit it then, never otherwise. One PR"}
    judge.against = [{"order": "o1", "words": "land it", "file": "", "change": "no moved table"}]
    monkeypatch.setattr(served, "judged", lambda name, said, changes: judge.asked.append((said, changes)) or (list(judge.against) if '"land it"' in said else []))
    assert run("pr", "set", PR, "--serves", "o2", "--design", "asked:3185177")[0] == 0
    assert keep(monkeypatch, capsys, "j1", f"PUSHED {PR}\n") == (0, "")
    assert cli.main(["pr", "check", PR, "--drive", "demo"]) == 0
    [(said, _)] = judge.asked
    assert (state.served_orders(state.load("demo"), state.load("demo")["prs"][with_pr]), said.startswith("o2, "), '"land it"' in said) == (["o2"], True, False)
    assert f"this change delivers: {grant}\n" in said and "3185177, " in said
    assert state.load("demo")["jobs"]["j1"]["said"] == f"PUSHED {PR}"
    assert state.load("demo")["prs"][with_pr]["checked"]["orders"] == ["o2"]


def test_a_check_hands_its_outcome_to_the_newer_install_that_saved_meanwhile(
    run: Run, with_pr: str, on_github: dict, judge: Judge, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("pr", "set", PR, "--serves", "o1", "--design", "none")[0] == 0
    handed = []
    newest, version = tmp_path / "newest" / "bin" / "drive", state.VERSION

    def judged(name: str, said: str, changes: dict) -> list[dict]:
        monkeypatch.setattr(state, "VERSION", "0.0.1")
        return []

    real = subprocess.run

    def newer(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[0] != str(newest):
            return real(argv, **kwargs)
        handed.append(argv[1:])
        monkeypatch.setattr(state, "VERSION", version)
        code = cli.main(argv[1:])
        return subprocess.CompletedProcess(argv, code, capsys.readouterr().out, "")

    monkeypatch.setattr(served, "judged", judged)
    monkeypatch.setattr(served.install, "upgraded", lambda: newest)
    monkeypatch.setattr(served.install.subprocess, "run", newer)
    assert cli.main(["pr", "check", PR, "--drive", "demo"]) == 0
    [argv] = handed
    assert (argv[:3], argv[4:], Path(argv[3]).exists()) == (["pr", "checked", state.pr_url(with_pr)], ["--drive", "demo", "--json"], False)
    pr = state.load("demo")["prs"][with_pr]
    assert (pr["checked"]["orders"], pr["checker"]["outcome"].startswith(served.CHECKED)) == (["o1"], True)
    assert capsys.readouterr().out.startswith(f"{with_pr}: {served.CHECKED}")


def test_a_ready_is_judged_by_the_watchs_check_against_the_answers_its_design_line_names(
    run: Run, with_pr: str, on_github: dict, judge: Judge, recorded: Answers, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorded.shown["953a8c8"] = DOORS
    assert keep(monkeypatch, capsys, "j1", f"READY {PR}\nDESIGN: the doors stay open until Env::file_of reads env_id, by 953a8c8\n") == (0, "")
    assert cli.main(["pr", "check", PR, "--drive", "demo"]) == 0
    said = judge.asked[-1][0]
    assert said.startswith('o1, ') and f'asked "{DOORS["title"]}": "Close every door once Env::file_of reads env_id (recommended)' in said


def test_a_ready_or_pushed_whose_design_line_names_no_answer_of_his_keeps_nothing_and_pr_set_refuses_such_a_design(
    run: Run, with_pr: str, on_github: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for first in ("READY", "PUSHED"):
        code, err = keep(monkeypatch, capsys, "j1", f"{first} {PR}\nDESIGN: also start stopped boxes, as built (his pick, 3:24 AM Nov 10)\n")
        assert (code, f"j1: refused, nothing kept: {served.NAMELESS}" in err) == (2, True)
    assert state.report_text("demo", "j1") == ""
    assert keep(monkeypatch, capsys, "j1", f"PUSHED {PR}\nDESIGN: none: it makes no design decision\n") == (0, "")
    with pytest.raises(SystemExit):
        cli.main(["pr", "set", PR, "--serves", "o1", "--design", "asked:his pick, 3:24 AM Nov 10"])
    assert served.NAMES in capsys.readouterr().err
    assert state.load("demo")["prs"][with_pr].get("serves") is None


def test_pr_set_design_replaces_what_a_returns_design_line_cited_and_the_check_reads_only_the_answers_kept_on_the_pr(
    run: Run, with_pr: str, on_github: dict, judge: Judge, recorded: Answers, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorded.shown["953a8c8"] = DOORS
    dropped = "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794357048254809"
    assert keep(monkeypatch, capsys, "j1", f"PUSHED {PR}\nDESIGN: one proposal tool, his tap {dropped}\n") == (0, "")
    assert state.load("demo")["prs"][with_pr]["design"] == f"DESIGN: one proposal tool, his tap {dropped}"
    code, out = run("pr", "set", PR, "--design", "asked:953a8c8")
    assert (code, ", design answers 953a8c8" in out.splitlines()[0], "his answer its design cites, kept: 953a8c8, " in out) == (0, True, True)
    pr = state.load("demo")["prs"][with_pr]
    assert (pr["design"], [answer.partition(", ")[0] for answer in pr["answers"]], pr["checker"]) == ("asked:953a8c8", ["953a8c8"], None)
    monkeypatch.setattr(settled, "shown", lambda answer_id, cwd: pytest.fail(f"read {answer_id} again"))
    monkeypatch.setattr(settled, "heard", lambda link, found, data: pytest.fail(f"read {link}"))
    judge.against = [{"order": "o1", "words": "land it", "change": "api/run.ts keeps the staff flag", "file": "api/run.ts"}]
    assert cli.main(["pr", "check", PR, "--drive", "demo"]) == 0
    judge.against = []
    assert cli.main(["pr", "check", PR, "--drive", "demo"]) == 0
    assert [f'asked "{DOORS["title"]}"' in said and dropped not in said for said, _ in judge.asked] == [True, True]
    assert state.load("demo")["prs"][with_pr]["checked"]["orders"] == ["o1"]


def test_a_drive_saved_before_prs_kept_their_design_cites_on_each_open_pr_the_design_lines_of_its_jobs_newest_ready_or_pushed_naming_it(
    run: Run, with_pr: str
) -> None:
    for at, whole in (
        ("2026-11-11T08:00:00-08:00", f"PUSHED {PR}\nDESIGN: the doors stay open, by 953a8c8\n"),
        ("2026-11-11T09:00:00-08:00", f"WAITING CI on {PR}\nDESIGN: one proposal tool, by 5f75623\n"),
        ("2026-11-11T10:00:00-08:00", f"PUSHED {PR}\nCHANGE: the doors stay open\n"),
    ):
        state.keep_report("demo", "j1", state.opening(whole), whole, state.parse_time(at))
    with state.edit("demo") as data:
        data.pop("designs_cited")
    assert state.load("demo")["prs"][with_pr]["design"] == "DESIGN: the doors stay open, by 953a8c8"
    with state.edit("demo") as data:
        data["prs"][with_pr]["design"] = None
    assert state.load("demo")["prs"][with_pr]["design"] is None


def test_order_set_sorts_an_order_again(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why did the copy stall?", "--kind", "answer")
    assert run("order", "set", "o1", "--kind", "build", "--complex")[0] == 0
    assert [state.load("demo")["orders"]["o1"][field] for field in ("kind", "complex")] == ["build", True]
    run("order", "set", "o1", "--no-complex")
    assert state.load("demo")["orders"]["o1"]["complex"] is False


@pytest.mark.parametrize(
    ("kind", "first", "taken"),
    [
        ("builder", "DONE installed 1.0.76", "done"),
        ("builder", "READY https://github.com/yasyf/cc-skills/pull/1", "waiting"),
        ("builder", "WAITING: CI on #7", None),
        ("design", "DESIGN the table move", "waiting"),
        ("builder", "BLOCKED no order of his names this", "failed"),
        ("builder", "HANDOFF /scratch/j1/left.md", "stopped"),
        ("reader", "The flag exists since 1.0.40", "done"),
        ("builder", "BUILT lr-move, waits on demo:j1-3", "built"),
        ("builder", "I pushed the fix", None),
    ],
)
def test_a_kept_report_sets_a_running_job_from_its_first_line(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, first: str, taken: str | None
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it", "--parts", "ship", "ship again")
    run("job", "add", kind, "--order", "o1", "--part", "ship", "--goal", "ship")
    kept_proof(tmp_path, "j1")
    if first.startswith("READY "):
        run("pr", "add", first.split()[1], "--job", "j1", *LEAD)
    run("job", "add", "builder", "--order", "o1", "--part", "ship" if kind == "design" else "ship again", "--goal", "ship again")
    run("job", "set", "j2", "--state", "stopped")
    rest = DESIGNED.split("\n", 1)[1] if kind == "design" else ""
    for job_id in ("j1", "j2"):
        monkeypatch.setattr("sys.stdin", io.StringIO(f"{first}\n{rest}CHECK: https://github.com/yasyf/cc-skills/actions/runs/12\n"))
        run("job", "report", job_id, "-")
    jobs = state.load("demo")["jobs"]
    assert (jobs["j1"]["state"], jobs["j1"]["said"]) == ((taken, first) if taken else ("running", None))
    assert (jobs["j2"]["state"], jobs["j2"]["said"]) == ("stopped", None)


@pytest.mark.parametrize("branch", ["lr-move", "passive-only-checkpoints, passive-only-checkpoints-next, litestream-passive-only"])
def test_a_built_return_sets_its_job_built_on_its_branches_waiting_on_each_card_it_names(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, branch: str
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    card = "https://acme.slack.com/archives/D123/p1794200000000100"
    monkeypatch.setattr("sys.stdin", io.StringIO(f"BUILT {branch}, waits on demo:j1-3, {card}\nCHECK: build 12\n"))
    run("job", "report", "j1", "-")
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["branch"], job["waits"]) == ("built", branch, ["demo:j1-3", card])


def test_a_report_takes_a_waiting_or_built_job_and_is_refused_on_a_stopped_one_keeping_nothing_until_a_fresh_worker_runs_it(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    check = "CHECK: https://github.com/yasyf/cc-skills/actions/runs/12\n"
    built = f"BUILT lr-move, waits on demo:j1-3\n{check}"
    assert keep(monkeypatch, capsys, "j1", built) == (0, "")
    assert keep(monkeypatch, capsys, "j1", f"WAITING on his card demo:j1-3\n{check}") == (0, "")
    assert state.load("demo")["jobs"]["j1"]["state"] == "waiting"
    done = "DONE installed 1.0.392\nCHECK: https://github.com/yasyf/cc-skills/actions/runs/12\n"
    assert keep(monkeypatch, capsys, "j1", done) == (0, "")
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("done", "DONE installed 1.0.392")
    run("job", "set", "j1", "--state", "stopped", "--said", "handed to j2")
    code, err = keep(monkeypatch, capsys, "j1", "DONE The lane comes from bookkeeping\nCHECK: https://github.com/yasyf/cc-skills/actions/runs/13\n")
    assert (code, "j1: refused, nothing kept: j1 is stopped, so its work goes on in another job" in err) == (2, True)
    assert [reply["body"] for reply in state.returns("demo", "j1")] == [done, f"WAITING on his card demo:j1-3\n{check}", built]
    assert state.load("demo")["jobs"]["j1"]["state"] == "stopped"
    run("job", "set", "j1", "--agent", "a-7")
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    assert keep(monkeypatch, capsys, "j1", "DONE The lane comes from bookkeeping\nCHECK: https://github.com/yasyf/cc-skills/actions/runs/13\n") == (0, "")
    assert state.load("demo")["jobs"]["j1"]["state"] == "done"


def test_a_waiting_line_names_what_frees_it_and_each_pr_and_release_it_names_is_a_wait(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    run("pr", "add", PR, "--job", "j1", *LEAD)
    code, err = keep(monkeypatch, capsys, "j1", "WAITING release: #54575 merged after the newest release was cut\n")
    assert (code, "j1: refused, nothing kept: its WAITING names nothing the watch frees it on: name the release as release:<service>" in err) == (2, True)
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    assert keep(monkeypatch, capsys, "j1", "WAITING release:api:Forge-AI/monorepo#42 and release:api:Forge-AI/monorepo#9\n") == (0, "")
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["waits"]) == ("waiting", ["release:api:Forge-AI/monorepo#42"])


def test_a_waiting_whose_first_line_names_no_link_waits_on_each_pr_and_release_its_body_names_as_j1485s_did(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "read each table's stored slot")
    run("order", "add", "--title", "Other title", "--project", "The drive", "--workstream", "Drive tooling", "ship the SandSQL slots")
    run("job", "add", "builder", "--order", "o1", "--goal", "read the slots")
    run("job", "add", "builder", "--order", "o2", "--goal", "ship the slots")
    keys = [f"Forge-AI/monorepo#{number}" for number in (54607, 54665, 54680)]
    for key in keys:
        run("pr", "add", state.pr_url(key), "--job", "j2", *LEAD)
    report = (
        "WAITING change (b) of j660 needs Env::file_of to read each table's stored slot; that change follows three open SandSQL PRs and the release carrying them\n"
        f"It follows {state.pr_url(keys[0])}, {state.pr_url(keys[1])} and {state.pr_url(keys[2])}.\n"
        f"A SandSQL release ships carrying release:sandsql:{keys[2]}.\n"
    )
    assert keep(monkeypatch, capsys, "j1", report) == (0, "")
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["waits"]) == ("waiting", [f"release:sandsql:{keys[2]}", *keys])


def test_job_set_refuses_any_state_but_stopped_changing_nothing(run: Run, demo: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship", "--agent", "a-1")
    run("job", "set", "j1", "--said", "DONE The lane comes from bookkeeping")
    for taken in ("waiting", "built", "done", "failed"):
        assert cli.main(["job", "set", "j1", "--state", taken, "--said", f"asked {taken}"]) == 2
        assert capsys.readouterr().err == (
            "drive: j1: refused, nothing changed: --state sets only stopped, for a job that ends unfinished, and done for a design job a"
            " builder carries, which ends on its own once drive job add builder --part or drive job set <builder> --part names its part or a"
            " change its DESIGN listed; every other state comes from the job's return: pipe it into `drive job report j1 -`, its first line"
            " `DONE The lane comes from bookkeeping`\n"
        )
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("running", "DONE The lane comes from bookkeeping")
    assert run("job", "set", "j1", "--state", "stopped")[0] == 0
    assert state.load("demo")["jobs"]["j1"]["state"] == "stopped"


def test_a_design_job_ends_once_the_builder_its_return_or_his_pick_adds_takes_its_part_or_one_of_its_changes(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy one store", "--options-only")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store copy")
    assert keep(monkeypatch, capsys, "j1", DESIGNED) == (0, "")
    assert cli.main(["job", "set", "j1", "--state", "done"]) == 2
    assert "drive: j1: refused, nothing changed: --state sets only stopped" in capsys.readouterr().err
    assert run("order", "set", "o1", "--no-options-only")[1].splitlines()[-1] == 'j2 builder on "Short title": start its agent now'
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store list")
    assert keep(monkeypatch, capsys, "j3", DESIGNED) == (0, "")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "move the tables", "--parts", "move the tables", "the old copy")
    run("job", "add", "design", "--order", "o2", "--part", "move the tables", "--goal", "cards: the moved tables")
    run("job", "add", "builder", "--order", "o2", "--part", "the old copy", "--goal", "drop the old copy")
    assert keep(monkeypatch, capsys, "j4", DESIGNED_IN_CHANGES) == (0, "")
    jobs = state.load("demo")["jobs"]
    assert {job_id: (jobs[job_id]["state"], jobs[job_id]["said"]) for job_id in ("j1", "j3", "j4")} == {
        "j1": ("done", "carried by builder j2"),
        "j3": ("done", "carried by builder j2"),
        "j4": ("done", "carried by builder j6"),
    }
    assert run("job", "set", "j4", "--state", "done")[1].splitlines()[0] == "j4 is already done"


def test_job_set_naming_the_state_a_job_already_holds_changes_nothing_and_says_so(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "reader", "--order", "o1", "--goal", "read where it ships")
    monkeypatch.setattr("sys.stdin", io.StringIO("DONE it ships from the release branch\n"))
    assert run("job", "report", "j1", "-")[0] == 0
    code, out = run("job", "set", "j1", "--state", "done", "--said", "read")
    assert (code, out.splitlines()[0]) == (0, "j1 is already done")
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("done", "read")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship the rest")
    assert run("job", "set", "j2", "--state", "running")[1].splitlines()[0] == "j2 is already running"


def test_job_report_prints_each_result_a_remote_lane_sent_newest_first_and_none_of_its_other_messages(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship", "--remote")
    sent = [
        ("worker_done", "DONE installed 1.0.75", "DONE installed 1.0.75\nCHECK: drive --version"),
        ("status", "building 1.0.76", "building 1.0.76"),
        ("question", "LOG https://buildkite.com/forge/test/builds/1", "LOG https://buildkite.com/forge/test/builds/1"),
        ("question", "DONE installed 1.0.76", "DONE installed 1.0.76\nCHECK: drive --version"),
    ]
    for hour, (kind, subject, body) in enumerate(sent):
        state.keep_reply("demo", "j1", {"created_at": f"2026-11-12T0{hour}:00:00+00:00", "id": f"msg_{hour}", "type": kind, "subject": subject, "body": body})
    earlier = state.EARLIER_RETURN.format(at=state.stamp(state.parse_time("2026-11-12T00:00:00+00:00").astimezone()))
    assert run("job", "report", "j1", "--all") == (0, f"DONE installed 1.0.76\nCHECK: drive --version\n\n{earlier}\nDONE installed 1.0.75\nCHECK: drive --version")


@pytest.fixture
def with_pr(run: Run, demo: str) -> str:
    run("order", "add", "--title", "land it", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    run("pr", "add", PR + "/files", "--job", "j1", *LEAD)
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


def test_pr_add_waits_on_his_tap_unless_it_is_a_port_exempt_drive_tooling_outside_forge_ai_or_untapped_for_a_reason(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    key = "Forge-AI/monorepo#42"
    assert cli.main(["pr", "add", PR, "--job", "j1", "--no-tap", *LEAD]) == 2
    assert "--no-tap needs --why <reason>" in capsys.readouterr().err
    assert cli.main(["pr", "add", PR, "--job", "j1", "--no-tap", "--why", "a teammate lands it", *LEAD]) == 2
    assert "only his tap or an exempt class lands a Forge-AI PR" in capsys.readouterr().err
    assert key not in state.load("demo")["prs"]
    assert run("pr", "add", "https://github.com/yasyf/cc-skills/pull/8", "--job", "j1", "--no-tap", "--why", "a teammate lands it", *LEAD)[0] == 0
    assert run("pr", "add", PR, "--job", "j1", "--exempt", "test-only", *LEAD)[0] == 0
    prs = state.load("demo")["prs"]
    assert (prs["yasyf/cc-skills#8"]["wants_tap"], stage.carded(prs["yasyf/cc-skills#8"])) == (False, False)
    assert (prs[key]["wants_tap"], prs[key]["exempt"], stage.carded(prs[key])) == (True, "test-only", False)
    with state.edit("demo") as data:
        data["prs"].pop(key)
        data["orders"]["o1"]["text"] = "Drive tooling: the watch draws his card"
    assert run("pr", "add", PR, "--job", "j1", *LEAD)[0] == 0
    assert run("pr", "add", "https://github.com/yasyf/cc-skills/pull/7", "--job", "j1", *LEAD)[0] == 0
    prs = state.load("demo")["prs"]
    assert (prs[key]["wants_tap"], prs["yasyf/cc-skills#7"]["wants_tap"]) == (True, False)


def test_pr_add_refuses_a_new_pr_of_a_job_on_a_surface_until_its_proof_file_holds_the_user_stories_criteria(
    run: Run, demo: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "show the queue")
    run("job", "add", "builder", "--order", "o1", "--goal", "draw the queue")
    run(
        "job", "brief", "j1", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main",
        "--native", "the dashboard's queue", "--interactions", "press j and the next card opens",
    )
    assert cli.main(["pr", "add", PR, "--job", "j1"]) == 2
    assert capsys.readouterr().err.startswith(
        "drive: Forge-AI/monorepo#42: refused, nothing added: j1 builds something he sees (the dashboard's queue), so each user story's"
        " strict acceptance criteria come before the build"
    )
    assert "Forge-AI/monorepo#42" not in state.load("demo")["prs"]
    kept_proof(tmp_path, "j1")
    assert run("pr", "add", PR, "--job", "j1", *LEAD)[0] == 0


def test_pr_add_refuses_a_pr_his_card_asks_his_tap_on_until_its_lead_says_why_what_and_downstream_keeps_it_and_redraws_his_card_when_it_changes(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str], draws: Draws, redraws: Redraws
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    key = "Forge-AI/monorepo#42"
    assert cli.main(["pr", "add", PR, "--job", "j1", "--headline", "Uploads survive a flaky network", "--because", "upload.Retry dropped files"]) == 2
    refusal = capsys.readouterr().err
    assert f"{key}: refused, nothing added: his card opens with its lead, and --because names upload.Retry;" in refusal
    assert "--does is missing; --downstream is missing" in refusal
    assert (key in state.load("demo")["prs"], draws.keys) == (False, [])
    assert run("pr", "add", "https://github.com/yasyf/cc-skills/pull/8", "--job", "j1", "--no-tap", "--why", "a teammate lands it")[0] == 0
    assert run("pr", "add", PR, "--job", "j1", *LEAD) == (0, f"{key}, his card {draws.link}")
    lead = dict(zip(LEAD[::2], LEAD[1::2], strict=True))
    assert state.load("demo")["prs"][key]["lead"] == {line: lead[flag] for line, flag in limits.LEAD_LINES}
    assert run("pr", "add", PR, "--job", "j1") == (0, key)
    assert run("pr", "add", PR, "--job", "j1", "--downstream", "Builders also rewrite the lead when the change moves") == (0, f"{key}, his card redrawn with its new lead")
    assert (state.load("demo")["prs"][key]["lead"]["why"], state.load("demo")["prs"][key]["lead"]["downstream"], redraws.asked) == (
        lead["--because"],
        "Builders also rewrite the lead when the change moves",
        [PR],
    )


def test_pr_add_and_set_refuse_a_pr_someone_else_opened_unless_the_order_it_serves_names_it(
    run: Run, demo: str, on_github: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    key = "Forge-AI/monorepo#42"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    on_github["author"] = {"login": "rowan", "is_bot": False}
    assert cli.main(["pr", "add", PR, "--job", "j1", *LEAD]) == 2
    assert f"{key}: refused, nothing added: {state.foreign(key, 'rowan', [])}" in capsys.readouterr().err
    assert key not in state.load("demo")["prs"]
    with state.edit("demo") as data:
        data["orders"]["o1"]["text"] = "land #42 once it is green"
    assert run("pr", "add", PR, "--job", "j1", *LEAD)[0] == 0
    assert state.load("demo")["prs"][key]["author"] == "rowan"
    with state.edit("demo") as data:
        data["orders"]["o1"]["text"] = "land it"
    assert cli.main(["pr", "set", PR, "--exempt", "test-only"]) == 2
    assert f"{key}: refused, nothing set: {state.foreign(key, 'rowan', [])}" in capsys.readouterr().err
    assert state.load("demo")["prs"][key]["exempt"] is None


def test_pr_add_takes_a_pr_someone_else_opened_once_he_answers_a_question_card_of_its_order_linking_it_above_its_findings(
    run: Run, demo: str, on_github: dict, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = "Forge-AI/monorepo#42"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land the fix he approved")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    on_github["author"] = {"login": "rowan", "is_bot": False}
    asked = tmp_path / "j1-c960362c.md"
    asked.write_text(f"ASK Should the drive merge the teammate's fix?\nFor {{order:o1}}: your pick decides whether it lands.\nFindings:\n- {PR}: green\n")
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"].append(
            state.new_question(str(asked), "Should the drive merge the teammate's fix?", state.stamp(), card=slack_card(1), closed=state.new_close("answered", "Merge it", slack_card(1)))
        )
    assert cli.main(["pr", "add", PR, "--job", "j1", *LEAD]) == 2
    assert f"{key}: refused, nothing added: {state.foreign(key, 'rowan', [])}" in capsys.readouterr().err
    asked.write_text(f"ASK Should the drive merge the teammate's fix?\nFor {{order:o1}}: your pick decides whether <{PR}|the teammate's fix> lands.\nFindings:\n")
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = None
    assert cli.main(["pr", "add", PR, "--job", "j1", *LEAD]) == 2
    assert f"{key}: refused, nothing added: {state.foreign(key, 'rowan', [])}" in capsys.readouterr().err
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("answered", "Merge it", slack_card(1))
    assert run("pr", "add", PR, "--job", "j1", *LEAD)[0] == 0
    assert state.foreign_pr(state.load("demo"), key) is None


def test_pr_add_refuses_a_pr_another_projects_job_holds_from_a_sub_coordinator_or_a_sessionless_sprite_lane_and_its_opener_takes_it_back(
    run: Run, demo: str, on_github: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url, key = "https://github.com/Forge-AI/monorepo/pull/53447", "Forge-AI/monorepo#53447"
    on_github["url"] = url
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Agents", "agents wake on their inbox", "--parts", "wake", "take over")
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Agents", "the agent's chat lists under its env")
    run("job", "add", "builder", "--order", "o1", "--part", "wake", "--goal", "wake agents on their inbox")
    run("job", "add", "builder", "--order", "o2", "--goal", "list the agent's chat")
    run("job", "add", "builder", "--order", "o1", "--part", "take over", "--goal", "take over the inbox wake")
    run("pr", "add", url, "--job", "j1", *LEAD)
    for project, session in (("Platform", "platform-1"), ("Iris", "iris-1")):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
        run("start", "demo", "--role", "coordinator", "--project", project)
    held = state.pr_held_elsewhere(state.load("demo"), key, "j2")
    assert held is not None
    assert held.startswith("j1 holds it for o1, the order of the sub-coordinator for Platform, session platform-1, while j2 is on o2, which the sub-coordinator for Iris holds:")
    assert held.endswith(f'put j2\'s change up as a PR of its own stacked on {key}\'s branch, or send the sub-coordinator for Platform the step as a handoff record on the drive\'s record tool, to "platform" and attrs {{"order": "o1"}}')
    for session in ("iris-1", None):
        if session:
            monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
        else:
            monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
        assert cli.main(["pr", "add", url, "--job", "j2", "--drive", "demo", *LEAD]) == 2
        assert f"{key}: refused, nothing added: {held}" in capsys.readouterr().err
    data = state.load("demo")
    assert (data["prs"][key]["job"], data["prs"][key]["order"], data["jobs"]["j2"]["prs"]) == ("j1", "o1", [])
    for session in ("iris-1", "session-under-test"):
        assert "o1 is the sub-coordinator for Platform's order, session platform-1:" in (state.held_elsewhere(data, "o1", session) or "")
    assert state.held_elsewhere(data, "o1", "platform-1") is None
    with state.edit("demo") as data:
        data["prs"][key].update(job="j2", order="o2")
        data["jobs"]["j2"]["prs"].append(url)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "platform-1")
    assert cli.main(["pr", "add", url, "--job", "j3", *LEAD]) == 2
    assert "j2 holds it for o2, the order of the sub-coordinator for Iris, session iris-1, while j3 is on o1" in capsys.readouterr().err
    assert run("pr", "add", url, "--job", "j1", *LEAD)[0] == 0
    assert (state.load("demo")["prs"][key]["job"], state.load("demo")["prs"][key]["order"]) == ("j1", "o1")
    assert run("pr", "add", url, "--job", "j3", *LEAD)[0] == 0
    assert state.load("demo")["prs"][key]["job"] == "j3"


def test_a_yasyf_pr_reads_ready_for_the_lander_once_its_builders_ready_is_taken_waiting_while_a_pr_it_waits_on_is_open_and_red_once_a_check_fails_and_no_report_or_pr_add_starts_one(
    run: Run, meta: str, tmp_path: Path, landers: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    key, url = "yasyf/cc-skills#7", "https://github.com/yasyf/cc-skills/pull/7"
    assert run("pr", "add", url, "--job", "j1", *LEAD) == (0, key)
    with state.edit("demo") as data:
        data["prs"][key]["head"] = "abc1234"
    assert stage.of(state.load("demo"), key, view.NO_CARDS).name == "grading"
    kept_proof(tmp_path, "j1")
    monkeypatch.setattr("sys.stdin", io.StringIO(f"READY {url}\n"))
    assert (run("job", "report", "j1", "-")[0], run("pr", "add", url, "--job", "j1", *LEAD), landers) == (0, (0, key), [])
    ready = stage.Stage("ready", stage.LANDER, "its builder reported it and no check fails at abc1234, so the lander merges it with --admin while any still run")
    assert stage.of(state.load("demo"), key, view.NO_CARDS) == ready
    waited = "Forge-AI/monorepo#42"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: guard it")
    run("job", "add", "builder", "--order", "o2", "--goal", "guard")
    assert run("pr", "add", PR, "--job", "j2", *LEAD)[0] == 0
    assert run("pr", "set", url, "--waits", waited)[0] == 0
    assert stage.of(state.load("demo"), key, view.NO_CARDS) == stage.Stage("waiting", stage.WAITED, f"it waits on {waited}, and the watch frees it once each ends")
    with state.edit("demo") as data:
        data["prs"][waited]["state"] = "merged"
    assert stage.of(state.load("demo"), key, view.NO_CARDS) == ready
    with state.edit("demo") as data:
        data["prs"][key].update(checks="fail", failed_checks=["test"])
    assert stage.of(state.load("demo"), key, view.NO_CARDS) == stage.Stage("red", stage.BUILDER, "its checks fail at abc1234: test")


def test_pr_add_of_a_port_takes_the_pure_port_exemption_and_no_tap(run: Run, demo: str, on_github: dict) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    on_github["title"] = "Port: land it"
    assert run("pr", "add", PR, "--job", "j1", "--port-of", "51234", *LEAD)[0] == 0
    pr = state.load("demo")["prs"]["Forge-AI/monorepo#42"]
    assert (pr["port_of"], pr["exempt"], pr["wants_tap"], stage.carded(pr)) == (51234, "pure port", False, False)


def test_pr_add_on_a_public_repo_takes_demo_pictures_and_draws_his_card_in_the_same_call(
    run: Run, demo: str, on_github: dict, visibilities: dict, draws: Draws, images: Images
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    visibilities.update({"Forge-AI/monorepo": "public", "yasyf/cc-skills": "public"})
    on_github["body"] = (
        "## Show me\n<img src=\"https://raw.githubusercontent.com/yasyf/cc-skills/73f9efa/public/board.png\">\n"
        "![Hover](https://github.com/yasyf/cc-skills/blob/73f9efa/pr-shots/demo-hover.png?raw=true)\n"
        "Follows yasyf/cc-skills#4146."
    )
    assert run("pr", "add", PR, "--job", "j1", *LEAD) == (0, f"Forge-AI/monorepo#42, his card {draws.link}")
    assert (draws.keys, view.tap_card(state.load("demo")["prs"]["Forge-AI/monorepo#42"], view.live_cards("demo"))) == ([["Forge-AI/monorepo#42"]], draws.link)


def test_pr_add_whose_card_draw_fails_registers_the_pr_uncarded_for_the_watch_to_draw(run: Run, demo: str, draws: Draws) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    draws.failure = "cc-slack exited 1"
    assert run("pr", "add", PR, "--job", "j1", *LEAD) == (0, "Forge-AI/monorepo#42, his card not drawn yet, so the watch draws it on its next read: cc-slack exited 1")
    data = state.load("demo")
    assert stage.of(data, "Forge-AI/monorepo#42", view.NO_CARDS).name == "uncarded"


def test_a_builders_pushed_naming_a_pr_the_drive_does_not_track_registers_it_on_that_builder(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, draws: Draws, tmp_path: Path
) -> None:
    url = "https://github.com/yasyf/cc-skills/pull/7"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    kept_proof(tmp_path, "j1")
    monkeypatch.setattr("sys.stdin", io.StringIO(f"PUSHED {url}\n"))
    code, out = run("job", "report", "j1", "-")
    assert (code, out.splitlines()[1]) == (0, "yasyf/cc-skills#7: registered on j1")
    pr = state.load("demo")["prs"]["yasyf/cc-skills#7"]
    assert (pr["job"], draws.keys) == ("j1", [])


def test_a_builders_pushed_naming_an_untracked_pr_his_card_would_open_with_no_lead_is_refused_until_drive_pr_add_keeps_one(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], draws: Draws
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    monkeypatch.setattr("sys.stdin", io.StringIO(f"PUSHED {PR}\n"))
    assert cli.main(["job", "report", "j1", "-"]) == 2
    refusal = capsys.readouterr().err
    assert "drive pr add refuses a PR its first line names: Forge-AI/monorepo#42: refused, nothing added: his card opens with its lead, and --headline is missing" in refusal
    assert f'drive pr add {PR} --job j1 --headline "<headline>" --because "<why>" --does "<what>" --downstream "<downstream>"' in refusal
    assert ("Forge-AI/monorepo#42" in state.load("demo")["prs"], draws.keys) == (False, [])
    assert run("pr", "add", PR, "--job", "j1", *LEAD) == (0, f"Forge-AI/monorepo#42, his card {draws.link}")
    monkeypatch.setattr("sys.stdin", io.StringIO(f"PUSHED {PR}\n"))
    assert (run("job", "report", "j1", "-")[0], draws.keys) == (0, [["Forge-AI/monorepo#42"]])


def test_pr_add_again_keeps_stored_state(run: Run, with_pr: str) -> None:
    with state.edit("demo") as data:
        data["prs"][with_pr]["checks"] = "pass"
    run("pr", "add", PR, "--job", "j1", *LEAD)
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
    assert out.endswith(f"\ndropped the Hold the drive gave itself ({source}): the release carrying its reader is live\nredrawing in place the 1 open card(s) of his carrying it, to drop the Hold the drive gave itself")
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


def test_an_exempt_class_its_files_are_outside_is_refused_by_the_landers_class_check_before_pr_add_or_pr_set_keeps_it(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], redraws: Redraws
) -> None:
    run("order", "add", "--title", "land it", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    paths = ["go/sand/next/cmd/runtime-api/followed_reads_test.go", "go/sand/next/internal/sandsql/sidecar/sidecartest/run.go"]
    diff = "diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+b\n"
    monkeypatch.setattr(served, "change", lambda key: {"title": "Fix: follow reads", "body": "", "head": "abc", "diff": "".join(diff.format(path=path) for path in paths).encode()})
    refused = f'it is no "test-only": it changes {paths[1]}, outside test and fixture code, by the class check the lander runs before it lands an exempt PR'
    assert cli.main(["pr", "add", PR, "--job", "j1", "--exempt", "test-only", *LEAD]) == 2
    assert f"Forge-AI/monorepo#42: refused, nothing added: {refused}" in capsys.readouterr().err
    assert "Forge-AI/monorepo#42" not in state.load("demo")["prs"]
    assert run("pr", "add", PR, "--job", "j1", *LEAD)[0] == 0
    assert cli.main(["pr", "set", PR, "--exempt", "test-only"]) == 2
    assert f"Forge-AI/monorepo#42: refused, nothing set: {refused}" in capsys.readouterr().err
    assert cli.main(["pr", "set", PR, "--exempt", "ci-only"]) == 2
    assert f'it is no "ci-only": it changes {", ".join(paths)}, outside CI configuration and CI tooling' in capsys.readouterr().err
    pr = state.load("demo")["prs"]["Forge-AI/monorepo#42"]
    assert (pr["exempt"], state.asks_tap(pr), redraws.asked) == (None, True, [])
    paths.pop()
    assert run("pr", "set", PR, "--exempt", "test-only")[0] == 0
    assert state.load("demo")["prs"]["Forge-AI/monorepo#42"]["exempt"] == "test-only"


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
    run("incident", "open", "table moves rerun on every write", "--no-rollback", *FILED)
    run("job", "add", "builder", "--order", "o1", "--part", "stop", "--goal", "ship the stop")
    run("pr", "add", PR, "--job", "j4", *LEAD)
    on_github.update(title="Fix: table moves stop rerunning on every write", body=CARD_BODY)
    assert run("pr", "set", PR, "--exempt", "incident fix")[0] == 0
    assert state.load("demo")["prs"]["Forge-AI/monorepo#42"]["exempt"] == "incident fix"


def test_pr_set_never_leaves_a_forge_ai_pr_with_neither_his_tap_nor_an_exempt_class(
    run: Run, with_pr: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["pr", "set", PR, "--no-tap", "--why", "not his order"]) == 2
    assert "only his tap or an exempt class lands a Forge-AI PR" in capsys.readouterr().err
    assert state.load("demo")["prs"][with_pr]["wants_tap"] is True
    run("pr", "set", PR, "--exempt", "test-only")
    assert run("pr", "set", PR, "--no-tap", "--why", "its class lands it")[0] == 0
    assert cli.main(["pr", "set", PR, "--no-exempt"]) == 2
    assert state.load("demo")["prs"][with_pr]["exempt"] == "test-only"
    run("pr", "set", PR, "--fundamental", "now drops the old column")
    pr = state.load("demo")["prs"][with_pr]
    assert (pr["wants_tap"], pr["exempt"]) == (True, None)


def test_pr_set_tap_takes_a_pr_whatever_the_size_of_its_stack(run: Run, with_pr: str, native_stacks: dict[str, list[tuple[str, str]]]) -> None:
    chain = [f"Forge-AI/monorepo#{n}" for n in (42, 43, 44, 45, 46, 47, 48)]
    with state.edit("demo") as data:
        for key in chain[1:]:
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1")
        for key in chain:
            data["prs"][key]["stack"] = chain
    for key in chain:
        native_stacks[key] = [(member, "open") for member in chain]
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")[0] == 0
    assert state.load("demo")["prs"][with_pr]["wants_tap"] is True


def test_pr_set_serves_or_design_from_a_session_not_holding_the_prs_order_is_refused_naming_its_holder(
    run: Run, with_pr: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with state.edit("demo") as data:
        data["orders"]["o1"]["owner"] = "communicator"
    for flags in (["--serves", "o1", "--design", "none"], ["--design", "none"]):
        assert cli.main(["pr", "set", PR, *flags]) == 2
        assert "o1 is the communicator's order, session not started: only the communicator adds its jobs, starts its agents, checks its PRs and posts its reports" in capsys.readouterr().err
    assert (state.load("demo")["prs"][with_pr].get("serves"), state.load("demo")["prs"][with_pr].get("design")) == (None, None)
    with state.edit("demo") as data:
        data["orders"]["o1"]["owner"] = "coordinator"
    assert run("pr", "set", PR, "--serves", "o1", "--design", "none")[0] == 0

def test_pr_set_serves_names_the_order_it_serves_and_prints_his_words(run: Run, with_pr: str) -> None:
    assert run("pr", "set", PR, "--tap", "--serves", "o9", "--design", "none")[0] == 2
    assert state.load("demo")["prs"][with_pr].get("serves") is None
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
    assert run("pr", "set", PR, "--tap", "--serves", "o1")[0] == 2
    assert state.load("demo")["prs"][with_pr].get("serves") is None
    for unsaid in ("maybe", "asked:", "asked: "):
        with pytest.raises(SystemExit):
            run("pr", "set", PR, "--tap", "--serves", "o1", "--design", unsaid)
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "asked:045b08d")[0] == 0
    pr = state.load("demo")["prs"][with_pr]
    assert (pr["wants_tap"], pr["design"]) == (True, "asked:045b08d")
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")[0] == 0
    assert state.load("demo")["prs"][with_pr]["design"] == "none"


def test_pr_set_tap_draws_a_body_with_no_releasable_section_and_a_step_after_the_release(
    run: Run, with_pr: str, on_github: dict
) -> None:
    on_github["body"] = CARD_BODY + "## Risk\nThen run the fill from a pass box once it ships.\n"
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")[0] == 0


def test_pr_set_tap_serving_a_drive_tooling_order_needs_no_words_of_his_and_only_the_meta_session_sets_it(
    run: Run, meta: str, on_github: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: a tooling PR lands on his Approve")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    run("pr", "add", PR, "--job", "j1", *LEAD)
    on_github["body"] = CARD_BODY.split("## His words")[0] + "## His words, step by step\nNone: drive tooling.\n"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "elsewhere")
    assert cli.main(["pr", "set", PR, "--tap", "--serves", "o1", "--design", "none"]) == 2
    assert capsys.readouterr().err.strip() == (
        'drive: Forge-AI/monorepo#42 --serves o1: refused, o1 is a "Drive tooling:" order, which only the meta session acts on:'
        " send a defect record to meta@demo naming what it needs, and the meta session's builder takes it over"
    )
    assert state.load("demo")["prs"]["Forge-AI/monorepo#42"].get("serves") is None
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-under-test")
    code, out = run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")
    assert code == 0
    assert out.splitlines()[-1] == "serves o1, his words: Drive tooling: a tooling PR lands on his Approve"
    pr = state.load("demo")["prs"]["Forge-AI/monorepo#42"]
    assert (pr["wants_tap"], pr["serves"]) == (True, "o1")


def test_a_tooling_orders_job_or_pr_never_carries_or_serves_his_feature_order_nor_the_reverse(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: the ask card redraws")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: fix ask")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "elsewhere")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "show the run page")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-under-test")
    run("job", "add", "builder", "--order", "o1", "--goal", "redraw it")
    run("pr", "add", PR, "--job", "j1", *LEAD)
    crossed = "o1 is a drive tooling order and o3 is an order of his feature work, and one job or PR never carries both: start a job on o3 with drive job add <kind> --order o3"
    assert cli.main(["job", "set", "j1", "--carries", "o3"]) == 2
    assert capsys.readouterr().err.strip() == f"drive: j1 --carries o3: refused, {crossed}"
    assert cli.main(["pr", "set", PR, "--serves", "o3"]) == 2
    assert capsys.readouterr().err.strip() == f"drive: Forge-AI/monorepo#42 --serves o3: refused, {crossed}"
    assert state.load("demo")["jobs"]["j1"]["carries"] == {}
    assert run("job", "set", "j1", "--carries", "o2")[0] == 0
    assert state.load("demo")["jobs"]["j1"]["carries"] == {"o2": ["Short title"]}
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "elsewhere")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "land the page")
    run("job", "add", "builder", "--order", "o4", "--goal", "land it")
    assert cli.main(["job", "set", "j2", "--carries", "o2"]) == 2
    assert "j2 --carries o2: refused, o2 is a \"Drive tooling:\" order, which only the meta session acts on: send a defect record to meta@demo" in capsys.readouterr().err


def test_pr_set_tap_accepts_a_body_with_every_section_his_card_needs(run: Run, with_pr: str) -> None:
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")[0] == 0
    assert state.load("demo")["prs"][with_pr]["wants_tap"] is True


def test_pr_set_tap_on_an_exempt_pr_draws_no_card_so_checks_none(run: Run, with_pr: str, on_github: dict) -> None:
    run("pr", "set", PR, "--exempt", "test-only")
    on_github["body"] = "## Summary\nTests only."
    assert run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "none")[0] == 0


def test_a_port_changing_how_its_data_is_stored_is_no_pure_port(run: Run, demo: str, on_github: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    on_github["title"] = "Port: land it"
    table = "api/migrations/0042_site_visuals.sql"
    diff = f"diff --git a/{table} b/{table}\n--- /dev/null\n+++ b/{table}\n@@ -0,0 +1 @@\n+create table site_visuals (key text, value text);\n"
    monkeypatch.setattr(served, "change", lambda key: {"title": "Port: land it", "head": "abc", "diff": diff.encode()})
    assert cli.main(["pr", "add", PR, "--job", "j1", "--port-of", "51234", *LEAD]) == 2
    refused = capsys.readouterr().err
    assert f"it changes {table}, how its data is stored, so it is no pure port of #51234" in refused and "modelng the data" in refused
    assert "Forge-AI/monorepo#42" not in state.load("demo")["prs"]
    monkeypatch.setattr(served, "change", lambda key: {"title": "Port: land it", "head": "abc", "diff": diff.replace(table, "dashboard/src/visuals.ts").encode()})
    assert run("pr", "add", PR, "--job", "j1", "--port-of", "51234", *LEAD)[0] == 0


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
    assert "no tap needed, " in out
    assert "exempt: pure port of #51234; grading, owed by Buildkite and forge-pr-reviewer: " in out
    assert withdrawals.asked == [(PR, "port of #51234, which lands with no tap")]
    assert run("pr", "tapped", PR) == (0, f"{with_pr}: exempt: pure port")
    assert run("pr", "set", PR, "--port-of", "51234", "--tap", "--serves", "o1", "--design", "none")[0] == 2
    with pytest.raises(SystemExit):
        run("pr", "set", PR, "--port-of", "51234", "--exempt", "test-only")


def test_pr_set_no_tap_withdraws_his_open_card_showing_why(
    run: Run, with_pr: str, withdrawals: "Withdrawals", monkeypatch: pytest.MonkeyPatch
) -> None:
    card = "https://acme.slack.com/archives/D123/p1794286637698859"
    run("pr", "set", PR, "--exempt", "test-only")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "move the strip")
    run("job", "add", "builder", "--order", "o1", "--goal", "move it")
    on_github["title"] = "move strip"
    assert cli.main(["pr", "add", PR, "--job", "j1", *LEAD]) == 2
    assert capsys.readouterr().err.strip() == (
        'drive: Forge-AI/monorepo#42: refused, its title "move strip" does not open Fix:, New:, Cleanup: or Port:, the kind of change'
        ' his card names it by: retitle it with gh pr edit 42 -R Forge-AI/monorepo --title "<Fix|New|Cleanup|Port>: move strip", then run this again'
    )
    assert state.load("demo")["prs"] == {}
    for title in ("Port: move strip", "store: 🐛 move strip", "fix(store): move strip"):
        on_github["title"] = title
        assert run("pr", "add", PR, "--job", "j1", *LEAD)[0] == 0


def test_pr_add_adds_a_body_whose_shown_sections_speak_of_him_as_he_or_his_with_a_note(run: Run, demo: str, on_github: dict, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Land it", "--project", "The drive", "--workstream", "Releases", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land it")
    on_github["body"] = CARD_BODY.replace("once your tap comes in", "once his tap comes in")
    assert cli.main(["pr", "add", PR, "--job", "j1", *LEAD]) == 0
    assert (
        'Forge-AI/monorepo#42: note, added all the same: its card shows him these sections, which may speak of him as he or his: summary: "`land.py` lands the PR once his tap comes in.".'
        ' Where these mean him, say you and your, as in "Your Nov 11 words narrowed it", and edit the body with gh pr edit 42 -R Forge-AI/monorepo --body-file <file>;'
        " where they mean someone else, leave them"
    ) in capsys.readouterr().err.splitlines()
    assert "Forge-AI/monorepo#42" in state.load("demo")["prs"]
    on_github["body"] = CARD_BODY.replace("once your tap comes in", 'once your tap comes in, as `he` and "his tap" say')
    assert cli.main(["pr", "add", PR, "--job", "j1", *LEAD]) == 0
    assert "may speak of him" not in capsys.readouterr().err


def test_pr_add_reads_a_capitalised_he_a_word_precedes_as_a_name(run: Run, demo: str, on_github: dict, images: Images, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Show the timeline", "--project", "The drive", "--workstream", "Releases", "show the timeline")
    run("job", "add", "builder", "--order", "o1", "--goal", "show it")
    on_github["body"] = CARD_BODY + (
        "\n## Show me\n![The timeline beside Grace Hopper's and Wren He's entries](https://example.com/after.png)\n"
        "\n| who | review |\n| --- | --- |\n| Wren He | approved |\n"
    )
    assert cli.main(["pr", "add", PR, "--job", "j1", *LEAD]) == 0
    assert "may speak of him" not in capsys.readouterr().err
    on_github["body"] += "\n## The stack\nWren's 19 are left for him.\n"
    assert cli.main(["pr", "add", PR, "--job", "j1", *LEAD]) == 0
    noted = capsys.readouterr().err
    assert ('which may speak of him as he or his: the stack: "Wren\'s 19 are left for him.".' in noted, "Wren He" in noted) == (True, False)
    assert "Forge-AI/monorepo#42" in state.load("demo")["prs"]
    for pronoun in ("| His approval | yes |", "It merged. He tapped it.", "[His words](https://example.com)", "**He** said so."):
        assert limits.third_person({"show me": pronoun}), pronoun


def test_pr_add_refuses_a_show_me_fence_that_does_not_draw_and_adds_it_once_it_draws(
    run: Run, demo: str, on_github: dict, chrome: Chrome, images: Images, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Move the strip", "--project", "The drive", "--workstream", "Releases", "move the strip")
    run("job", "add", "builder", "--order", "o1", "--goal", "move it")
    on_github["body"] = CARD_BODY + '\n## Show me\nThe map\n```arch\n{"arch": 1}\n```\n\n![before](https://example.com/before.png)\n'
    chrome.refusal = '"name" is required'
    assert cli.main(["pr", "add", PR, "--job", "j1", *LEAD]) == 2
    assert capsys.readouterr().err.strip() == 'drive: Forge-AI/monorepo#42: refused, nothing added: its Show me arch does not draw: "name" is required'
    assert state.load("demo")["prs"] == {}
    chrome.refusal = None
    assert run("pr", "add", PR, "--job", "j1", *LEAD)[0] == 0
    assert [kind for kind, _ in chrome.drawn] == ["arch"]


def test_pr_add_refuses_a_show_me_image_his_card_cannot_download_and_adds_it_once_it_links_one_that_downloads(
    run: Run, demo: str, on_github: dict, images: Images, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Fill the tree", "--project", "The drive", "--workstream", "Releases", "fill the tree")
    run("job", "add", "builder", "--order", "o1", "--goal", "fill it")
    artifact = "https://buildkite.com/organizations/acme/pipelines/test/builds/7/jobs/01a1ca2b/artifacts/01a1ca2e"
    attached = "https://github.com/user-attachments/assets/0f8e7a4c-1b2d-4e3f-9a8b-7c6d5e4f3a2b"
    images.refusals[artifact] = "HTTP Error 403: Forbidden"
    on_github["body"] = CARD_BODY + f"\n## Show me\n![1 · Before, dev]({artifact})\n"
    assert cli.main(["pr", "add", PR, "--job", "j1", *LEAD]) == 2
    assert capsys.readouterr().err.strip() == f"drive: Forge-AI/monorepo#42: refused, nothing added: its Show me image does not draw: {artifact} did not download: HTTP Error 403: Forbidden"
    assert state.load("demo")["prs"] == {}
    on_github["body"] = CARD_BODY + f"\n## Show me\n![1 · Before, dev]({attached})\n"
    assert run("pr", "add", PR, "--job", "j1", *LEAD)[0] == 0
    assert images.fetched == [attached]


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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "the mint endpoint fails", "--incident", "--parts", "fix", "first check")
    on_github.update(title="Fix: the mint endpoint answers again", body="Reverts Forge-AI/monorepo#41")
    assert cli.main(["pr", "add", PR, "--job", "j1", "--exempt", "incident fix", *LEAD]) == 2
    refusal = capsys.readouterr().err
    assert 'refused, it reverts a change, and a revert never lands as an "incident fix"' in refusal
    assert f"Add it waiting on his tap with drive pr add {PR} --job j1. Draw his tap card with drive pr set {PR} --tap --serves o1 --design none" in refusal
    assert state.load("demo")["prs"] == {}
    assert run("pr", "add", PR, "--job", "j1", *LEAD)[0] == 0
    assert state.load("demo")["prs"]["Forge-AI/monorepo#42"]["wants_tap"] is True
    on_github["body"] = CARD_BODY
    assert run("pr", "add", PR, "--job", "j1", *LEAD)[0] == 0


def test_job_add_sends_a_builder_past_the_macs_cap_or_behind_its_line_to_a_sprite_at_once_and_never_refuses_a_monorepo_one_for_load(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(place, "busy", lambda: 0.95)
    monkeypatch.setattr(place, "limit", lambda: 0.8)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "land the strip", "--parts", *(f"strip {number}" for number in range(1, 12)))
    assert run("job", "add", "builder", "--order", "o1", "--part", "strip 1", "--goal", "land", "--repo", "Forge-AI/monorepo") == (0, "j1")
    for number in range(2, place.LOCAL_BUILDERS + 1):
        assert run("job", "add", "builder", "--order", "o1", "--part", f"strip {number}", "--goal", "land", "--repo", "yasyf/cc-skills", "--agent", "a")[0] == 0
    assert run("job", "add", "builder", "--order", "o1", "--part", "strip 9", "--goal", "land", "--repo", "yasyf/cc-skills")[0] == 0
    assert state.load(demo)["jobs"]["j9"]["state"] == "running"
    with state.edit("demo") as data:
        data["jobs"]["j9"].update(state.local_lane("dispatch-9"))
    mac_only = f"one whose brief declares a step only this Mac runs, on {place.MAC_LINES}, waits its turn for a slot here once its agent starts"
    code, added = run("job", "add", "builder", "--order", "o1", "--part", "strip 10", "--goal", "land", "--repo", "yasyf/cc-skills")
    added_id, sprite, *_ = added.splitlines()
    assert (code, added_id) == (0, "j10")
    assert sprite.startswith("j10 starts on a Sprite at once, since 8 builders already run on this Mac, its cap of 8 (j2 on o1")
    assert sprite.endswith(f"; j9 on o1, its Orca lane dispatch-9, not ended): drive job launch j10 <brief>; {mac_only}")
    routed = state.load(demo)["jobs"]["j10"]
    assert (routed["state"], routed["waits"], routed["agent"], routed["where"]) == ("running", [], None, "remote")
    with state.edit("demo") as data:
        data["jobs"]["j2"]["state"] = "done"
        place.queue(data["jobs"]["j10"], state.now())
    assert run("job", "add", "builder", "--order", "o1", "--part", "strip 11", "--goal", "land", "--repo", "yasyf/cc-skills")[1].splitlines()[1] == (
        "j11 starts on a Sprite at once, since builders already wait their turn for a slot on this Mac ahead of it, earliest due first (j10):"
        f" drive job launch j11 <brief>; {mac_only}"
    )
    assert place.waiting_builders(state.load(demo)) == ["j10"]
    assert "j10 builder waiting: land; waits on a builder slot on this Mac, 1 of 1 in line" in run("status", "o1")[1]
    assert json.loads(run("status", "o1", "--json")[1])["jobs"]["j10"]["in_line"] == "1 of 1 in line"
    assert run("job", "add", "reader", "--order", "o1", "--part", "strip 1", "--goal", "read")[0] == 0


def test_job_add_starts_an_open_incidents_fix_builder_31st_in_line_and_an_immediate_fix_at_once_past_the_macs_cap(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "tidy the docs", "--parts", *(f"doc {number}" for number in range(place.LOCAL_BUILDERS + 30)))
    for number in range(place.LOCAL_BUILDERS):
        assert run("job", "add", "builder", "--order", "o1", "--part", f"doc {number}", "--goal", "build", "--agent", "a")[0] == 0
    for number in range(place.LOCAL_BUILDERS, place.LOCAL_BUILDERS + 30):
        assert "starts on a Sprite at once" in run("job", "add", "builder", "--order", "o1", "--part", f"doc {number}", "--goal", "wait")[1]
    with state.edit(demo) as data:
        for job in data["jobs"].values():
            if job["where"] == "remote":
                place.queue(job, state.now())
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "you broke something again fix this asap", "--incident", "--parts", "fix", "first check")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the card is broken", "--parts", "the card", "immediate fix")
    for order_id, part in (("o2", "fix"), ("o3", "immediate fix")):
        code, added = run("job", "add", "builder", "--order", order_id, "--part", part, "--goal", "fix it")
        job = state.load(demo)["jobs"][added.splitlines()[0]]
        assert (code, job["state"], job["waits"], job["where"], "Sprite" in added) == (0, "running", [], "local", False)
    assert len(place.waiting_builders(state.load(demo))) == 30


def test_job_add_records_an_agent_already_running_past_the_macs_cap_and_its_limit(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(place, "busy", lambda: 0.95)
    monkeypatch.setattr(place, "limit", lambda: 0.8)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "land the strip", "--parts", *(f"strip {number}" for number in range(place.LOCAL_BUILDERS + 1)))
    for number in range(place.LOCAL_BUILDERS):
        assert run("job", "add", "builder", "--order", "o1", "--part", f"strip {number}", "--goal", "land", "--agent", "a")[0] == 0
    assert run("job", "add", "builder", "--order", "o1", "--part", f"strip {place.LOCAL_BUILDERS}", "--goal", "land", "--repo", "Forge-AI/monorepo", "--agent", "a-9")[0] == 0
    assert state.load(demo)["jobs"]["j9"]["agent"] == "a-9"

def test_job_brief_writes_native_interactions_would_he_ask_and_a_readers_research_and_keeps_each_flag_left_out(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "show the run page", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the run page")
    code, out = run("job", "brief", "j1", "--serves", "his order", "--done-when", "researched", "--where", "yasyf/cc-skills main")
    assert code == 0
    assert "\nResearch: the Researched: block this build needs\n" in Path(out.splitlines()[0].removeprefix("Read your brief first: ")).read_text()
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "show the run's steps")
    run("job", "add", "builder", "--order", "o2", "--goal", "draw the steps")
    code, out = run("job", "brief", "j3")
    assert (code, "\nServes: his order\n" in Path(out.splitlines()[0].removeprefix("Read your brief first: ")).read_text()) == (0, True)
    first = ["job", "brief", "j3", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main", "--native", "the dashboard's run page"]
    assert cli.main(first) == 2
    assert capsys.readouterr().err.startswith(
        "drive: j3's result is something he uses, by --native: name each user story on the dashboard's run page with --interactions, one per `;`,"
    )
    code, out = run(*first, "--interactions", "open the run page, click a step, read its log", "--would-he-ask", "a link to the run from his DM")
    written = Path(out.splitlines()[0].removeprefix("Read your brief first: "))
    assert code == 0
    assert (
        "Native: the dashboard's run page\nInteractions: open the run page, click a step, read its log\nWould he ask: a link to the run from his DM\n"
        in written.read_text()
    )
    code, out = run("job", "brief", "j3", "--plan", "the steps section")
    assert code == 0
    assert out.splitlines()[-1] == "kept from its earlier brief: --serves, --done-when, --where, --native, --interactions, --would-he-ask"
    assert "Serves: his order\n" in written.read_text()
    assert "Native: the dashboard's run page\n" in written.read_text()
    assert "Plan: the steps section\n" in written.read_text()


def test_a_brief_naming_no_flag_written_as_drive_job_brief_types_a_plan_is_written_again_keeping_that_plan(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "watch for stuck jobs")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the watchdog")
    rendered = cli.linked_messages

    def typed_meanwhile(*given: object) -> list[str]:
        monkeypatch.setattr(cli, "linked_messages", rendered)
        assert run("job", "brief", "j1", "--plan", "the watchdog section")[0] == 0
        return rendered(*given)

    monkeypatch.setattr(cli, "linked_messages", typed_meanwhile)
    line, _ = cli.write_brief(demo, "j1", {})
    assert line.splitlines()[-1] == "kept from its earlier brief: --serves, --plan"
    assert state.load(demo)["jobs"]["j1"]["brief_flags"]["plan"] == "the watchdog section"
    assert "\nPlan: the watchdog section\n" in Path(state.load(demo)["jobs"]["j1"]["brief"]).read_text()


def test_job_brief_keeps_an_earlier_handoff_and_adds_each_ruling_to_the_earlier_ones(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "watch for stuck jobs")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the watchdog")
    handoff = f"{PR} head abc: his answer picks the watchdog\nINSTALL: drive install long-running@skills\nPROVE: drive status j1"
    code, out = run(
        "job", "brief", "j1", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main",
        "--handoff", handoff, "--ruling", "5abdd1a", "--ruling", "c7756f4",
    )
    written = Path(out.splitlines()[0].removeprefix("Read your brief first: "))
    code, out = run("job", "brief", "j1", "--done-when", "merged and installed")
    assert code == 0
    assert out.splitlines()[-1] == "kept from its earlier brief: --serves, --where, --handoff, --ruling"
    assert f"Handoff: {handoff}\n" in written.read_text()
    code, out = run("job", "brief", "j1", "--ruling", "5abdd1a")
    assert code == 0
    assert out.splitlines()[-1] == "kept from its earlier brief: --serves, --done-when, --where, --handoff, --ruling"
    assert state.load(demo)["jobs"]["j1"]["brief_flags"]["ruling"] == ["5abdd1a", "c7756f4"]
    assert f"Handoff: {handoff}\n" in written.read_text()
    assert "(c7756f4)" in written.read_text()


def test_job_brief_carries_the_words_of_each_order_the_job_carries_and_each_ruling_their_readers_name(
    run: Run, meta: str, recorded: Answers, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: partition the journal tables")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: pick each table's partition by its usage", "--kind", "build")
    run("job", "add", "reader", "--order", "o2", "--goal", "research: the partitions")
    rulings = "rulings: bc0da25: 'time, hash by key and spill all ship'; a6a9980 (tap) \"build the abstraction first\"; 9f3c2e1 reverted the spill"
    assert keep(monkeypatch, capsys, "j1", RESEARCH.replace("rulings: none on the copy path", rulings)) == (0, "")
    recorded.shown["bc0da25"] = {"id": "bc0da25e", "title": "Partition strategies", "body": "time, hash by key and spill all ship\nNotes: read as three strategies", "tags": [], "created_at": "2026-11-12T11:33:00Z"}
    recorded.shown["a6a9980"] = {"id": "a6a9980f", "title": "Abstraction first", "body": "build the abstraction first", "tags": [], "created_at": "2026-11-12T11:34:00Z"}
    recorded.unrecorded.add("9f3c2e1")
    newer = run("job", "add", "reader", "--order", "o2", "--goal", "research: the debug guard")[1].splitlines()[0]
    assert keep(monkeypatch, capsys, newer, RESEARCH.replace("rulings: none on the copy path", 'rulings: 7f91d2f "use the machine profile"; bc0da25e "time, hash by key and spill all ship"')) == (0, "")
    recorded.shown["7f91d2f"] = {"id": "7f91d2fa", "title": "Machine profile", "body": "use the machine profile", "tags": [], "created_at": "2026-11-12T11:35:00Z"}
    recorded.shown["bc0da25e"] = recorded.shown["bc0da25"]
    builder = run("job", "add", "builder", "--order", "o1", "--goal", "partition the tables")[1].splitlines()[0]
    run("job", "set", "j2", "--state", "stopped")
    assert run("job", "set", builder, "--carries", "o2")[0] == 0
    code, out = run("job", "brief", builder, "--serves", "drive tooling", "--done-when", "merged", "--where", "yasyf/cc-skills main")
    assert code == 0
    assert out.splitlines()[1].startswith("his words from o1, o2, rulings: 3,")
    written = Path(out.splitlines()[0].removeprefix("Read your brief first: ")).read_text()
    assert written.startswith("Order (his words): Drive tooling: partition the journal tables\no2, which it also carries: Drive tooling: pick each table's partition by its usage\n")
    assert (
        "Decisions that bind:\n- Machine profile (7f91d2f)\n  use the machine profile\n- Partition strategies (bc0da25)\n  time, hash by key and spill all ship\n"
        "- Abstraction first (a6a9980)\n  build the abstraction first\n"
    ) in written
    assert "9f3c2e1" not in written


def test_job_brief_carries_each_slack_message_its_orders_link_and_refuses_on_one_cc_slack_cannot_read_only_while_nothing_stands_in_for_it(
    run: Run, demo: str, slack_thread: Thread, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    alert, his = "1794600671.912379", "1794600700.000100"
    alert_link, his_link = (f"https://acme.slack.com/archives/{channel}/p{ts.replace('.', '')}" for channel, ts in (("C0822AHFY3G", alert), ("D123", his)))
    slack_thread.messages += [
        {"ts": alert, "user": "U0828UHTV42", "user_name": "Datadog", "text": "Runner refusing runs whose journal cannot open", "from_bot": True},
        {"ts": his, "user": "U1", "user_name": "", "text": "this one &amp; the next", "from_user": True, "shares": [{"user": "U2", "user_name": "PagerDuty", "text": "the journal is gone"}]},
    ]
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", alert_link)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", f"and <{his_link}|this>, as {alert_link} says")
    run("job", "add", "builder", "--order", "o1", "--goal", "stop the refusals")
    assert run("job", "set", "j1", "--carries", "o2")[0] == 0
    code, out = run("job", "brief", "j1", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main")
    assert code == 0
    written = Path(out.splitlines()[0].removeprefix("Read your brief first: "))
    posted = {ts: slack.sent_at({"ts": ts}).isoformat(timespec="seconds") for ts in (alert, his)}
    assert written.read_text().startswith(
        f"Order (his words): {alert_link}\no2, which it also carries: and <{his_link}|this>, as {alert_link} says\nLinked messages:\n"
        f"- {alert_link}, posted by Datadog at {posted[alert]}:\n  Runner refusing runs whose journal cannot open\n"
        f"- {his_link}, posted by him at {posted[his]}:\n  this one & the next\n  Shared from PagerDuty: the journal is gone\nServes: his order\n"
    )
    refusing = {alert_link}

    def refused(*where: str) -> list[dict]:
        if refusing & set(where):
            raise slack.SlackError("not_in_channel")
        return list(slack_thread.messages)

    monkeypatch.setattr(slack, "thread", refused)
    his_message = f"- {his_link}, posted by him at {posted[his]}:\n  this one & the next\n  Shared from PagerDuty: the journal is gone\n"
    assert run("job", "brief", "j1")[0] == 0
    assert f"Linked messages:\n- {alert_link}, not read: not_in_channel\n{his_message}Serves: his order\n" in written.read_text()
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", f"<{his_link}>")
    assert run("job", "set", "j1", "--state", "stopped")[0] == 0
    run("job", "add", "builder", "--order", "o1", "--goal", "prove the fix live")
    assert run("job", "set", "j2", "--carries", "o3")[0] == 0
    code, out = run("job", "brief", "j2", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main")
    assert code == 0
    link_only = Path(out.splitlines()[0].removeprefix("Read your brief first: "))
    assert f"Linked messages:\n- {alert_link}, not read: not_in_channel\n{his_message}Serves: his order\n" in link_only.read_text()
    kept = link_only.read_text()
    refusing.add(his_link)
    assert cli.main(["job", "brief", "j2"]) == 2
    assert capsys.readouterr().err == (
        f"drive: j2: no brief written, since cc-slack cannot read {alert_link}, which its order links: not_in_channel;"
        f" {his_link}, which its order links: not_in_channel; write it again once cc-slack reads it\n"
    )
    assert link_only.read_text() == kept
    assert run("job", "brief", "j2", "--handoff", "#7 merged: install it and prove it live")[0] == 0
    assert f"Linked messages:\n- {alert_link}, not read: not_in_channel\n- {his_link}, not read: not_in_channel\nServes: his order\n" in link_only.read_text()


def test_a_live_or_finished_jobs_handoff_briefed_before_brief_flags_kept_one_is_seeded_from_its_brief_file(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "watch for stuck jobs", "--parts", "the watchdog", "its retry")
    run("job", "add", "builder", "--order", "o1", "--part", "the watchdog", "--goal", "build the watchdog")
    run("job", "add", "builder", "--order", "o1", "--part", "its retry", "--goal", "build the watchdog's retry")
    handoff = f"{PR} head abc: CHECK test failed\nINSTALL: drive install long-running@skills\nPROVE: drive status j1"
    for job_id in ("j1", "j2"):
        code, out = run("job", "brief", job_id, "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main", "--handoff", handoff)
    with state.edit(demo) as data:
        for job in data["jobs"].values():
            del job["brief_flags"]["handoff"]
        data["jobs"]["j2"]["state"] = "done"
    jobs = state.load(demo)["jobs"]
    assert (jobs["j1"]["brief_flags"]["handoff"], jobs["j2"]["brief_flags"]["handoff"]) == (handoff, handoff)
    code, out = run("job", "brief", "j1", "--plan", "the watchdog section")
    assert "--handoff" in out.splitlines()[-1]
    assert f"Handoff: {handoff}\n" in Path(out.splitlines()[0].removeprefix("Read your brief first: ")).read_text()


def test_job_brief_on_a_surface_refuses_interactions_jev_reads_as_steps_and_sends_user_stories_out(
    run: Run, demo: str, jev: Jev, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "show the run's steps")
    run("job", "add", "builder", "--order", "o1", "--goal", "draw the steps")
    briefed = ["job", "brief", "j1", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main", "--native", "the dashboard's run page"]
    jev.picks("steps")
    assert cli.main([*briefed, "--interactions", "open the run page, click a step"]) == 2
    assert capsys.readouterr().err.startswith(
        "drive: j1's result is something he uses, by --native: --interactions names \"open the run page, click a step\", steps with no result he must see:"
    )
    [read] = [asked["questions"]["story1"]["label"]["instructions"] for asked in jev.asked if "story1" in asked["questions"]]
    assert read.startswith('Is "open the run page, click a step" a user story on the surface below')
    jev.picks("none")
    told = "click a step and its log opens beside it; press j and the next step's log replaces it"
    code, out = run(*briefed, "--interactions", told)
    assert code == 0
    assert [asked["state"] for asked in jev.asked if "story1" in asked["questions"]][-1] == {"surface": "the dashboard's run page", "stories": told.split("; ")}
    assert f"Interactions: {told}\n" in Path(out.splitlines()[0].removeprefix("Read your brief first: ")).read_text()


def test_an_answer_orders_reader_brief_sends_his_answer_to_his_thread_before_its_full_report(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why did the copy stall?", "--kind", "answer", "--from", "slack:D123/1794300000.000500/1794300000.000600")
    run("job", "add", "reader", "--order", "o1", "--goal", "why the copy stalled")
    code, out = run("job", "brief", "j1", "--serves", "his order", "--done-when", "answered", "--where", "yasyf/cc-skills main")
    assert code == 0
    brief = Path(out.splitlines()[0].removeprefix("Read your brief first: ")).read_text()
    thread = state.load(demo)["orders"]["o1"]["thread"][1]
    assert (
        "\nAnswer: his answer goes to his thread the moment you have it proven, before any reproduction file or full report: one"
        " `report` record on the cci `post` tool, `to: [communicator@demo]`, `text: POST <the answer>`,"
        f' `attrs: {{"thread": "{thread}", "order": "o1", "job": "j1", "progress": "true"}}`, with `"incident": "true"` added when it says'
        " a customer team's production runs are failing or stopped, so it posts while he is away too; then keep your full report and return as usual\n"
    ) in brief
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "show the run page", "--kind", "build")
    run("job", "add", "reader", "--order", "o2", "--goal", "research: the run page")
    code, out = run("job", "brief", "j2", "--serves", "his order", "--done-when", "researched", "--where", "yasyf/cc-skills main")
    assert "\nAnswer:" not in Path(out.splitlines()[0].removeprefix("Read your brief first: ")).read_text()


def test_order_set_proves_done_on_his_surface_with_a_recording_or_the_live_post(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    view, walk, log, log_walk = (tmp_path / saved for saved in ("view.json", "walk.mov", "log.json", "log.mov"))
    for saved in (view, walk, log, log_walk):
        saved.write_text("saved")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "show the run's steps")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "count the runs")
    run("job", "add", "builder", "--order", "o2", "--goal", "count them")
    run("job", "brief", "j2", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main", "--native", "none: he never sees it")
    assert run("order", "set", "o2", "--state", "done", "--proof", str(view))[0] == 0
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "show the run's log")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "land the strip")
    run("job", "add", "builder", "--order", "o1", "--goal", "Fix: land it", "--repo", "Forge-AI/monorepo", "--remote")
    run("pr", "add", PR, "--job", "j1", *LEAD)
    monkeypatch.setattr("sys.stdin", io.StringIO(f"WAITING {PR}\n"))
    assert run("job", "report", "j1", "-")[0] == 0
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
    run("pr", "add", PR, "--job", asking, *LEAD)
    run("pr", "set", PR, "--port-of", "4100")
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    assert cli.main(["job", "ask", asking, str(block)]) == 2
    assert capsys.readouterr().err.count(
        '\n- j1\'s PR is a port of #4100: its ASK\'s summary says "port of #4100", as the text of that PR\'s link, and what in the'
        " target tree forces a choice #4100 did not make, such as what that tree lacks\n"
    ) == 1
    ported = f"This is a <https://github.com/Forge-AI/monorepo/pull/4100|port of #4100>, opened as <{PR}|the move strip port>, and your pick decides how a finished run's files are laid out in a target tree that lacks the snapshot writer;"
    block.write_text(SHORT_ASK.replace("Drive tooling: your pick decides how a finished run's files are laid out;", ported))
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
    code, out = run("status", "o1")
    assert code == 0
    assert out.splitlines()[0] == "demo: 1 open of 1 orders"
    assert 'Forge-AI/monorepo#42 "Fix: the watched title" open, checks pass, review approved' in out
    assert json.loads(run("status", "o1", "--json")[1])["prs"][with_pr]["title"] == "Fix: the watched title"


def test_status_prints_an_open_pr_while_the_installed_cc_slack_lists_no_closed_cards(run: Run, with_pr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": []})
    code, out = run("status")
    assert code == 0
    assert out.splitlines()[0] == "demo: 1 open of 1 orders"
    assert json.loads(run("status", "o1", "--json")[1])["prs"][with_pr]["card_state"] is None


def test_status_reads_only_the_orders_its_ids_name(run: Run, with_pr: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "something else")
    for key in ("o1", "j1", with_pr, PR):
        code, out = run("status", key)
        assert code == 0
        assert out.splitlines()[0].startswith("demo: 1 open of 1 orders")
        assert "land it" in out
        assert "something else" not in out
    assert run("status", "o2")[1].splitlines()[0].startswith("demo: 1 open of 1 orders")
    assert run("status", "j9")[0] != 0


def test_status_of_an_order_a_design_of_an_unnamed_order_carries_reads_that_design_and_its_builder_on_the_whole_drive(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "auto-partition big tables")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why is the database so big", "--parts", "partitioning design choices")
    run("job", "add", "design", "--order", "o1", "--goal", "partitioning options")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the partitioning")
    with state.edit("demo") as data:
        data["jobs"]["j1"]["state"] = "done"
        state.carry(data, "j1", "o2", ["partitioning design choices"])
    code, out = run("status", "o2")
    assert code == 0
    assert out.splitlines()[:3] == ["demo: 1 open of 1 orders", "- o2 open: why is the database so big", '  parts: "partitioning design choices": design j1 done']


def test_status_reads_a_tracked_pr_by_its_bare_number_and_names_the_forms_an_id_takes(
    run: Run, with_pr: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "something else")
    code, out = run("status", "42")
    assert (code, "land it" in out, "something else" in out) == (0, True, False)
    assert cli.main(["status", "53615"]) == 2
    assert "no order, job or PR 53615; name an order o<n>, a job j<n>, or a PR as owner/repo#N, its URL or its bare number" in capsys.readouterr().err
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/sand#42"] = data["prs"][with_pr] | {"url": "https://github.com/Forge-AI/sand/pull/42"}
    assert cli.main(["status", "42"]) == 2
    assert "42 is the number of Forge-AI/monorepo#42, Forge-AI/sand#42; name one as owner/repo#42" in capsys.readouterr().err


def test_order_show_and_job_show_print_what_status_prints(run: Run, with_pr: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "something else")
    for verb, key in (("order", "o1"), ("job", "j1"), ("job", "42")):
        shown = run(verb, "show", key)
        assert (shown[0], "land it" in shown[1]) == (0, True)
        assert shown == run("status", key)


def test_status_prints_each_order_its_ids_name_whole_and_closed_ones_too(run: Run, demo: str) -> None:
    words = "move the replica's restore " + "onto the primary's one restore code path, " * 4
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", words)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "something else")
    run("job", "add", "builder", "--order", "o1", "--goal", "one restore code path " * 8)
    said = "DONE " + "merged and installed, the replica now calls the primary's restore code, " * 4
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(state="done", said=said)
    run("order", "set", "o1", "--state", "done")
    lines = run("status", "j1")[1].splitlines()
    assert lines == [
        "demo: 0 open of 1 orders",
        f"- o1 done: {' '.join(words.split())}",
        f"  parts: \"{state.load('demo')['orders']['o1']['parts'].popitem()[0]}\": delivered",
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
    assert "| 🙋 Needs you | 🔴 Blocked |" in out
    assert "land it" in out


def test_drive_flag_picks_among_several(run: Run, demo: str) -> None:
    run("start", "other", "--role", "coordinator")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "for other", "--drive", "other")
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
    monkeypatch.setattr(cli.install, "main", lambda *args: calls.append(args) or 0)
    assert run("install", "long-running@skills", "--sha", "abc", "--repo", "yasyf/cc-skills")[0] == 0
    assert run("install", "long-running@skills", "--timeout", "60")[0] == 0
    assert calls == [("long-running@skills", "abc", "yasyf/cc-skills", 900, None), ("long-running@skills", None, None, 60, None)]


def installed(config: Path, entries: dict[str, list[dict]]) -> None:
    plugins = config / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": entries}))


def fake_plugin(path: Path, marker: str) -> str:
    (path / "bin").mkdir(parents=True)
    target = path / "bin" / "drive"
    target.write_text(f"import sys\nprint({marker!r}, *sys.argv[1:])\n")
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


def test_the_drive_servers_launch_line_runs_the_newest_installed_drive_mcp(tmp_path: Path) -> None:
    config = tmp_path / "claude"
    installed(
        config,
        {
            "long-running@skills": [{"installPath": fake_plugin(tmp_path / "old", "old"), "lastUpdated": "2026-11-01T00:00:00Z"}],
            "long-running@other": [{"installPath": fake_plugin(tmp_path / "new", "new"), "lastUpdated": "2026-11-09T00:00:00Z"}],
        },
    )
    server = json.loads(cli.MANIFEST.read_text())["mcpServers"]["drive"]
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(config), **server["env"]}
    result = subprocess.run([server["command"], *server["args"]], capture_output=True, text=True, check=True, env=env)
    assert result.stdout.strip() == "new mcp"


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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "build the files design", "--parts", "stack 1", "stack 2", "stack 3")
    assert run("job", "add", "builder", "--order", "o1", "--goal", "all of it", "--agent", "a")[0] == 2
    assert run("job", "add", "builder", "--order", "o1", "--goal", "x", "--part", "stack 9")[0] == 2
    assert run("job", "add", "builder", "--order", "o1", "--goal", "stacks 2-3", "--part", "stack 2", "--part", "stack 3")[1] == "j1"
    run("job", "set", "j1", "--part", "stack 1", "--card", "https://acme.slack.com/archives/D1/p1")
    data = state.load("demo")
    assert list(data["orders"]["o1"]["parts"]) == ["stack 1", "stack 2", "stack 3"]
    assert data["jobs"]["j1"]["parts"] == ["stack 2", "stack 3", "stack 1"]
    assert [question["card"] for question in data["jobs"]["j1"]["questions"]] == ["https://acme.slack.com/archives/D1/p1"]


def test_a_change_job_added_on_a_part_another_unfinished_one_carries_folds_into_it_is_refused_while_it_runs_and_replaces_it_once_it_returned(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    url, key = "https://github.com/acme/app/pull/55", "acme/app#55"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "three spikes", "--parts", "spike 1", "spike 2", "spike 3")
    run("job", "add", "builder", "--order", "o1", "--goal", "spike 1", "--part", "spike 1")
    assert run("job", "add", "codex", "--order", "o1", "--goal", "spikes 1-2: restore cost", "--part", "spike 1", "--part", "spike 2") == (
        0,
        'j1\nj1 carried "spike 1" and no worker took it, so this add folded into it, a part having one change job: its goal is this add\'s and its waits gained this add\'s',
    )
    job = state.load("demo")["jobs"]["j1"]
    assert (job["kind"], job["goal"], job["parts"]) == ("codex", "spikes 1-2: restore cost", ["spike 1", "spike 2"])
    run("job", "add", "builder", "--order", "o1", "--goal", "spike 3", "--part", "spike 3")
    assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", "spikes", "--part", "spike 3", "--part", "spike 2"]) == 2
    assert 'j1 codex running carries "spike 2" already, and j2 builder running carries "spike 3": a part has one change job' in capsys.readouterr().err
    run("job", "set", "j1", "--agent", "a1")
    assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", "spike 2 again", "--part", "spike 2"]) == 2
    assert 'refused, no job added: j1 codex running carries "spike 2" already, so a second builder would race it on one change: send it the event with drive job resume j1' in capsys.readouterr().err
    assert cli.main(["job", "set", "j2", "--part", "spike 1"]) == 2
    assert 'j2: refused, nothing changed: j1 codex running carries "spike 1" already, so a second builder would race it' in capsys.readouterr().err
    with state.edit("demo") as data:
        data["prs"][key] = state.new_pr(url, "j1", "o1")
        data["jobs"]["j1"].update(state="waiting", prs=[url])
    assert run("job", "add", "builder", "--order", "o1", "--goal", "spike 2 again", "--part", "spike 2") == (
        0,
        f'j3\nj3 replaces j1, which carried "spike 2" and returned, a part having one change job: it takes j1\'s parts and {key}, and j1 stopped, handed to j3',
    )
    data = state.load("demo")
    assert (data["jobs"]["j3"]["parts"], data["jobs"]["j3"]["prs"], data["prs"][key]["job"]) == (["spike 2", "spike 1"], [url], "j3")
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["said"]) == ("stopped", "handed to j3")


def test_the_immediate_fix_a_sub_coordinators_builder_runs_takes_no_second_change_job_and_no_step_of_the_top_coordinator(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Agents", "fault the socket", "--parts", "one code path", "immediate fix")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "platform-1")
    run("start", "demo", "--role", "coordinator", "--project", "Platform")
    assert run("job", "add", "builder", "--order", "o1", "--part", "immediate fix", "--goal", "fault the socket", "--agent", "a1")[1] == "j1"
    assert cli.main(["job", "add", "codex", "--order", "o1", "--part", "immediate fix", "--goal", "fault it again"]) == 2
    assert capsys.readouterr().err.strip() == (
        'drive: refused, no job added: j1 builder running carries "immediate fix" already, so a second codex would race it on one change:'
        ' send it the event with drive job resume j1 "<the event>", or stop it with drive job set j1 --state stopped before adding the job that replaces it'
    )
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-under-test")
    held = "o1 is the sub-coordinator for Platform's order, session platform-1: only the sub-coordinator for Platform adds its jobs"
    for argv in (["job", "add", "builder", "--order", "o1", "--part", "one code path", "--goal", "build it"], ["job", "resume", "j1", "fault the socket"]):
        assert cli.main(argv) == 2
        assert held in capsys.readouterr().err
    assert list(state.load("demo")["jobs"]) == ["j1"]


def test_job_add_names_by_id_each_pr_its_goal_names_and_each_other_order_whose_jobs_carry_it(run: Run, demo: str) -> None:
    url, key = "https://github.com/acme/app/pull/55", "acme/app#55"
    run("order", "add", "--title", "Upload fix", "--project", "The drive", "--workstream", "Drive tooling", "ship the upload fix to production")
    run("order", "add", "--title", "Ship again", "--project", "The drive", "--workstream", "Drive tooling", "ship the upload fix again")
    run("order", "add", "--title", "Notes", "--project", "The drive", "--workstream", "Drive tooling", "read the release notes")
    run("job", "add", "builder", "--order", "o1", "--goal", f"waits on release:api:{key}")
    run("job", "add", "reader", "--order", "o3", "--goal", "read the release notes")
    run("job", "add", "reader", "--order", "o2", "--goal", f"read what shipping {url} again needs")
    with state.edit("demo") as data:
        data["prs"][key] = state.new_pr(url, "j1", "o1") | {"state": "merged", "title": "Fix: the upload reaches production"}
        data["jobs"]["j1"]["prs"].append(url)
        for job_id in ("j1", "j2", "j3"):
            data["jobs"][job_id]["state"] = "done"
    assert run("job", "add", "reader", "--order", "o2", "--goal", f"write his question card for shipping {url} again") == (0, f"j4\nj4 names {key}, also on o1: drive status <id>")
    assert run("job", "add", "reader", "--order", "o2", "--goal", f"read the logs of {url}") == (0, f"j5\nj5 names {key}, also on o1: drive status <id>")
    assert json.loads(run("job", "add", "reader", "--order", "o2", "--goal", "read the logs", "--json")[1])["history"] == {}
    assert run("job", "add", "reader", "--order", "o3", "--goal", "read the notes again") == (0, "j7")


def test_an_order_of_one_deliverable_is_one_part_its_jobs_take_by_default(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix   the\nwatch")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix", "--agent", "a")
    data = state.load("demo")
    assert list(data["orders"]["o1"]["parts"]) == ["Short title"]
    assert data["jobs"]["j1"]["parts"] == ["Short title"]
    assert data["jobs"]["j1"]["session"] == os.environ["CLAUDE_CODE_SESSION_ID"]


def test_setting_parts_keeps_what_a_kept_part_recorded(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: "Thread"
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "two answers", "--kind", "answer", "--parts", "why", "how")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    answer = slack_thread.post(state.now(), from_claude=True)
    report_records(monkeypatch, {"661300": {"part": "why"}})
    assert run("order", "posted", "o1", answer, "--record", "661300")[0] == 0
    run("order", "set", "o1", "--parts", "why", "what")
    parts = state.load("demo")["orders"]["o1"]["parts"]
    assert parts == {"why": state.new_part() | {"posted": answer}, "what": state.new_part()}


def test_a_post_naming_no_part_of_an_order_of_several_is_a_progress_line_that_keeps_its_promised_time(
    run: Run, demo: str, slack_thread: "Thread", capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "stop the harm, roll back, find the cause", "--kind", "answer", "--thread", "1794200000.000100", "--due", "+10m", "--parts", "stop the harm", "rollback", "cause")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "stop the harm, roll back", "--kind", "answer", "--thread", "1794200000.000100", "--parts", "stop the harm", "rollback")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "how fast can restore go?", "--kind", "answer", "--thread", "1794100000.000100")
    run("job", "add", "reader", "--order", "o1", "--goal", "faster restore")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    answer = slack_thread.post(state.now(), from_claude=True)
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(None, None, state.stamp()) | {"waits": "demo:j1-1"})
    cli.follow_card("demo", "demo:j1-1", set(), "https://acme.slack.com/archives/D123/p1794200000000100")
    report_records(monkeypatch, {"661300": {"job": "j1", "part": "Short title"}})
    assert run("order", "posted", "o1", answer, "--record", "661300") == (0, f'o1 "Short title": posted {answer}')
    assert state.load("demo")["orders"]["o1"]["parts"]["Short title"]["posted"] == answer


def test_a_line_in_the_thread_of_the_card_of_a_pr_on_the_order_is_in_the_orders_thread(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship web search", "--kind", "answer", "--thread", "1794100000.000100", "--due", "+30m")
    run("job", "add", "builder", "--order", "o1", "--goal", "web search")
    run("pr", "add", PR, "--job", "j1", *LEAD)
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    root = {"ts": "1794200000.000100", "thread_ts": "1794200000.000100", "from_claude": True}
    slack_thread.messages.append(root | {"text": "New: approve web search? — ✓ *Approve* · <https://github.com/Forge-AI/monorepo/pull/41|PR 41>"})
    line = slack_thread.post(state.now(), from_claude=True)
    assert run("order", "posted", "o1", line) == (0, f"o1 progress line, no part delivered: {line}, in thread {root['ts']}, now one of its threads")
    slack_thread.messages[0] = root | {"text": f"New: approve web search? — ✓ *Approve* · answered 7:10pm · <{PR}|monorepo PR 42>"}
    report_records(monkeypatch, {"661300": {"job": "j1", "part": "Short title"}})
    assert run("order", "posted", "o1", line, "--record", "661300") == (0, f'o1 "Short title": posted {line}')
    order = state.load("demo")["orders"]["o1"]
    assert (order["parts"]["Short title"]["posted"], order["last_line"]) == (line, state.stamp(slack.sent_at(slack_thread.messages[-1])))


def test_a_bare_link_to_a_line_under_the_card_of_a_pr_on_the_order_reads_the_card_its_thread_hangs_from(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship web search", "--thread", "1794100000.000100")
    run("job", "add", "builder", "--order", "o1", "--goal", "web search")
    run("pr", "add", PR, "--job", "j1", *LEAD)
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    card = {"ts": "1794200000.000100", "thread_ts": "1794200000.000100", "from_claude": True, "text": f"New: approve web search? — ✓ *Approve* · <{PR}|monorepo PR 42>"}
    slack_thread.messages.append(card)
    line = slack_thread.post(state.now(), from_claude=True).split("?")[0]
    reply = slack_thread.messages[-1]
    monkeypatch.setattr(slack, "thread", lambda *where: slack_thread.reads.append(where) or ([reply] if where[0] == "--url" else list(slack_thread.messages)))
    assert run("order", "posted", "o1", line) == (0, f"o1 progress line, no part delivered: {line}, in thread {card['ts']}, now one of its threads")
    assert ("--channel", "D123", "--thread", card["ts"], "--limit", "1000") in slack_thread.reads


def test_a_part_delivered_at_the_root_of_another_channel_is_recorded_where_it_was_made_and_keeps_no_promised_time(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch
) -> None:
    parts = ["answer why it fails silently", "tell the two stopped people now"]
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "tell the stopped people", "--kind", "answer", "--thread", "1794100000.000100", "--due", "+30m", "--parts", *parts)
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    told = slack_thread.post(state.now(), from_claude=True, thread_ts=None).split("?")[0].replace("/D123/", "/C0SHARE01/")
    before = state.load("demo")["orders"]["o1"]
    away = "at the root of another channel, so it is no line to him and keeps no promised time"
    assert run("order", "posted", "o1", told) == (0, f"o1 progress line, no part delivered: {told}, {away}")
    report_records(monkeypatch, {"661300": {"part": parts[1]}})
    assert run("order", "posted", "o1", told, "--record", "661300") == (0, f'o1 "{parts[1]}": posted {told}, {away}')
    after = state.load("demo")["orders"]["o1"]
    assert after == before | {"parts": {parts[0]: state.new_part(), parts[1]: {**state.new_part(), "posted": told}}}


def test_a_post_for_a_report_delivers_the_part_its_record_names_in_any_thread(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: "Thread"
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL errors on plat", "--thread", "1794100000.000100", "--kind", "answer", "--parts", "fix", "first check")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    monkeypatch.setattr(cli.reads, "run", lambda argv, checkout: json.dumps({"seq": argv[2], "kind": "report", "lane": "root", "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": {"part": "first check"}}}))
    line = slack_thread.post(state.now(), from_claude=True)
    assert run("order", "posted", "o1", line, "--record", "660611") == (0, f'o1 "first check": posted {line}, in thread 1794200000.000100, now one of its threads')
    parts = state.load("demo")["orders"]["o1"]["parts"]
    assert (parts["fix"]["posted"], parts["first check"]["posted"]) == (None, line)


def test_a_line_on_an_incident_in_the_thread_of_another_alert_joins_that_thread_and_counts_on_the_alerts_order_once_it_joins_the_incident(
    run: Run, demo: str, slack_thread: "Thread"
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL errors on plat", "--thread", "1794100000.000100", "--incident", "--parts", "fix", "first check")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    slack_thread.messages.append({"ts": "1794200000.000100", "thread_ts": "1794200000.000100", "from_user": True})
    line = slack_thread.post(state.now(), from_claude=True)
    assert run("order", "posted", "o1", line) == (0, f"o1 progress line, no part delivered: {line}, in thread 1794200000.000100, now one of its threads")
    alert = "SandSQL replica lanes unproven on staging"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", alert, "--from", "slack:D123/1794200000.000100", "--thread", "1794200000.000100", "--joins", "o1")
    assert run("order", "posted", "o2", line) == (0, f"o2 progress line, no part delivered: {line}")


def test_a_line_in_the_thread_of_the_incident_an_alert_joined_is_its_last_line_and_keeps_no_promised_time_since_the_watch_owes_an_incident_its_lines(
    run: Run, demo: str, slack_thread: "Thread"
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL errors on plat", "--thread", "1794200000.000100", "--incident", "--parts", "fix", "first check")
    alert = "SandSQL replica lanes unproven on staging"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", alert, "--from", "slack:C0ALERTS1/1794300000.000100", "--thread", "1794300000.000100", "--joins", "o1", "--due", "+30m")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    line = slack_thread.post(state.now() + timedelta(seconds=1), from_claude=True)
    assert run("order", "posted", "o2", line) == (0, f"o2 progress line, no part delivered: {line}")
    alert_order = state.load("demo")["orders"]["o2"]
    assert (alert_order["kept"], state.owed(alert_order), alert_order["last_line"]) == (None, False, state.stamp(slack.sent_at(slack_thread.messages[-1])))


def test_an_alert_saved_before_joins_was_kept_loads_joined_to_the_incident_its_owner_job_carries_it_for(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "SandSQL errors on plat", "--incident", "--parts", "fix", "first check")
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "SandSQL error log count", "--joins", "o1")
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "SandSQL restore runs retrying", "--joins", "o2")
    with state.locked("demo"):
        saved = state.stored("demo")
        assert [saved["orders"][order_id]["joins"] for order_id in ("o1", "o2", "o3")] == [None, "o1", "o1"]
        for order in saved["orders"].values():
            del order["joins"]
        saved["jobs"]["j1"]["carries"]["o2"] = ["Short title"]
        saved["orders"]["o2"]["jobs"].append("j1")
        state.store("demo", saved)
    data = state.load("demo")
    assert [data["orders"][order_id]["joins"] for order_id in ("o1", "o2", "o3")] == [None, "o1", None]
    assert state.incident_orders(data, "o2") == ["o1", "o2"]


def test_only_his_own_message_withdraws_a_promised_line(run: Run, demo: str, slack_thread: "Thread") -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "send the numbers", "--thread", "1794200000.000100", "--due", "+30m")
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
        "23": ({"close": "o2", "part": "Short title"}, moment + timedelta(seconds=30)),
    }
    monkeypatch.setattr(
        cli.reads,
        "run",
        lambda argv, checkout: json.dumps({"seq": argv[2], "kind": "report", "lane": "root", "at": reports[argv[2]][1].isoformat(), "fields": {"attrs": reports[argv[2]][0]}}),
    )
    for text in ("unfreeze and release", fix):
        run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", text, "--thread", "1794200000.000100")
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
        f" {render.when(told['promised'], seconds=True)}: post it there first with say(record=22)"
    ) in capsys.readouterr().err
    assert run("order", "set", "o1", "--state", "done", "--record", "21")[0] == 0
    closed = state.load("demo")["orders"]["o1"]
    assert (closed["state"], closed["kept"], closed["due"]) == ("done", told["promised"], told["due"])
    due = state.load("demo")["orders"]["o2"]["due"]
    assert run("order", "posted", "o2", done, "--record", "23") == (
        0,
        f'o2 "Short title": posted {done}, its close keeps the line promised for {render.when(due)}',
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
    monkeypatch.setattr(cli.reads, "run", lambda argv, checkout: json.dumps({"seq": argv[2], "kind": "report", "lane": "root", "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": attrs[argv[2]]}}))
    for text in ("fix the watch", "fix the handler", "fix the say tool", "fix the card"):
        run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", text)
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
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the stuck staging deploy", "--thread", "1794200000.000100", "--reporter", REPORTED)
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the watch", "--thread", "1794200000.000100")
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


def test_a_second_report_of_the_same_bug_joins_its_order_and_each_thread_hears_the_fix_once_before_the_order_closes(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    second = "https://acme.slack.com/archives/C0REPORTS02/p1794593225751189"
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the stuck staging deploy", "--thread", "1794200000.000100", "--reporter", REPORTED)
    for link in (second, "slack:C0REPORTS01/1794400100.000200"):
        run("order", "set", "o1", "--reporter", link)
    assert [held["link"] for held in state.load("demo")["orders"]["o1"]["reporters"]] == [REPORTED, second]
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    report_records(monkeypatch, {"30": {"close": "o1"}})
    run("order", "set", "o1", "--proof", "https://buildkite.com/forge/release/builds/14185")
    owed = "owed its fix-live note now, since its fix is proven live"
    assert f", reported in {REPORTED}, {owed}, reported in {second}, {owed}:" in run("order", "show", "o1")[1]
    assert cli.main(["order", "set", "o1", "--state", "done", "--record", "30"]) == 2
    assert f"o1: refused, its bug was reported in {REPORTED} and {second}, and no fix-live note there is recorded." in capsys.readouterr().err
    monkeypatch.setattr(slack, "group_dm", {"U0REPORTER": "C0GROUPDM1"}.get)
    slack_thread.messages.append({"ts": "1794600000.000100", "from_claude": True})
    grouped = "https://acme.slack.com/archives/C0GROUPDM1/p1794600000000100"
    assert cli.main(["order", "told", "o1", grouped, "--group-dm", "U0REPORTER"]) == 2
    assert f"o1: refused, {REPORTED} and {second} have not heard, so name whose group DM the note went to with --reporter" in capsys.readouterr().err
    assert run("order", "told", "o1", grouped, "--group-dm", "U0REPORTER", "--reporter", second) == (0, f"o1: told in the group DM of U0REPORTER, him and the bot, {grouped}")
    assert cli.main(["order", "set", "o1", "--state", "done", "--record", "30"]) == 2
    assert f"o1: refused, its bug was reported in {REPORTED}, and no fix-live note there is recorded." in capsys.readouterr().err
    note = note_in(slack_thread, "1794600060.000100")
    assert run("order", "told", "o1", note) == (0, f"o1: told in the reporter's thread, {note}")
    assert f", reported in {REPORTED}, told it is fixed in {note}, reported in {second}, told it is fixed in {grouped}:" in run("order", "show", "o1")[1]
    assert run("order", "set", "o1", "--state", "done", "--record", "30")[0] == 0


def test_his_order_to_keep_answering_a_teammates_thread_keeps_that_thread_on_the_order_from_its_filing_and_a_link_naming_no_thread_is_refused(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "watch her responses", "--watch", "C0TEAM0001/1794500000.000300"]) == 2
    assert "--watch C0TEAM0001/1794500000.000300: refused, name the thread by its Slack permalink or as slack:<channel>/<its thread's ts>" in capsys.readouterr().err
    linked = "https://acme.slack.com/archives/C0TEAM0001/p1794500060000100?thread_ts=1794500000.000300&cid=C0TEAM0001"
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "watch her responses and keep replying", "--kind", "answer", "--watch", linked)
    order = state.load("demo")["orders"]["o1"]
    since = f"{state.parse_time(order['created']).timestamp():.6f}"
    assert order["watch"] == {"channel": "C0TEAM0001", "thread": "1794500000.000300", "since": since, "seen": since, "record": None}
    run("order", "set", "o1", "--watch", "slack:C0TEAM0002/1794557000.000100")
    assert [state.load("demo")["orders"]["o1"]["watch"][field] for field in ("channel", "thread", "record")] == ["C0TEAM0002", "1794557000.000100", None]


def test_a_reporter_the_bot_cannot_reach_is_told_in_their_group_dm_with_him_and_only_his_words_take_a_reporter_off(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix it", "--reporter", "C0REPORTS01/1794400100.000200"]) == 2
    assert "refused, name the message by its Slack permalink or as slack:<channel>/<its thread's ts>" in capsys.readouterr().err
    for words in ("fix the deploy", "fix the cache"):
        run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", words, "--thread", "1794200000.000100", "--reporter", "slack:C0REPORTS01/1794400100.000200")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    run("order", "set", "o1", "--proof", "https://buildkite.com/forge/release/builds/14185")
    monkeypatch.setattr(slack, "group_dm", {"U0REPORTER": "C0GROUPDM1"}.get)
    slack_thread.messages.append({"ts": "1794418000.000100", "from_claude": True})
    note = "https://acme.slack.com/archives/C0GROUPDM1/p1794418000000100"
    assert cli.main(["order", "told", "o1", note]) == 2
    assert "the note goes to the group DM of the reporter, him and the bot: pass the reporter's Slack user id with --group-dm" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["order", "told", "o1", note, "--group-dm", "C0GROUPDM1"])
    assert "name the reporter by their Slack user id, such as U0123ABCDE: a channel id names no person" in capsys.readouterr().err
    assert cli.main(["order", "told", "o1", note, "--group-dm", "U0OTHER01"]) == 2
    assert f"{note} is not the drive's post in the group DM of U0OTHER01, him and the bot" in capsys.readouterr().err
    assert run("order", "told", "o1", note, "--group-dm", "U0REPORTER") == (0, f"o1: told in the group DM of U0REPORTER, him and the bot, {note}")
    assert f"told it is fixed in {note}" in run("order", "show", "o1")[1]
    ours = slack_thread.post(state.now() - timedelta(minutes=2), from_claude=True)
    his = slack_thread.post(state.now() - timedelta(minutes=1), from_user=True)
    assert run("order", "set", "o2", "--no-reporter", ours)[0] == 2
    assert run("order", "set", "o2", "--no-reporter", his)[0] == 0
    assert state.load("demo")["orders"]["o2"]["reporters"] == []


def test_a_question_a_teammate_asked_him_that_he_shares_reaches_them_once_its_answer_is_posted_to_him_and_its_order_closes_only_then(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked = "https://acme.slack.com/archives/D0TEAMMATE1/p1794513893775629"
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "what is left for cloud Iris", "--kind", "answer", "--thread", "1794200000.000100", "--reporter", asked)
    order = state.load("demo")["orders"]["o1"]
    assert (state.reported_bug(order), state.hurts_now(state.load("demo"), order)) == (False, False)
    assert f", asked in {asked}, which hears the answer once it is posted" in run("order", "show", "o1")[1]
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    report_records(monkeypatch, {"40": {"close": "o1"}})
    monkeypatch.setattr(slack, "group_dm", {"U0TEAMMATE1": "C0GROUPDM1"}.get)
    slack_thread.messages.append({"ts": "1794533324.854699", "from_claude": True})
    note = "https://acme.slack.com/archives/C0GROUPDM1/p1794533324854699"
    assert cli.main(["order", "told", "o1", note, "--group-dm", "U0TEAMMATE1"]) == 2
    assert "o1: refused, a part of its answer is not posted yet, so a note now would come before the answer: post it first" in capsys.readouterr().err
    run("order", "posted", "o1", slack_thread.post(state.now(), from_claude=True), "--record", "40")
    assert f", asked in {asked}, owed its answer now, since it is posted" in run("order", "show", "o1")[1]
    assert cli.main(["order", "set", "o1", "--state", "done", "--record", "40"]) == 2
    assert f"o1: refused, its question was asked in {asked}, and no note of its answer there is recorded." in capsys.readouterr().err
    assert run("order", "told", "o1", note, "--group-dm", "U0TEAMMATE1") == (0, f"o1: told in the group DM of U0TEAMMATE1, him and the bot, {note}")
    assert f", asked in {asked}, told the answer in {note}" in run("order", "show", "o1")[1]
    assert run("order", "set", "o1", "--state", "done", "--record", "40")[0] == 0
    assert state.load("demo")["orders"]["o1"]["state"] == "done"


def test_the_communicator_drops_an_order_no_record_names_with_a_note_and_closes_one_done_only_by_its_record(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix the watch")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why?")
    assert cli.main(["order", "posted", "o1", "https://acme.slack.com/archives/D1/p2"]) == 2
    assert (
        "only the communicator records a post in an order's thread or takes back a part's mark: send the post's facts as a report record"
        " to communicator@demo, its part or close attr naming what it delivers, and the communicator's handler posts it and records it;"
        " a part a post of the drive already answered, or work a merged PR delivered, is marked from the root@demo session, the one its ORPHAN lines reach,"
        ' with drive order posted o1 <that post\'s or PR\'s link> --part "<part>"'
    ) in capsys.readouterr().err
    assert state.load("demo")["orders"]["o1"]["parts"]["Short title"]["posted"] is None


def report_records(monkeypatch: pytest.MonkeyPatch, attrs: dict[str, dict], lane: str = "root") -> None:
    monkeypatch.setattr(
        cli.reads, "run", lambda argv, checkout: json.dumps({"seq": argv[2], "kind": "report", "lane": lane, "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": attrs[argv[2]]}})
    )


def test_a_part_is_delivered_only_by_the_report_record_that_names_it_and_a_post_with_none_is_a_progress_line(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the unfurl", "--kind", "answer", "--thread", "1794200000.000100", "--parts", "fix")
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
    refused = capsys.readouterr().err
    assert "record 661301's part attr names none" in refused
    assert f'marked from the root@demo session, the one its ORPHAN lines reach, with drive order posted o1 {line} --part "fix", posting nothing' in refused
    assert run("order", "posted", "o1", line) == (0, f"o1 progress line, no part delivered: {line}")
    assert state.load("demo")["orders"]["o1"]["parts"]["fix"]["posted"] is None
    assert run("order", "posted", "o1", line, "--record", "661300") == (0, f'o1 "fix": posted {line}')
    assert state.load("demo")["orders"]["o1"]["parts"]["fix"]["posted"] == line


def test_a_post_delivers_an_incidents_first_check_and_its_close_delivers_its_fix(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "the journal will not open", "--thread", "1794200000.000100", "--kind", "build", "--parts", "fix", "first check")
    with state.edit("demo") as data:
        data["orders"]["o1"]["incident"] = True
    line = slack_thread.post(state.now(), from_claude=True)
    assert cli.main(["order", "posted", "o1", line, "--part", "fix"]) == 2
    assert '--part "fix": refused, ' + state.UNPOSTED_WORK.format(order="o1", part="fix", holder="root@demo") in capsys.readouterr().err
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    report_records(monkeypatch, {"674088": {"order": "o1", "incident": "true", "part": "o1:fix"}, "674089": {"part": "fix;o1:first check"}, "674090": {"close": "o1"}})
    assert run("order", "posted", "o1", line, "--record", "674088") == (0, f"o1 progress line, no part delivered: {line}")
    with state.edit("demo") as data:
        data["orders"]["o1"]["recovered"] = state.stamp()
    assert run("order", "posted", "o1", line, "--record", "674089") == (0, f'o1 "first check": posted {line}')
    assert [part["posted"] for part in state.load("demo")["orders"]["o1"]["parts"].values()] == [None, line]
    assert run("order", "posted", "o1", line, "--record", "674090") == (0, f'o1 "fix", "first check": posted {line}')


def test_the_session_holding_an_order_closes_a_part_by_the_drives_post_that_already_answered_it(
    run: Run, demo: str, slack_thread: "Thread", capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "why does the search worker OOM?", "--kind", "answer", "--thread", "1794200000.000100", "--parts", "cause", "fix")
    answered = slack_thread.post(state.now(), from_claude=True)
    his = slack_thread.post(state.now() + timedelta(seconds=1))
    assert cli.main(["order", "posted", "o1", his, "--part", "cause"]) == 2
    assert f"{his} is not the drive's post" in capsys.readouterr().err
    assert run("order", "posted", "o1", answered, "--part", "cause") == (0, f'o1 "cause": posted {answered}, the post that answered it')
    parts = state.load("demo")["orders"]["o1"]["parts"]
    assert (parts["cause"]["posted"], parts["fix"]["posted"]) == (answered, None)
    assert run("order", "posted", "o1", answered, "--part", "fix", "--record", "670354") == (0, f'o1 "fix": posted {answered}, the post that answered it')
    assert state.load("demo")["orders"]["o1"]["parts"]["fix"]["posted"] == answered


def test_a_readers_posted_return_delivers_its_parts_and_closes_the_answer_order_once_every_part_is_posted(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "which page shows a run's history, and can it filter?", "--kind", "answer", "--parts", "the page", "its filters")
    run("job", "add", "reader", "--order", "o1", "--part", "the page", "--goal", "find the page")
    run("job", "add", "reader", "--order", "o1", "--part", "its filters", "--goal", "read its filters")
    page, filters = slack_thread.post(state.now(), from_claude=True), slack_thread.post(state.now() + timedelta(seconds=1), from_claude=True)
    monkeypatch.setattr("sys.stdin", io.StringIO(f"POSTED {page}\n"))
    run("job", "report", "j1", "-")
    order = state.load(demo)["orders"]["o1"]
    assert ({part: held["posted"] for part, held in order["parts"].items()}, order["state"] == "done") == ({"the page": page, "its filters": None}, False)
    monkeypatch.setattr("sys.stdin", io.StringIO(f"POSTED {filters}\n"))
    run("job", "report", "j2", "-")
    order = state.load(demo)["orders"]["o1"]
    assert ({part: held["posted"] for part, held in order["parts"].items()}, order["state"]) == ({"the page": page, "its filters": filters}, "done")


def test_an_answer_orders_reporter_is_told_once_its_answer_is_posted_and_never_asked_for_proof_a_fix_is_live(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "which page shows a run's history?", "--kind", "answer", "--thread", "1794200000.000100", "--reporter", REPORTED)
    run("job", "add", "reader", "--order", "o1", "--goal", "find the page")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    order = state.load(demo)["orders"]["o1"]
    assert not watch.owed_reporters(order, state.parse_time(order["updated"]) + watch.TELL_AFTER)
    note = note_in(slack_thread, "1794418000.000100")
    assert cli.main(["order", "told", "o1", note]) == 2
    assert "o1: refused, a part of its answer is not posted yet, so a note now would come before the answer: post it first" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", io.StringIO(f"POSTED {slack_thread.post(state.now(), from_claude=True)}\n"))
    run("job", "report", "j1", "-")
    order = state.load(demo)["orders"]["o1"]
    assert order["state"] != "done" and not order["proof"]
    assert watch.owed_reporters(order, state.parse_time(order["updated"]) + watch.TELL_AFTER)
    assert run("order", "told", "o1", note) == (0, f"o1: told in the reporter's thread, {note}")


def test_a_finished_job_given_a_part_it_does_not_hold_runs_and_one_given_a_part_it_holds_stays_finished(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "why does the search worker OOM?", "--parts", "cause", "fix")
    run("job", "add", "reader", "--order", "o1", "--part", "cause", "--goal", "find the cause")
    for finished in ("done", "failed"):
        with state.edit("demo") as data:
            data["jobs"]["j1"].update(state=finished, parts=["cause"])
        run("job", "set", "j1", "--part", "cause")
        assert state.load(demo)["jobs"]["j1"]["state"] == finished
        run("job", "set", "j1", "--part", "fix")
        assert state.load(demo)["jobs"]["j1"]["state"] == "running"


def test_a_report_naming_parts_delivers_each_whatever_its_posts_words_say_and_the_holder_marks_a_part_by_any_post_of_the_drive(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch
) -> None:
    status = "status of the fixes"
    parts = [status, "the intern cap is sized to the pod's memory, its own PR", "the self-retro: why the row-id change also changed the ids' printed format"]
    words = "whats the status of our fixes, size the intern cap to the pod's memory as its own PR, and do a self-retro on the id format"
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", words, "--thread", "1794200000.000100", "--kind", "answer", "--parts", *parts)
    earlier = slack_thread.post(state.now(), from_claude=True, text="Checking where each fix stands now; I will post it by 1:30 PM.")
    assert run("order", "posted", "o1", earlier, "--part", status) == (0, f'o1 "{status}": posted {earlier}, the post that answered it')
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    report_records(monkeypatch, {"681339": {"part": ";".join(f"o1:{part}" for part in parts)}})
    line = slack_thread.post(state.now() + timedelta(minutes=1), from_claude=True, text="The spill-file PR is approved but its test build failed. None has shipped yet.")
    quoted = ", ".join(f'"{part}"' for part in parts)
    assert run("order", "posted", "o1", line, "--record", "681339") == (0, f"o1 {quoted}: posted {line}")
    assert {part: held["posted"] for part, held in state.load("demo")["orders"]["o1"]["parts"].items()} == dict.fromkeys(parts, line)


def test_job_set_parts_replaces_the_parts_a_job_delivers_so_a_done_builder_given_a_part_it_did_not_build_runs_again(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    main = "an incident never goes five quiet minutes without a line"
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", main, "--parts", main, state.IMMEDIATE_FIX)
    run("job", "add", "builder", "--order", "o1", "--part", state.IMMEDIATE_FIX, "--goal", "the line every five minutes", "--agent", "a")
    with state.edit("demo") as data:
        data["jobs"]["j1"]["state"] = "done"
    assert not state.delivered(state.load("demo"), "o1", main)
    assert cli.main(["job", "set", "j1", "--parts", main, state.IMMEDIATE_FIX]) == 2
    assert f'the "{state.IMMEDIATE_FIX}" part is a job of its own' in capsys.readouterr().err
    assert run("job", "set", "j1", "--parts", main)[0] == 0
    data = state.load("demo")
    assert (data["jobs"]["j1"]["parts"], data["jobs"]["j1"]["state"], state.delivered(data, "o1", main), state.delivered(data, "o1", state.IMMEDIATE_FIX)) == ([main], "running", False, False)


def test_pr_set_part_on_a_done_builders_merged_pr_delivers_a_part_its_job_never_held(run: Run, demo: str, capsys: pytest.CaptureFixture[str]) -> None:
    main = "an incident never goes five quiet minutes without a line"
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", main, "--parts", main, state.IMMEDIATE_FIX)
    run("job", "add", "builder", "--order", "o1", "--part", state.IMMEDIATE_FIX, "--goal", "the line every five minutes", "--agent", "a")
    url = "https://github.com/yasyf/cc-skills/pull/4582"
    run("pr", "add", url, "--job", "j1", *LEAD)
    assert cli.main(["pr", "set", url, "--part", main]) == 2
    assert f"yasyf/cc-skills#4582: refused, nothing set: its job j1 delivers no part {main}" in capsys.readouterr().err
    with state.edit("demo") as data:
        data["jobs"]["j1"]["state"] = "done"
        data["prs"]["yasyf/cc-skills#4582"]["state"] = "merged"
    assert not state.delivered(state.load("demo"), "o1", main)
    assert cli.main(["pr", "set", url, "--part", "the copy"]) == 2
    assert "yasyf/cc-skills#4582: refused, nothing set: its order o1 has no part the copy" in capsys.readouterr().err
    assert run("pr", "set", url, "--part", state.IMMEDIATE_FIX, "--part", main)[0] == 0
    data = state.load("demo")
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["parts"], data["orders"]["o1"]["parts"][main]["posted"], state.delivered(data, "o1", main)) == (
        "done",
        [state.IMMEDIATE_FIX],
        url,
        True,
    )


def test_one_post_delivers_each_part_its_report_names_on_every_order_an_item_names(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the search worker", "--kind", "answer", "--thread", "1794200000.000100", "--parts", "fix", "first check")
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "raise its RAM", "--kind", "answer", "--thread", "1794200000.000100", "--parts", "increase the RAM")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    report_records(monkeypatch, {"671700": {"part": "fix;o2:increase the RAM"}})
    line = slack_thread.post(state.now(), from_claude=True)
    assert run("order", "posted", "o1", line, "--part", "fix", "--record", "671700") == (0, f'o1 "fix": posted {line}; o2 "increase the RAM": posted {line}')
    orders = state.load("demo")["orders"]
    assert (orders["o1"]["parts"]["fix"]["posted"], orders["o1"]["parts"]["first check"]["posted"], orders["o2"]["parts"]["increase the RAM"]["posted"]) == (line, None, line)


def test_order_posted_on_the_communicators_own_order_takes_the_report_its_handler_wrote_from_the_communicators_lane(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "which page shows a run's history?", "--kind", "answer", "--thread", "1794200000.000100", "--owner", "communicator")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    line = slack_thread.post(state.now(), from_claude=True)
    report_records(monkeypatch, {"664348": {"close": "o1"}})
    assert cli.main(["order", "posted", "o1", line, "--record", "664348"]) == 2
    assert (
        "refused, the report is the coordinator's and o1 is the communicator's order, so it delivers nothing there: post your own report on the cci"
        " post tool with kind report, lane communicator, attrs {close: o1} and no to, its first words as text, carrying this report's facts, and"
        " pass that seq, or post with no record, a progress line"
    ) in capsys.readouterr().err
    assert state.load("demo")["orders"]["o1"]["parts"]["Short title"]["posted"] is None
    report_records(monkeypatch, {"664350": {"close": "o1"}}, lane="communicator")
    assert run("order", "posted", "o1", line, "--record", "664350") == (0, f'o1 "Short title": posted {line}')


def test_order_set_done_refuses_the_report_order_posted_refuses_so_one_check_decides_both_verbs(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "ship the run page", "--thread", "1794200000.000100")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    line = slack_thread.post(state.now(), from_claude=True)
    report_records(monkeypatch, {"669443": {"close": "o1"}}, lane="communicator")
    refused = "refused, the report is the communicator's and o1 is the coordinator's order, so it delivers nothing there: pass the coordinator's own report on o1"
    for verb in (["order", "posted", "o1", line, "--record", "669443"], ["order", "set", "o1", "--state", "done", "--record", "669443"]):
        assert cli.main(verb) == 2
        assert refused in capsys.readouterr().err
    assert state.load("demo")["orders"]["o1"]["state"] == "open"
    report_records(monkeypatch, {"669450": {"close": "o1"}})
    assert run("order", "posted", "o1", line, "--record", "669450")[0] == 0
    assert run("order", "set", "o1", "--state", "done", "--record", "669450")[0] == 0
    assert state.load("demo")["orders"]["o1"]["state"] == "done"


def test_start_outside_an_orca_terminal_is_refused_naming_the_command_that_starts_it_in_one(
    run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.delenv("ORCA_TERMINAL_HANDLE")
    monkeypatch.delenv("TMUX")
    assert cli.main(["start", "demo", "--role", "coordinator"]) == 2
    refused = capsys.readouterr().err
    assert "drive start demo --role coordinator: refused, this session runs in no Orca terminal, so he cannot open it from Orca" in refused
    register, show = (shlex.split(line) for line in refused.partition("start the session under cci and show it in one with ")[2].split(" then "))
    assert register[:5] == ["cci", "session", "register", "demo-coordinator", "--new"]
    assert register[register.index("--cwd") + 1] == str(tmp_path)
    assert register[register.index("--resume-prompt") + 1] == "/long-running demo as the coordinator: run Start or resume for that role"
    session = shlex.split(register[register.index("--command") + 1])
    assert session[: session.index("claude")] == ["env", *(f"{key}={value}" for key, value in agents.session_settings({})["env"].items())]
    assert json.loads(session[session.index("--settings") + 1]) == agents.session_settings({})
    assert show == ["orca", "terminal", "create", "--worktree", f"path:{tmp_path}", "--title", "demo-coordinator", "--command", "cci session attach demo-coordinator", "--json"]
    assert not state.exists("demo")
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "term_outer")
    monkeypatch.setenv("TMUX", "/private/tmp/tmux-501/default,1,0")
    assert run("start", "demo", "--role", "coordinator")[0] == 0


def test_a_drive_tooling_order_keeps_his_thread_so_his_later_words_there_find_it(run: Run, demo: str) -> None:
    tooling = ["order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "Drive tooling: fix ask", "--thread", "1794378983.440769"]
    assert run(*tooling) == (0, "o1")
    assert state.load("demo")["orders"]["o1"]["thread"] == ["D123", "1794378983.440769"]
    assert state.thread_order(state.load("demo"), "1794378983.440769") == "o1"


def test_a_drive_tooling_order_his_words_already_hold_adds_none(run: Run, demo: str) -> None:
    tooling = ["order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "Drive tooling: fix ask"]
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
    filed = ["order", "add", "--title", "Short title", "--project", "p", "--workstream", "w"]
    assert cli.main([*filed, flagged]) == 2
    refused = capsys.readouterr().err
    assert refused.startswith(f"drive: refused, `cc-slack check-post` finds {'; '.join(drive_terms(flagged, []))}; ")
    assert 'a "Drive tooling:" order\'s words reach him verbatim, quoted on the line above its PR card' in refused
    assert refused.count("\n") == 1 and flagged not in refused
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


def commit_shas(text: str, asked: list[str]) -> list[str]:
    return [f"1:{found.start() + 1} commit-sha {found[0]!r}: link the commit" for found in re.finditer(r"\b[0-9a-f]{7}\b", text)]


def test_a_drive_tooling_orders_words_may_cite_his_ruling_by_its_answer_id_but_never_an_unlinked_commit(
    demo: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def shown(answer_id: str, cwd: str) -> dict:
        if answer_id != "f03a0aa":
            raise state.DriveError(f"could not read his answer {answer_id}")
        return {"body": "you need to fix the hookw henver it wrongly refuses yu"}

    monkeypatch.setattr(slack, "check_post", commit_shas)
    monkeypatch.setattr(settled, "shown", shown)
    filed = ["order", "add", "--title", "Short title", "--project", "p", "--workstream", "w"]
    assert cli.main([*filed, 'Drive tooling: check-post wrongly refuses handler posts; his ruling f03a0aa: "you need to fix the hookw henver it wrongly refuses yu"']) == 0
    assert cli.main([*filed, "Drive tooling: revert 1a2b3c4, which broke the card hook"]) == 2
    assert "finds 1:23 commit-sha '1a2b3c4': link the commit;" in capsys.readouterr().err


def test_a_drive_tooling_order_with_no_project_or_workstream_is_filed_under_the_drive_and_his_order_is_refused_naming_the_pairs_in_use(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("order", "add", "--title", "Short title", "Drive tooling: the say tool drops a table") == (0, "o1")
    assert run("order", "add", "--title", "Short title", "--workstream", "Slack cards and posts", "Drive tooling: a card drops its chart") == (0, "o2")
    assert cli.main(["order", "add", "--title", "Short title", "--project", "Iris", "ship the run page"]) == 2
    assert capsys.readouterr().err.strip() == (
        "drive: refused, no order added: name --workstream, which file his order on the dashboard's Now screen, reusing a pair in use"
        " where it fits: The drive / Drive tooling; The drive / Slack cards and posts"
    )
    orders = state.load(demo)["orders"]
    assert [(order["project"], order["workstream"]) for order in orders.values()] == [("The drive", "Drive tooling"), ("The drive", "Slack cards and posts")]


def test_a_proof_is_the_checks_link_file_or_output_never_a_claim(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "unquarantine the flaky test")
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
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "give me options for the cache layout", "--options-only")
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
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.reads, "run", recorded([]))
    for joined in ([], ["--joins", "o1"]):
        assert cli.main(["order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "give me options only", "--incident", "--parts", "fix", "--options-only", *joined]) == 2
        assert "--options-only: refused, an incident order is never limited to options, since its codex jobs start at the alert" in capsys.readouterr().err
    assert (state.load("demo")["orders"], state.load("demo")["jobs"]) == ({}, {})
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "errors on the api", "--incident", "--parts", "fix")
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "give me options for the api fix", "--parts", "fix", "--options-only")
    run("job", "add", "reader", "--order", "o2", "--goal", "research the options", "--part", "fix")
    for changed in (["o1", "--options-only"], ["o2", "--incident"]):
        assert cli.main(["order", "set", *changed]) == 2
        assert f"{changed[0]}: refused, an incident order is never limited to options" in capsys.readouterr().err
    orders = state.load("demo")["orders"]
    assert [(order["incident"], order["options_only"]) for order in orders.values()] == [(True, False), (False, True)]
    assert run("order", "set", "o2", "--incident", "--no-options-only")[0] == 0


def test_an_order_saved_before_options_only_and_once_loads_as_neither(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the watch")
    with state.locked("demo"):
        saved = state.stored("demo")
        for field in ("options_only", "once"):
            del saved["orders"]["o1"][field]
        state.store("demo", saved)
    order = state.load("demo")["orders"]["o1"]
    assert (order["options_only"], order["once"]) == (False, False)


def test_a_repair_he_asked_to_run_once_puts_up_no_pr(run: Run, demo: str, capsys: pytest.CaptureFixture[str], draws: Draws) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "rerun the backfill once", "--once")
    run("job", "add", "builder", "--order", "o1", "--goal", "run the backfill")
    assert cli.main(["pr", "add", PR, "--job", "j1", *LEAD]) == 2
    assert "refused, o1 is a repair he asked to run once: run it, and put up no PR for it" in capsys.readouterr().err
    assert state.load("demo")["prs"] == {}
    run("order", "set", "o1", "--no-once")
    assert run("pr", "add", PR, "--job", "j1", *LEAD) == (0, f"Forge-AI/monorepo#42, his card {draws.link}")


def test_status_names_the_open_incidents_and_each_running_job_whose_session_ended(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "errors on the api", "--incident", "--parts", "fix", "first check")
    run("order", "set", "o1", "--note", "record 3fa2c1")
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the watch")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "fix the watch")
    run("order", "add", "--title", "Short title", "--project", "Billing", "--workstream", "Invoices", "why is the invoice late?")
    assert run("heard") == (0, "heard")
    assert run("heard", "--away") == (0, "away")
    assert "in_use" not in json.loads(run("heard", "--json")[1])


def test_heard_reads_nothing_from_github_so_a_refusal_there_never_leaves_him_unheard(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "fix the watch")
    monkeypatch.setattr(github, "run", lambda *args, **kwargs: pytest.fail("drive heard read GitHub"))
    monkeypatch.setattr(card, "run", lambda *args, **kwargs: pytest.fail("drive heard read GitHub"))
    assert run("heard") == (0, "heard")
    assert state.load(demo)["owner"]["heard"] is not None


def test_heard_refuses_his_words_repeating_a_part_no_live_job_carries(run: Run, demo: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "ship the unfurl fix and its check", "--parts", "fix", "check")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "ship the unfurl fix and its check", "--parts", "fix", "check")
    run("job", "add", "design", "--order", "o1", "--part", "fix", "--goal", "pick how the card unfurls")
    run("job", "set", "j1", "--card", DESIGN_CARD)
    with state.edit("demo") as data:
        data["jobs"]["j1"]["state"] = "done"
    question = {"question": "Which unfurl?", "options": ["Bot", "Link"], "recommended": "Bot", "asked": state.stamp(), "url": DESIGN_CARD, "card": {"title": "Which unfurl?"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [question], "pr_cards": [], "closed": []})
    view.refresh_cards("demo")
    assert run("heard", "--repeats", "o1", "--part", "fix")[1].splitlines()[0] == "heard"
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][-1]["closed"] = state.new_close("answered", "Bot", DESIGN_CARD)
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
    run("order", "add", "--title", "Short title", "--project", "Billing", "--workstream", "Invoices", "the invoice run fails")
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
        f"1. his word {his} recorded on the coordinator; demo-coordinator registered: cci session run keeps session {held['session']} running in tmux",
        (
            '2. cci post --lane communicator --kind ask --to root@demo --text "Moving you into an Orca terminal on his word: run cc-slack'
            " unfollow for each of your Slack watches, cci unsubscribe, stop your drive watch Monitor if you run one, reply released, and"
            ' take no more work." and wait for its reply'
        ),
        "3. orca terminal send --terminal term_coordinator --text /exit --enter",
        "4. wait until ps -p 4242 lists nothing",
        f"then steps 5 to 7 of drive manual migration, Into an Orca terminal: cci session run resumes {held['session']} in tmux session demo-coordinator; create the terminal that shows it",
    ]


def test_drive_start_and_drive_move_bind_a_role_to_the_drives_checkout_at_its_default_branchs_head_wherever_its_session_ran(
    run: Run, demo: str, tmp_path: Path, ccx_worktrees: Path, registered: list[tuple[str, str, str]], slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch
) -> None:
    head = cloned(tmp_path, demo)
    clone, kept = tmp_path / "clone", ccx_worktrees / "drive-demo"
    session = state.load(demo)["roles"]["coordinator"]["session"]
    monkeypatch.chdir(clone)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "communicator-1")
    code, out = run("start", demo, "--role", "communicator")
    assert (code, state.load(demo)["roles"]["communicator"]["cwd"], registered[-1]) == (0, str(kept), ("demo-communicator", str(kept), "communicator-1"))
    assert f"this session runs in {clone} until it next exits, when cci resumes it in the drive's checkout {kept}\n" in out
    assert git_in(kept, "rev-parse", "HEAD") == head
    monkeypatch.setattr(orca, "claude_pids", lambda session: [])
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    his = slack_thread.post(state.now() - timedelta(minutes=1), from_user=True)
    assert run("move", "coordinator", "--his-word", his)[0] == 0
    assert (state.load(demo)["roles"]["coordinator"]["cwd"], registered[-1]) == (str(kept), ("demo-coordinator", str(kept), session))


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


def test_root_names_the_install_whose_hooks_and_lib_a_proof_file_imports(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="0"):
        cli.main(["--root"])
    root = Path(capsys.readouterr().out.strip())
    assert (root / "hooks" / "bash_guard.py").is_file() and root / "lib" / "drive" == Path(drive.__file__).resolve().parent


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
        assert set(FLAG.findall(re.sub(r"`[a-z][^`\s]*\s[^`]*`", "", line))) - UNLISTED == flags(parser), verb
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
        "drive order set o1 --goal x",
        "drive job set j1 --state live",
        "drive job add reader --order X",
        "drive job add --order X design",
        "drive order add X --from migrated: X --due X --project X --workstream X",
    ):
        assert refusal(guess, leaves)
    for mention in ("drive order add X --from slack: X / X --due X --project X --workstream X --title X", "drive order add --due", "drive job add --order X"):
        assert refusal(mention, leaves) is None


def test_drive_manual_prints_only_the_rows_a_word_names(run: Run) -> None:
    code, out = run("manual", "coordinator", "tap")
    assert code == 0
    assert [line.split("|")[1].strip() for line in out.splitlines()] == ["`TAP Approve …`", "`TAP Hold …`"]
    assert len(out) < len((cli.PAGES / "coordinator.md").read_text()) / 10
    assert run("manual", "coordinator", "nothing-names-this")[0] == 2
    code, out = run("manual", "coordinator", "An order, in an owner record")
    assert code == 0
    assert [line.split("|")[1].strip() for line in out.splitlines()] == ["An order, in an `owner` record"]


def test_drive_manual_prints_the_bullet_carrying_words_no_row_names(run: Run) -> None:
    code, out = run("manual", "incidents", "hand repair")
    assert code == 0
    assert "never under `drive break-glass` from this Mac" in " ".join(out.split())
    assert len(out) < len((cli.PAGES / "incidents.md").read_text()) / 10


def test_page_parts_prints_each_part_carrying_the_words_once() -> None:
    page = "\n".join(
        [
            "# Page",
            "",
            "## Clocks",
            "",
            "- A bullet naming a hand",
            "  repair across a wrap.",
            "- A bullet with no match.",
            "  - A nested bullet on a hand repair.",
            "",
            "### Deeper",
            "",
            "Text under the deeper heading.",
            "",
            "## Hand repair",
            "",
            "```sh",
            "# not a heading",
            "```",
            "",
            "## Later",
            "",
            "| row | a hand repair in its second cell |",
            "",
            "A paragraph about a hand",
            "repair.",
            "",
            "A `hand` repair in code marks.",
        ]
    )
    assert cli.page_parts(page, "HAND  repair") == [
        "- A bullet naming a hand\n  repair across a wrap.",
        "  - A nested bullet on a hand repair.",
        "## Hand repair\n\n```sh\n# not a heading\n```",
        "| row | a hand repair in its second cell |",
        "A paragraph about a hand\nrepair.",
        "A `hand` repair in code marks.",
    ]
    assert cli.page_parts(page, "clocks") == cli.page_parts(page, "`clocks`") == ["\n".join(page.splitlines()[2:12])]
    assert cli.page_parts(page, "absent words") == []


def test_drive_manual_with_no_page_lists_the_pages(run: Run) -> None:
    pages = sorted(path.stem for path in cli.PAGES.glob("*.md"))
    assert run("manual") == (0, "\n".join(["drive manual <page> prints one of these pages:", *(f"- {page}" for page in pages)]))


DESIGN_CARD = "https://acme.slack.com/archives/D123/p1794300000000100"
TAP_CARD = "https://acme.slack.com/archives/D123/p1794300000000200"
PICK = "https://acme.slack.com/archives/D123/p1794300000000300?thread_ts=1794300000.000100"
WORDS = "Iris proposes with one new tool, guided by each topology's use-when line"
STACKED = "https://github.com/Forge-AI/monorepo/pull/43"
ANSWERED = ("card-answer", "--card", DESIGN_CARD, "--label", WORDS, "--settled", "--other", "--via", PICK)
BACKFILL, ASK_JEV, UNSAID = settled.backfill, settled.jev, settled.unsaid


def cards_asking(monkeypatch: pytest.MonkeyPatch, *prs: str) -> list[tuple[str, ...]]:
    question = {"question": "Which topology?", "options": ["Tree", "Ring"], "recommended": "Tree", "asked": state.stamp(), "url": DESIGN_CARD, "card": {"title": "Which topology?", "waits": "demo:j2-7"}}
    heads = [{"url": url, "head": state.load("demo")["prs"][state.pr_key(url)]["head"]} for url in prs]
    tap = {"asked": state.stamp(), "url": TAP_CARD, "prs": list(prs), "card": {"title": "New: land it?", "waits": "merge:Forge-AI:monorepo:42", "prs": heads}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [question], "pr_cards": [tap], "closed": []})
    view.refresh_cards("demo")
    with state.edit("demo") as data:
        for url in prs:
            data["prs"][state.pr_key(url)]["stage"] = {"name": "card", "owner": "him", "why": "green, waits on his Approve", "since": state.stamp(), "woke": None}
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
    assert f"  {designing}  tap  j1 o1  land it  {TAP_CARD}" in listed
    assert cli.main(["answer", "a1", "--stands", "demo:j2-7"]) == 2
    refusal = capsys.readouterr().err
    assert "demo:j2-7: not a card, order, part or PR that a1 has not marked" in refusal
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


def test_his_answer_on_a_card_asking_him_to_lift_a_pick_lists_each_open_order_the_pick_binds_and_dropping_it_stops_its_job_and_supersedes_the_pick(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], recorded: Answers, closed: list[str]
) -> None:
    run("order", "add", "--title", "Isolate helper scripts", "--project", "Iris", "--workstream", "Durable agents", "isolate helper scripts in their own process with a memory cap")
    run("job", "add", "builder", "--order", "o2", "--goal", "build the separate process once the four PRs merge")
    with state.edit("demo") as data:
        data["jobs"]["j2"]["questions"][0]["unsettled"] = "9992100: he ruled the wait on o2's separate process; this card asks whether to build it now"
        data["jobs"]["j3"].update(state="waiting", agent="agent-3")
    recorded.shown["9992100"] = {"id": "9992100", "title": "Still build the separate process now?", "body": "Wait for the four PRs to merge\nNotes: Binds order o2", "tags": [], "created_at": "2026-11-10T00:45:49Z"}
    superseding: list[tuple[str, str]] = []
    monkeypatch.setattr(settled, "supersede", lambda pick, answer_id, cwd: superseding.append((pick, answer_id)))
    cards_asking(monkeypatch, PR)
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    listed = capsys.readouterr().err
    assert "1 order and 1 PR have no mark:" in listed
    assert "  o2  order  j3 o2  Isolate helper scripts" in listed
    assert "  o1  order" not in listed
    assert "  j3 waits on o2 and has not heard a1: " in listed
    code, out = run("answer", "a1", "--dropped", "o2", "--superseded", f"{designing}=stop")
    assert (code, out.splitlines()[:4]) == (
        0,
        [
            "his answer a1 recorded: 0 standing, 1 dropped, 1 superseded, 0 reopened by it",
            "his pick 9992100, which bound o2, is superseded by a1 in cc-notes, so ccn answer match no longer reads it as standing",
            "o2 dropped by his answer a1",
            "j3 stopped with its order: end its agent, or its lane with drive job end j3",
        ],
    )
    data = state.load("demo")
    assert (data["orders"]["o2"]["state"], data["orders"]["o2"]["withdrawn"], data["jobs"]["j3"]["state"], superseding) == ("dropped", PICK, "stopped", [("9992100", "a1")])


def test_his_answer_under_a_pr_card_lists_the_order_a_pick_binds_when_the_record_tool_ended_the_card_asking_to_lift_it_with_his_words(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], recorded: Answers
) -> None:
    under_tap = "https://acme.slack.com/archives/D123/p1794300000000400?thread_ts=1794300000.000200"
    run("order", "add", "--title", "Isolate helper scripts", "--project", "Iris", "--workstream", "Durable agents", "isolate helper scripts in their own process with a memory cap")
    run("job", "add", "builder", "--order", "o2", "--goal", "build the separate process once the four PRs merge")
    with state.edit("demo") as data:
        question = data["jobs"]["j2"]["questions"][0]
        question["unsettled"] = "9992100: he ruled the wait on o2's separate process; this card asks whether to build it now"
        question["closed"] = state.new_close("answered", WORDS, under_tap, seq="#691445", ruling="a1")
        data["jobs"]["j3"].update(state="waiting", agent="agent-3")
    recorded.shown["9992100"] = {"id": "9992100", "title": "Still build the separate process now?", "body": "Wait for the four PRs to merge\nNotes: Binds order o2", "tags": [], "created_at": "2026-11-10T00:45:49Z"}
    cards_asking(monkeypatch, PR)
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", under_tap]) == 2
    listed = capsys.readouterr().err
    assert "  o2  order  j3 o2  Isolate helper scripts" in listed
    assert "  j3 waits on o2 and has not heard a1: " in listed


def test_an_answer_with_no_id_records_his_words_in_cc_notes_first_and_goes_on_by_its_id_whatever_prefix_names_it(
    run: Run, designing: str, recorded: Answers, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cards_asking(monkeypatch, PR)
    recorded.shown["beb09e5"] = {"id": "beb09e5f00d", "title": "Which topology?", "body": WORDS, "tags": [], "created_at": "2026-11-12T11:33:00Z"}
    added: list[tuple[list[str], str]] = []

    def ccn(argv: list[str], input: str, **_: object) -> subprocess.CompletedProcess[str]:
        added.append((argv, input))
        return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "beb09e5f00d"}), "")

    monkeypatch.setattr("drive.settled.subprocess", types.SimpleNamespace(run=ccn, TimeoutExpired=subprocess.TimeoutExpired))
    assert cli.main(["answer", "--words", WORDS, "--link", PICK]) == 2
    assert "--title" in capsys.readouterr().err
    assert cli.main(["answer", "--title", "Which topology?", "--words", WORDS, "--link", PICK, "--label", "header:topology"]) == 2
    assert "his answer beb09e5f00d " in capsys.readouterr().err
    labels = ["--label=scope:durable", "--label=drive:demo", "--label=header:topology"]
    assert added == [(["ccn", "answer", "add", "--title=Which topology?", "--body-file", "-", *labels, "--json"], WORDS)]
    assert run("answer", "beb09e5", "--others-stand")[0] == 0
    assert list(state.load("demo")["answers"]) == ["beb09e5f00d"]


def test_j3986_his_ruling_kept_under_its_full_id_is_found_by_any_prefix_and_never_kept_twice(
    run: Run, designing: str, recorded: Answers, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cards_asking(monkeypatch, PR)
    ruling = "bf042d636954f67a80ca7fd1fc26ddfdbfb1527d"
    for typed in (ruling, ruling[:12], ruling[:7]):
        recorded.shown[typed] = {"id": ruling, "title": "Which topology?", "body": WORDS, "tags": [], "created_at": "2026-11-13T20:19:36Z"}
    assert cli.main(["answer", ruling, "--words", WORDS, "--link", PICK]) == 2
    capsys.readouterr()
    assert cli.main(["answer", ruling[:12], "--words", WORDS, "--link", PICK]) == 2
    assert f"{ruling} is recorded already, at {PICK}, so --words and --link would change nothing" in capsys.readouterr().err
    assert run("answer", ruling[:7], "--others-stand")[0] == 0
    assert list(state.load("demo")["answers"]) == [ruling]


def test_an_answer_is_recorded_only_as_his_words_at_the_permalink_it_names(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], heard: Heard
) -> None:
    cards_asking(monkeypatch, PR)
    added: list[list[str]] = []
    monkeypatch.setattr(
        "drive.settled.subprocess",
        types.SimpleNamespace(run=lambda argv, **_: added.append(argv) or subprocess.CompletedProcess(argv, 0, json.dumps({"id": "beb09e5f00d"}), ""), TimeoutExpired=subprocess.TimeoutExpired),
    )
    heard.refusal = "his words at the link do not say it"
    assert cli.main(["answer", "--title", "Which topology?", "--words", "a drafted reading of his question", "--link", PICK]) == 2
    assert "refused, nothing recorded: his words at the link do not say it" in capsys.readouterr().err
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    assert "a1: refused, nothing recorded: his words at the link do not say it" in capsys.readouterr().err
    assert (added, "a1" in state.load("demo")["answers"]) == ([], False)
    assert heard.checked == [("a drafted reading of his question", [PICK]), (WORDS, [PICK])]


@pytest.mark.parametrize(
    ("words", "links", "said", "refusal"),
    [
        (WORDS, [PICK], f"{WORDS}.", None),
        ("Iris proposes … use-when line", [PICK], "<https://example.com|Iris> *proposes* with one new tool, guided by each topology's use-when line", None),
        ("Iris proposes [one way:] two new tools", [PICK], WORDS, f'his words at {PICK} do not say "two new tools"'),
        ("Iris should propose topologies itself", [PICK], WORDS, f'his words at {PICK} do not say "Iris should propose topologies itself"'),
        (WORDS, [PICK], None, f"{PICK} is neither his own message nor a card he answered"),
        (WORDS, [], WORDS, f'no permalink names where he said "{WORDS}"'),
        ("Tree", [DESIGN_CARD], "Tree", None),
        ("Tree", [DESIGN_CARD], "withdrawn", f"{DESIGN_CARD} is neither his own message nor a card he answered"),
        ("Tree", [DESIGN_CARD], "open", f"{DESIGN_CARD} is neither his own message nor a card he answered"),
    ],
)
def test_words_count_as_his_answer_only_when_his_message_or_card_answer_at_the_link_holds_each_piece(
    slack_thread: Thread, words: str, links: list[str], said: str | None, refusal: str | None
) -> None:
    card = {"waits": "demo:j2-7", "url": DESIGN_CARD, "answer": {"label": "Tree", **({"withdrawn": True} if said == "withdrawn" else {})}}
    found = view.NO_CARDS | {"closed": [] if said == "open" else [card]}
    slack_thread.messages += [
        {"ts": "1794300000.000100", "text": "Which topology?", "from_user": False},
        {"ts": "1794300000.000300", "thread_ts": "1794300000.000100", "text": said or "a line the drive posted", "from_user": said is not None},
    ]
    unsaid = UNSAID([words], links, found, {"jobs": {}})
    assert (unsaid is None) if refusal is None else (unsaid.startswith(refusal) and unsaid.endswith(f"; {settled.HIS_ANSWER}"))


NEXT_PICK = "https://acme.slack.com/archives/D123/p1794300000000400?thread_ts=1794300000.000100"
ROOTS = [f"https://acme.slack.com/archives/D123/p17943000000006{n}" for n in ("00", "10")]


@pytest.mark.parametrize(
    ("links", "refusal"),
    [
        ([PICK, NEXT_PICK], None),
        ([DESIGN_CARD, PICK, NEXT_PICK], None),
        (ROOTS, None),
        ([PICK], f'his words at {PICK} do not say "reuse the blob pointer"'),
        ([PICK, ROOTS[1]], f"his messages at {PICK}, {ROOTS[1]} sit in more than one thread"),
        ([PICK, "https://acme.slack.com/archives/C456/p1794300000000400?thread_ts=1794300000.000100"], "sit in more than one thread"),
        ([PICK, "terminal"], "sit in more than one thread"),
    ],
)
def test_his_messages_in_a_row_are_one_answer_read_joined_in_link_order_only_in_one_thread_or_at_one_channels_root(
    slack_thread: Thread, links: list[str], refusal: str | None
) -> None:
    said = ("Why do we have text and file pointers?", "just reuse the blob pointer")
    slack_thread.messages += [
        {"ts": "1794300000.000100", "text": "Which topology?", "from_user": False},
        *({"ts": f"1794300000.000{n}", "thread_ts": "1794300000.000100", "text": text, "from_user": True} for n, text in zip(("300", "400"), said, strict=True)),
        *({"ts": f"1794300000.000{n}", "text": text, "from_user": True} for n, text in zip(("600", "610"), said, strict=True)),
    ]
    found = view.NO_CARDS | {"closed": [{"waits": "demo:j2-7", "url": DESIGN_CARD, "answer": {"label": "Tree"}}]}
    unsaid = UNSAID(["Tree … text and file pointers … reuse the blob pointer" if DESIGN_CARD in links else "text and file pointers … reuse the blob pointer"], links, found, {"jobs": {}})
    assert (unsaid is None) if refusal is None else (refusal in unsaid and unsaid.endswith(f"; {settled.HIS_ANSWER}"))


def test_his_messages_in_a_row_are_one_answer_kept_at_the_first_link_and_a_kept_answer_refuses_more_words_or_links(
    run: Run, designing: str, recorded: Answers, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], heard: Heard
) -> None:
    cards_asking(monkeypatch, PR)
    words = "text and file pointers … reuse the blob pointer"
    assert cli.main(["answer", "a1", "--words", words, "--link", PICK, NEXT_PICK]) == 2
    assert "his answer a1 " in capsys.readouterr().err
    assert heard.checked == [(words, [PICK, NEXT_PICK]), ("", [PICK, NEXT_PICK])]
    kept = state.load("demo")["answers"]["a1"]
    assert (kept["words"], kept["link"]) == (words, PICK)
    for again in (["--words", words], ["--link", NEXT_PICK]):
        assert cli.main(["answer", "a1", *again]) == 2
        assert f"a1 is recorded already, at {PICK}, so --words and --link would change nothing" in capsys.readouterr().err
    assert state.load("demo")["answers"]["a1"] == kept


@pytest.mark.parametrize(("how", "refusal"), [("answered", None), ("withdrawn", f"{DESIGN_CARD} is neither his own message nor a card he answered")])
def test_his_words_on_a_card_cc_slack_forgot_are_read_from_the_question_the_drive_ended_on_them(slack_thread: Thread, how: str, refusal: str | None) -> None:
    slack_thread.messages.append({"ts": "1794300000.000100", "text": "Which topology?", "from_user": False})
    question = {"waits": "demo:j2-7", "card": DESIGN_CARD, "closed": state.new_close(how, "Tree", DESIGN_CARD)}
    unsaid = UNSAID(["Tree"], [DESIGN_CARD], view.NO_CARDS, {"jobs": {"j2": {"questions": [question]}}})
    assert (unsaid is None) if refusal is None else unsaid.startswith(refusal)


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


def test_jev_is_asked_in_process_through_spawnllm_decide_sync_and_a_failed_call_or_a_declined_question_is_unheard_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, dict, dict]] = []
    results = [
        spawnllm.Decision({"decides": spawnllm.LabelAnswer("none", {"none": 0.9}, 0.9)}, "jev-1.13.0", 12, 40.0),
        spawnllm.DecideError(400, "unknown question type"),
        TimeoutError("no jev decision within 20s"),
        spawnllm.Decision({"decides": spawnllm.Refused()}, "jev-1.13.0", 12, 40.0),
    ]

    def decide_sync(state: object, questions: dict, **options: object) -> spawnllm.Decision:
        calls.append((state, questions, options))
        if isinstance(result := results.pop(0), Exception):
            raise result
        return result

    monkeypatch.setattr(spawnllm, "decide_sync", decide_sync)
    monkeypatch.setenv("TYPESAFE_API_KEY", "jev-key")
    settled.jev_key.cache_clear()
    request = {"state": {"question": "Which store?"}, "questions": {"decides": {"label": {"instructions": "Which?", "options": {"none": "No"}}}}}
    assert ASK_JEV(request) == {"decides": {"choice": "none", "probabilities": {"none": 0.9}, "confidence": 0.9}}
    failures = []
    for _ in range(3):
        with pytest.raises(settled.Unheard) as unheard:
            ASK_JEV(request)
        failures.append(str(unheard.value))
    settled.jev_key.cache_clear()
    assert [(seen, questions, options["provider"], options["api_key"]) for seen, questions, options in calls] == [
        ({"question": "Which store?"}, {"decides": spawnllm.Label("Which?", {"none": "No"})}, spawnllm.JEV, "jev-key")
    ] * 4
    assert all(19 < options["timeout"] <= settled.DECIDE_SECONDS for _, _, options in calls)
    assert failures == ["decision failed with HTTP 400: unknown question type", "no jev decision within 20s", "Jev declined the question"]


AGENT_START = """ASK Where does an agent a chat starts with agent::start run, and how does it start?
A chat's agent::start call starts a child agent. Today a child of a chat runs inside the chat's own process on his Mac, while a child of a cloud agent starts through the api and runs on its Env's pod.
Recommended: one api start for every agent, so a chat's child starts and runs like any other agent, with a parent tag the UI reads.
- One api start: no runner on any host (recommended)
  agent::start in `go/sand/internal/iris/agents.go` calls the one api start every agent takes, a chat's child included; its Env's pod runs it like any other agent and the UI groups it under its parent by its parent tag.
- The Mac builds a chat's children: a child of a chat runs in the chat's process
  agent::start keeps a chat's branch: a chat's child runs inside `sand iris serve` on his Mac beside its parent, and stops when the Mac sleeps; a cloud agent's child keeps the api start.
- A pod per chat: each chat gets a pod its children run on
  A chat that starts its first child gets a pod of its own in `infra/rows/executor/executor-template.ts`, and its children run there; other agents keep their api start.

Findings:
Root cause: agent::start branches on whether its caller is a chat, so a chat's child takes a start path, host and limits of its own that no other agent has
First principles: One api start
(1) `go/sand/internal/iris/agents.go` holds the branch.
"""
NOT_SPECIAL = {
    "id": "p1794200000000200",
    "asked": "why can a chat's subagent not start subagents of its own?",
    "said": "how many times do I have to say subagents aren't special they're like any other agent other than in UI so there should be no hard coded thing preventing them from starting subagents",
}


def test_jev_reads_every_option_of_a_card_in_one_call_against_his_words_and_the_card_against_his_rulings_and_prints_only_what_it_is_eight_in_ten_sure_they_rule_out(
    jev: Jev, capsys: pytest.CaptureFixture[str]
) -> None:
    asked = limits.question(AGENT_START, kept=True)
    case = limits.ask_case(AGENT_START, asked)
    jev.picks(NOT_SPECIAL["id"], 0.79)
    settled.judged(case, asked, [NOT_SPECIAL], [], {})
    assert capsys.readouterr().err == ""
    [request] = jev.asked
    questions = request["questions"]
    assert list(questions) == ["breaks1", "breaks2", "breaks3", "worked1", "worked2", "worked3", "untapped", "card"]
    assert list(questions["breaks2"]["label"]["options"]) == ["none", NOT_SPECIAL["id"], "ruling:32f49af"]
    assert list(questions["worked2"]["label"]["options"]) == ["none", limits.WORKED_WORDS["id"]]
    assert list(questions["card"]["label"]["options"]) == ["none", "ruling:0c59046", "ruling:32f49af"]
    assert '"One api start: no runner on any host" (recommended)' in questions["breaks1"]["label"]["instructions"]
    assert f'The option: "{asked.options[2]}".' in questions["worked3"]["label"]["instructions"]
    assert "Option 1 (recommended): One api start: no runner on any host -- agent::start in" in request["state"]["card"]
    assert request["state"]["his_words"] == [NOT_SPECIAL, limits.ROOT_CAUSE_WORDS | {"rule": limits.ROOT_CAUSE_RULES["found"]}, limits.TEAM_DATA_WORDS, limits.WORKED_WORDS]
    jev.picks("none")
    jev.breaks("breaks2", NOT_SPECIAL["id"])
    settled.judged(case, asked, [NOT_SPECIAL], [], {})
    assert capsys.readouterr().err.startswith(f'drive job ask: Jev reads option "{asked.options[1]}" as breaking {NOT_SPECIAL["id"]}, {NOT_SPECIAL["asked"]}, "{NOT_SPECIAL["said"]}". This finding refuses no card')
    settled.judged(case, asked, [], [], {})
    assert list(jev.asked[-1]["questions"]) == ["breaks1", "breaks2", "breaks3", "worked1", "worked2", "worked3", "card"]


def test_his_ruling_on_root_causes_is_judged_once_on_the_cards_recommendation_beside_its_simplest_line_and_only_advises(jev: Jev, capsys: pytest.CaptureFixture[str]) -> None:
    flagged = AGENT_START.replace("First principles: One api start\n", "First principles: One api start\nSimplest: The Mac builds a chat's children\n")
    asked = limits.question(flagged, kept=True)
    case = limits.ask_case(flagged, asked)
    assert case.splitlines()[-3:] == [limits.ROOT_CAUSE.search(flagged)[0], "First principles: One api start", "Simplest: The Mac builds a chat's children"]
    assert "speaks only to the option the card recommends" in settled.CARD_QUESTION
    assert "root causes" not in settled.BREAKS_QUESTION
    jev.breaks("card", "ruling:0c59046")
    settled.judged(case, asked, [NOT_SPECIAL], [], {})
    assert all("ruling:0c59046" not in question["label"]["options"] for key, question in jev.asked[-1]["questions"].items() if key != "card")
    advice = capsys.readouterr().err
    assert advice.startswith(f'drive job ask: Jev reads the card as breaking ruling:0c59046, his ruling on root causes, "{limits.ROOT_CAUSE_QUOTE}". Its rule: {limits.ROOT_CAUSE_RULES["found"]}')
    assert "This finding refuses no card, by his pick 320b7a7" in advice


PROOF_IDENTITY = """ASK Which identity should start the cloud agent of a production proof that a release build runs?
Drive tooling: your pick decides whose name starts each production proof's cloud agent, and what is built before a production cloud agent can prove a fix.
I recommend the existing smoke session in a new release-build mode: no new principal or permission. Meanwhile nothing is built.
Show me:
What the production API records as each proof's starter, and what must be built first
| Option | Starter the API records | Built first |
|---|---|---|
| Smoke session, new mode | the smoke team owner, its session minted by the release build | a release-build mode |
| Smoke Workflow | the Workflow run, as its Env | agent::start and its cloud-runner driver |
- Smoke owner's session in a new release-build proof mode: ready once the mode ships; the starter reads the smoke owner (recommended)
  A new release-build mode mints the smoke team owner's session through the existing smoke-session route, starts the cloud agent with the given first message, reads its chat back and archives it.
- A smoke Workflow starts the agent as its Env: the agent names no person; waits for the unbuilt agent::start Sand call
  The same mode invokes a smoke project Workflow that calls agent::start, so the run is the agent's starter and it runs as its Env; that call and its driver ship first.
Findings:
Root cause: startCloudAgent admits only a signed-in person's session, the only one a machine can get in production is the smoke team owner's, and no release mode starts a cloud agent, so every proof fell back to a person's sign-in
First principles: Smoke owner's session in a new release-build proof mode
Holds: none: no option changes code any job builds
"""
PROOF_IDENTITY_REDRAWN = """ASK Which identity should start the cloud agent of a production proof that a release build runs?
Drive tooling: your pick decides whose session each production proof's cloud agent and page pictures run under, and what is built before a production cloud agent can prove a fix.
A customer team's page is shown on a smoke-team run instead. I recommend a test account. Meanwhile nothing is built.
Show me:
Which session each proof holds, whom the audit rows name, and how a page in a customer team is proven
| Option | Session the proof holds | Starter the API records | Page in a customer team |
|---|---|---|---|
| Test account | a smoke team test member's, minted by the release build | the test account | the same page on a smoke-team run |
| Owner session | the smoke owner's, a platform admin's | the smoke owner | the same stand-in |
- A dedicated smoke test account in a new release-build proof mode: neither a person nor an admin (recommended)
  Each production smoke team gains a test member, which releases create through the existing members route; the mode mints its session, starts the cloud agent, reads its chat, pictures its pages and archives the agent.
- The smoke owner's pipeline-minted session: no env change, but each proof holds a platform admin's session
  The same mode mints the smoke owner session the release smoke already uses, a platform admin who may act as any user of any team, so proof code holds that power.
Findings:
Root cause: startCloudAgent admits only a signed-in person's session, the only one a machine can get in production is the smoke team owner's, a platform admin, and no release mode starts a cloud agent or pictures a page, so every proof fell back to a person's sign-in
First principles: A dedicated smoke test account in a new release-build proof mode
Holds: none: no option changes code any job builds
"""


def test_a_redrawn_card_is_judged_against_the_rulings_its_first_draft_was_never_his_words_on_another_area(jev: Jev, capsys: pytest.CaptureFixture[str]) -> None:
    jev.breaks("card", "ruling:a99b78b")
    offered = []
    for text in (PROOF_IDENTITY, PROOF_IDENTITY_REDRAWN):
        asked = limits.question(text, kept=True)
        settled.judged(limits.ask_case(text, asked), asked, [], [], {})
        assert capsys.readouterr().err == ""
        request = jev.asked[-1]
        offered.append((list(request["questions"]["card"]["label"]["options"]), request["state"]["his_words"]))
    assert offered == [(["none", "ruling:0c59046", "ruling:32f49af"], [limits.ROOT_CAUSE_WORDS | {"rule": limits.ROOT_CAUSE_RULES["found"]}, limits.TEAM_DATA_WORDS, limits.WORKED_WORDS])] * 2
    assert "story" not in settled.CARD_QUESTION


def test_jev_names_the_recorded_answer_or_ruling_it_picks_for_the_recommended_option_at_least_0_8_sure_the_cited_answer_the_card_says_it_goes_against_first(
    jev: Jev, capsys: pytest.CaptureFixture[str]
) -> None:
    text = AGENT_START.replace("(1) `go/sand", f"{DEPARTS}\n(1) `go/sand")
    asked = limits.question(text, kept=True)
    case = limits.ask_case(text, asked, frozenset({"7a1c0de"}))
    assert [line for line in case.splitlines() if line != DEPARTS] == limits.ask_case(text, asked).splitlines()
    assert DEPARTS in case.splitlines() and "holds the branch" not in case
    cited = {"id": "7a1c0de", "asked": NIGHTLY_EXPORT["title"], "said": NIGHTLY_EXPORT["body"]}
    assert settled.judged(case, asked, [NOT_SPECIAL], [cited], {})[0] is None
    assert (jev.asked[-1]["questions"]["says"]["label"]["instructions"], cited in jev.asked[-1]["state"]["his_words"]) == (settled.SAYS_QUESTION, True)
    jev.breaks("breaks1", NOT_SPECIAL["id"])
    assert settled.judged(case, asked, [NOT_SPECIAL], [cited], {})[0] is None
    jev.breaks("breaks1", "ruling:32f49af")
    assert settled.judged(case, asked, [NOT_SPECIAL], [cited], {})[0] == limits.TEAM_DATA_WORDS
    jev.breaks("says", "7a1c0de")
    assert settled.judged(case, asked, [NOT_SPECIAL], [cited], {})[0] == cited
    capsys.readouterr()
    jev.ruled = {}
    jev.picks("7a1c0de", 0.55)
    assert settled.judged(case, asked, [NOT_SPECIAL], [cited], {})[0] is None
    assert capsys.readouterr().err == ""
    jev.picks("7a1c0de", 0.8)
    assert settled.judged(case, asked, [NOT_SPECIAL], [cited], {})[0] == cited


def test_job_ask_says_on_stderr_that_jev_gave_no_answer_and_sends_the_question_to_him(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], jev: Jev
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    jev.answer = None
    assert cli.main(["job", "ask", asking, str(block)]) == 0
    unheard = capsys.readouterr().err
    assert "drive job ask: Jev gave no reading of this question against his words, so it goes to him: no jev decision within 20s" in unheard
    assert "drive job ask: Jev did not say whether this question's options differ in how a screen looks, so it goes up unchecked for screenshots" in unheard
    assert state.load("demo")["jobs"]["j1"]["questions"][0]["refused"] is None


def test_backfill_labels_as_approvals_every_tap_on_a_pr_card_by_its_question_whatever_his_words(
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
        {"id": "b8" * 20, "title": "The devbox clone token fix", "body": "Owner tapped Approve on the card", "tags": ["header:Tap", "scope:durable"]},
        {"id": "c9" * 20, "title": "Tap: #51295, the queue store moves", "body": "Tap Approve on the card", "tags": ["scope:durable"]},
    ]

    def ccn(argv: list[str], input: str, cwd: str, **_: object) -> subprocess.CompletedProcess[str]:
        calls.append([cwd, *argv])
        listed = [{**answer, "created_at": "2026-11-10T05:00:00Z"} for answer in stored]
        return subprocess.CompletedProcess(argv, 0, json.dumps(listed) if argv[2] == "list" else "{}", "")

    monkeypatch.setattr("drive.settled.subprocess", types.SimpleNamespace(run=ccn, TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(settled, "listed", SETTLED_LISTED)
    BACKFILL("/work")
    assert calls == [
        ["/work", "ccn", "answer", "list", "--label", "scope:durable", "--limit", "0", "--full", "--json"],
        ["/work", "ccn", "answer", "edit", "a1" * 20, "--add-label", "kind:approval"],
        ["/work", "ccn", "answer", "edit", "b2" * 20, "--add-label", "kind:approval"],
        ["/work", "ccn", "answer", "edit", "d4" * 20, "--add-label", "kind:approval"],
        ["/work", "ccn", "answer", "edit", "a7" * 20, "--add-label", "kind:approval"],
        ["/work", "ccn", "answer", "edit", "b8" * 20, "--add-label", "kind:approval"],
        ["/work", "ccn", "answer", "edit", "c9" * 20, "--add-label", "kind:approval"],
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


def test_an_answer_lists_every_pr_of_its_order_not_yet_live_and_a_reopened_one_goes_to_the_live_job_that_reworks_it_its_card_withdrawn(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], withdrawals: Withdrawals
) -> None:
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/44", "--job", "j1", *LEAD)
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/45", "--job", "j2", *LEAD)
    run("job", "add", "builder", "--order", "o1", "--goal", "shipped", "--waits", designing)
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/46", "--job", "j3", *LEAD)
    settled = cards_asking(monkeypatch, PR)
    withdrawals.cards = [TAP_CARD]
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
    reason = f'Being reworked to your words "{WORDS}". A fresh card asks your tap once its builder says how they are met'
    assert out.splitlines() == [
        "his answer a1 recorded: 1 standing, 0 superseded, 2 reopened by it",
        f"{designing} reworked to a1: its card stays down until its builder's READY says how his words are met",
        f"withdrew his card {TAP_CARD}: {reason}",
        f"{designing} reopened by his answer a1: j1 reworks it to his words, so brief j1 with them",
        "Forge-AI/monorepo#45 reopened by his answer a1: j1 reworks it to his words, so brief j1 with them",
    ]
    assert (code, settled, withdrawals.asked) == (0, [ANSWERED], [(PR, reason)])
    data = state.load("demo")
    reworked = data["prs"][designing]
    assert (reworked["wants_tap"], reworked["tap"], data["jobs"]["j1"]["state"]) == (True, None, "running")
    assert reworked["rework"] | {"at": None} == {"by": "a1", "words": WORDS, "at": None}
    assert data["answers"]["a1"]["cards"]["Forge-AI/monorepo#45"] == {"mark": "reopened", "job": "j1", "card": None}


def test_his_answer_reopening_the_top_pr_of_a_two_pr_stack_card_cards_the_pr_it_leaves_standing_alone_and_never_hands_his_tap_on_it_to_the_reworked_one(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, withdrawals: Withdrawals, native_stacks: dict[str, list[tuple[str, str]]]
) -> None:
    top, top_url = "Forge-AI/monorepo#44", "https://github.com/Forge-AI/monorepo/pull/44"
    run("job", "add", "builder", "--order", "o1", "--goal", "rework the top", "--waits", designing)
    run("pr", "add", top_url, "--job", "j3", *LEAD)
    cards_asking(monkeypatch, PR, top_url)
    stack = [designing, top]
    with state.edit("demo") as data:
        for key in stack:
            data["prs"][key] |= {"native": stack, "stack": stack, "head": "abc", "checks": "pass", "review": "approved"}
    for key in stack:
        native_stacks[key] = [(member, "open") for member in stack]
    withdrawals.cards = [TAP_CARD]
    assert cli.main(["answer", "8213afb", "--words", WORDS, "--link", PICK]) == 2
    code, out = run("answer", "8213afb", "--stands", designing, "--reopened", f"{top}=j3")
    assert (code, withdrawals.asked) == (0, [(top_url, state.rework_reason(state.load("demo")["prs"][top]))]), out
    gone = {"waits": "merge:Forge-AI:monorepo:42-44", "title": "New: land it?", "prs": [PR, top_url], "asked": state.stamp(), "url": TAP_CARD, "tapped": "", "replaced_by": ""}
    gone["answer"] = {"at": state.stamp(), "label": state.rework_reason(state.load("demo")["prs"][top]), "withdrawn": True}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [], "closed": [gone]})
    view.refresh_cards("demo")
    assert stage.of(state.load("demo"), designing, view.live_cards("demo")).name == "uncarded"
    read = {"title": "Fix: land it", "body": CARD_BODY, "state": "OPEN", "isDraft": False, "headRefOid": "abc", "baseRefName": "main", "headRefName": "land-it", "reviewDecision": "APPROVED", "labels": []}
    monkeypatch.setattr(card, "run", lambda cmd, **_: "[]" if cmd[1] == "api" else json.dumps(read | {"url": f"https://github.com/Forge-AI/monorepo/pull/{cmd[3]}"}))
    drawn = card.cards([{"repo": "Forge-AI/monorepo", "number": 42}])
    assert [([row["url"] for row in each["card"]["prs"]], each["card"]["waits"], each["card"]["options"]) for each in drawn["cards"]] == [([PR], "merge:Forge-AI:monorepo:42", ["Approve", "Hold"])]
    assert [skipped["number"] for skipped in drawn["skipped"]] == [44]
    assert [state.load("demo")["prs"][key]["stack"] for key in stack] == [[designing], stack]


def test_an_answer_under_a_card_lists_only_its_job_and_orders_cards_and_prs_and_those_its_words_name(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cards_asking(monkeypatch, PR)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the new tool")
    run("job", "add", "builder", "--order", "o2", "--goal", "build the tool")
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/44", "--job", "j3", *LEAD)
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/45", "--job", "j3", *LEAD)
    words = f"{WORDS}; hold https://github.com/Forge-AI/monorepo/pull/45 until it lands"
    assert cli.main(["answer", "a1", "--words", words, "--link", PICK]) == 2
    listed = capsys.readouterr().err
    assert "; 2 PRs have no mark:" in listed
    assert f"  {designing}  tap  j1 o1  land it  {TAP_CARD}" in listed
    assert "  Forge-AI/monorepo#45  open  j3 o2  " in listed
    assert "#44" not in listed
    marked = run("answer", "a1", "--stands", designing, "Forge-AI/monorepo#45", "Forge-AI/monorepo#44")
    assert marked == (0, "his answer a1 recorded: 3 standing, 0 superseded, 0 reopened by it")
    assert state.load("demo")["answers"]["a1"]["cards"]["Forge-AI/monorepo#44"] == {"mark": "stands"}


def test_his_ruling_under_a_pr_card_lists_the_pr_waiting_on_his_tap_there_so_drive_answer_drops_it_and_closes_it_on_github(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], closed: list[str], send_message: Callable[..., None]
) -> None:
    settled = cards_asking(monkeypatch, PR)
    under_tap = "https://acme.slack.com/archives/D123/p1794300000000400?thread_ts=1794300000.000200"
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", under_tap]) == 2
    listed = capsys.readouterr().err
    assert "; 1 card and 1 PR have no mark:" in listed
    assert f"  {designing}  tap  j1 o1  land it  {TAP_CARD}" in listed
    with state.edit("demo") as data:
        data["jobs"]["j1"]["agent"] = "agent-1"
    assert cli.main(["answer", "a1", "--dropped", designing, "--stands", "demo:j2-7"]) == 2
    dropping = capsys.readouterr().err
    for line in (
        f"{designing}: its card {TAP_CARD} now shows his words and a link to them, with no buttons, its question withdrawn",
        f"{designing} no longer waits on his tap",
        f"{designing} closed on GitHub",
        "  j1 waits on o1 and has not heard a1: SendMessage it his words verbatim",
    ):
        assert line in dropping
    send_message("demo", "j1", WORDS)
    assert run("answer", "a1") == (0, "his answer a1 recorded: 1 standing, 1 dropped, 0 superseded, 0 reopened by it; 1 waiting job sent his words, 0 untouched")
    assert (settled, closed) == ([("card-answer", "--card", TAP_CARD, "--label", WORDS, "--withdrawn", "--via", under_tap)], [PR])
    data = state.load("demo")
    assert (data["prs"][designing]["state"], data["prs"][designing]["wants_tap"]) == ("closed", False)
    assert data["answers"]["a1"]["cards"][designing] == {"mark": "dropped", "job": "j1", "card": TAP_CARD}


def test_an_answer_stays_unfinished_until_each_job_waiting_on_an_order_it_touches_is_sent_his_words_or_marked_untouched_and_a_mark_takes_his_words_off_what_it_holds(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], send_message: Callable[..., None]
) -> None:
    cards_asking(monkeypatch, PR)
    run("job", "add", "builder", "--order", "o1", "--goal", "build the store", "--waits", designing)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the new tool")
    run("job", "add", "builder", "--order", "o2", "--goal", "build the tool")
    other = 'His answer on card demo:j9-1, verbatim: "Knock"'
    with state.edit("demo") as data:
        data["jobs"]["j3"]["state"] = data["jobs"]["j4"]["state"] = "waiting"
        data["jobs"]["j3"]["agent"] = "agent-3"
        data["jobs"]["j3"]["undelivered"] = [
            {"seq": seq, "words": words, "at": state.stamp(), "said": None, "request": f"req-{seq}"} for seq, words in (("61", f'His answer ({PICK}), verbatim: "{WORDS}"'), ("62", other))
        ]
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    listed = capsys.readouterr().err
    sender = "from root@demo, the session session-under-test that started its agent agent-3"
    assert f"  j3 waits on o1 and has not heard a1: SendMessage it his words verbatim {sender}, or mark it untouched" in listed
    assert "j4" not in listed
    assert cli.main(["answer", "a1", "--stands", designing]) == 2
    refusal = capsys.readouterr().err
    assert "stays unfinished until each job waiting on an order it touches has heard it:" in refusal
    assert f"  {designing}  tap" not in refusal
    assert "mark each his words leave as it is: drive answer a1 --untouched <job>...; any other job is marked sent by the SendMessage" in refusal
    assert cli.main(["answer", "a1", "--untouched", "j4"]) == 2
    assert "j4: not a job waiting on an order a1 touches that is unmarked" in capsys.readouterr().err
    send_message("demo", "j3", f"His answer, verbatim: {WORDS}")
    assert run("answer", "a1") == (
        0,
        "his answer a1 recorded: 1 standing, 0 superseded, 0 reopened by it; 1 waiting job sent his words, 0 untouched",
    )
    drive = state.load("demo")
    assert (drive["answers"]["a1"]["jobs"], [kept["words"] for kept in drive["jobs"]["j3"]["undelivered"]]) == ({"j3": {"mark": "sent"}}, [other])


def test_an_agent_the_top_started_on_an_order_a_sub_coordinator_now_hears_takes_his_answer_through_drive_job_resume_never_the_tops_sendmessage(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], send_message: Callable[..., None]
) -> None:
    cards_asking(monkeypatch, PR)
    run("job", "add", "builder", "--order", "o1", "--goal", "build the store", "--waits", designing)
    with state.edit("demo") as data:
        data["jobs"]["j3"].update(state="waiting", agent="agent-3")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "drive-1")
    assert run("start", "demo", "--role", "coordinator", "--project", "The drive")[0] == 0
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    assert "  j3 waits on o1 and has not heard a1: drive job resume j3 with his words verbatim, or mark it untouched" in capsys.readouterr().err
    send_message("demo", "j3", WORDS)
    assert cli.main(["answer", "a1", "--stands", designing]) == 2
    assert "  j3 waits on o1 and has not heard a1: drive job resume j3 with his words verbatim" in capsys.readouterr().err


def test_a_remote_lane_waiting_on_an_order_his_answer_touches_is_marked_sent_only_once_a_resume_delivered_his_words(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cards_asking(monkeypatch, PR)
    run("job", "add", "builder", "--order", "o1", "--goal", "build the store", "--remote", "--waits", designing)
    with state.edit("demo") as data:
        data["jobs"]["j3"].update(state="waiting", dispatch="ctx_lane0003")
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    assert "  j3 waits on o1 and has not heard a1: drive job resume j3 with his words verbatim, or mark it untouched" in capsys.readouterr().err
    assert cli.main(["answer", "a1", "--stands", designing]) == 2
    assert state.load("demo")["answers"]["a1"]["jobs"] == {"j3": None}
    with state.edit("demo") as data:
        superseded.hear(data, "j3", f"His answer, verbatim:\n\n{WORDS}\n\nAct on it.", state.now())
    assert run("answer", "a1") == (
        0,
        "his answer a1 recorded: 1 standing, 0 superseded, 0 reopened by it; 1 waiting job sent his words, 0 untouched",
    )
    assert state.load("demo")["answers"]["a1"]["jobs"] == {"j3": {"mark": "sent"}}


def test_his_answer_is_born_sent_to_a_lane_whose_brief_carried_his_words_once_he_gave_them_and_owed_to_one_whose_brief_quoted_them_before(
    run: Run, designing: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cards_asking(monkeypatch, PR)
    for goal, after in (("build the store", []), ("build the cache", ["--waits", "j3"])):
        run("job", "add", "builder", "--order", "o1", "--goal", goal, "--remote", "--waits", designing, *after)
    moment = state.now()
    with state.edit("demo") as data:
        for job_id, text, written in (("j3", f'His answers since:\n- "{WORDS}" ({PICK})\n', moment + timedelta(minutes=1)), ("j4", f"Recommended: {WORDS}\n", moment - timedelta(hours=1))):
            brief = tmp_path / f"{job_id}.md"
            brief.write_text(text)
            os.utime(brief, (written.timestamp(), written.timestamp()))
            data["jobs"][job_id].update(state="waiting", dispatch=f"ctx_lane000{job_id[1]}", brief=str(brief))
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    listed = capsys.readouterr().err
    assert ("  j3 waits on o1" in listed, "  j4 waits on o1 and has not heard a1: drive job resume j4 with his words verbatim, or mark it untouched" in listed) == (False, True)
    assert state.load("demo")["answers"]["a1"]["jobs"] == {"j3": {"mark": "sent"}, "j4": None}


def test_an_agent_set_on_a_job_whose_brief_carries_his_answer_marks_it_sent_and_takes_off_the_copy_it_held(
    run: Run, designing: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cards_asking(monkeypatch, PR)
    run("job", "add", "builder", "--order", "o1", "--goal", "build the store", "--waits", designing)
    with state.edit("demo") as data:
        data["jobs"]["j3"]["state"] = "waiting"
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    assert state.load("demo")["answers"]["a1"]["jobs"] == {"j3": None}
    brief = tmp_path / "j3.md"
    brief.write_text(f'Order (his words): build the store\nHis answers since:\n- "{WORDS}" ({PICK})\n')
    held = {"seq": "661500", "words": f'His answer on card {DESIGN_CARD}, verbatim: "{WORDS}"', "at": state.stamp(), "said": None, "request": "r-1"}
    with state.edit("demo") as data:
        data["jobs"]["j3"].update(brief=str(brief), undelivered=[held])
    run("job", "set", "j3", "--agent", "agent-3")
    drive = state.load("demo")
    assert (drive["answers"]["a1"]["jobs"], drive["jobs"]["j3"]["undelivered"]) == ({"j3": {"mark": "sent"}}, [])


def test_untouched_on_a_job_no_worker_runs_is_refused_and_his_answer_finishes_without_it_since_the_launch_that_starts_it_carries_his_words(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cards_asking(monkeypatch, PR)
    run("job", "add", "builder", "--order", "o1", "--goal", "build the store", "--waits", designing)
    with state.edit("demo") as data:
        data["jobs"]["j3"]["state"] = "waiting"
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    assert "j3 waits on o1" not in capsys.readouterr().err
    assert cli.main(["answer", "a1", "--stands", designing, "--untouched", "j3"]) == 2
    assert capsys.readouterr().err.startswith(
        "drive: j3: not a job waiting on an order a1 touches that is unmarked, with an agent or lane; a job with neither hears his words"
        " from the launch that starts it\n"
    )
    assert run("answer", "a1", "--stands", designing) == (0, "his answer a1 recorded: 1 standing, 0 superseded, 0 reopened by it")
    assert state.load("demo")["answers"]["a1"]["jobs"] == {"j3": None}


def test_an_answer_under_a_card_no_longer_open_still_waits_on_each_job_waiting_on_that_cards_order(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], send_message: Callable[..., None]
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [], "closed": []})
    view.refresh_cards("demo")
    run("job", "add", "builder", "--order", "o1", "--goal", "build the store", "--waits", designing)
    with state.edit("demo") as data:
        data["jobs"]["j3"].update(state="waiting", agent="agent-3")
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    listed = capsys.readouterr().err
    assert f"  {designing}  open  j1 o1  " in listed
    sender = "from root@demo, the session session-under-test that started its agent agent-3"
    assert f"  j3 waits on o1 and has not heard a1: SendMessage it his words verbatim {sender}, or mark it untouched" in listed
    send_message("demo", "j3", WORDS)
    assert run("answer", "a1", "--stands", designing) == (
        0,
        "his answer a1 recorded: 1 standing, 0 superseded, 0 reopened by it; 1 waiting job sent his words, 0 untouched",
    )


def test_others_stand_refuses_to_mark_more_than_a_few_unread_cards_and_prs_standing_so_each_is_marked_by_its_key(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for number in (44, 45, 46):
        run("pr", "add", f"https://github.com/Forge-AI/monorepo/pull/{number}", "--job", "j1", *LEAD)
    cards_asking(monkeypatch, PR)
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
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/44", "--job", "j1", *LEAD)
    cards_asking(monkeypatch, PR)
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the new tool")
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


def test_folding_a_superseded_prs_job_into_a_live_one_moves_its_open_pr_so_its_wakes_reach_that_job_alone(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, closed: list[str]
) -> None:
    cards_asking(monkeypatch, PR)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the new tool")
    run("job", "add", "builder", "--order", "o2", "--goal", "build the tool")
    assert run("answer", "a1", "--words", WORDS, "--link", PICK)[0] == 2
    code, out = run("answer", "a1", "--superseded", f"{designing}=j3")
    assert (code, f"{designing} now answers to j3, which its wakes, checks and lander reach" in out.splitlines()) == (0, True)
    data = state.load("demo")
    assert (data["prs"][designing]["job"], data["jobs"]["j3"]["prs"], data["jobs"]["j1"]["state"]) == ("j3", [PR], "stopped")
    assert (stage.owner_job(data, designing), closed) == ("j3", [])


def test_job_set_pr_moves_a_tracked_prs_row_to_the_job_that_took_it_over_so_its_stage_line_and_wakes_name_that_job(run: Run, with_pr: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the new tool")
    run("job", "add", "builder", "--order", "o2", "--goal", "take over the landing")
    untracked = "https://github.com/Forge-AI/monorepo/pull/43"
    assert run("job", "set", "j2", "--pr", PR, "--pr", untracked)[0] == 0
    data = state.load("demo")
    assert (data["prs"][with_pr]["job"], data["jobs"]["j2"]["prs"], stage.owner_job(data, with_pr)) == ("j2", [PR, untracked], "j2")


def test_job_set_carries_refuses_a_part_a_running_builder_carries_so_one_change_has_one_builder(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land", "--agent", "a1")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the new tool")
    run("job", "add", "builder", "--order", "o2", "--goal", "build the tool")
    assert cli.main(["job", "set", "j2", "--carries", "o1"]) == 2
    assert 'drive: j2: refused, nothing changed: j1 builder running carries "Short title" already, so a second builder would race it on one change' in capsys.readouterr().err
    assert state.load("demo")["jobs"]["j2"]["carries"] == {}
    assert run("job", "set", "j1", "--state", "stopped")[0] == 0
    assert run("job", "set", "j2", "--carries", "o1")[0] == 0
    assert state.load("demo")["jobs"]["j2"]["carries"] == {"o1": ["Short title"]}


LAYOUT_CARD = "https://acme.slack.com/archives/D123/p1794300000000500"
LAYOUT = "Which layout does the run page read?"


def another_order_asking(run: Run, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    settled = cards_asking(monkeypatch, PR)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "lay out the run page")
    run("job", "add", "design", "--order", "o2", "--goal", "pick a layout")
    found = view.cards("demo")
    layout = {
        "question": LAYOUT,
        "options": ["One snapshot file", "A map file beside each"],
        "recommended": "One snapshot file",
        "asked": state.stamp(),
        "url": LAYOUT_CARD,
        "card": {"title": LAYOUT, "waits": "demo:j3-9"},
    }
    monkeypatch.setattr(view, "cards", lambda name: found | {"questions": [*found["questions"], layout]})
    view.refresh_cards("demo")
    run("job", "set", "j3", "--card", LAYOUT_CARD)
    return settled


@pytest.mark.parametrize(("probability", "held"), [(0.2, True), (0.1, False)])
def test_an_answer_holds_each_open_card_of_any_job_or_order_jev_reads_it_as_changing_read_against_all_of_them_in_one_call(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev, probability: float, held: bool
) -> None:
    another_order_asking(run, monkeypatch)
    jev.changes = {"j3-9": probability}
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    assert (f"  demo:j3-9  question  j3 o2  {LAYOUT}  {LAYOUT_CARD}" in capsys.readouterr().err) is held
    [read] = [request for request in jev.asked if "changes" in request["questions"]]
    assert read["state"]["answer"]["said"] == WORDS
    assert read["questions"]["changes"]["label"]["options"] == {
        "none": "It changes none of them.",
        "j2-7": "Which topology? Options: Tree | Ring",
        "j3-9": f"{LAYOUT} Options: One snapshot file | A map file beside each",
    }


def test_settled_shows_his_answer_on_a_card_it_decides_settles_its_jobs_question_and_waits_until_that_job_hears_his_words(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev, send_message: Callable[..., None]
) -> None:
    settled = another_order_asking(run, monkeypatch)
    with state.edit("demo") as data:
        data["jobs"]["j3"]["agent"] = "agent-3"
    jev.changes = {"j3-9": 0.9}
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    capsys.readouterr()
    assert cli.main(["answer", "a1", "--stands", designing, "--settled", "demo:j3-9"]) == 2
    told = capsys.readouterr().err
    assert f"demo:j3-9: its card {LAYOUT_CARD} now shows his answer a1 with no buttons, settling j3's question, so j3 builds on his words" in told
    sender = "from root@demo, the session session-under-test that started its agent agent-3"
    assert f"  j3 waits on o2 and has not heard a1: SendMessage it his words verbatim {sender}, or mark it untouched" in told
    send_message("demo", "j3", WORDS, waiting=True)
    assert run("answer", "a1") == (
        0,
        "his answer a1 recorded: 1 standing, 1 settled, 0 superseded, 0 reopened by it; 1 waiting job sent his words, 0 untouched",
    )
    assert settled == [ANSWERED, ("card-answer", "--card", LAYOUT_CARD, "--label", WORDS, "--settled", "--other", "--via", PICK)]
    data = state.load("demo")
    assert data["answers"]["a1"]["cards"]["demo:j3-9"] == {"mark": "settled", "job": "j3", "card": LAYOUT_CARD}
    assert [question["closed"] and (question["closed"]["how"], question["closed"]["answer"]) for question in data["jobs"]["j3"]["questions"]] == [("answered", "a1")]


def test_an_answer_lists_a_question_kept_with_no_card_drawn_yet_and_settling_it_ends_it_so_no_card_ever_draws(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev
) -> None:
    settled = cards_asking(monkeypatch, PR)
    with state.edit("demo") as data:
        data["jobs"]["j2"]["questions"].append(state.new_question("/tmp/j2-journal.md", "Which journal?", state.stamp()) | {"waits": "demo:j2-8"})
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    assert "  demo:j2-8  question  j2 o1  Which journal?\n" in capsys.readouterr().err
    code, out = run("answer", "a1", "--stands", designing, "--settled", "demo:j2-8")
    assert (code, out.splitlines()[:2]) == (
        0,
        [
            "his answer a1 recorded: 1 standing, 1 settled, 0 superseded, 0 reopened by it",
            "demo:j2-8: no card draws for it, his answer a1 ending its question, settling j2's question, so j2 builds on his words",
        ],
    )
    [journal] = [question for question in state.load("demo")["jobs"]["j2"]["questions"] if question["waits"] == "demo:j2-8"]
    assert (journal["card"], journal["closed"]["how"], journal["closed"]["answer"], settled) == (None, "answered", "a1", [ANSWERED])


def test_settled_redraws_a_card_it_decides_in_part_to_the_ask_its_job_keeps_for_what_is_left_and_refuses_one_unkept_or_not_linking_his_answer(
    run: Run, designing: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev, send_message: Callable[..., None]
) -> None:
    another_order_asking(run, monkeypatch)
    jev.changes = {"j3-9": 0.5}
    sent: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: sent.append((lane, records)) or ["9"])
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    unlinked, narrowed, loose = tmp_path / "unlinked.md", tmp_path / "narrowed.md", tmp_path / "loose.md"
    unlinked.write_text(SHORT_ASK)
    narrowed.write_text(SHORT_ASK.replace("Meanwhile nothing is built on either layout.", f"Meanwhile nothing is built on either layout, by <{PICK}|his answer>."))

    def redraws(path: Path) -> None:
        with state.edit("demo") as data:
            data["jobs"]["j3"]["questions"] = [state.new_question(str(path), LAYOUT, state.stamp(), card=LAYOUT_CARD) | {"waits": "demo:j3-9"}]

    redraws(unlinked)
    capsys.readouterr()
    for mark, refusal in [
        (designing, f"{designing} is no question card a job of this drive asked, so --settled cannot mark it"),
        (f"demo:j3-9={loose}", f"{loose.resolve()} keeps no open question of j3 redrawing demo:j3-9: keep what his answer leaves demo:j3-9 asking with drive job ask j3 <file> --redraws demo:j3-9"),
        (f"demo:j3-9={unlinked}", f"{unlinked.resolve()} does not link his answer {PICK} above its Findings:"),
    ]:
        assert cli.main(["answer", "a1", "--settled", mark]) == 2
        assert refusal in capsys.readouterr().err
    redraws(narrowed)
    with state.edit("demo") as data:
        data["jobs"]["j3"]["agent"] = "agent-3"
    send_message("demo", "j3", WORDS)
    code, out = run("answer", "a1", "--stands", designing, "--settled", f"demo:j3-9={narrowed}")
    redrawn = str(narrowed.resolve())
    assert (code, out.splitlines()) == (
        0,
        [
            "his answer a1 recorded: 1 standing, 1 redrawn, 0 superseded, 0 reopened by it; 1 waiting job sent his words, 0 untouched",
            f"demo:j3-9: its card {LAYOUT_CARD} is redrawn in place to {redrawn}, asking only what his answer a1 leaves open, as drive job ask j3 --redraws demo:j3-9 drew it",
        ],
    )
    assert sent == []
    data = state.load("demo")
    kept = [(question["file"], question["waits"], question["card"]) for question in data["jobs"]["j3"]["questions"]]
    assert kept == [(str(narrowed), "demo:j3-9", LAYOUT_CARD)]
    assert data["answers"]["a1"]["cards"]["demo:j3-9"] == {"mark": "redrawn", "job": "j3", "card": LAYOUT_CARD, "ask": redrawn}


def test_an_answer_records_nothing_while_jev_cannot_read_it_against_his_open_cards(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev
) -> None:
    another_order_asking(run, monkeypatch)
    jev.answer = None
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    assert (
        "nothing recorded in the drive: Jev did not read his answer against his 2 open cards (no jev decision within 20s)"
        in capsys.readouterr().err
    )
    assert "a1" not in state.load("demo")["answers"]


def test_superseding_a_card_he_already_answered_refuses_and_stops_no_job(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cards_asking(monkeypatch, PR)
    assert run("answer", "a1", "--words", WORDS, "--link", TAP_CARD)[0] == 2
    with state.edit("demo") as data:
        data["jobs"]["j2"]["questions"][-1]["closed"] = state.new_close("answered", "Tree", DESIGN_CARD)
    assert cli.main(["answer", "a1", "--superseded", "demo:j2-7=stop"]) == 2
    assert "demo:j2-7: not a card, order, part or PR that a1 has not marked" in capsys.readouterr().err
    data = state.load("demo")
    assert (data["answers"]["a1"]["cards"]["demo:j2-7"], data["jobs"]["j2"]["state"]) == (None, "running")


def test_superseding_one_pr_of_a_stack_card_refuses_until_the_whole_stack_is_marked_and_settles_it_once(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("pr", "add", STACKED, "--job", "j1", *LEAD)
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
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
    view.refresh_cards("demo")
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "Which cache disk?", state.stamp(), None, shown["url"]) | {"waits": "demo:j1-7"})
    calls: list[tuple] = []
    monkeypatch.setattr(slack, "cc_slack", lambda *args: calls.append(args) or {"answered": True})
    words, via = "We're already addressing this with the lazy + restate object", "https://acme.slack.com/archives/D123/p1794300000000999"
    code, line = run("cards", "withdraw", "demo:j1-7", "--words", words, "--via", via)
    assert (code, calls) == (0, [("card-answer", "--card", shown["url"], "--label", words, "--withdrawn", "--via", via)])
    assert line.splitlines() == [
        "withdrew 1 question(s) drawn on his card demo:j1-7",
        f"demo:j1-7: his card {shown['url']} now shows his words with no buttons, its question withdrawn",
        "drive job ask <job> <file> --redraws demo:j1-7 asks it again once its work is back, from j1 or any job of o1, which redraws its card",
    ]
    assert state.load("demo")["jobs"]["j1"]["questions"][-1]["closed"]["how"] == "withdrawn"
    assert cli.main(["cards", "withdraw", "demo:j1-8", "--words", words, "--via", via]) == 2
    assert "demo:j1-8: no open question of this drive is drawn on that card" in capsys.readouterr().err


def test_cards_withdraw_settles_a_card_an_event_overtook_showing_what_happened_and_refuses_before_the_event(
    run: Run, asking: str, heard: Heard, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    shown = open_card(7, {"title": "Hold the deploy?", "options": ["Hold", "Let it apply"], "recommended": "Hold", "waits": "demo:j1-7"})
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
    view.refresh_cards("demo")
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "Hold the deploy?", state.stamp(), None, shown["url"]) | {"waits": "demo:j1-7"})
    calls: list[tuple] = []
    monkeypatch.setattr(slack, "cc_slack", lambda *args: calls.append(args) or {"answered": True})
    read = {"state": "OPEN"}
    monkeypatch.setattr(github, "graphql", lambda text, what: {"data": {"r0": {"p42": read}}})
    heard.refusal = "his message at --via does not hold those words"
    words = "The freeze lifted and the deploy applied"
    assert cli.main(["cards", "withdraw", "demo:j1-7", "--words", words, "--via", PR]) == 2
    assert f"demo:j1-7: refused, not withdrawn: {heard.refusal}; nor did an event at {PR} overtake it: Forge-AI/monorepo#42 is still open" in capsys.readouterr().err
    assert calls == []
    read["state"] = "MERGED"
    code, line = run("cards", "withdraw", "demo:j1-7", "--words", words, "--via", PR)
    assert (code, calls) == (0, [("card-answer", "--card", shown["url"], "--label", words, "--withdrawn", "--via", PR)])
    assert f"demo:j1-7: his card {shown['url']} now shows what happened with no buttons, its question withdrawn" in line.splitlines()
    assert state.load("demo")["jobs"]["j1"]["questions"][-1]["closed"]["how"] == "withdrawn"


def test_an_event_overtakes_a_card_once_its_pr_closed_its_build_or_job_finished_or_a_teammate_or_another_bot_wrote(
    slack_thread: Thread, monkeypatch: pytest.MonkeyPatch
) -> None:
    his, ours, theirs = (slack_thread.post(datetime(2026, 11, 14, 0, minute, tzinfo=UTC), text="The freeze is lifted", **flags) for minute, flags in ((1, {"from_user": True}), (2, {"from_claude": True}), (3, {})))
    states = {"42": "OPEN", "43": "MERGED"}
    monkeypatch.setattr(github, "graphql", lambda text, what: {"data": {"r0": {f"p{number}": {"state": states[number]} for number in re.findall(r"pullRequest\(number: (\d+)\)", text)}}})
    deployed, running, missing = "01a1d675-2646-4852-9362-f7eeade5e8c7", "01a1d672-1b16-4b2a-9d0e-3c5f6a7b8c9d", "01a1d699-0000-4000-8000-000000000000"
    builds = {
        "15515": {"state": "passed", "finished_at": "2026-11-14T00:57:12.057Z", "jobs": [{"id": deployed, "state": "passed", "finished_at": "2026-11-14T00:51:27.139Z"}, {"id": running, "state": "running"}]},
        "15516": {"state": "running"},
    }
    monkeypatch.setattr(github, "run", lambda cmd, **kwargs: json.dumps(builds[cmd[3]]))
    release, gone = "https://buildkite.com/forge/release/builds/15515", "https://acme.slack.com/archives/D123/p1794300000000999"
    reasons = {
        his: "it is his own message, which withdraws a card only by his words there, verbatim",
        ours: "it is a post of the drive or of another agent, never an event",
        theirs: None,
        gone: f"it could not be read: no message 1794300000.000999 in the thread at --url {gone}",
        PR: "Forge-AI/monorepo#42 is still open",
        STACKED: None,
        release: None,
        f"{release}#{deployed}": None,
        f"{release}#{running}": "that job has not finished: it reads running",
        f"{release}#{missing}": f"forge/release build 15515 has no job {missing}",
        "https://buildkite.com/forge/release/builds/15516": "that build has not finished: it reads running",
        f"https://buildkite.com/forge/release/builds/15516#{running}": f"forge/release build 15516 has no job {running}",
        "terminal": f"the drive reads no such event; {settled.EVENTS} overtakes a card",
    }
    assert {link: settled.unhappened(link) for link in reasons} == reasons


def test_a_ruling_records_only_with_the_tooling_order_rewriting_the_skill_to_it(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [], "closed": []})
    view.refresh_cards(demo)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "design the run page")
    ruling = ["answer", "--title", "How should cards read?", "--words", "Cards say why", "--link", "https://acme.slack.com/archives/D123/p1", "--label", "ruling"]
    for extra in ([], ["--rewrite", "o1"]):
        assert cli.main([*ruling, *extra]) == 2
        assert "refused, nothing recorded: a ruling's skill rewrite is owed the moment the ruling is recorded" in capsys.readouterr().err


def test_a_pr_carrying_a_code_ruling_is_refused_until_it_writes_his_words_into_the_nearest_agents_md(
    recorded: Answers, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded.shown["23ff059"] = {"id": "23ff059", "title": "How should the cache key?", "body": '"Key the cache on the run id"\nNotes: cci owner 1', "tags": ["ruling", "code:go/sand/cache"]}
    data = {"roles": {"coordinator": {"cwd": "/repo"}}, "prs": {"Forge-AI/monorepo#7": {"job": "j1"}}, "jobs": {"j1": {"brief_flags": {"ruling": ["23ff059"]}}}}
    diff = b"diff --git a/go/sand/AGENTS.md b/go/sand/AGENTS.md\n--- a/go/sand/AGENTS.md\n+++ b/go/sand/AGENTS.md\n@@ -1 +1,2 @@\n # Sand\n+- Key the cache on the run id (Nov 11)\n"
    monkeypatch.setattr(served, "change", lambda key: {"title": "Fix: cache key", "head": "aaaaaaa111", "diff": diff})
    cli.refuse_an_unwritten_rule(data, "Forge-AI/monorepo#7")
    diff = diff.replace(b"go/sand/AGENTS.md", b"go/sand/other/AGENTS.md")
    with pytest.raises(state.DriveError, match='his ruling 23ff059 on how go/sand/cache must work, "Key the cache on the run id", is not written'):
        cli.refuse_an_unwritten_rule(data, "Forge-AI/monorepo#7")


def test_a_drive_started_from_an_older_install_hands_its_verb_to_the_newest_install_before_a_drive_json_it_saved_refuses_it(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state.store(demo, state.stored(demo) | {"saved_by": "9.9.9"})
    newer = tmp_path / "cache" / "9.9.9" / "bin" / "drive"
    argv = ["drive", "status", "--drive", demo]

    class Execed(Exception):
        pass

    def execv(drive: Path, args: list[str]) -> None:
        raise Execed(drive, args)

    monkeypatch.setattr(cli.os, "execv", execv)
    monkeypatch.setattr(cli.sys, "argv", argv)
    monkeypatch.setattr(install, "upgraded", lambda: newer)
    with pytest.raises(Execed) as execed:
        cli.main()
    assert execed.value.args == (newer, [str(newer), *argv[1:]])
    monkeypatch.setattr(install, "upgraded", lambda: None)
    assert cli.main() == 2
    assert capsys.readouterr().err.startswith(f"drive: the {demo} drive was saved by long-running 9.9.9")


def test_a_verb_a_newer_installs_save_refused_before_it_changed_anything_runs_again_on_the_newest_install_with_the_stdin_it_read(
    demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state.store(demo, state.stored(demo) | {"saved_by": "9.9.9"})
    arguments = json.dumps({"drive": demo})
    reopened: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(install, "upgraded", lambda: tmp_path / "cache" / "9.9.9" / "bin" / "drive")
    monkeypatch.setattr(install, "reopen", lambda argv, stdin=None: reopened.append((argv, stdin)))
    monkeypatch.setattr(cli.sys, "argv", ["drive", "tool", "status"])
    monkeypatch.setattr(cli, "STDIN", [])
    monkeypatch.setattr(state, "WROTE", set())
    monkeypatch.setattr("sys.stdin", io.StringIO(arguments))
    assert cli.main() == 2
    assert reopened == [(["tool", "status"], None), (["tool", "status"], arguments)]
    assert capsys.readouterr().err.startswith(f"drive: the {demo} drive was saved by long-running 9.9.9, newer than this")
    reopened.clear()
    monkeypatch.setattr("sys.stdin", io.StringIO(arguments))
    subprocess.run([sys.executable, "-c", ""], check=True)
    assert cli.main() == 2
    assert reopened == [(["tool", "status"], None)]


@pytest.mark.parametrize(
    ("argv", "again"),
    [
        (["job", "resume", "j1", watch.UNREACHED], True),
        (["job", "launch", "j1"], True),
        (["status"], False),
    ],
)
def test_a_launch_or_resume_a_newer_installs_save_refused_after_it_woke_a_sprite_runs_again_on_the_newest_install(
    asking: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], again: bool
) -> None:
    state.store("demo", state.stored("demo") | {"saved_by": "9.9.9"})
    argv = [*argv, "--drive", "demo"]
    reopened: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(install, "upgraded", lambda: tmp_path / "cache" / "9.9.9" / "bin" / "drive")
    monkeypatch.setattr(install, "reopen", lambda argv, stdin=None: reopened.append((argv, os.environ.get(state.DETACHED_ENV))))
    monkeypatch.setattr(cli.sys, "argv", ["drive", *argv])
    monkeypatch.setattr(cli, "STDIN", [])
    monkeypatch.setattr(state, "WROTE", {"orca-reclaim.py"})
    monkeypatch.setenv(state.DETACHED_ENV, watch.RELAUNCHES_LOG)
    assert cli.main() == 2
    assert reopened == [(argv, watch.RELAUNCHES_LOG)] * (2 if again else 1)


def test_a_resume_the_watch_ran_says_the_refusal_it_still_meets_in_the_watchs_line_for_its_order_and_a_hand_run_only_prints_it(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with state.edit("demo") as data:
        cli.bind_subcoordinator(data, "The drive", {"session": "drive-1", "terminal": None, "cwd": "/work", "started": state.stamp()})
    start = state.events_end("demo")
    state.store("demo", state.stored("demo") | {"saved_by": "9.9.9"})
    monkeypatch.setattr(install, "upgraded", lambda: None)
    monkeypatch.setattr(cli.sys, "argv", ["drive", "job", "resume", asking, watch.UNREACHED, "--drive", "demo"])
    assert cli.main() == 2
    assert state.events_end("demo") == start
    monkeypatch.setenv(state.DETACHED_ENV, watch.RELAUNCHES_LOG)
    assert cli.main() == 2
    refusal = f"the demo drive was saved by long-running 9.9.9, newer than this {state.VERSION}, which may misread it: run the newest install's drive"
    assert capsys.readouterr().err == f"drive: {refusal}\n" * 2
    state.store("demo", state.stored("demo") | {"saved_by": state.VERSION})
    assert watch.event_lines(watch.Watcher("demo", offset=start)) == []
    assert watch.event_lines(watch.Watcher("demo", offset=start, project="The drive")) == [
        f"EVENT relaunch_refused drive job resume j1, which the watch ran, refused: {refusal}; its output is in the drive's relaunches.log [o1 j1]"
    ]


def test_a_parse_never_hands_a_verb_the_list_its_parser_keeps_as_a_default() -> None:
    first = cli.parsed(["job", "set", "j1"])
    first.waits.append("job j2")
    first.part.append("a part")
    again = cli.parsed(["job", "set", "j1"])
    assert (again.waits, again.part, cli.parser() is cli.parser()) == ([], [], True)
